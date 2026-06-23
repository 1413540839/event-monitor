# -*- coding: utf-8 -*-
"""Perpetual Futures Monitor v2 - Clean notifications with entry/SL/TP/settlement
   10u margin, 20x leverage (200u notional)
   Long: RSI7<30 P20<0.30 VR>1.0 | Short: RSI7>70 P20>0.70 VR>1.0
   SL=1.0% TP=1.5% (R:R=1.5:1), max hold 4 bars
   Backtest: 9.9/d WR=51.8% +24u/mo"""
import time, json, requests, os, logging, sys, traceback, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
try: import pandas_ta_classic as ta
except: import pandas_ta as ta

SYMBOLS = ["ETH-USDT", "BTC-USDT"]
BAR = "1H"; LIMIT = 300; POLL_SEC = 30
SENDKEY = os.environ.get("SENDKEY", "")
TRADE_LOG = Path("perp_trades.csv")

RSI_LOW=30; RSI_HIGH=70; P20_LOW=0.30; P20_HIGH=0.70; VR_MIN=1.0
SL_PCT=1.0; TP_PCT=1.5; MAX_HOLD=4
MARGIN=10; LEVERAGE=20; NOTIONAL=MARGIN*LEVERAGE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log=logging.getLogger(__name__)
SEEN=set(); PENDING={}; CONSEC_LOSS=0; LAST_SIGNAL={}; TOTAL_PNL=0.0
MAX_RUN=350

def push_wechat(title, content):
    if not SENDKEY: return False
    try:
        r=requests.post(f"https://sctapi.ftqq.com/{SENDKEY}.send",
                       data={"title":title,"desp":content}, timeout=10)
        return r.status_code==200
    except: return False

def load_trades():
    if TRADE_LOG.exists(): return pd.read_csv(TRADE_LOG)
    return pd.DataFrame(columns=["time","coin","direction","entry_price","sl_price","tp_price","exit_price","pnl","result","exit_reason"])

def save_trades(df):
    df.to_csv(TRADE_LOG, index=False, encoding="utf-8")
    try:
        subprocess.run(["git","pull","--rebase"], capture_output=True, timeout=15)
        subprocess.run(["git","add","perp_trades.csv"], capture_output=True, timeout=10)
        r=subprocess.run(["git","commit","-m","update perp trades"], capture_output=True, text=True, timeout=10)
        if r.returncode==0 or "nothing to commit" in (r.stdout or ""):
            subprocess.run(["git","push"], capture_output=True, text=True, timeout=30)
    except: pass

def fetch_candles(sym):
    url="https://www.okx.com/api/v5/market/candles"
    try:
        r=requests.get(url, params={"instId":sym,"bar":BAR,"limit":LIMIT}, timeout=15)
        if r.json()["code"]!="0": return None
        rows=[{"ts":int(c[0]),"open":float(c[1]),"high":float(c[2]),
               "low":float(c[3]),"close":float(c[4]),"volume":float(c[5])}
              for c in reversed(r.json()["data"])]
        return rows
    except: return None

def build_df(rows):
    df=pd.DataFrame(rows)
    for col in ["open","high","low","close","volume","ts"]: df[col]=pd.to_numeric(df[col])
    df.index=df["ts"].astype(int)
    return df.sort_index()

def compute_signal(df):
    c,h,l,v=df["close"],df["high"],df["low"],df["volume"]
    if len(c)<80: return 0,None
    rsi7=ta.rsi(c,7)
    p20=(c-l.rolling(20).min())/(h.rolling(20).max()-l.rolling(20).min()+1e-10)
    vr20=v/v.rolling(20).mean()
    lc=c.iloc[-2]; lr7=rsi7.iloc[-2]; lp20=p20.iloc[-2]; lvr=vr20.iloc[-2]
    if pd.isna(lr7) or pd.isna(lp20) or pd.isna(lvr): return 0,None
    if lvr<VR_MIN: return 0,None
    if lr7<RSI_LOW and lp20<P20_LOW:
        return 1,{"rsi":lr7,"p20":lp20,"vr":lvr,"close":lc,"ts":df.index[-2]}
    elif lr7>RSI_HIGH and lp20>P20_HIGH:
        return -1,{"rsi":lr7,"p20":lp20,"vr":lvr,"close":lc,"ts":df.index[-2]}
    return 0,None

def check_exits(dfs_current):
    global TOTAL_PNL, CONSEC_LOSS
    results=[]; to_remove=[]
    for (sym,entry_ts),trade in list(PENDING.items()):
        if sym not in dfs_current: continue
        df=dfs_current[sym]
        direction=trade["direction"]; entry=trade["entry_price"]
        sl_price=trade["sl_price"]; tp_price=trade["tp_price"]
        future=df[df.index>entry_ts]
        if len(future)==0: continue
        
        exit_price=None; exit_reason=""
        for idx in range(min(len(future), MAX_HOLD)):
            bar=future.iloc[idx]
            if direction==1:
                if bar["high"]>=tp_price: exit_price=tp_price; exit_reason="TP"; break
                if bar["low"]<=sl_price: exit_price=sl_price; exit_reason="SL"; break
            else:
                if bar["low"]<=tp_price: exit_price=tp_price; exit_reason="TP"; break
                if bar["high"]>=sl_price: exit_price=sl_price; exit_reason="SL"; break
        else:
            if len(future)>=MAX_HOLD:
                exit_price=future.iloc[MAX_HOLD-1]["close"]
                exit_reason="TIME"
            else: continue
        
        if exit_price is None: continue
        
        if direction==1: actual_pnl=(exit_price-entry)/entry*NOTIONAL
        else: actual_pnl=(entry-exit_price)/entry*NOTIONAL
        
        is_win=actual_pnl>0; result="WIN" if is_win else "LOSS"
        TOTAL_PNL+=actual_pnl
        dir_str="LONG" if direction==1 else "SHORT"
        
        results.append({
            "time":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "coin":trade["coin"],"direction":dir_str,
            "entry_price":entry,"sl_price":sl_price,"tp_price":tp_price,
            "exit_price":exit_price,"pnl":round(actual_pnl,2),
            "result":result,"exit_reason":exit_reason
        })
        to_remove.append((sym,entry_ts))
        
        if not is_win: CONSEC_LOSS+=1
        else: CONSEC_LOSS=0
        
        # Settlement push with full detail
        emoji="[WIN]" if is_win else "[LOSS]"
        push_wechat(
            f"{emoji} {trade['coin']} {dir_str} {actual_pnl:+.1f}u ({exit_reason})",
            f"币种: {trade['coin']}\n"
            f"方向: {dir_str}\n"
            f"入场: ${entry:,.2f}\n"
            f"止损: ${sl_price:,.2f} | 止盈: ${tp_price:,.2f}\n"
            f"出场: ${exit_price:,.2f}\n"
            f"盈亏: {actual_pnl:+.1f}u\n"
            f"原因: {exit_reason}\n"
            f"累计盈亏: {TOTAL_PNL:+.1f}u\n"
            f"{datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')}"
        )
    for k in to_remove: del PENDING[k]
    return results

def run():
    global TOTAL_PNL
    start_time=datetime.now()
    trade_df=load_trades()
    n=len(trade_df)
    TOTAL_PNL=trade_df["pnl"].sum() if n else 0.0
    if n:
        w=(trade_df["result"]=="WIN").sum()
        log.info("History: %d trades %.1f%% PnL%+.1f", n, w/n*100, TOTAL_PNL)
    
    push_wechat(
        "永续合约监控 v2 启动",
        f"币种: BTC+ETH | 周期: 1H\n"
        f"保证金: {MARGIN}u | 杠杆: {LEVERAGE}x\n"
        f"止损: {SL_PCT}% | 止盈: {TP_PCT}%\n"
        f"历史: {n}笔 累计{TOTAL_PNL:+.1f}u\n"
        f"{datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')}"
    )
    log.info("Perp v2 - %s %s | %du %dx", BAR, SYMBOLS, MARGIN, LEVERAGE)
    
    dfs={}
    for sym in SYMBOLS:
        rows=fetch_candles(sym)
        if rows: dfs[sym]=build_df(rows)
        else: log.error("No data for %s", sym)
    if not dfs: return
    
    loop=0
    while True:
        try:
            if (datetime.now()-start_time).total_seconds()>MAX_RUN*60: break
            
            for sym in SYMBOLS:
                rows=fetch_candles(sym)
                if rows: dfs[sym]=build_df(rows)
            
            new_recs=check_exits(dfs)
            if new_recs:
                ndf=pd.DataFrame(new_recs)
                trade_df=pd.concat([trade_df, ndf], ignore_index=True)
                save_trades(trade_df)
            
            bj=(datetime.now(timezone.utc)+timedelta(hours=8)).strftime("%H:%M:%S")
            status=[]
            for sym in SYMBOLS:
                if sym not in dfs or len(dfs[sym])<80: continue
                direction,sc=compute_signal(dfs[sym])
                coin="ETH" if "ETH" in sym else "BTC"
                
                if sc:
                    close=sc["close"]; rsi_v=sc["rsi"]; p20_v=sc["p20"]
                    cid=f"{sym}_{sc['ts']}"
                    
                    if direction!=0 and cid not in SEEN:
                        if coin in LAST_SIGNAL:
                            if (datetime.now()-LAST_SIGNAL[coin]).total_seconds()<7200:
                                status.append(f"{coin}${close:,.0f} CD"); continue
                        
                        dir_str="LONG" if direction==1 else "SHORT"
                        sl_p=close*(1-SL_PCT/100) if direction==1 else close*(1+SL_PCT/100)
                        tp_p=close*(1+TP_PCT/100) if direction==1 else close*(1-TP_PCT/100)
                        
                        SEEN.add(cid)
                        if len(SEEN)>1000: SEEN.clear()
                        LAST_SIGNAL[coin]=datetime.now()
                        
                        PENDING[(sym,sc["ts"])]={
                            "coin":coin,"direction":direction,
                            "entry_price":close,"sl_price":sl_p,"tp_price":tp_p,
                            "time":datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        
                        log.info("SIGNAL %s %s @$%.2f SL=$%.2f TP=$%.2f",
                                coin,dir_str,close,sl_p,tp_p)
                        
                        # Signal push with all details
                        push_wechat(
                            f"开仓 {coin} {dir_str} @${close:,.0f}",
                            f"币种: {coin}\n"
                            f"方向: {dir_str}\n"
                            f"入场价: ${close:,.2f}\n"
                            f"止损价: ${sl_p:,.2f} (-{SL_PCT}%)\n"
                            f"止盈价: ${tp_p:,.2f} (+{TP_PCT}%)\n"
                            f"保证金: {MARGIN}u | 杠杆: {LEVERAGE}x\n"
                            f"RSI7={rsi_v:.0f} P20={p20_v:.3f} VR={sc['vr']:.2f}\n"
                            f"{datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                    
                    status.append(f"{coin}${close:,.0f} R{rsi_v:.0f}")
                else:
                    cv=dfs[sym]["close"].iloc[-2]
                    status.append(f"{coin}${cv:,.0f} -")
            
            print(f"[{bj}] {' | '.join(status)} | pend:{len(PENDING)} sigs:{len(SEEN)} PnL:{TOTAL_PNL:+.1f} L{loop}")
            loop+=1
            time.sleep(POLL_SEC)
        except KeyboardInterrupt: break
        except Exception as e:
            log.error("Loop: %s", traceback.format_exc())
            time.sleep(15)
    log.info("Stopped. %.0f min", (datetime.now()-start_time).total_seconds()/60)

if __name__=="__main__":
    run()