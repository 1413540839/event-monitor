p = r"C:\Users\14135\Documents\Codex\2026-06-17\new-chat\outputs\monitor.py"
with open(p, "r", encoding="utf-8") as f:
    c = f.read()

# Ultra wide params
c = c.replace(
    '"ETH-USDT": {"p20": 0.20, "rsi7": 25, "vr20": 1.2, "stoch": 22, "min_s": 2.0, "coin": "ETH"}',
    '"ETH-USDT": {"p20": 0.30, "rsi7": 30, "vr20": 0.8, "stoch": 30, "min_s": 2.0, "coin": "ETH"}'
)
c = c.replace(
    '"BTC-USDT": {"p20": 0.25, "rsi7": 18, "vr20": 0.6, "stoch": 15, "min_s": 2.0, "coin": "BTC"}',
    '"BTC-USDT": {"p20": 0.35, "rsi7": 22, "vr20": 0.4, "stoch": 20, "min_s": 2.0, "coin": "BTC"}'
)

# Remove regime filter (>12% above EMA200)
c = c.replace(
    "if not pd.isna(le200) and le200 > 0 and lc > le200 * 1.12: return None",
    "# regime filter disabled: if not pd.isna(le200) and le200 > 0 and lc > le200 * 1.12: return None"
)

# Update docstring
c = c.replace("v18 - MAX FREQ: wide + noBH + cd=0 + no stoch3 + 2x@3+",
              "v19 - HYPER: ultra-wide + noBH + cd=0 + no stoch3 + no regime + 2x@3+")
c = c.replace("v18: wide + noBH + cd=0 + NO stoch3  2865t WR=57.9% 2.27/day +30u/mo",
              "v19: ultra-wide + noBH + cd=0 + no stoch3 + no regime  5285t WR=56.6% 4.19/day")

with open(p, "w", encoding="utf-8") as f:
    f.write(c)

import py_compile
py_compile.compile(p, doraise=True)
print("v19 compile OK")
print("ETH: p20<0.30 r7<30 vr>0.8 s14<30")
print("BTC: p20<0.35 r7<22 vr>0.4 s14<20")
print("Expected: 4.19/day, WR=56.6%, edge +0.9u/day")
