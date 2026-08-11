"""支付宝沙箱人工联调探针（§9.17 批 2 的「真实接入」验收点）。

做什么：用 .env 里的沙箱凭证直连 AlipayProvider（不经编排/Agent），走一遍
precreate（1 分钱）→ 打印二维码内容 → 等你用**沙箱钱包 App** 扫码支付 →
轮询查单到 PAID → 退款收尾。四接口全链路真实验证。

为什么不进 e2e_manifest：中间需要人掏出手机扫码——人工联调不该被任何自动车道
选中（比 e2e_observability 的 manual_inspection 语义更进一步：连 runner 都不占）。

前置（.env 或环境变量）：
  ALIPAY_APP_ID / ALIPAY_APP_PRIVATE_KEY(_PATH) / ALIPAY_PUBLIC_KEY(_PATH)
  ALIPAY_GATEWAY=https://openapi-sandbox.dl.alipaydev.com/gateway.do
凭证申请：open.alipay.com 控制台 → 开发工具-沙箱；手机装「沙箱钱包」。
用法：python scripts/alipay_sandbox_probe.py [--amount-fen 1] [--skip-refund]
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
import time
import uuid

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PG_DIR = os.path.join(_ROOT, "payment-gateway")


def _load_pkg():
    if "payment_gateway" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "payment_gateway", os.path.join(_PG_DIR, "__init__.py"),
        submodule_search_locations=[_PG_DIR])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["payment_gateway"] = pkg
    spec.loader.exec_module(pkg)


def _load_env_file() -> None:
    """读 .env（不覆盖已有环境变量）——探针脚本从仓库根直跑的便利层。"""
    path = os.path.join(_ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--amount-fen", type=int, default=1)
    parser.add_argument("--skip-refund", action="store_true")
    parser.add_argument("--wait-s", type=int, default=180)
    args = parser.parse_args()

    _load_env_file()
    _load_pkg()
    from payment_gateway.providers.alipay import AlipayProvider
    from payment_gateway.providers.base import (PAID, PaymentProviderError,
                                                WAITING)

    gateway = os.getenv("ALIPAY_GATEWAY", "")
    if "sandbox" not in gateway:
        print("SKIP：ALIPAY_GATEWAY 未指向沙箱（拒绝对生产网关跑探针）。\n"
              "  在 .env 设 ALIPAY_GATEWAY=https://openapi-sandbox.dl.alipaydev.com/gateway.do")
        return 0
    try:
        provider = AlipayProvider.from_env()
    except PaymentProviderError as e:
        print(f"SKIP：沙箱凭证未配置——{e}")
        return 0

    payment_id = f"probe_{uuid.uuid4().hex[:12]}"
    print(f"[1/4] precreate {args.amount_fen} 分（out_trade_no={payment_id}）…")
    qr = await provider.create_qr(payment_id, args.amount_fen, "car-agent 沙箱联调")
    print(f"      二维码内容：{qr.qr_content}")
    try:
        import qrcode
        q = qrcode.QRCode(border=1)
        q.add_data(qr.qr_content)
        q.print_ascii(invert=True)
    except Exception:
        pass
    print("      → 打开手机「沙箱钱包」App 扫上方二维码支付")

    from payment_gateway.providers.base import PaymentChannelError

    print(f"[2/4] 轮询查单（最多 {args.wait_s}s）…")
    deadline = time.time() + args.wait_s
    state = None
    while time.time() < deadline:
        try:
            st = await provider.query(payment_id)
        except PaymentChannelError as e:
            # 沙箱以抖动闻名（SYSTEM_ERROR 随手可见）——退避继续，不让抖动打断联调
            print(f"      查单抖动（退避重试）：{str(e)[:80]}")
            await asyncio.sleep(4)
            continue
        if st.state != state:
            state = st.state
            print(f"      {time.strftime('%H:%M:%S')} state={state} "
                  f"trade_no={st.trade_no or '-'}")
        if st.state == PAID:
            print(f"[3/4] ✅ 渠道确认收款：trade_no={st.trade_no} "
                  f"amount={st.paid_amount_cents} 分")
            break
        if st.state not in (WAITING,):
            print(f"[3/4] 终态 {st.state}（未支付）")
            break
        await asyncio.sleep(3)
    else:
        print("[3/4] 超时未支付——关单收尾")
        ok = await provider.close(payment_id)
        print(f"      close → {ok}")
        return 1

    if state == PAID and not args.skip_refund:
        print("[4/4] 退款收尾…")
        ok, ref = await provider.refund(payment_id, args.amount_fen, "联调退款")
        print(f"      refund → ok={ok} refund_id={ref}")
        return 0 if ok else 1
    return 0 if state == PAID else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(asyncio.run(main()))
