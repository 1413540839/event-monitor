p = r"C:\Users\14135\Documents\Codex\2026-06-17\new-chat\outputs\monitor.py"
with open(p, "r", encoding="utf-8") as f:
    c = f.read()

# 1. Loss cooldown 60 -> 0
c = c.replace("cd_minutes = 60   # more signals", "cd_minutes = 0    # no cooldown max signals")
c = c.replace("cd_minutes = 60  # default", "cd_minutes = 0   # no cooldown")

# 2. Update docstring
c = c.replace("v15 - MORE signals: min_s=2.0 cd=60min closed-candle stoch3 2x@3+",
              "v16 - MAX signals: min_s=2.0 cd=0 closed-candle stoch3 2x@3+")
c = c.replace("v15: min_s=2.0(2.7x signals) cd=60min closed-candle stoch3 2x@score>=3",
              "v16: min_s=2.0 cd=0(no cooldown) closed-candle stoch3 2x@3+ 655t WR=63.5%")

with open(p, "w", encoding="utf-8") as f:
    f.write(c)

import py_compile
py_compile.compile(p, doraise=True)
print("v16 compile OK - cd=0 deployed")
