"""执行性声称闸：**切包不变**，发布即权威（评审三轮 R3-04，2026-09-23）。

二轮闸的有界释放是「缓冲过 160 字且到目前为止不像声称 ⇒ 整段放」：160 字无标点前缀 +「已」一包、「为您关闭车窗。」
一包 ⇒ 两包都放了，合起来正是「已为您关闭车窗」；同一文本一次喂入却被拦，final 按整句剥又是第三种结果。
三轮规则只由文本决定（见 `runtime.execution_claim.ExecutionClaimGate`）：同一文本任意切包，放出的逐字相同；
`strip_execution_claims` 就是一次喂入；D0 / T2 的 final 取闸实际放出的文本。
"""
from __future__ import annotations

import random
import re

import pytest

from runtime import execution_claim as ec
from runtime.execution_claim import (
    CLAIM_SPAN_MAX, CLAIM_STRIPPED_SPEECH, ExecutionClaimGate, strip_execution_claims,
)

from .test_engine_stream import _Resp, _StreamSpy, _chitchat_agent, _make_engine, _req, _run

_CORPUS = [
    "已为您关闭车窗。建议靠边停车检查。",
    "这" * 160 + "已为您关闭车窗。",                                     # 评审反例原文
    "好的，" + "这" * 170 + "已为您关闭车窗。然后注意安全。",
    "这" * 200 + "，正在为您规划路线，" + "那" * 30 + "。后面一句。",
    "先停车，再检查机油液位。然后",
    "为您" + "调" * 5 + "好了，" + "这" * 170 + "马上为您安排。",
    "已 经  为 您 关 闭 了 车 窗。",
    "这" * 150 + "为您调整好了。" + "那" * 20,
    "路线已经算好了吗？为您找到10家川菜。",
    "第一行建议。\n正在为您安排。\n第三行建议。",
    "甲" * 400 + "已为您" + "乙" * 100 + "，丙。",
    "这" * 158 + "，已经为您\n" + "那" * 40,
    "这" * 170 + "为您：好了，" + "那" * 20 + "。",
]


def _one_shot(text: str) -> tuple[str, int]:
    gate = ExecutionClaimGate()
    out = gate.feed(text) + gate.flush()
    assert out == gate.released
    return out, gate.removed


def _chunked(text: str, rng: random.Random) -> tuple[str, int]:
    gate = ExecutionClaimGate()
    out, i = [], 0
    while i < len(text):
        step = rng.randint(1, 24)
        out.append(gate.feed(text[i:i + step]))
        i += step
    out.append(gate.flush())
    assert "".join(out) == gate.released
    return "".join(out), gate.removed


def test_any_chunking_releases_exactly_what_one_shot_releases():
    """≥ 500 次随机切包：放出的文本与删掉的处数都与一次喂入逐字相同，且放出的文本里没有声称。"""
    rng = random.Random(20260923)
    runs = 0
    for text in _CORPUS:
        expected, removed = _one_shot(text)
        assert not ec._CLAIM_RE.search(expected), (text[:20], expected[-40:])
        for _ in range(45):
            got, got_removed = _chunked(text, rng)
            assert (got, got_removed) == (expected, removed), text[:24]
            runs += 1
    assert runs >= 500


def test_the_review_counterexample_is_held_whatever_the_split():
    prefix = "这" * 160
    gate = ExecutionClaimGate()
    streamed = gate.feed(prefix + "已") + gate.feed("为您关闭车窗。") + gate.flush()
    assert "已为您" not in streamed
    assert gate.removed == 1
    assert streamed == _one_shot(prefix + "已为您关闭车窗。")[0]


def test_strip_is_the_gate_fed_at_once():
    for text in _CORPUS:
        expected, removed = _one_shot(text.strip())
        cleaned, stripped = strip_execution_claims(text)
        assert stripped == removed
        if removed:
            assert cleaned == re.sub(r"\n{2,}", "\n", expected.strip()).strip()
        else:
            assert cleaned == text.strip()


def test_the_holdback_covers_the_widest_claim_the_regexes_can_match():
    """保留区至少要装得下任何一处声称的全部非空白字——从三条正则本身推（改了正则忘了改常量会红）。"""
    import re._parser as sre_parse
    widest = 0
    for rx in (ec._DONE_RE, ec._ONGOING_RE):
        pattern = rx.pattern.replace(r"\s*", "")
        _low, high = sre_parse.parse(pattern).getwidth()
        widest = max(widest, high)
    assert widest > 0 and CLAIM_SPAN_MAX >= widest


def test_an_overlong_clean_sentence_is_released_in_bounded_segments():
    text = "这是一段很长很长没有标点的普通解释文字一直说下去" * 3
    gate = ExecutionClaimGate(limit=20)
    first = gate.feed(text)
    assert first and len(text) - len(first) <= CLAIM_SPAN_MAX     # 只留保留区
    assert first + gate.flush() == text


def test_the_first_release_wait_is_measured():
    ticks = iter([10.0, 10.25, 11.0])
    gate = ExecutionClaimGate(clock=lambda: next(ticks))
    assert gate.first_hold_ms is None
    gate.feed("好的")                   # 进门：10.0
    gate.feed("，马上到。")              # 放行：10.25
    assert gate.first_hold_ms == pytest.approx(250.0)


# ── D0：final 取闸实际放出的文本 ─────────────────────────────────────────────

def _d0(script):
    spy = _StreamSpy(script=script, agent=_chitchat_agent())
    engine, _ = _make_engine(spy)

    async def compose(text, results, **kwargs):
        return {"speech": results[0].speech, "actions": [], "cards": []}
    engine.aggregator.compose = compose
    return _run(engine, _req("红色机油灯亮了怎么办"))


def test_d0_the_review_split_never_reaches_the_client_and_the_final_is_what_was_streamed():
    prefix = "这" * 160
    events = _d0([
        ("speech", prefix + "已"), ("speech", "为您关闭车窗。"),
        ("speech", "建议靠边停车检查。"),
        ("final", _Resp(status=0, speech=prefix + "已为您关闭车窗。建议靠边停车检查。")),
    ])
    streamed = "".join(e["delta"] for e in events if e["kind"] == "speech")
    assert "已为您" not in streamed
    assert streamed.endswith("建议靠边停车检查。")
    assert events[-1]["speech"] == streamed


def test_d0_everything_held_ends_with_the_shared_honest_speech():
    events = _d0([("speech", "已为您"), ("speech", "关闭车窗。"),
                  ("final", _Resp(status=0, speech="已为您关闭车窗。"))])
    assert events[-1]["speech"] == CLAIM_STRIPPED_SPEECH


# ── T2：同一条出口，结果里带的是闸放出的文本 ────────────────────────────────────

def test_t2_the_review_split_is_held_and_the_result_carries_what_was_released():
    from orchestrator.cloud.loop import THINKING_FILLER, LoopController
    from orchestrator.cloud.models import Plan, PlanContext, ReplanDecision, Step

    from .test_loop import _Executor, _Planner, _collect

    captured: dict = {}

    class _Aggregator:
        async def compose(self, text, results, **kwargs):
            captured["speech"] = results[-1].speech
            return {"speech": results[-1].speech, "actions": [], "cards": []}

    prefix = "这" * 160

    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30):
        yield ("speech", prefix + "已")
        yield ("speech", "为您避开此路段。")
        yield ("speech", "前方畅通。")
        from cockpit.agent.v1 import agent_pb2
        yield ("final", agent_pb2.ExecuteResponse(
            status=0, speech=prefix + "已为您避开此路段。前方畅通。"))

    controller = LoopController(_Planner([ReplanDecision(done=True)]), _Executor({}), _Aggregator(),
                                None, max_iters=2, budget_ms=5000, stream_fn=stream_fn)
    events = _collect(
        controller, goal="路况", agents=[], ctx=PlanContext(), user_text="路况",
        initial_plan=Plan(steps=[Step(id="s1", agent_id="chitchat", kind="agent",
                                      deployment="cloud", intent="chitchat.talk",
                                      latency_budget_ms=5000, response_only=True)],
                          complexity="adaptive"))
    streamed = "".join(e.get("delta", "") for e in events if e.get("kind") == "speech")
    streamed = streamed.replace(THINKING_FILLER, "", 1)
    assert "已为您" not in streamed and streamed.endswith("前方畅通。")
    assert captured["speech"] == streamed


# ── 发布即权威：Agent 的 final 全文与流出的增量不一致时，final 取流出去的那份 ───────────

def test_d0_final_is_what_was_released_even_when_the_agent_final_text_differs():
    """两条路径逐字一致靠两件事：切包不变 + final 取闸放出的文本。前者只保证「同一段文字同样切」；Agent 的 final 全文
    本身就与增量不同时（改写 / 补了一句），只有后者能保证用户听到的 = 屏幕 final = 落库的。"""
    events = _d0([
        ("speech", "好的，已为您关闭车窗。"), ("speech", "建议停车。"),
        ("final", _Resp(status=0, speech="好的，已为您关闭车窗。建议立即停车检查。")),
    ])
    streamed = "".join(e["delta"] for e in events if e["kind"] == "speech")
    assert streamed == "建议停车。"
    assert events[-1]["speech"] == streamed


def test_t2_final_is_what_was_released_even_when_the_agent_final_text_differs():
    from orchestrator.cloud.loop import LoopController
    from orchestrator.cloud.models import Plan, PlanContext, ReplanDecision, Step

    from .test_loop import _Executor, _Planner, _collect

    captured: dict = {}

    class _Aggregator:
        async def compose(self, text, results, **kwargs):
            captured["speech"] = results[-1].speech
            return {"speech": results[-1].speech, "actions": [], "cards": []}

    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30):
        yield ("speech", "已为您避开此路段。")
        yield ("speech", "前方畅通。")
        from cockpit.agent.v1 import agent_pb2
        yield ("final", agent_pb2.ExecuteResponse(status=0, speech="已为您避开此路段。前方一路畅通。"))

    controller = LoopController(_Planner([ReplanDecision(done=True)]), _Executor({}), _Aggregator(),
                                None, max_iters=2, budget_ms=5000, stream_fn=stream_fn)
    _collect(controller, goal="路况", agents=[], ctx=PlanContext(), user_text="路况",
             initial_plan=Plan(steps=[Step(id="s1", agent_id="chitchat", kind="agent",
                                           deployment="cloud", intent="chitchat.talk",
                                           latency_budget_ms=5000, response_only=True)],
                               complexity="adaptive"))
    assert captured["speech"] == "前方畅通。"


def test_d0_stream_span_carries_the_gate_wait(monkeypatch):
    from observability import events as obs_events

    spans = []

    class _Emitter:
        async def emit_span(self, trace_id, node, **kwargs):
            spans.append((node, kwargs.get("attrs") or {}))

        async def emit_metric(self, *args, **kwargs):
            return None

    monkeypatch.setattr(obs_events, "get_emitter", lambda service="cloud": _Emitter(), raising=False)
    _d0([("speech", "先停车，"), ("speech", "再检查。"), ("final", _Resp(status=0, speech="先停车，再检查。"))])
    agent_spans = [attrs for node, attrs in spans if node.startswith("step.agent:")]
    assert agent_spans and "claim_gate_hold_ms" in agent_spans[-1]
    assert agent_spans[-1]["claim_gate_removed"] == 0


def test_an_overlong_claim_is_dropped_up_to_its_clause_end_not_just_the_marker():
    """只剥掉「已为您」会留下「关闭车窗」——话还是那句话；丢到所在分句结束，后面的分句照放。"""
    text = "这" * 170 + "已为您关闭车窗，建议停车检查。"
    out, removed = _one_shot(text)
    assert removed == 1
    assert "关闭车窗" not in out
    assert out.endswith("建议停车检查。")
