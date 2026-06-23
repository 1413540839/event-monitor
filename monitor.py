# -*- coding: utf-8 -*-
import time, json, requests, os, logging, sys, traceback, subprocess, threading, queue
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np, pandas as pd, pandas_ta_classic as ta

try: import websocket; HAS_WS = True
except ImportError: HAS_WS = False; log = logging.getLogger(__name__)

SYMBOLS = ["BTC-USDT", "ETH-USDT"]
BAR = "5m"; LIMIT = 300
SENDKEY = os.environ.get("SENDKEY", "")
TRADE_LOG = Path("trade_log.csv")
CONTRACT_CANDLES = 2; COOLDOWN_MINUTES = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
SEEN = set(); PENDING = {}; LAST_SIGNAL = {}; DAILY_PNL = {}; CRASH_HALT = {}
DATA_LOCK = threading.Lock(); latest_data = {}; ws_queue = queue.Queue()

def load_trade_log():
    if TRADE_LOG.exists(): return pd.read_csv(TRADE_LOG)
    return pd.DataFrame(columns=["time","coin","direction","rule","entry_price","exit_price","pnl","result","detail"])

def save_trade_log(df):
    df.to_csv(TRADE_LOG, index=False, encoding="utf-8")
    try:
        subprocess.run(["git","pull","--rebase"], capture_output=True, timeout=15)
        subprocess.run(["git","add","trade_log.csv"], capture_output=True, timeout=10)
        r = subprocess.run(["git","commit","-m","update trade log"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 or "nothing to commit" in (r.stdout or ""):
            subprocess.run(["git","push"], capture_output=True, text=True, timeout=30)
    except: pass

def push_wechat(title, content):
    if not SENDKEY: return False
    try:
        r = requests.post(f"https://sctapi.ftqq.com/{SENDKEY}.send", data={"title":title,"desp":content}, timeout=5)
        return r.status_code == 200
    except: return False

def fetch_initial(sym):
    url = "https://www.okx.com/api/v5/market/candles"
    r = requests.get(url, params={"instId": sym, "bar": BAR, "limit": LIMIT}, timeout=10)
    if r.json()["code"] != "0": return None
    rows = [{"ts": int(c[0]), "open": float(c[1]), "high": float(c[2]),
              "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
            for c in reversed(r.json()["data"])]
    return rows

def build_df(rows):
    df = pd.DataFrame(rows).astype(float)
    df.index = df["ts"].astype(int)
    return df.sort_index()

def update_candle(df, candle_ts, o, h, l, c, v):
    if candle_ts in df.index:
        df.loc[candle_ts, "open"] = o
        df.loc[candle_ts, "high"] = h
        df.loc[candle_ts, "low"] = l
        df.loc[candle_ts, "close"] = c
        df.loc[candle_ts, "volume"] = v
    elif candle_ts > df.index[-1]:
        df.loc[candle_ts] = [o, h, l, c, v]
        df = df.sort_index()
        if len(df) > LIMIT: df = df.iloc[-LIMIT:]
    return df

def analyze_signals(df, sym):
    """v8: v7 signal + crash circuit breaker + daily loss limit | backtest 67.7% WR"""
    try:
        c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
        if len(c) < 60: return [], 0, 0, 0, 0, 0, ""
        ema20=ta.ema(c,20); ema60=ta.ema(c,60); rsi7=ta.rsi(c,7)
        lc=c.iloc[-1]; lr7=rsi7.iloc[-1]
        pos20=(c-l.rolling(20).min())/(h.rolling(20).max()-l.rolling(20).min()+1e-10); lp=pos20.iloc[-1]
        vol_sma=v.rolling(20).mean(); vr=v.iloc[-1]/vol_sma.iloc[-1] if vol_sma.iloc[-1]>0 else 0
        trend="UP" if ema20.iloc[-1]>ema60.iloc[-1] else "DN"
        current_ts=int(df.index[-1]); coin="BTC" if "BTC" in sym else "ETH"
        now=datetime.now()
        if coin in LAST_SIGNAL and (now-LAST_SIGNAL[coin]).total_seconds()<COOLDOWN_MINUTES*60:
            return [], lc, lr7, vr, current_ts, coin, trend
        
        # Daily loss limit: stop trading after -10u
        today=now.strftime("%Y-%m-%d")
        daily_loss=DAILY_PNL.get(today,0)
        if daily_loss <= -10:
            return [], lc, lr7, vr, current_ts, coin, "HALT"
        
        # Slow crash circuit breaker: DD from 1h high > 0.8% + 5+ bearish candles
        hh_1h=h.rolling(12).max().iloc[-1]/lc-1 if len(h)>=12 else 0
        bear_6=int(((c<o).astype(int).rolling(6).sum()).iloc[-1]) if len(c)>=6 else 0
        if (hh_1h>0.008 and bear_6>=5):
            CRASH_HALT[(sym,now.date())]=now+timedelta(hours=1)
        if (sym,now.date()) in CRASH_HALT and now<CRASH_HALT[(sym,now.date())]:
            return [], lc, lr7, vr, current_ts, coin, "CRASH"
        
        alerts=[]
        # v7 signal: P20 extreme oversold + volume confirmation
        if not pd.isna(lp) and lp<0.15 and vr>1.3:
            alerts.append(("HC7","LONG",f"P20={lp:.2f} V={vr:.1f}x DD={hh_1h*100:.1f}%"))
        return alerts, lc, lr7, vr, current_ts, coin, trend
    except Exception as e:
        log.error("analyze %s: %s", sym, e)
        return [], 0, 0, 0, 0, 0, "??"

def update_daily_pnl(pnl): today=datetime.now().strftime("%Y-%m-%d"); DAILY_PNL[today]=DAILY_PNL.get(today,0)+pnl

def settle_trades(dfs):
    completed = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with DATA_LOCK:
        for key, trade in list(PENDING.items()):
            t_sym, t_entry_ts = key
            if t_sym not in dfs: continue
            df = dfs[t_sym]
            if t_entry_ts not in df.index: continue
            bars = df.index.get_loc(int(df.index[-1])) - df.index.get_loc(t_entry_ts)
            if bars >= CONTRACT_CANDLES:
                cp = float(df.loc[int(df.index[-1]), "close"])
                win = (cp > trade["entry_price"]) if trade["direction"] == "LONG" else (cp < trade["entry_price"])
                pnl = 4 if win else -5
                completed.append((key, {"time":trade["time"],"coin":trade["coin"],"direction":trade["direction"],
                    "rule":trade["rule"],"entry_price":trade["entry_price"],"exit_price":cp,
                    "pnl":pnl,"result":"WIN" if win else "LOSS","detail":trade["detail"]}))
                log.info("settle: %s %s [%s] %s %+du", trade["coin"], trade["direction"], trade["rule"], "WIN" if win else "LOSS", pnl)
        for key, _ in completed: del PENDING[key]
    if completed:
        parts=[];tp=0
        for _,r in completed:
            parts.append(f"{'+' if r['result']=='WIN' else '-'} {r['coin']} {r['direction']} [{r['rule']}] {r['pnl']:+d}u")
            tp+=r["pnl"]
        for _,r in completed: update_daily_pnl(r["pnl"])
        push_wechat(f"Settle x{len(completed)} PnL{tp:+d}u","\n".join(parts)+f"\n\nTotal:{tp:+d}u\n{now_str}")
    return [v for _,v in completed]

def on_message(ws, message):
    try:
        data = json.loads(message)
        if "data" not in data or not data["data"]: return
        instId = data["arg"]["instId"]
        for c in data["data"]:
            ws_queue.put((instId, int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])))
    except: pass

def on_error(ws, error): log.error("WS err: %s", error)
def on_close(ws, status, msg): log.warning("WS closed: %s %s", status, msg)

def ws_connect():
    ws = websocket.WebSocketApp("wss://ws.okx.com:8443/ws/v5/business",
        on_message=on_message, on_error=on_error, on_close=on_close)
    t = threading.Thread(target=lambda: ws.run_forever(), daemon=True)
    t.start()
    time.sleep(1)
    channels = [{"channel": "candle5m", "instId": s} for s in SYMBOLS]
    ws.send(json.dumps({"op": "subscribe", "args": channels}))
    log.info("WS live: %s", SYMBOLS)
    return ws, t

def main():
    log.info("v8 P20+VR+CB+DL start")
    trade_df = load_trade_log()
    total=len(trade_df); w=(trade_df["result"]=="WIN").sum() if total else 0; tp=trade_df["pnl"].sum() if total else 0
    if total: log.info("history: %d %.1f%% PnL%+d", total, w/total*100, tp)
    
    for sym in SYMBOLS:
        rows = fetch_initial(sym)
        if rows:
            with DATA_LOCK: latest_data[sym] = build_df(rows)
            log.info("%s: %d candles", sym, len(latest_data[sym]))
    
    if HAS_WS:
        ws, ws_t = ws_connect()
        push_wechat("Monitor v8 P20+VR+CircuitBreaker (WebSocket)", f"BTC+ETH\nReal-time WS\nHistory:{total} trades PnL{tp:+d}u")
    else:
        log.warning("No websocket-client")
        push_wechat("Monitor v8 P20+VR+CircuitBreaker (REST)", f"BTC+ETH\nREST mode\nHistory:{total} trades PnL{tp:+d}u")
    
    while True:
        try:
            bj = (datetime.now(timezone.utc)+timedelta(hours=8)).strftime("%H:%M:%S")
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            processed=0
            
            while not ws_queue.empty():
                try:
                    instId, ts, o, h, l, c, v = ws_queue.get_nowait()
                    with DATA_LOCK:
                        if instId in latest_data:
                            latest_data[instId] = update_candle(latest_data[instId], ts, o, h, l, c, v)
                    processed+=1
                except: pass
            
            with DATA_LOCK: dfs = {k:v.copy() for k,v in latest_data.items()}
            new_records = settle_trades(dfs)
            
            for sym in SYMBOLS:
                if sym not in dfs: continue
                alerts, lc, lr7, vr, ts, coin, trend = analyze_signals(dfs[sym], sym)
                ns=",".join(rl for rl,_,_ in alerts) if alerts else "-"
                cd=""
                if coin in LAST_SIGNAL:
                    s=COOLDOWN_MINUTES*60-(datetime.now()-LAST_SIGNAL[coin]).total_seconds()
                    if s>0: cd=f" CD{int(s/60)}m"
                print(f"{coin}${lc:,.0f} {trend} R={lr7:.0f} V={vr:.1f}x{cd} [{ns}]", end="  ", flush=True)
                
                for rl,d,detail in alerts:
                    cid=f"{sym}_{ts}_{rl}"
                    if cid not in SEEN:
                        SEEN.add(cid)
                        if len(SEEN)>200: SEEN.clear()
                        LAST_SIGNAL[coin]=datetime.now()
                        PENDING[(sym,ts)]={"time":now_str,"coin":coin,"direction":d,"rule":rl,"entry_price":lc,"detail":detail}
                        lat=(datetime.now(timezone.utc)-pd.Timestamp(ts,unit="ms").tz_localize("UTC")).total_seconds()
                        log.info("SIG: %s %s [%s] @$%.2f lat=%.1fs", coin, d, rl, lc, lat)
                        push_wechat(f"SIG {coin} {d} [{rl}] {lat:.1f}s",
                                   f"Pair:{coin}\nRule:{rl}\nEntry:${lc:,.2f}\nDetail:{detail}\nLatency:{lat:.1f}s\n{now_str}")
            
            if new_records:
                new_df=pd.DataFrame(new_records)
                trade_df=pd.concat([trade_df,new_df],ignore_index=True)
                save_trade_log(trade_df)
                tn=len(trade_df);tw=(trade_df["result"]=="WIN").sum();tpnl=trade_df["pnl"].sum()
                print(f"\n settle{len(new_records)} total{tn} W{tw} {tw/tn*100:.1f}% PnL{tpnl:+d}u", end="")
            
            print(f" [{bj}] ws:{processed}")
            time.sleep(0.3)
        except Exception as e:
            log.error("loop: %s", traceback.format_exc())
            time.sleep(2)

if __name__=="__main__":
    main()
