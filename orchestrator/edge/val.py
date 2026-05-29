"""模拟车控抽象层 VAL (Vehicle Abstraction Layer)。

架构约束：VAL 是唯一能"碰车"的组件。所有车控只经此下发，做指令合法性、权限、安全态门控、状态机。
PoC 为内存模拟；真实实现对接 SOME-IP / AUTOSAR AP / VSOA / CAN。
"""
from __future__ import annotations


class VAL:
    def __init__(self):
        self.state = {
            "hvac_on": False, "hvac_temp": 24,
            "window": "closed", "media": "stopped", "speed_kmh": 60,
        }

    def execute(self, command: str, args: dict) -> tuple[bool, str]:
        # 安全态门控示例（示意）：高速行驶中不完全打开车窗
        if command == "window.open" and self.state["speed_kmh"] > 120:
            return False, "高速行驶中为安全起见暂不打开车窗"
        return self._apply(command, args)

    def _apply(self, command: str, args: dict) -> tuple[bool, str]:
        if command == "hvac.set":
            self.state["hvac_on"] = True
            self.state["hvac_temp"] = int(args.get("temp", 24))
            return True, f"已为您打开空调，设定{self.state['hvac_temp']}度"
        if command == "hvac.on":
            self.state["hvac_on"] = True
            return True, "已为您打开空调"
        if command == "hvac.off":
            self.state["hvac_on"] = False
            return True, "已关闭空调"
        if command == "window.open":
            self.state["window"] = "open"
            return True, "车窗已打开"
        if command == "window.close":
            self.state["window"] = "closed"
            return True, "车窗已关闭"
        if command == "media.play":
            self.state["media"] = "playing"
            return True, "已开始播放"
        if command == "media.pause":
            self.state["media"] = "paused"
            return True, "已暂停播放"
        if command == "media.next":
            return True, "已切换到下一首"
        if command == "media.prev":
            return True, "已切换到上一首"
        return False, "暂不支持该控制指令"
