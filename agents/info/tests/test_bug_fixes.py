"""真机 bug 修复的纯函数单测：股票市场分类（#5 腾讯港股误标）+ 赛事追问/队名/开球时间（#4）。"""
from agents.info.src.providers.base import market_label
from agents.info.src.handlers.sports import _find_team, _fmt_kickoff, SportsMixin


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
