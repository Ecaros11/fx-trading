"""
全量回归（run_all.py）
====================
跑【8 个模式 + 全部检查脚本】。含联网模式，约 10 分钟。
日常不用跑这个 —— 每天只需要：
    uv run ma50_live.py --console --check --archive
    uv run dd_live.py --snapshot

⚠️ 2026-10-07 审计修复：本脚本默认【只跑只读模式】。
   下面 4 个模式会改生产文件，必须显式加 --with-writes 才会跑：
       --sync-methods   重写 ma50_live.py 的 METHODS 表
       --rebuild        从 API 全量重拉日线缓存
       --archive        写归档 data/live/ma50_log.csv
       --backfill       改归档的前向收益
   ⇒ 所以「跑一遍 run_all.py」在不加参数时是安全的。

用法：
    uv run run_all.py                 # 只读（安全）
    uv run run_all.py --with-writes   # 含 4 个写操作
"""
import argparse
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
PY = sys.executable
FAIL = []

READ_ONLY = ["--selfcheck", "--check", "--console", "--history"]
WRITES = ["--archive", "--backfill", "--rebuild", "--sync-methods"]

SCRIPTS = ["test_leverage.py", "test_fixes.py", "test_ma50_fail.py",
           "check_doc_tool.py", "check_thresholds.py", "verify_rebalance.py",
           "verify_all_lookahead.py", "calc_thresholds.py", "leverage_integer.py",
           "adverse_excursion.py", "test_cache.py", "dynamic_vs_const.py",
           "verify_funding.py", "full_check.py"]

ap = argparse.ArgumentParser()
ap.add_argument("--with-writes", action="store_true",
                help="额外跑 4 个会改生产文件的模式（--archive/--backfill/"
                     "--rebuild/--sync-methods）")
A = ap.parse_args()

MODES = READ_ONLY + (WRITES if A.with_writes else [])
print("=" * 76)
print(f"  {len(MODES)} 个运行模式"
      f"{'（含 4 个写操作）' if A.with_writes else '（只读，安全）'}")
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
    tag = "WRITE" if f in WRITES else "OK   "
    print(f"  {tag} {f:<18} exit={r.returncode}  {el:6.1f}s")
if not A.with_writes:
    print()
    print("  （跳过 4 个写操作模式；要跑它们加 --with-writes）")

print()
print("=" * 76)
print(f"  {len(SCRIPTS)} 个检查脚本")
print("=" * 76)
for t in SCRIPTS:
    q = HERE / t
    if not q.exists():
        print(f"  --  {t} 不存在")
        continue
    t0 = time.time()
    try:
        r = subprocess.run([PY, str(q)], capture_output=True, text=True,
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
