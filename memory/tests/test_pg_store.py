"""分层记忆存储单测（P0）：内存兜底路径（无 PG、无 embedding 模型，lexical 召回）。

pg_store.py / store.py 为纯 Python，直接驱动。验证写读、scope/occupant/kind 过滤、
时序-lite（superseded 默认排除）、合规 forget/export。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pg_store import MemoryVectorStore, _lexical_score, _normalize_item  # noqa: E402
from store import MemoryStore  # noqa: E402


def _vstore() -> MemoryVectorStore:
    s = MemoryVectorStore("")  # 无 DSN → 内存兜底
    assert asyncio.run(s.init()) is False
    return s


def _sem(user, text, predicate, scope, occupant="primary", confidence=1.0):
    return {"user_id": user, "kind": "semantic", "text": text, "predicate": predicate,
            "scope": scope, "occupant_id": occupant, "confidence": confidence}


# ── 写读闭环 ──────────────────────────────────────────────
def test_remember_then_recall_hits_relevant():
    vs = _vstore()

    async def go():
        await vs.remember([
            _sem("u1", "用户不吃辣", "taste.spicy", "profile.taste"),
            _sem("u1", "用户喜欢摇滚乐", "music.genre", "profile.music"),
        ])
        return await vs.recall("u1", query="辣")

    hits = asyncio.run(go())
    assert hits, "应召回到与‘辣’相关的偏好"
    assert hits[0][0]["predicate"] == "taste.spicy"
    assert hits[0][1] > 0  # 有分数


def test_remember_returns_ids_and_generates_uuid():
    vs = _vstore()
    ids = asyncio.run(vs.remember([_sem("u1", "用户不吃辣", "taste.spicy", "profile.taste")]))
    assert len(ids) == 1 and ids[0]


# ── 过滤 ─────────────────────────────────────────────────
def test_recall_scope_filter():
    vs = _vstore()

    async def go():
        await vs.remember([
            _sem("u1", "用户不吃辣", "taste.spicy", "profile.taste"),
            _sem("u1", "用户喜欢辣的音乐节奏", "music.genre", "profile.music"),
        ])
        only_taste = await vs.recall("u1", query="辣", scopes=["profile.taste"])
        return only_taste

    hits = asyncio.run(go())
    assert all(h[0]["scope"] == "profile.taste" for h in hits)
    assert any(h[0]["predicate"] == "taste.spicy" for h in hits)


def test_recall_kind_filter_excludes_episodic():
    vs = _vstore()

    async def go():
        await vs.remember([
            _sem("u1", "用户不吃辣", "taste.spicy", "profile.taste"),
            {"user_id": "u1", "kind": "episodic", "text": "在西湖吃了辣的菜",
             "scope": "episodic.general"},
        ])
        return await vs.recall("u1", query="辣", kinds=["semantic"])

    hits = asyncio.run(go())
    assert hits and all(h[0]["kind"] == "semantic" for h in hits)


def test_recall_occupant_isolation():
    vs = _vstore()

    async def go():
        await vs.remember([
            _sem("u1", "驾驶员不吃辣", "taste.spicy", "profile.taste", occupant="primary"),
            _sem("u1", "乘客很能吃辣", "taste.spicy", "profile.taste", occupant="passenger"),
        ])
        default_occ = await vs.recall("u1", query="辣")  # 默认 primary
        passenger = await vs.recall("u1", query="辣", occupant_id="passenger")
        return default_occ, passenger

    default_occ, passenger = asyncio.run(go())
    assert all(h[0]["occupant_id"] == "primary" for h in default_occ)
    assert any(h[0]["text"] == "乘客很能吃辣" for h in passenger)


def test_recall_empty_query_lists_scope():
    vs = _vstore()

    async def go():
        await vs.remember([_sem("u1", "用户不吃辣", "taste.spicy", "profile.taste")])
        return await vs.recall("u1", query="", scopes=["profile.taste"])

    hits = asyncio.run(go())
    assert len(hits) == 1 and hits[0][0]["predicate"] == "taste.spicy"


# ── 时序-lite（supersede）────────────────────────────────
def test_supersede_excludes_old_by_default():
    vs = _vstore()

    async def go():
        ids_old = await vs.remember([_sem("u1", "用户不吃辣", "taste.spicy", "profile.taste")])
        ids_new = await vs.remember([_sem("u1", "用户现在能吃辣了", "taste.spicy", "profile.taste")])
        await vs.supersede(ids_old[0], ids_new[0])
        current = await vs.recall("u1", query="辣")
        with_old = await vs.recall("u1", query="辣", include_superseded=True)
        return current, with_old

    current, with_old = asyncio.run(go())
    assert len(current) == 1 and current[0][0]["text"] == "用户现在能吃辣了"
    assert len(with_old) == 2  # include_superseded 取回旧值


# ── 合规 forget / export ─────────────────────────────────
def test_forget_user_removes_all():
    vs = _vstore()

    async def go():
        await vs.remember([
            _sem("u1", "用户不吃辣", "taste.spicy", "profile.taste"),
            _sem("u2", "另一个用户", "taste.spicy", "profile.taste"),
        ])
        deleted = await vs.forget("u1")
        remain_u1 = await vs.recall("u1", query="辣")
        remain_u2 = await vs.recall("u2", query="用户")
        return deleted, remain_u1, remain_u2

    deleted, remain_u1, remain_u2 = asyncio.run(go())
    assert deleted == 1
    assert remain_u1 == []
    assert remain_u2  # 不误删别的用户


def test_forget_scoped_only():
    vs = _vstore()

    async def go():
        await vs.remember([
            _sem("u1", "用户不吃辣", "taste.spicy", "profile.taste"),
            _sem("u1", "用户喜欢摇滚乐", "music.genre", "profile.music"),
        ])
        await vs.forget("u1", scopes=["profile.taste"])
        return await vs.export("u1")

    items = asyncio.run(go())
    assert len(items) == 1 and items[0]["scope"] == "profile.music"


def test_export_strips_embedding():
    vs = _vstore()
    asyncio.run(vs.remember([_sem("u1", "用户不吃辣", "taste.spicy", "profile.taste")]))
    items = asyncio.run(vs.export("u1"))
    assert len(items) == 1
    assert "embedding" not in items[0]
    assert items[0]["predicate"] == "taste.spicy"


# ── 评审后：精确优先 / 隐私 / 过期 / 阈值 ─────────────────
def test_predicate_prefix_exact_filter():
    vs = _vstore()

    async def go():
        await vs.remember([
            {"user_id": "u1", "text": "家在深圳南山", "predicate": "place.home",
             "scope": "profile.places"},
            _sem("u1", "用户不吃辣", "taste.spicy", "profile.taste"),
        ])
        return await vs.recall("u1", query="", predicate_prefix="place.")

    hits = asyncio.run(go())
    assert len(hits) == 1 and hits[0][0]["predicate"] == "place.home"


def test_highly_sensitive_excluded_from_generalization():
    vs = _vstore()

    async def go():
        await vs.remember([{
            "user_id": "u1", "text": "家在深圳南山腾讯滨海大厦", "predicate": "place.home",
            "scope": "profile.places", "privacy_level": "highly_sensitive"}])
        # 泛化召回（无 scope/predicate）：高敏不应出现
        general = await vs.recall("u1", query="家")
        # 显式定向（predicate_prefix）：可取回
        targeted = await vs.recall("u1", query="家", predicate_prefix="place.")
        return general, targeted

    general, targeted = asyncio.run(go())
    assert general == []
    assert len(targeted) == 1 and targeted[0][0]["predicate"] == "place.home"


def test_expired_temporary_pref_not_recalled():
    vs = _vstore()

    async def go():
        await vs.remember([
            {"user_id": "u1", "text": "今天别走高速", "predicate": "route.avoid_highway",
             "scope": "profile.route", "expires_at": 1},  # 早已过期
            _sem("u1", "用户喜欢摇滚乐", "music.genre", "profile.music"),
        ])
        return await vs.recall("u1", query="", scopes=["profile.route"])

    hits = asyncio.run(go())
    assert hits == []  # 过期临时偏好不召回


def test_min_confidence_threshold():
    vs = _vstore()

    async def go():
        await vs.remember([
            _sem("u1", "用户大概不吃辣", "taste.spicy", "profile.taste", confidence=0.3),
            _sem("u1", "用户明确喜欢清淡", "taste.light", "profile.taste", confidence=0.9),
        ])
        return await vs.recall("u1", query="", scopes=["profile.taste"], min_confidence=0.5)

    hits = asyncio.run(go())
    assert len(hits) == 1 and hits[0][0]["predicate"] == "taste.light"


def test_no_real_embedder_means_semantic_unavailable():
    vs = _vstore()
    # 内存兜底无模型：语义不可用，召回走 lexical（诚实），不报错
    assert vs.semantic_available is False


def test_normalize_governance_defaults():
    it = _normalize_item({"user_id": "u1", "text": "x"})
    assert it["tenant_id"] == "default" and it["memory_level"] == "user"
    assert it["privacy_level"] == "normal" and it["review_status"] == "user_confirmed"
    assert it["valid_to"] == 0 and it["expires_at"] == 0


# ── 纯函数 ───────────────────────────────────────────────
def test_lexical_score_substring_bonus():
    assert _lexical_score("口味", "用户口味偏辣") >= 0.6  # 子串命中加成
    assert _lexical_score("", "任意") == 0.0


def test_normalize_item_defaults():
    it = _normalize_item({"user_id": "u1", "text": "x"})
    assert it["id"] and it["kind"] == "semantic" and it["occupant_id"] == "primary"
    assert it["provenance"] == "user_stated" and it["confidence"] == 1.0


# ── store.py 门面（server 走这条）─────────────────────────
def test_store_facade_remember_recall_forget_export():
    store = MemoryStore()
    store.url = ""  # Redis 内存兜底

    async def go():
        await store.remember([_sem("u1", "用户不吃辣", "taste.spicy", "profile.taste")])
        hits = await store.recall(user_id="u1", query="辣")
        exported = await store.export_user("u1")
        deleted = await store.forget_user("u1")
        after = await store.recall(user_id="u1", query="辣")
        return hits, exported, deleted, after

    hits, exported, deleted, after = asyncio.run(go())
    assert hits and hits[0][0]["predicate"] == "taste.spicy"
    assert exported["memories"] and "profile" in exported
    assert deleted == 1 and after == []
