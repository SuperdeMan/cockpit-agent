"""支付渠道 Provider 抽象与决议工厂（契约 docs/conventions.md §9.17）。

- 接口面刻意小：create_qr / query / close / refund 四个动作，参数显式传
  （payment_id / amount_cents / description），不传 store 的订单对象——provider
  不该知道订单还有哪些字段；out_trade_no ≡ payment_id（§9.17 幂等三层链）。
- 决议与严格栈在本模块**自实现**（口径逐字对齐 agents/_sdk/provenance.py §9.4），
  不 import agents 包——那会经 agents/_sdk/__init__ 连带拖入 BaseAgent/gRPC 全家，
  而网关镜像刻意不含 agents/。
- payment 是独立决议域、**不进 REQUIRE_REAL_EXEMPT 默认豁免**（设计 2.7）：
  严格栈（REQUIRE_REAL_PROVIDERS=on）下 PAYMENT_VENDOR=mock 即启动失败。
"""
from __future__ import annotations

import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger("payment.providers")

# 渠道侧对账状态（封闭词表；渠道各自的状态字符串在 provider 内归一到这三个）
WAITING = "WAITING"   # 已亮码未支付（含渠道「交易不存在」——用户没扫码前渠道无单）
PAID = "PAID"         # 渠道确认收款
CLOSED = "CLOSED"     # 渠道侧已关/撤销/支付失败终局


@dataclass
class QrResult:
    qr_content: str            # 二维码内容（支付宝 qr_code / 微信 code_url / mockpay://）
    pay_url: str = ""          # H5 支付链接（渠道给了才有）
    channel_trade_ref: str = ""  # 渠道侧引用（precreate 阶段多为空）
    expires_at: float = 0.0    # epoch 秒；0=由 store 按 PAYMENT_QR_EXPIRE_S 补


@dataclass
class ChannelStatus:
    state: str                 # WAITING | PAID | CLOSED
    trade_no: str = ""
    paid_amount_cents: int = 0


class PaymentProviderError(RuntimeError):
    """显式配置了真实渠道但不可用（缺凭证/构造失败）。修配置，不静默回退 mock。"""


class PaymentChannelError(RuntimeError):
    """运行期渠道调用失败（网络/签名/渠道拒绝）。调用方诚实上报，不造数。"""


class PaymentProvider(ABC):
    channel: str = ""          # "alipay" | "wechat" | "mock"
    mode: str = "mock"         # "real" | "mock"（proto provider_mode 与 _prov 的依据）

    @abstractmethod
    async def create_qr(self, payment_id: str, amount_cents: int,
                        description: str) -> QrResult:
        """渠道下单出码（out_trade_no=payment_id）。失败抛 PaymentChannelError。"""

    @abstractmethod
    async def query(self, payment_id: str) -> ChannelStatus:
        """主动查单（v1 无回调，轮询唯一推进源）。"""

    @abstractmethod
    async def close(self, payment_id: str) -> bool:
        """关未支付的单。渠道「交易不存在」按成功算——用户从没扫码，渠道侧本就无单。"""

    @abstractmethod
    async def refund(self, payment_id: str, amount_cents: int,
                     reason: str) -> tuple[bool, str]:
        """退款（out_refund_no = payment_id + "_r1"，v1 仅整单一次）。返回 (ok, refund_id|error)。"""


def _strict_forbidden() -> bool:
    """严格栈下 payment 域 mock 决议是否被禁止。口径同 agents/_sdk/provenance.py。"""
    if os.getenv("REQUIRE_REAL_PROVIDERS", "off").strip().lower() not in ("on", "true", "1", "yes"):
        return False
    exempt = {d.strip() for d in
              os.getenv("REQUIRE_REAL_EXEMPT", "parking,knowledge").split(",") if d.strip()}
    return "payment" not in exempt


def resolve_payment_providers() -> dict[str, PaymentProvider]:
    """按 PAYMENT_VENDOR 构造渠道 provider 集合。

    - 返回的 dict **始终含 "mock" 键**：PAYMENT_REAL_SCENES 白名单外的场景强制路由
      mock（fail-closed，设计 2.7），这条兜底路径不能因为配了真渠道就消失。
    - 决议行 `provider[payment]=…` 格式逐字对齐 §9.4（e2e_strict_stack 的 grep 探针）。
    - 显式 real 意图下构造失败 → 抛 PaymentProviderError 启动即炸，绝不静默回退 mock。
    """
    from .mock import MockPaymentProvider

    vendor_env = os.getenv("PAYMENT_VENDOR", "mock").strip().lower()
    vendors = [v.strip() for v in vendor_env.split(",") if v.strip()]
    providers: dict[str, PaymentProvider] = {"mock": MockPaymentProvider()}
    real_parts: list[str] = []

    for v in vendors:
        if v == "mock":
            continue
        if v == "alipay":
            from .alipay import AlipayProvider
            providers["alipay"] = AlipayProvider.from_env()
            real_parts.append("alipay(real)")
        elif v == "wechat":
            from .wechat import WechatProvider
            providers["wechat"] = WechatProvider.from_env()
            real_parts.append("wechat(real)")
        else:
            raise PaymentProviderError(
                f"PAYMENT_VENDOR={v!r} 未知（可选 mock/alipay/wechat）——fail-fast，不猜")

    line = f"provider[payment]={','.join(real_parts) if real_parts else 'mock'}"
    logger.info(line)
    print(line, flush=True)
    if not real_parts and _strict_forbidden():
        raise PaymentProviderError(
            "REQUIRE_REAL_PROVIDERS=on：provider[payment] 决议为 mock——严格栈禁止；"
            "配置 PAYMENT_VENDOR=alipay|wechat 及其凭证，或把 payment 加入 REQUIRE_REAL_EXEMPT")
    return providers
