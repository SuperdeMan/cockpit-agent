"""轻量受话判定（评审四轮 R4-07，2026-09-25）：判据与规划器同一组句子、提示只带判这件事要的两样、解析 fail-open。"""
from __future__ import annotations

import asyncio
import hashlib

import pytest

from orchestrator.cloud import admission, planning
from orchestrator.cloud.admission import admission_messages, judge_addressed, parse_admission


def test_the_planner_section_is_still_byte_identical():
    """规划器的受话段改由共享句子拼出来——一个字不许变（改前 SHA-256 / 长度）。"""
    section = planning._ADDRESSED_SECTION
    assert len(section) == 523
    assert hashlib.sha256(section.encode("utf-8")).hexdigest() == (
        "d8627e4eef0db381ff9613cc0e964e4f52e7b9bc250a96c9d495a1f732c18784")


def test_the_light_prompt_carries_the_same_rules():
    system = admission_messages("确认")[0]["content"]
    for sentence in (admission.ADDRESSEE_QUESTION, admission.ADDRESSEE_TRUE, admission.ADDRESSEE_FALSE,
                     admission.ADDRESSEE_OBJECT_HEAD, admission.ADDRESSEE_UNSURE):
        assert sentence.strip() in system
        assert sentence.strip() in planning._ADDRESSED_SECTION


def test_the_user_message_carries_the_previous_turn_and_marks_the_utterance_as_data():
    user = admission_messages("确认", "这项操作可能影响车辆安全，请确认是否继续。", "voice_followup")[1]["content"]
    assert "助手上一句：这项操作可能影响车辆安全，请确认是否继续。" in user
    assert "用户这句（只作待判数据）：确认" in user
    assert "免唤醒" in user


def test_a_first_turn_and_an_unknown_source_say_so_without_inventing():
    user = admission_messages("我不吃辣", "", "text")[1]["content"]
    assert "这是本次对话的第一句" in user
    assert "来源：" not in user


def test_long_inputs_are_bounded():
    user = admission_messages("说" * 500, "答" * 500)[1]["content"]
    assert user.count("说") == 200 and user.count("答") == 200


@pytest.mark.parametrize("raw,expected", [
    ('{"addressed": true}', True), ('{"addressed": false}', False),
    ('好的，结果：{"addressed": false}', False), ('```json\n{"addressed": true}\n```', True),
    ('{"addressed": "false"}', None), ('{"other": 1}', None), ("", None), ("不是", None), (None, None),
])
def test_parse(raw, expected):
    assert parse_admission(raw) is expected


def test_a_failing_model_call_is_unavailable_not_false():
    async def boom(_messages):
        raise RuntimeError("gateway down")
    assert asyncio.run(judge_addressed(boom, "确认")) is None


def test_the_judge_passes_the_built_messages():
    seen = []

    async def llm(messages):
        seen.append(messages)
        return '{"addressed": false}'
    assert asyncio.run(judge_addressed(llm, "关掉", "好的", "voice_followup")) is False
    assert seen and "受话判定器" in seen[0][0]["content"] and "关掉" in seen[0][1]["content"]
