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


# ── Q2 残余（2026-08-19）：两条新判据原语本身也要有尺子 ────────────────────
#
# 判据是尺子，**尺子写错的代价是把读数指向错误的根因**。CD1 原判据只压
# 「不许说未查到」，于是「营业时间从来没进过候选集」这个真缺陷在探针上看不见
# ——真栈那次取样它答的是「你想对这家咖啡店做什么？」，一个排除词都没触发。

def _prior(turn: int, items: list[dict]) -> list[dict]:
    return [{"turn": turn, "card_items_raw": items}]


def _obs(speech: str) -> dict:
    """`_judge` 直接索引 `actions`/`speech`——**观测装置必须是完整形状**。

    首版只给了 `speech`，五条用例齐红在 `KeyError: 'actions'`，
    读起来像新判据写坏了。同 `_Spy` 那次少喂一条执行路径：
    **装置不完整，读数就会指向错误的根因。**
    """
    return {"speech": speech, "actions": [], "card_text": "",
            "card_item_count": 0, "card_items": [], "card_items_raw": []}


_CARD = [
    {"name": "甲咖啡", "open_today": "07:00-21:00", "price": "18.00"},
    {"name": "乙咖啡", "open_today": "10:00-01:00", "price": "15.60"},
]


def test_latest_closing_expectation_is_computed_from_the_card():
    """期望值**从卡片算出来**，不写死——换一批 POI 也要判得对。"""
    fails = probe._judge({"latest_closing_from": 1},
                         _obs("「乙咖啡」营业到次日 01:00。"),
                         _prior(1, _CARD), [])
    assert fails == []


def test_naming_the_wrong_one_is_a_failure():
    """点到了不是最晚的那家 ⇒ 红。跨零点必须赢过 21:00。"""
    fails = probe._judge({"latest_closing_from": 1},
                         _obs("甲咖啡营业到 21:00。"),
                         _prior(1, _CARD), [])
    assert fails and "最晚关门" in fails[0]


def test_saying_nothing_specific_is_also_a_failure():
    """真栈那次的原样回答——**它没说错话，但它没答**。原判据会放它过去。"""
    fails = probe._judge({"latest_closing_from": 1},
                         _obs("你想对这家咖啡店做什么？"),
                         _prior(1, _CARD), [])
    assert fails and "一家都没点到" in fails[0]


def test_missing_hours_is_a_note_not_a_silent_pass():
    """卡上不足两项带营业时间 ⇒ **出提示，不判绿**。

    一个什么都没证明的样本不该被当成证据（同 `not_names_item_from` 那条纪律）。
    """
    notes: list[str] = []
    fails = probe._judge({"latest_closing_from": 1},
                         _obs("随便说点什么"),
                         _prior(1, [{"name": "甲"}, {"name": "乙"}]), notes)
    assert fails == []
    assert notes and "不构成证据" in notes[0]


def test_sum_expectation_is_computed_and_accepts_equivalent_forms():
    total = probe._judge({"sums_from": {"turn": 1, "indices": [1, 2]}},
                         _obs("一共 33.60 元。"), _prior(1, _CARD), [])
    assert total == []
    # 等价写法也认（33.6 == 33.60），但**近似值不认**
    assert probe._judge({"sums_from": {"turn": 1, "indices": [1, 2]}},
                        _obs("一共 33.6 元。"), _prior(1, _CARD), []) == []
    wrong = probe._judge({"sums_from": {"turn": 1, "indices": [1, 2]}},
                         _obs("大概 33 元左右。"), _prior(1, _CARD), [])
    assert wrong and "正确合计" in wrong[0]


def test_closing_minute_handles_the_real_field_names():
    """这张键表**从产生方派生**：`open_today` / `open_week`，不是猜的 `open_hours`。

    ⚠ 探针**刻意不 import 生产实现**（`runtime.openhours`）：尺子与被测系统同源
    就会一起错、一起绿。这份是独立的第二实现，两边对不上时才有信号。
    """
    assert probe._closing_minute({"open_today": "10:00-22:00"}) == 22 * 60
    assert probe._closing_minute({"open_week": "周一至周日 10:00-23:00"}) == 23 * 60
    assert probe._closing_minute({"open_today": "17:00-02:00"}) == 26 * 60
    assert probe._closing_minute({"open_today": "24小时"}) == 24 * 60
    assert probe._closing_minute({"name": "无营业信息"}) is None
