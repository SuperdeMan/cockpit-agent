"""生活指数 vs 股指的回归钉子（2026-08-10 修）。

缺陷：股票规则用的是裸 `"指数" in t`，于是「查深圳的穿衣指数」被判成股指。
它此前只被**记录**在 `nlu_objects.yaml` 里当反例（「不许把真 badcase 洗成 agree」），
缺陷本身一直挂在 §4.2「端侧能力面」那张卡上没修。

真实分布是这个修法的全部依据——不是语感：
`test/eval_corpus/feishu_intents_full.jsonl` 里 `object=指数` 共 179 条，**全部
domain=weather**（洗车/紫外线/穿衣/化妆/感冒/旅游/路况/钓鱼/运动/戴口罩/扩散条件…），
真股指只有 4 条标普500 落在 `object=股票` 里。**裸「指数」判给股票，是把 97.8% 的
多数派判错去接住少数派。**

修法两半，缺一半都不成立：
- 生活指数分支（早于股票分支），词表逐条从语料提取，且**必须与「指数」共现**；
- 股票词收窄成两档：自身无歧义的（股票/大盘/上证/标普…）与须共现的（科创/日经…）。

本文件的断言分四组：整族落点、劫持面、股票不回归、歧义面。
第二组是重点——`洗车`/`运动`/`旅游`/`路况` 都是高频日常词，我第一版就是裸词匹配，
实测会把「附近哪里可以洗车」抢成生活指数。同 badcase c9bcf8c2 的体感入口收窄：
**新增一条宽匹配面时，让路面必须和召回面一起钉。**
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import classify, classify_structured

_CORPUS = Path(__file__).resolve().parents[3] / "test" / "eval_corpus" / "feishu_intents_full.jsonl"


def _name(text: str) -> str | None:
    result = classify(text)
    return result["name"] if result else None


def _object(text: str) -> str | None:
    structured = classify_structured(text)
    return structured["data"]["object"] if structured else None


def _corpus_rows(label: str) -> list[dict]:
    rows = [json.loads(line) for line in _CORPUS.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    return [r for r in rows if r.get("object") == label]


# ═══════════════════════════════════════════════════════════════════════════
# 1. 整族落点（量的断言——单句钉不住"一个词被整体判错"这种缺陷）
# ═══════════════════════════════════════════════════════════════════════════

def test_whole_life_index_family_lands_on_indices():
    rows = _corpus_rows("指数")
    assert len(rows) >= 179
    landed = [_object(r["text"]) for r in rows]
    assert all(obj == "life_index" for obj in landed), \
        f"未全部落 life_index：{sorted(set(landed))}"


def test_whole_stock_family_still_lands_on_stock():
    """反向：修生活指数不许把股票域碰掉。

    改动前后逐条对照过——股票域 26 条落点从 24/2 变成 26/26（顺手补了
    「深圳成指」「标普500的信息」两条既有漏接），**没有一条从 stock 掉出去**。
    """
    rows = _corpus_rows("股票")
    landed = [_object(r["text"]) for r in rows]
    assert all(obj == "stock" for obj in landed), \
        f"股票域掉出：{[(r['text'], o) for r, o in zip(rows, landed) if o != 'stock']}"


# ═══════════════════════════════════════════════════════════════════════════
# 2. 劫持面（第一版真的踩了——裸词匹配）
# ═══════════════════════════════════════════════════════════════════════════

class TestDoesNotHijackEverydayWords:
    """`洗车`/`运动`/`旅游`/`路况`/`钓鱼` 都是高频日常词，没有「指数」就不是生活指数。"""

    @pytest.mark.parametrize("text", [
        "附近哪里可以洗车", "导航去洗车店", "放点运动音乐", "我想去旅游",
        "今天路况怎么样", "帮我规划一下旅行", "附近有钓鱼的地方吗", "来点运动",
    ])
    def test_no_index_word_means_not_an_index_query(self, text):
        assert _object(text) != "life_index"

    def test_navigation_and_media_still_own_their_domains(self):
        """让路不是让成 None 就算完——得让回各自正确的域。"""
        assert _name("导航去洗车店") == "navi.plan"
        assert _name("放点运动音乐") == "music.play"


# ═══════════════════════════════════════════════════════════════════════════
# 3. 生活指数召回（含语料里的方言/异形词）
# ═══════════════════════════════════════════════════════════════════════════

class TestLifeIndexRecall:

    @pytest.mark.parametrize("text,tag", [
        ("查深圳的穿衣指数", "穿衣"),
        ("紫外线指数怎么样", "紫外线"),
        ("成都的洗车指数如何", "洗车"),
        ("查台北市化妆指数", "化妆"),
        ("查一下深圳今天感冒指数", "感冒"),
        ("查戴口罩指数台北市的", "戴口罩"),
        # 语料实证的方言/异形词，不是我编的：着衫＝粤语穿衣、扮靓＝化妆、带遮＝遮阳
        ("查一下澳门的着衫指数", "着衫"),
        ("麻烦你将澳门特别行政区1日五更天的扮靓指数查询出来", "扮靓"),
        # 与 air_quality（AQI/PM2.5）是两件事
        ("请你将台北的空气污染扩散条件指数查询一下", "扩散条件"),
    ])
    def test_recalled_with_kind_tag(self, text, tag):
        result = classify(text)
        assert result and result["name"] == "info.indices"
        assert result["slots"].get("tag") == tag

    def test_intent_name_matches_the_cloud_capability(self):
        """`info.indices` 必须与 `agents/info/manifest.yaml` 的 capability 同名，
        否则端侧给的意图提示在云侧对不上任何工具。"""
        import yaml
        manifest = Path(__file__).resolve().parents[3] / "agents" / "info" / "manifest.yaml"
        declared = {c["intent"] for c in
                    yaml.safe_load(manifest.read_text(encoding="utf-8"))["capabilities"]}
        assert "info.indices" in declared

    def test_not_local_so_it_still_goes_to_cloud(self):
        """生活指数要联网，端侧只给提示不自己答（语料 `edge_expected` 全是 None）。"""
        from fast_intent import is_local
        assert not is_local("info.indices")


# ═══════════════════════════════════════════════════════════════════════════
# 4. 股票侧：两档词的歧义面
# ═══════════════════════════════════════════════════════════════════════════

class TestStockTermTiers:

    @pytest.mark.parametrize("text", [
        "查一下上证指数", "今天上证指数怎么样", "查标普500指数", "大盘怎么样",
        "茅台股价", "纳斯达克指数", "深圳成指", "请将标普500的信息查询清楚",
    ])
    def test_unambiguous_terms_are_enough_on_their_own(self, text):
        assert _object(text) == "stock"

    @pytest.mark.parametrize("text,expect_stock", [
        ("查日经指数", True),      # 与「指数」共现 → 股指
        ("日经新闻", False),       # 单独出现 → 不是证券
        ("创业板指数怎么样", True),
        ("沪深300指数", True),
        ("科创中心在哪", False),
    ])
    def test_ambiguous_terms_require_the_index_word(self, text, expect_stock):
        assert (_object(text) == "stock") is expect_stock

    def test_unknown_index_is_not_guessed(self):
        """不认识的「X指数」不再猜成股票——落错域给 planner 一个错提示，
        漏接只是慢一点。同「宁可漏接上云」那条纪律。"""
        assert _object("查一下幸福指数") is None
        assert _object("基尼指数是多少") is None
