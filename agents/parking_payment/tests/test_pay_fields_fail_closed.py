"""缴费确认前的字段完整性闸（阶段 1 / 卡 Q10，QA 轮 I-027）。

现象：停车费用卡上明明显示着「粤B12345」，继续说缴费时 `parking.pay` 的 payload
里 `plate` 却是空串、`order_id` 是占位符 `"current"`——**一次没有车牌的付款确认**
就这样进到了用户面前。

判据：**支付确认必须完整回显 plate / order / amount，缺任一项不得进入确认。**
这是确定性校验，与 LLM 无关——同 B1「安全不变量放在唯一出口」。

⚠ 断言先红再绿（AGENTS.md §4.3）。
"""
import asyncio
from types import SimpleNamespace

from agents._sdk.testing import run_handle
from agents.parking_payment.src.agent import ParkingPaymentAgent


class _StubPayment:
    """与 `test_parking_payment_agent.py` 同款替身——本文件只关心字段闸，
    不重复那边已经钉死的幂等/token 不外泄断言。"""

    async def authorize(self, **kw):
        return SimpleNamespace(
            payment_id="pay_t", require_confirm=True,
            confirm_prompt="确认支付 15.00 元（停车费）吗？",
            confirm_token="tok", status=1, provider_mode="mock",
            amount_cents=1500)

    async def capture(self, payment_id, confirm_token):
        return SimpleNamespace(ok=True, receipt_id="r", error="",
                               qr_content="mockpay://x", pay_url="",
                               expires_at_ms=0, trade_no="", provider_mode="mock")


def _agent():
    return ParkingPaymentAgent(payment=_StubPayment())


def test_pay_without_plate_does_not_reach_confirm():
    """无车牌 → 不得进入 NEED_CONFIRM，必须问回来。

    车牌是**用户填得出来**的东西（他自己的车、且费用卡上就写着），
    所以这里用 NEED_SLOT 是正当的——与「不许把用户填不了的东西声明成
    missing_slots」那条纪律不冲突（商户门店槽那次栽的是用户根本填不了）。
    """
    res = asyncio.run(run_handle(_agent(), "parking.pay", slots={}))
    assert res.status != "need_confirm", "缺车牌不得进入付款确认"
    assert res.status == "need_slot"
    assert "plate" in (res.missing_slots or []), \
        f"应点名缺的是车牌，实得 {res.missing_slots}"


def test_pay_with_plate_reaches_confirm_and_echoes_all_three():
    """反向对照：字段齐全时必须照常走到确认，且三项都回显。"""
    res = asyncio.run(run_handle(
        _agent(), "parking.pay",
        slots={"plate": "粤B12345", "order_id": "PK-20260815-001"}))
    assert res.status == "need_confirm"
    card = res.ui_card or {}
    assert card.get("plate") == "粤B12345", f"确认卡必须回显车牌：{card}"
    assert card.get("order_id") == "PK-20260815-001", f"确认卡必须回显订单：{card}"
    assert card.get("amount"), f"确认卡必须回显金额：{card}"
    # 动作 payload 同样要带全（HMI 点确认时原样回发）。
    # ⚠ actions 是 dict 不是对象——首版这里写成 `.payload` 属性访问，是**尺子写错**。
    act = (res.actions or [{}])[0]
    payload = (act.get("payload") if isinstance(act, dict) else {}) or {}
    assert payload.get("plate") == "粤B12345", f"动作 payload 必须带车牌：{act}"


def test_query_fee_without_plate_still_works():
    """反向对照：**查费不是付款**，不该被这道闸拦住。

    修安全闸最容易顺手把只读路径也焊死——那会把「不敢付」变成「不敢问」。
    """
    res = asyncio.run(run_handle(_agent(), "parking.query_fee", slots={}))
    assert res.status == "ok"
    assert (res.ui_card or {}).get("type") == "parking_fee"
