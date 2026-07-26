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


# ── T5 补槽恢复不注入 confirmed：补槽答案（「拿铁」）不是确认（验收 P0）─────────
def test_slot_fill_restore_does_not_inject_confirmed():
    """wait_slot 恢复若也注入 confirmed="true"，require_confirm 步会在用户从未见过
    金额/后果的情况下直接执行（「下单一杯咖啡」→「要点什么？」→「拿铁」→ 无确认下单）。
    补槽重跑后该步照常 NEED_CONFIRM，走第二次挂起等真正的确认。"""
    from orchestrator.cloud.engine import PlannerEngine
    from orchestrator.cloud.models import SessionState

    state = SessionState(
        phase="wait_slot", pending_step_id="s1", missing_slots=["item"],
        pending_plan={"steps": [{"id": "s1", "agent_id": "pay",
                                 "intent": "parking.pay", "slots": {},
                                 "require_confirm": True}]})
    plan, _ = PlannerEngine._restore(None, state, inject_confirmed=False)
    assert plan is not None
    assert (plan.steps[0].meta or {}).get("confirmed") != "true"

    # 对照：wait_confirm 恢复（用户明确说了「确认」）才注入
    plan2, _ = PlannerEngine._restore(None, state, inject_confirmed=True)
    assert (plan2.steps[0].meta or {}).get("confirmed") == "true"


# ── T6 两条流式直通都必须排除 require_confirm 步（engine D0 + loop T2）──────────
def test_both_streaming_fastpaths_exclude_require_confirm_steps():
    """流式直通把流中 action 直接放行到 HMI、final 不经 _enforce_capability_confirm。
    engine D0 落地时排除了 require_confirm 步；loop T2 同源路径曾漏（验收抓到）。
    断言两处流式条件块里都含该排除——按条件块定位，不做全文匹配（防注释凑数）。"""
    import inspect
    from orchestrator.cloud import engine as engine_mod
    from orchestrator.cloud import loop as loop_mod

    esrc = inspect.getsource(engine_mod.PlannerEngine)
    d0 = esrc[esrc.index('plan.complexity == "simple"'):]
    d0 = d0[:d0.index("):")]
    assert "require_confirm" in d0, "engine D0 流式直通丢失 require_confirm 排除"

    lsrc = inspect.getsource(loop_mod.LoopController)
    t2 = lsrc[lsrc.index("if (self._stream"):]
    t2 = t2[:t2.index("):")]
    assert "require_confirm" in t2, "loop T2 流式直通丢失 require_confirm 排除"


# ── T7 挂起恢复保留防抖指纹：跨确认/补槽窗口的副作用步防抖不得失效 ────────────────
def test_restore_whitelist_keeps_fingerprint():
    from orchestrator.cloud import engine as engine_mod
    assert "fingerprint" in engine_mod._RESULT_FIELDS, (
        "_RESULT_FIELDS 丢了 fingerprint——挂起恢复后 M2 防抖会对空串永不命中")
