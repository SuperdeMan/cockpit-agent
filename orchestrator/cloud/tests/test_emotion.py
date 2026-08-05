"""会话级情绪信号契约测试（M2 记忆图谱 P2，子 RFC §2.3）。

**为什么它不进记忆层**：母提案 §4.D 给的约束（短 TTL + 不入长期画像 + 需显式授权）
已经把它排除在「记忆」之外了——剩下的就是会话态。它唯一的消费方是 TTS 情感参数
（M1b 已就绪的能力面），要的是「当前这轮」不是画像。

**为什么走 prompt-only 不进 submit_plan schema**：B4-1 两轮教训证明「模型对 schema
结构的响应强于 description 文本」，可选字段摆进 schema 会诱发多填；emotion 是旁路信号
（只喂 TTS 选参、不影响 steps），更不值得冒行为漂移的风险。
"""
import json

import pytest

from orchestrator.cloud.planning import (
    EMOTIONS, PlanBuilder, _assemble_capability_catalog, _parse_emotion,
    _planner_system, _submit_plan_tools,
)


# ── 解析（封闭词表 + fail-open）────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["happy", "tired", "urgent", "frustrated"])
def test_valid_emotions_pass(raw):
    assert _parse_emotion(raw) == raw


def test_case_and_whitespace_tolerated():
    assert _parse_emotion("  TIRED ") == "tired"


def test_neutral_is_no_signal():
    """中性不发信号——空串让下游走缺省（不发 TTS instruct 键，零行为变化）。"""
    assert _parse_emotion("neutral") == ""


@pytest.mark.parametrize("raw", ["", None, "开心", "excited", "very happy", 42, {}])
def test_garbage_falls_open_to_neutral(raw):
    """词表外一律中性——fail-open，与 addressed/clarify 同款姿态。"""
    assert _parse_emotion(raw) == ""


def test_vocab_is_closed_and_documented():
    assert set(EMOTIONS) == {"neutral", "happy", "tired", "urgent", "frustrated"}


# ── prompt 接线 ──────────────────────────────────────────────────────────

def test_emotion_section_present_by_default(monkeypatch):
    monkeypatch.delenv("PLANNER_EMOTION", raising=False)
    assert "情绪标注" in _planner_system()


def test_emotion_section_can_be_disabled(monkeypatch):
    monkeypatch.setenv("PLANNER_EMOTION", "off")
    assert "情绪标注" not in _planner_system()


def test_emotion_never_enters_tool_schema():
    """**铁律**（B4-1 教训）：旁路字段不进 schema。进了就会诱发多填、扰动 steps。"""
    schema = json.dumps(_submit_plan_tools(), ensure_ascii=False)
    assert "emotion" not in schema


def test_emotion_prompt_forbids_changing_steps():
    """prompt 必须明说「不影响规划」——否则模型可能为了标情绪去改 steps。"""
    assert "不影响你的规划" in _planner_system()


# ── Plan 装配 ────────────────────────────────────────────────────────────

class _Agent:
    endpoint = "x:1"

    def __init__(self):
        from cockpit.agent.v1 import agent_pb2
        self.manifest = agent_pb2.AgentManifest(
            agent_id="info", capabilities=[agent_pb2.Capability(intent="info.weather")])


def _parse(data: dict):
    return PlanBuilder._parse_and_validate_data(
        PlanBuilder.__new__(PlanBuilder), data,
        _assemble_capability_catalog([_Agent()]), "今天天气")


_STEP = {"id": "s1", "capability_ref": "cap_0001"}


def test_plan_carries_emotion():
    plan = _parse({"steps": [_STEP], "emotion": "tired"})
    assert plan.emotion == "tired"


def test_plan_without_emotion_defaults_empty():
    plan = _parse({"steps": [_STEP]})
    assert plan.emotion == ""


def test_bad_emotion_does_not_break_plan():
    """情绪字段畸形绝不能影响计划本身——它是旁路信号。"""
    plan = _parse({"steps": [_STEP], "emotion": {"weird": 1}})
    assert plan.emotion == "" and len(plan.steps) == 1
