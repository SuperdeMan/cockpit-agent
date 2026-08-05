"""意图对抗语料契约的自身回归测试。

只测「契约怎么读、怎么校验」——不跑 Edge、不跑 Planner、不连网络。
语料本身的质量由 coverage/boundary 门禁在同一模块里另行断言。
"""
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

# test/ 无 __init__.py，CI 用 --import-mode=importlib 不会自动把本文件所在目录加入
# sys.path；同目录兄弟模块导入需显式插入（同 test_eval_common.py 等既有惯例）。
sys.path.insert(0, str(Path(__file__).parent))

from support.intent_adversarial_contract import load_cases, load_suites  # noqa: E402


def test_shop_menu_then_order_requires_the_item_to_cross_the_dependency():
    """仅有拓扑边不够：用户说「招牌」时，商品名必须来自菜单结果。

    2026-08-04 定向 live 的第二独立进程产出 `depends_on=[s1]` 但 `slot_refs={}`，
    旧 gold 因 carries 为空仍判绿。那会让 executor 排对顺序，却在下单时没有商品。
    """
    root = Path(__file__).parent / "eval_corpus" / "intent_adversarial" / "cases"
    case = next(c for c in load_cases(root) if c.id == "cp.dep.menu-then-order")
    dep = case.turns[0].expected.plan.dependencies[0]
    assert dep.producer == ("shop.menu",)
    assert dep.consumer == "shop.order"
    assert dep.carries == ("item",)


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
    independent_processes: 1
    independent_layers: []
  gate:
    statuses: [stable]
    live_statuses: [stable]
    min_cases: 120
    max_cases: 160
    attack_minimums: {}
    normal_repeats: 3
    failure_repeats: 3
    high_risk_repeats: 3
    independent_processes: 2
    independent_layers: [l1, l2]
""", encoding="utf-8")

    suites = load_suites(path)

    assert suites["gate"].statuses == ("stable",)
    assert suites["gate"].live_statuses == ("stable",)
    assert (suites["gate"].min_cases, suites["gate"].max_cases) == (120, 160)
    assert suites["discovery"].attack_minimums["A4"] == 60
    assert suites["gate"].failure_repeats == 3
    assert suites["gate"].normal_repeats == 3
    assert suites["gate"].independent_processes == 2
    assert suites["gate"].independent_layers == ("l1", "l2")
    assert suites["discovery"].independent_processes == 1
    assert suites["discovery"].independent_layers == ()


def test_formal_suites_declare_the_independent_process_policy():
    path = (Path(__file__).parent / "eval_corpus" / "intent_adversarial" /
            "suites.yaml")

    suites = load_suites(path)

    assert suites["gate"].independent_processes == 2
    assert suites["gate"].independent_layers == ("l1", "l2")
    assert suites["discovery"].independent_processes == 1
    assert suites["discovery"].independent_layers == ()


def test_gate_suite_rejects_single_sample_normal_policy(tmp_path: Path):
    """正式 gate 至少要能观测进程内方差；`normal_repeats: 1` 不再是合法配置。"""
    path = tmp_path / "suites.yaml"
    path.write_text("""
schema_version: 1
suites:
  discovery:
    statuses: [candidate, reviewed, stable]
    live_statuses: [reviewed, stable]
    min_cases: 1
    max_cases: 10
    attack_minimums: {}
    normal_repeats: 3
    failure_repeats: 3
    high_risk_repeats: 3
    independent_processes: 1
    independent_layers: []
  gate:
    statuses: [stable]
    live_statuses: [stable]
    min_cases: 1
    max_cases: 10
    attack_minimums: {}
    normal_repeats: 1
    failure_repeats: 3
    high_risk_repeats: 3
    independent_processes: 2
    independent_layers: [l1, l2]
""", encoding="utf-8")

    with pytest.raises(ValueError, match=r"gate\.normal_repeats must be >= 3"):
        load_suites(path)


# ── 严格契约：未知键、状态/来源、relation 与 family 泄漏 ────────────────────
from dataclasses import replace  # noqa: E402

from support.intent_adversarial_contract import (  # noqa: E402
    AdversarialCase, CaseTurn, IntentGroup, PlanExpectation, RelationSpec,
    SuiteConfig, TurnExpectation, validate_cases,
)


def _write_minimal_suites(path: Path, *, gate_processes: object = 2,
                          gate_layers: object = "[l1, l2]",
                          discovery_processes: object = 1,
                          discovery_layers: object = "[]") -> None:
    path.write_text(f"""
schema_version: 1
suites:
  discovery:
    statuses: [candidate, reviewed, stable]
    live_statuses: [reviewed, stable]
    min_cases: 1
    max_cases: 10
    attack_minimums: {{}}
    normal_repeats: 1
    failure_repeats: 3
    high_risk_repeats: 3
    independent_processes: {discovery_processes}
    independent_layers: {discovery_layers}
  gate:
    statuses: [stable]
    live_statuses: [stable]
    min_cases: 1
    max_cases: 10
    attack_minimums: {{}}
    normal_repeats: 3
    failure_repeats: 3
    high_risk_repeats: 3
    independent_processes: {gate_processes}
    independent_layers: {gate_layers}
""", encoding="utf-8")


def test_suite_config_process_policy_defaults_are_backward_compatible():
    suite = SuiteConfig(
        statuses=("reviewed",), live_statuses=("reviewed",),
        min_cases=1, max_cases=10, attack_minimums={},
        normal_repeats=1, failure_repeats=3, high_risk_repeats=3,
    )

    assert suite.independent_processes == 1
    assert suite.independent_layers == ()


def test_gate_suite_requires_at_least_two_independent_processes(tmp_path: Path):
    path = tmp_path / "suites.yaml"
    _write_minimal_suites(path, gate_processes=1)

    with pytest.raises(
            ValueError, match=r"gate\.independent_processes must be >= 2"):
        load_suites(path)


@pytest.mark.parametrize(
    "layers",
    (
        "[l0]", "[l3]", "[l1, l3]",
        "[]", "[l1]", "[l2]",
        "[l1, l1, l2]", "[l1, l2, l2]",
    ),
)
def test_gate_suite_requires_exactly_one_l1_and_one_l2_independent_layer(
        tmp_path: Path, layers: str):
    path = tmp_path / "suites.yaml"
    _write_minimal_suites(path, gate_layers=layers)

    with pytest.raises(
            ValueError,
            match=(r"gate\.independent_layers must contain exactly "
                   r"one l1 and one l2")):
        load_suites(path)


@pytest.mark.parametrize("suite_name", ("gate", "discovery"))
@pytest.mark.parametrize(
    "raw_processes",
    ('"2"', "2.0", "true", "{count: 2}"),
)
def test_suite_independent_processes_rejects_coercible_or_structured_values(
        tmp_path: Path, suite_name: str, raw_processes: str):
    path = tmp_path / "suites.yaml"
    kwargs = {f"{suite_name}_processes": raw_processes}
    _write_minimal_suites(path, **kwargs)

    with pytest.raises(
            ValueError,
            match=(rf"{suite_name}\.independent_processes must be a "
                   r"non-boolean integer")):
        load_suites(path)


@pytest.mark.parametrize("suite_name", ("gate", "discovery"))
@pytest.mark.parametrize(
    "raw_layers",
    ("l1", "{first: l1}", "1", "[l1, 2]", "[l1, '']"),
)
def test_suite_independent_layers_rejects_scalars_mappings_and_bad_items(
        tmp_path: Path, suite_name: str, raw_layers: str):
    path = tmp_path / "suites.yaml"
    kwargs = {f"{suite_name}_layers": raw_layers}
    _write_minimal_suites(path, **kwargs)

    with pytest.raises(
            ValueError,
            match=(rf"{suite_name}\.independent_layers must be a list/tuple "
                   r"of non-empty strings")):
        load_suites(path)


def test_discovery_suite_rejects_multiple_independent_processes(tmp_path: Path):
    path = tmp_path / "suites.yaml"
    _write_minimal_suites(path, discovery_processes=2)

    with pytest.raises(
            ValueError, match=r"discovery\.independent_processes must be 1"):
        load_suites(path)


def test_discovery_suite_rejects_independent_layers(tmp_path: Path):
    path = tmp_path / "suites.yaml"
    _write_minimal_suites(path, discovery_layers="[l1]")

    with pytest.raises(
            ValueError, match=r"discovery\.independent_layers must be empty"):
        load_suites(path)


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


# ── P1-7 反向构造：family 之外的泄漏 ─────────────────────────────────────


def test_same_sentence_in_both_cohorts_is_rejected_even_with_different_families(
        contract_case):
    """family 闸只防住「作者记得它们同源」的那一半：换个 family id 就能绕过。

    实际语料里 `今天天气怎么样` 同时以 seen 和 unseen 出现过 4 次，family 闸全绿。
    """
    from support.intent_adversarial_contract import validate_cohort_isolation

    seen = replace(contract_case, id="other.seen", family_id="another.family",
                   cohort="seen_regression")
    # family 闸对这一对全绿——两条 family_id 本来就不同
    assert not [e for e in validate_cases([contract_case, seen], {"info.alerts"})
                if "family leakage" in e]
    errors = validate_cohort_isolation([contract_case, seen])
    assert any("cohort leakage" in e and "有没有天气预警" in e for e in errors)


def test_unseen_cannot_claim_a_sentence_that_is_literally_in_the_knowledge(
        contract_case):
    from support.intent_adversarial_contract import validate_cohort_isolation

    errors = validate_cohort_isolation([contract_case], {"有没有天气预警"})
    assert any("literally present in the injected knowledge" in e for e in errors)
    # seen 一侧不设对称断言：知识里没这句话不代表它没被拿去改过规则（Hint 是正则）
    seen = replace(contract_case, cohort="seen_regression")
    assert validate_cohort_isolation([seen], {"有没有天气预警"}) == []


def test_fingerprint_normalises_punctuation_and_width(contract_case):
    from support.intent_adversarial_contract import (
        canonical_text, utterance_fingerprint,
    )

    assert canonical_text("有没有天气预警？") == canonical_text("有没有天气预警")
    assert canonical_text("Ａ Ｂ") == canonical_text("a,b")
    assert utterance_fingerprint(contract_case.turns[0]) == "有没有天气预警"


def test_scale_unit_is_the_distinct_input_not_the_case_count(contract_case):
    """反向构造 P1-7：同一句话复制 4 遍。

    stable 113 条里只有 104 个唯一输入；用条数报规模等于允许「复制近义句冲条数」。
    """
    from support.intent_adversarial_contract import (
        SuiteConfig, distinct_input_units, duplicate_input_groups,
        validate_suite_counts,
    )

    clones = [replace(contract_case, id=f"clone.{i}", family_id=f"fam.{i}")
              for i in range(4)]
    assert distinct_input_units(clones) == 1
    assert list(duplicate_input_groups(clones).values()) == [
        ["clone.0", "clone.1", "clone.2", "clone.3"]]
    suite = SuiteConfig(statuses=("reviewed",), live_statuses=("reviewed",),
                        min_cases=2, max_cases=10, attack_minimums={},
                        normal_repeats=1, failure_repeats=3, high_risk_repeats=3)
    errors = validate_suite_counts(clones, suite)
    assert any("distinct-input count 1" in e and "cases=4" in e for e in errors)


# ── P0-2 反向构造：L2 才观测得到的 engine 期望 ──────────────────────────


def test_engine_expectation_must_declare_l2(contract_case):
    engine_case = replace(contract_case, turns=(replace(
        contract_case.turns[0],
        expected=replace(contract_case.turns[0].expected,
                         engine=_engine(forbidden_agent_calls=("trunk.open",)))),))
    errors = validate_cases([engine_case], {"info.alerts", "trunk.open"})
    assert any("requires layers to include l2" in e for e in errors)


def test_engine_expectation_rejects_unknown_or_contradictory_agent_calls(
        contract_case):
    l2_case = replace(contract_case,
                      tags={**contract_case.tags, "layers": ["l2"]})
    bad = replace(l2_case, turns=(replace(
        l2_case.turns[0],
        expected=replace(l2_case.turns[0].expected,
                         engine=_engine(required_agent_calls=("trunk.open",),
                                        forbidden_agent_calls=("trunk.open",
                                                               "nope.missing")))),))
    errors = validate_cases([bad], {"info.alerts", "trunk.open"})
    assert any("unknown engine agent call nope.missing" in e for e in errors)
    assert any("required/forbidden overlap" in e for e in errors)


def test_empty_engine_block_is_a_contract_error(contract_case):
    l2_case = replace(contract_case,
                      tags={**contract_case.tags, "layers": ["l2"]})
    empty = replace(l2_case, turns=(replace(
        l2_case.turns[0],
        expected=replace(l2_case.turns[0].expected, engine=_engine())),))
    errors = validate_cases([empty], {"info.alerts"})
    assert any("expected.engine declared but empty" in e for e in errors)


def _engine(**changes):
    from support.intent_adversarial_contract import EngineExpectation
    return EngineExpectation(declared=True, **changes)


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


def test_invariant_slot_policy_rejects_unknown_semantics(contract_case):
    relation = RelationSpec(
        contract_case.id, "invariant", {"slot_policy": "guess_from_utterance"})
    errors = validate_cases(
        [contract_case, replace(contract_case, id="variant", relation=relation)],
        {"info.alerts"})
    assert any("slot_policy must be 'subset'" in error for error in errors)


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


def test_negation_policy_distinguishes_unstarted_cancel_from_active_stop():
    """取消尚未执行的并列动作不能被改写成 pause/stop 等反向动作。"""
    policy = yaml.safe_load((_ROOT / "skills" / "policies" /
                             "negation-and-deferral.yaml").read_text(
                                 encoding="utf-8"))
    golden = {row["text"]: row for row in policy["golden"]}

    cancelled = golden["查下天气，音乐先别放"]
    assert cancelled["expect_intents"] == ["info.weather"]
    assert set(cancelled["expect_not"]) >= {"media.play", "media.pause"}

    active_stop = golden["音乐已经在放了，帮我暂停一下"]
    assert active_stop["expect_intents"] == ["media.pause"]
    assert "media.play" in active_stop["expect_not"]


def test_depleted_vehicle_exemplar_routes_to_find_without_copying_badcase():
    """电量见底是补能求助；不用把对抗原句写进范例来得到这个边界。"""
    data = yaml.safe_load((_ROOT / "skills" / "exemplars" /
                           "charging.yaml").read_text(encoding="utf-8"))
    exemplars = {row["text"]: row for row in data["exemplars"]}

    assert "车没电了" not in exemplars
    depleted = exemplars["续航已经见底，帮我找个补电的地方"]
    assert depleted["plan"] == [{
        "agent": "charging-planner",
        "intent": "charging.find",
    }]


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
    # 2026-08-03 泓舟补裁两条（nearby-navigation.find-vs-go /
    # manual-vision.described-light-vs-unknown-light），18 → 20。
    # 条数断言是**故意**的：台账每加一条都要求双向各 2 条对照用例，这里红一次
    # 就是提醒「裁定加了，兑现物加了吗」。
    assert ledger["nearby-navigation.find-vs-go"] == ("nearby", "navigation")
    assert ledger["manual-vision.described-light-vs-unknown-light"] == ("manual", "vision")
    # 2026-08-04 泓舟再补一条（charging-nearby.charger-vs-gas-station），20 → 21。
    # **这条断言拦了我一次，拦得对**：它逼我先证明兑现物在场再改数字。
    # 已证：`validate_boundary_coverage` 对该 ruling 零错误，左右各 2 条 `reviewed`
    # 对照（`bd.cn-charger-gas.{left.charger,left.fastcharge,right.gas,right.store}`），
    # 两侧同句式只换对象——句式相同才证明分开它们的是对象的能力耦合，不是措辞。
    assert ledger["charging-nearby.charger-vs-gas-station"] == ("charging", "nearby")
    assert len(ledger) == 21


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


def test_coverage_counts_distinct_inputs_not_case_rows():
    """反向构造：两条 case 用**完全相同**的一句话给同一个 intent 记正例。

    规模总数早就按唯一输入算了，coverage 账却还在按 case 次数加一——实测
    `nq.trunk.command` 与 `os.open.trunk` 都是「打开后备箱」，把 `trunk.open` 的
    正例从唯一 1 条记成 2 条，**恰好涂绿 `positive>=2` 这条最低要求**（评审 P1-E）。
    """
    from support.intent_adversarial_contract import coverage_matrix, validate_coverage

    def _positive(case_id, utterance):
        return AdversarialCase(
            id=case_id, title=case_id, family_id=case_id,
            cohort="unseen_transfer", risk="low", status="stable",
            tags={"attacks": ["A1"], "layers": ["l1"]},
            provenance={"kind": "authored"},
            turns=(CaseTurn(utterance=utterance, context={},
                            expected=TurnExpectation(plan=PlanExpectation(
                                assert_plan=True,
                                required_groups=(IntentGroup(("trunk.open",)),)))),))

    same = [_positive("a", "打开后备箱"), _positive("b", "打开后备箱")]
    assert coverage_matrix(same, {"trunk.open"})["trunk.open"]["positive"] == 1
    assert any("positive has 1" in row
               for row in validate_coverage(same, {"trunk.open"}, {}))

    # 反向：真的是两句不同的话就该记 2，别把去重做成一刀切
    different = [_positive("a", "打开后备箱"), _positive("b", "把尾箱打开")]
    assert coverage_matrix(different, {"trunk.open"})["trunk.open"]["positive"] == 2
    assert validate_coverage(different, {"trunk.open"},
                             {"trunk.open": {"hard_negative", "relation"}}) == []


# ── 晋级取证的统计功效（2026-08-04，findings §10）─────────────────────────

def _stable_case(case_id: str, **provenance):
    base = {"kind": "authored", "reviewed_by": "human", "reviewed_at": "2026-08-04",
            "stabilized_provider": "minimax:MiniMax-M3",
            "evidence_report": "docs/reviews/eval/x.json"}
    base.update(provenance)
    return AdversarialCase(
        id=case_id, title=case_id, family_id=case_id,
        cohort="unseen_transfer", risk="low", status="stable",
        tags={"attacks": ["A1"], "layers": ["l1"]},
        provenance=base,
        turns=(CaseTurn(utterance="打开后备箱", context={},
                        expected=TurnExpectation(plan=PlanExpectation(
                            assert_plan=True,
                            required_groups=(IntentGroup(("trunk.open",)),)))),))


def _errors_for(case) -> list[str]:
    return [row for row in validate_cases([case], {"trunk.open"})
            if case.id in row]


def _independent_process_provenance(**overrides):
    provenance = {
        "stabilized_at": "2026-08-04",
        "stabilized_processes": 2,
        "stabilized_samples_per_process": 3,
        "stabilized_process_runs": ["promotion-a", "promotion-b"],
        "stabilized_samples": 6,
    }
    provenance.update(overrides)
    return provenance


@pytest.mark.parametrize(
    "missing_key",
    (
        "stabilized_processes",
        "stabilized_samples_per_process",
        "stabilized_process_runs",
        "stabilized_samples",
    ),
)
def test_new_promotions_require_complete_independent_process_provenance(
        missing_key: str):
    provenance = _independent_process_provenance()
    provenance.pop(missing_key)

    errors = _errors_for(_stable_case("x.missing", **provenance))

    assert any(missing_key in row for row in errors)


def test_new_promotions_must_use_at_least_two_independent_processes():
    case = _stable_case(
        "x.one-process",
        **_independent_process_provenance(stabilized_processes=1),
    )

    assert any("stabilized_processes=1" in row for row in _errors_for(case))


def test_new_promotions_must_sample_each_process_at_least_three_times():
    case = _stable_case(
        "x.two-samples",
        **_independent_process_provenance(stabilized_samples_per_process=2),
    )

    assert any("stabilized_samples_per_process=2" in row
               for row in _errors_for(case))


@pytest.mark.parametrize(
    "runs",
    ([], (), "promotion-a", ["promotion-a", "promotion-a"]),
)
def test_new_promotions_require_a_non_empty_unique_process_run_list(runs):
    case = _stable_case(
        "x.bad-runs",
        **_independent_process_provenance(stabilized_process_runs=runs),
    )

    assert any("stabilized_process_runs" in row for row in _errors_for(case))


@pytest.mark.parametrize(
    "runs",
    (
        ["promotion-a"],
        ["promotion-a", "promotion-b", "promotion-c"],
    ),
)
def test_process_run_count_must_equal_declared_process_count(runs):
    case = _stable_case(
        "x.run-count",
        **_independent_process_provenance(stabilized_process_runs=runs),
    )

    assert any(
        f"stabilized_process_runs has {len(runs)} entries; expected 2" in row
        for row in _errors_for(case)
    )


def test_new_promotions_require_total_samples_to_cover_every_process():
    case = _stable_case(
        "x.total-too-small",
        **_independent_process_provenance(stabilized_samples=5),
    )

    assert any("stabilized_samples=5 < 2 * 3" in row
               for row in _errors_for(case))


def test_complete_independent_process_provenance_is_accepted():
    """「独立跑两趟」说的是**进程数不是样本数**——`normal_repeats: 1` 下那只有 2 个样本。

    实测（findings §10）：3 趟 × repeat 3 共 9 个样本下，132 条 stable 里 18 条不稳定，
    而它们全都通过了旧的两趟取证。
    """
    case = _stable_case("x.ok", **_independent_process_provenance())

    assert not any("stabilized_" in row for row in _errors_for(case))


def test_formal_charge_then_navigate_records_two_process_runs():
    root = Path(__file__).parent / "eval_corpus" / "intent_adversarial" / "cases"
    case = next(c for c in load_cases(root)
                if c.id == "cp.dep.charge-then-navigate")

    assert case.provenance["stabilized_processes"] == 2
    assert case.provenance["stabilized_samples_per_process"] == 3
    assert case.provenance["stabilized_process_runs"] == [
        "promotion-charge-nav-a",
        "promotion-charge-nav-b",
    ]
    assert case.provenance["stabilized_samples"] == 6


def test_legacy_promotions_are_grandfathered_by_date_not_waived_silently():
    """存量 132 条在旧判据下晋级——一次性判它们违约既不真实也不可执行。

    按日期分段是**有意的**：它们的账另记在 findings §10（按机制逐族处理），
    不是被悄悄豁免。
    """
    legacy = _stable_case("x.legacy", stabilized_at="2026-08-02")
    assert not any("stabilized_samples" in row for row in _errors_for(legacy))


@pytest.mark.parametrize("stabilized_at", (None, ""))
def test_missing_or_empty_stabilized_at_does_not_trigger_new_provenance_fields(
        stabilized_at):
    case = _stable_case("x.undated", stabilized_at=stabilized_at)

    errors = _errors_for(case)

    assert not any(
        field in row
        for field in (
            "stabilized_processes",
            "stabilized_samples_per_process",
            "stabilized_process_runs",
            "stabilized_samples",
        )
        for row in errors
    )


@pytest.mark.parametrize(
    "stabilized_at",
    (
        "08/05/2026",
        "2026/08/05",
        "not-a-date",
        True,
        20260805,
        "2026-02-30",
    ),
)
def test_explicit_stabilized_at_must_be_a_real_strict_iso_date(stabilized_at):
    case = _stable_case("x.bad-date", stabilized_at=stabilized_at)

    assert any(
        "provenance.stabilized_at must be a date or strict YYYY-MM-DD string"
        in row
        for row in _errors_for(case)
    )


def test_valid_old_stabilized_at_string_remains_grandfathered():
    case = _stable_case("x.old-date", stabilized_at="2026-08-03")

    assert _errors_for(case) == []


def test_valid_threshold_stabilized_at_string_enforces_and_accepts_evidence():
    case = _stable_case("x.threshold", **_independent_process_provenance())

    assert _errors_for(case) == []


def test_pyyaml_date_object_is_compared_as_a_date():
    case = _stable_case(
        "x.yaml-date",
        **_independent_process_provenance(stabilized_at=date(2026, 8, 4)),
    )

    assert _errors_for(case) == []


def test_stabilized_samples_must_be_an_integer_not_a_bool_or_string():
    """`True` 是 int 的子类——不挡住它，一个手滑的 `yes` 就变成「样本数 1」。"""
    for bad in (True, "6", 6.0):
        case = _stable_case(
            "x.bad",
            **_independent_process_provenance(stabilized_samples=bad),
        )
        assert any("integer provenance.stabilized_samples" in row
                   for row in _errors_for(case)), bad


def test_process_run_ids_are_deduplicated_after_stripping():
    case = _stable_case(
        "x.normalized-duplicate",
        **_independent_process_provenance(
            stabilized_process_runs=["promotion-a", " promotion-a"],
        ),
    )

    errors = _errors_for(case)
    assert any("must not have surrounding whitespace" in row for row in errors)
    assert any("must be unique after stripping" in row for row in errors)


def test_process_run_ids_reject_trailing_whitespace():
    case = _stable_case(
        "x.trailing-space",
        **_independent_process_provenance(
            stabilized_process_runs=["promotion-a", "promotion-b "],
        ),
    )

    assert any("must not have surrounding whitespace" in row
               for row in _errors_for(case))
