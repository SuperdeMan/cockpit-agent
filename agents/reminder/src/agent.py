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
from agents._sdk.shared_state import REMINDABLE_ACTIVE, REMINDERS_ACTIVE, REMINDER_PENDING

from .store import Reminder, ReminderStore, DONE, CANCELLED, FIRED
from .timeparse import (OK as T_OK, FAIL as T_FAIL, ParsedTime, align_workday,
                        business_tz, format_display, parse_lead, parse_recur,
                        parse_time_text, recur_label, strip_time_expressions)

logger = logging.getLogger("agent.reminder")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")
_PROACTIVE_SUBJECT = "agent.proactive"

_TODO_RE = re.compile(r"记一下|记个|待办|备忘")
_CMD_STRIP_RE = re.compile(
    r"^(麻烦|请|帮我|给我)?(再)?(提醒我|叫我|别忘了|记得|记一下|记个待办|记个|设个提醒|建个提醒|待办[:：]?)+")
_ORDINAL_RE = re.compile(r"第([一二三四五六七八九十0-9]+)\s*[条个项场]?")   # 场：跨域「第N场」
_ALL_RE = re.compile(r"全部|所有|都|清空|全删")
_AGAIN_RE = re.compile(r"再(提醒|叫)")   # P1a：显式 snooze 标记（「过10分钟再提醒我」）
# P1c 跨域：无序号时的事件指代词形（含"赛/场/开始"语素，刻意收窄防泛指误命中）
# R7 扩词形：navigation 产 ETA 事件后，「到之前/快到（的时候）/到达前/到X之前」也是对
# REMINDABLE 事件的指代（「到之前一刻钟提醒我给张姐打电话」「到公司之前提醒我交周报」——
# 后者中间隔了地点词，字面「到之前」匹配不上，旅程 B5-1 抓到）。
_REMINDABLE_REF_RE = re.compile(
    r"这场|那场|到时候|开赛|开场|比赛开始|开始前|开始的?时候|"
    r"到[^，。,]{0,6}之?前|到达前|抵达前|快到(的时候|时)?")
_CN_IDX = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


class ReminderAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.store = ReminderStore()
        self._nc = None
        self._tz = business_tz()
        self._sched_task = None

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

    async def _publish_proactive(self, payload: dict) -> None:
        if not self._nc:
            logger.info("reminder fired（NATS 禁用未推送）: %s",
                        payload.get("speech", "")[:40])
            return
        await self._nc.publish(_PROACTIVE_SUBJECT,
                               json.dumps(payload, ensure_ascii=False).encode())

    # ── 请求-响应 ──
    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {"reminder.create": self._create, "reminder.list": self._list,
                    "reminder.complete": self._complete, "reminder.cancel": self._cancel,
                    "reminder.update": self._update}
        h = handlers.get(intent.name)
        if not h:
            return AgentResult(status=FAILED, speech="提醒助手暂不支持该请求。")
        return await h(intent, ctx, meta)

    # 测试注入点：所有"现在"经此取
    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _uid(ctx) -> str:
        return ctx.user_id or "u1"

    # ── create（含 P1a：update 续接 / snooze 收编 / 重复规则）──
    async def _create(self, intent, ctx, meta) -> AgentResult:
        raw = intent.raw_text or ""
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
            fired, _ = await self.store.list_split(self._uid(ctx), statuses=(FIRED,))
            if fired:
                snooze_target = max(fired, key=lambda r: r.fired_at)
                title = snooze_target.title
        if not title:
            return AgentResult(status=NEED_SLOT, speech="要提醒你什么事？",
                               follow_up="比如：明天早上八点提醒我带充电线",
                               missing_slots=["title"])
        is_todo = intent.slots.get("kind") == "todo" or bool(
            _TODO_RE.search(raw) and not re.search(r"提醒|叫我", raw))
        if is_todo:
            r = await self.store.add(Reminder(
                user_id=self._uid(ctx), vehicle_id=ctx.vehicle_id or "",
                title=title, kind="todo"))
            await self._refresh_active(ctx)
            await self._clear_pending(ctx)
            return AgentResult(speech=f"记下了：{title}。办完了跟我说「完成」就行。",
                               ui_card=self._card_single(r, "created"))
        now = self._now_utc()
        pt = parse_time_text(time_text, now=now, tz=self._tz) if time_text \
            else ParsedTime(T_FAIL)
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
                    user_id=self._uid(ctx), vehicle_id=ctx.vehicle_id or "",
                    title=rem["title"], kind="time", fire_at=rem["fire_at"]))
                await self._refresh_active(ctx)
                await self._clear_pending(ctx)
                return AgentResult(speech=rem["speech"],
                                   ui_card=self._card_single(r, "created"))
        if pt.status == T_FAIL:
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
        #   显式「再提醒」时同名 pending 也改期不重建 —— 根治 P0 的 fired 尸体堆积
        target = snooze_target or await self._reschedule_target(ctx, title, raw)
        if target:
            await self.store.update_fire_at(self._uid(ctx), target.id, fire_at)
            await self._refresh_active(ctx)
            await self._clear_pending(ctx)
            r2 = await self.store.get(self._uid(ctx), target.id)
            return AgentResult(speech=f"好的，{display}再提醒你：{target.title}。",
                               ui_card=self._card_single(r2, "updated"))
        r = await self.store.add(Reminder(
            user_id=self._uid(ctx), vehicle_id=ctx.vehicle_id or "",
            title=title, kind="time", fire_at=fire_at, recur=recur))
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
        """
        data = await ctx.load_shared_state(REMINDABLE_ACTIVE)
        try:
            d = json.loads(data) if isinstance(data, str) else (data or {})
        except Exception:
            return None
        all_items = [it for it in (d.get("items") or [])
                     if isinstance(it, dict) and it.get("fire_at") and it.get("title")]
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
            title = f"{verb}{item['title']}"
        elif cur_title and cur_title != raw and len(cur_title) <= 24:
            title = cur_title
        else:
            title = item["title"]
        event_disp = format_display(event_ts, now=self._now_utc(), tz=self._tz)
        return {"fire_at": fire, "title": title,
                "speech": f"好的，{item['title']} {event_disp} 开始，{lead_txt}提醒你。"}

    async def _reschedule_target(self, ctx, title: str, raw: str) -> Reminder | None:
        """同名 fired 尸体一律收编改期；显式「再提醒/再叫」时同名 pending 也改期不重建。"""
        uid = self._uid(ctx)
        exact = [h for h in await self.store.find_by_title(uid, title)
                 if h.title == title]
        fired = [h for h in exact if h.status == FIRED]
        if fired:
            return fired[0]
        if _AGAIN_RE.search(raw) and exact:
            return exact[0]
        return None

    async def _apply_update(self, ctx, rid: str, title: str, pt: ParsedTime) -> AgentResult:
        """改期落地（update 直达轮与缺时间续接轮共用）。"""
        ok = await self.store.update_fire_at(self._uid(ctx), rid, pt.fire_at)
        await self._refresh_active(ctx)
        await self._clear_pending(ctx)
        if not ok:
            return AgentResult(status=FAILED,
                               speech="这条提醒不在了，说「看看我的提醒」我给你列一下。")
        r2 = await self.store.get(self._uid(ctx), rid)
        return AgentResult(speech=f"好的，「{title}」改到{pt.display}。",
                           ui_card=self._card_single(r2, "updated"))

    # ── update（P1a：改时间；缺时间经 REMINDER_PENDING(action=update) 两轮续接）──
    async def _update(self, intent, ctx, meta) -> AgentResult:
        raw = intent.raw_text or ""
        hits = await self._resolve_targets(ctx, raw, intent.slots)
        if not hits:
            return AgentResult(status=FAILED,
                               speech="没找到要改的提醒，说「看看我的提醒」我给你列一下。")
        if len(hits) > 1:
            return await self._clarify_multi(ctx, hits, "改")
        r = hits[0]
        now = self._now_utc()
        tt = (intent.slots.get("time_text") or "").strip()
        pt = parse_time_text(tt, now=now, tz=self._tz) if tt else ParsedTime(T_FAIL)
        if pt.status == T_FAIL:
            pt = parse_time_text(raw, now=now, tz=self._tz)
        if pt.status == T_FAIL:
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

        times, todos = await self.store.list_split(self._uid(ctx), from_ts=frm, to_ts=to)
        if todo_only:
            times = []
        total = len(times) + len(todos)
        if total == 0:
            return AgentResult(speech=f"{label}没有提醒或待办。想加一条直接说"
                                      f"「明天早上八点提醒我…」。")
        await self._refresh_active(ctx, times + todos)
        head = "、".join(
            f"{r.title}（{format_display(r.fire_at, now=now_utc, tz=self._tz)}）"
            if r.fire_at else r.title for r in (times + todos)[:3])
        speech = f"{label}共 {total} 条：{head}" + ("等。" if total > 3 else "。")
        card = {"type": "reminder_list", "view": view, "date_label": label,
                "items": [r.to_card_item(now=now_utc, tz=self._tz) for r in times],
                "todos": [r.to_card_item(now=now_utc, tz=self._tz) for r in todos]}
        return AgentResult(speech=speech, ui_card=card)

    # ── complete / cancel ──
    async def _complete(self, intent, ctx, meta) -> AgentResult:
        hits = await self._resolve_targets(ctx, intent.raw_text or "", intent.slots)
        if not hits:
            return AgentResult(status=FAILED,
                               speech="没找到这条提醒，说「看看我的提醒」我给你列一下。")
        if len(hits) > 1:
            return await self._clarify_multi(ctx, hits, "完成")
        r = hits[0]
        if r.recur:
            # 重复系列：「完成」只确认本次，不杀系列（列表恒显示下一次）；结束系列用「取消」
            nxt = r.to_card_item(now=self._now_utc(), tz=self._tz).get("time_display", "")
            return AgentResult(speech=f"好，这次完成了。「{r.title}」{recur_label(r.recur)}"
                                      f"还会提醒，下次{nxt}；不需要了说「取消{r.title}」。")
        await self.store.set_status(self._uid(ctx), r.id, DONE)
        await self._refresh_active(ctx)
        return AgentResult(speech=f"「{r.title}」已完成。")

    async def _cancel(self, intent, ctx, meta) -> AgentResult:
        raw = intent.raw_text or ""
        wants_all = (intent.slots.get("all") or "").lower() in ("true", "1", "全部") \
            or bool(_ALL_RE.search(raw))
        if wants_all:
            times, todos = await self.store.list_split(self._uid(ctx))
            n = len(times) + len(todos)
            if n == 0:
                return AgentResult(speech="现在没有提醒或待办。")
            if (meta or {}).get("confirmed") == "true":   # engine 确认续接（R2 契约）
                await self.store.cancel_all(self._uid(ctx))
                await self._refresh_active(ctx, [])
                return AgentResult(speech=f"好的，已清空全部 {n} 条提醒和待办。")
            return AgentResult(status=NEED_CONFIRM,
                               speech=f"确定要清空全部 {n} 条提醒和待办吗？清掉就找不回来了。")
        hits = await self._resolve_targets(ctx, raw, intent.slots)
        if not hits:
            return AgentResult(status=FAILED,
                               speech="没找到这条提醒，说「看看我的提醒」我给你列一下。")
        if len(hits) > 1:
            return await self._clarify_multi(ctx, hits, "取消")
        r = hits[0]
        await self.store.set_status(self._uid(ctx), r.id, CANCELLED)
        await self._refresh_active(ctx)
        return AgentResult(speech=f"好的，取消了「{r.title}」。")

    async def _resolve_targets(self, ctx, raw: str, slots: dict) -> list[Reminder]:
        """序号经 REMINDERS_ACTIVE（须本会话列过/建过）→ 唯一命中；
        标题走 store 子串匹配 → 可能多条，全部返回由调用方决定（单条直接执行、多条反问澄清）。"""
        uid = self._uid(ctx)
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
            data = await ctx.load_shared_state(REMINDERS_ACTIVE)
            try:
                d = json.loads(data) if isinstance(data, str) else (data or {})
                items = d.get("items", [])
            except Exception:
                items = []
            if 0 < idx <= len(items):
                r = await self.store.get(uid, items[idx - 1]["id"])
                return [r] if r else []
            return []
        q = (slots.get("title") or "").strip()
        if not q or q == raw:
            q = self._extract_title(re.sub(
                r"完成提醒[:：]|完成|办完|做完|搞定|取消|删掉|删除|不用|那条|这条"
                r"|把|改到|改成|推迟到?|提前到?|延到|换到|改个?时间|的提醒|的待办|了",
                "", raw))
        return await self.store.find_by_title(uid, q) if q else []

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
                           ui_card=card)

    # ── shared_state（conventions §9）──
    async def _refresh_active(self, ctx, items: list | None = None) -> None:
        if items is None:
            times, todos = await self.store.list_split(self._uid(ctx))
            items = times + todos
        await ctx.save_shared_state(REMINDERS_ACTIVE, {
            "items": [{"id": r.id, "title": r.title} for r in items[:10]]})

    async def _save_pending(self, ctx, title: str, update_id: str = "") -> None:
        """追问上下文：update_id 非空表示这是「改期缺时间」的续接（P1a）。"""
        pend = {"title": title}
        if update_id:
            pend.update(action="update", id=update_id)
        await ctx.save_shared_state(REMINDER_PENDING, pend)

    async def _clear_pending(self, ctx) -> None:
        await ctx.save_shared_state(REMINDER_PENDING, {})

    async def _load_pending(self, ctx) -> dict:
        data = await ctx.load_shared_state(REMINDER_PENDING)
        try:
            d = json.loads(data) if isinstance(data, str) else (data or {})
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _card_single(self, r: Reminder, context: str) -> dict:
        return {"type": "reminder_card", "context": context,
                "item": r.to_card_item(now=self._now_utc(), tz=self._tz)}
