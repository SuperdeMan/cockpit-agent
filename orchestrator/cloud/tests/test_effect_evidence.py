"""CA2-10 effect evidence: acknowledgement, state, attribution and verification stay separate.

The legacy verdict (sat/unsat/unknown) must not move: the old state judgment is kept
here verbatim as an oracle. Evidence is a second record, identical across T1, D0 and T2.
"""
from __future__ import annotations

import asyncio
import itertools

import pytest
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct

from cockpit.agent.v1 import agent_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

from orchestrator.cloud import result_bundle
from orchestrator.cloud import verify as V
from orchestrator.cloud.clients import Clients
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.loop import LoopController, summarize
from orchestrator.cloud.models import (Plan, PlanContext, ReplanDecision, Step, StepResult,
                                       StepStatus, step_record)
from runtime import effect_evidence as E

_EXPECT = {"keys": {"hvac_on": "true"}}
_SET_EXPECT = {"keys": {"hvac_on": "true", "hvac_temp": "$slot:temperature"}}
_REF = "a" * 32


def _signal(value=None, *, quality="good", ref="", kind="simulated", auth=False):
    return {"quality": quality, "operation_id": ref, "source_kind": kind, "authenticated": auth,
            "observed_at_ms": 1}


def _view(**signals):
    state = {k: v[0] for k, v in signals.items() if v[1]["quality"] == "good"}
    return {"state": state, "signals": {k: v[1] for k, v in signals.items()}}


def _assess(expect, view, **kw):
    keys = V.resolve_expect_keys(expect.get("keys"), kw.pop("slots", None))
    return V.assess(keys, view, **kw)


# ── 四个事实的组合表 ───────────────────────────────────────────────────────

def test_attributed_change_is_verified():
    receipt = E.read_receipt({E.RECEIPT: E.make_receipt(_REF, ["hvac_on"])})
    evidence, details, pending = _assess(
        _EXPECT, _view(hvac_on=(True, _signal(ref=_REF))), ref=_REF, receipt=receipt)
    assert evidence == {"version": 1, "ack": "acknowledged", "state": "satisfied", "observed": "attributed",
                        "verified": True, "reasons": [], "source_kind": "simulated", "authenticated": False}
    assert details["hvac_on"]["attributed"] and not pending


def test_target_satisfied_before_the_action_is_never_verified():
    """动作前就已满足：回执说没改任何期望键，状态满足也不能伪造因果。"""
    receipt = E.read_receipt({E.RECEIPT: E.make_receipt(_REF, [])})
    evidence, _, pending = _assess(_EXPECT, _view(hvac_on=(True, _signal())), ref=_REF, receipt=receipt)
    assert (evidence["state"], evidence["observed"], evidence["verified"]) == ("satisfied", "unchanged", False)
    assert evidence["reasons"] == ["already_satisfied"] and not pending
    assert V.verdict_of(evidence) == V.SAT                       # 原结论不变：状态确实满足


def test_partly_preexisting_target_is_verified_by_the_changed_key():
    receipt = E.read_receipt({E.RECEIPT: E.make_receipt(_REF, ["hvac_on"])})
    evidence, details, _ = _assess(
        _SET_EXPECT, _view(hvac_on=(True, _signal(ref=_REF)), hvac_temp=(26, _signal())),
        ref=_REF, receipt=receipt, slots={"temperature": "26"})
    assert evidence["verified"] and evidence["observed"] == "attributed"
    assert details["hvac_temp"] == {"verdict": "sat", "attributed": False, "observed_at_ms": 1}


def test_changed_but_never_observed_is_missing_and_still_pending():
    receipt = E.read_receipt({E.RECEIPT: E.make_receipt(_REF, ["hvac_on"])})
    evidence, _, pending = _assess(_EXPECT, _view(hvac_on=(False, _signal())), ref=_REF, receipt=receipt)
    assert (evidence["state"], evidence["observed"], evidence["verified"]) == ("unsatisfied", "missing", False)
    assert evidence["reasons"] == ["observation_missing", "value_mismatch"] and pending


@pytest.mark.parametrize("quality,reason", [("stale", "signal_stale"), ("uncertain", "signal_uncertain"),
                                            ("unavailable", "signal_unavailable")])
def test_unknown_names_the_signal_quality(quality, reason):
    evidence, _, _ = _assess(_EXPECT, _view(hvac_on=(True, _signal(quality=quality))))
    assert evidence["state"] == "unknown" and reason in evidence["reasons"]
    assert V.verdict_of(evidence) == V.UNKNOWN


def test_unknown_names_missing_signal_unresolved_slot_and_blind_mirror():
    assert "signal_missing" in _assess(_EXPECT, _view())[0]["reasons"]
    unresolved = _assess(_SET_EXPECT, _view(hvac_on=(True, _signal())), slots={})[0]
    assert unresolved["state"] == "unknown" and "expectation_unresolved" in unresolved["reasons"]
    blind = _assess(_EXPECT, None)[0]
    assert blind["state"] == "unknown" and blind["reasons"] == ["mirror_unavailable", "not_attributed"]
    assert "expectation_unresolved" in V.assess({}, _view())[0]["reasons"]


def test_lost_acknowledgement_does_not_hide_an_attributed_effect():
    evidence, _, _ = _assess(_EXPECT, _view(hvac_on=(True, _signal(ref=_REF))), ref=_REF,
                             receipt=None, ack=E.ACK_UNKNOWN)
    assert (evidence["ack"], evidence["verified"]) == ("unknown", True)
    assert evidence["reasons"] == ["ack_lost"]


def test_attribution_needs_this_steps_reference_and_a_tagging_executor():
    tagged_elsewhere = _view(hvac_on=(True, _signal(ref="b" * 32)))
    assert _assess(_EXPECT, tagged_elsewhere, ref=_REF)[0]["observed"] == "unattributed"
    no_echo = E.read_receipt({E.RECEIPT: E.make_receipt("", ["hvac_on"])})
    evidence = _assess(_EXPECT, _view(hvac_on=(True, _signal(ref=_REF))), ref=_REF, receipt=no_echo)[0]
    assert evidence["observed"] == "unattributed" and evidence["reasons"] == ["not_attributed"]
    assert not evidence["verified"]


def test_source_is_the_weakest_and_authentication_needs_every_signal():
    both = _view(hvac_on=(True, _signal(kind="vehicle", auth=True)), hvac_temp=(26, _signal(kind="simulated")))
    evidence = _assess(_SET_EXPECT, both, slots={"temperature": "26"})[0]
    assert (evidence["source_kind"], evidence["authenticated"]) == ("simulated", False)
    signed = _assess(_EXPECT, _view(hvac_on=(True, _signal(kind="vehicle", auth=True))))[0]
    assert (signed["source_kind"], signed["authenticated"]) == ("vehicle", True)


# ── 原结论一个值都不许动：旧判据原样留作对照 ──────────────────────────────

def _legacy_eval_state_match(expect, snapshot, slots=None):
    keys = V.resolve_expect_keys(expect.get("keys"), slots)
    if not keys:
        return V.UNKNOWN
    if not snapshot:
        return V.UNKNOWN
    unknown = False
    for k, want in keys.items():
        if want is V.UNRESOLVED:
            unknown = True
            continue
        if k not in snapshot or snapshot[k] is None:
            unknown = True
            continue
        if not V._values_equal(snapshot[k], want):
            return V.UNSAT
    return V.UNKNOWN if unknown else V.SAT


def test_legacy_verdict_is_unchanged_over_the_whole_matrix():
    expects = [_EXPECT, _SET_EXPECT, {"keys": {"window": "open", "volume": 30}}, {"keys": {}}, {}]
    values = [None, True, False, "true", 26, "26", 20, "open", "closed", 30, "30.0"]
    keys = ["hvac_on", "hvac_temp", "window", "volume"]
    slot_sets = [None, {}, {"temperature": "26"}, {"temperature": ""}]
    checked = 0
    for expect, slots in itertools.product(expects, slot_sets):
        for combo in itertools.product([False, True], repeat=len(keys)):
            for picks in itertools.islice(itertools.product(values, repeat=sum(combo)), 40):
                present = [k for k, on in zip(keys, combo) if on]
                snapshot = dict(zip(present, picks))
                assert V.eval_state_match(expect, snapshot, slots) == \
                    _legacy_eval_state_match(expect, snapshot, slots), (expect, snapshot, slots)
                checked += 1
    assert checked > 1000


# ── 轮询 ─────────────────────────────────────────────────────────────────

class _ViewMirror:
    def __init__(self, *views):
        self.views = list(views)

    def view(self, vehicle_id=None):
        return self.views.pop(0) if len(self.views) > 1 else self.views[0]


def test_polling_waits_for_the_attributed_sample(monkeypatch):
    monkeypatch.setattr(V, "_POLL_INTERVAL_S", 0.001)
    receipt = E.read_receipt({E.RECEIPT: E.make_receipt(_REF, ["hvac_on"])})
    mirror = _ViewMirror(_view(hvac_on=(False, _signal())), _view(hvac_on=(True, _signal(ref=_REF))))
    verdict, evidence, _ = asyncio.run(V.evaluate_effect(
        {"mode": "state_match", "timeout_ms": 500, "expect": _EXPECT}, mirror, ref=_REF, receipt=receipt))
    assert verdict == V.SAT and evidence["verified"]


def test_a_satisfied_value_does_not_stop_the_wait_for_its_attribution(monkeypatch):
    """回执说改了、值也对了，但带关联键的样本还没到：继续等，不能提前收工判成「未核实」。"""
    monkeypatch.setattr(V, "_POLL_INTERVAL_S", 0.001)
    receipt = E.read_receipt({E.RECEIPT: E.make_receipt(_REF, ["hvac_on"])})
    mirror = _ViewMirror(_view(hvac_on=(True, _signal())), _view(hvac_on=(True, _signal(ref=_REF))))
    _, evidence, _ = asyncio.run(V.evaluate_effect(
        {"mode": "state_match", "timeout_ms": 500, "expect": _EXPECT}, mirror, ref=_REF, receipt=receipt))
    assert evidence["verified"]


def test_polling_gives_up_at_the_declared_deadline(monkeypatch):
    monkeypatch.setattr(V, "_POLL_INTERVAL_S", 0.001)
    receipt = E.read_receipt({E.RECEIPT: E.make_receipt(_REF, ["hvac_on"])})
    verdict, evidence, _ = asyncio.run(V.evaluate_effect(
        {"mode": "state_match", "timeout_ms": 20, "expect": _EXPECT},
        _ViewMirror(_view(hvac_on=(True, _signal()))), ref=_REF, receipt=receipt))
    assert verdict == V.SAT and evidence["observed"] == "missing" and not evidence["verified"]


# ── Step 的关联键：每次派发一枚，不持久化，客户端给不了 ─────────────────────

def _edge_step(**over):
    kw = {"id": "s1", "agent_id": "edge-vehicle", "deployment": "edge", "intent": "hvac.on",
          "latency_budget_ms": 2000, "verification": {"mode": "state_match", "timeout_ms": 300,
                                                       "on_fail": "report", "expect": _EXPECT}}
    kw.update(over)
    return Step(**kw)


def test_state_match_steps_carry_a_fresh_reference_per_construction():
    step = _edge_step(meta={E.META: "f" * 32})
    assert E.valid_ref(step.observation_ref) and step.observation_ref != "f" * 32
    assert step.meta[E.META] == step.observation_ref
    record = step_record(step)
    assert "observation_ref" not in record and E.META not in str(record)
    restored = Step(**record)
    assert E.valid_ref(restored.observation_ref) and restored.observation_ref != step.observation_ref


def test_other_steps_carry_no_reference():
    step = Step(id="s1", agent_id="info", intent="info.weather", meta={E.META: _REF},
                verification={"mode": "schema", "expect": {"data_keys": ["items"]}})
    assert step.observation_ref == "" and E.META not in step.meta


def test_clients_cannot_supply_the_reference():
    step = _edge_step()
    ctx = PlanContext(prefs={E.META: _REF})
    assert E.META not in Clients._merge_meta(ctx, {})
    assert Clients._merge_meta(ctx, step.meta)[E.META] == step.observation_ref


# ── 一辆假车：读到下发的关联键，盖在改动的观测上，并回回执 ─────────────────

class _Vehicle:
    """state 是车的真实值；view 是云端镜像看到的（带每键关联键）。"""

    def __init__(self, hvac_on=False, *, publish=True, tag=True):
        self.state = {"hvac_on": hvac_on}
        self.signals = {"hvac_on": _signal()}
        self.publish, self.tag = publish, tag
        self.metas = []

    def view(self, vehicle_id=None):
        return {"state": dict(self.state) if self.publish or not self.metas else {"hvac_on": not self.state["hvac_on"]},
                "signals": {k: dict(v) for k, v in self.signals.items()}}

    def execute(self, meta):
        self.metas.append(dict(meta))
        ref = meta.get(E.META, "") if self.tag else ""
        changed = [] if self.state["hvac_on"] else ["hvac_on"]
        self.state["hvac_on"] = True
        if changed and self.publish:
            self.signals["hvac_on"] = _signal(ref=ref)
        data = Struct()
        data.update({"intent": "hvac.on", "executed": True, E.RECEIPT: E.make_receipt(ref, changed)})
        return agent_pb2.ExecuteResponse(status=0, speech="已打开空调", data=data)


class _VehicleDispatcher:
    def __init__(self, vehicle):
        self.vehicle = vehicle

    async def dispatch(self, step, ctx):
        return self.vehicle.execute(step.meta)


def _t1(vehicle):
    executor = DagExecutor(dispatcher=_VehicleDispatcher(vehicle), state_mirror=vehicle)

    async def go():
        return [r async for r in executor.run(Plan(steps=[_edge_step()]), PlanContext(vehicle_id="v1"))][0]
    return asyncio.run(go())


def _d0(vehicle):
    class _Clients:
        async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None, context_scopes=None):
            yield ("final", vehicle.execute(meta or {}))

    engine = PlannerEngine.__new__(PlannerEngine)
    engine.clients = _Clients()
    engine.executor = DagExecutor(call_agent_fn=_unused, state_mirror=vehicle)
    sink: dict = {}

    async def go():
        async for _ in engine._stream_single_step(_edge_step(), PlanContext(vehicle_id="v1"), False, sink):
            pass
        return sink["final_sr"]
    return asyncio.run(go())


def _t2(vehicle):
    class _Planner:
        async def replan(self, *args, **kwargs):
            return ReplanDecision(done=True)

    class _Aggregator:
        results = None

        async def compose(self, text, results, **kwargs):
            _Aggregator.results = list(results)
            return {"speech": "好", "actions": [], "cards": []}

    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30, context_scopes=None):
        yield ("final", vehicle.execute(meta or {}))

    executor = DagExecutor(call_agent_fn=_unused, state_mirror=vehicle)
    controller = LoopController(_Planner(), executor, _Aggregator(), None, max_iters=2,
                                budget_ms=5000, stream_fn=stream_fn)

    async def go():
        step = _edge_step(deployment="cloud", kind="agent")
        async for _ in controller.run(goal="开空调", initial_plan=Plan(steps=[step], complexity="adaptive"),
                                      agents=[], ctx=PlanContext(vehicle_id="v1"), user_text="打开空调"):
            pass
        return _Aggregator.results[0]
    return asyncio.run(go())


async def _unused(*_args, **_kwargs):      # pragma: no cover - the stream must be used
    raise AssertionError("unary fallback must not run")


@pytest.mark.parametrize("path", [_t1, _d0, _t2], ids=["T1", "D0", "T2"])
def test_every_execution_path_produces_the_same_evidence(path, monkeypatch):
    monkeypatch.setattr(V, "_POLL_INTERVAL_S", 0.001)
    fresh = path(_Vehicle())
    assert fresh.data[E.EVIDENCE]["verified"] and fresh.data[E.EVIDENCE]["observed"] == "attributed"
    already = path(_Vehicle(hvac_on=True))
    assert already.data[E.EVIDENCE]["observed"] == "unchanged" and not already.data[E.EVIDENCE]["verified"]
    lost = path(_Vehicle(publish=False))
    assert lost.data[E.EVIDENCE]["observed"] == "missing"
    untagged = path(_Vehicle(tag=False))
    assert untagged.data[E.EVIDENCE]["observed"] == "unattributed"
    for result in (fresh, already, lost, untagged):
        assert "_verify" not in result.data or result.data["_verify"]["verdict"] == "unsat"


def test_paths_agree_value_for_value(monkeypatch):
    monkeypatch.setattr(V, "_POLL_INTERVAL_S", 0.001)
    for make in (lambda: _Vehicle(), lambda: _Vehicle(hvac_on=True), lambda: _Vehicle(publish=False)):
        records = [path(make()).data[E.EVIDENCE] for path in (_t1, _d0, _t2)]
        assert records[0] == records[1] == records[2]


def test_the_reference_reaches_the_executor_on_every_path(monkeypatch):
    monkeypatch.setattr(V, "_POLL_INTERVAL_S", 0.001)
    for path in (_t1, _d0, _t2):
        vehicle = _Vehicle()
        path(vehicle)
        assert E.valid_ref(vehicle.metas[0][E.META])


# ── 回执丢失的两条路：超时翻案、流断在 final 之前 ────────────────────────────

def test_timed_out_step_with_an_attributed_effect_is_verified_without_ack(monkeypatch):
    monkeypatch.setattr(V, "_POLL_INTERVAL_S", 0.001)
    step = _edge_step()
    mirror = _ViewMirror(_view(hvac_on=(True, _signal(ref=step.observation_ref))))
    failed = StepResult(step_id="s1", status=StepStatus.FAILED, error="step_timeout")
    out = asyncio.run(DagExecutor(call_agent_fn=_unused, state_mirror=mirror)
                      ._verify_outcome(step, failed, PlanContext(vehicle_id="v1")))
    assert out.status == StepStatus.FAILED and out.data["_verify"]["exec"] == "uncertain_confirmed"
    assert (out.data[E.EVIDENCE]["ack"], out.data[E.EVIDENCE]["verified"]) == ("unknown", True)


def test_timed_out_step_keeps_unknown_evidence_without_changing_the_failure(monkeypatch):
    monkeypatch.setattr(V, "_POLL_INTERVAL_S", 0.001)
    mirror = _ViewMirror(_view(hvac_on=(False, _signal())))
    failed = StepResult(step_id="s1", status=StepStatus.FAILED, error="step_timeout")
    out = asyncio.run(DagExecutor(call_agent_fn=_unused, state_mirror=mirror)
                      ._verify_outcome(_edge_step(), failed, PlanContext(vehicle_id="v1")))
    assert "_verify" not in out.data and out.data[E.EVIDENCE]["state"] == "unsatisfied"
    assert "ack_lost" in out.data[E.EVIDENCE]["reasons"]


def test_stream_lost_final_reports_evidence(monkeypatch):
    monkeypatch.setattr(V, "_POLL_INTERVAL_S", 0.001)
    step = _edge_step()
    mirror = _ViewMirror(_view(hvac_on=(True, _signal(ref=step.observation_ref))))
    out = asyncio.run(DagExecutor(call_agent_fn=_unused, state_mirror=mirror)
                      .stream_uncertain_result(step, PlanContext(vehicle_id="v1")))
    assert out.data[E.EVIDENCE]["ack"] == "unknown" and out.data[E.EVIDENCE]["verified"]


# ── 回执与证据的边界：执行方造不了证据，回放不继承，规划看不到 ────────────────

def test_an_executor_cannot_supply_evidence():
    data = Struct()
    data.update({E.EVIDENCE: {"version": 1, "verified": True}, "x": 1})
    result = DagExecutor._to_result("s1", agent_pb2.ExecuteResponse(status=0, data=data))
    assert E.EVIDENCE not in result.data and result.data["x"] == 1


def test_duplicate_replay_does_not_inherit_the_prior_evidence():
    prior = StepResult(step_id="s0", status=StepStatus.OK, speech="已打开", actions=[{"type": "x"}],
                       data={E.EVIDENCE: {"verified": True}, E.RECEIPT: {}, "k": 1}, fingerprint="fp")
    replay = asyncio.run(DagExecutor(call_agent_fn=_unused)._replay_prior(_edge_step(), prior, PlanContext()))
    assert replay.data == {"k": 1}


def test_t2_observations_do_not_show_receipts_or_evidence():
    result = StepResult(step_id="s1", status=StepStatus.OK,
                        data={"intent": "hvac.on", E.RECEIPT: {}, E.EVIDENCE: {}})
    assert summarize(result)["data"] == {"intent": "hvac.on"}


# ── ResultBundle：公开证据、明示 unknown、序列化 ──────────────────────────────

def _ctx_with(step):
    ctx = PlanContext()
    ctx.task_identity = {"task_id": "t1", "plan_revision": 1, "goal_refs": []}
    result_bundle.register_plan(ctx, Plan(steps=[step]))
    return ctx


def test_bundle_rows_carry_public_evidence_and_serialize():
    step = _edge_step()
    evidence = E.summarize(ack="acknowledged", state="satisfied", observed="unchanged",
                           reasons=["already_satisfied"], source_kind="simulated")
    result = StepResult(step_id="s1", status=StepStatus.OK, speech="空调已经是打开状态了",
                        data={E.EVIDENCE: evidence})
    bundle = result_bundle.build(_ctx_with(step), [result], {"speech": "空调已经是打开状态了"})
    row = bundle["results"][0]
    assert row["verification"] == "sat"
    assert row["evidence"] == {"ack": "acknowledged", "state": "satisfied", "observed": "unchanged",
                               "verified": False, "reasons": ["already_satisfied"],
                               "source_kind": "simulated", "authenticated": False}
    final = orchestrator_pb2.FinalResult()
    ParseDict(bundle, final.result_bundles.add())
    assert final.result_bundles[0].results[0].evidence.observed == "unchanged"


def test_bundle_drops_inconsistent_or_absent_evidence():
    step = _edge_step()
    forged = dict(E.summarize(ack="acknowledged", state="satisfied", observed="unchanged"), verified=True)
    rows = [result_bundle.build(_ctx_with(step), [StepResult(step_id="s1", status=StepStatus.OK, speech="好",
                                                             data=data)], {})["results"][0]
            for data in ({E.EVIDENCE: forged}, {}, {E.EVIDENCE: {"version": 2}})]
    assert all("evidence" not in row for row in rows)
    assert rows[1]["verification"] == "unknown"


def test_actions_dispatched_after_the_reply_say_why_they_are_unknown():
    step = Step(id="s1", agent_id="scene", intent="scene.activate")
    result = StepResult(step_id="s1", status=StepStatus.OK, speech="已开启",
                        actions=[{"type": "vehicle.control", "payload": {"command": "hvac.on"}}])
    row = result_bundle.build(_ctx_with(step), [result], {"speech": "已开启"})["results"][0]
    assert row["pending_edge"] and row["status"] == "unknown"
    assert row["evidence"]["reasons"] == ["dispatched_after_reply"] and not row["evidence"]["verified"]
