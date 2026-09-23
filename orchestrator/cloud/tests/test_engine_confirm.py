"""PlannerEngine 多轮确认闭环测试（F1 回归）。

覆盖：确认完成下单且不重跑已完成步骤、取消、确认标记无挂起任务、
语音短肯定话术兜底、答非所问按新请求处理。
全部用进程内 stub，不依赖 gRPC/proto 生成代码。
"""
from __future__ import annotations
import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.session import SessionStore

_PLAN_JSON = json.dumps({
    "steps": [
        {"id": "s1", "capability_ref": "cap_0002",
         "slots": {"cuisine": "川菜"}, "depends_on": [], "slot_refs": {}},
        {"id": "s2", "capability_ref": "cap_0001",
         "slots": {"restaurant_name": "川菜·名店1", "datetime": "今晚7点", "party_size": "2"},
         "depends_on": ["s1"], "slot_refs": {}},
    ]
})

_AGG_SPEECH = "好的，已为您找到川菜·名店1并订好今晚7点两位。"


class _Cap:
    def __init__(self, intent, slots):
        self.intent, self.slots, self.description = intent, slots, intent


def _food_agent():
    manifest = SimpleNamespace(
        agent_id="nearby",
        trust_level="third_party",
        latency_budget_ms=2000,
        requires_permissions=[],
        capabilities=[
            _Cap("nearby.search", ["cuisine"]),
            _Cap("nearby.order", ["restaurant_name", "datetime", "party_size"]),
        ],
    )
    return SimpleNamespace(manifest=manifest, endpoint="stub:50063")


class _Resp:
    def __init__(self, status=0, speech="", follow_up="", ui_card=None,
                 data=None):
        self.status = status
        self.speech = speech
        self.follow_up = follow_up
        self.actions = []
        self.ui_card = ui_card
        self.data = data           # F3
        self.missing_slots = []    # F12


class _Spy:
    """记录每次 agent 调用的 (intent, meta)，并按脚本返回结果。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.llm_plan_calls = 0

    def count(self, intent: str) -> int:
        return sum(1 for i, _ in self.calls if i == intent)

    def metas(self, intent: str) -> list[dict]:
        return [m for i, m in self.calls if i == intent]

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append((intent, dict(meta or {})))
        if intent == "nearby.search":
            return _Resp(
                speech="为您找到 3 家川菜。",
                ui_card={"type": "place_list", "display_priority": 1,
                         "items": [{"name": "川菜·名店1"}]},
                data={"items": [{"name": "川菜·名店1"}]},
            )
        if intent == "nearby.order":
            if (meta or {}).get("confirmed") == "true":
                return _Resp(
                    speech="已为您订好：川菜·名店1 今晚7点 2位。",
                    ui_card={"type": "payment_qr", "payment_id": "pay-1"},
                )
            return _Resp(
                status=1,
                speech="确认为您预订川菜·名店1 今晚7点 2位吗？",
                follow_up="说『确认』即可下单",
                ui_card={"type": "mcp_order", "status": "confirm_pending"},
            )
        return _Resp(status=3, speech="未知意图")

    async def llm(self, messages, **kwargs):
        system = messages[0]["content"]
        if "任务编排器" in system:
            self.llm_plan_calls += 1
            return _PLAN_JSON
        return _AGG_SPEECH

    async def resolve(self, query="", intent="", top_k=1):
        return [_food_agent()]

    async def list_agents(self):
        return [_food_agent()]


def _make_engine() -> tuple[PlannerEngine, _Spy, SessionStore]:
    spy = _Spy()
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session,
    )
    return engine, spy, session


def _req(text: str, session_id: str = "sess-1", is_confirmation: bool = False,
         user_id: str = "u1", operation_id: str = ""):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="r1",
        is_confirmation=is_confirmation, operation_id=operation_id,
        context=SimpleNamespace(user_id=user_id, vehicle_id="v1"),
    )


def _run(engine, req) -> list[dict]:
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


# ─── 闭环主路径 ───

def test_confirm_completes_reservation_without_rerunning_done_steps():
    engine, spy, session = _make_engine()

    # 第 1 轮：搜索 OK → 预订挂起 NEED_CONFIRM
    events = _run(engine, _req("找家川菜馆订今晚7点两位"))
    final = events[-1]
    assert final["need_confirm"] is True
    assert spy.count("nearby.search") == 1
    assert spy.count("nearby.order") == 1
    state = asyncio.run(session.load("sess-1", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_confirm"
    assert state.pending_step_id == "s2"
    assert state.owner_user_id == "u1"
    # The pending merchant step is re-run from pending_plan after confirmation.
    # Persisting its card/data as well would duplicate checkout tokens, store,
    # specification and amount data in planner:sess:* for no restore purpose.
    assert "s2" not in state.completed_results
    # The completed dependency marker remains so it is not re-run.  This plan
    # uses a literal restaurant_name (no slot_refs), therefore none of the
    # provider response payload is required in the pending session.
    assert state.completed_results["s1"]["data"] == {}

    # 第 2 轮：HMI 确认按钮（is_confirmation=true）
    events = _run(engine, _req("确认", is_confirmation=True))
    final = events[-1]

    # 已完成的搜索步骤不重跑；挂起步骤带 confirmed 重跑并完成
    assert spy.count("nearby.search") == 1
    assert spy.count("nearby.order") == 2
    assert spy.metas("nearby.order")[-1].get("confirmed") == "true"
    assert not final.get("need_confirm")
    assert final["speech"] == _AGG_SPEECH
    # 会话清理，确认不可重放
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None


def test_other_owner_cannot_resume_or_cancel_pending_session_id():
    engine, spy, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))

    foreign = _run(engine, _req(
        "确认", is_confirmation=True, user_id="u2"))[-1]

    assert "没有待确认" in foreign["speech"]
    assert spy.count("nearby.order") == 1
    assert asyncio.run(session.load(
        "sess-1", owner_user_id="u1")) is not None


def test_confirm_result_card_replaces_restored_dependency_card():
    """确认恢复时，上轮 nearby 发现列表只是依赖数据，不是本轮新结果。

    后续业务步已成功产出支付/订单卡时，旧 ``place_list`` 不得再参与
    本轮卡片择优；否则其 ``display_priority=1`` 会永久压住默认优先级的
    ``payment_qr``，真实订单虽已创建，HMI 却仍只看到门店列表。
    """
    engine, _, _ = _make_engine()

    first = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]
    assert first["ui_card"]["type"] == "mcp_order"

    confirmed = _run(engine, _req("确认", is_confirmation=True))[-1]
    assert confirmed["ui_card"]["type"] == "payment_qr"


def test_cancel_clears_pending_and_does_not_execute():
    engine, spy, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))

    events = _run(engine, _req("取消", is_confirmation=True))
    final = events[-1]
    assert "取消" in final["speech"]
    assert spy.count("nearby.order") == 1          # 没有再执行
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None


def test_confirm_flag_without_pending_session():
    engine, spy, _ = _make_engine()
    events = _run(engine, _req("确认", is_confirmation=True))
    assert "没有待确认" in events[-1]["speech"]
    assert spy.llm_plan_calls == 0                  # 不会拿"确认"二字去规划


def test_bare_confirm_word_without_flag_or_pending_not_replanned():
    """裸"确认"（无 is_confirmation 标记，也无挂起任务）绝不下交 Planner。

    回归：挂起任务丢失（TTL/上一步异常）后，"确认"曾被借历史重规划成上一意图的重复执行
    （反复 trip.modify），表现为"确认后又改一遍并再次要确认"死循环。"""
    engine, spy, _ = _make_engine()
    events = _run(engine, _req("确认", is_confirmation=False))
    assert "没有待确认" in events[-1]["speech"]
    assert spy.llm_plan_calls == 0                  # 关键：不重规划
    # "取消"同样兜底
    events = _run(engine, _req("取消", is_confirmation=False))
    assert "没有待确认" in events[-1]["speech"]
    assert spy.llm_plan_calls == 0


def test_voice_short_yes_resumes_without_flag():
    """语音说"订吧"（无 is_confirmation 标记）也应续接挂起任务。"""
    engine, spy, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))

    events = _run(engine, _req("订吧"))
    final = events[-1]
    assert spy.metas("nearby.order")[-1].get("confirmed") == "true"
    assert not final.get("need_confirm")
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None


def test_unrelated_reply_treated_as_new_request():
    """挂起期间换话题：按新请求重新规划（R2 起挂起保留可回头续接，见下方 R2 组）。"""
    engine, spy, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    assert spy.llm_plan_calls == 1

    events = _run(engine, _req("附近有什么好玩的景点推荐一下"))
    final = events[-1]
    assert spy.llm_plan_calls == 2                  # 走了新规划
    assert "取消" not in final["speech"] and "过期" not in final["speech"]
    # 新规划重跑了搜索（确认续接则不会）
    assert spy.count("nearby.search") == 2


# ─── 确认话术判定 ───

def test_confirm_reply_rules():
    f = PlannerEngine._confirm_reply
    assert f("取消", True) == "no"                  # 否定优先于显式标记
    assert f("确认", True) == "yes"
    assert f("订吧", False) == "yes"                # 语音短肯定
    assert f("好的", False) == "yes"
    assert f("行", False) == "yes"                  # 单字肯定（占据整句）
    assert f("帮我看看附近有什么充电站好吗", False) is None   # 长句不误判
    assert f("", False) is None
    # 回归：肯定/否定词作子串出现在更长的指令里，绝不能误判成确认/取消
    assert f("第二天行程换一个", False) is None      # 含"行"(行程)，是修改不是确认
    assert f("可以换第二天的安排吗", False) is None   # 含"可以"，是请求不是确认
    assert f("第二天不要去长城了", False) is None     # 含"不要"，是修改不是取消
    # 评审 F01（2026-09-19）：问句形态**永远不是授权**——判据复用 runtime.question_shape
    for ask in ("确认吗", "可以吗", "行吗", "行不行", "好吗？"):
        assert f(ask, False) is None, ask
    assert f("请确认", False) == "yes"                # 礼貌祈使仍是指令（question_shape 的 marker）
    assert f("确认", True) == "yes"                  # HMI 按钮仍是显式寻址


def test_modify_phrase_with_xing_not_mistaken_for_confirm():
    """『第二天行程换一个』含"行"字，不得被当成确认而恢复上一行程并收尾。

    回归：用户报告改第二天没被识别、直接进了最终导航——根因是"行程"里的"行"误命中肯定词。"""
    engine, spy, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))      # 制造一个待确认任务
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is not None

    _run(engine, _req("第二天行程换一个"))            # 不是确认 → 应换新规划
    assert spy.llm_plan_calls == 2                   # 走了新规划（而非恢复挂起收尾）
    assert spy.count("nearby.search") == 2  # 新规划重跑了搜索（确认续接则不会）
    # 若被误判成确认，会用 confirmed 续接挂起的订餐那一步
    assert all(m.get("confirmed") != "true" for m in spy.metas("nearby.order"))


# ─── R2 中断-恢复（Q1 口径：插话不清除挂起，TTL 内可回头续接）───
#
# 插话轮必须用**不会自己挂起**的单步计划（搜索即完成），否则 stub planner 恒回两步
# 计划会让插话轮再次 NEED_CONFIRM、单槽覆盖旧挂起——测试会"因错误的理由通过"。

_SEARCH_ONLY_PLAN = json.dumps({
    "steps": [{"id": "s1", "capability_ref": "cap_0002",
               "slots": {"cuisine": "景点"}, "depends_on": [],
               "slot_refs": {}}]
})


def _make_engine_interject() -> tuple[PlannerEngine, "_Spy", SessionStore]:
    """同 _make_engine，但含「景点」的规划请求返回单步计划（插话轮不挂起）。"""
    spy = _Spy()
    orig_llm = spy.llm

    async def llm(messages, **kwargs):
        blob = json.dumps([m.get("content", "") for m in messages], ensure_ascii=False)
        if "任务编排器" in (messages[0].get("content") or "") and "景点" in blob:
            spy.llm_plan_calls += 1
            return _SEARCH_ONLY_PLAN
        return await orig_llm(messages, **kwargs)

    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=llm),
        session=session,
    )
    return engine, spy, session


def test_interjection_keeps_pending_and_confirm_resumes():
    """R2（旅程 B2-1）：确认挂起中插一句别的（插话轮自身不挂起）→ 挂起保留 +
    final 带软提醒；回头「确认」仍能续接完成。原实现插话即清挂起，回头确认
    只得到「当前没有待确认的操作」。"""
    engine, spy, session = _make_engine_interject()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    state0 = asyncio.run(session.load("sess-1", owner_user_id="u1"))
    assert state0 is not None and state0.phase == "wait_confirm"

    events = _run(engine, _req("帮我看看附近有什么景点"))
    final = events[-1]
    assert not final.get("need_confirm")                         # 插话轮正常完成
    state1 = asyncio.run(session.load("sess-1", owner_user_id="u1"))
    assert state1 is not None and state1.phase == "wait_confirm"  # 旧挂起原样保留
    assert state1.pending_step_id == state0.pending_step_id
    assert "等你确认" in (final.get("follow_up") or "")            # 软提醒

    events = _run(engine, _req("确认", is_confirmation=True))
    final = events[-1]
    assert spy.metas("nearby.order")[-1].get("confirmed") == "true"
    assert not final.get("need_confirm")
    assert asyncio.run(session.load(
        "sess-1", owner_user_id="u1")) is None           # 消费后才清


def test_interjection_cancel_still_cancels():
    """插话后说「取消」仍取消挂起（保留不等于永生）。"""
    engine, spy, session = _make_engine_interject()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    _run(engine, _req("帮我看看附近有什么景点"))
    events = _run(engine, _req("取消", is_confirmation=True))
    assert "取消" in events[-1]["speech"]
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None
    assert all(m.get("confirmed") != "true" for m in spy.metas("nearby.order"))


def test_unaddressed_confirm_with_two_confirm_pendings_asks_instead_of_guessing():
    """无寻址键的「确认」撞上**两条** `wait_confirm` ⇒ 问一次，零动作、零关闭（评审 F01 / W01）。

    ⚠ 这条测试的前身 `test_unaddressed_confirm_resolves_to_the_newest_pending`
    把「静默落最新一条」钉成了契约——而那正是评审点名的缺陷：用户脑子里惦记着
    两件事，系统替他猜了一件去执行。裸「确认」在两条挂起并存时**没有唯一所指**，
    唯一诚实的做法是把两条都念出来让他选；点选走 `operation_id`，语音走「确认+点名」。
    """
    engine, spy, session = _make_engine()
    op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
    op2 = _run(engine, _req("再找一家川菜馆订明晚8点三位"))[-1]["operation_id"]
    orders_before = spy.count("nearby.order")

    events = _run(engine, _req("确认", is_confirmation=True))
    final = events[-1]
    assert spy.count("nearby.order") == orders_before          # 一条都没执行
    assert all(m.get("confirmed") != "true" for m in spy.metas("nearby.order"))
    assert "还是" in final["speech"] and "确认" in final["speech"]   # 问了一次
    assert not final.get("actions")
    assert not final.get("closed_operation_ids")               # 两条都还活着
    assert set(final.get("held_operation_ids") or []) == {op1, op2}
    left = {s.operation_id for s in
            asyncio.run(session.load_all("sess-1", owner_user_id="u1"))}
    assert left == {op1, op2}
    assert spy.llm_plan_calls == 2                              # 没拿「确认」去规划


def test_unaddressed_confirm_targets_the_only_confirm_pending_even_if_a_slot_ask_is_newer():
    """恰好一条 `wait_confirm` + 更新的一条 `wait_slot` ⇒ 裸「确认」打给那条 wait_confirm。

    旧实现按「最近一条」寻址 ⇒ 落到 wait_slot 分支，「确认」二字被当槽值填进 `time_text`
    ——确认与补槽混在一起（评审 W01「确认与补槽不混」）。
    """
    from orchestrator.cloud.models import SessionState
    engine, spy, session = _make_engine()
    op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_slot", owner_user_id="u1", operation_id="op-slot",
        pending_step_id="s1", missing_slots=["time_text"],
        completed_results={}, pending_plan={"goal": "创建吃药提醒"})))

    final = _run(engine, _req("确认"))[-1]
    assert spy.metas("nearby.order")[-1].get("confirmed") == "true"
    assert op1 in (final.get("closed_operation_ids") or [])
    left = [s.operation_id for s in
            asyncio.run(session.load_all("sess-1", owner_user_id="u1"))]
    assert left == ["op-slot"]                                  # 补槽挂起原样活着


def test_bare_confirm_word_is_never_a_slot_answer():
    """只有 `wait_slot` 挂起时说「确认」：不是槽值，也不是授权——走「没有待确认」出口，
    并提醒那条补槽还在等。"""
    from orchestrator.cloud.models import SessionState
    engine, spy, session = _make_engine()
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_slot", owner_user_id="u1", operation_id="op-slot",
        pending_step_id="s1", missing_slots=["time_text"],
        completed_results={}, pending_plan={"goal": "创建吃药提醒"})))

    final = _run(engine, _req("确认"))[-1]
    assert "没有待确认" in final["speech"]
    assert spy.llm_plan_calls == 0
    assert "创建吃药提醒" in (final.get("follow_up") or "")
    state = asyncio.run(session.load("sess-1", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_slot"


def test_question_shaped_yes_word_does_not_authorize():
    """「可以吗 / 确认吗 / 行吗」是在**问**，不是在授权（评审 F01 局部复算）。

    每问一条都起一份新引擎：stub planner 对任何非确认句都会再挂一条，三问之后
    挂起表（容量 3）会把 op1 淘汰——那是容量语义，不是本条要验的东西。
    """
    for ask in ("可以吗", "确认吗", "行吗", "确认订单吗"):
        engine, spy, session = _make_engine()
        op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
        final = _run(engine, _req(ask))[-1]
        assert all(m.get("confirmed") != "true" for m in spy.metas("nearby.order")), ask
        assert op1 not in (final.get("closed_operation_ids") or []), ask
        # 原挂起没被消费掉（问句按插话处理，R2 保留挂起）
        assert asyncio.run(session.load(
            "sess-1", owner_user_id="u1", operation_id=op1)) is not None, ask


def test_a_bare_confirm_question_is_answered_deterministically_not_by_the_model():
    """「可以吗」「确认吗」「行不行」挂着一条待确认时：系统自己念出挂着什么、怎么确认。

    真栈 2026-09-19 CF7 第 1 次取样：这句交给规划落 chitchat，答「可以，已为您执行」——
    零动作却声称做了。系统持有的事实不交给模型答（同 `system.pending_state` 那条出口）。
    """
    for ask in ("可以吗", "确认吗", "行不行", "可不可以？"):
        engine, spy, session = _make_engine()
        op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
        plans_before = spy.llm_plan_calls
        final = _run(engine, _req(ask))[-1]
        assert spy.llm_plan_calls == plans_before, ask          # 零 LLM
        assert "待确认的操作" in final["speech"] and "说「确认」" in final["speech"], ask
        assert not final.get("actions"), ask
        assert final.get("held_operation_ids") == [op1], ask
        assert asyncio.run(session.load(
            "sess-1", owner_user_id="u1", operation_id=op1)) is not None, ask


def test_a_confirm_question_with_extra_content_still_goes_to_the_planner():
    """「可以换第二天的安排吗」不是裸确认询问——照旧交给规划。"""
    engine, spy, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    plans_before = spy.llm_plan_calls
    _run(engine, _req("可以换第二天的安排吗"))
    assert spy.llm_plan_calls == plans_before + 1


def test_a_bare_confirm_question_without_any_pending_is_not_hijacked():
    engine, spy, _ = _make_engine()
    _run(engine, _req("可以吗"))
    assert spy.llm_plan_calls == 1


def test_a_slot_answer_that_starts_with_a_yes_word_is_still_a_slot_answer():
    """「好的，明天早上八点」回答的是补槽追问——没有任何 wait_confirm 时，肯定词开头
    的长句不进确认寻址（否则会被判成「点名落空」而当插话重规划）。"""
    from orchestrator.cloud.models import SessionState
    engine, spy, session = _make_engine()
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_slot", owner_user_id="u1", operation_id="op-slot",
        pending_step_id="s2", missing_slots=["datetime"],
        completed_results={}, pending_plan={
            "goal": "订川菜馆", "raw_text": "找家川菜馆订两位",
            "steps": [{"id": "s2", "agent_id": "nearby", "intent": "nearby.order",
                       "slots": {"restaurant_name": "川菜·名店1", "party_size": "2"},
                       "depends_on": [], "slot_refs": {}, "kind": "agent",
                       "deployment": "cloud", "endpoint": "stub:50063"}]})))
    plans_before = spy.llm_plan_calls
    _run(engine, _req("好的，明天早上八点"))
    assert spy.llm_plan_calls == plans_before                     # 没有重规划
    assert spy.metas("nearby.order"), "补槽答案应当续接挂起步"


def test_named_confirm_that_names_nothing_pending_is_not_an_authorization():
    """「确认订单」而挂着的是订餐（goal / 原话都不含「订单」）⇒ 点名落空，不执行、不关闭。

    旧判据「确认」在 4 字句里占据整句 ⇒ yes ⇒ 把挂着的那条执行了。
    """
    engine, spy, session = _make_engine()
    op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
    final = _run(engine, _req("确认订单"))[-1]
    assert all(m.get("confirmed") != "true" for m in spy.metas("nearby.order"))
    assert op1 not in (final.get("closed_operation_ids") or [])
    assert asyncio.run(session.load(
        "sess-1", owner_user_id="u1", operation_id=op1)) is not None


def test_spoken_confirm_can_name_one_of_two_pendings():
    """「确认明晚8点那个」——肯定词 + 点名了恰好一条挂起 ⇒ 打给它。

    ⚠ 评审三轮 R3-01 B（2026-09-23）起点名要过**裁决**：点名的「明晚8点」必须出现在那一步的已校验摘要
    （能力描述 + 槽值）里。本文件的替身计划对两句话都写死 `datetime=今晚7点`、能力描述是机器名 ⇒ 摘要为空，
    点名按新规则不能授权——那正是新规则要挡的形态（点的和要执行的不是一回事）。所以这里把两条挂起的摘要
    写成真实挂起会有的样子（`_suspend` 从步骤槽值生成），验的仍是「点名在两条之间选中那一条」。"""
    engine, spy, session = _make_engine()
    op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
    op2 = _run(engine, _req("再找一家川菜馆订明晚8点三位"))[-1]["operation_id"]
    for op, summary in ((op1, "预订餐厅（2，今晚7点，川菜·名店1）"),
                        (op2, "预订餐厅（3，明晚8点，川菜·名店1）")):
        state = asyncio.run(session.load("sess-1", owner_user_id="u1", operation_id=op))
        state.action_summary = summary
        asyncio.run(session.save("sess-1", state))

    final = _run(engine, _req("确认明晚8点那个"))[-1]
    assert spy.metas("nearby.order")[-1].get("confirmed") == "true"
    assert op2 in (final.get("closed_operation_ids") or [])
    left = [s.operation_id for s in
            asyncio.run(session.load_all("sess-1", owner_user_id="u1"))]
    assert left == [op1]


def test_slot_interjection_keeps_pending():
    """R2（旅程 B2-2 引擎层）：wait_slot 挂起中换话题（动词开头、插话轮不挂起）→
    挂起保留 + 软提醒「继续补充」。"""
    from orchestrator.cloud.models import SessionState
    engine, spy, session = _make_engine_interject()
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_slot", owner_user_id="u1",
        pending_step_id="s1", missing_slots=["time_text"],
        completed_results={}, pending_plan={"goal": "创建吃药提醒"})))

    events = _run(engine, _req("帮我看看附近有什么景点"))
    final = events[-1]
    state = asyncio.run(session.load("sess-1", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_slot"      # 挂起还在
    assert "继续补充" in (final.get("follow_up") or "")
    assert "创建吃药提醒" in (final.get("follow_up") or "")       # 软提醒点名 goal


def test_slot_pending_cancel_phrase_clears():
    """B5-1 黑洞修复①：wait_slot 挂起中「那个提醒不用了，取消吧」= 取消挂起
    （长句被 _confirm_reply 整句规则拦住，须语境内词表）。"""
    from orchestrator.cloud.models import SessionState
    engine, spy, session = _make_engine_interject()
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_slot", owner_user_id="u1",
        pending_step_id="s1", missing_slots=["time_text"],
        completed_results={}, pending_plan={"goal": "创建交周报提醒"})))
    events = _run(engine, _req("那个提醒不用了，取消吧"))
    assert "取消" in events[-1]["speech"]
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None


def test_reminder_cancel_ordinal_resumes_business_selection_not_pending_cancel():
    """“取消第一条”是 reminder.cancel 的 index 答案，不是撤销这次澄清。"""
    from orchestrator.cloud.models import Plan, SessionState, Step

    engine, spy, session = _make_engine()
    plan = Plan(
        steps=[Step(
            id="s1", agent_id="reminder", endpoint="stub:reminder",
            intent="reminder.cancel", slots={"title": "喝水"},
        )],
        raw_text="取消喝水提醒", goal="取消喝水提醒",
    )
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_slot", owner_user_id="u1", operation_id="op-reminder",
        pending_step_id="s1", missing_slots=["index"], completed_results={},
        pending_plan=engine._serialize_plan(plan),
    )))

    events = _run(engine, _req("取消第一条"))

    assert spy.count("reminder.cancel") == 1
    assert events[-1]["speech"] != "好的，已为您取消。"


def test_batch_reminder_cleanup_crosses_agent_clarify_and_engine_resume():
    """SL1 cleanup: real batch title -> clarify -> engine ordinal -> final item."""
    from agents._sdk.base import Context
    from agents._sdk.testing import run_handle
    from agents.reminder.src.agent import ReminderAgent
    from agents.reminder.src.store import ReminderStore
    from orchestrator.cloud.models import Plan, SessionState, Step

    class _Memory:
        def __init__(self):
            self.values: dict[str, str] = {}

        async def upsert_profile(self, _uid, key, value, **_kwargs):
            self.values[f"profile.{key}"] = value
            return True

        async def get_context(self, _sid, _uid, _vid, scopes, **_kwargs):
            return {scope: self.values[scope] for scope in scopes
                    if scope in self.values}

        async def get_session(self, *_args, **_kwargs):
            return []

        async def recall(self, *_args, **_kwargs):
            return []

    memory = _Memory()
    agent_ctx = Context("sess-1", "u1", "v1", memory)
    agent = ReminderAgent()
    agent.store = ReminderStore(dsn="")
    asyncio.run(agent.store.init())
    raw = "明天下午四点提醒我参加代号ZX9的评审会，三点半再提醒我一次"

    created = asyncio.run(run_handle(
        agent, "reminder.create_batch", raw_text=raw, ctx=agent_ctx))
    assert created.ui_card["type"] == "card_group"
    assert len(created.ui_card["items"]) == 2

    cancel_text = "取消参加代号ZX9的评审会"
    clarify = asyncio.run(run_handle(
        agent, "reminder.cancel", raw_text=cancel_text, ctx=agent_ctx))
    assert clarify.status == "need_slot"
    assert clarify.missing_slots == ["index"]
    assert len(clarify.ui_card["items"]) == 2

    class _ReminderSpy(_Spy):
        async def call_agent(self, endpoint, intent, slots, ctx, meta):
            self.calls.append((intent, dict(meta or {})))
            result = await run_handle(
                agent, intent, slots=slots, raw_text=ctx.raw_text,
                ctx=agent_ctx, meta=meta)
            status = {
                "ok": 0, "need_confirm": 1, "need_slot": 2,
                "failed": 3, "rejected": 4,
            }[result.status]
            return SimpleNamespace(
                status=status, speech=result.speech, follow_up=result.follow_up,
                actions=[], ui_card=result.ui_card, data=result.data or {},
                missing_slots=result.missing_slots,
            )

    spy = _ReminderSpy()
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session,
    )
    plan = Plan(
        steps=[Step(
            id="s1", agent_id="reminder", endpoint="stub:reminder",
            intent="reminder.cancel", slots={"title": "参加代号ZX9的评审会"},
        )],
        raw_text=cancel_text, goal="取消重复提醒",
    )
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_slot", owner_user_id="u1", operation_id="op-reminder",
        pending_step_id="s1", missing_slots=clarify.missing_slots,
        completed_results={}, pending_plan=engine._serialize_plan(plan),
    )))

    _run(engine, _req("取消第一条"))
    remaining, _ = asyncio.run(agent.store.list_split("u1"))
    assert len(remaining) == 1

    final_cancel = asyncio.run(run_handle(
        agent, "reminder.cancel", raw_text=cancel_text, ctx=agent_ctx))
    assert final_cancel.status == "ok" and "取消" in final_cancel.speech
    remaining, _ = asyncio.run(agent.store.list_split("u1"))
    assert remaining == []


def test_slot_pending_question_not_eaten_as_answer():
    """B5-1 黑洞修复②：疑问/回忆式（「我刚才让你提醒我什么来着」）不是槽位答案——
    判为换话题（挂起保留），不再拿去当 time_text 反复追问。"""
    assert PlannerEngine._is_topic_change("我刚才让你提醒我什么来着") is True
    assert PlannerEngine._is_topic_change("现在几点了？") is True
    assert PlannerEngine._is_topic_change("外面冷吗") is True
    assert PlannerEngine._is_topic_change("晚上九点") is False      # 真答案不误判
    assert PlannerEngine._is_topic_change("明天早上八点") is False


def test_slot_pending_full_navigation_command_is_topic_change():
    """B5-1：路况补槽挂起时，「导航去南山科技园」是完整新指令，不是旧 route 槽答案。"""
    assert PlannerEngine._is_topic_change("导航去南山科技园") is True
    assert PlannerEngine._is_topic_change("带我去宝安机场") is True


def test_slot_pending_midsentence_question_and_quantified_order_are_topic_change():
    """demo-3ukshz 真栈探针：麦当劳选店挂起（wait_slot store_hint）把后面两句
    **完整新意图**当成补槽答案吞掉——「附近的瑞幸有什么可以点的」（尾字「的」躲过
    句尾问式判据）和「在瑞幸咖啡(科技园文化广场店)点一杯标准美式」（句首「在」不在
    动作动词表）。补两条零领域字面量的语言学判据：句中问式（有什么/哪些）、
    动词+数量+量词+宾语的完整点单结构。"""
    assert PlannerEngine._is_topic_change("附近的瑞幸有什么可以点的") is True
    assert PlannerEngine._is_topic_change("菜单上有哪些辣的") is True
    assert PlannerEngine._is_topic_change(
        "在瑞幸咖啡(科技园文化广场店)点一杯标准美式") is True
    assert PlannerEngine._is_topic_change("来两份薯条加一个圣代") is True
    # 真槽位答案不误判：名词短语/裸数量仍是补槽答案
    assert PlannerEngine._is_topic_change("科苑南路店") is False
    assert PlannerEngine._is_topic_change("标准美式") is False
    assert PlannerEngine._is_topic_change("要两杯") is False


def test_slot_pending_enroute_search_command_is_topic_change():
    """B5-1：句首带场景状语的完整搜索请求不能被旧 route 槽吞掉。"""
    assert PlannerEngine._is_topic_change("路上帮我找家咖啡店，顺路买一杯") is True
    assert PlannerEngine._is_topic_change("途中搜一个充电站") is True
    assert PlannerEngine._is_topic_change("路上经过深南大道") is False


def test_slot_pending_ordinal_selection_is_topic_change():
    """B5-1：最新列表的裸序号选择不能被更早的 wait_slot 抢占。"""
    assert PlannerEngine._is_topic_change("第二个") is True
    assert PlannerEngine._is_topic_change("第3家") is True
    assert PlannerEngine._is_topic_change("三号方案") is True


def test_slot_pending_ordinal_resumes_its_own_choice_card():
    """B2-3：挂起步骤自身刚给出选择卡时，插话后的裸序号仍应补回该步骤。"""
    from orchestrator.cloud.models import SessionState

    pending = SessionState(
        phase="wait_slot",
        pending_step_id="charge",
        missing_slots=["destination"],
        completed_results={
            "charge": {
                "step_id": "charge",
                "ui_card": {"type": "poi_list", "purpose": "dest_choice"},
            },
        },
    )

    assert PlannerEngine._is_topic_change("第一个", pending) is False
    assert PlannerEngine._is_topic_change("第3家", pending) is False


def test_slot_pending_conditional_reminder_is_topic_change():
    """B5-1：条件在前、动作在后的完整提醒不能被旧 route 槽吞掉。"""
    assert PlannerEngine._is_topic_change("到公司之前提醒我交周报") is True
    assert PlannerEngine._is_topic_change("电量低于20%时叫我充电") is True
    assert PlannerEngine._is_topic_change("到公司") is False


def test_slot_pending_explanation_question_is_not_consumed_as_destination():
    pending = SimpleNamespace(
        phase="wait_slot", pending_step_id="s1", missing_slots=["destination"],
        pending_plan={"steps": [{"id": "s1", "intent": "charging.plan"}]},
    )

    assert PlannerEngine._is_topic_change("第一站为什么选它", pending) is True


def test_order_id_slot_only_accepts_an_explicit_order_reference():
    """退款/取消缺订单号时，任意新话题绝不能被当成写操作参数。"""
    from orchestrator.cloud.models import SessionState

    pending = SessionState(
        phase="wait_slot", pending_step_id="s1", missing_slots=["order_id"],
        owner_user_id="u1", operation_id="op-order",
    )

    assert PlannerEngine._is_topic_change("附近的咖啡店", pending) is True
    assert PlannerEngine._is_topic_change("上次麦当劳那单", pending) is True
    assert PlannerEngine._is_topic_change("1030837030000753499156095268", pending) is False
    assert PlannerEngine._is_topic_change(
        "订单号是 1030837030000753499156095268", pending) is False


def test_order_id_slot_answer_strips_the_spoken_label():
    """追问恢复后，商户参数必须只有 ID，不能把“订单号是”一起下发。"""
    oid = "1030837030000753499156095268"
    assert PlannerEngine._slot_answer("order_id", f"订单号是 {oid}") == oid
    assert PlannerEngine._slot_answer("order_id", oid) == oid
    assert PlannerEngine._slot_answer("destination", "订单号是深圳湾") == "订单号是深圳湾"


def test_pending_plan_preserves_skills_across_suspend_restore():
    """T2 知识继承跨挂起（2026-07-27 评审二批）：plan.skills 必须随 pending_plan 持久化
    ——补槽/确认恢复后的再规划按它重渲染知识（loop.py→replan skill_names），
    不存=恢复轮失忆，「全生命周期闭合」在挂起链上是假的。"""
    from orchestrator.cloud.models import Plan, SessionState, Step

    plan = Plan(steps=[Step(id="s1", agent_id="info", intent="info.weather",
                            slots={}, depends_on=[], slot_refs={})],
                raw_text="查天气", complexity="adaptive", goal="g")
    plan.skills = ["full:conditional-reminder@vec", "full:freshness-and-depth"]
    data = PlannerEngine._serialize_plan(plan)
    assert data["skills"] == plan.skills

    state = SessionState(phase="wait_slot", pending_plan=data, pending_step_id="s1")
    restored, _seeds = PlannerEngine._restore(None, state, inject_confirmed=False)
    assert restored is not None
    assert restored.skills == plan.skills


def test_pending_plan_preserves_server_owned_safety_origin_separately_from_slot_answer():
    """pending 只持久化首句安全依据；续接轮 raw_text 仍可保持当前槽答案。"""
    from orchestrator.cloud.models import Plan, PlanContext, SessionState, Step

    origin = "红色机油灯亮了还能继续开吗"
    plan = Plan(
        steps=[Step(id="s1", agent_id="manual", intent="manual.query")],
        raw_text=origin, complexity="adaptive", goal="关闭故障灯",
        safety_origin_text=origin,
    )

    snap = PlannerEngine._serialize_plan(plan)
    assert snap["safety_origin_text"] == origin
    restored, _ = PlannerEngine._restore(
        None,
        SessionState(
            phase="wait_slot", pending_plan=snap, pending_step_id="s1",
        ),
        inject_confirmed=False,
    )
    ctx = PlanContext(
        raw_text="深圳", safety_origin_text=restored.safety_origin_text,
    )

    assert restored.safety_origin_text == origin
    assert ctx.safety_origin_text == origin
    assert ctx.raw_text == "深圳"


def test_legacy_pending_origin_falls_back_only_to_persisted_raw_text_not_goal():
    """滚动升级旧记录可用服务端 raw_text；goal 绝不升级成安全授权来源。"""
    from orchestrator.cloud.models import SessionState

    with_raw, _ = PlannerEngine._restore(
        None,
        SessionState(
            phase="wait_slot", pending_step_id="s1",
            pending_plan={
                "steps": [{"id": "s1", "agent_id": "manual",
                           "intent": "manual.query"}],
                "raw_text": "红色机油灯亮了还能继续开吗",
                "goal": "关闭故障灯",
            },
        ),
        inject_confirmed=False,
    )
    without_raw, _ = PlannerEngine._restore(
        None,
        SessionState(
            phase="wait_slot", pending_step_id="s1",
            pending_plan={
                "steps": [{"id": "s1", "agent_id": "manual",
                           "intent": "manual.query"}],
                "raw_text": "",
                "goal": "关闭故障灯",
            },
        ),
        inject_confirmed=False,
    )

    assert with_raw.safety_origin_text == "红色机油灯亮了还能继续开吗"
    assert without_raw.safety_origin_text == ""


def test_unknown_legacy_pending_confirmed_side_effect_fails_closed_before_dispatch():
    """旧 pending 无 origin/raw_text 时，当前“确认”和 goal 都不能放行原挂起写步。"""
    from orchestrator.cloud.models import SessionState

    engine, spy, session = _make_engine()
    asyncio.run(session.save("sess-legacy-write", SessionState(
        phase="wait_confirm",
        owner_user_id="u1",
        operation_id="op-legacy-write",
        pending_step_id="s1",
        pending_plan={
            "steps": [{
                "id": "s1", "agent_id": "nearby", "endpoint": "stub:50063",
                "intent": "nearby.order", "require_confirm": True,
            }],
            "raw_text": "",
            "goal": "预订餐厅",
        },
    )))

    events = _run(engine, _req(
        "确认", session_id="sess-legacy-write", is_confirmation=True,
        operation_id="op-legacy-write",
    ))

    assert spy.count("nearby.order") == 0
    assert "重新发起" in events[-1]["speech"]
    assert asyncio.run(session.load(
        "sess-legacy-write", owner_user_id="u1",
        operation_id="op-legacy-write",
    )) is None


def test_unknown_legacy_pending_read_still_dispatches_with_current_slot_answer():
    """unknown origin 只挡声明式副作用；恢复后的只读补槽仍使用当前回答。"""
    from orchestrator.cloud.models import SessionState

    engine, spy, session = _make_engine()
    asyncio.run(session.save("sess-legacy-read", SessionState(
        phase="wait_slot",
        owner_user_id="u1",
        operation_id="op-legacy-read",
        pending_step_id="s1",
        missing_slots=["cuisine"],
        pending_plan={
            "steps": [{
                "id": "s1", "agent_id": "nearby", "endpoint": "stub:50063",
                "intent": "nearby.search", "slots": {},
            }],
            "raw_text": "",
            "goal": "查找餐厅",
        },
    )))

    events = _run(engine, _req(
        "川菜", session_id="sess-legacy-read",
        operation_id="op-legacy-read",
    ))

    assert spy.count("nearby.search") == 1
    assert events[-1]["kind"] == "final"


def test_slot_pending_compound_cancel_continues_as_fresh_request():
    """EVA 三§3（2026-08-15 真栈实测）：wait_slot 挂起中「算了咖啡不买了，先去
    帮我看看附近有什么景点」——取消词只该作用于挂起，后半句是新请求。此前命中
    `_SLOT_CANCEL_RE` 即整句吞掉、4.6ms 直回「已取消」。修后：挂起清 + 余句
    按全新请求正常规划执行。"""
    from orchestrator.cloud.models import SessionState
    engine, spy, session = _make_engine_interject()
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_slot", owner_user_id="u1",
        pending_step_id="s1", missing_slots=["time_text"],
        completed_results={}, pending_plan={"goal": "创建交周报提醒"})))

    events = _run(engine, _req("算了那个不要了，先去帮我看看附近有什么景点"))

    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None  # 挂起清了
    final = events[-1]
    assert "已为您取消" not in (final.get("speech") or "")   # 不是取消直回
    assert spy.count("nearby.search") == 1                   # 余句真的被执行了


def test_confirm_pending_referenced_cancel_clears():
    """QA I-046（Q1-A）：`wait_confirm` 挂起中说「取消刚才的预订」——6 字 > 2+3，
    旧「词占据整句」判据**判不出取消**，落进「插话保留挂起」，pending 一直活着
    （用户报告：第三次单独说「取消」才清除）。收敛到唯一判据后立即取消。"""
    engine, spy, session = _make_engine_interject()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is not None

    events = _run(engine, _req("取消刚才的预订"))

    assert "取消" in events[-1]["speech"]
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None
    assert all(m.get("confirmed") != "true" for m in spy.metas("nearby.order"))


def test_confirm_pending_compound_cancel_continues_as_fresh_request():
    """Q1-A 的另一半：`wait_confirm` 也要有 `wait_slot` 的复合余量续处理，
    否则收敛只搬走了一半（§37 那批的产物在这条分支上从来没生效过）。"""
    engine, spy, session = _make_engine_interject()
    _run(engine, _req("找家川菜馆订今晚7点两位"))

    events = _run(engine, _req("算了不订了，帮我看看附近有什么景点"))

    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None
    assert "已为您取消" not in (events[-1].get("speech") or "")
    assert spy.count("nearby.search") == 2      # 首轮 1 次 + 余句真的被执行了


def test_confirm_pending_weak_negation_word_still_cancels():
    """收敛不得换一个洞：`不订/不付/先不` 只在旧 `wait_confirm` 词表里，
    直接改用 `wait_slot` 那套会把它们丢掉。"""
    engine, _, session = _make_engine_interject()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    events = _run(engine, _req("先不"))
    assert "取消" in events[-1]["speech"]
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None


def test_slot_pending_pure_cancel_unchanged():
    """对照：纯取消句（「那个提醒不用了，取消吧」剥后余量 <6）行为逐字不变。"""
    from orchestrator.cloud.models import SessionState
    engine, spy, session = _make_engine_interject()
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_slot", owner_user_id="u1",
        pending_step_id="s1", missing_slots=["time_text"],
        completed_results={}, pending_plan={"goal": "创建交周报提醒"})))

    events = _run(engine, _req("那个提醒不用了，取消吧"))

    assert "取消" in events[-1]["speech"]
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None
    assert spy.count("nearby.search") == 0


# ── C3：wait_slot 方向反转（形状契约 + 词表合并 + 止损底线） ──────────────

def test_slot_pending_declared_shape_rejects_a_new_request():
    """C3-A（真栈 T45/T46）：`item_query` 声明了 `item_name` 形状之后，
    「附近的川菜馆」「…哪个贵」不再被整句吞成搜索词。

    这两句此前**每一句都能连答三轮**「在售餐单里没查到"<用户刚说的那句话>"」——
    补槽把用户越推越远，而挂起一直不放。
    """
    from orchestrator.cloud.models import SessionState

    pending = SessionState(
        phase="wait_slot", pending_step_id="s1",
        missing_slots=["item_query"],
        slot_shapes={"item_query": "item_name"})

    assert PlannerEngine._is_topic_change("附近的川菜馆", pending) is True
    assert PlannerEngine._is_topic_change(
        "麦当劳的第二个和川菜的第二个哪个贵", pending) is True
    # 反向对照：真餐品名仍然是槽位答案，别把补槽修死
    assert PlannerEngine._is_topic_change("巨无霸", pending) is False
    assert PlannerEngine._is_topic_change("拿铁", pending) is False
    assert PlannerEngine._is_topic_change(
        "马来咖喱风味薄皮肉骨鸡随心配", pending) is False


def test_slot_pending_without_declaration_keeps_old_behaviour():
    """没声明形状的槽**逐字维持原行为**——这条机制不许悄悄收紧全域补槽。"""
    from orchestrator.cloud.models import SessionState

    pending = SessionState(
        phase="wait_slot", pending_step_id="s1", missing_slots=["store_hint"])
    assert PlannerEngine._is_topic_change("科苑南路店", pending) is False
    assert PlannerEngine._is_topic_change("晚上九点", pending) is False


def test_slot_pending_new_search_wordlist_is_shared_with_candidate_query():
    """C3-B：「这是一次新检索」的词表只有一份——`candidate_query.NEW_SEARCH_RE`。

    此前它只在候选集聚合那一侧生效，同一句「附近的川菜馆」在那边判成新检索、
    在补槽这边被当成槽值：**同一判据两份实现，给同一句话两个答案**。
    """
    from orchestrator.cloud import candidate_query

    for text in ("附近的川菜馆", "旁边有没有充电站", "帮我找家咖啡店", "重新搜一遍"):
        assert candidate_query.NEW_SEARCH_RE.search(text), text
        assert PlannerEngine._is_topic_change(text) is True, text
    # 「哪个」补进疑问词表（旧表只有「哪些」）
    assert PlannerEngine._is_topic_change("哪个更便宜") is True
    # 误伤面对照：这些不含新检索词，仍是槽位答案
    for text in ("路上经过深南大道", "科苑南路店", "到公司"):
        assert PlannerEngine._is_topic_change(text) is False, text


def test_slot_pending_index_shape_rejects_a_new_instruction():
    """C3 续（QA 余项，真栈长会话 `e15ac1e` family turn 18/73/76 连撞三轮）：
    `index` 声明 `ordinal` 形状之后，一句新指令不再被当成序号答案。

    原始形态：系统问「有 3 条都能对上…要取消哪条？」，用户下一句说
    「**列出我现在进行中的提醒**」——整句被填进 `index` 槽，Agent 认不出序号、
    退回标题路径、又查出同样三条、**又问一遍**。三轮之后挂起还活着，
    turn 77 用户说「取消导航」时被它接走，导航从头到尾没被取消过。
    **一个黑洞会喂大下一个洞。**
    """
    from orchestrator.cloud.models import SessionState

    pending = SessionState(
        phase="wait_slot", pending_step_id="s1",
        missing_slots=["index"], slot_shapes={"index": "ordinal"})

    # 真栈那三轮的原话
    assert PlannerEngine._is_topic_change("列出我现在进行中的提醒", pending) is True
    # 同族：不是序号的任何说法都判换题（`_resolve_targets` 也用不上它们）
    for text in ("都取消", "取消全部", "看看我的待办", "现在几点了"):
        assert PlannerEngine._is_topic_change(text, pending) is True, text
    # 反向对照：真序号答案仍然是槽值，别把补槽修死
    for text in ("第一条", "取消第一条", "完成第二条", "改第二条", "1", "第2个"):
        assert PlannerEngine._is_topic_change(text, pending) is False, text


def test_pending_cancel_does_not_swallow_a_named_cancel_instruction():
    """QA 余项（真栈长会话 `e15ac1e` family turn 77）：有挂起时说「取消导航」，
    **挂起该清、导航也该被规划**——旧判据把整轮吞掉，回一句关于另一件事的
    「好的，已为您取消。」，而导航一次都没被取消（C11 那一族的假完成声明）。

    这条与 `test_cancel_with_short_reference_is_pure_cancel` 是一对：
    带回指的（「取消刚才解锁」）仍判纯取消，不带的按新请求继续走。
    """
    from orchestrator.cloud.models import SessionState

    engine, spy, session = _make_engine_interject()
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_slot", owner_user_id="u1", operation_id="op-stuck",
        pending_step_id="s1", missing_slots=["index"],
        slot_shapes={"index": "ordinal"},
        completed_results={}, pending_plan={"goal": "取消提醒"})))

    events = _run(engine, _req("取消导航"))

    # ① 原挂起被清掉了（`op-stuck` 不复存在）
    left = asyncio.run(session.load("sess-1", owner_user_id="u1"))
    assert left is None or left.operation_id != "op-stuck"
    # ② 但**不许**只回一句「已为您取消」就收场——那句话说的是另一件事
    assert "已为您取消" not in (events[-1].get("speech") or "")
    # ③ 余句真的被当成新请求规划、执行了（替身计划落到 nearby）
    assert spy.count("nearby.search") >= 1, "「取消导航」被吞掉了，没有走到规划"


def test_asking_to_relist_the_candidates_is_never_a_slot_answer():
    """真栈（release `538335f`，2/2 复现）：充电规划出了 `dest_choice` 选择卡之后，
    「请重新列出刚才可以选择的项目」被**整句填进 `destination` 槽**，答成
    「暂时无法获取前往**请重新列出刚才可以选择的项目**的路线」。

    与 `index` 那个黑洞同族，只是换了个槽——而这一族**没有终点**，
    所以判据不是补词表：**「这句话是在问候选集」定义上就不是某个待补槽的值**，
    词表复用 `candidate_query.RELIST_RE`（同 C3-B 那笔，判据只许有一份）。
    """
    from orchestrator.cloud.models import SessionState

    pending = SessionState(phase="wait_slot", pending_step_id="s1",
                           missing_slots=["destination"])
    for text in ("请重新列出刚才可以选择的项目", "再列一遍", "刚才那些选项再说一下"):
        assert PlannerEngine._is_topic_change(text, pending) is True, text


def test_a_bare_ordinal_on_a_choice_card_is_still_the_answer():
    """误伤对照：**裸「第一个」正是选择卡的合法答案**——收进换题判据会把整条
    选店/选目的地流程修死。上面那条只收「重列」，一个序数都不许沾。
    """
    from orchestrator.cloud.models import SessionState

    pending = SessionState(
        phase="wait_slot", pending_step_id="s1", missing_slots=["destination"],
        completed_results={"s1": {"ui_card": {"type": "poi_list",
                                              "purpose": "dest_choice"}}})
    for text in ("第一个", "第二个", "第3个"):
        assert PlannerEngine._is_topic_change(text, pending) is False, text


def test_slot_pending_is_abandoned_after_repeated_unanswered_asks():
    """C3-D 止损底线：同一个问题问到上限还没接上就放弃它，并**说一句**。

    判据再准也有漏网的说法（词表竞赛没有终点），这条保证黑洞只能吞有限轮。
    """
    from orchestrator.cloud.models import SessionState
    from orchestrator.cloud.engine import SLOT_RETRY_LIMIT

    engine, spy, session = _make_engine_interject()
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_slot", owner_user_id="u1", operation_id="op-stuck",
        pending_step_id="s1", missing_slots=["item_query"],
        slot_retry=SLOT_RETRY_LIMIT,
        completed_results={}, pending_plan={"goal": "查看麦当劳菜单"})))

    events = _run(engine, _req("帮我看看附近有什么景点"))
    final = events[-1]

    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None
    assert spy.count("nearby.search") == 1          # 这一轮按全新请求真的执行了
    assert "查看麦当劳菜单" in (final.get("follow_up") or "")
    assert "放一放" in (final.get("follow_up") or "")
    assert "op-stuck" in (final.get("closed_operation_ids") or [])


def test_slot_retry_counts_only_the_same_question():
    """计数只认「同一步 + 同一组待补槽」。

    商户流程「先问门店、再问餐品」是**进展**，同一步换个槽再问不能被算成
    原地打转——否则一个正常的多槽流程走到第三问就被放弃了。
    """
    from orchestrator.cloud.models import PlanContext, StepResult, StepStatus, Plan, Step

    engine, _spy, session = _make_engine_interject()
    plan = Plan(steps=[Step(id="s1", agent_id="mcp-bridge", intent="mcd.menu")])

    async def _suspend_with(probe, missing):
        ctx = PlanContext(session_id="sess-x", user_id="u1",
                          pending_operation_id="op-prev",
                          pending_slot_probe=probe)
        await engine._suspend(
            StepResult(step_id="s1", status=StepStatus.NEED_SLOT,
                       missing_slots=missing),
            [], plan, ctx)
        return (await session.load("sess-x", owner_user_id="u1")).slot_retry

    same = asyncio.run(_suspend_with(
        {"step_id": "s1", "missing": ["item_query"], "retry": 1}, ["item_query"]))
    assert same == 2                                  # 同一个问题又问了一遍

    progressed = asyncio.run(_suspend_with(
        {"step_id": "s1", "missing": ["store_hint"], "retry": 1}, ["item_query"]))
    assert progressed == 0                            # 换了槽 = 进展，归零


# ── I-024 第二层：挂起轮从来不写焦点（2026-08-30）─────────────────────────

def _suspend_with_card(card: dict) -> list:
    """跑一次 `_suspend`，返回 `update_focus` 被调用的次数记录。"""
    from orchestrator.cloud.models import Plan, Step, StepResult, StepStatus, PlanContext

    engine, _, _ = _make_engine_interject()
    calls = []

    async def _spy_update_focus(session_id, plan, results, **kw):
        calls.append((session_id, len(results)))
    engine.context.update_focus = _spy_update_focus

    step = Step(id="s1", agent_id="mcp-bridge", endpoint="x",
                intent="luckin.order", slots={})
    plan = Plan(steps=[step], raw_text="先查附近的瑞幸，再点一杯生椰拿铁")
    sr = StepResult(step_id="s1", status=StepStatus.NEED_SLOT,
                    speech="要在哪家下？", ui_card=card,
                    missing_slots=["store_hint"])
    ctx = PlanContext(session_id="sess-1", user_id="u1", request_id="r1")
    asyncio.run(engine._suspend(sr, [sr], plan, ctx))
    return calls


def test_a_choice_card_suspend_writes_the_focus():
    """**抽取改对了，而调用方在它之前就返回了。**

    三个 `_suspend` 调用点都是 `yield await self._suspend(...)` 紧跟 `return`，
    而 `update_focus` 在它们**之后** ⇒ 把「可见选择卡的候选」收进 `extract_focus`
    之后，那份候选**仍然到不了存储**（真栈实测：重列仍答「没有您刚才那页选项的记录」）。

    同一形态的第三例——前两次是「安全告警登记在 clarify/no_plan 的 return 之后」
    与「`_refresh_active` 刷成了用户没看见的那份」。
    """
    assert _suspend_with_card(
        {"type": "merchant_choices", "purpose": "store_choice"}) == [("sess-1", 1)]
    assert _suspend_with_card(
        {"type": "poi_list", "purpose": "dest_choice"}) == [("sess-1", 1)]


def test_an_ordinary_suspend_still_writes_nothing():
    """误伤对照：**普通的确认/补槽挂起行为逐字不变**——不写焦点。

    判据窄到「挂起步自己出的是选择卡」，那正是 C10-A 说的
    「用户最后一眼看到的那份列表」；别的挂起没有这份列表，写它就是给序数指代埋雷。
    """
    assert _suspend_with_card({"type": "merchant_order_preview"}) == []
    assert _suspend_with_card(None) == []


def test_a_safety_statement_is_never_a_slot_answer():
    """真栈（release `0d414816`，T47 收口探针 3 趟里 1 趟）：充电规划出了 `dest_choice` 选择卡之后，
    「检查过了，机油灯已经灭了，恢复正常了」被**整句填进 `destination`**，答成
    「暂时无法获取前往…的路线」；解除扫描只在云侧规划轮跑，这一句被挂起吃掉 ⇒ 机油灯永远清不掉。
    反方向更危险：挂起期间说「机油灯亮了」同样会被当地址吞掉、登记不上。
    判据：**安全信号是系统持有的事实，定义上就不是某个待补槽的值**（复用 `runtime.safety_signal`）。
    """
    from orchestrator.cloud.models import SessionState

    pending = SessionState(
        phase="wait_slot", pending_step_id="s1", missing_slots=["destination"],
        completed_results={"s1": {"ui_card": {"type": "poi_list",
                                              "purpose": "dest_choice"}}})
    for text in ("检查过了，机油灯已经灭了，恢复正常了", "机油灯灭了",
                 "红色机油灯亮了", "刹车有异响", "困到睁不开眼了",
                 "规划去广州路上的补能，但先不要启动导航"):
        assert PlannerEngine._is_topic_change(text, pending) is True, text
    # 误伤对照：真正的目的地答案照旧是槽值
    for text in ("广州南站", "白云机场", "第二个"):
        assert PlannerEngine._is_topic_change(text, pending) is False, text


def test_slot_pending_disposal_frame_and_polite_imperative_are_topic_change():
    """批 5 W18 真栈（continuity persona T21）：路况补槽挂起（「您想查询哪条路线的路况？」）把
    「把全车门解锁」整句当路线吃掉——答「为您找到 0 个把全车门解锁 路况」，用户的车控指令消失。
    「把 / 将 + 对象 + 动词」是处置式祈使，「请 / 麻烦 + 动词」是礼貌祈使，都不是槽值。"""
    assert PlannerEngine._is_topic_change("把全车门解锁") is True
    assert PlannerEngine._is_topic_change("将空调调到26度") is True
    assert PlannerEngine._is_topic_change("请打开车窗") is True
    assert PlannerEngine._is_topic_change("麻烦关一下天窗") is True
    # 真路线 / 地址答案不误判
    assert PlannerEngine._is_topic_change("走滨海大道") is False
    assert PlannerEngine._is_topic_change("深南大道") is False
    assert PlannerEngine._is_topic_change("从科技园到机场那条") is False


def test_slot_pending_task_title_accepts_a_disposal_phrase_as_its_value():
    """批 5 W18 的对照：待办标题槽声明了 `task_title` ⇒「把文件交给张总」是槽值不是新指令；
    没声明形状的槽（路况 route）照旧把它判成换题。"""
    from orchestrator.cloud.models import SessionState
    titled = SessionState(phase="wait_slot", pending_step_id="s1",
                          missing_slots=["title"], slot_shapes={"title": "task_title"})
    assert PlannerEngine._is_topic_change("把文件交给张总", titled) is False
    assert PlannerEngine._is_topic_change("请帮我订一下会议室", titled) is False
    assert PlannerEngine._is_topic_change("讲个笑话", titled) is True          # 通用判据照旧
    assert PlannerEngine._is_topic_change("把全车门解锁吗", titled) is True     # 问句不是标题
    routed = SessionState(phase="wait_slot", pending_step_id="s1", missing_slots=["route"])
    assert PlannerEngine._is_topic_change("把全车门解锁", routed) is True


# ── 评审二轮 R1（2026-09-22）：肯定词 + 任意实质短尾不是授权 ────────────────

@pytest.mark.parametrize("text", ["行程", "确认函", "可以改", "行李", "确定性", "好的那本", "可以看"])
def test_a_yes_word_with_a_substantive_tail_never_authorizes(text):
    """「行程」「确认函」「可以改」：肯定词开头、剩一个实质字——旧判据把 ≤1 字的余量当语气尾
    ⇒ 裸确认 ⇒ `confirm_resolved` ⇒ 恢复挂起并注入 confirmed（局部复算已复现）。
    语气词剥完必须一个实质字都不剩，才是裸确认；剩下的按插话 / 新请求走。"""
    engine, spy, session = _make_engine()
    op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]

    final = _run(engine, _req(text))[-1]

    assert all(m.get("confirmed") != "true" for m in spy.metas("nearby.order")), text
    assert op1 not in (final.get("closed_operation_ids") or []), text
    assert asyncio.run(session.load(
        "sess-1", owner_user_id="u1", operation_id=op1)) is not None, text
    assert spy.llm_plan_calls == 2, text                  # 当新请求规划了，没被短路成确认


@pytest.mark.parametrize("text", ["好的，确认吧", "嗯确认", "可以可以", "确认了", "行啊", "好的好的",
                                  "嗯，可以", "确认一下", "好，确认", "确认吧。", "好"])
def test_a_bare_affirmation_with_tone_particles_still_confirms(text):
    """对照面：全句只有肯定词与语气词 ⇒ 仍是确认（「好的，确认吧」此前被拆成点名「确认」而落空）。"""
    engine, spy, session = _make_engine()
    op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]

    final = _run(engine, _req(text))[-1]

    assert spy.metas("nearby.order")[-1].get("confirmed") == "true", text
    assert op1 in (final.get("closed_operation_ids") or []), text
    assert spy.llm_plan_calls == 1, text


def test_confirm_reply_strict_rules():
    f = PlannerEngine._confirm_reply
    for t in ("行程", "确认函", "可以改", "行李箱", "好的那个", "确定性", "可以看"):
        assert f(t, False) is None, t
    for t in ("好的，确认吧", "嗯确认", "可以可以", "确认了", "行啊", "确认一下", "好", "好了", "ok了"):
        assert f(t, False) == "yes", t
    assert PlannerEngine._is_bare_confirm_word("行程") is False
    assert PlannerEngine._is_bare_confirm_word("确认函") is False
    assert PlannerEngine._split_confirm_prefix("行程") == (False, "")
    assert PlannerEngine._split_confirm_prefix("好的，确认吧") == (True, "")
    assert PlannerEngine._split_confirm_prefix("确认订单") == (False, "订单")


def test_a_yes_word_with_a_tail_and_no_pending_is_not_hijacked_as_no_pending():
    """没有挂起时「行程」曾被 `_is_bare_confirm_word` 拦成「当前没有待确认的操作」——它是一个新请求。"""
    engine, spy, _ = _make_engine()
    final = _run(engine, _req("行程"))[-1]
    assert "没有待确认" not in final["speech"]
    assert spy.llm_plan_calls == 1
