"""支付链接闭环契约（§9.9 批 3）：_dig 声明式提取 / 域名白名单第一层 /
登记参数（MERCHANT_HOSTED）/ 登记失败 fail closed 不透传裸链接 / demo 三重标注保持。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from agents.mcp_bridge.src.agent import McpBridgeAgent
from agents.mcp_bridge.src.admission import ServerSpec, ToolSpec


class SpyPayment:
    def __init__(self, resp="default"):
        self.calls: list[dict] = []
        self._resp = resp

    async def authorize(self, **kw):
        self.calls.append(kw)
        if self._resp == "none":
            return None
        if self._resp == "boom":
            raise RuntimeError("gateway down")
        return SimpleNamespace(payment_id="pay_m1", confirm_token="",
                               confirm_prompt="", status=5, provider_mode="real",
                               amount_cents=int(kw.get("amount_cents") or 0),
                               qr_svg="data:image/svg+xml;base64,QUJD")


def _binding(agent, *, pay_url_hosts=None, demo=False, locator="payH5Url"):
    server = ServerSpec(id="mcd-test", command=[], version="", tools=[],
                        demo=demo,
                        pay_url_hosts=(["m.mcd.cn"] if pay_url_hosts is None
                                       else pay_url_hosts))
    tool = ToolSpec(name="order.create", intent="mcd.order", write=True,
                    require_confirm=True, compensate_policy="abandon_unpaid",
                    confirm_prompt="x", pay_url_locator=locator)
    return SimpleNamespace(server=server, tool=tool, client=None)


def _agent(resp="default") -> tuple[McpBridgeAgent, SpyPayment]:
    payment = SpyPayment(resp)
    agent = McpBridgeAgent(payment=payment)
    return agent, payment


_CTX = SimpleNamespace(user_id="u1", vehicle_id="v1", session_id="s1")
_INTENT = SimpleNamespace(name="mcd.order", slots={})


def test_dig_dotted_path():
    dig = McpBridgeAgent._dig
    assert dig({"payH5Url": "https://x/1"}, "payH5Url") == "https://x/1"
    assert dig({"order": {"payUrl": "https://x/2"}}, "order.payUrl") == "https://x/2"
    assert dig({"payH5Url": 123}, "payH5Url") == ""       # 非字符串不认
    assert dig({}, "payH5Url") == ""
    assert dig({"a": "x"}, "") == ""


def test_register_builds_qr_card_with_gateway_svg():
    agent, payment = _agent()
    order = {"order_id": "mcd-001", "amount_cents": 3500,
             "payH5Url": "https://m.mcd.cn/mcp/scanToPay?orderId=mcd-001"}
    card = asyncio.run(agent._register_merchant_payment(
        _binding(agent), _CTX, _INTENT, order))
    assert card["type"] == "payment_qr"
    assert card["payment_id"] == "pay_m1"
    assert card["qr_svg"].startswith("data:image/svg")
    assert card["amount"] == "35.00元"
    assert card["merchant_note"] == "订单状态以商家为准"
    kw = payment.calls[0]
    assert kw["channel"] == 3                      # MERCHANT_HOSTED
    assert kw["external_pay_url"].startswith("https://m.mcd.cn/")
    assert kw["external_order_ref"] == "mcd-001"
    assert kw["scene"] == "mcd.order"
    assert kw["amount_cents"] == 3500


@pytest.mark.parametrize("bad_url", [
    "https://evil.example.com/pay",     # 域名不在白名单
    "http://m.mcd.cn/pay",              # 非 https
])
def test_host_gate_refuses_card(bad_url):
    """白名单第一层：不合规链接**不出码不出链接**（防钓鱼），且不打网关。"""
    agent, payment = _agent()
    card = asyncio.run(agent._register_merchant_payment(
        _binding(agent), _CTX, _INTENT, {"payH5Url": bad_url}))
    assert card is None
    assert payment.calls == []


def test_empty_host_allowlist_refuses_card():
    agent, payment = _agent()
    card = asyncio.run(agent._register_merchant_payment(
        _binding(agent, pay_url_hosts=[]), _CTX, _INTENT,
        {"payH5Url": "https://m.mcd.cn/pay"}))
    assert card is None and payment.calls == []


def test_no_locator_hit_returns_none():
    agent, payment = _agent()
    card = asyncio.run(agent._register_merchant_payment(
        _binding(agent), _CTX, _INTENT, {"order_id": "x"}))   # 响应里没有链接
    assert card is None and payment.calls == []


@pytest.mark.parametrize("mode", ["none", "boom"])
def test_gateway_failure_blocks_raw_payment_card(mode):
    """登记失败必须 fail closed：不能绕过网关把原始商户链接交给 HMI。"""
    agent, _ = _agent(mode)
    order = {"order_id": "mcd-002",
             "payH5Url": "https://m.mcd.cn/mcp/scanToPay?orderId=mcd-002"}
    card = asyncio.run(agent._register_merchant_payment(
        _binding(agent), _CTX, _INTENT, order))
    assert card is None


class _Ledger:
    def __init__(self):
        self.closed = []

    async def open(self, user_id, session_id, agent_id, kind, goal, **kw):
        from agents._sdk.ledger import LedgerTask
        return LedgerTask(task_id="t1", user_id=user_id, session_id=session_id,
                          agent_id=agent_id, kind=kind, goal=goal,
                          idempotency_key="k", status="accepted")

    async def close(self, task_id, status, **kw):
        self.closed.append((task_id, status, kw))
        return True


class _PayUrlClient:
    healthy = True
    alive = True

    def __init__(self, pay_url):
        self.pay_url = pay_url
        self.extra_urls = [
            "https://other.example/pay?token=other-secret",
            r"https:\/\/json.example\/pay?token=json-secret",
            "https:&#x2F;&#x2F;html.example/pay?token=html-secret",
            "https%3A%2F%2Fpercent.example%2Fpay%3Ftoken%3Dpercent-secret",
        ]
        self.retry_flags = []

    async def call_tool(self, name, args, timeout_s=None,
                        retry_on_session_loss=True):
        self.retry_flags.append(retry_on_session_loss)
        return {"ok": True,
                "text": "订单已创建，请访问 " + " 或 ".join(
                    [self.pay_url, *self.extra_urls]),
                "data": {"order_id": "mcd-003", "payH5Url": self.pay_url,
                         "backup_urls": list(self.extra_urls)}}


def test_gateway_failure_redacts_raw_url_from_user_facing_result():
    pay_url = "https://m.mcd.cn/private/pay?token=raw-secret"
    agent, _ = _agent("none")
    agent.ledger = _Ledger()
    binding = _binding(agent)
    binding.client = _PayUrlClient(pay_url)
    binding.input_schema = {"type": "object", "properties": {}, "required": []}
    intent = SimpleNamespace(name="mcd.order", slots={}, raw_text="下单")
    result = asyncio.run(agent._call_write(binding, intent, _CTX,
                                           {"confirmed": "true"}))
    user_facing = json.dumps({"speech": result.speech,
                              "ui_card": result.ui_card,
                              "data": result.data}, ensure_ascii=False)
    assert pay_url not in user_facing and "raw-secret" not in user_facing
    assert all(url not in user_facing for url in binding.client.extra_urls)
    assert not any(marker in user_facing for marker in (
        "other-secret", "json-secret", "html-secret", "percent-secret"))
    assert "商家应用" in result.speech
    assert binding.client.retry_flags == [False]


def test_demo_server_card_keeps_triple_honesty():
    agent, _ = _agent()
    order = {"order_id": "d1", "payH5Url": "https://m.mcd.cn/pay?o=d1"}
    card = asyncio.run(agent._register_merchant_payment(
        _binding(agent, demo=True), _CTX, _INTENT, order))
    assert card["demo"] is True and card["demo_label"] == "演示商户"
    assert card["_prov"]["mode"] == "mock"


def test_amount_zero_when_merchant_omits_it():
    """商户不回金额 → 登记 0（金额未知的诚实表达），卡片不显金额、不造数。"""
    agent, payment = _agent()
    order = {"order_id": "m3", "payH5Url": "https://m.mcd.cn/pay?o=m3"}
    card = asyncio.run(agent._register_merchant_payment(
        _binding(agent), _CTX, _INTENT, order))
    assert card["amount"] == ""
    assert payment.calls[0]["amount_cents"] == 0
