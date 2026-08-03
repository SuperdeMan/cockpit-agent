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


# ── 兜底计划与检索降级（2026-08-03 第二批尺子硬化） ─────────────────────────


def test_probe_builder_records_the_fallback_and_restores_it():
    """兜底计划必须留痕：它与 gold 逐字相同的时候，是这条痕迹撑住了「这条绿不算数」。

    判据用「`_fallback` 被不被调到」，不用「计划长得像兜底」——`chitchat.talk` 本来
    就是一部分用例的正确答案，按形状判会把真通过一起打掉。
    """
    import asyncio

    from support.intent_adversarial_trace import TraceSink, probe_builder

    class _Builder:
        def __init__(self):
            self.calls = 0

        async def _fallback(self, text, agents=None):
            self.calls += 1
            return f"fallback:{text}"

    builder = _Builder()
    inner = builder._fallback
    sink = TraceSink()
    with probe_builder(builder, sink):
        assert asyncio.run(builder._fallback("空调先别关")) == "fallback:空调先别关"
    assert sink.fallbacks == ["空调先别关"]
    assert builder.calls == 1                    # 仍然真的委派给了被包的那一个
    assert "_fallback" not in builder.__dict__   # 逐字还原，下一条 case 不会双重记账
    assert builder._fallback.__func__ is inner.__func__


def test_probe_retrieval_counts_only_the_calls_that_wanted_vectors():
    """空输入返回 None 是契约不是降级；冷却期内被跳过的算降级——那一轮确实只有词法。"""
    import asyncio

    from orchestrator.cloud import embedding
    from support.intent_adversarial_trace import probe_retrieval

    original = embedding.embed_texts
    calls: list[list[str]] = []

    async def fake(texts, timeout_s=1.0):
        calls.append(list(texts))
        if not texts:
            return None
        return (None if texts[0] == "boom" else ([(1.0,)] * len(texts), "m"))

    embedding.embed_texts = fake
    try:
        with probe_retrieval() as probe:
            asyncio.run(embedding.embed_texts([]))          # 不计
            asyncio.run(embedding.embed_texts(["ok"]))      # 计，不降级
            asyncio.run(embedding.embed_texts(["boom"]))    # 计，降级
        assert probe.calls == 2 and probe.degraded == 1
        assert probe.as_dict() == {"calls": 2, "degraded": 1}
        # 探针必须可还原，否则下一个用例还在数上一个用例的账
        assert embedding.embed_texts is fake
    finally:
        embedding.embed_texts = original
    assert calls == [[], ["ok"], ["boom"]]


def test_l1_can_reach_a_divergence_label_at_all():
    """反向构造：L1 跑不了 L2 专属的两条 arm，旧实现于是**结构上**只能返回
    `UNCLASSIFIED`——context/retrieval/hint/validation/planner 五个标签一个都出不来。

    「没观测」与「不适用」都不是「已排除」，但处理方式相反：前者阻断结论，
    后者必须跳过。把它们压成同一个 `None` 正是上一批反复修的那类默认值错误。
    """
    from support.intent_adversarial_trace import applicable_boundaries

    # L1：Edge / 状态恢复两条边界不存在，其余全实测过 → 轮得到 Planner
    l1 = DivergenceEvidence(full_entry_pass=False, empty_history_pass=False,
                            retrieval_ablation_pass=False, pre_hint_pass=False,
                            raw_planner_pass=False)
    assert first_divergence(l1, "l1") == "PLANNER_DIVERGENCE"
    assert first_divergence(l1) == "UNCLASSIFIED"      # 不给 layer 时保持保守
    assert first_divergence(l1, "l2") == "UNCLASSIFIED"

    # L1 上更早的边界翻正了，就该报那一个，而不是被 L2 字段吞掉
    hinted = DivergenceEvidence(full_entry_pass=False, empty_history_pass=False,
                                retrieval_ablation_pass=False, pre_hint_pass=True)
    assert first_divergence(hinted, "l1") == "HINT_DIVERGENCE"

    # L1 上**真的没观测**（没跑消融）仍必须阻断，不能因为跳过了两条就放行
    thin = DivergenceEvidence(full_entry_pass=False, pre_hint_pass=True)
    assert first_divergence(thin, "l1") == "UNCLASSIFIED"

    names = [name for name, _ in applicable_boundaries("l1")]
    assert "engine_direct_pass" not in names and "planner_post_hint_pass" not in names
    assert len(applicable_boundaries("l2")) == 6


def test_the_probe_never_kills_the_run_on_malformed_model_output():
    """反向构造：模型把 `slots` 写成字符串列表。

    实测形态：`dict(["mode"])` 抛 `ValueError: dictionary update sequence element #0
    has length 4`，**整趟 L1 全量在跑到一半时被这个观察者杀死**——而生产侧的
    `_parse_and_validate_data` 已经安全解析完了。观察者不能比被观察的东西更脆弱。

    降级方向必须是**更保守**：这一轮记成「没有 raw 通道」，不进幻觉率分母，
    并把异常留进 `trace_errors`——不许静默。
    """
    from support.intent_adversarial_trace import (
        TraceSink, attach_validation_trace, snapshot_raw_candidate,
    )

    for bad in ({"steps": [{"intent": "hvac.set", "slots": ["mode"]}]},
                {"steps": [{"intent": "a", "slots": "auto", "depends_on": "s1",
                            "slot_refs": 5}]},
                {"steps": "oops"}, {"steps": 7}, {"steps": None}):
        snap = snapshot_raw_candidate(bad)          # 不抛
        for step in snap.steps:
            assert step.slots == {} and step.depends_on == () and step.slot_refs == {}

    class _Builder:
        def _parse_and_validate_data(self, data, agent_map, text):
            return SimpleNamespace(steps=[], complexity="", goal="")

    sink = TraceSink()
    builder = _Builder()
    attach_validation_trace(builder, sink)

    class _Exploding(dict):
        def get(self, *_a, **_k):
            raise TypeError("模型这次的输出形状是穷举不完的")

    plan = builder._parse_and_validate_data(_Exploding(steps=[]), {}, "空调先别关")
    assert plan is not None, "生产的返回值必须原样透出——观察不该改变被观察的行为"
    assert sink.validations == [], "取不到候选就记成没观测，不许伪造一份"
    assert sink.trace_errors and "TypeError" in sink.trace_errors[0]
