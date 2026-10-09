"""FastAPI service for observability snapshots and live event streaming."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from . import otel_bridge
from .db import ObsDB
from .metrics_export import render_prometheus_metrics
from .query_projection import trace_view
from .store import CollectorStore
from observability.redact import content_capture_enabled
from runtime import obs_access
from runtime.vehicle_state import LEGACY_VEHICLE

logger = logging.getLogger("obs.collector")

SUBJECTS = (
    "vehicle.state.changed",
    "obs.span",
    "obs.metric",
    "obs.agent.health",
    "obs.turn",
    "obs.llm",
    "obs.log",
)
DEBUG_KEYS = {"speed_kmh", "battery", "gear", "location"}
#: 不带用户内容、不改状态的接口（健康、agent 维度计数与健康）；其余读写一律要运维凭据（`runtime/obs_access.py`）。
OPEN_PATHS = frozenset({"/healthz", "/metrics", "/api/agents"})
_FROM_ENV = object()
# 保留期清理周期（秒）。清理本体见 db.cleanup（badcase 与 gold 标注豁免）。
_CLEANUP_INTERVAL_S = 6 * 3600


class Hub:
    """Broadcast incremental observability events to connected dashboards."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.vehicles: dict[WebSocket, str] = {}
        self._locks: dict[WebSocket, asyncio.Lock] = {}
        self._observations: dict[WebSocket, dict] = {}

    async def join(self, websocket: WebSocket, vehicle_id=LEGACY_VEHICLE) -> None:
        """登记一个已接受、已认证的连接（接受与首帧认证在 `/stream` 里，认证前不推任何东西）。"""
        self.clients.add(websocket)
        self.vehicles[websocket] = vehicle_id
        self._locks[websocket] = asyncio.Lock()

    def leave(self, websocket: WebSocket) -> None:
        self.clients.discard(websocket)
        self.vehicles.pop(websocket, None)
        self._locks.pop(websocket, None)
        self._observations.pop(websocket, None)

    async def send_observation(self, websocket: WebSocket, store: CollectorStore, *, initial=False) -> None:
        lock = self._locks.get(websocket)
        if lock is None:
            return
        async with lock:
            vehicle_id = self.vehicles.get(websocket, "")
            # Read inside the send lock: a queued expiry must not overwrite a
            # newer NATS observation with a previously captured projection.
            observation = store.vehicle_states.view(vehicle_id)
            previous = self._observations.get(websocket)
            if not initial and previous == observation:
                return
            if initial:
                message = {"type": "snapshot", "vehicle_id": vehicle_id,
                           "vehicle_state": observation["state"], "vehicle_observation": observation,
                           "agents": store.agents,
                           "traces": [trace_view(trace) for trace in store.snapshot_traces(30)]}
            else:
                before, after = (previous or {}).get("state", {}), observation["state"]
                changes = [{"key": k, "old": before.get(k), "new": after.get(k)}
                           for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]
                message = {"type": "state_change", "vehicle_id": vehicle_id,
                           "observation": observation, "changes": changes}
            await websocket.send_text(json.dumps(message, ensure_ascii=False))
            self._observations[websocket] = observation

    async def broadcast_vehicle(self, store: CollectorStore, vehicle_id: str) -> None:
        for websocket in list(self.clients):
            if self.vehicles.get(websocket) != vehicle_id:
                continue
            try:
                await self.send_observation(websocket, store)
            except Exception:
                self.leave(websocket)

    async def broadcast(self, message: dict) -> None:
        text = json.dumps(message, ensure_ascii=False)
        for websocket in list(self.clients):
            if message.get("type") == "state_change" and message.get("vehicle_id") != self.vehicles.get(websocket, LEGACY_VEHICLE):
                continue
            try:
                lock = self._locks.get(websocket)
                if lock is not None:
                    async with lock:
                        await websocket.send_text(text)
            except Exception:
                self.leave(websocket)


def create_app(
    store: CollectorStore | None = None,
    hub: Hub | None = None,
    db: ObsDB | None = None,
    operator_key=_FROM_ENV,
) -> FastAPI:
    app = FastAPI(title="cockpit-observability-collector")
    app.state.operator_key = obs_access.key_from_env() if operator_key is _FROM_ENV else operator_key

    async def require_operator(request, call_next):
        if request.method == "OPTIONS" or request.url.path in OPEN_PATHS:
            return await call_next(request)
        if not app.state.operator_key:
            return JSONResponse({"error": "operator access not configured"}, status_code=503)
        token = obs_access.bearer(request.headers.get("authorization", ""))
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            obs_access.verify(app.state.operator_key, token)
        except obs_access.ObsTokenError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)

    # 先加的在内层：CORS 必须在最外层，401/403 才带得上 CORS 头（dashboard 读得到才会请运维者给令牌）、预检不被拦。
    app.add_middleware(BaseHTTPMiddleware, dispatch=require_operator)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = store or CollectorStore()
    app.state.hub = hub or Hub()
    app.state.db = db or ObsDB()
    app.state.nc = None

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "nats": app.state.nc is not None}

    @app.get("/api/meta")
    async def meta():
        # Runtime configuration is not a tombstone: a missing trace cannot be
        # labeled expired from the current retention window alone.
        return {
            "content_capture": content_capture_enabled(),
            "retention_days": float(os.getenv("OBS_RETENTION_DAYS", "7")),
            "debug_vehicle_control": os.getenv("DEBUG_VEHICLE_CONTROL", "true").lower() == "true",
            "query_features": ["turn_filters", "turn_pagination", "session_pagination"],
        }

    @app.get("/api/vehicle/state")
    async def vehicle_state(vehicle_id: str = LEGACY_VEHICLE):
        return app.state.store.vehicle_states.snapshot(vehicle_id)

    @app.get("/api/vehicle/observation")
    async def vehicle_observation(vehicle_id: str = LEGACY_VEHICLE):
        return app.state.store.vehicle_states.view(vehicle_id)

    @app.get("/api/traces")
    async def traces(limit: int = 50):
        return [trace_view(trace) for trace in app.state.store.snapshot_traces(limit)]

    @app.get("/api/traces/{trace_id}")
    async def trace(trace_id: str):
        value = app.state.store.traces.get(trace_id)
        return trace_view(value) if value else {"error": "not found"}

    @app.get("/api/agents")
    async def agents():
        return app.state.store.agents

    # ── 会话/轮次（badcase 排查主视图数据源，SQLite 持久） ──────────────

    @app.get("/api/sessions")
    async def sessions(limit: int = 50, q: str = "",
                       offset: int = Query(0, ge=0), paginated: bool = False,
                       origin: str = ""):
        return await asyncio.to_thread(app.state.db.sessions, limit, q,
                                       offset=offset, paginated=paginated, origin=origin)

    @app.get("/api/sessions/{session_id}/turns")
    async def session_turns(session_id: str, limit: int = 200):
        return await asyncio.to_thread(
            app.state.db.session_turns, session_id, limit)

    @app.get("/api/turns/{trace_id}")
    async def turn_detail(trace_id: str):
        detail = await asyncio.to_thread(app.state.db.turn_detail, trace_id)
        return detail or {"error": "not found"}

    @app.get("/api/search")
    async def search(q: str = "", status: str = "", session: str = "",
                     badcase: int = -1, since: int = 0, until: int = 0,
                     limit: int = 50,
                     offset: int = Query(0, ge=0), paginated: bool = False,
                     origin: str = "", category: str = "", outcome: str = "",
                     edge_disagreement: bool | None = None,
                     actionability_disagreement: bool | None = None,
                     degraded: bool | None = None, has_warnings: bool | None = None,
                     labeled: bool | None = None,
                     min_duration_ms: float = Query(0, ge=0)):
        return await asyncio.to_thread(
            app.state.db.search_turns, q, status, session,
            None if badcase < 0 else bool(badcase), since, until, limit,
            offset=offset, paginated=paginated, origin=origin, category=category,
            outcome=outcome, edge_disagreement=edge_disagreement,
            actionability_disagreement=actionability_disagreement, degraded=degraded,
            has_warnings=has_warnings, labeled=labeled, min_duration_ms=min_duration_ms)

    @app.post("/api/turns/{trace_id}/badcase")
    async def mark_badcase(trace_id: str, body: dict):
        ok = await asyncio.to_thread(
            app.state.db.set_badcase, trace_id,
            bool(body.get("badcase", True)), str(body.get("note", "") or ""))
        return {"ok": ok, "trace_id": trace_id}

    # ── 落域标注载体（数据飞轮 P0）：一次标注 = 评测用例 + 范例 + 训练标注的原料 ──

    @app.post("/api/turns/{trace_id}/label")
    async def label_turn(trace_id: str, body: dict):
        """正确落域标注：gold_intents 传 list 或逗号串；空=清除标注。"""
        raw = body.get("gold_intents", "")
        parts = raw if isinstance(raw, list) else str(raw or "").split(",")
        gold = ",".join(str(x).strip() for x in parts if str(x).strip())
        ok = await asyncio.to_thread(app.state.db.set_gold, trace_id, gold)
        return {"ok": ok, "trace_id": trace_id, "gold_intents": gold}

    # 注意：必须注册在 /api/export/{trace_id} 之前，否则 "labels" 会被当 trace_id 吞掉
    @app.get("/api/export/labels")
    async def export_labels(since: int = 0, until: int = 0, limit: int = 5000):
        """批量导出标注集（utterance → gold 落域 → 实际落域）。"""
        rows = await asyncio.to_thread(
            app.state.db.export_labels, since, until, limit)
        return {"exported_at": int(time.time() * 1000),
                "count": len(rows), "labels": rows}

    @app.get("/api/intents/observed")
    async def intents_observed():
        """已观测意图清单（实际落域 ∪ 已标注 gold），标注输入的候选源。"""
        return await asyncio.to_thread(app.state.db.observed_intents)

    @app.get("/api/logs")
    async def logs(trace_id: str = "", service: str = "", level: str = "",
                   q: str = "", limit: int = 200):
        return await asyncio.to_thread(
            app.state.db.query_logs, trace_id, service, level, q, limit)

    @app.get("/api/llm/summary")
    async def llm_summary(hours: float = 24):
        """LLM 消耗归属汇总（caller×model 分组），dashboard「LLM」视图数据源。
        窗口夹紧 [1h, 30d]，防误传拖垮全表扫描。"""
        return await asyncio.to_thread(
            app.state.db.llm_summary, min(max(hours, 1.0), 24.0 * 30))

    @app.get("/api/export/{trace_id}")
    async def export_turn(trace_id: str):
        """单轮全量 JSON（turn+spans+llm+logs）：一键素材，可直接贴 issue/回归用例。"""
        detail = await asyncio.to_thread(app.state.db.turn_detail, trace_id)
        if not detail:
            return {"error": "not found"}
        return {"exported_at": int(time.time() * 1000), **detail}

    @app.get("/metrics")
    async def metrics():
        return PlainTextResponse(
            render_prometheus_metrics(app.state.store),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.post("/api/debug/vehicle")
    async def debug_vehicle(body: dict):
        debug_enabled = (
            os.getenv("DEBUG_VEHICLE_CONTROL", "true").lower() == "true"
        )
        if not debug_enabled:
            return {"ok": False, "error": "debug disabled"}

        key = body.get("key")
        value = body.get("value")
        if key not in DEBUG_KEYS:
            return {"ok": False, "error": f"key not allowed: {key}"}

        if app.state.nc is not None:
            await app.state.nc.publish(
                "obs.debug.vehicle.set",
                json.dumps({"key": key, "value": value}).encode(),
            )
        return {"ok": True, "key": key, "value": value}

    @app.websocket("/stream")
    async def stream(websocket: WebSocket):
        dashboard_hub = app.state.hub
        vehicle_id = websocket.query_params.get("vehicle_id", LEGACY_VEHICLE)
        await websocket.accept()
        # 浏览器不能给 WebSocket 设头：首帧认证，限时内没有有效凭据就关，之前不推任何东西
        try:
            first = await asyncio.wait_for(websocket.receive_text(), timeout=obs_access.AUTH_FRAME_TIMEOUT_S)
            obs_access.verify(app.state.operator_key, obs_access.token_from_frame(first))
        except (asyncio.TimeoutError, obs_access.ObsTokenError, WebSocketDisconnect, RuntimeError):
            try:
                await websocket.close(code=1008)
            except Exception:
                pass
            return
        await dashboard_hub.join(websocket, vehicle_id)
        try:
            await dashboard_hub.send_observation(websocket, app.state.store, initial=True)
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=1)
                except asyncio.TimeoutError:
                    pass
                await dashboard_hub.send_observation(websocket, app.state.store)
        except WebSocketDisconnect:
            dashboard_hub.leave(websocket)
        except Exception:
            dashboard_hub.leave(websocket)

    return app


async def ingest_loop(app: FastAPI) -> None:
    """Subscribe to NATS and aggregate events; fail open when unavailable."""
    nats_url = os.getenv("NATS_URL", "")
    if not nats_url:
        logger.warning("NATS_URL unset; collector runs without live stream")
        return

    try:
        import nats

        connection = await nats.connect(
            nats_url,
            max_reconnect_attempts=-1,
        )
    except Exception as exc:
        logger.warning("collector NATS connect failed: %s", exc)
        return

    app.state.nc = connection
    store = app.state.store
    hub = app.state.hub

    db = app.state.db

    async def _persist(fn, event):
        """SQLite 落盘（best-effort）：持久层故障绝不拖垮实时流。"""
        try:
            await asyncio.to_thread(fn, event)
        except Exception as exc:
            logger.debug("obs db persist failed: %s", exc)

    async def handler(message):
        try:
            event = json.loads(message.data.decode())
        except Exception:
            return

        if message.subject == "vehicle.state.changed":
            result = store.apply_state(message.data)
            if result.accepted:
                await hub.broadcast_vehicle(store, result.vehicle_id)
        elif message.subject == "obs.span":
            store.apply_span(event)
            otel_bridge.export_span(event)  # T3.6: best-effort tee, no-op unless bridge active
            await _persist(db.insert_span, event)
            await hub.broadcast({"type": "span", **event})
        elif message.subject == "obs.metric":
            store.apply_metric(event)
            await hub.broadcast({"type": "metric", **event})
        elif message.subject == "obs.agent.health":
            store.apply_health(event)
            await hub.broadcast({"type": "health", **event})
        elif message.subject == "obs.turn":
            await _persist(db.insert_turn, event)
            await hub.broadcast({"type": "turn", **event})
        elif message.subject == "obs.llm":
            await _persist(db.insert_llm, event)
            await hub.broadcast({"type": "llm", **event})
        elif message.subject == "obs.log":
            await _persist(db.insert_log, event)
            await hub.broadcast({"type": "log", **event})

    for subject in SUBJECTS:
        await connection.subscribe(subject, cb=handler)
    logger.info("collector subscribed: %s", SUBJECTS)


async def cleanup_loop(app: FastAPI) -> None:
    """保留期清理（OBS_RETENTION_DAYS，默认 7 天；badcase 与 gold 标注豁免）。启动即清一次。"""
    while True:
        try:
            deleted = await asyncio.to_thread(app.state.db.cleanup)
            if deleted:
                logger.info("obs retention cleanup: %d turns removed", deleted)
        except Exception as exc:
            logger.warning("obs retention cleanup failed: %s", exc)
        await asyncio.sleep(_CLEANUP_INTERVAL_S)
