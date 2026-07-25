"""S2S 对上事件协议 + 单工具 escalate 定义（M4 RFC §3.2 / §5.1，P0 探针冻结）。

**两端两个契约**（RFC §3.1）：本模块是「对上」那个——HMI 只认识这里的事件名，
穿越厂商差异、永不随厂商变。对下的 provider 抽象在 `provider.py`。

事件名走本侧语义，不照抄 OpenAI realtime（协议漂移即 HMI 重写是要避免的事）。
"""
from __future__ import annotations
import os
import uuid

# ── 上行（HMI → 网关）──
UP_SESSION_START = "session.start"
UP_AUDIO = "audio"                    # 之后跟二进制 PCM 帧（16k mono s16le）
UP_BARGE_IN = "barge_in"              # L-HMI 判定打断（本侧 VAD/KWS 权威）
UP_CANCEL_TURN = "cancel_turn"        # THINKING 期取消（对齐既有 onCancelTurn）
UP_ESCALATED_RESULT = "escalated_result"  # 逃逸轮主链回答回传（只为上下文连续，不为播报）
UP_SESSION_END = "session.end"

# ── 下行（网关 → HMI）──
DOWN_TRANSCRIPT = "turn.transcript"      # 用户话转写（上屏复用 partial 气泡）
DOWN_ANSWER_DELTA = "turn.answer_delta"  # 回答文本增量（字幕/气泡）
DOWN_AUDIO_META = "turn.audio_meta"      # 之后跟二进制 PCM 帧（复用既有播放器）
DOWN_TURN_END = "turn.end"               # reason=complete|cancelled|escalated|error
DOWN_ESCALATED = "turn.escalated"        # 本轮改走文本主链，HMI 按既有 send(utterance) 走
DOWN_SESSION_STATE = "session.state"     # ready|reconnecting|degraded
DOWN_UNSUPPORTED = "unsupported"         # 无 provider/无 key（HMI 回落三段式，与 ASR 面同口径）

# turn 结束原因
END_COMPLETE = "complete"
END_CANCELLED = "cancelled"
END_ESCALATED = "escalated"
END_ERROR = "error"

# L-Session 状态（对上只暴露三态；内部态见 session.SessionState）
STATE_READY = "ready"
STATE_RECONNECTING = "reconnecting"
STATE_DEGRADED = "degraded"

# ── §5.1 单工具 escalate ──
# **刻意不注入座舱 capability 清单**：M1a 教训（tool schema 三向改变模型输出分布）在语音
# 场景没有旅程级护栏可兜——S2S 轮不过 planner 校验。单工具把判定权压缩为二元（自答 or
# 移交），错误面只剩「该移交没移交」（§5.3 三道背板）。
ESCALATE_TOOL_NAME = "escalate"

# §6.2 域灰度 = 收放这段 description 的边界，**不做运行时按 intent 拦截**
#（判定点在模型生成前的工具选择；生成后拦截必然截断音频）。
_ESCALATE_DESC_DEFAULT = (
    "当用户的请求超出闲聊/常识问答范围时调用——车辆控制（空调/车窗/座椅/氛围灯）、导航、"
    "查询实时信息（天气/股价/新闻/赛事/路况）、设置提醒、播放媒体、支付等一切需要执行动作"
    "或查询车辆/外部系统的请求，都必须调用本工具移交，不要自己口头答应。把用户请求原样转述。"
)


def escalate_tool() -> dict:
    """单工具定义（description 经 env 收放实现域灰度）。"""
    return {
        "type": "function",
        "name": ESCALATE_TOOL_NAME,
        "description": os.getenv("S2S_ESCALATE_DESC", "").strip() or _ESCALATE_DESC_DEFAULT,
        "parameters": {
            "type": "object",
            "properties": {"utterance": {"type": "string", "description": "用户请求原话"}},
            "required": ["utterance"],
        },
    }


_PERSONA_DEFAULT = (
    "你是车载语音助手小舟，说话简短口语化，一两句话说完。"
    "遇到需要执行动作或查实时信息的请求，必须调用 escalate 工具移交，绝不自己口头答应。"
)


def persona() -> str:
    """S2S 会话人设（与 chitchat persona 同源口径；env 可覆盖）。"""
    return os.getenv("S2S_PERSONA", "").strip() or _PERSONA_DEFAULT


def new_turn_id() -> str:
    """turn_id 由**网关**生成——provider 的 response id 只在 L-Session 内部对账用，
    不透传给上层（§3.2「可丢弃缓存」原则的协议面）。"""
    return uuid.uuid4().hex[:16]
