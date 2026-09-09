"""AR05 结构化契约的服务端产出：确认策略、补槽请求、规划技术失败（F09）。

判据要点：

- 契约必须由**真实生产函数**产出后走 final 事件面，只塞一帧画廊样本不算闭环；
- 截止时刻是 SessionStore 落盘后的绝对时刻，客户端只读不续期；
- 摘要来自**已验证的挂起步骤**，不是 LLM 的 goal；
- F09 只改「非法计划且重试没有有效计划」这一支——合法空动作、拒识、澄清、
  重试成功、既有 salvage 五条路径必须逐条仍在（只验负例时缺陷会躲在没验的那半）。
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


def _cap(intent, slots=(), *, require_confirm=False, response_only=False,
         slot_shapes=None):
    return SimpleNamespace(
        intent=intent, slots=list(slots), description=intent, examples=[],
        require_confirm=require_confirm, heavy=False, response_only=response_only,
        whole_utterance=False, slot_shapes=dict(slot_shapes or {}),
        verification=None)


def _agent(agent_id, caps, *, trust="first_party", endpoint="stub:1"):
    manifest = SimpleNamespace(
        agent_id=agent_id, trust_level=trust, latency_budget_ms=2000,
        requires_permissions=[], capabilities=list(caps), context_scopes=[],
        deployment="cloud", kind="agent", route_hints=[], edge_intents=[])
    return SimpleNamespace(manifest=manifest, endpoint=endpoint, score=1.0)


_MERCHANT = _agent("merchant", [
    _cap("merchant.order", ["store", "item"], require_confirm=True),
    _cap("merchant.pick_store", ["store"], slot_shapes={"store": "item_name"}),
])
_CHITCHAT = _agent("chitchat", [_cap("chitchat.talk", ["text"], response_only=True)])


class _Resp:
    def __init__(self, status=0, speech="", follow_up="", ui_card=None,
                 data=None, missing_slots=()):
        self.status = status
        self.speech = speech
        self.follow_up = follow_up
        self.actions = []
        self.ui_card = ui_card
        self.data = data
        self.missing_slots = list(missing_slots)


class _Spy:
    """按脚本回放 LLM 计划与 Agent 结果。`plan_texts` 逐轮弹出。"""

    def __init__(self, plan_texts, responses):
        self.plan_texts = list(plan_texts)
        self.responses = responses
        self.calls: list[str] = []
        self.plan_calls = 0

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append(intent)
        maker = self.responses.get(intent)
        return maker(meta or {}) if maker else _Resp(status=3, speech="未知意图")

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            self.plan_calls += 1
            return (self.plan_texts.pop(0) if self.plan_texts
                    else self.plan_texts_last())
        return "汇总话术"

    def plan_texts_last(self):
        return "still not a plan"

    async def resolve(self, query="", intent="", top_k=1):
        return [_MERCHANT, _CHITCHAT]

    async def list_agents(self):
        return [_MERCHANT, _CHITCHAT]


def _engine(spy):
    return PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=SessionStore(redis_url=""),
    )


def _req(text, *, session_id="s-ar05", is_confirmation=False, prefs=None):
    meta = {"granted_scopes": "merchant.read,merchant.write"}
    meta.update(prefs or {})
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="req-ar05",
        is_confirmation=is_confirmation, operation_id="", meta=meta,
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


def _ref(agent_id: str, intent: str) -> str:
    """按**生产同一份映射**算 capability_ref，不写死 cap_0001——
    编号随 catalog 顺序变，写死就会在无关改动后静默失配、整条测试退化成走兜底。"""
    _, pair_to_ref = _build_ref_maps([_MERCHANT, _CHITCHAT])
    return pair_to_ref[(agent_id, intent)]


_ORDER_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": _ref("merchant", "merchant.order"),
     "slots": {"store": "望京店", "item": "拿铁"}, "depends_on": [], "slot_refs": {}}]})
_PICK_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": _ref("merchant", "merchant.pick_store"),
     "slots": {}, "depends_on": [], "slot_refs": {}}]})


# ─── 确认策略 ───────────────────────────────────────────────────────────────

def _confirm_spy():
    return _Spy([_ORDER_PLAN], {
        "merchant.order": lambda meta: (
            _Resp(speech="已下单。") if meta.get("confirmed") == "true"
            else _Resp(status=1, speech="确认在望京店下一杯拿铁吗？",
                       follow_up="说『确认』即可"))})


def test_need_confirm_final_carries_confirm_policy():
    spy = _confirm_spy()
    final = _final(_run(_engine(spy), _req("在望京店点一杯拿铁")))

    assert final["need_confirm"] is True
    policy = final["confirm_policy"]
    assert policy["operation_id"] == final["operation_id"], "策略必须归属到这条挂起"
    assert policy["risk"] == contracts.RISK_HIGH
    assert policy["reason_code"] == contracts.REASON_REQUIRE_CONFIRM
    assert policy["allowed_channels"] == list(contracts.DEFAULT_CHANNELS)


def test_confirm_policy_summary_comes_from_the_verified_step_not_the_model():
    """摘要要说清「正在确认什么」，而且出处是已验证的步骤而非模型话术。"""
    spy = _confirm_spy()
    policy = _final(_run(_engine(spy), _req("在望京店点一杯拿铁")))["confirm_policy"]

    assert policy["summary_source"] == contracts.SUMMARY_FROM_CAPABILITY
    assert "merchant.order" in policy["action_summary"]
    assert "望京店" in policy["action_summary"] and "拿铁" in policy["action_summary"]
    assert policy["object_summary"] == "merchant"
    assert policy["target_intent"] == "merchant.order"


def test_confirm_policy_deadline_is_the_persisted_absolute_time():
    """截止时刻取 SessionStore 落盘后的绝对时刻，且晚于服务端此刻。"""
    spy = _confirm_spy()
    engine = _engine(spy)
    final = _final(_run(engine, _req("在望京店点一杯拿铁")))
    policy = final["confirm_policy"]

    state = asyncio.run(engine.session.load(
        "s-ar05", owner_user_id="u1", operation_id=final["operation_id"]))
    assert state is not None
    assert policy["expires_at_ms"] == int(state.expires_at * 1000)
    assert policy["expires_at_ms"] > policy["server_now_ms"]


def test_plain_turn_has_no_confirm_policy_or_slot_request():
    """没挂起就一个契约字段都不该有——恒空消息会让客户端天天判空。

    刻意用**不声明 require_confirm** 的能力：`merchant.order` 声明了，
    Agent 就算回 OK 也会被 `_enforce_capability_confirm` 兜底闸改判 NEED_CONFIRM
    （那条闸本身是对的），拿它当"普通轮"测的是另一件事。
    """
    spy = _Spy([_PICK_PLAN], {
        "merchant.pick_store": lambda meta: _Resp(speech="已为您选好望京店。")})
    final = _final(_run(_engine(spy), _req("就望京店吧")))

    assert not final.get("need_confirm")
    assert "confirm_policy" not in final
    assert "slot_request" not in final


# ─── 补槽请求 ───────────────────────────────────────────────────────────────

def test_need_slot_final_carries_slot_request_with_shape_and_deadline():
    spy = _Spy([_PICK_PLAN], {
        "merchant.pick_store": lambda meta: _Resp(
            status=2, speech="要在哪家店？", follow_up="说个门店名",
            missing_slots=["store"])})
    engine = _engine(spy)
    final = _final(_run(engine, _req("帮我点一杯")))

    req = final["slot_request"]
    assert req["operation_id"] == final["operation_id"]
    assert req["slot"] == "store"
    assert req["remaining_slots"] == ["store"]
    assert req["state"] == contracts.STATE_ACTIVE
    # 形状来自 capability 声明（`slot_shapes`），不是客户端猜的
    assert req["shape"] == "item_name"
    assert req["prompt"] == "说个门店名"
    assert req["expires_at_ms"] > req["server_now_ms"]


def test_slot_suggestions_only_come_from_a_visible_choice_card():
    """建议值只能来自用户**真的看见了**的那份列表；否则留空给自由输入。"""
    def with_card(meta):
        return _Resp(status=2, speech="要在哪家店？", missing_slots=["store"],
                     ui_card={"type": "merchant_choices",
                              "items": [{"name": "望京店"}, {"name": "国贸店"}]})

    spy = _Spy([_PICK_PLAN], {"merchant.pick_store": with_card})
    final = _final(_run(_engine(spy), _req("帮我点一杯")))
    assert final["slot_request"]["suggestions"] == ["望京店", "国贸店"]

    def without_card(meta):
        return _Resp(status=2, speech="要在哪家店？", missing_slots=["store"],
                     data={"items": [{"name": "望京店"}]})

    spy2 = _Spy([_PICK_PLAN], {"merchant.pick_store": without_card})
    final2 = _final(_run(_engine(spy2), _req("帮我点一杯")))
    assert final2["slot_request"]["suggestions"] == [], (
        "没渲染成选择卡的东西用户一眼没见过，做成可点选项就是假选项")


# ─── F09：规划技术失败 ──────────────────────────────────────────────────────

_GARBAGE = "对不起，我不知道该怎么安排。"


def test_planner_technical_failure_is_reported_not_disguised_as_chitchat():
    """非法计划 + 重试仍无有效计划 ⇒ 诚实终态与恢复入口，不伪装成一次闲聊。"""
    spy = _Spy([_GARBAGE, _GARBAGE], {})
    final = _final(_run(_engine(spy), _req("把那个东西弄一下")))

    assert "chitchat.talk" not in spy.calls, "技术失败被当成闲聊执行了"
    codes = [i["code"] for i in final.get("issues", [])]
    assert codes == [contracts.ISSUE_PLANNER_TECHNICAL_FAILURE], codes
    issue = final["issues"][0]
    assert issue["severity"] == contracts.SEVERITY_ERROR
    assert [r["kind"] for r in issue["recovery"]] == [contracts.RECOVERY_RETRY_REQUEST]
    assert issue["request_id"] == "req-ar05"
    assert final["speech"], "技术失败也要有一句人话"


def test_planner_technical_failure_executes_nothing():
    spy = _Spy([_GARBAGE, _GARBAGE], {})
    _run(_engine(spy), _req("把那个东西弄一下"))
    assert spy.calls == [], f"技术失败轮不该调用任何 Agent：{spy.calls}"


def test_legal_no_action_plan_is_not_treated_as_technical_failure():
    """「空调先别关」这类合法空动作仍走兜底 Agent 应答，不许被误报成失败。"""
    no_action = json.dumps({"addressed": True, "steps": []})
    spy = _Spy([no_action, no_action], {
        "chitchat.talk": lambda meta: _Resp(speech="好的，保持现状。")})
    final = _final(_run(_engine(spy), _req("空调先别关")))

    assert spy.calls == ["chitchat.talk"]
    assert not final.get("issues")


def test_valid_plan_on_retry_is_not_treated_as_technical_failure():
    """第一次垃圾、第二次给出合法计划 ⇒ 正常执行，不留技术失败痕迹。"""
    spy = _Spy([_GARBAGE, _ORDER_PLAN], {
        "merchant.order": lambda meta: _Resp(speech="已下单。")})
    final = _final(_run(_engine(spy), _req("在望京店点一杯拿铁")))

    assert spy.calls == ["merchant.order"]
    assert not final.get("issues")


def test_empty_fallback_still_uses_the_existing_honest_degrade_speech():
    """兜底给不出任何步时，仍走既有「没听清」那条，一个字不变。"""
    class _NoFallback(_Spy):
        async def resolve(self, query="", intent="", top_k=1):
            return []

        async def list_agents(self):
            return [_MERCHANT]           # 没有 chitchat ⇒ 兜底拿不到可说话的步

    spy = _NoFallback([_GARBAGE, _GARBAGE], {})
    final = _final(_run(_engine(spy), _req("把那个东西弄一下")))

    assert final["speech"].startswith("抱歉，我没听清")
    assert not final.get("issues"), "空计划的诚实降级不是技术失败终态"


# ─── 换题后的 held（§4.3 / V04）────────────────────────────────────────────

_TALK_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": _ref("chitchat", "chitchat.talk"),
     "slots": {"text": "讲个笑话"}, "depends_on": [], "slot_refs": {}}]})


def _slot_then_topic_change_spy():
    return _Spy([_PICK_PLAN, _TALK_PLAN], {
        "merchant.pick_store": lambda meta: _Resp(
            status=2, speech="要在哪家店？", missing_slots=["store"]),
        "chitchat.talk": lambda meta: _Resp(speech="有个笑话是这样的。"),
    })


def test_topic_change_reports_the_held_operation_id():
    """用户换题、原任务仍有效 ⇒ final 必须**结构化**说出是哪一条被搁置了。

    话术里那句「对了，X 还在等你」是给人听的；客户端要撤下哪一条追问得有个 id。
    """
    spy = _slot_then_topic_change_spy()
    engine = _engine(spy)

    first = _final(_run(engine, _req("帮我点一杯")))
    held_id = first["operation_id"]
    assert first["slot_request"]["state"] == contracts.STATE_ACTIVE

    second = _final(_run(engine, _req("讲个笑话")))

    assert second.get("held_operation_ids") == [held_id], second
    # 软提醒仍在：两者同一处产出，不会一个说了一个忘了
    assert "还在等你" in (second.get("follow_up") or "")


def test_plain_turn_reports_no_held_operations():
    """没有挂起被搁置时不发这个键——恒空数组会让客户端每轮都判一次空。"""
    spy = _Spy([_ORDER_PLAN], {
        "merchant.order": lambda meta: _Resp(speech="已下单。")})
    final = _final(_run(_engine(spy), _req("在望京店点一杯拿铁")))

    assert "held_operation_ids" not in final
