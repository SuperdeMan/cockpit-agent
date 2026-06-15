"""Agent SDK 周期重注册：registry 重启/不可达后能自动补注册。"""
import asyncio

from agents._sdk.server import _reregister_loop


class _FakeRegistry:
    def __init__(self, fail: bool = False):
        self.calls = 0
        self.fail = fail

    async def register(self, manifest, endpoint):
        self.calls += 1
        if self.fail:
            raise RuntimeError("registry unavailable")
        return f"lease-{self.calls}"


def _drive(loop_coro, seconds: float = 0.05):
    async def run():
        task = asyncio.create_task(loop_coro)
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(run())


def test_reregister_loop_periodically_registers():
    reg = _FakeRegistry()
    _drive(_reregister_loop(reg, object(), "host:50060", interval=0.01))
    assert reg.calls >= 2, "应周期性重注册（幂等 upsert）"


def test_reregister_loop_survives_registry_failure():
    reg = _FakeRegistry(fail=True)
    _drive(_reregister_loop(reg, object(), "host:50060", interval=0.01))
    # registry 一直失败也持续重试、不崩溃，恢复后下个周期即补注册
    assert reg.calls >= 2
