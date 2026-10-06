"""
非对称均线 + 震荡期均值回归（fastslow.py）
========================================
用户的想法：
  A. TSMOM 让 MA50 【更快】反应趋势的上升与下降
  B. 均值回归在【震荡周期】里提高成功率

测试：
  A 组：非对称均线（快出慢进 / 快进慢出 / 双速确认）
  B 组：震荡识别 + 在震荡期用均值回归
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
TV = 0.40

j = json.loads((ROOT / "data" / "crypto" / f"{SYM}.json").read_text(encoding="utf-8"))
T = np.array([b["t"] for b in j], float)
C = np.array([b["c"] for b in j], float)
H = np.array([b["h"] for b in j], float)
LO = np.array([b["l"] for b in j], float)
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
    c = np.cumsum(np.insert(x, 0, 0.0))
    o[k - 1:] = (c[k:] - c[:-k]) / k
    return o


def ema(x, k):
    a = 2 / (k + 1)
    o = np.zeros(len(x))
    o[0] = x[0]
    for i in range(1, len(x)):
        o[i] = a * x[i] + (1 - a) * o[i - 1]
    return o


MA = {k: sma(C, k) for k in (10, 20, 30, 50, 100, 200)}
tr = np.zeros(nn)
tr[0] = H[0] - LO[0]
tr[1:] = np.maximum(H[1:] - LO[1:],
                    np.maximum(np.abs(H[1:] - C[:-1]), np.abs(LO[1:] - C[:-1])))
atr = sma(tr, 14)

# RSI(14)
delta = np.zeros(nn)
delta[1:] = C[1:] - C[:-1]
gain = sma(np.maximum(delta, 0), 14)
loss = sma(np.maximum(-delta, 0), 14)
rsi = 100 - 100 / (1 + gain / np.where(loss == 0, np.nan, loss))
rsi = np.nan_to_num(rsi, nan=50.0)


def run(sig, label, cost=True):
    eq, pk, dd, wp = 1000.0, 1000.0, 0.0, 0.0
    x = np.zeros(nn)
    on = 0
    for i in range(61, nn):
        if sig[i - 1] and np.isfinite(vol[i - 1]) and vol[i - 1] > 0:
            w = min(3.0, TV / vol[i - 1])
            on += 1
        else:
            w = 0.0
        turn = abs(w - wp)
        x[i] = w * r[i] - (turn * FEE if cost else 0) - w * FR[i]
        eq *= (1 + x[i])
        pk = max(pk, eq)
        dd = min(dd, eq / pk - 1)
        wp = w
    y = x[61:]
    sh = y.mean() / y.std(ddof=1) * np.sqrt(365) if y.std() > 0 else 0
    return sh, eq, dd, on


base = np.nan_to_num((C > MA[50]).astype(float))
bsh, beq, bdd, bon = run(base, "base")
print("=" * 94)
print(f"  基准：收盘 > MA50（40% 档，每天对账，含手续费和资金费）")
print(f"     夏普 {bsh:.4f}   期末 {beq:,.0f}   回撤 {bdd*100:.1f}%   在场 {bon} 天")
print("=" * 94)
RES = []


def add(lab, sig):
    sh, eq, dd, on = run(sig, lab)
    RES.append((lab, sh, eq, dd, on))
    print(f"  {lab:<42}{sh:>9.4f}{sh-bsh:>+9.4f}{eq:>9.0f}{dd*100:>8.1f}%{on:>7}")


# ══ A 组：非对称均线 ══
print()
print("  ── A. 非对称均线（让进出场用不同速度）──")
add("A0 基准（进 MA50 / 出 MA50）", base)

# 快出慢进：入场要求 MA50，出场用 MA20 跌破
# 用状态机实现
def asym(ma_in, ma_out):
    s = np.zeros(nn)
    st = 0
    for i in range(60, nn):
        if st == 0:
            if np.isfinite(ma_in[i]) and C[i] > ma_in[i]:
                st = 1
        else:
            if np.isfinite(ma_out[i]) and C[i] < ma_out[i]:
                st = 0
        s[i] = st
    return s


add("A1 进 MA50 / 出 MA20（快出慢进）", asym(MA[50], MA[20]))
add("A2 进 MA20 / 出 MA50（快进慢出）", asym(MA[20], MA[50]))
add("A3 进 MA50 / 出 MA30", asym(MA[50], MA[30]))
add("A4 进 MA100 / 出 MA20（更极端）", asym(MA[100], MA[20]))
add("A5 进 MA50 且 MA20>MA50（双速确认）",
    base * np.nan_to_num((MA[20] > MA[50]).astype(float)))
add("A6 单纯用 MA20（最快的单均线）",
    np.nan_to_num((C > MA[20]).astype(float)))
add("A7 EMA20 > EMA50（指数均线）",
    np.nan_to_num((ema(C, 20) > ema(C, 50)).astype(float)))
# 唐奇安通道突破（另一种 TSMOM）
don = np.full(nn, np.nan)
for i in range(20, nn):
    don[i] = H[i - 20:i].max()
add("A8 20 日唐奇安通道突破", np.nan_to_num((C > don).astype(float)))

# ══ B 组：震荡识别 + 均值回归 ══
print()
print("  ── B. 震荡期用均值回归 ──")
# 震荡识别：|C/MA50 - 1| < 阈值 且 MA50 斜率小
slope = np.full(nn, np.nan)
slope[55:] = (MA[50][55:] - MA[50][50:-5]) / MA[50][50:-5]
range_regime = np.nan_to_num((np.abs(C / MA[50] - 1) < 0.10).astype(float)) * \
               np.nan_to_num((np.abs(slope) < 0.02).astype(float))
print(f"     （识别为震荡的天数：{int(range_regime.sum())} / {nn}）")

# B1：震荡期改用均值回归（RSI<35 买），趋势期用 MA50
mr_sig = np.nan_to_num((rsi < 35).astype(float))
add("B1 趋势期用 MA50，震荡期用 RSI<35",
    np.clip(base * (1 - range_regime) + mr_sig * range_regime, 0, 1))
add("B2 只在震荡期交易（RSI<35 买，RSI>65 卖）",
    np.clip(np.nan_to_num((rsi < 35).astype(float)) * range_regime, 0, 1))
add("B3 MA50 且 RSI < 70（避免追高超买）",
    base * np.nan_to_num((rsi < 70).astype(float)))
add("B4 MA50 且 RSI > 30（避免超卖入场）",
    base * np.nan_to_num((rsi > 30).astype(float)))
# 布林带下轨买入（均值回归的另一形式）
ma20, sd20 = sma(C, 20), np.full(nn, np.nan)
for i in range(20, nn):
    sd20[i] = C[i - 20:i].std(ddof=1)
add("B5 跌破布林下轨（MA20−2σ）才买",
    base * np.nan_to_num((C < ma20 - 2 * sd20).astype(float)))
add("B6 MA50 且不在布林上轨外（C < MA20+2σ）",
    base * np.nan_to_num((C < ma20 + 2 * sd20).astype(float)))

print()
print("=" * 94)
print("  排序")
print("=" * 94)
print()
for lab, sh, eq, dd, on in sorted(RES, key=lambda x: -x[1]):
    tag = "  ← 基准" if lab.startswith("A0") else (
        "  ✅ 改善" if sh > bsh + 0.01 else ("  ⚠️ 变差" if sh < bsh - 0.01 else "  ≈ 持平"))
    print(f"  {lab:<42}{sh:>9.4f}{sh-bsh:>+9.4f}{tag}")
