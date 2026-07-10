"""FastAPI service for observability snapshots and live event streaming."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from . import otel_bridge
from .db import ObsDB
from .metrics_export import render_prometheus_metrics
from .store import CollectorStore

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
# 保留期清理周期（秒）。清理本体见 db.cleanup（badcase 豁免）。
_CLEANUP_INTERVAL_S = 6 * 3600


class Hub:
    """Broadcast incremental observability events to connected dashboards."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def join(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.clients.add(websocket)

    def leave(self, websocket: WebSocket) -> None:
        self.clients.discard(websocket)

    async def broadcast(self, message: dict) -> None:
        text = json.dumps(message, ensure_ascii=False)
        for websocket in list(self.clients):
            try:
                await websocket.send_text(text)
            except Exception:
                self.clients.discard(websocket)


def create_app(
    store: CollectorStore | None = None,
    hub: Hub | None = None,
    db: ObsDB | None = None,
) -> FastAPI:
    app = FastAPI(title="cockpit-observability-collector")
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

    @app.get("/api/vehicle/state")
    async def vehicle_state():
        return app.state.store.vehicle_state

    @app.get("/api/traces")
    async def traces(limit: int = 50):
        return app.state.store.snapshot_traces(limit)

    @app.get("/api/traces/{trace_id}")
    async def trace(trace_id: str):
        return app.state.store.traces.get(trace_id) or {"error": "not found"}

    @app.get("/api/agents")
    async def agents():
        return app.state.store.agents

    # ── 会话/轮次（badcase 排查主视图数据源，SQLite 持久） ──────────────

    @app.get("/api/sessions")
    async def sessions(limit: int = 50, q: str = ""):
        return await asyncio.to_thread(app.state.db.sessions, limit, q)

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
                     limit: int = 50):
        return await asyncio.to_thread(
            app.state.db.search_turns, q, status, session,
            None if badcase < 0 else bool(badcase), since, until, limit)

    @app.post("/api/turns/{trace_id}/badcase")
    async def mark_badcase(trace_id: str, body: dict):
        ok = await asyncio.to_thread(
            app.state.db.set_badcase, trace_id,
            bool(body.get("badcase", True)), str(body.get("note", "") or ""))
        return {"ok": ok, "trace_id": trace_id}

    @app.get("/api/logs")
    async def logs(trace_id: str = "", service: str = "", level: str = "",
                   q: str = "", limit: int = 200):
        return await asyncio.to_thread(
            app.state.db.query_logs, trace_id, service, level, q, limit)

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
        await dashboard_hub.join(websocket)
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "snapshot",
                        "vehicle_state": app.state.store.vehicle_state,
                        "agents": app.state.store.agents,
                        "traces": app.state.store.snapshot_traces(30),
                    },
                    ensure_ascii=False,
                )
            )
            while True:
                await websocket.receive_text()
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
            store.apply_state(event)
            await hub.broadcast({"type": "state_change", **event})
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
    """保留期清理（OBS_RETENTION_DAYS，默认 7 天；badcase 豁免）。启动即清一次。"""
    while True:
        try:
            deleted = await asyncio.to_thread(app.state.db.cleanup)
            if deleted:
                logger.info("obs retention cleanup: %d turns removed", deleted)
        except Exception as exc:
            logger.warning("obs retention cleanup failed: %s", exc)
        await asyncio.sleep(_CLEANUP_INTERVAL_S)
