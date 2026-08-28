"""C4-A · 会话账本的**数据源维**（QA 修复批第 2 批，2026-08-28）。

立卡背景：`_prov`（契约 §9.3）此前**只活在卡上**——渲染完就丢，全仓
orchestrator/memory 没有任何一处读它。于是「刚才那个行情的数据源是什么」
这类跨轮追问手里根本没有材料，落到 chitchat 就地编一个：真栈 T41 逐字答出
「东方财富实时行情、19:23 前后」，而真实 provider 是 Tushare、行情日 20260826。

> 判据（§4.2 I-033 那行的原文）：**「这一轮用了谁、降级没降级」得像动作一样入账。**
> 别在披露层加话术——话术层判据验证不了「说的是不是真的」（Q6 那条）。

落点与 `actions` 同一格，理由也同一条：会话轮次已经具备 TTL / user 索引 /
OwnerKey / 幂等键 / `ltrim(-50)`，加一维是纯增量。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from store import MemoryStore, TurnConflict, _clean_sources  # noqa: E402

_STOCK = {"card": "stock_quote", "vendor": "tushare", "mode": "real",
          "fetched_at": "2026-08-26T19:23:00+08:00",
          "data_time": "20260826", "data_time_label": "行情时间"}


@pytest.mark.asyncio
async def test_sources_are_stored_with_the_turn():
    store = MemoryStore()
    await store.append_turn("src1", "assistant", "宁德时代当前价…", user_id="u1",
                            sources=[_STOCK])
    turns = await store.get_session("src1", 10, user_id="u1")
    assert turns and turns[-1].get("sources") == [_STOCK]


@pytest.mark.asyncio
async def test_turn_without_sources_stays_backward_compatible():
    """不传 sources 的旧调用方一个字都不用改——字段是**纯增量**。"""
    store = MemoryStore()
    await store.append_turn("src2", "user", "查一下宁德时代", user_id="u1")
    turns = await store.get_session("src2", 10, user_id="u1")
    assert turns and turns[-1].get("sources") == []


@pytest.mark.asyncio
async def test_sources_are_part_of_the_idempotency_payload():
    """**一条被悄悄改过的来源记录比没有记录更糟**——审计问答会照着它回答，
    而用户无从发现。幂等键的既有语义对来源事实只会更严，不会更松。"""
    store = MemoryStore()
    await store.append_turn("src3", "assistant", "行情", user_id="u1",
                            turn_id="t1", sources=[_STOCK])
    # 同 id 同内容 = 重放，静默成功
    await store.append_turn("src3", "assistant", "行情", user_id="u1",
                            turn_id="t1", sources=[_STOCK])
    with pytest.raises(TurnConflict):
        await store.append_turn(
            "src3", "assistant", "行情", user_id="u1", turn_id="t1",
            sources=[{**_STOCK, "vendor": "eastmoney"}])


@pytest.mark.asyncio
async def test_a_legacy_turn_replayed_after_the_upgrade_is_not_a_conflict():
    """**存量轮次读出来没有 `sources` 键，而新写入方给的是 `[]`。**

    不把两者归一就会把一次合法重放判成篡改——且只在**升级后的第一次重试**上
    出现，本地永远复现不了。这条用例把那个升级窗口原样搭出来。
    """
    store = MemoryStore()
    await store.append_turn("src4", "assistant", "开了", user_id="u1",
                            turn_id="t9", actions=["window.open"])
    # 模拟本字段上线之前写下的那一条：直接把键删掉
    for turn in store._mem["src4"]:
        turn.pop("sources", None)
    await store.append_turn("src4", "assistant", "开了", user_id="u1",
                            turn_id="t9", actions=["window.open"])
    assert len(store._mem["src4"]) == 1


def test_untrusted_shapes_are_dropped_not_coerced():
    """上游给的是不可信输入。**非法元素直接丢，不做 `str()` 转换**
    ——CLAUDE.md §6 那条：转出来的值匹配不上任何东西，却会在账本里留下
    一个不存在的 vendor。"""
    assert _clean_sources("not a list") == []
    assert _clean_sources([None, 42, "x"]) == []
    assert _clean_sources([{"vendor": 42}]) == []
    assert _clean_sources([{"mode": "real"}]) == [], "无 vendor 的条目等于没有记录"
    assert _clean_sources([{"vendor": " tushare ", "junk": "x"}]) == \
        [{"vendor": "tushare"}], "白名单之外的键不许进账本"


def test_the_ledger_is_capped():
    """封顶的理由不是省空间，是**别让一条畸形上游把会话历史撑爆**（同 actions）。"""
    many = [{"vendor": f"v{i}"} for i in range(20)]
    assert len(_clean_sources(many)) == 5
