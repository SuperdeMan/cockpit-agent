"""补槽答案**替换**了挂起步里原来的槽值——之后这一轮不许再追那个旧值（评审四轮，`4438ea6b` 真栈 RS39，2026-09-25）。

真栈那一趟：「导航去云岚国际中心」被声明成 adaptive，T2 循环的 navigate_to 找不到 ⇒ 追问目的地并挂起；用户答「深圳湾公园」，
续接导航之后循环按**模型在第 1 轮写下的 goal**（「解析云岚国际中心为可导航的具体地点后启动导航」）再规划一次，又去搜云岚国际中心、
再挂一条追问——导航已经发出去了，话术却在问旧地点。goal 是模型写的，补槽答案是用户说的：**用户在追问里给出的值胜过模型写下的锚**。

判据（这一份，零领域词）：挂起步在续接前，某个待补槽里本来就有值（`before`，Agent 用不了它才追问），用户的答案 `after` 与它不同，
且**两者互不包含**（「万象城」→「深圳湾万象城」是细化，不是替换）⇒ `before` 被 `after` 替换。消费两处：
① goal 里逐字出现的 `before` 换成 `after`（循环的锚跟着用户走；goal 里没出现就不动）；
② 这一次续接里，循环再规划出的、槽值里仍含 `before` 的步确定性丢掉，依赖它们的下游一起丢——与「被拒诉求不许换能力再试」同一种做法，
提示只是弱约束。
"""
from __future__ import annotations


def superseded_values(before_slots: dict, answers: dict) -> dict[str, str]:
    """`{旧值: 用户给的新值}`：只收「原来有值、答案不同、两者互不包含」的槽。"""
    out: dict[str, str] = {}
    for name, answer in (answers or {}).items():
        before = str((before_slots or {}).get(name) or "").strip()
        after = str(answer or "").strip()
        if before and after and before not in after and after not in before:
            out[before] = after
    return out


def rewrite_goal(goal: str, superseded: dict[str, str]) -> str:
    """goal 里逐字出现的旧值换成用户的新值；没出现就原样返回。"""
    text = str(goal or "")
    for before, after in (superseded or {}).items():
        text = text.replace(before, after)
    return text


def drop_superseded_steps(steps: list, superseded: dict[str, str]) -> tuple[list, list[str]]:
    """丢掉槽值里仍含被替换旧值的步，以及（传递地）依赖它们的步。返回 (保留的步, 丢掉的步 id)。"""
    if not superseded:
        return list(steps), []
    stale = [str(before) for before in superseded]
    dropped: set[str] = set()
    for step in steps:
        values = [str(v) for v in (getattr(step, "slots", None) or {}).values() if v is not None]
        if any(before in value for before in stale for value in values):
            dropped.add(step.id)
    changed = bool(dropped)
    while changed:
        changed = False
        for step in steps:
            if step.id not in dropped and set(getattr(step, "depends_on", None) or []) & dropped:
                dropped.add(step.id)
                changed = True
    return [s for s in steps if s.id not in dropped], [s.id for s in steps if s.id in dropped]
