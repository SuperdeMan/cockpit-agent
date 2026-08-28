"""C4-B · 「系统持有的会话事实」三条读出口的判据与话术（2026-08-28）。

判据面来自 2026-08-26 MiniMax 长会话 QA 的五张症状卡：来源追问被编造（T41/T43）、
五轮总结落进手册 mock（T55）、「还有待确认的操作吗」答了一个「嗯」（T51）和一个
学校地址（T56）、「刚才实际改了哪一条」答了整份提醒列表（T37）。

**本文件近一半用例是误伤对照组**，这是刻意的，而且比 chitchat 那批更要紧：
判据从 chitchat 兜底位搬到了**编排层**，命中即整轮不进 Planner——
**判据搬家会改变误伤代价，词表必须跟着重看一遍**（`做什么的` 那条就是这么补的）。

执行史部分的行为锁另有一份 `agents/chitchat/tests/test_audit_answer.py`
（迁移前那份，断言一条没改）；这里只补 C4 新增的那几段。
"""
from __future__ import annotations

import pytest

from runtime import session_facts as sf


# ── ① 执行史：回顾词表与执行词表各补了一段 ────────────────────────────────

@pytest.mark.parametrize("raw", [
    "总结这五轮里哪些执行了、哪些只是建议",   # T55 原话（探针语料逐字）
    "这几轮都执行了什么",
    "这三次操作过哪些",
    "到目前为止执行了什么",
])
def test_the_multi_turn_retrospect_forms_are_recognized(raw):
    """T55 原话在原词表上**一个回顾词都不命中**（只有「刚才/这次对话」），
    于是一句标准的审计问题从来没进过那道闸。"""
    assert sf.is_execution_audit_question(raw) is True, raw


@pytest.mark.parametrize("raw", [
    "刚才实际改了哪一条，时间是什么",         # T37 原话
    "刚才修改了哪一项",
    "刚刚设置了哪几个",                        # 「哪几」在泛问分支里
])
def test_the_named_execution_forms_are_recognized(raw):
    """T37 在泛问分支上一个词都不命中（它问的是「改了哪一条」不是「做了什么」），
    于是被 planner 接给 reminder.list、**答了整份列表**。"""
    assert sf.is_execution_audit_question(raw) is True, raw


@pytest.mark.parametrize("raw", [
    "刚才那家店是做什么的",      # ← 判据搬家新增的误伤面：两段全中却不是审计问题
    "刚刚那个牌子是干什么的",
    "帮我改一下第二条的时间",    # 指令，不是回顾
    "这五轮的天气怎么样",        # 有回顾、无执行询问
    "你能执行哪些操作",          # 问能力不是问执行史
])
def test_the_relocated_gate_does_not_widen_its_blast_radius(raw):
    """**误伤对照**：搬到编排层之后，命中的代价是整轮不进 Planner。"""
    assert sf.is_execution_audit_question(raw) is False, raw


def test_the_time_dimension_is_off_unless_asked():
    """账本里本来就有 `ts`，但**不问不报**——多说一个维度也是改行为，
    而缺省行为是 Q6 上线以来的行为锁。"""
    history = [{"role": "user", "text": "打开车窗", "exchange_id": "A"},
               {"role": "assistant", "text": "开了", "actions": ["window.open"],
                "exchange_id": "A", "ts": 1756000000}]
    assert "（" not in sf.audit_answer(history)
    assert "（" in sf.audit_answer(history, with_time=True)


def test_the_time_dimension_uses_the_business_timezone():
    """容器 TZ=UTC，裸 `localtime` 会整体偏 8 小时——那条老账已经犯过四次。"""
    history = [{"role": "user", "text": "打开车窗", "exchange_id": "A"},
               {"role": "assistant", "text": "开了", "actions": ["window.open"],
                "exchange_id": "A", "ts": 1756000000}]
    # 1756000000 = 2025-08-24T09:46:40+08:00
    assert "09:46" in sf.audit_answer(history, with_time=True)


def test_a_turn_without_ts_still_reports_the_utterance():
    """存量轮次没有 `ts`：**报动作仍然是真的**，别因为凑不齐时刻就少报一条。"""
    history = [{"role": "user", "text": "打开车窗", "exchange_id": "A"},
               {"role": "assistant", "text": "开了", "actions": ["window.open"],
                "exchange_id": "A"}]
    assert "打开车窗" in sf.audit_answer(history, with_time=True)


@pytest.mark.parametrize("raw", ["时间是什么", "什么时候执行的", "几点改的"])
def test_when_asks_are_recognized(raw):
    assert sf.asks_when(raw) is True


# ── ② 数据源 ──────────────────────────────────────────────────────────────

_STOCK = {"card": "stock_quote", "vendor": "tushare", "mode": "real",
          "fetched_at": "2026-08-26T19:23:00+08:00",
          "data_time": "20260826", "data_time_label": "行情时间"}


@pytest.mark.parametrize("raw", [
    "数据源和更新时间是什么",     # T41 原话（探针语料逐字）
    "这个行情来源是哪里",
    "数据来源是什么",
    "什么时候更新的",
])
def test_provenance_questions_are_recognized(raw):
    assert sf.is_provenance_question(raw) is True, raw


@pytest.mark.parametrize("raw", [
    "帮我把更新时间改成明天",   # 祈使式：是指令不是提问
    "请把数据源换一个",
    "这家公司的收入来源是什么",  # 词表刻意不含裸「来源」
    "",
])
def test_provenance_gate_stays_narrow(raw):
    assert sf.is_provenance_question(raw) is False, raw


def test_provenance_answer_reads_the_ledger_verbatim():
    """真栈 T41 的对照：真实是 Tushare / 20260826，编出来的是「东方财富 19:23」。
    读出口把两者都念出来——**只念取数时刻正是那次混淆的形态**。"""
    history = [{"role": "assistant", "text": "宁德时代…", "sources": [_STOCK]}]
    got = sf.provenance_answer(history)
    assert "数据来源" in got and "tushare" in got
    assert "行情时间 20260826" in got, got
    assert "取数时间 2026-08-26T19:23:00+08:00" in got


def test_the_time_label_comes_from_the_producer_not_from_this_module():
    """编排层不认识「行情时间」这个词。产生方不声明称呼时用通用词，
    **代码里不许出现第二个领域说法**（同 `_candidate_label` 的判据）。"""
    src = dict(_STOCK)
    src.pop("data_time_label")
    history = [{"role": "assistant", "text": "x", "sources": [src]}]
    assert "数据时间 20260826" in sf.provenance_answer(history)
    assert "行情" not in sf.provenance_answer(history)


def test_a_degraded_source_says_it_degraded_and_why():
    """**降级要点名是谁降级了**（§9.3 的既有约定），跨轮追问同样成立。"""
    history = [{"role": "assistant", "text": "x", "sources": [
        {"card": "stock_quote", "vendor": "eastmoney", "mode": "degraded",
         "note": "Tushare 失败降级东方财富"}]}]
    got = sf.provenance_answer(history)
    assert "降级数据源" in got and "Tushare 失败降级东方财富" in got


def test_latest_sources_takes_the_newest_recorded_turn_only():
    """汇总全会话会把三轮前那家 provider 一起念出来——**每个字都是真的、
    合起来是错的**（同 I-030「答错组比编造更难被发现」）。"""
    history = [
        {"role": "assistant", "text": "天气", "sources": [
            {"card": "weather_now", "vendor": "qweather", "mode": "real"}]},
        {"role": "assistant", "text": "行情", "sources": [_STOCK]},
        {"role": "assistant", "text": "嗯", "sources": []},
    ]
    assert [s["vendor"] for s in sf.latest_sources(history)] == ["tushare"]


def test_an_empty_ledger_says_so_instead_of_inventing():
    got = sf.provenance_answer([])
    assert "还没有调用过外部数据源" in got
    assert "tushare" not in got


def test_provenance_is_deterministic():
    history = [{"role": "assistant", "text": "x", "sources": [_STOCK]}]
    assert sf.provenance_answer(history) == sf.provenance_answer(history)


# ── ③ 挂起状态 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "现在还有待确认的操作吗",      # T51/T56 原话（探针语料逐字）
    "还有没确认的吗",
    "有几条等我确认的",
    "还有需要确认的操作吗",
])
def test_pending_questions_are_recognized(raw):
    assert sf.is_pending_question(raw) is True, raw


@pytest.mark.parametrize("raw", [
    "确认",                        # 裸确认词：走既有的确认分支，不是提问
    "取消待确认的操作",            # 指令
    "帮我确认待确认的那一条",      # 祈使式
    "现在几点",
    "",
])
def test_pending_gate_stays_narrow(raw):
    assert sf.is_pending_question(raw) is False, raw


def test_no_pending_is_stated_plainly():
    assert sf.pending_answer([]) == "当前没有待确认的操作。"
    assert sf.pending_answer(None) == "当前没有待确认的操作。"


def test_pending_answer_reports_every_live_entry():
    """挂起表本来就是多条（Q1-C）。只答最新那一条等于把「还有几条」答错。"""
    got = sf.pending_answer([{"what": "取消订单", "phase": "wait_confirm"},
                             {"what": "创建提醒", "phase": "wait_slot"}])
    assert "2 条" in got and "取消订单" in got and "创建提醒" in got
    assert "等你确认" in got and "等你补充信息" in got


def test_pending_answer_is_deterministic():
    entries = [{"what": "取消订单", "phase": "wait_confirm"}]
    assert sf.pending_answer(entries) == sf.pending_answer(entries)


def test_pending_answer_survives_a_pending_without_a_goal():
    """挂起态没带 goal（旧负载/异常形状）时仍要说得出是哪件事的占位词。"""
    got = sf.pending_answer([{"what": "", "phase": "wait_confirm"}])
    assert "刚才那个操作" in got


# ── 共用纪律的源码级守卫 ──────────────────────────────────────────────────

def test_this_module_never_asks_an_llm_or_the_network():
    """三条读出口的全部价值就是**零 LLM、零网络**：一旦有人在这里加一次调用，
    「有账才答」就退回成「让模型润色一下账」。"""
    from pathlib import Path
    src = Path(sf.__file__).read_text(encoding="utf-8")
    body = "\n".join(line for line in src.splitlines()
                     if not line.lstrip().startswith("#"))
    for forbidden in ("import httpx", "import requests", "llm", "await "):
        assert forbidden not in body, f"读出口里出现了 {forbidden}"
