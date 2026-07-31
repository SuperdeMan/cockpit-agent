"""Memory gRPC 服务。"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time

from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc
from runtime.proactive import P_ADVISORY, publish_proactive

import voiceprint
from e2e_capability import (
    derive_runner_run_id,
    MemoryCapabilityError,
    decode_capability_secret,
    verify_memory_capability,
)
from store import ALL_OCCUPANTS, MemoryStore, OWNER_ONLY, TurnConflict, owner_of

logger = logging.getLogger("memory.server")

_CONSOLIDATE_EVERY = 4  # 每累积 N 轮触发一次异步抽取巩固
# routine 建议属**建议类**：可以攒着说（10 分钟 TTL），也可以不说——习惯建议不是
# 用户显式约定，赶上高负荷/免打扰就该让路。治理见 docs/conventions.md §9.8。
_ROUTINE_TTL_MS = 600_000

# 显式记忆陈述立即抽取（旅程 B3-3 M2）：「记住，我最喜欢26度」这类会话往往一问一答
# 就结束（2 轮），永远凑不满 4 轮节流窗 → 偏好从未被抽取。用户明说「记住/我喜欢」
# 即写入意图明确，等不起下个周期。
_REMEMBER_NOW_RE = re.compile(r"记住|记好|别忘了|我(最|比较|还是)?(喜欢|习惯|偏好)")

# 合成会话（eval/e2e/badcase 重放/探针）跳过 LLM 抽取巩固：不烧 token、不把测试对话
# 沉淀进真实用户画像（2026-07-13 消耗排查：抽取跟着 active provider 跑且 caller 为空，
# 是消耗归属盲区之一）。session_id 前缀仍标记合成会话；只有 AppendTurnRequest
# 专用字段里的 runner capability 严格绑定 run/user/session 后才能放行抽取。
# 短期轮次存取（GetSession）不受影响；memtest- 同样是不可信客户端标记。
_EXTRACT_SKIP_PREFIXES = tuple(
    p.strip() for p in os.getenv(
        "MEMORY_EXTRACT_SKIP_PREFIXES",
        "eval-,e2e-,ctxe2e-,central-,review-,nightly-,replay-,probe-,smoke-,memtest-",
    ).split(",") if p.strip())


class MemoryServicer(memory_pb2_grpc.MemoryServicer):
    def __init__(self):
        self.store = MemoryStore()
        self._turn_counts: dict[str, int] = {}  # session_id -> 累计轮数（抽取节流）
        self._bg: set = set()                    # 持有后台 consolidate task 引用
        self._nc = None                          # NATS 连接（主动建议投递，懒连）
        self._nats_tried = False
        self._e2e_capability_enabled = (
            os.getenv("E2E_CAPABILITY_ENABLED", "").strip().lower()
            in {"1", "true", "on", "yes"}
        )
        self._e2e_capability_secret: bytes | None = None
        if self._e2e_capability_enabled:
            try:
                self._e2e_capability_secret = decode_capability_secret(
                    os.getenv("E2E_CAPABILITY_SECRET", ""),
                )
            except MemoryCapabilityError:
                # 配置错误必须 fail closed；不要把 secret 或 bearer session 写日志。
                logger.error("memory E2E capability gate is misconfigured")

    async def GetContext(self, request, context):
        values = await self.store.get_context(
            request.session_id, request.user_id, request.vehicle_id,
            list(request.scopes), occupant_id=request.occupant_id)
        return memory_pb2.GetContextResponse(values=values)

    async def AppendTurn(self, request, context):
        # user_id 一并入库：维护 user→sessions 索引，ForgetUser 才能删到原始对话
        # occupant/turn/exchange 一并入库（M-B）：识别得出「谁在说话」而数据面存不下来，
        # 等于没识别——巩固会按触发者给整段混合历史归属。
        try:
            await self.store.append_turn(
                request.session_id, request.role, request.text,
                user_id=request.user_id, occupant_id=request.occupant_id,
                vehicle_id=request.vehicle_id, turn_id=request.turn_id,
                exchange_id=request.exchange_id)
        except TurnConflict:
            # 重放可以，改写不行：保留原 Turn 并如实报错，不静默覆盖已发生的对话。
            return memory_pb2.AppendTurnResponse(ok=False, error="turn_conflict")
        self._maybe_consolidate(request)
        return memory_pb2.AppendTurnResponse(ok=True)

    def _maybe_consolidate(self, request):
        """每 N 轮触发一次异步抽取巩固；显式记忆陈述（「记住/我喜欢」用户轮）立即触发。
        无 user_id（如端侧本地轮）或合成会话（session_id 命中 _EXTRACT_SKIP_PREFIXES）
        不触发。"""
        if not request.user_id:
            return
        sid = request.session_id
        if (
            sid.startswith(_EXTRACT_SKIP_PREFIXES)
            and not self._allows_synthetic_extraction(request)
        ):
            logger.debug("consolidate skipped for synthetic session")
            return
        # 节流键带 OwnerKey（M-B）：session 级计数会让「A 说三轮、B 说第四轮」在 B 名下
        # 触发一次，而 B 只说过一句——各自数各自的轮数，谁攒够谁巩固。
        uid, occ = owner_of(request.user_id, request.occupant_id)
        key = (sid, uid, occ)
        n = self._turn_counts.get(key, 0) + 1
        self._turn_counts[key] = n
        if request.role == "user" and _REMEMBER_NOW_RE.search(request.text or ""):
            self._turn_counts[key] = 0   # 立即触发后重新计数，防紧邻的周期触发双跑
        elif n % _CONSOLIDATE_EVERY != 0:
            return
        task = asyncio.create_task(self._consolidate_bg(
            sid, uid, occ, request.vehicle_id))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    def _allows_synthetic_extraction(self, request) -> bool:
        """Fail closed unless a dedicated runner capability binds this request."""

        secret = self._e2e_capability_secret
        if not self._e2e_capability_enabled or secret is None:
            return False
        capability = getattr(request, "e2e_memory_capability", "")
        if not capability:
            return False
        try:
            claims = verify_memory_capability(
                capability,
                secret,
            )
        except MemoryCapabilityError:
            return False
        try:
            expected_run_id = derive_runner_run_id(request.user_id)
        except MemoryCapabilityError:
            return False
        return (
            hmac.compare_digest(claims.user_id, request.user_id)
            and hmac.compare_digest(claims.run_id, expected_run_id)
            and hmac.compare_digest(claims.session_id, request.session_id)
        )

    async def _consolidate_bg(self, session_id, user_id, occupant_id, vehicle_id):
        try:
            ids = await self.store.consolidate(session_id, user_id, occupant_id, vehicle_id)
            if ids:
                logger.info("consolidate: +%d memories", len(ids))
            await self._derive_and_emit(user_id, occupant_id)
        except Exception as e:
            logger.debug("consolidate failed: %s", e)

    async def _derive_and_emit(self, user_id, occupant_id):
        """从情景记忆派生 routine，对新沉淀的 routine 发 agent.proactive 主动建议。"""
        routines = await self.store.derive_routines(user_id, occupant_id)
        for r in routines:
            await self._emit_proactive(
                r.get("suggestion", ""),
                r.get("predicate", ""),
                user_id,
                occupant_id,
            )

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

    async def _emit_proactive(
        self,
        suggestion: str,
        predicate: str,
        user_id: str,
        occupant_id: str,
    ):
        """向主动治理器发建议（治理器缺席则自动直发老主题，见 runtime/proactive.py）。"""
        nc = await self._ensure_nats()
        if not nc or not suggestion:
            return
        owner_fingerprint = hashlib.sha256(
            f"{user_id}\0{occupant_id}".encode("utf-8")
        ).hexdigest()[:16]
        payload = {"type": "routine_suggestion", "speech": suggestion,
                   "agent_id": "memory", "predicate": predicate,
                   "user_id": user_id, "occupant_id": occupant_id,
                   "ts": int(time.time() * 1000),
                   "priority": P_ADVISORY,
                   # 同一乘员的同一习惯在窗口内只说一次；身份只留不可逆指纹，
                   # 避免把用户标识塞进去重键与诊断面。
                   "dedup_key": (
                       f"memory.routine|{owner_fingerprint}|{predicate}"
                   ),
                   "ttl_ms": _ROUTINE_TTL_MS}
        await publish_proactive(nc, payload)
        logger.info("memory: 主动建议 %s", suggestion[:40])

    async def GetSession(self, request, context):
        # scope 未指定 = OWNER_ONLY：**共享绝不是缺省行为**，跨乘员必须显式声明，
        # 且只供 HMI 管理视图与合规导出。
        scope = (ALL_OCCUPANTS
                 if request.scope == memory_pb2.HISTORY_SCOPE_ALL_OCCUPANTS
                 else OWNER_ONLY)
        turns = await self.store.get_session(
            request.session_id, request.last_n or 6,
            user_id=request.user_id, occupant_id=request.occupant_id, scope=scope)
        return memory_pb2.GetSessionResponse(turns=[
            memory_pb2.Turn(
                role=t["role"], text=t["text"], ts=t["ts"],
                user_id=t.get("user_id", ""), vehicle_id=t.get("vehicle_id", ""),
                occupant_id=t.get("occupant_id", ""), turn_id=t.get("turn_id", ""),
                exchange_id=t.get("exchange_id", ""))
            for t in turns
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
        await self.store.upsert_profile(request.user_id, request.key, value,
                                        occupant_id=request.occupant_id)
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
        """合规：导出用户全量记忆 + 画像 + 关系边。"""
        data = await self.store.export_user(request.user_id) if request.user_id else {}
        return memory_pb2.ExportUserResponse(json=json.dumps(data, ensure_ascii=False))

    # ── M2 记忆图谱 P1：关系边 ────────────────────────────────────────
    async def QueryRelations(self, request, context):
        if not request.user_id:
            return memory_pb2.QueryRelationsResponse()
        edges = await self.store.relations(
            request.user_id, occupant_id=request.occupant_id or "primary",
            subject=request.subject, rel=request.rel, object_=request.object,
            limit=int(request.limit or 20))
        return memory_pb2.QueryRelationsResponse(
            edges=[_edge_to_proto(e) for e in edges])

    async def ResolvePersonPlace(self, request, context):
        """「去接孩子放学」的一跳解析。**查不到/有歧义一律 found=false**——
        调用方据此诚实追问，绝不猜（导航到错地方比查不到更糟）。"""
        if not request.user_id or not request.person_word:
            return memory_pb2.ResolvePersonPlaceResponse(found=False)
        hit = await self.store.resolve_person_place(
            request.user_id, request.person_word,
            occupant_id=request.occupant_id or "primary")
        if not hit:
            return memory_pb2.ResolvePersonPlaceResponse(found=False)
        return memory_pb2.ResolvePersonPlaceResponse(
            found=True, person=hit["person"], place=hit["place"],
            object_ref=hit.get("object_ref", ""))

    # ── 声纹（M4 P4）：只收向量不收音频；判定见 memory/voiceprint.py ──────────────
    async def EnrollVoiceprint(self, request, context):
        if not request.user_id or not request.samples:
            return memory_pb2.EnrollVoiceprintResponse(ok=False, error="no_samples")
        res = await self.store.enroll_voiceprint(
            request.user_id, [list(s.values) for s in request.samples],
            occupant_id=request.occupant_id, display_name=request.display_name,
            model=request.model, tenant_id=request.tenant_id or "default")
        if not res.get("ok"):
            return memory_pb2.EnrollVoiceprintResponse(
                ok=False, error=res.get("error", ""),
                self_consistency=float(res.get("self_consistency", 0.0)))
        logger.info("voiceprint enrolled: user=%s occupant=%s samples=%d consistency=%.3f",
                    request.user_id, res["occupant_id"], res["sample_count"],
                    res["self_consistency"])
        return memory_pb2.EnrollVoiceprintResponse(
            ok=True, occupant_id=res["occupant_id"], display_name=res["display_name"],
            sample_count=int(res["sample_count"]),
            self_consistency=float(res["self_consistency"]))

    async def IdentifySpeaker(self, request, context):
        if not request.user_id or not request.probe.values:
            return memory_pb2.IdentifySpeakerResponse(
                occupant_id="primary", decision="no_templates")
        res = await self.store.identify_speaker(
            request.user_id, list(request.probe.values), model=request.model,
            tenant_id=request.tenant_id or "default")
        return memory_pb2.IdentifySpeakerResponse(
            occupant_id=res["occupant_id"], display_name=res.get("display_name", ""),
            decision=res["decision"], score=float(res["score"]),
            runner_up=float(res["runner_up"]))

    async def ListVoiceprints(self, request, context):
        rows = await self.store.list_voiceprints(
            request.user_id, model=request.model,
            tenant_id=request.tenant_id or "default") if request.user_id else []
        return memory_pb2.ListVoiceprintsResponse(
            occupants=[memory_pb2.VoiceprintInfo(
                occupant_id=r["occupant_id"], display_name=r.get("display_name", ""),
                sample_count=int(r.get("sample_count") or 0),
                self_consistency=float(r.get("self_consistency") or 0.0),
                updated_at=int(r.get("updated_at") or 0), model=r.get("model", ""),
                stale=bool(r.get("stale"))) for r in rows],
            threshold=voiceprint.threshold(), margin=voiceprint.margin())

    async def DeleteVoiceprint(self, request, context):
        if not request.user_id or not request.occupant_id:
            return memory_pb2.DeleteVoiceprintResponse(ok=False)
        res = await self.store.delete_voiceprint(
            request.user_id, request.occupant_id, purge_memory=request.purge_memory,
            tenant_id=request.tenant_id or "default")
        logger.info("voiceprint deleted: user=%s occupant=%s templates=%d memories=%d",
                    request.user_id, request.occupant_id,
                    res["deleted_templates"], res["deleted_memories"])
        return memory_pb2.DeleteVoiceprintResponse(
            ok=res["ok"], deleted_templates=int(res["deleted_templates"]),
            deleted_memories=int(res["deleted_memories"]))

    async def RenameVoiceprint(self, request, context):
        """只改称呼不动模板：名字是元数据，不该和「重录三段声纹」绑一起。"""
        if not request.user_id or not request.occupant_id:
            return memory_pb2.RenameVoiceprintResponse(ok=False, error="empty_name")
        res = await self.store.rename_voiceprint(
            request.user_id, request.occupant_id, request.display_name,
            tenant_id=request.tenant_id or "default")
        if res.get("ok"):
            logger.info("voiceprint renamed: user=%s occupant=%s name=%s",
                        request.user_id, request.occupant_id, res["display_name"])
        return memory_pb2.RenameVoiceprintResponse(
            ok=bool(res.get("ok")), display_name=res.get("display_name", ""),
            error=res.get("error", ""))


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
        # M2 P0 偏好加权：weight=0 表示未参与加权，消费方回退 confidence（存量兼容）
        "weight": m.weight, "evidence_count": m.evidence_count,
        "half_life_days": m.half_life_days, "consent": m.consent,
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
        weight=float(d.get("weight", 0) or 0),
        evidence_count=int(d.get("evidence_count", 0) or 0),
        half_life_days=float(d.get("half_life_days", 0) or 0),
        consent=d.get("consent", "") or "",
        source_ts=int(d.get("source_ts", 0) or 0),
        source_session=d.get("source_session", "") or "")


def _edge_to_proto(e: dict):
    return memory_pb2.RelationEdge(
        subject=e.get("subject", "") or "", rel=e.get("rel", "") or "",
        object=e.get("object", "") or "", object_ref=e.get("object_ref", "") or "",
        confidence=float(e.get("confidence", 0) or 0),
        provenance=e.get("provenance", "") or "",
        source_turn_ids=e.get("source_turn_ids", "") or "")
