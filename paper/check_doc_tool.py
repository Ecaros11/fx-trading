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
chk("1.031" in md, "夏普 1.031 在文档里")
chk(m.METHODS[0][3] == 1.031, f"工具 METHODS[0] 夏普 = {m.METHODS[0][3]}", "应为 1.031")
chk(m.MIN_NOTIONAL == 20.0, f"MIN_NOTIONAL = {m.MIN_NOTIONAL}")
chk("14.20" in md or "14.2" in md, "门槛 14.20 在文档里")

print()
print("  ── 门槛是否真的等于 20 ÷ fixed_position(门槛) ──")
for name, tv, need, sr, dd in m.METHODS:
    p = m.target_position(need, 0.445, (name, tv, need, sr, dd))
    notion = need * p
    chk(abs(notion - m.MIN_NOTIONAL) < 0.05 or p <= 1.0,
        f"{name} 门槛 {need}U → 名义 {notion:.2f}U")

print()
print("  ── 【防漂移】同一件事必须在两个文件里说同样的话 ──")
# 这一类错误（工具说 A、文档说 B）之前发生过多次，所以逐项对。
pairs = [
    ("标准误", f"{m.SE_SHARPE:.3f}", ["0.383", "0.389"],
     "工具 SE_SHARPE 与文档里的标准误"),
    ("样本年数", f"{m.SAMPLE_YEARS:.2f}", ["6.86", "6.70"],
     "工具 SAMPLE_YEARS 与文档里的年数"),
    ("夏普", f"{m.METHODS[0][3]:.3f}", None, "METHODS 夏普出现在文档里"),
    ("1.405 作常量", None, None, "工具头部不再宣称「固定 1.405x」"),
]
chk(f"{m.SE_SHARPE:.3f}" in md,
    f"标准误 {m.SE_SHARPE:.3f} 在文档里", "文档与工具必须同值")
for stale in ("0.383", "0.389"):
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
chk("ceil(target_position)" in md.replace(" ", ""),
    "文档 §9 参数表写了 LEV_SETTING 公式")


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
chk(len(j) - 60 == m.SAMPLE_DAYS,
    f"回测样本 {m.SAMPLE_DAYS} 天 = 日线 {len(j)} 根 − 60 根预热")

# 文档 §4 的"在场时间 55.0%"
import numpy as np
C = np.array([b["c"] for b in j], float)
ma = np.full(len(C), np.nan)
cs = np.cumsum(np.insert(C, 0, 0.0))
ma[49:] = (cs[50:] - cs[:-50]) / 50
onn = (C > ma)[60:].mean()
chk(abs(onn - 0.55) < 0.02, f"在场比例 {onn*100:.1f}%（文档说 55.0%）")

# 文档 §2.3 的例子
a = m.advice(14.83, 2707.0, 0.445)
chk(abs(a["position"] - 1.3486) < 0.001, f"14.83U → 仓位 {a['position']:.4f}x")
chk(abs(a["notional"] - 20.0) < 0.01, f"→ 名义 {a['notional']:.2f}U")
chk(abs(a["drawdown"] + 0.689) < 0.005, f"→ 回撤 {a['drawdown']*100:.1f}%")
chk(abs(a["worst"] - 4.61) < 0.02, f"→ 最坏 {a['worst']:.2f}U")

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
