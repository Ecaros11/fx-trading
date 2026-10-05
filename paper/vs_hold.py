"""
这个方法 vs 现货持有（vs_hold.py）
================================
同一段数据、同一笔本金，直接比。
"""
import collections
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
import datetime as dt
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


def run(cap, use_ma50, pay_funding):
    eq, peak, dd, low = cap, cap, 0.0, cap
    w_prev = 0.0
    for i in range(60, n):
        if use_ma50:
            if C[i - 1] > ma[i - 1]:
                _, tv, _ = pick(eq)
                w = max(1.0, MIN_NOTIONAL / eq) if tv is None else \
                    (min(3.0, tv / vol[i - 1]) if np.isfinite(vol[i - 1]) else 0.0)
            else:
                w = 0.0
        else:
            w = 1.0
        turn = abs(w - w_prev)
        f = FR[i] if pay_funding else 0.0
        eq *= (1 + w * r[i] - turn * FEE - w * f)
        peak = max(peak, eq)
        low = min(low, eq)
        dd = min(dd, eq / peak - 1)
        w_prev = w
    return eq, dd, low


print("=" * 92)
print(f"  {SYM}   这个方法 vs 现货持有   （本金 1000 USDT）")
print("=" * 92)
print()
print(f"  数据：{dt.datetime.fromtimestamp(T[0]/1000, dt.UTC):%Y-%m-%d} ~ "
      f"{dt.datetime.fromtimestamp(T[-1]/1000, dt.UTC):%Y-%m-%d}"
      f"（{n} 天 ≈ {n/365.25:.2f} 年）")
print()
print(f"  {'策略':<34}{'期末':>11}{'总收益':>11}{'最大回撤':>11}{'路径最低':>11}")
print("  " + "-" * 78)


def show(lab, eq, dd, low, cap=1000):
    print(f"  {lab:<34}{eq:>10.0f}U{(eq/cap-1)*100:>10.1f}%{dd*100:>10.1f}%{low:>10.0f}U")


show("① 现货持有（买完不动，不扣资金费）", *run(1000, False, False))
show("② 现货持有（扣资金费）", *run(1000, False, True))
show("③ MA50 + 波动率目标（本方法）", *run(1000, True, True))

print()
print("=" * 92)
print("  分段对比（每段重新起算 1000U）")
print("=" * 92)
print()
segs = [("2019-11-27", "2021-11-30", "上一轮牛市"),
        ("2021-12-01", "2023-12-31", "熊市 + 震荡"),
        ("2024-01-01", "2026-10-05", "本轮")]
print(f"  {'时段':<22}{'市场':<14}{'现货':>10}{'本方法':>11}{'差':>10}{'现货回撤':>10}{'方法回撤':>10}")
print("  " + "-" * 90)
for a, b, lab in segs:
    ta = dt.datetime.strptime(a, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000
    tb = dt.datetime.strptime(b, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp() * 1000
    ia = int(np.argmin(np.abs(T - ta)))
    ib = int(np.argmin(np.abs(T - tb)))
    mkt = (C[ib] / C[ia] - 1) * 100

    def seg_run(use_ma50):
        eq, peak, dd = 1000.0, 1000.0, 0.0
        w_prev = 0.0
        for i in range(max(ia, 60) + 1, ib + 1):
            if use_ma50:
                if C[i - 1] > ma[i - 1]:
                    _, tv, _ = pick(eq)
                    w = max(1.0, MIN_NOTIONAL / eq) if tv is None else \
                        (min(3.0, tv / vol[i - 1]) if np.isfinite(vol[i - 1]) else 0.0)
                else:
                    w = 0.0
            else:
                w = 1.0
            eq *= (1 + w * r[i] - abs(w - w_prev) * FEE - w * FR[i])
            peak = max(peak, eq)
            dd = min(dd, eq / peak - 1)
            w_prev = w
        return eq, dd

    es, ds = seg_run(False)
    em, dm = seg_run(True)
    print(f"  {a}~{b[:7]:<10}{lab:<14}{es-1000:>+9.0f}U{em-1000:>+10.0f}U"
          f"{(em-es):>+9.0f}U{ds*100:>9.1f}%{dm*100:>9.1f}%")
