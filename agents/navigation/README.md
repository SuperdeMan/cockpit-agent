# navigation Agent (core)

导航能力：POI 搜索、目的地导航（含顺路停靠/途经点）、常用地点、逆地理、定位、POI 详情。
**本目录是新增 Agent 的参考模板。**

## 能力（与 manifest.yaml / docs/conventions.md §2 对齐）
| intent | 说明 | 关键槽位 |
|---|---|---|
| `navigation.search_poi` | 定位**一个**可导航目标（多候选发现归 nearby.search）；含视觉地标描述解析 | keyword/category/near/rating_min |
| `navigation.navigate_to` | 导航到目的地；`stop_category` 顺路候选（真沿途 45% 采样）二选、`waypoint` 途经点（、/和 连写多个保序；**逐 token 三级解析**：人称→关系图谱、家/公司/学校→画像、其余 POI 搜索，未知诚实教学问）并入路线出 route_plan 卡；`arrive_by` 到达时限 ETA 判定+出发提醒；`route_pref` 路线策略（缺省消费记忆 route.* 偏好）；**接送句四段兜底**（2026-08-20 person-pickup 卡，架构 §5.2.8）：槽值判「只是个人称」→ 一跳解析；**接不着目的地**或**常用地点别名没设过**时按原话的 `接/送+人称` 再回退一次（已设置的别名不许被顶掉）；查不到给教学问（两处共用同一份话术）；命中结果 >`PICKUP_MAX_KM`（默认 100km）**不导航、报出距离反问**——接人是本地差事 | destination/stop_category/waypoint/place_address/arrive_by/route_pref |
| `navigation.reroute` | G8 增量改道**当前导航**：删/加途经点（「先去X」插首位）、换路线策略、改目的地；活动路线从 `meta.focus_active_route` 读（engine 按焦点 `active_route` 注入，来源=本 Agent 每次 navigate 写的 `_route_session` 保留键，conventions §9.1）；未点名约束保持并重出时限判定；无会话/过龄（2h）诚实降级 | remove_waypoint/add_waypoint/destination/route_pref |
| `navigation.set_place` | 设置常用地点（家/公司/学校），只记不导航；**人称守卫**：「我老婆在X上班」这类家人位置陈述只口头记下、绝不写本人画像（真栈曾被臆断成 set_place 改写用户公司地点） | place/address |
| `navigation.reverse_geocode` | 坐标→地址 | lng/lat |
| `navigation.locate` | 「我在哪」：当前位置逆地理 | — |
| `navigation.poi_detail` | POI 详情 | poi_id |

## 结构
```
manifest.yaml           能力声明（路由依据）
src/agent.py            业务实现（继承 BaseAgent，调 Provider）
src/providers/          Provider 适配层（mock/amap 可切换）
  base.py               POIProvider 接口
  mock.py               MockPOIProvider
  amap.py               AmapPOIProvider（高德真实 provider：POI/geocode/route）
  __init__.py           build_poi_provider() 工厂
main.py                 启动入口
tests/                  契约测试
Dockerfile
```

## Provider 切换
```bash
# mock（无凭证时）
POI_VENDOR=mock python main.py

# 高德（真实，AMAP_KEY 经 .env）
POI_VENDOR=amap AMAP_KEY=xxx python main.py
```

## 本地测试
```bash
PYTHONPATH=$(git rev-parse --show-toplevel):$(git rev-parse --show-toplevel)/gen/python \
  python -m pytest agents/navigation/tests -q
```

## 后续量产项
- 将 `navigate_to` action 接入真实导航 App（PoC 的 HMI 不渲染导航几何）。
