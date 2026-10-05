"""
2025-10-06 起始 · 1000U · 本方法 vs ETH 现货持有（y1.py）
"""
import collections
import datetime as dt
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
SYM = "ETHUSDT"
FEE = 0.0005
MIN_NOTIONAL = 20.0
CAP = 1000.0
START = "2025-10-06"

j = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
T = np.array([b["t"] for b in j], float)
C = np.array([b["c"] for b in j], float)
n = len(C)
r = np.zeros(n)
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(n, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
vol = np.full(n, np.nan)
for i in range(21, n):
    vol[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)
fr = json.loads((ROOT / "data" / "funding" / f"{SYM}.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
fd = {int(k): float(np.sum(v)) for k, v in agg.items()}
cd = np.array([int(t // 86400000) for t in T])
FR = np.nan_to_num(np.array([fd.get(int(d), np.nan) for d in cd]))

METHODS = [("固定版", None, 14.2), ("波动率目标 40%", 0.40, 52.3),
           ("波动率目标 25%", 0.25, 83.7), ("波动率目标 15%", 0.15, 139.5)]


def pick(eq):
    b = METHODS[0]
    for m in METHODS:
        if eq >= m[2]:
            b = m
    return b


tgt = dt.datetime.strptime(START, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000
idx = int(np.argmin(np.abs(T - tgt)))
D = lambda i: dt.datetime.fromtimestamp(T[i] / 1000, dt.UTC).strftime("%Y-%m-%d")

print("=" * 88)
print(f"  {START} 起始 · {CAP:.0f} U · 到今天（{D(n-1)}）")
print("=" * 88)
print()
print(f"  起始日 ETH 收盘   {C[idx]:>10,.2f}")
print(f"  最新日 ETH 收盘   {C[n-1]:>10,.2f}   （{(C[n-1]/C[idx]-1)*100:+.1f}%）")

# ── 本方法 ──
eq, peak, low, dd = CAP, CAP, CAP, 0.0
w_prev = 0.0
trades = []
in_pos = False
ep = eeq = None
days = 0
first = None
for i in range(idx + 1, n):
    if C[i - 1] > ma[i - 1]:
        mname, tv, need = pick(eq)
        w = max(1.0, MIN_NOTIONAL / eq) if tv is None else \
            (min(3.0, tv / vol[i - 1]) if np.isfinite(vol[i - 1]) else 0.0)
        if not in_pos:
            in_pos, ep, eeq, days = True, C[i - 1], eq, 0
            lev = max(1, int(np.ceil(w - 1e-9)))
            if first is None:
                first = (D(i), mname, w, eq * w, eq * w / C[i - 1], lev)
    else:
        w = 0.0
        if in_pos:
            trades.append((ep, C[i - 1], eq - eeq, days))
            in_pos = False
    eq *= (1 + w * r[i] - abs(w - w_prev) * FEE - w * FR[i])
    peak = max(peak, eq)
    low = min(low, eq)
    dd = min(dd, eq / peak - 1)
    w_prev = w
    days += 1
if in_pos:
    trades.append((ep, C[n - 1], eq - eeq, days))

# ── 现货 ──
sp = CAP * C[n - 1] / C[idx]
speq = np.array([C[i] / C[idx] for i in range(idx, n)])
sp_dd = (speq / np.maximum.accumulate(speq) - 1).min()
sp_low = CAP * speq.min()

# ── 现货扣资金费 ──
ef = CAP
for i in range(idx + 1, n):
    ef *= (1 + r[i] - FR[i])
spf = ef

print()
print("=" * 88)
print("  结果对比")
print("=" * 88)
print()
print(f"  {'方案':<28}{'期末':>11}{'收益':>11}{'最大回撤':>11}{'路径最低':>11}")
print("  " + "-" * 76)
print(f"  {'① 现货持有（不扣费）':<28}{sp:>10.1f}U{(sp/CAP-1)*100:>10.1f}%"
      f"{sp_dd*100:>10.1f}%{sp_low:>10.1f}U")
print(f"  {'② 现货持有（扣资金费）':<28}{spf:>10.1f}U{(spf/CAP-1)*100:>10.1f}%"
      f"{sp_dd*100:>10.1f}%{sp_low:>10.1f}U")
print(f"  {'③ 本方法（MA50）':<28}{eq:>10.1f}U{(eq/CAP-1)*100:>10.1f}%"
      f"{dd*100:>10.1f}%{low:>10.1f}U")

print()
print(f"  差：本方法 - 现货（扣费） = {eq - spf:+.1f} U")

if first:
    print()
    print("=" * 88)
    print("  第一天你该怎么做")
    print("=" * 88)
    print()
    print(f"     日期        {first[0]}")
    print(f"     匹配版本    {first[1]}")
    print(f"     目标仓位    {first[2]:.4f}x")
    print(f"     目标名义    {first[3]:.2f} USDT")
    print(f"     目标数量    {first[4]:.4f} ETH")
    print(f"     杠杆设置    {first[5]}x")

print()
print(f"  期间交易 {len(trades)} 笔")
if trades:
    print()
    print(f"  {'入场':>10}{'出场':>10}{'天':>5}{'盈亏':>11}")
    print("  " + "-" * 38)
    for ep_, xp, pnl, d in trades:
        print(f"  {ep_:>10,.2f}{xp:>10,.2f}{d:>5}{pnl:>+10.2f}U")
