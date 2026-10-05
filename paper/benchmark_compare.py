"""
对照基准：我们的方法 vs 各种"别人"
================================
分三层：
  ① ETH 本身能给你什么（这是最公平的对照，同一段数据、同一个成本口径）
  ② 学术界测到的散户实际表现（有数据来源的）
  ③ 机构趋势跟踪的表现（公开可查的代理指标）

第 ① 层我可以精确复算；②③ 只能引用外部来源，不能自己编。
"""
import datetime as dt
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from align import panel, sharpe, max_dd, cagr, lag

ROOT = pathlib.Path(__file__).parent.parent
W = 60
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
yrs = (N - W) / 365
P = panel(C, FR, 0.0005)

print("=" * 100)
print(f"  ① 同一段数据的横向对照（ETHUSDT 永续，{yrs:.2f} 年）")
print("=" * 100)
r_all = np.zeros(N)
r_all[1:] = C[1:] / C[:-1] - 1
r = r_all[W:]
r = r[np.isfinite(r)]

rows = []


def add(lab, x, note=""):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    rows.append((lab, sharpe(x), max_dd(x), cagr(x), float(np.prod(1 + x)), note))


# 1. 买 ETH 现货（1x，无杠杆，无资金费）
add("ETH 现货买入持有（1x）", r)

# 2. ETH 永续多头持有（1x，付资金费，不择时）
add("ETH 永续一直做多（1x，付资金费）", r - FR[W:])

# 3. 永续 1.405x
add("ETH 永续一直做多（1.405x）", r * 1.405 - FR[W:] * 1.405)

# 4. 永续 2x（会爆仓的口径没算，只给近似）
add("ETH 永续一直做多（2x）", r * 2 - FR[W:] * 2)

# 5. 我们的方法：MA50 + 固定版
add("MA50 择时（固定版，1.405x）", P.net(None)[W:], "我们的")

# 6. 我们的方法：波动率目标 25%
add("MA50 择时（波动率目标 25%）", P.net(0.25)[W:], "我们的")

# 7. 无杠杆的 MA50（1x 满仓）—— 最干净的"择时值多少钱"
w1 = P.sig * 1.0
wl1 = lag(w1)
tn1 = np.abs(np.diff(np.concatenate([[0.0], wl1])))
add("MA50 择时（1x，不加杠杆）", (wl1 * P.r - tn1 * 0.0005 - wl1 * lag(P.FR))[W:], "择时本身的贡献")

print(f"  {'策略':<32}{'夏普':>8}{'年化':>10}{'最大回撤':>11}"
      f"{'总倍率':>10}   说明")
print("  " + "-" * 92)
for lab, s, d, a, m, note in sorted(rows, key=lambda x: -x[1]):
    print(f"  {lab:<32}{s:>8.3f}{a*100:>9.1f}%{d*100:>10.1f}%{m:>10.2f}   {note}")

print()
print("=" * 100)
print("  ② 关键分解：我们赚的钱里，多少来自 ETH 本身，多少来自择时")
print("=" * 100)
ours = [x for x in rows if x[0].startswith("MA50 择时（固定版")][0]
bh = [x for x in rows if x[0].startswith("ETH 永续一直做多（1x")][0]
bh14 = [x for x in rows if "1.405x" in x[0]][0]
timing = [x for x in rows if x[0].startswith("MA50 择时（1x")][0]

print(f"  ETH 永续一直做多（1x）        夏普 {bh[1]:.3f}   年化 {bh[2]*100:>6.1f}%")
print(f"  ETH 永续一直做多（1.405x）    夏普 {bh14[1]:.3f}   年化 {bh14[2]*100:>6.1f}%")
print(f"  MA50 择时（1x）              夏普 {timing[1]:.3f}   年化 {timing[2]*100:>6.1f}%")
print(f"  MA50 择时（固定版 1.405x）     夏普 {ours[1]:.3f}   年化 {ours[2]*100:>6.1f}%")
print()
print(f"  ⇒ 择时的贡献（1x 口径）：夏普 {timing[1]-bh[1]:+.3f}   年化 {(timing[2]-bh[2])*100:+.1f}pp")
print(f"  ⇒ 杠杆的贡献（1.405x vs 1x）：夏普 {bh14[1]-bh[1]:+.3f}   年化 {(bh14[2]-bh[2])*100:+.1f}pp")
print()
print(f"  我们 vs 无脑 1.405x 买入持有：")
print(f"     夏普 {ours[1]:.3f} vs {bh14[1]:.3f}   差 {ours[1]-bh14[1]:+.3f}")
print(f"     年化 {ours[2]*100:.1f}% vs {bh14[2]*100:.1f}%   差 {(ours[2]-bh14[2])*100:+.1f}pp")
print(f"     回撤 {ours[3]*100:.1f}% vs {bh14[3]*100:.1f}%   差 {(ours[3]-bh14[3])*100:+.1f}pp")

print()
print("=" * 100)
print("  ③ 逐年的对照（看看优势来自哪一年）")
print("=" * 100)
x_ours = P.net(None)
x_bh = r_all * 1.405 - FR * 1.405
NA = next(i for i, b in enumerate(d1) if b["t"] >= 1577836800000)
print(f"  {'年份':<8}{'我们的':>11}{'无脑1.405x':>13}{'市场1x':>11}   {'差':>9}")
print("  " + "-" * 56)
wins = 0
for y in range(2020, 2027):
    t0 = int(dt.datetime.strptime(f"{y}-01-01", "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000)
    t1 = int(dt.datetime.strptime(f"{y}-12-31", "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000)
    i0 = next((j for j in range(NA, N) if T[j] >= t0), None)
    i1 = next((j for j in range(NA, N) if T[j] >= t1), N - 1)
    if i0 is None:
        continue
    a = x_ours[i0:i1 + 1]; a = a[np.isfinite(a)]
    b = x_bh[i0:i1 + 1]; b = b[np.isfinite(b)]
    va = np.prod(1 + a) - 1
    vb = np.prod(1 + b) - 1
    if va > vb:
        wins += 1
    print(f"  {y:<8}{va*100:>10.1f}%{vb*100:>12.1f}%{(C[i1]/C[i0]-1)*100:>10.1f}%"
          f"{'':>2}{(va-vb)*100:>+8.1f}pp")
print()
print(f"  ⇒ 7 年里有 {wins} 年跑赢无脑杠杆")
