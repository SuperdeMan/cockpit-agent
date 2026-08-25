"""Edge Orchestrator gRPC 服务：快意图本地秒回，慢意图上云，云端不可达则降级。

Phase 1 改进：云端 action 分发（车控→VAL）、连接状态追踪、降级增强。
"""
from __future__ import annotations
import os
import asyncio
import logging
import time
import uuid
from collections import OrderedDict

import grpc
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from cockpit.orchestrator.v1 import orchestrator_pb2, orchestrator_pb2_grpc
from cockpit.common.v1 import common_pb2
from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc

from runtime.grpcio import aio_channel
from fast_intent import classify, classify_structured, climate_feeling_intents, is_local, is_negated_write_directive, is_sequence_connector, split_and_classify, split_and_classify_any, structured_to_legacy
import nlu as edge_nlu          # M5 P3 端侧语义 NLU（默认 shadow：只算不用）
from val import VAL
from edge_agents import edge_execute
from cloud_client import CloudClient
from edge_call import EdgeCallExecutor, action_to_structured, action_type_for
from observability.events import EventEmitter, change_source
from observability.tracing import (get_trace_id, new_trace_id, set_session_id,
                                   set_trace_id)

logger = logging.getLogger("edge.orchestrator")

_HIGH = float(os.getenv("FAST_INTENT_THRESHOLD_HIGH", "0.85"))
_LOCAL_EXCHANGE_MAX = 256
_LOCAL_EXCHANGE_TTL_S = 10 * 60
_LOCAL_ID_MAX_CHARS = 256
_LOCAL_EXCHANGE_ID_MAX_CHARS = 128


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


def _join_speeches(speeches: list[str]) -> str:
    """聚合多条本地播报：去掉空串与相邻重复（如多个高耗电动作各报一次"电量过低"，
    只保留一次），用顿号连接。"""
    out: list[str] = []
    for s in speeches:
        s = (s or "").strip()
        if s and (not out or out[-1] != s):
            out.append(s)
    return "，".join(out)


def _ui_card_type(final) -> str:
    """从 FinalResult.ui_card(Struct) 读卡片类型（拒识/澄清/业务卡），供轮次状态判定。"""
    try:
        fields = final.ui_card.fields
        if "type" in fields:
            return fields["type"].string_value or ""
    except Exception:
        pass
    return ""


def _starts_new_act(intent: dict) -> bool:
    """这个上云片段是**新的一件事**，不是上一句的补语。

    两个正信号，都只把片段从「粘」推向「独立」，缺证据时仍按保守的粘连处理：
    ① 端侧认出它属于哪个云侧域（提醒/场景/记忆）——**认得出就说明它自成一句**；
    ② 分隔它的是顺承连词（然后/再/并且/顺便…）——顺承引出新动作，而补语
       （「周杰伦的」「走最快的那条路」）跟在**裸逗号**后面。
    """
    return bool(intent.get("_cloud_domain")) or is_sequence_connector(intent.get("_sep", ""))


def _group_mixed_intents(intents: list[dict]) -> list[list[dict]]:
    """把无法独立分类的续接片段附着到前一个主意图，避免丢失上下文。

    ⚠ 只有**续接片段**该被附着。旧实现对所有 `_needs_cloud` 片段一律附着，于是
    「音量调小一点，提醒我八点开会」「打开座椅加热，再找个充电站」整句上云——端侧秒回
    退化成整句上云，断网时本地那半条也跟着失效（对抗测试 ei.mixed.volume-reminder /
    ei.mixed.seat-charging）。判据见 `_starts_new_act`。
    """
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
        if intent.get("_needs_cloud") and groups and not _starts_new_act(intent):
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
            self._ch = aio_channel(self.addr)
        return memory_pb2_grpc.MemoryStub(self._ch)

    async def append(self, session_id: str, role: str, text: str, *,
                     user_id: str = "", occupant_id: str = "", vehicle_id: str = "",
                     turn_id: str = "", exchange_id: str = "", actions=None):
        """M-B：端侧轮次也带 OwnerKey。

        此前这里只传 session/role/text——于是端侧处理的每一轮都是**无主**的，
        云端切到 OWNER_ONLY 后它们会全部落进 primary 桶。车控/媒体本身没有偏好可抽，
        但「谁说的」这一维在端侧丢掉后，乘员 B 的本地轮次会被记成主驾说的。

        **Q6（2026-08-16）再补一维：`actions`（本轮真实执行了什么）。** 同款理由——
        端侧秒回的 313 个动作**云侧一个都看不到**，「刚才实际执行了什么」于是只能
        由 LLM 从话术里猜（真栈三次取样三个样，一次直接否认执行过）。
        识别得出「做了什么」而数据面存不下来，等于没记录。
        """
        try:
            await self._stub().AppendTurn(
                memory_pb2.AppendTurnRequest(
                    session_id=session_id, role=role, text=text,
                    user_id=user_id, occupant_id=occupant_id or "primary",
                    vehicle_id=vehicle_id, turn_id=turn_id, exchange_id=exchange_id,
                    actions=list(actions or [])),
                timeout=5)
        except Exception as e:  # 离线/记忆不可用 → 静默跳过
            logger.debug("edge memory append failed: %s", e)


class EdgeOrchestratorServicer(orchestrator_pb2_grpc.EdgeOrchestratorServicer):
    # cabin_temp：座舱温度传感器（场景策略的环境分支据此选制冷/制热），可模拟压值
    _DEBUG_KEYS = {"speed_kmh", "battery", "gear", "location", "cabin_temp"}

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
        self._last_local_exchange: OrderedDict[
            tuple[str, str, str], tuple[str, float, tuple[str, ...]]
        ] = OrderedDict()
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

    @staticmethod
    def _theta_band(conf: float) -> str:
        """θ 双阈值落在哪一档：`high`（会本地执行）/`mid`（会带初判上云）/`low`（裸句上云）。

        **只记不用**——挡位仍是 shadow，这里一个字都不影响路由。它存在的理由是把
        `theta_high`/`theta_low` 从「运行时零消费方」变成有读者的契约（2026-07-30 评审
        的 INFO 项：本仓「没消费方的契约会潜伏」教训的形态；同一个 `theta_low` 在 P3a 之前
        就已经在 .env/compose/conventions 里躺了三处而代码只读 _HIGH）。

        更实际的收益是：P3b 的开工判据要的是「θ=0.9 时会有多少请求被本地执行、其中多少
        与规则分歧」——这两个数只有把档位和四态一起落盘才算得出来，事后从 conf 反推要
        重跑全部历史。
        """
        if conf >= edge_nlu.theta_high():
            return "high"
        return "mid" if conf >= edge_nlu.theta_low() else "low"

    def _nlu_shadow_bg(self, trace_id: str, text: str, path: str,
                       rule_objects: list[str] | None = None) -> None:
        """把影子推理**排到响应之后**跑，落独立的 `nlu.shadow` span。

        M5 P3a 时影子只挂在上云那一支，理由是「快路径毫秒级秒回，为一次 3-8ms 的推理
        牺牲它不值」。当时就记了账：**规则误接发生在本地那一支，影子看不见——而误接
        恰恰是最危险的一类**（用户说车窗、系统开天窗；漏接顶多是上云绕一圈）。
        本方法就是那笔账的补法——**不是把推理搬到关键路径上，是搬到关键路径后面**：
        `create_task` 在 `yield final` 之后才拿到事件循环，秒回一毫秒都不让。

        ⚠ 两个必须记住的实现细节：
        - **任务要留强引用**（复用既有的 `self._bg`）。`asyncio.create_task` 的返回值
          没人持有时任务可能被 GC 在半路收走，表现是「影子有时候有数据有时候没有」
          ——这种缺陷不报错，只让数据悄悄变稀。
        - **进程收尾时未完成的影子会被取消**，这是可接受的：它是诊断不是账本。
          真要一条不丢就得落队列，那是另一个量级的东西，当前没有需求撑它。
        """
        if edge_nlu.mode() == "off":
            return
        try:
            task = asyncio.create_task(
                self._nlu_shadow_emit(trace_id, text, path, rule_objects))
        except RuntimeError:            # 无运行中的 loop（同步测试路径）——影子不生效即可
            return
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _nlu_shadow_emit(self, trace_id: str, text: str, path: str,
                               rule_objects: list[str] | None = None) -> None:
        attrs = await self._nlu_shadow(text, rule_objects)
        if attrs:
            await self._emit_span(trace_id, "nlu.shadow", attrs={"path": path, **attrs})

    async def _nlu_shadow(self, text: str,
                          rule_objects: list[str] | None = None) -> dict:
        """端侧语义 NLU 影子（M5 P3，`EDGE_NLU_MODE=shadow` 默认）：**只算不用**。

        两个刻意的选择：
        - **落 span 属性而不是 metric**。P4 声纹的 `vp_*` 指标被 collector 的 `apply_metric`
          固定键白名单整批丢掉过（RFC 承诺的「四态进 obs」实际没落地），span 属性没有白名单。
        - **推理放线程池**：onnxruntime 是同步阻塞的 C 扩展，直接在事件循环里跑会顶住整个
          端侧 orchestrator（它还要同时喂 WS/gRPC 流）。

        `path` 由调用方给（local/multi/mixed/cloud）——**误接与漏接必须分得开**：
        `path=cloud` 是「规则没接住」，`path=local|multi|mixed` 是「规则接住并且已经执行了」，
        后者的 `differ` 才是真正要人看一眼的那一档。

        `rule_objects`：多意图路径必须传。**模型是单标签分类器，一句多意图它只能选一个**
        ——「打开空调，把车窗关上」模型给「车窗」、规则给 `aircon`，直接比就是一条**结构性
        的假 differ**（与桥接表要消除的「命名差异被记成分歧」是同一类错，只是换了个成因）。
        传进来之后判据变成「模型选中的那个在不在规则执行的这一组里」。
        """
        if edge_nlu.mode() == "off":
            return {}
        try:
            engine = edge_nlu.default()
            if not engine.available:
                return {}
            t0 = time.perf_counter()
            got = await asyncio.to_thread(engine.classify, text)
            if not got:
                return {}
            if rule_objects is None:
                rule = classify_structured(text)
                rule_objects = [(rule.get("data", {}) or {}).get("object", "")] if rule else []
                rule_objects = [o for o in rule_objects if o]
            attrs = {"nlu_domain": got["domain"], "nlu_object": got["object"],
                     "nlu_conf": got["conf"],
                     "nlu_ms": round((time.perf_counter() - t0) * 1000, 1),
                     "nlu_gate": self._theta_band(got["conf"])}
            # 与规则的关系分四态：规则没接住（覆盖率增量的来源）／两边一致／两边分歧／
            # 对不上号（模型给的对象在 VAL 里没有可执行对应物，或还没人裁过）。
            #
            # ⚠ 这里原来是三态，且**比错了东西**：模型输出的是语料标签空间的中文
            # （`空调模式/功能控制`），规则输出的是它自己那套 object（`aircon`、
            # `humidity`、`navigation_route`——95 种，38 种连 VAL 里都没有），
            # 直接 `==` 的结果是**规则一命中就恒为 differ**——`agree` 这个状态在生产里
            # 从来没有出现过，而 P3b 的错对象率正要拿这一档当分母。桥接表
            # （`knowledge/nlu_objects.yaml`）补上后两边才在同一个空间里比。
            if not rule_objects:
                attrs["nlu_vs_rule"] = "rule_miss"
            else:
                attrs["rule_object"] = "|".join(rule_objects)
                equiv = edge_nlu.equivalent_objects(got["object"])
                if not equiv:
                    # None=表里没这个标签（待裁定）／[]=已裁定连规则侧也无对应名。
                    # 两种都不下「模型错了」的结论——**无金标不装懂**。
                    attrs["nlu_vs_rule"] = "unmapped"
                else:
                    hit = any(o in equiv for o in rule_objects)
                    attrs["nlu_vs_rule"] = "agree" if hit else "differ"
            return attrs
        except Exception as e:      # 影子绝不许影响主链——它的全部价值就是「不生效」
            logger.debug("edge NLU shadow skipped: %s", e)
            return {}

    async def _execute_val_observed(
        self,
        trace_id: str,
        command,
        args: dict | None = None,
        answer_length: str = "short",
        intent: str = "",
        multi: bool = False,
        confirmed: bool = False,
    ):
        """本地经 VAL 执行并出 span。

        confirmed 默认 False：本函数的全部调用点（快路径 A/A2/B、云端降级兜底）都是
        **没走过确认闭环**的本地路径，本就不该执行危险动作——VAL 侧 fail-closed 之后
        它们即使被绕进来也执行不了（B1）。真正带凭据的路径是 `edge_call.py`。
        """
        started = time.perf_counter()
        before = dict(self.val.state)
        ok, speech = self.val.execute(
            command,
            args,
            answer_length=answer_length,
            multi=multi,
            confirmed=confirmed,
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

    @staticmethod
    def _executed_names(items) -> list[str]:
        """本轮真实执行的动作名：优先 `payload.command`，回退 `type`。

        ⚠ **与 obs/探针同一口径**（`server.py:908` 的 `payload.get("command", type)`、
        探针的 `_action_names`）。审计回答与可观测台读到的必须是同一个名字，
        否则「刚才执行了什么」答的和 badcase 面板看到的对不上。

        两种入参形态都要吃：单意图分支给的是 dict，多意图分支给的是 `AgentAction`。
        """
        out: list[str] = []
        for a in items or []:
            if isinstance(a, dict):
                payload, atype = a.get("payload") or {}, a.get("type") or ""
            else:
                atype = getattr(a, "type", "") or ""
                raw = getattr(a, "payload", None)
                payload = MessageToDict(
                    raw, preserving_proto_field_name=True) if raw else {}
            name = str((payload or {}).get("command") or atype or "").strip()
            if name:
                out.append(name)
        return out

    def _record_local_turn(self, request, user_text: str, assistant_speech: str,
                           actions=None):
        """把纯本地处理的一轮 best-effort 异步写入共享记忆（gated on memory_enabled）。

        **Q6（2026-08-16）：动作一并写。** 端侧快路径那 313 个动作**根本不上云**，
        云侧无从知道车窗到底开没开——「刚才实际执行了什么」于是只能由 LLM 从
        对话历史里猜。这一处是唯一能把 local 那 40% 补进事实源的位置。
        """
        meta = dict(request.meta) if request.meta else {}
        if meta.get("memory_enabled", "true") == "false":
            return
        if not request.session_id or not user_text:
            return
        ctxp = getattr(request, "context", None)
        uid = getattr(ctxp, "user_id", "") or ""
        vid = getattr(ctxp, "vehicle_id", "") or ""
        occ = (meta.get("occupant_id") or "").strip() or "primary"
        # 一次本地请求 = 一个完整 exchange（user + 本地最终话术）。请求 id 就是 exchange 键。
        exch = getattr(request, "request_id", "") or f"edge-{uuid.uuid4().hex[:16]}"
        executed_names = self._executed_names(actions)
        key = self._local_exchange_key(request)
        if key is not None and len(exch) <= _LOCAL_EXCHANGE_ID_MAX_CHARS:
            now = time.monotonic()
            self._prune_local_exchanges(now)
            self._last_local_exchange.pop(key, None)
            self._last_local_exchange[key] = (
                exch, now, tuple(executed_names))
            while len(self._last_local_exchange) > _LOCAL_EXCHANGE_MAX:
                self._last_local_exchange.popitem(last=False)

        async def _write():
            await self.memory.append(request.session_id, "user", user_text,
                                     user_id=uid, occupant_id=occ, vehicle_id=vid,
                                     turn_id=f"{exch}:user", exchange_id=exch)
            if assistant_speech:
                await self.memory.append(request.session_id, "assistant", assistant_speech,
                                         user_id=uid, occupant_id=occ, vehicle_id=vid,
                                         turn_id=f"{exch}:assistant:0", exchange_id=exch,
                                         actions=executed_names)

        task = asyncio.create_task(_write())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    @staticmethod
    def _local_exchange_key(request) -> tuple[str, str, str] | None:
        ctxp = getattr(request, "context", None)
        meta = request.meta if getattr(request, "meta", None) is not None else {}
        values = (
            str(getattr(request, "session_id", "") or ""),
            str(getattr(ctxp, "user_id", "") or ""),
            str((meta.get("occupant_id") or "").strip() or "primary"),
        )
        if not values[0] or any(len(value) > _LOCAL_ID_MAX_CHARS for value in values):
            return None
        return values

    def _prune_local_exchanges(self, now: float) -> None:
        cutoff = now - _LOCAL_EXCHANGE_TTL_S
        while self._last_local_exchange:
            _key, (_exchange, seen_at, _actions) = next(
                iter(self._last_local_exchange.items()))
            if seen_at >= cutoff:
                break
            self._last_local_exchange.popitem(last=False)

    def _attach_previous_local_exchange(self, request) -> None:
        """Forward one unconsumed local-turn boundary; client meta cannot forge it."""
        key = self._local_exchange_key(request)
        if key is None:
            return
        self._prune_local_exchanges(time.monotonic())
        entry = self._last_local_exchange.pop(key, None)
        if entry:
            request.meta["_edge_previous_local_exchange"] = entry[0]
            if entry[2]:
                request.meta["_edge_previous_local_actions"] = ",".join(entry[2])

    async def Handle(self, request, context):
        """观测收口 wrapper：一次 Handle = 一条 obs.turn（badcase 排查的核心数据）。

        编排逻辑全部在 _handle_impl；此处只旁路累积流经的事件（speech/final/卡片），
        在流结束/取消/异常时 best-effort 发轮次事件——不改变事件语义与时序。
        端侧是所有请求的漏斗（本地快路径/混合/上云/确认续接都流经这里），单点收口后
        云端内部路径怎么变，turn 完整性都不受影响。
        """
        trace_id = _ensure_trace_id(request)
        set_session_id(request.session_id)
        started = time.perf_counter()
        ts_ms = int(time.time() * 1000)
        turn = {"path": ""}
        speeches: list[str] = []
        deltas: list[str] = []
        card_type = ""
        actions_n = 0
        need_confirm = False
        status = ""
        error = ""
        try:
            async for event in self._handle_impl(request, context, turn):
                which = event.WhichOneof("event")
                if which == "final":
                    f = event.final
                    if f.speech:
                        speeches.append(f.speech)
                    actions_n += len(f.actions)
                    need_confirm = need_confirm or f.need_confirm
                    card_type = _ui_card_type(f) or card_type
                elif which == "speech_delta":
                    deltas.append(event.speech_delta)
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            status = "cancelled"
            raise
        except Exception as e:
            status, error = "err", str(e)
            raise
        finally:
            if not status:
                if card_type == "rejected":
                    status = "rejected"
                elif card_type == "intent_choice":
                    status = "clarify"
                elif need_confirm:
                    status = "need_confirm"
                elif not speeches and not deltas and not actions_n:
                    status = "empty"
                else:
                    status = "ok"
            meta = dict(request.meta) if request.meta else {}
            try:
                # emit_turn 入队即返回（无真实 await 悬挂点），取消/关闭路径下同样安全。
                await self.obs.emit_turn(
                    trace_id,
                    request.session_id,
                    user_text=request.text,
                    speech=_join_speeches(speeches) or "".join(deltas),
                    status=status,
                    path=turn.get("path", ""),
                    input_source=meta.get("input_source", ""),
                    is_confirmation=request.is_confirmation,
                    ui_card_type=card_type,
                    actions=actions_n,
                    intents=turn.get("intents") or [],
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error=error,
                    ts=ts_ms,
                )
            except Exception:
                pass

    async def _handle_impl(self, request, context, turn: dict):
        trace_id = _ensure_trace_id(request)
        self._change_source.set("T0")
        # `_edge_executed` 是端侧执行器签发给云侧的内部事实，不是客户端输入。
        # 网关会透传 HMI meta，因此每轮入口必须先剥掉同名键；混合路径只有在 VAL
        # 实际成功后才会重新写入。否则网页/手机可伪造「刚执行过什么」污染指代焦点。
        try:
            request.meta.pop("_edge_executed", None)
            request.meta.pop("_edge_previous_local_exchange", None)
            request.meta.pop("_edge_previous_local_actions", None)
        except Exception:
            pass
        # 把端侧真实车辆电量注入 meta，透传给云端 Agent（充电规划等），避免云端读 memory
        # 默认值(50%)与可观测台/仪表实际电量(如72%)不一致。
        try:
            request.meta["vehicle_battery"] = str(self.val.state.get("battery", ""))
        except Exception:
            pass
        # 从 request.meta 读取 HMI 设置
        meta = dict(request.meta) if request.meta else {}
        answer_length = meta.get("answer_length", "short")

        # 确认/补槽续接必须回到挂起会话所在的云端，不走本地快路径
        if request.is_confirmation:
            intent = None
            multi = None
            mixed_intents = None
        else:
            # 体感冷热→空调温度/风速方向推断（"感觉冷，温度和风速都调一下"→温度↑风速↓）优先，
            # 命中则当多意图并行执行；否则常规多意图拆分。
            multi = climate_feeling_intents(request.text) or split_and_classify(request.text)
            mixed_intents = None
            if multi:
                intent = None
            else:
                # 全有全无失败 → 尝试混合拆分（本地+非本地）
                mixed_intents = split_and_classify_any(request.text)
                intent = None if mixed_intents else classify(request.text)

        # 单句负极性写操作已经没有需要模型补全的信息：「别开」的正确语义是保持
        # 不变。此前 classify 正确地不产本地意图，服务层却把它再送云端，模型仍可能
        # 猜成反向动作。复合句由下面 `_negated_directive` 分段标记处理。
        negated_only = bool(
            not request.is_confirmation
            and not mixed_intents
            and is_negated_write_directive(request.text)
        )

        # 快路径 A：多意图全部本地，并行执行聚合语音
        if multi:
            speeches = []
            actions = []
            for m_intent in multi:
                legacy = structured_to_legacy(m_intent)
                if (legacy and legacy["confidence"] >= _HIGH and is_local(legacy["name"])
                        and not self._confirm_required(m_intent)):
                    # 结构化命令直通 VAL（multi=名词式话术，合并播报可归属：「空调已开启，车窗已打开」）
                    ok, speech = await self._execute_val_observed(
                        trace_id,
                        m_intent,
                        answer_length=answer_length,
                        intent=legacy["name"],
                        multi=len(multi) > 1,
                    )
                    if not ok:
                        speech = speech or "操作失败"
                    speeches.append(speech)
                    # 仅在 VAL 真正执行成功(ok)时回传 action。被安全门控拒绝时只播报原因、
                    # 不下发动作——否则 HMI 会把被拒动作显示成"已执行"，与"已禁用"自相矛盾。
                    if ok:
                        obj = m_intent.get("data", {}).get("object", "")
                        action_type = action_type_for(obj)
                        actions.append(common_pb2.AgentAction(
                            type=action_type,
                            payload=_struct({"command": legacy["name"], **legacy.get("slots", {})}),
                            require_confirm=False,
                        ))
                    logger.info("MULTI-LOCAL %s -> %s (ok=%s)", legacy["name"], speech, ok)
                else:
                    # 子意图无法本地处理 / 需二次确认 → 整句走云（保守策略）
                    logger.info("MULTI sub-intent needs cloud, falling through")
                    speeches = []
                    break
            if speeches:
                turn["path"] = "local"
                turn["intents"] = self._executed_names(actions)
                await self._emit_span(
                    trace_id,
                    "route.multi",
                    attrs={"count": len(actions)},
                )
                combined = _join_speeches(speeches)
                final = orchestrator_pb2.FinalResult(speech=combined)
                final.actions.extend(actions)
                yield orchestrator_pb2.HandleEvent(final=final)
                self._nlu_shadow_bg(
                    trace_id, request.text, "multi",
                    rule_objects=[o for o in
                                  ((m.get("data") or {}).get("object", "") for m in multi) if o])
                self._record_local_turn(request, request.text, combined,
                                        actions=actions)
                return

        # 快路径 A2：混合意图（部分本地 + 部分非本地）。
        # 本地意图立即经 VAL 执行，非本地意图上云编排。
        if mixed_intents:
            local_speeches = []
            local_actions = []
            cloud_parts = []  # 非本地意图的原始文本片段
            for group in _group_mixed_intents(mixed_intents):
                effective_group = [
                    item for item in group if not item.get("_negated_directive")
                ]
                if not effective_group:
                    local_speeches.append("好的，保持当前状态")
                    continue
                local_group = []
                for m_intent in effective_group:
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
                            multi=len(mixed_intents) > 1,
                        )
                        if not ok:
                            speech = speech or "操作失败"
                        local_speeches.append(speech)
                        # 同快路径 A：门控拒绝(ok=False)只播报、不下发 action。
                        if ok:
                            obj = m_intent.get("data", {}).get("object", "")
                            action_type = action_type_for(obj)
                            local_actions.append(common_pb2.AgentAction(
                                type=action_type,
                                payload=_struct({"command": legacy["name"], **legacy.get("slots", {})}),
                                require_confirm=False,
                            ))
                        logger.info("MIXED-LOCAL %s -> %s (ok=%s)", legacy["name"], speech, ok)
                else:
                    # 组内任一片段需上云时，整组上云，保留目的地/路线偏好、
                    # 媒体动作/歌手等相邻片段之间的语义上下文。
                    for m_intent in effective_group:
                        raw = m_intent.get("_raw_text", "")
                        if raw:
                            cloud_parts.append(raw)
                    logger.info(
                        "MIXED-CLOUD group=%s",
                        [item.get("data", {}).get("object", "") for item in group],
                    )

            if cloud_parts:
                turn["path"] = "mixed"
                turn["intents"] = self._executed_names(local_actions)
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
                    combined = _join_speeches(local_speeches)
                    final = orchestrator_pb2.FinalResult(speech=combined)
                    final.actions.extend(local_actions)
                    yield orchestrator_pb2.HandleEvent(final=final)
                    self._nlu_shadow_bg(
                        trace_id, request.text, "mixed",
                        rule_objects=[o for o in
                                      ((m.get("data") or {}).get("object", "")
                                       for m in mixed_intents) if o])
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
                        e2e_memory_capability=request.e2e_memory_capability,
                    )
                    # 已在端侧给过云段占位时，让云端别再重复"正在为您处理"（避免双占位文案）
                    if local_speeches:
                        cloud_req.meta["_mixed_subrequest"] = "1"
                    # QA 卡 Q7-OR2：把**本轮端侧已经执行掉的动作**告诉云侧。
                    # 上云的片段可能是个没有对象的碎片——「关闭空调然后打开，按顺序执行」
                    # 上云的是「打开，按顺序执行」，对象在同一轮的**另一个组**里。
                    # 真栈实测：云侧就此落兜底，答「我不能帮你执行操作」，
                    # 而它 4 秒前刚关了空调。
                    # 名字口径与 Q6 的执行事实账本、obs、探针**共用 `_executed_names`**
                    # ——审计答的、面板看的、云侧消解用的必须是同一个名字。
                    executed = self._executed_names(local_actions)
                    if executed:
                        cloud_req.meta["_edge_executed"] = ",".join(executed)
                    self._attach_previous_local_exchange(cloud_req)
                    async for event in self.cloud.handle(cloud_req):
                        got = True
                        self.cloud_connected = True
                        event = self._dispatch_cloud_actions(event, answer_length)
                        event = self._stamp_progress(event)  # 过程区事件标注行车态
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
                turn["path"] = "local"
                turn["intents"] = self._executed_names(local_actions)
                if local_speeches:
                    combined = _join_speeches(local_speeches)
                    final = orchestrator_pb2.FinalResult(speech=combined)
                    final.actions.extend(local_actions)
                    yield orchestrator_pb2.HandleEvent(final=final)
                    self._nlu_shadow_bg(
                        trace_id, request.text, "local",
                        rule_objects=[o for o in
                                      ((m.get("data") or {}).get("object", "")
                                       for m in mixed_intents) if o])
                    self._record_local_turn(
                        request, request.text, combined, actions=local_actions)
                return

        if negated_only:
            turn["path"] = "local"
            turn["intents"] = ["noop.negated"]
            speech = "好的，保持当前状态。"
            await self._emit_span(
                trace_id, "route.local",
                attrs={"intent": "noop.negated", "confidence": 1.0},
            )
            yield orchestrator_pb2.HandleEvent(
                final=orchestrator_pb2.FinalResult(speech=speech))
            self._nlu_shadow_bg(trace_id, request.text, "local")
            self._record_local_turn(request, request.text, speech, actions=[])
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
                    action_type = action_type_for(structured.get("data", {}).get("object", ""))
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
                turn["path"] = "local"
                turn["intents"] = [intent["name"]]
                await self._emit_span(
                    trace_id,
                    "route.local",
                    attrs={
                        "intent": intent["name"],
                        "confidence": intent["confidence"],
                    },
                )
                yield orchestrator_pb2.HandleEvent(final=final)
                # 秒回之后才排影子——这一支是**规则已经执行了**的那一支，`differ` 在这里
                # 意味着「模型认为你说的是另一个对象，而车已经动了」，是最该被人看的一档。
                self._nlu_shadow_bg(trace_id, request.text, "local")
                self._record_local_turn(request, request.text, speech,
                                        actions=[action] if action else [])
                return
            logger.info("LOCAL confirm-required %s -> route to cloud", intent["name"])

        # 慢路径：上云编排
        turn["path"] = "cloud"
        # 端云信息断链修复（M5 P2-D2）：端侧已经算出的分类结果此前**原样不随行**——
        # 既浪费一次免费信号，也让「端云分歧」这个信息量最大的标注线索无从谈起
        # （Shadow NLU 实测规则臂 75.9% vs LLM 91.2%，分歧处正是两边最该被人看一眼的地方）。
        # 只带**判断**不带执行：云侧默认只用于观测与分歧挖掘，不注入 prompt（见 planning.py）。
        if intent and intent.get("name"):
            request.meta["_edge_nlu"] = (
                f"{intent['name']}|{float(intent.get('confidence') or 0):.2f}")
        logger.info("CLOUD route: %s", request.text)
        await self._emit_span(
            trace_id,
            "route.cloud",
            attrs={"text": request.text[:40]},
        )
        # 影子从 `route.cloud` 的属性里搬了出来，落独立的 `nlu.shadow` span：
        # 它现在四条路径都挂（local/multi/mixed/cloud），寄生在某一条路径的 span 上
        # 就意味着**数据分散在四个 node 里**，查一次分歧要 union 四张表。
        # 顺带把这 3-8ms 从上云路径的关键段也摘了出去。
        self._nlu_shadow_bg(trace_id, request.text, "cloud")
        # 云端**整条流**是否已经给过用户任何实质输出（B1）：只看 final.speech 会漏掉
        # 「流式 speech_delta 已经播出去、final.speech 恰为空」这一档——那时兜底会认为
        # 「云端没输出」而本地补执行，造成双执行。progress 不算：过程区只是 UI 进度，
        # 用户没拿到答案，那正是兜底该覆盖的场景。
        cloud_had_output = False
        try:
            got = False
            self._attach_previous_local_exchange(request)
            async for event in self.cloud.handle(request):
                got = True
                self.cloud_connected = True
                # 云端回流 action 分发：车控类走 VAL
                event = self._dispatch_cloud_actions(event, answer_length)
                event = self._stamp_progress(event)  # 过程区事件标注行车态
                which = event.WhichOneof("event")
                if which == "final":
                    if event.final.speech or len(event.final.actions) > 0:
                        cloud_had_output = True
                elif which == "speech_delta":
                    cloud_had_output = cloud_had_output or bool(event.speech_delta)
                elif which == "action":
                    cloud_had_output = True
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

        # 兜底：云端整条流零输出 → 尝试端侧 VAL 本地执行
        # 场景：LLM 规划失败 → chitchat 兜底但无实质回复 → 但原意可能是车控
        #
        # ⚠ 这条分支曾是一条完整的执行旁路（B1 修复的 P0）：它重新分类原话就直接下发 VAL，
        # **不过 _confirm_required**。于是「打开后备箱」只要云端出任何空结果故障
        # （LLM 超时 / 解析失败 / chitchat 空回复），后备箱就会无确认打开——不需要恶意输入。
        # 三道挡板：① 云端已给过任何输出就不兜底（下面的 cloud_had_output）；
        # ② 危险对象不兜底，播降级话术；③ 非危险车控兜底行为不变（这条分支存在的意义）。
        if not cloud_had_output:
            local_structured = classify_structured(request.text)
            if local_structured and self._confirm_required(local_structured):
                # 挡板 ②：不执行、也不静默。不发 NEED_CONFIRM——端侧确认闭环依赖云端
                # 挂起/恢复（见 `_confirm_required` 注释与 edge_call 的 NEED_CONFIRM），
                # 而本分支触发的前提恰恰是云端没有结果，本地发确认没有恢复通道承接，
                # 会造成「用户确认了却没人执行」的悬空确认。
                obj = local_structured.get("data", {}).get("object", "")
                logger.warning(
                    "CLOUD-DEGRADED-DANGER-BLOCKED %s (text=%r)", obj, request.text[:40])
                yield orchestrator_pb2.HandleEvent(
                    final=orchestrator_pb2.FinalResult(
                        speech="网络不太好，这个操作需要确认后执行，请稍后再试。"))
                return
            if local_structured:
                ok, speech = await self._execute_val_observed(
                    trace_id,
                    local_structured,
                    answer_length=answer_length,
                    intent=local_structured.get("intent", ""),
                )
                if ok and speech:
                    obj = local_structured.get("data", {}).get("object", "")
                    action_type = action_type_for(obj)
                    action = common_pb2.AgentAction(
                        type=action_type,
                        payload=_struct({"command": f"{obj}.{local_structured['data'].get('operate', '')}"}),
                        require_confirm=False,
                    )
                    final = orchestrator_pb2.FinalResult(speech=speech)
                    final.actions.append(action)
                    logger.info("CLOUD-DEGRADED-LOCAL %s -> %s", obj, speech)
                    yield orchestrator_pb2.HandleEvent(final=final)

    def _is_driving(self) -> bool:
        """按端侧 VAL 真实车速/档位判定是否行驶中——供过程区行车/泊车双态门控。
        状态缺失时默认非行驶（泊车，可展开），不强制限制（避免演示/单测下永不可展开）。"""
        st = self.val.state
        try:
            speed = float(st.get("speed_kmh", 0) or 0)
        except (TypeError, ValueError):
            speed = 0.0
        gear = str(st.get("gear", "") or "").upper()
        return speed > 0 or gear in ("D", "R", "S")

    def _stamp_progress(self, event):
        """给过程区事件标注行车态（Edge 是车辆状态真相源）。非 progress 事件原样返回。"""
        if event.WhichOneof("event") == "progress":
            event.progress.driving = self._is_driving()
        return event

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
        dispatched = 0          # 真正交给 VAL 的动作数（跳过的不算）
        last_msg = ""           # 最后一条动作的 VAL 应答
        rejected: list[str] = []
        for action in final.actions:
            # media.control 与 vehicle.control 同经 VAL 结构化流水线（P1.4）：VAL 早已建模
            # media/music/radio 等对象、`edge_call.action_type_for` 也早已把媒体对象映射到
            # media.control——只是回流分发漏了这一类，导致场景里的「放舒缓音乐」永远落不了地。
            if not action.type.startswith(("vehicle.control", "media.control")):
                continue
            payload = MessageToDict(
                action.payload, preserving_proto_field_name=True
            ) if action.payload else {}
            # 已在车端 VAL 执行过（中枢 edge_call 回流），仅展示不二次下发，避免双发。
            if payload.get("_origin") == "edge_val":
                continue
            cmd = payload.get("command", action.type)
            # 先翻译成 VAL 结构化命令（场景/计划层的 ambient_light/seat/volume/fragrance
            # 等命令只在结构化路径受支持，且结构化路径才过安全门控）；翻译失败再回退 legacy 串。
            objects = (self.val.commands or {}).get("objects") or {}
            structured = action_to_structured(
                cmd, payload,
                known_objects=set(objects) if objects else None,
                object_defs=objects or None,
            )
            # 不传 confirmed（B1）：能走到这里的危险 action 一定**没经过确认闭环**——
            # 合法确认后的执行走 edge_call，回流时带 `_origin=edge_val` 已在上面被跳过；
            # 场景编排的危险动作在编译/激活层就按 require_confirm 处理过
            # （scene_orchestrator `catalog._DANGER_OBJECTS`）。于是这里的危险 action
            # 只可能来自异常路径，VAL fail-closed 会把它从「静默执行」变成「拒绝并播报」。
            if structured is not None:
                ok, msg = self.val.execute(structured, answer_length=answer_length)
            else:
                ok, msg = self.val.execute(cmd, payload, answer_length=answer_length)
            dispatched += 1
            last_msg = msg
            if ok:
                logger.info("VAL executed: %s -> %s", cmd, msg)
            else:
                logger.warning("VAL rejected: %s -> %s", cmd, msg)
                rejected.append(msg)

        # 话术归属（2026-07-14 修正「new_speech 逐条覆盖」的两个真缺陷）：
        # ① **失败不再被后续成功盖掉**——旧实现循环内逐条覆盖 new_speech，5 个动作里第 2 条被
        #    安全门控拒绝、第 3~5 条成功，最终只播最后一条的成功话术，拒绝对用户完全静默。
        # ② **多动作保留云端总结**——场景激活的「已为您开启露营模式」不该被最后一条动作
        #    （scene_mode.set）的 VAL 通用应答顶成「好的」。逐条 VAL 应答是碎片，云端话术才是
        #    整体交代。单条动作仍用 VAL 应答（含真实温度/档位，比云端话术精确），逐字保持现状。
        if dispatched == 1:
            final.speech = last_msg
        elif dispatched > 1 and rejected:
            reasons = "；".join(dict.fromkeys(rejected))
            final.speech = f"{final.speech}不过{reasons}。" if final.speech else reasons
        # dispatched > 1 且全成功 → 保留云端总结话术；dispatched == 0 → 原样不动
        return event
