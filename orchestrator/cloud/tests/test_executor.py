"""Planner 引擎单元测试：拓扑分层、环检测、slot_refs、部分失败。"""
import pytest
from orchestrator.cloud.models import Step, StepResult, StepStatus, Plan, CyclicPlan
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
