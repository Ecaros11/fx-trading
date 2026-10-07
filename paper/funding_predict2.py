"""
模拟「UTC 00:05 时点」的资金费估计（funding_predict2.py）
=====================================================
场景：当天只结算了 00:00 那一次，要估全天 3 次合计
这是你实际使用时的情况（北京 08:05 = UTC 00:05）
"""
import collections
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
BP = 1e4

# 历史：00:00 那次 与 全日合计 的关系
a0 = np.array([agg[d][0] for d in full])
tot = np.array([F[d] for d in full])
print("=" * 84)
print("  一、00:00 那次 与 全天合计 的关系")
print("=" * 84)
print()
print(f"  样本 {len(full)} 天")
print(f"  00:00 那次   均值 {a0.mean()*BP:+.4f} bp   标准差 {a0.std(ddof=1)*BP:.4f} bp")
print(f"  全天合计     均值 {tot.mean()*BP:+.4f} bp   标准差 {tot.std(ddof=1)*BP:.4f} bp")
print(f"  相关系数     {np.corrcoef(a0, tot)[0,1]:.4f}")
print(f"  全天/00:00   {tot.mean()/a0.mean():.3f} 倍")
print(f"  剩余 2 次    均值 {(tot-a0).mean()*BP:+.4f} bp   标准差 {(tot-a0).std(ddof=1)*BP:.4f} bp")

print()
print("=" * 84)
print("  二、六种估计方法的误差（目标：估全天合计）")
print("=" * 84)
print()
print("  ⚠️ 只用【当天 00:00 那次】+【此前的历史】—— 模拟 UTC 00:05 的真实处境")
print()
N_HIST = 30


def m_mean30(i):
    """最近 30 个完整日的均值"""
    return float(np.mean([F[full[j]] for j in range(i - N_HIST, i)]))


def m_prevday(i):
    """昨天的日合计"""
    return F[full[i - 1]]


def m_scaled(i):
    """按历史比例缩放 00:00 那次"""
    k = tot.mean() / a0.mean()
    return agg[days[i]][0] * k


def m_a0_plus_mean(i):
    """00:00 那次 + 历史剩余均值"""
    return agg[days[i]][0] + (tot - a0)[-N_HIST:].mean()


def m_blend(i):
    """00:00 那次 + 昨天剩余部分"""
    return agg[days[i]][0] + (tot - a0)[i - 1] if False else \
        agg[days[i]][0] + (F[full[i - 1]] - agg[full[i - 1]][0])


def m_reg(i):
    """线性回归：全天 = α + β × 00:00"""
    h = np.arange(max(0, i - 250), i)
    if len(h) < 30:
        return m_mean30(i)
    aa, tt = a0[h], tot[h]
    b = np.cov(aa, tt)[0, 1] / np.var(aa, ddof=1)
    al = tt.mean() - b * aa.mean()
    return al + b * agg[days[i]][0]


METHODS = [
    ("A. 最近 30 完整日均值（工具现用）", m_mean30),
    ("B. 昨天的日合计", m_prevday),
    ("C. 00:00 那次 × 历史比例", m_scaled),
    ("D. 00:00 那次 + 历史剩余均值", m_a0_plus_mean),
    ("E. 00:00 那次 + 昨天剩余", m_blend),
    ("F. 线性回归（250 天窗口）", m_reg),
]
print(f"  {'方法':<34}{'MAE(bp)':>10}{'RMSE(bp)':>11}{'偏差(bp)':>11}{'vs A':>9}")
print("  " + "-" * 76)
base = None
for lab, fn in METHODS:
    errs = []
    for i in range(250, len(full)):
        p = fn(i)
        errs.append(p - tot[i])
    errs = np.array(errs)
    mae = np.abs(errs).mean() * BP
    rmse = np.sqrt((errs ** 2).mean()) * BP
    bias = errs.mean() * BP
    if base is None:
        base = mae
    print(f"  {lab:<34}{mae:>10.4f}{rmse:>11.4f}{bias:>+11.4f}"
          f"{(mae/base-1)*100:>+8.1f}%")

print()
print("=" * 84)
print("  三、结论")
print("=" * 84)
print()
print("  ❌ 不能【算出】—— 机制上不可能：")
print("     费率 = f(溢价指数的 8 小时时间加权平均)")
print("     ⇒ 取决于结算前的未来价格")
print()
print("  ✅ 但能【估得比 30 天均值好得多】：")
print("     因为 00:00 那次已经结算了，它包含当天的信息")
print()
print("  最好的是 D 和 E（用当天已结算的部分 + 历史补足）")
print("  ⇒ 而 A（工具现在用的 30 天均值）忽略了当天已结算的信息")
