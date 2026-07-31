"""真栈闭环：受控 MCP 桥的准入、读、确认写、幂等与真实补偿。

订单和 Task Ledger 均使用 runner 签发的 exact owner。脚本不重启共享服务；
finally 通过签名 E2E 管理面原子枚举、补偿并清理，不依赖是否拿到 order_id，
并验证 external+ledger count=0。
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

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "gen", "python"),
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    websockets = None

REGISTRY_ADDR = "localhost:50051"
NATS_URL = os.getenv("NATS_URL_LOCAL", "nats://localhost:4222")
ADMIN_SUBJECT = "e2e.mcp.namespace.admin"
ADMIN_MAX_RESPONSE_BYTES = 16 * 1024
PG = [
    "docker", "exec", "car-agent-postgres-1", "psql", "-U", "cockpit",
    "-d", "cockpit", "-tAc",
]
_recorder: CaseRecorder | None = None
_case_index = 0


def record(name: str, ok: bool, detail: str = "") -> None:
    global _case_index
    if _recorder is None:
        raise RuntimeError("CaseRecorder is not initialized")
    _case_index += 1
    case_id = f"mcp-{_case_index:02d}"
    if ok:
        _recorder.pass_case(case_id)
    else:
        _recorder.fail_case(case_id, "assertion_failed", detail or name)
    print(f"{'✅' if ok else '❌'} {name}  {detail}")


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


def cleanup_ledger(user: str) -> None:
    sql(f"DELETE FROM task_ledger WHERE user_id={quoted(user)}")
    remaining = ledger_count(user)
    if remaining:
        raise RuntimeError(f"MCP ledger cleanup left {remaining} rows")


async def ask(
    recorder: CaseRecorder,
    text: str,
    session: str,
) -> dict:
    """一问一答，返回 final 帧。"""

    async with websockets.connect(
        recorder.ws_url(),
        max_size=8 * 1024 * 1024,
    ) as ws:
        await ws.send(json.dumps({
            "type": "user_text",
            "text": text,
            "session_id": session,
        }, ensure_ascii=False))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(
                    ws.recv(),
                    timeout=max(1, deadline - time.monotonic()),
                )
            except asyncio.TimeoutError:
                break
            message = json.loads(raw)
            if message.get("type") in (
                "e2e_identity_ack", "vehicle_state", "process",
            ):
                continue
            if message.get("type") in ("final", "error"):
                return message
    return {}


async def admin_request(
    nc,
    *,
    identity_token: str,
    user_id: str,
    op: str,
    order_id: str = "",
) -> dict:
    payload = json.dumps({
        "identity_token": identity_token,
        "user_id": user_id,
        "op": op,
        "order_id": order_id,
        "intent": "shop.order",
    }, ensure_ascii=True, separators=(",", ":")).encode()
    message = await nc.request(ADMIN_SUBJECT, payload, timeout=5.0)
    if len(message.data) > ADMIN_MAX_RESPONSE_BYTES:
        raise RuntimeError("MCP admin response is too large")
    response = json.loads(message.data.decode("utf-8"))
    if (
        not isinstance(response, dict)
        or set(response) != {
            "ok", "op", "count", "order_id", "status", "deleted", "error",
        }
        or type(response.get("ok")) is not bool
        or type(response.get("count")) is not int
        or type(response.get("deleted")) is not int
        or any(
            not isinstance(response.get(key), str)
            for key in ("op", "order_id", "status", "error")
        )
    ):
        raise RuntimeError("MCP admin response is invalid")
    if not response["ok"]:
        raise RuntimeError(f"MCP admin rejected request: {response['error']}")
    return response


def cleanup_external(
    identity_token: str,
    user_id: str,
) -> None:
    async def cleanup() -> None:
        import nats
        nc = await nats.connect(NATS_URL, connect_timeout=5)
        try:
            cleaned = await admin_request(
                nc,
                identity_token=identity_token,
                user_id=user_id,
                op="lifecycle_cleanup",
            )
            if cleaned["count"] != 0:
                raise RuntimeError("MCP atomic lifecycle cleanup is not empty")
            counted = await admin_request(
                nc,
                identity_token=identity_token,
                user_id=user_id,
                op="count",
            )
            if counted["count"] != 0:
                raise RuntimeError("MCP external cleanup is not empty")
        finally:
            await nc.close()

    asyncio.run(cleanup())


def bridge_registration() -> tuple[list | None, str]:
    """Return the registered capabilities and endpoint for mcp-bridge."""

    try:
        import grpc
        from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc
    except ImportError:
        return None, ""
    with grpc.insecure_channel(REGISTRY_ADDR) as channel:
        response = registry_pb2_grpc.RegistryStub(channel).ListAgents(
            registry_pb2.ListRequest(),
            timeout=10,
        )
    for agent in response.agents:
        if agent.manifest.agent_id == "mcp-bridge":
            return (
                [
                    capability.intent
                    for capability in agent.manifest.capabilities
                ],
                str(agent.endpoint or ""),
            )
    return None, ""


def current_bridge_hostname() -> str:
    """Resolve the hostname of the container that owns this E2E profile."""

    completed = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Config.Hostname}}",
            "car-agent-mcp-bridge-1",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def wait_bridge_capabilities(timeout_s: float = 30) -> list | None:
    """Wait until Registry points at the current, not the replaced, container."""

    current = current_bridge_hostname()
    if not current:
        return None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        capabilities, endpoint = bridge_registration()
        endpoint_host = endpoint.rsplit(":", 1)[0]
        if capabilities is not None and endpoint_host == current:
            return capabilities
        time.sleep(0.5)
    return None


def cards_of(message: dict) -> list[dict]:
    cards = [message["ui_card"]] if message.get("ui_card") else []
    result = []
    for card in cards:
        if isinstance(card, dict) and card.get("type") == "card_group":
            result.extend(card.get("items") or [])
        elif isinstance(card, dict):
            result.append(card)
    return result


async def run(recorder: CaseRecorder) -> None:
    if websockets is None:
        recorder.fail_case(
            "websocket_dependency",
            "dependency_unavailable",
            "websockets is unavailable",
        )
        return
    try:
        import nats
    except ImportError:
        recorder.fail_case(
            "nats_dependency",
            "dependency_unavailable",
            "nats-py is unavailable",
        )
        return

    user = recorder.user_id()
    token = recorder.identity_token()
    order_ids: list[str] = []
    recorder.register_cleanup(user, lambda: cleanup_ledger(user))
    recorder.register_cleanup(
        user,
        lambda: cleanup_external(token, user),
    )

    nc = await nats.connect(NATS_URL, connect_timeout=5)
    try:
        external_before = await admin_request(
            nc,
            identity_token=token,
            user_id=user,
            op="count",
        )
        before = external_before["count"] + ledger_count(user)
        record("前置 exact owner namespace 为空", before == 0, f"count={before}")
        if before:
            return
    finally:
        await nc.close()

    print("── 1. 准入边界：注册中心只看得到清单里的能力 ──")
    caps = wait_bridge_capabilities()
    record("mcp-bridge 已注册", caps is not None, "" if caps else "注册中心里没有")
    caps = caps or []
    record("准入的两个工具都在", {"shop.menu", "shop.order"} <= set(caps), str(caps))
    record("cancel/admin 均未注册到业务能力",
           not any("cancel" in item or "admin" in item for item in caps), str(caps))

    print("\n── 2. 只读工具经 MCP 取真数据 ──")
    response = await ask(recorder, "看看咖啡菜单有什么", recorder.session_id(1))
    speech = response.get("speech", "")
    record("菜单经 MCP server 返回",
           any(item in speech for item in ("拿铁", "美式", "摩卡")), speech[:70])
    record("演示商户在话术里说明白", "演示商户" in speech, speech[:40])
    cards = cards_of(response)
    demo_card = next((card for card in cards if card.get("demo")), None)
    record("卡片带演示商户角标", bool(demo_card),
           str([card.get("type") for card in cards]))
    record("_prov 不盖真章（mode=mock）",
           bool(demo_card)
           and (demo_card.get("_prov") or {}).get("mode") == "mock",
           str((demo_card or {}).get("_prov")))

    print("\n── 3. 写操作：先确认 ──")
    order_session = recorder.session_id(2)
    response = await ask(recorder, "点一杯大杯拿铁", order_session)
    speech = response.get("speech", "")
    record("下单先要二次确认（钱的事不能一句话就扣）",
           "确认" in speech or response.get("status") == "need_confirm",
           speech[:70])

    print("\n── 4. 确认后下单 + 幂等 ──")
    response = await ask(recorder, "确认", order_session)
    speech1 = response.get("speech", "")
    order_card = next(
        (
            card for card in cards_of(response)
            if card.get("type") == "mcp_order" and card.get("order_id")
        ),
        None,
    )
    if order_card:
        order_ids.append(str(order_card["order_id"]))
    record("确认后真的下单了（新单，不是幂等复用）",
           bool(order_card)
           and ("订单" in speech1 or "下单" in speech1)
           and "已经下过了" not in speech1,
           speech1[:70])

    second_session = recorder.session_id(3)
    response = await ask(recorder, "点一杯大杯拿铁", second_session)
    if "确认" in response.get("speech", ""):
        response = await ask(recorder, "确认", second_session)
    speech2 = response.get("speech", "")
    duplicate_card = next(
        (
            card for card in cards_of(response)
            if card.get("type") == "mcp_order" and card.get("order_id")
        ),
        None,
    )
    if duplicate_card:
        order_ids.append(str(duplicate_card["order_id"]))
    record("同一单再说一遍不双扣（幂等键=请求指纹）",
           ("已经下过了" in speech2 or "已存在" in speech2)
           and bool(order_card)
           and bool(duplicate_card)
           and duplicate_card["order_id"] == order_card["order_id"],
           speech2[:70])

    nc = await nats.connect(NATS_URL, connect_timeout=5)
    try:
        status = await admin_request(
            nc,
            identity_token=token,
            user_id=user,
            op="status",
            order_id=order_ids[0] if order_ids else "",
        ) if order_ids else {}
        record("外部商户 exact order 可查且仍为 submitted",
               status.get("status") == "submitted",
               f"status={status.get('status')}")
    finally:
        await nc.close()


def main() -> int:
    _source_contract()
    global _recorder
    _recorder = CaseRecorder()
    with _recorder:
        asyncio.run(run(_recorder))
    result = _recorder.result
    print(f"\n===== e2e_mcp: {result.counts['passed']}/{result.counts['selected']} =====")
    return _recorder.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
