"""
全面体检（final_audit.py）
========================
按本项目历史上出现过的【错误类型】逐类排查。
"""
import ast
import importlib.util
import pathlib
import re
import sys

import numpy as np

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("m", HERE / "ma50_live.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SRC = (HERE / "ma50_live.py").read_text(encoding="utf-8")
L = SRC.split("\n")
MD = (ROOT / "ma50_rules.md").read_text(encoding="utf-8")
M = MD.split("\n")
issues = []


def bad(tag, detail):
    issues.append((tag, detail))
    print(f"  🔴 {tag}: {detail}")


def ok(tag):
    print(f"  ✅ {tag}")


print("=" * 86)
print("  【1】恒真 / 恒假条件")
print("=" * 86)
print()
hits = [(i, l.strip()) for i, l in enumerate(L, 1)
        if re.search(r"if\s+(True|False|1|0)\s*:", l)]
if hits:
    for i, l in hits:
        bad(f"L{i}", l[:70])
else:
    ok("没有 if True/False")

# 检查所有 if 的条件变量是否真的会被赋不同值
print()
print("=" * 86)
print("  【2】硬编码数字 vs 常量")
print("=" * 86)
print()
out_num = []
for i, l in enumerate(L, 1):
    ls = l.strip()
    if ls.startswith(("A(", "print(")) and re.search(r"[+\-−]?\d+\.\d+\s*%", l):
        out_num.append((i, ls[:74]))
print(f"  输出里的百分比数字 {len(out_num)} 处：")
for i, l in out_num:
    print(f"     L{i}: {l}")
if not out_num:
    ok("输出里没有硬编码的百分比")

print()
print("=" * 86)
print("  【3】静默兜底（or / 默认值吞掉错误）")
print("=" * 86)
print()
pat = re.compile(r"\bor\s+[\d\.\-]+|\bor\s+\[\]|\bor\s+\{\}|\bor\s+['\"]")
hits = [(i, l.strip()) for i, l in enumerate(L, 1)
        if pat.search(l) and not l.strip().startswith("#")]
if hits:
    for i, l in hits:
        print(f"  ⚠️ L{i}: {l[:74]}")
else:
    ok("没有可疑的 or 兜底")

print()
print("=" * 86)
print("  【4】除零风险")
print("=" * 86)
print()
for i, l in enumerate(L, 1):
    for mt in re.finditer(r"/\s*([a-z_][a-z_0-9]*)", l):
        v = mt.group(1)
        if v in ("equity", "eq", "eq_now", "price", "rvol", "vol", "notional",
                 "tgt_pos", "pos", "p10", "margin"):
            # 看这行前后有没有保护
            ctx = "\n".join(L[max(0, i - 4):i])
            guarded = (f"{v} > 0" in ctx or f"{v} <= 0" in ctx
                       or f"if {v}" in ctx or f"{v} == 0" in ctx
                       or "max(" in l or "np.isfinite" in ctx)
            if not guarded:
                print(f"  ⚠️ L{i}: 除以 {v}（邻近未见保护）  {l.strip()[:60]}")
print("  （逐条人工判断）")

print()
print("=" * 86)
print("  【5】变量名冲突（同名不同义）")
print("=" * 86)
print()
t = ast.parse(SRC)
for fn in [n for n in ast.walk(t) if isinstance(n, ast.FunctionDef)]:
    names = {}
    for nd in ast.walk(fn):
        if isinstance(nd, ast.Name) and isinstance(nd.ctx, ast.Store):
            names.setdefault(nd.id, []).append(nd.lineno)
    # 找在同一函数里被赋值为不同类型的地方（粗判：同名多次赋值且跨度大）
    for k, v in names.items():
        if len(v) > 1 and k not in ("i", "j", "n", "x", "eq", "w", "c", "r", "L", "A"):
            span = max(v) - min(v)
            if span > 40:
                print(f"  ⚠️ {fn.name}(): 变量 {k} 在 L{min(v)}~L{max(v)} 反复赋值（跨度 {span}）")
print("  （逐条人工判断）")

print()
print("=" * 86)
print("  【6】文档 vs 工具 的关键数字")
print("=" * 86)
print()
pairs = [
    ("夏普", f"{m.METHODS[0][3]:.3f}"),
    ("标准误", f"{m.SE_SHARPE:.3f}"),
    ("回测天数", f"{m.SAMPLE_DAYS}"),
    ("样本年数", f"{m.SAMPLE_YEARS:.2f}"),
    ("最坏单笔", f"{abs(m.WORST_TRADE_LOW)*100:.1f}"),
    ("安全杠杆", f"{m.MAX_SAFE_LEV}"),
]
for lab, v in pairs:
    inmd = v in MD
    print(f"  {'✅' if inmd else '🔴'} {lab} {v}{'' if inmd else '  ← 文档里找不到'}")

print()
print("=" * 86)
print("  【7】边界：极端输入")
print("=" * 86)
print()
for lab, args in (
        ("equity=0", (0, 2700, 0.5)),
        ("equity=负数", (-5, 2700, 0.5)),
        ("price=0", (100, 0, 0.5)),
        ("rvol=inf", (100, 2700, float("inf"))),
        ("equity=1e9", (1e9, 2700, 0.5)),
):
    try:
        a = m.advice(*args)
        print(f"  ✅ {lab:<14} → 版本 {a['method']:<16} 仓位 {a['position']:.4f} "
              f"fail={a['fail']!r}")
    except Exception as e:
        bad(lab, f"{type(e).__name__}: {e}")

print()
print("=" * 86)
print("  【8】文档章节编号")
print("=" * 86)
print()
seen = {}
for i, l in enumerate(M, 1):
    mt = re.match(r"^#{2,3}\s+([\d\.]+)", l)
    if mt:
        k = mt.group(1)
        if k in seen:
            bad("章节重复", f"{k} 出现在 L{seen[k]} 和 L{i}")
        seen[k] = i
print(f"  章节数 {len(seen)}：{', '.join(sorted(seen))}")

print()
print("=" * 86)
print(f"  共发现 {len(issues)} 个需要人工判断的点")
print("=" * 86)
for t, d in issues:
    print(f"  🔴 {t}: {d}")
