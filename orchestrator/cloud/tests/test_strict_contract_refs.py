"""CA2-19 S4：严格契约下，引用填出、接收方收不下的槽派发前丢掉；模型直接写的未声明字面值仍交契约拒绝。"""
from __future__ import annotations

from pathlib import Path

from agents._sdk.manifest import load_manifest
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import Step, StepResult, StepStatus
from runtime.capability_contract import argument_error, step_fields

_NAV = load_manifest(str(Path(__file__).resolve().parents[3] / "agents" / "navigation" / "manifest.yaml"))
_DONE = {"s1": StepResult(step_id="s1", status=StepStatus.OK,
                          data={"items": [{"id": "poi-1", "name": "灯花·川小馆", "address": "科技园路 1 号"}]})}


def _navigate(**kw) -> Step:
    cap = next(c for c in _NAV.capabilities if c.intent == "navigation.navigate_to")
    return Step(id="s2", agent_id="navigation", intent="navigation.navigate_to", **step_fields(_NAV, cap), **kw)


def _resolve(step: Step) -> Step:
    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(step, _DONE)
    return step


def test_minimax_alias_carrier_is_dropped_for_a_strict_contract():
    """真栈形态 `destination="$ref.poi_id"` + slot_refs.poi_id：值进了 destination，载体 poi_id 导航不收。"""
    step = _resolve(_navigate(slots={"destination": "$ref.poi_id"}, slot_refs={"poi_id": "s1.data.items.0.id"}))
    assert step.slots == {"destination": "poi-1"}
    assert argument_error(step.capability_contract, step.slots) == ""


def test_ref_filled_slots_outside_the_contract_are_dropped_declared_ones_kept():
    """2026-07-31 真栈形态：占位填进声明的槽，另带一串指向同一列表项的 name / address / poi_id 引用。"""
    step = _resolve(_navigate(
        slots={"destination": "${s1.data.items.0.name}", "place_address": "${s1.data.items.0.address}"},
        slot_refs={"name": "s1.data.items.0.name", "address": "s1.data.items.0.address",
                   "poi_id": "s1.data.items.0.id"}))
    assert step.slots == {"destination": "灯花·川小馆", "place_address": "科技园路 1 号"}
    assert argument_error(step.capability_contract, step.slots) == ""


def test_a_literal_the_model_wrote_outside_the_contract_is_still_rejected():
    """可能带着用户说的约束（「不走高速」写成了契约外的键）——不能悄悄丢，交派发前的契约校验拒绝。"""
    step = _resolve(_navigate(slots={"destination": "机场", "avoid_highway": "true"}))
    assert step.slots["avoid_highway"] == "true"
    assert argument_error(step.capability_contract, step.slots) == "unexpected_parameter"


def test_steps_without_a_strict_contract_keep_their_carriers():
    step = _resolve(Step(id="s2", agent_id="navigation", slots={"destination": "$ref.poi_id"},
                         slot_refs={"poi_id": "s1.data.items.0.id"}))
    assert step.slots == {"destination": "poi-1", "poi_id": "poi-1"}


def test_a_literal_alias_carrier_is_dropped_once_its_value_moved_into_a_declared_slot():
    """`$ref.` 指向本步一个字面值：值已经搬进 destination，载体只是线格式中间物（不是丢掉用户说的约束）。"""
    step = _resolve(_navigate(slots={"poi_id": "B0FFTEST01", "destination": "$ref.poi_id"}))
    assert step.slots == {"destination": "B0FFTEST01"}
    assert argument_error(step.capability_contract, step.slots) == ""
