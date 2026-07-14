"""场景动作词表与校验（D3：编译白名单的**唯一真相源 = VAL 知识库**）。

为什么在这里：0.1.0 的 `scenes.yaml` 自带一份命令词表，与 VAL 的 `commands.yaml` 漂移，
导致场景动作全部静默失效（roadmap §8）。本模块**只加载 VAL 知识库**（构建期 COPY 进镜像，
见 Dockerfile），不再维护第二份对象/操作表——新增车控对象只改 `commands.yaml` 一处。

三件事：
1. **校验**：LLM 编译出的动作逐条过 `validate_action`——对象/操作/参数/模式必须在词表内，
   数值超范围夹紧，不可翻译的动作诚实剔除（回读时告知用户，不静默丢）。
2. **危险动作强制确认**（设计 §8.1）：`require_confirm` 由本模块按对象/模式强制改写，
   **LLM 说了不算**（取 commands.yaml `require_confirm` 与 §8.1 表的并集）。
3. **反向默认表**（D5）：deactivate 时快照缺键的兜底恢复动作。

**对齐目标**：`orchestrator/edge/edge_call.py::action_to_structured` 是场景动作真正的翻译器
（云端动作 → VAL 结构化命令）。本模块的命令拆分/参数别名逐字镜像它——凡本模块判为合法的动作，
必须能被它翻译且被 VAL `_validate_command` 接受。该不变量由 `tests/test_catalog.py` 的契约
测试对着**真实的** edge_call/VAL 钉死，防两侧漂移。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import yaml

logger = logging.getLogger("agent.scene.catalog")

# ── 命令语法（逐字镜像 edge_call）───────────────────────────────────────────
# 对象别名：场景/计划层用友好名，VAL 用协议名。
_OBJECT_ALIASES = {"hvac": "aircon", "tire_pressure": "tire_pressure_monitoring"}
# 操作归一（edge_call._normalize_operation）。
_OPERATE_ALIASES = {"on": "open", "off": "close", "play": "start",
                    "next": "switch", "prev": "switch", "fold": "set", "unfold": "set"}
# 无法由 <object>.<operate> 直接拆出的命令（edge_call._COMMAND_OVERRIDES）。
_COMMAND_OVERRIDES = {"seat.recline": ("seat", "set", "", "recline")}
# 三段式命令的中段 → VAL attr（edge_call._to_structured 的 attribute 映射）。
_ATTR_PATHS = {
    ("aircon", "wind_speed"): "speed",
    ("screen", "brightness"): "brightness",
    ("steering_wheel", "height"): "height",
    ("wiper", "speed"): "speed",
}
# 友好参数名 → VAL data 字段（edge_call._ACTION_PARAM_ALIASES + 量级归一 for-loop）。
# **只有这几个名字能真正抵达 VAL 的 data.value**——用别的名字（speed/height）传数值，
# edge_call 不会归一到 value，VAL 静默不生效。故这就是参数词表的全集。
_MAGNITUDE_PARAMS = ("temperature", "level", "brightness", "angle")
_PARAM_TO_VAL = {"color": "tag", "position": "positions", "angle": "value",
                 "level": "value", "brightness": "value", "temperature": "value",
                 "mode": "mode"}
# 量级参数**归一**：这几个名字对 VAL 是等价的（都归到 data.value），但 LLM 每次挑的不一样
# （「氛围灯调到10%」可能编成 brightness=10 也可能 level=10）。落库前统一到该对象的规范名——
# 否则 P2 的 assert 断言/幂等跳过/快照恢复会因参数名漂移而对不上，回读话术也会渲染成空。
# 只对"无 attr 段的两段式命令"生效（aircon.wind_speed.set 的 level 是风速，不能归成温度）。
_MAGNITUDE_CANON = {"aircon": "temperature", "ambient_light": "brightness",
                    "screen": "brightness", "volume": "level"}

# ── 场景层策略（非词表，可在此维护）────────────────────────────────────────
# 值域开放的对象：scene_mode 的模式就是"当前场景"，用户可造场景（D1）→ 值域天然开放，
# commands.yaml 用 `modes: []` 显式声明"不做枚举校验"，权威场景集在本 Agent（store+scenes.yaml）。
_OPEN_MODE_OBJECTS = {"scene_mode"}
# 媒体类对象（与 edge_call._MEDIA_OBJECTS 同集）：action.type 用 media.control。
# P1.4 已放开——端侧 `_dispatch_cloud_actions` 现在同时回流 media.control，媒体动作能真正落地
# （此前场景里的「放舒缓音乐」只能编译期剔除，浪漫模式一直没有音乐）。
_MEDIA_OBJECTS = {"media", "music", "radio", "online_radio", "audiobook",
                  "opera", "news", "video", "TV"}
# 进 prompt 摘要的媒体对象：**只有这两个真能落地**——edge_call 把 `play` 归一成 `start`，
# 而 music/audiobook/video 都没声明 `start` 操作，`music.play` 会被 VAL 直接拒（实测「暂不
# 支持哦」）。能起播的只有 media（声明了 start）；radio 走 open/close。契约测试钉死这条。
_MEDIA_IN_DIGEST = {"media", "radio"}
# 不进场景词表的对象：交互元对象 / 需 target 参数而词表无声明 / 纯查询。
_EXCLUDED_OBJECTS = {"interaction", "navigation", "map", "page", "app", "system",
                     "phone", "contacts", "call_log"}
# 可控操作（"纯查询对象"不进场景词表）。含媒体动词——媒体对象只有 play/pause/stop 这类操作，
# 不列进来的话 digest 会把 music 渲染成只有 close/switch，等于把「放音乐」藏起来了。
_CONTROL_OPS = {"open", "close", "set", "switch", "inc", "dec",
                "play", "pause", "stop", "resume", "start"}

# §8.1 危险动作强制确认表：与 commands.yaml 的 require_confirm 取并集。
_DANGER_OBJECTS = {"trunk", "frunk", "door_lock", "charging_port", "fuel_tank_cover"}
_DANGER_SEAT_MODES = {"recline", "放平", "座椅放平"}      # 座椅位移（加热/通风不算）

# 数值夹紧范围（obj, 友好参数名）→ (lo, hi)。commands.yaml 只声明单位不声明范围，
# 故此表是**范围**知识（非对象/操作词表，不违反 D3），与 VAL `_simulate` 的行为对齐。
_RANGES = {
    ("aircon", "temperature"): (16, 32), ("aircon", "level"): (0, 10),
    ("ambient_light", "brightness"): (0, 100), ("ambient_light", "level"): (0, 100),
    ("volume", "level"): (0, 100),
    ("screen", "brightness"): (0, 100), ("screen", "level"): (0, 100),
    ("window", "level"): (0, 100), ("sunroof", "level"): (0, 100),
    ("sunshade", "level"): (0, 100),
    ("seat", "angle"): (90, 180), ("seat", "temperature"): (0, 3),
    ("seat", "level"): (0, 3),
    ("wiper", "level"): (0, 10), ("steering_wheel", "level"): (0, 10),
    ("fragrance", "level"): (1, 5), ("energy_recovery", "level"): (0, 3),
}
_RANGE_DEFAULT = (0, 100)

# prompt 可读性用的中文标签（**非词表**：缺失回退英文对象名，不影响任何校验）。
_LABELS = {
    "aircon": "空调", "seat": "座椅", "window": "车窗", "sunroof": "天窗",
    "sunshade": "遮阳帘", "ambient_light": "氛围灯", "headlight": "大灯",
    "trunk": "后备箱", "frunk": "前备箱", "door_lock": "车门锁", "volume": "音量",
    "fragrance": "香氛", "wiper": "雨刮", "screen": "屏幕", "steering_wheel": "方向盘",
    "rear_view_mirror": "后视镜", "driving_mode": "驾驶模式", "power_mode": "动力模式",
    "energy_recovery": "动能回收", "air_purifier": "空气净化", "navi_broadcast": "导航播报",
    "key_tone": "按键音", "dashcam": "行车记录仪", "accompany_home": "伴我回家",
    "charging_port": "充电口盖", "fuel_tank_cover": "油箱盖", "auto_hold": "自动驻车",
    "epb": "电子手刹", "bluetooth": "蓝牙", "wifi": "WiFi", "hotspot": "热点",
    "equalizer": "均衡器", "voice_assistant": "语音助手", "surround_view": "全景影像",
    "dashboard": "仪表", "lane_assistance": "车道保持",
    "music": "音乐", "radio": "收音机", "media": "音乐播放",
    "lane_departure_assistance": "车道偏离预警",
}

# 对象 → VAL 状态镜像里的键（快照 / assert 断言 / 恢复用）。
# 来源：`orchestrator/edge/val.py::_simulate` 实际写入的 self.state 键。
_STATE_KEYS = {
    "aircon": ("hvac_on", "hvac_temp", "hvac_wind_speed"),
    "ambient_light": ("ambient_light", "ambient_light_brightness", "ambient_light_color"),
    "volume": ("volume",),
    "seat": ("seat_recline", "seat_heating", "seat_ventilation"),
    "fragrance": ("fragrance",),
    "window": ("window",), "sunroof": ("sunroof",), "sunshade": ("sunshade",),
    "headlight": ("headlight",), "wiper": ("wiper", "wiper_speed"),
    "trunk": ("trunk",), "door_lock": ("door_lock",),
    "fuel_tank_cover": ("fuel_tank_cover",), "charging_port": ("charging_port",),
    "rear_view_mirror": ("rear_view_mirror",),
    "steering_wheel": ("steering_wheel_heating", "steering_wheel_height"),
    "screen": ("screen_brightness",), "driving_mode": ("driving_mode",),
    "scene_mode": ("scene_mode",), "energy_recovery": ("energy_recovery",),
    "accompany_home": ("accompany_home",),
    # 媒体类对象在 VAL 里共用一个 media 状态键（val.py::_simulate）
    "media": ("media",), "music": ("media",), "radio": ("media",),
    "online_radio": ("media",), "audiobook": ("media",), "opera": ("media",),
    "news": ("media",), "video": ("media",), "TV": ("media",),
}

# 环境条件 key 白名单的非车身部分（P2 `when`/`guards` 用）。
ENV_KEYS = ("battery", "gear", "speed_kmh", "location.city", "hour", "cabin_temp")


class CatalogError(RuntimeError):
    """词表缺失/损坏。诚实抛错——静默空词表会让所有动作被判非法，比崩溃更难查。"""


@dataclass
class Catalog:
    objects: dict          # commands.yaml 的 objects
    entities: dict         # entities.yaml（模式/位置/颜色归一字典）
    path: str = ""

    def obj(self, name: str) -> dict | None:
        return self.objects.get(name)

    def positions(self) -> set[str]:
        pos = self.entities.get("positions") or {}
        out: set[str] = set(pos.keys())
        for v in pos.values():
            out.update(v if isinstance(v, list) else [v])
        return out

    def colors(self) -> set[str]:
        c = self.entities.get("light_colors") or {}
        return set(c.keys()) | set(c.values())

    def state_keys(self) -> set[str]:
        """vehicle_state 镜像里可被条件/断言引用的键全集（车身键 + 动态量）。"""
        keys: set[str] = set()
        for obj_name in self.objects:
            keys.update(_STATE_KEYS.get(obj_name, ()))
        keys.update(("speed_kmh", "gear", "battery", "location"))
        return keys

    def condition_keys(self) -> set[str]:
        """P2 `when`/`guards`/`assert` 的 key 白名单（幻觉键 → 编译期剔除）。"""
        return self.state_keys() | set(ENV_KEYS)


def _knowledge_candidates(path: str | None) -> list[str]:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # agents/scene_orchestrator
    root = os.path.dirname(os.path.dirname(here))                        # 仓库根
    return [p for p in (
        path,
        os.getenv("SCENE_CATALOG_DIR", ""),
        os.path.join(here, "knowledge"),                                 # 镜像内（Dockerfile COPY）
        os.path.join(root, "orchestrator", "edge", "knowledge"),         # 本地开发 / CI 回退
    ) if p]


def load_catalog(path: str | None = None) -> Catalog:
    """加载 VAL 知识库目录。路径序：显式参数 → SCENE_CATALOG_DIR → 镜像内 → 仓库相对。

    找不到 commands.yaml → 抛 CatalogError（不静默返回空词表）。
    """
    for d in _knowledge_candidates(path):
        cmd_path = os.path.join(d, "commands.yaml")
        if not os.path.isfile(cmd_path):
            continue
        with open(cmd_path, encoding="utf-8") as f:
            commands = yaml.safe_load(f) or {}
        objects = commands.get("objects") or {}
        if not objects:
            raise CatalogError(f"词表 {cmd_path} 无 objects 节")
        entities = {}
        ent_path = os.path.join(d, "entities.yaml")
        if os.path.isfile(ent_path):
            with open(ent_path, encoding="utf-8") as f:
                entities = yaml.safe_load(f) or {}
        logger.info("scene catalog: 已加载 %d 个对象（%s）", len(objects), cmd_path)
        return Catalog(objects=objects, entities=entities, path=cmd_path)
    raise CatalogError(
        "找不到 VAL 词表 commands.yaml（查找序：SCENE_CATALOG_DIR → 镜像 knowledge/ → "
        "orchestrator/edge/knowledge/）。构建期未 COPY 词表进镜像？")


# ── 命令解析 ────────────────────────────────────────────────────────────────

def resolve_command(command: str) -> tuple[str, str, str, str] | None:
    """`<object>[.<path>].<operate>` → (object, operate, attr, mode)；不可解析 → None。

    逐字镜像 edge_call：对象/操作别名、seat.recline 覆盖、三段式中段的 attr/mode 归属。
    """
    command = (command or "").strip()
    if command in _COMMAND_OVERRIDES:
        return _COMMAND_OVERRIDES[command]
    parts = [p for p in command.split(".") if p]
    if len(parts) < 2:
        return None
    obj = _OBJECT_ALIASES.get(parts[0], parts[0])
    operate = _OPERATE_ALIASES.get(parts[-1], parts[-1])
    path = ".".join(parts[1:-1])
    attr = _ATTR_PATHS.get((obj, path), "")
    mode = "" if attr else path
    return obj, operate, attr, mode


def allowed_params(name: str, d: dict) -> set[str]:
    """该对象接受的**友好参数名**——只列真正能抵达 VAL 的（见 _MAGNITUDE_PARAMS 注释）。"""
    attrs = set(d.get("attrs") or [])
    units = set(d.get("units") or [])
    ps: set[str] = set()
    if "temperature" in attrs:
        ps.add("temperature")
    if "color" in attrs:
        ps.add("color")
    if "brightness" in attrs:
        ps.add("brightness")
    # 通用量级：对象有 level/percent 单位，或有 speed/height/second 这类数值属性
    # （这些属性名本身不会被 edge_call 归一到 value，必须用 level 传）。
    if (units & {"level", "percent"}) or (attrs & {"speed", "height", "second", "level"}):
        ps.add("level")
    if name == "seat":
        ps.add("angle")                                      # 座椅放平角度（angle→value）
    if d.get("modes") or name in _OPEN_MODE_OBJECTS:
        ps.add("mode")
    if d.get("positions"):
        ps.add("position")
    return ps


def normalize_mode(cat: Catalog, obj: str, mode: str) -> str | None:
    """mode 合法 → 返回原值（VAL 自己经 entities 归一，保持同一条路径）；非法 → None。"""
    if obj in _OPEN_MODE_OBJECTS:
        return mode or None                                  # 值域开放（用户造场景）
    d = cat.obj(obj) or {}
    modes = d.get("modes") or []
    if not modes:
        return None
    if mode in modes:
        return mode
    for cate in ("seat_modes", "aircon_modes", "driving_modes", "scene_modes", "wind_modes"):
        mapped = (cat.entities.get(cate) or {}).get(mode)
        if mapped and mapped in modes:
            return mode
    return None


def is_media(obj: str) -> bool:
    """媒体类对象 → action.type 用 media.control（口径同 edge_call.action_type_for）。"""
    return obj in _MEDIA_OBJECTS


# 车控类动作的两种 action.type（都经端侧 `_dispatch_cloud_actions` → VAL 结构化流水线）。
CAR_TYPES = ("vehicle.control", "media.control")


def action_type_for(obj: str) -> str:
    return "media.control" if is_media(obj) else "vehicle.control"


def _is_car_action(a: dict) -> bool:
    return (a.get("type") or "vehicle.control") in CAR_TYPES


def is_danger(obj: str, operate: str, mode: str, cat: Catalog) -> bool:
    """§8.1 危险动作判定（commands.yaml require_confirm ∪ 场景层危险表）。"""
    if (cat.obj(obj) or {}).get("require_confirm"):
        return True
    if obj in _DANGER_OBJECTS:
        return True
    if obj == "seat" and mode in _DANGER_SEAT_MODES:
        return True
    if obj == "window" and operate in ("open", "set", "inc"):
        return True
    return False


def _clamp(obj: str, key: str, raw) -> tuple[str, bool]:
    """数值参数夹紧到合理区间。返回 (夹紧后的字符串, 是否被夹紧)。非数值原样返回。"""
    try:
        v = float(str(raw).strip().rstrip("%°度档级"))
    except (TypeError, ValueError):
        return str(raw), False
    lo, hi = _RANGES.get((obj, key), _RANGE_DEFAULT)
    c = max(lo, min(hi, v))
    return (str(int(c)) if c == int(c) else str(c)), c != v


# ── 动作校验 ────────────────────────────────────────────────────────────────

def validate_action(action: dict, cat: Catalog) -> tuple[bool, dict | None, str]:
    """校验并清洗一条场景动作。返回 (ok, cleaned, reason)。

    ok=False 时 reason 是**给用户看的中文原因**（回读时诚实告知"这条我做不到"）。
    cleaned 的 `require_confirm` 由 §8.1 强制改写——LLM 的声明不作数。
    """
    if not isinstance(action, dict):
        return False, None, "动作格式不对"
    a_type = (action.get("type") or "vehicle.control").strip()

    if a_type == "navigate":
        payload = dict(action.get("payload") or action.get("params") or {})
        dest = str(payload.get("destination") or "").strip()
        if not dest:
            return False, None, "导航动作缺目的地"
        return True, {"type": "navigate", "payload": {"destination": dest},
                      "require_confirm": False}, ""

    if a_type not in CAR_TYPES:
        return False, None, f"暂不支持 {a_type} 类动作"

    command = str(action.get("command") or "").strip()
    if not command:
        return False, None, "动作缺 command"
    resolved = resolve_command(command)
    if resolved is None:
        return False, None, f"「{command}」不是有效的车控指令"
    obj, operate, attr, path_mode = resolved

    d = cat.obj(obj)
    if d is None:
        return False, None, f"车上没有「{obj}」这个可控对象"
    if d.get("voice_forbidden"):
        return False, None, f"「{_LABELS.get(obj, obj)}」不支持语音控制"

    operates = set(d.get("operates") or [])
    if operate not in operates and not (operate in ("inc", "dec") and "set" in operates):
        raw_op = command.split(".")[-1]          # 用户/LLM 写的原词（别回「不支持 start」）
        return False, None, f"「{_LABELS.get(obj, obj)}」不支持「{raw_op}」操作"
    if attr and attr not in set(d.get("attrs") or []):
        return False, None, f"「{_LABELS.get(obj, obj)}」没有「{attr}」属性"

    # 参数清洗
    allowed = allowed_params(obj, d)
    params: dict[str, str] = {}
    notes: list[str] = []
    mode = path_mode
    # 量级参数规范名（brightness/level/temperature 对 VAL 等价，但下游要稳定）——
    # **必须在夹紧之前归一**：否则 hvac.set{level:22} 会先被风速的 0~10 区间夹成 10 再改名成温度。
    canon = _MAGNITUDE_CANON.get(obj) if not attr else None
    for k, v in (action.get("params") or {}).items():
        k = str(k).strip()
        if k in ("command", "_origin"):
            continue
        if canon and canon in allowed and k in _MAGNITUDE_PARAMS and k != canon:
            k = canon
        if k not in allowed:
            notes.append(f"忽略了不适用的参数「{k}」")
            continue
        if k == "mode":
            m = normalize_mode(cat, obj, str(v).strip())
            if m is None:
                return False, None, (f"「{_LABELS.get(obj, obj)}」没有「{v}」这个模式")
            mode = m
            params["mode"] = m
            continue
        if k == "position":
            p = str(v).strip()
            if p not in cat.positions():
                notes.append(f"忽略了无效位置「{p}」")
                continue
            params["position"] = p
            continue
        if k == "color":
            c = str(v).strip()
            if c not in cat.colors():
                notes.append(f"忽略了无效颜色「{c}」")
                continue
            params["color"] = c
            continue
        val, clamped = _clamp(obj, k, v)
        if clamped:
            notes.append(f"「{_LABELS.get(obj, obj)}」的{k}超出范围，已调整为 {val}")
        params[k] = val

    # path_mode（三段式命令中段，如 steering_wheel.heating.open）也要过模式校验
    if path_mode and "mode" not in params:
        if normalize_mode(cat, obj, path_mode) is None:
            return False, None, f"「{_LABELS.get(obj, obj)}」没有「{path_mode}」这个模式"

    # set/inc/dec 清洗后无参数 = 空操作（VAL 会静默不生效）→ 诚实剔除，别让用户以为做了
    if operate in ("set", "inc", "dec") and not params and not path_mode:
        return False, None, f"「{command}」缺少必要参数"

    cleaned = {"type": action_type_for(obj), "command": command, "params": params,
               "require_confirm": is_danger(obj, operate, mode, cat)}
    return True, cleaned, "；".join(notes)


# ── 快照 / 恢复（D5）────────────────────────────────────────────────────────

def affected_state_keys(action: dict) -> tuple[str, ...]:
    """该动作会改动的 vehicle_state 键（激活前按此采快照；P2 的 assert 也用它）。"""
    if not _is_car_action(action):
        return ()
    resolved = resolve_command(str(action.get("command") or ""))
    if resolved is None:
        return ()
    obj, _operate, _attr, mode = resolved
    params = action.get("params") or {}
    if obj == "seat":
        m = str(params.get("mode") or mode or "")
        if m in _DANGER_SEAT_MODES:
            return ("seat_recline",)
        if m in ("heating", "加热"):
            return ("seat_heating",)
        if m in ("ventilation", "通风"):
            return ("seat_ventilation",)
    return _STATE_KEYS.get(obj, ())


def _int_or(v, default: int) -> int:
    try:
        return int(float(str(v).replace("%", "")))
    except (TypeError, ValueError):
        return default


def restore_action(action: dict, snapshot: dict,
                   cat: Catalog | None = None) -> tuple[dict | None, str]:
    """按快照（缺键退反向默认表，D5）生成恢复动作。返回 (action|None, note)。

    None = 该对象没有可靠的恢复语义 → 调用方在话术里诚实说明"没法自动还原"。
    恢复动作的 `require_confirm` 同样经 §8.1 强制标注（D5：恢复里含座椅等危险类照走确认）。
    """
    if not _is_car_action(action):
        return None, ""
    resolved = resolve_command(str(action.get("command") or ""))
    if resolved is None:
        return None, ""
    obj, _operate, _attr, mode = resolved
    params = action.get("params") or {}
    snap = snapshot or {}

    def act(command: str, p: dict | None = None) -> dict:
        r = resolve_command(command)
        danger = bool(cat and r and is_danger(r[0], r[1], (p or {}).get("mode", r[3]), cat))
        return {"type": action_type_for(r[0]) if r else "vehicle.control",
                "command": command, "params": p or {}, "require_confirm": danger}

    if obj == "aircon":
        if snap.get("hvac_on") is False:
            return act("hvac.close"), ""
        temp = snap.get("hvac_temp")
        return act("hvac.set", {"temperature": str(_int_or(temp, 24))}), ""

    if obj == "ambient_light":
        if snap.get("ambient_light") in (False, None):
            return act("ambient_light.close"), ""
        p: dict[str, str] = {}
        if snap.get("ambient_light_brightness") is not None:
            p["brightness"] = str(_int_or(snap["ambient_light_brightness"], 60))
        if snap.get("ambient_light_color"):
            p["color"] = str(snap["ambient_light_color"])
        return (act("ambient_light.set", p) if p else act("ambient_light.open")), ""

    if obj == "volume":
        return act("volume.set", {"level": str(_int_or(snap.get("volume"), 50))}), ""

    if obj == "seat":
        m = str(params.get("mode") or mode or "")
        if m in _DANGER_SEAT_MODES:
            angle = _int_or(snap.get("seat_recline"), 90)
            if angle < 90:                       # 快照里是 True/非角度值 → 退复位默认
                angle = 90
            p = {"angle": str(angle)}
            if params.get("position"):
                p["position"] = str(params["position"])
            return act("seat.recline", p), ""
        return None, "座椅加热/通风没法自动还原"

    if obj == "fragrance":
        return act("fragrance.open" if snap.get("fragrance") else "fragrance.close"), ""

    if obj in ("window", "sunroof"):
        cur = snap.get(obj)
        if cur in (None, "closed", "close"):
            return act(f"{obj}.close"), ""
        if cur == "open":
            return act(f"{obj}.open"), ""
        return act(f"{obj}.set", {"level": str(_int_or(cur, 0))}), ""

    if obj == "screen":
        b = snap.get("screen_brightness")
        if b is None:
            return None, ""
        return act("screen.brightness.set", {"level": str(_int_or(b, 60))}), ""

    if obj in ("headlight", "wiper", "sunshade"):
        on = bool(snap.get(obj))
        return act(f"{obj}.{'open' if on else 'close'}"), ""

    if obj in _MEDIA_OBJECTS:
        # 精确还原到激活前的播放态：paused ≠ stopped（VAL 两者都建模了，别把暂停还原成停止）
        cur = snap.get("media")
        if cur == "playing":
            return act("media.play"), ""
        if cur == "paused":
            return act("media.pause"), ""
        return act("media.close"), ""

    return None, f"「{_LABELS.get(obj, obj)}」没法自动还原"


# ── LLM prompt 词表摘要（D3：白名单原样喂给编译器）─────────────────────────

def scene_objects(cat: Catalog) -> dict:
    """可进场景的对象子集：可控（非纯查询）、非语音禁用、非媒体、非元对象、非 Agent 自管。"""
    out = {}
    for name, d in cat.objects.items():
        if name in _EXCLUDED_OBJECTS or name in _OPEN_MODE_OBJECTS:
            continue          # scene_mode 由 Agent 自己追加，不给 LLM 造
        if name in _MEDIA_OBJECTS and name not in _MEDIA_IN_DIGEST:
            continue          # 媒体对象只推荐 music/radio（其余仍合法，只是不进摘要）
        if d.get("voice_forbidden"):
            continue
        if not (set(d.get("operates") or []) & _CONTROL_OPS):
            continue
        out[name] = d
    return out


def catalog_digest(cat: Catalog, max_modes: int = 8) -> str:
    """紧凑渲染白名单供 LLM 编译（设计 §5.1）。控制在 ~2000 字内。"""
    lines: list[str] = []
    for name, d in scene_objects(cat).items():
        label = _LABELS.get(name, "")
        declared = set(d.get("operates") or [])
        # 只渲染**归一后仍在词表里**的操作：edge_call 把 play 归一成 start，radio 没声明 start
        # → `radio.play` 其实会被 VAL 拒。摘要里摆一个执行不了的操作 = 引诱 LLM 编出废动作。
        ops = "/".join(o for o in (d.get("operates") or [])
                       if o in _CONTROL_OPS and _OPERATE_ALIASES.get(o, o) in declared)
        bits: list[str] = []
        for p in sorted(allowed_params(name, d)):
            if p == "mode":
                modes = [m for m in (d.get("modes") or [])][:max_modes]
                bits.append("mode=" + "|".join(modes))
            elif p == "position":
                bits.append("position=front_left|front_right|rear_left|rear_right|all")
            elif p == "color":
                bits.append("color=" + "|".join(sorted(set(
                    (cat.entities.get("light_colors") or {}).values()))))
            else:
                lo, hi = _RANGES.get((name, p), _RANGE_DEFAULT)
                bits.append(f"{p}={lo}~{hi}")
        head = f"{name}({label})" if label else name
        lines.append(f"- {head}: {ops}" + (f" | 参数 {', '.join(bits)}" if bits else ""))
    return "\n".join(lines)
