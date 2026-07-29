"""受控 MCP 桥启动入口。

两件事的顺序都不能反：
1. **日志先于 bootstrap**：准入决议（拒载/忽略/准入了哪些工具）是本 Agent 最该被审计的
   输出，而 `serve()` 里才配结构化日志——不提前配，这些 INFO 会被默认级别吞掉。
2. **bootstrap 先于 serve**：注册发生在 serve 里，capability 是从准入清单合成的，
   晚一步注册中心看到的就是空能力表。
"""
import asyncio
import json
import os

from agents._sdk import serve
from agents.mcp_bridge.src.agent import McpBridgeAgent

ADMIN_SUBJECT = "e2e.mcp.namespace.admin"
ADMIN_MAX_REQUEST_BYTES = 16 * 1024


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _result(*, ok=False, op="", count=0, order_id="", status="",
            deleted=0, error=""):
    return {
        "ok": bool(ok),
        "op": str(op),
        "count": int(count),
        "order_id": str(order_id),
        "status": str(status),
        "deleted": int(deleted),
        "error": str(error),
    }


def _verified_request(data: bytes, *, secret: bytes, now=None) -> dict:
    if not isinstance(data, bytes) or len(data) > ADMIN_MAX_REQUEST_BYTES:
        raise ValueError("invalid request")
    try:
        request = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid request") from exc
    if not isinstance(request, dict) or set(request) != {
        "identity_token", "user_id", "op", "order_id", "intent",
    }:
        raise ValueError("invalid request")
    token = request.pop("identity_token")
    owner = request.get("user_id")
    if not isinstance(token, str) or not isinstance(owner, str) or not owner:
        raise PermissionError("unauthorized")
    if (
        not isinstance(request.get("op"), str)
        or not isinstance(request.get("order_id"), str)
        or not isinstance(request.get("intent"), str)
    ):
        raise ValueError("invalid request")
    from scripts.e2e_identity import IdentityTokenError, verify_identity
    try:
        claims = verify_identity(token, secret, now=now)
    except IdentityTokenError as exc:
        raise PermissionError("unauthorized") from exc
    if owner != claims.user_id or owner != f"{claims.run_id}-e2e_mcp":
        raise PermissionError("unauthorized")
    return request


async def install_namespace_admin(
    nc,
    agent: McpBridgeAgent,
    *,
    environ=None,
    now=None,
) -> bool:
    """Subscribe the signed hidden lifecycle plane; default is no subscription."""

    source = os.environ if environ is None else environ
    if str(source.get("E2E_NAMESPACE_ADMIN_ENABLED") or "").lower() != "true":
        return False
    raw_secret = source.get("E2E_NAMESPACE_ADMIN_SECRET")
    if not isinstance(raw_secret, str) or not raw_secret:
        return False
    try:
        from scripts.e2e_identity import decode_secret
        secret = decode_secret(raw_secret)
    except Exception:
        return False

    async def callback(message):
        response = _result(error="invalid_request")
        try:
            request = _verified_request(message.data, secret=secret, now=now)
            candidate = await agent.namespace_admin(request)
            if isinstance(candidate, dict) and set(candidate) == set(response):
                response = _result(**candidate)
            else:
                response = _result(error="invalid_response")
        except PermissionError:
            response = _result(error="unauthorized")
        except Exception:
            response = _result(error="invalid_request")
        if getattr(message, "reply", ""):
            await nc.publish(
                message.reply,
                json.dumps(
                    response,
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode(),
            )

    await nc.subscribe(ADMIN_SUBJECT, cb=callback)
    return True


async def main():
    try:
        from observability import setup_structured_logging
        setup_structured_logging(os.getenv("LOG_LEVEL", "info"), service="mcp-bridge")
    except Exception:
        pass
    agent = McpBridgeAgent()
    await agent.bootstrap()
    admin_nc = None
    try:
        enabled = (
            os.getenv("E2E_NAMESPACE_ADMIN_ENABLED", "").lower() == "true"
            and bool(os.getenv("E2E_NAMESPACE_ADMIN_SECRET", ""))
        )
        if enabled:
            try:
                import nats
                admin_nc = await nats.connect(
                    os.getenv("NATS_URL", "nats://nats:4222"),
                    max_reconnect_attempts=-1,
                )
                if await install_namespace_admin(admin_nc, agent):
                    import logging
                    logging.getLogger("agent.mcp_bridge").info(
                        "E2E MCP namespace admin enabled",
                    )
            except Exception:
                # Admin remains fail-closed; business MCP capabilities still run.
                if admin_nc is not None:
                    await admin_nc.close()
                    admin_nc = None
        await serve(agent)
    finally:
        if admin_nc is not None:
            await admin_nc.close()
        await agent.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
