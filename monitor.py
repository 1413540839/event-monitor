# -*- coding: utf-8 -*-
"""BTC/ETH 事件合约 24h监控 v2 — 含测试模式"""
import time, requests, os, logging, sys, traceback, subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np, pandas as pd, pandas_ta_classic as ta

SYMBOLS = ["BTC-USDT", "ETH-USDT"]
BAR = "5m"; LIMIT = 200; POLL = 30
SENDKEY = os.environ.get("SENDKEY", "SCT368411TN5CPulBnZ7HuuE1GKx6D9bvu")
TRADE_LOG = Path("trade_log.csv")
CONTRACT_CANDLES = 2
TEST_MODE = os.environ.get("TEST_MODE", "0") == "1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)
SEEN = set()
PENDING = {}

def load_trade_log():
    if TRADE_LOG.exists():
        return pd.read_csv(TRADE_LOG)
    return pd.DataFrame(columns=["time","coin","direction","rule","entry_price","exit_price","pnl","result","detail"])

def save_trade_log(df):
    df.to_csv(TRADE_LOG, index=False, encoding="utf-8")
    try:
        subprocess.run(["git","add","trade_log.csv"], capture_output=True, timeout=10)
        subprocess.run(["git","commit","-m","update trade log"], capture_output=True, timeout=10)
        subprocess.run(["git","push"], capture_output=True, timeout=30)
    except Exception as e:
        log.error("Git push failed: %s", e)

def push_wechat(title, content):
    try:
        url = f"https://sctapi.ftqq.com/{SENDKEY}.send"
        r = requests.post(url, data={"title": title, "desp": content}, timeout=15)
        return r.status_code == 200
    except:
        return False

def check_pending_trades(df, current_candle_ts, current_price, sym):
    completed = []
    for key, trade in list(PENDING.items()):
        t_sym, t_entry_ts = key
        if t_sym != sym: continue
        try:
            bars = df.index.get_loc(current_candle_ts) - df.index.get_loc(t_entry_ts)
        except:
            continue
        if bars >= CONTRACT_CANDLES:
            win = (current_price > trade["entry_price"]) if trade["direction"] == "看涨" else (current_price < trade["entry_price"])
            pnl = 4 if win else -5
            completed.append((key, {
                "time": trade["time"], "coin": trade["coin"], "direction": trade["direction"],
                "rule": trade["rule"], "entry_price": trade["entry_price"],
                "exit_price": current_price, "pnl": pnl, "result": "赢" if win else "输",
                "detail": trade["detail"],
            }))
            emoji = "OK" if win else "NO"
            push_wechat(f"[结算] {trade['coin']} {'赢' if win else '输'} {pnl:+d}u",
                       f"{emoji} {trade['direction']} [{trade['rule']}]\n入场: ${trade['entry_price']:,.2f}\n出场: ${current_price:,.2f}\n盈亏: {pnl:+d}u")
            log.info("结算: %s %s [%s] %s %+du", trade["coin"], trade["direction"], trade["rule"], "赢" if win else "输", pnl)
    for key in completed:
        del PENDING[key]
    return [v for _, v in completed]

def main():
    log.info("启动 %s TEST=%s", SYMBOLS, TEST_MODE)
    push_wechat("事件合约监控已上线" + (" [测试模式]" if TEST_MODE else ""),
               f"币种: BTC, ETH\n周期: 10分钟\n记录: trade_log.csv\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    trade_df = load_trade_log()
    total = len(trade_df)
    wins_n = (trade_df["result"] == "赢").sum() if total else 0
    total_pnl = trade_df["pnl"].sum() if total else 0
    log.info("历史: %d笔 胜率%.1f%% 累计%+du", total, wins_n/total*100 if total else 0, total_pnl)
    
    test_sent = False  # 测试模式只触发一次
    
    while True:
        try:
            bj = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%H:%M:%S")
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{bj}]", end=" ", flush=True)
            new_records = []
            
            for sym in SYMBOLS:
                url = "https://www.okx.com/api/v5/market/candles"
                r = requests.get(url, params={"instId": sym, "bar": BAR, "limit": LIMIT}, timeout=10)
                if r.json()["code"] != "0": continue
                rows = [{"ts": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                          "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
                        for c in reversed(r.json()["data"])]
                df = pd.DataFrame(rows).astype(float)
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
                candle_ts = df.index[-1]
                coin="BTC" if "BTC" in sym else "ETH"
                
                # 检查待回看
                completed = check_pending_trades(df, candle_ts, lc, sym)
                for rec in completed: new_records.append(rec)
                
                alerts = []
                
                # === 测试模式：强制对 ETH 发一个看涨信号（仅一次） ===
                if TEST_MODE and not test_sent and coin == "ETH":
                    alerts.append(("TEST","看涨",f"测试信号\nRSI7={lr7:.0f} 区间={lp:.2f}"))
                    test_sent = True
                
                if not pd.isna(lr7) and lr7<25 and lp<0.2 and lmh>pmh:
                    alerts.append(("HC1","看涨",f"极限超卖反弹\nRSI7={lr7:.0f} 区间低位={lp:.2f}"))
                if rsi_div==1 and vr>1.2 and lp<0.4:
                    alerts.append(("HC2","看涨",f"RSI底背离\n量={vr:.1f}倍 区间低={lp:.2f}"))
                if rsi_div==-1 and vr>1.2 and lp>0.6:
                    alerts.append(("HC4","看跌",f"RSI顶背离\n量={vr:.1f}倍 区间高={lp:.2f}"))
                ba=abs(c.iloc[-1]-o.iloc[-1]); tr=h.iloc[-1]-l.iloc[-1]
                if tr>0:
                    br=ba/tr; lw=(min(c.iloc[-1],o.iloc[-1])-l.iloc[-1])/tr; up=(h.iloc[-1]-max(c.iloc[-1],o.iloc[-1]))/tr
                    hammer=lw>0.6 and br<0.35 and up<0.15; star=up>0.6 and br<0.35 and lw<0.15
                    be=c.iloc[-1]>o.iloc[-1] and o.iloc[-2]>c.iloc[-2] and c.iloc[-1]>o.iloc[-2] and o.iloc[-1]<c.iloc[-2] if len(o)>=2 else False
                    bere=c.iloc[-1]<o.iloc[-1] and o.iloc[-2]<c.iloc[-2] and c.iloc[-1]<o.iloc[-2] and o.iloc[-1]>c.iloc[-2] if len(o)>=2 else False
                    if (hammer or be) and lp<0.3 and vr>1.3:
                        alerts.append(("HC3","看涨",f"{'锤子线' if hammer else '看涨吞没'}\n量={vr:.1f}倍 支撑位"))
                    if (star or bere) and lp>0.7 and vr>1.3:
                        alerts.append(("HC5","看跌",f"{'射击星' if star else '看跌吞没'}\n量={vr:.1f}倍 阻力位"))
                
                ns=",".join(rl for rl,_,_ in alerts) if alerts else "-"
                status = f"{coin}${lc:,.0f} {trend} R7={lr7:.0f} V={vr:.1f}x"
                if any(k[0]==sym for k in PENDING): status += f" [持仓{sum(1 for k in PENDING if k[0]==sym)}]"
                status += f" [{ns}]"
                print(status, end="  ", flush=True)
                
                for rl,d,detail in alerts:
                    cid = f"{sym}_{candle_ts}_{rl}"
                    if cid not in SEEN:
                        SEEN.add(cid)
                        if len(SEEN)>200: SEEN.clear()
                        PENDING[(sym, candle_ts)] = {"time":now_str,"coin":coin,"direction":d,"rule":rl,"entry_price":lc,"detail":detail.replace("\n"," ")}
                        log.info("开仓: %s %s [%s] @ $%.2f", coin, d, rl, lc)
                        push_wechat(f"[开仓] {coin} {d} [{rl}]", f"币种: {coin}\n方向: {d}\n规则: {rl}\n详情: {detail}\n入场价: ${lc:,.2f}\n时间: {now_str}\n\n10分钟后结算")
            
            if new_records:
                new_df = pd.DataFrame(new_records)
                trade_df = pd.concat([trade_df, new_df], ignore_index=True)
                save_trade_log(trade_df)
                total_n = len(trade_df)
                w = (trade_df["result"]=="赢").sum()
                pnl = trade_df["pnl"].sum()
                print(f"\n  结算: {len(new_records)}笔 | 累计: {total_n}笔 {w}赢 胜率{w/total_n*100:.1f}% PnL{pnl:+d}u", end="")
                if TEST_MODE:
                    print(" [测试完成!]", end="")
            
            print(f" 下次:{POLL}s")
            time.sleep(POLL)
            
            if TEST_MODE and test_sent and len(PENDING) == 0 and len(new_records) == 0:
                # 等了一轮还没结算？继续等
                pass
                
        except Exception as e:
            log.error("异常: %s", traceback.format_exc())
            time.sleep(10)

if __name__=="__main__":
    main()
