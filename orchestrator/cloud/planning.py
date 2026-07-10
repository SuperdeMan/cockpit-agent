"""PlanBuilder：LLM 生成 DAG 计划 + 解析 + 校验 + 重试 + 降级。

WS3 §4。LLM 把已注册 Agent 能力当工具，输出 JSON DAG 计划。
"""
from __future__ import annotations
import json
import logging
import os
from security.permission import check_permission
from .models import Plan, Step, PlanContext, ReplanDecision
from .context import WorkingSet, _FALLBACK_AGENT
from .route_hints import RouteHintEngine

logger = logging.getLogger("planner.planning")

# 路由兜底已全部机制化：research.run 与 trip.*（含 trip.plan 的目的地/天数/偏好抽取）均由各
# Agent manifest.route_hints 声明、通用 RouteHintEngine 消费（R2.1）；trip.plan 的话术抽取在
# trip-planner Agent 的 extract.py。编排核心不再持任何领域正则/Agent 字面量。

_PLANNER_BASE = (
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
    "{\"id\":\"s1\",\"agent_id\":\"nearby\",\"intent\":\"nearby.search\",\"slots\":{\"category\":\"餐饮\",\"cuisine\":\"川菜\"},\"depends_on\":[],\"slot_refs\":{}},"
    "{\"id\":\"s2\",\"agent_id\":\"nearby\",\"intent\":\"nearby.order\",\"slots\":{},\"depends_on\":[\"s1\"],\"slot_refs\":{\"poi_id\":\"s1.data.items.0.id\"}}"
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
    "→ 单 step：顺路用餐用 navigation.navigate_to 的 stop_category（它会导航并给餐厅候选让用户选途经点），不要拆成 nearby.search：\n"
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
    " nearby.search（nearby 仅用于纯发现/看详情/订位，不产生导航途经点；拆了会串味）。\n"
    "- 用 slot_refs 引用前序 step 结果，如 {\"poi_id\":\"s1.data.items.0.id\"}\n"
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

# R4.4：受话判定段——恒附在 base 之后（消费端 engine 按 input_source 门控，附着无副作用）。
# 保守取向：拿不准输出 true（宁可处理不可误丢，母卡 §7 风险缓解）。provider 无关：纯 JSON、
# 字段可选、fail-open。
_ADDRESSED_SECTION = (
    "\n\n== 受话判定（必须输出）==\n"
    "输出顶层布尔字段 \"addressed\"：这句话是否是对你（车载助手）说的。\n"
    "- true：请求/问题/指令/情绪表达（如『好烦啊』『我有点冷』也需要你回应）\n"
    "- false：明显不是对助手说的——乘客间对话片段（『妈你到哪了』）、自言自语、"
    "电台/视频/新闻播报腔（『本台记者报道…』『欢迎收听今天的节目』）、"
    "称呼他人姓名的交谈（『王总我马上发您』）、无法构成请求的残句\n"
    "- **拿不准时必须输出 true**（宁可处理，不可误丢）\n"
    "- addressed 为 false 时输出 {\"addressed\":false,\"steps\":[]}，不要输出其他内容"
)

# R4.4：路由歧义澄清段——仅当 CLARIFY_ENABLED=on 时拼入（off 时 LLM 不会输出 clarify，
# 避免它输出后被 engine 丢弃退化成空计划话术，母卡实施计划 §0-10）。
_CLARIFY_SECTION = (
    "\n\n== 路由歧义澄清（谨慎使用）==\n"
    "仅当这句话确实是对你说的、但在能力清单上存在两种以上合理且结果差异明显的落法、"
    "且从『当前对话焦点』『最近对话』都无法确定用户要哪种时，输出澄清代替 steps：\n"
    "{\"addressed\":true,\"clarify\":{\"question\":\"口语化一句提问\","
    "\"options\":[{\"label\":\"不超过10字\",\"send_text\":\"消歧后的完整第一人称指令\"}]}}\n"
    "- options 2~3 个；send_text 必须可直接当用户新指令执行（如『帮我找附近的川菜馆』）\n"
    "- **绝大多数请求是明确的，明确请求绝不允许反问**\n"
    "- **缺槽位不算歧义**（『导航』缺目的地→照常输出 step，由对应 agent 追问）\n"
    "- 多意图句只要主意图清楚就正常拆 step，不因次要成分歧义而澄清"
)


def _planner_system() -> str:
    """每次 build() 实时拼 Planner system prompt：base + 受话段（恒附）+ 澄清段（CLARIFY_ENABLED=on）。
    os.getenv 实时读——env 翻转即刻生效，且让 monkeypatch 单测可行（母卡实施计划 §0-10）。"""
    prompt = _PLANNER_BASE + _ADDRESSED_SECTION
    if os.getenv("CLARIFY_ENABLED", "off").lower() == "on":
        prompt += _CLARIFY_SECTION
    return prompt


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
        # R2.1：确定性路由兜底降为通用引擎——领域正则由各 Agent manifest.route_hints 声明，
        # 编排核心不再硬编码特定 Agent/意图（恢复「新增 Agent 不改编排核心」铁律）。
        self._route_hints = RouteHintEngine(self._validated_steps)

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
        last_raw = ""
        for _ in range(2):
            raw = await self._llm_plan(text, agents, working_set)
            last_raw = raw or last_raw
            parsed = self._parse_and_validate(raw, agent_map, text)
            # R4.4：放行「合法的空 steps 计划」——受话判定 addressed=false / 澄清 clarify
            # 的正确输出 steps 恰为空，不能当解析失败去重试+fallback（母卡实施计划 §0-1/§0-2）。
            if parsed and (parsed.steps or not parsed.addressed or parsed.clarify):
                plan = parsed
                break

        if plan is None:
            logger.warning("Plan parse failed twice, falling back to chitchat/routing")
            # 降级：chitchat 全局兜底 / Registry 语义路由 top-1
            plan = await self._fallback(text, agents)
        # 观测：保留 LLM 最后一次原始输出（fallback 路径保留失败现场），供 planning span 门控采集
        plan.raw_llm = last_raw

        # 确定性路由兜底（覆盖 LLM 解析成功 + 降级语义路由两条路径）：通用 RouteHintEngine
        # 按各 Agent manifest.route_hints（priority 降序）施加。research.run 与 trip.*（含
        # trip.plan append 新出行兜底）全部为各 Agent 声明式 route_hints——编排核心不含任何
        # 领域 Agent/意图字面量（恢复「新增 Agent 不改编排核心」铁律）。
        self._route_hints.apply(plan, text, agent_map)
        step_summary = [(s.id, s.agent_id, s.intent) for s in plan.steps]
        logger.info("Plan ready: complexity=%s steps=%s", plan.complexity, step_summary)
        return plan

    async def _llm_plan(self, text: str, agents: list, working_set: WorkingSet) -> str:
        catalog = WorkingSet.render_catalog(agents)
        ctx_block = working_set.render_context()  # 记忆 +（焦点）+ 历史，统一预算
        user_msg = f"可用能力:\n{catalog}\n\n{ctx_block}用户说: {text}"
        try:
            raw = await self._llm([
                {"role": "system", "content": _planner_system()},
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

        # R4.4：受话/澄清在 steps 校验之前短路——它们的合法输出 steps 恰为空，若走下面
        # `if not steps: return None` 会被当解析失败触发重试+fallback（母卡实施计划 §0-1）。
        if data.get("addressed") is False:      # 仅显式 false 生效；缺省/垃圾值=True（fail-open）
            return Plan(steps=[], raw_text=fallback_text, addressed=False)
        clarify = self._parse_clarify(data.get("clarify"))

        steps = self._validated_steps(data.get("steps", []) or [], agent_map)
        if not steps:
            if clarify:      # 是请求但落法歧义：无 steps 但带合法 clarify → 合法计划（P1 消费）
                return Plan(steps=[], raw_text=fallback_text, clarify=clarify)
            return None
        # steps 非空 → clarify 忽略（互斥，执行优先，母卡 D6-2>D6-3）；后续现状不动。

        # Chitchat is the open-domain fallback. Never trust an LLM-generated
        # text slot here: it can be missing or stale, which makes the agent
        # answer an empty/previous request instead of the current utterance.
        for step in steps:
            if step.agent_id == _FALLBACK_AGENT:
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

    @staticmethod
    def _parse_clarify(raw) -> dict | None:
        """R4.4：解析澄清输出。非 dict / question 空 / 有效 options<2 → None；options>3 截断为 3；
        每项须 label+send_text 均为非空 str。纯函数不读 env——CLARIFY_ENABLED 由 prompt 拼接
        （生产端）与 engine 消费（消费端）两端门控，解析器只认格式（母卡实施计划 §0-10）。"""
        if not isinstance(raw, dict):
            return None
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            return None
        opts_raw = raw.get("options")
        if not isinstance(opts_raw, list):
            return None
        options = []
        for o in opts_raw:
            if not isinstance(o, dict):
                continue
            label, send_text = o.get("label"), o.get("send_text")
            if (isinstance(label, str) and label.strip()
                    and isinstance(send_text, str) and send_text.strip()):
                options.append({"label": label.strip(), "send_text": send_text.strip()})
        if len(options) < 2:
            return None
        return {"question": question.strip(), "options": options[:3]}

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
                heavy=next((bool(getattr(c, "heavy", False))
                            for c in manifest.capabilities if c.intent == intent), False),
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
        """规划失败的降级。优先兜底到全局兜底 Agent（env PLANNER_FALLBACK_AGENT，默认
        chitchat；开放域/LLM 抽风时仍有回应），其次 Registry 语义路由 top-1。"""
        # 1) 全局兜底 Agent：把原话交给它（已在权限过滤后的 agents 里）
        for a in (agents or []):
            if a.manifest.agent_id == _FALLBACK_AGENT:
                intent = a.manifest.capabilities[0].intent if a.manifest.capabilities else "chitchat.talk"
                return Plan(steps=[Step(
                    id="s1", agent_id=a.manifest.agent_id, endpoint=a.endpoint,
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
            # R4.4 D5-2：低分不再硬执行 capabilities[0]（堵「score 0.36 也硬套首个能力」真 bug）。
            # 门槛与 SEMANTIC_PROMOTE_SIM 对齐（精确 intent=1.0/关键词 0.3+/语义重排=真 cosine）。
            # 分数不足 → 诚实降级空计划（engine 出「没听清」话术），不臆断。chitchat 全局兜底
            # （上面第 1 优先分支）不受影响——门槛只作用于「chitchat 不在 catalog、走语义 top-1」路径。
            if (float(getattr(a, "score", 0.0) or 0.0)
                    < float(os.getenv("CLARIFY_FALLBACK_MIN", "0.5"))):
                logger.info("Fallback top-1 score %.3f below threshold, honest degrade",
                            float(getattr(a, "score", 0.0) or 0.0))
                return Plan(steps=[])
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
        """过滤掉用户无权调用的 Agent（越权能力不暴露给 LLM）。

        判定委托运行时唯一决策 `security.permission.check_permission`（与 dispatch 执行期同源）：
        - granted 为 None → 不过滤（权限系统未启用，PoC 兼容）
        - granted 为空列表 → 只放行无权限要求的 Agent（零授权 = 最小权限）
        - Agent 的 requires_permissions 被 granted（父子覆盖）全覆盖 → 保留
        - third_party Agent 的 vehicle.control 无论 granted 都被拒绝
        """
        if granted is None:
            return agents
        filtered = []
        for a in agents:
            m = a.manifest
            d = check_permission(
                agent_id=m.agent_id, trust_level=m.trust_level,
                required=list(m.requires_permissions), granted=granted, kind="agent")
            if not d.allowed:
                logger.debug("Filtered %s: %s", m.agent_id, d.reason)
                continue
            filtered.append(a)
        return filtered
