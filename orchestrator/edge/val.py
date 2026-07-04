"""模拟车控抽象层 VAL (Vehicle Abstraction Layer)。

架构约束：VAL 是唯一能"碰车"的组件。所有车控只经此下发，做指令合法性、权限、安全态门控、状态机。
PoC 为内存模拟；真实实现对接 SOME-IP / AUTOSAR AP / VSOA / CAN。

知识库驱动：启动时加载 knowledge/*.yaml，缺失时回退硬编码（保证 smoke 不破坏）。
"""
from __future__ import annotations

import os
import random
import yaml
from typing import Any


def _load_yaml(path: str) -> dict:
    """加载 YAML 文件，不存在则返回空 dict。"""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class VAL:
    def __init__(
        self,
        knowledge_dir: str | None = None,
        vehicle_model: str | None = None,
        on_change=None,
    ):
        self.state = {
            # 空调
            "hvac_on": False, "hvac_temp": 24,
            # 门窗 / 车身开闭
            "window": "closed", "sunroof": "closed",
            "door_lock": "locked", "trunk": "closed",
            # 座椅
            "seat_heating": False, "seat_ventilation": False,
            # 灯光
            "ambient_light": False, "headlight": False,
            # 媒体
            "media": "stopped", "volume": 30,
            # 其他车身
            "wiper": False, "fragrance": False, "steering_wheel_heating": False,
            # ws8: 安全相关
            "child_lock": False,
            # 动态量（在「车辆动态」面板呈现）
            "speed_kmh": 0, "gear": "P", "battery": 72, "location": None,
        }
        self._on_change = on_change
        self.vehicle_model = vehicle_model
        self.commands: dict = {}
        self.entities: dict = {}
        self.responses: dict = {}

        if knowledge_dir is None:
            knowledge_dir = os.path.join(os.path.dirname(__file__), "knowledge")
        self._load_knowledge(knowledge_dir)

    # ── 知识库加载 ──────────────────────────────────────────────

    def _load_knowledge(self, knowledge_dir: str):
        """加载三件套 YAML；目录不存在或文件缺失时静默跳过（回退硬编码）。"""
        if not os.path.isdir(knowledge_dir):
            return
        self.commands = _load_yaml(os.path.join(knowledge_dir, "commands.yaml"))
        self.entities = _load_yaml(os.path.join(knowledge_dir, "entities.yaml"))
        self.responses = _load_yaml(os.path.join(knowledge_dir, "responses.yaml"))

    # ── 统一入口 ──────────────────────────────────────────────

    def execute(
        self,
        cmd: Any,
        args: dict | None = None,
        answer_length: str = "short",
    ) -> tuple[bool, str]:
        before = dict(self.state)
        result = self._run(cmd, args, answer_length)
        self._notify(before)
        return result

    def _run(
        self,
        cmd: Any,
        args: dict | None,
        answer_length: str,
    ) -> tuple[bool, str]:
        """兼容旧接口 (str, dict) 和新接口 (dict)。

        answer_length: "short"/"standard"（默认，行车简短）或 "detailed"（详细）。
        """
        self._answer_length = answer_length
        if isinstance(cmd, str):
            return self._legacy_execute(cmd, args or {})
        if isinstance(cmd, dict):
            return self._structured_execute(cmd)
        return False, "暂不支持该控制指令"

    # ── 旧接口（向后兼容）──────────────────────────────────────

    def _notify(self, before: dict) -> None:
        if not self._on_change:
            return
        changes = [
            {"key": key, "old": before.get(key), "new": value}
            for key, value in self.state.items()
            if before.get(key) != value
        ]
        if not changes:
            return
        try:
            self._on_change(changes)
        except Exception:
            pass

    def set_env(self, key: str, value: Any) -> None:
        """Set a simulated sensor value and publish the resulting state diff.

        车速与档位保持物理自洽（避免 P 挡却 60km/h 这类矛盾）：
        - 挂入 P/N 挡 → 车速归 0；
        - 车速 >0 而当前处于 P/N → 自动挂入 D（要动得先挂前进挡）。
        """
        before = dict(self.state)
        if before.get(key) == value:
            return
        self.state[key] = value
        if key == "gear" and value in ("P", "N"):
            self.state["speed_kmh"] = 0
        elif key == "speed_kmh":
            try:
                moving = float(value) > 0
            except (TypeError, ValueError):
                moving = False
            if moving and self.state.get("gear") in ("P", "N"):
                self.state["gear"] = "D"
        self._notify(before)

    def _legacy_execute(self, command: str, args: dict) -> tuple[bool, str]:
        # 安全态门控示例：高速行驶中不完全打开车窗
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

    # ── 新接口（结构化命令）──────────────────────────────────

    def _structured_execute(self, cmd: dict) -> tuple[bool, str]:
        """结构化命令执行流水线。

        cmd = {domain, intent, data: {operate, object, attr, positions, value, unit, ...}}
        流水线：归一化 → 校验 → 安全门控 → 模拟 → 选话术
        """
        data = cmd.get("data", {})
        obj = data.get("object")
        operate = data.get("operate")

        if not obj or not operate:
            return False, self._pick_response("unsupported_command")

        # 1. 归一化实体
        normalized = self._normalize_entities(data)

        # 2. 校验：对象/操作/属性是否合法
        ok, err = self._validate_command(obj, operate, normalized)
        if not ok:
            return False, err

        # 3. 安全门控
        ok, err = self._safety_gate(obj, operate, normalized)
        if not ok:
            return False, err

        # 4. 需要二次确认（返回提示，由调用方决定是否继续）
        if self._need_confirm(obj):
            confirm_msg = self._pick_response("Car_general_restrictions_5")
            # PoC：直接执行；真实场景返回 (False, confirm_msg) 让上层处理
            # 这里简化为标记后继续

        # 5. 模拟状态变更
        state_key, new_value = self._simulate(obj, operate, normalized)

        # 6. 选话术（相对调温把模拟后的目标温度并入，便于话术回显"当前26度"）
        response_key = self._build_response_key(obj, operate, normalized)
        resp_data = normalized
        if state_key in ("hvac_temp", "hvac_wind_speed") and operate in ("inc", "dec"):
            resp_data = {**normalized, "value": new_value}
        elif obj == "battery":
            resp_data = {**normalized, "value": new_value}
        speech = self._pick_response(response_key, resp_data)

        return True, speech

    # ── 归一化 ──────────────────────────────────────────────

    def _normalize_entities(self, data: dict) -> dict:
        """把 data 中的中文实体映射为协议标识。"""
        normalized = dict(data)

        # 位置归一化
        if "positions" in data and data["positions"]:
            pos_map = self.entities.get("positions", {})
            raw_positions = data["positions"]
            if isinstance(raw_positions, str):
                raw_positions = [raw_positions]
            resolved = []
            for p in raw_positions:
                if p in pos_map:
                    val = pos_map[p]
                    if isinstance(val, list):
                        resolved.extend(val)
                    else:
                        resolved.append(val)
                else:
                    resolved.append(p)
            normalized["positions"] = resolved

        # 模式归一化（seat_modes / aircon_modes / driving_modes 等）
        if "mode" in data and data["mode"]:
            mode = data["mode"]
            for category in ("seat_modes", "aircon_modes", "driving_modes",
                             "scene_modes", "wind_modes"):
                cat_map = self.entities.get(category, {})
                if mode in cat_map:
                    normalized["mode"] = cat_map[mode]
                    break

        # 颜色归一化
        if "tag" in data and data["tag"]:
            color_map = self.entities.get("light_colors", {})
            if data["tag"] in color_map:
                normalized["tag"] = color_map[data["tag"]]

        # 单位归一化
        if "unit" in data and data["unit"]:
            unit_map = self.entities.get("units", {})
            if data["unit"] in unit_map:
                normalized["unit"] = unit_map[data["unit"]]

        return normalized

    # ── 校验 ──────────────────────────────────────────────

    def _validate_command(self, obj: str, operate: str, data: dict) -> tuple[bool, str]:
        """校验对象、操作、属性是否在 commands.yaml 中合法。"""
        objects = self.commands.get("objects", {})
        if not objects:
            # 无知识库时跳过校验（兼容旧模式）
            return True, ""

        obj_def = objects.get(obj)
        if obj_def is None:
            return False, self._pick_response("unsupported_command")

        # 操作校验
        valid_operates = obj_def.get("operates", [])
        relative_set = operate in ("inc", "dec") and "set" in valid_operates
        if operate not in valid_operates and not relative_set:
            return False, self._pick_response("unsupported_command")

        # 属性校验
        attr = data.get("attr")
        if attr:
            valid_attrs = obj_def.get("attrs", [])
            if valid_attrs and attr not in valid_attrs:
                return False, self._pick_response("unsupported_command")

        # 模式校验（aircon 风速 wind_speed 是 _simulate 处理的伪模式，等价 speed 属性，放行）
        mode = data.get("mode")
        if mode and not (obj == "aircon" and mode == "wind_speed"):
            valid_modes = obj_def.get("modes", [])
            if valid_modes and mode not in valid_modes:
                return False, self._pick_response("unsupported_command")

        # 车型裁剪
        if self.vehicle_model:
            projects = obj_def.get("projects", [])
            if projects and self.vehicle_model not in projects:
                return False, self._pick_response("model_not_supported")

        return True, ""

    # ── 安全门控 ──────────────────────────────────────────

    def _safety_gate(self, obj: str, operate: str, data: dict) -> tuple[bool, str]:
        """安全态门控：voice_forbidden / drive_restricted / speed / battery / gear / child_lock。"""
        objects = self.commands.get("objects", {})
        obj_def = objects.get(obj, {}) if objects else {}

        # voice_forbidden：不支持语音操作
        if obj_def.get("voice_forbidden", False):
            return False, self._pick_response("Car_general_restrictions_4")

        # drive_restricted：行车中不允许操控
        if obj_def.get("drive_restricted", False):
            if self._is_driving():
                if operate in ("open", "set", "switch", "start"):
                    return False, self._pick_response("Car_general_restrictions_2")
                if operate in ("close", "stop"):
                    return False, self._pick_response("Car_general_restrictions_3")

        # drive_restricted_off：行车中只禁"关"（如大灯——夜间关灯致盲），开仍放行
        if obj_def.get("drive_restricted_off", False) and self._is_driving():
            if operate in ("close", "stop", "off"):
                return False, self._pick_response("Car_general_restrictions_3")

        # 通用速度门控（高速行车中限制某些操作）
        speed = self.state.get("speed_kmh", 0)
        if self._is_driving():
            # 高速行驶中不完全打开车窗（>120km/h）
            if obj == "window" and operate == "open":
                if speed > 120:
                    return False, "高速行驶中为安全起见暂不打开车窗"
            # ws8 P0: 高速行驶中禁开车窗/天窗（>80km/h）
            if obj in ("window", "sunroof") and operate == "open" and speed > 80:
                return False, "高速行驶中请勿打开车窗/天窗"

        # ws8 P0: 低电量（<10%）禁用非必要高耗电功能（座椅加热/通风、方向盘加热、氛围灯、香氛）。
        # 用指令对象名（seat/steering_wheel）+ mode 判断；座椅加热/通风是 object=seat、
        # mode=heating/ventilation，不能写成状态键名 seat_heating（那样永不命中）。
        # 注意：空调(aircon)不在此列——AC 关系到行车舒适与除雾安全，真实车低电量也只提示影响
        # 续航、不硬禁；硬禁 AC 反而违和（见 2026-06-23 用户反馈）。低电量对 AC 的处理留给
        # 场景编排/建议层做"降级提示"，而非端侧门控直接拒绝。
        battery = self.state.get("battery", 100)
        if battery < 10:
            mode = data.get("mode")
            high_power = (
                obj in ("ambient_light", "fragrance")
                or (obj == "seat" and mode in ("heating", "ventilation"))
                or (obj == "steering_wheel" and mode == "heating")
            )
            if high_power:
                return False, "电量过低，已禁用高耗电功能"

        # ws8 P0: 倒车中禁用非安全相关车控
        gear = self.state.get("gear", "P")
        if gear == "R" and obj not in ("rear_view_mirror", "wiper", "headlight"):
            return False, "倒车中请专注驾驶"

        # ws8 P0: 儿童锁激活时禁用后排车窗/车门
        if self.state.get("child_lock", False):
            if obj in ("window", "door_lock"):
                positions = data.get("positions", [])
                # 后排位置：rear / rear_left / rear_right / rear_center
                rear_positions = {"rear", "rear_left", "rear_right", "rear_center"}
                if any(p in rear_positions for p in positions):
                    return False, "儿童锁已激活，后排车窗/车门已锁定"

        return True, ""

    def _is_driving(self) -> bool:
        """判断是否处于行车状态（speed>0 或档位非 P）。"""
        speed = self.state.get("speed_kmh", 0)
        gear = self.state.get("gear", "P")
        return speed > 0 or gear not in ("P", "N")

    def _need_confirm(self, obj: str) -> bool:
        """检查是否为危险动作（需要二次确认）。"""
        objects = self.commands.get("objects", {})
        obj_def = objects.get(obj, {}) if objects else {}
        return obj_def.get("require_confirm", False)

    # ── 状态模拟 ──────────────────────────────────────────

    @staticmethod
    def _window_pct(value: Any) -> int:
        """把车窗状态值（open/closed/"70%"）归一成 0–100 整数。"""
        if value == "open":
            return 100
        if value in ("closed", "close", None):
            return 0
        if isinstance(value, str):
            digits = value.replace("%", "").strip()
            if digits.isdigit():
                return max(0, min(100, int(digits)))
        if isinstance(value, (int, float)):
            return max(0, min(100, int(value)))
        return 0

    def _simulate(self, obj: str, operate: str, data: dict) -> tuple[str | None, Any]:
        """模拟状态变更；返回 (state_key, new_value)。"""
        value = data.get("value")
        mode = data.get("mode")

        if obj == "aircon":
            # 风速：attr=="speed"（edge_call 路径）或 mode=="wind_speed"（fast_intent 路径）
            is_wind = data.get("attr") == "speed" or data.get("mode") == "wind_speed"
            if is_wind:
                current = self.state.get("hvac_wind_speed", 1)
                if operate == "set" and value is not None:
                    self.state["hvac_wind_speed"] = int(value)
                elif operate == "inc":
                    self.state["hvac_wind_speed"] = min(current + 1, 10)
                elif operate == "dec":
                    self.state["hvac_wind_speed"] = max(current - 1, 0)
                # set 无具体值时回退到当前档，避免未初始化 KeyError
                return "hvac_wind_speed", self.state.setdefault("hvac_wind_speed", current)
            # 相对调温（"再高一点/我冷了"→inc，"低一点"→dec），实际 ±1 度并夹在 16~32
            if operate == "inc":
                self.state["hvac_on"] = True
                self.state["hvac_temp"] = min(int(self.state.get("hvac_temp", 24)) + 1, 32)
                return "hvac_temp", self.state["hvac_temp"]
            if operate == "dec":
                self.state["hvac_on"] = True
                self.state["hvac_temp"] = max(int(self.state.get("hvac_temp", 24)) - 1, 16)
                return "hvac_temp", self.state["hvac_temp"]
            if operate in ("open", "set"):
                self.state["hvac_on"] = True
                if value:
                    self.state["hvac_temp"] = int(value)
                return "hvac_on", True
            if operate == "close":
                self.state["hvac_on"] = False
                return "hvac_on", False

        elif obj == "window":
            if operate == "open":
                # "开一半/开到 X" 带程度：有 value 记百分比，否则视为全开
                pos = data.get("value")
                self.state["window"] = f"{int(pos)}%" if pos else "open"
                return "window", self.state["window"]
            if operate == "close":
                self.state["window"] = "closed"
                return "window", "closed"
            if operate == "set":
                pos = data.get("value")
                self.state["window"] = f"{int(pos)}%" if pos is not None else "open"
                return "window", self.state["window"]
            if operate in ("inc", "dec"):
                # "开大/小一点"：解析当前开度 ±step（默认 20%），夹在 0–100
                cur = self._window_pct(self.state.get("window"))
                step = int(data.get("value") or 20)
                new = min(cur + step, 100) if operate == "inc" else max(cur - step, 0)
                self.state["window"] = (
                    "closed" if new == 0 else "open" if new >= 100 else f"{new}%"
                )
                return "window", self.state["window"]

        elif obj == "seat":
            key = f"seat_{mode or 'heating'}"
            # 座椅放平：带角度时记角度（供仪表盘/话术回显），否则记 True
            if mode == "recline" and operate in ("open", "set"):
                self.state["seat_recline"] = int(value) if value else True
                return "seat_recline", self.state["seat_recline"]
            if operate in ("open", "set"):
                self.state[key] = True
                return key, True
            if operate == "close":
                self.state[key] = False
                return key, False

        elif obj == "sunroof":
            if operate in ("open", "set"):
                # "开一半/开到 X" 等带程度的指令：有 value 记百分比，否则视为打开
                pos = data.get("value")
                self.state["sunroof"] = f"{int(pos)}%" if pos else "open"
                return "sunroof", self.state["sunroof"]
            if operate == "close":
                self.state["sunroof"] = "closed"
                return "sunroof", "closed"

        elif obj == "sunshade":
            if operate == "open":
                self.state["sunshade"] = "open"
                return "sunshade", "open"
            if operate == "close":
                self.state["sunshade"] = "closed"
                return "sunshade", "closed"

        elif obj == "ambient_light":
            if operate == "open":
                self.state["ambient_light"] = True
                return "ambient_light", True
            if operate == "close":
                self.state["ambient_light"] = False
                return "ambient_light", False
            if operate == "set":
                self.state["ambient_light"] = True  # 设色/亮度隐含开灯
                if data.get("tag"):
                    self.state["ambient_light_color"] = data["tag"]
                    return "ambient_light_color", data["tag"]
                if value:
                    self.state["ambient_light_brightness"] = int(value)
                    return "ambient_light_brightness", int(value)
                return "ambient_light", True

        elif obj in ("media", "music", "radio", "online_radio", "audiobook",
                     "opera", "news", "video", "TV"):
            if operate in ("open", "start", "play"):
                self.state["media"] = "playing"
                return "media", "playing"
            if operate == "pause":
                self.state["media"] = "paused"
                return "media", "paused"
            if operate in ("close", "stop"):
                self.state["media"] = "stopped"
                return "media", "stopped"
            if operate in ("switch", "next", "prev"):
                self.state["media"] = "playing"
                return "media", "playing"

        elif obj == "headlight":
            if operate == "open":
                self.state["headlight"] = True
                return "headlight", True
            if operate == "close":
                self.state["headlight"] = False
                return "headlight", False

        elif obj == "trunk":
            if operate == "open":
                self.state["trunk"] = "open"
                return "trunk", "open"
            if operate == "close":
                self.state["trunk"] = "closed"
                return "trunk", "closed"

        elif obj == "door_lock":
            if operate == "open":
                self.state["door_lock"] = "unlocked"
                return "door_lock", "unlocked"
            if operate == "close":
                self.state["door_lock"] = "locked"
                return "door_lock", "locked"

        elif obj == "fuel_tank_cover":
            if operate == "open":
                self.state["fuel_tank_cover"] = "open"
                return "fuel_tank_cover", "open"
            if operate == "close":
                self.state["fuel_tank_cover"] = "closed"
                return "fuel_tank_cover", "closed"

        elif obj == "charging_port":
            if operate == "open":
                self.state["charging_port"] = "open"
                return "charging_port", "open"
            if operate == "close":
                self.state["charging_port"] = "closed"
                return "charging_port", "closed"

        elif obj == "rear_view_mirror":
            if operate == "open" or (operate == "set" and mode == "unfold"):
                self.state["rear_view_mirror"] = "unfolded"
                return "rear_view_mirror", "unfolded"
            if operate == "close" or (operate == "set" and mode == "fold"):
                self.state["rear_view_mirror"] = "folded"
                return "rear_view_mirror", "folded"

        elif obj == "wiper":
            if operate == "open":
                self.state["wiper"] = True
                return "wiper", True
            if operate == "close":
                self.state["wiper"] = False
                return "wiper", False
            if operate == "set" and value:
                self.state["wiper_speed"] = int(value)
                return "wiper_speed", int(value)

        elif obj == "fragrance":
            if operate == "open":
                self.state["fragrance"] = True
                return "fragrance", True
            if operate == "close":
                self.state["fragrance"] = False
                return "fragrance", False

        elif obj == "steering_wheel":
            attr = data.get("attr")
            if operate == "set" and mode == "heating":
                enabled = data.get("enabled", True)
                if isinstance(enabled, str):
                    enabled = enabled.lower() == "true"
                self.state["steering_wheel_heating"] = bool(enabled)
                return "steering_wheel_heating", bool(enabled)
            if attr == "height":
                current = self.state.get("steering_wheel_height", 0)
                if operate == "set" and value is not None:
                    self.state["steering_wheel_height"] = int(value)
                elif operate == "inc":
                    self.state["steering_wheel_height"] = current + 1
                elif operate == "dec":
                    self.state["steering_wheel_height"] = current - 1
                return (
                    "steering_wheel_height",
                    self.state["steering_wheel_height"],
                )

        elif obj == "driving_mode":
            if operate in ("set", "switch") and mode:
                self.state["driving_mode"] = mode
                return "driving_mode", mode

        elif obj == "scene_mode":
            if operate in ("set", "switch") and mode:
                self.state["scene_mode"] = mode
                return "scene_mode", mode

        elif obj == "volume":
            if operate == "set" and value:
                self.state["volume"] = int(value)
                return "volume", int(value)
            if operate == "inc":
                self.state["volume"] = min(self.state.get("volume", 50) + 10, 100)
                return "volume", self.state["volume"]
            if operate == "dec":
                self.state["volume"] = max(self.state.get("volume", 50) - 10, 0)
                return "volume", self.state["volume"]

        elif obj == "screen":
            if operate == "set" and value is not None:
                self.state["screen_brightness"] = int(value)
                return "screen_brightness", int(value)
            if operate == "inc":
                self.state["screen_brightness"] = min(
                    self.state.get("screen_brightness", 50) + 10, 100)
                return "screen_brightness", self.state["screen_brightness"]
            if operate == "dec":
                self.state["screen_brightness"] = max(
                    self.state.get("screen_brightness", 50) - 10, 0)
                return "screen_brightness", self.state["screen_brightness"]

        elif obj == "energy_recovery":
            if operate == "set" and value:
                self.state["energy_recovery"] = int(value)
                return "energy_recovery", int(value)

        elif obj == "accompany_home":
            if operate == "open":
                self.state["accompany_home"] = True
                return "accompany_home", True
            if operate == "close":
                self.state["accompany_home"] = False
                return "accompany_home", False

        elif obj == "battery":
            # 查询类：不改状态，回传当前电量供话术回显
            return None, self.state.get("battery", 0)

        elif obj in ("tire_pressure_monitoring", "dashcam"):
            # 查询类 / 开关类
            if operate == "open":
                self.state[obj] = True
                return obj, True
            if operate == "close":
                self.state[obj] = False
                return obj, False

        # 兜底：标记状态
        key = f"{obj}_{operate}"
        self.state[key] = True
        return key, True

    # ── 话术选择 ──────────────────────────────────────────

    def _build_response_key(self, obj: str, operate: str, data: dict) -> str:
        """根据对象+操作构建 responses.yaml 的 key。"""
        mode = data.get("mode")

        # 特殊映射
        if obj == "aircon":
            is_wind = data.get("attr") == "speed" or data.get("mode") == "wind_speed"
            if operate == "open":
                return "hvac_on_success"
            if operate == "close":
                return "hvac_off_success"
            if operate == "set":
                return "hvac_wind_speed_set_success" if is_wind else "hvac_set_success"
            # 风速相对调（"风速调小一点"）也给明确话术，别落到 generic "好的"
            if is_wind and operate == "inc":
                return "hvac_wind_speed_inc_success"
            if is_wind and operate == "dec":
                return "hvac_wind_speed_dec_success"
            if operate == "inc":
                return "hvac_inc_success"
            if operate == "dec":
                return "hvac_dec_success"

        if obj == "window":
            op_map = {"open": "window_open_success", "close": "window_close_success"}
            return op_map.get(operate, "generic_success")

        if obj == "sunroof":
            op_map = {"open": "sunroof_open_success", "close": "sunroof_close_success"}
            return op_map.get(operate, "generic_success")

        if obj == "sunshade":
            op_map = {"open": "sunshade_open_success", "close": "sunshade_close_success"}
            return op_map.get(operate, "generic_success")

        if obj == "seat":
            if mode == "heating":
                return "seat_heating_on_success" if operate in ("open", "set") else "seat_heating_off_success"
            if mode == "ventilation":
                return "seat_ventilation_on_success" if operate in ("open", "set") else "seat_ventilation_off_success"
            if mode == "massage":
                return "seat_massage_on_success" if operate in ("open", "set") else "seat_massage_off_success"
            if mode == "recline":
                return "seat_recline_success"
            return "seat_set_success"

        if obj == "ambient_light":
            if operate in ("open", "close"):
                return "ambient_light_on_success" if operate == "open" else "ambient_light_off_success"
            if data.get("tag"):
                return "ambient_light_color_success"
            return "ambient_light_brightness_success"

        if obj == "headlight":
            return "headlight_on_success" if operate == "open" else "headlight_off_success"

        if obj == "trunk":
            return "trunk_open_success" if operate == "open" else "trunk_close_success"

        if obj == "door_lock":
            return "door_lock_open_success" if operate == "open" else "door_lock_close_success"

        if obj == "fuel_tank_cover":
            return "fuel_tank_cover_open_success" if operate == "open" else "fuel_tank_cover_close_success"

        if obj == "charging_port":
            return "charging_port_open_success" if operate == "open" else "charging_port_close_success"

        if obj == "rear_view_mirror":
            if operate == "open" or (operate == "set" and mode == "unfold"):
                return "rear_view_mirror_unfold_success"
            return "rear_view_mirror_fold_success"

        if obj == "wiper":
            if operate == "open":
                return "wiper_on_success"
            if operate == "close":
                return "wiper_off_success"
            return "wiper_set_success"

        if obj == "fragrance":
            return "fragrance_on_success" if operate == "open" else "fragrance_off_success"

        if obj == "steering_wheel":
            return "steering_wheel_heating_on_success" if operate in ("open", "set") else "steering_wheel_heating_off_success"

        if obj == "driving_mode":
            return "driving_mode_set_success"

        if obj == "scene_mode":
            return "scene_mode_set_success"

        if obj == "volume":
            if operate == "set":
                return "volume_set_success"
            if operate == "inc":
                return "volume_inc_success"
            if operate == "dec":
                return "volume_dec_success"

        if obj == "screen":
            return "screen_brightness_success"

        if obj == "energy_recovery":
            return "energy_recovery_set_success"

        if obj == "accompany_home":
            return "accompany_home_on_success" if operate in ("open", "set") else "accompany_home_off_success"

        if obj == "battery":
            return "battery_query_success"

        if obj == "tire_pressure_monitoring":
            return "tire_pressure_query_success"

        if obj == "dashcam":
            return "dashcam_on_success" if operate == "open" else "dashcam_off_success"

        # ── R4.1b P0：端侧对象化话术 ──
        if obj == "air_purifier":
            return "air_purifier_on_success" if operate == "open" else "air_purifier_off_success"

        if obj == "navi_broadcast":
            if operate == "set":
                return "navi_broadcast_mode_success"
            return "navi_broadcast_on_success" if operate == "open" else "navi_broadcast_off_success"

        if obj == "key_tone":
            return "key_tone_on_success" if operate == "open" else "key_tone_off_success"

        if obj == "media":
            op_map = {"start": "media_start_success", "pause": "media_pause_success",
                      "stop": "media_stop_success", "switch": "media_switch_success"}
            return op_map.get(operate, "media_start_success")

        return "generic_success"

    def _pick_response(self, key: str, data: dict | None = None) -> str:
        """从 responses.yaml 选话术。根据 answer_length 选 brief 或 full。"""
        resp = self.responses.get(key)
        if not resp:
            return key  # 无模板时返回 key 本身作为 fallback

        # 根据 answer_length 设置选择话术列表
        # HMI 发 short/standard/detailed；detailed 用 full，其余用 brief
        length = getattr(self, '_answer_length', 'short')
        if length == 'detailed':
            speeches = resp.get("speech_full") or resp.get("speech_brief") or []
        else:
            speeches = resp.get("speech_brief") or resp.get("speech_full") or []
        if not speeches:
            return resp.get("scene", key)

        # 有数据时优先选含占位符的模板
        if data:
            placeholder_speeches = [s for s in speeches if "{" in s]
            if placeholder_speeches:
                speeches = placeholder_speeches

        template = random.choice(speeches)

        # 替换占位符
        if data:
            template = template.replace("{value}", str(data.get("value", "")))
            template = template.replace("{mode}", str(data.get("mode", "")))
            template = template.replace("{tag}", str(data.get("tag", "")))
            # 位置：取第一个
            positions = data.get("positions", [])
            if positions:
                pos_display = positions[0] if isinstance(positions, list) else str(positions)
            else:
                pos_display = ""
            template = template.replace("{position}", pos_display)

        return template
