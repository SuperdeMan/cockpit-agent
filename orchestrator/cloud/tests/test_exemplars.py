"""范例库契约测试（M5 P1 数据飞轮）。契约 `skills/exemplars/README.md`。

覆盖四件事，每件都对应一个「错了会很贵」的性质：
  ① 最软层——范例层任何异常/坏文件都不许影响规划（它的全部价值就是「写错了不伤人」）；
  ② 归因诚实——名单必须反映真实注入（被裁记 !clipped），否则 badcase 归因会说谎；
  ③ 继承贯通——T2 再规划与挂起恢复不失忆（skills 为此补过三次漏链）；
  ④ 与 skills 的边界——范例文件绝不能被 SkillStore 当 skill 文档吃掉。
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.cloud import embedding as _embedding
from orchestrator.cloud import exemplars as ex
from orchestrator.cloud import skills as sk


def _write(root, domain: str, body: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{domain}.yaml").write_text(body, encoding="utf-8")


def _mini(tmp_path):
    root = tmp_path / "exemplars"
    _write(root, "nearby",
           "domain: nearby\nexemplars:\n"
           "  - text: 附近有什么咖啡店\n    plan:\n      - agent: nearby\n"
           "        intent: nearby.search\n        slots: {keyword: 咖啡店}\n"
           "    source: trace\n    added: '2026-07-29'\n")
    _write(root, "vision",
           "domain: vision\nexemplars:\n"
           "  - text: 那是什么东西\n    plan:\n      - agent: vision\n"
           "        intent: vision.describe\n    source: manifest\n")
    return ex.ExemplarStore(root=root)


# ── ① 最软层：坏文件/异常绝不影响规划 ────────────────────────────────────────

def test_bad_files_never_break_loading(tmp_path):
    """顶层数组 / 缺 plan / plan 步缺 intent / source 非法——逐条降级，好条目照常可用。"""
    root = tmp_path / "exemplars"
    _write(root, "broken", "- 顶层写成数组\n- 就是坏文件\n")
    _write(root, "half",
           "domain: half\nexemplars:\n"
           "  - text: 没有计划的一条\n"
           "  - text: 半条计划\n    plan:\n      - agent: x\n"      # 缺 intent
           "  - text: 好的一条\n    plan:\n      - intent: nearby.search\n"
           "    source: 乱写的来源\n")
    store = ex.ExemplarStore(root=root)
    items = store.load()
    assert [e.text for e in items] == ["好的一条"]
    assert items[0].source == "manual"            # 非法 source 回落而不是丢条目


def test_retrieval_exception_is_swallowed(monkeypatch, tmp_path):
    """检索炸了 → 本轮无范例，规划继续（返回三元组形状不变）。"""
    monkeypatch.setattr(ex, "_default_store", _mini(tmp_path))

    async def boom(*a, **k):
        raise RuntimeError("模拟检索崩溃")

    monkeypatch.setattr(ex, "retrieve", boom)
    mode, names, block = asyncio.run(ex.plan_exemplars("附近咖啡店"))
    assert (mode, names, block) == ("full", [], "")


def test_off_and_shadow_modes(monkeypatch, tmp_path):
    monkeypatch.setattr(ex, "_default_store", _mini(tmp_path))
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")
    monkeypatch.setenv("EXEMPLARS_MODE", "off")
    assert asyncio.run(ex.plan_exemplars("附近有什么咖啡店")) == ("off", [], "")
    monkeypatch.setenv("EXEMPLARS_MODE", "shadow")
    mode, names, block = asyncio.run(ex.plan_exemplars("附近有什么咖啡店"))
    assert mode == "shadow" and names and block == ""    # 只记录、零行为变化


# ── 词法通道：IDF 加权是「功能词不该支配短文本」的根治 ───────────────────────

def test_idf_downweights_function_word_overlap(tmp_path):
    """裸 Dice 下「现在是什么情况」会靠「现在/什么」高分匹配 vision；IDF 加权后
    功能词在语料里到处都是→权重被压低，实义词才决定排序。"""
    root = tmp_path / "exemplars"
    _write(root, "vision",
           "domain: vision\nexemplars:\n"
           "  - text: 那是什么东西\n    plan:\n      - intent: vision.describe\n")
    _write(root, "nearby",
           "domain: nearby\nexemplars:\n"
           + "".join(f"  - text: 附近有什么{w}\n    plan:\n      - intent: nearby.search\n"
                     for w in ("咖啡店", "川菜馆", "加油站", "书店", "药店")))
    items = ex.ExemplarStore(root=root).load()
    idf = ex.build_idf(items)
    v = next(e for e in items if e.domain == "vision")
    naked = ex.lex_score("附近有什么充电桩", v)
    weighted = ex.lex_score("附近有什么充电桩", v, idf)
    assert weighted < naked        # 「有什么」在语料里遍地都是 → 权重被压


def test_same_domain_dedup_keeps_the_boundary_visible(monkeypatch, tmp_path):
    """同域去重在**选取时**生效：3 条同域近义句挤满预算，等于放弃让模型看到边界。
    阈值放到最低——这里验的是去重规则本身，不是阈值。"""
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")
    monkeypatch.setenv("EXEMPLAR_LEX_THRESHOLD", "0.01")
    root = tmp_path / "exemplars"
    _write(root, "nearby",
           "domain: nearby\nexemplars:\n"
           + "".join(f"  - text: 附近有什么{w}\n    plan:\n      - intent: nearby.search\n"
                     for w in ("咖啡店", "咖啡厅", "咖啡馆")))
    _write(root, "navigation",
           "domain: navigation\nexemplars:\n"
           "  - text: 附近的咖啡店导航过去\n    plan:\n"
           "      - intent: navigation.search_poi\n")
    store = ex.ExemplarStore(root=root)
    pairs = asyncio.run(ex.retrieve("附近有什么咖啡店", store, k=3))
    assert len(pairs) == 2
    assert sorted(e.domain for e, _, _ in pairs) == ["navigation", "nearby"]


# ── ② 归因诚实：名单必须反映真实注入 ────────────────────────────────────────

def test_clipped_entries_are_marked_not_claimed(monkeypatch, tmp_path):
    """预算裁掉的范例记 `!clipped`——「说注入了实际被裁」会让 badcase 归因说谎。"""
    monkeypatch.setattr(ex, "_default_store", _mini(tmp_path))
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")
    monkeypatch.setattr(ex, "EXEMPLAR_BUDGET", len(ex._BLOCK_HEAD) + 5)
    mode, names, block = asyncio.run(ex.plan_exemplars("附近有什么咖啡店"))
    assert names and all(n.endswith("!clipped") for n in names)
    assert block == ""             # 一条都没进 → 不留只有抬头的空块污染 prompt


def test_names_carry_channel_and_score(monkeypatch, tmp_path):
    monkeypatch.setattr(ex, "_default_store", _mini(tmp_path))
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")
    _, names, block = asyncio.run(ex.plan_exemplars("附近有什么咖啡店"))
    assert names[0].startswith("full:nearby#1@lex:")
    assert "nearby.search" in block and "仅供参考不是规则" in block


# ── 语义通道：fail-open + 补位 ──────────────────────────────────────────────

def test_hybrid_fails_open_to_lexical(monkeypatch, tmp_path):
    """Embed 不可用 → 该轮纯词法，绝不堵规划（与 skills 共享同一段冷却）。"""
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "hybrid")
    _embedding.reset_cooldown()

    async def dead(texts, timeout_s=1.0):
        return None

    monkeypatch.setattr(_embedding, "embed_texts", dead)
    store = _mini(tmp_path)
    pairs = asyncio.run(ex.retrieve("附近有什么咖啡店", store))
    assert [(e.domain, ch) for e, ch, _ in pairs] == [("nearby", "lex")]


def test_semantic_supplements_lexical_miss(monkeypatch, tmp_path):
    """词法零命中的改写句由语义补位，通道记 @vec——范例库存在的全部理由。"""
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "hybrid")
    _embedding.reset_cooldown()
    store = _mini(tmp_path)
    query = "想喝杯拿铁，边上有店吗"          # 与「附近有什么咖啡店」零 bigram 重合
    table = {query: (1.0, 0.0), "附近有什么咖啡店": (0.99, 0.14),
             "那是什么东西": (0.0, 1.0)}

    async def fake(texts, timeout_s=1.0):
        return [table[t] for t in texts], "fake-embed"

    monkeypatch.setattr(_embedding, "embed_texts", fake)
    assert ex.top_lexical(query, store.load(), idf=store.idf()) == []
    pairs = asyncio.run(ex.retrieve(query, store))
    assert [(e.domain, ch) for e, ch, _ in pairs] == [("nearby", "vec")]


# ── ③ 继承贯通：T2 再规划 / 挂起恢复不失忆 ──────────────────────────────────

def test_render_for_names_skips_shadow_and_clipped(monkeypatch, tmp_path):
    monkeypatch.setattr(ex, "_default_store", _mini(tmp_path))
    assert ex.render_for_names(["shadow:nearby#1@lex:0.9"]) == ""
    assert ex.render_for_names(["full:nearby#1@lex:0.9!clipped"]) == ""
    assert ex.render_for_names([]) == ""
    block = ex.render_for_names(["full:nearby#1@lex:0.9"])
    assert "nearby.search" in block


def test_pending_plan_round_trip_keeps_exemplars():
    """挂起序列化/恢复必须带上 exemplars——skills 为这条漏链补过三次。"""
    from orchestrator.cloud.engine import PlannerEngine
    from orchestrator.cloud.models import Plan

    plan = Plan(steps=[])
    plan.skills = ["full:charging-strategy@lex:20"]
    plan.exemplars = ["full:nearby#1@vec:0.71"]
    snap = PlannerEngine._serialize_plan(plan)
    assert snap["exemplars"] == ["full:nearby#1@vec:0.71"]
    assert snap["skills"] == ["full:charging-strategy@lex:20"]


# ── ④ 与 skills 的边界 ─────────────────────────────────────────────────────

def test_skillstore_ignores_exemplar_dir(tmp_path):
    """范例文件同处 skills/ 但没有 name/type/knowledge——被 SkillStore 扫到会刷一屏
    「缺必填字段」告警。目录级排除是契约（skills.py::_NON_SKILL_DIRS）。"""
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides" / "g.yaml").write_text(
        "name: g\ntype: guide\ndescription: d\nknowledge: k\n", encoding="utf-8")
    _write(tmp_path / "exemplars", "nearby",
           "domain: nearby\nexemplars:\n  - text: t\n    plan:\n"
           "      - intent: nearby.search\n")
    assert [d.name for d in sk.SkillStore(root=str(tmp_path)).load()] == ["g"]


def test_real_corpus_loads_and_all_intents_exist():
    """仓库真实语料的冒烟：能加载、eid 唯一、每条至少一个 intent。
    intent 存在性由 test/eval_exemplars.py 的契约车道硬校验（那里有端侧意图集）。"""
    items = ex.ExemplarStore().load()
    assert items, "skills/exemplars/ 空了？"
    assert len({e.eid for e in items}) == len(items)
    assert all(e.intents() for e in items)


@pytest.mark.parametrize("bad,expected", [("0", 0.01), ("2.5", 1.0), ("oops", 0.34)])
def test_lex_threshold_is_clamped(monkeypatch, bad, expected):
    """越界阈值不崩但会**静默**改变行为：钳 0 = 全量放行（skills min_score 同款教训）。"""
    monkeypatch.setenv("EXEMPLAR_LEX_THRESHOLD", bad)
    assert ex._lex_threshold() == pytest.approx(expected)
