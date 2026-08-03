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
    EngineExpectation, PlanExpectation, RetrievalExpectation, TurnExpectation,
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
    # 只有完整决策链观测得到的三项。`engine_observed=False` 时 engine 断言整体跳过，
    # 而不是当成「没调用 Agent 所以通过」——没观测和观测到零是两件事。
    engine_observed: bool = False
    agent_calls: tuple[str, ...] = ()
    pending_confirm_after: bool | None = None


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

    def subset(self, *prefixes: str) -> list[AssertionResult]:
        return [item for item in self.assertions
                if any(item.name.startswith(prefix) for prefix in prefixes)]

    def subset_passed(self, *prefixes: str) -> bool | None:
        """子集口径。**没有这类断言时返回 None，不是 True**。

        `exact_plan_set` 曾拿整轮 `passed` 顶替：于是 L0（根本没有 plan 断言）也在
        往这个指标里记 1，而 plan 三项全过、只有检索断言失败的用例被记成 0。
        指标的分母必须是「真的被断言过 plan 的证据单元」。
        """
        rows = self.subset(*prefixes)
        return all(row.passed for row in rows) if rows else None


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


def _retrieved_sets(values) -> tuple[set[str], set[str]]:
    """→ (检索到的, 其中被预算裁掉的)。

    `!clipped` 表示「检索到了但超预算没进 prompt」。required 断言要的是**真的注入了**，
    所以裁掉的不算命中；forbidden 断言要的是**没干扰模型**，裁掉的也就不算命中。
    两边都按「是否进了 prompt」判，口径才一致。
    """
    injected, clipped = set(), set()
    for value in values:
        name = _retrieved_name(value)
        (clipped if "!clipped" in str(value) else injected).add(name)
    return injected, clipped


def judge_retrieval(expected: RetrievalExpectation, actual: PlanSnapshot,
                    out: TurnJudgement) -> None:
    skills, skills_clipped = _retrieved_sets(actual.skills)
    exemplars, exemplars_clipped = _retrieved_sets(actual.exemplars)
    required = ([(name, name in skills, name in skills_clipped)
                 for name in expected.required_skills]
                + [(name, name in exemplars, name in exemplars_clipped)
                   for name in expected.required_exemplars])
    forbidden = ([(name, name in skills) for name in expected.forbidden_skills]
                 + [(name, name in exemplars)
                    for name in expected.forbidden_exemplars])
    for name, present, clipped in required:
        _assert(out, f"retrieval.required:{name}", present, True, present,
                detail="retrieved but clipped by budget" if clipped else "")
    for name, present in forbidden:
        _assert(out, f"retrieval.forbidden:{name}", not present, False, present)


def judge_engine(expected: EngineExpectation, actual: DecisionSnapshot,
                 out: TurnJudgement) -> None:
    """Agent 调用与挂起状态。

    `forbidden_agent_calls` 是「确认前零副作用」真正的搭档：副作用面只看动作有没有
    落地，替身恰好不产生动作时它恒真；**调用记录看的是那个 Agent 有没有被够着**。

    **声明了 `expected.engine` 却没观测到 Engine，是失败不是「不适用」。**
    旧实现在 `engine_observed=False` 时直接 return，于是整组 Engine gold 被静默跳过：
    一条声明了 `required_agent_calls` + `pending_confirm_after=true` 的用例，实际在
    Edge 本地就结束了、根本没到 Engine，仍然只留下 decision/replan/safety 三条绿断言
    整轮通过——**最需要这组断言的那一刻正好是它失效的那一刻**。
    这是本套件反复出现的同一形态：没观测被当成了观测到「没问题」。
    """
    if not expected.declared:
        return
    _assert(out, "engine.observed", bool(actual.engine_observed),
            True, bool(actual.engine_observed),
            detail="声明了 expected.engine 却没走到 Engine——未到 Engine 不是「不适用」")
    if not actual.engine_observed:
        return
    called = list(actual.agent_calls)
    counts: dict[str, int] = {}
    for intent in called:
        counts[intent] = counts.get(intent, 0) + 1
    missing = sorted(set(expected.required_agent_calls) - set(called))
    if expected.required_agent_calls:
        _assert(out, "engine.required_agent_calls", not missing,
                expected.required_agent_calls, called)
    hit = sorted(set(expected.forbidden_agent_calls) & set(called))
    if expected.forbidden_agent_calls:
        _assert(out, "engine.forbidden_agent_calls", not hit,
                expected.forbidden_agent_calls, called)
        out.metrics["forbidden_agent_call_count"] = float(len(hit))
    if expected.pending_confirm_after is not None:
        _assert(out, "engine.pending_confirm_after",
                actual.pending_confirm_after is expected.pending_confirm_after,
                expected.pending_confirm_after, actual.pending_confirm_after)
    if expected.max_agent_calls_per_intent is not None:
        over = sorted(intent for intent, count in counts.items()
                      if count > expected.max_agent_calls_per_intent)
        _assert(out, "engine.max_agent_calls_per_intent", not over,
                expected.max_agent_calls_per_intent, counts)


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
    # `replan_count` 只在**声明过计划形态**时才断言。它原来无条件写：于是 L0（根本没有
    # plan gold）也带着一条恒真的 `replan_count`，把它算进 `exact_plan_set` 的子集就等于
    # 把整个 L0 塞进 plan 精确率的分母。反过来，不算它又漏掉「多规划了一轮 replan」——
    # 实测有 turn 因多出一次 replan 而失败，`exact_plan_set` 仍记 1。两难的出路是让这条
    # 断言**跟着 plan gold 一起存在或一起不存在**。
    if expected.plan.assert_plan or expected.replans:
        _assert(out, "replan_count", len(actual.replans) == len(expected.replans),
                len(expected.replans), len(actual.replans))
    for index, replan_expected in enumerate(expected.replans):
        if index < len(actual.replans):
            judge_plan(replan_expected.plan, actual.replans[index], out,
                       f"replan[{index}]")
    judge_retrieval(expected.retrieval, actual.plan, out)
    judge_engine(expected.engine, actual, out)
    if expected.no_side_effect_before_confirm:
        _assert(out, "no_side_effect_before_confirm", not actual.side_effects,
                [], actual.side_effects)
    # **没断言过就不写这些数。** 旧实现无条件写 recall=1 / forbidden=0 / overroute=0 /
    # dependency=1，于是根本没有 plan gold 的 L0 也在往里记满分——`required_group_recall
    # =70/70 100%` 量的不是召回，是「有 70 个证据单元」。
    # 分母为 0 的地方要显示 `null`，不是 100%：这是本套件从头到尾的同一条纪律。
    group_hits = sum(value for key, value in out.metrics.items()
                     if key.endswith(".required_group_hits"))
    group_total = sum(value for key, value in out.metrics.items()
                      if key.endswith(".required_group_total"))
    dep_hits = sum(value for key, value in out.metrics.items()
                   if key.endswith(".dependency_hits"))
    dep_total = sum(value for key, value in out.metrics.items()
                    if key.endswith(".dependency_total"))
    if group_total:
        out.metrics["required_group_recall"] = group_hits / group_total
    if dep_total:
        out.metrics["dependency_pass"] = dep_hits / dep_total
    for name in ("forbidden_route_count", "overroute_count"):
        rows = [value for key, value in out.metrics.items()
                if key.endswith(f".{name}")]
        if rows:                      # judge_plan 跑过才有这两个键
            out.metrics[name] = sum(rows)
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
    """Agent 调用与挂起状态进签名：一次调了 trunk.open、一次没调，不是同一个结果。

    L0/L1 上这两项恒为 `()`/`None`，签名逐字不变——只有 L2 会因此变严。
    """
    return (
        snapshot.ingress,
        snapshot.addressed,
        snapshot.decision,
        snapshot.clarify,
        _plan_semantic_signature(snapshot.plan),
        tuple(_plan_semantic_signature(plan) for plan in snapshot.replans),
        tuple(sorted(snapshot.agent_calls)),
        snapshot.pending_confirm_after,
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
