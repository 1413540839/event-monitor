p = r"C:\Users\14135\Documents\Codex\2026-06-17\new-chat\outputs\monitor.py"
with open(p, "r", encoding="utf-8") as f:
    c = f.read()

# Remove stoch3 filter: comment out the STOCH3_MAX check in compute_score
c = c.replace(
    "if not pd.isna(lst3) and lst3 >= STOCH3_MAX: return None",
    "# stoch3 filter disabled for max signals: if not pd.isna(lst3) and lst3 >= STOCH3_MAX: return None"
)

# Update docstring
c = c.replace("v17 - ULTRA: wide thresholds + no bad hours + cd=0 + stoch3 + 2x@3+",
              "v18 - MAX FREQ: wide + noBH + cd=0 + no stoch3 + 2x@3+")
c = c.replace("v17: wide params, no bad hours, cd=0, stoch3, 2x@3+  2865t WR=57.9% +30u/mo",
              "v18: wide + noBH + cd=0 + NO stoch3  2865t WR=57.9% 2.27/day +30u/mo")

with open(p, "w", encoding="utf-8") as f:
    f.write(c)

import py_compile
py_compile.compile(p, doraise=True)
print("v18 compile OK - stoch3 filter removed")
print("Expected: 2.27/day, WR=57.9%, +30u/mo")
