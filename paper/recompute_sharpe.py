"""
重新计算：我们这个方法的夏普
==========================
当前口径（已修正）：
  align.py 手续费系数 1
  METHODS 固定版 夏普 1.048 / 回撤 -71.2%
  DD_BY_LEV -0.462 ... -0.884

这次把三种可能被质疑的地方都摊开：
  ① 算术 vs 对数（年化 67.7% 时两者会有差）
  ② 恒定 1.405x vs 动态仓位（按权益匹配版本）
  ③ 样本区间（全样本 vs 2020 起）
  ④ 年化因子 365 vs 252
  ⑤ 切不切预热期
"""
import datetime as dt
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from align import panel, lag, sharpe, max_dd, cagr

ROOT = pathlib.Path(__file__).parent.parent
FEE = 0.0005
MIN_N = 20.0

d1 = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))
fd = json.loads((ROOT / "data" / "funding" / "ETHUSDT.json").read_text(encoding="utf-8"))
fday = {}
for x in fd:
    k = int(x["t"] // 86400000)
    fday[k] = fday.get(k, 0.0) + x["rate"]
C = np.array([b["c"] for b in d1], float)
T = np.array([b["t"] for b in d1], float)
day = np.array([b["t"] // 86400000 for b in d1])
FR = np.array([fday.get(int(day[i]), 0.0) for i in range(len(C))])
N = len(C)
W = 60
P = panel(C, FR, FEE)
r, FRl, sig = P.r, lag(FR), P.sig

print("=" * 104)
print("  ① 主口径：恒定 1.405x · 全样本 · 算术夏普 · √365")
print("=" * 104)
x = P.net(None)[W:]
x = x[np.isfinite(x)]
sr_d = x.mean() / x.std()
n = len(x)
print(f"  样本            {n} 天 = {n/365:.4f} 年")
print(f"  日夏普          {sr_d:.6f}")
print(f"  年化夏普        {sr_d*np.sqrt(365):.4f}")
print(f"  日均收益        {x.mean()*100:+.5f}%")
print(f"  日标准差        {x.std()*100:.5f}%")
print(f"  年化收益(几何)  {cagr(x)*100:.2f}%")
print(f"  年化波动        {x.std()*np.sqrt(365)*100:.2f}%")
print(f"  最大回撤        {max_dd(x)*100:.2f}%")
print()
print(f"  ⇒ 夏普 = {sr_d*np.sqrt(365):.4f}")
print(f"  工具 METHODS 表写的是 1.048 —— {'✅ 一致' if abs(sr_d*np.sqrt(365)-1.048)<0.001 else '🔴 差 %.4f' % (sr_d*np.sqrt(365)-1.048)}")

print()
print("=" * 104)
print("  ② 算术 vs 对数口径")
print("=" * 104)
lv = np.log1p(x)
print(f"  算术夏普  mean/std           = {x.mean()/x.std()*np.sqrt(365):.4f}")
print(f"  对数夏普  mean(log)/std(log) = {lv.mean()/lv.std()*np.sqrt(365):.4f}")
print(f"  差                            {lv.mean()/lv.std()*np.sqrt(365) - x.mean()/x.std()*np.sqrt(365):+.4f}")
print()
print("  ⇒ 年化 67.7% 时，对数口径比算术口径", end="")
d = x.mean()/x.std()*np.sqrt(365) - lv.mean()/lv.std()*np.sqrt(365)
print(f"【{'高' if d<0 else '低'}】{abs(d):.4f}")
print(f"     原因：算术夏普的分子是算术平均收益，会把正偏度的极端值算进去")

print()
print("=" * 104)
print("  ③ 年化因子")
print("=" * 104)
print(f"  √365（加密货币 7×24）  {x.mean()/x.std()*np.sqrt(365):.4f}")
print(f"  √252（股票交易日）      {x.mean()/x.std()*np.sqrt(252):.4f}")
print(f"  ⇒ 加密永续每天都能交易，365 才对")

print()
print("=" * 104)
print("  ④ 样本区间敏感性")
print("=" * 104)
NA = next(i for i, b in enumerate(d1) if b["t"] >= 1577836800000)   # 2020-01-01
cuts = [("全样本（第60根起）", W), ("2020-01-01 起", NA),
        ("2021-01-01 起", next(i for i, b in enumerate(d1)
                               if b["t"] >= 1609459200000)),
        ("2022-01-01 起", next(i for i, b in enumerate(d1)
                               if b["t"] >= 1640995200000)),
        ("2023-01-01 起", next(i for i, b in enumerate(d1)
                               if b["t"] >= 1672531200000))]
print(f"  {'区间':<22}{'天数':>7}{'夏普':>9}{'年化':>10}{'回撤':>10}")
print("  " + "-" * 58)
for lab, lo in cuts:
    a = P.net(None)[lo:]
    a = a[np.isfinite(a)]
    if len(a) < 60:
        continue
    print(f"  {lab:<22}{len(a):>7}{sharpe(a):>9.3f}{cagr(a)*100:>9.1f}%"
          f"{max_dd(a)*100:>9.1f}%")

print()
print("=" * 104)
print("  ⑤ 恒定 1.405x vs 动态仓位（按权益匹配版本）")
print("=" * 104)
print("  恒定 1.405x —— 回测和文档的口径：")
print(f"     夏普 {sr_d*np.sqrt(365):.4f}   回撤 {max_dd(x)*100:.2f}%")
print()
print("  动态仓位 —— 每天按 max(1.0, 20/权益) 定仓位，从 14.78U 起：")
eq = 14.78
peak = eq
dd = 0.0
rets = []
qty_w = 0.0
for i in range(W, N):
    w_now = max(1.0, MIN_N / eq) if (sig[i] and eq > 0) else 0.0
    w_prev = max(1.0, MIN_N / eq) if (sig[i - 1] and eq > 0) else 0.0
    turn = abs(w_now - w_prev)
    prev_eq = eq
    eq *= (1 + w_now * r[i] - turn * FEE - w_now * FR[i])
    if eq <= 0:
        eq = 0.0
        break
    if prev_eq > 0:
        rets.append(eq / prev_eq - 1)
    peak = max(peak, eq)
    dd = min(dd, eq / peak - 1)
rets = np.array(rets)
print(f"     夏普 {sharpe(rets):.4f}   回撤 {dd*100:.2f}%   期末 {eq:.2f}U")
print()
print("  ⚠️ 两者不同，是因为动态仓位会在权益涨过 20U 后自动降到 1.0x")
print("     ⇒ 仓位序列不同 ⇒ 夏普不同（夏普对【恒定】缩放不变，对【时变】缩放会变）")

print()
print("=" * 104)
print("  ⑥ 不确定性")
print("=" * 104)
se = np.sqrt((1 + 0.5 * sr_d ** 2) / n) * np.sqrt(365)
rho = [np.corrcoef(x[:-k], x[k:])[0, 1] for k in range(1, 6)]
eta = 1 + 2 * sum((1 - k / 6) * rho[k - 1] for k in range(1, 6))
print(f"  Lo(2002) 自相关修正 η = {eta:.3f}   1~5阶自相关 {[f'{v:+.3f}' for v in rho]}")
print(f"  年化标准误 {se:.4f}")
print(f"  95% 置信区间 [{sr_d*np.sqrt(365)-1.96*se:.4f}, {sr_d*np.sqrt(365)+1.96*se:.4f}]")
print()
print(f"  ⇒ 写文档时用 [{sr_d*np.sqrt(365)-1.96*se:.2f}, {sr_d*np.sqrt(365)+1.96*se:.2f}]")

print()
print("=" * 104)
print("  ⑦ 独立复算（不经过 align，手写一遍做交叉验证）")
print("=" * 104)
m50 = np.concatenate([[np.nan] * 49, np.convolve(C, np.ones(50) / 50, "valid")])
sg2 = np.nan_to_num((C > m50).astype(float))
r2 = np.zeros(N)
r2[1:] = C[1:] / C[:-1] - 1
w2 = sg2 * 1.405
w2l = np.concatenate([[0.0], w2[:-1]])
tn2 = np.abs(np.diff(np.concatenate([[0.0], w2l])))
FRl2 = np.concatenate([[0.0], FR[:-1]])
net2 = w2l * r2 - tn2 * FEE - w2l * FRl2
y = net2[W:]
y = y[np.isfinite(y)]
print(f"  手写复算 夏普 {y.mean()/y.std()*np.sqrt(365):.4f}   "
      f"回撤 {max_dd(y)*100:.2f}%   天数 {len(y)}")
print(f"  align    夏普 {sr_d*np.sqrt(365):.4f}   "
      f"回撤 {max_dd(x)*100:.2f}%   天数 {n}")
print(f"  ⇒ {'✅ 完全一致' if abs(y.mean()/y.std()*np.sqrt(365)-sr_d*np.sqrt(365))<1e-9 else '🔴 不一致（%.6f）' % (y.mean()/y.std()*np.sqrt(365)-sr_d*np.sqrt(365))}")
