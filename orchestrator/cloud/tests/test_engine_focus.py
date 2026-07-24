"""引擎级焦点态集成：控制轮后焦点持久，下一轮指代规划 prompt 注入焦点块。

进程内 stub（_Spy 模式，参考 test_multi_intent.py），不依赖 gRPC。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.session import SessionStore


@pytest.fixture(autouse=True)
def _skills_off(monkeypatch):
    """本文件测焦点机制：假 LLM 按 prompt 关键词出计划——skill 注入块的 policy 文本
    （含「空调」「最近对话」字样）会误触发关键词匹配，钉 off 隔离（M0b Full Migration）。"""
    monkeypatch.setenv("SKILLS_MODE", "off")


def _agent(agent_id, intents):
    caps = [SimpleNamespace(intent=i, slots=[], description=i) for i in intents]
    manifest = SimpleNamespace(
        agent_id=agent_id, trust_level="oem", latency_budget_ms=2000,
        requires_permissions=[], capabilities=caps, kind="agent", deployment="cloud")
    return SimpleNamespace(manifest=manifest, endpoint=f"{agent_id}:50000")


_AGENTS = [_agent("hvac", ["hvac.set", "hvac.inc"])]


class _Spy:
    def __init__(self):
        self.planner_prompts: list[str] = []

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        return SimpleNamespace(status=0, speech="空调已设置为26度", follow_up="",
                               actions=[], ui_card=None, data=None, missing_slots=[])

    async def llm(self, messages, **kwargs):
        system = messages[0]["content"]
        user = messages[1]["content"] if len(messages) > 1 else ""
        if "任务编排器" in system:
            self.planner_prompts.append(user)
            if "调高" in user or "再" in user:
                return json.dumps({"steps": [
                    {"id": "s1", "agent_id": "hvac", "intent": "hvac.inc",
                     "slots": {}, "depends_on": [], "slot_refs": {}}]})
            return json.dumps({"steps": [
                {"id": "s1", "agent_id": "hvac", "intent": "hvac.set",
                 "slots": {"temperature": "26", "position": "副驾"},
                 "depends_on": [], "slot_refs": {}}]})
        return "好的，已完成。"

    async def resolve(self, query="", intent="", top_k=1):
        return _AGENTS

    async def list_agents(self):
        return _AGENTS


def _engine():
    spy = _Spy()
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session,
    )
    return engine, spy, session


def _req(text, session_id="focus-s"):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="r",
        is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"))


def _run(engine, req):
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


def test_control_turn_persists_focus():
    engine, spy, session = _engine()
    _run(engine, _req("把副驾空调调到26度"))
    focus = asyncio.run(session.load_focus("focus-s"))
    assert focus is not None
    assert focus["obj"] == "空调"
    assert "副驾" in focus["positions"]
    assert focus["attr"] == "温度"


def test_followup_turn_injects_focus_into_planner_prompt():
    engine, spy, session = _engine()
    _run(engine, _req("把副驾空调调到26度"))   # 第一轮：建立焦点
    _run(engine, _req("再调高一点"))            # 第二轮：指代
    # 第一轮 prompt 无焦点（首轮），第二轮 prompt 应带焦点块
    assert "当前对话焦点" not in spy.planner_prompts[0]
    assert any("当前对话焦点" in p and "空调" in p and "副驾" in p
               for p in spy.planner_prompts[1:])


def test_focus_disabled_when_memory_off():
    engine, spy, session = _engine()

    def _req_mem_off(text):
        r = _req(text)
        r.meta = {"memory_enabled": "false"}
        return r

    _run(engine, _req_mem_off("把副驾空调调到26度"))
    # memory_enabled=false → 不读写焦点/历史
    assert asyncio.run(session.load_focus("focus-s")) is None
