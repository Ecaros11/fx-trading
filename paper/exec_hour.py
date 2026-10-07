"""
下单时刻的影响（exec_hour.py）
============================
同一套日线信号，但在一天里的不同小时执行 —— 结果差多少？
数据：ETHUSDT 1h（60125 根，2019-11-27 ~ 2026-10-06）
"""
import collections
import datetime as dt
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
FEE, SPREAD, TV, CAP, WIN = 0.0005, 0.003, 0.60, 1.20, 10

# ── 日线（算信号 + 波动率）──
db = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))
DT = np.array([b["t"] for b in db], float)
DC = np.array([b["c"] for b in db], float)
nd = len(DC)
dr = np.zeros(nd)
dr[1:] = DC[1:] / DC[:-1] - 1
dma = np.full(nd, np.nan)
cs = np.cumsum(np.insert(DC, 0, 0.0))
dma[49:] = (cs[50:] - cs[:-50]) / 50
dsig = np.nan_to_num((DC > dma).astype(float))
dV = np.full(nd, np.nan)
for i in range(WIN + 1, nd):
    dV[i] = dr[i - WIN:i].std(ddof=1) * np.sqrt(365)
# 每日索引
dday = np.array([int(t // 86400000) for t in DT], dtype=np.int64)
dmap = {int(d): i for i, d in enumerate(dday)}

# ── 1h ──
hb = json.loads((ROOT / "data" / "crypto" / "ETHUSDT_1h.json").read_text(encoding="utf-8"))
HT = np.array([b["t"] for b in hb], float)
HC = np.array([b["o"] for b in hb], float)      # 用开盘价（该小时的成交价）
hd = np.array([int(t // 86400000) for t in HT], dtype=np.int64)
hh = np.array([dt.datetime.fromtimestamp(t / 1000, dt.UTC).hour for t in HT])
nh = len(HC)

# 资金费
fr = json.loads((ROOT / "data" / "funding" / "ETHUSDT.json").read_text(encoding="utf-8"))
fagg = collections.OrderedDict()
for x in fr:
    fagg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
fday = {int(k): float(np.sum(v)) for k, v in fagg.items()}

print("=" * 92)
print("  同一套日线信号，在不同小时执行 —— 结果对比")
print("=" * 92)
print()
print(f"  日线 {nd} 根   1h {nh} 根   信号用日线收盘（UTC 00:00 确定）")
print()
print(f"  {'执行时刻(UTC)':>13}{'夏普':>9}{'年化':>9}{'回撤':>9}{'期末':>10}"
      f"{'vs 00:00':>11}")
print("  " + "-" * 62)

BASE = None
results = {}
for H in range(0, 24, 1):
    eq, wp, pk, dd = 1000.0, 0.0, 1000.0, 1.0
    xx = []
    # 找每个交易日该小时的第一根 1h
    hidx = {}
    for i in range(nh):
        k = (int(hd[i]), int(hh[i]))
        if k not in hidx:
            hidx[k] = i
    prev_price = None
    for di in range(61, nd):
        d = int(dday[di])
        i = hidx.get((d, H))
        if i is None or i + 24 >= nh:
            continue
        px = HC[i]
        # 信号用【前一天】的日线
        w = (min(3.0, TV / dV[di - 1])
             if (dsig[di - 1] and np.isfinite(dV[di - 1]) and 0 < dV[di - 1] <= CAP)
             else 0.0)
        # 持有到下一个同小时
        px2 = HC[i + 24]
        ret = px2 / px - 1
        fr_d = fday.get(d, 0.0)
        turn = abs(w - wp)
        x = w * ret - turn * FEE - w * fr_d
        xx.append(x)
        eq *= (1 + x)
        pk = max(pk, eq)
        dd = min(dd, eq / pk)
        wp = w
    xx = np.array(xx)
    if len(xx) < 100:
        continue
    sh = xx.mean() / xx.std(ddof=1) * np.sqrt(365)
    ann = (eq / 1000) ** (365 / len(xx)) - 1
    results[H] = (sh, ann * 100, (dd - 1) * 100, eq)
    if H == 0:
        BASE = sh

for H in sorted(results):
    sh, ann, ddp, eqe = results[H]
    tag = ""
    if BASE is not None and H != 0:
        d = sh - BASE
        tag = f"{d:>+10.4f}"
    print(f"  {H:>2}:05 UTC{'':<4}{sh:>9.4f}{ann:>8.1f}%{ddp:>8.1f}%"
          f"{eqe:>9.1f}x{tag:>11}")

print()
print("=" * 92)
print("  统计")
print("=" * 92)
print()
S = np.array([results[H][0] for H in sorted(results)])
A = np.array([results[H][1] for H in sorted(results)])
print(f"  夏普： 最高 {S.max():.4f}   最低 {S.min():.4f}   极差 {S.max()-S.min():.4f}")
print(f"         均值 {S.mean():.4f}   标准差 {S.std(ddof=1):.4f}")
print(f"  年化： 最高 {A.max():.1f}%   最低 {A.min():.1f}%   极差 {A.max()-A.min():.1f}pp")
print()
best, worst = max(results, key=lambda h: results[h][0]), min(results, key=lambda h: results[h][0])
print(f"  最好时刻 {best:>2}:00   最差时刻 {worst:>2}:00   差 {S.max()-S.min():.4f}")
print(f"  ⚠️ 而夏普的标准误是 0.387 ⇒ 这个极差是它的 {(S.max()-S.min())/0.387*100:.1f}%")
