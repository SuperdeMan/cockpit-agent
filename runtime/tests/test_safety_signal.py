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


# ── 追加批 K（K-2，2026-09-24；设计 §14）：此刻自述 vs 提到这类风险 ───────────────────────────
# 修前纯词表包含：「我没喝酒」= 饮酒（会话里登记一条 critical、用户听到「喝过酒…请不要驾驶」）、
# 「疲劳驾驶有什么危害」= 犯困自述、「如果喝了酒还能开车吗」= 此刻饮酒、「我好困」认不出。

import pytest  # noqa: E402

from runtime.safety_signal import driver_state_mentioned  # noqa: E402


@pytest.mark.parametrize("text", [
    "我没喝酒", "没有喝酒", "我没熬夜", "别熬夜", "我不头晕", "我不困了", "今天没喝酒，放心",
])
def test_negated_states_are_not_states(text):
    assert driver_state(text) == "", text
    assert driver_state_mentioned(text) == "", text


def test_drinking_a_little_is_still_drinking():
    """「没怎么喝酒」是喝了一点——否定只看紧前那一个字，不往前扫。"""
    assert driver_state("我没怎么喝酒") == "alcohol"


@pytest.mark.parametrize("text, state", [
    ("我好困", "fatigue"), ("有点困了", "fatigue"), ("困了怎么办", "fatigue"),
    ("困得不行了", "fatigue"), ("我喝酒了，还能开吗", "alcohol"),
    ("我疲劳驾驶了怎么办", "fatigue"), ("喝了酒还能开吗", "alcohol"),
])
def test_self_reports_are_states(text, state):
    assert driver_state(text) == state, text


@pytest.mark.parametrize("text, state", [
    ("疲劳驾驶有什么危害", "fatigue"),       # 话题名词 + 问句 + 无第一人称
    ("醉驾怎么处罚", "alcohol"),
    ("酒后驾驶有什么后果", "alcohol"),
    ("喝酒后多久能开车", "alcohol"),
    ("熬夜后开车要注意什么", "fatigue"),
    ("如果喝了酒还能开车吗", "alcohol"),      # 假设框架
    ("万一开车时犯困怎么办", "fatigue"),
    ("要是困了怎么办", "fatigue"),
])
def test_topics_and_hypotheticals_are_mentioned_not_reported(text, state):
    assert driver_state(text) == "", text
    assert driver_state_mentioned(text) == state, text


def test_every_state_has_a_topic_speech_that_does_not_claim_the_drivers_state():
    from runtime.safety_signal import DRIVER_STATE_ADVICE
    for state, spec in DRIVER_STATE_ADVICE.items():
        assert spec.get("topic_speech"), state
        assert "您现在" not in spec["topic_speech"], state


# ── 追加批 K（K-1）：拒绝安全建议 ──────────────────────────────────────────────────────────

from runtime.safety_signal import refuses_safety_advice  # noqa: E402


@pytest.mark.parametrize("text", [
    "别提醒我，继续开就行",      # SF4 原句
    "我没事，继续开", "不用管我", "不用停，我撑得住", "别啰嗦了", "我不休息",
    "我撑得住", "我真没事",      # 只有自我担保
    "好了好了，别提醒了", "行，我不休息",     # 夹应答词仍只是拒绝
])
def test_refusals_of_safety_advice(text):
    assert refuses_safety_advice(text) is True, text


@pytest.mark.parametrize("text", [
    "帮我找个地方", "最近的服务区在哪", "好的，我去休息", "那我停一下", "导航去公司", "",
    "别提醒我，帮我找个地方", "别提醒我了，导航去公司",    # 夹着别的请求：不只是拒绝
])
def test_other_utterances_are_not_refusals(text):
    assert refuses_safety_advice(text) is False, text


@pytest.mark.parametrize("text", [
    "我不太困了", "没那么困了", "我不怎么困了",      # 否定隔着程度副词（饮酒不跳，见上）
    "这题好困难", "有点困惑", "被困了",                # 「困」字开头的另一个词 / 被动
])
def test_not_sleepy(text):
    assert driver_state_mentioned(text) == "", text


@pytest.mark.parametrize("text, state", [
    ("开车特别犯困", "fatigue"),                       # 「特别」不是否定
    ("我主要是太困了", "fatigue"),                     # 「主要是」不是假设框架
    ("我困了，要是继续开怎么办", "fatigue"),           # 假设框架只管它自己那一分句
    ("昨晚熬夜，开车要注意什么", "fatigue"),           # 有此刻 / 说话人的锚，话题名词就是在说自己
    ("熬夜了还能开吗", "fatigue"),                     # 话题名词后面跟「了」= 发生过的事
    ("要是方便的话陪我聊会儿，我太困了", "fatigue"),   # 假设框架在前一分句
    ("熬夜加班，开车回家", "fatigue"),                 # 不是问句：话题名词只在问句里当话题
])
def test_self_reports_that_look_like_negations_or_topics(text, state):
    assert driver_state(text) == state, text


@pytest.mark.parametrize("text", [
    "现在还能继续开吗", "我可以不休息吗",      # 问句：在问能不能，不是拒绝
    "继续开导航", "我不继续开了", "要不休息一下吧", "不用提醒我带伞", "我没事做", "我撑不住了",
])
def test_near_misses_are_not_refusals(text):
    assert refuses_safety_advice(text) is False, text


# ── 追加批 M（2026-09-24；设计 §16）：会话告警名 → 驾驶员状态 ─────────────────────────────────

from runtime.safety_signal import DRIVER_STATE_ADVICE, driver_state_of_signal  # noqa: E402


def test_every_driver_state_signal_maps_back_to_its_state():
    for state, spec in DRIVER_STATE_ADVICE.items():
        assert driver_state_of_signal(spec["signal"]) == state


@pytest.mark.parametrize("signal", ["机油灯", "胎压灯", "车辆告警", "", None])
def test_vehicle_signals_are_not_driver_states(signal):
    assert driver_state_of_signal(signal) == ""
