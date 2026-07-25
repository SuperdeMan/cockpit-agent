"""Registry manifests exposed by the in-vehicle fast executor."""
from __future__ import annotations

import logging
import os

import grpc

from cockpit.agent.v1 import agent_pb2
from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc

from runtime.grpcio import aio_channel
from edge_agents_mod.media import MEDIA_INTENTS
from edge_agents_mod.vehicle import VEHICLE_INTENTS

logger = logging.getLogger("edge.capabilities")


# M2 Outcome Verifier 首批声明（车控面试点）：VAL 说"执行成功"但状态没落地是**真实
# 发生过**的静默失败（scene Verify 首跑抓到 ambient_light.set 的亮度分支被提前 return
# 吞掉，四个预置场景的亮度从没生效过）。这里按 intent 声明期望态，云侧 executor 的通用
# 求值器对 NATS 车况镜像对账；镜像读不到 → UNKNOWN 不定罪。
# 状态键与 `orchestrator/edge/val.py` 的 `self.state` 同源。
# on_fail 一律 report：车控是副作用动作，**永不自动重放**（retry 只对查询步开放）。
_VERIFICATION = {
    "hvac.set": {"mirror": "vehicle_state", "keys": {"hvac_on": "true"}},
    "hvac.on": {"mirror": "vehicle_state", "keys": {"hvac_on": "true"}},
    "hvac.off": {"mirror": "vehicle_state", "keys": {"hvac_on": "false"}},
}


def _verification_for(intent: str):
    expect = _VERIFICATION.get(intent)
    if not expect:
        return None
    from google.protobuf.struct_pb2 import Struct
    s = Struct()
    s.update(expect)
    return agent_pb2.Verification(mode="state_match", timeout_ms=2500,
                                  on_fail="report", max_attempts=1, expect=s)


def _capabilities(intents: set[str], description: str):
    return [
        agent_pb2.Capability(
            intent=intent,
            description=description,
            examples=[],
            verification=_verification_for(intent),
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
    channel = aio_channel(addr)
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
