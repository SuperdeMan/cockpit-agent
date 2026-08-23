"""MiniMax 云端真栈长会话 QA：5 类 persona，每类 50–100 连续业务轮。

这不是把 61 条迷你集各自放进干净 session。它把同一批已知红绿对照串进长上下文，
并补一条 50 轮信息/出行业务线；每个 persona 始终复用同一个 session_id。逐轮同时保存：
speech / card / action / vehicle_state / trace，以及 collector 的 path / intent / agent /
LLM provider。所有 LLM 调用必须是 minimax:MiniMax-M3；商户与危险动作最多走到预览/
二次确认并立即取消，不付款、不创建真实订单、不执行危险确认。

用法（真栈动作前仍须先 `python scripts/dev_stack.py target show`）：
    python scripts/probe_qa_long_sessions.py
    python scripts/probe_qa_long_sessions.py --persona vehicle,merchant
    python scripts/probe_qa_long_sessions.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import probe_qa_regression as probe                         # noqa: E402
from scripts.dev_stack_lib import read_root_env                          # noqa: E402
from scripts.e2e_target import endpoint_environment, resolve_e2e_target  # noqa: E402


_CASE_BY_ID = {case["id"]: case for case in probe.CASES}
_MIN_TURNS = 50
_MAX_TURNS = 100


def _custom_case(cid: str, *turns: dict) -> dict:
    return {
        "id": cid, "group": "long", "card": "LONG", "issue": "fresh",
        "known": "audit", "why": "长会话业务覆盖", "turns": list(turns),
    }


_INFORMATION_CASES = [
    _custom_case("INF-WEATHER",
        {"say": "深圳现在天气怎么样", "expect": {"no_actions": True},
         "audit": {"intent_any": ["info.weather"], "provider_required": True}},
        {"say": "明天呢", "expect": {"no_actions": True},
         "audit": {"intent_any": ["info.weather", "info.forecast"]}},
        {"say": "空气质量也看一下", "expect": {"no_actions": True},
         "audit": {"intent_any": ["info.air_quality"]}},
        {"say": "现在有影响开车的天气预警吗", "expect": {"no_actions": True},
         "audit": {"intent_any": ["safety.weather_alert", "info.alerts"]}},
        {"say": "明天适合洗车吗", "expect": {"no_actions": True},
         "audit": {"intent_any": ["info.indices"]}},
    ),
    _custom_case("INF-NEWS",
        {"say": "查一下今天人工智能行业的重要新闻", "expect": {"no_actions": True},
         "audit": {"intent_any": ["info.news", "info.search"], "provider_required": True}},
        {"say": "把来源名称说清楚", "expect": {"no_actions": True}},
        {"say": "这些消息分别是什么时间发布的", "expect": {"no_actions": True}},
        {"say": "再搜索一下钠离子电池最近的产业化进展", "expect": {"no_actions": True},
         "audit": {"intent_any": ["info.search"]}},
        {"say": "只总结有来源支持的结论", "expect": {"no_actions": True}},
    ),
    _custom_case("INF-NAV",
        {"say": "从深圳市民中心到深圳北站开车多远多久，只算不要导航",
         "expect": {"no_actions": True},
         "audit": {"intent_any": ["navigation.estimate"]}},
        {"say": "那现在导航去深圳北站", "expect": {"actions_include": ["navigate"]},
         "audit": {"intent_any": ["navigation.navigate_to"]}},
        {"say": "换成避堵路线", "expect": {},
         "audit": {"intent_any": ["navigation.reroute"]}},
        {"say": "取消导航", "expect": {},
         "audit": {"intent_any": ["navigation.cancel"]}},
        {"say": "再换一条路", "expect": {"no_actions": True}},
    ),
    _custom_case("INF-TRIP",
        {"say": "规划一个深圳到广州再到珠海的三天行程，按这个顺序",
         "expect": {}, "audit": {"intent_any": ["trip.plan"]}},
        {"say": "先别确认，第二天有哪些安排", "expect": {"no_actions": True}},
        {"say": "不要把珠海排到广州前面", "expect": {"no_actions": True}},
        {"say": "如果时间不够就减少最后一天的景点", "expect": {"no_actions": True}},
        {"say": "这份方案先取消，不执行", "expect": {"no_actions": True}},
    ),
    _custom_case("INF-MANUAL-SAFETY",
        {"say": "不知道具体车型时，标准胎压应该是多少", "expect": {"no_actions": True}},
        {"say": "红色机油灯亮了还能继续开吗", "expect": {"no_actions": True},
         "audit": {"intent_any": ["safety.driver_state", "safety.driving_advice", "manual.query"]}},
        {"say": "慢一点开可以吗", "expect": {"no_actions": True},
         "audit": {"intent_any": ["safety.driving_advice"]}},
        {"say": "你的判断依据来自车主手册还是通用安全建议", "expect": {"no_actions": True}},
        {"say": "好的，我会靠边停车检查", "expect": {"no_actions": True}},
    ),
    _custom_case("INF-PREFERENCE",
        {"say": "我不吃辣，也不想排长队", "expect": {"no_actions": True}},
        {"say": "推荐附近适合晚饭的地方", "expect": {"no_actions": True},
         "audit": {"intent_any": ["nearby.search"]}},
        {"say": "为什么推荐这些", "expect": {"no_actions": True}},
        {"say": "只说我这轮明确给出的偏好", "expect": {"no_actions": True}},
        {"say": "把仍然缺的信息问清楚，不要猜", "expect": {"no_actions": True}},
    ),
    _custom_case("INF-REMINDER",
        {"say": "明天下午四点提醒我交周报", "expect": {},
         "audit": {"intent_any": ["reminder.create"]}},
        {"say": "列出我现在进行中的提醒", "expect": {"no_actions": True},
         "audit": {"intent_any": ["reminder.list"]}},
        {"say": "把交周报那条改到明天下午四点半", "expect": {"no_actions": True},
         "audit": {"intent_any": ["reminder.update"]}},
        {"say": "再查一次进行中的提醒", "expect": {"no_actions": True},
         "audit": {"intent_any": ["reminder.list"]}},
        {"say": "刚才实际改了哪一条，时间是什么", "expect": {"no_actions": True}},
    ),
    _custom_case("INF-STOCK",
        {"say": "查一下宁德时代现在的股价", "expect": {"no_actions": True},
         "audit": {"intent_any": ["info.stock"]}},
        {"say": "数据源和更新时间是什么", "expect": {"no_actions": True}},
        {"say": "再看一下沪深300", "expect": {"no_actions": True},
         "audit": {"intent_any": ["info.stock"]}},
        {"say": "不要把生活指数当成股票指数", "expect": {"no_actions": True}},
        {"say": "只总结刚才查到的行情，不做投资建议", "expect": {"no_actions": True}},
    ),
    _custom_case("INF-CHARGING",
        {"say": "找一下附近的充电站", "expect": {"no_actions": True},
         "audit": {"intent_any": ["charging.find", "nearby.search"]}},
        {"say": "优先空闲而且绕路少的", "expect": {"no_actions": True}},
        {"say": "规划去广州路上的补能，但先不要启动导航", "expect": {"no_actions": True},
         "audit": {"intent_any": ["charging.plan"]}},
        {"say": "第一站为什么选它", "expect": {"no_actions": True}},
        {"say": "不要执行，只保留建议", "expect": {"no_actions": True}},
    ),
    _custom_case("INF-COMPOUND",
        {"say": "查明天深圳天气，如果下雨再问我要不要提醒带伞", "expect": {"no_actions": True}},
        {"say": "先不要自动建提醒", "expect": {"no_actions": True}},
        {"say": "接女儿放学，路上买杯咖啡", "expect": {},
         "audit": {"intent_any": ["navigation.navigate_to"]}},
        {"say": "如果找不到她的地点就直接问我，不要猜另一个城市", "expect": {"no_actions": True}},
        {"say": "总结这五轮里哪些执行了、哪些只是建议", "expect": {"no_actions": True}},
    ),
]


_PERSONA_CASE_IDS = {
    "vehicle": (
        "CF1", "CF2", "CF4", "NG1", "NG2", "NG3", "NG4", "NG5", "NG6",
        "OR1", "OR2", "OR3", "EL1", "EL2", "EL3", "AU1", "CA2", "CA3",
        "CA5", "CA4", "SF3", "SF4", "NG1", "NG2", "NG3", "NG4", "NG5",
        "NG6", "OR1", "OR2", "OR3", "EL2", "NG5",
    ),
    "family": (
        "PU1", "PU2", "PU3", "PU4", "PU5", "PU6", "PU7", "PU8", "PU9",
        "SL1", "SL2", "SL3", "SL4", "XS2", "XS3", "SF1", "SF2", "SF3",
        "SF4", "SF5", "AU2", "CD1", "CD2", "CD3", "CA1", "CA5",
        "PU1", "PU2", "PU3", "PU4", "PU5", "PU6", "PU7", "PU8", "PU9",
        "SL1", "SF3", "XS2",
    ),
    "merchant": (
        "CD4", "CD5", "CD6", "CD7", "MC1", "MC2", "SP1", "SP2", "SP3",
        "XS4", "XS7", "XS8", "CD1", "CD2", "CD3", "CD4", "CD5", "CD6",
        "CD7", "MC1", "MC2", "XS7", "XS8",
    ),
    "adversarial": (
        "CF1", "CF2", "CF4", "NG1", "NG2", "NG3", "NG4", "NG5", "NG6",
        "OR1", "OR2", "OR3", "EL1", "EL2", "EL3", "XS2", "XS3", "XS4",
        "XS7", "XS8", "SF1", "SF2", "SF3", "SF4", "SF5", "CD3", "AU2",
        "CA1", "CA2", "CA3", "CA4", "CA5", "PU7", "PU8", "CF4",
    ),
}


def build_persona_plans() -> dict[str, list[dict]]:
    plans: dict[str, list[dict]] = {}
    for persona, ids in _PERSONA_CASE_IDS.items():
        rows = []
        for index, cid in enumerate(ids, 1):
            case = copy.deepcopy(_CASE_BY_ID[cid])
            case["id"] = f"{cid}-{persona[:3]}-{index:02d}"
            case["source_case_id"] = cid
            # The source regression corpus uses ``sid`` to open an isolated
            # session for cross-session leakage checks.  A persona run has the
            # opposite contract: all turns must share one continuous session.
            # Keep the user utterance/assertions, but deliberately discard the
            # source harness' session selector here.
            for turn in case["turns"]:
                turn.pop("sid", None)
            rows.append(case)
        plans[persona] = rows
    plans["information"] = copy.deepcopy(_INFORMATION_CASES)
    return plans


def audit_trace_detail(detail: dict, trace_id: str) -> tuple[dict, list[str]]:
    if not isinstance(detail, dict) or detail.get("error") == "not found":
        return {}, [f"collector 缺少 trace {trace_id}"]
    failures: list[str] = []
    turn = detail.get("turn") or {}
    if turn.get("trace_id") not in (None, "", trace_id):
        failures.append(f"collector trace 对不上：{turn.get('trace_id')} != {trace_id}")
    if str(turn.get("status") or "").lower() in {"error", "failed", "timeout"}:
        failures.append(f"collector 轮次状态异常：{turn.get('status')}")

    providers: list[str] = []
    fallbacks = 0
    for call in detail.get("llm_calls") or []:
        provider = str(call.get("provider") or "").lower()
        model = str(call.get("model") or "")
        providers.append(f"{provider}:{model}")
        if provider != "minimax" or model != "MiniMax-M3":
            failures.append(f"出现非 MiniMax-M3 LLM 调用：{provider}:{model}")
        if call.get("fallback"):
            fallbacks += 1
        if call.get("error"):
            failures.append(f"LLM 调用报错：{call.get('error')}")

    agents: set[str] = set()
    span_nodes: list[str] = []
    for span in detail.get("spans") or []:
        node = str(span.get("node") or "")
        if node:
            span_nodes.append(node)
        attrs = span.get("attrs") or {}
        if isinstance(attrs, dict):
            for key in ("agent_id", "agent"):
                value = str(attrs.get(key) or "").strip()
                if value:
                    agents.add(value)

    return {
        "path": str(turn.get("path") or ""),
        "intents": [x for x in str(turn.get("intents") or "").split(",") if x],
        "agents": sorted(agents),
        "providers": providers,
        "fallbacks": fallbacks,
        "span_nodes": span_nodes,
    }, failures


def audit_card_provenance(card_text: str) -> tuple[list[dict], list[str]]:
    """提取卡片树里的真实性章；真栈出现显式 mock 一律失败。

    生产契约用的是 ``vendor``，少数外部卡历史上用 ``provider``。报告统一成
    ``provider``，但不把缺章的普通控制卡误判成 provider 卡。
    """
    raw = str(card_text or "").strip()
    if not raw or raw == "{}":
        return [], []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return [], [f"卡片 JSON 无法审计 provenance: {type(exc).__name__}"]

    entries: list[dict] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            prov = value.get("_prov")
            if isinstance(prov, dict):
                provider = str(
                    prov.get("provider") or prov.get("vendor")
                    or prov.get("source") or "unknown").strip()
                mode = str(prov.get("mode") or "unknown").strip().lower()
                entries.append({"provider": provider, "mode": mode})
            for key, child in value.items():
                if key != "_prov":
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    failures = [
        f"真栈卡片出现 mock provenance: {entry['provider']}"
        for entry in entries if entry["mode"] == "mock"
    ]
    return entries, failures


def _resolve_endpoints() -> tuple[str, str, str]:
    ws_url, target_name = probe._resolve_ws_url()
    env = dict(os.environ)
    env.update(read_root_env(_ROOT, {"TAILNET_FQDN"}))
    target = resolve_e2e_target(_ROOT, explicit=None, environ=env)
    collector = endpoint_environment(target)["COLLECTOR_URL"]
    return ws_url, collector, target_name


def _http_json(url: str, timeout: float = 20.0) -> dict | list:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


async def _connect(ws_url: str):
    ws = await probe.websockets.connect(ws_url)
    try:
        await asyncio.wait_for(ws.recv(), timeout=probe._HELLO_WAIT_S)
    except asyncio.TimeoutError:
        pass
    return ws


def _state_failures(actions: list[str], state: dict) -> list[str]:
    failures: list[str] = []
    checks = {
        "hvac.on": ("hvac_on", True), "hvac.off": ("hvac_on", False),
        "window.open": ("window", "open"), "window.close": ("window", "closed"),
        "rear_view_mirror.fold": ("rear_view_mirror", "folded"),
        "rear_view_mirror.unfold": ("rear_view_mirror", "unfolded"),
        "warning_light.open": ("warning_light", True),
        "warning_light.close": ("warning_light", False),
        "volume.mute": ("volume_muted", True),
        "volume.unmute": ("volume_muted", False),
    }
    for action in actions:
        if action not in checks:
            continue
        key, expected = checks[action]
        actual = state.get(key)
        if expected == "open":
            ok = actual == "open" or str(actual).endswith("%")
        else:
            ok = actual == expected
        if not ok:
            failures.append(f"状态未兑现 {action}: {key}={actual!r}, 期望 {expected!r}")
    return failures


async def _turn(ws, session: str, say: str, *, operation_id: str = "",
                trace_id: str, is_confirmation: bool = False) -> dict:
    return await probe._one_turn(
        ws, session, say, operation_id=operation_id,
        is_confirmation=is_confirmation, trace_id=trace_id)


async def _fetch_detail(collector: str, trace_id: str) -> dict:
    url = f"{collector}/api/turns/{urllib.parse.quote(trace_id, safe='')}"
    for attempt in range(6):
        try:
            detail = await asyncio.to_thread(_http_json, url)
        except Exception as exc:
            detail = {"error": f"{type(exc).__name__}: {exc}"}
        if isinstance(detail, dict) and detail.get("error") != "not found":
            return detail
        if attempt < 5:
            await asyncio.sleep(0.5)
    return detail if isinstance(detail, dict) else {"error": "not found"}


async def _vehicle_state(collector: str) -> dict:
    try:
        value = await asyncio.to_thread(_http_json, f"{collector}/api/vehicle/state")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


async def _run_persona(name: str, cases: list[dict], ws_url: str,
                       collector: str, stamp: int) -> dict:
    session = f"probe-qa-long-{name}-{stamp}"
    rows: list[dict] = []
    active_ops: dict[str, int] = {}
    ws = await _connect(ws_url)
    try:
        for case_no, case in enumerate(cases, 1):
            local_rows: list[dict] = []
            source_id = case.get("source_case_id") or case["id"]
            for local_turn, turn in enumerate(case["turns"], 1):
                pre_failures: list[str] = []
                operation_id = str(turn.get("op_literal") or "")
                if turn.get("op_from") is not None:
                    previous = next((row for row in local_rows
                                     if row["local_turn"] == int(turn["op_from"])), None)
                    operation_id = str((previous or {}).get("operation_id") or "")
                    if not operation_id:
                        pre_failures.append(
                            f"{source_id} 引用的 operation_id 不存在，未伪造")

                if turn.get("say_button") is not None:
                    ref = turn["say_button"]
                    previous = next((row for row in local_rows
                                     if row["local_turn"] == int(ref["turn"])), None)
                    buttons = (previous or {}).get("card_buttons") or []
                    index = int(ref.get("index", 1))
                    if len(buttons) >= index:
                        say = buttons[index - 1]
                    else:
                        say = "请重新列出刚才可以选择的项目"
                        pre_failures.append(
                            f"{source_id} 缺第 {index} 个真实按钮，未编造 send_text")
                else:
                    say = probe._subst(str(turn.get("say") or ""), stamp + len(rows) + 1)

                trace_id = uuid.uuid4().hex
                try:
                    obs = await _turn(
                        ws, session, say, operation_id=operation_id,
                        trace_id=trace_id,
                        is_confirmation=bool(turn.get("confirm")))
                except Exception as exc:
                    obs = {
                        "speech": f"[transport error] {type(exc).__name__}",
                        "actions": [], "need_confirm": False, "card_type": "",
                        "is_question": False, "error": True, "trace_id": trace_id,
                    }
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    ws = await _connect(ws_url)

                notes: list[str] = []
                failures = pre_failures + probe._judge(
                    probe._subst(turn.get("expect") or {}, stamp + len(rows) + 1),
                    obs, local_rows, notes)
                if obs.get("error"):
                    failures.append("本轮 transport/backend error")
                if not (obs.get("speech") or obs.get("actions") or obs.get("card_type")):
                    failures.append("本轮既无 speech、action，也无 card")

                if obs.get("actions"):
                    state = await _vehicle_state(collector)
                    obs["vehicle_state"] = state
                    if not state:
                        failures.append("动作已发但 collector 没有 vehicle_state")
                    else:
                        failures.extend(_state_failures(obs["actions"], state))

                row = {
                    "turn": len(rows) + 1, "case": source_id,
                    "case_instance": case["id"], "local_turn": local_turn,
                    "say": say, "audit_expect": dict(turn.get("audit") or {}),
                    **obs, "notes": notes, "fails": failures,
                }
                rows.append(row)
                local_rows.append(row)

                op = str(obs.get("operation_id") or "")
                if obs.get("need_confirm") and op:
                    active_ops[op] = row["turn"]
                for closed in obs.get("closed_operation_ids") or []:
                    active_ops.pop(str(closed), None)
                if say.strip().startswith(("取消", "不用", "先别")) and not obs.get("need_confirm"):
                    active_ops.clear()

                flag = "✓" if not failures else "✗"
                print(f"  {flag} {name:<11} T{row['turn']:02d} {source_id:<5} "
                      f"{say[:26]:<26} → {(obs.get('speech') or '')[:42]}")

            # 商户预览/危险确认绝不执行：用服务端 operation_id 逐条取消，且继续后续会话。
            for op in list(active_ops):
                trace_id = uuid.uuid4().hex
                obs = await _turn(ws, session, "取消", operation_id=op, trace_id=trace_id)
                failures = probe._judge({"no_actions": True}, obs, rows, [])
                if obs.get("need_confirm"):
                    failures.append("取消后仍处于待确认")
                row = {
                    "turn": len(rows) + 1, "case": "AUTO-CANCEL",
                    "case_instance": case["id"], "local_turn": 0,
                    "say": "取消", "audit_expect": {}, **obs,
                    "notes": [f"取消源自第 {active_ops[op]} 轮的待确认"],
                    "fails": failures,
                }
                rows.append(row)
                active_ops.pop(op, None)
                print(f"  {'✓' if not failures else '✗'} {name:<11} "
                      f"T{row['turn']:02d} SAFE 取消待确认")

        # 失败/取消后至少再走三轮，验证会话不会被卡死。
        last_event = max((row["turn"] for row in rows
                          if row["fails"] or row["case"] == "AUTO-CANCEL"), default=0)
        recovery = ("现在还有待确认的操作吗", "今天深圳天气怎么样", "谢谢，继续")
        for say in recovery:
            if len(rows) - last_event >= 3:
                break
            trace_id = uuid.uuid4().hex
            obs = await _turn(ws, session, say, trace_id=trace_id)
            failures = [] if not obs.get("error") else ["恢复轮出错"]
            rows.append({
                "turn": len(rows) + 1, "case": "RECOVERY", "case_instance": "",
                "local_turn": 0, "say": say, "audit_expect": {}, **obs,
                "notes": ["失败/取消后的连续恢复轮"], "fails": failures,
            })
    finally:
        await ws.close()

    # Collector 可能比 final 晚几十毫秒落库；逐 trace 有界重试后再做路由/Agent/provider 对账。
    for row in rows:
        detail = await _fetch_detail(collector, row["trace_id"])
        audit, failures = audit_trace_detail(detail, row["trace_id"])
        row["trace"] = audit
        row["fails"].extend(failures)
        provenance, provenance_failures = audit_card_provenance(
            row.get("card_text") or "")
        row["provenance"] = provenance
        row["fails"].extend(provenance_failures)
        expected = row.pop("audit_expect", {})
        intents = audit.get("intents") or []
        allowed = expected.get("intent_any") or []
        if allowed and not any(want in intents for want in allowed):
            row["fails"].append(
                f"落域不符：期望其一 {allowed}，实际 {intents or '空'}")
        forbidden = expected.get("intent_not") or []
        hits = sorted(set(forbidden) & set(intents))
        if hits:
            row["fails"].append(f"命中禁止落域：{hits}")
        if expected.get("provider_required") and not audit.get("providers"):
            row["fails"].append("该业务轮没有可核对的 LLM provider 记录")

    return {
        "persona": name, "session_id": session, "turn_count": len(rows),
        "passed": sum(not row["fails"] for row in rows),
        "failed": sum(bool(row["fails"]) for row in rows),
        "turns": rows,
    }


def _summary(results: list[dict]) -> dict:
    paths: Counter[str] = Counter()
    intents: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    agents: Counter[str] = Counter()
    cards: Counter[str] = Counter()
    actions: Counter[str] = Counter()
    external_providers: Counter[str] = Counter()
    provenance_modes: Counter[str] = Counter()
    fallbacks = 0
    for result in results:
        for row in result["turns"]:
            trace = row.get("trace") or {}
            paths[trace.get("path") or "unknown"] += 1
            intents.update(trace.get("intents") or [])
            providers.update(trace.get("providers") or [])
            agents.update(trace.get("agents") or [])
            fallbacks += int(trace.get("fallbacks") or 0)
            if row.get("card_type"):
                cards[row["card_type"]] += 1
            actions.update(row.get("actions") or [])
            for provenance in row.get("provenance") or []:
                external_providers[provenance.get("provider") or "unknown"] += 1
                provenance_modes[provenance.get("mode") or "unknown"] += 1
    return {
        "turns": sum(r["turn_count"] for r in results),
        "passed": sum(r["passed"] for r in results),
        "failed": sum(r["failed"] for r in results),
        "paths": dict(paths), "intents": dict(intents),
        "providers": dict(providers), "agents": dict(agents),
        "cards": dict(cards), "actions": dict(actions),
        "external_providers": dict(external_providers),
        "provenance_modes": dict(provenance_modes),
        "fallbacks": fallbacks,
    }


async def _run(selected: list[str], ws_url: str, collector: str) -> list[dict]:
    plans = build_persona_plans()
    stamp = int(time.time())
    results = []
    for offset, name in enumerate(selected):
        print(f"\n=== 长会话 persona: {name} ===")
        results.append(await _run_persona(
            name, plans[name], ws_url, collector, stamp + offset))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--persona", default="",
                        help="只跑指定 persona，逗号分隔")
    parser.add_argument("--out", default=str(
        _ROOT / ".artifacts" / "dev-stack-verifications" /
        "qa-minimax-long-sessions.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    plans = build_persona_plans()
    selected = list(plans)
    if args.persona:
        wanted = [part.strip() for part in args.persona.split(",") if part.strip()]
        unknown = sorted(set(wanted) - set(plans))
        if unknown:
            print(f"未知 persona: {unknown}", file=sys.stderr)
            return 2
        selected = wanted

    if args.dry_run:
        for name in selected:
            count = sum(len(case["turns"]) for case in plans[name])
            print(f"{name}: {count} 个计划轮（运行时取消/恢复轮另计）")
        return 0

    ws_url, collector, target = _resolve_endpoints()
    print(f"真栈目标：{target}；persona={','.join(selected)}；LLM 锁=minimax:MiniMax-M3")
    results = asyncio.run(_run(selected, ws_url, collector))
    summary = _summary(results)
    payload = {
        "ts": int(time.time()), "target": target,
        "llm_lock": {"provider": "minimax", "model": "MiniMax-M3"},
        "summary": summary, "personas": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 长会话汇总 ===")
    for result in results:
        print(f"{result['persona']:<12} {result['passed']}/{result['turn_count']} PASS")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"明细：{out}")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
