"""Agent 业务返回的友好结构，SDK 负责转成 proto ExecuteResponse。"""
from __future__ import annotations
from dataclasses import dataclass, field

OK = "ok"
NEED_CONFIRM = "need_confirm"
NEED_SLOT = "need_slot"
FAILED = "failed"
REJECTED = "rejected"


@dataclass
class AgentResult:
    speech: str = ""                       # 给 TTS 的播报话术
    status: str = OK                       # 见上方常量
    ui_card: dict | None = None            # 给 HMI 的结构化卡片
    actions: list[dict] = field(default_factory=list)
    # action: {"type": "navigate"|"vehicle.control"|..., "payload": {...}, "require_confirm": bool}
    follow_up: str = ""                    # 多轮追问/澄清

    def action(self, type_: str, payload: dict, require_confirm: bool = False) -> "AgentResult":
        """链式添加一个动作。注意: vehicle.control 仅产出意图，真正下发由端侧 Executor 经 VAL 校验。"""
        self.actions.append({"type": type_, "payload": payload, "require_confirm": require_confirm})
        return self
