"""C4 · 会话事实读出口的**挂点**契约（MiniMax QA 修复批第 2 批，2026-08-28）。

engine 里现在有四条方向不同、判据同源的确定性短路，**全都在 plan 构建之前**：

| 出口 | 条件 | 结果 |
|---|---|---|
| 候选弃权（I-052） | 句首引用序数 **而候选集为空** | 诚实弃权 |
| 候选聚合（I-018/I-023） | 引用当前候选 **而候选就在手里** | 确定性回答 |
| **挂起状态**（C4-B ①） | 问「还有没有待确认的」 | 念挂起表 |
| **数据源**（C4-B ②） | 问「数据哪来的」**且账本里有** | 念来源账本 |
| **执行史**（C4-B ③） | 问「刚才执行了什么」 | 念执行账本 |

## 为什么必须有这个文件

**判据是纯函数、验证它很便宜，但「那条路径会不会走到它」从来不是自动成立的。**
本仓为这个区别付过四次学费（M2 Ledger / 商户 badcase / Q6「只在 handle 里加闸」/
I-052 挂点零测试）。而这一批的病**本身就是同一个形态**：Q6 把审计闸建在
chitchat 里，2026-08-26 QA 的 T37 被 planner 接给了 reminder.list——
**判据一直是对的，够不着而已。**

> 所以这里跑的是 `engine.run`，每条都断言三件事：话术对、**本轮一个 Agent 都没调**、
> **一次 LLM 都没调**（那才叫「不进 Planner」）。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine, _turn_sources
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import SessionState
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore

_PLAN = json.dumps({"steps": [
    {"id": "s1", "capability_ref": "cap_0001", "slots": {}, "depends_on": [],
     "slot_refs": {}},
]})

_STOCK_CARD = {
    "type": "stock_quote", "name": "宁德时代", "symbol": "300750",
    "price": "251.30", "market_time": "20260826",
    "_prov": {"mode": "real", "vendor": "tushare",
              "fetched_at": "2026-08-26T19:23:00+08:00",
              "data_time": "20260826", "data_time_label": "行情时间"},
}


class _Cap:
    def __init__(self, intent):
        self.intent, self.slots, self.description = intent, [], intent
        self.heavy = False
        self.examples = []


def _agents():
    info = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="info", trust_level="internal", latency_budget_ms=15000,
        deployment="cloud", requires_permissions=[], context_scopes=[],
        capabilities=[_Cap("info.stock")], route_hints=[],
    ), endpoint="stub:50060")
    return [info]


class _Resp:
    def __init__(self, status=0, speech="", data=None, ui_card=None):
        self.status, self.speech, self.follow_up = status, speech, ""
        self.actions, self.ui_card, self.missing_slots = [], ui_card, []
        self.data = data


class _Spy:
    """带**真实账本**的替身：`append_turn` 存什么，`get_session` 就读什么。

    ⚠ 这一点是刻意的。替身若只接收 `sources` 而不还回来，读出口那几条断言就是在
    验一个我自己喂进去的前提——**测试替被测系统提供了前提，那条前提就不再被验证**
    （CLAUDE.md §6）。这里让写读两侧共用同一个 list，写侧漏一个字段读侧当场看得见。
    """

    def __init__(self, unary_seq=None):
        self.unary_seq = list(unary_seq or [])
        self.unary_calls: list[str] = []
        self.llm_calls = 0
        self.turns: list[dict] = []

    async def call_agent(self, endpoint, intent, slots, ctx=None, meta=None):
        self.unary_calls.append(intent)
        if self.unary_seq:
            return self.unary_seq.pop(0)
        return _Resp(speech=f"（{intent} 兜底）")

    async def call_agent_stream(self, endpoint, intent, slots, ctx=None, meta=None):
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

    async def append_turn(self, session_id, role, text, *, user_id="",
                          vehicle_id="", occupant_id="primary",
                          e2e_memory_capability="", turn_id="", exchange_id="",
                          actions=None, sources=None):
        self.turns.append({"role": role, "text": text, "ts": 1756000000,
                           "exchange_id": exchange_id,
                           "actions": list(actions or []),
                           "sources": [dict(s) for s in (sources or [])]})

    async def get_session(self, session_id, last_n=6, *, user_id="",
                          occupant_id=""):
        return [dict(t) for t in self.turns[-last_n:]]


def _engine(spy, session=None):
    return PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session or SessionStore(redis_url=""),
    )


def _req(text, session_id):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id=f"r-{text[:6]}",
        is_confirmation=False,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _run(engine, text, session_id):
    async def collect():
        return [e async for e in engine.run(_req(text, session_id))]
    return asyncio.run(collect())


def _finals(events):
    return [e for e in events if e.get("kind") == "final"]


# ── C4-A：卡上的 `_prov` 必须真的走进账本 ─────────────────────────────────

def test_turn_sources_are_collected_from_the_final_card():
    assert _turn_sources(_STOCK_CARD) == [{
        "card": "stock_quote", "vendor": "tushare", "mode": "real",
        "fetched_at": "2026-08-26T19:23:00+08:00",
        "data_time": "20260826", "data_time_label": "行情时间"}]


def test_turn_sources_walks_into_a_card_group():
    """同源多卡时 `attach()` 把章打在**每个成员卡**上，账本也要逐张收。"""
    group = {"type": "card_group", "items": [
        _STOCK_CARD,
        {"type": "weather_now", "_prov": {"vendor": "qweather", "mode": "real"}}]}
    assert [s["vendor"] for s in _turn_sources(group)] == ["tushare", "qweather"]


def test_a_card_without_a_stamp_contributes_nothing():
    """无 vendor 的章在「来源是什么」上等于没有记录——落库只会让读出口念空话。"""
    assert _turn_sources({"type": "reminder_card"}) == []
    assert _turn_sources({"type": "x", "_prov": {"mode": "real"}}) == []
    assert _turn_sources(None) == []


def test_the_engine_writes_the_source_ledger_on_the_assistant_turn():
    """**挂点断言**：卡上的章要经 `append_turn` 真的落到账本里。

    `_prov` 此前只活在卡上、渲染完就丢——这条用例就是那条链的接线证据。
    """
    spy = _Spy(unary_seq=[_Resp(speech="宁德时代当前价 251.30。",
                                ui_card=_STOCK_CARD)])
    _run(_engine(spy), "查一下宁德时代现在的股价", "sess-prov-1")
    assistant = [t for t in spy.turns if t["role"] == "assistant"]
    assert assistant and assistant[-1]["sources"], "final 帧的章没进账本"
    assert assistant[-1]["sources"][0]["vendor"] == "tushare"
    # user 轮不带来源——账本记的是**这一轮系统用了谁**，不是用户说了什么。
    assert all(not t["sources"] for t in spy.turns if t["role"] == "user")


# ── C4-B ②：数据源读出口（T41/T43）────────────────────────────────────────

def test_provenance_followup_is_answered_from_the_ledger_without_planning():
    """真栈 T41：真实是 Tushare / 20260826，chitchat 编出「东方财富 19:23」。

    修后这一轮**根本不进 Planner**——答案由上一轮落库的那个章直接念出来。
    """
    spy = _Spy(unary_seq=[_Resp(speech="宁德时代当前价 251.30。",
                                ui_card=_STOCK_CARD)])
    engine = _engine(spy)
    _run(engine, "查一下宁德时代现在的股价", "sess-prov-2")

    calls_before, llm_before = len(spy.unary_calls), spy.llm_calls
    final = _finals(_run(engine, "数据源和更新时间是什么", "sess-prov-2"))[-1]

    assert "tushare" in final["speech"] and "20260826" in final["speech"]
    assert "数据来源" in final["speech"] and "行情时间" in final["speech"]
    assert len(spy.unary_calls) == calls_before, "读出口轮不该调任何 Agent"
    assert spy.llm_calls == llm_before, "读出口轮不该调 LLM"


def test_a_provenance_question_with_an_empty_ledger_still_reaches_the_planner():
    """**误伤对照 + 分工**：账本空说明这一轮之前没有任何外部数据卡，
    那就不是「系统持有的事实」——`info.stock` 的域内直答比一句「我没记到」有用。"""
    spy = _Spy(unary_seq=[_Resp(speech="数据来源是 Tushare。")])
    _run(_engine(spy), "数据源和更新时间是什么", "sess-prov-3")
    assert spy.unary_calls, "空账本时被短路吞掉了"


def test_the_provenance_answer_is_verbatim_stable():
    speeches = set()
    for i in range(3):
        spy = _Spy(unary_seq=[_Resp(speech="行情", ui_card=_STOCK_CARD)])
        engine = _engine(spy)
        sid = f"sess-prov-det-{i}"
        _run(engine, "查一下宁德时代现在的股价", sid)
        speeches.add(_finals(_run(engine, "数据源是什么", sid))[-1]["speech"])
    assert len(speeches) == 1, speeches


# ── C4-B ③：执行史读出口（T37/T55）────────────────────────────────────────

def test_execution_audit_is_answered_at_the_orchestration_layer():
    """T37 的形态：这句话此前被 planner 接给 reminder.list，
    **闸在 chitchat 里就够不着**。短路搬到落域之前，谁接走都不影响。
    """
    spy = _Spy(unary_seq=[_Resp(speech="好的", data={"_x": 1})])
    engine = _engine(spy)
    # 造一条带动作的历史（engine 落账走 final 帧的 actions）
    asyncio.run(spy.append_turn("s", "user", "把车窗打开", exchange_id="A"))
    asyncio.run(spy.append_turn("s", "assistant", "开了", exchange_id="A",
                                actions=["window.open"]))

    calls_before, llm_before = len(spy.unary_calls), spy.llm_calls
    final = _finals(_run(engine, "刚才实际改了哪一条，时间是什么",
                         "sess-audit-1"))[-1]

    assert "把车窗打开" in final["speech"]
    assert "09:46" in final["speech"], "问了时间就要报时间"
    assert len(spy.unary_calls) == calls_before
    assert spy.llm_calls == llm_before


def test_a_five_turn_summary_reaches_the_same_outlet():
    """T55 原话。修前它落进 manual mock、答「手册里没有查到」。"""
    spy = _Spy()
    asyncio.run(spy.append_turn("s", "user", "打开空调", exchange_id="A"))
    asyncio.run(spy.append_turn("s", "assistant", "好的", exchange_id="A",
                                actions=["hvac.on"]))
    final = _finals(_run(_engine(spy), "总结这五轮里哪些执行了、哪些只是建议",
                         "sess-audit-2"))[-1]
    assert "打开空调" in final["speech"] and "1 个操作" in final["speech"]
    assert spy.unary_calls == [] and spy.llm_calls == 0


def test_an_empty_ledger_says_so_instead_of_planning_something():
    """无账明说——**这一条不进 Planner**：审计问题没有第二个能答的人。"""
    spy = _Spy()
    final = _finals(_run(_engine(spy), "刚才执行了什么", "sess-audit-3"))[-1]
    assert "还没有执行过" in final["speech"]
    assert spy.unary_calls == [] and spy.llm_calls == 0


# ── C4-B ①：挂起状态读出口（T51/T56）──────────────────────────────────────

def test_pending_question_reads_the_pending_table():
    """真栈 T51 答了一个「嗯」、T56 答了学校地址——**问句形态此前没有出口**
    （`system.no_pending` 只在裸确认词/确认帧上触发）。"""
    session = SessionStore(redis_url="")
    spy = _Spy()
    engine = _engine(spy, session=session)
    asyncio.run(session.save("sess-pending-1", SessionState(
        phase="wait_confirm", owner_user_id="u1", operation_id="op1",
        pending_plan={"goal": "取消刚才那笔订单"}, pending_step_id="s1")))

    final = _finals(_run(engine, "现在还有待确认的操作吗", "sess-pending-1"))[-1]
    assert "取消刚才那笔订单" in final["speech"]
    assert spy.unary_calls == [] and spy.llm_calls == 0


def test_no_pending_is_stated_plainly_without_planning():
    spy = _Spy()
    final = _finals(_run(_engine(spy), "现在还有待确认的操作吗",
                         "sess-pending-2"))[-1]
    assert final["speech"] == "当前没有待确认的操作。"
    assert spy.unary_calls == [] and spy.llm_calls == 0


def test_an_unreadable_pending_table_says_so_instead_of_saying_none():
    """**「读不到」与「读到了、是空的」永远分开报**（C2 第二次沉淀的那条）。

    两者都答「当前没有待确认的操作」，就是让一次存储故障说出一句听起来很确定的
    假话——而用户正是靠它决定要不要重说一遍。
    """
    class _Broken(SessionStore):
        # `load` 走另一条既有路径（engine 开头判挂起），保持可用——本用例要隔离的
        # 是**读出口那一次取数**失败，不是整轮不可用。
        async def load(self, session_id, *, owner_user_id="", operation_id=""):
            return None

        async def load_all(self, session_id, *, owner_user_id=""):
            raise RuntimeError("redis down")

    spy = _Spy()
    engine = _engine(spy, session=_Broken(redis_url=""))
    final = _finals(_run(engine, "现在还有待确认的操作吗", "sess-pending-3"))[-1]
    assert "查不到" in final["speech"]
    assert "没有待确认" not in final["speech"]


# ── 误伤对照：短路挂在全部流量上，宽一格就是吞掉正常请求 ───────────────────

def test_ordinary_requests_still_reach_the_planner():
    for text in ("刚才那家店是做什么的", "帮我把更新时间改成明天",
                 "今天深圳天气怎么样", "这五轮的天气怎么样"):
        spy = _Spy(unary_seq=[_Resp(speech="（业务回答）")])
        _run(_engine(spy), text, f"sess-miss-{abs(hash(text)) % 997}")
        assert spy.llm_calls or spy.unary_calls, f"「{text}」被短路吞掉了"


def test_the_bare_confirm_word_keeps_its_own_older_outlet():
    """裸「确认」走的是既有的 `system.no_pending`（2026-06 就在），**不是**本批
    新增的挂起读出口。两条话术刻意不同——它们回答的是两个问题：
    「我说确认，你怎么没反应」 vs 「我还有几件事没确认」。
    """
    spy = _Spy()
    final = _finals(_run(_engine(spy), "确认", "sess-bare-confirm"))[-1]
    assert final["speech"] == "当前没有待确认的操作。您可以重新告诉我需求。"
