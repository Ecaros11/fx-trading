"""
修正版：正确的统计（nonoverlap_correct.py）
========================================
修正两处：
  ① 每次切分用自己的【正确临界 t 值】（按自由度）
  ② 不合并跨切分的观测 —— 以"每次切分的均值"为单位
"""
import collections
import datetime as dt
import json
import pathlib

import numpy as np
# 没有 scipy，用临界值表（双尾 5%）
TCRIT = {1:12.706,2:4.303,3:3.182,4:2.776,5:2.571,6:2.447,7:2.365,
         8:2.306,9:2.262,10:2.228,11:2.201,12:2.179,13:2.160,14:2.145,
         15:2.131,16:2.120,17:2.110,18:2.101,19:2.093,20:2.086}
def tcrit(df):
    return TCRIT.get(df, 1.96)

ROOT = pathlib.Path(__file__).parent.parent
FEE, SPREAD, TV = 0.0005, 0.003, 0.40
INIT = MONTHLY = 500.0
MINLEN = 180
START = 130
NSEED = 200
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
    return (eq * fx_of(DAY[i1]) * (1 - SPREAD) / inv - 1) * 100, dd


def partition(seed):
    g = np.random.default_rng(seed)
    cuts, pos = [START], START
    while nn - pos >= 2 * MINLEN:
        hi = nn - MINLEN
        if hi <= pos + MINLEN:
            break
        c = int(g.integers(pos + MINLEN, hi + 1))
        cuts.append(c); pos = c
    cuts.append(nn)
    return [(cuts[k], cuts[k + 1] - 1) for k in range(len(cuts) - 1)
            if cuts[k + 1] - cuts[k] >= MINLEN]


print("=" * 94)
print(f"  修正版：{NSEED} 次随机切分，每次用【正确的临界 t】")
print("=" * 94)
print()

part_mean, part_t, part_n, part_signif = [], [], [], []
for s in range(NSEED):
    segs = partition(1000 + s)
    if len(segs) < 3:
        continue
    d = []
    for a, b in segs:
        x20, _ = run(a, b, V20)
        x10, _ = run(a, b, V10)
        d.append(x10 - x20)
    d = np.array(d)
    t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))
    crit = tcrit(len(d) - 1)
    part_mean.append(d.mean())
    part_t.append(t)
    part_n.append(len(d))
    part_signif.append(abs(t) > crit)

pm = np.array(part_mean)
pt = np.array(part_t)
pn = np.array(part_n)
ps = np.array(part_signif)

print(f"  有效切分次数      {len(pm)}")
print(f"  每次段数          {pn.min()} ~ {pn.max()}，均值 {pn.mean():.1f}")
print()
print("  ── ① 关键：以【每次切分的均值】为单位（避免跨切分重叠）──")
print()
print(f"  切分均值差的分布：")
print(f"     均值     {pm.mean():+.2f}pp")
print(f"     中位     {np.median(pm):+.2f}pp")
print(f"     标准差   {pm.std(ddof=1):.2f}pp")
print(f"     范围     {pm.min():+.1f} ~ {pm.max():+.1f}pp")
se = pm.std(ddof=1) / np.sqrt(len(pm))
print(f"     标准误   {se:.2f}pp")
print(f"     95% CI   [{pm.mean()-1.96*se:+.2f}, {pm.mean()+1.96*se:+.2f}]pp")
print(f"     ⇒ {'✅ 不含 0 ⇒ 显著' if pm.mean()-1.96*se > 0 else '❌ 含 0 ⇒ 不显著'}")

print()
print("  ── ② 用正确临界值的显著比例 ──")
print()
print(f"  {'段数':>5}{'df':>5}{'临界t':>9}{'次数':>7}{'其中显著':>10}{'比例':>8}")
print("  " + "-" * 46)
for nseg in sorted(set(pn)):
    m = pn == nseg
    crit = tcrit(nseg - 1)
    sg = int((np.abs(pt[m]) > crit).sum())
    print(f"  {nseg:>5}{nseg-1:>5}{crit:>9.3f}{int(m.sum()):>7}{sg:>10}"
          f"{sg/m.sum()*100:>7.0f}%")

tot_sig = int(ps.sum())
print()
print(f"  总体：显著 {tot_sig} / {len(ps)}（{tot_sig/len(ps)*100:.0f}%）")
print(f"  ⚠️ 零假设下的期望 ≈ 5%")
print(f"  ⇒ {'✅ 远超 5%' if tot_sig/len(ps) > 0.20 else '⚠️ 需要更多数据'}")

print()
print("  ── ③ t 值 vs 段数（检验是否有功效偏差）──")
nz = np.abs(pt)
print(f"     相关（段数 vs |t|）  {np.corrcoef(pn, nz)[0,1]:+.3f}")
print(f"     ⇒ {'⚠️ 段数越多越容易显著（功效偏差）' if np.corrcoef(pn,nz)[0,1] > 0.3 else '✅ 无明显功效偏差'}")
print()
print(f"  ── ④ 只看段数最多的那些（功效最高）──")
m = pn >= np.percentile(pn, 75)
print(f"     段数 ≥ {int(pn[m].min())} 的切分：{int(m.sum())} 次")
print(f"     均值差 {pm[m].mean():+.2f}pp   显著 {int(ps[m].sum())} / {int(m.sum())}"
      f"（{ps[m].mean()*100:.0f}%）")
