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
