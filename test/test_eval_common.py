"""eval_common.py 自身回归测试（T3.4）——合成数据，不跑真实 fast_intent/route_hints。

保护 build_report/diff_against_baseline 这层报告基础设施未来被重构时不悄悄改坏 diff 逻辑。
"""
import sys
from pathlib import Path

# test/ 无 __init__.py，CI 用 --import-mode=importlib 不会自动把本文件所在目录加入
# sys.path；同目录兄弟模块导入需显式插入（同 e2e_degrade.py 等既有惯例）。
sys.path.insert(0, str(Path(__file__).parent))
from eval_common import CaseResult, build_report, diff_against_baseline, load_baseline  # noqa: E402


def _case(id_, bucket, passed, actual="x", expected="x"):
    return CaseResult(id=id_, bucket=bucket, text=id_, expected=expected, actual=actual, passed=passed)


def test_build_report_aggregates_buckets_and_overall():
    cases = [
        _case("a::1", "a", True),
        _case("a::2", "a", False, actual="y"),
        _case("b::1", "b", True),
    ]
    report = build_report("demo", [{"path": "x.yaml", "count": 3}], cases)
    assert report["buckets"]["a"]["total"] == 2
    assert report["buckets"]["a"]["passed"] == 1
    assert report["buckets"]["a"]["pass_rate"] == 0.5
    assert report["buckets"]["b"]["pass_rate"] == 1.0
    assert report["overall"]["total"] == 3
    assert report["overall"]["passed"] == 2
    assert set(report["cases"]) == {"a::1", "a::2", "b::1"}


def test_build_report_empty_cases_no_division_by_zero():
    report = build_report("demo", [], [])
    assert report["overall"]["total"] == 0
    assert report["overall"]["pass_rate"] == 0.0


def test_diff_against_baseline_detects_regression():
    baseline = build_report("demo", [], [_case("a::1", "a", True)])
    current = build_report("demo", [], [_case("a::1", "a", False, actual="broken")])
    diff = diff_against_baseline(current, baseline)
    assert diff.has_regressions is True
    assert diff.regressions[0][0] == "a::1"
    assert diff.regressions[0][2]["actual"] == "broken"


def test_diff_against_baseline_detects_improvement_not_regression():
    baseline = build_report("demo", [], [_case("a::1", "a", False)])
    current = build_report("demo", [], [_case("a::1", "a", True)])
    diff = diff_against_baseline(current, baseline)
    assert diff.has_regressions is False
    assert diff.improvements[0][0] == "a::1"


def test_diff_against_baseline_stable_pass_is_neither():
    baseline = build_report("demo", [], [_case("a::1", "a", True)])
    current = build_report("demo", [], [_case("a::1", "a", True)])
    diff = diff_against_baseline(current, baseline)
    assert diff.has_regressions is False
    assert diff.regressions == []
    assert diff.improvements == []


def test_diff_against_baseline_tracks_new_and_removed_cases():
    baseline = build_report("demo", [], [_case("a::1", "a", True)])
    current = build_report("demo", [], [_case("a::2", "a", True)])
    diff = diff_against_baseline(current, baseline)
    assert diff.new_cases == ["a::2"]
    assert diff.removed_cases == ["a::1"]
    assert diff.has_regressions is False  # 新增/移除用例不算回归


def test_load_baseline_missing_file_returns_none(tmp_path: Path):
    assert load_baseline(tmp_path / "does_not_exist.json") is None
