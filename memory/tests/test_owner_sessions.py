"""会话轮次的 owner 归属（M-B）。

背景：`occupant_id` 从 M4 P4 起就贯到了请求控制面，但**没有落到数据面**——Redis 里的
Turn 只有 `role/text/ts`。后果不是「识别不出说话人」，是识别对了也存不下来：同一
cabin session 里换个人说话，上一位的话会按当前说话人归档，planner 也会读到别人的历史。

本文件钉死 OwnerKey=(user_id, occupant_id) 在轮次层的四条契约：
写侧带 owner、读侧默认 OWNER_ONLY、旧数据统一归 primary 且 id 稳定、owner 级删除
物理删而不是读时隐藏。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from store import ALL_OCCUPANTS, MemoryStore, TurnConflict  # noqa: E402


def _store() -> MemoryStore:
    s = MemoryStore()
    s.url = ""  # 强制内存兜底，测试不依赖 Redis
    return s


async def _say(store, occ, text, *, role="user", exch, idx=None, sid="s1", uid="u1"):
    tid = f"{exch}:user" if role == "user" else f"{exch}:assistant:{idx or 0}"
    return await store.append_turn(sid, role, text, user_id=uid, occupant_id=occ,
                                   vehicle_id="v1", turn_id=tid, exchange_id=exch)


def test_turns_carry_owner_and_exchange():
    store = _store()

    async def go():
        await _say(store, "primary", "我叫泓舟", exch="e1")
        await _say(store, "primary", "记住了", role="assistant", exch="e1")
        return await store.get_session("s1", 10, user_id="u1", occupant_id="primary")

    turns = asyncio.run(go())
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert all(t["occupant_id"] == "primary" for t in turns)
    assert all(t["user_id"] == "u1" for t in turns)
    assert {t["exchange_id"] for t in turns} == {"e1"}
    assert turns[0]["turn_id"] == "e1:user"
    assert turns[1]["turn_id"] == "e1:assistant:0"


def test_owner_only_is_the_default_and_hides_the_other_occupant():
    """同 session 先 A 后 B 各说一轮，两边都只看见自己的。

    这是本批的核心断言：**共享绝不能是缺省行为**。
    """
    store = _store()

    async def go():
        await _say(store, "primary", "我女儿叫小满", exch="e1")
        await _say(store, "primary", "记住了", role="assistant", exch="e1")
        await _say(store, "occ-2", "我喜欢吃辣", exch="e2")
        await _say(store, "occ-2", "好的", role="assistant", exch="e2")
        return (
            await store.get_session("s1", 10, user_id="u1", occupant_id="primary"),
            await store.get_session("s1", 10, user_id="u1", occupant_id="occ-2"),
        )

    a, b = asyncio.run(go())
    assert [t["text"] for t in a] == ["我女儿叫小满", "记住了"]
    assert [t["text"] for t in b] == ["我喜欢吃辣", "好的"]


def test_missing_occupant_normalizes_to_primary_never_to_shared():
    store = _store()

    async def go():
        await _say(store, "primary", "主驾的话", exch="e1")
        await _say(store, "occ-2", "乘客的话", exch="e2")
        return await store.get_session("s1", 10, user_id="u1", occupant_id="")

    turns = asyncio.run(go())
    assert [t["text"] for t in turns] == ["主驾的话"]


def test_all_occupants_requires_explicit_scope():
    store = _store()

    async def go():
        await _say(store, "primary", "主驾的话", exch="e1")
        await _say(store, "occ-2", "乘客的话", exch="e2")
        return await store.get_session("s1", 10, user_id="u1", occupant_id="primary",
                                       scope=ALL_OCCUPANTS)

    turns = asyncio.run(go())
    assert [t["text"] for t in turns] == ["主驾的话", "乘客的话"]
    assert [t["occupant_id"] for t in turns] == ["primary", "occ-2"]


def test_last_n_counts_after_filtering_and_never_splits_an_exchange():
    """`last_n` 是**过滤后**的上限；切中 exchange 时整体舍弃最旧的半个。

    半个 exchange 比没有更糟：只留 assistant 那半句，抽取会把助手的话当成用户偏好。
    """
    store = _store()

    async def go():
        for i in (1, 2, 3):
            await _say(store, "primary", f"问题{i}", exch=f"e{i}")
            await _say(store, "primary", f"回答{i}", role="assistant", exch=f"e{i}")
        return await store.get_session("s1", 3, user_id="u1", occupant_id="primary")

    turns = asyncio.run(go())
    assert [t["text"] for t in turns] == ["问题3", "回答3"]  # 3 条会切开 e2，故只留完整的 e3


def test_same_turn_id_same_payload_is_idempotent():
    store = _store()

    async def go():
        await _say(store, "primary", "只说一次", exch="e1")
        await _say(store, "primary", "只说一次", exch="e1")
        return await store.get_session("s1", 10, user_id="u1", occupant_id="primary")

    assert len(asyncio.run(go())) == 1


def test_same_turn_id_different_payload_conflicts_and_keeps_the_original():
    store = _store()

    async def go():
        await _say(store, "primary", "原文", exch="e1")
        try:
            await _say(store, "primary", "被改写的原文", exch="e1")
        except TurnConflict:
            pass
        else:
            raise AssertionError("同 turn_id 异 payload 必须冲突，不能静默覆盖")
        return await store.get_session("s1", 10, user_id="u1", occupant_id="primary")

    turns = asyncio.run(go())
    assert [t["text"] for t in turns] == ["原文"]


def test_legacy_turns_map_to_primary_with_stable_ids():
    """旧 JSON 没有 owner，**不猜**——统一归 primary，且 id 多次读不漂移。"""
    store = _store()
    store._mem["s1"] = [
        {"role": "user", "text": "旧的问题", "ts": 100},
        {"role": "assistant", "text": "旧的回答", "ts": 101},
    ]
    store._mem_user_sessions["u1"] = {"s1"}

    async def go():
        first = await store.get_session("s1", 10, user_id="u1", occupant_id="primary")
        second = await store.get_session("s1", 10, user_id="u1", occupant_id="primary")
        return first, second

    first, second = asyncio.run(go())
    assert [t["text"] for t in first] == ["旧的问题", "旧的回答"]
    assert all(t["occupant_id"] == "primary" for t in first)
    assert all(t["turn_id"] for t in first)
    assert [t["turn_id"] for t in first] == [t["turn_id"] for t in second]
    # 旧轮次归 primary，occ-2 读不到——不因为「没标 owner」就当成共享
    third = asyncio.run(store.get_session("s1", 10, user_id="u1", occupant_id="occ-2"))
    assert third == []


def test_owner_forget_physically_removes_only_that_owner_turns():
    store = _store()

    async def go():
        await _say(store, "primary", "主驾的话", exch="e1")
        await _say(store, "primary", "回答主驾", role="assistant", exch="e1")
        await _say(store, "occ-2", "乘客的话", exch="e2")
        removed = await store.forget_owner_sessions("u1", "occ-2")
        raw = json.dumps(store._mem["s1"], ensure_ascii=False)
        return removed, raw

    removed, raw = asyncio.run(go())
    assert removed == 1
    assert "乘客的话" not in raw, "owner 级删除必须物理删除，不能只在读取时隐藏"
    assert "主驾的话" in raw and "回答主驾" in raw


def test_ownerless_writes_fall_back_to_primary_not_to_shared():
    """缺 owner 的写入（端侧快路径 / 合成会话）只能兼容为 primary。

    这是**有损归属**：这些轮次本来就没有真实 owner。但它绝不能变成「谁都能读」——
    兼容的方向永远是收窄到 primary，不是放开成共享。
    """
    store = _store()

    async def go():
        await store.append_turn("s1", "user", "端侧说的话")
        return (
            await store.get_session("s1", 10, user_id="u1", occupant_id="primary"),
            await store.get_session("s1", 10, user_id="u1", occupant_id="occ-2"),
        )

    a, b = asyncio.run(go())
    assert [t["text"] for t in a] == ["端侧说的话"]
    assert b == []


def test_deleting_an_occupant_also_purges_their_transcripts():
    """删一个乘员＝忘掉这个人：长期记忆删了、对话原文还在，那不是删除是搬家。

    此前做不到（轮次不带说话人标注、无法选择性删），轮次带 owner 之后才成立。
    """
    store = _store()

    async def go():
        await _say(store, "primary", "主驾说的话", exch="e1")
        await _say(store, "occ-2", "乘客说的话", exch="e2")
        res = await store.delete_voiceprint("u1", "occ-2")
        return res, json.dumps(store._mem["s1"], ensure_ascii=False)

    res, raw = asyncio.run(go())
    assert res.get("deleted_turns") == 1
    assert "乘客说的话" not in raw
    assert "主驾说的话" in raw


def test_deleting_primary_never_purges_transcripts():
    """primary 不 purge——删单个乘员不该有清空全车的爆炸半径。"""
    store = _store()

    async def go():
        await _say(store, "primary", "主驾说的话", exch="e1")
        res = await store.delete_voiceprint("u1", "primary")
        return res, json.dumps(store._mem["s1"], ensure_ascii=False)

    res, raw = asyncio.run(go())
    assert "deleted_turns" not in res
    assert "主驾说的话" in raw
