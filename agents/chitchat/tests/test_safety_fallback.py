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
    asyncio.run(run_handle(
        agent, "chitchat.talk", raw_text="别提醒我，继续开就行",
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
