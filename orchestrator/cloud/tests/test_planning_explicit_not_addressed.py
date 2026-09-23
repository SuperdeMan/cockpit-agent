"""显式输入「不受话」重试的两处收口（追加批 G，2026-09-23；设计 §10）。

`explicit_input_not_addressed` 自 2026-08-14 起在云端触发 80 次（collector 日志逐条读终态与第二轮输出）：
- 40 次终态是技术失败，其中 ≥ 26 次第二轮逐字 `{"addressed":true,"steps":[]}`——模型两次都说没事可做（「你好，请只
  回复一句问候」「hello」「啊」「可以，已为您执行」），系统却让用户听「这次我没能把您的请求拆成可以执行的步骤」（G-2）；
- 2 次第二轮编出了动作：「可以，已为您执行」→ `warning_light.close`（`a59b1621` RS21，真栈关了双闪）/ `reminder.cancel`。
  纠正话术「无法完成显式请求；请重新逐句规划」断言了一个不存在的请求，模型顺着前提编（G-3 盖写车控 / 需确认那一类）。
  改纠正话术本身做过容器内 A/B：新话术没减少编造（两臂 0/48），却让「我有点冷」救回率 8/8 → 5/8，已证伪、不采用。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder, _assemble_capability_catalog

from tests.test_planning import MockAgent

NOT_ADDRESSED = '{"addressed":false,"steps":[]}'
NO_ACTION = '{"addressed":true,"steps":[]}'
GARBAGE = "用户在复述一句话，我来提交计划。"


@pytest.fixture(autouse=True)
def _offline_retrieval(monkeypatch):
    """范例检索缺省 hybrid 会打 Embed（网络）；单测离线确定。"""
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _agents():
    return [
        MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)),
        MockAgent("edge-vehicle", ["warning_light.close", "trunk.open", "hvac.inc"],
                  kind="edge_fast", deployment="edge", require_confirm=("trunk.open",)),
        MockAgent("nearby", ["nearby.search"]),
    ]


def _wire(*pairs, goal=""):
    """capability_ref 从真实装配的 catalog 取（编号随目录顺序走，不写死）。"""
    catalog = _assemble_capability_catalog(_agents())
    steps = [{"id": f"s{i}", "capability_ref": catalog.pair_to_ref[pair], "slots": {},
              "depends_on": [], "slot_refs": {}} for i, pair in enumerate(pairs, 1)]
    return json.dumps({"addressed": True, "complexity": "simple", "goal": goal, "steps": steps},
                      ensure_ascii=False)


def _build(replies, text, input_source=""):
    calls = {"llm": 0, "fallback": 0}

    async def mock_llm(messages):
        calls["llm"] += 1
        return replies[min(calls["llm"] - 1, len(replies) - 1)]

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    inner = builder._fallback

    async def counted(t, agents=None):
        calls["fallback"] += 1
        return await inner(t, agents)

    builder._fallback = counted
    ctx = PlanContext(session_id="t", prefs={"input_source": input_source} if input_source else {})
    plan = asyncio.run(builder.build(text, WorkingSet(catalog=_agents()), ctx))
    return plan, calls


# ── G-2：第一轮不受话 + 第二轮受话零步 = 模型两次都说没事可做 ⇒ 谈话，不是技术失败 ──────────────

@pytest.mark.parametrize("text", [
    "你好，请只回复一句问候",   # 发布验收探针原句（历史 8 次落技术失败）
    "可以，已为您执行",
    "啊",
    "hello",
    "我不想排队",
])
def test_not_addressed_then_no_action_on_typed_input_is_answered(text):
    plan, calls = _build([NOT_ADDRESSED, NO_ACTION], text)
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps
    assert plan.technical_failure is False
    assert plan.plan_mode.endswith("_no_action"), plan.plan_mode
    assert plan.retry_policies[0] == "explicit_input_not_addressed", plan.retry_policies
    assert calls == {"llm": 2, "fallback": 0}


@pytest.mark.parametrize("text", ["把全车门解锁", "打开空调"])
def test_an_operation_shaped_utterance_keeps_the_honest_failure(text):
    """带操作证据的原话两轮空手 ⇒ 仍按 F09 诚实报失败：谈话在这里是伪装（用户要的是开锁，不是一句话）。"""
    plan, calls = _build([NOT_ADDRESSED, NO_ACTION], text)
    assert plan.technical_failure is True
    assert calls["fallback"] == 1


def test_not_addressed_then_garbage_is_still_a_technical_failure():
    """第二轮是坏输出：模型只说过一次（还是「不受话」）⇒ 没有两次判断可以兑现。"""
    plan, calls = _build([NOT_ADDRESSED, GARBAGE], "你好，请只回复一句问候")
    assert plan.technical_failure is True
    assert calls["fallback"] == 1


def test_a_single_no_action_without_the_not_addressed_retry_is_not_enough():
    """模型只说过一次无需动作（没有第一轮「不受话」）：既有「连说两次才认」照旧，F09 照报。"""
    plan, calls = _build([NO_ACTION, GARBAGE], "可以，已为您执行")
    assert plan.technical_failure is True
    assert calls["fallback"] == 1


def test_the_directive_not_addressed_path_is_unchanged():
    """「记住…」走 `directive_not_addressed`（原话被确定性判成对助手的祈使）：两轮空手照旧报失败。"""
    plan, calls = _build([NOT_ADDRESSED, NO_ACTION], "记住我不吃香菜")
    assert plan.retry_policies[0] == "directive_not_addressed", plan.retry_policies
    assert plan.technical_failure is True
    assert calls["fallback"] == 1


def test_voice_not_addressed_is_still_a_rejection():
    plan, _ = _build([NOT_ADDRESSED, NO_ACTION], "可以，已为您执行", input_source="voice_followup")
    assert plan.steps == [] and plan.addressed is False


# ── G-3：被催出来的写车控 / 需确认步，原话里得有操作证据 ─────────────────────────────────────

def test_a_nudged_vehicle_write_without_an_operation_cue_is_not_executed():
    """真栈 `a59b1621` RS21 第 2 趟逐字：第一轮不受话，第二轮 goal「关闭雾灯」+ `warning_light.close` ⇒ 端侧关了双闪。"""
    plan, calls = _build([NOT_ADDRESSED, _wire(("edge-vehicle", "warning_light.close"), goal="关闭雾灯")],
                         "可以，已为您执行")
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps
    assert "_nudged_write_blocked" in plan.plan_mode, plan.plan_mode
    assert plan.technical_failure is False
    assert calls["fallback"] == 0


def test_a_nudged_confirmation_step_without_an_operation_cue_is_not_offered():
    plan, _ = _build([NOT_ADDRESSED, _wire(("edge-vehicle", "trunk.open"))], "好的，谢谢")
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps
    assert "_nudged_write_blocked" in plan.plan_mode, plan.plan_mode


def test_the_whole_nudged_plan_goes_not_only_the_write():
    """前提是编的，剩下的读步同样可疑；摘掉写步还会留下悬空依赖 ⇒ 整份作废。"""
    plan, _ = _build([NOT_ADDRESSED, _wire(("nearby", "nearby.search"),
                                           ("edge-vehicle", "warning_light.close"))],
                     "可以，已为您执行")
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan.steps


def test_a_nudged_write_with_an_operation_cue_is_kept():
    """历史救回的真请求：「把后备箱打开」第一轮被判不受话，第二轮规划出开后备箱，照常走到确认。"""
    plan, _ = _build([NOT_ADDRESSED, _wire(("edge-vehicle", "trunk.open"))], "把后备箱打开")
    assert [s.intent for s in plan.steps] == ["trunk.open"], plan.steps
    assert plan.steps[0].require_confirm is True
    assert "_nudged_write_blocked" not in plan.plan_mode


def test_a_nudged_read_without_an_operation_cue_is_kept():
    plan, _ = _build([NOT_ADDRESSED, _wire(("nearby", "nearby.search"))], "欢乐海岸附近的餐厅")
    assert [s.intent for s in plan.steps] == ["nearby.search"], plan.steps


def test_a_first_attempt_write_is_not_touched():
    """首轮就规划出的写步没有被催过：隐式车控「我有点冷」照常执行。"""
    plan, calls = _build([_wire(("edge-vehicle", "hvac.inc"))], "我有点冷")
    assert [s.intent for s in plan.steps] == ["hvac.inc"], plan.steps
    assert calls["llm"] == 1
    assert "_nudged_write_blocked" not in plan.plan_mode
