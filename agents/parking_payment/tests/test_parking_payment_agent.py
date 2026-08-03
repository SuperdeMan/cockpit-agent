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


def test_query_fee_reads_without_paying():
    """只查金额，**一分钱都不动**：不产生任何 action，也不进确认闸。

    出处：对抗测试 findings §6.1（`capability_gap`）。此前只有 `parking.pay` 一个能力，
    「我想先知道多少钱」系统答不了，模型被迫选到唯一那个。
    """
    res = asyncio.run(run_handle(
        ParkingPaymentAgent(), "parking.query_fee",
        slots={"plate": "沪A12345"}, raw_text="停车费多少钱"))
    assert res.status == "ok", res.status
    assert res.actions == [], "查询能力不许产生任何动作"
    assert "15元" in res.speech
    assert res.ui_card and res.ui_card["type"] == "parking_fee"


def test_query_fee_is_not_confirm_gated():
    """反向：查费**不该**要二次确认——把只读操作也塞进确认闸，用户会学会无脑点确认。"""
    manifest = ParkingPaymentAgent().manifest
    caps = {c.intent: c for c in manifest.capabilities}
    assert not getattr(caps["parking.query_fee"], "require_confirm", False)
    assert caps["parking.pay"].require_confirm, "付款那条的红线一分不许减"


def test_query_fee_failure_is_reported_not_swallowed():
    """provider 报错时如实失败，不许拿一个编出来的金额糊弄过去。"""
    agent = ParkingPaymentAgent()

    async def _boom(lot_id, plate):
        return 0, "商户系统超时"

    agent.parking.get_fee = _boom
    res = asyncio.run(run_handle(agent, "parking.query_fee",
                                 slots={"plate": "沪A1"}, raw_text="停车费多少钱"))
    assert res.status == "failed" and "商户系统超时" in res.speech
