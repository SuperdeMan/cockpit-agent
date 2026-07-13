"""Memory gRPC 服务。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc

from store import MemoryStore

logger = logging.getLogger("memory.server")

_CONSOLIDATE_EVERY = 4  # 每累积 N 轮触发一次异步抽取巩固
_PROACTIVE_SUBJECT = "agent.proactive"

# 合成会话（eval/e2e/badcase 重放/探针）跳过 LLM 抽取巩固：不烧 token、不把测试对话
# 沉淀进真实用户画像（2026-07-13 消耗排查：抽取跟着 active provider 跑且 caller 为空，
# 是消耗归属盲区之一）。AppendTurnRequest 无 meta 字段，session_id 前缀是零 proto 变更
# 的显式契约（见 docs/conventions.md §9.2）；短期轮次存取（GetSession）不受影响。
# 记忆管线自测用 memtest- 前缀（刻意不在此列，e2e_memory 靠它验证抽取链路）。
_EXTRACT_SKIP_PREFIXES = tuple(
    p.strip() for p in os.getenv(
        "MEMORY_EXTRACT_SKIP_PREFIXES",
        "eval-,e2e-,ctxe2e-,central-,review-,nightly-,replay-,probe-,smoke-",
    ).split(",") if p.strip())


class MemoryServicer(memory_pb2_grpc.MemoryServicer):
    def __init__(self):
        self.store = MemoryStore()
        self._turn_counts: dict[str, int] = {}  # session_id -> 累计轮数（抽取节流）
        self._bg: set = set()                    # 持有后台 consolidate task 引用
        self._nc = None                          # NATS 连接（主动建议投递，懒连）
        self._nats_tried = False

    async def GetContext(self, request, context):
        values = await self.store.get_context(
            request.session_id, request.user_id, request.vehicle_id, list(request.scopes))
        return memory_pb2.GetContextResponse(values=values)

    async def AppendTurn(self, request, context):
        await self.store.append_turn(request.session_id, request.role, request.text)
        self._maybe_consolidate(request)
        return memory_pb2.AppendTurnResponse(ok=True)

    def _maybe_consolidate(self, request):
        """每 N 轮触发一次异步抽取巩固。无 user_id（如端侧本地轮）或合成会话
        （session_id 命中 _EXTRACT_SKIP_PREFIXES）不触发。"""
        if not request.user_id:
            return
        sid = request.session_id
        if sid.startswith(_EXTRACT_SKIP_PREFIXES):
            logger.debug("consolidate skipped for synthetic session %s", sid)
            return
        n = self._turn_counts.get(sid, 0) + 1
        self._turn_counts[sid] = n
        if n % _CONSOLIDATE_EVERY != 0:
            return
        task = asyncio.create_task(self._consolidate_bg(
            sid, request.user_id, request.occupant_id or "primary", request.vehicle_id))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _consolidate_bg(self, session_id, user_id, occupant_id, vehicle_id):
        try:
            ids = await self.store.consolidate(session_id, user_id, occupant_id, vehicle_id)
            if ids:
                logger.info("consolidate %s: +%d memories", session_id, len(ids))
            await self._derive_and_emit(user_id, occupant_id)
        except Exception as e:
            logger.debug("consolidate failed: %s", e)

    async def _derive_and_emit(self, user_id, occupant_id):
        """从情景记忆派生 routine，对新沉淀的 routine 发 agent.proactive 主动建议。"""
        routines = await self.store.derive_routines(user_id, occupant_id)
        for r in routines:
            await self._emit_proactive(r.get("suggestion", ""), r.get("predicate", ""))

    async def _ensure_nats(self):
        if self._nc is not None:
            return self._nc
        if self._nats_tried:
            return None
        self._nats_tried = True
        url = os.getenv("NATS_URL", "")
        if not url:
            return None
        try:
            import nats
            self._nc = await nats.connect(url, max_reconnect_attempts=-1)
        except Exception as e:
            logger.warning("memory: NATS 连接失败，主动建议禁用：%s", e)
            self._nc = None
        return self._nc

    async def _emit_proactive(self, suggestion: str, predicate: str):
        """向 NATS 发主动建议（best-effort，复用 agent.proactive；HMI 投递为既有待接一跳）。"""
        nc = await self._ensure_nats()
        if not nc or not suggestion:
            return
        payload = {"type": "routine_suggestion", "speech": suggestion,
                   "agent_id": "memory", "predicate": predicate,
                   "ts": int(time.time() * 1000)}
        try:
            await nc.publish(_PROACTIVE_SUBJECT,
                             json.dumps(payload, ensure_ascii=False).encode())
            logger.info("memory: 主动建议 %s", suggestion[:40])
        except Exception as e:
            logger.debug("memory: 主动建议发布失败：%s", e)

    async def GetSession(self, request, context):
        turns = await self.store.get_session(request.session_id, request.last_n or 6)
        return memory_pb2.GetSessionResponse(turns=[
            memory_pb2.Turn(role=t["role"], text=t["text"], ts=t["ts"]) for t in turns
        ])

    async def UpsertProfile(self, request, context):
        """写用户画像字段（如常用地点 places）。value_json 非法则拒绝，不写脏数据。"""
        if not request.user_id or not request.key:
            return memory_pb2.UpsertProfileResponse(ok=False)
        try:
            value = json.loads(request.value_json) if request.value_json else None
        except json.JSONDecodeError as e:
            logger.warning("UpsertProfile bad json (%s/%s): %s",
                           request.user_id, request.key, e)
            return memory_pb2.UpsertProfileResponse(ok=False)
        await self.store.upsert_profile(request.user_id, request.key, value)
        return memory_pb2.UpsertProfileResponse(ok=True)

    # ── 分层记忆（语义画像 / 情景）──────────────────────────
    async def Remember(self, request, context):
        """写语义/情景记忆（抽取管线或 Agent 显式）。无 user_id 的条目跳过。"""
        items = [_item_to_dict(m) for m in request.items if m.user_id]
        if not items:
            return memory_pb2.RememberResponse(ok=False)
        ids = await self.store.remember(items)
        return memory_pb2.RememberResponse(ids=ids, ok=True)

    async def Recall(self, request, context):
        """语义召回（向量 + scope/occupant + 时序融合）。"""
        if not request.user_id:
            return memory_pb2.RecallResponse()
        pairs = await self.store.recall(
            user_id=request.user_id, occupant_id=request.occupant_id,
            query=request.query, scopes=list(request.scopes), kinds=list(request.kinds),
            top_k=request.top_k, include_superseded=request.include_superseded,
            predicate_prefix=request.predicate_prefix, min_score=request.min_score,
            min_confidence=request.min_confidence, max_age_days=request.max_age_days)
        resp = memory_pb2.RecallResponse()
        for d, score in pairs:
            resp.items.append(_dict_to_item(d))
            resp.scores.append(float(score))
        return resp

    async def ForgetUser(self, request, context):
        """合规：删除用户全量记忆。"""
        if not request.user_id:
            return memory_pb2.ForgetUserResponse(ok=False)
        n = await self.store.forget_user(
            request.user_id, request.occupant_id, list(request.scopes))
        return memory_pb2.ForgetUserResponse(ok=True, deleted=n)

    async def ExportUser(self, request, context):
        """合规：导出用户全量记忆 + 画像。"""
        data = await self.store.export_user(request.user_id) if request.user_id else {}
        return memory_pb2.ExportUserResponse(json=json.dumps(data, ensure_ascii=False))


def _item_to_dict(m) -> dict:
    return {
        "id": m.id, "kind": m.kind, "tenant_id": m.tenant_id, "user_id": m.user_id,
        "occupant_id": m.occupant_id, "vehicle_id": m.vehicle_id,
        "memory_level": m.memory_level, "predicate": m.predicate, "text": m.text,
        "value_json": m.value_json, "embedding_model": m.embedding_model,
        "provenance": m.provenance, "confidence": m.confidence,
        "review_status": m.review_status, "scope": m.scope, "privacy_level": m.privacy_level,
        "valid_from": m.valid_from, "valid_to": m.valid_to, "expires_at": m.expires_at,
        "superseded_by": m.superseded_by, "source_turn_ids": m.source_turn_ids,
        "source_ts": m.source_ts, "source_session": m.source_session,
    }


def _dict_to_item(d: dict):
    return memory_pb2.MemoryItem(
        id=d.get("id", "") or "", kind=d.get("kind", "") or "",
        tenant_id=d.get("tenant_id", "") or "", user_id=d.get("user_id", "") or "",
        occupant_id=d.get("occupant_id", "") or "", vehicle_id=d.get("vehicle_id", "") or "",
        memory_level=d.get("memory_level", "") or "", predicate=d.get("predicate", "") or "",
        text=d.get("text", "") or "", value_json=d.get("value_json", "") or "",
        embedding_model=d.get("embedding_model", "") or "",
        provenance=d.get("provenance", "") or "", confidence=float(d.get("confidence", 0) or 0),
        review_status=d.get("review_status", "") or "", scope=d.get("scope", "") or "",
        privacy_level=d.get("privacy_level", "") or "",
        valid_from=int(d.get("valid_from", 0) or 0), valid_to=int(d.get("valid_to", 0) or 0),
        expires_at=int(d.get("expires_at", 0) or 0),
        superseded_by=d.get("superseded_by", "") or "",
        source_turn_ids=d.get("source_turn_ids", "") or "",
        source_ts=int(d.get("source_ts", 0) or 0),
        source_session=d.get("source_session", "") or "")
