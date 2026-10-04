"""Jev 判别任务规格（版本化 allowlist）。调用方只能点名这里的 (task_id, rubric_version)，问法由网关渲染。

规格是数据：不复制能力表 / 领域词表；要判别的对象由 payload 带进来（state），问题只描述判据。
JV01 只有冒烟任务（契约与真请求 smoke 用，不接业务）；actionability 等业务任务随 JV03 加入，并按设计文档做中文校准。
"""
from __future__ import annotations

from runtime.decision_contract import QuestionSpec, TaskSpec

SMOKE = TaskSpec(
    task_id="smoke",
    rubric_version="1",
    payload_fields={"text": "str"},
    required=frozenset({"text"}),
    questions=(
        QuestionSpec(
            id="asks_action",
            type="noul",
            instructions=("Judge only the wording of state.text. Does state.text ask the assistant to carry out an "
                          "action now (an instruction), rather than ask for information or how something works?"),
            criteria={"true": "state.text asks the assistant to do something now.",
                      "false": "state.text asks a question, asks for an explanation, or requests no action."},
        ),
    ),
)

# 受话判定（JV03 首个 shadow 任务）：英文问法——2026-10-04 离线中文评测里受话英文问法 AUC 1.00（47 句），
# 中文问法漏掉情绪表达（产品定义里算对助手说的）。只做 shadow 对照规划器的 `addressed`，不影响结果。
ADDRESSED = TaskSpec(
    task_id="addressed",
    rubric_version="1",
    payload_fields={"utterance": "str"},
    required=frozenset({"utterance"}),
    questions=(
        QuestionSpec(
            id="addressed",
            type="noul",
            instructions=("Judge only state.utterance, a sentence heard in a car cabin. Is it spoken to the in-car "
                          "voice assistant (a request, a question or a feeling the assistant should respond to), rather "
                          "than talk between passengers, a radio or TV broadcast, a phone call or speech addressed to "
                          "another person?"),
            criteria={"true": "The assistant is being addressed and should respond.",
                      "false": "Someone else is being addressed, or it is a broadcast, phone call or background talk."},
        ),
    ),
)

SPECS = {spec.key(): spec for spec in (SMOKE, ADDRESSED)}
