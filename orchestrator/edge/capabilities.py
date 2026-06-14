"""Registry manifests exposed by the in-vehicle fast executor."""
from __future__ import annotations

import logging
import os

import grpc

from cockpit.agent.v1 import agent_pb2
from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc

from edge_agents_mod.media import MEDIA_INTENTS
from edge_agents_mod.vehicle import VEHICLE_INTENTS

logger = logging.getLogger("edge.capabilities")


def _capabilities(intents: set[str], description: str):
    return [
        agent_pb2.Capability(
            intent=intent,
            description=description,
            examples=[],
        )
        for intent in sorted(intents)
    ]


def build_edge_manifests() -> list[agent_pb2.AgentManifest]:
    return [
        agent_pb2.AgentManifest(
            agent_id="edge-vehicle",
            version="1.0.0",
            display_name="车端快思考-车控",
            category="core",
            trust_level="system",
            deployment="edge",
            latency_budget_ms=800,
            kind="edge_fast",
            capabilities=_capabilities(
                VEHICLE_INTENTS, "通过车端 VAL 执行确定性车控意图"),
            requires_permissions=["vehicle.control"],
            edge_intents=sorted(VEHICLE_INTENTS),
        ),
        agent_pb2.AgentManifest(
            agent_id="edge-media",
            version="1.0.0",
            display_name="车端快思考-媒体",
            category="core",
            trust_level="system",
            deployment="edge",
            latency_budget_ms=500,
            kind="edge_fast",
            capabilities=_capabilities(
                MEDIA_INTENTS, "通过车端执行器控制本地媒体"),
            requires_permissions=["media.control"],
            edge_intents=sorted(MEDIA_INTENTS),
        ),
    ]


async def register_edge_capabilities():
    """Best-effort capability registration; execution still requires an active vehicle stream."""
    addr = os.getenv("REGISTRY_ADDR", "registry:50051")
    channel = grpc.aio.insecure_channel(addr)
    stub = registry_pb2_grpc.RegistryStub(channel)
    try:
        for manifest in build_edge_manifests():
            await stub.Register(
                registry_pb2.RegisterRequest(
                    manifest=manifest,
                    endpoint="edge://vehicle",
                ),
                timeout=5,
            )
            logger.info("Registered edge capability %s", manifest.agent_id)
    finally:
        await channel.close()
