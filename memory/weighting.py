"""偏好加权与时间衰减（M2 记忆图谱 P0）。

**对症的真缺陷**：现状 `confidence` 是抽取那一刻定死的（explicit=1.0、inferred≤0.5），
重复出现不会加强、久了也不会衰减——「上个月随口说过一次想吃辣」和「每周三次点川菜」
在召回里同权，top-3 里前者还可能把后者挤掉。

**为什么不建 preference 新表**（子 RFC §2.1，泓舟 2026-07-25 拍板）：字段级对照后真缺口
只有 weight/evidence_count/half_life 三个，其余（predicate/confidence/source_turn_ids
证据溯源/superseded_by 冲突/privacy_level/occupant_id）`memory_item` 全都有；建表会推翻
2026-06-25 刚做的「两表合并为单表」决策，且 supersede/隐私分级/GDPR 级联/召回打分要重写一遍。

纯函数、无 IO、不读 env——离线可测，是本模块存在的形式要求（打分逻辑必须能被逐条钉死）。
"""
from __future__ import annotations

import math

DAY_S = 86400.0

# 起始强度按证据来源分档：用户明说的 > Agent 推断的
BASE_BY_PROVENANCE = {"user_stated": 0.6, "agent_inferred": 0.3}
BASE_DEFAULT = 0.3
# 每多一次独立证据 +0.1，封顶 +0.4——让「重复发生」能超过「说过一次」，但不至于压死显式陈述
REINFORCE_PER_EVIDENCE = 0.1
REINFORCE_CAP = 0.4
# 半衰期（天）：**显式偏好不衰减**——用户明说的偏好凭什么因为久了就不算数；
# 推断出来的才随时间沉底（没有新证据支撑的猜测本就该越来越不可信）。
HALF_LIFE_EXPLICIT = 0.0
HALF_LIFE_INFERRED = 90.0


def base_strength(provenance: str) -> float:
    return BASE_BY_PROVENANCE.get((provenance or "").strip(), BASE_DEFAULT)


def reinforcement(evidence_count: int) -> float:
    """重复证据的加成。count≤1 → 0（第一次不加成）。"""
    try:
        n = int(evidence_count or 0)
    except (TypeError, ValueError):
        n = 0
    return min(REINFORCE_CAP, REINFORCE_PER_EVIDENCE * max(0, n - 1))


def decay(age_seconds: float, half_life_days: float) -> float:
    """指数衰减因子。half_life<=0 → 1.0（不衰减）；未来时间戳按 1.0（不放大）。"""
    try:
        hl = float(half_life_days or 0)
    except (TypeError, ValueError):
        return 1.0
    if hl <= 0:
        return 1.0
    age_days = max(0.0, float(age_seconds or 0) / DAY_S)
    return math.pow(0.5, age_days / hl)


def default_half_life(provenance: str, review_status: str = "") -> float:
    """按证据来源选半衰期。显式陈述/用户确认 → 不衰减；其余按推断档。"""
    if (provenance or "").strip() == "user_stated":
        return HALF_LIFE_EXPLICIT
    if (review_status or "").strip() in ("user_confirmed", "corrected"):
        return HALF_LIFE_EXPLICIT
    return HALF_LIFE_INFERRED


def compute_weight(*, provenance: str, evidence_count: int = 1,
                   age_seconds: float = 0.0, half_life_days: float | None = None,
                   review_status: str = "") -> float:
    """weight = clamp(base + reinforce, 0, 1) × decay。结果夹在 [0, 1]。

    典型值（子 RFC §3.2）：
    - 「说过一次爱吃辣」 user_stated × count 1        → 0.60
    - 「每周三次点川菜」 agent_inferred × count 8     → 0.70（**反超前者**）
    - 半年前的一次性推断 agent_inferred × count 1     → 0.30 × 0.5^2 = 0.075（自然沉底）
    """
    hl = (default_half_life(provenance, review_status)
          if half_life_days is None else half_life_days)
    raw = min(1.0, max(0.0, base_strength(provenance) + reinforcement(evidence_count)))
    return round(max(0.0, min(1.0, raw * decay(age_seconds, hl))), 4)


def effective_confidence(item: dict, now: float = 0.0) -> float:
    """召回打分用的有效强度。

    **存量兼容是硬要求**：`weight <= 0`（M2 之前写入的所有条目、以及非 semantic 的
    情景/程序记忆）一律回退到 `confidence`——打分与今天逐字一致，不扰动已绿的旅程。
    weight > 0 时按存储值再算一次实时衰减（存的是巩固那一刻的值，读的时候可能又老了）。
    """
    try:
        w = float(item.get("weight") or 0)
    except (TypeError, ValueError):
        w = 0.0
    if w <= 0:
        try:
            return float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            return 0.0
    try:
        hl = float(item.get("half_life_days") or 0)
    except (TypeError, ValueError):
        hl = 0.0
    if hl <= 0 or not now:
        return w
    try:
        age = max(0.0, float(now) - float(item.get("valid_from") or 0))
    except (TypeError, ValueError):
        return w
    return round(w * decay(age, hl), 4)


def merge_evidence(old: str, new: str, cap: int = 32) -> str:
    """合并证据轮次 id（逗号分隔，去重保序，超上限丢最旧）。

    **必须累加而非覆盖**（子 RFC §5 证据溯源强制项）：今天 supersede 时新条目只带自己
    那一轮的 id，旧条目连同它的证据一起被标记取代——派生偏好就追不回完整证据链了。
    """
    seen, out = set(), []
    for part in list(_split(old)) + list(_split(new)):
        if part not in seen:
            seen.add(part)
            out.append(part)
    return ",".join(out[-cap:])


def evidence_count(source_turn_ids: str, fallback: int = 1) -> int:
    """从证据轮次串数出独立证据数。空串 → fallback（存量条目至少算 1 次）。"""
    n = len(list(_split(source_turn_ids)))
    return n if n > 0 else max(1, fallback)


def _split(s: str):
    for p in (s or "").split(","):
        p = p.strip()
        if p:
            yield p
