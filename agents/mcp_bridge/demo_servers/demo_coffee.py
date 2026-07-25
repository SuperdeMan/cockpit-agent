"""**演示商户** MCP server（stdio）——真实商户的替身。

母提案 §4.F 原文即「一个只读 + 一个可确认写入（示例咖啡下单 mock→真实）」。
它存在的唯一目的是让**写操作生命周期五项**（幂等键/订单状态机/timeout·cancel/
补偿退款/审计）能在真栈上被验证——真商户 BD 是非技术依赖。

**诚实标注是硬要求**（铁律③ 的同一条精神）：`demo: true` 的 server 产出的卡片一律打
`_prov = demo`、HMI 带「演示商户」角标、下单话术明说。**演示不是问题，把演示装成真实才是。**

协议：JSON-RPC 2.0 over stdin/stdout，换行分帧。实现 initialize / tools/list / tools/call。
运行：`python -m agents.mcp_bridge.demo_servers.demo_coffee`
"""
from __future__ import annotations

import json
import sys
import time
import uuid

SERVER_NAME = "demo-coffee"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-06-18"

MENU = [
    {"sku": "latte", "name": "拿铁", "price_cents": 2200, "sizes": ["中杯", "大杯"]},
    {"sku": "americano", "name": "美式", "price_cents": 1800, "sizes": ["中杯", "大杯"]},
    {"sku": "mocha", "name": "摩卡", "price_cents": 2600, "sizes": ["中杯", "大杯"]},
]
SIZE_SURCHARGE = {"中杯": 0, "大杯": 300}

# 订单状态机：submitted → done（取货）/ refunded（补偿）
_ORDERS: dict = {}
_BY_IDEM: dict = {}


def _menu_list(_args: dict) -> dict:
    lines = [f"{m['name']}（{m['price_cents'] / 100:.0f} 元起）" for m in MENU]
    return {"content": [{"type": "text", "text": "、".join(lines)}],
            "structuredContent": {"items": MENU, "demo": True}}


def _order_create(args: dict) -> dict:
    sku = str(args.get("sku") or "").strip()
    size = str(args.get("size") or "中杯").strip()
    idem = str(args.get("idempotency_key") or "").strip()
    item = next((m for m in MENU if m["sku"] == sku or m["name"] == sku), None)
    if not item:
        return {"isError": True,
                "content": [{"type": "text", "text": f"菜单里没有「{sku}」"}]}
    if not idem:
        return {"isError": True,
                "content": [{"type": "text", "text": "缺少 idempotency_key"}]}
    if idem in _BY_IDEM:                       # 幂等：同键复用同一单，绝不双扣
        order = _ORDERS[_BY_IDEM[idem]]
        return {"content": [{"type": "text",
                             "text": f"订单已存在：{order['order_id']}"}],
                "structuredContent": {**order, "duplicate": True}}
    amount = item["price_cents"] + SIZE_SURCHARGE.get(size, 0)
    order = {"order_id": "DC" + uuid.uuid4().hex[:10].upper(), "sku": item["sku"],
             "name": item["name"], "size": size, "amount_cents": amount,
             "status": "submitted", "created_at": int(time.time()), "demo": True}
    _ORDERS[order["order_id"]] = order
    _BY_IDEM[idem] = order["order_id"]
    return {"content": [{"type": "text",
                         "text": f"已下单：{item['name']}{size} "
                                 f"{amount / 100:.0f} 元，订单号 {order['order_id']}"}],
            "structuredContent": order}


def _order_cancel(args: dict) -> dict:
    """补偿路径：已提交的单可取消退款。没有它的写工具不允许准入（admission 强制）。"""
    oid = str(args.get("order_id") or "").strip()
    order = _ORDERS.get(oid)
    if not order:
        return {"isError": True,
                "content": [{"type": "text", "text": f"找不到订单 {oid}"}]}
    if order["status"] == "refunded":
        return {"content": [{"type": "text", "text": f"订单 {oid} 已退款"}],
                "structuredContent": {**order, "duplicate": True}}
    order["status"] = "refunded"
    order["refund_id"] = "RF" + uuid.uuid4().hex[:8].upper()
    return {"content": [{"type": "text",
                         "text": f"已取消并退款：{oid}（{order['amount_cents'] / 100:.0f} 元）"}],
            "structuredContent": order}


TOOLS = [
    {"name": "menu.list", "description": "查看演示咖啡店的菜单与价格",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "order.create", "description": "下一杯咖啡（演示商户，不产生真实交易）",
     "inputSchema": {"type": "object", "properties": {
         "sku": {"type": "string", "description": "商品名或 sku，如 拿铁/latte"},
         "size": {"type": "string", "description": "中杯 | 大杯"},
         "idempotency_key": {"type": "string", "description": "幂等键，重试必须复用"},
     }, "required": ["sku", "idempotency_key"]}},
    {"name": "order.cancel", "description": "取消订单并退款（补偿路径）",
     "inputSchema": {"type": "object", "properties": {
         "order_id": {"type": "string"}}, "required": ["order_id"]}},
]
_HANDLERS = {"menu.list": _menu_list, "order.create": _order_create,
             "order.cancel": _order_cancel}


def _handle(msg: dict):
    method, rid = msg.get("method"), msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        fn = _HANDLERS.get(params.get("name"))
        if not fn:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"unknown tool {params.get('name')}"}}
        try:
            return {"jsonrpc": "2.0", "id": rid, "result": fn(params.get("arguments") or {})}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32603, "message": str(e)}}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"unknown method {method}"}}


def main() -> None:
    # stdio 强制 UTF-8：Windows 上子进程默认走 cp936，中文菜单会以 GBK 写出去，
    # 客户端按 UTF-8 解就炸（本机开发首跑即中招）。容器里也一并钉死，免得靠环境撞运气。
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print(f"[{SERVER_NAME}] demo MCP server up (v{SERVER_VERSION})", file=sys.stderr,
          flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = _handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
