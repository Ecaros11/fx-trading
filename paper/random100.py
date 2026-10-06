"""
100 个起止都随机的窗口 · 20日 vs 10日（random300.py）
==================================================
· 起始日和结束日都随机
· 最小窗口 180 天（6 个月，约 6 次定投）
· 每月定投 500 元，40% 档，ETHUSDT 永续，含全部成本
· 同时报告窗口重叠度，以判断 t 值的可信度
"""
import collections
import datetime as dt
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
FEE, SPREAD, TV = 0.0005, 0.003, 0.40
INIT = MONTHLY = 500.0
MIN_DAYS = 180
N = 300
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
I0MIN = 130


def run(i0, i1, V):
    """从 i0 到 i1，每月同日投 500"""
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
    cny = eq * fx_of(DAY[i1]) * (1 - SPREAD)
    return cny, inv, dd


rng = np.random.default_rng(20261007)
pairs = []
for _ in range(N):
    a = int(rng.integers(I0MIN, nn - MIN_DAYS))
    b = int(rng.integers(a + MIN_DAYS, nn))
    pairs.append((a, b))

print("=" * 104)
print(f"  {N} 个起止都随机的窗口 · 20日 vs 10日 · 每月 500 元 · 40% 档")
print(f"  最小窗口 {MIN_DAYS} 天（约 {MIN_DAYS//30} 次定投），实际数据 {nn} 天")
print("=" * 104)
print()
print(f"  {'#':>3} {'起始':<11}{'结束':<11}{'天':>5}{'投入':>8}"
      f"{'20日%':>9}{'回撤':>7}{'10日%':>9}{'回撤':>7}{'差pp':>8}{'谁好':>5}")
print("  " + "-" * 96)

D20, D10, DD20, DD10, LEN = [], [], [], [], []
for k, (a, b) in enumerate(pairs, 1):
    c20, inv, d20 = run(a, b, V20)
    c10, _, d10 = run(a, b, V10)
    p20, p10 = (c20 / inv - 1) * 100, (c10 / inv - 1) * 100
    D20.append(p20); D10.append(p10); DD20.append(d20); DD10.append(d10)
    LEN.append(b - a)
    if k <= 12 or k % 25 == 0:
        who = "10日" if p10 > p20 else "20日"
        print(f"  {k:>3} {DAY[a]:%Y-%m-%d} {DAY[b]:%Y-%m-%d}{b-a:>5}{inv:>7,.0f}"
              f"{p20:>8.1f}%{d20*100:>6.1f}%{p10:>8.1f}%{d10*100:>6.1f}%"
              f"{p10-p20:>+7.1f}{who:>5}")
print(f"  ... （共 {N} 个，只显示前 12 个和每 25 个）")

D20, D10 = np.array(D20), np.array(D10)
dif = D10 - D20
LEN = np.array(LEN)

print("  " + "-" * 96)
print()
print("=" * 104)
print("  汇总")
print("=" * 104)
print()
print(f"  {'':<16}{'20日':>14}{'10日':>14}{'差':>12}")
print("  " + "-" * 58)
print(f"  {'平均收益':<16}{D20.mean():>13.1f}%{D10.mean():>13.1f}%{dif.mean():>+11.1f}pp")
print(f"  {'中位收益':<16}{np.median(D20):>13.1f}%{np.median(D10):>13.1f}%"
      f"{np.median(dif):>+11.1f}pp")
print(f"  {'最差收益':<16}{D20.min():>13.1f}%{D10.min():>13.1f}%{dif.min():>+11.1f}pp")
print(f"  {'最好收益':<16}{D20.max():>13.1f}%{D10.max():>13.1f}%{dif.max():>+11.1f}pp")
print(f"  {'平均回撤':<16}{np.mean(DD20)*100:>13.1f}%{np.mean(DD10)*100:>13.1f}%"
      f"{(np.mean(DD10)-np.mean(DD20))*100:>+11.1f}pp")
print(f"  {'最差回撤':<16}{min(DD20)*100:>13.1f}%{min(DD10)*100:>13.1f}%"
      f"{(min(DD10)-min(DD20))*100:>+11.1f}pp")
print(f"  {'收益/回撤':<16}{D20.mean()/abs(np.mean(DD20)*100):>14.2f}"
      f"{D10.mean()/abs(np.mean(DD10)*100):>14.2f}")

print()
print("=" * 104)
print("  统计检验")
print("=" * 104)
print()
w = int((dif > 0).sum())
print(f"  10 日更好的次数    {w} / {N}")
print(f"  差异均值           {dif.mean():+.1f}pp")
print(f"  差异标准差         {dif.std(ddof=1):.1f}pp")
se = dif.std(ddof=1) / np.sqrt(N)
t = dif.mean() / se
print(f"  配对 t 统计量      {t:+.2f}   （|t| > 1.98 才算 5% 显著，自由度 {N-1}）")
print(f"  ⇒ {'✅ 显著' if abs(t) > 1.98 else '❌ 不显著'}")

# 重叠度
print()
print("  窗口重叠度（判断 t 值可信度）：")
tot = np.zeros(nn, bool)
sel = np.zeros(nn, int)
for a, b in pairs:
    tot[a:b + 1] = True
    sel[a:b + 1] += 1
print(f"     被覆盖的天数    {int(tot.sum())} / {nn}")
print(f"     平均每天被几个窗口覆盖  {sel[tot].mean():.1f}")
print(f"     窗口平均长度    {LEN.mean():.0f} 天")
print(f"     ⇒ 总时间跨度 {int(tot.sum())} 天 / 平均窗口 {LEN.mean():.0f} 天"
      f" ≈ {tot.sum()/LEN.mean():.1f} 个独立样本")

# 差异 vs 窗口长度
c = np.corrcoef(LEN, dif)[0, 1]
print()
print(f"  差异 vs 窗口长度的相关   {c:+.3f}")
print(f"  ⇒ {'差异主要由窗口长度（复利）解释' if c > 0.8 else '差异与长度关系不大'}")

# 只看长窗口（> 1 年）
m = LEN > 365
if m.sum() > 5:
    print()
    print(f"  只算窗口 > 1 年的（{int(m.sum())} 个）：")
    print(f"     20日 均值 {D20[m].mean():.1f}%   10日 均值 {D10[m].mean():.1f}%"
          f"   差 {dif[m].mean():+.1f}pp")
    print(f"     10日更好 {int((dif[m]>0).sum())} / {int(m.sum())}")
