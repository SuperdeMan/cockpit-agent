"""Read projection policy. These decisions never authorize a business action.

Opaque owner history/remembered text belongs to profile.read. Sensor acquisition
and structured derived context have separate grants. No secret/config I/O here.
"""
from __future__ import annotations

import json
import time

from runtime.scope import is_scope_covered

READ_SCOPES = {
    "profile": "profile.read",
    "location": "location.read",
    "vehicle_state": "vehicle.read.state",
    "vision": "camera.frame",
}

# Receiver demand and subject authorization are independent intersections.
META_FIELDS = {
    **{key: ("location", "location") for key in (
        "current_lat", "current_lng", "current_accuracy_m", "current_location_at",
        "current_location_source", "focus_destination", "focus_destination_lat",
        "focus_destination_lng", "focus_active_route")},
    "vehicle_battery": ("vehicle_state", "vehicle_state"),
    "vehicle_observation": ("vehicle_state", "vehicle_state"),
    "vision_frame_id": ("vision", "vision"),
    "focus_candidate_set": ("profile", "candidates"),
    "focus_session_constraints": ("profile", "session_constraints"),
    "occupant_name": ("profile", None),
}

VEHICLE_KEYS = ("battery", "speed_kmh", "gear")


def vehicle_projection(meta, vehicle_id, *, now_ms=None):
    """Validate the bounded internal projection, without a legacy KV fallback.

    Origin verification remains in VehicleStateStore. This is an internal RPC
    projection made by that reader, not a new source-signature protocol.
    """
    try:
        raw = meta.get("vehicle_observation", "")
        if not isinstance(raw, str) or len(raw) > 16384:
            return {}, {}
        view = json.loads(raw)
        if view.get("version") != 2 or not vehicle_id or view.get("vehicle_id") != vehicle_id:
            return {}, {}
        now_ms = time.time() * 1000 if now_ms is None else now_ms
        signals, values = {}, {}
        for key in VEHICLE_KEYS:
            signal = view.get("signals", {}).get(key, {})
            expires = signal.get("expires_at_ms")
            if (signal.get("quality") == "good" and signal.get("freshness") == "bounded"
                    and type(expires) in (int, float) and expires > now_ms
                    and key in view.get("state", {})):
                signals[key] = signal
                values[key] = view["state"][key]
        return values, signals
    except (ValueError, TypeError, AttributeError, RecursionError):
        return {}, {}


def grants(ctx) -> set[str]:
    return {v for v in getattr(ctx, "granted_permissions", ()) or () if isinstance(v, str)}


def allowed(ctx, domain: str) -> bool:
    if not is_scope_covered(READ_SCOPES[domain], grants(ctx)):
        return False
    if domain == "profile":
        return bool(getattr(ctx, "user_id", "")) and (
            (getattr(ctx, "prefs", None) or {}).get("memory_enabled", "true") != "false")
    if domain == "vehicle_state":
        return bool(getattr(ctx, "vehicle_id", ""))
    return True


def project_meta(ctx, meta: dict, context_scopes=()) -> dict:
    """Filter *after* all merges so step/sub-agent metadata cannot reintroduce data.

    An omitted receiver declaration is not permission to receive sensor context.
    Safety alerts are enforcement inputs and remain outside the model data policy.
    """
    requested = set(context_scopes or ())
    result = {}
    for key, value in meta.items():
        rule = META_FIELDS.get(key)
        if rule is not None:
            domain, demand = rule
            if not allowed(ctx, domain) or (demand and demand not in requested):
                continue
        result[key] = value
    if "vehicle_state" in requested and allowed(ctx, "vehicle_state"):
        values, signals = vehicle_projection(result, getattr(ctx, "vehicle_id", ""))
        result.pop("vehicle_battery", None)
        result.pop("vehicle_observation", None)
        if values:
            result["vehicle_observation"] = json.dumps({
                "version": 2, "vehicle_id": ctx.vehicle_id, "state": values, "signals": signals},
                separators=(",", ":"))
        if "battery" in values:
            result["vehicle_battery"] = str(values["battery"])
    if not allowed(ctx, "profile"):
        result["memory_enabled"] = "false"
        if "focus_safety_alert" in result:
            try:
                alert = json.loads(result["focus_safety_alert"])
                result["focus_safety_alert"] = json.dumps({k: alert[k] for k in ("level", "ts") if k in alert})
            except (ValueError, TypeError, AttributeError):
                result.pop("focus_safety_alert", None)
    return result
