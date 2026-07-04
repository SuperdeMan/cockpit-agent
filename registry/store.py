"""Agent 注册表 + 能力路由。

Phase 1 改进：健康探测 + 自动摘除 + 路由打分增强。
路由打分：intent 精确命中=1.0；否则按 query 在 capabilities/examples/description 的关键词命中打分。
权限过滤：调用方 granted_permissions 必须覆盖 Agent 的 requires_permissions（granted 为空表示不过滤）。

ws2 P0：新增 PgStore——PostgreSQL 持久化，Registry 重启秒恢复。接口与 Store 一致，
内存缓存 + 定期刷新，PostgreSQL 不可用时回退内存模式。
"""
from __future__ import annotations
import hashlib
import json
import os
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

# R4.1 P0 语义路由参数
# 向量维度：与 llm-gateway 向百炼请求的输出维度对齐（默认 1024，同 memory）。
EMBED_DIM = int(os.getenv("LLM_EMBED_DIMENSIONS", "1024"))
# 语义相似度下限：低于此值的候选一律丢弃（防弱向量源把随机 Agent 追加进候选，见 §1.1 bug）。
SEMANTIC_MIN_SIM = float(os.getenv("SEMANTIC_MIN_SIM", "0.35"))
# 语义提升阈值（R4.1 语义重排）：语义 top-1 相似度 ≥ 此值时，语义排序在关键词之前——纠正
# 关键词字符打分对中文的噪声 top-1（实测纯语义 20/20 全对、5 条 requires_embed 分 0.508~0.752）；
# 低于此值则保守追加在关键词之后（关键词 top 不变）。精确 intent 命中（1.0）永不被语义覆盖。
SEMANTIC_PROMOTE_SIM = float(os.getenv("SEMANTIC_PROMOTE_SIM", "0.5"))
_EMBED_TIMEOUT_REGISTER = 5.0   # 注册路径 embed 超时（稳态因 text_hash 去重≈0 次调用）
_EMBED_TIMEOUT_QUERY = 1.5      # 查询路径 embed 超时（失败即空，关键词结果原样生效）
_QUERY_CACHE_MAX = 128
_QUERY_CACHE_TTL = 300          # 秒


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

    R4.1 P0: 语义路由——**按 capability 粒度**向量化（一个 Agent 一条混合向量会把
    info 的天气/股票/新闻搅在一起，检索粒度必须到 capability），embedding 经 llm-gateway
    → 百炼 text-embedding-v4（与 memory 同源）。无 embedding 源 / llm-gateway 不可达 /
    PG 不可达时：**行为与纯关键词路径完全一致**（语义分支静默缺席，绝不哈希伪语义）。
    """

    def __init__(self, dsn: str):
        super().__init__()
        self._dsn = dsn
        self._pool = None  # asyncpg pool, lazy init
        self._pg_ok = False
        self._llm_addr = os.getenv("LLM_GATEWAY_ADDR", "")
        self._embed_source = None            # "llm" | None（None=无源，语义路由禁用）
        self._embed_probe_done = False       # 探测是否已定论（不可达时保持 False 以便重探）
        self._query_cache: dict[str, tuple] = {}   # query -> (embedding, expiry_ts)
        self._query_cache_order: list[str] = []     # FIFO 淘汰序
        # capability 向量化后台任务（agent_id → Task）：register 只调度不等待
        # （内联等待会被注册客户端 5s deadline 取消——2026-07-04 embed 泄漏根因），
        # 兼作同 agent 在飞去重。
        self._embed_tasks: dict[str, asyncio.Task] = {}

    async def init(self) -> bool:
        """初始化 PostgreSQL 连接池并加载全量注册表。返回是否成功。"""
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=5,
                command_timeout=10, max_inactive_connection_lifetime=300)
            await self._ensure_schema()
            await self._load_all()
            self._pg_ok = True
            self._embed_probe_done = False
            await self._probe_embedder()
            logger.info("PgStore: PostgreSQL connected, loaded %d agents", len(self._agents))
            return True
        except Exception as e:
            logger.warning("PgStore: PostgreSQL unavailable, falling back to memory: %s", e)
            self._pg_ok = False
            return False

    async def _probe_embedder(self):
        """探测 llm-gateway embedding 源（唯一 embedding 出口，同 memory）。

        三态收敛：
          - 返回正确维度向量 → `_embed_source="llm"`（定论，停止重探）。
          - 返回向量但维度不符（llm-gateway 无 embed key 回退 384 维 mock）→ `None`（定论，
            停止重探）——nightly 纯 mock 下仅此一次探测、零后续感知。
          - 返回 None（llm-gateway 暂不可达）→ 不判死刑，留待注册路径按需重探（兜底
            llm-gateway 晚于 registry 就绪的启动时序）。
        """
        if not self._llm_addr:
            self._embed_source = None
            self._embed_probe_done = True
            return
        v = await self._llm_embed(["探测"], timeout=_EMBED_TIMEOUT_REGISTER)
        if not v or not v[0]:
            self._embed_source = None            # 暂不可达：probe_done 保持 False，可重探
            return
        if len(v[0]) == EMBED_DIM:
            self._embed_source = "llm"
            self._embed_probe_done = True
            logger.info("PgStore: embedding via llm-gateway (dim=%d)", EMBED_DIM)
        else:
            self._embed_source = None
            self._embed_probe_done = True
            logger.info("PgStore: llm-gateway embedding dim=%d != %d（无真实源）→ 语义路由禁用",
                        len(v[0]), EMBED_DIM)

    async def _llm_embed(self, texts: list[str], timeout: float) -> list[list[float]] | None:
        """经 llm-gateway Embed RPC 向量化（唯一 embedding 出口，模式照抄 memory）。失败返回 None。"""
        try:
            from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
            from runtime.grpcio import aio_channel
            async with aio_channel(self._llm_addr) as ch:
                stub = llm_pb2_grpc.LLMGatewayStub(ch)
                resp = await stub.Embed(llm_pb2.EmbedRequest(texts=list(texts)), timeout=timeout)
                return [list(e.values) for e in resp.embeddings]
        except Exception as e:
            logger.debug("PgStore: llm-gateway embed failed: %s", e)
            return None

    async def _embed(self, text: str, timeout: float) -> list[float] | None:
        """文本向量化：仅经 llm-gateway；无源/维度不符返回 None（不写向量、不语义检索）。"""
        if not text or self._embed_source != "llm":
            return None
        v = await self._llm_embed([text], timeout=timeout)
        return v[0] if v and v[0] and len(v[0]) == EMBED_DIM else None

    async def _embed_query_cached(self, query: str) -> list[float] | None:
        """query 向量化 + FIFO/TTL 缓存（128 条 / 5min），避免热点 query 反复打 embed API。"""
        now = time.time()
        hit = self._query_cache.get(query)
        if hit and hit[1] > now:
            return hit[0]
        emb = await self._embed(query, timeout=_EMBED_TIMEOUT_QUERY)
        if emb is not None:
            if query in self._query_cache:      # 过期重算：先移除旧 FIFO 位，避免重复项提前淘汰有效条目
                try:
                    self._query_cache_order.remove(query)
                except ValueError:
                    pass
            self._query_cache[query] = (emb, now + _QUERY_CACHE_TTL)
            self._query_cache_order.append(query)
            while len(self._query_cache_order) > _QUERY_CACHE_MAX:
                self._query_cache.pop(self._query_cache_order.pop(0), None)
        return emb

    @staticmethod
    def _capability_text(cap) -> str:
        """单条 capability 的向量化文本 = intent + description + examples（检索粒度到 capability）。"""
        parts = [getattr(cap, "intent", ""), getattr(cap, "description", "")]
        parts.extend(getattr(cap, "examples", []) or [])
        return " ".join(p for p in parts if p)

    async def _ensure_schema(self):
        """确保 agents 表 + agent_capability_vec 表存在。

        agent_capability_vec 用 EMBED_DIM 维向量；若既有表维度不符**直接 DROP 重建**——
        向量由 Agent 周期重注册 10s 内自动回填，是本仓独有的零成本迁移（无数据搬迁）。
        R4.1 前的 `agents.embedding`（hash 伪向量）停写停读、保留不迁移（不再 ADD 该列，
        既有库的旧列原样留存、无人读写）。
        """
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
            try:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            except Exception as e:
                logger.debug("PgStore: create vector ext: %s", e)
            existing_dim = await self._cap_vec_dim(conn)
            if existing_dim is not None and existing_dim != EMBED_DIM:
                logger.warning("PgStore: agent_capability_vec dim %d != %d，DROP 重建",
                               existing_dim, EMBED_DIM)
                await conn.execute("DROP TABLE IF EXISTS agent_capability_vec")
            try:
                await conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS agent_capability_vec (
                        agent_id  VARCHAR(64) NOT NULL,
                        intent    VARCHAR(128) NOT NULL,
                        text_hash VARCHAR(64) NOT NULL,
                        embedding vector({EMBED_DIM}),
                        PRIMARY KEY (agent_id, intent)
                    )
                """)
            except Exception as e:
                logger.warning("PgStore: create agent_capability_vec failed (pgvector 未装?): %s", e)

    @staticmethod
    async def _cap_vec_dim(conn) -> int | None:
        """读 agent_capability_vec.embedding 的向量维度；表/列不存在或未定维返回 None。
        pgvector 把维度直接存进 atttypmod（不像 varchar 有 +4 offset）。"""
        row = await conn.fetchrow("""
            SELECT a.atttypmod AS dim
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = 'agent_capability_vec' AND a.attname = 'embedding'
              AND a.attnum > 0 AND NOT a.attisdropped AND pg_table_is_visible(c.oid)
        """)
        if not row or row["dim"] is None:
            return None
        return int(row["dim"]) if int(row["dim"]) > 0 else None

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
        """幂等注册：内存 + PostgreSQL 双写；capability 向量化**后台任务**增量写入。

        向量化绝不能内联在 Register RPC 里等（2026-07-04 embed 泄漏事故根因）：
        edge-vehicle 74 caps 串行 embed ~20s+，超过注册客户端 timeout(5s) → grpc.aio 取消
        handler 协程 → CancelledError（BaseException）绕过 except Exception 静默逃逸 →
        向量永远写不进 PG → 每 10s 周期重注册对同一批 cap 全量重 embed（上游已计费）→
        无限烧 API（实测 ~1.5-2 次/秒、每小时数千次调用）。
        """
        lease = super().register(manifest, endpoint)
        if self._pg_ok:
            try:
                manifest_json = json.dumps(_manifest_to_dict(manifest), ensure_ascii=False)
                async with self._pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO agents (agent_id, manifest, endpoint, lease_id, last_heartbeat, status)
                        VALUES ($1, $2, $3, $4, now(), 'healthy')
                        ON CONFLICT (agent_id) DO UPDATE SET
                            manifest = EXCLUDED.manifest,
                            endpoint = EXCLUDED.endpoint,
                            lease_id = EXCLUDED.lease_id,
                            last_heartbeat = now(),
                            status = 'healthy'
                    """, manifest.agent_id, manifest_json, endpoint, lease)
            except Exception as e:
                logger.warning("PgStore: register PG write failed: %s", e)
            self._schedule_embed(manifest)
        return lease

    def _schedule_embed(self, manifest):
        """把 capability 向量化调度为 store 自有的后台任务（生命周期=进程，不随 RPC 取消）。

        同 agent 在飞去重：上一轮任务未完成时跳过本轮（10s 重注册快于大 manifest 的
        向量化时长，否则会堆叠重复任务重复计费）；已写入的 text_hash 让下一轮的缺口
        单调收敛。"""
        if self._embed_source != "llm" and self._embed_probe_done:
            return  # 已定论无 embedding 源（如 nightly 纯 mock）：不起空转任务
        agent_id = manifest.agent_id
        old = self._embed_tasks.get(agent_id)
        if old is not None and not old.done():
            return
        task = asyncio.create_task(self._embed_capabilities_safe(manifest))
        self._embed_tasks[agent_id] = task

        def _cleanup(t, aid=agent_id):
            if self._embed_tasks.get(aid) is t:
                self._embed_tasks.pop(aid, None)

        task.add_done_callback(_cleanup)

    async def _embed_capabilities_safe(self, manifest):
        """后台任务包装：异常自吞并告警（任务无人 await，不能让异常变 'never retrieved'）。"""
        try:
            await self._embed_capabilities(manifest)
        except Exception as e:
            logger.warning("PgStore: background embed for %s failed: %s",
                           manifest.agent_id, e)

    async def _embed_capabilities(self, manifest):
        """按 capability 粒度增量向量化写入 agent_capability_vec。

        text_hash（sha256）去重：Agent 每 ~10s 周期重注册，未变化的 capability 直接跳过，
        稳态 embed API 调用≈0（仅 manifest 变更时才打）。无 embedding 源时静默不写
        （语义路由缺席，行为回落关键词路径，nightly 纯 mock 零感知）。
        每条 embed 成功即**写穿**入库（部分失败/进程中断也保住进度，缺口单调收敛，
        绝不重演「全量成功才落库→一条失败全轮重烧」）。
        """
        # 启动时序兜底：llm-gateway 晚于 registry 就绪时 init 探测会失败，此处按需重探。
        if self._embed_source != "llm" and not self._embed_probe_done and self._llm_addr:
            await self._probe_embedder()
        if self._embed_source != "llm":
            return
        agent_id = manifest.agent_id
        caps = [c for c in (getattr(manifest, "capabilities", []) or []) if getattr(c, "intent", "")]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT intent, text_hash FROM agent_capability_vec WHERE agent_id = $1", agent_id)
        existing_hash = {r["intent"]: r["text_hash"] for r in rows}
        want_intents = {getattr(c, "intent", "") for c in caps}

        pending = [c for c in caps
                   if existing_hash.get(getattr(c, "intent", ""))
                   != hashlib.sha256(self._capability_text(c).encode()).hexdigest()]
        written = failed = 0
        for cap in pending:
            intent = getattr(cap, "intent", "")
            text = self._capability_text(cap)
            th = hashlib.sha256(text.encode()).hexdigest()
            emb = await self._embed(text, timeout=_EMBED_TIMEOUT_REGISTER)
            if emb is None:
                failed += 1
                continue  # 本条失败：留待下轮（缺口单调收敛）
            # 写穿：单条成功立刻落库，进程中断/后续失败不丢已花钱的进度
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO agent_capability_vec (agent_id, intent, text_hash, embedding)
                    VALUES ($1, $2, $3, $4::vector)
                    ON CONFLICT (agent_id, intent) DO UPDATE SET
                        text_hash = EXCLUDED.text_hash, embedding = EXCLUDED.embedding
                """, agent_id, intent, th, str(emb))
            written += 1
        if pending:
            logger.info("PgStore: embedded %d/%d capabilities for %s (%d failed, retry next cycle)",
                        written, len(pending), agent_id, failed)

        stale = [i for i in existing_hash if i not in want_intents]
        if stale:  # manifest 删掉的 capability 级联清理
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM agent_capability_vec WHERE agent_id = $1 AND intent = ANY($2::text[])",
                    agent_id, stale)

    async def deregister(self, agent_id: str):
        """注销：内存 + PostgreSQL 双删。"""
        super().deregister(agent_id)
        if self._pg_ok:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute("DELETE FROM agents WHERE agent_id = $1", agent_id)
                    await conn.execute(
                        "DELETE FROM agent_capability_vec WHERE agent_id = $1", agent_id)
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
        """R4.1 P0 语义路由：query 向量化 → 按 capability 粒度 pgvector cosine 检索，
        按 agent 聚 max(similarity)、过 SEMANTIC_MIN_SIM 下限、返回 top_k。

        无 embedding 源 / PG 不可用 / 空 query / embed 失败：返回 []（关键词结果原样生效）。
        返回 [(Record, score), ...] 按相似度降序；Record 取自内存权威副本（含 route_hints 等）。
        """
        if not self._pg_ok or not query or self._embed_source != "llm":
            return []
        query_embedding = await self._embed_query_cached(query)
        if not query_embedding:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT v.agent_id, MAX(1 - (v.embedding <=> $1::vector)) AS similarity
                    FROM agent_capability_vec v
                    JOIN agents a ON a.agent_id = v.agent_id
                    WHERE a.status = 'healthy' AND v.embedding IS NOT NULL
                    GROUP BY v.agent_id
                    HAVING MAX(1 - (v.embedding <=> $1::vector)) >= $2
                    ORDER BY similarity DESC
                    LIMIT $3
                """, str(query_embedding), SEMANTIC_MIN_SIM, top_k)
            results = []
            for row in rows:
                rec = self._agents.get(row["agent_id"])   # 内存权威副本（含 route_hints/heavy）
                if rec is None or not rec.healthy:
                    continue
                if granted and not self._permitted(rec.manifest, granted):
                    continue
                results.append((rec, float(row["similarity"])))
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
            heavy=bool(c.get("heavy", False)),
        )
        for c in d.get("capabilities", [])
    ]
    # R2.1：route_hints 必须随 PgStore round-trip 还原，否则 registry 重启恢复后
    # RouteHintEngine 拿不到提示、确定性路由兜底静默失效。context_scopes 同理（此前遗漏，
    # 会致重启后敏感上下文最小化下发口径丢失）。
    route_hints = [
        agent_pb2.RouteHint(
            pattern=h.get("pattern", ""),
            intent=h.get("intent", ""),
            policy=h.get("policy", ""),
            priority=int(h.get("priority") or 0),
            guard=h.get("guard", ""),
            slots={k: str(v) for k, v in (h.get("slots") or {}).items()},
        )
        for h in d.get("route_hints", [])
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
        context_scopes=list(d.get("context_scopes") or []),
        route_hints=route_hints,
    )
