"""deep-research 契约测试：直接驱动 handle（注入 fake llm/search，不联网）。"""
import asyncio
import json

from agents._sdk.testing import run_handle, make_context, assert_manifest_consistent
from agents.info.src.providers.base import SearchResult
from agents.deep_research.src.agent import DeepResearchAgent


def _agent_llm(messages, **kwargs):
    """按 system 提示区分 plan / synthesize 两次调用，返回各自的 JSON。"""
    sys = messages[0]["content"]
    if "规划" in sys:          # plan 阶段
        return ('{"subquestions":[{"text":"现状","perspective":"背景"},'
                '{"text":"对比","perspective":"对比"}]}')
    return ('{"summary":"这是核心结论。","sections":['
            '{"heading":"现状","body":"要点一[1]。","citations":[1],"confidence":"high"}],'
            '"overall_confidence":"medium","gaps":[]}')


class _FakeSearch:
    async def search(self, query, limit=5, meta=None, **kwargs):
        return [SearchResult(title=f"{query}-src", url=f"http://x/{query}",
                             source="src", published="2026-06-25T08:00:00",
                             content=f"关于「{query}」的正文。")]


def _async(fn):
    async def _inner(messages, **kwargs):
        return fn(messages, **kwargs)
    return _inner


def _wire(agent):
    agent.llm.complete = _async(_agent_llm)
    agent.search = _FakeSearch()
    agent.extractor = None
    return agent


def test_manifest_consistent():
    assert assert_manifest_consistent(DeepResearchAgent()) is True


def test_research_need_slot_when_empty():
    res = asyncio.run(run_handle(DeepResearchAgent(), "research.run",
                                 slots={}, raw_text=""))
    assert res.status == "need_slot"
    assert "query" in res.missing_slots


def test_research_end_to_end_produces_report_card():
    agent = _wire(DeepResearchAgent())
    res = asyncio.run(run_handle(agent, "research.run",
                                 slots={"query": "固态电池"},
                                 raw_text="深入调研固态电池"))
    assert res.status == "ok"
    assert res.speech and "核心结论" in res.speech
    assert res.ui_card and res.ui_card["type"] == "research_report"
    assert res.ui_card["question"] == "固态电池"
    assert len(res.ui_card["sections"]) == 1
    assert res.ui_card["sources"] and res.ui_card["sources"][0]["idx"] == 1
    assert res.data["confidence"] == "medium"


def test_research_unknown_intent_fails_gracefully():
    res = asyncio.run(run_handle(DeepResearchAgent(), "research.unknown",
                                 slots={}, raw_text="x"))
    assert res.status == "failed"


# ── P1 多轮研究上下文 ───────────────────────────────────────

def test_resolve_deepen_maps_ordinal_to_section():
    prior = {"sections": [{"heading": "技术原理"}, {"heading": "量产风险"},
                          {"heading": "市场前景"}]}
    f = DeepResearchAgent._resolve_deepen
    assert f("再深入第2点", prior) == "量产风险"
    assert f("展开第三节", prior) == "市场前景"
    assert f("这部分再详细讲讲", prior) == "市场前景"   # 无序号 → 最近一节
    assert f("第2点", prior) == ""                      # 无深挖词不触发
    assert f("展开第9点", prior) == ""                  # 越界
    assert f("展开", None) == ""                        # 无 prior


def test_resolve_news_deepen_maps_ordinal_to_news_title():
    news = {"items": [{"title": "英伟达发布新GPU"}, {"title": "央行降准"},
                      {"title": "世界杯开赛"}]}
    ctx = make_context(context_values={"profile.news_active": json.dumps(news)})
    agent = DeepResearchAgent()
    assert asyncio.run(agent._resolve_news_deepen(ctx, "详细讲讲第2条")) == "央行降准"
    assert asyncio.run(agent._resolve_news_deepen(ctx, "这条新闻讲讲")) == "英伟达发布新GPU"
    assert asyncio.run(agent._resolve_news_deepen(ctx, "今天天气")) == ""   # 无深挖/新闻词


def test_research_deepen_focuses_prior_section():
    prior = {"question": "固态电池", "summary": "...",
             "sections": [{"heading": "技术原理"}, {"heading": "量产风险"}]}
    ctx = make_context(context_values={"profile.research_active": json.dumps(prior)})
    agent = _wire(DeepResearchAgent())
    res = asyncio.run(run_handle(agent, "research.run", raw_text="再深入第2点", ctx=ctx))
    assert res.status == "ok"
    assert "量产风险" in res.data["question"]           # 聚焦到上次第2节，不重跑整份调研


def test_research_remember_saves_and_skips_pipeline():
    prior = {"question": "固态电池", "summary": "结论摘要", "sections": [{"heading": "h"}]}
    ctx = make_context(context_values={"profile.research_active": json.dumps(prior)})
    agent = _wire(DeepResearchAgent())
    res = asyncio.run(run_handle(agent, "research.run",
                                 raw_text="帮我记一下这个调研", ctx=ctx))
    assert res.status == "ok"
    assert "记下" in res.speech
    assert res.ui_card is None                          # 存记忆不出报告卡
