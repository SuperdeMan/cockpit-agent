"""通用 escalate 机制契约测试（2026-07-12 mode-routing 设计 P1-2）。

协议：Agent 在 `AgentResult.data["_escalate"]={"intent","slots","reason"}` 声明改派，
engine 有界消费（每轮最多 1 跳）：目标步经 `PlanBuilder._validated_steps` 装配成单步
mini-plan 走 executor（heavy/latency_budget/权限自动带出）。固化的契约：
  a) D0 流式 final 带 _escalate（零播报）→ 改派执行，final=escalated 结果
  b) escalated 结果再带 _escalate → 不再跳（单跳预算，结构性防环）
  c) intent 无承接 Agent → 忽略 + 诚实兜底话术
  d) executor 多步计划：仅声明步的结果被替换，其余步保留进聚合
  e) escalated NEED_CONFIRM → F1 挂起语义一致
  f) 已流式播报（streamed=True）→ 忽略 escalate，不二次回答
  g) heavy escalated 步发过程区 execute 事件 + meta thinking=on
  h) 改派后的单步云端 Agent 走**与 D0 同一份流式直通**（2026-09-18）：edge 步 / 需确认步仍走 executor；
     流零输出（Agent 不支持流式）⇒ 回退 executor unary
  j) 声明带 `resume`：改派那一步成功后把声明方这一步续跑一次（清掉列出的槽，由执行器从本轮结果补），续跑仍改派 / 失败 ⇒ 丢掉
     续跑；续跑挂起落在声明方那一步（2026-10-17 demo-dqt1fu）
  k) 声明带 `fallback_speech`：改派装配不出或那一步失败 ⇒ 用回落话术；没消费的改派（同轮第二条、T2）也照回落话术说（F01 S3）
全部进程内 stub（装置风格同 test_engine_stream.py），不依赖 gRPC。
"""
from __future__ import annotations
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.models import Plan, PlanContext, Step, StepResult, StepStatus
from orchestrator.cloud.session import SessionStore

_SINGLE_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": "cap_0001", "slots": {}, "depends_on": [],
     "slot_refs": {}},
]})
_TWO_STEP_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": "cap_0001", "slots": {}, "depends_on": [],
     "slot_refs": {}},
    {"id": "s2", "capability_ref": "cap_0001", "slots": {},
     "depends_on": ["s1"], "slot_refs": {}},
]})

_ESC = {"_escalate": {"intent": "info.search",
                      "slots": {"query": "昨晚欧冠决赛结果"},
                      "reason": "needs_realtime"}}


class _Cap:
    def __init__(self, intent, heavy=False, require_confirm=False,
                 response_only=False):
        self.intent, self.slots, self.description = intent, [], intent
        self.heavy = heavy
        self.require_confirm = require_confirm
        self.response_only = response_only
        self.examples = []


def _agents():
    chitchat = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="chitchat", trust_level="first_party", latency_budget_ms=2000,
        deployment="cloud", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("chitchat.talk", response_only=True)], route_hints=[],
    ), endpoint="stub:50062")
    info = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="info", trust_level="first_party", latency_budget_ms=50000,
        deployment="cloud", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("info.search", heavy=True)], route_hints=[],
    ), endpoint="stub:50063")
    edge = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="edge-vehicle", trust_level="first_party", latency_budget_ms=2000,
        deployment="edge", kind="edge_fast", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("warning_light.close")], route_hints=[],
    ), endpoint="stub:50064")
    merchant = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="mcp-bridge", trust_level="first_party", latency_budget_ms=5000,
        deployment="cloud", kind="agent", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("luckin.order", require_confirm=True)], route_hints=[],
    ), endpoint="stub:50065")
    manual = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="manual", trust_level="first_party", latency_budget_ms=5000,
        deployment="cloud", kind="agent", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("manual.query")], route_hints=[],
    ), endpoint="stub:50066")
    navigation = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="navigation", trust_level="first_party", latency_budget_ms=5000,
        deployment="cloud", kind="agent", requires_permissions=[], context_scopes=["location"],
        capabilities=[_Cap("navigation.reroute")], route_hints=[],
    ), endpoint="stub:50067")
    return [chitchat, info, edge, merchant, manual, navigation]


class _Resp:
    def __init__(self, status=0, speech="", follow_up="", data=None):
        self.status, self.speech, self.follow_up = status, speech, follow_up
        self.actions, self.ui_card, self.missing_slots = [], None, []
        self.data = data


class _EscSpy:
    """clients stub：D0 流式脚本 + 按次序可编程的响应队列。

    `script` 只喂**第一次**流式调用（D0 的 chitchat 步）；之后的流式调用（改派后的单步云端 Agent，
    2026-09-18 起与 D0 同一份流式直通）从 `unary_seq` 出队、作为单个 final 流出——真实 Agent 的 SDK
    缺省 `handle_stream` 就是「把 handle 包成一个 final」。队列空 ⇒ 流零输出 ⇒ 编排回退 executor unary。
    `calls` 按时间序记录两条路（("stream"|"unary", intent, slots, meta)），断言「最后派了谁」看它。"""

    def __init__(self, plan_json=_SINGLE_PLAN, script=None, unary_seq=None):
        self.plan_json = plan_json
        self.script = script or []
        self._script_used = False
        self.unary_seq = list(unary_seq or [])   # _Resp 顺序出队（流式改派步与 unary 共用）
        self.stream_calls: list[tuple[str, dict, dict]] = []
        self.stream_ctx_raw_texts: list[str] = []
        self.unary_calls: list[tuple[str, dict, dict]] = []
        self.unary_ctx_raw_texts: list[str] = []
        self.calls: list[tuple[str, str, dict, dict]] = []

    async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None, context_scopes=None):
        self.stream_calls.append((intent, dict(slots or {}), dict(meta or {})))
        self.stream_ctx_raw_texts.append(str(getattr(ctx, "raw_text", "") or ""))
        self.calls.append(("stream", intent, dict(slots or {}), dict(meta or {})))
        if self.script and not self._script_used:
            self._script_used = True
            for item in self.script:
                yield item
            return
        if self.unary_seq:
            yield ("final", self.unary_seq.pop(0))

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None):
        self.unary_calls.append((intent, dict(slots or {}), dict(meta or {})))
        self.unary_ctx_raw_texts.append(str(getattr(ctx, "raw_text", "") or ""))
        self.calls.append(("unary", intent, dict(slots or {}), dict(meta or {})))
        if self.unary_seq:
            return self.unary_seq.pop(0)
        return _Resp(speech=f"（{intent} 兜底）")

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            return self.plan_json
        return "（聚合话术）"

    async def resolve(self, query="", intent="", top_k=1):
        return _agents()[:1]

    async def list_agents(self):
        return _agents()


def _make_engine(spy):
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session,
    )
    return engine, session


def _req(text, session_id="sess-esc", is_confirmation=False):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="r1",
        is_confirmation=is_confirmation,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _run(engine, req):
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


def test_d0_escalate_redirects_to_search_with_process_region():
    """契约 a+g：零播报 chitchat 声明改派 → executor 跑 info.search（带 heavy 预算/thinking/
    过程区），final=搜索结果话术。"""
    spy = _EscSpy(
        script=[("final", _Resp(speech="", data=dict(_ESC)))],
        unary_seq=[_Resp(speech="昨晚决赛皇马夺冠。")])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("昨晚欧冠谁赢了"))

    final = events[-1]
    assert final["kind"] == "final"
    assert final["speech"] == "昨晚决赛皇马夺冠。"
    # 改派步走与 D0 同一份流式直通（契约 h；step 仍经 _validated_steps 装配，heavy 预算 / thinking 带出）
    assert not spy.unary_calls
    assert [c[0] for c in spy.stream_calls] == ["chitchat.talk", "info.search"]
    assert spy.stream_calls[1][1].get("query") == "昨晚欧冠决赛结果"
    assert spy.stream_calls[1][2].get("thinking") == "on"      # heavy → 动态开思考
    progress = [e for e in events if e["kind"] == "progress"]
    assert any(e["phase"] == "execute" and e["status"] == "running" for e in progress)
    assert any(e["phase"] == "execute" and e["status"] == "done" for e in progress)


def test_escalated_result_cannot_escalate_again():
    """契约 b：escalated 结果再带 _escalate → 不再跳（单跳预算），且保留键被剥离。"""
    spy = _EscSpy(
        script=[("final", _Resp(speech="", data=dict(_ESC)))],
        unary_seq=[_Resp(speech="搜到了。", data={"_escalate": {
            "intent": "info.search", "slots": {"query": "再来一跳"}}})])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("昨晚欧冠谁赢了"))

    assert [c[0] for c in spy.stream_calls] == ["chitchat.talk", "info.search"]  # 只有一跳
    assert not spy.unary_calls
    assert events[-1]["speech"] == "搜到了。"


def test_escalate_invalid_intent_ignored_with_honest_fallback():
    """契约 c：目标 intent 无承接 Agent → 忽略改派，诚实兜底话术（不空转、不崩）。"""
    spy = _EscSpy(script=[("final", _Resp(speech="", data={"_escalate": {
        "intent": "nope.intent", "slots": {}}}))])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("昨晚欧冠谁赢了"))

    assert not spy.unary_calls                                  # 没有乱派
    assert [c[0] for c in spy.stream_calls] == ["chitchat.talk"]
    assert "联网查询" in events[-1]["speech"]


def test_executor_path_replaces_only_escalating_step():
    """契约 d：多步计划里第一步声明改派 → 该步结果被 escalated 结果替换，第二步保留聚合。"""
    spy = _EscSpy(
        plan_json=_TWO_STEP_PLAN,
        unary_seq=[
            _Resp(speech="", data=dict(_ESC)),      # s1 chitchat：声明改派
            _Resp(speech="第二步话术。"),             # s2 chitchat：正常
            _Resp(speech="搜索结果话术。"),            # esc1 info.search
        ])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("先查个东西再聊一句"))

    assert [c[0] for c in spy.unary_calls] == ["chitchat.talk", "chitchat.talk"]
    assert [c[1] for c in spy.calls] == ["chitchat.talk", "chitchat.talk", "info.search"]
    assert spy.calls[-1][0] == "stream"                          # 改派步流式（契约 h）
    assert events[-1]["kind"] == "final"
    assert events[-1]["speech"]                                  # 聚合产出非空


def test_escalated_need_confirm_suspends_like_f1():
    """契约 e：escalated 步 NEED_CONFIRM → 走既有挂起语义（session 保存 mini-plan）。"""
    spy = _EscSpy(
        script=[("final", _Resp(speech="", data=dict(_ESC)))],
        unary_seq=[_Resp(status=1, speech="需要确认后才能继续，确认吗？")])
    engine, session = _make_engine(spy)
    events = _run(engine, _req("昨晚欧冠谁赢了"))

    final = events[-1]
    assert final["need_confirm"] is True
    state = asyncio.run(session.load("sess-esc", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_confirm"
    assert state.pending_step_id == "esc1"


def test_streamed_reply_ignores_escalate():
    """契约 f：已流式播报过增量 → 忽略 escalate（不二次回答），final 用原话术。"""
    spy = _EscSpy(script=[
        ("speech", "早上"),
        ("final", _Resp(speech="早上好呀。", data=dict(_ESC)))])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("早"))

    assert not spy.unary_calls                                   # 未改派
    assert [c[0] for c in spy.stream_calls] == ["chitchat.talk"]
    assert events[-1]["speech"] == "早上好呀。"


@pytest.mark.parametrize("route", ["d0", "executor"])
@pytest.mark.parametrize("intent", ["warning_light.close", "luckin.order"])
def test_question_escalate_replaces_blocked_target_with_safe_response(route, intent):
    """问句改派不能执行 edge 写/manifest-confirmed cloud，须走声明式安全回答。"""
    esc = {"_escalate": {
        "intent": intent, "slots": {}, "reason": "model_redirect",
    }}
    safe = _Resp(
        speech="红色机油灯表示润滑系统可能异常，请立即安全停车并联系救援。",
        data={"_escalate": {
            "intent": "warning_light.close", "slots": {},
            "reason": "second_hop",
        }},
    )
    if route == "d0":
        spy = _EscSpy(
            script=[("final", _Resp(speech="", data=esc))],
            unary_seq=[safe],
        )
    else:
        spy = _EscSpy(unary_seq=[_Resp(speech="", data=esc), safe])
    engine, session = _make_engine(spy)

    events = _run(engine, _req("红色机油灯亮了还能继续开吗"))

    called = [call[1] for call in spy.calls]
    assert intent not in called
    assert called[-1] == "chitchat.talk"
    assert "请立即安全停车" in events[-1]["speech"]
    assert not any(event.get("kind") == "action" for event in events)
    assert not any(event.get("need_confirm") for event in events)
    assert called.count("warning_light.close") == 0  # replacement 的二跳也不得消费
    assert asyncio.run(session.load("sess-esc", owner_user_id="u1")) is None


def test_directive_escalate_to_edge_write_still_dispatches():
    esc = {"_escalate": {
        "intent": "warning_light.close", "slots": {},
        "reason": "model_redirect",
    }}
    spy = _EscSpy(
        script=[("final", _Resp(speech="", data=esc))],
        unary_seq=[_Resp(speech="已关闭双闪。")],
    )
    engine, _ = _make_engine(spy)

    events = _run(engine, _req("关闭双闪"))

    assert [call[0] for call in spy.unary_calls].count("warning_light.close") == 1
    assert events[-1]["speech"] == "已关闭双闪。"


def test_d0_question_escalate_to_cloud_read_still_dispatches():
    esc = {"_escalate": {
        "intent": "manual.query", "slots": {}, "reason": "model_redirect",
    }}
    spy = _EscSpy(
        script=[("final", _Resp(speech="", data=esc))],
        unary_seq=[_Resp(speech="请立即安全停车并查阅车辆手册。")],
    )
    engine, _ = _make_engine(spy)

    events = _run(engine, _req("红色机油灯亮了还能继续开吗"))

    assert [c[1] for c in spy.calls] == ["chitchat.talk", "manual.query"]
    assert spy.calls[-1][0] == "stream"                          # 云端只读改派步流式
    assert events[-1]["speech"] == "请立即安全停车并查阅车辆手册。"


def test_executor_question_escalate_to_cloud_read_still_dispatches():
    esc = {"_escalate": {
        "intent": "manual.query", "slots": {}, "reason": "model_redirect",
    }}
    spy = _EscSpy(unary_seq=[
        _Resp(speech="", data=esc),
        _Resp(speech="请立即安全停车并查阅车辆手册。"),
    ])
    engine, _ = _make_engine(spy)

    events = _run(engine, _req("红色机油灯亮了还能继续开吗"))

    assert [call[1] for call in spy.calls].count("manual.query") == 1
    assert events[-1]["kind"] == "final"
    assert events[-1]["speech"]


def test_blocked_escalate_without_safe_response_has_no_dispatch():
    async def run_case():
        spy = _EscSpy()
        engine, _ = _make_engine(spy)
        sink = {}
        ctx = PlanContext(raw_text="红色机油灯亮了还能继续开吗")
        agents = [agent for agent in _agents()
                  if agent.manifest.agent_id != "chitchat"]
        events = [event async for event in engine._run_escalated(
            {"intent": "warning_light.close", "slots": {},
             "reason": "redirect"},
            ctx, agents, sink,
        )]
        return spy, sink, events

    spy, sink, events = asyncio.run(run_case())

    assert "warning_light.close" not in [call[1] for call in spy.calls]
    assert sink == {}
    assert events == []


@pytest.mark.parametrize("intent", ["warning_light.close", "luckin.order"])
def test_resumed_escalate_uses_origin_but_agent_keeps_current_slot_answer(intent):
    """安全判定看首句，Agent 上下文仍看本轮“拿铁”；两者不可互相覆盖。"""
    async def run_case():
        safe = _Resp(speech="请立即安全停车并联系救援。")
        spy = _EscSpy(unary_seq=[safe])
        engine, _ = _make_engine(spy)
        sink = {}
        ctx = PlanContext(
            raw_text="拿铁",
            safety_origin_text="红色机油灯亮了还能继续开吗",
        )
        events = [event async for event in engine._run_escalated(
            {"intent": intent, "slots": {}, "reason": "redirect"},
            ctx, _agents(), sink,
        )]
        return spy, sink, events, ctx

    spy, sink, _events, ctx = asyncio.run(run_case())

    called = [item[1] for item in spy.calls]
    assert intent not in called
    assert called == ["chitchat.talk"]
    assert spy.calls[0][0] == "stream"                           # response-only 云端步同样流式
    assert spy.stream_calls[0][1]["text"] == "红色机油灯亮了还能继续开吗"
    assert spy.stream_ctx_raw_texts == ["拿铁"]
    assert ctx.raw_text == "拿铁"
    assert sink["results"][0].speech == "请立即安全停车并联系救援。"


def _nav_agents():
    nav = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="navigation", trust_level="first_party", latency_budget_ms=5000,
        deployment="cloud", kind="agent", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("navigation.search_poi"), _Cap("navigation.navigate_to")], route_hints=[],
    ), endpoint="stub:50070")
    return [nav]


class _NavSpy(_EscSpy):
    async def resolve(self, query="", intent="", top_k=1):
        return _nav_agents()

    async def list_agents(self):
        return _nav_agents()


def test_escalated_need_slot_is_resumed_on_the_escalated_step():
    """契约 i（评审四轮 RS39，2026-09-25）：改派步带着自己的话术（「没找到 X」，不是零播报）照样改派——final 里的话术
    不算流出过；目标返回 NEED_SLOT ⇒ 挂起落在 esc1（session 存 mini-plan），改派前那句不重复播；下一句补槽续接的是 esc1：
    槽值是这句话，Agent 读到的本轮原话也是这句话。"""
    esc = {"_escalate": {"intent": "navigation.navigate_to", "slots": {"destination": "云岚国际中心"},
                         "reason": "search_not_found"}}
    ask = _Resp(status=2, speech="暂时无法确定「云岚国际中心」对应的具体地点。", follow_up="请补充城市或更详细的地址")
    ask.missing_slots = ["destination"]
    spy = _NavSpy(
        plan_json=json.dumps({"steps": [{"id": "s1", "capability_ref": "cap_0002",   # navigation.search_poi
                                         "slots": {"keyword": "云岚国际中心"}, "depends_on": [], "slot_refs": {}}]}),
        script=[("final", _Resp(speech="没找到「云岚国际中心」。", data=esc))],
        unary_seq=[ask, _Resp(speech="为您导航到深圳湾公园。")])
    engine, session = _make_engine(spy)

    first = _run(engine, _req("导航去云岚国际中心"))[-1]
    assert first["speech"] == "暂时无法确定「云岚国际中心」对应的具体地点。", first
    state = asyncio.run(session.load("sess-esc", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_slot" and state.pending_step_id == "esc1"
    assert state.missing_slots == ["destination"]

    second = _run(engine, SimpleNamespace(
        text="深圳湾公园", session_id="sess-esc", request_id="r2", is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1")))[-1]
    assert [c[1] for c in spy.calls] == [
        "navigation.search_poi", "navigation.navigate_to", "navigation.navigate_to"]
    assert spy.calls[-1][2].get("destination") == "深圳湾公园"
    assert (spy.stream_ctx_raw_texts + spy.unary_ctx_raw_texts)[-1] == "深圳湾公园"
    # 改派步被规划时那句随 meta 下发给续接那一次（导航认起点、认「换第二个」是在换站都靠它）；首次派发不带
    from runtime.slots import STEP_ORIGIN_META
    assert spy.calls[-1][3].get(STEP_ORIGIN_META) == "导航去云岚国际中心"
    assert STEP_ORIGIN_META not in spy.calls[1][3]
    assert second["speech"] == "为您导航到深圳湾公园。"
    assert asyncio.run(session.load("sess-esc", owner_user_id="u1")) is None


def test_unknown_legacy_origin_blocks_escalated_write_but_allows_read():
    async def run_target(intent):
        spy = _EscSpy(unary_seq=[_Resp(speech="read result")])
        engine, _ = _make_engine(spy)
        sink = {}
        ctx = PlanContext(raw_text="确认", safety_origin_text="")
        awaitables = engine._run_escalated(
            {"intent": intent, "slots": {}, "reason": "legacy"},
            ctx, _agents(), sink,
        )
        _ = [event async for event in awaitables]
        return spy, sink

    write_spy, _ = asyncio.run(run_target("warning_light.close"))
    read_spy, read_sink = asyncio.run(run_target("manual.query"))

    assert "warning_light.close" not in [item[1] for item in write_spy.calls]
    assert [item[1] for item in read_spy.calls] == ["manual.query"]
    assert read_sink["results"][0].speech == "read result"


def test_escalated_step_receives_the_focus_facts_of_this_turn():
    """CA2-19 换站：改派出的步与主计划的步同一轮执行，活动路线这类焦点事实按同一份 `_apply_focus_meta` 下发
    （只给声明 location 的步）。此前只给主计划，改派到导航改路线时 Agent 只能答「当前没有正在进行的导航」。"""
    route = {"destination": "深圳北站", "lat": 22.609, "lng": 114.029,
             "waypoints": [{"name": "路特斯充电站", "lat": 22.61, "lng": 114.03}],
             "strategy": "", "ts": int(time.time())}
    spy = _EscSpy(
        script=[("final", _Resp(speech="", data={"_escalate": {
            "intent": "navigation.reroute", "slots": {}, "reason": "swap_route_charger"}}))],
        unary_seq=[_Resp(speech="已把途经点换成另一个充电站。")])
    engine, _ = _make_engine(spy)
    nav_plan = Plan(steps=[Step(id="s1", agent_id="navigation", intent="navigation.navigate_to")])
    seeded = StepResult(step_id="s1", status=StepStatus.OK, source_intent="navigation.navigate_to",
                        data={"destination": "深圳北站", "_route_session": route})
    asyncio.run(engine.context.update_focus("sess-esc", nav_plan, [seeded], user_id="u1"))

    events = _run(engine, _req("换一个充电站"))

    assert [c[1] for c in spy.calls] == ["chitchat.talk", "navigation.reroute"]
    assert json.loads(spy.calls[-1][3]["focus_active_route"])["destination"] == "深圳北站"
    assert "focus_active_route" not in spy.calls[0][3]      # 没声明 location 的闲聊照旧收不到
    assert events[-1]["speech"] == "已把途经点换成另一个充电站。"


def test_a_resumed_step_receives_the_focus_facts_at_resume_time():
    """2026-10-10 真栈 swap2：「换一个充电站」改派导航改路线、路线上两个充电站 ⇒ 反问换哪一个、挂起；「换第二个」续接时
    step.meta 不持久化，恢复出的那一步收不到活动路线，导航答「当前没有正在进行的导航」。续接轮按这一刻的焦点重新下发。"""
    route = {"destination": "深圳北站", "lat": 22.609, "lng": 114.029,
             "waypoints": [{"name": "路特斯充电站", "lat": 22.61, "lng": 114.03},
                           {"name": "特来电充电站", "lat": 22.615, "lng": 114.03}],
             "strategy": "", "ts": int(time.time())}
    ask = _Resp(status=2, speech="路线上有2个充电站：路特斯充电站、特来电充电站。您要换哪一个？")
    ask.missing_slots = ["remove_waypoint"]
    spy = _EscSpy(
        script=[("final", _Resp(speech="", data={"_escalate": {
            "intent": "navigation.reroute", "slots": {}, "reason": "swap_route_charger"}}))],
        unary_seq=[ask, _Resp(speech="已把途经点特来电充电站换成星星充电。")])
    engine, session = _make_engine(spy)
    nav_plan = Plan(steps=[Step(id="s1", agent_id="navigation", intent="navigation.navigate_to")])
    seeded = StepResult(step_id="s1", status=StepStatus.OK, source_intent="navigation.navigate_to",
                        data={"destination": "深圳北站", "_route_session": route})
    asyncio.run(engine.context.update_focus("sess-esc", nav_plan, [seeded], user_id="u1"))

    first = _run(engine, _req("换一个充电站"))[-1]
    state = asyncio.run(session.load("sess-esc", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_slot" and state.pending_step_id == "esc1", first

    second = _run(engine, SimpleNamespace(
        text="换第二个", session_id="sess-esc", request_id="r2", is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1")))[-1]

    assert [c[1] for c in spy.calls] == ["chitchat.talk", "navigation.reroute", "navigation.reroute"]
    resumed_meta = spy.calls[-1][3]
    assert json.loads(resumed_meta["focus_active_route"])["destination"] == "深圳北站"
    assert len(json.loads(resumed_meta["focus_active_route"])["waypoints"]) == 2
    assert second["speech"] == "已把途经点特来电充电站换成星星充电。"


# ── 改派续跑（`resume`）与失败回落（`fallback_speech`），2026-10-17 ──────────────────────────────────────────────

_MCD_ESC = {"_refused": True, "_escalate": {
    "intent": "nearby.search", "slots": {"brand": "麦当劳"}, "reason": "mcd_store_nearby",
    "resume": {"clear": ["store_hint", "city"]}}}
#: 周边按品牌搜回来的一批：第一家不是麦当劳（demo-dqt1fu 的形状），名字带搜索词的是第二家
_NEARBY_MCD = {"items": [{"name": "沙县小吃(科技园店)", "city": "深圳市"},
                         {"name": "麦当劳(汉京中心店)", "city": "深圳市"}],
               "_candidate_label": "麦当劳"}
_MENU_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": "cap_0001", "slots": {"store_hint": "附近", "city": "当前位置"},
     "depends_on": [], "slot_refs": {}}]})
_ORDER_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": "cap_0002", "slots": {"store_hint": "附近", "item": "巨无霸"},
     "depends_on": [], "slot_refs": {}}]})


def _cap(intent, slots=(), **kw):
    cap = _Cap(intent, **kw)
    cap.slots = list(slots)
    return cap


def _mcd_agents():
    bridge = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="mcp-bridge", trust_level="first_party", latency_budget_ms=5000,
        deployment="cloud", kind="agent", requires_permissions=[], context_scopes=[],
        capabilities=[_cap("mcd.menu", ["store_hint", "city"]),
                      _cap("mcd.order", ["store_hint", "city", "item"], require_confirm=True)],
        route_hints=[]), endpoint="stub:50065")
    nearby = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="nearby", trust_level="first_party", latency_budget_ms=15000,
        deployment="cloud", kind="agent", requires_permissions=[], context_scopes=[],
        capabilities=[_cap("nearby.search", ["brand", "keyword"])], route_hints=[]), endpoint="stub:50070")
    return [bridge, nearby]


class _McdSpy(_EscSpy):
    async def resolve(self, query="", intent="", top_k=1):
        return _mcd_agents()

    async def list_agents(self):
        return _mcd_agents()


def _pending_step(state, step_id):
    plan = json.loads(state.pending_plan) if isinstance(state.pending_plan, str) else state.pending_plan
    return next(s for s in plan["steps"] if s["id"] == step_id)


def test_resume_reruns_the_declaring_step_with_the_escalated_listing():
    """契约 j（demo-dqt1fu 登记项「改派后选店不续原请求」）：麦当劳桥没有门店线索、改派周边按品牌找；周边成功后编排把
    「看菜单」那一步续跑一次——清掉声明里的槽，执行器按本轮补全取名字带「麦当劳」的那一家（不是第一家沙县小吃）与城市。
    修前列完就结束，用户回「第一个。」被规划成看周边详情。"""
    spy = _McdSpy(
        plan_json=_MENU_PLAN,
        script=[("final", _Resp(speech="", data=dict(_MCD_ESC)))],
        unary_seq=[_Resp(speech="为您找到 2 家麦当劳。", data=dict(_NEARBY_MCD)),
                   _Resp(speech="汉京中心店的菜单：巨无霸 25 元。")])
    engine, session = _make_engine(spy)
    events = _run(engine, _req("帮我查看离我最近那家麦当劳的菜单"))

    assert [c[1] for c in spy.calls] == ["mcd.menu", "nearby.search", "mcd.menu"]
    rerun_slots = spy.calls[-1][2]
    assert rerun_slots.get("store_hint") == "麦当劳(汉京中心店)"
    assert rerun_slots.get("city") == "深圳市"
    assert events[-1]["kind"] == "final" and not events[-1].get("need_slot")
    assert asyncio.run(session.load("sess-esc", owner_user_id="u1")) is None


def test_resume_is_dropped_when_the_rerun_still_escalates():
    """续跑仍要改派（结果里没有它要的店）：不消费第二次改派、丢掉续跑，只交周边的结果（= 修前行为）。"""
    spy = _McdSpy(
        plan_json=_MENU_PLAN,
        script=[("final", _Resp(speech="", data=dict(_MCD_ESC)))],
        unary_seq=[_Resp(speech="附近没有找到麦当劳。", data={"items": [{"name": "沙县小吃(科技园店)"}],
                                                          "_candidate_label": "麦当劳"}),
                   _Resp(speech="我先帮你找找附近的麦当劳。", data=dict(_MCD_ESC))])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("帮我查看离我最近那家麦当劳的菜单"))

    assert [c[1] for c in spy.calls] == ["mcd.menu", "nearby.search", "mcd.menu"]
    assert events[-1]["speech"] == "附近没有找到麦当劳。"


def test_no_resume_when_the_escalated_step_fails_or_is_not_declared():
    """改派那一步失败不续跑；声明里没有 `resume` 的改派（旧形态）照旧只跑一跳。"""
    failed = _McdSpy(plan_json=_MENU_PLAN,
                     script=[("final", _Resp(speech="", data=dict(_MCD_ESC)))],
                     unary_seq=[_Resp(status=3, speech="周边搜索暂时不可用。")])
    engine, _ = _make_engine(failed)
    _run(engine, _req("帮我查看离我最近那家麦当劳的菜单"))
    assert [c[1] for c in failed.calls] == ["mcd.menu", "nearby.search"]

    legacy = {"_refused": True, "_escalate": {k: v for k, v in _MCD_ESC["_escalate"].items() if k != "resume"}}
    plain = _McdSpy(plan_json=_MENU_PLAN,
                    script=[("final", _Resp(speech="", data=legacy))],
                    unary_seq=[_Resp(speech="为您找到 2 家麦当劳。", data=dict(_NEARBY_MCD))])
    engine, _ = _make_engine(plain)
    events = _run(engine, _req("帮我查看离我最近那家麦当劳的菜单"))
    assert [c[1] for c in plain.calls] == ["mcd.menu", "nearby.search"]
    assert events[-1]["speech"] == "为您找到 2 家麦当劳。"


def test_a_resumed_step_that_asks_suspends_on_the_declaring_step():
    """续跑出选店卡（官方同名多店）：走既有挂起语义，挂起落在声明方那一步（不是改派步），门店与城市已随计划存下。"""
    ask = _Resp(status=2, speech="附近有两家汉京中心店，要哪一家？")
    ask.missing_slots = ["store_hint"]
    spy = _McdSpy(
        plan_json=_MENU_PLAN,
        script=[("final", _Resp(speech="", data=dict(_MCD_ESC)))],
        unary_seq=[_Resp(speech="为您找到 2 家麦当劳。", data=dict(_NEARBY_MCD)), ask])
    engine, session = _make_engine(spy)
    events = _run(engine, _req("帮我查看离我最近那家麦当劳的菜单"))

    state = asyncio.run(session.load("sess-esc", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_slot" and state.pending_step_id == "s1", events[-1]
    pending = _pending_step(state, "s1")
    assert pending["slots"].get("store_hint") == "麦当劳(汉京中心店)"
    assert pending["depends_on"] == ["esc1"]


def test_resume_on_the_executor_path_reaches_the_confirmation():
    """需确认的下单步走 executor 路径：改派、续跑同样生效，续跑出的确认卡挂在声明方那一步上；没列在 clear 里的槽照留。"""
    confirm = _Resp(status=1, speech="请确认：麦当劳汉京中心店，巨无霸 1 份。")
    spy = _McdSpy(
        plan_json=_ORDER_PLAN,
        unary_seq=[_Resp(speech="我先帮你找找附近的麦当劳。", data=dict(_MCD_ESC)),
                   _Resp(speech="为您找到 2 家麦当劳。", data=dict(_NEARBY_MCD)),
                   confirm])
    engine, session = _make_engine(spy)
    events = _run(engine, _req("帮我在最近的麦当劳点个巨无霸"))

    assert [c[1] for c in spy.calls] == ["mcd.order", "nearby.search", "mcd.order"]
    assert spy.calls[-1][2].get("store_hint") == "麦当劳(汉京中心店)"
    assert spy.calls[-1][2].get("item") == "巨无霸"
    state = asyncio.run(session.load("sess-esc", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_confirm" and state.pending_step_id == "s1", events[-1]


_ALERT_ESC = {"_safety_alert": {"level": "amber", "signal": "胎压报警"}, "_escalate": {
    "intent": "manual.query", "slots": {"question": "胎压报警灯是什么意思"},
    "reason": "vehicle_alert_question", "fallback_speech": "胎压报警灯亮了，请降低车速，尽快检查轮胎。"}}


def test_fallback_speech_answers_when_the_escalated_step_fails():
    """契约 k（F01 S3 的前提）：改派必须零播报，失败时原步话术是空的——声明方带了回落话术就用它，处置提示一个字不少。"""
    spy = _EscSpy(script=[("final", _Resp(speech="", data=dict(_ALERT_ESC)))],
                  unary_seq=[_Resp(status=3, speech="")])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("胎压报警灯是什么意思"))

    assert [c[1] for c in spy.calls] == ["chitchat.talk", "manual.query"]
    assert events[-1]["speech"] == "胎压报警灯亮了，请降低车速，尽快检查轮胎。"


def test_fallback_speech_answers_when_no_agent_serves_the_target():
    esc = {"_escalate": {**_ALERT_ESC["_escalate"], "intent": "nope.intent"}}
    spy = _EscSpy(script=[("final", _Resp(speech="", data=esc))])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("胎压报警灯是什么意思"))
    assert events[-1]["speech"] == "胎压报警灯亮了，请降低车速，尽快检查轮胎。"


def test_fallback_speech_is_not_used_when_the_escalated_step_answers():
    spy = _EscSpy(script=[("final", _Resp(speech="", data=dict(_ALERT_ESC)))],
                  unary_seq=[_Resp(speech="胎压报警灯表示胎压异常（手册第 257 页）。")])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("胎压报警灯是什么意思"))
    assert events[-1]["speech"] == "胎压报警灯表示胎压异常（手册第 257 页）。"


class _RecordingSpy(_EscSpy):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.llm_prompts: list[str] = []

    async def llm(self, messages, **kwargs):
        self.llm_prompts.append(json.dumps(messages, ensure_ascii=False))
        return await super().llm(messages, **kwargs)


def test_executor_path_fallback_and_unconsumed_escalations_speak_their_fallback():
    """executor 路径：被消费的那条改派失败用回落话术；每轮只消费第一条改派，第二条声明方的回落话术也照说（不然那一步一个字都没有）。"""
    second = {"_escalate": {**_ALERT_ESC["_escalate"], "fallback_speech": "机油灯亮了，请尽快安全停车。"}}
    spy = _RecordingSpy(
        plan_json=_TWO_STEP_PLAN,
        unary_seq=[_Resp(speech="", data=dict(_ALERT_ESC)),     # s1：改派手册
                   _Resp(speech="", data=second),               # s2：也声明改派（本轮不再消费）
                   _Resp(status=3, speech="")])                 # esc1 手册失败
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("胎压报警灯是什么意思，机油灯呢"))

    assert [c[1] for c in spy.calls] == ["chitchat.talk", "chitchat.talk", "manual.query"]
    aggregate = spy.llm_prompts[-1]
    assert "请降低车速" in aggregate and "请尽快安全停车" in aggregate, aggregate[:400]
    assert events[-1]["kind"] == "final"


def test_unconsumed_escalate_speech_only_fills_a_silent_declaring_step():
    from orchestrator.cloud.executor import unconsumed_escalate_speech

    silent = StepResult("s1", StepStatus.OK, speech="", data=dict(_ALERT_ESC))
    assert unconsumed_escalate_speech(silent).speech.startswith("胎压报警灯亮了")
    assert "_escalate" in silent.data, "T2 不消费改派、也不剥键（同修前）"
    spoken = StepResult("s1", StepStatus.OK, speech="已经说过的话。", data=dict(_ALERT_ESC))
    assert unconsumed_escalate_speech(spoken).speech == "已经说过的话。"
    plain = StepResult("s1", StepStatus.OK, speech="", data={"_escalate": {"intent": "info.search", "slots": {}}})
    assert unconsumed_escalate_speech(plain).speech == ""
