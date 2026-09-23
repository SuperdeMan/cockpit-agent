"""执行性声称在**首次对外释放之前**就拦（评审二轮 R4，2026-09-22）。

W14 只在 final 上剥谈话步的「已为您…」；而 D0 / T2 的流式直通把 speech delta 直接 yield——
「已为您关闭」「车窗。」两个增量先到了屏幕 / TTS，final 才把同一句剥掉。终态正确、用户已经听到了假话。
判据仍是 `runtime.execution_claim` 那一份（按句、零领域词），只是多了一个**句级有界缓冲**
（`ExecutionClaimGate`）挂在 response_only 步的流式出口上；E 路径逐步 speech 也走同一条。
"""
from __future__ import annotations

import asyncio

from runtime.execution_claim import ExecutionClaimGate

from orchestrator.cloud.loop import LoopController
from orchestrator.cloud.models import Plan, PlanContext, ReplanDecision, Step

from .test_engine_stream import _Resp, _StreamSpy, _chitchat_agent, _make_engine, _req, _run
from .test_loop import _Aggregator, _Executor, _Planner, _collect


# ── 闸本身 ────────────────────────────────────────────────────────────────

def test_gate_holds_a_sentence_until_its_boundary_and_drops_claims():
    gate = ExecutionClaimGate()
    assert gate.feed("已为您关闭") == ""            # 句子没结束：一个字都不放
    assert gate.feed("车窗。") == ""                 # 结束了、是声称：整句丢
    assert gate.feed("建议") == ""
    assert gate.feed("靠边停车检查。") == "建议靠边停车检查。"
    assert gate.flush() == ""
    assert gate.removed == 1


def test_gate_releases_ordinary_sentences_and_a_clean_tail_on_flush():
    gate = ExecutionClaimGate()
    assert gate.feed("先停车，") == ""
    assert gate.feed("再检查机油液位。然后") == "先停车，再检查机油液位。"
    assert gate.flush() == "然后"
    assert gate.removed == 0


def test_gate_drops_a_claim_that_never_got_its_punctuation():
    gate = ExecutionClaimGate()
    assert gate.feed("正在为您重新规划路线") == ""
    assert gate.flush() == ""
    assert gate.removed == 1


def test_gate_releases_an_overlong_clean_sentence_without_waiting():
    """有界：一句话超过上限还没标点 ⇒ 不是声称就放（不让正常长句卡在缓冲里）。

    ⚠ 评审三轮 R3-04 改写：二轮版在这里**整段**放行、flush 为空——正是它让「160 字前缀 +『已』」一包放行、
    「为您关闭车窗。」下一包也放行。三轮起超长句只放保留区之前的前缀，末尾 `CLAIM_SPAN_MAX` 个非空白字等后文或流末。"""
    from runtime.execution_claim import CLAIM_SPAN_MAX
    text = "这是一段很长很长没有标点的普通解释文字一直说下去"
    gate = ExecutionClaimGate(limit=20)
    out = gate.feed(text)
    assert out.startswith("这是一段")
    assert len(text) - len(out) <= CLAIM_SPAN_MAX
    assert out + gate.flush() == text


def test_gate_keeps_holding_an_overlong_claim():
    gate = ExecutionClaimGate(limit=10)
    assert gate.feed("已经为您把车窗关好了并且顺手把空调也调低了一些") == ""
    assert gate.flush() == "" and gate.removed == 1


# ── D0：response_only 步的流式出口 ──────────────────────────────────────────

def _d0_events(script):
    spy = _StreamSpy(script=script, agent=_chitchat_agent())
    engine, _ = _make_engine(spy)

    async def compose(text, results, **kwargs):
        return {"speech": results[0].speech, "actions": [], "cards": []}
    engine.aggregator.compose = compose
    return _run(engine, _req("红色机油灯亮了怎么办"))


def test_d0_stream_never_releases_a_claim_sentence_before_the_final():
    events = _d0_events([
        ("speech", "已为您关闭"), ("speech", "车窗。"),
        ("speech", "建议靠边"), ("speech", "停车检查。"),
        ("final", _Resp(status=0, speech="已为您关闭车窗。建议靠边停车检查。")),
    ])
    streamed = "".join(e["delta"] for e in events if e["kind"] == "speech")
    assert "已为您" not in streamed
    assert streamed == "建议靠边停车检查。"
    final = events[-1]
    assert "已为您" not in final["speech"]
    assert final["speech"] == streamed                     # 流出的 = 落库的 = final 的


def test_d0_stream_with_only_claims_ends_with_the_honest_final():
    events = _d0_events([
        ("speech", "已为您"), ("speech", "关闭车窗。"),
        ("final", _Resp(status=0, speech="已为您关闭车窗。")),
    ])
    assert [e for e in events if e["kind"] == "speech"] == []
    assert "没有执行任何操作" in events[-1]["speech"]


def test_d0_stream_of_a_non_talk_step_is_not_gated():
    """只有按声明不可能执行的步才拦；信息类能力的「已为您规划 3 天行程」是真的。"""
    spy = _StreamSpy(script=[
        ("speech", "已为您规划"), ("speech", "3 天行程。"),
        ("final", _Resp(status=0, speech="已为您规划3 天行程。")),
    ])
    engine, _ = _make_engine(spy)
    events = _run(engine, _req("规划三天行程"))
    streamed = "".join(e["delta"] for e in events if e["kind"] == "speech")
    assert streamed == "已为您规划3 天行程。"


# ── T2：同一条出口 ───────────────────────────────────────────────────────────

def test_t2_stream_gates_a_response_only_step_the_same_way():
    planner = _Planner([ReplanDecision(done=True)])
    executor = _Executor({})
    aggregator = _Aggregator()

    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=30):
        yield ("speech", "已为您避开")
        yield ("speech", "此路段。")
        yield ("speech", "前方畅通。")
        from cockpit.agent.v1 import agent_pb2
        yield ("final", agent_pb2.ExecuteResponse(status=0, speech="已为您避开此路段。前方畅通。"))

    controller = LoopController(planner, executor, aggregator, None,
                                max_iters=2, budget_ms=5000, stream_fn=stream_fn)
    events = _collect(
        controller, goal="路况", agents=[], ctx=PlanContext(), user_text="路况",
        initial_plan=Plan(steps=[Step(id="s1", agent_id="chitchat", kind="agent",
                                      deployment="cloud", intent="chitchat.talk",
                                      latency_budget_ms=5000, response_only=True)],
                          complexity="adaptive"))
    streamed = "".join(e.get("delta", "") for e in events if e.get("kind") == "speech")
    assert "已为您" not in streamed
    assert "前方畅通。" in streamed


# ── E 路径（流式不可用 ⇒ unary 回退）：逐步播报同样在释放前过闸 ─────────────────

def test_unary_fallback_step_speech_is_stripped_before_release():
    spy = _StreamSpy(stream_error=True, agent=_chitchat_agent())

    async def call_agent(endpoint, intent, slots, ctx=None, meta=None):
        spy.unary_calls.append((intent, dict(meta or {})))
        return _Resp(speech="已为您关闭车窗。建议靠边停车检查。")
    spy.call_agent = call_agent
    engine, _ = _make_engine(spy)
    engine.executor._dispatcher._call = call_agent
    events = _run(engine, _req("红色机油灯亮了怎么办"))
    streamed = "".join(e["delta"] for e in events if e["kind"] == "speech")
    assert spy.unary_calls, "应当回退到了 unary"
    assert "已为您" not in streamed and "建议靠边停车检查" in streamed
    assert "已为您" not in events[-1]["speech"]
