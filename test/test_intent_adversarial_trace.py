"""Hint 前后计划、资产指纹与首偏离点的回归测试（不连真实 LLM）。"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.cloud.models import Plan, Step  # noqa: E402
from orchestrator.cloud.context import WorkingSet  # noqa: E402
from orchestrator.cloud.models import PlanContext  # noqa: E402
from orchestrator.cloud.planning import (  # noqa: E402
    PlanBuilder, _assemble_capability_catalog, _SUBMIT_PLAN_NAME,
)
from orchestrator.cloud.route_hints import RouteHintEngine  # noqa: E402
from support.intent_adversarial_trace import (  # noqa: E402
    DivergenceEvidence, TraceSink, TracingRouteHints, asset_digest,
    attach_validation_trace, first_divergence,
    probe_builder,
)


def _catalog():
    capability = SimpleNamespace(intent="info.weather")
    agent = SimpleNamespace(
        manifest=SimpleNamespace(capabilities=[capability]))
    return SimpleNamespace(
        ref_to_pair={"cap_0001": ("info", "info.weather")},
        agent_map={"info": agent},
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
        def _parse_and_validate_data(self, data, _catalog, _text):
            return Plan(steps=[Step(id="s1", agent_id="info", endpoint="i:1",
                                    intent="info.weather")])
    builder, sink = Builder(), TraceSink()
    attach_validation_trace(builder, sink)
    builder._parse_and_validate_data(
        {"steps": [{"capability_ref": "cap_0001"}]}, _catalog(), "查天气")
    assert sink.validations[-1].raw_intents == ("info.weather",)
    assert sink.validations[-1].raw_candidate.intents == ("info.weather",)
    assert sink.validations[-1].accepted.intents == ("info.weather",)
    assert sink.validations[-1].request_capability_catalog == (
        ("cap_0001", "info", "info.weather"),)


def test_validation_trace_marks_rejected_capability_hallucination():
    class Builder:
        def _parse_and_validate_data(self, _data, _catalog, _text):
            return None
    builder, sink = Builder(), TraceSink()
    attach_validation_trace(builder, sink)
    builder._parse_and_validate_data(
        {"steps": [{"capability_ref": "cap_9999"}]}, _catalog(), "随便说点什么")
    trace = sink.validations[-1]
    assert trace.result == "rejected"
    assert trace.raw_intents == ("__invalid_capability_reference__",)
    assert trace.accepted.intents == ()
    assert [row.value for row in trace.raw_capability_refs] == ["cap_9999"]
    assert [row.status for row in trace.raw_capability_refs] == ["unknown"]
    assert trace.request_capability_catalog == (
        ("cap_0001", "info", "info.weather"),)
    assert trace.stage == "build"
    assert trace.attempt == 0
    assert trace.wire_mode == "direct"


def test_validation_trace_preserves_malformed_string_capability_reference():
    class Builder:
        def _parse_and_validate_data(self, _data, _catalog, _text):
            return None

    builder, sink = Builder(), TraceSink()
    attach_validation_trace(builder, sink)
    builder._parse_and_validate_data(
        {"steps": [{"capability_ref": "info.weather"}]},
        _catalog(),
        "随便说点什么",
    )

    trace = sink.validations[-1]
    assert trace.raw_intents == ("__invalid_capability_reference__",)
    assert [(row.value, row.status) for row in trace.raw_capability_refs] == [
        ("info.weather", "malformed_reference"),
    ]


def test_validation_trace_preserves_malformed_steps_container_type():
    class Builder:
        def _parse_and_validate_data(self, _data, _catalog, _text):
            return None

    builder, sink = Builder(), TraceSink()
    attach_validation_trace(builder, sink)
    builder._parse_and_validate_data(
        {"steps": {"capability_ref": "cap_0001"}},
        _catalog(),
        "随便说点什么",
    )

    trace = sink.validations[-1]
    assert trace.raw_intents == ("__invalid_capability_reference__",)
    assert [(row.value, row.status) for row in trace.raw_capability_refs] == [
        ("<malformed-steps:type=dict;shape=step>", "malformed_steps"),
    ]


def test_validation_trace_classifies_misnested_clarify_without_recording_values():
    class Builder:
        def _parse_and_validate_data(self, _data, _catalog, _text):
            return None

    builder, sink = Builder(), TraceSink()
    attach_validation_trace(builder, sink)
    builder._parse_and_validate_data(
        {"steps": {"question": "sensitive", "options": ["sensitive"]}},
        _catalog(),
        "随便说点什么",
    )

    assert [row.value for row in sink.validations[-1].raw_capability_refs] == [
        "<malformed-steps:type=dict;shape=clarify>",
    ]


def test_validation_trace_identifies_both_structured_semantic_attempts(monkeypatch):
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    monkeypatch.setenv("SKILLS_MODE", "off")
    monkeypatch.setenv("EXEMPLARS_MODE", "off")
    capability = SimpleNamespace(
        intent="info.weather", slots=[], description="天气", examples=[],
        heavy=False, require_confirm=False)
    manifest = SimpleNamespace(
        agent_id="info", capabilities=[capability], latency_budget_ms=5000,
        kind="agent", deployment="cloud", requires_permissions=[],
        trust_level="first_party", route_hints=[], context_scopes=[])
    agent = SimpleNamespace(manifest=manifest, endpoint="info:1")
    admitted_ref = _assemble_capability_catalog([agent]).pair_to_ref[
        ("info", "info.weather")]
    valid = {
        "addressed": True, "complexity": "simple", "goal": "查天气",
        "steps": [{"id": "s1", "capability_ref": admitted_ref, "slots": {},
                   "depends_on": [], "slot_refs": {}}],
    }
    invalid = {**valid, "steps": [
        {**valid["steps"][0], "capability_ref": "cap_9999"},
    ]}

    async def llm(_messages):
        raise AssertionError("semantic retry must stay on submit_plan")

    tool_attempt = 0
    async def llm_tools(_messages, _tools):
        nonlocal tool_attempt
        tool_attempt += 1
        arguments = invalid if tool_attempt == 1 else valid
        return "", [{"id": f"c{tool_attempt}", "name": _SUBMIT_PLAN_NAME,
                      "arguments": arguments}]

    async def no_resolve(_query, top_k=1):
        return []

    builder = PlanBuilder(
        llm_fn=llm, registry_fn=no_resolve, llm_tool_fn=llm_tools)
    sink = TraceSink()
    with probe_builder(builder, sink):
        plan = asyncio.run(builder.build(
            "查天气", WorkingSet(catalog=[agent]), PlanContext(session_id="t")))

    assert plan.plan_mode == "toolcall"
    assert [(row.stage, row.attempt, row.wire_mode) for row in sink.validations] == [
        ("build", 0, "toolcall"),
        ("build", 1, "toolcall"),
    ]
    assert [(ref.value, ref.status)
            for ref in sink.validations[0].raw_capability_refs] == [
                ("cap_9999", "unknown"),
            ]


def test_validation_trace_identifies_json_fallback_after_protocol_failure(monkeypatch):
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    monkeypatch.setenv("SKILLS_MODE", "off")
    monkeypatch.setenv("EXEMPLARS_MODE", "off")
    capability = SimpleNamespace(
        intent="info.weather", slots=[], description="天气", examples=[],
        heavy=False, require_confirm=False)
    manifest = SimpleNamespace(
        agent_id="info", capabilities=[capability], latency_budget_ms=5000,
        kind="agent", deployment="cloud", requires_permissions=[],
        trust_level="first_party", route_hints=[], context_scopes=[])
    agent = SimpleNamespace(manifest=manifest, endpoint="info:1")
    admitted_ref = _assemble_capability_catalog([agent]).pair_to_ref[
        ("info", "info.weather")]
    valid = {
        "addressed": True,
        "steps": [{"id": "s1", "capability_ref": admitted_ref, "slots": {},
                   "depends_on": [], "slot_refs": {}}],
    }

    async def llm(_messages):
        return json.dumps(valid, ensure_ascii=False)

    async def llm_tools(_messages, _tools):
        return "", [{"id": "c1", "name": "unsupported_tool",
                      "arguments": valid}]

    async def no_resolve(_query, top_k=1):
        return []

    builder = PlanBuilder(
        llm_fn=llm, registry_fn=no_resolve, llm_tool_fn=llm_tools)
    sink = TraceSink()
    with probe_builder(builder, sink):
        plan = asyncio.run(builder.build(
            "查天气", WorkingSet(catalog=[agent]), PlanContext(session_id="t")))

    assert plan.plan_mode == "toolcall_fallback"
    assert [(row.stage, row.attempt, row.wire_mode) for row in sink.validations] == [
        ("build", 1, "toolcall_fallback"),
    ]


def test_validation_trace_preserves_non_object_wire_identity_on_real_build(monkeypatch):
    monkeypatch.setenv("PLANNER_TOOLCALL", "off")
    monkeypatch.setenv("SKILLS_MODE", "off")
    monkeypatch.setenv("EXEMPLARS_MODE", "off")
    capability = SimpleNamespace(
        intent="info.weather", slots=[], description="天气", examples=[],
        heavy=False, require_confirm=False)
    manifest = SimpleNamespace(
        agent_id="info", capabilities=[capability], latency_budget_ms=5000,
        kind="agent", deployment="cloud", requires_permissions=[],
        trust_level="first_party", route_hints=[], context_scopes=[])
    agent = SimpleNamespace(manifest=manifest, endpoint="info:1")

    async def malformed_llm(_messages):
        return "[]"

    async def no_resolve(_query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=malformed_llm, registry_fn=no_resolve)
    sink = TraceSink()
    with probe_builder(builder, sink):
        asyncio.run(builder.build(
            "查天气", WorkingSet(catalog=[agent]), PlanContext(session_id="t")))

    assert [(row.attempt, row.wire_mode) for row in sink.validations] == [
        (0, "json"), (1, "json"),
    ]
    assert [
        (ref.value, ref.status)
        for row in sink.validations
        for ref in row.raw_capability_refs
    ] == [
        ("<wire:not-object>", "malformed_wire"),
        ("<wire:not-object>", "malformed_wire"),
    ]


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

    plan = builder._parse_and_validate_data(
        _Exploding(steps=[]), _catalog(), "空调先别关")
    assert plan is not None, "生产的返回值必须原样透出——观察不该改变被观察的行为"
    assert sink.validations == [], "取不到候选就记成没观测，不许伪造一份"
    assert sink.trace_errors and "TypeError" in sink.trace_errors[0]


def test_the_hint_probe_survives_the_no_hints_ablation_stand_in():
    """反向构造：`no-hints` 消融臂把 `_route_hints` 换成只有 `apply` 的替身。

    实测：`TracingRouteHints` 直接 `delegate._ordered_hints(...)` → `AttributeError`
    **把整趟 L1 全量打死**。这条路只在 `--ablations on-failure` 下可达，而发现轨主跑
    一直是 `off`——**没跑过的分支不算实现过**，它已经躺在那儿好几批了。

    替身是**合法**的：它就该报「一条 hint 都没有」，不是错误。
    """
    from support.intent_adversarial_runtime import _NoRouteHints
    from support.intent_adversarial_trace import TraceSink, TracingRouteHints

    plan = Plan(steps=[Step(id="s1", agent_id="chitchat", endpoint="c:1",
                            intent="chitchat.talk")])
    sink = TraceSink()
    wrapped = TracingRouteHints(_NoRouteHints(), sink)

    assert wrapped.apply(plan, "附近的充电站", {}) is False
    assert len(sink.hints) == 1, "证据仍要留下：before/after 一样是这一轮的事实"
    assert sink.hints[0].matches == () and sink.hints[0].hit is False
    assert sink.trace_errors == [], "缺 `_ordered_hints` 是合法替身，不该记成探针出错"

    # 枚举过程真的抛异常时：记 trace_errors，但仍然委派、仍然留证据
    class _Exploding:
        def _ordered_hints(self, _agent_map):
            raise RuntimeError("hint 表这次是坏的")

        def _match(self, _hint, _text):
            return None

        def apply(self, _plan, _text, _agent_map):
            return True

    sink2 = TraceSink()
    assert TracingRouteHints(_Exploding(), sink2).apply(plan, "x", {}) is True
    assert len(sink2.hints) == 1 and sink2.hints[0].matches == ()
    assert sink2.trace_errors and "hint_enumeration" in sink2.trace_errors[0]
