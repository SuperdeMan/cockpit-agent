import asyncio

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
