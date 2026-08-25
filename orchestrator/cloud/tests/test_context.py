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
    ContextManager, WorkingSet, Focus, extract_focus,
    normalize_weather_city_slot)
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
    f = extract_focus(plan, [_ok("s1", {
        "items": [{"name": "海岸城"}],
        "lat": 22.533,
        "lng": 113.942,
    })])
    assert f.last_destination == "深圳湾"
    assert f.last_poi == "海岸城"
    assert f.destination_lat == 22.533
    assert f.destination_lng == 113.942


def test_extract_focus_waypoint_choice_keeps_resolved_destination_and_candidates():
    """顺路候选卡的 data 是协议事实：下一轮裸序号要知道选什么、仍去哪里。

    原始 step.destination 可能是「南山科技园」，地图已解析结果却是具体的腾讯滨海大厦；
    下一轮必须继承后者，不能拿原始模糊词重新搜索后漂到另一个城市。
    """
    plan = Plan(steps=[Step(
        id="s1",
        agent_id="navigation",
        intent="navigation.navigate_to",
        slots={"destination": "南山科技园", "stop_category": "咖啡"},
    )])
    f = extract_focus(plan, [_ok("s1", {
        "destination": "腾讯滨海大厦",
        "stops": [
            {"name": "戴言咖啡"},
            {"name": "Something For"},
            {"name": "迈理咖啡"},
        ],
    })])

    assert f.last_destination == "腾讯滨海大厦"
    assert f.last_poi == "戴言咖啡"
    assert f.last_choice_purpose == "waypoint"
    assert f.last_choices == ["戴言咖啡", "Something For", "迈理咖啡"]
    rendered = WorkingSet(focus=f).render_context()
    assert "最新候选用途=顺路途经点选择" in rendered
    assert "2:Something For" in rendered


def test_extract_focus_info_turn_keeps_last_intent():
    """纯信息轮（无对象/POI/目的地）也落焦点：last_intent 供「明天呢」省略式追问延续判域
    （badcase demo-i9c92i：赛程问句后「明天呢」被错绑到天气）。"""
    plan = Plan(steps=[Step(id="s1", agent_id="info",
                            intent="info.sports", slots={})])
    f = extract_focus(plan, [_ok("s1")])
    assert f is not None and f.last_intent == "info.sports"
    assert "上一轮意图=info.sports" in WorkingSet(focus=f).render_context()


def test_weather_focus_keeps_the_last_explicit_city():
    plan = Plan(steps=[Step(id="s1", agent_id="info",
                            intent="info.weather", slots={"city": "深圳"})])
    focus = extract_focus(plan, [_ok("s1")])

    assert focus is not None and focus.last_city == "深圳"
    assert "上个城市=深圳" in WorkingSet(focus=focus).render_context()


def test_object_city_on_first_turn_is_available_to_the_next_weather_turn():
    """首轮没有旧 focus 时，对象化 city 也必须落账，供“明天呢”续接。"""
    first = Plan(steps=[Step(
        id="s1", agent_id="info", intent="info.weather",
        slots={"city": '{"city":"北京","road":""}'},
    )])
    focus = extract_focus(first, [_ok("s1")])

    assert focus is not None and focus.last_city == "北京"

    followup = Plan(
        raw_text="明天呢",
        steps=[Step(id="s2", agent_id="info", intent="info.forecast",
                    slots={"city": ""}, context_scopes=["location"])],
    )
    from orchestrator.cloud.engine import PlannerEngine
    PlannerEngine._apply_focus_meta(followup, focus)
    assert followup.steps[0].slots["city"] == "北京"


def test_focus_city_normalizer_rejects_non_string_object_values():
    assert normalize_weather_city_slot({"city": 123}) == ""
    assert normalize_weather_city_slot('{"city":123}') == ""
    assert normalize_weather_city_slot("{oops}") == ""


def test_stock_focus_keeps_the_last_successful_symbol():
    plan = Plan(steps=[Step(id="s1", agent_id="info",
                            intent="info.stock", slots={"symbol": "宁德时代"})])
    focus = extract_focus(plan, [_ok("s1")])

    assert focus is not None and focus.last_stock_symbol == "宁德时代"
    assert "上个股票标的=宁德时代" in WorkingSet(focus=focus).render_context()


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
    asyncio.run(cm.update_focus(
        "sess-f", plan, [_ok("s1")], user_id="u1"))
    ctx = SimpleNamespace(session_id="sess-f", user_id="u1")
    ws = asyncio.run(cm.assemble("再调高一点", ctx))
    assert ws.focus is not None and ws.focus.obj == "空调"
    assert "对象=空调" in ws.render_context()


def test_focus_not_loaded_when_mem_off():
    session = SessionStore(redis_url="")
    cm = ContextManager(_Clients([_agent("hvac", ["hvac.set"])]), session)
    plan = Plan(steps=[Step(id="s1", agent_id="hvac", intent="hvac.set",
                            slots={"position": "副驾"})])
    asyncio.run(cm.update_focus(
        "sess-f", plan, [_ok("s1")], user_id="u1"))
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
    assert "cloud-39" not in cat          # 非核心从尾部被裁剪以让位（不再绑死具体预算值）


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
    """edge 车控核心紧凑渲染（仅意图名，不带 slots/desc）。

    ⚠ M5 P3 收尾试过把 `capabilities.py` 新生成的判别化描述也渲进来，**双臂差分否掉了**：
    25 条语料 ×2 轮 ×2 provider（minimax/deepseek）Δ=0、零翻面，代价是每次规划 +1462
    字符。intent 名本身就是判别性文本，planner 这一侧不缺信息。故本断言保留原样——
    它现在守的是「不要在没有证据的情况下往每次规划里加 1.4k 字符」。
    """
    item = WorkingSet.render_catalog(
        [_agent_dep("edge-vehicle", 3, deployment="edge", kind="edge_fast")])
    assert "edge-vehicle.act0" in item   # 意图名在
    assert "slots" not in item and "desc" not in item  # 不带 slots/desc


# ── 裁剪可观测（数据飞轮 P0 D1）──

def test_render_catalog_stats_no_trim():
    stats: dict = {}
    WorkingSet.render_catalog([_agent_dep("a", 2), _agent_dep("b", 2)], stats)
    assert stats["dropped"] == []
    assert stats["chars_full"] == stats["chars_final"] > 0


def test_render_catalog_stats_records_dropped_from_tail(monkeypatch):
    """裁剪不再静默：stats 回填被裁 agent 名单（尾部优先），cloud.planning span 可查。"""
    import orchestrator.cloud.context as ctxmod
    monkeypatch.setattr(ctxmod, "_CATALOG_BUDGET", 120)
    stats: dict = {}
    agents = [_agent_dep(f"ag-{i}", 2) for i in range(6)]
    WorkingSet.render_catalog(agents, stats)
    assert stats["chars_full"] > 120
    assert len(stats["dropped"]) == 5            # 裁到只剩 1 个（至少留 1）
    assert stats["dropped"][0] == "ag-5"         # 从尾部开始裁
    assert stats["chars_final"] < stats["chars_full"]


# ── 历史归属（M-B）──────────────────────────────────────────
def test_history_is_fetched_with_the_owner_key():
    """planner 历史按 OwnerKey 取。

    车里只有一个会话而说话人会换：不按 owner 过滤时，上一位的称呼比 system 提示更近，
    会把当前这位的答案盖掉（P4 真机第四批实测：先聊过阿灵再问「我是谁」答成阿灵）。
    """
    seen = {}

    class _OwnerAware(_Clients):
        async def get_session(self, session_id, last_n, *, user_id="", occupant_id=""):
            seen["owner"] = (user_id, occupant_id)
            return []

    cm = ContextManager(_OwnerAware([_agent("a", ["a.x"])]))
    ctx = SimpleNamespace(session_id="sess", user_id="u1", occupant_id="occ-2")
    asyncio.run(cm.assemble("hi", ctx))
    assert seen["owner"] == ("u1", "occ-2")


def test_history_falls_back_to_primary_when_occupant_unknown():
    seen = {}

    class _OwnerAware(_Clients):
        async def get_session(self, session_id, last_n, *, user_id="", occupant_id=""):
            seen["owner"] = (user_id, occupant_id)
            return []

    cm = ContextManager(_OwnerAware([_agent("a", ["a.x"])]))
    asyncio.run(cm.assemble("hi", _ctx()))
    assert seen["owner"] == ("u1", "primary")


def test_legacy_two_arg_get_session_still_works():
    """签名探测只是**建议**：旧客户端/测试替身仍是两参，不能给它们塞 kwargs。"""
    cm = ContextManager(_Clients([_agent("a", ["a.x"])], history=[{"role": "user", "text": "hi"}]))
    ws = asyncio.run(cm.assemble("hi", _ctx()))
    assert ws.history == [{"role": "user", "text": "hi"}]


def test_focus_keeps_last_turn_public_pois_for_cross_turn_store_anchoring():
    """只认 nearby.search，只留 name/lng/lat 三标量。

    这一格让「先查附近的瑞幸」「在最近那家点一杯」两轮走得通——门店可信链原本
    只在同一轮 plan 内成立。**刻意不存 deptId 之类商户内部 id**：那是每次现查的事实，
    缓存它等于把商户的内部状态当成我们的事实。
    """
    plan = Plan(steps=[Step(id="s1", agent_id="nearby", intent="nearby.search")])
    results = [StepResult(
        step_id="s1", status=StepStatus.OK, source_intent="nearby.search",
        data={"items": [
            {"name": "瑞幸咖啡(前海印里店)", "lng": 113.8981, "lat": 22.5301,
             "deptId": 602825, "rating": 4.4},
            {"name": "坏数据", "lng": "abc", "lat": 22.5},
            {"name": "", "lng": 113.9, "lat": 22.5},
        ]})]

    focus = extract_focus(plan, results)

    assert focus.last_places == [
        {"name": "瑞幸咖啡(前海印里店)", "lng": 113.8981, "lat": 22.5301}]
    assert "deptId" not in focus.last_places[0]
    assert "rating" not in focus.last_places[0]


def test_last_places_keep_city_but_still_reject_merchant_ids():
    """city 是第四个下游实际引用的安全标量（麦当劳官方检索 searchType=2 城市必填，
    直点句唯一的城市来源是焦点）；deptId/卡片/话术照旧不进焦点。"""
    from orchestrator.cloud.models import StepResult, StepStatus

    result = StepResult(
        step_id="s1", status=StepStatus.OK, source_intent="nearby.search",
        data={"items": [{"name": "麦当劳(高新中五道餐厅)", "lng": 113.94,
                         "lat": 22.54, "city": "深圳市", "deptId": 99,
                         "rating": 4.6}]})
    focus = extract_focus(Plan(steps=[]), [result])

    assert focus.last_places == [{
        "name": "麦当劳(高新中五道餐厅)", "lng": 113.94, "lat": 22.54,
        "city": "深圳市",
    }]


def test_only_nearby_search_feeds_the_store_anchor():
    """别的域返回的同名字段不许冒充门店——那正是可信链要挡的事。"""
    plan = Plan(steps=[Step(id="s1", agent_id="navigation",
                            intent="navigation.navigate_to")])
    results = [StepResult(
        step_id="s1", status=StepStatus.OK,
        source_intent="navigation.navigate_to",
        data={"items": [{"name": "某个导航结果", "lng": 113.9, "lat": 22.5}]})]

    focus = extract_focus(plan, results)

    assert (focus is None) or not focus.last_places


def test_places_only_focus_is_still_worth_persisting():
    """只有门店列表的焦点也必须落盘——它就是跨轮锚定的全部载体。"""
    assert not Focus(last_places=[{"name": "x", "lng": 1.0, "lat": 2.0}]).is_empty()


def test_last_places_survive_a_turn_that_did_not_search():
    """门店列表是粘性的：只有新的 nearby.search 才替换它。

    focus 每轮从当前 plan 重建，不接力就会被紧随的任何一轮抹空——2026-08-13
    真栈三轮实证：查门店 → 「第一个」（落 luckin.menu，无搜索步）→ 「这家的菜单」
    又回到「请先查询附近的瑞幸门店」。**两轮测试测不出来**，第二轮恰好紧邻搜索轮。
    """
    store = SessionStore()
    manager = ContextManager(clients=SimpleNamespace(), session=store)
    search_plan = Plan(steps=[Step(id="s1", agent_id="nearby",
                                   intent="nearby.search")])
    search_results = [StepResult(
        step_id="s1", status=StepStatus.OK, source_intent="nearby.search",
        data={"items": [{"name": "瑞幸咖啡(前海印里店)",
                         "lng": 113.8981, "lat": 22.5301}]})]
    menu_plan = Plan(steps=[Step(id="s1", agent_id="mcp-bridge",
                                 intent="luckin.menu")])
    menu_results = [StepResult(step_id="s1", status=StepStatus.OK,
                               source_intent="luckin.menu", data={})]

    async def _run():
        await manager.update_focus("sess", search_plan, search_results,
                                   user_id="u1")
        await manager.update_focus("sess", menu_plan, menu_results,
                                   user_id="u1")
        return await manager._load_focus("sess", "u1")

    focus = asyncio.run(_run())

    assert focus is not None
    assert [p["name"] for p in focus.last_places] == ["瑞幸咖啡(前海印里店)"]
    assert focus.last_intent == "luckin.menu", "其余焦点字段仍按本轮刷新"


def test_current_location_weather_replaces_instead_of_reviving_an_old_city():
    """新的无城市天气轮表示回到 GPS；不得把更早的显式城市重新粘回来。"""
    store = SessionStore()
    manager = ContextManager(clients=SimpleNamespace(), session=store)
    explicit = Plan(steps=[Step(
        id="s1", agent_id="info", intent="info.weather", slots={"city": "深圳"})])
    current_location = Plan(steps=[Step(
        id="s2", agent_id="info", intent="info.weather", slots={"city": ""})])

    async def _run():
        await manager.update_focus(
            "sess", explicit, [_ok("s1")], user_id="u1")
        await manager.update_focus(
            "sess", current_location, [_ok("s2")], user_id="u1")
        return await manager._load_focus("sess", "u1")

    focus = asyncio.run(_run())

    assert focus is not None
    assert focus.last_intent == "info.weather"
    assert focus.last_city == ""


def test_unrelated_turn_drops_the_previous_stock_symbol_from_focus():
    store = SessionStore()
    manager = ContextManager(clients=SimpleNamespace(), session=store)
    stock = Plan(steps=[Step(
        id="s1", agent_id="info", intent="info.stock",
        slots={"symbol": "宁德时代"})])
    unrelated = Plan(steps=[Step(
        id="s2", agent_id="chitchat", intent="chitchat.talk", slots={})])

    async def _run():
        await manager.update_focus("sess", stock, [_ok("s1")], user_id="u1")
        await manager.update_focus(
            "sess", unrelated, [_ok("s2")], user_id="u1")
        return await manager._load_focus("sess", "u1")

    focus = asyncio.run(_run())

    assert focus is not None
    assert focus.last_intent == "chitchat.talk"
    assert focus.last_stock_symbol == ""


def test_places_come_from_result_provenance_not_the_plan_object():
    """salvage/replan 轮里，调用方给的 plan 是**重规划后的**，搜索步不在里面。

    2026-08-13 真栈实证：同一句话走 toolcall 时三轮通、走 toolcall_salvage 时
    第三轮回到「请先查询附近的瑞幸门店」——因为门店列表从第一轮起就没存下。
    `source_intent` 是执行器用权威 Step 盖的章，比调用方递来的 plan 可靠。
    """
    replanned = Plan(steps=[Step(id="s2", agent_id="mcp-bridge",
                                 intent="luckin.menu")])
    results = [
        StepResult(step_id="s1", status=StepStatus.OK,
                   source_intent="nearby.search",
                   data={"items": [{"name": "瑞幸咖啡(前海印里店)",
                                    "lng": 113.8981, "lat": 22.5301}]}),
        StepResult(step_id="s2", status=StepStatus.OK,
                   source_intent="luckin.menu", data={}),
    ]

    focus = extract_focus(replanned, results)

    assert focus is not None
    assert [p["name"] for p in focus.last_places] == ["瑞幸咖啡(前海印里店)"]


# ── EVA 二轮 G6：历史指代 → episodic 召回放开 ─────────────────────────────

def test_recall_expands_kinds_on_history_reference():
    """「上次/上回」历史指代 → 召回放开 episodic；普通话术仍只召 semantic
    （episodic 噪声大，无差别注入会挤掉偏好预算）。"""
    seen = {}

    class _C(_Clients):
        async def recall(self, user_id, query="", **kw):
            seen[query] = kw.get("kinds")
            return []

    agents = [_agent("a", ["a.x"])]
    cm = ContextManager(_C(agents), top_k=12)
    asyncio.run(cm.assemble("带我去上次看夜景的那个地方", _ctx()))
    asyncio.run(cm.assemble("附近找个川菜馆", _ctx()))
    assert seen["带我去上次看夜景的那个地方"] == ["semantic", "episodic"]
    assert seen["附近找个川菜馆"] == ["semantic"]
