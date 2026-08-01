"""SDK 侧的 owner 透传（M-B）。

每个 Agent 从 M4 P4 起就持有 `ctx.occupant_id`——缺的一直是把它传下去这一步。
`Context.fetch/history/save_profile` 不带 owner 时，profile.* 的读写恒落 primary，
于是「常去地点各自独立」在代码里从来没成立过。这里钉死透传本身。
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from _sdk.base import Context  # noqa: E402


class _RecordingMemory:
    def __init__(self):
        self.calls: list[tuple] = []

    async def get_context(self, session_id, user_id, vehicle_id, scopes,
                          occupant_id=""):
        self.calls.append(("get_context", occupant_id))
        return {}

    async def get_session(self, session_id, last_n=6, *, user_id="", occupant_id=""):
        self.calls.append(("get_session", occupant_id))
        return []

    async def upsert_profile(self, user_id, key, value_json, occupant_id=""):
        self.calls.append(("upsert_profile", occupant_id))
        return True


def _ctx(occ="occ-2", mem=None):
    return Context("s1", "u1", "v1", mem or _RecordingMemory(), occ)


def test_fetch_history_and_save_profile_all_carry_the_owner():
    mem = _RecordingMemory()
    ctx = _ctx("occ-2", mem)

    async def go():
        await ctx.fetch("profile.places")
        await ctx.history(4)
        await ctx.save_profile("places", {"home": {"name": "翠竹苑"}})

    asyncio.run(go())
    assert mem.calls == [
        ("get_context", "occ-2"),
        ("get_session", "occ-2"),
        ("upsert_profile", "occ-2"),
    ]


def test_missing_occupant_normalizes_to_primary():
    mem = _RecordingMemory()
    ctx = Context("s1", "u1", "v1", mem, "")

    async def go():
        await ctx.fetch("profile.places")

    asyncio.run(go())
    assert mem.calls == [("get_context", "primary")]


def test_save_profile_without_user_id_writes_nothing():
    """无 user_id 时静默跳过——没有 user 就没有 OwnerKey，写下去只会是无主数据。"""
    mem = _RecordingMemory()
    ctx = Context("s1", "", "v1", mem, "occ-2")
    assert asyncio.run(ctx.save_profile("places", {})) is False
    assert mem.calls == []
