"""
全量回归（run_all.py）
====================
跑【8 个模式 + 全部检查脚本】。含联网模式，约 10 分钟。
日常不用跑这个 —— 每天只需要 `uv run ma50_live.py --console --check --archive`。
"""
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
PY = sys.executable
FAIL = []

MODES = ["--selfcheck", "--check", "--archive", "--backfill", "--history",
         "--console", "--rebuild", "--sync-methods"]
SCRIPTS = ["test_leverage.py", "test_fixes.py", "test_ma50_fail.py",
           "check_doc_tool.py", "check_thresholds.py", "verify_rebalance.py",
           "verify_all_lookahead.py", "calc_thresholds.py", "leverage_integer.py",
           "adverse_excursion.py", "test_cache.py", "dynamic_vs_const.py",
           "verify_funding.py", "full_check.py"]

print("=" * 76)
print("  8 个运行模式")
print("=" * 76)
for f in MODES:
    t0 = time.time()
    r = subprocess.run([PY, str(HERE / "ma50_live.py"), f],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(HERE), timeout=600)
    el = time.time() - t0
    ok = r.returncode == 0
    if not ok:
        FAIL.append(f"ma50_live.py {f}")
    print(f"  {'OK ' if ok else 'XX '} {f:<18} exit={r.returncode}  {el:6.1f}s")

print()
print("=" * 76)
print("  14 个检查脚本")
print("=" * 76)
for t in SCRIPTS:
    p = HERE / t
    if not p.exists():
        print(f"  --  {t} 不存在")
        continue
    t0 = time.time()
    try:
        r = subprocess.run([PY, str(p)], capture_output=True, text=True,
                           encoding="utf-8", cwd=str(HERE), timeout=600)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        rc = -1
    el = time.time() - t0
    ok = rc == 0
    if not ok:
        FAIL.append(t)
    print(f"  {'OK ' if ok else 'XX '} {t:<26} exit={rc}  {el:6.1f}s")

print()
print("=" * 76)
if FAIL:
    print(f"  XX  {len(FAIL)} 项失败：")
    for f in FAIL:
        print(f"      - {f}")
else:
    print("  OK  全部通过")
print("=" * 76)
sys.exit(1 if FAIL else 0)
