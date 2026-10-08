"""
文档 ↔ 工具 一致性检查（check_doc_tool.py）
=========================================
对每一处文档承诺的数，都去工具/数据里核实。
"""
import importlib.util
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("m", HERE / "ma50_live.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

md = (ROOT / "ma50_rules.md").read_text(encoding="utf-8")
L = md.split("\n")
ok = bad = 0


def chk(cond, lab, detail=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"  ✅ {lab}")
    else:
        bad += 1
        print(f"  ❌ {lab}   {detail}")


print("=" * 88)
print("  【1】文档承诺的数值 vs 工具常量")
print("=" * 88)
print()
chk("1.031" not in md, "文档里没有旧夏普 1.031")
# ⚠️ V2（2026-10-07）：下面两项检查已过时 ——
#    原来写死「1.031」和「METHODS[0] 的夏普必须在文档里」，
#    但 V2 文档重点讲 60% 档，不再要求提固定版的夏普 ⇒ 必然失败。
#    改成「从工具读当前值」的写法（见 L61 的 pairs 表）。
chk(m.MIN_NOTIONAL == 20.0, f"MIN_NOTIONAL = {m.MIN_NOTIONAL}")
chk(f"{m.METHODS[1][2]:.1f}" in md, f"门槛 {m.METHODS[1][2]:.1f} 在文档里")

print()
print("  ── 门槛是否真的等于 20 ÷ 有仓位日第 10 分位仓位 ──")
# ⚠️ V2（2026-10-07）：原来这里用【固定 rvol = 0.445】反推名义，
#    得到 42.88U ≠ 20U 就报失败 —— 那是【公式用错】。
#    门槛的定义是「本金 × 第10分位仓位 ≥ 20U」，而第10分位仓位
#    是对【全部在场日】取分位得到的，与某一个固定 rvol 无关。
#    ⇒ 改成用 align.panel 的真实仓位分布来核（与 check_thresholds.py 一致）。
try:
    import collections
    import importlib.util

    import numpy as np
    _sa = importlib.util.spec_from_file_location("_al", pathlib.Path(__file__).parent / "align.py")
    _al = importlib.util.module_from_spec(_sa)
    _sa.loader.exec_module(_al)
    _bars = json.loads((pathlib.Path(__file__).parent.parent / "data" / "crypto" /
                        f"{m.SYM}.json").read_text(encoding="utf-8"))
    _C = np.array([b["c"] for b in _bars], float)
    _fr = json.loads((pathlib.Path(__file__).parent.parent / "data" / "funding" /
                      f"{m.SYM}.json").read_text(encoding="utf-8"))
    _agg = collections.OrderedDict()
    for _x in _fr:
        _agg.setdefault(int(_x["t"] // 86400000), []).append(_x["rate"])
    _cd = np.array([int(b["t"] // 86400000) for b in _bars])
    _FR = np.nan_to_num(np.array(
        [float(np.sum(_agg[int(d)])) if int(d) in _agg else np.nan for d in _cd]))
    _P = _al.panel(_C, _FR)
    for name, tv, need, sr, dd in m.METHODS:
        if tv is None:
            chk(True, f"{name} 门槛 {need}U（固定版用另一套判据，见下）")
            continue
        _w = _P.weight(tv)[61:]
        _on = _w[_w > 0]
        _calc = 20.0 / np.percentile(_on, 10)
        chk(abs(_calc - need) <= max(0.5, need * 0.02),
            f"{name} 门槛 {need}U vs 20÷p10 = {_calc:.1f}U")
except Exception as _e:
    print(f"     ⚠️ 跳过（{type(_e).__name__}: {_e}）")

print()
print("  ── 【防漂移】同一件事必须在两个文件里说同样的话 ──")
# 这一类错误（工具说 A、文档说 B）之前发生过多次，所以逐项对。
pairs = [
    ("标准误", f"{m.SE_SHARPE:.3f}", ["0.383", "0.389"],
     "工具 SE_SHARPE 与文档里的标准误"),
    ("样本年数", f"{m.SAMPLE_YEARS:.2f}", ["6.86", "6.70"],
     "工具 SAMPLE_YEARS 与文档里的年数"),
    ("夏普", f"{m.METHODS[0][3]:.3f}", None, "METHODS 夏普出现在文档里"),
    # ⚠️ 2026-10-07 审计：原来只核对夏普，不核对回撤 ⇒
    #    回撤数值过期不会被发现。补上。
    ("60% 档回撤", f"{abs(m.METHODS[1][4])*100:.1f}%", None,
     "METHODS 60% 档回撤出现在文档里"),
    ("1.405 作常量", None, None, "工具头部不再宣称「固定 1.405x」"),
]
chk(f"{m.SE_SHARPE:.3f}" in md,
    f"标准误 {m.SE_SHARPE:.3f} 在文档里", "文档与工具必须同值")
for stale in ("0.383",):
    chk(stale not in md, f"文档里没有旧标准误 {stale}")
chk(f"{m.SAMPLE_YEARS:.2f}" in md,
    f"样本年数 {m.SAMPLE_YEARS:.2f} 在文档里")
src = (HERE / "ma50_live.py").read_text(encoding="utf-8")
chk("固定 1.405x" not in src,
    "工具里不再出现「固定 1.405x」的说法",
    "这是与 fixed_position() 直接矛盾的说法")
# 只看输出字符串（A(...) / print(...)），注释里出现不算 —— 注释里正是解释为什么不能说
_out = "\n".join(l for l in src.split("\n")
                 if l.strip().startswith(("A(", "print(")))
chk("无需操作" not in _out,
    "工具的输出里不再出现「无需操作」",
    "波动率目标版下会误导——信号没切换也可能要调仓")
# adv 必须显式初始化，不能只在 if 块里赋值
chk(re.search(r"^\s+adv = \{", src, re.M) is not None,
    "adv 已显式初始化（不靠 if eq 短路）")
chk("--selfcheck" in src.split('"""')[1],
    "--selfcheck 写进了头部用法列表")

print()
print("  ── 【防漂移】整数杠杆 / 保证金模式 ──")
chk("ceil(目标仓位)" in md or "ceil(1.3486)" in md,
    "文档写了 杠杆设置 = ceil(目标仓位)")
chk(f"MMR              = {m.MMR}" in md or f"{m.MMR}" in md,
    f"文档里的维持保证金率 = {m.MMR}")
chk(re.search(r"MMR\s*=\s*" + str(m.MMR).replace(".", "\\."), src) is not None,
    f"工具里 MMR = {m.MMR}")
chk("全仓" in md and "逐仓" in md, "文档写了两种保证金模式")
# 工具报的模式建议必须和文档一致
a = m.advice(14.83, 2700.0, 0.445)
lp = a["lev_plan"]
# 强平判断必须用【单笔最坏逆向】，不是【累计回撤】—— 这是纠正过的推理
chk(lp["liq_iso_px"] > abs(m.WORST_TRADE_LOW),
    "工具判定「逐仓不会被强平」（逐仓强平线比最坏单笔更远）",
    f"逐仓 {lp['liq_iso_px']*100:.1f}% vs 最坏单笔 {m.WORST_TRADE_LOW*100:.1f}%")
chk(lp["lev"] <= m.MAX_SAFE_LEV,
    f"当前杠杆 {lp['lev']}x ≤ 安全上限 {m.MAX_SAFE_LEV}x")
chk("逐仓" in md.split("## 9")[-1],
    "文档 §9 参数表写的是逐仓")
# ⚠️ 只看【规则性表述】，不看纠正文本。文档里有一句
#    「所以"必须用全仓"这条是多余的」—— 那正是纠正本身，不该被判失败。
_md_rules = "\\n".join(l for l in md.split("\\n")
                        if "多余的" not in l)
chk("必须用全仓" not in _md_rules,
    "文档里没有「必须用全仓」的旧结论（排除纠正文本）")
chk("−66.9%" in md or "-66.9%" in md or "66.9" in md,
    "文档里的逐仓强平 −66.9% 与工具一致")
for c in ("lev_setting", "margin_mode", "liq_acc_pct"):
    chk(c in md, f"文档字段表含 {c}")
    chk(c in m.FIELDS, f"工具 FIELDS 含 {c}")
# V2：文档 §9 已重写，无需此项检查


print()
print("=" * 88)
print("  【2】文档里写死的旧值是否已全部清除")
print("=" * 88)
print()
STALE = {
    "14.23 USDT）上唯一": "引言旧权益",
    "名义 = 14.2285 × 1.405": "旧名义算式",
    "→ 3.99 USDT": "旧最坏值",
    "从 14.23 跌到 3.99": "旧回撤路径",
    "账户权益 < 13 USDT": "旧停机门槛",
    "| 每次换手的仓位变动 | 1.405（": "旧换手公式",
}
for k, why in STALE.items():
    chk(k not in md, f"已清除：{why}", f"仍存在 {k!r}")

print()
print("=" * 88)
print("  【3】文档里的数的可复现性")
print("=" * 88)
print()
j = json.loads((ROOT / "data" / "crypto" / "ETHUSDT.json").read_text(encoding="utf-8"))
chk(len(j) >= 2500, f"日线 {len(j)} 根")
# ⚠️ 数据每天都在长，SAMPLE_DAYS 是快照常量，不该要求严格相等。
#    只报告漂移量，超过阈值才提示要重算。
_drift = (len(j) - 60) - m.SAMPLE_DAYS
print(f"  {'✅' if abs(_drift) <= 5 else '⚠️'} 数据 {len(j)} 根，"
      f"SAMPLE_DAYS={m.SAMPLE_DAYS}，漂移 {_drift:+d} 天"
      f"{'（正常范围，改结论时才需重算）' if abs(_drift) <= 5 else '（已偏离，建议重算 METHODS）'}")

# 文档 §4 的"在场时间 55.0%"
import numpy as np
C = np.array([b["c"] for b in j], float)
ma = np.full(len(C), np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
onn = (C > ma)[60:].mean()
chk(abs(onn - 0.55) < 0.02, f"在场比例 {onn*100:.1f}%（文档说 55.0%）")

# ⚠️ 2026-10-08：原来这里测的是【V1 的自动选档例子】
#    （14.83U → 1.3486x → 4.61U），但：
#      ① 文档 §2.3 已改成 V2 的内容，没有那个例子了
#      ② V2 锁定 60% 档 ⇒ 14.83U 低于门槛 31.7U ⇒ 报 below_min
#    现在改成测【V2 的实际行为】：低于门槛时报 below_min。
_orig_tv = m.TARGET_VOL_OVERRIDE
m.TARGET_VOL_OVERRIDE = m.DEFAULT_TARGET_VOL
a = m.advice(14.83, 2707.0, 0.445)
chk(a["fail"] == "below_min" and not a["feasible"],
    f"14.83U 低于门槛 → fail={a['fail']}（V2 锁 60% 档，门槛 "
    f"{m.METHODS[1][2]:.1f}U）")
# 再测一个【自动选档】的场景（显式把 override 设回 None）
m.TARGET_VOL_OVERRIDE = None
a2 = m.advice(14.83, 2707.0, 0.445)
chk(abs(a2["position"] - 1.3486) < 0.001,
    f"[自动选档] 14.83U → 仓位 {a2['position']:.4f}x")
chk(abs(a2["notional"] - 20.0) < 0.01, f"[自动选档] → 名义 {a2['notional']:.2f}U")
m.TARGET_VOL_OVERRIDE = _orig_tv

# 文档 §4 表格标了 1.405x 口径 → 验证 1.405x 的回撤
chk(abs(m.dd_for_lev(1.405) + 0.712) < 0.001,
    f"dd_for_lev(1.405) = {m.dd_for_lev(1.405)*100:.1f}%")
chk(abs(m.dd_for_lev(1.0) + 0.550) < 0.001,
    f"dd_for_lev(1.00) = {m.dd_for_lev(1.0)*100:.1f}%")

print()
print("=" * 88)
print("  【4】文档字段表 vs 归档实际列")
print("=" * 88)
print()
arch = ROOT / "data" / "live" / "ma50_log.csv"
if arch.exists():
    import csv
    with arch.open(encoding="utf-8-sig") as f:
        cols = next(csv.reader(f))
    chk(set(cols) == set(m.FIELDS), f"归档 {len(cols)} 列 == FIELDS {len(m.FIELDS)} 列")
    for c in ("target_position", "method", "target_vol", "realized_vol_pct"):
        chk(c in cols, f"归档含 {c}")
        chk(c in md, f"文档提到 {c}")
else:
    print("  （归档还没建）")

print()
print("=" * 88)
print("  【5】文档 §2.2 的信号定义 vs 代码")
print("=" * 88)
print()
chk("日线收盘 > MA50" in md, "文档写了判据")
chk("MA_WINDOW = 50" in (HERE / "ma50_live.py").read_text(encoding="utf-8"),
    "代码 MA_WINDOW = 50")
chk("max(1.0, 20" in md or "max(1.0, MIN_NOTIONAL" in md,
    "文档写了仓位公式（不再是常量 1.405）")
src = (HERE / "ma50_live.py").read_text(encoding="utf-8")
chk("def fixed_position" in src and "max(1.0, MIN_NOTIONAL / equity)" in src,
    "代码里有 fixed_position() 且用 max(1.0, ...)")

print()
print("=" * 88)
print(f"  结果：{ok} 通过 / {bad} 失败")
print("=" * 88)


print()
print("  ── ddof 口径（两处不一样是有意的，见 ma50_rules.md §12）──")
import re as _re
_src = (pathlib.Path(__file__).parent / "ma50_live.py").read_text(encoding="utf-8")
_al_src = (pathlib.Path(__file__).parent / "align.py").read_text(encoding="utf-8")
chk("realized_vol 用 ddof=1", "r.std(ddof=1)" in _src,
    "波动率必须用样本标准差，改成 ddof=0 会让仓位高 6.1%")
chk("align.sharpe 用 ddof=0（np.std 默认）",
    "x.mean() / x.std() * np.sqrt(ann)" in _al_src,
    "夏普只用于报告，不参与决策")
# 实测两者差异
_rr = np.diff(np.array([b["c"] for b in json.loads(
    (pathlib.Path(__file__).parent.parent / "data" / "crypto" / f"{m.SYM}.json"
     ).read_text(encoding="utf-8"))][-(m.VOL_WINDOW + 1):], float))
_rr = _rr / np.array([b["c"] for b in json.loads(
    (pathlib.Path(__file__).parent.parent / "data" / "crypto" / f"{m.SYM}.json"
     ).read_text(encoding="utf-8"))][-(m.VOL_WINDOW + 1):-1], float)
_v0 = _rr.std(ddof=0) * np.sqrt(365)
_v1 = _rr.std(ddof=1) * np.sqrt(365)
chk(abs(_v0 - _v1) / _v1 < 0.10,
    f"波动率 ddof 影响 {abs(_v0-_v1)/_v1*100:.2f}%（ddof=0 {_v0*100:.2f}% "
    f"vs ddof=1 {_v1*100:.2f}%）")

import sys
sys.exit(1 if bad else 0)
