"""历史 RoutingBench `domain_hit_rate` 口径不变回归。

正名只改名字与说明，不改判定：任何一条历史用例的 pass/fail 都必须逐字不变，
否则「保留历史趋势」就变成了另一条新曲线。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from routing_bench import domain_hit  # noqa: E402


def test_domain_hit_preserves_legacy_any_domain_semantics():
    assert domain_hit(["info.weather"], {"info", "nearby"}) is True


def test_domain_hit_rejects_no_expected_domain_overlap():
    assert domain_hit(["chitchat.talk"], {"info", "nearby"}) is False


def test_domain_hit_is_deliberately_blind_to_a_missing_second_domain():
    """这正是它不能当组合完整性尺子的原因——期望两域只命中一域，它照样绿。"""
    assert domain_hit(["info.weather"], {"info", "nearby"}) is True
    assert domain_hit(["info.weather", "nearby.search"], {"info", "nearby"}) is True


def test_domain_hit_handles_empty_plan_and_empty_expectation():
    assert domain_hit([], {"info"}) is False
    assert domain_hit(["info.weather"], set()) is False
