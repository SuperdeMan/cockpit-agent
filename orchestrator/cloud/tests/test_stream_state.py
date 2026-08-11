"""D0/T2 流式判定统一（B5 §4）。

三组断言：
1. **纯函数全分支**——判定小到做得到 100% 分支覆盖，B5 §5 把它列为验收判据；
2. **源码级「只有一份判定」**——本批的价值不是「这次写对了」，而是「以后只有一处
   可以写错」。局部布尔再冒出来，这条会红；
3. **统一后的两处行为变化**（都是把 D0 拉到 T2 已有的口径上，各自单独取证）。
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import PlanContext, Step, StepStatus
from orchestrator.cloud.stream_state import (
    StreamAttempt, StreamTracker, allow_unary_fallback, emitted_anything,
    outcome_uncertain,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


def _src(*parts: str) -> str:
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ── 1. 纯函数全分支 ────────────────────────────────────────────────────────

def test_tracker_starts_with_no_output():
    assert StreamTracker().state is StreamAttempt.NO_OUTPUT


def test_empty_speech_delta_is_not_output():
    """空 delta 不算流出——否则一个空串就能把 unary 回退整条关掉。"""
    tracker = StreamTracker()
    tracker.on_speech("")
    assert tracker.state is StreamAttempt.NO_OUTPUT
    assert tracker.spoke is False


def test_speech_then_action_reports_action_and_keeps_spoke():
    """`state` 是决策量、`spoke` 是记账量——话术+动作并发时两者都要还原得出来。

    只留 `state` 会漏掉「话术确实播过」，T2 的挂起前缀就会复读一遍。
    """
    tracker = StreamTracker()
    tracker.on_speech("正在为您")
    tracker.on_action()

    assert tracker.state is StreamAttempt.ACTION_EMITTED
    assert tracker.spoke is True


def test_action_then_speech_does_not_downgrade_state():
    """状态按「不可撤回程度」取，不按到达顺序——动作发出后再播话术仍是 ACTION_EMITTED。"""
    tracker = StreamTracker()
    tracker.on_action()
    tracker.on_speech("已为您打开")

    assert tracker.state is StreamAttempt.ACTION_EMITTED


def test_allow_unary_fallback_only_on_no_output():
    assert allow_unary_fallback(StreamAttempt.NO_OUTPUT) is True
    assert allow_unary_fallback(StreamAttempt.SPEECH_EMITTED) is False
    assert allow_unary_fallback(StreamAttempt.ACTION_EMITTED) is False


def test_emitted_anything_is_the_exact_complement():
    """两个判定按取反定义，不可能漂移。"""
    for state in StreamAttempt:
        assert emitted_anything(state) is not allow_unary_fallback(state)


def test_outcome_uncertain_only_for_action_without_final():
    for state in StreamAttempt:
        assert outcome_uncertain(state, True) is False, state
    assert outcome_uncertain(StreamAttempt.NO_OUTPUT, False) is False
    assert outcome_uncertain(StreamAttempt.SPEECH_EMITTED, False) is False
    assert outcome_uncertain(StreamAttempt.ACTION_EMITTED, False) is True


def test_on_final_flag():
    tracker = StreamTracker()
    assert tracker.got_final is False
    tracker.on_final()
    assert tracker.got_final is True


# ── 2. 源码级：判定只有一份 ────────────────────────────────────────────────

def test_stream_paths_hold_no_private_emission_booleans():
    """D0/T2 不得再各自维护「流出过没有」的局部布尔。

    B1 修的那个 bug（`elif streamed:` 永不可达）不是手滑，是同一张判定表被抄了
    两份的必然结果。行为测试只能证明「今天两份是一致的」，这条能挡住**下一次**
    有人图省事在某一侧加回一个局部布尔。
    """
    for module in ("engine.py", "loop.py"):
        src = _src("orchestrator", "cloud", module)
        for banned in ("streamed =", "did_speak", "did_action"):
            assert banned not in src, (
                f"{module} 里出现 `{banned}`——流出面判定必须走 stream_state，"
                "不许再各写一份")


def test_both_stream_paths_import_the_shared_judgment():
    for module in ("engine.py", "loop.py"):
        assert "stream_state import" in _src("orchestrator", "cloud", module), module


# ── 3. readback：三档话术 + 指纹（B5 §4.2，还 B1 留的账）────────────────────

class _Mirror:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


def _control_step(verification=None):
    return Step(id="s1", agent_id="vehicle", kind="agent", deployment="cloud",
                intent="hvac.on", slots={"temp": 22},
                verification=verification or {})


def _readback(step, mirror=None):
    executor = DagExecutor(call_agent_fn=_unused, state_mirror=mirror)
    return asyncio.run(executor.stream_uncertain_result(step, PlanContext()))


async def _unused(*_args, **_kwargs):      # pragma: no cover - 只为满足构造契约
    raise AssertionError("readback 不该调 Agent")


_STATE_MATCH = {"mode": "state_match", "expect": {"keys": {"hvac_on": True}},
                "timeout_ms": 10}


def test_readback_without_declaration_keeps_the_honest_default():
    """没声明 state_match → 维持「结果暂时无法确认」。

    **不许拿 schema 模式凑数**：schema 验的是本次响应的内容，而响应根本没回来。
    """
    result = _readback(_control_step())

    assert result.status == StepStatus.OK
    assert "无法确认" in result.speech
    assert result.data["_outcome_uncertain"] is True
    assert "_verify" not in result.data


def test_readback_schema_mode_is_not_accepted():
    result = _readback(_control_step({"mode": "schema",
                                      "expect": {"data_keys": ["temp"]}}))

    assert "无法确认" in result.speech
    assert "_verify" not in result.data


def test_readback_sat_says_it_took_effect():
    result = _readback(_control_step(_STATE_MATCH), _Mirror({"hvac_on": True}))

    assert "已经生效" in result.speech
    assert result.data["_verify"]["verdict"] == "sat"
    assert result.data["_verify"]["exec"] == "stream_lost_final"


def test_readback_unsat_defers_the_note_to_the_aggregator():
    """UNSAT 只说前半句——后半句由聚合器统一补，同一句话不写第二遍。"""
    from orchestrator.cloud.aggregator import Aggregator

    result = _readback(_control_step(_STATE_MATCH), _Mirror({"hvac_on": False}))
    assert result.data["_verify"]["verdict"] == "unsat"

    composed = asyncio.run(Aggregator(llm_fn=_unused).compose("开空调", [result]))
    assert composed["speech"].startswith("操作指令已发出。")
    assert "没确认到这个操作真的生效" in composed["speech"]


def test_readback_unknown_mirror_does_not_convict():
    """镜像读不到 = 「我看不见」，不是「没做成」——回到默认话术，不报未生效。"""
    result = _readback(_control_step(_STATE_MATCH), _Mirror({}))

    assert "无法确认" in result.speech
    assert "_verify" not in result.data


def test_readback_result_carries_the_dedup_fingerprint():
    """指纹是本条的第二个理由：`_exec_step` 早就给 step_timeout 打指纹了
    （「副作用可能已发生，不打指纹 replan 重出同一动作会被原样重发」），
    而 B1 合成的那份不确定结果没有——流断丢 final 与超时是同一种处境。"""
    step = _control_step()
    result = _readback(step)

    assert result.fingerprint == DagExecutor._fingerprint(step)
    assert result.fingerprint


def test_readback_never_invents_domain_vocabulary():
    """三档话术都不许出现领域词——编排核心零领域字面量（同 M-C 的口径）。"""
    src = _src("orchestrator", "cloud", "executor.py")
    head = src.split("class DagExecutor")[0]
    for banned in ("空调", "后备箱", "车窗", "座椅", "导航"):
        assert banned not in head, f"executor 话术常量出现领域词 `{banned}`"


# ── 4. D0 统一后的两处行为变化（各自取证）────────────────────────────────

_SINGLE_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": "cap_0001", "slots": {}, "depends_on": [],
     "slot_refs": {}},
]})


class _Cap:
    def __init__(self, intent):
        self.intent, self.slots, self.description = intent, [], intent


def _chitchat_agent():
    manifest = SimpleNamespace(
        agent_id="chitchat", trust_level="first_party", latency_budget_ms=2000,
        requires_permissions=[], capabilities=[_Cap("chitchat.talk")],
    )
    return SimpleNamespace(manifest=manifest, endpoint="stub:50062")


class _Resp:
    def __init__(self, status=0, speech="", follow_up=""):
        self.status, self.speech, self.follow_up = status, speech, follow_up
        self.actions, self.ui_card, self.data, self.missing_slots = [], None, None, []


class _Spy:
    def __init__(self, script):
        self.script = script
        self.stream_calls, self.unary_calls = [], []

    async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None):
        self.stream_calls.append(intent)
        for item in self.script:
            yield item

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None):
        self.unary_calls.append(intent)
        return _Resp(speech="（unary 兜底回复）")

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            return _SINGLE_PLAN
        return "（聚合话术）"

    async def resolve(self, query="", intent="", top_k=1):
        return [_chitchat_agent()]

    async def list_agents(self):
        return [_chitchat_agent()]


def _run_d0(script):
    from orchestrator.cloud.aggregator import Aggregator
    from orchestrator.cloud.engine import PlannerEngine
    from orchestrator.cloud.planning import PlanBuilder
    from orchestrator.cloud.session import SessionStore

    spy = _Spy(script)
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=SessionStore(redis_url=""),
    )
    req = SimpleNamespace(
        text="随便说点什么", session_id="sess-b5", request_id="r1",
        is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"))

    async def collect():
        return [e async for e in engine.run(req)]
    return spy, asyncio.run(collect())


def test_d0_action_then_lost_final_no_longer_invites_a_retry():
    """**行为变化 1**：D0 此前对「动作已发出、final 丢了」说「请再试一次」
    ——等于邀请用户把一个有副作用的动作发第二遍，正是 B1 在 T2 修掉的形态。
    统一后 D0 拿到同一档处置。"""
    spy, events = _run_d0([("action", {"type": "vehicle.control"})])

    final = events[-1]
    assert final["kind"] == "final"
    assert "再试一次" not in final["speech"], "还在邀请重发一个有副作用的动作"
    assert "无法确认" in final["speech"]
    assert not spy.unary_calls, "action 已发出还回退 unary——重复副作用"


def test_d0_speech_then_lost_final_keeps_its_wording():
    """对照：只流了话术那一档逐字不变（自进化的兜底话术模式还认它）。"""
    _spy, events = _run_d0([("speech", "为什么天空")])

    assert events[-1]["speech"] == "抱歉，刚才没说完，请再试一次。"


def test_d0_softened_empty_delta_falls_back_to_unary():
    """**行为变化 2**：softener 把悬空 `*` 扣下一拍时用户什么都没看到，
    D0 此前照样算「流出过输出」并放弃回退——一个空串就能关掉整条回退。
    T2 一直是严的那个口径（有专门的空 delta 对照测试），统一到严的这边。"""
    spy, events = _run_d0([("speech", "*")])

    assert spy.unary_calls, "软化后零输出仍没有回退 unary"
    assert events[-1]["speech"] == "（unary 兜底回复）"
