"""商户工作流公共 typed model 与金额/持久化白名单。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def yuan_to_cents(value) -> int:
    """元转分；Decimal(str(value)) + HALF_UP，拒绝负数、NaN 与无穷。"""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("金额不是有效数字") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("金额必须是非负有限数")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class MerchantItem:
    name: str
    quantity: int = 1
    sku: str = ""
    product_id: str = ""
    specifications: list[str] = field(default_factory=list)
    amount_cents: int = 0

    @classmethod
    def from_dict(cls, value: dict) -> "MerchantItem":
        return cls(
            name=str(value.get("name") or ""),
            quantity=int(value.get("quantity") or 1),
            sku=str(value.get("sku") or ""),
            product_id=str(value.get("product_id") or ""),
            specifications=[str(v) for v in value.get("specifications", [])],
            amount_cents=int(value.get("amount_cents") or 0),
        )


@dataclass(frozen=True)
class MerchantChoice:
    id: str
    name: str
    subtitle: str = ""
    send_text: str = ""
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MerchantDraft:
    token: str
    merchant: str
    operation: str
    user_id: str
    session_id: str
    store: dict
    items: list[MerchantItem]
    amount_cents: int
    upstream_args: dict
    schema_digest: str
    created_at: float
    original_amount_cents: int = 0
    discount_cents: int = 0
    fulfillment: str = ""
    currency: str = "CNY"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"),
                          sort_keys=True, allow_nan=False)

    @classmethod
    def from_json(cls, raw: str) -> "MerchantDraft":
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("draft 顶层必须是 object")
        operation = str(value.get("operation") or "").strip().lower()
        if not operation:
            raise ValueError("draft operation 必须非空")
        return cls(
            token=str(value.get("token") or ""),
            merchant=str(value.get("merchant") or ""),
            operation=operation,
            user_id=str(value.get("user_id") or ""),
            session_id=str(value.get("session_id") or ""),
            store=dict(value.get("store") or {}),
            items=[MerchantItem.from_dict(item)
                   for item in (value.get("items") or []) if isinstance(item, dict)],
            amount_cents=int(value.get("amount_cents") or 0),
            upstream_args=dict(value.get("upstream_args") or {}),
            schema_digest=str(value.get("schema_digest") or ""),
            created_at=float(value.get("created_at") or 0),
            original_amount_cents=int(value.get("original_amount_cents") or 0),
            discount_cents=int(value.get("discount_cents") or 0),
            fulfillment=str(value.get("fulfillment") or ""),
            currency=str(value.get("currency") or "CNY"),
        )


@dataclass
class MerchantResult:
    server: str
    merchant: str
    order_id: str = ""
    status: str = ""
    amount_cents: int = 0
    store_name: str = ""
    pay_url: str = ""
    items: list[dict] = field(default_factory=list)
    cancelled: bool = False
    raw: dict = field(default_factory=dict)

    def ledger_ref(self) -> dict:
        """最小持久化面：不保存支付 URI、地址、电话、优惠券或原始响应。"""
        return {
            "server": str(self.server),
            "merchant": str(self.merchant),
            "order_id": str(self.order_id),
            "status": str(self.status),
            "amount_cents": int(self.amount_cents),
            "store_name": str(self.store_name),
        }
