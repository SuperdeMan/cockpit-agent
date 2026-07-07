"""parking-payment 契约测试（补 F10 缺口）。

文件名刻意不用 test_agent.py：各 agent tests/ 无 __init__.py 时
重名模块会让根目录 pytest 收集冲突（F7 修复前的规避）。
"""
import asyncio

from agents._sdk.testing import run_handle
from agents.parking_payment.src.agent import ParkingPaymentAgent


def test_find_deprecated_redirects_to_nearby():
    """停车场发现已归 nearby（真高德）——parking.find 停用，本 Agent 只做缴费。"""
    res = asyncio.run(run_handle(
        ParkingPaymentAgent(), "parking.find", raw_text="附近有停车场吗"))
    assert res.status == "failed"
    assert "缴费" in res.speech


def test_pay_requires_confirm():
    res = asyncio.run(run_handle(
        ParkingPaymentAgent(), "parking.pay",
        slots={"plate": "沪A12345"}, raw_text="交停车费"))
    assert res.status == "need_confirm"
    assert any(a["require_confirm"] for a in res.actions)


def test_pay_confirmed_completes():
    """F1 确认闭环：带 confirmed 标记时真正支付，返回凭证。"""
    res = asyncio.run(run_handle(
        ParkingPaymentAgent(), "parking.pay",
        slots={"plate": "沪A12345"}, raw_text="确认",
        meta={"confirmed": "true"}))
    assert res.status == "ok"
    assert "已为您支付" in res.speech
    assert res.ui_card["type"] == "payment_receipt"
    assert res.ui_card["receipt_id"].startswith("rcpt_")
