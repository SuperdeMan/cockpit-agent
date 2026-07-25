"""M4 S2S 单测（P1）——协议 / provider 映射 / L-Session 状态机 / 回灌，全部离线可跑。

重点覆盖 RFC 里「混为一谈必出双播/幽灵执行」的三层打断语义（§4.3），以及回灌的
两条易漏项：escalated 轮不重复写 memory、被打断轮只存已播增量（★1 实测）。

含源码级铁律断言（同 M2 Verifier / M3 治理器的做法）：会话层不得出现领域字面量、
单工具契约不得被偷偷扩成能力清单。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re

import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from s2s import protocol as P  # noqa: E402
from s2s.provider import (  # noqa: E402
    EV_ANSWER_DELTA, EV_AUDIO_DELTA, EV_ERROR, EV_TOOL_CALL, EV_TRANSCRIPT,
    EV_TURN_DONE, EV_TURN_STARTED, MockS2SProvider, QwenOmniRealtimeProvider,
    S2SEvent, build_s2s_provider,
)
from s2s.reflux import Reflux, build_context_summary, detect_false_promise  # noqa: E402
from s2s.session import (  # noqa: E402
    S2SSession, SessionState, T_ABANDONED, T_CANCELLING, T_ESCALATED, Turn,
)


# ─────────────────────────── protocol ───────────────────────────

def test_escalate_is_single_tool_with_utterance_slot():
    t = P.escalate_tool()
    assert t["name"] == "escalate"
    assert list(t["parameters"]["properties"]) == ["utterance"]
    assert t["parameters"]["required"] == ["utterance"]


def test_escalate_description_is_env_tunable_for_domain_rollout(monkeypatch):
    """§6.2 域灰度 = 收放 description 的边界，不是运行时白名单。"""
    base = P.escalate_tool()["description"]
    monkeypatch.setenv("S2S_ESCALATE_DESC", "只有车控才移交")
    assert P.escalate_tool()["description"] == "只有车控才移交"
    monkeypatch.delenv("S2S_ESCALATE_DESC")
    assert P.escalate_tool()["description"] == base


def test_turn_ids_are_gateway_generated_and_unique():
    ids = {P.new_turn_id() for _ in range(200)}
    assert len(ids) == 200


# ─────────────────────── provider 事件映射 ───────────────────────

def _qwen():
    return QwenOmniRealtimeProvider("k", "wss://x", "qwen3.5-omni-flash-realtime")


@pytest.mark.parametrize("raw,kind,check", [
    ({"type": "response.created", "response": {"id": "r1"}}, EV_TURN_STARTED,
     lambda e: e.response_id == "r1"),
    ({"type": "conversation.item.input_audio_transcription.delta", "delta": "今天"},
     EV_TRANSCRIPT, lambda e: e.text == "今天" and not e.final),
    ({"type": "conversation.item.input_audio_transcription.completed",
      "transcript": "今天天气"}, EV_TRANSCRIPT,
     lambda e: e.text == "今天天气" and e.final),
    ({"type": "response.audio_transcript.delta", "delta": "好的"}, EV_ANSWER_DELTA,
     lambda e: e.text == "好的"),
    ({"type": "response.function_call_arguments.done", "name": "escalate",
      "call_id": "c1", "arguments": '{"utterance":"开空调"}'}, EV_TOOL_CALL,
     lambda e: e.name == "escalate" and e.call_id == "c1"),
    ({"type": "response.done", "response": {"status": "cancelled"}}, EV_TURN_DONE,
     lambda e: e.reason == "cancelled"),
    ({"type": "error", "error": {"message": "boom"}}, EV_ERROR,
     lambda e: e.reason == "provider"),
])
def test_provider_maps_vendor_events_to_normalized(raw, kind, check):
    evs = _qwen()._map(raw)
    assert len(evs) == 1 and evs[0].kind == kind
    assert check(evs[0])


def test_provider_decodes_audio_delta_base64():
    import base64
    pcm = b"\x01\x02\x03\x04"
    evs = _qwen()._map({"type": "response.audio.delta",
                        "delta": base64.b64encode(pcm).decode()})
    assert evs[0].kind == EV_AUDIO_DELTA and evs[0].pcm == pcm


def test_provider_ignores_function_call_arguments_delta():
    """流式 args 增量不带 name → 不产归一化事件（tool_call 只在 .done 出，★2 实测）。"""
    assert _qwen()._map({"type": "response.function_call_arguments.delta",
                         "call_id": "c1", "delta": '{"utt'}) == []


def test_provider_maps_unknown_event_to_nothing():
    assert _qwen()._map({"type": "response.content_part.added"}) == []


# ─────────────────────────── 工厂 ───────────────────────────

def test_factory_rejects_models_that_silently_drop_tools(monkeypatch):
    """P0 探针 ★T：这些型号静默丢弃 tools → fail-fast，不静默降级。"""
    monkeypatch.setenv("LLM_EMBED_API_KEY", "sk-test")
    for bad in ("qwen3-omni-flash-realtime", "qwen-omni-turbo-realtime",
                "QWEN3-OMNI-REALTIME"):
        with pytest.raises(ValueError, match="tools"):
            build_s2s_provider("dashscope", bad)


def test_factory_off_and_missing_key_return_none(monkeypatch):
    assert build_s2s_provider("off") is None
    monkeypatch.delenv("S2S_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_ASR_KEY", raising=False)
    monkeypatch.delenv("LLM_EMBED_API_KEY", raising=False)
    assert build_s2s_provider("dashscope") is None


def test_factory_mock_available_without_key(monkeypatch):
    monkeypatch.delenv("LLM_EMBED_API_KEY", raising=False)
    assert isinstance(build_s2s_provider("mock"), MockS2SProvider)


def test_factory_default_model_supports_tools(monkeypatch):
    monkeypatch.setenv("LLM_EMBED_API_KEY", "sk-test")
    p = build_s2s_provider("dashscope")
    assert isinstance(p, QwenOmniRealtimeProvider)
    assert p.model == "qwen3.5-omni-flash-realtime"


def test_provider_clamps_vad_silence():
    assert _qwen().vad_silence_ms == 800
    assert QwenOmniRealtimeProvider("k", "w", "m", vad_silence_ms=99).vad_silence_ms == 300
    assert QwenOmniRealtimeProvider("k", "w", "m", vad_silence_ms=9999).vad_silence_ms == 2000


# ───────────────────── L-Session 测试脚手架 ─────────────────────

class Harness:
    """收集下行帧的会话夹具（provider 用 MockS2SProvider 手动喂事件）。"""

    def __init__(self, **kw):
        self.json_out: list[dict] = []
        self.audio_out: list[bytes] = []
        self.reflux_calls: list[Turn] = []
        self.providers: list[MockS2SProvider] = []
        self.fail_opens = 0
        self.open_summaries: list[str] = []

        def factory():
            p = MockS2SProvider()
            if self.fail_opens > 0:
                self.fail_opens -= 1

                async def boom(**_kw):
                    raise RuntimeError("open failed")
                p.open = boom
            else:
                orig = p.open

                async def rec(**kwargs):
                    self.open_summaries.append(kwargs.get("context_summary", ""))
                    await orig(**kwargs)
                p.open = rec
            self.providers.append(p)
            return p

        async def emit_json(o):
            self.json_out.append(o)

        async def emit_audio(b):
            self.audio_out.append(b)

        async def reflux(t):
            self.reflux_calls.append(t)

        kw.setdefault("reconnect_backoff", (0.0, 0.0, 0.0))
        self.sess = S2SSession(provider_factory=factory, emit_json=emit_json,
                               emit_audio=emit_audio, reflux=reflux,
                               session_id="s-test", **kw)

    @property
    def prov(self) -> MockS2SProvider:
        return self.providers[-1]

    def types(self) -> list[str]:
        return [m["type"] for m in self.json_out]

    def of(self, t: str) -> list[dict]:
        return [m for m in self.json_out if m["type"] == t]

    async def pump_once(self, *events: S2SEvent):
        """直接喂 provider 归一化事件并等事件泵消费。"""
        for e in events:
            await self.prov.push(e)
        await asyncio.sleep(0)
        for _ in range(6):
            if self.prov._q.empty():
                break
            await asyncio.sleep(0)
        await asyncio.sleep(0)


async def _normal_turn(h: Harness, answer="嗯，我在呢。"):
    await h.pump_once(
        S2SEvent(kind=EV_TURN_STARTED, response_id="r1"),
        S2SEvent(kind=EV_TRANSCRIPT, text="你好呀", final=True),
        S2SEvent(kind=EV_ANSWER_DELTA, text=answer),
        S2SEvent(kind=EV_AUDIO_DELTA, pcm=b"\x00\x01" * 80),
        S2SEvent(kind=EV_TURN_DONE, reason="completed"))


# ────────────────────── L-Session：正常轮 ──────────────────────

@pytest.mark.asyncio
async def test_normal_turn_emits_full_downstream_sequence():
    h = Harness()
    await h.sess.start()
    await _normal_turn(h)
    assert h.types() == [
        P.DOWN_SESSION_STATE, P.DOWN_TRANSCRIPT, P.DOWN_ANSWER_DELTA,
        P.DOWN_AUDIO_META, P.DOWN_TURN_END,
    ]
    assert h.of(P.DOWN_TURN_END)[0]["reason"] == P.END_COMPLETE
    assert h.audio_out == [b"\x00\x01" * 80]
    assert len(h.reflux_calls) == 1
    t = h.reflux_calls[0]
    assert t.transcript == "你好呀" and t.answer == "嗯，我在呢。"
    await h.sess.close()


@pytest.mark.asyncio
async def test_audio_meta_carries_provider_sample_rate_once():
    h = Harness()
    await h.sess.start()
    await h.pump_once(S2SEvent(kind=EV_TURN_STARTED),
                      S2SEvent(kind=EV_AUDIO_DELTA, pcm=b"aa"),
                      S2SEvent(kind=EV_AUDIO_DELTA, pcm=b"bb"))
    metas = h.of(P.DOWN_AUDIO_META)
    assert len(metas) == 1 and metas[0]["sample_rate"] == 24000
    assert h.audio_out == [b"aa", b"bb"]
    await h.sess.close()


@pytest.mark.asyncio
async def test_turn_id_is_stable_within_turn_and_new_across_turns():
    h = Harness()
    await h.sess.start()
    await _normal_turn(h)
    first = {m.get("turn_id") for m in h.json_out if "turn_id" in m}
    assert len(first) == 1
    await _normal_turn(h)
    allids = {m.get("turn_id") for m in h.json_out if "turn_id" in m}
    assert len(allids) == 2
    await h.sess.close()


@pytest.mark.asyncio
async def test_transcript_before_turn_started_still_opens_turn():
    """实测两种事件序都出现过（转写可能早于 response.created）→ 谁先来谁开 turn。"""
    h = Harness()
    await h.sess.start()
    await h.pump_once(S2SEvent(kind=EV_TRANSCRIPT, text="嗨", final=False))
    assert h.of(P.DOWN_TRANSCRIPT)[0]["turn_id"]
    await h.sess.close()


# ─────────────── L-Session：①听感打断（残包丢弃）───────────────

@pytest.mark.asyncio
async def test_barge_in_cancels_provider_and_ends_turn():
    h = Harness()
    await h.sess.start()
    await h.pump_once(S2SEvent(kind=EV_TURN_STARTED),
                      S2SEvent(kind=EV_ANSWER_DELTA, text="好的，"),
                      S2SEvent(kind=EV_AUDIO_DELTA, pcm=b"x" * 10))
    await h.sess.barge_in()
    assert h.prov.cancels == 1
    assert h.of(P.DOWN_TURN_END)[-1]["reason"] == P.END_CANCELLED
    assert h.sess.turn.status == T_CANCELLING
    await h.sess.close()


@pytest.mark.asyncio
async def test_barge_in_discards_residual_audio_and_text():
    """★1：cancel 在途时 delta 可能已在飞 → 本侧必须丢，否则打断后残包续播。"""
    h = Harness()
    await h.sess.start()
    await h.pump_once(S2SEvent(kind=EV_TURN_STARTED),
                      S2SEvent(kind=EV_ANSWER_DELTA, text="好的，"),
                      S2SEvent(kind=EV_AUDIO_DELTA, pcm=b"a" * 4))
    await h.sess.barge_in()
    n_audio, n_delta = len(h.audio_out), len(h.of(P.DOWN_ANSWER_DELTA))
    await h.pump_once(S2SEvent(kind=EV_ANSWER_DELTA, text="残余文本"),
                      S2SEvent(kind=EV_AUDIO_DELTA, pcm=b"b" * 4),
                      S2SEvent(kind=EV_TURN_DONE, reason="cancelled"))
    assert len(h.audio_out) == n_audio, "残包音频必须丢弃"
    assert len(h.of(P.DOWN_ANSWER_DELTA)) == n_delta, "残余文本必须丢弃"
    assert len(h.of(P.DOWN_TURN_END)) == 1, "turn 只收束一次（幂等）"
    await h.sess.close()


@pytest.mark.asyncio
async def test_barge_in_marks_truncated_so_reflux_stores_only_played_text():
    """被打断轮回灌的 answer 只含打断前已播增量（provider 全文≠用户听到的）。"""
    h = Harness()
    await h.sess.start()
    await h.pump_once(S2SEvent(kind=EV_TURN_STARTED),
                      S2SEvent(kind=EV_ANSWER_DELTA, text="好的，我来讲"))
    await h.sess.barge_in()
    t = h.reflux_calls[-1]
    assert t.truncated is True
    assert t.answer == "好的，我来讲"
    await h.sess.close()


@pytest.mark.asyncio
async def test_barge_in_with_no_active_turn_is_noop():
    h = Harness()
    await h.sess.start()
    await h.sess.barge_in()
    assert h.prov.cancels == 0 and not h.of(P.DOWN_TURN_END)
    await h.sess.close()


# ────────── L-Session：逃逸 + ③工具调用中打断 ──────────

@pytest.mark.asyncio
async def test_escalate_emits_escalated_then_end_and_does_not_execute():
    h = Harness()
    await h.sess.start()
    await h.pump_once(
        S2SEvent(kind=EV_TURN_STARTED),
        S2SEvent(kind=EV_TRANSCRIPT, text="把空调调到24度", final=True),
        S2SEvent(kind=EV_TOOL_CALL, name="escalate", call_id="c1",
                 args=json.dumps({"utterance": "把空调调到24度"}, ensure_ascii=False)))
    esc = h.of(P.DOWN_ESCALATED)
    assert len(esc) == 1 and esc[0]["utterance"] == "把空调调到24度"
    assert h.of(P.DOWN_TURN_END)[-1]["reason"] == P.END_ESCALATED
    assert not h.audio_out, "逃逸轮网关不产音频（执行与播报全在主链）"
    assert h.sess.turn.status == T_ESCALATED
    await h.sess.close()


@pytest.mark.asyncio
async def test_escalate_with_broken_args_falls_back_to_transcript():
    """槽位坏了也别丢这一轮——utterance 本就是「原话转述」。"""
    h = Harness()
    await h.sess.start()
    await h.pump_once(S2SEvent(kind=EV_TURN_STARTED),
                      S2SEvent(kind=EV_TRANSCRIPT, text="导航去公司", final=True),
                      S2SEvent(kind=EV_TOOL_CALL, name="escalate", call_id="c1",
                               args="{not json"))
    assert h.of(P.DOWN_ESCALATED)[0]["utterance"] == "导航去公司"
    await h.sess.close()


@pytest.mark.asyncio
async def test_unknown_tool_name_is_not_treated_as_escalate():
    """单工具契约外的调用=协议漂移，诚实忽略不臆测。"""
    h = Harness()
    await h.sess.start()
    await h.pump_once(S2SEvent(kind=EV_TURN_STARTED),
                      S2SEvent(kind=EV_TOOL_CALL, name="set_hvac", call_id="c9",
                               args='{"temp":24}'))
    assert not h.of(P.DOWN_ESCALATED)
    await h.sess.close()


@pytest.mark.asyncio
async def test_escalated_result_injects_context_without_triggering_speech():
    """§5.2/R1：回注只为上下文连续；provider 侧不 create response 故不双播。"""
    h = Harness()
    await h.sess.start()
    await h.pump_once(S2SEvent(kind=EV_TURN_STARTED),
                      S2SEvent(kind=EV_TRANSCRIPT, text="开空调", final=True),
                      S2SEvent(kind=EV_TOOL_CALL, name="escalate", call_id="c1",
                               args='{"utterance":"开空调"}'))
    tid = h.of(P.DOWN_ESCALATED)[0]["turn_id"]
    await h.sess.escalated_result(tid, "空调已调到24度")
    assert h.prov.injected == [("c1", "空调已调到24度")]
    assert not h.audio_out
    await h.sess.close()


@pytest.mark.asyncio
async def test_barge_in_during_tool_call_abandons_turn_not_rollback():
    """§4.3③ 打断≠回滚：丢的是播报权，副作用由主链确认链自己收束。"""
    h = Harness()
    await h.sess.start()
    await h.pump_once(S2SEvent(kind=EV_TURN_STARTED),
                      S2SEvent(kind=EV_TRANSCRIPT, text="订个位", final=True),
                      S2SEvent(kind=EV_TOOL_CALL, name="escalate", call_id="c1",
                               args='{"utterance":"订个位"}'))
    tid = h.of(P.DOWN_ESCALATED)[0]["turn_id"]
    await h.sess.barge_in()
    assert h.sess.turn.status == T_ABANDONED
    assert h.prov.cancels == 0, "逃逸轮无 provider 生成在飞，不该发 cancel"
    await h.sess.escalated_result(tid, "已为你订位")
    assert h.prov.injected == [], "abandoned turn 的结果不回注、不播报"
    await h.sess.close()


@pytest.mark.asyncio
async def test_escalated_result_for_unknown_turn_is_ignored():
    h = Harness()
    await h.sess.start()
    await h.sess.escalated_result("no-such-turn", "x")
    assert h.prov.injected == []
    await h.sess.close()


# ──────────────── L-Session：重连 / 降级 / 重建 ────────────────

@pytest.mark.asyncio
async def test_reconnect_reinjects_summary_and_reports_ready():
    summaries = []

    async def ctx():
        summaries.append(1)
        return "用户：你好\n你：在呢"

    h = Harness(context_provider=ctx)
    await h.sess.start()
    await h.prov.close()  # provider 事件流结束 → 触发重连
    await asyncio.sleep(0.05)
    assert h.sess.state == SessionState.READY
    assert [m["state"] for m in h.of(P.DOWN_SESSION_STATE)] == ["ready", "reconnecting", "ready"]
    assert h.open_summaries[-1] == "用户：你好\n你：在呢", "重连必须带摘要重注入"
    await h.sess.close()


@pytest.mark.asyncio
async def test_reconnect_exhausted_goes_degraded():
    h = Harness()
    await h.sess.start()
    h.fail_opens = 99
    await h.prov.close()
    await asyncio.sleep(0.05)
    assert h.sess.state == SessionState.DEGRADED
    assert h.of(P.DOWN_SESSION_STATE)[-1]["state"] == P.STATE_DEGRADED
    await h.sess.close()


@pytest.mark.asyncio
async def test_disconnect_mid_turn_ends_turn_honestly():
    """§4.5：IN_TURN 断连不假装无事——收 turn.end(cancelled, detail=disconnected)。"""
    h = Harness()
    await h.sess.start()
    await h.pump_once(S2SEvent(kind=EV_TURN_STARTED),
                      S2SEvent(kind=EV_ANSWER_DELTA, text="正说着"))
    await h.prov.close()
    await asyncio.sleep(0.05)
    end = h.of(P.DOWN_TURN_END)[-1]
    assert end["reason"] == P.END_CANCELLED and end["detail"] == "disconnected"
    assert h.reflux_calls[-1].truncated is True
    await h.sess.close()


@pytest.mark.asyncio
async def test_audio_during_reconnect_is_buffered_then_flushed():
    """§4.5 前滚缓冲：重连期音频不丢，恢复后续灌（复用 R4.3b pcmRing 思想）。"""
    h = Harness()
    await h.sess.start()
    h.fail_opens = 1
    await h.prov.close()
    await asyncio.sleep(0)
    await h.sess.push_audio(b"z" * 100)
    await asyncio.sleep(0.05)
    assert h.sess.state == SessionState.READY
    assert h.prov.sent_audio == 100, "缓冲的音频应在重连成功后续灌"
    await h.sess.close()


@pytest.mark.asyncio
async def test_ring_buffer_is_bounded():
    h = Harness(ring_max_bytes=50)
    await h.sess.start()
    h.sess.state = SessionState.RECONNECTING
    await h.sess.push_audio(b"a" * 200)
    assert len(h.sess._ring) == 50
    await h.sess.close()


@pytest.mark.asyncio
async def test_audio_dropped_when_degraded():
    h = Harness()
    await h.sess.start()
    h.sess.state = SessionState.DEGRADED
    await h.sess.push_audio(b"q" * 20)
    assert h.prov.sent_audio == 0
    await h.sess.close()


@pytest.mark.asyncio
async def test_max_turns_triggers_proactive_rebuild():
    """★3 未钉死上限 → 轮数旋钮主动重建（走同一重连路径，零新机制）。"""
    h = Harness(max_turns=2)
    await h.sess.start()
    await _normal_turn(h)
    await _normal_turn(h)
    await asyncio.sleep(0.05)
    assert len(h.providers) >= 2, "达上限应重建 provider 会话"
    assert h.sess.turns_done == 0, "重建后轮计数复位"
    await h.sess.close()


@pytest.mark.asyncio
async def test_hanging_turn_is_honestly_closed_by_watchdog():
    """真栈踩到的缺口：turn 开了却永不 done（下行背压/provider 静默）→ HMI 干等。
    任何成因都在此收口：诚实收 turn.end(error, provider_silent)，不假装还在处理。"""
    h = Harness(turn_timeout_s=0.05)
    await h.sess.start()
    await h.pump_once(S2SEvent(kind=EV_TURN_STARTED),
                      S2SEvent(kind=EV_ANSWER_DELTA, text="说了一半"))
    await asyncio.sleep(0.12)
    end = h.of(P.DOWN_TURN_END)[-1]
    assert end["reason"] == P.END_ERROR and end["detail"] == "provider_silent"
    assert h.reflux_calls[-1].truncated is True, "悬挂轮回灌须标截断"
    await h.sess.close()


@pytest.mark.asyncio
async def test_watchdog_does_not_fire_on_normal_turn():
    h = Harness(turn_timeout_s=0.2)
    await h.sess.start()
    await _normal_turn(h)
    await asyncio.sleep(0.3)
    reasons = [m["reason"] for m in h.of(P.DOWN_TURN_END)]
    assert reasons == [P.END_COMPLETE], f"正常轮不该被看门狗补收束: {reasons}"
    await h.sess.close()


@pytest.mark.asyncio
async def test_watchdog_disabled_when_timeout_zero(monkeypatch):
    monkeypatch.setenv("S2S_TURN_TIMEOUT_S", "0")
    h = Harness()
    assert h.sess.turn_timeout_s == 0
    await h.sess.start()
    await h.pump_once(S2SEvent(kind=EV_TURN_STARTED))
    await asyncio.sleep(0.05)
    assert not h.of(P.DOWN_TURN_END)
    await h.sess.close()


@pytest.mark.asyncio
async def test_context_provider_failure_does_not_block_session():
    async def boom():
        raise RuntimeError("memory down")

    h = Harness(context_provider=boom)
    await h.sess.start()  # fail-open：取不到摘要照样开会话
    assert h.sess.state == SessionState.READY
    await h.sess.close()


@pytest.mark.asyncio
async def test_reflux_failure_does_not_kill_session():
    async def boom(_t):
        raise RuntimeError("obs down")

    h = Harness()
    h.sess._reflux = boom
    await h.sess.start()
    await _normal_turn(h)
    assert h.sess.state == SessionState.READY
    await h.sess.close()


# ─────────────────────────── 回灌 ───────────────────────────

@pytest.mark.parametrize("answer,expect", [
    ("已经帮你把空调调到24度了", True),
    ("好的，导航已经开始了", True),
    ("温度调好了", True),
    ("为什么数学书很忧郁？因为它有太多问题", False),
    ("今天天气不错呀", False),
    ("好的", False),                     # 光有应答词不算承诺
    ("已经很晚了，早点休息", False),      # 有完成词无动作对象
])
def test_detect_false_promise(answer, expect):
    assert detect_false_promise(answer, escalated=False) is expect


def test_escalated_turn_is_never_false_promise():
    """移交了就不是漏移交——哪怕话术里有完成词。"""
    assert detect_false_promise("已经帮你调好空调了", escalated=True) is False


class FakeMemStub:
    def __init__(self):
        self.appended: list[tuple[str, str]] = []
        self.turns: list = []

    async def AppendTurn(self, req, timeout=None):
        self.appended.append((req.role, req.text))
        return object()

    async def GetSession(self, req, timeout=None):
        class R:
            pass
        r = R()
        r.turns = self.turns
        return r


class FakeObs:
    def __init__(self):
        self.turns: list[dict] = []
        self.spans: list[tuple[str, dict]] = []

    async def emit_turn(self, trace_id, session_id, **kw):
        self.turns.append({"session_id": session_id, **kw})

    async def emit_span(self, trace_id, node, **kw):
        self.spans.append((node, kw))


def _turn(**kw) -> Turn:
    t = Turn(turn_id=kw.pop("turn_id", "t1"))
    for k, v in kw.items():
        setattr(t, k, v)
    return t


@pytest.mark.asyncio
async def test_reflux_writes_memory_and_obs_for_normal_turn():
    mem, obs = FakeMemStub(), FakeObs()
    r = Reflux(memory_stub_getter=lambda: mem, obs=obs, gate_content=lambda s, n: s[:n],
               session_id="s1", user_id="u1", provider_name="dashscope", model="m")
    await r(_turn(transcript="你好", answer="在呢", end_reason="complete"))
    assert mem.appended == [("user", "你好"), ("assistant", "在呢")]
    assert len(obs.turns) == 1 and obs.turns[0]["path"] == "s2s"
    assert obs.spans[0][0] == "s2s.turn"


@pytest.mark.asyncio
async def test_reflux_skips_memory_for_escalated_turn_but_keeps_span():
    """§7-3：escalated 轮主链已落，不重复回灌，只补 span 关联。"""
    mem, obs = FakeMemStub(), FakeObs()
    r = Reflux(memory_stub_getter=lambda: mem, obs=obs, gate_content=lambda s, n: s[:n],
               session_id="s1")
    t = _turn(transcript="开空调", utterance="开空调", status="escalated",
              end_reason="escalated")
    await r(t)
    assert mem.appended == [], "escalated 轮不得重复写 memory"
    assert obs.turns == [], "escalated 轮不重复出 obs.turn"
    assert obs.spans[0][1]["attrs"]["escalated"] is True
    assert obs.spans[0][1]["attrs"]["utterance"] == "开空调"


@pytest.mark.asyncio
async def test_reflux_span_flags_false_promise():
    obs = FakeObs()
    r = Reflux(memory_stub_getter=None, obs=obs, session_id="s1")
    await r(_turn(transcript="把空调开到24", answer="好的，已经帮你打开空调了",
                  end_reason="complete"))
    assert obs.spans[0][1]["attrs"]["s2s_false_promise"] is True
    assert r.false_promises == 1


@pytest.mark.asyncio
async def test_reflux_span_marks_truncated_turn():
    obs = FakeObs()
    r = Reflux(memory_stub_getter=None, obs=obs, session_id="s1")
    await r(_turn(answer="好的我来", truncated=True, end_reason="cancelled"))
    assert obs.spans[0][1]["attrs"]["truncated"] is True


@pytest.mark.asyncio
async def test_reflux_survives_memory_failure():
    class Boom:
        async def AppendTurn(self, req, timeout=None):
            raise RuntimeError("pg down")

    obs = FakeObs()
    r = Reflux(memory_stub_getter=lambda: Boom(), obs=obs, session_id="s1")
    await r(_turn(transcript="你好", answer="在呢", end_reason="complete"))
    assert obs.spans, "记忆写失败仍要出 obs（缺口可见）"


@pytest.mark.asyncio
async def test_context_summary_renders_recent_turns():
    mem = FakeMemStub()

    class T:
        def __init__(self, role, text):
            self.role, self.text = role, text
    mem.turns = [T("user", "我叫泓舟"), T("assistant", "记住啦")]
    s = await build_context_summary(mem, "s1", last_n=4)
    assert s == "用户：我叫泓舟\n你：记住啦"


@pytest.mark.asyncio
async def test_context_summary_empty_when_no_session():
    assert await build_context_summary(FakeMemStub(), "") == ""
    assert await build_context_summary(None, "s1") == ""


# ──────────────────────── 铁律（源码级）────────────────────────

def test_session_layer_has_no_domain_literals():
    """L-Session 是通用会话层——出现领域字面量就是把编排逻辑漏进了传输层。"""
    from s2s import session as S
    src = inspect.getsource(S)
    body = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#") and "：" not in l)
    for bad in ("空调", "导航", "hvac", "navigation", "天气", "媒体", "支付"):
        assert bad not in body, f"会话层不得出现领域字面量 {bad!r}"


def test_protocol_defines_exactly_one_tool():
    """§5.1：不注入 capability 清单。多一个工具就是把判定权还给了 S2S 模型。"""
    from s2s import protocol as PR
    src = inspect.getsource(PR)
    assert len(re.findall(r'"type":\s*"function"', src)) == 1


def test_provider_never_creates_response_after_tool_result():
    """R1 前提的源码守卫：inject_tool_result 里发 response.create 即双播。

    只看**有效代码行**——注释里正写着「刻意不发 response.create」，不能连注释一起判。
    """
    src = inspect.getsource(QwenOmniRealtimeProvider.inject_tool_result)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert "response.create" not in code
