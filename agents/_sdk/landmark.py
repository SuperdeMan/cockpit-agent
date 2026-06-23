"""视觉地标描述 → 地图可检索的正式 POI 名解析（导航/充电等多 Agent 共用）。

用户常用外观/造型描述指代建筑（“像笋的建筑”）。地图 POI 库按**正式注册名**检索，
故须经 LLM 把视觉描述转成地图可检索的正式名（如“中国华润大厦”而非俗称“华润春笋大厦”）——
否则高德等会对俗称返回**同位置的邻近无关 POI**（搜“华润春笋大厦”→ V东滨店）。

约定：LLM 只产出“候选正式名”，由调用方用地图验证后才采用；模型不得直接决定导航目的地。
"""
from __future__ import annotations
import json
import logging

_DEFAULT_LOGGER = logging.getLogger("agent.landmark")

# 视觉地标 marker：用外观/造型指代建筑（作用于文本本身，不要求“导航/去”动词前缀）
_LANDMARK_MARKERS = ("像", "一样", "造型", "外形", "形状", "船型", "笋", "地标", "建筑")

_SYSTEM = (
    "你是车载导航语义解析器。用户会用外观、造型等视觉描述来指代地标建筑。\n"
    "你的任务：根据描述，推断出 1-3 个最可能的中国地标/建筑名称，用于**地图 POI 搜索**。\n\n"
    "常见映射（参考，输出地图可检索的正式名）：\n"
    "- 像船的建筑 → 东方之门（苏州）\n"
    "- 像笋的建筑 → 中国华润大厦（深圳，俗称华润春笋/春笋大厦）\n"
    "- 像鸟巢 → 国家体育场\n"
    "- 像裤衩 / 大秋裤 → 中央电视台总部大楼\n"
    "- 像飞碟 → 深圳宝安国际机场卫星厅\n\n"
    "规则：\n"
    "1. 输出**地图 POI 库可检索的正式注册名**（如『中国华润大厦』而非俗称『华润春笋大厦』）；"
    "可在数组里同时给正式名与俗称，**正式名排第一**\n"
    "2. 尽量给 2-3 个候选，按『最可能被地图收录』排序\n"
    "3. 如描述含城市名，候选应位于该城市\n"
    "4. 只输出 JSON 字符串数组，不要解释。若无法判断，输出 []"
)


def is_landmark_description(text: str) -> bool:
    """文本是否像视觉地标描述（不要求“导航/去”动词前缀）。"""
    normalized = (text or "").strip()
    return any(marker in normalized for marker in _LANDMARK_MARKERS)


async def landmark_candidates(llm, description: str, *, logger=None) -> list[str]:
    """把视觉化地标描述转成 1-3 个地图可检索的正式 POI 名候选（不接受模型直接导航）。"""
    log = logger or _DEFAULT_LOGGER
    try:
        raw = await llm.complete([
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": description},
        ], temperature=0.0, max_tokens=120)
    except Exception as e:
        log.warning("landmark resolution unavailable: %s", e)
        return []

    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0].strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        values = json.loads(raw[start:end + 1])
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(values, list):
        return []

    candidates: list[str] = []
    for value in values:
        candidate = value.strip() if isinstance(value, str) else ""
        if candidate and candidate not in candidates and len(candidate) <= 80:
            candidates.append(candidate)
    return candidates[:3]


def name_matches(candidate: str, poi_name: str) -> bool:
    """地图返回的 POI 名是否与候选地标名实质匹配。

    高德对**非官方名**会返回同位置的邻近无关 POI（搜“华润春笋大厦”→“V东滨店”）。
    用名字重合度过滤掉这类“挂羊头”的结果：任一方向包含，或有 ≥2 字公共子串即算匹配。
    """
    a = (candidate or "").strip()
    b = (poi_name or "").strip()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    for i in range(len(a) - 1):
        if a[i:i + 2] in b:
            return True
    return False
