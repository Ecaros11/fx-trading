"""
切换次数到底该怎么数 —— 以及哪种修法真的有用
==========================================
我的复刻对上回撤和路径最低（−37.4% / 14.25U / −13.3% / 99.20U），
但切换次数对不上（我 0~31，用户 124~132）。

差异来源推测：
  工具里 cur = _pos(eq, MA_WINDOW)[1]，而 _pos 在【无信号】时返回
  METHODS[0][0]（固定版名）。所以信号一平掉，cur 就被重置成"固定版"；
  下次有信号时又会变回匹配版 ⇒ 每次进出场都记一次"切换"。

  这种"切换"发生在【空仓时】，不需要任何交易 ⇒ 成本为零。
  真正有成本的是【在场时】切换 —— 那会强制改目标仓位。
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


def idx_of(eq, mode, cur, band, last_sw, i, cooldown):
    base = 0
    for j, t in enumerate(THR):
        if eq >= t:
            base = j
    if mode == "none":
        return base
    if mode == "hyst":
        j = cur
        while j + 1 < len(THR) and eq >= THR[j + 1] * (1 + band):
            j += 1
        while j > 0 and eq < THR[j] * (1 - band):
            j -= 1
        return j
    if mode == "cool":
        return cur if (i - last_sw) < cooldown else base
    return base


def run(eq0, mode="none", band=0.05, cooldown=60):
    eq = float(eq0); peak = eq; dd = 0.0; low = eq
    cur = 0
    sw_flat = 0        # 空仓时的"切换"（无成本）
    sw_live = 0        # 在场时的切换（有成本）
    sw_live_jump = 0.0  # 在场切换时的仓位跳变（|Δw| 的纯增量）
    last_sw = -999
    turn_tot = 0.0

    def _w(e, i, idx):
        if not sg[i] or e <= 0:
            return 0.0
        tv = M.METHODS[idx][1]
        if tv is None:
            return max(1.0, M.MIN_NOTIONAL / e)
        v = vol[i]
        if not np.isfinite(v) or v <= 0:
            return 0.0
        return min(3.0, tv / v)

    for i in range(M.MA_WINDOW + 1, n):
        new = idx_of(eq, mode, cur, band, last_sw, i, cooldown)
        if new != cur:
            # 有没有实际持仓？有 ⇒ 这次切换要动仓
            w_now = _w(eq, i - 1, cur)      # 切换前该持多少
            w_new = _w(eq, i - 1, new)      # 切换后该持多少
            if w_now > 0:
                sw_live += 1
                sw_live_jump += abs(w_new - w_now)
            else:
                sw_flat += 1
            cur = new
            last_sw = i
        w = _w(eq, i - 1, cur)
        wp = _w(eq, i - 2, cur)
        t_ = abs(w - wp)
        turn_tot += t_
        eq *= (1 + w * r[i] - t_ * M.FEE_PER_SIDE * 2 - w * FR[i])
        if eq <= 0:
            eq = 0.0; break
        peak = max(peak, eq); low = min(low, eq); dd = min(dd, eq / peak - 1)
    return dict(dd=dd, low=low, end=eq, sw_flat=sw_flat, sw_live=sw_live,
                jump=sw_live_jump, turn=turn_tot)


print("=" * 100)
print("  ① 两种「切换」要分开数")
print("=" * 100)
print(f"  {'起始权益':>10}{'空仓时切换':>12}{'在场时切换':>12}"
      f"{'在场切换的仓位跳变':>20}{'总换手':>10}")
print("  " + "-" * 68)
for eq0 in (14.78, 30, 60, 100, 200, 500):
    a = run(eq0)
    print(f"  {eq0:>9.2f}U{a['sw_flat']:>12}{a['sw_live']:>12}"
          f"{a['jump']:>20.3f}{a['turn']:>10.1f}")
print()
print("  ⇒ 「空仓时切换」不需要任何交易 ⇒ 成本为零（但工具会计数）")
print("  ⇒ 「在场时切换」才真的改目标仓位 ⇒ 有成本")

print()
print("=" * 100)
print("  ② 在场切换的成本有多大")
print("=" * 100)
yrs = (n - M.MA_WINDOW - 1) / 365
print(f"  {'起始':>8}{'在场切换':>10}{'跳变合计':>11}{'成本(10bp)':>12}"
      f"{'占总换手':>10}{'年化':>9}")
print("  " + "-" * 62)
for eq0 in (14.78, 30, 60, 100, 200, 500):
    a = run(eq0)
    cost = a['jump'] * M.FEE_PER_SIDE * 2
    print(f"  {eq0:>7.2f}U{a['sw_live']:>10}{a['jump']:>11.3f}"
          f"{cost*100:>11.3f}%{a['jump']/a['turn']*100 if a['turn'] else 0:>9.1f}%"
          f"{cost/yrs*100:>8.3f}%")
print()
print("  ⚠️ 对比：正常换手成本年化约 1.3%（固定版）/ 0.9%（波动率目标版）")

print()
print("=" * 100)
print("  ③ 哪种修法能真正减少「在场切换」")
print("=" * 100)
print(f"  {'修法':<28}{'起始100U 在场切换':>20}{'起始14.78U':>14}"
      f"{'回撤(100U)':>12}{'期末(100U)':>12}")
print("  " + "-" * 88)
cfgs = [("① 无处理", dict(mode="none")),
        ("② 滞回 ±1%", dict(mode="hyst", band=0.01)),
        ("② 滞回 ±2%", dict(mode="hyst", band=0.02)),
        ("② 滞回 ±5%", dict(mode="hyst", band=0.05)),
        ("② 滞回 ±10%", dict(mode="hyst", band=0.10)),
        ("③ 冷却 20 天", dict(mode="cool", cooldown=20)),
        ("③ 冷却 60 天", dict(mode="cool", cooldown=60)),
        ("③ 冷却 120 天", dict(mode="cool", cooldown=120)),
        ("③ 冷却 250 天", dict(mode="cool", cooldown=250))]
for lab, kw in cfgs:
    a = run(100, **kw)
    b = run(14.78, **kw)
    print(f"  {lab:<28}{a['sw_live']:>20}{b['sw_live']:>14}"
          f"{a['dd']*100:>11.1f}%{a['end']:>11.2f}U")

print()
print("=" * 100)
print("  ④ 为什么滞回带比冷却期更合适")
print("=" * 100)
print("""
  滞回带（hysteresis）：
    · 是【状态相关】的 —— 只在门槛附近才起作用
    · 权益离门槛很远时，行为和"无处理"完全一样
    · 数学上有明确含义："越过门槛 5% 才认为真的跨过去了"
    · 不引入时间依赖，重启工具后状态可复现

  冷却期（cooldown）：
    · 是【时间相关】的 —— 不管你离门槛多远，N 天内一律不切
    · 引入一个没有经济含义的参数（为什么是 60 天？）
    · 权益在门槛上方一路涨，冷却期到了却没切 ⇒ 该升级时不升级
    · 工具重启后 last_switch 丢失 ⇒ 行为不可复现
""")

print("=" * 100)
print("  ⑤ 滞回带的边界：带宽多大才合适")
print("=" * 100)
print(f"  {'带宽':>7}{'升级门槛(52.3)':>15}{'降级门槛(52.3)':>15}"
      f"{'在场切换':>10}{'回撤':>10}{'期末':>11}")
print("  " + "-" * 72)
for band in (0.0, 0.01, 0.02, 0.05, 0.10, 0.20):
    a = run(100, "hyst", band=band)
    print(f"  {band*100:>6.0f}%{52.3*(1+band):>14.2f}U{52.3*(1-band):>14.2f}U"
          f"{a['sw_live']:>10}{a['dd']*100:>9.1f}%{a['end']:>10.2f}U")
print()
print("  代价分析：带宽太大 ⇒ 该升级时不升级")
print(f"  {'带宽':>7}{'升级时权益':>12}{'本该升级的权益':>16}{'延迟幅度':>11}")
print("  " + "-" * 50)
for band in (0.0, 0.02, 0.05, 0.10, 0.20):
    print(f"  {band*100:>6.0f}%{52.3*(1+band):>11.2f}U{52.3:>15.2f}U"
          f"{band*100:>10.0f}%")
