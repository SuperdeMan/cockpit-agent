"""Permission Scope 全集、trust_level 硬上限表、父子覆盖判定。

命名规则：<resource>.<action>[.<sub>]
父 scope 覆盖子：拥有 vehicle.control 即覆盖 vehicle.control.hvac。
"""

# ─── Scope 全集 ───
VEHICLE_CONTROL_HVAC = "vehicle.control.hvac"
VEHICLE_CONTROL_WINDOW = "vehicle.control.window"
VEHICLE_CONTROL_SEAT = "vehicle.control.seat"
VEHICLE_READ_STATE = "vehicle.read.state"
LOCATION_READ = "location.read"
LOCATION_PRECISE = "location.precise"
NAVIGATION_CONTROL = "navigation.control"
MEDIA_CONTROL = "media.control"
PAYMENT_INVOKE = "payment.invoke"
NETWORK_EXTERNAL = "network.external"
PROFILE_READ = "profile.read"
PROFILE_WRITE = "profile.write"
MICROPHONE_READ = "microphone.read"
CAMERA_READ = "camera.read"

ALL_SCOPES: set[str] = {
    VEHICLE_CONTROL_HVAC, VEHICLE_CONTROL_WINDOW, VEHICLE_CONTROL_SEAT,
    VEHICLE_READ_STATE, LOCATION_READ, LOCATION_PRECISE,
    NAVIGATION_CONTROL, MEDIA_CONTROL, PAYMENT_INVOKE, NETWORK_EXTERNAL,
    PROFILE_READ, PROFILE_WRITE, MICROPHONE_READ, CAMERA_READ,
}

# 车控类 scope 前缀
VEHICLE_CONTROL_PREFIX = "vehicle.control"

# ─── trust_level 硬上限 ───
# system: 全部；first_party: 除高危外大部分；third_party: 禁高危车控/精确位置/摄像头麦克风
TRUST_LEVEL_CAPS: dict[str, set[str]] = {
    "system": set(ALL_SCOPES),
    "first_party": {
        VEHICLE_CONTROL_HVAC, VEHICLE_CONTROL_WINDOW, VEHICLE_CONTROL_SEAT,
        VEHICLE_READ_STATE, LOCATION_READ, LOCATION_PRECISE,
        NAVIGATION_CONTROL, MEDIA_CONTROL, PAYMENT_INVOKE, NETWORK_EXTERNAL,
        PROFILE_READ, PROFILE_WRITE,
    },
    "third_party": {
        VEHICLE_READ_STATE, LOCATION_READ,
        NAVIGATION_CONTROL, MEDIA_CONTROL, PAYMENT_INVOKE, NETWORK_EXTERNAL,
        PROFILE_READ,
    },
}

# third_party 强制禁止的 scope 前缀（即使 token/user_grants 授予了也不生效）
THIRD_PARTY_DENY_PREFIXES: set[str] = {
    VEHICLE_CONTROL_PREFIX, CAMERA_READ, LOCATION_PRECISE, MICROPHONE_READ,
}


def is_scope_covered(required: str, effective: set[str]) -> bool:
    """判断 required scope 是否被 effective 集合覆盖（支持父子覆盖）。

    拥有 vehicle.control 覆盖 vehicle.control.hvac；
    拥有 vehicle.control.hvac 不覆盖 vehicle.control.window。
    """
    parts = required.split(".")
    return any(".".join(parts[:i]) in effective for i in range(len(parts), 0, -1))


def deny_third_party(scopes: set[str]) -> set[str]:
    """从 scope 集合中剔除 third_party 禁止的 scope。"""
    return {s for s in scopes if not any(s.startswith(p) for p in THIRD_PARTY_DENY_PREFIXES)}
