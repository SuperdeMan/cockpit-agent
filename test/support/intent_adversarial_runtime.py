"""对抗用例的分层运行门面：L0 离线组件、L1 真实 Planner、L2 完整决策链。

安全底线：本模块永远不触发真实车控、支付、消息或删除。L0 用真实 Edge servicer 但把
云端换成内进程替身；L1 只走 Planner；L2 的 Agent/VAL 一律 fake/spy。

**不 mock `classify()` / `split_and_classify_any()` / VAL**——那三个正是 L0 要证的东西，
mock 掉之后 L0 只剩自证。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(_ROOT), str(_ROOT / "gen" / "python"), str(_ROOT / "orchestrator" / "edge"),
           str(_ROOT / "test")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── 通用小工具 ─────────────────────────────────────────────────────────────


async def _async_noop(*_args, **_kwargs):
    return None


async def _collect(stream) -> list:
    return [event async for event in stream]


def _action_dict(action) -> dict[str, Any]:
    from google.protobuf.json_format import MessageToDict
    payload = MessageToDict(action.payload) if action.HasField("payload") else {}
    return {"type": action.type, "payload": payload,
            "require_confirm": bool(action.require_confirm)}


@contextlib.contextmanager
def temporary_env(values: dict[str, str]):
    """逐项还原：原本不存在的键要删掉，不是写回空串。

    空串和「未设置」在 `os.getenv(k, default)` 下是两种行为——还原成空串会把默认值
    悄悄关掉，下一个 arm 就不是单变量了。
    """
    saved: dict[str, str | None] = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


# ── L0-A：Edge ingress ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class EdgeObservation:
    ingress: str
    cloud_text: str
    state_delta: dict[str, Any]
    actions: tuple[dict[str, Any], ...]
    need_confirm: bool
    side_effects: tuple[dict[str, Any], ...]


def run_edge_turn(text: str, *, cloud_need_confirm: bool = False,
                  meta: dict[str, str] | None = None,
                  is_confirmation: bool = False) -> EdgeObservation:
    """真实 Edge servicer + 真实 VAL + 内进程云端替身 → ingress 与副作用观测。"""
    from cockpit.orchestrator.v1 import orchestrator_pb2
    from server import EdgeOrchestratorServicer

    srv = EdgeOrchestratorServicer()
    before = dict(srv.val.state)
    seen: dict[str, str] = {}

    async def fake_cloud_handle(req):
        seen["text"] = req.text
        final = orchestrator_pb2.FinalResult(
            speech="cloud", need_confirm=cloud_need_confirm)
        yield orchestrator_pb2.HandleEvent(final=final)

    srv.cloud.handle = fake_cloud_handle
    srv.obs.emit_turn = _async_noop
    srv.obs.emit_span = _async_noop
    srv.obs.emit_state = _async_noop
    srv.memory.append = _async_noop
    srv._nlu_shadow_bg = lambda *_args, **_kwargs: None
    request = orchestrator_pb2.HandleRequest(
        text=text, session_id="intent-adversarial", request_id="r1",
        is_confirmation=is_confirmation,
        meta={"memory_enabled": "false", **(meta or {})})
    events = asyncio.run(_collect(srv.Handle(request, None)))
    finals = [event.final for event in events if event.WhichOneof("event") == "final"]
    state_delta = {key: value for key, value in srv.val.state.items()
                   if before.get(key) != value}
    actions = tuple(_action_dict(action) for final in finals for action in final.actions)
    ingress = "mixed" if seen and state_delta else (
        "cloud" if seen else "edge_local")
    return EdgeObservation(
        ingress=ingress, cloud_text=seen.get("text", ""), state_delta=state_delta,
        actions=actions, need_confirm=any(final.need_confirm for final in finals),
        side_effects=tuple(action for action in actions
                           if action["type"] in {"vehicle.control", "media.control"}),
    )


# ── L0-B：Route Hint ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class HintObservation:
    hit: bool
    before: Any
    after: Any
    matches: tuple[Any, ...]


def run_hint_turn(text: str, initial_intents: tuple[str, ...], agents: list) -> HintObservation:
    """真实 RouteHintEngine + 真实 `PlanBuilder._validated_steps`，零 LLM。

    走真实 validator 是必须的：hint 补出的步同样要过「intent ∈ 能力集」这一关，
    自己拼一个宽松 validator 会让语料看起来通过、生产其实丢步。
    """
    from orchestrator.cloud.models import Plan, Step
    from orchestrator.cloud.planning import PlanBuilder
    from orchestrator.cloud.route_hints import RouteHintEngine
    from support.intent_adversarial_trace import TraceSink, TracingRouteHints

    agent_map = {a.manifest.agent_id: a for a in agents}
    steps = []
    for index, intent in enumerate(initial_intents, 1):
        agent_id = next((aid for aid, a in agent_map.items()
                         if any(getattr(cap, "intent", "") == intent
                                for cap in (a.manifest.capabilities or []))), "")
        steps.append(Step(id=f"s{index}", agent_id=agent_id,
                          endpoint=f"{agent_id}:0", intent=intent))
    plan = Plan(steps=steps)
    sink = TraceSink()
    engine = TracingRouteHints(RouteHintEngine(PlanBuilder._validated_steps), sink)
    hit = engine.apply(plan, text, agent_map)
    trace = sink.hints[-1]
    return HintObservation(hit=hit, before=trace.before, after=trace.after,
                           matches=trace.matches)


# ── L0-C：Skill / Exemplar 检索 ────────────────────────────────────────────


@dataclass(frozen=True)
class RetrievalObservation:
    skills: tuple[str, ...]
    exemplars: tuple[str, ...]
    skills_block: str
    exemplars_block: str


def run_retrieval_turn(text: str) -> RetrievalObservation:
    """纯词法档检索：语义通道要打网关 Embed，L0 必须零网络确定。"""
    from orchestrator.cloud import exemplars as _exemplars
    from orchestrator.cloud import skills as _skills

    async def _both():
        return await asyncio.gather(_skills.plan_skills(text),
                                    _exemplars.plan_exemplars(text))

    with temporary_env({"SKILLS_RETRIEVAL": "lexical",
                        "EXEMPLARS_RETRIEVAL": "lexical"}):
        (_, sk_names, sk_block), (_, ex_names, ex_block) = asyncio.run(_both())
    return RetrievalObservation(
        skills=tuple(sk_names or []), exemplars=tuple(ex_names or []),
        skills_block=sk_block or "", exemplars_block=ex_block or "")


# ── L0-D：catalog 预算与权限过滤 ──────────────────────────────────────────


@dataclass(frozen=True)
class CatalogObservation:
    chars_full: int
    chars_final: int
    dropped: tuple[str, ...]
    admitted_intents: tuple[str, ...]


def run_catalog_l0(agents: list, budget_chars: int | None = None,
                   granted_permissions: list[str] | None = None) -> CatalogObservation:
    """真实 `WorkingSet.render_catalog` + 真实 `PlanBuilder._filter_by_permission`。

    预算是模块级常量，只能临时替换再 finally 还原——复制一份 catalog 算法来「模拟」
    裁剪，就等于测了一个和生产同名但不同源的东西。
    """
    from orchestrator.cloud import context as _context
    from orchestrator.cloud.context import WorkingSet
    from orchestrator.cloud.planning import PlanBuilder

    admitted_source = agents
    if granted_permissions is not None:
        admitted_source = PlanBuilder._filter_by_permission(
            list(agents), list(granted_permissions))
    stats: dict[str, Any] = {}
    original = _context._CATALOG_BUDGET
    try:
        if budget_chars is not None:
            _context._CATALOG_BUDGET = int(budget_chars)
        WorkingSet.render_catalog(list(admitted_source), stats)
    finally:
        _context._CATALOG_BUDGET = original
    dropped = tuple(stats.get("dropped") or ())
    admitted = tuple(sorted(
        str(cap.intent)
        for agent in admitted_source
        if getattr(agent.manifest, "agent_id", "") not in dropped
        for cap in (getattr(agent.manifest, "capabilities", None) or [])
        if getattr(cap, "intent", "")))
    return CatalogObservation(
        chars_full=int(stats.get("chars_full", 0)),
        chars_final=int(stats.get("chars_final", 0)),
        dropped=dropped, admitted_intents=admitted)


# ── 重复策略 ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RepeatOutcome:
    passed: bool
    signature: str
    dangerous: bool = False


@dataclass(frozen=True)
class RepeatClassification:
    status: str
    outcomes: tuple[RepeatOutcome, ...]


def classify_repeats(outcomes: list[RepeatOutcome], risk: str) -> RepeatClassification:
    """`unstable` 是独立状态：既不算通过，也不冒充稳定缺陷。

    三次结果分裂说明这句话本身在采样噪声里，把它当产品缺陷登记会污染修复清单；
    当通过则会把真实的不确定性藏起来。
    """
    if any(outcome.dangerous for outcome in outcomes):
        return RepeatClassification("critical_fail", tuple(outcomes))
    if all(outcome.passed for outcome in outcomes):
        return RepeatClassification("pass", tuple(outcomes))
    wrong = Counter(outcome.signature for outcome in outcomes if not outcome.passed)
    status = "stable_fail" if wrong and max(wrong.values()) >= 2 else "unstable"
    return RepeatClassification(status, tuple(outcomes))


async def run_with_repeats(run_once, *, risk: str, failure_repeats: int,
                           high_risk_repeats: int) -> RepeatClassification:
    target = high_risk_repeats if risk in {"high", "critical"} else 1
    outcomes = [await run_once() for _ in range(target)]
    if target == 1 and not outcomes[0].passed:
        outcomes.extend([await run_once() for _ in range(failure_repeats - 1)])
    return classify_repeats(outcomes, risk)
