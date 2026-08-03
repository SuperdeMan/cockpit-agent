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
class ValidationTrace:
    raw_intents: tuple[str, ...]
    raw_candidate: PlanSnapshot
    admitted_intents: tuple[str, ...]
    accepted: PlanSnapshot
    result: str


@dataclass
class TraceSink:
    hints: list[HintTrace] = field(default_factory=list)
    plans: list[PlannerTrace] = field(default_factory=list)
    validations: list[ValidationTrace] = field(default_factory=list)
    # `PlanBuilder._fallback` 的调用记录。**这份计划不是 planner 的判断**——
    # 两次解析都没成、由编排兜底合成出来的。见 `probe_builder` 的说明。
    fallbacks: list[str] = field(default_factory=list)


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

    def apply(self, plan, text: str, agent_map: dict) -> bool:
        matches: list[HintMatch] = []
        for agent_id, hint in self.delegate._ordered_hints(agent_map):
            if self.delegate._match(hint, text) is None:
                continue
            policy = (hint.policy or "replace").lower()
            matches.append(HintMatch(agent_id, str(hint.intent), policy,
                                     int(hint.priority or 0)))
            if policy != "append":
                break
        before = snapshot_plan(plan)
        hit = self.delegate.apply(plan, text, agent_map)
        self.sink.hints.append(HintTrace(
            text=text, matches=tuple(matches), before=before,
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
        plan = await self.delegate.build(*args, **kwargs)
        self.sink.plans.append(PlannerTrace("build", snapshot_plan(plan)))
        return plan

    async def replan(self, goal, *args, **kwargs):
        decision = await self.delegate.replan(goal, *args, **kwargs)
        self.sink.plans.append(PlannerTrace(
            "replan", snapshot_plan(decision.to_plan(goal)), done=decision.done))
        return decision


def snapshot_raw_candidate(data: dict[str, Any]) -> PlanSnapshot:
    """把已解析但尚未 capability validation 的结构转成可裁判快照。

    只比较 raw_intents 不够：依赖、槽位和额外步在校验前是否正确，同样要用 judge_plan
    裁一次，否则「校验前就错了」和「校验把对的丢了」分不开。
    """
    rows = data.get("steps") if isinstance(data, dict) else []
    steps = []
    for index, row in enumerate(rows or [], 1):
        if not isinstance(row, dict):
            continue
        steps.append(StepSnapshot(
            id=str(row.get("id") or f"raw-{index}"),
            agent_id=str(row.get("agent_id") or ""),
            intent=str(row.get("intent") or ""),
            slots=dict(row.get("slots") or {}),
            depends_on=tuple(row.get("depends_on") or []),
            slot_refs=dict(row.get("slot_refs") or {}),
            require_confirm=bool(row.get("require_confirm", False)),
        ))
    return PlanSnapshot(
        steps=tuple(steps), complexity=str(data.get("complexity") or "simple"),
        goal=str(data.get("goal") or ""), skills=(), exemplars=(),
        hint_effect="", catalog_stats={})


def attach_validation_trace(builder, sink: TraceSink) -> None:
    original = builder._parse_and_validate_data

    def traced(data, agent_map, text):
        raw = deepcopy(data) if isinstance(data, dict) else {}
        raw_intents = tuple(str(step.get("intent") or "")
                            for step in raw.get("steps") or []
                            if isinstance(step, dict) and step.get("intent"))
        admitted = tuple(sorted(
            str(cap.intent)
            for agent in agent_map.values()
            for cap in (getattr(agent.manifest, "capabilities", None) or [])))
        plan = original(data, agent_map, text)
        sink.validations.append(ValidationTrace(
            raw_intents=raw_intents, admitted_intents=admitted,
            raw_candidate=snapshot_raw_candidate(raw),
            accepted=snapshot_plan(plan) if plan is not None else PlanSnapshot.empty(),
            result="accepted" if plan is not None else "rejected"))
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


def first_divergence(evidence: DivergenceEvidence) -> str:
    """按执行顺序找**最早**的不一致边界；证据不足返回 `UNCLASSIFIED`。

    只要还有一个更早的边界没被观测，就不能声称后面那个是「第一个」——那是在拿沉默
    当证据。`PLANNER_DIVERGENCE` 只在**前面每一层都实测过且都没翻正**时才成立。
    """
    if evidence.full_entry_pass:
        return "NONE"
    for field_name, label in _DIVERGENCE_ORDER:
        value = getattr(evidence, field_name)
        if value is None:
            return "UNCLASSIFIED"
        if value:
            return label
    return "PLANNER_DIVERGENCE"


def divergence_candidates(evidence: DivergenceEvidence) -> tuple[str, ...]:
    """全部有正向证据的边界（不排序、不声称谁在前）。

    首偏离点要求「更早的都被排除」，代价是廉价证据（Hint 前后、校验前后是**免费**
    的，跑一次就有）在没跑消融时全被 `UNCLASSIFIED` 吞掉。候选名单把这份免费证据
    留下来，同时不冒充因果：`divergence` 才是结论，这里只是线索。
    """
    if evidence.full_entry_pass:
        return ()
    return tuple(label for field_name, label in _DIVERGENCE_ORDER
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
