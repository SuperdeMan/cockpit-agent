# Gateway 接入层（Go）

| 服务 | 角色 | 端口 | 上游 |
|---|---|---|---|
| `edge/` Edge Gateway | HMI 接入（WebSocket/REST） | 8090 | Edge Orchestrator (gRPC) |
| `cloud/` Cloud Gateway | 端云边界代理 | 8080 | Cloud Planner (gRPC) |

## Edge Gateway WebSocket 协议
- 连接：`ws://<host>:8090/ws`
- 上行：`{"text": "打开空调26度", "session_id": "abc"}`
- 下行（流式，多条）：
  - `{"type":"speech_delta","delta":"..."}`
  - `{"type":"action","action":{"type":"vehicle.control","payload":{...},"require_confirm":false}}`
  - `{"type":"final","speech":"...","actions":[...],"follow_up":"...","need_confirm":false}`

## 构建
依赖 `gen/go`（先 `make proto`）。`go build ./gateway/...` 或经各自 Dockerfile。

## 待办
- TODO(Phase1): Edge—语音流接入、会话保持、本地限流；Cloud—设备证书+token 鉴权、会话路由、限流、审计。
