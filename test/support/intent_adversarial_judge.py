"""对抗用例裁判：把一轮决策快照与契约期望比对，产出断言与指标。

判定口径的三条硬规矩：

1. **必要组之间 AND、组内 OR**，默认不允许未声明的额外 intent——「命中任一期望域」是
   历史趋势口径（`routing_bench.domain_hit_rate`），证明不了组合计划完整。
2. **goal 不计分**。goal 说要推荐、steps 只拆了查天气，是真实发生过的缺陷；把 goal 当
   计划完整性的替代物就永远看不见它。
3. **relation 与 absolute 分开算**。只声明「和 base 一样」的用例，两个一起错也会绿。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from support.intent_adversarial_contract import (
    PlanExpectation, RetrievalExpectation, TurnExpectation,
)


@dataclass(frozen=True)
class StepSnapshot:
    id: str
    agent_id: str
    intent: str
    slots: dict[str, Any]
    depends_on: tuple[str, ...]
    slot_refs: dict[str, str]
    require_confirm: bool


@dataclass(frozen=True)
class PlanSnapshot:
    steps: tuple[StepSnapshot, ...]
    complexity: str
    goal: str
    skills: tuple[str, ...]
    exemplars: tuple[str, ...]
    hint_effect: str
    catalog_stats: dict[str, Any]
    raw_llm: str = ""
    plan_mode: str = ""

    @classmethod
    def empty(cls) -> "PlanSnapshot":
        return cls((), "", "", (), (), "", {})

    @property
    def intents(self) -> tuple[str, ...]:
        return tuple(step.intent for step in self.steps)


@dataclass(frozen=True)
class DecisionSnapshot:
    ingress: str
    addressed: bool
    decision: str
    clarify: bool
    plan: PlanSnapshot
    replans: tuple[PlanSnapshot, ...] = ()
    side_effects: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AssertionResult:
    name: str
    passed: bool
    expected: Any
    actual: Any
    detail: str = ""


@dataclass
class TurnJudgement:
    assertions: list[AssertionResult] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.assertions)

    def metric(self, name: str) -> float:
        return self.metrics[name]


def _assert(out: TurnJudgement, name: str, passed: bool,
            expected: Any, actual: Any, detail: str = "") -> None:
    out.assertions.append(AssertionResult(name, passed, expected, actual, detail))


def _retrieved_name(value: str) -> str:
    """检索名单形如 `full:weather-outing@lex:1.0!clipped`——只取资产名做精确判定。"""
    value = str(value or "")
    head, sep, tail = value.partition(":")
    if sep and head in {"full", "shadow", "canary", "lexical", "hybrid"}:
        value = tail
    return value.split("!", 1)[0].split("@", 1)[0]


def judge_plan(expected: PlanExpectation, actual: PlanSnapshot,
               out: TurnJudgement, prefix: str = "plan") -> None:
    intents = set(actual.intents)
    hits = [bool(intents & set(group.any_of)) for group in expected.required_groups]
    recall = sum(hits) / len(hits) if hits else 1.0
    out.metrics[f"{prefix}.required_group_recall"] = recall
    out.metrics[f"{prefix}.required_group_hits"] = float(sum(hits))
    out.metrics[f"{prefix}.required_group_total"] = float(len(hits))
    _assert(out, f"{prefix}.required_groups", all(hits),
            [group.any_of for group in expected.required_groups], sorted(intents))

    forbidden = sorted(intents & set(expected.forbidden_intents))
    out.metrics[f"{prefix}.forbidden_route_count"] = float(len(forbidden))
    _assert(out, f"{prefix}.forbidden_intents", not forbidden,
            expected.forbidden_intents, forbidden)

    allowed = {i for group in expected.required_groups for i in group.any_of}
    allowed.update(expected.allowed_extra_intents)
    extras = [] if expected.allow_extra_intents else sorted(intents - allowed)
    out.metrics[f"{prefix}.overroute_count"] = float(len(extras))
    _assert(out, f"{prefix}.extra_intents", not extras,
            "any" if expected.allow_extra_intents else sorted(allowed), extras)

    if expected.allowed_complexities:
        _assert(out, f"{prefix}.complexity",
                actual.complexity in expected.allowed_complexities,
                expected.allowed_complexities, actual.complexity)

    by_intent: dict[str, list[StepSnapshot]] = {}
    for step in actual.steps:
        by_intent.setdefault(step.intent, []).append(step)
    dependency_results: list[bool] = []
    for dep in expected.dependencies:
        producer_ids = {s.id for intent in dep.producer for s in by_intent.get(intent, [])}
        consumers = by_intent.get(dep.consumer, [])
        # all()：一条正确接线不能掩盖同 intent 的第二条没接线的步。
        ok = bool(consumers) and all(
            bool(producer_ids & set(step.depends_on))
            and all(
                key in step.slot_refs
                and str(step.slot_refs[key]).split(".", 1)[0] in producer_ids
                for key in dep.carries)
            for step in consumers)
        dependency_results.append(ok)
        _assert(out, f"{prefix}.dependency:{dep.consumer}", ok, dep,
                [(s.id, s.depends_on, s.slots, s.slot_refs) for s in consumers])
    out.metrics[f"{prefix}.dependency_hits"] = float(sum(dependency_results))
    out.metrics[f"{prefix}.dependency_total"] = float(len(dependency_results))

    for slot in expected.slots:
        steps = by_intent.get(slot.intent, [])
        rows = [
            (slot.key in step.slots, step.slots.get(slot.key),
             slot.key in step.slot_refs, step.slot_refs.get(slot.key))
            for step in steps]
        values = [value for has_value, value, _, _ in rows if has_value]
        refs = [ref for _, _, has_ref, ref in rows if has_ref]
        if slot.matcher == "presence":
            ok = bool(rows) and all(has_value or has_ref
                                    for has_value, _, has_ref, _ in rows)
        elif slot.matcher == "absence":
            ok = bool(rows) and all(not has_value and not has_ref
                                    for has_value, _, has_ref, _ in rows)
        elif slot.matcher == "exact":
            ok = bool(rows) and all(has_value and value == slot.value
                                    for has_value, value, _, _ in rows)
        elif slot.matcher == "one_of":
            ok = bool(rows) and all(has_value and str(value) in slot.allowed
                                    for has_value, value, _, _ in rows)
        elif slot.matcher == "range":
            try:
                numbers = [float(value) for has_value, value, _, _ in rows
                           if has_value]
                ok = bool(rows) and len(numbers) == len(rows) and all(
                    (slot.minimum is None or value >= slot.minimum)
                    and (slot.maximum is None or value <= slot.maximum)
                    for value in numbers)
            except (TypeError, ValueError):
                ok = False
        elif slot.matcher == "source_reference":
            ok = bool(rows) and all(has_ref and ref == slot.source
                                    for _, _, has_ref, ref in rows)
        else:
            ok = False
        _assert(out, f"{prefix}.slot:{slot.intent}.{slot.key}", ok,
                slot, {"values": values, "refs": refs})


def judge_retrieval(expected: RetrievalExpectation, actual: PlanSnapshot,
                    out: TurnJudgement) -> None:
    skills = {_retrieved_name(value) for value in actual.skills}
    exemplars = {_retrieved_name(value) for value in actual.exemplars}
    required = ([(name, name in skills) for name in expected.required_skills]
                + [(name, name in exemplars)
                   for name in expected.required_exemplars])
    forbidden = ([(name, name in skills) for name in expected.forbidden_skills]
                 + [(name, name in exemplars)
                    for name in expected.forbidden_exemplars])
    for name, present in required:
        _assert(out, f"retrieval.required:{name}", present, True, present)
    for name, present in forbidden:
        _assert(out, f"retrieval.forbidden:{name}", not present, False, present)


def judge_turn(expected: TurnExpectation, actual: DecisionSnapshot) -> TurnJudgement:
    out = TurnJudgement()
    if expected.addressed is not None:
        _assert(out, "addressed", actual.addressed == expected.addressed,
                expected.addressed, actual.addressed)
    if expected.ingress_allowed:
        _assert(out, "ingress_allowed", actual.ingress in expected.ingress_allowed,
                expected.ingress_allowed, actual.ingress)
    if expected.ingress_forbidden:
        _assert(out, "ingress_forbidden", actual.ingress not in expected.ingress_forbidden,
                expected.ingress_forbidden, actual.ingress)
    if expected.decision_allowed:
        _assert(out, "decision_allowed", actual.decision in expected.decision_allowed,
                expected.decision_allowed, actual.decision)
    if expected.clarify != "allowed":
        wanted = expected.clarify == "required"
        _assert(out, "clarify", actual.clarify is wanted, wanted, actual.clarify)
    if expected.plan.assert_plan:
        judge_plan(expected.plan, actual.plan, out)
    _assert(out, "replan_count", len(actual.replans) == len(expected.replans),
            len(expected.replans), len(actual.replans))
    for index, replan_expected in enumerate(expected.replans):
        if index < len(actual.replans):
            judge_plan(replan_expected.plan, actual.replans[index], out,
                       f"replan[{index}]")
    judge_retrieval(expected.retrieval, actual.plan, out)
    if expected.no_side_effect_before_confirm:
        _assert(out, "no_side_effect_before_confirm", not actual.side_effects,
                [], actual.side_effects)
    group_hits = sum(value for key, value in out.metrics.items()
                     if key.endswith(".required_group_hits"))
    group_total = sum(value for key, value in out.metrics.items()
                      if key.endswith(".required_group_total"))
    dep_hits = sum(value for key, value in out.metrics.items()
                   if key.endswith(".dependency_hits"))
    dep_total = sum(value for key, value in out.metrics.items()
                    if key.endswith(".dependency_total"))
    out.metrics["required_group_recall"] = (
        group_hits / group_total if group_total else 1.0)
    out.metrics["forbidden_route_count"] = sum(
        value for key, value in out.metrics.items()
        if key.endswith(".forbidden_route_count"))
    out.metrics["overroute_count"] = sum(
        value for key, value in out.metrics.items()
        if key.endswith(".overroute_count"))
    out.metrics["dependency_pass"] = (
        dep_hits / dep_total if dep_total else 1.0)
    return out


# ── 最小对照关系 ───────────────────────────────────────────────────────────
# 语义签名刻意不含 step id 和自然语言：id 是 Planner 临时生成的，回复文风每轮都不同。
# 两者任一进签名，invariant 就永远为假、route_flip 就永远为真——断言变成噪声。


def _canonical(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _plan_semantic_signature(plan: PlanSnapshot) -> tuple:
    by_id = {step.id: step.intent for step in plan.steps}
    step_signatures = []
    dependencies = []
    for step in plan.steps:
        normalized_refs = []
        for key, ref in step.slot_refs.items():
            source_id, _, path = str(ref).partition(".")
            normalized_refs.append((key, by_id.get(source_id, source_id), path))
        step_signatures.append((
            step.intent,
            tuple(sorted((key, _canonical(value))
                         for key, value in step.slots.items())),
            tuple(sorted(normalized_refs)),
            step.require_confirm,
        ))
        dependencies.extend(
            (by_id.get(source_id, source_id), step.intent)
            for source_id in step.depends_on
        )
    return (
        plan.complexity,
        tuple(sorted(step_signatures)),
        tuple(sorted(dependencies)),
    )


def semantic_signature(snapshot: DecisionSnapshot) -> tuple:
    return (
        snapshot.ingress,
        snapshot.addressed,
        snapshot.decision,
        snapshot.clarify,
        _plan_semantic_signature(snapshot.plan),
        tuple(_plan_semantic_signature(plan) for plan in snapshot.replans),
    )


def judge_relation(spec, base: DecisionSnapshot,
                   variant: DecisionSnapshot) -> TurnJudgement:
    out = TurnJudgement()
    base_intents = set(base.plan.intents)
    variant_intents = set(variant.plan.intents)
    expected = spec.expectation or {}
    if spec.type == "invariant":
        _assert(out, "relation.invariant",
                semantic_signature(base) == semantic_signature(variant),
                semantic_signature(base), semantic_signature(variant))
    elif spec.type == "route_flip":
        changed = semantic_signature(base) != semantic_signature(variant)
        forbidden = set(expected.get("forbidden_after") or []) & variant_intents
        _assert(out, "relation.route_flip",
                (changed or not expected.get("required_change", True)) and not forbidden,
                expected, {"changed": changed, "forbidden": sorted(forbidden)})
    elif spec.type == "intent_add":
        wanted = set(expected.get("add") or [])
        _assert(out, "relation.intent_add",
                wanted <= (variant_intents - base_intents), wanted,
                sorted(variant_intents - base_intents))
    elif spec.type == "intent_remove":
        wanted = set(expected.get("remove") or [])
        _assert(out, "relation.intent_remove",
                wanted <= (base_intents - variant_intents), wanted,
                sorted(base_intents - variant_intents))
    elif spec.type == "clarify_flip":
        _assert(out, "relation.clarify_flip",
                base.clarify and not variant.clarify,
                (True, False), (base.clarify, variant.clarify))
    elif spec.type == "context_override":
        changed = semantic_signature(base) != semantic_signature(variant)
        _assert(out, "relation.context_override",
                changed if expected.get("must_differ", True) else True,
                expected, changed)
    elif spec.type == "clause_commute":
        _assert(out, "relation.clause_commute",
                semantic_signature(base) == semantic_signature(variant),
                semantic_signature(base), semantic_signature(variant))
    else:
        _assert(out, "relation.unknown", False, spec.type, "unsupported")
    out.metrics["relation_pass"] = 1.0 if out.passed else 0.0
    return out
