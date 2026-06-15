"""Observability collector process entry point."""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from observability.collector.server import create_app, ingest_loop

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
    asyncio.create_task(ingest_loop(app))


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("OBS_COLLECTOR_PORT", "8092")),
    )
