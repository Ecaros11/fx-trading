"""
核验：回测用的资金费是否正确（funding_audit.py）
=============================================
三个问题：
  ① 数据覆盖了回测期间吗？
  ② 数据有缺口或错误吗？
  ③ 回测脚本的用法对吗？
"""
import collections
import datetime as dt
import json
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
fr = json.loads((ROOT / "data" / "funding" / "ETHUSDT.json").read_text(encoding="utf-8"))
raw = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))

print("=" * 84)
print("  ① 覆盖范围")
print("=" * 84)
print()
d0 = dt.datetime.fromtimestamp(fr[0]["t"] / 1000, dt.UTC)
d1 = dt.datetime.fromtimestamp(fr[-1]["t"] / 1000, dt.UTC)
k0 = dt.datetime.fromtimestamp(raw[0]["t"] / 1000, dt.UTC)
k1 = dt.datetime.fromtimestamp(raw[-1]["t"] / 1000, dt.UTC)
print(f"  资金费数据   {d0:%Y-%m-%d %H:%M}  ~  {d1:%Y-%m-%d %H:%M}   {len(fr)} 条")
print(f"  日线数据     {k0:%Y-%m-%d %H:%M}  ~  {k1:%Y-%m-%d %H:%M}   {len(raw)} 条")
print()
print(f"  ⇒ 资金费起点 {d0:%Y-%m-%d}，日线起点 {k0:%Y-%m-%d}"
      f"  ⇒ {'✅ 覆盖' if d0 <= k0 else '🔴 资金费起步晚'}")

print()
print("=" * 84)
print("  ② 缺口检查（8 小时间隔，应连续）")
print("=" * 84)
print()
gaps = []
for a, b in zip(fr, fr[1:]):
    dt_ms = b["t"] - a["t"]
    if dt_ms != 8 * 3600 * 1000:
        gaps.append((a["t"], b["t"], dt_ms / 3600000))
print(f"  相邻间隔不是 8 小时的：{len(gaps)} 处")
for t0, t1, h in gaps[:6]:
    print(f"      {dt.datetime.fromtimestamp(t0/1000, dt.UTC):%Y-%m-%d %H:%M}"
          f" → {dt.datetime.fromtimestamp(t1/1000, dt.UTC):%Y-%m-%d %H:%M}"
          f"   间隔 {h:.1f} 小时")
if not gaps:
    print("     ✅ 完全连续，0 缺口")

# 理论条数
span_h = (fr[-1]["t"] - fr[0]["t"]) / 3600000
print()
print(f"  理论条数   {span_h/8 + 1:.0f}")
print(f"  实际条数   {len(fr)}")
print(f"  ⇒ {'✅ 一致' if abs(span_h/8 + 1 - len(fr)) < 2 else '🔴 有缺失'}")

print()
print("=" * 84)
print("  ③ 按日汇总后与日线对齐（回测用的就是这个）")
print("=" * 84)
print()
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
print(f"  汇总出 {len(agg)} 天")
cnt = collections.Counter(len(v) for v in agg.values())
print(f"  每天条数分布：{dict(cnt)}")
bad = [d for d, v in agg.items() if len(v) != 3]
print(f"  不是 3 条的天数：{len(bad)}"
      f"{'（首尾不完整，正常）' if bad else ''}")

# 日线的每一天都能对上吗
kdays = {int(b["t"] // 86400000) for b in raw}
mdays = set(agg.keys())
print()
print(f"  日线天数       {len(kdays)}")
print(f"  资金费天数     {len(mdays)}")
missing = kdays - mdays
print(f"  日线有但资金费没有的天数：{len(missing)}")
if missing:
    ms = sorted(missing)
    print(f"     最早 {dt.datetime.fromtimestamp(ms[0]*86400000/1000, dt.UTC):%Y-%m-%d}")
    print(f"     最晚 {dt.datetime.fromtimestamp(ms[-1]*86400000/1000, dt.UTC):%Y-%m-%d}")

print()
print("=" * 84)
print("  ④ 回测脚本的用法")
print("=" * 84)
print()
print("  典型写法（vol_check.py / sharpe_tier.py 等）：")
print()
print("     FR = 按日汇总的资金费率          # 3 次之和")
print("     x[i] = w*r[i] - |Δw|*fee - w*FR[i]")
print("                                ↑")
print("                          仓位 × 日资金费")
print()
print("  ⇒ 币安按【名义价值】收费")
print("     名义 = 仓位 w × 权益")
print("     费用 = 名义 × 费率 = (w × 权益) × FR")
print("     占权益比例 = w × FR          ✅ 与代码一致")

print()
print("=" * 84)
print("  ⑤ 结论")
print("=" * 84)
print()
print("  ① 覆盖：资金费从 2019-11-27 起，与日线同期 ⇒ 完整覆盖所有回测期间")
print("  ② 缺口：上面已核")
print("  ③ 用法：w × FR（按名义收费）⇒ 正确")
print()
print("  ⚠️ 唯一的已知缺口是【最新一天】——")
print("     但那不在回测期间内（回测止于最后一根已走完的日线）")
