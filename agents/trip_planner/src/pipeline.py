"""行程规划四段流水线（P0）—— LLM 提议 / 确定性落地。

把项目铁律「规划/执行分离、LLM 提议、确定性 Executor 落地」下沉到 trip-planner 内部：

  propose : LLM 只产**结构化骨架**（每天选哪些景点名/类型/停留），且**只能从参考 POI 池选名字**
            （降幻觉，对症 TravelPlanner 纯 LLM 失败模式）；解析失败 → 确定性兜底分配。
  ground  : 确定性把每个 stop 接地为真实 POI（优先池内复用坐标，否则搜索 + name_matches 校验，
            拒「挂错名的非空结果」）；接不到标 grounded=False，绝不臆造。
  solve   : 确定性算相邻 stop 车程（get_route）、按日上限顺延尾部 stop、按真实 SoC 沿路线编织充电点。
  narrate : 确定性渲染 TTS 话术 + trip_itinerary 卡。**LLM 不再产事实**。

provider 在进程内复用（navigation 的 POIProvider）——跟随 charging_planner 先例，避免每 leg 跨 gRPC。
"""
from __future__ import annotations
import json
import logging
import os
import re
from datetime import datetime

from agents._sdk.http import ProviderError
from agents._sdk.landmark import is_landmark_description, landmark_candidates, name_matches
from agents.navigation.src.providers.base import GeoPoint, POI
from agents.charging_planner.src.weave import weave_charging_targets
from .models import Trip, Day, Stop, Leg

logger = logging.getLogger("agent.trip_planner.pipeline")

# 满电续航假设（公里），与 charging 对齐同一 env。
try:
    FULL_RANGE_KM = float(os.getenv("CHARGING_FULL_RANGE_KM", "500"))
except ValueError:
    FULL_RANGE_KM = 500.0
# 单日（驾驶+游览）分钟上限，超过则把尾部 stop 顺延次日。
try:
    DAY_MAX_MIN = int(os.getenv("TRIP_DAY_MAX_MIN", "480"))
except ValueError:
    DAY_MAX_MIN = 480

# 放松节奏偏好（带老人/轻松/不累/悠闲）→ 每天更少停靠点。
_PREF_RELAXED = ("带老人", "老人", "轻松", "不累", "不要太累", "悠闲", "慢", "带娃", "带孩子", "亲子")
_DWELL_BY_TYPE = {"attraction": 120, "meal": 60, "hotel": 0, "charging": 30, "custom": 60}
_MAX_DAYS = 10
# 住宿类标记：泛地点（如"惠州海边"）搜"景点"常返回一堆民宿/酒店，剔除出景点候选池。
_LODGING_MARKERS = ("民宿", "酒店", "公寓", "别墅", "客栈", "宾馆", "旅馆", "旅店")


def per_day_count(prefs: str) -> int:
    return 2 if any(w in (prefs or "") for w in _PREF_RELAXED) else 3


# ──────────────────────────── pool ────────────────────────────

async def build_poi_pool(poi_provider, dest: str, prefs: str,
                         near, meta) -> list[POI]:
    """搜目的地候选景点/美食池（供 propose 选名字、ground 复用坐标）。去重按名。"""
    keywords = [f"{dest} 景点", f"{dest} 美食"]
    if any(w in (prefs or "") for w in ("带娃", "带孩子", "亲子")):
        keywords.append(f"{dest} 亲子乐园")
    pool: list[POI] = []
    seen: set[str] = set()
    for kw in keywords:
        try:
            results = await poi_provider.search(kw, near=near, limit=8, meta=meta)
        except ProviderError as e:
            # 铁律③（M0a 同款，此处曾漏网）：运行期真实源失败绝不回退 mock 假 POI——
            # 假景点会被写进行程并被用户导航过去。该关键词记空，池子不足由上层诚实降级。
            logger.warning("pool search '%s' failed（诚实降级，无 mock 回退）: %s", kw, e)
            results = []
        is_attraction = "景点" in kw or "乐园" in kw
        for p in results:
            nm = (p.name or "").strip()
            # 景点候选剔除住宿类（泛地点搜"景点"易把民宿/酒店当景点）
            if is_attraction and any(m in nm for m in _LODGING_MARKERS):
                continue
            if nm and nm not in seen:
                seen.add(nm)
                pool.append(p)
    return pool


# ── G4 主题检索步：主题 → LLM 提议候选地名 → 高德接地验证 → 通过者入池 ────
# 幻觉防线与 landmark_candidates 同款：LLM 只产「名字候选」，坐标/存在性由高德
# 裁决（name_matches 校验，拒挂错名的非空结果）；接不到的候选直接丢，
# 池的封闭纪律不变——只是入池来源多一路。

_THEME_SYSTEM = (
    "你了解影视/文化主题与真实地点的关联。列出与指定主题在指定城市**真实存在**的"
    "具体地点（取景地/原型地/主题相关地标/故事发生地）。\n"
    "只输出 JSON 数组（地名字符串，不带说明），最多 8 个；"
    "不确定真实存在的**不要列**，宁少勿错。没有把握时输出 []。"
)


async def build_theme_pool(llm, poi_provider, theme: str, dest: str,
                           meta) -> list[POI]:
    """主题相关候选地名经高德接地验证后入池。LLM 失败/全部接不到 → 空列表
    （普通池兜底，narrate 如实降级话术）。"""
    if not (theme and dest):
        return []
    try:
        out = await llm.complete(
            [{"role": "system", "content": _THEME_SYSTEM},
             {"role": "user", "content": f"主题：《{theme}》；城市：{dest}"}],
            temperature=0.2, max_tokens=300)
    except Exception as e:
        logger.warning("theme candidates LLM failed（普通池兜底）: %s", e)
        return []
    block = _extract_json_block_array(out)
    names: list[str] = []
    if block:
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, TypeError):
            data = []
        for item in (data if isinstance(data, list) else [])[:8]:
            nm = str(item).strip() if isinstance(item, (str, int, float)) else ""
            if nm and nm not in names:
                names.append(nm)
    pool: list[POI] = []
    for nm in names:
        poi = await _ground_theme_name(poi_provider, nm, dest, meta)
        if poi is not None:
            pool.append(poi)
    if names:
        logger.info("theme pool《%s》: %d/%d candidates grounded",
                    theme, len(pool), len(names))
    return pool


async def _ground_theme_name(poi_provider, name: str, dest: str, meta) -> POI | None:
    """「{城市}{名}」优先（城市约束——「鼓楼」类多义名不带城市必然接错），
    裸名兜底；两跳都过 name_matches 才收。"""
    for kw in (f"{dest}{name}", name):
        try:
            results = await poi_provider.search(kw, near=None, limit=1, meta=meta)
        except ProviderError as e:
            logger.warning("theme ground '%s' failed（诚实丢弃，无 mock 回退）: %s",
                           kw, e)
            continue
        if results and name_matches(name, results[0].name):
            return results[0]
    return None


def _extract_json_block_array(text: str) -> str:
    """从 LLM 输出抠第一个 [...] JSON 数组（容忍 ```json 包裹与前后噪声）。"""
    if not text:
        return ""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t).rstrip("` \n")
    start = t.find("[")
    end = t.rfind("]")
    return t[start:end + 1] if start != -1 and end > start else ""


def theme_hint(theme: str, theme_names: list[str]) -> str:
    """主题池名字 → propose 的 LLM 提示（与 _weather_hint 同款织入）。空则 ''。"""
    if not (theme and theme_names):
        return ""
    return (f"\n【主题】本次行程按《{theme}》主题编排。可选景点里这些与主题直接相关，"
            f"请优先排入并合理串联：{'、'.join(theme_names[:8])}。"
            "其余天数/空位用普通景点补齐。")


# ─────────────────────────── propose ───────────────────────────

_PROPOSE_SYSTEM = (
    "你是自驾行程规划助手。只能从【可选景点】列表里挑选景点名，**不得编造列表以外的地点**。\n"
    "按天输出 JSON（且只输出 JSON，无多余文字）：\n"
    '{"days":[{"day_index":1,"theme":"主题","stops":[{"name":"列表里的名字","type":"attraction"}]}]}\n'
    "type 取 attraction|meal|hotel。每天 2-4 个停靠点，节奏按偏好（带老人/轻松→更少更慢）。"
)


def _extract_json_block(text: str) -> str:
    """从 LLM 输出里抠出第一个 {...} JSON 块（容忍 ```json 包裹与前后噪声）。"""
    if not text:
        return ""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t).rstrip("` \n")
    start = t.find("{")
    end = t.rfind("}")
    return t[start:end + 1] if start != -1 and end > start else ""


def _norm_days(days: str) -> int:
    try:
        n = int(re.sub(r"[^0-9]", "", str(days)) or 0)
    except ValueError:
        n = 0
    return n


# ── 天气联动（#3 能力增强）：目的地多日预报 → 织进 propose(雨天优先室内) + 每天卡片标注 ──

def _start_offset(raw_text: str, now: datetime) -> int:
    """从原话推断行程起始日相对今天的偏移（对齐预报窗口）。默认 0=今天/最近可用。"""
    t = raw_text or ""
    if "大后天" in t:
        return 3
    if "后天" in t:
        return 2
    if "明天" in t:
        return 1
    wd = now.weekday()  # 周一=0 … 周日=6
    nextweek = "下周" in t or "下星期" in t or "下礼拜" in t
    if any(k in t for k in ("周日", "星期日", "星期天", "礼拜日", "礼拜天")):
        base = (6 - wd) % 7
        return base + (7 if nextweek else 0)
    if any(k in t for k in ("周末", "周六", "星期六", "礼拜六")):
        base = (5 - wd) % 7
        return base + (7 if nextweek else 0)
    return 0


async def plan_weather(weather_provider, dest: str, raw_text: str,
                       num_days: int, meta) -> list[dict | None]:
    """取目的地未来预报并对齐到行程各天。无 provider/无 key/超预报窗口 → 对应天 None（优雅降级）。
    返回长度 num_days 的列表，每项 {date,text,temp_high,temp_low} 或 None。"""
    out: list[dict | None] = [None] * max(0, num_days)
    if not weather_provider or num_days <= 0 or not dest:
        return out
    try:
        fc = await weather_provider.forecast(city=dest, days=7, meta=meta)
    except Exception as e:  # 无 key / provider 抖动 → 静默降级（天气非行程硬依赖）
        logger.info("trip weather forecast unavailable for %s: %s", dest, e)
        return out
    if not fc:
        return out
    off = _start_offset(raw_text, datetime.now())
    for i in range(num_days):
        idx = off + i
        if 0 <= idx < len(fc):
            f = fc[idx]
            out[i] = {"date": f.date, "text": f.text_day,
                      "temp_high": f.temp_high, "temp_low": f.temp_low}
    return out


def _weather_hint(weather: list[dict | None]) -> str:
    """把对齐后的各天天气拼成 propose 的 LLM 提示（雨天优先室内/就近景点）。全空返回 ''。"""
    parts = []
    for i, w in enumerate(weather, start=1):
        if w and w.get("text"):
            lo, hi = w.get("temp_low", ""), w.get("temp_high", "")
            rng = f" {lo}-{hi}℃" if lo and hi else ""
            parts.append(f"第{i}天{w['text']}{rng}")
    if not parts:
        return ""
    return ("\n【各天天气预报】" + "；".join(parts)
            + "。请据此编排：室外/登山/海滨/公园类景点安排在天气好的天；"
            "预计降雨的天多安排室内景点（博物馆/展馆/商圈/水族馆）或彼此就近的点，减少淋雨与奔波。")


async def propose(llm, dest: str, days: str, prefs: str,
                  pool_names: list[str], raw_text: str = "",
                  weather_hint: str = "", cities: list[str] | None = None,
                  pool_by_city: dict | None = None) -> dict:
    """LLM 产结构化骨架；约束只选池内名字。解析失败/空 → 确定性兜底分配。

    G9 多城市（cities 非空）：prompt 按城列池、要求每天标 city 且按序编排；
    骨架 day 的 city 被收敛到城市集内，缺标的按序均摊（确定性兜底，不臆造城市）。"""
    target_days = _norm_days(days)
    cities = [c for c in (cities or []) if c]
    if not pool_names:
        return _fallback_skeleton(pool_names, target_days or 1, prefs,
                                  cities=cities, pool_by_city=pool_by_city)

    if cities:
        pools_txt = "\n".join(
            f"【可选景点·{c}】：{'、'.join([p.name for p in (pool_by_city or {}).get(c, [])][:15]) or '（暂无）'}"
            for c in cities)
        user = (f"目的地（按此顺序途经）：{' → '.join(cities)}；"
                f"天数：{target_days or '不限'}；偏好：{prefs or '无特别要求'}。\n"
                f"原始需求：{raw_text}\n{pools_txt}"
                + (weather_hint or "")
                + f"\n每天的 stops 只选当天所在城市的可选景点，并给每天加"
                  f'"city"字段（只能取：{"、".join(cities)}）；'
                  "按城市顺序编排，不要来回跳城。")
    else:
        user = (f"目的地：{dest}；天数：{target_days or '不限'}；偏好：{prefs or '无特别要求'}。\n"
                f"原始需求：{raw_text}\n【可选景点】：{'、'.join(pool_names[:30])}"
                + (weather_hint or ""))
    try:
        out = await llm.complete(
            [{"role": "system", "content": _PROPOSE_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.5, max_tokens=600)
    except Exception as e:
        logger.warning("propose LLM failed, deterministic fallback: %s", e)
        return _fallback_skeleton(pool_names, target_days or 2, prefs,
                                  cities=cities, pool_by_city=pool_by_city)

    skeleton = _parse_skeleton(out, pool_names, cities=cities)
    if not skeleton.get("days"):
        return _fallback_skeleton(pool_names, target_days or 2, prefs,
                                  cities=cities, pool_by_city=pool_by_city)
    # 天数对齐：LLM 给少了用兜底补，给多了截断。
    if target_days:
        skeleton["days"] = skeleton["days"][:target_days]
        if len(skeleton["days"]) < target_days:
            extra = _fallback_skeleton(pool_names, target_days, prefs,
                                       cities=cities,
                                       pool_by_city=pool_by_city)["days"]
            skeleton["days"].extend(extra[len(skeleton["days"]):])
    if cities:
        _assign_missing_cities(skeleton, cities)
    return skeleton


def _assign_missing_cities(skeleton: dict, cities: list[str]) -> None:
    """缺 city 的 day 按序均摊（LLM 漏标不炸、不臆造城市名）。原地修改。"""
    days = skeleton.get("days") or []
    if not days:
        return
    per = max(1, -(-len(days) // len(cities)))      # ceil
    for i, day in enumerate(days):
        if not (day.get("city") or "").strip():
            day["city"] = cities[min(i // per, len(cities) - 1)]


def _parse_skeleton(text: str, pool_names: list[str],
                    cities: list[str] | None = None) -> dict:
    """解析 LLM JSON 骨架，并把 stop 名收敛到池内（拒列表外幻觉名）。
    G9：day 的 city 收敛到城市集内（列表外城市名置空，由按序均摊兜底）。"""
    block = _extract_json_block(text)
    if not block:
        return {"days": []}
    try:
        data = json.loads(block)
    except (json.JSONDecodeError, TypeError):
        return {"days": []}
    pool_set = {n: n for n in pool_names}
    city_set = set(cities or [])
    # 池名的宽松匹配：LLM 可能写「西湖景区」而池里是「西湖」
    days_out = []
    for i, day in enumerate(data.get("days") or [], start=1):
        if not isinstance(day, dict):
            continue
        stops_out = []
        for s in day.get("stops") or []:
            if not isinstance(s, dict):
                continue
            nm = (s.get("name") or "").strip()
            if not nm:
                continue
            matched = _match_pool_name(nm, pool_set)
            if not matched:
                continue                       # 列表外 → 丢弃（不臆造）
            stype = (s.get("type") or "attraction").strip() or "attraction"
            stops_out.append({"name": matched, "type": stype})
        if stops_out:
            city = str(day.get("city") or "").strip()
            days_out.append({"day_index": i,
                             "theme": (day.get("theme") or "").strip(),
                             "city": city if city in city_set else "",
                             "stops": stops_out})
    return {"days": days_out}


def _match_pool_name(name: str, pool_set: dict) -> str:
    if name in pool_set:
        return name
    for pn in pool_set:
        if pn and (pn in name or name in pn):
            return pn
    return ""


def _fallback_skeleton(pool_names: list[str], days: int, prefs: str,
                       cities: list[str] | None = None,
                       pool_by_city: dict | None = None) -> dict:
    """确定性兜底：把池内景点按每天 N 个均匀分配，保证不空。
    G9 多城市：天数按城市序均摊，每城用自己的池（城池空则跳过该城不空转）。"""
    days = max(1, min(days or 2, _MAX_DAYS))
    per = per_day_count(prefs)
    cities = [c for c in (cities or []) if c]
    out = []
    if cities and pool_by_city:
        per_city = max(1, -(-days // len(cities)))      # ceil
        d = 1
        for c in cities:
            names = [p.name for p in (pool_by_city.get(c) or []) if p.name]
            idx = 0
            for _ in range(per_city):
                if d > days:
                    break
                chunk = names[idx:idx + per]
                idx += per
                if not chunk and names:
                    chunk = names[:1]
                out.append({"day_index": d, "theme": "", "city": c,
                            "stops": [{"name": n, "type": "attraction"}
                                      for n in chunk]})
                d += 1
        return {"days": out}
    names = pool_names or []
    idx = 0
    for d in range(1, days + 1):
        chunk = names[idx:idx + per]
        idx += per
        if not chunk and names:                # 池用尽 → 循环复用，避免空天
            chunk = names[:1]
        out.append({"day_index": d, "theme": "",
                    "stops": [{"name": n, "type": "attraction"} for n in chunk]})
    return {"days": out}


# ──────────────────────────── ground ───────────────────────────

def _poi_to_dict(p: POI) -> dict:
    return {"id": p.id, "name": p.name, "address": p.address,
            "lat": p.lat, "lng": p.lng, "rating": p.rating}


def _city_center(pool: list[POI]):
    pts = [(p.lat, p.lng) for p in pool if p.lat and p.lng]
    if not pts:
        return None
    return GeoPoint(lat=sum(a for a, _ in pts) / len(pts),
                    lng=sum(b for _, b in pts) / len(pts))


async def ground(poi_provider, skeleton: dict, pool: list[POI],
                 meta, *, dest: str, days: str = "", prefs: str = "",
                 raw_text: str = "", llm=None,
                 cities: list[str] | None = None,
                 pool_by_city: dict | None = None) -> Trip:
    """把骨架接地为结构化 Trip：每个 stop 映射真实 POI。
    G9 多城市：按 day.city 优先在该城池取坐标；新搜索的 near 也锚在该城池中心。"""
    pool_by_name = {(p.name or "").strip(): p for p in pool}
    center = _city_center(pool)
    trip = Trip(destination=dest, days=_norm_days(days),
                cities=[c for c in (cities or []) if c],
                preferences=[w for w in _PREF_RELAXED if w in (prefs or "")],
                raw_text=raw_text, ev={"full_range_km": FULL_RANGE_KM})
    sid = 0
    for day in skeleton.get("days") or []:
        day_city = str(day.get("city") or "").strip()
        city_pool = (pool_by_city or {}).get(day_city) or []
        city_by_name = {(p.name or "").strip(): p for p in city_pool}
        d = Day(day_index=int(day.get("day_index", 1) or 1),
                theme=(day.get("theme") or "").strip(), city=day_city)
        for s in day.get("stops") or []:
            sid += 1
            nm = (s.get("name") or "").strip()
            stype = (s.get("type") or "attraction").strip() or "attraction"
            stop = Stop(stop_id=f"s{sid}", name=nm, type=stype,
                        dwell_min=_DWELL_BY_TYPE.get(stype, 90), source="llm")
            poi = city_by_name.get(nm) or pool_by_name.get(nm)
            if poi is None:
                poi = await _ground_one(poi_provider, nm,
                                        _city_center(city_pool) or center,
                                        meta, llm)
            # 景点接地到住宿类（泛地点经 ground 新搜索易把民宿/别墅当景点）→ 整条丢弃，不进行程
            if (stype == "attraction" and poi is not None
                    and any(m in (poi.name or "") for m in _LODGING_MARKERS)):
                continue
            if poi is not None and poi.lat and poi.lng:
                stop.poi = _poi_to_dict(poi)
                stop.name = poi.name or nm
                stop.grounded = True
            d.stops.append(stop)
        trip.itinerary.append(d)
    return trip


async def _ground_one(poi_provider, name: str, near, meta, llm=None) -> POI | None:
    """搜索接地单个名字：name_matches 校验，拒「挂错名的非空结果」；有 llm 时经 landmark 解析官方名。"""
    async def _search(kw, n):
        try:
            return await poi_provider.search(kw, near=n, limit=1, meta=meta)
        except ProviderError as e:
            # 铁律③：接地失败返回空 → 该站保留 LLM 名字、grounded=False（不臆造地址），
            # 绝不用 mock 假 POI 充数（「示例{i}」冒充真实景点正是被验收点名的形态）。
            logger.warning("ground search '%s' failed（诚实降级，无 mock 回退）: %s", kw, e)
            return []

    results = await _search(name, near)
    if results and name_matches(name, results[0].name):
        return results[0]
    # 视觉/俗称地标 → 解析官方名再搜（与 navigation _find_destination 同套）；无 llm 则跳过。
    if llm is not None and is_landmark_description(name):
        for cand in await landmark_candidates(llm, name, logger=logger):
            cres = await _search(cand, None)
            if cres and name_matches(cand, cres[0].name):
                return cres[0]
    return None


# ──────────────────────────── solve ────────────────────────────

async def solve(poi_provider, trip: Trip, start_soc_pct: float, meta,
                 *, full_range_km: float = None, day_cap_min: int = None) -> Trip:
    """确定性：算相邻 stop 车程 → 按日上限顺延 → 沿路线按 SoC 编织充电点 → 递推 SoC。"""
    full_range = float(full_range_km or FULL_RANGE_KM)
    cap = int(day_cap_min or DAY_MAX_MIN)
    cache: dict = {}
    origin = None
    try:
        origin_lat = float((meta or {}).get("current_lat") or 0)
        origin_lng = float((meta or {}).get("current_lng") or 0)
        if origin_lat and origin_lng:
            origin = Stop(
                stop_id="__origin__",
                name="当前位置",
                poi={
                    "name": "当前位置",
                    "lat": origin_lat,
                    "lng": origin_lng,
                },
                dwell_min=0,
                grounded=True,
                source="vehicle",
            )
    except (TypeError, ValueError):
        origin = None

    async def route(a: Stop, b: Stop):
        """相邻已接地 stop 的路线（distance_km, drive_min, points），按 id 对缓存。"""
        key = (a.stop_id, b.stop_id)
        if key in cache:
            return cache[key]
        res = (0.0, 0, [])
        if a.lat and a.lng and b.lat and b.lng:
            try:
                r = await poi_provider.get_route(
                    GeoPoint(lat=a.lat, lng=a.lng), GeoPoint(lat=b.lat, lng=b.lng),
                    meta=meta, with_polyline=True)
                res = (float(r.get("distance_km") or 0), int(r.get("duration_min") or 0),
                       r.get("points") or [])
            except Exception as e:   # best-effort：算不出按 0 计（含 ProviderError）
                logger.debug("route unavailable: %s", e)
        cache[key] = res
        return res

    async def day_minutes(day: Day) -> int:
        gs = day.grounded_stops()
        total = sum(s.dwell_min for s in gs)
        for a, b in zip(gs, gs[1:]):
            total += (await route(a, b))[1]
        return total

    # 1) 按日上限把尾部 stop 顺延次日（前向、有界）。
    i = 0
    while i < len(trip.itinerary) and len(trip.itinerary) <= _MAX_DAYS:
        day = trip.itinerary[i]
        while len(day.grounded_stops()) > 1 and await day_minutes(day) > cap:
            moved = day.stops.pop()
            if i + 1 >= len(trip.itinerary):
                trip.itinerary.append(Day(day_index=len(trip.itinerary) + 1))
            trip.itinerary[i + 1].stops.insert(0, moved)
        i += 1
    for idx, day in enumerate(trip.itinerary, start=1):   # 顺延后重排 day_index
        day.day_index = idx
    trip.days = len(trip.itinerary)

    # 2) 逐 leg 建结构化驾驶段 + 充电编织 + SoC 递推。
    # G9 跨天衔接：前一天末站 → 当天首站也建 leg（两端都已接地才建）——多城市行程
    # 全程最长的恰是跨城段，此前它不进 leg，充电编织对它是盲的、SoC 天与天之间断开。
    # 日上限顺延判定（上方 day_minutes）刻意不含跨天 leg：跨城赶路不该挤走景点。
    running = float(start_soc_pct or 0) or 50.0
    trip.ev["start_soc"] = round(running)
    prev_last = None
    for day_index, day in enumerate(trip.itinerary):
        gs = day.grounded_stops()
        day.legs = []
        route_stops = (
            [origin, *gs]
            if day_index == 0 and origin is not None and gs
            else list(gs)
        )
        if day_index > 0 and prev_last is not None and gs:
            route_stops = [prev_last, *route_stops]
        if gs:
            prev_last = gs[-1]
        for a, b in zip(route_stops, route_stops[1:]):
            dist, drive_min, points = await route(a, b)
            leg = Leg(from_stop_id=a.stop_id, to_stop_id=b.stop_id,
                      distance_km=dist, drive_min=drive_min, soc_before=round(running))
            targets = weave_charging_targets(points, dist, running, full_range)
            for t in targets:
                st = await _ground_station(poi_provider, t, meta)
                if st:
                    leg.charging_stops.append(st)
            if leg.charging_stops:                       # 中途补电 → 抵达约 80% 减末段
                last_km = leg.charging_stops[-1].get("at_km") or 0
                running = max(10.0, 80.0 - (dist - last_km) / full_range * 100)
            else:
                running = max(0.0, running - dist / full_range * 100)
            leg.soc_after = round(running)
            day.legs.append(leg)
    return trip


async def _ground_station(poi_provider, target: dict, meta) -> dict | None:
    """把充电目标点接地为真实站（near=该坐标搜「充电站」）。接不到返回 None（不臆造）。"""
    lat, lng = target.get("lat"), target.get("lng")
    if not lat or not lng:
        return None
    try:
        near = await poi_provider.search("充电站", near=GeoPoint(lat=lat, lng=lng),
                                         limit=1, meta=meta)
    except ProviderError:
        near = []
    if not near:
        return None
    st = near[0]
    return {"name": st.name, "address": st.address, "lat": st.lat, "lng": st.lng,
            "at_km": target.get("at_km")}


# ─────────────────────────── narrate ───────────────────────────

def narrate(trip: Trip) -> tuple[str, dict]:
    """确定性渲染：按天 1-2 句 TTS 话术 + trip_itinerary 卡。有天气则每天标注、开头点明已结合天气。"""
    lines = []
    has_weather = False
    for day in trip.itinerary:
        gs = day.grounded_stops()
        names = "、".join(s.name for s in gs[:4]) if gs else "（待补充景点）"
        charge_n = sum(len(leg.charging_stops) for leg in day.legs)
        w = day.weather if isinstance(day.weather, dict) else None
        wtag = ""
        if w and w.get("text"):
            has_weather = True
            lo, hi = w.get("temp_low", ""), w.get("temp_high", "")
            wtag = f"（{w['text']}{f' {lo}-{hi}℃' if lo and hi else ''}）"
        ctag = f"（{day.city}）" if day.city else ""    # G9 多城市：每天标城
        seg = f"第{day.day_index}天{ctag}{wtag}：{names}"
        if charge_n:
            seg += f"（途中补电{charge_n}次）"
        lines.append(seg)
    theme_tag = f"按《{trip.theme}》主题" if trip.theme else ""
    head = (f"已{theme_tag}结合天气为您规划{trip.destination}{trip.days}天行程："
            if has_weather
            else f"已{theme_tag}为您规划{trip.destination}{trip.days}天行程："
            if theme_tag else f"为您规划{trip.destination}{trip.days}天行程：")
    speech = head + "；".join(lines) + "。"
    return speech, trip.card_dict()
