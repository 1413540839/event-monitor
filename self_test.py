# -*- coding: utf-8 -*-
"""self_test.py - Validate strategy against latest data"""
import pandas as pd, numpy as np
from pathlib import Path
import requests, csv, json, sys
import pandas_ta_classic as ta

OUTPUTS = Path(__file__).parent

STOCH3_MAX = 15  # v13

PARAMS = {
    "ETH-USDT": {"p20": 0.10, "rsi7": 18, "vr20": 2.0, "stoch": 15, "min_score": 2.5},
    "BTC-USDT": {"p20": 0.15, "rsi7": 12, "vr20": 1.0, "stoch": 8, "min_score": 2.5},
}

def fetch_data(sym, bars=500):
    url = "https://api.binance.com/api/v3/klines"
    all_rows = []; end_t = None
    for _ in range(bars//1000 + 1):
        params = {"symbol": sym, "interval": "1h", "limit": min(1000, bars)}
        if end_t: params["endTime"] = end_t
        r = requests.get(url, params=params, timeout=15)
        d = r.json()
        if not isinstance(d, list) or len(d) <= 1: break
        all_rows = d + all_rows; end_t = int(d[0][0]) - 1
    return all_rows

def backtest(df, p20_t, rsi_t, vr_t, st_t, ms, bad_hours=[5,12,14,22], stoch3_max=15):
    c,h,l,v = df["close"].values,df["high"].values,df["low"].values,df["volume"].values
    n = len(c)
    if n < 80: return None
    
    rsi7 = ta.rsi(pd.Series(c), 7).values
    rsi14 = ta.rsi(pd.Series(c), 14).values
    p20 = (c - pd.Series(l).rolling(20).min().values) / (pd.Series(h).rolling(20).max().values - pd.Series(l).rolling(20).min().values + 1e-10)
    vr20 = v / (pd.Series(v).rolling(20).mean().values + 1e-10)
    stk = 100 * (c - pd.Series(l).rolling(14).min().values) / (pd.Series(h).rolling(14).max().values - pd.Series(l).rolling(14).min().values + 1e-10)
    ret5 = np.zeros(n); ret5[5:] = (c[5:] - c[:-5]) / (c[:-5] + 1e-10)
    
    trades = []
    for i in range(200, n-1):
        if df.index[i].hour in bad_hours: continue
        if np.isnan(rsi7[i]): continue
        
        score = 0.0
        if p20[i] < p20_t: score += 1
        if rsi7[i] < rsi_t: score += 1
        if not np.isnan(rsi14[i]) and rsi14[i] < rsi_t+5: score += 0.5
        if vr20[i] > vr_t: score += 1
        if not np.isnan(stk[i]) and stk[i] < st_t: score += 1
        if not np.isnan(ret5[i]) and ret5[i] < -0.03: score += 1
        
        if score >= ms:
            mult = 2 if score >= ms+0.5 else 1
            trades.append(4*mult if c[i+1] > c[i] else -5*mult)
    
    if not trades: return None
    arr = np.array(trades)
    dd = int((np.cumsum(arr) - np.maximum.accumulate(np.cumsum(arr))).min())
    return {"trades": len(arr), "wr": (arr>0).mean(), "pnl": int(arr.sum()), "dd": dd}

if __name__ == "__main__":
    print("=" * 60)
    print("v12g STRATEGY SELF-TEST")
    print("=" * 60)
    
    status = []
    for sym in ["ETHUSDT", "BTCUSDT"]:
        key = sym.replace("USDT", "-USDT")
        rows = fetch_data(sym, 500)
        if not rows:
            print(f"FAIL: cannot fetch {sym}")
            status.append(False)
            continue
        
        df = pd.DataFrame(rows, columns=["ts","open","high","low","close","volume"] +
                         ["_"]*6).iloc[:,:6].astype(float)
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df.set_index("ts", inplace=True); df.sort_index(inplace=True)
        
        p = PARAMS[key]
        r = backtest(df, p["p20"], p["rsi7"], p["vr20"], p["stoch"], p["min_score"])
        if r:
            breakeven = 5/9
            above = "PASS" if r["wr"] > breakeven else "FAIL"
            print(f"{key}: {r['trades']}t WR={r['wr']:.1%} PnL={r['pnl']:+.0f}u DD={r['dd']:+.0f}u [{above}]")
            status.append(r["wr"] > breakeven)
        else:
            print(f"{key}: insufficient data")
            status.append(False)
    
    all_pass = all(status)
    print(f"\n{'ALL TESTS PASSED' if all_pass else 'SOME TESTS FAILED'}")
    sys.exit(0 if all_pass else 1)
