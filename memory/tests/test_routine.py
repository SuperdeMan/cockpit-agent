"""程序记忆雏形单测（P3）：routine 检测 + derive 写 procedural（去重）。纯内存。"""
import asyncio
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
