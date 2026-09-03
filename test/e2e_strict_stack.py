"""数据真实性 e2e（治理 P2 D5）：严格栈冒烟 + mock 泄漏探针。

对**已起的真栈**（make up，.env 带真实凭证）做两件事：
  1. active LLM 不是 mock（严格栈基本面）；
  2. 天气/周边/导航/充电/车型手册五条 WS 请求返回的所有带 `_prov` 的卡，mode 不得为
     "mock"；且至少 2 张卡带 `_prov`（探针有效性下限，防止「全都没标所以全过」）；
  3. 真实手册 retrieval corpus 的全部主集/holdout 问句必须落 `manual` 卡，逐条核对
     SU7 车型、PDF 页、正文、图片与 real provenance；负例必须零 chunk，全部零 action。

mock 栈上跑无意义：检测到 active=mock 写结构化 whole-skip（退出码 77）——本探针属 live 车道。
用法：python test/e2e_strict_stack.py
"""
import asyncio
import json
from pathlib import Path
import re
import sys
import unicodedata
import urllib.error
import urllib.request

import yaml

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
REPO_ROOT = Path(__file__).resolve().parents[1]
MANUAL_CORPUS_PATH = REPO_ROOT / "test" / "eval_corpus" / "manual_rag_retrieval.yaml"
MANUAL_CATALOG_PATH = (
    REPO_ROOT / "agents" / "manual_rag" / "resources" / "manual_catalog.yaml"
)
# M0a（2026-07-24）补充电探针：navigation/charging 曾在运行期 ProviderError 后回退 mock
# 仍盖真实 provider 章（评审核实的 §9.5 铁律③违例，已改诚实降级）。泄漏形态（mock 数据
# 盖 real 章）本探针按 vendor/mode 一致性兜不住故障注入场景——运行期降级契约由
# agents/{navigation,charging_planner,nearby}/tests 的 outage 用例在 unit 层锁定。
def _load_manual_contract() -> tuple[dict[str, dict], dict]:
    corpus = yaml.safe_load(MANUAL_CORPUS_PATH.read_text(encoding="utf-8")) or {}
    catalog = yaml.safe_load(MANUAL_CATALOG_PATH.read_text(encoding="utf-8")) or {}
    cases = corpus.get("cases")
    documents = catalog.get("documents") or {}
    document = documents.get("xiaomi-su7-2024-user-manual")
    if corpus.get("version") != 1 or not isinstance(cases, list):
        raise ValueError("manual retrieval corpus is invalid")
    if not isinstance(document, dict):
        raise ValueError("manual catalog is invalid")
    probes: dict[str, dict] = {}
    for case in cases:
        if not isinstance(case, dict) or not str(case.get("query") or "").strip():
            raise ValueError("manual retrieval case is invalid")
        query = str(case["query"]).strip()
        if query in probes:
            raise ValueError(f"duplicate manual query: {query}")
        probes[query] = dict(case)
    return probes, dict(document)


MANUAL_PROBES, MANUAL_DOCUMENT = _load_manual_contract()
PROBES = (
    "北京今天天气怎么样", "附近有什么川菜馆", "导航去天安门",
    "帮我找附近的充电站", *MANUAL_PROBES,
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


def _comparable(value: object) -> str:
    return re.sub(
        r"\s+", "", unicodedata.normalize("NFKC", str(value or "")).casefold(),
    )


def _manual_card_errors(card: dict, expected: dict) -> list[str]:
    """按离线 retrieval evaluator 的同一字段口径审核真栈 manual 卡。"""
    errors: list[str] = []
    prov = card.get("_prov") or {}
    document = card.get("document") or {}
    chunks = [item for item in (card.get("chunks") or []) if isinstance(item, dict)]
    images = [item for item in (card.get("images") or []) if isinstance(item, dict)]
    pages = [int(item["page_start"]) for item in chunks if item.get("page_start")]
    page_set = set(pages)

    if prov.get("mode") != "real" or prov.get("vendor") != "xiaomi-su7-2024-user-manual":
        errors.append("manual provenance is not the approved real provider")
    for key in (
        "vehicle_model", "revision", "source_sha256", "content_sha256",
        "visual_assets_sha256",
    ):
        if document.get(key) != MANUAL_DOCUMENT.get(key):
            errors.append(f"document {key} mismatch")

    if expected.get("expect_empty"):
        if chunks:
            errors.append(f"expected empty chunks, got pages {pages}")
        if images:
            errors.append("expected empty images")
        return errors

    sources = card.get("sources") or []
    if not any("PDF第" in str(source) for source in sources):
        errors.append("manual card has no PDF page citation")
    expected_top = expected.get("expect_top_page")
    if expected_top is not None and (not pages or pages[0] != int(expected_top)):
        errors.append(f"wrong top page: want {expected_top}, got {pages[:1]}")
    expected_any = {int(page) for page in expected.get("expect_pages_any") or []}
    if expected_any and not expected_any.intersection(page_set):
        errors.append(f"missing expected page: want any {sorted(expected_any)}, got {pages}")
    expected_all = {int(page) for page in expected.get("expect_pages_all") or []}
    if not expected_all.issubset(page_set):
        errors.append(f"missing required pages: {sorted(expected_all - page_set)}")

    combined = _comparable("\n".join(str(item.get("content") or "") for item in chunks))
    missing_text = [
        str(term) for term in expected.get("expect_text_all") or []
        if _comparable(term) not in combined
    ]
    if missing_text:
        errors.append(f"missing text: {missing_text}")
    alternatives = [str(term) for term in expected.get("expect_text_any") or []]
    if alternatives and not any(_comparable(term) in combined for term in alternatives):
        errors.append(f"missing any text: {alternatives}")

    image_pages = {int(item["page_start"]) for item in images if item.get("page_start")}
    expected_image_pages = {
        int(page) for page in expected.get("expect_image_pages_any") or []
    }
    if expected_image_pages and not expected_image_pages.intersection(image_pages):
        errors.append(
            f"missing expected image page: want any {sorted(expected_image_pages)}, "
            f"got {sorted(image_pages)}"
        )
    captions = _comparable("\n".join(str(item.get("caption") or "") for item in images))
    missing_captions = [
        str(caption) for caption in expected.get("expect_image_caption_all") or []
        if _comparable(caption) not in captions
    ]
    if missing_captions:
        errors.append(f"missing image captions: {missing_captions}")
    for image in images:
        data_uri = str(image.get("data_uri") or "")
        if not data_uri.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")):
            errors.append("manual image is not a trusted inline PNG/JPEG")
            break
        if re.fullmatch(r"[0-9a-f]{64}", str(image.get("sha256") or "")) is None:
            errors.append("manual image SHA-256 is invalid")
            break
    return errors


def _manual_response_errors(msg: dict, expected: dict) -> list[str]:
    """一轮只能有一张 manual 卡且零 action；其它域卡不能被正确 manual 卡遮住。"""
    errors: list[str] = []
    if msg.get("actions"):
        errors.append("manual probe returned actions")
    cards = _cards(msg)
    manual_cards = [card for card in cards if card.get("type") == "manual"]
    other_types = [str(card.get("type") or "") for card in cards
                   if card.get("type") != "manual"]
    if len(manual_cards) != 1:
        errors.append(f"expected exactly one manual card, got {len(manual_cards)}")
    if other_types:
        errors.append(f"manual response included other card types: {other_types}")
    if len(manual_cards) == 1:
        errors.extend(_manual_card_errors(manual_cards[0], expected))
    return errors


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
            manual_seen: set[str] = set()
            manual_errors: list[str] = []
            for index, text in enumerate(PROBES, start=1):
                msg = await _ask(recorder, text, recorder.session_id(index))
                if text in MANUAL_PROBES:
                    errors = _manual_response_errors(msg, MANUAL_PROBES[text])
                    if errors:
                        manual_errors.extend(f"{text}: {error}" for error in errors)
                    else:
                        manual_seen.add(text)
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

            if leaks:
                recorder.fail_case(
                    "strict_stack_provider_provenance",
                    "mock_data_leak",
                    f"real stack returned {len(leaks)} mock provenance cards",
                )
                print("✗ 真栈出现 mock 数据卡（泄漏）：\n  " + "\n  ".join(leaks))
            elif manual_errors or set(MANUAL_PROBES) - manual_seen:
                missing = sorted(set(MANUAL_PROBES) - manual_seen)
                detail = "; ".join(manual_errors) or (
                    f"manual probes returned no valid manual card: {missing}")
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
