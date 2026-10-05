"""Jev 判别网关 JV01 真请求冒烟（docs/design/2026-10-04-jev-decide-integration.md）。

在本进程里用真实 TypeSafe 提供方跑网关的 DecisionService，经 `smoke` 任务判几句合成句子。凭证只从环境变量 TYPESAFE_API_KEY 读
——只注入这个进程（不落仓库里的任何文件），也从不打印。每句打印：状态、原因、「要求动作」的概率、时延、token 用量与实际模型。
请求体与凭证都不进日志。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "llm-gateway", ROOT / "gen" / "python"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from google.protobuf.struct_pb2 import Struct  # noqa: E402
from cockpit.llm.v1 import llm_pb2  # noqa: E402

from decision_provider import TypeSafeDecisionProvider  # noqa: E402
from decision_service import DecisionService  # noqa: E402

TEXTS = ("打开空调", "空调怎么打开？", "把车窗关上", "今天深圳天气怎么样", "别开车窗，只说说怎么开")


async def run(budget_ms: int) -> int:
    key = os.environ.get("TYPESAFE_API_KEY", "")
    if not key.strip():
        print("TYPESAFE_API_KEY is not set in this process")
        return 2
    service = DecisionService(TypeSafeDecisionProvider(key), enabled=True, tasks=("smoke",), max_budget_ms=budget_ms)
    failures = 0
    for index, text in enumerate(TEXTS):
        payload = Struct()
        payload.update({"text": text})
        request = llm_pb2.DecideRequest(request_id=f"smoke-{index}", tasks=[
            llm_pb2.DecisionTask(task_id="smoke", rubric_version="1", payload=payload)], budget_ms=budget_ms)
        response = await service.decide(request)
        result = response.results[0]
        p_true = result.answers[0].noul.p_true if result.answers else None
        status = llm_pb2.DecisionStatus.Name(result.status)
        failures += result.status != llm_pb2.DECISION_STATUS_OK
        tokens = response.usage.input_tokens if response.usage.known else "unknown"
        print(f"{text:<16} {status:<34} reason={result.reason_code or '-':<16} "
              f"p_action={p_true if p_true is None else round(p_true, 3)} latency_ms={response.latency_ms} "
              f"input_tokens={tokens} model={response.model_used or '-'}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-ms", type=int, default=10000)
    args = parser.parse_args()
    return asyncio.run(run(args.budget_ms))


if __name__ == "__main__":
    raise SystemExit(main())
