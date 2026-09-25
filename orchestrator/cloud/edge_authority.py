"""需确认的车端命令：写操作的**方向**以车端的确定性解析为准（评审四轮 §5.5 b，2026-09-25）。

真栈 `5ca289c7` RS34：新会话里「关闭后备箱」被规划成 `trunk.open`（能力按 (agent, intent) 排序，trunk.close / trunk.open 编号相邻，差一位），
一句「好的」就开了后备箱。而车端快路径早就确定性地解出了 `trunk.close`——需确认的命令不在车端秒回、整句上云，
车端在这条路上把它的解析盖章成 `_edge_confirm`（客户端同名键在车端入口剥掉）。

口径窄到只剩一种形态（collector 全量历史 266 轮：264 轮一致；**方向冲突 1 轮就是 RS34 那一趟，车端对**；「计划里没有这个对象」1 轮
是问句「后备箱能放几个行李箱」被车端误判成 `trunk.open`、规划的 `manual.query` 才对）：

- 只认车端在**需确认那条路上**盖的章，不认泛化的 `_edge_nlu`（那份照旧只作观测、不进 prompt）；
- 只动计划里**同一个对象**的**写步**、且方向与车端不同的那一步——计划里没有这个对象就一个字不动（问句那一轮正是这样）；
- 读步（`query`）不会被改成写，其余步骤不动；改完仍走确认，确认问句念出的是改过之后的动作（§5.5 a）。

判据零领域词：对象 = intent 去掉最后一段，读写 = `runtime.intent_effect.is_write_intent`。
"""
from __future__ import annotations

from runtime.intent_effect import is_write_intent


def _object_of(intent: str) -> str:
    return str(intent or "").strip().rpartition(".")[0]


def direction_conflicts(steps: list, edge_intent: str) -> list[int]:
    """计划里与车端解析**同一个对象**、都是写、但不是同一条 intent 的步（下标，按计划顺序）。"""
    edge_intent = str(edge_intent or "").strip()
    target = _object_of(edge_intent)
    if not target or not is_write_intent(edge_intent):
        return []
    return [index for index, step in enumerate(steps or [])
            if _object_of(getattr(step, "intent", "")) == target
            and is_write_intent(getattr(step, "intent", ""))
            and str(getattr(step, "intent", "")).strip() != edge_intent]
