"""
10日 vs 20日 在各档位下都成立吗（window_by_tier.py）
=================================================
如果只在 40% 档成立 ⇒ 可疑
如果所有档位都成立 ⇒ 强证据
"""
import collections
import datetime as dt
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
FEE = 0.0005

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


def volw(w):
    v = np.full(nn, np.nan)
    for i in range(w + 1, nn):
        v[i] = r[i - w:i].std(ddof=1) * np.sqrt(365)
    return v


def series(V, tv):
    x = np.zeros(nn)
    wp = 0.0
    for i in range(61, nn):
        if sig[i - 1] and np.isfinite(V[i - 1]) and V[i - 1] > 0:
            w = min(3.0, tv / V[i - 1])
        else:
            w = 0.0
        x[i] = w * r[i] - abs(w - wp) * FEE - w * FR[i]
        wp = w
    return x[61:]


def nw_t(d, lags=10):
    x = d - d.mean()
    n_ = len(x)
    s = (x @ x) / n_
    for k in range(1, lags + 1):
        gk = (x[k:] @ x[:-k]) / n_
        s += 2 * (1 - k / (lags + 1)) * gk
    se = np.sqrt(s / n_)
    return d.mean() / se, se


print("=" * 94)
print("  10 日 vs 20 日 —— 各目标波动档位下的检验")
print("=" * 94)
print()
print(f"  {'档位':>7}{'20日夏普':>11}{'10日夏普':>11}{'Δ夏普':>10}"
      f"{'20日年化':>11}{'10日年化':>11}{'年化差':>10}{'HAC t':>9}{'判定':>9}")
print("  " + "-" * 89)

V20, V10 = volw(20), volw(10)
for tv in (0.10, 0.15, 0.25, 0.40, 0.60, 0.80):
    x20 = series(V20, tv)
    x10 = series(V10, tv)
    sh20 = x20.mean() / x20.std(ddof=1) * np.sqrt(365)
    sh10 = x10.mean() / x10.std(ddof=1) * np.sqrt(365)
    a20 = x20.mean() * 365 * 100
    a10 = x10.mean() * 365 * 100
    t, se = nw_t(x10 - x20)
    mark = "✅ 显著" if abs(t) > 1.96 else ("≈ 边缘" if abs(t) > 1.6 else "❌ 不显著")
    print(f"  {tv*100:>6.0f}%{sh20:>11.4f}{sh10:>11.4f}{sh10-sh20:>+10.4f}"
          f"{a20:>10.1f}%{a10:>10.1f}%{a10-a20:>+9.1f}pp{t:>+9.2f}{mark:>9}")

print()
print("=" * 94)
print("  回撤对比（各档位）")
print("=" * 94)
print()
print(f"  {'档位':>7}{'20日回撤':>11}{'10日回撤':>11}{'差':>10}")
print("  " + "-" * 42)
for tv in (0.10, 0.15, 0.25, 0.40, 0.60, 0.80):
    dd = []
    for V in (V20, V10):
        eq, pk, d_ = 1.0, 1.0, 0.0
        wp = 0.0
        for i in range(61, nn):
            if sig[i - 1] and np.isfinite(V[i - 1]) and V[i - 1] > 0:
                w = min(3.0, tv / V[i - 1])
            else:
                w = 0.0
            eq *= (1 + w * r[i] - abs(w - wp) * FEE - w * FR[i])
            pk = max(pk, eq)
            d_ = min(d_, eq / pk - 1)
            wp = w
        dd.append(d_ * 100)
    print(f"  {tv*100:>6.0f}%{dd[0]:>10.1f}%{dd[1]:>10.1f}%{dd[1]-dd[0]:>+9.1f}pp")

print()
print("=" * 94)
print("  多窗口一起看（40% 档）：只有 10 日特殊，还是越快越好？")
print("=" * 94)
print()
print(f"  {'窗口':>6}{'夏普':>10}{'年化':>10}{'vs 20日 t':>12}{'判定':>10}")
print("  " + "-" * 50)
b = series(V20, 0.40)
for w in (5, 7, 8, 10, 12, 15, 20, 25, 30):
    xw = series(volw(w), 0.40)
    sh = xw.mean() / xw.std(ddof=1) * np.sqrt(365)
    t, _ = nw_t(xw - b)
    mark = "✅" if abs(t) > 1.96 else ("≈" if abs(t) > 1.6 else "—")
    print(f"  {w:>6}{sh:>10.4f}{xw.mean()*365*100:>9.1f}%{t:>+12.2f}{mark:>10}")
