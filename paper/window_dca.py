"""
20日 vs 10日波动窗口 · 定投对比（window_dca.py）
==============================================
· 起始日投 500 元，之后每月同日投 500 元
· 目标波动档位固定 40%，只做 ETHUSDT 永续
· 含手续费（5bp/边）、资金费、双边换汇 0.3%
· 汇率用逐年真实值
"""
import collections
import datetime as dt
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
FEE = 0.0005
SPREAD = 0.003
TV = 0.40
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
I0MIN = 130                                   # 保证两个窗口都有值

DATES = ["2022-04-09", "2020-12-17", "2025-09-22", "2023-02-05", "2021-07-30",
         "2024-01-14", "2026-06-11", "2020-05-21", "2022-11-03", "2025-02-18"]


def deposits(i0):
    """起始日投 INIT，之后每月同一天投 MONTHLY"""
    day = DAY[i0].day
    d, seen = {}, set()
    for i in range(i0, nn):
        x = DAY[i]
        if i == i0:
            d[i] = INIT
            seen.add((x.year, x.month))
        elif x.day == day and (x.year, x.month) not in seen:
            d[i] = MONTHLY
            seen.add((x.year, x.month))
    return d


def run(i0, V, dep):
    eq, pk, dd, wp = 0.0, 0.0, 0.0, 0.0
    fee_tot = fund_tot = 0.0
    for i in range(i0, nn):
        if i in dep:
            eq += dep[i] / fx_of(DAY[i]) * (1 - SPREAD)
        if eq <= 0:
            continue
        if sig[i - 1] and np.isfinite(V[i - 1]) and V[i - 1] > 0:
            w = min(3.0, TV / V[i - 1])
        else:
            w = 0.0
        turn = abs(w - wp)
        fee_tot += turn * FEE * eq
        fund_tot += w * FR[i] * eq
        eq *= (1 + w * r[i] - turn * FEE - w * FR[i])
        pk = max(pk, eq)
        dd = min(dd, eq / pk - 1) if pk > 0 else 0.0
        wp = w
    fxe = fx_of(DAY[-1])
    return eq, dd, fee_tot, fund_tot, eq * fxe * (1 - SPREAD)


print("=" * 104)
print("  20 日 vs 10 日波动窗口 · 每月定投 500 元 · 40% 档 · ETHUSDT 永续")
print("  汇率按逐年真实值，双边换汇 0.3%，含手续费和资金费")
print("=" * 104)
print()
print(f"  {'起始日':<12}{'月数':>5}{'总投入':>9}{'入场ETH':>9}{'现价':>8}"
      f"{'20日:期末':>11}{'收益':>9}{'%':>7}{'回撤':>8}"
      f"{'10日:期末':>11}{'收益':>9}{'%':>7}{'回撤':>8}{'谁好':>6}")
print("  " + "-" * 100)

SUM = {"d20": [], "d10": []}
rows = []
for ds in DATES:
    tg = dt.datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000
    i0 = int(np.argmin(np.abs(T - tg)))
    if i0 < I0MIN:
        print(f"  {ds:<12}  数据不足（需 ≥ {DAY[I0MIN]:%Y-%m-%d}）")
        continue
    dep = deposits(i0)
    inv = sum(dep.values())
    e20, dd20, f20, u20, c20 = run(i0, V20, dep)
    e10, dd10, f10, u10, c10 = run(i0, V10, dep)
    p20, p10 = c20 - inv, c10 - inv
    r20, r10 = p20 / inv * 100, p10 / inv * 100
    SUM["d20"].append(r20)
    SUM["d10"].append(r10)
    rows.append((ds, inv, p20, p10))
    who = "10日" if r10 > r20 else "20日"
    print(f"  {DAY[i0]:%Y-%m-%d}{len(dep):>5}{inv:>8,.0f}{C[i0]:>9,.0f}"
          f"{C[-1]:>8,.0f}{c20:>10,.0f}{p20:>+9,.0f}{r20:>6.1f}%{dd20*100:>7.1f}%"
          f"{c10:>10,.0f}{p10:>+9,.0f}{r10:>6.1f}%{dd10*100:>7.1f}%{who:>6}")

print("  " + "-" * 100)
print(f"  {'平均':<12}{'':>5}{'':>9}{'':>9}{'':>8}"
      f"{'':>11}{'':>9}{np.mean(SUM['d20']):>6.1f}%{'':>8}"
      f"{'':>11}{'':>9}{np.mean(SUM['d10']):>6.1f}%")
print(f"  {'中位':<12}{'':>5}{'':>9}{'':>9}{'':>8}"
      f"{'':>11}{'':>9}{np.median(SUM['d20']):>6.1f}%{'':>8}"
      f"{'':>11}{'':>9}{np.median(SUM['d10']):>6.1f}%")
print(f"  {'10日更好':<12}  {sum(1 for a,b in zip(SUM['d20'],SUM['d10']) if b>a)} / "
      f"{len(SUM['d20'])} 次")
print(f"  {'最差':<12}{'':>5}{'':>9}{'':>9}{'':>8}"
      f"{'':>11}{'':>9}{min(SUM['d20']):>6.1f}%{'':>8}"
      f"{'':>11}{'':>9}{min(SUM['d10']):>6.1f}%")
