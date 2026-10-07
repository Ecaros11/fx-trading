"""
杠杆取整的影响（leverage_integer.py）
===================================
币安只允许整数杠杆，所以 1.3486x 设不了。

关键区分：
  · 杠杆【设置】 = 决定占用多少保证金 + 强平距离
  · 仓位【大小】 = 由名义价值决定，与杠杆设置无关

所以正确做法是：杠杆设置取 ceil(目标仓位)，仓位仍按名义下。
"""
import numpy as np

MIN_NOTIONAL = 20.0
MMR = 0.004          # ETHUSDT 第一档维持保证金率

print("=" * 92)
print("  币安 ETHUSDT 永续 · 杠杆设置与仓位的关系")
print("=" * 92)
print()
print("  公式：")
print("     保证金占用 = 名义价值 ÷ 杠杆设置")
print("     强平距离(标的) = 1/杠杆 − 维持保证金率")
print("     账户回撤 = 强平距离 × 名义 ÷ 权益")
print()

print("=" * 92)
print("  各权益下的【最小可设杠杆】与后果")
print("=" * 92)
print()
print(f"  {'权益':>9}{'目标仓位':>10}{'最小整数杠杆':>13}{'名义':>9}{'保证金':>9}"
      f"{'富余':>9}{'强平@标的':>11}{'账户回撤@强平':>14}")
print("  " + "-" * 86)
for eq in (14.20, 14.83, 16, 18, 20, 25, 30, 47.5):
    if eq < 14.20:
        continue
    pos = max(1.0, MIN_NOTIONAL / eq) if eq < 47.5 else 0.899
    notion = eq * pos
    need_lev = notion / eq                    # 使保证金恰好 = 权益
    lev = int(np.ceil(need_lev - 1e-9))       # 最小整数杠杆
    lev = max(1, lev)
    margin = notion / lev
    spare = eq - margin
    liq_px = 1 / lev - MMR                    # 标的跌幅
    acc_dd = liq_px * notion / eq             # 账户跌幅
    print(f"  {eq:>8.2f}U{pos:>10.4f}{lev:>12}x{notion:>8.2f}U{margin:>8.2f}U"
          f"{spare:>8.2f}U{liq_px*100:>10.1f}%{acc_dd*100:>13.1f}%")

print()
print("=" * 92)
print("  🔴 关键：强平与历史最大回撤谁先到（权益 14.83，名义 20.00）")
print("=" * 92)
print()
eq, notion = 14.83, 20.00
HIST_DD = -0.689          # 工具报的历史最大回撤
print(f"  权益 {eq:.2f}U   名义 {notion:.2f}U   历史最大回撤 {HIST_DD*100:.1f}%")
print(f"  ⇒ 历史最坏时账户跌到 {eq*(1+HIST_DD):.2f}U")
print()
print(f"  {'模式':<10}{'杠杆':>6}{'保证金':>10}{'强平@标的':>12}{'账户@强平':>12}"
      f"{'账户回撤':>11}{'跑到历史最坏吗':>16}")
print("  " + "-" * 80)
for mode, lev, backing in (("逐仓", 2, None), ("逐仓", 3, None),
                           ("全仓", 2, eq), ("全仓", 3, eq)):
    if backing is None:
        margin = notion / lev
        spare = 0
    else:
        margin = notion / lev
        spare = eq - margin          # 全仓时富余也做保证金
    total_back = margin + spare
    liq_px = (total_back / notion) - MMR
    acc_at_liq = eq - liq_px * notion
    acc_dd = acc_at_liq / eq - 1
    survive = "✅ 能扛" if acc_dd <= HIST_DD else "❌ 会先强平"
    print(f"  {mode:<10}{lev:>5}x{margin:>9.2f}U{liq_px*100:>11.1f}%"
          f"{acc_at_liq:>11.2f}U{acc_dd*100:>10.1f}%{survive:>16}")

print()
print("=" * 92)
print("  结论")
print("=" * 92)
print(f"""
  ① 币安只允许整数杠杆 ⇒ 1.3486x 设不了，最小是 {int(np.ceil(20/14.83))}x
  ② 但杠杆设置【不改变仓位】—— 仓位由名义 20.00U 决定
  ③ 逐仓 2x：保证金 10.00U，强平在标的 −49.6%（账户 −66.9%）
     ⇒ 比历史最大回撤 −68.9% 更早到 ⇒ 会先被强平，亏掉 10U 保证金
  ④ 全仓 2x：整个账户 14.83U 做保证金，强平在标的 −73.8%
     ⇒ 能扛住 −68.9% 的回撤
""")
