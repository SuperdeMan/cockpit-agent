"""deep-research 契约测试：直接驱动 handle（注入 fake llm/search，不联网）。"""
import asyncio
import json
from unittest.mock import AsyncMock

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


# ── 异步分钟级深调研（解同步 90s 上限封顶的报告深度）────────────────

def test_is_async_request_detection():
    f = DeepResearchAgent._is_async_request
    assert f("深入调研固态电池，不急，慢慢查") is True
    assert f("深度调研下AI芯片，查完告诉我") is True
    assert f("给我出一份详细的固态电池调研报告") is True   # 「出一份详细」
    assert f("深入调研固态电池") is False                  # 无延后信号 → 同步即时答
    assert f("彻底研究一下固态电池") is False              # 「彻底」≠延后，仍同步
    assert f("") is False


def test_strip_async_noise_cleans_trailing_deferral():
    f = DeepResearchAgent._strip_async_noise
    assert f("固态电池的量产前景，不急慢慢查，查完语音告诉我") == "固态电池的量产前景"
    assert f("深度调研AI芯片，查完告诉我") == "深度调研AI芯片"
    assert f("固态电池技术路线，慢慢研究，先忙别的") == "固态电池技术路线"
    assert f("给我一份固态电池的详细报告") == "给我一份固态电池的详细报告"   # 报告类与请求一体，不剔
    assert f("不急慢慢查") == "不急慢慢查"                                  # 清后过短 → 回退原句


def test_research_async_returns_ack_and_defers():
    """异步请求：立即返回受理（无报告卡、标 async），并 spawn 后台 task。"""
    async def scenario():
        agent = _wire(DeepResearchAgent())
        agent.memory = AsyncMock()      # 后台落 memory 不打真实 gRPC
        res = await run_handle(agent, "research.run", slots={"query": "固态电池"},
                               raw_text="深入调研固态电池，不急慢慢查，查完告诉我")
        assert res.status == "ok"
        assert res.data.get("async") is True
        assert res.ui_card is None                       # 受理 ack 不带报告
        assert "几分钟" in res.speech and "报告" in res.speech
        assert len(agent._bg_tasks) >= 1                 # 已 spawn 后台调研
        # 排空后台任务，避免事件循环关闭时 "Task was destroyed" 噪声
        await asyncio.gather(*list(agent._bg_tasks), return_exceptions=True)
    asyncio.run(scenario())


def test_run_deep_async_publishes_report_with_card():
    """后台跑完深度流水线 → 经 NATS 发 agent.proactive（带可读分节报告卡）。"""
    class FakeNC:
        def __init__(self):
            self.published = []

        async def publish(self, subject, data):
            self.published.append((subject, json.loads(data.decode())))

    async def scenario():
        agent = _wire(DeepResearchAgent())
        agent.memory = AsyncMock()
        agent._nc = FakeNC()
        await agent._run_deep_async("固态电池", {}, "s", "u", "v", {})
        assert len(agent._nc.published) == 1
        subject, payload = agent._nc.published[0]
        assert subject == "agent.proactive"
        assert payload["type"] == "research_done"
        assert "固态电池" in payload["speech"]
        card = payload["card"]
        assert card and card["type"] == "research_report"
        assert card["question"] == "固态电池"
    asyncio.run(scenario())


def test_run_deep_async_publishes_failure_on_error():
    """后台流水线**意外**异常 → 发一条简短失败告知（不崩进程）。

    注：plan/investigate/synthesize 各自内部都优雅降级（坏 LLM 仍出低置信兜底报告并发 done），
    故这里直接让 synthesize 抛出未捕获异常，验证 _run_deep_async 的最后一道 except 兜底。
    """
    from agents.deep_research.src import agent as agent_mod

    class FakeNC:
        def __init__(self):
            self.published = []

        async def publish(self, subject, data):
            self.published.append((subject, json.loads(data.decode())))

    async def scenario():
        agent = _wire(DeepResearchAgent())
        agent.memory = AsyncMock()
        agent._nc = FakeNC()

        async def boom(*a, **k):
            raise RuntimeError("synth exploded")
        orig = agent_mod.synthesize
        agent_mod.synthesize = boom             # 让合成抛出未捕获异常
        try:
            await agent._run_deep_async("固态电池", {}, "s", "u", "v", {})
        finally:
            agent_mod.synthesize = orig
        assert agent._nc.published and agent._nc.published[0][1]["type"] == "research_failed"
    asyncio.run(scenario())
