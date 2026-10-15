"""Full public answers survive suspension without broadening pending storage or replaying actions."""
import asyncio
import dataclasses
import json
import pytest

from google.protobuf.json_format import ParseDict, MessageToDict
from cockpit.orchestrator.v1 import orchestrator_pb2

from orchestrator.cloud import result_bundle
from orchestrator.cloud.models import Plan, PlanContext, SessionState, Step, StepResult, StepStatus
from orchestrator.cloud.step_input import bind_step_inputs
from orchestrator.cloud.task_identity import bind_task_identity
from orchestrator.cloud.engine import PlannerEngine

TEXT = "打开后备箱，再告诉我空调有哪些模式"
ANSWER = "空调支持自动模式、制冷、制热和通风。" + "自动模式根据设定温度调整风量，操作方法应参考本车型手册。" * 5
CARD = {"type": "manual", "title": "空调", "display_priority": 2,
        "images": [{"data_uri": "data:image/png;base64,unique-image", "sha256": "asset-proof"}]}


def prepared():
    ctx = PlanContext(request_id="r1", session_id="bundle-session", user_id="u1",
                      raw_text=TEXT, safety_origin_text=TEXT)
    plan = Plan([
        Step("s1", "manual-rag", intent="manual.query", origin_text=TEXT, response_only=True,
             slots={"question": "空调有哪些模式"}, capability_description="车型手册"),
        Step("s2", "edge", intent="trunk.open", origin_text=TEXT, require_confirm=True,
             capability_description="打开后备箱"),
    ], raw_text=TEXT, safety_origin_text=TEXT, origin_exchange_id="r1")
    bind_step_inputs(plan)
    bind_task_identity(plan, ctx)
    result_bundle.register_plan(ctx, plan)
    done = StepResult("s1", StepStatus.OK, speech=ANSWER, ui_card=CARD,
                      data={"private_value": "not-public@example.test"})
    pending = StepResult("s2", StepStatus.NEED_CONFIRM, speech="要打开后备箱吗？")
    return ctx, plan, done, pending


def test_full_answer_and_card_are_preserved_once_while_tts_stays_short():
    from tests.test_suspend_prior import _Spy, _make_engine
    ctx, plan, done, pending = prepared()
    engine, store = _make_engine(_Spy())
    final = asyncio.run(engine._suspend(pending, [done, pending], plan, ctx, prior=[done]))
    bundle = final["result_bundles"][0]
    assert ANSWER in bundle["display_text"] and ANSWER not in final["speech"]
    assert bundle["display_text"].endswith("要打开后备箱吗？")
    assert final["ui_card"] == CARD
    row = next(r for r in bundle["results"] if r["step_id"] == "s1")
    assert row["card_ref"] == "final:" and not bundle["cards"]
    assert json.dumps(final).count("unique-image") == 1
    assert all("actions" not in r and "data" not in r for r in bundle["results"])
    assert "not-public@example.test" not in json.dumps(final)
    state = asyncio.run(store.load(ctx.session_id, owner_user_id="u1"))
    saved = json.dumps(dataclasses.asdict(state), ensure_ascii=False)
    assert ANSWER not in saved and "unique-image" not in saved
    assert "private_value" not in saved


def test_proto_roundtrip_preserves_full_answer_and_card_reference():
    ctx, _, done, pending = prepared()
    event = {"ui_card": CARD, "operation_id": "op-1", "need_confirm": True}
    bundle = result_bundle.build(ctx, [done, pending], event)
    wire = ParseDict(bundle, orchestrator_pb2.ResultBundle())
    result = MessageToDict(wire, preserving_proto_field_name=True)
    assert result["task_id"] == ctx.task_identity["task_id"]
    assert result["results"][0]["answer"] == ANSWER
    assert result["results"][0]["card_ref"] == "final:"
    assert result["goals"][0]["coverage"] == "unknown"


def test_resume_keeps_a_reference_without_replaying_the_old_body_or_card():
    from tests.test_suspend_prior import _Spy, _make_engine
    ctx, plan, done, pending = prepared()
    engine, store = _make_engine(_Spy())
    old = asyncio.run(engine._suspend(pending, [done, pending], plan, ctx, prior=[done]))
    state = asyncio.run(store.load(ctx.session_id, owner_user_id="u1"))
    restored, seeds = engine._restore(state, inject_confirmed=False)
    assert seeds[0].from_history
    ctx2 = PlanContext()
    bind_task_identity(restored, ctx2)
    result_bundle.register_plan(ctx2, restored)
    bundle = result_bundle.build(ctx2, seeds, {})
    row = next(r for r in bundle["results"] if r["step_id"] == "s1")
    original = old["result_bundles"][0]["results"][0]
    assert row["result_ref"] == original["result_ref"]
    assert row["answer_state"] == "reference" and row["answer"] == ""
    assert "card_ref" not in row and bundle["cards"] == {}


def test_cancellation_keeps_completed_references_without_inventing_completion():
    ctx, plan, done, pending = prepared()
    state = SessionState(phase="wait_confirm", operation_id="op-1", pending_step_id="s2",
                         pending_plan=PlannerEngine._serialize_plan(plan),
                         completed_results={"s1": {"status": "ok"}})
    ctx.pending_result_states = {"op-1": state}
    event = {"closed_operation_ids": ["op-1"], "speech": "已取消"}
    result_bundle.attach(event, ctx, None, "cancelled")
    bundle = event["result_bundles"][0]
    assert bundle["revision"] == plan.plan_revision+1
    assert [(r["step_id"], r["status"]) for r in bundle["results"]] == [("s1", "ok"), ("s2", "cancelled")]
    assert all(r["answer_state"] == "reference" and not r["answer"] for r in bundle["results"])


def test_privacy_fence_cannot_reintroduce_closed_task_references_at_the_outer_exit():
    ctx, plan, _, _ = prepared()
    ctx.pending_result_states = {"op-1": SessionState(
        phase="wait_confirm", operation_id="op-1", pending_step_id="s2",
        pending_plan=PlannerEngine._serialize_plan(plan))}
    event = {"closed_operation_ids": ["op-1"], "speech": "正在清除你的数据"}
    result_bundle.attach(event, ctx, None, "store_fenced")
    assert "result_bundles" not in event


def test_response_only_false_claim_cannot_reappear_in_details():
    ctx, _, done, _ = prepared()
    done.speech = "已为您打开车窗。空调有自动模式。"
    bundle = result_bundle.build(ctx, [done], {})
    answer = bundle["results"][0]["answer"]
    assert "已为您打开" not in answer and "自动模式" in answer


def test_non_primary_card_is_a_single_bundle_resource():
    ctx, _, done, _ = prepared()
    event = {"ui_card": {"type": "route_plan", "display_priority": 0}}
    bundle = result_bundle.build(ctx, [done], event)
    assert bundle["results"][0]["card_ref"] == "bundle:s1"
    assert bundle["cards"] == {"s1": CARD}


def test_pending_interaction_card_keeps_its_root_contract():
    ctx, _, done, _ = prepared()
    primary = {"type": "scene_card", "confirmation_context": "activate", "display_priority": 1}
    assert result_bundle.suspension_cards(primary, [done]) is primary


def test_deferred_vehicle_action_is_not_published_as_completed_before_val():
    ctx, plan, _, _ = prepared()
    result = StepResult("s2", StepStatus.OK, speech="已打开后备箱", actions=[
        {"type": "vehicle.control", "payload": {"command": "trunk.open"}}])
    bundle = result_bundle.build(ctx, [result], {})
    row = bundle["results"][0]
    assert row["pending_edge"] and row["status"] == "unknown"
    assert "已打开" not in row["answer"] and "尚未核实" in row["answer"]


def test_failed_pending_save_has_no_active_confirmation_in_bundle():
    ctx, _, done, pending = prepared()
    bundle = result_bundle.build(ctx, [done, pending], {"speech": "状态未能保存"})
    row = next(r for r in bundle["results"] if r["step_id"] == "s2")
    assert row["status"] == "unknown" and not row["answer"] and "operation_id" not in row


def test_escalation_suspension_keeps_the_completed_sibling_answer():
    from tests.test_suspend_prior import _Spy, _make_engine
    ctx, plan, done, pending = prepared()
    engine, _ = _make_engine(_Spy())
    final = asyncio.run(engine._suspend(pending, [pending], plan, ctx, prior=[done]))
    bundle = final["result_bundles"][0]
    assert ANSWER in bundle["display_text"] and final["ui_card"] == CARD
    assert next(r for r in bundle["results"] if r["step_id"] == "s1")["status"] == "ok"


@pytest.mark.parametrize("fenced", [False, True])
def test_pending_store_outage_preserves_public_answers_but_privacy_fence_does_not(fenced):
    from tests.test_suspend_prior import _Spy, _make_engine
    from orchestrator.cloud.session import SAVE_FENCED, SAVE_UNAVAILABLE
    ctx, plan, done, pending = prepared()
    engine, store = _make_engine(_Spy())
    async def fail_save(*args, **kwargs):
        return (SAVE_FENCED if fenced else SAVE_UNAVAILABLE), None
    store.save_pending_result = fail_save
    final = asyncio.run(engine._suspend(pending, [done, pending], plan, ctx, prior=[done]))
    assert not final["need_confirm"] and not final["actions"] and not final.get("operation_id")
    if fenced:
        assert not final.get("result_bundles") and ANSWER not in json.dumps(final, ensure_ascii=False)
    else:
        bundle = final["result_bundles"][0]
        assert ANSWER in bundle["display_text"] and final["speech"] in bundle["display_text"]
        assert all(r["status"] != "need_confirm" for r in bundle["results"])


@pytest.mark.parametrize("kind", ["speech", "action"])
def test_actual_d0_lost_stream_publishes_unknown_without_retry(kind, monkeypatch):
    from tests.test_stream_state import _run_d0
    monkeypatch.setenv("SKILLS_MODE", "off")
    monkeypatch.setenv("EXEMPLARS_MODE", "off")
    spy, events = _run_d0([(kind, "回答开头" if kind == "speech" else {"type": "vehicle.control"})])
    row = events[-1]["result_bundles"][0]["results"][0]
    assert row["status"] == "unknown" and row["verification"] == "unknown"
    assert not spy.unary_calls


def test_executor_timeout_is_unknown_not_proven_failure():
    ctx, _, _, _ = prepared()
    bundle = result_bundle.build(ctx, [StepResult("s2", StepStatus.FAILED, error="step_timeout")], {})
    assert bundle["results"][0]["status"] == "unknown"


def test_legacy_pending_without_identity_keeps_legacy_protocol():
    state = SessionState(phase="wait_confirm", pending_plan={"steps": [{"id": "s1"}]})
    assert result_bundle.pending_closed(state, cancelled=True) is None


def test_public_contract_crosses_the_actual_python_servicer():
    from orchestrator.cloud.server import CloudPlannerServicer
    ctx, _, done, pending = prepared()
    event = {"kind": "final", "speech": "简报", "ui_card": CARD, "operation_id": "op-1", "need_confirm": True}
    result_bundle.with_results(event, ctx, [done, pending])
    class Engine:
        async def run(self, request):
            yield event
    async def collect():
        return [e async for e in CloudPlannerServicer(Engine()).Handle(None, None)]
    final = asyncio.run(collect())[0].final
    assert final.speech == "简报" and final.need_confirm
    assert final.result_bundles[0].results[0].answer == ANSWER
    assert final.result_bundles[0].results[0].card_ref == "final:"


# ── 挂起步自己没卡时，本轮唯一那张交互卡上屏（2026-10-15，核心旅程 NB06）────────────────────────────

_LIST = {"type": "place_list", "display_priority": 1, "items": [{"id": "p1", "name": "川胖虎"}, {"id": "p2", "name": "渝味轩"}]}
_ROUTE = {"type": "charging_route", "display_priority": 0, "items": [{"id": "c1", "name": "一号站"}]}
_INFO = {"type": "weather", "display_priority": 2}


def _ok(step_id, card, history=False):
    return StepResult(step_id, StepStatus.OK, speech="", ui_card=card, from_history=history)


def test_a_suspension_without_its_own_card_shows_the_turns_single_list():
    """「已找到 5 个地点。您想看哪一家的详情？」——问的就是那份列表，不上屏用户没法挑（`70b179b7` NB06 一遍）。交互卡独显。"""
    results = [_ok("s1", _LIST), _ok("s3", _INFO)]
    assert result_bundle.suspension_list(None, results) == ("s1", _LIST)
    assert result_bundle.suspension_cards(None, results) is _LIST


def test_the_pending_steps_own_card_stays_the_authority():
    own = {"type": "merchant_choices", "purpose": "store_choice"}
    assert result_bundle.suspension_list(own, [_ok("s1", _LIST)]) is None
    assert result_bundle.suspension_cards(own, [_ok("s1", _LIST)]) is own


def test_two_interactive_cards_or_a_carried_over_one_are_not_guessed_between():
    """两张交互卡分不清问的是哪份；上一轮带进来的（`from_history`）用户这一轮没看到——都照旧只附信息卡。"""
    assert result_bundle.suspension_list(None, [_ok("s1", _LIST), _ok("s2", _ROUTE)]) is None
    assert result_bundle.suspension_cards(None, [_ok("s1", _LIST), _ok("s2", _ROUTE), _ok("s3", _INFO)]) is _INFO
    assert result_bundle.suspension_list(None, [_ok("s1", _LIST, history=True)]) is None
    # 同一张卡在「本轮未播报」与「全部结果」里各出现一次（`_suspend` 的 public_results）不算两张
    assert result_bundle.suspension_list(None, [_ok("s1", _LIST), _ok("s1", dict(_LIST))]) == ("s1", _LIST)

