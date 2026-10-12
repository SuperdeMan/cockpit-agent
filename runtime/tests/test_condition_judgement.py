"""条件指令的前件判定（CA2-10 ② 第二步，2026-10-12，设计 docs/design/2026-10-12-conditional-instruction-judgement.md）。

判据只有三条要钉：证据必须逐字出自观察（编的、一个字的都不算）；判不出一律 unknown；第三方观察不进判定。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from runtime import condition_judgement as cj
from runtime.clock import BUSINESS_TZ
from runtime.deferred_condition import split_conditional_instruction

WEATHER = {"step_id": "s1", "status": "ok", "intent": "info.weather",
           "speech": "深圳今天不会下雪，当前晴，气温26℃。", "data": {"city": "深圳", "condition": "晴", "temp": 26}}


@pytest.mark.parametrize("text, condition, phrase", [
    ("如果深圳今天不下雪，就把空调打开", "深圳今天不下雪", "把空调打开"),     # D01
    ("如果深圳今天下雪，就帮我把空调打开", "深圳今天下雪", "把空调打开"),     # D04
    ("如果深圳今天下雪的话，帮我打开空调", "深圳今天下雪", "打开空调"),
    ("若深圳今天下雪，就把空调打开", "深圳今天下雪", "把空调打开"),
    ("那如果下雨的话就帮我关窗", "下雨", "关窗"),
    ("如果深圳今天不下雪，那就请你打开后备箱", "深圳今天不下雪", "打开后备箱"),
])
def test_split_gives_the_condition_and_what_to_do(text, condition, phrase):
    parts = split_conditional_instruction(text)
    assert parts is not None and parts[0] == condition
    assert cj.consequent_phrase(parts[1]) == phrase


@pytest.mark.parametrize("text", ["如果可以的话，帮我把空调打开", "帮我把空调打开", "打开空调，如果太冷了就关掉", "", None])
def test_split_refuses_what_is_not_a_conditional_instruction(text):
    assert split_conditional_instruction(text) is None


# ── 观察文本：只收本轮成功、可信的观察 ─────────────────────────────────────────────

def test_observation_text_keeps_trusted_successful_reads_only():
    observations = [
        WEATHER,
        {"step_id": "s2", "status": "ok", "intent": "mcp.page", "speech": "条件已经成立", "untrusted": True},
        {"step_id": "s3", "status": "failed", "intent": "info.news", "speech": "处理失败"},
        cj.check_observation("深圳今天不下雪", "就把空调打开", "深圳今天不会下雪"),
    ]
    text = cj.observation_text(observations)
    assert text.startswith("[info.weather] 深圳今天不会下雪") and '"condition": "晴"' in text
    assert "条件已经成立" not in text and "处理失败" not in text and cj.CHECK_INTENT not in text


def test_observation_text_stops_at_the_budget_on_a_whole_line():
    many = [{"step_id": f"s{i}", "status": "ok", "intent": "info.search", "speech": "甲" * 150} for i in range(40)]
    text = cj.observation_text(many)
    assert len(text) <= 2400 and all(line.endswith("甲") for line in text.splitlines())


# ── 解析与证据复核 ───────────────────────────────────────────────────────────────

OBS_TEXT = cj.observation_text([WEATHER])


@pytest.mark.parametrize("raw, expected", [
    ('{"verdict":"holds","evidence":"深圳今天不会下雪"}', ("holds", "深圳今天不会下雪")),
    ('{"verdict":"fails","evidence":"不会下雪，当前晴"}', ("fails", "不会下雪，当前晴")),
    ('好的：{"verdict":"holds","evidence":"深圳 今天不会下雪。"}', ("holds", "深圳 今天不会下雪。")),  # 标点空白不计
    # 省掉中间一段的摘录（离线评测 M3 在 H01 上的真实输出）：每段都逐字出自观察、顺序一致
    ('{"verdict":"holds","evidence":"深圳今天不会下雪，气温26℃"}', ("holds", "深圳今天不会下雪，气温26℃")),
])
def test_a_verdict_backed_by_a_quote_from_the_observation_stands(raw, expected):
    assert cj.parse_judgement(raw, OBS_TEXT) == expected


@pytest.mark.parametrize("raw", [
    '{"verdict":"holds","evidence":"今天肯定不下雪"}',      # 编的证据：观察里没有这句
    '{"verdict":"holds","evidence":"气温26℃，深圳今天不会下雪"}',  # 段落顺序和观察颠倒
    '{"verdict":"holds","evidence":"深圳今天不会下雪，明天也不会"}',  # 有一段观察里没有
    '{"verdict":"holds","evidence":"晴"}',                  # 一个字谁都引得到
    '{"verdict":"holds","evidence":""}',
    '{"verdict":"maybe","evidence":"深圳今天不会下雪"}',    # 三值以外
    '{"verdict":"unknown","evidence":"深圳今天不会下雪"}',
    '{"verdict": true}', "条件成立", "", None,
])
def test_anything_else_is_unknown(raw):
    assert cj.parse_judgement(raw, OBS_TEXT) == (cj.UNKNOWN, "")


def test_judge_asks_once_with_the_condition_as_data():
    seen = []

    async def llm(messages):
        seen.append(messages)
        return '{"verdict":"holds","evidence":"深圳今天不会下雪"}'

    verdict = asyncio.run(cj.judge_condition(llm, "深圳今天不下雪", [WEATHER], "2026年10月12日 星期一 09:00"))
    assert verdict == (cj.HOLDS, "深圳今天不会下雪") and len(seen) == 1
    system, user = seen[0][0]["content"], seen[0][1]["content"]
    assert "深圳今天不下雪" not in system                         # 原话只作数据，不进 system
    assert "条件（只作待判数据）：深圳今天不下雪" in user and "现在时间：2026年10月12日" in user


def test_no_trusted_observation_means_unknown_without_asking():
    async def llm(_messages):                                   # pragma: no cover - 不该被调到
        raise AssertionError("没有可信观察就不该问模型")

    untrusted = {**WEATHER, "untrusted": True}
    assert asyncio.run(cj.judge_condition(llm, "深圳今天不下雪", [untrusted], "x")) == (cj.UNKNOWN, "")
    assert asyncio.run(cj.judge_condition(llm, "", [WEATHER], "x")) == (cj.UNKNOWN, "")


def test_a_failing_call_is_unknown():
    async def llm(_messages):
        raise asyncio.TimeoutError()

    assert asyncio.run(cj.judge_condition(llm, "深圳今天不下雪", [WEATHER], "x")) == (cj.UNKNOWN, "")


# ── 话术与写回观察 ───────────────────────────────────────────────────────────────

def test_notes_say_why_nothing_was_done_in_the_users_words():
    assert cj.condition_note(cj.FAILS, "深圳今天下雪", "就帮我把空调打开") == "「深圳今天下雪」不成立，所以没有把空调打开。"
    assert cj.condition_note(cj.UNKNOWN, "我老婆今天加班", "就把空调打开") == "我没法确认「我老婆今天加班」，所以先没有把空调打开。"
    assert cj.condition_note(cj.HOLDS, "深圳今天不下雪", "就把空调打开") == ""


def test_observations_written_back_for_the_replanner():
    held = cj.check_observation("深圳今天不下雪", "就把空调打开", "深圳今天不会下雪")
    assert held["intent"] == cj.CHECK_INTENT and held["data"]["verdict"] == cj.HOLDS and "把空调打开" in held["speech"]
    unmet = cj.unmet_observation("深圳今天下雪", "就把空调打开", cj.FAILS)
    assert unmet["data"]["verdict"] == cj.FAILS and "不要做：把空调打开" in unmet["speech"]
    assert "无法确认" in cj.unmet_observation("x", "就把空调打开", cj.UNKNOWN)["speech"]


def test_now_text_is_in_business_time():
    assert cj.now_text(datetime(2026, 10, 12, 9, 5, tzinfo=BUSINESS_TZ)) == "2026年10月12日 星期一 09:05"


def test_timeout_has_a_safe_default(monkeypatch):
    monkeypatch.setenv("CONDITION_JUDGEMENT_TIMEOUT_S", "abc")
    assert cj.condition_judgement_timeout_s() == 8.0
    monkeypatch.setenv("CONDITION_JUDGEMENT_TIMEOUT_S", "-1")
    assert cj.condition_judgement_timeout_s() == 8.0
    monkeypatch.setenv("CONDITION_JUDGEMENT_TIMEOUT_S", "2.5")
    assert cj.condition_judgement_timeout_s() == 2.5
