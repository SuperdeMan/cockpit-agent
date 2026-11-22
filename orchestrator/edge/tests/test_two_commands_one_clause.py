"""整句只解出一条、原话里还有另一条的句子不在端侧执行（2026-10-29，第十七轮）。

修前离线复现（与 edge server 同一分支顺序）：「先关窗再开空调」「先打开空调再关窗」「关上车窗打开空调」「关闭空调再打开」全被当场
执行成关空调——分支按子串认对象与方向，方向取自另一个分句；「打开天窗再关闭车窗」只关了车窗，「把温度调到26度再开风扇」只调了温度，
「查天气再放首歌」只放了歌。逗号 / 连词分得开的句子在拆句那层各解各的，这里管的是分不开的：不带逗号的「再」、两个动宾直接挨着。
云端对这类句子排得对（只规划取证 6 / 6），端侧整句上云。

同批：名词「开关」不再当方向——「打开语音唤醒开关」修前被关掉（飞书意图语料 12 句全解成 close）。
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
    request = orchestrator_pb2.HandleRequest(text=text, session_id="two-commands-one-clause", meta={})

    async def collect():
        return [event async for event in servicer.Handle(request, None)]

    events = asyncio.run(collect())
    commands = [action.payload["command"]
                for event in events if event.WhichOneof("event") == "final"
                for action in event.final.actions]
    return commands, seen["text"]


TWO_COMMANDS = [
    # 不带逗号的「再」
    "先关窗再开空调", "先打开空调再关窗", "先开空调再关车窗", "关窗再开空调", "把车窗关上再打开空调",
    "关闭空调再打开", "打开天窗再关闭车窗", "把温度调到26度再开风扇", "打开座椅加热再把温度调到26度",
    "查天气再放首歌", "放首歌再查天气", "空调先调到24度再调到26度", "把空调温度调到26度再把风量调到2档",
    "先调低音量再调高音量", "打开前排车窗再打开后排车窗",
    # 两个动宾直接挨着 / 「先…后…」
    "关窗开空调", "关上车窗打开空调", "打开空调关窗", "开着空调关窗", "先关窗后开空调", "关闭座椅加热打开座椅通风",
    "打开前排车窗打开后排车窗",
]


@pytest.mark.parametrize("text", TWO_COMMANDS)
def test_one_clause_with_two_commands_is_not_classified_as_one(text):
    assert classify(text) is None


@pytest.mark.parametrize("text", ["先关窗再开空调", "先打开空调再关窗", "关上车窗打开空调", "关闭空调再打开",
                                  "先调低音量再调高音量"])
def test_the_whole_utterance_goes_to_the_cloud_and_nothing_runs_on_the_edge(monkeypatch, text):
    commands, cloud_text = _run(monkeypatch, text)
    assert commands == [] and cloud_text == text


def test_in_a_compound_the_unsplit_half_never_runs_as_a_frankenstein_command(monkeypatch):
    """逗号切开之后，后一截自己又是分不开的两件事：它不能再被解成关空调。"""
    commands, _cloud_text = _run(monkeypatch, "打开座椅加热，先关窗再开空调")
    assert "hvac.off" not in commands


@pytest.mark.parametrize(("text", "name"), [
    # 「再」是「再多一些 / 又一次」：前一截只是对象，后一截回填后还是同一件事
    ("空调温度再调高一点", "hvac.inc"), ("温度再低一点", "hvac.dec"), ("车窗再关小一点", "window.dec"),
    ("后排车窗再关闭10%", "window.close"), ("后排车窗再打开10%", "window.set"), ("空调再开大一点", "hvac.on"),
    ("音量再大一点", "volume.inc"), ("再开一下空调", "hvac.on"),
    # 「再」后面没有指令内容
    ("打开空调再说", "hvac.on"), ("先调空调再看结果", "hvac.on"),
    # 一件事：动词和对象只说了一组，或补充的那截没说方向
    ("关闭空调", "hvac.off"), ("帮我打开空调", "hvac.on"), ("请把车窗关上", "window.close"),
    ("打开空调调到26度", "hvac.set"), ("打开主驾和副驾车窗", "window.open"),
])
def test_a_single_command_still_runs_locally(text, name):
    result = classify(text)
    assert result is not None and result["name"] == name


@pytest.mark.parametrize(("text", "name"), [
    ("打开语音唤醒开关", "voice_assistant.open"), ("开语音唤醒开关", "voice_assistant.open"),
    ("请把语音唤醒开关打开", "voice_assistant.open"), ("语音唤醒开关打开", "voice_assistant.open"),
    ("关闭语音唤醒开关", "voice_assistant.close"), ("语音唤醒开关关闭", "voice_assistant.close"),
    ("关语音唤醒开关", "voice_assistant.close"),
    ("打开座椅加热开关", "seat.heating.on"), ("关掉香氛开关", "fragrance.off"),
])
def test_the_noun_switch_is_not_a_direction(text, name):
    result = classify(text)
    assert result is not None and result["name"] == name
