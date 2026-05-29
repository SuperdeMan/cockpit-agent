"""PlanBuilder：LLM 生成 DAG 计划 + 解析 + 校验 + 重试 + 降级。

WS3 §4。LLM 把已注册 Agent 能力当工具，输出 JSON DAG 计划。
"""
from __future__ import annotations
import json
import logging
from .models import Plan, Step, PlanContext, StepStatus

logger = logging.getLogger("planner.planning")

_PLANNER_SYSTEM = (
    "你是智能座舱的任务编排器。根据用户话术和可用 agent 能力清单，输出 JSON 调用计划。\n"
    "格式严格为：{\"steps\":[{\"id\":\"s1\",\"agent_id\":\"..\",\"intent\":\"..\","
    "\"slots\":{..},\"depends_on\":[],\"slot_refs\":{}}]}\n"
    "规则：\n"
    "- 单意图只输出一个 step\n"
    "- 组合意图拆成多步，用 depends_on 表示依赖\n"
    "- 用 slot_refs 引用前序 step 结果，如 {\"restaurant_id\":\"s1.data.items.0.id\"}\n"
    "- 只输出 JSON，不要任何解释\n"
    "- 无法匹配时输出 {\"steps\":[]}"
)


class PlanBuilder:
    def __init__(self, llm_fn, registry_fn):
        """
        llm_fn: async (messages: list[dict]) -> str
        registry_fn: async (query: str, top_k: int) -> list[ResolvedAgent]
        """
        self._llm = llm_fn
        self._resolve = registry_fn

    async def build(self, text: str, agents: list, ctx: PlanContext) -> Plan:
        """构建执行计划。最多重试 1 次，失败降级到语义路由。"""
        agent_map = {a.manifest.agent_id: a for a in agents}
        valid_intents = self._build_intent_set(agents)

        for _ in range(2):
            raw = await self._llm_plan(text, agents)
            plan = self._parse_and_validate(raw, agent_map, valid_intents, text)
            if plan and plan.steps:
                return plan

        # 降级：Registry 语义路由 top-1
        return await self._fallback(text)

    async def _llm_plan(self, text: str, agents: list) -> str:
        catalog = self._build_catalog(agents)
        user_msg = f"可用能力:\n{catalog}\n\n用户说: {text}"
        try:
            return await self._llm([
                {"role": "system", "content": _PLANNER_SYSTEM},
                {"role": "user", "content": user_msg},
            ])
        except Exception as e:
            logger.warning("LLM plan failed: %s", e)
            return ""

    def _parse_and_validate(self, raw: str, agent_map: dict,
                            valid_intents: set, fallback_text: str) -> Plan | None:
        if not raw:
            return None
        try:
            data = json.loads(self._extract_json(raw))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Plan JSON parse failed: %s", e)
            return None

        steps = []
        for s in data.get("steps", []):
            aid = s.get("agent_id", "")
            intent = s.get("intent", "")

            # 校验 agent_id
            if aid not in agent_map:
                logger.warning("Unknown agent_id in plan: %s, skipping", aid)
                continue

            # 校验 intent
            if intent not in valid_intents:
                # 用该 agent 首个 capability 兜底
                caps = agent_map[aid].manifest.capabilities
                intent = caps[0].intent if caps else ""
                if not intent:
                    continue

            step = Step(
                id=s.get("id", f"s{len(steps)+1}"),
                agent_id=aid,
                intent=intent,
                slots={k: str(v) for k, v in (s.get("slots") or {}).items()},
                depends_on=s.get("depends_on") or [],
                slot_refs=s.get("slot_refs") or {},
                latency_budget_ms=int(agent_map[aid].manifest.latency_budget_ms or 5000),
            )
            steps.append(step)

        if not steps:
            return None

        # 校验 depends_on 引用
        valid_ids = {s.id for s in steps}
        for s in steps:
            s.depends_on = [d for d in s.depends_on if d in valid_ids]

        return Plan(steps=steps, raw_text=fallback_text)

    async def _fallback(self, text: str) -> Plan:
        """Registry 语义路由降级。"""
        try:
            agents = await self._resolve(text, top_k=1)
            if not agents:
                return Plan(steps=[])
            a = agents[0]
            intent = a.manifest.capabilities[0].intent if a.manifest.capabilities else ""
            return Plan(steps=[Step(
                id="s1", agent_id=a.manifest.agent_id, endpoint=a.endpoint,
                intent=intent, slots={},
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
            items.append({"agent_id": a.manifest.agent_id, "capabilities": caps})
        return json.dumps(items, ensure_ascii=False)

    @staticmethod
    def _build_intent_set(agents: list) -> set:
        intents = set()
        for a in agents:
            for c in a.manifest.capabilities:
                intents.add(c.intent)
        return intents
