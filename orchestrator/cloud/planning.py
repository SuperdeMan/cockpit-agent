"""PlanBuilder：LLM 生成 DAG 计划 + 解析 + 校验 + 重试 + 降级。

WS3 §4。LLM 把已注册 Agent 能力当工具，输出 JSON DAG 计划。
"""
from __future__ import annotations
import json
import logging
import re
from security.scopes import is_scope_covered
from .models import Plan, Step, PlanContext, ReplanDecision
from .context import WorkingSet

logger = logging.getLogger("planner.planning")

# ── 多日出行确定性兜底（弱 LLM 常漏行程规划，命中下列模式必补 trip.plan 步）──
# 目的地：取『去/到/赴X』中的 X（懒匹配到 玩/住/游/标点/N天 前），通勤/固定点不算出行
_TRIP_DEST_RE = re.compile(
    r"(?:去|到|赴|游)\s*([一-鿿]{2,6}?)"
    r"(?=玩|住|待|游|逛|的|附近|边|，|,|。|！|!|、|\s|[一二两三四五六七八九十0-9]+\s*[天日]|$)")
# 退路：『杭州三日游』这类无『去』前缀、地名直接接 N日游
_TRIP_DEST_BEFORE_DAYS_RE = re.compile(
    r"([一-鿿]{2,6}?)(?=[一二两三四五六七八九十0-9]+\s*[天日]游)")
_TRIP_DAYS_RE = re.compile(r"([一二两三四五六七八九十0-9]+)\s*[天日]")
_TRIP_PREF_WORDS = ("带老人", "带娃", "带孩子", "带小孩", "不要太累", "不累",
                    "轻松", "悠闲", "慢一点", "慢点", "休闲")
_TRIP_PREF_RE = re.compile("|".join(_TRIP_PREF_WORDS))
# 强出行信号：与目的地同现即判为行程规划（即便没说天数）
_TRIP_TRIGGER_RE = re.compile("行程|自驾游|度假")
# 行程修改信号：『第N天…换/改/调整』或『行程/景点…换/改』——弱 LLM 常把它误路由成
# 天气/充电（借历史里的目的地上下文），故确定性识别并改走 trip.modify。
_TRIP_MODIFY_RE = re.compile(
    r"第\s*[一二两三四五六七八九十\d]+\s*天[^，。！？]*?(换|改|调整|替换|更换|删|加|重新|别去|不去)"
    r"|(行程|景点|目的地|这天|那天)[^，。！？]*?(换|改|调整|替换|更换|重新规划)"
    r"|(换|改|调整|更换)[^，。！？]{0,4}(行程|景点|目的地|第\s*[一二两三四五六七八九十\d]+\s*天)")
# 通勤/固定地点：是导航日常目的地，不是多日出行，命中则不触发行程规划
_TRIP_DEST_BLOCK = {"公司", "家", "单位", "学校", "上班", "这里", "那里", "机场", "车站"}
_CN_NUM = {"一": "1", "两": "2", "二": "2", "三": "3", "四": "4", "五": "5",
           "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}

_PLANNER_SYSTEM = (
    "你是智能座舱的任务编排器。根据用户话术和可用 agent 能力清单，输出 JSON 调用计划。\n"
    "格式严格为：{\"complexity\":\"simple|adaptive\",\"goal\":\"一句话目标\","
    "\"steps\":[{\"id\":\"s1\",\"agent_id\":\"..\",\"intent\":\"..\","
    "\"slots\":{..},\"depends_on\":[],\"slot_refs\":{}}]}\n"
    "simple 表示一次可确定全部步骤；adaptive 表示必须根据运行结果决定下一步"
    "（例如满了换次近、失败换一家、探索式查询）。普通单域、多意图并行、固定串行都选 simple。\n"
    "\n"
    "== 意图拆分 ==\n"
    "- 用户一句话包含多个意图时（如『打开空调并播放音乐』），必须拆成多个 step\n"
    "- 单意图只输出一个 step，不要过度拆分\n"
    "\n"
    "== 并行 vs 串行 ==\n"
    "- 无数据依赖的步骤 → 各自 depends_on=[]，执行器会自动并行\n"
    "- 有数据依赖（如先搜索再预订）→ 后续步骤用 depends_on + slot_refs 引用前序结果\n"
    "- 判断依据：后一步是否需要前一步的输出数据？不需要则并行\n"
    "\n"
    "== 指令类型（规划时参考，影响执行语义）==\n"
    "- 控制类（control）：车控/媒体等硬件操作，立即执行，可并行。如 hvac.set、media.play\n"
    "- 引导类（guide）：打开 UI/导航界面。如 navigation.search_poi\n"
    "- 播报类（query）：查询后播报结果，需要联网。如 info.weather、info.news\n"
    "- 不同类型互不阻塞，可并行；同类型也可并行（只要无数据依赖）\n"
    "\n"
    "== 示例 ==\n"
    "用户：『打开空调并播放音乐』\n"
    "→ 2 个 step，无依赖，并行执行：\n"
    "{\"steps\":["
    "{\"id\":\"s1\",\"agent_id\":\"hvac\",\"intent\":\"hvac.set\",\"slots\":{\"temperature\":\"24\"},\"depends_on\":[],\"slot_refs\":{}},"
    "{\"id\":\"s2\",\"agent_id\":\"media\",\"intent\":\"media.play\",\"slots\":{},\"depends_on\":[],\"slot_refs\":{}}"
    "]}\n"
    "\n"
    "用户：『找川菜馆然后帮我订位』\n"
    "→ 2 个 step，有依赖，串行：\n"
    "{\"steps\":["
    "{\"id\":\"s1\",\"agent_id\":\"food-ordering\",\"intent\":\"food.search_restaurant\",\"slots\":{\"cuisine\":\"川菜\"},\"depends_on\":[],\"slot_refs\":{}},"
    "{\"id\":\"s2\",\"agent_id\":\"food-ordering\",\"intent\":\"food.reserve\",\"slots\":{},\"depends_on\":[\"s1\"],\"slot_refs\":{\"restaurant_id\":\"s1.data.items.0.id\"}}"
    "]}\n"
    "\n"
    "用户：『打开空调顺便看看今天天气』\n"
    "→ 2 个 step，无依赖，并行（控制类 + 播报类互不阻塞）：\n"
    "{\"steps\":["
    "{\"id\":\"s1\",\"agent_id\":\"hvac\",\"intent\":\"hvac.set\",\"slots\":{\"temperature\":\"24\"},\"depends_on\":[],\"slot_refs\":{}},"
    "{\"id\":\"s2\",\"agent_id\":\"info\",\"intent\":\"info.weather\",\"slots\":{},\"depends_on\":[],\"slot_refs\":{}}"
    "]}\n"
    "\n"
    "用户：『导航去深圳湾，在附近找个充电桩』\n"
    "→ 2 个 step，无依赖并行；充电步必须带 destination（按目的地找站，作为导航途经点）：\n"
    "{\"steps\":["
    "{\"id\":\"s1\",\"agent_id\":\"navigation\",\"intent\":\"navigation.navigate_to\",\"slots\":{\"destination\":\"深圳湾\"},\"depends_on\":[],\"slot_refs\":{}},"
    "{\"id\":\"s2\",\"agent_id\":\"charging-planner\",\"intent\":\"charging.find\",\"slots\":{\"destination\":\"深圳湾\"},\"depends_on\":[],\"slot_refs\":{}}"
    "]}\n"
    "\n"
    "用户：『导航去东方之门，然后在附近找个吃饭的地方』\n"
    "→ 单 step：顺路用餐用 navigation.navigate_to 的 stop_category（它会导航并给餐厅候选让用户选途经点），不要拆成 food.search_restaurant：\n"
    "{\"steps\":["
    "{\"id\":\"s1\",\"agent_id\":\"navigation\",\"intent\":\"navigation.navigate_to\",\"slots\":{\"destination\":\"东方之门\",\"stop_category\":\"吃饭\"},\"depends_on\":[],\"slot_refs\":{}}"
    "]}\n"
    "\n"
    "用户：『导航去东方之门途经肯德基』（用户已选好途经点）\n"
    "→ 单 step navigation.navigate_to，destination + waypoint：\n"
    "{\"steps\":["
    "{\"id\":\"s1\",\"agent_id\":\"navigation\",\"intent\":\"navigation.navigate_to\",\"slots\":{\"destination\":\"东方之门\",\"waypoint\":\"肯德基\"},\"depends_on\":[],\"slot_refs\":{}}"
    "]}\n"
    "\n"
    "用户：『周末去杭州两天，带老人，不要太累，顺便看看天气和是否需要中途充电』\n"
    "→ 3 个 step，无依赖并行：『去X玩N天』+出行偏好(带老人/轻松)本身就是行程规划意图，必须"
    "单独成 trip.plan 步，别只把它当成天气/充电的目的地上下文而漏掉：\n"
    "{\"steps\":["
    "{\"id\":\"s1\",\"agent_id\":\"trip-planner\",\"intent\":\"trip.plan\",\"slots\":{\"destination\":\"杭州\",\"days\":\"2\",\"preferences\":\"带老人,轻松不累\"},\"depends_on\":[],\"slot_refs\":{}},"
    "{\"id\":\"s2\",\"agent_id\":\"info\",\"intent\":\"info.forecast\",\"slots\":{\"city\":\"杭州\"},\"depends_on\":[],\"slot_refs\":{}},"
    "{\"id\":\"s3\",\"agent_id\":\"charging-planner\",\"intent\":\"charging.plan\",\"slots\":{\"destination\":\"杭州\"},\"depends_on\":[],\"slot_refs\":{}}"
    "]}\n"
    "\n"
    "== 通用规则 ==\n"
    "- **多日出行必出行程规划**：用户话术含『去X玩/住N天』『N日游/两日游』或带出行偏好"
    "（带老人/带娃/轻松/不要太累/悠闲/慢一点），即是行程规划意图，**必须**出一个 trip-planner 的"
    " trip.plan 步——即便同句还顺便问天气/路况/充电，trip.plan 也要与它们**并列成独立 step**，"
    "**绝不能**只把『去X几天』当成天气/充电的目的地上下文而漏掉行程规划。\n"
    "- **导航去X + 顺路/在附近 找吃饭/餐厅/咖啡** → 用**单个** navigation.navigate_to"
    "（带 stop_category，它会导航并给真实候选让用户选途经点），**不要**再拆出"
    " food.search_restaurant（food 仅用于纯找店/订位，不带导航途经点；拆了会出假数据）。\n"
    "- 用 slot_refs 引用前序 step 结果，如 {\"restaurant_id\":\"s1.data.items.0.id\"}\n"
    "- 若用户话术含指代（如『再调高一点』『还是刚才那家』『换个颜色』），"
    "优先结合下方『当前对话焦点』（对象/位置/属性/上个地点）、再参考『最近对话』"
    "补全对象/槽位后再规划\n"
    "- **隐式车控必须识别**：若最近对话含车控操作（如 hvac.set、window.open），"
    "用户说『再高/低一点』『打开/关掉』『我冷/热』等，必须映射为对应车控 step（如 hvac.inc/hvac.dec/hvac.set），"
    "不得输出空 steps 或当作闲聊。不确定具体值时用合理的默认值（如温度调高/低 1 度）。\n"
    "- 状态/查询类（query）意图必须与某个 capability 语义精确对应；"
    "若用户想查的状态（如电量、续航、能耗、剩余里程、保养）在能力清单里没有对应 intent，"
    "绝不要硬套相近的查询意图（例如把『电量/续航』套成 tire_pressure.query/胎压查询）。"
    "此时输出 {\"steps\":[]} 交系统兜底，宁可不答也不要张冠李戴\n"
    "- 只输出 JSON，不要任何解释\n"
    "- 无法匹配时输出 {\"steps\":[]}"
)

_REPLAN_SYSTEM = (
    "你是智能座舱有界任务循环的再规划器。根据用户目标、最近观察和可用能力，"
    "一次性判断任务是否完成，并在未完成时给出下一批 JSON DAG。\n"
    "严格输出 JSON：{\"done\":true|false,\"steps\":[{\"id\":\"r1\","
    "\"agent_id\":\"..\",\"intent\":\"..\",\"slots\":{},\"depends_on\":[],"
    "\"slot_refs\":{}}]}。仅在必要时改变计划；不得输出解释。"
)


class PlanBuilder:
    def __init__(self, llm_fn, registry_fn):
        """
        llm_fn: async (messages: list[dict]) -> str
        registry_fn: async (query: str, top_k: int) -> list[ResolvedAgent]
        """
        self._llm = llm_fn
        self._resolve = registry_fn

    async def build(self, text: str, working_set: WorkingSet, ctx: PlanContext,
                    granted_permissions: list[str] = None) -> Plan:
        """构建执行计划。最多重试 1 次，失败降级到语义路由。

        working_set: 由 ContextManager 装配的工作上下文——已语义预筛的 catalog +
        最近对话历史 + 长期记忆召回，统一字符预算渲染（见 context.py）。
        granted_permissions: 用户已授予的权限列表。规划时过滤掉越权能力，
        LLM 看不到用户无权调用的 Agent/意图（越权能力不暴露给 LLM）。
        """
        agents = list(working_set.catalog)
        # 权限过滤：只保留用户有权调用的 Agent
        if granted_permissions is not None:
            agents = self._filter_by_permission(agents, granted_permissions)

        agent_map = {a.manifest.agent_id: a for a in agents}

        plan = None
        for _ in range(2):
            raw = await self._llm_plan(text, agents, working_set)
            parsed = self._parse_and_validate(raw, agent_map, text)
            if parsed and parsed.steps:
                plan = parsed
                break

        if plan is None:
            logger.warning("Plan parse failed twice, falling back to chitchat/routing")
            # 降级：chitchat 全局兜底 / Registry 语义路由 top-1
            plan = await self._fallback(text, agents)

        # 确定性兜底（覆盖 LLM 解析成功 + 降级语义路由两条路径）：
        # 先判修改意图（『第N天换一个』走 trip.modify，与新规划互斥）；非修改再判新出行
        # （『去X几天』补 trip.plan）。否则弱 LLM 会把这两类都误回天气/充电、漏掉行程。
        if not self._ensure_trip_modify(plan, text, agent_map):
            self._ensure_trip_step(plan, text, agent_map)
        step_summary = [(s.id, s.agent_id, s.intent) for s in plan.steps]
        logger.info("Plan ready: complexity=%s steps=%s", plan.complexity, step_summary)
        return plan

    async def _llm_plan(self, text: str, agents: list, working_set: WorkingSet) -> str:
        catalog = WorkingSet.render_catalog(agents)
        ctx_block = working_set.render_context()  # 记忆 +（焦点）+ 历史，统一预算
        user_msg = f"可用能力:\n{catalog}\n\n{ctx_block}用户说: {text}"
        try:
            raw = await self._llm([
                {"role": "system", "content": _PLANNER_SYSTEM},
                {"role": "user", "content": user_msg},
            ])
            logger.info("LLM plan raw: %s", (raw or "")[:500])
            return raw
        except Exception as e:
            logger.warning("LLM plan exception: %s", e)
            return ""

    async def replan(self, goal: str, observations: list[dict], agents: list,
                     ctx: PlanContext, granted_permissions: list[str] = None,
                     working_set: WorkingSet = None) -> ReplanDecision:
        """Decide completion and optionally produce the next validated batch.

        working_set: 复用初规划的同一装配——再规划也注入历史(+焦点)，消除初规划与
        再规划上下文不一致（见 docs/design/2026-06-25-context-system-redesign.md P3）。
        """
        if granted_permissions is not None:
            agents = self._filter_by_permission(agents, granted_permissions)
        agent_map = {a.manifest.agent_id: a for a in agents}
        ctx_block = working_set.render_context() if working_set is not None else ""
        prompt = (
            f"目标：{goal}\n"
            f"{ctx_block}最近观察：{json.dumps(observations, ensure_ascii=False)}\n"
            f"可用能力：{WorkingSet.render_catalog(agents)}"
        )
        try:
            raw = await self._llm([
                {"role": "system", "content": _REPLAN_SYSTEM},
                {"role": "user", "content": prompt},
            ])
            data = json.loads(self._extract_json(raw))
        except Exception as exc:
            logger.warning("Replan failed: %s", exc)
            return ReplanDecision(done=True)

        if bool(data.get("done")):
            return ReplanDecision(done=True)
        steps = self._validated_steps(data.get("steps", []), agent_map)
        return ReplanDecision(done=not bool(steps), steps=steps)

    def _parse_and_validate(self, raw: str, agent_map: dict,
                            fallback_text: str) -> Plan | None:
        if not raw:
            return None
        try:
            data = json.loads(self._extract_json(raw))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Plan JSON parse failed: %s", e)
            return None

        steps = self._validated_steps(data.get("steps", []) or [], agent_map)
        if not steps:
            return None

        # Chitchat is the open-domain fallback. Never trust an LLM-generated
        # text slot here: it can be missing or stale, which makes the agent
        # answer an empty/previous request instead of the current utterance.
        for step in steps:
            if step.agent_id == "chitchat":
                step.slots["text"] = fallback_text

        complexity = data.get("complexity", "simple")
        if complexity not in ("simple", "adaptive"):
            complexity = "simple"
        goal = str(data.get("goal", "") or "")

        # 校验 depends_on 引用
        valid_ids = {s.id for s in steps}
        for s in steps:
            s.depends_on = [d for d in s.depends_on if d in valid_ids]

        return Plan(
            steps=steps,
            raw_text=fallback_text,
            complexity=complexity,
            goal=goal,
        )

    def _ensure_trip_step(self, plan: "Plan", text: str, agent_map: dict) -> None:
        """确定性兜底：多日出行必出行程规划（对最终 Plan 生效，覆盖解析/降级两条路径）。

        弱 LLM（如 MiMo）常把『去X玩两天/带老人』只当成天气/充电的目的地上下文，
        或干脆把整句规划失败 → 降级语义路由只命中天气/充电，漏掉 trip.plan。
        命中出行模式且 trip-planner 可用、计划里又没有 trip.plan 时，追加一个并列步
        （depends_on=[]，与天气/充电并行），交聚合器统一合成。trip-planner 内部还会
        自取天气/充电，故只补这一步即可得到完整行程卡。"""
        if not plan or "trip-planner" not in agent_map:
            return
        if any(s.intent == "trip.plan" for s in plan.steps):
            return
        dest, days, prefs = self._extract_trip(text or "")
        if not dest:
            return
        slots = {"destination": dest}
        if days:
            slots["days"] = days
        if prefs:
            slots["preferences"] = prefs
        # 复用 _validated_steps 装配 endpoint/权限/budget（与正常步骤同一路径）
        trip_steps = self._validated_steps([{
            "id": f"s_trip{len(plan.steps) + 1}",
            "agent_id": "trip-planner",
            "intent": "trip.plan",
            "slots": slots,
            "depends_on": [],
            "slot_refs": {},
        }], agent_map)
        if trip_steps:
            plan.steps.extend(trip_steps)
            logger.info("Ensured trip.plan step (safety net): dest=%s days=%s prefs=%s",
                        dest, days, prefs)

    def _ensure_trip_modify(self, plan: "Plan", text: str, agent_map: dict) -> bool:
        """确定性兜底：『第N天换一个/改行程』必走 trip.modify。返回是否命中修改意图。

        弱 LLM 常借历史里的目的地把『第二天换一个』误规划成天气/充电（甚至直接出充电路线），
        漏掉行程修改。命中修改模式时用单步 trip.modify 取代误规划的计划（修改不需要重跑
        天气/充电）。命中即返回 True，调用方据此跳过 trip.plan 兜底（二者互斥）。"""
        if "trip-planner" not in agent_map:
            return False
        if not _TRIP_MODIFY_RE.search(text or ""):
            return False
        if any(s.intent == "trip.modify" for s in plan.steps):
            return True                       # LLM 已正确路由，保持
        steps = self._validated_steps([{
            "id": "s_trip_mod", "agent_id": "trip-planner", "intent": "trip.modify",
            "slots": {"modification": text}, "depends_on": [], "slot_refs": {},
        }], agent_map)
        if steps:
            replaced = len(plan.steps)
            plan.steps = steps                # 取代误规划（如天气/充电）
            logger.info("Ensured trip.modify step (safety net), replaced %d steps", replaced)
        return True

    @staticmethod
    def _extract_trip(text: str) -> tuple[str, str, str]:
        """从话术解析 (destination, days, preferences)；非出行/无目的地返回空。"""
        m_dest = _TRIP_DEST_RE.search(text) or _TRIP_DEST_BEFORE_DAYS_RE.search(text)
        dest = (m_dest.group(1) if m_dest else "").strip()
        # 通勤/固定点用前缀判定（"公司开"仍算公司；"张家界"不会被单字"家"误杀）
        if not dest or any(dest.startswith(b) for b in _TRIP_DEST_BLOCK):
            return "", "", ""
        m_days = _TRIP_DAYS_RE.search(text)
        # 出行判定：有目的地，且（N天/N日 或 出行偏好词 或 N日游 或 行程/自驾游/度假）
        if not (m_days or _TRIP_PREF_RE.search(text) or "日游" in text
                or _TRIP_TRIGGER_RE.search(text)):
            return "", "", ""
        days = ""
        if m_days:
            d = m_days.group(1)
            days = _CN_NUM.get(d, d)
        prefs = "、".join(w for w in _TRIP_PREF_WORDS if w in text)
        return dest, days, prefs

    @staticmethod
    def _validated_steps(raw_steps: list, agent_map: dict) -> list[Step]:
        # F4：按 agent 校验 intent（不是全局集合），防止 LLM 错配 agent/intent
        agent_intents: dict[str, set[str]] = {
            aid: {c.intent for c in a.manifest.capabilities}
            for aid, a in agent_map.items()
        }

        steps = []
        invalid = False
        for s in raw_steps:
            aid = s.get("agent_id", "")
            intent = s.get("intent", "")

            # 校验 agent_id
            if aid not in agent_map:
                logger.warning("Unknown agent_id in plan: %s, skipping", aid)
                invalid = True
                continue

            # F4：intent 必须属于该 agent 的能力集，否则丢弃该 step（不替换）
            if intent not in agent_intents.get(aid, set()):
                logger.warning("Intent %s not in agent %s capabilities, dropping step",
                               intent, aid)
                invalid = True
                continue

            manifest = agent_map[aid].manifest
            step = Step(
                id=s.get("id", f"s{len(steps)+1}"),
                agent_id=aid,
                endpoint=agent_map[aid].endpoint,
                kind=getattr(manifest, "kind", "") or "agent",
                deployment=getattr(manifest, "deployment", "") or "cloud",
                intent=intent,
                slots={k: str(v) for k, v in (s.get("slots") or {}).items()},
                depends_on=s.get("depends_on") or [],
                slot_refs=s.get("slot_refs") or {},
                latency_budget_ms=int(manifest.latency_budget_ms or 5000),
                required_permissions=list(
                    getattr(manifest, "requires_permissions", []) or []),
                trust_level=getattr(manifest, "trust_level", "") or "",
                context_scopes=list(getattr(manifest, "context_scopes", []) or []),
            )
            steps.append(step)

        # Plans are atomic: executing only the valid remainder silently drops
        # user intents and can falsely report completion. Reject the whole plan
        # so the caller retries or falls back with the original utterance.
        if invalid:
            return []

        valid_ids = {step.id for step in steps}
        for step in steps:
            step.depends_on = [dep for dep in step.depends_on if dep in valid_ids]
        return steps

    async def _fallback(self, text: str, agents: list = None) -> Plan:
        """规划失败的降级。优先兜底到 chitchat（系统全局 fallback，开放域/LLM 抽风时
        仍有回应），其次 Registry 语义路由 top-1。"""
        # 1) chitchat 全局兜底：把原话交给闲聊 Agent（已在权限过滤后的 agents 里）
        for a in (agents or []):
            if a.manifest.agent_id == "chitchat":
                intent = a.manifest.capabilities[0].intent if a.manifest.capabilities else "chitchat.talk"
                return Plan(steps=[Step(
                    id="s1", agent_id="chitchat", endpoint=a.endpoint,
                    kind=getattr(a.manifest, "kind", "") or "agent",
                    deployment=getattr(a.manifest, "deployment", "") or "cloud",
                    intent=intent, slots={"text": text},
                    required_permissions=list(
                        getattr(a.manifest, "requires_permissions", []) or []),
                    trust_level=getattr(a.manifest, "trust_level", "") or "",
                )], raw_text=text)

        # 2) Registry 语义路由 top-1
        try:
            resolved = await self._resolve(text, top_k=1)
            if not resolved:
                return Plan(steps=[])
            a = resolved[0]
            intent = a.manifest.capabilities[0].intent if a.manifest.capabilities else ""
            return Plan(steps=[Step(
                id="s1", agent_id=a.manifest.agent_id, endpoint=a.endpoint,
                kind=getattr(a.manifest, "kind", "") or "agent",
                deployment=getattr(a.manifest, "deployment", "") or "cloud",
                intent=intent, slots={},
                required_permissions=list(
                    getattr(a.manifest, "requires_permissions", []) or []),
                trust_level=getattr(a.manifest, "trust_level", "") or "",
            )])
        except Exception as e:
            logger.error("Fallback routing failed: %s", e)
            return Plan(steps=[])

    @staticmethod
    def _extract_json(s: str) -> str:
        i, j = s.find("{"), s.rfind("}")
        return s[i:j + 1] if i >= 0 and j > i else s

    # 上下文/能力清单渲染已迁入 context.py 的 WorkingSet（统一字符预算）。

    @staticmethod
    def _build_intent_set(agents: list) -> set:
        intents = set()
        for a in agents:
            for c in a.manifest.capabilities:
                intents.add(c.intent)
        return intents

    @staticmethod
    def _filter_by_permission(agents: list, granted: list[str]) -> list:
        """过滤掉用户无权调用的 Agent。

        规则（fail-closed）：
        - granted 为 None → 不过滤（权限系统未启用，PoC 兼容）
        - granted 为空列表 → 只放行无权限要求的 Agent（零授权 = 最小权限）
        - Agent 的 requires_permissions 全部在 granted 中 → 保留
        - third_party Agent 的 vehicle.control scope 无论 granted 都被拒绝
        """
        if granted is None:
            return agents
        granted_set = set(granted)
        filtered = []
        for a in agents:
            manifest = a.manifest
            required = set(manifest.requires_permissions)
            # third_party 禁止 vehicle.control（硬禁令，无论授权）
            if manifest.trust_level == "third_party":
                if any(r.startswith("vehicle.control") for r in required):
                    logger.debug("Filtered %s: third_party cannot access vehicle.control",
                                 manifest.agent_id)
                    continue
            # 检查权限覆盖：无权限要求的 Agent（如 chitchat）始终放行
            missing = {
                scope for scope in required
                if not is_scope_covered(scope, granted_set)
            }
            if missing:
                logger.debug("Filtered %s: missing permissions %s", manifest.agent_id, missing)
                continue
            filtered.append(a)
        return filtered
