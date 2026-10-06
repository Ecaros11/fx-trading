"""
最大可行方案：随机切分的不重叠窗口（nonoverlap_final.py）
=====================================================
① 每次把时间轴随机切成 ~13 段【互不重叠】的窗口
② 重复 50 次不同的随机切分
③ 每段都跑 20日 和 10日，配对比较
⇒ 得到 650 个真正独立的配对观测
"""
import collections
import datetime as dt
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
FEE, SPREAD, TV = 0.0005, 0.003, 0.40
INIT = MONTHLY = 500.0
MINLEN = 180
START = 130
NSEED = 50
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
    return (eq * fx_of(DAY[i1]) * (1 - SPREAD) / inv - 1) * 100, dd, i1 - i0 + 1


def partition(seed):
    """随机切成不重叠的段，每段 >= MINLEN"""
    g = np.random.default_rng(seed)
    cuts = [START]
    pos = START
    while nn - pos >= 2 * MINLEN:
        hi = nn - MINLEN
        if hi <= pos + MINLEN:
            break
        c = int(g.integers(pos + MINLEN, hi + 1))
        cuts.append(c)
        pos = c
    cuts.append(nn)
    return [(cuts[k], cuts[k + 1] - 1) for k in range(len(cuts) - 1)
            if cuts[k + 1] - cuts[k] >= MINLEN]


print("=" * 96)
print(f"  {NSEED} 次随机切分 × 每次数个不重叠窗口")
print(f"  20日 vs 10日 · 每月 500 元 · 40% 档 · 含手续费和资金费")
print("=" * 96)
print()

allp20, allp10, alld, alllen = [], [], [], []
ts = []
for s in range(NSEED):
    segs = partition(1000 + s)
    if len(segs) < 3:
        continue
    p20, p10, dd20, dd10, L = [], [], [], [], []
    for a, b in segs:
        x20, q20, ln = run(a, b, V20)
        x10, q10, _ = run(a, b, V10)
        p20.append(x20); p10.append(x10); dd20.append(q20); dd10.append(q10)
        L.append(ln)
    p20, p10 = np.array(p20), np.array(p10)
    t = (p10 - p20).mean() / ((p10 - p20).std(ddof=1) / np.sqrt(len(p20)))
    ts.append(t)
    allp20 += p20.tolist(); allp10 += p10.tolist()
    alld += (p10 - p20).tolist(); alllen += L
    if s < 6:
        print(f"  切分 {s+1:>2}：{len(segs)} 块  "
              f"20日 {p20.mean():>6.1f}%  10日 {p10.mean():>6.1f}%  "
              f"差 {(p10-p20).mean():>+6.1f}pp   t={t:>+5.2f}")
print(f"  ... （共 {NSEED} 次切分）")

allp20 = np.array(allp20); allp10 = np.array(allp10)
alld = np.array(alld); alllen = np.array(alllen)
ts = np.array(ts)

print()
print("=" * 96)
print("  汇总（所有不重叠窗口合并）")
print("=" * 96)
print()
print(f"  有效独立观测数    {len(allp20)}")
print(f"  窗口长度          {alllen.min()} ~ {alllen.max()} 天，均值 {alllen.mean():.0f}")
print()
print(f"  {'':<18}{'20日':>14}{'10日':>14}{'差':>12}")
print("  " + "-" * 60)
print(f"  {'平均收益':<18}{allp20.mean():>13.1f}%{allp10.mean():>13.1f}%"
      f"{alld.mean():>+11.1f}pp")
print(f"  {'中位收益':<18}{np.median(allp20):>13.1f}%{np.median(allp10):>13.1f}%"
      f"{np.median(alld):>+11.1f}pp")
print(f"  {'最差收益':<18}{allp20.min():>13.1f}%{allp10.min():>13.1f}%"
      f"{alld.min():>+11.1f}pp")
print(f"  {'最好收益':<18}{allp20.max():>13.1f}%{allp10.max():>13.1f}%"
      f"{alld.max():>+11.1f}pp")
print()
print(f"  10 日更好        {int((alld>0).sum())} / {len(alld)}"
      f"（{(alld>0).mean()*100:.1f}%）")
print(f"  差异均值         {alld.mean():+.2f}pp")
print(f"  差异标准差       {alld.std(ddof=1):.2f}pp")

# 只在【同一个切分内】做配对 t（这是严格的）
print()
print("=" * 96)
print("  逐次切分的配对 t 统计量分布")
print("=" * 96)
print()
print(f"  t 统计量：均值 {ts.mean():+.2f}   中位 {np.median(ts):+.2f}   "
      f"范围 {ts.min():+.2f} ~ {ts.max():+.2f}")
print(f"  显著（|t|>2.0）的次数   {int((np.abs(ts)>2.0).sum())} / {len(ts)}"
      f"（{(np.abs(ts)>2.0).mean()*100:.0f}%）")
print()
print(f"  ⇒ 每次切分内是【真独立】的配对检验")
print(f"     如果 {len(ts)} 次里绝大多数显著 ⇒ 结论稳健")
print(f"     如果只有一半显著 ⇒ 结论不稳")
