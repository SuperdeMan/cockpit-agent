"""Agent 注册表 + 能力路由。

Phase 1 改进：健康探测 + 自动摘除 + 路由打分增强。
路由打分：intent 精确命中=1.0；否则按 query 在 capabilities/examples/description 的关键词命中打分。
权限过滤：调用方 granted_permissions 必须覆盖 Agent 的 requires_permissions（granted 为空表示不过滤）。

ws2 P0：新增 PgStore——PostgreSQL 持久化，Registry 重启秒恢复。接口与 Store 一致，
内存缓存 + 定期刷新，PostgreSQL 不可用时回退内存模式。
"""
from __future__ import annotations
import json
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

    def all(self) -> list[Record]:
        """Return every record, including agents currently marked unhealthy."""
        return list(self._agents.values())


# ── ws2 P0: PostgreSQL 持久化注册表 ──────────────────────────────────────

class PgStore(Store):
    """PostgreSQL 持久化注册表，接口与 Store 一致。

    启动时从 PostgreSQL 加载全量到内存；写操作双写（内存 + PG）。
    PG 不可用时降级为纯内存模式（与 Store 行为一致）。

    ws2 P1: 语义路由——capabilities 向量化 + pgvector cosine 检索。
    """

    def __init__(self, dsn: str):
        super().__init__()
        self._dsn = dsn
        self._pool = None  # asyncpg pool, lazy init
        self._pg_ok = False
        self._embedder = None  # lazy init embedding model

    async def init(self) -> bool:
        """初始化 PostgreSQL 连接池并加载全量注册表。返回是否成功。"""
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
            await self._ensure_schema()
            await self._load_all()
            self._pg_ok = True
            self._init_embedder()
            logger.info("PgStore: PostgreSQL connected, loaded %d agents", len(self._agents))
            return True
        except Exception as e:
            logger.warning("PgStore: PostgreSQL unavailable, falling back to memory: %s", e)
            self._pg_ok = False
            return False

    def _init_embedder(self):
        """初始化 embedding 模型（可选）。优先用 sentence-transformers，否则 hash-based fallback。"""
        try:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer("BAAI/bge-small-zh-v1.5")
            logger.info("PgStore: loaded bge-small-zh embedding model")
        except Exception:
            self._embedder = None
            logger.info("PgStore: no embedding model, using hash-based fallback")

    def _embed(self, text: str) -> list[float] | None:
        """生成文本的 embedding 向量。"""
        if not text:
            return None
        if self._embedder:
            try:
                vec = self._embedder.encode(text, normalize_embeddings=True)
                return vec.tolist()
            except Exception:
                pass
        # hash-based fallback（384 维，与 schema 一致）
        import hashlib
        h = hashlib.sha256(text.encode()).digest()
        # 扩展到 384 维
        vec = []
        for i in range(384):
            byte_val = h[i % len(h)]
            vec.append((byte_val / 128.0) - 1.0)  # 归一化到 [-1, 1]
        return vec

    def _capabilities_text(self, manifest) -> str:
        """从 manifest 提取 capabilities 文本用于向量化。"""
        parts = []
        for cap in getattr(manifest, "capabilities", []):
            parts.append(getattr(cap, "intent", ""))
            parts.append(getattr(cap, "description", ""))
            parts.extend(getattr(cap, "examples", []))
        return " ".join(p for p in parts if p)

    async def _ensure_schema(self):
        """确保 agents 表存在（含 embedding 列）。"""
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id      VARCHAR(64) PRIMARY KEY,
                    manifest      JSONB NOT NULL,
                    endpoint      VARCHAR(256) NOT NULL,
                    lease_id      VARCHAR(64),
                    registered_at TIMESTAMPTZ DEFAULT now(),
                    last_heartbeat TIMESTAMPTZ DEFAULT now(),
                    status        VARCHAR(16) DEFAULT 'healthy'
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status)")
            # P1: 确保 embedding 列存在（ALTER TABLE IF NOT EXISTS 不标准，用 try）
            try:
                await conn.execute(
                    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS embedding vector(384)")
            except Exception:
                pass  # 列已存在或 pgvector 未安装

    async def _load_all(self):
        """从 PostgreSQL 加载全量注册表到内存。"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM agents")
        for row in rows:
            manifest_dict = json.loads(row["manifest"]) if isinstance(row["manifest"], str) else row["manifest"]
            # 构造一个轻量 manifest 对象，兼容 Store 的属性访问
            manifest = _dict_to_manifest(manifest_dict)
            rec = Record(
                manifest=manifest,
                endpoint=row["endpoint"],
                lease_id=row["lease_id"] or "",
                last_seen=row["last_heartbeat"].timestamp() if row["last_heartbeat"] else time.time(),
                fail_count=0,
                healthy=(row["status"] == "healthy"),
            )
            self._agents[row["agent_id"]] = rec

    async def register(self, manifest, endpoint: str) -> str:
        """幂等注册：内存 + PostgreSQL 双写（含 embedding 向量化）。"""
        lease = super().register(manifest, endpoint)
        if self._pg_ok:
            try:
                manifest_json = json.dumps(_manifest_to_dict(manifest), ensure_ascii=False)
                cap_text = self._capabilities_text(manifest)
                embedding = self._embed(cap_text)
                async with self._pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO agents (agent_id, manifest, endpoint, lease_id, last_heartbeat, status, embedding)
                        VALUES ($1, $2, $3, $4, now(), 'healthy', $5::vector)
                        ON CONFLICT (agent_id) DO UPDATE SET
                            manifest = EXCLUDED.manifest,
                            endpoint = EXCLUDED.endpoint,
                            lease_id = EXCLUDED.lease_id,
                            last_heartbeat = now(),
                            status = 'healthy',
                            embedding = EXCLUDED.embedding
                    """, manifest.agent_id, manifest_json, endpoint, lease,
                         str(embedding) if embedding else None)
            except Exception as e:
                logger.warning("PgStore: register PG write failed: %s", e)
        return lease

    async def deregister(self, agent_id: str):
        """注销：内存 + PostgreSQL 双删。"""
        super().deregister(agent_id)
        if self._pg_ok:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute("DELETE FROM agents WHERE agent_id = $1", agent_id)
            except Exception as e:
                logger.warning("PgStore: deregister PG delete failed: %s", e)

    async def mark_healthy(self, agent_id: str):
        """健康探测成功：内存 + PostgreSQL 双更新。"""
        super().mark_healthy(agent_id)
        if self._pg_ok:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE agents SET last_heartbeat = now(), status = 'healthy'
                        WHERE agent_id = $1
                    """, agent_id)
            except Exception as e:
                logger.debug("PgStore: mark_healthy PG update failed: %s", e)

    async def mark_unhealthy(self, agent_id: str):
        """健康探测失败：内存更新 + PostgreSQL 更新 status。"""
        super().mark_unhealthy(agent_id)
        rec = self._agents.get(agent_id)
        if rec and not rec.healthy and self._pg_ok:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE agents SET status = 'unhealthy' WHERE agent_id = $1
                    """, agent_id)
            except Exception as e:
                logger.debug("PgStore: mark_unhealthy PG update failed: %s", e)

    async def resolve_semantic(self, query: str, top_k: int = 3,
                               granted: list[str] | None = None) -> list[tuple]:
        """P1 语义路由：query 向量化 → pgvector cosine 检索 top_k。

        仅在有 embedding 模型且 pgvector 可用时生效。
        返回 [(Record, score), ...] 按相似度降序。
        """
        if not self._pg_ok or not query:
            return []

        query_embedding = self._embed(query)
        if not query_embedding:
            return []

        try:
            async with self._pool.acquire() as conn:
                # pgvector cosine 检索（1 - cosine_distance = cosine_similarity）
                rows = await conn.fetch("""
                    SELECT agent_id, manifest, endpoint, lease_id, last_heartbeat, status,
                           1 - (embedding <=> $1::vector) AS similarity
                    FROM agents
                    WHERE status = 'healthy' AND embedding IS NOT NULL
                    ORDER BY embedding <=> $1::vector
                    LIMIT $2
                """, str(query_embedding), top_k)

            results = []
            for row in rows:
                manifest_dict = json.loads(row["manifest"]) if isinstance(row["manifest"], str) else row["manifest"]
                manifest = _dict_to_manifest(manifest_dict)
                rec = Record(
                    manifest=manifest,
                    endpoint=row["endpoint"],
                    lease_id=row["lease_id"] or "",
                    last_seen=row["last_heartbeat"].timestamp() if row["last_heartbeat"] else time.time(),
                    fail_count=0,
                    healthy=(row["status"] == "healthy"),
                )
                # 权限过滤
                if granted and not self._permitted(manifest, granted):
                    continue
                score = float(row["similarity"])
                results.append((rec, score))
            return results
        except Exception as e:
            logger.debug("PgStore: semantic resolve failed: %s", e)
            return []


def _manifest_to_dict(manifest) -> dict:
    """将 manifest proto/dataclass 转为可序列化的 dict。"""
    if hasattr(manifest, "DESCRIPTOR"):  # protobuf
        from google.protobuf.json_format import MessageToDict
        return MessageToDict(manifest, preserving_proto_field_name=True)
    # dataclass or SimpleNamespace
    d = {}
    for k in ("agent_id", "version", "display_name", "category", "trust_level",
              "deployment", "latency_budget_ms", "fallback"):
        d[k] = getattr(manifest, k, "")
    caps = []
    for c in getattr(manifest, "capabilities", []):
        cap = {}
        for ck in ("intent", "description", "examples", "require_confirm"):
            cap[ck] = getattr(c, ck, None if ck != "examples" else [])
        cap["slots"] = list(getattr(c, "slots", []))
        caps.append(cap)
    d["capabilities"] = caps
    d["requires_permissions"] = list(getattr(manifest, "requires_permissions", []))
    return d


def _dict_to_manifest(d: dict):
    """将 dict 还原为 AgentManifest proto。

    必须是 proto（不能是 SimpleNamespace）：registry server 会把它塞进
    `ResolvedAgent.manifest`（proto 字段），SimpleNamespace 赋值会抛 TypeError，
    使「重启恢复 / 语义路由」在序列化时失败。proto 同样支持 Store 的属性访问
    （manifest.requires_permissions / cap.intent 等），不影响打分与过滤。
    """
    from cockpit.agent.v1 import agent_pb2
    caps = [
        agent_pb2.Capability(
            intent=c.get("intent", ""),
            description=c.get("description", ""),
            slots=list(c.get("slots") or []),
            examples=list(c.get("examples") or []),
            require_confirm=bool(c.get("require_confirm", False)),
        )
        for c in d.get("capabilities", [])
    ]
    return agent_pb2.AgentManifest(
        agent_id=d.get("agent_id", ""),
        version=d.get("version", ""),
        display_name=d.get("display_name", ""),
        category=d.get("category", ""),
        trust_level=d.get("trust_level", "first_party"),
        deployment=d.get("deployment", "cloud"),
        latency_budget_ms=int(d.get("latency_budget_ms") or 0),
        fallback=d.get("fallback", ""),
        capabilities=caps,
        requires_permissions=list(d.get("requires_permissions") or []),
        edge_intents=list(d.get("edge_intents") or []),
        kind=d.get("kind", ""),
    )
