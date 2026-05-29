# parking-payment Agent (ecosystem / third_party)

停车缴费：停车场查找 + 缴费。

| intent | 说明 |
|---|---|
| `parking.find` | 查找附近停车场（空位/价格） |
| `parking.pay` | 缴费（`require_confirm`，支付前二次确认） |

## 待办
- TODO(Phase1): 接真实停车/无感支付平台；车牌识别绑定；支付经统一支付网关。
- TODO: 补契约测试（参考 agents/food_ordering/tests）。
