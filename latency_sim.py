# -*- coding: utf-8 -*-
"""Simulate latency + slippage impact on event contract outcomes"""
import csv, random, math
from datetime import datetime, timezone

BASE = r"C:\Users\14135\Documents\Codex\2026-06-17\new-chat\outputs"
BH = {5,12,14,22}

def ema(d,p):
    k=2.0/(p+1);o=[d[0]]
    for x in d[1:]: o.append(x*k+o[-1]*(1-k))
    return o

def load(path):
    rows=[]
    with open(path) as f:
        for r in csv.DictReader(f):
            for k in ["open","high","low","close","volume"]: r[k]=float(r[k])
            tc="timestamp" if "timestamp" in r else "ts"
            ts=int(r[tc]); rows.append({**r,"ts":ts if ts<99999999999 else ts//1000})
    return rows

def precompute(cd):
    rows=[]
    cl=[c["close"] for c in cd]
    hi=[c["high"] for c in cd]
    lo=[c["low"] for c in cd]
    vo=[c["volume"] for c in cd]
    n=len(cl); e200=ema(cl,200)
    for idx in range(60,n-1):
        dt=datetime.fromtimestamp(cd[idx]["ts"],tz=timezone.utc)
        g=sum(max(cl[i]-cl[i-1],0) for i in range(idx-7,idx))
        ls=sum(max(cl[i-1]-cl[i],0) for i in range(idx-7,idx))
        r7=100*g/(g+ls) if (g+ls)>0 else 50
        h14=max(hi[idx-14:idx]); l14=min(lo[idx-14:idx])
        s14=100*(cl[idx-1]-l14)/(h14-l14) if h14!=l14 else 50
        s3=100*(cl[idx-1]-min(lo[idx-3:idx]))/(max(hi[idx-3:idx])-min(lo[idx-3:idx])) if max(hi[idx-3:idx])!=min(lo[idx-3:idx]) else 50
        v20=sum(vo[idx-20:idx])/20; v50=sum(vo[idx-50:idx])/50
        vr=v20/v50 if v50>0 else 1.0
        h20=max(hi[idx-20:idx]); l20=min(lo[idx-20:idx])
        p20=(cl[idx-1]-l20)/(h20-l20) if h20!=l20 else 0.5
        d200=(cl[idx-1]-e200[idx-1])/e200[idx-1]*100 if e200[idx-1]>0 else 0
        rows.append({
            "idx":idx,"ts":cd[idx]["ts"],"hr":dt.hour,"r7":r7,"s14":s14,"s3":s3,
            "vr":vr,"p20":p20,"d200":d200,"c":cl[idx-1],"nc":cl[idx+1] if idx+1<n else None,
            "open":cd[idx]["open"],"high":cd[idx]["high"],"low":cd[idx]["low"],"close":cd[idx]["close"]
        })
    return rows

# Load
pairs=[("binance_ETHUSDT_1h_2023.csv","ETH"),("binance_BTCUSDT_1h_2023.csv","BTC"),
       ("binance_ETHUSDT_1h_2024.csv","ETH"),("binance_BTCUSDT_1h_2024.csv","BTC"),
       ("binance_ETHUSDT_1h_kline.csv","ETH"),("binance_BTCUSDT_1h_kline.csv","BTC")]

eth_p={"p20":0.10,"r7":18,"vr":2.0,"s14":15,"ms":2.5}
btc_p={"p20":0.15,"r7":12,"vr":1.0,"s14":8,"ms":2.5}

all_rows=[]
for path,sym in pairs:
    cd=load(BASE+"\\"+path)
    rows=precompute(cd)
    for r in rows: r["sym"]=sym
    all_rows.extend(rows)
all_rows.sort(key=lambda r:r["ts"])

print(f"Precomputed {len(all_rows)} points")

# Get baseline (no latency)
baseline=[]
last_idx=-999
for r in all_rows:
    if r["hr"] in BH: continue
    if r["idx"]-last_idx<2: continue
    p=eth_p if r["sym"]=="ETH" else btc_p
    sc=0
    if r["p20"]<p["p20"]: sc+=1
    if r["r7"]<p["r7"]: sc+=1
    if r["vr"]>p["vr"]: sc+=1
    if r["s14"]<p["s14"]: sc+=1
    if sc<p["ms"]: continue
    if r["d200"]>12.0: continue
    if r["nc"] is None: continue
    w=r["nc"]>r["c"]; last_idx=r["idx"]
    baseline.append({"w":w,"e":r["c"],"x":r["nc"]})

bw=sum(1 for s in baseline if s["w"])
print(f"Baseline (no latency): {len(baseline)}t WR={bw/len(baseline)*100:.1f}% PnL={sum(4 if s['w'] else -5 for s in baseline):+d}u")

# Simulate different latency scenarios
scenarios=[
    ("1s delay, 0.005% slip", 0.005, 0.005),
    ("3s delay, 0.01% slip", 0.01, 0.01),
    ("5s delay, 0.02% slip", 0.02, 0.02),
    ("10s delay, 0.05% slip", 0.05, 0.05),
    ("30s delay, 0.1% slip", 0.1, 0.1),
    ("1min delay, 0.2% slip", 0.2, 0.2),
]

print(f"\n{'Scenario':<30} {'Trades':>6} {'WR':>7} {'PnL':>8} {'WR Chg':>7} {'PnL Chg':>8}")
print("-"*72)

for label, slip_pct, _ in scenarios:
    results=[]
    last_idx=-999
    random.seed(42)
    for r in all_rows:
        if r["hr"] in BH: continue
        if r["idx"]-last_idx<2: continue
        p=eth_p if r["sym"]=="ETH" else btc_p
        sc=0
        if r["p20"]<p["p20"]: sc+=1
        if r["r7"]<p["r7"]: sc+=1
        if r["vr"]>p["vr"]: sc+=1
        if r["s14"]<p["s14"]: sc+=1
        if sc<p["ms"]: continue
        if r["d200"]>12.0: continue
        if r["nc"] is None: continue
        
        # Simulate: entry price = closed candle close + random slippage
        # Slippage always negative (worse entry for long)
        slip = 1 - random.uniform(0, slip_pct/100)
        entry_price = r["c"] * slip
        
        w= r["nc"] > entry_price  # harder to win with worse entry
        last_idx=r["idx"]
        results.append({"w":w})
    
    w=sum(1 for s in results if s["w"])
    t=len(results)
    wr=w/t*100 if t else 0
    pnl=sum(4 if s["w"] else -5 for s in results)
    wr_chg=wr-bw/len(baseline)*100
    pnl_chg=pnl-sum(4 if s["w"] else -5 for s in baseline)
    print(f"{label:<30} {t:>6} {wr:6.1f}% {pnl:+8d}u {wr_chg:+6.1f}% {pnl_chg:+8d}u")

# Now test the REAL killer: signal arriving mid-candle vs at candle close
print(f"\n{'='*72}")
print("REAL-WORLD SCENARIO: Signal triggers at various points within candle")
print("="*72)
print("Simulating: signal fires at X% into the candle, we enter at that mid-candle price")
print("Then check if NEXT candle close > our entry price")

for pct_into in [0, 10, 25, 50, 75, 90]:
    results=[]
    last_idx=-999
    for r in all_rows:
        if r["hr"] in BH: continue
        if r["idx"]-last_idx<2: continue
        p=eth_p if r["sym"]=="ETH" else btc_p
        sc=0
        if r["p20"]<p["p20"]: sc+=1
        if r["r7"]<p["r7"]: sc+=1
        if r["vr"]>p["vr"]: sc+=1
        if r["s14"]<p["s14"]: sc+=1
        if sc<p["ms"]: continue
        if r["d200"]>12.0: continue
        if r["nc"] is None: continue
        
        # Simulate entry at X% into the candle
        # Linear interpolation between open and close
        entry = r["open"] + (r["close"] - r["open"]) * pct_into/100
        # Add worst-case slippage (always a bit worse)
        entry *= (1 - 0.005/100)
        
        w = r["nc"] > entry
        last_idx=r["idx"]
        results.append({"w":w})
    
    w=sum(1 for s in results if s["w"])
    t=len(results)
    wr=w/t*100 if t else 0
    pnl=sum(4 if s["w"] else -5 for s in results)
    print(f"  Entry at {pct_into:3d}% into candle: {t}t WR={wr:.1f}% PnL={pnl:+d}u")

print("\nDONE")
