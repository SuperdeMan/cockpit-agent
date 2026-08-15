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
