"""
四档全验：对方的数是不是都来自前视（verify_all_lookahead.py）
"""
import collections
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
SYM = "ETHUSDT"
COST = 0.0005

j = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
C = np.array([b["c"] for b in j], float)
T = np.array([b["t"] for b in j], float)
n = len(C)
r = np.zeros(n)
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(n, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
sig = np.nan_to_num((C > ma).astype(float))
vol20 = np.full(n, np.nan)
for i in range(21, n):
    vol20[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)
fr = json.loads((ROOT / "data" / "funding" / f"{SYM}.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
fd = {int(k): float(np.sum(v)) for k, v in agg.items()}
cd = np.array([int(t // 86400000) for t in T])
FR = np.nan_to_num(np.array([fd.get(int(d), np.nan) for d in cd]))


def go(w, lookahead, cost=True):
    if lookahead:
        pr = w * r
    else:
        pr = np.zeros(n)
        pr[1:] = w[:-1] * r[1:]
    if cost:
        pr -= np.abs(np.diff(w, prepend=w[0])) * COST
        pr -= w * FR
    x = pr[60:]
    eq = np.cumprod(1 + x)
    return (x.mean() / x.std(ddof=1) * np.sqrt(365),
            x.std(ddof=1) * np.sqrt(365),
            (eq / np.maximum.accumulate(eq) - 1).min())


THEIRS = {
    "固定 1.405x":    (None, None, -0.349),
    "波动率目标 40%":  (2.788, 0.357, -0.166),
    "波动率目标 25%":  (2.788, None, -0.106),
    "波动率目标 15%":  (2.788, None, -0.065),
}

print("=" * 100)
print("  四档全验：正确口径 vs 前视口径 vs 对方报的")
print("=" * 100)
print()
print(f"  {'版本':<18}{'口径':<12}{'夏普':>9}{'波动':>9}{'回撤':>10}"
      f"{'对方夏普':>10}{'对方回撤':>10}")
print("  " + "-" * 80)

for lab, tv in (("固定 1.405x", None), ("波动率目标 40%", 0.40),
                ("波动率目标 25%", 0.25), ("波动率目标 15%", 0.15)):
    w = sig * (1.405 if tv is None else np.clip(tv / vol20, 0, 3))
    w = np.nan_to_num(w)
    ts, tvol, tdd = THEIRS[lab]
    for la, name in ((False, "正确"), (True, "前视")):
        sr, vol, dd = go(w, la)
        print(f"  {lab:<18}{name:<12}{sr:>9.3f}{vol*100:>8.1f}%{dd*100:>9.1f}%"
              f"{(f'{ts:.3f}' if ts else '—'):>10}"
              f"{(f'{tdd*100:.1f}%' if tdd else '—'):>10}")
    print()
