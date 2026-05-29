"""闲聊 Agent —— 生态(ecosystem) Agent 范本。

演示：调用 LLM Gateway、带会话历史、流式话术(handle_stream)。
作为系统的兜底 fallback（其他 Agent 拒绝/失败时降级到这里）。
"""
from __future__ import annotations
import os

from agents._sdk import BaseAgent, AgentResult

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

_SYSTEM = (
    "你是车载语音助手「小舟」。风格简洁、口语化、温暖、安全。"
    "回答控制在两三句话内，适合驾车时收听；不输出列表、代码或长文。"
)


class ChitchatAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)

    async def _build_messages(self, intent, ctx) -> list[dict]:
        msgs = [{"role": "system", "content": _SYSTEM}]
        for turn in await ctx.history(4):
            msgs.append({"role": turn["role"], "content": turn["text"]})
        msgs.append({"role": "user", "content": intent.raw_text or intent.slots.get("text", "")})
        return msgs

    async def handle(self, intent, ctx, meta) -> AgentResult:
        reply = await self.llm.complete(
            await self._build_messages(intent, ctx), temperature=0.8, max_tokens=200)
        return AgentResult(speech=reply)

    async def handle_stream(self, intent, ctx, meta):
        msgs = await self._build_messages(intent, ctx)
        buf = ""
        async for delta in self.llm.stream(msgs, temperature=0.8, max_tokens=200):
            buf += delta
            yield ("speech", delta)
        yield ("final", AgentResult(speech=buf))
