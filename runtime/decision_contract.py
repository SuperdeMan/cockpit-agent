"""Jev 判别（Decide）的纯契约：任务规格、payload 校验、供应商答案校验。无网络、无领域词。

设计：docs/design/2026-10-04-jev-decide-integration.md（研究方案 docs/research/2026-09-25-cockpit-agent-jev-integration-plan.md §3）。
网关（llm-gateway）是唯一外呼方；调用方只能点名 allowlist 里的任务并给出合 schema 的 payload，问法由网关按规格渲染。
答案按整批校验：模型 pin、问题 ID 集、类型、数值有限且在范围内、分布容差、Choice 是最大概率项——任一不合格整批不采纳，
不让「只有打到分的那部分」获得优势。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

QUESTION_TYPES = ("noul", "choice", "score")
DISTRIBUTION_TOLERANCE = 0.02      # 概率分布求和与 1 的容差
MAX_TEXT_CHARS = 2000               # 单个文本字段上限（远低于供应商上限，工作预算）
MAX_LIST_ITEMS = 32

# 状态短码（与 proto DecisionStatus 一一对应；网关负责映射到枚举）
OK = "ok"
ABSTAIN = "abstain"
DISABLED = "disabled"
UNAVAILABLE = "unavailable"
TIMEOUT = "timeout"
INVALID_RESPONSE = "invalid_response"
INVALID_REQUEST = "invalid_request"
PRIVACY_FILTERED = "privacy_filtered"


@dataclass(frozen=True)
class QuestionSpec:
    """一个问题的固定问法。instructions 必须明确指向 state 里的对象与判据（问题 ID 模型看不到）。"""
    id: str
    type: str                        # noul | choice | score
    instructions: str
    criteria: object = None          # noul: {"true","false"}（可选）；choice: {选项: 描述}；score: [等级描述, …]

    def levels(self) -> int:
        return len(self.criteria) if self.type == "score" and isinstance(self.criteria, (list, tuple)) else 0


@dataclass(frozen=True)
class TaskSpec:
    """一个判别任务的版本化规格。payload_fields: 字段名 → "str" | "list[str]"。"""
    task_id: str
    rubric_version: str
    payload_fields: dict = field(default_factory=dict)
    required: frozenset = frozenset()
    questions: tuple = ()
    third_party_ok: bool = True      # False ⇒ 这类数据不发供应商（PRIVACY_FILTERED）

    def key(self) -> tuple[str, str]:
        return self.task_id, self.rubric_version


def validate_spec(spec: TaskSpec) -> list[str]:
    """规格自检（网关启动时跑）：问题 ID 唯一、类型合法、choice / score 的判据形状对。"""
    errors = []
    ids = [q.id for q in spec.questions]
    if not spec.task_id or not spec.rubric_version or not ids:
        errors.append("empty_spec")
    if len(set(ids)) != len(ids):
        errors.append("duplicate_question_id")
    for q in spec.questions:
        if q.type not in QUESTION_TYPES or not q.instructions.strip():
            errors.append(f"bad_question:{q.id}")
        elif q.type == "choice" and not (isinstance(q.criteria, dict) and 2 <= len(q.criteria) <= 255):
            errors.append(f"bad_choice_criteria:{q.id}")
        elif q.type == "score" and not (isinstance(q.criteria, (list, tuple)) and 2 <= len(q.criteria) <= 10):
            errors.append(f"bad_score_criteria:{q.id}")
        elif q.type == "noul" and q.criteria is not None and set(q.criteria) - {"true", "false"}:
            errors.append(f"bad_noul_criteria:{q.id}")
    for name in spec.required:
        if name not in spec.payload_fields:
            errors.append(f"required_not_declared:{name}")
    return errors


def validate_payload(spec: TaskSpec, payload: dict) -> list[str]:
    """payload 按规格校验：未声明字段、缺必填、类型不对、超长一律拒绝（返回错误码列表，空 = 合格）。"""
    if not isinstance(payload, dict):
        return ["payload_not_object"]
    errors = [f"unknown_field:{k}" for k in sorted(set(payload) - set(spec.payload_fields))]
    errors += [f"missing_field:{k}" for k in sorted(spec.required - set(payload))]
    for name, kind in spec.payload_fields.items():
        if name not in payload:
            continue
        value = payload[name]
        if kind == "str":
            if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS:
                errors.append(f"bad_field:{name}")
        elif kind == "list[str]":
            if (not isinstance(value, list) or len(value) > MAX_LIST_ITEMS
                    or not all(isinstance(v, str) and len(v) <= MAX_TEXT_CHARS for v in value)):
                errors.append(f"bad_field:{name}")
        else:
            errors.append(f"undeclared_kind:{name}")
    return errors


def vendor_questions(spec: TaskSpec) -> dict:
    """规格 → 供应商请求的 questions（问题 ID → {type, instructions, criteria}）。"""
    out = {}
    for q in spec.questions:
        item = {"type": q.type, "instructions": q.instructions}
        if q.criteria is not None:
            item["criteria"] = list(q.criteria) if q.type == "score" else dict(q.criteria)
        out[q.id] = item
    return out


def _unit(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0.0 <= x <= 1.0


def _distribution(probs, allowed: set) -> bool:
    if not isinstance(probs, dict) or not probs or set(probs) - allowed:
        return False
    if not all(_unit(v) for v in probs.values()):
        return False
    return abs(sum(probs.values()) - 1.0) <= DISTRIBUTION_TOLERANCE


def validate_answers(spec: TaskSpec, answers, *, model_requested: str, model_used: str) -> tuple[str, dict, str]:
    """供应商答案整批校验 ⇒ (状态, 问题 ID → 规范化答案, 原因码)。任一项不合格 ⇒ (INVALID_RESPONSE, {}, 原因)。

    规范化答案：noul → {"type": "noul", "p_true"}；choice → {"type", "choice", "probabilities", "confidence"}；
    score → {"type", "score", "probabilities", "confidence"}。
    """
    if model_requested and model_used != model_requested:
        return INVALID_RESPONSE, {}, "model_drift"
    if not isinstance(answers, dict):
        return INVALID_RESPONSE, {}, "answers_not_object"
    expected = {q.id: q for q in spec.questions}
    if set(answers) != set(expected):
        return INVALID_RESPONSE, {}, "question_ids_mismatch"
    out = {}
    for qid, q in expected.items():
        a = answers[qid]
        if not isinstance(a, dict) or a.get("type", q.type) != q.type:
            return INVALID_RESPONSE, {}, f"type_mismatch:{qid}"
        if q.type == "noul":
            if not _unit(a.get("noul")):
                return INVALID_RESPONSE, {}, f"bad_noul:{qid}"
            out[qid] = {"type": "noul", "p_true": float(a["noul"])}
        elif q.type == "choice":
            options = set(q.criteria)
            probs, choice = a.get("probabilities"), a.get("choice")
            if choice not in options or not _distribution(probs, options) or not _unit(a.get("confidence")):
                return INVALID_RESPONSE, {}, f"bad_choice:{qid}"
            if probs.get(choice, 0.0) + 1e-9 < max(probs.values()):
                return INVALID_RESPONSE, {}, f"choice_not_argmax:{qid}"
            out[qid] = {"type": "choice", "choice": choice, "probabilities": {k: float(v) for k, v in probs.items()},
                        "confidence": float(a["confidence"])}
        else:
            probs = a.get("probabilities")
            levels = {str(i) for i in range(q.levels())}
            levels_from_one = {str(i) for i in range(1, q.levels() + 1)}
            allowed = levels if isinstance(probs, dict) and set(probs) <= levels else levels_from_one
            score = a.get("score")
            low, high = min(int(k) for k in allowed), max(int(k) for k in allowed)
            if (not _distribution(probs, allowed) or not _unit(a.get("confidence"))
                    or not isinstance(score, (int, float)) or isinstance(score, bool)
                    or not math.isfinite(score) or not low <= score <= high):
                return INVALID_RESPONSE, {}, f"bad_score:{qid}"
            out[qid] = {"type": "score", "score": float(score), "probabilities": {k: float(v) for k, v in probs.items()},
                        "confidence": float(a["confidence"])}
    return OK, out, ""
