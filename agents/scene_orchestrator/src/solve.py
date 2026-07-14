"""Ground·Solve：激活期把场景**具象化**成本次要下发的动作（D9）。

「聪明在编译期，可靠在运行期」的运行期那一半：LLM 在创建期产出带环境条件的策略，激活期由
本模块**确定性**求值——同一场景在 35℃ 和 5℃ 展开不同动作，但**同环境同结果、全程零 LLM**。

纯函数（env 注入），全离线可测：`solve(actions, guards, env) -> Solved`。

三态求值（v2.1 修正②，本模块的要害）：条件不是「真/假」两态，而是 **sat / unsat / unknown**。
`unknown`（读不到那个状态量）**绝不当成满足**——若按满足处理，一对互斥分支
（夏 `cabin_temp>=28` 走制冷 / 冬 `cabin_temp<15` 走制热）在缺数据时会**同时生效、后条覆盖
前条**，这是实打实的 bug。故 `when` 的 unknown → 跳过并告知（消失要透明），
`guard` 的 unknown → 降级 confirm 问用户（block 拦截只在**确凿证据**下发生）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import derive_assert

SAT, UNSAT, UNKNOWN = "sat", "unsat", "unknown"


@dataclass
class Solved:
    actions: list = field(default_factory=list)        # 本次要下发的动作（有序，不重排）
    notes: list = field(default_factory=list)          # 裁剪/跳过的诚实告知（进话术）
    confirm_notes: list = field(default_factory=list)  # guard 降级 confirm 的提示
    blocked: str = ""                                  # 非空 = guard block，诚实拒绝激活
    skipped_done: int = 0                              # 幂等跳过（已达成）的动作数


def _cmp(actual, op: str, expect) -> str:
    """三态比较。取不到/类型不可比 → UNKNOWN（绝不猜）。"""
    if actual is None:
        return UNKNOWN
    if op == "in":
        seq = expect if isinstance(expect, (list, tuple, set)) else [expect]
        return SAT if actual in seq else UNSAT
    if op in ("eq", "ne"):
        a, e = _coerce(actual, expect)
        eq = a == e
        return SAT if (eq if op == "eq" else not eq) else UNSAT
    # 数值比较：非数值 → UNKNOWN（"P" > 20 无意义，不能瞎判）
    try:
        a, e = float(actual), float(expect)
    except (TypeError, ValueError):
        return UNKNOWN
    ok = {"lt": a < e, "lte": a <= e, "gt": a > e, "gte": a >= e}[op]
    return SAT if ok else UNSAT


def _coerce(actual, expect):
    """跨类型等值比较：状态镜像里 22 与 DSL 里的 "22"、True 与 "true" 应当相等。"""
    if isinstance(actual, bool) or isinstance(expect, bool):
        return _to_bool(actual), _to_bool(expect)
    try:
        return float(actual), float(expect)
    except (TypeError, ValueError):
        return str(actual).strip().lower(), str(expect).strip().lower()


def _to_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "on", "yes")


def evaluate(cond, env: dict) -> str:
    """条件在 env 下的三态结果。cond 可以是单条 `{key,op,value}`，也可以是**条件数组**。

    数组按 **AND** 求值，且 UNSAT 优先于 UNKNOWN：
    - 任一确凿不满足 → UNSAT（有硬证据说明没做成，该报就报）
    - 否则任一读不到 → UNKNOWN（不知道 ≠ 失败，也 ≠ 已达成）
    - 全满足 → SAT
    复合断言（灯开着**且**亮度10）靠这条：只判亮度的话，灯关着而亮度值恰好是 10 时，
    幂等跳过会把开灯动作剔掉（真栈实测）。
    """
    if isinstance(cond, (list, tuple)):
        rs = [evaluate(c, env) for c in cond if c]
        if not rs:
            return UNKNOWN
        if UNSAT in rs:
            return UNSAT
        return UNKNOWN if UNKNOWN in rs else SAT
    if not isinstance(cond, dict) or not cond.get("key"):
        return UNKNOWN
    key = cond["key"]
    if key not in env:
        return UNKNOWN
    return _cmp(env.get(key), str(cond.get("op") or "eq"), cond.get("value"))


def check_guards(guards: list, env: dict, label: str = "") -> tuple[str, list]:
    """激活前置检查。返回 (blocked_reason, confirm_notes)。

    确凿不满足 → 按 mode（block=拒绝 / confirm=提示后可继续）；
    **读不到 → 一律降级 confirm**（「电量数据读不到，仍要开启吗？」）——拦截只在确凿证据下发生。
    """
    confirm_notes: list[str] = []
    for g in guards or []:
        if not isinstance(g, dict) or not g.get("key"):
            continue
        r = evaluate(g, env)
        if r == SAT:
            continue
        msg = str(g.get("message") or "").strip()
        if r == UNKNOWN:
            confirm_notes.append(f"{msg or g['key']}读不到")
            continue
        if str(g.get("mode") or "confirm").lower() == "block":
            return (msg or f"{g['key']} 不满足条件") + f"，{label or '这个场景'}先不开了", \
                confirm_notes
        confirm_notes.append(msg or f"{g['key']} 不太合适")
    return "", confirm_notes


def solve(actions: list, guards: list, env: dict, *, label: str = "") -> Solved:
    """(场景动作, guards, 环境) → 本次动作序列。执行序不重排，**只裁不排**。"""
    out = Solved()
    out.blocked, out.confirm_notes = check_guards(guards, env, label)
    if out.blocked:
        return out

    for a in actions or []:
        if not isinstance(a, dict):
            continue
        desc = _desc(a)

        # ① when：环境分支裁剪（三态；unknown 与 unsat 同样跳过，见模块 docstring）
        cond = a.get("when")
        if cond:
            r = evaluate(cond, env)
            if r == UNSAT:
                out.notes.append(f"本次跳过{desc}（当前环境不满足）")
                continue
            if r == UNKNOWN:
                out.notes.append(f"本次跳过{desc}（{cond.get('key')} 读不到）")
                continue

        # ② 幂等：期望态已达成的动作剔除（重复激活/触发撞车/「再试一次」天然只补缺失项）
        exp = a.get("assert") or derive_assert(a)
        if exp and evaluate(exp, env) == SAT:      # 键读不到 → UNKNOWN → 不剔，照常执行
            out.skipped_done += 1
            continue

        out.actions.append(a)
    return out


def unmet(solved_actions: list, env: dict) -> list[dict]:
    """Verify 对账：返回**确凿未达成**的动作（读不到的键不算失败——fail-open，绝不假警）。"""
    bad = []
    for a in solved_actions or []:
        exp = (a or {}).get("assert") or derive_assert(a or {})
        if not exp:
            continue
        if evaluate(exp, env) == UNSAT:
            bad.append(a)
    return bad


def _desc(a: dict) -> str:
    from .compiler import action_desc          # 延迟导入，避免 solve↔compiler 循环
    return action_desc(a)
