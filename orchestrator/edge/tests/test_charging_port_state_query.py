"""充电口盖开关现状查询 `charging_port.query`（2026-10-16，核心旅程 K12；200×5 后续批 §18.5）。

修前：「打开充电口」确认之后问「充电口现在开着吗」，没有任何能力可派——标定时规划器拿「查询香氛」顶替答成「香氛关着」，
`bf103d15` 起改成诚实答看不了。照香氛查询（`370d5eab`）的模板补上，五段链路逐段钉住：原话 → 意图 → 对象 / 操作 → VAL（只读、
读不到如实说）→ 话术。充电口是需确认对象，这里多钉一条：**只读查询不挂确认**（对象级 `require_confirm` 管的是开 / 关），
开 / 关照旧要确认、没确认照旧拒绝执行。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import classify, classify_structured, is_local  # noqa: E402
from val import VAL  # noqa: E402

from orchestrator.edge.tests.test_fragrance_state_query import _handle, _service  # noqa: E402


def _query(v: VAL):
    return v.execute({"domain": "query", "intent": "query",
                      "data": {"object": "charging_port", "operate": "query"}})


def _open(v: VAL, *, confirmed: bool):
    return v.execute({"domain": "setting", "intent": "control",
                      "data": {"object": "charging_port", "operate": "open"}}, confirmed=confirmed)


# ── 原话 → 意图 ────────────────────────────────────────────────────────────

def test_state_question_is_a_local_read_not_an_open():
    for text in ("充电口现在开着吗", "帮我看看充电口开着吗", "充电口是开着还是关着"):
        s = classify_structured(text)
        assert s is not None
        assert (s["intent"], s["data"]["operate"], s["data"]["object"]) == ("query", "query", "charging_port"), text
    assert classify("充电口现在开着吗")["name"] == "charging_port.query"
    assert is_local("charging_port.query")


def test_commands_still_open_and_close():
    assert classify("打开充电口")["name"] == "charging_port.open"
    assert classify("关闭充电口")["name"] == "charging_port.close"


# ── VAL：只读、不挂确认、读车态、不编 ──────────────────────────────────────

def test_query_reads_the_state_without_changing_it_or_asking_to_confirm():
    v = VAL()
    before = dict(v.state)
    ok, speech = _query(v)
    assert ok is True and "关着" in speech and "开着" not in speech and "确认" not in speech
    assert v.state == before


def test_query_after_a_confirmed_open_says_open():
    v = VAL()
    assert _open(v, confirmed=True)[0] is True
    ok, speech = _query(v)
    assert ok is True and "开着" in speech and "关着" not in speech
    assert speech.endswith("（模拟车读数）")


def test_query_without_a_reading_does_not_guess():
    v = VAL()
    v.state.pop("charging_port", None)
    ok, speech = _query(v)
    assert ok is True and "读不到" in speech
    assert "开着" not in speech and "关着" not in speech


def test_open_still_needs_the_confirmation():
    v = VAL()
    assert v._need_confirm("charging_port", "open") and v._need_confirm("charging_port", "close")
    assert not v._need_confirm("charging_port", "query")
    ok, _speech = _open(v, confirmed=False)
    assert ok is False and v.state["charging_port"] == "closed"


def test_capability_is_declared_read_only():
    from capabilities import _effect_for

    objects = (VAL().commands or {}).get("objects") or {}
    assert "query" in objects["charging_port"]["operates"]
    assert "charging_port.query" in objects["charging_port"]["edge_intents"]
    assert _effect_for("charging_port.query", objects) == "read"
    assert _effect_for("charging_port.open", objects) == "write"


# ── 端侧服务：本地秒回、不回传动作；开照旧不走本地 ──────────────────────────

def test_state_question_answers_locally_without_an_action(monkeypatch):
    service, turns = _service(monkeypatch)
    before = dict(service.val.state)
    finals = _handle(service, "充电口现在开着吗", "port-query")
    assert len(finals) == 1
    assert "充电口" in finals[0].speech and "关着" in finals[0].speech and not finals[0].need_confirm
    assert list(finals[0].actions) == []
    assert turns[0]["path"] == "local" and turns[0]["intents"] == ["charging_port.query"]
    assert service.val.state == before


def test_opening_is_still_not_answered_locally(monkeypatch):
    """危险动作不走本地秒回（`_confirm_required`）：开充电口照旧上云经确认闭环——这里落到云端桩。"""
    service, _turns = _service(monkeypatch)
    finals = _handle(service, "打开充电口", "port-open")
    assert finals and finals[-1].speech == "〔云端桩〕"
    assert service.val.state["charging_port"] == "closed"
