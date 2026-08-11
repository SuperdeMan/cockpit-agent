"""微信支付 APIv3 Native provider（扫码收款）——直连自实现。

- 请求签名：`WECHATPAY2-SHA256-RSA2048`，签名串 `METHOD\\nURL_PATH\\nts\\nnonce\\nbody\\n`，
  商户私钥 SHA256withRSA。
- **应答验签双模式**（设计 2.9）：优先「微信支付公钥」静态配置（2024 起新商户默认，
  `WECHATPAY_PUBLIC_KEY(_PATH)` + `WECHATPAY_PUBLIC_KEY_ID`，零轮换）；未配则平台证书
  懒加载兼容——`GET /v3/certificates`（APIv3 key AES-256-GCM 解密）内存缓存 12h，
  **验签遇未知序列号即时重拉一次**（覆盖轮换窗口），不做后台定时任务。
- 拉证书那一次请求自身无法验签（鸡生蛋）：TOFU（首次信任），与官方 SDK 同姿态。
- **v1 无支付回调、纯主动查单**：`notify_url` 是 v3 必填字段，填形式合法的不可达地址
  （`WECHATPAY_NOTIFY_URL`，默认 https://127.0.0.1/payment/notify）——车机无公网入站，
  收不到回调是设计而非缺陷（§9.17）。
- 无公开沙箱：本文件的真实联调待商户号配置；签名/验签由单测用自造密钥对锁死
  （test_sign_wechat.py），不拿支付宝沙箱通过盖微信的章。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import time

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509 import load_pem_x509_certificate

from .base import (CLOSED, PAID, WAITING, ChannelStatus, PaymentChannelError,
                   PaymentProvider, PaymentProviderError, QrResult)

logger = logging.getLogger("payment.providers.wechat")

BASE = "https://api.mch.weixin.qq.com"
_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
_CERT_TTL_S = 12 * 3600

_STATE_MAP = {
    "SUCCESS": PAID,
    "NOTPAY": WAITING,
    "USERPAYING": WAITING,
    "ACCEPT": WAITING,
    "CLOSED": CLOSED,
    "REVOKED": CLOSED,
    "PAYERROR": CLOSED,
    "REFUND": PAID,   # 已支付后发生退款——对「钱到过账」的判定仍是 PAID，退款态由网关自己管
}


def _pem(raw: str, *, tag: str) -> bytes:
    text = (raw or "").strip().replace("\\n", "\n")
    if "-----BEGIN" in text:
        return text.encode()
    body = "".join(text.split())
    lines = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN {tag}-----\n{lines}\n-----END {tag}-----\n".encode()


def _env_or_file(name: str) -> str:
    path = os.getenv(f"{name}_PATH", "")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            raise PaymentProviderError(f"{name}_PATH={path} 读取失败：{e}") from e
    return os.getenv(name, "")


class WechatProvider(PaymentProvider):
    channel = "wechat"
    mode = "real"

    def __init__(self, mchid: str, mch_serial: str, mch_private_key: str,
                 apiv3_key: str, app_id: str,
                 public_key: str = "", public_key_id: str = "",
                 notify_url: str = ""):
        if not (mchid and mch_serial and mch_private_key and apiv3_key and app_id):
            raise PaymentProviderError(
                "WechatProvider 缺凭证（WECHATPAY_MCHID / WECHATPAY_MCH_SERIAL / "
                "WECHATPAY_MCH_PRIVATE_KEY(_PATH) / WECHATPAY_APIV3_KEY / "
                "WECHATPAY_APP_ID）——fail-fast，不静默回退 mock")
        self.mchid = mchid
        self.mch_serial = mch_serial
        self.app_id = app_id
        self.notify_url = notify_url or "https://127.0.0.1/payment/notify"
        self._apiv3_key = apiv3_key.encode()
        self._key = serialization.load_pem_private_key(
            _pem(mch_private_key, tag="PRIVATE KEY"), password=None)
        self._pub = None            # 公钥模式：静态微信支付公钥
        self._pub_id = public_key_id
        if public_key:
            self._pub = serialization.load_pem_public_key(
                _pem(public_key, tag="PUBLIC KEY"))
        self._platform_certs: dict[str, object] = {}   # serial -> public key（证书模式缓存）
        self._certs_fetched_at = 0.0
        self._http = httpx.AsyncClient(timeout=_TIMEOUT, trust_env=True)

    @classmethod
    def from_env(cls) -> "WechatProvider":
        return cls(
            mchid=os.getenv("WECHATPAY_MCHID", ""),
            mch_serial=os.getenv("WECHATPAY_MCH_SERIAL", ""),
            mch_private_key=_env_or_file("WECHATPAY_MCH_PRIVATE_KEY"),
            apiv3_key=os.getenv("WECHATPAY_APIV3_KEY", ""),
            app_id=os.getenv("WECHATPAY_APP_ID", ""),
            public_key=_env_or_file("WECHATPAY_PUBLIC_KEY"),
            public_key_id=os.getenv("WECHATPAY_PUBLIC_KEY_ID", ""),
            notify_url=os.getenv("WECHATPAY_NOTIFY_URL", ""),
        )

    # ── 请求签名 ────────────────────────────────────────────────

    def _auth_header(self, method: str, url_path: str, body: str) -> str:
        ts = str(int(time.time()))
        nonce = secrets.token_hex(16)
        msg = f"{method}\n{url_path}\n{ts}\n{nonce}\n{body}\n"
        sig = base64.b64encode(
            self._key.sign(msg.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
        return (f'WECHATPAY2-SHA256-RSA2048 mchid="{self.mchid}",'
                f'nonce_str="{nonce}",signature="{sig}",'
                f'timestamp="{ts}",serial_no="{self.mch_serial}"')

    # ── 应答验签 ────────────────────────────────────────────────

    async def _verifier_for(self, serial: str):
        """按应答的 Wechatpay-Serial 找验签公钥：公钥模式直配 / 证书模式懒加载缓存。"""
        if self._pub is not None and (not self._pub_id or serial == self._pub_id):
            return self._pub
        now = time.time()
        if serial not in self._platform_certs or now - self._certs_fetched_at > _CERT_TTL_S:
            await self._fetch_platform_certs()
        return self._platform_certs.get(serial)

    async def _fetch_platform_certs(self) -> None:
        url_path = "/v3/certificates"
        headers = {
            "Authorization": self._auth_header("GET", url_path, ""),
            "Accept": "application/json",
            "User-Agent": "car-agent-payment-gateway",
        }
        try:
            resp = await self._http.get(BASE + url_path, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise PaymentChannelError(f"wechat 拉取平台证书失败：{e}") from e
        # TOFU：本次应答自身跳过验签（验签所需公钥正是本次要取的东西）
        certs: dict[str, object] = {}
        for item in resp.json().get("data", []):
            enc = item.get("encrypt_certificate", {})
            try:
                pem_bytes = AESGCM(self._apiv3_key).decrypt(
                    enc.get("nonce", "").encode(),
                    base64.b64decode(enc.get("ciphertext", "")),
                    enc.get("associated_data", "").encode())
                cert = load_pem_x509_certificate(pem_bytes)
                certs[item.get("serial_no", "")] = cert.public_key()
            except Exception as e:   # 单张证书解不开不拖垮整批
                logger.warning("wechat 平台证书解密失败 serial=%s：%s",
                               item.get("serial_no", ""), e)
        if certs:
            self._platform_certs = certs
            self._certs_fetched_at = time.time()

    async def _verify_response(self, resp: httpx.Response) -> bool:
        serial = resp.headers.get("Wechatpay-Serial", "")
        ts = resp.headers.get("Wechatpay-Timestamp", "")
        nonce = resp.headers.get("Wechatpay-Nonce", "")
        sign = resp.headers.get("Wechatpay-Signature", "")
        if not (serial and ts and nonce and sign):
            return False
        pub = await self._verifier_for(serial)
        if pub is None:
            return False
        msg = f"{ts}\n{nonce}\n{resp.text}\n"
        try:
            pub.verify(base64.b64decode(sign), msg.encode(),
                       padding.PKCS1v15(), hashes.SHA256())
            return True
        except Exception:
            return False

    # ── 请求 ────────────────────────────────────────────────────

    async def _call(self, method: str, url_path: str, body_obj: dict | None,
                    *, ok_codes: tuple[int, ...] = (200,)) -> tuple[int, dict]:
        body = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":")) \
            if body_obj is not None else ""
        headers = {
            "Authorization": self._auth_header(method, url_path, body),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "car-agent-payment-gateway",
        }
        try:
            resp = await self._http.request(method, BASE + url_path,
                                            headers=headers,
                                            content=body or None)
        except httpx.HTTPError as e:
            raise PaymentChannelError(f"wechat {url_path} 请求失败：{e}") from e
        payload: dict = {}
        if resp.text:
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
        if resp.status_code in ok_codes:
            if resp.status_code != 204 and not await self._verify_response(resp):
                raise PaymentChannelError(f"wechat {url_path} 应答验签失败")
            return resp.status_code, payload
        return resp.status_code, payload

    # ── 四接口 ──────────────────────────────────────────────────

    async def create_qr(self, payment_id: str, amount_cents: int,
                        description: str) -> QrResult:
        status, payload = await self._call("POST", "/v3/pay/transactions/native", {
            "appid": self.app_id,
            "mchid": self.mchid,
            "description": (description or "车载支付")[:127],
            "out_trade_no": payment_id,
            "notify_url": self.notify_url,
            "amount": {"total": amount_cents, "currency": "CNY"},
        })
        if status != 200 or not payload.get("code_url"):
            raise PaymentChannelError(
                f"wechat native 下单失败：{status}/{payload.get('code')}/{payload.get('message')}")
        return QrResult(qr_content=payload["code_url"])

    async def query(self, payment_id: str) -> ChannelStatus:
        status, payload = await self._call(
            "GET", f"/v3/pay/transactions/out-trade-no/{payment_id}?mchid={self.mchid}",
            None)
        if status == 404 or payload.get("code") == "ORDER_NOT_EXIST":
            return ChannelStatus(state=WAITING)
        if status != 200:
            raise PaymentChannelError(
                f"wechat query 失败：{status}/{payload.get('code')}/{payload.get('message')}")
        state = _STATE_MAP.get(payload.get("trade_state", ""), WAITING)
        paid = int((payload.get("amount") or {}).get("payer_total") or
                   (payload.get("amount") or {}).get("total") or 0)
        return ChannelStatus(state=state,
                             trade_no=payload.get("transaction_id", ""),
                             paid_amount_cents=paid if state == PAID else 0)

    async def close(self, payment_id: str) -> bool:
        status, payload = await self._call(
            "POST", f"/v3/pay/transactions/out-trade-no/{payment_id}/close",
            {"mchid": self.mchid}, ok_codes=(204,))
        if status == 204:
            return True
        if payload.get("code") in ("ORDER_NOT_EXIST", "ORDER_CLOSED"):
            return True
        return False

    async def refund(self, payment_id: str, amount_cents: int,
                     reason: str) -> tuple[bool, str]:
        refund_id = f"{payment_id}_r1"
        status, payload = await self._call("POST", "/v3/refund/domestic/refunds", {
            "out_trade_no": payment_id,
            "out_refund_no": refund_id,
            "reason": (reason or "用户退款")[:80],
            "amount": {"refund": amount_cents, "total": amount_cents,
                       "currency": "CNY"},
        })
        if status == 200:
            return True, refund_id
        return False, f"{payload.get('code')}/{payload.get('message')}"
