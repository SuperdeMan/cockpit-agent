"""L1 精确删除（M-B）。

HMI 记忆面板的「删除这一行」此前走 `ForgetUser(scopes=[scope])`——那是**按 scope 删**，
会连带清掉该 scope 下所有乘员的条目。表现最直白的一例：删自己的名字会把全车每个人的
`identity.name` 一起删光。精确删除必须有自己的入口，不能复用 scope 删。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from store import MemoryStore  # noqa: E402


def _store() -> MemoryStore:
    s = MemoryStore()
    s.url = ""
    s._vstore._dsn = ""
    return s


def _sem(user_id, occupant_id, text, predicate, scope="profile.taste"):
    return {"user_id": user_id, "occupant_id": occupant_id, "kind": "semantic",
            "predicate": predicate, "text": text, "scope": scope,
            "provenance": "user_stated", "confidence": 0.9}


def test_deletes_exactly_one_item_and_leaves_the_same_scope_alone():
    store = _store()

    async def go():
        ids = await store.remember([
            _sem("u1", "primary", "用户不吃辣", "taste.spicy"),
            _sem("u1", "primary", "用户喜欢清淡", "taste.light"),
        ])
        res = await store.delete_memory_item("u1", "primary", ids[0])
        return res, await store.recall(user_id="u1", query="")

    res, left = asyncio.run(go())
    assert res["ok"] is True and res["deleted"] == 1
    assert [m[0]["predicate"] for m in left] == ["taste.light"]


def test_other_occupant_item_is_not_found_not_denied():
    """跨 owner 一律 not_found——「不是你的」这个回答本身会泄露它属于谁。"""
    store = _store()

    async def go():
        ids = await store.remember([_sem("u1", "occ-2", "乘客喜欢冰美式", "drink.temp")])
        res = await store.delete_memory_item("u1", "primary", ids[0])
        return res, await store.recall(user_id="u1", occupant_id="occ-2", query="")

    res, left = asyncio.run(go())
    assert res["ok"] is False and res["error"] == "not_found"
    assert len(left) == 1                       # 对方的条目原封不动


def test_missing_occupant_is_rejected_and_never_widened():
    """owner 级动作缺 occupant 一律拒绝——绝不推断 primary，更不扩大成 user-all。"""
    store = _store()

    async def go():
        ids = await store.remember([_sem("u1", "primary", "用户不吃辣", "taste.spicy")])
        res = await store.delete_memory_item("u1", "", ids[0])
        return res, await store.recall(user_id="u1", query="")

    res, left = asyncio.run(go())
    assert res["ok"] is False and res["error"] == "missing_owner"
    assert len(left) == 1


def test_identity_name_is_managed_and_cannot_be_deleted_here():
    """`identity.name` 是声纹的受管投影：从通用记忆面板删掉它会留下
    「模板还在、名字没了」的半截状态，助手继续认得出人却叫不出名字。"""
    store = _store()

    async def go():
        ids = await store.remember([
            _sem("u1", "primary", "这位乘员的名字是泓舟", "identity.name",
                 scope="profile.identity")])
        res = await store.delete_memory_item("u1", "primary", ids[0])
        return res, await store.recall(user_id="u1", query="",
                                       scopes=["profile.identity"])

    res, left = asyncio.run(go())
    assert res["ok"] is False and res["error"] == "managed_memory"
    assert len(left) == 1


def test_deleting_an_item_clears_relations_pointing_at_it():
    """悬空边：删了地点条目却留着指向它的关系边，人称解析会解到一条已删数据。"""
    store = _store()

    async def go():
        ids = await store.remember([
            _sem("u1", "primary", "家：阳光小区", "place.home", scope="profile.places")])
        vs = await store._vec()
        await vs.add_relations("u1", [
            {"subject": "小雨", "rel": "lives_at", "object": "阳光小区",
             "object_ref": ids[0], "provenance": "user_stated", "confidence": 0.9}],
            occupant_id="primary")
        before = await store.relations("u1")
        res = await store.delete_memory_item("u1", "primary", ids[0])
        return before, res, await store.relations("u1")

    before, res, after = asyncio.run(go())
    assert len(before) == 1
    assert res["deleted_relations"] == 1
    assert after == []


def test_deleting_one_owner_name_keeps_the_other_owner_name():
    """本条 RPC 存在的原因：scope 删除会把全车每个人的名字一起删光。"""
    store = _store()

    async def go():
        ids = await store.remember([
            _sem("u1", "primary", "这位乘员的名字是泓舟", "identity.name",
                 scope="profile.identity"),
            _sem("u1", "occ-2", "这位乘员的名字是阿灵", "identity.name",
                 scope="profile.identity"),
        ])
        # 对照：旧口径（scope 定向删）会一次清掉两条
        await store.forget_user("u1", scopes=["profile.identity"])
        return ids, await store.recall(user_id="u1", query="",
                                       scopes=["profile.identity"]), \
            await store.recall(user_id="u1", occupant_id="occ-2", query="",
                               scopes=["profile.identity"])

    _ids, a, b = asyncio.run(go())
    assert a == [] and b == [], "scope 定向删的爆炸半径是全部乘员——这正是 L1 要取代它的原因"
