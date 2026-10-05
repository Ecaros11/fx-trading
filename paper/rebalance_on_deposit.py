"""
加本金要不要调仓？—— 以及一个更根本的问题
=========================================
文档 §2.3：目标仓位 = max(1.0, 20 ÷ 权益)，目标名义 = 权益 × 目标仓位

所以：
  · 权益 < 20U  →  目标仓位 = 20/权益  →  目标名义恒为 20.00U
  · 权益 ≥ 20U  →  目标仓位 = 1.0      →  目标名义 = 权益

⇒ 加本金会改变目标名义 ⇒ 逻辑上需要调仓。

但这里有个更根本的问题：权益【平时就在变】——
  持仓盈亏、资金费、手续费都会改变权益，
  而目标名义 = f(权益)，所以目标名义每天都在变。

那么"只在信号切换时下单"这个做法，
到底和"每天按目标重新对账"差多少？
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
T = np.array([b["t"] for b in d1], float)
day = np.array([b["t"] // 86400000 for b in d1])
FR = np.array([fday.get(int(day[i]), 0.0) for i in range(len(C))])
N = len(C)
sig = np.nan_to_num((C > sma(C, 50)).astype(float))
W = 60
FEE = 0.0005
MIN_N = 20.0

print("=" * 100)
print("  问题 1：加本金时目标名义怎么变")
print("=" * 100)
print(f"  {'权益':>10}{'目标仓位':>11}{'目标名义':>11}{'加 10U 后':>12}"
      f"{'名义变化':>11}{'变化%':>9}")
print("  " + "-" * 64)
for eq in (14.8, 18, 19, 20, 30, 52.3, 100):
    pos = max(1.0, MIN_N / eq)
    n = eq * pos
    eq2 = eq + 10
    pos2 = max(1.0, MIN_N / eq2)
    n2 = eq2 * pos2
    print(f"  {eq:>9.1f}U{pos:>11.4f}{n:>10.2f}U{eq2:>11.1f}U"
          f"{n2-n:>+10.2f}U{(n2/n-1)*100:>+8.1f}%")
print()
print("  ⇒ 权益 < 20U：加钱【不改变】目标名义（恒 20.00U），不需要调仓")
print("  ⇒ 权益 ≥ 20U：加钱【等额增加】目标名义，需要调仓")

print()
print("=" * 100)
print("  问题 2（更根本）：权益平时就在变，目标名义每天在变")
print("=" * 100)
print("  权益 = 起始 + 持仓盈亏 + 资金费 − 手续费")
print("  目标名义 = 权益 × 仓位，所以它每天都在变。")
print()
print("  那「只在信号切换时下单」和「每天对账」差多少？")


def simulate(rebalance):
    """
    rebalance = False : 只在信号切换时下单（现在工具的逻辑）
    rebalance = True  : 每天把数量调到 权益×仓位/价格
    返回权益倍数序列，以及累计换手
    """
    eq = 14.80
    qty = 0.0
    out = []
    turn_total = 0.0
    for i in range(N):
        # 结算
        if qty != 0 and i > 0:
            eq += qty * (C[i] - C[i - 1])
            eq -= qty * C[i] * FR[i]
        if i < W or sig[i] < 0:
            out.append(eq)
            continue
        pos = max(1.0, MIN_N / eq)
        want_long = sig[i] == 1
        if want_long:
            tgt_qty = eq * pos / C[i]
            if not rebalance:
                # 只在信号【刚切换】时才动
                if i > 0 and sig[i - 1] == 1 and qty > 0:
                    tgt_qty = qty          # 不动
        else:
            tgt_qty = 0.0
        if abs(tgt_qty - qty) * C[i] > 0.01:
            turn_total += abs(tgt_qty - qty) * C[i] * FEE
            eq -= abs(tgt_qty - qty) * C[i] * FEE
            qty = tgt_qty
        out.append(eq)
    return np.array(out) / 14.80, turn_total


eqA, feeA = simulate(False)
eqB, feeB = simulate(True)
yrs = (N - W) / 365


def st(e):
    x = np.diff(e) / e[:-1]
    x = x[np.isfinite(x)]
    return sharpe(x), max_dd(x), cagr(x), e[-1]


for lab, e, f in (("只在信号切换时下单（现在）", eqA, feeA),
                  ("每天对账到目标名义（理想）", eqB, feeB)):
    sh, dd, an, mult = st(e[W:])
    print()
    print(f"  {lab}")
    print(f"    夏普 {sh:.3f}   回撤 {dd*100:.1f}%   年化 {an*100:.1f}%   "
          f"终值 {mult:.3f}x")
    print(f"    累计手续费占初始本金 {f*100:.2f}%   年化 {f/yrs*100:.2f}%")

shA, ddA, anA, mA = st(eqA[W:])
shB, ddB, anB, mB = st(eqB[W:])
print()
print("=" * 100)
print("  差异")
print("=" * 100)
print(f"  夏普    {shB-shA:+.4f}")
print(f"  年化    {(anB-anA)*100:+.2f}pp")
print(f"  回撤    {(ddB-ddA)*100:+.2f}pp")
print(f"  终值    {mB-mA:+.3f}x   （{mA:.2f} → {mB:.2f}）")
print(f"  手续费  {feeB*100:.2f}% vs {feeA*100:.2f}%  （年化 {(feeB-feeA)/yrs*100:+.2f}pp）")

print()
print("=" * 100)
print("  中间态：多久对账一次")
print("=" * 100)
print(f"  {'对账频率':<22}{'夏普':>9}{'年化':>10}{'回撤':>10}{'累计手续费':>12}")
print("  " + "-" * 64)
for every in (1, 5, 10, 20, 99999):
    eq = 14.80; qty = 0.0; out = []; fee = 0.0
    for i in range(N):
        if qty != 0 and i > 0:
            eq += qty * (C[i] - C[i - 1])
            eq -= qty * C[i] * FR[i]
        if i < W or sig[i] < 0:
            out.append(eq); continue
        pos = max(1.0, MIN_N / eq)
        if sig[i] == 1:
            tgt = eq * pos / C[i]
            if every > 90000 and i > 0 and sig[i - 1] == 1 and qty > 0:
                tgt = qty
            elif every < 90000 and (i - W) % every != 0 and qty > 0:
                tgt = qty
        else:
            tgt = 0.0
        if abs(tgt - qty) * C[i] > 0.01:
            fee += abs(tgt - qty) * C[i] * FEE
            eq -= abs(tgt - qty) * C[i] * FEE
            qty = tgt
        out.append(eq)
    e = np.array(out) / 14.80
    sh, dd, an, m = st(e[W:])
    lab = "只在信号切换时" if every > 90000 else f"每 {every} 天"
    print(f"  {lab:<22}{sh:>9.3f}{an*100:>9.1f}%{dd*100:>9.1f}%{fee*100:>11.2f}%")
