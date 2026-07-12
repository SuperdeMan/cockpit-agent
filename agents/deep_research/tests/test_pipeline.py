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


def test_investigate_backtrack_merges_on_thin_results():
    """首轮只有 1 条（薄，<_THIN_EVIDENCE）→ 换宽 query 追一轮且**合并**（首轮那条保留）。"""
    calls = []

    class _ThinThenMore:
        async def search(self, query, limit=5, meta=None, **kwargs):
            calls.append(query)
            if len(calls) == 1:
                return [SearchResult(title="仅一条", url="http://only/1", source="s",
                                     content="首轮唯一正文。")]
            return [SearchResult(title="补充", url="http://more/2", source="s",
                                 content="回溯轮补充正文。")]

    subqs = [SubQuestion(sq_id="sq1", text="冷门主题")]
    asyncio.run(pipeline.investigate(_ThinThenMore(), None, subqs, meta={}))
    assert calls == ["冷门主题", "冷门主题 详细介绍"]
    urls = [e.url for e in subqs[0].evidence]
    assert urls == ["http://only/1", "http://more/2"]   # 合并不替换
    assert subqs[0].status == "answered"


def test_investigate_skips_preseeded_subquestion():
    """预置证据的子问题（深挖种子 sq0）不再检索——幂等化。"""
    class _MustNotSearch:
        async def search(self, *a, **k):
            raise AssertionError("预置证据的子问题不应触发检索")

    seeded = SubQuestion(sq_id="sq0", text="上轮结论回顾：某节", status="pending",
                         evidence=[Evidence(title="旧引用", url="http://prior/1",
                                            excerpt="上轮正文")])
    asyncio.run(pipeline.investigate(_MustNotSearch(), None, [seeded], meta={}))
    assert seeded.status == "answered"
    assert [e.url for e in seeded.evidence] == ["http://prior/1"]


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


def test_synthesize_strips_markdown_from_summary_and_body():
    """prompt「纯文本无 markdown」是软约束——换 provider 后出口硬剥兜底；[N] 引用标记保留。"""
    subqs = _subqs_with_evidence()
    llm = FakeLLM(lambda m, **k: (
        '{"summary":"**核心结论**：`固态`更优。","sections":['
        '{"heading":"原理","body":"# 小标题\\n用**固态电解质**[1]。","citations":[1],'
        '"confidence":"high"}],"overall_confidence":"medium","gaps":[]}'))
    report = asyncio.run(pipeline.synthesize(llm, "固态电池", subqs))
    assert report.summary == "核心结论：固态更优。"
    body = report.sections[0].body
    assert "**" not in body and "#" not in body
    assert "[1]" in body                                # 引用编号不受影响


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


def test_synthesize_rescues_truncated_json_sections():
    """badcase 0f4105c4：合成 JSON 打满 max_tokens 被截断 → 抢救 summary+已完整小节，
    绝不整份退化 fallback 堆原文。"""
    subqs = _subqs_with_evidence()
    truncated = (
        '{"summary":"英阿1982年围绕马岛主权爆发战争，英国获胜但争端未解。",'
        '"sections":['
        '{"heading":"**战略价值**","body":"马岛扼守南大西洋航道[1]。","citations":[1],"confidence":"high"},'
        '{"heading":"主权争议","body":"两国自19世纪起各自主张主权[2]。","citations":[2],"confidence":"medium"},'
        '{"heading":"被截断的节","body":"这一节写到一半就没了')
    llm = FakeLLM(lambda m, **k: truncated)
    report = asyncio.run(pipeline.synthesize(llm, "马岛战争历史背景", subqs))
    assert len(report.sections) == 2                     # 完整的两节被抢救，半截节丢弃
    assert report.sections[0].heading == "战略价值"       # 抢救路径同样剥 markdown
    assert report.sections[0].body == "马岛扼守南大西洋航道[1]。"
    assert report.summary.startswith("英阿1982年")        # summary 完整字段被抢救
    assert report.overall_confidence == "low"
    assert any("截断" in g for g in report.gaps)          # 诚实标注
    # speech（brief）念的是抢救出的 summary，而非原文堆砌
    speech, _ = pipeline.brief(report, "马岛战争历史背景")
    assert speech.startswith("英阿1982年")


def test_synthesize_rescues_report_with_naked_quotes():
    """badcase 6ce027fe 同族：某节 body 含裸英文引号 → 整份 JSON 非法 → 逐块+边界式
    提取应恢复**全部**小节（含裸引号那节，引号保留为文本）。"""
    subqs = _subqs_with_evidence()
    bad_quotes = (
        '{"summary":"英阿战争概述。",'
        '"sections":['
        '{"heading":"战略价值","body":"马岛扼守航道[1]。","citations":[1],"confidence":"high"},'
        '{"heading":"经典对决","body":"包括1986年马拉多纳的"上帝之手"事件[2]。","citations":[2],"confidence":"medium"}],'
        '"overall_confidence":"medium","gaps":[]}')
    llm = FakeLLM(lambda m, **k: bad_quotes)
    report = asyncio.run(pipeline.synthesize(llm, "马岛战争", subqs))
    assert len(report.sections) == 2                      # 两节全部恢复
    assert report.sections[0].body == "马岛扼守航道[1]。"   # 合法节走 json.loads 原样
    assert "上帝之手" in report.sections[1].body           # 裸引号节边界式恢复
    assert report.sections[1].body.endswith("事件[2]。")   # 不在裸引号处截断
    assert report.summary == "英阿战争概述。"


def test_fallback_report_bodies_are_short_and_clean():
    """fallback 报告可读性：节选剥 markdown + 截 200 字 + 诚实标注（不再上千字原文糊脸）。"""
    long_raw = ("# 福克兰战争 - 维基百科\n**背景**\n" + "英国和阿根廷为争夺福克兰群岛主权而爆发战争，" * 30)
    sq = SubQuestion(sq_id="sq1", text="历史背景", status="answered",
                     evidence=[Evidence(idx=1, title="wiki", url="http://w/1",
                                        excerpt=long_raw)])
    report = pipeline._fallback_report("马岛战争", [sq], [{"idx": 1, "url": "http://w/1"}])
    body = report.sections[0].body
    assert len(body) <= 210
    assert "#" not in body and "**" not in body
    assert body.endswith("……")                            # 截断处有省略标记
    assert any("合成暂不可用" in g for g in report.gaps)
    assert len(report.summary) < 300                       # speech 不再是千字原文


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
    assert "8-9" in cap["sys"]                        # 与 MAX_SUBQ_DEEP=9 对齐
    asyncio.run(pipeline.plan(FakeLLM(responder), "X"))
    assert "5-6" in cap["sys"]                        # 与 MAX_SUBQ=6 对齐，不再让 cap 白丢产出


def test_synthesize_deep_uses_larger_budget_and_prompt():
    subqs = _subqs_with_evidence()
    good = ('{"summary":"s","sections":[{"heading":"h","body":"b[1]","citations":[1],'
            '"confidence":"high"}],"overall_confidence":"medium","gaps":[]}')
    dllm = FakeLLM(lambda m, **k: good)
    asyncio.run(pipeline.synthesize(dllm, "q", subqs, deep=True))
    msgs, kw = dllm.calls[0]
    # 深度：合成预算放大（8-9节×300-500字≈4500字 落在 6000 tok 内有余量）、超时放宽，
    # 仍恒不开思考（大材料开思考会 DEADLINE 退化）
    assert kw["max_tokens"] == 6000 and kw["timeout"] == 150 and kw["thinking"] is False
    assert "8-9" in msgs[1]["content"]
    sllm = FakeLLM(lambda m, **k: good)
    asyncio.run(pipeline.synthesize(sllm, "q", subqs))
    _, kw2 = sllm.calls[0]
    # 同步：token 预算不变，但要求对齐预算（5-6节×180-300字≈1800字 < 2400 tok，
    # 不再结构性截断——badcase 0f4105c4）
    assert kw2["max_tokens"] == 2400 and kw2["timeout"] == 55
    assert "5-6" in sllm.calls[0][0][1]["content"]


# ── 源质量加权 + 学术兜底 ────────────────────────────────────

def test_synthesize_reranks_evidence_by_authority():
    sq = SubQuestion(sq_id="sq1", text="原理", perspective="背景", status="answered", evidence=[
        Evidence(title="农场", url="https://blog.csdn.net/x", source="csdn.net", excerpt="农场正文"),
        Evidence(title="论文", url="https://arxiv.org/abs/1", source="arxiv.org", excerpt="学术正文"),
    ])
    cap = {}

    def responder(messages, **k):
        cap["user"] = messages[1]["content"]
        return ('{"summary":"s","sections":[{"heading":"h","body":"b[1]","citations":[1],'
                '"confidence":"high"}],"overall_confidence":"medium","gaps":[]}')

    report = asyncio.run(pipeline.synthesize(FakeLLM(responder), "固态电池", [sq]))
    by_idx = {s["idx"]: s for s in report.sources}
    assert by_idx[1]["source"] == "arxiv.org"      # tier3 学术重排到前 → idx 1
    assert by_idx[2]["source"] == "csdn.net"       # tier0 内容农场沉到 idx 2
    assert "[1] 论文" in cap["user"] and "学术正文" in cap["user"]


class _ThinThenPapers:
    """普通搜索每问回 1 条(薄)；research paper 类目回 1 条学术源。"""
    def __init__(self):
        self.categories = []

    async def search(self, query, limit=5, meta=None, **kwargs):
        self.categories.append(kwargs.get("category", ""))
        if kwargs.get("category") == "research paper":
            return [SearchResult(title="paper", url="https://arxiv.org/p", source="arxiv.org",
                                 published="", content="学术正文")]
        return [SearchResult(title="gen", url="https://x.com/1", source="x.com",
                             published="", content="一般正文")]


def test_investigate_deep_academic_backfill_for_thin():
    s = _ThinThenPapers()
    subqs = [SubQuestion(sq_id="sq1", text="原理")]
    asyncio.run(pipeline.investigate(s, None, subqs, meta={}, deep=True))
    assert "research paper" in s.categories               # 深度模式薄结果触发学术兜底
    assert len(subqs[0].evidence) == 2                    # 一般1 + 学术1（去重合并）
    assert any("arxiv" in e.url for e in subqs[0].evidence)


def test_investigate_sync_no_academic_backfill():
    s = _ThinThenPapers()
    subqs = [SubQuestion(sq_id="sq1", text="原理")]
    asyncio.run(pipeline.investigate(s, None, subqs, meta={}))   # deep=False
    assert "research paper" not in s.categories           # 同步不做学术兜底（省延迟）
    assert len(subqs[0].evidence) == 1


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
