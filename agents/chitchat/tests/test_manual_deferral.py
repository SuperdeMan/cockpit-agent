"""闲聊兜底遇到车辆功能问句时交给有把握的手册（docs/design/2026-10-04-chitchat-defers-to-confident-manual.md）。

固定语料：规划把「空调温度怎么调？」「空调有哪些模式？」退到闲聊时，用户听到的是「直接说『把空调调到24度』就行」
「风量通常分 1 到 7 档」这类泛泛的话，甚至被建议「看下车辆说明书」——系统手里就有说明书。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agents._sdk.testing import make_context, run_handle, run_handle_stream
from agents.chitchat.src.agent import ChitchatAgent

USES_MANUAL_CLAIM = True
_ESCALATE = {"intent": "manual.query", "slots": {"question": "空调温度怎么调？"}, "reason": "manual_confident"}


class _Agents:
    def __init__(self, confident=True, error=None):
        self.calls, self.confident, self.error = [], confident, error

    async def call(self, agent_id, intent, slots, ctx=None, timeout=None):
        self.calls.append((agent_id, intent, dict(slots), timeout))
        if self.error:
            raise self.error
        return SimpleNamespace(status="ok", data={"confident": self.confident})


def _agent(monkeypatch, agents):
    monkeypatch.setattr(ChitchatAgent, "agents", property(lambda self: agents))
    agent = ChitchatAgent()
    agent.llm.complete = AsyncMock(return_value="闲聊的回答")
    return agent


def test_a_vehicle_question_the_manual_is_sure_of_goes_to_the_manual(monkeypatch):
    agents = _Agents(confident=True)
    agent = _agent(monkeypatch, agents)
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="空调温度怎么调？", ctx=make_context()))
    assert res.speech == "" and res.data["_escalate"] == _ESCALATE
    assert agents.calls == [("manual-rag", "manual.claim", {"question": "空调温度怎么调？"}, 1.5)]
    agent.llm.complete.assert_not_awaited()


def test_the_stream_path_defers_without_streaming_a_word(monkeypatch):
    """改派是零播报：流出任何一个字，编排就不再认这次改派。"""
    agent = _agent(monkeypatch, _Agents(confident=True))
    events = asyncio.run(run_handle_stream(agent, "chitchat.talk", raw_text="空调温度怎么调？", ctx=make_context()))
    assert [kind for kind, _ in events] == ["final"]
    assert events[-1][1].data["_escalate"] == _ESCALATE


def test_not_confident_or_unreachable_manual_keeps_chitchat(monkeypatch):
    for agents in (_Agents(confident=False), _Agents(error=RuntimeError("unreachable"))):
        agent = _agent(monkeypatch, agents)
        res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="黑洞是什么？", ctx=make_context()))
        assert res.speech == "闲聊的回答" and "_escalate" not in (res.data or {})
        assert len(agents.calls) == 1


def test_directives_and_memory_recall_never_ask_the_manual(monkeypatch):
    """指令句闲聊不接管执行；个人记忆的回忆问句是闲聊记忆路径的事。"""
    agents = _Agents(confident=True)
    agent = _agent(monkeypatch, agents)
    for text in ("把空调调到24度", "你还记得我喜欢把空调开到多少度吗？"):
        res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text=text, ctx=make_context()))
        assert "_escalate" not in (res.data or {})
    assert agents.calls == []


def test_the_real_internal_client_carries_the_manuals_verdict(monkeypatch):
    """不替换内部调用客户端：只把 gRPC 桩换掉，判定要穿过真实的 AgentClient 转换才算数。
    （上面几条换掉了整个客户端——真栈上转换把 data 丢了，它们照样是绿的。）"""
    from unittest.mock import MagicMock, patch
    from cockpit.agent.v1 import agent_pb2
    from agents._sdk.manifest import load_manifest
    from agents._sdk.server import _to_struct
    from pathlib import Path
    monkeypatch.setenv("MANUAL_RAG_ENDPOINT", "stub:1")
    manual = load_manifest(str(Path(__file__).resolve().parents[2] / "manual_rag" / "manifest.yaml"))
    agent = ChitchatAgent()
    agent.llm.complete = AsyncMock(return_value="闲聊的回答")
    stub = MagicMock()
    stub.Describe = AsyncMock(return_value=manual)
    stub.Execute = AsyncMock(return_value=agent_pb2.ExecuteResponse(
        status=agent_pb2.ExecuteResponse.OK, data=_to_struct({"confident": True})))
    with patch("agents._sdk.agent_client.aio_channel", return_value=MagicMock()), \
            patch("agents._sdk.agent_client.agent_pb2_grpc.AgentStub", return_value=stub):
        res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="空调温度怎么调？", ctx=make_context()))
    assert res.data["_escalate"] == _ESCALATE
    request = stub.Execute.await_args.args[0]
    assert request.intent.name == "manual.claim" and dict(request.intent.slots) == {"question": "空调温度怎么调？"}

