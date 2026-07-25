"""巩固期加权与召回打分接线单测（M2 记忆图谱 P0）。

驱动真实 MemoryStore 的内存兜底路径（无 PG、无 embedding，lexical 召回），
验证「同一偏好复现 → 就地加强、不新增条目、不刷新衰减基准」这条链。
"""
import asyncio
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from store import MemoryStore  # noqa: E402
import weighting as W  # noqa: E402

DAY = 86400


def _store() -> MemoryStore:
    return MemoryStore()


async def _turns(store, session_id, texts):
    for t in texts:
        await store.append_turn(session_id, "user", t)


def _fake_llm(candidates):
    async def _complete(messages, **kw):
        return json.dumps({"items": candidates}, ensure_ascii=False)
    return _complete


_SPICY = [{"category": "explicit_preference", "predicate": "taste.spicy",
           "text": "用户喜欢吃辣", "scope": "profile.taste", "confidence": 0.9}]


def test_new_preference_gets_initial_weight():
    async def go():
        store = _store()
        await _turns(store, "s1", ["记住，我喜欢吃辣"])
        ids = await store.consolidate("s1", "u1", complete_fn=_fake_llm(_SPICY))
        assert ids
        vs = await store._vec()
        item = await vs.current_by_predicate("u1", "primary", "taste.spicy")
        return item
    item = asyncio.run(go())
    assert item["weight"] > 0                       # 参与加权
    assert item["evidence_count"] >= 1
    assert item["kind"] == "semantic"


def test_repeat_reinforces_instead_of_skipping():
    """**本卡核心**：同一偏好第二次出现要加权，而不是像今天那样静默跳过。"""
    async def go():
        store = _store()
        await _turns(store, "s1", ["记住，我喜欢吃辣"])
        await store.consolidate("s1", "u1", complete_fn=_fake_llm(_SPICY))
        vs = await store._vec()
        first = dict(await vs.current_by_predicate("u1", "primary", "taste.spicy"))

        await _turns(store, "s2", ["我还是喜欢吃辣"])
        ids2 = await store.consolidate("s2", "u1", complete_fn=_fake_llm(_SPICY))
        second = dict(await vs.current_by_predicate("u1", "primary", "taste.spicy"))
        all_items = [v for v in vs._mem.values() if v["predicate"] == "taste.spicy"]
        return first, second, ids2, all_items
    first, second, ids2, all_items = asyncio.run(go())
    assert ids2 == []                               # 等价 → 不新增条目
    assert len(all_items) == 1                      # 库里仍只有一条
    assert second["evidence_count"] > first["evidence_count"]
    assert second["weight"] >= first["weight"]
    assert second["id"] == first["id"]              # 就地更新，不换身份


def test_reinforce_does_not_reset_decay_baseline():
    """刷新 valid_from 等于把陈年偏好洗成新的——刻意不做。"""
    async def go():
        store = _store()
        await _turns(store, "s1", ["记住，我喜欢吃辣"])
        await store.consolidate("s1", "u1", complete_fn=_fake_llm(_SPICY))
        vs = await store._vec()
        item = await vs.current_by_predicate("u1", "primary", "taste.spicy")
        vs._mem[item["id"]]["valid_from"] = int(time.time()) - 200 * DAY
        old_from = vs._mem[item["id"]]["valid_from"]

        await _turns(store, "s2", ["我还是喜欢吃辣"])
        await store.consolidate("s2", "u1", complete_fn=_fake_llm(_SPICY))
        return old_from, vs._mem[item["id"]]["valid_from"]
    old_from, new_from = asyncio.run(go())
    assert new_from == old_from


def test_conflict_inherits_evidence_chain():
    """冲突 supersede 时新条目要继承旧证据链（子 RFC §5 溯源强制项）。"""
    async def go():
        store = _store()
        await _turns(store, "s1", ["记住，我喜欢吃辣"])
        await store.consolidate("s1", "u1", complete_fn=_fake_llm(_SPICY))
        vs = await store._vec()
        old = await vs.current_by_predicate("u1", "primary", "taste.spicy")
        vs._mem[old["id"]]["source_turn_ids"] = "turn-a,turn-b"

        changed = [dict(_SPICY[0], text="用户不吃辣了")]
        await _turns(store, "s2", ["我现在不吃辣了"])
        await store.consolidate("s2", "u1", complete_fn=_fake_llm(changed))
        new = await vs.current_by_predicate("u1", "primary", "taste.spicy")
        return old, new
    old, new = asyncio.run(go())
    assert new["id"] != old["id"] and new["text"] == "用户不吃辣了"
    assert "turn-a" in new["source_turn_ids"] and "turn-b" in new["source_turn_ids"]
    assert new["evidence_count"] >= 2


def test_weighting_off_restores_pre_m2_behaviour(monkeypatch):
    """env 一键回退：MEMORY_WEIGHTING=off → 不写 weight、不加强（逐字回 M2 前）。"""
    monkeypatch.setenv("MEMORY_WEIGHTING", "off")

    async def go():
        store = _store()
        await _turns(store, "s1", ["记住，我喜欢吃辣"])
        await store.consolidate("s1", "u1", complete_fn=_fake_llm(_SPICY))
        vs = await store._vec()
        first = dict(await vs.current_by_predicate("u1", "primary", "taste.spicy"))
        await _turns(store, "s2", ["我还是喜欢吃辣"])
        await store.consolidate("s2", "u1", complete_fn=_fake_llm(_SPICY))
        second = dict(await vs.current_by_predicate("u1", "primary", "taste.spicy"))
        return first, second
    first, second = asyncio.run(go())
    assert first["weight"] == 0                     # 不写 weight
    assert second["evidence_count"] == first["evidence_count"]   # 不加强


def test_recall_ranks_reinforced_preference_higher():
    """召回排序真的用上了 weight：反复印证的偏好压过只说过一次的。"""
    async def go():
        store = _store()
        vs = await store._vec()
        await vs.remember([
            {"user_id": "u1", "kind": "semantic", "predicate": "taste.spicy",
             "text": "用户喜欢吃辣的菜", "scope": "profile.taste",
             "provenance": "user_stated", "confidence": 0.9},
            {"user_id": "u1", "kind": "semantic", "predicate": "taste.sichuan",
             "text": "用户常点辣的川菜", "scope": "profile.taste",
             "provenance": "agent_inferred", "confidence": 0.4,
             "evidence_count": 8,
             "weight": W.compute_weight(provenance="agent_inferred",
                                        evidence_count=8)},
        ])
        return await vs.recall("u1", query="辣", top_k=5)
    hits = asyncio.run(go())
    assert hits, "召回不应为空"
    top_predicate = hits[0][0]["predicate"]
    assert top_predicate == "taste.sichuan"         # 高权反超（0.7 > 0.6）


def test_recall_legacy_items_unaffected():
    """存量条目（weight=0）打分逐字回到 confidence，不因加权机制上线而漂移。"""
    async def go():
        store = _store()
        vs = await store._vec()
        await vs.remember([
            {"user_id": "u1", "kind": "semantic", "predicate": "a.x",
             "text": "老条目甲", "confidence": 0.9, "weight": 0},
            {"user_id": "u1", "kind": "semantic", "predicate": "a.y",
             "text": "老条目乙", "confidence": 0.3, "weight": 0},
        ])
        return await vs.recall("u1", query="老条目", top_k=5)
    hits = asyncio.run(go())
    assert [h[0]["predicate"] for h in hits][:2] == ["a.x", "a.y"]   # 按 confidence 排
