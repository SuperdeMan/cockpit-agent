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
    # 本轮端侧真正落到 VAL 的 (object, operate)，以及其中需要二次确认的那些。
    val_commands: tuple[tuple[str, str], ...] = ()
    dangerous_commands: tuple[tuple[str, str], ...] = ()


class EdgeSession:
    """一条 case 的 Edge 侧会话：**跨轮共用同一个 servicer**。

    每轮新建 servicer 会把 VAL 状态和端侧会话态一起清零——多轮用例里「第二轮不该
    重复执行」「挂起还在不在」这类断言就永远为真。单轮用例用 `run_edge_turn()`
    走同一条路，只是会话活一轮。
    """

    def __init__(self, session_id: str = "intent-adversarial",
                 *, cloud_need_confirm: bool = False):
        from server import EdgeOrchestratorServicer
        self.session_id = session_id
        self.cloud_need_confirm = cloud_need_confirm
        self.srv = EdgeOrchestratorServicer()
        self.cloud_texts: list[str] = []
        self._turn = 0
        self._install_probes()

    def _install_probes(self) -> None:
        from cockpit.orchestrator.v1 import orchestrator_pb2
        srv = self.srv

        async def fake_cloud_handle(req):
            self.cloud_texts.append(req.text)
            self._seen_this_turn = True
            final = orchestrator_pb2.FinalResult(
                speech="cloud", need_confirm=self.cloud_need_confirm)
            yield orchestrator_pb2.HandleEvent(final=final)

        srv.cloud.handle = fake_cloud_handle
        srv.obs.emit_turn = _async_noop
        srv.obs.emit_span = _async_noop
        srv.obs.emit_state = _async_noop
        srv.memory.append = _async_noop
        srv._nlu_shadow_bg = lambda *_args, **_kwargs: None
        self._val_log = install_val_probe(srv.val)

    def turn(self, text: str, *, meta: dict[str, str] | None = None,
             is_confirmation: bool = False) -> EdgeObservation:
        from cockpit.orchestrator.v1 import orchestrator_pb2
        self._turn += 1
        self._seen_this_turn = False
        before = dict(self.srv.val.state)
        self._val_log.clear()
        request = orchestrator_pb2.HandleRequest(
            text=text, session_id=self.session_id, request_id=f"r{self._turn}",
            is_confirmation=is_confirmation,
            meta={"memory_enabled": "false", **(meta or {})})
        events = asyncio.run(_collect(self.srv.Handle(request, None)))
        return _edge_observation(events, before, self.srv.val.state,
                                 cloud_text=(text if self._seen_this_turn else ""),
                                 reached_cloud=self._seen_this_turn,
                                 val=self.srv.val, commands=list(self._val_log))


def install_val_probe(val) -> list[tuple[str, str]]:
    """记录端侧**真的落到 VAL 的** (object, operate)，返回可清空的日志。

    为什么不直接看 `state` 差分：状态键名由 `_simulate` 逐 object 手写（aircon 会写
    `hvac_on`/`hvac_temp`/`hvac_wind_speed`），在测试里复刻一份 key→object 映射就是
    第二套口径，改一边忘一边必然发生。这里只观察调用参数，危险与否交给生产自己的
    `_need_confirm()` 判。
    """
    log: list[tuple[str, str]] = []
    original = val._simulate

    def traced(obj, operate, data):
        log.append((str(obj), str(operate)))
        return original(obj, operate, data)

    val._simulate = traced
    return log


def _edge_observation(events, before: dict, after: dict, *, cloud_text: str,
                      reached_cloud: bool, val=None,
                      commands: list[tuple[str, str]] | None = None
                      ) -> EdgeObservation:
    finals = [event.final for event in events if event.WhichOneof("event") == "final"]
    state_delta = {key: value for key, value in after.items()
                   if before.get(key) != value}
    actions = tuple(_action_dict(action) for final in finals for action in final.actions)
    ingress = ("mixed" if reached_cloud and state_delta
               else "cloud" if reached_cloud else "edge_local")
    executed = tuple(commands or ())
    dangerous = tuple(row for row in executed
                      if val is not None and val._need_confirm(row[0]))
    return EdgeObservation(
        ingress=ingress, cloud_text=cloud_text, state_delta=state_delta,
        actions=actions, need_confirm=any(final.need_confirm for final in finals),
        side_effects=tuple(action for action in actions
                           if action["type"] in {"vehicle.control", "media.control"}),
        val_commands=executed, dangerous_commands=dangerous,
    )


def run_edge_turn(text: str, *, cloud_need_confirm: bool = False,
                  meta: dict[str, str] | None = None,
                  is_confirmation: bool = False) -> EdgeObservation:
    """真实 Edge servicer + 真实 VAL + 内进程云端替身 → ingress 与副作用观测。"""
    session = EdgeSession(cloud_need_confirm=cloud_need_confirm)
    return session.turn(text, meta=meta, is_confirmation=is_confirmation)


def edge_side_effect_rows(edge: EdgeObservation) -> tuple[dict[str, Any], ...]:
    """Edge 侧「确认前副作用」的证据 = **端侧执行了需要二次确认的对象**。

    口径刻意窄：`no_side_effect_before_confirm` 问的不是「有没有发生任何状态变化」，
    而是「危险动作有没有在确认前落地」。同一句「打开空调，再把后备箱打开」里
    `hvac_on=True` 是完全正确的本地执行——把它算成副作用会把正确行为判红。

    危险与否用生产自己的 `VAL._need_confirm()` 判，不在测试里另立一份对象清单。
    """
    rows = [{"source": "edge", "type": "val.execute", "object": obj,
             "operate": operate, "delta": dict(edge.state_delta)}
            for obj, operate in edge.dangerous_commands]
    # 端侧回传的车控/媒体动作沿用既有口径（`require_confirm=True` 的那些是**待确认
    # 提案**不是已落地效果，但它们本来就不该出现在端侧终局里，一并留证）。
    rows += [{"source": "edge", "type": action["type"], "payload": action["payload"],
              "require_confirm": action["require_confirm"]}
             for action in edge.side_effects]
    return tuple(rows)


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
    import eval_live as _eval_live
    from orchestrator.cloud import exemplars as _exemplars
    from orchestrator.cloud import skills as _skills
    from orchestrator.cloud.planning import _assemble_capability_catalog

    # L0 observes the same request-local asset rendering as production.  Building
    # the final visible catalog is filesystem-only; it neither calls Embed nor any
    # provider.  A static mapping would drift whenever manifests or budget pruning
    # change, so assemble it from the real live inventory on every observation.
    catalog = _assemble_capability_catalog(
        _eval_live.load_agents(include_edge=True))

    async def _both():
        return await asyncio.gather(
            _skills.plan_skills(text, capability_refs=catalog.pair_to_ref),
            _exemplars.plan_exemplars(text, capability_refs=catalog.pair_to_ref),
        )

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
    process_run_id: str = ""
    sample_index: int = 0
    raw_intents: tuple[str, ...] = ()
    raw_capability_refs: tuple[dict[str, Any], ...] = ()
    request_capability_catalog: tuple[dict[str, str], ...] = ()
    raw_observed: bool = False
    validation_observed: bool = False
    actual_intents: tuple[str, ...] = ()
    plan_from_fallback: bool = False


@dataclass(frozen=True)
class RepeatClassification:
    status: str
    outcomes: tuple[RepeatOutcome, ...]


def classify_repeats(outcomes: list[RepeatOutcome], risk: str,
                     deterministic: bool = False) -> RepeatClassification:
    """`unstable` 是独立状态：既不算通过，也不冒充稳定缺陷。

    三次结果分裂说明这句话本身在采样噪声里，把它当产品缺陷登记会污染修复清单；
    当通过则会把真实的不确定性藏起来。

    `deterministic=True`（L0：无模型参与）时**不存在 unstable**——同代码同输入
    100% 可复现，跑一次就是结论。不加这个开关，L0 的每一条低风险红灯都会被记成
    「不稳定」，于是既进不了修复清单也进不了门禁，白白丢掉一条确定的缺陷。
    """
    if any(outcome.dangerous for outcome in outcomes):
        return RepeatClassification("critical_fail", tuple(outcomes))
    if all(outcome.passed for outcome in outcomes):
        return RepeatClassification("pass", tuple(outcomes))
    if deterministic:
        return RepeatClassification("stable_fail", tuple(outcomes))
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


# ── L1：真实 Planner ───────────────────────────────────────────────────────


def skill_and_exemplar_inventory() -> tuple[set[str], set[str]]:
    """真实 store 的资产名单（force=True 绕过 mtime 缓存），供契约引用校验。"""
    from orchestrator.cloud import exemplars as _exemplars
    from orchestrator.cloud import skills as _skills
    names = {doc.name for doc in _skills.default_store().load(force=True)}
    eids = {item.eid for item in _exemplars.default_store().load(force=True)}
    return names, eids


def filter_unavailable_capabilities(agents: list, unavailable: set[str]) -> list:
    """深拷贝后过滤能力副本。

    原地过滤会污染同进程后续案例——A8「能力从 catalog 消失」的用例跑完，别人的
    catalog 也就永远少了那一项，而且悄无声息。
    """
    from copy import deepcopy
    if not unavailable:
        return list(agents)
    out = []
    for agent in agents:
        clone = deepcopy(agent)
        caps = [cap for cap in (getattr(clone.manifest, "capabilities", None) or [])
                if str(getattr(cap, "intent", "")) not in unavailable]
        try:
            del clone.manifest.capabilities[:]
            clone.manifest.capabilities.extend(caps)
        except (AttributeError, TypeError):
            clone.manifest.capabilities = caps
        out.append(clone)
    return out


def parse_focus(raw: dict[str, Any]):
    """只接受 `Focus` 的真实字段——未知 key 在这里报错，而不是被静默丢掉。"""
    from dataclasses import fields
    from orchestrator.cloud.context import Focus
    if not raw:
        return None
    allowed = {f.name for f in fields(Focus)}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown focus fields {sorted(unknown)}")
    return Focus(**raw)


def build_working_set(turn_context: dict[str, Any], agents: list):
    from orchestrator.cloud.context import WorkingSet
    catalog = filter_unavailable_capabilities(
        agents, set(turn_context.get("unavailable_intents") or []))
    return WorkingSet(
        catalog=catalog,
        history=list(turn_context.get("history") or []),
        memories=list(turn_context.get("memories") or []),
        focus=parse_focus(turn_context.get("focus") or {}),
    )


async def run_planner_turn(turn, agents, builder, *, granted_permissions=None):
    """一条用例的 L1 执行：初规划 +（按契约声明的）replan 序列。"""
    from orchestrator.cloud.models import PlanContext
    from support.intent_adversarial_judge import DecisionSnapshot
    from support.intent_adversarial_trace import snapshot_plan

    working_set = build_working_set(turn.context, agents)
    catalog = working_set.catalog
    ctx = PlanContext(
        request_id="intent-adversarial", session_id="intent-adversarial",
        user_id="eval-user", vehicle_id="eval-vehicle",
        granted_permissions=list(granted_permissions or []),
        raw_text=turn.utterance,
    )
    plan = await builder.build(turn.utterance, working_set, ctx,
                               granted_permissions=granted_permissions)
    replans = []
    # Mirror Engine._run_new_or_restored_plan exactly: a ``simple`` plan never
    # enters the bounded loop.  Gold may describe the replan that should happen,
    # but it must not grant the harness permission to manufacture that production
    # behavior when the initial planner classification is wrong.
    if plan.complexity == "adaptive":
        for expected_replan in turn.expected.replans:
            observation = dict(expected_replan.after["result"])
            # Production enters the bounded loop with ``plan.goal or text``. Goal
            # is optional on the model wire and is commonly blank, so preserve the
            # same fallback or the user's conditional request disappears.
            replan_goal = plan.goal or turn.utterance
            decision = await builder.replan(
                replan_goal, [observation], catalog, ctx,
                granted_permissions=granted_permissions,
                working_set=working_set,
                skill_names=list(plan.skills or []),
                exemplar_names=list(plan.exemplars or []),
            )
            replans.append(snapshot_plan(decision.to_plan(replan_goal)))
    return DecisionSnapshot(
        ingress="cloud", addressed=bool(plan.addressed),
        decision=("clarify" if plan.clarify and not plan.steps else
                  "execute" if plan.steps else
                  "reject" if not plan.addressed else "degrade"),
        clarify=bool(plan.clarify and not plan.steps), plan=snapshot_plan(plan),
        replans=tuple(replans),
    )


# ── 定向消融 ───────────────────────────────────────────────────────────────
# 默认只跑 full。全库全排列消融的成本是线性乘 arm 数，而绝大多数案例是绿的——
# 为绿灯付六倍模型开销买不到任何信息。

ABLATION_ARMS = ("no-hints", "no-skills", "no-exemplars", "empty-history",
                 "cloud-direct")

# **arm 按 layer 取**。`cloud-direct`（绕开 Edge 直连 Engine）与 `planner-only`
# （只跑 Planner，不恢复会话状态）在 L1 上没有对照物——L1 本来就没有 Edge、没有
# Engine 状态。原来无论失败来自哪层都只跑 L1 那组，于是 `EDGE_DIVERGENCE` 与
# `STATE_RESTORE_DIVERGENCE` 两个边界结构上永远不可达。
ABLATION_ARMS_BY_LAYER = {
    "l1": ("no-hints", "no-skills", "no-exemplars", "empty-history"),
    "l2": ("cloud-direct", "planner-only", "no-hints", "no-skills",
           "no-exemplars", "empty-history"),
}


def requested_ablations(repeat_status: str, layer: str = "l1") -> tuple[str, ...]:
    if repeat_status == "pass":
        return ()
    return ABLATION_ARMS_BY_LAYER.get(layer, ())


class _NoRouteHints:
    """no-hints arm：唯一变量是 Hint 引擎不生效，其余装配逐字不变。"""

    @staticmethod
    def apply(_plan, _text, _agent_map) -> bool:
        return False


def disable_route_hints(builder) -> None:
    builder._route_hints = _NoRouteHints()


def ablation_env(arm: str) -> dict[str, str]:
    if arm == "no-skills":
        return {"SKILLS_MODE": "off"}
    if arm == "no-exemplars":
        return {"EXEMPLARS_MODE": "off"}
    return {}


def ablation_context(arm: str, turn_context: dict[str, Any]) -> dict[str, Any]:
    """empty-history 只清上下文，catalog 保持不变——多动一个变量就归不了因。"""
    if arm != "empty-history":
        return dict(turn_context)
    kept = dict(turn_context)
    kept["history"] = []
    kept["memories"] = []
    kept["focus"] = {}
    return kept


def causal_effect(full_status: str, ablation_status: str, same_provider: bool,
                  same_assets: bool) -> str:
    """只有「稳定错 → 稳定对」且 provider/资产逐字相同，才配叫因果证据。

    一次翻转是噪声也解释得通；provider 或资产变了，翻转的原因根本不唯一。
    """
    if not (same_provider and same_assets):
        return "invalid"
    if full_status == "stable_fail" and ablation_status == "pass":
        return "supported"
    if ablation_status == "pass":
        return "suspect"
    return "none"


# ── L2：完整 Edge→Engine 决策链 ───────────────────────────────────────────
# 下游一律 fake/spy：本套件永远不触发真实车控、支付、消息、删除。
# 只有测试显式注入 `confirmed_response` 时才生成 action，并把它记进 side_effects——
# 「确认前零副作用」这条断言需要一个真的会被触发的对照物，否则它永远为真。


class _FakeResponse:
    """`agent_pb2.ExecuteResponse` 的结构等价体（不依赖 proto 生成代码即可单测）。"""

    def __init__(self, status=0, speech="", follow_up="", data=None, actions=None):
        self.status = status
        self.speech = speech
        self.follow_up = follow_up
        self.actions = list(actions or [])
        self.ui_card = None
        self.data = data
        self.missing_slots = []


class SafeClients:
    """Engine 的 clients 替身：真实形状、零真实副作用。"""

    def __init__(self, agents, *, history=(), memories=(), responses=None,
                 confirm_intents=(), confirmed_responses=None,
                 default_status: int = 0):
        self.agents = list(agents)
        self.history = list(history)
        self.memories = list(memories)
        self.responses = dict(responses or {})
        self.confirm_intents = set(confirm_intents)
        self.confirmed_responses = dict(confirmed_responses or {})
        self.default_status = default_status
        self.agent_calls: list[dict[str, Any]] = []
        self.side_effects: list[dict[str, Any]] = []

    async def list_agents(self):
        return list(self.agents)

    async def resolve(self, query="", intent="", top_k=1):
        if intent:
            return [a for a in self.agents if any(
                cap.intent == intent for cap in a.manifest.capabilities)][:top_k]
        return list(self.agents[:top_k])

    async def get_session(self, *_args, **_kwargs):
        return list(self.history)

    async def recall(self, *_args, **_kwargs):
        return list(self.memories)

    async def append_turn(self, session_id: str = "", role: str = "",
                          text: str = "", *_args, **_kwargs):
        """签名逐字对齐生产 `ContextAssembler.append_turn`（session_id, role, text, …）。

        生产侧这一步在 `memory_enabled=false` 时根本不会被调到，所以多轮 history
        由 `record_turn()` 显式累积——不能靠一个本轮不会执行的分支来串联上下文。
        """
        return self.record_turn(str(role or "user"), str(text or ""))

    def record_turn(self, role: str, text: str) -> None:
        """多轮用例的第二轮必须看得见第一轮。

        `history` 原本只有契约里写死的那份，同 session 连跑两轮也不会增长——
        「上一轮已经答过了」这类断言于是测不出任何东西。
        """
        if text:
            self.history.append({"role": role, "text": text})

    async def save_focus(self, *_args, **_kwargs):
        return None

    async def call_agent_stream(self, *_args, **_kwargs):
        if False:                      # 永不产出：L2 不测流式直通
            yield None

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None, **_kwargs):
        self.agent_calls.append({"intent": intent, "slots": dict(slots or {}),
                                 "meta": dict(meta or {})})
        return self._response_for(intent, dict(meta or {}))

    def _response_for(self, intent: str, meta: dict[str, str]):
        confirmed = meta.get("confirmed") == "true"
        if intent in self.confirm_intents and not confirmed:
            return _FakeResponse(status=1, speech=f"确认要执行 {intent} 吗？",
                                 follow_up="说『确认』即可")
        spec = self.confirmed_responses.get(intent) or {}
        actions = list(spec.get("actions") or [])
        if intent in self.confirm_intents and not actions:
            # **危险能力一旦真被执行，必须留下证据。**
            # 原来只有测试显式传了 `confirmed_responses` 才会产生副作用，而真实
            # L2 从不传——于是「确认前零副作用」在**最危险的那一类动作上恒为真**：
            # 替身什么都没做，和确认闸生效，在副作用面上长得一模一样。
            # 这里让替身自己造证据：真被调到就一定看得见。
            actions = [{"type": "vehicle.control",
                        "payload": {"intent": intent, "spy": "auto"}}]
        for action in actions:
            self.side_effects.append({"source": "engine", "intent": intent,
                                      "confirmed": confirmed,
                                      "action": dict(action)})
        if actions:
            return _FakeResponse(speech=spec.get("speech", "已完成"),
                                 data=spec.get("data"), actions=actions)
        payload = self.responses.get(intent)
        return _FakeResponse(status=self.default_status,
                             speech=f"{intent} 已完成",
                             data=payload if isinstance(payload, dict) else None)


def confirm_intent_inventory(agents: list, extra: set[str] | None = None) -> set[str]:
    """需要二次确认的 intent 全集 = manifest require_confirm ∪ 端侧危险动作 ∪ 显式补充。

    刻意不信 `eval_live` 合成的 edge capability——那里的 `require_confirm=False` 是
    占位默认值，拿它当准入清单会让「确认前零副作用」在最危险的一类动作上恒为真。
    """
    out = set(extra or ())
    for agent in agents:
        for cap in (getattr(agent.manifest, "capabilities", None) or []):
            if getattr(cap, "require_confirm", False):
                out.add(str(cap.intent))
    try:
        from val import VAL
        val = VAL()
        for intent in _edge_intents(agents):
            obj = intent.split(".", 1)[0]
            if val._need_confirm(obj):
                out.add(intent)
    except Exception:                  # VAL 不可用时只退化成 manifest 口径
        pass
    return out


def _edge_intents(agents: list) -> set[str]:
    return {str(cap.intent)
            for agent in agents
            if getattr(agent.manifest, "deployment", "") == "edge"
            for cap in (getattr(agent.manifest, "capabilities", None) or [])}


class ScriptedPlanner:
    """状态机单测用的确定性 planner。准确率证据必须用真实 PlanBuilder，不是它。"""

    def __init__(self, plan, replans=()):
        self.plan = plan
        self.replans = list(replans)
        self.calls: list[str] = []

    async def build(self, *_args, **_kwargs):
        from copy import deepcopy
        self.calls.append("build")
        return deepcopy(self.plan)

    async def replan(self, *_args, **_kwargs):
        from copy import deepcopy
        from orchestrator.cloud.models import ReplanDecision
        self.calls.append("replan")
        return deepcopy(self.replans.pop(0)) if self.replans else ReplanDecision(done=True)


async def _deterministic_aggregate(messages, **_kwargs):
    """聚合器不产生落域证据，固定返回可预期文本，避免再引入一次模型波动。"""
    return "已处理"


@dataclass(frozen=True)
class EngineObservation:
    decision: str
    speech: str
    need_confirm: bool
    events: tuple[dict[str, Any], ...]
    agent_calls: tuple[dict[str, Any], ...]
    side_effects: tuple[dict[str, Any], ...]
    planner_calls: tuple[str, ...]
    plans: tuple[Any, ...]
    pending_confirm_after: bool | None = None


class EngineHarness:
    """一案例一 harness：跑完即丢，不复用——session/focus 穿案例是最难查的假绿。"""

    def __init__(self, engine, clients, session, *, planner=None, sink=None):
        self.engine = engine
        self.clients = clients
        self.session = session
        self.planner = planner
        self.sink = sink

    def seed_pending_confirm(self, session_id: str, *, intent: str,
                             step_id: str = "s1") -> None:
        from orchestrator.cloud.models import SessionState
        pending = {"steps": [{"id": step_id, "agent_id": intent.split(".", 1)[0],
                              "endpoint": "fake:1", "intent": intent, "slots": {},
                              "depends_on": [], "slot_refs": {},
                              "require_confirm": True, "status": "need_confirm"}],
                   "complexity": "simple", "goal": ""}
        asyncio.run(self.session.save(session_id, SessionState(
            phase="wait_confirm", pending_plan=pending, pending_step_id=step_id)))

    def seed_focus(self, session_id: str, focus: dict[str, Any]) -> None:
        asyncio.run(self.session.save_focus(session_id, dict(focus)))

    def run(self, text: str, **kwargs) -> EngineObservation:
        return asyncio.run(self.run_async(text, **kwargs))

    async def run_async(self, text: str, *, session_id: str = "adv-1",
                        is_confirmation: bool = False,
                        meta: dict[str, str] | None = None,
                        granted_permissions: list[str] | None = None
                        ) -> EngineObservation:
        """完整入口把 Engine 跑在 Edge 已有的事件循环里，因此必须有 async 形态——
        在运行中的 loop 里再 `asyncio.run()` 会直接抛，而 Edge 把它吞成「云端不可达」
        然后静默降级：那正好是一条看起来像产品缺陷的假红。"""
        from types import SimpleNamespace
        request_meta = {"memory_enabled": "false", **(meta or {})}
        if granted_permissions is not None:
            request_meta["granted_scopes"] = ",".join(granted_permissions)
        request = SimpleNamespace(
            text=text, session_id=session_id, request_id="adv-r1",
            is_confirmation=is_confirmation, meta=request_meta,
            context=SimpleNamespace(user_id="eval-user", vehicle_id="eval-vehicle"))
        events = [event async for event in self.engine.run(request)]
        finals = [e for e in events if e.get("kind") == "final"]
        final = finals[-1] if finals else {}
        card_type = str(((final.get("ui_card") or {}) or {}).get("type") or "")
        need_confirm = bool(final.get("need_confirm"))
        speech = str(final.get("speech") or "")
        planned = bool(self.planner.calls if self.planner else self.sink and self.sink.plans)
        if card_type == "rejected":
            decision = "reject"
        elif card_type == "intent_choice":
            decision = "clarify"
        elif "取消" in speech and not planned and not self.clients.agent_calls:
            # engine 的取消分支在**规划之前**就 return 了：没有 planner 调用、没有
            # agent 调用、话术是取消。刻意不看 is_confirmation——裸「取消」不带 HMI
            # 标记也会走同一分支（`_confirm_reply` 的否定词优先），要求带标记等于
            # 把语音取消这条路判成 execute。
            decision = "cancel"
        elif need_confirm:
            decision = "confirm"
        else:
            decision = "execute"
        plans = tuple(trace.plan for trace in (self.sink.plans if self.sink else ()))
        # 本轮结束时挂起确认还在不在，是「确认闸没被绕过」的另一半证据：
        # 决策是 confirm 但挂起没落库，下一轮的「确认」就会落到空处。
        state = await self.session.load(session_id)
        pending_after = bool(state and state.phase == "wait_confirm"
                             and state.pending_plan)
        self.clients.record_turn("user", text)
        if speech:
            self.clients.record_turn("assistant", speech)
        return EngineObservation(
            decision=decision, speech=str(final.get("speech") or ""),
            need_confirm=need_confirm, events=tuple(events),
            agent_calls=tuple(self.clients.agent_calls),
            side_effects=tuple(self.clients.side_effects),
            planner_calls=tuple(self.planner.calls if self.planner else ()),
            plans=plans, pending_confirm_after=pending_after)


def _new_engine(planner, clients):
    from orchestrator.cloud.aggregator import Aggregator
    from orchestrator.cloud.engine import PlannerEngine
    from orchestrator.cloud.executor import DagExecutor
    from orchestrator.cloud.session import SessionStore
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=clients, planner=planner,
        executor=DagExecutor(call_agent_fn=clients.call_agent),
        aggregator=Aggregator(llm_fn=_deterministic_aggregate),
        session=session)
    return engine, session


def build_scripted_engine_harness(plan, *, replans=(), responses=None,
                                  agent_status: str = "OK",
                                  confirm_intents=(), confirmed_responses=None,
                                  agents=()):
    planner = ScriptedPlanner(plan, replans)
    clients = SafeClients(agents, responses=responses,
                          confirm_intents=set(confirm_intents),
                          confirmed_responses=confirmed_responses,
                          default_status=1 if agent_status == "NEED_CONFIRM" else 0)
    engine, session = _new_engine(planner, clients)
    return EngineHarness(engine, clients, session, planner=planner)


def build_engine_harness(builder, agents, safe_clients, trace_sink):
    """真实 L2：必须传 Task 8 的 live PlanBuilder，scripted plan 不构成准确率证据。"""
    from support.intent_adversarial_trace import RecordingPlanner
    planner = RecordingPlanner(builder, trace_sink)
    engine, session = _new_engine(planner, safe_clients)
    return EngineHarness(engine, safe_clients, session, sink=trace_sink)


class FullEntrySession:
    """完整入口的一条 case 会话：真实 Edge servicer 的云端替身直连内进程 Engine。

    servicer 与 harness 都**跨轮存活**：Edge 的 VAL 状态、Engine 的 pending plan 与
    history 只有活过一轮才谈得上「第二轮不许再执行一次」。

    只有 cloud-direct 通过而完整入口失败时，首偏离点才是 Edge；反过来推不出
    「Edge 有问题」——那只说明这句话本来就不该上云。
    """

    def __init__(self, engine_harness: EngineHarness, *, session_id: str = "adv-1"):
        from server import EdgeOrchestratorServicer
        self.harness = engine_harness
        self.session_id = session_id
        self.srv = EdgeOrchestratorServicer()
        self._turn = 0
        self._engine_out: dict[str, EngineObservation] = {}
        self._install_probes()

    def _install_probes(self) -> None:
        from cockpit.orchestrator.v1 import orchestrator_pb2
        from google.protobuf import struct_pb2
        srv = self.srv

        async def cloud_handle(req):
            observation = await self.harness.run_async(
                req.text, session_id=self.session_id,
                is_confirmation=req.is_confirmation, meta=dict(req.meta or {}))
            self._engine_out["value"] = observation
            for event in observation.events:
                if event.get("kind") != "final":
                    continue
                final = orchestrator_pb2.FinalResult(
                    speech=str(event.get("speech") or ""),
                    need_confirm=bool(event.get("need_confirm")))
                for action in event.get("actions") or []:
                    payload = struct_pb2.Struct()
                    payload.update({k: str(v) for k, v in
                                    (action.get("payload") or {}).items()})
                    final.actions.append(_common_action(
                        str(action.get("type") or ""), payload,
                        bool(action.get("require_confirm"))))
                yield orchestrator_pb2.HandleEvent(final=final)

        srv.cloud.handle = cloud_handle
        srv.obs.emit_turn = _async_noop
        srv.obs.emit_span = _async_noop
        srv.obs.emit_state = _async_noop
        srv.memory.append = _async_noop
        srv._nlu_shadow_bg = lambda *_args, **_kwargs: None

    def turn(self, text: str, *, meta: dict[str, str] | None = None,
             is_confirmation: bool = False
             ) -> tuple[EdgeObservation, EngineObservation | None]:
        from cockpit.orchestrator.v1 import orchestrator_pb2
        self._turn += 1
        self._engine_out.pop("value", None)
        before = dict(self.srv.val.state)
        request = orchestrator_pb2.HandleRequest(
            text=text, session_id=self.session_id,
            request_id=f"adv-r{self._turn}", is_confirmation=is_confirmation,
            meta={"memory_enabled": "false", **(meta or {})})
        events = asyncio.run(_collect(self.srv.Handle(request, None)))
        reached_cloud = "value" in self._engine_out
        edge = _edge_observation(events, before, self.srv.val.state,
                                 cloud_text=text if reached_cloud else "",
                                 reached_cloud=reached_cloud)
        return edge, self._engine_out.get("value")


def run_full_entry_turn(text: str, engine_harness: EngineHarness, *,
                        meta: dict[str, str] | None = None,
                        session_id: str = "adv-1",
                        is_confirmation: bool = False
                        ) -> tuple[EdgeObservation, EngineObservation]:
    session = FullEntrySession(engine_harness, session_id=session_id)
    return session.turn(text, meta=meta, is_confirmation=is_confirmation)


def _common_action(type_: str, payload, require_confirm: bool):
    from cockpit.common.v1 import common_pb2
    return common_pb2.AgentAction(type=type_, payload=payload,
                                  require_confirm=require_confirm)
