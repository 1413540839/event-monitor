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
        v20=sum(vo[idx-19:idx+1])/20;v50=sum(vo[idx-49:idx+1])/50
        vr=v20/v50 if v50>0 else 1.0
        h20=max(hi[idx-19:idx+1]);l20=min(lo[idx-19:idx+1])
        p20=(cl[idx]-l20)/(h20-l20) if h20!=l20 else 0.5
        d200=(cl[idx]-e200[idx])/e200[idx]*100 if e200[idx]>0 else 0
        all_rows.append({"idx":idx,"ts":candles[idx]["ts"],"hr":dt.hour,"sym":sym,
            "r7":r7,"s14":s14,"vr":vr,"p20":p20,"d200":d200,"c":cl[idx],"nc":cl[idx+1]})
all_rows.sort(key=lambda r:r["ts"])
print(f"Precomputed {len(all_rows)} rows")

def fast_sigs(rows, eth_p, btc_p, ms, cd_bars, bad_hrs, regime):
    bh=bad_hrs if bad_hrs else set()
    tr=[]; li=-999
    for r in rows:
        if r["hr"] in bh: continue
        if r["idx"]-li<cd_bars: continue
        if r["d200"]>regime: continue
        p=eth_p if r["sym"]=="ETH" else btc_p
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

eth_n={"p20":0.10,"r7":18,"vr":2.0,"s14":15}
btc_n={"p20":0.15,"r7":12,"vr":1.0,"s14":8}
eth_w={"p20":0.20,"r7":25,"vr":1.2,"s14":22}
btc_w={"p20":0.25,"r7":18,"vr":0.6,"s14":15}
BH={5,12,14,22}

configs=[
    ("v15 now (ms=2.0 cd=1)   ", eth_n,btc_n,2.0,1,BH,12.0),
    ("ms=1.5                   ", eth_n,btc_n,1.5,1,BH,12.0),
    ("ms=1.0 (all signals)     ", eth_n,btc_n,1.0,1,BH,12.0),
    ("cd=0 (no cooldown)       ", eth_n,btc_n,2.0,0,BH,12.0),
    ("no bad hours             ", eth_n,btc_n,2.0,1,set(),12.0),
    ("no regime filter         ", eth_n,btc_n,2.0,1,BH,999),
    ("wide thresholds           ", eth_w,btc_w,2.0,1,BH,12.0),
    ("ms=1.5 + cd=0 + noBH     ", eth_n,btc_n,1.5,0,set(),12.0),
    ("MAX: all off              ", eth_n,btc_n,1.0,0,set(),999),
]

print(f"{'Config':<30} {'Trades':>6} {'/day':>6} {'WR':>7} {'PnL':>8} {'DD':>6} {'/mo':>7}")
print("-"*72)
for label,ep,bp,ms,cd,bh,reg in configs:
    tr=fast_sigs(all_rows,ep,bp,ms,cd,bh,reg)
    T=len(tr);W=sum(1 for t in tr if t["w"])
    WR=W/T*100 if T else 0
    pnl=sum(t["pnl"] for t in tr)
    dd=0;peak=0;run=0
    for t in tr: run+=t["pnl"];peak=max(peak,run);dd=max(dd,peak-run)
    print(f"{label:<30} {T:>6} {T/1260:5.2f} {WR:6.1f}% {pnl:+8d}u {-dd:>6d}u {pnl/41:+7.0f}u")
