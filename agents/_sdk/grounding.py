"""接地合成内核（共享）——喂正文级资料、强制来源引用、无依据即诚实弃权。

从 info Agent 抽出（原 `_synthesize_grounded`/`_parse_synth`/`_clean_snippet`/`_fallback_brief`），
供 info（单轮搜索）与 deep-research（分节调研合成）共用。**注入式**：llm 由调用方传入，
本模块不依赖任何具体 Agent，避免 `_sdk → agent` 反向依赖。

设计原则（继承 2026-06-22 搜索质量重构）：
- 只依据提供的资料作答，资料未覆盖必须明说「未获取到」，禁止编造对阵/比分/时间/数字/人名/因果。
- 排行榜/数据类以最权威且最新的一条为准，绝不把互相矛盾的数字混进同一答案。
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
import logging
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .source_quality import rerank_by_authority, rerank_fresh_authority

logger = logging.getLogger("agent.sdk.grounding")

# 列表编号前缀（- * • 1. 一、 等），合成降级时剥离用。
LIST_MARKER = re.compile(r"(?m)^\s*(?:[-*•]|(?:\d+|[一二三四五六七八九十]+)[.、)）])\s*")


def shanghai_now() -> datetime:
    """上海时区当前时间（容器无 tzdata 时退回固定 +8 偏移）。"""
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except ZoneInfoNotFoundError:
        return datetime.now(timezone(timedelta(hours=8), name="Asia/Shanghai"))


def clean_snippet(text: str) -> str:
    """清理搜索结果 snippet：去 markdown 标记 + 末尾/中间省略号，保留语义。"""
    if not text:
        return ""
    text = re.sub(r'[.。…]{2,}$', '', text.strip())
    text = text.replace(' ... ', '，').replace('…', '，')
    # 去 markdown：行首标题#/引用>/列表-* + 内联强调**/代码`（网页正文偶带，防摘要出现"# 标题"片段）
    text = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', text)
    text = re.sub(r'(?m)^\s{0,3}>\s+', '', text)
    text = re.sub(r'(?m)^\s{0,3}[-*]\s+', '', text)
    text = text.replace('**', '').replace('`', '')
    return text.strip()


# ── speech 通道 markdown 归一 ────────────────────────────────────────────────
# 设计决策（2026-07-12，mode-routing 收尾）：speech 不上 markdown 渲染——第一消费者是
# TTS（渲染解决不了念星号），Aurora Glass 契约=气泡短结论/结构化在卡片，且各家 LLM 的
# md 输出不稳定（半吊子语法渲染反出乱码）。prompt 软约束（"不要 markdown"）保留，
# 这里是出口硬剥。保留 "1. 2." 数字分行要点（prompt 刻意要求，TTS 可读）。
_MD_FENCE = re.compile(r"(?m)^\s*```.*$")
_MD_TABLE_SEP = re.compile(r"(?m)^\s*\|?\s*:?-{2,}[-|:\s]*$")
_MD_TABLE_ROW = re.compile(r"(?m)^\s*\|(.+)\|\s*$")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_MD_QUOTE = re.compile(r"(?m)^\s{0,3}>\s?")
_MD_BULLET = re.compile(r"(?m)^(\s*)[-*•]\s+")


def strip_markdown_speech(text: str) -> str:
    """把 LLM 泄漏的 markdown 归一成 speech 可读纯文本（TTS/气泡口径）。

    清理：加粗/行内代码、围栏、#标题、>引用、无序列表符、链接[t](u)→t、
    表格行→顿号连接（表格进语音本就不可读，退化成逐行罗列）。
    不动：数字序号行（要点分行是刻意的）、单个 * （乘号/颜文字防误伤）。
    """
    t = text or ""
    if not any(ch in t for ch in ("*", "#", "`", "|", "[", ">", "_")):
        return t                                  # 快路径：无 md 字符（绝大多数话术）
    t = _MD_FENCE.sub("", t)
    t = _MD_TABLE_SEP.sub("", t)
    t = _MD_TABLE_ROW.sub(
        lambda m: "，".join(c.strip() for c in m.group(1).split("|") if c.strip()), t)
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_HEADING.sub("", t)
    t = _MD_QUOTE.sub("", t)
    t = _MD_BULLET.sub(r"\1", t)
    t = t.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def latest_published(sources: list[dict]) -> str:
    """取最新发布时间（ISO 字符串按字典序比较），供卡片时效展示。"""
    dates = [s.get("published") for s in sources if s.get("published")]
    return max(dates) if dates else ""


# 句末标点（收口截断用）；来源正文里的纯样板行（页面骨架词，进语音全是噪声）
_SENT_END = "。！？!?；;"
_BOILERPLATE_LINES = {"正文", "导读", "摘要", "广告", "相关阅读", "点击查看", "责任编辑"}


def clip_sentence(text: str, limit: int) -> str:
    """限长收口到句边界：limit 内取最后一个句末标点；退而求逗号；再退硬切加省略号。
    杜绝「拦腰截断+句号」直达用户（badcase 6d29929e：speech 以「…4支球队不。」收尾）。"""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for i in range(len(cut) - 1, -1, -1):
        if cut[i] in _SENT_END:
            return cut[:i + 1]
    comma = max(cut.rfind("，"), cut.rfind(","))
    if comma > limit // 2:
        return cut[:comma] + "……"
    return cut + "……"


def _first_prose(text: str) -> str:
    """从正文抽开头一段「人话」：跳过 SEO 标题行（含 | 分隔且无句末标点）、样板词行、
    开头的短碎行（面包屑/栏目名），剩余行顺序拼接（约 160 字后停）。"""
    picked: list[str] = []
    total = 0
    for ln in (line.strip() for line in (text or "").splitlines()):
        if not ln or ln in _BOILERPLATE_LINES:
            continue
        if ("|" in ln or "｜" in ln) and not any(ch in ln for ch in _SENT_END):
            continue                          # SEO 标题行（「A | B | C - 站名」）
        if len(ln) < 12 and not picked:
            continue                          # 开头的短碎行
        if picked and picked[-1][-1] not in _SENT_END + "，,、：:":
            picked.append("，")
        picked.append(ln)
        total += len(ln)
        if total >= 160:
            break
    return "".join(picked)


def fallback_brief(query: str, sources: list[dict]) -> str:
    """LLM 归纳不可用时的诚实兜底：抽 1-2 条来源的首段人话、句边界收口、总长受控，
    明说未完成归纳并指向卡片。绝不整段倾倒原文（badcase 6d29929e：两篇正文直拼 +
    拦腰截断直达用户，含 SEO 标题和「正文」样板字）。"""
    points = []
    for s in sources:
        t = _first_prose(clean_snippet(s.get("snippet") or s.get("content") or ""))
        if len(t) >= 12:
            points.append(clip_sentence(t, 110).rstrip(_SENT_END))
        if len(points) == 2:
            break
    lead = f"关于「{query}」，" if query else ""
    if points:
        return (lead + "归纳暂时没有完成，先念两条检索到的要点：" + "；".join(points)
                + "。完整来源见屏幕卡片。")
    return lead + "暂时没有足够资料形成可靠结论，建议稍后再查。"


def extract_json_str_field(text: str, field: str,
                           next_fields: tuple[str, ...]) -> tuple[str, bool]:
    """从**可能非法/截断**的 JSON 文本中边界式提取字符串字段值。返回 (值, 是否找到闭合边界)。

    对症两种 LLM 病（badcase 0f4105c4 / 6ce027fe）：
    - max_tokens 截断：字符串没写完 → 无边界，取到文本末尾（found=False，调用方标截断）；
    - 字符串值里写**裸英文双引号**（如 …马拉多纳的"上帝之手"…）→ json.loads 整体失败，
      而按「下一个引号」截取会拦腰截断。故按「引号 + 下一个已知字段名 / 收尾括号」找
      **真正结尾**，中间的裸引号原样保留为文本。
    """
    m = re.search(r'"%s"\s*:\s*"' % re.escape(field), text)
    if not m:
        return "", False
    start = m.end()
    boundary = re.compile(
        r'"\s*(?:,\s*"(?:%s)"\s*:|[}\]])' % "|".join(map(re.escape, next_fields)))
    b = boundary.search(text, start)
    raw = text[start:b.start()] if b else text[start:]
    # 手工反转义常见序列（不能 json.loads——文本可能含裸引号）；顺序：先 \\" 与 \\n，最后 \\\\
    val = (raw.replace('\\"', '"').replace("\\n", "\n")
              .replace("\\t", " ").replace("\\\\", "\\")).strip()
    return val, b is not None


def parse_synth(raw: str) -> dict | None:
    """解析接地合成的结构化输出。JSON 解析失败则把整段当作答案文本（去列表编号）。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        nl = text.find("\n")
        if nl != -1 and text[:nl].strip().lower() in ("json", ""):
            text = text[nl + 1:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            answer = str(obj.get("answer") or "").strip()
            if answer:
                kp = [str(p).strip() for p in (obj.get("key_points") or [])
                      if str(p).strip()]
                conf = str(obj.get("confidence") or "medium").lower()
                if conf not in ("high", "medium", "low"):
                    conf = "medium"
                used = [int(i) for i in (obj.get("used_sources") or [])
                        if str(i).isdigit()]
                return {"answer": answer, "key_points": kp[:8],
                        "confidence": conf, "used_sources": used}
        except (ValueError, TypeError):
            pass
    # JSON 非法（max_tokens 截断 / 字符串里裸英文引号，真栈 @MiniMax 两个 badcase）→
    # 边界式抢救 answer 字段，绝不把 JSON 外壳念给用户、也不在裸引号处拦腰截断。
    if text.lstrip().startswith("{"):
        answer, closed = extract_json_str_field(
            text, "answer", ("key_points", "confidence", "used_sources"))
        answer = answer.strip()
        if answer:
            if not closed:                       # 真截断：收口半句 + 降置信
                answer = answer.rstrip("，,、；;：:")
            conf_m = re.search(r'"confidence"\s*:\s*"(high|medium|low)"', text)
            conf = conf_m.group(1) if (closed and conf_m) else "low"
            return {"answer": answer, "key_points": [],
                    "confidence": conf, "used_sources": []}
        return None            # JSON 外壳但连 answer 都没有：交调用方走诚实兜底
    # 非 JSON：剥离列表编号，合并为连续文本作为答案
    flat = LIST_MARKER.sub("", text)
    flat = " ".join(line.strip() for line in flat.splitlines() if line.strip())
    if flat:
        return {"answer": flat, "key_points": [], "confidence": "medium",
                "used_sources": []}
    return None


# 抓取的网页正文可能带控制字符/孤立代理对（脏页面/编码残缺）。json 序列化后部分服务商在
# 请求解析层拒收整包（badcase 6d29929e：同模板同体量合成 11:08 成功、11:10 换一批来源即
# 422 秒拒 @MiniMax，MiMo 容忍）——进 prompt 前统一消毒，杜绝单条脏来源废掉整轮归纳。
_CTRL_OR_SURROGATE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")


def sanitize_prompt_text(text: str) -> str:
    """剥控制字符（保留 \\t\\n）与孤立 UTF-16 代理对，供拼 LLM prompt 的正文/标题使用。"""
    return _CTRL_OR_SURROGATE.sub("", text or "")


def build_materials(sources: list[dict], *, first_cap: int = 2400,
                    rest_cap: int = 900, limit: int = 5) -> str:
    """把来源资料拼成给 LLM 的材料块（带编号/来源/发布时间；正文/标题过消毒）。

    榜单/表格常在正文较深处：给最权威的首条更多正文配额，其余收紧，控总量防上游超时。
    """
    blocks = []
    for i, s in enumerate(sources[:limit]):
        cap = first_cap if i == 0 else rest_cap
        body = sanitize_prompt_text((s.get("content") or s.get("snippet") or "").strip()[:cap])
        title = sanitize_prompt_text(s.get("title", ""))
        head = f"[{s.get('idx', i + 1)}] {title}（来源：{s.get('source', '')}"
        if s.get("published"):
            head += f"，发布：{s['published']}"
        head += "）"
        blocks.append(f"{head}\n{body}")
    return "\n\n".join(blocks)


async def grounded_synthesis(llm, subject: str, sources: list[dict], *,
                             timeout: float = 25, max_tokens: int = 600,
                             thinking: bool = False,
                             recency_days: int = 0) -> dict | None:
    """基于正文级资料接地合成。返回 {answer,key_points,confidence,used_sources} 或 None
    （LLM 不可用，调用方走诚实兜底 fallback_brief）。要求**无依据即弃权**，从根上消除编造。
    限 6 源、首源截 2400 字其余 800 字——过大 prompt 会令上游 LLM 推理超时退化。

    **thinking 默认 False**：接地合成是「依据资料抽取/组织」的结构化任务，不需深推理；
    开思考(HEAVY_INTENT 经 meta 自动开)会让大正文(如整页 wiki)在 deadline 内推理超时
    DEADLINE_EXCEEDED → 退化兜底堆原文（实测 info.search「什么是固态电池」踩到，与深调研同源）。
    timeout 20→25 给大页面留余量。

    recency_days>0（调用方判定查询时效敏感）时改用时效+权威双序：窗口内的新源优先于
    窗口外的高权威旧源（对症榜单/比分/价格类被旧权威页压排）。
    """
    # 源质量加权：按域名权威重排 → 学术/官方/百科优先进 top-6（首源拿更多正文配额）；
    # 稳定排序，同档保留检索相关性序（榜单/赛事类来源多为同档，顺序不变，不影响既有逻辑）。
    if recency_days > 0:
        used = rerank_fresh_authority(sources, recency_days,
                                      key=lambda s: s.get("url", ""))[:6]
    else:
        used = rerank_by_authority(sources, key=lambda s: s.get("url", ""))[:6]

    def _prompt_for(subset: list[dict]) -> str:
        materials = build_materials(subset, rest_cap=800, limit=6)
        return (
            f"用户问题：{subject}\n"
            f"当前时间：{shanghai_now():%Y年%m月%d日 %H:%M}（Asia/Shanghai）\n\n"
            f"以下是检索到的资料（共{len(subset)}条，方括号内为编号）：\n"
            f"{materials}\n\n"
            "请只依据上述资料用中文作答，并严格遵守：\n"
            "1. 先给核心结论，再按需展开；不要说「根据搜索结果/资料显示」这类废话。\n"
            "2. 资料未覆盖的内容，明确说明「未能从检索到的资料中确认」，"
            "禁止编造对阵、比分、时间、数字、人名或因果关系。\n"
            "3. **排行榜/榜单/数据类**：以**最权威且最新**的那一条资料为准、照它的数据呈现，"
            "不要用你自己的记忆补全或改写名次/数字；不同资料数字冲突或时效不同时，取最新权威者"
            "并给出**前后一致**的结论、注明依据时间，**绝不**把互相矛盾的数字混进同一答案"
            "（例如说榜首16球却又称另一人也16球并列，自相矛盾）。\n"
            "4. 只输出一个 JSON 对象，不要额外文字，格式：\n"
            '{"answer": "给用户的结论文本", "key_points": ["要点1", "要点2"], '
            '"confidence": "high|medium|low", "used_sources": [1, 2]}\n'
            "answer 的可读性很重要：若有多个要点/条目/步骤，**每条单独成行**"
            "（用真实换行符 \\n 分隔，可带序号），不要把多条挤在一行；"
            "解释类问题用连贯段落、先结论后展开。"
            "key_points 是卡片用精简要点（每条≤30字，可为空）；"
            "confidence 反映资料对问题的覆盖程度；used_sources 是真正支撑结论的资料编号。"
            "JSON 字符串值内不要使用英文双引号，需要引用时用中文引号「」（否则 JSON 会解析失败）。"
        )

    async def _ask(subset: list[dict]) -> str:
        return await llm.complete([
            {"role": "system", "content":
             "你是严谨的车载信息编辑，只能依据提供的资料作答，宁可说没有也绝不编造。"},
            {"role": "user", "content": _prompt_for(subset)},
        ], temperature=0.2, max_tokens=max_tokens, timeout=timeout, thinking=thinking)

    try:
        raw = await _ask(used)
    except Exception as e:
        # 服务商内容风控拒收（badcase a3fad033：MiniMax 422 new_sensitive——检索源夹带
        # 敏感站内容整包被拒）：多为个别低权威来源夹带，收窄到权威 top-2 重试一次；
        # 仍被拒/其他错误才走调用方兜底。
        if _is_content_rejection(e) and len(used) > 2:
            logger.warning("synthesis content-rejected, retrying with top-2 authority: %s", e)
            try:
                raw = await _ask(used[:2])
            except Exception as e2:
                logger.warning("grounded synthesis failed after narrowed retry: %s", e2)
                return None
        else:
            logger.warning("grounded synthesis failed: %s", e)
            return None
    raw = (raw or "").strip()
    if not raw or raw.startswith("[mock]"):
        return None
    return parse_synth(raw)


def _is_content_rejection(err: Exception) -> bool:
    """服务商内容风控拒收特征：MiniMax new_sensitive / DashScope data_inspection /
    OpenAI 系 content_filter。4xx 响应体由 llm-gateway 带进错误文本（2026-07-13）。"""
    s = str(err).lower()
    return ("sensitive" in s or "data_inspection" in s or "content_filter" in s
            or "contentfilter" in s)
