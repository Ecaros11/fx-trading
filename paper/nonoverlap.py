"""
不重叠窗口检验（nonoverlap.py）
============================
把 2506 天切成 K 个【不重叠】的块，每块内跑一次定投。
这才是真正独立的样本 —— 没有重叠导致的 t 值虚高。
"""
import collections
import datetime as dt
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
FEE, SPREAD, TV = 0.0005, 0.003, 0.40
INIT = MONTHLY = 500.0
FX = {2020: 6.90, 2021: 6.45, 2022: 6.73, 2023: 7.08,
      2024: 7.20, 2025: 7.15, 2026: 7.15}
fx_of = lambda d: FX.get(d.year, 7.15)

j = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))
T = np.array([b["t"] for b in j], float)
C = np.array([b["c"] for b in j], float)
nn = len(C)
r = np.zeros(nn)
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(nn, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
sig = np.nan_to_num((C > ma).astype(float))
fr = json.loads((ROOT / "data" / "funding" / "ETHUSDT.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
fday = {int(k): float(np.sum(v)) for k, v in agg.items()}
cd = np.array([int(t // 86400000) for t in T])
FR = np.nan_to_num(np.array([fday.get(int(d), np.nan) for d in cd]))
DAY = [dt.datetime.fromtimestamp(t / 1000, dt.UTC) for t in T]


def volw(w):
    v = np.full(nn, np.nan)
    for i in range(w + 1, nn):
        v[i] = r[i - w:i].std(ddof=1) * np.sqrt(365)
    return v


V20, V10 = volw(20), volw(10)
START = 130


def run(i0, i1, V):
    day = DAY[i0].day
    eq, pk, dd, wp, inv = 0.0, 0.0, 0.0, 0.0, 0.0
    seen = set()
    for i in range(i0, i1 + 1):
        x = DAY[i]
        amt = 0.0
        if i == i0:
            amt = INIT; seen.add((x.year, x.month))
        elif x.day == day and (x.year, x.month) not in seen:
            amt = MONTHLY; seen.add((x.year, x.month))
        if amt:
            eq += amt / fx_of(x) * (1 - SPREAD)
            inv += amt
        if eq <= 0:
            continue
        if sig[i - 1] and np.isfinite(V[i - 1]) and V[i - 1] > 0:
            w = min(3.0, TV / V[i - 1])
        else:
            w = 0.0
        eq *= (1 + w * r[i] - abs(w - wp) * FEE - w * FR[i])
        pk = max(pk, eq)
        dd = min(dd, eq / pk - 1) if pk > 0 else 0.0
        wp = w
    return (eq * fx_of(DAY[i1]) * (1 - SPREAD) / inv - 1) * 100, dd


span = nn - START
print("=" * 96)
print("  不重叠窗口检验（真正独立的样本）")
print("=" * 96)
print()

for K in (4, 6, 8, 10, 12):
    L = span // K
    if L < 120:
        continue
    d20, d10, dd20, dd10 = [], [], [], []
    for k in range(K):
        a = START + k * L
        b = a + L - 1 if k < K - 1 else nn - 1
        if b - a < 100:
            continue
        p20, q20 = run(a, b, V20)
        p10, q10 = run(a, b, V10)
        d20.append(p20); d10.append(p10); dd20.append(q20); dd10.append(q10)
    d20, d10 = np.array(d20), np.array(d10)
    dif = d10 - d20
    t = dif.mean() / (dif.std(ddof=1) / np.sqrt(len(dif)))
    print(f"  K = {K:>2} 块（每块约 {L} 天 = {L/30:.0f} 个月）")
    print(f"     20日 均值 {d20.mean():>7.1f}%   10日 均值 {d10.mean():>7.1f}%"
          f"   差 {dif.mean():>+7.1f}pp")
    print(f"     10日更好 {int((dif>0).sum())} / {len(dif)}")
    print(f"     配对 t = {t:>+6.2f}   "
          f"{'✅ 显著' if abs(t) > 2.0 else '❌ 不显著（自由度 %d）' % (len(dif)-1)}")
    print(f"     回撤：20日 {np.mean(dd20)*100:>6.1f}%   10日 {np.mean(dd10)*100:>6.1f}%")
    print(f"     明细差：" + "  ".join(f"{x:+.0f}" for x in dif))
    print()
