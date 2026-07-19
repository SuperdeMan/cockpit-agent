"""赛事域：赛程/比分/进球详情/射手榜（api-football 结构化，杜绝编造）。

未识别赛事回落通用搜索（self._search，SearchMixin）；历史累计榜亦转搜索。
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import logging
import re

from agents._sdk import AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.provenance import attach
from agents._sdk.shared_state import REMINDABLE_ACTIVE

from ._util import _shanghai_now

logger = logging.getLogger("agent.info")

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

# 预测/前瞻类问题（badcase 736e4bba/1de7e50c）：api-football 只有**已定事实**（且免费档只开
# 今天±1 天，未来对阵/分析根本给不了）——「谁会赢/预测决赛/判断结果」走结构化路径必然
# 答非所问（把最近完赛场次当答案播）。命中即让路通用搜索（检索对阵+分析后接地综合）。
_PREDICTIVE_HINT = ("预测", "预判", "判断", "前瞻", "赔率", "概率", "可能性",
                    "谁会赢", "谁能赢", "会是谁", "谁胜出", "会胜出", "能胜出",
                    "夺冠热门", "你觉得谁", "怎么看",
                    # badcase demo-i9c92i：「你猜一猜…结果大概是怎么样」没命中预测词 →
                    # 被结构化源接走重播赛程列表。补「猜」族与胜负揣测句式。
                    "猜一猜", "猜猜", "你猜", "猜测", "胜算", "赢面",
                    "会赢", "能赢", "几比几",
                    # badcase f11aa344 复验抓到：「更看好哪支球队」不含「看好谁」——放宽为
                    # 裸「看好」（本词表只在赛事域内消费，不会误伤泛句）；「夺冠」问句结构化
                    # 源同样答不了（免费档无历届冠军），一并让路搜索。
                    "看好", "夺冠")

# api-football round → 中文阶段名（speech 必须带阶段——badcase 736e4bba：赛果不标阶段，
# 聚合 LLM 把四分之一决赛赛果脑补成「半决赛」并错推决赛对阵）。注意 quarter/semi 须在
# final 之前匹配（"Quarter-finals" 含 "final" 子串）。
_ROUND_ZH = (("quarter", "四分之一决赛"), ("semi", "半决赛"),
             ("3rd place", "季军赛"), ("third place", "季军赛"),
             ("final", "决赛"), ("round of 16", "十六强淘汰赛"),
             ("round of 32", "三十二强淘汰赛"), ("group", "小组赛"))


def _round_zh(round_name: str) -> str:
    low = (round_name or "").lower()
    for key, zh in _ROUND_ZH:
        if key in low:
            return zh
    return ""


def _is_predictive(text: str) -> bool:
    return any(w in (text or "") for w in _PREDICTIVE_HINT)

# 追问某具体场次的进球/详情（→ 进球详情）；与"列全部"的列表诉求区分
_DETAIL_HINT = ("进球", "谁进", "射手", "得分", "详细", "赛况", "详情", "战报",
                "经过", "具体", "怎么样", "怎样", "集锦", "介绍", "讲讲", "说说")
_LIST_HINT = ("全部", "所有", "有哪些", "哪些比赛", "哪些场", "赛程", "列表",
              "几场", "都有", "还有")
# 进球/战报类**强详情词**：与列表词共现时详情优先（badcase 8e23ce30：「这场比赛都有
# 谁进球」的「都有」误判成列表诉求，进球明细被吞成当日比分汇总）。弱详情词
# （怎么样/详情）不夺权——「赛程怎么样」仍是列表。
_GOAL_DETAIL_HINT = ("进球", "谁进", "射手", "得分", "战报", "赛况", "集锦", "经过")
# 射手榜（联赛级排行，非某场）—— 独立于赛程列表与单场进球详情
_SCORERS_HINT = ("射手榜", "射手", "金靴", "得分王", "进球榜", "神射手",
                 "谁进球最多", "谁球最多", "topscorer", "top scorer")
# 历史/累计「总」射手榜：按赛季的 topscorers API 给不了 → 走通用搜索（接地合成历史榜）
_ALLTIME_HINT = ("总射手", "历史射手", "历届", "历史总", "累计", "史上",
                 "历史最佳", "历史进球", "all-time", "总进球", "历史榜")
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_ORDINAL_RE = re.compile(r"第\s*(\d+|[一二两三四五六七八九十]+)\s*场")

# 「下一场/接下来某队的比赛什么时候」——未来赛程追问（非今日赛程）。要求同时命中球队名，
# 避免「世界杯什么时候开始」这类无队名的误触发。
_NEXT_HINT = ("下一场", "下场", "下一个", "下一轮", "下轮", "接下来", "下次", "下一次",
              "什么时候", "何时", "啥时候", "几号", "什么日子", "下一回")

# 预测问句里的场次指代（「这一场/那场比赛」）——无日期词时按今天→明天顺序找具体对阵作检索锚点
_ANAPHOR_HINT = ("这场", "那场", "这一场", "那一场", "这次", "那次", "这个比赛", "那个比赛")

# 显式日期词（与 _sports_date 同源口径）：预测指代解析时判「本句是否自带日期」——
# 自带则只按该日找；没带才用「最近赛事轮的焦点日期 → 今天 → 明天」链（badcase bfb5d9c7）。
_DATE_WORDS = ("今天", "今日", "今晚", "明天", "明晚", "明早", "后天", "大后天",
               "昨天", "昨晚", "昨夜", "前天")
_WEEKDAY_RE = re.compile(r"(?:下+)?(?:周|星期|礼拜)[一二三四五六日天]")


def _has_date_word(text: str) -> bool:
    t = text or ""
    return any(w in t for w in _DATE_WORDS) or bool(_WEEKDAY_RE.search(t))


# 阶段词 → _round_zh 规范名（长词在前：「半决赛/四分之一决赛」含「决赛」子串）。
# 预测指代解析按阶段过滤场次——badcase bfb5d9c7：「决赛谁会赢」不加阶段过滤会锚到
# 当天的季军赛（planner 又把历史里的法英对阵缝进「决赛」，成幻觉查询）。
_STAGE_CANON = (("四分之一决赛", "四分之一决赛"), ("1/4决赛", "四分之一决赛"),
                ("半决赛", "半决赛"), ("季军赛", "季军赛"), ("季军战", "季军赛"),
                ("三四名", "季军赛"), ("十六强", "十六强淘汰赛"),
                ("小组赛", "小组赛"), ("决赛", "决赛"))


def _stage_in(text: str) -> str:
    t = text or ""
    for w, canon in _STAGE_CANON:
        if w in t:
            return canon
    return ""

# 复用 api-football provider 的中文国家队名表做队名提取（同域同包）；导入失败则退化空表。
try:
    from ..providers.sports_apifootball import _ZH_TEAMS as _AF_ZH_TEAMS, flag_for as _flag_for
    _TEAM_NAMES = sorted(set(_AF_ZH_TEAMS.values()), key=len, reverse=True)
except Exception:  # pragma: no cover
    _TEAM_NAMES = []

    def _flag_for(_name):
        return ""


def _find_team(text: str) -> str:
    """从查询中提取已知中文国家队名（长名优先，避免子串误命中）。无则返回 ''。"""
    t = text or ""
    for name in _TEAM_NAMES:
        if name in t:
            return name
    return ""


def _fmt_kickoff(iso: str) -> str:
    """ISO 开球时间 → 「MM-DD HH:MM」（上海时区，api-football 已按 timezone 返回）。"""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso[:16].replace("T", " ")


def _kickoff_epoch(iso: str) -> int:
    """ISO 开球时间 → epoch 秒（跨域提醒用）；解析失败返回 0。
    api-football 返回带时区；万一裸时间按上海时区（与 _fmt_kickoff 同源假设）。"""
    if not iso:
        return 0
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return 0


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


_WEEKDAY_ZH = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _sports_date(query: str, now: datetime) -> str:
    """从查询推断目标日期，默认今天（YYYY-MM-DD，上海时区）。

    Q3（旅程 A5-2/A3-1 抓到）：「下周三」「昨晚」原来解析不了 → 静默回落今天，
    答出「**今天**没有查询到…」答非所问。补齐常见相对日期与周几；解析出的日期
    超出数据源窗口时由 `_do_sports` 的门控分支按**所问日期**口径诚实告知。
    """
    def d(offset: int) -> str:
        return (now + timedelta(days=offset)).strftime("%Y-%m-%d")

    if "大后天" in query:
        return d(3)
    if "后天" in query:
        return d(2)
    if "前天" in query:
        return d(-2)
    if any(w in query for w in ("昨天", "昨晚", "昨夜")):
        return d(-1)
    if any(w in query for w in ("明天", "明晚", "明早")):
        return d(1)
    m = re.search(r"(下+)?(?:周|星期|礼拜)([一二三四五六日天])", query)
    if m:
        target = _WEEKDAY_ZH[m.group(2)]
        n_down = len(m.group(1) or "")
        if n_down:      # 「下周X」=下个自然周（周一起算）的周X；「下下周」再 +7
            ahead = (7 - now.weekday()) + target + 7 * (n_down - 1)
        else:           # 裸「周X」=本周该天（已过则下周），当天算今天
            ahead = (target - now.weekday()) % 7
        return d(ahead)
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


class SportsMixin:
    @staticmethod
    def _fixture_dict(f) -> dict:
        scored = f.status in ("finished", "live") and f.home_goals != ""
        return {
            "league": f.league, "round": f.round,
            "home": f.home, "away": f.away,
            "home_logo": f.home_logo, "away_logo": f.away_logo,
            "home_flag": _flag_for(f.home), "away_flag": _flag_for(f.away),
            "score": f"{f.home_goals}-{f.away_goals}" if scored else "",
            "home_goals": f.home_goals, "away_goals": f.away_goals,
            "status": f.status, "status_text": f.status_text,
            "elapsed": f.elapsed, "kickoff": f.kickoff,
        }

    async def _save_remindable(self, ctx, res) -> None:
        """未开赛场次 → 可提醒上下文（跨域提醒 P1c，best-effort 失败不影响出卡）。

        卡驱动：从 `sports_scores` 卡的 fixtures 收割。**写全部有 kickoff 的场次**
        （含已结束——序号必须与卡片渲染严格同序，「第二场提醒我」不能因首场已结束而错位；
        过去项由消费侧按「已经开始」诚实答复）。无 kickoff 的卡不写、不覆盖旧值。
        见 2026-07-11-reminder-cross-domain.md §3.1/§6。
        """
        try:
            card = getattr(res, "ui_card", None) or {}
            if ctx is None or card.get("type") != "sports_scores":
                return
            items = []
            for fd in card.get("fixtures") or []:
                ts = _kickoff_epoch(fd.get("kickoff", ""))
                if ts:
                    items.append({"title": f"{fd.get('home', '')} vs {fd.get('away', '')}",
                                  "fire_at": ts})
            if items:
                await ctx.save_shared_state(REMINDABLE_ACTIVE, {
                    "source": "info.sports", "label": card.get("title", "赛程"),
                    "ts": int(_shanghai_now().timestamp()), "items": items})
        except Exception as e:
            logger.debug("sports remindable save skipped: %s", e)

    async def _maybe_sports(self, query: str, meta, raw_text: str = "") -> AgentResult | None:
        """命中「已知赛事 + 赛事意图词」才路由到结构化数据源；否则返回 None 走通用搜索。

        组合 ``query``（planner 解析后的槽位，跟进句靠它带回「世界杯」）与 ``raw_text``
        （完整原话，带回「明天/昨天」等时间词）一起识别——单用任一个都会漏：
        跟进句「明天的呢」raw_text 无赛事名、slots.query 又可能丢时间词（实测 bug）。
        """
        text = f"{query} {raw_text}".strip()
        if _is_predictive(text):
            return None            # 预测/前瞻：结构化源答不了，让通用搜索接手
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
        # 「下一场阿根廷的比赛什么时候」——查该队未来赛程（而非今日赛程列表）。
        team = _find_team(query)
        if team and self._is_next_match(query):
            res = await self._next_team_match(team, league_id, league_name, meta)
            if res is not None:
                return res
            # 扫描窗口内无该队赛程 → 诚实告知（不回退今日无关列表误导用户）。
            # 免费档只开放近两天，更靠后的赛程查不到。
            return AgentResult(
                speech=f"没有查询到{team}近两天的{league_name}赛程；受免费数据源限制，"
                       f"更靠后的赛程暂时查不到，可稍后再问或换个近期有比赛的球队。",
                ui_card=attach({"type": "sports_scores", "title": f"{league_name} · {team}",
                                "fixtures": [],
                                "freshness": now.isoformat(timespec="minutes"),
                                "source": "api-football"}, self.sports),
                data={"fixtures": []})

        date = _sports_date(query, now)
        try:
            # 按日期查全联赛、再客户端按 league_id 精确过滤：
            # date+league+season 在 api-football 免费档常被「赛季门限」挡（2026 季不开放），
            # 而单日期查询（今天±1 窗口）免费档放行，付费档同样适用——故统一走日期查。
            all_fixtures = await self.sports.fixtures(date=date, meta=meta)
        except ProviderError as e:
            logger.warning("sports fixtures failed: %s", e)
            # 日期门控（免费档只放行今天±1）→ 按**所问日期**口径诚实（Q3：原来静默回落
            # 今天，答「今天没有查询到」答非所问）；其余故障返回 None → 上层回落通用搜索。
            msg = str(e).lower()
            if "access to this date" in msg or "not have access" in msg:
                asked = "今天" if date == now.strftime("%Y-%m-%d") else date[5:]
                return AgentResult(
                    speech=f"受数据源限制，只能查今天前后一天的赛程，{asked}的暂时查不到，"
                           f"可以临近了再问我。")
            return None
        fixtures = [f for f in all_fixtures if f.league_id == league_id]

        date_label = "今天" if date == now.strftime("%Y-%m-%d") else date[5:]
        title = f"{league_name} · {date_label}"
        freshness = now.isoformat(timespec="minutes")
        if not fixtures:
            return AgentResult(
                speech=f"{date_label}没有查询到{league_name}的比赛安排。",
                ui_card=attach({"type": "sports_scores", "title": title, "fixtures": [],
                                "freshness": freshness, "source": "api-football"},
                               self.sports),
                data={"fixtures": []})

        # 追问某具体场次（第N场/队名）且非"列全部"诉求 → 进球详情（射手/分钟）
        picked = self._pick_fixture(query, fixtures)
        # 「这场/那场」纯指代解析不到序号/队名；当日仅此一场时它就是指代对象
        # （badcase 8e23ce30：「这场比赛都有谁进球」落回当日比分汇总，没查进球明细）
        if picked is None and len(fixtures) == 1 and (
                any(w in query for w in _ANAPHOR_HINT) or self._is_detail_request(query)):
            picked = fixtures[0]
        # 进球类强详情词优先于列表词：「都有谁进球」的「都有」不再劫持成列表
        if picked is not None and (self._is_goal_detail(query)
                                   or not self._is_list_request(query)):
            return await self._match_detail(picked, league_name, meta)

        finished = [f for f in fixtures if f.status == "finished"]
        live = [f for f in fixtures if f.status == "live"]
        scheduled = [f for f in fixtures if f.status == "scheduled"]
        # 阶段标注：同日同轮（淘汰赛常态）时在首句点明「四分之一决赛/半决赛」——不标阶段
        # 会让下游把 1/4 决赛赛果当半决赛错推决赛对阵（badcase 736e4bba）。
        rounds = {_round_zh(f.round) for f in fixtures if _round_zh(f.round)}
        stage = rounds.pop() if len(rounds) == 1 else ""
        parts = [f"{date_label}{league_name}{stage}共{len(fixtures)}场比赛"]
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
            # 点名对阵（badcase bfb5d9c7 根因链：只报「未开赛1场」不说是谁对谁，语音端
            # 听不到对阵，下游追问「这场谁会赢」时 planner 只能从更早历史里缝合错对阵）
            segs = []
            for f in scheduled[:4]:
                seg = f"{f.home} vs {f.away}"
                ko = _fmt_kickoff(f.kickoff)
                if ko and len(scheduled) <= 2:
                    seg += f"（{ko.split(' ')[-1]} 开球）"
                segs.append(seg)
            parts.append(f"未开赛{len(scheduled)}场：" + "、".join(segs))
        speech = "，".join(parts) + "。"

        card = attach({"type": "sports_scores", "title": title,
                       "fixtures": [self._fixture_dict(f) for f in fixtures],
                       "freshness": freshness, "source": "api-football"}, self.sports)
        return AgentResult(speech=speech, ui_card=card,
                           data={"fixtures": card["fixtures"]})

    @staticmethod
    def _is_detail_request(text: str) -> bool:
        return any(w in (text or "") for w in _DETAIL_HINT)

    @staticmethod
    def _is_goal_detail(text: str) -> bool:
        return any(w in (text or "") for w in _GOAL_DETAIL_HINT)

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
    def _is_next_match(text: str) -> bool:
        """是否问的是「下一场/接下来某队的比赛」（未来赛程，非今日列表）。"""
        return any(w in (text or "") for w in _NEXT_HINT)

    async def _next_team_match(self, team_zh: str, league_id: int,
                               league_name: str, meta) -> AgentResult | None:
        """某队下一场比赛：从今天起按日扫描（免费档只放行 date 参数——`next`/`season` 均被门控），
        逐日过滤联赛+队伍+未结束，命中即停。窗口 10 天覆盖世界杯赛程节奏；无数据返回 None（回落）。"""
        now = _shanghai_now()
        for offset in range(0, 10):
            date = (now + timedelta(days=offset)).strftime("%Y-%m-%d")
            try:
                fx = await self.sports.fixtures(date=date, meta=meta)
            except ProviderError as e:
                logger.warning("next-match scan %s failed: %s", date, e)
                # 免费档只开放近两天；命中「date not accessible」后续日期都会失败 → 停止扫描
                if "access to this date" in str(e).lower() or "not have access" in str(e).lower():
                    break
                continue
            for f in fx:
                if league_id and f.league_id != league_id:
                    continue
                if team_zh not in (f.home, f.away):
                    continue
                if f.status == "finished":
                    continue  # 已结束的跳过，找下一场未开赛/进行中的
                full = _fmt_kickoff(f.kickoff)   # "MM-DD HH:MM"
                time_part = full.split(" ")[-1] if " " in full else full
                when_label = (f"今天 {time_part}" if offset == 0
                              else f"明天 {time_part}" if offset == 1 else full)
                lg = f.league or league_name
                speech = f"{team_zh}下一场比赛：{lg} {f.home} vs {f.away}"
                speech += f"，{when_label} 开球。" if full else "。"
                fd = self._fixture_dict(f)
                card = attach({"type": "sports_scores",
                               "title": f"{lg} · {f.home} vs {f.away}",
                               "fixtures": [fd],
                               "freshness": now.isoformat(timespec="minutes"),
                               "source": "api-football"}, self.sports)
                return AgentResult(speech=speech, ui_card=card, data={"fixtures": [fd]})
        return None

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
        stage = _round_zh(f.round)     # 单场详情同样点明阶段（badcase 736e4bba 同源）
        head = (f"{league_name}{stage}，{f.home} {f.home_goals}-{f.away_goals} {f.away}"
                if scored else f"{league_name}{stage}，{f.home} 对阵 {f.away}")
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
        card = attach({"type": "sports_scores",
                       "title": f"{league_name} · {f.home} vs {f.away}",
                       "fixtures": [fd],
                       "freshness": _shanghai_now().isoformat(timespec="minutes"),
                       "source": "api-football"}, self.sports)
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
            # 诚实降级用 OK——FAILED 话术会被聚合器吞掉换成裸「处理失败」（R9/scene 同坑）
            return AgentResult(
                speech=f"暂时获取不到{league_name}的射手榜，可能是数据源限制，请稍后再试。")

        label = f"{used_season}赛季"
        top3 = "、".join(f"{s.player} {s.goals}球（{s.team}）" for s in scorers[:3])
        speech = f"{league_name}（{label}）射手榜：{top3}。"
        card = attach({"type": "sports_scorers",
                "title": f"{league_name} 射手榜", "season": label,
                "scorers": [{"rank": s.rank, "player": s.player,
                             "team": s.team, "goals": s.goals} for s in scorers[:10]],
                "freshness": _shanghai_now().isoformat(timespec="minutes"),
                "source": "api-football"}, self.sports)
        return AgentResult(speech=speech, ui_card=card, data={"scorers": card["scorers"]})

    async def _focus_date_from_history(self, ctx, now) -> str:
        """最近一轮赛事相关**用户**发言的日期语境（badcase bfb5d9c7 的焦点锚）。

        「明天世界杯有什么比赛」→「这场比赛你预测谁会赢」：「这场」指的是明天那场，
        不是今天的。只扫用户轮——assistant 播报里的「今天/07-20」是转述口径，直接
        解析会把焦点拽回播报当天。最近一轮提及联赛/赛事词的用户句即焦点轮，取其
        日期词（无日期词=今天）。取不到返回 ""。"""
        try:
            turns = await ctx.history(6)
        except Exception as e:
            logger.debug("sports focus history fetch failed: %s", e)
            return ""
        for t in reversed(turns or []):
            if (t.get("role") or "") != "user":
                continue
            tx = t.get("text") or ""
            if not tx:
                continue
            if _detect_league(tx)[0] or any(h in tx for h in _SPORTS_HINT):
                return _sports_date(tx, now)
        return ""

    async def _resolve_predictive(self, ref_text: str, league_text: str, ctx, meta,
                                  assume_sports: bool = False):
        """预测问句的场次解析。返回 (检索锚点, 已定局答案 AgentResult|None)。

        锚点=把「这场/明天那场/决赛」解析成具体对阵（阶段+双方+开球时间）拼进检索
        query；若明确指代的场次**已经完赛**（badcase bfb5d9c7：季军赛已 4-6 终场还被
        当未来赛出预测），预测无从谈起——直接返回结构化赛果作答（系统持有的事实
        不让检索/LLM 猜）。日期锚定顺序：本句显式日期词 > 最近赛事轮的焦点日期 >
        今天 → 明天；带阶段词（决赛/季军赛）时按阶段过滤场次。
        解析不出（无联赛上下文/无赛程/provider 故障）返回 ("", None)——按原话走。

        **ref_text=用户原话**：指代词/日期词/阶段词只从原话取——planner 改写的 query
        是不可信的指代通道（真栈复验二层缺口：planner 把「今天这场」缝成「决赛 西班牙
        vs 阿根廷」，query 里的「决赛」把当天季军赛阶段过滤清空，完赛短路失效）。
        league_text=query+原话：联赛识别是封闭词表无幻觉面，query 里的「世界杯」可信。

        assume_sports：经 info.sports 意图进来时为 True，允许在本句无赛事词时也查
        历史回填联赛；info.search 入口为 False（非赛事的预测句不多付一次历史 RTT）。"""
        league_id, name = _detect_league(league_text)
        if not league_id and ctx is not None and (
                assume_sports or any(h in ref_text for h in _SPORTS_HINT)
                or any(w in ref_text for w in _ANAPHOR_HINT) or _stage_in(ref_text)):
            try:
                league_id, name = await self._league_from_history(ctx)
            except Exception:  # 历史回填失败不阻塞
                league_id = 0
        if not league_id:
            return "", None
        now = _shanghai_now()
        today = now.strftime("%Y-%m-%d")
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        stage = _stage_in(ref_text)
        # 指代是否落到「具体某场」：指代词/阶段词/显式日期任一 → 完赛时可直接报赛果；
        # 泛问（「世界杯你看好谁夺冠」）不具体，完赛场次只跳过、绝不抢答成单场结果。
        specific_ref = bool(stage or _has_date_word(ref_text)
                            or any(w in ref_text for w in _ANAPHOR_HINT))
        if _has_date_word(ref_text):
            dates = [_sports_date(ref_text, now)]
        else:
            dates = []
            if ctx is not None:
                focus = await self._focus_date_from_history(ctx, now)
                if focus:
                    dates.append(focus)
            dates += [today, tomorrow]
        seen: set[str] = set()
        for d in dates:
            if d in seen:
                continue
            seen.add(d)
            try:
                fixtures = [f for f in await self.sports.fixtures(date=d, meta=meta)
                            if f.league_id == league_id]
            except ProviderError as e:
                logger.debug("predictive anchor fixtures failed: %s", e)
                return "", None
            if stage:
                fixtures = [f for f in fixtures if _round_zh(f.round) == stage]
            if not fixtures:
                continue
            label = "今天" if d == today else ("明天" if d == tomorrow else d[5:])
            upcoming = [f for f in fixtures if f.status != "finished"]
            if upcoming:
                parts = []
                for f in upcoming[:2]:   # 最多两场，锚点保持紧凑
                    st = _round_zh(f.round)
                    seg = f"{st}{'：' if st else ''}{f.home} vs {f.away}"
                    ko = _fmt_kickoff(f.kickoff)
                    if ko:
                        seg += (f"（{ko} 开球，尚未开赛）" if f.status == "scheduled"
                                else "（进行中）")
                    parts.append(seg)
                return f"{label}的{name}{'；'.join(parts)}", None
            if specific_ref and len(fixtures) == 1:
                res = await self._match_detail(fixtures[0], name, meta)
                res.speech = "这场比赛已经踢完了，结果是：" + res.speech
                return "", res
            # 该日全部完赛且指代不到唯一一场 → 看下一个候选日期
        return "", None

    async def _predictive_redirect(self, intent, ctx, meta,
                                   assume_sports: bool = False):
        """预测/前瞻句统一收口（info.sports 与 planner 直路由 info.search 两入口共用）：
        指代→具体对阵锚点拼进检索 query（以原话+结构化对阵重建，**丢弃** planner 可能
        缝合出的幻觉对阵——badcase bfb5d9c7 把历史里的法英 4-6 与「决赛」缝成
        「决赛 法国 vs 英格兰」）；指代场次已完赛→直接报结构化赛果。
        解析不出返回 None，调用方按原 query 继续（不降级已有的 LLM 改写）。"""
        query = (intent.slots.get("query") or "").strip()
        ref = (intent.raw_text or query).strip()
        anchor, settled = await self._resolve_predictive(
            ref, f"{query} {intent.raw_text or ''}".strip(), ctx, meta,
            assume_sports=assume_sports)
        if settled is not None:
            return settled
        if not anchor:
            return None
        q = (intent.raw_text or intent.slots.get("query") or "").strip()
        intent.slots["query"] = f"{q}（用户问的是{anchor}）"
        return await self._search(intent, ctx, meta, skip_sports=True)

    async def _sports(self, intent, ctx, meta) -> AgentResult:
        """info.sports 意图入口。识别赛事后取结构化数据；未识别则回落通用搜索。"""
        query = (intent.slots.get("query") or intent.slots.get("league")
                 or intent.slots.get("topic") or "").strip()
        text = f"{query} {intent.raw_text or ''}".strip()
        if not text:
            return AgentResult(status=NEED_SLOT, speech="您想查询哪个赛事的比分或赛程？",
                               follow_up="请告诉我赛事名称", missing_slots=["query"])
        # 预测/前瞻（badcase 1de7e50c「预测半决赛和决赛结果」被答成单场已结束赛果）：
        # 结构化源只有已定事实且免费档拿不到未来对阵 → 经 _predictive_redirect 收口：
        # 指代解析成具体对阵锚定检索（badcase demo-i9c92i）、指代场次已完赛直接报赛果
        # （badcase bfb5d9c7）。解析不出按原话整句检索（保住「半决赛/决赛/预测」上下文），
        # skip_sports 防回环。
        if _is_predictive(text):
            res = await self._predictive_redirect(intent, ctx, meta, assume_sports=True)
            if res is not None:
                return res
            intent.slots["query"] = (intent.raw_text or query).strip()
            return await self._search(intent, ctx, meta, skip_sports=True)
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
            # R9（旅程 A3-1/A2-2a 抓到）：provider 故障时原来返回 FAILED——聚合器会吞掉
            # FAILED 话术换成裸「抱歉，处理失败」（scene 主题登记过的同一坑）。降级升级为
            # **回落通用搜索接地**（_search 自带诚实弃权），skip_sports 防再进结构化源
            # 二次吃超时。原话整句作 query，保住「昨晚/决赛」等上下文。
            logger.warning("sports provider down, fallback to grounded search: %s", text[:40])
            intent.slots["query"] = (intent.raw_text or query).strip()
            return await self._search(intent, ctx, meta, skip_sports=True)
        await self._save_remindable(ctx, res)   # 跨域提醒 P1c：未开赛场次交接给 reminder
        return res
