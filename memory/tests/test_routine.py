"""程序记忆雏形单测（P3）：routine 检测 + derive 写 procedural（去重）。纯内存。"""
import asyncio

import pytest
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routine import detect_routines, _hour_bucket  # noqa: E402
from store import MemoryStore  # noqa: E402


def _ep(action, place, hour):
    return {"value_json": json.dumps({"action": action, "place": place, "hour": hour},
                                     ensure_ascii=False)}


def test_detect_routines_threshold():
    eps = [_ep("买咖啡", "公司附近星巴克", 8) for _ in range(3)]
    out = detect_routines(eps, min_count=3)
    assert len(out) == 1
    r = out[0]
    assert r["kind"] == "procedural"
    assert r["predicate"].startswith("routine.买咖啡")
    assert "早上" in r["text"] and "星巴克" in r["text"]
    assert r["suggestion"]
    # 不足阈值 → 不产出
    assert detect_routines(eps[:2], min_count=3) == []


def test_detect_routines_ignores_unstructured():
    assert detect_routines([{"text": "随便聊聊"}], min_count=1) == []


def test_hour_bucket():
    assert _hour_bucket(8) == "早上" and _hour_bucket(12) == "中午"
    assert _hour_bucket(20) == "晚上" and _hour_bucket(2) == "深夜"


def _store() -> MemoryStore:
    s = MemoryStore()
    s.url = ""
    s._vstore._dsn = ""
    return s


def test_derive_routines_writes_procedural_and_dedups():
    store = _store()

    async def go():
        for _ in range(3):
            await store.remember([{
                "user_id": "u1", "kind": "episodic", "text": "在公司附近星巴克买咖啡",
                "scope": "episodic.general",
                "value_json": json.dumps({"action": "买咖啡", "place": "公司附近星巴克",
                                          "hour": 8}, ensure_ascii=False)}])
        first = await store.derive_routines("u1", min_count=3)
        second = await store.derive_routines("u1", min_count=3)  # 已沉淀 → 去重
        exported = await store.export_user("u1")
        return first, second, exported

    first, second, exported = asyncio.run(go())
    assert len(first) == 1 and first[0]["suggestion"]
    assert second == []  # 不重复沉淀
    kinds = [m["kind"] for m in exported["memories"]]
    assert kinds.count("procedural") == 1 and kinds.count("episodic") == 3


# ── M2 P2：routine 时间加权（旧习惯自然沉底）────────────────────────────

def _ev(action, place, hour, ts=0):
    return {"value_json": json.dumps({"action": action, "place": place, "hour": hour}),
            "source_ts": ts}


def test_legacy_events_without_ts_behave_as_before():
    """存量事件没有 source_ts → 全额计入，行为与加权前逐字一致。"""
    evs = [_ev("买咖啡", "星巴克", 8) for _ in range(3)]
    assert len(detect_routines(evs, min_count=3)) == 1


def test_recent_routine_still_detected():
    now = 1_800_000_000
    evs = [_ev("买咖啡", "星巴克", 8, now - i * 86400) for i in range(3)]
    assert len(detect_routines(evs, min_count=3, now=now)) == 1


def test_stale_routine_sinks_below_threshold():
    """**本卡语义**：裸频次够（真发生过 3 次）但已凉透 → 有效计数跌破 recency 门槛，
    不再骚扰用户。"""
    now = 1_800_000_000
    old = now - 120 * 86400          # 4 个月前（半衰期 30 天 → 衰减到 ~6%）
    evs = [_ev("买咖啡", "星巴克", 8, old - i * 86400) for i in range(3)]
    assert detect_routines(evs, min_count=3, now=now) == []


def test_many_stale_events_can_still_qualify():
    """够多的旧事件仍能达标——衰减是降权不是一刀切（十几次两个月前的习惯仍算习惯）。"""
    now = 1_800_000_000
    old = now - 60 * 86400           # 2 个月前 → 每条约 0.25
    evs = [_ev("买咖啡", "星巴克", 8, old) for _ in range(16)]
    assert len(detect_routines(evs, min_count=3, now=now)) == 1


def test_effective_count_recorded_for_debug():
    now = 1_800_000_000
    evs = [_ev("买咖啡", "星巴克", 8, now) for _ in range(3)]
    v = json.loads(detect_routines(evs, min_count=3, now=now)[0]["value_json"])
    assert v["count"] == 3 and v["effective_count"] == pytest.approx(3.0, abs=0.01)
