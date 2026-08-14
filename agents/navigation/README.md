# navigation Agent (core)

导航能力：POI 搜索、目的地导航（含顺路停靠/途经点）、常用地点、逆地理、定位、POI 详情。
**本目录是新增 Agent 的参考模板。**

## 能力（与 manifest.yaml / docs/conventions.md §2 对齐）
| intent | 说明 | 关键槽位 |
|---|---|---|
| `navigation.search_poi` | 定位**一个**可导航目标（多候选发现归 nearby.search）；含视觉地标描述解析 | keyword/category/near/rating_min |
| `navigation.navigate_to` | 导航到目的地；`stop_category` 顺路候选二选、`waypoint` 途经点并入路线出 route_plan 卡 | destination/stop_category/waypoint/place_address |
| `navigation.set_place` | 设置常用地点（家/公司/学校），只记不导航 | place/address |
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
