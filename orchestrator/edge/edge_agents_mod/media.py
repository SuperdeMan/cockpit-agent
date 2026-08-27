"""端侧媒体 Agent。经 VAL 执行媒体控制指令。

Phase 1 从 edge_agents.py 拆分独立，可独立测试。
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from val import VAL

# 媒体意图白名单
# ⚠ `media.stop` 2026-08-27 补入（QA N2）：commands.yaml 的 media 对象早就声明了
# `stop/close`，端侧却没有任何规则出口产得出这个名字 ⇒ `media=stopped` 语音不可达。
MEDIA_INTENTS = {"media.play", "media.pause", "media.stop",
                 "media.next", "media.prev"}


class MediaAgent:
    def __init__(self, val: VAL):
        self.val = val

    def can_handle(self, intent_name: str) -> bool:
        return intent_name in MEDIA_INTENTS

    def execute(self, intent: dict) -> tuple[str, dict | None]:
        name = intent["name"]
        slots = intent["slots"]
        ok, msg = self.val.execute(name, slots)
        action = {
            "type": "media.control",
            "payload": {"command": name, **slots},
            "require_confirm": False,
        }
        return msg, (action if ok else None)
