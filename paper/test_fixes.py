"""验证三个修复（test_fixes.py）"""
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
spec = importlib.util.spec_from_file_location("m", HERE / "ma50_live.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print("=" * 92)
print("  ① 固定版仓位随本金变（不再是常量 1.405）")
print("=" * 92)
print()
print(f"  {'权益':>9}{'目标仓位':>12}{'目标名义':>12}{'够最小单吗':>12}{'回撤':>10}{'最坏跌到':>11}")
print("  " + "-" * 68)
for eq in (14.2, 14.83, 16, 18, 19.99, 20, 30, 52.2):
    a = m.advice(eq, 2707.46, 0.445)
    print(f"  {eq:>8.2f}U{a['position']:>11.4f}x{a['notional']:>11.2f}U"
          f"{'✅' if a['feasible'] else '❌':>12}{a['drawdown']*100:>9.1f}%"
          f"{a['worst']:>10.2f}U")

print()
print("=" * 92)
print("  ② 波动率目标版在 52.3U 以上（模拟）")
print("=" * 92)
print()
print(f"  {'权益':>9}{'版本':<18}{'仓位':>10}{'名义':>11}{'回撤':>10}{'最坏跌到':>11}")
print("  " + "-" * 70)
for eq, rv in ((52.3, 0.445), (52.3, 0.90), (60, 0.30), (100, 0.445),
               (200, 0.80), (1000, 0.20)):
    a = m.advice(eq, 2707.46, rv)
    print(f"  {eq:>8.1f}U{a['method']:<18}{a['position']:>10.3f}"
          f"{a['notional']:>10.1f}U{a['drawdown']*100:>9.1f}%{a['worst']:>10.2f}U")

print()
print("=" * 92)
print("  ③ 除零：rvol 无效时不再崩，且失败原因分开报")
print("=" * 92)
print()
for rv, lab in ((None, "None"), (float("nan"), "nan"), (0.0, "0"),
                (-0.1, "负数")):
    a = m.advice(200, 2707.46, rv)
    print(f"  rvol={lab:<6} → fail={a['fail']!r:<12} 仓位={a['position']:.3f} "
          f"名义={a['notional']:.2f} 可下单={a['feasible']}")
print()
print("  ⇒ fail='vol' 表示【数据问题】，不是本金不足 ✅")

print()
print("=" * 92)
print("  ④ action 语义：信号没切换但仓位变了 → 应该写「调仓」")
print("=" * 92)
print()
# 模拟归档：昨天仓位 1.30，今天 1.00
rows_prev = [{"target_position": "1.3000", "signal": "做多"}]
for sig, prev, pos, lab in (
        (True, True, 1.349, "信号未切换，仓位 1.300→1.349（+3.8%）"),
        (True, True, 1.100, "信号未切换，仓位 1.300→1.100（-15.4%）"),
        (True, True, 1.310, "信号未切换，仓位 1.300→1.310（+0.8%）"),
        (True, False, 1.349, "信号切换：空→多"),
        (False, True, 0.0, "信号切换：多→空"),
        (False, False, 0.0, "空仓且未切换"),
        (True, None, 1.349, "建仓")):
    act = m.decide_action(sig, prev, pos, rows_prev)
    print(f"  {lab:<38}→  action = {act!r}")

print()
print("=" * 92)
print("  ⑤ 阈值常量")
print("=" * 92)
print(f"  REBALANCE_THRESHOLD = {m.REBALANCE_THRESHOLD}  "
      f"（仓位相对变化超过 {m.REBALANCE_THRESHOLD*100:.0f}% 才算需要操作）")
