import csv
from datetime import datetime, timezone

BASE = r"C:\Users\14135\Documents\Codex\2026-06-17\new-chat\outputs"

def load(path):
    rows=[]
    with open(path) as f:
        for r in csv.DictReader(f):
            for k in ["open","high","low","close","volume"]: r[k]=float(r[k])
            tc="timestamp" if "timestamp" in r else "ts"
            ts=int(r[tc]); rows.append({**r,"ts":ts if ts<99999999999 else ts//1000})
    return rows

def ema(d,p):
    k=2.0/(p+1);o=[d[0]]
    for x in d[1:]: o.append(x*k+o[-1]*(1-k))
    return o

def precompute(candles):
    rows=[]
    cl=[c["close"] for c in candles]
    hi=[c["high"] for c in candles]
    lo=[c["low"] for c in candles]
    vo=[c["volume"] for c in candles]
    n=len(cl); e200=ema(cl,200)
    for idx in range(200,n-1):
        dt=datetime.fromtimestamp(candles[idx]["ts"],tz=timezone.utc)
        g=sum(max(cl[i]-cl[i-1],0) for i in range(idx-6,idx+1))
        ls=sum(max(cl[i-1]-cl[i],0) for i in range(idx-6,idx+1))
        r7=100*g/(g+ls) if (g+ls)>0 else 50
        h14=max(hi[idx-13:idx+1]);l14=min(lo[idx-13:idx+1])
        s14=100*(cl[idx]-l14)/(h14-l14) if h14!=l14 else 50
        v20=sum(vo[idx-19:idx+1])/20;v50=sum(vo[idx-49:idx+1])/50
        vr=v20/v50 if v50>0 else 1.0
        h20=max(hi[idx-19:idx+1]);l20=min(lo[idx-19:idx+1])
        p20=(cl[idx]-l20)/(h20-l20) if h20!=l20 else 0.5
        d200=(cl[idx]-e200[idx])/e200[idx]*100 if e200[idx]>0 else 0
        rows.append({"idx":idx,"ts":candles[idx]["ts"],"hr":dt.hour,"r7":r7,"s14":s14,"vr":vr,"p20":p20,"d200":d200,"c":cl[idx],"nc":cl[idx+1]})
    return rows

print("Loading 30min...")
eth=precompute(load(BASE+"/binance_ETHUSDT_30m.csv"))
btc=precompute(load(BASE+"/binance_BTCUSDT_30m.csv"))
all_rows=eth+btc
for i,r in enumerate(all_rows): r["sym"]="ETH" if i<len(eth) else "BTC"
all_rows.sort(key=lambda r:r["ts"])
days=(all_rows[-1]["ts"]-all_rows[0]["ts"])/86400
print(f"{len(all_rows)} rows, {days:.0f} days")

def sigs(rows, ep, bp, ms, cd):
    tr=[]; li=-999
    for r in rows:
        if r["idx"]-li<cd: continue
        p=ep if r["sym"]=="ETH" else bp
        sc=0
        if r["p20"]<p["p20"]: sc+=1
        if r["r7"]<p["r7"]: sc+=1
        if r["vr"]>p["vr"]: sc+=1
        if r["s14"]<p["s14"]: sc+=1
        if sc<ms: continue
        w=r["nc"]>r["c"]; li=r["idx"]
        mult=2 if sc>=3 else 1
        tr.append({"w":w,"pnl":(4 if w else -5)*mult})
    return tr

# Test multiple configs
configs=[
    ("v12g orig", {"p20":0.10,"r7":18,"vr":2.0,"s14":15}, {"p20":0.15,"r7":12,"vr":1.0,"s14":8}, 2.5, 0),
    ("v12g ms=2.0", {"p20":0.10,"r7":18,"vr":2.0,"s14":15}, {"p20":0.15,"r7":12,"vr":1.0,"s14":8}, 2.0, 0),
    ("tight", {"p20":0.08,"r7":15,"vr":2.5,"s14":10}, {"p20":0.10,"r7":8,"vr":1.5,"s14":5}, 2.0, 0),
    ("medium", {"p20":0.15,"r7":22,"vr":1.5,"s14":18}, {"p20":0.20,"r7":15,"vr":0.8,"s14":10}, 2.0, 0),
    ("ultra-wide", {"p20":0.30,"r7":30,"vr":0.8,"s14":30}, {"p20":0.35,"r7":22,"vr":0.4,"s14":20}, 2.0, 0),
]

# Also 1H v19 for comparison
print(f"\n{'Config':<20} {'Trades':>6} {'/day':>7} {'WR':>7} {'PnL':>8} {'DD':>8} {'/mo':>8}")
print("-"*72)
for label,ep,bp,ms,cd in configs:
    tr=sigs(all_rows,ep,bp,ms,cd)
    T=len(tr);W=sum(1 for t in tr if t["w"])
    WR=W/T*100 if T else 0
    pnl=sum(t["pnl"] for t in tr)
    dd=0;peak=0;run=0
    for t in tr: run+=t["pnl"];peak=max(peak,run);dd=max(dd,peak-run)
    print(f"{label:<20} {T:>6} {T/days:6.1f} {WR:6.1f}% {pnl:+8d}u {-dd:>8d}u {pnl/(days/30):+8.0f}u")

print(f"\n--- COMPARISON ---")
print(f"1H v19:  4.2/day WR=56.6% +37u/mo")
print(f"15m tight: 8.2/day WR=57.8% +57u/mo (10m proxy)")
