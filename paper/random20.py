"""
随机 20 个时间段 · 20日 vs 10日（random20.py）
==========================================
随机种子固定，结果可复现。
每月定投 500 元，40% 档，ETHUSDT 永续，含全部成本。
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


def run(i0, V):
    day = DAY[i0].day
    eq, pk, dd, wp = 0.0, 0.0, 0.0, 0.0
    inv = 0.0
    seen = set()
    for i in range(i0, nn):
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
    cny = eq * fx_of(DAY[-1]) * (1 - SPREAD)
    return cny, inv, dd


rng = np.random.default_rng(20261007)
LO, HI = 130, nn - 120                      # 至少留 4 个月
idxs = sorted(rng.integers(LO, HI, 20).tolist())

print("=" * 100)
print("  随机 20 个时间段 · 20日 vs 10日波动窗口 · 每月定投 500 元 · 40% 档")
print("=" * 100)
print()
print(f"  {'#':>2} {'起始日':<12}{'月数':>5}{'总投入':>9}"
      f"{'20日期末':>10}{'收益%':>9}{'回撤':>8}"
      f"{'10日期末':>10}{'收益%':>9}{'回撤':>8}{'差(pp)':>9}{'谁好':>6}")
print("  " + "-" * 94)

D20, D10, DD20, DD10 = [], [], [], []
for k, i0 in enumerate(idxs, 1):
    c20, inv, d20 = run(i0, V20)
    c10, _, d10 = run(i0, V10)
    p20, p10 = (c20 / inv - 1) * 100, (c10 / inv - 1) * 100
    D20.append(p20); D10.append(p10); DD20.append(d20); DD10.append(d10)
    who = "10日" if p10 > p20 else "20日"
    print(f"  {k:>2} {DAY[i0]:%Y-%m-%d}{int(inv/500):>5}{inv:>8,.0f}"
          f"{c20:>10,.0f}{p20:>8.1f}%{d20*100:>7.1f}%"
          f"{c10:>10,.0f}{p10:>8.1f}%{d10*100:>7.1f}%{p10-p20:>+8.1f}{who:>6}")

D20, D10 = np.array(D20), np.array(D10)
dif = D10 - D20
print("  " + "-" * 94)
print(f"  {'平均':<15}{'':>5}{'':>9}{'':>10}{D20.mean():>8.1f}%{np.mean(DD20)*100:>7.1f}%"
      f"{'':>10}{D10.mean():>8.1f}%{np.mean(DD10)*100:>7.1f}%{dif.mean():>+8.1f}")
print(f"  {'中位':<15}{'':>5}{'':>9}{'':>10}{np.median(D20):>8.1f}%"
      f"{np.median(DD20)*100:>7.1f}%{'':>10}{np.median(D10):>8.1f}%"
      f"{np.median(DD10)*100:>7.1f}%{np.median(dif):>+8.1f}")
print(f"  {'最差':<15}{'':>5}{'':>9}{'':>10}{D20.min():>8.1f}%{min(DD20)*100:>7.1f}%"
      f"{'':>10}{D10.min():>8.1f}%{min(DD10)*100:>7.1f}%{dif.min():>+8.1f}")
print(f"  {'最好':<15}{'':>5}{'':>9}{'':>10}{D20.max():>8.1f}%{max(DD20)*100:>7.1f}%"
      f"{'':>10}{D10.max():>8.1f}%{max(DD10)*100:>7.1f}%{dif.max():>+8.1f}")

print()
print("=" * 100)
print("  统计检验")
print("=" * 100)
print()
w = int((dif > 0).sum())
print(f"  10 日更好的次数    {w} / 20")
print(f"  差异均值           {dif.mean():+.1f}pp")
print(f"  差异标准差         {dif.std(ddof=1):.1f}pp")
se = dif.std(ddof=1) / np.sqrt(len(dif))
t = dif.mean() / se
print(f"  配对 t 统计量      {t:+.2f}   （|t| > 2.09 才算 5% 显著，自由度 19）")
print(f"  ⇒ {'✅ 显著' if abs(t) > 2.09 else '❌ 不显著'}")
print()
print(f"  回撤对比：20日 均值 {np.mean(DD20)*100:.1f}%   最差 {min(DD20)*100:.1f}%")
print(f"            10日 均值 {np.mean(DD10)*100:.1f}%   最差 {min(DD10)*100:.1f}%")
print(f"  ⇒ 10 日回撤深 {abs(np.mean(DD10)-np.mean(DD20))*100:.1f}pp")
print()
print(f"  收益/回撤：20日 {D20.mean()/abs(np.mean(DD20)*100):.2f}"
      f"   10日 {D10.mean()/abs(np.mean(DD10)*100):.2f}")
