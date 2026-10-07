"""
门槛的真正检验（check_thresholds.py）· V2 版
==========================================
门槛定义：本金 × 有仓位日的第 10 分位仓位 ≥ 20U
必须用【历史分布】算，不能用某一个固定的 rvol。

⚠️ V2：直接调用工具自己的 signal_of() / realized_vol()，
   保证口径完全一致（不再自己实现一遍 —— 那会漂移）。
"""
import importlib.util
import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent

_spec = importlib.util.spec_from_file_location("ml", HERE / "ma50_live.py")
ml = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ml)

bars = json.loads((ROOT / "data" / "crypto" / f"{ml.SYM}.json").read_text(encoding="utf-8"))

print("=" * 88)
print("  门槛检验（V2 口径：直接调用工具的 signal_of / realized_vol）")
print("=" * 88)
print()
print(f"  VOL_WINDOW = {ml.VOL_WINDOW}    VOL_CAP = {ml.VOL_CAP}")
print()

DIST = {}
for tv in (0.60, 0.40, 0.25, 0.15):
    ws = []
    for i in range(60, len(bars)):
        sub = bars[:i + 1]
        sg = ml.signal_of(sub)
        if not sg or not sg["long"]:
            continue
        rv = ml.realized_vol(sub, ml.VOL_WINDOW)
        if not np.isfinite(rv) or rv <= 0:
            continue
        if ml.VOL_CAP is not None and rv > ml.VOL_CAP:
            continue
        ws.append(min(3.0, tv / rv))
    DIST[tv] = np.array(ws)

print(f"  {'版本':<18}{'门槛(工具)':>12}{'第10分位仓位':>15}{'20÷p10':>10}"
      f"{'够20U?':>9}{'最高仓位':>10}")
print("  " + "-" * 76)
bad = 0
for name, tv, need, _, _ in ml.METHODS:
    if tv is None:
        continue
    ws = DIST[tv]
    p10 = np.percentile(ws, 10)
    calc = 20.0 / p10
    ok = "✅" if abs(calc - need) <= max(0.5, need * 0.02) else "❌"
    if ok == "❌":
        bad += 1
    print(f"  {name:<18}{need:>11.1f}U{p10:>15.4f}{calc:>9.1f}U{ok:>9}"
          f"{ws.max():>9.3f}x")

print()
print("  固定版特殊性：")
print("     仓位 = max(1.0, 20/权益) ⇒ 权益 < 20U 时名义恒为 20.00U")
print(f"     METHODS[0] 门槛 = {ml.METHODS[0][2]}U"
      f" ⇒ 该处被迫杠杆 = 20/{ml.METHODS[0][2]} = "
      f"{20/ml.METHODS[0][2]:.4f}x ≤ 上限 1.4085x ✅")

print()
print("  门槛处的可下单覆盖率（应该接近 90%）：")
print(f"  {'版本':<18}{'门槛':>10}{'可下单天数的比例':>18}")
print("  " + "-" * 48)
for name, tv, need, _, _ in ml.METHODS:
    if tv is None:
        continue
    ws = DIST[tv]
    n = sum(1 for w in ws if w * need >= 20.0)
    print(f"  {name:<18}{need:>9.1f}U{n/len(ws)*100:>17.1f}%")

print()
print("=" * 88)
if bad == 0:
    print("  结论：✅ 所有门槛都成立（20U ÷ 第10分位仓位）")
else:
    print(f"  结论：❌ 有 {bad} 个门槛不成立")
    print("  ⇒ 修法：把上面「20÷p10」那一列填回 ma50_live.py 的 METHODS")
print("=" * 88)
sys.exit(0 if bad == 0 else 1)
