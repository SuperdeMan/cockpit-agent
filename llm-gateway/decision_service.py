"""Jev 判别服务：allowlist、payload 校验、预算截断、供应商外呼、整批答案校验、用量记账。

全局关闭（缺省）时零外呼，所有任务回 disabled；任务不在 allowlist / payload 不合 schema 回 invalid_request；
凭证缺失、鉴权、限流、过载、网络回 unavailable；超时回 timeout；答案不合格整批回 invalid_response。
只返回建议，不记录原文：日志只有请求号、任务、状态、时延与 token 数。
设计：docs/design/2026-10-04-jev-decide-integration.md。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from google.protobuf.json_format import MessageToDict
from cockpit.llm.v1 import llm_pb2

from runtime import decision_contract as dc
from decision_provider import DecisionProviderError, TypeSafeDecisionProvider
from decision_specs import SPECS

logger = logging.getLogger("llm.decision")

DEFAULT_MODEL = "jev-1.13.0"            # 固定版本；`jev-latest` 是会移动的别名，不用
DEFAULT_MAX_BUDGET_MS = 3000           # 服务端上限；shadow 用满，在线建议（JV04 起）各自传更小的预算

_STATUS = {
    dc.OK: llm_pb2.DECISION_STATUS_OK,
    dc.ABSTAIN: llm_pb2.DECISION_STATUS_ABSTAIN,
    dc.DISABLED: llm_pb2.DECISION_STATUS_DISABLED,
    dc.UNAVAILABLE: llm_pb2.DECISION_STATUS_UNAVAILABLE,
    dc.TIMEOUT: llm_pb2.DECISION_STATUS_TIMEOUT,
    dc.INVALID_RESPONSE: llm_pb2.DECISION_STATUS_INVALID_RESPONSE,
    dc.INVALID_REQUEST: llm_pb2.DECISION_STATUS_INVALID_REQUEST,
    dc.PRIVACY_FILTERED: llm_pb2.DECISION_STATUS_PRIVACY_FILTERED,
}


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


class DecisionService:
    def __init__(self, provider, *, enabled: bool = False, model: str = DEFAULT_MODEL, tasks=(),
                 max_budget_ms: int = DEFAULT_MAX_BUDGET_MS, specs=None, clock=time.monotonic):
        self.provider = provider
        self.enabled = bool(enabled)
        self.model = model
        self.tasks = frozenset(tasks)
        self.max_budget_ms = max(1, int(max_budget_ms))
        self.specs = dict(SPECS if specs is None else specs)
        self._clock = clock
        bad = {key: errs for key, spec in self.specs.items() if (errs := dc.validate_spec(spec))}
        if bad:
            raise ValueError(f"invalid decision specs: {bad}")

    @classmethod
    def from_env(cls) -> "DecisionService":
        """服务端受控配置：客户端 meta 无权开启。`DECISION_ENABLED` 是总开关（false 时连 shadow 都不外呼）。"""
        provider = TypeSafeDecisionProvider(os.getenv("TYPESAFE_API_KEY", ""),
                                            os.getenv("DECISION_BASE_URL", "") or None)
        tasks = [t.strip() for t in os.getenv("DECISION_TASKS", "").split(",") if t.strip()]
        service = cls(provider, enabled=_truthy(os.getenv("DECISION_ENABLED", "false")),
                      model=os.getenv("DECISION_MODEL", "") or DEFAULT_MODEL, tasks=tasks,
                      max_budget_ms=int(os.getenv("DECISION_MAX_BUDGET_MS", "") or DEFAULT_MAX_BUDGET_MS))
        if service.enabled and not provider.configured:
            logger.warning("decision enabled without TYPESAFE_API_KEY: every task will be unavailable")
        return service

    def _gate(self, task) -> tuple[str, str, object, dict]:
        """(状态, 原因, 规格, payload)：状态为空表示可以外呼。"""
        if not self.enabled:
            return dc.DISABLED, "global_off", None, {}
        spec = self.specs.get((task.task_id, task.rubric_version))
        if spec is None:
            return dc.INVALID_REQUEST, "unknown_task", None, {}
        if spec.task_id not in self.tasks:
            return dc.DISABLED, "task_off", spec, {}
        if not spec.third_party_ok:
            return dc.PRIVACY_FILTERED, "data_policy", spec, {}
        payload = MessageToDict(task.payload) if task.HasField("payload") else {}
        errors = dc.validate_payload(spec, payload)
        if errors:
            return dc.INVALID_REQUEST, errors[0], spec, {}
        return "", "", spec, payload

    async def _call(self, spec, payload: dict, timeout_s: float) -> tuple[str, dict, str, dict | None, str]:
        """(状态, 答案, 原因, 用量, model_used)。"""
        try:
            raw = await self.provider.decide(model=self.model, state=payload,
                                             questions=dc.vendor_questions(spec), timeout_s=timeout_s)
        except DecisionProviderError as e:
            return e.status, {}, e.reason, None, ""
        model_used = str(raw.get("model") or "")
        status, answers, reason = dc.validate_answers(spec, raw.get("answers"),
                                                      model_requested=self.model, model_used=model_used)
        usage = raw.get("usage")
        return status, answers, reason, usage if isinstance(usage, dict) else None, model_used

    async def decide(self, request) -> llm_pb2.DecideResponse:
        started = self._clock()
        budget_ms = min(request.budget_ms or self.max_budget_ms, self.max_budget_ms)
        timeout_s = budget_ms / 1000.0
        response = llm_pb2.DecideResponse(request_id=request.request_id,
                                          state_fingerprint=request.binding.state_fingerprint,
                                          model_requested=self.model)
        results: list[llm_pb2.DecisionTaskResult] = []
        pending: list[tuple[int, object, dict]] = []
        for index, task in enumerate(request.tasks):
            status, reason, spec, payload = self._gate(task)
            results.append(llm_pb2.DecisionTaskResult(task_id=task.task_id, rubric_version=task.rubric_version,
                                                      status=_STATUS[status or dc.OK], reason_code=reason))
            if not status:
                pending.append((index, spec, payload))

        usage_known, tokens_in, tokens_out, models = bool(pending), 0, 0, set()
        if pending:
            try:
                outcomes = await asyncio.wait_for(
                    asyncio.gather(*(self._call(spec, payload, timeout_s) for _, spec, payload in pending)),
                    timeout=timeout_s + 0.05)
            except asyncio.TimeoutError:
                outcomes = [(dc.TIMEOUT, {}, "budget", None, "")] * len(pending)
            for (index, spec, _), (status, answers, reason, usage, model_used) in zip(pending, outcomes):
                result = results[index]
                result.status = _STATUS[status]
                result.reason_code = reason
                if status == dc.OK:
                    for qid, answer in answers.items():
                        result.answers.append(_answer_proto(qid, answer))
                if model_used:
                    models.add(model_used)
                if (usage and isinstance(usage.get("input_tokens"), int)
                        and isinstance(usage.get("output_tokens"), int)):
                    tokens_in += usage["input_tokens"]
                    tokens_out += usage["output_tokens"]
                else:
                    usage_known = False      # 有一个请求发出去却没拿到用量 ⇒ 总量不可知，不用 0 冒充
        response.results.extend(results)
        response.model_used = ",".join(sorted(models))
        response.usage.known = usage_known
        response.usage.input_tokens = tokens_in
        response.usage.output_tokens = tokens_out
        response.latency_ms = int((self._clock() - started) * 1000)
        logger.info("decision request=%s tasks=%s statuses=%s latency_ms=%d input_tokens=%s",
                    request.request_id[:24], [t.task_id for t in request.tasks],
                    [llm_pb2.DecisionStatus.Name(r.status) for r in results], response.latency_ms,
                    tokens_in if usage_known else "unknown")
        return response


def _answer_proto(question_id: str, answer: dict) -> llm_pb2.DecisionAnswer:
    out = llm_pb2.DecisionAnswer(question_id=question_id)
    if answer["type"] == "noul":
        out.noul.p_true = answer["p_true"]
    elif answer["type"] == "choice":
        out.choice.choice = answer["choice"]
        out.choice.probabilities.update(answer["probabilities"])
        out.choice.confidence = answer["confidence"]
    else:
        out.score.score = answer["score"]
        out.score.probabilities.update(answer["probabilities"])
        out.score.confidence = answer["confidence"]
    return out
