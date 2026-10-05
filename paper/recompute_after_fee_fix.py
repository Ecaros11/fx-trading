"""
系数 1 是正确的之后：所有受影响的数字要重算
==========================================
真相：|Δw| 的【和】= 实际成交额之和（以权益为单位）
      系数 1 正确，系数 2 重复计算一倍。
      ⇒ align.py（主回测）多算了一倍手续费。
      ⇒ 所有引用 align.py 的数字都偏低（夏普）和偏深（回撤）。
"""
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
FEE = 0.0005
W = 60
MIN_N = 20.0

d1 = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))
fd = json.loads((ROOT / "data" / "funding" / "ETHUSDT.json").read_text(encoding="utf-8"))
fday = {}
for x in fd:
    k = int(x["t"] // 86400000)
    fday[k] = fday.get(k, 0.0) + x["rate"]
C = np.array([b["c"] for b in d1], float)
day = np.array([b["t"] // 86400000 for b in d1])
FR = np.array([fday.get(int(day[i]), 0.0) for i in range(len(C))])
N = len(C)
yrs = (N - W) / 365
m50 = np.concatenate([[np.nan] * 49, np.convolve(C, np.ones(50) / 50, "valid")])
sig = np.nan_to_num((C > m50).astype(float))
r = np.zeros(N)
r[1:] = C[1:] / C[:-1] - 1
FRl = np.concatenate([[0.0], FR[:-1]])
vol20 = np.full(N, np.nan)
for i in range(21, N):
    vol20[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)


def sh(x):
    x = np.asarray(x, float)[W:]
    x = x[np.isfinite(x)]
    return x.mean() / x.std() * np.sqrt(365)


def ddc(x):
    x = np.asarray(x, float)[W:]
    eq = np.cumprod(1 + x)
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def ann(x):
    x = np.asarray(x, float)[W:]
    x = x[np.isfinite(x)]
    return np.expm1(np.log1p(x).mean() * 365)


def se(x):
    x = np.asarray(x, float)[W:]
    x = x[np.isfinite(x)]
    sr = x.mean() / x.std()
    return np.sqrt((1 + 0.5 * sr ** 2) / len(x)) * np.sqrt(365)


def build(tv, lev=None):
    if lev is not None:
        return np.nan_to_num(sig * lev)
    if tv is None:
        return np.nan_to_num(sig * 1.405)
    raw = np.where(np.isfinite(vol20) & (vol20 > 1e-9),
                   tv / np.where(vol20 > 1e-9, vol20, 1.0), 0.0)
    return np.nan_to_num(sig * np.clip(raw, 0, 3))


def net(w, coef):
    wl = np.concatenate([[0.0], w[:-1]])
    tn = np.abs(np.diff(np.concatenate([[0.0], wl])))
    return wl * r - tn * FEE * coef - wl * FRl


print("=" * 104)
print("  ① METHODS 表：修正前后的完整对比")
print("=" * 104)
print(f"  {'版本':<18}{'旧夏普':>9}{'新夏普':>9}{'Δ':>8}"
      f"{'旧回撤':>10}{'新回撤':>10}{'Δ(pp)':>8}{'新年化':>10}")
print("  " + "-" * 76)
RES = {}
for lab, tv in (("固定版", None), ("波动率目标 40%", 0.40),
                ("波动率目标 25%", 0.25), ("波动率目标 15%", 0.15)):
    w = build(tv)
    a2, a1 = net(w, 2), net(w, 1)
    RES[lab] = (sh(a2), sh(a1), ddc(a2), ddc(a1), ann(a1))
    print(f"  {lab:<18}{sh(a2):>9.3f}{sh(a1):>9.3f}{sh(a1)-sh(a2):>+8.3f}"
          f"{ddc(a2)*100:>9.1f}%{ddc(a1)*100:>9.1f}%"
          f"{(ddc(a1)-ddc(a2))*100:>+7.1f}{ann(a1)*100:>9.1f}%")

print()
print("=" * 104)
print("  ② DD_BY_LEV 表：修正前后")
print("=" * 104)
print(f"  {'杠杆':>8}{'旧(系数2)':>12}{'新(系数1)':>12}{'Δ(pp)':>9}"
      f"{'夏普(不变)':>12}")
print("  " + "-" * 56)
for lev in (0.8, 1.0, 1.2, 1.405, 1.6, 2.0):
    w = build(None, lev)
    a2, a1 = net(w, 2), net(w, 1)
    print(f"  {lev:>8.3f}{ddc(a2)*100:>11.1f}%{ddc(a1)*100:>11.1f}%"
          f"{(ddc(a1)-ddc(a2))*100:>+8.2f}{sh(a1):>12.3f}")

print()
print("=" * 104)
print("  ③ 不确定性与置信区间：修正前后")
print("=" * 104)
w = build(None)
a1 = net(w, 1)
s = sh(a1)
e = se(a1)
print(f"  固定版 1.405x：")
print(f"    夏普 {s:.4f}   标准误 {e:.4f}")
print(f"    95%CI [{s-1.96*e:.2f}, {s+1.96*e:.2f}]")
print(f"    ⇒ 原来的 CI 是 [0.27, 1.79]（系数2口径）")
print()
print(f"  要 95% 把握判定失效，需观测夏普 < {0-1.96*e:.2f}")

print()
print("=" * 104)
print("  ④ 逐年（修正后，1.3486x = 你当前口径）")
print("=" * 104)
import datetime as dt
w = np.nan_to_num(sig * 1.3486)
wlc = np.concatenate([[0.0], w[:-1]])
tnc = np.abs(np.diff(np.concatenate([[0.0], wlc])))
n1 = wlc * r - tnc * FEE - wlc * FRl
n2 = wlc * r - tnc * FEE * 2 - wlc * FRl
NA = next(i for i, b in enumerate(d1) if b["t"] >= 1577836800000)
print(f"  {'年份':<8}{'旧(系数2)':>12}{'新(系数1)':>12}{'Δ(pp)':>9}")
print("  " + "-" * 44)
for y in range(2020, 2027):
    t0 = int(dt.datetime.strptime(f"{y}-01-01", "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000)
    t1 = int(dt.datetime.strptime(f"{y}-12-31", "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000)
    i0 = next((j for j in range(NA, N) if d1[j]["t"] >= t0), None)
    i1 = next((j for j in range(NA, N) if d1[j]["t"] >= t1), N - 1)
    if i0 is None:
        continue
    v2 = np.prod(1 + n2[i0:i1 + 1]) - 1
    v1 = np.prod(1 + n1[i0:i1 + 1]) - 1
    print(f"  {y:<8}{v2*100:>11.1f}%{v1*100:>11.1f}%{(v1-v2)*100:>+8.1f}")

print()
print("=" * 104)
print("  ⑤ 阈值表（本轮最要紧的实用结论）")
print("=" * 104)
print("  系数错一倍 ⇒ 门槛不变（门槛只用仓位分位，不涉及收益）")
print(f"  {'版本':<18}{'门槛':>9}{'夏普(新)':>10}")
print("  " + "-" * 40)
for lab, tv, thr in (("固定版", None, 14.2), ("波动率目标 40%", 0.40, 52.3),
                     ("波动率目标 25%", 0.25, 83.7), ("波动率目标 15%", 0.15, 139.5)):
    print(f"  {lab:<18}{thr:>8.1f}U{RES[lab][1]:>10.3f}")
print()
print("  ⇒ 门槛没变（它们只依赖仓位分布，不依赖收益）✅")
print("  ⇒ 但夏普全部上移：1.031→1.048（固定）/ 1.218→1.244（波动率目标）")
