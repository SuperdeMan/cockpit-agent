"""R4.4 拒识主链端到端验证：经 Edge Gateway WebSocket 注入带 input_source 的语音源请求。

前置：`make up` 起全栈 + 真 LLM provider（active 非 mock）。依赖：pip install websockets
用法：python test/e2e_rejection.py

SKIP guard：探测 llm-gateway active provider，mock 模式下 LLM 不输出 addressed，
fail-open 恒不拒是**正确**行为；此时写结构化 whole-skip 结果（退出码 77）。
"""
import asyncio
import json
import sys
import os
import urllib.request

from support.e2e import CaseRecorder

try:                                   # Windows 控制台默认 GBK，强制 UTF-8（同 e2e_ws.py 惯例）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

TIMEOUT = 60


def _active_provider() -> str:
    port = os.getenv("AUDIO_HTTP_PORT", "50059")
    host = os.getenv("LLM_GATEWAY_HTTP_HOST", "localhost")
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/llm/providers", timeout=5) as r:
            return (json.loads(r.read().decode("utf-8")).get("active", {}) or {}).get("provider", "?")
    except Exception:
        return "?"


async def ask(
    recorder: CaseRecorder,
    payload: dict,
    desc: str,
) -> dict:
    async with websockets.connect(recorder.ws_url()) as ws:
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        recorder.confirm_identity_ack(ack)
        await ws.send(json.dumps(payload))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            if msg.get("type") in ("final", "error"):
                print(f"\n[{desc}] 输入: {payload['text']!r}")
                print(f"  type={msg.get('type')} speech={msg.get('speech','')!r} "
                      f"ui_card={msg.get('ui_card')}")
                return msg


async def main() -> int:
    recorder = CaseRecorder()
    provider = _active_provider()
    with recorder:
        if provider.startswith("mock"):
            print(
                f"SKIP：active provider={provider}——mock 不判 addressed，"
                "fail-open 不拒是正确行为。",
            )
            recorder.skip_case(
                "rejection_matrix",
                "credential_unavailable",
                "active provider is mock and cannot evaluate addressed",
            )
        else:
            print(f"=== R4.4 拒识 E2E（provider={provider}）===")

            m1 = await ask(
                recorder,
                {
                    "text": "他昨天跟我说那个项目黄了",
                    "session_id": recorder.session_id(1),
                    "meta": {
                        "input_source": "voice_followup",
                        "voice_utterance_ms": "1500",
                    },
                },
                "case1 乘客对话（应拒识）",
            )
            card = m1.get("ui_card") or {}
            rejected = card.get("type") == "rejected" and not m1.get("speech")
            if rejected:
                recorder.pass_case("passenger_speech_rejected")
                print("  ✓ 已静默拒识（rejected 卡 + 无 TTS）")
            else:
                recorder.fail_case(
                    "passenger_speech_rejected",
                    "assertion_failed",
                    "expected rejected card with empty speech",
                )
                print("  ✗ 未拒识——期望 ui_card.type=rejected + speech 空")

            m2 = await ask(
                recorder,
                {
                    "text": "今天深圳天气怎么样",
                    "session_id": recorder.session_id(2),
                    "meta": {
                        "input_source": "voice_followup",
                        "voice_utterance_ms": "1800",
                    },
                },
                "case2 正常受话（不应拒）",
            )
            card = m2.get("ui_card") or {}
            answered = card.get("type") != "rejected" and m2.get("speech")
            if answered:
                recorder.pass_case("addressed_speech_answered")
                print("  ✓ 正常应答（受话指令不受拒识影响）")
            else:
                recorder.fail_case(
                    "addressed_speech_answered",
                    "assertion_failed",
                    "addressed request was rejected or had no response",
                )
                print("  ✗ 正常指令被误拒或无应答")
    return recorder.exit_code()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
