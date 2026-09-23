"""被拒诉求的身份 = 任务原话里它所在的分句（评审三轮 R3-03，2026-09-23）。

二轮 R5 把 unsupported 的终止标记从「整个领域」收窄到「领域 + 被拒那步的槽值实质」，但同一性仍用任意两字交集近似：
拒绝了「深圳下雨就通知我」之后，同一句里独立的「明天八点提醒我和深圳客户开会」因为共用「深圳」被当成再试丢掉。
身份改成**来源**：被拒那一步的槽值落在原话的哪个分句（最长公共子串最大的那个），新步的槽值全落在被拒分句里才算再试；
有任何一个落点在别的分句就是独立诉求。证据不足（空槽、落不下）时：原话只有被拒那些分句 ⇒ 再试；否则只有空槽写能力
（`reminder.cancel {}` 那类「对它撤销 / 改动」）算再试，读能力照做。被拒步自己落不下时只认强证据（同指纹 / 整值包含）。
依赖被丢步的下游照旧传递 blocked（二轮 R5 的正确部分不退回）。
"""
from __future__ import annotations

import asyncio

from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder, _grounding, _origin_clauses

from .test_planning import MockAgent

# catalog 按 (agent_id, intent) 排：reminder.cancel=0001, reminder.create=0002, reminder.list=0003
_CANCEL, _CREATE, _LIST = "cap_0001", "cap_0002", "cap_0003"


def _reminder_agent():
    agent = MockAgent("reminder", ["reminder.cancel", "reminder.create", "reminder.list"])
    for cap in agent.manifest.capabilities:
        if cap.intent in ("reminder.cancel", "reminder.create"):
            cap.effect = "write"
    return agent


def _refused(title: str) -> dict:
    return {"step_id": "s1", "status": "ok", "intent": "reminder.create",
            "slots": {"title": title}, "refused": "unsupported",
            "data": {"_refused": "unsupported"}}


def _step(ref: str, slots: dict, sid: str = "r1", **extra) -> str:
    import json
    return json.dumps({"id": sid, "capability_ref": ref, "slots": slots,
                       "depends_on": extra.get("depends_on", []),
                       "slot_refs": extra.get("slot_refs", {})}, ensure_ascii=False)


def _replan(steps: list[str], observations: list[dict], origin: str, goal: str = "LLM 的一句话目标"):
    body = '{"done":false,"steps":[' + ",".join(steps) + "]}"

    async def mock_llm(_messages):
        return body

    async def mock_resolve(query, top_k=1):
        return []

    ctx = PlanContext(safety_origin_text=origin, raw_text=origin)
    return asyncio.run(PlanBuilder(mock_llm, mock_resolve).replan(
        goal, observations, [_reminder_agent()], ctx))


_ORIGIN = "深圳下雨就通知我，另外明天八点提醒我和深圳客户开会"


def test_origin_clauses_split_on_sentence_ends_and_the_shared_connectives():
    assert _origin_clauses("有堵车就提醒我；另外明早八点提醒我开会。然后导航回家") == [
        "有堵车就提醒我", "明早八点提醒我开会", "导航回家"]


def test_grounding_takes_the_best_matching_clause():
    clauses = _origin_clauses(_ORIGIN)
    assert _grounding({"深圳下雨就通知我"}, clauses) == {0}
    assert _grounding({"深圳客户会议"}, clauses) == {1}       # 「深圳客户」4 字 > 「深圳」2 字
    assert _grounding({"明天八点"}, clauses) == {1}
    assert _grounding({"8:00"}, clauses) == set()             # 落不下就是落不下


def test_the_review_counterexample_keeps_an_independent_request_sharing_a_city():
    decision = _replan([_step(_CREATE, {"title": "深圳客户会议", "time_text": "明天八点"})],
                       [_refused("深圳下雨就通知我")], _ORIGIN)
    assert [(s.intent, s.slots) for s in decision.steps] == [
        ("reminder.create", {"title": "深圳客户会议", "time_text": "明天八点"})]
    assert decision.done is False


def test_the_control_sample_is_still_kept():
    decision = _replan([_step(_LIST, {"scope": "明天"})], [_refused("深圳下雨就通知我")], _ORIGIN)
    assert [s.intent for s in decision.steps] == ["reminder.list"]


def test_a_retry_grounded_only_in_the_refused_clause_is_still_dropped():
    decision = _replan([_step(_CREATE, {"title": "深圳下雨提醒"})],
                       [_refused("深圳下雨就通知我")], _ORIGIN)
    assert decision.done is True and decision.steps == []


def test_a_slotless_write_on_the_refused_domain_is_a_retry_even_with_other_clauses():
    decision = _replan([_step(_CANCEL, {})], [_refused("深圳下雨就通知我")], _ORIGIN)
    assert decision.done is True and decision.steps == []


def test_a_slotless_read_on_the_refused_domain_is_kept_when_the_origin_has_other_clauses():
    """二轮 R5 记下的边界（「列出我的提醒」无参数形态会被误拦）在这里关掉。"""
    decision = _replan([_step(_LIST, {})], [_refused("深圳下雨就通知我")],
                       "深圳下雨就通知我，另外列出我的提醒")
    assert [s.intent for s in decision.steps] == ["reminder.list"]


def test_a_single_clause_origin_treats_any_same_domain_step_as_a_retry():
    decision = _replan([_step(_CREATE, {"title": "明天开会", "time_text": "明天"})],
                       [_refused("有堵车")], "只要有堵车就提醒我")
    assert decision.done is True and decision.steps == []


def test_an_ungrounded_refusal_only_blocks_on_strong_identity():
    """被拒那一步的槽值（模型写成英文）在中文原话里一个字都落不下 ⇒ 只认强证据。"""
    origin = "下雨就通知我，另外明天提醒我看演示"
    same = _replan([_step(_CREATE, {"title": "rain alert"})], [_refused("rain alert")], origin)
    assert same.done is True and same.steps == []            # 同指纹 ⇒ 再试
    other = _replan([_step(_CREATE, {"title": "演示", "time_text": "明天"})],
                    [_refused("rain alert")], origin)
    assert [s.intent for s in other.steps] == ["reminder.create"]


def test_the_downstream_of_a_dropped_retry_is_still_blocked_not_rewired():
    decision = _replan([
        _step(_CREATE, {"title": "深圳下雨提醒"}, sid="r1"),
        _step(_LIST, {"scope": "明天"}, sid="r2", depends_on=["r1"], slot_refs={"note": "r1.data.id"}),
        _step(_CREATE, {"title": "深圳客户会议", "time_text": "明天八点"}, sid="r3"),
    ], [_refused("深圳下雨就通知我")], _ORIGIN)
    assert [s.id for s in decision.steps] == ["r3"]
    assert decision.blocked == ["reminder.list"]
