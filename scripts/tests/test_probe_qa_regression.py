from __future__ import annotations

import asyncio
import json

from scripts import probe_qa_regression as probe


def _action(name: str) -> dict:
    return {"type": name, "payload": {"command": name}}


class _Socket:
    def __init__(self, messages: list[dict]):
        self._messages = iter(messages)
        self.sent: list[dict] = []

    async def send(self, payload: str):
        self.sent.append(json.loads(payload))

    async def recv(self):
        try:
            return json.dumps(next(self._messages), ensure_ascii=False)
        except StopIteration:
            await asyncio.sleep(60)
            raise AssertionError("unreachable")


def test_one_turn_merges_the_local_and_cloud_finals(monkeypatch):
    monkeypatch.setattr(probe, "_TAIL_IDLE_S", 0.001)
    monkeypatch.setattr(probe, "_TAIL_BUDGET_S", 0.1)
    monkeypatch.setattr(probe, "TIMEOUT", 0.1)
    socket = _Socket(
        [
            {
                "type": "final",
                "speech": "本地完成",
                "actions": [_action("hvac.off")],
                "need_confirm": True,
                "operation_id": "local-op",
            },
            {"type": "progress", "text": "云端处理中"},
            {
                "type": "final",
                "speech": "云端完成？",
                "actions": [_action("hvac.on")],
                "need_confirm": False,
                "operation_id": "cloud-op",
            },
        ]
    )

    observed = asyncio.run(probe._one_turn(socket, "session-1", "按顺序执行"))

    assert socket.sent == [
        {
            "text": "按顺序执行",
            "session_id": "session-1",
            "meta": dict(probe.PROBE_META),
        }
    ]
    assert observed["actions"] == ["hvac.off", "hvac.on"]
    assert observed["speech"] == "本地完成\n云端完成？"
    assert observed["is_question"] is True
    assert observed["need_confirm"] is True
    assert observed["operation_id"] == "local-op"


def test_one_turn_returns_a_single_final_after_the_idle_window(monkeypatch):
    monkeypatch.setattr(probe, "_TAIL_IDLE_S", 0.001)
    monkeypatch.setattr(probe, "_TAIL_BUDGET_S", 0.1)
    monkeypatch.setattr(probe, "TIMEOUT", 0.1)
    socket = _Socket(
        [{"type": "final", "speech": "完成", "actions": [_action("sunroof.open")]}]
    )

    observed = asyncio.run(probe._one_turn(socket, "session-2", "打开天窗"))

    assert observed["actions"] == ["sunroof.open"]
    assert observed["speech"] == "完成"
    assert observed["is_question"] is False


def test_merge_finals_only_fills_empty_primary_semantics():
    first = {
        "speech": "第一段",
        "actions": ["hvac.off"],
        "need_confirm": True,
        "operation_id": "primary-op",
        "card_type": "confirm",
        "closed_operation_ids": ["old-op"],
        "card_text": '{"type":"confirm"}',
    }
    later = {
        "speech": "第二段",
        "actions": ["hvac.on"],
        "need_confirm": False,
        "operation_id": "secondary-op",
        "card_type": "result",
        "closed_operation_ids": ["other-op"],
        "card_text": '{"type":"result"}',
    }

    merged = probe._merge_finals(first, later)

    assert merged["actions"] == ["hvac.off", "hvac.on"]
    assert merged["speech"] == "第一段\n第二段"
    assert merged["need_confirm"] is True
    assert merged["operation_id"] == "primary-op"
    assert merged["card_type"] == "confirm"
    assert merged["closed_operation_ids"] == ["old-op"]
    assert merged["card_text"] == '{"type":"confirm"}'
