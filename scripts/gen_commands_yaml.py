#!/usr/bin/env python3
"""
Generate orchestrator/edge/knowledge/commands.yaml from Feishu intent data.

Reads all feishu_tblN5NfQff850L5O_*.json files, merges records,
extracts object/operate/mode/attr/unit metadata, and writes a comprehensive
commands.yaml covering every intent.
"""
import json
import glob
import os
import re
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEISHU_GLOB = os.path.join(PROJECT_ROOT, "feishu_tblN5NfQff850L5O_*.json")
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "orchestrator", "edge", "knowledge", "commands.yaml")

# ── Field index mapping (from Feishu schema) ──
IDX = {
    "intent_id": 0,
    "description": 3,
    "skill_l2": 4,
    "network_dep": 6,       # 执行成功网络依赖
    "restriction": 28,      # 限制
    "standard": 36,         # 标准说法
    "high_freq": 50,        # 高频说法
    "data": 41,             # JSON payload
    "object_l3": 53,        # 三级 对象
    "operate_l5": 62,       # 五级 操作
    "intent_type": 73,      # 多意图-指令类型
}

# ── Object name mapping: Feishu data.object → canonical YAML key ──
# Handles both exact matches and angle-bracket template variants.
OBJECT_MAP = {
    # ── 空调 family ──
    "aircon/circulation": "aircon",
    "aircon/cooling": "aircon",
    "aircon/heating": "aircon",
    "aircon/wind": "aircon",
    "fan": "aircon",
    "wind_direction": "aircon",
    "wind_force": "aircon",
    "humidity": "aircon",
    "空调双区": "aircon",
    # ── 座椅 family ──
    "step_heating": "seat",
    # ── 氛围灯 family ──
    "ambient_light/multi": "ambient_light",
    "dynamic_ambient_light": "ambient_light",
    # ── 大灯 family ──
    "high_beam": "headlight",
    "high_beam_assist": "headlight",
    "warning_light": "headlight",
    # ── 雾灯 family ──
    "fog_light/front": "fog_light",
    "fog_light/back": "fog_light",
    # ── 声音/音量 family ──
    "sound": "volume",
    # ── 媒体 family ──
    "sound_effect": "media",
    "DTS_sound_effect": "media",
    "equalizer": "media",
    "acoustic": "media",
    "lyric": "media",
    "playback_mode": "media",
    "playback_speed": "media",
    "singer": "media",
    "playlist": "media",
    "video_resolution": "media",
    # ── 屏幕 family ──
    "screensaver": "screen",
    "display_mode": "screen",
    # ── 页面 family ──
    "launcher": "page",
    "theme": "page",
    "wallpaper": "page",
    # ── 天气 family ──
    "weather_conditions": "weather",
    # ── 导航 family ──
    "navi": "navigation",
    "navigation_announce": "navigation",
    "navigation_preference": "navigation",
    "navigation_route": "navigation",
    "next_guidance": "navigation",
    "map_theme": "navigation",
    "map_view": "navigation",
    "road_condition": "navigation",
    "waypoint": "navigation",
    "waypoint_list": "navigation",
    "remaining_distance": "navigation",
    "remaining_time": "navigation",
    # ── 电话 family ──
    "address_book": "phone",
    "phone_number": "phone",
    "yellow_pages": "phone",
    # ── 蓝牙 family ──
    "bluetooth_auto_connect": "bluetooth",
    "bluetooth_device": "bluetooth",
    "bluetooth_discoverability": "bluetooth",
    # ── 网络连接 family ──
    "wifi": "network_connectivity",
    "hotspot": "network_connectivity",
    "network_data": "network_connectivity",
    "remaining_network_data": "network_connectivity",
    # ── 驾驶模式 family ──
    "vehicle_mode": "driving_mode",
    "energy_mode": "driving_mode",
    "power_mode": "driving_mode",
    # ── 车道辅助 family ──
    "lane_correction_system": "lane_assistance",
    # ── 方向盘 family ──
    "steering_assistant": "steering_wheel",
    # ── 碰撞预警 family ──
    "forward_collision_warning": "collision_warning",
    "predictive_collision_warning": "collision_warning",
    "rear_collision_warning": "collision_warning",
    "reverse_collision_warning/rear_side": "collision_warning",
    "collision_avoidance_radar/front": "collision_warning",
    "collision_warning_sensitivity/front": "collision_warning",
    "traffic_crossing_warning_ahead": "collision_warning",
    # ── 超速提醒 family ──
    "overspeed_sound_warning": "speed_warning",
    "overspeed_vision_warning": "speed_warning",
    "speed_limit_warning": "speed_warning",
    "speed_limit_change_warning_sound": "speed_warning",
    # ── 限速 family ──
    "speed_limit_assistance": "speed_limit",
    "active_speed_limit_control": "speed_limit",
    # ── 巡航 family ──
    "cruise_following": "cruise_control",
    # ── 距离预警 family ──
    "distance_detection_warning": "distance_warning",
    # ── 行人预警 family ──
    "low_speed_pedestrian_warning": "pedestrian_warning",
    # ── 交通标志 family ──
    "traffic_sign_recognition_warning_sound": "traffic_sign_recognition",
    # ── 变道 family ──
    "active_lane_change": "lane_change",
    # ── 车身稳定 family ──
    "body_stability_system": "stability_control",
    # ── 充电 family ──
    "scheduled_charging": "charging",
    "v2v_charging": "charging",
    # ── 胎压 family ──
    "tire_pressure_calibration": "tire_pressure",
    "tire_temperature_monitoring": "tire_pressure",
    "tire_pressure_monitoring": "tire_pressure",
    # ── 能耗 family ──
    "energy_consumption/analysis": "energy_consumption",
    # ── 摄像头 family ──
    "photo": "camera",
    # ── 行车记录仪 family ──
    "dashcam/<录像录音位置>": "dashcam",
    # ── 后视镜 family ──
    "rearview_mirror": "rear_view_mirror",
    "rear_view_mirror/<后视镜位置>": "rear_view_mirror",
    # ── 语音助手 family ──
    "tts_role": "voice_assistant",
    "voice_assistant_speak": "voice_assistant",
    "voice_assistant/continuous_interact": "voice_assistant",
    "voice_assistant/just_talk": "voice_assistant",
    "voice_assistant/wake_word_free": "voice_assistant",
    "voice_assistant/wakeup": "voice_assistant",
    "voice_assistant/wakeup_reply": "voice_assistant",
    "voice_assistant/zone": "voice_assistant",
    # ── 车灯 family (template objects) ──
    "<指定车内灯>": "interior_light",
    "<车内灯>": "interior_light",
    # ── 系统设置 family ──
    "time_format": "system_setting",
    "language": "system_setting",
    "factory_settings": "system_setting",
    "system": "system_setting",
    "system_info": "system_setting",
    # ── 中文对象名 ──
    "一语直达": "one_sentence_direct",
}

# ── Dangerous objects that require confirmation ──
CONFIRM_OBJECTS = {
    "door_lock", "fuel_tank_cover", "charging_port", "trunk",
    "door", "child_lock",
}

# ── Display names for YAML comments ──
DISPLAY_NAMES = {
    "seat": "座椅",
    "window": "车窗",
    "sunroof": "天窗",
    "sunshade": "遮阳帘",
    "aircon": "空调",
    "ambient_light": "氛围灯",
    "low_beam": "近光灯",
    "headlight": "大灯/远光灯/警示灯",
    "fog_light": "雾灯",
    "interior_light": "车内灯",
    "trunk": "后备箱",
    "door_lock": "车门锁",
    "door": "车门",
    "child_lock": "儿童锁",
    "fuel_tank_cover": "油箱盖",
    "charging_port": "充电口盖",
    "rear_view_mirror": "后视镜",
    "steering_wheel": "方向盘",
    "wiper": "雨刷",
    "fragrance": "香氛",
    "tire_pressure": "胎压/胎温监测",
    "dashcam": "行车记录仪",
    "scene_mode": "场景模式",
    "driving_mode": "驾驶/动力模式",
    "energy_recovery": "动能回收",
    "lane_departure_assistance": "车道偏离辅助",
    "lane_assistance": "车道保持/车道纠正",
    "lane_change": "主动变道",
    "accompany_home": "伴我回家",
    "volume": "音量",
    "page": "页面/界面",
    "screen": "屏幕",
    "app": "应用",
    "weather": "天气",
    "media": "媒体/音效",
    "navigation": "导航",
    "phone": "电话/通讯录",
    "bluetooth": "蓝牙",
    "network_connectivity": "网络连接",
    "blind_spot_monitoring": "盲区监测",
    "blind_spot_warning": "盲区预警",
    "collision_warning": "碰撞预警",
    "speed_warning": "超速提醒",
    "speed_limit": "限速辅助",
    "speed_control": "速度控制",
    "cruise_control": "巡航跟车",
    "distance_warning": "距离检测预警",
    "seatbelt_reminder": "安全带提醒",
    "smoking_detection": "吸烟检测",
    "fatigue_detection": "疲劳检测",
    "handheld_phone_detection": "手持电话检测",
    "dangerous_driving_detection": "危险驾驶检测",
    "pedestrian_warning": "低速行人预警",
    "traffic_sign_recognition": "交通标志识别",
    "traffic_notice": "交通通告",
    "hill_descent_control": "陡坡缓降",
    "auto_hold": "自动驻车",
    "creep_mode": "蠕行模式",
    "stability_control": "车身稳定系统",
    "pto": "取力器",
    "battery": "电池",
    "charging": "充电",
    "energy_consumption": "能耗分析",
    "camera": "摄像头/拍照",
    "voice_assistant": "语音助手",
    "memory": "记忆",
    "stock": "股票",
    "train": "火车",
    "flight": "航班",
    "team": "组队",
    "temperature": "温度",
    "system_setting": "系统设置",
    "video_restriction": "视频限制",
    "usb_power": "USB供电",
    "screen_clean": "屏幕清洁",
    "index": "指数",
    "acc": "自适应巡航",
    "vehicle": "车辆",
    "one_sentence_direct": "一语直达",
    "air_quality": "空气质量",
    "TV": "电视",
    "interaction": "通用交互",
}


def load_all_records():
    """Load and merge all Feishu JSON files, deduplicating by intent_id."""
    seen_ids = set()
    all_records = []
    for fp in sorted(glob.glob(FEISHU_GLOB)):
        sz = os.path.getsize(fp)
        if sz == 0:
            print(f"  Skipped {os.path.basename(fp)}: empty file")
            continue
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)
        recs = d["data"]["data"]
        added = 0
        for r in recs:
            rid = r[IDX["intent_id"]] if len(r) > IDX["intent_id"] else None
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            all_records.append(r)
            added += 1
        print(f"  Loaded {os.path.basename(fp)}: {added}/{len(recs)} unique records")
    print(f"  Total unique records: {len(all_records)}")
    return all_records


def safe_json_parse(s):
    """Parse JSON string, handling common formatting issues."""
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        try:
            return json.loads(s.strip().replace("\n", "").replace("\r", ""))
        except json.JSONDecodeError:
            return None


def normalize_object(raw_obj):
    """Map raw data.object to canonical YAML key."""
    if not raw_obj:
        return None
    # Direct match
    if raw_obj in OBJECT_MAP:
        return OBJECT_MAP[raw_obj]
    # Strip angle brackets for template objects like <车内灯>
    clean = raw_obj.strip("<>").strip()
    if clean in OBJECT_MAP:
        return OBJECT_MAP[clean]
    # Check if the cleaned version matches
    if raw_obj.startswith("<") and raw_obj.endswith(">"):
        # Template object, use cleaned version as key
        return clean
    # Use as-is
    return raw_obj


def infer_object(dd):
    """Infer canonical object from data JSON when 'object' key is missing."""
    # Has app key → app object
    if "app" in dd:
        return "app"
    # Has media_info or media_extra → media
    if "media_info" in dd or "media_extra" in dd:
        return "media"
    # Has phone_number → phone
    if "phone_number" in dd:
        return "phone"
    # Has navigation-related keys → navigation
    nav_keys = {"to_poi", "from_poi", "nearby", "to_city", "from_city",
                "province", "district", "road", "pass_by"}
    if nav_keys & set(dd.keys()):
        return "navigation"
    # Has stock_name/stock_code → stock
    if "stock_name" in dd or "stock_code" in dd:
        return "stock"
    # Generic interaction commands → interaction
    op = dd.get("operate", "")
    if op in ("confirm", "cancel", "select", "prev", "next", "skip"):
        return "interaction"
    return None


def build_object_data(records):
    """
    Group records by canonical object, collecting operates/modes/attrs/units
    and metadata flags.
    """
    objects = defaultdict(lambda: {
        "operates": set(),
        "attrs": set(),
        "modes": set(),
        "units": set(),
        "has_positions": False,
        "has_tag": False,
        "online_only": False,
        "offline_ok": True,
        "drive_restricted": False,
        "voice_forbidden": False,
        "require_confirm": False,
        "intent_types": set(),
        "count": 0,
    })

    skipped_no_data = 0
    skipped_no_object = 0

    for r in records:
        data_str = r[IDX["data"]] if len(r) > IDX["data"] else None
        restriction = r[IDX["restriction"]] if len(r) > IDX["restriction"] else None
        network_raw = r[IDX["network_dep"]] if len(r) > IDX["network_dep"] else None
        intent_type_raw = r[IDX["intent_type"]] if len(r) > IDX["intent_type"] else None

        dd = safe_json_parse(data_str)
        if not dd:
            skipped_no_data += 1
            continue

        raw_obj = dd.get("object")
        if not raw_obj:
            # Try to infer object from other fields
            raw_obj = infer_object(dd)
            if not raw_obj:
                skipped_no_object += 1
                continue

        canon = normalize_object(raw_obj)
        if not canon:
            continue

        obj = objects[canon]
        obj["count"] += 1

        # Operates
        op = dd.get("operate")
        if op:
            obj["operates"].add(op)

        # Attrs
        attr = dd.get("attr")
        if attr:
            obj["attrs"].add(attr)

        # Modes
        mode = dd.get("mode")
        if mode:
            obj["modes"].add(str(mode))

        # Units
        unit = dd.get("unit")
        if unit:
            obj["units"].add(unit)

        # Positions (in data JSON)
        positions = dd.get("positions")
        if positions:
            obj["has_positions"] = True

        # Tags (also indicate position-like semantics)
        tag = dd.get("tag")
        if tag:
            obj["has_tag"] = True

        # Network dependency
        if network_raw:
            net_str = str(network_raw)
            if "在线" in net_str and "离线" not in net_str:
                obj["online_only"] = True
                obj["offline_ok"] = False

        # Restriction
        if restriction:
            res_str = str(restriction)
            if "行车中不允许操控" in res_str:
                obj["drive_restricted"] = True
            if "不支持语音操作" in res_str:
                obj["voice_forbidden"] = True

        # Intent type
        if intent_type_raw:
            if isinstance(intent_type_raw, list):
                for it in intent_type_raw:
                    obj["intent_types"].add(str(it))
            else:
                obj["intent_types"].add(str(intent_type_raw))

    print(f"  Skipped {skipped_no_data} records with no parseable data JSON")
    print(f"  Skipped {skipped_no_object} records with no 'object' key")

    # Post-processing
    for key in objects:
        # require_confirm for dangerous objects
        if key in CONFIRM_OBJECTS:
            objects[key]["require_confirm"] = True

    # ── Fallback: add objects from original YAML that are missing from Feishu data ──
    # These were in a now-empty Feishu file; preserve them as known vehicle features.
    FALLBACK_OBJECTS = {
        "fragrance": {
            "operates": {"open", "close", "set"}, "attrs": set(), "modes": set(),
            "units": {"level"}, "has_positions": False, "has_tag": False,
            "online_only": False, "offline_ok": True, "drive_restricted": False,
            "voice_forbidden": False, "require_confirm": False, "intent_types": set(), "count": 0,
        },
        "charging_port": {
            "operates": {"open", "close"}, "attrs": set(), "modes": set(),
            "units": set(), "has_positions": False, "has_tag": False,
            "online_only": False, "offline_ok": True, "drive_restricted": False,
            "voice_forbidden": False, "require_confirm": True, "intent_types": set(), "count": 0,
        },
        "door_lock": {
            "operates": {"open", "close"}, "attrs": set(), "modes": set(),
            "units": set(), "has_positions": True, "has_tag": False,
            "online_only": False, "offline_ok": True, "drive_restricted": False,
            "voice_forbidden": False, "require_confirm": True, "intent_types": set(), "count": 0,
        },
        "dashcam": {
            "operates": {"open", "close", "query"}, "attrs": set(), "modes": set(),
            "units": set(), "has_positions": False, "has_tag": False,
            "online_only": False, "offline_ok": True, "drive_restricted": False,
            "voice_forbidden": False, "require_confirm": False, "intent_types": set(), "count": 0,
        },
        "rear_view_mirror": {
            "operates": {"open", "close", "set", "inc", "dec"}, "attrs": set(),
            "modes": {"fold", "unfold", "heating"}, "units": set(),
            "has_positions": True, "has_tag": False,
            "online_only": False, "offline_ok": True, "drive_restricted": False,
            "voice_forbidden": False, "require_confirm": False, "intent_types": set(), "count": 0,
        },
        "accompany_home": {
            "operates": {"open", "close", "set"}, "attrs": {"second"}, "modes": set(),
            "units": {"second"}, "has_positions": False, "has_tag": False,
            "online_only": False, "offline_ok": True, "drive_restricted": False,
            "voice_forbidden": False, "require_confirm": False, "intent_types": set(), "count": 0,
        },
    }
    for key, fallback in FALLBACK_OBJECTS.items():
        if key not in objects:
            objects[key] = fallback
            print(f"  Added fallback object: {key}")

    return objects


def format_list(items):
    """Format a list for YAML inline: [a, b, c] or []"""
    if not items:
        return "[]"
    return "[" + ", ".join(str(i) for i in items) + "]"


def build_yaml_content(objects_data):
    """Build the YAML content string."""
    lines = []
    lines.append("# commands.yaml — 车控命令 schema（来源：同行者公版语音指令表 6.1 分类表+意图表）")
    lines.append("# 自动生成自 Feishu 意图表，覆盖全部意图记录")
    lines.append("# 每个 object 声明：operates/attrs/modes/positions/units/online/drive_restricted/require_confirm/voice_forbidden")
    lines.append("# positions=true 表示支持位置选择（主驾/副驾/前排/后排/全车）")
    lines.append("")
    lines.append("objects:")

    # Ordering: known vehicle-control objects first, then alphabetical
    primary_order = [
        # 座舱舒适
        "seat", "window", "sunroof", "sunshade", "aircon", "ambient_light",
        "interior_light", "fragrance",
        # 灯光
        "low_beam", "headlight", "fog_light", "accompany_home",
        # 车身
        "trunk", "door_lock", "door", "child_lock", "fuel_tank_cover",
        "charging_port", "rear_view_mirror", "steering_wheel", "wiper",
        # 驾驶/动力
        "driving_mode", "energy_recovery", "scene_mode", "acc", "cruise_control",
        "auto_hold", "creep_mode", "hill_descent_control", "speed_control",
        "pto",
        # ADAS / 安全
        "lane_departure_assistance", "lane_assistance", "lane_change",
        "blind_spot_monitoring", "blind_spot_warning", "collision_warning",
        "speed_warning", "speed_limit", "distance_warning",
        "stability_control", "seatbelt_reminder",
        # 驾驶员监测
        "fatigue_detection", "smoking_detection", "handheld_phone_detection",
        "dangerous_driving_detection", "pedestrian_warning",
        "traffic_sign_recognition", "traffic_notice",
        # 三电
        "battery", "charging", "tire_pressure", "energy_consumption",
        # 声音/媒体
        "volume", "media", "dashcam", "camera",
        # 屏幕/界面
        "screen", "screen_clean", "page", "video_restriction",
        # 语音助手
        "voice_assistant",
        # 通信/连接
        "bluetooth", "phone", "network_connectivity",
        # 信息服务
        "navigation", "weather", "app",
        # 生活服务
        "stock", "train", "flight", "team", "temperature",
        # 系统
        "system_setting", "usb_power", "memory", "vehicle",
        "one_sentence_direct", "air_quality", "TV", "index",
        # 通用交互
        "interaction",
    ]
    new_keys = sorted(k for k in objects_data if k not in primary_order)
    ordered_keys = [k for k in primary_order if k in objects_data] + new_keys

    for key in ordered_keys:
        od = objects_data[key]
        display = DISPLAY_NAMES.get(key, key)

        # Determine online status
        if od["online_only"] and not od["offline_ok"]:
            online = "online_only"
        else:
            online = "offline_ok"

        operates = sorted(od["operates"])
        attrs = sorted(od["attrs"])
        modes = sorted(od["modes"])
        units = sorted(od["units"])

        lines.append("")
        lines.append(f"  # ── {display} ──")
        lines.append(f"  {key}:")
        lines.append(f"    operates: {format_list(operates)}")
        lines.append(f"    attrs: {format_list(attrs)}")
        lines.append(f"    modes: {format_list(modes)}")
        lines.append(f"    positions: {'true' if od['has_positions'] else 'false'}")
        lines.append(f"    units: {format_list(units)}")
        lines.append(f"    online: {online}")
        lines.append(f"    drive_restricted: {'true' if od['drive_restricted'] else 'false'}")
        lines.append(f"    require_confirm: {'true' if od['require_confirm'] else 'false'}")
        lines.append(f"    voice_forbidden: {'true' if od['voice_forbidden'] else 'false'}")

    lines.append("")
    return "\n".join(lines)


def main():
    print("Step 1: Loading Feishu data...")
    records = load_all_records()

    print("\nStep 2: Building object data...")
    objects_data = build_object_data(records)
    print(f"  Found {len(objects_data)} unique objects")

    print("\nStep 3: Generating commands.yaml...")
    yaml_content = build_yaml_content(objects_data)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(yaml_content)

    print(f"\nWrote {OUTPUT_PATH}")
    print(f"Total objects: {len(objects_data)}")

    # Summary
    print("\nObject summary:")
    for key in sorted(objects_data.keys()):
        od = objects_data[key]
        display = DISPLAY_NAMES.get(key, key)
        flags = []
        if od["drive_restricted"]:
            flags.append("DRIVE_RESTRICTED")
        if od["voice_forbidden"]:
            flags.append("VOICE_FORBIDDEN")
        if od["require_confirm"]:
            flags.append("REQUIRE_CONFIRM")
        if od["online_only"]:
            flags.append("ONLINE_ONLY")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        print(f"  {key} ({display}): {od['count']} intents, "
              f"operates={sorted(od['operates'])}{flag_str}")


if __name__ == "__main__":
    main()
