"""MemoryStore 单测：画像写入/读取（含常用地点 places）。

store.py 为纯 Python（无 proto 依赖），直接驱动。默认无 REDIS_URL 走内存兜底。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from store import MemoryStore  # noqa: E402


def _store() -> MemoryStore:
    s = MemoryStore()
    s.url = ""  # 强制内存兜底，测试不依赖 Redis
    return s


def test_upsert_profile_places_roundtrip():
    store = _store()

    async def go():
        await store.upsert_profile("u1", "places", {
            "home": {"name": "阳光小区", "address": "上海长宁", "lat": 31.21, "lng": 121.40}})
        return await store.get_context("s", "u1", "v", ["profile.places"])

    vals = asyncio.run(go())
    assert "profile.places" in vals
    places = json.loads(vals["profile.places"])
    assert places["home"]["name"] == "阳光小区"
    assert places["home"]["lat"] == 31.21


def test_upsert_profile_merges_additional_place():
    store = _store()

    async def go():
        await store.upsert_profile("u1", "places", {"home": {"lat": 1.0, "lng": 2.0}})
        # 再写一次 places 全量（navigation 侧合并后整存）
        await store.upsert_profile("u1", "places", {
            "home": {"lat": 1.0, "lng": 2.0},
            "company": {"lat": 3.0, "lng": 4.0}})
        return await store.get_context("s", "u1", "v", ["profile.places"])

    places = json.loads(asyncio.run(go())["profile.places"])
    assert set(places) == {"home", "company"}
    assert places["company"]["lat"] == 3.0


def test_get_context_no_profile_falls_back_clean():
    """未设置画像时 profile.* 不报错、不返回脏数据。"""
    store = _store()
    vals = asyncio.run(store.get_context("s", "u-none", "v", ["profile.places"]))
    assert "profile.places" not in vals


def test_delete_profile_removes_places():
    store = _store()

    async def go():
        await store.upsert_profile("u1", "places", {"home": {"lat": 1.0, "lng": 2.0}})
        existed = await store.delete_profile("u1")
        after = await store.get_context("s", "u1", "v", ["profile.places"])
        return existed, after

    existed, after = asyncio.run(go())
    assert existed is True
    assert "profile.places" not in after


def test_places_mirrored_highly_sensitive_and_not_generalized():
    """P1 收敛：places 镜像为 highly_sensitive memory_item；get_context 直读取回，
    但泛化召回不带出（隐私）。"""
    store = _store()

    async def go():
        await store.upsert_profile("u1", "places", {
            "home": {"name": "阳光小区", "address": "上海长宁", "lat": 31.2, "lng": 121.4}})
        vals = await store.get_context("s", "u1", "v", ["profile.places"])
        general = await store.recall(user_id="u1", query="阳光小区")  # 泛化召回
        exported = await store.export_user("u1")
        return vals, general, exported

    vals, general, exported = asyncio.run(go())
    assert json.loads(vals["profile.places"])["home"]["name"] == "阳光小区"  # 直读拿到
    assert general == []                                                    # 高敏不泛化
    places_mem = [m for m in exported["memories"] if m["predicate"].startswith("place.")]
    assert places_mem and places_mem[0]["privacy_level"] == "highly_sensitive"


def test_migrate_places_from_legacy_kv():
    """P1.5：既有 KV places 一次性迁入 memory_item，get_context 收敛到新表。"""
    store = _store()

    async def go():
        store._profiles["u2"] = {"places": {"company": {"name": "华润大厦", "lat": 1, "lng": 2}}}
        n = await store.migrate_places("u2")
        got = await store.get_context("s", "u2", "v", ["profile.places"])
        return n, got

    n, got = asyncio.run(go())
    assert n == 1
    assert json.loads(got["profile.places"])["company"]["name"] == "华润大厦"


def test_forget_user_purges_session_transcripts():
    """GDPR 全量删必须连会话原文一起清（验收抓到：长期记忆删了、对话原文永久留存
    且无 TTL——那不是删除是搬家）。内存兜底路径验 user→sessions 索引 + 级联删。"""
    store = _store()

    async def go():
        await store.append_turn("sess-a", "user", "我家在阳光小区", user_id="u9")
        await store.append_turn("sess-b", "user", "我爱吃辣", user_id="u9")
        await store.append_turn("sess-c", "user", "别人的会话", user_id="u8")
        await store.forget_user("u9")
        return (await store.get_session("sess-a", 10),
                await store.get_session("sess-b", 10),
                await store.get_session("sess-c", 10))

    a, b, c = asyncio.run(go())
    assert a == [] and b == [], "u9 的会话原文必须随 ForgetUser 清除"
    assert len(c) == 1, "别人的会话不受影响（无爆炸半径）"


def test_forget_user_scoped_delete_keeps_sessions():
    """scope 定向删（如清一条偏好）不该把会话原文整个端掉。"""
    store = _store()

    async def go():
        await store.append_turn("sess-d", "user", "你好", user_id="u9")
        await store.forget_user("u9", scopes=["profile.taste"])
        return await store.get_session("sess-d", 10)

    assert len(asyncio.run(go())) == 1


# ── places 的 owner 维度（M-B）────────────────────────────
def test_two_occupants_each_have_their_own_home():
    """乘员 B 设「家」不得覆盖主驾的家。

    此前 places 读写两侧都是 user 级：`get_places` 恒取 primary、写侧是共享 KV，
    于是 RFC 宣传的「常去地点各自独立」在代码里根本不成立。
    """
    store = _store()

    async def go():
        await store.upsert_profile("u1", "places", {"home": {"name": "阳光小区", "lat": 1, "lng": 2}})
        await store.upsert_profile("u1", "places", {"home": {"name": "翠竹苑", "lat": 3, "lng": 4}},
                                   occupant_id="occ-2")
        a = await store.get_context("s", "u1", "v", ["profile.places"])
        b = await store.get_context("s", "u1", "v", ["profile.places"], occupant_id="occ-2")
        return a, b

    a, b = asyncio.run(go())
    assert json.loads(a["profile.places"])["home"]["name"] == "阳光小区"
    assert json.loads(b["profile.places"])["home"]["name"] == "翠竹苑"


def test_non_primary_never_reads_legacy_kv_places():
    """非 primary 永不读 legacy KV——那是主驾的地址，泄漏比查不到更糟。"""
    store = _store()
    store._profiles["u1"] = {"places": {"home": {"name": "主驾的家", "lat": 1, "lng": 2}}}

    async def go():
        return (
            await store.get_context("s", "u1", "v", ["profile.places"]),
            await store.get_context("s", "u1", "v", ["profile.places"], occupant_id="occ-2"),
        )

    a, b = asyncio.run(go())
    assert json.loads(a["profile.places"])["home"]["name"] == "主驾的家"  # primary dual-read
    assert "profile.places" not in b


def test_primary_dual_read_only_fills_missing_keys():
    """dual-read 只补新表**缺失**的 key，不用整块 KV 覆盖新表。"""
    store = _store()
    store._profiles["u1"] = {"places": {"home": {"name": "旧的家"}, "company": {"name": "旧的公司"}}}

    async def go():
        await store.upsert_profile("u1", "places", {"home": {"name": "新的家", "lat": 1, "lng": 2}})
        return await store.get_context("s", "u1", "v", ["profile.places"])

    places = json.loads(asyncio.run(go())["profile.places"])
    assert places["home"]["name"] == "新的家"       # memory_item 胜出
    assert places["company"]["name"] == "旧的公司"  # KV 只补缺失的


def test_places_upsert_is_a_patch_not_a_replace():
    store = _store()

    async def go():
        await store.upsert_profile("u1", "places", {"home": {"name": "家", "lat": 1, "lng": 2}})
        await store.upsert_profile("u1", "places", {"company": {"name": "公司", "lat": 3, "lng": 4}})
        return await store.get_context("s", "u1", "v", ["profile.places"])

    places = json.loads(asyncio.run(go())["profile.places"])
    assert set(places) == {"home", "company"}
