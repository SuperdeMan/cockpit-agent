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

    async def compose(self, user_text: str, results: list[StepResult]) -> dict:
        """聚合结果，返回 Final 事件结构。"""
        actions = [a for r in results for a in r.actions]
        cards = [r.ui_card for r in results if r.ui_card]
        follow_ups = [r.follow_up for r in results if r.follow_up]
        # ui_card 取第一个（单 Agent 场景）；多 Agent 未来可聚合
        ui_card = cards[0] if cards else None

        if not results:
            return {"speech": "抱歉，我暂时无法处理这个请求。", "actions": []}

        if len(results) == 1:
            r = results[0]
            if r.status == StepStatus.FAILED:
                return {"speech": f"抱歉，{r.error or '处理失败'}。", "actions": []}
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

    async def _aggregate_speech(self, user_text: str, results: list[StepResult]) -> str:
        """用 LLM 把多步结果改写为连贯口语。"""
        summaries = []
        for r in results:
            if r.status == StepStatus.OK and r.speech:
                summaries.append(r.speech)
            elif r.status == StepStatus.FAILED:
                summaries.append(f"[{r.step_id} 失败: {r.error}]")

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
