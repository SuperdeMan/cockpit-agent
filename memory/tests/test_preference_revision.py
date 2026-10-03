"""CA2-15 S3a: one current statement per preference dimension; explicit revisions win.

Shapes come from the live store (2026-10-03): eight paraphrases of one coffee habit current at
once, an older inferred "likes spicy food" next to a newer explicit "doesn't eat spicy", and a
child's two different facts that must both stay.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from store import MemoryStore  # noqa: E402

UID = "u-rev"


def _store():
    store = MemoryStore()
    store.url = ""
    store._vstore._dsn = ""
    return store


def _item(text, predicate, *, provenance="user_stated", subject="", polarity="", valid_from=None, kind="semantic"):
    item = {"user_id": UID, "occupant_id": "primary", "kind": kind, "predicate": predicate, "text": text,
            "scope": "profile.taste", "provenance": provenance, "confidence": 0.9, "subject": subject,
            "polarity": polarity}
    if valid_from is not None:
        item["valid_from"] = valid_from
    return item


async def _current(store, prefix=""):
    vs = await store._vec()
    return sorted(v["text"] for v in vs._mem.values()
                  if v["user_id"] == UID and not v["superseded_by"] and v["predicate"].startswith(prefix))


async def _recall(store, **kw):
    return [d["text"] for d, _ in await store.recall(UID, occupant_id="primary", top_k=20, **kw)]


def test_paraphrases_of_one_habit_leave_one_current_statement():
    store = _store()

    async def go():
        for text in ("用户最喜欢喝美式咖啡", "用户喜欢点瑞幸的美式咖啡，冰的", "用户习惯点瑞幸咖啡的美式中杯"):
            await store.remember([_item(text, "taste.coffee")])
        return await _current(store, "taste.coffee")

    assert asyncio.run(go()) == ["用户习惯点瑞幸咖啡的美式中杯"]


def test_an_inference_never_replaces_an_explicit_statement():
    store = _store()

    async def go():
        await store.remember([_item("用户最喜欢喝美式咖啡", "taste.coffee")])
        written = await store.remember([_item("用户常点拿铁", "beverage.coffee", provenance="agent_inferred")])
        return written, await _current(store, "taste.coffee")

    written, current = asyncio.run(go())
    assert written == [] and current == ["用户最喜欢喝美式咖啡"]


def test_an_explicit_statement_replaces_every_current_inference():
    store = _store()

    async def go():
        vs = await store._vec()
        await vs.remember([_item("用户常点拿铁", "taste.coffee", provenance="agent_inferred"),
                           _item("用户常点摩卡", "taste.coffee", provenance="agent_inferred")])
        await store.remember([_item("用户最喜欢喝美式咖啡", "taste.coffee")])
        return await _current(store, "taste.coffee")

    assert asyncio.run(go()) == ["用户最喜欢喝美式咖啡"]


def test_a_newer_explicit_dislike_retires_the_older_inferred_like():
    store = _store()

    async def go():
        await store.remember([_item("用户喜欢川菜/四川火锅", "cuisine.spicy", provenance="agent_inferred")])
        await store.remember([_item("用户不吃辣", "taste.spicy", polarity="dislike")])
        return await _current(store, "taste.spicy")

    assert asyncio.run(go()) == ["用户不吃辣"]


def test_multi_valued_dimensions_keep_distinct_values_and_merge_restatements():
    store = _store()

    async def go():
        await store.remember([_item("用户喜欢川菜/四川火锅", "taste.cuisine")])
        await store.remember([_item("用户也喜欢粤菜", "food.cuisine")])
        await store.remember([_item("用户喜欢川菜和四川火锅", "taste.cuisine")])          # a restatement
        await store.remember([_item("不想再去三立方(南山创维店)", "taste.dislike_place")])
        await store.remember([_item("不想再去星巴克(海岸城店)", "place.avoid")])
        return await _current(store, "taste.cuisine"), await _current(store, "taste.dislike_place")

    cuisine, places = asyncio.run(go())
    assert cuisine == ["用户也喜欢粤菜", "用户喜欢川菜/四川火锅"]
    assert len(places) == 2


def test_facts_only_drop_word_for_word_repeats():
    store = _store()

    async def go():
        for text in ("用户有一个女儿", "用户的女儿在深圳市南山实验小学上学", "用户有一个女儿"):
            await store.remember([_item(text, "person.child")])
        return await _current(store, "person.child")

    assert asyncio.run(go()) == ["用户有一个女儿", "用户的女儿在深圳市南山实验小学上学"]


def test_unknown_predicates_are_single_valued():
    store = _store()

    async def go():
        await store.remember([_item("用户喜欢提拉米苏", "taste.dessert")])
        await store.remember([_item("用户最近不吃甜食", "taste.dessert")])
        return await _current(store, "taste.dessert")

    assert asyncio.run(go()) == ["用户最近不吃甜食"]


def test_reads_fold_legacy_duplicates_without_touching_the_data():
    store = _store()

    async def go():
        vs = await store._vec()
        legacy = [
            _item("用户最喜欢喝美式咖啡", "taste.coffee", valid_from=1),
            _item("用户常点瑞幸美式中杯", "taste.coffee", provenance="agent_inferred", valid_from=2),
            _item("用户习惯点瑞幸咖啡的美式中杯", "taste.coffee", valid_from=3),
            _item("用户习惯点瑞幸咖啡的美式中杯", "taste.coffee", provenance="agent_inferred", valid_from=4),
            _item("用户喜欢川菜/四川火锅", "taste.spicy", provenance="agent_inferred", valid_from=5),
            _item("用户不吃辣", "taste.spicy", polarity="dislike", valid_from=6),
            _item("用户喜欢川菜/四川火锅", "taste.cuisine", valid_from=7),
            _item("用户喜欢川菜和四川火锅", "taste.cuisine", valid_from=8),
            _item("用户平时爱吃川菜", "taste.cuisine", provenance="agent_inferred", valid_from=9),
            _item("用户有一个女儿", "person.child", valid_from=10),
            _item("用户的女儿在深圳市南山实验小学上学", "person.child", valid_from=11),
        ]
        await vs.remember(legacy)
        return await _recall(store), await _recall(store, include_superseded=True), await _current(store)

    folded, history, stored = asyncio.run(go())
    assert sorted(folded) == sorted(["用户习惯点瑞幸咖啡的美式中杯", "用户不吃辣", "用户喜欢川菜和四川火锅", "用户平时爱吃川菜",
                                     "用户有一个女儿", "用户的女儿在深圳市南山实验小学上学"])
    assert len(history) == 11 and len(stored) == 11          # history views and the data itself are untouched


def test_consolidation_follows_the_same_rule():
    store = _store()
    replies = iter([
        json.dumps([{"category": "explicit_preference", "kind": "semantic", "predicate": "climate.temperature",
                     "text": "用户喜欢空调22度", "scope": "profile.comfort", "confidence": 0.9}]),
        json.dumps([{"category": "inferred_preference", "kind": "semantic", "predicate": "hvac.temp",
                     "text": "用户常把空调开到24度", "scope": "profile.comfort", "confidence": 0.9}]),
        json.dumps([{"category": "explicit_preference", "kind": "semantic", "predicate": "ac.temperature",
                     "text": "用户喜欢空调26度", "scope": "profile.comfort", "confidence": 0.9}]),
    ])

    async def llm(messages):
        return next(replies)

    async def go():
        for n, text in enumerate(("记住我喜欢22度", "开到24度", "记住我喜欢26度")):
            await store.append_turn("s1", "user", text, user_id=UID, occupant_id="primary", turn_id=f"t{n}")
            await store.consolidate("s1", UID, "primary", "", complete_fn=llm)
        return await _current(store, "climate.temperature")

    assert asyncio.run(go()) == ["用户喜欢空调26度"]


def test_reads_prefer_an_older_explicit_statement_over_a_newer_inference():
    store = _store()

    async def go():
        vs = await store._vec()
        await vs.remember([
            _item("用户喜欢空调26度", "climate.temperature", valid_from=1),
            _item("用户常把空调开到22度", "hvac.temp", provenance="agent_inferred", valid_from=2),
        ])
        return await _recall(store)

    assert asyncio.run(go()) == ["用户喜欢空调26度"]
