"""
当前仓位的保证金分析（margin_check.py）
====================================
目标：强平距离 50%
公式：强平距离 = 保证金/名义 − MMR
      ⇒ 保证金 = 名义 × (50% + MMR)
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from binance_api import BN

MMR = 0.004
TARGET = 0.50

bn = BN()
p = [x for x in bn.positions() if float(x.get("positionAmt", 0)) != 0]
if not p:
    print("  当前无持仓")
    sys.exit(0)
p = p[0]

qty = abs(float(p["positionAmt"]))
entry = float(p["entryPrice"])
mark = float(p["markPrice"])
lev = float(p["leverage"])
liq = float(p["liquidationPrice"])
im = float(p.get("isolatedMargin", 0))
notional = float(p.get("notional", 0)) or qty * mark
upnl = float(p.get("unRealizedProfit", 0))
pos_margin = float(p.get("positionInitialMargin", 0))
maint = float(p.get("maintMargin", 0))

acct = bn.futures_account()
wallet = float(acct["totalWalletBalance"])
avail = float(acct["availableBalance"])
total_im = float(acct["totalInitialMargin"])
total_mm = float(acct["totalMaintMargin"])
equity = float(acct["totalMarginBalance"])

print("=" * 84)
print("  当前持仓")
print("=" * 84)
print()
print(f"  数量              {qty} ETH")
print(f"  开仓均价          {entry:,.2f}")
print(f"  标记价            {mark:,.2f}")
print(f"  名义价值          {notional:,.2f} USDT")
print(f"  杠杆设置          {lev:.0f}x")
print(f"  保证金模式        {p.get('marginType', '?')} / {p.get('isolated', '?')}")
print(f"  逐仓保证金        {im:,.4f} USDT")
print(f"  未实现盈亏        {upnl:+,.4f} USDT")
print(f"  维持保证金        {maint:,.4f} USDT")
print(f"  币安报的强平价    {liq:,.2f}")

print()
print("  账户：")
print(f"     钱包余额      {wallet:,.4f} USDT")
print(f"     账户权益      {equity:,.4f} USDT")
print(f"     可用余额      {avail:,.4f} USDT")
print(f"     总起始保证金  {total_im:,.4f} USDT")
print(f"     总维持保证金  {total_mm:,.4f} USDT")

print()
print("=" * 84)
print("  当前强平距离")
print("=" * 84)
print()
cur_dist = (im / (qty * entry) - MMR) if qty > 0 and entry > 0 else 0
cur_dist_px = entry * (1 - cur_dist) if entry > 0 else 0
print(f"  按公式算：保证金/名义 − MMR = {im:,.4f}/{qty*entry:,.2f} − {MMR}")
print(f"           = {im/(qty*entry):.6f} − {MMR} = {cur_dist*100:.2f}%")
print(f"  ⇒ 强平价 {cur_dist_px:,.2f}（币安报 {liq:,.2f}）")
print()
print(f"  ⚠️ 相对【开仓价】下跌 {cur_dist*100:.1f}% 就被强平")
print(f"     当前标记价距强平 {(mark/cur_dist_px-1)*100:.1f}%")

print()
print("=" * 84)
print(f"  要达到 {TARGET*100:.0f}% 还需要多少保证金")
print("=" * 84)
print()
print(f"  公式：保证金 = 名义 × ({TARGET*100:.0f}% + MMR)")
print(f"       = {qty*entry:,.2f} × {TARGET+MMR}")
need_margin = qty * entry * (TARGET + MMR)
print(f"       = {need_margin:,.4f} USDT")
print()
print(f"  当前保证金    {im:,.4f} USDT")
print(f"  需要保证金    {need_margin:,.4f} USDT")
print(f"  ─────────────────────────")
add = need_margin - im
if add > 0:
    print(f"  还需追加      {add:,.4f} USDT")
else:
    print(f"  ✅ 已足够，多出 {-add:,.4f} USDT")

print()
print(f"  可用余额      {avail:,.4f} USDT")
if add > 0:
    if add <= avail:
        print(f"  ⇒ ✅ 可用余额够（加完剩 {avail-add:,.4f} USDT）")
    else:
        print(f"  ⇒ 🔴 可用余额不够（还差 {add-avail:,.4f} USDT）")

print()
print("=" * 84)
print("  加完之后的强平距离")
print("=" * 84)
print()
new_dist = (need_margin / (qty * entry) - MMR)
new_liq = entry * (1 - new_dist)
print(f"  保证金      {need_margin:,.4f} USDT")
print(f"  有效杠杆    名义/保证金 = {qty*entry/need_margin:.4f}x")
print(f"  强平距离    {new_dist*100:.2f}%")
print(f"  强平价      {new_liq:,.2f}")
print(f"  ⇒ 相对开仓价下跌 {new_dist*100:.1f}% 才强平")
print()
print(f"  对比历史最坏盘中逆向 −44.8%（2021-05-19）")
print(f"     {'✅ 安全（余量 +%.1fpp）' % ((new_dist-0.448)*100) if new_dist > 0.448 else '🔴 仍会被打穿'}")
print()
print(f"  对比史上最差单日跌幅 −25.6%（2021-09-07，平静期闪崩）")
print(f"     {'✅ 安全' if new_dist > 0.256 else '🔴 危险'}")

print()
print("=" * 84)
print("  几个目标距离的对照")
print("=" * 84)
print()
print(f"  {'目标距离':>10}{'需要保证金':>14}{'还需追加':>13}{'有效杠杆':>11}"
      f"{'占比权益':>11}")
print("  " + "-" * 62)
for t in (0.30, 0.40, 0.45, 0.50, 0.55, 0.60):
    m = qty * entry * (t + MMR)
    d = m - im
    print(f"  {t*100:>9.0f}%{m:>13.2f}U{d:>+12.2f}U{qty*entry/m:>10.2f}x"
          f"{m/equity*100:>10.1f}%")
