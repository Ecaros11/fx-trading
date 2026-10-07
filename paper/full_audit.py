"""
完整检查（full_audit.py）—— 只报告，不修改
"""
import collections
import datetime as dt
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import numpy as np

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
PY = sys.executable
ISSUES = []


def issue(sev, title, why, fix):
    ISSUES.append((sev, title, why, fix))
    print(f"\n  [{sev}] {title}")
    print(f"        原因：{why}")
    print(f"        办法：{fix}")


print("=" * 92)
print("  A. 运行模式")
print("=" * 92)
for f in ("--selfcheck", "--check", "--archive", "--backfill", "--history",
          "--console", "--rebuild", "--sync-methods"):
    r = subprocess.run([PY, str(HERE / "ma50_live.py"), f],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(HERE), timeout=200)
    print(f"  {'✅' if r.returncode == 0 else '❌'} {f}  exit={r.returncode}")

print()
print("=" * 92)
print("  B. 检查脚本")
print("=" * 92)
SCRIPTS = ["test_leverage.py", "test_fixes.py", "test_ma50_fail.py",
           "check_doc_tool.py", "check_thresholds.py", "verify_rebalance.py",
           "verify_all_lookahead.py", "calc_thresholds.py", "leverage_integer.py",
           "adverse_excursion.py", "dynamic_vs_const.py",
           "verify_funding.py", "full_check.py"]
for t in SCRIPTS:
    p = HERE / t
    if not p.exists():
        print(f"  ⚠️ {t} 不存在")
        continue
    r = subprocess.run([PY, str(p)], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(HERE), timeout=200)
    print(f"  {'✅' if r.returncode == 0 else '❌'} {t}  exit={r.returncode}")

print()
print("=" * 92)
print("  C. 工具常量 vs 实算")
print("=" * 92)
sm = importlib.util.spec_from_file_location("ml", HERE / "ma50_live.py")
m = importlib.util.module_from_spec(sm)
sm.loader.exec_module(m)
sa = importlib.util.spec_from_file_location("al", HERE / "align.py")
al = importlib.util.module_from_spec(sa)
sa.loader.exec_module(al)

bars = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))
cb, cbnote = m.complete_bars(bars)
C = np.array([b["c"] for b in cb], float)
fr = json.loads((ROOT / "data" / "funding" / "ETHUSDT.json").read_text(encoding="utf-8"))
agg = collections.OrderedDict()
for x in fr:
    agg.setdefault(int(x["t"] // 86400000), []).append(x["rate"])
CD = np.array([int(b["t"] // 86400000) for b in cb])
FR = np.nan_to_num(np.array(
    [float(np.sum(agg[int(d)])) if int(d) in agg else np.nan for d in CD]))
P = al.panel(C, FR)
W = 60

print(f"  complete_bars        {cbnote}  ⇒ {len(cb)} 根")
print(f"  SAMPLE_DAYS 工具={m.SAMPLE_DAYS}  实算={len(cb)-W}  "
      f"{'✅' if m.SAMPLE_DAYS == len(cb)-W else '❌'}")
print(f"  SAMPLE_YEARS 工具={m.SAMPLE_YEARS}  实算={round((len(cb)-W)/365,2)}  "
      f"{'✅' if abs(m.SAMPLE_YEARS-(len(cb)-W)/365) < 0.005 else '❌'}")
print()
print(f"  {'档位':<18}{'表里门槛':>10}{'实算':>10}{'表里夏普':>11}{'实算':>10}"
      f"{'表里回撤':>11}{'实算':>10}")
for name, tv, need, sh, dd in m.METHODS:
    x = P.net(tv, lev=(m.LEVERAGE if tv is None else None))[W:]
    x = x[np.isfinite(x)]
    rsh, rdd = al.sharpe(x), al.max_dd(x)
    w = P.weight(tv)[W:]
    on = w[w > 0]
    rneed = need if tv is None else round(20.0 / np.percentile(on, 10), 1)
    f1 = "✅" if abs(rsh - sh) < 0.003 else "❌"
    f2 = "✅" if abs(rdd - dd) < 0.005 else "❌"
    f3 = "✅" if abs(rneed - need) < 0.5 else "❌"
    print(f"  {name:<18}{need:>9.1f}U{rneed:>9.1f}U{sh:>11.4f}{rsh:>10.4f}"
          f"{dd*100:>10.1f}%{rdd*100:>9.1f}%  {f1}{f2}{f3}")

Sd = m.METHODS[1][3] / np.sqrt(365)
se = np.sqrt((1 + Sd ** 2 / 2) / m.SAMPLE_DAYS) * np.sqrt(365)
print(f"\n  SE_SHARPE 工具={m.SE_SHARPE}  实算={se:.4f}  "
      f"{'✅' if abs(m.SE_SHARPE-se) < 0.002 else '❌'}")

print()
print("=" * 92)
print("  D. 文档 vs 工具")
print("=" * 92)
DOCS = [ROOT / "ma50_rules.md", ROOT / "用户使用说明.md"]
for d in DOCS:
    t = d.read_text(encoding="utf-8")
    print(f"\n  ── {d.name} （{len(t.splitlines())} 行，{len(t)/1024:.1f} KB）──")
    CHECKS = [
        (f"含 60% 档夏普 {m.METHODS[1][3]:.4f}", f"{m.METHODS[1][3]:.4f}" in t),
        (f"含 40% 档夏普 {m.METHODS[2][3]:.4f}", f"{m.METHODS[2][3]:.4f}" in t),
        (f"含 25% 档夏普 {m.METHODS[3][3]:.4f}", f"{m.METHODS[3][3]:.4f}" in t),
        (f"含 60% 门槛 {m.METHODS[1][2]:.1f}", f"{m.METHODS[1][2]:.1f}" in t),
        (f"含 SE {m.SE_SHARPE}", f"{m.SE_SHARPE}" in t),
        (f"含样本天数 {m.SAMPLE_DAYS}",
         f"{m.SAMPLE_DAYS}" in t or d.name != "ma50_rules.md"),
        ("不含 1.3407（旧夏普）", "1.3407" not in t),
        ("不含 2447（除 §12.5 举例外）",
         "2447" not in t or "事故 3" in t),
        ("不含 −52.4%（旧回撤）", "52.4" not in t),
        ("不含 2026-10-06", "2026-10-06" not in t),
        ("不含 6.69（旧年数）", "6.69" not in t),
    ]
    for lab, ok in CHECKS:
        print(f"     {'✅' if ok else '❌'} {lab}")
        if not ok:
            ISSUES.append(("中", f"{d.name}: {lab}", "文档与工具不同步", "同步该处数字"))

print()
print("=" * 92)
print("  E. 陈旧文本扫描（工具源码）")
print("=" * 92)
src = (HERE / "ma50_live.py").read_text(encoding="utf-8")
PATTERNS = [
    (r"1\.3407", "旧夏普"),
    (r"1\.2442", "V1 夏普（注释里作为历史记录可接受）"),
    (r"2447", "旧样本天数"),
    (r"2445", "更旧的样本天数"),
    (r"6\.69", "旧样本年数"),
    (r"52\.4", "旧回撤"),
    (r"2506 根", "旧根数写法"),
    (r"固定仓位信号", "V2 已不是固定仓位"),
    (r"已是最高档", "在强制档位下误导（已在 else 分支，可接受）"),
]
for pat, why in PATTERNS:
    hits = [(i + 1, l.strip()) for i, l in enumerate(src.split("\n"))
            if re.search(pat, l)]
    if hits:
        print(f"\n  ⚠️ 「{pat}」× {len(hits)}  （{why}）")
        for ln, txt in hits[:4]:
            print(f"        L{ln}: {txt[:76]}")

print()
print("=" * 92)
print("  F. 文件结构 / 残留")
print("=" * 92)
for lab, ok in [
    ("paper/ma50_live.py 存在", (HERE / "ma50_live.py").exists()),
    ("无 ma50_live_v2.py", not (HERE / "ma50_live_v2.py").exists()),
    ("无 _v1_backup", not list(HERE.glob("_v1*"))),
    ("无 _*.py 临时文件", not [p for p in HERE.glob("_*.py")]),
    ("无 reports_v2", not (ROOT / "data" / "reports_v2").exists()),
    ("无 ma50_log_v2.csv", not (ROOT / "data" / "live" / "ma50_log_v2.csv").exists()),
]:
    print(f"  {'✅' if ok else '❌'} {lab}")
    if not ok:
        ISSUES.append(("低", lab, "残留文件", "清理"))

print()
print("=" * 92)
print(f"  汇总：{len(ISSUES)} 个问题")
print("=" * 92)
for sev, title, why, fix in ISSUES:
    print(f"  [{sev}] {title}")
