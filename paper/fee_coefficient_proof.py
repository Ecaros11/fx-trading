"""
手续费系数：到底是 1 还是 2 —— 逐笔现金流推导
============================================
争论的核心：turn = |Δw| 的【和】是否等于【实际成交额的和】。

用显式的钱来算，不用比例。
"""
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
FEE = 0.0005

print("=" * 100)
print("  ① 显式现金流：0 → 1.405 → 0，权益 100 USDT 不变（价格不动）")
print("=" * 100)
E = 100.0
print(f"  初始权益 {E} USDT，价格恒为 1（方便看名义）")
print()
steps = [
    ("t0  空仓", 0.0, None),
    ("t1  信号转多，买入", 1.405, "买入"),
    ("t2  持有中，信号不变", 1.405, None),
    ("t3  信号转空，卖出", 0.0, "卖出"),
]
for lab, w, act in steps:
    if act == "买入":
        notional = w * E
        cost = notional * FEE
        print(f"  {lab:<22} w={w:.3f}  成交额 {notional:>7.3f}  手续费 {cost:>7.4f}")
    elif act == "卖出":
        notional = 1.405 * E
        cost = notional * FEE
        print(f"  {lab:<22} w={w:.3f}  成交额 {notional:>7.3f}  手续费 {cost:>7.4f}")
    else:
        print(f"  {lab:<22} w={w:.3f}  无成交")
print()
actual = 1.405 * E * FEE + 1.405 * E * FEE
print(f"  实际手续费合计 = 2 × 1.405 × {E} × {FEE} = {actual:.4f} USDT")
print()
turn_sum = abs(1.405 - 0.0) + abs(1.405 - 1.405) + abs(0.0 - 1.405)
print(f"  |Δw| 之和 = |1.405-0| + |1.405-1.405| + |0-1.405| = {turn_sum:.3f}")
print()
print(f"  系数 1：{turn_sum:.3f} × {E} × {FEE} = {turn_sum*E*FEE:.4f}  "
      f"{'✅ 与现金流一致' if abs(turn_sum*E*FEE-actual)<1e-9 else '❌'}")
print(f"  系数 2：{turn_sum:.3f} × {E} × {FEE} × 2 = {turn_sum*E*FEE*2:.4f}  "
      f"{'✅' if abs(turn_sum*E*FEE*2-actual)<1e-9 else '❌ 多算一倍'}")
print()
print("  ⇒ 结论：【系数 1 是正确的】。|Δw| 的【和】已经等于两次成交额之和。")
print("     系数 2 把手续费重复计算了。")
print("     我上一条建议改成 ×2 是错的。")

print()
print("=" * 100)
print("  ② 为什么我搞错了：两种写法的等价性只在特定条件下成立")
print("=" * 100)
print("""
  写法 A（对的）： Σ |Δw_t| × E_t × fee
      —— 每一步的成交额 × 费率，然后求和

  写法 B（align.py）： (Σ |Δw_t|) × fee × 2
      —— 先求和，再乘费率，再乘 2

  B = A 只在【Σ|Δw| 恰好等于 2 × 单边总量】时成立，也就是只有"进出各一次"时。
  一旦有【同向多次成交】（比如仓位从 1.0 加到 1.5 再加到 2.0），
  A 是三步各一次，B 会把它们也乘 2 ⇒ 偏大。

  但更根本的是：A 里的 E_t 是【每天的权益】，B 里把 Σ|Δw| 提出来再乘，
  丢掉了权益随时间变化的信息。

  ⇒ 我当时的推理是"align.py 是主回测，肯定对，所以让 dynamic_drawdown 跟它一致"。
     这是【诉诸权威】而不是【自己推导】。错在这里。
""")

print("=" * 100)
print("  ③ align.py 的 ×2 影响有多大 —— 用文档的数交叉验证")
print("=" * 100)
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
W = 60
m50 = np.concatenate([[np.nan] * 49, np.convolve(C, np.ones(50) / 50, "valid")])
sig = np.nan_to_num((C > m50).astype(float))
r = np.zeros(N)
r[1:] = C[1:] / C[:-1] - 1
yrs = (N - W) / 365

w = sig * 1.405
wl = np.concatenate([[0.0], w[:-1]])
FRl = np.concatenate([[0.0], FR[:-1]])
turn = np.abs(np.diff(np.concatenate([[0.0], wl])))

print(f"  |Δw| 合计 = {turn.sum():.2f}")
print(f"  年换手 = {turn.sum()/yrs:.1f} 次/年")
print()
print(f"  文档 §3.1 写：换手 18.5 次/年，年化手续费 1.30%")
print(f"     18.5 × 1.405 × 0.0005 = {18.5*1.405*0.0005*100:.2f}%   ← 系数 1 ✅")
print(f"     18.5 × 1.405 × 0.0005 × 2 = {18.5*1.405*0.0005*2*100:.2f}%  ← 系数 2 ❌")
print()


def sh(x):
    x = np.asarray(x, float)[W:]
    x = x[np.isfinite(x)]
    return x.mean() / x.std() * np.sqrt(365)


def ddc(x):
    x = np.asarray(x, float)[W:]
    eq = np.cumprod(1 + x)
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def cagr(x):
    x = np.asarray(x, float)[W:]
    x = x[np.isfinite(x)]
    return np.expm1(np.log1p(x).mean() * 365)


print("=" * 100)
print("  ④ 修正 align.py 的 ×2 之后，METHODS 表的数字会变多少")
print("=" * 100)
print(f"  {'版本':<18}{'系数2夏普':>11}{'系数1夏普':>11}{'Δ':>8}"
      f"{'系数2回撤':>11}{'系数1回撤':>11}{'Δ(pp)':>9}")
print("  " + "-" * 80)
vol20 = np.full(N, np.nan)
for i in range(21, N):
    vol20[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)


def build(tv):
    if tv is None:
        return np.nan_to_num(sig * 1.405)
    raw = np.where(np.isfinite(vol20) & (vol20 > 1e-9), tv / np.where(vol20 > 1e-9, vol20, 1.0), 0.0)
    return np.nan_to_num(sig * np.clip(raw, 0, 3))


rows = []
for lab, tv in (("固定版", None), ("波动率目标 40%", 0.40),
                ("波动率目标 25%", 0.25), ("波动率目标 15%", 0.15)):
    w = build(tv)
    wl = np.concatenate([[0.0], w[:-1]])
    tn = np.abs(np.diff(np.concatenate([[0.0], wl])))
    net2 = wl * r - tn * FEE * 2 - wl * FRl
    net1 = wl * r - tn * FEE - wl * FRl
    rows.append((lab, tv, net1, net2))
    print(f"  {lab:<18}{sh(net2):>11.3f}{sh(net1):>11.3f}{sh(net1)-sh(net2):>+8.3f}"
          f"{ddc(net2)*100:>10.1f}%{ddc(net1)*100:>10.1f}%"
          f"{(ddc(net1)-ddc(net2))*100:>+8.1f}")

print()
print("  换成 1.405x 的 DD_BY_LEV 表：")
print(f"  {'杠杆':>8}{'系数2':>10}{'系数1':>10}{'Δ(pp)':>9}")
print("  " + "-" * 40)
for lev in (0.8, 1.0, 1.2, 1.405, 1.6, 2.0):
    w = np.nan_to_num(sig * lev)
    wl = np.concatenate([[0.0], w[:-1]])
    tn = np.abs(np.diff(np.concatenate([[0.0], wl])))
    n2 = wl * r - tn * FEE * 2 - wl * FRl
    n1 = wl * r - tn * FEE - wl * FRl
    print(f"  {lev:>8.3f}{ddc(n2)*100:>9.1f}%{ddc(n1)*100:>9.1f}%"
          f"{(ddc(n1)-ddc(n2))*100:>+8.2f}")

print()
print("=" * 100)
print("  ⑤ 方向判断")
print("=" * 100)
w = build(None)
wl = np.concatenate([[0.0], w[:-1]])
tn = np.abs(np.diff(np.concatenate([[0.0], wl])))
c2 = (tn * FEE * 2)[W:].sum()
c1 = (tn * FEE)[W:].sum()
print(f"  累计手续费（系数 2）{c2*100:.2f}%    （系数 1）{c1*100:.2f}%")
print(f"  ⇒ 系数 2 每年多扣 {(c2-c1)/yrs*100:.2f}pp")
print(f"  ⇒ 所以系数 2 让夏普【偏低】，真实夏普应该【更高】")
print(f"     实测：{sh(build(None)):.3f}（系数2）→ "
      f"{sh(np.concatenate([[0.0],build(None)[:-1]])*r - tn*FEE - np.concatenate([[0.0],build(None)[:-1]])*FRl):.3f}（系数1）")
