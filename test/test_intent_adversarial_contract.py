"""意图对抗语料契约的自身回归测试。

只测「契约怎么读、怎么校验」——不跑 Edge、不跑 Planner、不连网络。
语料本身的质量由 coverage/boundary 门禁在同一模块里另行断言。
"""
import sys
from pathlib import Path

# test/ 无 __init__.py，CI 用 --import-mode=importlib 不会自动把本文件所在目录加入
# sys.path；同目录兄弟模块导入需显式插入（同 test_eval_common.py 等既有惯例）。
sys.path.insert(0, str(Path(__file__).parent))

from support.intent_adversarial_contract import load_cases, load_suites  # noqa: E402


def test_load_cases_parses_one_turn_and_plan_groups(tmp_path: Path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "a.yaml").write_text("""
schema_version: 1
cases:
  - id: boundary.weather_alert.info
    title: 裸问天气预警
    family_id: boundary.weather_alert
    cohort: unseen_transfer
    risk: medium
    status: reviewed
    tags: {attacks: [A1], mechanisms: [domain_collision], domains: [info, safety], boundary: weather-alert, boundary_ledger: info-safety.weather-alert, boundary_side: left, layers: [l0, l1]}
    provenance: {kind: boundary_ledger, source_ref: weather-alert, reviewed_by: human, reviewed_at: 2026-08-02}
    turns:
      - input: {utterance: 有没有天气预警, context: {}}
        expected:
          addressed: true
          ingress: {allowed: [cloud], forbidden: [edge_local]}
          decision: {allowed: [execute], clarify: forbidden}
          plan:
            required_intent_groups: [{any_of: [info.alerts]}]
            forbidden_intents: [safety.weather_alert]
            allow_extra_intents: false
""", encoding="utf-8")

    cases = load_cases(root)

    assert len(cases) == 1
    assert cases[0].id == "boundary.weather_alert.info"
    assert cases[0].turns[0].expected.plan.required_groups[0].any_of == ("info.alerts",)
    assert cases[0].turns[0].expected.plan.forbidden_intents == ("safety.weather_alert",)
    assert cases[0].turns[0].expected.plan.assert_plan is True


def test_load_cases_keeps_replan_observation_and_expected_plan(tmp_path: Path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "adaptive.yaml").write_text("""
schema_version: 1
cases:
  - id: composition.weather-outing
    title: 天气出游
    family_id: composition.weather-outing
    cohort: seen_regression
    risk: medium
    status: candidate
    tags: {attacks: [A4], mechanisms: [composition], domains: [info, nearby], layers: [l1, l2]}
    provenance: {kind: authored}
    turns:
      - input: {utterance: 今天的天气适合去哪玩, context: {}}
        expected:
          plan:
            required_intent_groups: [{any_of: [info.weather]}]
          replans:
            - after:
                result: {step_id: s1, status: ok, data: {condition: 中雨}, speech: 有中雨}
              plan:
                required_intent_groups: [{any_of: [nearby.search]}]
""", encoding="utf-8")

    turn = load_cases(root)[0].turns[0]
    assert turn.expected.replans[0].after["result"]["data"] == {"condition": "中雨"}
    assert turn.expected.replans[0].plan.required_groups[0].any_of == ("nearby.search",)


def test_load_suites_keeps_run_policy_out_of_gold(tmp_path: Path):
    path = tmp_path / "suites.yaml"
    path.write_text("""
schema_version: 1
suites:
  discovery:
    statuses: [candidate, reviewed, stable]
    live_statuses: [reviewed, stable]
    min_cases: 450
    max_cases: 500
    attack_minimums: {A1: 80, A2: 50, A3: 45, A4: 60, A5: 50, A6: 45, A7: 40, A8: 35, A9: 45}
    normal_repeats: 1
    failure_repeats: 3
    high_risk_repeats: 3
  gate:
    statuses: [stable]
    live_statuses: [stable]
    min_cases: 120
    max_cases: 160
    attack_minimums: {}
    normal_repeats: 1
    failure_repeats: 3
    high_risk_repeats: 3
""", encoding="utf-8")

    suites = load_suites(path)

    assert suites["gate"].statuses == ("stable",)
    assert suites["gate"].live_statuses == ("stable",)
    assert (suites["gate"].min_cases, suites["gate"].max_cases) == (120, 160)
    assert suites["discovery"].attack_minimums["A4"] == 60
    assert suites["gate"].failure_repeats == 3


# ── 严格契约：未知键、状态/来源、relation 与 family 泄漏 ────────────────────
from dataclasses import replace  # noqa: E402

import pytest  # noqa: E402

from support.intent_adversarial_contract import (  # noqa: E402
    AdversarialCase, CaseTurn, IntentGroup, PlanExpectation, RelationSpec,
    TurnExpectation, validate_cases,
)


@pytest.fixture
def contract_case():
    return AdversarialCase(
        id="boundary.weather_alert.info",
        title="裸问天气预警",
        family_id="boundary.weather_alert",
        cohort="unseen_transfer",
        risk="medium",
        status="reviewed",
        tags={"attacks": ["A1"], "mechanisms": ["domain_collision"],
              "domains": ["info", "safety"],
              "boundary": "weather-alert",
              "boundary_ledger": "info-safety.weather-alert",
              "boundary_side": "left",
              "layers": ["l0", "l1"]},
        provenance={"kind": "boundary_ledger", "reviewed_by": "human",
                    "reviewed_at": "2026-08-02"},
        turns=(CaseTurn(
            utterance="有没有天气预警",
            context={},
            expected=TurnExpectation(plan=PlanExpectation(
                assert_plan=True,
                required_groups=(IntentGroup(("info.alerts",)),),
            )),
        ),),
    )


def test_duplicate_ids_are_rejected(contract_case):
    errors = validate_cases([contract_case, contract_case], {"info.alerts"})
    assert any("duplicate id" in e for e in errors)


def test_unknown_required_intent_is_rejected(contract_case):
    errors = validate_cases([contract_case], set())
    assert any("unknown intent info.alerts" in e for e in errors)


def test_required_and_forbidden_conflict_is_rejected(contract_case):
    plan = replace(contract_case.turns[0].expected.plan,
                   forbidden_intents=("info.alerts",))
    expected = replace(contract_case.turns[0].expected, plan=plan)
    turn = replace(contract_case.turns[0], expected=expected)
    errors = validate_cases([replace(contract_case, turns=(turn,))], {"info.alerts"})
    assert any("required and forbidden" in e for e in errors)


def test_stable_requires_human_review(contract_case):
    case = replace(contract_case, status="stable",
                   provenance={"kind": "llm_candidate", "reviewed_by": "model"})
    errors = validate_cases([case], {"info.alerts"})
    assert any("stable requires reviewed_by=human" in e for e in errors)


def test_relation_base_must_exist(contract_case):
    relation = RelationSpec("missing.base", "invariant", {})
    errors = validate_cases([replace(contract_case, relation=relation)], {"info.alerts"})
    assert any("missing relation base" in e for e in errors)


def test_family_cannot_mix_seen_and_unseen(contract_case):
    seen = replace(contract_case, id="seen", cohort="seen_regression")
    unseen = replace(contract_case, id="unseen", cohort="unseen_transfer")
    errors = validate_cases([seen, unseen], {"info.alerts"})
    assert any("family leakage" in e for e in errors)


def test_reviewed_turn_requires_absolute_gold(contract_case):
    empty = replace(contract_case.turns[0], expected=TurnExpectation())
    errors = validate_cases(
        [replace(contract_case, turns=(empty,))], {"info.alerts"})
    assert any("reviewed turn requires absolute gold" in e for e in errors)


def test_unknown_relation_expectation_key_is_rejected(contract_case):
    relation = RelationSpec(
        contract_case.id, "route_flip", {"forbiden_after": ["info.alerts"]})
    errors = validate_cases(
        [contract_case, replace(contract_case, id="variant", relation=relation)],
        {"info.alerts"})
    assert any("unknown relation expectation keys" in e for e in errors)


def test_relation_pair_must_declare_the_same_layers(contract_case):
    variant = replace(
        contract_case, id="variant",
        tags={**contract_case.tags, "layers": ["l2"]},
        relation=RelationSpec(contract_case.id, "invariant", {}))
    errors = validate_cases(
        [contract_case, variant], {"info.alerts"})
    assert any("relation pair must share layers" in e for e in errors)


def test_stable_relation_requires_stable_base(contract_case):
    variant = replace(
        contract_case, id="variant", status="stable",
        relation=RelationSpec(contract_case.id, "invariant", {}))
    errors = validate_cases(
        [contract_case, variant], {"info.alerts"})
    assert any("stable relation requires stable base" in e for e in errors)


def test_relation_cannot_reference_itself(contract_case):
    case = replace(
        contract_case,
        relation=RelationSpec(contract_case.id, "invariant", {}))
    errors = validate_cases([case], {"info.alerts"})
    assert any("relation base must be a distinct root case" in e for e in errors)


def test_unknown_plan_key_is_rejected(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "bad.yaml").write_text("""
schema_version: 1
cases:
  - id: bad
    title: bad
    family_id: bad
    cohort: unseen_transfer
    risk: low
    status: candidate
    tags: {attacks: [expression], domains: [info]}
    provenance: {kind: authored}
    turns:
      - input: {utterance: 查天气, context: {}}
        expected:
          plan: {required_intent_groups: [{any_of: [info.weather]}], typo_key: true}
""", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown keys.*typo_key"):
        load_cases(root)


def test_duplicate_yaml_key_is_rejected(tmp_path):
    path = tmp_path / "suites.yaml"
    path.write_text("schema_version: 1\nschema_version: 1\nsuites: {}\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate YAML key schema_version"):
        load_suites(path)


# ── 覆盖盘点、边界台账与检索引用 ─────────────────────────────────────────
import eval_live  # noqa: E402

from support.intent_adversarial_contract import (  # noqa: E402
    RetrievalExpectation, load_boundary_ledger, load_coverage_exemptions,
    validate_boundary_coverage, validate_coverage, validate_retrieval_references,
)

_ROOT = Path(__file__).resolve().parent.parent


def test_active_intent_must_be_covered_or_exempt(contract_case):
    errors = validate_coverage([contract_case],
                               active_intents={"info.alerts", "new.intent"},
                               exemptions={})
    assert any("new.intent positive has 0, need 2" in error for error in errors)


def test_any_of_group_does_not_fake_per_intent_positive_coverage(contract_case):
    plan = replace(contract_case.turns[0].expected.plan,
                   required_groups=(IntentGroup(("info.alerts", "info.weather")),))
    turn = replace(contract_case.turns[0], expected=replace(
        contract_case.turns[0].expected, plan=plan))
    errors = validate_coverage(
        [replace(contract_case, turns=(turn,))],
        active_intents={"info.alerts", "info.weather"}, exemptions={})
    assert any("info.alerts positive has 0" in error for error in errors)


def test_candidate_cases_do_not_count_towards_authoritative_coverage(contract_case):
    candidate = replace(contract_case, status="candidate")
    errors = validate_coverage([candidate], active_intents={"info.alerts"},
                               exemptions={})
    assert any("info.alerts positive has 0" in error for error in errors)


def test_exemption_only_waives_the_named_requirement(contract_case):
    errors = validate_coverage([], active_intents={"info.alerts"},
                               exemptions={"info.alerts": {"positive"}})
    kinds = {error.split()[3] for error in errors}
    assert kinds == {"hard_negative", "relation"}


def test_active_inventory_includes_admitted_mcp_capabilities():
    assert "shop.order" in eval_live.known_intents()


def test_boundary_requires_two_cases_per_side(contract_case):
    errors = validate_boundary_coverage(
        [contract_case],
        boundaries={"info-safety.weather-alert": ("info", "safety")},
        minimum_per_side=2)
    assert any("info-safety.weather-alert" in error and "left" in error
               for error in errors)


def test_boundary_side_needs_required_and_forbidden_to_count(contract_case):
    plan = replace(contract_case.turns[0].expected.plan,
                   forbidden_intents=("safety.weather_alert",))
    proving = replace(contract_case, turns=(replace(
        contract_case.turns[0], expected=replace(
            contract_case.turns[0].expected, plan=plan)),))
    errors = validate_boundary_coverage(
        [proving],
        boundaries={"info-safety.weather-alert": ("info", "safety")},
        minimum_per_side=1)
    assert not [e for e in errors if "does not prove" in e]


def test_boundary_ledger_maps_stable_ids_to_declared_domain_order():
    ledger = load_boundary_ledger(_ROOT / "skills" / "exemplars" / "boundaries.yaml")
    assert ledger["info-safety.weather-alert"] == ("info", "safety")
    assert ledger["navigation-vision.where-vs-what"] == ("navigation", "vision")
    assert len(ledger) == 18


def test_boundary_ledger_rejects_missing_duplicate_id_and_empty_why(tmp_path):
    missing = tmp_path / "missing.yaml"
    missing.write_text(
        "rulings:\n  - texts: [a, b]\n    domains: [info, safety]\n    why: x\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="requires a stable id"):
        load_boundary_ledger(missing)

    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        "rulings:\n"
        "  - id: info-safety.x\n    texts: [a, b]\n    domains: [info, safety]\n    why: x\n"
        "  - id: info-safety.x\n    texts: [c, d]\n    domains: [info, safety]\n    why: y\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate ruling id"):
        load_boundary_ledger(duplicate)

    empty_why = tmp_path / "why.yaml"
    empty_why.write_text(
        "rulings:\n  - id: info-safety.x\n    texts: [a, b]\n"
        "    domains: [info, safety]\n    why: '  '\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="requires why"):
        load_boundary_ledger(empty_why)


def test_unknown_retrieval_reference_is_a_contract_error(contract_case):
    expected = replace(
        contract_case.turns[0].expected,
        retrieval=RetrievalExpectation(required_skills=("typo-guide",)))
    case = replace(contract_case, turns=(replace(
        contract_case.turns[0], expected=expected),))
    errors = validate_retrieval_references(
        [case], skill_names={"weather-outing"}, exemplar_ids={"info#1"})
    assert any("unknown skill typo-guide" in error for error in errors)


def test_coverage_exemptions_reject_wildcards_and_empty_reasons(tmp_path):
    path = tmp_path / "coverage_exemptions.yaml"
    path.write_text(
        "schema_version: 1\nexemptions:\n"
        "  - intent: info.alerts\n    requirements: [positive]\n"
        "    reason: ''\n    owner: 泓舟\n    reviewed_at: 2026-08-02\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="reason must not be empty"):
        load_coverage_exemptions(path, {"info.alerts"})

    path.write_text(
        "schema_version: 1\nexemptions:\n"
        "  - intent: info.*\n    requirements: [positive]\n"
        "    reason: 批量\n    owner: 泓舟\n    reviewed_at: 2026-08-02\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="unknown intent"):
        load_coverage_exemptions(path, {"info.alerts"})

    path.write_text(
        "schema_version: 1\nexemptions:\n"
        "  - intent: info.alerts\n    requirements: [everything]\n"
        "    reason: 批量\n    owner: 泓舟\n    reviewed_at: 2026-08-02\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="invalid requirements"):
        load_coverage_exemptions(path, {"info.alerts"})
