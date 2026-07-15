"""chitchat 契约测试。mock 掉 LLM 调用，只验证编排逻辑。"""
import asyncio
import os
from unittest.mock import AsyncMock

from agents._sdk.testing import make_context, run_handle
from agents.chitchat.src.agent import ChitchatAgent, _resolve_model, _length, _system


def test_chitchat_injects_recalled_personal_memory():
    """记住宠物名：召回到的个人信息注入 chitchat system prompt，使其答得上。"""
    agent = ChitchatAgent()
    captured = {}

    async def fake_complete(messages, **kw):
        captured["messages"] = messages
        return "您的宠物叫旺财呀～"

    agent.llm.complete = fake_complete
    ctx = make_context()
    ctx._memory.recall.return_value = [
        {"text": "用户的宠物叫旺财", "scope": "profile.person",
         "predicate": "person.pet", "confidence": 0.9}]
    res = asyncio.run(run_handle(agent, "chitchat.talk",
                                 raw_text="我的宠物叫什么名字", ctx=ctx))
    assert res.status == "ok"
    assert "旺财" in captured["messages"][0]["content"]  # 召回的宠物名进了 system


def test_talk_returns_speech():
    agent = ChitchatAgent()
    agent.llm.complete = AsyncMock(return_value="哈哈，那我给你讲个冷笑话～")
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="讲个笑话"))
    assert res.status == "ok"
    assert res.speech == "哈哈，那我给你讲个冷笑话～"


# ─── task 4：开放域模型分层 + 话术长度 + 昵称 ───

def test_model_tiering_by_pref():
    # 多 LLM 源：分层返回**档位哨兵**（非具体模型名），由网关按 active provider 解析成具体模型。
    assert _resolve_model({"model_pref": "deep"}) == ""        # 深度→重模型档位（primary=空串）
    assert _resolve_model({"model_pref": "fast"}) == "@fast"   # 快速→快模型档位
    assert _resolve_model({}) == "@fast"                        # 默认开放域走快模型档位


def test_model_tiering_depth_slot_beats_session_pref():
    """P1-1：Planner 对知识/解释类下发 slots.depth=deep → primary；寒暄不传仍走快模型。"""
    assert _resolve_model({}, {"depth": "deep"}) == ""
    assert _resolve_model({"model_pref": "deep"}, {}) == ""     # 会话级偏好仍生效
    assert _resolve_model({"model_pref": "fast"}, {"depth": "deep"}) == ""  # slot 优先
    assert _resolve_model({}, {}) == "@fast"


def test_system_has_date_anchor_and_no_fabrication_guard():
    """P1-1：system 注入今日日期 + 时效不编造护栏。"""
    from agents._sdk.grounding import shanghai_now
    sys_text = _system({})
    assert f"{shanghai_now():%Y年%m月%d日}" in sys_text
    assert "绝不编造" in sys_text


def test_system_anchor_includes_weekday_and_clock():
    """badcase 2026-07-15：锚只有日期时模型会编时刻——锚补星期+时刻。"""
    import re as _re
    sys_text = _system({})
    assert _re.search(r"星期[一二三四五六日]", sys_text)
    assert _re.search(r"现在\d{2}:\d{2}", sys_text)


# ─── 钟点/日期/星期确定性直答（badcase 2026-07-15：LLM 编造时刻）───

def test_clock_answer_patterns():
    from agents.chitchat.src.agent import _clock_answer
    # 占据整句的钟点/日期/星期问句 → 直答
    for q in ("现在几点了", "几点了", "请问现在几点", "现在几点钟",
              "现在是什么时间", "当前时间", "现在时间是多少"):
        assert _clock_answer(q).startswith("现在是"), q
    for q in ("今天几号", "今天是几月几号", "今天多少号"):
        assert _clock_answer(q).startswith("今天是"), q
    for q in ("今天星期几", "今天周几", "今天是礼拜几"):
        assert _clock_answer(q).startswith("今天星期"), q
    # 含时间词的其他意图不劫持
    for q in ("明天几点有比赛", "几点提醒我吃药", "现在时间还早吗",
              "昨晚比赛几点开的", "讲个笑话", "今天天气怎么样", ""):
        assert _clock_answer(q) == "", q


def test_spoken_time_segments():
    from datetime import datetime
    from agents.chitchat.src.agent import _spoken_time
    assert _spoken_time(datetime(2026, 7, 15, 14, 27)) == "下午2点27分"
    assert _spoken_time(datetime(2026, 7, 15, 0, 5)) == "凌晨12点5分"
    assert _spoken_time(datetime(2026, 7, 15, 12, 0)) == "中午12点整"
    assert _spoken_time(datetime(2026, 7, 15, 20, 30)) == "晚上8点30分"
    assert _spoken_time(datetime(2026, 7, 15, 7, 0)) == "早上7点整"


def test_handle_clock_question_skips_llm():
    """「现在几点了」按系统墙钟直答，LLM 完全不参与（零编造面）。"""
    agent = ChitchatAgent()
    agent.llm.complete = AsyncMock(side_effect=AssertionError("LLM 不该被调"))
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="现在几点了"))
    assert res.status == "ok"
    assert res.speech.startswith("现在是") and "点" in res.speech
    agent.llm.complete.assert_not_called()


def test_handle_stream_clock_question_skips_llm():
    agent = ChitchatAgent()

    async def boom(*a, **k):
        raise AssertionError("LLM 不该被调")
        yield  # pragma: no cover

    agent.llm.stream = boom
    events = _collect_stream(agent, "今天星期几")
    kinds = [k for k, _ in events]
    assert kinds == ["speech", "final"]
    assert events[0][1].startswith("今天星期")
    assert events[-1][1].speech == events[0][1]


def test_length_and_name_honored():
    assert _length({"answer_length": "short"})[0] == 140
    assert _length({"answer_length": "detailed"})[0] == 440
    assert _length({})[0] == 220
    assert "小航" in _system({"assistant_name": "小航"})


def test_handle_passes_fast_model_and_tokens():
    """handle 把分层模型与长度对应的 max_tokens 透传给 LLM。"""
    agent = ChitchatAgent()
    captured = {}

    async def fake_complete(messages, model="", temperature=0.7, max_tokens=512):
        captured["model"], captured["max_tokens"] = model, max_tokens
        return "好的"

    agent.llm.complete = fake_complete
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="讲个笑话",
                                 meta={"model_pref": "fast", "answer_length": "short"}))
    assert res.speech == "好的"
    assert captured["model"] == "@fast"   # 快模型档位哨兵（网关侧解析成 active provider 的 fast 模型）
    assert captured["max_tokens"] == 140


# ─── P1-2 时效兜底：<search> 标记 → 通用 escalate 改派 ───

def test_parse_search_mark():
    from agents.chitchat.src.agent import _parse_search_mark
    assert _parse_search_mark("<search>昨晚欧冠决赛结果</search>") == "昨晚欧冠决赛结果"
    assert _parse_search_mark("  <search> 今日油价 </search>") == "今日油价"
    assert _parse_search_mark("好的，我给你讲个笑话") == ""
    assert _parse_search_mark("我觉得<search>不算标记</search>") == ""   # 非开头不算
    assert _parse_search_mark("") == ""


def test_handle_escalates_on_search_mark():
    agent = ChitchatAgent()
    agent.llm.complete = AsyncMock(return_value="<search>昨晚欧冠决赛结果</search>")
    res = asyncio.run(run_handle(agent, "chitchat.talk", raw_text="昨晚欧冠谁赢了"))
    assert res.status == "ok"
    assert res.speech == ""                                   # 零播报
    esc = res.data["_escalate"]
    assert esc["intent"] == "info.search"
    assert esc["slots"]["query"] == "昨晚欧冠决赛结果"


def _stream_of(*chunks):
    async def _gen(*a, **k):
        for c in chunks:
            yield c
    return _gen


def _collect_stream(agent, raw_text):
    async def scenario():
        out = []
        from agents._sdk.testing import make_context
        from agents._sdk.base import IntentView
        ctx = make_context()
        intent = IntentView(name="chitchat.talk", slots={}, raw_text=raw_text,
                            confidence=1.0)
        async for ev in agent.handle_stream(intent, ctx, {}):
            out.append(ev)
        return out
    return asyncio.run(scenario())


def test_handle_stream_buffers_marker_and_escalates_with_zero_speech():
    """标记被拆成多个 delta 到达：头部缓冲判定、全程零 speech、final 带 _escalate。"""
    agent = ChitchatAgent()
    agent.llm.stream = _stream_of("<se", "arch>昨晚欧冠", "决赛结果</search>")
    events = _collect_stream(agent, "昨晚欧冠谁赢了")
    kinds = [k for k, _ in events]
    assert "speech" not in kinds                              # 零增量播报
    final = events[-1][1]
    assert final.data["_escalate"]["slots"]["query"] == "昨晚欧冠决赛结果"


def test_handle_stream_normal_reply_flushes_after_probe():
    """普通回复：判定后一次性放流缓冲，其后逐 delta 直通，final 话术完整。"""
    agent = ChitchatAgent()
    agent.llm.stream = _stream_of("哈哈", "，给你讲个", "冷笑话～")
    events = _collect_stream(agent, "讲个笑话")
    speech = "".join(p for k, p in events if k == "speech")
    assert speech == "哈哈，给你讲个冷笑话～"
    assert events[-1][0] == "final" and events[-1][1].speech == "哈哈，给你讲个冷笑话～"


def test_handle_stream_short_reply_flushed_at_end():
    """极短回复（判定窗内即结束）也不丢字。"""
    agent = ChitchatAgent()
    agent.llm.stream = _stream_of("好")
    events = _collect_stream(agent, "帮我记住这件事")
    speech = "".join(p for k, p in events if k == "speech")
    assert speech == "好"
