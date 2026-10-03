"""CA2-15 residuals: evidence counts occasions; one person under one subject.

Live data (2026-10-03): every preference reached the weight cap on its first write, because the
evidence count was the number of turns in the extraction window (8-12) — so every explicit item
read 「常用」 and frequency meant nothing. And one daughter was stored under two subjects (the
kinship word and her name), so her two preferences never met in one dimension.
The PostgreSQL paths of these rules were also run once against an embedded pgvector server.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from store import MemoryStore  # noqa: E402
import weighting  # noqa: E402

UID = "u-quality"


def _store():
    store = MemoryStore()
    store.url = ""
    store._vstore._dsn = ""
    return store


def _item(text, predicate, **kw):
    out = {"user_id": UID, "occupant_id": "primary", "kind": "semantic", "predicate": predicate, "text": text,
           "scope": "profile.taste", "provenance": "user_stated", "confidence": 0.9}
    out.update(kw)
    return out


async def _row(store, predicate):
    vs = await store._vec()
    return (await vs.current_in_dimension(UID, "primary", [predicate]))[0]


def test_a_new_preference_counts_one_occasion_whatever_the_window_size():
    store = _store()
    reply = json.dumps([{"category": "explicit_preference", "kind": "semantic", "predicate": "climate.temperature",
                         "text": "用户喜欢空调26度", "scope": "profile.comfort", "confidence": 0.9}])

    async def llm(messages):
        return reply

    async def go():
        for n in range(6):
            await store.append_turn("s1", "user", f"第{n}句：记住我喜欢26度", user_id=UID, turn_id=f"t{n}")
        await store.consolidate("s1", UID, "primary", "", complete_fn=llm)
        first = dict(await _row(store, "climate.temperature"))
        await store.consolidate("s1", UID, "primary", "", complete_fn=llm)      # overlapping window
        same_session = dict(await _row(store, "climate.temperature"))
        await store.append_turn("s2", "user", "记住我喜欢26度", user_id=UID, turn_id="u1")
        await store.consolidate("s2", UID, "primary", "", complete_fn=llm)      # another occasion
        return first, same_session, dict(await _row(store, "climate.temperature"))

    first, same_session, later = asyncio.run(go())
    assert (first["evidence_count"], first["weight"]) == (1, 0.6)
    assert same_session["evidence_count"] == 1
    assert later["evidence_count"] == 2 and later["weight"] > first["weight"]


async def _family(store, *edges):
    vs = await store._vec()
    await vs.add_relations(UID, [{"subject": s, "rel": "family", "object": o, "confidence": 0.9} for s, o in edges],
                           occupant_id="primary")


def test_one_named_daughter_and_the_kinship_word_are_one_subject():
    store = _store()

    async def go():
        await _family(store, ("小雨", "女儿"))
        vs = await store._vec()
        aliases = await vs.subject_aliases(UID, "primary")
        await store.remember([_item("女儿不吃辣", "taste.spicy", subject="女儿")])
        await store.remember([_item("小雨现在能吃一点辣", "taste.spicy", subject="小雨")])
        current = [r["text"] for r in await vs.current_in_dimension(UID, "primary", ["taste.spicy"], ["女儿", "小雨"])]
        by_kin = [d["text"] for d, _ in await store.recall(UID, occupant_id="primary", subject="女儿")]
        by_name = [d["text"] for d, _ in await store.recall(UID, occupant_id="primary", subject="小雨")]
        return aliases, current, by_kin, by_name

    aliases, current, by_kin, by_name = asyncio.run(go())
    assert aliases == {"小雨": "女儿"}
    assert current == ["小雨现在能吃一点辣"]
    assert by_kin == by_name == ["小雨现在能吃一点辣"]


def test_two_named_daughters_stay_two_people():
    store = _store()

    async def go():
        await _family(store, ("小雨", "女儿"), ("小雪", "女儿"))
        vs = await store._vec()
        await store.remember([_item("小雨不吃辣", "taste.spicy", subject="小雨")])
        await store.remember([_item("小雪爱吃辣", "taste.spicy", subject="小雪")])
        return await vs.subject_aliases(UID, "primary"), [d["text"] for d, _ in await store.recall(UID)]

    aliases, texts = asyncio.run(go())
    assert aliases == {}
    assert sorted(texts) == ["小雨不吃辣", "小雪爱吃辣"]


def test_reads_fold_one_person_across_both_subjects():
    store = _store()

    async def go():
        await _family(store, ("小雨", "女儿"))
        vs = await store._vec()
        await vs.remember([_item("女儿不吃辣", "taste.spicy", subject="女儿", valid_from=1),
                           _item("小雨不吃辣", "taste.spicy", subject="小雨", valid_from=2)])
        return [d["text"] for d, _ in await store.recall(UID)]

    assert asyncio.run(go()) == ["小雨不吃辣"]


def test_every_spelling_of_one_kinship_word_is_one_subject():
    store = _store()

    async def go():
        vs = await store._vec()
        await vs.remember([_item("妻子喜欢24度", "climate.temperature", subject="妻子", valid_from=1),
                           _item("老婆现在喜欢25度", "climate.temperature", subject="老婆", valid_from=2),
                           _item("我喜欢22度", "climate.temperature", subject="我", valid_from=3),
                           _item("用户喜欢23度", "climate.temperature", valid_from=4)])
        by_spelling = [d["text"] for d, _ in await store.recall(UID, occupant_id="primary", subject="太太")]
        return by_spelling, sorted(d["text"] for d, _ in await store.recall(UID))

    by_spelling, everything = asyncio.run(go())
    assert by_spelling == ["老婆现在喜欢25度"]
    assert everything == ["用户喜欢23度", "老婆现在喜欢25度"]


def test_agent_writes_store_the_normalized_subject():
    store = _store()

    async def go():
        await store.remember([_item("女儿喜欢甜的", "taste.sweet", subject="我女儿")])
        await store.remember([_item("用户喜欢甜的", "taste.sweet", subject="本人")])
        vs = await store._vec()
        return sorted(str(v.get("subject") or "") for v in vs._mem.values())

    assert asyncio.run(go()) == ["", "女儿"]


def test_only_unambiguous_names_are_aliased():
    store = _store()

    async def go():
        # a kinship word on the name side is not a name; a name with two kinship words is ambiguous
        await _family(store, ("妈妈", "老婆"), ("阿明", "儿子"), ("阿明", "老公"), ("小雨", "女儿"))
        vs = await store._vec()
        return await vs.subject_aliases(UID, "primary")

    assert asyncio.run(go()) == {"小雨": "女儿"}


def test_reinforcing_an_old_inference_stores_the_undecayed_weight():
    """Recall decays the stored weight by age; storing an already decayed weight decays it twice."""
    import time
    store = _store()

    async def go():
        vs = await store._vec()
        [item_id] = await vs.remember([_item("用户可能喜欢爵士乐", "media.genre", provenance="agent_inferred",
                                             review_status="auto_extracted", confidence=0.4, source_session="a")])
        vs._mem[item_id]["valid_from"] = int(time.time()) - 90 * 86400
        await store.remember([_item("用户可能喜欢爵士乐", "media.genre", provenance="agent_inferred",
                                    review_status="auto_extracted", confidence=0.4, source_session="b")])
        return dict(vs._mem[item_id])

    row = asyncio.run(go())
    assert row["evidence_count"] == 2 and row["half_life_days"] == weighting.HALF_LIFE_INFERRED
    assert row["weight"] == weighting.compute_weight(provenance="agent_inferred", evidence_count=2,
                                                     half_life_days=row["half_life_days"])


def test_a_query_finds_a_spelling_that_is_neither_the_query_nor_the_canonical_word():
    store = _store()

    async def go():
        vs = await store._vec()
        await vs.remember([_item("妻子喜欢24度", "climate.temperature", subject="妻子")])
        return [d["text"] for d, _ in await store.recall(UID, occupant_id="primary", subject="太太")]

    assert asyncio.run(go()) == ["妻子喜欢24度"]


def test_a_new_statement_about_oneself_replaces_one_stored_under_a_self_word():
    store = _store()

    async def go():
        vs = await store._vec()
        [legacy] = await vs.remember([_item("我喜欢22度", "climate.temperature", subject="我")])
        await store.remember([_item("用户喜欢26度", "climate.temperature")])
        return vs._mem[legacy]["superseded_by"], [d["text"] for d, _ in await store.recall(UID)]

    superseded_by, texts = asyncio.run(go())
    assert superseded_by and texts == ["用户喜欢26度"]
