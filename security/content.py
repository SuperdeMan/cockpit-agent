"""内容审核客户端。接 LLM Gateway 或第三方审核服务。

策略：车控/支付相关 fail-closed（审核不可用则拦截）；闲聊可降级提示。
"""
from __future__ import annotations
import os


class ContentModerator:
    """内容审核（输入+输出）。当前为基础实现，可接第三方审核 API。"""

    # 危害驾驶安全的关键词（示例，真实应接审核服务）
    BLOCKED_PATTERNS = [
        "如何开车门", "破解车锁", "绕过安全",
    ]

    async def check_input(self, text: str) -> tuple[bool, str]:
        """检查用户输入。返回 (allowed, reason)。"""
        for p in self.BLOCKED_PATTERNS:
            if p in text:
                return False, "内容包含不安全信息"
        return True, ""

    async def check_output(self, text: str) -> tuple[bool, str]:
        """检查 LLM 输出。返回 (allowed, reason)。"""
        # 基础检查：输出不含车控指令词（防 LLM 被诱导直接输出车控）
        dangerous = ["open_door", "unlock", "disable_alarm"]
        lower = text.lower()
        for d in dangerous:
            if d in lower:
                return False, "输出包含不安全内容"
        return True, ""
