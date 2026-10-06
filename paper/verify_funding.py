"""
核对资金费：本地缓存 vs API 实拉 vs 账户实际扣款（verify_funding.py）
==================================================================
"""
import collections
import datetime as dt
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from binance_api import BN

bn = BN()
SYM = "ETHUSDT"

# ── ① 本地缓存 ──
loc = json.loads((ROOT / "data" / "funding" / f"{SYM}.json").read_text(encoding="utf-8"))
locmap = {int(x["t"]): float(x["rate"]) for x in loc}
print("=" * 84)
print("  ① 本地资金费缓存")
print("=" * 84)
print()
print(f"  条数      {len(loc)}")
print(f"  最早      {dt.datetime.fromtimestamp(loc[0]['t']/1000, dt.UTC):%Y-%m-%d %H:%M}")
print(f"  最新      {dt.datetime.fromtimestamp(loc[-1]['t']/1000, dt.UTC):%Y-%m-%d %H:%M}")
print(f"  间隔      {int((loc[-1]['t']-loc[0]['t'])/1000/(len(loc)-1)/3600)} 小时")

# ── ② 从 API 现拉最近 100 条 ──
print()
print("=" * 84)
print("  ② 从 API 现拉最近 100 条")
print("=" * 84)
print()
api = bn.fapi("/fapi/v1/fundingRate", {"symbol": SYM, "limit": 100}, signed=False)
apimap = {int(x["fundingTime"]): float(x["fundingRate"]) for x in api}
print(f"  条数      {len(api)}")
print(f"  最早      {dt.datetime.fromtimestamp(api[0]['fundingTime']/1000, dt.UTC):%Y-%m-%d %H:%M}")
print(f"  最新      {dt.datetime.fromtimestamp(api[-1]['fundingTime']/1000, dt.UTC):%Y-%m-%d %H:%M}")

# ── ③ 逐条对比 ──
print()
print("=" * 84)
print("  ③ 逐条对比（API 的每一条，本地有没有、值是否一致）")
print("=" * 84)
print()
miss = diff = same = 0
for t, r in sorted(apimap.items()):
    if t not in locmap:
        miss += 1
        if miss <= 5:
            print(f"  🔴 本地缺失  {dt.datetime.fromtimestamp(t/1000, dt.UTC):%Y-%m-%d %H:%M}"
                  f"  API={r*100:+.5f}%")
    elif abs(locmap[t] - r) > 1e-12:
        diff += 1
        if diff <= 5:
            print(f"  🔴 值不一致  {dt.datetime.fromtimestamp(t/1000, dt.UTC):%Y-%m-%d %H:%M}"
                  f"  本地={locmap[t]*100:+.5f}%  API={r*100:+.5f}%")
    else:
        same += 1
print()
print(f"  一致 {same}   缺失 {miss}   值不同 {diff}   （API 共 {len(apimap)} 条）")

# ── ④ 账户实际资金费 ──
print()
print("=" * 84)
print("  ④ 你账户的实际资金费扣款（/fapi/v1/income type=FUNDING_FEE）")
print("=" * 84)
print()
inc = bn.fapi("/fapi/v1/income",
              {"symbol": SYM, "incomeType": "FUNDING_FEE", "limit": 1000},
              signed=True)
print(f"  记录数    {len(inc)}")
if inc:
    inc = sorted(inc, key=lambda x: int(x["time"]))
    tot = sum(float(x["income"]) for x in inc)
    print(f"  合计      {tot:+.6f} USDT")
    print(f"  最早      {dt.datetime.fromtimestamp(inc[0]['time']/1000, dt.UTC):%Y-%m-%d %H:%M}")
    print(f"  最新      {dt.datetime.fromtimestamp(inc[-1]['time']/1000, dt.UTC):%Y-%m-%d %H:%M}")
    print()
    print(f"  {'时间（UTC）':<20}{'费率':>12}{'金额':>14}")
    print("  " + "-" * 48)
    for x in inc[-12:]:
        t = int(x["time"])
        r = apimap.get(t)
        rs = f"{r*100:+.5f}%" if r is not None else "（不在 API 100 条内）"
        print(f"  {dt.datetime.fromtimestamp(t/1000, dt.UTC):%Y-%m-%d %H:%M}   "
              f"{rs:>12}{float(x['income']):>+13.6f}")

# ── ⑤ 验证公式：费率 × 名义 ──
print()
print("=" * 84)
print("  ⑤ 验证：实际扣款 = 费率 × 当时的持仓名义？")
print("=" * 84)
print()
if inc:
    for x in inc[-4:]:
        t = int(x["time"])
        amt = float(x["income"])
        r = apimap.get(t)
        if r:
            notion = amt / r
            print(f"  {dt.datetime.fromtimestamp(t/1000, dt.UTC):%Y-%m-%d %H:%M}"
                  f"   费率 {r*100:+.5f}%   扣款 {amt:+.6f}U"
                  f"   ⇒ 反推名义 {notion:,.2f}U")
    print()
    print("  ⇒ 若反推的名义与你当时的持仓一致，说明【费率 × 名义】这个公式是对的")
