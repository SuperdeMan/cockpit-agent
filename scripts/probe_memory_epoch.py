"""CA2-15 S1b manual real-stack probe: a write observed before a deletion is refused.

REMOTE-MUTATING, synthetic namespace only. Inside the live Memory container it creates a throwaway
owner ``probe-memfence-<run>`` (its session prefix ``probe-`` skips LLM extraction), writes a turn
under the current epoch, forgets the owner, then replays writes that carry the pre-forget epoch:
every one must be refused with ``stale_memory_epoch`` and leave nothing behind, while a write with
the new epoch is accepted. The probe forgets the owner again and removes its epoch key at the end.
No real user's memory is read or written. Run only with explicit per-round authorization and
``--allow-synthetic-memory-write``.

    python scripts/probe_memory_epoch.py --expected-sha <sha> --allow-synthetic-memory-write \\
        --output .artifacts/ca2-15/<sha>-memory-epoch-probe.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cloud_release_lib import SshConfig  # noqa: E402

_IN_CONTAINER = r'''
import asyncio, json, os, sys
import grpc
sys.path.insert(0, "/app/gen/python")
from cockpit.memory.v1 import memory_pb2 as m, memory_pb2_grpc

USER = sys.argv[1]
SESSION = USER + "-s1"


async def main():
    out = {}
    async with grpc.aio.insecure_channel("127.0.0.1:" + os.getenv("MEMORY_PORT", "50053")) as channel:
        stub = memory_pb2_grpc.MemoryStub(channel)

        async def epoch():
            return (await stub.GetMemoryEpoch(m.GetMemoryEpochRequest(user_id=USER), timeout=5)).memory_epoch

        async def turn(text, value, turn_id):
            r = await stub.AppendTurn(m.AppendTurnRequest(
                session_id=SESSION, role="user", text=text, user_id=USER, occupant_id="primary",
                turn_id=turn_id, exchange_id=turn_id, memory_epoch=value), timeout=5)
            return {"ok": r.ok, "error": r.error}

        async def session():
            r = await stub.GetSession(m.GetSessionRequest(
                session_id=SESSION, last_n=20, user_id=USER, occupant_id="primary"), timeout=5)
            return [t.text for t in r.turns]

        e0 = await epoch()
        out["epoch_before"] = e0.split(".")[0]
        out["write_current"] = await turn("probe before forget", e0, "x1:user")
        out["forget"] = (await stub.ForgetUser(m.ForgetUserRequest(user_id=USER), timeout=5)).ok
        e1 = await epoch()
        out["epoch_after"] = e1.split(".")[0]
        out["epoch_changed"] = e1 != e0
        out["turn_with_old_epoch"] = await turn("probe stale turn", e0, "x2:user")
        r = await stub.Remember(m.RememberRequest(items=[m.MemoryItem(
            user_id=USER, occupant_id="primary", kind="semantic", text="probe stale item",
            predicate="probe.fence")], memory_epoch=e0), timeout=5)
        out["remember_with_old_epoch"] = {"ok": r.ok, "error": r.error, "ids": len(r.ids)}
        r = await stub.UpsertProfile(m.UpsertProfileRequest(
            user_id=USER, key="probe_fence", value_json="{}", memory_epoch=e0), timeout=5)
        out["profile_with_old_epoch"] = {"ok": r.ok, "error": r.error}
        out["session_after_stale_writes"] = await session()
        out["turn_with_new_epoch"] = await turn("probe after forget", e1, "x3:user")
        out["session_after_new_write"] = await session()
        # cleanup: the owner's data, then its epoch key
        out["cleanup_forget"] = (await stub.ForgetUser(m.ForgetUserRequest(user_id=USER), timeout=5)).ok
        out["session_after_cleanup"] = await session()
    url = os.getenv("REDIS_URL", "")
    if url:
        import redis.asyncio as aioredis
        r = aioredis.from_url(url, decode_responses=True)
        out["epoch_key_removed"] = bool(await r.delete("mem_epoch:" + USER))
        await r.aclose()
    print(json.dumps(out, ensure_ascii=False))


asyncio.run(main())
'''

# The in-container program travels inside this one as a literal, so neither the SSH remote shell
# nor docker sees any of its quoting.
_REMOTE = r'''
import subprocess, sys
payload = __PAYLOAD__
ids = subprocess.check_output(["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=memory"],
                              timeout=20).decode().split()
assert len(ids) == 1, ids
print(subprocess.check_output(["docker", "exec", "-i", ids[0], "python", "-", sys.argv[1]],
                              input=payload.encode(), timeout=60).decode().strip())
'''


def expected(result: dict) -> list[str]:
    """The verdict: every line that does not hold."""
    stale = "stale_memory_epoch"
    checks = {
        "current write accepted": result.get("write_current") == {"ok": True, "error": ""},
        "forget succeeded": result.get("forget") is True,
        "forget advanced the epoch": result.get("epoch_changed") is True
        and int(result.get("epoch_after", -1)) == int(result.get("epoch_before", -2)) + 1,
        "turn with the old epoch refused": result.get("turn_with_old_epoch") == {"ok": False, "error": stale},
        "remember with the old epoch refused": result.get("remember_with_old_epoch") == {"ok": False, "error": stale, "ids": 0},
        "profile with the old epoch refused": result.get("profile_with_old_epoch") == {"ok": False, "error": stale},
        "nothing came back": result.get("session_after_stale_writes") == [],
        "new epoch accepted": result.get("turn_with_new_epoch") == {"ok": True, "error": ""}
        and result.get("session_after_new_write") == ["probe after forget"],
        "cleaned up": result.get("cleanup_forget") is True and result.get("session_after_cleanup") == []
        and result.get("epoch_key_removed", True) is True,
    }
    return [name for name, ok in checks.items() if not ok]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--allow-synthetic-memory-write", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.allow_synthetic_memory_write:
        parser.error("remote-mutating: pass --allow-synthetic-memory-write after per-round authorization")
    output = Path(args.output)
    if output.exists():
        parser.error("output already exists; use a new run artifact")
    status = json.loads(subprocess.check_output(
        [sys.executable, str(ROOT / "scripts" / "dev_stack.py"), "status"], timeout=300).decode().splitlines()[-1])
    if status.get("running_release_sha") != args.expected_sha:
        parser.error(f"live release is {status.get('running_release_sha')}, not {args.expected_sha}")
    cfg = SshConfig(os.environ["CAR_AGENT_DEPLOY_HOST"], os.environ.get("CAR_AGENT_DEPLOY_USER", "ubuntu"),
                    Path(os.environ["CAR_AGENT_SSH_IDENTITY"]), os.environ.get("CAR_AGENT_SSH_KEX_ALGORITHMS"))
    user = f"probe-memfence-{uuid.uuid4().hex[:12]}"
    program = _REMOTE.replace("__PAYLOAD__", repr(_IN_CONTAINER))
    proc = subprocess.run(cfg.ssh_argv(f"sudo python3 - {user}"), input=program.encode(),
                          capture_output=True, timeout=180)
    lines = [line for line in proc.stdout.decode(errors="replace").splitlines() if line.startswith("{")]
    result = json.loads(lines[-1]) if lines else {"error": proc.stderr.decode(errors="replace")[-400:]}
    failed = expected(result)
    record = {"release_sha": args.expected_sha, "probe_user": user, "result": result,
              "verdict": "PASS" if not failed else "FAIL", "failed": failed}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": record["verdict"], "failed": failed}, ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
