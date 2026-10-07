"""
"明天要卖 0.08 ETH"是亏损吗（rebalance_cost.py）
=============================================
对比三种做法：
  A. 波动率目标 + 每天调仓（现状）
  B. 波动率目标 + 只在信号翻转时调仓（懒调仓）
  C. 固定仓位（买入后完全不管）
"""
import collections
import importlib.util
import json
import pathlib

import numpy as np

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
sa = importlib.util.spec_from_file_location("al", HERE / "align.py")
al = importlib.util.module_from_spec(sa)
sa.loader.exec_module(al)

bars = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))
bars, _ = al.__dict__.get("_", (bars, None)) if False else (bars, None)
C = np.array([b["c"] for b in bars], float)
fr = json.loads((ROOT / "data" / "funding" / "ETHUSDT.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
CD = np.array([int(b["t"] // 86400000) for b in bars])
FR = np.nan_to_num(np.array(
    [float(np.sum(agg[int(d)])) if int(d) in agg else np.nan for d in CD]))
P = al.panel(C, FR)
W, FEE, TV, CAP = 60, 0.0005, 0.60, 1.20
n = P.n
r, sig, vol = P.r, P.sig, P.vol

print("=" * 92)
print("  一、先看清「卖 0.08 ETH」到底发生了什么")
print("=" * 92)
print()
# 造一个具体例子
eq0, px0 = 1000.0, 2700.0
vol0, vol1 = 0.15, 0.30          # 波动率翻倍
w0, w1 = min(3.0, TV / vol0), min(3.0, TV / vol1)
q0, q1 = eq0 * w0 / px0, eq0 * w1 / px0
print(f"  假设：权益 {eq0:.0f}U，价格 {px0:.0f}")
print(f"     第 1 天：波动 {vol0*100:.0f}%  ⇒ 仓位 {w0:.3f}x  ⇒ 持有 {q0:.4f} ETH")
print(f"     第 2 天：波动 {vol1*100:.0f}%  ⇒ 仓位 {w1:.3f}x  ⇒ 目标 {q1:.4f} ETH")
print(f"     ⇒ 要卖 {q0-q1:.4f} ETH")
print()
fee = (q0 - q1) * px0 * FEE
print(f"  卖出手续费 = {q0-q1:.4f} × {px0:.0f} × 0.05% = {fee:.4f} U"
      f"（占权益 {fee/eq0*100:.4f}%）")
print()
print("  ⚠️ 关键区分：")
print(f"     · 【已经发生的浮亏】= 价格跌的部分 —— 卖不卖都已经亏了")
print(f"     · 【手续费】= {fee:.4f}U —— 这才是调仓的额外成本")
print(f"     · 【卖出的意义】= 那 {q0-q1:.4f} ETH 不再承担后续下跌风险")
print()
print(f"  ⇒ 如果不卖：那 {q0-q1:.4f} ETH 继续跟着跌")
print(f"  ⇒ 如果卖了：变成现金，不再承受风险")
print(f"  ⇒ 而策略判断「波动率翻倍 ⇒ 该少持有」是有依据的")

print()
print("=" * 92)
print("  二、三种做法的实测对比")
print("=" * 92)
print()


def run(mode):
    eq, wp, pk, dd = 1000.0, 0.0, 1000.0, 1.0
    xx, turns = [], 0.0
    for i in range(W, n):
        if mode == "daily":
            w = (min(3.0, TV / vol[i - 1])
                 if (sig[i - 1] and np.isfinite(vol[i - 1])
                     and 0 < vol[i - 1] <= CAP) else 0.0)
        elif mode == "signal_only":
            # 只在信号翻转时调仓，期间保持
            w = wp
            cur = (min(3.0, TV / vol[i - 1])
                   if (sig[i - 1] and np.isfinite(vol[i - 1])
                       and 0 < vol[i - 1] <= CAP) else 0.0)
            if (cur > 0) != (wp > 0):
                w = cur
        else:  # fixed
            w = sig[i - 1] * 1.405
        turn = abs(w - wp)
        turns += turn
        x = w * r[i] - turn * FEE - w * FR[i]
        xx.append(x)
        eq *= (1 + x)
        pk = max(pk, eq)
        dd = min(dd, eq / pk)
        wp = w
    xx = np.array(xx)
    sh = xx.mean() / xx.std(ddof=1) * np.sqrt(365)
    geo = (eq ** (365 / len(xx)) - 1) * 100
    return sh, geo, (dd - 1) * 100, eq, turns / (len(xx) / 365)


print(f"  {'做法':<34}{'夏普':>9}{'几何年化':>11}{'回撤':>9}"
      f"{'年均换手':>10}{'手续费/年':>11}")
print("  " + "-" * 84)
for lab, mode in (("A. 波动率目标 + 每天调仓（现状）", "daily"),
                  ("B. 波动率目标 + 只在信号翻转时调", "signal_only"),
                  ("C. 固定仓位（买入后不管）", "fixed")):
    sh, geo, dd, eq, turn = run(mode)
    print(f"  {lab:<34}{sh:>9.4f}{geo:>10.1f}%{dd:>8.1f}%"
          f"{turn:>10.2f}{turn*FEE*100:>10.2f}%")

print()
print("=" * 92)
print("  三、结论")
print("=" * 92)
print()
sh_a, geo_a, dd_a, _, turn_a = run("daily")
sh_b, geo_b, dd_b, _, turn_b = run("signal_only")
sh_c, geo_c, dd_c, _, turn_c = run("fixed")
print(f"  A（现状）vs B（懒调仓）：")
print(f"     年化  {geo_a:>6.1f}%  vs  {geo_b:>6.1f}%   差 {geo_a-geo_b:+.1f}pp")
print(f"     回撤  {dd_a:>6.1f}%  vs  {dd_b:>6.1f}%   差 {dd_a-dd_b:+.1f}pp")
print(f"     换手  {turn_a:>6.2f}  vs  {turn_b:>6.2f}")
print(f"     手续费 {turn_a*FEE*100:.2f}%/年  vs  {turn_b*FEE*100:.2f}%/年")
print(f"     ⇒ 每天调仓多付 {(turn_a-turn_b)*FEE*100:.2f}%/年 手续费，"
      f"换来 {abs(dd_a)-abs(dd_b):+.1f}pp 回撤")
print()
print(f"  A（现状）vs C（固定仓位）：")
print(f"     年化  {geo_a:>6.1f}%  vs  {geo_c:>6.1f}%")
print(f"     回撤  {dd_a:>6.1f}%  vs  {dd_c:>6.1f}%   ⇒ 调仓换来 {abs(dd_c)-abs(dd_a):+.1f}pp 回撤改善")
