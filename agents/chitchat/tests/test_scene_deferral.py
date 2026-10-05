"""规划把场景定义问退到闲聊时，交给场景编排（docs/design/2026-10-05-scene-describe.md §6）。

`4e3ffb14` 上 V207「露营模式是什么意思？」5 遍里 1 遍规划器判无需动作、退到闲聊，闲聊凭常识编了一个露营模式。
闲聊先问场景编排「这是不是在问一个已知场景」，认领了就零播报改派 `scene.describe`；与问手册并发，场景优先。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agents._sdk.testing import make_context, run_handle, run_handle_stream
from agents.chitchat.src.agent import ChitchatAgent

USES_SCENE_CLAIM = True
USES_MANUAL_CLAIM = True
_ESCALATE = {"intent": "scene.describe", "slots": {"scene": "露营模式"}, "reason": "scene_definition"}


class _Agents:
    """按 Agent 回答认领：场景编排给场景名，手册给有没有把握。"""

    def __init__(self, scene="露营模式", manual=False):
        self.calls, self.scene, self.manual = [], scene, manual

    async def call(self, agent_id, intent, slots, ctx=None, timeout=None):
        self.calls.append((agent_id, intent))
        if agent_id == "scene-orchestrator":
            return SimpleNamespace(status="ok", data={"confident": bool(self.scene), "scene": self.scene})
        return SimpleNamespace(status="ok", data={"confident": self.manual})


def _agent(monkeypatch, agents):
    monkeypatch.setattr(ChitchatAgent, "agents", property(lambda self: agents))
    agent = ChitchatAgent()
    agent.llm.complete = AsyncMock(return_value="闲聊的回答")
    return agent


def test_a_question_about_a_known_scene_goes_to_the_scene(monkeypatch):
    agents = _Agents(scene="露营模式", manual=True)          # 手册也有把握：场景优先
    agent = _agent(monkeypatch, agents)
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="露营模式是什么意思？", ctx=make_context()))
    assert res.speech == "" and res.data["_escalate"] == _ESCALATE
    agent.llm.complete.assert_not_awaited()


def test_the_stream_path_defers_to_the_scene_without_a_word(monkeypatch):
    agent = _agent(monkeypatch, _Agents())
    events = asyncio.run(run_handle_stream(agent, "chitchat.talk", raw_text="露营模式是什么意思？", ctx=make_context()))
    assert [kind for kind, _ in events] == ["final"] and events[-1][1].data["_escalate"] == _ESCALATE


def test_an_unclaimed_question_still_asks_the_manual(monkeypatch):
    agents = _Agents(scene="", manual=True)
    agent = _agent(monkeypatch, agents)
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="空调温度怎么调？", ctx=make_context()))
    assert res.data["_escalate"]["intent"] == "manual.query"
    assert ("scene-orchestrator", "scene.claim") in agents.calls and ("manual-rag", "manual.claim") in agents.calls


def test_directives_never_ask_the_scene(monkeypatch):
    agents = _Agents()
    agent = _agent(monkeypatch, agents)
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="打开露营模式", ctx=make_context()))
    assert "_escalate" not in (res.data or {}) and agents.calls == []


def test_the_real_internal_client_carries_the_scene_claim(monkeypatch):
    """不替换内部调用客户端：只换 gRPC 桩，认领结果要穿过真实的 AgentClient 转换才算数。"""
    from pathlib import Path
    from unittest.mock import MagicMock, patch
    from cockpit.agent.v1 import agent_pb2
    from agents._sdk.manifest import load_manifest
    from agents._sdk.server import _to_struct
    monkeypatch.setenv("SCENE_ORCHESTRATOR_ENDPOINT", "stub:1")
    scene = load_manifest(str(Path(__file__).resolve().parents[2] / "scene_orchestrator" / "manifest.yaml"))
    agent = ChitchatAgent()
    agent.llm.complete = AsyncMock(return_value="闲聊的回答")
    stub = MagicMock()
    stub.Describe = AsyncMock(return_value=scene)
    stub.Execute = AsyncMock(return_value=agent_pb2.ExecuteResponse(
        status=agent_pb2.ExecuteResponse.OK, data=_to_struct({"confident": True, "scene": "露营模式"})))
    with patch("agents._sdk.agent_client.aio_channel", return_value=MagicMock()),             patch("agents._sdk.agent_client.agent_pb2_grpc.AgentStub", return_value=stub):
        res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="露营模式是什么意思？", ctx=make_context()))
    assert res.data["_escalate"] == _ESCALATE
    names = [call.args[0].intent.name for call in stub.Execute.await_args_list]
    assert "scene.claim" in names
