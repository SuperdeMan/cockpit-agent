"""Aggregator：多 Agent 结果 → 口语话术 + 卡片。

WS3 §7。单步直出（省一次 LLM 调用），多步 LLM 改写为连贯口语。
"""
from __future__ import annotations
import logging
import re
from .models import StepResult, StepStatus

logger = logging.getLogger("planner.aggregator")

# ── speech 出口 markdown 归一（编排自持实现）────────────────────────────────
# 设计决策（2026-07-12）：speech 不上 markdown 渲染——第一消费者是 TTS，结构化内容归卡片。
# **刻意不 import agents/_sdk**：cloud-planner 镜像不含 agents/（跨镜像依赖闭包坑，
# 真栈实测 ModuleNotFoundError 崩启动）。与 `agents/_sdk/grounding.strip_markdown_speech`
# （Agent 侧）、`llm-gateway/providers._strip_md_tts`（TTS 侧）配对，口径变化三处同步。
_MD_FENCE = re.compile(r"(?m)^\s*```.*$")
_MD_TABLE_SEP = re.compile(r"(?m)^\s*\|?\s*:?-{2,}[-|:\s]*$")
_MD_TABLE_ROW = re.compile(r"(?m)^\s*\|(.+)\|\s*$")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_MD_QUOTE = re.compile(r"(?m)^\s{0,3}>\s?")
_MD_BULLET = re.compile(r"(?m)^(\s*)[-*•]\s+")


def strip_markdown_speech(text: str) -> str:
    """LLM 泄漏的 markdown → speech 可读纯文本。保留数字序号行（分行要点是刻意的）、
    单个 *（乘号防误伤）；表格行退化为顿号连接。"""
    t = text or ""
    if not any(ch in t for ch in ("*", "#", "`", "|", "[", ">", "_")):
        return t
    t = _MD_FENCE.sub("", t)
    t = _MD_TABLE_SEP.sub("", t)
    t = _MD_TABLE_ROW.sub(
        lambda m: "，".join(c.strip() for c in m.group(1).split("|") if c.strip()), t)
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_HEADING.sub("", t)
    t = _MD_QUOTE.sub("", t)
    t = _MD_BULLET.sub(r"\1", t)
    t = t.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\n{3,}", "\n\n", t).strip()


class MdDeltaSoftener:
    """流式 speech 增量的轻量 markdown 软化（D0 直通路径专用）。

    只剥行内视觉噪声 ``**``/`` ` ``（跨 chunk 安全：尾悬单 ``*`` 暂存一拍等下个增量配对）；
    完整清理由 final 的 `strip_markdown_speech`（aggregator.compose 出口）收口——流式期间
    屏显/TTS 不再蹦星号，final 替换整段时彻底干净。
    """

    def __init__(self):
        self._carry = ""

    def feed(self, delta: str) -> str:
        s = self._carry + (delta or "")
        self._carry = ""
        s = s.replace("**", "").replace("`", "")
        if s.endswith("*"):
            self._carry, s = "*", s[:-1]
        return s

    def flush(self) -> str:
        out, self._carry = self._carry, ""
        return out

_AGGREGATE_SYSTEM = (
    "你是座舱助手的回复组织者。把多个步骤的结果组织为自然口语回复，不罗列 JSON。\n"
    "保留每个意图的实质内容：新闻给要点、股价给数字、分析给结论——别压成一个笼统总结，"
    "也别让某一项把其它项吞掉（比如别用结论替掉新闻要点）。\n"
    "当用户对某个具体问题要求了条数/分点（如『对X有没有影响，给我三条结论』），那几条结论必须"
    "紧扣那个问题（每条都围绕『对X的影响』展开，不要拿股价、新闻凑数）；新闻、股价等其它意图"
    "在结论之外另外简述带过。\n"
    "没有这类格式要求时，默认连贯口语、简洁（不超过 3 句）。"
)


# M2 Outcome Verifier：对账判定「确凿未达成」时补的诚实口径（executor 落
# `data["_verify"]` 保留键，见 docs/conventions.md §9.1）。**确定性拼接、不进 LLM**——
# 「有没有生效」是系统持有的事实，让模型改写它就可能被润色掉（墙钟直答同一原则）。
_VERIFY_NOTE = {
    "state_match": "不过我没确认到这个操作真的生效，你留意一下。",
    "schema": "不过这次没拿到实际内容，可能是数据源没返回，要不要我再试一次？",
}
_VERIFY_NOTE_DEFAULT = "不过我没确认到结果真的达成，你留意一下。"

# M-C：传输不确定的失败被世界状态证实「其实已经生效」时的诚实口径。
# **不伪造成功话术**——合成「后备箱已打开」需要领域知识，而编排核心零领域字面量；
# 但也不能继续说「抱歉，处理超时」，那是**已知为假**的一句话。
_EXEC_UNCERTAIN_SPEECH = "刚才没收到确认回执，不过我查了车辆状态，这个操作其实已经生效了。"


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

    @staticmethod
    def _exec_confirmed_by_state(r: StepResult) -> bool:
        """这一步虽然报了失败，但世界状态证实它其实生效了（M-C）。

        判据来自 executor 落的 `data["_verify"]` 保留键，通用、零领域字面量。
        """
        v = (r.data or {}).get("_verify") if isinstance(r.data, dict) else None
        return (isinstance(v, dict) and v.get("verdict") == "sat"
                and v.get("exec") == "uncertain_confirmed")

    @staticmethod
    def _verify_note(results: list[StepResult]) -> str:
        """收集本轮所有 unsat 的对账口径（同 mode 只说一次，不逐步轰炸）。"""
        modes, seen = [], set()
        for r in results:
            v = (r.data or {}).get("_verify") if isinstance(r.data, dict) else None
            if not isinstance(v, dict) or v.get("verdict") != "unsat":
                continue
            mode = str(v.get("mode") or "")
            if mode in seen:
                continue
            seen.add(mode)
            modes.append(_VERIFY_NOTE.get(mode, _VERIFY_NOTE_DEFAULT))
        return "".join(modes)

    @classmethod
    def _append_verify_note(cls, speech: str, results: list[StepResult]) -> str:
        note = cls._verify_note(results)
        if not note:
            return speech
        s = (speech or "").strip()
        if s and s[-1] not in "。！？!?":
            s += "。"
        return s + note

    async def compose(self, user_text: str, results: list[StepResult],
                      thinking: bool = False) -> dict:
        """聚合结果，返回 Final 事件结构。thinking=True 时多步合成开思考（复杂任务）。"""
        actions = self._compose_actions(results)
        cards = [r.ui_card for r in results if r.ui_card]
        follow_ups = [r.follow_up for r in results if r.follow_up]
        # 交互卡（需用户选择/操作：充电路线、顺路停靠/目的地候选）单独展示——同屏多卡会干扰选择；
        # 纯信息卡（股票/新闻/天气/搜索）可多卡同屏：合成 card_group 让 HMI 逐张渲染
        # （ui_card 是自由 Struct，不必改 proto/网关）。这样"查英伟达股价+新闻"能股票卡+新闻卡并存。
        def _card_priority(c: dict) -> int:
            # 卡片展示优先级由出卡的 Agent 自带 display_priority 声明（R2.1 P4：聚合器不再硬编码
            # 卡片类型）。0=主卡（行程/充能路线/调研报告等，多意图下须独显、不被 card_group 吞）；
            # 1=交互候选（顺路停靠/目的地二次选择）；2=普通信息卡（缺省，可多卡同屏）。
            return int(c.get("display_priority", 2))
        interactive = [c for c in cards if _card_priority(c) < 2]
        if interactive:
            ui_card = min(interactive, key=_card_priority)
        elif len(cards) > 1:
            ui_card = {"type": "card_group", "items": cards}
        elif cards:
            ui_card = cards[0]
        else:
            ui_card = None

        if not results:
            return {"speech": "抱歉，我暂时无法处理这个请求。", "actions": []}

        if len(results) == 1:
            r = results[0]
            if r.status == StepStatus.FAILED:
                if self._exec_confirmed_by_state(r):
                    return {"speech": _EXEC_UNCERTAIN_SPEECH, "actions": []}
                friendly = self._ERROR_FRIENDLY.get(r.error or "", r.error or "处理失败")
                return {"speech": f"抱歉，{friendly}。", "actions": []}
            return {
                # speech 出口统一剥 markdown（设计决策：不上渲染，见 grounding.strip_markdown_speech）
                # 对账口径在剥 markdown **之后**拼接：它是确定性文本，不该过任何改写
                "speech": self._append_verify_note(
                    strip_markdown_speech(r.speech), results),
                "actions": actions,
                "ui_card": ui_card,
                "follow_up": r.follow_up,
                "need_confirm": r.status == StepStatus.NEED_CONFIRM,
            }

        # 多步：LLM 聚合
        speech = await self._aggregate_speech(user_text, results, thinking)
        return {
            "speech": self._append_verify_note(
                strip_markdown_speech(speech), results),
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

    async def _aggregate_speech(self, user_text: str, results: list[StepResult],
                                thinking: bool = False) -> str:
        """用 LLM 把多步结果改写为连贯口语。thinking=True 时开思考（复杂跨域合成）。"""
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
            "请组织为口语回复：保留各意图实质、不要互相吞掉；"
            "用户对某问题要了 N 条结论时，这 N 条都紧扣那个问题、不拿其它意图凑数。"
        )
        try:
            return await self._llm([
                {"role": "system", "content": _AGGREGATE_SYSTEM},
                {"role": "user", "content": prompt},
            ], thinking=thinking)
        except Exception as e:
            logger.warning("Aggregation LLM failed, using raw: %s", e)
            return " ".join(r.speech for r in results if r.speech)
