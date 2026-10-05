"""
门槛的真正检验（check_thresholds.py）
==================================
门槛定义：本金 × 有仓位日的第 10 分位仓位 ≥ 20U
必须用【历史分布】算，不能用某一个固定的 rvol。
"""
import collections
import importlib.util
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("m", HERE / "ma50_live.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

C = np.array([b["c"] for b in json.loads(
    (ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))], float)
n = len(C)
r = np.zeros(n)
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(n, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
sig = np.nan_to_num((C > ma).astype(float))
vol20 = np.full(n, np.nan)
for i in range(21, n):
    vol20[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)

print("=" * 90)
print("  门槛检验：本金 × 有仓位日第10分位仓位 ≥ 20U ？")
print("=" * 90)
print()
print(f"  {'版本':<20}{'门槛(文档)':>11}{'第10分位仓位':>14}{'门槛×十分位':>14}"
      f"{'够20U吗':>10}{'90%日子可下单':>14}")
print("  " + "-" * 84)

allok = True
for name, tv, need, sr, dd in m.METHODS:
    if tv is None:
        # 固定版：仓位 = max(1.0, 20/权益)，在门槛处恰好 = 20/need
        pos = np.nan_to_num(sig * max(1.0, m.MIN_NOTIONAL / need))
    else:
        pos = np.nan_to_num(sig * np.clip(tv / vol20, 0, 3))
    on = pos[60:][pos[60:] > 0]
    p10 = np.percentile(on, 10)
    notion = need * on
    frac = (notion >= m.MIN_NOTIONAL).mean() * 100
    enough = need * p10 >= m.MIN_NOTIONAL - 0.05
    allok &= enough
    print(f"  {name:<20}{need:>10.1f}U{p10:>14.4f}{need*p10:>13.2f}U"
          f"{'✅' if enough else '❌':>10}{frac:>13.1f}%")

print()
print("=" * 90)
print("  交叉验证：门槛是否等于 20 ÷ 第10分位")
print("=" * 90)
print()
for name, tv, need, sr, dd in m.METHODS:
    if tv is None:
        pos = np.nan_to_num(sig * max(1.0, m.MIN_NOTIONAL / need))
    else:
        pos = np.nan_to_num(sig * np.clip(tv / vol20, 0, 3))
    on = pos[60:][pos[60:] > 0]
    p10 = np.percentile(on, 10)
    calc = m.MIN_NOTIONAL / p10
    print(f"  {name:<20}20 ÷ {p10:.4f} = {calc:>6.1f}U   文档写 {need:>6.1f}U   "
          f"{'✅' if abs(calc-need) < 0.3 else '⚠️ 差 %.1f' % abs(calc-need)}")

print()
print("=" * 90)
print("  固定版门槛的特殊性")
print("=" * 90)
print()
print("  固定版的仓位公式是 max(1.0, 20/权益)，所以：")
print("    · 权益 < 20U 时，仓位 = 20/权益 ⇒ 名义恒为 20.00U ⇒ 十分位 = 20/权益")
print("    · 门槛处：20 × (20/门槛) = 20 ⇒ 恒成立，与门槛值无关")
print("    · 所以固定版的门槛【不能】用这个公式验，它由另一个条件定：")
print("      「被迫杠杆 ≤ 该版本允许的上限」—— 即权益 ≥ 20/1.4085 = 14.20U")
print()
crit = m.MIN_NOTIONAL / m.fixed_position(m.METHODS[0][2] - 0.01)
print(f"  验证：门槛 14.2U 处的被迫杠杆 = 20 ÷ 14.2 = {m.MIN_NOTIONAL/14.2:.4f}x")
print(f"        METHODS[0][2] = {m.METHODS[0][2]}U")

print()
print("=" * 90)
print(f"  结论：{'✅ 全部通过' if allok else '❌ 有门槛不成立'}")
print("=" * 90)
