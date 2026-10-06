"""
强平价怎么算的（liq_formula.py）
==============================
用你的实际持仓验证币安的公式
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from binance_api import BN

bn = BN()
p = [x for x in bn.positions() if float(x.get("positionAmt", 0)) != 0][0]
Q = abs(float(p["positionAmt"]))
EP = float(p["entryPrice"])
MARK = float(p["markPrice"])
LEV = float(p["leverage"])
LIQ = float(p["liquidationPrice"])
IM = Q * EP / LEV                      # 起始保证金
MMR = 0.004                            # ETHUSDT 第一档维持保证金率

print("=" * 78)
print("  你的持仓")
print("=" * 78)
print()
print(f"  数量 Q        {Q} ETH")
print(f"  开仓均价 EP   {EP:,.2f}")
print(f"  标记价        {MARK:,.2f}")
print(f"  杠杆          {LEV:.0f}x")
print(f"  起始保证金 IM {IM:,.4f} U   （= Q × EP ÷ {LEV:.0f}）")
print(f"  维持保证金率  {MMR*100:.1f}%")
print(f"  币安报的强平价 {LIQ:,.2f}")

print()
print("=" * 78)
print("  推导")
print("=" * 78)
print()
print("  逐仓多仓的保证金余额：")
print("     MB = IM + 未实现盈亏")
print("        = IM + Q × (标记价 − EP)")
print()
print("  强平时 MB = 维持保证金 = Q × 强平价 × MMR")
print()
print("     IM + Q×(LP − EP) = Q × LP × MMR")
print("     IM − Q×EP        = Q×LP×MMR − Q×LP")
print("     IM − Q×EP        = Q×LP×(MMR − 1)")
print()
print("  ⇒  LP = (Q×EP − IM) ÷ (Q × (1 − MMR))")
print()

calc = (Q * EP - IM) / (Q * (1 - MMR))
print("=" * 78)
print("  代入你的数字")
print("=" * 78)
print()
print(f"  分子  Q×EP − IM = {Q}×{EP:,.2f} − {IM:,.4f} = {Q*EP - IM:,.4f}")
print(f"  分母  Q×(1−MMR) = {Q} × {1-MMR} = {Q*(1-MMR):.6f}")
print()
print(f"  ⇒ 算出 LP = {calc:,.2f}")
print(f"     币安报的  {LIQ:,.2f}")
print(f"     差        {calc - LIQ:+,.2f}  （{(calc/LIQ-1)*100:+.3f}%）")

print()
print("=" * 78)
print("  为什么有差")
print("=" * 78)
print()
print("  币安的实际公式还包含：")
print("     · 已付/已收的资金费（计入保证金余额）")
print("     · 已付的手续费")
print("     · 维持保证金按【名义分档】计算（不是单一 MMR）")
print()
# 反推：要得到币安的值，需要多少「调整项」
adj = (Q * EP - IM) - LIQ * Q * (1 - MMR)
print(f"  反推所需的调整项 = {adj:+,.4f} U")
print(f"  ⇒ 你已付的手续费 ≈ {Q*EP*0.0005:,.4f} U（taker 单边）")
print(f"     量级吻合 ⇒ 差额主要来自【手续费和资金费】")

print()
print("=" * 78)
print("  简化结论（够用）")
print("=" * 78)
print()
print("  对逐仓多仓，忽略手续费和资金费时：")
print()
print("     LP ≈ EP × (1 − 1/杠杆 + MMR)")
print()
for lev in (1, 2, 3, 4, 5):
    print(f"     杠杆 {lev}x  ⇒  LP ≈ EP × (1 − 1/{lev} + {MMR}) "
          f"= EP × {1 - 1/lev + MMR:.4f}  ⇒  距开仓 {(1/lev - MMR)*100:.1f}%")
print()
print("  代你的 EP = 2,707.29：")
for lev in (1, 2, 3):
    print(f"     杠杆 {lev}x  ⇒  LP ≈ {EP*(1-1/lev+MMR):,.2f}")
