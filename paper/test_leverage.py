"""整数杠杆功能的测试（test_leverage.py）"""
import importlib.util
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
spec = importlib.util.spec_from_file_location("m", HERE / "ma50_live.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ok = bad = 0


def chk(c, lab, d=""):
    global ok, bad
    if c:
        ok += 1
        print(f"  ✅ {lab}")
    else:
        bad += 1
        print(f"  ❌ {lab}   {d}")


print("=" * 90)
print("  ① 杠杆设置必须是整数，且刚好够开")
print("=" * 90)
print()
print(f"  {'权益':>9}{'仓位':>10}{'设杠杆':>9}{'名义':>10}{'保证金':>10}"
      f"{'≤权益?':>9}{'整数?':>7}")
print("  " + "-" * 68)
for eq in (14.20, 14.5, 14.80, 16, 18, 19.9, 20, 30, 52.2, 100):
    a = m.advice(eq, 2707, 0.445)
    lp = a["lev_plan"]
    chk_int = float(lp["lev"]).is_integer()
    fits = lp["margin"] <= eq + 1e-9
    print(f"  {eq:>8.2f}U{a['position']:>10.4f}{lp['lev']:>8}x{a['notional']:>9.2f}U"
          f"{lp['margin']:>9.2f}U{'✅' if fits else '❌':>9}{'✅' if chk_int else '❌':>7}")
    chk(chk_int, f"{eq}U 杠杆是整数", f"{lp['lev']}")
    chk(fits, f"{eq}U 保证金 {lp['margin']:.2f} ≤ 权益 {eq}", "")

print()
print("=" * 90)
print("  ② 杠杆设置 = ceil(仓位)（且是满足条件的最小整数）")
print("=" * 90)
print()
for eq in (14.2, 14.8, 16, 19.9, 20, 30):
    a = m.advice(eq, 2707, 0.445)
    expect = max(1, int(np.ceil(a["position"] - 1e-9)))
    chk(a["lev_plan"]["lev"] == expect,
        f"{eq}U: ceil({a['position']:.4f}) = {expect} == {a['lev_plan']['lev']}")
    # 更小的整数杠杆应该不够
    if expect > 1:
        need = a["notional"] / (expect - 1)
        chk(need > eq, f"{eq}U: {expect-1}x 不够（需保证金 {need:.2f} > {eq}）")

print()
print("=" * 90)
print("  ③ 强平价公式自洽性")
print("=" * 90)
print()
a = m.advice(14.80, 2707, 0.445)
lp = a["lev_plan"]
notion = a["notional"]
# 全仓：account = equity - px*notional 应为 0
for mode in ("iso", "cross"):
    px = lp[f"liq_{mode}_px"]
    acc = 14.80 - px * notion
    print(f"  {mode:<6} 标的跌 {px*100:.2f}%  →  账户 {acc:+.4f} USDT  "
          f"（{mode}=iso 时应等于富余 {lp['spare']:.4f}；cross 时应 ≈ 0）")
print()
# ⚠️ 强平的定义是"剩余保证金 = 维持保证金"，不是"剩余 = 0"。
#    所以强平时账户里还剩 MMR × 名义（这部分交易所留着做维持保证金）。
mm_keep = m.MMR * notion
chk(abs((14.80 - lp["liq_cross_px"] * notion) - mm_keep) < 0.005,
    f"全仓强平时账户 = 维持保证金 {mm_keep:.4f}",
    f"{14.80 - lp['liq_cross_px']*notion:.4f} vs {mm_keep:.4f}")
chk(abs((14.80 - lp["liq_iso_px"] * notion) - (lp["spare"] + mm_keep)) < 0.005,
    f"逐仓强平时账户 = 富余 + 维持保证金 {lp['spare']+mm_keep:.4f}",
    f"{14.80 - lp['liq_iso_px']*notion:.4f}")

print()
print("=" * 90)
print("  ④ 全仓/逐仓的建议是否与历史回撤一致")
print("=" * 90)
print()
for eq in (14.2, 14.83, 16, 18, 20, 30, 52.2):
    a = m.advice(eq, 2707, 0.445)
    lp = a["lev_plan"]
    need_cross = lp["liq_iso_acc"] > a["drawdown"]
    print(f"  {eq:>6.2f}U  历史回撤 {a['drawdown']*100:>6.1f}%   "
          f"逐仓强平账户 {lp['liq_iso_acc']*100:>6.1f}%   "
          f"⇒ {'用全仓' if need_cross else '都行'}")

print()
print("=" * 90)
print("  ⑤ 边界")
print("=" * 90)
print()
for eq, rv, lab in ((0, 0.445, "权益 0"), (1, 0.445, "权益 1U"),
                    (14.8, None, "rvol=None"), (14.8, float("nan"), "rvol=nan")):
    try:
        a = m.advice(eq, 2707, rv)
        lp = a["lev_plan"]
        print(f"  {lab:<14} → 仓位 {a['position']:.4f}  lev_plan="
              f"{'None' if lp is None else lp['lev']}  fail={a['fail']!r}  "
              f"可下单={a['feasible']}")
        chk(True, f"{lab} 不崩")
    except Exception as e:
        chk(False, f"{lab} 不崩", f"{type(e).__name__}: {e}")

print()
print("=" * 90)
print(f"  {ok} 通过 / {bad} 失败")
print("=" * 90)
