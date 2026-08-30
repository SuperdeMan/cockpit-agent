from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from scripts import probe_qa_long_sessions as long_qa
from scripts.dev_stack_lib import (
    EndpointStatus,
    StackStatus,
    stack_status_to_dict,
)


def _real_cloud_status_payload(sha: str, *, healthy: int = 5) -> dict:
    endpoints = tuple(
        EndpointStatus(
            name=f"endpoint-{index}",
            url=f"https://endpoint-{index}.example.invalid/health",
            status="healthy" if index <= healthy else "timeout",
            http_status=200 if index <= healthy else None,
        )
        for index in range(1, 6)
    )
    return {
        **stack_status_to_dict(StackStatus(
            target="cloud",
            release_sha=sha,
            container_total=None,
            container_running=None,
            healthy_endpoints=healthy,
            endpoint_results=endpoints,
            warnings=(),
        )),
        # ``scripts.dev_stack`` adds the CLI result status around the shared
        # redacted serializer. Keep the test payload identical to that path.
        "status": "ok" if healthy == 5 else "degraded",
    }


def _complete_vehicle_state_payload():
    return {
        "hvac_on": False,
        "front_defogger": False,
        "rear_defogger": False,
        "window": "closed",
        "sunroof": "closed",
        "rear_view_mirror": "unfolded",
        "steering_wheel_heating": False,
        "volume_muted": False,
        "warning_light": False,
        "media": "stopped",
    }


def _settled(payload: dict) -> long_qa.VehicleStateRead:
    """已收敛的一次回读。2026-08-27 起 `_settled_vehicle_state` 返回的是
    `VehicleStateRead`（两种失败分开报，C2-D），替身也要跟着换形状——
    **替身的形状与被测函数不一致时，测的就不是那条路径了**。"""
    return long_qa.VehicleStateRead(payload, True, True, ())


async def _complete_vehicle_state(_collector, **_kwargs):
    return _settled(_complete_vehicle_state_payload())


def test_five_personas_each_have_a_continuous_50_to_100_turn_plan():
    plans = long_qa.build_persona_plans()

    assert set(plans) == {
        "vehicle", "family", "merchant", "information", "adversarial",
    }
    for name, cases in plans.items():
        turns = sum(len(case["turns"]) for case in cases)
        assert 50 <= turns <= 100, (name, turns)
        assert all(int(turn.get("sid", 0)) == 0
                   for case in cases for turn in case["turns"]), name

    assert all((case.get("source_case_id") or case["id"]) != "CD3"
               for cases in plans.values() for case in cases)
    for name in ("merchant", "adversarial"):
        assert any(case["id"] == "LONG-ORDER-INTERRUPT"
                   for case in plans[name])


def test_every_persona_requires_the_complete_restorable_vehicle_baseline():
    plans = long_qa.build_persona_plans()

    for name, cases in plans.items():
        assert set(long_qa._required_vehicle_state_keys(cases)) == set(
            long_qa._MANAGED_VEHICLE_KEYS
        ), name


def test_long_session_plan_never_authorizes_dangerous_or_merchant_writes():
    plans = long_qa.build_persona_plans()
    forbidden = ("付款", "支付", "确认下单", "确认开门", "确认解锁")

    for cases in plans.values():
        for case in cases:
            for turn in case["turns"]:
                assert not turn.get("confirm")
                text = str(turn.get("say") or "")
                assert not any(word in text for word in forbidden), text


def test_trip_planning_turns_explicitly_expect_the_service_confirmation_gate():
    plans = long_qa.build_persona_plans()
    trip = next(case for case in plans["information"] if case["id"] == "INF-TRIP")

    assert trip["turns"][0]["expect"]["need_confirm"] is True
    assert trip["turns"][3]["expect"]["need_confirm"] is True


def test_reminder_case_uses_a_run_scoped_title_and_cleans_up_its_record():
    plans = long_qa.build_persona_plans()
    case = next(case for case in plans["information"]
                if case["id"] == "INF-REMINDER")
    says = [turn["say"] for turn in case["turns"]]

    assert "{run}" in says[0]
    assert any("取消" in say and "{run}" in say for say in says)
    assert case["turns"][-1]["expect"]["speech_not"] == ["交周报QA{run}"]


def test_family_batch_reminders_keep_same_item_two_time_semantics_and_clean_up():
    plans = long_qa.build_persona_plans()
    cases = [case for case in plans["family"]
             if case.get("source_case_id") == "SL1"]

    assert len(cases) == 2
    for case in cases:
        says = [turn["say"] for turn in case["turns"]]
        assert says[0] == (
            "明天下午四点提醒我参加代号{run}的评审会，三点半再提醒我一次"
        )
        assert "评审材料" not in says[0]
        assert says[1:4] == [
            "取消参加代号{run}的评审会",
            "取消第一条",
            "取消参加代号{run}的评审会",
        ]
        assert case["turns"][-1]["expect"]["speech_not"] == [
            "代号{run}的评审会",
        ]


def test_generic_history_order_case_requires_the_real_merchant_card_shape():
    plans = long_qa.build_persona_plans()
    xs7 = next(case for case in plans["merchant"]
               if (case.get("source_case_id") or case["id"]) == "XS7")

    assert xs7["turns"][0]["expect"]["card_type"] == "mcp_order"


def test_active_operations_are_only_removed_by_server_closed_ids():
    active: dict[str, int] = {}
    long_qa._track_active_operations(
        active, {"operation_id": "op-1", "closed_operation_ids": []}, 7)
    assert active == {"op-1": 7}

    # 用户话术里出现“取消”不是服务端关闭证据；没有 closed id 时仍须保留并精确清理。
    long_qa._track_active_operations(
        active, {"operation_id": "", "closed_operation_ids": []}, 8)
    assert active == {"op-1": 7}

    long_qa._track_active_operations(
        active, {"operation_id": "", "closed_operation_ids": ["op-1"]}, 9)
    assert active == {}


def test_cleanup_requires_exact_server_closure_evidence():
    good = {
        "actions": [], "need_confirm": False, "closed_operation_ids": ["op-1"],
    }
    assert long_qa.validate_cleanup_result("op-1", good) == []

    failures = long_qa.validate_cleanup_result("op-1", {
        "actions": [], "need_confirm": False, "closed_operation_ids": [],
    })
    assert failures == ["服务端未证明待确认 op-1 已关闭（closed=空）"]

    failures = long_qa.validate_cleanup_result("op-1", {
        "actions": [], "need_confirm": False, "closed_operation_ids": [],
        "error": True,
    })
    assert "取消轮 transport/backend error" in failures


def test_persona_aborts_before_bare_confirm_when_cleanup_is_unproven(monkeypatch):
    sent: list[str] = []

    class _Ws:
        async def close(self):
            return None

    async def connect(_url):
        return _Ws()

    async def turn(_ws, _session, say, *, operation_id="", trace_id,
                   is_confirmation=False):
        sent.append(say)
        if say == "触发危险确认":
            return {
                "speech": "需要确认", "actions": [], "need_confirm": True,
                "operation_id": "op-1", "closed_operation_ids": [],
                "card_type": "", "is_question": True, "trace_id": trace_id,
            }
        assert say == "取消"
        return {
            "speech": "已取消", "actions": [], "need_confirm": False,
            "operation_id": "", "closed_operation_ids": [],
            "card_type": "", "is_question": False, "trace_id": trace_id,
        }

    async def detail(_collector, trace_id):
        return {
            "turn": {"trace_id": trace_id, "status": "ok", "path": "cloud"},
            "spans": [], "llm_calls": [], "logs": [],
        }

    monkeypatch.setattr(long_qa, "_connect", connect)
    monkeypatch.setattr(long_qa, "_turn", turn)
    monkeypatch.setattr(long_qa, "_fetch_detail", detail)
    monkeypatch.setattr(
        long_qa, "_settled_vehicle_state", _complete_vehicle_state,
    )
    cases = [
        {"id": "CF1", "turns": [{
            "say": "触发危险确认", "expect": {"need_confirm": True},
        }]},
        {"id": "CF4", "turns": [{"say": "确认", "expect": {}}]},
    ]

    result = asyncio.run(long_qa._run_persona(
        "safety", cases, "wss://example.invalid", "https://collector.invalid", 1))

    assert sent == ["触发危险确认", "取消"]
    assert result["aborted"] is True
    assert result["open_operation_ids"] == ["op-1"]
    assert all(row["case"] != "RECOVERY" for row in result["turns"])


def test_persona_aborts_before_bare_confirm_after_transport_uncertainty(monkeypatch):
    sent: list[str] = []

    class _Ws:
        async def close(self):
            return None

    async def connect(_url):
        return _Ws()

    async def turn(_ws, _session, say, *, operation_id="", trace_id,
                   is_confirmation=False):
        sent.append(say)
        raise TimeoutError("response lost")

    async def detail(_collector, trace_id):
        return {"error": "not found"}

    monkeypatch.setattr(long_qa, "_connect", connect)
    monkeypatch.setattr(long_qa, "_turn", turn)
    monkeypatch.setattr(long_qa, "_fetch_detail", detail)
    monkeypatch.setattr(
        long_qa, "_settled_vehicle_state", _complete_vehicle_state,
    )
    cases = [
        {"id": "CF1", "turns": [{"say": "触发危险确认", "expect": {}}]},
        {"id": "CF4", "turns": [{"say": "确认", "expect": {}}]},
    ]

    result = asyncio.run(long_qa._run_persona(
        "transport", cases, "wss://example.invalid", "https://collector.invalid", 1))

    assert sent == ["触发危险确认"]
    assert result["aborted"] is True
    assert all(row["case"] != "RECOVERY" for row in result["turns"])


def test_transport_abort_restores_known_vehicle_delta_in_a_fresh_session(monkeypatch):
    sent: list[tuple[str, str]] = []
    original_closed = False

    class _Ws:
        def __init__(self, name):
            self.name = name

        async def close(self):
            nonlocal original_closed
            if self.name == "original":
                original_closed = True

    connections = iter([_Ws("original"), _Ws("cleanup")])

    async def connect(_url):
        return next(connections)

    states = iter([
        _complete_vehicle_state_payload(),
        {**_complete_vehicle_state_payload(), "hvac_on": True},
        {**_complete_vehicle_state_payload(), "hvac_on": False},
    ])

    async def settled(_collector, **_kwargs):
        return _settled(next(states))

    async def turn(ws, session, say, *, operation_id="", trace_id,
                   is_confirmation=False):
        sent.append((session, say))
        if say == "打开空调":
            return {
                "speech": "已打开", "actions": ["hvac.on"],
                "vehicle_state": {"hvac_on": True}, "need_confirm": False,
                "operation_id": "", "closed_operation_ids": [],
                "card_type": "", "card_text": "", "is_question": False,
                "trace_id": trace_id,
            }
        if say == "触发超时":
            raise TimeoutError("response lost")
        assert ws.name == "cleanup"
        assert original_closed is True
        assert say == "关闭空调"
        return {
            "speech": "已关闭", "actions": ["hvac.off"],
            "vehicle_state": {"hvac_on": False}, "need_confirm": False,
            "operation_id": "", "closed_operation_ids": [],
            "card_type": "", "card_text": "", "is_question": False,
            "trace_id": trace_id,
        }

    async def detail(_collector, trace_id):
        return {
            "turn": {"trace_id": trace_id, "status": "ok", "path": "local"},
            "spans": [], "llm_calls": [], "logs": [],
        }

    monkeypatch.setattr(long_qa, "_connect", connect)
    monkeypatch.setattr(long_qa, "_settled_vehicle_state", settled)
    monkeypatch.setattr(long_qa, "_turn", turn)
    monkeypatch.setattr(long_qa, "_fetch_detail", detail)
    cases = [{
        "id": "ABORT-RESTORE",
        "turns": [
            {"say": "打开空调", "expect": {"actions_include": ["hvac.on"]}},
            {"say": "触发超时", "expect": {}},
        ],
    }]

    result = asyncio.run(long_qa._run_persona(
        "vehicle", cases, "wss://example.invalid",
        "https://collector.invalid", 1,
    ))

    assert result["aborted"] is True
    assert result["vehicle_cleanup"]["verified"] is True
    assert result["vehicle_cleanup"]["cleanup_session_id"].endswith("-cleanup")
    assert (result["vehicle_cleanup"]["cleanup_session_id"], "关闭空调") in sent


def test_collector_audit_accepts_minimax_and_rejects_any_other_llm_provider():
    good = {
        "turn": {"trace_id": "t1", "status": "ok", "path": "cloud",
                 "intents": "info.weather"},
        "spans": [{"service": "cloud-orchestrator", "node": "agent.call",
                    "attrs": {"agent_id": "info", "intent": "info.weather"}}],
        "llm_calls": [{"provider": "minimax", "model": "MiniMax-M3",
                        "status": "ok", "fallback": 0, "pinned": True}],
        "logs": [],
    }
    summary, failures = long_qa.audit_trace_detail(good, "t1")
    assert failures == []
    assert summary["providers"] == ["minimax:MiniMax-M3"]
    assert summary["agents"] == ["info"]

    bad = {**good, "llm_calls": [
        {"provider": "deepseek", "model": "deepseek-v4-flash",
         "status": "ok", "fallback": 1},
    ]}
    _, failures = long_qa.audit_trace_detail(bad, "t1")
    assert failures and "非 MiniMax" in failures[0]

    unpinned = {**good, "llm_calls": [{
        "provider": "minimax", "model": "MiniMax-M3",
        "status": "ok", "fallback": 0, "pinned": False,
    }]}
    _, failures = long_qa.audit_trace_detail(unpinned, "t1")
    assert failures == ["MiniMax-M3 LLM 调用未兑现请求级 pin"]


def test_missing_collector_trace_is_never_counted_as_pass():
    _, failures = long_qa.audit_trace_detail({"error": "not found"}, "lost")
    assert failures == ["collector 缺少 trace lost"]


def test_partial_collector_detail_without_authoritative_turn_is_rejected():
    _, failures = long_qa.audit_trace_detail({
        "turn": {}, "spans": [], "llm_calls": [], "logs": [],
    }, "t-partial")

    assert "collector 缺少权威 turn t-partial" in failures


def test_collector_audit_requires_exact_trace_terminal_status_and_route():
    _, failures = long_qa.audit_trace_detail({
        "turn": {"trace_id": "", "status": "", "path": ""},
        "spans": [], "llm_calls": [], "logs": [],
    }, "t1")

    assert "collector trace 对不上：空 != t1" in failures
    assert "collector 轮次状态不完整：空" in failures
    assert "collector 路由路径不完整：空" in failures


def test_local_trace_also_requires_an_authoritative_intent():
    _, failures = long_qa.audit_trace_detail({
        "turn": {"trace_id": "t1", "status": "ok", "path": "local",
                 "intents": ""},
        "spans": [], "llm_calls": [], "logs": [],
    }, "t1")

    assert "collector 轮次缺少可审计 intent" in failures


def test_cloud_trace_requires_agent_span_or_explicit_engine_lifecycle_node():
    detail = {
        "turn": {"trace_id": "t1", "status": "ok", "path": "cloud",
                 "intents": "info.weather"},
        "spans": [], "llm_calls": [], "logs": [],
    }
    _, failures = long_qa.audit_trace_detail(detail, "t1")
    assert "collector 云端/混合业务轮缺少 agent 归属 span" in failures

    engine_only = {
        **detail,
        "spans": [{"node": "cloud.candidate_aggregate", "attrs": {}}],
    }
    _, failures = long_qa.audit_trace_detail(engine_only, "t1")
    assert "collector 云端/混合业务轮缺少 agent 归属 span" not in failures

    pending_cancel = {
        "turn": {"trace_id": "t1", "status": "ok", "path": "cloud",
                 "intents": ""},
        "spans": [{
            "node": "cloud.pending_cancel",
            "attrs": {"intent": "system.pending_cancel", "owner": "cloud-engine"},
        }],
        "llm_calls": [], "logs": [],
    }
    summary, failures = long_qa.audit_trace_detail(pending_cancel, "t1")
    assert failures == []
    assert summary["intents"] == ["system.pending_cancel"]


def test_cancelled_collector_status_needs_an_explicit_case_expectation():
    detail = {
        "turn": {"trace_id": "t1", "status": "cancelled", "path": "local"},
        "spans": [], "llm_calls": [], "logs": [],
    }
    _, failures = long_qa.audit_trace_detail(detail, "t1")
    assert "collector 轮次状态异常：cancelled" in failures

    _, failures = long_qa.audit_trace_detail(
        detail, "t1", allow_cancelled=True)
    assert "collector 轮次状态异常：cancelled" not in failures


def test_fetch_detail_waits_for_partial_collector_row_to_become_terminal(monkeypatch):
    ready = {"turn": {"trace_id": "t1", "status": "ok", "path": "cloud",
                      "intents": "info.weather"},
             "spans": [{"node": "step.agent:info",
                         "attrs": {"agent_id": "info", "intent": "info.weather"}}],
             "llm_calls": [], "logs": []}
    payloads = iter([
        {"turn": {}, "spans": [{"node": "cloud.planning"}]},
        ready,
        ready,
    ])
    calls = 0

    def http_json(_url):
        nonlocal calls
        calls += 1
        return next(payloads)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(long_qa, "_http_json", http_json)
    monkeypatch.setattr(long_qa.asyncio, "sleep", no_wait)

    detail = asyncio.run(long_qa._fetch_detail(
        "https://collector.invalid", "t1"))

    assert detail["turn"]["trace_id"] == "t1"
    assert calls == 3


def test_fetch_detail_waits_for_agent_span_after_complete_cloud_turn(monkeypatch):
    turn = {"trace_id": "t1", "status": "ok", "path": "cloud",
            "intents": "info.weather"}
    ready = {"turn": turn,
             "spans": [{"node": "step.agent:info",
                         "attrs": {"agent_id": "info", "intent": "info.weather"}}],
             "llm_calls": [], "logs": []}
    payloads = iter([
        {"turn": turn, "spans": [], "llm_calls": [], "logs": []},
        ready,
        ready,
    ])
    calls = 0

    def http_json(_url):
        nonlocal calls
        calls += 1
        return next(payloads)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(long_qa, "_http_json", http_json)
    monkeypatch.setattr(long_qa.asyncio, "sleep", no_wait)

    detail = asyncio.run(long_qa._fetch_detail(
        "https://collector.invalid", "t1"))

    assert detail["spans"][0]["attrs"]["agent_id"] == "info"
    assert calls == 3


def test_fetch_detail_does_not_miss_a_late_unpinned_non_minimax_call(monkeypatch):
    turn = {"trace_id": "t1", "status": "ok", "path": "cloud",
            "intents": "info.weather"}
    spans = [{"id": 1, "node": "step.agent:info",
              "attrs": {"agent_id": "info", "intent": "info.weather"}}]
    late = [{
        "id": 7, "provider": "deepseek", "model": "deepseek-v4-flash",
        "pinned": False, "fallback": 0, "status": "ok", "error": "",
    }]
    payloads = iter([
        {"turn": turn, "spans": spans, "llm_calls": [], "logs": []},
        {"turn": turn, "spans": spans, "llm_calls": late, "logs": []},
        {"turn": turn, "spans": spans, "llm_calls": late, "logs": []},
    ])
    calls = 0

    def http_json(_url):
        nonlocal calls
        calls += 1
        return next(payloads)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(long_qa, "_http_json", http_json)
    monkeypatch.setattr(long_qa.asyncio, "sleep", no_wait)

    detail = asyncio.run(long_qa._fetch_detail(
        "https://collector.invalid", "t1"))
    _, failures = long_qa.audit_trace_detail(detail, "t1")

    assert calls == 3
    assert any("非 MiniMax" in failure for failure in failures)
    assert any("未兑现请求级 pin" in failure for failure in failures)


def test_card_provenance_is_collected_and_mock_is_rejected():
    card_text = '''{
      "type": "weather",
      "_prov": {"provider": "qweather", "mode": "real"},
      "items": [{"_prov": {"provider": "amap", "mode": "degraded"}}]
    }'''
    entries, failures, warnings, notes = long_qa.audit_card_provenance(card_text)
    # ⚠ `card_type` 2026-08-28 随 C15 加进每一条章：**没有卡型就没法按卡型判**，
    # 而「按卡型判」正是这次裁决落地的形态（mock 两档 / deterministic 只对
    # 登记过的卡合法）。同一张卡里的子节点继承就近的 `type`。
    assert entries == [
        {"provider": "qweather", "mode": "real", "card_type": "weather"},
        {"provider": "amap", "mode": "degraded", "card_type": "weather"},
    ]
    assert failures == [] and warnings == [] and notes == []

    entries, failures, warnings, _ = long_qa.audit_card_provenance(
        '{"type":"weather","_prov":{"provider":"qweather","mode":"mock"}}')
    assert entries == [
        {"provider": "qweather", "mode": "mock", "card_type": "weather"}]
    assert failures == ["真栈卡片出现 mock provenance: qweather"]
    assert warnings == []


def test_required_external_card_without_provenance_is_rejected():
    """**出了卡却一个章都没有** —— 这一档才是这条判据要抓的。"""
    entries, failures, warnings, notes = long_qa.audit_card_provenance(
        '{"type":"weather","city":"深圳"}', required=True)

    assert entries == []
    assert failures == ["外部数据卡缺少真实性章"]
    assert warnings == [] and notes == []


def test_no_card_at_all_is_a_note_not_a_failure():
    """**「没出卡」与「出了卡没盖章」是两件事**（2026-08-28 真栈实测逼出来的）。

    INF-WEATHER T4「现在有影响开车的天气预警吗」答「深圳当前没有生效的天气预警。」、
    INF-STOCK T41 答「没有找到「000300.SH」的行情数据」——**没有数据所以不出卡**，
    那是诚实降级、是**正确行为**，判红等于要求它编一张卡出来。
    同 C2-D「『读不到』与『读到了但不对』永远分开报」。
    ⇒ 无卡出 note；真要求「这一轮必须有卡」，用例写 `card_type`。
    """
    for blank in ("", "{}"):
        entries, failures, warnings, notes = long_qa.audit_card_provenance(
            blank, required=True)
        assert entries == [] and failures == [] and warnings == []
        assert notes and "不构成证据" in notes[0]

    # 不 required 时连 note 都不出——那一轮本来就没主张过什么。
    assert long_qa.audit_card_provenance("{}") == ([], [], [], [])


def test_required_external_card_rejects_incomplete_or_unknown_provenance():
    entries, failures, *_ = long_qa.audit_card_provenance(
        '{"type":"weather","_prov":{"vendor":"qweather"}}', required=True)
    assert entries == [
        {"provider": "qweather", "mode": "unknown", "card_type": "weather"}]
    assert failures == [_illegal_mode("unknown", "weather")]

    entries, failures, *_ = long_qa.audit_card_provenance(
        '{"type":"weather","_prov":{"mode":"real"}}', required=True)
    assert entries == [
        {"provider": "unknown", "mode": "real", "card_type": "weather"}]
    assert failures == ["外部数据卡真实性章 provider 缺失"]

    _, failures, *_ = long_qa.audit_card_provenance(
        '{"type":"weather","_prov":{"vendor":"qweather","mode":"typo"}}',
        required=True)
    assert failures == [_illegal_mode("typo", "weather")]

    _, failures, *_ = long_qa.audit_card_provenance(
        '{"type":"weather","_prov":{"vendor":"qweather","mode":"typo"}}')
    assert failures == [_illegal_mode("typo", "weather")]


# ── C15 裁决落探针：deterministic 收编 + mock 两档（2026-08-27 泓舟拍板）──
# 两把尺子过去方向相反（探针「真栈不该有 mock」vs 契约「有 mock 必须承认」），
# 裁决把它们拆成**部署形态期望**与**诚实契约**两件事。下面每条都两向：
# 该合法的合法、该判红的仍判红——只写一半就又是一把只会说「他没说错话」
# 的尺子（同 CD1 那次的判据升级）。

def _illegal_mode(mode: str, card_type: str) -> str:
    """失败串按被测实现自己的规则表拼——**期望值从被消费方派生**，
    改一次 modes 集合不用手改一堆字符串（同 `card_items_raw` 那条判据）。"""
    allowed = sorted(long_qa.card_prov_rule(card_type)["modes"])
    return (f"外部数据卡真实性章 mode 非法: {mode}"
            f"（{card_type} 卡只许 {allowed}）")


def test_deterministic_is_legal_only_on_the_registered_internal_cards():
    _, failures, warnings, _n = long_qa.audit_card_provenance(
        '{"type":"safety_advice",'
        '"_prov":{"vendor":"road-safety","mode":"deterministic"}}')
    assert failures == [] and warnings == []

    # 外源数据卡打 deterministic 是**盖错章**——拿「我自己算的」躲开来源审计。
    _, failures, *_ = long_qa.audit_card_provenance(
        '{"type":"weather","_prov":{"vendor":"qweather","mode":"deterministic"}}')
    assert failures == [_illegal_mode("deterministic", "weather")]

    # 反过来：内部确定性卡打 real 也是错的（§9.3「出现则 mode 必为 deterministic」）。
    _, failures, *_ = long_qa.audit_card_provenance(
        '{"type":"safety_advice","_prov":{"vendor":"road-safety","mode":"real"}}')
    assert failures == [_illegal_mode("real", "safety_advice")]


def test_truthfully_labelled_mock_is_a_warning_only_on_declared_card_types():
    entries, failures, warnings, _n = long_qa.audit_card_provenance(
        '{"type":"manual","_prov":{"vendor":"mock","mode":"mock"}}')
    assert entries == [
        {"provider": "mock", "mode": "mock", "card_type": "manual"}]
    # **WARN 不进 fail**：真栈里 manual-rag 是 mock 属于已知形态，
    # 判红等于要求探针去修一件本轮不打算修的事（裁决 ③：等真手册）。
    assert failures == []
    assert warnings and warnings[0].startswith("manual 卡如实标注了 mock：mock")

    # 没登记过「mock 可接受」的卡型，出现 mock 仍是红。
    _, failures, warnings, _n = long_qa.audit_card_provenance(
        '{"type":"place_list","_prov":{"vendor":"amap","mode":"mock"}}')
    assert failures == ["真栈卡片出现 mock provenance: amap"]
    assert warnings == []


def test_mock_impersonating_real_is_still_a_failure_on_the_exempt_card():
    """豁免的是「如实标注的 mock」，不是「这张卡怎么盖章都行」。"""
    _, failures, warnings, _n = long_qa.audit_card_provenance(
        '{"type":"manual","city":"深圳"}', required=True)
    assert failures == ["外部数据卡缺少真实性章"] and warnings == []

    _, failures, *_ = long_qa.audit_card_provenance(
        '{"type":"manual","_prov":{"vendor":"mock","mode":"typo"}}')
    assert failures == [_illegal_mode("typo", "manual")]


def test_card_group_members_are_judged_by_their_own_card_type():
    """`card_group` 的章打在**成员卡**上（§9.3），判据也必须按成员的卡型走。"""
    _, failures, warnings, _n = long_qa.audit_card_provenance(json.dumps({
        "type": "card_group",
        "items": [
            {"type": "safety_advice",
             "_prov": {"vendor": "road-safety", "mode": "deterministic"}},
            {"type": "manual", "_prov": {"vendor": "mock", "mode": "mock"}},
            {"type": "route_plan", "_prov": {"vendor": "amap", "mode": "mock"}},
        ],
    }, ensure_ascii=False))
    assert failures == ["真栈卡片出现 mock provenance: amap"]
    assert len(warnings) == 1 and warnings[0].startswith("manual 卡")


def test_unregistered_card_types_fall_back_to_the_external_default():
    rule = long_qa.card_prov_rule("brand_new_card")
    assert rule == {"modes": long_qa._EXTERNAL_PROV_MODES, "mock": "fail"}


def test_card_prov_rules_match_the_contract_mandatory_list():
    """§9.3 的必带清单与探针的卡型表**是一份声明两个消费方**，不许各自演化。

    这条守的是 C15 落地的方式本身：裁决说「给探针建卡型清单，正好把 §9.3 的
    必带清单机械化成探针判据」——两份表要是能分头改，那就还是两把尺子。
    """
    doc = (long_qa._ROOT / "docs" / "conventions.md").read_text(encoding="utf-8")
    segment = re.search(r"凡展示外源数据的卡必须带（(.+?)——", doc, re.S)
    assert segment, "§9.3 的必带清单段落找不到了——契约改了就要同批改这里"
    declared = {
        token.strip(" *`\n")
        for token in re.split(r"[/；：,、\s]+", segment.group(1))
    }
    declared = {t for t in declared if re.fullmatch(r"[a-z][a-z_]+", t)}
    assert declared == set(long_qa._EXTERNAL_PROV_CARDS)
    # 内部确定性卡这一条也在契约里，同样不许只改一边。
    assert "`safety_advice` 据此登记为**内部确定性卡**" in doc
    assert set(long_qa._DETERMINISTIC_PROV_CARDS) == {"safety_advice"}


def test_stock_source_followup_is_checked_against_the_previous_real_card():
    prior = [{
        "case_instance": "INF-STOCK",
        "local_turn": 1,
        "card_text": '{"type":"stock_quote","market_time":"2026-08-25 15:00:00",'
                     '"_prov":{"vendor":"tushare","mode":"real"}}',
    }]
    row = {
        "case_instance": "INF-STOCK",
        "local_turn": 2,
        "speech": "数据来源是 Tushare，行情时间是2026-08-25 15:00:00。",
    }

    assert long_qa.audit_stock_provenance_followup(row, prior, 1) == []
    row["speech"] = "数据来源和更新时间见卡片。"
    assert long_qa.audit_stock_provenance_followup(row, prior, 1) == [
        "股票来源追问没有复述真实 provider：tushare",
        "股票来源追问没有复述真实行情时间：2026-08-25 15:00:00",
    ]


def test_endpoint_snapshot_is_resolved_once_and_derives_all_cloud_urls(monkeypatch):
    calls = 0
    target = SimpleNamespace(name="cloud")

    def resolve(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return target

    monkeypatch.setattr(long_qa, "read_root_env", lambda *_args: {
        "TAILNET_FQDN": "qa.example.ts.net", "VITE_WS_TOKEN": "secret token",
    })
    monkeypatch.setattr(long_qa, "resolve_e2e_target", resolve)
    monkeypatch.setattr(long_qa, "endpoint_environment", lambda _target: {
        "WS_URL": "wss://qa.example.ts.net:8443/ws",
        "COLLECTOR_URL": "https://qa.example.ts.net:8446",
        "AUDIO_API_URL": "https://qa.example.ts.net:8444",
    })

    ws_url, collector, audio, name = long_qa._resolve_endpoints()

    assert calls == 1
    assert ws_url == "wss://qa.example.ts.net:8443/ws?token=secret+token"
    assert collector == "https://qa.example.ts.net:8446"
    assert audio == "https://qa.example.ts.net:8444"
    assert name == "cloud"


def test_long_probe_fails_closed_when_target_is_not_cloud(monkeypatch):
    monkeypatch.setattr(long_qa, "read_root_env", lambda *_args: {})
    monkeypatch.setattr(long_qa, "resolve_e2e_target", lambda *_args, **_kwargs:
                        SimpleNamespace(name="local"))
    monkeypatch.setattr(long_qa, "endpoint_environment", lambda _target: {
        "WS_URL": "ws://localhost/ws", "COLLECTOR_URL": "http://localhost",
        "AUDIO_API_URL": "http://localhost:50059",
    })

    with pytest.raises(SystemExit, match="只允许 target=cloud"):
        long_qa._resolve_endpoints()


def test_minimax_tts_validation_requires_available_capability_and_real_audio():
    good = {
        "provider": "minimax", "model": "speech-2.8-turbo",
        "voice": "female-tianmei", "capability_available": True,
        "meta": {"type": "meta", "format": "pcm", "sample_rate": 24000},
        "chunks": 2, "audio_bytes": 1024, "terminal": "done",
        "pcm": {
            "sample_rate": 24000, "sample_count": 512,
            "duration_ms": 500, "peak_abs": 12000,
            "rms": 3000, "nonzero_ratio": 0.8, "playable": True,
        },
    }
    assert long_qa.validate_minimax_tts(good) == []

    bad = {**good, "provider": "mock", "capability_available": False,
           "meta": None, "chunks": 0, "audio_bytes": 0,
           "terminal": "unsupported"}
    assert long_qa.validate_minimax_tts(bad) == [
        "TTS 未锁定 minimax",
        "MiniMax TTS 能力未在云端声明 available",
        "MiniMax TTS 未返回 PCM meta",
        "MiniMax TTS 未返回二进制音频帧",
        "MiniMax TTS 未正常 done（terminal=unsupported）",
        "MiniMax TTS PCM 不能被 HMI 播放",
    ]


def test_pcm_metrics_prove_playable_non_silent_effective_audio():
    # 24 kHz mono s16le，正负交替 0.5 秒；这与 HMI PcmPlayer 的输入契约一致。
    one = (12000).to_bytes(2, "little", signed=True)
    other = (-12000).to_bytes(2, "little", signed=True)
    metrics = long_qa.pcm_metrics((one + other) * 6000, sample_rate=24000)

    assert metrics["sample_count"] == 12000
    assert metrics["duration_ms"] == 500
    assert metrics["peak_abs"] == 12000
    assert metrics["nonzero_ratio"] == 1.0
    assert metrics["playable"] is True

    assert long_qa.pcm_metrics(b"\x00\x00" * 12000, sample_rate=24000)[
        "playable"] is False
    assert long_qa.pcm_metrics(b"\x00", sample_rate=24000)["playable"] is False


def test_tts_samples_are_bound_to_one_real_business_turn_per_persona():
    results = [
        {
            "persona": name, "session_id": f"s-{name}",
            "turns": [
                {"turn": 1, "case": "AUTO-CANCEL", "speech": "已取消",
                 "trace_id": f"cleanup-{name}", "fails": []},
                {"turn": 2, "case": "BIZ", "speech": f"{name} 的真实业务回复",
                 "trace_id": f"biz-{name}", "fails": []},
            ],
        }
        for name in ("vehicle", "family", "merchant", "information", "adversarial")
    ]

    samples = long_qa.select_tts_business_samples(results)

    assert set(samples) == {result["persona"] for result in results}
    assert all(sample["case"] == "BIZ" for sample in samples.values())
    assert all(sample["trace_id"].startswith("biz-") for sample in samples.values())


def test_barge_in_validation_requires_cancel_close_and_no_post_cancel_audio():
    good = {
        "provider": "minimax", "cancel_sent": True,
        "audio_before_cancel_bytes": 2048, "post_cancel_audio_bytes": 0,
        "terminal": "closed_after_cancel", "closed_after_cancel_ms": 80,
    }
    assert long_qa.validate_tts_barge_in(good) == []

    bad = {**good, "cancel_sent": False, "post_cancel_audio_bytes": 512,
           "terminal": "timeout", "closed_after_cancel_ms": None}
    assert long_qa.validate_tts_barge_in(bad) == [
        "MiniMax TTS 打断帧未发出",
        "MiniMax TTS cancel 后仍收到音频残帧",
        "MiniMax TTS cancel 后连接未及时关闭",
    ]


def test_release_snapshot_requires_healthy_cloud_and_exact_full_sha():
    sha = "a" * 40
    good = _real_cloud_status_payload(sha)

    assert long_qa.validate_release_snapshot(good, sha) == []
    assert long_qa.validate_release_snapshot(good, "b" * 40) == [
        f"云端 release_sha={sha}，与 expected_sha={'b' * 40} 不一致",
    ]

    broken = {
        **_real_cloud_status_payload("a" * 7, healthy=4),
        "warnings": ["one down"],
    }
    failures = long_qa.validate_release_snapshot(broken, sha)
    assert "云端 status 不是 ok：degraded" in failures
    assert "云端 release_sha 不是完整 40 位 SHA" in failures
    assert "云端端点不是 5/5 healthy：4/5" in failures
    assert "云端 status 带 warning：one down" in failures


def test_release_snapshot_uses_unified_status_entry_and_persists_payload():
    sha = "c" * 40
    calls = []

    def status_probe():
        calls.append("status")
        return 0, _real_cloud_status_payload(sha)

    snapshot = long_qa.cloud_release_snapshot(sha, status_probe=status_probe)

    assert calls == ["status"]
    assert snapshot["release_sha"] == sha
    assert snapshot["expected_sha"] == sha
    assert snapshot["failures"] == []


def test_release_continuity_requires_the_same_healthy_sha_at_start_and_end():
    sha = "d" * 40
    start = _real_cloud_status_payload(sha)
    end = _real_cloud_status_payload(sha)

    assert long_qa.validate_release_continuity(start, end, sha) == []

    changed = _real_cloud_status_payload("e" * 40, healthy=4)
    failures = long_qa.validate_release_continuity(start, changed, sha)
    assert any(failure.startswith("end: 云端 release_sha=") for failure in failures)
    assert "end: 云端端点不是 5/5 healthy：4/5" in failures


def test_vehicle_restore_plan_covers_every_managed_state_without_debug_backdoor():
    before = {
        "hvac_on": True, "front_defogger": False,
        "rear_defogger": False, "window": "30%", "sunroof": "closed",
        "rear_view_mirror": "folded", "steering_wheel_heating": False,
        "volume_muted": True, "warning_light": False, "media": "playing",
    }
    after = {
        **before, "hvac_on": False, "front_defogger": True,
        "rear_defogger": True, "window": "open", "sunroof": "open",
        "rear_view_mirror": "unfolded", "steering_wheel_heating": True,
        "volume_muted": False, "warning_light": True, "media": "paused",
    }

    commands = long_qa.vehicle_restore_commands(before, after)

    assert commands == [
        ("hvac_on", "打开空调"),
        ("front_defogger", "关闭前挡风玻璃除雾"),
        ("rear_defogger", "关闭后挡风玻璃除雾"),
        ("window", "把车窗开到30%"),
        ("sunroof", "关闭天窗"),
        ("rear_view_mirror", "把后视镜折叠起来"),
        ("steering_wheel_heating", "关闭方向盘加热"),
        ("volume_muted", "静音"),
        ("warning_light", "关闭双闪"),
        ("media", "播放音乐"),
    ]


def test_missing_vehicle_keys_are_not_invented_from_semantic_defaults():
    normalized = long_qa.managed_vehicle_state({
        "hvac_on": False, "window": "closed", "sunroof": "closed",
        "media": "stopped",
    })

    assert "rear_view_mirror" not in normalized
    assert "volume_muted" not in normalized
    assert "warning_light" not in normalized


def test_vehicle_persona_rejects_missing_authoritative_baseline_key(monkeypatch):
    async def incomplete_state(_collector, **_kwargs):
        """通道通、但只给得出一个键——**这是「读到了但不全」，不是「读不到」**。"""
        required = set(long_qa._MANAGED_VEHICLE_KEYS)
        return long_qa.VehicleStateRead(
            {"hvac_on": False}, False, True, tuple(sorted(required - {"hvac_on"})))

    async def forbidden_connect(_url):
        raise AssertionError("missing baseline must fail before opening a session")

    monkeypatch.setattr(long_qa, "_settled_vehicle_state", incomplete_state)
    monkeypatch.setattr(long_qa, "_connect", forbidden_connect)
    cases = [{
        "id": "MIRROR",
        "turns": [{
            "say": "把后视镜折叠起来",
            "expect": {"actions_include": ["rear_view_mirror.fold"]},
        }],
    }]

    result = asyncio.run(long_qa._run_persona(
        "vehicle", cases, "wss://example.invalid",
        "https://collector.invalid", 1,
    ))

    assert result["aborted"] is True
    assert result["turn_count"] == 0
    assert "rear_view_mirror" in result["abort_reason"]


def test_settled_vehicle_state_waits_past_old_cache_for_expected_state(monkeypatch):
    values = iter([
        {"rear_view_mirror": "unfolded"},
        {"rear_view_mirror": "folded"},
        {"rear_view_mirror": "folded"},
    ])
    calls = 0

    async def vehicle_state(_collector):
        nonlocal calls
        calls += 1
        return next(values)

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(long_qa, "_vehicle_state", vehicle_state)
    monkeypatch.setattr(long_qa.asyncio, "sleep", no_wait)

    result = asyncio.run(long_qa._settled_vehicle_state(
        "https://collector.invalid",
        attempts=3,
        required_keys={"rear_view_mirror"},
        expected={"rear_view_mirror": "folded"},
    ))

    assert result.settled is True
    assert result.reachable is True
    assert result.value["rear_view_mirror"] == "folded"
    assert calls == 3


def test_merchant_persona_proves_draft_zero_state_even_without_preview_card(
    monkeypatch,
):
    sent: list[str] = []

    class _Ws:
        async def close(self):
            return None

    async def connect(_url):
        return _Ws()

    async def turn(_ws, _session, say, *, operation_id="", trace_id,
                   is_confirmation=False):
        sent.append(say)
        if say == "清理本次会话的订单预览":
            card = json.dumps({
                "type": "merchant_draft_cleanup",
                "session_id_digest": "abc123",
                "drafts_before": 0,
                "drafts_removed": 0,
                "drafts_after": 0,
            }, ensure_ascii=False)
            return {
                "speech": "已清理", "actions": [], "need_confirm": False,
                "operation_id": "", "closed_operation_ids": [],
                "card_type": "merchant_draft_cleanup", "card_text": card,
                "is_question": False, "trace_id": trace_id,
            }
        return {
            "speech": "没有卡片的普通商户回复", "actions": [],
            "need_confirm": False, "operation_id": "",
            "closed_operation_ids": [], "card_type": "", "card_text": "",
            "is_question": False, "trace_id": trace_id,
        }

    async def detail(_collector, trace_id):
        return {
            "turn": {"trace_id": trace_id, "status": "ok", "path": "edge",
                     "intents": "shop.preview_discard"},
            "spans": [], "llm_calls": [], "logs": [],
        }

    monkeypatch.setattr(long_qa, "_connect", connect)
    monkeypatch.setattr(long_qa, "_turn", turn)
    monkeypatch.setattr(long_qa, "_fetch_detail", detail)
    monkeypatch.setattr(
        long_qa, "_settled_vehicle_state", _complete_vehicle_state,
    )
    monkeypatch.setattr(long_qa, "recovery_turns", lambda: [])
    cases = [{"id": "MERCHANT", "turns": [{"say": "看看咖啡", "expect": {}}]}]

    result = asyncio.run(long_qa._run_persona(
        "merchant", cases, "wss://example.invalid",
        "https://collector.invalid", 1,
    ))

    assert sent == ["看看咖啡", "清理本次会话的订单预览"]
    assert result["merchant_cleanup_proofs"][-1]["drafts_after"] == 0
    assert result["cleanup_failures"] == []


def test_merchant_draft_cleanup_card_proves_exact_session_is_empty():
    card = json.dumps({
        "type": "merchant_draft_cleanup", "session_id_digest": "abc123",
        "drafts_before": 2, "drafts_removed": 2, "drafts_after": 0,
    }, ensure_ascii=False)
    proof, failures = long_qa.audit_merchant_draft_cleanup(card)

    assert proof["drafts_before"] == 2
    assert proof["drafts_removed"] == 2
    assert proof["drafts_after"] == 0
    assert failures == []

    _, failures = long_qa.audit_merchant_draft_cleanup(json.dumps({
        "type": "merchant_draft_cleanup", "session_id_digest": "abc123",
        "drafts_before": 2,
        "drafts_removed": 1, "drafts_after": 1,
    }))
    assert failures == ["商户临时预览清理后仍有 1 条 active draft"]


# ── 逐 persona 落盘（2026-08-28 一次外部中止逼出来的）────────────────────
# 那一趟跑到最后一个 persona 时被中止，而产物只在**全部 persona 结束之后**才写
# ⇒ 四个已跑完 persona 的 trace / 卡片明细全丢，只剩控制台 ✓/✗。
# 判据：**长时任务的中间产物要在产生的时候就落地，不是在结束的时候。**

def test_each_finished_persona_is_persisted_before_the_next_one_starts(
        tmp_path, monkeypatch):
    out = tmp_path / "run.json"
    seen: list[list[str]] = []

    async def fake_persona(name, cases, ws_url, collector, stamp):
        # 每跑完一个就去看增量档里已经有谁——**证明它是「跑完就落」不是「最后一次性落」**
        if out.with_name("run.partial.json").exists():
            payload = json.loads(
                out.with_name("run.partial.json").read_text(encoding="utf-8"))
            seen.append(list(payload["completed_personas"]))
        else:
            seen.append([])
        return {"persona": name, "turns": [], "turn_count": 0,
                "passed": 0, "failed": 0}

    monkeypatch.setattr(long_qa, "_run_persona", fake_persona)
    monkeypatch.setattr(long_qa, "build_persona_plans",
                        lambda: {"a": [], "b": [], "c": []})

    results = asyncio.run(long_qa._run(["a", "b", "c"], "ws://x", "http://y", out))

    assert [r["persona"] for r in results] == ["a", "b", "c"]
    # 第 1 个 persona 开跑时还没有档；第 2 个开跑时 a 已经在档里；第 3 个时 a、b 都在。
    assert seen == [[], ["a"], ["a", "b"]]
    final = json.loads(
        out.with_name("run.partial.json").read_text(encoding="utf-8"))
    assert final["completed_personas"] == ["a", "b", "c"]
    assert "不能当完整证据用" in final["note"]


def test_partial_file_never_breaks_the_run_when_it_cannot_be_written(
        tmp_path, monkeypatch):
    """保险失效不该拖垮被保的东西。"""
    async def fake_persona(name, cases, ws_url, collector, stamp):
        return {"persona": name, "turns": [], "turn_count": 0,
                "passed": 0, "failed": 0}

    def boom(*_args, **_kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(long_qa, "_run_persona", fake_persona)
    monkeypatch.setattr(long_qa, "build_persona_plans", lambda: {"a": []})
    monkeypatch.setattr(long_qa.Path, "write_text", boom)

    results = asyncio.run(
        long_qa._run(["a"], "ws://x", "http://y", tmp_path / "run.json"))
    assert [r["persona"] for r in results] == ["a"]


def test_partial_file_is_removed_once_the_full_artifact_lands(tmp_path):
    """整趟成功 ⇒ 增量档功成身退，别留半截的在旁边误导下一个人。"""
    out = tmp_path / "run.json"
    long_qa._write_partial(out, [{"persona": "a"}])
    partial = long_qa._partial_path(out)
    assert partial.exists()
    partial.unlink(missing_ok=True)
    assert not partial.exists()


def test_recovery_contract_has_three_audited_turns():
    turns = long_qa.recovery_turns()

    assert len(turns) == 3
    assert turns[0]["expect"] == {
        "no_actions": True, "need_confirm": False,
        "speech_not": ["仍有待确认", "还有待确认的操作"],
    }
    assert turns[1]["audit"] == {
        "intent_any": ["info.weather"], "provenance_required": True,
    }
    assert turns[2]["expect"] == {"no_actions": True, "need_confirm": False}


def test_recovery_first_turn_asserts_the_deterministic_read_out():
    """C16-5：这一轮原来只有排除式判据，于是真栈里答「嗯」和答一个学校地址
    双双判绿。**答非所问不是措辞问题**，排除表永远抓不到它——C4-B 之后
    这句话有确定性出口了，断言就该落在**出口命中**上。"""
    first = long_qa.recovery_turns()[0]
    assert first["audit"]["intent_any"] == [
        "system.pending_state", "system.no_pending"]

    # 反向：落到 chitchat（真栈原样）当场红。
    row = {"speech": "嗯", "actions": [], "card_text": "",
           "trace": {"intents": ["chitchat.talk"]}}
    failures, _ = long_qa.audit_row_expectations(row, [row], first["audit"])
    assert failures and "落域不符" in failures[0]

    # 正向：命中确定性出口就绿。
    row["trace"] = {"intents": ["system.pending_state"]}
    row["speech"] = "当前没有待确认的操作。"
    assert long_qa.audit_row_expectations(row, [row], first["audit"]) == ([], [])


def test_persona_judge_resolves_case_local_turn_references_not_global_turns():
    turn = {
        "expect": {"names_item_from": {"turn": 1, "index": 2}},
    }
    prior = [{
        "turn": 41,
        "local_turn": 1,
        "card_items": ["第一项", "第二项"],
    }]
    obs = {
        "speech": "第二项 14.90 元。",
        "actions": [],
        "need_confirm": False,
        "card_type": "",
        "is_question": False,
    }

    failures, notes = long_qa.judge_persona_turn(
        turn, obs, prior, stamp=123456)

    assert failures == []
    assert notes == []


def test_persona_judge_rejects_unexpected_confirmation_on_read_only_turn():
    turn = {"expect": {"no_actions": True}}
    obs = {
        "speech": "准备退款，确认吗？",
        "actions": [],
        "need_confirm": True,
        "card_type": "",
        "is_question": True,
    }

    failures, _ = long_qa.judge_persona_turn(
        turn, obs, [], stamp=123456)

    assert "只读/普通业务轮意外进入待确认" in failures


def test_persona_judge_rejects_agent_internal_error_speech():
    failures, _ = long_qa.judge_persona_turn(
        {"expect": {"no_actions": True}},
        {
            "speech": "Agent 内部错误：ValueError",
            "actions": [],
            "need_confirm": False,
            "card_type": "",
            "is_question": False,
        },
        [],
        stamp=123456,
    )

    assert "Agent 返回内部错误" in failures


def test_persona_judge_allows_confirmation_when_case_explicitly_requires_it():
    turn = {"expect": {"need_confirm": True}}
    obs = {
        "speech": "这项操作需要确认。",
        "actions": [],
        "need_confirm": True,
        "card_type": "",
        "is_question": False,
    }

    failures, _ = long_qa.judge_persona_turn(
        turn, obs, [], stamp=123456)

    assert failures == []


def test_websocket_connect_retries_transient_opening_handshake_failures(monkeypatch):
    calls = 0

    class _Ws:
        async def recv(self):
            raise asyncio.TimeoutError

    async def flaky(_url):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("opening handshake")
        return _Ws()

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(long_qa.probe.websockets, "connect", flaky)
    monkeypatch.setattr(long_qa.asyncio, "sleep", no_wait)

    ws = asyncio.run(long_qa._connect("wss://example.invalid", attempts=3))

    assert isinstance(ws, _Ws)
    assert calls == 3


def test_long_turn_injects_the_minimax_request_pin(monkeypatch):
    captured = {}

    async def one_turn(*_args, **kwargs):
        captured.update(kwargs)
        return {"speech": "ok", "actions": []}

    monkeypatch.setattr(long_qa.probe, "_one_turn", one_turn)

    asyncio.run(long_qa._turn(
        object(), "session", "查天气", trace_id="trace-1"))

    assert captured["meta_overrides"] == {
        "llm_provider": "minimax", "llm_model": "MiniMax-M3",
    }


def test_state_check_uses_only_the_last_action_for_each_state_key():
    assert long_qa._state_failures(
        ["hvac.on", "hvac.off"], {"hvac_on": False}) == []
    assert long_qa._state_failures(
        ["hvac.off", "hvac.on"], {"hvac_on": True}) == []


def test_state_check_still_reports_a_wrong_final_state():
    failures = long_qa._state_failures(
        ["hvac.on", "hvac.off"], {"hvac_on": True})

    assert failures == [
        "状态未兑现 hvac.off: hvac_on=True, 期望 False",
    ]


# ── 诊断出口：读不到 vs 读到了但不对（卡 C2-D / C16-8，2026-08-27）───────────
# 2026-08-26 那轮 QA 的 vehicle persona 报的是「collector 无法回读车辆恢复终态，
# 终值未知」——**回读通道一直是好的**，读回来的值就是不等于基线。两种失败被合并成
# 一句谎话，于是逐键 diff 成了死代码，两个端侧真 bug（后挡除雾关错对象、
# 「关闭音乐」落 pause）被整个盖住，最后写进 findings 的是「探针基建问题」。
#
# 判据（第二次沉淀，android-m3 那批在别处记过一次）：
# **「读不到」与「读到了但不对」永远分开报。**

def test_unmatched_read_returns_the_last_value_not_an_empty_dict(monkeypatch):
    async def vehicle_state(_collector):
        return {"rear_defogger": True}

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(long_qa, "_vehicle_state", vehicle_state)
    monkeypatch.setattr(long_qa.asyncio, "sleep", no_wait)

    result = asyncio.run(long_qa._settled_vehicle_state(
        "https://collector.invalid", attempts=2,
        required_keys={"rear_defogger"}, expected={"rear_defogger": False}))

    assert result.settled is False
    assert result.reachable is True, "通道是通的，不许报成读不到"
    assert result.value == {"rear_defogger": True}, "末次读数必须带回去，否则 diff 无从谈起"
    assert result.missing == ()


def test_unreachable_collector_is_reported_as_unreachable(monkeypatch):
    async def vehicle_state(_collector):
        return {}

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(long_qa, "_vehicle_state", vehicle_state)
    monkeypatch.setattr(long_qa.asyncio, "sleep", no_wait)

    result = asyncio.run(long_qa._settled_vehicle_state(
        "https://collector.invalid", attempts=2, required_keys={"rear_defogger"}))

    assert result.reachable is False
    assert result.value == {}
    assert result.missing == ("rear_defogger",)


def test_restore_failure_names_the_key_instead_of_blaming_the_collector(monkeypatch):
    """整条 persona 跑一遍：恢复没到位时，failures 必须**逐键点名**。"""
    class _Ws:
        async def close(self):
            return None

    async def connect(_url):
        return _Ws()

    baseline = _complete_vehicle_state_payload()
    after_business = {**baseline, "rear_defogger": True}
    reads = iter([
        _settled(baseline),                                   # 基线
        _settled(after_business),                             # 业务后
        long_qa.VehicleStateRead(after_business, False, True, ()),   # 恢复后仍没回去
    ])

    async def settled(_collector, **_kwargs):
        return next(reads)

    async def turn(_ws, _session, say, *, operation_id="", trace_id,
                   is_confirmation=False):
        actions = ["rear_defogger.open"] if say == "打开后挡风玻璃除雾" else []
        return {
            "speech": "好的", "actions": actions, "need_confirm": False,
            "operation_id": "", "closed_operation_ids": [],
            "card_type": "", "card_text": "", "is_question": False,
            "trace_id": trace_id,
        }

    async def detail(_collector, trace_id):
        return {"turn": {"trace_id": trace_id, "status": "ok", "path": "local"},
                "spans": [], "llm_calls": [], "logs": []}

    monkeypatch.setattr(long_qa, "_connect", connect)
    monkeypatch.setattr(long_qa, "_settled_vehicle_state", settled)
    monkeypatch.setattr(long_qa, "_turn", turn)
    monkeypatch.setattr(long_qa, "_fetch_detail", detail)

    result = asyncio.run(long_qa._run_persona(
        "vehicle", [{"id": "DEFOG", "turns": [
            {"say": "打开后挡风玻璃除雾",
             "expect": {"actions_include": ["rear_defogger.open"]}}]}],
        "wss://example.invalid", "https://collector.invalid", 1))

    failures = " ".join(result["vehicle_cleanup"]["failures"])
    assert "rear_defogger" in failures, f"必须点名到键：{failures}"
    assert "无法回读" not in failures, f"通道是通的，不许报成读不到：{failures}"


# ── 恢复轮动作-目标一致性（N7 / C16-6）─────────────────────────────────────

def test_restore_turn_flags_an_action_on_the_wrong_object():
    """T56 原样：恢复 `rear_defogger`，实际执行 `front_defogger.close`。"""
    fails = long_qa.judge_restore_turn(
        "rear_defogger", False, ["front_defogger.close"])
    assert fails and "front_defogger.close" in fails[0]


def test_restore_turn_flags_a_wrong_value_on_the_right_object():
    """T59 原样：目标 `media=stopped`，实际落 `media.pause`。"""
    fails = long_qa.judge_restore_turn("media", "stopped", ["media.pause"])
    assert fails and "media.pause" in fails[0]


def test_restore_turn_flags_no_action_at_all():
    assert long_qa.judge_restore_turn("hvac_on", False, [])


def test_restore_turn_accepts_the_correct_action():
    assert long_qa.judge_restore_turn("media", "stopped", ["media.stop"]) == []
    assert long_qa.judge_restore_turn(
        "rear_defogger", False, ["rear_defogger.close"]) == []


def test_restore_turn_tolerates_actions_the_ruler_does_not_know():
    """尺子认不出的动作（开度型 `window.set`）**不当证据也不当罪证**。

    这是「尺子覆盖面」问题，不该变成被测对象的红灯——否则下一个人会为了让探针变绿
    去改被测系统。
    """
    assert long_qa.judge_restore_turn("window", "50%", ["window.set"]) == []


# ── C10-D：跑批结束的提醒清理段 ─────────────────────────────────────────

def _reminder_persona(monkeypatch, *, leftover_after_cancel: bool):
    """一个建了提醒的 persona；`leftover_after_cancel` 控制复核轮还剩不剩。"""
    sent: list[str] = []
    listed = {"n": 0}

    class _Ws:
        async def close(self):
            return None

    async def connect(_url):
        return _Ws()

    async def turn(_ws, _session, say, *, operation_id="", trace_id,
                   is_confirmation=False):
        sent.append(say)
        items: list[dict] = []
        if say == "列出我现在进行中的提醒":
            listed["n"] += 1
            first = listed["n"] == 1
            if first or leftover_after_cancel:
                items = [{"title": "交周报QA000002"}, {"title": "别人的提醒"}]
            else:
                items = [{"title": "别人的提醒"}]
        card = ({"type": "reminder_list", "items": items} if items else {})
        return {
            "speech": "好的", "actions": [], "need_confirm": False,
            "operation_id": "", "closed_operation_ids": [],
            "card_type": card.get("type", ""),
            "card_text": json.dumps(card, ensure_ascii=False),
            "card_items": [it["title"] for it in items],
            "is_question": False, "trace_id": trace_id,
        }

    async def detail(_collector, trace_id):
        return {
            "turn": {"trace_id": trace_id, "status": "ok", "path": "cloud",
                     "intents": "reminder.list"},
            "spans": [{"node": "agent", "attrs": {"agent_id": "reminder"}}],
            "llm_calls": [], "logs": [],
        }

    monkeypatch.setattr(long_qa, "_connect", connect)
    monkeypatch.setattr(long_qa, "_turn", turn)
    monkeypatch.setattr(long_qa, "_fetch_detail", detail)
    monkeypatch.setattr(
        long_qa, "_settled_vehicle_state", _complete_vehicle_state)
    monkeypatch.setattr(long_qa, "recovery_turns", lambda: [])
    cases = [{"id": "SL1", "turns": [
        {"say": "明天下午四点提醒我交周报QA{run}", "expect": {}}]}]
    result = asyncio.run(long_qa._run_persona(
        "family", cases, "wss://example.invalid", "https://collector.invalid", 1))
    return sent, result


def test_reminder_cleanup_cancels_only_its_own_run_and_verifies(monkeypatch):
    """C10-D：跑批自己建的提醒必须清掉——不清就永远沉在库里参与序数参照系。

    匹配面是**本次 run 号**：别人的提醒一个字都不碰。
    """
    sent, result = _reminder_persona(monkeypatch, leftover_after_cancel=False)

    assert sent[-3:] == ["列出我现在进行中的提醒", "取消交周报QA000002",
                         "列出我现在进行中的提醒"]
    assert "取消别人的提醒" not in sent
    assert result["cleanup_failures"] == []


def test_reminder_cleanup_reports_when_the_count_does_not_fall(monkeypatch):
    """**计数要回落**——「发过取消指令」不等于「库里没有了」（Q6 那条判据）。

    而且这一段**只观测不中止**：清理失败不该把下一趟跑批截断。
    """
    sent, result = _reminder_persona(monkeypatch, leftover_after_cancel=True)

    assert any("提醒清理后仍有本次 run 的条目" in f
               for f in result["cleanup_failures"])
    assert result["aborted"] is False


def test_reminder_cleanup_is_skipped_when_the_persona_built_nothing(monkeypatch):
    """没有 `{run}` 的 persona **一轮都不多跑**。

    多出来的轮次本身就会改变读数（同「测试替被测系统提供前提」那条）：
    每个 persona 白搭两次 LLM 调用，还会把轮号往后推、让既有 fails 行对不上号。
    """
    class _Ws:
        async def close(self):
            return None

    sent: list[str] = []

    async def connect(_url):
        return _Ws()

    async def turn(_ws, _session, say, *, operation_id="", trace_id,
                   is_confirmation=False):
        sent.append(say)
        return {
            "speech": "好的", "actions": [], "need_confirm": False,
            "operation_id": "", "closed_operation_ids": [], "card_type": "",
            "card_text": "", "card_items": [], "is_question": False,
            "trace_id": trace_id,
        }

    async def detail(_collector, trace_id):
        return {
            "turn": {"trace_id": trace_id, "status": "ok", "path": "cloud",
                     "intents": "info.weather"},
            "spans": [{"node": "agent", "attrs": {"agent_id": "info"}}],
            "llm_calls": [], "logs": [],
        }

    monkeypatch.setattr(long_qa, "_connect", connect)
    monkeypatch.setattr(long_qa, "_turn", turn)
    monkeypatch.setattr(long_qa, "_fetch_detail", detail)
    monkeypatch.setattr(
        long_qa, "_settled_vehicle_state", _complete_vehicle_state)
    monkeypatch.setattr(long_qa, "recovery_turns", lambda: [])
    cases = [{"id": "INF", "turns": [{"say": "深圳明天天气", "expect": {}}]}]

    asyncio.run(long_qa._run_persona(
        "information", cases, "wss://example.invalid",
        "https://collector.invalid", 1))

    assert sent == ["深圳明天天气"]


# ── C16-2：兜底闲聊说自己做了事却零动作（N4，2026-08-28）─────────────────
# 判据本体在 `runtime/execution_claim.py`——探针复用同一份，**不许抄第二张表**。

def _claim_row(speech: str, intents: list[str], actions: list[str]) -> dict:
    return {"speech": speech, "actions": actions, "card_text": "",
            "trace": {"intents": intents}}


def test_chitchat_claiming_an_execution_without_actions_is_red():
    row = _claim_row(
        "好的，已经为您重新计算路线，从华侨城欢乐海岸出发，不走高速，全程大约1.6公里。",
        ["chitchat.talk"], [])
    failures, _ = long_qa.audit_row_expectations(row, [row], {})
    assert failures and "兜底闲聊声称系统做了事（done 形态）" in failures[0]


def test_the_ongoing_form_is_reported_separately_from_the_done_form():
    """两族分开报（`runtime.execution_claim` 的口径）：误报面不同，
    合成一个布尔就分不开了。"""
    row = _claim_row("正在为您查找附近的结果。", ["chitchat.talk"], [])
    failures, _ = long_qa.audit_row_expectations(row, [row], {})
    assert failures and "ongoing 形态" in failures[0]


def test_a_chitchat_turn_that_really_acted_is_green():
    row = _claim_row("好的，已为您打开空调。", ["chitchat.talk"], ["hvac.on"])
    assert long_qa.audit_row_expectations(row, [row], {}) == ([], [])


def test_other_domains_are_not_judged_by_this_rule():
    """信息类能力的合法完成声明不归这条管——`trip`/`research` 本来就不出 action，
    「已为您规划3天行程」是真的。**判据只压 chitchat 那一条兜底路径**：
    它手上一个执行通道都没有，说自己做了就一定没做。"""
    row = _claim_row("已为您规划好 3 天行程。", ["trip.plan"], [])
    assert long_qa.audit_row_expectations(row, [row], {}) == ([], [])
    # 取消挂起是编排自己干的确定性动作，没有 action 是它的正常形态。
    row = _claim_row("好的，已为您取消。", ["system.pending_cancel"], [])
    assert long_qa.audit_row_expectations(row, [row], {}) == ([], [])


def test_a_plain_chitchat_answer_is_green():
    row = _claim_row("深圳今天多云，气温28℃。", ["chitchat.talk"], [])
    assert long_qa.audit_row_expectations(row, [row], {}) == ([], [])


def test_the_execution_claim_ruler_is_the_shared_one():
    """`scripts/` 里不许出现第二份形态表——抄两份就会长出分歧。"""
    from runtime.execution_claim import execution_claim

    assert long_qa.execution_claim is execution_claim


# ── C16-3：取消的是不是用户点名的那条 ──────────────────────────────────

def test_reminder_cancel_turns_require_the_named_title():
    """真栈 T59：说「取消参加代号740945的评审会」，系统答「取消了『刚才那个
    提醒现在几点』」——`reminder.cancel` 成功即绿，没人对过取消的是哪一条。"""
    plans = long_qa.build_persona_plans()
    case = next(c for c in plans["family"] if c.get("source_case_id") == "SL1")
    cancels = [turn for turn in case["turns"]
               if str(turn.get("say") or "").startswith("取消")]
    assert cancels, "SL1 的清理段应当有取消轮"
    named = [turn for turn in cancels
             if "代号{run}的评审会" in (turn.get("expect") or {}).get(
                 "speech_has", [])]
    assert len(named) == 2, "两条真正执行取消的轮都要点名标题"


# ── C16-7（＝C9-E）/ C12-D：语料侧接线 ─────────────────────────────────

def test_every_weather_turn_pins_the_city_named_in_this_session():
    plans = long_qa.build_persona_plans()
    case = next(c for c in plans["information"] if c["id"] == "INF-WEATHER")
    assert all(turn["expect"]["city_any"] == ["深圳"] for turn in case["turns"])


def test_the_recommendation_turn_after_a_dietary_statement_is_judged():
    plans = long_qa.build_persona_plans()
    case = next(c for c in plans["information"] if c["id"] == "INF-PREFERENCE")
    assert "不吃辣" in case["turns"][0]["say"]
    assert case["turns"][1]["expect"]["honors_no_spicy"] is True


# ── 回放计分（C16 验收）─────────────────────────────────────────────────

def _replay_payload(rows: list[dict]) -> dict:
    return {"ts": 1, "expected_sha": "0" * 40,
            "personas": [{"persona": "family", "turns": rows}]}


def _replay_row(**kwargs) -> dict:
    row = {"turn": 1, "case": "", "case_instance": "", "local_turn": 0,
           "say": "", "speech": "", "actions": [], "need_confirm": False,
           "card_type": "", "is_question": False, "card_text": "",
           "card_item_count": 0, "card_items": [], "card_items_raw": [],
           "operation_id": "", "closed_operation_ids": [], "card_buttons": [],
           "nav_targets": [], "follow_up": "", "trace": {"intents": []},
           "fails": []}
    row.update(kwargs)
    return row


def test_replay_recomputes_the_stored_run_with_todays_rulers():
    """存档里那一轮当时是绿的（`expect: {}` 时代），今天的尺子该把它判红。"""
    plans = long_qa.build_persona_plans()
    case = next(c for c in plans["family"] if c.get("source_case_id") == "SF3")
    row = _replay_row(
        turn=28, case="SF3", case_instance=case["id"], local_turn=1,
        say=case["turns"][0]["say"], speech="已为您关闭双闪。",
        actions=["warning_light.close"],
        trace={"intents": ["warning_light.close"]})
    report = long_qa.replay_scoring(_replay_payload([row]))

    assert report["summary"]["stored_failed"] == 0
    assert report["summary"]["newly_red"] == 1
    replayed = report["personas"][0]["turns"][0]
    assert replayed["replayable"] is True
    assert any("不该有动作" in f for f in replayed["replay_fails"])


def test_replay_resolves_the_run_tag_per_case_not_per_turn():
    """`{run}` 往往只出现在 case 的第一轮。**按轮反解会让后面那些轮拿到 0**，
    `speech_has` 立刻整片假红（首版实测 4 行）。"""
    plans = long_qa.build_persona_plans()
    case = next(c for c in plans["family"] if c.get("source_case_id") == "SL1")
    first = probe_subst(case["turns"][0]["say"])
    rows = [
        _replay_row(turn=1, case="SL1", case_instance=case["id"], local_turn=1,
                    say=first, speech="好的，明天 16:00和明天 15:30各提醒你一次："
                                      "参加代号740903的评审会。",
                    card_item_count=2,
                    card_text='{"items":[{"a":1},{"b":2}],"t":"15:30 16:00"}',
                    trace={"intents": ["reminder.create"]}),
        _replay_row(turn=2, case="SL1", case_instance=case["id"], local_turn=3,
                    say="取消第一条",
                    speech="好的，取消了「参加代号740903的评审会」。",
                    trace={"intents": ["reminder.cancel"]}),
    ]
    report = long_qa.replay_scoring(_replay_payload(rows))
    cancel = report["personas"][0]["turns"][1]
    assert cancel["replayable"] is True
    assert not [f for f in cancel["replay_fails"] if "代号" in f]


def test_replay_never_turns_an_unreplayable_row_green():
    """runner 自造的轮次重算不出来 ⇒ **原样保留存档判读**。
    否则「回放红数」会因为尺子够不着而凭空变小。"""
    row = _replay_row(turn=1, case="VEHICLE-RESTORE", local_turn=0,
                      say="关闭音乐", speech="好的",
                      fails=["恢复 media 的动作值不对"])
    report = long_qa.replay_scoring(_replay_payload([row]))
    replayed = report["personas"][0]["turns"][0]
    assert replayed["replayable"] is False
    assert replayed["replay_fails"] == ["恢复 media 的动作值不对"]
    assert report["summary"]["newly_green"] == 0


def test_replay_reports_the_c15_ruling_as_newly_green():
    """`deterministic` 收编之后，当时那 11 行 mode 红该转绿——**并且能说出
    是哪一条判据变了**（转绿逐行打出原判，不做自动归因）。"""
    row = _replay_row(
        turn=1, case="RECOVERY", local_turn=0, say="现在还有待确认的操作吗",
        speech="当前没有待确认的操作。",
        card_text='{"type":"safety_advice",'
                  '"_prov":{"vendor":"road-safety","mode":"deterministic"}}',
        trace={"intents": ["system.pending_state"]},
        fails=["外部数据卡真实性章 mode 非法: deterministic"])
    report = long_qa.replay_scoring(_replay_payload([row]))
    assert report["summary"]["newly_green"] == 1
    assert report["personas"][0]["newly_green"][0]["stored_fails"] == [
        "外部数据卡真实性章 mode 非法: deterministic"]


def probe_subst(template: str) -> str:
    """把用例模板里的 `{run}` 换成回放测试固定用的那个标记。"""
    return template.replace("{run}", "740903")
