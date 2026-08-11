"""parking-payment 契约测试（批 2 起走 payment-gateway，§9.17）。

文件名刻意不用 test_agent.py：各 agent tests/ 无 __init__.py 时
重名模块会让根目录 pytest 收集冲突（F7 修复前的规避）。

安全回归钉死的三件事：
- 两趟幂等键**逐字一致**（confirm_token 靠同键重取传递，键不含金额）；
- confirmed 分支**不重查费**（金额单一真相=网关快照，防「确认 5 元扣 6 元」）；
- confirm_token **不出现在任何输出面**（speech/ui_card/data/actions）。
"""
import asyncio
from types import SimpleNamespace

from agents._sdk.testing import run_handle
from agents.parking_payment.src.agent import ParkingPaymentAgent


def _auth_resp(status=1, payment_id="pay_test1", token="tok_secret_abc",
               amount=1500):
    return SimpleNamespace(
        payment_id=payment_id, require_confirm=True,
        confirm_prompt=f"确认支付 {amount // 100}.{amount % 100:02d} 元（停车费）吗？",
        confirm_token=token, status=status, provider_mode="mock",
        amount_cents=amount)


def _cap_resp(ok=True, qr="mockpay://pay_test1", error=""):
    return SimpleNamespace(ok=ok, receipt_id="pay_test1", error=error,
                           qr_content=qr, pay_url="", expires_at_ms=9999999999999,
                           trade_no="", provider_mode="mock")


class StubPayment:
    def __init__(self, auth=None, cap=None):
        self.auth_resp = auth or _auth_resp()
        self.cap_resp = cap or _cap_resp()
        self.authorize_calls: list[dict] = []
        self.capture_calls: list[tuple] = []

    async def authorize(self, **kw):
        self.authorize_calls.append(kw)
        return self.auth_resp

    async def capture(self, payment_id, confirm_token):
        self.capture_calls.append((payment_id, confirm_token))
        return self.cap_resp


def _agent(payment=None) -> ParkingPaymentAgent:
    return ParkingPaymentAgent(payment=payment or StubPayment())


def test_find_deprecated_redirects_to_nearby():
    """停车场发现已归 nearby（真高德）——parking.find 停用，本 Agent 只做缴费。"""
    res = asyncio.run(run_handle(
        _agent(), "parking.find", raw_text="附近有停车场吗"))
    assert res.status == "failed"
    assert "缴费" in res.speech


def test_pay_first_pass_authorizes_and_needs_confirm():
    """第一趟：查费→Authorize（渠道零动作）→NEED_CONFIRM，话术用网关 confirm_prompt。"""
    stub = StubPayment()
    res = asyncio.run(run_handle(
        _agent(stub), "parking.pay",
        slots={"plate": "沪A12345"}, raw_text="交停车费"))
    assert res.status == "need_confirm"
    assert any(a["require_confirm"] for a in res.actions)
    assert res.speech == stub.auth_resp.confirm_prompt   # 金额话术来自订单快照
    assert stub.authorize_calls[0]["amount_cents"] == 1500  # 真实查费金额进建单
    assert stub.capture_calls == []                      # 未确认绝不亮码


def test_pay_confirmed_captures_and_shows_qr():
    """第二趟：幂等重取→Capture→payment_qr 卡（mock 渠道必须打 _prov 角标）。"""
    stub = StubPayment()
    res = asyncio.run(run_handle(
        _agent(stub), "parking.pay",
        slots={"plate": "沪A12345"}, raw_text="确认",
        meta={"confirmed": "true"}))
    assert res.status == "ok"
    assert res.ui_card["type"] == "payment_qr"
    assert res.ui_card["qr_content"] == "mockpay://pay_test1"
    assert res.ui_card["_prov"]["mode"] == "mock"
    assert "扫码" in res.speech
    assert stub.capture_calls == [("pay_test1", "tok_secret_abc")]


def test_idempotency_key_identical_across_both_passes():
    """两趟同键——confirm_token 的官方传递通道就建立在这条等式上。"""
    stub = StubPayment()
    agent = _agent(stub)
    asyncio.run(run_handle(agent, "parking.pay",
                           slots={"plate": "沪A12345"}, raw_text="交停车费"))
    asyncio.run(run_handle(agent, "parking.pay",
                           slots={"plate": "沪A12345"}, raw_text="确认",
                           meta={"confirmed": "true"}))
    keys = [c["idempotency_key"] for c in stub.authorize_calls]
    assert len(keys) == 2 and keys[0] == keys[1]


def test_confirmed_pass_does_not_requery_fee():
    """confirmed 分支不重查费：金额漂移防线（§9.17）。"""
    stub = StubPayment()
    agent = _agent(stub)
    calls = {"n": 0}

    async def _counting_fee(lot_id, plate):
        calls["n"] += 1
        return 1500, ""

    agent.parking.get_fee = _counting_fee
    asyncio.run(run_handle(agent, "parking.pay",
                           slots={"plate": "沪A1"}, raw_text="确认",
                           meta={"confirmed": "true"}))
    assert calls["n"] == 0


def test_confirm_token_never_leaks_to_any_output():
    """token 只活在 handle() 栈内：speech/ui_card/data/actions 全不出现。"""
    stub = StubPayment()
    agent = _agent(stub)
    for meta in ({}, {"confirmed": "true"}):
        res = asyncio.run(run_handle(agent, "parking.pay",
                                     slots={"plate": "沪A12345"},
                                     raw_text="x", meta=meta))
        dump = repr([res.speech, res.ui_card, res.data, res.actions,
                     res.follow_up])
        assert "tok_secret_abc" not in dump


def test_already_captured_says_paid_not_double_pay():
    stub = StubPayment(auth=_auth_resp(status=2))     # CAPTURED
    res = asyncio.run(run_handle(_agent(stub), "parking.pay",
                                 slots={"plate": "沪A1"}, raw_text="确认",
                                 meta={"confirmed": "true"}))
    assert res.status == "ok" and "已经支付过" in res.speech
    assert stub.capture_calls == []


def test_repayable_terminal_asks_user_to_restart():
    stub = StubPayment(auth=_auth_resp(status=6))     # EXPIRED
    res = asyncio.run(run_handle(_agent(stub), "parking.pay",
                                 slots={"plate": "沪A1"}, raw_text="确认",
                                 meta={"confirmed": "true"}))
    assert res.status == "ok" and "重新" in res.speech
    assert stub.capture_calls == []


def test_gateway_unavailable_degrades_honestly():
    """网关不可达：R9 诚实降级（OK+话术），不假装支付在进行、不产生动作。"""
    class DownPayment(StubPayment):
        async def authorize(self, **kw):
            return None

    res = asyncio.run(run_handle(_agent(DownPayment()), "parking.pay",
                                 slots={"plate": "沪A1"}, raw_text="交停车费"))
    assert res.status == "ok" and "暂时不可用" in res.speech
    assert res.actions == []


def test_capture_failure_reported_honestly():
    stub = StubPayment(cap=_cap_resp(ok=False, qr="", error="渠道下单失败：限额"))
    res = asyncio.run(run_handle(_agent(stub), "parking.pay",
                                 slots={"plate": "沪A1"}, raw_text="确认",
                                 meta={"confirmed": "true"}))
    assert res.status == "ok" and "限额" in res.speech
    assert not res.ui_card


def test_query_fee_reads_without_paying():
    """只查金额，**一分钱都不动**：不产生任何 action，也不进确认闸。

    出处：对抗测试 findings §6.1（`capability_gap`）。此前只有 `parking.pay` 一个能力，
    「我想先知道多少钱」系统答不了，模型被迫选到唯一那个。
    """
    res = asyncio.run(run_handle(
        _agent(), "parking.query_fee",
        slots={"plate": "沪A12345"}, raw_text="停车费多少钱"))
    assert res.status == "ok", res.status
    assert res.actions == [], "查询能力不许产生任何动作"
    assert "15元" in res.speech
    assert res.ui_card and res.ui_card["type"] == "parking_fee"


def test_query_fee_is_not_confirm_gated():
    """反向：查费**不该**要二次确认——把只读操作也塞进确认闸，用户会学会无脑点确认。"""
    manifest = _agent().manifest
    caps = {c.intent: c for c in manifest.capabilities}
    assert not getattr(caps["parking.query_fee"], "require_confirm", False)
    assert caps["parking.pay"].require_confirm, "付款那条的红线一分不许减"


def test_query_fee_failure_is_reported_not_swallowed():
    """provider 报错时如实失败，不许拿一个编出来的金额糊弄过去。"""
    agent = _agent()

    async def _boom(lot_id, plate):
        return 0, "商户系统超时"

    agent.parking.get_fee = _boom
    res = asyncio.run(run_handle(agent, "parking.query_fee",
                                 slots={"plate": "沪A1"}, raw_text="停车费多少钱"))
    assert res.status == "failed" and "商户系统超时" in res.speech
