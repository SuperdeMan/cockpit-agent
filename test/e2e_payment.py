"""支付真栈闭环（§9.17，批 2）：交停车费 → 确认亮码 → mock 渠道自动支付 → 防双付。

纯 WS 黑盒三场景（刻意不从宿主直连 :50071——winnat 动态保留区挡 50070/50071
宿主发布的老坑，`compose.winnat.local.yaml`）：
  ① 「交停车费」→ NEED_CONFIRM，话术是**网关 confirm_prompt**（金额=订单快照）；
  ② 「确认」→ payment_qr 卡：mockpay:// 码 + qr_svg data URI + **mock 角标**
     （PAYMENT_REAL_SCENES 默认空 → 强制 mock provider，零真渠道外呼的用户可见证据）；
  ③ 等 PAYMENT_MOCK_AUTOPAY_S + 轮询裕量后再说「交停车费」→ 「已经支付过」
     ——这句话术只有幂等命中 **captured** 单才会出现，一句话同时证明
     「worker 查单推进了终态」与「幂等防双付」两件事。

注：场景③依赖 mock 停车数据源恒返 order_id="current"（幂等键因此稳定）；真实
ETCP 接入后每次停车是新订单号，不会误触「已支付过」。
前置：全栈已起（make up；PAYMENT_MOCK_AUTOPAY_S 默认 8）。依赖：pip install websockets
用法：python test/e2e_payment.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from support.e2e import CaseRecorder, assert_persistent_source_contract


def _source_contract() -> None:
    assert_persistent_source_contract(Path(__file__).read_text(encoding="utf-8"))


if "--source-contract" in sys.argv:
    _source_contract()
    print("source contract: PASS")
    raise SystemExit(0)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

TIMEOUT = 120


def _autopay_wait_s() -> float:
    try:
        autopay = float(os.getenv("PAYMENT_MOCK_AUTOPAY_S", "8"))
    except ValueError:
        autopay = 8.0
    return max(autopay, 0.0) + 10.0     # worker tick 3s + 步进裕量


async def _ask(recorder: CaseRecorder, ws, text: str, session: str) -> dict:
    await ws.send(json.dumps({"text": text, "session_id": session}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
        if msg.get("type") in ("final", "error"):
            return msg


def _card(msg: dict) -> dict:
    card = msg.get("ui_card") or {}
    if card.get("type") == "card_group":
        for item in card.get("items") or []:
            if isinstance(item, dict) and item.get("type") == "payment_qr":
                return item
    return card


async def run(recorder: CaseRecorder) -> None:
    session = recorder.session_id(1)
    async with websockets.connect(recorder.ws_url()) as ws:
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        recorder.confirm_identity_ack(ack)

        # ① 建单出确认话术（渠道零动作；金额来自订单快照）
        first = await _ask(recorder, ws, "帮我交一下停车费", session)
        speech1 = first.get("speech", "")
        if "确认支付" in speech1 and "15.00" in speech1:
            recorder.pass_case("pay_authorize_confirm_prompt")
        else:
            recorder.fail_case("pay_authorize_confirm_prompt", "assertion_failed",
                               f"speech={speech1[:120]}")
            return

        # ② 确认 → 亮码（mock 码 + SVG + mock 角标）
        second = await _ask(recorder, ws, "确认", session)
        card = _card(second)
        prov = card.get("_prov") or {}
        checks = (
            card.get("type") == "payment_qr",
            str(card.get("qr_content", "")).startswith("mockpay://"),
            str(card.get("qr_svg", "")).startswith("data:image/svg+xml;base64,"),
            prov.get("mode") == "mock",
            "15" in str(card.get("amount", "")),
        )
        if all(checks):
            recorder.pass_case("pay_capture_qr_with_mock_badge")
        else:
            recorder.fail_case("pay_capture_qr_with_mock_badge", "assertion_failed",
                               f"card_type={card.get('type')} checks={checks}")
            return

        # ③ mock 自动支付完成 → 幂等命中 captured →「已经支付过」
        await asyncio.sleep(_autopay_wait_s())
        third = await _ask(recorder, ws, "交停车费", session)
        speech3 = third.get("speech", "")
        if "已经支付过" in speech3:
            recorder.pass_case("pay_worker_captured_and_no_double_pay")
        else:
            recorder.fail_case("pay_worker_captured_and_no_double_pay",
                               "assertion_failed", f"speech={speech3[:120]}")


def main() -> int:
    _source_contract()
    recorder = CaseRecorder()
    with recorder:
        asyncio.run(run(recorder))
    result = recorder.result
    print(f"\n=== 结果：{result.counts['passed']}/{result.counts['selected']} 通过 ===")
    return recorder.exit_code()


if __name__ == "__main__":
    sys.exit(main())
