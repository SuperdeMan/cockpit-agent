"""治理 P1（nearby 试点族）：place 卡 `_prov` 标记 + 运行期真实源失败诚实降级（不再回退 mock）。"""
import asyncio

from agents._sdk.http import ProviderError
from agents._sdk.testing import run_handle
from agents.nearby.src.agent import NearbyAgent


def test_place_list_card_carries_prov_mock():
    res = asyncio.run(run_handle(
        NearbyAgent(), "nearby.search",
        slots={"cuisine": "川菜"}, raw_text="附近的川菜馆"))
    prov = (res.ui_card or {}).get("_prov")
    assert prov and prov["mode"] == "mock" and prov["vendor"] == "mock"


def test_search_runtime_failure_degrades_honestly_no_mock_items():
    """真实源运行期失败 → FAILED + 诚实话术；绝不端上 mock 假 POI（可能被导航过去）。"""
    agent = NearbyAgent()

    async def boom(keyword, **kwargs):
        raise ProviderError("amap 5xx")

    agent.place.search = boom
    res = asyncio.run(run_handle(agent, "nearby.search",
                                 slots={"cuisine": "川菜"}, raw_text="附近的川菜馆"))
    # M0a 对齐 R9 契约：诚实降级话术用 OK 返回——单步 FAILED 的 speech 会被聚合器
    # 吞成裸「抱歉，处理失败」（executor 不映射 error，aggregator 只读 r.error）。
    assert res.status == "ok"
    assert res.ui_card is None                      # 没有假列表
    assert "暂时不可用" in res.speech


def test_detail_runtime_failure_degrades_honestly():
    agent = NearbyAgent()

    async def boom(place_id, **kwargs):
        raise ProviderError("amap timeout")

    agent.place.detail = boom
    res = asyncio.run(run_handle(agent, "nearby.detail",
                                 slots={"name": "老王川菜"}, raw_text="看看老王川菜的详情"))
    assert res.status == "ok"          # 同上：R9 契约，OK 话术防聚合器吞
    assert res.ui_card is None
    assert "老王川菜" in res.speech
