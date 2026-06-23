# -*- coding: utf-8 -*-
"""Quantify: mid-candle noise (iloc[-1]) vs closed-candle (iloc[-2])"""
import csv, math
from collections import defaultdict
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
    op=[c["open"] for c in cd]
    n=len(cl); e200=ema(cl,200)
    for idx in range(60,n-1):
        dt=datetime.fromtimestamp(cd[idx]["ts"],tz=timezone.utc)
        # Current candle (still forming in live)
        g=sum(max(cl[i]-cl[i-1],0) for i in range(idx-6,idx+1))
        ls=sum(max(cl[i-1]-cl[i],0) for i in range(idx-6,idx+1))
        r7c=100*g/(g+ls) if (g+ls)>0 else 50
        h14c=max(hi[idx-13:idx+1]); l14c=min(lo[idx-13:idx+1])
        s14c=100*(cl[idx]-l14c)/(h14c-l14c) if h14c!=l14c else 50
        s3c=100*(cl[idx]-min(lo[idx-2:idx+1]))/(max(hi[idx-2:idx+1])-min(lo[idx-2:idx+1])) if max(hi[idx-2:idx+1])!=min(lo[idx-2:idx+1]) else 50
        v20=sum(vo[idx-19:idx+1])/20; v50=sum(vo[idx-49:idx+1])/50
        vrc=v20/v50 if v50>0 else 1.0
        h20=max(hi[idx-19:idx+1]); l20=min(lo[idx-19:idx+1])
        p20c=(cl[idx]-l20)/(h20-l20) if h20!=l20 else 0.5
        d200c=(cl[idx]-e200[idx])/e200[idx]*100 if e200[idx]>0 else 0
        
        # Previous closed candle
        g2=sum(max(cl[i]-cl[i-1],0) for i in range(idx-7,idx))
        ls2=sum(max(cl[i-1]-cl[i],0) for i in range(idx-7,idx))
        r7p=100*g2/(g2+ls2) if (g2+ls2)>0 else 50
        h14p=max(hi[idx-14:idx]); l14p=min(lo[idx-14:idx])
        s14p=100*(cl[idx-1]-l14p)/(h14p-l14p) if h14p!=l14p else 50
        s3p=100*(cl[idx-1]-min(lo[idx-3:idx]))/(max(hi[idx-3:idx])-min(lo[idx-3:idx])) if max(hi[idx-3:idx])!=min(lo[idx-3:idx]) else 50
        v20p=sum(vo[idx-20:idx])/20; v50p=sum(vo[idx-50:idx])/50
        vrp=v20p/v50p if v50p>0 else 1.0
        h20p=max(hi[idx-20:idx]); l20p=min(lo[idx-20:idx])
        p20p=(cl[idx-1]-l20p)/(h20p-l20p) if h20p!=l20p else 0.5
        d200p=(cl[idx-1]-e200[idx-1])/e200[idx-1]*100 if e200[idx-1]>0 else 0
        
        rows.append({
            "idx":idx,"ts":cd[idx]["ts"],"hr":dt.hour,
            # Current (live mid-candle)
            "cr7":r7c,"cs14":s14c,"cs3":s3c,"cvr":vrc,"cp20":p20c,"cd200":d200c,"cc":cl[idx],
            # Previous (closed candle) 
            "pr7":r7p,"ps14":s14p,"ps3":s3p,"pvr":vrp,"pp20":p20p,"pd200":d200p,"pc":cl[idx-1],
            "nc":cl[idx+1] if idx+1<n else None
        })
    return rows

def score_row(r, p20t, r7t, vrt, s14t, ms, use_prev=True):
    """use_prev=True: evaluate on closed candle; False: on current forming candle"""
    if use_prev:
        sc=0
        if r["pp20"]<p20t: sc+=1
        if r["pr7"]<r7t: sc+=1
        if r["pvr"]>vrt: sc+=1
        if r["ps14"]<s14t: sc+=1
        return sc >= ms
    else:
        sc=0
        if r["cp20"]<p20t: sc+=1
        if r["cr7"]<r7t: sc+=1
        if r["cvr"]>vrt: sc+=1
        if r["cs14"]<s14t: sc+=1
        return sc >= ms

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

print(f"Precomputed {len(all_rows)} evaluation points")
print(f"Range: {datetime.fromtimestamp(all_rows[0]['ts'])} to {datetime.fromtimestamp(all_rows[-1]['ts'])}")
print()

# Simulate BOTH modes
for label, use_prev in [("Closed-Candle (iloc[-2])", True), ("Mid-Candle Live (iloc[-1])", False)]:
    signals=[]
    last_idx=-999
    for r in all_rows:
        if r["hr"] in BH: continue
        if r["idx"]-last_idx<2: continue
        p=eth_p if r["sym"]=="ETH" else btc_p
        p20t=p["p20"]; r7t=p["r7"]; vrt=p["vr"]; s14t=p["s14"]; ms=p["ms"]
        
        if not score_row(r,p20t,r7t,vrt,s14t,ms,use_prev): continue
        # Check regime filter
        d200 = r["pd200"] if use_prev else r["cd200"]
        if d200 > 12.0: continue
        if r["nc"] is None: continue
        
        # Outcome: next candle close vs entry price
        entry = r["pc"] if use_prev else r["cc"]
        win = r["nc"] > entry
        last_idx=r["idx"]
        signals.append({"w":win,"sym":r["sym"],
            "entry":entry,"exit":r["nc"]})
    
    w=sum(1 for s in signals if s["w"])
    t=len(signals)
    wr=w/t*100 if t else 0
    pnl=sum(4 if s["w"] else -5 for s in signals)
    
    # Signal frequency (signals per day)
    days=(all_rows[-1]["ts"]-all_rows[0]["ts"])/86400
    spd=t/days if days>0 else 0
    
    print(f"{label}:")
    print(f"  Signals: {t} ({spd:.1f}/day)")
    print(f"  WR: {wr:.1f}%")
    print(f"  PnL: {pnl:+d}u")
    
    # Count duplicate signals (same candle triggering multiple times)
    if not use_prev:
        candle_ts={}
        for s in signals:
            ts_key=s.get("_ts",0)
        print(f"  (Mid-candle has no dedup - would re-trigger on every tick!)")
    print()

# Quantify the noise: how many EXTRA signals does mid-candle produce?
# For this, count unique candle timestamps in signals
# In live mode without dedup, the same candle could trigger multiple times
print("="*60)
print("NOISE QUANTIFICATION")
print("="*60)

# Run mid-candle WITHOUT cooldown to count raw signal count
raw_signals=[]
for r in all_rows:
    if r["hr"] in BH: continue
    p=eth_p if r["sym"]=="ETH" else btc_p
    p20t=p["p20"]; r7t=p["r7"]; vrt=p["vr"]; s14t=p["s14"]; ms=p["ms"]
    if not score_row(r,p20t,r7t,vrt,s14t,ms,False): continue
    if r["cd200"]>12.0: continue
    if r["nc"] is None: continue
    raw_signals.append(r["ts"])

# Count unique candles vs total triggers
unique_candles=len(set(raw_signals))
total_triggers=len(raw_signals)
print(f"Mid-candle raw triggers: {total_triggers}")
print(f"Unique candles triggered: {unique_candles}")
if unique_candles>0:
    print(f"Noise ratio: {total_triggers/unique_candles:.1f}x (each candle triggers this many times!)")
    print(f"False re-triggers: {total_triggers-unique_candles} ({ (total_triggers-unique_candles)/total_triggers*100:.0f}% are duplicates)")

# Compare closed vs mid-candle signal count
closed_sigs=[]
for r in all_rows:
    if r["hr"] in BH: continue
    p=eth_p if r["sym"]=="ETH" else btc_p
    if not score_row(r,p["p20"],p["r7"],p["vr"],p["s14"],p["ms"],True): continue
    if r["pd200"]>12.0: continue
    if r["nc"] is None: continue
    closed_sigs.append(r["ts"])

print(f"\nClosed-candle signals: {len(closed_sigs)}")
print(f"Mid-candle signals: {total_triggers} (raw, no dedup)")
print(f"Signal inflation: {total_triggers/len(closed_sigs):.1f}x more signals in live mode without closed-candle fix!")

print("\nDONE")
