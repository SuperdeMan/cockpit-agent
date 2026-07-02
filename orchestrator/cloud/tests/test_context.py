"""ContextManager / WorkingSet 装配层单测（Phase 1）。

覆盖：catalog 语义预筛（≤K no-op / >K 取 top-K∪always-include / resolve 失败回退全量）、
历史召回缺能力时优雅降级、render_context 与旧 _format_* 逐字一致、预算按优先级裁剪、
render_catalog JSON 结构。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud.context import (
    ContextManager, WorkingSet, Focus, extract_focus)
from orchestrator.cloud.models import Plan, Step, StepResult, StepStatus
from orchestrator.cloud.session import SessionStore


def _agent(agent_id, intents):
    caps = [SimpleNamespace(intent=i, slots=[], description=i) for i in intents]
    manifest = SimpleNamespace(agent_id=agent_id, capabilities=caps,
                               kind="agent", deployment="cloud")
    return SimpleNamespace(manifest=manifest, endpoint=f"{agent_id}:50000")


def _ctx(user_id="u1"):
    return SimpleNamespace(session_id="sess", user_id=user_id)


class _Clients:
    """全功能 stub：list_agents/resolve/get_session/recall 都有。"""
    def __init__(self, agents, resolve_result=None, history=None, memories=None):
        self._agents = agents
        self._resolve_result = resolve_result if resolve_result is not None else agents
        self._history = history or []
        self._memories = memories or []

    async def list_agents(self):
        return list(self._agents)

    async def resolve(self, query="", top_k=1):
        return list(self._resolve_result)

    async def get_session(self, session_id, last_n):
        return list(self._history)

    async def recall(self, user_id, query="", **kw):
        return list(self._memories)


def _ids(ws):
    return {a.manifest.agent_id for a in ws.catalog}


# ── catalog 预筛 ──

def test_catalog_noop_when_within_top_k():
    """agent 数 ≤ top_k → 返回全量，不依赖 resolve（即便 resolve 只给子集）。"""
    agents = [_agent("a", ["a.x"]), _agent("b", ["b.y"])]
    cm = ContextManager(_Clients(agents, resolve_result=[agents[0]]), top_k=12)
    ws = asyncio.run(cm.assemble("hi", _ctx()))
    assert _ids(ws) == {"a", "b"}


def test_catalog_prefilters_when_exceeding_top_k():
    """agent 数 > top_k → 用 resolve 的语义子集。"""
    agents = [_agent(f"ag{i}", [f"ag{i}.x"]) for i in range(5)]
    cm = ContextManager(_Clients(agents, resolve_result=[agents[0], agents[1]]), top_k=2)
    ws = asyncio.run(cm.assemble("hi", _ctx()))
    assert _ids(ws) == {"ag0", "ag1"}


def test_catalog_multi_intent_agents_preserved():
    """多意图：resolve 命中多个相关 agent 时都保留（不漏召回）。"""
    agents = ([_agent("hvac", ["hvac.set"]), _agent("media", ["media.play"]),
               _agent("info", ["info.weather"])]
              + [_agent(f"noise{i}", [f"noise{i}.x"]) for i in range(5)])
    relevant = [agents[0], agents[1], agents[2]]
    cm = ContextManager(_Clients(agents, resolve_result=relevant), top_k=3)
    ws = asyncio.run(cm.assemble("打开空调并播放音乐顺便看天气", _ctx()))
    assert {"hvac", "media", "info"}.issubset(_ids(ws))


def test_catalog_always_includes_fallback_and_route_hint_agents():
    """兜底 Agent（chitchat）与声明了 route_hints 的 Agent（如 trip-planner）总在 catalog，
    即便 resolve 没选中——route_hint 的确定性路由依赖该 manifest 在 catalog 可见（R2.1 P5，
    通用保护取代硬编码 _ALWAYS_INCLUDE）。"""
    agents = ([_agent(f"ag{i}", [f"ag{i}.x"]) for i in range(5)]
              + [_agent("chitchat", ["chitchat.talk"]),
                 _agent("trip-planner", ["trip.plan"])])
    # trip-planner 靠「声明 route_hints」被通用保护（不再靠硬编码 agent_id）
    agents[-1].manifest.route_hints = [SimpleNamespace(
        pattern="去.+天", intent="trip.plan", policy="append", priority=50, guard="", slots={})]
    cm = ContextManager(_Clients(agents, resolve_result=[agents[0]]), top_k=3)
    ws = asyncio.run(cm.assemble("hi", _ctx()))
    assert _ids(ws) == {"ag0", "chitchat", "trip-planner"}


def test_catalog_always_keeps_edge_control_agents():
    """edge/edge_fast 车控 agent 即使不在 resolve top-K 也必须保留——安全核心不被预筛丢掉
    （dangerous_trunk_confirm 回归根因：车控被丢→危险动作确认退化成 chitchat）。"""
    def _edge(aid):
        caps = [SimpleNamespace(intent=f"{aid}.do", slots=[], description=aid)]
        m = SimpleNamespace(agent_id=aid, capabilities=caps,
                            kind="edge_fast", deployment="edge")
        return SimpleNamespace(manifest=m, endpoint=f"{aid}:1")

    agents = ([_agent(f"ag{i}", [f"ag{i}.x"]) for i in range(5)]
              + [_edge("edge-vehicle"), _edge("edge-media")])
    # resolve 故意只给一个 cloud agent，不含 edge-vehicle/edge-media
    cm = ContextManager(_Clients(agents, resolve_result=[agents[0]]), top_k=3)
    ws = asyncio.run(cm.assemble("打开后备箱", _ctx()))
    ids = _ids(ws)
    assert "edge-vehicle" in ids and "edge-media" in ids


def test_catalog_falls_back_to_full_when_resolve_empty():
    """resolve 返回空（不可用/无命中）→ 回退全量，绝不把 catalog 砍空。"""
    agents = [_agent(f"ag{i}", [f"ag{i}.x"]) for i in range(5)]
    cm = ContextManager(_Clients(agents, resolve_result=[]), top_k=2)
    ws = asyncio.run(cm.assemble("hi", _ctx()))
    assert len(ws.catalog) == 5


def test_assemble_graceful_without_memory_methods():
    """clients 缺 get_session/recall → 历史/记忆为空、不崩（不阻塞规划）。"""
    agents = [_agent("a", ["a.x"])]

    class _Bare:
        async def list_agents(self):
            return list(agents)

        async def resolve(self, query="", top_k=1):
            return list(agents)

    cm = ContextManager(_Bare())
    ws = asyncio.run(cm.assemble("hi", _ctx()))
    assert ws.history == [] and ws.memories == []
    assert _ids(ws) == {"a"}


def test_assemble_skips_memory_when_mem_off():
    """mem_on=False → 不读历史/记忆（catalog 仍装配）。"""
    agents = [_agent("a", ["a.x"])]
    cm = ContextManager(_Clients(
        agents, history=[{"role": "user", "text": "x"}],
        memories=[{"text": "y"}]))
    ws = asyncio.run(cm.assemble("hi", _ctx(), mem_on=False))
    assert ws.history == [] and ws.memories == []


# ── 渲染：与旧 _format_* 逐字一致 + 预算 ──

def test_render_context_preserves_legacy_format():
    ws = WorkingSet(
        history=[{"role": "user", "text": "把副驾空调调到26度"},
                 {"role": "assistant", "text": "好的"}],
        memories=[{"text": "用户不吃辣", "scope": "taste",
                   "provenance": "user_stated", "confidence": 0.9}])
    out = ws.render_context()
    assert out.startswith("已知用户记忆")
    assert "[taste | 0.90 | user_stated] 用户不吃辣" in out
    assert "最近对话（用于指代消解）：" in out
    assert "用户：把副驾空调调到26度" in out
    assert "助手：好的" in out
    assert out.endswith("\n\n")


def test_render_context_empty_when_no_history_or_memory():
    assert WorkingSet(history=[], memories=[]).render_context() == ""


def test_render_context_budget_trims_oldest_history(monkeypatch):
    import orchestrator.cloud.context as ctxmod
    monkeypatch.setattr(ctxmod, "_CTX_BUDGET", 40)
    ws = WorkingSet(history=[
        {"role": "user", "text": "最旧一句啊啊啊啊啊啊"},
        {"role": "user", "text": "中间一句啊啊啊啊啊啊"},
        {"role": "assistant", "text": "最新一句啊啊啊啊啊啊"},
    ], memories=[])
    out = ws.render_context()
    assert "最新一句" in out      # 最新一轮保留
    assert "最旧一句" not in out  # 紧预算下最旧一轮被裁


def test_render_catalog_structure():
    agents = [_agent("nav", ["navigation.search_poi", "navigation.navigate"])]
    data = json.loads(WorkingSet.render_catalog(agents))
    assert data[0]["agent_id"] == "nav"
    assert data[0]["deployment"] == "cloud"
    intents = {c["intent"] for c in data[0]["capabilities"]}
    assert intents == {"navigation.search_poi", "navigation.navigate"}


def test_render_catalog_trims_tail_over_budget(monkeypatch):
    import orchestrator.cloud.context as ctxmod
    monkeypatch.setattr(ctxmod, "_CATALOG_BUDGET", 120)
    agents = [_agent(f"agent-with-longish-id-{i}", [f"agent{i}.intent"])
              for i in range(10)]
    data = json.loads(WorkingSet.render_catalog(agents))
    assert 0 < len(data) < 10  # 超预算 → 丢尾部，但至少留 1 个


# ── 焦点态（Phase 2）──

def _ok(step_id, data=None):
    return StepResult(step_id=step_id, status=StepStatus.OK, data=data or {})


def test_extract_focus_control_hvac():
    plan = Plan(steps=[Step(id="s1", agent_id="hvac", intent="hvac.set",
                            slots={"temperature": "26", "position": "副驾"})])
    f = extract_focus(plan, [_ok("s1")])
    assert f.obj == "空调" and f.attr == "温度"
    assert f.positions == ["副驾"]
    assert f.last_intent == "hvac.set"


def test_extract_focus_navigation_poi():
    plan = Plan(steps=[Step(id="s1", agent_id="navigation",
                            intent="navigation.search_poi",
                            slots={"destination": "深圳湾"})])
    f = extract_focus(plan, [_ok("s1", {"items": [{"name": "海岸城"}]})])
    assert f.last_destination == "深圳湾"
    assert f.last_poi == "海岸城"


def test_extract_focus_empty_returns_none():
    plan = Plan(steps=[Step(id="s1", agent_id="chitchat",
                            intent="chitchat.talk", slots={})])
    assert extract_focus(plan, [_ok("s1")]) is None


def test_extract_focus_ignores_failed_steps():
    plan = Plan(steps=[Step(id="s1", agent_id="hvac", intent="hvac.set",
                            slots={"position": "副驾"})])
    results = [StepResult(step_id="s1", status=StepStatus.FAILED)]
    assert extract_focus(plan, results) is None


def test_render_context_includes_focus_block():
    ws = WorkingSet(focus=Focus(obj="空调", positions=["副驾"], attr="温度"))
    out = ws.render_context()
    assert out.startswith("当前对话焦点")
    assert "对象=空调" in out and "位置=副驾" in out and "属性=温度" in out


def test_render_context_focus_first_then_memory_then_history():
    ws = WorkingSet(
        focus=Focus(obj="空调"),
        memories=[{"text": "用户不吃辣", "scope": "taste",
                   "provenance": "user_stated", "confidence": 0.9}],
        history=[{"role": "user", "text": "刚才那句"}])
    out = ws.render_context()
    assert out.index("当前对话焦点") < out.index("已知用户记忆") < out.index("最近对话")


def test_focus_update_and_load_roundtrip():
    session = SessionStore(redis_url="")
    cm = ContextManager(_Clients([_agent("hvac", ["hvac.set"])]), session)
    plan = Plan(steps=[Step(id="s1", agent_id="hvac", intent="hvac.set",
                            slots={"position": "副驾", "temperature": "26"})])
    asyncio.run(cm.update_focus("sess-f", plan, [_ok("s1")]))
    ctx = SimpleNamespace(session_id="sess-f", user_id="u1")
    ws = asyncio.run(cm.assemble("再调高一点", ctx))
    assert ws.focus is not None and ws.focus.obj == "空调"
    assert "对象=空调" in ws.render_context()


def test_focus_not_loaded_when_mem_off():
    session = SessionStore(redis_url="")
    cm = ContextManager(_Clients([_agent("hvac", ["hvac.set"])]), session)
    plan = Plan(steps=[Step(id="s1", agent_id="hvac", intent="hvac.set",
                            slots={"position": "副驾"})])
    asyncio.run(cm.update_focus("sess-f", plan, [_ok("s1")]))
    ctx = SimpleNamespace(session_id="sess-f", user_id="u1")
    ws = asyncio.run(cm.assemble("再调高一点", ctx, mem_on=False))
    assert ws.focus is None


def _agent_dep(agent_id, n_caps, deployment="cloud", kind="agent"):
    caps = [SimpleNamespace(intent=f"{agent_id}.act{i}", slots=[], description="x" * 20)
            for i in range(n_caps)]
    manifest = SimpleNamespace(agent_id=agent_id, capabilities=caps,
                               kind=kind, deployment=deployment)
    return SimpleNamespace(manifest=manifest, endpoint=f"{agent_id}:50000")


def test_render_catalog_keeps_edge_core_over_budget():
    """超 catalog 预算时，edge 车控核心（caps 多、体积大、常在尾部）绝不被裁剪——否则 LLM
    看不到 trunk 等危险动作 → 规划退化成 chitchat 兜底（dangerous_trunk_confirm 根因）。"""
    agents = ([_agent_dep(f"cloud-{i}", 8) for i in range(40)]
              + [_agent_dep("edge-vehicle", 74, deployment="edge", kind="edge_fast")])
    cat = WorkingSet.render_catalog(agents)
    assert "edge-vehicle" in cat          # 安全核心保住
    assert "edge-vehicle.act0" in cat     # 其能力（trunk 类比）随之可见
    assert "cloud-0" not in cat or len(cat) <= 8000  # 非核心被从尾部裁剪以让位


def test_render_catalog_no_trim_under_budget():
    """未超预算时不丢任何 agent（行为同改造前）。"""
    cat = WorkingSet.render_catalog([_agent_dep("a", 2), _agent_dep("b", 2)])
    assert "\"a\"" in cat and "\"b\"" in cat


def test_render_catalog_keeps_always_include_over_budget():
    """always-include（chitchat 全局兜底）超预算时也不被裁——否则开放域请求因 catalog 无
    chitchat 而误路由到 info（cloud_chitchat_streaming 根因）。"""
    agents = ([_agent_dep(f"cloud-{i}", 8) for i in range(40)]
              + [_agent_dep("edge-vehicle", 74, deployment="edge", kind="edge_fast"),
                 _agent_dep("chitchat", 1)])
    cat = WorkingSet.render_catalog(agents)
    assert "edge-vehicle" in cat       # 安全核心保住
    assert "\"chitchat\"" in cat       # always-include 保住


def test_render_catalog_edge_core_compact():
    """edge 车控核心紧凑渲染（仅意图名，不带 slots/desc），避免体积撑爆预算偏置路由。"""
    item = WorkingSet.render_catalog(
        [_agent_dep("edge-vehicle", 3, deployment="edge", kind="edge_fast")])
    assert "edge-vehicle.act0" in item   # 意图名在
    assert "slots" not in item and "desc" not in item  # 不带 slots/desc
