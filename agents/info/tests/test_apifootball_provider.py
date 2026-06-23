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
         "teams": {"home": {"id": 6, "name": "巴西", "logo": "h.png"},
                   "away": {"id": 7, "name": "海地", "logo": "a.png"}},
         "goals": {"home": 3, "away": 0}},
        {"fixture": {"id": 2, "date": "2026-06-22T23:00:00+08:00",
                     "status": {"short": "NS", "long": "Not Started", "elapsed": None}},
         "league": {"id": 1, "name": "World Cup", "round": "Group Stage - 2"},
         "teams": {"home": {"id": 8, "name": "美国"}, "away": {"id": 9, "name": "澳大利亚"}},
         "goals": {"home": None, "away": None}},
    ],
}

# 进球事件黄金响应：含罚丢点球（type=Goal 但 detail=Missed Penalty，须剔除）、
# 普通进球、点球、乌龙球，以及黄牌/换人（type≠Goal，须剔除）。
_EVENTS_OK = {
    "errors": [],
    "response": [
        {"time": {"elapsed": 9, "extra": None}, "type": "Goal", "detail": "Missed Penalty",
         "team": {"id": 6, "name": "Brazil"}, "player": {"name": "L. Messi"}},
        {"time": {"elapsed": 38, "extra": None}, "type": "Goal", "detail": "Normal Goal",
         "team": {"id": 6, "name": "Brazil"}, "player": {"name": "L. Messi"}},
        {"time": {"elapsed": 40, "extra": None}, "type": "Card", "detail": "Yellow Card",
         "team": {"id": 7, "name": "Haiti"}, "player": {"name": "S. Posch"}},
        {"time": {"elapsed": 70, "extra": 2}, "type": "Goal", "detail": "Penalty",
         "team": {"id": 7, "name": "Haiti"}, "player": {"name": "K. Mbappe"}},
        {"time": {"elapsed": 80, "extra": None}, "type": "Goal", "detail": "Own Goal",
         "team": {"id": 6, "name": "Brazil"}, "player": {"name": "J. Doe"}},
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
    assert res[0].fixture_id == 1 and res[0].home_id == 6 and res[0].away_id == 7
    assert res[1].status == "scheduled" and res[1].status_text == "未开赛"
    assert res[1].home_goals == ""   # 未开赛无比分


def test_events_filters_to_real_goals():
    p = _provider({"/fixtures/events": _EVENTS_OK})
    res = asyncio.run(p.events(1))
    # 罚丢点球(Missed Penalty)、黄牌(Card) 被剔除；仅留 3 粒真实进球
    assert [g.minute for g in res] == ["38", "70+2", "80"]
    assert [g.detail for g in res] == ["进球", "点球", "乌龙球"]
    assert res[0].team_id == 6 and res[0].player == "L. Messi"
    assert res[1].minute == "70+2" and res[1].detail == "点球"


def test_events_sends_fixture_and_key():
    p = _provider({"/fixtures/events": _EVENTS_OK})
    asyncio.run(p.events(1489399))
    assert p._http.get_json.last_params["fixture"] == "1489399"
    assert p._http.get_json.last_headers["x-apisports-key"] == "test-key"


def test_events_empty_fixture_id_no_call():
    p = _provider({"/fixtures/events": _EVENTS_OK})
    assert asyncio.run(p.events(0)) == []


def test_events_api_error_raises():
    p = _provider({"/fixtures/events": {"errors": {"requests": "limit"}, "response": []}})
    with pytest.raises(ProviderError, match="api-football"):
        asyncio.run(p.events(1))


_TOPSCORERS_OK = {
    "errors": [],
    "response": [
        {"player": {"name": "Kylian Mbappé"},
         "statistics": [{"goals": {"total": 8}, "team": {"name": "France"}}]},
        {"player": {"name": "L. Messi"},
         "statistics": [{"goals": {"total": 7}, "team": {"name": "Argentina"}}]},
    ],
}


def test_top_scorers_parses_rank_goals_and_zh_team():
    p = _provider({"/players/topscorers": _TOPSCORERS_OK})
    res = asyncio.run(p.top_scorers(league=1, season=2022))
    assert [(s.rank, s.player, s.team, s.goals) for s in res] == [
        (1, "Kylian Mbappé", "法国", 8),   # 球队名英→中映射
        (2, "L. Messi", "阿根廷", 7),
    ]
    assert p._http.get_json.last_params["league"] == "1"
    assert p._http.get_json.last_params["season"] == "2022"


def test_top_scorers_season_block_raises():
    p = _provider({"/players/topscorers": {
        "errors": {"plan": "Free plans do not have access to this season"}, "response": []}})
    with pytest.raises(ProviderError, match="api-football"):
        asyncio.run(p.top_scorers(league=1, season=2026))


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
