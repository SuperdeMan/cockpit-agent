"""意图的读写效果——**只读操作名的唯一声明处**（2026-08-27）。

## 为什么它在这里而不是在 `capability_meta` 里

`orchestrator/edge/capability_meta.py` 判的是**对象**是查的还是控的（`effect_of`，
从 `commands.yaml` 的 `operates` 派生）；云侧需要的是**这一步**是读还是写，
而云侧镜像既没有 `commands.yaml` 也没有 `orchestrator/edge`
（`orchestrator/cloud/Dockerfile` 只 COPY cloud/security/observability/runtime/skills）。

两边判的是同一件事的两个粒度，但「哪些操作名算只读」只该有**一份**声明。
所以那份集合搬到这里，`capability_meta` 改成从这里 import——同 `polarity` /
`cntime` / `question_shape` 的落点判据（镜像依赖闭包）。

## 粒度差异是有意的

对象级 `effect` 与意图级 `is_write_intent` 会在**混合对象**上给出不同答案：
`media` 对象声明 `effect: write`（它**能**改状态），而 `media.query` 这一步只是读。
云侧安全闸要的正是后者——它拦的是「这一步会不会改车的状态」，不是「这个对象能不能被控」。
两者不矛盾，也不许互相覆盖；一致性由 `runtime/tests/test_intent_effect.py`
的一条断言守：凡 `effect: read` 的对象，它声明的 `edge_intents` **必须**全部判成读。
"""
from __future__ import annotations

#: 只查不改的操作名。对象的 operates 全落在这里 ⇒ 该对象是 read。
READ_ONLY_OPERATES = frozenset({"query", "locate"})


def is_write_intent(intent_name: str) -> bool:
    """`<object>[.<path>].<operate>` 的最后一段不在只读集合里 ⇒ 这一步是写操作。

    判据只看**操作名**，不看对象名——这是它能零领域词地放进编排核心的前提
    （R2.1：编排核心不许出现 agent_id/intent 字面量）。
    空名字返回 False：**认不出来就不当写操作**，宁可闸少拦一次，
    也不要让一个解析失败静默地把正常请求拦掉。
    """
    tail = str(intent_name or "").rsplit(".", 1)[-1].strip().lower()
    return bool(tail) and tail not in READ_ONLY_OPERATES
