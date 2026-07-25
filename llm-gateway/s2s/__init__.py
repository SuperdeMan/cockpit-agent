"""S2S（speech-to-speech）全双工会话模块——M4 形态升级。

设计：`docs/design/2026-07-25-m4-s2s-fullduplex-rfc.md`（协议冻结基线在 §3.5）。

分层（换厂商只动 provider.py）：
  protocol.py  对上事件协议 + 单工具 escalate 定义（HMI 只认识这层，永不随厂商变）
  provider.py  对下 provider 抽象 + 厂商实现（qwen omni realtime / mock）
  session.py   L-Session 状态机：turn 对账 / barge-in 残包丢弃 / 重连重注入 / 降级
  reflux.py    文本副产品强制回灌 memory+obs（防记忆黑洞）+ 漏移交检测

**安全铁律**：S2S 会话内**没有任何执行通道**。模型唯一的工具是 `escalate`，它只把
用户原话交回确定性主链——车控/支付/一切副作用照走 planner→executor→VAL→确认闸，
S2S 只是新的「话筒」，不是新的规划入口。
"""
from .protocol import (  # noqa: F401
    DOWN_ANSWER_DELTA, DOWN_AUDIO_META, DOWN_ESCALATED, DOWN_SESSION_STATE,
    DOWN_TRANSCRIPT, DOWN_TURN_END, DOWN_UNSUPPORTED, END_CANCELLED, END_COMPLETE,
    END_ERROR, END_ESCALATED, ESCALATE_TOOL_NAME, UP_AUDIO, UP_AUDIO_DONE, UP_BARGE_IN,
    UP_CANCEL_TURN, UP_ESCALATED_RESULT, UP_SESSION_END, UP_SESSION_START,
    escalate_tool, new_turn_id, persona,
)
from .provider import (  # noqa: F401
    BaseS2SProvider, MockS2SProvider, QwenOmniRealtimeProvider, S2SEvent,
    build_s2s_provider, s2s_available,
)
from .reflux import Reflux, build_context_summary, detect_false_promise  # noqa: F401
from .session import S2SSession, SessionState, Turn  # noqa: F401
