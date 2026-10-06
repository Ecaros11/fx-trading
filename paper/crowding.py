"""
拥挤度 + 剩余增强空间（crowding.py）
==================================
① 滚动夏普：边缘在衰减吗？（这是"拥挤"的直接检验）
② 波动率估计：EWMA / 不同窗口能不能改善
③ 成本敏感度：maker 费率能省多少
④ 分段胜率：趋势跟踪的收益是不是集中在少数年份
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
TV = 0.40

j = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
T = np.array([b["t"] for b in j], float)
C = np.array([b["c"] for b in j], float)
nn = len(C)
r = np.zeros(nn)
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(nn, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
fr = json.loads((ROOT / "data" / "funding" / f"{SYM}.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
fday = {int(k): float(np.sum(v)) for k, v in agg.items()}
cd = np.array([int(t // 86400000) for t in T])
FR = np.nan_to_num(np.array([fday.get(int(d), np.nan) for d in cd]))
DAY = np.array([int(t // 86400000) for t in T], int)
sig = np.nan_to_num((C > ma).astype(float))


def vol_simple(w):
    v = np.full(nn, np.nan)
    for i in range(w + 1, nn):
        v[i] = r[i - w:i].std(ddof=1) * np.sqrt(365)
    return v


def vol_ewma(halflife):
    lam = 0.5 ** (1 / halflife)
    v = np.zeros(nn)
    v[0] = r[:20].std(ddof=1)
    for i in range(1, nn):
        v[i] = np.sqrt(lam * v[i - 1] ** 2 + (1 - lam) * r[i] ** 2)
    return v * np.sqrt(365)


def run(v, fee=0.0005, sig_=None):
    s = sig if sig_ is None else sig_
    eq, pk, dd, wp = 1000.0, 1000.0, 0.0, 0.0
    x = np.zeros(nn)
    for i in range(61, nn):
        if s[i - 1] and np.isfinite(v[i - 1]) and v[i - 1] > 0:
            w = min(3.0, TV / v[i - 1])
        else:
            w = 0.0
        x[i] = w * r[i] - abs(w - wp) * fee - w * FR[i]
        eq *= (1 + x[i])
        pk = max(pk, eq)
        dd = min(dd, eq / pk - 1)
        wp = w
    y = x[61:]
    sh = y.mean() / y.std(ddof=1) * np.sqrt(365) if y.std() > 0 else 0
    return sh, eq, dd, y


print("=" * 92)
print("  ① 滚动夏普（2 年窗口）—— 边缘在衰减吗？")
print("=" * 92)
print()
v20 = vol_simple(20)
_, _, _, y = run(v20)
W = 365 * 2
print(f"  {'窗口':<24}{'夏普':>10}{'年化':>10}")
print("  " + "-" * 44)
step = 365 // 2
dates = [dt.datetime.fromtimestamp(T[i] / 1000, dt.UTC) for i in range(61, nn)]
for a in range(0, len(y) - W, step):
    seg = y[a:a + W]
    sh = seg.mean() / seg.std(ddof=1) * np.sqrt(365)
    print(f"  {dates[a]:%Y-%m} ~ {dates[min(a+W,len(y)-1)]:%Y-%m}      "
          f"{sh:>8.3f}{seg.mean()*365*100:>9.1f}%")

# 前半 vs 后半
h = len(y) // 2
sh1 = y[:h].mean() / y[:h].std(ddof=1) * np.sqrt(365)
sh2 = y[h:].mean() / y[h:].std(ddof=1) * np.sqrt(365)
print()
print(f"  前半段（{dates[0]:%Y-%m} ~ {dates[h]:%Y-%m}）  夏普 {sh1:.4f}")
print(f"  后半段（{dates[h]:%Y-%m} ~ {dates[-1]:%Y-%m}）  夏普 {sh2:.4f}")
print(f"  ⇒ {'⚠️ 后半段更差 ⇒ 有衰减迹象' if sh2 < sh1 - 0.15 else '✅ 没有明显衰减'}")

print()
print("=" * 92)
print("  ② 波动率估计：能不能改善夏普")
print("=" * 92)
print()
print(f"  {'估计方法':<28}{'夏普':>10}{'Δ':>10}{'回撤':>10}")
print("  " + "-" * 58)
bsh, beq, bdd, _ = run(v20)
base = bsh
print(f"  {'简单 20 日（现用）':<28}{bsh:>10.4f}{0.0:>+10.4f}{bdd*100:>9.1f}%")
for lab, v in (("简单 10 日", vol_simple(10)),
               ("简单 30 日", vol_simple(30)),
               ("简单 60 日", vol_simple(60)),
               ("EWMA 半衰期 5", vol_ewma(5)),
               ("EWMA 半衰期 10", vol_ewma(10)),
               ("EWMA 半衰期 20", vol_ewma(20)),
               ("EWMA 半衰期 40", vol_ewma(40))):
    sh, eq, dd, _ = run(v)
    mark = "  ✅" if sh > base + 0.01 else ("  ⚠️" if sh < base - 0.01 else "  ≈")
    print(f"  {lab:<28}{sh:>10.4f}{sh-base:>+10.4f}{dd*100:>9.1f}%{mark}")

print()
print("=" * 92)
print("  ③ 成本敏感度")
print("=" * 92)
print()
print(f"  {'费率（单边）':<24}{'夏普':>10}{'期末':>12}{'Δ夏普':>10}")
print("  " + "-" * 56)
for lab, f in (("0（理论）", 0.0), ("maker 0.02%", 0.0002),
               ("现用 taker 0.05%", 0.0005),
               ("VIP taker 0.04%", 0.0004), ("0.10%（更差）", 0.0010)):
    sh, eq, dd, _ = run(v20, fee=f)
    print(f"  {lab:<24}{sh:>10.4f}{eq:>11,.0f}{sh-bsh:>+10.4f}")

print()
print("=" * 92)
print("  ④ 逐年表现（趋势跟踪是不是集中在少数年份）")
print("=" * 92)
print()
print(f"  {'年份':<8}{'策略':>12}{'买入持有':>13}{'差':>12}{'在场天数':>10}")
print("  " + "-" * 56)
byyear = collections.OrderedDict()
for i in range(61, nn):
    yy = dt.datetime.fromtimestamp(T[i] / 1000, dt.UTC).year
    byyear.setdefault(yy, []).append(i)
for yy, idxs in byyear.items():
    if len(idxs) < 60:
        continue
    xs, bh = [], 1.0
    for i in idxs:
        w = min(3.0, TV / v20[i - 1]) if (sig[i - 1] and np.isfinite(v20[i - 1])
                                          and v20[i - 1] > 0) else 0.0
        xs.append(w * r[i] - w * FR[i])
        bh *= (1 + r[i])
    e = np.prod([1 + x for x in xs])
    on = sum(1 for i in idxs if sig[i - 1])
    print(f"  {yy:<8}{(e-1)*100:>+11.1f}%{(bh-1)*100:>+12.1f}%"
          f"{(e-bh)*100:>+11.1f}pp{on:>10}")
