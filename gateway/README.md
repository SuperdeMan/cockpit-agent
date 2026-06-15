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
