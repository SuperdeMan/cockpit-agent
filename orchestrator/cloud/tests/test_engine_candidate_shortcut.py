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
from orchestrator.cloud.planning import PlanBuilder
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
        self.llm_calls = 0

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None):
        self.unary_calls.append(intent)
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
