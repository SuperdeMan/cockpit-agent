"""新闻域：聚合(Exa+SerpApi)/时效质量重排/逐条摘要/个性化排序/深挖桥接(news_active)。

繁→简与上海时区 now 走 _util 共享；来源权威分层走 _sdk.source_quality。
"""
from __future__ import annotations
from datetime import datetime, timedelta
import json
import logging
import re
from urllib.parse import urlparse

from agents._sdk import AgentResult
from agents._sdk.http import ProviderError
from agents._sdk.grounding import clean_snippet
from agents._sdk.source_quality import domain_tier
from agents._sdk.shared_state import NEWS_ACTIVE

from ._util import _shanghai_now, _to_simplified

logger = logging.getLogger("agent.info")

_NEWS_SUBJECT_RE = re.compile(
    r"([一-鿿A-Za-z0-9·&]{2,15}?)(?:的)?(?:最新)?(?:消息|新闻|资讯|动态|头条|进展)")
_NEWS_SUBJECT_STRIP = ("查一下", "查查", "看一下", "看一看", "看看", "帮我查", "帮我看",
                       "帮我", "今天", "今日", "最近", "现在", "查", "看", "、", "，", ",")
# 提取到的"主体"含这些（疑问/泛指/泛新闻词）说明不是具体实体，回落泛新闻（含子串即判）
_NEWS_NON_SUBJECT = ("有什么", "有啥", "什么", "啥", "哪些", "有没有", "最新", "今天",
                     "今日", "一些", "值得关注", "值得", "重要", "热点", "要闻", "大事", "头条")
# 标题恰为这些=门户版块/栏目落地页（非具体文章），综合新闻检索偶尔命中 → 剔除（精确匹配）
_NEWS_SECTION_TITLES = {
    "即时", "即時", "最新", "最新消息", "最新新闻", "最新新聞", "新闻联播", "今日要闻", "今日热点",
    "最新国内新闻", "最新国际新闻", "国内新闻", "國內新聞", "国际新闻", "國際新聞", "今日新闻", "今日新聞",
    "国际", "國際", "国内", "國內", "要闻", "要聞", "头条", "頭條", "热点", "熱點", "时事", "時事",
    "财经", "財經", "科技", "体育", "體育", "社会", "社會", "军事", "軍事", "娱乐", "娛樂",
    "时政", "時政", "推荐", "推薦", "视频", "視頻", "首页", "首頁", "新闻", "新聞", "资讯", "資訊"}


def _extract_news_subject(raw: str) -> str:
    """从原句兜底提取新闻主体："查一下今天英伟达最新消息"→"英伟达"。
    剥掉前导动词/时间词；疑问/泛指或提取不到返回空串（交泛新闻默认）。"""
    m = _NEWS_SUBJECT_RE.search(raw or "")
    if not m:
        return ""
    s = m.group(1).strip()
    changed = True
    while changed and s:
        changed = False
        for pre in _NEWS_SUBJECT_STRIP:
            if s.startswith(pre) and len(s) > len(pre):
                s, changed = s[len(pre):], True
    s = s.strip()
    return "" if (not s or any(w in s for w in _NEWS_NON_SUBJECT)) else s


# ── 新闻个性化：从画像兴趣给泛新闻排序（P2）─────────────────────────
_INTEREST_STRIP = ("用户", "我", "他", "她", "经常", "比较", "特别", "平时", "喜欢",
                   "关注", "感兴趣", "对", "想看", "想了解", "希望", "看", "爱看", "在意")
_INTEREST_STOP = {"新闻", "资讯", "领域", "方面", "内容", "信息", "话题", "东西", "这些",
                  "一些", "相关", "等等", "之类"}


def _news_interest_keywords(texts: list[str]) -> list[str]:
    """从召回的画像兴趣文本里抽兴趣关键词（剥『用户关注/喜欢…』前缀、去停用词、去尾缀『新闻/资讯』）。"""
    kws, seen = [], set()
    for t in texts:
        s = (t or "").strip()
        changed = True
        while changed and s:                       # 循环剥前缀（用户关注/我喜欢/对…）
            changed = False
            for p in _INTEREST_STRIP:
                if s.startswith(p):
                    s, changed = s[len(p):], True
        for tok in re.split(r"[、,，。;；和及与以及/\s（）()]+", s):
            tok = re.sub(r"(新闻|资讯|领域|方面|动态|消息|感兴趣|有兴趣|话题)$", "",
                         tok.strip("的等之类 ")).strip()
            if (2 <= len(tok) <= 8 and tok not in _INTEREST_STOP
                    and re.search(r"[一-鿿A-Za-z]", tok) and tok not in seen):
                seen.add(tok)
                kws.append(tok)
    return kws[:8]


def _rank_news_by_interest(raw: list[dict], kws: list[str]) -> tuple[list[dict], list[str]]:
    """命中兴趣关键词的新闻置顶（稳定排序，同分保持原序）。返回 (排序后, 实际命中的关键词)。"""
    if not kws:
        return raw, []
    hit: list[str] = []

    def score(n: dict) -> int:
        text = (n.get("title") or "") + (n.get("snippet") or "")
        ks = [k for k in kws if k in text]
        hit.extend(ks)
        return len(ks)

    ranked = sorted(raw, key=lambda n: -score(n))
    return ranked, sorted(set(hit))


# 新闻发布时间归一：把相对时间("3小时前"/"昨天")在采集时即转绝对 ISO（研究文档明确建议），
# 否则 HMI relativeTime 解析不了、freshness 字符串比较也错。无法解析返回 ""（不展示错误时间）。
_REL_UNIT = (("分钟前", "minutes"), ("小时前", "hours"), ("天前", "days"))


def _normalize_publish_time(raw: str, now: datetime | None = None) -> str:
    """新闻发布时间 → 绝对 ISO(YYYY-MM-DDTHH:MM:SS)。相对/中文/英文日期均归一；无法解析→""。"""
    s = (raw or "").strip()
    if not s or s == "mock":
        return ""
    now = now or _shanghai_now()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)", s)  # 已是 ISO
    if m:
        t = m.group(4)
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{t if len(t) == 8 else t + ':00'}"
    if re.match(r"\d{4}-\d{2}-\d{2}$", s):
        return s + "T00:00:00"
    if "刚刚" in s:
        return now.strftime("%Y-%m-%dT%H:%M:%S")
    for kw, unit in _REL_UNIT:                       # X分钟前/X小时前/X天前
        m = re.search(r"(\d+)\s*" + kw, s)
        if m:
            return (now - timedelta(**{unit: int(m.group(1))})).strftime("%Y-%m-%dT%H:%M:%S")
    m = re.search(r"(\d+)\s*周前", s)
    if m:
        return (now - timedelta(weeks=int(m.group(1)))).strftime("%Y-%m-%dT00:00:00")
    if "今天" in s:
        return now.strftime("%Y-%m-%dT00:00:00")
    if "昨天" in s:
        return (now - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
    if "前天" in s:
        return (now - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")
    m = re.search(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]", s)   # 中文绝对日期
    if m:
        y = int(m.group(1)) if m.group(1) else now.year
        return f"{y:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}T00:00:00"
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})", s)            # SerpApi Google MM/DD/YYYY
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}T00:00:00"
    return ""


def _iso_sortkey(ts: str) -> float:
    """ISO 时间 → 可比较排序键（无效→0=最旧）。"""
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "")[:19]).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _news_source_key(n: dict) -> str:
    """新闻来源去重键：优先 url 域名，否则来源名。供来源多样性上限用。"""
    u = n.get("url") or ""
    if u:
        try:
            host = urlparse(u).netloc.lower()
            return host[4:] if host.startswith("www.") else host
        except Exception:
            pass
    return (n.get("source") or "").strip().lower()


def _rank_news_quality(items: list[dict], per_source_cap: int = 3) -> list[dict]:
    """新闻重排：①沉内容农场(tier0)到末尾；②非农场按发布时间新→旧；③**来源多样性**——每来源
    最多 per_source_cap 条进主区、超出降补充区，避免单一来源刷屏（实测「8 条全 36 氪」）。

    返回 主区(多样·新→旧) + 补充区(同源溢出·新→旧) + 农场区。**刻意不按 tier 优先排序**——
    那会把同一权威源(如 36 氪)全顶到前面、牺牲多样性；权威性只用于沉农场，正文质量靠多样+时效。
    """
    nonfarm = [n for n in items if domain_tier(n.get("url") or "") > 0]
    farm = [n for n in items if domain_tier(n.get("url") or "") == 0]
    nonfarm.sort(key=lambda n: _iso_sortkey(n.get("publish_time") or ""), reverse=True)
    primary, overflow, seen = [], [], {}
    for n in nonfarm:
        k = _news_source_key(n)
        seen[k] = seen.get(k, 0) + 1
        (primary if seen[k] <= per_source_cap else overflow).append(n)
    return primary + overflow + farm


def _summary_adds_info(summary: str, title: str) -> bool:
    """摘要是否比标题多信息：非空、且与标题不互相包含（信源正文太薄时摘要常只回显标题→去重）。"""
    norm = lambda x: re.sub(r"[\s\-—｜|_]+", "", x or "")
    s, t = norm(summary), norm(title)
    return bool(s) and s not in t and t not in s


def _recent_only(items: list[dict], days: int = 3) -> list[dict]:
    """丢弃发布时间早于 days 天的陈旧新闻（对症 provider 兜底返回数天前旧闻）。

    无发布时间的保留（可能是新的、不误杀）；若过滤后全空则退回原列表（有旧闻总好过无新闻）。
    publish_time 已归一为 ISO，字典序可直接比较。
    """
    cutoff = (_shanghai_now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    recent = [n for n in items
              if not (n.get("publish_time") or "").strip() or n["publish_time"] >= cutoff]
    return recent or items


class NewsMixin:
    @staticmethod
    def _is_junk_news(title: str, url: str, content: str) -> bool:
        """剔除门户首页/栏目页/错误页等非新闻条目（宽泛新闻检索偶尔会命中）。"""
        t, c = (title or "").strip(), content or ""
        # 栏目/版块名（即時/最新消息/新闻联播/國際…）= 门户版块页非文章 → 丢。
        # 按**首段**判（原始标题常带来源尾巴如「最新消息 | UN News」「新闻联播_央视网」）。
        head = re.split(r"[\s\-—_｜|]+", t, 1)[0].strip() if t else ""
        if t in _NEWS_SECTION_TITLES or head in _NEWS_SECTION_TITLES:
            return True
        if any(k in t for k in ("首页", "新闻中心", "频道首页", "新闻列表", "焦点图")):
            return True
        if any(k in c for k in ("浏览器版本", "版本过低", "请升级", "请使用最新",
                                 "开启JavaScript", "启用JavaScript", "您的浏览器")):
            return True
        if url:  # 仅当有 url 时判断纯域名根（首页）；serpapi 兜底无 url 不应被误删
            try:
                if not urlparse(url).path.strip("/"):
                    return True
            except Exception:
                pass
        return False

    async def _news_from_exa(self, topic: str, limit: int, meta) -> list[dict]:
        """Exa 正文级新闻：返回全文+发布时间，利于逐条摘要；recency 2 天。失败/空 → []。

        综合新闻用**自然问句**作 query——Exa 是神经语义检索，吃自然语言、不吃「头条要闻」式关键词堆
        （后者实测返回空 → 回落 provider 旧闻）。
        """
        query = f"{topic} 最新进展" if topic else "今天有哪些值得关注的重要新闻 最新"
        try:
            results = await self.search.search(
                query, limit=limit + 5, meta=meta, recency_days=2, category="news")
        except ProviderError as e:
            logger.warning("exa news failed: %s", e)
            return []
        return [{"title": r.title, "url": r.url, "source": r.source,
                 "publish_time": _normalize_publish_time(r.published),
                 "snippet": clean_snippet(r.snippet or (r.content[:300] if r.content else ""))}
                for r in results
                if r.title and not self._is_junk_news(r.title, r.url, r.content)]

    async def _news_from_provider(self, topic: str, limit: int, meta) -> list[dict]:
        """SerpApi 新闻源（Google/Baidu News，多来源广覆盖头条）→ AnySearch → mock。失败/空 → []。"""
        try:
            items = await self.news.headlines(topic=topic, limit=limit + 5, meta=meta)
        except ProviderError as e:
            logger.warning("news provider failed, fallback to mock: %s", e)
            try:
                items = await self._fallback_news.headlines(topic=topic, limit=limit, meta=meta)
            except ProviderError:
                return []
        return [{"title": n.title, "url": n.url, "source": n.source,
                 "publish_time": _normalize_publish_time(n.publish_time),
                 "snippet": clean_snippet(n.summary)}
                for n in items if not self._is_junk_news(n.title, n.url, n.summary)]

    async def _gather_news(self, topic: str, limit: int, meta) -> list[dict]:
        """聚合新闻：Exa 优先（近期正文+发布时间，利于逐条摘要+时效），SerpApi 新闻源兜底；
        再「时效过滤（去数天前旧闻）+沉内容农场+来源多样性上限」重排。

        说明：综合要闻**合并 Exa + 新闻源**——Exa 语义检索对「今日头条」run-to-run 方差大（有时多源、
        有时寥寥），合并新闻源补足材料、再统一时效过滤去旧闻+去重+沉农场+来源多样性，稳住覆盖面；
        话题新闻仍 Exa 优先（全文利于逐条摘要）。真·策展级多源均衡仍需接 News API/RSS（见 docs/research）。
        """
        if topic:
            items = (await self._news_from_exa(topic, limit, meta)
                     or await self._news_from_provider(topic, limit, meta))
        else:
            exa_items = await self._news_from_exa("", limit, meta)
            prov_items = await self._news_from_provider("", limit, meta)
            items = exa_items + prov_items
        return _rank_news_quality(_recent_only(items))

    @staticmethod
    def _dedup_news(items: list[dict]) -> list[dict]:
        """按标题去重——serpapi 常返回同标题多条（如"今日投资舆情热点"重复 N 次）。"""
        seen, out = set(), []
        for n in items:
            key = (n.get("title") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(n)
        return out

    @staticmethod
    def _clean_title(title: str) -> str:
        """清理新闻标题里的栏目/来源尾巴（如「…|治疗|靶点」「…_新浪网」），用于卡片来源链接。"""
        t = (title or "").strip()
        # 「|」「｜」是栏目/来源分隔，中文新闻绝大多数为「标题 ｜ 来源」→ 取首段作正文标题
        # （兼容全角｜+长来源名如「… ｜ 公視新聞網 PNN」；旧码只认半角|漏掉全角致台媒尾巴没切）
        m = re.split(r"[|｜]", t, 1)
        if len(m) > 1 and m[0].strip():
            t = m[0].strip()
        # 「_」「 - 」常是来源尾巴；主标题足够长才切，避免误伤正文里的下划线/连字符
        for sep in ("_", " - ", " – "):
            idx = t.find(sep)
            if idx >= 4:
                t = t[:idx].strip()
                break
        # 尾部来源标签：「-36氪」「-新浪科技」「｜界面」等（分隔符 + 含中文的短媒体名，无空格也切）
        m = re.search(r"[\-—｜|]\s*([^\-—｜|]{1,8})$", t)
        if m and re.search(r"[一-鿿]", m.group(1)) and len(t) - len(m.group(0)) >= 5:
            t = t[:m.start()].strip()
        return t or (title or "").strip()

    @staticmethod
    def _first_sentence(text: str, limit: int = 40) -> str:
        """取首句作兜底一句话摘要（LLM 不可用时）。"""
        t = (text or "").strip()
        for sep in ("。", "！", "？", "\n"):
            idx = t.find(sep)
            if 0 < idx <= limit:
                return t[:idx + 1]
        return t[:limit]

    async def _summarize_news_list(
            self, subject: str,
            items: list[dict]) -> tuple[str, dict[int, str], dict[int, str]]:
        """一次 LLM 调用产出：总体概述 + 逐条一句话摘要 + **逐条简体中文标题**（按编号）。
        返回 (overview, {idx: summary}, {idx: 简体标题})；失败返回 ("", {}, {})。
        标题转换只做繁→简（台/港源标题转简体），LLM 调用兜底失败时调用方退回原标题。
        """
        # snippet 收到 120 字：标题繁→简不需长正文，缩输入降 MiMo 推理延迟（原 400 字 ×10 条致 20s DEADLINE）
        blocks = [f"[{i}] {n['title']}\n{(n.get('snippet') or '')[:120]}"
                  for i, n in enumerate(items, 1)]
        prompt = (
            f"用户想看：{subject}（今日新闻速览）\n"
            f"当前时间：{_shanghai_now():%Y年%m月%d日}\n\n"
            f"以下是 {len(items)} 条新闻（方括号内为编号）：\n" + "\n\n".join(blocks) + "\n\n"
            "只依据各条内容输出一个 JSON：\n"
            '{"overview": "一句话总体概述（≤40字，简体中文）", '
            '"summaries": {"1": "该条一句话摘要（≤30字，简体中文，仅当比标题更有信息量才给、否则留空）", "2": "…"}}\n'
            "全部用**简体中文**；摘要只依据对应编号内容、不得编造张冠李戴；只输出 JSON。"
        )
        try:
            raw = await self.llm.complete([
                {"role": "system", "content": "你是严谨的车载新闻编辑，只归纳给定内容，绝不编造。"},
                {"role": "user", "content": prompt},
            ], temperature=0.2, max_tokens=1200, timeout=30)
        except Exception as e:
            logger.warning("news list summarize failed: %s", e)
            return "", {}, {}
        raw = (raw or "").strip()
        if not raw or raw.startswith("[mock]"):
            return "", {}, {}
        text = raw.strip().strip("`")
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return "", {}, {}
        try:
            obj = json.loads(text[start:end + 1])
        except (ValueError, TypeError):
            return "", {}, {}
        overview = str(obj.get("overview") or "").strip()
        summaries: dict[int, str] = {}
        for k, v in (obj.get("summaries") or {}).items():
            if str(k).isdigit() and str(v).strip():
                summaries[int(k)] = str(v).strip()
        titles: dict[int, str] = {}
        for k, v in (obj.get("titles") or {}).items():
            if str(k).isdigit() and str(v).strip():
                titles[int(k)] = str(v).strip()
        return overview, summaries, titles

    async def _news(self, intent, ctx, meta) -> AgentResult:
        topic = (intent.slots.get("topic") or "").strip()
        # planner 在复杂多意图句里常漏抽 topic（"查英伟达最新消息、股价…"）→ 从原句兜底提取
        # "X最新消息/X新闻"的主体，否则会拿"今日值得关注"的泛新闻（与"英伟达消息"不符）。
        if not topic:
            topic = _extract_news_subject(intent.raw_text or "")
        # 座舱看新闻 = 一屏扫到约 10 条带一句话摘要的列表
        limit = int(intent.slots.get("limit", 10 if not topic else 8) or (10 if not topic else 8))
        subject = topic or "今日值得关注的新闻"

        raw = self._dedup_news(await self._gather_news(topic, limit, meta))[:limit]
        if not raw:
            return AgentResult(speech="暂无新闻资讯。")

        # 个性化（P2）：泛新闻（无指定 topic）时按画像兴趣置顶；有明确 topic 不重排（用户已指定）。
        hit: list[str] = []
        if not topic:
            interests = await self._recall_interests(ctx)
            if interests:
                raw, hit = _rank_news_by_interest(raw, interests)

        overview, summaries, titles = await self._summarize_news_list(subject, raw)
        lines, items = [], []
        for i, n in enumerate(raw, 1):
            # 标题繁→简（zhconv 确定性，台/港源转简体）再清栏目/来源尾巴；摘要同样归一简体。
            clean_t = self._clean_title(_to_simplified(titles.get(i) or n["title"]))
            one = _to_simplified(summaries.get(i) or self._first_sentence(n.get("snippet", "")))
            # 摘要与标题近重复（信源正文太薄→只能回显标题）就不放，避免卡片标题渲染两遍/语音复读。
            card_sum = one if _summary_adds_info(one, clean_t) else ""
            lines.append(f"{i}. {card_sum or clean_t}")   # 有真摘要播摘要，否则播干净标题
            # 卡片：标题+（有信息量的）摘要+来源+发布时间，车机一屏可扫读。
            items.append({"title": clean_t, "summary": card_sum,
                          "url": n.get("url", ""), "source": n["source"],
                          "publish_time": n["publish_time"]})

        # 座舱以 TTS 播报为本：语音/气泡 = 总览 + 逐条一句话提炼（听完即可，无需点开）；
        # 卡片 = 可点开的来源清单（想看原文才点）。
        head = _to_simplified(overview) or (f"关于{topic}的新闻有 {len(raw)} 条：" if topic
                                            else f"今天值得关注的新闻有 {len(raw)} 条：")
        if hit:                                    # 个性化命中 → 告知优先了哪些关注点
            head = f"（已为你优先放了关注的{'、'.join(hit[:3])}）" + head
        speech = head + "\n" + "\n".join(lines)
        await self._save_news_active(ctx, items)   # 持久化供「深挖第N条」桥接 research.run（P2）
        fresh = [n["publish_time"] for n in raw
                 if n["publish_time"] and n["publish_time"] != "mock"]
        card = {"type": "news_brief", "topic": topic, "items": items,
                "freshness": max(fresh) if fresh else ""}
        return AgentResult(speech=speech, ui_card=card,
                           follow_up="想深入某条说『详细讲讲第N条』。",
                           data={"items": items})

    async def _recall_interests(self, ctx) -> list[str]:
        """召回用户兴趣画像 → 兴趣关键词（供泛新闻个性化排序）。无 user_id/无记忆 → []。"""
        try:
            hits = await ctx.recall(query="关注 兴趣 喜欢 领域 行业 话题",
                                    top_k=8, min_score=0.15)
        except Exception as e:
            logger.debug("news interest recall skipped: %s", e)
            return []
        return _news_interest_keywords([str(h.get("text", "")) for h in hits if h.get("text")])

    async def _save_news_active(self, ctx, items: list[dict]) -> None:
        """持久化当前新闻列表（标题/来源），供深调研「深挖第N条」桥接定位（best-effort）。"""
        try:
            await ctx.save_shared_state(NEWS_ACTIVE, {
                "items": [{"title": it.get("title", ""), "source": it.get("source", "")}
                          for it in items[:12]],
            })
        except Exception as e:
            logger.debug("save news_active skipped: %s", e)
