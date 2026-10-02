"""Execute cloud-scheduled edge intents through the deterministic VAL."""
from __future__ import annotations

import json

from google.protobuf import struct_pb2

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2

from types import SimpleNamespace

from orchestrator.edge.vehicle_driver import COMMAND_REF
from runtime import effect_evidence
from runtime import operation as operation_contract
from runtime import operation_gate
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
    """Translate an EdgeCall to a VAL command and return Agent response semantics.

    ``dispatch`` is what the cloud reaches (CA2-11): a read-only operation query, the
    capability-contract checks, vehicle-side admission against the operation log, then
    VAL, then the record is settled. ``execute`` is the bare translation without
    admission (VAL-level semantics); production wiring never calls it directly.
    """

    def __init__(self, val: VAL, operation_log=None):
        self.val = val
        self.operation_log = operation_log
        self._contract_manifests = None

    async def dispatch(self, call) -> agent_pb2.ExecuteResponse:
        if getattr(call, "operation_query", ""):
            return self._operation_query(call)
        early = self._preflight(call)
        if early is not None:
            return early
        gate = await self._admit(call)
        if gate.response is not None:
            return gate.response
        try:
            response = self._run(call)
        except BaseException:
            await gate.settle_uncertain("edge_executor_error")
            raise
        await gate.settle(response)
        return response

    async def _admit(self, call):
        """The cloud's admission loop, against the vehicle's log. The subject is the vehicle:
        the log never stores who spoke, and operation IDs are server-minted UUIDs."""
        manifest = next((m for m in self._manifests() for c in m.capabilities
                         if c.intent == call.intent.name), None)
        vehicle_id = self.val.driver.vehicle_id
        agent = SimpleNamespace(manifest=manifest, ledger=self.operation_log)
        request = SimpleNamespace(intent=call.intent, meta=call.meta, session_id="",
                                  context=SimpleNamespace(user_id=f"vehicle:{vehicle_id}",
                                                          vehicle_id=vehicle_id))
        return await operation_gate.admit(agent, request)

    def _operation_query(self, call) -> agent_pb2.ExecuteResponse:
        """What the vehicle's log says about one operation. Read-only: VAL is never touched."""
        from runtime.capability_contract import rejected
        operation_id = call.operation_query
        if call.intent.name or call.intent.slots or not operation_contract.valid_operation_id(operation_id):
            return rejected("ambiguous_operation_query")
        if self.operation_log is None:
            return operation_gate.reject(operation_contract.UNAVAILABLE, operation_id)
        try:
            found = self.operation_log.lookup(operation_id)
        except operation_contract.OperationStoreError:
            return operation_gate.reject(operation_contract.UNAVAILABLE, operation_id)
        record = {"operation_id": operation_id, "decision": "query",
                  "status": found["status"] if found else "absent"}
        if found:
            record["phase"] = found["phase"]
            record["outcome"] = str((found.get("result_ref") or {}).get("outcome") or "")
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK,
                                         data=_struct({"_operation": record}))

    def _manifests(self):
        if self._contract_manifests is None:
            from capabilities import build_edge_manifests
            self._contract_manifests = build_edge_manifests()
        return self._contract_manifests

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

    def _confirm_state(self) -> dict:
        """CA2-09：要求确认那一刻的行驶状态（只在本地输入可用时给；不可用时执行闸本身就会拒绝）。"""
        if not self.val.driver.inputs_available({"speed_kmh", "gear"}):
            return {}
        return {"driving": bool(self.val._is_driving()), "gear": str(self.val.state.get("gear") or "")}

    def _confirm_state_changed(self, raw: str) -> str:
        """确认之后挂挡 / 起步 ⇒ 旧确认不再适用。没带快照 = 旧挂起，兼容放行（执行闸照常把守）。"""
        if not raw:
            return ""
        try:
            confirmed_state = json.loads(raw)
        except (TypeError, ValueError):
            confirmed_state = None
        current = self._confirm_state()
        if not isinstance(confirmed_state, dict) or not current or any(
                confirmed_state.get(key) != current[key] for key in ("driving", "gear")):
            return "车辆状态在您确认之后变了（比如挂挡或起步），为安全起见没有执行，请重新确认。"
        return ""

    def execute(self, call) -> agent_pb2.ExecuteResponse:
        early = self._preflight(call)
        return early if early is not None else self._run(call)

    def _preflight(self, call) -> agent_pb2.ExecuteResponse | None:
        """The capability-version probe and contract checks; None means the call may proceed."""
        from runtime.capability_contract import call_error, capability_digest, HEADER, rejected

        manifests = self._manifests()
        query = getattr(call, "contract_query", "")
        if query:
            if call.intent.name or call.intent.slots:
                return rejected("ambiguous_contract_probe")
            for manifest in manifests:
                for cap in manifest.capabilities:
                    if cap.intent == query:
                        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK,
                            data=_struct({HEADER: capability_digest(manifest, cap), "version": 2}))
            return rejected("capability_removed")
        for manifest in manifests:
            cap = next((c for c in manifest.capabilities if c.intent == call.intent.name), None)
            if cap is not None:
                reason = call_error(manifest, cap, dict(call.meta), dict(call.intent.slots))
                if reason:
                    return rejected(reason)
                break
        return None

    def _run(self, call) -> agent_pb2.ExecuteResponse:
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
            # 确认问句念出**被派下来的这条 intent** 真会做的事（「要关闭后备箱吗？…」）——修前是通用句，
            # 规划把「关闭」错成 `trunk.open` 时用户听不出来（评审四轮，`5ca289c7` RS34）
            # CA2-09：同时报出**问这句话时**的行驶状态；云端把它封进确认，确认派发时原样带回来比对。
            snapshot = self._confirm_state()
            return agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.NEED_CONFIRM,
                speech=self.val.confirm_speech(structured),
                follow_up="说“确认”后我再执行。",
                **({"data": _struct({"confirm_state": snapshot})} if snapshot else {}),
            )
        if confirmed and self.val._need_confirm(obj):
            changed = self._confirm_state_changed(call.meta.get("confirm_state", ""))
            if changed:
                return agent_pb2.ExecuteResponse(
                    status=agent_pb2.ExecuteResponse.REJECTED,
                    speech=changed,
                    error=common_pb2.ErrorInfo(code="confirm_state_changed", message=changed),
                )

        answer_length = call.meta.get("answer_length", "short")
        # CA2-10：云端每次派发一枚观测关联键；本条命令改动的观测样本盖上它，回执说明改了哪些键。
        # 只认格式正确的键——它只用于归属，不是授权，也不是幂等键。
        ref = call.meta.get(effect_evidence.META, "")
        ref = ref if effect_evidence.valid_ref(ref) else ""
        before = dict(self.val.state)
        token = COMMAND_REF.set(ref)
        try:
            # 确认凭据下沉给 VAL（B1）：上面 :269 那道闸保留，形成双检查纵深——
            # 这里是**唯一**能合法把 confirmed=True 交给 VAL 的生产路径（凭据来自
            # `call.meta.confirmed`，由云端确认闭环写入）。
            ok, speech = self.val.execute(
                structured, answer_length=answer_length, confirmed=confirmed)
        finally:
            COMMAND_REF.reset(token)
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
        after = self.val.state
        changed = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
        return agent_pb2.ExecuteResponse(
            status=agent_pb2.ExecuteResponse.OK,
            speech=speech,
            data=_struct({"intent": intent_name, "executed": True,
                          effect_evidence.RECEIPT: effect_evidence.make_receipt(ref, changed)}),
            actions=[action],
        )
