# -*- coding: utf-8 -*-
"""v22 - REST polling monitor, WebSocket-free, GitHub Actions stable
   v21 params +25% wider: 24.1/d WR=55.7% +50u/mo"""
import time, json, requests, os, logging, sys, traceback, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
try: import pandas_ta_classic as ta
except: import pandas_ta as ta

SYMBOLS = ["ETH-USDT", "BTC-USDT"]
BAR = "15m"; LIMIT = 200; POLL_SEC = 30
SENDKEY = os.environ.get("SENDKEY", "")
TRADE_LOG = Path("trade_log.csv")

SNIPER = {
    "ETH-USDT": {"p20": 0.12, "rsi7": 22, "vr20": 1.8, "stoch": 18, "min_s": 2.0, "coin": "ETH"},
    "BTC-USDT": {"p20": 0.18, "rsi7": 15, "vr20": 0.9, "stoch": 10, "min_s": 2.0, "coin": "BTC"},
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
SEEN = set(); PENDING = {}; CONSEC_LOSS = 0; LAST_SIGNAL = {}
MAX_RUN_MIN = 350

def push_wechat(title, content):
    if not SENDKEY: return False
    try:
        r = requests.post(f"https://sctapi.ftqq.com/{SENDKEY}.send",
                         data={"title": title, "desp": content}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error("WeChat push failed: %s", e)
        return False

def load_trade_log():
    if TRADE_LOG.exists():
        return pd.read_csv(TRADE_LOG)
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

def fetch_candles(sym):
    url = "https://www.okx.com/api/v5/market/candles"
    try:
        r = requests.get(url, params={"instId": sym, "bar": BAR, "limit": LIMIT}, timeout=15)
        if r.json()["code"] != "0":
            log.warning("API error for %s: %s", sym, r.json().get("msg",""))
            return None
        rows = [{"ts": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                  "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
                for c in reversed(r.json()["data"])]
        return rows
    except Exception as e:
        log.error("Fetch failed %s: %s", sym, e)
        return None

def build_df(rows):
    df = pd.DataFrame(rows)
    for col in ["open","high","low","close","volume","ts"]:
        df[col] = pd.to_numeric(df[col])
    df.index = df["ts"].astype(int)
    return df.sort_index()

def compute_score(df, params):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    if len(c) < 80: return None
    rsi7 = ta.rsi(c, 7)
    p20 = (c - l.rolling(20).min()) / (h.rolling(20).max() - l.rolling(20).min() + 1e-10)
    vr20 = v / v.rolling(20).mean()
    l14, h14 = l.rolling(14).min(), h.rolling(14).max()
    stoch_k = 100 * (c - l14) / (h14 - l14 + 1e-10)
    lc = c.iloc[-2]; lr7 = rsi7.iloc[-2]
    lp20 = p20.iloc[-2]; lvr = vr20.iloc[-2]; lsk = stoch_k.iloc[-2]
    if pd.isna(lr7) or pd.isna(lp20): return None
    score = 0.0; p = params
    if lp20 < p["p20"]: score += 1
    if lr7 < p["rsi7"]: score += 1
    if lvr > p["vr20"]: score += 1
    if not pd.isna(lsk) and lsk < p["stoch"]: score += 1
    ema20 = ta.ema(c, 20).iloc[-2]; ema60 = ta.ema(c, 60).iloc[-2]
    trend = "BULL" if not pd.isna(ema20) and not pd.isna(ema60) and ema20 > ema60 else "BEAR"
    return {"score": score, "close": lc, "rsi7": lr7, "p20": lp20, "vr": lvr,
            "stoch": lsk if not pd.isna(lsk) else 50, "trend": trend,
            "ema20": ema20, "ema60": ema60, "ts": df.index[-2]}

def classify_signal(sc, params):
    p = params; rules = []
    if sc["p20"] < p["p20"]: rules.append("PP")
    if sc["rsi7"] < p["rsi7"]: rules.append("RSI")
    if sc["vr"] > p["vr20"]: rules.append("VR")
    if sc["stoch"] < p["stoch"]: rules.append("SK")
    rule_str = "+".join(rules)
    direction = "LONG"
    return rule_str, direction

def check_settlements(dfs_current):
    results = []; to_remove = []
    for (sym, entry_ts), trade in list(PENDING.items()):
        if sym not in dfs_current: continue
        df = dfs_current[sym]
        if len(df) < 2: continue
        latest_closed_ts = df.index[-2]
        if latest_closed_ts <= entry_ts: continue
        exit_price = float(df["close"].loc[df.index[df.index <= latest_closed_ts].max()])
        entry_price = float(trade["entry_price"])
        is_win = exit_price > entry_price
        multiplier = 2 if trade.get("score", 0) >= 3 else 1
        pnl = 4 * multiplier if is_win else -5 * multiplier
        result = "WIN" if is_win else "LOSS"
        results.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "coin": trade["coin"], "direction": trade["direction"],
            "rule": trade["rule"], "entry_price": entry_price,
            "exit_price": exit_price, "pnl": pnl,
            "result": result, "detail": trade.get("detail","")
        })
        to_remove.append((sym, entry_ts))
        global CONSEC_LOSS
        if not is_win: CONSEC_LOSS += 1
        else: CONSEC_LOSS = 0
        emoji = "[WIN]" if is_win else "[LOSS]"
        push_wechat(
            f"{emoji} {trade['coin']} {result} {pnl:+d}u [{trade['rule']}]",
            f"Coin:{trade['coin']}\nRule:{trade['rule']}\nEntry:${entry_price:,.2f}\nExit:${exit_price:,.2f}\nPnL:{pnl:+d}u\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    for k in to_remove: del PENDING[k]
    return results

def run():
    start_time = datetime.now()
    trade_df = load_trade_log()
    n = len(trade_df); tp = trade_df["pnl"].sum() if n else 0
    if n:
        w = (trade_df["result"] == "WIN").sum()
        log.info("History: %d trades %.1f%% PnL%+d", n, w/n*100, tp)
    
    push_wechat(
        "Monitor v22 Started",
        f"BTC+ETH 15m\n+25% params\nHistory:{n}t PnL{tp:+d}u\n{datetime.now().strftime('%H:%M')}"
    )
    log.info("v22 REST polling - %s %s", BAR, SYMBOLS)
    
    dfs = {}
    for sym in SYMBOLS:
        rows = fetch_candles(sym)
        if rows: dfs[sym] = build_df(rows)
        else: log.error("No data for %s", sym)
    
    if not dfs:
        log.critical("No data loaded"); return
    
    loop = 0
    while True:
        try:
            if (datetime.now() - start_time).total_seconds() > MAX_RUN_MIN * 60:
                log.info("Max runtime reached"); break
            
            for sym in SYMBOLS:
                rows = fetch_candles(sym)
                if rows: dfs[sym] = build_df(rows)
            
            new_recs = check_settlements(dfs)
            if new_recs:
                ndf = pd.DataFrame(new_recs)
                trade_df = pd.concat([trade_df, ndf], ignore_index=True)
                save_trade_log(trade_df)
                tn = len(trade_df); tw = (trade_df["result"] == "WIN").sum()
                tp = trade_df["pnl"].sum()
                log.info("Settled:%d Total:%d W:%d %.1f%% PnL:%+d", len(new_recs), tn, tw, tw/tn*100 if tn else 0, tp)
            
            bj = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%H:%M:%S")
            status = []
            for sym in SYMBOLS:
                if sym not in dfs or len(dfs[sym]) < 80: continue
                sc = compute_score(dfs[sym], SNIPER[sym])
                if sc is None: continue
                coin = SNIPER[sym]["coin"]; p = SNIPER[sym]
                
                cid = f"{sym}_{sc['ts']}"
                if sc["score"] >= p["min_s"] and cid not in SEEN:
                    rule_str, direction = classify_signal(sc, p)
                    detail = f"P20={sc['p20']:.3f} R7={sc['rsi7']:.0f} VR={sc['vr']:.2f} SK={sc['stoch']:.0f}"
                    SEEN.add(cid)
                    if len(SEEN) > 1000: SEEN.clear()
                    LAST_SIGNAL[coin] = datetime.now()
                    PENDING[(sym, sc["ts"])] = {
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "coin": coin, "direction": direction, "rule": rule_str,
                        "entry_price": sc["close"], "detail": detail, "score": sc["score"]
                    }
                    log.info("SIGNAL %s %s [%s] @$%.2f", coin, direction, rule_str, sc["close"])
                    push_wechat(
                        f"SIGNAL {coin} {direction} [{rule_str}]",
                        f"Coin:{coin}\nRule:{rule_str}\nEntry:${sc['close']:,.2f}\n{detail}\nScore:{sc['score']:.1f}/4"
                    )
                
                status.append(f"{coin}${sc['close']:,.0f} R{sc['rsi7']:.0f} V{sc['vr']:.1f} S{sc['score']:.1f}")
            
            print(f"[{bj}] {' | '.join(status)} | pend:{len(PENDING)} sig:{len(SEEN)} L{loop}")
            loop += 1
            time.sleep(POLL_SEC)
        except KeyboardInterrupt: break
        except Exception as e:
            log.error("Loop: %s", traceback.format_exc())
            time.sleep(15)
    
    log.info("Stopped. %.0f min", (datetime.now()-start_time).total_seconds()/60)

if __name__ == "__main__":
    run()