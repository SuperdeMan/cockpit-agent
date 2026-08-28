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


class _AlertingWeather:
    """只为 alerts 那张卡造一条预警——mock provider 无预警时**不出卡**（正确行为），
    而「不出卡」证明不了「出卡时盖没盖章」。"""
    provenance_vendor = "qweather"
    provenance_mode = "real"

    async def alerts(self, city, meta=None):
        from agents.info.src.providers.base import WeatherAlert
        return [WeatherAlert(title="深圳市气象台发布台风蓝色预警", level="蓝",
                             type_name="台风", text="注意防范", pub_time="2026-08-26T11:00Z")]


def test_the_three_weather_family_cards_carry_prov_too():
    """C9-C（真栈 info T3/T4/T5 三张卡全缺章）：同一个 `weather.py` 里 5 个 handler，
    2 个盖章 3 个漏——漏的恰好是 QA 抓到的这三张。契约 §9.3 的必带清单
    2026-08-27 已补登 `air_quality / weather_alerts / life_indices`，
    这一条是**实现侧的兑现**（契约与实现一起漏过一次，就不能只补一边）。
    """
    for intent in ("info.air_quality", "info.indices"):
        res = asyncio.run(run_handle(
            InfoAgent(), intent, slots={"city": "北京"}, raw_text="北京"))
        prov = (res.ui_card or {}).get("_prov")
        assert prov, f"{intent} 的卡必须带 _prov"
        assert prov["mode"] == "mock" and prov["vendor"] == "mock"
        assert prov["fetched_at"]

    agent = InfoAgent()
    agent.weather = _AlertingWeather()
    res = asyncio.run(run_handle(
        agent, "info.alerts", slots={"city": "深圳"}, raw_text="深圳有预警吗"))
    prov = (res.ui_card or {}).get("_prov")
    assert prov and prov["vendor"] == "qweather"
    # C9-D：前缀不进 join 列表——真栈原话是「…天气预警：；台风蓝色预警」。
    assert "：；" not in res.speech and "：，" not in res.speech


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
