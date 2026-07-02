"""RouteHintEngine —— 确定性路由兜底的通用引擎（R2.1）。

背景：弱 LLM（MiMo）常漏/误路由重域意图（把「深入调研 X」当浅搜、「第N天换一个」
当天气/充电）。历史做法是在编排核心 `planning.py` 为每个重域 Agent 硬编码正则 + `_ensure_*`
兜底——违反「新增 Agent 不改编排核心」铁律，且债随 Agent 数线性增长、正则互扰频发。

本引擎把「路由兜底」降为**通用机制**：领域路由知识由各 Agent 在 `manifest.yaml` 的
`route_hints` 声明（见 proto `RouteHint`），编排核心只保留这一个引擎按 `priority` 通用消费。
语义**逐字复刻**原 `_ensure_*` 系列：

- `replace`（默认，互斥）：命中即用单步取代整条计划；若该 intent 已在计划中则视为 LLM 已正确
  路由、保留不动；**命中即停**（对应原「research 与 trip 互斥、trip 内 navigate>…>modify 首命中 break」）。
- `append`（并列）：命中则并列补一步（`depends_on=[]`，与天气/充电等并行），intent 已存在则跳过；
  不终止迭代（对应原 `_ensure_trip_step` 只在无 replace 命中时补 trip.plan）。

`priority` 降序应用；约定 replace 类高于 append 类（与原顺序一致：research=100 >
trip.navigate/reschedule/status/modify=90/80/70/60 > trip.plan(append)=50）。
`guard` 为可选反例守卫正则：命中 guard 则该 hint 不生效（对应原 `_TRIP_NAV_BLOCK_RE`）。
`slots` 模板：值 `$text`→原话、`$1..`→捕获组、其余字面量原样。
"""
from __future__ import annotations

import logging
import re
from typing import Callable

logger = logging.getLogger(__name__)

# slot 模板中的捕获组引用：$1 / $2 ...
_GROUP_REF_RE = re.compile(r"^\$(\d+)$")


class RouteHintEngine:
    """按已注册 Agent 的 manifest.route_hints 对 Plan 施加确定性路由兜底。

    与原 `_ensure_*` 等价：只在弱 LLM 漏/误判时兜底，不劫持 LLM 已正确路由的计划。
    """

    def __init__(self, validate_steps: Callable[[list, dict], list]):
        # validate_steps(raw_steps, agent_map) -> list[Step]，复用 planner._validated_steps，
        # 使兜底步与正常步走同一装配路径（endpoint/权限/budget/intent∈能力集 校验）。
        self._validate = validate_steps

    def apply(self, plan, text: str, agent_map: dict) -> bool:
        """对 plan 原地施加路由兜底。返回是否命中（命中过任一 hint 即 True，供调用方观测）。

        agent_map: {agent_id: agent}，agent.manifest.route_hints 为该 Agent 声明的提示。
        """
        text = text or ""
        hit = False
        for agent_id, hint in self._ordered_hints(agent_map):
            m = self._match(hint, text)
            if m is None:
                continue
            policy = (hint.policy or "replace").lower()
            if policy == "append":
                hit = True
                if not self._has_intent(plan, hint.intent):
                    self._apply_step(plan, agent_id, hint, m, text, agent_map, replace=False)
                # append 不终止：允许多个 Agent 并列补步
                continue
            # replace（默认，互斥）
            hit = True
            if self._has_intent(plan, hint.intent):
                # LLM 已正确路由到该 intent，保留原计划；replace 互斥 → 停
                return True
            self._apply_step(plan, agent_id, hint, m, text, agent_map, replace=True)
            return True
        return hit

    # ── 内部 ──

    @staticmethod
    def _ordered_hints(agent_map: dict):
        """收集所有 (agent_id, hint)，按 priority 降序、同优先级保持声明顺序（稳定）。"""
        collected = []
        for agent_id, agent in agent_map.items():
            manifest = getattr(agent, "manifest", None)
            for idx, hint in enumerate(getattr(manifest, "route_hints", []) or []):
                collected.append((agent_id, hint, idx))
        # 稳定排序：priority 降序为主键；同优先级按 (agent_id, 声明序) 保持确定性
        collected.sort(key=lambda t: (-int(t[1].priority or 0), t[0], t[2]))
        return [(agent_id, hint) for agent_id, hint, _ in collected]

    @staticmethod
    def _match(hint, text: str):
        """pattern 命中且 guard 不命中 → 返回 match 对象；否则 None。"""
        pattern = hint.pattern or ""
        if not pattern:
            return None
        try:
            m = re.search(pattern, text)
        except re.error:
            logger.warning("route_hint bad pattern for intent=%s: %r", hint.intent, pattern)
            return None
        if m is None:
            return None
        guard = hint.guard or ""
        if guard:
            try:
                if re.search(guard, text):
                    return None
            except re.error:
                logger.warning("route_hint bad guard for intent=%s: %r", hint.intent, guard)
        return m

    @staticmethod
    def _has_intent(plan, intent: str) -> bool:
        return any(s.intent == intent for s in plan.steps)

    def _apply_step(self, plan, agent_id, hint, match, text, agent_map, replace: bool):
        raw = {
            "id": f"s_hint_{hint.intent.replace('.', '_')}",
            "agent_id": agent_id,
            "intent": hint.intent,
            "slots": self._resolve_slots(hint, match, text),
            "depends_on": [],
            "slot_refs": {},
        }
        steps = self._validate([raw], agent_map)
        if not steps:
            return
        if replace:
            plan.steps = steps
            logger.info("route_hint replace -> %s (%s)", hint.intent, agent_id)
        else:
            plan.steps.extend(steps)
            logger.info("route_hint append -> %s (%s)", hint.intent, agent_id)

    @staticmethod
    def _resolve_slots(hint, match, text: str) -> dict:
        """把 slots 模板解析为实际值：$text→原话、$N→捕获组、其余字面量。"""
        out = {}
        for key, tmpl in (hint.slots or {}).items():
            if tmpl == "$text":
                out[key] = text
                continue
            g = _GROUP_REF_RE.match(tmpl or "")
            if g and match is not None:
                idx = int(g.group(1))
                try:
                    out[key] = match.group(idx) or ""
                except (IndexError, re.error):  # 捕获组不存在
                    out[key] = ""
                continue
            out[key] = tmpl
        return out
