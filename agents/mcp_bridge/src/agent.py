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
import json
import logging
import os

from agents._sdk import AgentResult, BaseAgent, NEED_CONFIRM, NEED_SLOT
from agents._sdk.ledger import DONE, FAILED as LEDGER_FAILED, Duplicate, idem_key
from agents._sdk.payment_client import PaymentClient
from agents._sdk.provenance import attach
from cockpit.agent.v1 import agent_pb2

from .admission import admit, check_version, load_servers
from .mcp_client import McpError, StdioMcpClient

logger = logging.getLogger("agent.mcp_bridge")

# 缺槽追问词：按**槽位名**索引。此前 NEED_SLOT 恒说「要点什么？」——那是下单的词，
# 取消复用同一条写路径后就会对着「取消订单」问「要点什么？」。
# 槽位名来自 capability 声明（servers.yaml），不是 intent 字面量。
_SLOT_PROMPTS = {
    "item": "要点什么？",
    "order_id": "要操作哪一单？说个订单号，或者先说「查一下我的订单」。",
}

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANIFEST = os.path.join(_HERE, "manifest.yaml")
_SERVERS = os.path.join(_HERE, "servers.yaml")

LEDGER_KIND = "mcp_order"           # 契约登记 conventions §9.6 的 kind 全集
DEMO_PROVIDER = "demo"              # _prov 标记（conventions §9.3）：演示商户永远标出来
_HIDDEN_ADMIN_TOOL = "__e2e.namespace.admin"

PERSONAL_DATA_TARGETS = (
    {
        # The demo MCP server is an external subprocess and owns these maps.
        "id": "mcp_demo_order",
        "storage_variants": ("demo_coffee._ORDERS", "demo_coffee._BY_IDEM"),
    },
)


class _Binding:
    """一个准入通过的工具：intent → (server client, tool spec)。"""

    def __init__(self, server, client, tool):
        self.server = server
        self.client = client
        self.tool = tool


class McpBridgeAgent(BaseAgent):
    def __init__(self, servers_path: str = _SERVERS,
                 payment: PaymentClient | None = None):
        super().__init__(_MANIFEST)
        self._servers_path = servers_path
        self._bindings: dict[str, _Binding] = {}
        self._clients: list = []
        self.rejections: list[str] = []
        # 涉钱走 payment-gateway（§9.9/§9.17）：桥只发登记意图，不持支付凭证
        self._payment = payment or PaymentClient()

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
            for tool, _schema in admitted:
                self._bindings[tool.intent] = _Binding(spec, client, tool)
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

    def _sync_capabilities(self) -> None:
        """把准入的工具写进 manifest.capabilities——注册中心看到的就是这份。"""
        caps = []
        for intent, b in self._bindings.items():
            desc = b.tool.description or f"{b.server.id} 的 {b.tool.name}"
            if b.server.demo:
                desc += "（演示商户，不产生真实交易）"
            caps.append(agent_pb2.Capability(
                intent=intent, description=desc, slots=b.tool.slots,
                examples=b.tool.examples,
                require_confirm=bool(b.tool.require_confirm or b.tool.write),
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
        b = self._bindings.get(intent.name)
        if not b:
            # 准入清单里没有 = 这个能力今天不存在。诚实说，不猜、不静默成功。
            return AgentResult(speech="这个外部服务还没接入。")
        if not b.client.healthy or not b.client.alive:
            return AgentResult(speech=f"{b.server.id} 暂时不可用，稍后再试。")
        return await (self._call_write(b, intent, ctx, meta) if b.tool.write
                      else self._call_read(b, intent, ctx, meta))

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
            response = await binding.client.call_tool(
                _HIDDEN_ADMIN_TOOL,
                {
                    "op": op,
                    "_owner_user_id": owner,
                    "order_id": order_id,
                    "write_tool": binding.tool.name,
                    "compensate_tool": binding.tool.compensate_tool,
                },
                timeout_s=binding.tool.timeout_ms / 1000.0,
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

    # ── 只读 ──
    async def _call_read(self, b, intent, ctx, meta) -> AgentResult:
        try:
            # const_args 打底（声明死的场景选择器）→ 槽位映射值覆盖 → 系统键最后
            args = {**b.tool.const_args,
                    **{b.tool.arg_map.get(k, k): v
                       for k, v in (intent.slots or {}).items() if v}}
            user_id = str(getattr(ctx, "user_id", "") or "").strip()
            if user_id:
                # owner 由已验证 Context 派生，**不是 planner 槽位**（同写路径）：
                # 让 LLM 能指定查谁的订单就是把越权做成了一个可填的字段。
                args["_owner_user_id"] = user_id
                args = await self._resolve_order_ref(b, args, user_id)
            # 端到端取证修（2026-08-11）：declared 槽位一个都没有、账本也回填不到
            # （用户在商户 App 下的单不在我们账本）→ **追问而不是打一发必败请求**
            # ——此前无单号查真实商户，收到「订单号不存在」后把 2836 字 API 文档
            # 念给用户。写路径早有同款追问，读路径漏了。
            # 账本回填的引用（order_id **或幂等键**——超时单只有幂等键）也算「有
            # 引用」：那是 M-D「按幂等键跟商户核对」的既有路径，追问不许打断它。
            declared = list(b.tool.slots or [])
            declared_args = {b.tool.arg_map.get(k, k) for k in declared}
            declared_args.add(b.tool.arg_map.get("idempotency_key",
                                                 "idempotency_key"))
            if declared and not any(args.get(a) for a in declared_args):
                missing = declared[0]
                return AgentResult(
                    status=NEED_SLOT,
                    speech=_SLOT_PROMPTS.get(missing, "还差点信息，说说具体是哪一个？"),
                    missing_slots=[missing])
            res = await b.client.call_tool(b.tool.name, args,
                                           timeout_s=b.tool.timeout_ms / 1000.0)
        except Exception as e:
            logger.warning("[mcp:%s] %s 调用失败：%s", b.server.id, b.tool.name, e)
            # 铁律③：外部源失败诚实说拿不到，绝不改供假数据（R9 契约用 OK 承载话术）
            return AgentResult(speech="这个外部服务暂时拿不到数据，稍后再试。")
        if not res["ok"]:
            speech = await self._readable_speech(b, intent, res, ok=False)
            return AgentResult(speech=speech or "外部服务返回了错误。")
        card = self._card(b, "mcp_result", self._slim_payload(res))
        speech = await self._readable_speech(b, intent, res, ok=True)
        return AgentResult(speech=self._demo_prefix(b) + (speech or "查到了。"),
                           ui_card=card, data=res["data"])

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
        material = (text + "\n" + json.dumps(res.get("data") or {},
                                             ensure_ascii=False))[:3000]
        try:
            summary = (await self.llm.complete([
                {"role": "system", "content":
                 "你是车载助手。根据外部服务的返回内容，用一两句中文口语直接回答"
                 "用户的问题；只答与问题相关的部分，不要念字段名或文档结构；"
                 "内容答不了问题就说明没查到并给一句下一步建议。不要编造数据。"},
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

    async def _backfill_write_slots(self, b, declared: list, ctx) -> dict:
        """补偿类写操作的槽位回填（只回填订单号，只从账本，只取确定完成的那单，
        且只认**同一商户**——跨商户污染修，2026-08-11）。"""
        if "order_id" not in declared or not self.ledger:
            return {}
        user_id = str(getattr(ctx, "user_id", "") or "").strip()
        if not user_id:
            return {}
        try:
            recent = await self.ledger.recent(user_id, kind=LEDGER_KIND, limit=5)
        except Exception as e:
            logger.debug("[mcp] 取最近订单失败（照常追问）：%s", e)
            return {}
        for task in recent or []:
            ref = task.result_ref or {}
            if (ref.get("server") or "demo-coffee") != b.server.id:
                continue
            if ref.get("order_id"):
                return {"order_id": ref["order_id"]}
        return {}

    async def _resolve_order_ref(self, b, args: dict, user_id: str) -> dict:
        """用户说「查一下我的订单」时没有订单号——从账本取他最近这一单的引用。

        **两种引用都要给**：正常完成的单有 `order_id`；而**下单超时那一单根本没有
        order_id**（响应没回来，账本里只有 `outcome=uncertain`），但幂等键是我们
        自己生成的、商户按它索引。给幂等键，「到底下没下成」才第一次可以核对
        ——此前只能让用户「先去商家处核实，别急着再下一单」。
        """
        # 键名走 arg_map 翻译（麦当劳激活时修，2026-08-11）：本函数在 args 层
        # （已翻译）工作，回填却一直用规划期名 "order_id"——demo-coffee 的商户
        # 参数恰好同名没暴露；真实商户叫 orderId 就会塞进一个它不认识的键。
        oid_key = b.tool.arg_map.get("order_id", "order_id")
        idem_key_name = b.tool.arg_map.get("idempotency_key", "idempotency_key")
        if not self.ledger or args.get(oid_key) or args.get(idem_key_name):
            return args
        try:
            recent = await self.ledger.recent(user_id, kind=LEDGER_KIND, limit=5)
        except Exception as e:
            logger.debug("[mcp] 取最近订单失败（按无引用查）：%s", e)
            return args
        # 只认**同一商户**的单（跨商户污染修，2026-08-11）：旧账（result_ref 无
        # server 字段的存量单）按 demo-coffee 归属——server 字段是本日新增，此前
        # 只有 demo 商户在写账。
        for task in recent or []:
            ref = task.result_ref or {}
            owner = ref.get("server") or "demo-coffee"
            if owner != b.server.id:
                continue
            if ref.get("order_id"):
                args[oid_key] = ref["order_id"]
            elif getattr(task, "idempotency_key", ""):
                args[idem_key_name] = task.idempotency_key
            break
        return args

    # ── 可确认写入（生命周期五项）──
    async def _call_write(self, b, intent, ctx, meta) -> AgentResult:
        confirmed = str((meta or {}).get("confirmed", "")).lower() == "true"
        declared = list(b.tool.slots or [])
        slots = {k: v for k, v in (intent.slots or {}).items() if v and k in declared}
        if declared and not slots:
            # 补偿类写操作（取消）用户往往不报订单号——从账本取他最近这一单。
            # 只补**已完成**那一单的 order_id：outcome=uncertain 的单连订单号都没有，
            # 拿它去取消等于对着一个不知道存不存在的单执行写操作。
            slots = await self._backfill_write_slots(b, declared, ctx)
        if declared and not slots:
            # 追问词按缺的那个槽位来。此前恒说「要点什么？」——那是**下单**的词，
            # 取消复用同一条写路径后就会对着「取消订单」问「要点什么？」。
            # 判据不引入领域字面量：拿 capability 声明的槽位名做 key，缺声明就用通用词。
            missing = declared[0]
            return AgentResult(status=NEED_SLOT,
                               speech=_SLOT_PROMPTS.get(missing, "还差点信息，说说具体是哪一个？"),
                               missing_slots=[missing])
        goal = json.dumps({"tool": b.tool.name, **slots}, ensure_ascii=False,
                          sort_keys=True)
        if not confirmed:
            # 二次确认由中央闸兜底（M0a），这里自己也返回一次——把**要花的钱**说清楚
            # 确认词由工具**自己声明**：用户正要点头同意的就是这句，说错动作
            # （取消订单被问成「准备下单」）比说得笨拙严重得多。缺声明时用中性词，
            # 桥核心不认识「下单」「取消」这些动词。
            tmpl = b.tool.confirm_prompt or "准备执行：{args}，确认吗？"
            return AgentResult(
                status=NEED_CONFIRM,
                speech=self._demo_prefix(b) + tmpl.format(args=self._readable(slots)))

        user_id = str(getattr(ctx, "user_id", "") or "").strip()
        if not user_id:
            return AgentResult(speech="当前会话没有可验证的用户身份，不能下单。")
        task = await self.ledger.open(
            user_id, getattr(ctx, "session_id", "") or "", self.manifest.agent_id,
            LEDGER_KIND, goal)
        if isinstance(task, Duplicate):
            # 幂等受理：连说两遍不双下单（M2 Ledger 的第二个载体）
            ref = task.existing.result_ref or {}
            return AgentResult(
                speech=self._demo_prefix(b) + "这一单已经下过了" +
                (f"，订单号 {ref.get('order_id')}。" if ref.get("order_id") else "。"),
                ui_card=self._card(b, "mcp_order", ref) if ref else None)

        # 幂等键 = **请求指纹**（user + kind + 归一化 goal），不是 task_id：
        # task_id 每次调用都新，用它当幂等键等于没有幂等——重说一遍就会双扣。
        # 用请求指纹，商户侧才能认出「这是同一单」并复用原订单（demo server 已实现）。
        # 账本 `idempotency_key` 列存的正是同一个值（M2 §9.6），两侧同源。
        idem = idem_key(user_id, LEDGER_KIND, goal)
        args = {**b.tool.const_args,
                **{b.tool.arg_map.get(k, k): v for k, v in slots.items()}}
        # Internal owner is derived only from authenticated Context; it is not
        # declared as a planner slot and cannot be supplied/overridden by LLM.
        args["_owner_user_id"] = user_id
        if b.tool.idempotency_key_arg:
            args[b.tool.idempotency_key_arg] = idem
        try:
            res = await b.client.call_tool(b.tool.name, args,
                                           timeout_s=b.tool.timeout_ms / 1000.0)
        except asyncio.TimeoutError:
            logger.warning("[mcp:%s] 写操作超时（结局不确定）", b.server.id)
            if task:
                # 账本状态机没有 uncertain 态（加终态动全仓消费方，本轮不动）；
                # 先在 result_ref 落 outcome=uncertain——将来接订单查询入口时按它
                # 回答，绝不能照 failed 状态说「上次下单失败了」（可能商户已受理）。
                await self.ledger.close(task.task_id, LEDGER_FAILED,
                                        result_ref={"error": "timeout",
                                                    "outcome": "uncertain"})
            # 超时不等于没下单：诚实说「不确定」，并给出**真的存在**的核对入口。
            # 这句话的历史值得留着：验收发现它当时承诺「说『查一下我的订单』我帮你
            # 核对」而入口并不存在（准入清单只有 menu/order），于是先改成不承诺；
            # M-D 把 order.get 接进清单后才把承诺加回来。**先有能力再有话术**，
            # 反过来就是把不确定包装成「有办法查清楚」。
            # 核对靠的是幂等键而不是订单号——超时这一单我们根本没拿到订单号。
            return AgentResult(
                speech="下单没有拿到确认结果，可能没成功也可能已经受理。"
                       "先别再下一单——说「查一下我的订单」，我按幂等键跟商户核对。")
        except Exception as e:
            # 非超时异常（子进程没起/协议错）= 确定没发出去，按失败说，不装不确定
            logger.warning("[mcp:%s] 写操作失败：%s", b.server.id, e)
            if task:
                await self.ledger.close(task.task_id, LEDGER_FAILED,
                                        result_ref={"error": str(e)[:200]})
            return AgentResult(speech="下单没成功，这一单没有发出去，请稍后再试。")
        if not res["ok"]:
            if task:
                await self.ledger.close(task.task_id, LEDGER_FAILED,
                                        result_ref={"error": res["text"][:200]})
            return AgentResult(speech=res["text"] or "下单失败了。")

        order = res["data"] or {}
        if task:
            # result_ref 记 server 归属（2026-08-11 端到端抓的跨商户污染）：查单
            # 回填只认**同一商户**的单——拿 demo 咖啡的单号去查麦当劳，商户端
            # 「订单号不存在」的报错曾把这层错配伪装成「用户没有订单」。
            await self.ledger.close(task.task_id, DONE,
                                    result_ref={**order, "server": b.server.id},
                                    progress="已下单")
        # 审计：server/tool/订单号/金额/账本 id 进结构化日志（参数摘要按 obs 既有脱敏口径）
        logger.info("[mcp:%s] 写操作成功 order=%s amount=%s task=%s duplicate=%s",
                    b.server.id, order.get("order_id"), order.get("amount_cents"),
                    task.task_id if task else "-", order.get("duplicate", False))
        if order.get("duplicate"):
            # 商户认出同一幂等键 → 复用原单。这就是「连说两遍不双下单」的最终裁决点：
            # 我们的账本管受理，**商户管钱**，两侧都不该重复。
            speech = (self._demo_prefix(b) + "这一单已经下过了，订单号 "
                      + str(order.get("order_id", "")) + "。")
        else:
            speech = self._demo_prefix(b) + (res["text"] or "下单成功。")
        # 支付链接闭环（§9.9 批 3）：工具声明了 pay_url_locator 且响应里真有链接
        # → 经 payment-gateway 登记 merchant_hosted 会话并出付款码卡
        if b.tool.pay_url_locator:
            pay_card = await self._register_merchant_payment(
                b, ctx, intent, order)
            if pay_card is not None:
                speech += "请扫码完成支付，订单状态以商家为准。"
                return AgentResult(speech=speech, ui_card=pay_card, data=order)
        return AgentResult(speech=speech,
                           ui_card=self._card(b, "mcp_order", order), data=order)

    @staticmethod
    def _dig(data: dict, dotted_path: str) -> str:
        """按点路径取值（"payH5Url" / "order.payUrl"）——声明式提取，桥零领域词。"""
        cur = data
        for part in (dotted_path or "").split("."):
            if not isinstance(cur, dict):
                return ""
            cur = cur.get(part)
        return cur if isinstance(cur, str) else ""

    async def _register_merchant_payment(self, b, ctx, intent,
                                         order: dict) -> dict | None:
        """商户支付链接 → 网关登记（审计+展示+过期收口）→ payment_qr 卡。

        - 域名白名单第一层在此（`pay_url_hosts`）：不合白名单**不出链接**（防被
          篡改的响应诱导扫码钓鱼），只提示到商家应用支付；网关 `PAYMENT_EXTERNAL_
          PAY_HOSTS` 是第二层。
        - **登记失败不阻断出卡**：下单是既成事实，缺的是登记不是能力——打 warning
          照出卡带 pay_url（qr_svg 空由 HMI 回落成链接文本）。
        """
        pay_url = self._dig(order, b.tool.pay_url_locator)
        if not pay_url:
            return None
        host = ""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(pay_url)
            host = (parsed.hostname or "").lower()
            scheme_ok = parsed.scheme == "https"
        except ValueError:
            scheme_ok = False
        if not scheme_ok or (b.server.pay_url_hosts and
                             host not in b.server.pay_url_hosts):
            logger.warning("[mcp:%s] 支付链接域名不在白名单，拒出码：host=%s",
                           b.server.id, host)
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
        }
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
        except Exception as e:      # 登记通道的任何意外都不阻断出卡
            resp = None
            logger.warning("[mcp:%s] 支付登记异常（照出卡）：%s", b.server.id, e)
        if resp is not None:
            card["payment_id"] = resp.payment_id
            if getattr(resp, "qr_svg", ""):
                card["qr_svg"] = resp.qr_svg
        else:
            logger.warning("[mcp:%s] 支付登记未完成（网关不可用？）——卡片带原始链接",
                           b.server.id)
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
        card = {"type": card_type, "server": b.server.id, "tool": b.tool.name,
                **{k: v for k, v in (payload or {}).items() if k != "demo"}}
        if b.server.demo:
            card["demo"] = True
            card["demo_label"] = "演示商户"
        # `_prov` 标记（conventions §9.3）：演示数据永远标出来，**绝不盖真章**——
        # mode=mock + note 说清楚它是演示商户，与运行期 mock 回退同一套诚实口径。
        if b.server.demo:
            return attach(card, source=b.server.id, mode="mock",
                          note="演示商户，不产生真实交易")
        return attach(card, source=b.server.id)
