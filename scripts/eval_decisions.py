"""JV03 离线评测：Jev 判中文座舱话语判得怎么样？（docs/design/2026-10-04-jev-decide-integration.md）

经网关的 DecisionService 发真实请求（真实 TypeSafe 提供方；凭证只取本进程的环境变量 TYPESAFE_API_KEY，从不打印），评的是仓库里
带标注的语料——测试数据，不是用户数据：

- 受话：test/eval_corpus/rejection_cases.yaml（accept = 对助手说的，reject = 乘客间对话 / 播报 / 打电话）；
- 追问：test/eval_corpus/clarify_cases.yaml（该追问 vs 直接做），加上意图对抗语料里金标写明必须 / 禁止追问的轮次。

每个任务用英文和中文两种问法各问一遍（Jev 的主训练语言是英文）。这些任务规格只用于评测，不在网关 allowlist 里。按变体报告：
AUC、0.5 处准确率，以及在两个阈值之间弃权时的覆盖率 / 风险；写一份带逐条概率的 JSON 证据文件。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "llm-gateway", ROOT / "gen" / "python", ROOT / "test"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from google.protobuf.struct_pb2 import Struct  # noqa: E402
from cockpit.llm.v1 import llm_pb2  # noqa: E402

from decision_provider import TypeSafeDecisionProvider  # noqa: E402
from decision_service import DecisionService  # noqa: E402
from runtime.decision_contract import QuestionSpec, TaskSpec  # noqa: E402

_CRITERIA = {
    "addressed_en": ("Judge only state.utterance, a sentence heard in a car cabin. Is it spoken to the in-car voice "
                     "assistant (a request, a question or a feeling the assistant should respond to), rather than talk "
                     "between passengers, a radio or TV broadcast, a phone call or speech addressed to another person?",
                     {"true": "The assistant is being addressed and should respond.",
                      "false": "Someone else is being addressed, or it is a broadcast, phone call or background talk."}),
    "addressed_zh": ("只根据 state.utterance（车内听到的一句话）判断：这句话是不是对车载语音助手说的（请求、提问或希望助手回应的感受），"
                     "而不是乘客之间聊天、广播电视播报、打电话或对别人说的话？",
                     {"true": "是在对助手说话，助手应该回应。", "false": "是在对别人说、广播播报、打电话或背景对话。"}),
    "clarify_en": ("Judge only state.utterance, said to an in-car assistant that can navigate, search nearby places, "
                   "control the car, play media and answer questions. Must the assistant ask a follow-up question before "
                   "doing anything, because it is unclear what the user wants done (for example only a place or object "
                   "name with no request, or wording that reasonably fits two different actions)?",
                   {"true": "What to do is unclear; the assistant should ask before acting.",
                    "false": "What the user wants is clear enough to act on or answer directly."}),
    "clarify_zh": ("只根据 state.utterance（对车载助手说的一句话；助手能导航、找周边、控车、放媒体、回答问题）判断："
                   "助手是否必须先追问一句才能行动，因为用户想做什么不清楚（例如只说了一个地名或物体名而没有请求，"
                   "或者同一句话合理地对应两种不同的动作）？",
                   {"true": "想做什么不清楚，应该先问再做。", "false": "用户想要什么足够清楚，可以直接执行或回答。"}),
}


def _spec(name: str) -> TaskSpec:
    instructions, criteria = _CRITERIA[name]
    return TaskSpec(task_id=name, rubric_version="eval0", payload_fields={"utterance": "str"},
                    required=frozenset({"utterance"}),
                    questions=(QuestionSpec(id="verdict", type="noul", instructions=instructions, criteria=criteria),))


def load_items() -> dict[str, list[tuple[str, bool]]]:
    """任务 → [(原话, 金标是否为「是」)]。"""
    corpus = ROOT / "test" / "eval_corpus"
    addressed = [(row["text"], row["expect"] == "accept")
                 for row in yaml.safe_load((corpus / "rejection_cases.yaml").read_text(encoding="utf-8"))]
    clarify = [(row["text"], row["expect"] == "clarify")
               for row in yaml.safe_load((corpus / "clarify_cases.yaml").read_text(encoding="utf-8"))]
    from support.intent_adversarial_contract import load_cases
    for case in load_cases(corpus / "intent_adversarial" / "cases"):
        for turn in case.turns:
            if turn.expected.clarify in ("required", "forbidden") and not turn.context:
                clarify.append((turn.utterance, turn.expected.clarify == "required"))
    return {"addressed": addressed, "clarify": clarify}


def auc(scored: list[tuple[float, bool]]) -> float | None:
    pos = [s for s, g in scored if g]
    neg = [s for s, g in scored if not g]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def report(scored: list[tuple[float, bool]]) -> dict:
    out = {"n": len(scored), "positives": sum(g for _, g in scored), "auc": auc(scored),
           "accuracy_at_0.5": sum((s >= 0.5) == g for s, g in scored) / len(scored) if scored else None}
    for low, high in ((0.3, 0.7), (0.2, 0.8)):
        taken = [(s, g) for s, g in scored if s <= low or s >= high]
        wrong = sum((s >= high) != g for s, g in taken)
        out[f"abstain_{low}_{high}"] = {"coverage": len(taken) / len(scored) if scored else None,
                                        "risk": wrong / len(taken) if taken else None, "taken": len(taken)}
    return out


async def run(concurrency: int, out: Path) -> int:
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key.strip():
        print("TYPESAFE_API_KEY is not set in this process")
        return 2
    specs = {_spec(name).key(): _spec(name) for name in _CRITERIA}
    service = DecisionService(TypeSafeDecisionProvider(key), enabled=True, tasks=tuple(_CRITERIA),
                              max_budget_ms=15000, specs=specs)
    items = load_items()
    gate = asyncio.Semaphore(concurrency)
    rows: list[dict] = []
    tokens = {"input": 0, "unknown": 0}

    async def ask(variant: str, text: str, gold: bool):
        payload = Struct()
        payload.update({"utterance": text})
        request = llm_pb2.DecideRequest(request_id=f"eval-{variant}-{len(rows)}", tasks=[
            llm_pb2.DecisionTask(task_id=variant, rubric_version="eval0", payload=payload)])
        async with gate:
            response = await service.decide(request)
        result = response.results[0]
        if response.usage.known:
            tokens["input"] += response.usage.input_tokens
        else:
            tokens["unknown"] += 1
        p = result.answers[0].noul.p_true if result.answers else None
        rows.append({"variant": variant, "text": text, "gold": gold, "p_true": p,
                     "status": llm_pb2.DecisionStatus.Name(result.status), "reason": result.reason_code,
                     "latency_ms": response.latency_ms})

    jobs = [ask(f"{task}_{lang}", text, gold) for task, pairs in items.items()
            for lang in ("en", "zh") for text, gold in pairs]
    await asyncio.gather(*jobs)
    await service.provider.aclose()
    summary = {}
    for variant in _CRITERIA:
        scored = [(r["p_true"], r["gold"]) for r in rows if r["variant"] == variant and r["p_true"] is not None]
        failed = sum(1 for r in rows if r["variant"] == variant and r["p_true"] is None)
        summary[variant] = report(scored) | {"failed_calls": failed}
    latencies = sorted(r["latency_ms"] for r in rows)
    summary["latency_ms_p50_p95"] = [latencies[len(latencies) // 2], latencies[int(len(latencies) * 0.95) - 1]] \
        if latencies else None
    summary["input_tokens"] = tokens
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path, default=ROOT / ".artifacts" / "decisions" / "eval-jev.json")
    args = parser.parse_args()
    return asyncio.run(run(args.concurrency, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
