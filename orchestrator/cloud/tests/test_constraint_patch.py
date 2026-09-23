"""会话约束：本轮补丁与跨轮快照分开（评审三轮 R3-02，2026-09-23）。

修前 `extract_focus` 先 `merge_constraints({}, patch)` 把补丁里的 `None`（撤销）归一掉，只剩 SET 那一半进入
`update_focus` 的跨轮合并——「今天想吃辣，排不排队都行」之后旧的 `no_queue=True` 复活，致谢话术还会念出用户
刚撤掉的「不想排队」。补丁一律原样（带墓碑）交给 `update_focus`，那里是唯一「合并旧快照 → 归一」的地方。
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.cloud.context import ContextManager, extract_focus
from orchestrator.cloud.models import Plan
from orchestrator.cloud.session import SessionStore
from runtime.session_constraints import constraints_in


def _manager():
    return ContextManager(clients=None, session=SessionStore(redis_url=""))


def _say(cm, text, occupant="primary"):
    asyncio.run(cm.update_focus("sess-1", Plan(steps=[], raw_text=text), [],
                                user_id="u1", occupant_id=occupant))


def _constraints(cm, occupant="primary"):
    focus = asyncio.run(cm._load_focus("sess-1", "u1", occupant_id=occupant))
    return dict(getattr(focus, "session_constraints", None) or {}) if focus else {}


def test_extract_focus_carries_the_patch_with_its_tombstones():
    focus = extract_focus(Plan(steps=[], raw_text="今天想吃辣，排不排队都行"), [])
    assert focus.session_constraints == {"no_spicy": False, "no_queue": None}


@pytest.mark.parametrize("second, expected", [
    ("今天想吃辣，排不排队都行", {"no_spicy": False}),                  # SET + DELETE 同句
    ("辣不辣都行，可以排队", {"no_queue": False}),                      # 两个维度互换
    ("排不排队都行", {"no_spicy": True}),                               # 仅 DELETE
    ("今天想吃辣", {"no_spicy": False, "no_queue": True}),              # 仅 SET
    ("导航去公司", {"no_spicy": True, "no_queue": True}),               # 未提及保持
])
def test_one_utterance_patches_the_previous_snapshot(second, expected):
    cm = _manager()
    _say(cm, "我不吃辣，也不想排队")
    assert _constraints(cm) == {"no_spicy": True, "no_queue": True}
    _say(cm, second)
    assert _constraints(cm) == expected


def test_the_others_sub_object_takes_set_and_delete_together():
    cm = _manager()
    first = "朋友不吃辣，朋友也不想排队"
    assert constraints_in(first).get("others") == {"no_spicy": True, "no_queue": True}
    _say(cm, first)
    second = "朋友想吃辣，朋友排不排队都行"
    assert constraints_in(second) == {"others": {"no_spicy": False, "no_queue": None}}
    _say(cm, second)
    assert _constraints(cm).get("others") == {"no_spicy": False}


def test_a_set_and_delete_patch_stays_inside_its_owner():
    cm = _manager()
    _say(cm, "我不吃辣，也不想排队", occupant="alice")
    _say(cm, "我不吃辣，也不想排队", occupant="bob")
    _say(cm, "今天想吃辣，排不排队都行", occupant="alice")
    assert _constraints(cm, "alice") == {"no_spicy": False}
    assert _constraints(cm, "bob") == {"no_spicy": True, "no_queue": True}


def test_a_first_turn_delete_is_not_stored_as_a_key():
    cm = _manager()
    _say(cm, "排不排队都行")
    assert _constraints(cm) == {}


def test_the_ack_never_reads_back_a_constraint_the_user_just_waived():
    """用户可见的那一面：修前第二句的致谢是「好的，排不排队都行；这次想吃辣、**不想排队**…」——自相矛盾。"""
    from .test_engine_confirm import _make_engine, _req, _run
    engine, _spy, _ = _make_engine()
    first = _run(engine, _req("我不吃辣，也不想排队"))[-1]
    assert "不吃辣" in first["speech"] and "不想排队" in first["speech"]
    second = _run(engine, _req("今天想吃辣，排不排队都行"))[-1]
    assert "排不排队都行" in second["speech"] and "想吃辣" in second["speech"]
    assert "不想排队" not in second["speech"]
    recall = _run(engine, _req("我今天说过不想排队吗"))[-1]
    assert "不想排队" not in recall["speech"]
