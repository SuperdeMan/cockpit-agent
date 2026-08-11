"""重试策略表驱动（B5 §3）的契约与验收判据。

四组：
1. **清单≡代码表**——方案附录 A 的规则清单是重构前的行为快照，代码表必须与它逐条
   对齐（§5 第 1 条）。表变了而清单没变、或反过来，都会红。
2. **表本身的结构不变量**——谓词覆盖全部 trigger、模板无孤儿、声明序即求值序。
3. **消融通道是活的**——§3.2 第 4 条的前置：先用一条**已知有效**的策略
   （`salvage_wire_accepted`，live 双臂 +34.2pp）证明「关掉它读数确实变化」，
   否则消融开关本身就是个摆设，用它做的任何 A/B 都在比两条相同的臂
   （§4.3「A/B 之前先证明两臂真的不同」）。
4. **场景矩阵**——21 条覆盖全部 13 条策略的输入，钉住「命中了哪几条 / 调了几次
   LLM / plan_mode 是什么」。表驱动落地时这 21 条与重构前逐字一致（差分取证见
   方案 §6），此后它们是防回归的网。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from unittest.mock import MagicMock

import pytest

from orchestrator.cloud.context import Focus, WorkingSet
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder, _RETRY_PREDICATES
from orchestrator.cloud.retry_policy import (
    CORRECTION_DEFAULT, CORRECTION_TEMPLATES, POLICY_BY_NAME, RETRY_POLICIES,
    STAGE_ACCEPT, STAGE_GUARD, STAGE_TAIL, WIRE_ALL, WIRE_SALVAGE, WIRE_TOOL,
    PlanAttemptState, RetryController, TriggerKind, disabled_policies,
    render_correction,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
_PLAN_DOC = os.path.join(
    _ROOT, "docs", "design", "2026-08-10-b5-planner-retry-stream-refactor.md")

_WIRE_WORDS = {"tool": WIRE_TOOL, "全部": WIRE_ALL,
               "salvage": frozenset({WIRE_SALVAGE})}


def _inventory_rows() -> list[dict]:
    """解析方案附录 A 的规则清单表。

    ⚠ 扫描类断言写完先验证它扫到的集合非空且合理（§4.3，B3 那次覆盖断言第一版
    只认绝对 import，14 个 Agent 全被误判）——故本函数的调用方**先断行数**。
    """
    with open(_PLAN_DOC, encoding="utf-8") as f:
        doc = f.read()
    rows = []
    for line in doc.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 8 or not re.fullmatch(r"\d+", cells[0]):
            continue
        name = cells[1].strip("`")
        if name not in POLICY_BY_NAME:      # 只认策略行，不误收其它编号表
            continue
        template = cells[6]
        rows.append({
            "name": name,
            "stage": cells[2],
            "attempt_limit": int(cells[4]),
            "wire_modes": _WIRE_WORDS[cells[5]],
            "correction_template": (
                "" if template.startswith("—")
                else template.split("（")[0].strip("`")),
            "correction_mode": (CORRECTION_DEFAULT if "default" in template
                                else "override"),
            "next_wire": "" if cells[7].startswith("—") else cells[7].strip("`"),
        })
    return rows


# ── 1. 清单≡代码表 ─────────────────────────────────────────────────────────

def test_inventory_parser_actually_found_the_table():
    """先证明解析器不是在空扫——扫不全的结构断言比没有更糟。"""
    assert len(_inventory_rows()) == len(RETRY_POLICIES) == 13


def test_inventory_matches_the_code_table():
    """逐条逐列比对，不只比 name 集合。

    只比 name 会漏掉「清单说 attempt_limit=1、代码写了 2」这种最容易漂的差异。
    """
    by_name = {row["name"]: row for row in _inventory_rows()}
    assert set(by_name) == set(POLICY_BY_NAME), "清单与代码表的策略名集合不一致"
    for policy in RETRY_POLICIES:
        row = by_name[policy.name]
        assert row["stage"] == policy.stage, policy.name
        assert row["attempt_limit"] == policy.attempt_limit, policy.name
        assert row["wire_modes"] == policy.wire_modes, policy.name
        assert row["correction_template"] == policy.correction_template, policy.name
        assert row["correction_mode"] == policy.correction_mode, policy.name
        assert row["next_wire"] == policy.next_wire, policy.name


def test_inventory_row_order_matches_declaration_order():
    """声明顺序即求值顺序——清单的行序是它的一部分，不是排版。"""
    assert [row["name"] for row in _inventory_rows()] == [
        policy.name for policy in RETRY_POLICIES]


# ── 2. 表的结构不变量 ──────────────────────────────────────────────────────

def test_every_trigger_has_a_predicate():
    """缺一条谓词 = 那条守卫从此静默不生效。构造时就抛，不等运行期。"""
    assert set(_RETRY_PREDICATES) == set(TriggerKind)
    with pytest.raises(ValueError, match="缺少谓词"):
        RetryController({})


def test_policy_names_and_triggers_are_unique():
    assert len({p.name for p in RETRY_POLICIES}) == len(RETRY_POLICIES)
    assert len({p.trigger for p in RETRY_POLICIES}) == len(RETRY_POLICIES)


def test_no_orphan_correction_templates():
    """模板注册表里不许有没人引用的条目（死文案会被当成还在生效的）。"""
    used = {p.correction_template for p in RETRY_POLICIES if p.correction_template}
    assert used == set(CORRECTION_TEMPLATES)


def test_correction_templates_render_without_leftover_placeholders():
    """模板里有 JSON 花括号——渲染必须用 `$name` 占位，不能被 format 吃掉。"""
    focus = MagicMock()
    focus.last_intent = "nearby.search"
    state = PlanAttemptState(
        attempt=0, wire_mode="toolcall", data={}, parsed=None,
        working_set=MagicMock(focus=focus))
    for key in CORRECTION_TEMPLATES:
        text = render_correction(key, state)
        assert "$" not in text, key
        assert text.startswith("\n\n"), key
    assert "nearby.search" in render_correction("focus_dependent", state)
    assert "{\"addressed\":true" in render_correction("clarify_goal_with_steps", state)


def test_stage_membership_is_exactly_the_three_known_stages():
    assert {p.stage for p in RETRY_POLICIES} == {
        STAGE_GUARD, STAGE_ACCEPT, STAGE_TAIL}


def test_unknown_ablation_name_is_not_swallowed(monkeypatch):
    """拼错策略名却按「什么都没关」跑，读数会被读成「关了也没变化」。"""
    monkeypatch.setenv("PLANNER_RETRY_DISABLE", "salvage_wire_acceptd")
    with pytest.raises(ValueError, match="未知策略名"):
        disabled_policies()
    monkeypatch.setenv("PLANNER_RETRY_DISABLE", "")
    assert disabled_policies() == frozenset()


def test_planning_no_longer_holds_an_inline_guard_chain():
    """守卫链已经变成数据；本条挡住「顺手再加一个 elif」。"""
    with open(os.path.join(_ROOT, "orchestrator", "cloud", "planning.py"),
              encoding="utf-8") as f:
        src = f.read()
    for banned in ("semantic_guard_retry", "clarification_tool_retry",
                   "plan_only_tool_retry"):
        assert banned not in src, (
            f"planning.py 里出现 `{banned}`——重试规则要进 retry_policy 的表，"
            "不要在主循环里重新长出布尔状态机")


# ── 3. 场景矩阵与消融 ──────────────────────────────────────────────────────

class _MockAgent:
    def __init__(self, agent_id, intents):
        self.manifest = MagicMock()
        self.manifest.agent_id = agent_id
        self.manifest.capabilities = []
        self.manifest.latency_budget_ms = 5000
        self.manifest.kind = "agent"
        self.manifest.deployment = "cloud"
        self.manifest.requires_permissions = []
        self.manifest.trust_level = "first_party"
        for intent in intents:
            cap = MagicMock()
            cap.intent, cap.slots, cap.description = intent, [], ""
            cap.examples, cap.heavy, cap.require_confirm = [], False, False
            self.manifest.capabilities.append(cap)
        self.manifest.route_hints = []
        self.endpoint = "localhost:50060"


def _agents():
    # cap_0001 chitchat.talk / 0002 hvac.off / 0003 hvac.on / 0004 nearby.search
    # cap_0005 window.close / 0006 window.open（按 (agent_id, intent) 排序编号）
    return [_MockAgent("chitchat", ["chitchat.talk"]),
            _MockAgent("hvac", ["hvac.off", "hvac.on"]),
            _MockAgent("nearby", ["nearby.search"]),
            _MockAgent("window", ["window.close", "window.open"])]


async def _no_resolve(query="", intent="", top_k=1):
    return []


class _Spy:
    def __init__(self, text_replies=(), tool_replies=()):
        self._text, self._tool = list(text_replies), list(tool_replies)
        self.text_calls = self.tool_calls = 0

    async def llm(self, messages):
        self.text_calls += 1
        return self._text.pop(0) if self._text else ""

    async def llm_tools(self, messages, tools):
        self.tool_calls += 1
        return self._tool.pop(0) if self._tool else ("", None)


def _step(ref="cap_0004", sid="s1"):
    return {"id": sid, "capability_ref": ref, "slots": {},
            "depends_on": [], "slot_refs": {}}


def _args(goal="找川菜", steps=None):
    return {"complexity": "simple", "goal": goal, "addressed": True,
            "steps": [_step()] if steps is None else steps}


def _tool(args):
    return ("", [{"name": "submit_plan", "arguments": args}])


_EMPTY_STEPS = {"complexity": "simple", "goal": "无需动作",
                "addressed": True, "steps": []}
_NOT_ADDRESSED = {"complexity": "simple", "goal": "旁听",
                  "addressed": False, "steps": []}
_CLARIFY = {"addressed": True, "steps": [], "clarify": {
    "question": "要导航还是搜索？",
    "options": [{"label": "导航", "send_text": "导航去华润大厦"},
                {"label": "搜索", "send_text": "搜索华润大厦"}]}}

# (case, text, focus, prefs, tool_replies, text_replies,
#  期望命中的策略, 期望 tool/text 调用数, 期望 plan_mode)
_MATRIX = [
    ("plain", "附近有什么川菜馆", None, {}, [_tool(_args())], [],
     [], (1, 0), "toolcall"),
    ("salvage_then_retry", "附近有什么川菜馆", None, {},
     [(json.dumps(_args()), None), _tool(_args())], [],
     ["salvage_wire_accepted"], (2, 0), "toolcall"),
    ("salvage_kept", "附近有什么川菜馆", None, {},
     [(json.dumps(_args()), None), (json.dumps(_args()), None)], [],
     ["salvage_wire_accepted"], (2, 0), "toolcall_salvage"),
    ("protocol_broken", "附近有什么川菜馆", None, {}, [("", None)],
     [json.dumps(_args())], [], (1, 1), "toolcall_fallback"),
    ("schema_invalid", "附近有什么川菜馆", None, {},
     [_tool({"addressed": True, "steps": [_step("nope")]}), _tool(_args())], [],
     ["schema_validation_failed"], (2, 0), "toolcall"),
    ("no_action_twice", "空调先别关，等我说了再关", None, {},
     [_tool(_EMPTY_STEPS), _tool(_EMPTY_STEPS)], [],
     ["no_action_unconfirmed", "no_action_unconfirmed"], (2, 0),
     "toolcall_no_action"),
    ("clarify_goal_with_steps", "华润大厦", None, {},
     [_tool(_args(goal="需要澄清用户想对华润大厦做什么")), _tool(_CLARIFY)], [],
     ["clarify_goal_with_steps"], (2, 0), "toolcall"),
    ("clarification_contract_violated", "华润大厦", None, {},
     [_tool(_args(goal="需要澄清用户想对华润大厦做什么")), _tool(_args())], [],
     ["clarify_goal_with_steps", "clarification_contract_violated"], (2, 0),
     "toolcall_degraded"),
    ("directive_not_addressed", "记住，明天八点提醒我开会", None, {},
     [_tool(_NOT_ADDRESSED), _tool(_args())], [],
     ["directive_not_addressed"], (2, 0), "toolcall"),
    ("explicit_not_addressed", "附近有什么川菜馆", None, {},
     [_tool(_NOT_ADDRESSED), _tool(_args())], [],
     ["explicit_input_not_addressed"], (2, 0), "toolcall"),
    ("voice_not_addressed_kept", "附近有什么川菜馆", None,
     {"input_source": "voice_handsfree"}, [_tool(_NOT_ADDRESSED)], [],
     [], (1, 0), "toolcall"),
    ("open_close_inverted", "关闭车窗", None, {},
     [_tool(_args(goal="开窗", steps=[_step("cap_0006")])),
      _tool(_args(goal="关窗", steps=[_step("cap_0005")]))], [],
     ["open_close_polarity_inverted"], (2, 0), "toolcall"),
    ("multi_action_omitted", "打开空调然后找家川菜馆", None, {},
     [_tool(_args(goal="打开空调然后找川菜")),
      _tool(_args(goal="打开空调然后找川菜",
                  steps=[_step("cap_0003"), _step("cap_0004", "s2")]))], [],
     ["multi_action_omitted"], (2, 0), "toolcall"),
    ("focus_dependent", "换一个", Focus(last_intent="nearby.search"), {},
     [_tool(_args(steps=[_step("cap_0002")])), _tool(_args())], [],
     ["focus_dependent_conflict"], (2, 0), "toolcall"),
    ("focused_list_batch", "换一批",
     Focus(last_intent="nearby.search", last_choice_purpose="list"), {},
     [_tool(_args(steps=[_step("cap_0002")])), _tool(_args())], [],
     ["focused_list_batch_conflict"], (2, 0), "toolcall"),
    ("complete_conditional", "如果明天下雨就提醒我带伞", None, {},
     [_tool(_CLARIFY),
      _tool({"complexity": "adaptive", "goal": "如果明天下雨就提醒我带伞",
             "addressed": True, "steps": [_step()]})], [],
     ["complete_conditional_clarified"], (2, 0), "toolcall"),
    ("plan_only_contract_violated", "如果明天下雨就提醒我带伞", None, {},
     [_tool(_CLARIFY), _tool({**_args(goal="带伞"), "extra_key": 1})], [],
     ["complete_conditional_clarified", "plan_only_contract_violated"], (2, 0),
     "toolcall_degraded"),
]


def _build(text, focus, prefs, tool_replies, text_replies):
    spy = _Spy(text_replies, tool_replies)
    builder = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve,
                          llm_tool_fn=spy.llm_tools)
    ctx = PlanContext(session_id="retry-matrix")
    ctx.prefs = dict(prefs)
    plan = asyncio.run(builder.build(
        text, WorkingSet(catalog=_agents(), focus=focus), ctx))
    return plan, spy


@pytest.fixture(autouse=True)
def _planner_env(monkeypatch):
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    monkeypatch.setenv("PLANNER_TOOLCALL_SALVAGE_RETRY", "on")
    monkeypatch.setenv("PLANNER_RETRY_DISABLE", "")
    monkeypatch.setenv("SKILLS_MODE", "off")
    monkeypatch.setenv("EXEMPLARS_MODE", "off")


@pytest.mark.parametrize("case", _MATRIX, ids=[row[0] for row in _MATRIX])
def test_scenario_matrix(case):
    (_name, text, focus, prefs, tool_replies, text_replies,
     want_policies, want_calls, want_mode) = case
    plan, spy = _build(text, focus, prefs, tool_replies, text_replies)

    assert list(plan.retry_policies) == want_policies
    assert (spy.tool_calls, spy.text_calls) == want_calls
    assert plan.plan_mode == want_mode


def test_matrix_exercises_every_policy():
    """矩阵没覆盖到的策略，上面那 17 条对它一个字都没说。"""
    covered = {name for row in _MATRIX for name in row[6]}
    assert covered == set(POLICY_BY_NAME), (
        f"未被矩阵触发: {sorted(set(POLICY_BY_NAME) - covered)}")


def test_ablation_channel_is_live(monkeypatch):
    """§3.2 第 4 条：先证明消融通道是活的，再拿它做任何 A/B。

    选 `salvage_wire_accepted` 是因为它有**已知读数**（gate L1 双臂 117 样本
    51.3%→85.5%，p=2.3e-08）：关掉它必须看得见变化，看不见就说明开关是摆设。
    """
    replies = [(json.dumps(_args()), None), _tool(_args())]

    plan_on, spy_on = _build("附近有什么川菜馆", None, {}, list(replies), [])
    assert spy_on.tool_calls == 2 and plan_on.plan_mode == "toolcall"

    monkeypatch.setenv("PLANNER_RETRY_DISABLE", "salvage_wire_accepted")
    plan_off, spy_off = _build("附近有什么川菜馆", None, {}, list(replies), [])

    assert spy_off.tool_calls == 1, "关掉策略后仍重试了——消融开关没生效"
    assert plan_off.plan_mode == "toolcall_salvage"
    assert plan_off.retry_policies == []


def test_ablation_matches_the_dedicated_env_switch(monkeypatch):
    """同一条策略有两个开关，两者必须说同一件事。

    `PLANNER_TOOLCALL_SALVAGE_RETRY=off` 是 A/B 时用的专用开关，
    `PLANNER_RETRY_DISABLE=salvage_wire_accepted` 是通用消融。二者在
    「不再重试工具通道」这件事上应当一致——不一致就说明通用消融关的不是那条。
    """
    replies = [(json.dumps(_args()), None), _tool(_args())]

    monkeypatch.setenv("PLANNER_TOOLCALL_SALVAGE_RETRY", "off")
    plan_env, spy_env = _build("附近有什么川菜馆", None, {}, list(replies), [])

    monkeypatch.setenv("PLANNER_TOOLCALL_SALVAGE_RETRY", "on")
    monkeypatch.setenv("PLANNER_RETRY_DISABLE", "salvage_wire_accepted")
    plan_abl, spy_abl = _build("附近有什么川菜馆", None, {}, list(replies), [])

    assert spy_env.tool_calls == spy_abl.tool_calls == 1
    assert plan_env.plan_mode == plan_abl.plan_mode == "toolcall_salvage"


def test_ablating_a_guard_lets_the_bad_plan_through(monkeypatch):
    """消融对守卫段同样生效：关掉极性守卫，反向计划就直接落地了。

    这条同时是那条守卫**确实在干活**的证明——它不是恒不命中的装饰。
    """
    replies = [_tool(_args(goal="开窗", steps=[_step("cap_0006")])),
               _tool(_args(goal="关窗", steps=[_step("cap_0005")]))]

    monkeypatch.setenv("PLANNER_RETRY_DISABLE", "open_close_polarity_inverted")
    plan, spy = _build("关闭车窗", None, {}, list(replies), [])

    assert spy.tool_calls == 1
    assert [s.intent for s in plan.steps] == ["window.open"], "极性守卫没被关掉"
