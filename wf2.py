# -*- coding: utf-8 -*-
"""Walkforward v2: precompute indicators, then grid-search blazing fast"""
import csv, random, math
from collections import defaultdict
from datetime import datetime, timezone

BASE = r"C:\Users\14135\Documents\Codex\2026-06-17\new-chat\outputs"
BH = {5,12,14,22}

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

# Precompute ALL indicators for a candle list
def precompute(cd):
    rows=[]
    cl=[c["close"] for c in cd]
    hi=[c["high"] for c in cd]
    lo=[c["low"] for c in cd]
    vo=[c["volume"] for c in cd]
    n=len(cl)
    e200=ema(cl,200)
    for idx in range(200,n):
        dt=datetime.fromtimestamp(cd[idx]["ts"],tz=timezone.utc)
        g=sum(max(cl[i]-cl[i-1],0) for i in range(idx-6,idx+1))
        ls=sum(max(cl[i-1]-cl[i],0) for i in range(idx-6,idx+1))
        r7=100*g/(g+ls) if (g+ls)>0 else 50
        h14=max(hi[idx-13:idx+1]); l14=min(lo[idx-13:idx+1])
        s14=100*(cl[idx]-l14)/(h14-l14) if h14!=l14 else 50
        s3=100*(cl[idx]-min(lo[idx-2:idx+1]))/(max(hi[idx-2:idx+1])-min(lo[idx-2:idx+1])) if max(hi[idx-2:idx+1])!=min(lo[idx-2:idx+1]) else 50
        v20=sum(vo[idx-19:idx+1])/20; v50=sum(vo[idx-49:idx+1])/50
        vr=v20/v50 if v50>0 else 1.0
        h20=max(hi[idx-19:idx+1]); l20=min(lo[idx-19:idx+1])
        p20=(cl[idx]-l20)/(h20-l20) if h20!=l20 else 0.5
        d200=(cl[idx]-e200[idx])/e200[idx]*100 if e200[idx]>0 else 0
        rows.append({
            "idx":idx,"ts":cd[idx]["ts"],"hr":dt.hour,"r7":r7,"s14":s14,"s3":s3,
            "vr":vr,"p20":p20,"d200":d200,"c":cl[idx],"nc":cl[idx+1] if idx+1<n else None
        })
    return rows

# Fast signal extraction from precomputed rows
def fast_sigs(rows, p):
    tr=[]; li=-999
    for r in rows:
        if r["hr"] in BH: continue
        if r["idx"]-li<2: continue
        if r["d200"]>12.0: continue
        sc=0
        if r["p20"]<p["p20"]: sc+=1
        if r["r7"]<p["r7"]: sc+=1
        if r["vr"]>p["vr"]: sc+=1
        if r["s14"]<p["s14"]: sc+=1
        if sc<p["ms"]: continue
        if r["nc"] is None: continue
        w=r["nc"]>r["c"]; li=r["idx"]
        tr.append({"w":w})
    return tr

def eval_p(rows, p):
    t=fast_sigs(rows,p)
    if len(t)<5: return 0,0.0,0
    w=sum(1 for x in t if x["w"])
    return len(t),w/len(t)*100,sum(4 if x["w"] else -5 for x in t)

# Load all data
allc=[]
pairs=[("binance_ETHUSDT_1h_2023.csv","ETH"),("binance_BTCUSDT_1h_2023.csv","BTC"),
       ("binance_ETHUSDT_1h_2024.csv","ETH"),("binance_BTCUSDT_1h_2024.csv","BTC"),
       ("binance_ETHUSDT_1h_kline.csv","ETH"),("binance_BTCUSDT_1h_kline.csv","BTC")]
for path,sym in pairs:
    cd=load(BASE+"\\"+path)
    for c in cd: c["sym"]=sym
    allc.extend(cd)
allc.sort(key=lambda c:c["ts"])

bysym=defaultdict(list)
for c in allc: bysym[c["sym"]].append(c)

print("Precomputing indicators...")
pre_eth=precompute(bysym["ETH"])
pre_btc=precompute(bysym["BTC"])
print(f"ETH: {len(pre_eth)} rows, BTC: {len(pre_btc)} rows")

# Monthly boundaries
bnd=[]
d=datetime.fromtimestamp(min(c["ts"] for c in allc),tz=timezone.utc).replace(day=1,hour=0)
ed=datetime.fromtimestamp(max(c["ts"] for c in allc),tz=timezone.utc)
while d<ed:
    bnd.append(d)
    d=d.replace(month=d.month+1) if d.month<12 else d.replace(year=d.year+1,month=1)
print(f"Months: {len(bnd)} ({bnd[0].date()} to {bnd[-1].date()})")

def win_rows(rows,st,en):
    return [r for r in rows if st<=r["ts"]<en]

# Grids
grid_eth=[(0.08,18,2.0,15,2.5),(0.10,18,2.0,15,2.5),(0.12,18,2.0,15,2.5),
          (0.10,16,2.0,15,2.5),(0.10,20,2.0,15,2.5),(0.10,18,1.5,15,2.5),
          (0.10,18,2.5,15,2.5),(0.10,18,2.0,12,2.5),(0.10,18,2.0,18,2.5),
          (0.10,18,2.0,15,2.0),(0.10,18,2.0,15,3.0)]
grid_btc=[(0.12,12,1.0,8,2.5),(0.15,12,1.0,8,2.5),(0.18,12,1.0,8,2.5),
          (0.15,10,1.0,8,2.5),(0.15,14,1.0,8,2.5),(0.15,12,0.8,8,2.5),
          (0.15,12,1.5,8,2.5),(0.15,12,1.0,6,2.5),(0.15,12,1.0,10,2.5),
          (0.15,12,1.0,8,2.0),(0.15,12,1.0,8,3.0)]

v12_eth={"p20":0.10,"r7":18,"vr":2.0,"s14":15,"ms":2.5}
v12_btc={"p20":0.15,"r7":12,"vr":1.0,"s14":8,"ms":2.5}

print("\n"+80*"=")
print("WALKFORWARD (3mo train -> 1mo test)")
print(80*"=")

res=[]
train_m=3
for i in range(train_m,len(bnd)-1):
    ts=bnd[i-train_m]; te=bnd[i]; ss=bnd[i]; se=bnd[i+1]
    
    tr_eth=win_rows(pre_eth,int(ts.timestamp()),int(te.timestamp()))
    tr_btc=win_rows(pre_btc,int(ts.timestamp()),int(te.timestamp()))
    ts_eth=win_rows(pre_eth,int(ss.timestamp()),int(se.timestamp()))
    ts_btc=win_rows(pre_btc,int(ss.timestamp()),int(se.timestamp()))
    
    if len(tr_eth)+len(tr_btc)<500 or len(ts_eth)+len(ts_btc)<100: continue
    
    # Grid search on train
    best=(-9999,None,None)
    for pe in grid_eth:
        ep={"p20":pe[0],"r7":pe[1],"vr":pe[2],"s14":pe[3],"ms":pe[4]}
        _,_,epnl=eval_p(tr_eth,ep)
        for pb in grid_btc:
            bp={"p20":pb[0],"r7":pb[1],"vr":pb[2],"s14":pb[3],"ms":pb[4]}
            _,_,bpnl=eval_p(tr_btc,bp)
            tp=epnl+bpnl
            if tp>best[0]: best=(tp,ep,bp)
    
    bep,bbp=best[1],best[2]
    if bep is None: continue
    
    et_t,et_wr,et_pnl=eval_p(ts_eth,bep)
    bt_t,bt_wr,bt_pnl=eval_p(ts_btc,bbp)
    
    ev_t,ev_wr,ev_pnl=eval_p(ts_eth,v12_eth)
    bv_t,bv_wr,bv_pnl=eval_p(ts_btc,v12_btc)
    
    res.append({"w":ss.date(),"ep":et_pnl,"bp":bt_pnl,"v12":ev_pnl+bv_pnl,"pe":bep,"pb":bbp})
    print(f"  {ss.date()} opt={et_pnl+bt_pnl:+d} v12={ev_pnl+bv_pnl:+d}")

if res:
    opt_pnl=sum(r["ep"]+r["bp"] for r in res)
    v12_pnl=sum(r["v12"] for r in res)
    print(f"\n{len(res)} windows")
    print(f"Optimized PnL: {opt_pnl:+d}u")
    print(f"v12g static PnL: {v12_pnl:+d}u")
    print(f"Walkforward edge: {opt_pnl-v12_pnl:+d}u")
    
    print(f"\nPARAMETER STABILITY")
    for nm in ["p20","r7","vr","s14","ms"]:
        ev=[r["pe"][nm] for r in res]; bv=[r["pb"][nm] for r in res]
        em=sum(ev)/len(ev); es=math.sqrt(sum((v-em)**2 for v in ev)/(len(ev)-1)) if len(ev)>1 else 0
        bm=sum(bv)/len(bv); bs=math.sqrt(sum((v-bm)**2 for v in bv)/(len(bv)-1)) if len(bv)>1 else 0
        ecv=es/em*100 if em else 0; bcv=bs/bm*100 if bm else 0
        print(f"  {nm}: ETH {em:.3f}+-{es:.3f}(CV={ecv:.0f}%)  BTC {bm:.3f}+-{bs:.3f}(CV={bcv:.0f}%)")

# PERMUTATION
print(f"\nPERMUTATION TEST")
all_out=[]
for path,sym in pairs:
    cd=load(BASE+"\\"+path)
    p=v12_btc if sym=="BTC" else v12_eth
    rows=[r for r in (pre_eth if sym=="ETH" else pre_btc)]
    # Use precomputed rows filtered to this symbol
    pass

# Do it from precomputed
all_out=[]
for r in pre_eth:
    p=v12_eth
    sc=0
    if r["p20"]<p["p20"]: sc+=1
    if r["r7"]<p["r7"]: sc+=1
    if r["vr"]>p["vr"]: sc+=1
    if r["s14"]<p["s14"]: sc+=1
    if sc>=p["ms"] and r["nc"] and r["hr"] not in BH and r["d200"]<=12.0:
        all_out.append(r["nc"]>r["c"])
for r in pre_btc:
    p=v12_btc
    sc=0
    if r["p20"]<p["p20"]: sc+=1
    if r["r7"]<p["r7"]: sc+=1
    if r["vr"]>p["vr"]: sc+=1
    if r["s14"]<p["s14"]: sc+=1
    if sc>=p["ms"] and r["nc"] and r["hr"] not in BH and r["d200"]<=12.0:
        all_out.append(r["nc"]>r["c"])

act_wr=sum(all_out)/len(all_out)*100
perm_wrs=[]
for _ in range(1000):
    sh=random.sample(all_out,len(all_out))
    perm_wrs.append(sum(sh)/len(sh)*100)
perm_wrs.sort()
pv=sum(1 for w in perm_wrs if w>=act_wr)/1000
print(f"n={len(all_out)} WR={act_wr:.1f}%  p={pv:.4f} {'***SIG***' if pv<0.05 else 'not sig'}")

# AIC comparison
def aic(trades,k):
    w=sum(1 for t in trades if t["w"])
    n=len(trades)
    if n<5 or w in(0,n): return 1e9
    wr=w/n
    ll=w*math.log(wr)+(n-w)*math.log(1-wr)
    return -2*ll+2*k

v12_all=[];v13_all=[]
for r in pre_eth:
    p=v12_eth
    sc=sum([r["p20"]<p["p20"],r["r7"]<p["r7"],r["vr"]>p["vr"],r["s14"]<p["s14"]])
    if sc>=p["ms"] and r["nc"] and r["hr"] not in BH and r["d200"]<=12.0:
        v12_all.append({"w":r["nc"]>r["c"]})
        if r["s3"]<15: v13_all.append({"w":r["nc"]>r["c"]})
for r in pre_btc:
    p=v12_btc
    sc=sum([r["p20"]<p["p20"],r["r7"]<p["r7"],r["vr"]>p["vr"],r["s14"]<p["s14"]])
    if sc>=p["ms"] and r["nc"] and r["hr"] not in BH and r["d200"]<=12.0:
        v12_all.append({"w":r["nc"]>r["c"]})
        if r["s3"]<15: v13_all.append({"w":r["nc"]>r["c"]})

a12=aic(v12_all,4); a13=aic(v13_all,5)
print(f"\nAIC: v12g={a12:.0f}(4p) v13={a13:.0f}(5p) delta={a13-a12:+.0f} -> {'v13 BETTER' if a13<a12 else 'v12g BETTER'}")

print("\nDONE")
