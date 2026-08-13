"""Planner 引擎单元测试：拓扑分层、环检测、slot_refs、部分失败。"""
import json

import pytest
from orchestrator.cloud.models import (Step, StepResult, StepStatus, Plan,
                                       PlanContext, CyclicPlan)
from orchestrator.cloud.executor import DagExecutor


# ─── 拓扑分层测试 ───

def test_topo_single_step():
    steps = [Step(id="s1", agent_id="a")]
    layers = DagExecutor._topo_layers(steps)
    assert len(layers) == 1
    assert layers[0][0].id == "s1"

def test_topo_chain():
    steps = [
        Step(id="s1", agent_id="a"),
        Step(id="s2", agent_id="b", depends_on=["s1"]),
        Step(id="s3", agent_id="c", depends_on=["s2"]),
    ]
    layers = DagExecutor._topo_layers(steps)
    assert len(layers) == 3
    assert [s.id for s in layers[0]] == ["s1"]
    assert [s.id for s in layers[1]] == ["s2"]
    assert [s.id for s in layers[2]] == ["s3"]

def test_topo_parallel():
    steps = [
        Step(id="s1", agent_id="a"),
        Step(id="s2", agent_id="b"),
        Step(id="s3", agent_id="c", depends_on=["s1", "s2"]),
    ]
    layers = DagExecutor._topo_layers(steps)
    assert len(layers) == 2
    assert set(s.id for s in layers[0]) == {"s1", "s2"}
    assert [s.id for s in layers[1]] == ["s3"]

def test_topo_diamond():
    steps = [
        Step(id="s1", agent_id="a"),
        Step(id="s2", agent_id="b", depends_on=["s1"]),
        Step(id="s3", agent_id="c", depends_on=["s1"]),
        Step(id="s4", agent_id="d", depends_on=["s2", "s3"]),
    ]
    layers = DagExecutor._topo_layers(steps)
    assert len(layers) == 3
    assert [s.id for s in layers[0]] == ["s1"]
    assert set(s.id for s in layers[1]) == {"s2", "s3"}
    assert [s.id for s in layers[2]] == ["s4"]

def test_topo_cycle_raises():
    steps = [
        Step(id="s1", agent_id="a", depends_on=["s2"]),
        Step(id="s2", agent_id="b", depends_on=["s1"]),
    ]
    with pytest.raises(CyclicPlan):
        DagExecutor._topo_layers(steps)


# ─── 部分失败测试 ───

def test_run_accepts_dependency_from_external_seed():
    import asyncio

    calls = []

    async def call(endpoint, intent, slots, ctx, meta):
        calls.append((intent, dict(slots)))
        return MockResponse(status=0, speech="done")

    ex = DagExecutor(call_agent_fn=call)
    step = Step(
        id="r1",
        agent_id="a",
        intent="follow_up",
        depends_on=["s1"],
        slot_refs={"token": "s1.data.token"},
    )
    seed = {
        "s1": StepResult(
            step_id="s1",
            status=StepStatus.OK,
            data={"token": "abc"},
        ),
    }

    async def run():
        return [r async for r in ex.run(Plan(steps=[step]), None, done=seed)]

    results = asyncio.run(run())

    assert [r.step_id for r in results] == ["r1"]
    assert calls == [("follow_up", {"token": "abc"})]


def test_mark_skipped():
    steps = [
        Step(id="s1", agent_id="a"),
        Step(id="s2", agent_id="b", depends_on=["s1"]),
        Step(id="s3", agent_id="c", depends_on=["s2"]),
    ]
    done = {"s1": StepResult(step_id="s1", status=StepStatus.FAILED, error="timeout")}
    DagExecutor._mark_skipped(steps, done)
    assert done["s2"].status == StepStatus.SKIPPED
    assert done["s3"].status == StepStatus.SKIPPED

def test_should_run_with_failed_dep():
    step = Step(id="s2", agent_id="b", depends_on=["s1"])
    done = {"s1": StepResult(step_id="s1", status=StepStatus.FAILED)}
    assert DagExecutor._should_run(step, done) is False

def test_should_run_with_ok_dep():
    step = Step(id="s2", agent_id="b", depends_on=["s1"])
    done = {"s1": StepResult(step_id="s1", status=StepStatus.OK)}
    assert DagExecutor._should_run(step, done) is True

def test_should_run_no_deps():
    step = Step(id="s1", agent_id="a")
    assert DagExecutor._should_run(step, {}) is True


# ─── slot_refs 解析测试 ───

def test_resolve_ref_basic():
    done = {"s1": StepResult(step_id="s1", status=StepStatus.OK,
                             data={"restaurant_id": "r123", "name": "川菜馆"})}
    result = DagExecutor._resolve_ref("s1.data.restaurant_id", done)
    assert result == "r123"

def test_resolve_ref_nested():
    done = {"s1": StepResult(step_id="s1", status=StepStatus.OK,
                             data={"items": [{"id": "r1"}, {"id": "r2"}]})}
    result = DagExecutor._resolve_ref("s1.data.items.0.id", done)
    assert result == "r1"

def test_resolve_ref_missing():
    done = {}
    result = DagExecutor._resolve_ref("s1.data.x", done)
    assert result is None

def test_resolve_ref_invalid_path():
    done = {"s1": StepResult(step_id="s1", status=StepStatus.OK, data={"a": "b"})}
    result = DagExecutor._resolve_ref("s1.data.nonexistent", done)
    assert result is None


def test_resolve_slot_refs_expands_exact_placeholders_in_existing_slots():
    done = {
        "s1": StepResult(
            step_id="s1",
            status=StepStatus.OK,
            data={"items": [{"id": "poi-1", "name": "云栖咖啡", "address": "科技园路 1 号"}]},
        )
    }
    step = Step(
        id="s2",
        agent_id="navigation",
        slots={
            "destination": "${s1.data.items.0.name}",
            "place_address": "${s1.data.items.0.address}",
            "literal": "保留原值",
        },
        slot_refs={"poi_id": "s1.data.items.0.id"},
    )

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(step, done)

    assert step.slots == {
        "destination": "云栖咖啡",
        "place_address": "科技园路 1 号",
        "literal": "保留原值",
        "poi_id": "poi-1",
    }


def test_resolve_slot_refs_expands_minimax_ref_alias_in_existing_slot():
    """MiniMax 真栈会把自由对象编码成 $text，并把下游槽写成
    destination="$ref.poi_id" + slot_refs.poi_id。两段信息合起来是完整引用，
    不能让字面量 ``$ref.poi_id`` 漏进导航 Agent。"""
    done = {
        "s1": StepResult(
            step_id="s1",
            status=StepStatus.OK,
            data={"items": [{"id": "poi-1", "name": "灯花·川小馆"}]},
        )
    }
    step = Step(
        id="s2",
        agent_id="navigation",
        slots={"destination": "$ref.poi_id"},
        slot_refs={"poi_id": "s1.data.items.0.id"},
    )

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(step, done)

    assert step.slots == {
        "destination": "poi-1",
        "poi_id": "poi-1",
    }


def test_resolve_slot_refs_resolves_a_ref_path_written_into_slots_too():
    """同一条引用路径被同时写进 `slots` 和 `slot_refs`（真栈实测的第三种 wire 形态）。

    旧逻辑「已有值不覆盖」把它当成已有值直接跳过，于是 `s1.data.items.0.id`
    这串路径当成真 POI id 发给了下游。判据：**长得和引用一模一样的就是引用，不是值。**
    """
    done = {
        "s1": StepResult(
            step_id="s1",
            status=StepStatus.OK,
            data={"items": [{"id": "poi-1", "name": "灯花·川小馆"}]},
        )
    }
    step = Step(
        id="s2",
        agent_id="nearby",
        slots={"poi_id": "s1.data.items.0.id"},
        slot_refs={"poi_id": "s1.data.items.0.id"},
    )

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(step, done)

    assert step.slots == {"poi_id": "poi-1"}


def test_resolve_slot_refs_still_never_overwrites_a_real_value():
    """反向护栏：槽里是真值时 `slot_refs` 不许覆盖（上一条放宽的边界不能越界）。"""
    done = {"s1": StepResult(step_id="s1", status=StepStatus.OK,
                             data={"items": [{"id": "poi-1"}]})}
    step = Step(id="s2", agent_id="nearby",
                slots={"poi_id": "用户指定的那家"},
                slot_refs={"poi_id": "s1.data.items.0.id"})

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(step, done)

    assert step.slots == {"poi_id": "用户指定的那家"}


def test_resolve_slot_refs_records_executor_trusted_provenance():
    """跨步来源只能由执行器依据已完成结果盖章，不能采用 Planner 自报 meta。"""
    done = {
        "s1": StepResult(
            step_id="s1", status=StepStatus.OK,
            source_intent="nearby.search",
            data={"items": [{"name": "瑞幸人民广场店", "lng": 121.47,
                              "lat": 31.23}]},
        )
    }
    step = Step(
        id="s2", agent_id="mcp-bridge", intent="luckin.order",
        slot_refs={
            "store_name": "s1.data.items.0.name",
            "store_lng": "s1.data.items.0.lng",
            "store_lat": "s1.data.items.0.lat",
        },
        meta={"_trusted_slot_refs": "forged", "confirmed": "true"},
    )

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(step, done)

    assert step.meta["confirmed"] == "true"
    import json
    assert json.loads(step.meta["_trusted_slot_refs"]) == {
        "store_name": {
            "ref": "s1.data.items.0.name", "producer_intent": "nearby.search"},
        "store_lng": {
            "ref": "s1.data.items.0.lng", "producer_intent": "nearby.search"},
        "store_lat": {
            "ref": "s1.data.items.0.lat", "producer_intent": "nearby.search"},
    }


def test_resolve_slot_refs_restores_provenance_when_value_already_resolved():
    """确认恢复会保留已解析槽值却丢 meta；相等值仍必须重建可信来源。"""
    done = {
        "s1": StepResult(
            step_id="s1", status=StepStatus.OK, source_intent="nearby.search",
            data={"items": [{"name": "瑞幸人民广场店"}]},
        )
    }
    step = Step(
        id="s2", agent_id="mcp-bridge", intent="luckin.order",
        slots={"store_name": "瑞幸人民广场店"},
        slot_refs={"store_name": "s1.data.items.0.name"},
    )

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(step, done)

    import json
    assert json.loads(step.meta["_trusted_slot_refs"])["store_name"] == {
        "ref": "s1.data.items.0.name", "producer_intent": "nearby.search"}


def test_executor_overwrites_agent_claimed_source_intent():
    import asyncio

    async def call(endpoint, intent, slots, ctx, meta):
        return MockResponse(status=0, speech="done")

    ex = DagExecutor(call_agent_fn=call)
    step = Step(id="s1", agent_id="nearby", intent="nearby.search")

    async def run():
        return [r async for r in ex.run(Plan(steps=[step]), None)]

    result = asyncio.run(run())[0]
    assert result.source_intent == "nearby.search"


def test_slot_ref_provenance_survives_result_serialization_restore():
    """挂起态白名单必须保留 producer source，确认轮才能重建 provenance。"""
    from orchestrator.cloud.engine import _RESULT_FIELDS

    result = StepResult(
        step_id="s1", status=StepStatus.OK,
        source_intent="nearby.search", data={"items": [{"name": "门店"}]})
    wire = {k: v for k, v in result.__dict__.items() if k in _RESULT_FIELDS}
    wire["status"] = StepStatus(wire["status"])
    restored = StepResult(**wire)

    assert restored.source_intent == "nearby.search"


# ─── to_result 测试 ───

class MockResponse:
    def __init__(self, status=0, speech="", actions=None, follow_up=""):
        self.status = status
        self.speech = speech
        self.actions = actions or []
        self.ui_card = None
        self.follow_up = follow_up
        self.data = None           # F3
        self.missing_slots = []    # F12

def test_to_result_ok():
    resp = MockResponse(status=0, speech="已为您找到3家餐厅")
    result = DagExecutor._to_result("s1", resp)
    assert result.status == StepStatus.OK
    assert result.speech == "已为您找到3家餐厅"

def test_to_result_need_confirm():
    resp = MockResponse(status=1, speech="确认预订吗？")
    result = DagExecutor._to_result("s1", resp)
    assert result.status == StepStatus.NEED_CONFIRM

def test_to_result_failed():
    resp = MockResponse(status=3, speech="出错了")
    result = DagExecutor._to_result("s1", resp)
    assert result.status == StepStatus.FAILED


# ─── 确认续接：done 种子 + step.meta 透传（F1）───

def test_run_skips_seeded_done_steps_and_passes_meta():
    """种子结果不重跑；挂起步骤的 meta（confirmed）随调用下发。"""
    import asyncio

    calls = []

    async def call(endpoint, intent, slots, ctx, meta):
        calls.append((intent, dict(meta or {})))
        return MockResponse(status=0, speech="done")

    ex = DagExecutor(call_agent_fn=call)
    steps = [
        Step(id="s1", agent_id="a", intent="nearby.search"),
        Step(id="s2", agent_id="a", intent="nearby.order",
             depends_on=["s1"], meta={"confirmed": "true"}),
    ]
    seed = {"s1": StepResult(step_id="s1", status=StepStatus.OK, speech="earlier")}

    async def run():
        return [r async for r in ex.run(Plan(steps=steps), None, done=seed)]

    results = asyncio.run(run())
    # 只 yield 新执行的步骤；s1 未被重跑
    assert [r.step_id for r in results] == ["s2"]
    assert calls == [("nearby.order", {"confirmed": "true"})]


def test_run_seeded_failed_dep_skips_children():
    """种子里的失败步骤同样阻断后继。"""
    import asyncio

    calls = []

    async def call(endpoint, intent, slots, ctx, meta):
        calls.append(intent)
        return MockResponse(status=0, speech="done")

    ex = DagExecutor(call_agent_fn=call)
    steps = [
        Step(id="s1", agent_id="a", intent="i1"),
        Step(id="s2", agent_id="a", intent="i2", depends_on=["s1"]),
    ]
    seed = {"s1": StepResult(step_id="s1", status=StepStatus.FAILED, error="boom")}

    async def run():
        return [r async for r in ex.run(Plan(steps=steps), None, done=seed)]

    results = asyncio.run(run())
    assert calls == []
    assert results == []


def _focus_ctx(places):
    return PlanContext(session_id="s", user_id="u", focus_places=list(places))


_LAST_PLACES = [
    {"name": "瑞幸咖啡(前海华强金融大厦店)", "lng": 113.9004, "lat": 22.5362},
    {"name": "瑞幸咖啡(前海印里店)", "lng": 113.8981, "lat": 22.5301},
]


def test_store_slots_fall_back_to_last_turn_places_with_focus_provenance():
    """跨轮门店锚定：本轮 plan 内没有生产者时，用上一轮 nearby.search 的公开 POI 补。

    这条修的是「先查附近的瑞幸」「在最近那家点一杯」两轮走不通——用户心智里的
    「这家店」是跨轮的，而可信链原本只在同一轮 plan 内成立。
    延续的是**服务端记得取回过哪些门店**，不是让模型把坐标再说一遍。
    """
    step = Step(id="s1", agent_id="mcp-bridge", intent="luckin.order",
                slots={"item_query": "冰美式"})

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, {}, _focus_ctx(_LAST_PLACES))

    assert step.slots["store_name"] == "瑞幸咖啡(前海华强金融大厦店)"
    assert step.slots["store_longitude"] == "113.9004"
    assert step.slots["store_latitude"] == "22.5362"
    assert json.loads(step.meta["_trusted_slot_refs"]) == {
        "store_name": {"ref": "focus.last_places.0.name",
                       "producer_intent": "nearby.search"},
        "store_longitude": {"ref": "focus.last_places.0.lng",
                            "producer_intent": "nearby.search"},
        "store_latitude": {"ref": "focus.last_places.0.lat",
                           "producer_intent": "nearby.search"},
    }


def test_in_plan_producer_always_wins_over_last_turn_focus():
    """本轮真取回的结果永远比上一轮的记忆新——有生产者时焦点必须让路。"""
    done = {"s1": StepResult(
        step_id="s1", status=StepStatus.OK, source_intent="nearby.search",
        data={"items": [{"name": "本轮门店", "lng": 121.47, "lat": 31.23}]})}
    step = Step(id="s2", agent_id="mcp-bridge", intent="luckin.order",
                slot_refs={"store_name": "s1.data.items.0.name",
                           "store_longitude": "s1.data.items.0.lng",
                           "store_latitude": "s1.data.items.0.lat"})

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, done, _focus_ctx(_LAST_PLACES))

    assert step.slots["store_name"] == "本轮门店"
    refs = json.loads(step.meta["_trusted_slot_refs"])
    assert all(ref["ref"].startswith("s1.data.items.0.")
               for ref in refs.values())


def test_focus_anchor_never_overrides_a_store_the_user_named_this_turn():
    step = Step(id="s1", agent_id="mcp-bridge", intent="luckin.order",
                slots={"store_name": "用户本轮点名的那家"})

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, {}, _focus_ctx(_LAST_PLACES))

    assert step.slots["store_name"] == "用户本轮点名的那家"
    assert "_trusted_slot_refs" not in step.meta


def test_in_plan_producer_that_failed_is_a_defect_not_missing_context():
    """生产者**在本轮 plan 里**却没给出值 = 计划有缺陷，不许被焦点补成「看起来正常」。"""
    done = {"s1": StepResult(step_id="s1", status=StepStatus.FAILED,
                             source_intent="nearby.search", data={})}
    step = Step(id="s2", agent_id="mcp-bridge", intent="luckin.order",
                slot_refs={"store_name": "s1.data.items.0.name"})

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, done, _focus_ctx(_LAST_PLACES))

    assert "store_name" not in step.slots
    assert "_trusted_slot_refs" not in step.meta


def test_dangling_ref_to_a_previous_turn_step_is_exactly_the_cross_turn_case():
    """悬空引用 = 跨轮，不是缺陷。

    2026-08-13 真栈实测：第二轮「在最近那家看看有什么可以点的」模型产的是
    `slot_refs: {store_name: "s0.data.items.0.name"}`，而 `s0` 是**上一轮**的步骤 id。
    第一版把这种也当计划缺陷让路，跨轮锚定于是永远不触发——两轮对话照旧走不通。
    """
    step = Step(id="s1", agent_id="mcp-bridge", intent="luckin.menu",
                slot_refs={"store_name": "s0.data.items.0.name",
                           "store_longitude": "s0.data.items.0.lng",
                           "store_latitude": "s0.data.items.0.lat"})

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, {}, _focus_ctx(_LAST_PLACES))

    assert step.slots["store_name"] == "瑞幸咖啡(前海华强金融大厦店)"
    refs = json.loads(step.meta["_trusted_slot_refs"])
    assert all(ref["ref"].startswith("focus.last_places.0.")
               for ref in refs.values())


def test_no_focus_places_means_no_anchor_at_all():
    step = Step(id="s1", agent_id="mcp-bridge", intent="luckin.order",
                slots={"item_query": "冰美式"})

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, {}, _focus_ctx([]))

    assert "store_name" not in step.slots
    assert "_trusted_slot_refs" not in step.meta


def test_malformed_focus_places_are_dropped_not_partially_applied():
    """半条门店比没有门店更危险：任一字段坏掉就整条不补。"""
    for bad in ([{"name": "只有名字"}],
                [{"name": "", "lng": 113.9, "lat": 22.5}],
                [{"name": "越界", "lng": 999.0, "lat": 22.5}],
                [{"name": "非数", "lng": "abc", "lat": 22.5}]):
        step = Step(id="s1", agent_id="mcp-bridge", intent="luckin.order",
                    slots={"item_query": "冰美式"})

        DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
            step, {}, _focus_ctx(bad))

        assert "store_name" not in step.slots, bad
        assert "store_longitude" not in step.slots, bad
        assert "_trusted_slot_refs" not in step.meta, bad
