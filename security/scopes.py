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
# M4 P4 视觉入口：**用户显式请求时的单帧**，与 CAMERA_READ（连续流）是两件事。
# 沿 LOCATION_READ / LOCATION_PRECISE 的精度分级先例——把「问一句那是什么」和
# 「持续看着你」区分开，前者可授、后者维持 ❌ 禁（conventions §3 权限表）。
CAMERA_FRAME = "camera.frame"
# 真实商户 MCP（§9.9）：读=查单，写=创建/取消未支付订单。与 PAYMENT_INVOKE 分开——
# 下单和付款是两件事，本项目的商户链**只创建未支付订单**，付款始终另走 payment-gateway。
MERCHANT_READ = "merchant.read"
MERCHANT_WRITE = "merchant.write"

ALL_SCOPES: set[str] = {
    VEHICLE_CONTROL_HVAC, VEHICLE_CONTROL_WINDOW, VEHICLE_CONTROL_SEAT,
    VEHICLE_READ_STATE, LOCATION_READ, LOCATION_PRECISE,
    NAVIGATION_CONTROL, MEDIA_CONTROL, PAYMENT_INVOKE, NETWORK_EXTERNAL,
    PROFILE_READ, PROFILE_WRITE, MICROPHONE_READ, CAMERA_READ, CAMERA_FRAME,
    MERCHANT_READ, MERCHANT_WRITE,
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
        PROFILE_READ, PROFILE_WRITE, CAMERA_FRAME,
        MERCHANT_READ, MERCHANT_WRITE,
    },
    "third_party": {
        VEHICLE_READ_STATE, LOCATION_READ,
        NAVIGATION_CONTROL, MEDIA_CONTROL, PAYMENT_INVOKE, NETWORK_EXTERNAL,
        PROFILE_READ,
        # mcp-bridge 自身就是 third_party（外部服务一律）——商户能力若不在这一档，
        # 硬上限表会把它剥掉，等于把整条真实商户链锁死。它不属于 THIRD_PARTY_DENY
        # 那类（车控/摄像头/麦克风/精确位置）：那些是不可撤销的物理与隐私风险，
        # 商户下单是可确认、可取消、且不含付款的商业写。
        MERCHANT_READ, MERCHANT_WRITE,
    },
}

# third_party 强制禁止的 scope 前缀（即使 token/user_grants 授予了也不生效）
THIRD_PARTY_DENY_PREFIXES: set[str] = {
    VEHICLE_CONTROL_PREFIX, CAMERA_READ, CAMERA_FRAME, LOCATION_PRECISE, MICROPHONE_READ,
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
