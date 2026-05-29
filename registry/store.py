"""Agent 注册表 + 能力路由。

Phase 1 改进：健康探测 + 自动摘除 + 路由打分增强。
路由打分：intent 精确命中=1.0；否则按 query 在 capabilities/examples/description 的关键词命中打分。
权限过滤：调用方 granted_permissions 必须覆盖 Agent 的 requires_permissions（granted 为空表示不过滤）。
"""
from __future__ import annotations
import time
import uuid
import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("registry.store")

# 健康探测参数
HEALTH_CHECK_INTERVAL = 10  # 秒
HEALTH_TIMEOUT = 5          # 秒
MAX_FAIL_COUNT = 3          # 连续失败次数阈值


@dataclass
class Record:
    manifest: object
    endpoint: str
    lease_id: str
    last_seen: float = field(default_factory=time.time)
    fail_count: int = 0
    healthy: bool = True


class Store:
    def __init__(self):
        self._agents: dict[str, Record] = {}

    def register(self, manifest, endpoint: str) -> str:
        lease = uuid.uuid4().hex
        self._agents[manifest.agent_id] = Record(
            manifest=manifest, endpoint=endpoint, lease_id=lease,
            last_seen=time.time(), fail_count=0, healthy=True,
        )
        logger.info("Registered %s @ %s (lease=%s)", manifest.agent_id, endpoint, lease[:8])
        return lease

    def deregister(self, agent_id: str):
        if agent_id in self._agents:
            logger.info("Deregistered %s", agent_id)
            del self._agents[agent_id]

    def mark_healthy(self, agent_id: str):
        """健康探测成功，重置失败计数。"""
        rec = self._agents.get(agent_id)
        if rec:
            rec.last_seen = time.time()
            rec.fail_count = 0
            rec.healthy = True

    def mark_unhealthy(self, agent_id: str):
        """健康探测失败，累加失败计数。超阈值标记不健康。"""
        rec = self._agents.get(agent_id)
        if rec:
            rec.fail_count += 1
            if rec.fail_count >= MAX_FAIL_COUNT:
                rec.healthy = False
                logger.warning("Agent %s marked unhealthy (fail_count=%d)", agent_id, rec.fail_count)

    def get_healthy_agents(self) -> list[Record]:
        """返回所有健康的 Agent。"""
        return [r for r in self._agents.values() if r.healthy]

    @staticmethod
    def _permitted(manifest, granted: list[str]) -> bool:
        if not granted:
            return True
        return all(p in granted for p in manifest.requires_permissions)

    @staticmethod
    def _score(manifest, intent: str, query: str) -> float:
        score = 0.0
        for cap in manifest.capabilities:
            if intent and cap.intent == intent:
                return 1.0
            if query:
                hay = " ".join([cap.intent, cap.description, *cap.examples])
                hits = sum(1 for ch in set(query) if ch.strip() and ch in hay)
                if hits:
                    score = max(score, 0.3 + 0.05 * hits)
        if not intent and not query:
            return 0.5  # 全量列举场景
        return score

    def resolve(self, intent: str, query: str, top_k: int, granted: list[str]):
        scored = []
        for rec in self._agents.values():
            if not rec.healthy:
                continue
            if not self._permitted(rec.manifest, granted):
                continue
            s = self._score(rec.manifest, intent, query)
            if s > 0:
                scored.append((rec, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k] if top_k else scored

    def list(self, category: str):
        return [r for r in self._agents.values()
                if r.healthy and (not category or r.manifest.category == category)]
