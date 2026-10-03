"""本轮途经点的合并规则（CA2-19 S3）——聚合器下发导航动作、焦点提取对齐活动路线，两处同一份。

步骤经保留键声明途经点：`data.waypoint`（单个）或 `data.waypoints`（多个）；编排不认识产生方的私有字段
（同 `_route_session` 族，conventions §9.1）。此前聚合器把充电步的途经点并进了导航动作，活动路线却只记
导航步自己盖的章——「换掉那个充电站」时路线会话里根本没有它。
"""
from __future__ import annotations


def merged_waypoints(results) -> list[dict]:
    """按步骤顺序收集本轮声明的途经点（只收带名字的 dict）。"""
    waypoints: list[dict] = []
    for result in results or []:
        data = getattr(result, "data", None) or {}
        if not isinstance(data, dict):
            continue
        single = data.get("waypoint")
        if isinstance(single, dict) and single.get("name"):
            waypoints.append(single)
        for item in data.get("waypoints") or []:
            if isinstance(item, dict) and item.get("name"):
                waypoints.append(item)
    return waypoints
