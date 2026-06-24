"""复杂任务统一判据 + 过程区「四阶段」文案合成（脱敏）。

过程区四阶段：理解需求(understand) → 规划步骤(plan) → 执行任务(execute) → 整理结果(synthesize)。
文案**只**由编排层按步骤语义/已脱敏结果合成——**绝不**含 prompt、模型 raw reasoning、内部参数
（满足需求红线「不暴露 prompt/reasoning/敏感字段」）。
"""
from __future__ import annotations
import re

from .models import Plan, Step, StepResult, StepStatus

# 自由文本 / 调研 / 合成型意图：单独出现也算复杂（多步聚合天然也算）。
# 天气/股票/赛事等轻查询不在内——避免给秒回任务平添过程区与思考延迟（需求第 6 条）。
HEAVY_INTENTS = {
    "trip.plan", "trip.modify",
    "info.search", "info.news",
    "charging.plan",
}

# 任务领域（理解需求阶段的任务类型自然描述）。按序匹配；前缀项以 "." 结尾。
_DOMAINS = [
    (("trip.plan", "trip.modify"), "出行规划"),
    (("charging.plan", "charging.find"), "充能规划"),
    (("info.search", "info.news"), "信息调研"),
    (("info.stock",), "股价查询"),
    (("info.sports",), "赛事查询"),
    (("info.weather", "info.forecast", "info.alerts",
      "info.air_quality", "info.indices"), "天气查询"),
    (("navigation.",), "导航规划"),
    (("hvac.", "media.", "window.", "seat.", "light."), "车辆控制"),
]

# 能力名（规划步骤阶段列出，名词性）
_CAP_EXACT = {
    "trip.plan": "行程规划", "trip.modify": "行程调整",
    "info.weather": "天气查询", "info.forecast": "天气查询",
    "info.alerts": "预警查询", "info.air_quality": "空气质量", "info.indices": "生活指数",
    "info.search": "联网搜索", "info.news": "新闻汇总",
    "info.stock": "股价查询", "info.sports": "赛事查询",
    "charging.plan": "充电规划", "charging.find": "充电站查询",
}
_CAP_PREFIX = {
    "navigation.": "路线规划", "info.": "信息查询", "charging.": "充电规划",
    "trip.": "行程规划", "hvac.": "车辆控制", "media.": "媒体控制",
}

# 动作短语（执行任务阶段，动词性——「正在{label}…」要通顺）
_ACT_EXACT = {
    "trip.plan": "编排行程", "trip.modify": "调整行程",
    "info.weather": "查询天气", "info.forecast": "查询天气预报",
    "info.alerts": "查询预警", "info.air_quality": "查询空气质量",
    "info.indices": "查询生活指数",
    "info.search": "联网检索", "info.news": "汇总新闻",
    "info.stock": "查询股价", "info.sports": "查询赛事",
    "charging.plan": "规划充电", "charging.find": "查找充电站",
}
_ACT_PREFIX = {
    "navigation.": "规划路线", "info.": "查询信息", "charging.": "规划充电",
    "trip.": "编排行程", "hvac.": "调节空调", "media.": "控制媒体",
}


def make_progress(phase: str, label: str, summary: str = "",
                  status: str = "done", step_id: str = "") -> dict:
    """构造过程区事件 dict（engine/loop 共用）。内容仅来自脱敏步骤语义/结果。"""
    return {"kind": "progress", "phase": phase, "label": label,
            "summary": summary, "status": status, "step_id": step_id}


def is_complex(plan: Plan | None) -> bool:
    """统一「复杂任务」判据：自适应循环 / 多步 / 含调研型重意图。"""
    if not plan or not plan.steps:
        return False
    if getattr(plan, "complexity", "") == "adaptive":
        return True
    if len(plan.steps) >= 2:
        return True
    return any(s.intent in HEAVY_INTENTS for s in plan.steps)


def _lookup(intent: str, exact: dict, prefix: dict, default: str) -> str:
    if intent in exact:
        return exact[intent]
    for p, v in prefix.items():
        if intent.startswith(p):
            return v
    return default


def phase_label(intent: str) -> str:
    """执行任务阶段的动作短语（动词，如「查询天气」）。未知意图回「处理中」。"""
    return _lookup(intent, _ACT_EXACT, _ACT_PREFIX, "处理中")


def capability_label(intent: str) -> str:
    """规划步骤阶段的能力名（名词，如「天气查询」）。未知意图回「信息处理」。"""
    return _lookup(intent, _CAP_EXACT, _CAP_PREFIX, "信息处理")


def _domain_of(plan: Plan) -> str:
    for intents, name in _DOMAINS:
        for s in plan.steps:
            for it in intents:
                if (it.endswith(".") and s.intent.startswith(it)) or s.intent == it:
                    return name
    return capability_label(plan.steps[0].intent) if plan.steps else "综合"


def task_summary(plan: Plan) -> str:
    """理解需求阶段：自然语言任务类型。如「识别为多步骤出行规划任务」。"""
    if not plan or not plan.steps:
        return ""
    multi = len(plan.steps) >= 2
    return f"识别为{'多步骤' if multi else ''}{_domain_of(plan)}任务"


def plan_steps_summary(plan: Plan) -> str:
    """规划步骤阶段：列出各步能力名（去重保序）。如「行程规划、天气查询、充电规划」。"""
    seen, caps = set(), []
    for s in plan.steps:
        c = capability_label(s.intent)
        if c not in seen:
            seen.add(c)
            caps.append(c)
    return "、".join(caps)


def _first_sentence(text: str, limit: int = 60) -> str:
    """取首句（到句末标点），不在关键信息（如股价数字）中间硬截；过长才截。"""
    t = re.split(r"[。！？\n]", (text or "").strip(), maxsplit=1)[0].strip()
    return (t[:limit] + "…") if len(t) > limit else t


def step_summary(step: Step, result: StepResult) -> str:
    """执行任务阶段：按步骤结果合成一句脱敏自然摘要（「已…」句式）。

    优先安全计数（地点/充电点/来源数），否则取完整首句（不腰斩关键数字）。
    只用 speech（已用户向）与结构化结果里的**安全计数/名称**——不读内部参数 / prompt / reasoning。
    """
    card = result.ui_card or {}
    data = result.data or {}
    ctype = card.get("type", "") if isinstance(card, dict) else ""

    if ctype == "poi_list":
        n = len(card.get("items") or [])
        if n:
            return f"已找到 {n} 个地点"
    if ctype == "charging_route":
        n = len(card.get("stops") or [])
        if n:
            return f"已规划 {n} 个充电点"
    if ctype in ("search_result", "news_brief"):
        n = len(card.get("sources") or card.get("items") or [])
        if n:
            return f"已综合 {n} 个来源"
    if isinstance(data, dict) and isinstance(data.get("waypoint"), dict):
        nm = data["waypoint"].get("name")
        if nm:
            return f"已选定 {nm}"

    return _first_sentence(result.speech)
