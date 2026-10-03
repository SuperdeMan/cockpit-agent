# Collector 调试面访问控制

> 状态：2026-10-03 **用户批准**（凭据由现有 E2E 密钥派生；基础设施审批核对后执行）；**已实现、待发布**（§5）。来源：路线图 R2 第 6 项「S2S 直接历史与 collector 调试面的主体授权」，
> [CA2-07 §3](2026-09-28-v2-permissioned-context-view.md) 与 [CA2-15](2026-10-02-v2-memory-identity-governance.md) 登记的未关闭边界。

## 1. 现状（2026-10-03 实测，代码与线上）

### 1.1 S2S 直接历史：已在 CA2-15 S2 收口

S2S 重建上下文时直接读 Memory（`llm-gateway/s2s/reflux.py::build_context_summary`），不经 WorkingSet。S2（`fa9cbe9e`）之后：

- 会话主体只来自 bearer 或签名的 e2e 身份（`http_server.py` 的 `session.start`），客户端声明的 `user_id` 与 token 不符直接 1008；
- 乘员只认声纹签发的证明（`_attested_occupant`），没有证明按 primary；
- 读历史用 `HISTORY_SCOPE_OWNER_ONLY`，Memory 按 (user, occupant) 过滤轮次（`MemoryStore.get_session`），传别人的 `session_id` 读不到别人的话。

各处文档仍写「S2S 直接历史未关闭」，是 S2 之前的状态，本包一并更正。

### 1.2 Collector：没有任何鉴权

`observability/collector/server.py` 的 20 个接口全部不鉴权，CORS 为 `*`。云上绑定 `127.0.0.1:8092`，经 Tailscale Serve 暴露为 tailnet 内的 8446。

| 接口 | 带出的内容 |
|---|---|
| `/api/sessions`、`/api/sessions/{id}/turns`、`/api/search`、`/api/turns/{id}`、`/api/export/{id}`、`/api/export/labels` | 所有会话的用户原话、回答、意图、标注 |
| `/api/traces`、`/api/traces/{id}`、`/stream` | 实时与历史轨迹（span 属性） |
| `/api/logs`、`/api/llm/summary`、`/api/intents/observed` | 服务日志、LLM 调用归属 |
| `/api/vehicle/state`、`/api/vehicle/observation` | 模拟车态，含位置 |
| `POST /api/turns/{id}/badcase`、`POST /api/turns/{id}/label` | 写评测标注 |
| `POST /api/debug/vehicle` | 改模拟车态（`DEBUG_VEHICLE_CONTROL` 缺省开） |
| `/healthz`、`/metrics`、`/api/agents` | 不含用户内容（agent 维度计数与健康） |

能读到的人：tailnet 里任何设备上的任何客户端。表里没有 `user_id`（`turns` 只有 `session_id`），按车主过滤要改 schema。

消费方（实测约 30 个文件）：dashboard（云上 8445 与本地 Vite）；e2e 测试 15 个以上（读轨迹、改模拟车态复位）；数据飞轮与探针脚本
（`evolve.py`、`exemplars.py`、`probe_*`）；发布验收 `deploy/cloud/verify-release.sh` 在 collector 容器里探 `wss://…:8446/stream`；
`dev_stack.py status`。HMI 与手机不调 collector。

## 2. 判据（建议）

- collector 是运维工具，合法主体是**运维者**，不是车主。除 `/healthz`、`/metrics`、`/api/agents` 外，读写接口一律要求运维凭据；
  没有凭据 401，凭据不对 403，不回显凭据。
- 运维凭据：签名短期令牌 `obs.v1.<claims>.<sig>`，HMAC 密钥由现有的 `E2E_IDENTITY_SECRET` 按独立用途派生（与 S2 声音证明同一做法：
  不新增密钥、不改 `.env`；签发与校验不能互相冒用）。有效期不超过 12 小时，由运维工具现签：e2e 运行器每次运行签一个、
  `dev_stack.py dashboard` 启动本地 dashboard 时签一个，`python scripts/obs_token.py` 打印一个供云上 dashboard 粘贴。
- HTTP 只认 `Authorization: Bearer`（不进 URL，免得进访问日志）；`/stream` 在首帧认证（浏览器不能给 WebSocket 设头），
  5 秒内没有有效认证帧即关闭。
- 不做：按车主过滤（现有消费方都是运维、要改 schema）；CORS 收窄（bearer 不是浏览器自动附带的凭据，别的网站拿不到令牌，CORS 不是这里的闸）。

## 3. 改动面

- collector：鉴权依赖、`/stream` 首帧认证、令牌模块（签发 / 校验 / 派生密钥，单一声明源）。
- 部署配置：collector 服务的环境变量加 `E2E_IDENTITY_SECRET`（compose 变更，走基础设施审批）。
- 发布验收：`collector_ws_probe.py` 在 collector 容器里用同一密钥现签令牌后再探 `/stream`（部署脚本变更）。
- 工具：一个公共辅助件（签令牌、带头、WS 认证帧），dashboard、e2e、脚本改用它；dashboard 加令牌输入（只存本页会话）。
- 文档：更正「S2S 直接历史未关闭」的说法；collector 访问规则写进 conventions。

## 4. 待决定

1. 运维凭据来源：由 `E2E_IDENTITY_SECRET` 派生（建议，不碰 `.env`）／新增独立密钥（要同时改本机与云上 `.env`）。
2. compose 与发布验收脚本的变更按基础设施 / 部署审批流程发布。

## 5. 实现与验证（本地）

- 判据与格式只有一份：`runtime/obs_access.py`（派生密钥、签发、校验、Bearer 解析、首帧认证）。collector 用一层中间件，
  开放清单之外的路由一律要凭据——以后新加的路由默认关闭；CORS 在最外层，401/403 也带 CORS 头，dashboard 才能读到拒绝并请运维者给令牌。
  `/stream` 先接受连接，5 秒内首帧认证，之前不推任何东西。
- 密钥只给 collector 一个 `E2E_IDENTITY_SECRET`，不给 `E2E_IDENTITY_ENABLED`：它派生运维密钥，不接受 e2e 身份令牌
  （钉在 `test_compose_exposes_identity_gate_only_to_edge_and_llm_gateway`）。e2e 租约的一次性密钥同样注入 collector，并等它健康再开跑。
- 运行器在身份租约之后用生效的密钥签一枚，以 `E2E_COLLECTOR_TOKEN` 传给子进程；子进程不得持有密钥（`scripts/obs_token.py`
  在运行器子进程里不回退去读密钥）。手工脚本与探针从本进程环境或仓库根 `.env` 现签。
- 消费方：dashboard（注入或粘贴的令牌，只存本页会话；被拒只问一次；首帧认证；删掉无调用方、也带不了凭据的 `exportUrl`）；
  e2e 测试经 `support.e2e.collector_headers` / `collector_auth_frame`；数据飞轮脚本与探针经 `scripts/obs_token.py`；
  HMI CDP 驱动；发布验收探针在 collector 容器里现签。凭据只发给 collector，同一个助手调别的服务时不带。
- 用例：令牌 9 种不可信情形；collector 每个非开放路由无凭据 401、伪造 403、e2e 身份令牌 403、未配置 503、拒绝带 CORS 头、
  `/stream` 三种失败都 1008 且之前不推数据；租约与 compose 的注入面；运行器签发；工具取令牌的先后与子进程不回退；
  `dev_stack dashboard` 注入且输出脱敏；dashboard 带令牌、被拒只问一次后重试、首帧认证与 1008 后丢弃令牌；发布探针先发认证帧。
