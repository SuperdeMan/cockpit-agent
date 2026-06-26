"""deep-research 四段流水线单测（不起 gRPC、不联网、不打真实 LLM）。"""
import asyncio

from agents.info.src.providers.base import SearchResult
from agents.deep_research.src.models import SubQuestion, Evidence, Report
from agents.deep_research.src import pipeline


class FakeLLM:
    """按 responder(messages, **kwargs) -> str 产出，模拟 llm.complete。"""
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.responder(messages, **kwargs)


class FakeSearch:
    def __init__(self, per_query=2):
        self.per_query = per_query
        self.queries = []

    async def search(self, query, limit=5, meta=None, **kwargs):
        self.queries.append(query)
        return [SearchResult(title=f"{query}-{i}", url=f"http://x/{query}/{i}",
                             source=f"src{i}", published="2026-06-25T08:00:00",
                             content=f"关于「{query}」的正文资料第{i}段。")
                for i in range(self.per_query)]


class EmptySearch:
    async def search(self, query, limit=5, meta=None, **kwargs):
        return []


# ── plan ──────────────────────────────────────────────────

def test_plan_parses_perspective_subquestions():
    llm = FakeLLM(lambda m, **k: (
        '{"subquestions":[{"text":"固态电池原理","perspective":"背景"},'
        '{"text":"固态与液态对比","perspective":"对比"},'
        '{"text":"量产风险","perspective":"风险"}]}'))
    subqs = asyncio.run(pipeline.plan(llm, "固态电池"))
    assert len(subqs) == 3
    assert subqs[0].text == "固态电池原理" and subqs[0].perspective == "背景"
    assert subqs[1].perspective == "对比"
    # plan 阶段恒不开思考（结构化 JSON）
    assert llm.calls[0][1].get("thinking") is False


def test_plan_fallback_on_bad_json_keeps_question():
    llm = FakeLLM(lambda m, **k: "这不是 JSON，模型抽风了")
    subqs = asyncio.run(pipeline.plan(llm, "量子计算前景"))
    assert len(subqs) == 1
    assert subqs[0].text == "量子计算前景"


def test_plan_caps_at_max_subq():
    items = ",".join('{"text":"q%d","perspective":"背景"}' % i for i in range(9))
    llm = FakeLLM(lambda m, **k: '{"subquestions":[' + items + ']}')
    subqs = asyncio.run(pipeline.plan(llm, "X"))
    assert len(subqs) == pipeline.MAX_SUBQ


def test_clean_excerpt_strips_webpage_chrome():
    raw = "登录\n搜索\n媒体品牌\n企业服务\n我要入驻\n# 一文看懂BEVFormer技术\n这是正文内容。"
    cleaned = pipeline._clean_excerpt(raw)
    for chrome in ("登录", "媒体品牌", "企业服务", "我要入驻"):
        assert chrome not in cleaned
    assert "一文看懂BEVFormer技术" in cleaned and "这是正文内容。" in cleaned


def test_constraints_note_excludes_battery():
    # 电量与研究主题无关、会带偏（loop engineering→电量72%自适应控制）→ 绝不进研究约束。
    note = pipeline._constraints_note({"vehicle_state": "电量72%", "location": "杭州",
                                       "profile_prefs": ["带老人"]})
    assert "电量" not in note and "72" not in note
    assert "杭州" in note and "带老人" in note


def test_plan_injects_constraints():
    seen = {}

    def responder(messages, **k):
        seen["user"] = messages[1]["content"]
        return '{"subquestions":[{"text":"a","perspective":"背景"},{"text":"b","perspective":"对比"}]}'

    llm = FakeLLM(responder)
    asyncio.run(pipeline.plan(llm, "适合定居吗",
                              {"location": "杭州", "profile_prefs": ["带老人"]}))
    assert "杭州" in seen["user"] and "带老人" in seen["user"]


# ── investigate ───────────────────────────────────────────

def test_investigate_fills_evidence_parallel():
    subqs = [SubQuestion(sq_id="sq1", text="原理"),
             SubQuestion(sq_id="sq2", text="对比")]
    search = FakeSearch(per_query=2)
    asyncio.run(pipeline.investigate(search, None, subqs, meta={}))
    assert all(sq.status == "answered" for sq in subqs)
    assert all(len(sq.evidence) == 2 for sq in subqs)
    assert isinstance(subqs[0].evidence[0], Evidence)


def test_investigate_marks_gap_when_no_results():
    subqs = [SubQuestion(sq_id="sq1", text="查不到的东西")]
    asyncio.run(pipeline.investigate(EmptySearch(), None, subqs, meta={}))
    assert subqs[0].status == "gap" and subqs[0].evidence == []


# ── synthesize ────────────────────────────────────────────

def _subqs_with_evidence():
    sq1 = SubQuestion(sq_id="sq1", text="原理", perspective="背景", status="answered",
                      evidence=[Evidence(title="A", url="http://a", source="srcA",
                                         published="2026-06-25T00:00:00", excerpt="原理正文")])
    sq2 = SubQuestion(sq_id="sq2", text="风险", perspective="风险", status="answered",
                      evidence=[Evidence(title="B", url="http://b", source="srcB",
                                         excerpt="风险正文")])
    return [sq1, sq2]


def test_synthesize_builds_sectioned_report_with_global_sources():
    subqs = _subqs_with_evidence()
    llm = FakeLLM(lambda m, **k: (
        '{"summary":"一句话结论。","sections":['
        '{"heading":"原理","body":"用固态电解质[1]。","citations":[1],"confidence":"high"},'
        '{"heading":"风险","body":"成本高[2]。","citations":[2],"confidence":"medium"}],'
        '"overall_confidence":"medium","gaps":["上市时间"]}'))
    report = asyncio.run(pipeline.synthesize(llm, "固态电池", subqs))
    assert isinstance(report, Report)
    assert report.summary == "一句话结论。"
    assert len(report.sections) == 2
    assert len(report.sources) == 2                 # 全局去重编号
    assert report.sections[0].citations == [1]
    assert report.gaps == ["上市时间"]
    assert report.freshness == "2026-06-25T00:00:00"


def test_synthesize_drops_invalid_citations():
    subqs = _subqs_with_evidence()
    llm = FakeLLM(lambda m, **k: (
        '{"summary":"x","sections":[{"heading":"h","body":"b","citations":[1,99],'
        '"confidence":"low"}],"overall_confidence":"low","gaps":[]}'))
    report = asyncio.run(pipeline.synthesize(llm, "q", subqs))
    assert report.sections[0].citations == [1]      # 99 不在来源集，被剔除


def test_synthesize_empty_report_when_no_evidence():
    subqs = [SubQuestion(sq_id="sq1", text="查不到", status="gap")]
    llm = FakeLLM(lambda m, **k: (_ for _ in ()).throw(AssertionError("不应调用 LLM")))
    report = asyncio.run(pipeline.synthesize(llm, "查不到的主题", subqs))
    assert report.sections == [] and report.sources == []
    assert report.overall_confidence == "low" and report.gaps


def test_synthesize_fallback_on_bad_llm_output():
    subqs = _subqs_with_evidence()
    llm = FakeLLM(lambda m, **k: "模型乱答没有 JSON")
    report = asyncio.run(pipeline.synthesize(llm, "q", subqs))
    # 兜底：每个有证据的子问题一节，低置信，不编造
    assert report.overall_confidence == "low"
    assert len(report.sections) == 2
    assert report.sources and report.sources[0]["idx"] == 1


# ── deep（异步分钟级深调研）模式：放开覆盖面+合成预算，不受 90s 上限 ────────

def test_plan_deep_allows_more_subquestions():
    items = ",".join('{"text":"q%d","perspective":"背景"}' % i for i in range(12))
    body = '{"subquestions":[' + items + ']}'
    subqs = asyncio.run(pipeline.plan(FakeLLM(lambda m, **k: body), "X"))
    assert len(subqs) == pipeline.MAX_SUBQ            # 同步封顶 6（压 90s 上限）
    deep = asyncio.run(pipeline.plan(FakeLLM(lambda m, **k: body), "X", deep=True))
    assert len(deep) == pipeline.MAX_SUBQ_DEEP        # 深度放开到 9


def test_plan_deep_prompt_asks_more_angles():
    cap = {}

    def responder(messages, **k):
        cap["sys"] = messages[0]["content"]
        return '{"subquestions":[{"text":"a","perspective":"背景"},{"text":"b","perspective":"对比"}]}'

    asyncio.run(pipeline.plan(FakeLLM(responder), "X", deep=True))
    assert "8-11" in cap["sys"]
    asyncio.run(pipeline.plan(FakeLLM(responder), "X"))
    assert "5-7" in cap["sys"]


def test_synthesize_deep_uses_larger_budget_and_prompt():
    subqs = _subqs_with_evidence()
    good = ('{"summary":"s","sections":[{"heading":"h","body":"b[1]","citations":[1],'
            '"confidence":"high"}],"overall_confidence":"medium","gaps":[]}')
    dllm = FakeLLM(lambda m, **k: good)
    asyncio.run(pipeline.synthesize(dllm, "q", subqs, deep=True))
    msgs, kw = dllm.calls[0]
    # 深度：合成预算翻倍、超时放宽，仍恒不开思考（大材料开思考会 DEADLINE 退化）
    assert kw["max_tokens"] == 4000 and kw["timeout"] == 150 and kw["thinking"] is False
    assert "8-12" in msgs[1]["content"]
    sllm = FakeLLM(lambda m, **k: good)
    asyncio.run(pipeline.synthesize(sllm, "q", subqs))
    _, kw2 = sllm.calls[0]
    assert kw2["max_tokens"] == 2400 and kw2["timeout"] == 55    # 同步预算不变
    assert "5-7" in sllm.calls[0][0][1]["content"]


# ── brief ─────────────────────────────────────────────────

def test_brief_produces_speech_and_card():
    report = Report(summary="核心结论一句话。",
                    sections=[__import__("agents.deep_research.src.models",
                                         fromlist=["Section"]).Section(heading="h", body="b")],
                    sources=[{"idx": 1, "title": "A", "url": "http://a", "source": "s"}],
                    overall_confidence="medium", gaps=["未覆盖点"])
    speech, card = pipeline.brief(report, "固态电池")
    assert "核心结论一句话。" in speech
    assert "停车后" in speech            # 有报告时引导泊车读
    assert "1 个方面" in speech          # gaps 提示
    assert card["type"] == "research_report"
    assert card["question"] == "固态电池"
