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
import json
import logging
import os
import re
from datetime import datetime

from agents._sdk import BaseAgent, AgentResult
from agents._sdk.grounding import shanghai_now
from .audit import audit_answer, is_execution_audit_question
from agents._sdk.safety_signal import (DRIVER_STATE_ADVICE, alert_advice,
                                       alert_level, alert_signal, driver_state)

logger = logging.getLogger("agent.chitchat")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

# ── 钟点/日期/星期确定性直答（badcase 2026-07-15：「现在几点了」被 LLM 编造时刻）──
# 系统自己持有墙钟，这类问题不该让 LLM 回答——prompt 锚只有日期时，模型会编一个像真的
# 时刻（实测 14:25 答 14:43 / 10:06，两采样全错）。正则须**占据整句**（去礼貌前缀与
# 语气尾词后锚定 ^$），防劫持「明天几点有比赛」「几点提醒我」这类含时间词的其他意图。
_Q_PREFIX_RE = re.compile(r"^(请问|问一下|问下|那|哎|诶|嘿)+")
_Q_SUFFIX = " 呀啊呢哦吧了嘛么？?。！!，,"
_CLOCK_RE = re.compile(r"^(现在|当前)?(是)?几点(钟)?$|^(现在|当前)(的)?(是)?(什么)?时间(是多少|是几点)?$")
_DATE_RE = re.compile(r"^今天(是)?(几号|多少号|几月几号|几月几日|什么日期)$")
_WEEK_RE = re.compile(r"^今天(是)?(星期几|周几|礼拜几)$")
_WEEKDAY = "一二三四五六日"


def _spoken_time(now: datetime) -> str:
    """口语化时刻：「下午2点27分」（0 分说「整」；0 点按惯例说凌晨12点）。"""
    h, m = now.hour, now.minute
    seg = ("凌晨" if h < 5 else "早上" if h < 9 else "上午" if h < 12
           else "中午" if h == 12 else "下午" if h < 18 else "晚上")
    h12 = h % 12 or 12
    return f"{seg}{h12}点" + ("整" if m == 0 else f"{m}分")


# ── 身份问句确定性直答（真机 2026-07-27：换人说话后仍答上一个人的名字）──────────
# 同墙钟一族：**系统自己持有的事实不交给 LLM 答**。声纹已经告诉我们这轮是谁，
# 而车里只有一个会话、说话人会换——上一轮刚管别人叫过「阿灵」，这一轮 system 明写着泓舟，
# 模型照样答「你是阿灵呀，刚才不是说了嘛」。**加强提示词无效**（实测两个方向各两次全错）：
# 对话历史里的称呼比 system 提示更近、更像既成事实，靠改 prompt 是在跟采样赌。
# 正则同样须**占据整句**（去礼貌前缀与语气尾词后锚 ^$），防劫持「我是谁的乘客」这类。
_WHOAMI_RE = re.compile(
    r"^(你知道|你还记得|还记得|你猜)?(我是谁|我叫什么(名字)?|我的名字(是什么|叫什么)?|"
    r"知道我是谁)(吗|嘛)?$")


def _identity_answer(text: str, meta: dict) -> str:
    """「我是谁」→ 按声纹认定的乘员称呼直答；非此类或未识别出人返回空串（走 LLM）。"""
    who = (meta or {}).get("occupant_name", "").strip()
    if not who:
        return ""      # 认不出就别硬答——诚实降级由 LLM 按 system 里没有名字来处理
    t = _Q_PREFIX_RE.sub("", (text or "").strip()).strip(_Q_SUFFIX)
    return f"你是{who}呀。" if t and _WHOAMI_RE.match(t) else ""


def _clock_answer(text: str) -> str:
    """纯钟点/日期/星期问句 → 按系统墙钟直答；非此类返回空串（走 LLM）。"""
    t = _Q_PREFIX_RE.sub("", (text or "").strip()).strip(_Q_SUFFIX)
    if not t:
        return ""
    now = shanghai_now()
    if _CLOCK_RE.match(t):
        return f"现在是{_spoken_time(now)}。"
    if _DATE_RE.match(t):
        return f"今天是{now.year}年{now.month}月{now.day}日，星期{_WEEKDAY[now.weekday()]}。"
    if _WEEK_RE.match(t):
        return f"今天星期{_WEEKDAY[now.weekday()]}，{now.month}月{now.day}日。"
    return ""

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


def _safety_answer(text: str) -> tuple[str, dict]:
    """安全信号的**确定性**直答。返回 (话术, 告警声明)；不是安全问题返回 ("", {})。

    与 `_clock_answer` / `_identity_answer` 同一形态、同一理由：
    **系统持有的判据绝不交给 LLM 答**。这里持有的是「这句话里有没有安全信号」。

    为什么 chitchat 需要这个：安全问题的落域本身有方差——「红色机油灯亮了怎么办」
    在真栈三次取样里分别落到 manual-rag、闲聊和澄清。**加固了 manual-rag 与
    road-safety 之后，兜底这条路就成了唯一没有护栏的入口**，而它恰恰是
    QA 轮答出「收到，那不提醒也不停车」的那一条（迷你集 SF4）。

    判据取自 `agents/_sdk/safety_signal`（唯一实现，三个 Agent 共用）。
    """
    state = driver_state(text)
    if state:
        spec = DRIVER_STATE_ADVICE[state]
        return spec["speech"], {"level": spec["level"], "signal": spec["signal"]}
    level = alert_level(text)
    if level:
        return (alert_advice(level),
                {"level": level, "signal": alert_signal(text) or "车辆告警"})
    return "", {}


def _active_alert(meta: dict) -> dict:
    """编排下发的会话告警（`meta.focus_safety_alert`）。解析失败一律当没有。"""
    raw = (meta or {}).get("focus_safety_alert")
    if not raw:
        return {}
    try:
        alert = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    if not isinstance(alert, dict) or alert.get("level") not in ("critical", "amber"):
        return {}
    return alert


def _system(meta: dict) -> str:
    name = (meta or {}).get("assistant_name") or "小舟"
    # M4 P4：声纹识别出的说话人称呼。**没有它「你知道我是谁」只能靠语义召回碰运气**——
    # 而这类身份问句恰恰是用户验证声纹是否生效的第一句话，必须确定性答得上。
    # 只影响称呼与口吻，不参与任何权限判定（声纹不作鉴权因子，RFC §6.1）。
    who = (meta or {}).get("occupant_name", "").strip()
    _, hint = _length(meta)
    now = shanghai_now()
    # 锚点带星期与时刻：纯钟点问句已被 _clock_answer 确定性拦下，这里供「该吃午饭了吗」
    # 这类时间相对话题参考——没有时刻锚模型会编一个像真的（badcase 2026-07-15）。
    return (
        f"你是车载语音助手「{name}」。今天是{now:%Y年%m月%d日}"
        f"（星期{_WEEKDAY[now.weekday()]}），现在{now:%H:%M}。"
        # **必须压过对话历史**（2026-07-27 真机）：车里只有一个会话而说话人会换。
        # 上一轮刚管别人叫过「阿灵」，这一轮声纹已认出是泓舟、system 也注了泓舟，
        # 模型照样答「你是阿灵呀，刚才不是说了嘛」——**历史里的称呼比 system 提示更近、更像事实**。
        # 光说「他是谁」不够，得显式告诉模型「历史里的称呼可能是别人」。
        + (f"当前跟你说话的是「{who}」。**车内会换人说话**：上文出现过的其他称呼指的是别人，"
           f"不是现在这位——一律以「{who}」为准，别沿用上文的称呼。"
           f"他问「我是谁/你知道我是谁吗」时直接叫出「{who}」，别说不知道；"
           "平时不必每句都称呼。" if who else "")
        + f"风格简洁、口语化、温暖、安全。{hint}"
        "适合驾车时收听；不输出列表、代码或长文。"
        "若用户表达负面情绪，先共情、再轻轻给出建议或陪伴，不要说教。"
        "涉及实时或近期事实时，如果你不确定就明说无法确认并建议联网查询，绝不编造。"
        "如果不联网获取实时信息（今天的新闻、比分、价格、天气实况、近期事件等）就无法"
        "正确回答，就只输出 <search>不超过20字的中文搜索词</search>，不要输出任何其他文字；"
        "闲聊、情绪陪伴和不随时间变化的常识照常直接回答，不要滥用该标记。"
        # 防编造执行结果（demo-mkemhn 3650e2b5/2fd09d52）：掉到本 Agent 的轮里，模型
        # 曾凭对话历史生成「已为您找到 10 家门店，请选择其中一家」「请确认：X店，
        # 应付 10.90 元」——它没有任何检索/下单能力，这些话对用户全是假的。
        "你自己没有查询、下单、订座、支付这类执行能力：绝不声称「已为您找到/已选定/"
        "已下单」，绝不输出「请确认」开头的交易确认语，也不要替系统承诺接下来会执行"
        "什么。用户在推进这类操作时，直接建议他说出明确指令（如「查询附近的瑞幸咖啡」），"
        "由系统的对应能力接手。"
        # Q9：会话里有未解除的安全告警时，它是**这一轮回答的前提**，不是背景。
        # QA 轮实测，用户说「别提醒我，继续开就行」时兜底答了「收到，那不提醒也不停车」
        # ——**用户可以拒绝被提醒，系统不可以跟着改口说不用停车**。
        + (f"⚠本次会话里还有未解除的安全告警：{_active_alert(meta).get('signal')}。"
           "无论用户问什么、或明确表示不想被提醒，都不得表示可以继续危险驾驶、"
           "不得撤回或弱化停车/休息建议；可以不再重复啰嗦，但立场不改。"
           if _active_alert(meta) else "")
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

    async def _deterministic_reply(self, text, ctx, meta):
        """所有**零 LLM 直答**的唯一实现。返回 `AgentResult` 或 None（=交给 LLM）。

        ⚠ **`handle` 与 `handle_stream` 都必须调它**，源码级守卫
        `test_both_paths_share_one_deterministic_gate` 钉着这条。

        为什么要收敛：这个文件里原本有一条注释写着「两条路径都要挂……
        **只在 handle 里加闸等于没加**」，并点名本仓已为此踩过两次
        （M2 Ledger、商户 badcase）。**2026-08-16 加 Q6 审计出口时，
        我就在那条注释下面踩了第三次**——因为我改的是 `handle`，
        根本没读到 `handle_stream`。真栈读数是 1/3 命中：只有非 salvage 轮走了
        确定性回答，salvage 轮（`via: stream`）照旧让 LLM 编。

        > **判据**：同一条纪律被写成注释还是被写成结构，差别就是会不会有第三次。
        > 「记得加」不是机制，**只有一个入口**才是。
        """
        clock = _clock_answer(text)
        if clock:               # 钟点/日期/星期：系统墙钟直答，零 LLM 零编造
            return AgentResult(speech=clock)
        who = _identity_answer(text, meta)
        if who:                 # 「我是谁」：声纹已认定，同样不交给 LLM（历史会盖过 system）
            return AgentResult(speech=who)
        safety, alert = _safety_answer(text)
        if safety:              # 安全信号：确定性直答 + 声明会话告警，零 LLM
            return AgentResult(speech=safety, data={"_safety_alert": alert})
        if is_execution_audit_question(text):
            # Q6：「刚才实际执行了什么」是**系统持有的事实**，零 LLM。
            # 加提示词治不了它——模型手里根本没有那些数（真栈三次取样三个样，
            # 一次方向说反、一次直接否认执行过）。答案由会话历史里的 actions 拼出。
            try:
                history = await ctx.history(20)
            except Exception as e:      # 记忆不可用：诚实说查不到，别猜
                logger.debug("audit history unavailable: %s", e)
                return AgentResult(speech="我这会儿查不到执行记录，稍后再试。")
            return AgentResult(speech=audit_answer(history))
        return None

    async def handle(self, intent, ctx, meta) -> AgentResult:
        text = intent.raw_text or intent.slots.get("text", "")
        fixed = await self._deterministic_reply(text, ctx, meta)
        if fixed is not None:
            return fixed
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
        text = intent.raw_text or intent.slots.get("text", "")
        # ⚠ **两条路径都要挂**：D0 流式直通绕过 executor，本仓已经为此踩过三次
        #（M2 Ledger、商户 badcase、Q6 审计出口各一次）。只在 handle 里加闸等于没加。
        # 第三次之后把它收敛成唯一实现——**注释挡不住第三次，一个入口才行**。
        fixed = await self._deterministic_reply(text, ctx, meta)
        if fixed is not None:
            yield ("speech", fixed.speech)
            yield ("final", fixed)
            return
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
