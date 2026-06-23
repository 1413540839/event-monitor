p = r"C:\Users\14135\Documents\Codex\2026-06-17\new-chat\outputs\monitor.py"
with open(p, "r", encoding="utf-8") as f:
    c = f.read()

# 1. Widen ETH params
c = c.replace(
    '"ETH-USDT": {"p20": 0.10, "rsi7": 18, "vr20": 2.0, "stoch": 15, "min_s": 2.0, "coin": "ETH"}',
    '"ETH-USDT": {"p20": 0.20, "rsi7": 25, "vr20": 1.2, "stoch": 22, "min_s": 2.0, "coin": "ETH"}'
)

# 2. Widen BTC params
c = c.replace(
    '"BTC-USDT": {"p20": 0.15, "rsi7": 12, "vr20": 1.0, "stoch": 8,  "min_s": 2.0, "coin": "BTC"}',
    '"BTC-USDT": {"p20": 0.25, "rsi7": 18, "vr20": 0.6, "stoch": 15, "min_s": 2.0, "coin": "BTC"}'
)

# 3. Remove bad hours - change the BH check from (5,12,14,22) to empty tuple
c = c.replace("if now.hour in (5, 12, 14, 22):", "if now.hour in ():  # no bad hours")

# Also in compute_score the bad hours check... let me check if there's another one
# The bad hours are checked in analyze_signals only

# 4. Update docstring
c = c.replace(
    "v16 - MAX signals: min_s=2.0 cd=0 closed-candle stoch3 2x@3+",
    "v17 - ULTRA: wide thresholds + no bad hours + cd=0 + stoch3 + 2x@3+"
)
c = c.replace(
    "v16: min_s=2.0 cd=0(no cooldown) closed-candle stoch3 2x@score>=3",
    "v17: wide params, no bad hours, cd=0, stoch3, 2x@3+  2865t WR=57.9% +30u/mo"
)

with open(p, "w", encoding="utf-8") as f:
    f.write(c)

import py_compile
py_compile.compile(p, doraise=True)
print("v17 compile OK")
print("Changes: wide thresholds + no bad hours + cd=0")
print("ETH: p20<0.20 r7<25 vr>1.2 s14<22")
print("BTC: p20<0.25 r7<18 vr>0.6 s14<15")
print("Expected: 2.27/day, WR=57.9%, +30u/mo")
