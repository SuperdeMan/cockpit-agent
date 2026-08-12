"""Production privacy target registry shared by staged deletion/redaction sagas.

This module is deliberately dependency-free.  Production code reads this
registry; ``test/e2e_manifest.yaml`` is only an acceptance mirror.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType


MEMORY_ADAPTER = "memory.store:MemoryStore"
REMINDER_ADAPTER = "agents.reminder.src.store:ReminderStore"
SCENE_ADAPTER = "agents.scene_orchestrator.src.store:SceneStore"
OBSERVABILITY_ADAPTER = "observability.collector.db:ObsDB"
LEDGER_ADAPTER = "agents._sdk.ledger:TaskLedger"
PROACTIVE_ADAPTER = "proactive.governor:Governor"
PAYMENT_ADAPTER = "payment-gateway/store.py:PaymentStore"
MCP_ADAPTER = "agents.mcp_bridge.src.agent:McpBridgeAgent"
CLOUD_SESSION_ADAPTER = "orchestrator.cloud.session:SessionStore"


class PrivacyRegistryError(ValueError):
    """The static production privacy registry violates its own contract."""


def _build_privacy_adapters(
    entries: Sequence[tuple[str, str]],
) -> Mapping[str, str]:
    adapters: dict[str, str] = {}
    for index, entry in enumerate(entries):
        if (
            not isinstance(entry, (list, tuple))
            or len(entry) != 2
        ):
            raise PrivacyRegistryError(
                f"privacy adapter entry {index} must be a key/value pair",
            )
        key, adapter = entry
        if not isinstance(key, str) or not key:
            raise PrivacyRegistryError(
                f"privacy adapter entry {index} has an empty key",
            )
        if not isinstance(adapter, str) or not adapter:
            raise PrivacyRegistryError(
                f"privacy adapter {key!r} has an empty implementation",
            )
        if key in adapters:
            raise PrivacyRegistryError(
                f"privacy adapter key is duplicated: {key!r}",
            )
        adapters[key] = adapter
    return MappingProxyType(adapters)


PRIVACY_ADAPTERS = _build_privacy_adapters((
    ("memory", MEMORY_ADAPTER),
    ("reminder", REMINDER_ADAPTER),
    ("scene", SCENE_ADAPTER),
    ("observability", OBSERVABILITY_ADAPTER),
    ("ledger", LEDGER_ADAPTER),
    ("proactive", PROACTIVE_ADAPTER),
    ("payment", PAYMENT_ADAPTER),
    ("mcp", MCP_ADAPTER),
    ("cloud_session", CLOUD_SESSION_ADAPTER),
))

MILESTONE_ORDER = MappingProxyType({
    "M-A": 0,
    "M-B": 1,
    "M-C": 2,
    "M-D": 3,
})

OWNER_FIELDS = ("user_id", "occupant_id")
_PROBE_FIELDS = (
    "seed_case",
    "count_probe",
    "read_probe",
    "verify_case",
)


@dataclass(frozen=True)
class PrivacyTargetSpec:
    """One stable personal-data target and its staged acceptance contract."""

    id: str
    backend: str
    adapter_key: str
    adapter: str
    storage_variants: tuple[str, ...]
    lifecycle: str
    enforced_from: str
    owner_fields: tuple[str, ...]
    seed_case: str
    count_probe: str
    read_probe: str
    verify_case: str
    delete_action: str = ""
    retention_reason: str = ""
    retain_or_redact_action: str = ""
    policy_flags: tuple[str, ...] = ()


def _validate_privacy_registry(
    targets: Sequence[PrivacyTargetSpec],
    adapters: Mapping[str, str],
) -> None:
    seen_ids: set[str] = set()
    probe_owners: dict[str, str] = {}
    for index, target in enumerate(targets):
        if not isinstance(target.id, str) or not target.id:
            raise PrivacyRegistryError(
                f"privacy target {index} has an empty id",
            )
        if target.id in seen_ids:
            raise PrivacyRegistryError(
                f"duplicate privacy target id: {target.id!r}",
            )
        seen_ids.add(target.id)
        if not isinstance(target.adapter_key, str) or not target.adapter_key:
            raise PrivacyRegistryError(
                f"privacy target {target.id!r} has an empty adapter_key",
            )
        if target.adapter_key not in adapters:
            raise PrivacyRegistryError(
                f"privacy target {target.id!r} adapter_key "
                f"{target.adapter_key!r} is absent from PRIVACY_ADAPTERS",
            )
        if target.adapter != adapters[target.adapter_key]:
            raise PrivacyRegistryError(
                f"privacy target {target.id!r} adapter does not match "
                f"PRIVACY_ADAPTERS[{target.adapter_key!r}]",
            )
        probes_list: list[str] = []
        for field in _PROBE_FIELDS:
            probe_id = getattr(target, field)
            if not isinstance(probe_id, str) or not probe_id:
                raise PrivacyRegistryError(
                    f"privacy target {target.id!r} {field} must be a "
                    "non-empty string",
                )
            probes_list.append(probe_id)
        probes = tuple(probes_list)
        if len(set(probes)) != len(_PROBE_FIELDS):
            raise PrivacyRegistryError(
                f"privacy target {target.id!r} probe IDs must be distinct",
            )
        for probe_id in probes:
            previous = probe_owners.get(probe_id)
            if previous is not None:
                raise PrivacyRegistryError(
                    f"privacy probe ID {probe_id!r} is reused by "
                    f"{previous!r} and {target.id!r}",
                )
            probe_owners[probe_id] = target.id


PRIVACY_TARGETS = (
    PrivacyTargetSpec(
        id="memory_item",
        backend="postgres",
        adapter_key="memory",
        adapter=PRIVACY_ADAPTERS["memory"],
        storage_variants=("memory_item", "MemoryVectorStore._mem"),
        lifecycle="deletable",
        enforced_from="M-A",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_ma_memory_item_seed",
        count_probe="gdpr_ma_memory_item_count",
        read_probe="gdpr_ma_memory_item_read",
        delete_action="forget_user",
        verify_case="gdpr_ma_memory_item_verify",
    ),
    PrivacyTargetSpec(
        id="memory_relation",
        backend="postgres",
        adapter_key="memory",
        adapter=PRIVACY_ADAPTERS["memory"],
        storage_variants=("memory_relation", "MemoryVectorStore._rel"),
        lifecycle="deletable",
        enforced_from="M-A",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_ma_memory_relation_seed",
        count_probe="gdpr_ma_memory_relation_count",
        read_probe="gdpr_ma_memory_relation_read",
        delete_action="forget_user",
        verify_case="gdpr_ma_memory_relation_verify",
    ),
    PrivacyTargetSpec(
        id="voiceprint",
        backend="postgres",
        adapter_key="memory",
        adapter=PRIVACY_ADAPTERS["memory"],
        storage_variants=("voiceprint", "MemoryVectorStore._vp"),
        lifecycle="deletable",
        enforced_from="M-A",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_ma_voiceprint_seed",
        count_probe="gdpr_ma_voiceprint_count",
        read_probe="gdpr_ma_voiceprint_read",
        delete_action="forget_user",
        verify_case="gdpr_ma_voiceprint_verify",
    ),
    PrivacyTargetSpec(
        id="profile_identity",
        backend="redis_or_memory",
        adapter_key="memory",
        adapter=PRIVACY_ADAPTERS["memory"],
        storage_variants=(
            "redis.profile.identity",
            "MemoryStore._profiles.identity",
        ),
        lifecycle="deletable",
        enforced_from="M-A",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_ma_profile_identity_seed",
        count_probe="gdpr_ma_profile_identity_count",
        read_probe="gdpr_ma_profile_identity_read",
        delete_action="forget_user",
        verify_case="gdpr_ma_profile_identity_verify",
    ),
    PrivacyTargetSpec(
        id="session_history",
        backend="redis_or_memory",
        adapter_key="memory",
        adapter=PRIVACY_ADAPTERS["memory"],
        storage_variants=(
            "redis.sess",
            "redis.user_sessions",
            "MemoryStore._mem",
            "MemoryStore._mem_user_sessions",
        ),
        lifecycle="deletable",
        enforced_from="M-A",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_ma_session_history_seed",
        count_probe="gdpr_ma_session_history_count",
        read_probe="gdpr_ma_session_history_read",
        delete_action="forget_user",
        verify_case="gdpr_ma_session_history_verify",
    ),
    PrivacyTargetSpec(
        id="profile_places",
        backend="postgres_redis_or_memory",
        adapter_key="memory",
        adapter=PRIVACY_ADAPTERS["memory"],
        storage_variants=(
            "memory_item_scope_profile_places",
            "redis.profile.places",
            "MemoryStore._profiles.places",
        ),
        lifecycle="deletable",
        enforced_from="M-B",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_mb_profile_places_seed",
        count_probe="gdpr_mb_profile_places_count",
        read_probe="gdpr_mb_profile_places_read",
        delete_action="privacy_user_all",
        verify_case="gdpr_mb_profile_places_verify",
    ),
    PrivacyTargetSpec(
        id="reminder_item",
        backend="postgres_or_memory",
        adapter_key="reminder",
        adapter=PRIVACY_ADAPTERS["reminder"],
        storage_variants=("reminder_item", "ReminderStore._mem"),
        lifecycle="deletable",
        enforced_from="M-B",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_mb_reminder_item_seed",
        count_probe="gdpr_mb_reminder_item_count",
        read_probe="gdpr_mb_reminder_item_read",
        delete_action="privacy_user_all",
        verify_case="gdpr_mb_reminder_item_verify",
    ),
    PrivacyTargetSpec(
        id="reminder_shared_state",
        backend="profile_shared_state_kv",
        adapter_key="reminder",
        adapter=PRIVACY_ADAPTERS["reminder"],
        storage_variants=(
            "reminders_active",
            "reminder_pending",
        ),
        lifecycle="deletable",
        enforced_from="M-B",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_mb_reminder_state_seed",
        count_probe="gdpr_mb_reminder_state_count",
        read_probe="gdpr_mb_reminder_state_read",
        delete_action="privacy_user_all",
        verify_case="gdpr_mb_reminder_state_verify",
    ),
    PrivacyTargetSpec(
        id="scene_item",
        backend="postgres_or_memory",
        adapter_key="scene",
        adapter=PRIVACY_ADAPTERS["scene"],
        storage_variants=("scene_item", "SceneStore._mem"),
        lifecycle="deletable",
        enforced_from="M-B",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_mb_scene_item_seed",
        count_probe="gdpr_mb_scene_item_count",
        read_probe="gdpr_mb_scene_item_read",
        delete_action="privacy_user_all",
        verify_case="gdpr_mb_scene_item_verify",
    ),
    PrivacyTargetSpec(
        id="observability_raw_content",
        backend="sqlite",
        adapter_key="observability",
        adapter=PRIVACY_ADAPTERS["observability"],
        storage_variants=("turns", "spans", "llm_calls", "logs"),
        lifecycle="retained_audit",
        enforced_from="M-B",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_mb_observability_seed",
        count_probe="gdpr_mb_observability_count",
        read_probe="gdpr_mb_observability_read",
        retention_reason="diagnostic_metrics_without_raw_owner_content",
        retain_or_redact_action="observability_redact_owner",
        verify_case="gdpr_mb_observability_verify",
        policy_flags=(
            "owner_columns_required",
            "ownerless_redact_before_insert",
            "legacy_ownerless_redact",
            "probe_all_storage_variants",
            "badcase_not_exempt_from_owner_redaction",
        ),
    ),
    PrivacyTargetSpec(
        id="task_ledger",
        backend="postgres",
        adapter_key="ledger",
        adapter=PRIVACY_ADAPTERS["ledger"],
        storage_variants=("task_ledger",),
        lifecycle="deletable",
        enforced_from="M-C",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_mc_task_ledger_seed",
        count_probe="gdpr_mc_task_ledger_count",
        read_probe="gdpr_mc_task_ledger_read",
        delete_action="privacy_user_all",
        verify_case="gdpr_mc_task_ledger_verify",
    ),
    PrivacyTargetSpec(
        id="proactive_process_queue",
        backend="process_memory",
        adapter_key="proactive",
        adapter=PRIVACY_ADAPTERS["proactive"],
        storage_variants=("Governor._pending", "Governor._deferred"),
        lifecycle="deletable",
        enforced_from="M-C",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_mc_proactive_queue_seed",
        count_probe="gdpr_mc_proactive_queue_count",
        read_probe="gdpr_mc_proactive_queue_read",
        delete_action="privacy_user_all",
        verify_case="gdpr_mc_proactive_queue_verify",
    ),
    PrivacyTargetSpec(
        # M-C 可靠投递账本。**payload 里存着话术与卡片摘要**——那是个人数据；
        # 删记忆却留着投递账本，与「删了长期记忆但对话原文还在」是同一种假删除。
        id="proactive_delivery",
        backend="postgres",
        adapter_key="proactive",
        adapter=PRIVACY_ADAPTERS["proactive"],
        storage_variants=("proactive_delivery", "DeliveryStore._mem"),
        lifecycle="deletable",
        enforced_from="M-C",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_mc_proactive_delivery_seed",
        count_probe="gdpr_mc_proactive_delivery_count",
        read_probe="gdpr_mc_proactive_delivery_read",
        delete_action="privacy_user_all",
        verify_case="gdpr_mc_proactive_delivery_verify",
    ),
    PrivacyTargetSpec(
        # 2026-08-11 支付真实化：Redis 为主形态（hash payment:order:* + 幂等
        # payment:idem:*），进程内存是 Redis 不可达时的兜底——兜底存在就必须登记。
        # 脱敏动作 payment_redact_owner = PaymentStore.redact_owner（清 owner 字段
        # 保留金额/状态审计，对齐 retained_audit 语义）。
        id="payment_order",
        backend="redis_or_memory",
        adapter_key="payment",
        adapter=PRIVACY_ADAPTERS["payment"],
        storage_variants=("payment:order:*", "payment:idem:*",
                          "PaymentStore._mem", "PaymentStore._idem"),
        lifecycle="retained_audit",
        enforced_from="M-D",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_md_payment_order_seed",
        count_probe="gdpr_md_payment_order_count",
        read_probe="gdpr_md_payment_order_read",
        retention_reason="financial_audit_and_chargeback_window",
        retain_or_redact_action="payment_redact_owner",
        verify_case="gdpr_md_payment_order_verify",
    ),
    PrivacyTargetSpec(
        # Planner wait-confirm/wait-slot state and focus are short lived, but
        # can contain the user's goal, trusted POI dependency results and
        # interaction context.  The pending merchant step itself is minimised
        # before persistence; privacy_user_all still deletes both key families
        # immediately through the cloud SessionStore adapter.
        id="planner_pending_session",
        backend="redis_or_memory",
        adapter_key="cloud_session",
        adapter=PRIVACY_ADAPTERS["cloud_session"],
        storage_variants=("planner:sess:*", "planner:sess-owner:*",
                          "planner:sess-owner-fence:*", "planner:focus:*"),
        lifecycle="deletable",
        enforced_from="M-D",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_md_planner_pending_seed",
        count_probe="gdpr_md_planner_pending_count",
        read_probe="gdpr_md_planner_pending_read",
        delete_action="privacy_user_all",
        verify_case="gdpr_md_planner_pending_verify",
    ),
    PrivacyTargetSpec(
        # Merchant checkout snapshots contain selected public store coordinates,
        # product/specification choices, amount and upstream request arguments.
        # They expire after ten minutes, but privacy_user_all coordinates an
        # explicit, provable delete through the hashed owner index rather than
        # waiting for TTL.  An active merchant operation returns pending and
        # is retried after its lease is released.
        id="merchant_draft",
        backend="redis",
        adapter_key="mcp",
        adapter=PRIVACY_ADAPTERS["mcp"],
        storage_variants=("mcp:merchant:draft:*", "mcp:merchant:current:*",
                          "mcp:merchant:owner:*"),
        lifecycle="deletable",
        enforced_from="M-D",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_md_merchant_draft_seed",
        count_probe="gdpr_md_merchant_draft_count",
        read_probe="gdpr_md_merchant_draft_read",
        delete_action="privacy_user_all",
        verify_case="gdpr_md_merchant_draft_verify",
    ),
    PrivacyTargetSpec(
        id="mcp_demo_order",
        backend="external_process_memory",
        adapter_key="mcp",
        adapter=PRIVACY_ADAPTERS["mcp"],
        storage_variants=("demo_coffee._ORDERS", "demo_coffee._BY_IDEM"),
        lifecycle="external_reference",
        enforced_from="M-D",
        owner_fields=OWNER_FIELDS,
        seed_case="gdpr_md_mcp_external_seed",
        count_probe="gdpr_md_mcp_external_count",
        read_probe="gdpr_md_mcp_external_read",
        retention_reason="external_merchant_is_system_of_record",
        retain_or_redact_action="mcp_external_unlink",
        verify_case="gdpr_md_mcp_external_verify",
    ),
)

_validate_privacy_registry(PRIVACY_TARGETS, PRIVACY_ADAPTERS)


def targets_for_milestone(name: str) -> tuple[PrivacyTargetSpec, ...]:
    """Return targets whose enforcement starts no later than ``name``."""

    if name not in MILESTONE_ORDER:
        raise ValueError(f"unknown privacy milestone: {name!r}")
    current = MILESTONE_ORDER[name]
    return tuple(
        target
        for target in PRIVACY_TARGETS
        if MILESTONE_ORDER[target.enforced_from] <= current
    )


__all__ = [
    "MILESTONE_ORDER",
    "PRIVACY_ADAPTERS",
    "PRIVACY_TARGETS",
    "PrivacyTargetSpec",
    "targets_for_milestone",
]
