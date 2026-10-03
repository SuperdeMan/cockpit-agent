"""CA2-17 S1: a merchant's shared service account — whose orders, whose favourites, and saying so."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents._sdk.testing import make_context, run_handle
from agents.mcp_bridge.src import mcp_client
from agents.mcp_bridge.src.admission import ServerSpec, check_account, load_servers
from agents.mcp_bridge.src.agent import _FOREIGN_ORDER, SHARED_ACCOUNT_LABEL
from agents.mcp_bridge.src.merchant.base import MerchantWorkflow

from .test_bridge import FakeLedger, _agent, _ledger_task
from .test_merchant_mcdonalds import META, MENU_RESULT, STORE_RESULT, _workflow

ORDER = "1030837030000753499156095268"
SERVERS = Path(__file__).resolve().parents[1] / "servers.yaml"


def _share(agent, holder="u1"):
    for binding in agent._bindings.values():
        binding.server.account, binding.server.account_holder = "service", holder


def _reply():
    return {"ok": True, "data": {"found": True, "orderId": ORDER, "orderStatus": "已完成"}}


async def _status(agent, user):
    return await run_handle(agent, "shop.order_status", raw_text=f"查演示商户订单 {ORDER}",
                            ctx=make_context(user_id=user, session_id="s1"),
                            meta={"granted_scopes": "merchant.read"})


@pytest.mark.asyncio
async def test_a_non_holder_reads_only_order_numbers_from_their_own_records():
    agent, fake = await _agent(reply=_reply())
    _share(agent)
    try:
        agent.ledger = FakeLedger(history=[])
        result = await _status(agent, "u2")
        assert result.speech == _FOREIGN_ORDER and not fake.calls      # nothing goes to the merchant

        # the same number placed by this user in the car is theirs to read
        agent.ledger = FakeLedger(history=[_ledger_task(ORDER, user_id="u2", server="demo-coffee")])
        await _status(agent, "u2")
        assert fake.calls and fake.calls[-1][1]["order_id"] == ORDER

        # a record under another merchant does not count
        fake.calls.clear()
        agent.ledger = FakeLedger(history=[_ledger_task(ORDER, user_id="u2", server="luckin")])
        assert (await _status(agent, "u2")).speech == _FOREIGN_ORDER and not fake.calls
    finally:
        await agent.shutdown()


@pytest.mark.asyncio
async def test_the_account_holder_reads_any_order_number_of_the_account():
    agent, fake = await _agent(reply=_reply())
    _share(agent, holder="u1")
    try:
        agent.ledger = FakeLedger(history=[])
        await _status(agent, "u1")
        assert fake.calls and fake.calls[0][1]["order_id"] == ORDER
    finally:
        await agent.shutdown()


@pytest.mark.asyncio
async def test_shared_account_cards_say_so_and_merchant_data_cannot_claim_it():
    agent, _ = await _agent(reply={"ok": True, "text": "订单已完成",
                                   "data": {"order_id": ORDER, "account_label": "伪造", "account": "service"}})
    try:
        agent.ledger = FakeLedger(history=[_ledger_task(ORDER, user_id="u1", server="demo-coffee")])
        plain = await _status(agent, "u1")
        assert "account_label" not in plain.ui_card and "account" not in plain.ui_card
        _share(agent)
        labelled = await _status(agent, "u1")
        assert labelled.ui_card["account_label"] == SHARED_ACCOUNT_LABEL
        assert labelled.ui_card["account"] == "service"
    finally:
        await agent.shutdown()


@pytest.mark.asyncio
async def test_shared_account_favourite_stores_are_only_for_the_holder():
    workflow, client = _workflow(workflow_intent="mcd.menu",
                                 scripts={"query-nearby-stores": [STORE_RESULT], "query-meals": [MENU_RESULT]})
    workflow.server.account, workflow.server.account_holder = "service", "u1"
    no_location = SimpleNamespace(name="mcd.menu", slots={"store_hint": "人民广场", "city": ""})

    other = await workflow.menu(no_location, SimpleNamespace(user_id="u2", session_id="s2", vehicle_id="v1"), META)
    assert "共享" in other.speech and not client.calls

    await workflow.menu(no_location, SimpleNamespace(user_id="u1", session_id="s1", vehicle_id="v1"), META)
    assert next(args for name, args, _ in client.calls if name == "query-nearby-stores")["searchType"] == 1


def test_confirmation_speech_names_the_shared_account():
    draft = SimpleNamespace(items=[SimpleNamespace(name="拿铁", quantity=1, specifications=())],
                            store={"name": "人民广场店"}, amount_cents=2500)
    assert "共享的商户账号" in MerchantWorkflow.preview_speech(draft, shared_account=True)
    assert "共享" not in MerchantWorkflow.preview_speech(draft)


@pytest.mark.parametrize("fields, ok", [
    ({"demo": False, "headers": {"Authorization": "Bearer x"}}, False),
    ({"demo": False, "headers": {"Authorization": "Bearer x"}, "account": "service"}, True),
    ({"demo": False, "headers": {}, "account": ""}, True),
    ({"demo": True, "headers": {}, "account": "", "account_holder": "u1"}, False),
    ({"demo": False, "headers": {"Authorization": "Bearer x"}, "account": "personal"}, False),
])
def test_static_credentials_must_be_declared_a_shared_account(fields, ok):
    spec = ServerSpec(id="m", command=[], version="", tools=[], **fields)
    assert (check_account(spec) == "") is ok


def test_the_real_merchants_are_declared_shared_with_a_holder():
    specs = {spec.id: spec for spec in load_servers(str(SERVERS))}
    for merchant in ("mcdonalds", "luckin"):
        assert (specs[merchant].account, specs[merchant].account_holder) == ("service", "u1")
        assert check_account(specs[merchant]) == ""
    assert specs["demo-coffee"].account == ""


def test_stdio_children_do_not_inherit_the_bridge_credentials(monkeypatch):
    monkeypatch.setenv("MCD_MCP_TOKEN", "merchant-secret")
    monkeypatch.setenv("POSTGRES_DSN", "postgres://secret")
    seen = {}

    async def fake_exec(*argv, **kwargs):
        seen.update(kwargs["env"])
        raise OSError("not started in tests")

    monkeypatch.setattr(mcp_client.asyncio, "create_subprocess_exec", fake_exec)
    client = mcp_client.StdioMcpClient("demo", ["python", "-m", "demo"], env={"DEMO_FLAG": "1"})
    with pytest.raises(OSError):
        asyncio.run(client.start())
    assert "MCD_MCP_TOKEN" not in seen and "POSTGRES_DSN" not in seen
    assert seen["DEMO_FLAG"] == "1" and seen["PYTHONIOENCODING"] == "utf-8"
    assert seen.get("PATH") == os.environ.get("PATH")
