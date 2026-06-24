# -*- coding: utf-8 -*-
"""Perpetual Futures Monitor v7 - Wide RSI<40/60: 10u x20lev, wide params, multi-coin
   BTC(0.7/1.5) ETH(1.0/1.5) SOL(1.0/2.0) | RSI<25/75 P20<0.35/0.65
   Backtest: ~28/d +70u/mo (30u deployed, ~250% monthly)"""
import time, json, random, requests, os, logging, sys, traceback, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
try: import pandas_ta_classic as ta
except: import pandas_ta as ta

COINS = [
    {"sym":"BTC-USDT","coin":"BTC","sl":0.7,"tp":1.5,"rsi_lo":40,"rsi_hi":60,"p20_lo":0.45,"p20_hi":0.55},
    {"sym":"ETH-USDT","coin":"ETH","sl":1.0,"tp":1.5,"rsi_lo":40,"rsi_hi":60,"p20_lo":0.45,"p20_hi":0.55},
    {"sym":"SOL-USDT","coin":"SOL","sl":1.0,"tp":2.0,"rsi_lo":40,"rsi_hi":60,"p20_lo":0.45,"p20_hi":0.55},
]

BAR="1H"; LIMIT=300; POLL_SEC=15; VR_MIN=0.8; MAX_HOLD=4
MARGIN=10; LEVERAGE=20; NOTIONAL=MARGIN*LEVERAGE  # 200u
SENDKEY=os.environ.get("SENDKEY","")
TRADE_LOG=Path("perp_trades.csv")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log=logging.getLogger(__name__)
SEEN=set(); PENDING={}; CONSEC_LOSS={}; LAST_SIGNAL={}; TOTAL_PNL=0.0; MAX_RUN=350
for c in COINS: CONSEC_LOSS[c["coin"]]=0

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
        time.sleep(random.uniform(1, 5))  # avoid git conflict
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

def compute_signal(df, cfg):
    c,h,l,v=df["close"],df["high"],df["low"],df["volume"]
    if len(c)<80: return 0,None
    rsi7=ta.rsi(c,7)
    p20=(c-l.rolling(20).min())/(h.rolling(20).max()-l.rolling(20).min()+1e-10)
    vr20=v/v.rolling(20).mean()
    lc=c.iloc[-2]; lr7=rsi7.iloc[-2]; lp20=p20.iloc[-2]; lvr=vr20.iloc[-2]
    if pd.isna(lr7) or pd.isna(lp20) or pd.isna(lvr): return 0,None
    if lvr<VR_MIN: return 0,None
    if lr7<cfg["rsi_lo"] and lp20<cfg["p20_lo"]:
        return 1,{"rsi":lr7,"p20":lp20,"vr":lvr,"close":lc,"ts":df.index[-2]}
    elif lr7>cfg["rsi_hi"] and lp20>cfg["p20_hi"]:
        return -1,{"rsi":lr7,"p20":lp20,"vr":lvr,"close":lc,"ts":df.index[-2]}
    return 0,None

def check_exits(dfs_current):
    global TOTAL_PNL
    results=[]; to_remove=[]
    for (sym,entry_ts),trade in list(PENDING.items()):
        if sym not in dfs_current: continue
        df=dfs_current[sym]; direction=trade["direction"]
        entry=trade["entry_price"]; sl_price=trade["sl_price"]; tp_price=trade["tp_price"]
        future=df[df.index>entry_ts]
        if len(future)==0: continue
        exit_price=None; exit_reason=""
        for idx in range(min(len(future),MAX_HOLD)):
            bar=future.iloc[idx]
            if direction==1:
                if bar["high"]>=tp_price: exit_price=tp_price; exit_reason="TP"; break
                if bar["low"]<=sl_price: exit_price=sl_price; exit_reason="SL"; break
            else:
                if bar["low"]<=tp_price: exit_price=tp_price; exit_reason="TP"; break
                if bar["high"]>=sl_price: exit_price=sl_price; exit_reason="SL"; break
        else:
            if len(future)>=MAX_HOLD:
                exit_price=future.iloc[MAX_HOLD-1]["close"]; exit_reason="TIME"
            else: continue
        if exit_price is None: continue
        
        if direction==1: actual_pnl=(exit_price-entry)/entry*NOTIONAL
        else: actual_pnl=(entry-exit_price)/entry*NOTIONAL
        is_win=actual_pnl>0; result="WIN" if is_win else "LOSS"
        TOTAL_PNL+=actual_pnl; dir_str="LONG" if direction==1 else "SHORT"
        coin=trade["coin"]
        CONSEC_LOSS[coin]=CONSEC_LOSS.get(coin,0)+1 if not is_win else 0
        
        results.append({
            "time":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "coin":coin,"direction":dir_str,
            "entry_price":entry,"sl_price":sl_price,"tp_price":tp_price,
            "exit_price":exit_price,"pnl":round(actual_pnl,2),
            "result":result,"exit_reason":exit_reason
        })
        to_remove.append((sym,entry_ts))
        
        emoji="[WIN]" if is_win else "[LOSS]"
        push_wechat(
            f"{emoji} {coin} {dir_str} {actual_pnl:+.1f}u ({exit_reason})",
            f"币种:{coin} 方向:{dir_str}\n"
            f"入场:${entry:,.2f}\n"
            f"止损:${sl_price:,.2f} 止盈:${tp_price:,.2f}\n"
            f"出场:${exit_price:,.2f} 盈亏:{actual_pnl:+.1f}u\n"
            f"累计: {TOTAL_PNL:+.1f}u | {LEVERAGE}x\n"
            f"{datetime.now(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')}"
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
    
    coin_list=",".join(c["coin"] for c in COINS)
    push_wechat(
        f"永续合约 v4 启动 ({coin_list})",
        f"币种:{coin_list} 周期:1H\n"
        f"保证金:{MARGIN}u 杠杆:{LEVERAGE}x 名义:{NOTIONAL}u\n"
        f"历史:{n}笔 累计{TOTAL_PNL:+.1f}u\n"
        f"{datetime.now(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')}"
    )
    log.info("Perp v7 - %s | %du x%d = %du", coin_list, MARGIN, LEVERAGE, NOTIONAL)
    
    dfs={}
    for cfg in COINS:
        rows=fetch_candles(cfg["sym"])
        if rows: dfs[cfg["sym"]]=build_df(rows)
        else: log.error("No data for %s", cfg["coin"])
    
    # Recover pending trades
    pf = Path("perp_pending.json")
    if pf.exists():
        try:
            with open(pf) as fh:
                saved = json.load(fh)
            for pt in saved:
                key = (pt["sym"], pt["ts"])
                if key not in PENDING:
                    PENDING[key] = {"coin":pt["coin"],"direction":pt["direction"],
                        "entry_price":pt["entry_price"],"sl_price":pt["sl_price"],"tp_price":pt["tp_price"]}
            log.info("Recovered %d pending trades", len(saved))
        except: pass

    if not dfs: return
    
    loop=0
    while True:
        try:
            if (datetime.now()-start_time).total_seconds()>MAX_RUN*60: break
            for cfg in COINS:
                rows=fetch_candles(cfg["sym"])
                if rows: dfs[cfg["sym"]]=build_df(rows)
            
            new_recs=check_exits(dfs)
            if new_recs:
                ndf=pd.DataFrame(new_recs)
                trade_df=pd.concat([trade_df,ndf],ignore_index=True)
                save_trades(trade_df)
            
            bj=(datetime.now(timezone.utc)+timedelta(hours=8)).strftime("%H:%M")
            status=[]
            for cfg in COINS:
                sym=cfg["sym"]; coin=cfg["coin"]
                if sym not in dfs or len(dfs[sym])<80: continue
                direction,sc=compute_signal(dfs[sym],cfg)
                if sc:
                    close=sc["close"]; cid=f"{sym}_{sc['ts']}"
                    if direction!=0 and cid not in SEEN:
                        if coin in LAST_SIGNAL:
                            if (datetime.now()-LAST_SIGNAL[coin]).total_seconds()<7200: continue
                        dir_str="LONG" if direction==1 else "SHORT"
                        sl_p=close*(1-cfg["sl"]/100) if direction==1 else close*(1+cfg["sl"]/100)
                        tp_p=close*(1+cfg["tp"]/100) if direction==1 else close*(1-cfg["tp"]/100)
                        SEEN.add(cid)
                        if len(SEEN)>2000: SEEN.clear()
                        LAST_SIGNAL[coin]=datetime.now()
                        PENDING[(sym,sc["ts"])]={
                            "coin":coin,"direction":direction,
                            "entry_price":close,"sl_price":sl_p,"tp_price":tp_p
                        }
                        # Save open trade for crash recovery
                        # Persist pending for crash recovery
                        try:
                            pj=[]
                            for k,v in PENDING.items():
                                pj.append({"sym":k[0],"ts":k[1],"coin":v["coin"],"direction":v["direction"],
                                    "entry_price":v["entry_price"],"sl_price":v["sl_price"],"tp_price":v["tp_price"]})
                            with open("perp_pending.json","w") as pf:
                                json.dump(pj,pf)
                        except: pass
                        risk_u=abs(close-sl_p)/close*NOTIONAL
                        reward_u=abs(tp_p-close)/close*NOTIONAL
                        log.info("SIGNAL %s %s @$%.2f SL=$%.2f TP=$%.2f risk=%.1f reward=%.1f",
                                coin,dir_str,close,sl_p,tp_p,risk_u,reward_u)
                        push_wechat(
                            f"开仓 {coin} {dir_str} @${close:,.0f}",
                            f"币种:{coin} 方向:{dir_str}\n"
                            f"入场:${close:,.2f}\n"
                            f"止损:${sl_p:,.2f}(-{cfg['sl']}%) 止盈:${tp_p:,.2f}(+{cfg['tp']}%)\n"
                            f"风险:{risk_u:.0f}u 收益:{reward_u:.0f}u\n"
                            f"RSI7={sc['rsi']:.0f} P20={sc['p20']:.3f} VR={sc['vr']:.2f}\n"
                            f"{LEVERAGE}x | {datetime.now(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')}"
                        )
                    status.append(f"{coin}${close:,.0f}")
                else:
                    cv=dfs[sym]["close"].iloc[-2]
                    status.append(f"{coin}${cv:,.0f}")
            print(f"[{bj}] {'|'.join(status)} | pend:{len(PENDING)} PnL:{TOTAL_PNL:+.1f} L{loop}")
            loop+=1
            time.sleep(POLL_SEC)
        except KeyboardInterrupt: break
        except Exception as e:
            log.error("Loop: %s", traceback.format_exc())
            time.sleep(15)
    log.info("Stopped. %.0f min", (datetime.now()-start_time).total_seconds()/60)

if __name__=="__main__":
    run()