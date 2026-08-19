"""受控 MCP 桥（M3 P2）——一个 Agent 承载 N 个 MCP server，而不是每个外部服务写一个 Agent。

三条边界（母提案 §4.F + 子 RFC §4）：
1. **接入永远是人工准入**：server / tool / 版本 / schema 指纹全在 `servers.yaml`，
   动态放行是明确的不做项。
2. **MCP 只做生态桥**：内部核心能力保持 gRPC 强类型低延迟，不迁 MCP。
3. **写操作生命周期五项缺一不接**：幂等键 / 订单状态机 / timeout·cancel / 补偿 / 审计。
   订单状态机**复用 M2 的 `task_ledger`**（幂等受理、状态机、cancel 拉模式全在里面），
   不新建表——它是 Ledger 的第二个载体。

capability 是**启动期从准入清单合成**的（`bootstrap()` 在 `serve()` 之前跑）：
新增工具 = 改 servers.yaml + 人工审，零编排核心改动，也零本文件改动。
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import unquote

from agents._sdk import AgentResult, BaseAgent, NEED_CONFIRM, NEED_SLOT
from agents._sdk.ledger import DONE, FAILED as LEDGER_FAILED, Duplicate, idem_key
from agents._sdk.payment_client import PaymentClient
from agents._sdk.provenance import attach
from cockpit.agent.v1 import agent_pb2

from runtime.clock import local_dt

from . import candidate_ref
from .admission import (SAFE_MERCHANT_STATUSES, admit, admit_workflow,
                        check_version, load_servers, normalize_hostname)
from .mcp_client import McpError, StdioMcpClient
from .order_ref import (HISTORY, NEUTRAL, OrderRef, allows_history_fallback,
                        is_deictic_placeholder, reference_scope)

logger = logging.getLogger("agent.mcp_bridge")

# HMI / Ledger 的商户订单展示上限。当前接入是餐饮订单；100 万元已远高于
# 合理业务值，同时能阻断指数/超长数字把 UI、日志或 JSON 变成资源放大器。
_MAX_MERCHANT_ORDER_AMOUNT_CENTS = 100_000_000

# 缺槽追问词：按**槽位名**索引。此前 NEED_SLOT 恒说「要点什么？」——那是下单的词，
# 取消复用同一条写路径后就会对着「取消订单」问「要点什么？」。
# 槽位名来自 capability 声明（servers.yaml），不是 intent 字面量。
_SLOT_PROMPTS = {
    "item": "要点什么？",
    "order_id": "要操作哪一单？说个订单号，或者先说「查一下我的订单」。",
}

# Q10：本会话没下过单时对「刚才那笔订单」的诚实回答。**与 `_SLOT_PROMPTS` 的
# 泛泛追问刻意不同**——那句让用户以为系统没听清，这句说的是「你以为的那一单不存在」。
_NO_ORDER_THIS_SESSION = (
    "这次对话里我没有帮您下过单。要查以前的订单，说个订单号，"
    "或者说「查一下我之前的订单」。")


def _history_prefix(created_at: float) -> str:
    """把「这不是本次那一单」变成一句用户能核对的话。

    ⚠ 墙钟必须走 `runtime.clock`——容器 TZ=UTC 而业务时区是 UTC+8，
    裸 `datetime.fromtimestamp` 会把 8 月 15 日 08:00 那单说成 8 月 15 日 00:00，
    刚好在跨日边界上把日期也说错一天（§4.3 时区族，本仓已复发过四次）。
    """
    d = local_dt(created_at)
    return f"这是您 {d.month} 月 {d.day} 日下的那笔（不是本次对话里的）："

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANIFEST = os.path.join(_HERE, "manifest.yaml")
_SERVERS = os.path.join(_HERE, "servers.yaml")

LEDGER_KIND = "mcp_order"           # 契约登记 conventions §9.6 的 kind 全集
DEMO_PROVIDER = "demo"              # _prov 标记（conventions §9.3）：演示商户永远标出来
_HIDDEN_ADMIN_TOOL = "__e2e.namespace.admin"

PERSONAL_DATA_TARGETS = (
    {
        # Checkout snapshots contain store/product/specification/amount data.
        # The owner index lets privacy_user_all coordinate an explicit,
        # provable delete instead of relying on the ten-minute safety TTL.
        # An active create/cancel lease deliberately returns pending; the
        # caller retries after the operation releases its lease.
        "id": "merchant_draft",
        "storage_variants": (
            "mcp:merchant:draft:*",
            "mcp:merchant:current:*",
            "mcp:merchant:owner:*",
        ),
    },
    {
        # The demo MCP server is an external subprocess and owns these maps.
        "id": "mcp_demo_order",
        "storage_variants": ("demo_coffee._ORDERS", "demo_coffee._BY_IDEM"),
    },
)


class _Binding:
    """一个准入通过的工具：intent → (server client, tool spec)。"""

    def __init__(self, server, client, tool, input_schema):
        self.server = server
        self.client = client
        self.tool = tool
        self.input_schema = input_schema if isinstance(input_schema, dict) else {}


class _WorkflowBinding:
    """复合 intent → 已准入 server 内部工具 + 确定性 workflow 实例。"""

    def __init__(self, server, spec, workflow):
        self.server = server
        self.spec = spec
        self.workflow = workflow


class McpBridgeAgent(BaseAgent):
    def __init__(self, servers_path: str = _SERVERS,
                 payment: PaymentClient | None = None, draft_store=None):
        super().__init__(_MANIFEST)
        self._servers_path = servers_path
        self._bindings: dict[str, _Binding] = {}
        self._workflow_bindings: dict[str, _WorkflowBinding] = {}
        self._clients: list = []
        self.rejections: list[str] = []
        # 涉钱走 payment-gateway（§9.9/§9.17）：桥只发登记意图，不持支付凭证
        self._payment = payment or PaymentClient()
        if draft_store is None:
            from .merchant.drafts import RedisDraftStore
            draft_store = RedisDraftStore()
        self._draft_store = draft_store

    async def delete_personal_data(self, user_id: str, action: str) -> bool:
        """Delete the owner-indexed shared merchant-draft namespace only."""
        if action != "privacy_user_all" or not user_id:
            return False
        delete_owner = getattr(self._draft_store, "delete_owner", None)
        draft_deleted = False
        try:
            if callable(delete_owner):
                draft_deleted = await delete_owner(user_id) is True
        except Exception as exc:
            logger.warning("MCP merchant draft privacy delete failed: %s",
                           type(exc).__name__)
        return draft_deleted

    # ── 启动期准入 ────────────────────────────────────────────────────
    async def bootstrap(self) -> None:
        """启动 MCP server → 握手 → 校验版本/白名单/schema → 合成 capability。

        **在 `serve()` 之前调用**（注册发生在 serve 里）。任何一台 server 起不来 /
        版本对不上，只让它自己的工具缺席，桥照常服务其余——但**绝不静默降级成假数据**。
        """
        for spec in load_servers(self._servers_path):
            if spec.env_error:
                # 缺 env（如商户 token 未配）→ 该 server 整台诚实缺席，
                # 不静默拿空 token 出站吃 401（§9.9 transport 行）
                self.rejections.append(f"{spec.id}: {spec.env_error}")
                logger.warning("[mcp:%s] **拒载**：%s", spec.id, spec.env_error)
                continue
            client = self._make_client(spec)
            try:
                await client.start()
                await client.initialize()
            except (McpError, OSError, Exception) as e:
                self.rejections.append(f"{spec.id}: 启动/握手失败 {e}")
                logger.warning("[mcp:%s] 启动失败，该 server 全部工具缺席：%s", spec.id, e)
                await client.close()
                continue
            reason = check_version(spec, client.server_info)
            if reason:
                self.rejections.append(f"{spec.id}: {reason}")
                logger.warning("[mcp:%s] **拒载**：%s（版本变更必须重新人工准入）",
                               spec.id, reason)
                await client.close()
                continue
            try:
                offered = await client.list_tools()
            except Exception as e:
                self.rejections.append(f"{spec.id}: tools/list 失败 {e}")
                await client.close()
                continue
            admitted, rejected = admit(spec, offered)
            self.rejections.extend(f"{spec.id}: {r}" for r in rejected)
            for r in rejected:
                logger.warning("[mcp:%s] 拒绝工具 %s", spec.id, r)
            for tool, schema in admitted:
                self._bindings[tool.intent] = _Binding(spec, client, tool, schema)
            by_name = {tool.name: _Binding(spec, client, tool, schema)
                       for tool, schema in admitted}
            for workflow_spec in spec.workflows:
                workflow_error = admit_workflow(workflow_spec, by_name)
                if workflow_error:
                    reason = f"{workflow_spec.intent}: {workflow_error}"
                    self.rejections.append(f"{spec.id}: {reason}")
                    logger.warning("[mcp:%s] 拒绝 workflow %s", spec.id, reason)
                    continue
                workflow_tools = {
                    name: by_name[name] for name in workflow_spec.required_tools}
                try:
                    workflow = self._make_workflow(
                        workflow_spec.handler, spec, workflow_spec, workflow_tools)
                except Exception as exc:
                    self.rejections.append(
                        f"{spec.id}: {workflow_spec.intent}: workflow_init_failed")
                    logger.warning("[mcp:%s] workflow %s 初始化失败：%s",
                                   spec.id, workflow_spec.intent,
                                   type(exc).__name__)
                    continue
                self._workflow_bindings[workflow_spec.intent] = _WorkflowBinding(
                    spec, workflow_spec, workflow)
            self._clients.append(client)
            logger.info("[mcp:%s] 准入 %d 个工具：%s", spec.id, len(admitted),
                        [t.intent for t, _ in admitted])
        self._sync_capabilities()

    @staticmethod
    def _make_client(spec):
        """按 transport 分派客户端（§9.9 批 3）。两种客户端鸭子同形，其余零改。"""
        if spec.transport == "streamable_http":
            from .mcp_client import HttpMcpClient
            return HttpMcpClient(spec.id, spec.url, spec.headers,
                                 timeout_s=spec.startup_timeout_ms / 1000.0)
        return StdioMcpClient(spec.id, spec.command,
                              timeout_s=spec.startup_timeout_ms / 1000.0)

    def _make_workflow(self, handler: str, server, spec, tools):
        """受审 handler 名只映射本地确定性 codec；不做动态 import。"""
        if handler == "mcdonalds":
            from .merchant.mcdonalds import McDonaldsWorkflow
            return McDonaldsWorkflow(server, spec, tools, self._draft_store,
                                     self.ledger, self._payment)
        if handler == "luckin":
            from .merchant.luckin import LuckinWorkflow
            return LuckinWorkflow(server, spec, tools, self._draft_store,
                                  self.ledger, self._payment)
        raise ValueError("unknown workflow handler")

    def _sync_capabilities(self) -> None:
        """把准入的工具写进 manifest.capabilities——注册中心看到的就是这份。"""
        caps = []
        for intent, b in self._bindings.items():
            if not b.tool.expose:
                continue
            desc = b.tool.description or f"{b.server.id} 的 {b.tool.name}"
            if b.server.demo:
                desc += "（演示商户，不产生真实交易）"
            caps.append(agent_pb2.Capability(
                intent=intent, description=desc, slots=b.tool.slots,
                examples=b.tool.examples,
                require_confirm=bool(b.tool.require_confirm or b.tool.write),
            ))
        for intent, b in self._workflow_bindings.items():
            spec = b.spec
            if not spec.expose:
                continue
            caps.append(agent_pb2.Capability(
                intent=intent,
                description=spec.description or f"{b.server.id} 商户订单工作流",
                slots=spec.slots,
                examples=spec.examples,
                require_confirm=bool(spec.require_confirm),
            ))
        del self.manifest.capabilities[:]
        self.manifest.capabilities.extend(caps)
        logger.info("MCP 桥合成 capability %d 个：%s",
                    len(caps), [c.intent for c in caps])

    async def shutdown(self) -> None:
        for c in self._clients:
            await c.close()

    # ── 请求处理 ─────────────────────────────────────────────────────
    async def handle(self, intent, ctx, meta) -> AgentResult:
        workflow_binding = self._workflow_bindings.get(intent.name)
        if workflow_binding is not None:
            spec = workflow_binding.spec
            required = set(spec.required_scopes or [])
            if required - self._granted_scopes(meta):
                return AgentResult(
                    speech="当前账号缺少商户授权，不能执行这个操作。")
            confirmed = str((meta or {}).get("confirmed", "")).lower() == "true"
            token = str((intent.slots or {}).get("checkout_token") or "")
            if intent.name.endswith("_cancel"):
                return await workflow_binding.workflow.cancel(intent, ctx, meta)
            # Q10 双入口收敛（2026-08-19）：文本入口的说法先在**上一轮那张卡的候选集**
            # 里做确定性翻译，翻成按钮 `send_text` 里那个商家原名，再进各商户既有的
            # 选品链。挂在这里而不是各 workflow 里：这是「槽值指代哪一项」的通用判定，
            # 与商户无关；商户特有的严格判据仍在各自的 `_matching_products`，
            # 本步只把用户的说法换成它认得的那个词。
            # **只对 prepare/menu 生效**——confirm/cancel 靠 checkout_token 寻址，
            # 草稿里的商品早已定死；在那条路上改槽值只会与已核价的草稿对不上。
            # 条件写成「menu 或 未确认」而不是 `not confirmed`：menu 分支刻意排在
            # confirmed 判定**之前**（看菜单没有可确认的东西），所以一句带确认标记的
            # 菜单请求仍该翻译。
            if intent.name.endswith(".menu") or not confirmed:
                self._resolve_candidate_slot(workflow_binding.spec, intent, meta)
            # 只读看菜单：与 prepare/confirm 是**并列的第三条入口**，不建草稿、
            # 不碰写工具。放在 confirmed 判定之前——菜单没有可确认的东西，
            # 让它掉进 confirm 分支会拿一个不存在的 checkout_token 去核销。
            if intent.name.endswith(".menu"):
                return await workflow_binding.workflow.menu(intent, ctx, meta)
            if confirmed:
                return await workflow_binding.workflow.confirm(
                    intent, ctx, meta, token=token)
            return await workflow_binding.workflow.prepare(intent, ctx, meta)
        b = self._bindings.get(intent.name)
        if not b or not b.tool.expose:
            # 准入清单里没有 = 这个能力今天不存在。诚实说，不猜、不静默成功。
            return AgentResult(speech="这个外部服务还没接入。")
        required = set(b.tool.required_scopes or [])
        granted = self._granted_scopes(meta)
        missing_scopes = sorted(required - granted)
        if missing_scopes:
            return AgentResult(
                speech="当前账号缺少商户授权，不能执行这个操作。")
        if not b.client.healthy or not b.client.alive:
            return AgentResult(speech=f"{b.server.id} 暂时不可用，稍后再试。")
        return await (self._call_write(b, intent, ctx, meta) if b.tool.write
                      else self._call_read(b, intent, ctx, meta))

    @staticmethod
    def _resolve_candidate_slot(spec, intent, meta) -> None:
        """把用户对候选列表的指代翻成商家原名，写进 `candidate_slot`（就地改写）。

        判不出就**原样不动**——回到今天的行为（商户自己追问/出选项卡），不猜。
        翻错等于系统替用户改了他点的东西，而它没有任何「我不太确定」的信号。

        ## 三条通道，因为 planner 会把同一句话填成三种样子

        真栈同一句「麦当劳的第七个多少钱」三次取样，序数分别落在三个地方：

        | 取样 | planner 填成 | 靠哪条通道 |
        |---|---|---|
        | 1 | `item_query="第七个"` | ① 目标槽自己 |
        | 2 | **`category`**`="第7个"` | ② 错位槽重定向 |
        | 3 | **一个槽都没填** | ③ 原话兜底 |

        ⇒ 本步的判据因此是完整版：不只「序数落到哪一项」不该让 LLM 数，
        **「序数该放进哪个槽」也不该由它决定**。②③ 都只在目标槽为空时才动手，
        绝不覆盖用户说出口的实质内容。
        """
        slot = str(getattr(spec, "candidate_slot", "") or "")
        slots = getattr(intent, "slots", None)
        if not slot or not isinstance(slots, dict):
            return
        namespace = str(getattr(intent, "name", "") or "").split(".", 1)[0]
        value = str(slots.get(slot) or "").strip()

        # ① 目标槽自己有值：原名 / 序数 / 唯一部分名三通道。
        item = (candidate_ref.resolve(value, meta, namespace=namespace)
                if value else None)
        source = slot

        # ② 值是**纯序数**却落在别的槽上。一个纯序数短语不可能是分类名或门店名
        #    ——planner 把它放进哪个槽都改变不了它在指代第 N 项。命中即把那个槽
        #    **清掉**：留着它，商户侧会先按「有没有这个分类」去过滤然后诚实查无
        #    （真栈原话「餐单里没有「第7个」这一类」）。
        misplaced = ""
        if item is None and not value:
            for name in (getattr(spec, "slots", None) or []):
                if name == slot or not candidate_ref.is_bare_ordinal(
                        slots.get(name)):
                    continue
                item = candidate_ref.resolve(
                    str(slots[name]), meta, namespace=namespace)
                if item is not None:
                    misplaced, source = name, name
                    break

        # ③ 一个槽都没填：回原话取序数。**只认整句恰好出现一次**的那种。
        if item is None and not value:
            item = candidate_ref.from_raw_text(
                getattr(intent, "raw_text", ""), meta, namespace=namespace)
            source = "raw_text"

        if item is None or item["name"] == value:
            return
        logger.info("候选集指代解析：%s 的 %s ← %s %r → %r（第 %d 项）",
                    getattr(intent, "name", ""), slot, source,
                    value or slots.get(misplaced) or
                    getattr(intent, "raw_text", ""),
                    item["name"], item["index"])
        slots[slot] = item["name"]
        if misplaced:
            slots.pop(misplaced, None)

    @staticmethod
    def _granted_scopes(meta) -> set[str]:
        raw = (meta or {}).get("granted_scopes", "")
        if isinstance(raw, str):
            return {scope.strip() for scope in raw.split(",") if scope.strip()}
        if isinstance(raw, (list, tuple, set)):
            return {str(scope).strip() for scope in raw if str(scope).strip()}
        return set()

    async def namespace_admin(self, request: dict) -> dict:
        """Run an E2E-only exact-owner lifecycle operation through a write binding.

        The NATS layer verifies the signed owner first. This method remains
        generic: it discovers the write/compensation pair from admission data,
        and never adds the hidden operation to business capabilities.
        """

        if not isinstance(request, dict) or set(request) != {
            "op", "user_id", "order_id", "intent",
        }:
            return self._admin_result(error="invalid_request")
        op = request.get("op")
        owner = request.get("user_id")
        order_id = request.get("order_id")
        intent = request.get("intent")
        if (
            op not in {
                "count",
                "status",
                "compensate",
                "purge",
                "lifecycle_cleanup",
            }
            or not isinstance(owner, str)
            or not owner
            or not isinstance(order_id, str)
            or not isinstance(intent, str)
            or not intent
            or (op in {"status", "compensate"} and not order_id)
        ):
            return self._admin_result(op=str(op or ""), error="invalid_request")
        binding = self._bindings.get(intent)
        if (
            binding is None
            or not binding.tool.write
            or not binding.tool.compensate_tool
            or not binding.client.healthy
            or not binding.client.alive
        ):
            return self._admin_result(op=op, order_id=order_id,
                                      error="binding_unavailable")
        try:
            arguments = {
                "op": op,
                "order_id": order_id,
                "write_tool": binding.tool.name,
                "compensate_tool": binding.tool.compensate_tool,
            }
            if self._can_forward_owner(binding):
                arguments["_owner_user_id"] = owner
            else:
                arguments.pop("_owner_user_id", None)
            response = await binding.client.call_tool(
                _HIDDEN_ADMIN_TOOL,
                arguments,
                timeout_s=binding.tool.timeout_ms / 1000.0,
                retry_on_session_loss=False,
            )
        except Exception:
            return self._admin_result(op=op, order_id=order_id,
                                      error="admin_unavailable")
        if not response.get("ok"):
            return self._admin_result(op=op, order_id=order_id,
                                      error="admin_rejected")
        data = response.get("data") or {}
        return self._admin_result(
            ok=True,
            op=op,
            count=int(data.get("count") or 0),
            order_id=str(data.get("order_id") or order_id),
            status=str(data.get("status") or ""),
            deleted=int(data.get("deleted") or 0),
        )

    @staticmethod
    def _admin_result(*, ok=False, op="", count=0, order_id="",
                      status="", deleted=0, error="") -> dict:
        return {
            "ok": bool(ok),
            "op": str(op),
            "count": int(count),
            "order_id": str(order_id),
            "status": str(status),
            "deleted": int(deleted),
            "error": str(error),
        }

    @staticmethod
    def _can_forward_owner(binding) -> bool:
        return bool(binding.server.demo and binding.server.transport == "stdio" and
                    binding.tool.forward_owner)

    # ── 只读 ──
    async def _call_read(self, b, intent, ctx, meta) -> AgentResult:
        try:
            declared = list(b.tool.slots or [])
            # const_args 打底（声明死的场景选择器）→ 槽位映射值覆盖 → 系统键最后
            args = {**b.tool.const_args,
                    **{b.tool.arg_map.get(k, k): v
                       for k, v in (intent.slots or {}).items()
                       if v and k in declared}}
            args.pop("_owner_user_id", None)
            # 超长纯数字订单号不能让 LLM 充当转录器：模型只负责选 intent，用户
            # 原句里唯一的 10~40 位数字串才是查询参数真值。真实浏览器链曾把一位
            # 数字抄错，商户因而确定拒绝；这里不放宽到手机号长度以下，也不在有
            # 多个候选时猜测。
            oid_key = b.tool.arg_map.get("order_id", "order_id")
            explicit_order_id = self._explicit_numeric_order_id(
                str(getattr(intent, "raw_text", "") or ""))
            if "order_id" in declared and explicit_order_id:
                args[oid_key] = explicit_order_id
            user_id = str(getattr(ctx, "user_id", "") or "").strip()
            order_ref = OrderRef()
            if user_id:
                # Q10：查单必须绑会话。范围只从**原话**判，不问 LLM。
                scope = reference_scope(str(getattr(intent, "raw_text", "") or ""))
                args, order_ref = await self._resolve_order_ref(
                    b, args, user_id,
                    session_id=str(getattr(ctx, "session_id", "") or "").strip(),
                    scope=scope)
                if order_ref.needs_honest_declination:
                    # **不出站**：没有可查的引用就不该打商户请求。也不退回泛泛的
                    # 「要操作哪一单」——那会让用户以为系统没听清，而真相是
                    # 「你以为的那一单不存在」（§4.3「认不出就返回空」的查单版）。
                    return AgentResult(speech=_NO_ORDER_THIS_SESSION)
                # 只给明确声明 forward_owner 的受控 demo 下发内部 owner。官方 remote
                # 使用权威 granted_scopes，不认识也不应看到内部身份字段。
                if self._can_forward_owner(b):
                    args["_owner_user_id"] = user_id
            # 端到端取证修（2026-08-11）：declared 槽位一个都没有、账本也回填不到
            # （用户在商户 App 下的单不在我们账本）→ **追问而不是打一发必败请求**
            # ——此前无单号查真实商户，收到「订单号不存在」后把 2836 字 API 文档
            # 念给用户。写路径早有同款追问，读路径漏了。
            # 账本回填的引用（order_id **或幂等键**——超时单只有幂等键）也算「有
            # 引用」：那是 M-D「按幂等键跟商户核对」的既有路径，追问不许打断它。
            declared_args = {b.tool.arg_map.get(k, k) for k in declared}
            declared_args.add(b.tool.arg_map.get("idempotency_key",
                                                 "idempotency_key"))
            missing = self._missing_required_slot(b, args)
            if missing:
                return self._need_slot(missing)
            if declared and not any(args.get(a) for a in declared_args):
                return self._need_slot(declared[0])
            if not self._can_forward_owner(b):
                args.pop("_owner_user_id", None)
            res = await b.client.call_tool(b.tool.name, args,
                                           timeout_s=b.tool.timeout_ms / 1000.0,
                                           retry_on_session_loss=(
                                               b.tool.retry_policy != "never"))
        except Exception as e:
            logger.warning("[mcp:%s] %s 调用失败：%s", b.server.id, b.tool.name, e)
            # 铁律③：外部源失败诚实说拿不到，绝不改供假数据（R9 契约用 OK 承载话术）
            return AgentResult(speech="这个外部服务暂时拿不到数据，稍后再试。")
        if not res["ok"]:
            if b.tool.result_map:
                # 声明式订单归一是信任边界：协议级错误也只给固定话术，原始
                # content/text/data 既不进 LLM，也不进卡片或 AgentResult.data。
                return AgentResult(
                    speech="商户暂时无法返回这笔订单的有效状态，请稍后再试。")
            speech = await self._readable_speech(b, intent, res, ok=False)
            return AgentResult(speech=speech or "外部服务返回了错误。")
        if b.tool.result_map:
            normalized, error = self._normalize_declared_result(b, res)
            if error:
                return AgentResult(speech=error)
            requested_order_id = str(args.get(
                b.tool.arg_map.get("order_id", "order_id")) or "").strip()
            if (requested_order_id and
                    normalized.get("order_id") != requested_order_id):
                return AgentResult(
                    speech="商户返回的订单号不一致，请到官方应用核对。")
            card = self._card(b, "mcp_order", normalized)
            # Q10：不是本会话那一单就必须说清它是什么时候的——**不标注就与
            # 「刚才那单」不可区分**，正是 I-021 那个错误定性的成因。
            prefix = (_history_prefix(order_ref.created_at)
                      if order_ref.should_label_as_history else "")
            return AgentResult(
                speech=(f"{prefix}订单 {normalized['order_id']} 当前状态："
                        f"{normalized['status']}。"),
                ui_card=card, data=normalized)
        card = self._card(b, "mcp_result", self._slim_payload(res))
        speech = await self._readable_speech(b, intent, res, ok=True)
        # ⚠ 这条分支（无 result_map 的自由话术）**也要标注**。首版只改了上面那条
        # 声明式分支，测试当场抓到——正是本卡自己那句「同一件事的两条分支只修了
        # 一条」在**我自己这次修法里**的第四次应验。
        stale = (_history_prefix(order_ref.created_at)
                 if order_ref.should_label_as_history else "")
        return AgentResult(
            speech=self._demo_prefix(b) + stale + (speech or "查到了。"),
            ui_card=card, data=res["data"])

    @staticmethod
    def _dig_result(value, path: str):
        current = value
        for part in str(path or "").split("."):
            if not part or not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    @staticmethod
    def _explicit_numeric_order_id(raw_text: str) -> str:
        matches = re.findall(r"(?<![0-9])([0-9]{10,40})(?![0-9])",
                             str(raw_text or ""))
        return matches[0] if len(matches) == 1 else ""

    @staticmethod
    def _strict_allowed(value, allowed: list) -> bool:
        return any(type(value) is type(candidate) and value == candidate
                   for candidate in allowed)

    @classmethod
    def _normalize_declared_result(cls, b, res: dict) -> tuple[dict, str]:
        """Apply a pinned business envelope and return only declared fields.

        This path is intentionally deterministic: order truth must not depend
        on an LLM paraphrasing provider documentation.  Missing or rejected
        business fields fail closed and raw merchant data is not exposed.
        """
        payload = res.get("data")
        if not isinstance(payload, dict):
            return {}, "商户没有返回完整的订单结果，请稍后再试。"
        for path, allowed in b.tool.success_predicate.items():
            if not cls._strict_allowed(cls._dig_result(payload, path), allowed):
                return {}, "商户没有返回这笔订单的有效状态，请核对订单号后再试。"
        normalized: dict = {}
        for key, path in b.tool.result_map.items():
            value = cls._dig_result(payload, path)
            if value in (None, "") or isinstance(value, (dict, list, bool)):
                return {}, "商户返回的订单信息不完整，请稍后再试。"
            if key == "amount_cents":
                raw_amount = str(value).strip()
                if not raw_amount or len(raw_amount) > 32:
                    return {}, "商户返回的订单金额无效，请到官方应用核对。"
                try:
                    amount = Decimal(raw_amount)
                    if not amount.is_finite() or amount < 0:
                        raise InvalidOperation
                    if b.tool.amount_unit == "yuan":
                        amount *= 100
                    cents = int(amount.quantize(Decimal("1"),
                                                rounding=ROUND_HALF_UP))
                except (InvalidOperation, ValueError, TypeError):
                    return {}, "商户返回的订单金额无效，请到官方应用核对。"
                if not 0 <= cents <= _MAX_MERCHANT_ORDER_AMOUNT_CENTS:
                    return {}, "商户返回的订单金额无效，请到官方应用核对。"
                normalized[key] = cents
            elif key == "status":
                if not isinstance(value, str):
                    return {}, "商户返回的订单信息不完整，请稍后再试。"
                source = value.strip()
                target = b.tool.status_map.get(source)
                if (not source or target not in SAFE_MERCHANT_STATUSES):
                    return {}, "商户返回的订单信息不完整，请稍后再试。"
                normalized[key] = target
            else:
                if not isinstance(value, str):
                    return {}, "商户返回的订单信息不完整，请稍后再试。"
                text = value.strip()
                if (not text or len(text) > 128 or
                        any(ord(ch) < 32 or ord(ch) == 127 for ch in text) or
                        cls._contains_url_like(text)):
                    return {}, "商户返回的订单信息不完整，请稍后再试。"
                normalized[key] = text
        return normalized, ""

    @staticmethod
    def _missing_required_slot(b, args: dict) -> str:
        required = b.input_schema.get("required") or []
        if not isinstance(required, list):
            return ""
        reverse_map = {mapped: slot for slot, mapped in b.tool.arg_map.items()}
        for arg_name in required:
            if not isinstance(arg_name, str):
                continue
            if arg_name not in args or args.get(arg_name) in (None, ""):
                return reverse_map.get(arg_name, arg_name)
        return ""

    @staticmethod
    def _need_slot(missing: str) -> AgentResult:
        return AgentResult(
            status=NEED_SLOT,
            speech=_SLOT_PROMPTS.get(missing, "还差点信息，说说具体是哪一个？"),
            missing_slots=[missing])

    @staticmethod
    def _slim_payload(res: dict) -> dict:
        """卡片瘦身（端到端取证修）：text 不进卡（speech 已承载，此前 13KB 原始
        数据+全文 text 双份塞卡）；data 字段串值逐个截 800 字、总预算 4KB。"""
        out: dict = {}
        budget = 4096
        for k, v in (res.get("data") or {}).items():
            s = v if isinstance(v, (int, float, bool)) or v is None else str(v)
            if isinstance(s, str) and len(s) > 800:
                s = s[:800] + "…"
            cost = len(str(s))
            if budget - cost < 0:
                out["_truncated"] = True
                break
            budget -= cost
            out[k] = s
        return out

    @staticmethod
    def _relevance_material(question: str, text: str, data,
                            budget: int = 3000) -> str:
        """summarize 话术的取材：超预算时按与问句的相关性优先打包，不再盲截前段。

        营养表这类大列表被 `[:3000]` 盲截时，排在截断点之后的条目对 LLM 等于不存在
        ——「蘸酱麦辣大四角的热量」查无正是这么来的（demo-mkemhn c47671f5：品项在
        菜单与订单预览里都出现过，营养表 material 却截在它之前）。判据零领域字面量：
        原文切成片段、按问句字符 bigram 重合度排序装包，问句点名的条目只要在数据里
        就必然进 prompt；预算内装不下的才丢，最后按原文顺序拼回（保持可读性）。
        问句太短提不出 bigram 时退回原有的头部截断——没有更好的依据就不假装有。"""
        serialized = (str(text or "") + "\n"
                      + json.dumps(data, ensure_ascii=False))
        if len(serialized) <= budget:
            return serialized
        normalized_q = re.sub(r"[\s\W_]+", "", str(question or "").lower())
        bigrams = {normalized_q[i:i + 2] for i in range(len(normalized_q) - 1)}
        if len(bigrams) < 2:
            return serialized[:budget]
        # 行级切分；仍超长的行按定宽窗口再切（带少量重叠，防条目恰跨窗口边界）
        segments: list[str] = []
        for line in serialized.splitlines():
            line = line.strip()
            if not line:
                continue
            if len(line) <= 160:
                segments.append(line)
            else:
                step = 140
                for start in range(0, len(line), step):
                    segments.append(line[start:start + 160])
        if len(segments) <= 1:
            return serialized[:budget]

        def _score(segment: str) -> int:
            s = re.sub(r"[\s\W_]+", "", segment.lower())
            return sum(1 for gram in bigrams if gram in s)

        ranked = sorted(enumerate(segments),
                        key=lambda pair: (-_score(pair[1]), pair[0]))
        chosen: list[tuple[int, str]] = []
        remaining = budget - 40                     # 留出结尾的筛选说明
        for index, segment in ranked:
            cost = len(segment) + 1
            if cost > remaining:
                continue
            chosen.append((index, segment))
            remaining -= cost
        if not chosen:
            return serialized[:budget]
        chosen.sort(key=lambda pair: pair[0])
        return ("\n".join(segment for _, segment in chosen)
                + "\n（内容较长，已按与问题的相关性筛选）")

    async def _readable_speech(self, b, intent, res: dict, *, ok: bool) -> str:
        """话术形态（§9.9 speech_mode）。

        真实商户返回的是「给 LLM 读的 API 文档 + 原始数据」——`summarize` 把它
        交给 LLM 用中文一两句**直接回答用户的问题**；LLM 不可用时诚实回落
        （不念文档：截断 + 引导看屏幕）。`raw`（demo 商户中文短回执）逐字保持。
        """
        text = res.get("text") or ""
        if b.tool.speech_mode != "summarize":
            return text
        raw_q = str(getattr(intent, "raw_text", "") or "")
        material = self._relevance_material(raw_q, text, res.get("data") or {})
        try:
            summary = (await self.llm.complete([
                {"role": "system", "content":
                 "你是车载助手。根据外部服务的返回内容，用一两句中文口语直接回答"
                 "用户的问题；只答与问题相关的部分，不要念字段名或文档结构；"
                 "内容答不了问题就说明没查到并给一句下一步建议；没查到但内容里有"
                 "名称相近的条目时，报出最接近的一两条供用户确认。不要编造数据。"},
                {"role": "user", "content":
                 f"用户问：{raw_q}\n外部服务返回：\n{material}"},
            ], max_tokens=200, temperature=0.3) or "").strip()
            if summary:
                return summary
        except Exception as e:
            logger.warning("[mcp:%s] 话术重述失败（回落截断）：%s", b.server.id, e)
        if not ok:
            return "商户返回了错误，这次没有查到。"
        return (text[:100] + "…详情已放在屏幕上。") if len(text) > 120 else text

    async def _backfill_write_slots(self, b, declared: list, ctx, *,
                                    scope: str = NEUTRAL) -> tuple[dict, OrderRef]:
        """补偿类写操作的槽位回填（只回填订单号，只从账本，只取确定完成的那单，
        且只认**同一商户**——跨商户污染修，2026-08-11）。

        **会话范围（Q10，2026-08-16）**：本函数早就「优先本 session」，但仍会
        **回落**到跨 session 的历史单——于是「取消刚才那单」在本会话没下过单时，
        会捞一笔三天前的单进确认（I-037 的形态）。现在 `scope=SESSION` 时不回落。

        ⚠ 写路径比读路径更该严：查单捞错只是看错，取消/退款捞错是**不可逆写**。
        `NEUTRAL` 维持既有回落（有二次确认闸兜底、且订单号会念给用户），
        但确认话术要标注那单是什么时候的——**用户该在点头之前就知道**。

        返回 `(slots, OrderRef)`，与读路径 `_resolve_order_ref` **形态一致**：
        一条返回 dict、另一条返回 tuple 就又是一处「同一件事的两个样子」，
        而那正是本卡要消灭的形态。
        """
        if "order_id" not in declared or not self.ledger:
            return {}, OrderRef(scope=scope)
        user_id = str(getattr(ctx, "user_id", "") or "").strip()
        if not user_id:
            return {}, OrderRef(scope=scope)
        session_id = str(getattr(ctx, "session_id", "") or "").strip()
        try:
            recent = await self.ledger.recent(
                user_id, kind=LEDGER_KIND, limit=20)
        except Exception as e:
            logger.debug("[mcp] 取最近订单失败（照常追问）：%s", e)
            return {}, OrderRef(scope=scope)
        seen: set[str] = set()
        fallback: tuple[dict, OrderRef] | None = None
        for task in recent or []:
            if str(getattr(task, "user_id", "") or "") != user_id:
                continue
            if str(getattr(task, "kind", "") or "") != LEDGER_KIND:
                continue
            ref = getattr(task, "result_ref", {}) or {}
            if not isinstance(ref, dict):
                continue
            if (ref.get("server") or "demo-coffee") != b.server.id:
                continue

            candidate_id = str(ref.get("order_id") or "").strip()
            candidate_from_ref = bool(candidate_id)
            if not candidate_id:
                # A failed/uncertain compensation attempt may omit the id from
                # result_ref, while the original locally-built goal still has
                # it.  Parse that value only to tombstone older records; never
                # use a goal-only id as a new cancellation target.
                try:
                    goal = json.loads(str(getattr(task, "goal", "") or ""))
                except (TypeError, ValueError, json.JSONDecodeError):
                    goal = {}
                if isinstance(goal, dict):
                    candidate_id = str(goal.get("order_id") or "").strip()

            outcome = re.sub(
                r"[^0-9A-Za-z\u4e00-\u9fff]+", "",
                str(ref.get("outcome") or "")).lower()
            task_status = str(getattr(task, "status", "") or "").lower()
            if not candidate_id:
                if task_status != DONE or outcome in {"failed", "uncertain"}:
                    # Without a recoverable identity, a newer ambiguous write
                    # makes "the recent order" unsafe to infer.
                    return {}, OrderRef(scope=scope)
                continue
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            if not candidate_from_ref or task_status != DONE:
                continue
            if outcome in {"failed", "uncertain"}:
                continue
            order_status = re.sub(
                r"[^0-9A-Za-z\u4e00-\u9fff]+", "",
                str(ref.get("status") or "")).lower()
            if order_status in {
                    "cancelled", "canceled", "failed", "uncertain",
                    "refunded", "completed", "已取消", "取消成功", "已退款",
            }:
                continue

            slots = {"order_id": candidate_id}
            created = float(getattr(task, "created_at", 0.0) or 0.0)
            if session_id and str(
                    getattr(task, "session_id", "") or "") == session_id:
                return slots, OrderRef(found=True, from_session=True,
                                       created_at=created, scope=scope)
            if fallback is None:
                fallback = (slots, OrderRef(found=True, from_session=False,
                                            created_at=created, scope=scope))
        if not allows_history_fallback(scope):
            # 「取消**刚才**那单」而本会话没下过单 ⇒ **不许拿历史单顶上**。
            # 读路径是把历史当「刚才」陈述，写路径是把历史当「刚才」**执行**。
            return {}, OrderRef(scope=scope)
        return fallback or ({}, OrderRef(scope=scope))

    async def _resolve_order_ref(self, b, args: dict, user_id: str, *,
                                 session_id: str = "",
                                 scope: str = NEUTRAL) -> tuple[dict, OrderRef]:
        """用户说「查一下我的订单」时没有订单号——从账本取他这一单的引用。

        **两种引用都要给**：正常完成的单有 `order_id`；而**下单超时那一单根本没有
        order_id**（响应没回来，账本里只有 `outcome=uncertain`），但幂等键是我们
        自己生成的、商户按它索引。给幂等键，「到底下没下成」才第一次可以核对
        ——此前只能让用户「先去商家处核实，别急着再下一单」。

        **会话范围（Q10，2026-08-16）**：此前本函数只按 `user_id` 取最近一单，
        于是干净 session 里问「刚才那笔订单」会拿到**三天前**那笔——I-021 那个
        被推翻的 P0 就是这么产生的。现在按 `order_ref.reference_scope` 三档：
        `SESSION` 只认本 session（找不到就诚实弃权，**不回落**）、`HISTORY` 取最近、
        `NEUTRAL` 优先本 session 再回落。

        > 同一个进程里，写路径（`_backfill_write_slots`）早就绑了会话，读路径没绑。
        > **「同一件事的两条分支只修了一条」在本仓已经是第三次**（前两次：
        > `wait_confirm`/`wait_slot` 的取消判据、两个分类出口的命名）。

        ⚠ `limit` 从 5 提到 20（与写路径一致）：本 session 那单未必在最近 5 条里，
        而「优先本 session」若因为窗口太小取不到，就退化成了原来的行为。
        """
        # 键名走 arg_map 翻译（麦当劳激活时修，2026-08-11）：本函数在 args 层
        # （已翻译）工作，回填却一直用规划期名 "order_id"——demo-coffee 的商户
        # 参数恰好同名没暴露；真实商户叫 orderId 就会塞进一个它不认识的键。
        oid_key = b.tool.arg_map.get("order_id", "order_id")
        idem_key_name = b.tool.arg_map.get("idempotency_key", "idempotency_key")
        if args.get(oid_key) or args.get(idem_key_name):
            # 用户自己报了引用——范围判据不许拦它，否则历史订单再也查不了。
            return args, OrderRef(found=True, from_session=True, scope=scope)
        if not self.ledger:
            return args, OrderRef(scope=scope)
        try:
            recent = await self.ledger.recent(user_id, kind=LEDGER_KIND, limit=20)
        except Exception as e:
            logger.debug("[mcp] 取最近订单失败（按无引用查）：%s", e)
            return args, OrderRef(scope=scope)
        # 只认**同一商户**的单（跨商户污染修，2026-08-11）：旧账（result_ref 无
        # server 字段的存量单）按 demo-coffee 归属——server 字段是本日新增，此前
        # 只有 demo 商户在写账。
        picked = fallback = None
        for task in recent or []:
            ref = task.result_ref or {}
            owner = ref.get("server") or "demo-coffee"
            if owner != b.server.id:
                continue
            if not ref.get("order_id") and not getattr(task, "idempotency_key", ""):
                continue
            same_session = bool(
                session_id and str(getattr(task, "session_id", "") or "") == session_id)
            if scope == HISTORY:
                picked = (task, ref, same_session)
                break
            if same_session:
                picked = (task, ref, True)
                break
            if fallback is None:
                fallback = (task, ref, False)
        if picked is None and allows_history_fallback(scope):
            picked = fallback
        if picked is None:
            return args, OrderRef(scope=scope)
        task, ref, same_session = picked
        if ref.get("order_id"):
            args[oid_key] = ref["order_id"]
        else:
            args[idem_key_name] = task.idempotency_key
        return args, OrderRef(found=True, from_session=same_session,
                              created_at=float(getattr(task, "created_at", 0.0) or 0.0),
                              scope=scope)

    # ── 可确认写入（生命周期五项）──
    async def _call_write(self, b, intent, ctx, meta) -> AgentResult:
        confirmed = str((meta or {}).get("confirmed", "")).lower() == "true"
        declared = list(b.tool.slots or [])
        slots = {k: v for k, v in (intent.slots or {}).items() if v and k in declared}
        # planner 会把用户原话原样塞进 `order_id`（真栈实测填过字面量「刚才那笔
        # 订单」）。**槽位非空就不走账本回填**，于是会话范围守卫被整个绕过，
        # 那个字符串还会被念进确认话术并拿去调商户 API。
        # 模型输出是不可信输入——**防到真正会被拿去调 API 的那个值**。
        if is_deictic_placeholder(slots.get("order_id")):
            slots.pop("order_id", None)
        write_ref = OrderRef()
        if declared and not slots:
            # 补偿类写操作（取消）用户往往不报订单号——从账本取他最近这一单。
            # 只补**已完成**那一单的 order_id：outcome=uncertain 的单连订单号都没有，
            # 拿它去取消等于对着一个不知道存不存在的单执行写操作。
            slots, write_ref = await self._backfill_write_slots(
                b, declared, ctx,
                scope=reference_scope(str(getattr(intent, "raw_text", "") or "")))
        preview_args = {**b.tool.const_args,
                        **{b.tool.arg_map.get(k, k): v for k, v in slots.items()}}
        preview_args.pop("_owner_user_id", None)
        if (b.tool.idempotency_mode == "upstream" and
                b.tool.idempotency_key_arg):
            preview_args[b.tool.idempotency_key_arg] = "<bridge-generated>"
        missing = self._missing_required_slot(b, preview_args)
        if missing:
            return self._need_slot(missing)
        if declared and not slots:
            # 追问词按缺的那个槽位来。此前恒说「要点什么？」——那是**下单**的词，
            # 取消复用同一条写路径后就会对着「取消订单」问「要点什么？」。
            # 判据不引入领域字面量：拿 capability 声明的槽位名做 key，缺声明就用通用词。
            return self._need_slot(declared[0])
        goal = json.dumps({"tool": b.tool.name, **slots}, ensure_ascii=False,
                          sort_keys=True)
        if not confirmed:
            # 二次确认由中央闸兜底（M0a），这里自己也返回一次——把**要花的钱**说清楚
            # 确认词由工具**自己声明**：用户正要点头同意的就是这句，说错动作
            # （取消订单被问成「准备下单」）比说得笨拙严重得多。缺声明时用中性词，
            # 桥核心不认识「下单」「取消」这些动词。
            tmpl = b.tool.confirm_prompt or "准备执行：{args}，确认吗？"
            # Q10：要动的不是本会话那一单时，**在用户点头之前**就说清它是哪天的。
            # 「取消我的订单」把三天前那笔捞上来仍会走到这里，确认闸只念订单号——
            # 一串数字用户核对不了，一个日期可以。
            stale = (_history_prefix(write_ref.created_at)
                     if write_ref.should_label_as_history else "")
            speech = (self._demo_prefix(b) + stale
                      + tmpl.format(args=self._readable(slots)))
            if b.tool.compensate_policy == "abandon_unpaid":
                speech += " 本单不立即扣款；不支付会由商户自动失效。"
            return AgentResult(status=NEED_CONFIRM, speech=speech)

        user_id = str(getattr(ctx, "user_id", "") or "").strip()
        if not user_id:
            return AgentResult(speech="当前会话没有可验证的用户身份，不能下单。")
        task = await self.ledger.open(
            user_id, getattr(ctx, "session_id", "") or "", self.manifest.agent_id,
            LEDGER_KIND, goal)
        if isinstance(task, Duplicate):
            # 幂等受理：连说两遍不双下单（M2 Ledger 的第二个载体）
            ref = task.existing.result_ref or {}
            if b.tool.pay_url_locator:
                ref = self._sanitize_payment_payload(ref, b.tool.pay_url_locator)
            return AgentResult(
                speech=self._demo_prefix(b) + "这一单已经下过了" +
                (f"，订单号 {ref.get('order_id')}。" if ref.get("order_id") else "。"),
                ui_card=self._card(b, "mcp_order", ref) if ref else None,
                data=ref)

        # 幂等键 = **请求指纹**（user + kind + 归一化 goal），不是 task_id：
        # task_id 每次调用都新，用它当幂等键等于没有幂等——重说一遍就会双扣。
        # 用请求指纹，商户侧才能认出「这是同一单」并复用原订单（demo server 已实现）。
        # 账本 `idempotency_key` 列存的正是同一个值（M2 §9.6），两侧同源。
        idem = idem_key(user_id, LEDGER_KIND, goal)
        args = {**b.tool.const_args,
                **{b.tool.arg_map.get(k, k): v for k, v in slots.items()}}
        args.pop("_owner_user_id", None)
        # Internal owner is derived only from authenticated Context; it is not
        # declared as a planner slot and cannot be supplied/overridden by LLM.
        if self._can_forward_owner(b):
            args["_owner_user_id"] = user_id
        if (b.tool.idempotency_mode == "upstream" and
                b.tool.idempotency_key_arg):
            args[b.tool.idempotency_key_arg] = idem
        if not self._can_forward_owner(b):
            args.pop("_owner_user_id", None)
        try:
            # Until a valid success response is received, a side effect may have
            # reached the merchant without a reliable outcome.  This phase is
            # therefore uncertain and must never trigger an automatic replay.
            res = await b.client.call_tool(b.tool.name, args,
                                           timeout_s=b.tool.timeout_ms / 1000.0,
                                           retry_on_session_loss=False)
            if not isinstance(res, dict) or res.get("ok") is not True:
                raise McpError(f"{b.server.id}: 写响应未确认")
            if not isinstance(res.get("text", ""), str):
                raise McpError(f"{b.server.id}: 写响应 text 非字符串")
            if not isinstance(res.get("data", {}), dict):
                raise McpError(f"{b.server.id}: 写响应 data 非对象")
        except asyncio.CancelledError:
            await self._record_uncertain_write(b, task)
            raise
        except Exception as e:
            return await self._uncertain_write(b, task, e)

        # From here on the merchant outcome is known-success.  UI/payment/local
        # ledger failures may degrade the local experience, but must never turn
        # the merchant order back into an uncertain/FAILED write.
        try:
            raw_order = res.get("data") or {}
            raw_pay_url = (self._dig(raw_order, b.tool.pay_url_locator)
                           if b.tool.pay_url_locator else "")
            if b.tool.pay_url_locator:
                order = self._sanitize_payment_payload(
                    raw_order, b.tool.pay_url_locator)
                merchant_text = self._redact_urls(res.get("text", ""))
            else:
                order = raw_order
                merchant_text = res.get("text", "")
        except Exception as e:
            logger.warning("[mcp:%s] 已成功订单的响应清洗异常：%s",
                           b.server.id, type(e).__name__)
            raw_order, raw_pay_url, order, merchant_text = {}, "", {}, ""

        if order.get("duplicate"):
            speech = (self._demo_prefix(b) + "这一单已经下过了，订单号 "
                      + str(order.get("order_id", "")) + "。")
        else:
            speech = self._demo_prefix(b) + (merchant_text or "下单成功。")

        card = None
        done_started = False
        try:
            if b.tool.pay_url_locator:
                try:
                    pay_card = await self._register_merchant_payment(
                        b, ctx, intent, raw_order, pay_url=raw_pay_url)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(
                        "[mcp:%s] 已成功订单的支付登记异常：%s",
                        b.server.id, type(e).__name__)
                    pay_card = None
                if pay_card is not None:
                    speech += "请扫码完成支付，订单状态以商家为准。"
                    card = pay_card
                else:
                    speech = self._redact_urls(speech).strip()
                    speech += " 支付入口暂不可用，请到商家应用内完成支付。"
                    try:
                        card = self._card(b, "mcp_order", order)
                    except Exception as e:
                        logger.warning("[mcp:%s] 已成功订单的降级卡片构造异常：%s",
                                       b.server.id, type(e).__name__)
            else:
                try:
                    card = self._card(b, "mcp_order", order)
                except Exception as e:
                    logger.warning("[mcp:%s] 已成功订单的卡片构造异常：%s",
                                   b.server.id, type(e).__name__)

            if task:
                # Only sanitized merchant data enters the ledger. Payment URLs
                # are represented by the gateway payment_id/card, never raw refs.
                done_started = True
                synced = await self._record_verified_write(b, task, order)
                if not synced:
                    speech += " 本地记录同步异常，请勿重复下单。"
            logger.info(
                "[mcp:%s] 写操作成功 order=%s amount=%s task=%s duplicate=%s",
                b.server.id, order.get("order_id"), order.get("amount_cents"),
                task.task_id if task else "-", order.get("duplicate", False))
            return AgentResult(speech=speech, ui_card=card, data=order)
        except asyncio.CancelledError:
            if task and not done_started:
                await self._record_verified_write(b, task, order)
            raise

    async def _uncertain_write(self, b, task, error: Exception) -> AgentResult:
        logger.warning("[mcp:%s] 写操作未得到可靠响应（结局不确定）：%s",
                       b.server.id, type(error).__name__)
        await self._record_uncertain_write(b, task)
        return AgentResult(
            speech="下单没有拿到确认结果，可能没成功也可能已经受理。"
                   "先别再下一单——说「查一下我的订单」，我按幂等键跟商户核对。")

    async def _record_uncertain_write(self, b, task) -> None:
        if not task:
            return
        try:
            close = self.ledger.close(
                task.task_id, LEDGER_FAILED,
                result_ref={"error": "mcp_uncertain", "outcome": "uncertain"})
            await asyncio.wait_for(asyncio.shield(close), timeout=2.0)
        except Exception as ledger_error:
            logger.warning("[mcp:%s] 不确定写入落账失败：%s",
                           b.server.id, type(ledger_error).__name__)

    async def _record_verified_write(self, b, task, order: dict) -> bool:
        """Close a known-success write exactly once, preserving cancellation."""
        close_task = asyncio.create_task(self.ledger.close(
            task.task_id, DONE,
            result_ref={**order, "server": b.server.id},
            progress="已下单"))
        try:
            await asyncio.shield(close_task)
            return True
        except asyncio.CancelledError:
            # The merchant result is already known.  Give this *same* DONE close
            # a short chance to finish, then preserve cancellation semantics.
            try:
                await asyncio.wait_for(asyncio.shield(close_task), timeout=2.0)
            except asyncio.CancelledError:
                raise
            except Exception as ledger_error:
                logger.warning("[mcp:%s] 已成功写入落账失败：%s",
                               b.server.id, type(ledger_error).__name__)
            raise
        except Exception as ledger_error:
            logger.warning("[mcp:%s] 已成功写入落账失败：%s",
                           b.server.id, type(ledger_error).__name__)
            return False

    @staticmethod
    def _dig(data: dict, dotted_path: str) -> str:
        """按点路径取值（"payH5Url" / "order.payUrl"）——声明式提取，桥零领域词。"""
        cur = data
        for part in (dotted_path or "").split("."):
            if not isinstance(cur, dict):
                return ""
            cur = cur.get(part)
        return cur if isinstance(cur, str) else ""

    @staticmethod
    def _contains_url_like(value: str) -> bool:
        normalized = value
        for _ in range(8):
            decoded = html.unescape(unquote(normalized)).replace("\\/", "/")
            if decoded == normalized:
                break
            normalized = decoded
        return re.search(
            r"(?i)[a-z][a-z0-9+.-]*\s*:|//",
            normalized) is not None

    @classmethod
    def _redact_urls(cls, value):
        if isinstance(value, dict):
            return {k: cls._redact_urls(v) for k, v in value.items()
                    if not (isinstance(k, str) and cls._contains_url_like(k))}
        if isinstance(value, list):
            return [cls._redact_urls(item) for item in value]
        if isinstance(value, str):
            return "" if cls._contains_url_like(value) else value
        return value

    @classmethod
    def _sanitize_payment_payload(cls, value, locator: str):
        parts = [part for part in (locator or "").split(".") if part]

        def _walk(current, depth=0):
            if isinstance(current, dict):
                out = {}
                for key, item in current.items():
                    if (depth < len(parts) and key == parts[depth] and
                            depth == len(parts) - 1):
                        continue
                    if isinstance(key, str) and cls._contains_url_like(key):
                        continue
                    out[key] = _walk(item, depth + 1)
                return out
            if isinstance(current, list):
                return [_walk(item, depth) for item in current]
            if isinstance(current, str) and cls._contains_url_like(current):
                return ""
            return current

        return _walk(value)

    async def _register_merchant_payment(self, b, ctx, intent,
                                         order: dict, *, pay_url: str = "") -> dict | None:
        """商户支付链接 → 网关登记（审计+展示+过期收口）→ payment_qr 卡。

        - 域名白名单第一层在此（`pay_url_hosts`）：不合白名单**不出链接**（防被
          篡改的响应诱导扫码钓鱼），只提示到商家应用支付；网关 `PAYMENT_EXTERNAL_
          PAY_HOSTS` 是第二层。
        - 登记失败 fail closed：下单事实仍返回，但原始链接不进话术/卡片，用户改去商家应用。
        """
        pay_url = pay_url or self._dig(order, b.tool.pay_url_locator)
        if not pay_url:
            return None
        host = ""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(pay_url)
            host = normalize_hostname(parsed.hostname or "")
            scheme_ok = parsed.scheme.lower() == "https"
        except ValueError:
            scheme_ok = False
        if (not scheme_ok or not b.server.pay_url_hosts or
                host not in b.server.pay_url_hosts):
            logger.warning("[mcp:%s] 支付链接域名不在白名单，拒出码：host=%s",
                           b.server.id, host)
            return None
        try:
            resp = await self._payment.authorize(
                agent_id=self.manifest.agent_id,
                user_id=str(getattr(ctx, "user_id", "") or ""),
                vehicle_id=str(getattr(ctx, "vehicle_id", "") or ""),
                scene=intent.name,
                amount_cents=int(order.get("amount_cents") or 0),
                description=f"{b.server.id} 订单",
                idempotency_key=idem_key(
                    str(getattr(ctx, "user_id", "") or ""), "mcp_pay",
                    str(order.get("order_id") or pay_url)),
                channel=3,   # MERCHANT_HOSTED
                external_pay_url=pay_url,
                external_order_ref=str(order.get("order_id") or ""))
        except Exception as e:
            logger.warning("[mcp:%s] 支付登记异常，拒出原始链接：%s",
                           b.server.id, type(e).__name__)
            raise
        if resp is None:
            logger.warning("[mcp:%s] 支付登记未完成，拒出原始链接", b.server.id)
            return None
        card = {
            "type": "payment_qr",
            "amount": (f"{order['amount_cents'] // 100}."
                       f"{order['amount_cents'] % 100:02d}元"
                       if order.get("amount_cents") else ""),
            "scene": intent.name,
            "qr_content": pay_url,
            "pay_url": pay_url,
            "merchant_note": "订单状态以商家为准",
            "payment_id": getattr(resp, "payment_id", ""),
        }
        if getattr(resp, "qr_svg", ""):
            card["qr_svg"] = resp.qr_svg
        if b.server.demo:
            card["demo"] = True
            card["demo_label"] = "演示商户"
            return attach(card, source=b.server.id, mode="mock",
                          note="演示商户，不产生真实交易")
        return attach(card, source=b.server.id)

    # ── 话术与卡片 ────────────────────────────────────────────────────
    @staticmethod
    def _readable(slots: dict) -> str:
        return "".join(str(v) for v in slots.values() if v) or "这一单"

    @staticmethod
    def _demo_prefix(b) -> str:
        """演示商户在**话术层**也说明白——卡片角标 + _prov 之外的第三重冗余。"""
        return "（演示商户）" if b.server.demo else ""

    def _card(self, b, card_type: str, payload: dict) -> dict:
        reserved = {"_prov", "type", "server", "tool", "merchant",
                    "buttons", "actions", "demo_label"}
        card = {k: v for k, v in (payload or {}).items()
                if k != "demo" and k not in reserved}
        card.update({"type": card_type, "server": b.server.id,
                     "tool": b.tool.name, "merchant": b.server.id})
        if b.server.demo:
            card["demo"] = True
            card["demo_label"] = "演示商户"
        # `_prov` 标记（conventions §9.3）：演示数据永远标出来，**绝不盖真章**——
        # mode=mock + note 说清楚它是演示商户，与运行期 mock 回退同一套诚实口径。
        if b.server.demo:
            return attach(card, source=b.server.id, mode="mock",
                          note="演示商户，不产生真实交易")
        return attach(card, source=b.server.id)
