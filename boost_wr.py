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

# Test ALL timeframes with min_score sweep to find best monthly return
timeframes = [
    ("15m (10m proxy)", "binance_ETHUSDT_15m_kline.csv", "binance_BTCUSDT_15m_kline.csv"),
    ("30m", "binance_ETHUSDT_30m.csv", "binance_BTCUSDT_30m.csv"),
    ("1H", "binance_ETHUSDT_1h_kline.csv", "binance_BTCUSDT_1h_kline.csv"),
]

def sigs(rows, ep, bp, ms, cd=0):
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
        tr.append({"w":w,"pnl":(4 if w else -5)*mult,"sc":sc})
    return tr

for tf_name, eth_f, btc_f in timeframes:
    print(f"\n=== {tf_name} ===")
    eth=precompute(load(BASE+"/"+eth_f))
    btc=precompute(load(BASE+"/"+btc_f))
    all_rows=eth+btc
    for i,r in enumerate(all_rows): r["sym"]="ETH" if i<len(eth) else "BTC"
    all_rows.sort(key=lambda r:r["ts"])
    days=(all_rows[-1]["ts"]-all_rows[0]["ts"])/86400
    
    # Sweep min_score and param tightness
    configs = [
        ("v12g orig", {"p20":0.10,"r7":18,"vr":2.0,"s14":15}, {"p20":0.15,"r7":12,"vr":1.0,"s14":8}),
        ("tight", {"p20":0.06,"r7":12,"vr":3.0,"s14":8}, {"p20":0.08,"r7":6,"vr":2.0,"s14":5}),
        ("v12g wide", {"p20":0.15,"r7":22,"vr":1.5,"s14":20}, {"p20":0.20,"r7":16,"vr":0.8,"s14":12}),
    ]
    
    best = None
    for label, ep, bp in configs:
        for ms in [3.0, 2.5, 2.0]:
            tr = sigs(all_rows, ep, bp, ms)
            T=len(tr);W=sum(1 for t in tr if t["w"])
            WR=W/T*100 if T else 0
            pnl=sum(t["pnl"] for t in tr)
            mpnl=pnl/(days/30)
            if T >= 50:
                print(f"  {label} ms={ms:.1f}: {T:5d}t {T/days:5.1f}/d WR={WR:5.1f}% PnL={pnl:+6d}u /mo={mpnl:+6.0f}u")
                if best is None or mpnl > best["mpnl"]:
                    best = {"label":label,"ms":ms,"T":T,"per_day":T/days,"WR":WR,"pnl":pnl,"mpnl":mpnl}
    
    if best:
        print(f"  >>> BEST: {best['label']} ms={best['ms']:.1f} {best['per_day']:.1f}/d WR={best['WR']:.1f}% +{best['mpnl']:.0f}u/mo")
