"""抽取与巩固单测（P1）：四分类治理 + 黑名单 + consolidate 去重/冲突 supersede。

纯 Python，注入 mock complete_fn（不连真实 LLM）。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from extract import extract, _has_coords, _parse  # noqa: E402
from store import MemoryStore  # noqa: E402


def _mock(payload_json: str):
    async def fn(messages):
        return payload_json
    return fn


# ── 抽取治理 ─────────────────────────────────────────────
def test_extract_governs_four_classes_and_blacklist():
    cands = json.dumps([
        {"category": "explicit_preference", "kind": "semantic", "predicate": "route.avoid_highway",
         "text": "以后导航别走高速", "scope": "profile.route", "confidence": 0.9},
        {"category": "temporary_preference", "kind": "semantic", "predicate": "route.today",
         "text": "今天别走高速", "scope": "profile.route", "confidence": 0.8},
        {"category": "inferred_preference", "kind": "semantic", "predicate": "hvac.temp",
         "text": "用户常把空调调到23度", "scope": "profile.comfort", "confidence": 0.9},
        # ↑ hvac.temp 是 climate.temperature 的已知别名 → 写入前归一（B3-3 M2）
        {"category": "sensitive_fact", "kind": "semantic", "predicate": "place.home",
         "text": "家在某小区", "scope": "profile.places", "confidence": 0.95},
        {"category": "explicit_preference", "kind": "semantic", "predicate": "loc",
         "text": "我家坐标31.2304,121.4737", "confidence": 0.9},
    ])
    out = asyncio.run(extract([{"role": "user", "text": "x"}], user_id="u1",
                              complete_fn=_mock(cands)))
    preds = {o["predicate"]: o for o in out}
    assert "place.home" not in preds       # sensitive_fact 默认不自动写
    assert "loc" not in preds              # 黑名单：精确坐标丢弃
    assert preds["route.avoid_highway"]["provenance"] == "user_stated"
    assert preds["route.avoid_highway"]["confidence"] >= 0.7
    assert preds["route.today"]["expires_at"] > 0                 # temporary 带过期
    assert "hvac.temp" not in preds                              # 别名已归一
    assert preds["climate.temperature"]["provenance"] == "agent_inferred"
    assert preds["climate.temperature"]["confidence"] <= 0.5     # inferred 低置信
    assert all(o["review_status"] == "auto_extracted" for o in out)


def test_extract_personal_fact_stored_and_pii_dropped():
    """用户主动告知的个人实体（宠物名）→ 存 profile.person/sensitive；电话等 PII → 丢。"""
    cands = json.dumps([
        {"category": "personal_fact", "kind": "semantic", "predicate": "person.pet",
         "text": "用户的宠物叫旺财", "scope": "profile.person", "confidence": 0.9},
        {"category": "personal_fact", "kind": "semantic", "predicate": "person.phone",
         "text": "用户电话13800001111", "confidence": 0.9},  # 11 位数字 → PII 丢弃
    ])
    out = asyncio.run(extract([{"role": "user", "text": "x"}], user_id="u1",
                              complete_fn=_mock(cands)))
    preds = {o["predicate"]: o for o in out}
    assert "person.pet" in preds and "person.phone" not in preds
    pet = preds["person.pet"]
    assert pet["scope"] == "profile.person"
    assert pet["privacy_level"] == "sensitive" and pet["provenance"] == "user_stated"


def test_extract_empty_on_llm_error():
    async def boom(messages):
        raise RuntimeError("llm down")
    assert asyncio.run(extract([{"role": "user", "text": "x"}], user_id="u1",
                               complete_fn=boom)) == []


def test_extract_parses_fenced_json():
    fenced = ('```json\n[{"category":"explicit_preference","kind":"semantic",'
              '"predicate":"taste.spicy","text":"不吃辣","scope":"profile.taste",'
              '"confidence":0.9}]\n```')
    out = asyncio.run(extract([{"role": "user", "text": "x"}], user_id="u1",
                              complete_fn=_mock(fenced)))
    assert len(out) == 1 and out[0]["predicate"] == "taste.spicy"


def test_extract_no_turns_or_no_user():
    assert asyncio.run(extract([], user_id="u1", complete_fn=_mock("[]"))) == []
    assert asyncio.run(extract([{"role": "user", "text": "x"}], user_id="",
                               complete_fn=_mock("[]"))) == []


def test_has_coords_and_parse():
    assert _has_coords("31.2304,121.4737") and _has_coords("经度121")
    assert not _has_coords("用户不吃辣")
    assert _parse("garbage no json") == []


# ── 场景配置参数黑名单（B3-3 M1）──────────────────────────
_SCENE_TURNS = [
    {"role": "user", "text": "创建一个钓鱼模式：空调22度，氛围灯蓝色"},
    {"role": "assistant", "text": "将创建「钓鱼模式」：空调22度、氛围灯蓝色。确认保存吗？"},
    {"role": "user", "text": "确认"},
]


def test_scene_config_params_not_extracted_as_preference():
    """「创建钓鱼模式：空调22度」的 22 度是场景配置——即使 LLM 抽成偏好也被治理丢弃。"""
    cands = json.dumps([
        {"category": "explicit_preference", "kind": "semantic",
         "predicate": "climate.temperature", "text": "用户最喜欢的空调温度是22度",
         "scope": "profile.comfort", "confidence": 0.9},
        {"category": "inferred_preference", "kind": "semantic",
         "predicate": "light.ambient_color", "text": "用户喜欢蓝色氛围灯",
         "scope": "profile.comfort", "confidence": 0.8},
        {"category": "episodic", "kind": "episodic", "predicate": "",
         "text": "用户创建了钓鱼模式", "scope": "episodic.general", "confidence": 0.8},
    ])
    out = asyncio.run(extract(list(_SCENE_TURNS), user_id="u1",
                              complete_fn=_mock(cands)))
    texts = [o["text"] for o in out]
    assert "用户最喜欢的空调温度是22度" not in texts   # 场景参数 → 丢
    assert "用户喜欢蓝色氛围灯" not in texts           # 场景颜色 → 丢
    assert "用户创建了钓鱼模式" in texts               # 情景事件本身照常保留


def test_scene_activation_param_dropped_but_stated_preference_kept():
    """「开启午休模式，温度26」的 26 是一次性覆盖 → 丢；显式「记住我最喜欢26度」→ 留。"""
    activation = [{"role": "user", "text": "开启午休模式，温度26"}]
    stated = [{"role": "user", "text": "记住，我最喜欢的空调温度是26度"}]
    cand = json.dumps([
        {"category": "explicit_preference", "kind": "semantic",
         "predicate": "climate.temperature", "text": "用户最喜欢的空调温度是26度",
         "scope": "profile.comfort", "confidence": 0.9},
    ])
    dropped = asyncio.run(extract(activation, user_id="u1", complete_fn=_mock(cand)))
    kept = asyncio.run(extract(stated, user_id="u1", complete_fn=_mock(cand)))
    assert dropped == []
    assert len(kept) == 1 and kept[0]["predicate"] == "climate.temperature"


def test_preference_stated_inside_scene_sentence_kept():
    """场景句里带偏好口吻（「记住我喜欢…」）不算纯场景配置——不误伤。"""
    turns = [{"role": "user", "text": "睡觉模式的时候记住我喜欢24度"}]
    cand = json.dumps([
        {"category": "explicit_preference", "kind": "semantic",
         "predicate": "climate.temperature", "text": "用户喜欢24度",
         "scope": "profile.comfort", "confidence": 0.9},
    ])
    out = asyncio.run(extract(turns, user_id="u1", complete_fn=_mock(cand)))
    assert len(out) == 1


# ── consolidate ──────────────────────────────────────────
def _store() -> MemoryStore:
    s = MemoryStore()
    s.url = ""              # Redis 内存兜底
    s._vstore._dsn = ""     # 向量存储内存兜底
    return s


def test_consolidate_insert_then_conflict_supersede():
    store = _store()

    async def go():
        await store.append_turn("s1", "user", "我不吃辣")
        j1 = _mock(json.dumps([{"category": "explicit_preference", "kind": "semantic",
                                "predicate": "taste.spicy", "text": "用户不吃辣",
                                "scope": "profile.taste", "confidence": 0.9}]))
        ids1 = await store.consolidate("s1", "u1", complete_fn=j1)
        j2 = _mock(json.dumps([{"category": "explicit_preference", "kind": "semantic",
                                "predicate": "taste.spicy", "text": "用户现在能吃辣了",
                                "scope": "profile.taste", "confidence": 0.9}]))
        ids2 = await store.consolidate("s1", "u1", complete_fn=j2)
        current = await store.recall(user_id="u1", query="", scopes=["profile.taste"])
        return ids1, ids2, current

    ids1, ids2, current = asyncio.run(go())
    assert len(ids1) == 1 and len(ids2) == 1
    assert len(current) == 1 and current[0][0]["text"] == "用户现在能吃辣了"  # 只取现行


def test_consolidate_supersedes_across_predicate_aliases():
    """B3-3 M2：历史条目带 LLM 自由造的别名谓词（hvac.temperature），新显式偏好
    （climate.temperature）也要 supersede 它——否则新旧并存、召回二义（22 vs 26）。"""
    store = _store()

    async def go():
        vs = await store._vec()
        await vs.remember([{  # 模拟修复前入库的旧偏好（别名谓词）
            "user_id": "u1", "occupant_id": "primary", "kind": "semantic",
            "predicate": "hvac.temperature", "text": "用户最喜欢的空调温度是22度",
            "scope": "profile.comfort", "provenance": "user_stated",
            "confidence": 0.9, "review_status": "auto_extracted"}])
        await store.append_turn("s1", "user", "记住，我最喜欢的空调温度是26度")
        j = _mock(json.dumps([{"category": "explicit_preference", "kind": "semantic",
                               "predicate": "climate.temperature",
                               "text": "用户最喜欢的空调温度是26度",
                               "scope": "profile.comfort", "confidence": 0.9}]))
        ids = await store.consolidate("s1", "u1", complete_fn=j)
        current = await store.recall(user_id="u1", query="空调温度")
        return ids, current

    ids, current = asyncio.run(go())
    assert len(ids) == 1
    assert len(current) == 1                              # 旧 22 已被时序覆盖
    assert "26度" in current[0][0]["text"]
    assert current[0][0]["predicate"] == "climate.temperature"


def test_consolidate_equivalent_skips():
    store = _store()

    async def go():
        await store.append_turn("s1", "user", "我不吃辣")
        j = _mock(json.dumps([{"category": "explicit_preference", "kind": "semantic",
                               "predicate": "taste.spicy", "text": "用户不吃辣",
                               "scope": "profile.taste", "confidence": 0.9}]))
        await store.consolidate("s1", "u1", complete_fn=j)
        ids2 = await store.consolidate("s1", "u1", complete_fn=j)  # 等价
        exported = await store.export_user("u1")
        return ids2, exported

    ids2, exported = asyncio.run(go())
    assert ids2 == []                              # 等价跳过，不重复写
    assert len(exported["memories"]) == 1


# ── 观测归属（2026-07-13 消耗排查）────────────────────────
def test_default_complete_request_carries_caller_service():
    """抽取的 Complete 必须带 caller_service（obs.llm 归属；此前 caller 为空=盲区）。
    刻意不设 "caller"——那是网关限流桶键（惯例同 planner/SDK）。"""
    from extract import _build_complete_request
    req = _build_complete_request([{"role": "user", "content": "hi"}])
    assert req.meta["caller_service"] == "memory-extract"
    assert "caller" not in req.meta
    assert req.messages[0].content == "hi"
