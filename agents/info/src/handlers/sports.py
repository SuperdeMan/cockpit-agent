"""赛事域：赛程/比分/进球详情/射手榜（api-football 结构化，杜绝编造）。

未识别赛事回落通用搜索（self._search，SearchMixin）；历史累计榜亦转搜索。
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import logging
import re

from agents._sdk import AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
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

# 「下一场/接下来某队的比赛什么时候」——未来赛程追问（非今日赛程）。要求同时命中球队名，
# 避免「世界杯什么时候开始」这类无队名的误触发。
_NEXT_HINT = ("下一场", "下场", "下一个", "下一轮", "下轮", "接下来", "下次", "下一次",
              "什么时候", "何时", "啥时候", "几号", "什么日子", "下一回")

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
                ui_card={"type": "sports_scores", "title": f"{league_name} · {team}",
                         "fixtures": [], "freshness": now.isoformat(timespec="minutes"),
                         "source": "api-football"},
                data={"fixtures": []})

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
                card = {"type": "sports_scores",
                        "title": f"{lg} · {f.home} vs {f.away}",
                        "fixtures": [fd],
                        "freshness": now.isoformat(timespec="minutes"),
                        "source": "api-football"}
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
        await self._save_remindable(ctx, res)   # 跨域提醒 P1c：未开赛场次交接给 reminder
        return res
