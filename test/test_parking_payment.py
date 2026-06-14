"""parking-payment Provider 测试。独立于 proto 生成代码。"""
import asyncio
import sys
import os

# Add the agent's src to path, ensuring providers package is fresh
_src_dir = os.path.join(os.path.dirname(__file__), "..", "agents", "parking_payment", "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)


def _load():
    """Import providers.mock in isolation to avoid namespace pollution."""
    import importlib
    # Force reimport of the providers package from the correct path
    if "providers" in sys.modules:
        _saved = sys.modules.pop("providers")
        _saved_mock = sys.modules.pop("providers.mock", None)
        _saved_base = sys.modules.pop("providers.base", None)
    else:
        _saved = None
    try:
        from providers.mock import MockParkingProvider
        return MockParkingProvider
    finally:
        # Restore original if any
        if _saved is not None:
            sys.modules["providers"] = _saved


MockParkingProvider = _load()


def test_find():
    p = MockParkingProvider()
    lots = asyncio.run(p.find())
    assert len(lots) == 3
    assert lots[0].name == "停车场1"
    assert lots[0].available == 10


def test_get_fee():
    p = MockParkingProvider()
    fee_cents, err = asyncio.run(p.get_fee("lot1", "沪A12345"))
    assert fee_cents == 1500
    assert err == ""
