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

import relation
import voiceprint as vp
import weighting

logger = logging.getLogger("memory.pg_store")

# 向量维度：须与 embedding 源一致。百炼 text-embedding-v4 默认 1024（不支持 384）。
# 经 EMBED_DIM env 配置；schema 的 vector(384) 会被替换为本值，并 ALTER 既有空列。
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))
DEFAULT_TOP_K = 5
_EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def _weighting_on() -> bool:
    """M2 P0 偏好加权总开关。off = 逐字回到加权前（weight 恒 0 → 召回用 confidence）。"""
    return os.getenv("MEMORY_WEIGHTING", "on").strip().lower() != "off"


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
    # M2 P0 偏好加权：仅 semantic 参与（情景/程序记忆维持既有 confidence 打分）。
    # 未显式给 weight 时按 provenance/证据数算一次；weight=0 → 召回回退 confidence（存量兼容）。
    out["evidence_count"] = int(out.get("evidence_count")
                                or weighting.evidence_count(out["source_turn_ids"]))
    hl = out.get("half_life_days")
    out["half_life_days"] = float(
        hl if hl not in (None, "") else
        (weighting.default_half_life(out["provenance"], out["review_status"])
         if out["kind"] == "semantic" else 0.0))
    w = out.get("weight")
    if w in (None, "") and out["kind"] == "semantic" and _weighting_on():
        w = weighting.compute_weight(provenance=out["provenance"],
                                     evidence_count=out["evidence_count"],
                                     age_seconds=0.0,
                                     half_life_days=out["half_life_days"])
    out["weight"] = float(w or 0)
    out["consent"] = out.get("consent") or ""
    # G6（EVA 二轮）：关于谁 + 偏好极性。polarity 只认闭集，其他值一律归空（不猜）。
    out["subject"] = str(out.get("subject") or "").strip()
    pol = str(out.get("polarity") or "").strip().lower()
    out["polarity"] = pol if pol in ("like", "dislike") else ""
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


def _rowcount(tag) -> int:
    """asyncpg 的 `DELETE 3` 状态串 → 行数。解析不出按 0 算，不虚报删除量。"""
    try:
        return int(str(tag).split()[-1])
    except (ValueError, IndexError):
        return 0


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
        self._rel: dict[str, dict] = {}  # id -> 关系边（同上兜底；M2 P1）
        self._vp: dict[str, dict] = {}   # id -> 声纹模板（同上兜底；M4 P4）

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
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=5,
                command_timeout=10, max_inactive_connection_lifetime=300)
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
            from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
            from runtime.grpcio import aio_channel
            async with aio_channel(self._llm_addr) as ch:
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
            ddl = f.read().replace("vector(384)", f"vector({EMBED_DIM})")
        async with self._pool.acquire() as conn:
            try:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            except Exception as e:
                logger.debug("vector ext: %s", e)
            await conn.execute(ddl)
            # 既有表（如原 vector(384)）改到目标维度——空列可直接 ALTER；非空且不兼容则忽略。
            try:
                await conn.execute(
                    f"ALTER TABLE memory_item ALTER COLUMN embedding TYPE vector({EMBED_DIM})")
            except Exception as e:
                logger.debug("embedding dim alter skipped: %s", e)

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
                           salience,entities,source_ts,source_session,created_at,
                           weight,evidence_count,half_life_days,consent,subject,polarity)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NULLIF($10,'')::jsonb,$11::vector,$12,
                                $13,$14,$15,$16,$17,$18,$19,$20,NULLIF($21,''),$22,$23,$24,$25,
                                NULLIF($26,'')::jsonb,$27,$28,$29,$30,$31,$32,$33,$34,$35)
                        ON CONFLICT (id) DO UPDATE SET
                          text=EXCLUDED.text, value_json=EXCLUDED.value_json,
                          embedding=EXCLUDED.embedding, embedding_model=EXCLUDED.embedding_model,
                          confidence=EXCLUDED.confidence, review_status=EXCLUDED.review_status,
                          superseded_by=EXCLUDED.superseded_by, valid_to=EXCLUDED.valid_to,
                          weight=EXCLUDED.weight, evidence_count=EXCLUDED.evidence_count,
                          half_life_days=EXCLUDED.half_life_days,
                          source_turn_ids=EXCLUDED.source_turn_ids,
                          subject=EXCLUDED.subject, polarity=EXCLUDED.polarity
                    """, it["id"], it["kind"], it["tenant_id"], it["user_id"], it["occupant_id"],
                         it["vehicle_id"], it["memory_level"], it["predicate"], it["text"],
                         it["value_json"], str(emb) if emb else None, it["embedding_model"],
                         it["provenance"], it["confidence"], it["review_status"], it["scope"],
                         it["privacy_level"], it["valid_from"], it["valid_to"], it["expires_at"],
                         it["superseded_by"], it["source_turn_ids"], it["last_used_at"],
                         it["use_count"], it["salience"], it["entities"], it["source_ts"],
                         it["source_session"], it["created_at"],
                         it["weight"], it["evidence_count"], it["half_life_days"],
                         it["consent"], it["subject"], it["polarity"])
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

    async def reinforce(self, item_id: str, *, weight: float, evidence_count: int,
                        source_turn_ids: str = "", half_life_days: float = 0.0) -> bool:
        """同一偏好复现时**就地加强**（M2 P0）：只更新强度与证据，不动 text/valid_from。

        刻意不刷新 `valid_from`——它是衰减基准，刷新等于把陈年偏好洗成新的。
        """
        if not _weighting_on():
            return False
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE memory_item SET weight=$2, evidence_count=$3, "
                    "half_life_days=$4, "
                    "source_turn_ids=CASE WHEN $5='' THEN source_turn_ids ELSE $5 END, "
                    "last_used_at=$6 WHERE id=$1",
                    item_id, float(weight), int(evidence_count), float(half_life_days),
                    source_turn_ids or "", _now())
            return str(tag).endswith("1")
        it = self._mem.get(item_id)
        if not it:
            return False
        it["weight"] = float(weight)
        it["evidence_count"] = int(evidence_count)
        it["half_life_days"] = float(half_life_days)
        if source_turn_ids:
            it["source_turn_ids"] = source_turn_ids
        return True

    async def current_by_predicate(self, user_id: str, occupant_id: str,
                                   predicate: str, subject: str = "") -> dict | None:
        """取某谓词的现行（未被取代）条目，供抽取去重/冲突判定。

        G6：按 subject 精确限定——「爸爸的空调偏好」与「本人的空调偏好」是两条独立
        记忆，不加 subject 维会让关于家人的候选 supersede 掉本人的同谓词偏好。
        """
        if not predicate:
            return None
        occ = occupant_id or "primary"
        subj = (subject or "").strip()
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT * FROM memory_item
                    WHERE user_id=$1 AND occupant_id=$2 AND predicate=$3
                      AND COALESCE(subject,'')=$4
                      AND superseded_by IS NULL
                    ORDER BY valid_from DESC LIMIT 1
                """, user_id, occ, predicate, subj)
            return _row_to_item(row) if row else None
        for v in self._mem.values():
            if (v["user_id"] == user_id and v["occupant_id"] == occ
                    and v["predicate"] == predicate and not v["superseded_by"]
                    and str(v.get("subject") or "") == subj):
                return _strip(v)
        return None

    # ── 召回 ───────────────────────────────────────────────
    async def recall(self, user_id: str, occupant_id: str = "", query: str = "",
                     scopes: list[str] | None = None, kinds: list[str] | None = None,
                     top_k: int = 0, include_superseded: bool = False,
                     predicate_prefix: str = "", min_score: float = 0.0,
                     min_confidence: float = 0.0, max_age_days: int = 0,
                     subject: str = "") -> list[tuple[dict, float]]:
        """召回现行记忆。occupant 空→默认 primary。语义召回需真实模型，否则 lexical。
        subject 非空=只取「关于该人」的记忆（G6，如老婆的口味）；空=不过滤。"""
        top_k = top_k or DEFAULT_TOP_K
        occ = occupant_id or "primary"
        scopes = list(scopes or [])
        kinds = list(kinds or [])
        flt = dict(occ=occ, scopes=scopes, kinds=kinds, predicate_prefix=predicate_prefix,
                   include_superseded=include_superseded, min_confidence=min_confidence,
                   max_age_days=max_age_days, query=query, min_score=min_score, top_k=top_k,
                   subject=(subject or "").strip())
        # 语义向量路径仅当有真实模型 + PG；否则一律走候选过滤 + lexical（诚实）。
        if self._pg_ok and query and self.semantic_available:
            rows = await self._fetch_pg_semantic(user_id, occ, query, scopes, kinds,
                                                 predicate_prefix, include_superseded, top_k,
                                                 subject=flt["subject"])
            cands = [_row_to_item(r) for r in rows]
            sims = {id(c): float(r["sim"]) for c, r in zip(cands, rows)}
            return self._score(cands, flt, vector_sims=sims)
        # 候选来源：PG（无模型/无 query）或内存
        if self._pg_ok:
            rows = await self._fetch_pg_candidates(user_id, occ, scopes, kinds,
                                                   predicate_prefix, include_superseded,
                                                   subject=flt["subject"])
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
            # G6：subject 过滤（关于谁）。要「老婆的偏好」时本人条目（subject 空）不混入。
            if flt.get("subject") and str(it.get("subject") or "") != flt["subject"]:
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
            # M2 P0：有效强度 = weight（带实时衰减）或回退 confidence。
            # **存量兼容硬要求**：weight<=0（M2 前写入的全部条目 + 非 semantic）逐字回到
            # 原来的 `base × confidence`，不扰动已绿的旅程（B3-3 记忆族）。
            eff = weighting.effective_confidence(it, now=now) if _weighting_on() \
                else float(it.get("confidence") or 0)
            score = base * eff
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
                                 predicate_prefix, include_superseded, top_k,
                                 subject: str = ""):
        emb = await self._embed(query)
        async with self._pool.acquire() as conn:
            return await conn.fetch("""
                SELECT *, (1 - (embedding <=> $1::vector)) AS sim FROM memory_item
                WHERE user_id=$2 AND occupant_id=$3 AND embedding IS NOT NULL
                  AND ($4 OR superseded_by IS NULL)
                  AND (cardinality($5::text[])=0 OR kind = ANY($5))
                  AND (cardinality($6::text[])=0 OR scope = ANY($6))
                  AND ($7='' OR predicate LIKE $7 || '%')
                  AND ($9='' OR COALESCE(subject,'')=$9)
                ORDER BY embedding <=> $1::vector LIMIT $8
            """, str(emb), user_id, occ, include_superseded, kinds, scopes,
                 predicate_prefix, top_k * 4, subject or "")

    async def _fetch_pg_candidates(self, user_id, occ, scopes, kinds,
                                   predicate_prefix, include_superseded,
                                   subject: str = ""):
        async with self._pool.acquire() as conn:
            return await conn.fetch("""
                SELECT *, confidence AS sim FROM memory_item
                WHERE user_id=$1 AND occupant_id=$2
                  AND ($3 OR superseded_by IS NULL)
                  AND (cardinality($4::text[])=0 OR kind = ANY($4))
                  AND (cardinality($5::text[])=0 OR scope = ANY($5))
                  AND ($6='' OR predicate LIKE $6 || '%')
                  AND ($7='' OR COALESCE(subject,'')=$7)
                ORDER BY valid_from DESC LIMIT 200
            """, user_id, occ, include_superseded, kinds, scopes, predicate_prefix,
                 subject or "")

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
    # 受管记忆：不是「学到的」，是别的域投影过来的。删它必须回到那个域的入口去，
    # 否则会出现「模板还在、名字没了」这种半截状态。
    MANAGED_PREDICATES = ("identity.name",)

    async def delete_memory_item(self, user_id: str, occupant_id: str,
                                 item_id: str) -> dict:
        """L1：按 OwnerKey + item id **精确**删一条（M-B）。

        存在的理由：HMI 记忆面板此前用 `ForgetUser(scopes=[scope])` 实现「删这一行」
        ——那会删掉该 scope 下**所有乘员**的条目（删一个人的名字＝删光全车的名字）。

        跨 owner 一律 `not_found`，不区分「不存在」和「不是你的」——后者会泄露
        这条记忆属于谁。
        """
        occ = (occupant_id or "").strip()
        if not user_id or not occ or not item_id:
            return {"ok": False, "error": "missing_owner", "deleted": 0,
                    "deleted_relations": 0}
        row = await self._item_by_id(user_id, occ, item_id)
        if row is None:
            return {"ok": False, "error": "not_found", "deleted": 0,
                    "deleted_relations": 0}
        if (row.get("predicate") or "") in self.MANAGED_PREDICATES:
            return {"ok": False, "error": "managed_memory", "deleted": 0,
                    "deleted_relations": 0}
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                async with conn.transaction():      # 同事务：不能只删一半
                    res = await conn.execute(
                        "DELETE FROM memory_item WHERE id=$1 AND user_id=$2 "
                        "AND occupant_id=$3", item_id, user_id, occ)
                    # 以该 item 为端点的派生关系边同删——留着就是指向已删数据的悬空边。
                    rel = await conn.execute(
                        "DELETE FROM memory_relation WHERE user_id=$1 AND occupant_id=$2 "
                        "AND object_ref=$3", user_id, occ, item_id)
            n = _rowcount(res)
            return {"ok": True, "error": "", "deleted": n,
                    "deleted_relations": _rowcount(rel)}
        self._mem.pop(item_id, None)
        gone = [k for k, v in self._rel.items()
                if v.get("user_id") == user_id and v.get("occupant_id") == occ
                and v.get("object_ref") == item_id]
        for k in gone:
            self._rel.pop(k, None)
        return {"ok": True, "error": "", "deleted": 1, "deleted_relations": len(gone)}

    async def _item_by_id(self, user_id: str, occupant_id: str,
                          item_id: str) -> dict | None:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM memory_item WHERE id=$1 AND user_id=$2 "
                    "AND occupant_id=$3", item_id, user_id, occupant_id)
            return dict(row) if row else None
        v = self._mem.get(item_id)
        if v and v.get("user_id") == user_id and v.get("occupant_id") == occupant_id:
            return dict(v)
        return None

    async def forget(self, user_id: str, occupant_id: str = "",
                     scopes: list[str] | None = None) -> int:
        """合规硬删。**必须级联删关系边**（子 RFC §5 红线）——只删 memory_item 的话，
        家人关系与孩子学校（恰恰是最敏感的那部分数据）会留在库里，那是假删除。

        scope 定向删除（`scopes` 非空）时关系边不参与——它没有 scope 维度，按 scope 删
        一部分记忆不应连带清空整张关系图。全量删除（scopes 为空）才级联。
        """
        scopes = list(scopes or [])
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                async with conn.transaction():          # 同事务：不能只删一半
                    res = await conn.execute("""
                        DELETE FROM memory_item WHERE user_id=$1
                          AND ($2='' OR occupant_id=$2)
                          AND (cardinality($3::text[])=0 OR scope = ANY($3))
                    """, user_id, occupant_id or "", scopes)
                    if not scopes:
                        await conn.execute("""
                            DELETE FROM memory_relation WHERE user_id=$1
                              AND ($2='' OR occupant_id=$2)
                        """, user_id, occupant_id or "")
                        # M4 P4：声纹模板同级联（生物特征留在库里比关系边更严重）。
                        # 同 relation：scope 定向删除时不参与——声纹没有 scope 维度。
                        await conn.execute("""
                            DELETE FROM voiceprint WHERE user_id=$1
                              AND ($2='' OR occupant_id=$2)
                        """, user_id, occupant_id or "")
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
        if not scopes:
            for k in [k for k, v in self._rel.items()
                      if v["user_id"] == user_id
                      and (not occupant_id or v["occupant_id"] == occupant_id)]:
                del self._rel[k]
            for k in [k for k, v in self._vp.items()
                      if v["user_id"] == user_id
                      and (not occupant_id or v["occupant_id"] == occupant_id)]:
                del self._vp[k]
        return len(to_del)

    async def export(self, user_id: str) -> list[dict]:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM memory_item WHERE user_id=$1", user_id)
            return [_strip(_row_to_item(r)) for r in rows]
        return [_strip(dict(v)) for v in self._mem.values() if v["user_id"] == user_id]

    # ── 关系边（M2 P1）：只存边 + 一跳查询，不做多跳推理 ────────────────
    async def add_relations(self, user_id: str, rels: list[dict], *,
                            occupant_id: str = "primary") -> list[str]:
        """写关系边（同 (subject, rel, object) 已存在则跳过，不重复堆边）。"""
        out: list[str] = []
        for r in rels or []:
            edge = relation.normalize_candidate(r)
            if not edge:
                continue                                # 词表外/残缺 → 丢弃，不猜
            if await self._relation_exists(user_id, occupant_id, edge):
                continue
            row = {
                "id": uuid.uuid4().hex, "tenant_id": "default", "user_id": user_id,
                "occupant_id": occupant_id or "primary", "valid_from": _now(),
                "created_at": _now(), "superseded_by": "", "consent": "", **edge,
            }
            if self._pg_ok:
                async with self._pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO memory_relation
                          (id,tenant_id,user_id,occupant_id,subject,rel,object,object_ref,
                           confidence,provenance,privacy_level,consent,source_turn_ids,
                           valid_from,superseded_by,created_at)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                                NULLIF($15,''),$16)
                        ON CONFLICT (id) DO NOTHING
                    """, row["id"], row["tenant_id"], row["user_id"], row["occupant_id"],
                         row["subject"], row["rel"], row["object"], row["object_ref"],
                         row["confidence"], row["provenance"], row["privacy_level"],
                         row["consent"], row["source_turn_ids"], row["valid_from"],
                         row["superseded_by"], row["created_at"])
            else:
                self._rel[row["id"]] = row
            # Q5：单值谓词的旧边 supersede。`superseded_by` 列一直存在却**从未写过**
            # ——于是库里出现「同一个孩子三个学校」，每轮召回哪条看运气
            # （**这就是 I-044「幻觉」的真身**）。
            # ⚠ 只对单值语义的谓词做（`relation.is_single_valued`）：
            # `family`/`owns`/`prefers_brand` 天然一对多，对它们 supersede
            # 等于**丢掉一个真实的人**（清洗 dry-run 当场劝退过这条）。
            await self._supersede_older(user_id, occupant_id, row)
            out.append(row["id"])
        return out

    async def _supersede_older(self, user_id: str, occupant_id: str,
                               row: dict) -> None:
        """把同 `(subject, rel)` 的旧现行边标为被本条取代。非单值谓词直接返回。"""
        if not relation.is_single_valued(row["rel"]):
            return
        # ⚠ **只写 `superseded_by`，不写 `valid_to`**：`memory_relation` 建表语句里
        # 根本没有 `valid_to` 列（那是 `memory_item` 才有的）。首版照着 item 的
        # supersede 抄了过来，会在真库上直接报错——加列是 schema 变更（红线，要先问）。
        # 现有这一列已经够表达「这条边被那条取代了」。
        occ = occupant_id or "primary"
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    UPDATE memory_relation SET superseded_by=$1
                     WHERE user_id=$2 AND occupant_id=$3 AND subject=$4 AND rel=$5
                       AND id<>$1 AND superseded_by IS NULL
                """, row["id"], user_id, occ, row["subject"], row["rel"])
            return
        for k, v in self._rel.items():
            if (k != row["id"] and v.get("user_id") == user_id
                    and v.get("occupant_id") == occ
                    and v.get("subject") == row["subject"]
                    and v.get("rel") == row["rel"]
                    and not v.get("superseded_by")):
                v["superseded_by"] = row["id"]

    async def _relation_exists(self, user_id: str, occupant_id: str, edge: dict) -> bool:
        occ = occupant_id or "primary"
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT 1 FROM memory_relation WHERE user_id=$1 AND occupant_id=$2
                      AND subject=$3 AND rel=$4 AND object=$5 AND superseded_by IS NULL
                    LIMIT 1
                """, user_id, occ, edge["subject"], edge["rel"], edge["object"])
            return row is not None
        return any(v["user_id"] == user_id and v["occupant_id"] == occ
                   and v["subject"] == edge["subject"] and v["rel"] == edge["rel"]
                   and v["object"] == edge["object"] and not v.get("superseded_by")
                   for v in self._rel.values())

    async def query_relations(self, user_id: str, *, occupant_id: str = "primary",
                              subject: str = "", rel: str = "", object_: str = "",
                              limit: int = 20) -> list[dict]:
        """按 subject / rel / object 任意组合精确查（**双向**：object 也建了索引）。

        空条件即列出全部（供导出/调试）；不做模糊匹配——关系边靠精确实体名，
        模糊会把「小雨」匹到「小雨点」，导航到错地方比查不到更糟。
        """
        occ = occupant_id or "primary"
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT * FROM memory_relation
                    WHERE user_id=$1 AND occupant_id=$2 AND superseded_by IS NULL
                      AND ($3='' OR subject=$3) AND ($4='' OR rel=$4)
                      AND ($5='' OR object=$5)
                    ORDER BY confidence DESC, valid_from DESC LIMIT $6
                """, user_id, occ, subject or "", rel or "", object_ or "", limit)
            return [dict(r) for r in rows]
        out = [dict(v) for v in self._rel.values()
               if v["user_id"] == user_id and v["occupant_id"] == occ
               and not v.get("superseded_by")
               and (not subject or v["subject"] == subject)
               and (not rel or v["rel"] == rel)
               and (not object_ or v["object"] == object_)]
        out.sort(key=lambda v: (v["confidence"], v["valid_from"]), reverse=True)
        return out[:limit]

    # ── 声纹模板（M4 P4）：存向量、比余弦、三态判定；音频从不到这一层 ──────────
    async def _vp_rows(self, user_id: str, tenant_id: str = "default") -> list[dict]:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM voiceprint WHERE tenant_id=$1 AND user_id=$2 "
                    "ORDER BY occupant_id", tenant_id or "default", user_id)
            return [dict(r) for r in rows]
        return sorted([dict(v) for v in self._vp.values()
                       if v["user_id"] == user_id
                       and v["tenant_id"] == (tenant_id or "default")],
                      key=lambda v: v["occupant_id"])

    @staticmethod
    def _name_taken_by(rows: list[dict], name: str, occ: str) -> str:
        """规范名是否已被**别的** occupant 占用；是则返回那个 occupant_id。

        存量冲突行的 `display_name_norm` 是 NULL，所以判重必须**实时对原显示名重算
        规范形**——只查 norm 列会让存量冲突成为绕过唯一约束的入口。
        """
        want = vp.normalize_display_name(name)
        if not want:
            return ""
        for r in rows:
            if r["occupant_id"] == occ:
                continue
            if vp.normalize_display_name(r.get("display_name") or "") == want:
                return r["occupant_id"]
        return ""

    @staticmethod
    def _conflicted_occupants(rows: list[dict]) -> set:
        """存量重名组的 occupant 集合（供 ListVoiceprints 如实标 name_conflict）。"""
        seen: dict = {}
        for r in rows:
            norm = vp.normalize_display_name(r.get("display_name") or "")
            if norm:
                seen.setdefault(norm, []).append(r["occupant_id"])
        out = set()
        for occs in seen.values():
            if len(occs) > 1:
                out.update(occs)
        return out

    async def enroll_voiceprint(self, user_id: str, samples: list[list[float]], *,
                                occupant_id: str = "", display_name: str = "",
                                model: str = "", tenant_id: str = "default") -> dict:
        """建/更新一个乘员的声纹模板。样本自洽度过低即拒绝——见 voiceprint.min_consistency。"""
        samples = [s for s in (samples or []) if s]
        if not samples:
            return {"ok": False, "error": "no_samples"}
        dims = {len(s) for s in samples}
        if len(dims) != 1:
            return {"ok": False, "error": "dim_mismatch"}
        cons = vp.self_consistency(samples)
        if cons < vp.min_consistency():
            # 不建坏模板：这三段互相都不像，建出来此后谁都认不准（RFC §3.1）。
            return {"ok": False, "error": "low_consistency", "self_consistency": cons}
        tpl = vp.mean_template(samples)
        existing = await self._vp_rows(user_id, tenant_id)
        occ = occupant_id or vp.allocate_occupant_id([r["occupant_id"] for r in existing])
        # **空名保留旧名**（2026-07-26 真机反馈）：重录时前端若没重填称呼就发空串，
        # 无条件覆盖会把之前存对的名字冲掉——而调用方本来就没打算改名。改名有专门的
        # rename_voiceprint，走 enroll 的空名一律按「没传」处理。
        prev = next((r for r in existing if r["occupant_id"] == occ), None)
        name = (display_name or "").strip() or (prev or {}).get("display_name", "") or ""
        # 注：「新乘员必须有称呼」由 HTTP 注册入口把关（2026-07-26 真机批次已修：
        # 称呼必填、空名按「这次不改名」处理）。这里不再加第二道空名闸——重复的
        # 强制点只会打断合法的无名调用方（探针/迁移），不产生新的保护。
        clash = self._name_taken_by(existing, name, occ)
        if clash:
            return {"ok": False, "error": "duplicate_name", "occupant_id": clash}
        now = _now()
        row = {
            "id": uuid.uuid4().hex, "tenant_id": tenant_id or "default", "user_id": user_id,
            "occupant_id": occ, "display_name": name,
            "display_name_norm": vp.normalize_display_name(name) or None,
            "embedding": tpl,
            "dim": len(tpl), "model": model or "", "sample_count": len(samples),
            "self_consistency": cons, "created_at": now, "updated_at": now,
        }
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO voiceprint
                      (id,tenant_id,user_id,occupant_id,display_name,display_name_norm,
                       embedding,dim,model,
                       sample_count,self_consistency,created_at,updated_at)
                    VALUES ($1,$2,$3,$4,$5,$13,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (tenant_id,user_id,occupant_id) DO UPDATE SET
                      display_name=EXCLUDED.display_name,
                      display_name_norm=EXCLUDED.display_name_norm,
                      embedding=EXCLUDED.embedding,
                      dim=EXCLUDED.dim, model=EXCLUDED.model,
                      sample_count=EXCLUDED.sample_count,
                      self_consistency=EXCLUDED.self_consistency,
                      updated_at=EXCLUDED.updated_at
                """, row["id"], row["tenant_id"], row["user_id"], row["occupant_id"],
                     row["display_name"], row["embedding"], row["dim"], row["model"],
                     row["sample_count"], row["self_consistency"], row["created_at"],
                     row["updated_at"], row["display_name_norm"])
        else:
            key = f'{row["tenant_id"]}|{user_id}|{occ}'
            kept = self._vp.get(key)
            if kept:
                row["id"], row["created_at"] = kept["id"], kept["created_at"]
            self._vp[key] = row
        # 名字变了才重写身份记忆。**重录同一个人不该再写一条**——真机上一个人重录 4 次就
        # 攒了 4 条同名 identity.name（3 条 superseded），记忆面板里全是噪声。
        # 注意比的是 `prev`（写库前查到的旧行），别拿写完的 self._vp 比——那已经是新值了。
        if name and name != (prev or {}).get("display_name", ""):
            await self._write_identity_name(user_id, occ, name, row["tenant_id"])
        return {"ok": True, "occupant_id": occ, "display_name": row["display_name"],
                "sample_count": len(samples), "self_consistency": cons}

    async def _write_identity_name(self, user_id: str, occ: str, name: str,
                                   tenant: str = "default") -> None:
        """名字也写一条记忆（M4 P4 补）：只存在声纹表里的话，「你知道我是谁」答不上来——
        那张表没有任何消费方会去读。写进记忆则天然获得召回、导出、GDPR 删除与
        「记忆」面板可见性，且**删除该乘员时随其记忆一起没**（delete_voiceprint 已覆盖）。
        改名走 supersede：同 predicate 同 occupant 只保留现行一条。"""
        await self._supersede_identity(user_id, occ, tenant)
        await self.remember([{
            "user_id": user_id, "occupant_id": occ, "kind": "semantic",
            "predicate": "identity.name", "text": _identity_text(name),
            "value_json": json.dumps({"name": name}, ensure_ascii=False),
            "scope": "profile.identity", "provenance": "user_stated", "confidence": 1.0,
            "memory_level": "occupant", "tenant_id": tenant or "default",
        }])

    async def rename_voiceprint(self, user_id: str, occupant_id: str, display_name: str, *,
                                tenant_id: str = "default") -> dict:
        """只改称呼，不动模板（2026-07-26 真机反馈）。

        **为什么不复用 enroll**：enroll 要三段录音。名字是随时会改的元数据，把改名和「重录
        三段声纹」绑一起，用户为了改个称呼就得重走注册流程——真机上正是这一步把已存的名字
        丢了。表和记忆两处一起改：只改表的话助手嘴里还是旧名（identity.name 才是消费方）。
        """
        name = (display_name or "").strip()
        if not occupant_id or not name:
            return {"ok": False, "error": "empty_name", "display_name": ""}
        rows = await self._vp_rows(user_id, tenant_id)
        row = next((r for r in rows if r["occupant_id"] == occupant_id), None)
        if row is None:
            return {"ok": False, "error": "not_found", "display_name": ""}
        clash = self._name_taken_by(rows, name, occupant_id)
        if clash:
            # 冲突时**表和 identity.name 都不动**：改了一半比没改更糟。
            return {"ok": False, "error": "duplicate_name", "display_name": "",
                    "occupant_id": clash}
        norm = vp.normalize_display_name(name) or None
        now = _now()
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE voiceprint SET display_name=$4, display_name_norm=$6, "
                    "updated_at=$5 "
                    "WHERE tenant_id=$1 AND user_id=$2 AND occupant_id=$3",
                    tenant_id or "default", user_id, occupant_id, name, now, norm)
        else:
            key = f'{tenant_id or "default"}|{user_id}|{occupant_id}'
            if key in self._vp:
                self._vp[key]["display_name"] = name
                self._vp[key]["display_name_norm"] = norm
                self._vp[key]["updated_at"] = now
        if name != row.get("display_name", ""):
            await self._write_identity_name(user_id, occupant_id, name,
                                            tenant_id or "default")
        return {"ok": True, "display_name": name}

    async def _supersede_identity(self, user_id: str, occ: str, tenant: str) -> None:
        """改名时让旧的 identity.name 失效（不删——记忆的既有口径是时序 supersede）。"""
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE memory_item SET superseded_by='renamed' "
                    "WHERE user_id=$1 AND occupant_id=$2 AND predicate='identity.name' "
                    "AND superseded_by IS NULL", user_id, occ)
            return
        for v in self._mem.values():
            if (v["user_id"] == user_id and v["occupant_id"] == occ
                    and v.get("predicate") == "identity.name" and not v.get("superseded_by")):
                v["superseded_by"] = "renamed"

    async def identify_speaker(self, user_id: str, probe: list[float], *,
                               model: str = "", tenant_id: str = "default") -> dict:
        """向量 → occupant_id。判定见 voiceprint.decide（accept 之外一律回 primary）。"""
        rows = await self._vp_rows(user_id, tenant_id)
        if not rows:
            out = vp.decide([])
            out["display_name"] = ""
            return out
        # 跨模型的余弦不在同一个尺度上，stale 模板一律不参与比对（宁可回 primary）。
        usable = [r for r in rows if not model or not r["model"] or r["model"] == model]
        if not usable:
            return {"occupant_id": "primary", "decision": "stale_model",
                    "score": 0.0, "runner_up": 0.0, "display_name": ""}
        p = vp.l2_normalize(probe)
        scored = [(r["occupant_id"], vp.cosine(p, list(r["embedding"]))) for r in usable]
        out = vp.decide(scored)
        names = {r["occupant_id"]: r["display_name"] for r in usable}
        out["display_name"] = names.get(out["occupant_id"], "")
        # **降级时要留下「差一点的是谁」**：decide 一旦回落 primary，top1 的身份就丢了——
        # 网关那行日志只能看到 score=0.52 却不知道那是谁，正好是调阈值最需要的一个数
        # （2026-07-26 真人复标时踩到）。全量排名按分数降序打一行，别再丢。
        logger.info("identify: user=%s decision=%s -> %s | ranked=%s",
                    user_id, out["decision"], out["occupant_id"],
                    ", ".join(f"{o}:{s:.4f}"
                              for o, s in sorted(scored, key=lambda t: t[1], reverse=True)))
        return out

    async def list_voiceprints(self, user_id: str, *, model: str = "",
                               tenant_id: str = "default") -> list[dict]:
        rows = await self._vp_rows(user_id, tenant_id)
        # 存量重名组如实标出来（M-B）：迁移只保证不再**新增**冲突，历史行原名不动、
        # norm 留 NULL。用户看得见才有机会自己挑一个能区分的称呼——系统不替他选。
        conflicted = self._conflicted_occupants(rows)
        for r in rows:
            r["stale"] = bool(model and r["model"] and r["model"] != model)
            r["name_conflict"] = r["occupant_id"] in conflicted
        return rows

    async def delete_voiceprint(self, user_id: str, occupant_id: str, *,
                                purge_memory: bool = True,
                                tenant_id: str = "default") -> dict:
        """删一个乘员。`purge_memory` 默认真——用户说「删掉小雨」的预期是「忘掉这个人」，
        不是「留着他的记忆但认不出他」（RFC §3.4）。**primary 不允许 purge**：那等于把全车
        存量记忆一键清空，删除单个乘员的操作不该有这种爆炸半径（要清空走 ForgetUser）。"""
        if not occupant_id:
            return {"ok": False, "deleted_templates": 0, "deleted_memories": 0}
        purge = purge_memory and occupant_id != "primary"
        # primary 不 purge，但注册时写的那条名字记忆必须跟着模板走：模板都删了还留着
        # 「这位乘员的名字是乘客」，助手会继续管一个已经认不出的人叫那个名字。
        rows = await self._vp_rows(user_id, tenant_id)
        gone_name = next((r.get("display_name", "") for r in rows
                          if r["occupant_id"] == occupant_id), "")
        n_tpl = n_mem = 0
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    r1 = await conn.execute(
                        "DELETE FROM voiceprint WHERE tenant_id=$1 AND user_id=$2 "
                        "AND occupant_id=$3", tenant_id or "default", user_id, occupant_id)
                    n_tpl = _affected(r1)
                    if purge:
                        r2 = await conn.execute(
                            "DELETE FROM memory_item WHERE user_id=$1 AND occupant_id=$2",
                            user_id, occupant_id)
                        n_mem = _affected(r2)
                        await conn.execute(
                            "DELETE FROM memory_relation WHERE user_id=$1 AND occupant_id=$2",
                            user_id, occupant_id)
        else:
            for k in [k for k, v in self._vp.items()
                      if v["user_id"] == user_id and v["occupant_id"] == occupant_id
                      and v["tenant_id"] == (tenant_id or "default")]:
                del self._vp[k]
                n_tpl += 1
            if purge:
                for k in [k for k, v in self._mem.items()
                          if v["user_id"] == user_id and v["occupant_id"] == occupant_id]:
                    del self._mem[k]
                    n_mem += 1
                for k in [k for k, v in self._rel.items()
                          if v["user_id"] == user_id and v["occupant_id"] == occupant_id]:
                    del self._rel[k]
        if n_tpl and not purge and gone_name:
            # 只撤回注册自己写的那一条（按 _identity_text 逐字匹配），不碰用户在对话里
            # 说过的别的身份陈述——删声纹不该顺手抹掉一句「我叫泓舟」。
            await self._retract_identity_name(user_id, occupant_id, gone_name)
        return {"ok": True, "deleted_templates": n_tpl, "deleted_memories": n_mem}

    async def _retract_identity_name(self, user_id: str, occ: str, name: str) -> None:
        text = _identity_text(name)
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE memory_item SET superseded_by='voiceprint_deleted' "
                    "WHERE user_id=$1 AND occupant_id=$2 AND predicate='identity.name' "
                    "AND superseded_by IS NULL AND text=$3", user_id, occ, text)
            return
        for v in self._mem.values():
            if (v["user_id"] == user_id and v["occupant_id"] == occ
                    and v.get("predicate") == "identity.name"
                    and v.get("text") == text and not v.get("superseded_by")):
                v["superseded_by"] = "voiceprint_deleted"


def _identity_text(name: str) -> str:
    """注册/改名写进记忆的那句话。**写与撤回共用一个函数**——删除时要逐字匹配它才能
    只撤回注册自己写的那条，不误伤用户在对话里说过的别的身份陈述。"""
    return f"这位乘员的名字是{name}"


def _affected(status: str) -> int:
    """asyncpg execute() 返回 'DELETE n' 形式的状态串。"""
    try:
        return int(str(status).split()[-1])
    except Exception:
        return 0


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
