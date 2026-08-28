"""Planner 引擎单元测试：拓扑分层、环检测、slot_refs、部分失败。"""
import json
import time

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


def _focus_ctx(places, ts=None):
    """锚定测试上下文：缺省带**新鲜**取回时刻——时效是另一组用例单独测的变量。"""
    return PlanContext(session_id="s", user_id="u", focus_places=list(places),
                       focus_places_ts=time.time() if ts is None else ts)


# 锚定只对声明了门店三槽的商户 workflow 生效（demo-mkemhn 门控）；
# 测试步骤按 servers.yaml 里 luckin.order/menu 的真实声明造。
_STORE_SLOTS = ["item_query", "store_name", "store_longitude", "store_latitude"]


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
                slots={"item_query": "冰美式"}, declared_slots=_STORE_SLOTS)

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
                declared_slots=_STORE_SLOTS,
                slot_refs={"store_name": "s1.data.items.0.name",
                           "store_longitude": "s1.data.items.0.lng",
                           "store_latitude": "s1.data.items.0.lat"})

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, done, _focus_ctx(_LAST_PLACES))

    assert step.slots["store_name"] == "本轮门店"
    refs = json.loads(step.meta["_trusted_slot_refs"])
    assert all(ref["ref"].startswith("s1.data.items.0.")
               for ref in refs.values())


def test_a_store_name_not_in_the_server_held_list_is_never_substituted():
    """名字对不上服务端持有的门店 → 不锚定，绝不拿第一条顶替用户点名的店。"""
    step = Step(id="s1", agent_id="mcp-bridge", intent="luckin.order",
                slots={"store_name": "用户点名的另一家完全不同的店"},
                declared_slots=_STORE_SLOTS)

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, {}, _focus_ctx(_LAST_PLACES))

    assert "_trusted_slot_refs" not in step.meta
    assert "store_longitude" not in step.slots


def test_planner_supplied_store_values_are_a_hint_not_a_value():
    """Planner 塞进 slots 的门店值只当线索。

    2026-08-13 真栈实测第二轮：`store_name` 是模型从上下文抄来的店名，
    `store_longitude/latitude` 是 `s1.data.items.0.lng` 这种**没解析成的 ref 字面串**。
    它们没有 provenance，消费侧本来就会拒 —— 现状不是「已有门店」而是死路。
    名字命中服务端持有的那条时，三个值全部换成该条的值并盖同构 provenance。
    """
    step = Step(id="s1", agent_id="mcp-bridge", intent="luckin.menu",
                slots={"store_name": "瑞幸咖啡(前海印里店)",
                       "store_longitude": "s1.data.items.0.lng",
                       "store_latitude": "s1.data.items.0.lat"},
                declared_slots=_STORE_SLOTS)

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, {}, _focus_ctx(_LAST_PLACES))

    assert step.slots["store_name"] == "瑞幸咖啡(前海印里店)"
    assert step.slots["store_longitude"] == "113.8981"   # 第 1 条，不是第 0 条
    assert step.slots["store_latitude"] == "22.5301"
    refs = json.loads(step.meta["_trusted_slot_refs"])
    assert all(ref["ref"].startswith("focus.last_places.1.")
               for ref in refs.values())


def test_latin_brand_prefix_does_not_break_the_name_match():
    """高德给的名字常带 `luckin coffee ` 前缀；归一只为匹配，不改落地值。"""
    step = Step(id="s1", agent_id="mcp-bridge", intent="luckin.menu",
                slots={"store_name": "luckin coffee 瑞幸咖啡(前海华强金融大厦店)"},
                declared_slots=_STORE_SLOTS)

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, {}, _focus_ctx(_LAST_PLACES))

    assert step.slots["store_name"] == "瑞幸咖啡(前海华强金融大厦店)"
    assert step.slots["store_longitude"] == "113.9004"


def test_in_plan_producer_that_failed_is_a_defect_not_missing_context():
    """生产者**在本轮 plan 里**却没给出值 = 计划有缺陷，不许被焦点补成「看起来正常」。"""
    done = {"s1": StepResult(step_id="s1", status=StepStatus.FAILED,
                             source_intent="nearby.search", data={})}
    step = Step(id="s2", agent_id="mcp-bridge", intent="luckin.order",
                declared_slots=_STORE_SLOTS,
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
                declared_slots=_STORE_SLOTS,
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
                slots={"item_query": "冰美式"}, declared_slots=_STORE_SLOTS)

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
                    slots={"item_query": "冰美式"}, declared_slots=_STORE_SLOTS)

        DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
            step, {}, _focus_ctx(bad))

        assert "store_name" not in step.slots, bad
        assert "store_longitude" not in step.slots, bad
        assert "_trusted_slot_refs" not in step.meta, bad


def test_store_hint_is_completed_from_in_plan_nearby_producer():
    """`store_hint` 文本槽的一致性补全（demo-3ukshz 二轮旅程探针）：组合计划里
    nearby.search 生产者在场、菜单步却漏了 slot_refs（MiniMax 高频形态）——
    商户拿不到线索退回默认店，「附近的麦当劳」又变成十公里外的碧海君庭。
    只认**本轮 plan 内**的 OK 生产者；跨轮焦点刻意不吃（可能是别的品牌）。"""
    done = {"s1": StepResult(
        step_id="s1", status=StepStatus.OK, source_intent="nearby.search",
        data={"items": [{"name": "麦当劳(高新中五道店)", "city": "深圳",
                         "lng": 113.94, "lat": 22.54}]})}
    step = Step(id="s2", agent_id="mcp-bridge", intent="mcd.menu",
                slots={}, declared_slots=["store_hint", "city", "item_query"])

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, done, _focus_ctx(_LAST_PLACES))

    assert step.slots["store_hint"] == "麦当劳(高新中五道店)"
    # 城市顺带补上（官方按位置搜索 city 必填）——同一条 POI 的 cityname
    assert step.slots["city"] == "深圳"

    # 用户显式给了 hint → 不覆盖；但 city 补全**独立生效**（模型/用户给了 hint
    # 而没给 city 时，searchType 不能因此退回收藏档——真栈踩过）
    explicit = Step(id="s2", agent_id="mcp-bridge", intent="mcd.menu",
                    slots={"store_hint": "国贸店"},
                    declared_slots=["store_hint", "city", "item_query"])
    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        explicit, done, _focus_ctx(_LAST_PLACES))
    assert explicit.slots["store_hint"] == "国贸店"
    assert explicit.slots["city"] == "深圳"

    # 裸 $ 占位符残渣（2026-08-14 真栈：`$s1.data.items.0.name` 穿过 ${} 专用
    # 正则原样发给商户被拒）：①解析层放行裸 $ 形态直接解成真值；②hint 补全把
    # 残渣当未填
    bare = Step(id="s2", agent_id="mcp-bridge", intent="mcd.menu",
                slots={"store_hint": "$s1.data.items.0.name"},
                declared_slots=["store_hint", "city", "item_query"])
    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        bare, done, _focus_ctx(_LAST_PLACES))
    assert bare.slots["store_hint"] == "麦当劳(高新中五道店)"
    assert bare.slots["city"] == "深圳"

    # 本轮没有 nearby 生产者 → **店名**不从跨轮焦点补（焦点里是瑞幸店，注给
    # 麦当劳会把「默认店」恶化成「查无门店」）；**city 例外**——城市跨品牌无
    # 错配面，且官方按位置检索城市必填（直点句唯一的城市来源就是焦点）。
    with_city = [{**_LAST_PLACES[0], "city": "深圳市"}, _LAST_PLACES[1]]
    lone = Step(id="s1", agent_id="mcp-bridge", intent="mcd.menu",
                slots={"store_hint": "国贸店"},
                declared_slots=["store_hint", "city", "item_query"])
    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        lone, {}, _focus_ctx(with_city))
    assert lone.slots["store_hint"] == "国贸店"
    assert lone.slots["city"] == "深圳市"

    # 焦点超龄 → city 也不补（同坐标锚时效门控）
    stale = Step(id="s1", agent_id="mcp-bridge", intent="mcd.menu",
                 slots={"store_hint": "国贸店"},
                 declared_slots=["store_hint", "city", "item_query"])
    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        stale, {}, _focus_ctx(with_city, ts=time.time() - 3600 * 3))
    assert "city" not in stale.slots

    # 没声明 store_hint 的步不吃补全
    plain = Step(id="s2", agent_id="chitchat", intent="chitchat.talk",
                 slots={}, declared_slots=["text"])
    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        plain, done, _focus_ctx(_LAST_PLACES))
    assert "store_hint" not in plain.slots


def test_steps_without_declared_store_slots_are_never_anchored():
    """锚定门控（demo-mkemhn 2fd09d52/44943f00）：capability 没声明门店三槽的步骤
    （chitchat/nearby/任何非商户步）绝不吃焦点补槽——此前门店三槽被注进了
    `chitchat.talk` 的下发槽位，等于把商户上下文塞给开放域兜底模型。"""
    for intent, declared in (("chitchat.talk", ["text"]),
                             ("nearby.search", ["keyword", "brand"]),
                             ("luckin.order", [])):
        step = Step(id="s1", agent_id="x", intent=intent,
                    slots={}, declared_slots=declared)

        DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
            step, {}, _focus_ctx(_LAST_PLACES))

        assert "store_name" not in step.slots, intent
        assert "_trusted_slot_refs" not in step.meta, intent


def test_stale_focus_places_are_not_anchored():
    """时效（设计文档「刚才那家本来就有时效」）：粘性接力让 last_places 永生，
    超龄列表不得再当「刚才那家」用——过期就诚实回到「请先查询附近门店」。"""
    stale = time.time() - 3600 * 3
    step = Step(id="s1", agent_id="mcp-bridge", intent="luckin.order",
                slots={"item_query": "冰美式"}, declared_slots=_STORE_SLOTS)

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, {}, _focus_ctx(_LAST_PLACES, ts=stale))

    assert "store_name" not in step.slots
    assert "_trusted_slot_refs" not in step.meta


def test_focus_places_without_timestamp_count_as_expired():
    """旧焦点数据没有取回时刻（ts=0）：按过龄处理，不按新鲜处理——
    「没有证据」不能与「证据为新鲜」压成同一个值。"""
    step = Step(id="s1", agent_id="mcp-bridge", intent="luckin.order",
                slots={"item_query": "冰美式"}, declared_slots=_STORE_SLOTS)

    DagExecutor(call_agent_fn=lambda *_: None)._resolve_slot_refs(
        step, {}, _focus_ctx(_LAST_PLACES, ts=0.0))

    assert "store_name" not in step.slots
    assert "_trusted_slot_refs" not in step.meta


def test_streaming_direct_path_also_resolves_slots_before_dispatch():
    """D0 流式直通**绕过 executor.run**，所以挂在 `_resolve_slot_refs` 上的东西
    在那条路上默认不生效——本用例是那个坑的回归探针。

    2026-08-13 实证：跨轮门店锚定挂在 `_resolve_slot_refs`，而 `luckin.menu`
    （require_confirm=false）恰好走流式直通，于是诊断日志一行都没打出来——
    那个函数根本没被调用，两轮对话照旧走不通。
    **新增挂点必须枚举全部执行路径**；这是本项目第二次踩（M2 的 Verifier 同款）。

    用源码级断言而不是行为测试：这条要守的是「那个分支里有没有这一步」这个结构事实，
    行为测试会被流式桩的实现细节稀释（同 test_verify.py 的中央挂点断言）。
    """
    import inspect

    from orchestrator.cloud import engine as engine_module

    source = inspect.getsource(engine_module)
    marker = "# D0. 单步新规划走流式直通"
    assert marker in source
    branch = source.split(marker, 1)[1].split("_d0_start", 1)[0]
    assert "_resolve_slot_refs" in branch, (
        "D0 流式直通分支必须先解析槽位；漏了它，挂在 executor 上的槽位逻辑"
        "（跨轮门店锚定等）在这条路上会静默失效")


# ── C5-B：挂起不冻结兄弟步（2026-08-28，QA P1-03/P1-04）──────────────────
# 真栈原形 T50「接孩子放学，顺便找麦当劳，5点到校」：nearby 丢了「麦当劳」槽反问，
# **同轮的 navigate 一次都没发出去**——一个补槽问题把整轮劫持了。

class _Act:
    """action 是**对象**不是 dict，且 `_enforce_capability_confirm` 还要读
    `.require_confirm`。⚠ 首版写成 dict，`_to_result` 当场 AttributeError、被
    `gather(return_exceptions=True)` 兜成 FAILED——四条断言**全绿**，绿的却是
    「步失败了」而不是「步挂起了」（FAILED 同样会拦住下游）。
    **替身长得不像被测契约时，测试会走另一条路径给出同样的读数。**
    所以这一族每条都显式断言 `status`，不只断言「哪些步跑了」。"""

    def __init__(self, type_, payload=None):
        self.type = type_
        self.payload = payload or {}
        self.require_confirm = False


def _exec_with(statuses):
    """按 intent 名给状态的执行器替身：{intent: grpc_status}。"""
    seen = []

    async def call(endpoint, intent, slots, ctx, meta):
        seen.append(intent)
        st = statuses.get(intent, 0)
        return MockResponse(status=st, speech=f"{intent}-said",
                            actions=[_Act(intent)])

    return DagExecutor(call_agent_fn=call), seen


def test_need_slot_does_not_freeze_an_independent_sibling():
    import asyncio
    ex, seen = _exec_with({"nearby.search": 2})     # 2 = NEED_SLOT
    plan = Plan(steps=[
        Step(id="s1", agent_id="nearby", intent="nearby.search"),
        Step(id="s2", agent_id="nav", intent="navigation.navigate_to"),
    ])

    async def run():
        return [r async for r in ex.run(plan, None)]

    results = asyncio.run(run())
    assert {r.step_id for r in results} == {"s1", "s2"}
    by_id = {r.step_id: r for r in results}
    assert by_id["s1"].status == StepStatus.NEED_SLOT
    assert by_id["s2"].status == StepStatus.OK
    assert by_id["s2"].actions[0]["type"] == "navigation.navigate_to"
    assert set(seen) == {"nearby.search", "navigation.navigate_to"}


def test_need_slot_still_blocks_its_own_downstream():
    """下游由 `_should_run`（依赖必须 OK）天然拦住——这里不需要第二份判据。"""
    import asyncio
    ex, seen = _exec_with({"nearby.search": 2})
    plan = Plan(steps=[
        Step(id="s1", agent_id="nearby", intent="nearby.search"),
        Step(id="s2", agent_id="shop", intent="shop.order", depends_on=["s1"]),
    ])

    async def run():
        return [r async for r in ex.run(plan, None)]

    results = asyncio.run(run())
    assert [r.step_id for r in results] == ["s1"]
    assert results[0].status == StepStatus.NEED_SLOT
    assert seen == ["nearby.search"]


def test_need_confirm_still_stops_the_whole_turn():
    """对照：待确认是对**整轮**说的「先别做」，语义逐字不变。"""
    import asyncio
    ex, seen = _exec_with({"shop.order": 1})        # 1 = NEED_CONFIRM
    plan = Plan(steps=[
        Step(id="s1", agent_id="shop", intent="shop.order"),
        Step(id="s2", agent_id="w", intent="weather.query", depends_on=["s1"]),
    ])

    async def run():
        return [r async for r in ex.run(plan, None)]

    results = asyncio.run(run())
    assert [r.step_id for r in results] == ["s1"]
    assert results[0].status == StepStatus.NEED_CONFIRM
    assert seen == ["shop.order"]


def test_a_suspended_step_never_swallows_its_layer_mates_results():
    """同层是 `asyncio.gather` 一次跑完的：挂起那条不许把已算出来的结果吞掉
    ——那不是「没执行」，是「执行了但不报」。"""
    import asyncio
    ex, seen = _exec_with({"shop.order": 1})
    plan = Plan(steps=[
        Step(id="s1", agent_id="shop", intent="shop.order"),      # NEED_CONFIRM
        Step(id="s2", agent_id="w", intent="weather.query"),      # 同层、已执行
    ])

    async def run():
        return [r async for r in ex.run(plan, None)]

    results = asyncio.run(run())
    assert {r.step_id for r in results} == {"s1", "s2"}
    by_id = {r.step_id: r for r in results}
    assert by_id["s1"].status == StepStatus.NEED_CONFIRM
    assert by_id["s2"].status == StepStatus.OK
    assert set(seen) == {"shop.order", "weather.query"}


def test_need_slot_lets_a_later_layer_run_when_it_depends_on_something_else():
    """跨层那一半：s3 依赖的是 s2（OK）不是挂起的 s1 ⇒ 它照跑。

    ⚠ 这条是**反向验证补出来的**：前面三条里的兄弟步都在同一层，`asyncio.gather`
    本来就会跑它们——把旧的「任一挂起当场 return」注射回去，那三条**一条都不红**。
    真正被跨层语义决定的只有这一条。
    """
    import asyncio
    ex, seen = _exec_with({"nearby.search": 2})
    plan = Plan(steps=[
        Step(id="s1", agent_id="nearby", intent="nearby.search"),      # NEED_SLOT
        Step(id="s2", agent_id="w", intent="weather.query"),           # OK，同层
        Step(id="s3", agent_id="r", intent="reminder.create",
             depends_on=["s2"]),                                       # 下一层
    ])

    async def run():
        return [r async for r in ex.run(plan, None)]

    results = asyncio.run(run())
    assert {r.step_id for r in results} == {"s1", "s2", "s3"}
    assert {r.step_id: r.status for r in results}["s3"] == StepStatus.OK
    assert "reminder.create" in seen
