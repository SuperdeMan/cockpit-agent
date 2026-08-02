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


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(str(v) for v in (value or []))


def _parse_plan(raw: dict[str, Any] | None) -> PlanExpectation:
    asserted = raw is not None
    raw = raw or {}
    groups = tuple(IntentGroup(_strings(g.get("any_of")))
                   for g in raw.get("required_intent_groups") or [])
    deps = tuple(DependencyExpectation(
        producer=_strings(d.get("producer")),
        consumer=str(d.get("consumer") or ""),
        carries=_strings(d.get("carries")),
    ) for d in raw.get("dependencies") or [])
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


def _parse_expected(raw: dict[str, Any]) -> TurnExpectation:
    ingress = raw.get("ingress") or {}
    decision = raw.get("decision") or {}
    retrieval = raw.get("retrieval") or {}
    return TurnExpectation(
        addressed=raw.get("addressed") if isinstance(raw.get("addressed"), bool) else None,
        ingress_allowed=_strings(ingress.get("allowed")),
        ingress_forbidden=_strings(ingress.get("forbidden")),
        decision_allowed=_strings(decision.get("allowed")),
        clarify=str(decision.get("clarify") or "allowed"),
        plan=_parse_plan(raw.get("plan")),
        replans=tuple(ReplanExpectation(
            after=dict(p.get("after") or {}),
            plan=_parse_plan(p.get("plan")),
        ) for p in raw.get("replans") or []),
        retrieval=RetrievalExpectation(
            required_skills=_strings(retrieval.get("required_skills")),
            forbidden_skills=_strings(retrieval.get("forbidden_skills")),
            required_exemplars=_strings(retrieval.get("required_exemplars")),
            forbidden_exemplars=_strings(retrieval.get("forbidden_exemplars")),
        ),
        no_side_effect_before_confirm=bool(
            (raw.get("safety") or {}).get("no_side_effect_before_confirm", False)),
    )


def _parse_case(raw: dict[str, Any]) -> AdversarialCase:
    turns = tuple(CaseTurn(
        utterance=str((t.get("input") or {}).get("utterance") or ""),
        context=dict((t.get("input") or {}).get("context") or {}),
        expected=_parse_expected(t.get("expected") or {}),
    ) for t in raw.get("turns") or [])
    relation_raw = raw.get("relation")
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
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if data.get("schema_version") != 1:
            raise ValueError(f"{path}: schema_version must be 1")
        cases.extend(_parse_case(row) for row in data.get("cases") or [])
    return cases


def load_suites(path: Path) -> dict[str, SuiteConfig]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if data.get("schema_version") != 1:
        raise ValueError(f"{path}: schema_version must be 1")
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
