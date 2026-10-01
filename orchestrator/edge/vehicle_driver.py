"""Stateful simulator beneath VAL; no remote command or fault-injection endpoint.

VAL remains the only command interpreter. The driver owns simulated values and
observations, while the offline harness can independently lose an acknowledgement.
Neither a simulated observation nor an ACK is evidence of real vehicle actuation.
"""
from __future__ import annotations

import contextvars
import copy
import math
import os
import random
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from runtime.vehicle_state import StateSigner, StateContractError, simulation_binding

# CA2-10: the observation reference of the command VAL is executing right now. Set only by
# the edge-call executor around one VAL.execute; samples that command changed carry it.
COMMAND_REF: contextvars.ContextVar[str] = contextvars.ContextVar("command_observation_ref", default="")


def initial_state() -> dict:
    return {
        "hvac_on": False, "hvac_temp": 24,
        "front_defogger": False, "rear_defogger": False,
        "window": "closed", "sunroof": "closed",
        "door_lock": "locked", "trunk": "closed",
        "seat_heating": False, "seat_ventilation": False,
        "ambient_light": False, "headlight": False, "warning_light": False,
        "media": "stopped", "volume": 30, "volume_muted": False,
        "wiper": False, "fragrance": False,
        "rear_view_mirror": "unfolded", "rear_view_mirror_heating": False,
        "steering_wheel_heating": False, "child_lock": False,
        "speed_kmh": 0, "gear": "P", "battery": 72, "location": None,
        "cabin_temp": 24,
    }


@dataclass(frozen=True)
class SimulatedReceipt:
    operation_id: str
    status: str
    acknowledgement_received: bool
    simulated: bool = True


class SimulatedVehicleDriver:
    def __init__(self, vehicle_id=None, *, signer: StateSigner | None = None,
                 wall_ms: Callable[[], int] | None = None, seed: int | None = None,
                 state: dict | None = None):
        self.vehicle_id = vehicle_id or os.getenv("VEHICLE_ID", "v1")
        # Metadata builders also instantiate VAL. Only the actual edge producer
        # loads a runtime signing key; plain in-process simulators stay unsigned.
        self.signer = signer or StateSigner(simulation_binding(self.vehicle_id))
        if self.signer.binding.vehicle_id != self.vehicle_id or self.signer.binding.kind != "simulated":
            raise StateContractError("simulator_binding_required")
        self.state = copy.deepcopy(initial_state() if state is None else state)
        self._wall = wall_ms or (lambda: int(time.time() * 1000))
        self._rng = random.Random(seed) if seed is not None else None
        self.epoch = self._new_epoch()
        self.epoch_started_at_ms = self._wall()
        self.seq = 0
        self._silent: set[str] = set()
        self._quality: dict[str, str] = {}
        self._scheduled: list[tuple[int, dict]] = []
        self._lost_ack = 0
        self._operations: set[str] = set()
        self.command_count = 0

    def _new_epoch(self):
        return str(uuid.UUID(int=self._rng.getrandbits(128))) if self._rng else str(uuid.uuid4())

    def set_faults(self, *, silent=(), quality=None, lose_next_ack=0):
        """Explicit local harness API. Production never exposes these knobs."""
        if type(lose_next_ack) is not int or lose_next_ack < 0:
            raise ValueError("invalid_ack_fault")
        if any(value not in {"good", "uncertain", "unavailable"} for value in (quality or {}).values()):
            raise ValueError("invalid_quality_fault")
        self._silent = set(silent)
        self._quality = dict(quality or {})
        self._lost_ack = lose_next_ack

    def inputs_available(self, keys) -> bool:
        """Read current local simulator inputs; silence only affects publication.

        A bad sensor quality is distinct from losing telemetry on the way to cloud.
        VAL must refuse a guarded write when its own required input is unavailable.
        """
        for key in keys:
            value = self.state.get(key)
            if value is None or self._quality.get(key, "good") != "good":
                return False
            if key in {"speed_kmh", "battery"}:
                if type(value) not in (int, float) or not math.isfinite(value):
                    return False
            if key == "gear" and value not in {"P", "N", "R", "D", "S"}:
                return False
            if key == "child_lock" and type(value) is not bool:
                return False
        return True

    def schedule_environment(self, at_ms: int, values: dict):
        """Schedule sensor changes for deterministic offline scenarios, not commands."""
        allowed = {"speed_kmh", "gear", "battery", "location", "cabin_temp"}
        if type(at_ms) is not int or at_ms < self._wall() or not values or set(values) - allowed:
            raise ValueError("invalid_simulated_environment")
        self._scheduled.append((at_ms, copy.deepcopy(values)))
        self._scheduled.sort(key=lambda pair: pair[0])

    def poll_environment(self, val):
        """Even simulated environment consistency is owned by VAL.set_env."""
        now = self._wall()
        ready = [item for item in self._scheduled if item[0] <= now]
        self._scheduled = [item for item in self._scheduled if item[0] > now]
        for _, values in ready:
            for key, value in values.items():
                val.set_env(key, value)

    def observation(self, *, changes=(), snapshot=False, operation_id="") -> dict:
        now = self._wall()
        if now < self.epoch_started_at_ms:
            raise StateContractError("source_clock_reversed")
        # A mutation can race initial startup publication. Its first packet must
        # still seed consumers with a checkpoint rather than an unbound delta.
        snapshot = bool(snapshot or self.seq == 0)
        changes = list(changes)
        keys = list(self.state) if snapshot else [c["key"] for c in changes]
        # Attribution covers only what the command changed: a checkpoint carried by the
        # same packet must not stamp untouched values as caused by it (CA2-10).
        operation_id = operation_id or COMMAND_REF.get()
        tagged = {c.get("key") for c in changes if isinstance(c, dict)} if operation_id else set()
        signals = []
        for key in dict.fromkeys(keys):
            if key in self._silent or key not in self.state:
                continue
            value = copy.deepcopy(self.state[key])
            quality = self._quality.get(key, "unavailable" if value is None else "good")
            sample = {"key": key, "value": value, "observed_at_ms": now,
                      "quality": quality, "unit": self.signer.binding.unit(key)}
            if key in tagged:
                sample["operation_id"] = operation_id
            signals.append(sample)
        self.seq += 1
        payload = {"vehicle_id": self.vehicle_id, "source_id": self.signer.binding.source_id,
                   "source_epoch": self.epoch, "epoch_started_at_ms": self.epoch_started_at_ms,
                   "source_seq": self.seq, "emitted_at_ms": now, "snapshot": snapshot, "signals": signals}
        compatibility = ([{"key": k, "old": None, "new": copy.deepcopy(v)} for k, v in self.state.items()]
                         if snapshot else changes)
        # An old display also must not show a value the simulator has marked bad.
        good = {s["key"] for s in signals if s["quality"] == "good"}
        return self.signer.envelope(payload, changes=[c for c in compatibility if c["key"] in good])

    def restart(self, *, reset_values=False):
        now = self._wall()
        if now <= self.epoch_started_at_ms:
            raise StateContractError("restart_clock_not_advanced")
        self.epoch = self._new_epoch()
        self.epoch_started_at_ms = now
        self.seq = 0
        self._operations.clear()
        self._scheduled.clear()
        if reset_values:
            self.state.clear()
            self.state.update(initial_state())

    def run_once(self, operation_id: str, command: Callable[[], tuple[bool, str]]) -> SimulatedReceipt:
        """Harness only: execute a VAL closure once, independently model its ACK.

        Re-use is rejected, not retried. This in-memory diagnostic fence is not a
        persistent operation ledger, and resetting the simulator resets the fence.
        """
        if not operation_id or operation_id in self._operations:
            raise ValueError("duplicate_simulated_operation")
        self._operations.add(operation_id)
        self.command_count += 1
        ok, _ = command()
        if self._lost_ack:
            self._lost_ack -= 1
            return SimulatedReceipt(operation_id, "unknown", False)
        return SimulatedReceipt(operation_id, "acknowledged" if ok else "rejected", True)
