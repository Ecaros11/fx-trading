"""
2024-12-07 起始 · 100 USDT · 每天重新匹配版本（v2）
================================================
v1 的错：把版本固定在初始那一档，循环里没重新匹配。
实际规则：每次开仓都读当前权益 → pick_method(equity) → 可能升档/降档。
"""
import datetime as dt
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
SYM = "ETHUSDT"
CAP, START_DATE = 100.0, "2024-12-07"
MIN_NOTIONAL, MMR = 20.0, 0.004
METHODS = [("固定版", None, 14.2), ("波动率目标 40%", 0.40, 52.3),
           ("波动率目标 25%", 0.25, 83.7), ("波动率目标 15%", 0.15, 139.5)]


def pick(eq):
    best = METHODS[0]
    for m in METHODS:
        if eq >= m[2]:
            best = m
    return best


j = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
T = np.array([b["t"] for b in j], float)
C = np.array([b["c"] for b in j], float)
n = len(C)
r = np.zeros(n)
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(n, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
vol20 = np.full(n, np.nan)
for i in range(21, n):
    vol20[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)

tgt = dt.datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000
idx = int(np.argmin(np.abs(T - tgt)))
D = lambda i: dt.datetime.fromtimestamp(T[i] / 1000, dt.UTC).strftime("%Y-%m-%d")


def pos_of(eq, i):
    """第 i 天（用 i-1 的信号和波动）的仓位，版本按【当前 eq】匹配"""
    mname, tv, need = pick(eq)
    if C[i - 1] <= ma[i - 1]:
        return 0.0, mname, tv
    if tv is None:
        return max(1.0, MIN_NOTIONAL / eq), mname, tv
    v = vol20[i - 1]
    if not np.isfinite(v) or v <= 0:
        return 0.0, mname, tv
    return min(3.0, tv / v), mname, tv


print("=" * 90)
print(f"  {START_DATE} 起始 · {CAP:.0f} U · 每天重新匹配版本")
print("=" * 90)
print()
print(f"  决策日 {D(idx-1)}：收盘 {C[idx-1]:,.2f}  MA50 {ma[idx-1]:,.2f}"
      f"  距 {(C[idx-1]/ma[idx-1]-1)*100:+.2f}%  ⇒ 做多")

m0, tv0, nd0 = pick(CAP)
p0, _, _ = pos_of(CAP, idx)
lev0 = max(1, int(np.ceil(p0 - 1e-9)))
print()
print(f"  本金 {CAP:.0f}U ⇒ {m0}（门槛 {nd0}U）")
print(f"     20 日波动 {vol20[idx-1]*100:.1f}%  ⇒ 仓位 {p0:.4f}x"
      f"  名义 {CAP*p0:.2f}U  数量 {CAP*p0/C[idx-1]:.4f} ETH")
print(f"     杠杆设置 {lev0}x   逐仓   强平@标的 {(1/lev0-MMR)*100:.1f}%")

# ── 模拟 ──
eq, peak, low, dd = CAP, CAP, CAP, 0.0
switches = []
cur = m0
trades = []
in_pos = False
ep = eeq = None
days = 0
for i in range(idx, n):
    w, mname, tv = pos_of(eq, i)
    if mname != cur:
        switches.append((D(i), eq, cur, mname))
        cur = mname
    if C[i - 1] > ma[i - 1]:
        if not in_pos:
            in_pos, ep, eeq, days = True, C[i - 1], eq, 0
    else:
        if in_pos:
            trades.append((ep, C[i - 1], eq - eeq, days, cur))
            in_pos = False
    w_prev, _, _ = pos_of(eq, i - 1)
    turn = abs(w - w_prev)
    eq *= (1 + w * r[i] - turn * 0.0005)
    peak = max(peak, eq)
    low = min(low, eq)
    dd = min(dd, eq / peak - 1)
    days += 1
if in_pos:
    trades.append((ep, C[n - 1], eq - eeq, days, cur))

print()
print("=" * 90)
print(f"  结果（{D(idx)} ~ {D(n-1)}）")
print("=" * 90)
print()
print(f"  起始 {CAP:.2f} U  →  期末 {eq:.2f} U   （{(eq/CAP-1)*100:+.1f}%）")
print(f"  峰值 {peak:.2f} U   路径最低 {low:.2f} U   最大回撤 {dd*100:.1f}%")
print(f"  完整交易 {len(trades)} 笔")
print()
if switches:
    print(f"  ── 版本切换 {len(switches)} 次（v1 里完全没算）──")
    print(f"  {'日期':<12}{'权益':>9}{'从':<18}{'到':<18}")
    print("  " + "-" * 58)
    for d, e, a, b in switches:
        print(f"  {d:<12}{e:>8.2f}U {a:<18}{b:<18}")
else:
    print("  ── 版本全程未切换 ──")

print()
print(f"  {'入场':>10}{'出场':>10}{'天':>5}{'盈亏':>10}   当时版本")
print("  " + "-" * 62)
for ep_, xp, pnl, d, m in trades:
    print(f"  {ep_:>10,.2f}{xp:>10,.2f}{d:>5}{pnl:>+9.2f}U   {m}")
