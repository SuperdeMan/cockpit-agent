# parking-payment Agent (ecosystem / third_party)

停车缴费：停车场查找 + 缴费。

| intent | 说明 |
|---|---|
| `parking.find` | 查找附近停车场（空位/价格） |
| `parking.pay` | 缴费（`require_confirm`，支付前二次确认） |

## Provider
`providers/` 目录：`ParkingProvider` 接口 + `MockParkingProvider`。切换：`PARKING_VENDOR=etcp`。

## 后续量产项
- 实现 EtcpProvider（当前默认 MockParkingProvider）。
- 接入车牌识别与真实车辆绑定。
