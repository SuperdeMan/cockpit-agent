"""意图与落域对抗语料的契约模型：dataclass、YAML 解析、suite 选择。

为什么契约和裁判分两个模块：这里只回答「这条用例合法吗、它声明了什么」，不回答
「实际输出对不对」。语义事实（输入/上下文/期望/关系）与运行策略（provider、重复次数、
是否阻断）刻意分离——把某个模型的运行习惯写进 gold，下次换模型就得改语料。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class IntentGroup:
    """一个必要意图组。组内 `any_of` 是 OR，组与组之间是 AND。"""
    any_of: tuple[str, ...]


@dataclass(frozen=True)
class DependencyExpectation:
    """步骤间依赖：consumer 必须依赖 producer 之一，并沿 carries 取值。"""
    producer: tuple[str, ...]
    consumer: str
    carries: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlotExpectation:
    intent: str
    key: str
    matcher: str = "presence"
    value: Any = None
    allowed: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    source: str = ""


@dataclass(frozen=True)
class RetrievalExpectation:
    required_skills: tuple[str, ...] = ()
    forbidden_skills: tuple[str, ...] = ()
    required_exemplars: tuple[str, ...] = ()
    forbidden_exemplars: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanExpectation:
    """`assert_plan` 区分「声明了空 plan 约束」与「压根没声明 plan」——后者不产生断言。"""
    assert_plan: bool = False
    required_groups: tuple[IntentGroup, ...] = ()
    forbidden_intents: tuple[str, ...] = ()
    allow_extra_intents: bool = False
    allowed_extra_intents: tuple[str, ...] = ()
    allowed_complexities: tuple[str, ...] = ()
    dependencies: tuple[DependencyExpectation, ...] = ()
    slots: tuple[SlotExpectation, ...] = ()


@dataclass(frozen=True)
class ReplanExpectation:
    after: dict[str, Any]
    plan: PlanExpectation


@dataclass(frozen=True)
class TurnExpectation:
    addressed: bool | None = None
    ingress_allowed: tuple[str, ...] = ()
    ingress_forbidden: tuple[str, ...] = ()
    decision_allowed: tuple[str, ...] = ()
    clarify: str = "allowed"
    plan: PlanExpectation = field(default_factory=PlanExpectation)
    replans: tuple[ReplanExpectation, ...] = ()
    retrieval: RetrievalExpectation = field(default_factory=RetrievalExpectation)
    no_side_effect_before_confirm: bool = False


@dataclass(frozen=True)
class CaseTurn:
    utterance: str
    context: dict[str, Any]
    expected: TurnExpectation


@dataclass(frozen=True)
class RelationSpec:
    base_case: str
    type: str
    expectation: dict[str, Any]


@dataclass(frozen=True)
class AdversarialCase:
    id: str
    title: str
    family_id: str
    cohort: str
    risk: str
    status: str
    tags: dict[str, Any]
    provenance: dict[str, Any]
    turns: tuple[CaseTurn, ...]
    relation: RelationSpec | None = None


@dataclass(frozen=True)
class SuiteConfig:
    statuses: tuple[str, ...]
    live_statuses: tuple[str, ...]
    min_cases: int
    max_cases: int
    attack_minimums: dict[str, int]
    normal_repeats: int
    failure_repeats: int
    high_risk_repeats: int


# ── 合法取值表 ─────────────────────────────────────────────────────────────
_STATUSES = {"candidate", "reviewed", "stable", "retired"}
_RISKS = {"low", "medium", "high", "critical"}
_COHORTS = {"seen_regression", "unseen_transfer"}
_INGRESS = {"edge_local", "mixed", "cloud"}
_DECISIONS = {"execute", "clarify", "reject", "degrade", "confirm", "cancel"}
_CLARIFY = {"allowed", "required", "forbidden"}
_COMPLEXITIES = {"simple", "adaptive"}
_STEP_STATUSES = {"pending", "running", "ok", "failed", "skipped",
                  "need_confirm", "need_slot"}
_SLOT_MATCHERS = {"presence", "absence", "exact", "one_of", "range",
                  "source_reference"}
_ATTACKS = {f"A{index}" for index in range(1, 10)}
_LAYERS = {"l0", "l1", "l2", "l3"}
_RELATIONS = {
    "invariant", "route_flip", "intent_add", "intent_remove",
    "clarify_flip", "context_override", "clause_commute",
}
_RELATION_KEYS = {
    "invariant": set(),
    "route_flip": {"forbidden_after", "required_change"},
    "intent_add": {"add"},
    "intent_remove": {"remove"},
    "clarify_flip": set(),
    "context_override": {"must_differ"},
    "clause_commute": set(),
}

# ── 严格 YAML：重复键是错，不是「后写覆盖先写」 ───────────────────────────
# PyYAML 默认静默取最后一个同名键。语料里一个复制粘贴出来的重复 `plan:` 会让前一半
# gold 无声消失——那正是本套件要防的「测试自己错了却是绿的」。


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.load(Path(path).read_text(encoding="utf-8"),
                     Loader=UniqueKeyLoader) or {}


def _expect_keys(raw: dict[str, Any], allowed: set[str], where: str) -> None:
    """未知键一律拒绝。拼错的字段名静默失效，比缺字段更危险——它看起来写过了。"""
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected a mapping, got {type(raw).__name__}")
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(str(v) for v in (value or []))


def _parse_plan(raw: dict[str, Any] | None, where: str = "plan") -> PlanExpectation:
    asserted = raw is not None
    raw = raw or {}
    _expect_keys(raw, {"required_intent_groups", "forbidden_intents",
                       "allow_extra_intents", "allowed_extra_intents",
                       "complexity", "dependencies", "slots"}, where)
    for group in raw.get("required_intent_groups") or []:
        _expect_keys(group, {"any_of"}, f"{where}.required_intent_groups[]")
    groups = tuple(IntentGroup(_strings(g.get("any_of")))
                   for g in raw.get("required_intent_groups") or [])
    for dep in raw.get("dependencies") or []:
        _expect_keys(dep, {"producer", "consumer", "carries"},
                     f"{where}.dependencies[]")
    deps = tuple(DependencyExpectation(
        producer=_strings(d.get("producer")),
        consumer=str(d.get("consumer") or ""),
        carries=_strings(d.get("carries")),
    ) for d in raw.get("dependencies") or [])
    for slot in raw.get("slots") or []:
        _expect_keys(slot, {"intent", "key", "matcher", "value", "allowed",
                            "minimum", "maximum", "source"}, f"{where}.slots[]")
    slots = tuple(SlotExpectation(
        intent=str(s.get("intent") or ""),
        key=str(s.get("key") or ""),
        matcher=str(s.get("matcher") or ("one_of" if s.get("allowed") else "presence")),
        value=s.get("value"),
        allowed=_strings(s.get("allowed")),
        minimum=float(s["minimum"]) if s.get("minimum") is not None else None,
        maximum=float(s["maximum"]) if s.get("maximum") is not None else None,
        source=str(s.get("source") or ""),
    ) for s in raw.get("slots") or [])
    complexity = raw.get("complexity") or {}
    _expect_keys(complexity, {"allowed"}, f"{where}.complexity")
    return PlanExpectation(
        assert_plan=asserted,
        required_groups=groups,
        forbidden_intents=_strings(raw.get("forbidden_intents")),
        allow_extra_intents=bool(raw.get("allow_extra_intents", False)),
        allowed_extra_intents=_strings(raw.get("allowed_extra_intents")),
        allowed_complexities=_strings(complexity.get("allowed")),
        dependencies=deps,
        slots=slots,
    )


def _parse_expected(raw: dict[str, Any], where: str = "expected") -> TurnExpectation:
    _expect_keys(raw, {"addressed", "ingress", "decision", "plan", "replans",
                       "retrieval", "safety"}, where)
    ingress = raw.get("ingress") or {}
    decision = raw.get("decision") or {}
    retrieval = raw.get("retrieval") or {}
    safety = raw.get("safety") or {}
    _expect_keys(ingress, {"allowed", "forbidden"}, f"{where}.ingress")
    _expect_keys(decision, {"allowed", "clarify"}, f"{where}.decision")
    _expect_keys(retrieval, {"required_skills", "forbidden_skills",
                             "required_exemplars", "forbidden_exemplars"},
                 f"{where}.retrieval")
    _expect_keys(safety, {"no_side_effect_before_confirm"}, f"{where}.safety")
    for index, replan in enumerate(raw.get("replans") or []):
        _expect_keys(replan, {"after", "plan"}, f"{where}.replans[{index}]")
        after = replan.get("after") or {}
        _expect_keys(after, {"result"}, f"{where}.replans[{index}].after")
        result = after.get("result")
        if isinstance(result, dict):
            _expect_keys(result, {"step_id", "status", "data", "speech", "error"},
                         f"{where}.replans[{index}].after.result")
    return TurnExpectation(
        addressed=raw.get("addressed") if isinstance(raw.get("addressed"), bool) else None,
        ingress_allowed=_strings(ingress.get("allowed")),
        ingress_forbidden=_strings(ingress.get("forbidden")),
        decision_allowed=_strings(decision.get("allowed")),
        clarify=str(decision.get("clarify") or "allowed"),
        plan=_parse_plan(raw.get("plan"), f"{where}.plan"),
        replans=tuple(ReplanExpectation(
            after=dict(p.get("after") or {}),
            plan=_parse_plan(p.get("plan"), f"{where}.replans[{i}].plan"),
        ) for i, p in enumerate(raw.get("replans") or [])),
        retrieval=RetrievalExpectation(
            required_skills=_strings(retrieval.get("required_skills")),
            forbidden_skills=_strings(retrieval.get("forbidden_skills")),
            required_exemplars=_strings(retrieval.get("required_exemplars")),
            forbidden_exemplars=_strings(retrieval.get("forbidden_exemplars")),
        ),
        no_side_effect_before_confirm=bool(
            (raw.get("safety") or {}).get("no_side_effect_before_confirm", False)),
    )


def _parse_case(raw: dict[str, Any], where: str = "case") -> AdversarialCase:
    _expect_keys(raw, {"id", "title", "family_id", "cohort", "risk", "status",
                       "tags", "provenance", "turns", "relation"}, where)
    where = f"{where}[{raw.get('id')}]"
    for index, turn in enumerate(raw.get("turns") or []):
        _expect_keys(turn, {"input", "expected"}, f"{where}.turns[{index}]")
        _expect_keys(turn.get("input") or {}, {"utterance", "context"},
                     f"{where}.turns[{index}].input")
    turns = tuple(CaseTurn(
        utterance=str((t.get("input") or {}).get("utterance") or ""),
        context=dict((t.get("input") or {}).get("context") or {}),
        expected=_parse_expected(t.get("expected") or {},
                                 f"{where}.turns[{i}].expected"),
    ) for i, t in enumerate(raw.get("turns") or []))
    relation_raw = raw.get("relation")
    if relation_raw:
        _expect_keys(relation_raw, {"base_case", "type", "expectation"},
                     f"{where}.relation")
    relation = None if not relation_raw else RelationSpec(
        base_case=str(relation_raw.get("base_case") or ""),
        type=str(relation_raw.get("type") or ""),
        expectation=dict(relation_raw.get("expectation") or {}),
    )
    return AdversarialCase(
        id=str(raw.get("id") or ""),
        title=str(raw.get("title") or ""),
        family_id=str(raw.get("family_id") or ""),
        cohort=str(raw.get("cohort") or ""),
        risk=str(raw.get("risk") or ""),
        status=str(raw.get("status") or ""),
        tags=dict(raw.get("tags") or {}),
        provenance=dict(raw.get("provenance") or {}),
        turns=turns,
        relation=relation,
    )


def load_cases(root: Path) -> list[AdversarialCase]:
    cases: list[AdversarialCase] = []
    for path in sorted(Path(root).glob("*.yaml")):
        data = _read_yaml(path)
        _expect_keys(data, {"schema_version", "cases"}, str(path))
        if data.get("schema_version") != 1:
            raise ValueError(f"{path}: schema_version must be 1")
        cases.extend(_parse_case(row, path.name)
                     for row in data.get("cases") or [])
    return cases


def load_suites(path: Path) -> dict[str, SuiteConfig]:
    data = _read_yaml(path)
    _expect_keys(data, {"schema_version", "suites"}, str(path))
    if data.get("schema_version") != 1:
        raise ValueError(f"{path}: schema_version must be 1")
    names = set((data.get("suites") or {}).keys())
    if names != {"discovery", "gate"}:
        raise ValueError(f"suite names must be discovery/gate, got {sorted(names)}")
    for name, raw in data["suites"].items():
        _expect_keys(raw, {"statuses", "live_statuses", "min_cases", "max_cases",
                           "attack_minimums", "normal_repeats", "failure_repeats",
                           "high_risk_repeats"}, f"{path}:{name}")
        statuses = set(_strings(raw.get("statuses")))
        live_statuses = set(_strings(raw.get("live_statuses")))
        if not statuses <= _STATUSES:
            raise ValueError(f"{name}.statuses has unknown values")
        if not live_statuses <= statuses:
            raise ValueError(f"{name}.live_statuses must be a subset of statuses")
        min_cases, max_cases = int(raw.get("min_cases", 0)), int(raw.get("max_cases", 0))
        if min_cases < 1 or max_cases < min_cases:
            raise ValueError(f"{name}.case bounds are invalid")
        attack_minimums = raw.get("attack_minimums") or {}
        if (not isinstance(attack_minimums, dict)
                or not set(attack_minimums) <= _ATTACKS
                or any(int(value) < 0 for value in attack_minimums.values())):
            raise ValueError(f"{name}.attack_minimums are invalid")
        for key in ("normal_repeats", "failure_repeats", "high_risk_repeats"):
            if int(raw.get(key, 0)) < 1:
                raise ValueError(f"{name}.{key} must be >= 1")
    return {
        str(name): SuiteConfig(
            statuses=_strings(raw.get("statuses")),
            live_statuses=_strings(raw.get("live_statuses")),
            min_cases=int(raw.get("min_cases", 0)),
            max_cases=int(raw.get("max_cases", 0)),
            attack_minimums={str(key): int(value)
                             for key, value in (raw.get("attack_minimums") or {}).items()},
            normal_repeats=int(raw.get("normal_repeats", 1)),
            failure_repeats=int(raw.get("failure_repeats", 3)),
            high_risk_repeats=int(raw.get("high_risk_repeats", 3)),
        )
        for name, raw in (data.get("suites") or {}).items()
    }


# ── 语义校验 ───────────────────────────────────────────────────────────────
# 返回错误列表而不是抛异常：一次跑完能看到语料里全部问题，逐条修比逐次撞快。


def _plan_intents(plan: PlanExpectation) -> set[str]:
    intents = {intent for group in plan.required_groups for intent in group.any_of}
    intents.update(plan.forbidden_intents)
    intents.update(plan.allowed_extra_intents)
    for dep in plan.dependencies:
        intents.update(dep.producer)
        intents.add(dep.consumer)
    intents.update(slot.intent for slot in plan.slots)
    return intents


def _has_absolute_gold(expected: TurnExpectation) -> bool:
    """relation 不是 gold：只声明「和另一个一样」的用例可以两个一起错还是绿的。"""
    retrieval = expected.retrieval
    return bool(
        expected.addressed is not None
        or expected.ingress_allowed or expected.ingress_forbidden
        or expected.decision_allowed or expected.clarify != "allowed"
        or expected.plan.assert_plan or expected.replans
        or retrieval.required_skills or retrieval.forbidden_skills
        or retrieval.required_exemplars or retrieval.forbidden_exemplars
        or expected.no_side_effect_before_confirm
    )


def _validate_plan(case: AdversarialCase, plan: PlanExpectation,
                   known_intents: set[str], errors: list[str]) -> None:
    referenced = _plan_intents(plan)
    required = {intent for group in plan.required_groups for intent in group.any_of}
    forbidden = set(plan.forbidden_intents)
    admitted = required | set(plan.allowed_extra_intents)
    if not set(plan.allowed_complexities) <= _COMPLEXITIES:
        errors.append(f"{case.id}: invalid plan complexity")
    for intent in sorted(referenced):
        if intent not in known_intents:
            errors.append(f"{case.id}: unknown intent {intent}")
    overlap = required & forbidden
    if overlap:
        errors.append(f"{case.id}: required and forbidden overlap {sorted(overlap)}")
    extra_overlap = set(plan.allowed_extra_intents) & forbidden
    if extra_overlap:
        errors.append(
            f"{case.id}: allowed extra and forbidden overlap {sorted(extra_overlap)}")
    for group in plan.required_groups:
        if not group.any_of:
            errors.append(f"{case.id}: required intent group is empty")
    for dep in plan.dependencies:
        if not dep.producer or not dep.consumer:
            errors.append(f"{case.id}: invalid dependency {dep}")
        if not set(dep.producer) <= admitted:
            errors.append(f"{case.id}: dependency producer is not admitted")
        if dep.consumer not in admitted:
            errors.append(f"{case.id}: dependency consumer is not admitted")
    for slot in plan.slots:
        if not slot.intent or not slot.key:
            errors.append(f"{case.id}: invalid slot expectation {slot}")
        if slot.intent not in admitted:
            errors.append(f"{case.id}: slot intent is not admitted")
        if slot.matcher not in _SLOT_MATCHERS:
            errors.append(f"{case.id}: invalid slot matcher {slot.matcher!r}")
        if slot.matcher == "exact" and slot.value is None:
            errors.append(f"{case.id}: exact slot matcher requires value")
        if slot.matcher == "one_of" and not slot.allowed:
            errors.append(f"{case.id}: one_of slot matcher requires allowed")
        if (slot.matcher == "range"
                and slot.minimum is None and slot.maximum is None):
            errors.append(f"{case.id}: range slot matcher needs a bound")
        if (slot.minimum is not None and slot.maximum is not None
                and slot.minimum > slot.maximum):
            errors.append(f"{case.id}: slot minimum exceeds maximum")
        if slot.matcher == "source_reference" and not slot.source:
            errors.append(f"{case.id}: source_reference matcher requires source")


def _validate_relation(case: AdversarialCase, case_ids: set[str],
                       by_id: dict[str, AdversarialCase], errors: list[str]) -> None:
    relation = case.relation
    if relation.type not in _RELATIONS:
        errors.append(f"{case.id}: invalid relation {relation.type!r}")
    else:
        unknown = set(relation.expectation) - _RELATION_KEYS[relation.type]
        if unknown:
            errors.append(
                f"{case.id}: unknown relation expectation keys {sorted(unknown)}")
    if relation.base_case not in case_ids:
        errors.append(f"{case.id}: missing relation base {relation.base_case!r}")
        return
    if by_id[relation.base_case].family_id != case.family_id:
        errors.append(f"{case.id}: relation base must share family_id")
        return
    base = by_id[relation.base_case]
    if base.id == case.id or base.relation is not None:
        errors.append(f"{case.id}: relation base must be a distinct root case")
    if (case.status in {"reviewed", "stable"}
            and base.status not in {"reviewed", "stable"}):
        errors.append(f"{case.id}: reviewed relation requires reviewed base")
    if case.status == "stable" and base.status != "stable":
        errors.append(f"{case.id}: stable relation requires stable base")
    if set(base.tags.get("layers") or []) != set(case.tags.get("layers") or []):
        errors.append(f"{case.id}: relation pair must share layers")
    if len(base.turns) != 1 or len(case.turns) != 1:
        errors.append(f"{case.id}: schema v1 relation pair must each have one turn")


def validate_cases(cases: list[AdversarialCase], known_intents: set[str]) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    by_family: dict[str, set[str]] = {}
    case_ids = {case.id for case in cases}
    by_id = {case.id: case for case in cases}
    for case in cases:
        if not case.id or case.id in ids:
            errors.append(f"duplicate id: {case.id!r}")
        ids.add(case.id)
        if case.status not in _STATUSES:
            errors.append(f"{case.id}: invalid status {case.status!r}")
        if case.risk not in _RISKS:
            errors.append(f"{case.id}: invalid risk {case.risk!r}")
        if case.cohort not in _COHORTS:
            errors.append(f"{case.id}: invalid cohort {case.cohort!r}")
        if not case.family_id:
            errors.append(f"{case.id}: missing family_id")
        by_family.setdefault(case.family_id, set()).add(case.cohort)
        if (case.status in {"reviewed", "stable"}
                and case.provenance.get("reviewed_by") != "human"):
            errors.append(f"{case.id}: {case.status} requires reviewed_by=human")
        if case.status in {"reviewed", "stable"} and not case.provenance.get("reviewed_at"):
            errors.append(f"{case.id}: {case.status} requires reviewed_at")
        if case.status == "stable":
            for key in ("stabilized_provider", "stabilized_at", "evidence_report"):
                if not case.provenance.get(key):
                    errors.append(f"{case.id}: stable requires provenance.{key}")
        if case.status == "retired" and not case.provenance.get("retired_reason"):
            errors.append(f"{case.id}: retired requires provenance.retired_reason")
        attacks = set(case.tags.get("attacks") or [])
        layers = set(case.tags.get("layers") or [])
        if not attacks or not attacks <= _ATTACKS:
            errors.append(f"{case.id}: invalid tags.attacks {sorted(attacks)}")
        if not layers or not layers <= _LAYERS:
            errors.append(f"{case.id}: invalid tags.layers {sorted(layers)}")
        if not case.turns:
            errors.append(f"{case.id}: turns must not be empty")
        for index, turn in enumerate(case.turns):
            if not turn.utterance.strip():
                errors.append(f"{case.id}#{index}: empty utterance")
            if (case.status in {"reviewed", "stable"}
                    and not _has_absolute_gold(turn.expected)):
                errors.append(
                    f"{case.id}#{index}: {case.status} turn requires absolute gold")
            if not set(turn.expected.ingress_allowed) <= _INGRESS:
                errors.append(f"{case.id}: invalid ingress allow-list")
            if not set(turn.expected.ingress_forbidden) <= _INGRESS:
                errors.append(f"{case.id}: invalid ingress deny-list")
            if set(turn.expected.ingress_allowed) & set(turn.expected.ingress_forbidden):
                errors.append(f"{case.id}: ingress allow/deny overlap")
            if not set(turn.expected.decision_allowed) <= _DECISIONS:
                errors.append(f"{case.id}: invalid decision allow-list")
            if turn.expected.clarify not in _CLARIFY:
                errors.append(f"{case.id}: invalid clarify expectation")
            for plan in (turn.expected.plan,) + tuple(
                    replan.plan for replan in turn.expected.replans):
                _validate_plan(case, plan, known_intents, errors)
            for replan in turn.expected.replans:
                result = replan.after.get("result")
                if not isinstance(result, dict):
                    errors.append(f"{case.id}: replan.after.result must be a mapping")
                    continue
                missing = {"step_id", "status", "data"} - set(result)
                if missing:
                    errors.append(f"{case.id}: replan result missing {sorted(missing)}")
                elif result.get("status") not in _STEP_STATUSES:
                    errors.append(f"{case.id}: invalid replan result status")
        if case.relation:
            _validate_relation(case, case_ids, by_id, errors)
    for family, cohorts in sorted(by_family.items()):
        if len(cohorts) > 1:
            errors.append(f"family leakage: {family} spans {sorted(cohorts)}")
    return errors
