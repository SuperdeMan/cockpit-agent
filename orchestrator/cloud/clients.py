"""Cloud Planner 的下游客户端：Registry / LLM Gateway / Agent / Memory。

Phase 1 改进：连接复用、统一超时。
"""
from __future__ import annotations
import logging
import os
import time
from contextvars import ContextVar

import grpc

from runtime.grpcio import aio_channel
from runtime import admission, memory_read, context_access
from .context_view import check_current, check_context
from runtime import capability_contract as cap_contract
from runtime import operation
from runtime import effect_evidence

logger = logging.getLogger("planner.clients")

# 运行时硬化 D2：请求级 LLM pin（meta.llm_provider/llm_model）。engine 在请求入口按
# ctx.prefs 设置；llm_complete（planner/aggregator 共用）据此透传给网关。Agent 路径
# 不走此变量——pin 随 _merge_meta 进 ExecuteRequest.meta、SDK 自动透传。
_LLM_PIN: ContextVar[tuple[str, str]] = ContextVar("cloud_llm_pin", default=("", ""))


def set_llm_pin(provider: str = "", model: str = "") -> None:
    _LLM_PIN.set(((provider or "").strip(), (model or "").strip()))
from cockpit.registry.v1 import registry_pb2, registry_pb2_grpc
from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
from cockpit.agent.v1 import agent_pb2, agent_pb2_grpc
from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc
from cockpit.channel.v1 import channel_pb2, channel_pb2_grpc
from cockpit.common.v1 import common_pb2

_DEFAULT_TIMEOUT = 10
# 流式 Agent 调用（ExecuteStream）的 gRPC 总截止（秒）。此前缺省 30s：流式 Agent 边生成边流，长回答
# （chitchat 上限 900 token、MiniMax-M3 尾部 ~20 token/s ⇒ 45s）会在句中被掐断，然后 D0 走「只流了话术」
# 那档——用户屏上已流出的整段被「抱歉，刚才没说完」替掉。客户端看门狗 95s、规划 p90 4s，60s 留得下。
# 截止的职责是兜住挂死的 Agent（上游卡住由 llm-gateway 的逐片 stall 检测负责），不是限制回答长度。
# T2 按 step.latency_budget_ms 显式传，不走这个缺省。
AGENT_STREAM_TIMEOUT_S = float(os.getenv("CLOUD_AGENT_STREAM_TIMEOUT_S", "60"))

#: llm-gateway 无任何 chat 厂商 key 时的回显兜底（`llm-gateway/providers.py::MockProvider`）
#: 在 `CompleteResponse.model_used` 里自报的名字。planner 据此知道「这个栈里没有规划模型」
#: ——那时规划必然走 `_fallback`，兜底 Agent 是设计路径而不是技术失败（AR05 F09 的例外，
#: 见 docs/design/2026-09-09-ar05-structured-contracts-implementation-plan.md §5.2）。
#: 跨进程消费一个字面量：不嗅探 `[mock]` 话术前缀，认网关自报的 provider 身份。
_MOCK_MODEL_USED = "mock"

#: 数据源章（C4-A）的写侧构造。**逐键取而不是 `TurnSource(**s)`**：上游给的是
#: 从 `ui_card._prov` 收来的自由 dict，多一个键就在落库路径上抛 ValueError。
_TURN_SOURCE_FIELDS = ("card", "vendor", "mode", "fetched_at", "note",
                       "data_time", "data_time_label")


def _turn_source_pb(raw: dict):
    return memory_pb2.TurnSource(**{
        k: raw[k] for k in _TURN_SOURCE_FIELDS if isinstance(raw.get(k), str)})


class Clients:
    def __init__(self):
        self.registry_addr = os.getenv("REGISTRY_ADDR", "registry:50051")
        self.llm_addr = os.getenv("LLM_GATEWAY_ADDR", "llm-gateway:50052")
        self.memory_addr = os.getenv("MEMORY_ADDR", "memory:50053")
        self.cloud_gateway_addr = os.getenv("CLOUD_GATEWAY_ADDR", "cloud-gateway:8080")
        self._ch_registry: grpc.aio.Channel | None = None
        self._ch_llm: grpc.aio.Channel | None = None
        self._ch_memory: grpc.aio.Channel | None = None
        self._ch_edge: grpc.aio.Channel | None = None
        self._ch_agents: dict[str, grpc.aio.Channel] = {}  # F15：按 endpoint 复用 channel
        # 最近一次 Complete 网关自报的 model_used（见 llm_served_by_mock）。
        self._llm_model_used: str = ""

    def _registry_stub(self):
        if self._ch_registry is None:
            self._ch_registry = aio_channel(self.registry_addr)
        return registry_pb2_grpc.RegistryStub(self._ch_registry)

    def _llm_stub(self):
        if self._ch_llm is None:
            self._ch_llm = aio_channel(self.llm_addr)
        return llm_pb2_grpc.LLMGatewayStub(self._ch_llm)

    def _memory_stub(self):
        if self._ch_memory is None:
            self._ch_memory = aio_channel(self.memory_addr)
        return memory_pb2_grpc.MemoryStub(self._ch_memory)

    def _edge_stub(self):
        if self._ch_edge is None:
            self._ch_edge = aio_channel(self.cloud_gateway_addr)
        return channel_pb2_grpc.EdgeCloudChannelStub(self._ch_edge)

    async def append_turn(self, session_id: str, role: str, text: str,
                          user_id: str = "", vehicle_id: str = "",
                          occupant_id: str = "primary",
                          e2e_memory_capability: str = "",
                          turn_id: str = "", exchange_id: str = "",
                          actions=None, sources=None, memory_epoch: str = "",
                          speaker_unverified: bool = False):
        """写入一轮对话到 memory（指代消解的数据来源）。带 user_id 时 memory 侧据此触发异步抽取。
        occupant_id 决定抽取出的偏好归属哪个乘员（M4 P4；proto 字段 2026-06 就有，一直没人传）。
        turn_id/exchange_id 让重试是重放而不是追加一轮新对话（M-B）。
        `sources` 是 C4-A 的数据源事实（这一轮用了谁的数据、降没降级）。"""
        await self._memory_stub().AppendTurn(
            memory_pb2.AppendTurnRequest(session_id=session_id, role=role, text=text,
                                         user_id=user_id, vehicle_id=vehicle_id,
                                         occupant_id=occupant_id or "primary",
                                         e2e_memory_capability=e2e_memory_capability,
                                         turn_id=turn_id, exchange_id=exchange_id,
                                         actions=list(actions or []),
                                         sources=[_turn_source_pb(s)
                                                  for s in (sources or [])
                                                  if isinstance(s, dict)],
                                         memory_epoch=memory_epoch,
                                         speaker_unverified=speaker_unverified),
            timeout=_DEFAULT_TIMEOUT)

    async def get_memory_epoch(self, user_id: str) -> str:
        """CA2-15 S1b：本回合读记忆之前取一次 owner 代际。取不到回空串（写入按旧调用方处理）。"""
        if not user_id:
            return ""
        resp = await self._memory_stub().GetMemoryEpoch(
            memory_pb2.GetMemoryEpochRequest(user_id=user_id), timeout=_DEFAULT_TIMEOUT)
        return resp.memory_epoch

    async def get_session(self, session_id: str, last_n: int = 6, *,
                          user_id: str = "", occupant_id: str = "") -> list[dict]:
        """取最近 N 轮对话（供 planner 注入上下文）。

        M-B：默认 OWNER_ONLY——车里只有一个会话而说话人会换，不按 owner 过滤时
        planner 会拿到别人的对话当指代来源。scope 不传即 OWNER_ONLY，跨乘员读取
        必须显式声明，且不走这条规划路径。

        ⚠ **`actions` 是 Q7-EL1 补上的（2026-08-16），它此前只差这一行。**
        Q6 把执行事实写进了 `AppendTurn.actions`（端侧本地快路径与云侧规划轮各写各的），
        proto 的读侧字段 `Turn.actions` 也一并加了，注释逐字写着「读侧必须也带上——
        **存下来而读不到等于没存**」。`agents/_sdk/clients.py::get_session` 照做了，
        **而云侧这份没有**——同一个 proto 的两份客户端实现，一份读了一份没读。
        后果：端侧本地那 40% 的车控动作云侧规划路径完全看不见，
        「打开天窗」→「不用了，关掉」只能让 LLM 从对话文本猜对象（真栈三次三个样：
        无动作 / 反向执行 / 正确）。
        > 判据：**读写对称要逐个消费方验，不是「写侧加了字段」就算通了。**
        """
        turns, _state = await self.get_session_read(
            session_id, last_n, user_id=user_id, occupant_id=occupant_id)
        return turns

    async def get_session_read(self, session_id: str, last_n: int = 6, *,
                               user_id: str = "", occupant_id: str = "") -> tuple[list[dict], str]:
        """`get_session` 的三态版（批 5 W17）→ `(turns, state)`，state ∈ `runtime.memory_read`。

        RPC 失败**在这里就是 unavailable**，不再让上层把异常吞成空列表；服务端自报 `degraded`
        （配了 Redis 却在用内存兜底）时空列表同样是 unavailable。"""
        try:
            resp = await self._memory_stub().GetSession(
                memory_pb2.GetSessionRequest(
                    session_id=session_id, last_n=last_n, user_id=user_id,
                    occupant_id=occupant_id or "primary",
                    scope=memory_pb2.HISTORY_SCOPE_OWNER_ONLY),
                timeout=_DEFAULT_TIMEOUT)
        except Exception as e:
            logger.debug("get_session unavailable: %s", e)
            return [], memory_read.UNAVAILABLE
        turns = [{"role": t.role, "text": t.text, "ts": t.ts,
                  "occupant_id": t.occupant_id,
                  "actions": list(t.actions), "exchange_id": t.exchange_id,
                  # C4-A：来源账本的读侧。**这一行就是上面那条教训的复刻位**——
                  # 写侧加了字段而这里不读，云侧读出口手里就还是空的。
                  "sources": [{k: getattr(s, k) for k in _TURN_SOURCE_FIELDS}
                              for s in t.sources]}
                 for t in resp.turns]
        return turns, memory_read.read_state(turns, degraded=bool(resp.degraded))

    async def recall(self, user_id: str, query: str = "", *, occupant_id: str = "",
                     scopes: list[str] | None = None, kinds: list[str] | None = None,
                     top_k: int = 3, min_confidence: float = 0.0,
                     projection: str = "") -> list[dict]:
        """语义召回用户偏好（供 planner 注入）。返回 dict 列表（含 score）。
        RPC 失败照旧抛（既有调用方自己吞）；要三态用 `recall_read`。"""
        resp = await self._memory_stub().Recall(
            memory_pb2.RecallRequest(
                user_id=user_id, occupant_id=occupant_id, query=query,
                scopes=scopes or [], kinds=kinds or [], top_k=top_k,
                min_confidence=min_confidence, projection=projection),
            timeout=_DEFAULT_TIMEOUT)
        return self._recall_items(resp)

    @staticmethod
    def _recall_items(resp) -> list[dict]:
        return [{"text": it.text, "scope": it.scope, "predicate": it.predicate,
                 "provenance": it.provenance, "confidence": it.confidence,
                 # M2 P0：偏好强度（0=未参与加权的存量条目，渲染时回退 confidence）
                 "weight": it.weight, "evidence_count": it.evidence_count}
                for it in resp.items]

    async def recall_read(self, user_id: str, query: str = "", *, occupant_id: str = "",
                          scopes: list[str] | None = None, kinds: list[str] | None = None,
                          top_k: int = 3, min_confidence: float = 0.0,
                          projection: str = "") -> tuple[list[dict], str]:
        """`recall` 的三态版（批 5 W17）→ `(items, state)`。语义同 `get_session_read`。"""
        try:
            resp = await self._memory_stub().Recall(
                memory_pb2.RecallRequest(
                    user_id=user_id, occupant_id=occupant_id, query=query,
                    scopes=scopes or [], kinds=kinds or [], top_k=top_k,
                    min_confidence=min_confidence, projection=projection),
                timeout=_DEFAULT_TIMEOUT)
        except Exception as e:
            logger.debug("recall unavailable: %s", e)
            return [], memory_read.UNAVAILABLE
        items = self._recall_items(resp)
        return items, memory_read.read_state(items, degraded=bool(resp.degraded))

    async def list_agents(self):
        resp = await self._registry_stub().ListAgents(
            registry_pb2.ListRequest(category="", capability_contract_version=2), timeout=_DEFAULT_TIMEOUT)
        return list(resp.agents)

    async def register_manifest(self, manifest, endpoint: str):
        return await self._registry_stub().Register(
            registry_pb2.RegisterRequest(
                manifest=manifest,
                endpoint=endpoint,
            ),
            timeout=_DEFAULT_TIMEOUT,
            metadata=admission.client_metadata(),   # B3 §2.4；未配 token 时为空
        )

    async def resolve(self, query: str = "", intent: str = "", top_k: int = 1):
        resp = await self._registry_stub().ResolveAgents(
            registry_pb2.ResolveRequest(query=query, intent=intent, top_k=top_k, capability_contract_version=2),
            timeout=_DEFAULT_TIMEOUT)
        return list(resp.agents)

    @staticmethod
    def _stamp_llm_meta(req, thinking: bool = False) -> None:
        """llm_complete / llm_complete_tools 共用的 meta 盖章：思考开关 + 请求级 pin +
        trace 贯通 + 观测归属。"""
        if thinking:
            req.meta["thinking"] = "on"
        # 运行时硬化 D2：请求级 LLM pin（engine 在请求入口 set_llm_pin）——planner/aggregator
        # 的 LLM 调用与 Agent 路径同脑，评测/重放 A/B 才有意义。
        pin_provider, pin_model = _LLM_PIN.get()
        if pin_provider:
            req.meta["llm_provider"] = pin_provider
            if pin_model:
                req.meta["llm_model"] = pin_model
        # 观测贯通：LLM 网关据此发 obs.llm 事件（模型/tokens/时延按 trace 归档）。
        # caller_service 仅供观测归属——刻意不用 "caller"（那是限流桶键，不能扰动）。
        from observability.tracing import get_session_id, get_trace_id
        if get_trace_id():
            req.meta["trace_id"] = get_trace_id()
        if get_session_id():
            req.meta["session_id"] = get_session_id()
        req.meta["caller_service"] = "cloud-planner"

    async def llm_complete(self, messages: list[dict], max_tokens: int = 800,
                           thinking: bool = False, *, model: str = "",
                           temperature: float = 0.3) -> str:
        """thinking=True 时本次开思考（meta 透传给网关）并抬 token/超时。
        **Planner 调用恒 False**（结构化 JSON 不能被 reasoning 吃空）；Aggregator 由
        engine 对复杂任务传 True。model：网关档位（`""` = primary、`"@fast"` = 快档，
        评审四轮 R4-07 的轻量受话判定用快档）；缺省值与此前逐字相同。"""
        req = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role=m["role"], content=m["content"]) for m in messages],
            model=model, temperature=temperature,
            max_tokens=max(max_tokens, 2048) if thinking else max_tokens)
        self._stamp_llm_meta(req, thinking=thinking)
        check_current()
        resp = await self._llm_stub().Complete(req, timeout=60 if thinking else 30)
        check_current()
        self._llm_model_used = str(resp.model_used or "")
        return resp.content

    def llm_served_by_mock(self) -> bool:
        """LLM 网关是不是在用 MockProvider 服务（栈里没配任何 chat key）。

        mock 只在**零厂商 key** 时注册、且不可热切（llm_runtime._build：注册表为空才落
        mock），所以第一次响应之后这个读数就是栈级常量，不存在并发请求互相串味的窗口。
        没打过网关时返回 False——拿不准就当真模型（fail-closed：宁可多报一次技术失败，
        也不把真模型的失败伪装成兜底）。"""
        return self._llm_model_used == _MOCK_MODEL_USED

    @classmethod
    def _destruct_nums(cls, v):
        """protobuf Struct 数字恒 double：整数值 float 还原 int（递归）。对齐 JSON 路径
        json.loads 的 int 行为——否则 slots str() 化后 "24"→"24.0"，A/B 出现假漂移。"""
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, list):
            return [cls._destruct_nums(x) for x in v]
        if isinstance(v, dict):
            return {k: cls._destruct_nums(x) for k, x in v.items()}
        return v

    async def llm_complete_tools(self, messages: list[dict], tools: dict,
                                 max_tokens: int = 800) -> tuple[str, list[dict]]:
        """带工具定义的补全（M1a submit_plan 结构化输出，RFC §4）。

        tools：线格式 ``{"tools": [...], "tool_choice": ...}``，经 CompleteRequest.tools
        Struct 透传；返回 (content, tool_calls)，tool_calls 为网关归一化形状
        ``[{"id","name","arguments"(dict)}]``。规划轮恒关思考（同 llm_complete 口径）。"""
        req = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role=m["role"], content=m["content"]) for m in messages],
            temperature=0.3, max_tokens=max_tokens)
        req.tools.update(tools or {})
        self._stamp_llm_meta(req)
        check_current()
        resp = await self._llm_stub().Complete(req, timeout=30)
        check_current()
        self._llm_model_used = str(resp.model_used or "")
        calls: list[dict] = []
        if resp.HasField("tool_calls"):
            from google.protobuf.json_format import MessageToDict
            try:
                # Struct 的键是数据非字段名，MessageToDict 原样保留（无 camelCase 转换）
                data = MessageToDict(resp.tool_calls)
            except Exception:
                data = {}
            for tc in (data.get("tool_calls") or []):
                if isinstance(tc, dict) and tc.get("name"):
                    calls.append({
                        "id": tc.get("id") or "",
                        "name": tc["name"],
                        "arguments": self._destruct_nums(tc.get("arguments") or {}),
                    })
        return resp.content, calls

    def _agent_stub(self, endpoint: str):
        # F15：按 endpoint 复用 channel（之前每次新建泄漏）
        if endpoint not in self._ch_agents:
            self._ch_agents[endpoint] = aio_channel(endpoint)
        return agent_pb2_grpc.AgentStub(self._ch_agents[endpoint])

    @classmethod
    def _merge_meta(cls, ctx, meta: dict | None, context_scopes=None) -> dict:
        """One projection for unary/stream/legacy after merging runtime metadata."""
        check_context(ctx)
        prefs = dict(getattr(ctx, "prefs", None) or {})
        prefs.pop(cap_contract.HEADER, None)
        # CA2-08: the operation header is server-owned (Step); clients cannot supply it.
        prefs.pop(operation.HEADER, None)
        # CA2-09: the edge's confirmation snapshot only travels from the pending record.
        prefs.pop("confirm_state", None)
        # CA2-10: the observation reference is minted per dispatch by the Step.
        prefs.pop(effect_evidence.META, None)
        # CA2-15 S1b: the memory epoch is read by the server before this turn's memory reads.
        prefs.pop("memory_epoch", None)
        # CA2-15 S2: the read projection comes from the server's voice proof check.
        prefs.pop("memory_projection", None)
        merged = {**prefs, **dict(meta or {})}
        merged.pop("memory_epoch", None)
        merged.pop("memory_projection", None)
        if getattr(ctx, "memory_epoch", ""):
            merged["memory_epoch"] = ctx.memory_epoch
        if getattr(ctx, "memory_projection", ""):
            merged["memory_projection"] = ctx.memory_projection
        if operation.HEADER in merged:
            merged[operation.HEADER] = operation.with_plan(
                merged[operation.HEADER], getattr(ctx, "task_identity", None))
        merged.pop("granted_scopes", None)
        for key in ("current_lat", "current_lng", "current_accuracy_m", "current_location_at",
                    "current_location_source", "vision_frame_id", "occupant_name"):
            merged.pop(key, None)
            if key in prefs:
                merged[key] = prefs[key]
        # Client/step metadata cannot supply authoritative vehicle observations.
        merged.pop("vehicle_battery", None)
        merged.pop("vehicle_observation", None)
        view = check_context(ctx)
        if view is not None and "vehicle_state" in (context_scopes or ()):
            values, provenance = view.vehicle(consume=True, keys=context_access.VEHICLE_KEYS)
            if values:
                import json
                merged["vehicle_observation"] = json.dumps({
                    "version": 2, "vehicle_id": ctx.vehicle_id, "state": values,
                    "signals": {k: provenance["signals"][k] for k in values}}, separators=(",", ":"))
        merged = context_access.project_meta(ctx, merged, context_scopes)
        granted = sorted(context_access.grants(ctx))
        if granted:
            merged["granted_scopes"] = ",".join(granted)
        tid = getattr(ctx, "trace_id", "") or ""
        if tid:
            merged.setdefault("trace_id", tid)
        return merged

    def _exec_request(self, intent: str, slots: dict, ctx, meta: dict | None,
                      context_scopes=None):
        return agent_pb2.ExecuteRequest(
            session_id=ctx.session_id if ctx else "",
            intent=common_pb2.Intent(
                name=intent, slots=slots,
                raw_text=getattr(ctx, "raw_text", "") or "",
                confidence=0.9),
            context=common_pb2.ContextRef(
                session_id=ctx.session_id if ctx else "",
                user_id=ctx.user_id if ctx else "",
                vehicle_id=ctx.vehicle_id if ctx else "",
            ),
            meta=self._merge_meta(ctx, meta, context_scopes),
        )

    @staticmethod
    async def _contract_preflight(stub, intent, meta, timeout):
        expected = (meta or {}).get(cap_contract.HEADER, "")
        if not expected or cap_contract.known_legacy_digest(expected):
            return None, timeout
        start = time.monotonic()
        try:
            manifest = await stub.Describe(agent_pb2.DescribeRequest(), timeout=min(timeout, 2.0))
            cap = next((c for c in manifest.capabilities if c.intent == intent), None)
            if cap is None or cap_contract.capability_digest(manifest, cap) != expected:
                return cap_contract.rejected("capability_contract_changed"), timeout
        except (grpc.aio.AioRpcError, cap_contract.ContractError):
            return cap_contract.rejected("capability_contract_unavailable"), timeout
        return None, max(0.001, timeout - (time.monotonic() - start))

    async def call_agent(self, endpoint: str, intent: str, slots: dict,
                         ctx=None, meta: dict | None = None,
                         timeout: float = _DEFAULT_TIMEOUT,
                         context_scopes=None) -> agent_pb2.ExecuteResponse:
        """meta 随 ExecuteRequest.meta 下发给 Agent（确认续接标记、trace、会话偏好等）。

        context_scopes：Agent manifest 声明需要的敏感上下文（location|vehicle_state），
        由 dispatcher 传 step.context_scopes，据此最小化下发精确位置/电量。
        timeout 由 dispatcher 传 step.latency_budget_ms/1000——慢 Agent（trip-planner 20s+、
        info 调研）需大于默认 10s，否则开思考后会被 10s 卡死。"""
        stub = self._agent_stub(endpoint)
        denied, timeout = await self._contract_preflight(stub, intent, meta, timeout)
        if denied is not None:
            return denied
        req = self._exec_request(intent, slots, ctx, meta, context_scopes)
        view = check_context(ctx)
        if view is not None:
            timeout = view.remaining(timeout)
        result = await stub.Execute(req, timeout=timeout)
        check_context(ctx)
        return result

    async def call_agent_stream(self, endpoint: str, intent: str, slots: dict,
                                ctx=None, meta: dict | None = None,
                                timeout: float | None = None, context_scopes=None):
        """流式调用 Agent.ExecuteStream，归一化为 (kind, payload) 元组：
        ("speech", str) / ("action", AgentAction) / ("final", ExecuteResponse)。
        供 engine 单步开放域流式直通（边想边说）。

        `timeout` 缺省 = AGENT_STREAM_TIMEOUT_S（D0 用缺省；T2 按 step.latency_budget_ms 显式传）。
        """
        if timeout is None:
            timeout = AGENT_STREAM_TIMEOUT_S
        stub = self._agent_stub(endpoint)
        denied, timeout = await self._contract_preflight(stub, intent, meta, timeout)
        if denied is not None:
            yield ("final", denied)
            return
        req = self._exec_request(intent, slots, ctx, meta, context_scopes)
        view = check_context(ctx)
        if view is not None:
            timeout = view.remaining(timeout)
        async for ev in stub.ExecuteStream(req, timeout=timeout):
            check_context(ctx)
            which = ev.WhichOneof("event")
            if which == "speech_delta":
                yield ("speech", ev.speech_delta)
            elif which == "action":
                yield ("action", ev.action)
            elif which == "final":
                yield ("final", ev.final)

    async def query_edge_operation(self, vehicle_id: str, operation_id: str, *, step_id: str,
                                   timeout: float):
        """CA2-11: read-only lookup in the vehicle's operation log; never reaches VAL."""
        envelope = channel_pb2.EdgeCallEnvelope(
            vehicle_id=vehicle_id,
            call=channel_pb2.EdgeCall(step_id=step_id, operation_query=operation_id))
        result = await self._edge_stub().DispatchToEdge(envelope, timeout=timeout)
        if not result.HasField("result"):
            raise RuntimeError("edge result missing execute response")
        return result.result

    async def dispatch_to_edge(self, vehicle_id: str, step, ctx):
        """Call the requesting vehicle's edge executor through Cloud Gateway."""
        logger.info("DispatchToEdge: vehicle=%s step=%s intent=%s",
                    vehicle_id, step.id, step.intent)
        meta = self._merge_meta(ctx, step.meta)
        expected = meta.get(cap_contract.HEADER, "")
        budget = step.latency_budget_ms / 1000.0
        if expected and not cap_contract.known_legacy_digest(expected):
            started = time.monotonic()
            probe = channel_pb2.EdgeCallEnvelope(vehicle_id=vehicle_id, call=channel_pb2.EdgeCall(
                step_id=step.id + "-contract", contract_query=step.intent))
            observed = await self._edge_stub().DispatchToEdge(probe, timeout=min(budget, 2.0))
            if (not observed.HasField("result") or observed.result.status != agent_pb2.ExecuteResponse.OK
                    or dict(observed.result.data).get(cap_contract.HEADER) != expected):
                return cap_contract.rejected("capability_contract_unavailable")
            budget = max(0.001, budget - (time.monotonic() - started))
        if getattr(ctx, "trace_id", ""):
            meta.setdefault("trace_id", ctx.trace_id)
        envelope = channel_pb2.EdgeCallEnvelope(
            vehicle_id=vehicle_id,
            call=channel_pb2.EdgeCall(
                step_id=step.id,
                intent=common_pb2.Intent(
                    name=step.intent,
                    slots=step.slots,
                    confidence=0.9,
                ),
                meta=meta,
            ),
        )
        result = await self._edge_stub().DispatchToEdge(
            envelope, timeout=budget)
        if not result.HasField("result"):
            raise RuntimeError("edge result missing execute response")
        logger.info("DispatchToEdge result: status=%s speech=%s",
                    result.result.status, result.result.speech[:80])
        return result.result
