"""Nightly 真实 LLM 语料回归（P2 / spec 7.3 跨 Agent 组合 + 7.4 多轮指代）。

复杂多意图、跨 Agent 组合、多轮指代依赖真实 LLM 规划，mock 跑没意义，因此默认 skip：
需要宿主 export LLM_API_KEY（作为 nightly 开关）且全栈在跑（make up）。不进普通 PR 门禁。

运行：
  make up                          # 起全栈（容器内已配 LLM_API_KEY）
  export LLM_API_KEY=...           # 宿主侧同样配置，作为 nightly 开关
  python -m pytest test/nightly -m nightly -v

断言复用全栈断言 runner（test/e2e_central_hub_assertions.py），只绑定必达节点 /
确定的本地状态 / 少量关键词，容忍真实 LLM 的合理波动。
"""
import asyncio
import os
import sys
import urllib.request
import uuid
from pathlib import Path

import pytest
import yaml

_CORPUS = Path(__file__).parent / "corpus_llm.yaml"
with open(_CORPUS, encoding="utf-8") as _f:
    _CASES = yaml.safe_load(_f)


def _skip_reason():
    # 先查 key（瞬时）：宿主没 key 时直接 skip，不连网络、不 import runner，
    # 这样普通全量 pytest 秒过、不被 nightly 拖慢。
    if not os.getenv("LLM_API_KEY"):
        return "nightly 需真实 LLM：宿主未设置 LLM_API_KEY"
    try:
        urllib.request.urlopen("http://localhost:8092/healthz", timeout=5)
    except Exception:
        return "全栈不可达：先 make up"
    return None


_REASON = _skip_reason()
pytestmark = [
    pytest.mark.nightly,
    pytest.mark.skipif(_REASON is not None, reason=_REASON or ""),
]


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["name"])
def test_llm_corpus(case):
    asyncio.run(_run_case(case))


async def _run_case(case):
    # 仅在真正运行时才引入 runner（依赖 websockets），避免影响普通收集。
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from e2e_central_hub_assertions import (
        _assert_turn,
        _get,
        _post_debug,
        _send,
        _trace_id,
        _wait_trace,
    )

    session_id = f"nightly-{case['name']}-{uuid.uuid4().hex[:6]}"
    for key, value in case.get("setup", {}).items():
        _post_debug(key, value)
    for turn in case["turns"]:
        trace_id = _trace_id()
        before = _get("/api/vehicle/state")
        finals = await _send(
            turn["text"],
            session_id,
            trace_id,
            is_confirmation=turn.get("is_confirmation", False),
        )
        spans = _wait_trace(trace_id, turn.get("expect_spans", []))
        after = _get("/api/vehicle/state")
        _assert_turn(case["name"], turn, before, after, spans, finals, trace_id)
