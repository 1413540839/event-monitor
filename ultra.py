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
for sym,fname in [("ETH","binance_ETHUSDT_1h_2023.csv"),("BTC","binance_BTCUSDT_1h_2023.csv"),
                   ("ETH","binance_ETHUSDT_1h_2024.csv"),("BTC","binance_BTCUSDT_1h_2024.csv"),
                   ("ETH","binance_ETHUSDT_1h_kline.csv"),("BTC","binance_BTCUSDT_1h_kline.csv")]:
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
        s3=100*(cl[idx]-min(lo[idx-2:idx+1]))/(max(hi[idx-2:idx+1])-min(lo[idx-2:idx+1])) if max(hi[idx-2:idx+1])!=min(lo[idx-2:idx+1]) else 50
        v20=sum(vo[idx-19:idx+1])/20;v50=sum(vo[idx-49:idx+1])/50
        vr=v20/v50 if v50>0 else 1.0
        h20=max(hi[idx-19:idx+1]);l20=min(lo[idx-19:idx+1])
        p20=(cl[idx]-l20)/(h20-l20) if h20!=l20 else 0.5
        d200=(cl[idx]-e200[idx])/e200[idx]*100 if e200[idx]>0 else 0
        all_rows.append({"idx":idx,"ts":candles[idx]["ts"],"hr":dt.hour,"sym":sym,
            "r7":r7,"s14":s14,"s3":s3,"vr":vr,"p20":p20,"d200":d200,"c":cl[idx],"nc":cl[idx+1]})
all_rows.sort(key=lambda r:r["ts"])
print(f"Precomputed {len(all_rows)} rows")

def fast_sigs(rows, params, ms, cd_bars, bad_hrs, regime, stoch3_max=None):
    bh=bad_hrs if bad_hrs else set()
    tr=[]; li=-999
    for r in rows:
        if r["hr"] in bh: continue
        if r["sym"] not in params: continue
        if r["idx"]-li<cd_bars: continue
        if regime and r["d200"]>regime: continue
        if stoch3_max and r["s3"]>=stoch3_max: continue
        p=params[r["sym"]]
        sc=0
        if r["p20"]<p["p20"]: sc+=1
        if r["r7"]<p["r7"]: sc+=1
        if r["vr"]>p["vr"]: sc+=1
        if r["s14"]<p["s14"]: sc+=1
        if sc<ms: continue
        w=r["nc"]>r["c"]; li=r["idx"]
        mult=2 if sc>=3 else 1
        tr.append({"w":w,"pnl":(4 if w else -5)*mult,"sym":r["sym"],"sc":sc})
    return tr

eth_w={"p20":0.20,"r7":25,"vr":1.2,"s14":22}
btc_w={"p20":0.25,"r7":18,"vr":0.6,"s14":15}

# Ultra wide
eth_u={"p20":0.30,"r7":30,"vr":0.8,"s14":30}
btc_u={"p20":0.35,"r7":22,"vr":0.4,"s14":20}

configs=[
    ("v17 now                     ", {"ETH":eth_w,"BTC":btc_w}, 2.0, 0, set(), 12.0, 15),
    ("- regime filter             ", {"ETH":eth_w,"BTC":btc_w}, 2.0, 0, set(), None, 15),
    ("- stoch3 filter             ", {"ETH":eth_w,"BTC":btc_w}, 2.0, 0, set(), 12.0, None),
    ("- regime - stoch3           ", {"ETH":eth_w,"BTC":btc_w}, 2.0, 0, set(), None, None),
    ("ultra wide params           ", {"ETH":eth_u,"BTC":btc_u}, 2.0, 0, set(), 12.0, 15),
    ("ultra wide - all filters    ", {"ETH":eth_u,"BTC":btc_u}, 2.0, 0, set(), None, None),
    ("ultra wide ms=1.0           ", {"ETH":eth_u,"BTC":btc_u}, 1.0, 0, set(), None, None),
]

for label,params,ms,cd,bh,reg,s3 in configs:
    tr=fast_sigs(all_rows,params,ms,cd,bh,reg,s3)
    T=len(tr);W=sum(1 for t in tr if t["w"])
    WR=W/T*100 if T else 0
    pnl=sum(t["pnl"] for t in tr)
    dd=0;peak=0;run=0
    for t in tr: run+=t["pnl"];peak=max(peak,run);dd=max(dd,peak-run)
    per_day=T/1260
    exp_per_trade=pnl/T if T else 0
    daily_exp=per_day*exp_per_trade
    score4=sum(1 for t in tr if t["sc"]>=4)
    score3=sum(1 for t in tr if t["sc"]==3)
    score2=sum(1 for t in tr if t["sc"]==2)
    print(f"{label}: {T}t {per_day:.2f}/d WR={WR:.1f}% PnL={pnl:+d}u DD={-dd}u exp={daily_exp:+.1f}u/d sc4:{score4} sc3:{score3} sc2:{score2}")
