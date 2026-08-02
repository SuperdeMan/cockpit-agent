"""CLI 参数、退出码、baseline 硬闸与 L3 子进程的回归测试。"""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402

from eval_intent_adversarial import (  # noqa: E402
    known_journey_ids, load_journey_links, parse_args, run_l3, select_cases,
    validate_args, write_baseline_if_eligible,
)
from support.intent_adversarial_contract import (  # noqa: E402
    AdversarialCase, CaseTurn, SuiteConfig, TurnExpectation,
)
from support.intent_adversarial_report import BaselineEligibility  # noqa: E402


def _suite(statuses, live_statuses):
    return SuiteConfig(statuses=statuses, live_statuses=live_statuses,
                       min_cases=1, max_cases=999, attack_minimums={},
                       normal_repeats=1, failure_repeats=3, high_risk_repeats=3)


def _case(case_id, *, status="reviewed", layers=("l0", "l1"), tags=None, risk="medium",
          cohort="unseen_transfer"):
    merged = {"attacks": ["A1"], "layers": list(layers)}
    merged.update(tags or {})
    return AdversarialCase(
        id=case_id, title=case_id, family_id=case_id, cohort=cohort, risk=risk,
        status=status, tags=merged, provenance={"kind": "authored"},
        turns=(CaseTurn(utterance="查天气", context={},
                        expected=TurnExpectation()),))


def test_default_is_offline_l0_discovery():
    args = parse_args([])
    assert args.suite == "discovery"
    assert args.layer == "l0"
    assert args.live is False
    assert args.retrieval_state == "warm"


def test_l1_l2_require_live():
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--layer", "l1"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args([
            "--layer", "l1", "--live", "--provider", "mimo"]))
    validate_args(parse_args([
        "--layer", "l1", "--live", "--provider", "mimo",
        "--model", "mimo-model"]))


def test_write_baseline_requires_gate_and_explicit_provider():
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--write-baseline", "--suite", "discovery"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--write-baseline", "--suite", "gate", "--live"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args([
            "--write-baseline", "--suite", "gate", "--live",
            "--provider", "mimo", "--model", "mimo-model",
            "--layer", "l1"]))


def test_cold_start_never_reaches_gate():
    with pytest.raises(SystemExit):
        validate_args(parse_args([
            "--suite", "gate", "--retrieval-state", "cold", "--layer", "l1",
            "--live", "--provider", "mimo", "--model", "m"]))


def test_cli_has_no_bypass_flags():
    for flag in ("--update-baseline", "--accept-failures", "--force"):
        with pytest.raises(SystemExit):
            parse_args([flag])


def test_candidates_never_reach_a_live_layer():
    cases = [_case("cand", status="candidate"), _case("rev", status="reviewed")]
    suite = _suite(("candidate", "reviewed", "stable"), ("reviewed", "stable"))
    offline = select_cases(cases, parse_args(["--layer", "l0"]), suite)
    live = select_cases(cases, parse_args([
        "--layer", "l1", "--live", "--provider", "p", "--model", "m"]), suite)
    assert {c.id for c in offline} == {"cand", "rev"}
    assert {c.id for c in live} == {"rev"}


def test_tag_filter_matches_values_and_truthy_keys():
    cases = [_case("a", tags={"attacks": ["A7"], "gate_candidate": True}),
             _case("b", tags={"attacks": ["A1"]})]
    suite = _suite(("reviewed",), ("reviewed",))
    assert {c.id for c in select_cases(
        cases, parse_args(["--tag", "A7"]), suite)} == {"a"}
    assert {c.id for c in select_cases(
        cases, parse_args(["--tag", "gate_candidate"]), suite)} == {"a"}
    assert select_cases(
        cases, parse_args(["--tag", "A7", "--tag", "A1"]), suite) == []


def test_l3_uses_existing_runner_and_selected_journey_ids(monkeypatch):
    called = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: called.update(
        argv=argv, env=kw["env"]) or SimpleNamespace(returncode=0))
    assert run_l3(
        ["A1-1", "B4-1"], provider="mimo", model="mimo-model") == 0
    assert called["argv"] == [sys.executable, "scripts/run_e2e.py", "--id",
                              "e2e_journeys", "--provider", "mimo",
                              "--model", "mimo-model"]
    assert called["env"]["E2E_JOURNEY_IDS"] == "A1-1,B4-1"


def test_journey_links_reject_unknown_journey_ids(tmp_path):
    path = tmp_path / "journey_links.yaml"
    path.write_text("schema_version: 1\nlinks:\n  some.case: [NOPE-9]\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="unknown journeys"):
        load_journey_links(path)


def test_known_journey_ids_reads_the_real_corpus():
    ids = known_journey_ids()
    assert "A1-1" in ids and len(ids) > 10


def test_ineligible_run_never_touches_formal_baseline(tmp_path):
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    rejected_json = tmp_path / "_ci-run-rejected.json"
    rejected_md = tmp_path / "_ci-run-rejected.md"
    formal_json.write_text("old-json", encoding="utf-8")
    formal_md.write_text("old-md", encoding="utf-8")

    written = write_baseline_if_eligible(
        {"meta": {}}, "diagnostic", BaselineEligibility(False, ("l3_empty",)),
        formal_json, formal_md, rejected_json, rejected_md)

    assert written is False
    assert formal_json.read_text(encoding="utf-8") == "old-json"
    assert formal_md.read_text(encoding="utf-8") == "old-md"
    assert rejected_json.is_file() and rejected_md.is_file()


def test_eligible_run_writes_the_formal_pair(tmp_path):
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    written = write_baseline_if_eligible(
        {"meta": {}}, "official", BaselineEligibility(True, ()),
        formal_json, formal_md, tmp_path / "r.json", tmp_path / "r.md")
    assert written is True
    assert formal_md.read_text(encoding="utf-8") == "official"


def test_list_never_requires_live_because_it_runs_no_model():
    for layer in ("l1", "l2", "l3", "all"):
        validate_args(parse_args(["--layer", layer, "--list"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--layer", "l3"]))


def test_journey_links_resolve_against_the_real_journey_corpus():
    links = load_journey_links()
    assert links, "L3 选集为空时 baseline 会被 l3_empty 拒掉，链接不能是空的"
    known = known_journey_ids()
    for case_id, journeys in links.items():
        assert journeys, f"{case_id} 链接了空 journey 列表"
        for journey in journeys:
            assert journey in known


def test_l2_case_path_never_nests_event_loops():
    """守住 `cases=0` 那个坑：L2 一条 case 要经 seed → Edge servicer → Engine 三段，
    每段都自己驱动事件循环。任何一段被包进 async 再 asyncio.run 就整层抛，
    而调用方会把它吞成基础设施错误——报告看起来像「没有可跑的用例」。"""
    import inspect

    import eval_intent_adversarial as cli
    from support import intent_adversarial_runtime as rt

    assert not inspect.iscoroutinefunction(cli.run_l2_case)
    for name in ("run_full_entry_turn", "run_edge_turn", "run_retrieval_turn"):
        assert not inspect.iscoroutinefunction(getattr(rt, name)), name
    for name in ("seed_pending_confirm", "seed_focus", "run"):
        assert not inspect.iscoroutinefunction(getattr(rt.EngineHarness, name)), name
    # 反过来，L1 是纯 Planner 调用，必须是 async（由调用方 asyncio.run 驱动）
    assert inspect.iscoroutinefunction(cli.run_l1_case)
    assert inspect.iscoroutinefunction(rt.EngineHarness.run_async)
