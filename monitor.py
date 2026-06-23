# -*- coding: utf-8 -*-
"""v13 - stoch3 filter + asymmetric sizing (v12g base)
   BTC: P20<0.15 RSI7<12 VR20>1.0 Stoch<8 Score>=2.5 (2x@3.0)
   ETH: P20<0.10 RSI7<18 VR20>2.0 Stoch<15 Score>=2.5 (2x@3.0)
   NEW: stoch3<15 filter, score4=2x sizing
   Bad hours: UTC 5,12,14,22 (train/test validated)
   Backtest: 490t WR=65.1% PnL=+520u (3yr), LiveReplay: 484t WR=64.0% PnL=+457u"""
import time, json, requests, os, logging, sys, traceback, subprocess, threading, queue
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np, pandas as pd
try: import pandas_ta_classic as ta
except: import pandas_ta as ta
try: import websocket; HAS_WS = True
except ImportError: HAS_WS = False

SYMBOLS = ["ETH-USDT", "BTC-USDT"]
BAR = "1H"; LIMIT = 200
SENDKEY = os.environ.get("SENDKEY", "")
TRADE_LOG = Path("trade_log.csv")
CONTRACT_CANDLES = 1

SNIPER = {
    "ETH-USDT": {"p20": 0.10, "rsi7": 18, "vr20": 2.0, "stoch": 15, "min_s": 2.5, "coin": "ETH"},
    "BTC-USDT": {"p20": 0.15, "rsi7": 12, "vr20": 1.0, "stoch": 8,  "min_s": 2.5, "coin": "BTC"},
}

STOCH3_MAX = 15  # v13: filter weak stochastic bounce signals

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
SEEN = set(); PENDING = {}; DAILY_PNL = {}; CRASH_HALT = {}; CONSEC_LOSS = 0
LAST_SIGNAL = {}; LAST_RESULT = {}  # track per-coin last signal time and result
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
        r = requests.post(f"https://sctapi.ftqq.com/{SENDKEY}.send", data={"title": title, "desp": content}, timeout=5)
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
        df.loc[candle_ts, "open"] = o; df.loc[candle_ts, "high"] = h
        df.loc[candle_ts, "low"] = l; df.loc[candle_ts, "close"] = c
        df.loc[candle_ts, "volume"] = v
    elif candle_ts > df.index[-1]:
        df.loc[candle_ts] = [o, h, l, c, v]
        df = df.sort_index()
        if len(df) > LIMIT: df = df.iloc[-LIMIT:]
    return df

def compute_score(df, params):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    if len(c) < 80: return None
    rsi7 = ta.rsi(c, 7); rsi14 = ta.rsi(c, 14)
    p20 = (c - l.rolling(20).min()) / (h.rolling(20).max() - l.rolling(20).min() + 1e-10)
    vr20 = v / v.rolling(20).mean()
    l14, h14 = l.rolling(14).min(), h.rolling(14).max()
    stoch_k = 100 * (c - l14) / (h14 - l14 + 1e-10)
    ret5 = c.pct_change(5); ema200 = ta.ema(c, 200)
    l3, h3 = l.rolling(3).min(), h.rolling(3).max()
    stoch3 = 100 * (c - l3) / (h3 - l3 + 1e-10)
    lc = c.iloc[-1]; lr7 = rsi7.iloc[-1]; lr14 = rsi14.iloc[-1]
    lp20 = p20.iloc[-1]; lvr = vr20.iloc[-1]; lsk = stoch_k.iloc[-1]
    lst3 = stoch3.iloc[-1]
    lr5 = ret5.iloc[-1]; le200 = ema200.iloc[-1]
    le20 = ta.ema(c, 20).iloc[-1]; le60 = ta.ema(c, 60).iloc[-1]
    if pd.isna(lr7): return None
    if not pd.isna(le200) and le200 > 0 and lc > le200 * 1.12: return None
    if not pd.isna(lst3) and lst3 >= STOCH3_MAX: return None
    score = 0.0; p = params
    if lp20 < p["p20"]: score += 1
    if lr7 < p["rsi7"]: score += 1
    if not pd.isna(lr14) and lr14 < p["rsi7"] + 5: score += 0.5
    if lvr > p["vr20"]: score += 1
    if not pd.isna(lsk) and lsk < p["stoch"]: score += 1
    if not pd.isna(lr5) and lr5 < -0.03: score += 1
    trend = "UP" if le20 > le60 else "DN"
    return {"score": score, "lc": lc, "lr7": lr7, "lvr": lvr,
            "lp20": lp20, "lsk": lsk, "trend": trend, "ts": int(df.index[-1])}

def analyze_signals(df, sym):
    try:
        p = SNIPER[sym]; coin = p["coin"]
        result = compute_score(df, p)
        if result is None: return [], 0, 0, 0, 0, 0, "NODATA"
        score = result["score"]; lc = result["lc"]; lr7 = result["lr7"]; lst3 = result.get("stoch3", 0); lst3 = result.get("stoch3", 0)
        lvr = result["lvr"]; ts = result["ts"]; trend = result["trend"]
        now = datetime.now(); today = now.strftime("%Y-%m-%d")
        if DAILY_PNL.get(today, 0) <= -10: return [], lc, lr7, lvr, ts, coin, "HALT"
        if CONSEC_LOSS >= 4 and CRASH_HALT.get("pause") and now < CRASH_HALT["pause"]:
            return [], lc, lr7, lvr, ts, coin, "PAUSE"
        if now.hour in (5, 12, 14, 22): return [], lc, lr7, lvr, ts, coin, "BADHR"

        # Adaptive cooldown
        cd_minutes = 60  # default
        if coin in LAST_RESULT:
            if LAST_RESULT[coin] == "WIN":
                cd_minutes = 0   # aggressive after win
            else:
                cd_minutes = 120  # conservative after loss

        if coin in LAST_SIGNAL:
            elapsed = (now - LAST_SIGNAL[coin]).total_seconds()
            if elapsed < cd_minutes * 60:
                return [], lc, lr7, lvr, ts, coin, "CD"

        alerts = []; ms = p["min_s"]
        if score >= ms:
            mult = 2 if score >= ms + 0.5 else 1
            tag = f"SN{mult}x"
            alerts.append((tag, "LONG", f"Sc={score:.1f} P20={result['lp20']:.2f} R7={lr7:.0f} V={lvr:.1f}x {trend}"))
        return alerts, lc, lr7, lvr, ts, coin, trend
    except Exception as e:
        log.error("analyze %s: %s", sym, e)
        return [], 0, 0, 0, 0, 0, "??"

def update_daily_pnl(pnl):
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if yesterday in DAILY_PNL: del DAILY_PNL[yesterday]
    DAILY_PNL[today] = DAILY_PNL.get(today, 0) + pnl

def settle_trades(dfs):
    global CONSEC_LOSS
    if not PENDING: return []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    completed = []; to_remove = []
    current_ts = {}
    for sym, df in dfs.items():
        if df is not None and len(df) > 0: current_ts[sym] = int(df.index[-1])
    for (sym, entry_ts), rec in list(PENDING.items()):
        if sym not in current_ts: continue
        candles_passed = int((current_ts[sym] - entry_ts) / 3600000)
        if candles_passed < CONTRACT_CANDLES: continue
        df = dfs[sym]; entry_price = rec["entry_price"]
        try: exit_idx = df.index.get_loc(entry_ts) + CONTRACT_CANDLES
        except: to_remove.append((sym, entry_ts)); continue
        if exit_idx >= len(df): exit_price = df.iloc[-1]["close"]
        else: exit_price = df.iloc[exit_idx]["close"]
        bet = 2 if "2x" in rec.get("rule", "") else 1
        if exit_price > entry_price: pnl = 4 * bet; result = "WIN"
        else: pnl = -5 * bet; result = "LOSE"
        completed.append({"time": now_str, "coin": rec["coin"], "direction": rec["direction"],
            "rule": rec["rule"], "entry_price": entry_price, "exit_price": exit_price,
            "pnl": pnl, "result": result, "detail": rec.get("detail", "")})
        # Update adaptive cooldown state
        LAST_RESULT[rec["coin"]] = result
        to_remove.append((sym, entry_ts))
    for k in to_remove: del PENDING[k]
    if completed:
        parts = []; tp = 0
        for r in completed:
            parts.append(f"{r['coin']} {r['rule']} {r['entry_price']:.2f}->{r['exit_price']:.2f} {r['pnl']:+d}u")
            tp += r["pnl"]; update_daily_pnl(r["pnl"])
            if r["result"] == "WIN": CONSEC_LOSS = 0
            else: CONSEC_LOSS += 1
        if CONSEC_LOSS >= 4: CRASH_HALT["pause"] = datetime.now() + timedelta(hours=4)
        push_wechat(f"\u7ed3\u7b97 {len(completed)}\u7b14 PnL{tp:+d}u",
                   "\n".join(parts) + f"\n\n\u603b:{tp:+d}u\n{now_str}")
    return completed

def on_message(ws, message):
    try:
        data = json.loads(message)
        if "data" not in data or not data["data"]: return
        for c in data["data"]:
            ws_queue.put((data["arg"]["instId"], int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])))
    except: pass

def on_error(ws, error): log.error("WS: %s", error)
def on_close(ws, st, msg): log.warning("WS closed: %s %s", st, msg)

def ws_connect():
    ws = websocket.WebSocketApp("wss://ws.okx.com:8443/ws/v5/business",
        on_message=on_message, on_error=on_error, on_close=on_close)
    t = threading.Thread(target=lambda: ws.run_forever(), daemon=True); t.start()
    time.sleep(1)
    ws.send(json.dumps({"op": "subscribe", "args": [{"channel": "candle1H", "instId": s} for s in SYMBOLS]}))
    log.info("WS: %s", SYMBOLS); return ws, t

def main():
    log.info("v13 - stoch3 filter + asymmetric sizing (v12g base))")
    trade_df = load_trade_log()
    n = len(trade_df); w = (trade_df["result"]=="WIN").sum() if n else 0
    tp = trade_df["pnl"].sum() if n else 0
    if n: log.info("history: %d %.1f%% PnL%+d", n, w/n*100, tp)
    for sym in SYMBOLS:
        rows = fetch_initial(sym)
        if rows:
            with DATA_LOCK: latest_data[sym] = build_df(rows)
            log.info("%s: %d candles", sym, len(latest_data[sym]))
    if HAS_WS:
        ws, wt = ws_connect()
        push_wechat("v12g SNIPER \u542f\u52a8", f"BTC+ETH 1H\n\u81ea\u9002\u5e94\u51b7\u5374\u671f\n\u8bad\u7ec3/\u6d4b\u8bd5\u9a8c\u8bc1\u5dee\u65f6\u6bb5\n\u56de\u6d4b7\u6708:245\u7b14 66.1% +484u\n\u5386\u53f2:{n}\u7b14 PnL{tp:+d}u")
    else:
        log.warning("No WS"); push_wechat("v12g SNIPER(REST)", f"REST mode\n\u5386\u53f2:{n}\u7b14 PnL{tp:+d}u")
    while True:
        try:
            bj = (datetime.now(timezone.utc)+timedelta(hours=8)).strftime("%H:%M:%S")
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); processed = 0
            while not ws_queue.empty():
                try:
                    sid, ts, o, h, l, c, v = ws_queue.get_nowait()
                    with DATA_LOCK:
                        if sid in latest_data: latest_data[sid] = update_candle(latest_data[sid], ts, o, h, l, c, v)
                    processed += 1
                except: pass
            with DATA_LOCK: dfs = {k: v.copy() for k, v in latest_data.items()}
            new_recs = settle_trades(dfs)
            for sym in SYMBOLS:
                if sym not in dfs: continue
                alerts, lc, lr7, lvr, ts, coin, trend = analyze_signals(dfs[sym], sym)
                ns = ",".join(rl for rl,_,_ in alerts) if alerts else "-"
                cd_tag = ""
                if coin in LAST_SIGNAL:
                    cd_min = 0 if LAST_RESULT.get(coin)=="WIN" else 120
                    rem = cd_min*60 - (datetime.now()-LAST_SIGNAL[coin]).total_seconds()
                    if rem > 0: cd_tag = f" CD{int(rem/60)}m"
                print(f"[{bj}] {coin}${lc:,.0f} {trend} R={lr7:.0f} V={lvr:.1f}x{cd_tag} [{ns}]", end="  ", flush=True)
                for rl, d, detail in alerts:
                    cid = f"{sym}_{ts}_{rl}"
                    if cid not in SEEN:
                        SEEN.add(cid)
                        if len(SEEN) > 500: SEEN.clear()
                        LAST_SIGNAL[coin] = datetime.now()
                        PENDING[(sym, ts)] = {"time": now_str, "coin": coin,
                            "direction": d, "rule": rl, "entry_price": lc, "detail": detail}
                        lat = (datetime.now(timezone.utc)-pd.Timestamp(ts,unit="ms").tz_localize("UTC")).total_seconds()
                        log.info("SIG: %s %s [%s] @$%.2f lat=%.1fs", coin, d, rl, lc, lat)
                        push_wechat(f"\u4fe1\u53f7 {coin} {d} [{rl}]",
                                   f"\u54c1\u79cd:{coin}\n\u89c4\u5219:{rl}\n\u5165\u573a:${lc:,.2f}\n\u8be6\u60c5:{detail}\n\u5ef6\u8fdf:{lat:.1f}s\n{now_str}")
            if new_recs:
                ndf = pd.DataFrame(new_recs)
                trade_df = pd.concat([trade_df, ndf], ignore_index=True)
                save_trade_log(trade_df)
                tn = len(trade_df); tw = (trade_df["result"]=="WIN").sum(); tpn = trade_df["pnl"].sum()
                print(f"\n \u7ed3\u7b97{len(new_recs)} \u603b{tn} \u80dc{tw} {tw/tn*100:.1f}% PnL{tpn:+d}u", end="")
            print(f" ws:{processed}")
            time.sleep(0.3)
        except Exception as e:
            log.error("loop: %s", traceback.format_exc()); time.sleep(2)

if __name__ == "__main__":
    main()




