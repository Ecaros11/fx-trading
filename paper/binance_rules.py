"""查币安 ETHUSDT 永续的实际下单约束（binance_rules.py）"""
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from binance_api import BN

bn = BN()
info = bn.fapi("/fapi/v1/exchangeInfo", {}, signed=False)
sym = [s for s in info["symbols"] if s["symbol"] == "ETHUSDT"][0]

print("=" * 76)
print("  币安 ETHUSDT 永续 · 下单约束")
print("=" * 76)
print()
for f in sym["filters"]:
    t = f["filterType"]
    if t == "LOT_SIZE":
        print(f"  LOT_SIZE        minQty={f['minQty']}  maxQty={f['maxQty']}  "
              f"stepSize={f['stepSize']}")
    elif t == "MARKET_LOT_SIZE":
        print(f"  MARKET_LOT_SIZE minQty={f['minQty']}  stepSize={f['stepSize']}")
    elif t == "MIN_NOTIONAL":
        print(f"  MIN_NOTIONAL    notional={f['notional']}")
print()
print(f"  quantityPrecision  {sym['quantityPrecision']}")
print(f"  pricePrecision     {sym['pricePrecision']}")

px = float(bn.fapi("/fapi/v1/ticker/price", {"symbol": "ETHUSDT"}, signed=False)["price"])
print(f"  现价               {px:,.2f}")

mq = float([f for f in sym["filters"] if f["filterType"] == "LOT_SIZE"][0]["minQty"])
mn = float([f for f in sym["filters"] if f["filterType"] == "MIN_NOTIONAL"][0]["notional"])
ss = float([f for f in sym["filters"] if f["filterType"] == "LOT_SIZE"][0]["stepSize"])

print()
print("=" * 76)
print("  实际最小下单量")
print("=" * 76)
print()
print(f"  minQty 直接限制        {mq} ETH   ⇒ 名义 {mq*px:,.2f}U")
print(f"  minNotional 限制       {mn} USDT  ⇒ 需要 {mn/px:.5f} ETH")
print()
eff = max(mq, mn / px)
print(f"  ⇒ 实际最小可下单       {eff:.5f} ETH")
print(f"     （向上取到 stepSize={ss} 的整数倍 ⇒ {(int(eff/ss)+1)*ss:.3f} ETH）")
print()
print("=" * 76)
print("  你的情况")
print("=" * 76)
print()
EQ, CUR, TGT = 89.5712, 0.021, 0.0342
print(f"  当前持有   {CUR} ETH")
print(f"  目标       {TGT:.4f} ETH")
print(f"  差额       {TGT-CUR:+.4f} ETH")
print()
print(f"  {'买入量':>10}{'持有':>10}{'偏离目标':>12}{'名义':>12}{'能下单?':>10}")
print("  " + "-" * 56)
for buy in (0.013, 0.014):
    have = CUR + buy
    dev = have - TGT
    notion = buy * px
    ok = "✅" if (buy >= eff - 1e-9 and notion >= mn) else "🔴"
    print(f"  {buy:>10.3f}{have:>10.4f}{dev:>+11.4f}{notion:>11.2f}U{ok:>10}")
print()
print("  ⇒ 买 0.013 更接近目标（偏离 +0.0002 vs +0.0008）")
