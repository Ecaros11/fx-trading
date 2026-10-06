"""
TSMOM / 均值回归 能否强化 MA50（tsmom_mr.py）
==========================================
基准：日线收盘 > MA50 → 做多（每天对账，40% 档）
测试：
  A. TSMOM 变体（不同回看期 / 多周期确认）
  B. 均值回归（跌了才买 / 涨多了不买）
"""
import collections
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
SYM = "ETHUSDT"
FEE, MIN_NOTIONAL = 0.0005, 20.0
TV = 0.40

j = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
T = np.array([b["t"] for b in j], float)
C = np.array([b["c"] for b in j], float)
nn = len(C)
r = np.zeros(nn)
r[1:] = C[1:] / C[:-1] - 1
fr = json.loads((ROOT / "data" / "funding" / f"{SYM}.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
fday = {int(k): float(np.sum(v)) for k, v in agg.items()}
cd = np.array([int(t // 86400000) for t in T])
FR = np.nan_to_num(np.array([fday.get(int(d), np.nan) for d in cd]))
vol = np.full(nn, np.nan)
for i in range(21, nn):
    vol[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)


def sma(x, k):
    o = np.full(len(x), np.nan)
    cs = np.cumsum(np.insert(x, 0, 0.0))
    o[k - 1:] = (cs[k:] - cs[:-k]) / k
    return o


ma50 = sma(C, 50)


def run(sig, label):
    """sig: 每日在场信号（用 i-1 的值决定第 i 天）"""
    eq, peak, low, dd, wp = 1000.0, 1000.0, 1000.0, 0.0, 0.0
    x = np.zeros(nn)
    on = 0
    for i in range(61, nn):
        if sig[i - 1] and np.isfinite(vol[i - 1]) and vol[i - 1] > 0:
            w = min(3.0, TV / vol[i - 1])
            on += 1
        else:
            w = 0.0
        x[i] = w * r[i] - abs(w - wp) * FEE - w * FR[i]
        eq *= (1 + x[i])
        peak = max(peak, eq)
        low = min(low, eq)
        dd = min(dd, eq / peak - 1)
        wp = w
    y = x[61:]
    sh = y.mean() / y.std(ddof=1) * np.sqrt(365)
    return sh, eq, dd, on


base = np.nan_to_num((C > ma50).astype(float))
bsh, beq, bdd, bon = run(base, "基准")

print("=" * 92)
print(f"  基准：日线收盘 > MA50（40% 档，每天对账）")
print(f"     夏普 {bsh:.4f}   期末 {beq:.1f}x   回撤 {bdd*100:.1f}%   在场 {bon} 天")
print("=" * 92)
print()
print(f"  {'方案':<38}{'夏普':>9}{'Δ夏普':>9}{'期末':>10}{'回撤':>9}{'在场':>7}")
print("  " + "-" * 84)

RES = []


def add(label, sig, note=""):
    sh, eq, dd, on = run(sig, label)
    RES.append((label, sh, eq, dd, on, note))
    print(f"  {label:<38}{sh:>9.4f}{sh-bsh:>+9.4f}{eq:>9.1f}x{dd*100:>8.1f}%{on:>7}")


# ══ A. TSMOM 变体 ══
print("  ── A. TSMOM 变体（趋势确认）──")
add("A1 基准（MA50）", base)

ma100, ma200 = sma(C, 100), sma(C, 200)
add("A2 + MA100 > MA200（多周期确认）",
    base * np.nan_to_num((ma100 > ma200).astype(float)))
add("A3 + 6 个月收益 > 0",
    base * np.nan_to_num((np.concatenate([np.full(180, np.nan),
                                          C[180:] / C[:-180] - 1]) > 0).astype(float)))
add("A4 + 12 个月收益 > 0",
    base * np.nan_to_num((np.concatenate([np.full(365, np.nan),
                                          C[365:] / C[:-365] - 1]) > 0).astype(float)))
slope = np.full(nn, np.nan)
slope[55:] = ma50[55:] - ma50[50:-5]
add("A5 + MA50 向上倾斜", base * np.nan_to_num((slope > 0).astype(float)))
add("A6 双均线 MA20 > MA100（不用 MA50）",
    np.nan_to_num((sma(C, 20) > sma(C, 100)).astype(float)))
add("A7 三均线 MA20>MA50>MA100",
    np.nan_to_num(((sma(C, 20) > ma50) & (ma50 > ma100)).astype(float)))

# ══ B. 均值回归 ══
print()
print("  ── B. 均值回归（入场过滤）──")


def ret_n(k):
    o = np.full(nn, np.nan)
    o[k:] = C[k:] / C[:-k] - 1
    return o


r5, r20 = ret_n(5), ret_n(20)
add("B1 只在近 20 日【跌】时入场", base * np.nan_to_num((r20 < 0).astype(float)))
add("B2 只在近 20 日【涨】时入场", base * np.nan_to_num((r20 > 0).astype(float)))
add("B3 近 20 日跌超 10% 才入场", base * np.nan_to_num((r20 < -0.10).astype(float)))
add("B4 近 5 日跌超 5% 才入场", base * np.nan_to_num((r5 < -0.05).astype(float)))
add("B5 回踩 MA50 附近（±3%）才入场",
    base * np.nan_to_num((np.abs(C / ma50 - 1) < 0.03).astype(float)))
add("B6 距离 MA50 超 30% 不入场（追高过滤）",
    base * np.nan_to_num((C / ma50 - 1 < 0.30).astype(float)))

print()
print("=" * 92)
print("  排序（按 Δ夏普）")
print("=" * 92)
print()
for lab, sh, eq, dd, on, _ in sorted(RES, key=lambda x: -x[1]):
    mark = "  ← 基准" if "A1" in lab else ("  ✅ 改善" if sh > bsh + 0.01 else
                                       ("  ⚠️ 变差" if sh < bsh - 0.01 else "  ≈ 持平"))
    print(f"  {lab:<38}{sh:>9.4f}{sh-bsh:>+9.4f}{mark}")
