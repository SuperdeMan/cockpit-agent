"""ApiFootballProvider 单测：mock 掉底层 HTTP，喂黄金响应。不发真实网络。"""
import asyncio
import pytest

from agents._sdk.http import ProviderError
from agents.info.src.providers.sports_apifootball import ApiFootballProvider, _status


def _provider(responses: dict):
    p = ApiFootballProvider(key="test-key")

    async def fake_get_json(url, params=None, op="get", headers=None, meta=None):
        fake_get_json.last_params = params
        fake_get_json.last_headers = headers
        for key, val in responses.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"no scripted response for {url}")

    p._http.get_json = fake_get_json
    return p


_FIXTURES_OK = {
    "errors": [],
    "response": [
        {"fixture": {"id": 1, "date": "2026-06-22T20:00:00+08:00",
                     "status": {"short": "FT", "long": "Match Finished", "elapsed": 90}},
         "league": {"id": 1, "name": "World Cup", "round": "Group Stage - 2"},
         "teams": {"home": {"name": "巴西", "logo": "h.png"},
                   "away": {"name": "海地", "logo": "a.png"}},
         "goals": {"home": 3, "away": 0}},
        {"fixture": {"id": 2, "date": "2026-06-22T23:00:00+08:00",
                     "status": {"short": "NS", "long": "Not Started", "elapsed": None}},
         "league": {"id": 1, "name": "World Cup", "round": "Group Stage - 2"},
         "teams": {"home": {"name": "美国"}, "away": {"name": "澳大利亚"}},
         "goals": {"home": None, "away": None}},
    ],
}


def test_fixtures_parses_real_scores_and_status():
    p = _provider({"/fixtures": _FIXTURES_OK})
    res = asyncio.run(p.fixtures(date="2026-06-22", league=1, season=2026))
    assert len(res) == 2
    assert res[0].home == "巴西" and res[0].away == "海地"
    assert res[0].league_id == 1   # 供客户端按联赛过滤
    assert res[0].home_goals == "3" and res[0].away_goals == "0"
    assert res[0].status == "finished" and res[0].status_text == "已结束"
    assert res[1].status == "scheduled" and res[1].status_text == "未开赛"
    assert res[1].home_goals == ""   # 未开赛无比分


def test_fixtures_sends_key_and_params():
    p = _provider({"/fixtures": _FIXTURES_OK})
    asyncio.run(p.fixtures(date="2026-06-22", league=1, season=2026))
    assert p._http.get_json.last_headers["x-apisports-key"] == "test-key"
    assert p._http.get_json.last_params["date"] == "2026-06-22"
    assert p._http.get_json.last_params["league"] == "1"
    assert p._http.get_json.last_params["timezone"] == "Asia/Shanghai"


def test_live_param():
    p = _provider({"/fixtures": _FIXTURES_OK})
    asyncio.run(p.fixtures(live=True))
    assert p._http.get_json.last_params["live"] == "all"


def test_api_errors_raise():
    p = _provider({"/fixtures": {"errors": {"token": "invalid"}, "response": []}})
    with pytest.raises(ProviderError, match="api-football"):
        asyncio.run(p.fixtures(league=1, season=2026))


def test_missing_key_raises():
    with pytest.raises(ValueError):
        ApiFootballProvider(key="")


def test_status_mapping():
    assert _status("FT", "")[0] == "finished"
    assert _status("1H", "")[0] == "live"
    assert _status("NS", "")[0] == "scheduled"
    assert _status("PST", "Postponed")[0] == "other"
