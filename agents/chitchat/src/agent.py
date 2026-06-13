"""闲聊 Agent —— 生态(ecosystem) Agent 范本。

演示：调用 LLM Gateway、带会话历史、流式话术(handle_stream)。
作为系统的兜底 fallback（其他 Agent 拒绝/失败时降级到这里）。

开放域延迟优化（task 4）：
- 模型分层：闲聊/情绪等开放域默认走"快"模型（低延迟），meta.model_pref=deep 时才用重模型。
- 话术长度：meta.answer_length 控制 max_tokens 与提示，行车场景默认简短。
- 助手昵称：meta.assistant_name 注入 system，呼应 HMI 设置。
这些 meta 由编排器从 HandleRequest.meta 透传（见 orchestrator/cloud/engine.py _build_context）。
"""
from __future__ import annotations
import os

from agents._sdk import BaseAgent, AgentResult

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

# 话术长度 → (max_tokens, 提示语)
_LENGTH = {
    "short": (140, "用一两句话简短回答。"),
    "standard": (220, "回答控制在两三句话内。"),
    "detailed": (440, "可以多说几句，给出更具体的信息，但仍保持口语。"),
}


def _resolve_model(meta: dict) -> str:
    """开放域模型分层：deep→重模型(primary)，其余(fast/auto/未设)→快模型，低延迟。
    返回空串表示用网关默认（含降级链）。"""
    pref = (meta or {}).get("model_pref", "auto")
    if pref == "deep":
        return os.getenv("LLM_MODEL_PRIMARY", "")
    return os.getenv("LLM_MODEL_FAST", os.getenv("LLM_MODEL_FALLBACK", "mimo-v2.5"))


def _length(meta: dict) -> tuple[int, str]:
    return _LENGTH.get((meta or {}).get("answer_length", "standard"), _LENGTH["standard"])


def _system(meta: dict) -> str:
    name = (meta or {}).get("assistant_name") or "小舟"
    _, hint = _length(meta)
    return (
        f"你是车载语音助手「{name}」。风格简洁、口语化、温暖、安全。{hint}"
        "适合驾车时收听；不输出列表、代码或长文。"
        "若用户表达负面情绪，先共情、再轻轻给出建议或陪伴，不要说教。"
    )


class ChitchatAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)

    async def _build_messages(self, intent, ctx, meta) -> list[dict]:
        msgs = [{"role": "system", "content": _system(meta)}]
        for turn in await ctx.history(4):
            msgs.append({"role": turn["role"], "content": turn["text"]})
        msgs.append({"role": "user", "content": intent.raw_text or intent.slots.get("text", "")})
        return msgs

    async def handle(self, intent, ctx, meta) -> AgentResult:
        max_tokens, _ = _length(meta)
        model = _resolve_model(meta)
        msgs = await self._build_messages(intent, ctx, meta)
        reply = await self.llm.complete(msgs, model=model, temperature=0.8, max_tokens=max_tokens)
        if not reply.strip():  # MiMo 偶发空响应：兜底重试一次
            reply = await self.llm.complete(msgs, model=model, temperature=0.9, max_tokens=max_tokens)
        return AgentResult(speech=reply.strip() or "我在听，您可以再说一次。")

    async def handle_stream(self, intent, ctx, meta):
        max_tokens, _ = _length(meta)
        model = _resolve_model(meta)
        msgs = await self._build_messages(intent, ctx, meta)
        buf = ""
        async for delta in self.llm.stream(msgs, model=model, temperature=0.8, max_tokens=max_tokens):
            buf += delta
            yield ("speech", delta)
        if not buf.strip():  # 流式空响应：非流式重试一次，整段补发
            buf = await self.llm.complete(msgs, model=model, temperature=0.9, max_tokens=max_tokens)
            if buf.strip():
                yield ("speech", buf)
        yield ("final", AgentResult(speech=buf.strip() or "我在听，您可以再说一次。"))
