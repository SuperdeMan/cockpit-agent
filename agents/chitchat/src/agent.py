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
import re

from agents._sdk import BaseAgent, AgentResult
from agents._sdk.grounding import shanghai_now

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

# 时效兜底（2026-07-12 mode-routing 设计 P1-2）：LLM 判定「必须联网才能正确回答」时只输出
# 该标记；agent 解析后零播报、经通用 escalate 协议改派 info.search（engine 有界一跳消费）。
_SEARCH_MARK = "<search>"
_SEARCH_MARK_RE = re.compile(r"^\s*<search>\s*(.{1,50}?)\s*</search>", re.S)


def _parse_search_mark(text: str) -> str:
    """整段回复是否以 <search>查询词</search> 开头；是则返回查询词，否则空串。"""
    m = _SEARCH_MARK_RE.match(text or "")
    return m.group(1).strip() if m else ""


def _escalate_result(query: str) -> AgentResult:
    """零播报 + 通用改派声明（协议登记见 docs/conventions.md「Agent→编排结果保留键」）。"""
    return AgentResult(speech="", data={"_escalate": {
        "intent": "info.search", "slots": {"query": query},
        "reason": "needs_realtime"}})

# 话术长度 → (max_tokens, 提示语)
_LENGTH = {
    "short": (140, "用一两句话简短回答。"),
    "standard": (220, "回答控制在两三句话内。"),
    "detailed": (440, "可以多说几句，给出更具体的信息，但仍保持口语。"),
}


def _resolve_model(meta: dict, slots: dict | None = None) -> str:
    """开放域模型分层：deep→重模型档位（primary），其余(fast/auto/未设)→快模型档位，低延迟。

    返回的是**档位哨兵**而非具体模型名（``""``=primary、``"@fast"``=fast）——由 llm-gateway 按当前
    active provider 解析成该厂商的具体模型（见 llm-gateway/llm_runtime.py::resolve_models）。这样多
    LLM 源切换厂商时，不会把某家的模型名（如 mimo-v2.5）误发给另一家（如 DeepSeek）而报错。

    slots.depth：Planner 按问题类型下发（manifest desc 引导知识/解释类传 deep），优先于
    会话级 meta.model_pref——寒暄走快模型省延迟，科普/解释用更强模型保质量。"""
    pref = (slots or {}).get("depth") or (meta or {}).get("model_pref", "auto")
    return "" if pref == "deep" else "@fast"


def _length(meta: dict) -> tuple[int, str]:
    return _LENGTH.get((meta or {}).get("answer_length", "standard"), _LENGTH["standard"])


def _system(meta: dict) -> str:
    name = (meta or {}).get("assistant_name") or "小舟"
    _, hint = _length(meta)
    return (
        f"你是车载语音助手「{name}」。今天是{shanghai_now():%Y年%m月%d日}。"
        f"风格简洁、口语化、温暖、安全。{hint}"
        "适合驾车时收听；不输出列表、代码或长文。"
        "若用户表达负面情绪，先共情、再轻轻给出建议或陪伴，不要说教。"
        "涉及实时或近期事实时，如果你不确定就明说无法确认并建议联网查询，绝不编造。"
        "如果不联网获取实时信息（今天的新闻、比分、价格、天气实况、近期事件等）就无法"
        "正确回答，就只输出 <search>不超过20字的中文搜索词</search>，不要输出任何其他文字；"
        "闲聊、情绪陪伴和不随时间变化的常识照常直接回答，不要滥用该标记。"
    )


class ChitchatAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)

    async def _memory_context(self, intent, ctx) -> str:
        """召回与本问相关的个人信息/偏好（如宠物名、口味），注入 system 供自然作答。
        失败/无 user_id 返回空，不阻塞。"""
        query = intent.raw_text or intent.slots.get("text", "")
        if not query:
            return ""
        try:
            # 含 episodic：个人事实（宠物/家人名）抽取时可能被归为 semantic 或 episodic（叙事式输入常落
            # episodic），只召 semantic 会漏「我的猫叫什么」这类问题。语义排序 + top_k 保证不相关片段不被注入。
            mems = await ctx.recall(query, kinds=["semantic", "episodic"], top_k=4, min_confidence=0.5)
        except Exception:
            return ""
        lines = [f"- {m.get('text', '')}" for m in mems if m.get("text")]
        if not lines:
            return ""
        return ("已知用户信息（仅在与问题相关时自然引用，勿生硬复述、勿暴露这是系统记忆）：\n"
                + "\n".join(lines))

    async def _build_messages(self, intent, ctx, meta) -> list[dict]:
        sys = _system(meta)
        mem_ctx = await self._memory_context(intent, ctx)
        if mem_ctx:
            sys = f"{sys}\n\n{mem_ctx}"
        msgs = [{"role": "system", "content": sys}]
        for turn in await ctx.history(4):
            msgs.append({"role": turn["role"], "content": turn["text"]})
        msgs.append({"role": "user", "content": intent.raw_text or intent.slots.get("text", "")})
        return msgs

    async def handle(self, intent, ctx, meta) -> AgentResult:
        max_tokens, _ = _length(meta)
        model = _resolve_model(meta, intent.slots)
        msgs = await self._build_messages(intent, ctx, meta)
        reply = await self.llm.complete(msgs, model=model, temperature=0.8, max_tokens=max_tokens)
        if not reply.strip():  # MiMo 偶发空响应：兜底重试一次
            reply = await self.llm.complete(msgs, model=model, temperature=0.9, max_tokens=max_tokens)
        q = _parse_search_mark(reply)
        if q:                   # 时效兜底：需要实时信息 → 零播报改派 info.search
            return _escalate_result(q)
        return AgentResult(speech=reply.strip() or "我在听，您可以再说一次。")

    async def handle_stream(self, intent, ctx, meta):
        """流式直答。头部缓冲：在确定回复不是 <search> 改派标记前不放流任何 delta——
        escalate 的前提是「零播报」（engine 端 streamed=True 会忽略改派，双保险）。
        判定窗口 ≤ len("<search>")+空白，普通回复只延迟一个包级别，无感。"""
        max_tokens, _ = _length(meta)
        model = _resolve_model(meta, intent.slots)
        msgs = await self._build_messages(intent, ctx, meta)
        buf = ""
        held = ""
        mode = "probe"          # probe=判定中 | stream=正常放流 | silent=标记确认，静默缓冲
        async for delta in self.llm.stream(msgs, model=model, temperature=0.8, max_tokens=max_tokens):
            buf += delta
            if mode == "stream":
                yield ("speech", delta)
                continue
            held += delta
            probe = held.lstrip()
            if mode == "probe":
                if not probe:
                    continue
                if probe.startswith(_SEARCH_MARK):
                    mode = "silent"                    # 标记确认：静默缓冲到流结束
                elif _SEARCH_MARK.startswith(probe):
                    continue                           # 仍是 "<sea" 类前缀，继续观望
                else:
                    mode = "stream"                    # 不是标记：一次性放流缓冲
                    yield ("speech", held)
                    held = ""
        if mode == "silent":
            q = _parse_search_mark(buf)
            if q:
                yield ("final", _escalate_result(q))
                return
            # 形如标记但残缺（未闭合等）：剥标签当普通话术，不丢内容
            buf = re.sub(r"</?search>", "", buf).strip()
            if buf:
                yield ("speech", buf)
        elif mode == "probe" and held.strip():
            yield ("speech", held)                     # 极短回复（如「好」）整段放流
        if not buf.strip():  # 流式空响应：非流式重试一次，整段补发
            buf = await self.llm.complete(msgs, model=model, temperature=0.9, max_tokens=max_tokens)
            q = _parse_search_mark(buf)
            if q:
                yield ("final", _escalate_result(q))
                return
            if buf.strip():
                yield ("speech", buf)
        yield ("final", AgentResult(speech=buf.strip() or "我在听，您可以再说一次。"))
