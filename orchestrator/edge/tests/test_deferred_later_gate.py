"""往后推的说法当场不在端侧执行（2026-10-19，200×5 后续批 §23）。

修前离线复现：「八点打开空调」「等会儿再把车窗打开」「下班的时候打开空调」「十分钟后再打开空调」「明天早上八点打开空调」
「到了公司再开空调」「下雨的话把车窗关上」端侧全部当场执行。分类出口逐段否决（`defers_to_later`），组合句里当下要做的那段照常
本地秒回（`test_regression_complex_intent::test_independent_cloud_request_does_not_swallow_the_local_half`）；句首单说时间的一截
与条件句整句上云（`is_deferred_instruction`）。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.orchestrator.v1 import orchestrator_pb2

from fast_intent import classify
from server import EdgeOrchestratorServicer


def _run(monkeypatch, text: str) -> tuple[list[str], str]:
    """跑一句，返回（端侧真执行的命令, 上云的文本）。"""
    servicer = EdgeOrchestratorServicer()
    seen = {"text": ""}

    async def fake_cloud_handle(request):
        seen["text"] = request.text
        yield orchestrator_pb2.HandleEvent(final=orchestrator_pb2.FinalResult(speech="云端请求已处理"))

    monkeypatch.setattr(servicer.cloud, "handle", fake_cloud_handle)
    request = orchestrator_pb2.HandleRequest(text=text, session_id="deferred-later-gate", meta={})

    async def collect():
        return [event async for event in servicer.Handle(request, None)]

    events = asyncio.run(collect())
    commands = [action.payload["command"]
                for event in events if event.WhichOneof("event") == "final"
                for action in event.final.actions]
    return commands, seen["text"]


@pytest.mark.parametrize("text", [
    "八点打开空调", "等会儿再把车窗打开", "下班的时候打开空调", "十分钟后再打开空调", "明天早上八点打开空调",
    "到了公司再开空调", "一会儿到家了再开空调", "晚上再开空调",
    "下雨的话把车窗关上",                                   # 没有框架词的条件句
    "明天早上八点，帮我打开空调", "下班的时候，打开空调",     # 句首单说时间、逗号后才是要做的事
])
def test_a_deferred_instruction_does_not_run_on_the_edge(monkeypatch, text):
    commands, cloud_text = _run(monkeypatch, text)
    assert commands == [] and cloud_text == text


@pytest.mark.parametrize("text", ["八点打开空调", "等会儿再把车窗打开", "下班的时候打开空调"])
def test_the_classifier_exit_vetoes_the_write(text):
    assert classify(text) is None


def test_in_a_compound_the_deferred_half_never_runs_now(monkeypatch):
    """「打开座椅加热，八点再打开空调」：往后推的那段过不了出口；它认不出云侧域，按分组规则当补语带着整句上云（云端按分句点名：
    座椅加热照做、空调拦下，`orchestrator/cloud/tests/test_deferred_instruction_guard.py`）。端侧这里只钉住：八点要开的空调不当场开。"""
    commands, _cloud_text = _run(monkeypatch, "打开座椅加热，八点再打开空调")
    assert "hvac.on" not in commands
    commands, cloud_text = _run(monkeypatch, "音量调小一点，提醒我八点开会")
    assert commands == ["volume.dec"] and cloud_text == "提醒我八点开会"


def test_the_object_backfill_cannot_bring_a_deferred_write_back(monkeypatch):
    """「打开空调，八点再关掉」：第二段没有对象，回填成「空调八点再关掉」照样过出口否决——八点要做的关空调不在端侧当场执行。"""
    commands, _cloud_text = _run(monkeypatch, "打开空调，八点再关掉")
    assert "hvac.off" not in commands


@pytest.mark.parametrize(("text", "command"), [
    ("打开空调", "hvac.on"), ("空调调高一点", "hvac.inc"), ("把空调调到二十六度", "hvac.set"),
])
def test_plain_instructions_stay_local(monkeypatch, text, command):
    commands, cloud_text = _run(monkeypatch, text)
    assert commands == [command] and cloud_text == ""
