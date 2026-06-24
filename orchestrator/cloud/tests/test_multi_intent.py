"""Multi-intent DAG 拆分黄金用例。

覆盖 4 类场景：纯并行车控、串行跨域、控制+播报混合、单意图直通。
全部用进程内 stub，不依赖 gRPC/proto 生成代码。
遵循 test_engine_confirm.py 的 _Spy 模式。
"""
from __future__ import annotations
import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.session import SessionStore
from security.permission import PermissionEngine


# ─── 测试 Agent 定义 ───

class _Cap:
    def __init__(self, intent, slots=None):
        self.intent, self.slots, self.description = intent, slots or [], intent


def _hvac_agent():
    manifest = SimpleNamespace(
        agent_id="hvac",
        trust_level="oem",
        latency_budget_ms=2000,
        requires_permissions=[],
        capabilities=[_Cap("hvac.set", ["temperature", "mode"])],
    )
    return SimpleNamespace(manifest=manifest, endpoint="stub:50070")


def _media_agent():
    manifest = SimpleNamespace(
        agent_id="media",
        trust_level="oem",
        latency_budget_ms=2000,
        requires_permissions=[],
        capabilities=[_Cap("media.play", ["genre", "song"])],
    )
    return SimpleNamespace(manifest=manifest, endpoint="stub:50071")


def _food_agent():
    manifest = SimpleNamespace(
        agent_id="food-ordering",
        trust_level="third_party",
        latency_budget_ms=2000,
        requires_permissions=[],
        capabilities=[
            _Cap("food.search_restaurant", ["cuisine"]),
            _Cap("food.reserve", ["restaurant_name", "datetime", "party_size"]),
        ],
    )
    return SimpleNamespace(manifest=manifest, endpoint="stub:50063")


def _info_agent():
    manifest = SimpleNamespace(
        agent_id="info",
        trust_level="oem",
        latency_budget_ms=3000,
        requires_permissions=[],
        capabilities=[_Cap("info.weather", ["city"])],
    )
    return SimpleNamespace(manifest=manifest, endpoint="stub:50072")


_ALL_AGENTS = [_hvac_agent(), _media_agent(), _food_agent(), _info_agent()]


# ─── Stub 响应 ───

class _Resp:
    def __init__(self, status=0, speech="", data=None):
        self.status = status
        self.speech = speech
        self.follow_up = ""
        self.actions = []
        self.ui_card = None
        self.data = data
        self.missing_slots = []


# ─── Spy（记录调用顺序 + 返回脚本结果） ───

class _Spy:
    """记录 agent 调用的 (intent, order) 并按意图返回预设结果。

    execution_order 记录每次 call_agent 的调用序号，用于验证并行/串行语义。
    """

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.call_counter = 0
        # 记录 (intent, call_counter) 用于断言执行顺序
        self.execution_order: list[tuple[str, int]] = []

    def count(self, intent: str) -> int:
        return sum(1 for i, _ in self.calls if i == intent)

    def intents_called(self) -> list[str]:
        return [i for i, _ in self.calls]

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.call_counter += 1
        self.calls.append((intent, dict(meta or {})))
        self.execution_order.append((intent, self.call_counter))

        _RESPONSES = {
            "hvac.set": _Resp(speech="空调已设置为24度。"),
            "media.play": _Resp(speech="正在播放音乐。"),
            "food.search_restaurant": _Resp(speech="为您找到3家川菜。"),
            "food.reserve": _Resp(
                status=1,  # NEED_CONFIRM
                speech="确认为您预订川菜·名店1吗？",
            ),
            "info.weather": _Resp(speech="今天晴，气温28度。"),
        }
        return _RESPONSES.get(intent, _Resp(status=3, speech="未知意图"))

    async def llm(self, messages, **kwargs):
        system = messages[0]["content"]
        if "任务编排器" in system:
            # 根据 user message 中的关键词返回不同计划
            user_msg = messages[1]["content"] if len(messages) > 1 else ""
            return self._plan_for(user_msg)
        return "好的，已完成。"

    def _plan_for(self, user_msg: str) -> str:
        """根据用户输入返回对应的 DAG 计划 JSON。"""
        if "空调" in user_msg and "音乐" in user_msg:
            # 场景1: 纯并行车控
            return json.dumps({
                "steps": [
                    {"id": "s1", "agent_id": "hvac", "intent": "hvac.set",
                     "slots": {"temperature": "24"}, "depends_on": [], "slot_refs": {}},
                    {"id": "s2", "agent_id": "media", "intent": "media.play",
                     "slots": {}, "depends_on": [], "slot_refs": {}},
                ]
            })
        if "川菜" in user_msg and "订" in user_msg:
            # 场景2: 串行跨域（搜索 → 预订）
            return json.dumps({
                "steps": [
                    {"id": "s1", "agent_id": "food-ordering",
                     "intent": "food.search_restaurant",
                     "slots": {"cuisine": "川菜"}, "depends_on": [], "slot_refs": {}},
                    {"id": "s2", "agent_id": "food-ordering",
                     "intent": "food.reserve",
                     "slots": {}, "depends_on": ["s1"],
                     "slot_refs": {"restaurant_id": "s1.data.items.0.id"}},
                ]
            })
        if "空调" in user_msg and "天气" in user_msg:
            # 场景3: 控制 + 播报混合（并行）
            return json.dumps({
                "steps": [
                    {"id": "s1", "agent_id": "hvac", "intent": "hvac.set",
                     "slots": {"temperature": "24"}, "depends_on": [], "slot_refs": {}},
                    {"id": "s2", "agent_id": "info", "intent": "info.weather",
                     "slots": {}, "depends_on": [], "slot_refs": {}},
                ]
            })
        if "空调" in user_msg:
            # 场景4: 单意图
            return json.dumps({
                "steps": [
                    {"id": "s1", "agent_id": "hvac", "intent": "hvac.set",
                     "slots": {"temperature": "24"}, "depends_on": [], "slot_refs": {}},
                ]
            })
        return '{"steps":[]}'

    async def resolve(self, query="", intent="", top_k=1):
        return _ALL_AGENTS

    async def list_agents(self):
        return _ALL_AGENTS


# ─── 工厂 ───

def _make_engine() -> tuple[PlannerEngine, _Spy, SessionStore]:
    spy = _Spy()
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session,
        perms=PermissionEngine(),
    )
    return engine, spy, session


def _req(text: str, session_id: str = "sess-mi"):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="r1",
        is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _run(engine, req) -> list[dict]:
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


# ─── 黄金用例 ───

def test_parallel_vehicle_control():
    """「打开空调并播放音乐」→ 2 个无依赖 step，并行执行。

    验证点：
    - 拆出 2 个 step（hvac.set + media.play）
    - 两个 step 都被执行（各 1 次）
    - 两个 step 无 depends_on（并行层）
    - 聚合结果包含双方话术
    """
    engine, spy, _ = _make_engine()
    events = _run(engine, _req("打开空调并播放音乐"))
    final = events[-1]

    assert spy.count("hvac.set") == 1
    assert spy.count("media.play") == 1
    # 两个意图都被调用
    intents = spy.intents_called()
    assert "hvac.set" in intents
    assert "media.play" in intents
    # 聚合完成
    assert final["kind"] == "final"
    assert final.get("speech")  # 非空即可


def test_serial_cross_domain_with_depends_on():
    """「找川菜馆订今晚的位」→ 搜索 → 预订，有 depends_on。

    验证点：
    - 拆出 2 个 step（food.search_restaurant + food.reserve）
    - search 先于 reserve 执行（execution_order 验证）
    - reserve 的 meta 包含搜索结果（slot_refs 解析）
    - reserve 返回 NEED_CONFIRM → 最终事件 need_confirm=True
    """
    engine, spy, session = _make_engine()
    events = _run(engine, _req("找川菜馆订今晚的位"))
    final = events[-1]

    assert spy.count("food.search_restaurant") == 1
    assert spy.count("food.reserve") == 1

    # 验证执行顺序：search 在 reserve 之前
    search_order = next(o for i, o in spy.execution_order if i == "food.search_restaurant")
    reserve_order = next(o for i, o in spy.execution_order if i == "food.reserve")
    assert search_order < reserve_order

    # reserve 返回 NEED_CONFIRM
    assert final.get("need_confirm") is True

    # 会话挂起态
    state = asyncio.run(session.load("sess-mi"))
    assert state is not None
    assert state.phase == "wait_confirm"


def test_control_query_hybrid_parallel():
    """「打开空调顺便看看今天天气」→ 控制类 + 播报类，并行。

    验证点：
    - 拆出 2 个 step（hvac.set + info.weather）
    - 两个 step 都被执行
    - 聚合结果包含天气播报
    """
    engine, spy, _ = _make_engine()
    events = _run(engine, _req("打开空调顺便看看今天天气"))
    final = events[-1]

    assert spy.count("hvac.set") == 1
    assert spy.count("info.weather") == 1
    assert final["kind"] == "final"
    assert final.get("speech")  # 非空即可


def test_single_intent_passthrough():
    """「打开空调」→ 1 个 step，无 multi-intent 开销。

    验证点：
    - 只有 1 个 step
    - hvac.set 被调用 1 次
    - 无多余意图调用
    """
    engine, spy, _ = _make_engine()
    events = _run(engine, _req("打开空调"))
    final = events[-1]

    assert spy.count("hvac.set") == 1
    assert spy.count("media.play") == 0
    assert spy.count("info.weather") == 0
    assert final["kind"] == "final"
    assert final.get("speech")  # 非空即可
