from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass

import pytest

from scripts.privacy_bootstrap import (
    PrivacyBootstrapError,
    run_deletion_bootstrap,
)


@dataclass(frozen=True)
class Target:
    id: str
    adapter_key: str
    lifecycle: str
    enforced_from: str
    seed_case: str
    count_probe: str
    read_probe: str
    delete_action: str
    verify_case: str


def _target(target_id: str, *, milestone: str = "M-A") -> Target:
    slug = milestone.lower().replace("-", "")
    return Target(
        id=target_id,
        adapter_key="memory",
        lifecycle="deletable",
        enforced_from=milestone,
        seed_case=f"gdpr_{slug}_{target_id}_seed",
        count_probe=f"gdpr_{slug}_{target_id}_count",
        read_probe=f"gdpr_{slug}_{target_id}_read",
        delete_action="forget_user",
        verify_case=f"gdpr_{slug}_{target_id}_verify",
    )


class FakeAdapter:
    def __init__(self):
        self.data: dict[tuple[str, str], str] = {}
        self.calls: list[tuple] = []

    async def seed(self, target, user: str, marker: str) -> None:
        self.calls.append(("seed", target.id, user))
        self.data[(target.id, user)] = marker

    async def count(self, target, user: str) -> int:
        self.calls.append(("count", target.id, user))
        return int((target.id, user) in self.data)

    async def read_contains(self, target, user: str, marker: str) -> bool:
        self.calls.append(("read", target.id, user))
        return self.data.get((target.id, user)) == marker

    async def snapshot_fingerprint(
        self,
        target,
        user: str,
        marker: str,
    ) -> str:
        self.calls.append(("snapshot", target.id, user))
        payload = {
            "persistent": self.data.get((target.id, user)),
            "consumer": self.data.get((target.id, user)) == marker,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    async def delete(self, user: str, action: str) -> bool:
        self.calls.append(("delete", user, action))
        for key in [key for key in self.data if key[1] == user]:
            del self.data[key]
        return True


def test_bootstrap_dynamically_exercises_every_due_target_and_preserves_control():
    due_ids = (
        "memory_item",
        "memory_relation",
        "voiceprint",
        "profile_identity",
        "session_history",
    )
    targets = tuple(_target(target_id) for target_id in due_ids) + (
        _target("future_item", milestone="M-B"),
    )
    adapter = FakeAdapter()
    outcomes: list[tuple[str, bool, str]] = []

    result = asyncio.run(
        run_deletion_bootstrap(
            targets=targets,
            adapters={"memory": adapter},
            milestone="M-A",
            target_user="e2e-case-target",
            control_user="e2e-case-control",
            record=lambda case_id, ok, detail: outcomes.append(
                (case_id, ok, detail),
            ),
        ),
    )

    assert result.due_target_ids == due_ids
    assert result.future_target_ids == ("future_item",)
    assert all(ok for _case_id, ok, _detail in outcomes)
    assert {case_id for case_id, _ok, _detail in outcomes} == {
        probe
        for target in targets[:-1]
        for probe in (
            target.seed_case,
            target.count_probe,
            target.read_probe,
            target.verify_case,
        )
    }
    assert [
        call for call in adapter.calls if call[0] == "delete"
    ] == [
        ("delete", "e2e-case-target", "forget_user"),
        ("delete", "e2e-case-target", "forget_user"),
    ]
    assert not any(
        "future_item" in call
        for call in adapter.calls
    )
    assert all(
        ("future_item", user) not in adapter.data
        for user in ("e2e-case-target", "e2e-case-control")
    )
    assert all(
        (target_id, "e2e-case-target") not in adapter.data
        for target_id in due_ids
    )
    assert all(
        (target_id, "e2e-case-control") in adapter.data
        for target_id in due_ids
    )


def test_bootstrap_fails_before_seeding_when_due_adapter_is_missing():
    target = _target("memory_item")
    outcomes: list[tuple] = []

    with pytest.raises(PrivacyBootstrapError, match="adapter"):
        asyncio.run(
            run_deletion_bootstrap(
                targets=(target,),
                adapters={},
                milestone="M-A",
                target_user="e2e-case-target",
                control_user="e2e-case-control",
                record=lambda *args: outcomes.append(args),
            ),
        )

    assert outcomes == []


def test_control_snapshot_is_taken_after_all_cross_target_seed_side_effects():
    targets = (
        _target("memory_item"),
        _target("voiceprint"),
    )

    class CrossTargetAdapter(FakeAdapter):
        async def seed(self, target, user: str, marker: str) -> None:
            await super().seed(target, user, marker)
            if target.id == "voiceprint":
                self.data[("memory_item", user)] += "+identity-name"

        async def count(self, target, user: str) -> int:
            self.calls.append(("count", target.id, user))
            value = self.data.get((target.id, user))
            return 0 if value is None else value.count("+") + 1

        async def read_contains(self, target, user: str, marker: str) -> bool:
            self.calls.append(("read", target.id, user))
            return marker in self.data.get((target.id, user), "")

    adapter = CrossTargetAdapter()
    outcomes: list[tuple[str, bool, str]] = []

    asyncio.run(
        run_deletion_bootstrap(
            targets=targets,
            adapters={"memory": adapter},
            milestone="M-A",
            target_user="e2e-case-target",
            control_user="e2e-case-control",
            record=lambda case_id, ok, detail: outcomes.append(
                (case_id, ok, detail),
            ),
        ),
    )

    assert all(ok for _case_id, ok, _detail in outcomes)


@pytest.mark.parametrize("failure", ["exception", "invalid"])
def test_post_delete_consumer_read_must_complete_with_explicit_false(failure):
    target = _target("memory_item")

    class PostDeleteReadFailureAdapter(FakeAdapter):
        deleted = False

        async def read_contains(self, target, user: str, marker: str) -> bool:
            if self.deleted and user == "e2e-case-target":
                if failure == "exception":
                    raise RuntimeError("consumer unavailable")
                return "not-a-bool"
            return await super().read_contains(target, user, marker)

        async def delete(self, user: str, action: str) -> bool:
            result = await super().delete(user, action)
            self.deleted = True
            return result

    outcomes: dict[str, bool] = {}
    asyncio.run(
        run_deletion_bootstrap(
            targets=(target,),
            adapters={"memory": PostDeleteReadFailureAdapter()},
            milestone="M-A",
            target_user="e2e-case-target",
            control_user="e2e-case-control",
            record=lambda case_id, ok, _detail: outcomes.__setitem__(case_id, ok),
        ),
    )

    assert outcomes[target.read_probe] is True
    assert outcomes[target.verify_case] is False


@pytest.mark.parametrize("failure", ["exception", "invalid"])
def test_pre_delete_consumer_read_must_complete_with_explicit_true(failure):
    target = _target("memory_item")

    class PreDeleteReadFailureAdapter(FakeAdapter):
        async def read_contains(self, target, user: str, marker: str) -> bool:
            if user == "e2e-case-target":
                if failure == "exception":
                    raise RuntimeError("consumer unavailable")
                return "not-a-bool"
            return await super().read_contains(target, user, marker)

    outcomes: dict[str, bool] = {}
    asyncio.run(
        run_deletion_bootstrap(
            targets=(target,),
            adapters={"memory": PreDeleteReadFailureAdapter()},
            milestone="M-A",
            target_user="e2e-case-target",
            control_user="e2e-case-control",
            record=lambda case_id, ok, _detail: outcomes.__setitem__(case_id, ok),
        ),
    )

    assert outcomes[target.read_probe] is False
    assert outcomes[target.verify_case] is False


def test_control_field_mutation_fails_even_when_count_and_marker_still_match():
    target = _target("memory_item")
    marker = "gdpr-marker-memory_item"

    class MutatingControlAdapter(FakeAdapter):
        async def read_contains(self, target, user: str, marker: str) -> bool:
            self.calls.append(("read", target.id, user))
            return marker in self.data.get((target.id, user), "")

        async def delete(self, user: str, action: str) -> bool:
            result = await super().delete(user, action)
            control_key = (target.id, "e2e-case-control")
            if control_key in self.data:
                self.data[control_key] = marker + "+mutated-field"
            return result

    outcomes: list[tuple[str, bool, str]] = []
    asyncio.run(
        run_deletion_bootstrap(
            targets=(target,),
            adapters={"memory": MutatingControlAdapter()},
            milestone="M-A",
            target_user="e2e-case-target",
            control_user="e2e-case-control",
            record=lambda case_id, ok, detail: outcomes.append(
                (case_id, ok, detail),
            ),
        ),
    )

    by_case = {case_id: ok for case_id, ok, _detail in outcomes}
    assert by_case[target.count_probe] is True
    assert by_case[target.read_probe] is True
    assert by_case[target.verify_case] is False
    assert all(marker not in detail for _case_id, _ok, detail in outcomes)
