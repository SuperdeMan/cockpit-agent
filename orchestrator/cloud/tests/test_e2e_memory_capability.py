import asyncio
from types import SimpleNamespace

from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

import orchestrator.cloud.context as context_module
from orchestrator.cloud.clients import Clients
from orchestrator.cloud.context import ContextManager, build_context


def _request(capability: str):
    return orchestrator_pb2.HandleRequest(
        request_id="request-1",
        session_id="e2e-run-abc-e2e_journeys-session-1",
        text="remember this",
        context=common_pb2.ContextRef(
            user_id="e2e-run-abc-e2e_journeys",
            vehicle_id="vehicle-1",
        ),
        meta={"answer_length": "short"},
        e2e_memory_capability=capability,
    )


def test_capability_enters_plan_context_but_not_prefs_or_agent_meta():
    capability = "e2emem.v1.payload.signature"
    ctx = build_context(_request(capability))

    assert ctx.e2e_memory_capability == capability
    assert "e2e_memory_capability" not in ctx.prefs
    merged = Clients._merge_meta(ctx, {"confirmed": "true"})
    assert "e2e_memory_capability" not in merged


def test_context_and_client_forward_capability_only_to_memory_append():
    capability = "e2emem.v1.payload.signature"

    class MemoryStub:
        def __init__(self):
            self.request = None

        async def AppendTurn(self, request, timeout):
            del timeout
            self.request = request
            return SimpleNamespace(ok=True)

    stub = MemoryStub()
    clients = Clients()
    clients._memory_stub = lambda: stub
    manager = ContextManager(clients)

    asyncio.run(
        manager.append_turn(
            "e2e-run-abc-e2e_journeys-session-1",
            "user",
            "remember this",
            user_id="e2e-run-abc-e2e_journeys",
            vehicle_id="vehicle-1",
            occupant_id="primary",
            e2e_memory_capability=capability,
        ),
    )

    assert stub.request.e2e_memory_capability == capability
    assert stub.request.session_id == "e2e-run-abc-e2e_journeys-session-1"


def test_callee_internal_type_error_is_not_retried_or_downgraded():
    capability = "e2emem.v1.payload.signature"

    class ClientsWithInternalFailure:
        def __init__(self):
            self.calls = []

        async def append_turn(
            self,
            session_id,
            role,
            text,
            user_id="",
            vehicle_id="",
            occupant_id="",
            e2e_memory_capability="",
        ):
            self.calls.append({
                "session_id": session_id,
                "role": role,
                "text": text,
                "user_id": user_id,
                "vehicle_id": vehicle_id,
                "occupant_id": occupant_id,
                "e2e_memory_capability": e2e_memory_capability,
            })
            raise TypeError("callee implementation failed")

    clients = ClientsWithInternalFailure()
    manager = ContextManager(clients)
    asyncio.run(
        manager.append_turn(
            "plain-session",
            "user",
            "remember this",
            user_id="user-1",
            vehicle_id="vehicle-1",
            occupant_id="occ-2",
            e2e_memory_capability=capability,
        ),
    )

    assert clients.calls == [{
        "session_id": "plain-session",
        "role": "user",
        "text": "remember this",
        "user_id": "user-1",
        "vehicle_id": "vehicle-1",
        "occupant_id": "occ-2",
        "e2e_memory_capability": capability,
    }]


def test_legacy_append_turn_signatures_are_adapted_before_single_call():
    calls = []

    class LegacyClients:
        async def append_three(self, session_id, role, text):
            calls.append(("three", session_id, role, text))

        async def append_five(
            self,
            session_id,
            role,
            text,
            user_id="",
            vehicle_id="",
        ):
            calls.append(("five", session_id, role, text, user_id, vehicle_id))

        async def append_six(
            self,
            session_id,
            role,
            text,
            user_id="",
            vehicle_id="",
            occupant_id="",
        ):
            calls.append((
                "six",
                session_id,
                role,
                text,
                user_id,
                vehicle_id,
                occupant_id,
            ))

    clients = LegacyClients()
    manager = ContextManager(clients)
    for name in ("append_three", "append_five", "append_six"):
        clients.append_turn = getattr(clients, name)
        asyncio.run(
            manager.append_turn(
                "plain-session",
                "user",
                "remember this",
                user_id="user-1",
                vehicle_id="vehicle-1",
                occupant_id="occ-2",
                e2e_memory_capability="e2emem.v1.payload.signature",
            ),
        )

    assert calls == [
        ("three", "plain-session", "user", "remember this"),
        (
            "five",
            "plain-session",
            "user",
            "remember this",
            "user-1",
            "vehicle-1",
        ),
        (
            "six",
            "plain-session",
            "user",
            "remember this",
            "user-1",
            "vehicle-1",
            "occ-2",
        ),
    ]


def test_signature_inspection_failure_calls_once_without_stripping_capability(
    monkeypatch,
):
    capability = "e2emem.v1.payload.signature"
    calls = []

    class OpaqueClients:
        async def append_turn(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(
        context_module.inspect,
        "signature",
        lambda _fn: (_ for _ in ()).throw(ValueError("opaque callable")),
    )
    manager = ContextManager(OpaqueClients())
    asyncio.run(
        manager.append_turn(
            "plain-session",
            "user",
            "remember this",
            user_id="user-1",
            vehicle_id="vehicle-1",
            occupant_id="occ-2",
            e2e_memory_capability=capability,
        ),
    )

    assert len(calls) == 1
    assert calls[0][1]["e2e_memory_capability"] == capability
