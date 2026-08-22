"""候选集短路的**挂点**契约（QA Q2 残余，2026-08-19）。

engine 里有一对方向相反、判据同源的确定性短路，**都在 plan 构建之前**：

| 方向 | 条件 | 结果 |
|---|---|---|
| 负（I-052，2026-08-16 已上线） | 句首引用序数 **而候选集为空** | 诚实弃权 |
| 正（I-018/I-023，本批） | 引用当前候选 **而候选就在手里** | 确定性回答 |

## 为什么必须有这个文件

**I-052 那条守卫上线时没有任何 engine 层测试**——只测了 `references_a_candidate`
这个纯函数。于是「判据对不对」验过了，「挂点接上没接上」从来没验过。
本仓为这个区别付过三次学费（M2 Ledger / 商户 badcase / Q6 那次
「只在 handle 里加闸等于没加」，第三次那条注释就在原地而我在它下面又踩了一次）。

> 判据：**纯函数绿 ≠ 那条路径会走到它。** 所以这里跑的是 `engine.run`，
> 断言两件事：话术对，**且本轮一个 Agent 都没调**（那才叫「不进 Planner」）。

本文件顺带补上 I-052 的挂点欠账——两个方向一起钉，免得下次改动只保住一边。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.planning import PlanBuilder, _build_ref_maps
from orchestrator.cloud.session import SessionStore

_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": "cap_0001", "slots": {}, "depends_on": [],
     "slot_refs": {}},
]})

#: 逐字复刻 `nearby._item()` 经白名单裁剪后的形状（营业时间是 `open_today`，
#: **不是** `open_hours`——后者是本批修掉的那个猜错的名字）。
_CAFE_ITEMS = [
    {"id": "a", "name": "甲咖啡", "open_today": "07:00-21:00", "cost": "30"},
    {"id": "b", "name": "乙咖啡", "open_today": "09:00-23:00", "cost": "55"},
]


class _Cap:
    def __init__(self, intent):
        self.intent, self.slots, self.description = intent, [], intent
        self.heavy = False
        self.examples = []


def _agents():
    nearby = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="nearby", trust_level="third_party", latency_budget_ms=15000,
        deployment="cloud", requires_permissions=[], context_scopes=["location"],
        capabilities=[_Cap("nearby.search")], route_hints=[],
    ), endpoint="stub:50070")
    return [nearby]


class _Resp:
    def __init__(self, status=0, speech="", data=None, ui_card=None):
        self.status, self.speech, self.follow_up = status, speech, ""
        self.actions, self.ui_card, self.missing_slots = [], ui_card, []
        self.data = data


class _Spy:
    def __init__(self, unary_seq=None):
        self.unary_seq = list(unary_seq or [])
        self.unary_calls: list[str] = []
        #: 最后一次下发到 Agent 的 `step.meta`。下发面（`focus_candidate_set`）
        #: 只有在这里才看得见——**在 engine 外面断言那个静态方法，正是段 A
        #: 那处接线漏测的方式**。
        self.last_meta: dict = {}
        self.llm_calls = 0

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None):
        self.unary_calls.append(intent)
        self.last_meta = dict(meta or {})
        if self.unary_seq:
            return self.unary_seq.pop(0)
        return _Resp(speech=f"（{intent} 兜底）")

    async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None):
        """**流式与 unary 共用同一份响应队列**，这是刻意的。

        单步计划走 D0 流式直通而不是 executor——本仓为「新增挂点没枚举全部执行路径」
        踩过两次（M2 Ledger、商户 badcase）。首版这里只 yield 一句固定话术、不带
        `data`，于是第一轮压根没产生候选集，三条用例红成「有候选却走了弃权」，
        看起来像我的短路判据写反了。**装置少喂一条路径，读数就会指向错误的根因。**
        """
        self.unary_calls.append(intent)
        self.last_meta = dict(meta or {})
        yield ("final", self.unary_seq.pop(0) if self.unary_seq
               else _Resp(speech=f"（{intent} 兜底）"))

    async def llm(self, messages, **kwargs):
        self.llm_calls += 1
        if "任务编排器" in messages[0]["content"]:
            return _PLAN
        return "（聚合话术）"

    async def resolve(self, query="", intent="", top_k=1):
        return _agents()

    async def list_agents(self):
        return _agents()


def _engine(spy):
    return PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=SessionStore(redis_url=""),
    )


def _req(text, session_id):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="r1",
        is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _run(engine, text, session_id):
    async def collect():
        return [e async for e in engine.run(_req(text, session_id))]
    return asyncio.run(collect())


def _finals(events):
    return [e for e in events if e.get("kind") == "final"]


# ── 正方向：候选就在手里 ⇒ 确定性回答，且不进 Planner ─────────────────────

def test_aggregate_question_is_answered_deterministically_without_any_agent():
    """两轮端到端：搜一次咖啡店，再问「哪家最晚关门」。

    这条同时验证整条链：`extract_focus` 写候选集 → `update_focus` 跨轮接力 →
    `newest_candidate_set` 取组 → `candidate_query` 算。任何一环断了都会红。
    """
    spy = _Spy(unary_seq=[_Resp(
        speech="为您找到 2 家咖啡厅。",
        data={"items": _CAFE_ITEMS},
        ui_card={"type": "place_list"})])
    engine = _engine(spy)

    first = _run(engine, "附近的咖啡店", "sess-cand-1")
    assert _finals(first), "第一轮必须有 final"
    assert spy.unary_calls == ["nearby.search"]

    calls_before, llm_before = len(spy.unary_calls), spy.llm_calls
    second = _run(engine, "哪家最晚关门？", "sess-cand-1")

    final = _finals(second)[-1]
    assert "乙咖啡" in final["speech"] and "23:00" in final["speech"]
    assert "甲咖啡" not in final["speech"]
    # **这两条才是「挂点接上了」的证据**：不进 Planner ⇒ 零 Agent 调用、零 LLM 调用。
    assert len(spy.unary_calls) == calls_before, "短路轮不该调任何 Agent"
    assert spy.llm_calls == llm_before, "短路轮不该调 LLM（连规划都不该发生）"


def test_the_shortcut_is_verbatim_stable_across_sessions():
    """同一份候选 + 同一句话 ⇒ 逐字同一个答案。确定性的直接证据是零方差。"""
    speeches = set()
    for i in range(3):
        spy = _Spy(unary_seq=[_Resp(speech="为您找到 2 家。",
                                    data={"items": _CAFE_ITEMS})])
        engine = _engine(spy)
        sid = f"sess-cand-det-{i}"
        _run(engine, "附近的咖啡店", sid)
        speeches.add(_finals(_run(engine, "哪家最晚关门？", sid))[-1]["speech"])
    assert len(speeches) == 1, speeches


def test_a_normal_follow_up_still_reaches_the_planner():
    """误伤对照：有候选集在手，但问的不是聚合问题 ⇒ **照常进 Planner**。

    这条比正向那条更重要——短路挂在全部流量上，宽一格就是吞掉正常请求。
    """
    spy = _Spy(unary_seq=[
        _Resp(speech="为您找到 2 家咖啡厅。", data={"items": _CAFE_ITEMS}),
        _Resp(speech="甲咖啡评分 4.2。")])
    engine = _engine(spy)
    _run(engine, "附近的咖啡店", "sess-cand-2")

    calls_before = len(spy.unary_calls)
    _run(engine, "第一个怎么样", "sess-cand-2")
    assert len(spy.unary_calls) > calls_before, "普通追问被短路吞掉了"


# ── 负方向：I-052 的挂点欠账（此前只有纯函数测试）──────────────────────────

def test_ordinal_reference_with_no_candidates_abstains_without_planning():
    """干净会话里问「第一个营业到几点」⇒ 诚实弃权，**不进 Planner**。

    真栈原样复现过它不该发生的样子：无任何候选时答出「个芙云朵蛋糕(南山京基百纳店)，
    评分3.9，人均23.00，今日营业10:00-22:00」——一整条编出来的记录。
    """
    spy = _Spy()
    engine = _engine(spy)
    events = _run(engine, "第一个营业到几点？", "sess-cand-empty")

    final = _finals(events)[-1]
    assert "没有可以引用的列表" in final["speech"]
    assert spy.unary_calls == [], "弃权轮不该调任何 Agent"
    assert spy.llm_calls == 0, "弃权轮不该调 LLM"
    # 形态判据（同 CD3）：不许出现任何钟点，那就是编造的签名。
    assert ":" not in final["speech"]


def test_the_two_shortcuts_do_not_shadow_each_other():
    """有候选时问序数**不该**走弃权那条；无候选时问聚合**不该**走回答那条。

    两条守卫共用 `newest_candidate_set` 一个口径，所以它们必须是互补而非重叠的。
    """
    spy = _Spy(unary_seq=[_Resp(speech="找到 2 家。", data={"items": _CAFE_ITEMS}),
                          _Resp(speech="甲咖啡的详情。")])
    engine = _engine(spy)
    _run(engine, "附近的咖啡店", "sess-cand-3")
    got = _finals(_run(engine, "第一个营业到几点？", "sess-cand-3"))[-1]["speech"]
    assert "没有可以引用的列表" not in got, "有候选却走了弃权"

    spy2 = _Spy()
    engine2 = _engine(spy2)
    got2 = _finals(_run(engine2, "哪家最晚关门？", "sess-cand-4"))[-1]["speech"]
    assert "营业到" not in got2, "无候选却编出了一个营业时刻"


# ── I-030 跨组：engine 层的**接线守卫** ──────────────────────────────────
#
# ⚠ **这一段是反向验证逼出来的。** 组指代落地后我逐处打断判据验它们承不承重，
# 第一处「把 engine 改回 `newest_candidate_set`」跑出来是 **0 个用例** ——
# 挂点本身没有任何测试。纯函数全绿、接线断了照样绿，正是本文件开头那条
# 「纯函数绿 ≠ 那条路径会走到它」，我在写着这句话的文件里又欠了一次。

_MENU_MCD = [{"id": "m1", "name": "巨无霸", "price": "26.50"},
             {"id": "m2", "name": "麦辣鸡腿堡", "price": "19.50"}]
_MENU_LUCKIN = [{"id": "l1", "name": "美式", "price": "15.00"},
                {"id": "l2", "name": "生椰拿铁", "price": "16.00"}]


def _bridge_agents():
    """一个桥两条能力——**两家菜单必须能同时在场**，这才是 I-030 的场景。

    换成两轮 `nearby.search` 是造不出来的：合并键
    `(source_intent, purpose, is_fallback)` 相同，第二份会**取代**第一份
    （契约 §9.28「同源候选是取代不是叠加」）。
    """
    return [SimpleNamespace(manifest=SimpleNamespace(
        agent_id="mcp-bridge", trust_level="third_party", latency_budget_ms=15000,
        deployment="cloud", requires_permissions=[], context_scopes=["candidates"],
        capabilities=[_Cap("mcd.menu"), _Cap("luckin.menu")], route_hints=[],
    ), endpoint="stub:50080")]


class _BridgeSpy(_Spy):
    def __init__(self, unary_seq=None, plan_seq=()):
        super().__init__(unary_seq)
        self.plan_seq = list(plan_seq)

    async def llm(self, messages, **kwargs):
        self.llm_calls += 1
        if "任务编排器" in messages[0]["content"]:
            return self.plan_seq.pop(0) if self.plan_seq else _PLAN
        return "（聚合话术）"

    async def resolve(self, query="", intent="", top_k=1):
        return _bridge_agents()

    async def list_agents(self):
        return _bridge_agents()


def _cap_plan(intent):
    """按 **intent** 造计划，不写死 `cap_0001`。

    ⚠ **写死过一次，留痕**：`_capability_pairs` 是 `sorted(...)`，ref 编号按
    `(agent_id, intent)` 字典序而不是声明序 ⇒ `cap_0001` 是 `luckin.menu`。
    于是 fixture 把两家的 intent 和 label 配反了，段 A 那条断言红成
    「下发面选错组」，而下发面其实一直是对的。
    **装置自己算错，读数会指向一个不存在的缺陷**（同「A/B 之前先证明两臂真的
    不同」那条）。这里从真实映射反查，装置和被测系统用同一份口径。
    """
    _, pair_to_ref = _build_ref_maps(_bridge_agents())
    return json.dumps({"steps": [
        {"id": "s1", "capability_ref": pair_to_ref[("mcp-bridge", intent)],
         "slots": {}, "depends_on": [], "slot_refs": {}}]})


def _two_menus_in_session(session_id):
    """两轮真菜单落进同一个会话 → engine，返回 spy（第三轮用它记读数）。"""
    spy = _BridgeSpy(
        unary_seq=[
            _Resp(speech="麦当劳在售 2 款。",
                  data={"items": _MENU_MCD, "_candidate_label": "麦当劳"}),
            _Resp(speech="瑞幸可点 2 款。",
                  data={"items": _MENU_LUCKIN, "_candidate_label": "瑞幸"}),
        ],
        plan_seq=[_cap_plan("mcd.menu"), _cap_plan("luckin.menu")])
    engine = _engine(spy)
    _run(engine, "看看麦当劳有什么可以点的", session_id)
    _run(engine, "看看瑞幸有什么可以点的", session_id)
    return spy, engine


def test_naming_a_group_binds_to_it_end_to_end_not_to_the_newest():
    """**I-030 的端到端读数。** 修前这一句确定性地答「「生椰拿铁」16 元」
    ——瑞幸的第二个。商品名与价格都真实存在，没有一处对得上错，
    所以它比编造更难被发现。"""
    spy, engine = _two_menus_in_session("sess-i030-1")
    calls_before, llm_before = len(spy.unary_calls), spy.llm_calls

    got = _finals(_run(engine, "麦当劳的第二个多少钱", "sess-i030-1"))[-1]["speech"]

    assert "麦辣鸡腿堡" in got and "生椰拿铁" not in got
    assert len(spy.unary_calls) == calls_before, "短路轮不该调任何 Agent"
    assert spy.llm_calls == llm_before, "短路轮不该调 LLM"


def test_cross_group_comparison_end_to_end():
    spy, engine = _two_menus_in_session("sess-i030-2")
    got = _finals(_run(engine, "麦当劳的第二个和瑞幸的第二个哪个贵",
                       "sess-i030-2"))[-1]["speech"]
    assert "麦当劳的「麦辣鸡腿堡」" in got and "瑞幸的「生椰拿铁」" in got
    assert got.endswith("「麦辣鸡腿堡」更贵。")


def test_an_unnamed_ordinal_still_binds_to_the_newest_group():
    """**误伤对照**：没点名任何一家时，逐字还是旧行为（最新那一组）。
    组指代只在用户**点了名**时接管——它收窄的是错，不是放宽的口子。"""
    spy, engine = _two_menus_in_session("sess-i030-3")
    got = _finals(_run(engine, "第二个多少钱", "sess-i030-3"))[-1]["speech"]
    assert "生椰拿铁" in got


def test_the_downlink_each_step_gets_is_its_own_domain_s_group():
    """段 A 的接线守卫：`_apply_focus_meta` 逐步选组，不是全局取最新。

    走 engine 而不是直接调那个静态方法——**那正是这一段被漏掉的原因**。
    """
    spy, engine = _two_menus_in_session("sess-i030-4")
    spy.unary_seq.append(_Resp(speech="好的。"))
    spy.plan_seq.append(_cap_plan("mcd.menu"))          # 第三轮落回 mcd.menu

    _run(engine, "在麦当劳点一份", "sess-i030-4")

    payload = json.loads(spy.last_meta["focus_candidate_set"])
    assert payload["source_intent"] == "mcd.menu"
    assert [i["name"] for i in payload["items"]] == ["巨无霸", "麦辣鸡腿堡"]
