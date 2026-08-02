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
