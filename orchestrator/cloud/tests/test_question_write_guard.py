"""云侧安全闸：问句不许被规划成写车控（卡 C1-A，2026-08-26 QA P0-01）。

## 症状

同一句「红色机油灯亮了怎么办」在四个 persona 走了**三条不同的错法**，最恶性那条是
family T28 / adv T32：cloud planner 产 `warning_light.close` 并**真的执行了**——
用户在问故障灯，系统把双闪关了。执行链上没有任何一道闸拦得住它：
`actionability` 是 shadow 不进主链、`Step` 没有 effect 字段、`capability_meta.effect_of`
只有端侧消费，而 `warning_light` 恰好三闸（require_confirm / drive_restricted /
voice_forbidden）全 false。

端侧对同一形态**早就有闸**（`classify_structured` 出口：问句形态的写操作一律不产出）。
所以这一批做的是把那份判据**下沉到 runtime 再在云侧挂一次**，不是在云侧抄第二份
——判定抄两份正是 B1 那个 bug 的成因。

## 与 `test_safety_focus` 里那句「刻意不做」的关系

那份文档写着「不加『安全语境下禁止一切无关车控』的硬闸——安全对话不该剥夺用户开空调
的权利」。这条闸**不是那个**：它不看会话里有没有告警，只看**这一句话的形态**
（在问 vs 在下指令）。安全对话中间说「把空调打开」照样执行——下面第 3 组就是这条对照。

## 覆盖形状

四向，缺一条都守不住（收窄面的老规矩）：
  · 问句 + 写 = 拦；· 祈使 + 写 = 放；· 问句 + 读 = 放；· 问句 + 未确认云侧步 = 放。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.models import Plan, PlanContext, SessionState, Step
from orchestrator.cloud.planning import (
    PlanBuilder, _assemble_capability_catalog,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_planning import MockAgent  # noqa: E402  复用真实装配路径的 mock agent


def _step(
    intent: str,
    *,
    agent_id: str = "edge-vehicle",
    deployment: str = "edge",
    kind: str = "edge_fast",
    require_confirm: bool = False,
) -> Step:
    return Step(
        id="s1", agent_id=agent_id, intent=intent,
        deployment=deployment, kind=kind,
        require_confirm=require_confirm, slots={},
    )


def _response(agent, intent):
    next(
        c for c in agent.manifest.capabilities if c.intent == intent
    ).response_only = True
    return agent


_GUARD = PlanBuilder._question_side_effect_steps


# ── 1. 问句 + 写车控 = 拦 ──────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "红色机油灯亮了怎么办",
    "水温灯亮了还能继续开吗",
    "这车的天窗最大能开多大",
    "要是双闪一直开着会怎么样",
])
def test_question_shaped_write_is_blocked(text):
    assert _GUARD([_step("warning_light.close")], text)


# ── 2. 祈使 + 写车控 = 放（不许误伤正常指令）─────────────────────────────────

@pytest.mark.parametrize("text", [
    "打开双闪",
    "关闭双闪",
    "帮我把车窗关一下",
    "请把空调调到 24 度",
    "温度如何调高",              # 方式问法 + 操作动词 ⇒ 仍是指令
])
def test_directive_write_is_allowed(text):
    assert _GUARD([_step("warning_light.open")], text) == []


# ── 3. 问句 + 只读步 = 放 ──────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["胎压是多少", "电量还有多少", "车窗开着吗"])
def test_question_shaped_read_is_allowed(text):
    assert _GUARD([_step("tire_pressure.query")], text) == []


# ── 4. 问句 + 未确认云侧步 = 放（不盖查资料/查天气）────────────────────────────

@pytest.mark.parametrize("intent", ["manual.query", "info.search", "chitchat.talk"])
def test_question_shaped_unconfirmed_cloud_step_is_allowed(intent):
    cloud_step = _step(intent, deployment="cloud", kind="agent")
    assert _GUARD([cloud_step], "红色机油灯亮了怎么办") == []


def test_question_shaped_confirmed_cloud_step_is_blocked():
    order = _step(
        "luckin.order", agent_id="mcp-bridge", deployment="cloud",
        kind="agent", require_confirm=True,
    )
    assert _GUARD([order], "红色机油灯亮了还能继续开吗") == [order]


def test_directive_to_confirmed_cloud_step_is_allowed():
    order = _step(
        "luckin.order", agent_id="mcp-bridge", deployment="cloud",
        kind="agent", require_confirm=True,
    )
    assert _GUARD([order], "帮我点一杯生椰拿铁") == []


# ── 5. 混合计划只丢被拦的那一步 ─────────────────────────────────────────────

def test_only_the_offending_step_is_selected():
    steps = [_step("manual.query", deployment="cloud", kind="agent"),
             _step("warning_light.close")]
    blocked = _GUARD(steps, "红色机油灯亮了怎么办")
    assert [s.intent for s in blocked] == ["warning_light.close"]


def test_mixed_plan_only_selects_the_confirmed_cloud_step():
    manual = _step("manual.query", deployment="cloud", kind="agent")
    order = _step(
        "luckin.order", agent_id="mcp-bridge", deployment="cloud",
        kind="agent", require_confirm=True,
    )
    blocked = _GUARD([manual, order], "红色机油灯亮了还能继续开吗")
    assert blocked == [order]


# ── 6. 端到端：整条 build() 上真的拦得住，并且给得出回答 ──────────────────────

@pytest.fixture(autouse=True)
def _offline_retrieval(monkeypatch):
    """范例检索默认 hybrid 会打 llm-gateway Embed（网络）。单测必须离线确定。

    同根 conftest 对 `SKILLS_RETRIEVAL` 的处理——那一条只钉了知识通道，
    范例通道是第二条，两条都要钉住才叫离线。
    """
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _build(text: str):
    """跑真正的 `build()`：模型**照旧**产车控写步，看闸拦不拦得住。

    capability_ref 从真实装配出来的 catalog 里取，不写死 `cap_0001`
    ——ref 编号随目录顺序走，写死等于把测试挂在一个会漂的实现细节上。
    """
    agents = [
        MockAgent("edge-vehicle", ["warning_light.close"],
                  kind="edge_fast", deployment="edge"),
        MockAgent("chitchat", ["chitchat.talk"],
                  response_only=("chitchat.talk",)),
    ]
    catalog = _assemble_capability_catalog(agents)
    ref = catalog.pair_to_ref[("edge-vehicle", "warning_light.close")]
    wire = ('{"steps":[{"id":"s1","capability_ref":"%s",'
            '"slots":{},"depends_on":[],"slot_refs":{}}]}' % ref)

    async def mock_llm(messages):
        return wire

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    return asyncio.run(builder.build(text, WorkingSet(catalog=agents),
                                     PlanContext(session_id="t")))


def _build_cloud_order(text: str):
    agents = [
        MockAgent("mcp-bridge", ["luckin.order"],
                  require_confirm=("luckin.order",)),
        MockAgent("chitchat", ["chitchat.talk"],
                  response_only=("chitchat.talk",)),
    ]
    catalog = _assemble_capability_catalog(agents)
    ref = catalog.pair_to_ref[("mcp-bridge", "luckin.order")]
    wire = ('{"steps":[{"id":"s1","capability_ref":"%s",'
            '"slots":{},"depends_on":[],"slot_refs":{}}]}' % ref)

    async def mock_llm(messages):
        return wire

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    return asyncio.run(builder.build(text, WorkingSet(catalog=agents),
                                     PlanContext(session_id="t")))


def test_end_to_end_question_does_not_reach_a_vehicle_write_step():
    """**这条才是 P0-01 的复现**：模型照旧产 `warning_light.close`，闸在出口拦下。

    拦下之后不是空计划——空计划会让 engine 说「没听清」，而这一类句子恰恰最需要
    一个回答。落到兜底 Agent（它自带 `runtime.safety_signal` 的分级建议）。
    """
    plan = _build("红色机油灯亮了怎么办")
    assert all(s.intent != "warning_light.close" for s in plan.steps), \
        f"问句仍被规划成车控写步：{[s.intent for s in plan.steps]}"
    assert plan.steps, "拦下之后不能留下空计划"
    assert plan.steps[0].intent == "chitchat.talk"
    assert "question_write_blocked" in (plan.plan_mode or "")


def test_end_to_end_directive_still_executes():
    """对照：同一份 catalog、同一条计划，祈使句照常落到车控步。"""
    plan = _build("关闭双闪")
    assert [s.intent for s in plan.steps] == ["warning_light.close"]
    assert "question_write_blocked" not in (plan.plan_mode or "")


def test_end_to_end_question_does_not_reach_confirmed_cloud_write():
    plan = _build_cloud_order("红色机油灯亮了还能继续开吗")
    assert [s.intent for s in plan.steps] == ["chitchat.talk"]
    assert "question_write_blocked" in (plan.plan_mode or "")


def test_end_to_end_cloud_order_directive_still_reaches_confirmation_boundary():
    plan = _build_cloud_order("帮我点一杯生椰拿铁")
    assert [s.intent for s in plan.steps] == ["luckin.order"]
    assert plan.steps[0].require_confirm is True
    assert "question_write_blocked" not in (plan.plan_mode or "")


def test_question_fallback_registry_confirmed_capability_is_blocked():
    order = MockAgent("mcp-bridge", ["luckin.order"],
                      require_confirm=("luckin.order",))
    order.score = 1.0

    async def mock_llm(messages):
        return "not valid JSON"

    async def mock_resolve(query, top_k=1):
        return [order]

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    plan = asyncio.run(builder.build(
        "红色机油灯亮了还能继续开吗", WorkingSet(catalog=[order]),
        PlanContext(session_id="fallback-confirmed"),
    ))

    assert plan.steps == []
    assert all(step.intent != "luckin.order" for step in plan.steps)
    assert "question_write_blocked" in (plan.plan_mode or "")


def test_talk_only_plan_rejects_confirmed_fallback_capability():
    agent = MockAgent("chitchat", ["shop.order"],
                      require_confirm=("shop.order",))
    builder = PlanBuilder(llm_fn=None, registry_fn=None)

    assert builder._talk_only_plan("红色机油灯亮了还能继续开吗", [agent]) is None


def test_manifest_value_reaches_step_and_llm_cannot_forge_it():
    declared = _response(MockAgent("answer", ["answer.render"]), "answer.render")
    step = PlanBuilder._validated_steps([{
        "id": "s1", "agent_id": "answer", "intent": "answer.render",
        "slots": {}, "depends_on": [], "slot_refs": {}, "response_only": False,
    }], {"answer": declared})[0]
    assert getattr(step, "response_only", False) is True

    plain = MockAgent("plain", ["plain.render"])
    step = PlanBuilder._validated_steps([{
        "id": "s1", "agent_id": "plain", "intent": "plain.render",
        "slots": {}, "depends_on": [], "slot_refs": {}, "response_only": True,
    }], {"plain": plain})[0]
    assert getattr(step, "response_only", False) is False


def test_fallback_rejects_undeclared_talk_names():
    builder = PlanBuilder(llm_fn=None, registry_fn=None)
    for intent in ("chitchat.talk", "foo.talk"):
        assert builder._talk_only_plan(
            "机油灯亮了怎么办", [MockAgent("chitchat", [intent])]
        ) is None


def test_fallback_allows_declared_non_talk_and_scans_past_ineligible_entries():
    agent = MockAgent(
        "chitchat",
        ["chitchat.talk", "chitchat.confirmed", "chitchat.answer"],
        require_confirm=("chitchat.confirmed",),
    )
    _response(agent, "chitchat.confirmed")
    _response(agent, "chitchat.answer")
    plan = PlanBuilder(llm_fn=None, registry_fn=None)._talk_only_plan(
        "机油灯亮了怎么办", [agent]
    )
    assert [s.intent for s in plan.steps] == ["chitchat.answer"]
    assert getattr(plan.steps[0], "response_only", False) is True


def test_declared_response_still_has_to_survive_question_side_effect_guard():
    edge = _response(
        MockAgent(
            "chitchat",
            ["warning_light.close"],
            kind="edge_fast",
            deployment="edge",
        ),
        "warning_light.close",
    )
    assert PlanBuilder(llm_fn=None, registry_fn=None)._talk_only_plan(
        "机油灯亮了怎么办", [edge]
    ) is None


def test_total_block_downgrades_adaptive_but_mixed_plan_does_not():
    builder = PlanBuilder(llm_fn=None, registry_fn=None)
    fallback = _response(
        MockAgent("chitchat", ["chitchat.answer"]), "chitchat.answer"
    )
    total = Plan(
        steps=[_step("warning_light.close")],
        complexity="adaptive",
        raw_text="机油灯亮了怎么办",
    )
    total = builder._apply_question_side_effect_guard(
        total, total.raw_text, [fallback]
    )
    assert total.complexity == "simple"
    assert [s.intent for s in total.steps] == ["chitchat.answer"]

    mixed = Plan(
        steps=[
            _step("warning_light.close"),
            _step("manual.query", deployment="cloud", kind="agent"),
        ],
        complexity="adaptive",
        raw_text="机油灯亮了怎么办",
    )
    mixed = builder._apply_question_side_effect_guard(
        mixed, mixed.raw_text, [fallback]
    )
    assert mixed.complexity == "adaptive"
    assert [s.intent for s in mixed.steps] == ["manual.query"]


def test_response_only_round_trip_and_legacy_default_false():
    step = Step(id="s1", agent_id="chitchat", intent="chitchat.answer")
    step.response_only = True
    state = SessionState(
        phase="wait_slot",
        pending_plan=PlannerEngine._serialize_plan(Plan(steps=[step])),
        pending_step_id="s1",
    )
    restored, _ = PlannerEngine._restore(None, state, inject_confirmed=False)
    assert getattr(restored.steps[0], "response_only", False) is True

    legacy = SessionState(
        phase="wait_slot",
        pending_plan={
            "steps": [{
                "id": "s1", "agent_id": "legacy", "intent": "legacy.answer"
            }]
        },
        pending_step_id="s1",
    )
    restored, _ = PlannerEngine._restore(None, legacy, inject_confirmed=False)
    assert getattr(restored.steps[0], "response_only", False) is False


def test_focused_question_does_not_bypass_confirmed_fallback_guard(monkeypatch):
    order = MockAgent("mcp-bridge", ["luckin.order"],
                      require_confirm=("luckin.order",))
    order.score = 1.0
    registry_calls = []

    async def mock_llm(messages):
        return "unused"

    async def mock_resolve(query, top_k=1):
        registry_calls.append((query, top_k))
        return [order]

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    focused = Plan(
        steps=[_step("warning_light.close")],
        raw_text="红色机油灯亮了还能继续开吗",
        goal="focused guard regression",
    )
    monkeypatch.setattr(
        builder, "_focused_control_ellipsis_plan",
        lambda text, working_set, catalog: focused,
    )

    plan = asyncio.run(builder.build(
        "红色机油灯亮了还能继续开吗", WorkingSet(catalog=[order]),
        PlanContext(session_id="focused-confirmed-fallback"),
    ))

    assert plan.steps == []
    assert all(step.intent != "luckin.order" for step in plan.steps)
    assert "question_write_blocked" in (plan.plan_mode or "")
    assert registry_calls == []


# ── 7. 本闸的行为代价，**钉成可见断言**：礼貌请求会被答而不是被做 ─────────────

@pytest.mark.parametrize("text", [
    "把空调关了好吗？",
    "关一下空调好吗",
    "空调关一下行吗",
    "空调可以关了吗",
])
def test_polite_request_with_a_question_tail_is_also_blocked(text):
    """这一类**会被拦**——它是本闸的代价，不是遗漏，所以钉在这里而不是藏着。

    ## 现状与变化

    端侧**早就不执行**这一类（`classify_structured` 返回 None、整句上云，
    出口注释写的是「是提问，交云端如实作答」）；变的是云侧：此前 planner 会把它
    规划成 `hvac.off` 并执行，从今往后落到兜底 Agent 答一句。
    也就是说**端侧的既有裁定这次真正生效了**，不是新发明了一条判据。

    ## 为什么不给「好吗/行吗」开口子

    因为同一个口子会把 **SF3 第三轮**放回去：「慢一点开可以吗？」与
    「空调关一下行吗」逐字同构（礼貌尾 + 操作动词），而前者正是 QA 轮里被执行成
    `volume.dec`（「调小了」）的那一句——安全对话中途被一个无关车控劫持。
    **收窄面只写一边守不住**：放宽礼貌尾就等于把那个洞重新打开。

    真要救这一类，方向是**让被拦下来的写请求走一次澄清**（「您是想现在关，
    还是在问怎么关？」），而不是放宽问句判据——那是独立一笔，需要单独评审。

    ⚠ 与 C11 的交互也一并记在这里：落到 chitchat 之后**它可能编造「已为您关闭」**
    （findings N4 同族）。C11 修的就是这个，在第 5 批；在那之前这一类的下限是
    「答了一句可能不准的话」，而不是「执行了一个用户没下的指令」。
    """
    assert _GUARD([_step("hvac.off")], text)


def test_directive_marker_still_rescues_the_common_polite_form():
    """最常见的那种礼貌说法带「帮我」，仍然照做——闸不是把礼貌一起挡了。"""
    assert _GUARD([_step("hvac.off")], "帮我把空调关了好吗") == []
    assert _GUARD([_step("hvac.off")], "麻烦把空调关一下") == []


# ── 8. manual-rag v2：动作在前的方法问句也必须被拦 ─────────────────────────

def test_action_first_howto_question_is_blocked_without_changing_adjustment_command():
    """「怎么打开双闪」是方法问句；无标点也不能执行成 warning_light.open。

    v2 在共享 question_shape 中用词序收口：`怎么 + 打开 + 对象` 与
    `对象 + 怎么 + 打开` 是询问；`怎么把对象打开`、`温度如何调高` 仍按既有祈使合同。
    端侧与云侧继续复用同一实现，没有为 manual 另抄判据。
    """
    step = _step("warning_light.open")
    assert _GUARD([step], "怎么打开双闪") == [step]
    assert _GUARD([step], "温度如何调高") == []
