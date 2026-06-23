"""Aggregator：多 Agent 结果 → 口语话术 + 卡片。

WS3 §7。单步直出（省一次 LLM 调用），多步 LLM 改写为连贯口语。
"""
from __future__ import annotations
import logging
from .models import StepResult, StepStatus

logger = logging.getLogger("planner.aggregator")

_AGGREGATE_SYSTEM = (
    "你是座舱助手的回复组织者。把多个步骤的结果改写为一段连贯口语，适合语音播报。\n"
    "要求：不超过 3 句话，不罗列 JSON，语气自然口语化。"
)


class Aggregator:
    def __init__(self, llm_fn):
        """llm_fn: async (messages: list[dict]) -> str"""
        self._llm = llm_fn

    # 内部错误码 → 用户友好话术
    _ERROR_FRIENDLY = {
        "step_timeout": "处理超时了，请稍后再试",
        "timeout": "处理超时了，请稍后再试",
        "circuit_open": "该服务暂时不可用，请稍后再试",
    }

    async def compose(self, user_text: str, results: list[StepResult]) -> dict:
        """聚合结果，返回 Final 事件结构。"""
        actions = self._compose_actions(results)
        cards = [r.ui_card for r in results if r.ui_card]
        follow_ups = [r.follow_up for r in results if r.follow_up]
        # 多卡时择一展示：优先信息密度高/需用户操作的卡（充电路线途经点、顺路停靠/目的地
        # 候选选择卡），否则取第一个。（多卡同屏渲染待 HMI/协议支持后再做。）
        def _card_priority(c: dict) -> int:
            if c.get("type") == "charging_route":
                return 0
            if (c.get("type") == "poi_list"
                    and c.get("purpose") in ("waypoint_choice", "dest_choice")):
                return 1
            return 2
        ui_card = min(cards, key=_card_priority) if cards else None

        if not results:
            return {"speech": "抱歉，我暂时无法处理这个请求。", "actions": []}

        if len(results) == 1:
            r = results[0]
            if r.status == StepStatus.FAILED:
                friendly = self._ERROR_FRIENDLY.get(r.error or "", r.error or "处理失败")
                return {"speech": f"抱歉，{friendly}。", "actions": []}
            return {
                "speech": r.speech,
                "actions": actions,
                "ui_card": ui_card,
                "follow_up": r.follow_up,
                "need_confirm": r.status == StepStatus.NEED_CONFIRM,
            }

        # 多步：LLM 聚合
        speech = await self._aggregate_speech(user_text, results)
        return {
            "speech": speech,
            "actions": actions,
            "ui_card": ui_card,
            "follow_up": follow_ups[0] if follow_ups else "",
        }

    @staticmethod
    def _compose_actions(results: list[StepResult]) -> list[dict]:
        """汇总各步动作，并把充电途经点并入导航 navigate 动作。

        - 收集途经点：充电步用 data.waypoint / data.waypoints 暴露最优充电站坐标；
        - navigate 去重：同目的地的重复 navigate 只保留首个（防御多意图重复导航）；
        - 注入 waypoints：让“导航去X + 附近充电”产出带途经充电点的单条路线，而非
          孤立的充电列表 + 直达导航。
        """
        actions = [a for r in results for a in r.actions]

        waypoints: list[dict] = []
        for r in results:
            data = r.data or {}
            wp = data.get("waypoint")
            if isinstance(wp, dict) and wp.get("name"):
                waypoints.append(wp)
            for wp in (data.get("waypoints") or []):
                if isinstance(wp, dict) and wp.get("name"):
                    waypoints.append(wp)

        composed, seen_nav = [], set()
        for a in actions:
            if a.get("type") == "navigate":
                payload = dict(a.get("payload") or {})
                key = (payload.get("destination"),
                       payload.get("lat"), payload.get("lng"))
                if key in seen_nav:
                    continue          # 去重：同目的地的重复导航丢弃
                seen_nav.add(key)
                if waypoints:
                    payload["waypoints"] = waypoints
                    a = {**a, "payload": payload}
            composed.append(a)
        return composed

    async def _aggregate_speech(self, user_text: str, results: list[StepResult]) -> str:
        """用 LLM 把多步结果改写为连贯口语。"""
        summaries = []
        for r in results:
            if r.status == StepStatus.OK and r.speech:
                summaries.append(r.speech)
            elif r.status == StepStatus.FAILED:
                friendly = self._ERROR_FRIENDLY.get(r.error or "", r.error or "处理失败")
                summaries.append(f"[{r.step_id} 失败: {friendly}]")

        prompt = (
            f"用户说：{user_text}\n\n"
            f"各步骤结果：\n" + "\n".join(f"- {s}" for s in summaries) + "\n\n"
            "请把上述结果组织为一段连贯的口语回复。"
        )
        try:
            return await self._llm([
                {"role": "system", "content": _AGGREGATE_SYSTEM},
                {"role": "user", "content": prompt},
            ])
        except Exception as e:
            logger.warning("Aggregation LLM failed, using raw: %s", e)
            return " ".join(r.speech for r in results if r.speech)
