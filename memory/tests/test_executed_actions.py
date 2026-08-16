"""Q6 · 执行事实账本（I-047 / I-038 / I-002）。

立卡背景：`task_ledger` 实测只有 `research`(106) 与 `mcp_order`(17) 两类 kind，
**车控、导航、提醒、场景一律不进账本**，QA 那 533 轮在账本里一条记录都没有。
于是「刚才实际执行了什么」**没有可查询的事实源**，chitchat 只能拿对话历史让 LLM
重构——真栈三次取样读出三个样，其中一次逐字答「车窗没动，音乐也没停，
我这边只是文字回复，没法真的控制车」，而真实动作是 `window.open` + `media.pause`。

> 判据：**「系统持有的事实绝不让 LLM 答」**（墙钟三件套的既有纪律）在
> **执行事实**上还没兑现。

## 为什么落在会话轮次而不是新表（写入量先量清楚了，卡上的硬前置）

obs 实测 38 天累计 **2754 轮 / 763 个动作**，有动作的轮次只占 **24%**，
每轮动作数 1 个占 88.6%、最多 5 个；最忙一天（QA 轮）1105 轮 / 253 个动作。
**这个量落 PG 是过度设计。**

而 `store.append_turn` 已经具备这条链需要的全部东西——TTL、user 索引、OwnerKey、
幂等键、`ltrim(-50)`，**端侧与云侧都已经在调它**。加一个 `actions` 字段是纯增量，
**保留期由既有机制兜住**，不必新定（卡上那个「别做成只涨不清的表」自动满足）。

## 关键约束：40% 的动作在端侧执行，云侧看不到

obs 按 path 实测：`local` 273 轮 / **313 个动作**、`cloud` 226/264、`mixed` 172/198。
纯 local 轮**根本不上云** ⇒ 台账只建在云侧 Focus 的话，端侧车控（最该被审计的那类）
永远查不到。**卡上没写这条**，是量 obs 时才显形的。
落在会话轮次上正好覆盖三条路径——端侧那条 `AppendTurn` 早就在调了，只是没带动作。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from store import MemoryStore, TurnConflict  # noqa: E402


@pytest.mark.asyncio
async def test_actions_are_stored_with_the_turn():
    """动作随轮次落库——**这是「系统持有的事实」的存储面**。"""
    store = MemoryStore()
    await store.append_turn("s1", "assistant", "开了", user_id="u1",
                            actions=["window.open"])
    turns = await store.get_session("s1", 10, user_id="u1")
    assert turns and turns[-1].get("actions") == ["window.open"]


@pytest.mark.asyncio
async def test_turn_without_actions_stays_backward_compatible():
    """不传 actions 的旧调用方一个字都不用改——字段是**纯增量**。"""
    store = MemoryStore()
    await store.append_turn("s2", "user", "打开车窗", user_id="u1")
    turns = await store.get_session("s2", 10, user_id="u1")
    assert turns and turns[-1].get("actions") == []


@pytest.mark.asyncio
async def test_actions_are_part_of_the_idempotency_payload():
    """同 `turn_id` 异动作必须冲突——**已经发生过的执行事实不许被改写**。

    幂等键的既有语义是「重试可以重放，但不能改写已经发生过的对话」。
    执行事实比对话文本更需要这条：一条被悄悄改过的执行记录，比没有记录更糟。
    """
    store = MemoryStore()
    await store.append_turn("s3", "assistant", "开了", user_id="u1",
                            turn_id="t1", actions=["window.open"])
    # 同 id 同内容 = 重放，静默成功
    assert await store.append_turn("s3", "assistant", "开了", user_id="u1",
                                   turn_id="t1", actions=["window.open"])
    with pytest.raises(TurnConflict):
        await store.append_turn("s3", "assistant", "开了", user_id="u1",
                                turn_id="t1", actions=["window.close"])


@pytest.mark.asyncio
async def test_actions_are_normalized_to_a_bounded_list_of_strings():
    """模型/上游给的东西是不可信输入：非 list、非字符串元素、超长一律归一。

    同 CLAUDE.md §6 那条——`depends_on` 的 `[["s0"]]` **是** list、`isinstance` 照过，
    一路走到 `dep in valid_ids` 才崩。**归一时非法元素直接丢，不做 `str()` 转换**。
    """
    store = MemoryStore()
    await store.append_turn("s4", "assistant", "x", user_id="u1",
                            actions=["window.open", 42, None, ["nested"], "media.pause"])
    turns = await store.get_session("s4", 10, user_id="u1")
    assert turns[-1]["actions"] == ["window.open", "media.pause"]

    await store.append_turn("s5", "assistant", "x", user_id="u1",
                            actions="window.open")          # 非 list
    turns = await store.get_session("s5", 10, user_id="u1")
    assert turns[-1]["actions"] == []

    await store.append_turn("s6", "assistant", "x", user_id="u1",
                            actions=[f"a{i}" for i in range(50)])
    turns = await store.get_session("s6", 10, user_id="u1")
    assert len(turns[-1]["actions"]) == 10, "封顶，别让一轮撑爆会话历史"
