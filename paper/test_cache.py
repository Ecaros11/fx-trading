"""实测 7 种缓存损坏情形（现在都应自愈）"""
import importlib.util
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).parent.parent
CACHE = ROOT / "data" / "crypto" / "ETHUSDT.json"
PY = sys.executable

# ⚠️ 先把原始内容读进内存 —— 不依赖 .bak 文件（它可能被上一次运行删掉）
_ORIG = CACHE.read_bytes() if CACHE.exists() else None

CASES = [
    ("文件不存在", None),
    ("空文件", ""),
    ("非法 JSON", "{not json"),
    ("空数组 []", "[]"),
    ("null", "null"),
    ("只有 1 根", '[{"t":1567900800000,"o":1,"h":1,"l":1,"c":1,"v":1}]'),
    ("缺字段", '[{"t":1567900800000}]'),
]

print("=" * 80)
print("  缓存损坏自愈实测（跑 --check，不写归档）")
print("=" * 80)
print()
print(f"  {'情形':<14}{'退出码':>8}{'结果':<34}")
print("  " + "-" * 58)

for name, content in CASES:
    CACHE.unlink(missing_ok=True)
    if content is not None:
        CACHE.write_text(content, encoding="utf-8")
    p = subprocess.run([PY, "ma50_live.py", "--check"], cwd=str(ROOT / "paper"),
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
    err = ""
    if p.returncode != 0:
        tail = (p.stderr or "").strip().split("\n")[-1][:40]
        err = f"[{tail}]"
    rebuilt = "缓存不可用" in (p.stdout or "") or "缓存已重建" in (p.stdout or "")
    status = ("已自愈（自动重建）" if rebuilt else "正常读取") + (" " + err if err else "")
    print(f"  {name:<14}{p.returncode:>8}  {status:<34}")

# 恢复（用内存里保存的原始字节，不依赖备份文件是否还在）
if _ORIG is not None:
    CACHE.write_bytes(_ORIG)
    print("  缓存已从内存快照恢复")
else:
    print("  ⚠️ 没有原始快照 —— 缓存将由 load_cache 自愈重建")
print()
print("  缓存已恢复")
import json
j = json.loads(CACHE.read_text(encoding="utf-8"))
print(f"  {len(j)} 根 / {CACHE.stat().st_size/1024:.1f} KB")
