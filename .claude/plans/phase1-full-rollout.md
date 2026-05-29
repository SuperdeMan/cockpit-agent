# Phase 1 全量落地实施计划

## 背景
Phase 0 骨架已就位（10 个 Agent + 端云链路 + 6 份 proto + 完整 docker-compose）。现在按 `phase1-implementation-plan.md` 推进 Phase 1 工程化。

## 关键决策
- **分 5 批实施**，每批自检后再进下一批
- **第 1 批**：WS1 契约增强 + 安全基础设施（scopes/permission），无外部依赖，是后续所有 WS 的地基
- **第 2 批**：WS2 Registry 生产化 + WS3 Planner 引擎 + WS5 Memory 增强（M1 核心）
- **第 3 批**：WS4 端云通道（Go 网关 + proto + Python 端侧改造）
- **第 4 批**：WS6 真实能力适配层 + Agent 协作 + WS8 安全集成
- **第 5 批**：WS9 可观测 + WS10 端侧预备 + 全栈联调

---

## 第 1 批：契约增强 + 安全基础设施（WS1 部分 + WS8 基础）

### 1.1 新增 proto 文件
- `proto/cockpit/payment/v1/payment.proto` — 支付网关契约
- `proto/cockpit/channel/v1/channel.proto` — 端云双向流契约
- 更新 `proto/cockpit/common/v1/common.proto` — 补 ErrorInfo 枚举、trace_id 透传

### 1.2 安全模块 `security/`
- `security/__init__.py`
- `security/scopes.py` — scope 常量全集、trust_level 上限表、父子覆盖判定
- `security/permission.py` — PermissionEngine（编排层+执行层共用）
- `security/audit.py` — 审计事件结构化
- `security/injection.py` — 工具参数 schema 校验
- `security/tests/test_permission.py` — 单测

### 1.3 SDK 增强 `agents/_sdk/`
- `clients.py` — channel 复用（连接池）、超时配置
- `server.py` — ExecuteStream 流式贯通、异常→ErrorInfo 映射
- `testing.py` — manifest 一致性校验 + 流式断言 + 黄金用例模板

---

## 第 2 批：Registry 生产化 + Planner 引擎 + Memory 增强

### 2.1 Registry 增强 `registry/`
- `registry/store.py` — PostgreSQL 持久化 + 心跳健康探测 + 自动摘除
- `registry/server.py` — 灰度路由（按 vehicle_group/比例）

### 2.2 Planner 引擎 `orchestrator/cloud/`（核心改造）
- `orchestrator/cloud/models.py` — Plan/Step/StepResult/PlanContext/SessionState 数据结构
- `orchestrator/cloud/planning.py` — LLM DAG 规划 + schema 校验 + 重试 + 降级
- `orchestrator/cloud/executor.py` — 拓扑分层 + 并行执行 + 超时/部分失败
- `orchestrator/cloud/aggregator.py` — 单步直出 + 多步 LLM 聚合
- `orchestrator/cloud/session.py` — 多轮状态机（confirm/slot 续接，Redis 持久）
- `orchestrator/cloud/engine.py` — 编排主循环（串联上述模块）
- `orchestrator/cloud/planner.py` — 改造为 PlanBuilder 适配器
- `orchestrator/cloud/server.py` — 适配新 engine
- `orchestrator/cloud/tests/` — 拓扑/状态机/聚合单测

### 2.3 Memory 增强 `memory/`
- `memory/store.py` — 画像导出/删除接口（合规）；scope 权限控制

---

## 第 3 批：端云通道（WS4）

### 3.1 Go Cloud Gateway `gateway/cloud/`
- `gateway/cloud/main.go` — EdgeCloudChannel bidi 双向流
- `gateway/cloud/auth.go` — JWT 鉴权 + mTLS 占位
- `gateway/cloud/idempotency.go` — correlation_id 去重（Redis）
- `gateway/cloud/outbox.go` — 可靠投递（seq + Ack + 补发）
- `gateway/cloud/link.go` — 解复用→Planner + 主动下发

### 3.2 Go Edge Gateway `gateway/edge/`
- `gateway/edge/main.go` — 改造为 ChannelClient bidi 长连 + 心跳 + 重连
- `gateway/edge/auth.go` — token 管理

### 3.3 端侧改造 `orchestrator/edge/`
- `orchestrator/edge/channel_state.py` — LinkState 健康状态机
- `orchestrator/edge/degrade.py` — 降级矩阵
- `orchestrator/edge/server.py` — 路由前查 LinkState；云端回流 action 分发（车控→VAL）
- `orchestrator/edge/cloud_client.py` — 改造为 ChannelClient 适配器

---

## 第 4 批：真实能力适配 + Agent 协作 + 安全集成

### 4.1 Provider 适配层（以 navigation 为模板）
- `agents/navigation/src/providers/base.py` — POIProvider 接口
- `agents/navigation/src/providers/mock.py` — MockPOIProvider
- `agents/navigation/src/providers/__init__.py` — build_provider()
- `agents/navigation/src/agent.py` — 改造为调 Provider
- 其余 Agent 按模板接 Provider（可并行）

### 4.2 支付网关 `payment-gateway/`
- `payment-gateway/main.py` — gRPC 服务 Authorize/Capture/Cancel
- `payment-gateway/store.py` — 订单存储（Redis/内存）
- `agents/food_ordering/src/agent.py` — 接入支付网关
- `agents/parking_payment/src/agent.py` — 接入支付网关

### 4.3 Agent 协作 SDK
- `agents/_sdk/agent_client.py` — AgentClient + 护栏（深度/环/权限/超时）
- `agents/_sdk/base.py` — 注入 self.agents
- `agents/trip_planner/src/agent.py` — 改造为子规划者

### 4.4 安全集成
- `orchestrator/cloud/permissions.py` — 接 PermissionEngine
- `orchestrator/edge/val.py` — 执行层权限校验 + 门控表增强
- `llm-gateway/server.py` — 内容审核钩子

---

## 第 5 批：可观测 + 端侧预备 + 全栈联调

### 5.1 可观测（WS9）
- `observability/tracing.py` — OTel trace 贯通（trace_id 透传规范）
- `observability/metrics.py` — 核心指标（意图准确率/时延/成功率/降级率）
- `observability/logging.py` — 结构化日志 + 敏感脱敏
- 各服务接入 OTel middleware

### 5.2 端侧预备（WS10）
- `orchestrator/edge/fast_intent.py` — 阈值可热更新、意图白名单可配置
- 端侧 Agent 模块化拆分（车控/媒体独立）

### 5.3 评测与 CI
- `test/scenarios/` — 端到端场景回归集
- CI 配置（GitHub Actions / GitLab CI）

---

## 自检清单（每批完成后）
- [ ] 全部 Python 文件 `py_compile` 通过
- [ ] `test/smoke_edge.py` 13/13 通过（端侧逻辑不回归）
- [ ] 新增模块有对应 `tests/` 且通过
- [ ] 新增 proto 有对应 codegen 配置
- [ ] docker-compose 更新（如有新服务）
- [ ] conventions.md 更新（如有新 intent/scope/端口）

## 依赖关系
```
第1批(基础) ──→ 第2批(编排核心) ──→ 第4批(能力+安全)
     │                                      ↑
     └──→ 第3批(通道) ──────────────────────┘
                                    ──→ 第5批(可观测+联调)
```
