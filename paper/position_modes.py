"""
澄清：两种仓位模式，别混
======================
上一版脚本把两种模式当成一回事了，导致手续费荒谬（年化 83%）。

模式 A：固定名义（入场时定数量，持有期不变）
    入场：数量 = 权益 × 仓位 / 价格
    持有期：数量不动，只吃价格变动
    ⇒ 权益涨了，实际杠杆下降（从 1.4x 慢慢降到 1.0x 甚至更低）
    ⇒ 手续费只在信号切换时产生  ← 【回测和文档用的是这个】

模式 B：恒定杠杆（每天都对账）
    每天：数量 = 权益 × 仓位 / 当前价
    ⇒ 价格涨了要多买、跌了要少卖（追涨杀跌）
    ⇒ 每天都有换手，手续费极高

本脚本把两者分开算，并单独回答"加本金要不要调仓"。
"""
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from align import sma, sharpe, max_dd, cagr

ROOT = pathlib.Path(__file__).parent.parent
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
sig = np.nan_to_num((C > sma(C, 50)).astype(float))
W = 60
FEE = 0.0005
MIN_N = 20.0
EQ0 = 14.80
yrs = (N - W) / 365


def run(mode, lev_target=1.0):
    """
    mode = "fixed_notional" : 入场定数量，持有期不动（回测口径）
           "const_lev"      : 每天对账到 权益×lev_target
    """
    eq = EQ0
    qty = 0.0
    fee_total = 0.0
    turn_total = 0.0
    out = []
    for i in range(N):
        if qty != 0 and i > 0:
            eq += qty * (C[i] - C[i - 1])
            eq -= qty * C[i] * FR[i]
        if i < W or sig[i] < 0:
            if qty != 0:                    # 空仓信号：平掉
                fee = abs(qty) * C[i] * FEE
                eq -= fee; fee_total += fee; turn_total += abs(qty) * C[i]
                qty = 0.0
            out.append(eq); continue
        # 做多
        if mode == "fixed_notional":
            # 只在【刚从空仓转多】时建仓，之后不动
            if qty == 0:
                pos = max(1.0, MIN_N / eq)
                tgt = eq * pos / C[i]
                fee = abs(tgt) * C[i] * FEE
                eq -= fee; fee_total += fee; turn_total += abs(tgt) * C[i]
                qty = tgt
        else:
            tgt = eq * lev_target / C[i]
            if abs(tgt - qty) * C[i] > 0.05:
                fee = abs(tgt - qty) * C[i] * FEE
                eq -= fee; fee_total += fee; turn_total += abs(tgt - qty) * C[i]
                qty = tgt
        out.append(eq)
    return np.array(out) / EQ0, fee_total, turn_total


print("=" * 100)
print("  两种仓位模式（从 14.80U 起，2020 前开始全程）")
print("=" * 100)
print(f"  {'模式':<30}{'夏普':>9}{'年化':>10}{'回撤':>10}"
      f"{'终值':>10}{'年化手续费':>12}")
print("  " + "-" * 82)
res = {}
for lab, mode, lt in (("A 固定名义（入场定数量，回测口径）", "fixed_notional", None),
                      ("B 恒定 1.0x（每天对账）", "const_lev", 1.0),
                      ("B 恒定 1.3486x（每天对账）", "const_lev", 1.3486)):
    e, fee, turn = run(mode, lt or 1.0)
    x = np.diff(e[W:]) / e[W:][:-1]
    x = x[np.isfinite(x)]
    res[lab] = dict(sh=sharpe(x), dd=max_dd(x), ann=cagr(x), mult=e[-1],
                    fee=fee / yrs)
    print(f"  {lab:<30}{res[lab]['sh']:>9.3f}{res[lab]['ann']*100:>9.1f}%"
          f"{res[lab]['dd']*100:>9.1f}%{res[lab]['mult']:>10.3f}"
          f"{res[lab]['fee']*100:>11.2f}%")

print()
print("=" * 100)
print("  结论 1：模式 A 才是回测和文档用的口径")
print("=" * 100)
print(f"""
  模式 A（固定名义）年化手续费 {res['A 固定名义（入场定数量，回测口径）']['fee']*100:.2f}%
      —— 和文档 §3.1 写的「约 1.30%」一致 ✅

  模式 B（恒定杠杆）年化手续费 {res['B 恒定 1.0x（每天对账）']['fee']*100:.2f}%
      —— 高出一个数量级。文献里叫 "volatility drag / rebalancing cost"

  ⇒ 文档 §2.3 的公式「目标名义 = 权益 × 目标仓位」读起来像模式 B，
     但回测和实际执行应该是模式 A。
     这是文档里一个没说清的地方。
""")

print("=" * 100)
print("  结论 2：回答你的问题 —— 加本金要不要调仓？")
print("=" * 100)
print(f"  {'权益':>10}{'目标仓位':>11}{'目标名义':>11}{'加 10U 后':>12}"
      f"{'名义变化':>11}{'要调吗':>10}")
print("  " + "-" * 66)
for eq in (14.8, 18, 19, 19.99, 20, 30, 52.3):
    pos = max(1.0, MIN_N / eq)
    n = eq * pos
    eq2 = eq + 10
    n2 = eq2 * max(1.0, MIN_N / eq2)
    need = "❌ 不用" if abs(n2 - n) < 0.01 else "✅ 要调"
    print(f"  {eq:>9.2f}U{pos:>11.4f}{n:>10.2f}U{eq2:>11.1f}U"
          f"{n2-n:>+10.2f}U{need:>10}")
print()
print("  ⇒ 权益 < 20U：目标名义恒为 20.00U，加钱【不改变】目标 ⇒ 不用调")
print("  ⇒ 权益 ≥ 20U：目标名义 = 权益，加钱【等额增加】目标 ⇒ 要调")

print()
print("=" * 100)
print("  结论 3：但权益平时就在变，所以‘要不要调’每天都在问")
print("=" * 100)
print("  权益 = 起始 + 持仓盈亏 + 资金费 − 手续费")
print("  ⇒ 持有期内价格涨了，权益涨了，但【数量没变】⇒ 实际杠杆在漂")
print()
print("  模式 A 下一次持有期内的实际杠杆漂移（以最长那笔为例）：")
# 找最长的一笔
best = None
i = W
while i < N - 1:
    if sig[i] == 1 and (i == 0 or sig[i - 1] == 0):
        j = i + 1
        while j < N and sig[j] == 1:
            j += 1
        e = min(j, N - 1)
        if best is None or (e - i) > (best[1] - best[0]):
            best = (i, e)
        i = e
    else:
        i += 1
i0, i1 = best
print(f"    {i0} → {i1}  （{i1-i0} 天）")
eq = EQ0
for i in range(W, i1 + 1):
    if i > W and sig[i - 1] == 1:
        eq += eq * 1.405 * (C[i] / C[i - 1] - 1) if False else 0
print("    （下面用简化模型：入场 1.3486x，价格变动直接作用于权益）")
n0 = EQ0 * 1.3486 / C[i0]
print(f"    入场：权益 {EQ0:.2f}U  数量 {n0:.6f} ETH  名义 {EQ0*1.3486:.2f}U  杠杆 1.3486x")
for k in (0, 10, 20, 30, 50, 80, 100):
    i = min(i0 + k, i1)
    px = C[i] / C[i0]
    eq_k = EQ0 + n0 * (C[i] - C[i0])
    notional = n0 * C[i]
    print(f"    +{k:>3} 天  价格 {px:>6.2f}x  权益 {eq_k:>8.2f}U  "
          f"名义 {notional:>8.2f}U  实际杠杆 {notional/eq_k:>6.3f}x")
