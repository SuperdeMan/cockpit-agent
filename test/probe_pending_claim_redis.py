"""CA2-09 offline probe: the Redis path of SessionStore.claim_result.

pytest covers the in-memory fallback; this runs the real WATCH/MULTI code against
``fakeredis`` (needs ``fakeredis[lua]``: saving uses EVAL) or, with ``--redis-url``,
a disposable Redis you own. Never point it at a shared instance.

    python test/probe_pending_claim_redis.py --fakeredis
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "gen" / "python")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from orchestrator.cloud.models import SessionState  # noqa: E402
from orchestrator.cloud import session as session_module  # noqa: E402
from orchestrator.cloud.session import (CLAIM_ABSENT, CLAIM_FENCED, CLAIM_OK,  # noqa: E402
                                        CLAIM_TAKEN, SessionStore)


def _state(op, **over):
    base = dict(phase="wait_confirm", owner_user_id="u1", operation_id=op, pending_step_id="s1",
                pending_plan={"steps": [{"id": "s1", "depends_on": [], "slots": {}}]},
                missing_slots=[], confirmation={"v": 1, "edge_state": {"driving": False}})
    base.update(over)
    return SessionState(**base)


async def concurrent_claims_have_one_winner(store, _client):
    await store.save("s", _state("op-a"))
    results = await asyncio.gather(*(store.claim_result("s", owner_user_id="u1", operation_id="op-a",
                                                        token=f"t{i}") for i in range(8)))
    statuses = [r[0] for r in results]
    assert statuses.count(CLAIM_OK) == 1 and statuses.count(CLAIM_TAKEN) == 7, statuses
    winner = next(r[1].claimed_by for r in results if r[0] == CLAIM_OK)
    assert all(r[1].claimed_by == winner for r in results if r[0] == CLAIM_TAKEN)
    stored = await store.load("s", owner_user_id="u1", operation_id="op-a")
    assert stored.claimed_by == winner and stored.claimed_at > 0


async def claim_keeps_other_entries_and_json_shapes(store, _client):
    await store.save("s2", _state("op-1"))
    await store.save("s2", _state("op-2", phase="wait_slot", missing_slots=["item"]))
    status, _ = await store.claim_result("s2", owner_user_id="u1", operation_id="op-1", token="t")
    assert status == CLAIM_OK
    entries = {s.operation_id: s for s in await store.load_all("s2", owner_user_id="u1")}
    assert set(entries) == {"op-1", "op-2"}
    assert entries["op-2"].claimed_by == "" and entries["op-2"].missing_slots == ["item"]
    assert entries["op-1"].pending_plan["steps"][0]["depends_on"] == []          # lists stay lists
    assert entries["op-1"].confirmation == {"v": 1, "edge_state": {"driving": False}}


async def ttl_is_preserved(store, client):
    await store.save("s3", _state("op-t", ttl_seconds=120))
    key = store._session_key("u1", "s3")
    before = await client.ttl(key)
    await store.claim_result("s3", owner_user_id="u1", operation_id="op-t", token="t")
    after = await client.ttl(key)
    assert 0 < after <= before <= 120, (before, after)


async def absent_expired_and_fenced(store, client):
    await store.save("s4", _state("op-e", expires_at=time.time() + 0.05))
    await asyncio.sleep(0.1)
    assert (await store.claim_result("s4", owner_user_id="u1", operation_id="op-e", token="t"))[0] == CLAIM_ABSENT
    assert (await store.claim_result("s4", owner_user_id="u1", operation_id="nope", token="t"))[0] == CLAIM_ABSENT
    await store.save("s5", _state("op-f"))
    await client.set(store._owner_fence_key("u1"), "deleting", ex=30)
    try:
        assert (await store.claim_result("s5", owner_user_id="u1", operation_id="op-f", token="t"))[0] == CLAIM_FENCED
    finally:
        await client.delete(store._owner_fence_key("u1"))


async def a_concurrent_write_forces_a_retry_not_a_lost_claim(store, client):
    """Another writer touches the watched key between GET and EXEC: EXEC must fail and retry."""
    await store.save("s6", _state("op-w"))
    key = store._session_key("u1", "s6")
    real_pipeline, attempts = client.pipeline, {"exec": 0}

    def pipeline(*args, **kwargs):
        pipe = real_pipeline(*args, **kwargs)
        real_execute = pipe.execute

        async def execute(*a, **k):
            attempts["exec"] += 1
            if attempts["exec"] == 1:
                await client.set(key, await client.get(key), keepttl=True)
            return await real_execute(*a, **k)

        pipe.execute = execute
        return pipe

    client.pipeline = pipeline
    try:
        status, seen = await store.claim_result("s6", owner_user_id="u1", operation_id="op-w", token="t")
    finally:
        client.pipeline = real_pipeline
    assert attempts["exec"] == 2, f"expected one WATCH conflict then success, saw {attempts}"
    assert status == CLAIM_OK and seen.claimed_by == "t"
    assert (await store.load("s6", owner_user_id="u1", operation_id="op-w")).claimed_by == "t"


async def settlement_still_deletes_a_claimed_entry(store, _client):
    await store.save("s7", _state("op-d"))
    await store.claim_result("s7", owner_user_id="u1", operation_id="op-d", token="t")
    await store.clear("s7", owner_user_id="u1", operation_id="op-d")
    assert await store.load_all("s7", owner_user_id="u1") == []


SCENARIOS = [concurrent_claims_have_one_winner, claim_keeps_other_entries_and_json_shapes,
             ttl_is_preserved, absent_expired_and_fenced,
             a_concurrent_write_forces_a_retry_not_a_lost_claim, settlement_still_deletes_a_claimed_entry]


async def run(redis_url: str | None) -> dict:
    if redis_url:
        import redis.asyncio as aioredis
        client = aioredis.from_url(redis_url, decode_responses=True)
    else:
        import fakeredis
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
    results = []
    for scenario in SCENARIOS:
        store = SessionStore(redis_url="redis://probe")
        store._r = client
        try:
            await scenario(store, client)
            results.append({"scenario": scenario.__name__, "passed": True})
        except Exception:
            results.append({"scenario": scenario.__name__, "passed": False,
                            "error": traceback.format_exc(limit=5)})
    return {"backend": "redis" if redis_url else "fakeredis", "results": results,
            "passed": sum(r["passed"] for r in results), "failed": sum(not r["passed"] for r in results)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--fakeredis", action="store_true")
    target.add_argument("--redis-url")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args.redis_url))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("backend", "passed", "failed")}))
    for item in report["results"]:
        if not item["passed"]:
            print(item["scenario"], item["error"], sep="\n", file=sys.stderr)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
