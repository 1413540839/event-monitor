# -*- coding: utf-8 -*-
"""Perpetual Futures WebSocket Monitor v1 - Real-time 1H candles + ticker verification"""
import time, json, requests, os, logging, sys, traceback, subprocess, threading, random
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
try: import pandas_ta_classic as ta
except: import pandas_ta as ta

# ---- CONFIG ----
COINS = [
    {"sym":"BTC-USDT","coin":"BTC","sl":0.7,"tp":1.5,"rsi_lo":40,"rsi_hi":60,"p20_lo":0.45,"p20_hi":0.55},
    {"sym":"ETH-USDT","coin":"ETH","sl":1.0,"tp":1.5,"rsi_lo":40,"rsi_hi":60,"p20_lo":0.45,"p20_hi":0.55},
    {"sym":"SOL-USDT","coin":"SOL","sl":1.0,"tp":2.0,"rsi_lo":40,"rsi_hi":60,"p20_lo":0.45,"p20_hi":0.55},
    {"sym":"XRP-USDT","coin":"XRP","sl":1.0,"tp":2.0,"rsi_lo":38,"rsi_hi":62,"p20_lo":0.42,"p20_hi":0.58},
    {"sym":"BNB-USDT","coin":"BNB","sl":0.8,"tp":1.8,"rsi_lo":40,"rsi_hi":60,"p20_lo":0.44,"p20_hi":0.56},
    {"sym":"DOGE-USDT","coin":"DOGE","sl":1.5,"tp":3.0,"rsi_lo":35,"rsi_hi":65,"p20_lo":0.40,"p20_hi":0.60},
    {"sym":"LINK-USDT","coin":"LINK","sl":1.2,"tp":2.5,"rsi_lo":38,"rsi_hi":62,"p20_lo":0.42,"p20_hi":0.58},
    {"sym":"AVAX-USDT","coin":"AVAX","sl":1.5,"tp":3.0,"rsi_lo":38,"rsi_hi":62,"p20_lo":0.42,"p20_hi":0.58},
]

BAR="1H"; LIMIT=300; VR_MIN=0.8; MAX_HOLD=4
MARGIN=10; LEVERAGE=20; NOTIONAL=MARGIN*LEVERAGE
SENDKEY=os.environ.get("SENDKEY","")
TRADE_LOG=Path("perp_trades.csv")
MAX_RUN_MIN=350
PRICE_SLIPPAGE_MAX=0.005  # skip if ticker > 0.5% away


# === DIAGNOSTIC ===
if SENDKEY:
    log.info("SENDKEY OK: %s...%s", SENDKEY[:4], SENDKEY[-4:])
else:
    log.error("SENDKEY EMPTY! Check GitHub Secrets -> SENDKEY")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log=logging.getLogger(__name__)
SEEN=set(); PENDING={}; CONSEC_LOSS={}; LAST_SIGNAL={}; TOTAL_PNL=0.0
for c in COINS: CONSEC_LOSS[c["coin"]]=0

DATA_LOCK=threading.Lock()
dfs={}; ws_connected=False

def fmt_price(p):
    if abs(p) < 1: return f"${p:.6f}"
    elif abs(p) < 1000: return f"${p:,.2f}"
    else: return f"${p:,.0f}"

def push_wechat(title, content):
    if not SENDKEY: return False
    try:
        r=requests.post(f"https://sctapi.ftqq.com/{SENDKEY}.send", data={"title":title,"desp":content}, timeout=10)
        return r.status_code==200
    except: return False

def load_trades():
    if TRADE_LOG.exists(): return pd.read_csv(TRADE_LOG)
    return pd.DataFrame(columns=["time","coin","direction","entry_price","sl_price","tp_price","exit_price","pnl","result","exit_reason"])

def save_trades(df):
    df.to_csv(TRADE_LOG, index=False, encoding="utf-8")
    try:
        time.sleep(random.uniform(1,5))
        subprocess.run(["git","pull","--rebase"], capture_output=True, timeout=15)
        subprocess.run(["git","add","perp_trades.csv"], capture_output=True, timeout=10)
        r=subprocess.run(["git","commit","-m","update perp trades"], capture_output=True, text=True, timeout=10)
        if r.returncode==0 or "nothing to commit" in (r.stdout or ""):
            subprocess.run(["git","push"], capture_output=True, text=True, timeout=30)
    except: pass

def fetch_ticker(sym):
    try:
        r=requests.get("https://www.okx.com/api/v5/market/ticker", params={"instId":sym}, timeout=5)
        if r.json()["code"]=="0": return float(r.json()["data"][0]["last"])
    except: pass
    return None

def fetch_rest_candles(sym):
    try:
        r=requests.get("https://www.okx.com/api/v5/market/candles", params={"instId":sym,"bar":BAR,"limit":LIMIT}, timeout=10)
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

# ---- STRATEGY ----
def compute_signal(df, cfg):
    c,h,l,v=df["close"],df["high"],df["low"],df["volume"]
    if len(c)<80: return 0,None
    rsi7=ta.rsi(c,7)
    p20=(c-l.rolling(20).min())/(h.rolling(20).max()-l.rolling(20).min()+1e-10)
    vr20=v/v.rolling(20).mean()
    idx=-1  # current candle
    lc=c.iloc[idx]; lr7=rsi7.iloc[idx]; lp20=p20.iloc[idx]; lvr=vr20.iloc[idx]
    if pd.isna(lr7) or pd.isna(lp20) or pd.isna(lvr): return 0,None
    if lvr<VR_MIN: return 0,None
    if lr7<cfg["rsi_lo"] and lp20<cfg["p20_lo"]:
        return 1,{"rsi":lr7,"p20":lp20,"vr":lvr,"close":lc,"ts":df.index[idx]}
    elif lr7>cfg["rsi_hi"] and lp20>cfg["p20_hi"]:
        return -1,{"rsi":lr7,"p20":lp20,"vr":lvr,"close":lc,"ts":df.index[idx]}
    return 0,None

# ---- WEBSOCKET ----
def update_candle(df, candle):
    ts=int(candle[0]); o=float(candle[1]); h=float(candle[2])
    l=float(candle[3]); c_val=float(candle[4]); v=float(candle[5])
    if ts in df.index:
        df.loc[ts,["open","high","low","close","volume"]]=[o,h,l,c_val,v]
    elif ts>df.index[-1]:
        nr=pd.DataFrame({"open":[o],"high":[h],"low":[l],"close":[c_val],"volume":[v],"ts":[ts]}, index=[ts])
        df=pd.concat([df,nr]).sort_index()
        if len(df)>LIMIT: df=df.iloc[-LIMIT:]
    return df

def on_message(ws, message):
    global dfs
    try:
        data=json.loads(message)
        if "data" not in data: return
        for item in data["data"]:
            candle=item["candle"]; sym=item["instId"]
            with DATA_LOCK:
                if sym in dfs:
                    dfs[sym]=update_candle(dfs[sym],[candle[0],candle[1],candle[2],candle[3],candle[4],candle[5]])
    except: pass

def on_open(ws):
    global ws_connected
    ws_connected=True
    syms=[c["sym"] for c in COINS]
    channels=[{"channel":f"candle{BAR}","instId":s} for s in syms]
    ws.send(json.dumps({"op":"subscribe","args":channels}))
    log.info("WS connected: %d coins", len(syms))

def on_error(ws, error):
    global ws_connected; ws_connected=False
    log.error("WS error: %s", error)

def on_close(ws, status, msg):
    global ws_connected; ws_connected=False
    log.warning("WS closed: %s %s", status, msg)

def ws_connect():
    try:
        import websocket
        ws=websocket.WebSocketApp("wss://ws.okx.com:8443/ws/v5/business",
            on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
        t=threading.Thread(target=ws.run_forever, daemon=True)
        t.start()
        return True
    except ImportError:
        log.error("websocket-client not installed")
        return False

# ---- SETTLEMENT ----
def check_exits():
    global TOTAL_PNL
    results=[]; to_remove=[]
    for (sym,entry_ts),trade in list(PENDING.items()):
        if sym not in dfs: continue
        df=dfs[sym]; direction=trade["direction"]
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
        is_win=actual_pnl>0
        TOTAL_PNL+=actual_pnl
        dir_cn="做多" if direction==1 else "做空"
        coin=trade["coin"]
        CONSEC_LOSS[coin]=CONSEC_LOSS.get(coin,0)+1 if not is_win else 0
        
        results.append({
            "time":datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
            "coin":coin,"direction":"LONG" if direction==1 else "SHORT",
            "entry_price":entry,"sl_price":sl_price,"tp_price":tp_price,
            "exit_price":exit_price,"pnl":round(actual_pnl,2),
            "result":"WIN" if is_win else "LOSS","exit_reason":exit_reason
        })
        to_remove.append((sym,entry_ts))
        
        reason_cn={"TP":"止盈","SL":"止损","TIME":"超时"}.get(exit_reason,exit_reason)
        push_wechat(
            f"【永续】{'赚' if is_win else '亏'}了 {coin} {dir_cn} {actual_pnl:+.1f}u",
            f"{coin} {dir_cn} | {reason_cn}\n入场 {fmt_price(entry)} -> 出场 {fmt_price(exit_price)}\n"
            f"本单: {'赚' if is_win else '亏'}{abs(actual_pnl):.1f}u\n累计: {TOTAL_PNL:+.1f}u\n"
            f"{datetime.now(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')}"
        )
    for k in to_remove: del PENDING[k]
    return results

# ---- MAIN ----
def run():
    global dfs, ws_connected, TOTAL_PNL
    start_time=datetime.now(); loop=0
    trade_df=load_trades()
    n=len(trade_df)
    TOTAL_PNL=trade_df["pnl"].sum() if n else 0.0
    if n:
        w=(trade_df["result"]=="WIN").sum()
        log.info("History: %d trades %.1f%% PnL%+.1f", n, w/n*100, TOTAL_PNL)
    
    # Initial data
    log.info("Loading initial candles...")
    all_syms=[c["sym"] for c in COINS]
    for sym in all_syms:
        rows=fetch_rest_candles(sym)
        if rows:
            with DATA_LOCK: dfs[sym]=build_df(rows)
            log.info("  %s: %d candles", sym, len(dfs[sym]))
    
    ws_ok=ws_connect()
    if not ws_ok: log.warning("WS not available, REST fallback (45s)")
    
    coin_list=",".join(c["coin"] for c in COINS)
    push_wechat(
        "【永续】永续合约 已启动 (WS)",
        f"币种: {coin_list}\n保证金: {MARGIN}u | 杠杆: {LEVERAGE}x\n"
        f"历史: {n}笔 累计{TOTAL_PNL:+.1f}u\nWebSocket实时数据\n"
        f"{datetime.now(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')}"
    )
    log.info("SENDKEY: %s...", SENDKEY[:8] if SENDKEY else "NONE")
    log.info("Perp WS v1 - %s | %dux%d=%du", coin_list, MARGIN, LEVERAGE, NOTIONAL)
    
    random.seed()
    while True:
        try:
            if (datetime.now()-start_time).total_seconds()>MAX_RUN_MIN*60: break
            
            if not ws_connected:
                for sym in all_syms:
                    rows=fetch_rest_candles(sym)
                    if rows:
                        with DATA_LOCK: dfs[sym]=build_df(rows)
                time.sleep(45)
            
            # Settle
            new_recs=check_exits()
            if new_recs:
                ndf=pd.DataFrame(new_recs)
                trade_df=pd.concat([trade_df,ndf],ignore_index=True)
                save_trades(trade_df)
            
            bj=(datetime.now(timezone.utc)+timedelta(hours=8)).strftime("%H:%M")
            status=[]
            with DATA_LOCK:
                for cfg in COINS:
                    sym=cfg["sym"]; coin=cfg["coin"]
                    if sym not in dfs or len(dfs[sym])<80: continue
                    direction,sc=compute_signal(dfs[sym],cfg)
                    if sc:
                        close=sc["close"]; cid=f"{sym}_{sc['ts']}"
                        if direction!=0 and cid not in SEEN:
                            if coin in LAST_SIGNAL:
                                if (datetime.now()-LAST_SIGNAL[coin]).total_seconds()<7200: continue
                            
                            # Real-time price check
                            ticker_price=fetch_ticker(sym)
                            if ticker_price:
                                slippage=abs(ticker_price-close)/close
                                if slippage>PRICE_SLIPPAGE_MAX:
                                    log.info("SKIP %s: price moved %.2f%% (candle=%s ticker=%s)", coin, slippage*100, fmt_price(close), fmt_price(ticker_price))
                                    SEEN.add(cid); continue
                                entry_price=ticker_price
                            else:
                                entry_price=close
                            
                            dir_str="LONG" if direction==1 else "SHORT"
                            dir_cn="做多" if direction==1 else "做空"
                            sl_p=entry_price*(1-cfg["sl"]/100) if direction==1 else entry_price*(1+cfg["sl"]/100)
                            tp_p=entry_price*(1+cfg["tp"]/100) if direction==1 else entry_price*(1-cfg["tp"]/100)
                            SEEN.add(cid)
                            if len(SEEN)>2000: SEEN.clear()
                            LAST_SIGNAL[coin]=datetime.now()
                            PENDING[(sym,sc["ts"])]={
                                "coin":coin,"direction":direction,
                                "entry_price":entry_price,"sl_price":sl_p,"tp_price":tp_p
                            }
                            risk_u=abs(entry_price-sl_p)/entry_price*NOTIONAL
                            reward_u=abs(tp_p-entry_price)/entry_price*NOTIONAL
                            log.info("SIGNAL %s %s candle=%s ticker=%s", coin,dir_str,fmt_price(close),fmt_price(entry_price))
                            push_wechat(
                                f"【永续】开仓 {coin} {dir_cn} {fmt_price(entry_price)}",
                                f"{coin} {dir_cn}\n入场: {fmt_price(entry_price)} (实时价格)\n"
                                f"止损: {fmt_price(sl_p)} | 止盈: {fmt_price(tp_p)}\n"
                                f"最多亏{risk_u:.0f}u | 最多赚{reward_u:.0f}u\n"
                                f"RSI={sc['rsi']:.0f} 位置={sc['p20']:.2f} 量={sc['vr']:.1f}x\n"
                                f"{datetime.now(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')}"
                            )
                        status.append(f"{coin}{fmt_price(close)}")
                    else:
                        cv=dfs[sym]["close"].iloc[-2] if len(dfs[sym])>=2 else 0
                        status.append(f"{coin}{fmt_price(cv)}")
            
            ws_tag="WS" if ws_connected else "REST"
            print(f"[{bj}] {ws_tag} | {'|'.join(status)} | pend:{len(PENDING)} PnL:{TOTAL_PNL:+.1f} L{loop}")
            loop+=1
            if ws_connected: time.sleep(1)
        except KeyboardInterrupt: break
        except Exception as e:
            log.error("Loop: %s", traceback.format_exc())
            time.sleep(10)
    log.info("Stopped. %.0f min", (datetime.now()-start_time).total_seconds()/60)

if __name__=="__main__":
    run()
