"""M1a submit_plan 结构化输出：PlanBuilder 双路径契约
（RFC docs/design/2026-07-24-m1a-provider-toolcall-rfc.md §4）。

覆盖：PLANNER_TOOLCALL 灰度门控、toolcall args 直入、同轮文本抢救、轮内降级到
JSON 路径、两轮全失败 degraded、schema 门控（clarify）、system prompt 追加段、
Struct 数字还原（clients._destruct_nums）。
"""
import asyncio
import json
import os
from unittest.mock import MagicMock

from orchestrator.cloud.planning import (
    PlanBuilder, _planner_system, _submit_plan_tools, _SUBMIT_PLAN_NAME,
    _TOOLCALL_SECTION,
)
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.context import WorkingSet


class MockAgent:
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
            cap.intent = intent
            cap.slots = []
            cap.description = ""
            cap.examples = []
            cap.heavy = False
            cap.require_confirm = False
            self.manifest.capabilities.append(cap)
        self.manifest.route_hints = []
        self.endpoint = "localhost:50060"


def _agents():
    return [MockAgent("navigation", ["navigation.search_poi"]),
            MockAgent("hvac", ["hvac.set"])]


async def _no_resolve(query, top_k=1):
    return []


def _build(builder, text="找家川菜馆"):
    return asyncio.run(builder.build(
        text, WorkingSet(catalog=_agents()), PlanContext(session_id="t")))


_ARGS_OK = {"complexity": "simple", "goal": "找川菜",
            "addressed": True,
            "steps": [{"id": "s1", "agent_id": "navigation",
                       "intent": "navigation.search_poi",
                       "slots": {"keyword": "川菜"}, "depends_on": [],
                       "slot_refs": {}}]}


class _SpyLLM:
    """llm_fn / llm_tool_fn 双通道 spy：记录调用次数与收到的 prompt/tools。"""

    def __init__(self, text_reply="", tool_reply=None, tool_exc=None):
        self.text_calls = 0
        self.tool_calls_n = 0
        self.last_tools = None
        self.last_system = ""
        self._text = text_reply
        self._tool = tool_reply          # (content, calls)
        self._tool_exc = tool_exc

    async def llm(self, messages):
        self.text_calls += 1
        return self._text

    async def llm_tools(self, messages, tools):
        self.tool_calls_n += 1
        self.last_tools = tools
        self.last_system = messages[0]["content"]
        if self._tool_exc:
            raise self._tool_exc
        return self._tool


def test_toolcall_args_direct(monkeypatch):
    """on + 合法 arguments → dict 直入校验，plan_mode=toolcall，JSON 通道零调用。"""
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    spy = _SpyLLM(tool_reply=("", [{"id": "c1", "name": _SUBMIT_PLAN_NAME,
                                    "arguments": _ARGS_OK}]))
    b = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve, llm_tool_fn=spy.llm_tools)
    plan = _build(b)
    assert plan.plan_mode == "toolcall"
    assert [s.intent for s in plan.steps] == ["navigation.search_poi"]
    assert plan.steps[0].slots["keyword"] == "川菜"
    assert spy.tool_calls_n == 1 and spy.text_calls == 0
    # 观测：raw_llm 保留工具 arguments 的 JSON 序列化（badcase 回看）
    assert json.loads(plan.raw_llm) == _ARGS_OK
    # named 强制 tool_choice 随请求下发
    assert spy.last_tools["tool_choice"]["function"]["name"] == _SUBMIT_PLAN_NAME
    # system prompt 带工具调用模式段
    assert "工具调用模式" in spy.last_system


def test_toolcall_salvage_when_model_ignores_tool(monkeypatch):
    """模型无视工具直接文本 JSON → 同轮抢救成功，plan_mode=toolcall_salvage。"""
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    spy = _SpyLLM(tool_reply=(json.dumps(_ARGS_OK, ensure_ascii=False), []))
    b = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve, llm_tool_fn=spy.llm_tools)
    plan = _build(b)
    assert plan.plan_mode == "toolcall_salvage"
    assert len(plan.steps) == 1
    assert spy.tool_calls_n == 1 and spy.text_calls == 0


def test_toolcall_falls_back_to_json_second_round(monkeypatch):
    """第 1 轮工具通道异常 → 第 2 轮 JSON 路径接住，plan_mode=toolcall_fallback。"""
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    spy = _SpyLLM(
        text_reply=json.dumps(_ARGS_OK, ensure_ascii=False),
        tool_exc=RuntimeError("provider 不认 tools"))
    b = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve, llm_tool_fn=spy.llm_tools)
    plan = _build(b)
    assert plan.plan_mode == "toolcall_fallback"
    assert len(plan.steps) == 1
    assert spy.tool_calls_n == 1 and spy.text_calls == 1   # 最坏 2 次=现状重试上限


def test_toolcall_degraded_after_both_rounds_fail(monkeypatch):
    """两轮全失败 → 走 _fallback，plan_mode=toolcall_degraded（空计划诚实降级）。"""
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    spy = _SpyLLM(text_reply="not json", tool_reply=("also not json", []))
    b = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve, llm_tool_fn=spy.llm_tools)
    plan = _build(b)
    assert plan.plan_mode == "toolcall_degraded"
    assert plan.steps == []


def test_toolcall_default_on(monkeypatch):
    """默认（不设 env）＝on（2026-07-24 泓舟拍板翻正）：注入 llm_tool_fn 即走工具通道。"""
    monkeypatch.delenv("PLANNER_TOOLCALL", raising=False)
    spy = _SpyLLM(tool_reply=("", [{"id": "c1", "name": _SUBMIT_PLAN_NAME,
                                    "arguments": _ARGS_OK}]))
    b = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve, llm_tool_fn=spy.llm_tools)
    plan = _build(b)
    assert plan.plan_mode == "toolcall"
    assert spy.tool_calls_n == 1 and spy.text_calls == 0


def test_toolcall_off_is_json_fallback_tier(monkeypatch):
    """显式 off＝JSON 纯文本回退档（对照/应急）：工具通道零调用、plan_mode=json。"""
    monkeypatch.setenv("PLANNER_TOOLCALL", "off")
    spy = _SpyLLM(text_reply=json.dumps(_ARGS_OK, ensure_ascii=False))
    b = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve, llm_tool_fn=spy.llm_tools)
    plan = _build(b)
    assert plan.plan_mode == "json"
    assert spy.tool_calls_n == 0 and spy.text_calls == 1
    assert len(plan.steps) == 1


def test_same_builder_rereads_toolcall_env_for_each_build(monkeypatch):
    """同一实例 off→on 热切：build 每轮重读 env，不依赖重建 PlanBuilder/进程。"""
    spy = _SpyLLM(
        text_reply=json.dumps(_ARGS_OK, ensure_ascii=False),
        tool_reply=("", [{"id": "c1", "name": _SUBMIT_PLAN_NAME,
                          "arguments": _ARGS_OK}]),
    )
    builder = PlanBuilder(
        llm_fn=spy.llm,
        registry_fn=_no_resolve,
        llm_tool_fn=spy.llm_tools,
    )

    monkeypatch.setenv("PLANNER_TOOLCALL", "off")
    first = _build(builder)
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    second = _build(builder)

    assert first.plan_mode == "json"
    assert second.plan_mode == "toolcall"
    assert spy.text_calls == 1
    assert spy.tool_calls_n == 1


def test_toolcall_on_without_tool_fn_uses_json(monkeypatch):
    """on 但未注入 llm_tool_fn（存量测试/spy 形态）→ JSON 路径，防御性回退。"""
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    spy = _SpyLLM(text_reply=json.dumps(_ARGS_OK, ensure_ascii=False))
    b = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve)
    plan = _build(b)
    assert plan.plan_mode == "json"
    assert len(plan.steps) == 1


def test_toolcall_addressed_false_is_legal_empty_plan(monkeypatch):
    """受话判定经工具通道：addressed=false + 空 steps 是合法计划（不触发重试）。"""
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    spy = _SpyLLM(tool_reply=("", [{"id": "c1", "name": _SUBMIT_PLAN_NAME,
                                    "arguments": {"addressed": False, "steps": []}}]))
    b = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve, llm_tool_fn=spy.llm_tools)
    plan = _build(b, "妈你到哪了")
    assert plan.plan_mode == "toolcall"
    assert plan.addressed is False and plan.steps == []
    assert spy.tool_calls_n == 1 and spy.text_calls == 0


def test_toolcall_numeric_slot_normalized_via_validated_steps(monkeypatch):
    """工具 arguments 里 slots 给数字 → _validated_steps str() 归一（int 24→"24"）。"""
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    args = {"addressed": True,
            "steps": [{"id": "s1", "agent_id": "hvac", "intent": "hvac.set",
                       "slots": {"temperature": 24}}]}
    spy = _SpyLLM(tool_reply=("", [{"id": "c1", "name": _SUBMIT_PLAN_NAME,
                                    "arguments": args}]))
    b = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve, llm_tool_fn=spy.llm_tools)
    plan = _build(b, "空调24度")
    assert plan.steps[0].slots["temperature"] == "24"


def test_toolcall_unwraps_provider_freeform_object_text_envelope(monkeypatch):
    """MiniMax may encode a free-form object in a synthetic ``$text`` field."""
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    args = {
        "addressed": True,
        "steps": [{
            "id": "s1",
            "agent_id": "navigation",
            "intent": "navigation.search_poi",
            "slots": {
                "$text": '{"query":"车规级固态电池量产良率对比","limit":5}',
            },
            "slot_refs": {"$text": "{}"},
        }],
    }
    spy = _SpyLLM(tool_reply=("", [{
        "id": "c1",
        "name": _SUBMIT_PLAN_NAME,
        "arguments": args,
    }]))
    b = PlanBuilder(
        llm_fn=spy.llm,
        registry_fn=_no_resolve,
        llm_tool_fn=spy.llm_tools,
    )

    plan = _build(b, "帮我查查车规级固态电池量产良率对比")

    assert plan.steps[0].slots == {
        "query": "车规级固态电池量产良率对比",
        "limit": "5",
    }
    assert plan.steps[0].slot_refs == {}


def test_wrong_tool_name_treated_as_protocol_failure(monkeypatch):
    """返回了别的工具名 → 非 submit_plan 不消费，按协议失败走第 2 轮 JSON。"""
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    spy = _SpyLLM(
        text_reply=json.dumps(_ARGS_OK, ensure_ascii=False),
        tool_reply=("", [{"id": "c1", "name": "other_tool", "arguments": _ARGS_OK}]))
    b = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve, llm_tool_fn=spy.llm_tools)
    plan = _build(b)
    assert plan.plan_mode == "toolcall_fallback"
    assert len(plan.steps) == 1


# ── schema / prompt 契约 ──

def test_submit_plan_tools_shape_and_confirm_absent(monkeypatch):
    """schema 顶层=现 JSON 协议顶层；无 require_confirm（确认权不在 LLM，M0a）；
    named tool_choice 强制。"""
    monkeypatch.delenv("CLARIFY_ENABLED", raising=False)
    spec = _submit_plan_tools()
    fn = spec["tools"][0]["function"]
    assert fn["name"] == _SUBMIT_PLAN_NAME
    props = fn["parameters"]["properties"]
    assert set(fn["parameters"]["required"]) == {"addressed", "steps"}
    step_props = props["steps"]["items"]["properties"]
    assert set(step_props) == {"id", "agent_id", "intent", "slots",
                               "depends_on", "slot_refs"}
    # slots 语义随字段走（真栈 B1-4：空 object 诱发省略追问丢继承槽）
    assert "继承的槽位" in step_props["slots"]["description"]
    assert "require_confirm" not in json.dumps(spec)
    assert spec["tool_choice"] == {"type": "function",
                                   "function": {"name": _SUBMIT_PLAN_NAME}}
    assert "clarify" not in props          # off 时 schema 不反向引导澄清


def test_submit_plan_tools_clarify_never_in_schema(monkeypatch):
    """clarify 恒不进 schema（真栈 B4-1：结构可见性把误澄清 0→~50%+，description
    约束压不回去）——触发面退回 prompt-only（=R4.4 验收原始形态），两路径对称。"""
    monkeypatch.setenv("CLARIFY_ENABLED", "on")
    props = _submit_plan_tools()["tools"][0]["function"]["parameters"]["properties"]
    assert "clarify" not in props


def test_toolcall_clarify_in_arguments_still_consumed(monkeypatch):
    """软 schema 下模型按 prompt 在 arguments 带 clarify（schema 外字段）→ 照常消费
    出澄清计划（协议兼容：schema 去声明不去消费）。"""
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    monkeypatch.setenv("CLARIFY_ENABLED", "on")
    args = {"addressed": True, "steps": [], "clarify": {
        "question": "要找餐厅还是加油站？",
        "options": [{"label": "餐厅", "send_text": "帮我找附近的餐厅"},
                    {"label": "加油站", "send_text": "帮我找附近的加油站"}]}}
    spy = _SpyLLM(tool_reply=("", [{"id": "c1", "name": _SUBMIT_PLAN_NAME,
                                    "arguments": args}]))
    b = PlanBuilder(llm_fn=spy.llm, registry_fn=_no_resolve, llm_tool_fn=spy.llm_tools)
    plan = _build(b, "帮我找个地方")
    assert plan.plan_mode == "toolcall"
    assert plan.steps == [] and plan.clarify
    assert plan.clarify["question"] == "要找餐厅还是加油站？"


def test_planner_system_toolcall_section_appended():
    assert _TOOLCALL_SECTION in _planner_system(toolcall=True)
    assert _TOOLCALL_SECTION not in _planner_system()
    # 追加段之外逐字一致（双路径共享领域协议=A/B 单变量）
    assert _planner_system(toolcall=True) == _planner_system() + _TOOLCALL_SECTION


# ── clients 解码层：Struct 数字还原 ──

def test_destruct_nums_restores_ints():
    from orchestrator.cloud.clients import Clients
    fixed = Clients._destruct_nums(
        {"steps": [{"slots": {"temperature": 24.0, "ratio": 0.5},
                    "depends_on": []}], "n": 3.0})
    assert fixed["steps"][0]["slots"]["temperature"] == 24
    assert isinstance(fixed["steps"][0]["slots"]["temperature"], int)
    assert fixed["steps"][0]["slots"]["ratio"] == 0.5      # 真小数不动
    assert fixed["n"] == 3 and isinstance(fixed["n"], int)


def test_malformed_slots_list_rejects_plan_atomically():
    """验收真栈抓到：模型偶发输出 slots=["item>拿铁","size>大杯"]（list 非 dict），
    旧代码 .items() 直接 AttributeError 崩掉整个 Handle——空响应、确认挂起蒸发。
    畸形 slots 步按无效步走计划原子拒绝（触发既有重试链），绝不崩 servicer。"""
    from types import SimpleNamespace
    from orchestrator.cloud.planning import PlanBuilder

    cap = SimpleNamespace(intent="shop.order", description="", slots=[],
                          require_confirm=True, heavy=False)
    manifest = SimpleNamespace(agent_id="mcp-bridge", trust_level="third_party",
                               latency_budget_ms=2000, requires_permissions=[],
                               capabilities=[cap], kind="agent", deployment="cloud",
                               context_scopes=[])
    amap = {"mcp-bridge": SimpleNamespace(manifest=manifest, endpoint="stub:1")}
    raw = [{"id": "s1", "agent_id": "mcp-bridge", "intent": "shop.order",
            "slots": ["item>拿铁", "size>大杯"]}]
    assert PlanBuilder._validated_steps(raw, amap) == []


def test_malformed_depends_and_slot_refs_normalized_not_crash():
    """depends_on/slot_refs 被模型输出成 ""（真栈日志实证）：字符串 depends_on 会被
    逐字符迭代、非 dict slot_refs 在 executor 处同款崩——归一为空后步骤照常成立。"""
    from types import SimpleNamespace
    from orchestrator.cloud.planning import PlanBuilder

    cap = SimpleNamespace(intent="shop.order", description="", slots=[],
                          require_confirm=True, heavy=False)
    manifest = SimpleNamespace(agent_id="mcp-bridge", trust_level="third_party",
                               latency_budget_ms=2000, requires_permissions=[],
                               capabilities=[cap], kind="agent", deployment="cloud",
                               context_scopes=[])
    amap = {"mcp-bridge": SimpleNamespace(manifest=manifest, endpoint="stub:1")}
    raw = [{"id": "s1", "agent_id": "mcp-bridge", "intent": "shop.order",
            "slots": {"item": "拿铁"}, "depends_on": "", "slot_refs": ""}]
    steps = PlanBuilder._validated_steps(raw, amap)
    assert len(steps) == 1
    assert steps[0].depends_on == [] and steps[0].slot_refs == {}


def test_depends_on_with_unhashable_elements_does_not_crash_the_planner():
    """`depends_on: [["s1"]]` —— **容器类型守住了，元素类型没守住。**

    上一条测试防的是 `depends_on: ""`（非 list，会被逐字符迭代）。这条是它的下一层：
    `[["s1"]]` **是** list，isinstance 检查照过，直到 `dep in valid_ids` 拿 list 去
    hash 才崩 `TypeError: unhashable type: 'list'`。而 `_parse_and_validate_data`
    在 `build()` 里没有任何异常防护——**一次畸形模型输出就能让整条规划抛出去**。
    真栈实证：2026-08-03 一次 140 选集的 L1 跑批被它整趟打死。

    模型输出是不可信输入：防御要一路防到**会被拿去 hash / 拿去 split 的那个值**，
    不是防到最外层容器为止。
    """
    from types import SimpleNamespace
    from orchestrator.cloud.planning import PlanBuilder

    cap = SimpleNamespace(intent="nearby.search", description="", slots=[],
                          require_confirm=False, heavy=False)
    manifest = SimpleNamespace(agent_id="nearby", trust_level="first_party",
                               latency_budget_ms=2000, requires_permissions=[],
                               capabilities=[cap], kind="agent", deployment="cloud",
                               context_scopes=[])
    amap = {"nearby": SimpleNamespace(manifest=manifest, endpoint="stub:1")}
    raw = [{"id": "s1", "agent_id": "nearby", "intent": "nearby.search",
            "slots": {"keyword": "川菜"}, "depends_on": [["s0"], {"a": 1}, 7, None],
            "slot_refs": {}}]
    steps = PlanBuilder._validated_steps(raw, amap)
    assert len(steps) == 1
    # 非 str 元素无论如何都匹配不上 id（id 本身是 str），丢弃与「保留后被过滤掉」
    # 结果相同——但 str(["s0"]) == "['s0']" 会在日志里留下一个不存在的 id，更误导。
    assert steps[0].depends_on == []


def test_a_real_dependency_survives_the_element_level_normalisation():
    """收紧不能把正常的依赖一起收掉——否则「防住崩溃」会变成「悄悄丢依赖」。"""
    from types import SimpleNamespace
    from orchestrator.cloud.planning import PlanBuilder

    def _agent(agent_id, intent):
        cap = SimpleNamespace(intent=intent, description="", slots=[],
                              require_confirm=False, heavy=False)
        manifest = SimpleNamespace(agent_id=agent_id, trust_level="first_party",
                                   latency_budget_ms=2000, requires_permissions=[],
                                   capabilities=[cap], kind="agent",
                                   deployment="cloud", context_scopes=[])
        return SimpleNamespace(manifest=manifest, endpoint="stub:1")

    amap = {"nearby": _agent("nearby", "nearby.search")}
    amap["shop"] = _agent("shop", "shop.order")
    raw = [{"id": "s1", "agent_id": "nearby", "intent": "nearby.search",
            "slots": {}, "depends_on": [], "slot_refs": {}},
           {"id": "s2", "agent_id": "shop", "intent": "shop.order", "slots": {},
            "depends_on": ["s1", ["junk"]], "slot_refs": {}}]
    steps = PlanBuilder._validated_steps(raw, amap)
    assert [s.depends_on for s in steps] == [[], ["s1"]]


def test_slot_ref_values_that_are_not_strings_are_dropped_at_plan_time():
    """同族的第二处，而且更危险——它崩在 **executor 执行期**不是规划期。

    `slot_refs` 只保证是 dict，value 类型没保证；`executor._resolve_ref` 对它做
    `ref_path.split(".")`，非 str value 直接 AttributeError。JSON object 的 key 恒为
    str，但 value 可以是任意 JSON 值——所以要防的是 value。

    与 depends_on 同一判据：非 str 直接丢，不做 str() 转换——`"['s1', 'data']"`
    不是有效引用路径，转了只会让 `_resolve_ref` 返回 None 并打一条误导性日志。
    """
    from types import SimpleNamespace
    from orchestrator.cloud.planning import PlanBuilder

    cap = SimpleNamespace(intent="shop.order", description="", slots=[],
                          require_confirm=False, heavy=False)
    manifest = SimpleNamespace(agent_id="shop", trust_level="first_party",
                               latency_budget_ms=2000, requires_permissions=[],
                               capabilities=[cap], kind="agent", deployment="cloud",
                               context_scopes=[])
    amap = {"shop": SimpleNamespace(manifest=manifest, endpoint="stub:1")}
    raw = [{"id": "s1", "agent_id": "shop", "intent": "shop.order", "slots": {},
            "depends_on": [],
            "slot_refs": {"poi_id": ["s0", "data", "id"], "name": 7,
                          "ok": "s0.data.name", "nil": None}}]
    steps = PlanBuilder._validated_steps(raw, amap)
    assert len(steps) == 1
    # 合法的那条留下，会让 executor 崩的三条丢掉。
    assert steps[0].slot_refs == {"ok": "s0.data.name"}


def _two_step_amap():
    """nearby.search（产出方）+ nearby.detail（消费方）的最小 agent_map。"""
    from types import SimpleNamespace
    caps = [SimpleNamespace(intent=i, description="", slots=[],
                            require_confirm=False, heavy=False)
            for i in ("nearby.search", "nearby.detail")]
    manifest = SimpleNamespace(agent_id="nearby", trust_level="first_party",
                               latency_budget_ms=3000, requires_permissions=[],
                               capabilities=caps, kind="agent", deployment="cloud",
                               context_scopes=[])
    return {"nearby": SimpleNamespace(manifest=manifest, endpoint="stub:1")}


def test_slot_ref_to_another_step_derives_the_missing_depends_on_edge():
    """**引用了另一步的输出就是依赖的定义。**

    真栈实测（`cp.dep.search-then-detail`）：模型把引用写全了
    （`slot_refs={"poi_id": "s1.data.items.0.id"}`），`depends_on` 却是空的。
    执行侧 `_topo_layers` 只看 `depends_on` → 两步排进同一层**并行下发** →
    s2 去取 s1 结果时 s1 还没回来 → 引用解析成 None，那串路径当成真 POI id 发下去。
    这不是路由错，是**计划自相矛盾**，与「depends_on 非 list 归空」同一族归一。
    """
    from orchestrator.cloud.planning import PlanBuilder

    raw = [{"id": "s1", "agent_id": "nearby", "intent": "nearby.search",
            "slots": {"cuisine": "火锅"}, "depends_on": [], "slot_refs": {}},
           {"id": "s2", "agent_id": "nearby", "intent": "nearby.detail",
            "slots": {}, "depends_on": [],
            "slot_refs": {"poi_id": "s1.data.items.0.id"}}]
    steps = PlanBuilder._validated_steps(raw, _two_step_amap())
    assert [s.depends_on for s in steps] == [[], ["s1"]]


def test_ref_written_into_slots_also_derives_the_edge():
    """第二种 wire 形态：引用直接写在 `slots` 里（含 `${...}` 包裹）。"""
    from orchestrator.cloud.planning import PlanBuilder

    raw = [{"id": "s1", "agent_id": "nearby", "intent": "nearby.search"},
           {"id": "s2", "agent_id": "nearby", "intent": "nearby.detail",
            "slots": {"poi_id": "${s1.data.items.0.id}"}}]
    steps = PlanBuilder._validated_steps(raw, _two_step_amap())
    assert steps[1].depends_on == ["s1"]


def test_derivation_never_invents_an_edge():
    """三条边界一起守：已声明的不重复补、自引用不补、引用不存在的步不补。

    补依赖是**归一**不是发明——被引用的步必须真实存在于本计划。
    """
    from orchestrator.cloud.planning import PlanBuilder

    raw = [{"id": "s1", "agent_id": "nearby", "intent": "nearby.search",
            "slot_refs": {"x": "s1.data.a"}},                    # 自引用
           {"id": "s2", "agent_id": "nearby", "intent": "nearby.detail",
            "depends_on": ["s1"], "slot_refs": {"poi_id": "s1.data.items.0.id"}}]
    steps = PlanBuilder._validated_steps(raw, _two_step_amap())
    assert steps[0].depends_on == []                              # 自引用不成环
    assert steps[1].depends_on == ["s1"]                          # 不重复

    raw2 = [{"id": "s2", "agent_id": "nearby", "intent": "nearby.detail",
             "slot_refs": {"poi_id": "s9.data.items.0.id"}}]      # s9 不在计划里
    assert PlanBuilder._validated_steps(raw2, _two_step_amap())[0].depends_on == []


def test_literal_slot_values_are_not_mistaken_for_refs():
    """普通槽值不得被当成引用——判据是 `<步骤id>.data.` 这个形状，不是「含点号」。"""
    from orchestrator.cloud.planning import PlanBuilder

    raw = [{"id": "s1", "agent_id": "nearby", "intent": "nearby.search"},
           {"id": "s2", "agent_id": "nearby", "intent": "nearby.detail",
            "slots": {"keyword": "s1.data 咖啡馆", "note": "3.5 分以上"}}]
    steps = PlanBuilder._validated_steps(raw, _two_step_amap())
    assert steps[1].depends_on == []
