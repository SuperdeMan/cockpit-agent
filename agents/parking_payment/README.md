# parking-payment Agent (ecosystem / third_party)

停车缴费：停车场查找 + 缴费。

| intent | 说明 |
|---|---|
| `parking.find` | 查找附近停车场（空位/价格） |
| `parking.pay` | 缴费（`require_confirm`，支付前二次确认） |

## Provider
`providers/` 目录：`ParkingProvider` 接口 + `MockParkingProvider`。切换：`PARKING_VENDOR=etcp`。

## 待办
- TODO(Phase1): 实现 EtcpProvider（接真实停车/无感支付平台）。
- TODO(Phase1): 车牌识别绑定。
