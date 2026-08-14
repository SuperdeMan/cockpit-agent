# parking-payment Agent (ecosystem / third_party)

停车缴费：查费 + 缴费（只做交易面；**停车场发现归 nearby**）。

| intent | 说明 |
|---|---|
| `parking.query_fee` | 查询停车费（只读，不产生支付动作） |
| `parking.pay` | 缴费（`require_confirm`，经 payment-gateway，契约 conventions §9.17） |

> `parking.find` 已于 2026-07-07 停用删除：它是与 nearby 重复的 mock（假空位数据），
> 「找停车场」由 `nearby.search`（类目「停车场」）承接；回归测试钉死本 Agent 不再处理该 intent。

## Provider
`providers/` 目录：`ParkingProvider` 接口 + `MockParkingProvider`。切换：`PARKING_VENDOR=etcp`。

## 后续量产项
- 实现 EtcpProvider（当前默认 MockParkingProvider）。
- 接入车牌识别与真实车辆绑定。
