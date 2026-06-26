"""深度调研四段流水线（P0）—— LLM 提议 / 确定性落地。

把项目铁律「规划/执行分离、LLM 提议、确定性 Executor 落地」下沉到 deep-research 内部：

  plan       : LLM 把研究问题拆成 3-5 个**带视角**的子问题（STORM 多视角），只产 JSON、不产结论；
               解析失败 → 确定性兜底（单子问题=原问题）。thinking 关（结构化 JSON）。
  investigate: 确定性**有界并行**迭代检索——每子问题经 _sdk/retrieval 检索正文级资料；
               空结果再换更宽 query 追一轮（max_rounds 有界）。子问题间 asyncio.gather 并行压延迟。
  synthesize : 复用 _sdk/grounding 的「强制引用 + 无依据弃权」内核，升级为**分节报告**
               （每子问题/视角一节，结论+引用+置信度，跨节诚实标 gaps）。thinking 自动开（深合成）。
  brief      : 确定性渲染——一段式 TTS 简报 + research_report 卡。LLM 不再产事实。

注入式：llm/search_provider/extractor 由调用方传入，本模块不依赖具体 Agent。
对症「单轮检索多跳天花板」：多跳/对比/时间线问题用有界多轮迭代覆盖后跳证据。
"""
from __future__ import annotations
import asyncio
import json
import logging
import re

from agents._sdk.retrieval import retrieve
from agents._sdk.grounding import shanghai_now, fallback_brief, latest_published
from agents._sdk.http import ProviderError
from .models import SubQuestion, Evidence, Section, Report, PERSPECTIVES

logger = logging.getLogger("agent.deep_research.pipeline")

# 子问题数量边界（太少覆盖不足、太多超延迟预算）。
MIN_SUBQ, MAX_SUBQ = 2, 5
# 每子问题检索条数 + 检索轮上限（空结果换宽 query 再来一轮）。
PER_Q_LIMIT = 4
MAX_ROUNDS = 2
# 合成材料每条证据正文配额 + 每子问题入材料的证据条数（控 prompt 体量防上游超时）。
_EXCERPT_CAP = 600
_EV_PER_SUBQ_IN_MATERIALS = 3
# 时效敏感词：命中则检索开 livecrawl 抓实时页 + 收窄时效窗口。
_FRESH_MARKERS = ("最新", "今年", "现在", "目前", "近期", "实时", "榜", "排行", "排名",
                  "趋势", "进展", "动态", "新款", "新车", "财报", "股价", "价格")
_RECENCY_MARKERS = ("最新", "今天", "今日", "现在", "目前", "近期", "实时", "动态")


def _is_fresh(text: str) -> bool:
    return any(m in (text or "") for m in _FRESH_MARKERS)


def _extract_json_block(text: str) -> str:
    """从 LLM 输出抠出第一个 {...} JSON 块（容忍 ```json 包裹与前后噪声）。"""
    if not text:
        return ""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t).rstrip("` \n")
    start, end = t.find("{"), t.rfind("}")
    return t[start:end + 1] if start != -1 and end > start else ""


# ──────────────────────────── plan ────────────────────────────

_PLAN_SYSTEM = (
    "你是严谨的车载深度调研规划助手。把用户的研究问题拆成 3-5 个**带视角**的子问题，"
    "覆盖不同角度（背景/对比/风险/最新进展/与用户处境的相关性），合在一起能完整回答原问题。\n"
    "只输出 JSON（无多余文字）：\n"
    '{"subquestions":[{"text":"具体、可检索的子问题","perspective":"背景|对比|风险|最新进展|适配用户"}]}\n'
    "子问题要具体、彼此不重复；**只产问题，不要产结论、不要编造事实**。"
)


def _constraints_note(constraints: dict | None) -> str:
    """把车辆/画像处境拼成一句提示，让子问题贴合「我」（座舱差异化）。"""
    c = constraints or {}
    bits = []
    if c.get("location"):
        bits.append(f"当前位置{c['location']}")
    if c.get("vehicle_state"):
        bits.append(f"车辆状态{c['vehicle_state']}")
    prefs = c.get("profile_prefs") or []
    if prefs:
        bits.append("偏好" + "、".join(prefs[:5]))
    if c.get("time_now"):
        bits.append(f"当前时间{c['time_now']}")
    if not bits:
        return ""
    return "用户处境：" + "；".join(bits) + "。请让子问题尽量贴合该处境。"


def _coerce_perspective(p: str, i: int) -> str:
    p = (p or "").strip()
    if p in PERSPECTIVES:
        return p
    return PERSPECTIVES[i % len(PERSPECTIVES)]


def _parse_plan(text: str, question: str) -> list[SubQuestion]:
    block = _extract_json_block(text)
    if not block:
        return []
    try:
        data = json.loads(block)
    except (json.JSONDecodeError, TypeError):
        return []
    out: list[SubQuestion] = []
    seen: set[str] = set()
    for i, sq in enumerate(data.get("subquestions") or []):
        if not isinstance(sq, dict):
            continue
        t = (sq.get("text") or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(SubQuestion(sq_id=f"sq{len(out) + 1}", text=t,
                               perspective=_coerce_perspective(sq.get("perspective"), i)))
        if len(out) >= MAX_SUBQ:
            break
    return out


async def plan(llm, question: str, constraints: dict | None = None) -> list[SubQuestion]:
    """LLM 拆带视角子问题；解析失败/过少 → 确定性兜底（至少含原问题）。"""
    user = f"研究问题：{question}\n{_constraints_note(constraints)}".strip()
    try:
        out = await llm.complete(
            [{"role": "system", "content": _PLAN_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.4, max_tokens=500, thinking=False)
    except Exception as e:
        logger.warning("plan LLM failed, deterministic fallback: %s", e)
        out = ""
    subqs = _parse_plan(out, question)
    if len(subqs) < MIN_SUBQ:
        # 兜底：至少把原问题作为一个子问题，保证 investigate 仍能跑（诚实降级，不臆造拆分）
        subqs = [SubQuestion(sq_id="sq1", text=question, perspective="背景")]
    return subqs[:MAX_SUBQ]


# ─────────────────────────── investigate ───────────────────────────

async def _retrieve_for(search_provider, extractor, query: str, meta) -> list[Evidence]:
    """单子问题检索 → Evidence 列表。ProviderError/空 → []（诚实降级，不臆造）。"""
    recency_days = 2 if any(m in query for m in _RECENCY_MARKERS) else 0
    livecrawl = "preferred" if _is_fresh(query) else ""
    try:
        sources = await retrieve(
            search_provider, query, limit=PER_Q_LIMIT, recency_days=recency_days,
            livecrawl=livecrawl, extractor=extractor, meta=meta)
    except ProviderError as e:
        logger.warning("investigate retrieve '%s' failed: %s", query, e)
        return []
    out = []
    for s in sources:
        excerpt = (s.get("content") or s.get("snippet") or "").strip()[:_EXCERPT_CAP]
        if not excerpt:
            continue
        out.append(Evidence(title=s.get("title", ""), url=s.get("url", ""),
                            source=s.get("source", ""), published=s.get("published", ""),
                            excerpt=excerpt))
    return out


async def investigate(search_provider, extractor, subqs: list[SubQuestion],
                      *, meta=None, max_rounds: int = MAX_ROUNDS) -> None:
    """确定性有界并行检索：每子问题 1 轮；空结果换更宽 query 再追 1 轮（受 max_rounds 约束）。"""
    async def one(sq: SubQuestion) -> None:
        sq.status = "searching"
        try:
            evs = await _retrieve_for(search_provider, extractor, sq.text, meta)
            if not evs and max_rounds >= 2:
                # gap 回溯/转向：换更宽 query 再来一轮（仿 Deep Research 的 backtrack）
                evs = await _retrieve_for(search_provider, extractor,
                                          f"{sq.text} 详细介绍", meta)
            sq.evidence = evs
            sq.status = "answered" if evs else "gap"
        except Exception as e:  # 单子问题失败不拖垮整批
            logger.warning("investigate subq failed: %s", e)
            sq.status = "gap"

    await asyncio.gather(*(one(sq) for sq in subqs), return_exceptions=True)


# ─────────────────────────── synthesize ───────────────────────────

_SYNTH_SYSTEM = (
    "你是严谨的车载深度调研分析师。只能依据提供的资料分节作答，宁可说没有也绝不编造。"
    "资料未覆盖的方面必须写进 gaps 诚实标注，禁止编造来源、数字、时间、人名或因果。"
)


def _assign_global_sources(subqs: list[SubQuestion]) -> list[dict]:
    """给所有证据分配全局来源编号（按 url 去重），回填 ev.idx，返回 sources 列表。"""
    sources: list[dict] = []
    by_key: dict[str, int] = {}
    for sq in subqs:
        for ev in sq.evidence:
            key = ev.url or f"{ev.title}|{ev.source}"
            if key in by_key:
                ev.idx = by_key[key]
                continue
            idx = len(sources) + 1
            by_key[key] = idx
            ev.idx = idx
            sources.append({"idx": idx, "title": ev.title, "url": ev.url,
                            "source": ev.source, "published": ev.published})
    return sources


def _build_grouped_materials(subqs: list[SubQuestion]) -> str:
    """按子问题分组拼材料块（带全局来源编号），供 LLM 分节合成。"""
    groups = []
    for sq in subqs:
        if not sq.evidence:
            continue
        lines = [f"【{sq.perspective}】{sq.text}"]
        for ev in sq.evidence[:_EV_PER_SUBQ_IN_MATERIALS]:
            head = f"[{ev.idx}] {ev.title}（来源：{ev.source}"
            if ev.published:
                head += f"，发布：{ev.published}"
            head += "）"
            lines.append(head + "\n" + ev.excerpt)
        groups.append("\n".join(lines))
    return "\n\n".join(groups)


def _parse_report(text: str, sources: list[dict]) -> Report | None:
    block = _extract_json_block(text)
    if not block:
        return None
    try:
        obj = json.loads(block)
    except (json.JSONDecodeError, TypeError):
        return None
    summary = str(obj.get("summary") or "").strip()
    valid_idx = {s["idx"] for s in sources}
    sections = []
    for sec in obj.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        body = str(sec.get("body") or "").strip()
        if not body:
            continue
        cits = [int(c) for c in (sec.get("citations") or [])
                if str(c).isdigit() and int(c) in valid_idx]
        conf = str(sec.get("confidence") or "medium").lower()
        if conf not in ("high", "medium", "low"):
            conf = "medium"
        sections.append(Section(heading=str(sec.get("heading") or "").strip(),
                                body=body, citations=cits, confidence=conf))
    if not summary and not sections:
        return None
    overall = str(obj.get("overall_confidence") or "medium").lower()
    if overall not in ("high", "medium", "low"):
        overall = "medium"
    gaps = [str(g).strip() for g in (obj.get("gaps") or []) if str(g).strip()]
    return Report(summary=summary, sections=sections, sources=sources,
                 overall_confidence=overall, gaps=gaps[:6])


def _fallback_report(question: str, subqs: list[SubQuestion],
                     sources: list[dict]) -> Report:
    """LLM 合成不可用时的诚实兜底：每子问题一节用首条证据节选，不编造、低置信。"""
    sections = []
    for sq in subqs:
        if not sq.evidence:
            continue
        body = (sq.evidence[0].excerpt or "").strip()
        sections.append(Section(heading=sq.text, body=body,
                                citations=[sq.evidence[0].idx], confidence="low"))
    gaps = [sq.text for sq in subqs if not sq.evidence]
    summary = fallback_brief(question, [{"snippet": s.body} for s in sections])
    return Report(summary=summary, sections=sections, sources=sources,
                 overall_confidence="low", gaps=gaps[:6],
                 freshness=latest_published(sources))


def _empty_report(question: str, subqs: list[SubQuestion]) -> Report:
    """完全没检索到资料：诚实弃权，不臆造。"""
    return Report(
        summary=f"关于「{question}」，暂时没有检索到足够可靠的资料形成结论，建议稍后再试。",
        sections=[], sources=[], overall_confidence="low",
        gaps=[sq.text for sq in subqs][:6])


async def synthesize(llm, question: str, subqs: list[SubQuestion],
                     constraints: dict | None = None) -> Report:
    """复用接地内核出**分节报告**：每子问题一节、强制引用、诚实标 gaps。失败诚实兜底。"""
    sources = _assign_global_sources(subqs)
    if not sources:
        return _empty_report(question, subqs)
    materials = _build_grouped_materials(subqs)
    note = _constraints_note(constraints)
    user = (
        f"研究问题：{question}\n"
        f"当前时间：{shanghai_now():%Y年%m月%d日 %H:%M}（Asia/Shanghai）\n"
        + (note + "\n" if note else "") +
        f"\n以下是按子问题分组的检索资料（方括号内为来源编号）：\n{materials}\n\n"
        "请只依据上述资料，输出一个 JSON 对象（不要额外文字）：\n"
        '{"summary":"一段式总体结论（≤3句，先结论，面向语音播报）",'
        '"sections":[{"heading":"小节标题","body":"该节正文，关键陈述标注来源编号如[1][2]",'
        '"citations":[1,2],"confidence":"high|medium|low"}],'
        '"overall_confidence":"high|medium|low","gaps":["未能从资料中确认的方面"]}\n'
        "要求：①先结论后展开，不说「根据资料显示」这类废话；②每条关键陈述带[编号]，"
        "无对应来源的陈述不要写；③资料没覆盖的写进 gaps，**禁止编造**数字/时间/人名/因果；"
        "④body 多要点时每条单独成行（\\n 分隔）；⑤不同资料数字冲突时取最权威最新者、给前后一致结论。"
    )
    try:
        raw = await llm.complete(
            [{"role": "system", "content": _SYNTH_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.3, max_tokens=1400, timeout=40)
    except Exception as e:
        logger.warning("synthesis failed, fallback report: %s", e)
        return _fallback_report(question, subqs, sources)
    raw = (raw or "").strip()
    if not raw or raw.startswith("[mock]"):
        return _fallback_report(question, subqs, sources)
    report = _parse_report(raw, sources)
    if report is None:
        return _fallback_report(question, subqs, sources)
    report.freshness = latest_published(sources)
    return report


# ─────────────────────────── brief ───────────────────────────

def brief(report: Report, question: str = "") -> tuple[str, dict]:
    """确定性渲染：一段式 TTS 简报 + research_report 卡。行车听简报、泊车读报告。"""
    speech = (report.summary or "").strip() or "这次调研没能得到足够可靠的结论。"
    if report.sections:
        speech += "\n\n完整调研报告已生成，停车后可查看。"
    if report.gaps:
        speech += f"（有 {len(report.gaps)} 个方面资料不足，已在报告中标注。）"
    return speech, report.card_dict(question)
