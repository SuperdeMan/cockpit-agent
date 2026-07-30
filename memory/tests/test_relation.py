"""关系边单测（M2 记忆图谱 P1）：封闭词表 / 一跳解析 / **GDPR 级联删除（红线）**。

关系边是本轮唯一新增的表与隐私面。三条硬要求：
1. **rel 封闭词表**——词表外一律丢弃，不猜（`predicate_class` 别名爆炸的教训）；
2. **查不到/有歧义一律不返回**——导航到错地方比查不到更糟，调用方须诚实追问；
3. **GDPR 硬删必须级联**（子 RFC §5 红线）——只删 memory_item 的话，家人关系与孩子学校
   会留在库里，那是假删除。
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import relation as R  # noqa: E402
from store import MemoryStore  # noqa: E402


# ── 封闭词表与归一 ────────────────────────────────────────────────────────

def test_vocab_rels_pass_through():
    for rel in R.REL_VOCAB:
        assert R.normalize_rel(rel) == rel
        assert R.is_valid(rel)


def test_known_aliases_normalize():
    assert R.normalize_rel("school") == R.REL_PLACE_OF
    assert R.normalize_rel("studies_at") == R.REL_PLACE_OF
    assert R.normalize_rel("relative") == R.REL_FAMILY
    assert R.normalize_rel("COMPANY") == R.REL_WORKS_AT       # 大小写不敏感


def test_unknown_rel_is_rejected_not_guessed():
    """词表外返回空串 → 调用方丢弃。不做模糊匹配、不兜底猜测。"""
    assert R.normalize_rel("是我的好朋友的邻居") == ""
    assert R.normalize_rel("friend_of_friend") == ""
    assert not R.is_valid("")


def test_candidate_requires_all_three_parts():
    assert R.normalize_candidate({"subject": "小雨", "rel": "family"}) is None
    assert R.normalize_candidate({"rel": "family", "object": "女儿"}) is None
    assert R.normalize_candidate({"subject": "小雨", "object": "女儿"}) is None
    assert R.normalize_candidate({"subject": " ", "rel": "family",
                                  "object": "女儿"}) is None
    assert R.normalize_candidate("not a dict") is None


def test_candidate_rejects_sentence_length_entities():
    """明显是句子不是实体名 → 丢（LLM 偶尔把整句塞进 subject）。"""
    assert R.normalize_candidate({"subject": "我" * 50, "rel": "family",
                                  "object": "女儿"}) is None


def test_candidate_normalizes_and_defaults():
    edge = R.normalize_candidate({"subject": " 小雨 ", "rel": "school",
                                  "object": " XX小学 "})
    assert edge["subject"] == "小雨" and edge["object"] == "XX小学"
    assert edge["rel"] == R.REL_PLACE_OF
    assert edge["privacy_level"] == "sensitive"     # 家人/地点默认敏感
    assert edge["confidence"] == 1.0


def test_candidate_clamps_garbage_confidence():
    assert R.normalize_candidate({"subject": "a", "rel": "owns", "object": "b",
                                  "confidence": "abc"})["confidence"] == 1.0
    assert R.normalize_candidate({"subject": "a", "rel": "owns", "object": "b",
                                  "confidence": 5})["confidence"] == 1.0


# ── 人称词与亲属同义 ─────────────────────────────────────────────────────

def test_find_person_word_prefers_longest():
    """「女儿」不能被「儿」截断。"""
    assert R.find_person_word("去接女儿放学") == "女儿"
    assert R.find_person_word("帮我接一下孩子") == "孩子"
    assert R.find_person_word("导航去公司") == ""


def test_generic_kinship_covers_specific():
    """**解析成功率的关键**：存的时候说「我女儿叫小雨」，用的时候说「去接孩子」。"""
    assert "女儿" in R.kinship_aliases("孩子")
    assert "儿子" in R.kinship_aliases("孩子")
    assert "妻子" in R.kinship_aliases("老婆")


def test_kinship_normalization_strips_natural_language():
    """**真栈首验实测**：LLM 抽出来是「用户的女儿」，而查询侧是精确匹配（刻意的，
    防「小雨」匹到「小雨点」）→ 存成变体就永远查不到。入库时归一到裸称谓。"""
    assert R.normalize_kinship("用户的女儿") == "女儿"
    assert R.normalize_kinship("我女儿") == "女儿"
    assert R.normalize_kinship("他儿子") == "儿子"
    assert R.normalize_kinship("阳光小学") == "阳光小学"    # 非亲属原样


def test_family_candidate_object_is_normalized():
    edge = R.normalize_candidate({"subject": "小雨", "rel": "family",
                                  "object": "用户的女儿"})
    assert edge["object"] == "女儿"


def test_non_family_object_not_normalized():
    edge = R.normalize_candidate({"subject": "小雨", "rel": "place_of",
                                  "object": "我的母校阳光小学"})
    assert edge["object"] == "我的母校阳光小学"


def test_specific_kinship_maps_to_own_group():
    assert "闺女" in R.kinship_aliases("女儿")
    assert R.kinship_aliases("") == ()


# ── 存储与一跳解析 ───────────────────────────────────────────────────────

def _store() -> MemoryStore:
    return MemoryStore()


async def _seed(store, edges):
    vs = await store._vec()
    return await vs.add_relations("u1", edges)


def test_add_and_query_relations():
    async def go():
        store = _store()
        await _seed(store, [{"subject": "小雨", "rel": "family", "object": "女儿"},
                            {"subject": "小雨", "rel": "school", "object": "XX小学"}])
        return (await store.relations("u1", subject="小雨"),
                await store.relations("u1", rel="place_of"),
                await store.relations("u1", object_="女儿"))
    by_subject, by_rel, by_object = asyncio.run(go())
    assert len(by_subject) == 2
    assert len(by_rel) == 1 and by_rel[0]["object"] == "XX小学"
    assert len(by_object) == 1        # 双向查：按 object 也能查到


def test_invalid_edges_are_dropped_on_write():
    async def go():
        store = _store()
        ids = await _seed(store, [{"subject": "小雨", "rel": "好朋友", "object": "小明"},
                                  {"subject": "", "rel": "family", "object": "女儿"}])
        return ids, await store.relations("u1")
    ids, rows = asyncio.run(go())
    assert ids == [] and rows == []


def test_duplicate_edge_not_stacked():
    async def go():
        store = _store()
        e = [{"subject": "小雨", "rel": "family", "object": "女儿"}]
        await _seed(store, e)
        await _seed(store, e)
        return await store.relations("u1")
    assert len(asyncio.run(go())) == 1


def test_resolve_person_place_one_hop():
    """「去接孩子放学」的核心链路：孩子 → family(小雨-女儿) → place_of(小雨-XX小学)。"""
    async def go():
        store = _store()
        await _seed(store, [{"subject": "小雨", "rel": "family", "object": "女儿"},
                            {"subject": "小雨", "rel": "place_of", "object": "XX小学"}])
        return await store.resolve_person_place("u1", "孩子")
    hit = asyncio.run(go())
    assert hit == {"person": "小雨", "place": "XX小学", "object_ref": ""}


def test_resolve_returns_none_when_person_unknown():
    async def go():
        return await _store().resolve_person_place("u1", "孩子")
    assert asyncio.run(go()) is None


def test_resolve_returns_none_when_place_unknown():
    """知道谁但不知道在哪 → 不返回，让调用方诚实追问。"""
    async def go():
        store = _store()
        await _seed(store, [{"subject": "小雨", "rel": "family", "object": "女儿"}])
        return await store.resolve_person_place("u1", "孩子")
    assert asyncio.run(go()) is None


def test_resolve_returns_none_on_ambiguity():
    """两个孩子 → 有歧义就不猜（导航到错学校比查不到更糟）。"""
    async def go():
        store = _store()
        await _seed(store, [
            {"subject": "小雨", "rel": "family", "object": "女儿"},
            {"subject": "小明", "rel": "family", "object": "儿子"},
            {"subject": "小雨", "rel": "place_of", "object": "XX小学"},
            {"subject": "小明", "rel": "place_of", "object": "YY小学"},
        ])
        return await store.resolve_person_place("u1", "孩子")
    assert asyncio.run(go()) is None


def test_resolve_none_for_non_person_word():
    async def go():
        return await _store().resolve_person_place("u1", "公司")
    assert asyncio.run(go()) is None


# ── GDPR 级联删除（红线）─────────────────────────────────────────────────

def test_forget_user_cascades_to_relations():
    """**红线**：全量硬删必须连关系边一起删——只删 memory_item 的话，家人关系与
    孩子学校（最敏感的那部分）会留在库里，那是假删除。"""
    async def go():
        store = _store()
        vs = await store._vec()
        await vs.remember([{"user_id": "u1", "text": "用户喜欢吃辣",
                            "predicate": "taste.spicy"}])
        await _seed(store, [{"subject": "小雨", "rel": "family", "object": "女儿"},
                            {"subject": "小雨", "rel": "place_of", "object": "XX小学"}])
        await store.forget_user("u1")
        return (await vs.export("u1"), await store.relations("u1"))
    memories, relations = asyncio.run(go())
    assert memories == [], "记忆条目未删干净"
    assert relations == [], "关系边残留 = GDPR 假删除"


def test_forget_other_user_does_not_touch_relations():
    async def go():
        store = _store()
        await _seed(store, [{"subject": "小雨", "rel": "family", "object": "女儿"}])
        await store.forget_user("u2")
        return await store.relations("u1")
    assert len(asyncio.run(go())) == 1


def test_scoped_forget_keeps_relation_graph():
    """按 scope 删一部分记忆，不该连带清空整张关系图（它没有 scope 维度）。"""
    async def go():
        store = _store()
        vs = await store._vec()
        await vs.remember([{"user_id": "u1", "text": "口味", "scope": "profile.taste"}])
        await _seed(store, [{"subject": "小雨", "rel": "family", "object": "女儿"}])
        await store.forget_user("u1", scopes=["profile.taste"])
        return await vs.export("u1"), await store.relations("u1")
    memories, relations = asyncio.run(go())
    assert memories == [] and len(relations) == 1


def test_export_includes_relations():
    """导出必须与删除对称：能被删掉的东西，用户有权先看到。"""
    async def go():
        store = _store()
        await _seed(store, [{"subject": "小雨", "rel": "family", "object": "女儿"}])
        return await store.export_user("u1")
    data = asyncio.run(go())
    assert "relations" in data and len(data["relations"]) == 1


# ── 抽取分流 ─────────────────────────────────────────────────────────────

def test_extract_prompt_fixes_family_direction_and_multi_relation_completeness():
    from extract import _SYSTEM

    assert "subject 必须是家人成员的名字" in _SYSTEM
    assert "object 必须是用户说出的亲属称谓" in _SYSTEM
    assert "绝不能把 object 写成用户/我" in _SYSTEM
    assert "小雨-family-女儿" in _SYSTEM
    assert "小雨-place_of-阳光小学" in _SYSTEM
    assert "多个明说关系时逐条完整输出" in _SYSTEM


def test_extract_routes_relation_candidates():
    """关系候选走 `_relation` 保留键分流，不混进 memory_item。"""
    from extract import extract

    async def _fake(messages, **kw):
        return json.dumps([
            {"category": "relation", "subject": "小雨", "rel": "family",
             "object": "女儿"},
            {"category": "explicit_preference", "predicate": "taste.spicy",
             "text": "用户喜欢吃辣", "scope": "profile.taste"},
        ], ensure_ascii=False)

    async def go():
        return await extract([{"role": "user", "text": "我女儿叫小雨，她喜欢吃辣"}],
                             user_id="u1", complete_fn=_fake)
    cands = asyncio.run(go())
    edges = [c["_relation"] for c in cands if c.get("_relation")]
    items = [c for c in cands if not c.get("_relation")]
    assert len(edges) == 1 and edges[0]["subject"] == "小雨"
    assert len(items) == 1 and items[0]["predicate"] == "taste.spicy"


def test_extract_drops_out_of_vocab_relation():
    from extract import extract

    async def _fake(messages, **kw):
        return json.dumps([{"category": "relation", "subject": "小雨",
                            "rel": "是我邻居的同事", "object": "小明"}],
                          ensure_ascii=False)

    async def go():
        return await extract([{"role": "user", "text": "..."}],
                             user_id="u1", complete_fn=_fake)
    assert asyncio.run(go()) == []


def test_consolidate_writes_relations_to_graph_not_items():
    from extract import extract  # noqa: F401  （确保模块可导入）

    async def _fake(messages, **kw):
        return json.dumps([{"category": "relation", "subject": "小雨",
                            "rel": "place_of", "object": "XX小学"}],
                          ensure_ascii=False)

    async def go():
        store = _store()
        await store.append_turn("s1", "user", "小雨在XX小学上学")
        ids = await store.consolidate("s1", "u1", complete_fn=_fake)
        vs = await store._vec()
        return ids, await vs.export("u1"), await store.relations("u1")
    ids, memories, relations = asyncio.run(go())
    assert ids == [] and memories == []          # 不进 memory_item
    assert len(relations) == 1                   # 进关系图
