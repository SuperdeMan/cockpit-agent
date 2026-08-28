"""行程规划 Agent（P0 重构）—— 结构化可执行行程 + 充电感知 + 落 memory。

把项目铁律「规划/执行分离、LLM 提议、确定性 Executor 落地」下沉到 trip-planner 内部：
`_plan` 不再让 LLM 自由文本直出整份行程，而是驱动 `pipeline` 四段
（propose 提议骨架 → ground 接地真实 POI → solve 算车程/编织充电 → narrate 出话术+卡），
产出结构化 `Trip`（`models.Trip`）。状态落 memory（profile KV `trip_active`），Agent 无状态化。

provider 在进程内复用 navigation 的 `POIProvider`（跟随 charging_planner 先例）。
确认轮（`meta.confirmed=="true"`）→ `_finalize` 直接收尾、绝不再 NEED_CONFIRM（防死循环）。
"""
from __future__ import annotations
import json
import logging
import os
import re

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, NEED_CONFIRM
from agents._sdk.shared_state import TRIP_ACTIVE
from agents._sdk.location import current_location_from_meta
from agents._sdk.provenance import attach
from agents.navigation.src.providers import build_poi_provider
from agents.info.src.providers import build_weather_provider
from .models import Trip, Stop
from .pipeline import (build_poi_pool, build_theme_pool, theme_hint,
                       ground_must_visit, must_visit_hint,
                       ensure_must_visit_in_itinerary, correct_stop_cities,
                       propose, ground, solve, narrate,
                       plan_weather, _weather_hint, _norm_days,
                       rough_km, _ground_one, _poi_to_dict)
from .extract import (extract_trip, extract_theme, extract_cities,
                      is_direction_dest, _CITY_SPLIT_RE)

logger = logging.getLogger("agent.trip_planner")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")
# 当前活动行程键经 agents._sdk.shared_state.TRIP_ACTIVE 引用；登记见 docs/conventions.md「跨 Agent 状态键」。

_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")


def _drop_named_pois_from_cities(cities: list[str], must_visit: list[str]) -> list[str]:
    """把混进城市序的**点名 POI** 剔掉（E3）。

    真栈实测（六城长句）：planner 把「大秋裤→东方之门」同时填进 destination 的
    城市串和 must_visit，于是「东方之门」成了一座城——逐城建池搜「东方之门 景点」、
    后面每天都标着它，归城校正也无从谈起（没有一天标着真正的城）。

    判据是**归一后精确相等**，不是包含：「苏州园林」不等于「苏州」，真城市不受伤。
    剔完只剩一城 → 返回单元素列表，由调用方退回单城路径。
    """
    if len(cities) < 2 or not must_visit:
        return cities
    named = {_PAREN_RE.sub("", w).strip() for w in must_visit}
    named.discard("")
    kept = [c for c in cities if c not in named]
    if len(kept) != len(cities):
        logger.info("城市序剔除点名 POI：%s → %s",
                    "、".join(cities), "、".join(kept) or "（空）")
    return kept


_SPOKEN_DAYS_RE = re.compile(r"[0-9一二两三四五六七八九十]")


def _days_for_cities(days: str, cities: list[str]) -> str:
    """多城且**用户没说天数**时，天数取城数（每城至少一天）。

    真栈六城实测：planner 填 days=「用户未指定」→ 骨架排成 3 天，南京/济南/潍坊/
    北京四城一天都没分到，归城校正无处可搬、话术还写着「6 城 3 天」。

    「说没说」按**原始槽值里有没有数量词**判断，不能用 `_norm_days`——它把
    非数字全剥掉，「三天」会被读成 0（那就成了「替用户改需求」）。
    """
    if len(cities) < 2:
        return days
    if _SPOKEN_DAYS_RE.search(str(days or "")):
        return days                        # 用户明说了（含中文数字）→ 一律不覆盖
    return str(len(cities))


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_MOD_DAY_RE = re.compile(r"第\s*([一二两三四五六七八九十0-9]+)\s*天")
_ORDINAL_RE = re.compile(r"第\s*([一二两三四五六七八九十0-9]+)\s*[个站]")
# 换/调整某站时若指定了换成什么（『第二站换成西湖』），取目标名
_REPLACE_TARGET_RE = re.compile(r"(?:换成|改成|换为|改为|换到)\s*([^，。,、\s]{2,12})")
_DAY_PREFIX_RE = re.compile(r"^第\s*[一二两三四五六七八九十0-9]+\s*天的?")
# 结构化编辑：删/加某个具体停靠点（『换』走整天重规划，不在此匹配）
_REMOVE_RE = re.compile(r"(?:删掉|删除|去掉|不去|不想去|不要去|去不了|取消)\s*([^，。,、\s了]{2,12})")
_ADD_RE = re.compile(r"(?:加一个|加个|再加|增加|多加|加上|想去|顺便去|顺路去)\s*([^，。,、\s]{2,12})")
# C7-C（2026-08-28，QA P1-09）：否定式顺序约束「不要把A排到B前面」。
# 它此前**三张词表一张都不含**（_REMOVE_RE/_ADD_RE/_replace 逐条实测 None）⇒ 必然掉进
# 路径③整程重规划，而路径③把 cities/theme 全丢了——跨城混排与「3 天变 4 天」都源于此。
# 判据零领域词：A/B 只在**行程自己的城市表**里解析，一个城市名都不写死。
_ORDER_CONSTRAINT_RE = re.compile(
    r"(?:不要|不想|别|不能|不许|不得)\s*(?:把|将)?\s*([^，。,、\s]{2,10}?)\s*"
    r"(?:排|放|安排|调|摆)\s*(?:到|在|去|得)?\s*([^，。,、\s]{2,10}?)\s*"
    r"(?:的)?\s*(?:前面|前边|之前|前头)")
# C7-B：修改话术里**有没有点名天数**。「第N天」是定位不是天数，负向后顾排掉——
# 不排掉的话「第二天换成宋城」会被当成「用户说了天数」，守恒闸整条失效。
_DAY_COUNT_RE = re.compile(r"(?<!第)\s*(?:[0-9]+|[一二两三四五六七八九十]+)\s*天")
# C7-A：裸 POI 名的跨城披露闸。150km 与 navigation R1 的区县级可信闸**同一把尺子**
# ——「用户裸报一个名字时心智是本地」在两处是同一条判断。
_CROSS_CITY_KM = 150.0

# R8：天气驱动改排——「哪天要下雨的话，把那天的安排换成室内的」（雨×室内 / 雨×换改调）
_RAIN_INDOOR_RE = re.compile(
    r"(下雨|有雨|雨天|要下雨).{0,15}(室内|屋里)|室内.{0,10}(雨|下雨)|"
    r"(下雨|有雨|雨天|要下雨)的?话?.{0,10}(换|改|调)")


def _is_rain_indoor_modification(text: str) -> bool:
    """Recognize the closed rain + indoor + edit concept after planner paraphrase."""
    value = text or ""
    return (
        _RAIN_INDOOR_RE.search(value) is not None
        or (
            any(word in value for word in ("下雨", "有雨", "雨天", "要下雨"))
            and any(word in value for word in ("室内", "屋里"))
            and any(word in value for word in ("换", "改", "调"))
        )
    )


class TripPlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        # 进程内复用 navigation 的 POI provider（接地景点/充电站 + 算 leg 路线），
        # 跟随 charging_planner 先例，避免每 leg 跨 gRPC。铁律③（M0a 同款，此处曾漏网）：
        # 运行期真实源失败诚实降级，**无 mock 回退**——假景点会被写进行程被导航过去。
        self.poi = build_poi_provider()
        # 天气联动（#3）：进程内复用 info 的和风 provider，规划时结合目的地多日预报。
        # 无凭据/抖动时 forecast 抛错 → plan_weather 静默降级（天气非行程硬依赖）。
        self.weather = build_weather_provider()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {"trip.plan": self._plan, "trip.modify": self._modify,
                    "trip.navigate": self._navigate, "trip.status": self._status,
                    "trip.reschedule": self._reschedule}
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status="failed", speech="行程助手暂不支持该请求。")

    # ── 电量 ───────────────────────────────────────────────────
    async def _soc_pct(self, ctx, meta) -> float:
        """当前电量百分比：优先边端注入的真实车辆电量，回退 memory，再回退 50%。
        与 charging_planner._resolve_soc 同源，保证多日行程起点 SoC 与仪表一致。"""
        soc = str((meta or {}).get("vehicle_battery", "") or "").strip()
        if not soc:
            try:
                vals = await ctx.fetch("vehicle.battery")
                soc = vals.get("vehicle.battery", "")
            except Exception:
                soc = ""
        try:
            return float(str(soc).replace("%", "").strip()) or 50.0
        except ValueError:
            return 50.0

    # ── 持久化（memory profile KV；Agent 无状态化）───────────────
    async def _load_trip(self, ctx) -> Trip | None:
        """从 memory 读当前活动行程。失败/无 → None。"""
        raw = await ctx.load_shared_state(TRIP_ACTIVE)
        if not raw:
            return None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None
        return Trip.from_dict(raw) if isinstance(raw, dict) else None

    async def _save_trip(self, ctx, trip: Trip) -> None:
        """写当前活动行程到 memory（best-effort，失败不阻断规划）。"""
        try:
            await ctx.save_shared_state(TRIP_ACTIVE, trip.to_dict())
        except Exception as e:
            logger.warning("save trip failed: %s", e)

    async def _pool_for_trip(self, trip: Trip, prefs: str, meta) -> list:
        """修改类路径的候选池：多城市行程逐城并集（「杭州、苏州 景点」搜不出东西），
        单城市走原样。"""
        cities = [c for c in (trip.cities or []) if c]
        if len(cities) < 2:
            return await build_poi_pool(self.poi, trip.destination, prefs, None, meta)
        pool, seen = [], set()
        for c in cities:
            for p in await build_poi_pool(self.poi, c, prefs, None, meta):
                nm = (p.name or "").strip()
                if nm and nm not in seen:
                    seen.add(nm)
                    pool.append(p)
        return pool

    # ── C7-A：接地的城市锚 ────────────────────────────────────
    async def _city_anchor(self, dest: str, cities: list, meta):
        """裸 POI 名的城市锚与跨城披露。返回 `(near, disclosure_or_None)`。

        病历：`build_poi_pool` 四个调用点 `near` 恒 None（注释明写是设计：靠关键词
        「{dest} 景点」定位）⇒ `place_text` 走全国序，「万象城 美食」命中**杭州**，
        而唯一的校验 `name_matches` 是子串包含，「万象城」⊂「杭州万象城店」直接放行。

        **为什么不整套复用 navigation 的 R1**（方案要求先评估）：R1 的救济链
        （去偏置全国重搜 / 地标 LLM / 类目锚词复核）解的是**反方向**的问题——就近搜
        返回垃圾时怎么捞回全国唯一的那个地标；trip 这边要的恰恰相反，是**别捞到
        外地那个同名的**。整块下沉 `_find_destination` 要连 `_dest_matches`/
        `_category_anchor`/`_grounds_to`/`_rough_km`/landmark 一起搬（数百行 + 全套
        navigation 测试），换来的是一条方向相反的救济链。**复用的是它真正共用的那一件**：
        `geocode_level`（provider 能力，charging_planner 已有同款复用先例）与
        150km 这把尺子——「用户裸报一个名字时心智是本地」在两处是同一条判断。

        三段判据，全部零领域词：
        ① 多城行程（cities≥2）自己就点了城，不加锚；
        ② 目的地本身是行政区划（geocode level）⇒ 用户说的就是城市，不加锚
           （「去三亚玩三天」不该被锚在深圳）；
        ③ 否则是裸 POI 名：以当前位置为锚。接地结果离当前位置超过 150km ⇒
           **披露而不是直接排行程**（NEED_SLOT 让用户裁决，同 R1「区县级裸名
           只在 150km 内可信」那条）。
        """
        if len(cities or []) >= 2 or not dest:
            return None, None
        current = current_location_from_meta(meta)
        if current is None:
            return None, None            # 没有锚就不猜——同「认不出就返回空」
        level_fn = getattr(self.poi, "geocode_level", None)
        if level_fn and 2 <= len(dest) <= 4:
            try:
                level, _loc = await level_fn(dest, meta=meta)
            except Exception as e:       # provider 抖动不该阻断规划
                logger.debug("trip geocode level probe failed: %s", e)
                level = ""
            if level in ("国家", "省", "市", "区县"):
                return None, None
        try:
            found = await self.poi.search(dest, near=current, limit=1, meta=meta)
        except Exception as e:
            logger.debug("trip city anchor probe failed: %s", e)
            return None, None
        if not found or found[0].lat is None:
            return None, None            # 搜不到交给流水线照常诚实降级
        km = rough_km(current.lat, current.lng,
                      float(found[0].lat), float(found[0].lng))
        if km <= _CROSS_CITY_KM:
            return current, None
        top = found[0]
        where = (top.address or "").strip()
        return None, AgentResult(
            status=NEED_SLOT,
            speech=f"我找到的「{top.name}」在{where or '外地'}，"
                   f"离您现在的位置约{round(km)}公里。要按这个地方排行程吗？"
                   "如果是本地的那家，说一下城市我重新排。",
            follow_up="例如「深圳的那家」",
            missing_slots=["destination"])

    # ── 规划流水线 ─────────────────────────────────────────────
    async def _run_pipeline(self, ctx, meta, dest: str, days: str, prefs: str,
                            raw_text: str, theme: str = "",
                            cities: list[str] | None = None,
                            must_visit: list[str] | None = None,
                            near=None) -> tuple[Trip, dict]:
        """propose → ground → solve，产出结构化 Trip 与**软层观测**（E3）。

        G9 多城市（cities ≥2）：逐城建池（各「{city} 景点/美食」）、propose 按序分天
        标 city、ground 按城取坐标；城市顺序=用户口述序（v1 不做顺路重排）。

        第二个返回值 `obs` 只进 `data`（观测面），不进话术也不进卡片正文：
        主题接地命中率 `theme_grounding` + 归城校正搬动记录 `city_fixes`。"""
        cities = [c for c in (cities or []) if c]
        pool_by_city: dict[str, list] = {}
        if len(cities) >= 2:
            pool, seen = [], set()
            for c in cities:
                cp = await build_poi_pool(self.poi, c, prefs, None, meta)
                pool_by_city[c] = cp
                for p in cp:
                    nm = (p.name or "").strip()
                    if nm and nm not in seen:
                        seen.add(nm)
                        pool.append(p)
        else:
            cities = []
            # 目的地是行程城市（非当前位置）→ pool 搜索 near=None，靠关键词「{dest} 景点」定位。
            # C7-A 例外：目的地是**裸 POI 名**（「万象城」，geocode 判不出行政级）时
            # `near` 由 `_city_anchor` 给出当前位置——全国序会把「万象城 美食」搜到杭州。
            pool = await build_poi_pool(self.poi, dest, prefs, near, meta)
        # G4 主题检索步：主题相关地点经 LLM 提议 + 高德接地验证后**并入**池
        # （去重按名，池的封闭纪律不变——只是入池来源多一路）。
        theme_names: list[str] = []
        obs: dict = {}
        if theme:
            seen = {(p.name or "").strip() for p in pool}
            theme_pool, theme_stats = await build_theme_pool(
                self.llm, self.poi, theme, dest, meta)
            obs["theme_grounding"] = theme_stats   # E3：降级是设计，命中率要有读数
            for tp in theme_pool:
                nm = (tp.name or "").strip()
                theme_names.append(nm)
                if nm and nm not in seen:
                    seen.add(nm)
                    pool.append(tp)
        # P2：用户点名 POI 第三路入池（俗称经 landmark 解析；接不到丢弃不臆造）。
        mv_pairs = []
        if must_visit:
            mv_pairs = await ground_must_visit(
                self.llm, self.poi, must_visit, cities, dest, meta,
                pool_by_city=pool_by_city)
            seen_mv = {(p.name or "").strip() for p in pool}
            for city, mp in mv_pairs:
                nm = (mp.name or "").strip()
                if nm and nm not in seen_mv:
                    seen_mv.add(nm)
                    pool.append(mp)
                    if city and city in pool_by_city:
                        pool_by_city[city] = list(pool_by_city[city]) + [mp]
        # 天气联动：先取目的地多日预报对齐到行程各天，织进 propose（雨天优先室内/就近景点）。
        # 多城市按首城取（预报窗口本就 7 天、跨城对齐属 v2；首城覆盖头几天最准）。
        weather = await plan_weather(self.weather, cities[0] if cities else dest,
                                     raw_text, _norm_days(days) or 2, meta)
        skeleton = await propose(self.llm, dest, days, prefs,
                                 [p.name for p in pool], raw_text,
                                 weather_hint=_weather_hint(weather)
                                 + theme_hint(theme, theme_names)
                                 + must_visit_hint(mv_pairs),
                                 cities=cities, pool_by_city=pool_by_city)
        trip = await ground(self.poi, skeleton, pool, meta,
                            dest=dest, days=days, prefs=prefs, raw_text=raw_text,
                            llm=self.llm, cities=cities, pool_by_city=pool_by_city)
        # P2 确定性补插：LLM 骨架漏排的必去点不许丢（「点了名的不许丢」与
        # 「池外不臆造」是两条互补纪律）。
        ensure_must_visit_in_itinerary(trip, mv_pairs)
        # E3 归城校正：补插只管「漏排」，这里管「排错天」——按坐标把排进别城那天的
        # 停靠点搬回归属城首日。必须在 solve 之前：跨天衔接 leg 依赖最终分天。
        fixes = correct_stop_cities(trip, pool_by_city)
        if fixes:
            obs["city_fixes"] = fixes
        if theme and theme_names:
            trip.theme = theme          # 接地成功才标主题（narrate 据此带主题话术）
        # C7-B：点名地点随行程持久化——`trip.modify` 的整程重规划要把它再说一遍。
        trip.must_visit = [w for w in (must_visit or []) if str(w).strip()]
        soc = await self._soc_pct(ctx, meta)
        trip = await solve(self.poi, trip, soc, meta)
        # 每天填天气（卡片/话术展示；按 day_index 对齐，超预报窗口的天保持 None）
        for day in trip.itinerary:
            wi = day.day_index - 1
            if 0 <= wi < len(weather) and weather[wi]:
                day.weather = weather[wi]
        trip.session_id = ctx.session_id or ""
        trip.user_id = ctx.user_id or ""
        return trip, obs

    async def _plan(self, intent, ctx, meta) -> AgentResult:
        if meta.get("confirmed") == "true":
            return await self._finalize(ctx, meta)

        dest = (intent.slots.get("destination") or "").strip()
        days = (intent.slots.get("days") or "").strip()
        prefs = (intent.slots.get("preferences") or "").strip()
        # G4：主题槽 planner 可直接填，原话确定性兜底（与 G1 arrive_by 同款双轨）。
        # slot 值必须清洗——真栈首验实测 planner 把**整句**填进 theme
        # （「跟着《太平年》游杭州」原样进槽，话术念出嵌套书名号）：槽里带主题
        # 标记就提主体；否则只接受不含行程动词的干净短语；再不行回落原话解析。
        slot_theme = (intent.slots.get("theme") or "").strip()
        theme = extract_theme(slot_theme)
        if not theme and slot_theme and len(slot_theme) <= 16 \
                and not re.search(r"游|去|玩|带|规划|行程", slot_theme):
            theme = slot_theme
        if not theme:
            theme = extract_theme(intent.raw_text or "")
        if not dest:
            # 确定性路由（manifest route_hints）注入的 trip.plan 步 slots 为空——
            # 从原话抽取目的地/天数/偏好（原编排核心 _extract_trip 的领域逻辑，R2.1 搬回本 Agent）。
            edest, edays, eprefs = extract_trip(intent.raw_text or "")
            dest, days, prefs = dest or edest, days or edays, prefs or eprefs
        if not dest:
            return AgentResult(
                status=NEED_SLOT, speech="您想去哪里玩？",
                follow_up="请告诉我目的地", missing_slots=["destination"])
        # 方向词目的地（「北方」「南边」）：planner 会把「北上追春天」臆断成
        # destination=北方——确定性拦下追问（EVA 一#8 的期望行为本体），
        # 不管值来自 slot 还是抽取。真栈实测不拦的后果：搜「北方 景点」出
        # 北方车辆集团、天气配到阿拉伯语区。
        if is_direction_dest(dest):
            return AgentResult(
                status=NEED_SLOT,
                speech=f"想去{dest}玩呀——具体想去哪个城市？我来帮您安排行程。",
                follow_up="比如说「去哈尔滨玩三天」", missing_slots=["destination"])

        # G9 多城市：destination 槽的连写（「杭州、苏州」）优先拆，原话保序抽取兜底。
        cities = [c.strip() for c in _CITY_SPLIT_RE.split(dest) if c.strip()]
        if len(cities) < 2:
            raw_cities = extract_cities(intent.raw_text or "")
            cities = raw_cities if len(raw_cities) >= 2 else []
        if len(cities) >= 2:
            dest = "、".join(cities)        # 话术/卡片可读；build_poi_pool 走逐城池

        # P2：点名地点（planner 填 must_visit 顿号连写；俗称如「大秋裤」由接地侧
        # landmark 解析，这里不做映射）。
        must_visit = [w.strip(" 、，,。") for w in
                      _CITY_SPLIT_RE.split(intent.slots.get("must_visit") or "")
                      if w.strip(" 、，,。")]
        cities = _drop_named_pois_from_cities(cities, must_visit)
        if len(cities) >= 2:
            dest = "、".join(cities)
        elif len(cities) == 1:
            dest, cities = cities[0], []       # 只剩一城 → 退回单城路径
        days = _days_for_cities(days, cities)

        # C7-A：裸 POI 名先过跨城闸（「接孩子后去万象城」→ 杭州万象城 1 天行程）。
        near, disclosure = await self._city_anchor(dest, cities, meta)
        if disclosure is not None:
            return disclosure
        trip, obs = await self._run_pipeline(ctx, meta, dest, days, prefs,
                                             intent.raw_text, theme=theme,
                                             cities=cities, must_visit=must_visit,
                                             near=near)
        await self._save_trip(ctx, trip)
        speech, card = narrate(trip)
        attach(card, self.poi)  # trip_itinerary 卡盖 _prov 章（验收补口：此前全族无标）
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"{speech}\n\n确认按此方案出行吗？",
            ui_card=card,
            data=obs or None,      # E3 软层观测（主题接地命中率 / 归城校正搬动）
            follow_up="说『确认』即可，或告诉我需要调整的地方",
        ).action("trip.plan", {"destination": dest, "days": str(trip.days)},
                 require_confirm=True)

    async def _modify(self, intent, ctx, meta) -> AgentResult:
        if meta.get("confirmed") == "true":
            return await self._finalize(ctx, meta)

        modification = (intent.slots.get("modification") or "").strip() \
            or (intent.raw_text or "").strip()
        if not modification:
            return AgentResult(
                status=NEED_SLOT, speech="您想怎么调整行程？",
                follow_up="例如：第二天换成宋城", missing_slots=["modification"])

        trip = await self._load_trip(ctx)
        if not trip or not trip.itinerary:
            return AgentResult(
                status=NEED_SLOT,
                speech="还没有正在规划的行程，您想去哪里玩几天？",
                follow_up="例如：周末去杭州两天", missing_slots=["destination"])

        dest = trip.destination
        prefs = "、".join(trip.preferences)
        soc = await self._soc_pct(ctx, meta)

        # C7-C：**先问一句「这条约束是不是已经满足了」**。否定式顺序约束
        # 「不要把A排到B前面」三张编辑词表一条都不认，于是必然掉进路径③整程重规划
        # ——而重规划本身就是跨城混排与「3 天变 4 天」的来源。已满足就零重规划、
        # 零确认直答：**没动您的方案**这句话本身就是答案。
        satisfied = self._order_constraint_satisfied(trip, modification)
        if satisfied:
            return AgentResult(speech=satisfied, ui_card=trip.card_dict())

        # R8（旅程 B3-1）：「哪天要下雨就把那天换成室内的」——天气驱动按天改排。
        # 雨天判定用行程已存的 Day.weather（plan_weather 对齐的预报）**确定性**定位目标天，
        # 逐天走单天重规划（路径②同机制）+ 强室内约束；无雨天诚实说不用改。
        # 原路径③把这句并进偏好整程重规划，LLM 软约束压不住，原样端回（假重排）。
        if _is_rain_indoor_modification(modification):
            return await self._modify_rainy_days_indoor(
                ctx, meta, trip, dest, prefs, soc, modification)

        # 重规划会整个换掉 trip 对象，这三样要在**换掉之前**取（C7-B）。
        original_days = int(trip.days or 0)
        theme_before = trip.theme
        cities_before = list(trip.cities or [])
        must_visit_before = list(trip.must_visit or [])
        # ① 结构化编辑优先：加/删某个具体停靠点（只动受影响项，跨天去重）。
        if await self._apply_structural_edit(trip, modification, meta):
            trip = await solve(self.poi, trip, soc, meta)
        else:
            n = self._modify_day(modification)
            if n and trip.day(n):
                # ② 只重规划第 n 天：其余 Day 原样保留（结构化天然不漂移）。
                pool = await self._pool_for_trip(trip, prefs, meta)
                # 跨天去重：重规划某天时排除其它天已用景点，避免改完与别天撞车。
                used = {s.name for d in trip.itinerary if d.day_index != n for s in d.stops}
                names = [p.name for p in pool if p.name not in used]
                sk = await propose(self.llm, dest, "1", prefs, names, modification)
                oneday = await ground(self.poi, sk, pool, meta,
                                      dest=dest, prefs=prefs, raw_text=modification,
                                      llm=self.llm)
                if oneday.itinerary and oneday.itinerary[0].stops:
                    newday = oneday.itinerary[0]
                    newday.day_index = n
                    for idx, d in enumerate(trip.itinerary):
                        if d.day_index == n:
                            trip.itinerary[idx] = newday
                            break
                trip = await solve(self.poi, trip, soc, meta)
            else:
                # ③ 定位不到具体天 → 整程重规划（把修改并入偏好上下文）。
                #    ⚠ **上下文要整套带过去**（C7-B）：路径③此前只传 6 个位置参数，
                #    `cities/theme/must_visit` 全丢——多城逐城建池当场退化成把
                #    「深圳、广州、珠海 景点」当一个高德关键词搜，跨城混排的池子
                #    就是这么来的；多城 propose 的保序指令也只写在多城分支里，
                #    cities 一丢就永远走不到。丢上下文是纯 bug，不是取舍。
                trip, _ = await self._run_pipeline(
                    ctx, meta, dest, str(original_days or ""),
                    f"{prefs} {modification}".strip(), modification,
                    theme=theme_before, cities=list(cities_before),
                    must_visit=list(must_visit_before))
                trip = await self._keep_days(
                    ctx, meta, trip, original_days, modification, prefs, dest,
                    theme_before, cities_before, must_visit_before)

        await self._save_trip(ctx, trip)
        speech, card = narrate(trip)
        attach(card, self.poi)  # trip_itinerary 卡盖 _prov 章（验收补口：此前全族无标）
        drift = self._days_drift_note(original_days, trip, modification)
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"{speech}\n\n{drift}确认按此调整吗？",
            ui_card=card,
            follow_up="说『确认』即可",
        ).action("trip.modify", {"modification": modification}, require_confirm=True)

    # ── C7（2026-08-28，QA P1-09）：修改的三条不变量 ───────────────────
    @staticmethod
    def _order_constraint_satisfied(trip: Trip, text: str) -> str:
        """否定式顺序约束「不要把A排到B前面」已经成立时，返回一句直答；否则空串。

        **判据零领域词**：A/B 只在 `trip.cities` 里解析，城市名一个都不写死
        （同 `_candidate_label` 的分工——领域值由产生方给，判据这边只认结构）。
        解析不出任何一侧就返回空串走原路：**认不出就让路**，不替用户裁决。
        """
        cities = [str(c).strip() for c in (trip.cities or []) if str(c).strip()]
        if len(cities) < 2:
            return ""
        m = _ORDER_CONSTRAINT_RE.search(text or "")
        if not m:
            return ""

        def _resolve(token: str) -> str:
            token = (token or "").strip()
            for c in cities:
                if c and (c in token or token in c):
                    return c
            return ""

        a, b = _resolve(m.group(1)), _resolve(m.group(2))
        if not a or not b or a == b:
            return ""
        # 约束语义：A 不许排在 B 前面 ⇒ 现行程里 B 必须在 A 前面。
        if cities.index(b) < cities.index(a):
            return (f"现在就是{b}在{a}前面（{'、'.join(cities)}），"
                    "没动您的方案。")
        return ""

    async def _keep_days(self, ctx, meta, trip: Trip, original_days: int,
                         modification: str, prefs: str, dest: str,
                         theme: str, cities: list, must_visit: list) -> Trip:
        """C7-B 未提及维度守恒：这句修改里没提天数 ⇒ 天数不许自己变。

        `solve` 在单日超上限时会新建整天并回写 `trip.days`（pipeline 既有语义），
        跨城混排一撑爆 drive_min，3 天就「合法地」变成 4 天。这里**回炉一次**：
        把「保持 N 天」显式写进重规划上下文再跑一遍。还不等就不再硬掰
        ——由 `_days_drift_note` 把它变成一次**显式选择**，而不是静默扩天。
        """
        if not original_days or _DAY_COUNT_RE.search(modification or ""):
            return trip
        if int(trip.days or 0) == original_days:
            return trip
        retried, _ = await self._run_pipeline(
            ctx, meta, dest, str(original_days),
            f"{prefs} {modification} 保持{original_days}天不变".strip(),
            modification, theme=theme, cities=list(cities),
            must_visit=list(must_visit))
        return retried if int(retried.days or 0) == original_days else trip

    @staticmethod
    def _days_drift_note(original_days: int, trip: Trip, modification: str) -> str:
        """天数被改动且用户没要求过 ⇒ 在确认话术里**说出来**。

        静默扩天最坏的地方不是多一天，是用户以为自己只提了一条顺序要求。
        用户自己说了天数（「改成四天」）时这条不响——那不是漂移，是要求。
        """
        now = int(trip.days or 0)
        if not original_days or now == original_days:
            return ""
        if _DAY_COUNT_RE.search(modification or ""):
            return ""
        verb = "放不下" if now > original_days else "用不满"
        return (f"⚠ 按您这条要求调整，原来的{original_days}天{verb}，"
                f"会变成{now}天。")

    async def _modify_rainy_days_indoor(self, ctx, meta, trip: Trip, dest: str,
                                        prefs: str, soc, modification: str) -> AgentResult:
        """R8：按 Day.weather 定位雨天 → 逐天重规划为室内安排（强约束进 propose 原话）。"""
        rainy = [d.day_index for d in trip.itinerary
                 if isinstance(d.weather, dict) and "雨" in str(d.weather.get("text", ""))]
        if not rainy:
            return AgentResult(
                speech=f"看了下预报，{dest}这几天都没有雨，行程不用调整。")
        pool = await self._pool_for_trip(trip, prefs, meta)
        # 全部雨天**合并成一次** propose+ground（批次3 真栈：逐天各跑一轮 85.9s 撑爆
        # 90s 网关窗口→「处理超时」）；产出的第 i 天映射回第 i 个雨天。
        used = {s.name for d in trip.itinerary
                if d.day_index not in rainy for s in d.stops}
        names = [p.name for p in pool if p.name not in used]
        ask = ("这几天有雨，全部改成室内安排：只选博物馆/展览馆/科技馆/商场/"
               "剧院/水族馆等室内场馆，禁止海滨/泳场/沙滩/公园/登山等户外露天景点")
        sk = await propose(self.llm, dest, str(len(rainy)), prefs, names, ask)
        redone = await ground(self.poi, sk, pool, meta,
                              dest=dest, prefs=prefs, raw_text=ask, llm=self.llm)
        for i, n in enumerate(rainy):
            if i >= len(redone.itinerary) or not redone.itinerary[i].stops:
                continue
            newday = redone.itinerary[i]
            newday.day_index = n
            old = trip.day(n)
            newday.weather = old.weather if old else None
            for idx, d in enumerate(trip.itinerary):
                if d.day_index == n:
                    trip.itinerary[idx] = newday
                    break
        trip = await solve(self.poi, trip, soc, meta)
        await self._save_trip(ctx, trip)
        speech, card = narrate(trip)
        attach(card, self.poi)  # trip_itinerary 卡盖 _prov 章（验收补口：此前全族无标）
        rainy_txt = "、".join(f"第{n}天" for n in rainy)
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"{rainy_txt}预计有雨，已把当天安排改成室内为主。{speech}\n\n确认按此调整吗？",
            ui_card=card, follow_up="说『确认』即可",
        ).action("trip.modify", {"modification": modification}, require_confirm=True)

    async def _apply_structural_edit(self, trip: Trip, modification: str, meta) -> bool:
        """结构化编辑：删/加某个具体停靠点。命中并改动返回 True；否则 False（交给重规划）。"""
        day_n = self._modify_day(modification)
        m = _REMOVE_RE.search(modification)
        if m:
            name = _DAY_PREFIX_RE.sub("", m.group(1)).strip("的了 ")
            if name and self._remove_stop(trip, name, day_n):
                return True
        m = _ADD_RE.search(modification)
        if m:
            name = _DAY_PREFIX_RE.sub("", m.group(1)).strip("的了 ")
            if name and await self._add_stop(trip, name, day_n, meta):
                return True
        # 换/调整第N天第M站 → 替换那个具体停靠点（根治"调整某站却返回原样"的 no-op）
        if await self._replace_stop(trip, modification, meta):
            return True
        return False

    @staticmethod
    def _remove_stop(trip: Trip, name: str, day_n: int = 0) -> bool:
        name = (name or "").strip()
        for dy in trip.itinerary:
            if day_n and dy.day_index != day_n:
                continue
            for k, s in enumerate(dy.stops):
                nm = s.name or ""
                if nm and (name in nm or nm in name):
                    dy.stops.pop(k)
                    return True
        return False

    async def _add_stop(self, trip: Trip, name: str, day_n: int, meta) -> bool:
        # 跨天去重：已在行程里就视为已满足，不重复加、也不触发重规划。
        for dy in trip.itinerary:
            for s in dy.stops:
                nm = s.name or ""
                if nm and (name in nm or nm in name):
                    return True
        poi = await _ground_one(self.poi, name, None, meta, self.llm)
        if not (poi and poi.lat and poi.lng):
            return False
        nstops = sum(len(d.stops) for d in trip.itinerary)
        stop = Stop(stop_id=f"s_add{nstops + 1}", name=poi.name or name,
                    type="attraction", dwell_min=120, source="user",
                    poi=_poi_to_dict(poi), grounded=True)
        target = trip.day(day_n) if day_n else min(
            trip.itinerary, key=lambda d: len(d.stops))
        (target or trip.itinerary[0]).stops.append(stop)
        return True

    async def _replace_stop(self, trip: Trip, modification: str, meta) -> bool:
        """换/调整「第N天第M站」：替换那个具体停靠点。指定『换成X』用 X，否则从池里挑一个
        行程没用过的不同景点（根治『调整第N站』整天重规划又挑回原样的 no-op）。"""
        if not any(k in modification for k in ("调整", "换", "改", "替换")):
            return False
        n = self._modify_day(modification)
        m = self._parse_ordinal(modification)
        day = trip.day(n) if n else None
        if not (n and m and day and m <= len(day.stops)):
            return False
        used = {s.name for d in trip.itinerary for s in d.stops}
        tm = _REPLACE_TARGET_RE.search(modification)
        if tm:                                   # 指定换成 X → 接地 X
            poi = await _ground_one(self.poi,
                                    tm.group(1).strip(), None, meta, self.llm)
        else:                                    # 没指定 → 池里挑一个没用过的不同景点
            pool = await self._pool_for_trip(
                trip, "、".join(trip.preferences), meta)
            poi = next((p for p in pool if p.name not in used and p.lat and p.lng), None)
        if not (poi and poi.lat and poi.lng):
            return False
        old = day.stops[m - 1]
        day.stops[m - 1] = Stop(stop_id=old.stop_id, name=poi.name, type=old.type,
                                dwell_min=old.dwell_min, source="user",
                                poi=_poi_to_dict(poi), grounded=True)
        return True

    async def _finalize(self, ctx, meta) -> AgentResult:
        """确认收尾：把行程第一个已接地停靠点作导航第一站，给候选 POI 让用户选『第几个』。
        绝不再 NEED_CONFIRM。状态置 confirmed 并持久化。"""
        trip = await self._load_trip(ctx)
        if not trip:
            return AgentResult(speech="好的，行程已确认，祝您旅途愉快！")

        dest = trip.destination
        day_txt = f"{trip.days}天" if trip.days else ""
        first = trip.first_stop()
        items, label = [], dest

        if first:
            label = first.name
            try:    # 实时搜第一站候选（如「天坛公园」多个门）供「第N个」就近导航
                results = await self.poi.search(first.name, limit=5, meta=meta)
                items = [{"id": r.id, "name": r.name, "address": r.address,
                          "rating": r.rating, "lat": r.lat, "lng": r.lng}
                         for r in results if r.name]
            except Exception as e:
                logger.warning("finalize first-stop search failed: %s", e)
            if not items and first.poi:     # 搜不到退化到接地时的 POI
                items = [first.poi]

        trip.status = "confirmed"
        await self._save_trip(ctx, trip)

        if items:
            names = "、".join(i["name"] for i in items[:3])
            return AgentResult(
                speech=f"好的，{dest}{day_txt}的行程已确认！第一站为您安排在「{label}」："
                       f"{names}。说『第几个』我就为您导航过去。",
                ui_card={"type": "poi_list", "title": f"{label} · 选择第一站",
                         "items": items},
                follow_up="说『第一个』即可开始导航")
        return AgentResult(
            speech=f"好的，{dest}{day_txt}的行程已确认，祝您和家人旅途愉快！"
                   f"出发时说『导航去{label}』我就为您开始导航。")

    # ── 在途导航：把行程里任意停靠点变成一句话可导航（P1）──────────
    async def _navigate(self, intent, ctx, meta) -> AgentResult:
        """导航到当前行程里的某个停靠点：『下一站』/『第N天的X』/『第N天第M个』/『行程里的X』。

        从持久化 Trip 取已接地停靠点，按指代定位后发 navigate 动作，并推进 cursor。
        无行程 → 引导先规划（普通导航仍由 navigation 处理，本意图只在确定性路由命中行程指代时触发）。
        """
        trip = await self._load_trip(ctx)
        if not trip or not trip.itinerary:
            return AgentResult(
                status=NEED_SLOT,
                speech="还没有规划好的行程，先告诉我去哪里玩几天，我规划好就能带您一站站去。",
                follow_up="例如：周末去杭州两天", missing_slots=["destination"])

        flat = self._flatten_grounded(trip)
        if not flat:
            return AgentResult(speech="行程里还没有可导航的具体地点。")

        raw = intent.raw_text or ""
        target_slot = (intent.slots.get("target") or "").strip()
        day_n = self._modify_day(intent.slots.get("day") or raw)
        ordinal = self._parse_ordinal(intent.slots.get("stop") or raw)
        is_next = (target_slot == "next" or "下一站" in raw or "下个" in raw
                   or "继续导航" in raw)

        picked = None
        if is_next:
            picked = self._next_after_cursor(trip, flat)
            if picked is None:
                return AgentResult(speech="行程已经到最后一站啦，没有下一站了。")
        else:
            name = target_slot or self._strip_nav_prefix(raw)
            if name:
                picked = self._find_by_name(flat, name, day_n)
            if picked is None and day_n:
                picked = self._find_by_day_ordinal(flat, day_n, ordinal or 1)
            if picked is None and not day_n and ordinal:
                # 「导航到第一站」不带天号：按全程序数跨天取（2026-08-14 批 A②，
                # 此前被 `and day_n` 短路，剥壳残留「第N站」被当店名找 → 恒「没找到」）
                picked = flat[min(ordinal, len(flat)) - 1]

        if picked is None:
            return AgentResult(
                speech="没找到您说的那一站，可以说『下一站』，或『第二天的西湖』。",
                follow_up="说『下一站』或『第N天的某地点』")

        dy, gi, stop = picked
        trip.cursor = {"day_index": dy, "stop_index": gi}
        await self._save_trip(ctx, trip)
        poi = stop.poi or {}
        payload = {"destination": stop.name, "lat": poi.get("lat"), "lng": poi.get("lng")}
        cur = current_location_from_meta(meta)
        if cur:
            payload.update(origin_lat=cur.lat, origin_lng=cur.lng)
        return AgentResult(
            speech=f"好的，为您导航到第{dy}天的{stop.name}。",
            data={"destination": stop.name, "lat": poi.get("lat"), "lng": poi.get("lng")},
        ).action("navigate", payload)

    # ── 在途状态查询（P2，只读）─────────────────────────────────
    async def _status(self, intent, ctx, meta) -> AgentResult:
        """在途进度：在第几站/下一站/还剩几站/全程补电几次。不改行程。

        C7-D（2026-08-28，QA P1-09）：**「第二天有哪些安排」以前答的是游标进度**。
        缺的只是读侧——`itinerary[].day_index` 与每站 stops 早就是结构化的，
        `_find_by_day_ordinal` 就在同一个文件里，`_status` 一个都没调（同 Q7 那条
        「写侧齐全、缺的是读侧」）。按天读走 `day` 槽，槽空时从原话兜底解析。
        """
        trip = await self._load_trip(ctx)
        if not trip or not trip.itinerary:
            return AgentResult(speech="您还没有规划行程。说『去某地玩几天』我就帮您安排。")
        day_n = self._status_day(intent)
        if day_n:
            return self._status_of_day(trip, day_n)
        flat = self._flatten_grounded(trip)
        total = len(flat)
        cur = trip.cursor or {}
        cd, ci = cur.get("day_index", 0), cur.get("stop_index", 0)
        pos = next((k for k, (d, i, _s) in enumerate(flat) if d == cd and i == ci), -1)
        remaining = flat[pos + 1:] if pos >= 0 else flat
        charge_total = sum(len(leg.charging_stops)
                           for dy in trip.itinerary for leg in dy.legs)
        parts = [f"您正在{trip.destination}{trip.days}天行程（共{total}站）"]
        if pos >= 0:
            parts.append(f"已到第{pos + 1}站「{flat[pos][2].name}」")
        if remaining:
            parts.append(f"下一站是「{remaining[0][2].name}」，后面还有{len(remaining)}站")
        else:
            parts.append("行程已全部走完")
        if charge_total:
            parts.append(f"全程需补电{charge_total}次")
        return AgentResult(
            speech="，".join(parts) + "。", ui_card=trip.card_dict(),
            data={"total": total, "remaining": len(remaining), "charging": charge_total})

    @staticmethod
    def _status_day(intent) -> int:
        """本轮问的是第几天：`day` 槽优先，槽空回退原话。解析不到返回 0。"""
        raw = str((intent.slots or {}).get("day") or "").strip()
        if raw.isdigit():
            return int(raw)
        return (TripPlannerAgent._modify_day(raw)
                or TripPlannerAgent._modify_day(intent.raw_text or ""))

    def _status_of_day(self, trip: Trip, day_n: int) -> AgentResult:
        """按天渲染这一天的停靠点。**没有这一天要如实说**，不要退回整程进度
        ——退回去就是把「答非所问」包装成「答了」。"""
        day = trip.day(day_n)
        if day is None:
            return AgentResult(
                speech=f"这趟{trip.destination}行程一共{trip.days}天，没有第{day_n}天。",
                ui_card=trip.card_dict())
        flat = self._flatten_grounded(trip)
        inday = [t[2] for t in flat if t[0] == day_n]
        if not inday:
            return AgentResult(
                speech=f"第{day_n}天还没有排上具体的地点。",
                ui_card=trip.card_dict())
        where = f"（{day.city}）" if day.city else ""
        names = "、".join(f"{i}. {s.name}" for i, s in enumerate(inday, 1))
        charge = sum(len(leg.charging_stops) for leg in day.legs)
        speech = f"第{day_n}天{where}共{len(inday)}站：{names}"
        if charge:
            speech += f"；这天需补电{charge}次"
        return AgentResult(
            speech=speech + "。", ui_card=trip.card_dict(),
            data={"day": day_n, "stops": [s.name for s in inday],
                  "charging": charge})

    # ── 在途重排：确定性精简剩余行程（P2）──────────────────────
    async def _reschedule(self, intent, ctx, meta) -> AgentResult:
        """时间不够/太累/想提前回 → 确定性砍尾部停靠点或最后一天，二次确认。"""
        if meta.get("confirmed") == "true":
            return await self._finalize(ctx, meta)
        trip = await self._load_trip(ctx)
        if not trip or not trip.itinerary:
            return AgentResult(
                status=NEED_SLOT, speech="还没有规划好的行程，您想去哪里玩几天？",
                follow_up="例如：周末去杭州两天", missing_slots=["destination"])
        hint = (intent.slots.get("hint") or intent.raw_text or "")
        if not self._trim_itinerary(trip, hint):
            return AgentResult(
                speech="行程已经很精简了，没有可再删减的安排啦。",
                ui_card=trip.card_dict())
        soc = await self._soc_pct(ctx, meta)
        trip = await solve(self.poi, trip, soc, meta)
        await self._save_trip(ctx, trip)
        speech, card = narrate(trip)
        attach(card, self.poi)  # trip_itinerary 卡盖 _prov 章（验收补口：此前全族无标）
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"已为您精简行程：{speech}\n\n确认按此调整吗？",
            ui_card=card, follow_up="说『确认』即可",
        ).action("trip.reschedule", {"hint": hint}, require_confirm=True)

    @staticmethod
    def _trim_itinerary(trip: Trip, hint: str) -> bool:
        """确定性精简：想提前回→删最后一天；时间不够/太累→每个剩余天删尾部一站。返回是否改动。"""
        h = hint or ""
        if (any(k in h for k in ("提前回", "早点回", "早些回", "少一天", "回家"))
                and len(trip.itinerary) > 1):
            trip.itinerary.pop()
            return True
        cd = (trip.cursor or {}).get("day_index", 0)
        changed = False
        for dy in trip.itinerary:
            if dy.day_index < cd:               # 已过的天不动
                continue
            if len(dy.stops) > 1:
                dy.stops.pop()
                changed = True
        return changed

    @staticmethod
    def _flatten_grounded(trip: Trip) -> list:
        """按天序展开所有已接地停靠点：[(day_index, grounded_idx_in_day, Stop)]。"""
        out = []
        for dy in trip.itinerary:
            gi = 0
            for s in dy.stops:
                if getattr(s, "grounded", False) and (s.poi or {}).get("lat"):
                    out.append((dy.day_index, gi, s))
                    gi += 1
        return out

    @staticmethod
    def _next_after_cursor(trip: Trip, flat: list):
        """cursor 之后的下一站；cursor 未命中（初始 0,0）→ 首站；已是末站 → None。"""
        cur = trip.cursor or {}
        cd, ci = cur.get("day_index", 0), cur.get("stop_index", 0)
        for k, (d, i, _s) in enumerate(flat):
            if d == cd and i == ci:
                return flat[k + 1] if k + 1 < len(flat) else None
        return flat[0] if flat else None

    @staticmethod
    def _find_by_day_ordinal(flat: list, day_n: int, m: int):
        inday = [t for t in flat if t[0] == day_n]
        if not inday:
            return None
        idx = max(1, m) - 1
        return inday[idx] if idx < len(inday) else inday[-1]

    @staticmethod
    def _find_by_name(flat: list, name: str, day_n: int = 0):
        name = (name or "").strip()
        if not name:
            return None
        scoped = [t for t in flat if (not day_n or t[0] == day_n)]
        for pool in (scoped, flat):           # 先按指定天找，再跨天兜底
            for t in pool:
                nm = t[2].name or ""
                if name in nm or nm in name:
                    return t
        return None

    @staticmethod
    def _parse_ordinal(text: str) -> int:
        m = _ORDINAL_RE.search(text or "")
        if not m:
            return 0
        tok = m.group(1)
        return int(tok) if tok.isdigit() else _CN_NUM.get(tok, 0)

    @staticmethod
    def _strip_nav_prefix(raw: str) -> str:
        """从『导航去第二天的西湖』剥成『西湖』；非具体地点指代返回空。"""
        t = (raw or "").strip()
        for p in ("导航去", "导航到", "导航", "带我去", "去", "到"):
            if t.startswith(p):
                t = t[len(p):]
                break
        t = re.sub(r"^第\s*[一二两三四五六七八九十0-9]+\s*天的?", "", t)
        t = re.sub(r"^第\s*[一二两三四五六七八九十0-9]+\s*[个站]", "", t)
        t = re.sub(r"^行程(里|中)?的?", "", t).strip("的里中 ，。")
        return "" if t in ("下一站", "下个", "行程", "") else t

    @staticmethod
    def _modify_day(text: str) -> int:
        """从修改话术解析「第N天」的天号；解析不到返回 0。"""
        m = _MOD_DAY_RE.search(text or "")
        if not m:
            return 0
        tok = m.group(1)
        return int(tok) if tok.isdigit() else _CN_NUM.get(tok, 0)
