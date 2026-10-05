"""
2024-12-07 起始 · 100 USDT · 按规则该怎么开仓（start_20241207.py）
"""
import collections
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
SYM = "ETHUSDT"
CAP = 100.0
START_DATE = "2024-12-07"

j = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
import datetime as dt
T = np.array([b["t"] for b in j], float)
C = np.array([b["c"] for b in j], float)
LO = np.array([b["l"] for b in j], float)
n = len(C)
r = np.zeros(n)
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(n, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
vol20 = np.full(n, np.nan)
for i in range(21, n):
    vol20[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)

# 找 2024-12-07 对应的下标
tgt = dt.datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000
idx = int(np.argmin(np.abs(T - tgt)))
D = lambda i: dt.datetime.fromtimestamp(T[i] / 1000, dt.UTC).strftime("%Y-%m-%d")

print("=" * 88)
print(f"  {START_DATE} 起始 · {CAP:.0f} USDT · 按 ma50_rules.md")
print("=" * 88)
print()
print(f"  你那天看到的（决策日 = 前一根已走完的日线 {D(idx-1)}）：")
print()
print(f"     该日收盘      {C[idx-1]:>12,.2f}")
print(f"     MA50          {ma[idx-1]:>12,.2f}")
print(f"     距 MA50       {(C[idx-1]/ma[idx-1]-1)*100:>+11.2f}%")
print(f"     ⇒ 信号        {'**做多**' if C[idx-1] > ma[idx-1] else '**空仓**'}")

# 匹配版本
MIN_NOTIONAL = 20.0
METHODS = [("固定版", None, 14.2), ("波动率目标 40%", 0.40, 52.3),
           ("波动率目标 25%", 0.25, 83.7), ("波动率目标 15%", 0.15, 139.5)]
mname, tv, need = METHODS[0]
for m in METHODS:
    if CAP >= m[2]:
        mname, tv, need = m
print()
print(f"  本金 {CAP:.0f} USDT ⇒ 匹配版本：**{mname}**（门槛 {need}U）")

rv = vol20[idx-1]
pos = max(1.0, MIN_NOTIONAL / CAP) if tv is None else min(3.0, tv / rv)
lev = max(1, int(np.ceil(pos - 1e-9)))
notion = CAP * pos
qty = notion / C[idx-1]
MMR = 0.004
liq = 1 / lev - MMR

print()
print("  ── 该开多少 ──")
print()
print(f"     20 日已实现波动   {rv*100:>10.1f}%")
if tv is None:
    print(f"     目标仓位          max(1.0, 20/100) = {pos:.4f}x")
else:
    print(f"     目标仓位          min(3, {tv*100:.0f}% ÷ {rv*100:.1f}%) = {pos:.4f}x")
print(f"     目标名义          {notion:>10.2f} USDT")
print(f"     目标数量          {qty:>10.4f} ETH   （按收盘价 {C[idx-1]:,.2f}）")
print()
print("  ── 币安上怎么设 ──")
print()
print(f"     杠杆设置          {lev}x          （= ceil({pos:.4f})，币安只允许整数）")
print(f"     保证金模式        逐仓")
print(f"     保证金占用        {notion/lev:>10.2f} USDT   富余 {CAP-notion/lev:.2f} USDT")
print(f"     逐仓强平@标的      {liq*100:>9.1f}%")

# ── 实际路径 ──
print()
print("=" * 88)
print(f"  从那之后实际怎么走（{D(idx)} ~ {D(n-1)}）")
print("=" * 88)
print()
eq = CAP
peak = eq
dd = 0.0
low = eq
trades = []
in_pos = False
entry_eq = entry_px = None
days_in = 0
for i in range(idx, n):
    if ma[i-1] != ma[i-1]:
        continue
    if C[i-1] > ma[i-1]:
        w = max(1.0, MIN_NOTIONAL / eq) if tv is None else min(3.0, tv / vol20[i-1])
        if not in_pos:
            in_pos, entry_eq, entry_px, days_in = True, eq, C[i-1], 0
    else:
        w = 0.0
        if in_pos:
            trades.append((entry_px, C[i-1], eq - entry_eq, days_in))
            in_pos = False
    prev_w = (max(1.0, MIN_NOTIONAL/eq) if tv is None else min(3.0, tv/vol20[i-2])) if C[i-2] > ma[i-2] else 0.0
    turn = abs(w - prev_w)
    eq *= (1 + w * r[i] - turn * 0.0005)
    peak = max(peak, eq)
    low = min(low, eq)
    dd = min(dd, eq / peak - 1)
    days_in += 1
if in_pos:
    trades.append((entry_px, C[n-1], eq - entry_eq, days_in))

print(f"  起始 {CAP:.2f} U  →  期末 {eq:.2f} U   （{(eq/CAP-1)*100:+.1f}%）")
print(f"  峰值 {peak:.2f} U   路径最低 {low:.2f} U   最大回撤 {dd*100:.1f}%")
print(f"  完整交易 {len(trades)} 笔")
print()
print(f"  {'入场':>11}{'出场':>11}{'持仓天':>8}{'这笔盈亏':>12}")
print("  " + "-" * 44)
for ep, xp, pnl, d in trades:
    print(f"  {ep:>11,.2f}{xp:>11,.2f}{d:>8}{pnl:>+11.2f}U")
