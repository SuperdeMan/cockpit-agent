"""Active health probing for registered Agent gRPC endpoints."""
from __future__ import annotations

import asyncio
import inspect

from registry.store import HEALTH_TIMEOUT


async def probe_endpoint(endpoint: str, timeout: float = HEALTH_TIMEOUT) -> bool:
    from cockpit.agent.v1 import agent_pb2, agent_pb2_grpc
    from runtime.grpcio import aio_channel

    channel = aio_channel(endpoint)
    try:
        response = await agent_pb2_grpc.AgentStub(channel).Health(
            agent_pb2.HealthRequest(),
            timeout=timeout,
        )
        return response.status == agent_pb2.HealthResponse.SERVING
    except Exception:
        return False
    finally:
        await channel.close()


async def probe_all(store, probe=None) -> None:
    """Probe all current records concurrently and update routing health."""
    checker = probe or probe_endpoint
    records = [
        record for record in store.all() if "://" not in record.endpoint
    ]

    async def check(record):
        agent_id = record.manifest.agent_id
        try:
            healthy = await checker(record.endpoint)
        except Exception:
            healthy = False
        if healthy:
            result = store.mark_healthy(agent_id)
        else:
            result = store.mark_unhealthy(agent_id)
        # PgStore mark methods are async; await if needed
        if inspect.isawaitable(result):
            await result

    await asyncio.gather(*(check(record) for record in records))
