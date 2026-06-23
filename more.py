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

print("Precomputing...")
all_rows=[]
files=[("ETH","binance_ETHUSDT_1h_2023.csv"),("BTC","binance_BTCUSDT_1h_2023.csv"),
       ("ETH","binance_ETHUSDT_1h_2024.csv"),("BTC","binance_BTCUSDT_1h_2024.csv"),
       ("ETH","binance_ETHUSDT_1h_kline.csv"),("BTC","binance_BTCUSDT_1h_kline.csv")]
try:
    load(BASE+"/binance_SOLUSDT_1h_kline.csv")
    files.append(("SOL","binance_SOLUSDT_1h_kline.csv"))
    has_sol=True
except: has_sol=False

for sym,fname in files:
    candles=load(BASE+"/"+fname)
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
        all_rows.append({"idx":idx,"ts":candles[idx]["ts"],"hr":dt.hour,"sym":sym,
            "r7":r7,"s14":s14,"vr":vr,"p20":p20,"d200":d200,"c":cl[idx],"nc":cl[idx+1]})
all_rows.sort(key=lambda r:r["ts"])
print(f"Precomputed {len(all_rows)} rows, SOL={has_sol}")

def fast_sigs(rows, params, ms, cd_bars, bad_hrs, regime):
    bh=bad_hrs if bad_hrs else set()
    tr=[]; li=-999
    for r in rows:
        if r["hr"] in bh: continue
        if r["sym"] not in params: continue
        if r["idx"]-li<cd_bars: continue
        if r["d200"]>regime: continue
        p=params[r["sym"]]
        sc=0
        if r["p20"]<p["p20"]: sc+=1
        if r["r7"]<p["r7"]: sc+=1
        if r["vr"]>p["vr"]: sc+=1
        if r["s14"]<p["s14"]: sc+=1
        if sc<ms: continue
        w=r["nc"]>r["c"]; li=r["idx"]
        mult=2 if sc>=3 else 1
        tr.append({"w":w,"pnl":(4 if w else -5)*mult,"sym":r["sym"]})
    return tr

BH={5,12,14,22}
eth_n={"p20":0.10,"r7":18,"vr":2.0,"s14":15}
btc_n={"p20":0.15,"r7":12,"vr":1.0,"s14":8}
eth_w={"p20":0.20,"r7":25,"vr":1.2,"s14":22}
btc_w={"p20":0.25,"r7":18,"vr":0.6,"s14":15}
sol_p={"p20":0.12,"r7":18,"vr":2.0,"s14":15}

configs=[
    ("v16 now                ", {"ETH":eth_n,"BTC":btc_n}, 2.0, 0, BH, 12.0),
    ("+ no bad hours         ", {"ETH":eth_n,"BTC":btc_n}, 2.0, 0, set(), 12.0),
    ("wide thresholds        ", {"ETH":eth_w,"BTC":btc_w}, 2.0, 0, BH, 12.0),
    ("wide + no bad hours    ", {"ETH":eth_w,"BTC":btc_w}, 2.0, 0, set(), 12.0),
]
if has_sol:
    configs.append(("+SOL wide + noBH     ", {"ETH":eth_w,"BTC":btc_w,"SOL":sol_p}, 2.0, 0, set(), 12.0))

for label,params,ms,cd,bh,reg in configs:
    tr=fast_sigs(all_rows,params,ms,cd,bh,reg)
    T=len(tr);W=sum(1 for t in tr if t["w"])
    WR=W/T*100 if T else 0
    pnl=sum(t["pnl"] for t in tr)
    dd=0;peak=0;run=0
    for t in tr: run+=t["pnl"];peak=max(peak,run);dd=max(dd,peak-run)
    bt=sum(1 for t in tr if t["sym"]=="BTC")
    et=sum(1 for t in tr if t["sym"]=="ETH")
    st=sum(1 for t in tr if t["sym"]=="SOL")
    print(f"{label}: {T}t {T/1260:.2f}/d WR={WR:.1f}% PnL={pnl:+d}u DD={-dd}u /mo={pnl/41:+.0f}u  BTC:{bt} ETH:{et}"+ (f" SOL:{st}" if has_sol else ""))
