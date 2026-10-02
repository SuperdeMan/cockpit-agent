"""CA2-15 S2: the cloud takes the speaker only from a voice proof, and projects reads when it is unknown.

Decisions (2026-10-02/03): an unrecognized voice reads only ordinary preferences (A); only the car
HMI's hands-free voice can be "unrecognized" (typed, push-to-talk and the phone count as the owner);
an unrecognized speaker's words stay in history but are not extracted.
"""
import asyncio
from types import SimpleNamespace

import pytest

from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

from orchestrator.cloud.clients import Clients
from orchestrator.cloud.context import build_context
from runtime import memory_projection as mp
from runtime import voice_attestation as va

KEY = va.derive_key(b"c" * 64)


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(va, "runtime_key", lambda: KEY)


def _ctx(meta, user="u1"):
    return build_context(orchestrator_pb2.HandleRequest(
        request_id="r1", session_id="s1", text="t", meta=meta,
        context=common_pb2.ContextRef(user_id=user, vehicle_id="v1")))


def test_a_valid_proof_names_the_occupant_and_bare_claims_name_nobody():
    proof = va.issue(KEY, user_id="u1", occupant_id="occ-2", display_name="小雨")
    ctx = _ctx({va.META: proof, "occupant_id": "occ-9", "occupant_name": "冒名"})
    assert (ctx.occupant_id, ctx.prefs.get("occupant_name"), ctx.prefs["occupant_id"]) == ("occ-2", "小雨", "occ-2")
    bare = _ctx({"occupant_id": "occ-2", "occupant_name": "小雨"})
    assert (bare.occupant_id, bare.prefs.get("occupant_name")) == ("primary", None)


_BAD_PROOFS = {
    # Built inside the test: tokens carry the issue time, and xdist workers must collect
    # identical test IDs.
    "another_owner": lambda: va.issue(KEY, user_id="u2", occupant_id="occ-2"),
    "not_our_key": lambda: va.issue(va.derive_key(b"x" * 64), user_id="u1", occupant_id="occ-2"),
    "long_expired": lambda: va.issue(KEY, user_id="u1", occupant_id="occ-2", now=1_000_000),
    "garbage": lambda: "voice.v1.garbage.sig",
}


@pytest.mark.parametrize("case", sorted(_BAD_PROOFS))
def test_a_proof_that_does_not_hold_falls_back_to_primary(case):
    ctx = _ctx({va.META: _BAD_PROOFS[case]()})
    assert ctx.occupant_id == "primary" and ctx.memory_projection == mp.NONE


def test_an_unrecognized_hands_free_voice_reads_only_ordinary_preferences():
    ctx = _ctx({va.IDENTITY: va.UNRECOGNIZED, "input_source": "voice_wake"})
    assert (ctx.occupant_id, ctx.memory_projection, ctx.speaker_unverified) == ("primary", mp.NORMAL_ONLY, True)
    proven = _ctx({va.IDENTITY: va.UNRECOGNIZED, va.META: va.issue(KEY, user_id="u1", occupant_id="occ-2")})
    assert (proven.memory_projection, proven.speaker_unverified) == (mp.NONE, False)   # proof wins


@pytest.mark.parametrize("meta", [{}, {"input_source": "ptt"}, {"input_source": "voice_wake"}])
def test_typed_push_to_talk_and_the_phone_count_as_the_owner(meta):
    # Only the car HMI declares voice_identity; nothing else is projected.
    ctx = _ctx(meta)
    assert (ctx.occupant_id, ctx.memory_projection, ctx.speaker_unverified) == ("primary", mp.NONE, False)


def test_agents_get_the_server_projection_and_clients_cannot_supply_one():
    forged = {"memory_projection": "", "voice_identity": va.UNRECOGNIZED}
    ctx = _ctx(dict(forged))
    assert Clients._merge_meta(ctx, {"memory_projection": ""})["memory_projection"] == mp.NORMAL_ONLY
    owner = _ctx({"memory_projection": mp.NORMAL_ONLY})          # a client cannot project itself either
    assert owner.memory_projection == mp.NONE
    assert "memory_projection" not in Clients._merge_meta(owner, {"memory_projection": mp.NORMAL_ONLY})


def test_recall_and_turn_writes_carry_the_projection_and_the_flag():
    from orchestrator.cloud.context import ContextManager

    seen = {}

    class _Clients:
        async def recall_read(self, user_id, query="", **kw):
            seen["recall"] = kw.get("projection", "")
            return [], "none"

        async def append_turn(self, session_id, role, text, **kw):
            seen.setdefault("append", []).append(kw.get("speaker_unverified"))

    manager = ContextManager(_Clients())
    ctx = _ctx({va.IDENTITY: va.UNRECOGNIZED})
    ctx.granted_permissions = ["profile.read"]

    async def go():
        await manager._recall("我家在哪", ctx)
        await manager.append_turn("s1", "user", "我家在哪", user_id="u1", vehicle_id="v1",
                                  occupant_id="primary", speaker_unverified=ctx.speaker_unverified)

    asyncio.run(go())
    assert seen == {"recall": mp.NORMAL_ONLY, "append": [True]}


def test_the_client_puts_projection_and_flag_on_the_wire():
    class _Memory:
        def __init__(self):
            self.recall = self.append = None

        async def Recall(self, request, timeout):
            self.recall = request
            return SimpleNamespace(items=[], degraded=False)

        async def AppendTurn(self, request, timeout):
            self.append = request
            return SimpleNamespace(ok=True)

    stub = _Memory()
    clients = Clients()
    clients._memory_stub = lambda: stub

    async def go():
        await clients.recall_read("u1", "q", projection=mp.NORMAL_ONLY)
        await clients.append_turn("s1", "user", "t", user_id="u1", speaker_unverified=True)

    asyncio.run(go())
    assert stub.recall.projection == mp.NORMAL_ONLY and stub.append.speaker_unverified is True
