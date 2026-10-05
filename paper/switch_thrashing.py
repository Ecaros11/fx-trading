"""
版本切换抖动：三种修法的对比
==========================
复刻 ma50_live.dynamic_drawdown 的逻辑（不修改工具本身），
加上三种切换策略，量化各自的代价。

切换策略：
  ① none      无处理（现状）—— 越过门槛立刻切
  ② hysteresis 滞回带 —— 升级要越过 门槛×1.05，降级要跌破 门槛×0.95
  ③ cooldown  冷却期 —— 切换后 N 天内不再切换

衡量指标：
  · 切换次数
  · 最大回撤 / 期末权益
  · 总换手（|Δw| 之和）
  · 【切换额外成本】= 每次切换时 |Δw| 里超出正常调仓的那部分
    这是关键：切换会强制改目标仓位，产生一次额外的买卖
"""
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import ma50_live as M

d1 = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))
fd = json.loads((ROOT / "data" / "funding" / "ETHUSDT.json").read_text(encoding="utf-8"))
fday = {}
for x in fd:
    k = int(x["t"] // 86400000)
    fday[k] = fday.get(k, 0.0) + x["rate"]

bars, _ = M.complete_bars(d1)
C = np.array([b["c"] for b in bars], float)
T = np.array([b["t"] for b in bars], float)
n = len(C)
r = np.zeros(n)
r[1:] = C[1:] / C[:-1] - 1
sg = np.nan_to_num((C > M.ma(C, M.MA_WINDOW)).astype(float))
cd = np.array([int(t // 86400000) for t in T])
FR = np.nan_to_num(np.array([fday.get(int(d), np.nan) for d in cd]))
vol = np.full(n, np.nan)
for i in range(21, n):
    vol[i] = r[i - 20:i].std(ddof=1) * np.sqrt(365)

THR = [m[2] for m in M.METHODS]
NAMES = [m[0] for m in M.METHODS]


def pick_with(eq, mode, cur_idx, last_switch_i, i, band, cooldown, forced_idx=None):
    """返回匹配到的版本下标"""
    if forced_idx is not None:
        return forced_idx
    # 基础匹配
    base = 0
    for j, t in enumerate(THR):
        if eq >= t:
            base = j
    if mode == "none":
        return base
    if mode == "hysteresis":
        # 升级要越过 门槛×1.05；降级要跌破 门槛×0.95
        idx = cur_idx
        while idx + 1 < len(THR) and eq >= THR[idx + 1] * (1 + band):
            idx += 1
        while idx > 0 and eq < THR[idx] * (1 - band):
            idx -= 1
        return idx
    if mode == "cooldown":
        if i - last_switch_i < cooldown:
            return cur_idx
        return base
    return base


def simulate(start_equity, mode="none", band=0.05, cooldown=20,
             switch_penalty=0.0):
    """
    switch_penalty: 每次版本切换额外扣的比例（模拟"为切换而交易"的滑点/冲击）
                   0 = 不额外扣（只看 |Δw| 已经计入的部分）
    """
    eq = float(start_equity)
    peak = eq
    dd = 0.0
    low = eq
    cur_idx = pick_with(eq, "none", 0, -999, M.MA_WINDOW, band, cooldown)
    n_sw = 0
    last_sw = -999
    turn_total = 0.0
    turn_switch = 0.0
    fee_total = 0.0
    penalty_total = 0.0

    def _w(eq_now, i, idx):
        if not sg[i] or eq_now <= 0:
            return 0.0
        tv = M.METHODS[idx][1]
        if tv is None:
            return max(1.0, M.MIN_NOTIONAL / eq_now)
        v = vol[i]
        if not np.isfinite(v) or v <= 0:
            return 0.0
        return min(3.0, tv / v)

    for i in range(M.MA_WINDOW + 1, n):
        new_idx = pick_with(eq, mode, cur_idx, last_sw, i, band, cooldown)
        if new_idx != cur_idx:
            n_sw += 1
            last_sw = i
            cur_idx = new_idx
            if switch_penalty:
                eq -= eq * switch_penalty
                penalty_total += eq * switch_penalty
        w = _w(eq, i - 1, cur_idx)
        w_prev = _w(eq, i - 2, cur_idx)
        turn = abs(w - w_prev)
        turn_total += turn
        fee = turn * M.FEE_PER_SIDE * 2
        fee_total += fee * eq
        eq *= (1 + w * r[i] - fee - w * FR[i])
        if eq <= 0:
            eq = 0.0
            break
        peak = max(peak, eq)
        low = min(low, eq)
        dd = min(dd, eq / peak - 1)
    return dict(dd=dd, low=low, peak=peak, end=eq, switches=n_sw,
                turn=turn_total, fee=fee_total, penalty=penalty_total)


print("=" * 104)
print("  ① 复现当前工具的结果（确认我的复刻正确）")
print("=" * 104)
print(f"  {'起始权益':>10}{'回撤':>10}{'路径最低':>11}{'期末':>11}{'切换次数':>10}")
print("  " + "-" * 56)
mine = {}
for eq0 in (14.78, 30, 60, 100, 200, 500):
    a = simulate(eq0, "none")
    mine[eq0] = a
    print(f"  {eq0:>9.2f}U{a['dd']*100:>9.1f}%{a['low']:>10.2f}U"
          f"{a['end']:>10.2f}U{a['switches']:>10}")
print()
print("  用户报的：")
print("    14.78U  -37.4%  14.25U  191.11U  132")
print("    100U    -13.3%  99.20U  325.14U  132")
print("    500U    -13.3%  497.61U 1392.79U 124")

print()
print("=" * 104)
print("  ② 三种修法对比（起始 100U）")
print("=" * 104)
print(f"  {'修法':<34}{'切换':>7}{'回撤':>10}{'期末':>11}"
      f"{'总换手':>9}{'切换惩罚后':>12}")
print("  " + "-" * 86)
for lab, kw in (("① 无处理（现状）", dict(mode="none")),
                ("② 滞回带 ±2%", dict(mode="hysteresis", band=0.02)),
                ("② 滞回带 ±5%", dict(mode="hysteresis", band=0.05)),
                ("② 滞回带 ±10%", dict(mode="hysteresis", band=0.10)),
                ("③ 冷却 20 天", dict(mode="cooldown", cooldown=20)),
                ("③ 冷却 60 天", dict(mode="cooldown", cooldown=60)),
                ("③ 冷却 120 天", dict(mode="cooldown", cooldown=120))):
    a = simulate(100, **kw)
    b = simulate(100, switch_penalty=0.002, **kw)
    print(f"  {lab:<34}{a['switches']:>7}{a['dd']*100:>9.1f}%{a['end']:>10.2f}U"
          f"{a['turn']:>9.1f}{b['end']:>11.2f}U")

print()
print("=" * 104)
print("  ③ 切换到底产生多少额外成本（关键问题）")
print("=" * 104)
print("  每次切换会强制改目标仓位。看切换那一天的 |Δw|：")
print()
print(f"  {'起始':>8}{'切换次数':>9}{'切换当天的|Δw|合计':>21}"
      f"{'占总换手':>10}{'按 10bp 计':>12}{'年化':>9}")
print("  " + "-" * 74)
yrs = (n - M.MA_WINDOW - 1) / 365
for eq0 in (100, 200, 500):
    # 记录每次切换当天的 |Δw|
    eq = float(eq0); cur_idx = 0; sw_turn = 0.0; tot = 0.0
    for i in range(M.MA_WINDOW + 1, n):
        base = 0
        for j, t in enumerate(THR):
            if eq >= t:
                base = j
        switched = base != cur_idx
        if switched:
            cur_idx = base
        def _w(e, ii, idx):
            if not sg[ii] or e <= 0: return 0.0
            tv = M.METHODS[idx][1]
            if tv is None: return max(1.0, M.MIN_NOTIONAL / e)
            v = vol[ii]
            if not np.isfinite(v) or v <= 0: return 0.0
            return min(3.0, tv / v)
        w = _w(eq, i - 1, cur_idx)
        wp = _w(eq, i - 2, cur_idx)
        t_ = abs(w - wp)
        tot += t_
        if switched:
            sw_turn += t_
        eq *= (1 + w * r[i] - t_ * M.FEE_PER_SIDE * 2 - w * FR[i])
        if eq <= 0: break
    pct = sw_turn / tot * 100 if tot else 0
    cost = sw_turn * M.FEE_PER_SIDE * 2
    print(f"  {eq0:>7.0f}U{tot and '' or ''}{'':>9}{sw_turn:>20.1f}"
          f"{pct:>9.0f}%{cost*100:>11.2f}%{cost/yrs*100:>8.2f}%")
print()
print("  ⚠️ 注意上表的「切换当天的 |Δw|」包含了正常调仓，不是纯增量。")
print("     纯增量要看：切换前后的目标仓位差多少。")

print()
print("=" * 104)
print("  ④ 切换时的仓位跳变幅度（这才是真实的额外成本）")
print("=" * 104)
print("  门槛两侧的目标仓位差：")
print()
print("  示例：20 日已实现波动 = 72%（2022 年那种行情）")
V = 0.72
print(f"  {'权益':>10}{'匹配版本':<18}{'目标仓位':>10}{'名义':>10}   说明")
print("  " + "-" * 66)
for eq0 in (52.29, 52.31, 83.69, 83.71, 139.49, 139.51):
    idx = 0
    for j, t in enumerate(THR):
        if eq0 >= t:
            idx = j
    tv = M.METHODS[idx][1]
    pos = max(1.0, M.MIN_NOTIONAL / eq0) if tv is None else min(3.0, tv / V)
    print(f"  {eq0:>9.2f}U{M.METHODS[idx][0]:<18}{pos:>10.4f}{eq0*pos:>9.2f}U")
print()
print(f"  ⇒ 52.29 → 52.31（涨 0.02U）：仓位从 1.0x 跳到 {min(3.0, 0.40/V):.4f}x")
d_pos = abs(min(3.0, 0.40/V) - 1.0)
print(f"     跳变 {d_pos:.4f}x  ⇒ 名义变化 {52.3*d_pos:.2f}U")
print(f"     为这个跳变交易一次的成本 ≈ {52.3*d_pos*0.001:.4f}U = "
      f"{52.3*d_pos*0.001/52.3*100:.4f}% 权益")

print()
print("=" * 104)
print("  ⑤ 滞回带怎么选：带宽 vs 生效次数")
print("=" * 104)
print(f"  {'带宽':>8}{'切换次数(100U)':>16}{'切换次数(500U)':>16}"
      f"{'回撤(100U)':>13}{'期末(100U)':>13}")
print("  " + "-" * 70)
for band in (0.0, 0.01, 0.02, 0.05, 0.10, 0.20, 0.50):
    a = simulate(100, "hysteresis", band=band)
    b = simulate(500, "hysteresis", band=band)
    print(f"  {band*100:>7.0f}%{a['switches']:>16}{b['switches']:>16}"
          f"{a['dd']*100:>12.1f}%{a['end']:>12.2f}U")
