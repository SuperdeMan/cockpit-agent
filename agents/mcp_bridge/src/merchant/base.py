"""商户复合工作流公共接口与确定性卡片/摘要。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from agents._sdk import AgentResult

from .models import MerchantChoice, MerchantDraft


class DeclaredBusinessRejected(RuntimeError):
    """The provider returned a complete envelope that fails its pinned predicate."""


class DeclaredBusinessIncomplete(RuntimeError):
    """A write response cannot prove either success or explicit rejection."""


_MISSING = object()


class MerchantWorkflow(ABC):
    merchant = ""
    intents: tuple[str, ...] = ()

    @abstractmethod
    async def prepare(self, intent, ctx, meta) -> AgentResult:
        raise NotImplementedError

    @abstractmethod
    async def confirm(self, intent, ctx, meta, token: str = "") -> AgentResult:
        raise NotImplementedError

    async def cancel(self, intent, ctx, meta) -> AgentResult:
        return AgentResult(speech="这个商户暂不支持取消。")

    async def menu(self, intent, ctx, meta) -> AgentResult:
        """只读看菜单。默认不支持——诚实说，不猜、不回落到演示商户。"""
        return AgentResult(speech="这个商户暂不支持查看菜单。")

    @classmethod
    def choice_card(cls, kind: str, choices: list[MerchantChoice]) -> dict:
        selected = list(choices or [])[:3]
        return {
            "type": "merchant_choices",
            "merchant": str(cls.merchant or ""),
            "choice_kind": str(kind),
            "items": [
                {"id": c.id, "name": c.name, "subtitle": c.subtitle,
                 **dict(c.data or {})}
                for c in selected
            ],
            "buttons": [
                {"label": c.name, "send_text": c.send_text}
                for c in selected if c.send_text
            ],
        }

    @classmethod
    def preview_card(cls, draft: MerchantDraft) -> dict:
        return {
            "type": "merchant_order_preview",
            "merchant": draft.merchant,
            "store_name": str((draft.store or {}).get("name") or ""),
            "items": [
                {"name": item.name, "quantity": item.quantity,
                 "specifications": list(item.specifications),
                 "amount_cents": item.amount_cents}
                for item in draft.items
            ],
            "fulfillment": draft.fulfillment,
            "original_amount_cents": draft.original_amount_cents,
            "discount_cents": draft.discount_cents,
            "amount_cents": draft.amount_cents,
            "currency": draft.currency,
            "confirmation_context": "merchant_create",
            # 创建确认只走全局 ConfirmBubble；卡片不持 token、不自带确认按钮。
            "buttons": [],
        }

    @staticmethod
    def preview_speech(draft: MerchantDraft, *, unpaid_expiry: bool = False) -> str:
        item_text = "、".join(
            f"{item.name}×{item.quantity}"
            + (f"（{'/'.join(item.specifications)}）" if item.specifications else "")
            for item in draft.items)
        store = str((draft.store or {}).get("name") or "所选门店")
        amount = f"{draft.amount_cents // 100}.{draft.amount_cents % 100:02d} 元"
        text = f"请确认：{store}，{item_text}，应付 {amount}。确认后只创建未支付订单。"
        if unpaid_expiry:
            text += "不支付会由商户自动失效。"
        return text

    @staticmethod
    def granted(meta) -> set[str]:
        raw = (meta or {}).get("granted_scopes", "")
        return {value.strip() for value in str(raw).split(",") if value.strip()}

    @staticmethod
    def healthy(binding) -> bool:
        return bool(binding and getattr(binding.client, "healthy", False)
                    and getattr(binding.client, "alive", False))

    async def call_tool(self, name: str, arguments: dict, *,
                        write: bool | None = None) -> dict:
        binding = self.tools.get(name)
        if not self.healthy(binding):
            raise RuntimeError(f"{name} unavailable")
        # Replay policy is an admission fact, never a codec hint.  Keep the
        # compatibility kwarg temporarily but deliberately ignore it: a caller
        # forgetting ``write=True`` must not make create/cancel replayable.
        declared_write = getattr(binding.tool, "write", False) is True
        if write is True and not declared_write:
            raise RuntimeError(f"{name} is not declared as write")
        if write is False and declared_write:
            raise RuntimeError(f"{name} is not declared as read")
        is_write = declared_write
        retry_policy = str(getattr(binding.tool, "retry_policy", "safe") or "safe")
        response = await binding.client.call_tool(
            binding.tool.name, arguments,
            timeout_s=binding.tool.timeout_ms / 1000.0,
            retry_on_session_loss=(not is_write and retry_policy != "never"),
        )
        if is_write and isinstance(response, dict) and response.get("ok") is True:
            predicate = getattr(binding.tool, "success_predicate", {}) or {}
            if not predicate:
                raise RuntimeError(f"{name} missing success_predicate")
            envelope = response.get("data")
            if not isinstance(envelope, dict):
                raise DeclaredBusinessIncomplete(
                    f"{name} business envelope is incomplete")
            rejected = False
            incomplete = False
            for path, allowed in predicate.items():
                value = MerchantWorkflow._dig_declared(envelope, path)
                if value is _MISSING:
                    incomplete = True
                    continue
                same_type = [candidate for candidate in allowed
                             if type(value) is type(candidate)]
                if not same_type:
                    incomplete = True
                    continue
                if not any(value == candidate for candidate in same_type):
                    rejected = True
            if rejected:
                raise DeclaredBusinessRejected(
                    f"{name} business predicate rejected")
            if incomplete:
                raise DeclaredBusinessIncomplete(
                    f"{name} business predicate is incomplete")
        return response

    @staticmethod
    def _dig_declared(value, path: str):
        current = value
        for part in str(path or "").split("."):
            if not part or not isinstance(current, dict) or part not in current:
                return _MISSING
            current = current[part]
        return current
