"""
实测缓存损坏的自愈能力
====================
⚠️ 2026-10-07 重写（审计发现）：原版直接覆写生产缓存 data/crypto/ETHUSDT.json，
   且没有 try/finally ⇒ 任何中断（Ctrl-C/超时/被杀）都会留下坏缓存，
   并污染正在运行的工具。

新版：
  ① 用临时目录当缓存路径（monkeypatch M.CACHE）⇒ 生产文件【从不被碰】
  ② 用 FakeBN 供货 K 线 ⇒ 不真的调币安 API（原版 386 秒 → 现在 <1 秒）
  ③ 没有"恢复"这一步 ⇒ 不存在"忘了恢复"的可能
"""
import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
REAL = ROOT / "data" / "crypto" / "ETHUSDT.json"

# ══════════════ 载入被测模块 ══════════════
spec = importlib.util.spec_from_file_location("M", HERE / "ma50_live.py")
M = importlib.util.module_from_spec(spec)
sys.modules["M"] = M
spec.loader.exec_module(M)

# 真缓存的内容 —— 只【读】，不写
GOOD_BYTES = REAL.read_bytes()
GOOD_BARS = json.loads(GOOD_BYTES.decode("utf-8"))

# ══════════════ 临时缓存路径 ══════════════
TMP = pathlib.Path(tempfile.mkdtemp(prefix="ma50_cache_test_"))
M.CACHE = TMP / "ETHUSDT.json"


class FakeBN:
    """
    假 BN：只实现 fapi()，让"重建"走本地数据，不调网络。

    ⚠️ 必须实现【分页语义】（遵守 startTime / limit 并返回 <limit 表示结束），
       否则 fetch_all 的 while 循环永远拿满 1500 根 ⇒ 死循环。
    """
    calls = 0

    def fapi(self, path, params=None, signed=False):
        FakeBN.calls += 1
        if "klines" not in path:
            return []
        p = params or {}
        start = int(p.get("startTime", 0))
        limit = int(p.get("limit", 1500))
        rows = [b for b in GOOD_BARS if b["t"] >= start][:limit]
        return [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"],
                 b["t"] + 86399999, 0, 0, 0, 0, 0] for b in rows]


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
print("  缓存损坏自愈实测（临时路径 + 假 API，不碰生产文件）")
print("=" * 80)
print()
print(f"  生产文件：{REAL}")
print(f"  测试路径：{M.CACHE}")
print()
print(f"  {'情形':<14}{'判定':<8}{'结果':<26}{'重建根数':>10}")
print("  " + "-" * 62)

ok = True
try:
    for name, content in CASES:
        M.CACHE.unlink(missing_ok=True)
        if content is not None:
            M.CACHE.write_text(content, encoding="utf-8")
        FakeBN.calls = 0
        try:
            bars = M.load_cache(FakeBN())
            n = len(bars) if bars else 0
            # 判定：能拿到【非空且字段完整】的数据即可。
            # ⚠️ "只有 1 根" 是【有效 JSON】⇒ load_cache 正常返回它（不重建），
            #    随后 signal_of 会因不足 50 根而返回 None ⇒ 工具安全降级。
            #    所以这里不能用 n>=100 判定（那是误报）。
            good = n >= 1 and all("t" in b and "c" in b for b in bars)
            note = "已自愈（重建）" if FakeBN.calls else "正常读取"
            print(f"  {name:<14}{'OK' if good else 'XX':<8}{note:<26}{n:>10}")
            if not good:
                ok = False
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"[:24]
            print(f"  {name:<14}{'XX':<8}{msg:<26}{'-':>10}")
            ok = False
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
print("=" * 80)
print(f"  {'[OK] 全部通过' if ok else '[XX] 有失败'}")
same = REAL.read_bytes() == GOOD_BYTES
print(f"  生产缓存完整性：{'✅ 未被触碰' if same else '🔴 被改动了！'}")
print("=" * 80)
sys.exit(0 if ok and same else 1)
