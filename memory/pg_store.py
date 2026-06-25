"""分层记忆向量存储：PostgreSQL + pgvector 优先，无 PG 时纯内存（lexical 召回）。

仿 `registry/store.py` 的 PgStore：与 registry 同一 PG 实例、独立 `memory_item` 表，
不触碰 agents 表；embedding 用 bge-small-zh（可选）。

评审后的关键约束：
- **不做哈希伪语义**：无真实 embedding 模型时 embedding 存 NULL、语义召回降级为
  lexical 关键词匹配（诚实），绝不拿哈希向量当语义检索喂 planner。
- **精确优先**：predicate_prefix 命中时按谓词精确过滤，不先走向量。
- **阈值**：min_score/min_confidence/max_age_days 过滤，避免低相关记忆污染。
- **隐私**：highly_sensitive 默认不参与泛化召回，除非显式按 scope/predicate 定向读取。
- **过期**：expires_at 到期的临时偏好不召回。
"""
from __future__ import annotations
import json
import logging
import math
import os
import time
import uuid

logger = logging.getLogger("memory.pg_store")

EMBED_DIM = 384
DEFAULT_TOP_K = 5
_EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def _now() -> int:
    return int(time.time())


def _as_json(v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return ""


def _normalize_item(item: dict) -> dict:
    """补全一条记忆字段默认值并生成 id。"""
    out = dict(item or {})
    out["id"] = out.get("id") or uuid.uuid4().hex
    out["kind"] = out.get("kind") or "semantic"
    out["tenant_id"] = out.get("tenant_id") or "default"
    out["occupant_id"] = out.get("occupant_id") or "primary"
    out["vehicle_id"] = out.get("vehicle_id") or ""
    out["memory_level"] = out.get("memory_level") or "user"
    out["predicate"] = out.get("predicate") or ""
    out["text"] = out.get("text") or ""
    out["value_json"] = _as_json(out.get("value_json"))
    out["embedding_model"] = out.get("embedding_model") or ""
    out["provenance"] = out.get("provenance") or "user_stated"
    out["confidence"] = float(out.get("confidence") if out.get("confidence") not in (None, "") else 1.0)
    out["review_status"] = out.get("review_status") or "user_confirmed"
    out["scope"] = out.get("scope") or ""
    out["privacy_level"] = out.get("privacy_level") or "normal"
    out["valid_from"] = int(out.get("valid_from") or _now())
    out["valid_to"] = int(out.get("valid_to") or 0)
    out["expires_at"] = int(out.get("expires_at") or 0)
    out["superseded_by"] = out.get("superseded_by") or ""
    out["source_turn_ids"] = out.get("source_turn_ids") or ""
    out["last_used_at"] = int(out.get("last_used_at") or 0)
    out["use_count"] = int(out.get("use_count") or 0)
    out["salience"] = float(out.get("salience") if out.get("salience") not in (None, "") else 0.5)
    out["entities"] = _as_json(out.get("entities"))
    out["source_ts"] = int(out.get("source_ts") or out["valid_from"])
    out["source_session"] = out.get("source_session") or ""
    out["created_at"] = int(out.get("created_at") or _now())
    return out


def _tokens(s: str) -> list[str]:
    for ch in "，。、；：！？,.;:!?":
        s = s.replace(ch, " ")
    parts = [p for p in s.split() if p]
    grams: list[str] = []
    for p in parts:
        grams.append(p)
        for i in range(len(p) - 1):
            grams.append(p[i:i + 2])
    return grams


def _lexical_score(query: str, text: str, predicate: str = "") -> float:
    """无 embedding 模型时的兜底相关性：字符集合 Jaccard + 子串命中加成。确定性。"""
    q = (query or "").strip()
    hay = f"{text} {predicate}".strip()
    if not q or not hay:
        return 0.0
    qs, hs = set(q), set(hay)
    if not qs or not hs:
        return 0.0
    jacc = len(qs & hs) / len(qs | hs)
    sub = 0.0
    for tok in _tokens(q):
        if len(tok) >= 2 and tok in hay:
            sub = 0.6
            break
    return max(jacc, sub)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _strip(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("embedding", "sim")}


class MemoryVectorStore:
    """语义/情景记忆存储。PG(pgvector) 优先，无 PG 纯内存（lexical）。"""

    def __init__(self, dsn: str = ""):
        self._dsn = dsn or os.getenv("POSTGRES_DSN", "")
        self._pool = None
        self._pg_ok = False
        self._embedder = None
        self._llm_addr = os.getenv("LLM_GATEWAY_ADDR", "")
        self._embed_source = None  # "llm" | "local" | None（决定能否真实语义召回）
        self._mem: dict[str, dict] = {}  # id -> item（PG 不可用时兜底）

    @property
    def pg_ok(self) -> bool:
        return self._pg_ok

    @property
    def semantic_available(self) -> bool:
        """是否有真实 embedding 源（llm-gateway 或本地模型）——决定能否向量语义召回。
        无源时降级 lexical，绝不哈希伪语义。"""
        return self._embed_source is not None

    async def init(self) -> bool:
        if not self._dsn:
            logger.info("MemoryVectorStore: no POSTGRES_DSN, in-memory fallback (lexical)")
            return False
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
            await self._ensure_schema()
            self._pg_ok = True
            await self._probe_embedder()  # 探测 embedding 源（llm-gateway 优先）
            logger.info("MemoryVectorStore: PostgreSQL connected (embed=%s)",
                        self._embed_source)
            return True
        except Exception as e:
            logger.warning("MemoryVectorStore: PG unavailable, in-memory fallback: %s", e)
            self._pg_ok = False
            return False

    async def _probe_embedder(self):
        """探测可用 embedding 源：llm-gateway 优先（项目推荐），本地模型次之，皆无→lexical。
        维度须等于 EMBED_DIM（与 vector(384) 列对齐），否则忽略该源。"""
        if self._llm_addr:
            v = await self._llm_embed(["探测"])
            if v and len(v[0]) == EMBED_DIM:
                self._embed_source = "llm"
                logger.info("MemoryVectorStore: embedding via llm-gateway (dim=%d)", EMBED_DIM)
                return
            if v and v[0] and len(v[0]) != EMBED_DIM:
                logger.warning("MemoryVectorStore: llm-gateway embedding dim=%d != %d，忽略该源",
                               len(v[0]), EMBED_DIM)
        try:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer(_EMBED_MODEL)
            self._embed_source = "local"
            logger.info("MemoryVectorStore: embedding via local %s", _EMBED_MODEL)
            return
        except Exception:
            self._embedder = None
        self._embed_source = None
        logger.info("MemoryVectorStore: 无 embedding 源 → 语义召回降级 lexical")

    async def _llm_embed(self, texts: list[str]) -> list[list[float]] | None:
        """经 llm-gateway Embed RPC 向量化（唯一 embedding 出口）。失败返回 None。"""
        try:
            import grpc
            from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
            async with grpc.aio.insecure_channel(self._llm_addr) as ch:
                stub = llm_pb2_grpc.LLMGatewayStub(ch)
                resp = await stub.Embed(llm_pb2.EmbedRequest(texts=list(texts)), timeout=20)
                return [list(e.values) for e in resp.embeddings]
        except Exception as e:
            logger.debug("llm-gateway embed failed: %s", e)
            return None

    async def _embed(self, text: str) -> list[float] | None:
        """文本向量化：llm-gateway 优先，本地模型次之；无源/维度不符返回 None（→lexical）。"""
        if not text or self._embed_source is None:
            return None
        if self._embed_source == "llm":
            v = await self._llm_embed([text])
            return v[0] if v and len(v[0]) == EMBED_DIM else None
        if self._embed_source == "local" and self._embedder is not None:
            try:
                return self._embedder.encode(text, normalize_embeddings=True).tolist()
            except Exception:
                return None
        return None

    async def _ensure_schema(self):
        with open(_SCHEMA_PATH, encoding="utf-8") as f:
            ddl = f.read()
        async with self._pool.acquire() as conn:
            try:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            except Exception as e:
                logger.debug("vector ext: %s", e)
            await conn.execute(ddl)

    # ── 写 ─────────────────────────────────────────────────
    async def remember(self, items: list[dict]) -> list[str]:
        norm = [_normalize_item(it) for it in (items or [])]
        if self._pg_ok:
            model_name = (_EMBED_MODEL if self._embed_source == "local"
                          else "llm-gateway" if self._embed_source == "llm" else "")
            async with self._pool.acquire() as conn:
                for it in norm:
                    emb = await self._embed(it["text"])
                    it["embedding_model"] = model_name if emb else ""
                    await conn.execute("""
                        INSERT INTO memory_item
                          (id,kind,tenant_id,user_id,occupant_id,vehicle_id,memory_level,
                           predicate,text,value_json,embedding,embedding_model,provenance,
                           confidence,review_status,scope,privacy_level,valid_from,valid_to,
                           expires_at,superseded_by,source_turn_ids,last_used_at,use_count,
                           salience,entities,source_ts,source_session,created_at)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NULLIF($10,'')::jsonb,$11::vector,$12,
                                $13,$14,$15,$16,$17,$18,$19,$20,NULLIF($21,''),$22,$23,$24,$25,
                                NULLIF($26,'')::jsonb,$27,$28,$29)
                        ON CONFLICT (id) DO UPDATE SET
                          text=EXCLUDED.text, value_json=EXCLUDED.value_json,
                          embedding=EXCLUDED.embedding, embedding_model=EXCLUDED.embedding_model,
                          confidence=EXCLUDED.confidence, review_status=EXCLUDED.review_status,
                          superseded_by=EXCLUDED.superseded_by, valid_to=EXCLUDED.valid_to
                    """, it["id"], it["kind"], it["tenant_id"], it["user_id"], it["occupant_id"],
                         it["vehicle_id"], it["memory_level"], it["predicate"], it["text"],
                         it["value_json"], str(emb) if emb else None, it["embedding_model"],
                         it["provenance"], it["confidence"], it["review_status"], it["scope"],
                         it["privacy_level"], it["valid_from"], it["valid_to"], it["expires_at"],
                         it["superseded_by"], it["source_turn_ids"], it["last_used_at"],
                         it["use_count"], it["salience"], it["entities"], it["source_ts"],
                         it["source_session"], it["created_at"])
        else:
            for it in norm:
                self._mem[it["id"]] = it
        return [it["id"] for it in norm]

    async def supersede(self, old_id: str, new_id: str, valid_to: int = 0):
        vt = valid_to or _now()
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE memory_item SET superseded_by=$2, valid_to=$3 WHERE id=$1",
                    old_id, new_id, vt)
        elif old_id in self._mem:
            self._mem[old_id]["superseded_by"] = new_id
            self._mem[old_id]["valid_to"] = vt

    async def current_by_predicate(self, user_id: str, occupant_id: str,
                                   predicate: str) -> dict | None:
        """取某谓词的现行（未被取代）条目，供抽取去重/冲突判定。"""
        if not predicate:
            return None
        occ = occupant_id or "primary"
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT * FROM memory_item
                    WHERE user_id=$1 AND occupant_id=$2 AND predicate=$3
                      AND superseded_by IS NULL
                    ORDER BY valid_from DESC LIMIT 1
                """, user_id, occ, predicate)
            return _row_to_item(row) if row else None
        for v in self._mem.values():
            if (v["user_id"] == user_id and v["occupant_id"] == occ
                    and v["predicate"] == predicate and not v["superseded_by"]):
                return _strip(v)
        return None

    # ── 召回 ───────────────────────────────────────────────
    async def recall(self, user_id: str, occupant_id: str = "", query: str = "",
                     scopes: list[str] | None = None, kinds: list[str] | None = None,
                     top_k: int = 0, include_superseded: bool = False,
                     predicate_prefix: str = "", min_score: float = 0.0,
                     min_confidence: float = 0.0, max_age_days: int = 0
                     ) -> list[tuple[dict, float]]:
        """召回现行记忆。occupant 空→默认 primary。语义召回需真实模型，否则 lexical。"""
        top_k = top_k or DEFAULT_TOP_K
        occ = occupant_id or "primary"
        scopes = list(scopes or [])
        kinds = list(kinds or [])
        flt = dict(occ=occ, scopes=scopes, kinds=kinds, predicate_prefix=predicate_prefix,
                   include_superseded=include_superseded, min_confidence=min_confidence,
                   max_age_days=max_age_days, query=query, min_score=min_score, top_k=top_k)
        # 语义向量路径仅当有真实模型 + PG；否则一律走候选过滤 + lexical（诚实）。
        if self._pg_ok and query and self.semantic_available:
            rows = await self._fetch_pg_semantic(user_id, occ, query, scopes, kinds,
                                                 predicate_prefix, include_superseded, top_k)
            cands = [_row_to_item(r) for r in rows]
            sims = {id(c): float(r["sim"]) for c, r in zip(cands, rows)}
            return self._score(cands, flt, vector_sims=sims)
        # 候选来源：PG（无模型/无 query）或内存
        if self._pg_ok:
            rows = await self._fetch_pg_candidates(user_id, occ, scopes, kinds,
                                                   predicate_prefix, include_superseded)
            cands = [_row_to_item(r) for r in rows]
        else:
            cands = [c for c in self._mem.values() if c["user_id"] == user_id]
        return self._score(cands, flt)

    def _score(self, cands: list[dict], flt: dict, vector_sims: dict | None = None):
        now = _now()
        explicitly_targeted = bool(flt["scopes"]) or bool(flt["predicate_prefix"])
        results: list[tuple[dict, float]] = []
        for it in cands:
            if it["occupant_id"] != flt["occ"]:
                continue
            if not flt["include_superseded"] and it.get("superseded_by"):
                continue
            if flt["kinds"] and it["kind"] not in flt["kinds"]:
                continue
            if flt["scopes"] and it["scope"] not in flt["scopes"]:
                continue
            if flt["predicate_prefix"] and not it["predicate"].startswith(flt["predicate_prefix"]):
                continue
            # 隐私：高敏默认不参与泛化召回，除非显式定向（scope/predicate）
            if it.get("privacy_level") == "highly_sensitive" and not explicitly_targeted:
                continue
            # 过期 / 年龄 / 置信度阈值
            if it.get("expires_at") and it["expires_at"] < now:
                continue
            if flt["max_age_days"] and (now - it["valid_from"]) > flt["max_age_days"] * 86400:
                continue
            if it["confidence"] < flt["min_confidence"]:
                continue
            # 相关性：向量优先，否则 lexical；无 query→列出模式
            if vector_sims is not None and id(it) in vector_sims:
                base = vector_sims[id(it)]
            elif not flt["query"]:
                base = 1.0
            else:
                base = _lexical_score(flt["query"], it["text"], it["predicate"])
            if base <= 0:
                continue
            score = base * it["confidence"]
            if score < flt["min_score"]:
                continue
            results.append((it, score))
        results.sort(key=lambda x: (x[1], x[0]["valid_from"]), reverse=True)
        top = results[:flt["top_k"]]
        for it, _ in top:
            it["last_used_at"] = now
            it["use_count"] = it.get("use_count", 0) + 1
        return [(_strip(it), float(s)) for it, s in top]

    async def _fetch_pg_semantic(self, user_id, occ, query, scopes, kinds,
                                 predicate_prefix, include_superseded, top_k):
        emb = await self._embed(query)
        async with self._pool.acquire() as conn:
            return await conn.fetch("""
                SELECT *, (1 - (embedding <=> $1::vector)) AS sim FROM memory_item
                WHERE user_id=$2 AND occupant_id=$3 AND embedding IS NOT NULL
                  AND ($4 OR superseded_by IS NULL)
                  AND (cardinality($5::text[])=0 OR kind = ANY($5))
                  AND (cardinality($6::text[])=0 OR scope = ANY($6))
                  AND ($7='' OR predicate LIKE $7 || '%')
                ORDER BY embedding <=> $1::vector LIMIT $8
            """, str(emb), user_id, occ, include_superseded, kinds, scopes,
                 predicate_prefix, top_k * 4)

    async def _fetch_pg_candidates(self, user_id, occ, scopes, kinds,
                                   predicate_prefix, include_superseded):
        async with self._pool.acquire() as conn:
            return await conn.fetch("""
                SELECT *, confidence AS sim FROM memory_item
                WHERE user_id=$1 AND occupant_id=$2
                  AND ($3 OR superseded_by IS NULL)
                  AND (cardinality($4::text[])=0 OR kind = ANY($4))
                  AND (cardinality($5::text[])=0 OR scope = ANY($5))
                  AND ($6='' OR predicate LIKE $6 || '%')
                ORDER BY valid_from DESC LIMIT 200
            """, user_id, occ, include_superseded, kinds, scopes, predicate_prefix)

    async def get_places(self, user_id: str, occupant_id: str = "primary") -> dict:
        """从 place.* 现行条目重建 profile.places 字典（家/公司）。
        直接谓词读取（高敏定向，绕过泛化召回的隐私排除——这是显式定向读）。"""
        occ = occupant_id or "primary"
        out: dict = {}
        rows = []
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT predicate, value_json FROM memory_item
                    WHERE user_id=$1 AND occupant_id=$2 AND predicate LIKE 'place.%'
                      AND superseded_by IS NULL
                """, user_id, occ)
            rows = [(r["predicate"], r["value_json"]) for r in rows]
        else:
            rows = [(v["predicate"], v["value_json"]) for v in self._mem.values()
                    if v["user_id"] == user_id and v["occupant_id"] == occ
                    and v["predicate"].startswith("place.") and not v["superseded_by"]]
        for pred, vj in rows:
            key = pred.split(".", 1)[1] if "." in pred else pred
            try:
                out[key] = json.loads(vj) if vj else {}
            except (json.JSONDecodeError, TypeError):
                out[key] = {}
        return out

    # ── 合规 ───────────────────────────────────────────────
    async def forget(self, user_id: str, occupant_id: str = "",
                     scopes: list[str] | None = None) -> int:
        scopes = list(scopes or [])
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                res = await conn.execute("""
                    DELETE FROM memory_item WHERE user_id=$1
                      AND ($2='' OR occupant_id=$2)
                      AND (cardinality($3::text[])=0 OR scope = ANY($3))
                """, user_id, occupant_id or "", scopes)
            try:
                return int(res.split()[-1])
            except Exception:
                return 0
        to_del = [k for k, v in self._mem.items()
                  if v["user_id"] == user_id
                  and (not occupant_id or v["occupant_id"] == occupant_id)
                  and (not scopes or v["scope"] in scopes)]
        for k in to_del:
            del self._mem[k]
        return len(to_del)

    async def export(self, user_id: str) -> list[dict]:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM memory_item WHERE user_id=$1", user_id)
            return [_strip(_row_to_item(r)) for r in rows]
        return [_strip(dict(v)) for v in self._mem.values() if v["user_id"] == user_id]


def _row_to_item(row) -> dict:
    d = dict(row)
    d.pop("sim", None)
    d.pop("embedding", None)
    for k in ("value_json", "entities"):
        v = d.get(k)
        if v is not None and not isinstance(v, str):
            d[k] = json.dumps(v, ensure_ascii=False)
        elif v is None:
            d[k] = ""
    d["superseded_by"] = d.get("superseded_by") or ""
    return d
