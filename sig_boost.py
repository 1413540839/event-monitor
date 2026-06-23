# Fast signal boost test with precomputed indicators
import csv, math
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

def precompute(cd):
    rows=[]
    cl=[c["close"] for c in cd]
    hi=[c["high"] for c in cd]
    lo=[c["low"] for c in cd]
    vo=[c["volume"] for c in cd]
    n=len(cl); e200=ema(cl,200)
    for idx in range(200,n-1):
        dt=datetime.fromtimestamp(cd[idx]["ts"],tz=timezone.utc)
        g=sum(max(cl[i]-cl[i-1],0) for i in range(idx-6,idx+1))
        ls=sum(max(cl[i-1]-cl[i],0) for i in range(idx-6,idx+1))
        r7=100*g/(g+ls) if (g+ls)>0 else 50
        h14=max(hi[idx-13:idx+1]); l14=min(lo[idx-13:idx+1])
        s14=100*(cl[idx]-l14)/(h14-l14) if h14!=l14 else 50
        v20=sum(vo[idx-19:idx+1])/20; v50=sum(vo[idx-49:idx+1])/50
        vr=v20/v50 if v50>0 else 1.0
        h20=max(hi[idx-19:idx+1]); l20=min(lo[idx-19:idx+1])
        p20=(cl[idx]-l20)/(h20-l20) if h20!=l20 else 0.5
        d200=(cl[idx]-e200[idx])/e200[idx]*100 if e200[idx]>0 else 0
        rows.append({"idx":idx,"ts":cd[idx]["ts"],"hr":dt.hour,"r7":r7,"s14":s14,
                     "vr":vr,"p20":p20,"d200":d200,"c":cl[idx],"nc":cl[idx+1]})
    return rows

def fast_bt(rows, p20t, r7t, vrt, s14t, ms, cd_bars=2, bad_hrs=None, regime=12.0):
    if bad_hrs is None: bad_hrs={5,12,14,22}
    tr=[]; li=-999
    for r in rows:
        if r["hr"] in bad_hrs: continue
        if r["idx"]-li<cd_bars: continue
        if r["d200"]>regime: continue
        sc=0
        if r["p20"]<p20t: sc+=1
        if r["r7"]<r7t: sc+=1
        if r["vr"]>vrt: sc+=1
        if r["s14"]<s14t: sc+=1
        if sc<ms: continue
        w=r["nc"]>r["c"]; li=r["idx"]
        tr.append({"w":w})
    return tr

def stats(tr,label,bl_t,bl_wr):
    w=sum(1 for t in tr if t["w"])
    t=len(tr); wr=w/t*100 if t else 0
    pnl=sum(4 if x["w"] else -5 for x in tr)
    spd=t/1260; days=1260/t if t else 999
    return f"{label}: {t:4d}t (+{t-bl_t}) WR={wr:5.1f}% ({wr-bl_wr:+.1f}) PnL={pnl:+5d}u {spd:.2f}/d ~{days:.1f}d"

print("Precomputing...")
pre={}
for fname,sym in [("binance_ETHUSDT_1h_2023.csv","ETH"),("binance_BTCUSDT_1h_2023.csv","BTC"),
                  ("binance_ETHUSDT_1h_2024.csv","ETH"),("binance_BTCUSDT_1h_2024.csv","BTC"),
                  ("binance_ETHUSDT_1h_kline.csv","ETH"),("binance_BTCUSDT_1h_kline.csv","BTC")]:
    cd=load(BASE+"/"+fname)
    rows=precompute(cd)
    if sym not in pre: pre[sym]=[]
    pre[sym].extend(rows)

for s in pre: pre[s].sort(key=lambda r:r["ts"])
print(f"ETH: {len(pre['ETH'])} rows, BTC: {len(pre['BTC'])} rows")

eth_all=pre["ETH"]; btc_all=pre["BTC"]
all_rows=eth_all+btc_all
days_span=1260

eth_bl=fast_bt(eth_all,0.10,18,2.0,15,2.5)
btc_bl=fast_bt(btc_all,0.15,12,1.0,8,2.5)
bl=eth_bl+btc_bl
bl_t=len(bl); bl_w=sum(1 for t in bl if t["w"])
bl_wr=bl_w/bl_t*100; bl_pnl=sum(4 if t["w"] else -5 for t in bl)
print(f"\nBASELINE: {bl_t}t WR={bl_wr:.1f}% PnL={bl_pnl:+d}u {bl_t/days_span:.2f}/d")

# Test all quickly
tests=[]

# min_score
for ms in [2.5,2.0,1.5]:
    e=fast_bt(eth_all,0.10,18,2.0,15,ms); b=fast_bt(btc_all,0.15,12,1.0,8,ms)
    tests.append((f"ms={ms}",e+b))

# cooldown  
for cd in [2,1,0]:
    e=fast_bt(eth_all,0.10,18,2.0,15,2.5,cd_bars=cd)
    b=fast_bt(btc_all,0.15,12,1.0,8,2.5,cd_bars=cd)
    tests.append((f"cd={cd}bar",e+b))

# No bad hours
e=fast_bt(eth_all,0.10,18,2.0,15,2.5,bad_hrs=set())
b=fast_bt(btc_all,0.15,12,1.0,8,2.5,bad_hrs=set())
tests.append(("no bad hrs",e+b))

# Widen all
e=fast_bt(eth_all,0.15,22,1.5,20,2.0); b=fast_bt(btc_all,0.20,16,0.8,12,2.0)
tests.append(("wide",e+b))

# Combo: ms=2.0 + cd=1
e=fast_bt(eth_all,0.10,18,2.0,15,2.0,cd_bars=1)
b=fast_bt(btc_all,0.15,12,1.0,8,2.0,cd_bars=1)
tests.append(("ms2+cd1",e+b))

# Combo: ms=2.0 only (keep cd=2)
e=fast_bt(eth_all,0.10,18,2.0,15,2.0); b=fast_bt(btc_all,0.15,12,1.0,8,2.0)
tests.append(("ms2 only",e+b))

# Combo: ms=1.5 + cd=1
e=fast_bt(eth_all,0.10,18,2.0,15,1.5,cd_bars=1)
b=fast_bt(btc_all,0.15,12,1.0,8,1.5,cd_bars=1)
tests.append(("ms1.5+cd1",e+b))

# Best: ms=2.0 + cd=1 + no bad hours
e=fast_bt(eth_all,0.10,18,2.0,15,2.0,cd_bars=1,bad_hrs=set())
b=fast_bt(btc_all,0.15,12,1.0,8,2.0,cd_bars=1,bad_hrs=set())
tests.append(("ms2+cd1+nobh",e+b))

print(f"\n{'Test':<18} {'Trades':>6} {'WR':>7} {'PnL':>7} {'/day':>6} {'Verdict'}")
print("-"*60)
for label, tr in tests:
    w=sum(1 for t in tr if t["w"]); t=len(tr)
    wr=w/t*100 if t else 0; pnl=sum(4 if x["w"] else -5 for x in tr)
    spd=t/days_span
    # Verdict: keep if WR > 58% AND signal boost > 1.5x
    boost=t/bl_t
    if wr>60: v="BEST"
    elif wr>58: v="GOOD"
    elif wr>56: v="OK"
    else: v="DROP"
    if boost>2: v+=" 2x+"
    print(f"{label:<18} {t:>6} {wr:6.1f}% {pnl:+7d}u {spd:5.2f} {v}")

print(f"\nBASELINE for comparison: {bl_t}t WR={bl_wr:.1f}% PnL={bl_pnl:+d}u")
print(f"Target: >58% WR, >1.5x signals")
