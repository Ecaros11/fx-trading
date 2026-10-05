"""
1000U 现货 + 1000U 本方法（combo_2000.py）
=======================================
关键问题：这两半【不独立】—— 都是做多 ETH。
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


def series(cap, use_method):
    """返回逐日收益序列"""
    eq = cap
    w_prev = 0.0
    out = np.zeros(n)
    for i in range(60, n):
        if use_method:
            if C[i - 1] > ma[i - 1]:
                _, tv, _ = pick(eq)
                w = max(1.0, MIN_NOTIONAL / eq) if tv is None else \
                    (min(3.0, tv / vol[i - 1]) if np.isfinite(vol[i - 1]) else 0.0)
            else:
                w = 0.0
        else:
            w = 1.0
        out[i] = w * r[i] - abs(w - w_prev) * FEE - w * FR[i]
        eq *= (1 + out[i])
        w_prev = w
    return out


def stat(x, lab, cap):
    x = x[60:]
    eq = np.cumprod(1 + x)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    tot = eq[-1] - 1
    sh = x.mean() / x.std(ddof=1) * np.sqrt(365)
    print(f"  {lab:<34}{cap*(1+tot):>10.0f}U{tot*100:>11.1f}%{dd*100:>11.1f}%"
          f"{sh:>8.3f}")
    return tot, dd, sh


s_spot = series(1000, False)
s_meth = series(1000, True)
combo = 0.5 * s_spot + 0.5 * s_meth

print("=" * 92)
print(f"  {SYM}   2000U 怎么分（{dt.datetime.fromtimestamp(T[0]/1000, dt.UTC):%Y-%m-%d}"
      f" ~ {dt.datetime.fromtimestamp(T[-1]/1000, dt.UTC):%Y-%m-%d}）")
print("=" * 92)
print()
print(f"  {'方案':<34}{'期末':>10}{'总收益':>11}{'最大回撤':>11}{'夏普':>8}")
print("  " + "-" * 76)
stat(s_spot, "① 全部 2000U 现货持有", 2000)
stat(s_meth, "② 全部 2000U 用本方法", 2000)
stat(combo, "③ 1000 现货 + 1000 方法（你的方案）", 2000)

print()
print("=" * 92)
print("  你为什么觉得它可以 —— 以及为什么不是")
print("=" * 92)
print()
print("  两半都在做多 ETH，所以合起来 = 一个【仓位在 1.0x ~ 2.0x 之间浮动】的多头")
print()
# 算平均总敞口
w_spot = np.ones(n)
w_meth = np.zeros(n)
eq = 1000.0
wprev = 0.0
for i in range(60, n):
    if C[i - 1] > ma[i - 1]:
        _, tv, _ = pick(eq)
        w = max(1.0, MIN_NOTIONAL / eq) if tv is None else \
            (min(3.0, tv / vol[i - 1]) if np.isfinite(vol[i - 1]) else 0.0)
    else:
        w = 0.0
    w_meth[i] = w
    eq *= (1 + w * r[i] - abs(w - wprev) * FEE - w * FR[i])
    wprev = w
print(f"  现货半边的敞口      恒为 1.000x")
print(f"  方法半边的敞口      平均 {w_meth[60:].mean():.3f}x"
      f"（在场 {((w_meth[60:]>0).mean())*100:.0f}% 的时间）")
print(f"  ⇒ 合起来总敞口      平均 {(1 + w_meth[60:].mean())/2:.3f}x"
      f"   范围 { (1+0)/2:.2f}x ~ {(1+w_meth[60:].max())/2:.2f}x")
print()
print("  ⇒ 这【不是】对冲，也不是分散 —— 是【把一半的钱按 1x 拿，")
print("     另一半按 0~1x 拿】，合起来就是一个 1~2x 的多头。")
print()
print("  而它的表现（上面 ③）正好落在 ① 和 ② 之间：")
tot_s, dd_s, sh_s = 0, 0, 0
xs = s_spot[60:]
eqs = np.cumprod(1 + xs)
tot_s = eqs[-1] - 1
dd_s = (eqs / np.maximum.accumulate(eqs) - 1).min()
xc = combo[60:]
eqc = np.cumprod(1 + xc)
tot_c = eqc[-1] - 1
dd_c = (eqc / np.maximum.accumulate(eqc) - 1).min()
print(f"     收益：{tot_s*100:.0f}% → {tot_c*100:.0f}%  （拿到纯现货的 "
      f"{tot_c/tot_s*100:.0f}%）")
print(f"     回撤：{dd_s*100:.0f}% → {dd_c*100:.0f}%  （承受纯现货的 "
      f"{dd_c/dd_s*100:.0f}%）")
