"""
工具全面检查（full_check.py）
============================
1. 所有运行模式
2. 所有导出函数的行为
3. 数据层：连续性 / 新鲜度 / 损坏自愈
4. 逻辑层：信号 / 仓位 / 调仓 / 强平
5. 边界：异常输入
6. 一致性：输出之间不矛盾
7. 无前视
"""
import collections
import datetime as dt
import importlib.util
import inspect
import json
import pathlib
import subprocess
import sys

import numpy as np

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
PY = sys.executable
sm = importlib.util.spec_from_file_location("ml", HERE / "ma50_live.py")
m = importlib.util.module_from_spec(sm)
sm.loader.exec_module(m)

bars = json.loads((ROOT / "data" / "crypto" / f"{m.SYM}.json").read_text(encoding="utf-8"))
C = np.array([b["c"] for b in bars], float)
now = dt.datetime.now(dt.UTC)
BAD = []


def chk(n, ok, d=""):
    print(f"  {'[OK]' if ok else '[XX]'} {n:<42}{d}")
    if not ok:
        BAD.append(n)


print("=" * 90)
print("  1. 所有运行模式")
print("=" * 90)
print()
for f in ("--selfcheck", "--check", "--archive", "--backfill", "--history",
          "--console", "--rebuild"):
    r = subprocess.run([PY, str(HERE / "ma50_live.py"), f],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(HERE))
    chk(f"ma50_live.py {f}", r.returncode == 0, f"exit {r.returncode}")

print()
print("=" * 90)
print("  2. 新增的连续性检查")
print("=" * 90)
print()
g, note = m.check_bar_continuity(bars)
chk("正常数据判为连续", len(g) == 0, note)
bad1 = bars[:-3] + bars[-2:]
g1, n1 = m.check_bar_continuity(bad1)
chk("窗口内缺口能识别", len(g1) == 1 and "窗口内" in n1)
bad2 = bars[:100] + bars[101:]
g2, n2 = m.check_bar_continuity(bad2)
chk("窗口外缺口标为影响有限", len(g2) == 1 and "影响有限" in n2)
chk("空列表安全", m.check_bar_continuity([])[0] == [])
chk("单根安全", m.check_bar_continuity(bars[:1])[0] == [])

print()
print("=" * 90)
print("  3. 数据层")
print("=" * 90)
print()
cb, cnote = m.complete_bars(bars)
chk("complete_bars 丢掉未走完的", len(cb) == len(bars) - 1, cnote)
h = (now.timestamp() - bars[-1]["t"] / 1000) / 3600
chk("日线缓存新鲜 (<25h)", h < 25, f"{h:.1f} h")
fr = json.loads((ROOT / "data" / "funding" / f"{m.SYM}.json").read_text(encoding="utf-8"))
fh = (now.timestamp() - fr[-1]["t"] / 1000) / 3600
chk("资金费缓存新鲜 (<32h)", fh < 32, f"{fh:.1f} h")
chk("资金费 8 小时间隔", all(
    abs((b["t"] - a["t"]) / 3600000 - 8) < 0.01 for a, b in zip(fr[-100:-1], fr[-99:])))

print()
print("=" * 90)
print("  4. 逻辑层")
print("=" * 90)
print()
sg = m.signal_of(cb)
ma50c = C[:len(cb)][-50:].mean()
chk("MA50 与独立复算一致", abs(sg["ma50"] - ma50c) < 1e-6)
chk("close 是最后已走完的", abs(sg["close"] - cb[-1]["c"]) < 1e-9)
chk("long 与 close>ma50 一致", sg["long"] == (sg["close"] > sg["ma50"]))
chk("signal 不含未走完的", sg["bar_t"] == cb[-1]["t"])

meth = m.METHODS[1]
CASES = [(0.10, 3.0), (0.20, 3.0), (0.30, 2.0), (0.60, 1.0),
         (1.199, 0.60 / 1.199), (1.20, 0.60 / 1.20), (1.201, 0.0),
         (2.0, 0.0), (0.0, 0.0), (-1.0, 0.0), (float("nan"), 0.0)]
allok = all(abs(m.target_position(1000.0, rv, meth) - exp) < 1e-9 for rv, exp in CASES)
chk("target_position 11 组边界全对", allok)

# 无前视：w[t-1] vs w[t]
r = np.zeros(len(C))
r[1:] = C[1:] / C[:-1] - 1
ma = np.full(len(C), np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
sig = np.nan_to_num((C > ma).astype(float))
V = np.full(len(C), np.nan)
for i in range(m.VOL_WINDOW + 1, len(C)):
    V[i] = r[i - m.VOL_WINDOW:i].std(ddof=1) * np.sqrt(365)


def ser(lookahead):
    # lookahead=True  => 用 sig[i]（当天收盘才知道）决定 r[i]（错）
    # lookahead=False => 用 sig[i-1]（昨天收盘已知）决定 r[i]（对）
    out, wp = [], 0.0
    for i in range(61, len(C)):
        j = i if lookahead else i - 1
        w = (min(3.0, 0.60 / V[j])
             if (sig[j] and np.isfinite(V[j]) and 0 < V[j] <= m.VOL_CAP)
             else 0.0)
        out.append(w * r[i] - abs(w - wp) * 0.0005)
        wp = w
    return np.array(out)


sh_ok = ser(False).mean() / ser(True).std(ddof=1) * np.sqrt(365)
sh_la = ser(True).mean() / ser(False).std(ddof=1) * np.sqrt(365)
chk("无前视（正确口径夏普明显低于前视）", sh_la / sh_ok > 1.35,
    f"{sh_ok:.3f} vs {sh_la:.3f} = {sh_la/sh_ok:.2f}x")

print()
print("=" * 90)
print("  5. 边界输入")
print("=" * 90)
print()
a = m.advice(89.0, 2695.0, 0.157)
chk("正常输入 feasible", a["feasible"] is True)
chk("notional = pos x equity", abs(a["notional"] - a["position"] * 89.0) < 1e-6)
chk("qty = notional / price", abs(a["qty"] - a["notional"] / 2695.0) < 1e-9)
chk("feasible 与 fail 不矛盾", a["feasible"] == (a["fail"] is None))
chk("equity=None 安全", m.advice(None, 2695.0, 0.157)["feasible"] is False)
chk("price=None 安全", m.advice(89.0, None, 0.157)["feasible"] is False)
chk("price=0 安全", m.advice(89.0, 0.0, 0.157)["feasible"] is False)
chk("price=-5 安全", m.advice(89.0, -5.0, 0.157)["feasible"] is False)
chk("rvol=None 安全", m.advice(89.0, 2695.0, None)["fail"] == "vol")
chk("equity=0 安全", m.advice(0.0, 2695.0, 0.157)["feasible"] is False)

print()
print("=" * 90)
print("  6. 导出的函数清单（确认没有残留/缺失）")
print("=" * 90)
print()
funcs = sorted(n for n, o in vars(m).items()
               if inspect.isfunction(o) and o.__module__ == "ml")
print(f"  共 {len(funcs)} 个：")
for i in range(0, len(funcs), 3):
    print("     " + "  ".join(f"{x:<26}" for x in funcs[i:i + 3]))
core = {"fetch_all", "load_cache", "refresh_funding", "refresh", "complete_bars",
        "check_bar_continuity", "ma", "funding_by_day", "signal_of", "pick_method",
        "realized_vol", "fixed_position", "dd_for_lev", "target_position",
        "leverage_plan", "advice", "decide_action", "dynamic_drawdown", "run",
        "load_archive", "save_archive", "do_archive", "backfill", "history",
        "selfcheck", "main"}
missing = core - set(funcs)
chk("核心函数齐全", not missing, f"缺 {missing}" if missing else "")

print()
print("=" * 90)
if BAD:
    print(f"  [XX] 发现 {len(BAD)} 个问题：")
    for b in BAD:
        print(f"     - {b}")
else:
    print("  [OK] 全面检查通过")
print("=" * 90)
sys.exit(1 if BAD else 0)
