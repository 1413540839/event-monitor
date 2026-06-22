# -*- coding: utf-8 -*-
"""BTC/ETH 事件合约 24h监控 — Server酱微信推送版"""
import time, requests, os, logging, sys, traceback, urllib.parse
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd, pandas_ta_classic as ta

SYMBOLS = ["BTC-USDT", "ETH-USDT"]
BAR = "5m"; LIMIT = 200; POLL = 30
SENDKEY = os.environ.get("SENDKEY", "SCT368411TN5CPulBnZ7HuuE1GKx6D9bvu")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)
SEEN = set()

def push_wechat(title, content):
    """Server酱推送到微信"""
    try:
        url = f"https://sctapi.ftqq.com/{SENDKEY}.send"
        data = {"title": title, "desp": content}
        r = requests.post(url, data=data, timeout=15)
        if r.status_code == 200:
            log.info("微信推送成功: %s", title)
            return True
        log.error("推送返回: %s", r.text[:100])
        return False
    except Exception as e:
        log.error("推送失败: %s", e)
        return False

def main():
    log.info("监控启动 %s %s", SYMBOLS, BAR)
    push_wechat("事件合约监控已上线", f"币种: BTC, ETH\n周期: 10分钟合约\n规则: HC v3\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    while True:
        try:
            bj = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%H:%M:%S")
            print(f"[{bj}]", end=" ", flush=True)
            for sym in SYMBOLS:
                url = "https://www.okx.com/api/v5/market/candles"
                r = requests.get(url, params={"instId": sym, "bar": BAR, "limit": LIMIT}, timeout=10)
                data = r.json()
                if data["code"] != "0": continue
                rows = [{"ts": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                          "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
                        for c in reversed(data["data"])]
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
                candle_id=f"{sym}_{df.index[-1]}"
                coin="BTC" if "BTC" in sym else "ETH"
                alerts=[]
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
                print(f"{coin}${lc:,.0f} {trend} R7={lr7:.0f} V={vr:.1f}x [{ns}]", end="  ", flush=True)
                for rl,d,detail in alerts:
                    aid=f"{candle_id}_{rl}"
                    if aid not in SEEN:
                        SEEN.add(aid)
                        if len(SEEN)>200: SEEN.clear()
                        log.info("信号: %s %s [%s]", coin, d, rl)
                        title = f"[事件合约] {coin} {d} [{rl}]"
                        body = f"币种: {coin}\n方向: {d}\n规则: {rl}\n详情: {detail}\n价格: ${lc:,.2f}\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        push_wechat(title, body)
            print(f"下次:{POLL}s")
            time.sleep(POLL)
        except Exception as e:
            log.error("异常: %s", traceback.format_exc())
            time.sleep(10)

if __name__=="__main__":
    main()
