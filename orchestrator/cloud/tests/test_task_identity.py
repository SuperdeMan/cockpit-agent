"""Task identity survives retries/resumption without pretending a source reference proves coverage."""
import asyncio
import copy

import pytest

from orchestrator.cloud.models import Plan, PlanContext, SessionState, Step
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.context import _task_frame
from orchestrator.cloud.step_input import bind_step_inputs, source_hash
from orchestrator.cloud.task_identity import bind_task_identity, restore_task_identity, task_record


def make_plan(text="深圳天气怎么样"):
    p = Plan([Step("s1", "info", intent="info.weather", origin_text=text)],
             raw_text=text, safety_origin_text=text, origin_exchange_id="turn-1")
    bind_step_inputs(p)
    return p


def test_model_goal_and_covers_cannot_create_a_covered_goal():
    p = make_plan()
    p.goals = ["打开后备箱"]
    p.steps[0].covers = [1]
    bind_task_identity(p, PlanContext())
    assert len(p.goal_refs) == 1
    ref = p.goal_refs[0]
    assert ref["coverage"] == "unknown"
    assert ref["source_sha256"] == source_hash("深圳天气怎么样")
    assert p.steps[0].goal_ids == [ref["goal_id"]]
    assert "打开后备箱" not in str(task_record(p))


def test_serialize_restore_preserves_task_goals_and_server_origin():
    p = make_plan()
    bind_task_identity(p, PlanContext())
    record = PlannerEngine._serialize_plan(p)
    restored, _ = PlannerEngine._restore(None, SessionState(
        phase="wait_slot", pending_step_id="s1", pending_plan=record), inject_confirmed=False)
    bind_task_identity(restored, PlanContext(request_id="new-turn"))
    assert task_record(restored) == task_record(p)
    assert restored.origin_exchange_id == "turn-1"
    assert restored.steps[0].goal_ids == p.steps[0].goal_ids
    assert not restored.steps[0].meta.get("confirmed")


def test_t2_batches_and_reactive_upgrade_share_identity_and_advance_revision():
    ctx = PlanContext()
    p = make_plan()
    bind_task_identity(p, ctx)
    q, r = make_plan(), make_plan()
    bind_task_identity(q, ctx, continuation=True)
    bind_task_identity(r, ctx, continuation=True)
    assert p.task_id == q.task_id == r.task_id
    assert (p.plan_revision, q.plan_revision, r.plan_revision) == (1, 2, 3)
    assert p.goal_refs == q.goal_refs == r.goal_refs


def test_explicit_correction_reuses_goal_identity_and_updates_its_provenance():
    ctx = PlanContext()
    p = make_plan()
    bind_task_identity(p, ctx)
    frame = _task_frame(p, p.steps[0], "completed", "read")
    q = make_plan("我问的是广州天气")
    q.origin_exchange_id = "turn-2"
    q.acts = ["correct"]
    from types import SimpleNamespace
    PlannerEngine._apply_task_patch(q, SimpleNamespace(active_task=frame))
    bind_task_identity(q, PlanContext())
    assert q.task_id == p.task_id and q.plan_revision == 2
    assert q.steps[0].goal_ids == p.steps[0].goal_ids
    assert q.goal_refs[0]["source_sha256"] == source_hash(q.safety_origin_text)
    assert q.goal_refs[0]["origin_exchange_id"] == "turn-2"
    assert p.goal_refs[0]["origin_exchange_id"] == "turn-1"


def test_legacy_correction_has_no_invented_goal_coverage():
    p = make_plan()
    p.task_patch = {"task_id": "legacy-task", "revision": 2}
    bind_task_identity(p, PlanContext())
    assert p.task_id == "legacy-task" and p.plan_revision == 2
    assert p.goal_refs == p.steps[0].goal_ids == []


def test_identical_text_in_independent_requests_does_not_share_identity():
    a, b = make_plan(), make_plan()
    bind_task_identity(a, PlanContext(user_id="a"))
    bind_task_identity(b, PlanContext(user_id="b"))
    assert a.task_id != b.task_id
    assert a.steps[0].goal_ids != b.steps[0].goal_ids


@pytest.mark.parametrize("patch", [{"plan_revision": True}, {"plan_revision": 0},
                                   {"goal_refs": [None]}, {"task_id": 123}])
def test_bad_new_record_does_not_restore_as_a_valid_identity(patch):
    p = make_plan()
    bind_task_identity(p, PlanContext())
    with pytest.raises(ValueError):
        restore_task_identity(make_plan(), {**task_record(p), **patch})


def test_a_legacy_record_is_readable_without_manufactured_goals():
    p = Plan([Step("s1", "info")], raw_text="旧请求", safety_origin_text="旧请求")
    restore_task_identity(p, {})
    bind_task_identity(p, PlanContext(request_id="later-turn"))
    assert not p.task_id and not p.goal_refs and not p.steps[0].goal_ids


def test_invalid_business_scope_does_not_create_provenance_association():
    p = make_plan()
    p.steps[0].input_scope["spans"] = [None]
    bind_task_identity(p, PlanContext())
    assert p.steps[0].goal_ids == []


def test_actual_slot_then_confirmation_resuspension_retains_task_and_goals():
    from tests.test_step_origin_text import _make_engine, _req, _run, ORIGIN
    engine, _, session = _make_engine()
    assert not _run(engine, _req(ORIGIN))[-1].get("need_confirm")
    first = asyncio.run(session.load("sess-1", owner_user_id="u1"))
    saved = copy.deepcopy(first.pending_plan)
    assert _run(engine, _req("川菜"))[-1]["need_confirm"]
    second = asyncio.run(session.load("sess-1", owner_user_id="u1"))
    assert second.pending_plan["task_id"] == saved["task_id"]
    assert second.pending_plan["plan_revision"] == saved["plan_revision"]+1
    assert second.pending_plan["goal_refs"] == saved["goal_refs"]
    assert second.operation_id != first.operation_id
