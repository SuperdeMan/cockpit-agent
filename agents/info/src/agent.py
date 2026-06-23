"""信息 Agent（info）。实时天气 + 天气预报 + 预警 + 生活指数 + 联网搜索 + 新闻 + 股票。

Phase 1：使用 Provider 适配层（mock/real 可切换）。真实 provider 抖动时降级到 mock，
保证链路不阻断；失败本身由 provider span(outcome=error) 记录，便于在 Dashboard 发现。
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta
from .providers import (
    build_weather_provider, build_search_provider,
    build_news_provider, build_stock_provider, build_sports_provider,
    build_extractor,
)
from .providers.mock import MockNewsProvider
from .providers.amap_geocoder import build_location_resolver

logger = logging.getLogger("agent.info")

_LIST_MARKER = re.compile(r"(?m)^\s*(?:[-*•]|(?:\d+|[一二三四五六七八九十]+)[.、)）])\s*")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


def _shanghai_now() -> datetime:
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except ZoneInfoNotFoundError:
        return datetime.now(timezone(timedelta(hours=8), name="Asia/Shanghai"))


_RECENCY_NOW = ("今天", "今日", "今晚", "现在", "此刻", "实时", "刚刚", "最新", "目前", "当前")
_RECENCY_WEEK = ("本周", "这周", "近期", "最近", "这几天", "这两天", "近几天")
_NEWS_WORDS = ("新闻", "资讯", "头条", "热点")
# 时效敏感（榜单/排名/统计/最新…）：对支持的源启用实时抓取(livecrawl)，避免缓存快照给旧数据
_FRESH_MARKERS = ("榜", "排行", "排名", "纪录", "记录", "统计", "最新", "目前",
                  "现在", "截至", "实时", "今年", "本赛季", "射手", "积分")


def _is_fresh_sensitive(query: str) -> bool:
    return any(m in (query or "") for m in _FRESH_MARKERS)


def _plan_search(query: str) -> tuple[int, str]:
    """规划检索参数：返回 (recency_days, category)。

    取代旧的关键词拼接（``_fresh_search_query``）——Exa 的 neural 检索对自然语言友好，
    不需要把日期/「当日赛程」硬塞进查询串；真正需要的是**时效窗口**让实时类查询
    不混入历史资料，以及新闻类的 category 提示。recency_days=0 表示不限时效。
    """
    if any(w in query for w in _RECENCY_NOW):
        recency_days = 2          # 留一点时区/发布滞后缓冲
    elif any(w in query for w in _RECENCY_WEEK):
        recency_days = 7
    else:
        recency_days = 0
    category = "news" if any(w in query for w in _NEWS_WORDS) else ""
    return recency_days, category


# 赛事联赛映射（api-football league id，已核验官方 ID 表；id=4 各源说法冲突故不收录）
_LEAGUES: dict[str, tuple[int, str]] = {
    "世界杯": (1, "FIFA 世界杯"),
    "欧冠": (2, "欧冠联赛"),
    "欧联": (3, "欧联杯"), "欧罗巴": (3, "欧联杯"),
    "英超": (39, "英超"),
    "西甲": (140, "西甲"),
    "意甲": (135, "意甲"),
    "德甲": (78, "德甲"),
    "法甲": (61, "法甲"),
    "荷甲": (88, "荷甲"),
}
_SPORTS_HINT = ("赛程", "赛果", "比分", "比赛", "战报", "对阵", "结果", "踢")

# 追问某具体场次的进球/详情（→ 进球详情）；与"列全部"的列表诉求区分
_DETAIL_HINT = ("进球", "谁进", "射手", "得分", "详细", "赛况", "详情", "战报",
                "经过", "具体", "怎么样", "怎样", "集锦", "介绍", "讲讲", "说说")
_LIST_HINT = ("全部", "所有", "有哪些", "哪些比赛", "哪些场", "赛程", "列表",
              "几场", "都有", "还有")
# 射手榜（联赛级排行，非某场）—— 独立于赛程列表与单场进球详情
_SCORERS_HINT = ("射手榜", "射手", "金靴", "得分王", "进球榜", "神射手",
                 "谁进球最多", "谁球最多", "topscorer", "top scorer")
# 历史/累计「总」射手榜：按赛季的 topscorers API 给不了 → 走通用搜索（接地合成历史榜）
_ALLTIME_HINT = ("总射手", "历史射手", "历届", "历史总", "累计", "史上",
                 "历史最佳", "历史进球", "all-time", "总进球", "历史榜")
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_ORDINAL_RE = re.compile(r"第\s*(\d+|[一二两三四五六七八九十]+)\s*场")


def _detect_league(query: str) -> tuple[int, str]:
    """识别查询中的赛事；返回 (league_id, 中文名)，未命中返回 (0, "")。"""
    for kw, (lid, name) in _LEAGUES.items():
        if kw in query:
            return lid, name
    return 0, ""


def _ordinal_index(text: str, n: int) -> int | None:
    """从『第N场/首场/最后一场』解析 0-based 索引（按列表顺序）。无序号返回 None。"""
    t = text or ""
    if any(w in t for w in ("最后一场", "末场", "最后那场", "最后一个")):
        return n - 1
    if any(w in t for w in ("首场", "头一场", "头场")):
        return 0
    m = _ORDINAL_RE.search(t)
    if m:
        s = m.group(1)
        num = int(s) if s.isdigit() else _CN_NUM.get(s, 0)
        if num >= 1:
            return num - 1
    return None


def _sports_date(query: str, now: datetime) -> str:
    """从查询推断目标日期，默认今天（YYYY-MM-DD，上海时区）。"""
    if "明天" in query:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    if "昨天" in query:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def _season_candidates(league_id: int, now: datetime) -> list[int]:
    """射手榜按赛季优先级试取（首个有数据的赛季胜出）。

    免费档常挡当前赛季 → 回退到最近可用赛季并标注。世界杯每 4 年（2022/2026…），
    其它联赛按足球赛季年（下半年开赛算当年）。
    """
    y = now.year
    if league_id == 1:                      # 世界杯：year ≡ 2 (mod 4)
        m = y % 4
        nearest = y - (m - 2 if m >= 2 else m + 2)
        cands = [nearest, 2022]
    else:
        primary = y if now.month >= 7 else y - 1
        cands = [primary, primary - 1, 2024, 2022]
    seen, out = set(), []
    for s in cands:
        if s >= 2018 and s not in seen:
            seen.add(s)
            out.append(s)
    return out[:4]


def _is_coordinate_label(value: str) -> bool:
    """防止 mock/异常上游把 ``lng,lat`` 直接展示给用户。"""
    try:
        lng, lat = str(value).split(",", 1)
        float(lng)
        float(lat)
        return True
    except (TypeError, ValueError):
        return False


class InfoAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.weather = build_weather_provider()
        self.search = build_search_provider()
        self.news = build_news_provider()
        self.stock = build_stock_provider()
        self.sports = build_sports_provider()
        self.extractor = build_extractor()  # 正文补抓（AnySearch extract，可为 None）
        self.location_resolver = build_location_resolver()
        # 东方财富实时行情（免费无 key，全市场）：Tushare 无港美股权限时的降级
        try:
            from .providers.stock_eastmoney import EastMoneyStockProvider
            self._stock_eastmoney = EastMoneyStockProvider()
        except Exception:
            self._stock_eastmoney = None
        self._fallback_news = MockNewsProvider()  # 新闻 provider 失败时的离线兜底

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "info.weather": self._weather,
            "info.forecast": self._forecast,
            "info.alerts": self._alerts,
            "info.indices": self._indices,
            "info.air_quality": self._air_quality,
            "info.search": self._search,
            "info.sports": self._sports,
            "info.news": self._news,
            "info.stock": self._stock,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="抱歉，这个信息查询我还不会处理。")

    # ── 天气相关 ──────────────────────────────────────────────

    async def _resolve_city(self, intent, ctx, meta: dict | None = None) -> str:
        """从 intent slots 或浏览器定位解析城市名。空串表示无法解析。"""
        city = (intent.slots.get("city") or "").strip()
        current = current_location_from_meta(meta)
        if not city and current:
            # 和风 GeoAPI 接受 ``lng,lat``，再由 Provider 解析为规范城市与空气接口坐标。
            city = f"{current.lng:.6f},{current.lat:.6f}"
        # 不再使用 vehicle.location 的 mock 默认值
        # 如果没有定位且没有指定城市，返回空串，让调用者返回 NEED_SLOT
        return city

    async def _display_city(self, intent, city: str, meta: dict | None = None) -> str:
        """坐标仅用于请求上游；展示时优先用高德反查出的可读地址。"""
        explicit_city = (intent.slots.get("city") or "").strip()
        if explicit_city:
            return explicit_city
        current = current_location_from_meta(meta)
        if current:
            try:
                return await self.location_resolver.reverse(current.lng, current.lat, meta)
            except ProviderError as e:
                logger.warning("weather reverse geocode unavailable: %s", e)
                return ""
        return city

    @staticmethod
    def _location_accuracy_note(meta: dict | None = None) -> str:
        """定位精度较差时附加提示，引导用户手动指定城市。"""
        try:
            accuracy_m = float((meta or {}).get("current_accuracy_m", ""))
        except (TypeError, ValueError):
            return ""
        if accuracy_m > 5000:
            return "（定位精度较低，如不准确请直接告诉我城市名）"
        return ""

    async def _weather(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx, meta)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的天气？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        try:
            overview = await self.weather.overview(city, meta=meta)
        except ProviderError as e:
            # 真实 provider 失败不再 fallback mock 假数据（无效城市/服务抖动会编出"当前未知的小雨"）。
            logger.warning("weather query failed: %s", e)
            asked = (intent.slots.get("city") or "").strip() or "该地点"
            return AgentResult(status=FAILED,
                               speech=f"没查到「{asked}」的天气，可能是城市名不准确或天气服务暂时不可用，"
                                      f"换个城市名或稍后再试。")

        w = overview.now
        display_city = await self._display_city(intent, city, meta)
        provider_city = "" if _is_coordinate_label(w.city) else w.city
        name = display_city or provider_city or "当前位置"
        accuracy_note = self._location_accuracy_note(meta)
        parts = [f"{name}当前{w.text or '天气'}"]
        if w.temp:
            parts.append(f"，气温{w.temp}℃")
        if w.feels_like:
            parts.append(f"，体感{w.feels_like}℃")
        if w.wind_dir:
            parts.append(f"，{w.wind_dir}{w.wind_scale}级" if w.wind_scale else f"，{w.wind_dir}")
        if accuracy_note:
            parts.append(accuracy_note)
        speech = "".join(parts) + "。"

        forecast = [
            {"date": d.date, "text_day": d.text_day, "text_night": d.text_night,
             "temp_high": d.temp_high, "temp_low": d.temp_low,
             "wind_dir": d.wind_dir, "wind_scale": d.wind_scale,
             "humidity": d.humidity, "precip": d.precip, "uv_index": d.uv_index,
             "sunrise": d.sunrise, "sunset": d.sunset}
            for d in overview.forecast
        ]
        air_quality = {
            "aqi": overview.air_quality.aqi,
            "category": overview.air_quality.category,
            "pm2p5": overview.air_quality.pm2p5,
            "primary_pollutant": overview.air_quality.primary_pollutant,
        }
        indices = [
            {"name": idx.name, "level": idx.level, "text": idx.text}
            for idx in overview.indices[:3]
        ]
        alerts = [
            {"title": alert.title, "level": alert.level, "type": alert.type_name,
             "text": alert.text, "pub_time": alert.pub_time}
            for alert in overview.alerts
        ]
        card = {
            "type": "weather", "city": name, "temp": w.temp, "text": w.text,
            "feels_like": w.feels_like, "humidity": w.humidity,
            "wind_dir": w.wind_dir, "wind_scale": w.wind_scale,
            "precip": w.precip, "pressure": w.pressure, "visibility": w.visibility,
            "cloud": w.cloud, "dew_point": w.dew_point, "update_time": w.update_time,
            "forecast": forecast, "air_quality": air_quality,
            "indices": indices, "alerts": alerts,
            "alerts_available": overview.alerts_available,
        }
        return AgentResult(speech=speech, ui_card=card, data={"weather": card})

    async def _forecast(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx, meta)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的天气预报？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        display_city = await self._display_city(intent, city, meta)
        name = display_city or ("当前位置" if current_location_from_meta(meta) else city)
        days = int(intent.slots.get("days", 3) or 3)
        try:
            forecast = await self.weather.forecast(city, days=days, meta=meta)
        except ProviderError as e:
            logger.warning("forecast failed: %s", e)
            asked = (intent.slots.get("city") or "").strip() or "该地点"
            return AgentResult(status=FAILED,
                               speech=f"没查到「{asked}」的天气预报，可能是城市名不准确或服务暂时不可用，请稍后再试。")

        if not forecast:
            return AgentResult(speech=f"暂无{name}的天气预报数据。")

        parts = [f"{name}未来{len(forecast)}天天气预报："]
        for d in forecast:
            day_str = d.date[-5:] if len(d.date) >= 5 else d.date  # MM-DD
            parts.append(f"{day_str} {d.text_day}转{d.text_night}，"
                         f"{d.temp_low}~{d.temp_high}℃")
        speech = "；".join(parts) + "。"

        items = [{"date": d.date, "text_day": d.text_day, "text_night": d.text_night,
                  "temp_high": d.temp_high, "temp_low": d.temp_low,
                  "wind_dir": d.wind_dir, "wind_scale": d.wind_scale}
                 for d in forecast]
        card = {"type": "forecast", "city": name, "days": items}
        return AgentResult(speech=speech, ui_card=card, data={"forecast": items})

    async def _alerts(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx, meta)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的天气预警？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        name = await self._display_city(intent, city, meta) or ("当前位置" if current_location_from_meta(meta) else city)
        try:
            alerts = await self.weather.alerts(city, meta=meta)
        except ProviderError as e:
            # 不可 fallback mock（会谎报"无预警"，预警是安全信息）。
            logger.warning("alerts failed: %s", e)
            asked = (intent.slots.get("city") or "").strip() or "该地点"
            return AgentResult(status=FAILED,
                               speech=f"暂时无法获取「{asked}」的天气预警，请稍后再试。")

        if not alerts:
            return AgentResult(speech=f"{name}当前没有生效的天气预警。",
                               data={"alerts": []})

        parts = [f"{name}当前有{len(alerts)}条天气预警："]
        for a in alerts:
            parts.append(f"{a.title}（{a.level}级）")
        speech = "；".join(parts) + "。请注意防范。"

        items = [{"title": a.title, "level": a.level, "type": a.type_name,
                  "text": a.text, "pub_time": a.pub_time} for a in alerts]
        card = {"type": "weather_alerts", "city": name, "items": items}
        return AgentResult(speech=speech, ui_card=card, data={"alerts": items})

    async def _indices(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx, meta)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的生活指数？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        try:
            indices = await self.weather.indices(city, meta=meta)
        except ProviderError as e:
            logger.warning("indices failed: %s", e)
            asked = (intent.slots.get("city") or "").strip() or "该地点"
            return AgentResult(status=FAILED,
                               speech=f"暂时无法获取「{asked}」的生活指数，请稍后再试。")

        if not indices:
            return AgentResult(speech=f"暂无{city}的生活指数数据。")

        parts = [f"{city}生活指数："]
        for idx in indices:
            parts.append(f"{idx.name} {idx.level}——{idx.text}")
        speech = "，".join(parts) + "。"

        items = [{"category": idx.category, "name": idx.name,
                  "level": idx.level, "text": idx.text} for idx in indices]
        card = {"type": "life_indices", "city": city, "items": items}
        return AgentResult(speech=speech, ui_card=card, data={"indices": items})

    async def _air_quality(self, intent, ctx, meta) -> AgentResult:
        city = await self._resolve_city(intent, ctx, meta)
        if not city:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个城市的空气质量？",
                               follow_up="请告诉我城市名", missing_slots=["city"])
        try:
            aq = await self.weather.air_quality(city, meta=meta)
        except ProviderError as e:
            logger.warning("air_quality failed: %s", e)
            asked = (intent.slots.get("city") or "").strip() or "该地点"
            return AgentResult(status=FAILED,
                               speech=f"暂时无法获取「{asked}」的空气质量，请稍后再试。")

        parts = [f"{city}空气质量{aq.category or '未知'}"]
        if aq.aqi:
            parts.append(f"，AQI {aq.aqi}")
        if aq.pm2p5:
            parts.append(f"，PM2.5 {aq.pm2p5}μg/m³")
        if aq.primary_pollutant:
            parts.append(f"，首要污染物{aq.primary_pollutant}")
        speech = "".join(parts) + "。"

        card = {"type": "air_quality", "city": city, "aqi": aq.aqi,
                "category": aq.category, "pm2p5": aq.pm2p5, "pm10": aq.pm10,
                "primary_pollutant": aq.primary_pollutant,
                "no2": aq.no2, "o3": aq.o3, "co": aq.co, "so2": aq.so2,
                "update_time": aq.update_time}
        return AgentResult(speech=speech, ui_card=card, data={"air_quality": card})

    # ── 联网搜索 ──────────────────────────────────────────────

    @staticmethod
    def _clean_snippet(text: str) -> str:
        """清理搜索结果的 snippet，去掉省略号和多余空白。"""
        if not text:
            return ""
        # 去掉末尾的省略号
        text = re.sub(r'[.。…]{2,}$', '', text.strip())
        # 去掉中间的省略号（保留语义）
        text = text.replace(' ... ', '，').replace('…', '，')
        return text.strip()

    @staticmethod
    def _latest_published(results) -> str:
        """取最新发布时间（ISO 字符串可按字典序比较），供卡片时效展示。"""
        dates = [r.published for r in results if getattr(r, "published", "")]
        return max(dates) if dates else ""

    @staticmethod
    def _fallback_brief(query: str, sources: list[dict]) -> str:
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

    @staticmethod
    def _parse_synth(raw: str) -> dict | None:
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
        # 非 JSON：剥离列表编号，合并为连续文本作为答案
        flat = _LIST_MARKER.sub("", text)
        flat = " ".join(line.strip() for line in flat.splitlines() if line.strip())
        if flat:
            return {"answer": flat, "key_points": [], "confidence": "medium",
                    "used_sources": []}
        return None

    async def _enrich_empty_content(self, sources: list[dict], meta) -> None:
        """Exa 偶有结果正文为空时，用 AnySearch extract 补抓正文（best-effort）。

        仅补前 3 条且原正文为空者，单条失败静默跳过——绝不阻断主链/不引入编造。
        """
        if not self.extractor:
            return
        for s in sources[:3]:
            if s.get("content") or not s.get("url"):
                continue
            try:
                text = await self.extractor.extract(s["url"], meta=meta)
                if text:
                    s["content"] = text[:1500]
            except (ProviderError, Exception) as e:
                logger.debug("extract enrich skipped: %s", e)

    async def _synthesize_grounded(self, subject: str,
                                   sources: list[dict]) -> dict | None:
        """基于正文级资料接地合成。返回 {answer,key_points,confidence,used_sources}
        或 None（LLM 不可用，调用方走诚实兜底）。

        与旧 ``_summarize_sources`` 的本质区别：喂正文而非 snippet；要求**无依据即弃权**
        而不是「先把已知信息告诉用户」，从根上消除编造（修 R1/R2）。
        """
        # 控制 prompt 体量：限 5 源、每源正文截 1000 字符——过大 prompt 会使上游
        # LLM 推理超时（实测 5×1800 字符触发 DEADLINE_EXCEEDED 退化为 snippet 拼接）。
        used = sources[:5]
        blocks = []
        for i, s in enumerate(used):
            # 榜单/表格常在正文较深处：给最权威的首条更多正文配额，其余收紧，控总量防超时
            cap = 2400 if i == 0 else 900
            body = (s.get("content") or s.get("snippet") or "").strip()[:cap]
            head = f"[{s['idx']}] {s['title']}（来源：{s['source']}"
            if s.get("published"):
                head += f"，发布：{s['published']}"
            head += "）"
            blocks.append(f"{head}\n{body}")
        materials = "\n\n".join(blocks)
        prompt = (
            f"用户问题：{subject}\n"
            f"当前时间：{_shanghai_now():%Y年%m月%d日 %H:%M}（Asia/Shanghai）\n\n"
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
            # timeout 20s：比默认 10s 宽（大 prompt 需要），又收敛体感卡顿；裁剪后通常 5~10s 完成。
            raw = await self.llm.complete([
                {"role": "system", "content":
                 "你是严谨的车载信息编辑，只能依据提供的资料作答，宁可说没有也绝不编造。"},
                {"role": "user", "content": prompt},
            ], temperature=0.2, max_tokens=600, timeout=20)
        except Exception as e:
            logger.warning("grounded synthesis failed: %s", e)
            return None
        raw = (raw or "").strip()
        if not raw or raw.startswith("[mock]"):
            return None
        return self._parse_synth(raw)

    async def _search(self, intent, ctx, meta) -> AgentResult:
        query = (intent.slots.get("query") or "").strip()
        if not query:
            return AgentResult(status=NEED_SLOT, speech="您想搜什么？",
                               follow_up="请告诉我搜索内容", missing_slots=["query"])
        # 赛事路由：命中已知赛事 + 赛事意图词 → 走结构化数据源，不进通用搜索（杜绝编造比分）
        sports = await self._maybe_sports(query, meta, intent.raw_text)
        if sports is not None:
            return sports
        _broad = any(w in query for w in ("全部", "所有", "每场", "比分", "赛果", "结果"))
        limit = int(intent.slots.get("limit", 6 if _broad else 5) or (6 if _broad else 5))
        recency_days, category = _plan_search(query)
        # 时效敏感（榜单/排名/统计…）→ 让 Exa 抓实时页面，避免缓存快照给旧数据
        livecrawl = "preferred" if _is_fresh_sensitive(query) else ""
        try:
            results = await self.search.search(
                query, limit=limit, meta=meta,
                recency_days=recency_days, category=category, livecrawl=livecrawl)
        except ProviderError as e:
            logger.warning("search failed: %s", e)
            return AgentResult(
                status=FAILED,
                speech="联网检索暂时不可用，无法确认最新结果，请稍后再试。",
            )

        if not results:
            return AgentResult(speech=f"没有找到关于「{query}」的搜索结果。")

        sources = [{"idx": i + 1, "title": r.title, "url": r.url, "source": r.source,
                    "published": r.published, "content": r.content,
                    "snippet": self._clean_snippet(r.snippet)}
                   for i, r in enumerate(results)]
        await self._enrich_empty_content(sources, meta)
        synth = await self._synthesize_grounded(query, sources)
        if synth:
            speech, confidence = synth["answer"], synth["confidence"]
        else:
            speech, confidence = self._fallback_brief(query, sources), "low"

        # search_result：气泡给结论，卡片只给证据（来源/时效/置信度）——不放结论文本，
        # 也不放 key_points（要点与气泡结论重复，用户反馈像"又一个总结"）。
        card = {
            "type": "search_result",
            "query": query,
            "sources": [{"title": r.title, "url": r.url, "source": r.source,
                         "published": r.published} for r in results],
            "freshness": self._latest_published(results),
            "confidence": confidence,
        }
        return AgentResult(speech=speech, ui_card=card,
                           data={"sources": card["sources"]})

    # ── 赛事 ─────────────────────────────────────────────────

    @staticmethod
    def _fixture_dict(f) -> dict:
        scored = f.status in ("finished", "live") and f.home_goals != ""
        return {
            "league": f.league, "round": f.round,
            "home": f.home, "away": f.away,
            "home_logo": f.home_logo, "away_logo": f.away_logo,
            "score": f"{f.home_goals}-{f.away_goals}" if scored else "",
            "home_goals": f.home_goals, "away_goals": f.away_goals,
            "status": f.status, "status_text": f.status_text,
            "elapsed": f.elapsed, "kickoff": f.kickoff,
        }

    async def _maybe_sports(self, query: str, meta, raw_text: str = "") -> AgentResult | None:
        """命中「已知赛事 + 赛事意图词」才路由到结构化数据源；否则返回 None 走通用搜索。

        组合 ``query``（planner 解析后的槽位，跟进句靠它带回「世界杯」）与 ``raw_text``
        （完整原话，带回「明天/昨天」等时间词）一起识别——单用任一个都会漏：
        跟进句「明天的呢」raw_text 无赛事名、slots.query 又可能丢时间词（实测 bug）。
        """
        text = f"{query} {raw_text}".strip()
        league_id, name = _detect_league(text)
        if not league_id or not any(h in text for h in _SPORTS_HINT):
            return None
        return await self._do_sports(text, league_id, name, meta)

    async def _do_sports(self, query: str, league_id: int, league_name: str,
                         meta) -> AgentResult | None:
        """拉取并组织赛事。Provider 报错返回 None（回落通用搜索/诚实弃权）。"""
        # 射手榜是联赛级排行（非某场/赛程）→ 优先于赛程列表，避免"问射手榜答赛程"
        if self._is_scorers_request(query):
            return await self._top_scorers(league_id, league_name, meta)

        now = _shanghai_now()
        date = _sports_date(query, now)
        try:
            # 按日期查全联赛、再客户端按 league_id 精确过滤：
            # date+league+season 在 api-football 免费档常被「赛季门限」挡（2026 季不开放），
            # 而单日期查询（今天±1 窗口）免费档放行，付费档同样适用——故统一走日期查。
            all_fixtures = await self.sports.fixtures(date=date, meta=meta)
        except ProviderError as e:
            logger.warning("sports fixtures failed: %s", e)
            return None
        fixtures = [f for f in all_fixtures if f.league_id == league_id]

        date_label = "今天" if date == now.strftime("%Y-%m-%d") else date[5:]
        title = f"{league_name} · {date_label}"
        freshness = now.isoformat(timespec="minutes")
        if not fixtures:
            return AgentResult(
                speech=f"{date_label}没有查询到{league_name}的比赛安排。",
                ui_card={"type": "sports_scores", "title": title, "fixtures": [],
                         "freshness": freshness, "source": "api-football"},
                data={"fixtures": []})

        # 追问某具体场次（第N场/队名）且非"列全部"诉求 → 进球详情（射手/分钟）
        picked = self._pick_fixture(query, fixtures)
        if picked is not None and not self._is_list_request(query):
            return await self._match_detail(picked, league_name, meta)

        finished = [f for f in fixtures if f.status == "finished"]
        live = [f for f in fixtures if f.status == "live"]
        scheduled = [f for f in fixtures if f.status == "scheduled"]
        parts = [f"{date_label}{league_name}共{len(fixtures)}场比赛"]
        if finished:
            scores = "、".join(
                f"{f.home} {f.home_goals}-{f.away_goals} {f.away}" for f in finished[:6])
            parts.append(f"已结束{len(finished)}场：{scores}")
        if live:
            ls = "、".join(
                f"{f.home} {f.home_goals}-{f.away_goals} {f.away}"
                f"（{f.status_text}{f.elapsed + '′' if f.elapsed else ''}）"
                for f in live[:4])
            parts.append(f"进行中{len(live)}场：{ls}")
        if scheduled:
            parts.append(f"未开赛{len(scheduled)}场")
        speech = "，".join(parts) + "。"

        card = {"type": "sports_scores", "title": title,
                "fixtures": [self._fixture_dict(f) for f in fixtures],
                "freshness": freshness, "source": "api-football"}
        return AgentResult(speech=speech, ui_card=card,
                           data={"fixtures": card["fixtures"]})

    @staticmethod
    def _is_detail_request(text: str) -> bool:
        return any(w in (text or "") for w in _DETAIL_HINT)

    @staticmethod
    def _is_list_request(text: str) -> bool:
        return any(w in (text or "") for w in _LIST_HINT)

    @staticmethod
    def _is_scorers_request(text: str) -> bool:
        return any(w in (text or "") for w in _SCORERS_HINT)

    @staticmethod
    def _is_alltime_scorers(text: str) -> bool:
        """是否问的是「历史/累计总射手榜」（赛季 API 给不了，走通用搜索）。"""
        return any(w in (text or "") for w in _ALLTIME_HINT)

    @staticmethod
    def _pick_fixture(text: str, fixtures: list):
        """把『第N场/某队』指代解析到具体某场。无法定位返回 None。"""
        if not fixtures:
            return None
        idx = _ordinal_index(text, len(fixtures))
        if idx is not None and 0 <= idx < len(fixtures):
            return fixtures[idx]
        for f in fixtures:                       # 队名（中文）命中
            if (f.home and f.home in text) or (f.away and f.away in text):
                return f
        return None

    async def _league_from_history(self, ctx) -> tuple[int, str]:
        """赛事追问槽位常不带联赛名 → 从最近对话回填（最近一轮优先）。"""
        try:
            turns = await ctx.history(6)
        except Exception as e:
            logger.debug("sports history fetch failed: %s", e)
            return 0, ""
        for t in reversed(turns or []):
            lid, name = _detect_league(t.get("text") or "")
            if lid:
                return lid, name
        return 0, ""

    async def _match_detail(self, f, league_name: str, meta) -> AgentResult:
        """某场进球详情：射手 + 分钟（结构化真实数据，不编造）。"""
        try:
            events = await self.sports.events(f.fixture_id, meta=meta)
        except ProviderError as e:
            logger.warning("sports events failed: %s", e)
            events = []
        goals = []
        for e in events:
            side = ("home" if e.team_id == f.home_id
                    else "away" if e.team_id == f.away_id else "")
            goals.append({"minute": e.minute, "team": side,
                          "player": e.player, "detail": e.detail})

        scored = f.home_goals != "" and f.away_goals != ""
        head = (f"{league_name}，{f.home} {f.home_goals}-{f.away_goals} {f.away}"
                if scored else f"{league_name}，{f.home} 对阵 {f.away}")
        status = f.status_text + (f"{f.elapsed}′" if f.status == "live" and f.elapsed else "")
        if status:
            head += f"（{status}）"

        if goals:
            segs = []
            for g in goals:
                team = (f.home if g["team"] == "home"
                        else f.away if g["team"] == "away" else "")
                who = g["player"] or "球员"
                tag = "" if g["detail"] == "进球" else g["detail"]
                note = "".join(x for x in (team, tag) if x)
                segs.append(f"第{g['minute']}分钟{who}" + (f"（{note}）" if note else ""))
            speech = head + "。进球：" + "；".join(segs) + "。"
        elif not scored:
            speech = head + "，比赛尚未开始。"
        elif f.home_goals == "0" and f.away_goals == "0":
            speech = head + "，目前还没有进球。"
        else:
            speech = head + "。暂未获取到进球详情。"

        fd = self._fixture_dict(f)
        fd["goals"] = goals
        card = {"type": "sports_scores",
                "title": f"{league_name} · {f.home} vs {f.away}",
                "fixtures": [fd],
                "freshness": _shanghai_now().isoformat(timespec="minutes"),
                "source": "api-football"}
        return AgentResult(speech=speech, ui_card=card,
                           data={"fixtures": [fd], "goals": goals})

    async def _top_scorers(self, league_id: int, league_name: str, meta) -> AgentResult:
        """联赛射手榜。按赛季优先级试取，首个有数据的赛季胜出并标注（免费档常挡本届）。"""
        scorers, used_season = [], 0
        for season in _season_candidates(league_id, _shanghai_now()):
            try:
                scorers = await self.sports.top_scorers(league_id, season, meta=meta)
            except ProviderError as e:
                logger.warning("topscorers season %s failed: %s", season, e)
                continue
            if scorers:
                used_season = season
                break
        if not scorers:
            return AgentResult(
                status=FAILED,
                speech=f"暂时获取不到{league_name}的射手榜，可能是数据源限制，请稍后再试。")

        label = f"{used_season}赛季"
        top3 = "、".join(f"{s.player} {s.goals}球（{s.team}）" for s in scorers[:3])
        speech = f"{league_name}（{label}）射手榜：{top3}。"
        card = {"type": "sports_scorers",
                "title": f"{league_name} 射手榜", "season": label,
                "scorers": [{"rank": s.rank, "player": s.player,
                             "team": s.team, "goals": s.goals} for s in scorers[:10]],
                "freshness": _shanghai_now().isoformat(timespec="minutes"),
                "source": "api-football"}
        return AgentResult(speech=speech, ui_card=card, data={"scorers": card["scorers"]})

    async def _sports(self, intent, ctx, meta) -> AgentResult:
        """info.sports 意图入口。识别赛事后取结构化数据；未识别则回落通用搜索。"""
        query = (intent.slots.get("query") or intent.slots.get("league")
                 or intent.slots.get("topic") or "").strip()
        text = f"{query} {intent.raw_text or ''}".strip()
        if not text:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个赛事的比分或赛程？",
                               follow_up="请告诉我赛事名称", missing_slots=["query"])
        league_id, name = _detect_league(text)
        if not league_id:
            # 赛事追问（如"第一场谁进球"）槽位常不带联赛名 → 从对话历史回填联赛上下文
            follow_up = (self._is_detail_request(text)
                         or self._is_scorers_request(text)
                         or _ordinal_index(text, 1) is not None
                         or any(h in text for h in _SPORTS_HINT))
            if follow_up:
                league_id, name = await self._league_from_history(ctx)
            if league_id:
                text = f"{name} {text}"   # 并入联赛名，供 _pick_fixture/日期识别
        if not league_id:
            # 未识别赛事 → 用通用搜索兜底（接地合成，仍不会编造）
            return await self._search(intent, ctx, meta)
        # 历史/总射手榜：按赛季的 topscorers 给不了累计历史榜 → 通用搜索（接地合成真实历史榜）。
        # 改写 query 为明确的「历史总射手榜」，否则 _search 只拿 query 槽位（可能仅"世界杯"）搜不准。
        if self._is_scorers_request(text) and self._is_alltime_scorers(text):
            intent.slots["query"] = f"{name}历史总射手榜"
            return await self._search(intent, ctx, meta)
        res = await self._do_sports(text, league_id, name, meta)
        if res is None:
            return AgentResult(status=FAILED,
                               speech="赛事数据暂时不可用，无法确认比分，请稍后再试。")
        return res

    # ── 新闻 ─────────────────────────────────────────────────

    @staticmethod
    def _is_junk_news(title: str, url: str, content: str) -> bool:
        """剔除门户首页/栏目页/错误页等非新闻条目（宽泛新闻检索偶尔会命中）。"""
        t, c = title or "", content or ""
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

    async def _gather_news(self, topic: str, limit: int, meta) -> list[dict]:
        """聚合新闻为归一化 dict 列表。链路：Exa 正文级（时效）→ serpapi(Google/Baidu)
        → AnySearch → mock。Exa 优先因其返回**全文+发布时间**，远好于 serpapi 的聚合页标题。
        多取几条以便过滤掉首页/错误页后仍够 ~10 条。
        """
        query = f"{topic} 最新进展" if topic else "今日重要新闻 头条 要闻"
        try:
            # recency 2 天：兼顾"今天"时效与凑够覆盖面（条目带日期，用户可辨新旧）
            results = await self.search.search(
                query, limit=limit + 5, meta=meta, recency_days=2, category="news")
            exa = [{"title": r.title, "url": r.url, "source": r.source,
                    "publish_time": r.published,
                    "snippet": self._clean_snippet(r.snippet or (r.content[:160] if r.content else ""))}
                   for r in results
                   if r.title and not self._is_junk_news(r.title, r.url, r.content)]
            if exa:
                return exa
        except ProviderError as e:
            logger.warning("exa news failed, falling back to news provider: %s", e)
        try:
            items = await self.news.headlines(topic=topic, limit=limit, meta=meta)
        except ProviderError as e:
            logger.warning("news failed, fallback to mock: %s", e)
            items = await self._fallback_news.headlines(topic=topic, limit=limit, meta=meta)
        return [{"title": n.title, "url": "", "source": n.source,
                 "publish_time": n.publish_time, "snippet": self._clean_snippet(n.summary)}
                for n in items if not self._is_junk_news(n.title, "", n.summary)]

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
        # 「|」在中文新闻标题里几乎总是栏目分隔符 → 取首段
        if "|" in t:
            head = t.split("|", 1)[0].strip()
            if head:
                t = head
        # 「_」「 - 」常是来源尾巴；主标题足够长才切，避免误伤正文里的下划线/连字符
        for sep in ("_", " - ", " – "):
            idx = t.find(sep)
            if idx >= 4:
                t = t[:idx].strip()
                break
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

    async def _summarize_news_list(self, subject: str,
                                   items: list[dict]) -> tuple[str, dict[int, str]]:
        """一次 LLM 调用产出：总体概述 + 逐条一句话摘要（按编号）。
        返回 (overview, {idx: summary})；失败返回 ("", {}) 由调用方用首句兜底。
        """
        blocks = [f"[{i}] {n['title']}\n{(n.get('snippet') or '')[:400]}"
                  for i, n in enumerate(items, 1)]
        prompt = (
            f"用户想看：{subject}（今日新闻速览）\n"
            f"当前时间：{_shanghai_now():%Y年%m月%d日}\n\n"
            f"以下是 {len(items)} 条新闻（方括号内为编号）：\n" + "\n\n".join(blocks) + "\n\n"
            "只依据各条内容输出一个 JSON：\n"
            '{"overview": "一句话总体概述（≤40字）", '
            '"summaries": {"1": "该条一句话摘要（≤30字）", "2": "…"}}\n'
            "每条摘要必须只依据对应编号的内容，不得编造、不得张冠李戴；只输出 JSON。"
        )
        try:
            raw = await self.llm.complete([
                {"role": "system", "content": "你是严谨的车载新闻编辑，只归纳给定内容，绝不编造。"},
                {"role": "user", "content": prompt},
            ], temperature=0.2, max_tokens=700, timeout=20)
        except Exception as e:
            logger.warning("news list summarize failed: %s", e)
            return "", {}
        raw = (raw or "").strip()
        if not raw or raw.startswith("[mock]"):
            return "", {}
        text = raw.strip().strip("`")
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return "", {}
        try:
            obj = json.loads(text[start:end + 1])
        except (ValueError, TypeError):
            return "", {}
        overview = str(obj.get("overview") or "").strip()
        summaries: dict[int, str] = {}
        for k, v in (obj.get("summaries") or {}).items():
            if str(k).isdigit() and str(v).strip():
                summaries[int(k)] = str(v).strip()
        return overview, summaries

    async def _news(self, intent, ctx, meta) -> AgentResult:
        topic = (intent.slots.get("topic") or "").strip()
        # 座舱看新闻 = 一屏扫到约 10 条带一句话摘要的列表
        limit = int(intent.slots.get("limit", 10 if not topic else 8) or (10 if not topic else 8))
        subject = topic or "今日值得关注的新闻"

        raw = self._dedup_news(await self._gather_news(topic, limit, meta))[:limit]
        if not raw:
            return AgentResult(speech="暂无新闻资讯。")

        overview, summaries = await self._summarize_news_list(subject, raw)
        lines, items = [], []
        for i, n in enumerate(raw, 1):
            one = summaries.get(i) or self._first_sentence(n.get("snippet", ""))
            lines.append(f"{i}. {one}")
            # 卡片只放可点开的来源（正文已在语音里），不复述摘要 → 不与语音重复
            items.append({"title": self._clean_title(n["title"]), "url": n.get("url", ""),
                          "source": n["source"], "publish_time": n["publish_time"]})

        # 座舱以 TTS 播报为本：语音/气泡 = 总览 + 逐条一句话提炼（听完即可，无需点开）；
        # 卡片 = 可点开的来源清单（想看原文才点）。
        head = overview or (f"关于{topic}的新闻有 {len(raw)} 条：" if topic
                            else f"今天值得关注的新闻有 {len(raw)} 条：")
        speech = head + "\n" + "\n".join(lines)
        fresh = [n["publish_time"] for n in raw
                 if n["publish_time"] and n["publish_time"] != "mock"]
        card = {"type": "news_brief", "topic": topic, "items": items,
                "freshness": max(fresh) if fresh else ""}
        return AgentResult(speech=speech, ui_card=card, data={"items": items})

    # ── 股票 ─────────────────────────────────────────────────

    async def _stock(self, intent, ctx, meta) -> AgentResult:
        symbol = (intent.slots.get("symbol") or "").strip()
        if not symbol:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪只股票或指数？",
                               follow_up="请告诉我股票名称或代码", missing_slots=["symbol"])
        stock_provider = self.stock
        try:
            q = await self.stock.quote(symbol, meta=meta)
        except ProviderError as e:
            logger.warning("tushare quote failed: %s", e)
            # Tushare 失败（如无港美股权限）→ 降级到东方财富实时行情（免费，全市场）
            if self._stock_eastmoney:
                try:
                    q = await self._stock_eastmoney.quote_text(symbol, meta=meta)
                    stock_provider = self._stock_eastmoney  # history 也用东方财富
                except ProviderError as e2:
                    logger.warning("eastmoney quote also failed: %s", e2)
                    return AgentResult(
                        status=FAILED,
                        speech=f"没有找到「{symbol}」的行情数据。可能未上市或名称不准确。"
                               f"您可以试试用代码查询，如「600519」（A股）、「00700」（港股）。",
                    )
            else:
                return AgentResult(
                    status=FAILED,
                    speech=f"没有找到「{symbol}」的行情数据。可能未上市或名称不准确。",
                )
        try:
            candles = await stock_provider.history(symbol, limit=20, meta=meta)
        except ProviderError as e:
            # 报价仍然有价值；历史失败时不混用 mock K 线误导用户。
            logger.warning("stock history unavailable, leaving chart empty: %s", e)
            candles = []

        parts = [f"{q.name or symbol}"]
        if q.price:
            parts.append(f"当前价{q.price}")
        if q.change and q.change_pct:
            direction = "跌" if q.change.startswith("-") else "涨"
            parts.append(f"，{direction}{q.change}（{q.change_pct}）")
        speech = "".join(parts) + "。"

        card = {"type": "stock_quote", "name": q.name, "symbol": q.symbol,
                "price": q.price, "change": q.change, "change_pct": q.change_pct,
                "market_time": q.market_time,
                "candles": [
                    {"date": candle.date, "open": candle.open, "high": candle.high,
                     "low": candle.low, "close": candle.close, "volume": candle.volume}
                    for candle in candles
                ]}
        return AgentResult(speech=speech, ui_card=card, data={"quote": card})
