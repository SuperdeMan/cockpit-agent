"""商户复合工作流公共接口与确定性卡片/摘要。"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from urllib.parse import urlparse

from agents._sdk import AgentResult

from ..admission import normalize_hostname
from .models import MerchantChoice, MerchantDraft

logger = logging.getLogger("agent.mcp_bridge.merchant")

# ── P3（EVA 遗留卡）：数量槽容错解析 ─────────────────────────────────────
# planner 填「一杯」「2份」「１２」是自然语言常态，旧实现只认纯 ASCII 数字，
# 其余一律 NEED_SLOT「数量需要是 1 到 20 之间的整数」——机器话术直出 TTS
# （2026-08-15 真栈实测）。解析尽量宽、**校验边界不松**（仍 1–20 整数）。
_QTY_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_QTY_UNIT_RE = re.compile(r"(杯|份|个|只|支|块|盒|包|串)$")
_QTY_CN = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
           "十三": 13, "十四": 14, "十五": 15, "十六": 16, "十七": 17,
           "十八": 18, "十九": 19, "二十": 20}


# ── C3-C：**「全部」不是一个餐品名，是「不筛」** ─────────────────────────
# 真栈 T44：用户说的是「看看麦当劳有什么可以点的」（同一句话在 T1/T48 都走对了），
# 这一轮 planner 自己给 `item_query` 填了「全部」——桥的严格匹配「查询词 ⊂ 商品名」
# 当然一个都对不上，于是答「在售餐单里没查到"全部"」。**空槽=整份菜单**那条路
# 本来就在，一个「全部」把它变成了搜索词。
#
# 这是**消费方防御**（同「认不出退回干净类目词」那条判据）：模型输出是不可信输入，
# 桥不该假设 planner 只会填真实商品名。归一只在**只读菜单**路径做——下单路径
# 宁可追问也不能把「全部」理解成任何一款。
_MENU_WILDCARD_RE = re.compile(
    r"^(?:所有的?|全部的?|全都|都有(?:些)?(?:啥|什么)|有(?:些)?(?:啥|什么)|"
    r"整份(?:菜单|餐单)?|全部菜单|所有菜单|菜单|餐单|随便|任意)$")


def normalize_menu_query(value) -> str:
    """只读菜单的 `item_query` 归一：泛指词 → 空串（= 不筛，整份菜单）。"""
    text = str(value or "").strip()
    return "" if _MENU_WILDCARD_RE.match(text) else text


def parse_quantity(value, lo: int = 1, hi: int = 20) -> int | None:
    """「一杯」「2份」「１２」→ int；解析不出/越界返回 None（调用方友好追问）。"""
    text = str(value or "").strip().translate(_QTY_FW_DIGITS)
    text = _QTY_UNIT_RE.sub("", text).strip()
    if re.fullmatch(r"[0-9]+", text):
        n = int(text)
    elif text in _QTY_CN:
        n = _QTY_CN[text]
    else:
        return None
    return n if lo <= n <= hi else None


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

    @staticmethod
    def refused(speech: str, follow_up: str = "") -> AgentResult:
        """诚实拒绝：这一步没做、也没有东西待确认。

        打 `_refused` 保留键（§9.1）。`require_confirm=true` 的工作流里，executor 的
        中央确认闸会给**任何 OK 结果**追加「这个操作需要您确认后才会执行，确定继续吗？」，
        拒绝落在 OK 上就拼成自相矛盾的一句（2026-08-12 demo-2goetq 实证）。
        闸认这个键**只免除追加问句、不免除扣动作**（带动作的「拒绝」照旧被改判）。

        为什么不是 NEED_SLOT：试过，被真栈证否——那会挂起会话、吞掉后续每一句
        （2026-08-13 demo-f1hkwr：问麦当劳详情答瑞幸）。见 `_reselect_store` 的注释。
        """
        return AgentResult(speech=speech, follow_up=follow_up,
                           data={"_refused": True})

    def image_url(self, value) -> str:
        """商品图：**必须 https + 域名在该 server 的 `image_hosts` 精确白名单里**。

        外部服务给的 URL 是不可信输入，而这个值最终会变成 HMI 发起的一次网络请求。
        不合规返回空串——退回纯文字卡，不报错也不半渲染（商户换 CDN 该降级成没有图）。
        **放在基类而不是各商户各写一份**：两份判定必然漂移，而漂移的那一份就是缺口。
        """
        raw = str(value or "").strip()
        if not raw or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127
                          for ch in raw):
            return ""
        try:
            parsed = urlparse(raw)
        except ValueError:
            return ""
        if parsed.scheme != "https" or parsed.username or parsed.password:
            return ""
        allowed = {
            normalize_hostname(host)
            for host in (getattr(self.server, "image_hosts", []) or [])
        }
        allowed.discard("")
        host = normalize_hostname(parsed.hostname or "")
        if not host or host not in allowed:
            logger.debug("[%s] 商品图域名不在白名单，丢弃：host=%s",
                         self.merchant, host)
            return ""
        return raw

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
