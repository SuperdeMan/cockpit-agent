"""parking-payment Provider 测试。独立于 proto 生成代码。"""
import asyncio
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agents", "parking_payment", "src"))
from providers.mock import MockParkingProvider


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
