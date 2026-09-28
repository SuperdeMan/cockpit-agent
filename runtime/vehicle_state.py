"""Versioned vehicle observations. Identity, freshness and causality are separate.

This module performs no I/O except reading explicitly supplied environment values.
The unsigned compatibility lane belongs to one configured simulator, never to the
vehicle named by an incoming request. Signed observations still do not authorize
commands or prove that a particular command caused their values.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping
from types import MappingProxyType

VERSION = 2
DOMAIN = b"cockpit.vehicle-state.v2\x00"
TRUST_ENV = "VEHICLE_STATE_TRUST"
PRIVATE_KEY_ENV = "VEHICLE_STATE_PRIVATE_KEY"
KEY_ID_ENV = "VEHICLE_STATE_KEY_ID"
LEGACY_VEHICLE = "v1"
MAX_PAYLOAD = 128 * 1024
MAX_SIGNALS = 256
MAX_CLOCK_SKEW_MS = 5000
_ID = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")
_QUALITIES = {"good", "uncertain", "unavailable"}
_KINDS = {"simulated", "vehicle", "sandbox"}
_SIM_UNITS = {"speed_kmh": "km/h", "battery": "%", "hvac_temp": "degC",
              "cabin_temp": "degC", "volume": "%"}


class StateContractError(ValueError):
    """Messages are fixed codes and must never contain payload or key material."""


def _id(value, code="invalid_identity") -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise StateContractError(code)
    return value


def _int(value, minimum=0, maximum=2**53 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise StateContractError("invalid_number")
    return value


def _b64(value, size=None) -> bytes:
    try:
        if not isinstance(value, str) or len(value) > MAX_PAYLOAD * 2:
            raise ValueError()
        raw = base64.b64decode(value, validate=True)
        if size is not None and len(raw) != size:
            raise ValueError()
        if len(raw) > MAX_PAYLOAD:
            raise ValueError()
        return raw
    except (ValueError, TypeError):
        raise StateContractError("invalid_encoding") from None


def _json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise StateContractError("duplicate_field")
            result[key] = value
        return result
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise StateContractError("invalid_json") from None
    if not isinstance(result, dict):
        raise StateContractError("invalid_object")
    return result


def _value(value, depth=0):
    if depth > 6:
        raise StateContractError("value_too_deep")
    if value is None or type(value) is bool:
        return
    if type(value) is int and abs(value) <= 2**53 - 1:
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, str) and len(value) <= 2048:
        return
    if isinstance(value, list) and len(value) <= 64:
        for item in value:
            _value(item, depth + 1)
        return
    if isinstance(value, dict) and len(value) <= 64:
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 128:
                raise StateContractError("invalid_value_key")
            _value(item, depth + 1)
        return
    raise StateContractError("invalid_value")


def channel_token_digest(token: str) -> str:
    return hashlib.sha256(b"cockpit.vehicle-channel.v1\x00" + token.encode()).hexdigest()


def _known_signal_value(key, value, quality):
    if quality != "good":
        return
    if key in {"speed_kmh", "battery", "cabin_temp", "hvac_temp", "volume"}:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise StateContractError("invalid_signal_value")
        if key in {"speed_kmh", "battery", "volume"} and value < 0:
            raise StateContractError("invalid_signal_value")
        if key in {"battery", "volume"} and value > 100:
            raise StateContractError("invalid_signal_value")
    if key == "gear" and (not isinstance(value, str) or not value):
        raise StateContractError("invalid_signal_value")
    if key == "child_lock" and type(value) is not bool:
        raise StateContractError("invalid_signal_value")


@dataclass(frozen=True)
class SourceBinding:
    key_id: str
    vehicle_id: str
    source_id: str
    kind: str
    public_key: bytes = b""
    priority: int = 0
    ttl_ms: Mapping[str, int] = field(default_factory=lambda: {"*": 180_000})
    units: Mapping[str, str] = field(default_factory=dict)
    channel_token_sha256: str = ""

    def __post_init__(self):
        for value in (self.key_id, self.vehicle_id, self.source_id):
            _id(value)
        if self.kind not in _KINDS or len(self.public_key) not in (0, 32):
            raise StateContractError("invalid_source_binding")
        if not self.public_key and self.kind != "simulated":
            raise StateContractError("source_key_required")
        _int(self.priority, 0, 1000)
        if not self.ttl_ms or len(self.ttl_ms) > MAX_SIGNALS or len(self.units) > MAX_SIGNALS:
            raise StateContractError("invalid_signal_policy")
        for key, ttl in self.ttl_ms.items():
            if key != "*" and (not isinstance(key, str) or not _KEY.fullmatch(key)):
                raise StateContractError("invalid_signal_key")
            _int(ttl, 0 if not self.public_key and self.kind == "simulated" else 1, 86_400_000)
        for key, unit in self.units.items():
            if not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(unit, str) or len(unit) > 32:
                raise StateContractError("invalid_units")
        if self.kind != "simulated" and ("*" in self.ttl_ms or set(self.ttl_ms) - set(self.units)):
            raise StateContractError("real_source_requires_signal_policy")
        if not isinstance(self.channel_token_sha256, str) or (self.channel_token_sha256 and
                not re.fullmatch(r"[0-9a-f]{64}", self.channel_token_sha256)):
            raise StateContractError("invalid_channel_binding")
        object.__setattr__(self, "ttl_ms", MappingProxyType(dict(self.ttl_ms)))
        object.__setattr__(self, "units", MappingProxyType(dict(self.units)))

    def ttl(self, key: str) -> int | None:
        return self.ttl_ms.get(key, self.ttl_ms.get("*"))

    def unit(self, key: str) -> str:
        return self.units.get(key, "")


def simulation_binding(vehicle_id=LEGACY_VEHICLE, *, ttl_ms=180_000) -> SourceBinding:
    return SourceBinding("unsigned-simulator", _id(vehicle_id), "val-simulator", "simulated",
                         ttl_ms={"*": _int(ttl_ms, 0, 86_400_000)}, units=_SIM_UNITS)


@dataclass(frozen=True)
class TrustPolicy:
    sources: Mapping[str, SourceBinding] = field(default_factory=dict)
    legacy: SourceBinding | None = None

    def __post_init__(self):
        if self.sources and self.legacy is not None:
            raise StateContractError("mixed_legacy_trust")
        if len(self.sources) > 64 or any(k != v.key_id or not v.public_key for k, v in self.sources.items()):
            raise StateContractError("invalid_sources")
        if len({(s.vehicle_id, s.source_id) for s in self.sources.values()}) != len(self.sources):
            raise StateContractError("ambiguous_source_identity")
        if len({(s.vehicle_id, s.priority) for s in self.sources.values()}) != len(self.sources):
            raise StateContractError("ambiguous_source_priority")
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))

    @classmethod
    def from_env(cls, env=None, *, legacy_vehicle_id=LEGACY_VEHICLE, legacy_ttl_ms=180_000):
        env = os.environ if env is None else env
        from runtime.profile import DeployProfileError, PROD, resolve_profile
        try:
            profile = resolve_profile(env)
        except DeployProfileError:
            raise StateContractError("invalid_deploy_profile") from None
        raw = env.get(TRUST_ENV, "")
        if not raw:
            legacy = (simulation_binding(legacy_vehicle_id, ttl_ms=legacy_ttl_ms)
                      if legacy_vehicle_id and profile != PROD else None)
            return cls(legacy=legacy)
        if len(raw.encode()) > MAX_PAYLOAD:
            raise StateContractError("trust_too_large")
        data = _json(raw.encode())
        if set(data) != {"version", "sources"} or type(data["version"]) is not int or data["version"] != 1:
            raise StateContractError("invalid_trust_version")
        if not isinstance(data["sources"], list) or not 1 <= len(data["sources"]) <= 64:
            raise StateContractError("invalid_sources")
        sources = {}
        identities = set()
        token_bindings = {}
        for item in data["sources"]:
            required = {"key_id", "vehicle_id", "source_id", "kind", "public_key", "priority", "ttl_ms", "units"}
            if not isinstance(item, dict) or not required <= set(item) or set(item) - required - {"channel_token_sha256"}:
                raise StateContractError("invalid_source_binding")
            kid, vid, sid = (_id(item[k]) for k in ("key_id", "vehicle_id", "source_id"))
            kind = item["kind"]
            if not isinstance(kind, str) or kind not in _KINDS or kid in sources or (vid, sid) in identities:
                raise StateContractError("duplicate_or_invalid_source")
            ttl, units = item["ttl_ms"], item["units"]
            if not isinstance(ttl, dict) or not 1 <= len(ttl) <= MAX_SIGNALS:
                raise StateContractError("invalid_ttl_policy")
            if not isinstance(units, dict) or len(units) > MAX_SIGNALS:
                raise StateContractError("invalid_units")
            for key, age in ttl.items():
                if key != "*" and not _KEY.fullmatch(key):
                    raise StateContractError("invalid_signal_key")
                _int(age, 1, 86_400_000)
            if kind != "simulated" and "*" in ttl:
                raise StateContractError("real_source_requires_signal_policy")
            for key, unit in units.items():
                if not _KEY.fullmatch(key) or not isinstance(unit, str) or len(unit) > 32:
                    raise StateContractError("invalid_units")
            token_hash = item.get("channel_token_sha256", "")
            if not isinstance(token_hash, str):
                raise StateContractError("invalid_channel_binding")
            if token_hash:
                if not isinstance(token_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", token_hash):
                    raise StateContractError("invalid_channel_binding")
                if token_hash in token_bindings and token_bindings[token_hash] != vid:
                    raise StateContractError("ambiguous_channel_binding")
                token_bindings[token_hash] = vid
            sources[kid] = SourceBinding(kid, vid, sid, kind, _b64(item["public_key"], 32),
                                        _int(item["priority"], 0, 1000), dict(ttl), dict(units), token_hash)
            identities.add((vid, sid))
        return cls(sources=sources)


class StateSigner:
    def __init__(self, binding: SourceBinding, private_key: bytes | None = None):
        self.binding = binding
        self._key = None
        if private_key is not None:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            try:
                self._key = Ed25519PrivateKey.from_private_bytes(private_key)
                public = self._key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            except (ValueError, TypeError):
                raise StateContractError("invalid_signing_key") from None
            if public != binding.public_key:
                raise StateContractError("signing_key_mismatch")
        elif binding.public_key or binding.kind != "simulated":
            raise StateContractError("signing_key_required")

    @classmethod
    def from_env(cls, vehicle_id, env=None):
        env = os.environ if env is None else env
        policy = TrustPolicy.from_env(env, legacy_vehicle_id=vehicle_id)
        kid, key = env.get(KEY_ID_ENV, ""), env.get(PRIVATE_KEY_ENV, "")
        if not policy.sources:
            if kid or key or policy.legacy is None:
                raise StateContractError("signer_not_enrolled")
            return cls(policy.legacy)
        binding = policy.sources.get(kid)
        if binding is None or binding.vehicle_id != vehicle_id:
            raise StateContractError("signer_not_enrolled")
        return cls(binding, _b64(key, 32))

    def envelope(self, payload: dict, *, changes=()) -> dict:
        if (payload.get("vehicle_id"), payload.get("source_id")) != (self.binding.vehicle_id, self.binding.source_id):
            raise StateContractError("signer_identity_mismatch")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > MAX_PAYLOAD:
            raise StateContractError("payload_too_large")
        signature = self._key.sign(DOMAIN + raw) if self._key is not None else b""
        return {"version": VERSION, "key_id": self.binding.key_id,
                "payload": base64.b64encode(raw).decode(), "signature": base64.b64encode(signature).decode(),
                # Only the explicitly unsigned single-simulator lane projects to old consumers.
                "changes": list(changes) if self._key is None else []}


@dataclass
class _Cell:
    value: object
    quality: str
    unit: str
    observed_at_ms: int
    received_at_ms: int
    expires_at_ms: int | None
    expires_mono: float
    source_seq: int
    operation_id: str = ""


@dataclass
class _Stream:
    binding: SourceBinding
    authenticated: bool
    epoch: str
    started_at_ms: int
    seq: int
    cells: dict[str, _Cell] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    reason: str = ""
    vehicle_id: str = ""
    source_id: str = ""
    changes: tuple = ()


class VehicleStateStore:
    def __init__(self, policy: TrustPolicy | None = None, *, wall_ms: Callable[[], int] | None = None,
                 monotonic: Callable[[], float] = time.monotonic):
        self.policy = policy if policy is not None else TrustPolicy.from_env()
        self._wall = wall_ms or (lambda: int(time.time() * 1000))
        self._mono = monotonic
        self._streams: dict[tuple[str, str], _Stream] = {}
        self._legacy_seq = 0
        self._started_at_ms = self._wall()

    def _decode(self, event: dict, now: int):
        if not isinstance(event, dict):
            raise StateContractError("invalid_event")
        if "version" not in event:
            # Presence of any v2 identity/wire field cannot silently downgrade to legacy.
            if set(event) & {"vehicle_id", "source_id", "source_epoch", "payload", "signature", "key_id"}:
                raise StateContractError("invalid_legacy_event")
            binding = self.policy.legacy
            if binding is None:
                raise StateContractError("legacy_disabled")
            changes = event.get("changes")
            if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_SIGNALS:
                raise StateContractError("invalid_signals")
            self._legacy_seq += 1
            observed = event.get("ts", now)
            signals = [{"key": c.get("key"), "value": c.get("new"), "observed_at_ms": observed,
                        "quality": "good", "unit": binding.unit(c.get("key"))}
                       for c in changes if isinstance(c, dict)]
            if len(signals) != len(changes):
                raise StateContractError("invalid_signals")
            return binding, False, {"vehicle_id": binding.vehicle_id, "source_id": binding.source_id,
                "source_epoch": "legacy", "epoch_started_at_ms": 0, "source_seq": self._legacy_seq,
                "emitted_at_ms": observed, "snapshot": True, "signals": signals}
        if type(event["version"]) is not int or event["version"] != VERSION:
            raise StateContractError("unsupported_state_version")
        kid = _id(event.get("key_id"))
        raw = _b64(event.get("payload"))
        binding = self.policy.sources.get(kid)
        authenticated = binding is not None
        if authenticated:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.exceptions import InvalidSignature
            try:
                Ed25519PublicKey.from_public_bytes(binding.public_key).verify(_b64(event.get("signature"), 64), DOMAIN + raw)
            except (ValueError, InvalidSignature):
                raise StateContractError("invalid_state_signature") from None
        else:
            binding = self.policy.legacy
            if binding is None or kid != binding.key_id or event.get("signature") != "":
                raise StateContractError("unknown_state_source")
        return binding, authenticated, _json(raw)

    def ingest(self, event: dict | bytes) -> IngestResult:
        now, mono = self._wall(), self._mono()
        try:
            if isinstance(event, bytes):
                if len(event) > MAX_PAYLOAD * 2:
                    raise StateContractError("event_too_large")
                event = _json(event)
            binding, authenticated, data = self._decode(event, now)
            required = {"vehicle_id", "source_id", "source_epoch", "epoch_started_at_ms", "source_seq",
                        "emitted_at_ms", "snapshot", "signals"}
            if set(data) != required:
                raise StateContractError("invalid_state_fields")
            vid, sid, epoch = (_id(data[k]) for k in ("vehicle_id", "source_id", "source_epoch"))
            legacy = not authenticated and "version" not in event
            if vid != binding.vehicle_id or sid != binding.source_id:
                raise StateContractError("state_identity_mismatch")
            started, emitted = _int(data["epoch_started_at_ms"]), _int(data["emitted_at_ms"])
            seq = _int(data["source_seq"], 1)
            if started > emitted or emitted > now + MAX_CLOCK_SKEW_MS:
                raise StateContractError("invalid_source_clock")
            if not legacy and emitted + MAX_CLOCK_SKEW_MS < self._started_at_ms:
                raise StateContractError("predates_receiver")
            if type(data["snapshot"]) is not bool or not isinstance(data["signals"], list) or len(data["signals"]) > MAX_SIGNALS:
                raise StateContractError("invalid_signals")
            stream = self._streams.get((vid, sid))
            replacement = stream is None or stream.epoch != epoch
            if replacement and not data["snapshot"]:
                raise StateContractError("snapshot_required")
            if stream is not None:
                if stream.epoch == epoch and (started != stream.started_at_ms or seq <= stream.seq):
                    raise StateContractError("replayed_state")
                if stream.epoch != epoch and started <= stream.started_at_ms:
                    raise StateContractError("retired_epoch")
            cells = {}
            for sample in data["signals"]:
                fields = {"key", "value", "observed_at_ms", "quality", "unit"}
                if not isinstance(sample, dict) or not fields <= set(sample) or set(sample) - fields - {"operation_id"}:
                    raise StateContractError("invalid_signal_fields")
                key = sample["key"]
                if not isinstance(key, str) or not _KEY.fullmatch(key) or key in cells:
                    raise StateContractError("invalid_signal_key")
                quality, unit = sample["quality"], sample["unit"]
                if not isinstance(quality, str) or quality not in _QUALITIES:
                    raise StateContractError("invalid_quality")
                if not isinstance(unit, str) or unit != binding.unit(key):
                    raise StateContractError("signal_unit_mismatch")
                observed = _int(sample["observed_at_ms"])
                if observed > emitted + MAX_CLOCK_SKEW_MS or observed > now + MAX_CLOCK_SKEW_MS:
                    raise StateContractError("invalid_signal_clock")
                ttl = binding.ttl(key)
                if ttl is None:
                    raise StateContractError("signal_not_enrolled")
                _value(sample["value"])
                _known_signal_value(key, sample["value"], quality)
                operation = sample.get("operation_id", "")
                if not isinstance(operation, str):
                    raise StateContractError("invalid_operation_reference")
                if operation:
                    _id(operation, "invalid_operation_reference")
                remaining = max(0, ttl - max(0, now - observed))
                cells[key] = _Cell(copy.deepcopy(sample["value"]), quality, unit, observed, now,
                                   now + remaining if ttl else None,
                                   mono + remaining / 1000 if ttl else math.inf, seq, operation)
            if not replacement and len(set(stream.cells) | set(cells)) > MAX_SIGNALS:
                raise StateContractError("too_many_signals")
            before = self.snapshot(vid)
            if replacement:
                stream = _Stream(binding, authenticated, epoch, started, seq)
            else:
                stream.seq = seq
            for key, cell in cells.items():
                previous = stream.cells.get(key)
                if previous is None or cell.observed_at_ms >= previous.observed_at_ms:
                    if previous is not None and cell.observed_at_ms == previous.observed_at_ms:
                        # A repeated sample cannot renew its lease when the wall
                        # clock moves backwards or a sender repackages it.
                        cell.expires_mono = min(cell.expires_mono, previous.expires_mono)
                        cell.expires_at_ms = previous.expires_at_ms
                    stream.cells[key] = cell
            self._streams[(vid, sid)] = stream
            after = self.snapshot(vid)
            changes = tuple({"key": k, "old": before.get(k), "new": after.get(k)}
                            for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k))
            return IngestResult(True, vehicle_id=vid, source_id=sid, changes=changes)
        except (StateContractError, TypeError, ValueError, OverflowError, RecursionError) as exc:
            return IngestResult(False, str(exc) if isinstance(exc, StateContractError) else "invalid_state_event")

    def view(self, vehicle_id: str) -> dict:
        """A full replacement projection; stale and uncertain values are omitted."""
        candidates = {}
        for (vid, _), stream in self._streams.items():
            if vid != vehicle_id:
                continue
            for key, cell in stream.cells.items():
                rank = (stream.authenticated, stream.binding.priority, stream.binding.source_id)
                if key not in candidates or rank > candidates[key][0]:
                    candidates[key] = (rank, stream, cell)
        values, metadata = {}, {}
        mono = self._mono()
        for key, (_, stream, cell) in candidates.items():
            # Enrolled coverage defines authority, including before the first
            # checkpoint and after a reboot omits a field. Packet availability
            # cannot promote a lower-priority source into that authority.
            if any(source.vehicle_id == vehicle_id and source.priority > stream.binding.priority
                   and source.ttl(key) is not None for source in self.policy.sources.values()):
                continue
            status = "stale" if mono >= cell.expires_mono else cell.quality
            if status == "good":
                values[key] = copy.deepcopy(cell.value)
            metadata[key] = {"quality": status, "unit": cell.unit, "source_id": stream.binding.source_id,
                             "source_kind": stream.binding.kind, "authenticated": stream.authenticated,
                             "freshness": "bounded" if cell.expires_at_ms is not None else "legacy-unbounded",
                             "source_epoch": stream.epoch, "source_seq": cell.source_seq,
                             "observed_at_ms": cell.observed_at_ms, "received_at_ms": cell.received_at_ms,
                             "expires_at_ms": cell.expires_at_ms, "operation_id": cell.operation_id}
        return {"version": VERSION, "vehicle_id": vehicle_id, "state": values, "signals": metadata}

    def snapshot(self, vehicle_id=LEGACY_VEHICLE) -> dict:
        return self.view(vehicle_id)["state"]

    def vehicles(self) -> tuple[str, ...]:
        return tuple(sorted({vid for vid, _ in self._streams}))
