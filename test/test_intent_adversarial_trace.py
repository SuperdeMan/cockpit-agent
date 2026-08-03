"""Hint 前后计划、资产指纹与首偏离点的回归测试（不连 LLM）。"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.cloud.models import Plan, Step  # noqa: E402
from orchestrator.cloud.route_hints import RouteHintEngine  # noqa: E402
from support.intent_adversarial_trace import (  # noqa: E402
    DivergenceEvidence, TraceSink, TracingRouteHints, asset_digest,
    attach_validation_trace, first_divergence,
)


def _validate(rows, _agent_map):
    return [Step(id=row["id"], agent_id=row["agent_id"], endpoint="a:1",
                 intent=row["intent"], slots=row["slots"],
                 depends_on=row["depends_on"], slot_refs=row["slot_refs"])
            for row in rows]


def test_tracing_route_hints_keeps_before_and_after_plan():
    hint = SimpleNamespace(intent="charging.find", pattern="充电", guard="手机",
                           policy="replace", priority=90, slots={})
    agent = SimpleNamespace(manifest=SimpleNamespace(route_hints=[hint]))
    plan = Plan(steps=[Step(id="s1", agent_id="chitchat", endpoint="c:1",
                           intent="chitchat.talk")])
    sink = TraceSink()
    wrapped = TracingRouteHints(RouteHintEngine(_validate), sink)

    assert wrapped.apply(plan, "给车找个充电站", {"charging": agent}) is True

    trace = sink.hints[-1]
    assert trace.matches[0].intent == "charging.find"
    assert trace.before.intents == ("chitchat.talk",)
    assert trace.after.intents == ("charging.find",)


def test_tracing_route_hints_records_guarded_miss_without_touching_plan():
    hint = SimpleNamespace(intent="charging.find", pattern="充电", guard="手机",
                           policy="replace", priority=90, slots={})
    agent = SimpleNamespace(manifest=SimpleNamespace(route_hints=[hint]))
    plan = Plan(steps=[Step(id="s1", agent_id="chitchat", endpoint="c:1",
                           intent="chitchat.talk")])
    sink = TraceSink()
    wrapped = TracingRouteHints(RouteHintEngine(_validate), sink)

    assert wrapped.apply(plan, "手机快没电了去哪充电", {"charging": agent}) is False

    trace = sink.hints[-1]
    assert trace.matches == ()
    assert trace.before.intents == trace.after.intents == ("chitchat.talk",)


def test_asset_digest_is_order_independent_and_content_sensitive(tmp_path: Path):
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("a: 1\n", encoding="utf-8")
    b.write_text("b: 2\n", encoding="utf-8")
    assert asset_digest(tmp_path, [b, a]) == asset_digest(tmp_path, [a, b])
    old = asset_digest(tmp_path, [a, b])
    b.write_text("b: 3\n", encoding="utf-8")
    assert asset_digest(tmp_path, [a, b]) != old


def test_validation_trace_keeps_raw_and_accepted_intents():
    class Builder:
        def _parse_and_validate_data(self, data, _agent_map, _text):
            return Plan(steps=[Step(id="s1", agent_id="info", endpoint="i:1",
                                    intent="info.weather")])
    builder, sink = Builder(), TraceSink()
    attach_validation_trace(builder, sink)
    builder._parse_and_validate_data(
        {"steps": [{"intent": "info.weather"}]}, {}, "查天气")
    assert sink.validations[-1].raw_intents == ("info.weather",)
    assert sink.validations[-1].raw_candidate.intents == ("info.weather",)
    assert sink.validations[-1].accepted.intents == ("info.weather",)


def test_validation_trace_marks_rejected_capability_hallucination():
    class Builder:
        def _parse_and_validate_data(self, _data, _agent_map, _text):
            return None
    builder, sink = Builder(), TraceSink()
    attach_validation_trace(builder, sink)
    builder._parse_and_validate_data(
        {"steps": [{"intent": "does.not_exist"}]}, {}, "随便说点什么")
    trace = sink.validations[-1]
    assert trace.result == "rejected"
    assert trace.raw_intents == ("does.not_exist",)
    assert trace.accepted.intents == ()


def test_first_divergence_respects_execution_order():
    assert first_divergence(DivergenceEvidence(
        full_entry_pass=False, engine_direct_pass=True)) == "EDGE_DIVERGENCE"
    assert first_divergence(DivergenceEvidence(
        full_entry_pass=False, engine_direct_pass=False,
        planner_post_hint_pass=True)) == "STATE_RESTORE_DIVERGENCE"
    assert first_divergence(DivergenceEvidence(
        full_entry_pass=False, engine_direct_pass=False,
        planner_post_hint_pass=False, empty_history_pass=True)) == "CONTEXT_DIVERGENCE"
    assert first_divergence(DivergenceEvidence(
        full_entry_pass=False, engine_direct_pass=False,
        planner_post_hint_pass=False, empty_history_pass=False,
        retrieval_ablation_pass=True)) == "RETRIEVAL_SUSPECT"
    assert first_divergence(DivergenceEvidence(
        full_entry_pass=False, engine_direct_pass=False,
        planner_post_hint_pass=False, empty_history_pass=False,
        retrieval_ablation_pass=False, pre_hint_pass=False,
        raw_planner_pass=True)) == "VALIDATION_DIVERGENCE"
    assert first_divergence(DivergenceEvidence(
        full_entry_pass=False, engine_direct_pass=False,
        planner_post_hint_pass=False, empty_history_pass=False,
        retrieval_ablation_pass=False, raw_planner_pass=False,
        pre_hint_pass=True)) == "HINT_DIVERGENCE"
    assert first_divergence(DivergenceEvidence(full_entry_pass=True)) == "NONE"


def test_unobserved_boundaries_never_get_pinned_on_the_planner():
    """反向构造 P1-4：一个对照都没跑。

    原来七个字段都是默认 `False`，于是「没观测」与「观测了都没翻正」得到同一个结论
    `PLANNER_DIVERGENCE`——首偏离点因此变成失败的同义词，L0 的 5 条确定性失败也被
    贴上了这个标签，而 L0 根本没有 Planner。
    """
    assert first_divergence(DivergenceEvidence()) == "UNCLASSIFIED"
    # 更早的边界没观测时，后面的正向证据不能称「第一个」
    assert first_divergence(DivergenceEvidence(
        pre_hint_pass=True)) == "UNCLASSIFIED"
    # 前面每一层都实测过且都没翻正，才轮得到 Planner
    assert first_divergence(DivergenceEvidence(
        engine_direct_pass=False, planner_post_hint_pass=False,
        empty_history_pass=False, retrieval_ablation_pass=False,
        pre_hint_pass=False, raw_planner_pass=False)) == "PLANNER_DIVERGENCE"


def test_candidates_keep_the_free_evidence_without_claiming_it_is_first():
    from support.intent_adversarial_trace import divergence_candidates, evidence_dict

    evidence = DivergenceEvidence(pre_hint_pass=True, raw_planner_pass=False)
    assert divergence_candidates(evidence) == ("HINT_DIVERGENCE",)
    assert first_divergence(evidence) == "UNCLASSIFIED"
    # null=没观测、false=观测了没翻正，诊断时这两者不能混
    assert evidence_dict(evidence)["empty_history_pass"] is None
    assert evidence_dict(evidence)["raw_planner_pass"] is False


def test_l0_divergence_comes_from_the_failing_assertion_not_from_ablations():
    from support.intent_adversarial_trace import deterministic_divergence

    assert deterministic_divergence([]) == "NONE"
    assert deterministic_divergence(
        ["no_side_effect_before_confirm"]) == "EDGE_SIDE_EFFECT"
    assert deterministic_divergence(["ingress_allowed"]) == "EDGE_DIVERGENCE"
    assert deterministic_divergence(
        ["retrieval.required:weather-outing"]) == "RETRIEVAL_DIVERGENCE"
    assert deterministic_divergence(["something_else"]) == "UNCLASSIFIED"


def test_probe_builder_restores_the_builder_it_wrapped():
    """探针必须可还原：每条 case 包一层不还原，第二条就是双重 trace。"""
    from support.intent_adversarial_trace import TraceSink, probe_builder

    class _Hints:
        def apply(self, plan, text, agent_map):
            return False

    class _Builder:
        def __init__(self):
            self._route_hints = _Hints()

        def _parse_and_validate_data(self, data, agent_map, text):
            return None

    builder = _Builder()
    original_hints = builder._route_hints
    with probe_builder(builder, TraceSink()):
        assert builder._route_hints is not original_hints
        assert "_parse_and_validate_data" in builder.__dict__
    assert builder._route_hints is original_hints
    assert "_parse_and_validate_data" not in builder.__dict__


def test_asset_fingerprint_reports_missing_globs_instead_of_claiming_complete(tmp_path):
    from support.intent_adversarial_trace import asset_fingerprint

    empty = asset_fingerprint(tmp_path)
    assert empty["complete"] is False
    assert empty["digest"] == ""
    assert "agents/*/manifest.yaml" in empty["missing_assets"]

    real = asset_fingerprint(Path(__file__).resolve().parent.parent)
    assert real["complete"] is True
    assert real["missing_assets"] == []
    assert real["file_count"] > 10
    assert len(real["digest"]) == 64
