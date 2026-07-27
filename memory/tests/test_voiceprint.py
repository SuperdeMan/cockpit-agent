"""声纹模板单测（M4 P4）：判定四态 / 首个注册者绑 primary / 坏模板拒收 / **GDPR 级联删（红线）**。

四条硬要求：
1. **认不出一律回 primary**——不是 guest 也不是 unknown。primary 是存量语义，降到它=逐字
   回落到 P4 之前；造新身份等于凭空多一个空记忆空间，用户体感是「车失忆了」。
2. **第一个注册的人拿 primary**——今天全部存量记忆在 primary 名下，首个注册者若拿 occ-1，
   他自己过去说过的一切当场失联。这是本设计后果最大的回归点。
3. **坏模板宁可不建**——三段互不像说明录音里混了别人/噪声，建出来此后谁都认不准。
4. **GDPR 硬删必须级联声纹表**——声纹是生物特征，留着比留关系边更严重。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import voiceprint as V  # noqa: E402
from store import MemoryStore  # noqa: E402


def _store() -> MemoryStore:
    return MemoryStore()


# 正交的单位向量：互相余弦 = 0（模拟「完全不同的人」）
def _basis(i: int, dim: int = 8) -> list[float]:
    v = [0.0] * dim
    v[i % dim] = 1.0
    return v


def _near(i: int, eps: float, dim: int = 8) -> list[float]:
    """与 _basis(i) 夹角很小的向量（模拟「同一个人的另一句话」）。"""
    v = _basis(i, dim)
    v[(i + 1) % dim] = eps
    return V.l2_normalize(v)


# ── 纯计算 ────────────────────────────────────────────────────────────────

def test_l2_normalize_unit_length():
    v = V.l2_normalize([3.0, 4.0])
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9


def test_l2_normalize_zero_vector_does_not_nan():
    """零向量不能产生 NaN——下游 cosine 给 0 分，自然落 below_threshold。"""
    assert V.l2_normalize([0.0, 0.0]) == [0.0, 0.0]


def test_cosine_dim_mismatch_is_zero():
    """跨模型（维度不同）的余弦没有意义，返回 0 而不是抛错。"""
    assert V.cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_mean_template_normalizes_each_sample_first():
    """先归一再平均：否则录得响的那一段会因模长大而主导模板，
    模板就偏向「那一次的录音条件」而不是「这个人」。"""
    loud = [10.0, 0.0]
    quiet = [0.0, 0.1]
    tpl = V.mean_template([loud, quiet])
    assert abs(tpl[0] - tpl[1]) < 1e-6      # 两段等权 → 45 度


def test_self_consistency_single_sample_is_one():
    assert V.self_consistency([[1.0, 0.0]]) == 1.0


def test_self_consistency_detects_mixed_speakers():
    assert V.self_consistency([_basis(0), _basis(1), _basis(2)]) < 0.2


# ── 判定四态 ──────────────────────────────────────────────────────────────

def test_decide_no_templates_falls_back_to_primary():
    out = V.decide([])
    assert out["occupant_id"] == "primary" and out["decision"] == "no_templates"


def test_decide_below_threshold_falls_back_to_primary():
    out = V.decide([("occ-2", 0.40)], thr=0.62, mrg=0.05)
    assert out["occupant_id"] == "primary" and out["decision"] == "below_threshold"


def test_decide_ambiguous_falls_back_to_primary():
    """够像但和第二名拉不开——两个家庭成员声音接近时，分不开就别分。"""
    out = V.decide([("occ-2", 0.70), ("primary", 0.68)], thr=0.62, mrg=0.05)
    assert out["occupant_id"] == "primary" and out["decision"] == "ambiguous"


def test_decide_accept_returns_the_speaker():
    out = V.decide([("occ-2", 0.81), ("primary", 0.40)], thr=0.62, mrg=0.05)
    assert out["occupant_id"] == "occ-2" and out["decision"] == "accept"
    assert out["runner_up"] == 0.40


def test_no_decision_ever_returns_guest_or_unknown():
    """契约：降级目标只有 primary。别的身份=凭空造出一个空记忆空间。"""
    for scored in ([], [("occ-2", 0.1)], [("occ-2", 0.7), ("occ-3", 0.69)]):
        assert V.decide(scored)["occupant_id"] in ("primary", "occ-2")


def test_real_mic_measurement_is_accepted():
    """**真人真麦实测钉死**（2026-07-26，泓舟/阿灵两位已注册乘员）：
    同人 0.5243 / 异人 0.1157。旧阈值 0.62 把同人一并卡掉 → 谁说话都认成同一个。

    合成音色把这件事标反了：TTS 音色共享信道特征、异人余弦高达 0.65，逼阈值上抬；
    真人真麦的异人分离度好得多（0.12），阈值反而该下放。
    **代理数据标定的常量，必须在真实分布上复标一次。**"""
    out = V.decide([("occ-2", 0.5243), ("primary", 0.1157)])
    assert out["decision"] == "accept" and out["occupant_id"] == "occ-2", (
        f"真人实测的这组数必须认得出来（当前 threshold={V.threshold()}）")


def test_pure_noise_is_still_rejected():
    """下调阈值不能把噪声也放进来：实测正弦+白噪探针余弦 -0.0585。"""
    out = V.decide([("primary", -0.0585), ("occ-2", -0.1025)])
    assert out["occupant_id"] == "primary" and out["decision"] == "below_threshold"


# ── occupant_id 分配 ──────────────────────────────────────────────────────

def test_first_enrollee_takes_primary():
    """**红线级回归点**：首个注册者必须继承 primary 名下的全部存量记忆。"""
    assert V.allocate_occupant_id([]) == "primary"


def test_second_enrollee_starts_at_occ2():
    """primary 就是 1 号，不再造一个空的 occ-1。"""
    assert V.allocate_occupant_id(["primary"]) == "occ-2"


def test_allocation_fills_gap_after_delete():
    assert V.allocate_occupant_id(["primary", "occ-3"]) == "occ-2"
    assert V.allocate_occupant_id(["primary", "occ-2", "occ-3"]) == "occ-4"


# ── 存储层 ────────────────────────────────────────────────────────────────

def test_enroll_then_identify_round_trip():
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05), _near(0, 0.03)],
                                      display_name="泓舟", model="m1")
        return await store.identify_speaker("u1", _near(0, 0.04), model="m1")
    out = asyncio.run(go())
    assert out["occupant_id"] == "primary" and out["decision"] == "accept"
    assert out["display_name"] == "泓舟"


def test_second_occupant_is_distinguished():
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      display_name="泓舟", model="m1")
        await store.enroll_voiceprint("u1", [_basis(3), _near(3, 0.05)],
                                      display_name="小雨", model="m1")
        return (await store.identify_speaker("u1", _near(3, 0.02), model="m1"),
                await store.identify_speaker("u1", _near(0, 0.02), model="m1"))
    xiaoyu, hongzhou = asyncio.run(go())
    assert xiaoyu["occupant_id"] == "occ-2" and xiaoyu["display_name"] == "小雨"
    assert hongzhou["occupant_id"] == "primary"


def test_enroll_rejects_inconsistent_samples():
    """三段互不像 → 不建模板（建了此后谁都认不准）。"""
    async def go():
        return await _store().enroll_voiceprint(
            "u1", [_basis(0), _basis(1), _basis(2)], model="m1")
    out = asyncio.run(go())
    assert out["ok"] is False and out["error"] == "low_consistency"


def test_enroll_rejects_dim_mismatch():
    async def go():
        return await _store().enroll_voiceprint("u1", [[1.0, 0.0], [1.0, 0.0, 0.0]],
                                                model="m1")
    assert asyncio.run(go())["error"] == "dim_mismatch"


def test_stale_model_templates_are_skipped_not_compared():
    """换了提取模型，旧模板的余弦不在同一个尺度上——跳过并回 primary，
    绝不拿旧模型的向量跟新模型的向量比大小。"""
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      display_name="泓舟", model="old-model")
        return await store.identify_speaker("u1", _basis(0), model="new-model")
    out = asyncio.run(go())
    assert out["occupant_id"] == "primary" and out["decision"] == "stale_model"


def test_list_marks_stale_templates():
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)], model="old")
        return await store.list_voiceprints("u1", model="new")
    rows = asyncio.run(go())
    assert len(rows) == 1 and rows[0]["stale"] is True


def test_identify_without_templates_is_primary_not_error():
    async def go():
        return await _store().identify_speaker("u1", _basis(0), model="m1")
    assert asyncio.run(go())["occupant_id"] == "primary"


def test_enroll_is_idempotent_per_occupant():
    """重录同一个人 = 更新模板，不是多出一个乘员。"""
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      occupant_id="primary", model="m1")
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.02)],
                                      occupant_id="primary", model="m1")
        return await store.list_voiceprints("u1")
    assert len(asyncio.run(go())) == 1


# ── 删除与 GDPR 级联（红线）────────────────────────────────────────────────

def test_delete_occupant_purges_their_memories_by_default():
    """用户说「删掉小雨」的预期是「忘掉这个人」，不是「留着记忆但认不出他」。"""
    async def go():
        store = _store()
        vs = await store._vec()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      occupant_id="primary", model="m1")
        await store.enroll_voiceprint("u1", [_basis(3), _near(3, 0.05)],
                                      display_name="小雨", model="m1")
        await vs.remember([{"user_id": "u1", "occupant_id": "occ-2",
                            "text": "小雨喜欢草莓", "predicate": "taste.strawberry"}])
        await vs.remember([{"user_id": "u1", "occupant_id": "primary",
                            "text": "用户喜欢吃辣", "predicate": "taste.spicy"}])
        res = await store.delete_voiceprint("u1", "occ-2")
        return res, await vs.export("u1"), await store.list_voiceprints("u1")
    res, memories, rows = asyncio.run(go())
    # 2 条 = 草莓偏好 + 注册时写的 identity.name（「忘掉这个人」当然包括忘掉他的名字）
    assert res["deleted_templates"] == 1 and res["deleted_memories"] == 2
    assert {m["occupant_id"] for m in memories} == {"primary"}   # 主驾的记忆没被误伤
    assert [r["occupant_id"] for r in rows] == ["primary"]


def test_delete_occupant_can_keep_memories_when_asked():
    async def go():
        store = _store()
        vs = await store._vec()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      occupant_id="occ-2", model="m1")
        await vs.remember([{"user_id": "u1", "occupant_id": "occ-2", "text": "x"}])
        await store.delete_voiceprint("u1", "occ-2", purge_memory=False)
        return await vs.export("u1")
    assert len(asyncio.run(go())) == 1


def test_delete_primary_never_purges_whole_car_memory():
    """删单个乘员不该有「清空全车记忆」的爆炸半径——要清空走 ForgetUser。"""
    async def go():
        store = _store()
        vs = await store._vec()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      occupant_id="primary", model="m1")
        await vs.remember([{"user_id": "u1", "occupant_id": "primary", "text": "x"}])
        res = await store.delete_voiceprint("u1", "primary", purge_memory=True)
        return res, await vs.export("u1")
    res, memories = asyncio.run(go())
    assert res["deleted_templates"] == 1
    assert res["deleted_memories"] == 0 and len(memories) == 1


def test_forget_user_cascades_to_voiceprints():
    """**红线**：GDPR 硬删必须连声纹模板一起删。声纹是生物特征，
    留在库里比留关系边更严重——那是假删除。"""
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)], model="m1")
        await store.enroll_voiceprint("u1", [_basis(3), _near(3, 0.05)], model="m1")
        await store.forget_user("u1")
        return await store.list_voiceprints("u1")
    assert asyncio.run(go()) == [], "声纹模板残留 = GDPR 假删除"


def test_forget_other_user_does_not_touch_voiceprints():
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)], model="m1")
        await store.forget_user("u2")
        return await store.list_voiceprints("u1")
    assert len(asyncio.run(go())) == 1


def test_scoped_forget_keeps_voiceprints():
    """按 scope 删一部分记忆不该连带清空声纹（它没有 scope 维度）。"""
    async def go():
        store = _store()
        vs = await store._vec()
        await vs.remember([{"user_id": "u1", "text": "口味", "scope": "profile.taste"}])
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)], model="m1")
        await store.forget_user("u1", scopes=["profile.taste"])
        return await store.list_voiceprints("u1")
    assert len(asyncio.run(go())) == 1


# ── 身份名字进记忆（M4 P4 真机反馈补）───────────────────────────────────────

def test_enroll_writes_identity_name_memory():
    """**只存在声纹表里的名字答不出「你知道我是谁」**——那张表没有任何消费方会去读。
    写进记忆则天然获得召回、导出、GDPR 删除与记忆面板可见性。"""
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      display_name="阿段", model="m1")
        vs = await store._vec()
        return await vs.export("u1")
    items = asyncio.run(go())
    hit = [m for m in items if m.get("predicate") == "identity.name"]
    assert len(hit) == 1, "注册没有把名字写进记忆"
    assert "阿段" in hit[0]["text"]
    assert hit[0]["occupant_id"] == "primary"


def test_rename_supersedes_old_identity_memory():
    """改名不该留下两条现行的名字——旧的走 supersede（记忆的既有时序口径）。"""
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      occupant_id="primary", display_name="阿段", model="m1")
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.03)],
                                      occupant_id="primary", display_name="泓舟", model="m1")
        vs = await store._vec()
        return [m for m in await vs.export("u1")
                if m.get("predicate") == "identity.name" and not m.get("superseded_by")]
    live = asyncio.run(go())
    assert len(live) == 1 and "泓舟" in live[0]["text"]


def test_identity_memory_is_isolated_per_occupant():
    """两个乘员各自的名字互不串（隔离对身份同样成立）。"""
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      display_name="阿段", model="m1")
        await store.enroll_voiceprint("u1", [_basis(3), _near(3, 0.05)],
                                      display_name="小雨", model="m1")
        vs = await store._vec()
        return {m["occupant_id"]: m["text"] for m in await vs.export("u1")
                if m.get("predicate") == "identity.name"}
    names = asyncio.run(go())
    assert "阿段" in names["primary"] and "小雨" in names["occ-2"]


def test_reenroll_with_empty_name_keeps_the_existing_name():
    """**真机 2026-07-26 的名字丢失就是这条**：重录时前端没重填称呼 → 发空串 →
    无条件覆盖把存对的名字冲掉。空名的语义是「这次没打算改名」，不是「把名字清空」。"""
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      occupant_id="primary", display_name="泓舟", model="m1")
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.03)],
                                      occupant_id="primary", display_name="", model="m1")
        vs = await store._vec()
        return (await store.list_voiceprints("u1"),
                [m["text"] for m in await vs.export("u1")
                 if m.get("predicate") == "identity.name" and not m.get("superseded_by")])
    rows, live = asyncio.run(go())
    assert rows[0]["display_name"] == "泓舟", "重录把名字冲掉了"
    assert len(live) == 1 and "泓舟" in live[0]


def test_reenroll_same_name_does_not_pile_up_identity_memories():
    """重录同一个人不该每次都再写一条同名记忆——真机上重录 4 次就攒了 4 条。"""
    async def go():
        store = _store()
        for eps in (0.05, 0.04, 0.03):
            await store.enroll_voiceprint("u1", [_basis(0), _near(0, eps)],
                                          occupant_id="primary", display_name="泓舟",
                                          model="m1")
        vs = await store._vec()
        return [m for m in await vs.export("u1") if m.get("predicate") == "identity.name"]
    assert len(asyncio.run(go())) == 1


# ── 改名（不重录三段）────────────────────────────────────────────────────────

def test_rename_updates_both_the_template_row_and_the_memory():
    """只改表的话助手嘴里还是旧名——identity.name 才是「你知道我是谁」的数据来源。"""
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      occupant_id="primary", display_name="乘客", model="m1")
        res = await store.rename_voiceprint("u1", "primary", "泓舟")
        vs = await store._vec()
        return (res, await store.list_voiceprints("u1"),
                [m["text"] for m in await vs.export("u1")
                 if m.get("predicate") == "identity.name" and not m.get("superseded_by")])
    res, rows, live = asyncio.run(go())
    assert res["ok"] and res["display_name"] == "泓舟"
    assert rows[0]["display_name"] == "泓舟"
    assert len(live) == 1 and "泓舟" in live[0]


def test_rename_keeps_the_template_intact():
    """改名不动模板：改个称呼不该让声纹重新变得认不出人。"""
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      display_name="乘客", model="m1")
        await store.rename_voiceprint("u1", "primary", "泓舟")
        return await store.identify_speaker("u1", _near(0, 0.02), model="m1")
    out = asyncio.run(go())
    assert out["decision"] == "accept" and out["display_name"] == "泓舟"


def test_rename_rejects_empty_name_and_unknown_occupant():
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      display_name="泓舟", model="m1")
        return (await store.rename_voiceprint("u1", "primary", "   "),
                await store.rename_voiceprint("u1", "occ-9", "小雨"))
    empty, missing = asyncio.run(go())
    assert empty["ok"] is False and empty["error"] == "empty_name"
    assert missing["ok"] is False and missing["error"] == "not_found"


def test_delete_primary_retracts_the_name_it_enrolled():
    """primary 不 purge 记忆，但注册写的那条名字要跟着模板走——模板都删了还留着
    「这位乘员的名字是乘客」，助手会继续管一个已经认不出的人叫那个名字。
    **只撤回注册自己写的那条**，用户在对话里说过的别的身份陈述不动。"""
    async def go():
        store = _store()
        vs = await store._vec()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      display_name="乘客", model="m1")
        await vs.remember([{"user_id": "u1", "occupant_id": "primary",
                            "predicate": "identity.name", "text": "我在公司里叫老段"}])
        await store.delete_voiceprint("u1", "primary")
        return [m for m in await vs.export("u1")
                if m.get("predicate") == "identity.name" and not m.get("superseded_by")]
    live = asyncio.run(go())
    assert [m["text"] for m in live] == ["我在公司里叫老段"]


def test_deleting_occupant_removes_their_identity_memory():
    async def go():
        store = _store()
        await store.enroll_voiceprint("u1", [_basis(0), _near(0, 0.05)],
                                      display_name="阿段", model="m1")
        await store.enroll_voiceprint("u1", [_basis(3), _near(3, 0.05)],
                                      display_name="小雨", model="m1")
        await store.delete_voiceprint("u1", "occ-2")
        vs = await store._vec()
        return [m["text"] for m in await vs.export("u1")
                if m.get("predicate") == "identity.name"]
    left = asyncio.run(go())
    assert len(left) == 1 and "阿段" in left[0]
