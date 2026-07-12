"""深度调研四段流水线（P0）—— LLM 提议 / 确定性落地。

把项目铁律「规划/执行分离、LLM 提议、确定性 Executor 落地」下沉到 deep-research 内部：

  plan       : LLM 把研究问题拆成 3-5 个**带视角**的子问题（STORM 多视角），只产 JSON、不产结论；
               解析失败 → 确定性兜底（单子问题=原问题）。thinking 关（结构化 JSON）。
  investigate: 确定性**有界并行**迭代检索——每子问题经 _sdk/retrieval 检索正文级资料；
               空结果再换更宽 query 追一轮（max_rounds 有界）。子问题间 asyncio.gather 并行压延迟。
  synthesize : 复用 _sdk/grounding 的「强制引用 + 无依据弃权」内核，升级为**分节报告**
               （每子问题/视角一节，结论+引用+置信度，跨节诚实标 gaps）。thinking 关（大材料下
               开思考会 DEADLINE 退化，深度来自多轮检索而非此步）。
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
from agents._sdk.grounding import (shanghai_now, fallback_brief, latest_published,
                                   strip_markdown_speech, extract_json_str_field)
from agents._sdk.source_quality import rerank_by_authority, domain_tier
from agents._sdk.http import ProviderError
from .models import SubQuestion, Evidence, Section, Report, PERSPECTIVES

logger = logging.getLogger("agent.deep_research.pipeline")

# 子问题数量边界（同步深度调研要覆盖面，5-6 个角度；子问题间并行检索不显著增延迟；
# 上限 6 是为把 plan+investigate+synthesize 总时长压在 agent 85s 预算/网关 90s 上限内）。
MIN_SUBQ, MAX_SUBQ = 2, 6
# 异步「分钟级」深调研（deep=True）：不在请求路径、不受 90s 网关上限约束，放开覆盖面到 9 个角度、
# 合成预算翻倍（见 synthesize），换取真·深报告。同步路径默认 deep=False，行为不变。
MAX_SUBQ_DEEP = 9
# 每子问题检索条数 + 检索轮上限（空结果换宽 query 再来一轮）。
PER_Q_LIMIT = 5
MAX_ROUNDS = 2
# 证据「薄」阈值：深度模式下某子问题不足此数时，用 Exa research-paper 类目补权威学术文献（学术兜底）。
_THIN_EVIDENCE = 2
# 合成材料每条证据正文配额 + 每子问题入材料的证据条数：喂足料才出得了深报告；thinking 关后大材料不易超时。
_EXCERPT_CAP = 1000
_EV_PER_SUBQ_IN_MATERIALS = 3
_EV_PER_SUBQ_DEEP = 4
# 网页页眉/导航噪声行（Exa 正文偶含登录/搜索/栏目导航）→ 清理出证据正文，不喂合成、不污染兜底。
_CHROME_LINE = ("登录", "注册", "搜索", "首页", "菜单", "导航", "媒体品牌", "企业服务",
                "政府服务", "投资人服务", "创业者服务", "创投平台", "我要入驻", "下载App",
                "扫码", "关注我们", "版权所有", "Copyright", "意见反馈", "联系我们")


def _clean_excerpt(text: str) -> str:
    """剔除网页页眉/导航噪声行（登录/搜索/栏目名），保留正文。"""
    out = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if s in _CHROME_LINE or (len(s) <= 8 and any(c in s for c in _CHROME_LINE)):
            continue
        out.append(s)
    return "\n".join(out)


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

def _plan_system(deep: bool = False) -> str:
    """规划 system prompt。deep（异步分钟级深调研）时要求更多角度（8-9），覆盖更广。

    数量区间与解析 cap 对齐（同步 5-6/cap 6、deep 8-9/cap 9）——旧文案"5-7/8-11"让模型
    多产的角度在 `_parse_plan` 被 cap 截断白白丢弃（浪费 plan 预算）。"""
    count = "8-9" if deep else "5-6"
    return (
        f"你是严谨的调研规划助手。把用户的研究问题拆成 {count} 个**简短、可直接搜索**的子问题，"
        "从不同角度（背景/定义、原理/机制、对比、优劣/风险、最新进展、应用/案例）充分覆盖，"
        "合起来能支撑一份**有深度、成体系**的调研报告。\n"
        "硬要求：①每个子问题**像搜索查询一样简短（≤25字）**，聚焦一个角度；"
        "②**不要写成长句、不要加括号举例、不要堆砌限定词**；"
        "③**紧扣研究主题本身的字面，绝不引入主题之外的领域/场景/数字**"
        "（例如研究『loop engineering』就只查它本身，不要扯到汽车、电池、电量等无关领域）。\n"
        "只输出 JSON（无多余文字）：\n"
        '{"subquestions":[{"text":"简短子问题","perspective":"背景|对比|风险|最新进展|应用"}]}\n'
        "**只产问题，不要产结论、不要编造事实**。"
    )


def _constraints_note(constraints: dict | None) -> str:
    """把（与主题相关的）用户处境拼成**可选**提示。绝不强制贴合——否则会把无关主题带偏。

    刻意**不注入车辆电量**（与绝大多数研究主题无关，实测会把『loop engineering』带成『电量72%
    自适应控制』）；位置/画像仅作可选背景，由 LLM 自行判断是否相关。
    """
    c = constraints or {}
    bits = []
    if c.get("location"):
        bits.append(f"用户当前在{c['location']}")
    prefs = c.get("profile_prefs") or []
    if prefs:
        bits.append("用户偏好：" + "、".join(prefs[:3]))
    if not bits:
        return ""
    return ("（可选背景，仅当与研究问题直接相关时才结合，否则请完全忽略、不要为贴合而改变子问题方向）"
            + "；".join(bits) + "。")


def _coerce_perspective(p: str, i: int) -> str:
    p = (p or "").strip()
    if p in PERSPECTIVES:
        return p
    return PERSPECTIVES[i % len(PERSPECTIVES)]


def _parse_plan(text: str, question: str, cap: int = MAX_SUBQ) -> list[SubQuestion]:
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
        if len(out) >= cap:
            break
    return out


async def plan(llm, question: str, constraints: dict | None = None,
               *, deep: bool = False) -> list[SubQuestion]:
    """LLM 拆带视角子问题；解析失败/过少 → 确定性兜底（至少含原问题）。
    deep=True（异步分钟级深调研）放开到 MAX_SUBQ_DEEP 个角度，覆盖更广、报告更深。"""
    cap = MAX_SUBQ_DEEP if deep else MAX_SUBQ
    user = f"研究问题：{question}\n{_constraints_note(constraints)}".strip()
    try:
        out = await llm.complete(
            [{"role": "system", "content": _plan_system(deep)},
             {"role": "user", "content": user}],
            temperature=0.4, max_tokens=700 if deep else 500, thinking=False)
    except Exception as e:
        logger.warning("plan LLM failed, deterministic fallback: %s", e)
        out = ""
    subqs = _parse_plan(out, question, cap)
    if len(subqs) < MIN_SUBQ:
        # 兜底：至少把原问题作为一个子问题，保证 investigate 仍能跑（诚实降级，不臆造拆分）
        subqs = [SubQuestion(sq_id="sq1", text=question, perspective="背景")]
    return subqs[:cap]


# ─────────────────────────── investigate ───────────────────────────

def _ev_key(e: Evidence) -> str:
    return e.url or f"{e.title}|{e.source}"


def _merge_evidence(primary: list[Evidence], extra: list[Evidence]) -> list[Evidence]:
    """合并两批证据，按 url（无则 标题|来源）去重，保序、primary 优先。"""
    seen = {_ev_key(e) for e in primary}
    out = list(primary)
    for e in extra:
        if _ev_key(e) in seen:
            continue
        seen.add(_ev_key(e))
        out.append(e)
    return out


async def _retrieve_for(search_provider, extractor, query: str, meta,
                        *, category: str = "") -> list[Evidence]:
    """单子问题检索 → Evidence 列表。ProviderError/空 → []（诚实降级，不臆造）。

    研究检索**不开 livecrawl、不收窄时效窗口**：livecrawl×多子问题并发会让 Exa 频繁 18s 超时；
    过窄 recency 又会把结果过滤空。要的是相关正文，时效由合成按来源 published 如实呈现。
    `category`（如 "research paper"）透传给 Exa 做学术兜底定向，命中白名单才生效（见 search_exa）。
    """
    try:
        sources = await retrieve(
            search_provider, query, limit=PER_Q_LIMIT, extractor=extractor,
            category=category, meta=meta)
    except ProviderError as e:
        logger.warning("investigate retrieve '%s' failed: %s", query, e)
        return []
    out = []
    for s in sources:
        excerpt = _clean_excerpt(s.get("content") or s.get("snippet") or "")[:_EXCERPT_CAP]
        if not excerpt:
            continue
        out.append(Evidence(title=s.get("title", ""), url=s.get("url", ""),
                            source=s.get("source", ""), published=s.get("published", ""),
                            excerpt=excerpt))
    return out


async def investigate(search_provider, extractor, subqs: list[SubQuestion],
                      *, meta=None, max_rounds: int = MAX_ROUNDS,
                      deep: bool = False) -> None:
    """确定性有界并行检索：每子问题 1 轮；证据薄(<_THIN_EVIDENCE)换更宽 query 再追 1 轮
    合并（受 max_rounds 约束）。已带证据的子问题跳过检索（幂等化——深挖种子/重入不重检）。

    deep=True（异步分钟级深调研）额外做**学术兜底**：某子问题证据仍「薄」时，
    用 Exa `research paper` 类目补权威学术文献，回填薄弱角度——不新增小节、不收窄整体（只救薄的）。
    """
    async def one(sq: SubQuestion) -> None:
        if sq.evidence:                       # 预置证据（深挖种子）→ 不重检索
            sq.status = "answered"
            return
        sq.status = "searching"
        try:
            evs = await _retrieve_for(search_provider, extractor, sq.text, meta)
            if len(evs) < _THIN_EVIDENCE and max_rounds >= 2:
                # gap 回溯/转向：换更宽 query 再来一轮（仿 Deep Research 的 backtrack）。
                # 旧条件「空才回溯」放宽为「薄就回溯」，且**合并不替换**（首轮仅 1 条时那条仍保留）。
                extra = await _retrieve_for(search_provider, extractor,
                                            f"{sq.text} 详细介绍", meta)
                evs = _merge_evidence(evs, extra)
            if deep and len(evs) < _THIN_EVIDENCE:
                # 学术兜底：薄结果子问题补权威学术文献（research paper 类目），不替换、只补充去重
                papers = await _retrieve_for(search_provider, extractor, sq.text, meta,
                                             category="research paper")
                evs = _merge_evidence(evs, papers)
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
    """全局去重(按 url) + **按域名权威排序**分配来源编号，回填 ev.idx，返回 sources 列表。

    编号即权威序（[1]=最权威）：tier 降序、同档保留首现序（=检索相关性）。让报告的来源区与引用
    都以学术/官方/百科打头、内容农场垫底；与 synthesize 里「每子问题证据按权威进 top-N 材料」呼应。
    """
    uniq: dict[str, dict] = {}
    order = 0
    for sq in subqs:
        for ev in sq.evidence:
            key = ev.url or f"{ev.title}|{ev.source}"
            if key not in uniq:
                uniq[key] = {"first": order, "title": ev.title, "url": ev.url,
                             "source": ev.source, "published": ev.published, "evs": [ev]}
                order += 1
            else:
                uniq[key]["evs"].append(ev)
    ordered = sorted(uniq.values(), key=lambda u: (-domain_tier(u["url"]), u["first"]))
    sources: list[dict] = []
    for i, u in enumerate(ordered):
        idx = i + 1
        for ev in u["evs"]:
            ev.idx = idx
        sources.append({"idx": idx, "title": u["title"], "url": u["url"],
                        "source": u["source"], "published": u["published"]})
    return sources


def _build_grouped_materials(subqs: list[SubQuestion],
                             ev_per_subq: int = _EV_PER_SUBQ_IN_MATERIALS) -> str:
    """按子问题分组拼材料块（带全局来源编号），供 LLM 分节合成。"""
    groups = []
    for sq in subqs:
        if not sq.evidence:
            continue
        lines = [f"【{sq.perspective}】{sq.text}"]
        for ev in sq.evidence[:ev_per_subq]:
            head = f"[{ev.idx}] {ev.title}（来源：{ev.source}"
            if ev.published:
                head += f"，发布：{ev.published}"
            head += "）"
            lines.append(head + "\n" + ev.excerpt)
        groups.append("\n".join(lines))
    return "\n\n".join(groups)


# 抢救：section 对象内无嵌套花括号（citations 是整数数组），可逐块独立解析。
_SECTION_OBJ_RE = re.compile(r"\{[^{}]*\}")


def _rescue_section(chunk: str, valid_idx: set) -> Section | None:
    """单个 section 块：优先 json.loads；失败（裸引号/截断）→ 边界式抽 heading/body。"""
    sec = None
    try:
        obj = json.loads(chunk)
        if isinstance(obj, dict):
            sec = obj
    except (ValueError, TypeError):
        heading, _ = extract_json_str_field(chunk, "heading", ("body",))
        body, closed = extract_json_str_field(chunk, "body", ("citations", "confidence"))
        if heading and body and closed:      # 半截 body（未闭合）丢弃——节内容不完整不出
            sec = {"heading": heading, "body": body}
    if not sec or not sec.get("heading") or not sec.get("body"):
        return None
    cits = [int(c) for c in (sec.get("citations") or [])
            if str(c).isdigit() and int(c) in valid_idx]
    conf = str(sec.get("confidence") or "medium").lower()
    return Section(
        heading=strip_markdown_speech(str(sec["heading"]).strip()),
        body=strip_markdown_speech(str(sec["body"]).strip()),
        citations=cits,
        confidence=conf if conf in ("high", "medium", "low") else "medium")


def _rescue_truncated_report(text: str, sources: list[dict]) -> Report | None:
    """合成 JSON 非法（max_tokens 截断 / 字符串裸英文引号）时抢救 summary + 可恢复小节。

    badcase 0f4105c4：completion 打满 2400 token → json.loads 失败 → 整份退化 fallback
    堆原文——截断报告的前几节是完好的，丢掉它们是最差选择。badcase 6ce027fe：裸引号
    （…马拉多纳的"上帝之手"…）令整份 JSON 非法，逐块+边界式提取全部可恢复。
    抢救不到任何小节时返回 None（调用方走 fallback）。
    """
    summary, _ = extract_json_str_field(text, "summary", ("sections",))
    valid_idx = {s["idx"] for s in sources}
    sections = [s for s in (_rescue_section(c, valid_idx)
                            for c in _SECTION_OBJ_RE.findall(text)) if s is not None]
    if not sections:
        return None
    logger.warning("synthesis JSON invalid/truncated; rescued %d sections", len(sections))
    return Report(summary=strip_markdown_speech(summary) or sections[0].body[:120],
                  sections=sections, sources=sources, overall_confidence="low",
                  gaps=["报告解析不完整（生成截断或转义问题），仅保留可恢复的小节"])


def _parse_report(text: str, sources: list[dict]) -> Report | None:
    block = _extract_json_block(text)
    if not block:
        return None
    try:
        obj = json.loads(block)
    except (json.JSONDecodeError, TypeError):
        return _rescue_truncated_report(text, sources)
    # prompt 已要求 body 纯文本无 markdown，但换 provider 后是软约束——出口硬剥兜底
    # （加粗/表格/标题符进卡片显示成乱码、summary 进 TTS 念星号）。[N] 引用标记不受影响。
    summary = strip_markdown_speech(str(obj.get("summary") or "").strip())
    valid_idx = {s["idx"] for s in sources}
    sections = []
    for sec in obj.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        body = strip_markdown_speech(str(sec.get("body") or "").strip())
        if not body:
            continue
        cits = [int(c) for c in (sec.get("citations") or [])
                if str(c).isdigit() and int(c) in valid_idx]
        conf = str(sec.get("confidence") or "medium").lower()
        if conf not in ("high", "medium", "low"):
            conf = "medium"
        sections.append(Section(heading=strip_markdown_speech(str(sec.get("heading") or "").strip()),
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
    """LLM 合成不可用时的诚实兜底：每子问题一节用首条证据节选，不编造、低置信。

    节选必须**短且干净**（剥 markdown/页面残迹 + 截 200 字）——badcase 0f4105c4：旧版把
    上千字原始维基正文整段灌进 speech/卡片，行车语音完全不可读。宁短勿糊脸。
    """
    sections = []
    for sq in subqs:
        if not sq.evidence:
            continue
        body = strip_markdown_speech((sq.evidence[0].excerpt or "").strip())[:200]
        if body and not body.endswith(("。", "！", "？")):
            body = body.rstrip("，、；：,;") + "……"
        sections.append(Section(heading=sq.text, body=body,
                                citations=[sq.evidence[0].idx], confidence="low"))
    gaps = [sq.text for sq in subqs if not sq.evidence]
    gaps.append("自动合成暂不可用，以上为资料节选，建议稍后重试获取完整报告")
    summary = fallback_brief(question, [{"snippet": s.body[:80]} for s in sections[:2]])
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
                     constraints: dict | None = None, *, deep: bool = False) -> Report:
    """复用接地内核出**分节报告**：每子问题一节、强制引用、诚实标 gaps。失败诚实兜底。

    deep=True（异步分钟级深调研）：不受 90s 网关上限约束，合成预算放大（max_tokens 2400→6000、
    timeout 55→150）、要求更多小节（8-9）与更长正文、每节喂更多证据，换取真·深报告。

    预算与要求对齐（2026-07-12 badcase 0f4105c4：MiniMax 按旧要求 5-7节×250-450字 写满
    2400 token 被截断 → JSON 解析失败 → 整份退化 fallback 堆原文）：要求的字数上限
    必须落在 max_tokens 内有余量——sync 5-6节×180-300字≈≤1800字；deep 8-9节×300-500字
    ≈≤4500字 配 6000 token。截断仍可能（啰嗦 provider），由 _parse_report 抢救已完整小节。
    """
    # 源质量加权：合成前按域名权威重排每子问题证据 → 学术/官方/百科上移、内容农场下沉。
    # 既决定来源编号(靠前=更权威)、也决定哪几条进 top-N 合成材料。稳定排序，同档保留检索相关性序。
    for sq in subqs:
        sq.evidence = rerank_by_authority(sq.evidence, key=lambda e: e.url)
    sources = _assign_global_sources(subqs)
    if not sources:
        return _empty_report(question, subqs)
    ev_per = _EV_PER_SUBQ_DEEP if deep else _EV_PER_SUBQ_IN_MATERIALS
    materials = _build_grouped_materials(subqs, ev_per)
    note = _constraints_note(constraints)
    sec_count = "8-9" if deep else "5-6"
    body_len = "300-500 字" if deep else "180-300 字"
    user = (
        f"研究问题：{question}\n"
        f"当前时间：{shanghai_now():%Y年%m月%d日 %H:%M}（Asia/Shanghai）\n"
        + (note + "\n" if note else "") +
        f"\n以下是按子问题分组的检索资料（方括号内为来源编号）：\n{materials}\n\n"
        "请只依据上述资料，写一份**有深度、成体系**的调研报告，输出一个 JSON 对象（不要额外文字）：\n"
        '{"summary":"一段式总体结论（≤3句，先结论，面向语音播报）",'
        '"sections":[{"heading":"小节标题","body":"该节详实正文，关键陈述标注来源编号如[1][2]",'
        '"citations":[1,2],"confidence":"high|medium|low"}],'
        '"overall_confidence":"high|medium|low","gaps":["未能从资料中确认的方面"]}\n'
        f"要求：①**报告要充分展开**——按上面资料的角度组织 **{sec_count} 个小节**，"
        f"**每节 body 详实（{body_len}）**，写出具体机制/定义/数据/案例/对比，**充分利用提供的多条资料**、"
        "不要泛泛几句带过；②先结论后展开，不说「根据资料显示」这类废话；③每条关键陈述带[编号]，"
        "尽量综合**多条**来源（别每节只引一条）、无对应来源的陈述不要写；④资料没覆盖的写进 gaps，"
        "**禁止编造**数字/时间/人名/因果；⑤body 多要点时每条单独成行（\\n 分隔）；"
        "⑥不同资料数字冲突时取最权威最新者、给前后一致结论；"
        "⑦**body 用纯文本中文，不要任何 markdown 标记（#、**、- 、> 等），不要在正文里贴网址**"
        "（来源由编号引用，链接另在来源区）；"
        "⑧JSON 字符串值内**不要使用英文双引号**，需要引用时用中文引号「」（否则 JSON 会解析失败）。"
    )
    try:
        # thinking=False：分节合成是「组织已检索证据」的结构化任务，不需深推理；开思考(MiMo 2048
        # reasoning tokens)在大材料下频繁 DEADLINE_EXCEEDED 退化兜底（实测）。深度来自多轮检索而非此步。
        raw = await llm.complete(
            [{"role": "system", "content": _SYNTH_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.3, max_tokens=6000 if deep else 2400,
            timeout=150 if deep else 55, thinking=False)
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
