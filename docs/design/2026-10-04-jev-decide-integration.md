# Jev 判别层接入：执行计划（JV01 → JV03）

> 2026-10-04。状态：已排期，未开始实现。按用户 2026-10-04「把 jev 接入的计划也排进来」立项，并已提供 API 凭证
> （仓库外的本地文件，单行令牌；内容不进仓库、文档、日志或提交）。
> 方案来源：[研究方案](../research/2026-09-25-cockpit-agent-jev-integration-plan.md)（JV00–JV09 拆解、协议草案、失败矩阵、
> J001–J030 测试矩阵）、[实施方案 §5](2026-09-26-cockpit-agent-v2-implementation-plan.md#5-jev-工作包细化)、[路线图 §3](../roadmap.md)。
> 本文只定执行顺序、交付物、凭证与配置的落地方式和需要用户确认的节点；协议与判别设计以研究方案为准，不另抄一份。

## 1. 现状

- 研究方案已采纳并拆成 JV00–JV09，新增实现尚未开始；JV00 复用 CA2-01 的冻结基线（固定语料 20×3 已在跑）。
- 此前「无 Jev 凭证只做契约」的阻塞解除到：可以做真请求的契约 smoke 与离线评测。**不等于**可以接线上流量——
  线上 shadow 会把真实话术发给第三方，要先定数据策略（§5）。
- 实施 JV01 当天先核实外部事实（研究日 2026-09-25 的结论可能已变）：模型版本（当时固定 `jev-1.13.0`，`jev-latest` 是会移动的别名）、
  `POST /v1/systemone` 的 `model + state + questions` 请求形状、Noul / Choice / Score 的返回结构、限流与计费口径。

## 2. 排期

1. 先收完在途：借名歧义包的发布记录、本地具名目的地解析包（A/B 里两处退步修好后发布）。
2. **JV01 网关契约**：Decide RPC、响应校验、真 / 假 provider；缺省全关，部署后零外呼、业务等价。
3. **JV02 快照与绑定**：WorkingSet 只读投影、请求 / 状态 / 授权指纹绑定、脱敏、预算、有界 shadow、观测与用量记账。
4. **JV03 actionability shadow + 离线评测**：只观测不改业务结果；固定语料与标注集上出中文校准与分歧报告。
5. JV04（Skill / Exemplar 重排）、JV05（手册检索重排，先纯重构）在 JV03 的收益门过了之后再排，二者不同批上线；JV06–JV09 按研究方案依赖。

## 3. JV01 交付与验收

| 落点 | 内容 |
|---|---|
| `proto/cockpit/llm/v1/llm.proto` | 新增 `Decide` RPC 与显式消息（请求绑定、任务、预算；响应的任务结果、Noul / Choice / Score 显式 oneof、状态码、用量、时延）；旧 RPC 不动，先改 proto 再 codegen |
| `runtime/decision_contract.py` | 纯函数：任务 allowlist、payload schema、响应校验（模型 pin、问题 ID 集、类型、数值有限与范围、分布容差、整批完整性）；无网络、无领域词 |
| `llm-gateway/decision_provider.py` | 唯一外呼：HTTP 直连、不用 SDK 隐式重试；超时取阶段上限与剩余预算的较小值；429 / 529 / 超时 ⇒ unavailable / timeout 回基线；鉴权失败报配置错误、不循环 |
| `llm-gateway/decision_service.py` | 任务模板、限流、用量（缺失记 unknown，不记 0）、状态码归一；假 provider（确定性）用于测试与 off 对照 |
| SDK / Cloud 客户端 | 复用现有到 llm-gateway 的 gRPC 连接 |

- 测试：凭证缺失、全局 off 零外呼、超时、429 / 529、坏 JSON、未知问题 ID、NaN、半批响应、模型漂移；真 provider 的请求形状快照；
  一次真请求 smoke（凭证只在进程内注入，结果只记形状、状态与时延，不记原文）。
- 完成判据：全量、门禁、CI；部署后 `DECISION_ENABLED=false` 零外呼，固定语料业务等价（与上一个 release 同口径对照）。

## 4. 凭证与配置的落地方式

- 新键（名称沿用研究方案 §15 的拟定名）：`TYPESAFE_API_KEY`、`DECISION_ENABLED`、`DECISION_MODEL`、`DECISION_*_MODE`、
  `DECISION_ONLINE_BUDGET_MS` 等。全局 false 时连 shadow 也不发请求；开启要求非空 key。
- **不写进 `.env.example`**：发布闸把该文件归为运行配置契约，硬阻断且没有放行通道（开发指南「cloud 档需要的两个键」）；
  新键记在本文件与开发指南。
- 本地：离线评测与真请求 smoke 由命令在进程内把凭证读进环境变量，不打印、不落盘到仓库；写进本地根 `.env` 属于红线，要单独确认。
- 云端：llm-gateway 的环境变量在 `deploy/docker-compose.yaml` 里逐项列出 ⇒ 透传新键要走基础设施审批（`dev_stack infra-approval`）；
  云端 `.env` 写入密钥属于红线，要单独确认。先以 `DECISION_ENABLED=false` 部署（零外呼），开 shadow 另行决定。
- 数据：只发任务必需的文本投影；精确住址、电话、令牌、支付内容按研究方案 §3.2 过滤或跳过该任务；
  内部 user / session / vehicle 标识不发供应商。

## 5. 需要用户确认的节点（到点再问，不提前打包授权）

1. 本地根 `.env` 写入 `TYPESAFE_API_KEY`（或继续只在进程内注入、不写 `.env`）。
2. `deploy/docker-compose.yaml` 给 llm-gateway 透传 `TYPESAFE_API_KEY` / `DECISION_*`（基础设施审批）。
3. 云端 `.env` 写入密钥。
4. 线上 shadow 把真实用户话术（经脱敏投影）发给 typesafe.ai 的数据策略；确认前 JV03 只在合成 E2E 会话与固定语料上跑。

## 6. 风险与边界

- 中文需要单独验证（官方主训练语言是英语）；不把结构化响应当成理解正确的证明。
- 成本：研究日输入单价 $0.042 / 百万 token、输出免费，量级小，但按供应商 usage 记账；请求发出后本地超时也可能计费。
- 时延：在线新增阻塞预算研究建议 ≤500 ms；JV01–JV03 期间不进在线阻塞路径。
- 边界不变：Jev 不注册为业务 Agent、不进 HMI 的聊天模型切换、不生成执行授权、不改原话授权 / VAL / 确认 / 只响应边界；
  网络只在 llm-gateway，actionability 与 runtime 判据保持纯函数。
