"""统一主动引擎服务入口（M3 P0）。

订 `agent.proactive.request`（生产方经 `runtime/proactive.py` 发）→ 六道闸 + 合并窗口 →
发既有 `agent.proactive`（网关与 HMI 零改动）。

**ack 是投递所有权移交**：合并/延后在 ack 之后异步做，生产方不被窗口阻塞。
M-C 起，`user_contract`/`critical` 档**先落库再 ack**——ack 之后生产方就不再重发，
而 ack 与真正投出去之间隔着合并窗与延后队列，进程在这段里死掉那条「到点提醒我」
就永远没了。落库失败时 ack 里如实带 `durable=false`，不假装已持久接管。
`PROACTIVE_GOVERNOR_ENABLED=false` → 不订阅 → 生产方的 request 拿不到响应 → 自动直发老主题
（`runtime/proactive.py` 的 fail-open），行为逐字回落到治理器上线前。**这就是一键回退。**
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from observability import setup_structured_logging

from .delivery_store import DeliveryStore
from .governor import Governor
from .mirror import VehicleMirror, nats_url

setup_structured_logging(os.getenv("LOG_LEVEL", "info"), service="proactive")
logger = logging.getLogger("proactive.main")

REQUEST_SUBJECT = "agent.proactive.request"
OUTPUT_SUBJECT = "agent.proactive"
# HMI 回执与上线补投（M-C）。**这两条是「发出去了」与「用户收到了」之间缺的那一跳**：
# 此前网关 broadcast 的返回值（在线 HMI 数）只进了一行日志，n==0 时消息直接蒸发。
ACK_SUBJECT = "agent.proactive.ack"
REPLAY_SUBJECT = "agent.proactive.replay"
DECISION_SUBJECT = "obs.proactive.decision"
ADMIN_COUNT_SUBJECT = "e2e.proactive.namespace.count"
ADMIN_PURGE_SUBJECT = "e2e.proactive.namespace.purge"
ADMIN_MAX_REQUEST_BYTES = 16 * 1024


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _admin_response(
    *,
    ok=False,
    before=0,
    deleted=0,
    after=0,
    rate_delivered=0,
    rate_max_per_hour=0,
    error="",
):
    # Rate fields are anonymous global counters only; no other owner identity
    # or per-owner behavior is exposed through the signed E2E admin plane.
    return {
        "ok": bool(ok),
        "before": int(before),
        "deleted": int(deleted),
        "after": int(after),
        "rate_delivered": int(rate_delivered),
        "rate_max_per_hour": int(rate_max_per_hour),
        "error": str(error),
    }


def _admin_owner(data: bytes, *, secret: bytes, now=None) -> str:
    """Verify one strict signed request without ever returning/logging its token."""

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
        "identity_token", "user_id",
    }:
        raise ValueError("invalid request")
    token = request["identity_token"]
    owner = request["user_id"]
    if not isinstance(token, str) or not isinstance(owner, str) or not owner:
        raise ValueError("unauthorized")
    from scripts.e2e_identity import IdentityTokenError, verify_identity
    try:
        claims = verify_identity(token, secret, now=now)
    except IdentityTokenError as exc:
        raise PermissionError("unauthorized") from exc
    expected = f"{claims.run_id}-e2e_proactive"
    if owner != claims.user_id or owner != expected:
        raise PermissionError("unauthorized")
    return owner


async def install_namespace_admin(
    nc,
    governor: Governor,
    *,
    environ=None,
    now=None,
) -> bool:
    """Install signed E2E-only count/purge callbacks; default-off fails closed."""

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

    async def reply(message, operation: str) -> None:
        response = _admin_response(error="invalid_request")
        try:
            owner = _admin_owner(message.data, secret=secret, now=now)
            if operation == "count":
                count = governor.count_owner(owner)
                response = _admin_response(
                    ok=True,
                    before=count,
                    after=count,
                    **governor.rate_status(),
                )
            else:
                values = governor.purge_owner(owner)
                response = _admin_response(
                    ok=True,
                    **values,
                    **governor.rate_status(),
                )
        except PermissionError:
            response = _admin_response(error="unauthorized")
        except Exception:
            response = _admin_response(error="invalid_request")
        if getattr(message, "reply", ""):
            await nc.publish(
                message.reply,
                json.dumps(
                    response,
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode(),
            )

    async def count_callback(message) -> None:
        await reply(message, "count")

    async def purge_callback(message) -> None:
        await reply(message, "purge")

    await nc.subscribe(ADMIN_COUNT_SUBJECT, cb=count_callback)
    await nc.subscribe(ADMIN_PURGE_SUBJECT, cb=purge_callback)
    return True


def _enabled() -> bool:
    return os.getenv("PROACTIVE_GOVERNOR_ENABLED", "true").strip().lower() not in (
        "false", "0", "off", "no")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


class _Health(BaseHTTPRequestHandler):
    ready = False

    def do_GET(self):                                    # noqa: N802
        body = json.dumps({"status": "ok" if _Health.ready else "starting",
                           "service": "proactive"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):                       # 健康探针不刷日志
        return


def _serve_health(port: int) -> None:
    Thread(target=HTTPServer(("0.0.0.0", port), _Health).serve_forever,
           daemon=True).start()


async def main() -> None:
    port = _int_env("PROACTIVE_HTTP_PORT", 50075)
    _serve_health(port)

    if not _enabled():
        logger.warning("PROACTIVE_GOVERNOR_ENABLED=false —— 治理器不订阅，"
                       "生产方将直发 %s（回落到治理器上线前的行为）", OUTPUT_SUBJECT)
        await asyncio.Future()

    url = nats_url()
    if not url:
        logger.warning("NATS_URL 未设置 —— 治理器无法工作，生产方将直发 %s", OUTPUT_SUBJECT)
        await asyncio.Future()

    import nats
    nc = await nats.connect(url, max_reconnect_attempts=-1)
    mirror = VehicleMirror()
    await mirror.subscribe(nc)
    store = DeliveryStore()
    await store.init()

    async def publish(payload: dict) -> None:
        await nc.publish(OUTPUT_SUBJECT,
                         json.dumps(payload, ensure_ascii=False).encode())

    async def emit(event: dict) -> None:
        await nc.publish(DECISION_SUBJECT,
                         json.dumps(event, ensure_ascii=False).encode())

    gov = Governor(
        publish,
        state_fn=mirror.snapshot,
        emit=emit,
        merge_window_ms=_int_env("PROACTIVE_MERGE_WINDOW_MS", 1500),
        dedup_window_s=_int_env("PROACTIVE_DEDUP_WINDOW_S", 600),
        max_per_hour=_int_env("PROACTIVE_MAX_PER_HOUR", 6),
        high_load_speed=_float_env("PROACTIVE_HIGH_LOAD_SPEED", 80.0),
        quiet_hours=os.getenv("PROACTIVE_QUIET_HOURS", ""),
        defer_tick_s=_float_env("PROACTIVE_DEFER_TICK_S", 5.0),
        store=store,
    )
    await gov.start()
    if await install_namespace_admin(nc, gov):
        logger.info("E2E proactive namespace admin enabled")

    async def on_request(msg) -> None:
        try:
            payload = json.loads(msg.data.decode())
        except Exception:
            logger.warning("丢弃非法主动请求（JSON 解析失败）")
            return
        if not isinstance(payload, dict):
            return
        # 先接管（durable 档在此落库）再 ack，然后才裁决：ack 一旦发出生产方就不再
        # 重发，所以它必须晚于「有账」这一步；但仍早于合并窗与延后队列，生产方
        # 照旧不被窗口阻塞。
        try:
            item, durable = await gov.take_ownership(payload)
        except Exception as e:
            logger.warning("接管异常，回落直发：%s", e)
            if msg.reply:
                try:
                    await nc.publish(msg.reply, b'{"accepted":true,"durable":false}')
                except Exception:
                    pass
            await publish({k: v for k, v in payload.items()
                           if k not in ("priority", "conditions", "dedup_key", "ttl_ms")})
            return
        if msg.reply:
            try:
                ack = {"accepted": True, "durable": durable}
                if item.delivery_id:
                    ack["delivery_id"] = item.delivery_id
                await nc.publish(msg.reply,
                                 json.dumps(ack, ensure_ascii=False).encode())
            except Exception as e:
                logger.debug("ack 失败（继续裁决）：%s", e)
        try:
            await gov.govern(item)
        except Exception as e:
            logger.warning("裁决异常，回落直发：%s", e)
            await publish(item.forwardable())

    async def on_ack(msg) -> None:
        """HMI 回执——**唯一的通知合同完成条件**。"""
        try:
            data = json.loads(msg.data.decode())
        except Exception:
            return
        if not isinstance(data, dict):
            return
        ids = data.get("delivery_ids") or data.get("delivery_id") or []
        try:
            n = await gov.acknowledge(ids)
            if n:
                logger.info("投递回执：%d 条已呈现", n)
        except Exception as e:
            logger.warning("回执处理异常：%s", e)

    async def on_replay(msg) -> None:
        """HMI（重新）连上——补投还没送到的消息。"""
        try:
            data = json.loads(msg.data.decode())
        except Exception:
            data = {}
        user_id = str((data or {}).get("user_id") or "")
        try:
            await gov.replay_undelivered(user_id)
        except Exception as e:
            logger.warning("补投异常：%s", e)

    await nc.subscribe(REQUEST_SUBJECT, cb=on_request)
    await nc.subscribe(ACK_SUBJECT, cb=on_ack)
    await nc.subscribe(REPLAY_SUBJECT, cb=on_replay)
    _Health.ready = True
    logger.info("主动治理器就绪：%s → %s（durable=%s / 合并窗口 %sms / 频控 %s 条每小时 / "
                "免打扰 %s / 高负荷车速 %s）", REQUEST_SUBJECT, OUTPUT_SUBJECT,
                "on" if store.pg_ok else "off(内存兜底)",
                os.getenv("PROACTIVE_MERGE_WINDOW_MS", "1500"),
                os.getenv("PROACTIVE_MAX_PER_HOUR", "6"),
                os.getenv("PROACTIVE_QUIET_HOURS", "") or "未启用",
                os.getenv("PROACTIVE_HIGH_LOAD_SPEED", "80"))
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
