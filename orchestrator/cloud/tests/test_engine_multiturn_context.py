"""PlannerEngine multiturn context tests.

These tests use in-process fakes only. They verify the engine-level memory
contract around planner prompt context, session isolation, and memory opt-out.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from cockpit.agent.v1 import agent_pb2
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore


_PLAN_JSON = json.dumps({
    "complexity": "simple",
    "goal": "demo action",
    "steps": [
        {
            "id": "s1",
            "agent_id": "demo",
            "intent": "demo.do",
            "slots": {},
            "depends_on": [],
            "slot_refs": {},
        }
    ],
})
_FINAL_SPEECH = "Done with demo action."


class _Cap:
    def __init__(self, intent: str, slots: list[str]):
        self.intent = intent
        self.slots = slots
        self.description = intent


def _agent():
    manifest = SimpleNamespace(
        agent_id="demo",
        trust_level="first_party",
        latency_budget_ms=2000,
        requires_permissions=[],
        capabilities=[_Cap("demo.do", [])],
    )
    return SimpleNamespace(manifest=manifest, endpoint="stub:1")


class _Resp:
    def __init__(
        self,
        status: int = agent_pb2.ExecuteResponse.OK,
        speech: str = _FINAL_SPEECH,
        follow_up: str = "",
        missing_slots: list[str] | None = None,
    ):
        self.status = status
        self.speech = speech
        self.follow_up = follow_up
        self.actions = []
        self.ui_card = None
        self.data = None
        self.missing_slots = list(missing_slots or [])


class _MultiturnSpy:
    def __init__(
        self,
        histories: dict[str, list[dict]] | None = None,
        need_datetime_slot: bool = False,
    ):
        self.histories = {
            session_id: [dict(turn) for turn in turns]
            for session_id, turns in (histories or {}).items()
        }
        self.need_datetime_slot = need_datetime_slot
        self.read_calls: list[tuple[str, int]] = []
        self.append_calls: list[tuple[str, str, str]] = []
        self.append_calls_at_reads: list[list[tuple[str, str, str]]] = []
        self.planner_prompts: list[str] = []
        self.agent_calls: list[tuple[str, dict, dict]] = []

    async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None):
        raise RuntimeError("stream disabled")
        yield  # pragma: no cover

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None):
        slots_snapshot = dict(slots or {})
        self.agent_calls.append((intent, slots_snapshot, dict(meta or {})))
        if self.need_datetime_slot and not slots_snapshot.get("datetime"):
            return _Resp(
                status=agent_pb2.ExecuteResponse.NEED_SLOT,
                speech="What time should I use?",
                follow_up="Tell me a date and time.",
                missing_slots=["datetime"],
            )
        return _Resp()

    async def llm(self, messages, **kwargs):
        system = messages[0]["content"]
        if "simple|adaptive" in system:
            self.planner_prompts.append(messages[1]["content"])
            return _PLAN_JSON
        return _FINAL_SPEECH

    async def resolve(self, query="", intent="", top_k=1):
        return [_agent()]

    async def list_agents(self):
        return [_agent()]

    async def get_session(self, session_id, last_n=6):
        self.read_calls.append((session_id, last_n))
        self.append_calls_at_reads.append(list(self.append_calls))
        return [dict(turn) for turn in self.histories.get(session_id, [])[-last_n:]]

    async def append_turn(self, session_id, role, text):
        self.append_calls.append((session_id, role, text))
        self.histories.setdefault(session_id, []).append({"role": role, "text": text})


def _make_engine_with_session(spy: _MultiturnSpy) -> tuple[PlannerEngine, SessionStore]:
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session,
    )
    return engine, session


def _make_engine(spy: _MultiturnSpy) -> PlannerEngine:
    return _make_engine_with_session(spy)[0]


def _req(text: str, session_id: str = "session-a", meta: dict | None = None):
    return SimpleNamespace(
        text=text,
        session_id=session_id,
        request_id=f"req-{session_id}",
        is_confirmation=False,
        meta=meta or {},
        context=SimpleNamespace(user_id="user-1", vehicle_id="vehicle-1"),
    )


def _run(engine: PlannerEngine, request) -> list[dict]:
    async def collect():
        return [event async for event in engine.run(request)]

    return asyncio.run(collect())


def _final_event(events: list[dict]) -> dict:
    finals = [event for event in events if event.get("kind") == "final"]
    assert finals
    return finals[-1]


def test_same_session_history_reaches_planner_before_current_turn_is_appended():
    current_text = "Raise it a little"
    spy = _MultiturnSpy(histories={
        "session-a": [
            {"role": "user", "text": "Set passenger AC to 26"},
            {"role": "assistant", "text": "Passenger AC is set to 26."},
        ]
    })
    engine = _make_engine(spy)

    events = _run(engine, _req(current_text, session_id="session-a"))

    assert spy.read_calls == [("session-a", 6)]
    assert spy.append_calls_at_reads == [[]]
    prompt = spy.planner_prompts[0]
    assert "Set passenger AC to 26" in prompt
    assert "Passenger AC is set to 26." in prompt
    assert current_text in prompt
    assert prompt.count(current_text) == 1
    assert spy.append_calls == [
        ("session-a", "user", current_text),
        ("session-a", "assistant", _FINAL_SPEECH),
    ]
    assert _final_event(events)["speech"] == _FINAL_SPEECH


def test_session_histories_are_isolated_when_building_planner_prompt():
    histories = {
        "session-a": [
            {"role": "user", "text": "Find chargers near there"},
            {"role": "assistant", "text": "Showing chargers near your current route."},
        ],
        "session-b": [
            {"role": "user", "text": "Set passenger AC to 26"},
            {"role": "assistant", "text": "Passenger AC is set to 26."},
        ],
    }
    spy = _MultiturnSpy(histories=histories)
    engine = _make_engine(spy)

    events = _run(engine, _req("Which one is open now?", session_id="session-a"))

    assert spy.read_calls == [("session-a", 6)]
    prompt = spy.planner_prompts[0]
    assert "Find chargers near there" in prompt
    assert "Showing chargers near your current route." in prompt
    assert "Set passenger AC to 26" not in prompt
    assert "Passenger AC is set to 26." not in prompt
    assert spy.append_calls == [
        ("session-a", "user", "Which one is open now?"),
        ("session-a", "assistant", _FINAL_SPEECH),
    ]
    assert _final_event(events)["speech"] == _FINAL_SPEECH


def test_memory_disabled_skips_history_and_writes_but_still_returns_final_speech():
    spy = _MultiturnSpy(histories={
        "session-a": [
            {"role": "user", "text": "Set passenger AC to 26"},
            {"role": "assistant", "text": "Passenger AC is set to 26."},
        ]
    })
    engine = _make_engine(spy)

    events = _run(
        engine,
        _req(
            "Raise it a little",
            session_id="session-a",
            meta={"memory_enabled": "false"},
        ),
    )

    assert spy.read_calls == []
    assert spy.append_calls == []
    prompt = spy.planner_prompts[0]
    assert "Set passenger AC to 26" not in prompt
    assert "Raise it a little" in prompt
    assert _final_event(events)["speech"] == _FINAL_SPEECH


def test_need_slot_resume_reuses_pending_plan_and_fills_missing_slot():
    spy = _MultiturnSpy(need_datetime_slot=True)
    engine, session = _make_engine_with_session(spy)

    first_events = _run(engine, _req("Book dinner", session_id="slot-session"))

    first_final = _final_event(first_events)
    assert first_final["speech"] == "What time should I use?"
    assert first_final["follow_up"] == "Tell me a date and time."
    state = asyncio.run(session.load("slot-session"))
    assert state is not None
    assert state.phase == "wait_slot"
    assert state.pending_step_id == "s1"
    assert state.missing_slots == ["datetime"]
    assert len(spy.planner_prompts) == 1

    second_events = _run(engine, _req("Tonight at 7", session_id="slot-session"))

    assert len(spy.planner_prompts) == 1
    assert [call[0] for call in spy.agent_calls] == ["demo.do", "demo.do"]
    assert spy.agent_calls[0][1] == {}
    assert spy.agent_calls[1][1]["datetime"] == "Tonight at 7"
    assert asyncio.run(session.load("slot-session")) is None
    assert _final_event(second_events)["speech"] == _FINAL_SPEECH
