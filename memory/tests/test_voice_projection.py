"""CA2-15 S2: an unrecognized voice reads only ordinary preferences, and its words are not extracted.

Directed reads (GetContext scopes, Recall predicate prefixes) and undirected recall use the same
criterion (runtime/memory_projection); person-place resolution and relations return nothing.
"""
import asyncio
import importlib.util
import json
import os
import sys

_MEM_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _MEM_DIR)
from cockpit.memory.v1 import memory_pb2  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "memory_server_projection_under_test", os.path.join(_MEM_DIR, "server.py"))
_mem_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mem_server)

UID = "u-proj"
HIDDEN = "normal_only"


def _servicer():
    svc = _mem_server.MemoryServicer()
    svc.store.url = ""
    svc.store._vstore._dsn = ""
    return svc


def _item(**kw):
    base = {"user_id": UID, "occupant_id": "primary", "kind": "semantic", "privacy_level": "normal",
            "confidence": 1.0, "provenance": "user_stated", "review_status": "user_confirmed"}
    base.update(kw)
    return base


SEED = [
    _item(predicate="climate.temperature", scope="profile.comfort", text="喜欢空调 26 度"),
    _item(predicate="taste.spicy", scope="profile.taste", text="不吃辣"),
    _item(predicate="place.home", scope="profile.places", privacy_level="highly_sensitive",
          text="家：云岚小区", value_json=json.dumps({"name": "云岚小区"})),
    _item(predicate="person.child", scope="profile.person", privacy_level="sensitive", text="女儿叫小雨"),
    _item(predicate="identity.name", scope="profile.identity", text="我叫泓舟"),
    _item(kind="episodic", predicate="", scope="episodic.general", text="周六女儿钢琴比赛"),
    _item(predicate="taste.dislike_cold", scope="profile.comfort", text="爸爸不喜欢空调太冷", subject="爸爸"),
]


def _recall(svc, projection, **kw):
    async def go():
        resp = await svc.Recall(memory_pb2.RecallRequest(user_id=UID, projection=projection, top_k=20, **kw), None)
        return sorted(i.text for i in resp.items)
    return asyncio.run(go())


def _seeded():
    svc = _servicer()
    asyncio.run(svc.store.remember([dict(i) for i in SEED]))
    return svc


def test_an_unrecognized_voice_recalls_only_ordinary_preferences():
    svc = _seeded()
    assert _recall(svc, HIDDEN) == ["不吃辣", "喜欢空调 26 度"]
    # Undirected recall already leaves out highly_sensitive places; everything else is there
    # without projection, so the projection is what removes people, names, events and others.
    assert _recall(svc, "") == ["不吃辣", "周六女儿钢琴比赛", "喜欢空调 26 度", "女儿叫小雨", "我叫泓舟",
                                "爸爸不喜欢空调太冷"]


def test_directed_reads_follow_the_same_rule():
    svc = _seeded()
    assert _recall(svc, HIDDEN, predicate_prefix="place.") == []
    assert _recall(svc, HIDDEN, predicate_prefix="taste.") == ["不吃辣"]

    async def context(projection):
        resp = await svc.GetContext(memory_pb2.GetContextRequest(
            user_id=UID, occupant_id="primary", scopes=["profile.places", "profile.person"],
            projection=projection), None)
        return dict(resp.values)

    assert asyncio.run(context(HIDDEN)) == {}
    assert "profile.places" in asyncio.run(context(""))


def test_people_and_relations_resolve_to_nothing_under_projection():
    svc = _servicer()
    asked = []

    async def resolve(user_id, person_word, *, occupant_id="primary"):
        asked.append(person_word)
        return {"person": "小雨", "place": "XX小学", "object_ref": ""}

    async def relations(user_id, **kw):
        asked.append("relations")
        return [{"subject": "小雨", "rel": "family", "object": "女儿"}]

    svc.store.resolve_person_place = resolve
    svc.store.relations = relations

    async def go(projection):
        place = await svc.ResolvePersonPlace(memory_pb2.ResolvePersonPlaceRequest(
            user_id=UID, person_word="孩子", projection=projection), None)
        edges = await svc.QueryRelations(memory_pb2.QueryRelationsRequest(
            user_id=UID, projection=projection), None)
        return place.found, len(edges.edges)

    assert asyncio.run(go(HIDDEN)) == (False, 0) and asked == []
    assert asyncio.run(go("")) == (True, 1)


def test_unknown_projection_values_are_treated_as_the_strictest():
    svc = _seeded()
    assert _recall(svc, "anything") == ["不吃辣", "喜欢空调 26 度"]


def test_an_unrecognized_speakers_words_stay_in_history_but_are_never_extracted():
    svc = _servicer()
    triggered = []

    async def consolidate(*a, **k):
        triggered.append(a)
        return []

    svc.store.consolidate = consolidate

    async def go():
        await svc.AppendTurn(memory_pb2.AppendTurnRequest(
            session_id="s1", role="user", text="记住我喜欢18度", user_id=UID, occupant_id="primary",
            speaker_unverified=True), None)
        await asyncio.gather(*list(svc._bg))
        return await svc.store.get_session("s1", 10, user_id=UID, occupant_id="primary")

    turns = asyncio.run(go())
    assert triggered == []                                           # not even an immediate「记住」
    assert [(t["text"], t["speaker_unverified"]) for t in turns] == [("记住我喜欢18度", True)]


def test_extraction_windows_skip_unverified_turns():
    svc = _servicer()
    prompts = []

    async def llm(messages):
        prompts.append(messages[-1]["content"])
        return "[]"

    async def go():
        epoch = await svc.fence.current(UID)
        for text, unverified in (("我喜欢18度", True), ("导航去公司", False)):
            await svc.store.append_turn("s1", "user", text, user_id=UID, occupant_id="primary",
                                        memory_epoch=epoch, speaker_unverified=unverified)
        await svc.store.consolidate("s1", UID, "primary", "", complete_fn=llm, memory_epoch=epoch)

    asyncio.run(go())
    assert "导航去公司" in prompts[0] and "我喜欢18度" not in prompts[0]
