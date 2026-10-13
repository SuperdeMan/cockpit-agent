"""`memory.remember`（2026-10-13，docs/design/2026-10-13-v2-200x5-followups.md §2）：整句交给 Memory 当场写入，
**按写入结果答复**——只有 `stored` 才说「记住了」；清单里那条整句锚定的 route_hint 只接「记住 + 我…」的单句事实。"""
from types import SimpleNamespace

import pytest

from agents._sdk.manifest import load_manifest
from agents._sdk.testing import make_context, run_handle
from agents.reminder.src.agent import _MANIFEST, ReminderAgent
from orchestrator.cloud.route_hints import RouteHintEngine

_CLAIMS = ("记住了", "记下了", "已记住", "记好了")


def _ctx(outcome, **extra):
    ctx = make_context()
    ctx._memory.remember_utterance.return_value = {"outcome": outcome, "stored_count": 0,
                                                   "stored_texts": [], **extra}
    return ctx


@pytest.mark.asyncio
async def test_stored_says_so_in_the_users_own_words():
    ctx = _ctx("stored", stored_count=2, stored_texts=["用户的女儿叫小满"])
    res = await run_handle(ReminderAgent(), "memory.remember", raw_text="记住，我女儿叫小满。", ctx=ctx)

    assert res.speech == "好的，记住了：你女儿叫小满。"
    assert res.data["memory_outcome"] == "stored" and res.data["_speech_verbatim"] is True
    args, kwargs = ctx._memory.remember_utterance.await_args
    assert args[:2] == ("u1", "记住，我女儿叫小满。")          # 整句原样交给 Memory，不改写


@pytest.mark.asyncio
async def test_the_planners_content_echo_is_accepted_but_the_raw_sentence_is_written():
    """revision 2：规划器复述进 `content` 的内容不读——Memory 收到的是原话（复测里复述成「喜欢喝拿铁」「老婆的生日是…」）。"""
    ctx = _ctx("stored")
    res = await run_handle(ReminderAgent(), "memory.remember", raw_text="记住我喜欢喝拿铁",
                           slots={"content": "喜欢喝拿铁"}, ctx=ctx)
    assert res.speech == "好的，记住了：你喜欢喝拿铁。"
    args, _kwargs = ctx._memory.remember_utterance.await_args
    assert args[:2] == ("u1", "记住我喜欢喝拿铁")


@pytest.mark.asyncio
async def test_a_planned_statement_without_the_directive_is_echoed_too():
    res = await run_handle(ReminderAgent(), "memory.remember", raw_text="我喜欢喝拿铁", ctx=_ctx("stored"))
    assert res.speech == "好的，记住了：你喜欢喝拿铁。"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,expect", [
    ("pii", "不记"), ("not_stored", "没能记下来"), ("stale", "没能记下来"), ("off", "没开记忆"),
    ("timeout", "不确定"), ("unavailable", "没记下来"), ("", "没记下来")])
async def test_nothing_written_is_never_called_remembered(outcome, expect):
    """E08：电话号码答「好的，我记下了」而什么也没写——这里每一种没写进去的结果都不许出现「记住了」。"""
    res = await run_handle(ReminderAgent(), "memory.remember",
                           raw_text="记住，我老婆的手机号是13800138000", ctx=_ctx(outcome))
    assert expect in res.speech
    assert not any(word in res.speech for word in _CLAIMS)


def _memory_hint():
    manifest = load_manifest(_MANIFEST)
    [hint] = [h for h in manifest.route_hints if h.intent == "memory.remember"]
    return hint


def _hits(text):
    agent = SimpleNamespace(manifest=SimpleNamespace(route_hints=[_memory_hint()]))
    return RouteHintEngine(lambda raw, _map: []).matches_replace(text, {"reminder": agent})


@pytest.mark.parametrize("text", [
    "记住，我女儿叫小满", "记住我喜欢喝拿铁", "记住，我现在改喝美式了", "记住，我开车不喜欢走高速",
    "记住，我老婆的生日是五月二十号", "记住，我老婆的手机号是13800138000", "别忘了我不吃香菜",
    "帮我记住我对花生过敏", "嗯，记着我车停在B2层", "记住我老婆的生日是五月二十号",
])
def test_the_hint_takes_a_single_first_person_fact(text):
    assert _hits(text)


@pytest.mark.parametrize("text", [
    "记一下要买牛奶",                      # 待办口吻，提醒 Agent 的待办路径
    "记得明天提醒我带伞", "别忘了提醒我开会", "记住我明天要去开会", "别忘了今晚八点的会",   # 提醒 / 带时间
    "记着我下班前要你提醒我拿快递",          # 以「我」开头、只靠提醒线索挡下的那一类
    "你记得我喜欢喝什么吗", "你还记得我女儿叫什么吗", "记得我上次说的那家店吗", "记住我女儿叫什么",  # 在问
    "记住我喜欢喝拿铁，然后帮我点一杯",    # 复合句，交给规划器
    "我喜欢喝拿铁", "我记住了", "记住", "记住我", "记得把空调打开",
])
def test_the_hint_leaves_everything_else_to_the_planner(text):
    assert not _hits(text)
