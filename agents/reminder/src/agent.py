"""智能提醒 Agent：自然语言创建日程提醒/待办 + 列表/完成/取消 + 到点 proactive 触达。

设计：docs/design/2026-07-11-reminder-agent-design.md（已批准，含 D7）。
时间可测性：所有"现在"取 self._now_utc()（测试注入固定时钟）。
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from agents._sdk import BaseAgent, AgentResult, NEED_CONFIRM, NEED_SLOT, FAILED
from agents._sdk.shared_state import (REMINDABLE_ACTIVE, REMINDERS_ACTIVE,
                                      REMINDER_PENDING, owner_scoped)
from runtime.proactive import publish_proactive

from .placeparse import ARRIVE, parse_place_text
from .task_admission import admit_task_title
from .store import (Reminder, ReminderStore, DONE, CANCELLED, FIRED,
                    LOCATION, PENDING)
from .timeparse import (OK as T_OK, FAIL as T_FAIL, ParsedTime, align_workday,
                        business_tz, format_display, parse_lead, parse_recur,
                        parse_time_text, recur_label, strip_time_expressions)

logger = logging.getLogger("agent.reminder")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

_GEOFENCE_RADIUS_M = 300      # 默认围栏半径（米）——够近，又不至于反复进出抖动
_TODO_RE = re.compile(r"记一下|记个|待办|备忘")
_CMD_STRIP_RE = re.compile(
    r"^(麻烦|请|帮我|给我)?(再)?(提醒我|叫我|别忘了|记得|记一下|记个待办|记个|设个提醒|建个提醒|待办[:：]?)+")
_ORDINAL_RE = re.compile(r"第([一二三四五六七八九十0-9]+)\s*[条个项场]?")   # 场：跨域「第N场」
_ALL_RE = re.compile(r"全部|所有|都|清空|全删")
from runtime.polarity import NEG_WORDS   # 极性词表唯一来源（Q7/Q11 共用）

_AGAIN_RE = re.compile(r"再(提醒|叫)")   # P1a：显式 snooze 标记（「过10分钟再提醒我」）
#: 标题尾巴上的**域词**（「…的提醒」「…那条待办」）。查空之后再削一次尾，见
#: `_resolve_targets`。**必须带「的/这条/那条」这类连接词**——只削光杆「提醒」会把
#: 一条真叫「买提醒」的待办削成「买」，而那一步是在扩大匹配面，不是缩小。
_TITLE_DOMAIN_TAIL_RE = re.compile(r"(?:的|这条|那条|这个|那个)\s*(?:提醒|待办)\s*$")
# Q11 否定守卫（I-009②）：用户明说「别建提醒」。**极性词表来自 `runtime.polarity`**
# ——卡 §3-Q11 明写它与 Q7 的极性维度同源，共用一份，别写第二份。
# 这里只补「否定的宾语是**提醒这件事**」这半：`polarity` 判的是「别做某个动作」，
# 而「别建提醒」的动作词（建/设/记）不在车控动作表里。
# ⚠ 多一个 `不用`：它在共享词表里被**刻意排除**（「不用了」全局歧义——那是取消挂起，
# 见 `pending_cancel`），但**宾语是「提醒/闹钟」时不歧义**（「不用提醒我」）。
# 这不是第二份词表，是本域给共享词表补一个只在本域成立的词。
_NO_REMINDER_RE = re.compile(
    rf"(?:{NEG_WORDS}|不用)[^，。；！？,;!?]{{0,4}}?(?:建|设|加|记|存|要)?"
    r"[^，。；！？,;!?]{0,3}?(?:提醒|闹钟|日程|待办)")
# 双重否定：「**别忘了**提醒我开会」真实语义是**要建**。挡它等于反向漏执行
# ——同 `runtime.polarity` 的 `_DOUBLE_NEGATIVE_RE`，必须早于否定判据求值。
_REMINDER_DOUBLE_NEG_RE = re.compile(rf"(?:{NEG_WORDS}|不用)\s*(?:忘|忘了|忘记)")
_BATCH_SPLIT_RE = re.compile(r"\s*[，,；;]\s*")
_BATCH_REPEAT_RE = re.compile(r"再(?:提醒|叫)我(?:一|1)次")
_BATCH_DAY_RE = re.compile(r"大后天|后天|明天|今天")
_BATCH_SEGMENT_RE = re.compile(r"凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里")
_BATCH_CONDITIONAL_RE = re.compile(r"^(?:如果|要是|假如|若|只要|除非)")
_TIME_SIGNAL_RE = re.compile(
    r"今天|今晚|今早|明天|明早|明晚|后天|大后天|"
    r"周末|月底|月初|年末|年初|饭点|睡前|起床|稍后|待会|一会儿|"
    r"(?:周|星期|礼拜)[一二三四五六日天末]|"
    r"凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|"
    r"(?:\d+|[一两二三四五六七八九十]+)\s*"
    r"(?:年|月|日|号|点|分|分钟|小时|钟头|秒|秒钟)|"
    r"(?:[01]?\d|2[0-3])[:：][0-5]\d|"
    r"开赛前|开始前|到达前|抵达前|到[^，。,]{0,6}之?前",
)
# P1c 跨域：无序号时的事件指代词形（含"赛/场/开始"语素，刻意收窄防泛指误命中）
# R7 扩词形：navigation 产 ETA 事件后，「到之前/快到（的时候）/到达前/到X之前」也是对
# REMINDABLE 事件的指代（「到之前一刻钟提醒我给张姐打电话」「到公司之前提醒我交周报」——
# 后者中间隔了地点词，字面「到之前」匹配不上，旅程 B5-1 抓到）。
_REMINDABLE_REF_RE = re.compile(
    r"这场|那场|到时候|开赛|开场|比赛开始|开始前|开始的?时候|"
    r"到[^，。,]{0,6}之?前|到达前|抵达前|快到(的时候|时)?")
_CN_IDX = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _has_time_signal(text: str) -> bool:
    """Whether the user's own wording contains a temporal anchor."""
    return bool(_TIME_SIGNAL_RE.search(text or ""))


class ReminderAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.store = ReminderStore()
        self._nc = None
        self._tz = business_tz()
        self._sched_task = None
        self._geofence = None
        self._veh_state: dict = {}

    # ── 生命周期：存储初始化 + NATS + 调度循环（road-safety 先例）──
    async def on_start(self) -> None:
        await self.store.init()
        nats_url = os.getenv("NATS_URL", "")
        if nats_url:
            try:
                import nats
                self._nc = await nats.connect(nats_url, max_reconnect_attempts=-1)
                logger.info("reminder: NATS 已连接，主动触达开启")
            except Exception as e:
                logger.warning("reminder: NATS 连接失败，主动触达禁用：%s", e)
        else:
            logger.info("reminder: NATS_URL 未设置，主动触达禁用")
        from .scheduler import ReminderScheduler
        self._sched_task = asyncio.create_task(
            ReminderScheduler(self.store, self._publish_proactive).run_forever())
        if self._nc:                     # M3 P1：位置提醒围栏（复用同一条车况广播）
            from .geofence import GeofenceWatcher
            self._geofence = GeofenceWatcher(
                self.store, self._publish_proactive, tz=self._tz)
            await self._nc.subscribe("vehicle.state.changed", cb=self._on_state_event)
            logger.info("reminder: 已订阅车况，位置提醒围栏开启")

    async def _on_state_event(self, msg) -> None:
        try:
            event = json.loads(msg.data.decode())
        except Exception:
            return
        for c in event.get("changes") or []:
            if isinstance(c, dict) and c.get("key"):
                self._veh_state[c["key"]] = c.get("new")
        if not self._geofence:
            return
        try:
            await self._geofence.on_state([], dict(self._veh_state))
        except Exception as e:           # 围栏是旁路，异常绝不拖垮 Agent
            logger.warning("reminder: 围栏判定异常（忽略）：%s", e)

    async def _publish_proactive(self, payload: dict) -> None:
        """到点/到地触达经主动治理器（`user_contract` 档：免打扰/负荷/频控全豁免）。

        提醒是**用户显式约定**，到点必响的契约不因治理让路；治理器对它只做合并
        （和同窗到达的别的消息说成一句，而不是连响两次）。
        """
        if not self._nc:
            logger.info("reminder fired（NATS 禁用未推送）: %s",
                        payload.get("speech", "")[:40])
            return
        await publish_proactive(self._nc, payload)

    # ── 请求-响应 ──
    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {"reminder.create": self._create,
                    "reminder.create_batch": self._create_batch,
                    "reminder.list": self._list,
                    "reminder.complete": self._complete, "reminder.cancel": self._cancel,
                    "reminder.update": self._update}
        h = handlers.get(intent.name)
        if not h:
            # R9 契约：诚实拒绝用 OK——FAILED 话术会被聚合器吞成裸「抱歉，处理失败」
            # （M0a 三 Agent 同款修法；真栈 badcase 2026-07-24「取消观看的提醒」第四处补齐）
            return AgentResult(speech="提醒助手暂不支持该请求。")
        return await h(intent, ctx, meta)

    # 测试注入点：所有"现在"经此取
    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _uid(ctx) -> str:
        return ctx.user_id or "u1"

    @staticmethod
    def _occ(ctx) -> str:
        """本轮说话人（M-B）。提醒的 owner 是 (user_id, occupant_id)——此前全域零
        occupant，两位乘员的提醒混在一张表、序号互相污染、触达不区分人。
        认不出时回落 primary（既定兼容行为），**occupant 永不参与鉴权**。"""
        return (getattr(ctx, "occupant_id", "") or "").strip() or "primary"

    def _state_key(self, ctx, key: str) -> str:
        """per-speaker 会话态键（列表序号 / 待补槽）收窄到 OwnerKey。"""
        return owner_scoped(key, self._uid(ctx), self._occ(ctx))

    # ── create（含 P1a：update 续接 / snooze 收编 / 重复规则）──
    async def _create_batch(self, intent, ctx, meta) -> AgentResult:
        """同一事项、两个明确时刻的一次性创建（SL1）。

        该能力只由 manifest 的整句窄 route hint 进入；这里仍独立复核形状和两个未来
        时刻，再用存储事务整组写入。它不接受自由的「多提醒」列表，避免在 Agent 内
        复制一套通用 planner。
        """
        raw = str(intent.raw_text or "").strip()
        parts = _BATCH_SPLIT_RE.split(raw)
        if (len(parts) != 2 or not _BATCH_REPEAT_RE.search(parts[1])
                or _BATCH_CONDITIONAL_RE.search(raw)
                or (not _REMINDER_DOUBLE_NEG_RE.search(raw)
                    and _NO_REMINDER_RE.search(raw))):
            return AgentResult(
                status=NEED_SLOT,
                speech="这组提醒的两个时间我还没听完整，请分别说清楚。",
                follow_up="比如：明天下午四点提醒我开会，三点半再提醒我一次",
                missing_slots=["time_text"],
            )

        first, second = parts
        title = self._extract_title(first)
        day = _BATCH_DAY_RE.search(first)
        segment = _BATCH_SEGMENT_RE.search(first)
        # 这条能力只接「同一天、同一事项，第二次省略日/段位」；第二段若自己另给日期，
        # 交回普通 planner，避免擅自决定它是否还指同一件事。
        if (not title or day is None or _BATCH_DAY_RE.search(second)):
            return AgentResult(
                status=NEED_SLOT,
                speech="这组提醒的事项或日期不够明确，请分两句告诉我。",
                missing_slots=["title", "time_text"],
            )
        inherited = day.group(0)
        if segment is not None and _BATCH_SEGMENT_RE.search(second) is None:
            inherited += segment.group(0)
        second_time_text = inherited + second

        now = self._now_utc()
        parsed = [
            parse_time_text(first, now=now, tz=self._tz),
            parse_time_text(second_time_text, now=now, tz=self._tz),
        ]
        if (any(pt.status != T_OK or pt.fire_at <= int(now.timestamp()) for pt in parsed)
                or parsed[0].fire_at == parsed[1].fire_at):
            return AgentResult(
                status=NEED_SLOT,
                speech="这组提醒需要两个不同的未来时刻，请再说一次。",
                missing_slots=["time_text"],
            )

        # C10-C：同一条任务性准入（判据只有一份），批量口同样不许建垃圾标题。
        ok, why = admit_task_title(title)
        if not ok:
            logger.info("reminder.create_batch 拒建（%s）：%s", why, title[:40])
            return AgentResult(
                speech=f"「{title}」听着不像一件要做的事，我没建提醒。",
                follow_up="要建的话说清楚做什么，比如「明天九点提醒我交周报」。")
        turn = str((meta or {}).get("trace_id") or "")
        # C10-E：**整组都已经存在**才算跨轮重复。本能力的写入是原子的
        # （「任一无效时一条也不落」），所以幂等也按整组判——只挡「同一句话又说了
        # 一遍」这个真实形态，不去拆一半建一半破坏原子语义。
        existing = [await self._cross_turn_duplicate(ctx, title, pt.fire_at, turn)
                    for pt in parsed]
        if all(existing):
            await self._refresh_active(ctx)
            await self._clear_pending(ctx)
            return AgentResult(
                speech=(f"{parsed[0].display}和{parsed[1].display}的"
                        f"「{title}」都已经有了，就不重复建了。"),
                follow_up="要改时间说「改到几点」，不要了说「取消」。")
        reminders = [Reminder(
            user_id=self._uid(ctx), occupant_id=self._occ(ctx),
            vehicle_id=ctx.vehicle_id or "", title=title, kind="time",
            fire_at=pt.fire_at, extra={"turn": turn} if turn else {},
        ) for pt in parsed]
        created = await self.store.add_many(reminders)
        await self._refresh_active(ctx)
        await self._clear_pending(ctx)
        return AgentResult(
            speech=(f"好的，{parsed[0].display}和{parsed[1].display}"
                    f"各提醒你一次：{title}。"),
            ui_card={"type": "card_group", "items": [
                self._card_single(r, "created") for r in created
            ]},
        )

    async def _create(self, intent, ctx, meta) -> AgentResult:
        raw = intent.raw_text or ""
        # Q11 否定守卫：**用户明说「别建提醒」时不许建**。
        # 实测库里逐字躺着一条：「接爸妈去吃饭，**别建提醒**」→ 建了一条提醒，
        # 正文含「别建提醒」四个字（已 cancelled，但它进过库）。
        # ⚠ 判据**共用 Q7 的极性实现**（`runtime.polarity`），卡 §3-Q11 明写同源：
        # 「别建提醒」与「车窗别开」是同一件事的两个域，不许写第二份。
        if (not _REMINDER_DOUBLE_NEG_RE.search(raw)
                and _NO_REMINDER_RE.search(raw)):
            return AgentResult(
                speech="好的，那我不建提醒。",
                follow_up="要建的时候说一声就行。")
        title = (intent.slots.get("title") or "").strip()
        time_text = (intent.slots.get("time_text") or "").strip()
        if not title or title == raw:            # route_hints 灌整句 / planner 未抽槽
            title = self._extract_title(raw)
        if title and not _REMINDABLE_REF_RE.sub("", title).strip(" ，。,、的时候了吧呀"):
            title = ""    # P1c：纯事件指代（「开赛的时候」）不是标题 → 走 pending/跨域推导
        pend_update_id = ""
        if not title:                            # 上一轮 NEED_SLOT 只差时间（create 或 update）
            pend = await self._load_pending(ctx)
            title = (pend.get("title") or "").strip()
            if pend.get("action") == "update":
                pend_update_id = pend.get("id") or ""
        snooze_target = None
        if not title and _AGAIN_RE.search(raw):  # 「过10分钟再叫我」无标题 → 最近 fired
            fired, _ = await self.store.list_split(self._uid(ctx), statuses=(FIRED,),
                                                  occupant_id=self._occ(ctx))
            if fired:
                snooze_target = max(fired, key=lambda r: r.fired_at)
                title = snooze_target.title
        if not title:
            return AgentResult(status=NEED_SLOT, speech="要提醒你什么事？",
                               follow_up="比如：明天早上八点提醒我带充电线",
                               missing_slots=["title"])
        # C10-C 任务性准入：**这句话是不是一件待办**。写入闸此前只判 fire_at>0，
        # 于是「刚才那个提醒现在几点」（问句）和「用户计划…4天行程」（第三人称
        # 事实陈述）都建成了提醒，还进了序数参照系被后面的「取消第一条」选中。
        # 判据取形态不取关键词（判据本体见 `task_admission`），拒建要**诚实说**。
        ok, why = admit_task_title(title)
        if not ok:
            logger.info("reminder.create 拒建（%s）：%s", why, title[:40])
            await self._clear_pending(ctx)
            return AgentResult(
                speech=f"「{title}」听着不像一件要做的事，我没建提醒。",
                follow_up="要建的话说清楚做什么，比如「明天九点提醒我交周报」。")
        # 原话优先（B2-2 @M3 canonical 抓到：planner 对「提醒我吃降压药」误填 kind=todo，
        # todo 路径静默跳过时间追问）：显式「提醒/叫我」话术永远走定时提醒，槽位只在
        # 与原话不冲突时生效——同 scene custom_params 的「原话优先、槽位兜底」原则。
        explicit_remind = bool(re.search(r"提醒|叫我", raw))
        is_todo = not explicit_remind and (
            intent.slots.get("kind") == "todo" or bool(_TODO_RE.search(raw)))
        if is_todo:
            r = await self.store.add(Reminder(
                user_id=self._uid(ctx), occupant_id=self._occ(ctx),
                vehicle_id=ctx.vehicle_id or "",
                title=title, kind="todo"))
            await self._refresh_active(ctx)
            await self._clear_pending(ctx)
            return AgentResult(speech=f"记下了：{title}。办完了跟我说「完成」就行。",
                               ui_card=self._card_single(r, "created"))
        # M3 P1 位置提醒：「到公司提醒我拿文件」。放在时间解析**之前**——这类句子里
        # 没有时间表达，走完时间三层只会白白进 LLM 兜底再追问「什么时候」。
        # ETA 族（到X之前/快到）由 placeparse 自身让路，仍走下面的 _from_remindable。
        pp = parse_place_text(raw)
        if pp.ok:
            return await self._create_location(pp, title, ctx, meta)
        now = self._now_utc()
        user_time_signal = _has_time_signal(raw or time_text)
        pt = (
            parse_time_text(time_text, now=now, tz=self._tz)
            if time_text and user_time_signal
            else ParsedTime(T_FAIL)
        )
        if pt.status == T_FAIL:
            pt = parse_time_text(raw, now=now, tz=self._tz)
        if pt.status != T_OK:
            # P1c 跨域：规则解析不出时间 → 先查「可提醒上下文」（确定性数据优先于 LLM 猜；
            # 「第一场提醒我观看」→ 开赛时刻-提前量）。不命中走原三层不变（零回归）。
            rem = await self._from_remindable(ctx, raw, title)
            if isinstance(rem, AgentResult):
                return rem                       # 多项反问 / 已开始诚实告知
            if rem:
                r = await self.store.add(Reminder(
                    user_id=self._uid(ctx), occupant_id=self._occ(ctx),
                vehicle_id=ctx.vehicle_id or "",
                    title=rem["title"], kind="time", fire_at=rem["fire_at"]))
                await self._refresh_active(ctx)
                await self._clear_pending(ctx)
                return AgentResult(speech=rem["speech"],
                                   ui_card=self._card_single(r, "created"))
        if pt.status == T_FAIL and user_time_signal:
            pt = await self._llm_time_fallback(time_text or raw)
        if pt.status != T_OK:
            await self._save_pending(ctx, title, update_id=pend_update_id)
            return AgentResult(status=NEED_SLOT,
                               speech=f"好的，{title}。什么时候提醒你？",
                               follow_up="比如：明天早上八点 / 半小时后",
                               missing_slots=["time_text"])
        if pt.fire_at <= int(now.timestamp()):
            await self._save_pending(ctx, title, update_id=pend_update_id)
            return AgentResult(status=NEED_SLOT,
                               speech=f"{pt.display}已经过了，换个时间？",
                               missing_slots=["time_text"])
        # ①update 缺时间的续接轮：改原条目，不新建
        if pend_update_id:
            return await self._apply_update(ctx, pend_update_id, title, pt)
        # ②重复规则：工作日系列首触发落周末 → 顺延周一
        recur = parse_recur(raw) or parse_recur(time_text)
        fire_at, display = pt.fire_at, pt.display
        if recur == "workday":
            aligned = align_workday(fire_at, self._tz)
            if aligned != fire_at:
                fire_at, display = aligned, format_display(aligned, now=now, tz=self._tz)
        # ③snooze/尸体收编：同名 fired 一律改期原条目（「稍后10分钟」按钮即此路径）；
        #   显式「再提醒」时同名 pending 也改期不重建 —— 根治 P0 的 fired 尸体堆积。
        #   **本轮自己刚建的那条除外**（Q12，见 `_reschedule_target` 的注释）。
        turn = str((meta or {}).get("trace_id") or "")
        target = snooze_target or await self._reschedule_target(ctx, title, raw,
                                                                turn=turn)
        if target:
            await self.store.update_fire_at(self._uid(ctx), target.id, fire_at,
                                            occupant_id=self._occ(ctx))
            await self._refresh_active(ctx)
            await self._clear_pending(ctx)
            r2 = await self.store.get(self._uid(ctx), target.id,
                                      occupant_id=self._occ(ctx))
            return AgentResult(speech=f"好的，{display}再提醒你：{target.title}。",
                               ui_card=self._card_single(r2, "updated"))
        duplicate = await self._cross_turn_duplicate(ctx, title, fire_at, turn)
        if duplicate:
            # C10-E：跨轮重复 ⇒ 收编不新建。真栈实录库里躺着三条一模一样的
            # 09:00 pending——长会话里同一句被重复规划，每次都老实建了一条。
            await self._refresh_active(ctx)
            await self._clear_pending(ctx)
            return AgentResult(
                speech=f"「{title}」{display}已经有一条了，就不重复建了。",
                follow_up="要改时间说「改到几点」，不要了说「取消」。")
        r = await self.store.add(Reminder(
            user_id=self._uid(ctx), occupant_id=self._occ(ctx),
                vehicle_id=ctx.vehicle_id or "",
            title=title, kind="time", fire_at=fire_at, recur=recur,
            extra={"turn": turn} if turn else {}))
        await self._refresh_active(ctx)
        await self._clear_pending(ctx)
        speech = (f"好的，{recur_label(recur)} {display.split(' ')[-1]} 提醒你：{title}，"
                  f"首次{display}。" if recur
                  else f"好的，{display}提醒你：{title}。")
        return AgentResult(speech=speech, ui_card=self._card_single(r, "created"))

    async def _from_remindable(self, ctx, raw: str, cur_title: str):
        """跨域提醒 P1c：缺时间时从 `REMINDABLE_ACTIVE` 推导（2026-07-11-reminder-cross-domain）。

        返回 None=不命中（走原追问，零回归）；AgentResult=终局（多项反问/已开始）；
        dict{fire_at,title,speech}=命中成单。序号按 items 全序（=卡片渲染序，含已开赛占位）；
        无序号需命中指代词形，未来项唯一才直取、多项反问「第几场」。

        同轮多步计划可能并行执行 sports producer 与 reminder consumer。事件指代明确但状态
        尚未可见时，做 3.5s 有界轮询等待 producer 落 REMINDABLE_ACTIVE；普通提醒不等待。
        """
        all_items = []
        attempts = 8 if _REMINDABLE_REF_RE.search(raw) else 1
        for attempt in range(attempts):
            data = await ctx.load_shared_state(REMINDABLE_ACTIVE)
            try:
                d = json.loads(data) if isinstance(data, str) else (data or {})
            except Exception:
                d = {}
            all_items = [
                it for it in (d.get("items") or [])
                if isinstance(it, dict) and it.get("fire_at") and it.get("title")
            ]
            if all_items:
                break
            if attempt + 1 < attempts:
                await asyncio.sleep(0.5)
        if not all_items:
            return None
        now_ts = int(self._now_utc().timestamp())
        idx = None
        m = _ORDINAL_RE.search(raw)
        if m:
            v = m.group(1)
            idx = int(v) if v.isdigit() else _CN_IDX.get(v)
        if idx is None and not _REMINDABLE_REF_RE.search(raw):
            return None
        if idx is not None:
            if not (0 < idx <= len(all_items)):
                return None
            item = all_items[idx - 1]
        else:
            future = [it for it in all_items if int(it["fire_at"]) > now_ts]
            if not future:
                return None
            # G1（EVA 二轮）出发/到达双事件择项：navigation 带到达时限时会同时写
            # 「出发前往X」「到达X」两个事件。话里的「出发/到达」词形先按标题收窄，
            # 收窄后唯一即直取，不再把两个事件当歧义反问。
            if len(future) > 1:
                for kw in ("出发", "到达"):
                    if kw in raw:
                        narrowed = [it for it in future
                                    if kw in str(it.get("title") or "")]
                        if narrowed:
                            future = narrowed
                        break
            if len(future) > 1:
                heads = "、".join(
                    f"第{i}场 {it['title']}"
                    f"（{format_display(int(it['fire_at']), now=self._now_utc(), tz=self._tz)}）"
                    for i, it in enumerate(all_items, 1) if int(it["fire_at"]) > now_ts)
                return AgentResult(status=NEED_SLOT,
                                   speech=f"有几场还没开始：{heads}。提醒你看第几场？",
                                   missing_slots=["index"])
            item = future[0]
        event_ts = int(item["fire_at"])
        if event_ts <= now_ts:
            return AgentResult(speech=f"「{item['title']}」已经开始了，就不设提醒了。")
        lead = parse_lead(raw)
        fire = event_ts - lead
        lead_txt = (f"提前 {lead // 3600} 小时" if lead >= 3600 and lead % 3600 == 0
                    else f"提前 {max(lead // 60, 1)} 分钟")
        if fire <= now_ts:                 # 提前量落到过去 → 事件时刻直提
            fire, lead_txt = event_ts, "开始时"
        # 标题：干净短动词（观看/看球）+ 事件名 > 既有标题（planner/pending 拼的）> 事件名
        cleaned = _REMINDABLE_REF_RE.sub("", _ORDINAL_RE.sub("", raw)).strip(" ，。,、的时候了吧呀")
        verb = self._extract_title(cleaned).strip(" ，。,、的时候了吧呀")
        if verb and len(verb) <= 6 and not _REMINDABLE_REF_RE.search(verb):
            # 事件标题本就以该动词开头（「出发前往X」+话里的「出发」）时不再前缀，
            # 避免拼出「出发出发前往X」
            title = (str(item["title"]) if str(item["title"]).startswith(verb)
                     else f"{verb}{item['title']}")
        elif cur_title and cur_title != raw and len(cur_title) <= 24:
            title = cur_title
        else:
            title = item["title"]
        event_disp = format_display(event_ts, now=self._now_utc(), tz=self._tz)
        return {"fire_at": fire, "title": title,
                "speech": f"好的，{item['title']} {event_disp} 开始，{lead_txt}提醒你。"}

    async def _reschedule_target(self, ctx, title: str, raw: str,
                                 turn: str = "") -> Reminder | None:
        """同名 fired 尸体一律收编改期；显式「再提醒/再叫」时同名 pending 也改期不重建。

        ⚠ **本轮刚建的那条不算收编对象**（2026-08-16，Q12 取证抓到）。真栈原句
        「明天下午四点提醒我开会，**三点半再提醒我一次**」规划成两个 `reminder.create`
        步，第二步的 `raw` 是**同一句话**、照样含「再提醒」，于是它把 0.2 秒前
        由第一步建出来的那条**改期**了：用户要两条、库里只有一条，而话术照说
        「15:30 和 16:00 各提醒你一次」——**系统声称的与它真做的不一致**，
        同 Q6 那一族。

        判据用 `turn`（=本轮 `trace_id`）而不是「创建时间差几秒」：一句话里的两步
        与「上一轮建完这轮说『过10分钟再叫我』」在时间上分不开，在轮次上分得开。
        `turn` 缺失（老数据/端侧直发）时行为与此前逐字一致——不认得就不排除。
        """
        uid, occ = self._uid(ctx), self._occ(ctx)
        exact = [h for h in await self.store.find_by_title(uid, title,
                                                           occupant_id=occ)
                 if h.title == title
                 and not (turn and str(h.extra.get("turn") or "") == turn)]
        fired = [h for h in exact if h.status == FIRED]
        if fired:
            return fired[0]
        if _AGAIN_RE.search(raw) and exact:
            return exact[0]
        return None

    async def _cross_turn_duplicate(self, ctx, title: str, fire_at: int,
                                    turn: str) -> Reminder | None:
        """同 owner + **逐字同标题** + 同触发时刻的 pending 已存在 ⇒ 返回它（C10-E）。

        `turn` 判据与 `_reschedule_target` 同源、理由也同一条：一句话被规划成
        两步是**设计内**的（「同轮不收编」那条裁定保留），跨轮重复才是该挡的那个。
        用「逐字同标题 + 同时刻」而不是模糊匹配：**收编的代价是少建一条**，
        判宽了就会把「明天九点开会」和「明天九点开例会」当成同一件事。
        """
        for hit in await self.store.find_by_title(
                self._uid(ctx), title, occupant_id=self._occ(ctx)):
            if (hit.title == title and hit.fire_at == fire_at
                    and hit.status == PENDING
                    and not (turn and str(hit.extra.get("turn") or "") == turn)):
                return hit
        return None

    async def _apply_update(self, ctx, rid: str, title: str, pt: ParsedTime) -> AgentResult:
        """改期落地（update 直达轮与缺时间续接轮共用）。"""
        ok = await self.store.update_fire_at(self._uid(ctx), rid, pt.fire_at,
                                             occupant_id=self._occ(ctx))
        await self._refresh_active(ctx)
        await self._clear_pending(ctx)
        if not ok:
            # R9：诚实降级话术用 OK（FAILED 会被聚合器吞）
            return AgentResult(
                speech="这条提醒不在了，说「看看我的提醒」我给你列一下。")
        r2 = await self.store.get(self._uid(ctx), rid, occupant_id=self._occ(ctx))
        return AgentResult(speech=f"好的，「{title}」改到{pt.display}。",
                           ui_card=self._card_single(r2, "updated"))

    # ── 位置提醒（M3 P1）──
    async def _create_location(self, pp, fallback_title: str, ctx, meta) -> AgentResult:
        """建一条位置提醒。**地点解析不出就诚实追问，绝不存一条永远不会触发的提醒。**"""
        title = (pp.title or fallback_title or "").strip()
        if not title:
            return AgentResult(status=NEED_SLOT, speech=f"到{pp.place}提醒你什么事？",
                               missing_slots=["title"])
        resolved = await self._resolve_place(pp.place, ctx)
        verb = "离开" if pp.trigger_on != ARRIVE else "到"
        if not resolved:
            return AgentResult(
                status=NEED_SLOT,
                speech=f"我还不知道{pp.place}在哪，说个地址我就记住了。",
                follow_up=f"比如「我{pp.place}在XX路X号」，以后{verb}{pp.place}我就能提醒你。",
                missing_slots=["place_address"])
        r = await self.store.add(Reminder(
            user_id=self._uid(ctx), occupant_id=self._occ(ctx),
                vehicle_id=ctx.vehicle_id or "",
            title=title, kind=LOCATION,
            extra={"place": pp.place, "trigger_on": pp.trigger_on, **resolved}))
        await self._refresh_active(ctx)
        await self._clear_pending(ctx)
        return AgentResult(speech=f"好的，{verb}{pp.place}我就提醒你：{title}。",
                           ui_card=self._card_single(r, "created"))

    async def _resolve_place(self, place: str, ctx) -> dict | None:
        """地点 → 围栏数据。四级，全落空返回 None（调用方诚实追问）。

        ① 画像常用地点（家/公司/学校，带 lat/lng）；② 关系边一跳（「孩子学校」→ 校名）；
        ③ 经 nearby.search 拿坐标（**卡片可跨 Agent 传、data 不能**，所以读 ui_card）；
        ④ 落空 → None。
        """
        alias = {"家": "home", "我家": "home", "家里": "home",
                 "公司": "company", "单位": "company",
                 "学校": "school"}.get(place)
        if alias:
            try:
                vals = await ctx.fetch("profile.places")
                raw = vals.get("profile.places")
                places = json.loads(raw) if isinstance(raw, str) else (raw or {})
                hit = (places or {}).get(alias)
                if isinstance(hit, dict) and hit.get("lat") is not None:
                    return {"lat": hit.get("lat"), "lon": hit.get("lng"),
                            "radius_m": _GEOFENCE_RADIUS_M}
            except Exception as e:
                logger.debug("画像常用地点读取失败：%s", e)
        keyword = place
        try:
            hit = await ctx.resolve_person_place(place)
            if hit and hit.get("place"):
                keyword = hit["place"]        # 「孩子学校」→ 阳光小学，再去要坐标
        except Exception as e:
            logger.debug("关系边解析跳过：%s", e)
        try:
            res = await self.agents.call("nearby", "nearby.search",
                                         {"keyword": keyword}, ctx)
            items = ((res.ui_card or {}).get("items") or []) if res else []
            for it in items:
                if it.get("lat") is not None and it.get("lng") is not None:
                    return {"lat": it["lat"], "lon": it["lng"],
                            "radius_m": _GEOFENCE_RADIUS_M,
                            "resolved_name": it.get("name")}
        except Exception as e:
            logger.debug("nearby 地点解析失败：%s", e)
        return None

    # ── update（P1a：改时间；缺时间经 REMINDER_PENDING(action=update) 两轮续接）──
    async def _update(self, intent, ctx, meta) -> AgentResult:
        raw = intent.raw_text or ""
        hits = await self._resolve_targets(ctx, raw, intent.slots)
        if not hits:
            # R9：诚实降级话术用 OK（FAILED 会被聚合器吞）
            return AgentResult(
                speech="没找到要改的提醒，说「看看我的提醒」我给你列一下。")
        if len(hits) > 1:
            return await self._clarify_multi(ctx, hits, "改")
        r = hits[0]
        now = self._now_utc()
        tt = (intent.slots.get("time_text") or "").strip()
        user_time_signal = _has_time_signal(raw or tt)
        pt = (
            parse_time_text(tt, now=now, tz=self._tz)
            if tt and user_time_signal
            else ParsedTime(T_FAIL)
        )
        if pt.status == T_FAIL:
            pt = parse_time_text(raw, now=now, tz=self._tz)
        if pt.status == T_FAIL and user_time_signal:
            pt = await self._llm_time_fallback(tt or raw)
        if pt.status != T_OK or pt.fire_at <= int(now.timestamp()):
            await self._save_pending(ctx, r.title, update_id=r.id)
            speech = (f"{pt.display}已经过了，换个时间？" if pt.status == T_OK
                      else f"「{r.title}」改到什么时候？")
            return AgentResult(status=NEED_SLOT, speech=speech,
                               follow_up="比如：明天早上八点 / 晚上七点半",
                               missing_slots=["time_text"])
        return await self._apply_update(ctx, r.id, r.title, pt)

    @staticmethod
    def _extract_title(raw: str) -> str:
        t = strip_time_expressions(raw or "")
        t = _CMD_STRIP_RE.sub("", t).strip()
        t = re.sub(r"^(我?要|去|该)", "", t)
        return t.strip(" ，。,、！!？?的哦啊呀吧")

    async def _llm_time_fallback(self, text: str) -> ParsedTime:
        """规则未命中（"下下周三饭点"）→ LLM @fast 抽 ISO；失败 FAIL（外层追问）。"""
        ln = self._now_utc().astimezone(self._tz)
        prompt = (f"现在是 {ln.strftime('%Y-%m-%d %H:%M')}"
                  f"（周{'一二三四五六日'[ln.weekday()]}，UTC+8）。\n"
                  f"用户说：「{text}」\n"
                  '解析其中的提醒时间，只输出 JSON：{"iso": "YYYY-MM-DDTHH:MM"}；'
                  '解析不出输出 {"iso": null}')
        try:
            out = await self.llm.complete(
                [{"role": "system", "content": "你是时间解析器，只输出 JSON。"},
                 {"role": "user", "content": prompt}],
                model=os.getenv("LLM_MODEL_FAST", ""), temperature=0.0,
                max_tokens=60, thinking=False)
            m = re.search(r"\{.*\}", out, re.S)
            iso = json.loads(m.group(0)).get("iso") if m else None
            if not iso:
                return ParsedTime(T_FAIL)
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:        # N2：LLM 偶带 Z/偏移时不误标业务时区
                dt = dt.replace(tzinfo=self._tz)
            fire = int(dt.astimezone(timezone.utc).timestamp())
            return ParsedTime(T_OK, fire,
                              format_display(fire, now=self._now_utc(), tz=self._tz))
        except Exception as e:
            logger.debug("reminder: llm time fallback failed: %s", e)
            return ParsedTime(T_FAIL)

    # ── list（D7：scope 词表 + view 双形态）──
    async def _list(self, intent, ctx, meta) -> AgentResult:
        text = " ".join(filter(None, [intent.slots.get("scope", ""),
                                      intent.slots.get("date_text", ""),
                                      intent.raw_text or ""]))
        now_utc = self._now_utc()
        ln = now_utc.astimezone(self._tz)
        day0 = ln.replace(hour=0, minute=0, second=0, microsecond=0)

        def ep(dt):
            return int(dt.astimezone(timezone.utc).timestamp())

        view, label, frm, to, todo_only = "multi", "全部", 0, 0, False
        if "待办" in text and not re.search(r"提醒|日程|安排", text):
            todo_only, label = True, "待办"
        elif re.search(r"今天|今日", text):
            view, label = "day", f"今天 · {ln.month}月{ln.day}日"
            frm, to = ep(day0), ep(day0 + timedelta(days=1))
        elif "明天" in text:
            d = day0 + timedelta(days=1)
            view, label = "day", f"明天 · {d.month}月{d.day}日"
            frm, to = ep(d), ep(d + timedelta(days=1))
        elif "大后天" in text:            # B1：长词在前，否则被"后天"分支截胡错一天
            d = day0 + timedelta(days=3)
            view, label = "day", f"大后天 · {d.month}月{d.day}日"
            frm, to = ep(d), ep(d + timedelta(days=1))
        elif "后天" in text:
            d = day0 + timedelta(days=2)
            view, label = "day", f"后天 · {d.month}月{d.day}日"
            frm, to = ep(d), ep(d + timedelta(days=1))
        elif re.search(r"未来.{0,2}天|最近几天|这几天", text):
            label, frm, to = "未来三天", ep(now_utc), ep(day0 + timedelta(days=3))
        elif re.search(r"这周|本周", text):
            label, frm, to = "这周", ep(now_utc), ep(day0 + timedelta(days=7 - ln.weekday()))
        elif re.search(r"下个?月", text):  # P1a：月区间（低密度数据，multi 分组列表足够）
            first_next = (day0.replace(day=1) + timedelta(days=32)).replace(day=1)
            first_after = (first_next + timedelta(days=32)).replace(day=1)
            label, frm, to = f"下个月 · {first_next.month}月", ep(first_next), ep(first_after)
        elif re.search(r"这个?月|本月", text):
            nxt = (day0.replace(day=1) + timedelta(days=32)).replace(day=1)
            label, frm, to = f"这个月 · {day0.month}月", ep(now_utc), ep(nxt)
        # 词表外区间：诚实回退"全部"（frm=0 含过期未办项）

        # Q5/I-045：**默认范围收窄到「从现在起」**。此前不带时间词一律 frm=0，
        # 真栈实测答出「全部共 20 条」，头三条是 7 月的过期项——用户问的是
        # 「我现在有哪些进行中的任务」，得到的是一份考古清单。
        # ⚠ **收窄不等于隐藏**：过期的另计并显式报数，一条都不许悄悄消失。
        # ⚠ 用户明说「全部/所有」时不收窄——那是他要的。
        default_future = frm == 0 and not re.search(
            r"全部|所有|历史|以前|过去", text)
        # 无日期待办没有可用于落入“今天/明天/未来三天”的时间事实；日期范围查询
        # 只展示定时提醒。默认总览、显式全部和“待办”视图仍包含待办。
        include_todos = not bool(to) or todo_only
        expired_count = 0
        if default_future:
            cutoff = ep(now_utc)
            # 先在存储层按 cutoff 筛未来项，再 LIMIT；否则 50 条最早过期项会把
            # 后面的有效未来项全部挤掉。过期数走 COUNT，不受展示上限影响。
            if todo_only:
                label = "待办"
            else:
                label = "接下来"
                expired_count = await self.store.count_time(
                    self._uid(ctx), to_ts=cutoff, occupant_id=self._occ(ctx))
            times, todos = await self.store.list_split(
                self._uid(ctx), from_ts=cutoff, to_ts=to,
                occupant_id=self._occ(ctx))
            time_count = (0 if todo_only else await self.store.count_time(
                self._uid(ctx), from_ts=cutoff, to_ts=to,
                occupant_id=self._occ(ctx)))
        else:
            times, todos = await self.store.list_split(
                self._uid(ctx), from_ts=frm, to_ts=to,
                occupant_id=self._occ(ctx))
            time_count = (0 if todo_only else await self.store.count_time(
                self._uid(ctx), from_ts=frm, to_ts=to,
                occupant_id=self._occ(ctx)))
        todo_count = (await self.store.count_todo(
            self._uid(ctx), occupant_id=self._occ(ctx)) if include_todos else 0)
        if not include_todos:
            todos = []
        if todo_only:
            times = []
        total = time_count + todo_count
        if total == 0:
            tail = (f"另有 {expired_count} 条已过期没处理的，要看的话说「看全部」。"
                    if expired_count else "")
            return AgentResult(speech=f"{label}没有提醒或待办。{tail}想加一条直接说"
                                      f"「明天早上八点提醒我…」。")
        await self._refresh_active(ctx, times + todos)
        head = "、".join(
            f"{r.title}（{format_display(r.fire_at, now=now_utc, tz=self._tz)}）"
            if r.fire_at else r.title for r in (times + todos)[:3])
        speech = f"{label}共 {total} 条：{head}" + ("等。" if total > 3 else "。")
        if expired_count:
            speech += f"另有 {expired_count} 条已过期没处理的，说「看全部」可以查。"
        card = {"type": "reminder_list", "view": view, "date_label": label,
                "items": [r.to_card_item(now=now_utc, tz=self._tz) for r in times],
                "todos": [r.to_card_item(now=now_utc, tz=self._tz) for r in todos]}
        return AgentResult(speech=speech, ui_card=card)

    # ── complete / cancel ──
    async def _complete(self, intent, ctx, meta) -> AgentResult:
        hits = await self._resolve_targets(ctx, intent.raw_text or "", intent.slots)
        if not hits:
            # R9：诚实降级话术用 OK（FAILED 会被聚合器吞）
            return AgentResult(
                speech="没找到这条提醒，说「看看我的提醒」我给你列一下。")
        if len(hits) > 1:
            return await self._clarify_multi(ctx, hits, "完成")
        r = hits[0]
        if r.recur:
            # 重复系列：「完成」只确认本次，不杀系列（列表恒显示下一次）；结束系列用「取消」
            nxt = r.to_card_item(now=self._now_utc(), tz=self._tz).get("time_display", "")
            return AgentResult(speech=f"好，这次完成了。「{r.title}」{recur_label(r.recur)}"
                                      f"还会提醒，下次{nxt}；不需要了说「取消{r.title}」。")
        await self.store.set_status(self._uid(ctx), r.id, DONE,
                                    occupant_id=self._occ(ctx))
        await self._refresh_active(ctx)
        return AgentResult(speech=f"「{r.title}」已完成。")

    async def _cancel(self, intent, ctx, meta) -> AgentResult:
        raw = intent.raw_text or ""
        pending = await self._load_pending(ctx)
        confirmed = (meta or {}).get("confirmed") == "true"
        wants_all = (intent.slots.get("all") or "").lower() in ("true", "1", "全部") \
            or bool(_ALL_RE.search(raw)) \
            or bool(confirmed and pending.get("action") == "cancel_all")
        if wants_all:
            times, todos = await self.store.list_split(self._uid(ctx),
                                                   occupant_id=self._occ(ctx))
            n = len(times) + len(todos)
            if n == 0:
                await self._clear_pending(ctx)
                return AgentResult(speech="现在没有提醒或待办。")
            if confirmed:   # engine 确认续接（R2 契约）
                await self.store.cancel_all(self._uid(ctx), occupant_id=self._occ(ctx))
                await self._refresh_active(ctx, [])
                await self._clear_pending(ctx)
                return AgentResult(speech=f"好的，已清空全部 {n} 条提醒和待办。")
            await ctx.save_shared_state(self._state_key(ctx, REMINDER_PENDING), {
                "action": "cancel_all",
            })
            return AgentResult(status=NEED_CONFIRM,
                               speech=f"确定要清空全部 {n} 条提醒和待办吗？清掉就找不回来了。")
        hits = await self._resolve_targets(ctx, raw, intent.slots)
        if not hits:
            # R9：诚实降级话术用 OK（FAILED 会被聚合器吞）
            return AgentResult(
                speech="没找到这条提醒，说「看看我的提醒」我给你列一下。")
        if len(hits) > 1:
            return await self._clarify_multi(ctx, hits, "取消")
        r = hits[0]
        await self.store.set_status(self._uid(ctx), r.id, CANCELLED,
                                    occupant_id=self._occ(ctx))
        await self._refresh_active(ctx)
        return AgentResult(speech=f"好的，取消了「{r.title}」。")

    async def _resolve_targets(self, ctx, raw: str, slots: dict) -> list[Reminder]:
        """序号经 REMINDERS_ACTIVE（须本会话列过/建过）→ 唯一命中；
        标题走 store 子串匹配 → 可能多条，全部返回由调用方决定（单条直接执行、多条反问澄清）。"""
        uid, occ = self._uid(ctx), self._occ(ctx)
        idx = None
        idx_slot = (slots.get("index") or "").strip()
        if idx_slot.isdigit():
            idx = int(idx_slot)
        if idx is None:
            m = _ORDINAL_RE.search(idx_slot + " " + raw)
            if m:
                v = m.group(1)
                idx = int(v) if v.isdigit() else _CN_IDX.get(v)
        if idx:
            data = await ctx.load_shared_state(self._state_key(ctx, REMINDERS_ACTIVE))
            try:
                d = json.loads(data) if isinstance(data, str) else (data or {})
                items = d.get("items", [])
            except Exception:
                items = []
            if 0 < idx <= len(items):
                r = await self.store.get(uid, items[idx - 1]["id"], occupant_id=occ)
                return [r] if r else []
            return []
        q = (slots.get("title") or "").strip()
        if not q or q == raw:
            q = self._extract_title(re.sub(
                r"完成提醒[:：]|完成|办完|做完|搞定|取消|删掉|删除|不用|那条|这条"
                r"|把|改到|改成|推迟到?|提前到?|延到|换到|改个?时间|的提醒|的待办|了",
                "", raw))
        if not q:
            return []
        hits = await self.store.find_by_title(uid, q, occupant_id=occ)
        if not hits:
            # 域词漏进标题槽 ⇒ 库里永远查不到（2026-08-29 真栈实录，余项 ③ 症状②）。
            # `find_by_title` 是 `title LIKE %q%`：**q 比库里那条标题长就一定不匹配**。
            # 而「取消X**的提醒**」恰恰是最可靠的那种说法——同日受控对照实测，带域词的
            # 取消句落域 18/18、不带的只有 3/12。于是「说得更清楚」反而查不到：
            # planner 把整串（含「的提醒」）塞进 title 槽时，上面那条 `q == raw` 的
            # 兜底削尾**不会触发**（槽有值且不等于原话）。
            # 逐字实录：`取消参加代号17879686214的评审会的提醒` → `reminder.cancel`
            # →「没找到这条提醒」，紧接着同一 owner 的列表里它**还在**。
            # **只在查空之后再削一次**：这一步只能把「没找到」变成「找到」，
            # 不会改变任何一次已经命中的匹配。
            trimmed = _TITLE_DOMAIN_TAIL_RE.sub("", q).strip()
            if trimmed and trimmed != q:
                hits = await self.store.find_by_title(uid, trimmed,
                                                      occupant_id=occ)
                if hits:
                    q = trimmed      # 让下面的「逐字相等优先」比的是削过的那份
        # C10-B **精确度阶梯**：逐字相等优先于子串命中。planner 转述会把用户
        # 点名的标题放宽（「取消参加评审会」→ title=「评审会」），子串于是同时
        # 命中「评审会」与「准备评审会材料」；旧实现把两条都当候选，单条时
        # 直接执行——**取消掉的可能不是他点名的那条**。有逐字相等的就只认它，
        # 没有才退回子串（多条仍走澄清，那条既有行为不变）。
        exact = [r for r in hits if r.title == q]
        return exact or hits

    async def _clarify_multi(self, ctx, hits: list[Reminder], action: str) -> AgentResult:
        """标题命中多条时不擅自操作（P0 单条语义）：反问澄清，并把候选写入 active，
        用户可续接「第 N 条」精确选中。避免旧实现 hits[0] 静默少删。"""
        now_utc = self._now_utc()
        await self._refresh_active(ctx, hits)
        lines = "、".join(
            f"第{i}条 {r.title}"
            f"（{format_display(r.fire_at, now=now_utc, tz=self._tz)}）" if r.fire_at
            else f"第{i}条 {r.title}"
            for i, r in enumerate(hits[:5], 1))
        card = {"type": "reminder_list", "view": "multi", "date_label": f"待{action}",
                "items": [r.to_card_item(now=now_utc, tz=self._tz) for r in hits if r.fire_at],
                "todos": [r.to_card_item(now=now_utc, tz=self._tz) for r in hits if not r.fire_at]}
        return AgentResult(status=NEED_SLOT,
                           speech=f"有 {len(hits)} 条都能对上：{lines}。要{action}哪条？"
                                  f"说「{action}第几条」或换个更具体的说法。",
                           missing_slots=["index"],
                           ui_card=card)

    # ── shared_state（conventions §9）──
    async def _refresh_active(self, ctx, items: list | None = None) -> None:
        """序数参照系（`第N条`）的唯一写入口。

        ⚠ **无参形式必须与 `_list` 默认视图同一口径**（C10-A，2026-08-28）：
        「从现在起」的未来项。此前它取 `list_split()` 的**整表最老 10 条**——
        库里沉了 80 条过期垃圾时，取消成功后这里一刷新，参照系就被**静默换成**
        一份用户从来没看见过的考古清单；下一句「取消第一条」于是取消了
        「刚才那个提醒现在几点」（真栈 T59 逐字实录）。

        判据一句话：**「第N条」只许指向用户最后一眼看到的那份列表**
        （候选集下发面 §9.28 的同一条）。任何后台刷新参照系的动作
        都是在给序数指代埋雷——所以刷新也得刷成用户看得见的那份。
        """
        if items is None:
            cutoff = int(self._now_utc().timestamp())
            times, todos = await self.store.list_split(
                self._uid(ctx), from_ts=cutoff,
                occupant_id=self._occ(ctx))
            items = times + todos
        await ctx.save_shared_state(self._state_key(ctx, REMINDERS_ACTIVE), {
            "items": [{"id": r.id, "title": r.title} for r in items[:10]]})

    async def _save_pending(self, ctx, title: str, update_id: str = "") -> None:
        """追问上下文：update_id 非空表示这是「改期缺时间」的续接（P1a）。"""
        pend = {"title": title}
        if update_id:
            pend.update(action="update", id=update_id)
        await ctx.save_shared_state(self._state_key(ctx, REMINDER_PENDING), pend)

    async def _clear_pending(self, ctx) -> None:
        await ctx.save_shared_state(self._state_key(ctx, REMINDER_PENDING), {})

    async def _load_pending(self, ctx) -> dict:
        data = await ctx.load_shared_state(self._state_key(ctx, REMINDER_PENDING))
        try:
            d = json.loads(data) if isinstance(data, str) else (data or {})
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _card_single(self, r: Reminder, context: str) -> dict:
        return {"type": "reminder_card", "context": context,
                "item": r.to_card_item(now=self._now_utc(), tz=self._tz)}
