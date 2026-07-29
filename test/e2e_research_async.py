"""端到端验证：异步深度调研经 WS 受理并主动推送报告。

本脚本只使用 manifest runner 签发的 user/session namespace。Task Ledger 与
memory/profile 都按 exact user 清理；任一清理失败会由 CaseRecorder 把结果升级为 FAIL。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from support.e2e import CaseRecorder, assert_persistent_source_contract


def _source_contract() -> None:
    assert_persistent_source_contract(Path(__file__).read_text(encoding="utf-8"))


if "--source-contract" in sys.argv:
    _source_contract()
    print("source contract: PASS")
    raise SystemExit(0)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "gen", "python"),
)

import grpc  # noqa: E402
from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc  # noqa: E402

try:
    import websockets
except ImportError:
    websockets = None

ACK_TIMEOUT = 30
ASYNC_WAIT = 260
MEM_ADDR = os.getenv("MEM_ADDR", "localhost:50053")
PG = [
    "docker", "exec", "car-agent-postgres-1", "psql", "-U", "cockpit",
    "-d", "cockpit", "-tAc",
]


def sql(query: str) -> str:
    completed = subprocess.run(
        PG + [query],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError("task ledger SQL command failed")
    return (completed.stdout or "").strip()


def quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def ledger_count(user: str) -> int:
    return int(sql(
        f"SELECT count(*) FROM task_ledger WHERE user_id={quoted(user)}",
    ) or 0)


def memory_count(user: str) -> int:
    with grpc.insecure_channel(MEM_ADDR) as channel:
        response = memory_pb2_grpc.MemoryStub(channel).ExportUser(
            memory_pb2.ExportUserRequest(user_id=user),
            timeout=10,
        )
    data = json.loads(response.json) if response.json else {}
    return (
        len(data.get("memories") or [])
        + len(data.get("relations") or [])
        + (1 if data.get("profile") else 0)
    )


def namespace_count(user: str) -> int:
    return ledger_count(user) + memory_count(user)


def cleanup_namespace(user: str) -> None:
    owner = quoted(user)
    # Missing rows are treated as cancelled by the next worker heartbeat. This
    # stops an unfinished exact-owner task without restarting the shared agent.
    sql(f"DELETE FROM task_ledger WHERE user_id={owner}")
    with grpc.insecure_channel(MEM_ADDR) as channel:
        memory_pb2_grpc.MemoryStub(channel).ForgetUser(
            memory_pb2.ForgetUserRequest(user_id=user),
            timeout=15,
        )
    remaining = namespace_count(user)
    if remaining:
        raise RuntimeError(f"research cleanup left {remaining} owner records")


def record(
    recorder: CaseRecorder,
    case_id: str,
    ok: bool,
    detail: str = "",
) -> None:
    if ok:
        recorder.pass_case(case_id)
    else:
        recorder.fail_case(
            case_id,
            "assertion_failed",
            detail or case_id,
        )
    print(f"  {'✓' if ok else '✗'} {case_id}" + (f" — {detail}" if detail else ""))


async def run(recorder: CaseRecorder) -> None:
    if websockets is None:
        recorder.fail_case(
            "websocket_dependency",
            "dependency_unavailable",
            "websockets is unavailable",
        )
        return

    user = recorder.user_id()
    session = recorder.session_id(1)
    recorder.register_cleanup(user, lambda: cleanup_namespace(user))
    before = namespace_count(user)
    record(
        recorder,
        "isolation_precondition",
        before == 0,
        f"owner records before setup={before}",
    )
    if before:
        return

    # The run marker makes the report card itself an exact correlation key.
    topic = f"固态电池技术路线和量产前景（批次{recorder.run_id()}）"
    text = f"深入调研一下{topic}，不急慢慢查，查完语音告诉我"
    request = {"text": text, "session_id": session}

    async with websockets.connect(
        recorder.ws_url(),
        ping_interval=None,
        close_timeout=3,
    ) as ws:
        await ws.send(json.dumps(request, ensure_ascii=False))

        ack = None
        deadline = time.monotonic() + ACK_TIMEOUT
        while time.monotonic() < deadline:
            message = json.loads(await asyncio.wait_for(
                ws.recv(),
                timeout=max(1, deadline - time.monotonic()),
            ))
            if message.get("type") in ("process", "e2e_identity_ack", "vehicle_state"):
                continue
            if message.get("type") in ("final", "error"):
                ack = message
                break

        speech = (ack or {}).get("speech") or ""
        ack_card = (ack or {}).get("ui_card") or {}
        record(
            recorder,
            "research_ack",
            bool(
                ack
                and ack.get("type") == "final"
                and any(
                    word in speech
                    for word in ("几分钟", "报告", "查完", "稍后", "通知")
                )
            ),
            speech[:100] or "missing final acknowledgement",
        )
        record(
            recorder,
            "ack_has_no_report",
            ack_card.get("type") != "research_report",
            f"card.type={ack_card.get('type')}",
        )

        task_rows = json.loads(sql(
            "SELECT COALESCE(json_agg(row_to_json(t)), '[]'::json) "
            "FROM (SELECT task_id,status,session_id FROM task_ledger "
            f"WHERE user_id={quoted(user)} AND session_id={quoted(session)}) t",
        ) or "[]")
        record(
            recorder,
            "exact_task_opened",
            len(task_rows) == 1 and bool(task_rows[0].get("task_id")),
            f"exact task rows={len(task_rows)}",
        )

        report = None
        deadline = time.monotonic() + ASYNC_WAIT
        while time.monotonic() < deadline:
            try:
                message = json.loads(await asyncio.wait_for(
                    ws.recv(),
                    timeout=max(1, deadline - time.monotonic()),
                ))
            except asyncio.TimeoutError:
                break
            card = message.get("card") or {}
            if (
                message.get("type") == "proactive"
                and card.get("type") == "research_report"
                and recorder.run_id() in str(card.get("question") or "")
            ):
                report = message
                break

        card = (report or {}).get("card") or {}
        record(
            recorder,
            "research_report_push",
            bool(report and card.get("type") == "research_report"),
            "received exact run report" if report else "exact report not received",
        )
        sections = card.get("sections") or []
        sources = card.get("sources") or []
        record(
            recorder,
            "report_payload",
            "sections" in card and "sources" in card,
            f"sections={len(sections)} sources={len(sources)}",
        )


def main() -> int:
    _source_contract()
    recorder = CaseRecorder()
    with recorder:
        asyncio.run(run(recorder))
    print(
        f"\n{recorder.result.counts['passed']}/"
        f"{recorder.result.counts['selected']} passed",
    )
    return recorder.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
