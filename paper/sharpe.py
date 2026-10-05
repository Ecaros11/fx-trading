"""
策略夏普（sharpe.py）
====================
两种算法：
  A. 交易级：每次独立下注的超额 / 标准差，再按 sqrt(每年次数) 年化
  B. 时间序列级：构造日度净值曲线（有信号持仓、无信号空仓），
     按 mean/std × sqrt(365) 年化  ← 这是业界标准口径

并给出夏普估计自身的标准误 —— 样本少的时候它比夏普本身还大。

同时对比：含成本 / 不含成本、同状态基准 / 绝对收益
"""
import collections
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
SYM = "ETHUSDT"
H = 10              # 持有期（天）
COST = 0.10 / 100   # 单次往返成本（taker 0.05% × 2）
FUND_DAILY = 0.07844 / 100   # 信号时段的日均资金费（多头付）

j = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
T = np.array([b["t"] for b in j], float)
C = np.array([b["c"] for b in j], float)
V = np.array([b["v"] for b in j], float)
n = len(C)
fd = json.loads((ROOT / "data" / "funding" / f"{SYM}.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fd:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
days = np.array(sorted(agg), float)
frd = np.array([float(np.sum(agg[int(d)])) for d in days])
dmap = dict(zip(days.astype(int), frd))
cd = np.array([int(t // 86400000) for t in T])
FR = np.array([dmap.get(int(d), np.nan) for d in cd])


def ma(x, k):
    o = np.full(len(x), np.nan)
    cs = np.cumsum(np.insert(x, 0, 0.0))
    o[k - 1:] = (cs[k:] - cs[:-k]) / k
    return o


m20, v30 = ma(C, 20), ma(V, 30)
vr = V / v30
pct = np.full(n, np.nan)
for i in range(60, n):
    w = FR[max(0, i - 180):i]
    w = w[np.isfinite(w)]
    if len(w) > 40 and np.isfinite(FR[i]):
        pct[i] = (w <= FR[i]).mean()

okm = np.isfinite(m20) & np.isfinite(vr) & np.isfinite(pct)
VR67 = float(np.nanpercentile(vr[okm], 67))
a1 = C > m20
a2 = vr > VR67
a3 = pct >= 0.67
sig = okm & a1 & a2 & a3

years = (T[-1] - T[0]) / 86400000 / 365.25
print("=" * 82)
print(f"  {SYM}   策略夏普   {years:.1f} 年   H={H} 天")
print("=" * 82)

# ══════════════ 方法 A：交易级 ══════════════
sig_idx = [i for i in range(30, n - H) if sig[i]]
# 归并成簇 + 互不重叠
cl, cur = [], []
for i in sig_idx:
    if cur and i - cur[-1] > 1:
        cl.append(cur); cur = []
    cur.append(i)
if cur:
    cl.append(cur)
indep, last = [], -10 ** 9
for c in cl:
    if c[0] - last >= H:
        indep.append(c[0]); last = c[0]

r_abs = np.array([C[i + H] / C[i] - 1 for i in indep])
base_all = np.array([C[i + H] / C[i] - 1 for i in range(30, n - H) if okm[i]])
base_same = np.array([C[i + H] / C[i] - 1
                      for i in range(30, n - H) if okm[i] and a1[i]])

# 基准是【标量】（全样本 / 同状态的平均 10 天收益），不是逐日数组
base_all_m = float(base_all.mean())
base_same_m = float(base_same.mean())

print()
print("  ── 方法 A：交易级 ──")
print(f"     独立下注 {len(indep)} 次 / {years:.1f} 年 = 每年 {len(indep)/years:.1f} 次")
print(f"     基准：全样本 {base_all_m*100:+.2f}%   同状态 {base_same_m*100:+.2f}%"
      f"  （10 天）")
print()
print(f"     {'口径':<26}{'每次均值':>10}{'每次标准差':>11}{'单次夏普':>10}{'年化夏普':>10}")
print("     " + "-" * 68)
for lab, x in (("绝对收益（毛）", r_abs),
               ("超额 vs 全样本基准（毛）", r_abs - base_all_m),
               ("超额 vs 同状态基准（毛）", r_abs - base_same_m)):
    s = x.mean() / x.std(ddof=1)
    print(f"     {lab:<26}{x.mean()*100:>+9.2f}%{x.std(ddof=1)*100:>10.2f}%"
          f"{s:>10.3f}{s*np.sqrt(len(indep)/years):>10.2f}")

# 含成本
x_net = r_abs - base_same_m - COST - FUND_DAILY * H
s_net = x_net.mean() / x_net.std(ddof=1)
print(f"     {'超额 vs 同状态（含成本）':<26}{x_net.mean()*100:>+9.2f}%"
      f"{x_net.std(ddof=1)*100:>10.2f}%{s_net:>10.3f}"
      f"{s_net*np.sqrt(len(indep)/years):>10.2f}")

# ══════════════ 方法 B：日度净值曲线 ══════════════
print()
print("  ── 方法 B：日度净值曲线（业界标准口径）──")
pos = np.zeros(n)          # 0=空仓 1=持仓
entry = np.zeros(n, bool)  # 入场日（扣成本）
for i in sig_idx:
    end = min(i + H, n)
    pos[i:end] = 1
    entry[i] = True

ret = np.zeros(n)
ret[1:] = C[1:] / C[:-1] - 1
strat = pos * ret
strat[entry] -= COST                    # 入场那次的手续费（单边，平仓时再算一半）
strat[entry] -= FUND_DAILY              # 资金费按日近似
# 平仓成本：持有到 H 天时再扣半次
for i in sig_idx:
    e = i + H
    if e < n:
        strat[e] -= COST / 2

valid = slice(30, n)
sr = strat[valid]
bh = ret[valid]
print(f"     天数 {n-30}   在场比例 {pos[30:].mean()*100:.1f}%")
print()
print(f"     {'口径':<24}{'日均':>11}{'日标准差':>11}{'年化收益':>11}"
      f"{'年化波动':>11}{'夏普':>9}")
print("     " + "-" * 78)
for lab, x in (("策略（含成本）", sr),
               ("策略（不含成本）", pos[valid] * ret[valid]),
               ("买入持有", bh)):
    mu, sd = x.mean(), x.std(ddof=1)
    print(f"     {lab:<24}{mu*100:>+10.4f}%{sd*100:>10.3f}%"
          f"{mu*365*100:>+10.1f}%{sd*np.sqrt(365)*100:>10.1f}%"
          f"{mu/sd*np.sqrt(365):>9.2f}")

# ══════════════ 夏普估计本身的误差 ══════════════
print()
print("  ── 夏普估计自身的标准误 ──")
for lab, N in (("方法A：独立下注数", len(indep)),
               ("方法B：交易日数", n - 30)):
    for S in (1.0, 0.5, 0.2):
        se = np.sqrt((1 + S * S / 2) / N)
        print(f"     {lab} N={N:<5} 若真实夏普={S:.1f}  ⇒  估计标准误 ±{se:.2f}")
    print()

print("=" * 82)
print("  怎么读")
print("=" * 82)
print("""
  · 方法 A 的口径是「每次下注」，年化要乘 sqrt(每年次数)
  · 方法 B 的口径是「每天」，年化要乘 sqrt(365) —— 这是业界报表用的那个
  · 两者不该相等：A 假设资金只在下注时占用，B 按实际净值曲线算
  · 无风险利率这里取 0（加密常用做法）

  ⚠️ 最重要的一行是最后那个标准误：
     样本少的时候，夏普估计的不确定度比夏普本身还大。
""")
