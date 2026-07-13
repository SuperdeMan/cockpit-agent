import asyncio
import logging

import registry.store as store_mod
from registry.health import probe_all
from registry.store import Store


class _Manifest:
    agent_id = "navigation"
    deployment = "cloud"
    kind = "agent"
    requires_permissions = []
    capabilities = []


def test_probe_marks_agent_unhealthy_after_threshold():
    store = Store()
    store.register(_Manifest(), "navigation:50061")

    async def unavailable(endpoint):
        return False

    for _ in range(3):
        asyncio.run(probe_all(store, unavailable))

    assert store.all()[0].healthy is False
    assert store.all()[0].fail_count == 3


def test_probe_recovers_agent_after_success():
    store = Store()
    store.register(_Manifest(), "navigation:50061")
    store.mark_unhealthy("navigation")
    store.mark_unhealthy("navigation")
    store.mark_unhealthy("navigation")

    async def available(endpoint):
        return True

    asyncio.run(probe_all(store, available))

    assert store.all()[0].healthy is True
    assert store.all()[0].fail_count == 0


def test_probe_skips_virtual_endpoints():
    store = Store()
    store.register(_Manifest(), "edge://vehicle")
    calls = []

    async def checker(endpoint):
        calls.append(endpoint)
        return False

    asyncio.run(probe_all(store, checker))

    assert calls == []
    assert store.all()[0].healthy is True
    assert store.all()[0].fail_count == 0


def test_unhealthy_warning_only_on_transition(caplog):
    """不健康告警只在健康→不健康转变沿打一次，不随后续探测周期刷屏
    （food-ordering 残留曾每 5s 刷一条 WARNING 刷了 8 天）。"""
    store = Store()
    store.register(_Manifest(), "navigation:50061")
    with caplog.at_level(logging.WARNING, logger="registry.store"):
        for _ in range(10):
            store.mark_unhealthy("navigation")
    warns = [r for r in caplog.records if "marked unhealthy" in r.getMessage()]
    assert len(warns) == 1
    # 恢复后再失效 → 允许再告警一次（新的转变沿）
    store.mark_healthy("navigation")
    with caplog.at_level(logging.WARNING, logger="registry.store"):
        for _ in range(3):
            store.mark_unhealthy("navigation")
    warns = [r for r in caplog.records if "marked unhealthy" in r.getMessage()]
    assert len(warns) == 2


def test_evicts_after_prolonged_failure(monkeypatch):
    """连续失败达 EVICT_FAIL_COUNT 整体剔除（改名/下线残留不永生）；重注册即恢复。"""
    monkeypatch.setattr(store_mod, "EVICT_FAIL_COUNT", 5)
    store = Store()
    store.register(_Manifest(), "navigation:50061")
    for _ in range(5):
        store.mark_unhealthy("navigation")
    assert store.all() == []
    store.register(_Manifest(), "navigation:50061")
    assert store.all()[0].healthy is True


def test_evict_disabled_with_zero(monkeypatch):
    monkeypatch.setattr(store_mod, "EVICT_FAIL_COUNT", 0)
    store = Store()
    store.register(_Manifest(), "navigation:50061")
    for _ in range(50):
        store.mark_unhealthy("navigation")
    assert len(store.all()) == 1
    assert store.all()[0].healthy is False
