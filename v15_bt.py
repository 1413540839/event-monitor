import csv
from collections import defaultdict
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

eth_p={"p20":0.10,"r7":18,"vr":2.0,"s14":15,"ms":2.0}
btc_p={"p20":0.15,"r7":12,"vr":1.0,"s14":8,"ms":2.0}

data_files=[(BASE+"/binance_ETHUSDT_1h_2023.csv","ETH"),(BASE+"/binance_BTCUSDT_1h_2023.csv","BTC"),
            (BASE+"/binance_ETHUSDT_1h_2024.csv","ETH"),(BASE+"/binance_BTCUSDT_1h_2024.csv","BTC"),
            (BASE+"/binance_ETHUSDT_1h_kline.csv","ETH"),(BASE+"/binance_BTCUSDT_1h_kline.csv","BTC")]

all_t=[]
for fpath,sym in data_files:
    cd=load(fpath); p=eth_p if sym=="ETH" else btc_p
    last_idx=-999
    for idx in range(200,len(cd)-1):
        dt=datetime.fromtimestamp(cd[idx]["ts"],tz=timezone.utc)
        if dt.hour in {5,12,14,22}: continue
        if idx-last_idx<1: continue
        
        cl2=[c["close"] for c in cd[:idx+1]]
        hi2=[c["high"] for c in cd[:idx+1]]
        lo2=[c["low"] for c in cd[:idx+1]]
        vo2=[c["volume"] for c in cd[:idx+1]]
        
        g=sum(max(cl2[i]-cl2[i-1],0) for i in range(idx-6,idx+1))
        ls=sum(max(cl2[i-1]-cl2[i],0) for i in range(idx-6,idx+1))
        r7=100*g/(g+ls) if (g+ls)>0 else 50
        h14=max(hi2[-14:]);l14=min(lo2[-14:])
        s14=100*(cl2[-1]-l14)/(h14-l14) if h14!=l14 else 50
        v20=sum(vo2[-20:])/20;v50=sum(vo2[-50:])/50
        vr=v20/v50 if v50>0 else 1.0
        h20=max(hi2[-20:]);l20=min(lo2[-20:])
        p20=(cl2[-1]-l20)/(h20-l20) if h20!=l20 else 0.5
        e200=ema(cl2,200)[-1]
        d200=(cl2[-1]-e200)/e200*100 if e200>0 else 0
        
        sc=0
        if p20<p["p20"]: sc+=1
        if r7<p["r7"]: sc+=1
        if vr>p["vr"]: sc+=1
        if s14<p["s14"]: sc+=1
        if sc<p["ms"]: continue
        if d200>12.0: continue
        
        win=cd[idx+1]["close"]>cl2[-1]
        mult=2 if sc>=3 else 1
        pnl=(4 if win else -5)*mult
        last_idx=idx
        all_t.append({"time":dt,"coin":sym,"w":win,"pnl":pnl,"sc":sc})

all_t.sort(key=lambda t:t["time"])
T=len(all_t);W=sum(1 for t in all_t if t["w"]);WR=W/T*100
pnl=sum(t["pnl"] for t in all_t)
days=(all_t[-1]["time"]-all_t[0]["time"]).days
months=days/30.44

peak=0;running=0;maxdd=0
for t in all_t: running+=t["pnl"];peak=max(peak,running);maxdd=max(maxdd,peak-running)

monthly=defaultdict(lambda:{"pnl":0,"t":0})
for t in all_t:
    m=t["time"].strftime("%Y-%m");monthly[m]["t"]+=1;monthly[m]["pnl"]+=t["pnl"]
neg=sum(1 for m in monthly if monthly[m]["pnl"]<0)

for sc in [2,3,4]:
    st=[t for t in all_t if t["sc"]>=sc]
    if st:
        sw=sum(1 for t in st if t["w"])
        sp=sum(t["pnl"] for t in st)
        print(f"Score>={sc}: {len(st)}t WR={sw/len(st)*100:.1f}% PnL={sp:+d}u")

print(f"\n=== v15 FINAL (ms=2.0 cd=1bar 2x@sc>=3) ===")
print(f"Period: {all_t[0]['time'].date()} ~ {all_t[-1]['time'].date()} ({days}d)")
print(f"Trades: {T} | Wins: {W} | WR: {WR:.1f}%")
print(f"PnL: {pnl:+d}u | MaxDD: -{maxdd}u")
print(f"Per day: {T/days:.2f} | Per month: {T/months:.0f}")
print(f"Monthly PnL: {pnl/months:+.0f}u avg | Neg months: {neg}/{len(monthly)}")
print(f"Profit factor: {sum(4*t['w'] for t in all_t)/sum(5 for t in all_t if not t['w']):.2f}")
print(f"Boost vs v12g: {T/686:.1f}x signals, PnL {pnl/476:.1f}x")
