"""Edge Orchestrator gRPC 服务：快意图本地秒回，慢意图上云，云端不可达则降级。

Phase 1 改进：云端 action 分发（车控→VAL）、连接状态追踪、降级增强。
"""
from __future__ import annotations
import os
import asyncio
import logging
import time

import grpc
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from cockpit.orchestrator.v1 import orchestrator_pb2, orchestrator_pb2_grpc
from cockpit.common.v1 import common_pb2
from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc

from fast_intent import classify, classify_structured, is_local, split_and_classify, split_and_classify_any, structured_to_legacy
from val import VAL
from edge_agents import edge_execute
from cloud_client import CloudClient
from edge_call import EdgeCallExecutor
from observability.events import EventEmitter, change_source
from observability.tracing import get_trace_id, new_trace_id, set_trace_id

logger = logging.getLogger("edge.orchestrator")

_HIGH = float(os.getenv("FAST_INTENT_THRESHOLD_HIGH", "0.85"))


def _ensure_trace_id(request) -> str:
    """Preserve a caller trace ID or create one and forward it in request meta."""
    trace_id = request.meta.get("trace_id") if request.meta else ""
    if not trace_id:
        trace_id = new_trace_id()
    request.meta["trace_id"] = trace_id
    set_trace_id(trace_id)
    return trace_id


def _struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d or {})
    return s


def _state_changes(before: dict, after: dict) -> list[dict]:
    return [
        {"key": key, "old": before.get(key), "new": value}
        for key, value in after.items()
        if before.get(key) != value
    ]


def _group_mixed_intents(intents: list[dict]) -> list[list[dict]]:
    """把无法独立分类的续接片段附着到前一个主意图，避免丢失上下文。"""
    groups: list[list[dict]] = []
    for intent in intents:
        raw = (intent.get("_raw_text") or "").strip().rstrip("。！？!?")
        if intent.get("_needs_cloud") and raw in {
                "出发", "出发吧", "走吧", "开始导航", "开始出发", "带路吧", "导航吧"}:
            for group in reversed(groups):
                if any(
                        item.get("_needs_cloud")
                        or not structured_to_legacy(item)
                        or not is_local(structured_to_legacy(item)["name"])
                        for item in group):
                    group.append(intent)
                    break
            else:
                groups.append([intent])
            continue
        if intent.get("_needs_cloud") and groups:
            groups[-1].append(intent)
        else:
            groups.append([intent])
    return groups


class _MemoryClient:
    """端侧对话记忆写入（best-effort）：让纯本地快意图也进共享记忆，
    云端跟进指代消解（"再高一点"）才有上下文。失败静默，不阻塞快路径、不破坏离线。"""

    def __init__(self):
        self.addr = os.getenv("MEMORY_ADDR", "memory:50053")
        self._ch: grpc.aio.Channel | None = None

    def _stub(self):
        if self._ch is None:
            self._ch = grpc.aio.insecure_channel(self.addr)
        return memory_pb2_grpc.MemoryStub(self._ch)

    async def append(self, session_id: str, role: str, text: str):
        try:
            await self._stub().AppendTurn(
                memory_pb2.AppendTurnRequest(
                    session_id=session_id, role=role, text=text),
                timeout=5)
        except Exception as e:  # 离线/记忆不可用 → 静默跳过
            logger.debug("edge memory append failed: %s", e)


class EdgeOrchestratorServicer(orchestrator_pb2_grpc.EdgeOrchestratorServicer):
    _DEBUG_KEYS = {"speed_kmh", "battery", "gear", "location"}

    def __init__(self):
        self.obs = EventEmitter("edge")
        self._state_q: asyncio.Queue = asyncio.Queue()
        self._change_source = change_source
        self._get_trace_id = get_trace_id

        def _on_change(changes):
            try:
                self._state_q.put_nowait(
                    (
                        changes,
                        self._change_source.get(),
                        self._get_trace_id(),
                    )
                )
            except Exception:
                pass

        self.val = VAL(on_change=_on_change)
        self.cloud = CloudClient(edge_call_executor=EdgeCallExecutor(self.val))
        self.cloud_connected = False  # 连接状态追踪
        self.memory = _MemoryClient()
        self._bg: set[asyncio.Task] = set()  # 持有 fire-and-forget 任务引用，防 GC

    async def drain_state(self):
        """Publish queued state changes without blocking vehicle control."""
        while True:
            changes, source, trace_id = await self._state_q.get()
            try:
                await self.obs.emit_state(
                    changes,
                    source=source,
                    trace_id=trace_id,
                )
            finally:
                self._state_q.task_done()

    async def emit_snapshot(self):
        """Publish the complete initial vehicle-state mirror."""
        changes = [
            {"key": key, "old": None, "new": value}
            for key, value in self.val.state.items()
        ]
        await self.obs.emit_state(changes, source="snapshot")

    def apply_debug(self, key: str, value) -> bool:
        """Update a simulated environment value through a strict whitelist."""
        if key not in self._DEBUG_KEYS:
            return False
        if key in {"speed_kmh", "battery"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            upper = 300 if key == "speed_kmh" else 100
            if value < 0 or value > upper:
                return False
        elif key == "gear":
            if not isinstance(value, str) or value.upper() not in {
                "P",
                "R",
                "N",
                "D",
                "S",
            }:
                return False
            value = value.upper()
        elif value is not None and not isinstance(value, (str, dict)):
            return False

        self._change_source.set("debug")
        self.val.set_env(key, value)
        return True

    async def _emit_span(self, trace_id: str, node: str, **kwargs):
        try:
            await self.obs.emit_span(trace_id, node, **kwargs)
        except Exception:
            pass

    async def _execute_val_observed(
        self,
        trace_id: str,
        command,
        args: dict | None = None,
        answer_length: str = "short",
        intent: str = "",
    ):
        started = time.perf_counter()
        before = dict(self.val.state)
        ok, speech = self.val.execute(
            command,
            args,
            answer_length=answer_length,
        )
        changes = _state_changes(before, self.val.state)
        await self._emit_span(
            trace_id,
            "val.execute",
            status="ok" if ok else "err",
            duration_ms=(time.perf_counter() - started) * 1000,
            attrs={
                **({"intent": intent} if intent else {}),
                "changes": changes,
            },
        )
        return ok, speech

    def _confirm_required(self, structured: dict | None) -> bool:
        """该结构化指令的对象是否需要二次确认（trunk/door_lock/油箱盖/充电口盖）。
        危险动作不走本地秒回——落到云端经 edge_call→NEED_CONFIRM 闭环（CLAUDE.md 安全红线）。"""
        if not structured:
            return False
        obj = structured.get("data", {}).get("object", "")
        return bool(obj) and self.val._need_confirm(obj)

    def _record_local_turn(self, request, user_text: str, assistant_speech: str):
        """把纯本地处理的一轮 best-effort 异步写入共享记忆（gated on memory_enabled）。"""
        meta = dict(request.meta) if request.meta else {}
        if meta.get("memory_enabled", "true") == "false":
            return
        if not request.session_id or not user_text:
            return

        async def _write():
            await self.memory.append(request.session_id, "user", user_text)
            if assistant_speech:
                await self.memory.append(request.session_id, "assistant", assistant_speech)

        task = asyncio.create_task(_write())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def Handle(self, request, context):
        trace_id = _ensure_trace_id(request)
        self._change_source.set("T0")
        # 从 request.meta 读取 HMI 设置
        meta = dict(request.meta) if request.meta else {}
        answer_length = meta.get("answer_length", "short")

        # 确认/补槽续接必须回到挂起会话所在的云端，不走本地快路径
        if request.is_confirmation:
            intent = None
            multi = None
            mixed_intents = None
        else:
            multi = split_and_classify(request.text)
            mixed_intents = None
            if multi:
                intent = None
            else:
                # 全有全无失败 → 尝试混合拆分（本地+非本地）
                mixed_intents = split_and_classify_any(request.text)
                intent = None if mixed_intents else classify(request.text)

        # 快路径 A：多意图全部本地，并行执行聚合语音
        if multi:
            speeches = []
            actions = []
            for m_intent in multi:
                legacy = structured_to_legacy(m_intent)
                if (legacy and legacy["confidence"] >= _HIGH and is_local(legacy["name"])
                        and not self._confirm_required(m_intent)):
                    # 结构化命令直通 VAL
                    ok, speech = await self._execute_val_observed(
                        trace_id,
                        m_intent,
                        answer_length=answer_length,
                        intent=legacy["name"],
                    )
                    if not ok:
                        speech = speech or "操作失败"
                    speeches.append(speech)
                    # 构造 action
                    obj = m_intent.get("data", {}).get("object", "")
                    action_type = "media.control" if obj in ("media", "music", "radio", "online_radio", "audiobook", "opera", "news", "video", "TV") else "vehicle.control"
                    actions.append(common_pb2.AgentAction(
                        type=action_type,
                        payload=_struct({"command": legacy["name"], **legacy.get("slots", {})}),
                        require_confirm=False,
                    ))
                    logger.info("MULTI-LOCAL %s -> %s", legacy["name"], speech)
                else:
                    # 子意图无法本地处理 / 需二次确认 → 整句走云（保守策略）
                    logger.info("MULTI sub-intent needs cloud, falling through")
                    speeches = []
                    break
            if speeches:
                await self._emit_span(
                    trace_id,
                    "route.multi",
                    attrs={"count": len(actions)},
                )
                combined = "，".join(speeches)
                final = orchestrator_pb2.FinalResult(speech=combined)
                final.actions.extend(actions)
                yield orchestrator_pb2.HandleEvent(final=final)
                self._record_local_turn(request, request.text, combined)
                return

        # 快路径 A2：混合意图（部分本地 + 部分非本地）。
        # 本地意图立即经 VAL 执行，非本地意图上云编排。
        if mixed_intents:
            local_speeches = []
            local_actions = []
            cloud_parts = []  # 非本地意图的原始文本片段
            for group in _group_mixed_intents(mixed_intents):
                local_group = []
                for m_intent in group:
                    legacy = structured_to_legacy(m_intent)
                    if (not m_intent.get("_needs_cloud")
                            and legacy
                            and legacy["confidence"] >= _HIGH
                            and is_local(legacy["name"])
                            and not self._confirm_required(m_intent)):
                        local_group.append((m_intent, legacy))
                    else:
                        local_group = []
                        break

                if local_group:
                    for m_intent, legacy in local_group:
                        ok, speech = await self._execute_val_observed(
                            trace_id,
                            m_intent,
                            answer_length=answer_length,
                            intent=legacy["name"],
                        )
                        if not ok:
                            speech = speech or "操作失败"
                        local_speeches.append(speech)
                        obj = m_intent.get("data", {}).get("object", "")
                        action_type = "media.control" if obj in ("media", "music", "radio", "online_radio", "audiobook", "opera", "news", "video", "TV") else "vehicle.control"
                        local_actions.append(common_pb2.AgentAction(
                            type=action_type,
                            payload=_struct({"command": legacy["name"], **legacy.get("slots", {})}),
                            require_confirm=False,
                        ))
                        logger.info("MIXED-LOCAL %s -> %s", legacy["name"], speech)
                else:
                    # 组内任一片段需上云时，整组上云，保留目的地/路线偏好、
                    # 媒体动作/歌手等相邻片段之间的语义上下文。
                    for m_intent in group:
                        raw = m_intent.get("_raw_text", "")
                        if raw:
                            cloud_parts.append(raw)
                    logger.info(
                        "MIXED-CLOUD group=%s",
                        [item.get("data", {}).get("object", "") for item in group],
                    )

            if cloud_parts:
                await self._emit_span(
                    trace_id,
                    "route.mixed",
                    attrs={
                        "local_actions": len(local_actions),
                        "cloud_parts": len(cloud_parts),
                    },
                )
                # 有非本地意图：先返回本地结果，再把非本地片段上云
                if local_speeches:
                    combined = "，".join(local_speeches)
                    final = orchestrator_pb2.FinalResult(speech=combined)
                    final.actions.extend(local_actions)
                    yield orchestrator_pb2.HandleEvent(final=final)
                    # R6：本地 final 会清空 HMI 占位气泡；立刻补一个云段占位，
                    # 让慢意图气泡即时出现（配合 HMI 对 speech_delta 新建气泡），
                    # 消除规划期~1s 盲等。
                    yield orchestrator_pb2.HandleEvent(
                        speech_delta="正在为您处理其他请求…")

                # 把非本地子句拼接后上云
                cloud_text = "，".join(cloud_parts)
                logger.info("MIXED: local done, sending to cloud: %s", cloud_text[:60])
                try:
                    got = False
                    # 构造只含非本地子句的请求副本
                    cloud_req = orchestrator_pb2.HandleRequest(
                        text=cloud_text,
                        session_id=request.session_id,
                        request_id=request.request_id,
                        is_confirmation=False,
                        meta=request.meta,
                        context=request.context,
                    )
                    # 已在端侧给过云段占位时，让云端别再重复"正在为您处理"（避免双占位文案）
                    if local_speeches:
                        cloud_req.meta["_mixed_subrequest"] = "1"
                    async for event in self.cloud.handle(cloud_req):
                        got = True
                        self.cloud_connected = True
                        event = self._dispatch_cloud_actions(event, answer_length)
                        yield event
                    if not got:
                        yield orchestrator_pb2.HandleEvent(
                            final=orchestrator_pb2.FinalResult(
                                speech="非本地请求处理失败，请稍后重试。"))
                except Exception as e:
                    self.cloud_connected = False
                    logger.warning("MIXED cloud unavailable: %s", e)
                    yield orchestrator_pb2.HandleEvent(
                        final=orchestrator_pb2.FinalResult(
                            speech="网络不太好，部分请求暂时无法处理。"))
                return
            else:
                # 全部本地（不应该到这里，multi 应该已经捕获）
                if local_speeches:
                    combined = "，".join(local_speeches)
                    final = orchestrator_pb2.FinalResult(speech=combined)
                    final.actions.extend(local_actions)
                    yield orchestrator_pb2.HandleEvent(final=final)
                return

        # 快路径 B：高置信本地意图，端侧秒回（离线可用，不依赖网络）
        if intent and intent["confidence"] >= _HIGH and is_local(intent["name"]):
            # 尝试结构化命令直通 VAL（覆盖新意图：trunk/door_lock/seat/ambient_light 等）
            structured = classify_structured(request.text)
            # 危险动作（trunk/door_lock/油箱盖/充电口盖）不秒回，落云端走二次确认闭环
            if not self._confirm_required(structured):
                if structured:
                    ok, speech = await self._execute_val_observed(
                        trace_id,
                        structured,
                        answer_length=answer_length,
                        intent=intent["name"],
                    )
                    action_type = "vehicle.control" if structured.get("data", {}).get("object") not in ("media",) else "media.control"
                    action = {
                        "type": action_type,
                        "payload": {"command": intent["name"], **intent.get("slots", {})},
                        "require_confirm": False,
                    } if ok else None
                else:
                    # 回退旧路径
                    started = time.perf_counter()
                    before = dict(self.val.state)
                    speech, action = edge_execute(intent, self.val)
                    await self._emit_span(
                        trace_id,
                        "val.execute",
                        status="ok" if action else "err",
                        duration_ms=(time.perf_counter() - started) * 1000,
                        attrs={
                            "intent": intent["name"],
                            "changes": _state_changes(before, self.val.state),
                        },
                    )
                final = orchestrator_pb2.FinalResult(speech=speech)
                if action:
                    final.actions.append(common_pb2.AgentAction(
                        type=action["type"], payload=_struct(action["payload"]),
                        require_confirm=action["require_confirm"]))
                logger.info("LOCAL %s -> %s", intent["name"], speech)
                await self._emit_span(
                    trace_id,
                    "route.local",
                    attrs={
                        "intent": intent["name"],
                        "confidence": intent["confidence"],
                    },
                )
                yield orchestrator_pb2.HandleEvent(final=final)
                self._record_local_turn(request, request.text, speech)
                return
            logger.info("LOCAL confirm-required %s -> route to cloud", intent["name"])

        # 慢路径：上云编排
        logger.info("CLOUD route: %s", request.text)
        await self._emit_span(
            trace_id,
            "route.cloud",
            attrs={"text": request.text[:40]},
        )
        cloud_speech = ""
        cloud_has_actions = False
        try:
            got = False
            async for event in self.cloud.handle(request):
                got = True
                self.cloud_connected = True
                # 云端回流 action 分发：车控类走 VAL
                event = self._dispatch_cloud_actions(event, answer_length)
                which = event.WhichOneof("event")
                if which == "final":
                    cloud_speech = event.final.speech
                    cloud_has_actions = len(event.final.actions) > 0
                yield event
            if not got:
                yield orchestrator_pb2.HandleEvent(
                    final=orchestrator_pb2.FinalResult(speech="抱歉，我没能理解这个请求。"))
                return
        except Exception as e:
            self.cloud_connected = False
            logger.warning("Cloud unavailable, degrade: %s", e)
            yield orchestrator_pb2.HandleEvent(final=orchestrator_pb2.FinalResult(
                speech="网络不太好，复杂请求暂时无法处理，不过车内控制依然可以正常使用。"))
            return

        # 兜底：云端返回空 speech 且无 actions → 尝试端侧 VAL 本地执行
        # 场景：LLM 规划失败 → chitchat 兜底但无实质回复 → 但原意可能是车控
        if not cloud_speech and not cloud_has_actions:
            local_structured = classify_structured(request.text)
            if local_structured:
                ok, speech = await self._execute_val_observed(
                    trace_id,
                    local_structured,
                    answer_length=answer_length,
                    intent=local_structured.get("intent", ""),
                )
                if ok and speech:
                    obj = local_structured.get("data", {}).get("object", "")
                    action_type = "media.control" if obj in ("media", "music", "radio") else "vehicle.control"
                    action = common_pb2.AgentAction(
                        type=action_type,
                        payload=_struct({"command": f"{obj}.{local_structured['data'].get('operate', '')}"}),
                        require_confirm=False,
                    )
                    final = orchestrator_pb2.FinalResult(speech=speech)
                    final.actions.append(action)
                    logger.info("CLOUD-DEGRADED-LOCAL %s -> %s", obj, speech)
                    yield orchestrator_pb2.HandleEvent(final=final)

    def _dispatch_cloud_actions(self, event, answer_length="short"):
        """云端回流 action 分发：车控类交 VAL 执行，落实规划/执行分离。

        LLM/Planner 只产出 vehicle.control 意图，真正下发由端侧 VAL 做：
        1. 权限校验
        2. 安全态门控（行驶中禁某些操作）
        3. 状态变更
        """
        which = event.WhichOneof("event")
        if which != "final":
            return event

        final = event.final
        new_speech = final.speech
        for action in final.actions:
            if not action.type.startswith("vehicle.control"):
                continue
            payload = MessageToDict(
                action.payload, preserving_proto_field_name=True
            ) if action.payload else {}
            # 已在车端 VAL 执行过（中枢 edge_call 回流），仅展示不二次下发，避免双发。
            if payload.get("_origin") == "edge_val":
                continue
            cmd = payload.get("command", action.type)
            ok, msg = self.val.execute(cmd, payload, answer_length=answer_length)
            if ok:
                logger.info("VAL executed: %s -> %s", cmd, msg)
                new_speech = msg  # 用 VAL 执行结果替换 speech
            else:
                logger.warning("VAL rejected: %s -> %s", cmd, msg)
                new_speech = msg  # 安全门控拒绝：替换为拒绝原因

        # F14：真正替换 speech（之前构建了 dispatched_actions 但丢弃了）
        final.speech = new_speech
        return event
