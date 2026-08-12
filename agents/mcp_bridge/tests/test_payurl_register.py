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
    return SimpleNamespace(server=server, tool=tool, client=None,
                           input_schema={"type": "object", "properties": {},
                                         "required": []})


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


def test_gateway_explicit_rejection_blocks_raw_payment_card():
    """网关明确拒绝登记仍 fail closed，不把原始商户链接交给 HMI。"""
    agent, _ = _agent("none")
    order = {"order_id": "mcd-002",
             "payH5Url": "https://m.mcd.cn/mcp/scanToPay?orderId=mcd-002"}
    card = asyncio.run(agent._register_merchant_payment(
        _binding(agent), _CTX, _INTENT, order))
    assert card is None


def test_gateway_exception_propagates_to_write_post_success_fallback_boundary():
    agent, _ = _agent("boom")
    order = {"order_id": "mcd-002",
             "payH5Url": "https://m.mcd.cn/mcp/scanToPay?orderId=mcd-002"}
    with pytest.raises(RuntimeError, match="gateway down"):
        asyncio.run(agent._register_merchant_payment(
            _binding(agent), _CTX, _INTENT, order))


class _Ledger:
    def __init__(self, duplicate=None):
        self.closed = []
        self.duplicate = duplicate

    async def open(self, user_id, session_id, agent_id, kind, goal, **kw):
        if self.duplicate is not None:
            from agents._sdk.ledger import Duplicate
            return Duplicate(self.duplicate)
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


def _flatten_result(result) -> str:
    return json.dumps({"speech": result.speech,
                       "ui_card": result.ui_card,
                       "data": result.data}, ensure_ascii=False)


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
    user_facing = _flatten_result(result)
    assert pay_url not in user_facing and "raw-secret" not in user_facing
    assert all(url not in user_facing for url in binding.client.extra_urls)
    assert not any(marker in user_facing for marker in (
        "other-secret", "json-secret", "html-secret", "percent-secret"))
    assert "商家应用" in result.speech
    assert binding.client.retry_flags == [False]


@pytest.mark.parametrize(("locator", "payload"), [
    ("missing.path", {
        "renamedPayLink": "//m.mcd.cn/pay?token=scheme-relative-secret",
        "javascript": "javascript:alert('js-secret')",
    }),
    ("payH5Url", {
        "payH5Url": "ftp://m.mcd.cn/pay?token=ftp-secret",
        "contact": "mailto:pay-secret@example.cn",
        "phone": "tel:+8613800138000",
    }),
    ("payH5Url", {
        "payH5Url": "data:text/html;base64,PHNjcmlwdD5zZWNyZXQ8L3NjcmlwdD4=",
        "local": "file:///etc/secret",
    }),
    ("missing.path", {
        "wallet": "alipays://platformapi/startapp?secret=ali-secret",
        "wechat": "weixin://dl/business/?secret=wx-secret",
        "intent": "intent://pay/#Intent;S.secret=intent-secret;end",
        "custom": "custom:opaque-custom-secret",
    }),
    ("missing.path", {
        "assignment": "pay_url=https://x.invalid/?secret=embedded-http",
        "htmlish": "href=javascript:alert('embedded-js')",
        "cn": "链接：alipays://x?secret=embedded-ali",
        "paren": "请打开（weixin://x?secret=embedded-wx）",
    }),
])
def test_payment_locator_miss_or_illegal_scheme_never_returns_any_raw_uri(
        locator, payload):
    agent, _ = _agent()
    agent.ledger = _Ledger()
    binding = _binding(agent, locator=locator)
    main = payload.get("payH5Url", "https://m.mcd.cn/unused")
    binding.client = _PayUrlClient(main)
    binding.client.extra_urls = list(payload.values())
    binding.client.call_tool = lambda *args, **kwargs: None

    async def _reply(*args, **kwargs):
        binding.client.retry_flags.append(kwargs.get("retry_on_session_loss"))
        return {"ok": True,
                "text": "订单完成 " + " ".join(payload.values()),
                "data": {"order_id": "mcd-uri", **payload}}

    binding.client.call_tool = _reply
    intent = SimpleNamespace(name="mcd.order", slots={}, raw_text="下单")
    result = asyncio.run(agent._call_write(binding, intent, _CTX,
                                           {"confirmed": "true"}))
    surface = _flatten_result(result)
    ledger_surface = json.dumps(agent.ledger.closed[-1][2], ensure_ascii=False)
    for marker in ("scheme-relative-secret", "js-secret", "ftp-secret",
                   "pay-secret@example.cn", "+8613800138000", "PHNjcmlwdD5",
                   "/etc/secret", "ali-secret", "wx-secret", "intent-secret",
                   "opaque-custom-secret", "embedded-http", "embedded-js",
                   "embedded-ali", "embedded-wx"):
        assert marker not in surface
        assert marker not in ledger_surface


def test_payment_processing_exception_preserves_verified_order_success():
    primary = "https://m.mcd.cn/pay?token=approved-main"
    agent, _ = _agent("boom")
    agent.ledger = _Ledger()
    binding = _binding(agent)
    binding.client = _PayUrlClient(primary)
    intent = SimpleNamespace(name="mcd.order", slots={}, raw_text="下单")
    result = asyncio.run(agent._call_write(binding, intent, _CTX,
                                           {"confirmed": "true"}))
    assert "没有拿到确认结果" not in result.speech
    assert "支付入口暂不可用" in result.speech
    assert primary not in _flatten_result(result)
    task_id, status, kw = agent.ledger.closed[-1]
    assert task_id == "t1" and status == "done"
    assert kw["result_ref"]["order_id"] == "mcd-003"
    assert kw["result_ref"]["server"] == "mcd-test"
    assert "approved-main" not in json.dumps(kw["result_ref"], ensure_ascii=False)
    assert len(agent.ledger.closed) == 1


def test_successful_gateway_allows_only_approved_primary_url_in_payment_card():
    primary = "https://m.mcd.cn/pay?token=approved-main"
    secondaries = [
        "https://other.example/pay?token=second-http",
        "//other.example/pay?token=second-relative",
        "javascript:alert('second-js')",
        "ftp://other.example/pay?token=second-ftp",
        "https%253A%252F%252Fencoded.example%252Fpay%253Ftoken%253Dsecond-encoded",
    ]
    agent, payment = _agent()
    agent.ledger = _Ledger()
    binding = _binding(agent)
    binding.client = _PayUrlClient(primary)
    binding.client.extra_urls = secondaries
    binding.input_schema = {"type": "object", "properties": {}, "required": []}
    intent = SimpleNamespace(name="mcd.order", slots={}, raw_text="下单")
    result = asyncio.run(agent._call_write(binding, intent, _CTX,
                                           {"confirmed": "true"}))
    assert result.ui_card["type"] == "payment_qr"
    assert result.ui_card["pay_url"] == primary
    assert result.ui_card["qr_content"] == primary
    assert primary not in result.speech
    assert primary not in json.dumps(result.data, ensure_ascii=False)
    all_surfaces = _flatten_result(result)
    ledger_surface = json.dumps(agent.ledger.closed[-1][2], ensure_ascii=False)
    for marker in ("second-http", "second-relative", "second-js",
                   "second-ftp", "second-encoded"):
        assert marker not in all_surfaces
        assert marker not in ledger_surface
    assert payment.calls[0]["external_pay_url"] == primary


def test_embedded_uri_is_removed_from_text_nested_data_and_ledger():
    primary = "https://m.mcd.cn/pay?token=approved-main"
    embedded = {
        "assignment": "pay_url=https://x.invalid/?secret=nested-http",
        "htmlish": "href=javascript:alert('nested-js')",
        "cn": "链接：alipays://x?secret=nested-ali",
        "paren": "请打开（weixin://x?secret=nested-wx）",
    }
    agent, _ = _agent("none")
    agent.ledger = _Ledger()
    binding = _binding(agent)
    binding.client = _PayUrlClient(primary)

    async def _reply(*args, **kwargs):
        return {"ok": True,
                "text": " ".join(embedded.values()),
                "data": {"order_id": "nested-1", "payH5Url": primary,
                         "nested": embedded}}

    binding.client.call_tool = _reply
    intent = SimpleNamespace(name="mcd.order", slots={}, raw_text="下单")
    result = asyncio.run(agent._call_write(binding, intent, _CTX,
                                           {"confirmed": "true"}))
    surface = _flatten_result(result)
    ledger_surface = json.dumps(agent.ledger.closed[-1][2], ensure_ascii=False)
    for marker in ("nested-http", "nested-js", "nested-ali", "nested-wx"):
        assert marker not in surface
        assert marker not in ledger_surface


def test_duplicate_payment_ledger_reference_is_sanitized_before_card_or_data():
    from agents._sdk.ledger import LedgerTask

    primary = "pay_url=https://m.mcd.cn/pay?token=stale-primary"
    secondary = "链接：alipays://x?secret=stale-secondary"
    existing = LedgerTask(
        task_id="old", user_id="u1", session_id="s1", agent_id="mcp-bridge",
        kind="mcp_order", goal="{}", idempotency_key="k", status="done",
        result_ref={"order_id": "old-1", "payH5Url": primary,
                    "backup": secondary, "server": "mcd-test"})
    agent, _ = _agent()
    agent.ledger = _Ledger(duplicate=existing)
    binding = _binding(agent)
    binding.client = _PayUrlClient(primary)
    intent = SimpleNamespace(name="mcd.order", slots={}, raw_text="下单")
    result = asyncio.run(agent._call_write(binding, intent, _CTX,
                                           {"confirmed": "true"}))
    surface = _flatten_result(result)
    assert "stale-primary" not in surface
    assert "stale-secondary" not in surface


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
