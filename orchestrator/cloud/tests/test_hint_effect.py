"""_hint_effect：RouteHintEngine 作用分类（数据飞轮 P0 落域可观测，纯观测不改行为）。"""
from orchestrator.cloud.planning import _hint_effect


def test_no_hit_is_empty():
    assert _hint_effect(False, [], [], False) == ""
    assert _hint_effect(False, [("a", "a.x")], [("a", "a.x")], False) == ""


def test_hit_but_unchanged_is_noop():
    plan = [("info", "info.search")]
    assert _hint_effect(True, plan, list(plan), False) == "noop"


def test_fill_on_empty_plan():
    after = [("nearby", "nearby.search")]
    assert _hint_effect(True, [], after, False) == "fill"


def test_fill_over_clarify_flags_d3():
    """D3：hint 在澄清之前生效——空计划带 clarify 被补步 = 澄清被跳过（裁决数据）。"""
    after = [("vision", "vision.describe")]
    assert _hint_effect(True, [], after, True) == "fill_over_clarify"


def test_append_extends_prefix():
    before = [("info", "info.weather")]
    after = [("info", "info.weather"), ("trip-planner", "trip.plan")]
    assert _hint_effect(True, before, after, False) == "append"


def test_replace_rewrites_plan():
    before = [("chitchat", "chitchat.talk")]
    after = [("deep-research", "research.run")]
    assert _hint_effect(True, before, after, False) == "replace"
