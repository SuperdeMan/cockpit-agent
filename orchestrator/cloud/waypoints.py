"""本轮途经点的合并规则（CA2-19 S3）——聚合器下发导航动作、焦点提取对齐活动路线，两处同一份。

步骤经保留键声明途经点：`data.waypoint`（单个）或 `data.waypoints`（多个）；编排不认识产生方的私有字段
（同 `_route_session` 族，conventions §9.1）。此前聚合器把充电步的途经点并进了导航动作，活动路线却只记
导航步自己盖的章——「换掉那个充电站」时路线会话里根本没有它。
"""
from __future__ import annotations

from runtime.route_stops import same_stop


def merged_waypoints(results) -> list[dict]:
    """按步骤顺序收集本轮声明的途经点（只收带名字的 dict），同一处只留一个。

    同一处（判据 `runtime.route_stops.same_stop`）出现多次时留在**后出现**的位置、字段合并：后面的步看得到前面的结果——
    「充电步推荐一个站 → 改路线那一步把它加进路线」，改路线那一步给出的是整条路线的顺序。修前不去重，
    同一个站被充电步声明一次、改路线又带一次 ⇒ 一条路线上两三个一模一样的途经点（2026-10-10 真栈 swap2）。
    """
    waypoints: list[dict] = []
    for result in results or []:
        data = getattr(result, "data", None) or {}
        if not isinstance(data, dict):
            continue
        declared = []
        single = data.get("waypoint")
        if isinstance(single, dict) and single.get("name"):
            declared.append(single)
        for item in data.get("waypoints") or []:
            if isinstance(item, dict) and item.get("name"):
                declared.append(item)
        for waypoint in declared:
            earlier = next((w for w in waypoints if same_stop(w, waypoint)), None)
            if earlier is not None:
                waypoints.remove(earlier)
                waypoint = {**earlier, **waypoint}
            waypoints.append(waypoint)
    return waypoints
