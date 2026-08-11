"""支付宝当面付 provider（precreate 扫码收款）——OpenAPI 直连自实现。

不引第三方 SDK 的理由（设计 4.批1）：接口面只有四个（precreate/query/close/refund），
RSA2 签名是标准流程，自实现 + 单测锁死报文构造，比引入带全局状态的 SDK 更可控。

- 网关地址 `ALIPAY_GATEWAY` 可指沙箱（openapi-sandbox.dl.alipaydev.com）——
  真实联调的验收路径就是沙箱钱包扫码（设计 §1）。
- 签名：除 `sign` 外所有非空公共参数按 key 升序 `k=v` 拼接，SHA256withRSA。
- **验签必须针对响应原文中节点的字节**（`_extract_node`）：重新序列化会变键序/
  空白，签名对不上。
- 渠道「交易不存在」（ACQ.TRADE_NOT_EXIST）的语义：precreate 只是出码，用户扫码前
  渠道侧没有交易——query 按 WAITING 处理、close 按成功处理，不是错误。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import datetime

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .base import (CLOSED, PAID, WAITING, ChannelStatus, PaymentChannelError,
                   PaymentProvider, PaymentProviderError, QrResult)

logger = logging.getLogger("payment.providers.alipay")

DEFAULT_GATEWAY = "https://openapi.alipay.com/gateway.do"
_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


def _fen_to_yuan(cents: int) -> str:
    """分 → 元字符串，整数运算防浮点（500 → "5.00"）。"""
    return f"{cents // 100}.{cents % 100:02d}"


def _yuan_to_fen(s: str) -> int:
    try:
        yuan, _, fen = (s or "0").partition(".")
        return int(yuan or "0") * 100 + int((fen + "00")[:2] or "0")
    except ValueError:
        return 0


def _ensure_pem(raw: str, *, private: bool) -> bytes:
    """支付宝控制台给的是裸 base64 串；env 里的多行 PEM 常存成 \\n 字面量。归一成 PEM。"""
    text = (raw or "").strip().replace("\\n", "\n")
    if "-----BEGIN" in text:
        return text.encode()
    body = "".join(text.split())
    lines = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
    tag = "PRIVATE KEY" if private else "PUBLIC KEY"
    return f"-----BEGIN {tag}-----\n{lines}\n-----END {tag}-----\n".encode()


def _load_private_key(raw: str):
    pem = _ensure_pem(raw, private=True)
    try:
        return serialization.load_pem_private_key(pem, password=None)
    except ValueError:
        # 控制台旧工具产 PKCS#1（RSA PRIVATE KEY 头）
        pem2 = pem.replace(b"BEGIN PRIVATE KEY", b"BEGIN RSA PRIVATE KEY") \
                  .replace(b"END PRIVATE KEY", b"END RSA PRIVATE KEY")
        return serialization.load_pem_private_key(pem2, password=None)


def _extract_node(text: str, key: str) -> str:
    """从响应**原文**里提取 `"key":{...}` 的原始子串（验签对象；跳过字符串内花括号）。"""
    anchor = text.find(f'"{key}"')
    if anchor < 0:
        return ""
    start = text.find("{", anchor)
    if start < 0:
        return ""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


class AlipayProvider(PaymentProvider):
    channel = "alipay"
    mode = "real"

    def __init__(self, app_id: str, app_private_key: str, alipay_public_key: str,
                 gateway: str = DEFAULT_GATEWAY):
        if not (app_id and app_private_key and alipay_public_key):
            raise PaymentProviderError(
                "AlipayProvider 缺凭证（ALIPAY_APP_ID / ALIPAY_APP_PRIVATE_KEY(_PATH) / "
                "ALIPAY_PUBLIC_KEY(_PATH)）——fail-fast，不静默回退 mock")
        self.app_id = app_id
        self.gateway = gateway or DEFAULT_GATEWAY
        self._key = _load_private_key(app_private_key)
        self._pub = serialization.load_pem_public_key(
            _ensure_pem(alipay_public_key, private=False))
        self._http = httpx.AsyncClient(timeout=_TIMEOUT, trust_env=True)

    @classmethod
    def from_env(cls) -> "AlipayProvider":
        def _val(name: str) -> str:
            path = os.getenv(f"{name}_PATH", "")
            if path:
                try:
                    with open(path, encoding="utf-8") as f:
                        return f.read()
                except OSError as e:
                    raise PaymentProviderError(f"{name}_PATH={path} 读取失败：{e}") from e
            return os.getenv(name, "")

        return cls(
            app_id=os.getenv("ALIPAY_APP_ID", ""),
            app_private_key=_val("ALIPAY_APP_PRIVATE_KEY"),
            alipay_public_key=_val("ALIPAY_PUBLIC_KEY"),
            gateway=os.getenv("ALIPAY_GATEWAY", DEFAULT_GATEWAY),
        )

    # ── 签名/验签 ────────────────────────────────────────────────

    def _sign(self, params: dict[str, str]) -> str:
        unsigned = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k])
        sig = self._key.sign(unsigned.encode(), padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode()

    def _verify_response(self, text: str, node_key: str) -> bool:
        node = _extract_node(text, node_key)
        try:
            sign = json.loads(text).get("sign", "")
        except (ValueError, AttributeError):
            return False
        if not (node and sign):
            return False
        try:
            self._pub.verify(base64.b64decode(sign), node.encode(),
                             padding.PKCS1v15(), hashes.SHA256())
            return True
        except Exception:
            return False

    # ── 请求 ────────────────────────────────────────────────────

    async def _call(self, method: str, biz_content: dict) -> dict:
        """一次 OpenAPI 调用：签名 → POST → 验签 → 返回 response 节点 dict。"""
        params = {
            "app_id": self.app_id,
            "method": method,
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps(biz_content, ensure_ascii=False,
                                      separators=(",", ":")),
        }
        params["sign"] = self._sign(params)
        try:
            resp = await self._http.post(self.gateway, data=params)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise PaymentChannelError(f"alipay {method} 请求失败：{e}") from e
        text = resp.text
        node_key = method.replace(".", "_") + "_response"
        if not self._verify_response(text, node_key):
            # 网关级错误（error_response）没有业务节点签名可验，如实抛出
            raise PaymentChannelError(
                f"alipay {method} 应答验签失败（或返回 error_response）："
                f"{text[:200]}")
        try:
            return json.loads(text)[node_key]
        except (ValueError, KeyError) as e:
            raise PaymentChannelError(f"alipay {method} 应答解析失败") from e

    @staticmethod
    def _not_exist(node: dict) -> bool:
        return node.get("code") == "40004" and \
            node.get("sub_code") in ("ACQ.TRADE_NOT_EXIST", "ACQ.TRADE_NOT_EXISTS")

    # ── 四接口 ──────────────────────────────────────────────────

    async def create_qr(self, payment_id: str, amount_cents: int,
                        description: str) -> QrResult:
        node = await self._call("alipay.trade.precreate", {
            "out_trade_no": payment_id,
            "total_amount": _fen_to_yuan(amount_cents),
            "subject": (description or "车载支付")[:256],
        })
        if node.get("code") != "10000" or not node.get("qr_code"):
            raise PaymentChannelError(
                f"alipay precreate 失败：{node.get('code')}/{node.get('sub_msg') or node.get('msg')}")
        return QrResult(qr_content=node["qr_code"])

    async def query(self, payment_id: str) -> ChannelStatus:
        node = await self._call("alipay.trade.query", {"out_trade_no": payment_id})
        if self._not_exist(node):
            return ChannelStatus(state=WAITING)   # 出码未扫，渠道侧本就无单
        if node.get("code") != "10000":
            raise PaymentChannelError(
                f"alipay query 失败：{node.get('code')}/{node.get('sub_msg') or node.get('msg')}")
        status = node.get("trade_status", "")
        if status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            return ChannelStatus(state=PAID, trade_no=node.get("trade_no", ""),
                                 paid_amount_cents=_yuan_to_fen(node.get("total_amount", "0")))
        if status == "TRADE_CLOSED":
            return ChannelStatus(state=CLOSED, trade_no=node.get("trade_no", ""))
        return ChannelStatus(state=WAITING, trade_no=node.get("trade_no", ""))

    async def close(self, payment_id: str) -> bool:
        node = await self._call("alipay.trade.close", {"out_trade_no": payment_id})
        return node.get("code") == "10000" or self._not_exist(node)

    async def refund(self, payment_id: str, amount_cents: int,
                     reason: str) -> tuple[bool, str]:
        refund_id = f"{payment_id}_r1"
        node = await self._call("alipay.trade.refund", {
            "out_trade_no": payment_id,
            "refund_amount": _fen_to_yuan(amount_cents),
            "out_request_no": refund_id,
            "refund_reason": (reason or "用户退款")[:256],
        })
        if node.get("code") != "10000":
            return False, f"{node.get('code')}/{node.get('sub_msg') or node.get('msg')}"
        # fund_change=N = 本次请求没动钱（同 out_request_no 幂等重放）——照样算成功
        return True, refund_id
