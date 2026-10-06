"""
澄清：改杠杆设置 vs 改仓位（clarity.py）
=====================================
币安的核心事实：杠杆设置【不改变持仓数量】，只决定占用多少保证金
"""
import sys
import pathlib

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from binance_api import BN

bn = BN()
a = bn.futures_account()
eq = float(a["totalMarginBalance"])
ps = [p for p in bn.positions() if float(p.get("positionAmt", 0)) != 0]

print("=" * 86)
print("  你的真实持仓")
print("=" * 86)
print()
print(f"  账户权益   {eq:,.4f} USDT")
for p in ps:
    amt = abs(float(p["positionAmt"]))
    mark = float(p.get("markPrice", 0))
    ep = float(p["entryPrice"])
    lev = p.get("leverage")
    notion = amt * mark
    print(f"  持仓数量   {amt} ETH")
    print(f"  名义价值   {notion:,.2f} USDT")
    print(f"  杠杆设置   {lev}x")
    print(f"  占用保证金 {notion/float(lev):,.2f} USDT")
    print(f"  实际仓位   {notion/eq:.4f}x      ← 名义 ÷ 权益")
    print()

print("=" * 86)
print("  ① 如果只把杠杆从 1x 改成 2x（不动仓位）")
print("=" * 86)
print()
if ps:
    p = ps[0]
    amt = abs(float(p["positionAmt"]))
    mark = float(p.get("markPrice", 0))
    notion = amt * mark
    print(f"  {'':<16}{'改之前':>14}{'改之后':>14}{'变化':>14}")
    print("  " + "-" * 58)
    print(f"  {'持仓数量':<16}{amt:>13} {amt:>13}   {'不变':>12}")
    print(f"  {'名义价值':<16}{notion:>13,.2f} {notion:>13,.2f}   {'不变':>12}")
    print(f"  {'杠杆设置':<16}{'1x':>14}{'2x':>14}   {'—':>12}")
    print(f"  {'占用保证金':<16}{notion/1:>13,.2f} {notion/2:>13,.2f}"
          f"   {notion/2-notion:>+11,.2f}")
    print(f"  {'实际仓位':<16}{notion/eq:>13.4f}x{notion/eq:>13.4f}x   {'不变':>12}")
    print()
    print("  ⇒ 改杠杆【只释放保证金】，仓位一分钱没变")
    print(f"  ⇒ 释放 {notion/2:,.2f} U 的可用余额（可以用来加仓）")

print()
print("=" * 86)
print("  ② 40% 档要求多少（工具算的）")
print("=" * 86)
print()
tv = 0.40
vol = 0.387
pos = min(3.0, tv / vol)
tgt_n = eq * pos
print(f"  20 日已实现波动   {vol*100:.1f}%")
print(f"  目标仓位          min(3, 40% ÷ 38.7%) = {pos:.4f}x")
print(f"  目标名义          {tgt_n:,.2f} USDT")
if ps:
    cur = abs(float(ps[0]["positionAmt"])) * float(ps[0].get("markPrice", 0))
    print(f"  当前名义          {cur:,.2f} USDT")
    print()
    print(f"  ⇒ 差额            {tgt_n - cur:+,.2f} USDT")
    print(f"  ⇒ 你应该【{'加仓' if tgt_n > cur else '减仓'}】"
          f"{abs(tgt_n-cur)/float(ps[0].get('markPrice',1)):.4f} ETH")
    print()
    print(f"  ⚠️ 注意：这不是平仓重开，是{'买入' if tgt_n > cur else '卖出'}差额")

print()
print("=" * 86)
print("  ③ 关键：两件事是独立的")
print("=" * 86)
print()
print("  方向 A：改【杠杆设置】")
print("     目的 = 决定占用多少保证金 / 强平多远")
print("     手段 = 币安上点「调整杠杆」")
print("     ⇒ 不改变持仓数量")
print("     ⚠️ 逐仓只能调高不能调低")
print()
print("  方向 B：改【仓位数量】")
print("     目的 = 跟随规则（40% 档要求多少就持多少）")
print("     手段 = 买入/卖出差额")
print("     ⇒ 不改变杠杆设置")
print()
print("  ⇒ 你的问题「每次算出来要更少都得平一部分？」")
print("     答：是，但那是【方向 B】（卖出一部分），不是平仓重开。")
print("         杠杆设置完全不用动。")
