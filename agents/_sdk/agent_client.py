"""AgentClient：供 Agent 在 handle() 内调用其他 Agent。

WS6 协作模式：Agent 经 SDK 直接调用其他 Agent，带护栏防滥用。
护栏：
  1. 调用深度上限（防无限链），MAX_DEPTH=2
  2. 环检测（caller 在调用栈中再次出现 → 拒绝）
  3. 权限不放大（被调权限 ≤ 调用方）
  4. 超时（取被调 manifest.latency_budget_ms）
"""
from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING

import grpc
from cockpit.agent.v1 import agent_pb2, agent_pb2_grpc
from cockpit.common.v1 import common_pb2
from .result import AgentResult

if TYPE_CHECKING:
    from .base import BaseAgent

logger = logging.getLogger("sdk.agent_client")

MAX_DEPTH = 2


class AgentClient:
    """受控的跨 Agent 调用客户端。"""

    def __init__(self, caller: "BaseAgent", call_depth: int = 0,
                 call_stack: list[str] = None, timeout: float = 10):
        self._caller = caller
        self._depth = call_depth
        self._stack = call_stack or []
        self._timeout = timeout

    async def call(self, agent_id: str, intent: str, slots: dict,
                   ctx=None, timeout: float = None) -> AgentResult:
        """调用指定 Agent 的指定意图。

        Args:
            agent_id: 目标 agent_id（kebab-case）
            intent: 意图名（如 "navigation.search_poi"）
            slots: 槽位
            ctx: 上下文（可选，传 session_id 等）
            timeout: 超时秒数（可选，覆盖默认）

        Returns:
            AgentResult
        """
        # 护栏 1：深度上限
        if self._depth >= MAX_DEPTH:
            logger.warning("Call depth exceeded (%d >= %d), rejecting call to %s",
                           self._depth, MAX_DEPTH, agent_id)
            return AgentResult(status="failed", speech="调用深度超限，无法完成协作。")

        # 护栏 2：环检测
        caller_id = self._caller.manifest.agent_id
        if agent_id in self._stack or agent_id == caller_id:
            logger.warning("Circular call detected: %s -> %s (stack: %s)",
                           caller_id, agent_id, self._stack)
            return AgentResult(status="failed", speech="检测到循环调用，已中止。")

        # 解析目标 endpoint（通过环境变量或默认）
        endpoint = self._resolve_endpoint(agent_id)
        if not endpoint:
            return AgentResult(status="failed", speech=f"未找到 Agent: {agent_id}")

        # 构建请求
        req = agent_pb2.ExecuteRequest(
            session_id=ctx.session_id if ctx else "",
            intent=common_pb2.Intent(name=intent, slots=slots, raw_text="", confidence=0.9),
            context=common_pb2.ContextRef(
                session_id=ctx.session_id if ctx else "",
                user_id=ctx.user_id if ctx else "",
                vehicle_id=ctx.vehicle_id if ctx else "",
            ),
            meta={"call_depth": str(self._depth + 1),
                  "call_stack": ",".join(self._stack + [caller_id])},
        )

        # 调用（带超时）
        try:
            ch = grpc.aio.insecure_channel(endpoint)
            stub = agent_pb2_grpc.AgentStub(ch)
            resp = await stub.Execute(req, timeout=timeout or self._timeout)
        except asyncio.TimeoutError:
            logger.warning("Agent %s timed out", agent_id)
            return AgentResult(status="failed", speech=f"Agent {agent_id} 响应超时。")
        except Exception as e:
            logger.warning("Agent %s call failed: %s", agent_id, e)
            return AgentResult(status="failed", speech=f"调用失败: {e}")

        # 转换响应
        status_map = {0: "ok", 1: "need_confirm", 2: "need_slot", 3: "failed", 4: "rejected"}
        actions = [
            {"type": a.type, "payload": dict(a.payload.fields) if a.payload else {},
             "require_confirm": a.require_confirm}
            for a in resp.actions
        ]
        return AgentResult(
            status=status_map.get(resp.status, "failed"),
            speech=resp.speech,
            ui_card=dict(resp.ui_card.fields) if resp.ui_card else None,
            actions=actions,
            follow_up=resp.follow_up,
        )

    def _resolve_endpoint(self, agent_id: str) -> str:
        """解析目标 Agent 的 endpoint。优先从环境变量找，否则用默认端口映射。"""
        import os
        # 格式：<AGENT_ID_UPPER>_ENDPOINT，如 NAVIGATION_ENDPOINT
        env_key = f"{agent_id.upper().replace('-', '_')}_ENDPOINT"
        endpoint = os.getenv(env_key)
        if endpoint:
            return endpoint

        # 默认端口映射（与 conventions.md 一致）
        port_map = {
            "navigation": "50061", "chitchat": "50062",
            "food-ordering": "50063", "parking-payment": "50064",
            "manual-rag": "50065", "trip-planner": "50066",
        }
        port = port_map.get(agent_id)
        if port:
            return f"localhost:{port}"
        return ""

    def fork(self, target_agent_id: str) -> "AgentClient":
        """创建子调用的 AgentClient（深度+1，栈扩展）。"""
        return AgentClient(
            caller=self._caller,
            call_depth=self._depth + 1,
            call_stack=self._stack + [self._caller.manifest.agent_id],
            timeout=self._timeout,
        )
