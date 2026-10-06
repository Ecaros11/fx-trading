"""
波动率窗口扫描（vol_window.py）
============================
① 13 个窗口 × 2 个标的
② 分段稳健性（最好的窗口在每段都好吗）
③ 多重检验校正：13 个窗口里"最好"的应该有多好才不算运气
④ 与"随机窗口"对比
"""
import collections
import datetime as dt
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
TV = 0.40
FEE = 0.0005

WINDOWS = [5, 7, 10, 12, 15, 20, 25, 30, 40, 50, 60, 90, 120]


def load(SYM):
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
    return T, C, r, ma, FR, nn


def volw(r, nn, w):
    v = np.full(nn, np.nan)
    for i in range(w + 1, nn):
        v[i] = r[i - w:i].std(ddof=1) * np.sqrt(365)
    return v


def bt(r, FR, sig, v, a, b):
    eq, pk, dd, wp = 1.0, 1.0, 0.0, 0.0
    xs = []
    for i in range(a + 1, b + 1):
        if sig[i - 1] and np.isfinite(v[i - 1]) and v[i - 1] > 0:
            w = min(3.0, TV / v[i - 1])
        else:
            w = 0.0
        x = w * r[i] - abs(w - wp) * FEE - w * FR[i]
        xs.append(x)
        eq *= (1 + x)
        pk = max(pk, eq)
        dd = min(dd, eq / pk - 1)
        wp = w
    y = np.array(xs)
    sh = y.mean() / y.std(ddof=1) * np.sqrt(365) if y.std() > 0 else 0
    return sh, eq, dd, y


print("=" * 96)
print("  波动率窗口扫描")
print("=" * 96)

OUT = {}
for SYM in ("ETHUSDT", "BTCUSDT"):
    T, C, r, ma, FR, nn = load(SYM)
    sig = np.nan_to_num((C > ma).astype(float))
    I0 = 130
    print()
    print("=" * 96)
    print(f"  {SYM}   {nn} 天  {dt.datetime.fromtimestamp(T[I0]/1000, dt.UTC):%Y-%m-%d}"
          f" ~ {dt.datetime.fromtimestamp(T[-1]/1000, dt.UTC):%Y-%m-%d}")
    print("=" * 96)
    print()
    print(f"  {'窗口':>6}{'夏普':>10}{'年化':>9}{'波动':>9}{'期末':>11}{'回撤':>9}"
          f"{'Δ夏普':>10}{'仓位中位':>10}")
    print("  " + "-" * 76)
    base = None
    for w in WINDOWS:
        v = volw(r, nn, w)
        sh, eq, dd, y = bt(r, FR, sig, v, I0, nn - 1)
        on = [min(3.0, TV / v[i - 1]) for i in range(I0 + 1, nn)
              if sig[i - 1] and np.isfinite(v[i - 1]) and v[i - 1] > 0]
        if w == 20:
            base = sh
        OUT[(SYM, w)] = (sh, eq, dd, y)
        print(f"  {w:>6}{sh:>10.4f}{y.mean()*365*100:>8.1f}%"
              f"{y.std(ddof=1)*np.sqrt(365)*100:>8.1f}%{eq:>10.2f}x"
              f"{dd*100:>8.1f}%{sh-base if base else 0:>+10.4f}"
              f"{np.median(on) if on else 0:>10.3f}")
    # 补 Δ
    print()
    print(f"  （Δ夏普以窗口 20 为基准，基准值 {base:.4f}）")

print()
print("=" * 96)
print("  ① 两个标的最优窗口一致吗")
print("=" * 96)
print()
for SYM in ("ETHUSDT", "BTCUSDT"):
    vals = [(w, OUT[(SYM, w)][0]) for w in WINDOWS]
    vals.sort(key=lambda x: -x[1])
    print(f"  {SYM}  前五名：", end="")
    print("  ".join(f"{w}日({s:.4f})" for w, s in vals[:5]))

print()
print("=" * 96)
print("  ② 多重检验：13 个窗口里最好的那个应该有多好才不算运气")
print("=" * 96)
print()
print("  方法：夏普的标准误 ≈ sqrt((1+sh²/2)/N) × sqrt(365/天数)")
for SYM in ("ETHUSDT", "BTCUSDT"):
    T, C, r, ma, FR, nn = load(SYM)
    n = nn - 130
    sh20 = OUT[(SYM, 20)][0]
    se = np.sqrt((1 + sh20 ** 2 / 2) / n) * np.sqrt(365)
    print(f"  {SYM}  天数 {n}")
    print(f"     窗口 20 的夏普 {sh20:.4f}   标准误 ≈ {se:.4f}")
    print(f"     ⇒ 13 个窗口里最大值的期望 ≈ {sh20 + se*1.7:.4f}"
          f"（13 次独立抽样的极值 ≈ μ + 1.7σ）")
    best = max(OUT[(SYM, w)][0] for w in WINDOWS)
    print(f"     实测最大 {best:.4f}   "
          f"{'⚠️ 在运气范围内' if best < sh20 + se*1.7 else '✅ 超出运气范围'}")

print()
print("=" * 96)
print("  ③ 分段稳健性：每个窗口在三段里的夏普")
print("=" * 96)
print()
for SYM in ("ETHUSDT", "BTCUSDT"):
    T, C, r, ma, FR, nn = load(SYM)
    sig = np.nan_to_num((C > ma).astype(float))
    I0 = 130
    segs = [(I0, I0 + (nn - I0) // 3), (I0 + (nn - I0) // 3, I0 + 2 * (nn - I0) // 3),
            (I0 + 2 * (nn - I0) // 3, nn - 1)]
    print(f"  {SYM}")
    print(f"     {'窗口':>6}{'第1段':>10}{'第2段':>10}{'第3段':>10}{'全段':>10}"
          f"{'三段最小':>10}")
    print("     " + "-" * 56)
    for w in WINDOWS:
        v = volw(r, nn, w)
        ss = [bt(r, FR, sig, v, a, b)[0] for a, b in segs]
        full = OUT[(SYM, w)][0]
        print(f"     {w:>6}{ss[0]:>10.3f}{ss[1]:>10.3f}{ss[2]:>10.3f}"
              f"{full:>10.3f}{min(ss):>10.3f}")
    print()
