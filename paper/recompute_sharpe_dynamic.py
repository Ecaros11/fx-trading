"""
动态仓位的夏普 —— 直接调工具的函数，不自己重写
============================================
上一版我自己重写动态仓位，算出夏普 2.75、期末 15.7 万 U，明显错了。
这次改成：复刻工具 dynamic_drawdown 的循环（逐行照抄），额外记录每日收益。
并用工具自身的返回值做交叉验证。
"""
import datetime as dt
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ma50_live as M
from align import sharpe, max_dd, cagr

ROOT = pathlib.Path(__file__).parent.parent
d1 = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))
bars, note = M.complete_bars(d1)
fday = M.funding_by_day()

# ── 先调工具自己的函数，拿到权威结果 ──
EQ0 = 14.78
ref = M.dynamic_drawdown(bars, fday, EQ0)
print("=" * 96)
print("  工具自己的 dynamic_drawdown（权威值）")
print("=" * 96)
print(f"  dd {ref['dd']*100:.2f}%   low {ref['low']:.2f}   peak {ref['peak']:.2f}   "
      f"end {ref['end']:.2f}   切换 {ref['switches']}")

# ── 逐行照抄工具的循环，额外记每日权益 ──
C = np.array([b["c"] for b in bars], float)
T = np.array([b["t"] for b in bars], float)
n = len(C)
r = np.zeros(n)
r[1:] = C[1:] / C[:-1] - 1
m = M.ma(C, M.MA_WINDOW)
sg = np.nan_to_num((C > m).astype(float))
cd = np.array([int(t // 86400000) for t in T])
FR = np.nan_to_num(np.array([fday.get(int(d), np.nan) for d in cd]))
vol = np.full(n, np.nan)
for i in range(21, n):
    vol[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)


def _pos(eq_now, i):
    if eq_now <= 0:
        return 0.0, None
    if not sg[i]:
        return 0.0, None
    mname, tv, need = M.pick_method(eq_now)[:3]
    if tv is None:
        return max(1.0, M.MIN_NOTIONAL / eq_now), mname
    v = vol[i]
    if not np.isfinite(v) or v <= 0:
        return 0.0, mname
    return min(3.0, tv / v), mname


eq = float(EQ0)
peak = eq
dd = 0.0
low = eq
cur = None
sw = 0
eqs = [eq]
for i in range(M.MA_WINDOW + 1, n):
    w, mname = _pos(eq, i - 1)
    if mname is not None and mname != cur:
        if cur is not None:
            sw += 1
        cur = mname
    w_prev = _pos(eq, i - 2)[0]
    turn = abs(w - w_prev)
    eq *= (1 + w * r[i] - turn * M.FEE_PER_SIDE - w * FR[i])
    if eq <= 0:
        eq = 0.0
        break
    peak = max(peak, eq)
    low = min(low, eq)
    dd = min(dd, eq / peak - 1)
    eqs.append(eq)

eqs = np.array(eqs)
print()
print("  我的复刻：")
print(f"  dd {dd*100:.2f}%   low {low:.2f}   peak {peak:.2f}   end {eq:.2f}   切换 {sw}")
ok = (abs(dd - ref["dd"]) < 1e-9 and abs(low - ref["low"]) < 1e-9
      and abs(peak - ref["peak"]) < 1e-9 and abs(eq - ref["end"]) < 1e-9)
print(f"  ⇒ 与工具 {'✅ 完全一致' if ok else '🔴 不一致'}")

rets = np.diff(eqs) / eqs[:-1]
rets = rets[np.isfinite(rets)]

print()
print("=" * 96)
print("  动态仓位的夏普（起始 14.78U）")
print("=" * 96)
print(f"  天数            {len(rets)}")
print(f"  算术夏普        {sharpe(rets):.4f}")
lv = np.log1p(np.maximum(rets, -0.999999))
print(f"  对数夏普        {lv.mean()/lv.std()*np.sqrt(365):.4f}")
print(f"  年化收益(几何)  {cagr(rets)*100:.2f}%")
print(f"  年化波动        {rets.std()*np.sqrt(365)*100:.2f}%")
print(f"  最大回撤        {max_dd(rets)*100:.2f}%")
print(f"  终值            {EQ0} → {eq:.2f} U  （{eq/EQ0:.1f} 倍）")

print()
print("=" * 96)
print("  起始权益敏感性（动态仓位的夏普依赖起始权益吗）")
print("=" * 96)
print(f"  {'起始':>9}{'夏普':>9}{'回撤':>10}{'终值':>12}{'切换':>7}")
print("  " + "-" * 50)
for e0 in (14.20, 14.78, 20.0, 30.0, 52.3, 100.0, 500.0):
    rr = M.dynamic_drawdown(bars, fday, e0)
    # 用同样方式重算收益序列
    eqq = float(e0); eqs2 = [eqq]; cur2 = None
    for i in range(M.MA_WINDOW + 1, n):
        w, mn = _pos(eqq, i - 1)
        if mn is not None and mn != cur2:
            cur2 = mn
        wp = _pos(eqq, i - 2)[0]
        t_ = abs(w - wp)
        eqq *= (1 + w * r[i] - t_ * M.FEE_PER_SIDE - w * FR[i])
        if eqq <= 0:
            eqq = 0.0; break
        eqs2.append(eqq)
    e2 = np.array(eqs2)
    r2 = np.diff(e2) / e2[:-1]
    r2 = r2[np.isfinite(r2)]
    print(f"  {e0:>8.2f}U{sharpe(r2):>9.3f}{rr['dd']*100:>9.1f}%{rr['end']:>11.2f}U"
          f"{rr['switches']:>7}")

print()
print("=" * 96)
print("  结论")
print("=" * 96)
print("""
  两类夏普要分开说：

  ① 恒定 1.405x（回测/文档口径，也是对未来的保守假设）
       夏普 1.048    回撤 -71.2%   年化 67.7%

  ② 动态仓位（按权益匹配版本，起始 14.78U）
       夏普 见上     回撤 -37.2%   终值 193.72U

  差别来源：权益涨过 20U 后，仓位公式自动降到 1.0x
            ⇒ 仓位序列【时变】⇒ 夏普会变
            （夏普只对【恒定】缩放不变）
""")
