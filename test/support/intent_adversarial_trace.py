"""决策 trace：Hint 前后计划、校验前后候选、资产指纹与首偏离点。

只包裹实例、不改生产类：`RecordingPlanner` 代理 `PlanBuilder`，`TracingRouteHints`
代理 `RouteHintEngine`，`attach_validation_trace()` 换掉实例上的绑定方法。生产 span
schema 与 `Plan` 结构一个字段都不动——被测对象被测试改形状，测的就不是它了。

首偏离点是**执行顺序上的第一个不一致边界**，不是根因。检索/历史命中只标 suspect；
只有相同 provider、相同资产指纹、规定重复次数下的受控消融稳定翻转才升级为 causal。
"""
from __future__ import annotations

import contextlib
import hashlib
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from support.intent_adversarial_judge import PlanSnapshot, StepSnapshot


def snapshot_plan(plan) -> PlanSnapshot:
    """Plan → 不可变快照。用 getattr 兜底：replan 产出的 Plan 没有 skills/hint_effect。"""
    return PlanSnapshot(
        steps=tuple(StepSnapshot(
            id=str(step.id), agent_id=str(step.agent_id), intent=str(step.intent),
            slots=dict(step.slots or {}), depends_on=tuple(step.depends_on or []),
            slot_refs=dict(step.slot_refs or {}),
            require_confirm=bool(getattr(step, "require_confirm", False)),
        ) for step in (getattr(plan, "steps", None) or [])),
        complexity=str(getattr(plan, "complexity", "") or ""),
        goal=str(getattr(plan, "goal", "") or ""),
        skills=tuple(getattr(plan, "skills", None) or []),
        exemplars=tuple(getattr(plan, "exemplars", None) or []),
        hint_effect=str(getattr(plan, "hint_effect", "") or ""),
        catalog_stats=dict(getattr(plan, "catalog_stats", None) or {}),
        raw_llm=str(getattr(plan, "raw_llm", "") or ""),
        plan_mode=str(getattr(plan, "plan_mode", "") or ""),
        skill_effects=tuple(getattr(plan, "skill_effects", None) or []),
    )


@dataclass(frozen=True)
class HintMatch:
    agent_id: str
    intent: str
    policy: str
    priority: int


@dataclass(frozen=True)
class HintTrace:
    text: str
    matches: tuple[HintMatch, ...]
    before: PlanSnapshot
    after: PlanSnapshot
    hit: bool


@dataclass(frozen=True)
class PlannerTrace:
    stage: str
    plan: PlanSnapshot
    done: bool = False


@dataclass(frozen=True)
class RawCapabilityReference:
    """One bounded capability identity exactly as it appeared on the LLM wire."""
    value: str
    status: str


RAW_CAPABILITY_REF_STATUSES = frozenset({
    "admitted", "unknown", "malformed_reference", "legacy_identity",
    "missing", "invalid_type", "malformed_wire", "missing_steps",
    "malformed_steps", "malformed_step",
})
RAW_VALIDATION_STAGES = frozenset({"build", "replan"})
RAW_VALIDATION_WIRE_MODES = frozenset({
    "direct", "json", "toolcall", "toolcall_salvage", "toolcall_fallback",
})
INVALID_CAPABILITY_REFERENCE = "__invalid_capability_reference__"

_REQUEST_CAPABILITY_REF = re.compile(r"cap_[0-9]{4,}\Z")
_EXACT_RAW_REF_SENTINELS = {
    "missing": "<capability_ref:missing>",
    "malformed_wire": "<wire:not-object>",
    "missing_steps": "<steps:missing>",
}
_MALFORMED_STEPS_VALUE_RE = re.compile(
    r"<malformed-steps:type=[A-Za-z_][A-Za-z0-9_]{0,39}>\Z"
)


def raw_capability_ref_value_matches_status(value: Any, status: Any) -> bool:
    """Validate the bounded wire identity independently of its claimed status.

    Formal reports are untrusted inputs.  A status label therefore cannot turn
    a blank value, a semantic intent, or the wrong sentinel into admitted raw
    evidence.
    """
    if (not isinstance(value, str) or not value.strip() or len(value) > 81
            or status not in RAW_CAPABILITY_REF_STATUSES):
        return False
    if status in {"admitted", "unknown"}:
        return _REQUEST_CAPABILITY_REF.fullmatch(value) is not None
    if status == "malformed_reference":
        return _REQUEST_CAPABILITY_REF.fullmatch(value) is None
    if status == "malformed_steps":
        return bool(
            value == "<malformed-steps:list-required>"
            or _MALFORMED_STEPS_VALUE_RE.fullmatch(value)
        )
    exact = _EXACT_RAW_REF_SENTINELS.get(status)
    if exact is not None:
        return value == exact
    if status == "legacy_identity":
        return value.startswith("agent_id=") and (
            ";intent=" in value or len(value) == 81
        )
    if status == "invalid_type":
        return (value.startswith("<capability_ref:") and value.endswith(">")
                and bool(value[len("<capability_ref:"):-1]))
    if status == "malformed_step":
        return (value.startswith("<step:") and value.endswith(">")
                and bool(value[len("<step:"):-1]))
    return False


def normalize_request_capability_catalog(
    value: Any,
) -> tuple[tuple[str, str, str], ...] | None:
    """Return a canonical request-local ref map or reject an untrusted shape."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    normalized: list[tuple[str, str, str]] = []
    for index, entry in enumerate(value, 1):
        if not isinstance(entry, Mapping) or set(entry) != {
            "ref", "agent_id", "intent"
        }:
            return None
        ref = entry.get("ref")
        agent_id = entry.get("agent_id")
        intent = entry.get("intent")
        if any(not isinstance(item, str) or not item.strip()
               for item in (ref, agent_id, intent)):
            return None
        if ref != f"cap_{index:04d}":
            return None
        normalized.append((ref, agent_id, intent))
    pairs = [(agent_id, intent) for _, agent_id, intent in normalized]
    if pairs != sorted(set(pairs)):
        return None
    return tuple(normalized)


def raw_capability_ref_matches_catalog(
    raw_ref: Any,
    raw_intent: Any,
    catalog: tuple[tuple[str, str, str], ...],
) -> bool:
    """Bind one raw wire event to the exact catalog entry it resolved through."""
    if not isinstance(raw_ref, Mapping) or not isinstance(raw_intent, str):
        return False
    resolved_agent_id = raw_ref.get("resolved_agent_id")
    resolved_intent = raw_ref.get("resolved_intent")
    if not isinstance(resolved_agent_id, str) or not isinstance(resolved_intent, str):
        return False
    by_ref = {ref: (agent_id, intent) for ref, agent_id, intent in catalog}
    value = raw_ref.get("value")
    status = raw_ref.get("status")
    if status == "admitted":
        pair = by_ref.get(value)
        return (pair is not None
                and pair == (resolved_agent_id, resolved_intent)
                and raw_intent == resolved_intent)
    if status == "unknown":
        if value in by_ref:
            return False
    return (resolved_agent_id == ""
            and resolved_intent == INVALID_CAPABILITY_REFERENCE
            and raw_intent == INVALID_CAPABILITY_REFERENCE)


@dataclass(frozen=True)
class ValidationTrace:
    raw_intents: tuple[str, ...]
    raw_candidate: PlanSnapshot
    admitted_intents: tuple[str, ...]
    accepted: PlanSnapshot
    result: str
    stage: str = "build"
    attempt: int = 0
    wire_mode: str = "direct"
    raw_capability_refs: tuple[RawCapabilityReference, ...] = ()
    request_capability_catalog: tuple[tuple[str, str, str], ...] = ()


@dataclass
class TraceSink:
    hints: list[HintTrace] = field(default_factory=list)
    plans: list[PlannerTrace] = field(default_factory=list)
    validations: list[ValidationTrace] = field(default_factory=list)
    # 探针自己出的错。**不许静默、也不许拿它打死整趟跑批**——见 `attach_validation_trace`。
    trace_errors: list[str] = field(default_factory=list)
    # `PlanBuilder._fallback` 的调用记录。**这份计划不是 planner 的判断**——
    # 两次解析都没成、由编排兜底合成出来的。见 `probe_builder` 的说明。
    fallbacks: list[str] = field(default_factory=list)
    # Test-only call context used by the shared validation observer.
    validation_stage: str = "build"


@dataclass
class RetrievalProbe:
    """一次跑批里语义检索通道的实际服务情况。

    `calls` 只数**真的要向量的调用**（空输入不算）；`degraded` 数其中没拿到向量的那些
    —— 超时、网关不可达、以及**失败冷却期内被直接跳过**的都算，因为它们对这一轮的效果
    是同一件事：该轮只跑了词法档。
    """
    calls: int = 0
    degraded: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"calls": self.calls, "degraded": self.degraded}


@contextlib.contextmanager
def probe_retrieval():
    """把「语义检索**跑到一半掉档**」变成可观测事实，跑完逐字还原。

    首跑自查时只在**范例预热**那一处防住了静默降级（发现清单 §3-2），逐轮的检索调用
    没防——同一条判据没有铺满它该铺的面，这已经是第三次了（另两次：`_reject_unreached_
    planner`、确定性层的 `unstable`）。

    2026-08-03 宿主实测：`EXEMPLAR_EMBED_TIMEOUT` 缺省 1.0s，而宿主到网关的一次 Embed
    要 0.27–1.12s，**首次调用（含建 channel）必然超时** → `embedding` 打 30s 失败冷却
    → 之后整整 30 秒的规划全跑纯词法。而预热用的是 `max(5.0, timeout)`，它成功了，于是
    报告照写 `retrieval_state=warm / warmed_exemplars=223`。**一份「看起来正常」的报告，
    量的却不是生产装配。**

    包的是 `embedding.embed_texts` 这个**模块属性**：`exemplars.py` 与 `skills.py` 都用
    `_embedding.embed_texts(...)` 的形式调用（不是 from-import 绑定），所以换属性就够，
    不必碰生产源码——这一批只动尺子。
    """
    from orchestrator.cloud import embedding

    probe = RetrievalProbe()
    original = embedding.embed_texts

    async def counted(texts, timeout_s: float = 1.0):
        out = await original(texts, timeout_s)
        if texts:                       # 空输入返回 None 是契约，不是降级
            probe.calls += 1
            if out is None:
                probe.degraded += 1
        return out

    embedding.embed_texts = counted
    try:
        yield probe
    finally:
        embedding.embed_texts = original


class TracingRouteHints:
    """先算命中名单再委派，于是 before/after 两份证据都留得下来。

    命中枚举逐字复刻 `RouteHintEngine.apply()` 的短路语义：replace 命中即停，
    append 继续。复刻而不是改造引擎——引擎为了观测而改行为，观测的就不是生产了。
    """

    def __init__(self, delegate, sink: TraceSink):
        self.delegate = delegate
        self.sink = sink

    def _enumerate(self, text: str, agent_map: dict) -> tuple[HintMatch, ...]:
        """命中名单。**枚举不出来就交空名单，绝不打断被观察的那次调用。**

        `no-hints` 消融臂把 `_route_hints` 换成 `_NoRouteHints`（只有 `apply`），
        于是 `delegate._ordered_hints` 直接 `AttributeError`——**又一次把整趟全量打死**
        （2026-08-03 实测，这条路只在 `--ablations on-failure` 下可达，而发现轨主跑
        一直是 `off`，所以从没被走到）。那是**合法的替身**，不是错误：它就该报「一条
        hint 都没有」。同款判据见 `attach_validation_trace` 的兜底段。
        """
        if not (hasattr(self.delegate, "_ordered_hints")
                and hasattr(self.delegate, "_match")):
            return ()
        matches: list[HintMatch] = []
        try:
            for agent_id, hint in self.delegate._ordered_hints(agent_map):
                if self.delegate._match(hint, text) is None:
                    continue
                policy = (hint.policy or "replace").lower()
                matches.append(HintMatch(agent_id, str(hint.intent), policy,
                                         int(hint.priority or 0)))
                if policy != "append":
                    break
        except Exception as exc:                       # noqa: BLE001
            self.sink.trace_errors.append(
                f"hint_enumeration {type(exc).__name__}: {exc} | text={text[:40]!r}")
            return ()
        return tuple(matches)

    def apply(self, plan, text: str, agent_map: dict) -> bool:
        matches = self._enumerate(text, agent_map)
        before = snapshot_plan(plan)
        hit = self.delegate.apply(plan, text, agent_map)
        self.sink.hints.append(HintTrace(
            text=text, matches=matches, before=before,
            after=snapshot_plan(plan), hit=hit))
        return hit


class RecordingPlanner:
    """代理 PlanBuilder，记录 build/replan 产出。其余属性透传给被代理实例。"""

    def __init__(self, delegate, sink: TraceSink):
        self.delegate = delegate
        self.sink = sink

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def build(self, *args, **kwargs):
        previous = self.sink.validation_stage
        self.sink.validation_stage = "build"
        try:
            plan = await self.delegate.build(*args, **kwargs)
        finally:
            self.sink.validation_stage = previous
        self.sink.plans.append(PlannerTrace("build", snapshot_plan(plan)))
        return plan

    async def replan(self, goal, *args, **kwargs):
        previous = self.sink.validation_stage
        self.sink.validation_stage = "replan"
        try:
            decision = await self.delegate.replan(goal, *args, **kwargs)
        finally:
            self.sink.validation_stage = previous
        self.sink.plans.append(PlannerTrace(
            "replan", snapshot_plan(decision.to_plan(goal)), done=decision.done))
        return decision


def _as_mapping(value) -> dict[str, Any]:
    """**校验前的候选是未经任何清洗的模型输出**，什么形状都可能出现。

    2026-08-03 实测：模型把 `slots` 写成了一个字符串列表，`dict(["mode"])` 直接抛
    `ValueError: dictionary update sequence element #0 has length 4`——**整趟 L1 全量
    在跑到一半时被这个观察者杀死**。生产侧的 `_parse_and_validate_data` 已经安全地
    解析完了，是 trace 探针自己炸的。

    观察者绝不能比被观察的东西更脆弱。畸形结构一律降级成空：它只用于
    `raw_planner_pass` 这个**辅助**证据，降级只会让该证据更保守（槽位断言裁不过），
    不会把错的说成对的。
    """
    return dict(value) if isinstance(value, dict) else {}


def _as_sequence(value) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


_INVALID_CAPABILITY_REFERENCE = INVALID_CAPABILITY_REFERENCE
_RAW_REF_LIMIT = 80


def _bounded_ref(value: str) -> str:
    """Keep diagnostic identity useful without letting arbitrary wire text bloat reports."""
    clean = "".join(ch if ch.isprintable() else "?" for ch in str(value))
    return clean if len(clean) <= _RAW_REF_LIMIT else clean[:_RAW_REF_LIMIT] + "…"


def _raw_capability_references(data: Any, ref_to_pair=None,
                               *, allow_missing_steps: bool = False
                               ) -> tuple[RawCapabilityReference, ...]:
    if not isinstance(data, dict):
        return (RawCapabilityReference("<wire:not-object>", "malformed_wire"),)
    if "steps" not in data:
        return (() if allow_missing_steps else
                (RawCapabilityReference("<steps:missing>", "missing_steps"),))
    rows = data.get("steps")
    if not isinstance(rows, list):
        return (RawCapabilityReference(
            f"<malformed-steps:type={type(rows).__name__}>",
            "malformed_steps",
        ),)
    out = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(RawCapabilityReference(
                f"<step:{type(row).__name__}>", "malformed_step"))
            continue
        if "agent_id" in row or "intent" in row:
            legacy = f"agent_id={row.get('agent_id', '')};intent={row.get('intent', '')}"
            out.append(RawCapabilityReference(_bounded_ref(legacy), "legacy_identity"))
            continue
        if "capability_ref" not in row:
            out.append(RawCapabilityReference("<capability_ref:missing>", "missing"))
            continue
        ref = row.get("capability_ref")
        if not isinstance(ref, str):
            out.append(RawCapabilityReference(
                f"<capability_ref:{type(ref).__name__}>", "invalid_type"))
            continue
        if ref_to_pair is not None and ref in ref_to_pair:
            status = "admitted"
        elif _REQUEST_CAPABILITY_REF.fullmatch(ref):
            status = "unknown"
        else:
            status = "malformed_reference"
        out.append(RawCapabilityReference(_bounded_ref(ref), status))
    return tuple(out)


def _raw_identity(row, ref_to_pair=None) -> tuple[str, str]:
    if not isinstance(row, dict):
        return "", _INVALID_CAPABILITY_REFERENCE
    if ref_to_pair is None:
        # Standalone shape-fuzz tests can still snapshot the validator's internal,
        # already-resolved representation.  Production tracing always supplies the
        # request catalog below and therefore never treats legacy LLM wire as valid.
        return (str(row.get("agent_id") or ""), str(row.get("intent") or ""))
    if "agent_id" in row or "intent" in row:
        return "", _INVALID_CAPABILITY_REFERENCE
    ref = row.get("capability_ref")
    if not isinstance(ref, str):
        return "", _INVALID_CAPABILITY_REFERENCE
    pair = ref_to_pair.get(ref)
    if pair is None:
        return "", _INVALID_CAPABILITY_REFERENCE
    return str(pair[0]), str(pair[1])


def snapshot_raw_candidate(data: Any, ref_to_pair=None,
                           *, allow_missing_steps: bool = False) -> PlanSnapshot:
    """把已解析但尚未 capability validation 的结构转成可裁判快照。

    只比较 raw_intents 不够：依赖、槽位和额外步在校验前是否正确，同样要用 judge_plan
    裁一次，否则「校验前就错了」和「校验把对的丢了」分不开。
    """
    if isinstance(data, dict) and "steps" not in data:
        rows = [] if allow_missing_steps else [None]
    elif isinstance(data, dict) and isinstance(data.get("steps"), list):
        rows = data["steps"]
    else:
        # A present non-list container is one invalid raw identity, not an
        # observed empty plan. This keeps malformed fallback attempts in the
        # existing hallucination denominator.
        rows = [None]
    steps = []
    for index, row in enumerate(rows, 1):
        agent_id, intent = _raw_identity(row, ref_to_pair)
        row_data = row if isinstance(row, dict) else {}
        steps.append(StepSnapshot(
            id=str(row_data.get("id") or f"raw-{index}"),
            agent_id=agent_id,
            intent=intent,
            slots=_as_mapping(row_data.get("slots")),
            depends_on=_as_sequence(row_data.get("depends_on")),
            slot_refs=_as_mapping(row_data.get("slot_refs")),
            require_confirm=bool(row_data.get("require_confirm", False)),
        ))
    data_map = data if isinstance(data, dict) else {}
    return PlanSnapshot(
        steps=tuple(steps), complexity=str(data_map.get("complexity") or "simple"),
        goal=str(data_map.get("goal") or ""), skills=(), exemplars=(),
        hint_effect="", catalog_stats={})


def attach_validation_trace(builder, sink: TraceSink) -> None:
    original = builder._parse_and_validate_data

    def traced(wire, catalog, text):
        # 快照必须在生产解析**之前**取：`original` 可能就地改 `data`。
        # 但取快照本身也可能抛（`data` 是未经清洗的模型输出），所以它也在保护里。
        raw, failed = None, ""
        try:
            raw = deepcopy(wire)
        except Exception as exc:                       # noqa: BLE001
            failed = f"{type(exc).__name__}: {exc}"
            sink.trace_errors.append(f"{failed} | text={text[:40]!r}")
        try:
            plan = original(wire, catalog, text)
        except Exception as exc:                       # noqa: BLE001
            # A production validator failure must keep its original semantics.
            sink.trace_errors.append(
                f"validation_delegate:{type(exc).__name__}: {exc} | text={text[:40]!r}")
            raise
        if failed:
            return plan
        # 兜底 except 在这里是**有意的**：`raw` 的畸形形状穷举不完，而一次 traceback
        # 会把整趟全量打死（实测：模型把 `slots` 写成字符串列表，`dict(["mode"])` 抛
        # `ValueError`，烧掉一次 L1 全量——**而生产侧已经安全解析完了**）。
        # 观察者不能比被观察的东西更脆弱，也不该改变它的行为或时序。
        # 但不许静默：这一轮记成「没有 raw 通道」（`raw_observed=False`，如实，不进
        # 幻觉率分母），异常留进 `trace_errors` 由摘要打出来。
        try:
            legal_omission = (
                isinstance(raw, dict)
                and "steps" not in raw
                and plan is not None
                and (getattr(plan, "addressed", True) is False
                     or bool(getattr(plan, "clarify", None)))
            )
            raw_candidate = snapshot_raw_candidate(
                raw, catalog.ref_to_pair,
                allow_missing_steps=legal_omission)
            raw_refs = _raw_capability_references(
                raw, catalog.ref_to_pair,
                allow_missing_steps=legal_omission)
            raw_intents = tuple(step.intent for step in raw_candidate.steps
                                if step.intent)
            admitted = tuple(sorted(
                str(cap.intent)
                for agent in catalog.agent_map.values()
                for cap in (getattr(agent.manifest, "capabilities", None) or [])))
            from orchestrator.cloud.planning import _VALIDATION_TRACE_CONTEXT
            context = dict(_VALIDATION_TRACE_CONTEXT.get() or {})
            trace = ValidationTrace(
                raw_intents=raw_intents, admitted_intents=admitted,
                raw_candidate=raw_candidate,
                accepted=(snapshot_plan(plan) if plan is not None
                          else PlanSnapshot.empty()),
                result="accepted" if plan is not None else "rejected",
                stage=str(context.get("stage") or sink.validation_stage),
                attempt=int(context.get("attempt", 0)),
                wire_mode=str(context.get("wire_mode") or "direct"),
                raw_capability_refs=raw_refs,
                request_capability_catalog=tuple(
                    (str(ref), str(pair[0]), str(pair[1]))
                    for ref, pair in catalog.ref_to_pair.items()
                ))
        except Exception as exc:                       # noqa: BLE001
            sink.trace_errors.append(f"{type(exc).__name__}: {exc} | text={text[:40]!r}")
            return plan
        sink.validations.append(trace)
        return plan

    builder._parse_and_validate_data = traced


@contextlib.contextmanager
def probe_builder(builder, sink: TraceSink):
    """把校验前候选与 Hint 前后计划接到**主入口**上，跑完逐字还原 builder。

    这两份证据原本只活在单测里：主 CLI 从不调用 `attach_validation_trace()`，
    `TracingRouteHints` 也只在 L0 的 hint 门面里用过。于是「每个 live 失败都有首偏离
    点」实际退化成「凡是失败一律记 PLANNER_DIVERGENCE」——连 L0（根本没有 Planner）
    的 5 条确定性失败都被贴上了这个标签。

    还原用 `__dict__` 级别的存取而不是重新赋值：`attach_validation_trace` 写的是实例
    属性，直接写回绑定方法会在实例上留下一个永久遮蔽类方法的副本，下一个案例再包一层
    就是双重 trace。

    **也记 `_fallback`**：两次解析都没成时编排会合成一个兜底计划（默认
    `chitchat.talk`），而 `plan.raw_llm` 此时**非空**——`_reject_unreached_planner` 那条
    「模型没被够着」的闸看不见它。于是「计划是模型判断出来的」和「计划是兜底合成的」
    在报告里长得一模一样。2026-08-03 实测的代价：`nq.hvac-keep.dont`「空调先别关」的
    gold 恰好就是 `chitchat.talk`，兜底产物与正确答案逐字相同，**这条用例的绿证明不了
    否定语义有没有被消费**。判据用 `_fallback` 被不被调到，不用「计划长得像兜底」。
    """
    saved_parse = builder.__dict__.get("_parse_and_validate_data")
    saved_hints = getattr(builder, "_route_hints", None)
    saved_fallback = builder.__dict__.get("_fallback")
    # 没有这个钩子的 builder（脚本化替身）就是**没有 raw 通道**，不是「raw 一切正常」：
    # 上层据此把 `raw_observed=False`，该证据单元不进幻觉率分母。
    traceable = hasattr(builder, "_parse_and_validate_data")
    if traceable:
        attach_validation_trace(builder, sink)
    if saved_hints is not None:
        builder._route_hints = TracingRouteHints(saved_hints, sink)
    fallback_hook = hasattr(builder, "_fallback")
    if fallback_hook:
        inner = builder._fallback

        async def traced_fallback(text, agents=None):
            sink.fallbacks.append(str(text))
            return await inner(text, agents)

        builder._fallback = traced_fallback
    try:
        yield sink
    finally:
        if traceable:
            if saved_parse is None:
                builder.__dict__.pop("_parse_and_validate_data", None)
            else:
                builder.__dict__["_parse_and_validate_data"] = saved_parse
        if saved_hints is not None:
            builder._route_hints = saved_hints
        if fallback_hook:
            if saved_fallback is None:
                builder.__dict__.pop("_fallback", None)
            else:
                builder.__dict__["_fallback"] = saved_fallback


def asset_digest(root: Path, paths: list[Path]) -> str:
    """相对路径 + 内容的稳定摘要。顺序无关、内容敏感——换个 glob 顺序不该换指纹。"""
    digest = hashlib.sha256()
    root = Path(root).resolve()
    for path in sorted({Path(p).resolve() for p in paths},
                       key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class DivergenceEvidence:
    """`None` = **没观测**，与 `False`（观测了、没翻正）是两回事。

    原来这七个字段都是 `bool` 且默认 `False`，于是「一个对照都没跑」和「所有对照都
    跑了都没翻正」得到同一个结论 `PLANNER_DIVERGENCE`。首偏离点因此变成了失败的同义
    词——它本该是**排除法的产物**。
    """
    full_entry_pass: bool = False
    engine_direct_pass: bool | None = None
    planner_post_hint_pass: bool | None = None
    empty_history_pass: bool | None = None
    retrieval_ablation_pass: bool | None = None
    pre_hint_pass: bool | None = None
    raw_planner_pass: bool | None = None


# 执行顺序即语义：Edge 先于 Engine 状态恢复，恢复先于上下文，上下文先于检索，
# 检索先于 Hint，Hint 先于校验，都排除掉才轮到 Planner 自己。
_DIVERGENCE_ORDER = (
    ("engine_direct_pass", "EDGE_DIVERGENCE"),
    ("planner_post_hint_pass", "STATE_RESTORE_DIVERGENCE"),
    ("empty_history_pass", "CONTEXT_DIVERGENCE"),
    ("retrieval_ablation_pass", "RETRIEVAL_SUSPECT"),
    ("pre_hint_pass", "HINT_DIVERGENCE"),
    ("raw_planner_pass", "VALIDATION_DIVERGENCE"),
)
# **L2 独有的两个边界。** L1 只跑 Planner：它没有 Edge，也没有要恢复的会话状态——
# 这两个边界对 L1 不是「没观测」，是**不存在**。
_L2_ONLY_BOUNDARIES = frozenset({"engine_direct_pass", "planner_post_hint_pass"})


def applicable_boundaries(layer: str = "") -> tuple[tuple[str, str], ...]:
    """本层**存在**的边界，按执行顺序。

    这条区分是 P1-B 的全部内容：`first_divergence` 原来固定从 L2 专属的两个字段开始
    检查，而 L1 按设计不跑那两条 arm，于是它们永远是 `None`、函数在看到后面任何证据
    之前就返回 `UNCLASSIFIED`——**L1 的首偏离点结构上不可达**，
    context/retrieval/hint/validation/planner 五个标签一个都出不来。

    「没观测」与「不适用」都不能当成「已排除」，但它们的处理方式相反：前者必须阻断
    结论，后者必须跳过。把它们压成同一个 `None` 正是上一批反复修的那类默认值错误。
    """
    if layer == "l1":
        return tuple(row for row in _DIVERGENCE_ORDER
                     if row[0] not in _L2_ONLY_BOUNDARIES)
    return _DIVERGENCE_ORDER


def first_divergence(evidence: DivergenceEvidence, layer: str = "") -> str:
    """按执行顺序找**最早**的不一致边界；证据不足返回 `UNCLASSIFIED`。

    只要还有一个更早的**适用**边界没被观测，就不能声称后面那个是「第一个」——那是在
    拿沉默当证据。`PLANNER_DIVERGENCE` 只在**前面每一层都实测过且都没翻正**时才成立。
    """
    if evidence.full_entry_pass:
        return "NONE"
    for field_name, label in applicable_boundaries(layer):
        value = getattr(evidence, field_name)
        if value is None:
            return "UNCLASSIFIED"
        if value:
            return label
    return "PLANNER_DIVERGENCE"


def divergence_candidates(evidence: DivergenceEvidence,
                          layer: str = "") -> tuple[str, ...]:
    """全部有正向证据的边界（不排序、不声称谁在前）。

    首偏离点要求「更早的都被排除」，代价是廉价证据（Hint 前后、校验前后是**免费**
    的，跑一次就有）在没跑消融时全被 `UNCLASSIFIED` 吞掉。候选名单把这份免费证据
    留下来，同时不冒充因果：`divergence` 才是结论，这里只是线索。
    """
    if evidence.full_entry_pass:
        return ()
    return tuple(label for field_name, label in applicable_boundaries(layer)
                 if getattr(evidence, field_name) is True)


def evidence_dict(evidence: DivergenceEvidence) -> dict[str, Any]:
    """观测台账：`null` = 没观测，`false` = 观测了没翻正。诊断时这两者不能混。"""
    return {field_name: getattr(evidence, field_name)
            for field_name, _ in _DIVERGENCE_ORDER}


# L0 没有 Planner、没有 Hint、没有校验——那一层的失败断言**自己就是**边界。
# 拿 L1/L2 的排除法去套 L0，只会得到一个恒为 PLANNER_DIVERGENCE 的标签。
_L0_ASSERTION_BOUNDARY = (
    ("no_side_effect_before_confirm", "EDGE_SIDE_EFFECT"),
    ("ingress", "EDGE_DIVERGENCE"),
    ("retrieval.", "RETRIEVAL_DIVERGENCE"),
)


def deterministic_divergence(failed_assertions: list[str]) -> str:
    """L0 的首偏离点：按执行顺序取第一个失败断言所属的边界。"""
    if not failed_assertions:
        return "NONE"
    for prefix, label in _L0_ASSERTION_BOUNDARY:
        if any(name.startswith(prefix) for name in failed_assertions):
            return label
    return "UNCLASSIFIED"


# ── 指纹输入：只纳入真实参与落域决策的资产 ────────────────────────────────
# 代码版本另由 git commit 记录。glob 一个都没命中 / 必选路径缺失 → 记 missing_assets，
# 不允许「静默跳过但仍称指纹完整」。
ASSET_GLOBS = (
    "test/eval_corpus/intent_adversarial/**/*.yaml",
    "agents/*/manifest.yaml",
    "agents/*/servers.yaml",
    "skills/guides/*.yaml",
    "skills/exemplars/*.yaml",
)
ASSET_FILES = (
    "orchestrator/edge/knowledge/commands.yaml",
    "orchestrator/edge/fast_intent.py",
)


def collect_assets(root: Path) -> tuple[list[Path], list[str]]:
    root = Path(root).resolve()
    paths: list[Path] = []
    missing: list[str] = []
    for pattern in ASSET_GLOBS:
        hits = sorted(root.glob(pattern))
        if not hits:
            missing.append(pattern)
        paths.extend(hits)
    for relative in ASSET_FILES:
        path = root / relative
        if path.is_file():
            paths.append(path)
        else:
            missing.append(relative)
    return paths, missing


def asset_fingerprint(root: Path) -> dict[str, Any]:
    paths, missing = collect_assets(root)
    return {
        "digest": asset_digest(root, paths) if paths else "",
        "file_count": len(set(paths)),
        "missing_assets": missing,
        "complete": not missing and bool(paths),
    }
