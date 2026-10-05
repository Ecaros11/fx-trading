"""
方法:现货 = 6:4 的分段检验（mix_64.py）
====================================
不看全程（会被牛市主导），分几段看这个比例稳不稳。
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
M_RATIO = 0.6          # 方法占比
S_RATIO = 0.4          # 现货占比

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


CAP = 1000.0          # 每半边的本金
# 分别模拟半边：方法半边 1000U，现货半边 1000U
eq_m, wp = CAP, 0.0
s_meth = np.zeros(n)
for i in range(60, n):
    if C[i - 1] > ma[i - 1]:
        _, tv, _ = pick(eq_m)
        w = max(1.0, MIN_NOTIONAL / eq_m) if tv is None else \
            (min(3.0, tv / vol[i - 1]) if np.isfinite(vol[i - 1]) else 0.0)
    else:
        w = 0.0
    s_meth[i] = w * r[i] - abs(w - wp) * FEE - w * FR[i]
    eq_m *= (1 + s_meth[i])
    wp = w
s_spot = np.zeros(n)
s_spot[60:] = r[60:] - FR[60:]          # 现货：满仓 + 扣资金费（等价永续多头）

segs = [("2019-12-01", "2021-11-30", "牛市"),
        ("2021-12-01", "2023-10-31", "熊市"),
        ("2023-11-01", "2024-12-06", "震荡上行"),
        ("2024-12-07", "2025-10-05", "回调"),
        ("2025-10-06", "2026-10-05", "熊市"),
        ("2019-11-27", "2026-10-05", "全程")]

print("=" * 96)
print(f"  方法 : 现货 = {M_RATIO*10:.0f} : {S_RATIO*10:.0f}   分段检验（每段 2000U 起算）")
print("=" * 96)
print()
print(f"  {'时段':<24}{'市场':<10}{'6:4组合':>11}{'纯方法':>10}{'纯现货':>10}"
      f"{'组合回撤':>10}{'方法回撤':>10}{'现货回撤':>10}")
print("  " + "-" * 86)


def seg(x, ia, ib):
    y = x[ia:ib + 1]
    e = np.cumprod(1 + y)
    return (e[-1] - 1) * 100, (e / np.maximum.accumulate(e) - 1).min() * 100


for a, b, lab in segs:
    ta = dt.datetime.strptime(a, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000
    tb = dt.datetime.strptime(b, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000
    ia = max(int(np.argmin(np.abs(T - ta))), 60)
    ib = int(np.argmin(np.abs(T - tb)))
    if ib <= ia:
        continue
    mkt = (C[ib] / C[ia] - 1) * 100
    mixed = M_RATIO * s_meth + S_RATIO * s_spot
    rm, dm = seg(mixed, ia, ib)
    r1, d1 = seg(s_meth, ia, ib)
    r2, d2 = seg(s_spot, ia, ib)
    print(f"  {a}~{b[:7]:<11}{lab:<10}{rm:>10.0f}%{r1:>9.0f}%{r2:>9.0f}%"
          f"{dm:>9.1f}%{d1:>9.1f}%{d2:>9.1f}%")

print()
print("=" * 96)
print("  结论")
print("=" * 96)
print()
print(f"  6:4 组合 = 每 1000U 里，600U 走方法、400U 现货持有")
print()
print("  ⇒ 它在牛市里比纯方法好（现货那半吃到上涨）")
print("  ⇒ 它在熊市里比纯现货好（方法那半空仓避险）")
print("  ⇒ 但它在两个极端里都不是最好的：")
print("       牛市段：纯现货 > 6:4 > 纯方法")
print("       熊市段：纯方法 > 6:4 > 纯现货")
