"""Dynamic staged-deletion bootstrap used by the true-stack privacy E2E."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence


MILESTONE_ORDER = {"M-A": 0, "M-B": 1, "M-C": 2, "M-D": 3}
LIFECYCLES = frozenset({"deletable", "retained_audit", "external_reference"})
_STABLE_ID_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}\Z")


class PrivacyBootstrapError(RuntimeError):
    """The runtime privacy inventory cannot be exercised safely."""


class DeletionAdapter(Protocol):
    async def seed(self, target: Any, user: str, marker: str) -> None: ...

    async def count(self, target: Any, user: str) -> int: ...

    async def read_contains(
        self,
        target: Any,
        user: str,
        marker: str,
    ) -> bool: ...

    async def snapshot_fingerprint(
        self,
        target: Any,
        user: str,
        marker: str,
    ) -> str: ...

    async def delete(self, user: str, action: str) -> bool: ...


@dataclass(frozen=True)
class BootstrapResult:
    due_target_ids: tuple[str, ...]
    future_target_ids: tuple[str, ...]


@dataclass(frozen=True)
class _ConsumerRead:
    completed: bool
    contains_marker: bool


def _field(target: Any, name: str) -> str:
    value = getattr(target, name, None)
    if not isinstance(value, str) or not value:
        raise PrivacyBootstrapError(
            f"privacy target {getattr(target, 'id', '?')!r} "
            f"has invalid {name}",
        )
    return value


def _validate_targets(
    targets: Sequence[Any],
    milestone: str,
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    if milestone not in MILESTONE_ORDER:
        raise PrivacyBootstrapError("privacy milestone is unknown")
    current = MILESTONE_ORDER[milestone]
    due: list[Any] = []
    future: list[Any] = []
    ids: set[str] = set()
    probes: set[str] = set()
    for target in targets:
        target_id = _field(target, "id")
        if _STABLE_ID_RE.fullmatch(target_id) is None or target_id in ids:
            raise PrivacyBootstrapError("privacy target IDs are invalid or duplicated")
        ids.add(target_id)
        lifecycle = _field(target, "lifecycle")
        enforced_from = _field(target, "enforced_from")
        _field(target, "adapter_key")
        if lifecycle not in LIFECYCLES:
            raise PrivacyBootstrapError(
                f"privacy target {target_id!r} lifecycle is unknown",
            )
        if enforced_from not in MILESTONE_ORDER:
            raise PrivacyBootstrapError(
                f"privacy target {target_id!r} milestone is unknown",
            )
        for name in ("seed_case", "count_probe", "read_probe", "verify_case"):
            probe = _field(target, name)
            if _STABLE_ID_RE.fullmatch(probe) is None or probe in probes:
                raise PrivacyBootstrapError(
                    f"privacy target {target_id!r} probe IDs are invalid",
                )
            probes.add(probe)
        if MILESTONE_ORDER[enforced_from] < current:
            future.append(target)
            continue
        if enforced_from == milestone:
            if lifecycle != "deletable":
                future.append(target)
                continue
            _field(target, "delete_action")
            due.append(target)
        else:
            future.append(target)
    return tuple(due), tuple(future)


async def _safe_seed(
    adapter: DeletionAdapter,
    target: Any,
    user: str,
    marker: str,
) -> bool:
    try:
        await adapter.seed(target, user, marker)
        return True
    except Exception:
        return False


async def _safe_count(
    adapter: DeletionAdapter,
    target: Any,
    user: str,
) -> int:
    try:
        value = await adapter.count(target, user)
    except Exception:
        return -1
    if type(value) is not int or value < 0:
        return -1
    return value


async def _safe_read(
    adapter: DeletionAdapter,
    target: Any,
    user: str,
    marker: str,
) -> _ConsumerRead:
    try:
        value = await adapter.read_contains(target, user, marker)
    except Exception:
        return _ConsumerRead(completed=False, contains_marker=False)
    if type(value) is not bool:
        return _ConsumerRead(completed=False, contains_marker=False)
    return _ConsumerRead(completed=True, contains_marker=value)


async def _safe_delete(
    adapter: DeletionAdapter,
    user: str,
    action: str,
) -> bool:
    try:
        return await adapter.delete(user, action) is True
    except Exception:
        return False


async def _safe_snapshot_fingerprint(
    adapter: DeletionAdapter,
    target: Any,
    user: str,
    marker: str,
) -> str:
    try:
        value = await adapter.snapshot_fingerprint(
            target,
            user,
            marker,
        )
    except Exception:
        return ""
    if (
        not isinstance(value, str)
        or _FINGERPRINT_RE.fullmatch(value) is None
    ):
        return ""
    return value


async def run_deletion_bootstrap(
    *,
    targets: Sequence[Any],
    adapters: Mapping[str, DeletionAdapter],
    milestone: str,
    target_user: str,
    control_user: str,
    record: Callable[[str, bool, str], None],
) -> BootstrapResult:
    """Seed T/C, delete T twice, then prove storage and consumers are empty."""

    if (
        not isinstance(target_user, str)
        or not target_user
        or not isinstance(control_user, str)
        or not control_user
        or target_user == control_user
    ):
        raise PrivacyBootstrapError("privacy target/control users are invalid")
    if not callable(record):
        raise PrivacyBootstrapError("privacy recorder is not callable")
    due, future = _validate_targets(tuple(targets), milestone)
    missing = sorted({
        target.adapter_key
        for target in due
        if target.adapter_key not in adapters
    })
    if missing:
        raise PrivacyBootstrapError(
            f"privacy deletion adapter is missing: {missing}",
        )

    markers = {
        target.id: f"gdpr-marker-{target.id}"
        for target in due
    }
    seed_results: dict[str, bool] = {}
    control_before: dict[str, int] = {}
    control_fingerprints: dict[str, str] = {}
    for target in due:
        adapter = adapters[target.adapter_key]
        marker = markers[target.id]
        seeded_target = await _safe_seed(
            adapter,
            target,
            target_user,
            marker,
        )
        seeded_control = await _safe_seed(
            adapter,
            target,
            control_user,
            marker,
        )
        seed_results[target.id] = seeded_target and seeded_control
        record(
            target.seed_case,
            seed_results[target.id],
            "target and control seeds created",
        )

    # Take persistence/consumer snapshots only after every target has seeded.
    # Some adapters intentionally create cross-target side effects (voiceprint
    # enrollment also creates identity.name in memory_item).
    for target in due:
        adapter = adapters[target.adapter_key]
        marker = markers[target.id]
        target_count = await _safe_count(adapter, target, target_user)
        control_count = await _safe_count(adapter, target, control_user)
        control_before[target.id] = control_count
        record(
            target.count_probe,
            target_count > 0 and control_count > 0,
            "target and control persistent counts are positive",
        )

        target_read = await _safe_read(
            adapter,
            target,
            target_user,
            marker,
        )
        control_read = await _safe_read(
            adapter,
            target,
            control_user,
            marker,
        )
        control_fingerprint = await _safe_snapshot_fingerprint(
            adapter,
            target,
            control_user,
            marker,
        )
        control_fingerprints[target.id] = control_fingerprint
        record(
            target.read_probe,
            (
                target_read.completed
                and target_read.contains_marker
                and control_read.completed
                and control_read.contains_marker
                and bool(control_fingerprint)
            ),
            "consumer reads and the control snapshot completed",
        )

    delete_results: dict[tuple[str, str], bool] = {}
    for target in due:
        key = (target.adapter_key, target.delete_action)
        if key in delete_results:
            continue
        adapter = adapters[target.adapter_key]
        first = await _safe_delete(adapter, target_user, target.delete_action)
        second = await _safe_delete(adapter, target_user, target.delete_action)
        delete_results[key] = first and second

    for target in due:
        adapter = adapters[target.adapter_key]
        marker = markers[target.id]
        target_count = await _safe_count(adapter, target, target_user)
        target_read = await _safe_read(
            adapter,
            target,
            target_user,
            marker,
        )
        control_count = await _safe_count(adapter, target, control_user)
        control_read = await _safe_read(
            adapter,
            target,
            control_user,
            marker,
        )
        control_fingerprint = await _safe_snapshot_fingerprint(
            adapter,
            target,
            control_user,
            marker,
        )
        delete_ok = delete_results[(target.adapter_key, target.delete_action)]
        record(
            target.verify_case,
            (
                delete_ok
                and target_count == 0
                and target_read.completed
                and not target_read.contains_marker
                and control_count == control_before[target.id]
                and control_count > 0
                and control_read.completed
                and control_read.contains_marker
                and bool(control_fingerprint)
                and control_fingerprint == control_fingerprints[target.id]
            ),
            "target is absent and the control snapshot is unchanged",
        )

    return BootstrapResult(
        due_target_ids=tuple(target.id for target in due),
        future_target_ids=tuple(target.id for target in future),
    )


__all__ = [
    "BootstrapResult",
    "DeletionAdapter",
    "PrivacyBootstrapError",
    "run_deletion_bootstrap",
]
