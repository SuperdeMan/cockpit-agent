"""导航 Agent —— 所有 Agent 的参考范本。

演示：意图分发、缺槽位追问(NEED_SLOT)、按引用取上下文(ctx.fetch)、
产出动作(action) 与 HMI 卡片(ui_card)。
Phase 1：使用 Provider 适配层（mock/real 可切换）。
"""
from __future__ import annotations
import json
import logging
import math
import os
import re
import time

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta
from agents._sdk.provenance import attach
from agents._sdk.shared_state import REMINDABLE_ACTIVE
from agents._sdk.landmark import (
    is_landmark_description, landmark_candidates, name_matches)
from agents._sdk.timewindow import parse_clock_time
from .providers import build_poi_provider
from .providers.base import GeoPoint, POI

logger = logging.getLogger("agent.navigation")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

# 常用地点别名 → (画像 key, 中文标签)。精确匹配整段目的地，避免误伤含字地名。
_PLACE_ALIASES: dict[str, tuple[list[str], str]] = {
    "home": (["家", "我家", "回家", "家里"], "家"),
    "company": (["公司", "单位", "我公司", "我单位"], "公司"),
    "school": (["学校", "我的学校"], "学校"),
}


def _match_place_alias(text: str) -> tuple[str | None, str]:
    """目的地是否是常用地点别名。精确匹配 → (key, 标签)，否则 (None, '')。"""
    t = (text or "").strip().rstrip("。，,. ")
    for key, (aliases, label) in _PLACE_ALIASES.items():
        if t in aliases:
            return key, label
    return None, ""


# ── M2 记忆图谱 P1：人称目的地（「去接孩子放学」）────────────────────────
# 人称词表与 memory/relation.py::_KINSHIP_SYNONYMS 同源（那边是权威登记，这里只做
# 消费侧识别；跨服务不共享代码，保持各自可独立部署）。
# 含裸「妈/爸」：口语里「去接我妈」比「去接妈妈」更常见（真栈首验实测漏掉）。
# 单字不会误伤——`_person_destination` 要求「去掉人称词与填充词后没有实质内容」，
# 「大妈」剥掉「妈」剩「大」即不触发。
_PERSON_WORDS = ("女儿", "闺女", "儿子", "孩子", "娃", "小孩",
                 "老婆", "妻子", "太太", "媳妇", "老公", "丈夫",
                 "妈妈", "母亲", "老妈", "妈", "爸爸", "父亲", "老爸", "爸")
_PERSON_RE = re.compile("|".join(sorted(_PERSON_WORDS, key=len, reverse=True)))
# 目的地里去掉人称词后剩下的"非实质内容"：动词、泛称地点、方位词。剩下这些说明用户
# 没给具体地名（"接孩子"/"孩子的学校"），才需要走关系边解析。
# 含人称代词「我/你/他/她」：「接我妈」里的「我」不是地点信息（真栈首验实测漏掉——
# 剥完「妈」剩个「我」被当成实质内容，整条链路不触发）。
_PERSON_FILLER_RE = re.compile(
    r"导航|接|送|去|到|的|我|你|他|她|那边|那儿|附近|学校|幼儿园|放学|上学|下课|单位|公司|家|"
    r"所在|地方|一下|吧|呢")


# 裸称谓 → 播报用的自然说法（「我还不知道**妈**平时在哪」读着别扭，真栈实测）
_PERSON_DISPLAY = {"妈": "妈妈", "爸": "爸爸", "娃": "孩子", "小孩": "孩子"}

# set_place 人称守卫（P4）：长词直接算 + 单字「妈/爸」只认「我妈/你爸」代词组合——
# 裸单字会误伤「妈湾路」这类地名。
_SET_PLACE_PERSON_RE = re.compile(
    "|".join([re.escape(w) for w in _PERSON_WORDS if len(w) >= 2]
             + [r"[我你他她][妈爸]"]))


def _person_display(word: str) -> str:
    return _PERSON_DISPLAY.get(word, word)


def _person_destination(dest: str) -> str:
    """目的地是否只是个人称（需走关系边解析）→ 返回人称词，否则空串。

    **判据是「去掉人称词与填充词后还剩不剩实质内容」**：
    - 「孩子」/「接孩子」/「孩子的学校」→ 剩空 → 触发解析
    - 「孩子学校旁边的星巴克」→ 剩「旁边星巴克」→ **不触发**（用户已给具体地点，
      改写它就是帮倒忙）
    - 「XX小学」→ 无人称词 → 不触发
    """
    t = (dest or "").strip()
    if not t:
        return ""
    m = _PERSON_RE.search(t)
    if not m:
        return ""
    rest = _PERSON_FILLER_RE.sub("", _PERSON_RE.sub("", t)).strip("。，,. 、")
    return m.group(0) if not rest else ""


# "最近的/附近的X" 这类就近查询依赖当前位置；无定位时不应拿任意城市冒充"最近"。
_PROXIMITY_RE = re.compile(r"最近|附近|周边|就近|离我")
# 剥掉就近前缀，留类目关键词（"附近的粤菜馆"→"粤菜馆"）。否则高德按整句"附近的粤菜馆"
# 找同名 POI 必然落空（"暂时无法确定"），或匹配到远处无关结果。
_PROXIMITY_PREFIX_RE = re.compile(r"^(离我)?\s*(最近|附近|周边|就近)的?\s*")


def _strip_proximity(dest: str) -> str:
    stripped = _PROXIMITY_PREFIX_RE.sub("", dest or "").strip()
    return stripped or dest


# ── G1 时间约束（EVA 二轮批 B）：「五点前到」到达时限的确定性解析 ──────────
# E1：时刻解析本体已下沉 `agents/_sdk/timewindow.py`（nearby 的用餐窗反推要用同一套
# 消歧语义——判定抄两份正是 B1 那个 bug 的成因）。本模块只留**到达时限**这层语义门。
_parse_arrive_by = parse_clock_time      # 名字保持（测试与调用点逐字不变）
# 原话兜底门：时刻 + 「前/之前」或「N点(我)要/得/必须到」的到达措辞——
# 只认到达时限句式，不把「三点半的会」这类顺带提到的时间当成时限。
_ARRIVE_RAW_RE = re.compile(
    r"(?:上午|早上|凌晨|中午|下午|傍晚|晚上)?\s*"
    r"(?:\d{1,2}[:：]\d{2}|(?:十一|十二|\d{1,2}|[一两二三四五六七八九十])\s*点(?:半|\d{1,2}分)?)"
    r"\s*(?:之前|以前|前|我?(?:要|得|必须)到)")


# ── G11 路线策略：route_pref 槽/原话 → 高德 v3 driving strategy ────────────
_PREF_JAM_RE = re.compile(r"避堵|躲避拥堵|避开拥堵|避免拥堵|不要?堵|别堵")
_PREF_NO_HW_RE = re.compile(r"不走高速|不上高速|避开高速|少走高速|别走高速")
_PREF_TOLL_RE = re.compile(r"少收费|避免收费|不走收费|躲避收费|省点?钱|免费路")
_PREF_SCENIC_RE = re.compile(r"风景|景色|景观路")
_PREF_EXPRESS_RE = re.compile(r"不走快速路")


def _route_strategy(text: str) -> tuple[str, str]:
    """路线偏好 →（高德 strategy 值, 已应用偏好的话术片段）。无偏好 → ("", "")。

    「风景好的路」高德没有景观优先策略：降档为不走高速的大路并**如实说**（不假装有）。
    """
    t = text or ""
    jam, no_hw = bool(_PREF_JAM_RE.search(t)), bool(_PREF_NO_HW_RE.search(t))
    toll, scenic = bool(_PREF_TOLL_RE.search(t)), bool(_PREF_SCENIC_RE.search(t))
    if scenic and not (jam or no_hw or toll):
        return "6", "您想走风景好些的路——地图没有景观优先策略，已按不走高速的大路规划，沿途更从容。"
    if no_hw and toll and jam:
        return "9", "已按您的偏好避开高速、收费和拥堵。"
    if no_hw and toll:
        return "7", "已按您的偏好避开高速和收费路段。"
    if toll and jam:
        return "8", "已按您的偏好躲避收费和拥堵。"
    if no_hw:
        return "6", "已按您的偏好避开高速。"
    if jam:
        return "4", "已为您优先避开拥堵路段。"
    if toll:
        return "1", "已按费用优先规划（少走收费路）。"
    if _PREF_EXPRESS_RE.search(t):
        return "3", "已按您的偏好不走快速路。"
    return "", ""


# ── G8 增量改道：话术的确定性解析（planner 未填槽时兜底）─────────────────
# 删：「咖啡不买了」「刚才那个途经点不去了」（A：目标在删除词前）/「不去肯德基了」
# 「取消途经点」（B：目标在删除词后）。捕获后 strip 尾部助词；泛指词（途经点/那个…）
# 由消费侧兑现成「删最近加入的一个」。
_REROUTE_REMOVE_A_RE = re.compile(
    r"([^，。,、\s]{1,12}?)(?:就?先?不去了|不买了|不要了|不吃了|不喝了)")
_REROUTE_REMOVE_B_RE = re.compile(r"(?:取消|删掉|去掉|不去)\s*([^，。,、\s]{1,12})")
_REROUTE_GENERIC_WP_RE = re.compile(r"途经点|停靠点|那个|那里|那儿|刚才")
# 加：「先去加油站」「顺路再加个超市」。「先…」语义=插到途经点首位。
_REROUTE_ADD_RE = re.compile(
    r"(?:先去|先到|先买杯?|先加个?|顺路去|顺路买杯?|顺路加个?|顺道去|加个|加一个|"
    r"再加个?|多加个?|顺便去|再去)\s*([^，。,、\s]{1,12})")
# 换路（无明确偏好）：「换条路」「别走这条」。带偏好的（避堵/不走高速）走 _route_strategy。
_REROUTE_CHANGE_ROUTE_RE = re.compile(r"换条|换一条|换个路线|别走这|重新规划路线|换路")
# 改目的地：「改去COCO Park」「目的地换成宝安机场」。
_REROUTE_DEST_RE = re.compile(
    r"(?:目的地)?(?:改去|改到|改成|换成)去?\s*([^，。,、]{2,20})")


def _rating_policy(value, raw_text: str) -> tuple[float, bool]:
    """Normalize planner rating slots and retain superlative ordering semantics."""
    text = str(value or "").strip()
    try:
        rating_min = float(text) if text else 0.0
    except (TypeError, ValueError):
        rating_min = 0.0
    prefer_highest = any(
        marker in f"{text} {raw_text or ''}"
        for marker in ("最高", "评分高", "高评分", "从高到低")
    )
    return rating_min, prefer_highest


class NavigationAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.poi = build_poi_provider()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "navigation.search_poi": self._search_poi,
            "navigation.navigate_to": self._navigate_to,
            "navigation.reroute": self._reroute,
            "navigation.reverse_geocode": self._reverse_geocode,
            "navigation.poi_detail": self._poi_detail,
            "navigation.set_place": self._set_place,
            "navigation.locate": self._locate,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="抱歉，这个导航请求我还不会处理。")

    async def _current_position(self, ctx, meta) -> GeoPoint | None:
        """当前位置统一只取本轮已授权的浏览器 GPS——与天气、「我在哪」一致，避免三处定位打架。
        PoC 没有真实车机 GPS（memory 的 vehicle.location 是 mock 上海），回退它会给出误导结果
        且与天气不一致；故不再回退，无授权返回 None，由调用方诚实提示开启定位。"""
        current = current_location_from_meta(meta)
        if current:
            return GeoPoint(lat=current.lat, lng=current.lng)
        return None

    @staticmethod
    def _arrive_by_from(intent, raw_text: str) -> int | None:
        """arrive_by 槽优先（planner 填的时间原话），原话「…前/我要到」句式确定性兜底。"""
        slot = (intent.slots.get("arrive_by") or "").strip()
        if slot:
            ts = _parse_arrive_by(slot)
            if ts:
                return ts
        m = _ARRIVE_RAW_RE.search(raw_text or "")
        return _parse_arrive_by(m.group(0)) if m else None

    @staticmethod
    def _fmt_clock(ts) -> str:
        lt = time.localtime(int(ts))
        return f"{lt.tm_hour:02d}:{lt.tm_min:02d}"

    def _deadline_note(self, duration_min, arrive_by_ts) -> tuple[str, dict]:
        """ETA vs 到达时限的判定（G1）→（话术片段, data/卡片附加字段）。任一缺失 → ("", {})。"""
        if not (arrive_by_ts and duration_min):
            return "", {}
        eta = int(time.time()) + int(float(duration_min) * 60)
        margin = round((int(arrive_by_ts) - eta) / 60)
        extra = {"eta_ts": eta, "arrive_by_ts": int(arrive_by_ts), "margin_min": margin}
        clock, want = self._fmt_clock(eta), self._fmt_clock(arrive_by_ts)
        if margin >= 5:
            return f"预计{clock}到达，比您要求的{want}早约{margin}分钟。", extra
        if margin >= 0:
            return f"预计{clock}到达，刚好赶上您要求的{want}，别耽搁太久。", extra
        return (f"照当前路线预计{clock}到达，比您要求的{want}晚约{-margin}分钟；"
                "建议尽快出发，或说「帮我换避堵路线」。", extra)

    @staticmethod
    def _navigate_payload(destination: str, lat: float, lng: float, meta: dict | None) -> dict:
        """构建导航动作；仅携带本轮已授权的精确起点。"""
        payload = {"destination": destination, "lat": lat, "lng": lng}
        current = current_location_from_meta(meta)
        if current:
            payload.update({"origin_lat": current.lat, "origin_lng": current.lng})
        return payload

    async def _search_poi(self, intent, ctx, meta) -> AgentResult:
        keyword = intent.slots.get("keyword") or intent.slots.get("category")
        if not keyword:
            return AgentResult(status=NEED_SLOT, speech="您想找什么类型的地点呢？",
                               follow_up="请提供搜索关键词，如『充电站』『川菜馆』")

        # 按引用取车辆当前位置（隐私最小化：只取需要的 scope）
        near = await self._current_position(ctx, meta)

        raw_text = (intent.raw_text or "").strip()
        rating_min, prefer_highest = _rating_policy(
            intent.slots.get("rating_min"),
            raw_text,
        )
        # 真实 provider 运行期失败 → 诚实降级说拿不到（架构 §9.5 铁律③）：绝不改供 mock
        # 假 POI（可能被用户导航过去）。R9 契约：话术用 OK 返回（FAILED 会被聚合器吞成裸报错）。
        try:
            results = await self.poi.search(keyword, near=near, rating_min=rating_min, meta=meta)
        except ProviderError as e:
            logger.warning("poi search failed（诚实降级，无 mock 回退）: %s", e)
            return AgentResult(
                speech=f"地图服务暂时不可用，没查到「{keyword}」，请稍后再试。",
                follow_up="稍后再说一次就行")
        resolved_keyword = keyword

        # 设施类目搜索（充电站/加油站/停车场…）按本步关键词如实搜附近，不得被整句多意图
        # 原文的地标解析劫持，也不自动导航到首个结果——否则多意图“导航去X + 找充电桩”里
        # 找充电桩的子步会被整句改写成导航到 X（双 navigate、卡片串味）。
        is_category = self._is_category_search(keyword, intent.slots.get("category") or "")

        # Planner 有时会把“去深圳笋一样的建筑物”误抽成“笋岗”这类普通关键词。
        # 视觉地标描述即使碰巧命中一个同名普通 POI，也要优先由地图验证语义候选；
        # 候选名已含城市/正式名称，不能受车辆当前城市的周边检索范围限制。
        is_visual_landmark = (not is_category) and self._is_visual_landmark_description(raw_text)
        if raw_text and not is_category and (not results or is_visual_landmark):
            for candidate in await self._landmark_candidates(raw_text):
                try:
                    candidate_results = await self.poi.search(
                        candidate,
                        near=None if is_visual_landmark else near,
                        rating_min=rating_min,
                        meta=meta,
                    )
                except ProviderError as e:
                    logger.warning("semantic POI candidate search failed: %s", e)
                    continue
                # 同 _find_destination：拒绝高德对非官方名返回的邻近无关 POI
                if candidate_results and name_matches(candidate, candidate_results[0].name):
                    resolved_keyword, results = candidate, candidate_results
                    break

        if prefer_highest:
            results = sorted(results, key=lambda item: item.rating or 0, reverse=True)

        items = [{"id": r.id, "name": r.name, "rating": r.rating,
                  "distance_km": r.distance_km, "address": r.address,
                  "lat": r.lat, "lng": r.lng} for r in results]
        card = attach({"type": "poi_list", "keyword": resolved_keyword, "items": items},
                      self.poi)

        if results and not is_category and self._is_navigation_phrase(raw_text):
            first = results[0]
            # G6 轨迹写入也要挂这条自动导航路径——真栈「圆圆的湖→滴水湖」走的
            # 正是这里，漏挂则「上次去过的那个湖」无数据可召回（挂点枚举教训）。
            await self._remember_visited(ctx, first.name, first.lat, first.lng)
            return self._stamp_route_session(AgentResult(
                speech=f"识别到您说的是{first.name}（{first.address}）。已为您规划路线。",
                ui_card=card, data={"items": items},
            ).action("navigate", self._navigate_payload(
                first.name, first.lat, first.lng, meta)),
                first.name, first.lat, first.lng)

        names = "、".join(r.name for r in results[:3])
        return AgentResult(
            speech=f"为您找到 {len(results)} 个{resolved_keyword}，推荐前三个：{names}。需要导航过去吗？",
            ui_card=card,
            data={"items": items},  # F3：结构化结果供编排 slot_refs 取值（如 s1.data.items.0.id）
            follow_up="可以说『导航去第一个』",
        )

    @staticmethod
    def _is_navigation_phrase(text: str) -> bool:
        return (text or "").strip().startswith(("导航", "去", "到", "带我去"))

    # 设施类目关键词：这类搜索按本步关键词如实搜附近，不走整句地标解析、不自动导航
    _CATEGORY_MARKERS = (
        "充电", "快充", "慢充", "超充",
        "加油", "加气", "加氢",
        "停车", "车位",
        "超市", "便利店",
        "厕所", "卫生间", "洗手间", "公厕",
        "服务区", "药店", "医院", "银行", "atm",
    )

    @classmethod
    def _is_visual_landmark_description(cls, text: str) -> bool:
        """带明显视觉/地标描述的导航请求（含动词前缀）→ 语义候选优先。"""
        normalized = (text or "").strip()
        if not cls._is_navigation_phrase(normalized):
            return False
        return is_landmark_description(normalized)

    @classmethod
    def _is_category_search(cls, keyword: str, category_slot: str = "") -> bool:
        """本步是否为设施类目搜索（充电站/加油站/停车场…）。"""
        if (category_slot or "").strip():
            return True
        k = (keyword or "").strip().lower()
        return any(marker in k for marker in cls._CATEGORY_MARKERS)

    # 顺路停靠类目（吃饭/咖啡…）→ 高德搜索关键词
    _STOP_CATEGORY_KEYWORDS = {
        "吃饭": "餐厅", "餐厅": "餐厅", "饭店": "餐厅", "美食": "餐厅", "吃的": "餐厅",
        "咖啡": "咖啡", "奶茶": "奶茶饮品", "加油": "加油站",
        "厕所": "公共厕所", "卫生间": "公共厕所", "超市": "超市", "便利店": "便利店",
    }
    # raw_text 里的途经点兜底解析（planner 未填 waypoint 槽位时）。
    # G9：捕获组允许 、/和/与/及 连写多个途经点（「途经肯德基和星巴克」），消费侧拆分保序。
    _WAYPOINT_RE = re.compile(
        r"(?:途经|途径|经过|顺路去|顺道去|路过)\s*"
        r"([^，。,、\s]+(?:[、和与及][^，。,、\s]+)*)")
    # raw_text 里的"顺路停靠"兜底识别（planner 未填 stop_category，或误拆出 food 步时）
    _STOP_RAW_RE = re.compile(
        r"(?:附近|周边|顺路|顺道|沿途|中途|途中|路上|那边|那儿|路过)[^，。,、]{0,8}?"
        r"(餐厅|饭店|吃饭|吃的|美食|川菜|火锅|咖啡|奶茶|小吃|加油|充电)")

    @classmethod
    def _stop_keyword(cls, category: str) -> str:
        c = (category or "").strip()
        for k, v in cls._STOP_CATEGORY_KEYWORDS.items():
            if k in c:
                return v
        return c

    async def _navigate_to(self, intent, ctx, meta) -> AgentResult:
        dest = intent.slots.get("destination", "").strip()
        raw_text = (intent.raw_text or "").strip()
        if not dest:
            # 槽位为空时，尝试用 raw_text 做模糊搜索（处理"导航到上海那个像船一样的建筑"）
            raw = raw_text
            for prefix in ("导航到", "导航去", "导航", "带我去", "去", "到"):
                if raw.startswith(prefix):
                    raw = raw[len(prefix):].strip()
                    break
            raw = self._WAYPOINT_RE.sub("", raw).strip("，。, 、")  # 去掉"途经X"尾巴，不污染目的地
            if raw:
                dest = raw
        if not dest:
            return AgentResult(status=NEED_SLOT, speech="您要去哪里？", follow_up="请告诉我目的地")

        # G1/G11：到达时限（「五点前到」）与路线偏好（「不走高速/避堵」）——slot 优先、
        # 原话确定性兜底；解析结果贯穿本次导航的全部路径（普通/顺路停靠/途经点/常用地点）。
        arrive_by_ts = self._arrive_by_from(intent, raw_text)
        strategy, strategy_note = _route_strategy(
            f"{intent.slots.get('route_pref') or ''} {raw_text}")
        if not strategy:
            # G6 消费环：本次没说偏好 → 查记忆里的路线偏好（「以后都不走高速」说过
            # 一次就一直生效）。route.* 谓词此前全仓零消费方，这里是它的第一个出口。
            strategy, strategy_note = await self._route_pref_from_memory(ctx)

        # planner 臆断修正：见 _correct_planner_landmark（把 planner 错猜的具体楼名换回真地标官方名）。
        dest = await self._correct_planner_landmark(dest, raw_text, meta)

        # M2 记忆图谱 P1：人称目的地一跳解析（「去接孩子放学」→ 孩子=小雨 → 小雨在 XX 小学）。
        # 这是母提案 §1.2-E2 的 Eva 例子，也是关系边唯一非做不可的消费面。
        person_word = _person_destination(dest) or _person_destination(raw_text)
        if person_word:
            hit = await ctx.resolve_person_place(person_word)
            if hit and hit.get("place"):
                logger.info("person destination resolved: %s → %s(%s)",
                            person_word, hit["place"], hit.get("person", ""))
                dest = hit["place"]
            else:
                # **诚实追问，绝不猜**：不知道「孩子」是谁/在哪时，导航到错地方比问一句更糟。
                who = _person_display(person_word)
                return AgentResult(
                    status=NEED_SLOT,
                    speech=f"我还不知道你{who}平时在哪，你说个地方我就记住了。",
                    follow_up=f"可以说「我{who}在XX上班」或「我{who}在XX小学上学」，"
                              "以后我就能直接带你去。",
                    missing_slots=["destination"])

        # 常用地点（家/公司/学校）：命中别名先走画像，未设置则二次交互让用户设置。
        place_key, place_label = _match_place_alias(dest)
        if place_key:
            place_address = (intent.slots.get("place_address") or "").strip()
            if place_address:
                # 二次交互续接：用户给了地址 → 设为该常用地点并直接导航过去。
                return await self._set_place_and_go(
                    place_key, place_label, place_address, ctx, meta, navigate=True)
            stored = await self._get_place(ctx, place_key)
            if stored:
                # "导航回家，途中找个咖啡店"：常用地点同样支持途经点/顺路停靠，别丢这层意图。
                stored_poi = POI(
                    id=f"place_{place_key}", name=stored.get("name") or place_label,
                    address=stored.get("address") or "",
                    lat=stored.get("lat"), lng=stored.get("lng"))
                items = [stored]
                waypoint = (intent.slots.get("waypoint") or "").strip()
                if not waypoint:
                    m = self._WAYPOINT_RE.search(raw_text)
                    if m:
                        waypoint = m.group(1).strip()
                if waypoint:
                    return await self._navigate_via_waypoint(
                        stored_poi, place_label, waypoint, items, meta,
                        arrive_by_ts=arrive_by_ts, strategy=strategy,
                        strategy_note=strategy_note, ctx=ctx)
                stop_category = (intent.slots.get("stop_category") or "").strip()
                if not stop_category:
                    m = self._STOP_RAW_RE.search(raw_text)
                    if m:
                        stop_category = m.group(1)
                if stop_category:
                    return await self._navigate_with_stop_choice(
                        stored_poi, place_label, stop_category, items, meta,
                        arrive_by_ts=arrive_by_ts, strategy=strategy,
                        strategy_note=strategy_note)
                return await self._navigate_to_stored(
                    place_label, stored, meta, ctx=ctx, arrive_by_ts=arrive_by_ts,
                    strategy=strategy, strategy_note=strategy_note)
            example = "深圳科技园" if place_key == "company" else "上海长宁区某某小区"
            return AgentResult(
                status=NEED_SLOT,
                speech=f"您还没有设置「{place_label}」的位置，请告诉我{place_label}的地址。",
                follow_up=f"比如说『{example}』，我记住后直接带您过去。",
                missing_slots=["place_address"],
            )

        # 带当前位置就近解析目的地（"最近的/附近的粤菜馆"按距离排序）；无定位则 near=None。
        near = await self._current_position(ctx, meta)
        # 就近查询("最近的/附近的X")：X 是【目的地类目】——无定位→诚实提示开启定位（不拿任意城市
        # 冒充"最近"）；有定位→剥掉就近前缀按当前位置周边搜。只看 dest——「东方之门，附近找吃饭」
        # 里的"附近"指目的地周边停靠(顺路用餐流程)，dest 是"东方之门"，不该误触。
        is_proximity = bool(_PROXIMITY_RE.search(dest))
        if is_proximity:
            if near is None:
                return AgentResult(
                    speech="找最近的地点要先知道您在哪。请在设置里开启定位授权，我就按当前位置帮您就近找。",
                    follow_up="开启定位后再说一次『最近的…』")
            dest = _strip_proximity(dest)  # "附近的粤菜馆" → "粤菜馆"，按当前位置周边搜
        # "换一批"翻页：HMI 在续问时带上 meta.poi_page，取下一页不同候选。
        try:
            page = max(1, int((meta or {}).get("poi_page", 1)))
        except (TypeError, ValueError):
            page = 1
        # D4（接地卡 2026-08-14）：历史指代（「上次去过的那个湖」）→ episodic 轨迹
        # 坐标直取，不重搜。planner 召回把 dest 填对了名（滴水湖），重搜却把最后
        # 一跳交回就近偏置（真栈接到雅悦酒店）——去过的地方坐标本来就在轨迹里。
        visited = None
        history_ref = bool(not is_proximity and self._HISTORY_REF_RE.search(raw_text))
        if history_ref:
            visited = await self._visited_coords_from_memory(ctx, dest)
        # 历史指代 + **描述性** dest（「看夜景的那个地方」——planner 没解出名，原话
        # 整句进了槽，2026-08-15 真栈实测）且轨迹名匹配不上 → 把最近去过的地方列成
        # 候选让用户挑。确定性消费 episodic、零猜测：比「暂时无法确定」多给一步，
        # 又不替用户拍板（D4「描述性 dest 不做语义猜」纪律不破——挑的人是用户）。
        # 「上次去的星巴克」这类真专名不命中描述判定，照常走搜索。
        if visited is None and history_ref and self._DESCRIPTIVE_DEST_RE.search(dest):
            recent = await self._recent_visited_places(ctx)
            if recent:
                items = [{"id": f"episodic_{i}", "name": p["name"],
                          "address": "您去过的地方", "lat": p["lat"], "lng": p["lng"]}
                         for i, p in enumerate(recent)]
                names = "、".join(p["name"] for p in recent[:5])
                return AgentResult(
                    speech=f"您最近去过这些地方：{names}。您说的是哪一个？"
                           "说『第几个』即可。",
                    ui_card=attach({"type": "poi_list", "keyword": "最近去过",
                                    "items": items}, self.poi),
                    data={"items": items},
                    follow_up="说『第一个』『第二个』选择目的地")
        if visited:
            resolved_name = visited[0]
            results = [POI(id="episodic_place", name=visited[0],
                           address="您去过的地方", lat=visited[1], lng=visited[2])]
        else:
            # strict=False：就近类目（「最近的粤菜馆」）的结果店名天然不含类目词，
            # 不做 R1 强校验（否则每次类目导航都白跑一轮去偏置重搜 + 地标 LLM）。
            resolved_name, results = await self._find_destination(
                dest, meta, near=near, limit=5 if is_proximity else 3, page=page,
                strict=not is_proximity)
        if not results:
            if is_proximity:
                if page > 1:  # "换一批"翻到底了
                    return AgentResult(
                        speech=f"附近没有更多{dest}了，从前面给您的几家里挑一个吧。")
                return AgentResult(speech=f"附近暂时没找到{dest}，换个类型试试？")
            return AgentResult(
                status=NEED_SLOT,
                speech=f"暂时无法确定「{dest}」对应的具体地点。",
                follow_up="请补充城市、所在区域，或附近的地标，我再为您定位。",
                missing_slots=["destination"],
            )

        first = results[0]
        items = [{"id": r.id, "name": r.name, "rating": r.rating,
                  "distance_km": r.distance_km, "address": r.address,
                  "lat": r.lat, "lng": r.lng} for r in results]

        # 类目目的地（"最近的/附近的粤菜馆"）：附近这几家是【可选目的地】，不是顺路途经点。
        # 列出来让用户选哪家作目的地（plain poi_list，无 purpose → HMI「第N个」改写成
        # 「导航去{名称}」直接设为目的地），不走顺路停靠/途经点流程（那是"导航去X，途中找Y"语义）。
        if is_proximity:
            names = "、".join(r.name for r in results[:3])
            more = f" 等{len(results)}家" if len(results) > 3 else ""
            return AgentResult(
                speech=f"附近为您找到这些{dest}：{names}{more}。想去哪一家？说『第几个』即可。",
                ui_card=attach({"type": "poi_list", "keyword": dest, "items": items},
                               self.poi),
                data={"items": items},
                follow_up="说『第一个』『第二个』选择目的地",
            )

        # 轮2：已选途经点（slot 或 raw_text 的"途经X"）→ 解析坐标并入 navigate
        waypoint = (intent.slots.get("waypoint") or "").strip()
        if not waypoint:
            m = self._WAYPOINT_RE.search(raw_text)
            if m:
                waypoint = m.group(1).strip()
        if waypoint:
            return await self._navigate_via_waypoint(
                first, resolved_name, waypoint, items, meta,
                arrive_by_ts=arrive_by_ts, strategy=strategy,
                strategy_note=strategy_note, ctx=ctx)

        # 轮1：顺路停靠类目（吃饭/咖啡…）→ 导航到目的地 + 给候选让用户二次选择。
        # planner 未填 stop_category 槽位时，从 raw_text"附近/顺路…餐厅/吃饭"兜底识别——
        # 即便 planner 误把找餐厅拆成 nearby.search，导航侧也能自己产出真实餐厅
        # 途经点候选（聚合器优先 waypoint_choice 卡）。
        stop_category = (intent.slots.get("stop_category") or "").strip()
        if not stop_category:
            m = self._STOP_RAW_RE.search(raw_text)
            if m:
                stop_category = m.group(1)
        if stop_category:
            return await self._navigate_with_stop_choice(
                first, resolved_name, stop_category, items, meta,
                arrive_by_ts=arrive_by_ts, strategy=strategy, strategy_note=strategy_note)

        # 普通导航：出路线规划卡（当前位置 → 目的地，起终点 + best-effort 距离/时长）
        prefix = (f"识别到您说的是{first.name}。" if resolved_name != dest else "")
        return await self._route_plan_to(
            first.name, first.address, first.lat, first.lng, meta,
            resolved_prefix=prefix, ctx=ctx, arrive_by_ts=arrive_by_ts,
            strategy=strategy, strategy_note=strategy_note)

    # ── 常用地点（家/公司/学校）──────────────────────────────
    async def _get_places(self, ctx) -> dict:
        """从用户画像读常用地点 map。失败/未设置返回空 dict。"""
        try:
            vals = await ctx.fetch("profile.places")
        except Exception as e:  # 画像不可用不应阻断导航
            logger.warning("fetch profile.places failed: %s", e)
            return {}
        raw = vals.get("profile.places")
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    async def _get_place(self, ctx, place_key: str) -> dict | None:
        place = (await self._get_places(ctx)).get(place_key)
        return place if isinstance(place, dict) and place.get("lat") is not None else None

    async def _navigate_to_stored(self, label: str, stored: dict, meta,
                                  ctx=None, arrive_by_ts=None, strategy: str = "",
                                  strategy_note: str = "") -> AgentResult:
        """已设置的常用地点 → 直接导航（出路线规划卡 起点→终点）。"""
        name = stored.get("name") or label
        addr = stored.get("address") or name
        return await self._route_plan_to(
            name, addr, stored.get("lat"), stored.get("lng"), meta,
            resolved_prefix=f"正在前往{label}：", ctx=ctx, arrive_by_ts=arrive_by_ts,
            strategy=strategy, strategy_note=strategy_note)

    async def _set_place_and_go(self, place_key: str, label: str, address: str,
                                ctx, meta, navigate: bool) -> AgentResult:
        """地理编码地址 → 存为常用地点（best-effort）→ 按需导航。"""
        near = await self._current_position(ctx, meta)
        _resolved, results = await self._find_destination(address, meta, near=near)
        if not results:
            return AgentResult(
                status=NEED_SLOT,
                speech=f"没找到「{address}」，请补充城市/区域或换个说法。",
                follow_up=f"再说一次{label}的地址即可",
                missing_slots=["place_address" if navigate else "address"],
            )
        first = results[0]
        record = {"name": first.name, "address": first.address,
                  "lat": first.lat, "lng": first.lng}
        places = await self._get_places(ctx)
        places[place_key] = record
        try:
            await ctx.save_profile("places", places)  # 存画像失败不挡导航
        except Exception as e:
            logger.warning("save_profile places failed: %s", e)
        if navigate:
            return self._stamp_route_session(AgentResult(
                speech=f"已把{label}设为{first.name}，正在为您导航过去。",
                ui_card=attach({"type": "poi_list", "keyword": label, "items": [record]},
                               self.poi),
                data={"place": label, "item": record},
            ).action("navigate", self._navigate_payload(
                first.name, first.lat, first.lng, meta)),
                first.name, first.lat, first.lng)
        return AgentResult(
            speech=f"已把{label}设为{first.name}（{first.address}）。"
                   f"以后说『导航去{label}』就能直接出发。",
            data={"place": label, "item": record},
        )

    async def _set_place(self, intent, ctx, meta) -> AgentResult:
        """显式设置常用地点：『把家设成XX』『我家在XX』『设置公司地址为XX』。不导航。

        P4 守卫（2026-08-15 真栈恶性实测）：「我老婆平时在深圳湾万象城上班」被 planner
        语义映射成 set_place(公司=万象城)——**把用户自己的常用地点改写成了家人的位置**。
        家人位置陈述是记忆抽取/关系图谱的输入，不是设置本人常用地点：原话含人称词时
        只口头记下（真正的存储由抽取管线做），**绝不写画像**。
        """
        raw = getattr(intent, "raw_text", "") or ""
        person_word = _SET_PLACE_PERSON_RE.search(raw)
        if person_word:
            token = person_word.group(0)
            who = _person_display(token[1:] if len(token) == 2
                                  and token[0] in "我你他她" else token)
            return AgentResult(
                speech=f"好的，记下了——您{who}的位置我记在TA名下，"
                       f"以后说「去接{who}」我就知道去哪了。",
                follow_up="您自己的常用地点才用「把家/公司设成XX」设置")
        place_key, label = _match_place_alias(intent.slots.get("place", ""))
        address = (intent.slots.get("address") or "").strip()
        if not place_key or not address:
            pk, lb, addr = self._parse_set_place(getattr(intent, "raw_text", "") or "")
            place_key, label = (place_key or pk), (label or lb)
            address = address or addr
        if not place_key:
            return AgentResult(
                status=NEED_SLOT, speech="您想设置哪个常用地点？比如家或公司。",
                follow_up="可以说『把家设成XX地址』")
        if not address:
            return AgentResult(
                status=NEED_SLOT, speech=f"请告诉我{label}的具体地址。",
                missing_slots=["address"])
        return await self._set_place_and_go(
            place_key, label, address, ctx, meta, navigate=False)

    @staticmethod
    def _parse_set_place(raw: str) -> tuple[str | None, str, str]:
        """从原话兜底解析『把X设成Y/X在Y/X地址是Y』。返回 (key, 标签, 地址)。"""
        t = (raw or "").strip()
        m = re.search(
            r"(?:把|将)?\s*(家|我家|公司|单位|学校)(?:的)?(?:位置|地址)?\s*"
            r"(?:设成|设为|设置成|设置为|改成|改为|定为|定在)\s*(.+)", t)
        if not m:
            m = re.search(
                r"(我家|家|公司|单位|学校)(?:的)?(?:位置|地址)?\s*(?:在|是|为)\s*(.+)", t)
        if m:
            pk, lb = _match_place_alias(m.group(1))
            return pk, lb, m.group(2).strip(" 。，,.")
        return None, "", ""

    # 记忆路线偏好 → 话术（memory 来源要说清是「记住的」，不是本轮说的）
    _MEM_PREF_NOTES = {
        "6": "记得您平时不走高速，已按此规划。",
        "4": "记得您习惯避开拥堵，已按此规划。",
        "1": "记得您偏好少走收费路，已按此规划。",
    }

    # D4：历史指代词形——与 orchestrator/cloud/context.py::_EPISODIC_REF_RE 同一组
    # （planner 靠它放开 episodic 召回；端云各自部署，无法共享常量，改一处要对齐另一处）。
    _HISTORY_REF_RE = re.compile(r"上次|上回|那次|上一次|之前去过?的?|前几天去")
    # 描述性目的地（「那个地方」「去过的」）：没有可搜的专名——历史指代下轨迹名
    # 匹配不上时，拿它去 _find_destination 只会搜出垃圾。与「上次去的星巴克」区分：
    # 星巴克是真专名，轨迹没有也该正常搜。
    _DESCRIPTIVE_DEST_RE = re.compile(r"那个|那里|那儿|地方|去过的")

    async def _visited_coords_from_memory(self, ctx, dest: str) -> tuple[str, float, float] | None:
        """episodic 轨迹坐标直取（D4）：「召回对了最后一跳歪」的根治。

        名字对齐（_dest_matches 同款包含式）才直用——描述性 dest（planner 没解出名）
        不做语义猜：接错比重搜更糟。坐标从 memory 确定性直取、不经 LLM 转手
        （防模型编坐标——last_places 不进 prompt 的同款纪律）。查不到/失败回 None
        走正常解析，绝不阻塞导航。search_poi 自动导航分支刻意未挂：历史指代经
        planner episodic 召回都走 navigate_to（视觉地标才走 search_poi）。"""
        if ctx is None or not dest:
            return None
        try:
            mems = await ctx.recall(dest, scopes=["episodic.place"],
                                    kinds=["episodic"], top_k=5)
        except Exception as e:
            logger.debug("visited coords recall skipped: %s", e)
            return None
        for m in mems or []:
            try:
                v = json.loads(m.get("value_json") or "{}")
            except (TypeError, ValueError):
                continue
            name = str(v.get("name") or "")
            lat, lng = v.get("lat"), v.get("lng")
            if name and lat is not None and lng is not None \
                    and self._dest_matches(dest, name):
                try:
                    return name, float(lat), float(lng)
                except (TypeError, ValueError):
                    continue
        return None

    async def _recent_visited_places(self, ctx) -> list[dict]:
        """最近去过的地方（episodic 轨迹，按名去重，最多 5 个）。失败返回空。"""
        if ctx is None:
            return []
        try:
            mems = await ctx.recall("去过的地方", scopes=["episodic.place"],
                                    kinds=["episodic"], top_k=10)
        except Exception as e:
            logger.debug("recent visited recall skipped: %s", e)
            return []
        out, seen = [], set()
        for m in mems or []:
            try:
                v = json.loads(m.get("value_json") or "{}")
            except (TypeError, ValueError):
                continue
            name = str(v.get("name") or "").strip()
            lat, lng = v.get("lat"), v.get("lng")
            if not name or name in seen or lat is None or lng is None:
                continue
            try:
                out.append({"name": name, "lat": float(lat), "lng": float(lng)})
            except (TypeError, ValueError):
                continue
            seen.add(name)
            if len(out) >= 5:
                break
        return out

    async def _route_pref_from_memory(self, ctx) -> tuple[str, str]:
        """路线偏好记忆 → 高德 strategy（G6：route.* 谓词的确定性消费出口）。

        谓词前缀精确读取；**刻意不按 polarity 过滤**——route.* 族的方向已编码在
        谓词名里（avoid_highway 本来就是「不喜欢高速」），抽取端把「不要走高速」
        标成 dislike 是合理语义，按极性排除会把偏好挡在门外（2026-08-14 真栈
        实锤：B2 种下的 route.avoid_highway 带 polarity=dislike，首版过滤直接漏掉）。
        召回失败/无记忆一律回默认，绝不阻塞导航。
        """
        if ctx is None:
            return "", ""
        try:
            mems = await ctx.recall("路线偏好", predicate_prefix="route.", top_k=3)
        except Exception:
            return "", ""
        active = list(mems or [])
        if not active:
            return "", ""
        strategy, note = _route_strategy(" ".join(str(m.get("text") or "") for m in active))
        if not strategy:
            # 谓词级兜底：text 措辞不含关键词，但谓词本身就是声明
            preds = " ".join(str(m.get("predicate") or "") for m in active)
            strategy = ("6" if "route.avoid_highway" in preds
                        else "4" if "route.avoid_congestion" in preds
                        else "1" if "route.avoid_toll" in preds else "")
        if not strategy:
            return "", ""
        return strategy, self._MEM_PREF_NOTES.get(
            strategy, note or "已按您记住的路线偏好规划。")

    @staticmethod
    def _route_midpoint(route) -> GeoPoint | None:
        """路线几何里程 45% 处的采样点（G2 沿途搜索锚点）。几何缺失/短途返回 None。"""
        points = (route or {}).get("points") or []
        total = float((route or {}).get("distance_km") or 0)
        if not points or total < 2:
            return None
        target = total * 0.45
        for p in points:
            try:
                if float(p.get("cum_km", 0)) >= target:
                    return GeoPoint(lat=float(p["lat"]), lng=float(p["lng"]))
            except (TypeError, ValueError, KeyError):
                continue
        return None

    async def _navigate_with_stop_choice(self, dest_poi, resolved_name, stop_category,
                                         items, meta, *, arrive_by_ts=None,
                                         strategy: str = "",
                                         strategy_note: str = "") -> AgentResult:
        """轮1：导航到目的地，并给"顺路停靠"类目候选让用户二次选择途经点（不自动选）。

        G2（EVA 二轮）：候选搜索锚点从「目的地附近」改为**真沿途**——起点→目的地路线
        几何的 45% 里程采样点。拿不到路线几何/沿途空结果回落目的地附近，并在话术里
        如实说是哪种（不把目的地附近说成「顺路」）。
        G1：带 arrive_by 时报直达 ETA 判定，并给前 2 个候选算「顺道去这家」的到校时刻。
        """
        keyword = self._stop_keyword(stop_category)
        current = current_location_from_meta(meta)
        dest_pt = GeoPoint(lat=dest_poi.lat, lng=dest_poi.lng)
        base_route = None
        if current is not None:
            try:
                base_route = await self.poi.get_route(
                    GeoPoint(lat=current.lat, lng=current.lng), dest_pt,
                    meta=meta, with_polyline=True, strategy=strategy)
            except Exception as e:
                logger.debug("stop-choice base route unavailable: %s", e)
        mid = self._route_midpoint(base_route)
        where = "沿途" if mid is not None else "目的地附近"
        try:
            stops = await self.poi.search(keyword, near=(mid or dest_pt), limit=5, meta=meta)
        except ProviderError as e:
            logger.warning("stop category search failed: %s", e)
            stops = []
        if not stops and mid is not None:
            # 沿途采样点没搜到 → 回落目的地附近再试一次（原行为）
            where = "目的地附近"
            try:
                stops = await self.poi.search(keyword, near=dest_pt, limit=5, meta=meta)
            except ProviderError:
                stops = []
        payload = self._navigate_payload(dest_poi.name, dest_poi.lat, dest_poi.lng, meta)
        deadline_note, extra = self._deadline_note(
            (base_route or {}).get("duration_min"), arrive_by_ts)
        if not stops:
            return self._stamp_route_session(AgentResult(
                speech=f"{strategy_note}已为您导航到{dest_poi.name}。{deadline_note}"
                       f"沿途和目的地附近暂未找到{keyword}。",
                ui_card=attach({"type": "poi_list", "keyword": resolved_name,
                                "items": items}, self.poi),
                data={"items": items, **extra},
            ).action("navigate", payload),
                dest_poi.name, dest_poi.lat, dest_poi.lng,
                strategy=strategy, arrive_by_ts=arrive_by_ts)
        choice_items = [{"id": s.id, "name": s.name, "rating": s.rating,
                         "distance_km": s.distance_km, "address": s.address,
                         "lat": s.lat, "lng": s.lng} for s in stops]
        # G1：到达时限在场时，给前 2 个候选算「顺道去这家」的实际到达时刻（best-effort）
        eta_hint = ""
        if arrive_by_ts and current is not None:
            hints = []
            for it_c in choice_items[:2]:
                try:
                    wr = await self.poi.get_route(
                        GeoPoint(lat=current.lat, lng=current.lng), dest_pt, meta=meta,
                        waypoints=[GeoPoint(lat=it_c["lat"], lng=it_c["lng"])],
                        strategy=strategy)
                except Exception as e:
                    logger.debug("stop-choice candidate route unavailable: %s", e)
                    continue
                dur = wr.get("duration_min")
                if not dur:
                    continue
                eta_c = int(time.time()) + int(float(dur) * 60)
                late = round((eta_c - int(arrive_by_ts)) / 60)
                it_c["eta"] = self._fmt_clock(eta_c)
                it_c["late_min"] = max(0, late)
                hints.append(f"顺道{it_c['name']}约{it_c['eta']}到"
                             + (f"（晚{late}分钟）" if late > 0 else "（不迟到）"))
            if hints:
                eta_hint = "；".join(hints) + "。"
        names = "、".join(s.name for s in stops[:3])
        # purpose=waypoint_choice 让 HMI 把"第N个"回填为途经点（派发"导航去X途经Y"），而非发起新导航
        return self._stamp_route_session(AgentResult(
            speech=f"{strategy_note}已为您规划到{dest_poi.name}的路线。{deadline_note}"
                   f"{where}的{keyword}有：{names}。{eta_hint}"
                   f"想顺道去哪家？说『第几个』即可，不去也可以直接出发。",
            ui_card=attach({"type": "poi_list", "purpose": "waypoint_choice",
                            "display_priority": 1,
                            "title": f"顺路{keyword} · 选择途经点",
                            "destination": dest_poi.name, "items": choice_items},
                           self.poi),
            data={"destination": dest_poi.name, "stops": choice_items, **extra},
        ).action("navigate", payload),
            dest_poi.name, dest_poi.lat, dest_poi.lng,
            strategy=strategy, arrive_by_ts=arrive_by_ts)

    @staticmethod
    def _fmt_dur(minutes) -> str:
        m = int(minutes or 0)
        if m <= 0:
            return ""
        h, mm = divmod(m, 60)
        return (f"{h}小时" if h else "") + (f"{mm}分钟" if mm else "")

    async def _resolve_waypoint_token(self, w: str, ctx, near, meta
                                      ) -> "tuple[POI | None, AgentResult | None]":
        """P1（EVA 遗留卡）：单个途经点 token 三级解析——与目的地侧同款权威序。

        ① 人称词（孩子/老婆…）→ 关系图谱一跳；命中得地点名再 POI 搜索取坐标；
           **未命中 → 诚实教学问**（导航到随机学校比问一句更糟——真栈 B4 实测
           「先送孩子去学校」搜出「博明程国际教育」）。
        ② 常用地点别名（家/公司/学校）→ 画像 profile.places；未设置 → 引导先设置。
        ③ 其余照旧 POI 搜索（近目的地）。
        返回 (POI, None)=解析成功 / (None, AgentResult)=需要用户补信息（整轮中止）/
        (None, None)=搜不到（调用方记 missing 如实说跳过）。"""
        person_word = _person_destination(w)
        if person_word and ctx is not None:
            hit = await ctx.resolve_person_place(person_word)
            if hit and hit.get("place"):
                try:
                    r = await self.poi.search(hit["place"], near=near, limit=1,
                                              meta=meta)
                except ProviderError:
                    r = []
                if r:
                    return r[0], None
                return None, None          # 图谱有名但地图接不到 → 如实跳过
            who = _person_display(person_word)
            return None, AgentResult(
                status=NEED_SLOT,
                speech=f"我还不知道你{who}平时在哪，你说个地方我就记住了。",
                follow_up=f"可以说「我{who}在XX上班」或「我{who}在XX小学上学」",
                missing_slots=["waypoint"])
        place_key, place_label = _match_place_alias(w)
        if place_key and ctx is not None:
            stored = await self._get_place(ctx, place_key)
            if stored and stored.get("lat") is not None:
                return POI(id=f"place_{place_key}",
                           name=stored.get("name") or place_label,
                           address=stored.get("address") or "",
                           lat=stored.get("lat"), lng=stored.get("lng")), None
            return None, AgentResult(
                status=NEED_SLOT,
                speech=f"您还没有设置「{place_label}」的位置。"
                       f"先说「把{place_label}设成XX地址」，我记住后再帮您把它设为途经点。",
                follow_up=f"例如「把{place_label}设成深圳市南山实验小学」",
                missing_slots=["waypoint"])
        try:
            r = await self.poi.search(w, near=near, limit=1, meta=meta)
        except ProviderError as e:
            logger.warning("waypoint resolve failed: %s", e)
            r = []
        return (r[0] if r else None), None

    async def _navigate_via_waypoint(self, dest_poi, resolved_name, waypoint,
                                     items, meta, *, arrive_by_ts=None,
                                     strategy: str = "",
                                     strategy_note: str = "", ctx=None) -> AgentResult:
        """轮2：所选停靠点 near 目的地解析坐标→并入途经点，并出路线规划卡（出发地→途经点→目的地）。

        G9（EVA 二轮）：waypoint 支持 、/和/与/及 连写多个（「途经肯德基和星巴克」），
        逐个解析**保用户口述序**并入路线；解析不出的如实说跳过。
        G1：带 arrive_by 时做时限判定；能算出直达路线时量化绕行代价（多绕约 N 分钟），
        迟到而直达能赶上时给出量化替代（不做自动取舍，选择权在用户）。
        P1：token 先过人称/常用地点两级（`_resolve_waypoint_token`），
        「先送孩子去学校再去公司」的「学校」不再搜出随机学校。
        """
        near = GeoPoint(lat=dest_poi.lat, lng=dest_poi.lng)
        wanted = [w.strip(" 、，,。") for w in re.split(r"[、和与及,，]", waypoint or "")
                  if w.strip(" 、，,。")][:6]
        resolved, missing = [], []
        for w in wanted:
            poi_hit, ask = await self._resolve_waypoint_token(w, ctx, near, meta)
            if ask is not None:
                return ask
            (resolved.append(poi_hit) if poi_hit else missing.append(w))
        payload = self._navigate_payload(dest_poi.name, dest_poi.lat, dest_poi.lng, meta)
        if not resolved:
            return self._stamp_route_session(AgentResult(
                speech=f"没找到「{'、'.join(missing) or waypoint}」，已先为您导航到{dest_poi.name}。",
                ui_card=attach({"type": "poi_list", "keyword": resolved_name,
                                "items": items}, self.poi),
                data={"items": items},
            ).action("navigate", payload),
                dest_poi.name, dest_poi.lat, dest_poi.lng,
                strategy=strategy, arrive_by_ts=arrive_by_ts)

        payload["waypoints"] = [{"name": w.name, "address": w.address,
                                 "lat": w.lat, "lng": w.lng} for w in resolved]
        # 全程距离/时长（best-effort）：出发地→途经点→目的地；直达对照供绕行量化
        distance_km = duration_min = 0
        detour_min = None
        direct_dur = None
        current = current_location_from_meta(meta)
        if current:
            cur_pt = GeoPoint(lat=current.lat, lng=current.lng)
            try:
                route = await self.poi.get_route(
                    cur_pt, GeoPoint(lat=dest_poi.lat, lng=dest_poi.lng),
                    meta=meta, strategy=strategy,
                    waypoints=[GeoPoint(lat=w.lat, lng=w.lng) for w in resolved])
                distance_km = route.get("distance_km") or 0
                duration_min = route.get("duration_min") or 0
            except Exception as e:                       # best-effort：算不出就只给时间线
                logger.debug("route plan distance unavailable: %s", e)
            if duration_min:
                try:
                    direct = await self.poi.get_route(
                        cur_pt, GeoPoint(lat=dest_poi.lat, lng=dest_poi.lng),
                        meta=meta, strategy=strategy)
                    direct_dur = direct.get("duration_min") or None
                    if direct_dur:
                        detour_min = max(0, round(float(duration_min) - float(direct_dur)))
                except Exception as e:
                    logger.debug("direct route for detour compare unavailable: %s", e)
        wp_names = "、".join(w.name for w in resolved)
        head = (f"{strategy_note}已把{wp_names}设为途经点，为您规划好路线："
                f"当前位置 → {wp_names} → {dest_poi.name}")
        if distance_km:
            dur = self._fmt_dur(duration_min)
            head += f"，全程约{distance_km}公里" + (f"、约{dur}" if dur else "")
            if detour_min:
                head += f"（比直达多绕约{detour_min}分钟）"
        if missing:
            head += f"；「{'、'.join(missing)}」没找到，已跳过"
        deadline_note, extra = self._deadline_note(duration_min, arrive_by_ts)
        tail = deadline_note
        if extra.get("margin_min", 0) < 0 and direct_dur:
            direct_eta = int(time.time()) + int(float(direct_dur) * 60)
            if direct_eta <= int(arrive_by_ts):
                # 话术只许诺今天走得通的路：「直接导航去X」=新一轮直达导航（现有链路）；
                # G8 已落地：增量改道（navigation.reroute）可引导——「途经点不去了」
                # 会在保留目的地与时限的前提下改直达，比引导用户发起全新导航干净。
                tail += (f"若不带途经点直达，预计{self._fmt_clock(direct_eta)}可准时到；"
                         "要改直达就说「途经点不去了」。")
        card = attach({"type": "route_plan", "origin": "当前位置",
                       "destination": dest_poi.name,
                       "waypoints": [{"name": w.name, "address": w.address}
                                     for w in resolved],
                       "distance_km": distance_km, "duration_min": duration_min,
                       **extra}, self.poi)
        return self._stamp_route_session(AgentResult(
            speech=head + "。" + tail, ui_card=card,
            data={"waypoints": payload["waypoints"], **extra},
        ).action("navigate", payload),
            dest_poi.name, dest_poi.lat, dest_poi.lng,
            waypoints=payload["waypoints"], strategy=strategy,
            arrive_by_ts=arrive_by_ts)

    async def _route_plan_to(self, name: str, address: str, lat, lng, meta,
                             *, resolved_prefix: str = "", ctx=None,
                             arrive_by_ts=None, strategy: str = "",
                             strategy_note: str = "") -> AgentResult:
        """导航到具体目的地：出路线规划卡（当前位置 → 目的地，best-effort 距离/时长）+ navigate。
        与顺路途经点的 route_plan 卡同一范式，让用户直观看到"已规划好路线（起点→终点）"。"""
        payload = self._navigate_payload(name, lat, lng, meta)
        distance_km = duration_min = 0
        current = current_location_from_meta(meta)
        if current:
            try:
                route = await self.poi.get_route(
                    GeoPoint(lat=current.lat, lng=current.lng),
                    GeoPoint(lat=lat, lng=lng), meta=meta, strategy=strategy)
                distance_km = route.get("distance_km") or 0
                duration_min = route.get("duration_min") or 0
            except Exception as e:                       # best-effort：算不出就只给起终点
                logger.debug("route plan distance unavailable: %s", e)
        speech = f"{resolved_prefix}{strategy_note}为您导航到{name}（{address}）。"
        if distance_km:
            dur = self._fmt_dur(duration_min)
            speech += f"全程约{distance_km}公里" + (f"、约{dur}" if dur else "") + "，已规划好路线。"
        else:
            speech += "已规划好路线。"
        # G1：到达时限判定（「五点前到」）——ETA 与时限的确定性比对 + 量化话术
        deadline_note, deadline_extra = self._deadline_note(duration_min, arrive_by_ts)
        speech += deadline_note
        # 车辆接地 advisory（旅程 B3-2）：续航覆盖不了本程（含 15% 保留余量，与 charging
        # 同款判定）→ 主动提示补能。只加话术不加动作（advisory 不发车控/不改路线），
        # 用户接一句「沿途帮我找充电站」即进 charging 流程。电量经端侧 meta 注入
        # （server.py 把 VAL 真实电量写 vehicle_battery），拿不到就不提示（fail-open）。
        speech += self._range_advisory(distance_km, meta)
        # R7（旅程 A2-4/B5-1⑥）：REMINDABLE_ACTIVE「即插」契约兑现——写 ETA 事件，
        # 「到之前一刻钟提醒我打电话」由 reminder 消费（事件时刻-提前量），不再反问时间。
        # best-effort：无 ctx/无时长/写失败都不影响导航本体。
        if ctx is not None and duration_min:
            try:
                now_ts = int(time.time())
                dur_s = int(float(duration_min) * 60)
                remindable = [{"title": f"到达{name}", "fire_at": now_ts + dur_s}]
                # G1 出发提醒反向环：有到达时限时补「出发前往X」事件，
                # fire_at=必须出发的时刻（时限-路程）。reminder 的默认提前量（10 分钟）
                # 会让「到时候提醒我出发」在该出发前 10 分钟响——正好是提前量语义。
                if arrive_by_ts:
                    depart_ts = int(arrive_by_ts) - dur_s
                    if depart_ts > now_ts + 60:
                        remindable.insert(0, {"title": f"出发前往{name}",
                                              "fire_at": depart_ts})
                await ctx.save_shared_state(REMINDABLE_ACTIVE, {
                    "source": "navigation", "label": f"前往{name}",
                    "ts": now_ts, "items": remindable})
            except Exception as e:
                logger.debug("navigation remindable save skipped: %s", e)
        # G6 历史轨迹：导航成功落一条轻量情景记忆（「上次去的那个地方」的数据源）
        await self._remember_visited(ctx, name, lat, lng)
        card = attach({"type": "route_plan", "origin": "当前位置", "destination": name,
                       "waypoints": [], "distance_km": distance_km,
                       "duration_min": duration_min, **deadline_extra}, self.poi)
        return self._stamp_route_session(AgentResult(
            speech=speech, ui_card=card,
            data={"destination": name, "lat": lat, "lng": lng, **deadline_extra},
        ).action("navigate", payload),
            name, lat, lng, strategy=strategy, arrive_by_ts=arrive_by_ts)

    # ── G8 路线会话与增量改道 ────────────────────────────────────
    # 过龄阈值取一次驾驶会话的量级（默认 2h）：与门店锚定的 15min 语义不同，路线会话
    # 跟着「这趟在开」的时长走。PoC 无真实驾驶时长分母，先取不打扰的宽值。
    _ROUTE_SESSION_MAX_AGE_S = int(os.getenv("ROUTE_SESSION_MAX_AGE_S", "7200"))

    @staticmethod
    def _stamp_route_session(result: AgentResult, destination: str, lat, lng, *,
                             waypoints: list | None = None, strategy: str = "",
                             arrive_by_ts=None) -> AgentResult:
        """把本次导航的结构化路线写进结果保留键 `_route_session`（conventions §9.1）。

        engine 的 extract_focus 通用消费它落 focus.active_route，下一轮「途经点不去了」
        才有对象可指。**挂点必须枚举全部执行路径**（本仓第三次应验的教训）：每个发出
        navigate action 的 return 都要盖章，漏一处那条路径的导航就无法被增量修改。
        """
        try:
            lat_f, lng_f = float(lat), float(lng)
        except (TypeError, ValueError):
            return result
        session = {
            "destination": str(destination or ""), "lat": lat_f, "lng": lng_f,
            "waypoints": [{"name": str(w.get("name")), "lat": w.get("lat"),
                           "lng": w.get("lng")} for w in (waypoints or [])
                          if isinstance(w, dict) and w.get("name")],
            "strategy": str(strategy or ""), "ts": int(time.time())}
        if arrive_by_ts:
            session["arrive_by_ts"] = int(arrive_by_ts)
        if result.data is None:
            result.data = {}
        result.data["_route_session"] = session
        return result

    @classmethod
    def _active_route_from(cls, meta) -> dict | None:
        """从 meta.focus_active_route（engine 按 location scope 注入的服务端事实，
        LLM 与客户端都写不到）读活动路线。无/损坏/过龄 → None。坐标逐项校验，
        非法途经点直接丢——防御防到真正被拿去规划的那个值。"""
        raw = (meta or {}).get("focus_active_route", "")
        if not raw:
            return None
        try:
            d = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(d, dict) or not str(d.get("destination") or "").strip():
            return None
        try:
            lat, lng = float(d.get("lat")), float(d.get("lng"))
        except (TypeError, ValueError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return None
        try:
            ts = int(d.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0
        if ts <= 0 or time.time() - ts > cls._ROUTE_SESSION_MAX_AGE_S:
            return None      # 过龄：别把上周那趟当成「当前正在导航」
        waypoints = []
        for w in (d.get("waypoints") or []):
            if not isinstance(w, dict) or not str(w.get("name") or "").strip():
                continue
            try:
                wlat, wlng = float(w.get("lat")), float(w.get("lng"))
            except (TypeError, ValueError):
                continue
            if -90 <= wlat <= 90 and -180 <= wlng <= 180:
                waypoints.append({"name": str(w["name"]), "lat": wlat, "lng": wlng})
        out = {"destination": str(d["destination"]).strip(), "lat": lat, "lng": lng,
               "waypoints": waypoints, "strategy": str(d.get("strategy") or "")}
        try:
            ab = int(d.get("arrive_by_ts") or 0)
        except (TypeError, ValueError):
            ab = 0
        if ab > 0:
            out["arrive_by_ts"] = ab
        return out

    @staticmethod
    def _parse_reroute_remove(raw: str) -> str:
        for pattern in (_REROUTE_REMOVE_A_RE, _REROUTE_REMOVE_B_RE):
            m = pattern.search(raw or "")
            if m:
                return m.group(1).strip("的了 ")
        return ""

    @staticmethod
    def _parse_reroute_add(raw: str) -> tuple[str, bool]:
        """→ (目标词, 是否插首位)。「先去/先买」带先后语义 → 插到途经点首位。"""
        m = _REROUTE_ADD_RE.search(raw or "")
        if not m:
            return "", False
        return m.group(1).strip("个的杯 "), m.group(0).startswith("先")

    @staticmethod
    def _pop_waypoint(waypoints: list, word: str) -> str:
        """按名双向包含匹配删除一个途经点，返回被删名；未命中返回 ''。"""
        for i, w in enumerate(waypoints):
            nm = str(w.get("name") or "")
            if nm and (word in nm or nm in word):
                waypoints.pop(i)
                return nm
        return ""

    async def _reroute(self, intent, ctx, meta) -> AgentResult:
        """G8：增量调整当前活动路线（删/加途经点、换路线策略、改目的地）。

        只动被点名的那一项，其余约束（目的地/时限/策略/其余途经点）保持并重出
        G1 时限判定。产出仍是 navigate action（非危险动作，与 navigate_to 同级），
        坐标全部来自 meta 注入的路线会话与本轮高德接地——不经 LLM 转手。
        """
        raw_text = (intent.raw_text or "").strip()
        session = self._active_route_from(meta)
        if not session:
            return AgentResult(
                speech="当前没有正在进行的导航。直接说「导航去某地」，我就为您规划路线。",
                follow_up="例如『导航去万象天地，顺路买杯咖啡』")

        orig_dest = session["destination"]
        dest_name, dest_lat, dest_lng = orig_dest, session["lat"], session["lng"]
        waypoints = list(session.get("waypoints") or [])
        strategy = str(session.get("strategy") or "")
        arrive_by_ts = session.get("arrive_by_ts")
        new_arrive = self._arrive_by_from(intent, raw_text)
        if new_arrive:                      # 本轮新说了时限则覆盖；「别迟到」保持原值
            arrive_by_ts = new_arrive
        notes: list[str] = []
        changed = False

        # ① 改目的地（slot 优先，raw 兜底）：新目的地走 _find_destination 全套接地
        #    （R1 强校验/类目锚词/行政级判定全部生效），途经点保留。
        new_dest = (intent.slots.get("destination") or "").strip()
        if not new_dest:
            m = _REROUTE_DEST_RE.search(raw_text)
            if m:
                new_dest = m.group(1).strip(" 。，,的")
        if new_dest and not self._dest_matches(new_dest, orig_dest):
            near = await self._current_position(ctx, meta)
            _resolved, results = await self._find_destination(new_dest, meta, near=near)
            if results:
                first = results[0]
                dest_name, dest_lat, dest_lng = first.name, first.lat, first.lng
                notes.append(f"目的地已改为{first.name}")
                changed = True
            else:
                return AgentResult(
                    status=NEED_SLOT,
                    speech=f"暂时没找到「{new_dest}」，目的地保持{orig_dest}不变。"
                           "请补充城市或换个说法。",
                    missing_slots=["destination"])

        # ② 删途经点：按名包含匹配；泛指（「那个途经点」）或单途经点时删最近加入的。
        remove_word = (intent.slots.get("remove_waypoint") or "").strip() \
            or self._parse_reroute_remove(raw_text)
        if remove_word and not self._dest_matches(remove_word, orig_dest):
            removed = self._pop_waypoint(waypoints, remove_word)
            if not removed and waypoints and (
                    _REROUTE_GENERIC_WP_RE.search(remove_word)
                    or len(waypoints) == 1):
                removed = waypoints.pop(-1).get("name", "")
            if removed:
                notes.append(f"已去掉途经点{removed}")
                changed = True
            elif waypoints:
                notes.append(f"途经点里没找到「{remove_word}」，路线保持不变")
            else:
                notes.append("当前路线没有途经点")
        elif remove_word and not new_dest:
            # 点名不去的是目的地本身且没给新目的地：终止导航归 HMI/端侧，引导改道
            return AgentResult(
                speech=f"您是想不去{orig_dest}了吗？想改去别的地方，"
                       "直接说「导航去某地」或「目的地改成某地」就行。",
                follow_up="也可以说『取消途经点某某』只调整顺路安排")

        # ③ 加途经点：类目词映射后就近解析（当前位置优先，其次目的地）。
        add_word, prepend = "", False
        slot_add = (intent.slots.get("add_waypoint") or "").strip()
        if slot_add:
            add_word, prepend = slot_add, ("先" in raw_text[:raw_text.find(slot_add) + 1]
                                           if slot_add in raw_text else False)
        else:
            add_word, prepend = self._parse_reroute_add(raw_text)
        if add_word and add_word not in (remove_word or ""):
            keyword = self._stop_keyword(add_word)
            current = current_location_from_meta(meta)
            near = (GeoPoint(lat=current.lat, lng=current.lng) if current
                    else GeoPoint(lat=dest_lat, lng=dest_lng))
            try:
                found = await self.poi.search(keyword, near=near, limit=1, meta=meta)
            except ProviderError as e:
                logger.warning("reroute add-waypoint search failed: %s", e)
                found = []
            if found:
                wp = {"name": found[0].name, "lat": found[0].lat, "lng": found[0].lng}
                if prepend:
                    waypoints.insert(0, wp)
                else:
                    waypoints.append(wp)
                notes.append(f"已顺路加上{found[0].name}")
                changed = True
            else:
                notes.append(f"附近暂时没找到{add_word}")

        # ④ 换路线：带偏好走 _route_strategy；裸「换条路」在避堵/不走高速间轮换。
        pref_strategy, pref_note = _route_strategy(
            f"{intent.slots.get('route_pref') or ''} {raw_text}")
        if pref_strategy:
            strategy, note = pref_strategy, pref_note.rstrip("。")
            notes.append(note)
            changed = True
        elif _REROUTE_CHANGE_ROUTE_RE.search(raw_text):
            strategy = "4" if strategy != "4" else "6"
            notes.append("已按避开拥堵重新规划" if strategy == "4"
                         else "已按不走高速重新规划")
            changed = True

        if not changed and not notes:
            return AgentResult(
                speech="您想怎么调整当前路线？可以说「某某不去了」「顺路加个加油站」"
                       "「换条避堵的路」或「目的地改成某地」。",
                follow_up="告诉我要调整哪一项即可")

        # ⑤ 重算路线（当前位置 →（途经点）→ 目的地）+ G1 时限判定 + 写回会话。
        payload = self._navigate_payload(dest_name, dest_lat, dest_lng, meta)
        if waypoints:
            payload["waypoints"] = [dict(w) for w in waypoints]
        distance_km = duration_min = 0
        current = current_location_from_meta(meta)
        if current:
            try:
                route = await self.poi.get_route(
                    GeoPoint(lat=current.lat, lng=current.lng),
                    GeoPoint(lat=dest_lat, lng=dest_lng), meta=meta,
                    strategy=strategy,
                    waypoints=[GeoPoint(lat=w["lat"], lng=w["lng"])
                               for w in waypoints] or None)
                distance_km = route.get("distance_km") or 0
                duration_min = route.get("duration_min") or 0
            except Exception as e:       # best-effort：算不出只报调整结果
                logger.debug("reroute route recalc unavailable: %s", e)
        deadline_note, extra = self._deadline_note(duration_min, arrive_by_ts)
        wp_names = "、".join(str(w["name"]) for w in waypoints)
        head = "；".join(notes) + "。当前路线：当前位置 → " \
            + (f"{wp_names} → " if wp_names else "") + dest_name
        if distance_km:
            dur = self._fmt_dur(duration_min)
            head += f"，全程约{distance_km}公里" + (f"、约{dur}" if dur else "")
        card = attach({"type": "route_plan", "origin": "当前位置",
                       "destination": dest_name,
                       "waypoints": [{"name": w["name"], "address": ""}
                                     for w in waypoints],
                       "distance_km": distance_km, "duration_min": duration_min,
                       **extra}, self.poi)
        result = AgentResult(
            speech=head + "。" + deadline_note, ui_card=card,
            data={"destination": dest_name, "lat": dest_lat, "lng": dest_lng,
                  "waypoints": [dict(w) for w in waypoints], **extra},
        ).action("navigate", payload)
        return self._stamp_route_session(
            result, dest_name, dest_lat, dest_lng, waypoints=waypoints,
            strategy=strategy, arrive_by_ts=arrive_by_ts)

    async def _remember_visited(self, ctx, name, lat, lng) -> None:
        """导航成功落一条轻量情景轨迹（G6「上次去的那个地方」数据源）。best-effort。

        挂点必须枚举全部执行路径（本仓第三次应验）：批 C 首版只挂 _route_plan_to，
        而 search_poi 的「带我去X」自动导航分支绕过它——真栈「圆圆的湖→滴水湖」
        走的正是那条路径，轨迹整条漏写。
        """
        if ctx is None or not name:
            return
        try:
            await ctx.remember(
                f"{time.strftime('%Y-%m-%d')} 导航去过{name}",
                kind="episodic", scope="episodic.place",
                provenance="agent_inferred", confidence=0.9,
                privacy_level="sensitive",
                value={"name": name, "lat": lat, "lng": lng, "ts": int(time.time())})
        except Exception as e:
            logger.debug("navigation episodic save skipped: %s", e)

    @staticmethod
    def _range_advisory(distance_km, meta) -> str:
        """里程 vs 电量续航的补能提示；不适用/数据缺失返回空串。"""
        try:
            pct = float(str((meta or {}).get("vehicle_battery", "")).replace("%", ""))
            dist = float(distance_km or 0)
        except (TypeError, ValueError):
            return ""
        if not (0 < pct <= 100) or dist <= 0:
            return ""
        full_range = float(os.getenv("CHARGING_FULL_RANGE_KM", "500") or 500)
        usable = pct / 100.0 * full_range
        if dist <= usable * 0.85:
            return ""
        return (f"提醒一下：当前电量约{round(pct)}%（续航约{round(usable)}公里），"
                f"本程约{round(dist)}公里，建议途中补能，可以说「沿途帮我找充电站」。")

    @staticmethod
    def _dest_matches(query: str, poi_name: str) -> bool:
        """目的地名与 POI 名强校验（R1，包含式）。

        `landmark.name_matches` 的「2 字公共子串」对**用户直报的目的地名**太松——
        「广州塔」和「广州仄仄科技有限公司」共享「广州」也算匹配，带 near 偏置的
        关键词搜索会让就近弱匹配顶掉真地标（旅程 B3-2/A2-4/B1-2 三例同族）。
        归一（去括号注记/空白/连接符）后任一方向包含才算。"""
        def norm(s: str) -> str:
            s = re.sub(r"[（(].*?[)）]", "", s or "")
            return re.sub(r"[\s·,，\-—]", "", s)
        a, b = norm(query), norm(poi_name)
        return bool(a) and bool(b) and (a in b or b in a)

    # R1 二期（接地卡 2026-08-14）：类目锚词 → 期望高德 type 关键词族。
    # 包含式校验的隐含假设「名字包含 ⇒ 是本体」被真栈证伪——酒店/停车场/餐馆借地标
    # 之名命名（「如家…虹桥机场…停车场」「千岛湖·山野菜」），在就近距离序里天然顶掉
    # 本体。query 以锚词结尾时，top1 还必须过类目复核，失配走去偏置重搜救济。
    # 每加一行都要有真栈红例背书（机场=虹桥/浦东，湖=滴水湖/西湖/千岛湖/东湖，
    # 滩=外滩），别让它长成第二个 fast_intent（B4 纪律）。
    _DEST_CATEGORY_ANCHORS = (
        ("机场", ("机场",)),
        ("湖", ("风景名胜", "自然地名", "热点地名")),
        ("滩", ("风景名胜", "自然地名", "热点地名")),
    )

    @classmethod
    def _category_anchor(cls, query: str) -> tuple[str, tuple[str, ...]] | None:
        """query 以类目锚词结尾 → (锚词, 期望类目关键词族)；否则 None。
        要求严格长于锚词：纯类目词（「机场」=就近语义）维持现状距离序。"""
        q = (query or "").strip()
        for suffix, expect in cls._DEST_CATEGORY_ANCHORS:
            if q.endswith(suffix) and len(q) > len(suffix):
                return suffix, expect
        return None

    @staticmethod
    def _category_ok(poi: POI, expect: tuple[str, ...]) -> bool:
        return any(k in (poi.category or "") for k in expect)

    @classmethod
    def _grounds_to(cls, description: str, poi: POI, suffix: str,
                    expect: tuple[str, ...]) -> bool:
        """锚词在场时的双匹配判据：类目命中期望族，且名字过严格包含**或**主干包含。

        主干级是给官方名的：「虹桥机场」与「上海虹桥国际机场」因中间的「国际」不构成
        连续包含，严格校验够不着本体——剥掉锚词后的专名主干（「虹桥」）配上类目复核，
        名字校验管专名、类目校验管类目。主干 ≥2 字才启用（「外滩」1 字主干太弱，
        它的本体名严格包含已够）。"""
        if not cls._category_ok(poi, expect):
            return False
        if cls._dest_matches(description, poi.name):
            return True
        stem = description.strip()[:-len(suffix)]
        return len(stem) >= 2 and cls._dest_matches(stem, poi.name)

    @staticmethod
    def _rough_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """等距圆柱近似的两点距离（判「本地区县」的 150km 阈值足够，不求测地精度）。"""
        dlat = (lat2 - lat1) * 111.0
        dlng = (lng2 - lng1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2))
        return math.hypot(dlat, dlng)

    async def _find_destination(self, description: str, meta, near=None,
                                limit: int = 3, page: int = 1,
                                strict: bool = True) -> tuple[str, list]:
        """解析目的地 POI。

        视觉地标描述（“像笋的建筑”）：高德直接搜常返回勉强的模糊匹配，必须先经 LLM
        解析正式名称再由地图验证，避免被垃圾匹配抢占（否则导航到错误 POI）。
        普通目的地：原话直搜优先（带当前位置 near，使“最近的/附近的粤菜馆”按距离就近），
        top1 名字过 `_dest_matches` 强校验——不匹配先去偏置全国重搜（知名地标全国序第一），
        再走地标解析；都验证不出保留原结果兜底（话术会报出实际名，用户可纠正，不无中生有）。
        行政级目的地（「导航去惠州」）经 geocode level 判定，直接导航到行政中心，
        不给就近弱匹配（0.3km 的「惠州出口」）机会。
        limit：类目就近查询给更多候选（5）供用户选目的地；具体地点解析用默认（3）。
        """
        async def _direct(bias) -> list:
            try:
                return await self.poi.search(description, near=bias, limit=limit,
                                             page=page, meta=meta)
            except ProviderError as e:
                logger.warning("destination POI search failed: %s", e)
                return []

        async def _via_landmark() -> tuple[str, list]:
            for candidate in await self._landmark_candidates(description):
                try:
                    results = await self.poi.search(candidate, limit=limit, meta=meta)
                except ProviderError as e:
                    logger.warning("landmark candidate POI search failed: %s", e)
                    continue
                # 高德对非官方名会返回同位置的邻近无关 POI（搜“华润春笋大厦”→V东滨店）：
                # 只接受 top 结果名与候选实质匹配的，否则换下一个候选（如官方名“中国华润大厦”）。
                if results and name_matches(candidate, results[0].name):
                    return candidate, results
            return "", []

        if is_landmark_description(description):
            name, results = await _via_landmark()
            if results:
                return name, results
            results = await _direct(near)    # 地标候选验证不出来 → 退回原话直搜
            return (description, results) if results else ("", [])

        # R1：短名先过行政级判定（「惠州」「珠海」这类裸城市名不带 市/省 后缀，
        # 关键词搜索会顶出就近弱匹配）。仅 strict 且 ≤4 字触发，控制额外 geocode 调用面。
        geocode_level = getattr(self.poi, "geocode_level", None)
        if strict and geocode_level and 2 <= len(description) <= 4:
            try:
                level, loc = await geocode_level(description, meta=meta)
            except Exception as e:
                logger.debug("geocode level probe failed: %s", e)
                level, loc = "", ""
            if level in ("国家", "省", "市", "区县") and loc and "," in loc:
                try:
                    lng_s, lat_s = loc.split(",")[:2]
                    lat_f, lng_f = float(lat_s), float(lng_s)
                except ValueError:
                    lat_f = lng_f = None
                # R1 二期（接地卡 2026-08-14）：区县级裸名多义面大——高德 geocode 的
                # 全国唯一解会选错省的同名区划（实测「西湖」→台湾苗栗西湖乡、「金山」→
                # 台湾新北金山区、「东湖」→南昌东湖区），而用户裸报区县名的心智是本地。
                # 区县级仅在「有定位且 ≤150km」时可信，否则 fall through 到关键词搜索
                # （类目锚词校验接手「西湖」这类景点同名）；市及以上唯一性好（佛山/惠州），
                # 跨城导航合法，维持无条件直达。
                trustworthy = level != "区县" or (
                    lat_f is not None and near is not None
                    and self._rough_km(near.lat, near.lng, lat_f, lng_f) <= 150)
                if lat_f is not None and trustworthy:
                    admin_poi = POI(id=f"admin_{description}", name=description,
                                    address=f"{description}（市区中心）",
                                    lat=lat_f, lng=lng_f)
                    return description, [admin_poi]

        results = await _direct(near)
        if results:
            if not strict:
                return description, results
            # R1 二期：包含式名字校验之上叠类目锚词复核——「名字包含 ⇒ 是本体」被
            # 真栈证伪（「虹桥机场」→如家…停车场、「滴水湖」→雅悦酒店、「千岛湖」→
            # 上海的千岛湖鱼头馆，均名字包含放行、距离序顶掉本体，且本体根本不在
            # near 候选集里）。锚词失配视同校验失败，走同一条去偏置重搜救济。
            anchor = self._category_anchor(description)
            top_named = self._dest_matches(description, results[0].name)
            if top_named and (not anchor or self._category_ok(results[0], anchor[1])):
                return description, results
            if anchor:
                # 先在候选集内找名字+类目双匹配（零 API）：「东湖」→#2 东湖绿地
                # （风景名胜），比全国重搜接到 500km 外的武汉东湖更合就近意图。
                for r in results[1:]:
                    if self._grounds_to(description, r, *anchor):
                        return description, [r] + [x for x in results if x is not r]
            # R1：就近弱匹配/借名 POI 顶上了 top1 → 去偏置全国重搜（真地标全国序靠前）
            if near is not None:
                wide = await _direct(None)
                if wide:
                    if anchor:
                        # 锚词在场时按名字+类目双匹配选（「滴水湖」全国序 #1 湖本体
                        # #2 地铁站——只认 top1 会被同名亲戚卡住）；无锚词维持只看 top1。
                        for r in wide:
                            if self._grounds_to(description, r, *anchor):
                                return description, [r] + [x for x in wide if x is not r]
                    elif self._dest_matches(description, wide[0].name):
                        return description, wide
            name, lm = await _via_landmark()
            if lm:
                return name, lm
            return description, results     # 兜底：报出实际名让用户纠正
        return await _via_landmark()

    async def _landmark_candidates(self, description: str) -> list[str]:
        """把视觉化地标描述转换为少量地图可检索的正式 POI 候选（共享解析器，导航/充电共用）。"""
        return await landmark_candidates(self.llm, description, logger=logger)

    async def _correct_planner_landmark(self, dest: str, raw_text: str, meta) -> str:
        """修正云端 Planner 对视觉地标的错误臆断。

        Planner 的 LLM 有时会自作主张把视觉地标描述（"像笋的建筑"）直接解析成一个**具体楼名**
        （实测把"深圳笋状地标"错猜成"京基100"）写进 destination 槽位，绕过本 Agent 带 name_matches
        地图校验的专用地标解析器（它对整段凌乱原话仍能精准→中国华润大厦）。判据：原话是地标描述、
        而 dest 已被解析成**不含造型词**的具体名。命中则用原话重解析 + 高德校验，用**官方名**覆盖臆断；
        非该情形（普通导航/dest 本就是地标描述）零额外调用直接返回原 dest。"""
        if not (raw_text and is_landmark_description(raw_text) and not is_landmark_description(dest)):
            return dest
        for cand in await self._landmark_candidates(raw_text):
            try:
                hits = await self.poi.search(cand, limit=1, meta=meta)
            except ProviderError as e:
                logger.warning("planner-landmark correction search failed: %s", e)
                continue
            if hits and name_matches(cand, hits[0].name):
                logger.info("corrected planner dest %r -> landmark %r", dest, cand)
                return cand
        return dest

    async def _reverse_geocode(self, intent, ctx, meta) -> AgentResult:
        """逆地理编码：坐标 → 地址。"""
        lng_s = intent.slots.get("lng", "")
        lat_s = intent.slots.get("lat", "")
        if not lng_s or not lat_s:
            # 尝试用车辆位置
            current = await self._current_position(ctx, meta)
            if current and current.lng and current.lat:
                lng_s, lat_s = str(current.lng), str(current.lat)
            elif current and current.address:
                return AgentResult(speech=f"当前位置：{current.address}",
                                   data={"address": current.address})
            else:
                return AgentResult(status=NEED_SLOT, speech="请提供坐标或位置信息。",
                                   missing_slots=["lng", "lat"])
        try:
            lng, lat = float(lng_s), float(lat_s)
        except ValueError:
            return AgentResult(status=FAILED, speech="坐标格式不正确。")
        try:
            pt = await self.poi.reverse_geocode(lng, lat, meta=meta)
        except ProviderError as e:
            # §9.5 铁律③：不拿 mock 地址冒充真实位置，诚实说解析不了（OK 话术防聚合器吞）。
            logger.warning("reverse_geocode failed（诚实降级，无 mock 回退）: %s", e)
            return AgentResult(
                speech="定位服务暂时不可用，这个位置的地址暂时解析不出来，请稍后再试。",
                data={"lng": lng, "lat": lat})
        speech = f"该位置位于{pt.address}。" if pt.address else "未能解析该位置的地址。"
        return AgentResult(speech=speech,
                           data={"address": pt.address, "lng": lng, "lat": lat})

    async def _locate(self, intent, ctx, meta) -> AgentResult:
        """『我在哪 / 我现在在哪里 / 当前位置』：逆地理编码当前已授权位置 → 当前地址。
        与就近导航、天气统一只用浏览器 GPS；未授权时诚实提示开启定位，绝不回退编造 上海。"""
        current = await self._current_position(ctx, meta)
        if not current or current.lat is None or current.lng is None:
            return AgentResult(
                speech="还没获取到您的位置。在设置里开启定位授权后，我就能告诉您当前在哪，"
                       "也能帮您找最近的地点、导航回家或去公司。",
                follow_up="开启定位后再问我『我在哪』")
        try:
            pt = await self.poi.reverse_geocode(current.lng, current.lat, meta=meta)
        except ProviderError as e:
            # §9.5 铁律③：宁可说不知道，不拿 mock 地址回答「我在哪」。
            logger.warning("locate reverse_geocode failed（诚实降级，无 mock 回退）: %s", e)
            return AgentResult(
                speech="定位服务暂时不可用，暂时说不准您在哪，请稍后再试。",
                data={"lat": current.lat, "lng": current.lng})
        addr = pt.address or "当前位置"
        return AgentResult(
            speech=f"您当前位于{addr}。",
            data={"address": pt.address, "lat": current.lat, "lng": current.lng})

    async def _poi_detail(self, intent, ctx, meta) -> AgentResult:
        """查询 POI 详情。"""
        poi_id = (intent.slots.get("poi_id") or "").strip()
        if not poi_id:
            return AgentResult(status=NEED_SLOT, speech="请提供地点 ID。",
                               missing_slots=["poi_id"])
        try:
            poi = await self.poi.poi_detail(poi_id, meta=meta)
        except ProviderError as e:
            # §9.5 铁律③：不出 mock 假详情卡（假地址/假评分会误导决策）。
            logger.warning("poi_detail failed（诚实降级，无 mock 回退）: %s", e)
            return AgentResult(speech="地图服务暂时不可用，这个地点的详情暂时拿不到，请稍后再试。")
        speech = f"{poi.name}，地址：{poi.address}。"
        if poi.rating:
            speech += f"评分{poi.rating}。"
        card = attach({"type": "poi_detail", "id": poi.id, "name": poi.name,
                       "address": poi.address, "lat": poi.lat, "lng": poi.lng,
                       "rating": poi.rating, "category": poi.category}, self.poi)
        return AgentResult(speech=speech, ui_card=card, data={"poi": card})
