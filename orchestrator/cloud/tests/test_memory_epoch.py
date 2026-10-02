"""CA2-15 S1b: a cloud turn reads the owner memory epoch before any memory read and carries it.

The turn's two AppendTurn calls happen after the orchestration loop (seconds to tens of
seconds later) and agents write memory while it runs; a deletion in between must make
Memory refuse those writes, so every write carries the epoch observed at the start.
"""
import asyncio
import json
from types import SimpleNamespace

from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.clients import Clients
from orchestrator.cloud.context import ContextManager, build_context
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore
from runtime import memory_read as mr

_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": "cap_0001", "slots": {}, "depends_on": [], "slot_refs": {}},
]})


class _Cap:
    def __init__(self, intent):
        self.intent, self.slots, self.description = intent, [], intent
        self.heavy = False
        self.examples = []
        self.response_only = True


def _agents():
    return [SimpleNamespace(manifest=SimpleNamespace(
        agent_id="chitchat", trust_level="internal", latency_budget_ms=15000,
        deployment="cloud", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("chitchat.talk")], route_hints=[],
    ), endpoint="stub:50060")]


class _Resp:
    def __init__(self, speech=""):
        self.status, self.speech, self.follow_up = 0, speech, ""
        self.actions, self.ui_card, self.missing_slots, self.data = [], None, [], None


class _Spy:
    def __init__(self, epoch="1.ab"):
        self.epoch = epoch
        self.log: list[str] = []
        self.appended: list[tuple[str, str]] = []

    async def get_memory_epoch(self, user_id):
        self.log.append("epoch")
        return self.epoch

    async def get_session_read(self, session_id, last_n=6, *, user_id="", occupant_id=""):
        self.log.append("history")
        return [], mr.NONE

    async def recall_read(self, user_id, query="", **kw):
        self.log.append("recall")
        return [], mr.NONE

    async def append_turn(self, session_id, role, text, **kw):
        self.appended.append((role, kw.get("memory_epoch", "")))

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None):
        return _Resp("在呢")

    async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None, context_scopes=None):
        yield ("final", _Resp("在呢"))

    async def llm(self, messages, **kwargs):
        return _PLAN if "任务编排器" in messages[0]["content"] else "在呢"

    async def resolve(self, query="", intent="", top_k=1):
        return _agents()

    async def list_agents(self):
        return _agents()


def _run(spy, prefs=None):
    engine = PlannerEngine(
        clients=spy, planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent), aggregator=Aggregator(llm_fn=spy.llm),
        session=SessionStore(redis_url=""))
    request = SimpleNamespace(
        text="你好", session_id="s-epoch", request_id="r-epoch", is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"), meta=dict(prefs or {}))

    async def collect():
        return [e async for e in engine.run(request)]

    return asyncio.run(collect())


def test_the_epoch_is_read_before_any_memory_read_and_every_turn_write_carries_it():
    spy = _Spy()
    events = _run(spy)
    assert events and spy.log and spy.log[0] == "epoch" and spy.log.count("epoch") == 1
    assert spy.appended == [("user", "1.ab"), ("assistant", "1.ab")]


def test_a_memory_off_turn_neither_reads_an_epoch_nor_writes():
    spy = _Spy()
    _run(spy, prefs={"memory_enabled": "false"})
    assert "epoch" not in spy.log and spy.appended == []


def _ctx(epoch="", meta=None):
    ctx = build_context(orchestrator_pb2.HandleRequest(
        request_id="r1", session_id="s1", text="t",
        context=common_pb2.ContextRef(user_id="u1", vehicle_id="v1"), meta=meta or {}))
    ctx.memory_epoch = epoch
    return ctx


def test_agents_get_the_server_epoch_and_clients_cannot_supply_one():
    forged = {"memory_epoch": "9.forged"}
    assert Clients._merge_meta(_ctx("1.ab", forged), dict(forged))["memory_epoch"] == "1.ab"
    assert "memory_epoch" not in Clients._merge_meta(_ctx("", forged), dict(forged))
    assert build_context(orchestrator_pb2.HandleRequest(
        request_id="r1", session_id="s1", text="t", meta=forged)).memory_epoch == ""


def test_clients_put_the_epoch_on_append_turn_and_read_it_from_memory():
    class _Memory:
        def __init__(self):
            self.appended = None

        async def AppendTurn(self, request, timeout):
            self.appended = request
            return SimpleNamespace(ok=True)

        async def GetMemoryEpoch(self, request, timeout):
            return SimpleNamespace(memory_epoch=f"2.{request.user_id}")

    stub = _Memory()
    clients = Clients()
    clients._memory_stub = lambda: stub
    manager = ContextManager(clients)

    async def go():
        epoch = await manager.memory_epoch("u1")
        await manager.append_turn("s1", "user", "t", user_id="u1", vehicle_id="v1",
                                  occupant_id="primary", memory_epoch=epoch)
        return epoch

    assert asyncio.run(go()) == "2.u1" and stub.appended.memory_epoch == "2.u1"


def test_an_unreachable_memory_gives_no_epoch_instead_of_failing_the_turn():
    class _Down:
        async def get_memory_epoch(self, user_id):
            raise RuntimeError("memory down")

    assert asyncio.run(ContextManager(_Down()).memory_epoch("u1")) == ""
    assert asyncio.run(ContextManager(SimpleNamespace()).memory_epoch("u1")) == ""
