"""三态求值（sat / unsat / unknown）——治理器的情境断言用。

**为什么不 import `agents/scene_orchestrator/src/solve.py`**：那份求值器绑着场景语义
（catalog/derive_assert），且基础设施服务不该反向依赖 Agent 包。这里自带一份 ~40 行的
等价实现，**行为一致性由契约测试保证**（同一组用例跑两边，结果必须逐条相等）。
刻意的重复：两处各 40 行、语义靠测试对齐，好过为消除重复建一个谁都要依赖的公共包。

要害仍是 UNKNOWN：读不到那个状态量时**绝不当成满足**。生产方声称「电量 18%」，
投递时刻要么证实，要么别替它说。
"""
from __future__ import annotations

SAT, UNSAT, UNKNOWN = "sat", "unsat", "unknown"

_NUMERIC_OPS = ("lt", "lte", "gt", "gte")


def _to_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "on")


def _coerce(actual, expect):
    """跨类型等值比较：镜像里的 22 与声明里的 "22"、True 与 "true" 应当相等。"""
    if isinstance(actual, bool) or isinstance(expect, bool):
        return _to_bool(actual), _to_bool(expect)
    try:
        return float(actual), float(expect)
    except (TypeError, ValueError):
        return str(actual).strip().lower(), str(expect).strip().lower()


def compare(actual, op: str, expect) -> str:
    """三态比较。取不到 / 类型不可比 → UNKNOWN（绝不猜）。"""
    if actual is None:
        return UNKNOWN
    if op == "in":
        seq = expect if isinstance(expect, (list, tuple, set)) else [expect]
        return SAT if actual in seq else UNSAT
    if op in ("eq", "ne"):
        a, e = _coerce(actual, expect)
        eq = a == e
        return SAT if (eq if op == "eq" else not eq) else UNSAT
    if op not in _NUMERIC_OPS:
        return UNKNOWN                       # 不认识的算子 → 不定罪也不放行
    try:
        a, e = float(actual), float(expect)
    except (TypeError, ValueError):
        return UNKNOWN                       # "P" > 20 无意义，不能瞎判
    return SAT if {"lt": a < e, "lte": a <= e,
                   "gt": a > e, "gte": a >= e}[op] else UNSAT


def enrich(state: dict) -> dict:
    """把复合值摊平成条件可引用的 key（location 是 dict → location.city）。

    与 `agents/scene_orchestrator/src/triggers.py::enrich_env` 同口径。
    """
    env = dict(state or {})
    loc = env.get("location")
    if isinstance(loc, dict):
        for k in ("city", "district", "name"):
            if loc.get(k):
                env[f"location.{k}"] = loc[k]
    return env


def evaluate_all(conditions, env: dict) -> str:
    """一组断言的合取。任一 UNSAT → UNSAT；任一 UNKNOWN（且无 UNSAT）→ UNKNOWN；空 → SAT。"""
    if not conditions:
        return SAT
    seen_unknown = False
    for c in conditions:
        if not isinstance(c, dict):
            return UNKNOWN
        key = c.get("key")
        if not key:
            return UNKNOWN
        r = compare(env.get(key), str(c.get("op") or "eq"), c.get("value"))
        if r == UNSAT:
            return UNSAT
        if r == UNKNOWN:
            seen_unknown = True
    return UNKNOWN if seen_unknown else SAT
