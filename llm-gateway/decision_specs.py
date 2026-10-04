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

SPECS = {spec.key(): spec for spec in (SMOKE,)}
