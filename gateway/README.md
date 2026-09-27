# Gateway 接入层（Go）

| 服务 | 角色 | 端口 | 上游 |
|---|---|---|---|
| `edge/` Edge Gateway | HMI 接入（WebSocket/REST） | 8090 | Edge Orchestrator (gRPC) |
| `cloud/` Cloud Gateway | 端云边界代理 | 8080 | Cloud Planner (gRPC) |

## Edge Gateway WebSocket 协议
- 连接：`ws://<host>:8090/ws`
- 上行：`{"text": "打开空调26度", "session_id": "abc", "meta": {"trace_id": "可选"}}`
- 下行（流式，多条）：
  - `{"type":"speech_delta","delta":"..."}`
  - `{"type":"action","action":{"type":"vehicle.control","payload":{...},"require_confirm":false}}`
  - `{"type":"final","speech":"...","actions":[...],"follow_up":"...","need_confirm":false}`

CA2-04 的 final 可带 `result_bundles`（v1）：同一任务的完整答案、状态和卡片引用。
它不携带执行授权；actions、operation_id、确认策略继续使用原字段。
旧客户端可忽略增量，详见 [结果契约](../docs/design/2026-09-27-v2-result-bundle.md)。

## 构建
依赖 `gen/go`（先 `make proto`）。`go build ./gateway/...` 或经各自 Dockerfile。

## 已落地
- Edge Gateway：HMI WebSocket 接入、事件流转发、端云长连接复用、心跳与重连。
- Cloud Gateway：按 `vehicle_id + correlation_id` 配对 `DispatchToEdge`，并校验请求车辆与握手车辆绑定。
- 云端中枢可将计划中的 edge step 下发到指定车辆，结果回流后继续后续 DAG/T2 步骤。
- Edge Gateway 原样透传 `meta.trace_id`，供 edge/cloud/collector 串联同一请求链路。

## 待办
- Cloud Gateway 多实例下的车辆会话亲和/一致性路由。
- 量产设备证书与 token 鉴权、本地/云端限流和网关审计持久化。
- 当前 ASR/TTS 通过独立 HTTP 音频代理接入，不在 WebSocket 中传输原始音频流。

## 车辆来源与会话隔离（CA2-06）

`vehiclestate/` 按受控公钥验证观测并维护逐信号有效期。Cloud channel token 在签名模式绑定车辆，连接不能二次 Hello 换车。
Edge WS 先发 session_identity，再发带 projection_epoch/revision 的完整车态；车辆/用户定向广播，过期也主动推送。
[版本 2 契约和混部边界](../docs/design/2026-09-27-v2-vehicle-state-and-simulation.md)；认证模拟来源不等于 OEM 身份。
