p = r"C:\Users\14135\Documents\Codex\2026-06-17\new-chat\outputs\monitor.py"
with open(p, "r", encoding="utf-8") as f:
    c = f.read()

# 1. Change BAR from 1H to 15m
c = c.replace('BAR = "1H"', 'BAR = "15m"')

# 2. Restore v12g original params
c = c.replace(
    '"ETH-USDT": {"p20": 0.30, "rsi7": 30, "vr20": 0.8, "stoch": 30, "min_s": 2.0, "coin": "ETH"}',
    '"ETH-USDT": {"p20": 0.10, "rsi7": 18, "vr20": 2.0, "stoch": 15, "min_s": 2.0, "coin": "ETH"}'
)
c = c.replace(
    '"BTC-USDT": {"p20": 0.35, "rsi7": 22, "vr20": 0.4, "stoch": 20, "min_s": 2.0, "coin": "BTC"}',
    '"BTC-USDT": {"p20": 0.15, "rsi7": 12, "vr20": 1.0, "stoch": 8, "min_s": 2.0, "coin": "BTC"}'
)

# 3. Update docstring
c = c.replace("v19 - HYPER: ultra-wide + noBH + cd=0 + no stoch3 + no regime + 2x@3+",
              "v20 - 10MIN: v12g params + ms=2.0 + cd=0 + 15m bars")
c = c.replace("v19: ultra-wide + noBH + cd=0 + no stoch3 + no regime  5285t WR=56.6% 4.19/day",
              "v20: 15m bars v12g orig ms=2.0  17.4/day WR=56.7% +81u/mo")

with open(p, "w", encoding="utf-8") as f:
    f.write(c)

import py_compile
py_compile.compile(p, doraise=True)
print("v20 compile OK - 15m bars, v12g params, ms=2.0")
print("Expected: 17.4/day, WR=56.7%, +81u/mo")
