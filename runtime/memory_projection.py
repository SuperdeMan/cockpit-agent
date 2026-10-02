"""CA2-15 S2：没认出的声音只读普通偏好（用户口径 A，2026-10-02）。

一份判据，Memory 对定向读（``GetContext`` 的 scope、``Recall`` 的谓词前缀）与非定向召回同样执行：
可见的只有 ``kind=semantic``、``privacy_level=normal``、不是身份 / 人物 / 地点 / 健康 / 习惯类、
也不是关于别人的（``subject`` 为空）条目——即口味、舒适、路线、音乐这类偏好。人称地点解析与关系查询
在投影下一律返回空。会话历史不投影（座舱里当面说出来的对话）。

投影只决定这一轮能读到哪些个性化记忆，不授予、也不收回任何动作权限。
"""
from __future__ import annotations

from typing import Mapping

NONE = ""
NORMAL_ONLY = "normal_only"
#: 服务端自有 meta 键：云端按声音证明算出本轮投影，Agent 读记忆时原样带给 Memory。
META = "memory_projection"

PRIVATE_SCOPES = frozenset({
    "profile.places", "profile.person", "profile.identity", "profile.health", "profile.habit"})
PRIVATE_PREDICATES = ("identity.", "person.", "place.", "health.", "habit.", "routine.")


def normalize(value) -> str:
    """未知取值按最严处理：只有空串表示不投影。"""
    if value in (None, NONE):
        return NONE
    return NORMAL_ONLY


def scope_visible(scope: str, projection: str) -> bool:
    if normalize(projection) == NONE:
        return True
    scope = (scope or "").strip()
    return scope not in PRIVATE_SCOPES and not scope.startswith("episodic")


def visible(item: Mapping, projection: str) -> bool:
    if normalize(projection) == NONE:
        return True
    predicate = str(item.get("predicate") or "")
    return (
        (item.get("kind") or "semantic") == "semantic"
        and (item.get("privacy_level") or "normal") == "normal"
        and not predicate.startswith(PRIVATE_PREDICATES)
        and scope_visible(str(item.get("scope") or ""), projection)
        and not str(item.get("subject") or "").strip()
    )
