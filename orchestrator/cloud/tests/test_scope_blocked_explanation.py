"""受限身份的**解释面**：没权限就说没权限（AR05 余项，2026-09-10 真栈实录立项）。

背景（`docs/design/2026-09-10-ar06-ar09-engineering-implementation.md` 附录 B）：
只带 `location.read` 的身份连问 6 次「导航去广州塔」，`actions` 6/6 为空——**安全面成立**；
但话术是 6 种，其中 #1「好的，已为你规划路线前往广州塔」（说了没做）、#4 把内部错误串
`unsupported datetime format` 原样吐给用户，**0/6** 提到真实原因。而真实原因服务端自己
知道：同一 token 查 `/api/session` 就写着 `navigation → unauthorized / scope_missing`。

根因不是话术不好，是**理由被丢掉了**：规划前按权限过滤 catalog（这是对的，越权能力不该
暴露给 LLM），过滤完理由没跟出来，LLM 只能凭空解释。

判据要点：

- 系统持有的事实不交给 LLM 答 ⇒ 命中时**一次 LLM 都不调**，话术与 issue 都由服务端出；
- 恢复出口指**能力设置**而不是系统权限页（业务 scope ≠ 设备权限，conventions §9）；
- **不冤枉**：路由分不清或压根不是那条能力时，行为与今天逐字一致；
- 没有被挡下的能力时（绝大多数身份）这条路一步都不走。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud import contracts
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.planning import PlanBuilder, _build_ref_maps
from orchestrator.cloud.session import SessionStore


def _cap(intent, slots=()):
    return SimpleNamespace(
        intent=intent, slots=list(slots), description=intent, examples=[],
        require_confirm=False, heavy=False, response_only=intent.startswith("chitchat"),
        whole_utterance=False, slot_shapes={}, verification=None)


def _agent(agent_id, caps, *, requires=(), display_name="", score=1.0):
    manifest = SimpleNamespace(
        agent_id=agent_id, trust_level="first_party", latency_budget_ms=2000,
        requires_permissions=list(requires), capabilities=list(caps),
        context_scopes=[], deployment="cloud", kind="agent", route_hints=[],
        edge_intents=[], display_name=display_name)
    return SimpleNamespace(manifest=manifest, endpoint="stub:1", score=score)


_NAV = _agent("navigation", [_cap("navigation.navigate_to", ["destination"])],
              requires=["navigation"], display_name="导航")
_CHITCHAT = _agent("chitchat", [_cap("chitchat.talk", ["text"])])
_ALL = [_NAV, _CHITCHAT]


class _Resp:
    def __init__(self, status=0, speech=""):
        self.status = status
        self.speech = speech
        self.follow_up = ""
        self.actions = []
        self.ui_card = None
        self.data = None
        self.missing_slots = []


class _Spy:
    """按脚本回放；`resolve_top` 决定语义路由 top-1（受限判据要用它）。"""

    def __init__(self, plan_texts=(), resolve_top=None):
        self.plan_texts = list(plan_texts)
        self.plan_calls = 0
        self.resolve_calls: list[str] = []
        self.calls: list[str] = []
        self._resolve_top = resolve_top

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append(intent)
        return _Resp(speech="好的。")

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            self.plan_calls += 1
            return self.plan_texts.pop(0) if self.plan_texts else "not a plan"
        return "汇总话术"

    async def resolve(self, query="", intent="", top_k=1):
        self.resolve_calls.append(query)
        if self._resolve_top is not None:
            return list(self._resolve_top)
        return list(_ALL)

    async def list_agents(self):
        return list(_ALL)


def _engine(spy):
    return PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=SessionStore(redis_url=""),
    )


def _req(text, *, scopes="location.read", session_id="s-scope"):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="req-scope",
        is_confirmation=False, operation_id="",
        meta={"granted_scopes": scopes},
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _run(engine, req):
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


def _final(events):
    finals = [e for e in events if e.get("kind") == "final"]
    assert finals, f"没有 final 事件：{events}"
    return finals[-1]


# ─── 命中：说出真实原因 ─────────────────────────────────────────────────────

def test_scope_blocked_capability_is_named_as_the_real_reason():
    """受限身份要一条没授权的能力 ⇒ final 带 permission.scope_missing 并点名那条能力。"""
    spy = _Spy(resolve_top=[_NAV])
    final = _final(_run(_engine(spy), _req("导航去广州塔")))

    issues = final.get("issues") or []
    assert [i["code"] for i in issues] == [contracts.ISSUE_PERMISSION_SCOPE_MISSING]
    assert issues[0]["affected_capabilities"] == ["navigation"]
    assert issues[0]["severity"] == contracts.SEVERITY_WARNING
    assert "导航" in issues[0]["message"]


def test_scope_blocked_speech_says_no_permission_and_never_claims_execution():
    """话术必须说清「没有授权」且**明说没有去做**——不许再出现「已为你规划路线」。"""
    spy = _Spy(resolve_top=[_NAV])
    events = _run(_engine(spy), _req("导航去广州塔"))
    final = _final(events)

    speech = final.get("speech") or ""
    assert "授权" in speech and "导航" in speech
    assert "没有去做" in speech
    assert not (final.get("actions") or []), "零动作是安全面，必须仍然成立"
    assert not spy.calls, "没有任何 Agent 被调用"


def test_scope_blocked_never_asks_the_llm_to_explain():
    """理由是系统持有的事实 ⇒ 一次 LLM 都不调（同一句话不会再有 6 种说法）。"""
    spy = _Spy(resolve_top=[_NAV])
    _run(_engine(spy), _req("导航去广州塔"))

    assert spy.plan_calls == 0


def test_scope_blocked_recovery_points_at_capabilities_not_system_settings():
    """业务 scope 不足的出口是能力设置；指系统权限页会让用户去关一个不存在的开关。"""
    spy = _Spy(resolve_top=[_NAV])
    recovery = (_final(_run(_engine(spy), _req("导航去广州塔")))["issues"][0]["recovery"])

    assert [r["kind"] for r in recovery] == [contracts.RECOVERY_OPEN_CAPABILITY_SETTINGS]
    assert contracts.RECOVERY_OPEN_VOICE_SETTINGS not in [r["kind"] for r in recovery]


# ─── 不冤枉：分不清就退回今天的行为 ────────────────────────────────────────

def test_low_confidence_routing_does_not_blame_permissions():
    """语义 top-1 低于既有门槛 ⇒ 不下「你没权限」这个结论，照走今天的路。"""
    weak = _agent("navigation", list(_NAV.manifest.capabilities),
                  requires=["navigation"], display_name="导航", score=0.2)
    spy = _Spy(resolve_top=[weak])
    final = _final(_run(_engine(spy), _req("随便聊两句")))

    codes = [i["code"] for i in (final.get("issues") or [])]
    assert contracts.ISSUE_PERMISSION_SCOPE_MISSING not in codes
    assert spy.plan_calls >= 1, "没命中就该照常规划"


def test_request_for_an_allowed_capability_is_untouched():
    """要的是**有权限**的那条能力 ⇒ 与今天逐字一致，不产生 scope 问题。"""
    spy = _Spy(
        plan_texts=[json.dumps({"steps": [
            {"id": "s1",
             "capability_ref": _build_ref_maps([_CHITCHAT])[1][("chitchat", "chitchat.talk")],
             "slots": {"text": "你好"}, "depends_on": [], "slot_refs": {}}]})],
        resolve_top=[_CHITCHAT])
    final = _final(_run(_engine(spy), _req("你好")))

    codes = [i["code"] for i in (final.get("issues") or [])]
    assert contracts.ISSUE_PERMISSION_SCOPE_MISSING not in codes
    assert spy.plan_calls >= 1


def test_fully_authorized_identity_pays_nothing():
    """全权限身份没有被挡下的能力 ⇒ 这条判据一步都不走（不多发一次语义路由）。"""
    spy = _Spy(
        plan_texts=[json.dumps({"steps": [
            {"id": "s1",
             "capability_ref": _build_ref_maps(_ALL)[1][("navigation", "navigation.navigate_to")],
             "slots": {"destination": "广州塔"}, "depends_on": [], "slot_refs": {}}]})],
        resolve_top=[_NAV])
    final = _final(_run(_engine(spy), _req("导航去广州塔", scopes="navigation,location.read")))

    codes = [i["code"] for i in (final.get("issues") or [])]
    assert contracts.ISSUE_PERMISSION_SCOPE_MISSING not in codes
    assert "navigation.navigate_to" in spy.calls, "有权限就该真的执行"


# ─── 判据单元：过滤时把理由留在手里 ───────────────────────────────────────

def test_partition_keeps_the_blocked_half_as_the_reason():
    allowed, blocked = PlanBuilder._partition_by_permission(_ALL, ["location.read"])

    assert [a.manifest.agent_id for a in allowed] == ["chitchat"]
    assert [a.manifest.agent_id for a in blocked] == ["navigation"]


def test_partition_without_permission_system_blocks_nothing():
    """granted=None（权限系统未启用）时行为与今天一致，且没有「被挡下」这一半。"""
    allowed, blocked = PlanBuilder._partition_by_permission(_ALL, None)

    assert len(allowed) == len(_ALL) and blocked == []


def test_scope_blocked_target_needs_a_blocked_set():
    """没有被挡下的能力时不发路由请求——正常身份零代价。"""
    spy = _Spy(resolve_top=[_NAV])
    builder = PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve)

    assert asyncio.run(builder._scope_blocked_target("导航去广州塔", [])) is None
    assert spy.resolve_calls == []
