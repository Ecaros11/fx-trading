"""
算单笔最大逆向 —— 定义写清楚（adverse_excursion.py）
=================================================
两种常见定义，差很多：
  A. 从【入场价】算：入场 = 信号日收盘（因为次日才持有）
  B. 从【持有期首日开盘】算
我用 A。并列出两种，让文档能用准确的说法。
"""
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
SYM = "ETHUSDT"
MMR = 0.004

j = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
O = np.array([b["o"] for b in j], float)
C = np.array([b["c"] for b in j], float)
LO = np.array([b["l"] for b in j], float)
n = len(C)
ma = np.full(n, np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
sig = np.nan_to_num((C > ma).astype(float))

# 交易：sig=1 的连续段。持有期收益是 w[i-1]*r[i]，
# 所以信号段 [st, en] 对应的【持仓】是 [st, en+1]。
trades = []
i = 60
while i < n:
    if sig[i] and not sig[i - 1]:
        st = i
        while i < n and sig[i]:
            i += 1
        trades.append((st, min(i, n - 1)))
    else:
        i += 1

print("=" * 84)
print(f"  {SYM}   {len(trades)} 笔完整交易 · 期间最大逆向")
print("=" * 84)
print()


def calc(entry_idx, hold_end, use_low=True):
    e = C[entry_idx]                       # 入场 = 信号日收盘
    seg = LO[entry_idx + 1:hold_end + 2] if use_low else C[entry_idx + 1:hold_end + 2]
    if len(seg) == 0:
        return np.nan
    return (seg.min() / e - 1) * 100


a_low = np.array([calc(st, en, True) for st, en in trades])
a_cls = np.array([calc(st, en, False) for st, en in trades])
a_low = a_low[np.isfinite(a_low)]
a_cls = a_cls[np.isfinite(a_cls)]

print(f"  定义：入场 = 【信号日收盘】C[st]；持有期 = [st+1, en+1]")
print()
print(f"  {'口径':<14}{'中位':>10}{'最差':>10}{'第10分位':>11}{'笔数':>7}")
print("  " + "-" * 54)
print(f"  {'最低价 low':<14}{np.median(a_low):>9.1f}%{a_low.min():>9.1f}%"
      f"{np.percentile(a_low, 10):>10.1f}%{len(a_low):>7}")
print(f"  {'收盘 close':<14}{np.median(a_cls):>9.1f}%{a_cls.min():>9.1f}%"
      f"{np.percentile(a_cls, 10):>10.1f}%{len(a_cls):>7}")

print()
print(f"  ⇒ 最差单笔（low 口径）  {a_low.min():.1f}%")
print(f"  ⇒ 2x 逐仓强平线         −{(1/2-MMR)*100:.1f}%")
print(f"  ⇒ 相差                 {abs(a_low.min()) - (1/2-MMR)*100:+.1f}pp")
print()
print(f"  0 / {len(a_low)} 笔触及强平线")

print()
print("  各杠杆下的安全边界（强平线 > 最差单笔即安全）：")
print(f"  {'杠杆':>6}{'强平@标的':>12}{'安全?':>9}")
print("  " + "-" * 30)
for lev in (2, 3, 4, 5, 8, 10, 15, 20):
    liq = (1 / lev - MMR) * 100
    print(f"  {lev:>5}x{liq:>11.1f}%{'✅' if liq > abs(a_low.min()) else '❌':>9}")
