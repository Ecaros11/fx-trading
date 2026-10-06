"""
正确的方法：整段数据 + HAC 标准误 + 块自助法（correct_method.py）
=============================================================
三种互补的检验：
  ① 日收益差序列 d[t]，用 Newey-West (HAC) 标准误检验 H0: E[d]=0
  ② 块自助法（block bootstrap）：重采样 d[t] 的连续块，给出 E[d] 的 CI
  ③ 对定投终值做块自助法：重采样价格路径，跑 DCA，给出终值差的 CI
"""
import collections
import datetime as dt
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
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


def series(V):
    """返回逐日收益序列（含成本）"""
    x = np.zeros(nn)
    wp = 0.0
    for i in range(61, nn):
        if sig[i - 1] and np.isfinite(V[i - 1]) and V[i - 1] > 0:
            w = min(3.0, TV / V[i - 1])
        else:
            w = 0.0
        x[i] = w * r[i] - abs(w - wp) * FEE - w * FR[i]
        wp = w
    return x[61:]


X20, X10 = series(volw(20)), series(volw(10))
d = X10 - X20
n = len(d)
print("=" * 90)
print("  ① 日收益差序列 d[t] = x10[t] − x20[t]")
print("=" * 90)
print()
print(f"  观测数        {n} 天（{n/365:.2f} 年）")
print(f"  日均差        {d.mean()*1e4:+.4f} bp/天")
print(f"  日标准差      {d.std(ddof=1)*1e4:.2f} bp")
print(f"  年化差        {d.mean()*365*100:+.2f} %/年")
print()

# Newey-West HAC 标准误
def nw_se(x, lags):
    x = x - x.mean()
    n_ = len(x)
    g0 = (x @ x) / n_
    s = g0
    for k in range(1, lags + 1):
        gk = (x[k:] @ x[:-k]) / n_
        w = 1 - k / (lags + 1)          # Bartlett 核
        s += 2 * w * gk
    return np.sqrt(s / n_)

print("  ── Newey-West (HAC) 标准误 ──")
print()
print(f"  {'滞后阶数':>10}{'标准误(bp/天)':>16}{'t 统计量':>12}{'判定':>10}")
print("  " + "-" * 50)
for L in (0, 5, 10, 20, 40, 60, 90, 120):
    se = nw_se(d, L)
    t = d.mean() / se
    mark = "✅ 显著" if abs(t) > 1.96 else "❌ 不显著"
    print(f"  {L:>10}{se*1e4:>16.4f}{t:>12.2f}{mark:>10}")
print()
print("  （滞后 0 = 普通标准误；真实值应取与自相关长度匹配的滞后）")

# 自相关长度
ac = [np.corrcoef(d[:-k], d[k:])[0, 1] for k in range(1, 61)]
first_neg = next((i + 1 for i, v in enumerate(ac) if v < 0), 60)
print(f"  自相关首次转负的滞后 = {first_neg} 天   （这附近是合适的滞后阶数）")

print()
print("=" * 90)
print("  ② 块自助法：重采样 d[t] 的连续块")
print("=" * 90)
print()
rng = np.random.default_rng(20261007)
for blk in (20, 50, 100, 200):
    nb = int(np.ceil(n / blk))
    ms = []
    for _ in range(4000):
        idx = []
        for _ in range(nb):
            s = rng.integers(0, n - blk)
            idx.append(np.arange(s, s + blk))
        ii = np.concatenate(idx)[:n]
        ms.append(d[ii].mean() * 365 * 100)
    ms = np.array(ms)
    lo, hi = np.percentile(ms, [2.5, 97.5])
    print(f"  块长 {blk:>3} 天：年化差 95% CI [{lo:+.2f}, {hi:+.2f}] %/年"
          f"   {'✅ 不含0' if lo > 0 else '❌ 含0'}")

print()
print("=" * 90)
print("  ③ 块自助法：重采样【价格路径】再跑定投")
print("=" * 90)
print()


def run_dca(Cp, Vp):
    """给定价格序列和波动序列，跑定投，返回收益率"""
    n_ = len(Cp)
    rp = np.zeros(n_)
    rp[1:] = Cp[1:] / Cp[:-1] - 1
    day0 = DAY[min(61, len(DAY) - 1)].day
    eq, inv, wp = 0.0, 0.0, 0.0
    seen = set()
    for i in range(61, n_):
        if i == 61:
            eq += INIT / 7.15; inv += INIT
            seen.add((0, 0))
        elif i % 30 == 0:
            eq += MONTHLY / 7.15; inv += MONTHLY
        if eq <= 0:
            continue
        if sig[i - 1] and np.isfinite(Vp[i - 1]) and Vp[i - 1] > 0:
            w = min(3.0, TV / Vp[i - 1])
        else:
            w = 0.0
        eq *= (1 + w * rp[i] - abs(w - wp) * FEE - w * FR[i])
        wp = w
    return (eq / inv - 1) * 100


# 用真实的 20/10 序列跑一次基准
V20, V10 = volw(20), volw(10)
b20 = run_dca(C, V20)
b10 = run_dca(C, V10)
print(f"  真实数据：20日 {b20:.1f}%   10日 {b10:.1f}%   差 {b10-b20:+.1f}pp")
print()
print("  （块自助法重采样价格路径 —— 这会破坏日历连续性，仅作分布参考）")
for blk in (100, 200, 400):
    diffs = []
    for _ in range(300):
        idx = []
        nb = int(np.ceil(nn / blk))
        for _ in range(nb):
            s = rng.integers(0, nn - blk)
            idx.append(np.arange(s, s + blk))
        ii = np.concatenate(idx)[:nn]
        Cp = C[ii]
        try:
            a = run_dca(Cp, V20)
            b = run_dca(Cp, V10)
            diffs.append(b - a)
        except Exception:
            pass
    diffs = np.array(diffs)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    print(f"  块长 {blk:>3}：终值差 95% CI [{lo:+.1f}, {hi:+.1f}]pp"
          f"   10日更好 {int((diffs>0).mean()*100)}%")
