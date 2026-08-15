"""安全信号判据的唯一实现（卡 Q9）——**误杀面**是这组测试的重点。

`alert_level` 的消费方里有 chitchat，而 chitchat 兜底会看到**全部**流量。
判据宽一格的代价不是「多答一句」，是对着一盏正常的灯劝用户靠边停车。
所以这里正例只占一半，另一半全是**不该命中**的句子。
"""
from agents._sdk.safety_signal import (alert_level, alert_signal,
                                       driver_state)


# ── 该命中的 ─────────────────────────────────────────────────────────────

def test_critical_lights():
    assert alert_level("红色机油灯亮了怎么办") == "critical"
    assert alert_level("水温灯亮了") == "critical"
    assert alert_level("刹车突然失灵了") == "critical"
    assert alert_level("车里有股糊味还冒烟") == "critical"


def test_amber_lights_and_phenomena():
    assert alert_level("胎压黄灯亮了，还能开吗") == "amber"
    assert alert_level("仪表盘有个故障灯") == "amber"
    assert alert_level("轮胎好像漏气了") == "amber"
    assert alert_level("最近底盘有异响") == "amber"


# ── **不该命中的**（本组是这份判据的主要价值）──────────────────────────

def test_normal_lights_are_not_alerts():
    """车上大多数灯是正常功能灯。用「灯亮」这类通配会把它们全部误杀。"""
    for text in ("大灯亮了", "把大灯打开", "氛围灯亮着挺好看",
                 "阅读灯亮了帮我关掉", "转向灯亮着呢", "日行灯一直亮"):
        assert alert_level(text) == "", f"不该判成告警：{text}"


def test_ordinary_talk_is_not_an_alert():
    for text in ("胎压多少正常", "怎么连CarPlay", "讲个笑话吧",
                 "帮我导航去公司", "慢一点开可以吗", "今天天气怎么样"):
        assert alert_level(text) == "", f"不该判成告警：{text}"


def test_signal_is_a_name_not_a_sentence():
    sig = alert_signal("胎压黄灯亮了，还能继续开吗？应该补到多少？")
    assert sig and len(sig) <= 12, f"signal 不该是整句：{sig!r}"
    assert alert_signal("讲个笑话吧") == ""


# ── 驾驶员状态：认不出必须返回空 ─────────────────────────────────────────

def test_driver_state_positives():
    assert driver_state("困到睁不开眼了") == "fatigue"
    assert driver_state("刚喝了两杯酒") == "alcohol"
    assert driver_state("有点头晕") == "unwell"


def test_driver_state_returns_empty_when_unrecognised():
    """**纪律 ②**：认不出就返回空，调用方不许 `or "fatigue"` 兜底。

    阶段 1 首版就是栽在这里——「慢一点开可以吗」被答成
    「您现在的状态不适合继续开，困倦时…」，用户根本没说自己困。
    """
    for text in ("慢一点开可以吗", "现在在高速还能继续开吗",
                 "红色机油灯亮了怎么办", "帮我放首歌"):
        assert driver_state(text) == "", f"不该判成驾驶员状态：{text}"
