# nearby Agent —— 周边发现（ecosystem / third_party）

基于高德 POI 2.0 的**富数据周边搜索 + 详情增强**。发现归本 Agent、出行归 navigation。
（前身为 mock 的 `food-ordering`；重构见 `docs/design/2026-07-05-nearby-discovery-redesign.md`。）

| intent | 说明 |
|---|---|
| `nearby.search` | 搜周边地点（餐饮/酒店/景点/影院/停车/充电/加油等），支持菜系/品牌/评分/人均/排序过滤 |
| `nearby.detail` | 详情增强：评分、人均、电话、营业时间、特色、图片、地址 |
| `nearby.order` | 点单/订位（`require_confirm`，**诚实预留桩**——当前仅少数连锁支持，给电话+导航兜底，不假下单） |

## 与 navigation 的边界
- **发现/详情/比较**（「附近有什么好吃的」「这家怎么样」「附近的酒店/影院」）→ nearby。
- **出行动作**（导航/带我去/回家/顺路/途经/我在哪）→ navigation。
- nearby 经 manifest `route_hints` 声明式接管发现说法，`guard` 让出行动词归 navigation；不改编排核心。

## Provider
`providers/`：`PlaceProvider` 接口 + `Place`（富字段）+ `MockPlaceProvider` + `AmapPlaceProvider`。
切换：`POI_VENDOR=amap` 且 `AMAP_KEY` 非空 → 真实高德，否则 mock；真实失败自动降级 mock（不击穿主链）。
高德坑：坐标 `lng,lat`（经度在前）、HTTP 200 但 `status!="1"` 即失败、空字段返回 `[]`。

## 安全要点
- `trust_level: third_party` → 沙箱（`read_only` + 出站 http-proxy），禁用车控/摄像头/麦克风。
- 发现/详情**只读**；`payment.invoke` 仅 `nearby.order` 预留桩用，真实交易走统一 `payment-gateway`。
- 评分/人均等高德常缺字段**不编造**，话术按「是否已知」自适应。

## 后续（见设计文档 P1/P2）
- P1：营业中过滤、评分排序、图片渲染、「导航去第 N 个」handoff 全链路。
- P2：真实点单/订位适配器（麦当劳/瑞幸风格 pre-order）替换桩。
