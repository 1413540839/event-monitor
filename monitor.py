# -*- coding: utf-8 -*-
"""BTC/ETH 事件合约 24h监控 — GitHub Actions 版"""
import time, requests, smtplib, base64, os, logging, sys, traceback
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd, pandas_ta_classic as ta

SYMBOLS = ["BTC-USDT", "ETH-USDT"]
BAR = "5m"; LIMIT = 200; POLL = 30

SMTP_USER = os.environ.get("QQ_USER", "1413540839@qq.com")
SMTP_PASS = os.environ.get("QQ_PASS", "oeegrnacybrrfhdj")
TO_EMAIL = "1413540839@qq.com"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)
SEEN = set()

def send_qq_alert(coin, direction, rule, detail, price):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject_text = f"[事件合约] {coin} {direction}信号"
    body_text = f"信号时间: {now_str}\n\n币种: {coin}\n方向: {direction}\n规则: {rule}\n详情: {detail}\n当前价格: ${price:,.2f}\n\n--- 事件合约监控 HC v3 ---"
    subject_b64 = base64.b64encode(subject_text.encode("utf-8")).decode()
    body_b64 = base64.b64encode(body_text.encode("utf-8")).decode()
    msg = f"""From: {SMTP_USER}
To: {TO_EMAIL}
Subject: =?utf-8?B?{subject_b64}?=
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: base64

{body_b64}""".encode("utf-8")
    try:
        server = smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=10)
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [TO_EMAIL], msg)
        server.quit()
        log.info("邮件已发送: %s %s [%s]", coin, direction, rule)
        return True
    except Exception as e:
        log.error("邮件失败: %s", e)
        return False

def main():
    log.info("监控启动 %s %s", SYMBOLS, BAR)
    if not send_qq_alert("系统", "启动", "INIT", "GitHub Actions 监控上线", 0):
        log.warning("启动邮件发送失败")
    
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
                    alerts.append(("HC1","看涨",f"极限超卖反弹 R7={lr7:.0f} 低位={lp:.2f}"))
                if rsi_div==1 and vr>1.2 and lp<0.4:
                    alerts.append(("HC2","看涨",f"RSI底背离 量={vr:.1f}x 低={lp:.2f}"))
                if rsi_div==-1 and vr>1.2 and lp>0.6:
                    alerts.append(("HC4","看跌",f"RSI顶背离 量={vr:.1f}x 高={lp:.2f}"))
                ba=abs(c.iloc[-1]-o.iloc[-1]); tr=h.iloc[-1]-l.iloc[-1]
                if tr>0:
                    br=ba/tr; lw=(min(c.iloc[-1],o.iloc[-1])-l.iloc[-1])/tr; up=(h.iloc[-1]-max(c.iloc[-1],o.iloc[-1]))/tr
                    hammer=lw>0.6 and br<0.35 and up<0.15; star=up>0.6 and br<0.35 and lw<0.15
                    be=c.iloc[-1]>o.iloc[-1] and o.iloc[-2]>c.iloc[-2] and c.iloc[-1]>o.iloc[-2] and o.iloc[-1]<c.iloc[-2] if len(o)>=2 else False
                    bere=c.iloc[-1]<o.iloc[-1] and o.iloc[-2]<c.iloc[-2] and c.iloc[-1]<o.iloc[-2] and o.iloc[-1]>c.iloc[-2] if len(o)>=2 else False
                    if (hammer or be) and lp<0.3 and vr>1.3:
                        alerts.append(("HC3","看涨",f"{'锤子线' if hammer else '看涨吞没'} 量={vr:.1f}x"))
                    if (star or bere) and lp>0.7 and vr>1.3:
                        alerts.append(("HC5","看跌",f"{'射击星' if star else '看跌吞没'} 量={vr:.1f}x"))
                ns=",".join(rl for rl,_,_ in alerts) if alerts else "-"
                print(f"{coin}${lc:,.0f} {trend} R7={lr7:.0f} V={vr:.1f}x [{ns}]", end="  ", flush=True)
                for rl,d,detail in alerts:
                    aid=f"{candle_id}_{rl}"
                    if aid not in SEEN:
                        SEEN.add(aid)
                        if len(SEEN)>200: SEEN.clear()
                        log.info("信号: %s %s [%s] %s", coin, d, rl, detail)
                        send_qq_alert(coin,d,rl,detail,lc)
            print(f"下次:{POLL}s")
            time.sleep(POLL)
        except Exception as e:
            log.error("异常: %s", traceback.format_exc())
            time.sleep(10)

if __name__=="__main__":
    main()
