"""真机 bug 修复的纯函数单测：股票市场分类（#5 腾讯港股误标）+ 赛事追问/队名/开球时间（#4）
+ 旅程红灯 Q3 的日期解析（A5-2/A3-1：「下周三」「昨晚」原静默回落今天答非所问）。"""
from datetime import datetime, timedelta, timezone

from agents.info.src.providers.base import market_label
from agents.info.src.handlers.sports import (_find_team, _fmt_kickoff, _sports_date,
                                             SportsMixin)


# ── #5 market_label：腾讯 00700 是港股，不是 A 股深证 ──

def test_market_label_hk_bare_5digit():
    assert market_label("00700") == "港股"        # 腾讯（裸 5 位）
    assert market_label("hk00700") == "港股"       # 新浪前缀
    assert market_label("09988") == "港股"         # 阿里港股


def test_market_label_a_shares():
    assert market_label("600519.SH") == "上证·A股"  # 茅台（tushare 后缀）
    assert market_label("sh600519") == "上证·A股"   # 新浪前缀
    assert market_label("000001.SZ") == "深证·A股"  # 平安（tushare）
    assert market_label("sz000001") == "深证·A股"
    assert market_label("300750.SZ") == "深证·A股"  # 宁德时代创业板
    assert market_label("430047.BJ") == "北证·A股"


def test_market_label_us_and_unknown():
    assert market_label("AAPL") == "美股"          # 非数字 → 美股
    assert market_label("gb_aapl") == "美股"
    assert market_label("") == ""                  # 空 → 无标签（HMI 回退）


# ── #4 赛事追问 helper ──

def test_find_team_national_names():
    assert _find_team("世界杯下一场阿根廷的比赛在什么时候") == "阿根廷"
    assert _find_team("葡萄牙下一场比赛") == "葡萄牙"
    assert _find_team("今天有什么好玩的") == ""      # 无队名


def test_is_next_match():
    assert SportsMixin._is_next_match("下一场阿根廷的比赛") is True
    assert SportsMixin._is_next_match("阿根廷什么时候踢") is True
    assert SportsMixin._is_next_match("今天世界杯赛程") is False  # 今日列表非「下一场」


def test_fmt_kickoff():
    assert _fmt_kickoff("2026-07-07T03:00:00+08:00") == "07-07 03:00"
    assert _fmt_kickoff("") == ""


# ── Q3 _sports_date：相对日期/周几（旅程 A5-2「下周三」→曾答「今天没有查询到」）──

_TZ = timezone(timedelta(hours=8))
_TUE = datetime(2026, 7, 14, 12, 0, tzinfo=_TZ)     # 2026-07-14 周二


def test_sports_date_relative_days():
    assert _sports_date("昨晚欧冠决赛的比分", _TUE) == "2026-07-13"
    assert _sports_date("昨天的赛果", _TUE) == "2026-07-13"
    assert _sports_date("明天世界杯", _TUE) == "2026-07-15"
    assert _sports_date("后天有什么比赛", _TUE) == "2026-07-16"
    assert _sports_date("大后天赛程", _TUE) == "2026-07-17"
    assert _sports_date("前天的赛果", _TUE) == "2026-07-12"
    assert _sports_date("今天世界杯赛程", _TUE) == "2026-07-14"


def test_sports_date_weekday():
    # 周二问：裸「周三」=明天；「下周三」=下个自然周的周三
    assert _sports_date("周三世界杯有什么比赛", _TUE) == "2026-07-15"
    assert _sports_date("下周三世界杯有什么比赛", _TUE) == "2026-07-22"
    # 周四问「下周三」是 6 天后（下个自然周），不是 13 天；裸「周三」已过 → 下周
    thu = datetime(2026, 7, 16, 12, 0, tzinfo=_TZ)
    assert _sports_date("下周三的比赛", thu) == "2026-07-22"
    assert _sports_date("周三的比赛", thu) == "2026-07-22"


# ── R9：sports provider 故障 → 回落通用搜索（skip_sports 防二次吃超时），不再裸 FAILED ──

def test_sports_provider_down_falls_back_to_search():
    import asyncio

    class _Intent:
        raw_text = "昨晚欧冠决赛的比分是多少"
        slots = {"query": "昨晚欧冠决赛比分"}

    class _Stub(SportsMixin):
        def __init__(self):
            self.search_called_with = None

        async def _do_sports(self, *a, **kw):
            return None                       # provider 故障路径

        async def _search(self, intent, ctx, meta, skip_sports=False):
            self.search_called_with = (intent.slots.get("query"), skip_sports)
            from agents._sdk import AgentResult
            return AgentResult(speech="接地搜索结果")

        async def _save_remindable(self, *a, **kw):  # pragma: no cover
            raise AssertionError("故障路径不该写 REMINDABLE")

    stub = _Stub()
    res = asyncio.run(stub._sports(_Intent(), ctx=None, meta={}))
    assert res.speech == "接地搜索结果"
    assert stub.search_called_with == ("昨晚欧冠决赛的比分是多少", True)  # 原话整句 + 跳结构化源
