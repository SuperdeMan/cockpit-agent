"""安全信号判据的唯一实现（卡 Q9）——**误杀面**是这组测试的重点。

`alert_level` 的消费方里有 chitchat，而 chitchat 兜底会看到**全部**流量。
判据宽一格的代价不是「多答一句」，是对着一盏正常的灯劝用户靠边停车。
所以这里正例只占一半，另一半全是**不该命中**的句子。
"""
from runtime.safety_signal import (alert_level, alert_signal,
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


def test_named_light_is_not_prefixed_with_its_own_system():
    """具名灯自己就带系统名，再前缀一次就成了「机油机油灯」（2026-08-26 QA 实录）。

    ⚠ 上面那条断言只查 `len(sig) <= 12`，而「机油机油灯」正好 5 个字——
    **长度对、内容错**，四轮真栈实录（vehicle T35-36 / family T62-63）里它一路
    进了焦点、卡片与播报话术，没有任何一条断言拦得住。
    所以这条钉的是**具体返回值**：形状类断言抓不到内容错。
    """
    assert alert_signal("红色机油灯亮了怎么办") == "机油灯"
    assert alert_signal("水温灯亮了") == "水温灯"
    assert alert_signal("电池灯一直亮着") == "电池灯"
    # 反方向：系统名与现象词是两个词时，拼接仍然是对的（别把这条一起修掉）
    assert alert_signal("刹车有异响") == "刹车异响"
    assert alert_signal("制动失灵") == "制动失灵"


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


# ── 解除陈述（QA T47 裁决 A，2026-09-19）────────────────────────────────────
# 这是放宽一道安全约束的作用域，所以**反例是这组的主要价值**：问句、指令、否定、
# 意图陈述、无对象的「好了」、正常功能灯，一个都不许清掉一条 critical。

from runtime.safety_signal import alert_resolved  # noqa: E402


def test_resolution_statements_clear_the_alert():
    for text in ("机油灯灭了", "检查过了，机油灯已经灭了", "水温报警解决了", "故障灯不亮了",
                 "机油灯没亮", "机油灯是误报", "刹车异响修好了", "胎压报警处理好了",
                 "机油灯已排除", "警告灯熄灭了", "故障已解决", "机油灯没有报警"):
        assert alert_resolved(text), f"应判为解除：{text}"
        assert alert_level(text) == "", f"解除陈述不是一条新告警：{text}"


def test_questions_directives_negations_and_intentions_do_not_clear():
    for text in (
        "机油灯灭了吗", "机油灯是不是灭了", "如果机油灯灭了还能开吗",     # 问句 / 假设
        "帮我把故障灯灭了",                                            # 指令
        "机油灯还没灭", "机油灯没修好", "机油灯还没有排除了",          # 否定
        "我会靠边停车检查", "马上去修", "先开到服务区再看",              # 意图不是解除
        "刚才那个提醒处理好了", "没事了", "处理好了", "问题解决了",      # 没点名告警对象
        "大灯灭了", "雾灯不亮了",                                      # 正常功能灯不是告警对象
        "机油灯亮了", "红色机油灯亮了还能继续开吗",                    # 本来就是告警
    ):
        assert not alert_resolved(text), f"不该判为解除：{text}"


def test_resolution_clause_does_not_hide_a_new_alert_in_the_same_utterance():
    """「机油灯灭了但是水温灯亮了」：前半解除、后半是新告警，两件事都要成立。"""
    text = "机油灯灭了但是水温灯亮了"
    assert alert_resolved(text)
    assert alert_level(text) == "critical"
    assert alert_signal(text) == "水温灯"
    mixed = "水温报警解决了，胎压灯还亮着"
    assert alert_resolved(mixed)
    assert alert_level(mixed) == "amber"
    assert alert_signal(mixed) == "胎压灯"


def test_resolution_plus_continuation_question_is_not_an_alert():
    """「机油灯灭了，现在还能继续开吗」：解除 + 续驾问句，不许按告警答「靠边停车」。"""
    text = "机油灯灭了，现在还能继续开吗"
    assert alert_resolved(text)
    assert alert_level(text) == "" and alert_signal(text) == ""


def test_alert_recognition_is_byte_for_byte_unchanged_without_a_resolution():
    """没有解除陈述时 `alert_level` / `alert_signal` 的行为一个字不变——既有全部正反例照旧。"""
    for text, level, sig in (("红色机油灯亮了怎么办", "critical", "机油灯"),
                             ("胎压黄灯亮了，还能开吗", "amber", "黄灯"),
                             ("刹车有异响", "critical", "刹车异响"),
                             ("大灯亮了", "", ""), ("今天天气怎么样", "", "")):
        assert alert_level(text) == level, text
        assert alert_signal(text) == sig, text
