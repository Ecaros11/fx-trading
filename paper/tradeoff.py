"""
杠杆/档位的「安全 vs 收益」全景（tradeoff.py）
==========================================
对每个档位：算最高仓位 → 需要几倍杠杆 → 强平线 → 能不能挡住历史最坏
再对比收益
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

MMR = 0.004
WORST_WEIGHTED = 0.448          # 相对加权开仓价的最坏逆向（2021-05-19）
WORST_SINGLE = 0.256            # 平静期最差单日（2021-09-07）
CAP = 1.20

bars = json.loads((ROOT / "data/crypto/ETHUSDT.json").read_text(encoding="utf-8"))
C = np.array([b["c"] for b in bars], float)
fr = json.loads((ROOT / "data/funding/ETHUSDT.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
CD = np.array([int(b["t"] // 86400000) for b in bars])
FR = np.nan_to_num(np.array(
    [float(np.sum(agg[int(d)])) if int(d) in agg else np.nan for d in CD]))
P = al.panel(C, FR)
W = 60

print("=" * 96)
print("  一、安全维度（不涉及收益）")
print("=" * 96)
print()
print(f"  {'档位':<8}{'最高仓位':>10}{'需要杠杆':>10}{'强平线':>10}"
      f"{'vs −44.8%':>12}{'vs −25.6%':>12}{'判定':>10}")
print("  " + "-" * 78)
TIERS = []
for tv in (0.60, 0.40, 0.25, 0.15):
    w = P.weight(tv)[W:]
    on = w[w > 0]
    wmax = on.max()
    lev = int(np.ceil(wmax - 1e-9))
    liq = 1 / lev - MMR
    ok_w = liq > WORST_WEIGHTED
    ok_s = liq > WORST_SINGLE
    TIERS.append((tv, wmax, lev, liq))
    print(f"  {tv*100:>6.0f}%{wmax:>10.3f}x{lev:>9}x{liq*100:>9.1f}%"
          f"{'✅' if ok_w else '🔴':>12}{'✅' if ok_s else '🔴':>12}"
          f"{'✅ 结构化安全' if ok_w else '🔴 有强平风险':>10}")

print()
print("  ⇒ ⚠️ 关键：只有 15% 档能真正只用 2x")
print("     60% / 40% / 25% 档的最高仓位都 > 2.0x ⇒ 必须 3x")
print("     而逐仓杠杆【只能调高不能调低】⇒ 一旦设 3x 就永远 3x")

print()
print("=" * 96)
print("  二、收益维度（含全部成本，2019-11 ~ 2026-10）")
print("=" * 96)
print()
print(f"  {'档位':<8}{'夏普':>9}{'算数年化':>10}{'几何年化':>10}{'回撤':>10}"
      f"{'期末':>12}{'门槛':>9}")
print("  " + "-" * 70)
RET = []
for tv, wmax, lev, liq in TIERS:
    x = P.net(tv)[W:]
    x = x[np.isfinite(x)]
    sh = al.sharpe(x)
    dd = al.max_dd(x)
    eq = np.cumprod(1 + x)
    arith = x.mean() * 365 * 100
    geo = (eq[-1] ** (365 / len(x)) - 1) * 100
    w = P.weight(tv)[W:]
    on = w[w > 0]
    need = 20.0 / np.percentile(on, 10)
    RET.append((tv, sh, arith, geo, dd, eq[-1], need))
    print(f"  {tv*100:>6.0f}%{sh:>9.4f}{arith:>9.1f}%{geo:>9.1f}%{dd*100:>9.1f}%"
          f"{eq[-1]:>11.1f}x{need:>8.1f}U")

print()
print("=" * 96)
print("  三、合起来看：安全 vs 收益")
print("=" * 96)
print()
print(f"  {'档位':<8}{'杠杆':>6}{'强平线':>9}{'安全?':>8}{'几何年化':>10}"
      f"{'回撤':>10}{'每单位回撤的收益':>18}")
print("  " + "-" * 74)
for (tv, wmax, lev, liq), (_, sh, ar, geo, dd, eq, need) in zip(TIERS, RET):
    safe = "✅" if liq > WORST_WEIGHTED else "🔴"
    eff = geo / abs(dd * 100)
    print(f"  {tv*100:>6.0f}%{lev:>5}x{liq*100:>8.1f}%{safe:>8}"
          f"{geo:>9.1f}%{dd*100:>9.1f}%{eff:>17.2f}")

print()
print("=" * 96)
print("  四、如果按【回撤预算】选")
print("=" * 96)
print()
print("  假设你最多能接受 N% 的回撤：")
print()
for budget in (15, 25, 40, 55, 65):
    best = None
    for tv, sh, ar, geo, dd, eq, need in RET:
        if abs(dd * 100) <= budget:
            if best is None or geo > best[1]:
                best = (tv, geo, dd, need)
    # 加入固定版
    if best:
        print(f"     能扛 {budget:>2}% 回撤 ⇒ 最优是 {best[0]*100:>3.0f}% 档"
              f"   几何年化 {best[1]:>5.1f}%   实际回撤 {best[2]*100:>6.1f}%"
              f"   门槛 {best[3]:>5.1f}U")

print()
print("=" * 96)
print("  五、结论")
print("=" * 96)
print()
print("  ⚠️ 这里有一个【无法回避】的结构性事实：")
print()
print("     收益越高 ⇒ 仓位越大 ⇒ 需要的杠杆越高 ⇒ 强平线越近")
print("     而 3x 是唯一能支撑 60%/40%/25% 档的杠杆")
print("     ⇒ 想要 60% 档的收益，就必须接受 3x 的强平风险")
print()
print("  两个可能的出路：")
print("     ① 用 15% 档 + 2x：结构上安全，但年化只有 "
      f"{[r for r in RET if r[0]==0.15][0][3]:.1f}%")
print("     ② 60% 档 + 每日追加保证金：保留收益，但要占用大量资金")
print("        （且仓位 > 1.98x 时仍做不到）")
