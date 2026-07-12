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


def fallback_brief(query: str, sources: list[dict]) -> str:
    """LLM 不可用时的诚实兜底：用清理后的 snippet 拼一句简述，不编造、不罗列编号。"""
    points = []
    for s in sources[:2]:
        t = (s.get("snippet") or "").strip().rstrip("。")
        if t:
            points.append(t)
    lead = f"关于「{query}」，" if query else ""
    if points:
        return lead + "；".join(points) + "。"
    return lead + "暂时没有足够资料形成可靠结论，建议稍后再查。"


# 截断 JSON 的 answer 抢救：捕获 "answer": " 之后的合法字符串体（\\. 成对，不会断在孤反斜杠）。
_TRUNC_ANSWER_RE = re.compile(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)')


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
    # JSON 被 max_tokens 截断（啰嗦 provider 的长 answer 撑爆预算，真栈 @MiniMax 实测：
    # 整段原始 JSON 被当话术上屏）→ 抢救 answer 字段已生成的部分，绝不把 JSON 外壳念给用户。
    if text.lstrip().startswith("{"):
        m = _TRUNC_ANSWER_RE.search(text)
        if m and m.group(1).strip():
            try:
                answer = json.loads(f'"{m.group(1)}"')   # 反转义 \n/\" 等
            except (ValueError, TypeError):
                answer = m.group(1)
            answer = answer.strip().rstrip("，,、；;：:")
            if answer:
                return {"answer": answer, "key_points": [],
                        "confidence": "low", "used_sources": []}
        return None            # JSON 外壳但连 answer 都没有：交调用方走诚实兜底
    # 非 JSON：剥离列表编号，合并为连续文本作为答案
    flat = LIST_MARKER.sub("", text)
    flat = " ".join(line.strip() for line in flat.splitlines() if line.strip())
    if flat:
        return {"answer": flat, "key_points": [], "confidence": "medium",
                "used_sources": []}
    return None


def build_materials(sources: list[dict], *, first_cap: int = 2400,
                    rest_cap: int = 900, limit: int = 5) -> str:
    """把来源资料拼成给 LLM 的材料块（带编号/来源/发布时间）。

    榜单/表格常在正文较深处：给最权威的首条更多正文配额，其余收紧，控总量防上游超时。
    """
    blocks = []
    for i, s in enumerate(sources[:limit]):
        cap = first_cap if i == 0 else rest_cap
        body = (s.get("content") or s.get("snippet") or "").strip()[:cap]
        head = f"[{s.get('idx', i + 1)}] {s.get('title', '')}（来源：{s.get('source', '')}"
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
    materials = build_materials(used, rest_cap=800, limit=6)
    prompt = (
        f"用户问题：{subject}\n"
        f"当前时间：{shanghai_now():%Y年%m月%d日 %H:%M}（Asia/Shanghai）\n\n"
        f"以下是检索到的资料（共{len(used)}条，方括号内为编号）：\n"
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
    )
    try:
        raw = await llm.complete([
            {"role": "system", "content":
             "你是严谨的车载信息编辑，只能依据提供的资料作答，宁可说没有也绝不编造。"},
            {"role": "user", "content": prompt},
        ], temperature=0.2, max_tokens=max_tokens, timeout=timeout, thinking=thinking)
    except Exception as e:
        logger.warning("grounded synthesis failed: %s", e)
        return None
    raw = (raw or "").strip()
    if not raw or raw.startswith("[mock]"):
        return None
    return parse_synth(raw)
