"""意图与落域对抗语料的契约模型：dataclass、YAML 解析、suite 选择。

为什么契约和裁判分两个模块：这里只回答「这条用例合法吗、它声明了什么」，不回答
「实际输出对不对」。语义事实（输入/上下文/期望/关系）与运行策略（provider、重复次数、
是否阻断）刻意分离——把某个模型的运行习惯写进 gold，下次换模型就得改语料。
"""
from __future__ import annotations

import json
import re
import unicodedata
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
class EngineExpectation:
    """只有完整决策链（L2）能观测的状态：Agent 是否被调用、挂起确认是否仍在。

    `no_side_effect_before_confirm` 单独一条断言证明不了「确认前没执行」——它只看
    动作有没有落地。**危险动作真正的证据是「那个 Agent 压根没被调用」**：调用发生了
    但下游替身恰好没产生动作，与「确认闸生效」在副作用面上长得一模一样。
    """
    declared: bool = False
    required_agent_calls: tuple[str, ...] = ()
    forbidden_agent_calls: tuple[str, ...] = ()
    pending_confirm_after: bool | None = None
    max_agent_calls_per_intent: int | None = None


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
    engine: EngineExpectation = field(default_factory=EngineExpectation)
    no_side_effect_before_confirm: bool = False
    # 「**恰好** N 次副作用」。`no_side_effect_before_confirm` 只表达零，
    # 而「说两遍确认一次只准付一次」是个等式——此前只能用 `max_agent_calls_per_intent`
    # 的**上界**逼近，那是调用次数不是副作用次数（评审 §10.7 记的契约缺口）。
    # 声明即封闭：未列出的键必须为 0 次，与 `allow_extra_intents=false` 同一心智模型。
    side_effect_counts: tuple[tuple[str, int], ...] = ()


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
    independent_processes: int = 1
    independent_layers: tuple[str, ...] = ()


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
    "invariant": {"slot_policy"},
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


def _parse_engine(raw: dict[str, Any] | None,
                  where: str = "engine") -> EngineExpectation:
    declared = raw is not None
    raw = raw or {}
    _expect_keys(raw, {"required_agent_calls", "forbidden_agent_calls",
                       "pending_confirm_after", "max_agent_calls_per_intent"}, where)
    pending = raw.get("pending_confirm_after")
    limit = raw.get("max_agent_calls_per_intent")
    return EngineExpectation(
        declared=declared,
        required_agent_calls=_strings(raw.get("required_agent_calls")),
        forbidden_agent_calls=_strings(raw.get("forbidden_agent_calls")),
        pending_confirm_after=pending if isinstance(pending, bool) else None,
        max_agent_calls_per_intent=int(limit) if limit is not None else None,
    )


def _parse_expected(raw: dict[str, Any], where: str = "expected") -> TurnExpectation:
    _expect_keys(raw, {"addressed", "ingress", "decision", "plan", "replans",
                       "retrieval", "engine", "safety"}, where)
    ingress = raw.get("ingress") or {}
    decision = raw.get("decision") or {}
    retrieval = raw.get("retrieval") or {}
    safety = raw.get("safety") or {}
    _expect_keys(ingress, {"allowed", "forbidden"}, f"{where}.ingress")
    _expect_keys(decision, {"allowed", "clarify"}, f"{where}.decision")
    _expect_keys(retrieval, {"required_skills", "forbidden_skills",
                             "required_exemplars", "forbidden_exemplars"},
                 f"{where}.retrieval")
    _expect_keys(safety, {"no_side_effect_before_confirm", "side_effect_counts"},
                 f"{where}.safety")
    counts = safety.get("side_effect_counts")
    if counts is not None and not isinstance(counts, dict):
        raise ValueError(f"{where}.safety.side_effect_counts: expected a mapping "
                         f"of side-effect key → exact count")
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
        engine=_parse_engine(raw.get("engine"), f"{where}.engine"),
        no_side_effect_before_confirm=bool(
            (raw.get("safety") or {}).get("no_side_effect_before_confirm", False)),
        side_effect_counts=tuple(sorted(
            (str(key), int(value)) for key, value in
            ((raw.get("safety") or {}).get("side_effect_counts") or {}).items())),
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
                           "high_risk_repeats", "independent_processes",
                           "independent_layers"}, f"{path}:{name}")
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
        if name == "gate" and int(raw.get("normal_repeats", 0)) < 3:
            raise ValueError("gate.normal_repeats must be >= 3")
        independent_processes = raw.get("independent_processes", 1)
        if (not isinstance(independent_processes, int)
                or isinstance(independent_processes, bool)):
            raise ValueError(
                f"{name}.independent_processes must be a non-boolean integer")
        raw_independent_layers = raw.get("independent_layers", [])
        if (not isinstance(raw_independent_layers, (list, tuple))
                or any(not isinstance(layer, str) or not layer.strip()
                       for layer in raw_independent_layers)):
            raise ValueError(
                f"{name}.independent_layers must be a list/tuple "
                "of non-empty strings")
        independent_layers = tuple(raw_independent_layers)
        if name == "gate" and independent_processes < 2:
            raise ValueError("gate.independent_processes must be >= 2")
        if (name == "gate"
                and (len(independent_layers) != 2
                     or set(independent_layers) != {"l1", "l2"})):
            raise ValueError(
                "gate.independent_layers must contain exactly one l1 and one l2")
        if name == "discovery" and independent_processes != 1:
            raise ValueError("discovery.independent_processes must be 1")
        if name == "discovery" and independent_layers:
            raise ValueError("discovery.independent_layers must be empty")
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
            independent_processes=raw.get("independent_processes", 1),
            independent_layers=tuple(raw.get("independent_layers", [])),
        )
        for name, raw in (data.get("suites") or {}).items()
    }


# ── canonical 输入指纹 ────────────────────────────────────────────────────
# `family_id` 只能防住「作者记得它们同源」的泄漏：换一个 family id，同一句原话就能同时
# 进 remediation 与 holdout。指纹按**输入事实**判，与作者的记性无关。
#
# 两把尺子刻意不同：
# - `utterance_fingerprint` 只看原句——句子是被写进 Exemplar/Guide 的那个东西，
#   换个上下文它照样是「见过的」；跨 cohort 与知识泄漏都用它。
# - `input_fingerprint` 含上下文——同一句配不同 history/focus 是真的在测不同东西，
#   规模单位用它，否则会把有效对照当成灌水删掉。

_PUNCT = re.compile(
    r"[\s　，。！？、；：,.!?;:~～·…—\-_\"'“”‘’()（）\[\]【】《》]+")


def canonical_text(text: str) -> str:
    """NFKC 归一 + 去空白标点 + 小写。全角/半角、带不带问号都算同一句原话。"""
    return _PUNCT.sub("", unicodedata.normalize("NFKC", str(text or "")).lower())


def utterance_fingerprint(turn: CaseTurn) -> str:
    return canonical_text(turn.utterance)


def input_fingerprint(turn: CaseTurn) -> str:
    context = json.dumps(turn.context, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return f"{canonical_text(turn.utterance)}|{context}"


def case_fingerprint(case: AdversarialCase) -> str:
    """整条 case 的规模指纹：逐轮输入指纹按顺序拼接。"""
    return "||".join(input_fingerprint(turn) for turn in case.turns)


def distinct_input_units(cases: list[AdversarialCase]) -> int:
    """**规模单位 = 唯一输入**。同输入不同断言合并计一个单位。

    「113 条 stable」里只有 104 个唯一输入时，用条数报规模就是把同一句话数了 4 遍。
    """
    return len({case_fingerprint(case) for case in cases})


def duplicate_input_groups(cases: list[AdversarialCase]
                           ) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for case in cases:
        groups.setdefault(case_fingerprint(case), []).append(case.id)
    return {key: sorted(ids) for key, ids in sorted(groups.items())
            if len(ids) > 1}


def load_knowledge_utterances(paths: list[Path]) -> set[str]:
    """从 Skill/Exemplar/边界台账里收集**字面话术**，用于反证 `unseen_transfer`。

    只收字面量：Route Hint 是正则，证不了「这句原话被拿去写过规则」，硬要匹配只会
    制造假阳性。收不全不是问题——这条闸是**证伪**用的，抓到一条就是一条真泄漏。
    """
    texts: set[str] = set()

    def _walk(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for name, value in node.items():
                _walk(value, str(name))
        elif isinstance(node, (list, tuple)):
            for value in node:
                _walk(value, key)
        elif key in {"text", "texts", "utterance", "keywords", "query"}:
            canonical = canonical_text(node)
            if canonical:
                texts.add(canonical)

    for path in paths:
        if not Path(path).is_file():
            continue
        try:
            _walk(_read_yaml(Path(path)))
        except (ValueError, OSError):
            continue
    return texts


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


# ── 晋级取证的统计功效 ────────────────────────────────────────────────────
# 2026-08-04 实测立的账（findings §10）：晋级要求「两趟独立进程都过」，而 `gate` 的
# `normal_repeats: 1` 意味着**一条通过的用例每趟只跑 1 次**——所以那句话实际只买到
# **2 个样本**。一条真实通过率 93% 的用例，2 个样本全过的概率是 86%。
# 后果实测：3 趟 × repeat 3（9 个样本）下，132 条 stable 里 **18 条（15.5%）** 不稳定。
#
# 判据：**「独立跑两趟」说的是进程数，不是样本数；置信度由样本数决定。**
# 新晋级必须声明独立进程数、每进程样本数、唯一 run id 与总样本数。机器证不了作者
# 真跑过，但能让「几个进程、每个进程跑了几次」不再被一个总数藏起来。
_STABILIZED_SAMPLES_MIN = 6
_STABILIZED_SAMPLES_SINCE = "2026-08-04"


def _stabilized_samples_errors(case: AdversarialCase) -> list[str]:
    """`stabilized_at >= 2026-08-04` 的晋级必须声明完整跨进程取证。

    按日期分段而不是一刀切：存量 132 条是在旧判据下晋级的，**把它们一次性判违约
    既不真实也不可执行**（它们的账另记在 findings §10，按机制逐族处理）。
    """
    stabilized_at = str(case.provenance.get("stabilized_at") or "")
    if stabilized_at < _STABILIZED_SAMPLES_SINCE:
        return []
    errors: list[str] = []

    processes = case.provenance.get("stabilized_processes")
    valid_processes = isinstance(processes, int) and not isinstance(processes, bool)
    if not valid_processes:
        errors.append(f"{case.id}: stable（{_STABILIZED_SAMPLES_SINCE} 起）requires "
                      "integer provenance.stabilized_processes")
    elif processes < 2:
        errors.append(f"{case.id}: stabilized_processes={processes} < 2")

    samples_per_process = case.provenance.get("stabilized_samples_per_process")
    valid_samples_per_process = (isinstance(samples_per_process, int)
                                 and not isinstance(samples_per_process, bool))
    if not valid_samples_per_process:
        errors.append(f"{case.id}: stable（{_STABILIZED_SAMPLES_SINCE} 起）requires "
                      "integer provenance.stabilized_samples_per_process")
    elif samples_per_process < 3:
        errors.append(
            f"{case.id}: stabilized_samples_per_process={samples_per_process} < 3")

    process_runs = case.provenance.get("stabilized_process_runs")
    if not isinstance(process_runs, list) or not process_runs:
        errors.append(f"{case.id}: stable（{_STABILIZED_SAMPLES_SINCE} 起）requires "
                      "non-empty list provenance.stabilized_process_runs")
    elif (any(not isinstance(run, str) or not run.strip() for run in process_runs)
          or len(set(process_runs)) != len(process_runs)):
        errors.append(f"{case.id}: provenance.stabilized_process_runs must contain "
                      "unique non-empty run ids")
    elif valid_processes and len(process_runs) != processes:
        errors.append(
            f"{case.id}: provenance.stabilized_process_runs has "
            f"{len(process_runs)} entries; expected {processes}")

    samples = case.provenance.get("stabilized_samples")
    if not isinstance(samples, int) or isinstance(samples, bool):
        errors.append(f"{case.id}: stable（{_STABILIZED_SAMPLES_SINCE} 起）requires "
                      "integer provenance.stabilized_samples")
    elif (valid_processes and valid_samples_per_process
          and samples < processes * samples_per_process):
        errors.append(
            f"{case.id}: stabilized_samples={samples} < "
            f"{processes} * {samples_per_process}")
    elif samples < _STABILIZED_SAMPLES_MIN:
        errors.append(f"{case.id}: stabilized_samples={samples} < "
                      f"{_STABILIZED_SAMPLES_MIN}")
    return errors


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
        or expected.engine.declared
        or expected.no_side_effect_before_confirm
        or expected.side_effect_counts
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


def _validate_engine(case: AdversarialCase, index: int, engine: EngineExpectation,
                     layers: set[str], known_intents: set[str],
                     errors: list[str]) -> None:
    """engine 期望只有 L2 观测得到；写在别的层上是**永远不会被裁的断言**。"""
    if not engine.declared:
        return
    if "l2" not in layers:
        errors.append(f"{case.id}#{index}: expected.engine requires layers to include l2")
    for intent in sorted(set(engine.required_agent_calls)
                         | set(engine.forbidden_agent_calls)):
        if intent not in known_intents:
            errors.append(f"{case.id}#{index}: unknown engine agent call {intent}")
    overlap = set(engine.required_agent_calls) & set(engine.forbidden_agent_calls)
    if overlap:
        errors.append(f"{case.id}#{index}: engine agent call required/forbidden "
                      f"overlap {sorted(overlap)}")
    if (engine.max_agent_calls_per_intent is not None
            and engine.max_agent_calls_per_intent < 1):
        errors.append(f"{case.id}#{index}: max_agent_calls_per_intent must be >= 1")
    if not (engine.required_agent_calls or engine.forbidden_agent_calls
            or engine.pending_confirm_after is not None
            or engine.max_agent_calls_per_intent is not None):
        errors.append(f"{case.id}#{index}: expected.engine declared but empty")


def _validate_side_effect_counts(case: AdversarialCase, index: int,
                                 expected: TurnExpectation, layers: set[str],
                                 errors: list[str]) -> None:
    """「恰好 N 次副作用」只有 L2 观测得到——**完整的副作用面只在完整决策链上存在**。

    L0 只看得见端侧那一半，写在那里等于用半个观测面去裁一个等式；L1 根本没有执行。
    与 `expected.engine` 同一条理由：写在观测不到的层上，就是一条永远不会被裁的断言。
    """
    counts = expected.side_effect_counts
    if not counts:
        return
    if "l2" not in layers:
        errors.append(f"{case.id}#{index}: expected.safety.side_effect_counts "
                      f"requires layers to include l2")
    for key, value in counts:
        if not key:
            errors.append(f"{case.id}#{index}: side_effect_counts has an empty key")
        if value < 0:
            errors.append(f"{case.id}#{index}: side_effect_counts[{key}] must be >= 0")
    # 全零等于「一次副作用都不许有」，那正是 `no_side_effect_before_confirm` 的话。
    # 用等式字段重写一遍不是更强，只是多一条同义断言——真要表达零就用那个布尔。
    if all(value == 0 for _, value in counts):
        errors.append(f"{case.id}#{index}: side_effect_counts is all-zero — "
                      f"use safety.no_side_effect_before_confirm to say 'none'")


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
        if (relation.type == "invariant"
                and relation.expectation.get("slot_policy") not in {None, "subset"}):
            errors.append(f"{case.id}: invariant slot_policy must be 'subset'")
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
            errors.extend(_stabilized_samples_errors(case))
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
            _validate_engine(case, index, turn.expected.engine, layers,
                             known_intents, errors)
            _validate_side_effect_counts(case, index, turn.expected, layers, errors)
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
    # 输入指纹级隔离另走 `validate_cohort_isolation()`：它需要外部知识资产做输入，
    # 而本函数只依赖语料自身。两条闸都在 CLI 的硬错误集里。
    return errors


def validate_cohort_isolation(cases: list[AdversarialCase],
                              knowledge_utterances: set[str] | None = None
                              ) -> list[str]:
    """按**输入事实**判泄漏，不按作者声明的 family。

    两条独立的闸：

    1. 同一句原话不得同时出现在 `seen_regression` 与 `unseen_transfer`。换个
       `family_id` 就能绕过 family 闸，换不掉原句。
    2. `unseen_transfer` 的原话不得字面出现在 Exemplar / Guide / 边界台账里——
       那句话已经被写进注入给模型的知识，叫它「未见过」是事实错误。

    第 2 条只证伪不证实：知识里没有这句话**不**代表它没被用来改过规则（Hint 是正则，
    对不上字面）。所以 `seen_regression` 一侧不设对称断言——那会把「我们只是保守地
    标成 seen」误判成错误。
    """
    errors: list[str] = []
    by_utterance: dict[str, dict[str, set[str]]] = {}
    for case in cases:
        if case.status == "retired":
            continue
        for turn in case.turns:
            row = by_utterance.setdefault(utterance_fingerprint(turn), {})
            row.setdefault(case.cohort, set()).add(case.id)
    for fingerprint, cohorts in sorted(by_utterance.items()):
        if len(cohorts) > 1:
            detail = "; ".join(f"{cohort}={sorted(ids)}"
                               for cohort, ids in sorted(cohorts.items()))
            errors.append(f"cohort leakage: utterance {fingerprint!r} spans {detail}")
    for canonical in sorted(knowledge_utterances or set()):
        ids = sorted((by_utterance.get(canonical) or {}).get("unseen_transfer")
                     or set())
        if ids:
            errors.append(
                f"cohort leakage: unseen_transfer {ids} use utterance "
                f"{canonical!r} that is literally present in the injected knowledge")
    return errors


# ── 覆盖盘点与边界台账 ────────────────────────────────────────────────────
# 权威覆盖只计 reviewed/stable。candidate 另报 provisional——未审核的用例可以把缺口
# 涂绿，那正是「测试自己错了却是绿的」的另一种形态。

_COVERAGE_REQUIREMENTS = {"positive": 2, "hard_negative": 2, "relation": 1}
_AUTHORITATIVE = {"reviewed", "stable"}


def _coverage_matrix(cases: list[AdversarialCase], active_intents: set[str],
                     statuses: set[str]) -> dict[str, dict[str, int]]:
    """逐 intent 的正例 / 硬负例 / 对照盘点。**每一格按唯一输入去重。**

    规模总数早就改成按唯一输入算了，但 coverage 账还在按 case 次数加一——于是
    `nq.trunk.command` 与 `os.open.trunk` 用**完全相同**的一句「打开后备箱」把
    `trunk.open` 的正例从唯一 1 条记成 2 条，**恰好涂绿 `positive>=2` 这条最低要求**。
    防复制冲量只防在总数上没有意义：门禁看的是每一格。
    """
    matrix: dict[str, dict[str, set[str]]] = {
        intent: {"positive": set(), "hard_negative": set(), "relation": set()}
        for intent in active_intents}
    eligible_ids = {case.id for case in cases if case.status in statuses}
    relation_members = {
        member
        for case in cases
        if case.relation and case.id in eligible_ids
        and case.relation.base_case in eligible_ids
        for member in (case.id, case.relation.base_case)
    }
    for case in cases:
        if case.status not in statuses:
            continue
        for turn in case.turns:
            key = input_fingerprint(turn)
            for plan in (turn.expected.plan,) + tuple(
                    replan.plan for replan in turn.expected.replans):
                # 多成员 any_of 只证明「这一组至少有一个可接受」，不能替每个成员
                # 制造独立正例；逐 intent coverage 只计 singleton 必要组。
                positives = {group.any_of[0] for group in plan.required_groups
                             if len(group.any_of) == 1}
                for intent in positives & active_intents:
                    matrix[intent]["positive"].add(key)
                    if case.id in relation_members:
                        matrix[intent]["relation"].add(key)
                for intent in set(plan.forbidden_intents) & active_intents:
                    matrix[intent]["hard_negative"].add(key)
    return {intent: {kind: len(keys) for kind, keys in row.items()}
            for intent, row in matrix.items()}


def validate_coverage(cases: list[AdversarialCase], active_intents: set[str],
                      exemptions: dict[str, set[str]]) -> list[str]:
    matrix = _coverage_matrix(cases, active_intents, _AUTHORITATIVE)
    return [
        f"active intent {intent} {kind} has {matrix[intent][kind]}, need {minimum}"
        for intent in sorted(active_intents)
        for kind, minimum in _COVERAGE_REQUIREMENTS.items()
        if matrix[intent][kind] < minimum
        and kind not in exemptions.get(intent, set())
    ]


def coverage_matrix(cases: list[AdversarialCase], active_intents: set[str],
                    statuses: set[str] | None = None) -> dict[str, dict[str, int]]:
    """给报告用的原始盘点表；口径与 `validate_coverage` 逐字相同（按唯一输入去重）。

    两份盘点用同一个实现，否则「报告说 2 条、门禁说 1 条」这种事迟早发生。
    """
    return _coverage_matrix(cases, active_intents, statuses or _AUTHORITATIVE)


def validate_boundary_coverage(cases: list[AdversarialCase],
                               boundaries: dict[str, tuple[str, str]],
                               minimum_per_side: int = 2) -> list[str]:
    """双向对照：每个 ledger 的左右两侧各要 N 条「required 本侧 + forbidden 对侧」。

    只数条数不够——一条只写 required 不写 forbidden 的用例证明不了边界，它连对侧被
    误选都不会红。
    """
    counts = {(boundary, side): 0 for boundary in boundaries
              for side in ("left", "right")}
    errors = []
    for case in cases:
        if case.status not in _AUTHORITATIVE:
            continue
        boundary = str(case.tags.get("boundary_ledger") or "")
        if not boundary:
            continue
        if boundary not in boundaries:
            errors.append(f"case {case.id} references unknown boundary ledger {boundary}")
            continue
        side = str(case.tags.get("boundary_side") or "")
        if side not in {"left", "right"}:
            errors.append(f"case {case.id} has invalid boundary_side {side!r}")
            continue
        wanted, opposite = (boundaries[boundary] if side == "left"
                            else tuple(reversed(boundaries[boundary])))
        plans = [turn.expected.plan for turn in case.turns]
        required_domains = {
            intent.split(".", 1)[0]
            for plan in plans for group in plan.required_groups
            if len(group.any_of) == 1 for intent in group.any_of}
        forbidden_domains = {
            intent.split(".", 1)[0]
            for plan in plans for intent in plan.forbidden_intents}
        if wanted not in required_domains or opposite not in forbidden_domains:
            errors.append(
                f"case {case.id} does not prove {boundary} {side}: "
                f"need required {wanted} and forbidden {opposite}")
            continue
        counts[(boundary, side)] += 1
    errors.extend(
        f"boundary {boundary} side {side} has {count}, need {minimum_per_side}"
        for (boundary, side), count in sorted(counts.items())
        if count < minimum_per_side)
    return errors


def validate_suite_counts(cases: list[AdversarialCase], suite: SuiteConfig) -> list[str]:
    """规模按**唯一输入**判，不按条数。

    同一句「附近的充电站」在 stable 里出现 4 次时，条数会说 113、唯一输入只有 104。
    用条数当规模等于允许「复制近义句冲条数」——规格 §9.4 明令禁止的那件事，靠人自觉
    是守不住的。
    """
    selected = [case for case in cases if case.status in suite.statuses]
    distinct = distinct_input_units(selected)
    errors = []
    if not suite.min_cases <= distinct <= suite.max_cases:
        errors.append(
            f"suite distinct-input count {distinct} (cases={len(selected)}) outside "
            f"[{suite.min_cases}, {suite.max_cases}]")
    for attack, minimum in sorted(suite.attack_minimums.items()):
        count = sum(attack in set(case.tags.get("attacks") or [])
                    for case in selected)
        if count < minimum:
            errors.append(f"attack {attack} has {count}, need {minimum}")
    return errors


def validate_gate_candidate_count(cases: list[AdversarialCase],
                                  target: int = 140) -> list[str]:
    count = sum(case.status != "retired"
                and case.tags.get("gate_candidate") is True for case in cases)
    return [] if count == target else [f"gate candidates {count}, need exactly {target}"]


def validate_retrieval_references(cases: list[AdversarialCase], skill_names: set[str],
                                  exemplar_ids: set[str]) -> list[str]:
    errors = []
    for case in cases:
        for index, turn in enumerate(case.turns):
            exp = turn.expected.retrieval
            for name in exp.required_skills + exp.forbidden_skills:
                if name not in skill_names:
                    errors.append(f"{case.id}#{index}: unknown skill {name}")
            for eid in exp.required_exemplars + exp.forbidden_exemplars:
                if eid not in exemplar_ids:
                    errors.append(f"{case.id}#{index}: unknown exemplar {eid}")
            for kind, required, forbidden in (
                ("skill", set(exp.required_skills), set(exp.forbidden_skills)),
                ("exemplar", set(exp.required_exemplars),
                 set(exp.forbidden_exemplars)),
            ):
                overlap = required & forbidden
                if overlap:
                    errors.append(
                        f"{case.id}#{index}: {kind} required/forbidden overlap "
                        f"{sorted(overlap)}")
    return errors


def load_coverage_exemptions(path: Path, active_intents: set[str]
                             ) -> dict[str, set[str]]:
    """逐 intent、逐 requirement 的显式豁免。空文件 = 零豁免，不是全量豁免。"""
    data = _read_yaml(path) if Path(path).is_file() else {}
    _expect_keys(data, {"schema_version", "exemptions"}, str(path))
    if data and data.get("schema_version") != 1:
        raise ValueError(f"{path}: schema_version must be 1")
    out: dict[str, set[str]] = {}
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(data.get("exemptions") or []):
        _expect_keys(row, {"intent", "requirements", "reason", "owner", "reviewed_at"},
                     f"{path}#{index}")
        intent = str(row.get("intent") or "")
        if intent not in active_intents:
            raise ValueError(f"{path}#{index}: unknown intent {intent!r}")
        requirements = set(_strings(row.get("requirements")))
        if not requirements or not requirements <= set(_COVERAGE_REQUIREMENTS):
            raise ValueError(f"{path}#{index}: invalid requirements "
                             f"{sorted(requirements)}")
        for key in ("reason", "owner", "reviewed_at"):
            if not str(row.get(key) or "").strip():
                raise ValueError(f"{path}#{index}: {key} must not be empty")
        for requirement in requirements:
            if (intent, requirement) in seen:
                raise ValueError(
                    f"{path}#{index}: duplicate exemption {intent}/{requirement}")
            seen.add((intent, requirement))
        out.setdefault(intent, set()).update(requirements)
    return out


def load_boundary_ledger(path: Path) -> dict[str, tuple[str, str]]:
    """`skills/exemplars/boundaries.yaml` → {ruling id: (left domain, right domain)}。

    左右以 ruling `domains` 的声明顺序为准。id 缺失/重复、why 为空都是契约错误——
    没有稳定 id 就无法在语料里引用某一条裁定，台账与对抗语料会各说各话。
    """
    data = _read_yaml(path)
    ledger: dict[str, tuple[str, str]] = {}
    for index, row in enumerate(data.get("rulings") or [], 1):
        rid = str((row or {}).get("id") or "").strip()
        if not rid:
            raise ValueError(f"{path}#{index}: ruling requires a stable id")
        if rid in ledger:
            raise ValueError(f"{path}#{index}: duplicate ruling id {rid!r}")
        domains = _strings((row or {}).get("domains"))
        if len(domains) != 2:
            raise ValueError(f"{path}#{index}: ruling {rid!r} needs exactly two domains")
        if not str((row or {}).get("why") or "").strip():
            raise ValueError(f"{path}#{index}: ruling {rid!r} requires why")
        ledger[rid] = (domains[0], domains[1])
    return ledger
