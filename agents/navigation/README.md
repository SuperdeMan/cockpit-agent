# navigation Agent (core)

导航能力：POI 搜索、目的地导航。**本目录是新增 Agent 的参考模板。**

## 能力
| intent | 说明 | 关键槽位 |
|---|---|---|
| `navigation.search_poi` | 搜索 POI | keyword/category/rating_min |
| `navigation.navigate_to` | 导航到目的地 | destination |

## 结构
```
manifest.yaml           能力声明（路由依据）
src/agent.py            业务实现（继承 BaseAgent，调 Provider）
src/providers/          Provider 适配层（mock/real 可切换）
  base.py               POIProvider 接口
  mock.py               MockPOIProvider
  __init__.py            build_poi_provider() 工厂
main.py                 启动入口
tests/                  契约测试
Dockerfile
```

## Provider 切换
```bash
# mock（默认）
POI_VENDOR=mock python main.py

# 高德（需实现 AmapPOIProvider）
POI_VENDOR=amap AMAP_KEY=xxx python main.py
```

## 本地测试
```bash
PYTHONPATH=$(git rev-parse --show-toplevel):$(git rev-parse --show-toplevel)/gen/python \
  python -m pytest agents/navigation/tests -q
```

## 后续量产项
- 实现 AmapPOIProvider / BaiduPOIProvider（当前默认 MockPOIProvider）。
- 将 `navigate_to` action 接入真实导航 App。
