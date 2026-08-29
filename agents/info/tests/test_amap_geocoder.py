import asyncio

from agents.info.src.providers.amap_geocoder import AmapGeocoder


def test_reverse_geocoder_uses_amap_coordinates_and_returns_formatted_address():
    geocoder = AmapGeocoder("test-key")

    async def fake_get_json(url, params=None, **kwargs):
        assert url.endswith("/v3/geocode/regeo")
        assert params["location"] == "116.41,39.92"
        assert params["key"] == "test-key"
        return {"status": "1", "regeocode": {"formatted_address": "北京市朝阳区望京街道"}}

    geocoder._http.get_json = fake_get_json

    assert asyncio.run(geocoder.reverse(116.41, 39.92)) == "北京市朝阳区望京街道"


def test_reverse_falls_back_to_last_known_good_when_the_lookup_fails():
    """QA 长会话 `538335f` `information` T4（专项 `--repeat 3` = 1/3 复现）：
    一次瞬时反查失败，把系统几十秒前刚说对过的地名整个丢掉。

    真栈原话「**当前位置**当前有1条天气预警：深圳市气象台发布暴雨黄色预警（黄级）。」
    ——同一条会话的前三轮（实况/预报/空气质量）都正确说了「深圳」。
    机制从代码可证：GPS meta 在场且无显式 `city` 槽时，`_spoken_place` 落到
    「当前位置」**当且仅当** `_display_city` 拿到空串，而那一支的唯一来源
    就是这里抛 `ProviderError`。

    判据：**降级要降到「上一个正确答案」，不是降到「什么都不知道」。**
    """
    import pytest
    from agents._sdk.http import ProviderError

    geocoder = AmapGeocoder("test-key")
    calls = {"n": 0}

    async def flaky(url, params=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "1",
                    "regeocode": {"formatted_address": "广东省深圳市南山区"}}
        return {"status": "0", "info": "SERVICE_UNAVAILABLE"}

    geocoder._http.get_json = flaky

    assert asyncio.run(geocoder.reverse(113.9412, 22.5410)) == "广东省深圳市南山区"
    # 第二次真查失败 —— 但坐标没变、时间没走远 ⇒ 那个名字仍然是对的
    assert asyncio.run(geocoder.reverse(113.9412, 22.5410)) == "广东省深圳市南山区"
    assert calls["n"] == 2, "成功路径不许走兜底表——每次都要真查"

    # 反向对照①：换一个坐标（超出 110 米粒度）⇒ 没有可退回的答案，老老实实抛错
    with pytest.raises(ProviderError):
        asyncio.run(geocoder.reverse(116.41, 39.92))

    # 反向对照②：过了 TTL 也不许再退回——旧答案要有上界
    import agents.info.src.providers.amap_geocoder as mod
    geocoder._last_good[geocoder._lkg_key(113.9412, 22.5410)] = (
        0.0, "广东省深圳市南山区")
    with pytest.raises(ProviderError):
        asyncio.run(geocoder.reverse(113.9412, 22.5410))
    assert mod._LKG_TTL_S == 600, "TTL 改了就要改这条断言，别让它悄悄跟着实现走"
