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
from agents._sdk.provenance import attach
from cockpit.agent.v1 import agent_pb2

from .admission import admit, check_version, load_servers
from .mcp_client import McpError, StdioMcpClient

logger = logging.getLogger("agent.mcp_bridge")

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MANIFEST = os.path.join(_HERE, "manifest.yaml")
_SERVERS = os.path.join(_HERE, "servers.yaml")

LEDGER_KIND = "mcp_order"           # 契约登记 conventions §9.6 的 kind 全集
DEMO_PROVIDER = "demo"              # _prov 标记（conventions §9.3）：演示商户永远标出来


class _Binding:
    """一个准入通过的工具：intent → (server client, tool spec)。"""

    def __init__(self, server, client, tool):
        self.server = server
        self.client = client
        self.tool = tool


class McpBridgeAgent(BaseAgent):
    def __init__(self, servers_path: str = _SERVERS):
        super().__init__(_MANIFEST)
        self._servers_path = servers_path
        self._bindings: dict[str, _Binding] = {}
        self._clients: list[StdioMcpClient] = []
        self.rejections: list[str] = []

    # ── 启动期准入 ────────────────────────────────────────────────────
    async def bootstrap(self) -> None:
        """启动 MCP server → 握手 → 校验版本/白名单/schema → 合成 capability。

        **在 `serve()` 之前调用**（注册发生在 serve 里）。任何一台 server 起不来 /
        版本对不上，只让它自己的工具缺席，桥照常服务其余——但**绝不静默降级成假数据**。
        """
        for spec in load_servers(self._servers_path):
            client = StdioMcpClient(spec.id, spec.command,
                                    timeout_s=spec.startup_timeout_ms / 1000.0)
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

    # ── 只读 ──
    async def _call_read(self, b, intent, ctx, meta) -> AgentResult:
        try:
            args = {b.tool.arg_map.get(k, k): v
                    for k, v in (intent.slots or {}).items() if v}
            res = await b.client.call_tool(b.tool.name, args,
                                           timeout_s=b.tool.timeout_ms / 1000.0)
        except Exception as e:
            logger.warning("[mcp:%s] %s 调用失败：%s", b.server.id, b.tool.name, e)
            # 铁律③：外部源失败诚实说拿不到，绝不改供假数据（R9 契约用 OK 承载话术）
            return AgentResult(speech="这个外部服务暂时拿不到数据，稍后再试。")
        if not res["ok"]:
            return AgentResult(speech=res["text"] or "外部服务返回了错误。")
        card = self._card(b, "mcp_result", {"text": res["text"], **res["data"]})
        return AgentResult(speech=self._demo_prefix(b) + (res["text"] or "查到了。"),
                           ui_card=card, data=res["data"])

    # ── 可确认写入（生命周期五项）──
    async def _call_write(self, b, intent, ctx, meta) -> AgentResult:
        confirmed = str((meta or {}).get("confirmed", "")).lower() == "true"
        declared = list(b.tool.slots or [])
        slots = {k: v for k, v in (intent.slots or {}).items() if v and k in declared}
        if declared and not slots:
            return AgentResult(status=NEED_SLOT, speech="要点什么？",
                               missing_slots=declared[:1])
        goal = json.dumps({"tool": b.tool.name, **slots}, ensure_ascii=False,
                          sort_keys=True)
        if not confirmed:
            # 二次确认由中央闸兜底（M0a），这里自己也返回一次——把**要花的钱**说清楚
            return AgentResult(
                status=NEED_CONFIRM,
                speech=self._demo_prefix(b) + f"准备下单：{self._readable(slots)}，确认吗？")

        user_id = getattr(ctx, "user_id", "") or "u1"
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
        args = {b.tool.arg_map.get(k, k): v for k, v in slots.items()}
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
            # 超时不等于没下单：诚实说「不确定」。验收修正：此前话术承诺「说『查一下
            # 我的订单』我帮你核对」，但订单查询入口并不存在（准入清单只有 menu/order）
            # ——把不确定包装成「有办法查清楚」比不说更伤信任。改为不承诺不存在的能力。
            return AgentResult(
                speech="下单没有拿到确认结果，可能没成功也可能已经受理。"
                       "为避免重复下单，请先在商家处核实，别急着再下一单。")
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
            await self.ledger.close(task.task_id, DONE, result_ref=order,
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
        return AgentResult(speech=speech,
                           ui_card=self._card(b, "mcp_order", order), data=order)

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
