# -*- coding: utf-8 -*-
"""v23 - Skip uptrend signals + FG double bet + REST polling
   ETH: P20<0.12 RSI7<22 VR20>1.8 Stoch<18 ms=2.0 2x@3+
   BTC: P20<0.18 RSI7<15 VR20>0.9 Stoch<10 ms=2.0 2x@3+
   FILTER: skip uptrend (price>EMA200 & EMA20>EMA50)
   SIZING: FG<25 = 2x bet | Score>=3 = 2x bet
   Backtest: 18.6/d WR=56.7% +209u/mo"""
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
FG_VALUE = 50  # cached fear & greed, default neutral

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

def fetch_fear_greed():
    """Fetch latest Fear & Greed index"""
    global FG_VALUE
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = r.json().get("data", [{}])[0]
        FG_VALUE = int(data.get("value", 50))
        log.info("Fear&Greed: %d (%s)", FG_VALUE, data.get("value_classification",""))
    except Exception as e:
        log.warning("FG fetch failed: %s", e)

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
    # Trend check: uptrend = price > EMA200 AND EMA20 > EMA50
    ema20 = ta.ema(c, 20).iloc[-2]; ema50 = ta.ema(c, 60).iloc[-2]
    ema200 = ta.ema(c, 200).iloc[-2]
    is_uptrend = not pd.isna(ema200) and lc > ema200 and not pd.isna(ema20) and not pd.isna(ema50) and ema20 > ema50
    
    score = 0.0; p = params
    if lp20 < p["p20"]: score += 1
    if lr7 < p["rsi7"]: score += 1
    if lvr > p["vr20"]: score += 1
    if not pd.isna(lsk) and lsk < p["stoch"]: score += 1
    
    trend = "UP" if is_uptrend else "DN"
    return {"score": score, "close": lc, "rsi7": lr7, "p20": lp20, "vr": lvr,
            "stoch": lsk if not pd.isna(lsk) else 50, "trend": trend,
            "is_uptrend": is_uptrend, "ts": df.index[-2], "ema200": ema200}

def classify_signal(sc, params):
    p = params; rules = []
    if sc["p20"] < p["p20"]: rules.append("PP")
    if sc["rsi7"] < p["rsi7"]: rules.append("RSI")
    if sc["vr"] > p["vr20"]: rules.append("VR")
    if sc["stoch"] < p["stoch"]: rules.append("SK")
    return "+".join(rules), "LONG"

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
        multiplier = trade.get("mult", 1)
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
    
    fetch_fear_greed()
    push_wechat(
        "Monitor v23 Started",
        f"BTC+ETH 15m\nSkip uptrend + FG double\nFG:{FG_VALUE}\nHistory:{n}t PnL{tp:+d}u\n{datetime.now().strftime('%H:%M')}"
    )
    log.info("v23 REST polling - %s %s | FG=%d", BAR, SYMBOLS, FG_VALUE)
    
    dfs = {}
    for sym in SYMBOLS:
        rows = fetch_candles(sym)
        if rows: dfs[sym] = build_df(rows)
        else: log.error("No data for %s", sym)
    
    if not dfs:
        log.critical("No data loaded"); return
    
    loop = 0; fg_loop = 0
    while True:
        try:
            if (datetime.now() - start_time).total_seconds() > MAX_RUN_MIN * 60:
                log.info("Max runtime reached"); break
            
            # Refresh FG every 60 loops (~30 min)
            fg_loop += 1
            if fg_loop >= 60:
                fetch_fear_greed()
                fg_loop = 0
            
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
                    # Skip uptrend signals
                    if sc["is_uptrend"]:
                        log.info("SKIP %s: uptrend (score=%.1f)", coin, sc["score"])
                        status.append(f"{coin}${sc['close']:,.0f} UP SKIP S{sc['score']:.1f}")
                        # Still mark as seen to avoid repeated logs
                        SEEN.add(cid)
                        continue
                    
                    rule_str, direction = classify_signal(sc, p)
                    # Determine multiplier
                    base_mult = 2 if sc["score"] >= 3 else 1
                    fg_mult = 2 if FG_VALUE < 25 else 1
                    total_mult = base_mult * fg_mult
                    
                    detail = f"P20={sc['p20']:.3f} R7={sc['rsi7']:.0f} VR={sc['vr']:.2f} SK={sc['stoch']:.0f} FG={FG_VALUE}"
                    SEEN.add(cid)
                    if len(SEEN) > 1000: SEEN.clear()
                    LAST_SIGNAL[coin] = datetime.now()
                    PENDING[(sym, sc["ts"])] = {
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "coin": coin, "direction": direction, "rule": rule_str,
                        "entry_price": sc["close"], "detail": detail,
                        "score": sc["score"], "mult": total_mult
                    }
                    
                    tags = []
                    if base_mult >= 2: tags.append("2x")
                    if fg_mult >= 2: tags.append("FG2x")
                    tag_str = " ".join(tags)
                    log.info("SIGNAL %s %s [%s] @$%.2f mult=%dx FG=%d",
                            coin, direction, rule_str, sc["close"], total_mult, FG_VALUE)
                    push_wechat(
                        f"SIGNAL {coin} {direction} [{rule_str}] {tag_str}",
                        f"Coin:{coin}\nRule:{rule_str}\nEntry:${sc['close']:,.2f}\n"
                        f"{detail}\nBet:{total_mult}x\nScore:{sc['score']:.1f}/4\n"
                        f"{datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                
                cd_tag = ""
                if coin in LAST_SIGNAL:
                    sec_ago = (datetime.now() - LAST_SIGNAL[coin]).total_seconds()
                    if sec_ago < 120:
                        cd_tag = f" CD{int((120-sec_ago)/60)}m"
                status.append(f"{coin}${sc['close']:,.0f} {sc['trend']} R{sc['rsi7']:.0f} V{sc['vr']:.1f} S{sc['score']:.1f}{cd_tag}")
            
            print(f"[{bj}] FG:{FG_VALUE} | {' | '.join(status)} | pend:{len(PENDING)} sigs:{len(SEEN)} L{loop}")
            loop += 1
            time.sleep(POLL_SEC)
        except KeyboardInterrupt: break
        except Exception as e:
            log.error("Loop: %s", traceback.format_exc())
            time.sleep(15)
    
    log.info("Stopped. %.0f min", (datetime.now()-start_time).total_seconds()/60)

if __name__ == "__main__":
    run()