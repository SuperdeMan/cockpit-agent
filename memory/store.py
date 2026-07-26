"""记忆存储：短期会话(Redis，内存兜底) + 上下文 scope 取数 + 画像管理。

Phase 1 改进：画像导出/删除（合规）、scope 权限控制。
"""
from __future__ import annotations
import json
import os
import time
import logging

logger = logging.getLogger("memory.store")

try:
    import redis.asyncio as aioredis
except Exception:
    aioredis = None

from pg_store import MemoryVectorStore

_MOCK_CONTEXT = {
    "vehicle.location": {"lat": 31.23, "lng": 121.47, "city": "上海", "road": "延安高架"},
    "vehicle.state": {"speed_kmh": 60, "soc": 55, "gear": "D"},
    "profile.taste": {"spicy": "medium", "budget_per_person": 100},
}

# 敏感 scope（上云需脱敏）
_SENSITIVE_SCOPES = {"vehicle.location", "vehicle.state", "profile.taste"}

# 会话轮次原文 TTL（默认 7 天）：对话原文是个人数据，不设 TTL 等于永久留存。
# 近 N 轮上下文与巩固抽取都只用最近轮次，7 天远超任何消费方需要。
_SESSION_TTL_S = int(os.getenv("MEMORY_SESSION_TTL_S", "604800") or "604800")


class MemoryStore:
    def __init__(self):
        self.url = os.getenv("REDIS_URL", "")
        self._r = None
        self._mem: dict[str, list] = {}
        self._mem_user_sessions: dict[str, set] = {}  # user_id -> {session_id}（内存兜底的 GDPR 索引）
        self._profiles: dict[str, dict] = {}  # user_id -> profile data
        # 分层记忆（语义画像/情景）——PG+pgvector，无 PG 内存兜底；首用懒初始化。
        self._vstore = MemoryVectorStore()
        self._vstore_inited = False

    async def _redis(self):
        if aioredis and self.url and self._r is None:
            try:
                self._r = aioredis.from_url(
                    self.url, decode_responses=True, socket_timeout=3,
                    socket_connect_timeout=3, socket_keepalive=True,
                    health_check_interval=30, retry_on_timeout=True)
                await self._r.ping()
            except Exception as e:
                logger.warning("Redis unavailable, using in-memory: %s", e)
                self._r = None
        return self._r

    async def append_turn(self, session_id: str, role: str, text: str,
                          user_id: str = ""):
        """会话轮次原文。**有 TTL、有 user 索引**——两者都是 GDPR 侧的硬要求：
        无 TTL 的对话原文会永久留存；无 user→session 索引则 ForgetUser 只能删
        长期记忆、删不到原始对话（假删除的一半，验收抓到）。"""
        turn = {"role": role, "text": text, "ts": int(time.time())}
        r = await self._redis()
        key = f"sess:{session_id}"
        if r:
            await r.rpush(key, json.dumps(turn))
            await r.ltrim(key, -50, -1)
            await r.expire(key, _SESSION_TTL_S)
            if user_id:
                idx = f"user_sessions:{user_id}"
                await r.sadd(idx, session_id)
                await r.expire(idx, _SESSION_TTL_S)
        else:
            self._mem.setdefault(session_id, []).append(turn)
            if user_id:
                self._mem_user_sessions.setdefault(user_id, set()).add(session_id)

    async def get_session(self, session_id: str, last_n: int) -> list[dict]:
        r = await self._redis()
        if r:
            items = await r.lrange(f"sess:{session_id}", -last_n, -1)
            return [json.loads(i) for i in items]
        return self._mem.get(session_id, [])[-last_n:]

    def _profile_key(self, user_id: str) -> str:
        return f"profile:{user_id}"

    async def _load_profile(self, user_id: str) -> dict:
        """读用户画像：Redis 优先（持久，重启不丢），内存兜底/缓存。"""
        if not user_id:
            return {}
        r = await self._redis()
        if r:
            raw = await r.get(self._profile_key(user_id))
            profile = json.loads(raw) if raw else {}
            self._profiles[user_id] = profile  # 缓存
            return profile
        return self._profiles.get(user_id, {})

    async def _save_profile(self, user_id: str, profile: dict):
        """写用户画像：Redis（持久）+ 内存缓存。"""
        self._profiles[user_id] = profile
        r = await self._redis()
        if r:
            await r.set(self._profile_key(user_id),
                        json.dumps(profile, ensure_ascii=False))

    async def get_context(self, session_id, user_id, vehicle_id, scopes) -> dict:
        """按 scope 取上下文。敏感 scope 走脱敏路径。"""
        result = {}
        profile = await self._load_profile(user_id) if user_id else {}
        for scope in scopes:
            # profile.places：优先读分层记忆新表（P1 收敛），无则回退旧 KV
            if scope == "profile.places" and user_id:
                places = await (await self._vec()).get_places(user_id)
                if places:
                    result[scope] = json.dumps(places, ensure_ascii=False)
                    continue
            # 用户画像优先从持久化画像取（如 profile.places 常用地点）
            if scope.startswith("profile.") and user_id:
                key = scope.split(".", 1)[1] if "." in scope else scope
                if key in profile:
                    result[scope] = json.dumps(profile[key], ensure_ascii=False)
                    continue
            # 兜底 mock
            if scope in _MOCK_CONTEXT:
                data = _MOCK_CONTEXT[scope]
                # 敏感 scope 脱敏（如位置只给城市级）
                if scope in _SENSITIVE_SCOPES:
                    data = self._desensitize(scope, data)
                result[scope] = json.dumps(data, ensure_ascii=False)
        return result

    async def export_profile(self, user_id: str) -> dict:
        """导出用户画像（合规接口）。"""
        return await self._load_profile(user_id)

    async def delete_profile(self, user_id: str) -> bool:
        """删除用户画像（合规接口）。删除后不可再被检索。"""
        existed = bool(await self._load_profile(user_id))
        self._profiles.pop(user_id, None)
        r = await self._redis()
        if r:
            await r.delete(self._profile_key(user_id))
        # 一并清掉镜像的常用地点（profile.places），保持画像删除一致
        try:
            await (await self._vec()).forget(user_id, scopes=["profile.places"])
        except Exception as e:
            logger.debug("delete places mirror failed: %s", e)
        if existed:
            logger.info("Profile deleted: %s", user_id)
        return existed

    async def update_profile(self, user_id: str, data: dict):
        """更新用户画像（合并写，持久化）。"""
        profile = await self._load_profile(user_id)
        profile.update(data)
        await self._save_profile(user_id, profile)

    async def upsert_profile(self, user_id: str, key: str, value):
        """写单个画像字段（如 places），value 为已解析对象。持久化。
        places 额外镜像到分层记忆 memory_item（高敏，P1 双写收敛）。"""
        profile = await self._load_profile(user_id)
        profile[key] = value
        await self._save_profile(user_id, profile)
        if key == "places" and isinstance(value, dict):
            try:
                await self._mirror_places(user_id, value)
            except Exception as e:
                logger.debug("mirror places failed: %s", e)

    async def _mirror_places(self, user_id: str, places: dict, occupant_id: str = "primary"):
        """把常用地点镜像为 memory_item（predicate place.*，highly_sensitive，用户显式设置）。
        逐点 supersede-or-insert，避免重复。"""
        vs = await self._vec()
        for k, rec in (places or {}).items():
            if not isinstance(rec, dict):
                continue
            pred = f"place.{k}"
            vj = json.dumps(rec, ensure_ascii=False)
            cur = await vs.current_by_predicate(user_id, occupant_id, pred)
            if cur and (cur.get("value_json") or "") == vj:
                continue  # 未变
            item = {"user_id": user_id, "occupant_id": occupant_id, "kind": "semantic",
                    "predicate": pred, "scope": "profile.places",
                    "privacy_level": "highly_sensitive", "provenance": "user_stated",
                    "review_status": "user_confirmed", "confidence": 1.0,
                    "text": f"{k}：{rec.get('name') or rec.get('address') or ''}".strip("："),
                    "value_json": vj}
            ids = await vs.remember([item])
            if cur:
                await vs.supersede(cur["id"], ids[0])

    async def migrate_places(self, user_id: str) -> int:
        """P1.5：把既有 KV profile.places 一次性迁入 memory_item。返回迁移地点数。"""
        profile = await self._load_profile(user_id)
        places = profile.get("places") or {}
        if places:
            await self._mirror_places(user_id, places)
        return len(places)

    # ── 分层记忆（语义画像 / 情景）──────────────────────────
    async def _vec(self) -> MemoryVectorStore:
        """懒初始化向量存储（首次调用连 PG，失败降级内存）。"""
        if not self._vstore_inited:
            self._vstore_inited = True
            await self._vstore.init()
        return self._vstore

    async def remember(self, items: list[dict]) -> list[str]:
        return await (await self._vec()).remember(items)

    async def recall(self, user_id: str, occupant_id: str = "", query: str = "",
                     scopes: list[str] | None = None, kinds: list[str] | None = None,
                     top_k: int = 0, include_superseded: bool = False,
                     predicate_prefix: str = "", min_score: float = 0.0,
                     min_confidence: float = 0.0, max_age_days: int = 0
                     ) -> list[tuple[dict, float]]:
        return await (await self._vec()).recall(
            user_id=user_id, occupant_id=occupant_id, query=query, scopes=scopes,
            kinds=kinds, top_k=top_k, include_superseded=include_superseded,
            predicate_prefix=predicate_prefix, min_score=min_score,
            min_confidence=min_confidence, max_age_days=max_age_days)

    async def forget_user(self, user_id: str, occupant_id: str = "",
                          scopes: list[str] | None = None) -> int:
        """合规：删除用户记忆。occupant/scope 都为空时连画像一并清（删全量）。

        全量删同时清会话原文（`sess:*`）：长期记忆删了、原始对话还躺在 Redis 里，
        那不是删除是搬家。occupant 级删除动不了会话原文——轮次不带说话人标注，
        无法选择性删（已知限制，与巩固窗口说话人盲同根）。
        """
        deleted = await (await self._vec()).forget(user_id, occupant_id, scopes)
        if not occupant_id and not scopes:
            await self.delete_profile(user_id)
            await self._forget_sessions(user_id)
        return deleted

    async def _forget_sessions(self, user_id: str) -> None:
        r = await self._redis()
        if r:
            idx = f"user_sessions:{user_id}"
            try:
                sids = await r.smembers(idx)
                if sids:
                    await r.delete(*[f"sess:{s}" for s in sids])
                await r.delete(idx)
            except Exception as e:
                logger.warning("forget sessions failed for %s: %s", user_id, e)
        for sid in self._mem_user_sessions.pop(user_id, set()):
            self._mem.pop(sid, None)

    async def export_user(self, user_id: str) -> dict:
        """合规：导出用户画像 + 全量记忆 + 关系边。

        导出必须与删除对称：能被 `forget_user` 删掉的东西，用户就有权先看到它
        （M2 P1 关系边同样是个人数据）。
        """
        vs = await self._vec()
        return {
            "profile": await self.export_profile(user_id),
            "memories": await vs.export(user_id),
            "relations": await vs.query_relations(user_id, limit=500),
        }

    async def relations(self, user_id: str, *, occupant_id: str = "primary",
                        subject: str = "", rel: str = "", object_: str = "",
                        limit: int = 20) -> list[dict]:
        """关系边一跳查询（M2 P1）。消费方：navigation 人称地点解析、导出。"""
        return await (await self._vec()).query_relations(
            user_id, occupant_id=occupant_id, subject=subject, rel=rel,
            object_=object_, limit=limit)

    # ── 声纹模板（M4 P4）：纯委托，判定逻辑在 voiceprint.py，存取在 pg_store ──────
    async def enroll_voiceprint(self, user_id: str, samples, **kw) -> dict:
        return await (await self._vec()).enroll_voiceprint(user_id, samples, **kw)

    async def identify_speaker(self, user_id: str, probe, **kw) -> dict:
        return await (await self._vec()).identify_speaker(user_id, probe, **kw)

    async def list_voiceprints(self, user_id: str, **kw) -> list[dict]:
        return await (await self._vec()).list_voiceprints(user_id, **kw)

    async def delete_voiceprint(self, user_id: str, occupant_id: str, **kw) -> dict:
        return await (await self._vec()).delete_voiceprint(user_id, occupant_id, **kw)

    async def rename_voiceprint(self, user_id: str, occupant_id: str,
                                display_name: str, **kw) -> dict:
        return await (await self._vec()).rename_voiceprint(
            user_id, occupant_id, display_name, **kw)

    async def resolve_person_place(self, user_id: str, person_word: str, *,
                                   occupant_id: str = "primary") -> dict | None:
        """**一跳解析**：人称词 → family 边找实体 → place_of 边找地点。

        「去接孩子放学」→「孩子」→（family: 小雨-女儿）→（place_of: 小雨-XX小学）。
        泛称「孩子」要能命中存成「女儿」的边（kinship_aliases），因为用户存的时候说
        「我女儿叫小雨」、用的时候说「去接孩子」。

        **查不到返回 None**（调用方诚实追问）；命中多个实体也返回 None——导航到错地方
        比查不到更糟，宁可问一句。
        """
        from relation import REL_FAMILY, REL_PLACE_OF, kinship_aliases
        aliases = kinship_aliases(person_word)
        if not aliases:
            return None
        vs = await self._vec()
        persons: list[str] = []
        for alias in aliases:
            for edge in await vs.query_relations(user_id, occupant_id=occupant_id,
                                                 rel=REL_FAMILY, object_=alias):
                if edge["subject"] not in persons:
                    persons.append(edge["subject"])
        if len(persons) != 1:
            return None                      # 0=不知道；>1=有歧义，都该问不该猜
        places = await vs.query_relations(user_id, occupant_id=occupant_id,
                                          subject=persons[0], rel=REL_PLACE_OF)
        if len(places) != 1:
            return None
        return {"person": persons[0], "place": places[0]["object"],
                "object_ref": places[0].get("object_ref") or ""}

    async def consolidate(self, session_id: str, user_id: str, occupant_id: str = "primary",
                          vehicle_id: str = "", complete_fn=None) -> list[str]:
        """抽取并巩固：对话→候选→去重/等价跳过/冲突 supersede（时序-lite）。
        返回新写入的记忆 id。LLM 不可用时静默返回 []（不阻塞）。"""
        if not user_id:
            return []
        from extract import extract, predicate_class
        turns = await self.get_session(session_id, 12)
        cands = await extract(turns, user_id=user_id, occupant_id=occupant_id,
                              vehicle_id=vehicle_id, session_id=session_id,
                              complete_fn=complete_fn)
        if not cands:
            return []
        vs = await self._vec()
        written: list[str] = []
        # M2 P1：关系边与记忆条目分流——前者进 memory_relation，不进 memory_item
        # （`_relation` 保留键由 extract._govern 打，同 `_escalate`/`_verify` 哲学）。
        edges = [c["_relation"] for c in cands if c.get("_relation")]
        if edges:
            await vs.add_relations(user_id, edges, occupant_id=occupant_id)
        for c in cands:
            if c.get("_relation"):
                continue
            pred = c.get("predicate") or ""
            if c.get("kind") == "semantic" and pred:
                # 冲突查找按谓词等价类（B3-3 M2）：历史条目可能带 LLM 自由造的别名
                # （hvac.temperature vs climate.temperature），精确匹配失手会让新旧偏好
                # 并存、召回二义。命中类内任一别名即视为同一偏好做 supersede。
                cur = None
                for p in predicate_class(pred):
                    cur = await vs.current_by_predicate(user_id, occupant_id, p)
                    if cur:
                        break
                if cur:
                    if (cur.get("text") or "").strip() == (c.get("text") or "").strip():
                        # 等价 → **加权而非跳过**（M2 P0）：同一偏好每次复现都是一次独立
                        # 证据，「每周三次点川菜」正是靠这里超过「说过一次爱吃辣」。
                        # 今天的「跳过」让重复出现完全不留痕，是 C4 的根因之一。
                        await self._reinforce(vs, cur, c)
                        continue
                    # 冲突 → 插新。**证据链要继承**（子 RFC §5）：旧条目连同它的
                    # source_turn_ids 一起被 supersede，不继承就追不回完整证据了。
                    c = self._inherit_evidence(cur, c)
                    ids = await vs.remember([c])
                    await vs.supersede(cur["id"], ids[0])  # 旧条标记被取代
                    written += ids
                    continue
            written += await vs.remember([c])
        return written

    @staticmethod
    def _inherit_evidence(cur: dict, cand: dict) -> dict:
        """新条目继承旧条目的证据轮次与计数（冲突 supersede 路径）。"""
        import weighting
        out = dict(cand)
        merged = weighting.merge_evidence(cur.get("source_turn_ids") or "",
                                          out.get("source_turn_ids") or "")
        out["source_turn_ids"] = merged
        out["evidence_count"] = weighting.evidence_count(
            merged, fallback=int(cur.get("evidence_count") or 1))
        return out

    async def _reinforce(self, vs, cur: dict, cand: dict) -> None:
        """同一偏好复现 → 证据 +1、重算 weight 就地更新（不新增条目、不改 text）。

        **只动强度不动内容**：文本相同才走这条路，重写一遍没意义还会刷新 valid_from
        （那会让衰减基准漂移，等于把旧偏好洗成新的）。
        """
        import weighting
        merged = weighting.merge_evidence(cur.get("source_turn_ids") or "",
                                          cand.get("source_turn_ids") or "")
        count = weighting.evidence_count(
            merged, fallback=int(cur.get("evidence_count") or 1) + 1)
        # 证据串为空（存量条目没记轮次）时至少把计数往前推一格，否则永远加不上权
        if not merged:
            count = int(cur.get("evidence_count") or 1) + 1
        provenance = cur.get("provenance") or "user_stated"
        half_life = float(cur.get("half_life_days") or 0) or weighting.default_half_life(
            provenance, cur.get("review_status") or "")
        age = max(0, int(time.time()) - int(cur.get("valid_from") or 0))
        weight = weighting.compute_weight(provenance=provenance, evidence_count=count,
                                          age_seconds=age, half_life_days=half_life)
        await vs.reinforce(cur["id"], weight=weight, evidence_count=count,
                           source_turn_ids=merged, half_life_days=half_life)

    async def derive_routines(self, user_id: str, occupant_id: str = "primary",
                              min_count: int = 3) -> list[dict]:
        """从情景记忆派生 routine → 写 procedural 记忆（去重）+ 返回主动建议（供 agent.proactive）。
        实际投递经已有 proactive 通道，本方法只产出建议（与现状"投递一跳待接"对齐）。"""
        if not user_id:
            return []
        from routine import detect_routines
        vs = await self._vec()
        episodes = await vs.recall(user_id, occupant_id=occupant_id, query="",
                                   kinds=["episodic"], top_k=200)
        cands = detect_routines([e for e, _ in episodes], min_count=min_count)
        out = []
        for c in cands:
            suggestion = c.pop("suggestion", "")
            c["user_id"] = user_id
            c["occupant_id"] = occupant_id
            if await vs.current_by_predicate(user_id, occupant_id, c["predicate"]):
                continue  # 该 routine 已沉淀，不重复
            await vs.remember([c])
            out.append({"text": c["text"], "predicate": c["predicate"],
                        "suggestion": suggestion})
        return out

    @staticmethod
    def _desensitize(scope: str, data: dict) -> dict:
        """敏感数据脱敏。"""
        if scope == "vehicle.location":
            return {"city": data.get("city", ""), "road": ""}  # 只给城市，不给精确位置
        return data
