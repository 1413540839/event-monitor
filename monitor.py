# -*- coding: utf-8 -*-
"""BTC/ETH 事件合约 24h监控 v3 — 冷却+批量结算+安静模式"""
import time, requests, os, logging, sys, traceback, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np, pandas as pd, pandas_ta_classic as ta

SYMBOLS = ["BTC-USDT", "ETH-USDT"]
BAR = "5m"; LIMIT = 200; POLL = 30
SENDKEY = os.environ.get("SENDKEY", "")
TRADE_LOG = Path("trade_log.csv")
CONTRACT_CANDLES = 2
COOLDOWN_MINUTES = 30  # 同币种信号冷却时间

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
SEEN = set()
PENDING = {}
LAST_SIGNAL = {}  # coin -> datetime of last signal

def load_trade_log():
    if TRADE_LOG.exists(): return pd.read_csv(TRADE_LOG)
    return pd.DataFrame(columns=["time","coin","direction","rule","entry_price","exit_price","pnl","result","detail"])

def save_trade_log(df):
    df.to_csv(TRADE_LOG, index=False, encoding="utf-8")
    try:
        subprocess.run(["git","add","trade_log.csv"], capture_output=True, timeout=10)
        subprocess.run(["git","commit","-m","update trade log"], capture_output=True, timeout=10)
        subprocess.run(["git","push"], capture_output=True, timeout=30)
    except: pass

def push_wechat(title, content):
    if not SENDKEY: return False
    try:
        r = requests.post(f"https://sctapi.ftqq.com/{SENDKEY}.send", data={"title":title,"desp":content}, timeout=15)
        return r.status_code == 200
    except: return False

def fetch_df(sym):
    url = "https://www.okx.com/api/v5/market/candles"
    r = requests.get(url, params={"instId": sym, "bar": BAR, "limit": LIMIT}, timeout=10)
    if r.json()["code"] != "0": return None
    rows = [{"ts": int(c[0]), "open": float(c[1]), "high": float(c[2]),
              "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
            for c in reversed(r.json()["data"])]
    df = pd.DataFrame(rows).astype(float)
    df.index = df["ts"].astype(int)
    return df.sort_index()

def settle_all(dfs):
    completed = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for key, trade in list(PENDING.items()):
        t_sym, t_entry_ts = key
        if t_sym not in dfs: continue
        df = dfs[t_sym]
        if t_entry_ts not in df.index: continue
        current_ts = int(df.index[-1])
        bars = df.index.get_loc(current_ts) - df.index.get_loc(t_entry_ts)
        if bars >= CONTRACT_CANDLES:
            current_price = float(df.loc[current_ts, "close"])
            win = (current_price > trade["entry_price"]) if trade["direction"] == "看涨" else (current_price < trade["entry_price"])
            pnl = 4 if win else -5
            rec = {"time": trade["time"], "coin": trade["coin"], "direction": trade["direction"],
                   "rule": trade["rule"], "entry_price": trade["entry_price"],
                   "exit_price": current_price, "pnl": pnl, "result": "赢" if win else "输", "detail": trade["detail"]}
            completed.append((key, rec))
            log.info("结算: %s %s [%s] %s %+du 入场$%.2f 出场$%.2f bars=%d",
                     trade["coin"], trade["direction"], trade["rule"], "赢" if win else "输", pnl,
                     trade["entry_price"], current_price, bars)
    # 批量推送结算
    if completed:
        parts = []
        total_pnl = 0
        for _, rec in completed:
            emoji = "OK" if rec["result"] == "赢" else "NO"
            parts.append(f"{emoji} {rec['coin']} {rec['direction']} [{rec['rule']}] {rec['result']} {rec['pnl']:+d}u")
            total_pnl += rec["pnl"]
        body = "\n".join(parts) + f"\n\n累计盈亏: {total_pnl:+d}u\n{now_str}"
        title = f"[结算] {len(completed)}笔 累计{total_pnl:+d}u"
        push_wechat(title, body)
    for key, _ in completed: del PENDING[key]
    return [v for _, v in completed]

def detect_signals(df, sym):
    try:
        c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
        ema20=ta.ema(c,20); ema60=ta.ema(c,60); rsi7=ta.rsi(c,7); rsi14=ta.rsi(c,14)
        macd=ta.macd(c,12,26,9); macd_hist=macd["MACDh_12_26_9"]
        lc=c.iloc[-1]; lr7=rsi7.iloc[-1]; lr14=rsi14.iloc[-1]
        lmh=macd_hist.iloc[-1]; pmh=macd_hist.iloc[-2] if len(macd_hist)>=2 else 0
        pos20=(c-l.rolling(20).min())/(h.rolling(20).max()-l.rolling(20).min()+1e-10); lp=pos20.iloc[-1]
        vol_sma=v.rolling(20).mean(); vr=v.iloc[-1]/vol_sma.iloc[-1] if vol_sma.iloc[-1]>0 else 0
        rsi_div=0
        if len(c)>=6 and not pd.isna(lr14):
            pu=c.iloc[-1]>c.iloc[-6]; ru=lr14>rsi14.iloc[-6]
            if pu and not ru: rsi_div=-1
            if not pu and ru: rsi_div=1
        trend="多头" if ema20.iloc[-1]>ema60.iloc[-1] else "空头"
        current_ts = int(df.index[-1])
        coin="BTC" if "BTC" in sym else "ETH"
        
        # 冷却检查
        now = datetime.now()
        if coin in LAST_SIGNAL:
            if (now - LAST_SIGNAL[coin]).total_seconds() < COOLDOWN_MINUTES * 60:
                return [], trend, lc, lr7, vr, current_ts, coin
        
        alerts=[]
        if not pd.isna(lr7) and lr7<25 and lp<0.2 and lmh>pmh:
            alerts.append(("HC1","看涨",f"极限超卖 R7={lr7:.0f} P20={lp:.2f}"))
        if rsi_div==1 and vr>1.2 and lp<0.4:
            alerts.append(("HC2","看涨",f"RSI底背离 V={vr:.1f}x"))
        if rsi_div==-1 and vr>1.2 and lp>0.6:
            alerts.append(("HC4","看跌",f"RSI顶背离 V={vr:.1f}x"))
        ba=abs(c.iloc[-1]-o.iloc[-1]); tr=h.iloc[-1]-l.iloc[-1]
        if tr>0:
            br=ba/tr; lw=(min(c.iloc[-1],o.iloc[-1])-l.iloc[-1])/tr; up=(h.iloc[-1]-max(c.iloc[-1],o.iloc[-1]))/tr
            hammer=lw>0.6 and br<0.35 and up<0.15; star=up>0.6 and br<0.35 and lw<0.15
            be=c.iloc[-1]>o.iloc[-1] and o.iloc[-2]>c.iloc[-2] and c.iloc[-1]>o.iloc[-2] and o.iloc[-1]<c.iloc[-2] if len(o)>=2 else False
            bere=c.iloc[-1]<o.iloc[-1] and o.iloc[-2]<c.iloc[-2] and c.iloc[-1]<o.iloc[-2] and o.iloc[-1]>c.iloc[-2] if len(o)>=2 else False
            if (hammer or be) and lp<0.3 and vr>1.3:
                alerts.append(("HC3","看涨",f"{'锤子线' if hammer else '看涨吞没'} V={vr:.1f}x"))
            if (star or bere) and lp>0.7 and vr>1.3:
                alerts.append(("HC5","看跌",f"{'射击星' if star else '看跌吞没'} V={vr:.1f}x"))
        return alerts, trend, lc, lr7, vr, current_ts, coin
    except Exception as e:
        log.error("信号检测异常 %s: %s", sym, e)
        return [], "?", 0, 0, 0, 0, sym

def main():
    log.info("启动 v3 冷却=%dmin SYMBOLS=%s", COOLDOWN_MINUTES, SYMBOLS)
    
    trade_df = load_trade_log()
    total = len(trade_df)
    wins_n = (trade_df["result"]=="赢").sum() if total else 0
    total_pnl = trade_df["pnl"].sum() if total else 0
    log.info("历史: %d笔 胜率%.1f%% 累计%+du", total, wins_n/total*100 if total else 0, total_pnl)
    
    # 启动只推送一次
    push_wechat("监控上线 v3", f"币种: BTC, ETH\n周期: 10分钟\n冷却: {COOLDOWN_MINUTES}分钟\n累计: {total}笔 {wins_n}赢 PnL{total_pnl:+d}u\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    settlement_batch = []  # 批量收集结算，每分钟推一次
    last_batch_push = datetime.now()
    
    while True:
        try:
            bj = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%H:%M:%S")
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{bj}]", end=" ", flush=True)
            
            dfs = {}
            for sym in SYMBOLS:
                try:
                    df = fetch_df(sym)
                    if df is not None: dfs[sym] = df
                except Exception as e:
                    log.error("拉取%s失败: %s", sym, e)
            
            # 结算
            new_records = settle_all(dfs)
            
            # 信号检测
            for sym in SYMBOLS:
                if sym not in dfs: continue
                df = dfs[sym]
                alerts, trend, lc, lr7, vr, current_ts, coin = detect_signals(df, sym)
                ns = ",".join(rl for rl,_,_ in alerts) if alerts else "-"
                status = f"{coin}${lc:,.0f} {trend} R7={lr7:.0f} V={vr:.1f}x"
                if any(k[0]==sym for k in PENDING): status += f" P{sum(1 for k in PENDING if k[0]==sym)}"
                cd = ""
                if coin in LAST_SIGNAL:
                    sec = COOLDOWN_MINUTES*60 - (datetime.now() - LAST_SIGNAL[coin]).total_seconds()
                    if sec > 0: cd = f" CD{int(sec/60)}m"
                status += f"{cd} [{ns}]"
                print(status, end="  ", flush=True)
                
                for rl,d,detail in alerts:
                    cid = f"{sym}_{current_ts}_{rl}"
                    if cid not in SEEN:
                        SEEN.add(cid)
                        if len(SEEN)>200: SEEN.clear()
                        LAST_SIGNAL[coin] = datetime.now()
                        PENDING[(sym, current_ts)] = {"time":now_str,"coin":coin,"direction":d,"rule":rl,"entry_price":lc,"detail":detail}
                        log.info("信号: %s %s [%s] @ $%.2f", coin, d, rl, lc)
                        push_wechat(f"[信号] {coin} {d} [{rl}]",
                                   f"币种: {coin}\n方向: {d}\n规则: {rl}\n详情: {detail}\n入场价: ${lc:,.2f}\n时间: {now_str}")
            
            # 保存
            if new_records:
                new_df = pd.DataFrame(new_records)
                trade_df = pd.concat([trade_df, new_df], ignore_index=True)
                save_trade_log(trade_df)
                total_n = len(trade_df)
                w = (trade_df["result"]=="赢").sum()
                pnl = trade_df["pnl"].sum()
                print(f"\n  结算{len(new_records)}笔 | 累计{total_n}笔 {w}赢 {w/total_n*100:.1f}% {pnl:+d}u", end="")
            
            print(f"  >{POLL}s")
            time.sleep(POLL)
        except Exception as e:
            log.error("异常: %s", traceback.format_exc())
            time.sleep(10)

if __name__=="__main__":
    main()
