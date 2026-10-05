"""
1000U 本金下，版本切换抖动的实际损耗（hysteresis_1000.py）
=======================================================
关键：1000U 已经是最顶档（波动率目标 15%，门槛 139.5U）。
      要降档，权益得跌破 139.5U —— 那是 −86%。
所以理论上 1000U 不该有抖动。实测验证。
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

bars = json.loads((ROOT / "data/crypto/ETHUSDT.json").read_text(encoding="utf-8"))
fr = json.loads((ROOT / "data/funding/ETHUSDT.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
fd = {int(k): float(np.sum(v)) for k, v in agg.items()}

print("=" * 86)
print("  各本金下的版本切换（含 1000U）")
print("=" * 86)
print()
print(f"  {'起始本金':>10}{'初始版本':<20}{'最大回撤':>10}{'路径最低':>11}"
      f"{'期末':>11}{'切换次数':>9}")
print("  " + "-" * 74)
for eq in (100, 200, 500, 1000, 2000, 5000):
    d = m.dynamic_drawdown(bars, fd, eq)
    mname = m.pick_method(eq)[0]
    print(f"  {eq:>9.0f}U{mname:<20}{d['dd']*100:>9.1f}%{d['low']:>10.0f}U"
          f"{d['end']:>10.0f}U{d['switches']:>9}")

print()
print("=" * 86)
print("  为什么 1000U 几乎不会切换")
print("=" * 86)
print()
print("  门槛阶梯（越高档要求本金越大，且 139.5U 是【顶档】）：")
for name, tv, need, sr, dd in m.METHODS:
    print(f"     {name:<18}门槛 {need:>6.1f}U")
print()
d = m.dynamic_drawdown(bars, fd, 1000)
print(f"  1000U 的情况：")
print(f"     初始版本        波动率目标 15%（顶档）")
print(f"     路径最低        {d['low']:.0f}U")
print(f"     要降档需跌破    139.5U  ⇒  需跌 {(1-139.5/1000)*100:.0f}%")
print(f"     实际最低 {d['low']:.0f}U ⇒ {'不会降档' if d['low'] > 139.5 else '会降档'}")
print()
print(f"  ⇒ 切换 {d['switches']} 次，额外损耗 {'0' if d['switches']==0 else '见下'}")

print()
print("=" * 86)
print("  如果【手动】制造抖动，每次的成本是多少（1000U 口径）")
print("=" * 86)
print()
print("  最坏情形：在 52.3U 门槛附近反复横跳（那是相对 1000U 的 −95%，不可能）")
print("  现实情形：权益在某个门槛附近震荡，每次切换的仓位跳变")
print()
print(f"  {'权益':>9}{'从':<18}{'到':<18}{'仓位跳变':>10}{'名义变动':>11}"
      f"{'单次成本':>10}{'占权益':>9}")
print("  " + "-" * 82)
for eq in (100, 200, 500, 1000):
    for i in range(len(m.METHODS) - 1):
        a, b = m.METHODS[i], m.METHODS[i + 1]
        if not (b[2] * 0.97 <= eq <= b[2] * 1.03):
            continue
        vol = 0.72          # 举例：20 日已实现波动 72%
        pa = max(1.0, 20 / eq) if a[1] is None else min(3.0, a[1] / vol)
        pb = max(1.0, 20 / eq) if b[1] is None else min(3.0, b[1] / vol)
        dw = abs(pb - pa)
        notion = dw * eq
        cost = notion * 0.0005
        print(f"  {eq:>8.0f}U{a[0]:<18}{b[0]:<18}{dw:>10.4f}{notion:>10.0f}U"
              f"{cost:>9.2f}U{cost/eq*100:>8.3f}%")
