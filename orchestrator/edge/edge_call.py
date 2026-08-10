"""Execute cloud-scheduled edge intents through the deterministic VAL."""
from __future__ import annotations

from google.protobuf import struct_pb2

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2

from val import VAL


def _struct(values: dict) -> struct_pb2.Struct:
    result = struct_pb2.Struct()
    result.update(values)
    return result


def _normalize_operation(operation: str) -> str:
    return {
        "on": "open",
        "off": "close",
        "play": "start",
        "next": "switch",
        "prev": "switch",
        "fold": "set",
        "unfold": "set",
    }.get(operation, operation)


# 媒体类对象 → action.type 用 media.control（与 server.py 本地路径口径一致）
# 对象清单对应 VAL commands.yaml 的媒体类 objects。
_MEDIA_OBJECTS = {
    "media", "music", "radio", "online_radio", "audiobook",
    "opera", "news", "video", "TV",
}


def action_type_for(obj: str) -> str:
    """媒体类对象 → ``media.control``，其余 → ``vehicle.control``。

    端侧所有本地执行路径（server.py 快路径 A/A2/B、云端降级兜底、
    以及本模块 action_to_structured）判定 AgentAction.type 的唯一入口，
    保证同一对象在任何路径得到一致的 action_type（对象清单以
    ``_MEDIA_OBJECTS`` 为准）。
    """
    return "media.control" if obj in _MEDIA_OBJECTS else "vehicle.control"

# 知识库缺失（离线/无 commands.yaml）时的兜底对象集；
# 有知识库时由 VAL commands.yaml 的 objects 作为单一真相源（见 EdgeCallExecutor._known_objects）。
_FALLBACK_KNOWN_OBJECTS = {
    "aircon", "window", "seat", "sunroof", "sunshade", "trunk",
    "door_lock", "ambient_light", "headlight", "wiper",
    "rear_view_mirror", "fragrance", "volume", "fuel_tank_cover",
    "charging_port", "steering_wheel", "energy_recovery",
    "lane_departure_assistance", "lane_assistance", "scene_mode",
    "power_mode", "screen", "tire_pressure_monitoring", "dashcam",
    "accompany_home", "front_defogger", "rear_defogger",
    "media", "bluetooth", "wifi", "hotspot",
    "auto_hold", "equalizer", "sound_effect", "voice_assistant",
    "surround_view", "dashboard", "phone", "contacts", "call_log",
    "low_beam",
}


# 追问话术里属性名的中文（纯语言表，不是领域策略——策略在 commands.yaml 的
# `value_required_operates` 里）。缺词条时话术退成「您想设成多少？」，不崩不静默。
_ATTR_CN = {"temperature": "温度", "speed": "风速", "brightness": "亮度",
            "height": "高度", "level": "档位"}


def _to_structured(intent_name: str, slots: dict[str, str],
                   known_objects: set[str] | None = None) -> dict | None:
    parts = [p for p in intent_name.split(".") if p]
    if len(parts) < 2:
        return None

    raw_object = parts[0]
    object_name = {
        "hvac": "aircon",
        "tire_pressure": "tire_pressure_monitoring",
    }.get(raw_object, raw_object)
    operation = _normalize_operation(parts[-1])
    path = ".".join(parts[1:-1])
    attribute = {
        ("aircon", "wind_speed"): "speed",
        ("screen", "brightness"): "brightness",
        ("steering_wheel", "height"): "height",
        ("wiper", "speed"): "speed",
    }.get((object_name, path))
    mode = "" if attribute else path

    # 只放行 VAL 知识库已声明的对象（R5：对象集来自 commands.yaml，避免与知识库漂移）。
    if known_objects is None:
        known_objects = _FALLBACK_KNOWN_OBJECTS
    if object_name not in known_objects:
        return None

    data = dict(slots)
    data["object"] = object_name
    data["operate"] = operation
    if attribute:
        data.setdefault("attr", attribute)
    if mode:
        data.setdefault("mode", mode)
    if parts[-1] in ("fold", "unfold"):
        data["mode"] = parts[-1]
    if parts[-1] in ("next", "prev"):
        data["mode"] = parts[-1]
    if object_name == "steering_wheel" and mode == "heating":
        if parts[-1] in ("open", "on"):
            data["operate"] = "set"
            data["enabled"] = True
        elif parts[-1] in ("close", "off"):
            data["operate"] = "set"
            data["enabled"] = False

    for source in ("temp", "temperature", "level", "brightness"):
        if source in data and "value" not in data:
            data["value"] = data[source]
            break

    return {
        "domain": "car_control" if object_name != "media" else "media",
        "intent": intent_name,
        "data": data,
    }


# 云端 Agent / 场景知识库产出的 vehicle.control 动作用「友好参数名」，这里映射到 VAL
# data 字段；temperature/temp/level/brightness → value 由 _to_structured 兜底归一。
_ACTION_PARAM_ALIASES = {
    "color": "tag",
    "position": "positions",
    "angle": "value",
}

# 少数命令无法由 <object>.<operate> 直接拆出，显式声明 object/operate/mode。
# seat.recline（座椅放平）：VAL 用 seat + set + mode=recline 建模（recline 非通用 operate）。
_COMMAND_OVERRIDES = {
    "seat.recline": {"object": "seat", "operate": "set", "mode": "recline"},
}


def action_to_structured(
    command: str,
    params: dict | None,
    known_objects: set[str] | None = None,
    object_defs: dict | None = None,
) -> dict | None:
    """把云端 Agent 的 vehicle.control 动作（command 串 + 友好 params）翻译成 VAL 结构化命令。

    场景/计划层只声明意图（command + 友好参数）；车控的 object/operate/data 由端侧在此翻译，
    再走 VAL 完整结构化流水线（归一 → 校验 → 安全门控 → 模拟）。这样场景动作不再落到只认
    hvac/window/media 的 legacy 串路径，也让云端车控统一经安全门控（legacy 路径此前会绕过）。

    返回结构化 dict，或 None（无法翻译 → 调用方回退 legacy 串执行）。
    """
    aliased: dict = {}
    for k, v in (params or {}).items():
        if k in ("command", "_origin"):
            continue
        aliased[_ACTION_PARAM_ALIASES.get(k, k)] = v

    override = _COMMAND_OVERRIDES.get(command)
    if override:
        obj = override["object"]
        if known_objects is not None and obj not in known_objects:
            return None
        data = dict(aliased)
        data["object"] = obj
        data["operate"] = override["operate"]
        if override.get("mode"):
            data["mode"] = override["mode"]
        return {"domain": "car_control", "intent": command, "data": data}

    structured = _to_structured(command, aliased, known_objects=known_objects)
    if structured is None:
        return None

    # 丢弃该对象不支持的 mode（如场景 hvac 的 auto/quiet/external_circulation 舒适标签），
    # 否则 _validate_command 会因 mode 非法整条拒绝、动作不可执行。
    data = structured["data"]
    mode = data.get("mode")
    if mode and object_defs is not None:
        modes = (object_defs.get(data.get("object")) or {}).get("modes") or []
        if modes and mode not in modes:
            data.pop("mode", None)
    return structured


def decode_intent(intent_name: str,
                  known_objects: set[str] | None = None) -> dict | None:
    """intent 名 → VAL 结构化命令（**只解码不执行**）。

    存在的理由只有一个：让**能力描述与执行语义同源**。`capabilities.py` 生成
    catalog 里那 78 条判别化描述时走这个函数，拿到的 (object/operate/attr/mode)
    与 `EdgeCallExecutor.execute` 待会儿真拿去校验、门控、执行的**是同一组值**。
    自己另写一份 intent→object 的映射表，就是「注册与识别不同信道」那一课的翻版：
    两边各自漂移，而描述写错只会让 planner 悄悄选错工具、不会报错。
    """
    return _to_structured(intent_name, {}, known_objects=known_objects)


class EdgeCallExecutor:
    """Translate an EdgeCall to a VAL command and return Agent response semantics."""

    def __init__(self, val: VAL):
        self.val = val

    def _known_objects(self) -> set[str] | None:
        """VAL 知识库声明的对象集（单一真相源）；无知识库时返回 None 走兜底集。"""
        objects = (self.val.commands or {}).get("objects") or {}
        return set(objects) if objects else None

    def _missing_required_value(self, obj: str, data: dict) -> str:
        """声明了「这个 operate 必须带值」却没带值时，返回缺的那个属性名，否则空串。

        判据全部来自知识库（`commands.yaml` 的 `value_required_operates` + `attrs`），
        本函数**没有任何对象/意图字面量**——新对象要追问，加一行 YAML 即可。

        三个不触发的情形，缺一不可：
        - `mode` 在场（『空调开到制冷』设的是模式不是数值）；
        - `attr` 在场（`aircon.wind_speed.set` 走的是风速那条属性，不是默认属性）；
        - 对象没声明 `attrs`（它的 set 本来就是选模式）。

        > 为什么只挡云端计划这一路：端侧快路径的 `_to_structured` 只在**有值**时才产
        > `hvac.set`（无值走 `hvac.on`），压根到不了这里。挡在这里而不是 VAL 里，是因为
        > VAL 的失败通道是 REJECTED（安全门控），而这里要的是 NEED_SLOT（追问），
        > 两者对用户是完全不同的两件事。
        """
        if data.get("mode") or data.get("attr"):
            return ""
        if str(data.get("value") or "").strip():
            return ""
        defs = ((self.val.commands or {}).get("objects") or {}).get(obj) or {}
        if data.get("operate") not in (defs.get("value_required_operates") or []):
            return ""
        attrs = defs.get("attrs") or []
        return str(attrs[0]) if attrs else ""

    def execute(self, call) -> agent_pb2.ExecuteResponse:
        from observability.events import change_source

        change_source.set("edge_call")
        intent_name = call.intent.name
        slots = dict(call.intent.slots)
        structured = _to_structured(
            intent_name, slots, known_objects=self._known_objects())
        if structured is None:
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.FAILED,
                error=common_pb2.ErrorInfo(
                    code="invalid_request",
                    message=f"unsupported edge intent: {intent_name}",
                ),
            )

        obj = structured["data"]["object"]
        missing = self._missing_required_value(obj, structured["data"])
        if missing:
            defs = ((self.val.commands or {}).get("objects") or {}).get(obj) or {}
            noun = f"{defs.get('display_name') or ''}{_ATTR_CN.get(missing, '')}"
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.NEED_SLOT,
                speech=f"您想把{noun}设成多少？" if noun else "您想设成多少？",
                missing_slots=[missing],
            )
        confirmed = call.meta.get("confirmed", "").lower() == "true"
        if self.val._need_confirm(obj) and not confirmed:
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.NEED_CONFIRM,
                speech="这项操作可能影响车辆安全，请确认是否继续。",
                follow_up="说“确认”后我再执行。",
            )

        answer_length = call.meta.get("answer_length", "short")
        # 确认凭据下沉给 VAL（B1）：上面 :269 那道闸保留，形成双检查纵深——
        # 这里是**唯一**能合法把 confirmed=True 交给 VAL 的生产路径（凭据来自
        # `call.meta.confirmed`，由云端确认闭环写入）。
        ok, speech = self.val.execute(
            structured, answer_length=answer_length, confirmed=confirmed)
        if not ok:
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.REJECTED,
                speech=speech,
                error=common_pb2.ErrorInfo(
                    code="safety_gated",
                    message=speech,
                ),
            )

        # 回填动作卡用于 HMI 展示，与本地快路径口径一致。
        # _origin=edge_val 标记“已在车端 VAL 执行”，供 server._dispatch_cloud_actions
        # 跳过二次下发（避免双发）；车控类用 vehicle.control，媒体类用 media.control。
        action_type = action_type_for(obj)
        action = common_pb2.AgentAction(
            type=action_type,
            payload=_struct({
                "command": intent_name,
                **{k: str(v) for k, v in slots.items()},
                "_origin": "edge_val",
            }),
            require_confirm=False,
        )
        return agent_pb2.ExecuteResponse(
            status=agent_pb2.ExecuteResponse.OK,
            speech=speech,
            data=_struct({"intent": intent_name, "executed": True}),
            actions=[action],
        )
