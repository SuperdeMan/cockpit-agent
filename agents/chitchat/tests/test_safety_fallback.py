"""闲聊兜底的安全护栏（阶段 1 / 卡 Q9，QA 轮 I-043/I-054）。

为什么 chitchat 也要这道闸：**安全问题的落域本身有方差**。真栈实测同一句
「红色机油灯亮了怎么办？」三次取样分别落到 manual-rag、闲聊和澄清；
「困到睁不开眼了」也一样。加固了 manual-rag 与 road-safety 之后，
**兜底这条路就成了唯一没有护栏的入口**——而它正是 QA 轮里答出
「收到，那不提醒也不停车」的那一条。

形态与既有的 `_clock_answer` / `_identity_answer` 完全一致：
**系统持有的判据绝不交给 LLM 答**（墙钟三件套的既有纪律）。

⚠ 两条执行路径都必须挂（`handle` 与 `handle_stream`）——D0 流式直通绕过
executor，本仓已经为此踩过两次。
"""
import asyncio
from unittest.mock import AsyncMock

from agents._sdk.testing import run_handle, run_handle_stream
from agents.chitchat.src.agent import ChitchatAgent


def _agent(reply: str = "好的，安心开，稳住车速。"):
    agent = ChitchatAgent()
    agent.llm.complete = AsyncMock(return_value=reply)
    return agent


def test_fatigue_answered_deterministically_without_llm():
    agent = _agent()
    res = asyncio.run(run_handle(
        agent, "chitchat.talk", raw_text="困到睁不开眼了，还要开两个小时"))
    assert agent.llm.complete.await_count == 0, "安全信号不得交给 LLM"
    assert any(w in res.speech for w in ("休息", "服务区", "停车"))
    assert ((res.data or {}).get("_safety_alert") or {}).get("level") == "critical"


def test_warning_light_answered_deterministically():
    agent = _agent()
    res = asyncio.run(run_handle(
        agent, "chitchat.talk", raw_text="红色机油灯亮了怎么办？"))
    assert agent.llm.complete.await_count == 0
    assert "停车" in res.speech
    assert ((res.data or {}).get("_safety_alert") or {}).get("level") == "critical"


def test_stream_path_has_the_same_guard():
    """**D0 流式直通绕过 executor**——只在 handle 里加闸等于没加。"""
    agent = _agent()
    events = asyncio.run(run_handle_stream(
        agent, "chitchat.talk", raw_text="刚喝了两杯酒，还能开车回家吗"))
    assert agent.llm.complete.await_count == 0
    kinds = [k for k, _ in events]
    assert kinds == ["speech", "final"], kinds
    final = events[-1][1]
    assert any(w in final.speech for w in ("不要驾驶", "代驾", "别开"))
    assert ((final.data or {}).get("_safety_alert") or {}).get("level") == "critical"


def test_active_alert_enters_system_prompt():
    """会话有未解除告警时，它必须是**这一轮回答的前提**（进 system prompt）。

    用户可以拒绝被提醒，系统不可以跟着改口说不用停车。
    """
    import json
    agent = _agent("好的，那我不多说了。")
    # 评审四轮起「别提醒我，继续开就行」这类**只是在拒绝建议**的句子走确定性立场（见下面那组），不再交给模型；
    # 这里换一句告警在场时的普通话题，验的仍是「告警是这一轮回答的前提」。
    asyncio.run(run_handle(
        agent, "chitchat.talk", raw_text="路上无聊，陪我聊聊天",
        meta={"focus_safety_alert": json.dumps(
            {"level": "critical", "signal": "机油灯亮"})}))
    system = agent.llm.complete.await_args[0][0][0]["content"]
    assert "未解除的安全告警" in system
    assert "机油灯亮" in system


def test_ordinary_chitchat_untouched():
    """反向对照：普通闲聊照常走 LLM，system 里不得出现安全告警段。"""
    agent = _agent("今天心情不错呀。")
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="讲个笑话吧"))
    assert agent.llm.complete.await_count == 1
    assert res.speech == "今天心情不错呀。"
    assert not ((res.data or {}).get("_safety_alert") or {})
    system = agent.llm.complete.await_args[0][0][0]["content"]
    assert "未解除的安全告警" not in system


# ── 解除陈述（QA T47 裁决 A，2026-09-19）────────────────────────────────────

def test_resolution_statement_is_not_answered_as_an_alert():
    """「机油灯灭了」不是一条新告警：不走确定性直答、不声明 `_safety_alert`。"""
    agent = _agent("太好了，那就放心开吧。")
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="检查过了，机油灯已经灭了"))
    assert agent.llm.complete.await_count == 1
    assert res.speech == "太好了，那就放心开吧。"
    assert not ((res.data or {}).get("_safety_alert") or {})


def test_resolution_turn_drops_the_session_alert_from_the_prompt():
    """用户这一句已经说了灯灭了，会话里那条旧告警不再是这轮回答的前提。
    反向对照留在下面：没说解除时 prompt 照旧带着它。"""
    import json
    meta = {"focus_safety_alert": json.dumps({"level": "critical", "signal": "机油灯"})}
    agent = _agent("好的。")
    asyncio.run(run_handle(agent, "chitchat.talk", raw_text="机油灯灭了，不用管它了", meta=meta))
    system = agent.llm.complete.await_args[0][0][0]["content"]
    assert "未解除的安全告警" not in system
    agent = _agent("好的。")
    asyncio.run(run_handle(agent, "chitchat.talk", raw_text="好的，我会靠边停车检查", meta=meta))
    system = agent.llm.complete.await_args[0][0][0]["content"]
    assert "未解除的安全告警" in system


# ── 追加批 K（K-2，2026-09-24；设计 §14）：提到这类风险 ≠ 此刻自述 ─────────────────────────────

import pytest  # noqa: E402

from runtime.safety_signal import DRIVER_STATE_ADVICE  # noqa: E402


@pytest.mark.parametrize("text, state", [
    ("喝酒后多久能开车", "alcohol"), ("万一开车时犯困怎么办", "fatigue"), ("如果开车时头晕怎么办", "unwell"),
])
def test_a_mentioned_risk_gets_the_topic_advice_without_declaring_an_alert(text, state):
    """修前「喝酒后多久能开车」答「喝过酒…请不要驾驶…叫代驾」并声明一条 critical 会话告警。"""
    agent = _agent()
    unary = asyncio.run(run_handle(agent, "chitchat.talk", raw_text=text))
    streamed = asyncio.run(run_handle_stream(agent, "chitchat.talk", raw_text=text))[-1][1]
    for res in (unary, streamed):
        assert res.speech == DRIVER_STATE_ADVICE[state]["topic_speech"]
        assert not ((res.data or {}).get("_safety_alert") or {})
    assert agent.llm.complete.await_count == 0


def test_a_negated_state_goes_to_the_model_like_any_other_chat():
    agent = _agent("好的，那就放心开。")
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="我没喝酒，放心"))
    assert agent.llm.complete.await_count == 1
    assert res.speech == "好的，那就放心开。"
    assert not ((res.data or {}).get("_safety_alert") or {})


def test_a_lit_warning_light_outranks_a_mentioned_risk():
    """此刻亮着的灯比一个话题更要紧：同一句里两样都有时按告警答、并声明会话告警。"""
    agent = _agent()
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="机油灯亮了，万一犯困怎么办"))
    assert "停车" in res.speech
    assert ((res.data or {}).get("_safety_alert") or {}).get("level") == "critical"


# ── 评审四轮批 A 回归（2026-09-24，SF4）：告警在场时「只是在拒绝建议」⇒ 确定性立场，零 LLM ─────────────────
# system 里写着「不得表示可以继续危险驾驶、立场不改」，MiniMax 仍有一趟对「别提醒我，继续开就行」答了「可以。」
# （生产 `b48d76d7`，trace `bded2ebf88224ffa`）。安全线不能靠模型遵从一句提示。

import json as _json  # noqa: E402

from runtime.safety_signal import refusal_stance  # noqa: E402

_FATIGUE = {"focus_safety_alert": _json.dumps({"level": "critical", "signal": "疲劳驾驶"}, ensure_ascii=False)}
_OIL = {"focus_safety_alert": _json.dumps({"level": "critical", "signal": "机油灯"}, ensure_ascii=False)}


@pytest.mark.parametrize("text", ["别提醒我，继续开就行", "我没事，继续开", "不用管我", "好了，别啰嗦了"])
def test_refusing_advice_under_an_alert_keeps_the_stance_without_the_model(text):
    agent = _agent("可以。")
    unary = asyncio.run(run_handle(agent, "chitchat.talk", raw_text=text, meta=_FATIGUE))
    streamed = asyncio.run(run_handle_stream(agent, "chitchat.talk", raw_text=text, meta=_FATIGUE))[-1][1]
    assert agent.llm.complete.await_count == 0, text
    for res in (unary, streamed):
        assert res.speech == refusal_stance({"level": "critical", "signal": "疲劳驾驶"}), text
        assert "可以继续" not in res.speech.replace("没法说可以继续开", "")
        assert any(w in res.speech for w in ("服务区", "停车", "休息"))


def test_a_vehicle_alert_gets_the_vehicle_stance():
    agent = _agent("可以。")
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="别提醒我，继续开就行", meta=_OIL))
    assert agent.llm.complete.await_count == 0
    assert "机油灯还没有排除" in res.speech and "停车" in res.speech


@pytest.mark.parametrize("text, meta", [
    ("别提醒我，帮我找个服务区", _FATIGUE),     # 拒绝 + 一个请求：不是「只在拒绝」，归模型
    ("别提醒我，继续开就行", {}),               # 没有告警：普通闲聊
    ("机油灯已经灭了，继续开就行", _OIL),       # 这一句在解除告警
])
def test_the_stance_only_answers_a_bare_refusal_under_a_live_alert(text, meta):
    agent = _agent("好的。")
    asyncio.run(run_handle(agent, "chitchat.talk", raw_text=text, meta=meta))
    assert agent.llm.complete.await_count == 1, text


def test_refusal_stance_text_by_alert_kind():
    assert refusal_stance({}) == ""
    assert "疲劳驾驶的风险还在" in refusal_stance({"level": "critical", "signal": "疲劳驾驶"})
    assert "代驾" in refusal_stance({"level": "critical", "signal": "酒后/服药驾驶"})
    assert "降低车速" in refusal_stance({"level": "amber", "signal": "胎压报警"})
