"""Capability v2 declarations, migration admission and call-version checks.

Pure local validation. No model calls, permission grants, retries or unit conversion.
The migration inventory freezes old public interfaces; it is not a routing table.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from inspect import getattr_static
from functools import lru_cache
from pathlib import Path
from google.protobuf.message import Message

HEADER = "capability_contract_sha256"
EFFECTS = frozenset({"read", "information_task", "state_change", "external_write"})
STATE_EFFECTS = frozenset({"state_change", "external_write"})
_TYPES = frozenset({"string", "integer", "number", "boolean", "object", "array"})
_PRECONDITIONS = frozenset({"permission", "confirmation", "handler", "val"})
_REQUIRED = {"version", "revision", "effect", "parameters", "additional_parameters",
             "applicability", "preconditions", "idempotency", "verification"}
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}\Z")
_MIGRATION = Path(__file__).with_name("capability_migration.json")


class ContractError(ValueError):
    """A controlled reason code. Never include argument values in diagnostics."""


def _digest(value) -> str:
    def canonical(x):
        if isinstance(x, dict):
            return {k: canonical(v) for k, v in x.items()}
        if isinstance(x, list):
            return [canonical(v) for v in x]
        if isinstance(x, float) and math.isfinite(x) and x.is_integer():
            return int(x)
        return x
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _mapping(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if issubclass(type(value), Message):
        from google.protobuf.json_format import MessageToDict
        return MessageToDict(value, preserving_proto_field_name=True)
    raise ContractError("invalid_contract_object")


def _strings(value, *, limit=64) -> list[str]:
    if not isinstance(value, list) or len(value) > limit or any(
            not isinstance(x, str) or not x or len(x) > 128 for x in value):
        raise ContractError("invalid_contract_list")
    return sorted(set(value))


def normalize(raw) -> dict:
    data = _mapping(raw)
    if set(data) != _REQUIRED or isinstance(data.get("version"), bool) or data.get("version") != 2:
        raise ContractError("unsupported_contract_schema")
    revision = data["revision"]
    if not isinstance(revision, str) or not _NAME.fullmatch(revision):
        # Revisions may also start with a digit, like semantic versions.
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9][A-Za-z0-9_.-]{0,127}", revision):
            raise ContractError("invalid_contract_revision")
    if not isinstance(data["effect"], str) or data["effect"] not in EFFECTS:
        raise ContractError("invalid_contract_effect")
    if not isinstance(data["additional_parameters"], str) or data["additional_parameters"] not in {"reject", "legacy"}:
        raise ContractError("invalid_parameter_policy")
    if not isinstance(data["idempotency"], str) or data["idempotency"] not in {"unknown", "none", "idempotent", "keyed"}:
        raise ContractError("invalid_idempotency_declaration")
    if not isinstance(data["verification"], str) or data["verification"] not in {"none", "declared"}:
        raise ContractError("invalid_verification_declaration")
    conditions = _strings(data["preconditions"])
    if not set(conditions) <= _PRECONDITIONS:
        raise ContractError("unsupported_precondition")
    app = data["applicability"]
    if not isinstance(app, dict) or set(app) != {"status", "vehicle_models", "software_versions"}:
        raise ContractError("invalid_applicability")
    if not isinstance(app["status"], str) or app["status"] not in {"unspecified", "not_vehicle_specific", "declared"}:
        raise ContractError("invalid_applicability")
    app = {"status": app["status"], "vehicle_models": _strings(app["vehicle_models"]),
           "software_versions": _strings(app["software_versions"])}
    if app["status"] != "declared" and (app["vehicle_models"] or app["software_versions"]):
        raise ContractError("contradictory_applicability")
    if app["status"] == "declared" and not (app["vehicle_models"] or app["software_versions"]):
        raise ContractError("empty_applicability")
    params = data["parameters"]
    if not isinstance(params, dict) or len(params) > 64:
        raise ContractError("invalid_parameters")
    normalized = {}
    for name, spec in params.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name) or not isinstance(spec, dict):
            raise ContractError("invalid_parameter")
        if (set(spec) - {"type", "unit", "region", "minimum", "maximum"}
                or not isinstance(spec.get("type"), str) or spec["type"] not in _TYPES):
            raise ContractError("invalid_parameter_type")
        item = {"type": spec["type"]}
        for key in ("unit", "region"):
            value = spec.get(key, "")
            if not isinstance(value, str) or len(value) > 128:
                raise ContractError("invalid_parameter_annotation")
            item[key] = value
        for key in ("minimum", "maximum"):
            if key in spec:
                value = spec[key]
                if spec["type"] not in {"integer", "number"} or isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ContractError("invalid_parameter_bound")
                try:
                    value = float(value)
                except (ValueError, OverflowError):
                    raise ContractError("invalid_parameter_bound") from None
                if not math.isfinite(value):
                    raise ContractError("invalid_parameter_bound")
                item[key] = value
        if item.get("minimum", -math.inf) > item.get("maximum", math.inf):
            raise ContractError("invalid_parameter_bound")
        normalized[name] = item
    result = {**data, "version": 2, "parameters": normalized,
              "applicability": app, "preconditions": conditions}
    if len(json.dumps(result, ensure_ascii=False).encode()) > 16 * 1024:
        raise ContractError("contract_too_large")
    return result


def raw_contract(cap):
    if isinstance(cap, dict):
        return cap.get("contract")
    if issubclass(type(cap), Message):
        return cap.contract if "contract" in cap.DESCRIPTOR.fields_by_name and cap.HasField("contract") else None
    # Attribute-proxy objects must not fabricate an absent optional field merely
    # because getattr() asks for it. Explicit malformed fields still fail closed.
    if getattr_static(cap, "contract", None) is None:
        return None
    return getattr(cap, "contract")


def contract_of(cap) -> dict:
    raw = raw_contract(cap)
    return normalize(raw) if raw is not None else {}


def abi_digest(manifest, cap) -> str:
    """Semantic wire fields only; descriptions/examples do not change the ABI."""
    fields = {k: getattr(cap, k, None) for k in ("intent", "effect")}
    fields.update({k: bool(getattr(cap, k, False)) for k in
                   ("require_confirm", "response_only", "whole_utterance")})
    fields["slots"] = sorted(getattr(cap, "slots", []) or [])
    fields["slot_shapes"] = dict(getattr(cap, "slot_shapes", {}) or {})
    ver = getattr(cap, "verification", None)
    fields["verification"] = _mapping(ver) if ver is not None else {}
    fields.update({k: getattr(manifest, k, "") or "" for k in
                   ("agent_id", "kind", "deployment", "trust_level")})
    fields["kind"] = fields["kind"] or "agent"
    fields["requires_permissions"] = sorted(getattr(manifest, "requires_permissions", []) or [])
    fields["context_scopes"] = sorted(getattr(manifest, "context_scopes", []) or [])
    fields["effect"] = fields["effect"] or ""
    return _digest(fields)


def snapshot_digest(abi: str, contract: dict) -> str:
    if not isinstance(abi, str) or not re.fullmatch(r"[a-f0-9]{64}", abi):
        raise ContractError("invalid_capability_abi")
    return _digest({"abi": abi, "contract": normalize(contract)})


def capability_digest(manifest, cap) -> str:
    contract = contract_of(cap)
    return snapshot_digest(abi_digest(manifest, cap), contract) if contract else ""


@lru_cache(maxsize=1)
def migration_inventory() -> dict:
    return json.loads(_MIGRATION.read_text(encoding="utf-8"))["capabilities"]


def legacy_record(manifest, cap) -> dict | None:
    key = f"{manifest.agent_id}/{cap.intent}"
    row = migration_inventory().get(key)
    return row if row and row["abi_sha256"] == abi_digest(manifest, cap) else None


def compatible_legacy(manifest, cap) -> bool:
    row = legacy_record(manifest, cap)
    if not row:
        return False
    return raw_contract(cap) is None or row.get("contract_sha256") == capability_digest(manifest, cap)


def validate_capability(manifest, cap) -> None:
    contract = contract_of(cap)
    if not contract:
        if not legacy_record(manifest, cap):
            raise ContractError("capability_contract_required")
        return
    if not set(cap.slots) <= set(contract["parameters"]):
        raise ContractError("undeclared_parameter")
    if contract["effect"] != "read" and not legacy_record(manifest, cap):
        if not list(getattr(manifest, "requires_permissions", []) or []) or "permission" not in contract["preconditions"]:
            raise ContractError("write_permission_declaration_required")
    if contract["additional_parameters"] == "legacy" and not legacy_record(manifest, cap):
        raise ContractError("unregistered_legacy_parameter_policy")
    if getattr(cap, "response_only", False) and contract["effect"] != "read":
        raise ContractError("response_only_contract_conflict")
    coarse = getattr(cap, "effect", "") or ""
    expected = "read" if contract["effect"] == "read" else "write"
    if coarse and coarse != expected:
        raise ContractError("effect_contract_conflict")
    if not coarse and not legacy_record(manifest, cap):
        raise ContractError("coarse_effect_required")
    mode = getattr(getattr(cap, "verification", None), "mode", "")
    if (contract["verification"] == "declared") != bool(mode and mode != "none"):
        raise ContractError("verification_contract_conflict")


def validate_manifest(manifest) -> None:
    for cap in manifest.capabilities:
        validate_capability(manifest, cap)


def argument_error(contract: dict, slots: dict) -> str:
    """Validate the existing map<string,string> boundary; never convert units or fill slots.

    Missing values still belong to the existing Agent NEED_SLOT flow. Applicability
    restrictions on writes need the future trusted vehicle view and fail closed here.
    """
    if not contract:
        return ""
    try:
        contract = normalize(contract)
        if contract["effect"] != "read" and contract["applicability"]["status"] == "declared":
            return "applicability_unverified"
        params = contract["parameters"]
        if contract["additional_parameters"] == "reject" and set(slots) - set(params):
            return "unexpected_parameter"
        for name, spec in params.items():
            if name not in slots or slots[name] is None:
                continue
            value = slots[name]
            kind = spec["type"]
            if kind == "string":
                valid = isinstance(value, str)
            elif kind in {"integer", "number"}:
                valid = not isinstance(value, bool) and isinstance(value, (str, int, float))
                if kind == "integer" and isinstance(value, str):
                    valid = valid and bool(re.fullmatch(r"[+-]?\d+", value.strip()))
                number = float(value) if valid else math.nan
                valid = valid and math.isfinite(number) and (kind != "integer" or number.is_integer())
                valid = valid and spec.get("minimum", -math.inf) <= number <= spec.get("maximum", math.inf)
            else:
                decoded = json.loads(value) if isinstance(value, str) else value
                valid = isinstance(decoded, {"boolean": bool, "object": dict, "array": list}[kind])
            if not valid:
                return "invalid_parameter:" + name
        return ""
    except (ContractError, TypeError, ValueError, OverflowError):
        return "invalid_contract_arguments"


def call_error(manifest, cap, meta: dict, slots: dict) -> str:
    """Final receiver check. The hash proves protocol agreement, never authorization."""
    try:
        validate_capability(manifest, cap)
        expected = capability_digest(manifest, cap)
        received = meta.get(HEADER, "")
        if received and received != expected:
            return "capability_contract_changed"
        if not received and not compatible_legacy(manifest, cap):
            return "capability_contract_unsupported"
        return argument_error(contract_of(cap), slots)
    except ContractError as exc:
        return str(exc)


def declaration(slots, effect: str, *, verification=False, preconditions=("permission", "handler"),
                idempotency="unknown", parameters=None, vehicle_specific=False, legacy=True) -> dict:
    """Declaration builder for controlled generated capabilities; no intent-name inference."""
    return normalize({"version": 2, "revision": "1", "effect": effect,
                      "parameters": parameters if parameters is not None else {s: {"type": "string"} for s in slots},
                      "additional_parameters": "legacy" if legacy else "reject",
                      "applicability": {"status": "unspecified" if vehicle_specific else "not_vehicle_specific",
                                        "vehicle_models": [], "software_versions": []},
                      "preconditions": list(preconditions), "idempotency": idempotency,
                      "verification": "declared" if verification else "none"})


def to_proto(raw):
    from google.protobuf.struct_pb2 import Struct
    result = Struct()
    result.update(normalize(raw))
    return result


def step_fields(manifest, cap) -> dict:
    contract = contract_of(cap)
    if not contract:
        return {}
    return {"capability_contract": contract, "capability_abi": abi_digest(manifest, cap),
            "capability_revision": capability_digest(manifest, cap)}


def known_legacy_digest(digest: str) -> bool:
    return bool(digest) and any(r.get("contract_sha256") == digest for r in migration_inventory().values())


def rejected(reason: str):
    from cockpit.agent.v1 import agent_pb2
    from cockpit.common.v1 import common_pb2
    return agent_pb2.ExecuteResponse(
        status=agent_pb2.ExecuteResponse.REJECTED,
        speech="这项能力的接口或参数暂时不匹配，请重新说明。",
        error=common_pb2.ErrorInfo(code="capability_contract_rejected", message=reason))


def receiver_error(manifest, intent: str, meta: dict, slots: dict) -> str:
    if manifest is None:
        return ""  # Non-serving unit fixtures; every BaseAgent has a manifest.
    cap = next((c for c in manifest.capabilities if c.intent == intent), None)
    if cap is None:
        # Existing non-published internal RPCs retain their own handler guards.
        # A stale published call must not fall into that path after removal.
        if meta.get(HEADER) or f"{manifest.agent_id}/{intent}" in migration_inventory():
            return "capability_removed"
        return ""
    return call_error(manifest, cap, meta, slots)


def visible_manifest(manifest, reader_version: int):
    """Old planners cannot silently lose new constraint fields and execute a new capability."""
    if reader_version >= 2:
        return manifest
    allowed = [c for c in manifest.capabilities
               if raw_contract(c) is None or compatible_legacy(manifest, c)]
    if len(allowed) == len(manifest.capabilities):
        return manifest
    if not allowed:
        return None
    from cockpit.agent.v1 import agent_pb2
    view = agent_pb2.AgentManifest()
    view.CopyFrom(manifest)
    del view.capabilities[:]
    view.capabilities.extend(allowed)
    intents = {c.intent for c in allowed}
    hints = [h for h in view.route_hints if h.intent in intents]
    del view.route_hints[:]
    view.route_hints.extend(hints)
    edges = [i for i in view.edge_intents if i in intents]
    del view.edge_intents[:]
    view.edge_intents.extend(edges)
    return view
