"""天气意图先答 + speech 可读性单测（badcase f555cde3：「未来几天会下雨吗」只回
模板罗列、完整逆地理地址整段进语音、「预报：；」双标点）。纯函数离线可跑。

导入走全包路径（agents.info.src.…，同 test_agent.py）：裸 sys.path 插 src 会让
`providers` 这类通用包名劫持 sys.modules，污染 llm-gateway 等同名模块的测试收集。"""
from datetime import datetime

from agents.info.src.handlers import weather as W
from agents.info.src.providers.base import ForecastDay


def _freeze_today(monkeypatch, iso: str):
    monkeypatch.setattr(W, "_shanghai_now",
                        lambda: datetime.strptime(iso, "%Y-%m-%d"))


def _mk(date, day, night, hi="30", lo="25", wind=""):
    return ForecastDay(date=date, text_day=day, text_night=night,
                       temp_high=hi, temp_low=lo, wind_scale=wind)


_RAINY3 = [_mk("2026-07-13", "小雨", "中雨", "33", "26"),
           _mk("2026-07-14", "大雨", "中雨", "29", "25"),
           _mk("2026-07-15", "中雨", "中雨", "30", "26")]

_MIXED3 = [_mk("2026-07-13", "多云", "晴", "33", "26"),
           _mk("2026-07-14", "小雨", "多云", "29", "25"),
           _mk("2026-07-15", "晴", "晴", "30", "26")]

_DRY3 = [_mk("2026-07-13", "晴", "晴"), _mk("2026-07-14", "多云", "晴"),
         _mk("2026-07-15", "晴", "多云")]


def test_rain_question_all_days(monkeypatch):
    _freeze_today(monkeypatch, "2026-07-13")
    out = W._forecast_answer("未来几天会下雨吗", _RAINY3)
    assert out.startswith("会下雨")
    assert "每天都有雨" in out and "带伞" in out


def test_rain_question_some_days_labels(monkeypatch):
    _freeze_today(monkeypatch, "2026-07-13")
    out = W._forecast_answer("会下雨吗", _MIXED3)
    assert "明天有雨" in out            # 只有 07-14 有雨 → 人话日期


def test_rain_question_no_rain(monkeypatch):
    _freeze_today(monkeypatch, "2026-07-13")
    assert W._forecast_answer("要带伞吗", _DRY3) == "未来3天都不会下雨。"


def test_temp_question(monkeypatch):
    _freeze_today(monkeypatch, "2026-07-13")
    out = W._forecast_answer("这几天热不热", _RAINY3)
    assert "25℃" in out and "33℃" in out


def test_listing_query_no_lead():
    assert W._forecast_answer("未来三天天气预报", _RAINY3) == ""
    assert W._forecast_answer("", _RAINY3) == ""


def test_wind_question():
    fc = [_mk("2026-07-13", "晴", "晴", wind="6-7"), _mk("2026-07-14", "晴", "晴", wind="3")]
    out = W._forecast_answer("风大吗", fc)
    assert "风比较大" in out and "7级" in out


def test_speech_place_shrinks_full_address():
    full = "广东省深圳市南山区粤海街道科技南一路深投控创智天地大厦"
    assert W._speech_place(full) == "深圳市南山区"
    assert W._speech_place("北京") == "北京"
    assert W._speech_place("当前位置") == "当前位置"


def test_day_label(monkeypatch):
    _freeze_today(monkeypatch, "2026-07-13")
    assert W._day_label("2026-07-13") == "今天"
    assert W._day_label("2026-07-14") == "明天"
    assert W._day_label("2026-07-15") == "后天"
    assert W._day_label("2026-07-17") == "17号"
    assert W._day_label("bad") == "bad"


# ── 实时天气意图先答（badcase 11db5215：「今天天气怎么样，适合出行吗」只机械播报）──
from types import SimpleNamespace as _NS


def _now(text="阴", temp="33", feels="35"):
    return _NS(text=text, temp=temp, feels_like=feels)


def test_go_out_question_hot_day():
    out = W._weather_answer("今天天气怎么样，适合出行码", _now(), _mk("2026-07-13", "阴", "多云"), [])
    assert out.startswith("适合出行") and "防晒" in out


def test_go_out_question_rainy_day():
    out = W._weather_answer("适合出门吗", _now("小雨"), _mk("2026-07-13", "小雨", "中雨"), [])
    assert "带伞" in out


def test_go_out_question_with_alert():
    alert = _NS(type_name="暴雨", title="暴雨橙色预警")
    out = W._weather_answer("适合出行吗", _now(), None, [alert])
    assert "暴雨" in out and "预警" in out


def test_go_out_mild_day():
    out = W._weather_answer("适合出去玩吗", _now("晴", "24", "25"), _mk("2026-07-13", "晴", "晴"), [])
    assert out.startswith("适合出行")


def test_rain_question_current():
    assert "带伞" in W._weather_answer("外面下雨吗", _now("小雨"), None, [])
    assert W._weather_answer("下雨了吗", _now("晴"), _mk("2026-07-13", "晴", "晴"), []) == "今天没有降雨。"


def test_generic_weather_ask_no_lead():
    assert W._weather_answer("今天天气怎么样", _now(), None, []) == ""
    assert W._weather_answer("", _now(), None, []) == ""


def test_forecast_go_out_rule(monkeypatch):
    _freeze_today(monkeypatch, "2026-07-13")
    out = W._forecast_answer("未来几天适合出行吗", _MIXED3)
    assert "明天" in out and "带伞" in out
    assert W._forecast_answer("这几天适合出门吗", _DRY3) == "未来3天没有雨雪，适合出行。"


# ── 日期感知（badcase demo-i9c92i：「明天还会下雨吗」三连被答成今天实况）──


def test_requested_day_offset(monkeypatch):
    _freeze_today(monkeypatch, "2026-07-13")     # 2026-07-13 是周一
    assert W._requested_day_offset("明天", "") == 1                 # planner 槽位优先
    assert W._requested_day_offset("", "明天还会下雨吗？") == 1      # 原话兜底
    assert W._requested_day_offset("", "后天呢") == 2
    assert W._requested_day_offset("大后天", "") == 3
    assert W._requested_day_offset("2026-07-15", "") == 2           # ISO 槽位
    assert W._requested_day_offset("", "今天天气怎么样") == 0
    assert W._requested_day_offset("", "天气怎么样") == 0
    assert W._requested_day_offset("", "周三的天气") == 2            # 本周三
    assert W._requested_day_offset("", "周末适合出去玩吗") == 5      # 最近的周六
    assert W._requested_day_offset("下周一", "") == 7


def test_day_answer_rain_and_dry():
    rainy = _mk("2026-07-14", "小雨", "多云")
    dry = _mk("2026-07-14", "晴", "晴")
    assert W._day_answer("明天还会下雨吗", rainy, "明天") == "明天有雨，出门记得带伞。"
    assert W._day_answer("明天会下雨吗", dry, "明天") == "明天不会下雨。"
    assert "带伞" in W._day_answer("明天适合出行吗", rainy, "明天")
    assert W._day_answer("明天天气怎么样", rainy, "明天") == ""      # 罗列型问法不加前导
    assert W._day_answer("", rainy, "明天") == ""
