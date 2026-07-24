"""M0a-3 契约测试：capability 级 `require_confirm` 的中央强制落实（四条兜底契约）。

历史缺口（2026-07-24 评审核实）：manifest 声明了 require_confirm，但 `_validated_steps`
装配 Step 时从不读取——云路径确认全靠 Agent 自觉返回 NEED_CONFIRM，Agent 漏标无兜底。

权威链（设计稿 §4.A）：VAL/payment/Runtime > Capability Manifest > Plan Validator >
prompt 软层。契约：
- 确认权不在 LLM：`_validated_steps` 不读计划输出的 require_confirm（不可降级也不可升级）；
- Agent 漏标由 executor 兜底闸改判 NEED_CONFIRM 并扣下动作（副作用通道守住；Agent 内部
  副作用由 VAL/payment-gateway 各自硬层把守）；
- 下游（Agent/action）只可升级确认要求，不可被降级；
- confirmed 只解除追问、不开执行旁路（动作仍经 dispatch→VAL 硬层执行）。
"""
import asyncio
from types import SimpleNamespace

from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import Plan, Step, StepStatus
from orchestrator.cloud.planning import PlanBuilder


class _Cap:
    def __init__(self, intent, require_confirm=False):
        self.intent, self.description = intent, intent
        self.slots = []
        self.require_confirm = require_confirm
        self.heavy = False


def _agent_map(require_confirm):
    manifest = SimpleNamespace(
        agent_id="pay", trust_level="third_party", latency_budget_ms=2000,
        requires_permissions=[], capabilities=[_Cap("parking.pay", require_confirm)],
        kind="agent", deployment="cloud", context_scopes=[])
    return {"pay": SimpleNamespace(manifest=manifest, endpoint="stub:1")}


class _Resp:
    def __init__(self, status=0, speech="", actions=None):
        self.status, self.speech = status, speech
        self.actions = actions or []
        self.ui_card = None
        self.follow_up = ""
        self.data = None
        self.missing_slots = []


def _action(type_="payment.invoke", require_confirm=False):
    return SimpleNamespace(type=type_, payload=None, require_confirm=require_confirm)


def _run_single(step, resp):
    async def call(endpoint, intent, slots, ctx, meta):
        return resp

    ex = DagExecutor(call_agent_fn=call)

    async def run():
        return [r async for r in ex.run(Plan(steps=[step]), None)]

    return asyncio.run(run())


# ── T1 确认权不在 LLM：计划输出的 require_confirm 被忽略，以 manifest 为准 ──────
def test_llm_cannot_lower_or_raise_confirm_level():
    raw = [{"id": "s1", "agent_id": "pay", "intent": "parking.pay",
            "slots": {}, "require_confirm": False}]          # LLM 妄图降级 → 无效
    steps = PlanBuilder._validated_steps(raw, _agent_map(require_confirm=True))
    assert steps and steps[0].require_confirm is True

    raw = [{"id": "s1", "agent_id": "pay", "intent": "parking.pay",
            "slots": {}, "require_confirm": True}]           # LLM 妄图升级 → 也无效（升级权在 Agent/VAL）
    steps = PlanBuilder._validated_steps(raw, _agent_map(require_confirm=False))
    assert steps and steps[0].require_confirm is False


# ── T2 Agent 漏标 → manifest 兜底：OK+动作被改判 NEED_CONFIRM、动作扣下 ────────
def test_manifest_forces_confirm_when_agent_forgets():
    step = Step(id="s1", agent_id="pay", intent="parking.pay", require_confirm=True)
    r = _run_single(step, _Resp(status=0, speech="已为您支付8元",
                                actions=[_action()]))[0]
    assert r.status == StepStatus.NEED_CONFIRM
    assert r.actions == []                       # 副作用扣下，不派发
    assert "确认" in (r.speech + r.follow_up)


# ── T3 下游只可升级：Agent 的 NEED_CONFIRM / action.require_confirm 原样生效 ───
def test_downstream_can_raise_confirm_not_lowered():
    step = Step(id="s1", agent_id="pay", intent="parking.pay", require_confirm=False)
    r = _run_single(step, _Resp(status=1, speech="确认支付吗？"))[0]
    assert r.status == StepStatus.NEED_CONFIRM   # Agent 主动要确认，中央不干涉

    r = _run_single(step, _Resp(status=0, speech="好的",
                                actions=[_action(require_confirm=True)]))[0]
    assert r.status == StepStatus.OK
    assert r.actions[0]["require_confirm"] is True   # action 级确认要求透传（端侧再走确认）


# ── T4 confirmed 只解除追问、不开执行旁路：动作照常产出（仍经 dispatch→VAL） ───
def test_confirmed_releases_ask_but_not_execution_channel():
    step = Step(id="s1", agent_id="pay", intent="parking.pay", require_confirm=True,
                meta={"confirmed": "true"})
    r = _run_single(step, _Resp(status=0, speech="已支付", actions=[_action()]))[0]
    assert r.status == StepStatus.OK
    assert len(r.actions) == 1                   # 放行=走正常执行通道，不是绕过 VAL
