"""W19-c 视窗实验装置的纯本地守卫：语料形态 + 判据 + 汇总（零网络）。"""
from __future__ import annotations

import json

import pytest

from scripts import probe_history_window as hw


def test_corpus_shape_and_split():
    assert len(hw.GROUPS) == 32
    assert sum(1 for g in hw.GROUPS if g["k"] == 2) == 16
    assert sum(1 for g in hw.GROUPS if g["k"] == 4) == 16
    kws = [g["kw"] for g in hw.GROUPS]
    assert len(set(kws)) == len(kws), "关键词必须唯一：臂内各组共用一个 user，同一关键词会互相串"


@pytest.mark.parametrize("group", hw.GROUPS, ids=[g["kw"] for g in hw.GROUPS])
def test_referent_only_lives_in_the_opening_turn(group):
    """T1 必须含关键词；最后一轮与插话都不能含——否则视窗不是唯一变量。"""
    turns = hw.turns_of(group)
    assert hw._keyword_in(turns[0], group["kw"])
    for text in turns[1:]:
        assert not hw._keyword_in(text, group["kw"]), text
    assert len(turns) == group["k"] + 2


def test_fillers_are_history_neutral():
    """插话不能是天气 / 导航 / 车控 / 附近——那些会改写焦点块（上个城市 / POI / 对象）。"""
    banned = ("天气", "导航", "附近", "空调", "音量", "车窗", "股", "去")
    for text in (*hw._FILLERS_K2, *hw._FILLERS_K4):
        assert not any(b in text for b in banned), text


def test_parse_groups():
    assert hw.parse_groups("1-3,5") == [1, 2, 3, 5]
    assert hw.parse_groups("") == list(range(1, 33))
    with pytest.raises(SystemExit):
        hw.parse_groups("0")


def _detail(plan_steps, intents="info.search", outcome="completed", pairs=3, exch=4):
    return {
        "turn": {"intents": intents, "outcome": outcome, "trace_id": "t1"},
        "spans": [{"node": "cloud.planning",
                   "attrs": json.dumps({"plan": json.dumps(plan_steps, ensure_ascii=False),
                                        "plan_mode": "toolcall",
                                        "history_pairs_kept": pairs, "history_exchanges": exch})}],
    }


def test_judge_reads_the_planner_slots_not_the_speech():
    group = hw.GROUPS[0]                                         # kw=SU7
    resolved = hw.judge(_detail([{"intent": "info.search", "slots": {"query": "小米SU7 充电速度"}}]),
                        group, speech="这个我不清楚")
    assert resolved["slot_resolved"] is True and resolved["speech_mentions"] is False
    assert resolved["history_pairs_kept"] == 3 and resolved["intents"] == "info.search"

    unresolved = hw.judge(_detail([{"intent": "chitchat.talk", "slots": {"depth": "short"}}],
                                  intents="chitchat.talk"),
                          group, speech="小米SU7 的充电速度大约…")
    assert unresolved["slot_resolved"] is False and unresolved["speech_mentions"] is True


def test_judge_survives_a_turn_without_planning_span():
    group = hw.GROUPS[3]
    verdict = hw.judge({"turn": {"intents": "", "outcome": "no_plan"}, "spans": []}, group, "")
    assert verdict["slot_resolved"] is False and verdict["history_pairs_kept"] is None


def test_report_groups_by_k_and_window():
    cells = []
    for w in (2, 4, 6):
        for g in (hw.GROUPS[0], hw.GROUPS[16]):
            idx = hw.GROUPS.index(g) + 1
            cells.append({"window": w, "group": idx, "k": g["k"], "kw": g["kw"],
                          "slot_resolved": w >= g["k"] + 1, "speech_mentions": True,
                          "intents": "info.search", "history_pairs_kept": min(w, g["k"] + 1)})
    text = hw.report({"cells": cells})
    assert "| 2 | 2 对 | 1 | **0/1** |" in text
    assert "| 2 | 4 对 | 1 | **1/1** |" in text
    assert "| 4 | 4 对 | 1 | **0/1** |" in text
    assert "| 4 | 6 对 | 1 | **1/1** |" in text
    assert "视窗 6 对 2/2" in text
