"""
配比扫描（mix_scan.py）
====================
现货 : 方法 = 各种比例，2000U 本金
"""
import collections
import datetime as dt
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
SYM = "ETHUSDT"
FEE, MIN_NOTIONAL = 0.0005, 20.0
CAP = 2000.0

j = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
T = np.array([b["t"] for b in j], float)
C = np.array([b["c"] for b in j], float)
n = len(C)
r = np.zeros(n)
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(n, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
vol = np.full(n, np.nan)
for i in range(21, n):
    vol[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)
fr = json.loads((ROOT / "data" / "funding" / f"{SYM}.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
fd = {int(k): float(np.sum(v)) for k, v in agg.items()}
cd = np.array([int(t // 86400000) for t in T])
FR = np.nan_to_num(np.array([fd.get(int(d), np.nan) for d in cd]))
METHODS = [("固定版", None, 14.2), ("波动率目标 40%", 0.40, 52.3),
           ("波动率目标 25%", 0.25, 83.7), ("波动率目标 15%", 0.15, 139.5)]


def pick(eq):
    b = METHODS[0]
    for m in METHODS:
        if eq >= m[2]:
            b = m
    return b


def series(cap, use_method):
    eq, wp = cap, 0.0
    out = np.zeros(n)
    for i in range(60, n):
        if use_method:
            if C[i - 1] > ma[i - 1]:
                _, tv, _ = pick(eq)
                w = max(1.0, MIN_NOTIONAL / eq) if tv is None else \
                    (min(3.0, tv / vol[i - 1]) if np.isfinite(vol[i - 1]) else 0.0)
            else:
                w = 0.0
        else:
            w = 1.0
        out[i] = w * r[i] - abs(w - wp) * FEE - w * FR[i]
        eq *= (1 + out[i])
        wp = w
    return out


s_spot = series(1000, False)
s_meth = series(1000, True)

print("=" * 94)
print(f"  配比扫描（{SYM}，{dt.datetime.fromtimestamp(T[60]/1000, dt.UTC):%Y-%m-%d}"
      f" ~ {dt.datetime.fromtimestamp(T[-1]/1000, dt.UTC):%Y-%m-%d}，本金 {CAP:.0f}U）")
print("=" * 94)
print()
print(f"  {'现货占比':>9}{'方法占比':>9}{'期末':>11}{'总收益':>11}"
      f"{'最大回撤':>11}{'夏普':>9}{'收益/回撤':>11}")
print("  " + "-" * 78)
best = None
for sp in np.arange(0, 1.01, 0.1):
    x = (sp * s_spot + (1 - sp) * s_meth)[60:]
    eq = np.cumprod(1 + x)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    tot = eq[-1] - 1
    sh = x.mean() / x.std(ddof=1) * np.sqrt(365)
    ratio = tot / abs(dd) if dd else float("inf")
    print(f"  {sp*100:>8.0f}%{(1-sp)*100:>8.0f}%{CAP*(1+tot):>10.0f}U"
          f"{tot*100:>10.1f}%{dd*100:>10.1f}%{sh:>9.3f}{ratio:>10.2f}")
    if best is None or ratio > best[0]:
        best = (ratio, sp, tot, dd, sh)

print()
print(f"  ⇒ 收益/回撤 最高：现货 {best[1]*100:.0f}% / 方法 {(1-best[1])*100:.0f}%"
      f"   收益 {best[2]*100:.1f}%  回撤 {best[3]*100:.1f}%  夏普 {best[4]:.3f}")
print()
print("  ⚠️ 但这是在【同一段历史上】挑最优比例 —— 属于样本内优化。")
print("     换个时段最优比例会变。所以它只能当参考，不能当最优解。")
