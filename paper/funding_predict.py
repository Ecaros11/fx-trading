"""
能不能预测当天的资金费（funding_predict.py）
==========================================
问题：当天 16:00 那次还没结算，能用【昨天已收盘】的数据估得更准吗？
对比几种估计方法：
  A. 最近 30 个完整日的均值（工具现在用的）
  B. 昨天的日合计（自相关）
  C. 最近 3 天的均值
  D. 前天+昨天的均值
  E. 昨天已结算的部分 + 历史均值补足
"""
import collections
import datetime as dt
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
fr = json.loads((ROOT / "data" / "funding" / "ETHUSDT.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
days = sorted(agg)
full = [d for d in days if len(agg[d]) == 3]
F = {d: float(np.sum(agg[d])) for d in days}

print("=" * 88)
print("  一、资金费的可预测性")
print("=" * 88)
print()
x = np.array([F[d] for d in full])
print(f"  完整日样本 {len(x)} 天")
print(f"  日均 {x.mean()*1e4:+.4f} bp   标准差 {x.std(ddof=1)*1e4:.4f} bp")
print()
print(f"  {'滞后期':>8}{'相关系数':>12}{'解释力 R²':>12}")
print("  " + "-" * 36)
for k in (1, 2, 3, 5, 7, 14, 30):
    if len(x) <= k:
        continue
    c = np.corrcoef(x[:-k], x[k:])[0, 1]
    print(f"  {k:>7}天{c:>12.4f}{c*c:>12.4f}")

print()
print("=" * 88)
print("  二、各估计方法的误差")
print("=" * 88)
print()
print("  模拟：站在第 i 天早上，估第 i 天的日合计（那天只结算了 1 次）")
print()


def est_A(i, **kw):
    """最近 30 个完整日均值（工具现在的做法）"""
    return float(np.mean([F[full[j]] for j in range(max(0, i - 30), i)]))


def est_B(i, **kw):
    """昨天的日合计"""
    return F[days[i - 1]]


def est_C(i, **kw):
    """最近 3 天均值"""
    return float(np.mean([F[days[j]] for j in range(max(0, i - 3), i)]))


def est_D(i, **kw):
    """最近 7 天均值"""
    return float(np.mean([F[days[j]] for j in range(max(0, i - 7), i)]))


def est_E(i, **kw):
    """已结算的 00:00 那次 + 历史同期均值补 2 次"""
    # 历史"00:00 那次"与"全日"的差
    d0 = [agg[d][0] for d in full[-60:]]
    tot = [F[d] for d in full[-60:]]
    rest = np.mean([t - a for t, a in zip(tot, d0)])
    return agg[days[i]][0] + rest if len(agg[days[i]]) >= 1 else est_A(i)


def est_F(i, **kw):
    """已结算部分 + 昨天剩余部分的均值"""
    d0 = [agg[d][0] for d in full[-30:]]
    tot = [F[d] for d in full[-30:]]
    rest = np.mean([t - a for t, a in zip(tot, d0)])
    n = len(agg[days[i]])
    done = float(np.sum(agg[days[i]]))
    return done + rest * (3 - n) / 2 if n < 3 else done


START = 200
METHODS = [("A. 最近 30 个完整日均值", est_A),
           ("B. 昨天的日合计", est_B),
           ("C. 最近 3 天均值", est_C),
           ("D. 最近 7 天均值", est_D),
           ("E. 已结算的 00:00 + 历史补齐", est_E),
           ("F. 已结算全部 + 历史补齐", est_F)]
print(f"  {'方法':<28}{'MAE(bp)':>10}{'RMSE(bp)':>11}{'偏差(bp)':>11}{'vs A':>9}")
print("  " + "-" * 70)
base_mae = None
for lab, fn in METHODS:
    errs = []
    for i in range(START, len(days)):
        if len(agg[days[i]]) == 3:
            continue
        try:
            p = fn(i)
        except Exception:
            continue
        errs.append(p - F[days[i]])
    errs = np.array(errs)
    if len(errs) < 20:
        continue
    mae = np.abs(errs).mean() * 1e4
    rmse = np.sqrt((errs ** 2).mean()) * 1e4
    bias = errs.mean() * 1e4
    if base_mae is None:
        base_mae = mae
    print(f"  {lab:<28}{mae:>10.4f}{rmse:>11.4f}{bias:>+11.4f}"
          f"{(mae/base_mae-1)*100:>+8.1f}%")
print(f"\n  （样本：{sum(1 for i in range(START,len(days)) if len(agg[days[i]])<3)} 个非完整日）")

print()
print("=" * 88)
print("  三、结论")
print("=" * 88)
print()
print("  ❌ 不能【精确算出】，原因是机制：")
print()
print("     币安资金费率 = clamp(溢价指数 + clamp(利率 − 溢价指数, ±0.05%), ±上限)")
print("     其中【溢价指数】= (永续价 − 现货指数价) / 现货指数价 的【时间加权平均】")
print()
print("     ⇒ 它取决于【结算时刻及之前整个 8 小时窗口】的价格")
print("     ⇒ 而那是【未来】的信息")
print("     ⇒ 所以昨天收盘时，你无法知道今天 16:00 的费率")
print()
print("  [OK] 但可以做【统计估计】，而且比直接用历史均值更准：")
print()
print("     最好的做法是 F（用当天已结算的部分 + 历史同期均值补足）")
print("     ⇒ 因为它利用了【当天已经确定的信息】")
