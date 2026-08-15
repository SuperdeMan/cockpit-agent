"""manual-rag 安全与真实性护栏（阶段 1 / 卡 Q9，QA 轮 I-036 的根因面）。

背景（`docs/design/2026-08-15-qa-exploratory-root-cause-cards.md` 重判 2）：
QA 轮报告把「未知车型却给出 2.4–2.5 bar」定性成「LLM 用通用常识覆盖手册真实性」。
实测不是——`providers/mock.py:6` 的 **mock 知识库逐字写着**这个数值、source 是
「第3章·轮胎保养」，系统**忠实转述了一份演示数据**并称之为「本车型推荐」。

所以这一批要钉的是四件确定性的事（全部与模型行为无关）：
  ① 零命中**不调 LLM**——原实现把「（未检索到高相关条目）」当参考资料喂进去，
     等于让模型在没有资料的情况下自由发挥。
  ② 非真实手册来源（mock/web）**不得被表述成「本车型手册」**。
  ③ 安全信号词（警告灯/胎压报警/机油/水温/制动…）命中时必须给**分级安全建议**，
     不能只丢一个数值了事。
  ④ 卡片必须盖 `_prov`——manual-rag/road-safety/chitchat 三个「答案本身就是内容」
     的 Agent 此前 `_prov` 覆盖为 **0**，恰恰是最不该没有出处的那类。

⚠ 这些断言必须**先红再绿**（AGENTS.md §4.3「扫描类断言必须先注入一次缺陷看它红」）。
"""
import asyncio
from unittest.mock import AsyncMock

from agents._sdk.testing import run_handle
from agents.manual_rag.src.agent import ManualRagAgent


def _agent(answer: str = "答案"):
    agent = ManualRagAgent()
    agent.llm.complete = AsyncMock(return_value=answer)
    return agent


# ── ① 零命中短路 ─────────────────────────────────────────────────────────

def test_zero_hit_does_not_call_llm():
    """检索零命中时必须直接诚实弃权，**一次 LLM 都不调**。

    原实现返回一条哨兵 chunk「（未检索到高相关条目，建议联系客服）」并把它当
    【参考资料】送进 prompt——模型于是在「资料里什么都没有」的情况下作答。
    """
    agent = _agent("我编的答案")
    res = asyncio.run(run_handle(
        agent, "manual.query", raw_text="车载冰箱最低能到几度"))
    assert agent.llm.complete.await_count == 0, "零命中不得调用 LLM"
    assert res.status == "ok"
    assert "没" in res.speech or "未" in res.speech, f"应诚实弃权，实得：{res.speech}"


def test_zero_hit_card_has_no_fabricated_sources():
    agent = _agent()
    res = asyncio.run(run_handle(
        agent, "manual.query", raw_text="车载冰箱最低能到几度"))
    assert not (res.ui_card or {}).get("sources"), "零命中不得给出来源"


# ── ② 来源类型不得被改写 ─────────────────────────────────────────────────

def test_mock_source_is_not_presented_as_vehicle_manual():
    """当前唯一的知识库是 mock（`KNOWLEDGE_VENDOR=mock`，pgvector 仍是 TODO）。

    它没有绑定任何车型，所以**不能**说「本车型推荐」——必须让用户知道
    这是通用参考、以车辆铭牌/随车手册为准。
    """
    agent = _agent()
    res = asyncio.run(run_handle(agent, "manual.query", raw_text="胎压多少正常"))
    card = res.ui_card or {}
    assert card.get("source_type") == "mock", \
        f"卡片必须标出来源类型，实得 {card.get('source_type')!r}"
    # 送进 LLM 的 system prompt 不得把 mock 资料框成「车型手册」
    messages = agent.llm.complete.await_args[0][0]
    system = messages[0]["content"]
    assert "车型手册问答助手" not in system, \
        "非真实手册来源时，system prompt 不得自称车型手册助手"
    assert "以车辆铭牌" in system or "随车手册" in system, \
        "非真实手册来源时必须要求模型让用户核对铭牌/随车手册"


def test_real_manual_source_keeps_manual_wording():
    """反向对照：真实手册来源时话术不得被这道闸改掉（防止修过头）。"""
    from agents.manual_rag.src.providers.base import Chunk

    agent = _agent()

    async def _real(query, vehicle_model="", top_k=4):
        return [Chunk(content="推荐胎压 2.3 bar。", source="第3章",
                      score=0.95, source_type="manual")]

    agent.kb.retrieve = _real
    res = asyncio.run(run_handle(agent, "manual.query", raw_text="胎压多少正常"))
    assert (res.ui_card or {}).get("source_type") == "manual"
    system = agent.llm.complete.await_args[0][0][0]["content"]
    assert "车型手册问答助手" in system


# ── ③ 安全信号词 → 分级安全建议 ──────────────────────────────────────────

def test_safety_signal_gets_graded_advice_not_just_a_number():
    """「胎压黄灯亮了，能继续开吗」这类问题，答案里必须有安全处置，不能只有数值。

    QA 实测原文：「补到手册推荐值即可，前后轮都补到 2.4–2.5 bar（冷胎）」——
    数值给得斩钉截铁，风险处置一句没有。
    """
    agent = _agent("补到 2.4–2.5 bar 就行。")
    res = asyncio.run(run_handle(
        agent, "manual.query", raw_text="胎压黄灯亮了，还能继续开吗？应该补到多少？"))
    assert (res.data or {}).get("safety_signal"), "应识别出这是安全信号类问题"
    assert any(w in res.speech for w in ("停车", "减速", "尽快", "检查", "以车辆铭牌")), \
        f"安全信号类问题必须带处置建议，实得：{res.speech}"


def test_safety_signal_declares_session_level_alert():
    """安全信号必须经保留键 `_safety_alert` 声明**会话级**安全态。

    这是 SF3 那三轮真正缺的东西：告警只活在产生它的那一轮里，
    第二轮问「高速还能开吗」时编排已经不知道有告警了。
    """
    agent = _agent("停车检查。")
    res = asyncio.run(run_handle(
        agent, "manual.query", raw_text="红色机油灯亮了怎么办？"))
    alert = (res.data or {}).get("_safety_alert") or {}
    assert alert.get("level") == "critical", f"红色机油灯应判 critical：{alert}"
    assert "机油" in alert.get("signal", ""), f"signal 应点名告警对象：{alert}"


def test_safety_alert_signal_is_not_the_whole_sentence():
    """反向对照：signal 取命中的词，不取整句——否则用户的措辞会变成告警名字。"""
    agent = _agent("x")
    res = asyncio.run(run_handle(
        agent, "manual.query", raw_text="胎压黄灯亮了，还能继续开吗？应该补到多少？"))
    sig = ((res.data or {}).get("_safety_alert") or {}).get("signal", "")
    assert sig and len(sig) <= 12, f"signal 不该是整句：{sig!r}"


def test_non_safety_query_has_no_safety_banner():
    """反向对照：普通问题不得被安全护栏污染话术。"""
    agent = _agent("用数据线连接手机至中控 USB。")
    res = asyncio.run(run_handle(agent, "manual.query", raw_text="怎么连CarPlay"))
    assert not (res.data or {}).get("safety_signal")
    assert "停车" not in res.speech


# ── ④ 出处必须上卡 ───────────────────────────────────────────────────────

def test_card_carries_provenance():
    agent = _agent()
    res = asyncio.run(run_handle(agent, "manual.query", raw_text="胎压多少正常"))
    prov = (res.ui_card or {}).get("_prov") or {}
    assert prov.get("mode") == "mock", f"当前知识库是 mock，_prov 必须如实标注：{prov}"
    assert prov.get("vendor"), "_prov 必须带 vendor"
