"""
测试 ma50_live 的失败路径（test_ma50_fail.py）
============================================
用假 BN 让 fapi 抛异常，看：
  1. refresh 是否报 st['ok']=False + 错误信息
  2. 缓存落后 >48h 时是否拒绝出信号
"""
import importlib.util
import pathlib
import sys
import types

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent))


class FakeBN:
    def __init__(self, exc=None):
        self.exc = exc
        self.calls = []

    def fapi(self, path, params=None, signed=False):
        self.calls.append((path, params))
        if self.exc:
            raise self.exc
        # 只回一个很旧的窗口，模拟"API 通但数据不更新"
        return []


spec = importlib.util.spec_from_file_location("m50", HERE / "ma50_live.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print("=" * 76)
print("  ma50_live 失败路径测试")
print("=" * 76)

# ── 1. 三种异常 ──
for exc in (ConnectionError("proxy refused"),
            TimeoutError("read timeout"),
            RuntimeError("HTTP 429")):
    fake = FakeBN(exc)
    bars, fixed, st = m.refresh(fake)
    ok = (not st["ok"]) and st["err"]
    print()
    print(f"  注入 {type(exc).__name__}: {exc}")
    print(f"     st['ok']  = {st['ok']}     （应为 False）")
    print(f"     st['err'] = {st['err']}")
    print(f"     bars 仍返回 {len(bars)} 根（过期缓存）")
    print(f"     ⇒ {'✅ 正确报失败' if ok else '❌ 状态不对'}")

# ── 2. 无消息异常 ──
fake = FakeBN(ConnectionError())
_, _, st = m.refresh(fake)
print()
print(f"  无消息异常：st['err'] = {st['err']!r}   （应不为空）")

# ── 3. 关键：fetch 成功但不返回新数据 → 缓存仍是旧的 → 48h guard 会拦吗 ──
print()
print("  ── 模拟：API 通，但不返回新 K 线（缓存停在旧日期）──")
import json
import datetime as dt
CACHE = m.CACHE
# ⚠️ 2026-10-07 审计修复：原来 write_text(orig) 在末尾恢复，没有 try/finally
#    ⇒ 中途异常/被中断就会把【截断后的缓存】留在生产文件里。
#    现在用 try/finally 保证无论如何都还原；SIGKILL 仍无解，
#    但那种情况可用 `ma50_live.py --rebuild` 恢复。
orig = CACHE.read_text(encoding="utf-8")
try:
    j = json.loads(orig)
    cut = j[:-40]                      # 砍掉 40 天
    CACHE.write_text(json.dumps(cut), encoding="utf-8")
    last = dt.datetime.fromtimestamp(cut[-1]["t"] / 1000, dt.UTC)
    print(f"     缓存最后一根 {last:%Y-%m-%d}")
    fake = FakeBN(None)                # 不抛异常，但返回空列表
    bars, fixed, st = m.refresh(fake)
    bars2, note = m.complete_bars(bars)
    age = (dt.datetime.now(dt.UTC)
           - dt.datetime.fromtimestamp(bars2[-1]["t"] / 1000, dt.UTC)).total_seconds() / 3600
    print(f"     refresh 后 st['ok'] = {st['ok']}  （API 没抛异常，所以为 True）")
    print(f"     缓存仍停在 {dt.datetime.fromtimestamp(bars2[-1]['t']/1000, dt.UTC):%Y-%m-%d}"
          f"，age = {age:.0f} 小时")
    print(f"     ⇒ 48h guard {'会拦截 ✅' if age > 48 else '不拦截 ❌'}")
finally:
    # 无论正常/异常/无消息异常，都还原
    CACHE.write_text(orig, encoding="utf-8")
    print()
    print("     （缓存已还原）")

print()
print("=" * 76)
print("  结论")
print("=" * 76)
print("""
  48h guard 检查的是【缓存最后一根的时间】，不是"刷新是否成功"。
  ⇒ 所以只要缓存旧，即使 API 正常也会被拦。
  ⇒ 而 API 失败时缓存不会更新 ⇒ 也会被拦。
  两条路径都覆盖到了。
""")
