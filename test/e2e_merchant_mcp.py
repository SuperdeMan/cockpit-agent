"""真实麦当劳/瑞幸 MCP 的人工 opt-in 验收入口。

默认没有任何动作。只读模式仅握手并复核 tools/list；真实创建模式还需要显式确认、
非零且不超过 3 的订单预算，并由受控场景函数在每次写调用前 reserve。输出只包含
字段路径/类型、业务 code 和支付 URL 的 scheme/host，绝不打印 token、订单号、完整
URL、优惠券、地址或原始商户响应。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TEST_ROOT = ROOT / "test"
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from support.e2e import assert_persistent_source_contract


def _source_contract() -> None:
    assert_persistent_source_contract(Path(__file__).read_text(encoding="utf-8"))


if "--source-contract" in sys.argv:
    _source_contract()
    print("source contract: PASS")
    raise SystemExit(0)

_SENSITIVE_PATH_WORDS = (
    "authorization", "token", "coupon", "address", "phone", "mobile",
    "telephone", "traceid", "picture", "image",
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--live-readonly", action="store_true")
    modes.add_argument("--live-create-unpaid", action="store_true")
    parser.add_argument("--acknowledge-real-orders", action="store_true")
    parser.add_argument("--max-real-orders", type=int, default=0)
    parser.add_argument(
        "--merchant", choices=("all", "mcdonalds", "luckin"), default="all")
    args = parser.parse_args(argv)
    if args.live_readonly:
        if args.max_real_orders != 0 or args.acknowledge_real_orders:
            parser.error("readonly mode must have a zero write budget")
        return args
    if not args.acknowledge_real_orders:
        parser.error("live create requires --acknowledge-real-orders")
    if not 1 <= args.max_real_orders <= 3:
        parser.error("live create requires --max-real-orders in [1, 3]")
    return args


class OrderBudget:
    def __init__(self, limit: int):
        self.limit = int(limit)
        self.used = 0

    def reserve(self, operation: str) -> None:
        if self.used >= self.limit:
            raise RuntimeError("real-order budget exhausted")
        if not operation:
            raise RuntimeError("write operation name is required")
        self.used += 1


def _is_sensitive_path(path: str) -> bool:
    lowered = path.lower()
    return any(word in lowered for word in _SENSITIVE_PATH_WORDS)


def project_evidence(value, prefix: str = "") -> dict[str, dict]:
    """Project an upstream response to safe structural evidence only."""
    out: dict[str, dict] = {}

    def visit(candidate, path: str) -> None:
        if path and _is_sensitive_path(path):
            return
        if isinstance(candidate, dict):
            for key, child in candidate.items():
                child_path = f"{path}.{key}" if path else str(key)
                visit(child, child_path)
            return
        if isinstance(candidate, list):
            out[path] = {"type": "list", "length": len(candidate)}
            if candidate:
                visit(candidate[0], f"{path}[]")
            return
        if isinstance(candidate, str):
            try:
                parsed = urlparse(candidate)
            except ValueError:
                parsed = None
            if parsed and parsed.scheme and parsed.hostname:
                out[path] = {
                    "type": "url", "scheme": parsed.scheme.lower(),
                    "host": (parsed.hostname or "").lower(),
                }
            else:
                out[path] = {"type": "str", "present": bool(candidate)}
            return
        type_name = "null" if candidate is None else type(candidate).__name__
        out[path] = {"type": type_name}

    visit(value, prefix)
    return out


def _write_evidence(response: dict) -> dict[str, dict]:
    """Return only response locators/types and audited URL scheme/host."""
    return project_evidence(response)


def _result_diagnostic(result) -> dict:
    """Expose only control-plane facts when a live journey stops early."""
    data = getattr(result, "data", None)
    card = getattr(result, "ui_card", None)
    return {
        "status": str(getattr(result, "status", "") or ""),
        "missing_slots": sorted(str(item) for item in (
            getattr(result, "missing_slots", None) or ())),
        "card": str((card or {}).get("type") or "")
        if isinstance(card, dict) else "",
        "data_keys": sorted(str(key) for key in data)
        if isinstance(data, dict) else [],
    }


def _live_scenario_config(args) -> dict:
    if getattr(args, "merchant", "all") == "all":
        raise RuntimeError("live create requires exactly one merchant")
    if args.merchant == "luckin":
        return {
            "intent": "luckin.order", "write_tool": "createOrder",
            "required_env": (
                "MERCHANT_E2E_STORE_NAME", "MERCHANT_E2E_STORE_LONGITUDE",
                "MERCHANT_E2E_STORE_LATITUDE", "MERCHANT_E2E_ITEM_QUERY",
            ),
        }
    return {
        "intent": "mcd.order", "write_tool": "create-order",
        "required_env": (
            "MERCHANT_E2E_STORE_NAME", "MERCHANT_E2E_CITY",
            "MERCHANT_E2E_ITEM_QUERY",
        ),
    }


def _scenario_slots(merchant: str) -> dict[str, str]:
    values = {
        key: os.getenv(key, "").strip()
        for key in (
            "MERCHANT_E2E_STORE_NAME", "MERCHANT_E2E_STORE_LONGITUDE",
            "MERCHANT_E2E_STORE_LATITUDE", "MERCHANT_E2E_CITY",
            "MERCHANT_E2E_ITEM_QUERY", "MERCHANT_E2E_QUANTITY",
            "MERCHANT_E2E_TEMPERATURE", "MERCHANT_E2E_SWEETNESS",
            "MERCHANT_E2E_ICE", "MERCHANT_E2E_MILK",
            "MERCHANT_E2E_PICKUP_MODE",
        )
    }
    required = (
        ("MERCHANT_E2E_STORE_NAME", "MERCHANT_E2E_STORE_LONGITUDE",
         "MERCHANT_E2E_STORE_LATITUDE", "MERCHANT_E2E_ITEM_QUERY")
        if merchant == "luckin" else
        ("MERCHANT_E2E_STORE_NAME", "MERCHANT_E2E_CITY",
         "MERCHANT_E2E_ITEM_QUERY")
    )
    missing = [key for key in required if not values[key]]
    if missing:
        raise RuntimeError("live scenario env missing: " + ",".join(missing))
    if merchant == "luckin":
        return {
            "item_query": values["MERCHANT_E2E_ITEM_QUERY"],
            "quantity": values["MERCHANT_E2E_QUANTITY"] or "1",
            "store_name": values["MERCHANT_E2E_STORE_NAME"],
            "store_longitude": values["MERCHANT_E2E_STORE_LONGITUDE"],
            "store_latitude": values["MERCHANT_E2E_STORE_LATITUDE"],
            "temperature": values["MERCHANT_E2E_TEMPERATURE"],
            "sweetness": values["MERCHANT_E2E_SWEETNESS"],
            "ice": values["MERCHANT_E2E_ICE"],
            "milk": values["MERCHANT_E2E_MILK"],
        }
    return {
        "item_query": values["MERCHANT_E2E_ITEM_QUERY"],
        "quantity": values["MERCHANT_E2E_QUANTITY"] or "1",
        "store_hint": values["MERCHANT_E2E_STORE_NAME"],
        "city": values["MERCHANT_E2E_CITY"],
        "pickup_mode": values["MERCHANT_E2E_PICKUP_MODE"] or "到店自取",
    }


def _inside_container() -> bool:
    return Path("/.dockerenv").exists()


def _host_service_url(value: str) -> str:
    """Map only the root-Compose service hostnames to published localhost."""
    if not value or _inside_container():
        return value
    parsed = urlsplit(value)
    if parsed.hostname not in {"redis", "postgres"}:
        return value
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += ":" + parsed.password
        auth += "@"
    port = parsed.port or (6379 if parsed.hostname == "redis" else 5432)
    return urlunsplit((parsed.scheme, f"{auth}localhost:{port}",
                       parsed.path, parsed.query, parsed.fragment))


class _CaptureClient:
    def __init__(self, inner, tool_name: str):
        self._inner = inner
        self._tool_name = tool_name
        self.response = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def call_tool(self, name, arguments, **kwargs):
        response = await self._inner.call_tool(name, arguments, **kwargs)
        if name == self._tool_name:
            self.response = response
        return response


async def _run_live_create(args: argparse.Namespace) -> dict:
    from agents._sdk import NEED_CONFIRM
    from agents.mcp_bridge.src.agent import McpBridgeAgent

    config = _live_scenario_config(args)
    slots = _scenario_slots(args.merchant)
    os.environ["REDIS_URL"] = _host_service_url(os.getenv("REDIS_URL", ""))
    os.environ["POSTGRES_DSN"] = _host_service_url(
        os.getenv("POSTGRES_DSN", ""))
    agent = McpBridgeAgent()
    await agent.bootstrap()
    suffix = os.urandom(8).hex()
    session = "merchant-live-" + suffix
    owner = "merchant-live-owner-" + suffix
    vehicle = "merchant-live-vehicle-" + suffix
    ctx = SimpleNamespace(
        user_id=owner, session_id=session,
        vehicle_id=vehicle, trace_id=session)
    meta = {"granted_scopes": "merchant.read,merchant.write"}
    if args.merchant == "luckin":
        meta["_trusted_slot_refs"] = json.dumps({
            "store_name": {
                "ref": "nearby.data.items.0.name",
                "producer_intent": "nearby.search"},
            "store_longitude": {
                "ref": "nearby.data.items.0.lng",
                "producer_intent": "nearby.search"},
            "store_latitude": {
                "ref": "nearby.data.items.0.lat",
                "producer_intent": "nearby.search"},
        }, ensure_ascii=False)
    budget = OrderBudget(args.max_real_orders)
    try:
        workflow = agent._workflow_bindings[config["intent"]].workflow
        binding = workflow.tools[config["write_tool"]]
        capture = _CaptureClient(binding.client, config["write_tool"])
        binding.client = capture
        intent = SimpleNamespace(name=config["intent"], slots=slots)
        preview = await workflow.prepare(intent, ctx, meta)
        if preview.status != NEED_CONFIRM or not (preview.data or {}).get(
                "checkout_token"):
            raise RuntimeError(
                "live preview did not reach NEED_CONFIRM: " +
                json.dumps(_result_diagnostic(preview), sort_keys=True))
        if capture.response is not None:
            raise RuntimeError("write happened before confirmation")
        budget.reserve(config["write_tool"])
        confirmed = await workflow.confirm(
            intent, ctx, {**meta, "confirmed": "true"},
            token=preview.data["checkout_token"])
        order_id = str((confirmed.data or {}).get("order_id") or "")
        if not order_id or capture.response is None:
            raise RuntimeError("live create lacked a verified order result")
        evidence = {
            "merchant": args.merchant,
            "preview": {
                "status": preview.status,
                "card": (preview.ui_card or {}).get("type"),
                "amount_cents_present": isinstance(
                    (preview.ui_card or {}).get("amount_cents"), int),
            },
            "create": {
                "verified": True,
                "card": (confirmed.ui_card or {}).get("type"),
                "response_shape": _write_evidence(capture.response),
            },
        }
        if args.merchant == "luckin":
            cancel_flow = agent._workflow_bindings[
                "luckin.order_cancel"].workflow
            cancel_intent = SimpleNamespace(
                name="luckin.order_cancel", slots={"order_id": order_id})
            pending = await cancel_flow.cancel(cancel_intent, ctx, meta)
            if pending.status != NEED_CONFIRM:
                raise RuntimeError("live query did not reach cancellation confirm")
            cancelled = await cancel_flow.cancel(
                cancel_intent, ctx, {**meta, "confirmed": "true"})
            if str((cancelled.data or {}).get("status")) != "cancelled":
                raise RuntimeError("live cancellation terminal status not verified")
            evidence["query"] = {"verified": True}
            evidence["cancel"] = {"verified": True}
        else:
            status_binding = agent._bindings["mcd.order_status"]
            status_response = await status_binding.client.call_tool(
                status_binding.tool.name, {"orderId": order_id},
                timeout_s=status_binding.tool.timeout_ms / 1000.0,
                retry_on_session_loss=True)
            if not isinstance(status_response, dict) or not status_response.get("ok"):
                raise RuntimeError("live order query was not verified")
            evidence["query"] = {"verified": True}
            evidence["cancel"] = {"supported": False,
                                  "cleanup": "merchant_unpaid_expiry"}
        evidence["budget"] = {"limit": budget.limit, "used": budget.used}
        return evidence
    finally:
        await agent.shutdown()


def _load_root_env() -> None:
    """Load the supported root .env without overriding the caller or logging it."""
    path = ROOT / ".env"
    if not path.is_file() or path.stat().st_size > 512 * 1024:
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


async def _inventory(server_id: str, url: str, token_env: str) -> dict:
    from agents.mcp_bridge.src.admission import schema_fingerprint
    from agents.mcp_bridge.src.mcp_client import HttpMcpClient

    token = os.getenv(token_env, "").strip()
    if not token:
        raise RuntimeError(f"{token_env} is not configured")
    headers = {"Authorization": "{} {}".format("Bearer", token)}
    client = HttpMcpClient(server_id, url, headers=headers, timeout_s=20)
    await client.start()
    try:
        await client.initialize()
        tools = await client.list_tools()
        return {
            "server": server_id,
            "server_name_present": bool(client.server_info.get("name")),
            "server_version_present": bool(client.server_info.get("version")),
            "tools": [
                {
                    "name": str(tool.get("name") or ""),
                    "schema_sha": schema_fingerprint(tool.get("inputSchema") or {}),
                }
                for tool in tools if isinstance(tool, dict)
            ],
        }
    finally:
        await client.close()


async def _run(args: argparse.Namespace) -> int:
    _load_root_env()
    selected = (
        ("mcdonalds", "https://mcp.mcd.cn", "MCD_MCP_TOKEN"),
        ("luckin", "https://gwmcp.lkcoffee.com/order/user/mcp", "LUCKIN_MCP_TOKEN"),
    )
    if args.merchant != "all":
        selected = tuple(item for item in selected if item[0] == args.merchant)
    inventories = [await _inventory(*item) for item in selected]
    print(json.dumps(inventories, ensure_ascii=False, sort_keys=True))
    if args.live_create_unpaid:
        evidence = await _run_live_create(args)
        print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
