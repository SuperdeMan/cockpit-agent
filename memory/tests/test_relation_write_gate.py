"""关系图谱写入闸（QA 卡 Q5，本批最深的一处）。

`memory/relation.py` 此前只归一了**谓词词表**（`REL_VOCAB` + 别名），没有主宾角色
校验、没有自环守卫、没有 `(subject, rel)` 单值约束——`superseded_by` 列存在但
**从未写过**。2026-08-15 psql 实测库里的现状（本文件的用例逐条取自那次取证）：

| 实测行 | 病 |
|---|---|
| `深圳国家工程实验大楼A栋 --works_at--> 用户` | 主宾颠倒 |
| `公司 --lives_at--> 深圳国家工程实验大楼A栋` | 把「公司」当人 |
| `老婆 --family--> 老婆`（女儿/孩子/爸妈各一） | 自环边，零信息 |
| `深圳 --place_of--> 出发地` | 主宾颠倒 + 把「出发地」当实体 |
| `女儿 --place_of-->` 三个不同学校 | 同一个孩子三个学校，无 supersede |

> **这直接改写了 I-044 的定性**：不是「用相似 persona 记忆补全」，是**图谱里真有
> 三条互相矛盾的边，每轮召回哪条看运气**。

⚠ 清洗脚本那次 dry-run 当场劝退过一条判据：**「同一个 subject 有多个 object」
不是冲突，除非那个谓词本身是单值的**——`爸妈--family-->爸爸` 与
`爸妈--family-->妈妈` 都是真的。所以单值约束**只对 `works_at/lives_at/place_of`** 生效。
"""
from __future__ import annotations

import pytest

from memory import relation


def _cand(subject, rel, obj, **kw):
    return relation.normalize_candidate(
        {"subject": subject, "rel": rel, "object": obj, **kw})


# ── 自环 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("word", ["老婆", "女儿", "孩子", "爸妈"])
def test_family_self_loop_is_the_unnamed_person_idiom_not_noise(word):
    """⚠ **卡上的定性在这一条上是错的，读消费方才发现。**

    卡 §3-Q5 把 `老婆 --family--> 老婆` 记成「自环边，零信息」，清洗脚本的 ① 族
    正准备删掉它们。实际上它是**「无名的人」的表示法**——
    `store.resolve_person_place` 靠 family 边的 **object** 反查人实体，
    没名字的人就以称谓自身作实体名。删了它，「老婆在哪上班」这类一跳解析当场失效。

    首版写入闸把 family 自环也拒了，`test_resolve_person_place_via_works_at` 与
    `test_deterministic_place_relation_without_name` 两条既有断言当场红——
    **是既有测试拦住了一次按错误定性动手的改动**。
    """
    assert _cand(word, "family", word) is not None


@pytest.mark.parametrize("rel", ["place_of", "works_at", "lives_at", "owns"])
def test_self_loop_on_other_relations_is_rejected(rel):
    """非 family 的自环没有这层含义：`公司 --works_at--> 公司` 就是零信息。"""
    assert _cand("某某", rel, "某某") is None


# ── 主宾角色 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("subject,rel,obj", [
    ("深圳国家工程实验大楼A栋", "works_at", "用户"),   # 实测行：主宾颠倒
    ("深圳", "place_of", "出发地"),                     # 实测行：把「出发地」当实体
    ("南山实验小学", "place_of", "女儿"),               # 同族：地点在前、人在后
])
def test_reversed_place_relation_is_rejected(subject, rel, obj):
    """地点类关系的方向是**固定**的：人 → 地点。反过来存进去，消费侧
    「我女儿在哪上学」永远查不到，而它在库里看起来像一条正常的边。"""
    assert _cand(subject, rel, obj) is None


def test_place_like_subject_is_rejected():
    """`公司 --lives_at--> 深圳国家工程实验大楼A栋`——把「公司」当人。"""
    assert _cand("公司", "lives_at", "深圳国家工程实验大楼A栋") is None


def test_correct_direction_still_passes():
    """反向对照——**这一半和上一半一样重要**（§4.3「反向验证要两头做」）。"""
    for subject, rel, obj in (
            ("女儿", "place_of", "深圳市南山实验小学"),
            ("用户", "works_at", "深圳国家工程实验大楼A栋"),
            ("老婆", "works_at", "深圳湾万象城"),
            ("我", "lives_at", "科技园"),
    ):
        assert _cand(subject, rel, obj) is not None, (subject, rel, obj)


def test_non_place_relations_are_not_role_checked():
    """`family`/`owns`/`prefers_brand` 的宾语本来就该是人/物，不走地点角色判据。"""
    assert _cand("小雨", "family", "女儿") is not None
    assert _cand("用户", "owns", "特斯拉Model Y") is not None


# ── 单值谓词 ──────────────────────────────────────────────────────────────

def test_single_valued_predicates():
    """⚠ 清洗 dry-run 当场劝退的那条判据：**「同一个 subject 有多个 object」不是
    冲突，除非那个谓词本身是单值的**。首版把 `爸妈--family-->爸爸` 与
    `爸妈--family-->妈妈` 判成冲突，准备把「妈妈」标失效——**直接丢掉一个真实的人**。
    """
    assert relation.is_single_valued("place_of") is True
    assert relation.is_single_valued("works_at") is True
    assert relation.is_single_valued("lives_at") is True
    assert relation.is_single_valued("family") is False
    assert relation.is_single_valued("owns") is False
    assert relation.is_single_valued("prefers_brand") is False


# ── 置信度 ────────────────────────────────────────────────────────────────

def test_low_confidence_is_not_persisted():
    assert _cand("女儿", "place_of", "某小学", confidence=0.2) is None
    assert _cand("女儿", "place_of", "某小学", confidence=0.9) is not None


def test_missing_confidence_still_persists():
    """缺省 confidence 是 1.0（既有语义）——本批不得把「没写」当成「低」。"""
    assert _cand("女儿", "place_of", "某小学") is not None


def test_slot_name_leakage_is_rejected():
    """`深圳 --place_of--> 出发地`（库里实测）——「出发地」不是实体，是 planner 的
    **槽名**漏进了图谱。它既不是人也不是地点，前两道闸都不触发，所以单独成一类。
    存进去的边永远没有消费方，只会在召回里当噪声。"""
    for subject, rel, obj in (
            ("深圳", "place_of", "出发地"),
            ("女儿", "place_of", "未知"),
            ("公司", "place_of", "深圳"),      # subject 是槽名
            ("用户", "lives_at", "地址"),
    ):
        assert _cand(subject, rel, obj) is None, (subject, rel, obj)


def test_real_place_named_like_a_slot_word_is_not_over_blocked():
    """反向：真地名里**包含**这些字不算槽名——只按整词判。
    「公司」是槽名，「深圳国家工程实验大楼A栋」不是。"""
    assert _cand("用户", "works_at", "深圳国家工程实验大楼A栋") is not None
    assert _cand("老婆", "works_at", "深圳湾万象城") is not None


# ── 单值 supersede（`superseded_by` 列一直存在却从未写过）────────────────────

import asyncio  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from store import MemoryStore  # noqa: E402


async def _add(store, edges):
    vs = await store._vec()
    return await vs.add_relations("u1", edges)


async def _live(store, **kw):
    vs = await store._vec()
    return await vs.query_relations("u1", **kw)


def test_single_valued_relation_supersedes_the_older_edge():
    """**「同一个孩子三个学校」的根因**：`superseded_by` 列一直存在却从未写过，
    于是三条互相矛盾的边同时现行，每轮召回哪条看运气——**这就是 I-044「幻觉」的真身**。"""
    async def go():
        st = MemoryStore()
        await _add(st, [{"subject": "女儿", "rel": "place_of",
                         "object": "南山实验小学"}])
        await _add(st, [{"subject": "女儿", "rel": "place_of",
                         "object": "南山外国语学校"}])
        return await _live(st, subject="女儿", rel="place_of")
    live = asyncio.run(go())
    assert [e["object"] for e in live] == ["南山外国语学校"], \
        "旧边没有被 supersede ⇒ 两条现行边并存，召回看运气"


def test_multi_valued_relation_keeps_both():
    """反向对照——**这一半是 dry-run 当场劝退我的那一条**：
    `爸妈--family-->爸爸` 与 `爸妈--family-->妈妈` 都是真的，
    supersede 掉一条等于**丢掉一个真实的人**。"""
    async def go():
        st = MemoryStore()
        await _add(st, [{"subject": "爸妈", "rel": "family", "object": "爸爸"}])
        await _add(st, [{"subject": "爸妈", "rel": "family", "object": "妈妈"}])
        return await _live(st, subject="爸妈", rel="family")
    live = asyncio.run(go())
    assert {e["object"] for e in live} == {"爸爸", "妈妈"}
