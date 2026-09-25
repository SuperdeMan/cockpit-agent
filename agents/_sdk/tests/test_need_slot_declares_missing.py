"""每个 Agent 返回的 NEED_SLOT 都必须声明缺的是哪个槽（`missing_slots`）。

编排器续接补槽时只往 `missing_slots` 里的槽写答案（`engine` 补槽分支）。不声明 ⇒ 用户的回答写不进任何槽，挂起步带着原样的槽重跑，
**同一个问题原样再问一遍**，回答多少次都一样。真栈 `dabc2e2d` RS39 1/6：规划交出 `navigation.search_poi {destination: …}`，
「您想找什么类型的地点呢？」不声明 `keyword`，用户答「深圳湾公园」，得到的还是这一问；`navigation.set_place`「您想设置哪个常用地点？」同形。

这里按源码静态扫：任何调用里 `status=NEED_SLOT`（或位置参数传 `NEED_SLOT`）却没有 `missing_slots=` 的都算。
走 helper 的写法（如 mcp_bridge `_need_slot(missing)`）helper 自己那一处要带上它。只回答的能力（`response_only`）本来就不许返回 NEED_SLOT（安全红线 5）。
"""
from __future__ import annotations

import ast
from pathlib import Path

_AGENTS = Path(__file__).resolve().parents[2]


def _need_slot_without_missing(path: Path) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        keywords = {k.arg: k.value for k in node.keywords if k.arg}
        status = keywords.get("status")
        need_slot = (any(isinstance(a, ast.Name) and a.id == "NEED_SLOT" for a in node.args)
                     or (isinstance(status, ast.Name) and status.id == "NEED_SLOT")
                     or (isinstance(status, ast.Constant) and status.value == "need_slot"))
        if need_slot and "missing_slots" not in keywords:
            lines.append(node.lineno)
    return lines


def test_every_need_slot_declares_the_slot_it_asks_for():
    offenders = []
    for path in sorted(_AGENTS.rglob("*.py")):
        rel = path.relative_to(_AGENTS)
        if "tests" in rel.parts or rel.parts[0] == "_sdk":
            continue
        offenders += [f"{rel.as_posix()}:{line}" for line in _need_slot_without_missing(path)]
    assert not offenders, f"这些 NEED_SLOT 没声明 missing_slots（补槽答案写不进去、只会原样再问）：{offenders}"


def test_the_scan_sees_the_shapes_it_claims_to(tmp_path):
    """尺子自检：三种写法都认得出来，带了 missing_slots 的不误报。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "AgentResult(status=NEED_SLOT, speech='a')\n"
        "AgentResult(NEED_SLOT, 'b')\n"
        "make(status='need_slot')\n"
        "AgentResult(status=NEED_SLOT, speech='ok', missing_slots=['x'])\n",
        encoding="utf-8")
    assert _need_slot_without_missing(sample) == [1, 2, 3]
