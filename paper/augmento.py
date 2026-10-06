"""拉 Augmento Fusion V1 情绪指标（augmento.py）"""
import json
import pathlib
import sys
import urllib.request

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

URL = "https://augmento.ai/api/v1/market-indicator/1"
PROXY = {"http": "http://127.0.0.1:1080", "https": "http://127.0.0.1:1080"}

print("  GET", URL)
print()
txt = None
for lab, px in (("不走代理", {}), ("走 1080 代理", PROXY)):
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler(px))
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with op.open(req, timeout=30) as r:
            txt = r.read().decode("utf-8", "replace")
            code = r.status
        print(f"  ✅ {lab}   HTTP {code}   长度 {len(txt)}")
        break
    except Exception as e:
        print(f"  ❌ {lab}   {type(e).__name__}: {str(e)[:100]}")

if txt:
    (HERE / "_aug.json").write_text(txt, encoding="utf-8")
    print()
    print("  ── 原始内容（前 1200 字符）──")
    print("  " + txt[:1200].replace("\n", "\n  "))
    try:
        j = json.loads(txt)
        print()
        print("  ── 结构 ──")
        def walk(o, p="", d=0):
            if d > 2:
                return
            if isinstance(o, dict):
                for k, v in list(o.items())[:25]:
                    if isinstance(v, (dict, list)):
                        print(f"  {'  '*d}{k}: {type(v).__name__}"
                              f"[{len(v)}]")
                        walk(v, p + k + ".", d + 1)
                    else:
                        print(f"  {'  '*d}{k}: {str(v)[:70]}")
            elif isinstance(o, list) and o:
                print(f"  {'  '*d}[0] →")
                walk(o[0], p, d + 1)
        walk(j)
    except Exception as e:
        print("  不是 JSON:", e)
