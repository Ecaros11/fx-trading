"""
波动率上限为什么能防强平（why_cap.py）
====================================
检验假设：上限起作用不是因为"预测"，而是因为
          极端行情【发生前】波动率就已经升高了
"""
import datetime as dt
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
bars = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))
T = np.array([b["t"] for b in bars], float)
O = np.array([b["o"] for b in bars], float)
LO = np.array([b["l"] for b in bars], float)
C = np.array([b["c"] for b in bars], float)
nn = len(C)
r = np.zeros(nn)
r[1:] = C[1:] / C[:-1] - 1
DAY = [dt.datetime.fromtimestamp(t / 1000, dt.UTC).strftime("%Y-%m-%d") for t in T]

CAP = 1.20
WIN = 10
V = np.full(nn, np.nan)
for i in range(WIN + 1, nn):
    V[i] = r[i - WIN:i].std(ddof=1) * np.sqrt(365)

print("=" * 92)
print("  ① 2021-05-19 前后的波动率（信号用的是【前一天】的值）")
print("=" * 92)
print()
print(f"  {'日期':<12}{'开盘':>9}{'最低':>9}{'收盘':>9}{'当日':>8}"
      f"{'10日波动':>10}{'上限?':>8}{'实际仓位':>10}")
print("  " + "-" * 78)
for i in range(nn):
    d = DAY[i]
    if d < "2021-05-08" or d > "2021-05-24":
        continue
    v_prev = V[i - 1]                       # 信号用于【今天】的是【昨天】的波动
    tag = "🔴 空仓" if v_prev > CAP else "在场"
    w = 0.0 if v_prev > CAP else min(3.0, 0.60 / v_prev)
    mark = "  ← 519" if d == "2021-05-19" else ""
    print(f"  {d:<12}{O[i]:>9,.0f}{LO[i]:>9,.0f}{C[i]:>9,.0f}"
          f"{(C[i]/O[i]-1)*100:>7.1f}%{v_prev*100:>9.1f}%{tag:>8}{w:>9.3f}x{mark}")

print()
print("=" * 92)
print("  ② 关键：519 之前波动率【已经】超上限几天了")
print("=" * 92)
print()
i519 = DAY.index("2021-05-19")
n_before = 0
for k in range(i519 - 1, max(0, i519 - 30), -1):
    if V[k] > CAP:
        n_before += 1
    else:
        break
print(f"  519 之前连续超过上限的天数：{n_before}")
print(f"  ⇒ 信号在 519 【前 {n_before} 天】就已经让你空仓了")

print()
print("=" * 92)
print("  ③ 全部触发上限的时段（按连续段分组）")
print("=" * 92)
print()
segs = []
i = 61
while i < nn:
    if V[i - 1] > CAP:
        j = i
        while j < nn and V[j - 1] > CAP:
            j += 1
        segs.append((i, j - 1))
        i = j
    else:
        i += 1
print(f"  共 {len(segs)} 段，合计 {sum(b-a+1 for a,b in segs)} 天")
print()
print(f"  {'起始':<12}{'结束':<12}{'天数':>5}{'期间最低跌幅':>14}{'期间最高波动':>14}")
print("  " + "-" * 62)
for a, b in segs:
    lo = (LO[a:b + 1].min() / O[a] - 1) * 100
    hi = V[a - 1:b].max() * 100
    print(f"  {DAY[a]:<12}{DAY[b]:<12}{b-a+1:>5}{lo:>13.1f}%{hi:>13.1f}%")

print()
print("=" * 92)
print("  ④ 结论")
print("=" * 92)
print()
print("  上限起作用的机制【不是预测】，而是：")
print()
print("     极端下跌行情往往持续多日")
print("     ⇒ 第一波下跌后，10 日波动率立刻升高")
print("     ⇒ 上限在【后续更深的下跌】之前就让你空仓")
print()
print("  519 的具体路径：")
print("     05-12  −8.3%   ⇒ 波动率开始升高")
print("     05-13  −2.9%")
print("     05-17  −8.3%   ⇒ 波动率已 > 120%  ⇒ 空仓")
print("     05-18  +2.8%   ⇒ 仍然空仓（波动率 148%）")
print("     05-19  −27.7%（盘中 −58.5%）  ⇒ 你不在场 ✅")
print()
print("  ⇒ 所以它防的是【连续性暴跌】，不是【单日闪崩】")
print("  ⇒ 单日闪崩（一天之内从平静直接暴跌）它挡不住")
