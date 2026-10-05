"""
加本金 / 调仓 —— 用已验证的 align.panel 重算
==========================================
前两版脚本我都写错了（一个手续费荒谬、一个回撤 −105%）。
这次不再自己写逐日模拟，直接用 align.panel（它的正确性已单独验证过）。

只回答三个问题：
  ① 加本金时，目标名义怎么变 ⇒ 要不要调仓
  ② 回测口径到底是「固定名义」还是「恒定杠杆」
  ③ 如果是恒定杠杆，成本高多少
"""
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from align import panel, lag, sharpe, max_dd, cagr

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
P = panel(C, FR, FEE)
N = len(C)
sig = P.sig


def stat(x):
    x = np.asarray(x, float)[W:]
    x = x[np.isfinite(x)]
    return sharpe(x), max_dd(x), cagr(x), float(np.prod(1 + x))


print("=" * 100)
print("  ① 回测口径核对：align.panel 的 net() 用的是哪种模式")
print("=" * 100)
print("  align.panel.net() 的实现：")
print("      w = weight(...)        # 长度 N 的【权重序列】")
print("      wl = lag(w)            # 滞后一天")
print("      net = wl * r - |Δwl|*fee*2 - wl*fr")
print()
print("  ⇒ w[i] 是第 i 天的【目标权重】，且每天都被施加")
print("  ⇒ 所以它是【恒定杠杆】口径 —— 每天都对账到 权益×仓位")
print()
print("  这解释了为什么它会有换手成本：")
w = P.weight(None)
turn = np.abs(np.diff(np.concatenate([[0.0], lag(w)])))
yrs = (N - W) / 365
print(f"     固定版 |Δw| 合计 {turn.sum():.0f} 次（{turn.sum()/yrs:.1f}/年）")
print(f"     = 信号切换 124 次 + 持有期内因权益变化产生的再平衡")
print(f"     手续费年化 {turn.sum()*FEE/yrs*100:.2f}%")
print()
n_flip = int((np.diff(sig[W:]) != 0).sum())
print(f"     其中纯信号切换只有 {n_flip} 次（{n_flip/yrs:.1f}/年）")
print(f"     ⇒ 差额 {turn.sum():.0f} − {n_flip} × 1.405 = "
      f"{turn.sum() - n_flip*1.405:.0f} 次是【再平衡】")
print(f"     占比 {(turn.sum()-n_flip*1.405)/turn.sum()*100:.0f}%")

print()
print("=" * 100)
print("  ② 两种模式的实算对比")
print("=" * 100)


def fixed_notional():
    """入场定数量，持有期不动。用权重序列表达：持有期内 w 恒定"""
    w = np.zeros(N)
    i = W
    while i < N:
        if sig[i] == 1:
            j = i
            while j < N and sig[j] == 1:
                j += 1
            # 入场那根的权重按当时口径定，之后不变
            w[i:j] = 1.405
            i = j
        else:
            i += 1
    return w


w_const = P.weight(None)          # 恒定杠杆（align 的口径）
w_fixed = fixed_notional()        # 固定名义

r = P.r
FRl = lag(FR)


def net_of(w):
    wl = lag(w)
    turn = np.abs(np.diff(np.concatenate([[0.0], wl])))
    return wl * r - turn * FEE * 2 - wl * FRl, turn


print(f"  {'模式':<34}{'夏普':>9}{'年化':>10}{'回撤':>10}"
      f"{'终值':>10}{'换手/年':>10}{'手续费/年':>11}")
print("  " + "-" * 94)
for lab, w in (("恒定杠杆（align.panel 口径）", w_const),
               ("固定名义（入场定数量，不动）", w_fixed)):
    x, turn = net_of(w)
    sh, dd, an, mult = stat(x)
    print(f"  {lab:<34}{sh:>9.3f}{an*100:>9.1f}%{dd*100:>9.1f}%{mult:>10.3f}"
          f"{turn.sum()/yrs:>10.1f}{turn.sum()*FEE/yrs*100:>10.2f}%")

print()
print("=" * 100)
print("  ③ 直接回答：加本金要不要调仓")
print("=" * 100)
print(f"  {'权益':>10}{'目标仓位':>11}{'目标名义':>11}{'加 10U 后名义':>15}"
      f"{'差额':>10}{'要调吗':>10}")
print("  " + "-" * 70)
for eq in (14.80, 18, 19.99, 20, 25, 52.3, 100):
    pos = max(1.0, MIN_N / eq)
    n = eq * pos
    eq2 = eq + 10
    n2 = eq2 * max(1.0, MIN_N / eq2)
    need = "不用" if abs(n2 - n) < 0.01 else "要调"
    print(f"  {eq:>9.2f}U{pos:>11.4f}{n:>10.2f}U{n2:>14.2f}U"
          f"{n2-n:>+9.2f}U{need:>10}")
print()
print("  ⇒ 权益 < 20U：目标名义恒为 20.00U ⇒ 加钱不改变目标 ⇒ 【不用调】")
print("  ⇒ 权益 ≥ 20U：目标名义 = 权益     ⇒ 加钱等额增加  ⇒ 【要调】")

print()
print("=" * 100)
print("  ④ 你自己的账户现在在哪一档")
print("=" * 100)
EQ = 14.787
print(f"  实测权益 {EQ:.3f} USDT")
print(f"  ⇒ 目标仓位 = max(1.0, 20/{EQ:.3f}) = {max(1.0, MIN_N/EQ):.4f}x")
print(f"  ⇒ 目标名义 = {EQ * max(1.0, MIN_N/EQ):.2f} USDT  （恒为 20.00）")
print()
print(f"  你往账户里加钱，只要权益仍 < 20U：")
for add in (1, 2, 5):
    eq2 = EQ + add
    pos2 = max(1.0, MIN_N / eq2)
    print(f"    加 {add:>2}U → 权益 {eq2:>6.3f}U → 仓位 {pos2:.4f}x → "
          f"目标名义 {eq2*pos2:.2f}U   {'不用调 ✅' if eq2 < 20 else '要调'}")
print()
print(f"  加到 20U 以上才会开始需要调仓：")
for add in (5.3, 10, 20):
    eq2 = EQ + add
    pos2 = max(1.0, MIN_N / eq2)
    n2 = eq2 * pos2
    print(f"    加 {add:>4}U → 权益 {eq2:>6.3f}U → 仓位 {pos2:.4f}x → "
          f"目标名义 {n2:>6.2f}U   {'不用调' if eq2 < 20 else '要调 +%.2fU' % (n2-20)}")
