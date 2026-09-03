"""数据真实性 e2e（治理 P2 D5）：严格栈冒烟 + mock 泄漏探针。

对**已起的真栈**（make up，.env 带真实凭证）做两件事：
  1. active LLM 不是 mock（严格栈基本面）；
  2. 天气/周边/导航/充电/车型手册五条 WS 请求返回的所有带 `_prov` 的卡，mode 不得为
     "mock"；且至少 2 张卡带 `_prov`（探针有效性下限，防止「全都没标所以全过」）；
  3. 手册探针必须落 `manual` 卡，带 SU7 车型、PDF 页引用和 real provenance。

mock 栈上跑无意义：检测到 active=mock 写结构化 whole-skip（退出码 77）——本探针属 live 车道。
用法：python test/e2e_strict_stack.py
"""
import asyncio
import json
import sys
import urllib.error
import urllib.request

from support.e2e import CaseRecorder, is_network_timeout

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

LLM_HTTP = "http://localhost:50059"
# M0a（2026-07-24）补充电探针：navigation/charging 曾在运行期 ProviderError 后回退 mock
# 仍盖真实 provider 章（评审核实的 §9.5 铁律③违例，已改诚实降级）。泄漏形态（mock 数据
# 盖 real 章）本探针按 vendor/mode 一致性兜不住故障注入场景——运行期降级契约由
# agents/{navigation,charging_planner,nearby}/tests 的 outage 用例在 unit 层锁定。
MANUAL_PROBE = "SU7 的冷态胎压应该打到多少"
PROBES = (
    "北京今天天气怎么样", "附近有什么川菜馆", "导航去天安门",
    "帮我找附近的充电站", MANUAL_PROBE,
)


def _active_provider() -> str | None:
    try:
        with urllib.request.urlopen(f"{LLM_HTTP}/api/llm/providers", timeout=5) as r:
            data = json.loads(r.read().decode())
        return (data.get("active") or {}).get("provider", "")
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        if is_network_timeout(e):
            raise
        print(f"SKIP：llm-gateway HTTP 不可达（{e}）——需 make up 后再跑")
        return None


async def _ask(
    recorder: CaseRecorder,
    text: str,
    session: str,
) -> dict:
    async with websockets.connect(recorder.ws_url()) as ws:
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        recorder.confirm_identity_ack(ack)
        await ws.send(json.dumps({"text": text, "session_id": session}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=120))
            if msg.get("type") in ("final", "error"):
                return msg


def _cards(msg: dict) -> list[dict]:
    card = msg.get("ui_card") or {}
    if card.get("type") == "card_group":
        return [c for c in (card.get("items") or []) if isinstance(c, dict)]
    return [card] if card else []


async def main() -> int:
    recorder = CaseRecorder()
    active = None
    provider_error: tuple[str, str] | None = None
    try:
        active = _active_provider()
    except urllib.error.HTTPError as exc:
        provider_error = (
            "provider_http_error",
            f"llm-gateway provider inventory returned HTTP {exc.code}",
        )
    except Exception as exc:
        provider_error = (
            "provider_execution_failed",
            f"provider inventory failed: {type(exc).__name__}",
        )

    with recorder:
        if provider_error is not None:
            code, detail = provider_error
            recorder.fail_case(
                "strict_stack_provider_provenance",
                code,
                detail,
            )
        elif active is None:
            recorder.skip_case(
                "strict_stack_provider_provenance",
                "provider_unavailable",
                "llm-gateway provider inventory is unavailable",
            )
        elif active == "mock":
            print("SKIP：active LLM=mock（mock 栈），泄漏探针属 live 车道")
            recorder.skip_case(
                "strict_stack_provider_provenance",
                "credential_unavailable",
                "active LLM is mock",
            )
        else:
            print(
                f"=== 严格栈冒烟 + mock 泄漏探针"
                f"（active LLM: {active}）===",
            )

            prov_seen = 0
            leaks: list[str] = []
            manual_seen = False
            manual_errors: list[str] = []
            for index, text in enumerate(PROBES, start=1):
                msg = await _ask(recorder, text, recorder.session_id(index))
                for card in _cards(msg):
                    prov = card.get("_prov")
                    if not prov:
                        continue
                    prov_seen += 1
                    mark = (
                        f"{card.get('type')}: mode={prov.get('mode')} "
                        f"vendor={prov.get('vendor')}"
                    )
                    print(f"  [{text}] {mark}")
                    if prov.get("mode") == "mock":
                        leaks.append(f"{text} -> {mark}")
                    if text == MANUAL_PROBE and card.get("type") == "manual":
                        document = card.get("document") or {}
                        sources = card.get("sources") or []
                        if prov.get("mode") != "real":
                            manual_errors.append("manual provenance is not real")
                        elif document.get("vehicle_model") != "xiaomi-su7-2024":
                            manual_errors.append("manual vehicle_model is not xiaomi-su7-2024")
                        elif not any("PDF第" in str(source) for source in sources):
                            manual_errors.append("manual card has no PDF page citation")
                        else:
                            manual_seen = True

            if leaks:
                recorder.fail_case(
                    "strict_stack_provider_provenance",
                    "mock_data_leak",
                    f"real stack returned {len(leaks)} mock provenance cards",
                )
                print("✗ 真栈出现 mock 数据卡（泄漏）：\n  " + "\n  ".join(leaks))
            elif manual_errors or not manual_seen:
                detail = "; ".join(manual_errors) or "manual probe returned no valid manual card"
                recorder.fail_case(
                    "strict_stack_provider_provenance",
                    "manual_provider_invalid",
                    detail,
                )
                print(f"✗ 真实车型手册探针失败：{detail}")
            elif prov_seen < 2:
                recorder.fail_case(
                    "strict_stack_provider_provenance",
                    "assertion_failed",
                    f"only {prov_seen} cards carried provider provenance",
                )
                print(
                    f"✗ 带 _prov 的卡仅 {prov_seen} 张（<2）"
                    "——探针可能失效",
                )
            else:
                recorder.pass_case("strict_stack_provider_provenance")
                print(f"✅ PASS：{prov_seen} 张外源卡全为非 mock 来源")
    return recorder.exit_code()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
