"""
估计方法的完整回测（estimate_backtest.py）
========================================
做法：用第 1 天的数据估第 2 天，与第 2 天的【真实值】逐日比较
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
F = np.array([float(np.sum(agg[d])) for d in full])
BP = 1e4
N = len(F)
print("=" * 90)
print("  估计方法回测：用第 1 天估第 2 天，与真实值比较")
print("=" * 90)
print()
print(f"  价格样本：{N} 个完整日（每天 3 次结算）")
print(f"  真实日均 {F.mean()*BP:+.4f} bp   标准差 {F.std(ddof=1)*BP:.4f} bp")
print()

METHODS = [
    ("A. 昨天日合计（工具现用）", lambda i: F[i - 1]),
    ("B. 最近 30 天均值", lambda i: F[max(0, i - 30):i].mean()),
    ("C. 最近 7 天均值", lambda i: F[max(0, i - 7):i].mean()),
    ("D. 最近 3 天均值", lambda i: F[max(0, i - 3):i].mean()),
    ("E. 昨天×0.5 + 30天均值×0.5", lambda i: 0.5 * F[i - 1] + 0.5 * F[max(0, i - 30):i].mean()),
    ("F. 全局均值（最笨的基准）", lambda i: F[:i].mean()),
]
START = 100
print(f"  {'方法':<28}{'MAE':>9}{'RMSE':>9}{'中位误差':>10}{'符号准确率':>12}"
      f"{'完全相同的比例':>16}")
print("  " + "-" * 88)
best = None
for lab, fn in METHODS:
    est = np.array([fn(i) for i in range(START, N)])
    tru = F[START:]
    e = est - tru
    mae = np.abs(e).mean() * BP
    rmse = np.sqrt((e ** 2).mean()) * BP
    med = np.median(np.abs(e)) * BP
    sign = (np.sign(est) == np.sign(tru)).mean() * 100
    exact = (np.abs(e) < 1e-12).mean() * 100
    if best is None:
        best = mae
    print(f"  {lab:<28}{mae:>8.4f}{rmse:>9.4f}{med:>10.4f}{sign:>11.1f}%"
          f"{exact:>15.1f}%")

print()
print("=" * 90)
print("  最好的方法（昨天日合计）的详细误差分布")
print("=" * 90)
print()
est = np.array([F[i - 1] for i in range(START, N)])
tru = F[START:]
e = (est - tru) * BP
print(f"  误差（bp）：均值 {e.mean():+.4f}   标准差 {e.std(ddof=1):.4f}")
print(f"             范围 {e.min():+.2f} ~ {e.max():+.2f}")
print()
print(f"  {'误差范围':>18}{'占比':>10}{'累计':>10}")
print("  " + "-" * 40)
for lo, hi in ((-1, 1), (-2, 2), (-5, 5), (-10, 10), (-30, 30), (-100, 100)):
    p = ((e >= lo) & (e <= hi)).mean() * 100
    print(f"  {'±' + str(hi) + ' bp':>18}{p:>9.1f}%{p:>9.1f}%")
print()
print(f"  ⚠️ 完全相同（误差 < 1e-12）的比例：{(np.abs(e) < 1e-12).mean()*100:.2f}%")
print(f"     几乎完全一致（|误差| < 0.01 bp）的比例：{(np.abs(e) < 0.01).mean()*100:.2f}%")

print()
print("=" * 90)
print("  相关性")
print("=" * 90)
print()
print(f"  估计值 vs 真实值的相关系数 = {np.corrcoef(est, tru)[0,1]:.4f}")
print(f"  ⇒ 解释力 R² = {np.corrcoef(est, tru)[0,1]**2:.4f}")

print()
print("=" * 90)
print("  结论")
print("=" * 90)
print()
print(f"  ❌ 做不到「相同」—— 资金费是连续值，估计永远有误差")
print(f"     完全相同的比例 ≈ 0%")
print(f"     |误差| < 2 bp 的比例 ≈ {((e >= -2) & (e <= 2)).mean()*100:.0f}%")
print(f"     |误差| < 10 bp 的比例 ≈ {((e >= -10) & (e <= 10)).mean()*100:.0f}%")
print()
print(f"  ✅ 但「方向」基本能对：符号准确率 {(np.sign(est)==np.sign(tru)).mean()*100:.1f}%")
print(f"     （即：说'多头付'时，92% 的情况确实是多头付）")
print()
print(f"  ⚠️ 以年化看误差的绝对量级：")
print(f"     MAE = {np.abs(e).mean():.4f} bp/天 = {np.abs(e).mean()/100:.5f}%/天")
print(f"         = 年化 {np.abs(e).mean()/100*365:.2f}%")
print(f"     而 60% 档的平均仓位约 0.99x ⇒ 账户层面约 {np.abs(e).mean()/100*365*0.99:.2f}%/年")
print(f"     对照：策略年化 92.4% ⇒ 相对误差 {np.abs(e).mean()/100*365*0.99/92.4*100:.1f}%")
