"""FastAPI service for observability snapshots and live event streaming."""
from __future__ import annotations

import json
import logging
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from . import otel_bridge
from .metrics_export import render_prometheus_metrics
from .store import CollectorStore

logger = logging.getLogger("obs.collector")

SUBJECTS = (
    "vehicle.state.changed",
    "obs.span",
    "obs.metric",
    "obs.agent.health",
)
DEBUG_KEYS = {"speed_kmh", "battery", "gear", "location"}


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
            await hub.broadcast({"type": "span", **event})
        elif message.subject == "obs.metric":
            store.apply_metric(event)
            await hub.broadcast({"type": "metric", **event})
        elif message.subject == "obs.agent.health":
            store.apply_health(event)
            await hub.broadcast({"type": "health", **event})

    for subject in SUBJECTS:
        await connection.subscribe(subject, cb=handler)
    logger.info("collector subscribed: %s", SUBJECTS)
