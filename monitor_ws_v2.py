# -*- coding: utf-8 -*-
"""WebSocket Event Contract Monitor v1 - Real-time 15m candles + ticker verification
   Solves the stale-price problem: uses live WS data + ticker check before signal"""
import time, json, requests, os, logging, sys, traceback, subprocess, threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
try: import pandas_ta_classic as ta
except: import pandas_ta as ta

# ---- CONFIG ----
SYMBOLS = ["BTC-USDT", "ETH-USDT"]
BAR = "15m"; LIMIT = 200
SENDKEY = os.environ.get("SENDKEY", "")
TRADE_LOG = Path("trade_log.csv")
CONTRACT_CANDLES = 2  # settle after 2 bars (up to 30min window)
MAX_RUN_MIN = 350

SNIPER = {
    "ETH-USDT": {"p20": 0.12, "rsi7": 22, "vr20": 1.8, "stoch": 18, "min_s": 2.0, "coin": "ETH"},
    "BTC-USDT": {"p20": 0.18, "rsi7": 15, "vr20": 0.9, "stoch": 10, "min_s": 2.0, "coin": "BTC"},
}

FG_VALUE = 50
PRICE_SLIPPAGE_MAX = 0.008  # skip signal if ticker > 0.8% away from candle close



logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
SEEN = set(); PENDING = {}; LAST_SIGNAL = {}
DATA_LOCK = threading.Lock()
dfs = {}  # sym -> DataFrame (updated in real-time)
ws_connected = False

# ---- UTILS ----
def push_wechat(title, content):
    if not SENDKEY:
        log.error("PUSH: SENDKEY not set")
        return False
    try:
        r = requests.post(f"https://sctapi.ftqq.com/{SENDKEY}.send", data={"title": title, "desp": content}, timeout=10)
        if r.status_code == 200:
            resp = r.json()
            if resp.get("code") == 0:
                log.info("PUSH OK: %s", title[:50])
                return True
            else:
                log.error("PUSH FAIL: %s - %s", r.status_code, resp.get("message", ""))
                return False
        log.error("PUSH HTTP %d: %s", r.status_code, title[:50])
        return False
    except Exception as e:
        log.error("PUSH EXCEPTION: %s", e)
        return False

def load_trade_log():
    if TRADE_LOG.exists(): return pd.read_csv(TRADE_LOG)
    return pd.DataFrame(columns=["time","coin","direction","rule","entry_price","exit_price","pnl","result","detail"])

def save_trade_log(df):
    df.to_csv(TRADE_LOG, index=False, encoding="utf-8")
    try:
        time.sleep(random.uniform(1, 5))
        subprocess.run(["git","pull","--rebase"], capture_output=True, timeout=15)
        subprocess.run(["git","add","trade_log.csv"], capture_output=True, timeout=10)
        r = subprocess.run(["git","commit","-m","update trade log"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 or "nothing to commit" in (r.stdout or ""):
            subprocess.run(["git","push"], capture_output=True, text=True, timeout=30)
    except: pass

def fetch_fear_greed():
    global FG_VALUE
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = r.json().get("data", [{}])[0]
        FG_VALUE = int(data.get("value", 50))
        log.info("FG: %d (%s)", FG_VALUE, data.get("value_classification",""))
    except: pass

def get_fg_multiplier(fg):
    if fg < 15: return 3
    if fg < 25: return 2
    return 1

def fetch_ticker(sym):
    """Get real-time ticker price - the freshest data available"""
    try:
        r = requests.get("https://www.okx.com/api/v5/market/ticker", params={"instId": sym}, timeout=5)
        if r.json()["code"] == "0":
            data = r.json()["data"][0]
            return float(data["last"])
    except: pass
    return None

# ---- REST FALLBACK ----
def fetch_rest_candles(sym):
    url = "https://www.okx.com/api/v5/market/candles"
    try:
        r = requests.get(url, params={"instId": sym, "bar": BAR, "limit": LIMIT}, timeout=10)
        if r.json()["code"] != "0": return None
        rows = [{"ts": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                  "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
                for c in reversed(r.json()["data"])]
        return rows
    except: return None

def build_df(rows):
    df = pd.DataFrame(rows)
    for col in ["open","high","low","close","volume","ts"]:
        df[col] = pd.to_numeric(df[col])
    df.index = df["ts"].astype(int)
    return df.sort_index()

# ---- STRATEGY (v24 SNIPER) ----
def compute_score(df, params):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    if len(c) < 80: return None
    # Use iloc[-1] = current in-progress candle (real-time!)
    idx = -1
    rsi7 = ta.rsi(c, 7)
    p20 = (c - l.rolling(20).min()) / (h.rolling(20).max() - l.rolling(20).min() + 1e-10)
    vr20 = v / v.rolling(20).mean()
    l14, h14 = l.rolling(14).min(), h.rolling(14).max()
    stoch_k = 100 * (c - l14) / (h14 - l14 + 1e-10)
    
    lc = c.iloc[idx]; lr7 = rsi7.iloc[idx]; lp20 = p20.iloc[idx]
    lvr = vr20.iloc[idx]; lsk = stoch_k.iloc[idx]
    if pd.isna(lr7) or pd.isna(lp20): return None
    
    ema20 = ta.ema(c, 20).iloc[idx]; ema50 = ta.ema(c, 60).iloc[idx]
    ema200 = ta.ema(c, 200).iloc[idx]
    is_uptrend = not pd.isna(ema200) and lc > ema200 and not pd.isna(ema20) and not pd.isna(ema50) and ema20 > ema50
    
    score = 0.0; p = params
    if lp20 < p["p20"]: score += 1
    if lr7 < p["rsi7"]: score += 1
    if lvr > p["vr20"]: score += 1
    if not pd.isna(lsk) and lsk < p["stoch"]: score += 1
    
    trend = "UP" if is_uptrend else "DN"
    return {"score": score, "close": lc, "rsi7": lr7, "p20": lp20, "vr": lvr,
            "stoch": lsk if not pd.isna(lsk) else 50, "trend": trend,
            "is_uptrend": is_uptrend, "ts": df.index[idx], "ema200": ema200}

def classify_signal(sc, params):
    p = params; rules = []
    if sc["p20"] < p["p20"]: rules.append("PP")
    if sc["rsi7"] < p["rsi7"]: rules.append("RSI")
    if sc["vr"] > p["vr20"]: rules.append("VR")
    if sc["stoch"] < p["stoch"]: rules.append("SK")
    return "+".join(rules), "LONG"

# ---- WEBSOCKET ----
def update_candle(df, candle):
    """Update DataFrame from WS candle data"""
    ts = int(candle[0]); o = float(candle[1]); h = float(candle[2])
    l = float(candle[3]); c_val = float(candle[4]); v = float(candle[5])
    if ts in df.index:
        df.loc[ts, ["open","high","low","close","volume"]] = [o, h, l, c_val, v]
    elif ts > df.index[-1]:
        new_row = pd.DataFrame({"open":[o],"high":[h],"low":[l],"close":[c_val],"volume":[v],"ts":[ts]}, index=[ts])
        df = pd.concat([df, new_row]).sort_index()
        if len(df) > LIMIT: df = df.iloc[-LIMIT:]
    return df

def on_message(ws, message):
    global dfs
    try:
        data = json.loads(message)
        if "data" not in data: return
        for item in data["data"]:
            candle = item["candle"]; sym = item["instId"]
            with DATA_LOCK:
                if sym in dfs:
                    dfs[sym] = update_candle(dfs[sym], [candle[0], candle[1], candle[2], candle[3], candle[4], candle[5]])
    except: pass

def on_open(ws):
    global ws_connected
    ws_connected = True
    channels = [{"channel": f"candle{BAR}", "instId": s} for s in SYMBOLS]
    ws.send(json.dumps({"op": "subscribe", "args": channels}))
    log.info("WS connected, subscribed to %s", SYMBOLS)

def on_error(ws, error):
    global ws_connected
    ws_connected = False
    log.error("WS error: %s", error)

def on_close(ws, status, msg):
    global ws_connected
    ws_connected = False
    log.warning("WS closed: %s %s", status, msg)

def ws_connect():
    """Start WebSocket in background thread"""
    try:
        import websocket
        ws = websocket.WebSocketApp(
            "wss://ws.okx.com:8443/ws/v5/business",
            on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close
        )
        t = threading.Thread(target=ws.run_forever, daemon=True)
        t.start()
        return True
    except ImportError:
        log.error("websocket-client not installed, using REST fallback")
        return False

# ---- SETTLEMENT ----
def check_settlements():
    """Settle trades based on candle count"""
    results = []
    with DATA_LOCK:
        to_remove = []
        for (sym, entry_ts), trade in list(PENDING.items()):
            if sym not in dfs: continue
            df = dfs[sym]
            if entry_ts not in df.index: continue
            # Count bars since entry
            bars_passed = len(df) - df.index.get_loc(entry_ts) - 1
            if bars_passed < CONTRACT_CANDLES: continue
            
            current_price = float(df.iloc[-1]["close"])
            entry_price = float(trade["entry_price"])
            is_win = current_price > entry_price
            multiplier = trade.get("mult", 1)
            pnl = 4 * multiplier if is_win else -5 * multiplier
            result_cn = "赢" if is_win else "输"
            
            results.append({
                "time": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
                "coin": trade["coin"], "direction": trade["direction"],
                "rule": trade["rule"], "entry_price": entry_price,
                "exit_price": current_price, "pnl": pnl,
                "result": "WIN" if is_win else "LOSS", "detail": trade.get("detail","")
            })
            to_remove.append((sym, entry_ts))
            
            push_wechat(
                f"【事件】{'赚' if is_win else '亏'}了 {trade['coin']} 做多 {pnl:+d}u",
                f"币种: {trade['coin']} | 做多\n结果: {result_cn}{abs(pnl)}u\n入场: ${entry_price:,.2f} -> 出场: ${current_price:,.2f}\n信号: {trade['rule']}\n盈亏: {pnl:+d}u\n{datetime.now(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')}"
            )
        for k in to_remove: del PENDING[k]
    return results

# ---- MAIN LOOP ----
def run():
    global dfs, ws_connected
    start_time = datetime.now(); loop = 0
    trade_df = load_trade_log()
    n = len(trade_df); tp = trade_df["pnl"].sum() if n else 0
    if n:
        w = (trade_df["result"] == "WIN").sum()
        log.info("History: %d trades %.1f%% PnL%+d", n, w/n*100, tp)
    
    fetch_fear_greed()
    fg_m = get_fg_multiplier(FG_VALUE)
    
    # Initial data load via REST
    log.info("Loading initial candle data...")
    for sym in SYMBOLS:
        rows = fetch_rest_candles(sym)
        if rows:
            with DATA_LOCK: dfs[sym] = build_df(rows)
            log.info("  %s: %d candles", sym, len(dfs[sym]))
    
    # Try WebSocket
    ws_ok = ws_connect()
    if not ws_ok:
        log.warning("WebSocket not available, using REST-only mode (30s refresh)")
    
    push_wechat(
        "【事件】事件合约监控 已启动 (WS)",
        f"币种: BTC+ETH | 15分钟 WebSocket实时\n跳过上涨趋势 + 分级恐惧贪婪\n恐惧贪婪: {FG_VALUE} (仓位 x{fg_m})\n历史: {n}笔 累计{tp:+d}u\n{datetime.now(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')}"
    )
    log.info("SENDKEY: %s...", SENDKEY[:8] if SENDKEY else "NONE")
    log.info("SENDKEY: %s...%s", SENDKEY[:4] if SENDKEY else "NONE", SENDKEY[-4:] if SENDKEY else "NONE")
    log.info("WS Monitor v1 started - %s %s | FG=%d", BAR, SYMBOLS, FG_VALUE)
    
    random.seed()
    while True:
        try:
            if (datetime.now() - start_time).total_seconds() > MAX_RUN_MIN * 60:
                break
            
            # REST fallback: refresh candles every 30s if WS not connected
            if not ws_connected:
                for sym in SYMBOLS:
                    rows = fetch_rest_candles(sym)
                    if rows:
                        with DATA_LOCK: dfs[sym] = build_df(rows)
                time.sleep(30)
            
            # Check settlements
            new_recs = check_settlements()
            if new_recs:
                ndf = pd.DataFrame(new_recs)
                trade_df = pd.concat([trade_df, ndf], ignore_index=True)
                save_trade_log(trade_df)
                tn = len(trade_df); tw = (trade_df["result"] == "WIN").sum()
                tp2 = trade_df["pnl"].sum()
                log.info("Settled: %d | Total: %d W:%.1f%% PnL:%+d", len(new_recs), tn, tw/tn*100 if tn else 0, tp2)
            
            # Signal detection (on each loop iteration)
            bj = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%H:%M:%S")
            status = []
            
            with DATA_LOCK:
                for sym in SYMBOLS:
                    if sym not in dfs or len(dfs[sym]) < 80: continue
                    sc = compute_score(dfs[sym], SNIPER[sym])
                    if sc is None: continue
                    coin = SNIPER[sym]["coin"]; p = SNIPER[sym]
                    cid = f"{sym}_{sc['ts']}"
                    
                    if sc["score"] >= p["min_s"] and cid not in SEEN:
                        if sc["is_uptrend"]:
                            log.info("SKIP %s: uptrend (score=%.1f)", coin, sc["score"])
                            SEEN.add(cid); continue
                        
                        # ---- CRITICAL: Real-time price check ----
                        ticker_price = fetch_ticker(sym)
                        if ticker_price:
                            slippage = abs(ticker_price - sc["close"]) / sc["close"]
                            if slippage > PRICE_SLIPPAGE_MAX:
                                log.info("SKIP %s: price moved %.2f%% (candle=$%.2f ticker=$%.2f)", coin, slippage*100, sc["close"], ticker_price)
                                SEEN.add(cid); continue
                            # Use ticker price as entry (freshest)
                            entry_price = ticker_price
                        else:
                            entry_price = sc["close"]
                        # ----------------------------------------
                        
                        rule_str, direction = classify_signal(sc, p)
                        score_mult = 2 if sc["score"] >= 3 else 1
                        fg_mult = get_fg_multiplier(FG_VALUE)
                        total_mult = score_mult * fg_mult
                        
                        detail = f"P20={sc['p20']:.3f} R7={sc['rsi7']:.0f} VR={sc['vr']:.2f} SK={sc['stoch']:.0f} FG={FG_VALUE}"
                        SEEN.add(cid)
                        if len(SEEN) > 1000: SEEN.clear()
                        LAST_SIGNAL[coin] = datetime.now()
                        PENDING[(sym, sc["ts"])] = {
                            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "coin": coin, "direction": direction, "rule": rule_str,
                            "entry_price": entry_price, "detail": detail,
                            "score": sc["score"], "mult": total_mult
                        }
                        
                        tags = []
                        if score_mult >= 2: tags.append(f"S{score_mult}x")
                        if fg_mult >= 2: tags.append(f"FG{fg_mult}x")
                        tag_str = " ".join(tags)
                        log.info("SIGNAL %s %s [%s] candle=$%.2f ticker=$%.2f mult=%dx",
                                coin, direction, rule_str, sc["close"], entry_price, total_mult)
                        push_wechat(
                            f"【事件】开仓 {coin} 做多 [{rule_str}] {tag_str}",
                            f"币种: {coin} | 做多\n信号: {rule_str}\n入场: ${entry_price:,.2f} (实时价格)\n"
                            f"{detail}\n仓位: {total_mult}张 (信号{score_mult}x 恐惧贪婪{fg_mult}x)\n"
                            f"风险: {total_mult*5}u | 盈利: {total_mult*4}u\n"
                            f"{datetime.now(timezone(timedelta(hours=8))).strftime('%m/%d %H:%M')}"
                        )
                    
                    cd_tag = ""
                    if coin in LAST_SIGNAL:
                        sec_ago = (datetime.now() - LAST_SIGNAL[coin]).total_seconds()
                        if sec_ago < 120: cd_tag = f" CD{int((120-sec_ago)/60)}m"
                    status.append(f"{coin}${sc['close']:,.0f} {sc['trend']} R{sc['rsi7']:.0f} V{sc['vr']:.1f} S{sc['score']:.1f}{cd_tag}")
            
            fg_m2 = get_fg_multiplier(FG_VALUE)
            ws_tag = "WS" if ws_connected else "REST"
            print(f"[{bj}] {ws_tag} FG:{FG_VALUE}(x{fg_m2}) | {' | '.join(status)} | pend:{len(PENDING)} sigs:{len(SEEN)} L{loop}")
            loop += 1
            
            if ws_connected:
                time.sleep(1)  # WS mode: just check every second
            # REST mode already sleeps 30s above
            
        except KeyboardInterrupt: break
        except Exception as e:
            log.error("Loop: %s", traceback.format_exc())
            time.sleep(10)
    
    log.info("Stopped. %.0f min", (datetime.now() - start_time).total_seconds() / 60)

if __name__ == "__main__":
    import random
    run()
