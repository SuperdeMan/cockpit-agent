"""省略主语的追问不许把上一轮的只回答能力丢成更差的计划（2026-10-16，docs/design/2026-10-16-f01-manual-follow-ups.md §3.2）。

三次 200×5 里 M09「能量回收有几档」→「怎么调」8 遍：落闲聊 3、零步 1、写意图 `energy_recovery.set` 1；派对的 3 遍又被检索丢了
补全（手册那一半在 `agents/manual_rag/tests/test_toc_routing.py`）。判据只看结构：上一轮是非兜底 Agent 的只回答能力（manifest
`response_only`），这句整句省略了主语（`runtime.anaphora.has_elided_subject`）。
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.cloud.context import Focus, WorkingSet
from orchestrator.cloud.models import Plan, PlanContext, Step
from orchestrator.cloud.planning import PlanBuilder, _assemble_capability_catalog, _elided_follow_up_conflicts
from orchestrator.cloud.tests.test_retry_policy import _MockAgent, _Spy, _args, _no_resolve, _step, _tool


def _agents():
    """cap_0001 chitchat.talk（兜底、只回答）/ 0002 info.search（只回答）/ 0003 manual.query（只回答）/
    0004 energy_recovery.set（会执行）——按 (agent_id, intent) 排序编号。"""
    chat = _MockAgent("chitchat", ["chitchat.talk"])
    info = _MockAgent("info", ["info.search"])
    manual = _MockAgent("manual-rag", ["manual.query"])
    energy = _MockAgent("vehicle", ["energy_recovery.set"])
    for agent, only in ((chat, True), (info, True), (manual, True), (energy, False)):
        for cap in agent.manifest.capabilities:
            cap.response_only = only
    return [chat, info, manual, energy]


_AFTER_MANUAL = Focus(last_intent="manual.query", last_agent_id="manual-rag")


def _plan(*steps, addressed=True, clarify=None):
    return Plan(steps=list(steps), addressed=addressed, clarify=clarify)


def _s(agent_id, intent, only):
    return Step(id="s1", agent_id=agent_id, intent=intent, response_only=only)


def _conflicts(text, focus, plan):
    return _elided_follow_up_conflicts(text, WorkingSet(catalog=_agents(), focus=focus), plan,
                                       _assemble_capability_catalog(_agents()))


@pytest.mark.parametrize("plan", [
    None,
    _plan(),                                                   # 零步
    _plan(addressed=False),                                    # 判不受话
    _plan(clarify={"question": "要调什么？", "options": []}),   # 要澄清
    _plan(_s("chitchat", "chitchat.talk", True)),              # 全交兜底谈话
    _plan(_s("vehicle", "energy_recovery.set", False)),        # 推成了会执行的步骤
])
def test_an_elided_follow_up_must_not_abandon_the_answer_capability(plan):
    assert _conflicts("怎么调", _AFTER_MANUAL, plan)


@pytest.mark.parametrize("plan", [
    _plan(_s("manual-rag", "manual.query", True)),
    _plan(_s("info", "info.search", True)),                    # 改派到别的只回答能力：不算冲突
])
def test_staying_on_an_answer_capability_is_no_conflict(plan):
    assert not _conflicts("怎么调", _AFTER_MANUAL, plan)


@pytest.mark.parametrize("text,focus", [
    ("怎么调", Focus(last_intent="chitchat.talk", last_agent_id="chitchat")),          # 上一轮是兜底谈话
    ("怎么调", Focus(last_intent="energy_recovery.set", last_agent_id="vehicle")),     # 上一轮是车控：交给规划器与方法问句闸
    ("怎么调", Focus(last_intent="manual.query", last_agent_id="gone")),               # 这轮目录里没有那个 Agent
    ("怎么调", Focus()),
    ("能量回收怎么调", _AFTER_MANUAL),                                                  # 带主语：规划器自己认
])
def test_only_an_elided_question_after_a_non_fallback_answer_capability(text, focus):
    assert not _conflicts(text, focus, _plan(_s("chitchat", "chitchat.talk", True)))


@pytest.fixture
def _planner_env(monkeypatch):
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    monkeypatch.setenv("PLANNER_TOOLCALL_SALVAGE_RETRY", "on")
    monkeypatch.setenv("PLANNER_RETRY_DISABLE", "")
    monkeypatch.setenv("SKILLS_MODE", "off")
    monkeypatch.setenv("EXEMPLARS_MODE", "off")


def test_the_planner_is_asked_again_and_the_follow_up_stays_on_the_manual(_planner_env):
    """第一版落闲聊（M09 实测形态）⇒ 按 `focus_dependent_conflict` 重问一次，第二版留在手册。"""
    spy = _Spy(tool_replies=[_tool(_args(goal="闲聊", steps=[_step("cap_0001")])),
                             _tool(_args(goal="能量回收怎么调", steps=[_step("cap_0003")]))])
    builder = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve, llm_tool_fn=spy.llm_tools)
    # 授权同重试矩阵：焦点经权限化视图（CA2-07）才进规划，没有授权时判据看到的是空焦点
    ctx = PlanContext(session_id="elided-follow-up", user_id="owner",
                      granted_permissions=["profile.read", "location.read"])
    plan = asyncio.run(builder.build("怎么调", WorkingSet(catalog=_agents(), focus=_AFTER_MANUAL), ctx))

    assert list(plan.retry_policies) == ["focus_dependent_conflict"]
    assert [step.intent for step in plan.steps] == ["manual.query"]
    assert spy.tool_calls == 2
