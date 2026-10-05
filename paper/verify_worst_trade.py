"""
核对 WORST_TRADE_LOW = -0.266 / WORST_TRADE_CLOSE = -0.245
=======================================================
我上一轮算的是 −23.0%（low）/ −9.2%（close）。用户写 −26.6% / −24.5%。

差异可能来自"持有期"的定义：
  A. 我的定义：入场 = sig[i]=1 且 sig[i-1]=0 的那根【收盘】
               出场 = 之后第一个 sig=0 的那根【收盘】
               期间 = (i, exit]  —— 不含入场那根自己
  B. 用户的定义（注释写"持有期 = 次日到信号结束的次一日"）
               期间 = [i+1, exit+1] —— 多含出入场后一天
  C. 另一种：入场那根自己的 low 也算进去（但信号在收盘才出，不能算）

还有一种：把【信号持续期间】整个算进去，包括最后一根（跌破 MA50 那根）的 low。
"""
import datetime as dt
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from align import sma

ROOT = pathlib.Path(__file__).parent.parent
d1 = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))
C = np.array([b["c"] for b in d1], float)
H = np.array([b["h"] for b in d1], float)
L = np.array([b["l"] for b in d1], float)
T = np.array([b["t"] for b in d1], float)
N = len(C)
ma50 = sma(C, 50)
sig = np.nan_to_num((C > ma50).astype(float))
W = 60


def collect(mode):
    """按不同口径收集每笔交易的 (入场下标, 出场下标, 最坏逆向)"""
    out = []
    i = W
    while i < N - 1:
        if sig[i] == 1 and (i == 0 or sig[i - 1] == 0):
            j = i + 1
            while j < N and sig[j] == 1:
                j += 1
            exit_i = min(j, N - 1)
            entry = C[i]
            if mode == "A":       # 我的口径：(i, exit]，入场那根不算
                lo, hi = i + 1, exit_i + 1
            elif mode == "B":     # 再多含出场后一天
                lo, hi = i + 1, min(exit_i + 2, N)
            elif mode == "C":     # 含入场那根自己（信号在收盘出，理论上不能算）
                lo, hi = i, exit_i + 1
            elif mode == "D":     # 从入场次日到出场日之后一直算到下一笔入场
                nxt = exit_i
                while nxt < N and sig[nxt] == 0:
                    nxt += 1
                lo, hi = i + 1, min(nxt, N)
            lows = L[lo:hi]
            closes = C[lo:hi]
            if len(lows):
                out.append((i, exit_i, entry,
                            lows.min() / entry - 1, closes.min() / entry - 1, mode))
            i = exit_i
        else:
            i += 1
    return out


print("=" * 96)
print("  四种口径下的「单笔最大逆向」")
print("=" * 96)
print(f"  {'口径':<6}{'笔数':>6}{'最差(low)':>12}{'最差(close)':>13}  说明")
print("  " + "-" * 74)
LAB = {"A": "入场次根 → 出场根（我的口径）",
       "B": "入场次根 → 出场后一根",
       "C": "入场根 → 出场根（含入场那根自己）",
       "D": "入场次根 → 下一次入场前"}
res = {}
for m in ("A", "B", "C", "D"):
    tr = collect(m)
    wl = min(t[3] for t in tr)
    wc = min(t[4] for t in tr)
    res[m] = (wl, wc, tr)
    print(f"  {m:<6}{len(tr):>6}{wl*100:>11.1f}%{wc*100:>12.1f}%  {LAB[m]}")

print()
print(f"  用户写的:  WORST_TRADE_LOW = -26.6%   WORST_TRADE_CLOSE = -24.5%")
print()
for m in ("A", "B", "C", "D"):
    wl, wc, _ = res[m]
    hit_l = "✅" if abs(wl - (-0.266)) < 0.01 else "  "
    hit_c = "✅" if abs(wc - (-0.245)) < 0.01 else "  "
    print(f"    口径 {m}:  low {wl*100:>6.1f}% {hit_l}   close {wc*100:>6.1f}% {hit_c}")

print()
print("=" * 96)
print("  最差那几笔（口径 A 和 D 各列一下）")
print("=" * 96)
for m in ("A", "D"):
    _, _, tr = res[m]
    print(f"  口径 {m}:")
    print(f"    {'入场':<12}{'出场':<12}{'入场价':>10}{'最坏low':>10}{'最坏close':>11}{'天数':>7}")
    print("    " + "-" * 62)
    for i, j, e, w1, w2, _ in sorted(tr, key=lambda x: x[3])[:5]:
        a = dt.datetime.fromtimestamp(T[i] / 1000, dt.UTC)
        b = dt.datetime.fromtimestamp(T[j] / 1000, dt.UTC)
        print(f"    {a:%Y-%m-%d}  {b:%Y-%m-%d}  {e:>10,.2f}{w1*100:>9.1f}%"
              f"{w2*100:>10.1f}%{(T[j]-T[i])/86400000:>7.0f}")
    print()

print("=" * 96)
print("  按【用户的口径】（哪个最接近）验证 MAX_SAFE_LEV")
print("=" * 96)
MMR = 0.004
best_m = min(("A", "B", "C", "D"),
             key=lambda m: abs(res[m][0] - (-0.266)) + abs(res[m][1] - (-0.245)))
wl, wc, _ = res[best_m]
print(f"  最接近用户数的是口径 {best_m}:  low {wl*100:.1f}%   close {wc*100:.1f}%")
print()
print(f"  {'杠杆':>6}{'强平@标的 = 1/lev − MMR':>26}{'对比最坏逆向':>16}{'安全?':>10}")
print("  " + "-" * 60)
for lev in (1, 2, 3, 4, 5, 10):
    liq = 1/lev - MMR
    ok = liq > abs(wl)
    print(f"  {lev:>5}x{liq*100:>25.1f}%{wl*100:>15.1f}%{'✅' if ok else '❌ 会被强平':>10}")
print()
print(f"  用户写 MAX_SAFE_LEV = 3")
print(f"  ⇒ 代码注释: lev≤3 → 强平 ≥32.9% > 26.6% ✅ ; lev=4 → 24.6% < 26.6% ❌")
print(f"     用 low 口径 {abs(wl)*100:.1f}%: 3x 的 32.9% > {abs(wl)*100:.1f}% ✅，"
      f"4x 的 24.6% {'<' if 24.6 < abs(wl)*100 else '>'} {abs(wl)*100:.1f}% "
      f"{'✅' if 24.6 > abs(wl)*100 else '❌'}")
print(f"     用 close 口径 {abs(wc)*100:.1f}%: 4x 的 24.6% "
      f"{'>' if 24.6 > abs(wc)*100 else '<'} {abs(wc)*100:.1f}% "
      f"{'✅' if 24.6 > abs(wc)*100 else '❌'}")
print()
print("  ⇒ 结论：MAX_SAFE_LEV 应取哪个值，取决于用 low 还是 close 口径")
print("     low 口径更保守（用日内最低价）⇒ 应取 low 口径")
