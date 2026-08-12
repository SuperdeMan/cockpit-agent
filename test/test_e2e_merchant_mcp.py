"""真实商户 MCP 人工车道的离线安全契约。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


# Windows + importlib mode can mojibake the Chinese workspace segment carried by
# ``__file__``.  The project baseline is always launched from repository root.
ROOT = Path.cwd()
SCRIPT = ROOT / "test" / "e2e_merchant_mcp.py"


def _load():
    spec = importlib.util.spec_from_file_location("e2e_merchant_mcp", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_live_create_is_impossible_without_all_three_explicit_gates():
    mod = _load()
    with pytest.raises(SystemExit):
        mod._parse_args(["--live-create-unpaid"])
    with pytest.raises(SystemExit):
        mod._parse_args([
            "--live-create-unpaid", "--acknowledge-real-orders",
            "--max-real-orders", "4",
        ])
    args = mod._parse_args([
        "--live-create-unpaid", "--acknowledge-real-orders",
        "--max-real-orders", "1",
    ])
    assert args.live_create_unpaid is True
    assert args.max_real_orders == 1


def test_readonly_mode_never_inherits_a_write_budget():
    mod = _load()
    args = mod._parse_args(["--live-readonly"])
    assert args.live_readonly is True
    assert args.max_real_orders == 0


def test_compose_service_urls_stay_internal_inside_container(monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "_inside_container", lambda: True)
    redis_url = "redis://redis:6379/0"
    postgres_url = "postgresql://user:secret@postgres:5432/car_agent"
    assert mod._host_service_url(redis_url) == redis_url
    assert mod._host_service_url(postgres_url) == postgres_url


def test_order_budget_is_monotonic_and_refuses_the_next_write():
    mod = _load()
    budget = mod.OrderBudget(limit=1)
    budget.reserve("luckin.createOrder")
    assert budget.used == 1
    with pytest.raises(RuntimeError, match="real-order budget exhausted"):
        budget.reserve("mcd.create-order")


def test_live_scenario_requires_one_explicit_merchant_and_no_arbitrary_tool():
    mod = _load()
    with pytest.raises(RuntimeError, match="one merchant"):
        mod._live_scenario_config(SimpleNamespace(merchant="all"))
    luckin = mod._live_scenario_config(SimpleNamespace(merchant="luckin"))
    assert luckin["intent"] == "luckin.order"
    assert luckin["write_tool"] == "createOrder"
    assert "arguments" not in luckin


def test_write_evidence_keeps_only_locator_shape_and_safe_host():
    mod = _load()
    evidence = mod._write_evidence({
        "ok": True,
        "data": {"code": 0, "data": {
            "orderId": "ORDER-SECRET",
            "payUrl": "https://pay.example.test/path?token=SECRET",
            "amount": 16.6,
        }},
    })
    rendered = repr(evidence)
    assert "ORDER-SECRET" not in rendered
    assert "SECRET" not in rendered
    assert evidence["data.data.orderId"] == {"type": "str", "present": True}
    assert evidence["data.data.payUrl"] == {
        "type": "url", "scheme": "https", "host": "pay.example.test"}


def test_early_result_diagnostic_exposes_no_speech_or_payload_values():
    mod = _load()
    result = SimpleNamespace(
        status="need_slot", missing_slots=["store_name"],
        speech="secret address and order id", ui_card={"type": "choices"},
        data={"opaque_token": "secret-value"})
    diagnostic = mod._result_diagnostic(result)
    assert diagnostic == {
        "status": "need_slot", "missing_slots": ["store_name"],
        "card": "choices", "data_keys": ["opaque_token"]}
    assert "secret" not in repr(diagnostic)


def test_evidence_projection_never_emits_raw_uri_or_sensitive_values():
    mod = _load()
    projected = mod.project_evidence({
        "code": 0,
        "data": {
            "orderId": "ORDER-SECRET",
            "payUrl": "https://pay.example.test/path?token=SECRET",
            "couponCodeList": ["COUPON-SECRET"],
            "nested": {"amount": 16.6, "success": True},
        },
    })
    rendered = repr(projected)
    assert "ORDER-SECRET" not in rendered
    assert "SECRET" not in rendered
    assert "COUPON" not in rendered
    assert "https://" not in rendered
    assert projected["data.payUrl"] == {
        "type": "url", "scheme": "https", "host": "pay.example.test"
    }
    assert projected["data.orderId"] == {"type": "str", "present": True}
    assert projected["data.nested.amount"] == {"type": "float"}


def test_script_is_manual_only_and_not_import_time_network_code():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__"' in text
    assert "--acknowledge-real-orders" in text
    assert "Bearer " not in text
    assert "print(token" not in text
