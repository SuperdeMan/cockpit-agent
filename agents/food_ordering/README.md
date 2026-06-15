# food-ordering Agent (ecosystem / third_party)

点餐：餐厅搜索 + 预订。**交易类生态 Agent 范本**（第三方信任级、支付权限、二次确认）。

| intent | 说明 |
|---|---|
| `food.search_restaurant` | 按菜系/位置/评分/价格搜索 |
| `food.reserve` | 预订（`require_confirm`，涉及费用必须二次确认） |

## 安全要点
- `trust_level: third_party` → 默认禁用车控、精确位置、摄像头/麦克风。
- 预订不直接下单，返回 `NEED_CONFIRM` + `require_confirm` 动作，由上层确认后执行。
- 支付经统一 `payment-gateway` 服务（Agent 不持凭证）。

## Provider
`providers/` 目录：`RestaurantProvider` 接口 + `MockRestaurantProvider`。切换：`RESTAURANT_VENDOR=dianping`。

## 后续量产项
- 实现 DianpingProvider（当前默认 MockRestaurantProvider）。
