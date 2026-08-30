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
    python scripts/probe_qa_long_sessions.py --replay <artifact.json>   # 修正后计分
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from typing import NamedTuple
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from runtime.execution_claim import execution_claim                       # noqa: E402
from scripts import probe_qa_regression as probe                         # noqa: E402
from scripts.dev_stack_lib import read_root_env                          # noqa: E402
from scripts.e2e_target import endpoint_environment, resolve_e2e_target  # noqa: E402


_CASE_BY_ID = {case["id"]: case for case in probe.CASES}
_MIN_TURNS = 50
_MAX_TURNS = 100
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

_MANAGED_VEHICLE_KEYS = (
    "hvac_on",
    "front_defogger",
    "rear_defogger",
    "window",
    "sunroof",
    "rear_view_mirror",
    "steering_wheel_heating",
    "volume_muted",
    "warning_light",
    "media",
)

_VEHICLE_ACTION_TARGETS = {
    "hvac.on": ("hvac_on", True),
    "hvac.off": ("hvac_on", False),
    "front_defogger.open": ("front_defogger", True),
    "front_defogger.close": ("front_defogger", False),
    "rear_defogger.open": ("rear_defogger", True),
    "rear_defogger.close": ("rear_defogger", False),
    "window.open": ("window", "open"),
    "window.close": ("window", "closed"),
    "sunroof.open": ("sunroof", "open"),
    "sunroof.close": ("sunroof", "closed"),
    "rear_view_mirror.fold": ("rear_view_mirror", "folded"),
    "rear_view_mirror.unfold": ("rear_view_mirror", "unfolded"),
    "steering_wheel.heating.open": ("steering_wheel_heating", True),
    "steering_wheel.heating.close": ("steering_wheel_heating", False),
    "volume.mute": ("volume_muted", True),
    "volume.unmute": ("volume_muted", False),
    "warning_light.open": ("warning_light", True),
    "warning_light.close": ("warning_light", False),
    "media.play": ("media", "playing"),
    "media.pause": ("media", "paused"),
    "media.stop": ("media", "stopped"),
}


def _custom_case(cid: str, *turns: dict) -> dict:
    return {
        "id": cid, "group": "long", "card": "LONG", "issue": "fresh",
        "known": "audit", "why": "长会话业务覆盖", "turns": list(turns),
    }


_INFORMATION_CASES = [
    # ⚠ `city_any` 2026-08-28 全组补上（C16-7 ＝ C9-E）：这五轮原来**只查了
    # `_prov`**，于是 T4/T5 答上海（mock 车辆位置回退的产物）一路判绿，城市漂移
    # 全靠人工漏检兜出来。本会话里用户只点过「深圳」一个城市，所以允许集就是它
    # ——判据挂在**卡片的 `city` 机读字段**上，没有该字段时退回「话术里至少出现
    # 一个」的正向要求（写成排除表就要一份全国城市名单，那是补不完的）。
    _custom_case("INF-WEATHER",
        {"say": "深圳现在天气怎么样",
         "expect": {"no_actions": True, "city_any": ["深圳"]},
         "audit": {"intent_any": ["info.weather"], "provider_required": True,
                   "provenance_required": True}},
        {"say": "明天呢", "expect": {"no_actions": True, "city_any": ["深圳"]},
         "audit": {"intent_any": ["info.weather", "info.forecast"],
                   "provenance_required": True}},
        {"say": "空气质量也看一下",
         "expect": {"no_actions": True, "city_any": ["深圳"]},
         "audit": {"intent_any": ["info.air_quality"],
                   "provenance_required": True}},
        {"say": "现在有影响开车的天气预警吗",
         "expect": {"no_actions": True, "city_any": ["深圳"]},
         "audit": {"intent_any": ["safety.weather_alert", "info.alerts"],
                   "provenance_required": True}},
        {"say": "明天适合洗车吗",
         "expect": {"no_actions": True, "city_any": ["深圳"]},
         "audit": {"intent_any": ["info.indices"],
                   "provenance_required": True}},
    ),
    _custom_case("INF-NEWS",
        {"say": "查一下今天人工智能行业的重要新闻", "expect": {"no_actions": True},
         "audit": {"intent_any": ["info.news", "info.search"], "provider_required": True,
                   "provenance_required": True}},
        {"say": "把来源名称说清楚", "expect": {"no_actions": True}},
        {"say": "这些消息分别是什么时间发布的", "expect": {"no_actions": True}},
        {"say": "再搜索一下钠离子电池最近的产业化进展", "expect": {"no_actions": True},
         "audit": {"intent_any": ["info.search"], "provenance_required": True}},
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
         "expect": {"need_confirm": True}, "audit": {"intent_any": ["trip.plan"]}},
        {"say": "先别确认，第二天有哪些安排", "expect": {"no_actions": True}},
        {"say": "不要把珠海排到广州前面", "expect": {"no_actions": True}},
        {"say": "如果时间不够就减少最后一天的景点",
         "expect": {"need_confirm": True}},
        {"say": "这份方案先取消，不执行", "expect": {"no_actions": True}},
    ),
    _custom_case("INF-MANUAL-SAFETY",
        {"say": "不知道具体车型时，标准胎压应该是多少", "expect": {"no_actions": True}},
        # ⚠ 2026-08-29 **刻意不加 `info.search`**（与迷你集 SF3 首轮那条同日裁决，方向相反）。
        # 真栈实测这一轮现在落 `info.search`，**答案很好**（「不能继续开。红色机油灯亮起
        # 表示机油压力严重低于标准…」），但**路径是错的**：它把一句安全问句当成了搜索题，
        # 没走手册域、没有 provenance，答对靠的是模型自己的知识。
        # **SF3 那条加 `chitchat.talk` 是因为那是安全闸开的设计内出口；这条不加，
        # 是因为 `info.search` 不是任何人设计的安全出口。**「都红了」不等于「同一种红」。
        # ⚠ 同批更正一处我自己写错的话：本轮安全闸让它**不再落 `system.clarify`**，
        # 但**没有**让它落进这份名单——「不再危险」和「落对域」是两栏，别合成一句
        # 「T23 修好了」。
        {"say": "红色机油灯亮了还能继续开吗", "expect": {"no_actions": True},
         # QA 安全收尾 v1.46：`chitchat.talk` 是 capability.response_only 声明、
         # 经统一问句副作用闸选择的设计内安全出口；与下方刻意不接纳的 info.search 不同。
         "audit": {"intent_any": ["safety.driver_state", "safety.driving_advice",
                                    "manual.query", "chitchat.talk"]}},
        {"say": "慢一点开可以吗", "expect": {"no_actions": True},
         "audit": {"intent_any": ["safety.driving_advice"]}},
        {"say": "你的判断依据来自车主手册还是通用安全建议", "expect": {"no_actions": True}},
        {"say": "好的，我会靠边停车检查", "expect": {"no_actions": True}},
    ),
    # ⚠ `honors_no_spicy` 2026-08-28 补（C12-D）：真栈 T29 一轮里确定性地拼出
    # 三句自相矛盾的话（「找到 10 家川菜」+「按您的口味优先川菜」+「记得您说过
    # 不吃辣」），五轮判据里没有一条量得到它。判的是**两条分支签名**（检索词回显
    # 与 taste_note），不是「话里有没有川菜」——修好之后的正确话术同样含「川菜」。
    _custom_case("INF-PREFERENCE",
        {"say": "我不吃辣，也不想排长队", "expect": {"no_actions": True}},
        {"say": "推荐附近适合晚饭的地方",
         "expect": {"no_actions": True, "honors_no_spicy": True},
         "audit": {"intent_any": ["nearby.search"], "provenance_required": True}},
        {"say": "为什么推荐这些", "expect": {"no_actions": True}},
        {"say": "只说我这轮明确给出的偏好", "expect": {"no_actions": True}},
        {"say": "把仍然缺的信息问清楚，不要猜", "expect": {"no_actions": True}},
    ),
    _custom_case("INF-REMINDER",
        {"say": "明天下午四点提醒我交周报QA{run}", "expect": {},
         "audit": {"intent_any": ["reminder.create"]}},
        {"say": "列出我现在进行中的提醒", "expect": {"no_actions": True},
         "audit": {"intent_any": ["reminder.list"]}},
        {"say": "把交周报QA{run}那条改到明天下午四点半",
         "expect": {"no_actions": True},
         "audit": {"intent_any": ["reminder.update"]}},
        {"say": "再查一次进行中的提醒", "expect": {"no_actions": True},
         "audit": {"intent_any": ["reminder.list"]}},
        {"say": "刚才实际改了哪一条，时间是什么", "expect": {"no_actions": True}},
        {"say": "取消交周报QA{run}的提醒", "expect": {"no_actions": True},
         "audit": {"intent_any": ["reminder.cancel"]}},
        {"say": "再查一次进行中的提醒",
         "expect": {"no_actions": True, "speech_not": ["交周报QA{run}"]},
         "audit": {"intent_any": ["reminder.list"]}},
    ),
    _custom_case("INF-STOCK",
        {"say": "查一下宁德时代现在的股价", "expect": {"no_actions": True},
         "audit": {"intent_any": ["info.stock"], "provenance_required": True}},
        # ⚠ 落域期望 2026-08-28（C4-B）**显式放宽了一格**：这一句现在由编排层的
        # 数据源读出口确定性回答（`system.data_provenance`），不再必须走到
        # `info.stock`。理由是被测行为变了、而且是往对的方向变——原来那条链
        # 「路由对了才有护栏」正是 T41 的病（MiniMax 把它落到 chitchat，编出
        # 「东方财富实时行情、19:23 前后」）。**值级判据一个字没松**：
        # `stock_provenance_from` 仍要求把上一轮卡里的真实 provider 与
        # market_time 逐字复述出来，`speech_has` 的两个词也原样保留
        # （「行情时间」这四个字由产生方经 `_prov.data_time_label` 声明）。
        # 同批放宽的还有 `provenance_required`——读出口念的是账本不是新取的数，
        # **它本来就不该有卡**，要求出卡等于要求它再打一次 provider。
        {"say": "数据源和更新时间是什么", "expect": {"no_actions": True,
                                                          "speech_has": ["数据来源", "行情时间"]},
         "audit": {"intent_any": ["info.stock", "system.data_provenance"],
                   "stock_provenance_from": 1}},
        {"say": "再看一下沪深300", "expect": {"no_actions": True},
         "audit": {"intent_any": ["info.stock"], "provenance_required": True}},
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

_ORDER_SLOT_INTERRUPTION = _custom_case(
    "LONG-ORDER-INTERRUPT",
    {"say": "帮我取消刚才那笔订单",
     "expect": {"need_confirm": False},
     "audit": {"intent_any": ["shop.order_cancel", "mcd.order_cancel",
                                "luckin.order_cancel"]}},
    {"say": "附近的咖啡店",
     "expect": {"card_type": "place_list", "need_confirm": False},
     "audit": {"intent_any": ["nearby.search"]}},
)

_FAMILY_TOPIC_PIVOT = _custom_case(
    "LONG-FAMILY-PIVOT",
    {"say": "我们换个话题，今天深圳天气怎么样",
     "expect": {"no_actions": True},
     "audit": {"intent_any": ["info.weather"]}},
)


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
        "SF4", "SF5", "AU2", "CD1", "CD2", "CA1", "CA5",
        "PU1", "PU2", "PU3", "PU4", "PU5", "PU6", "PU7", "PU8", "PU9",
        "SL1", "SF3", "XS2",
    ),
    "merchant": (
        "CD4", "CD5", "CD6", "CD7", "MC1", "MC2", "SP1", "SP2", "SP3",
        "XS4", "XS7", "XS8", "CD1", "CD2", "CD4", "CD5", "CD6",
        "CD7", "MC1", "MC2", "XS7", "XS8",
    ),
    "adversarial": (
        "CF1", "CF2", "CF4", "NG1", "NG2", "NG3", "NG4", "NG5", "NG6",
        "OR1", "OR2", "OR3", "EL1", "EL2", "EL3", "XS2", "XS3", "XS4",
        "XS7", "XS8", "SF1", "SF2", "SF3", "SF4", "SF5", "AU2",
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
            if cid == "SL1":
                # 保留 SL1 的原始考点：同一事项在一句话里创建两个提醒时间。
                # 清理也走真实用户路径：同名命中两条后按序号取消，再按标题取消剩余项。
                case["turns"][0]["say"] = (
                    "明天下午四点提醒我参加代号{run}的评审会，"
                    "三点半再提醒我一次"
                )
                # ⚠ `speech_has` 2026-08-28 补（C16-3）：这两轮原来只要
                # `reminder.cancel` 成功就判绿，**没有人对「取消的那条」与
                # 「用户点名的那条」**。真栈 T59 说「取消参加代号740945的评审会」，
                # 系统答「好的，取消了『刚才那个提醒现在几点』」——取消了完全
                # 另一条提醒，探针 `fails=[]`。**探针知道自己点名了什么**，
                # 那就该要求答话里出现它。
                case["turns"].extend([
                    {"say": "取消参加代号{run}的评审会",
                     "expect": {"no_actions": True, "card_items_at_least": 2},
                     "audit": {"intent_any": ["reminder.cancel"]}},
                    {"say": "取消第一条",
                     "expect": {"no_actions": True,
                                "speech_has": ["代号{run}的评审会"]},
                     "audit": {"intent_any": ["reminder.cancel"]}},
                    {"say": "取消参加代号{run}的评审会",
                     "expect": {"no_actions": True,
                                "speech_has": ["代号{run}的评审会"]},
                     "audit": {"intent_any": ["reminder.cancel"]}},
                    {"say": "列出我现在进行中的提醒",
                     "expect": {"no_actions": True,
                                "speech_not": ["代号{run}的评审会"]},
                     "audit": {"intent_any": ["reminder.list"]}},
                ])
            rows.append(case)
        plans[persona] = rows
        if persona in {"merchant", "adversarial"}:
            plans[persona].append(copy.deepcopy(_ORDER_SLOT_INTERRUPTION))
        elif persona == "family":
            plans[persona].append(copy.deepcopy(_FAMILY_TOPIC_PIVOT))
    plans["information"] = copy.deepcopy(_INFORMATION_CASES)
    return plans


_ENGINE_ONLY_TRACE_NODES = frozenset({
    "cloud.candidate_aggregate",
    "cloud.candidate_missing",
    "cloud.pending_cancel",
    "cloud.pending_missing",
    "cloud.pending_expired",
    "cloud.no_pending",
    "cloud.injection_reject",
    "cloud.no_plan",
    # C4-B 会话事实读出口族（2026-08-28）：这三条与上面那些同类——**编排自己
    # 答完就结束的轮**，没有 agent 归属 span 是它们的正常形态，不是缺证据。
    "cloud.pending_state",
    "cloud.data_provenance",
    "cloud.execution_audit",
    "clarify",
    "rejected",
})


def audit_trace_detail(
    detail: dict,
    trace_id: str,
    *,
    allow_cancelled: bool = False,
) -> tuple[dict, list[str]]:
    if not isinstance(detail, dict) or detail.get("error") == "not found":
        return {}, [f"collector 缺少 trace {trace_id}"]
    failures: list[str] = []
    turn = detail.get("turn")
    if not isinstance(turn, dict) or not turn:
        turn = {}
        failures.append(f"collector 缺少权威 turn {trace_id}")
    actual_trace = str(turn.get("trace_id") or "").strip()
    if actual_trace != trace_id:
        failures.append(
            f"collector trace 对不上：{actual_trace or '空'} != {trace_id}")
    status = str(turn.get("status") or "").strip().lower()
    allowed_statuses = {"ok", "need_confirm", "clarify", "rejected"}
    if allow_cancelled:
        allowed_statuses.add("cancelled")
    if not status:
        failures.append("collector 轮次状态不完整：空")
    elif status not in allowed_statuses:
        failures.append(f"collector 轮次状态异常：{turn.get('status')}")
    path = str(turn.get("path") or "").strip().lower()
    if path not in {"local", "mixed", "cloud"}:
        failures.append(f"collector 路由路径不完整：{path or '空'}")

    providers: list[str] = []
    fallbacks = 0
    pinned_calls = 0
    for call in detail.get("llm_calls") or []:
        provider = str(call.get("provider") or "").lower()
        model = str(call.get("model") or "")
        providers.append(f"{provider}:{model}")
        if provider != "minimax" or model != "MiniMax-M3":
            failures.append(f"出现非 MiniMax-M3 LLM 调用：{provider}:{model}")
        if call.get("fallback"):
            fallbacks += 1
        if not call.get("pinned"):
            failures.append("MiniMax-M3 LLM 调用未兑现请求级 pin")
        else:
            pinned_calls += 1
        if call.get("error"):
            failures.append(f"LLM 调用报错：{call.get('error')}")

    agents: set[str] = set()
    span_nodes: list[str] = []
    span_intents: list[str] = []
    for span in detail.get("spans") or []:
        node = str(span.get("node") or "")
        if node:
            span_nodes.append(node)
        attrs = span.get("attrs") or {}
        if isinstance(attrs, dict):
            for value in str(attrs.get("intents") or "").split(","):
                if value.strip():
                    span_intents.append(value.strip())
            value = str(attrs.get("intent") or "").strip()
            if value:
                span_intents.append(value)
            for key in ("agent_id", "agent"):
                value = str(attrs.get(key) or "").strip()
                if value:
                    agents.add(value)

    intents = [
        value.strip() for value in str(turn.get("intents") or "").split(",")
        if value.strip()
    ]
    intents = list(dict.fromkeys([*intents, *span_intents]))
    if not intents:
        failures.append("collector 轮次缺少可审计 intent")
    if path in {"cloud", "mixed"} and not agents \
            and not (_ENGINE_ONLY_TRACE_NODES & set(span_nodes)):
        failures.append("collector 云端/混合业务轮缺少 agent 归属 span")
    return {
        "path": path,
        "intents": intents,
        "agents": sorted(agents),
        "providers": providers,
        "pinned_calls": pinned_calls,
        "fallbacks": fallbacks,
        "span_nodes": span_nodes,
    }, failures


#: 外源数据卡的合法 `_prov.mode`（`mock` 由下面两档单独判，不在这里）。
_EXTERNAL_PROV_MODES = frozenset({"real", "cached", "degraded"})
#: 内部确定性卡的合法 mode。**只有登记过的卡型能打它**——外源卡打
#: `deterministic` 是盖错章（拿「我自己算的」躲开来源审计），仍判红。
_DETERMINISTIC_PROV_MODES = frozenset({"deterministic"})

#: §9.3「凡展示外源数据的卡必须带 `_prov`」的清单，**卡型名逐字照抄契约**。
#: 与契约的漂移由 `test_card_prov_rules_match_the_contract_mandatory_list`
#: 当场报红——这张表存在的理由就是把那份清单机械化成探针判据
#: （**一份声明两个消费方**：Agent 侧 `attach()` 盖章，探针侧按它验）。
_EXTERNAL_PROV_CARDS = frozenset({
    "weather", "forecast", "search_result", "news_brief", "stock_quote",
    "sports_scores", "sports_scorers", "place_list", "place_detail",
    "poi_list", "poi_detail", "route_plan", "charging_route",
    "air_quality", "weather_alerts", "life_indices",
})

#: 内部确定性卡（§9.3，2026-08-27 泓舟拍板收编 `deterministic`）：
#: `_prov` 可选，出现则 mode 必为 `deterministic`。road-safety 的 `safety_advice`
#: 两处早就在打它——**实现先于契约发明了一个正当的值**，本次是补登不是放宽。
_DETERMINISTIC_PROV_CARDS = frozenset({"safety_advice"})

#: 已声明「mock 可接受」的卡型 → 为什么可接受。**逐卡型逐条写、禁通配符**
#: （同 `capability_exemptions.yaml` 的口径）：一条豁免要能被读的人问出
#: 「它什么时候能撤」。这里的答案是「真车型手册接入之日」。
_MOCK_ACCEPTED_PROV_CARDS = {
    "manual": "manual-rag 真车型手册未接入，PoC 已知形态（§9.3 裁决 ③：等真手册，不引入新工作）",
}


def card_prov_rule(card_type: str) -> dict:
    """卡型 → `{"modes": 允许的非 mock mode 集合, "mock": "warn"|"fail"}`。

    未登记的卡型按**外源默认**处理（modes=real/cached/degraded、mock 判红）
    ——新卡型想打 `deterministic` 或想让 mock 过关，先在上面登记一行。
    """
    name = str(card_type or "").strip()
    modes = (_DETERMINISTIC_PROV_MODES if name in _DETERMINISTIC_PROV_CARDS
             else _EXTERNAL_PROV_MODES)
    return {"modes": modes,
            "mock": "warn" if name in _MOCK_ACCEPTED_PROV_CARDS else "fail"}


def audit_card_provenance(
        card_text: str, *, required: bool = False,
) -> tuple[list[dict], list[str], list[str], list[str]]:
    """提取卡片树里的真实性章 → `(章, 失败, 警告)`。

    ## 两把尺子方向相反，2026-08-27 由泓舟裁决拆开（fix plan C15）

    探针原来无条件把 `mode=mock` 判红，而契约 §9.3 / §9.17 要求 mock **如实标注
    即合法**（`payment_qr` 的 mock 渠道**必须**打 mock）。两个立场说的不是一件事：
    探针那条是**部署形态期望**（真栈不该有 mock），契约那条是**诚实契约**
    （有 mock 必须承认）。裁决：

    · **`deterministic` 收进合法 mode**——内部确定性产物的自我声明，与
      degraded/mock 正交。但只对**登记过的内部确定性卡**（`safety_advice`）合法。
    · **mock 拆两档**——该卡型已声明「mock 可接受」（`manual`，真手册接入前）
      ⇒ 记 **WARN 不判 fail**；没声明的卡型出现 mock ⇒ 仍判 fail。
    · **「mock 冒充 real」永远是 fail**，这一档在上面两条之外：缺 `_prov`
      （`required` 那支）、provider 缺失、mode 写了个词表外的值，都判红。

    ## `required` 的语义 2026-08-28 收窄成「**出了卡就必须有章**」

    原来它把两件事判成同一件：「出了卡没盖章」（真缺陷）与「**这一轮压根没出卡**」。
    后者在修好之后成了**正确行为**——真栈实测 INF-WEATHER T4「现在有影响开车的
    天气预警吗」答「深圳当前没有生效的天气预警。」、INF-STOCK T41 答「没有找到
    「000300.SH」的行情数据」，**没有数据所以不出卡**，判红等于要求它编一张卡。
    ⇒ 无卡这一档改出 **note 不判 fail**（同 C2-D「『读不到』与『读到了但不对』
    永远分开报」）。真要求「这一轮必须有卡」，用例请显式写 ``card_type``
    ——**「要有章」和「要有卡」是两个主张，判据也该是两条。**

    返回第四个通道就是这些 note：它们**不进 fail 也不进 WARN**，
    只是让读数的人看见「这一轮为什么没被这条判据压到」。

    生产契约用的是 ``vendor``，少数外部卡历史上用 ``provider``。报告统一成
    ``provider``，但不把缺章的普通控制卡误判成 provider 卡。
    ``card_type`` 逐章记录（就近的 ``type``，`card_group` 成员用成员自己的）
    ——**没有卡型就没法按卡型判**，这是本次拆档的前提。
    """
    raw = str(card_text or "").strip()
    if not raw or raw == "{}":
        return [], [], [], ([
            "本轮没有出卡（诚实降级也长这样）⇒ 对「外源卡必须盖章」**不构成证据**；"
            "要求必须出卡请在用例上写 `card_type`"
        ] if required else [])
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return [], [f"卡片 JSON 无法审计 provenance: {type(exc).__name__}"], [], []

    entries: list[dict] = []

    def walk(value, card_type: str) -> None:
        if isinstance(value, dict):
            node_type = str(value.get("type") or "").strip() or card_type
            prov = value.get("_prov")
            if isinstance(prov, dict):
                provider = str(
                    prov.get("provider") or prov.get("vendor")
                    or prov.get("source") or "unknown").strip()
                mode = str(prov.get("mode") or "unknown").strip().lower()
                entries.append({"provider": provider, "mode": mode,
                                "card_type": node_type})
            for key, child in value.items():
                if key != "_prov":
                    walk(child, node_type)
        elif isinstance(value, list):
            for child in value:
                walk(child, card_type)

    walk(payload, "")
    failures: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    if required and not entries:
        # 出了卡却一个章都没有 —— 这一档仍然是红，它才是原判据要抓的那件事。
        failures.append("外部数据卡缺少真实性章")
    for entry in entries:
        card_type = entry["card_type"]
        label = f"{card_type or '未命名卡'} 卡"
        rule = card_prov_rule(card_type)
        if not entry["provider"] or entry["provider"].lower() == "unknown":
            failures.append("外部数据卡真实性章 provider 缺失")
        mode = entry["mode"]
        if mode == "mock":
            if rule["mock"] == "warn":
                warnings.append(
                    f"{label}如实标注了 mock：{entry['provider']}"
                    f"——{_MOCK_ACCEPTED_PROV_CARDS[card_type]}")
            else:
                failures.append(f"真栈卡片出现 mock provenance: {entry['provider']}")
        elif mode not in rule["modes"]:
            failures.append(
                f"外部数据卡真实性章 mode 非法: {mode}"
                f"（{label}只许 {sorted(rule['modes'])}）")
    return entries, failures, warnings, notes


def audit_stock_provenance_followup(row: dict, prior_rows: list[dict],
                                    source_turn: int) -> list[str]:
    """把省略式来源追问与上一轮真实股票卡逐字段对账。"""
    previous = next((candidate for candidate in prior_rows
                     if candidate.get("case_instance") == row.get("case_instance")
                     and int(candidate.get("local_turn") or 0) == source_turn), None)
    if previous is None:
        return [f"股票来源追问缺少第 {source_turn} 轮卡片证据"]
    try:
        card = json.loads(str(previous.get("card_text") or ""))
    except (TypeError, ValueError):
        return [f"股票来源追问的第 {source_turn} 轮卡片无法解析"]
    if not isinstance(card, dict):
        return [f"股票来源追问的第 {source_turn} 轮卡片不是对象"]

    prov = card.get("_prov") or {}
    provider = str(prov.get("vendor") or prov.get("provider")
                   or prov.get("source") or "").strip()
    market_time = str(card.get("market_time") or "").strip()
    if not provider or not market_time:
        return ["股票来源追问的前序卡缺 provider 或 market_time"]

    provider_aliases = {
        "tushare": ("tushare",),
        "eastmoney": ("eastmoney", "东方财富"),
        "alphavantage": ("alpha vantage", "alphavantage"),
    }.get(provider.lower(), (provider.lower(),))
    speech = str(row.get("speech") or "")
    speech_lower = speech.lower()
    failures: list[str] = []
    if not any(alias in speech_lower for alias in provider_aliases):
        failures.append(f"股票来源追问没有复述真实 provider：{provider}")
    if market_time not in speech:
        failures.append(f"股票来源追问没有复述真实行情时间：{market_time}")
    return failures


def _current_head_sha() -> str:
    try:
        value = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
            encoding="utf-8", errors="strict", timeout=10).strip().lower()
    except Exception as exc:
        raise SystemExit(
            f"无法解析当前 Git SHA：{type(exc).__name__}") from exc
    if not _FULL_SHA_RE.fullmatch(value):
        raise SystemExit("当前 Git SHA 不是完整 40 位 SHA")
    return value


def _unified_cloud_status() -> tuple[int, dict]:
    """Run the repository's single stack-status entry without scraping stdout."""
    from scripts import dev_stack

    payloads: list[dict] = []
    rc = dev_stack.main(["status"], repo=_ROOT, emit=payloads.append)
    payload = payloads[-1] if payloads else {}
    return int(rc), payload if isinstance(payload, dict) else {}


def validate_release_snapshot(snapshot: dict, expected_sha: str) -> list[str]:
    """Require a healthy cloud status bound to the exact tested release."""
    failures: list[str] = []
    expected = str(expected_sha or "").strip().lower()
    release = str(snapshot.get("release_sha") or "").strip().lower()
    if snapshot.get("target") != "cloud":
        failures.append(f"云端状态目标不是 cloud：{snapshot.get('target') or '空'}")
    if snapshot.get("status") != "ok":
        failures.append(f"云端 status 不是 ok：{snapshot.get('status') or '空'}")
    if not _FULL_SHA_RE.fullmatch(release):
        failures.append("云端 release_sha 不是完整 40 位 SHA")
    if not _FULL_SHA_RE.fullmatch(expected):
        failures.append("expected_sha 不是完整 40 位 SHA")
    elif _FULL_SHA_RE.fullmatch(release) and release != expected:
        failures.append(
            f"云端 release_sha={release}，与 expected_sha={expected} 不一致")
    healthy = int(snapshot.get("healthy_endpoints") or 0)
    endpoints = snapshot.get("endpoint_results") or []
    if healthy != 5 or len(endpoints) != 5 or any(
            not isinstance(item, dict) or item.get("status") != "healthy"
            for item in endpoints):
        failures.append(f"云端端点不是 5/5 healthy：{healthy}/5")
    warnings = [str(item) for item in (snapshot.get("warnings") or []) if str(item)]
    if warnings:
        failures.append("云端 status 带 warning：" + "；".join(warnings))
    return failures


def cloud_release_snapshot(expected_sha: str, *, status_probe=None) -> dict:
    """Capture one redacted live status payload and bind it to expected SHA."""
    probe_fn = status_probe or _unified_cloud_status
    try:
        rc, payload = probe_fn()
    except Exception as exc:
        payload = {}
        rc = -1
        error = f"云端 status 探测失败：{type(exc).__name__}"
    else:
        error = ""
    snapshot = dict(payload) if isinstance(payload, dict) else {}
    snapshot["expected_sha"] = str(expected_sha or "").strip().lower()
    snapshot["status_command_rc"] = int(rc)
    failures = ([error] if error else []) + validate_release_snapshot(
        snapshot, expected_sha)
    if rc != 0:
        failures.append(f"dev_stack status 返回 rc={rc}")
    snapshot["failures"] = list(dict.fromkeys(failures))
    return snapshot


def validate_release_continuity(
    start: dict,
    end: dict,
    expected_sha: str,
) -> list[str]:
    """Bind the entire long run—not only its first turn—to one healthy SHA."""
    failures: list[str] = []
    for label, snapshot in (("start", start), ("end", end)):
        reasons = validate_release_snapshot(
            snapshot if isinstance(snapshot, dict) else {}, expected_sha,
        )
        if isinstance(snapshot, dict):
            reasons.extend(str(item) for item in snapshot.get("failures") or [])
            rc = snapshot.get("status_command_rc")
            if rc is not None and int(rc) != 0:
                reasons.append(f"dev_stack status 返回 rc={rc}")
        failures.extend(f"{label}: {reason}" for reason in dict.fromkeys(reasons))
    return list(dict.fromkeys(failures))


def managed_vehicle_state(
    state: dict,
    *,
    required_keys: set[str] | tuple[str, ...] | None = None,
) -> dict:
    """Project only authoritative collector keys; never invent a baseline."""
    source = state if isinstance(state, dict) else {}
    wanted = set(required_keys) if required_keys is not None else None
    return {
        key: source[key]
        for key in _MANAGED_VEHICLE_KEYS
        if key in source and (wanted is None or key in wanted)
    }


def _required_vehicle_state_keys(_cases: list[dict]) -> tuple[str, ...]:
    """Snapshot every safely restorable key before any persona utterance.

    Negative/read-only cases are specifically meant to catch unexpected
    actions. Deriving this set from ``actions_include`` would omit exactly the
    state an erroneous ``actions_exclude`` violation can mutate.
    """
    return _MANAGED_VEHICLE_KEYS


def _expected_vehicle_state(actions: list[str]) -> dict:
    expected: dict[str, object] = {}
    for action in actions:
        target = _VEHICLE_ACTION_TARGETS.get(str(action))
        if target:
            key, value = target
            expected[key] = value
    return expected


def _vehicle_value_matches(actual: object, expected: object) -> bool:
    if expected == "open" and isinstance(actual, str):
        return actual == "open" or bool(re.fullmatch(r"(?:100|[1-9]?\d)%", actual))
    return actual == expected


def _position_command(object_name: str, value: object) -> str:
    label = "车窗" if object_name == "window" else "天窗"
    text = str(value or "").strip().lower()
    if text == "closed":
        return f"关闭{label}"
    if text == "open":
        return f"打开{label}"
    if re.fullmatch(r"(?:100|[1-9]?\d)%", text):
        return f"把{label}开到{text}"
    raise ValueError(f"无法安全恢复 {object_name}={value!r}")


def vehicle_restore_commands(before: dict, after: dict) -> list[tuple[str, str]]:
    """Map semantic state differences to deterministic, non-dangerous utterances."""
    target = managed_vehicle_state(before)
    current = managed_vehicle_state(after, required_keys=set(target))
    missing = [key for key in target if key not in current]
    if missing:
        raise ValueError("车辆恢复前态缺少权威终态键：" + ", ".join(missing))
    builders = {
        "hvac_on": lambda value: "打开空调" if value else "关闭空调",
        "front_defogger": lambda value: (
            "打开前挡风玻璃除雾" if value else "关闭前挡风玻璃除雾"),
        "rear_defogger": lambda value: (
            "打开后挡风玻璃除雾" if value else "关闭后挡风玻璃除雾"),
        "window": lambda value: _position_command("window", value),
        "sunroof": lambda value: _position_command("sunroof", value),
        "rear_view_mirror": lambda value: (
            "把后视镜折叠起来" if value == "folded" else "把后视镜展开"),
        "steering_wheel_heating": lambda value: (
            "打开方向盘加热" if value else "关闭方向盘加热"),
        "volume_muted": lambda value: "静音" if value else "取消静音",
        "warning_light": lambda value: "打开双闪" if value else "关闭双闪",
        "media": lambda value: {
            "playing": "播放音乐", "paused": "暂停音乐", "stopped": "关闭音乐",
        }[str(value)],
    }
    commands: list[tuple[str, str]] = []
    for key in _MANAGED_VEHICLE_KEYS:
        if key not in target:
            continue
        if current[key] == target[key]:
            continue
        try:
            command = builders[key](target[key])
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"无法安全恢复 {key}={target[key]!r}") from exc
        commands.append((key, command))
    return commands


def judge_restore_turn(state_key: str, target, actions: list) -> list[str]:
    """恢复轮：**这一句真的动了它该动的那个东西吗**（N7 / C16-6）。

    原判据只查 `need_confirm=False`，于是 2026-08-26 那轮里
    「关闭后挡风玻璃除雾」执行成 `front_defogger.close`（关错了另一个物理开关）、
    「关闭音乐」落 `media.pause`（到不了 `stopped`）**双双判绿**。
    「动作发出去了」与「动作落在目标上」是两件事，探针只看了前一半。

    三条，按顺序：
      · 一个动作都没有 ⇒ 红。恢复命令说出去了却什么都没发生，本身就是失败。
      · 动作落到**别的**受管状态键上 ⇒ 红。这条抓的是「关错对象」，
        它比值不对更恶性：它在**改一个本来不该动的东西**。
      · 落对了键但值不对 ⇒ 红（`media.pause` vs 目标 `stopped` 就死在这条）。

    只对 `_VEHICLE_ACTION_TARGETS` 认得的动作下结论——认不出的动作（如
    `window.set` 开到 50%）**不当证据也不当罪证**，那是尺子的覆盖面问题，
    不该变成被测对象的红灯。
    """
    observed = [str(action) for action in (actions or [])]
    if not observed:
        return [f"恢复 {state_key} 没有产生任何动作"]
    mapped = [(name, _VEHICLE_ACTION_TARGETS[name]) for name in observed
              if name in _VEHICLE_ACTION_TARGETS]
    wrong = [name for name, (key, _value) in mapped if key != state_key]
    if wrong:
        return [f"恢复 {state_key} 的动作落到了别的状态键：{wrong}（actions={observed}）"]
    on_target = [value for _name, (key, value) in mapped if key == state_key]
    if on_target and not any(
            _vehicle_value_matches(value, target) for value in on_target):
        return [f"恢复 {state_key} 的动作值不对：actions={observed} "
                f"落到 {on_target!r}，目标 {target!r}"]
    return []


def audit_merchant_draft_cleanup(card_text: str) -> tuple[dict, list[str]]:
    """Validate the bridge's owner/session-scoped zero-state proof card."""
    try:
        card = json.loads(str(card_text or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        card = {}
    if not isinstance(card, dict) or card.get("type") != "merchant_draft_cleanup":
        return {}, ["商户临时预览清理缺少可审计卡片"]
    proof = {
        "session_id_digest": str(card.get("session_id_digest") or ""),
        "drafts_before": int(card.get("drafts_before") or 0),
        "drafts_removed": int(card.get("drafts_removed") or 0),
        "drafts_after": int(card.get("drafts_after") or 0),
    }
    failures: list[str] = []
    if proof["drafts_after"] != 0:
        failures.append(
            f"商户临时预览清理后仍有 {proof['drafts_after']} 条 active draft")
    elif proof["drafts_removed"] != proof["drafts_before"]:
        failures.append(
            "商户临时预览清理计数不守恒："
            f"before={proof['drafts_before']} removed={proof['drafts_removed']}")
    if not proof["session_id_digest"]:
        failures.append("商户临时预览清理卡缺 session 摘要")
    return proof, failures


def _resolve_endpoints() -> tuple[str, str, str, str]:
    """从同一个 target 快照派生 WS、collector 与 audio；仅准云端真栈。"""
    env = dict(os.environ)
    env.update(read_root_env(_ROOT, {"TAILNET_FQDN", "VITE_WS_TOKEN"}))
    target = resolve_e2e_target(_ROOT, explicit=None, environ=env)
    if target.name != "cloud":
        raise SystemExit("长会话 QA 只允许 target=cloud，拒绝本地或混档运行")
    endpoints = endpoint_environment(target)
    token = str(env.get("VITE_WS_TOKEN") or "").strip()
    if not token:
        raise SystemExit("cloud 档缺 VITE_WS_TOKEN（根 .env）——网关会拒绝 upgrade")
    parts = urllib.parse.urlsplit(endpoints["WS_URL"])
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query.append(("token", token))
    ws_url = urllib.parse.urlunsplit(
        parts._replace(query=urllib.parse.urlencode(query)))
    return (ws_url, endpoints["COLLECTOR_URL"], endpoints["AUDIO_API_URL"],
            target.name)


def pcm_metrics(audio: bytes | bytearray, *, sample_rate: int) -> dict:
    """Prove that returned mono s16le PCM is long/non-silent enough to play."""
    raw = bytes(audio or b"")
    base = {
        "sample_rate": int(sample_rate or 0), "sample_count": 0,
        "duration_ms": 0, "peak_abs": 0, "rms": 0,
        "nonzero_ratio": 0.0, "playable": False,
    }
    if not raw or len(raw) % 2 or sample_rate < 8000 or sample_rate > 48000:
        return base
    samples = [int.from_bytes(raw[index:index + 2], "little", signed=True)
               for index in range(0, len(raw), 2)]
    count = len(samples)
    peak = max(abs(value) for value in samples)
    nonzero = sum(abs(value) >= 16 for value in samples)
    duration_ms = round(count * 1000 / sample_rate)
    rms = math.isqrt(sum(value * value for value in samples) // count)
    ratio = round(nonzero / count, 6)
    return {
        "sample_rate": int(sample_rate), "sample_count": count,
        "duration_ms": duration_ms, "peak_abs": peak, "rms": rms,
        "nonzero_ratio": ratio,
        "playable": bool(duration_ms >= 250 and peak >= 64 and ratio >= 0.01),
    }


def validate_minimax_tts(result: dict) -> list[str]:
    """Fail closed validation for the auditable MiniMax cloud TTS sample."""
    failures: list[str] = []
    if str(result.get("provider") or "").lower() != "minimax":
        failures.append("TTS 未锁定 minimax")
    if not result.get("capability_available"):
        failures.append("MiniMax TTS 能力未在云端声明 available")
    meta = result.get("meta") or {}
    if not isinstance(meta, dict) or meta.get("format") != "pcm":
        failures.append("MiniMax TTS 未返回 PCM meta")
    if int(result.get("chunks") or 0) < 1 or int(result.get("audio_bytes") or 0) < 1:
        failures.append("MiniMax TTS 未返回二进制音频帧")
    if result.get("terminal") != "done":
        failures.append(
            f"MiniMax TTS 未正常 done（terminal={result.get('terminal') or '空'}）")
    pcm = result.get("pcm") or {}
    if (int(result.get("chunks") or 0) < 1
            or not isinstance(pcm, dict) or not pcm.get("playable")):
        failures.append("MiniMax TTS PCM 不能被 HMI 播放")
    return failures


def validate_tts_barge_in(result: dict) -> list[str]:
    failures: list[str] = []
    if str(result.get("provider") or "").lower() != "minimax":
        failures.append("TTS 打断未锁定 minimax")
    if not result.get("cancel_sent"):
        failures.append("MiniMax TTS 打断帧未发出")
    if int(result.get("audio_before_cancel_bytes") or 0) < 1:
        failures.append("MiniMax TTS 未起播，无法验证打断")
    if int(result.get("post_cancel_audio_bytes") or 0) != 0:
        failures.append("MiniMax TTS cancel 后仍收到音频残帧")
    closed_ms = result.get("closed_after_cancel_ms")
    if (result.get("terminal") != "closed_after_cancel"
            or not isinstance(closed_ms, (int, float)) or closed_ms > 5000):
        failures.append("MiniMax TTS cancel 后连接未及时关闭")
    return failures


def select_tts_business_samples(results: list[dict]) -> dict[str, dict]:
    """Pick one successful, real business reply from every selected persona."""
    excluded = {"AUTO-CANCEL", "RECOVERY", "VEHICLE-RESTORE",
                "NAVIGATION-CLEANUP", "MERCHANT-DRAFT-CLEANUP"}
    selected: dict[str, dict] = {}
    for result in results:
        candidates = [
            row for row in result.get("turns") or []
            if row.get("case") not in excluded
            and not row.get("error") and not row.get("fails")
            and str(row.get("speech") or "").strip()
        ]
        if not candidates:
            continue
        row = max(candidates, key=lambda item: len(str(item.get("speech") or "")))
        persona = str(result.get("persona") or "")
        selected[persona] = {
            "persona": persona,
            "session_id": str(result.get("session_id") or ""),
            "turn": int(row.get("turn") or 0),
            "case": str(row.get("case") or ""),
            "trace_id": str(row.get("trace_id") or ""),
            "text": str(row.get("speech") or "").strip(),
        }
    return selected


def _tts_ws_url(audio_url: str) -> str:
    parts = urllib.parse.urlsplit(audio_url.rstrip("/"))
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urllib.parse.urlunsplit(parts._replace(
        scheme=scheme, path=f"{parts.path}/api/tts/stream"))


async def audit_minimax_tts(audio_url: str, text: str, *,
                            source: dict | None = None) -> dict:
    """Probe MiniMax TTS through the cloud audio gateway and retain byte evidence."""
    result = {
        "provider": "minimax", "model": "", "voice": "",
        "capability_available": False, "meta": None, "chunks": 0,
        "audio_bytes": 0, "first_chunk_ms": None,
        "done_first_chunk_ms": None, "terminal": None, "pcm": {},
        "source": dict(source or {}),
        "text_sha256": hashlib.sha256(
            str(text or "").encode("utf-8")).hexdigest(),
        "text_chars": len(str(text or "")), "failures": [],
    }
    try:
        info = await asyncio.to_thread(
            _http_json, f"{audio_url.rstrip('/')}/api/tts/stream/info")
    except Exception as exc:
        result["failures"] = [
            f"MiniMax TTS 能力探测失败：{type(exc).__name__}",
            *validate_minimax_tts(result),
        ]
        return result

    providers = info.get("providers") if isinstance(info, dict) else []
    capability = next((item for item in (providers or [])
                       if str(item.get("id") or "").lower() == "minimax"), None)
    if isinstance(capability, dict):
        result["capability_available"] = bool(capability.get("available"))
        result["model"] = str(capability.get("model") or "")
        voices = [str(item.get("voice_id") or "")
                  for item in capability.get("voices") or []
                  if isinstance(item, dict) and item.get("voice_id")]
        result["voice"] = (
            "female-tianmei" if "female-tianmei" in voices
            else (voices[0] if voices else "")
        )
    if not result["capability_available"] or not result["voice"]:
        result["failures"] = validate_minimax_tts(result)
        return result

    started = time.monotonic()
    audio = bytearray()
    try:
        async with probe.websockets.connect(
                _tts_ws_url(audio_url), max_size=8 * 1024 * 1024) as ws:
            await ws.send(json.dumps({
                "type": "start", "provider": "minimax",
                "model": result["model"], "voice": result["voice"],
            }, ensure_ascii=False))
            await ws.send(json.dumps({
                "type": "text", "delta": str(text or "").strip(),
            }, ensure_ascii=False))
            await ws.send(json.dumps({"type": "finish"}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
                if isinstance(raw, (bytes, bytearray)):
                    if result["first_chunk_ms"] is None:
                        result["first_chunk_ms"] = round(
                            (time.monotonic() - started) * 1000)
                    result["chunks"] += 1
                    result["audio_bytes"] += len(raw)
                    audio.extend(raw)
                    continue
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "meta":
                    result["meta"] = message
                elif kind in {"done", "error", "unsupported"}:
                    result["terminal"] = kind
                    if kind == "done":
                        result["done_first_chunk_ms"] = message.get("first_chunk_ms")
                    elif message.get("message"):
                        result["error"] = str(message["message"])
                    break
    except Exception as exc:
        result["terminal"] = result["terminal"] or "transport_error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    meta = result.get("meta") or {}
    result["pcm"] = pcm_metrics(
        audio, sample_rate=int(meta.get("sample_rate") or 0)
        if isinstance(meta, dict) else 0)
    result["failures"] = validate_minimax_tts(result)
    return result


async def audit_minimax_tts_barge_in(audio_url: str, text: str) -> dict:
    """Start real MiniMax audio, then send the same cancel frame as HMI.stopTTS."""
    result = {
        "provider": "minimax", "model": "", "voice": "",
        "cancel_sent": False, "audio_before_cancel_bytes": 0,
        "post_cancel_audio_bytes": 0, "terminal": None,
        "closed_after_cancel_ms": None, "failures": [],
    }
    try:
        info = await asyncio.to_thread(
            _http_json, f"{audio_url.rstrip('/')}/api/tts/stream/info")
        providers = info.get("providers") if isinstance(info, dict) else []
        capability = next((item for item in (providers or [])
                           if str(item.get("id") or "").lower() == "minimax"), None)
        if not isinstance(capability, dict) or not capability.get("available"):
            raise RuntimeError("minimax unavailable")
        result["model"] = str(capability.get("model") or "")
        voices = [str(item.get("voice_id") or "")
                  for item in capability.get("voices") or []
                  if isinstance(item, dict) and item.get("voice_id")]
        result["voice"] = (
            "female-tianmei" if "female-tianmei" in voices
            else (voices[0] if voices else ""))
        if not result["voice"]:
            raise RuntimeError("minimax voice unavailable")

        async with probe.websockets.connect(
                _tts_ws_url(audio_url), max_size=8 * 1024 * 1024) as ws:
            await ws.send(json.dumps({
                "type": "start", "provider": "minimax",
                "model": result["model"], "voice": result["voice"],
            }, ensure_ascii=False))
            # Do not finish: HMI barge-in may happen while text is still arriving.
            # A punctuated business reply is enough for MiniMax to emit its first chunk.
            payload = (str(text or "").strip() + "。") * 4
            await ws.send(json.dumps({"type": "text", "delta": payload},
                                     ensure_ascii=False))
            while not result["cancel_sent"]:
                raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
                if isinstance(raw, (bytes, bytearray)) and raw:
                    result["audio_before_cancel_bytes"] += len(raw)
                    await ws.send(json.dumps({"type": "cancel"}))
                    result["cancel_sent"] = True
                    cancelled_at = time.monotonic()
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    if isinstance(raw, (bytes, bytearray)):
                        result["post_cancel_audio_bytes"] += len(raw)
            except asyncio.TimeoutError:
                result["terminal"] = "timeout"
            except Exception as exc:
                if "ConnectionClosed" in type(exc).__name__:
                    result["terminal"] = "closed_after_cancel"
                    result["closed_after_cancel_ms"] = round(
                        (time.monotonic() - cancelled_at) * 1000)
                else:
                    raise
    except Exception as exc:
        result["terminal"] = result["terminal"] or "transport_error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["failures"] = validate_tts_barge_in(result)
    return result


def recovery_turns() -> tuple[dict, dict, dict]:
    """Three post-failure turns with distinct state, domain and closeout checks."""
    return (
        # ⚠ 2026-08-28（C16-5）补 `intent_any`。原来这一轮只有「别说某两句话」
        # 的排除式判据，于是真栈里答**「嗯」**（vehicle T51）和答**一个学校地址**
        # （information T56）双双判绿——「答非所问」不是措辞问题，排除表永远
        # 抓不到它。C4-B 之后这句话有确定性读出口了（`system.pending_state`；
        # 裸取消路径上是 `system.no_pending`），断言就该落在**出口命中**上：
        # 落到 chitchat 就是「护栏建在被路由之后」的那个形态（重判 5）。
        {"say": "现在还有待确认的操作吗",
         "expect": {"no_actions": True, "need_confirm": False,
                    "speech_not": ["仍有待确认", "还有待确认的操作"]},
         "audit": {"intent_any": ["system.pending_state", "system.no_pending"]}},
        {"say": "今天深圳天气怎么样",
         "expect": {"no_actions": True, "need_confirm": False},
         "audit": {"intent_any": ["info.weather"],
                   "provenance_required": True}},
        {"say": "谢谢，继续",
         "expect": {"no_actions": True, "need_confirm": False},
         "audit": {}},
    )


def _http_json(url: str, timeout: float = 20.0) -> dict | list:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


async def _connect(ws_url: str, *, attempts: int = 4):
    """建立真栈 WebSocket；TLS/代理瞬断做有界重试，不让整个人格丢 artifact。"""
    if attempts < 1:
        raise ValueError("attempts must be positive")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        ws = None
        try:
            ws = await probe.websockets.connect(ws_url)
            try:
                await asyncio.wait_for(ws.recv(), timeout=probe._HELLO_WAIT_S)
            except asyncio.TimeoutError:
                pass
            return ws
        except Exception as exc:
            last_error = exc
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass
            if attempt < attempts:
                await asyncio.sleep(0.5 * attempt)
    assert last_error is not None
    raise last_error


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
    final_checks: dict[str, tuple[str, object]] = {}
    for action in actions:
        if action in checks:
            key, expected = checks[action]
            final_checks[key] = (action, expected)
    for key, (action, expected) in final_checks.items():
        actual = state.get(key)
        if expected == "open":
            ok = actual == "open" or str(actual).endswith("%")
        else:
            ok = actual == expected
        if not ok:
            failures.append(f"状态未兑现 {action}: {key}={actual!r}, 期望 {expected!r}")
    return failures


def _track_active_operations(active: dict[str, int], obs: dict,
                             turn_no: int) -> None:
    """维护待清理操作集合；只有服务端 ``closed_operation_ids`` 能证明关闭。"""
    operation_id = str(obs.get("operation_id") or "")
    if operation_id:
        active[operation_id] = turn_no
    for closed in obs.get("closed_operation_ids") or []:
        active.pop(str(closed), None)


def validate_cleanup_result(operation_id: str, obs: dict) -> list[str]:
    """Only exact server closure evidence makes a suspended operation safe to leave."""
    judged = {"speech": "", "actions": [], "need_confirm": False, **obs}
    failures = probe._judge({"no_actions": True}, judged, [], [])
    if obs.get("error"):
        failures.append("取消轮 transport/backend error")
    if obs.get("need_confirm"):
        failures.append("取消后仍处于待确认")
    closed_ids = [str(value) for value in
                  (obs.get("closed_operation_ids") or [])]
    if operation_id not in closed_ids:
        failures.append(
            f"服务端未证明待确认 {operation_id} 已关闭（closed={closed_ids or '空'}）")
    return failures


def judge_persona_turn(turn: dict, obs: dict, local_rows: list[dict], *,
                       stamp: int) -> tuple[list[str], list[str]]:
    """用案例内轮号判一轮，并把意外待确认视为全局安全红灯。

    ``probe._judge`` 的引用契约（``names_item_from.turn`` / ``op_from`` 等）
    是**单个 case 内从 1 开始**。长会话行同时还保存 persona 全局 ``turn``；把后者
    直接传入会让第一个 case 之后的引用全部找不到前提，明明卡上有 20 项却报 0 项。

    另外，动作列表为空不等于安全：普通只读查询若意外续接成退款/车控确认，仍然没有
    action，却已经把错误写操作推进到危险边界。只有用例明确声明
    ``need_confirm: true`` 的轮次才允许挂起。
    """
    expected = probe._subst(turn.get("expect") or {}, stamp)
    judge_rows = [
        {**row, "turn": int(row.get("local_turn") or row.get("turn") or 0)}
        for row in local_rows
    ]
    notes: list[str] = []
    failures = probe._judge(expected, obs, judge_rows, notes)
    if obs.get("need_confirm") and expected.get("need_confirm") is not True:
        failures.append("只读/普通业务轮意外进入待确认")
    return failures, notes


#: 兜底闲聊的落域名。C16-2 只在这条路径上判「说了没做」——**别的域的完成语
#: 多半是真的**（trip/research 这类不出 action 的能力，「已为您规划3天行程」
#: 就是事实），而 chitchat 手上一个执行通道都没有，它说自己做了就一定没做。
_CHITCHAT_INTENT = "chitchat.talk"


def audit_row_expectations(row: dict, rows: list[dict],
                           expected: dict) -> tuple[list[str], list[str]]:
    """行级审计判据（真实性章 / 落域 / provider / 编造执行）→ `(失败, 警告)`。

    从跑批主循环里拎出来是为了**让回放模式跑的是同一份判据**（`--replay`）：
    「修正后计分」如果用的是另一份实现，它就只能证明那一份实现是什么样。
    """
    failures: list[str] = []
    warnings: list[str] = []
    provenance, prov_failures, prov_warnings, prov_notes = audit_card_provenance(
        row.get("card_text") or "",
        required=bool(expected.get("provenance_required")))
    row["provenance"] = provenance
    failures.extend(prov_failures)
    warnings.extend(prov_warnings)
    if prov_notes:
        row.setdefault("notes", []).extend(prov_notes)

    intents = ((row.get("trace") or {}).get("intents")) or []
    allowed = expected.get("intent_any") or []
    if allowed and not any(want in intents for want in allowed):
        failures.append(f"落域不符：期望其一 {allowed}，实际 {intents or '空'}")
    forbidden = expected.get("intent_not") or []
    hits = sorted(set(forbidden) & set(intents))
    if hits:
        failures.append(f"命中禁止落域：{hits}")
    if expected.get("provider_required") and not (row.get("trace") or {}).get("providers"):
        failures.append("该业务轮没有可核对的 LLM provider 记录")
    if expected.get("stock_provenance_from") is not None:
        failures.extend(audit_stock_provenance_followup(
            row, rows, int(expected["stock_provenance_from"])))

    # C16-2（N4）：**兜底闲聊说自己做了事，而这一轮一个动作都没有** ⇒ 红。
    # 真栈 family T21 原样：「好的，已经为您重新计算路线，从华侨城欢乐海岸出发，
    # 不走高速，全程大约1.6公里」——零 navigation 调用、零动作，探针 `fails=[]`。
    # 判据本体复用 `runtime/execution_claim.py`（C11-C 落的那一份，形态、零领域词）
    # ——**不许在 `scripts/` 抄第二张表**：抄两份就会长出「这边认得那边不认得」
    # 的分歧。它是**无条件**的，不挂在用例的 expect 上：漏检最常见的形态就是
    # 「新加的那一轮没人想到写这条断言」（同第 1 批那条扫全组的元断言）。
    if _CHITCHAT_INTENT in intents and not (row.get("actions") or []):
        family = execution_claim(str(row.get("speech") or ""))
        if family:
            failures.append(
                f"兜底闲聊声称系统做了事（{family} 形态）却零动作"
                f"：「{str(row.get('speech') or '')[:40]}」")
    return failures, warnings


async def _turn(ws, session: str, say: str, *, operation_id: str = "",
                trace_id: str, is_confirmation: bool = False) -> dict:
    return await probe._one_turn(
        ws, session, say, operation_id=operation_id,
        is_confirmation=is_confirmation, trace_id=trace_id,
        meta_overrides={
            "llm_provider": "minimax",
            "llm_model": "MiniMax-M3",
        })


def _trace_detail_fingerprint(detail: dict) -> str:
    turn = detail.get("turn") if isinstance(detail, dict) else {}
    turn = turn if isinstance(turn, dict) else {}
    spans = []
    for span in detail.get("spans") or []:
        if not isinstance(span, dict):
            continue
        attrs = span.get("attrs") or {}
        attrs = attrs if isinstance(attrs, dict) else {}
        spans.append((
            span.get("id"), span.get("span_id"), span.get("node"), span.get("status"),
            attrs.get("agent_id"), attrs.get("agent"), attrs.get("intent"),
            attrs.get("intents"), attrs.get("pinned"), attrs.get("provider"),
            attrs.get("model"),
        ))
    llm_calls = []
    for call in detail.get("llm_calls") or []:
        if not isinstance(call, dict):
            continue
        llm_calls.append((
            call.get("id"), call.get("provider"), call.get("model"),
            call.get("pinned"), call.get("fallback"), call.get("status"),
            call.get("error"),
        ))
    return json.dumps({
        "turn": (
            turn.get("trace_id"), turn.get("status"), turn.get("path"),
            turn.get("intents"),
        ),
        "spans": spans,
        "llm_calls": llm_calls,
    }, ensure_ascii=False, sort_keys=True, default=str)


async def _fetch_detail(collector: str, trace_id: str, *, attempts: int = 8) -> dict:
    url = f"{collector}/api/turns/{urllib.parse.quote(trace_id, safe='')}"
    detail: dict = {"error": "not found"}
    previous_ready_fingerprint = ""
    stable_ready_reads = 0
    for attempt in range(attempts):
        try:
            detail = await asyncio.to_thread(_http_json, url)
        except Exception as exc:
            detail = {"error": f"{type(exc).__name__}: {exc}"}
        if isinstance(detail, dict) and detail.get("error") != "not found":
            turn = detail.get("turn")
            if isinstance(turn, dict):
                exact = str(turn.get("trace_id") or "").strip() == trace_id
                terminal = bool(str(turn.get("status") or "").strip())
                routed = str(turn.get("path") or "").strip() in {
                    "local", "mixed", "cloud",
                }
                path = str(turn.get("path") or "").strip()
                trace_ready = True
                if path in {"cloud", "mixed"}:
                    spans = [span for span in detail.get("spans") or []
                             if isinstance(span, dict)]
                    nodes = {str(span.get("node") or "") for span in spans}
                    attrs = [span.get("attrs") or {} for span in spans]
                    has_agent = any(
                        isinstance(item, dict)
                        and (item.get("agent_id") or item.get("agent"))
                        for item in attrs)
                    has_owner = has_agent or bool(_ENGINE_ONLY_TRACE_NODES & nodes)
                    has_intent = bool(str(turn.get("intents") or "").strip()) or any(
                        isinstance(item, dict)
                        and (item.get("intent") or item.get("intents"))
                        for item in attrs)
                    trace_ready = has_owner and has_intent
                if exact and terminal and routed and trace_ready:
                    fingerprint = _trace_detail_fingerprint(detail)
                    stable_ready_reads = (
                        stable_ready_reads + 1
                        if fingerprint == previous_ready_fingerprint else 1
                    )
                    previous_ready_fingerprint = fingerprint
                    if stable_ready_reads >= 2:
                        return detail
                else:
                    previous_ready_fingerprint = ""
                    stable_ready_reads = 0
        if attempt + 1 < attempts:
            await asyncio.sleep(0.5)
    return detail if isinstance(detail, dict) else {"error": "not found"}


async def _vehicle_state(collector: str) -> dict:
    try:
        value = await asyncio.to_thread(_http_json, f"{collector}/api/vehicle/state")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


class VehicleStateRead(NamedTuple):
    """一次车态回读的结果。**「读不到」与「读到了但不对」永远分开报**（C2-D）。

    原实现两种失败都返回 `{}`，上层于是只会说一句「collector 无法回读车辆恢复终态」
    ——而真相是**回读通道一直是好的**，读回来的值就是不等于基线。逐键 diff 那段代码
    因此成了**死代码**，2026-08-26 那轮 QA 的两个端侧真 bug（后挡除雾关错对象、
    「关闭音乐」落 pause）被这句谎话整个盖住，最后写进 findings 的是
    「探针基建问题：终值未知」。

    这条判据 android-m3 那批已经沉淀过一次（「复合判据的诊断出口要拆开报」），
    它在探针上原样复发——所以这次把它写进类型里，而不是写进注释里。
    """
    #: 最后一次拿到的原始状态。`{}` = collector 一次都没给出非空状态。
    value: dict
    #: 完整 + 匹配 expected + 连续两次稳定。只有它为真才算「已收敛」。
    settled: bool
    #: 至少读到过一次非空状态 = **回读通道是通的**。
    reachable: bool
    #: 期望键里始终没读到的那些（`reachable` 为真时才有意义）。
    missing: tuple


async def _settled_vehicle_state(
    collector: str,
    *,
    attempts: int = 8,
    required_keys: set[str] | tuple[str, ...] = (),
    expected: dict | None = None,
) -> VehicleStateRead:
    """回读车态，并**如实报告失败的种类**。

    收敛判据一字未改（完整 + 匹配 + 稳定两次）；变的是没收敛时不再假装什么都没读到
    ——把最后一次读数带回去，让上层的逐键 diff 真跑起来。
    """
    required = set(required_keys) | set((expected or {}).keys())
    previous: tuple[tuple[str, object], ...] | None = None
    stable_reads = 0
    last_value: dict = {}
    last_missing: tuple = tuple(sorted(required))
    for attempt in range(attempts):
        value = await _vehicle_state(collector)
        projected = managed_vehicle_state(value, required_keys=required or None)
        if value:
            last_value = value
            last_missing = tuple(sorted(required - set(projected)))
        complete = bool(value) and required.issubset(projected)
        matches = complete and all(
            _vehicle_value_matches(projected.get(key), wanted)
            for key, wanted in (expected or {}).items()
        )
        if matches:
            snapshot = tuple(
                (key, projected[key]) for key in _MANAGED_VEHICLE_KEYS
                if key in projected
            )
            stable_reads = stable_reads + 1 if snapshot == previous else 1
            previous = snapshot
            if stable_reads >= 2:
                return VehicleStateRead(value, True, True, ())
        else:
            previous = None
            stable_reads = 0
        if attempt + 1 < attempts:
            await asyncio.sleep(0.25)
    return VehicleStateRead(last_value, False, bool(last_value), last_missing)


def _navigation_is_active(rows: list[dict]) -> bool:
    active = False
    for row in rows:
        for action in row.get("actions") or []:
            if action == "navigate":
                active = True
            elif action in {"navigate_cancel", "navigation.cancel"}:
                active = False
    return active


def _merchant_preview_created(rows: list[dict]) -> bool:
    return any(row.get("card_type") in {
        "merchant_order_preview", "merchant_checkout",
    } for row in rows)


async def _merchant_draft_cleanup_turn(
    ws,
    session: str,
    *,
    turn_no: int,
    case_instance: str,
) -> tuple[dict, dict, list[str]]:
    """Run one owner/session-scoped zero-state proof through the real bridge."""
    trace_id = uuid.uuid4().hex
    try:
        obs = await _turn(
            ws, session, "清理本次会话的订单预览", trace_id=trace_id,
        )
    except Exception as exc:
        obs = {
            "speech": f"[transport error] {type(exc).__name__}",
            "actions": [], "need_confirm": False, "card_type": "",
            "card_text": "", "is_question": False, "error": True,
            "trace_id": trace_id,
        }
    proof, failures = audit_merchant_draft_cleanup(obs.get("card_text") or "")
    if obs.get("error"):
        failures.append("商户临时预览清理 transport/backend error")
    row = {
        "turn": turn_no,
        "case": "MERCHANT-DRAFT-CLEANUP",
        "case_instance": case_instance,
        "local_turn": 0,
        "say": "清理本次会话的订单预览",
        "audit_expect": {"intent_any": ["shop.preview_discard"]},
        **obs,
        "notes": ["当前认证 user/session 精确清理"],
        "fails": failures,
    }
    proof_row = {
        "case_instance": case_instance,
        "trace_id": trace_id,
        **proof,
        "failures": list(failures),
    }
    return row, proof_row, failures


async def _reminder_cleanup_turns(
    ws,
    session: str,
    *,
    run_tags: set[str],
    turn_no: int,
) -> tuple[list[dict], list[str]]:
    """跑批结束把**自己建的提醒**扫干净（C10-D 的第二件）。

    为什么必须有：提醒的状态机 `pending→fired` 到头，**fired 永远算 ACTIVE**——
    每一轮探针留下的条目都会永远参与 `find_by_title` 与序数参照系。2026-08-26
    那一轮跑完，u1 底下沉了 90 条，直接后果是真栈 T59 的「取消第一条」取消掉了
    一条八月初的探针垃圾。存量已在 2026-08-27 清完，**入口不堵就会再脏**
    （§40 那次 `place.company` 复位已经证明过一次）。

    匹配面是**本次 run 号**（`{run}` 替换后的那 6 位）：只清自己建的，
    绝不按标题模式去猜哪些是垃圾——那会误删用户的真提醒。

    ⚠ **只观测不中止**：这一段跑在全部业务轮之后，中止已经没有任何保护价值，
    而一个新写的清理段误判成失败就会把下一趟跑批截断——那正是「修正后计分」
    那一趟最不能出的事。失败进 `cleanup_failures`，读数照样看得见。
    """
    rows: list[dict] = []
    failures: list[str] = []
    if not run_tags:
        return rows, failures

    async def _say(text: str, case: str) -> dict:
        trace_id = uuid.uuid4().hex
        try:
            obs = await _turn(ws, session, text, trace_id=trace_id)
        except Exception as exc:
            obs = {
                "speech": f"[transport error] {type(exc).__name__}",
                "actions": [], "need_confirm": False, "card_type": "",
                "card_text": "", "card_items": [], "is_question": False,
                "error": True, "trace_id": trace_id,
            }
        rows.append({
            "turn": turn_no + len(rows), "case": case, "case_instance": "",
            "local_turn": 0, "say": text, "audit_expect": {}, **obs,
            "notes": [f"提醒清理（run {sorted(run_tags)}）"],
            "fails": [],
        })
        return obs

    def _mine(obs: dict) -> list[str]:
        """列表里**属于本次 run** 的标题。认卡片 items，卡片缺失时退回话术包含。"""
        titles = [str(name) for name in (obs.get("card_items") or [])]
        mine = [t for t in titles if any(tag in t for tag in run_tags)]
        if mine or titles:
            return mine
        speech = str(obs.get("speech") or "")
        return [tag for tag in sorted(run_tags) if tag in speech]

    listed = await _say("列出我现在进行中的提醒", "REMINDER-CLEANUP-LIST")
    if listed.get("error"):
        failures.append("提醒清理 transport/backend error（列表）")
        return rows, failures
    leftovers = _mine(listed)
    for title in leftovers[:10]:
        cancelled = await _say(f"取消{title}", "REMINDER-CLEANUP-CANCEL")
        if cancelled.get("error"):
            failures.append(f"提醒清理 transport/backend error（取消 {title}）")
            return rows, failures

    if not leftovers:
        return rows, failures
    verify = await _say("列出我现在进行中的提醒", "REMINDER-CLEANUP-VERIFY")
    if verify.get("error"):
        failures.append("提醒清理 transport/backend error（复核）")
        return rows, failures
    remaining = _mine(verify)
    if remaining:
        # **计数要回落**——「发过取消指令」不等于「库里没有了」（同 Q6 那条：
        # 话术层判据验证不了它说的是不是真的）。
        failures.append(
            f"提醒清理后仍有本次 run 的条目：{remaining}")
    return rows, failures


async def _run_persona(name: str, cases: list[dict], ws_url: str,
                       collector: str, stamp: int) -> dict:
    session = f"probe-qa-long-{name}-{stamp}"
    rows: list[dict] = []
    active_ops: dict[str, int] = {}
    abort_reason = ""
    cleanup_failures: list[str] = []
    merchant_cleanup_proofs: list[dict] = []
    run_tags: set[str] = set()
    required_vehicle_keys = _required_vehicle_state_keys(cases)
    needs_vehicle = bool(required_vehicle_keys)
    baseline_read = await _settled_vehicle_state(
        collector, required_keys=required_vehicle_keys,
    ) if needs_vehicle else VehicleStateRead({}, True, True, ())
    baseline_state = managed_vehicle_state(
        baseline_read.value, required_keys=set(required_vehicle_keys),
    ) if baseline_read.value else {}
    missing_baseline_keys = [
        key for key in required_vehicle_keys if key not in baseline_state
    ]
    vehicle_cleanup = {
        "required": needs_vehicle,
        "before": baseline_state,
        "after_business": {}, "commands": [], "after_cleanup": {},
        "cleanup_session_id": "",
        "verified": not needs_vehicle, "failures": [],
    }
    if needs_vehicle and (not baseline_read.settled or missing_baseline_keys):
        # 两种失败分开说（C2-D）：通道不通 vs 通道通但基线不全。
        detail = ", ".join(missing_baseline_keys) or "无可用状态"
        failure = (
            ("collector 不可达，拒绝执行会改变车态的 persona"
             if not baseline_read.reachable else
             "collector 可达但车辆基线不完整，拒绝执行会改变车态的 persona：" + detail)
        )
        vehicle_cleanup["failures"].append(failure)
        cleanup_failures.append(failure)
        return {
            "persona": name, "session_id": session, "turn_count": 0,
            "passed": 0, "failed": 1, "aborted": True,
            "abort_reason": failure, "open_operation_ids": [], "turns": [],
            "vehicle_cleanup": vehicle_cleanup,
            "merchant_cleanup_proofs": merchant_cleanup_proofs,
            "cleanup_failures": cleanup_failures,
        }
    ws = await _connect(ws_url)
    try:
        for case_no, case in enumerate(cases, 1):
            local_rows: list[dict] = []
            source_id = case.get("source_case_id") or case["id"]
            # 一个 case 内的 `{run}` 是同一业务实体身份；逐轮换 token 会让创建、
            # 修改和取消各指向不同提醒，既测不到续接，也无法精确清理落库记录。
            case_stamp = stamp + len(rows) + 1
            # 本 case 用的 run 号：跑批结束按它清掉自己建的提醒（C10-D）。
            # **只收带 `{run}` 的 case**——那正是探针给「自己建的业务实体」
            # 打的标记；无差别收会让每个 persona 都多跑两轮清理，
            # 而多出来的轮次本身就会改变读数（同「测试替系统提供前提」那条）。
            if any("{run}" in str(turn.get("say") or "")
                   for turn in case["turns"]):
                run_tags.add(str(case_stamp)[-6:])
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
                    say = probe._subst(str(turn.get("say") or ""), case_stamp)

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
                    abort_reason = (
                        f"{source_id} T{local_turn} transport result uncertain")

                failures, notes = judge_persona_turn(
                    turn, obs, local_rows, stamp=case_stamp)
                failures = pre_failures + failures
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

                # NEED_SLOT 与 NEED_CONFIRM 都是挂起操作，二者都会下发 operation_id。
                # 旧 harness 只跟踪后者，导致订单号追问跨 case 污染下一句还没被清理。
                # 专门的 LONG-ORDER-INTERRUPT 在**同一 case 内**测试插话；case 结束后
                # 所有挂起统一按服务端 id 取消。
                _track_active_operations(active_ops, obs, row["turn"])

                flag = "✓" if not failures else "✗"
                print(f"  {flag} {name:<11} T{row['turn']:02d} {source_id:<5} "
                      f"{say[:26]:<26} → {(obs.get('speech') or '')[:42]}")

                # A lost response may have created a server-side pending operation whose
                # id never reached the harness.  Never reuse that session for a later bare
                # "确认"; stop sending frames and fail the persona closed.
                if abort_reason:
                    break

            # 商户预览/危险确认/缺槽挂起绝不遗留：用服务端 operation_id 逐条取消，
            # 且只有服务端 exact closed id 证明关闭后才允许继续后续会话。
            if not abort_reason:
                for op in list(active_ops):
                    trace_id = uuid.uuid4().hex
                    try:
                        obs = await _turn(
                            ws, session, "取消", operation_id=op, trace_id=trace_id)
                    except Exception as exc:
                        obs = {
                            "speech": f"[transport error] {type(exc).__name__}",
                            "actions": [], "need_confirm": False, "card_type": "",
                            "is_question": False, "error": True,
                            "trace_id": trace_id, "closed_operation_ids": [],
                        }
                    failures = validate_cleanup_result(op, obs)
                    closed_ids = [str(value) for value in
                                  (obs.get("closed_operation_ids") or [])]
                    row = {
                        "turn": len(rows) + 1, "case": "AUTO-CANCEL",
                        "case_instance": case["id"], "local_turn": 0,
                        "say": "取消", "audit_expect": {}, **obs,
                        "notes": [f"取消源自第 {active_ops[op]} 轮的待确认"],
                        "fails": failures,
                    }
                    rows.append(row)
                    if op in closed_ids:
                        active_ops.pop(op, None)
                    if failures:
                        abort_reason = f"cleanup not proven for {op}"
                    print(f"  {'✓' if not failures else '✗'} {name:<11} "
                          f"T{row['turn']:02d} SAFE 取消待确认")
                    if abort_reason:
                        break

            # Real merchant workflows keep an owner/session-scoped Redis
            # preview outside the orchestrator pending table.  Closing the
            # operation prevents execution; this second, explicit business
            # turn proves the transient preview itself is gone as well.
            if (not abort_reason and _merchant_preview_created(local_rows)):
                row, proof_row, failures = await _merchant_draft_cleanup_turn(
                    ws,
                    session,
                    turn_no=len(rows) + 1,
                    case_instance=case["id"],
                )
                rows.append(row)
                merchant_cleanup_proofs.append(proof_row)
                if failures:
                    abort_reason = "merchant draft cleanup not proven"
                    cleanup_failures.extend(failures)

            if abort_reason:
                break

        # A missing UI card is not proof that prepare had no Redis side effect.
        # Every merchant-bearing persona therefore ends with an unconditional
        # session-scoped zero-state proof, even when no preview card was seen.
        if name in {"merchant", "adversarial"} and not abort_reason:
            row, proof_row, failures = await _merchant_draft_cleanup_turn(
                ws,
                session,
                turn_no=len(rows) + 1,
                case_instance="",
            )
            rows.append(row)
            merchant_cleanup_proofs.append(proof_row)
            if failures:
                abort_reason = "merchant draft cleanup not proven"
                cleanup_failures.extend(failures)

        # 提醒清理段（C10-D）：跑批自己建的提醒不清掉就会沉在库里，
        # 永远参与序数参照系。**只观测不中止**，理由写在函数 docstring 里。
        if not abort_reason:
            cleanup_rows, reminder_failures = await _reminder_cleanup_turns(
                ws, session, run_tags=run_tags, turn_no=len(rows) + 1)
            rows.extend(cleanup_rows)
            cleanup_failures.extend(reminder_failures)

        # 只有会话状态确定且所有挂起都明确关闭，才继续三种恢复合同。否则继续发任何
        # 文本（尤其裸“确认”）都可能接上一个未知危险操作。
        if not abort_reason:
            for turn in recovery_turns():
                say = str(turn["say"])
                trace_id = uuid.uuid4().hex
                try:
                    obs = await _turn(ws, session, say, trace_id=trace_id)
                except Exception as exc:
                    obs = {
                        "speech": f"[transport error] {type(exc).__name__}",
                        "actions": [], "need_confirm": False, "card_type": "",
                        "is_question": False, "error": True, "trace_id": trace_id,
                    }
                failures, notes = judge_persona_turn(
                    turn, obs, [], stamp=stamp)
                if obs.get("error"):
                    failures.append("恢复轮出错")
                    abort_reason = "recovery transport result uncertain"
                rows.append({
                    "turn": len(rows) + 1, "case": "RECOVERY", "case_instance": "",
                    "local_turn": 0, "say": say,
                    "audit_expect": dict(turn.get("audit") or {}), **obs,
                    "notes": ["失败/取消后的连续恢复轮", *notes], "fails": failures,
                })
                if abort_reason:
                    break

        if not abort_reason and _navigation_is_active(rows):
            trace_id = uuid.uuid4().hex
            try:
                obs = await _turn(ws, session, "取消导航", trace_id=trace_id)
            except Exception as exc:
                obs = {
                    "speech": f"[transport error] {type(exc).__name__}",
                    "actions": [], "need_confirm": False, "card_type": "",
                    "is_question": False, "error": True, "trace_id": trace_id,
                }
            failures, notes = judge_persona_turn(
                {"expect": {"actions_include": ["navigate_cancel"]}},
                obs, [], stamp=stamp)
            if obs.get("error"):
                failures.append("导航清理 transport/backend error")
            rows.append({
                "turn": len(rows) + 1, "case": "NAVIGATION-CLEANUP",
                "case_instance": "", "local_turn": 0, "say": "取消导航",
                "audit_expect": {"intent_any": ["navigation.cancel"]},
                **obs, "notes": ["恢复 persona 前的导航基线", *notes],
                "fails": failures,
            })
            if failures:
                abort_reason = "navigation cleanup not proven"
                cleanup_failures.extend(failures)

        if needs_vehicle:
            business_actions = [
                str(action)
                for row in rows
                for action in row.get("actions") or []
            ]
            after_business_read = await _settled_vehicle_state(
                collector,
                required_keys=required_vehicle_keys,
                expected=_expected_vehicle_state(business_actions),
            )
            vehicle_cleanup["after_business"] = managed_vehicle_state(
                after_business_read.value,
                required_keys=set(required_vehicle_keys),
            ) if after_business_read.value else {}
            # ⚠ 这一读的 `expected` 只是**加速收敛的目标**，不是判据：业务轮做了什么
            # 本来就允许与预期不同（那正是探针要观测的）。所以门槛是 `reachable`
            # 而不是 `settled`——读到了就往下走，读不到才是真的没法恢复。
            if not after_business_read.reachable:
                failure = "collector 不可达，无法回读业务后的车辆状态"
                vehicle_cleanup["failures"].append(failure)
                cleanup_failures.append(failure)
            else:
                restore_ws = ws
                restore_session = session
                cleanup_ws = None
                if abort_reason:
                    # The uncertain business session must never receive another
                    # utterance (especially a bare confirmation). Vehicle restore
                    # is deterministic and non-dangerous, so move it to a fresh
                    # session after closing the original transport.
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    restore_session = f"{session}-cleanup"
                    vehicle_cleanup["cleanup_session_id"] = restore_session
                    try:
                        cleanup_ws = await _connect(ws_url)
                        restore_ws = cleanup_ws
                    except Exception as exc:
                        restore_ws = None
                        failure = (
                            "车辆恢复专用会话无法建立："
                            f"{type(exc).__name__}"
                        )
                        vehicle_cleanup["failures"].append(failure)
                        cleanup_failures.append(failure)
                try:
                    commands = vehicle_restore_commands(
                        baseline_state, vehicle_cleanup["after_business"])
                except ValueError as exc:
                    commands = []
                    vehicle_cleanup["failures"].append(str(exc))
                    cleanup_failures.append(str(exc))
                vehicle_cleanup["commands"] = [
                    {"state_key": key, "say": say} for key, say in commands]
                for key, say in (commands if restore_ws is not None else []):
                    trace_id = uuid.uuid4().hex
                    try:
                        obs = await _turn(
                            restore_ws, restore_session, say, trace_id=trace_id)
                    except Exception as exc:
                        obs = {
                            "speech": f"[transport error] {type(exc).__name__}",
                            "actions": [], "need_confirm": False, "card_type": "",
                            "is_question": False, "error": True,
                            "trace_id": trace_id,
                        }
                    failures, notes = judge_persona_turn(
                        {"expect": {"need_confirm": False}}, obs, [], stamp=stamp)
                    if obs.get("error"):
                        failures.append(f"恢复 {key} transport/backend error")
                    else:
                        # N7：动作与目标状态键的一致性。`need_confirm=False` 证明不了
                        # 这一句动对了东西——恢复链本身就是一次免费的对抗测试，
                        # 它的失败要**逐键报因**（C2-D 同一条判据的另一半）。
                        failures.extend(judge_restore_turn(
                            key, baseline_state.get(key), obs.get("actions")))
                    rows.append({
                        "turn": len(rows) + 1, "case": "VEHICLE-RESTORE",
                        "case_instance": "", "local_turn": 0, "say": say,
                        "audit_expect": {}, **obs,
                        "notes": [
                            f"恢复车态键 {key}",
                            f"恢复会话 {restore_session}",
                            *notes,
                        ],
                        "fails": failures,
                    })
                    if failures:
                        cleanup_failures.extend(failures)
                        abort_reason = f"vehicle restore uncertain for {key}"
                        break

                final_read = await _settled_vehicle_state(
                    collector,
                    required_keys=required_vehicle_keys,
                    expected=baseline_state,
                ) if restore_ws is not None else VehicleStateRead({}, False, False, ())
                final_state = managed_vehicle_state(
                    final_read.value,
                    required_keys=set(required_vehicle_keys),
                ) if final_read.value else {}
                vehicle_cleanup["after_cleanup"] = final_state
                expected_state = baseline_state
                # **「读不到」与「读到了但不对」分开报**（C2-D / C16-8）。
                # 原实现把两者都塞成一句「collector 无法回读车辆恢复终态」，
                # 于是下面这段逐键 diff 从来没有执行过——它是死代码，
                # 而两个端侧真 bug（rear_defogger 关错对象、media 落 pause）
                # 恰恰只有它报得出来。
                if not final_read.reachable:
                    vehicle_cleanup["failures"].append(
                        "collector 不可达，无法回读车辆恢复终态")
                elif not final_state:
                    vehicle_cleanup["failures"].append(
                        "collector 可达但恢复终态缺少全部权威键："
                        + (", ".join(final_read.missing) or "未知"))
                else:
                    for key in final_read.missing:
                        vehicle_cleanup["failures"].append(
                            f"车辆恢复终态缺键 {key}（collector 没有给出这一维）")
                    for key, expected in expected_state.items():
                        if key in final_read.missing:
                            continue
                        if final_state.get(key) != expected:
                            vehicle_cleanup["failures"].append(
                                f"车辆基线未恢复 {key}: "
                                f"actual={final_state.get(key)!r} expected={expected!r}")
                vehicle_cleanup["verified"] = not vehicle_cleanup["failures"]
                cleanup_failures.extend(
                    failure for failure in vehicle_cleanup["failures"]
                    if failure not in cleanup_failures)
                if cleanup_ws is not None:
                    try:
                        await cleanup_ws.close()
                    except Exception:
                        pass
    finally:
        await ws.close()

    # Collector 可能比 final 晚几十毫秒落库；逐 trace 有界重试后再做路由/Agent/provider 对账。
    for row in rows:
        detail = await _fetch_detail(collector, row["trace_id"])
        expected = row.pop("audit_expect", {})
        audit, failures = audit_trace_detail(
            detail, row["trace_id"],
            allow_cancelled=bool(expected.get("allow_cancelled")))
        row["trace"] = audit
        row["fails"].extend(failures)
        row_failures, row_warnings = audit_row_expectations(row, rows, expected)
        row["fails"].extend(row_failures)
        row.setdefault("warns", []).extend(row_warnings)

    return {
        "persona": name, "session_id": session, "turn_count": len(rows),
        "passed": sum(not row["fails"] for row in rows),
        "failed": sum(bool(row["fails"]) for row in rows),
        # C15：**如实标注的 mock 记 WARN 不判 fail**（泓舟 2026-08-27 裁决）。
        # 单开一列而不是塞进 notes：它要能被读数一眼看见，又绝不能进 `failed`
        # ——「真栈不该有 mock」是部署形态期望，「有 mock 必须承认」是诚实契约，
        # 两件事合成一个数就又回到了裁决之前那种自相矛盾。
        "warned": sum(bool(row.get("warns")) for row in rows),
        "aborted": bool(abort_reason),
        "abort_reason": abort_reason,
        "open_operation_ids": sorted(active_ops),
        "vehicle_cleanup": vehicle_cleanup,
        "merchant_cleanup_proofs": merchant_cleanup_proofs,
        "cleanup_failures": list(dict.fromkeys(cleanup_failures)),
        "turns": rows,
    }


# ── 回放计分（C16 验收，2026-08-28）──────────────────────────────────────
#
# 「修正后计分」= **拿今天的尺子重量当天的那一趟**。它不打网络、不发一句话，
# 只做一件事：把存档里逐轮的观测（speech / actions / card / trace）喂给**现在
# 这一份判据**，再与存档里当时记下的 `fails` 逐行对比。
#
# 为什么必须有它：探针在第 1–6 批里被改过五次，而真栈迷你集一次都没重跑。
# 没有回放就只能说「判据写好了」，说不出「它们会把当天那 33 行变成多少行」
# ——**新判据的价值全在「当时绿的哪几行会变红」上**，而那正是回放能算出来的
# 唯一一个数（§4.3「加了知识要拿对照跑证伪」的同款：判据也一样）。
#
# 回放的边界写在这里，别让读数越界：
#   · **runner 自造的轮次不可完全回放**（AUTO-CANCEL / VEHICLE-RESTORE /
#     NAVIGATION-CLEANUP / MERCHANT-DRAFT-CLEANUP / REMINDER-CLEANUP*）——
#     它们的判据要的是运行期状态（服务端 closed id、collector 车态、清理前后
#     计数），存档里没有。这些行**只跑无条件规则**，`replayable=False` 标出来。
#   · trace 审计（`audit_trace_detail`）也不可回放：存档存的是它的**结论**
#     不是 collector 原始详情。落域（`intent_any`）用的是结论里的 `intents`，
#     那一份是可回放的。
#   · 因此 `newly_green` 里可能混着「当时那条红来自不可回放的判据」——**逐行
#     打出来给人看**，不做自动归因。

def _replay_stamp(template: str, said: str) -> int | None:
    """从存档里那句话反解出当时的 `{run}` 标记 → 可喂给 `probe._subst` 的 stamp。

    `_subst` 用的是 `str(stamp)[-6:]`，所以反解出 6 位 tag 之后随便拼一个
    前缀就能还原（`int("1" + tag)`）。反解不出来返回 None——**宁可标成
    「这一行没法回放」，也不许拿一个错的标记去比对**：`{run}` 对不上会让
    `speech_has` 整片假红，那种读数比没有读数更糟。
    """
    if "{run}" not in template:
        return 0
    parts = [re.escape(part) for part in template.split("{run}")]
    match = re.fullmatch(r"(\d{6})".join(parts), said or "")
    if not match:
        return None
    return int("1" + match.group(1))


def _replay_index(plans: dict[str, list[dict]]) -> dict[tuple[str, int], dict]:
    """`(case_instance, local_turn) -> turn spec`（当前用例集的那一份）。"""
    index: dict[tuple[str, int], dict] = {}
    for cases in plans.values():
        for case in cases:
            for local_turn, turn in enumerate(case["turns"], 1):
                index[(case["id"], local_turn)] = turn
    return index


def _replay_case_stamps(rows: list[dict],
                        index: dict[tuple[str, int], dict]) -> dict[str, int | None]:
    """每个 case 实例的 `{run}` 标记 → `probe._subst` 能吃的 stamp。

    None = 这个 case 带了 `{run}` 却反解不出来 ⇒ 整个 case 按不可回放处理。
    **宁可标成不可回放，也不许拿一个错的标记去比对**：`{run}` 对不上会让
    `speech_has` 整片假红，那种读数比没有读数更糟。
    """
    stamps: dict[str, int | None] = {}
    for row in rows:
        instance = str(row.get("case_instance") or "")
        local_turn = int(row.get("local_turn") or 0)
        if not instance or local_turn < 1 or stamps.get(instance):
            continue
        spec = index.get((instance, local_turn))
        template = str((spec or {}).get("say") or "")
        if "{run}" not in template:
            stamps.setdefault(instance, 0)
            continue
        stamps[instance] = _replay_stamp(template, str(row.get("say") or ""))
    return stamps


def replay_scoring(payload: dict) -> dict:
    """用当前判据重算存档里每一轮 → 修正后计分（纯函数，零网络）。"""
    plans = build_persona_plans()
    index = _replay_index(plans)
    recovery = list(recovery_turns())
    personas: list[dict] = []
    for result in payload.get("personas") or []:
        rows = [dict(row) for row in result.get("turns") or []]
        # `{run}` 是**一个 case 一个**（`case_stamp = stamp + len(rows) + 1`），
        # 而带 `{run}` 的往往只有那个 case 的第一轮。所以先扫一遍把每个 case
        # 的标记反解出来，再逐轮回放——**按轮反解会让「取消第一条」这种不带
        # 标记的轮拿到 0**，`speech_has` 立刻整片假红（首版实测 4 行）。
        stamps = _replay_case_stamps(rows, index)
        replayed: list[dict] = []
        local_rows: dict[str, list[dict]] = {}
        recovery_seen = 0
        for row in rows:
            case = str(row.get("case") or "")
            instance = str(row.get("case_instance") or "")
            local_turn = int(row.get("local_turn") or 0)
            spec: dict | None = None
            if case == "RECOVERY":
                spec = (recovery[recovery_seen]
                        if recovery_seen < len(recovery) else None)
                recovery_seen += 1
            elif local_turn >= 1:
                spec = index.get((instance, local_turn))
                # `say_button` 轮的那句话是**上一轮卡片给的**，缺按钮时 runner
                # 会记一条「未编造 send_text」的前置失败——存档里没有按钮就
                # 重算不出它。这类轮按不可回放处理（存档判读原样带过来）。
                if spec is not None and spec.get("say_button") is not None:
                    spec = None
            stamp = stamps.get(instance, 0) if instance else 0
            if spec is not None and stamp is None:
                spec = None
            replayable = spec is not None
            failures: list[str] = []
            notes: list[str] = []
            audit_expect: dict = {}
            if spec is not None:
                failures, notes = judge_persona_turn(
                    spec, row, local_rows.get(instance, []), stamp=stamp or 0)
                audit_expect = probe._subst(dict(spec.get("audit") or {}),
                                            stamp or 0)
            audit_failures, warns = audit_row_expectations(
                dict(row), rows, audit_expect)
            failures = failures + audit_failures
            stored = [str(f) for f in (row.get("fails") or [])]
            if not replayable:
                # **不可回放 ≠ 通过**：重算不出来的那部分原样保留存档的判读，
                # 否则「回放红数」会因为尺子够不着而凭空变小（同 §4.3
                # 「前提不成立 ≠ 通过」那条，这次是尺子这一侧）。
                failures = stored + [f for f in failures if f not in stored]
            local_rows.setdefault(instance, []).append(
                {**row, "turn": local_turn})
            replayed.append({
                "turn": row.get("turn"), "case": case,
                "case_instance": instance, "local_turn": local_turn,
                "say": row.get("say"), "speech": row.get("speech"),
                "replayable": replayable,
                "stored_fails": stored, "replay_fails": failures,
                "warns": warns, "notes": notes,
            })
        personas.append({
            "persona": result.get("persona"),
            "turn_count": len(replayed),
            "stored_failed": sum(bool(r["stored_fails"]) for r in replayed),
            "replay_failed": sum(bool(r["replay_fails"]) for r in replayed),
            "warned": sum(bool(r["warns"]) for r in replayed),
            "not_replayable": sum(not r["replayable"] for r in replayed),
            "newly_red": [r for r in replayed
                          if r["replay_fails"] and not r["stored_fails"]],
            "newly_green": [r for r in replayed
                            if r["stored_fails"] and not r["replay_fails"]],
            "turns": replayed,
        })
    total = [row for result in personas for row in result["turns"]]
    return {
        "source_ts": payload.get("ts"),
        "source_sha": payload.get("expected_sha"),
        "summary": {
            "turns": len(total),
            "stored_failed": sum(bool(r["stored_fails"]) for r in total),
            "replay_failed": sum(bool(r["replay_fails"]) for r in total),
            "warned": sum(bool(r["warns"]) for r in total),
            "not_replayable": sum(not r["replayable"] for r in total),
            "newly_red": sum(bool(r["replay_fails"]) and not r["stored_fails"]
                             for r in total),
            "newly_green": sum(bool(r["stored_fails"]) and not r["replay_fails"]
                               for r in total),
        },
        "personas": personas,
    }


def _print_replay(report: dict) -> None:
    print("\n=== 修正后计分（回放，零网络）===")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    for result in report["personas"]:
        print(f"\n--- {result['persona']}: "
              f"存档 {result['stored_failed']} 红 → 回放 {result['replay_failed']} 红"
              f"（WARN {result['warned']}，不可回放 {result['not_replayable']}）")
        for row in result["newly_red"]:
            print(f"  ↑ 转红 T{row['turn']:02d} {row['case']} :: {row['say'][:34]}")
            for failure in row["replay_fails"]:
                print(f"      · {failure}")
        for row in result["newly_green"]:
            print(f"  ↓ 转绿 T{row['turn']:02d} {row['case']} :: {row['say'][:34]}")
            for failure in row["stored_fails"]:
                print(f"      · 原判：{failure}")


def _summary(results: list[dict]) -> dict:
    paths: Counter[str] = Counter()
    intents: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    agents: Counter[str] = Counter()
    cards: Counter[str] = Counter()
    actions: Counter[str] = Counter()
    external_providers: Counter[str] = Counter()
    provenance_modes: Counter[str] = Counter()
    provenance_cards: Counter[str] = Counter()
    warnings = 0
    fallbacks = 0
    pinned_calls = 0
    for result in results:
        for row in result["turns"]:
            trace = row.get("trace") or {}
            paths[trace.get("path") or "unknown"] += 1
            intents.update(trace.get("intents") or [])
            providers.update(trace.get("providers") or [])
            agents.update(trace.get("agents") or [])
            fallbacks += int(trace.get("fallbacks") or 0)
            pinned_calls += int(trace.get("pinned_calls") or 0)
            if row.get("card_type"):
                cards[row["card_type"]] += 1
            actions.update(row.get("actions") or [])
            warnings += len(row.get("warns") or [])
            for provenance in row.get("provenance") or []:
                external_providers[provenance.get("provider") or "unknown"] += 1
                provenance_modes[provenance.get("mode") or "unknown"] += 1
                provenance_cards[
                    f"{provenance.get('card_type') or '未命名卡'}"
                    f":{provenance.get('mode') or 'unknown'}"] += 1
    return {
        "turns": sum(r["turn_count"] for r in results),
        "passed": sum(r["passed"] for r in results),
        "failed": sum(r["failed"] for r in results),
        "aborted_personas": sum(bool(r.get("aborted")) for r in results),
        "warned": sum(r.get("warned") or 0 for r in results),
        "cleanup_failures": sum(
            len(r.get("cleanup_failures") or []) for r in results),
        "paths": dict(paths), "intents": dict(intents),
        "providers": dict(providers), "agents": dict(agents),
        "cards": dict(cards), "actions": dict(actions),
        "external_providers": dict(external_providers),
        "provenance_modes": dict(provenance_modes),
        # 卡型×mode 的分布：C15 的期望表按卡型判，读数也得按卡型看
        # ——只看 mode 分布会把「manual 卡的 mock」与「天气卡的 mock」并成一个数。
        "provenance_cards": dict(provenance_cards),
        "warnings": warnings,
        "fallbacks": fallbacks, "pinned_llm_calls": pinned_calls,
    }


def _partial_path(out: Path) -> Path:
    """整趟产物旁边的「已完成 persona」增量档。"""
    return out.with_name(out.stem + ".partial" + out.suffix)


def _write_partial(out: Path, results: list[dict]) -> None:
    """**每跑完一个 persona 就落一次盘。**

    2026-08-28 实证：一趟长会话跑到最后一个 persona 时被外部中止，而产物只在
    **全部 persona 结束之后**才写——四个已经跑完的 persona 的 trace / 卡片明细
    **全部丢失**，只剩控制台的 ✓/✗。一趟四十分钟的跑批把它的产物押在
    「跑完不出事」上，本身就是个设计缺陷。

    判据：**长时任务的中间产物要在产生的时候就落地，不是在结束的时候。**

    刻意写成**旁边的另一个文件**而不是覆盖 `--out`：整趟成功时 `--out` 才是那份
    带 release/TTS/summary 的完整证据，`.partial` 只是「中途没了也还剩点什么」，
    两者不该互相冒充。写盘失败**绝不打断跑批**——它是保险不是主线。
    """
    try:
        target = _partial_path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "note": "逐 persona 增量档：整趟若被中止，这里是已完成 persona 的明细；"
                    "**它没有 release / TTS / summary 对账**，不能当完整证据用",
            "ts": int(time.time()),
            "completed_personas": [r.get("persona") for r in results],
            "personas": results,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:          # 保险失效不该拖垮被保的东西
        print(f"（增量档写盘失败，不影响跑批：{type(exc).__name__}）", file=sys.stderr)


async def _run(selected: list[str], ws_url: str, collector: str,
               out: Path | None = None) -> list[dict]:
    plans = build_persona_plans()
    stamp = int(time.time())
    results = []
    for offset, name in enumerate(selected):
        print(f"\n=== 长会话 persona: {name} ===")
        results.append(await _run_persona(
            name, plans[name], ws_url, collector, stamp + offset))
        if out is not None:
            _write_partial(out, results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--persona", default="",
                        help="只跑指定 persona，逗号分隔")
    parser.add_argument("--out", default=str(
        _ROOT / ".artifacts" / "dev-stack-verifications" /
        "qa-minimax-long-sessions.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--replay", default="",
        help="拿当前判据重算一份已有 artifact（修正后计分）——纯本地、零网络")
    parser.add_argument(
        "--replay-out", default="",
        help="回放报告落盘路径（缺省只打印）")
    parser.add_argument(
        "--expected-sha", default="",
        help="必须与云端正在运行的完整 40 位 release SHA 一致；默认取当前 HEAD")
    args = parser.parse_args()

    # 回放**排在真栈前置校验之前**：它不发一个字节，不该要求 `target=cloud`、
    # 也不该被 release 校验挡住。这条顺序是它能在离线机器上跑的全部理由。
    if args.replay:
        source = Path(args.replay)
        report = replay_scoring(json.loads(source.read_text(encoding="utf-8")))
        _print_replay(report)
        if args.replay_out:
            out = Path(args.replay_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            print(f"\n回放明细：{out}")
        return 0

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

    expected_sha = str(args.expected_sha or _current_head_sha()).strip().lower()
    release_start = cloud_release_snapshot(expected_sha)
    if release_start["failures"]:
        payload = {
            "ts": int(time.time()), "target": release_start.get("target"),
            "expected_sha": expected_sha,
            "release": {"start": release_start, "end": None},
            "llm_lock": {"provider": "minimax", "model": "MiniMax-M3"},
            "tts_lock": {"provider": "minimax"},
            "tts": {"samples": {}, "barge_in": {},
                    "failures": ["release 校验失败，未执行 TTS"]},
            "summary": {"turns": 0, "passed": 0, "failed": 1},
            "open_operations": {}, "personas": [],
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(release_start, ensure_ascii=False, indent=2), file=sys.stderr)
        print(f"云端 release 校验失败，未发送业务轮；明细：{out}", file=sys.stderr)
        return 1

    ws_url, collector, audio_url, target = _resolve_endpoints()
    print(
        f"真栈目标：{target}；release={release_start['release_sha']}；"
        f"persona={','.join(selected)}；LLM 锁=minimax:MiniMax-M3")
    out = Path(args.out)
    results = asyncio.run(_run(selected, ws_url, collector, out))
    print("\n=== 每 persona 真实业务回复的 MiniMax 云端 TTS 取证 ===")
    business_samples = select_tts_business_samples(results)
    tts_samples: dict[str, dict] = {}
    tts_failures: list[str] = []
    for persona in selected:
        sample = business_samples.get(persona)
        if not sample:
            tts_failures.append(f"{persona} 没有可用于 TTS 的成功业务回复")
            continue
        source = {key: value for key, value in sample.items() if key != "text"}
        audit = asyncio.run(audit_minimax_tts(
            audio_url, sample["text"], source=source))
        tts_samples[persona] = audit
        tts_failures.extend(
            f"{persona}: {failure}" for failure in audit.get("failures") or [])
        print(json.dumps({
            "persona": persona,
            **{key: value for key, value in audit.items()
               if key not in {"error"}},
        }, ensure_ascii=False, indent=2))

    barge_source = max(
        business_samples.values(),
        key=lambda item: len(str(item.get("text") or "")),
        default=None,
    )
    if barge_source:
        barge_in = asyncio.run(audit_minimax_tts_barge_in(
            audio_url, str(barge_source["text"])))
        barge_in["source"] = {
            key: value for key, value in barge_source.items() if key != "text"}
        barge_in["text_sha256"] = hashlib.sha256(
            str(barge_source["text"]).encode("utf-8")).hexdigest()
        tts_failures.extend(
            f"barge-in: {failure}" for failure in barge_in.get("failures") or [])
    else:
        barge_in = {"failures": ["没有真实业务回复可用于 TTS 打断"]}
        tts_failures.extend(barge_in["failures"])
    tts = {
        "samples": tts_samples, "barge_in": barge_in,
        "covered_personas": sorted(tts_samples),
        "failures": list(dict.fromkeys(tts_failures)),
    }
    release_end = cloud_release_snapshot(expected_sha)
    release_failures = validate_release_continuity(
        release_start, release_end, expected_sha,
    )
    summary = _summary(results)
    open_operations = {
        result["persona"]: result["open_operation_ids"] for result in results
        if result.get("open_operation_ids")
    }
    payload = {
        "ts": int(time.time()), "target": target,
        "expected_sha": expected_sha,
        "release": {"start": release_start, "end": release_end,
                    "failures": release_failures},
        "llm_lock": {"provider": "minimax", "model": "MiniMax-M3"},
        "tts_lock": {"provider": "minimax"}, "tts": tts,
        "summary": summary, "open_operations": open_operations,
        "personas": results,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # 整趟成功 ⇒ 增量档功成身退，别留一份半截的在旁边误导下一个人。
    try:
        _partial_path(out).unlink(missing_ok=True)
    except OSError:
        pass

    print("\n=== 长会话汇总 ===")
    for result in results:
        print(f"{result['persona']:<12} {result['passed']}/{result['turn_count']} PASS")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if release_failures:
        print(json.dumps(
            {"release_continuity_failures": release_failures},
            ensure_ascii=False, indent=2), file=sys.stderr)
    print(f"明细：{out}")
    cleanup_failures = {
        result["persona"]: result.get("cleanup_failures") or []
        for result in results if result.get("cleanup_failures")
    }
    return 1 if (
        summary["failed"] or summary["aborted_personas"]
        or tts["failures"] or release_failures
        or open_operations or cleanup_failures
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
