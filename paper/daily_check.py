"""One-shot daily workflow and conservative execution evidence; never sends orders."""
import contextlib
import datetime as dt
from decimal import Decimal
import io
import json
from types import SimpleNamespace
import uuid

from dd_support import DAY, file_lock
from execution_ledger import load_decisions, ranges

BEIJING = dt.timezone(dt.timedelta(hours=8))
MAX_LEDGER_AGE_MS = 15 * 60 * 1000


def local_time(ms):
    return dt.datetime.fromtimestamp(int(ms)/1000, dt.UTC).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def dec(value):
    return Decimal(str(value))


def execution_check(rec, decisions, book, sync_ok):
    """Report observations, never certify that a manual fill followed a decision."""
    now = int(rec["decision_ms"])
    start = now // DAY * DAY  # Strategy session: Beijing 08:00 to next 08:00.
    scope = rec.get("account_scope")
    own = [d for d in decisions if scope and d.get("account_scope") == scope
           and d.get("symbol") == "ETHUSDT" and d.get("decision_id") != rec["decision_id"]]
    future = any(int(d["decision_ms"]) > now for d in own)
    prior = sorted((d for d in own if start <= int(d["decision_ms"]) <= now),
                   key=lambda d: (int(d["decision_ms"]), d["decision_id"]))
    last = prior[-1] if prior else None
    result = dict(session_start_ms=start, prior_runs=len(prior), last_decision_id=last["decision_id"] if last else None,
                  fills=0, orders=0, buy_qty="0", sell_qty="0", cutoff_ms=None,
                  coverage_complete=False, evidence_ok=False, match="暂无本轮早先建议可核对",
                  changes=[], warnings=[], attribution="仅数量/方向候选，未确认策略归属")
    if future:
        result["warnings"].append("本地建议时刻晚于当前服务器，需核查时钟或记录")
    if last:
        result["last_action"] = last.get("action", "未知")
        result["last_time_ms"] = int(last["decision_ms"])
        for field, title in (("reference_price", "参考价"), ("equity_at_signal", "账户权益"),
                             ("held_qty_at_signal", "实际多仓"), ("target_qty", "计算目标持仓")):
            old, new = last.get(field), rec.get(field)
            if old not in (None, "") and new not in (None, "") and dec(old) != dec(new):
                result["changes"].append(f"{title} {old} → {new}")
        if last.get("rule_version") != rec.get("rule_version"):
            result["changes"].append("规则版本或参数发生变化，不能直接按旧建议核对")
    if not book or not scope or book.get("account_scope") != scope:
        result["warnings"].append("成交账本缺失或账户归属不匹配，同日执行未核实")
        return result
    spans = ranges(book["trade_ranges"])
    if not spans or spans[-1][1] > now:
        result["warnings"].append("成交覆盖区间缺失或晚于当前时刻")
        return result
    cutoff = spans[-1][1]
    result["cutoff_ms"] = cutoff
    result["coverage_complete"] = cutoff >= start and any(a <= start and b >= cutoff for a, b in spans)
    fills = [f for f in book["trades"] if f.get("symbol") == "ETHUSDT" and start <= f["time"] <= cutoff]
    result.update(fills=len(fills), orders=len({f["orderId"] for f in fills}),
                  buy_qty=str(sum((dec(f["qty"]) for f in fills if f["side"] == "BUY"), Decimal(0))),
                  sell_qty=str(sum((dec(f["qty"]) for f in fills if f["side"] == "SELL"), Decimal(0))))
    fresh = 0 <= now - cutoff <= MAX_LEDGER_AGE_MS
    result["evidence_ok"] = bool(sync_ok and result["coverage_complete"] and fresh and not future)
    if not sync_ok:
        result["warnings"].append("本次DD/成交同步或成本对账未全部通过")
    if not result["coverage_complete"]:
        result["warnings"].append("本轮成交覆盖不完整，不能把缺口当作零成交")
    if not fresh:
        result["warnings"].append("成交查询截止时刻已超过15分钟或早于本轮，需重新同步")
    if last:
        after = [f for f in fills if f["time"] >= int(last["decision_ms"])]
        if cutoff < int(last["decision_ms"]):
            result["match"] = "账本尚未覆盖上次建议之后，执行情况未知"
        elif not after:
            result["match"] = "已覆盖区间未见上次建议后的成交" if result["evidence_ok"] else "上次建议后的成交未核实"
        elif last.get("validation_status") != "order" or not last.get("order_qty"):
            result["match"] = "上次没有可执行订单建议，但随后查到成交，请核查人工操作"
        elif any(f["side"] != last.get("side") or f["positionSide"] != last.get("position_side") for f in after):
            result["match"] = "上次建议后存在不同方向/持仓方向成交，不能自动归因"
        else:
            actual = sum((dec(f["qty"]) for f in after), Decimal(0))
            expected = dec(last["order_qty"])
            if actual == expected:
                result["match"] = f"成交数量与上次建议相符（{actual} ETH；候选关联）"
            elif actual < expected:
                result["match"] = f"成交数量小于上次建议：{actual}/{expected} ETH（可能部分执行）"
            else:
                result["match"] = f"成交数量超过上次建议：{actual}/{expected} ETH，先核查"
    return result


def operation_summary(rec, check, dd_result, dd_ok):
    if rec is None:
        return "每日检查：MA50未能生成有效建议；查看下方失败原因并重新运行。", "failed"
    valid = rec.get("validation_status")
    now = int(rec["decision_ms"])
    lines = [f"每日操作摘要  {local_time(now)} 北京时间",
             f"本轮日线执行日：{local_time(check['session_start_ms'])} 起（至次日08:00）"]
    holding = rec.get("held_qty_at_signal")
    qty = rec.get("order_qty", "")
    side = rec.get("side")
    urgent = (valid == "order" and side == "SELL" and
              (dec(rec["target_qty"]) == 0 or
               dec(holding) * dec(rec["reference_price"]) > 3 * dec(rec["equity_at_signal"])))
    review = not check["evidence_ok"] or (valid == "order" and check["fills"] > 0 and not urgent)
    status = "blocked" if valid == "blocked" else ("review" if review else "ready")
    if valid == "blocked":
        lines.append("今日动作：暂停数量建议")
    elif valid == "order":
        verb = "买入加多" if side == "BUY" else ("卖出平多" if dec(rec["target_qty"]) == 0 else "卖出减多")
        if urgent:
            lines.append(f"今日动作：优先核对并{verb} {qty} ETH（退出/超仓减仓，不因同日记录延后）")
        elif review:
            lines.append(f"今日动作：先复核同日执行；当前计算差额为{verb} {qty} ETH，勿直接重复旧单")
        else:
            lines.append(f"今日动作：{verb} {qty} ETH（本次数量增量）")
        direction = rec.get("position_side", "未知")
        constraint = "仅减仓" if str(rec.get("reduce_only")).lower() == "true" else "按指定持仓方向操作"
        lines.append(f"订单方向：{side}；持仓方向：{direction}；{constraint}")
    else:
        lines.append("今日动作：不用动（当前没有可执行调仓差额）")
    reasons = {"trend_off": "完成日线不高于MA50，目标空仓", "vol_cap": "10日波动超过120%，目标空仓",
               "in_market": "趋势和波动条件允许做多"}
    lines.append("原因：" + reasons.get(rec.get("decision_reason"), "参见完整检查") + "；" + rec.get("notes", ""))
    lines.append(f"信号日 {rec['date']} UTC；10日波动 {rec.get('realized_vol_pct','—')}%；目标名义/权益 {rec.get('target_position') or '—'}x")
    if holding not in (None, ""):
        after = dec(holding)
        if valid == "order":
            after += dec(qty) if side == "BUY" else -dec(qty)
        lines.append(f"当前多仓 {holding} ETH → {'若本次全部成交' if valid == 'order' else '本次保持'} {after} ETH")
        lines.append(f"权益 {rec.get('equity_at_signal')} USDT；可用保证金 {rec.get('available_at_signal')} USDT")
    lines.append(f"本轮已运行 {check['prior_runs']} 次（不含本次）；查到 {check['fills']} 笔成交/{check['orders']} 个订单")
    if check["cutoff_ms"] is not None:
        lines.append(f"成交核对截至 {local_time(check['cutoff_ms'])}；买入 {check['buy_qty']}、卖出 {check['sell_qty']} ETH")
        lines.append("查询截止后的成交尚未纳入；运行记录不等于已成交，数量匹配不等于策略归属已确认。")
    previous = f"（{local_time(check['last_time_ms'])}）" if check.get("last_time_ms") else ""
    lines.append("与上次建议" + previous + "核对：" + check["match"])
    if check["changes"]:
        lines.append("与上次采样相比：" + "；".join(check["changes"]))
    lines.extend("注意：" + w for w in check["warnings"])
    lines.append("DD采样及成本同步：" + ("通过" if dd_ok else "未全部通过，查看详细记录；不能按完整检查成功处理"))
    if dd_ok and dd_result.get("account_scope") == rec.get("account_scope"):
        navdd = dd_result.get("nav_dd")
        lines.append("现金流调整净值采样回撤：" + (f"{navdd:+.2%}" if navdd is not None else "无法核实"))
    if rec.get("liquidation_distance_pct"):
        lines.append(f"当前标记价至API强平价距离 {rec['liquidation_distance_pct']}%；不是账户回撤或安全保证")
    lines.append("执行前核对实时价格和持仓；成交后运行DD采样。本入口只读，不下单。")
    return "\n".join(lines), status


def run_daily(args, live):
    import dd_live as dd
    folder = live.ARCHIVE.parent
    with file_lock(folder / "daily_check", timeout=1):
        return _run_daily(args, live, dd)


def _run_daily(args, live, dd):
    dd_result = {}
    dd_text = io.StringIO()
    print("每日检查正在进行：先采样与同步成交，再生成最新MA50建议。", flush=True)
    try:
        with contextlib.redirect_stdout(dd_text):
            dd_code = dd.main(["--snapshot"], result=dd_result)
    except Exception as error:
        dd_code = 1
        dd_text.write(f"DD失败：{type(error).__name__}: {error}\n")
    print("正在核对最新日线和持仓……", flush=True)
    try:
        full, rec = live.run(SimpleNamespace(check=True, archive=True))
    except Exception as error:
        full, rec = f"MA50失败：{type(error).__name__}: {error}", None
    check = {}
    if rec:
        try:
            decisions = load_decisions(live.ARCHIVE.with_name("ma50_decisions.json"))
            check = execution_check(rec, decisions, dd_result.get("ledger"), dd_code == 0)
        except Exception as error:
            check = execution_check(rec, [], None, False)
            check["warnings"].append(f"同日记录无法核对：{type(error).__name__}: {error}")
    summary, state = operation_summary(rec, check, dd_result, dd_code == 0)
    archive_text = io.StringIO()
    if rec:
        rec.update(daily_check_status=state, daily_prior_runs=str(check["prior_runs"]),
                   daily_fill_count=str(check["fills"]), daily_ledger_cutoff_ms=str(check["cutoff_ms"] or ""))
        try:
            with contextlib.redirect_stdout(archive_text):
                live.do_archive(rec)
        except Exception as error:
            state = "failed"
            summary += f"\n记录保存失败：{type(error).__name__}: {error}；不得把本次视为完整成功。"
    code = 0 if state == "ready" and dd_code == 0 else 1
    report = live.ROOT / "data" / "reports" / (
        "daily_" + dt.datetime.now(dt.UTC).astimezone(BEIJING).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8] + ".md")
    print("\n" + summary)
    if getattr(args, "console", False):
        print("\n" + full + "\n" + dd_text.getvalue())
    details = render_report(summary, full, dd_text.getvalue(), archive_text.getvalue())
    try:
        live._atomic_write(report, details)
        live._atomic_write(report.with_suffix(".json"), json.dumps(
            dict(schema_version=1, status=state, exit_code=code, decision=rec, execution_check=check,
                 dd_exit_code=dd_code, summary=summary), ensure_ascii=False, indent=2))
    except Exception as error:
        print(f"\n日检报告未全部保存：{type(error).__name__}: {error}；本次不算完整成功，已完成的采样和归档仍保留。")
        if not getattr(args, "console", False):
            print("\n" + full + "\n" + dd_text.getvalue())
        return 1
    print(f"\n完整日检报告：{report}")
    return code


def render_report(summary, full, dd_text, archive_text):
    fence = chr(96) * 3
    sections = (("每日操作摘要", summary),
                ("MA50计算明细（同日复核提醒以顶部摘要为准）", full),
                ("DD采样与成交明细", dd_text), ("建议归档", archive_text))
    return "\n\n".join(f"## {title}\n\n{fence}text\n{body.rstrip()}\n{fence}"
                        for title, body in sections if body.strip()) + "\n"
