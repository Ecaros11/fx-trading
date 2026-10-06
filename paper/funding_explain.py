"""资金费口径（funding_explain.py）"""
import collections
import datetime as dt
import json
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
SYM = "ETHUSDT"

fr = json.loads((ROOT / "data" / "funding" / f"{SYM}.json").read_text(encoding="utf-8"))
print("=" * 80)
print("  币安资金费机制")
print("=" * 80)
print()
print("  · 结算频率：每 8 小时一次（UTC 00:00 / 08:00 / 16:00）")
print("  · 一天结算 3 次")
print("  · 每次按【持仓名义价值】收，不是按保证金")
print()
print("=" * 80)
print("  原始数据（最后 9 条）")
print("=" * 80)
print()
print(f"  {'时间（UTC）':<22}{'单次费率':>12}")
print("  " + "-" * 36)
for x in fr[-9:]:
    t = dt.datetime.fromtimestamp(x["t"] / 1000, dt.UTC)
    print(f"  {t:%Y-%m-%d %H:%M}      {x['rate']*100:>+10.5f}%")

# 按日汇总
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])

print()
print("=" * 80)
print("  工具用的口径：按【日】汇总（3 次相加）")
print("=" * 80)
print()
print(f"  {'日期':<14}{'3 次分别是':<34}{'日合计':>12}")
print("  " + "-" * 62)
days = list(agg.items())[-4:]
for d, rates in days:
    ds = dt.datetime.fromtimestamp(d * 86400000 / 1000, dt.UTC).strftime("%Y-%m-%d")
    ind = "  ".join(f"{r*100:+.5f}%" for r in rates)
    print(f"  {ds:<14}{ind:<34}{sum(rates)*100:>+10.5f}%")

print()
print("=" * 80)
print("  回答：工具显示的 +0.01972% 是什么")
print("=" * 80)
print()
d, rates = days[-1]
print(f"  最后一天 {dt.datetime.fromtimestamp(d*86400000/1000, dt.UTC):%Y-%m-%d}")
print(f"     3 次结算：{' + '.join(f'{r*100:.5f}%' for r in rates)}")
print(f"     = {sum(rates)*100:.5f}%   ← 工具显示的就是这个【日合计】")
print()
print("  ⇒ 所以它是【当天 3 次之和】，不是单次")
print()

# 成本换算
print("=" * 80)
print("  换成钱（你的持仓）")
print("=" * 80)
print()
NOTION = 91.77
EQ = 89.42
for lab, rate in (("日合计", 0.0001972), ("单次", 0.0001972 / 3)):
    u = NOTION * rate
    print(f"  {lab:<8}{rate*100:>+10.5f}%   ×  名义 {NOTION:.2f}U  =  {u:>7.4f} U/天"
          f"   （占权益 {u/EQ*100:.3f}%）")
print()
print(f"  ⇒ 一年：{NOTION*0.0001972*365:.2f} U（按当前费率和持仓）")
print(f"     占权益 {NOTION*0.0001972*365/EQ*100:.1f}%")
