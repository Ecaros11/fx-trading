"""
逐仓杠杆只能增不能减 —— 实际影响（leverage_stuck.py）
==================================================
关键问题：杠杆设置降不下来，会影响什么？
  · 盈亏：由【仓位】决定，与杠杆设置无关
  · 强平：由【杠杆设置】决定
  · 保证金：由【杠杆设置】决定
"""
import importlib.util
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
HERE = pathlib.Path(__file__).parent
_x = importlib.util.spec_from_file_location("m", HERE / "ma50_live.py")
m = importlib.util.module_from_spec(_x)
_x.loader.exec_module(m)

raw = json.loads((ROOT / "data/crypto/ETHUSDT.json").read_text(encoding="utf-8"))
C = np.array([b["c"] for b in raw], float)
nn = len(C)
r = np.zeros(nn)
r[1:] = C[1:] / C[:-1] - 1
vol = np.full(nn, np.nan)
for i in range(21, nn):
    vol[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)
v = vol[np.isfinite(vol)]

print("=" * 84)
print("  ① 各档位实际需要的最大杠杆（ceil(最高仓位)）")
print("=" * 84)
print()
print(f"  {'档位':>8}{'波动最低':>12}{'最高仓位':>12}{'需要杠杆':>12}{'触发3x上限':>12}")
print("  " + "-" * 58)
for name, tv, need, sh, dd in m.METHODS:
    if tv is None:
        continue
    raw_pos = tv / v
    mx = raw_pos.max()
    cap_days = int((raw_pos >= 3.0).sum())
    print(f"  {tv*100:>7.0f}%{v.min()*100:>11.1f}%{min(3.0,mx):>12.3f}x"
          f"{int(np.ceil(min(3.0,mx))):>11}x{cap_days:>12}")

print()
print(f"  数据里 20 日波动最低 {v.min()*100:.1f}%，最高 {v.max()*100:.1f}%，"
      f"中位 {np.median(v)*100:.1f}%")

print()
print("=" * 84)
print("  ② 杠杆设置降不下来的影响（以你的 90U / 40% 档为例）")
print("=" * 84)
print()
EQ = 90.0
print(f"  {'情形':<26}{'仓位':>9}{'名义':>10}{'杠杆':>7}{'保证金':>10}"
      f"{'富余':>9}{'强平@标的':>11}")
print("  " + "-" * 84)
for lab, w, lev in (("今天算出来（40% 档）", 1.035, 2),
                    ("权益涨到 200U 后（15% 档）", 0.19, 1),
                    ("但杠杆降不下来 → 仍是", 0.19, 2),
                    ("权益涨到 500U 后（15% 档）", 0.077, 1),
                    ("但杠杆降不下来 → 仍是", 0.077, 2)):
    eq = 90.0 if "今天" in lab else (200.0 if "200U" in lab else (200.0 if "降不下来 → 仍是" in lab and "500" not in lab else 500.0))
    notion = eq * w
    marg = notion / lev
    liq = (1 / lev - 0.004) * 100
    print(f"  {lab:<26}{w:>8.3f}x{notion:>9.2f}U{lev:>6}x{marg:>9.2f}U"
          f"{eq-marg:>8.2f}U{liq:>10.1f}%")

print()
print("=" * 84)
print("  ③ 安全问题：杠杆 ≤ 多少才安全")
print("=" * 84)
print()
WORST = abs(m.liq_acc_pct) if hasattr(m, "liq_acc_pct") else 0.266
print(f"  历史最坏单笔逆向（标的）  {abs(m.WORST_TRADE_LOW)*100:.1f}%")
print()
print(f"  {'杠杆':>6}{'强平@标的':>12}{'余量':>10}{'安全?':>8}")
print("  " + "-" * 38)
for lev in (1, 2, 3, 4, 5):
    liq = (1 / lev - 0.004) * 100
    margin = liq - abs(m.WORST_TRADE_LOW) * 100
    print(f"  {lev:>5}x{liq:>11.1f}%{margin:>+9.1f}pp"
          f"{'✅' if margin > 0 else '🔴':>8}")

print()
print("  ⇒ 结论：")
print("     杠杆降到 1x 的【唯一好处】是强平更远（−99.6% vs −49.6%）")
print("     而 2x 的 −49.6% 对最坏单笔 −26.6% 仍有 23.0pp 余量 ⇒ 安全")
print("     ⇒ 所以降不下来【不影响安全性】，只影响保证金占用")
