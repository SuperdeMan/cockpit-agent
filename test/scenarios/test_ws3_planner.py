"""WS3 场景回归：Planner 编排引擎关键路径。

不依赖 proto gen，直接测试 models/executor/planning/session 逻辑。
"""
import asyncio
import sys, os
import importlib

# 设置包路径，让 orchestrator.cloud 的相对 import 能工作
_root = os.path.join(os.path.dirname(__file__), "../..")
sys.path.insert(0, _root)

# 加载 models 模块（executor/session 等依赖它）
_models_path = os.path.join(_root, "orchestrator/cloud/models.py")
spec = importlib.util.spec_from_file_location("orchestrator.cloud.models", _models_path,
                                                submodule_search_locations=[])
models = importlib.util.module_from_spec(spec)
sys.modules["orchestrator.cloud.models"] = models
spec.loader.exec_module(models)

Step, StepResult, StepStatus, Plan, PlanContext, SessionState, CyclicPlan = (
    models.Step, models.StepResult, models.StepStatus, models.Plan,
    models.PlanContext, models.SessionState, models.CyclicPlan)

# 加载 executor
_exec_path = os.path.join(_root, "orchestrator/cloud/executor.py")
spec2 = importlib.util.spec_from_file_location("orchestrator.cloud.executor", _exec_path)
executor = importlib.util.module_from_spec(spec2)
sys.modules["orchestrator.cloud.executor"] = executor
spec2.loader.exec_module(executor)
DagExecutor = executor.DagExecutor

# 加载 session
_sess_path = os.path.join(_root, "orchestrator/cloud/session.py")
spec3 = importlib.util.spec_from_file_location("orchestrator.cloud.session", _sess_path)
session = importlib.util.module_from_spec(spec3)
sys.modules["orchestrator.cloud.session"] = session
spec3.loader.exec_module(session)
SessionStore = session.SessionStore


# ─── 场景 1：组合意图 DAG（搜餐厅→订位） ───

def test_scenario_combo_intent_dag():
    """"找家川菜馆订今晚的位" → DAG: s1(search) → s2(reserve)"""
    steps = [
        Step(id="s1", agent_id="navigation", intent="navigation.search_poi",
             slots={"keyword": "川菜"}),
        Step(id="s2", agent_id="food-ordering", intent="food.reserve",
             slots={"datetime": "今晚19:00", "party_size": "2"},
             depends_on=["s1"], slot_refs={"restaurant_id": "s1.data.items.0.id"}),
    ]
    layers = DagExecutor._topo_layers(steps)
    assert len(layers) == 2
    assert layers[0][0].id == "s1"
    assert layers[1][0].id == "s2"


# ─── 场景 2：缺槽位追问→补槽续接 ───

def test_scenario_need_slot_then_resume():
    """search_poi 缺 keyword → NEED_SLOT → 用户补关键词 → 续接"""
    store = SessionStore()
    plan_data = {
        "steps": [
            {"id": "s1", "agent_id": "navigation", "endpoint": "",
             "intent": "navigation.search_poi", "slots": {},
             "depends_on": [], "slot_refs": {}, "require_confirm": False,
             "latency_budget_ms": 5000},
        ],
        "raw_text": "找个地方",
    }
    state = SessionState(phase="wait_slot", pending_step_id="s1",
                         pending_plan=plan_data)
    asyncio.run(store.save("sess-slot", state))

    loaded = asyncio.run(store.load("sess-slot"))
    assert loaded is not None
    assert loaded.phase == "wait_slot"
    assert loaded.pending_step_id == "s1"

    # 续接后清除
    asyncio.run(store.clear("sess-slot"))
    assert asyncio.run(store.load("sess-slot")) is None


# ─── 场景 3：二次确认→确认续接 ───

def test_scenario_need_confirm_then_resume():
    """reserve 需确认 → 用户说"订吧" → 续接"""
    store = SessionStore()
    state = SessionState(
        phase="wait_confirm",
        pending_step_id="s2",
        pending_plan={"steps": [
            {"id": "s1", "agent_id": "navigation", "endpoint": "",
             "intent": "navigation.search_poi", "slots": {"keyword": "川菜"},
             "depends_on": [], "slot_refs": {}, "require_confirm": False,
             "latency_budget_ms": 5000},
            {"id": "s2", "agent_id": "food-ordering", "endpoint": "",
             "intent": "food.reserve", "slots": {},
             "depends_on": ["s1"], "slot_refs": {}, "require_confirm": True,
             "latency_budget_ms": 5000},
        ], "raw_text": ""},
        completed_results={"s1": {"step_id": "s1", "status": "ok"}},
    )
    asyncio.run(store.save("sess-confirm", state))
    loaded = asyncio.run(store.load("sess-confirm"))
    assert loaded.phase == "wait_confirm"
    assert "s1" in loaded.completed_results


# ─── 场景 4：部分失败→下游跳过 ───

def test_scenario_partial_failure_skip():
    """s1 失败 → s2(SKIPPED) → s3(SKIPPED)"""
    steps = [
        Step(id="s1", agent_id="a"),
        Step(id="s2", agent_id="b", depends_on=["s1"]),
        Step(id="s3", agent_id="c", depends_on=["s2"]),
    ]
    done = {"s1": StepResult(step_id="s1", status=StepStatus.FAILED, error="timeout")}
    DagExecutor._mark_skipped(steps, done)
    assert done["s2"].status == StepStatus.SKIPPED
    assert done["s3"].status == StepStatus.SKIPPED


# ─── 场景 5：并行无依赖 ───

def test_scenario_parallel_independent():
    """s1, s2 无依赖 → 同层并行"""
    steps = [
        Step(id="s1", agent_id="navigation"),
        Step(id="s2", agent_id="info"),
        Step(id="s3", agent_id="trip-planner", depends_on=["s1", "s2"]),
    ]
    layers = DagExecutor._topo_layers(steps)
    assert len(layers) == 2
    assert set(s.id for s in layers[0]) == {"s1", "s2"}
    assert layers[1][0].id == "s3"


# ─── 场景 6：计划成环检测 ───

def test_scenario_cycle_detection():
    """s1→s2→s1 成环"""
    steps = [
        Step(id="s1", agent_id="a", depends_on=["s2"]),
        Step(id="s2", agent_id="b", depends_on=["s1"]),
    ]
    try:
        DagExecutor._topo_layers(steps)
        assert False, "should raise CyclicPlan"
    except CyclicPlan:
        pass
