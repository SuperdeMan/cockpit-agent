"""Cloud Planner 旧版实现（Phase 0 PoC）。

⚠️ 不可运行：本文件的 call_agent 调用签名与当前 clients.py 不匹配（5参 vs 4参），
一跑就 TypeError。仅供历史参考，不要 import。

Phase 1 新版已在以下模块实现：
- engine.py：编排主循环
- planning.py：LLM DAG 规划 + schema 校验 + 降级
- executor.py：拓扑分层并行 + 超时 + 部分失败
- aggregator.py：结果聚合
- session.py：多轮状态机
- circuit.py：熔断器

TODO：确认后删除本文件（需经用户确认）。
"""
from __future__ import annotations
import json

from cockpit.agent.v1 import agent_pb2

from clients import Clients

_TERMINAL = {
    agent_pb2.ExecuteResponse.NEED_CONFIRM,
    agent_pb2.ExecuteResponse.NEED_SLOT,
    agent_pb2.ExecuteResponse.REJECTED,
    agent_pb2.ExecuteResponse.FAILED,
}

_PLANNER_SYSTEM = (
    "你是智能座舱的任务编排器。根据用户话术和可用 agent 能力清单，输出 JSON 调用计划。"
    "格式严格为 {\"steps\":[{\"agent_id\":\"..\",\"intent\":\"..\",\"slots\":{..}}]}。"
    "按需要拆成多步（如先搜索再预订）。只输出 JSON，不要任何解释。无法匹配时输出 {\"steps\":[]}。"
)


class Planner:
    def __init__(self):
        self.c = Clients()

    async def run(self, request):
        """异步生成 ('speech'|'action'|'final', payload)。"""
        agents = await self.c.list_agents()
        steps = await self._plan(request.text, agents)
        if not steps:
            yield ("final", {"speech": "抱歉，我暂时无法处理这个请求。"})
            return

        last, actions = None, []
        for step in steps:
            resp = await self.c.call_agent(
                step["endpoint"], step["intent"], step["slots"], request.text, request.context)
            last = resp
            actions.extend(resp.actions)
            if resp.status in _TERMINAL:
                break

        yield ("final", {
            "speech": last.speech,
            "actions": actions,
            "follow_up": last.follow_up,
            "need_confirm": last.status == agent_pb2.ExecuteResponse.NEED_CONFIRM,
        })

    async def _plan(self, text, agents):
        return await self._llm_plan(text, agents) or await self._fallback(text)

    async def _llm_plan(self, text, agents):
        if not agents:
            return None
        endpoints = {a.manifest.agent_id: a.endpoint for a in agents}
        catalog = [{
            "agent_id": a.manifest.agent_id,
            "capabilities": [{"intent": c.intent, "slots": list(c.slots), "desc": c.description}
                             for c in a.manifest.capabilities],
        } for a in agents]
        user = f"可用能力:\n{json.dumps(catalog, ensure_ascii=False)}\n\n用户说: {text}"
        try:
            content = await self.c.llm_complete(
                [{"role": "system", "content": _PLANNER_SYSTEM}, {"role": "user", "content": user}])
            data = json.loads(self._extract_json(content))
            steps = []
            for s in data.get("steps", []):
                aid = s.get("agent_id")
                if aid in endpoints:
                    steps.append({
                        "agent_id": aid, "endpoint": endpoints[aid],
                        "intent": s.get("intent", ""),
                        "slots": {k: str(v) for k, v in (s.get("slots") or {}).items()},
                    })
            return steps or None
        except Exception as e:
            print(f"[planner] LLM plan unusable ({e}); falling back to semantic route", flush=True)
            return None

    async def _fallback(self, text):
        agents = await self.c.resolve(text, top_k=1)
        if not agents:
            return []
        a = agents[0]
        intent = a.manifest.capabilities[0].intent if a.manifest.capabilities else ""
        return [{"agent_id": a.manifest.agent_id, "endpoint": a.endpoint, "intent": intent, "slots": {}}]

    @staticmethod
    def _extract_json(s: str) -> str:
        i, j = s.find("{"), s.rfind("}")
        return s[i:j + 1] if i >= 0 and j > i else s
