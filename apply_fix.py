p = r"C:\Users\14135\Documents\Codex\2026-06-17\new-chat\outputs\monitor.py"
with open(p, "r", encoding="utf-8") as f:
    c = f.read()

# Fix daily loss limit: -10 -> -30
c = c.replace("if DAILY_PNL.get(today, 0) <= -10:", "if DAILY_PNL.get(today, 0) <= -30:")

# Fix consecutive loss pause: 4 -> 7
c = c.replace("if CONSEC_LOSS >= 4 and", "if CONSEC_LOSS >= 7 and")

# Fix pause hours after consecutive losses: 4 -> 2
c = c.replace('CRASH_HALT["pause"] = datetime.now() + timedelta(hours=4)',
              'CRASH_HALT["pause"] = datetime.now() + timedelta(hours=2)')

with open(p, "w", encoding="utf-8") as f:
    f.write(c)

import py_compile
py_compile.compile(p, doraise=True)
print("Fixed: daily limit -10->-30, cons loss 4->7, pause 4h->2h")
