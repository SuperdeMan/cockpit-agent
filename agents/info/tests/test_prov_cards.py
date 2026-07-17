"""治理 P1：试点卡 `_prov` 真实性标记 + Struct 往返不丢键（契约 conventions §9.3）。"""
import asyncio

from google.protobuf.json_format import MessageToDict

from agents._sdk.provenance import attach
from agents._sdk.server import _to_struct
from agents._sdk.testing import run_handle
from agents.info.src.agent import InfoAgent


def test_weather_card_carries_prov_mock():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.weather", slots={"city": "北京"}, raw_text="北京天气"))
    prov = (res.ui_card or {}).get("_prov")
    assert prov, "试点族天气卡必须带 _prov"
    assert prov["mode"] == "mock" and prov["vendor"] == "mock"   # 无凭证栈 = 诚实标 mock
    assert prov["fetched_at"]


def test_search_card_carries_prov():
    res = asyncio.run(run_handle(
        InfoAgent(), "info.search", slots={"query": "人工智能"}, raw_text="搜一下人工智能"))
    prov = (res.ui_card or {}).get("_prov")
    assert prov and prov["mode"] == "mock" and prov["vendor"] == "mock"


def test_prov_survives_struct_roundtrip():
    """已知坑：ui_card 经 Struct↔dict 多跳（agent→engine→聚合→网关）——钉死 _prov 不丢。"""
    card = attach({"type": "weather", "city": "北京"}, "qweather")
    back = MessageToDict(_to_struct(card))
    assert back["_prov"]["mode"] == "real" and back["_prov"]["vendor"] == "qweather"
    assert back["_prov"]["fetched_at"]


def test_attach_card_group_stamps_members():
    group = {"type": "card_group", "items": [{"type": "a"}, {"type": "b"}]}
    attach(group, "amap")
    assert all(i["_prov"]["vendor"] == "amap" for i in group["items"])
    assert "_prov" not in group        # 章打在成员卡上，组壳不重复


def test_attach_explicit_degraded_mode_and_note():
    card = attach({"type": "sports_scores"}, "api-football",
                  mode="degraded", note="赛季回退 2024/25")
    assert card["_prov"]["mode"] == "degraded"
    assert card["_prov"]["note"] == "赛季回退 2024/25"
