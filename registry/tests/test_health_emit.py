import asyncio

from registry.main import emit_all_health
from registry.store import Store


class _Manifest:
    agent_id = "navigation"
    deployment = "cloud"
    kind = "agent"
    requires_permissions = []
    capabilities = []


def test_emit_all_health_sends_each_agent():
    store = Store()
    store.register(_Manifest(), "navigation:50061")
    sent = []

    class Emitter:
        async def emit_health(self, **kwargs):
            sent.append(kwargs)

    asyncio.run(emit_all_health(store, Emitter()))

    assert sent
    assert sent[0]["agent_id"] == "navigation"
    assert sent[0]["healthy"] is True
    assert sent[0]["deployment"] == "cloud"
