"""api-football 赛事 Provider —— 实时比分/赛程的结构化真相源（不经 LLM，杜绝编造）。

凭证经 env(API_FOOTBALL_KEY) 注入，绝不进代码/日志。失败抛 ProviderError，
Agent 据此回落通用搜索/诚实弃权，不击穿主链。

API: GET https://v3.football.api-sports.io/fixtures
Auth: header ``x-apisports-key``
Params: date=YYYY-MM-DD | league=<id>&season=<year> | live=all ；均可加 timezone
Resp: {"response":[{"fixture":{"date","status":{"short","long","elapsed"}},
        "league":{"name","round"},"teams":{"home","away"},"goals":{"home","away"}}], "errors":[]}
docs: https://www.api-football.com/documentation-v3
"""
from __future__ import annotations
import logging

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import SportsProvider, SportsFixture, GoalEvent, TopScorer

logger = logging.getLogger("agent.info.sports_apifootball")

_BASE = "https://v3.football.api-sports.io"

# 进球事件 detail → 中文。只收录"真实进球"，故意不含 "Missed Penalty"（罚丢点球，
# api-football 仍标 type=Goal，但不计进球）——据此过滤，避免谎报射手/比分。
_GOAL_DETAIL = {"Normal Goal": "进球", "Penalty": "点球", "Own Goal": "乌龙球"}

# api-football status.short → (归一化组, 中文)
_FINISHED = {"FT": "已结束", "AET": "加时赛结束", "PEN": "点球结束"}
_LIVE = {"1H": "上半场", "HT": "中场", "2H": "下半场", "ET": "加时赛",
         "BT": "加时中场", "P": "点球大战", "LIVE": "进行中"}
_SCHEDULED = {"NS": "未开赛", "TBD": "时间待定"}
_OTHER = {"PST": "推迟", "CANC": "取消", "ABD": "中断", "SUSP": "暂停",
          "INT": "中断", "AWD": "判定胜负", "WO": "弃权"}


# 国家队英文名（api-football）→ 中文。静态映射，准确无幻觉；未命中回退英文。
_ZH_TEAMS = {
    "Spain": "西班牙", "Saudi Arabia": "沙特阿拉伯", "Belgium": "比利时", "Iran": "伊朗",
    "Uruguay": "乌拉圭", "Cape Verde Islands": "佛得角", "Cape Verde": "佛得角",
    "New Zealand": "新西兰", "Egypt": "埃及", "Brazil": "巴西", "Argentina": "阿根廷",
    "France": "法国", "Germany": "德国", "England": "英格兰", "Portugal": "葡萄牙",
    "Netherlands": "荷兰", "Croatia": "克罗地亚", "Morocco": "摩洛哥", "Japan": "日本",
    "South Korea": "韩国", "Korea Republic": "韩国", "USA": "美国",
    "United States": "美国", "Mexico": "墨西哥", "Canada": "加拿大", "Italy": "意大利",
    "Switzerland": "瑞士", "Denmark": "丹麦", "Poland": "波兰", "Senegal": "塞内加尔",
    "Ghana": "加纳", "Cameroon": "喀麦隆", "Nigeria": "尼日利亚", "Australia": "澳大利亚",
    "Qatar": "卡塔尔", "Ecuador": "厄瓜多尔", "Serbia": "塞尔维亚", "Wales": "威尔士",
    "Costa Rica": "哥斯达黎加", "Tunisia": "突尼斯", "Colombia": "哥伦比亚", "Chile": "智利",
    "Peru": "秘鲁", "Paraguay": "巴拉圭", "Algeria": "阿尔及利亚", "Ivory Coast": "科特迪瓦",
    "Côte d'Ivoire": "科特迪瓦", "Norway": "挪威", "Sweden": "瑞典", "Austria": "奥地利",
    "Turkey": "土耳其", "Türkiye": "土耳其", "Ukraine": "乌克兰", "Czech Republic": "捷克",
    "Czechia": "捷克", "Greece": "希腊", "Scotland": "苏格兰", "Republic of Ireland": "爱尔兰",
    "Romania": "罗马尼亚", "Hungary": "匈牙利", "Slovakia": "斯洛伐克", "Slovenia": "斯洛文尼亚",
    "Jordan": "约旦", "Iraq": "伊拉克", "United Arab Emirates": "阿联酋", "Uzbekistan": "乌兹别克斯坦",
    "Oman": "阿曼", "Bahrain": "巴林", "China": "中国", "China PR": "中国", "India": "印度",
    "Indonesia": "印度尼西亚", "Thailand": "泰国", "Vietnam": "越南", "Panama": "巴拿马",
    "Honduras": "洪都拉斯", "Jamaica": "牙买加", "Venezuela": "委内瑞拉", "Bolivia": "玻利维亚",
    "South Africa": "南非", "Mali": "马里", "Burkina Faso": "布基纳法索", "Angola": "安哥拉",
    # 世界杯 2026 新晋/其它参赛队
    "Curacao": "库拉索", "Curaçao": "库拉索", "Haiti": "海地", "Panama": "巴拿马",
    "Jordan": "约旦", "New Caledonia": "新喀里多尼亚", "Suriname": "苏里南",
}

# 中文队名 → 国旗 emoji（静态映射，准确无幻觉；覆盖世界杯 2026 全部球队 + 主要国家队）。
# 说明：真实国旗 emoji 在真机 webview（Chromium/Android）正常显示；Windows 版 Chromium 缺国旗字形，
# 会退化成 ISO 双字母（如「ES」），仍能表意——不影响真车目标平台。英格兰/苏格兰/威尔士用官方地区旗序列。
_FLAGS = {
    "西班牙": "🇪🇸", "沙特阿拉伯": "🇸🇦", "比利时": "🇧🇪", "伊朗": "🇮🇷", "乌拉圭": "🇺🇾",
    "佛得角": "🇨🇻", "新西兰": "🇳🇿", "埃及": "🇪🇬", "巴西": "🇧🇷", "阿根廷": "🇦🇷",
    "法国": "🇫🇷", "德国": "🇩🇪", "英格兰": "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    "葡萄牙": "🇵🇹", "荷兰": "🇳🇱", "克罗地亚": "🇭🇷", "摩洛哥": "🇲🇦", "日本": "🇯🇵",
    "韩国": "🇰🇷", "美国": "🇺🇸", "墨西哥": "🇲🇽", "加拿大": "🇨🇦", "意大利": "🇮🇹",
    "瑞士": "🇨🇭", "丹麦": "🇩🇰", "波兰": "🇵🇱", "塞内加尔": "🇸🇳", "加纳": "🇬🇭",
    "喀麦隆": "🇨🇲", "尼日利亚": "🇳🇬", "澳大利亚": "🇦🇺", "卡塔尔": "🇶🇦", "厄瓜多尔": "🇪🇨",
    "塞尔维亚": "🇷🇸", "威尔士": "🏴\U000e0067\U000e0062\U000e0077\U000e006c\U000e0073\U000e007f",
    "哥斯达黎加": "🇨🇷", "突尼斯": "🇹🇳", "哥伦比亚": "🇨🇴", "智利": "🇨🇱", "秘鲁": "🇵🇪",
    "巴拉圭": "🇵🇾", "阿尔及利亚": "🇩🇿", "科特迪瓦": "🇨🇮", "挪威": "🇳🇴", "瑞典": "🇸🇪",
    "奥地利": "🇦🇹", "土耳其": "🇹🇷", "乌克兰": "🇺🇦", "捷克": "🇨🇿", "希腊": "🇬🇷",
    "苏格兰": "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f", "爱尔兰": "🇮🇪",
    "罗马尼亚": "🇷🇴", "匈牙利": "🇭🇺", "斯洛伐克": "🇸🇰", "斯洛文尼亚": "🇸🇮", "约旦": "🇯🇴",
    "伊拉克": "🇮🇶", "阿联酋": "🇦🇪", "乌兹别克斯坦": "🇺🇿", "阿曼": "🇴🇲", "巴林": "🇧🇭",
    "中国": "🇨🇳", "印度": "🇮🇳", "印度尼西亚": "🇮🇩", "泰国": "🇹🇭", "越南": "🇻🇳",
    "巴拿马": "🇵🇦", "洪都拉斯": "🇭🇳", "牙买加": "🇯🇲", "委内瑞拉": "🇻🇪", "玻利维亚": "🇧🇴",
    "南非": "🇿🇦", "马里": "🇲🇱", "布基纳法索": "🇧🇫", "安哥拉": "🇦🇴", "库拉索": "🇨🇼",
    "海地": "🇭🇹", "新喀里多尼亚": "🇳🇨", "苏里南": "🇸🇷",
}


def _zh(name: str) -> str:
    return _ZH_TEAMS.get(name, name)


def flag_for(name: str) -> str:
    """中文队名 → 国旗 emoji（未命中返回空串）。国家队用；俱乐部无旗（走 logo）。"""
    return _FLAGS.get(name or "", "")


def _fixture_from_item(item: dict) -> "SportsFixture":
    """api-football fixtures 响应单项 → SportsFixture（fixtures / next 复用）。"""
    fx = item.get("fixture") or {}
    lg = item.get("league") or {}
    teams = item.get("teams") or {}
    goals = item.get("goals") or {}
    home, away = teams.get("home") or {}, teams.get("away") or {}
    st = fx.get("status") or {}
    group, text = _status(_g(st.get("short")), _g(st.get("long")))
    return SportsFixture(
        league=_g(lg.get("name")), league_id=_int(lg.get("id")),
        round=_g(lg.get("round")),
        home=_zh(_g(home.get("name"))), away=_zh(_g(away.get("name"))),
        home_logo=_g(home.get("logo")), away_logo=_g(away.get("logo")),
        home_goals=_g(goals.get("home")), away_goals=_g(goals.get("away")),
        status=group, status_text=text,
        elapsed=_g(st.get("elapsed")), kickoff=_g(fx.get("date")),
        fixture_id=_int(fx.get("id")),
        home_id=_int(home.get("id")), away_id=_int(away.get("id")),
    )


def _status(short: str, long: str) -> tuple[str, str]:
    if short in _FINISHED:
        return "finished", _FINISHED[short]
    if short in _LIVE:
        return "live", _LIVE[short]
    if short in _SCHEDULED:
        return "scheduled", _SCHEDULED[short]
    return "other", _OTHER.get(short, long or short)


def _g(v) -> str:
    return "" if v is None else str(v)


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


class ApiFootballProvider(SportsProvider):
    def __init__(self, key: str, host: str = ""):
        if not key:
            raise ValueError("API_FOOTBALL_KEY required for ApiFootballProvider")
        self._key = key
        base = (host or "").strip()
        if base and not base.startswith("http"):
            base = "https://" + base
        self._base = (base or _BASE).rstrip("/")
        self._http = AsyncHttpClient(vendor="apifootball", service="info",
                                     timeout_s=8.0, max_retries=1)

    async def fixtures(self, date: str = "", league: int = 0, season: int = 0,
                       live: bool = False, timezone: str = "Asia/Shanghai",
                       meta: dict | None = None) -> list[SportsFixture]:
        params = {"timezone": timezone}
        if live:
            params["live"] = "all"
        else:
            if date:
                params["date"] = date
            if league:
                params["league"] = str(league)
            if season:
                params["season"] = str(season)
        data = await self._http.get_json(
            f"{self._base}/fixtures", params=params,
            headers={"x-apisports-key": self._key}, op="fixtures", meta=meta)
        errors = data.get("errors")
        if errors:  # dict 或非空 list 均代表上游报错（如 key/配额/参数）
            raise ProviderError(f"api-football error: {errors}")

        return [_fixture_from_item(item) for item in (data.get("response") or [])]

    async def events(self, fixture_id: int,
                     meta: dict | None = None) -> list[GoalEvent]:
        """拉某场进球事件。只取真实进球（Normal Goal/Penalty/Own Goal），剔除罚丢点球等。"""
        if not fixture_id:
            return []
        data = await self._http.get_json(
            f"{self._base}/fixtures/events", params={"fixture": str(fixture_id)},
            headers={"x-apisports-key": self._key}, op="fixture_events", meta=meta)
        if data.get("errors"):
            raise ProviderError(f"api-football events error: {data.get('errors')}")

        out: list[GoalEvent] = []
        for e in (data.get("response") or []):
            if _g(e.get("type")) != "Goal":
                continue
            detail = _g(e.get("detail"))
            zh = _GOAL_DETAIL.get(detail)
            if not zh:           # Missed Penalty 等非进球事件 → 跳过
                continue
            t = e.get("time") or {}
            elapsed, extra = _g(t.get("elapsed")), _g(t.get("extra"))
            minute = f"{elapsed}+{extra}" if extra else elapsed
            team = e.get("team") or {}
            player = e.get("player") or {}
            out.append(GoalEvent(
                minute=minute, team_id=_int(team.get("id")),
                player=_g(player.get("name")), detail=zh))
        return out

    async def top_scorers(self, league: int, season: int,
                          meta: dict | None = None) -> list[TopScorer]:
        """联赛射手榜。/players/topscorers?league&season（免费档仅 2022-2024 赛季放行）。"""
        data = await self._http.get_json(
            f"{self._base}/players/topscorers",
            params={"league": str(league), "season": str(season)},
            headers={"x-apisports-key": self._key}, op="topscorers", meta=meta)
        if data.get("errors"):
            raise ProviderError(f"api-football topscorers error: {data.get('errors')}")

        out: list[TopScorer] = []
        for i, item in enumerate(data.get("response") or [], 1):
            player = item.get("player") or {}
            stats = (item.get("statistics") or [{}])[0] or {}
            goals = ((stats.get("goals") or {}).get("total"))
            team = (stats.get("team") or {}).get("name")
            out.append(TopScorer(
                rank=i, player=_g(player.get("name")),
                team=_zh(_g(team)), goals=_int(goals)))
        return out
