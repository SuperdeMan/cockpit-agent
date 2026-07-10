"""Observability collector process entry point."""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from observability.collector import otel_bridge
from observability.collector.server import cleanup_loop, create_app, ingest_loop

logging.basicConfig(
    level=getattr(
        logging,
        os.getenv("LOG_LEVEL", "info").upper(),
        logging.INFO,
    )
)

app = create_app()


@app.on_event("startup")
async def _startup() -> None:
    otel_bridge.init_otel_bridge()  # T3.6: no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set
    asyncio.create_task(ingest_loop(app))
    asyncio.create_task(cleanup_loop(app))  # 保留期清理（badcase 豁免）


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("OBS_COLLECTOR_PORT", "8092")),
    )
