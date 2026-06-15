"""PlanBuilder：LLM 生成 DAG 计划 + 解析 + 校验 + 重试 + 降级。

WS3 §4。LLM 把已注册 Agent 能力当工具，输出 JSON DAG 计划。
"""
from __future__ import annotations
import json
import logging
from security.scopes import is_scope_covered
from .models import Plan, Step, PlanContext, ReplanDecision

logger = logging.getLogger("planner.planning")

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
    "- 播报类（query）：查询后播报结果，需要联网。如 weather.current、news.summary\n"
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
    "{\"id\":\"s2\",\"agent_id\":\"weather\",\"intent\":\"weather.current\",\"slots\":{},\"depends_on\":[],\"slot_refs\":{}}"
    "]}\n"
    "\n"
    "== 通用规则 ==\n"
    "- 用 slot_refs 引用前序 step 结果，如 {\"restaurant_id\":\"s1.data.items.0.id\"}\n"
    "- 若用户话术含指代（如『再调高一点』『还是刚才那家』『换个颜色』），"
    "结合下方『最近对话』补全对象/槽位后再规划\n"
    "- **隐式车控必须识别**：若最近对话含车控操作（如 hvac.set、window.open），"
    "用户说『再高/低一点』『打开/关掉』『我冷/热』等，必须映射为对应车控 step（如 hvac.inc/hvac.dec/hvac.set），"
    "不得输出空 steps 或当作闲聊。不确定具体值时用合理的默认值（如温度调高/低 1 度）。\n"
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

    async def build(self, text: str, agents: list, ctx: PlanContext,
                    granted_permissions: list[str] = None,
                    history: list[dict] = None) -> Plan:
        """构建执行计划。最多重试 1 次，失败降级到语义路由。

        granted_permissions: 用户已授予的权限列表。规划时过滤掉越权能力，
        LLM 看不到用户无权调用的 Agent/意图（越权能力不暴露给 LLM）。
        history: 最近对话（task 2），注入 prompt 供指代消解。
        """
        # 权限过滤：只保留用户有权调用的 Agent
        if granted_permissions is not None:
            agents = self._filter_by_permission(agents, granted_permissions)

        agent_map = {a.manifest.agent_id: a for a in agents}

        for _ in range(2):
            raw = await self._llm_plan(text, agents, history)
            plan = self._parse_and_validate(raw, agent_map, text)
            if plan and plan.steps:
                step_summary = [(s.id, s.agent_id, s.intent) for s in plan.steps]
                logger.info("Plan parsed: complexity=%s steps=%s", plan.complexity, step_summary)
                return plan

        logger.warning("Plan parse failed twice, falling back to chitchat/routing")
        # 降级：chitchat 全局兜底 / Registry 语义路由 top-1
        return await self._fallback(text, agents)

    async def _llm_plan(self, text: str, agents: list, history: list[dict] = None) -> str:
        catalog = self._build_catalog(agents)
        ctx_block = self._format_history(history)
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
                     ctx: PlanContext, granted_permissions: list[str] = None
                     ) -> ReplanDecision:
        """Decide completion and optionally produce the next validated batch."""
        if granted_permissions is not None:
            agents = self._filter_by_permission(agents, granted_permissions)
        agent_map = {a.manifest.agent_id: a for a in agents}
        prompt = (
            f"目标：{goal}\n"
            f"最近观察：{json.dumps(observations, ensure_ascii=False)}\n"
            f"可用能力：{self._build_catalog(agents)}"
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

        steps = self._validated_steps(data.get("steps", []), agent_map)
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

    @staticmethod
    def _build_catalog(agents: list) -> str:
        items = []
        for a in agents:
            caps = [{"intent": c.intent, "slots": list(c.slots), "desc": c.description}
                    for c in a.manifest.capabilities]
            items.append({
                "agent_id": a.manifest.agent_id,
                "kind": getattr(a.manifest, "kind", "") or "agent",
                "deployment": getattr(a.manifest, "deployment", "") or "cloud",
                "capabilities": caps,
            })
        return json.dumps(items, ensure_ascii=False)

    @staticmethod
    def _format_history(history: list[dict] | None) -> str:
        """把最近对话格式化为 prompt 片段（最多 4 轮），供指代消解。"""
        if not history:
            return ""
        lines = []
        for t in history[-4:]:
            txt = (t.get("text") or "").strip()
            if txt:
                who = "用户" if t.get("role") == "user" else "助手"
                lines.append(f"{who}：{txt}")
        if not lines:
            return ""
        return "最近对话（用于指代消解）：\n" + "\n".join(lines) + "\n\n"

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
