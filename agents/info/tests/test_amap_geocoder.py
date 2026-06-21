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
