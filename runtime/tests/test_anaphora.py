"""`runtime/anaphora` 的两向断言 + 「判据零领域词」的源码级钉子（批 7 ④）。

正例的前三句是 W19-c 视窗实验（2026-09-20，run 0920b）里三臂恒定被端侧 `battery.query` 接走的原话，
不改一个字；反例是坐在车里问本车、口头填充词、句中复指——这些必须留给端侧秒回。
"""
from __future__ import annotations

import os

import pytest
import yaml

from runtime.anaphora import ANAPHORIC_SUBJECTS, LEADING_PARTICLES, has_anaphoric_subject

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_COMMANDS = os.path.join(_ROOT, "orchestrator", "edge", "knowledge", "commands.yaml")


def _domain_vocabulary() -> set[str]:
    with open(_COMMANDS, encoding="utf-8") as handle:
        commands = yaml.safe_load(handle) or {}
    vocab: set[str] = set()
    for name, spec in (commands.get("objects") or {}).items():
        vocab.add(str(name))
        display = str((spec or {}).get("display_name") or "").strip()
        if display:
            vocab.add(display)
        for intent in ((spec or {}).get("edge_intents") or []):
            vocab.update(str(intent).split("."))
    return {word for word in vocab if word}


def test_no_marker_is_domain_vocabulary():
    vocab = _domain_vocabulary()
    assert len(vocab) > 50
    for word in (*ANAPHORIC_SUBJECTS, *LEADING_PARTICLES):
        assert word not in vocab, f"`{word}` 是 VAL 领域词——判据必须是虚词 / 量词"
        assert not any(obj in word for obj in ("电", "续航", "空调", "车窗", "天窗", "音乐")), word


@pytest.mark.parametrize("text", [
    "它续航多久",                 # W19-c g12 原话
    "那它的纯电续航呢",           # g18
    "那它的续航呢",               # g27
    "它的通行费是多少",           # g32
    "那款车加速几秒",
    "那台车的电池多大",
    "它们哪个更省电",
    "对了，它多重",
    "那么它的售价呢",
])
def test_anaphoric_subject_is_recognised(text):
    assert has_anaphoric_subject(text), text


@pytest.mark.parametrize("text", [
    "电量还有多少",               # 本车查询，端侧秒回
    "续航还剩多少公里",
    "这车续航多少",               # 近指 = 在场的这辆车
    "这辆车还能跑多远",
    "那个，还有多少电",           # 口头填充词
    "那个充电桩离这多远",         # 「那个」+ 名词，主语不是代词
    "把它关掉",                   # 句中，不是主语
    "空调它怎么不制冷",           # 复指，主语是空调
    "那边的天气怎么样",
    "",
])
def test_non_anaphoric_subjects_are_left_alone(text):
    assert not has_anaphoric_subject(text), text
