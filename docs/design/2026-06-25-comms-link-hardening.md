# 通讯链路量产级加固方案

> 状态：已批准，实施中（branch `feature/comms-hardening`，2026-06-25 起）。
> 本文是设计与实施依据；落地进展见 `AGENTS.md`。

## Context（为什么做这件事）

实测中频繁遇到**断连、服务超时、无响应**。逐条读完所有通讯链路后的结论：

**这不是若干孤立 bug，而是一组贯穿每条链路的系统性缺失。** 现有代码在"功能正确"上很细（F13/F15/F16/F18 等修复、熔断器、重连、幂等都有雏形），但"连接长期健壮"这一层是空的。四类根因：

1. **全链路 gRPC 没有 keepalive**（Go 客户端 3 处、Python channel 全部、所有 Python server 全部）。容器/NAT/代理会静默掐掉空闲连接，两端都不知道，直到下次请求挂住 → 「断连/无响应」头号根因，也是「单服务 recreate 换 IP 需重启依赖方」反复踩坑的底层原因。
2. **所有服务没有优雅停机**：清一色 `wait_for_termination()`，无 SIGTERM 处理。每次 `docker compose up --build` 重建容器，在途请求被硬杀 → 「报错/无响应」。
3. **HMI 前端遇断线静默吞消息**（`readyState!==OPEN` 直接 `return`），且**没有请求超时**——后端不回 `final`，气泡永久"思考中" → 最直观的「无响应」。重连固定 1.5s、无退避、无半开探测。
4. **熔断器是死代码 + 超时预算不成级联**：`circuit.py` 写好却没接进 `dispatch.py`，挂掉的 Agent 每次吃满 10–20s 超时；LLM 上游 120s 超时 > 网关 90s 端到端窗口，必中途取消 → 「超时」。

目标：以量产标准加固**所有**链路，消灭上述三类症状，且**不破坏现有 884 passed 测试与端侧秒回体验**。不在本次范围：mTLS/真实证书（PoC 仍用 insecure，单列为唯一遗留生产缺口）、DB schema 变更、密钥。

---

## 链路全景与现状诊断（证据）

| # | 链路 | 关键文件 | 现状缺陷 |
|---|---|---|---|
| 1 | HMI ⇄ 边网关 (WebSocket) | `hmi/src/App.tsx`、`gateway/edge/main.go` | 断线时 `dispatch()` 静默 `return` 丢消息(App.tsx:279)；**无请求超时**→气泡永久"思考中"；重连固定 1.5s 无退避(App.tsx:102)；无半开/陈旧探测。服务端 15s WS Ping 已有(✓) |
| 2 | 边网关 ⇄ 边编排器 (gRPC) | `gateway/edge/main.go:465` | `NewClient` 无 keepalive |
| 3 | 边编排器 ⇄ 云网关 (bidi) | `orchestrator/edge/cloud_client.py` | 每请求新建 bidi 流(非持久长连)；channel 无 keepalive |
| 4 | 边网关(ChannelClient) ⇄ 云网关 | `gateway/edge/main.go:161` | 重连/ping/missedPong 已有(✓)；但 `NewClient` 无 keepalive；pending 满则**静默丢事件**(main.go:263 `default:`) |
| 5 | 云网关 ⇄ 云 Planner (gRPC) | `gateway/cloud/main.go:288` | client `NewClient` 无 keepalive；server 有 EnforcementPolicy 但无 ServerParameters(不主动发 ping) |
| 6 | 云 Planner ⇄ Agents (gRPC) | `orchestrator/cloud/clients.py`、`dispatch.py` | channel 复用(✓ F15)但无 keepalive；**熔断器未接线**(死代码)→单 Agent 挂每次吃满超时；云 Agent 异常 **re-raise** 可炸整条计划(dispatch.py:232) |
| 7 | Agent ⇄ Agent (gRPC) | `agents/_sdk/agent_client.py:139` | **每次调用新建 channel 且从不关闭**(连接/fd 泄漏)；`fork()` 丢 `parent_meta`；超时捕获类型错(`asyncio.TimeoutError` 对 grpc.aio 无效) |
| 8 | Planner/Agents ⇄ LLM 网关 (gRPC) | `clients.py`(两处) | channel 复用但无 keepalive；UNAVAILABLE 重连重试 1 次(✓) |
| 9 | LLM 网关 ⇄ 上游 LLM/ASR/TTS (HTTP) | `llm-gateway/providers.py` | **每次调用新建 `httpx.AsyncClient`**(无连接池复用)；timeout 60–120s **超过**网关 90s 窗口(必中途取消)；流式无 stall/idle 超时 |
| 10 | Agent ⇄ 外部 Provider (HTTP) | `agents/_sdk/http.py` | 超时/重试/熔断已做好(✓)，仅需参数与预算对齐 |
| 11 | 各服务 ⇄ Registry (gRPC) | `agents/_sdk/server.py`、`registry/health.py` | 周期重注册(✓)；channel 无 keepalive |
| 12 | 全部 Python gRPC server | `*/main.py`(7 个) | 无 server keepalive、无 `maximum_concurrent_rpcs`、**无优雅停机**(SIGTERM 硬杀在途请求) |
| 13 | 依赖连接 (Redis/PG/NATS) | `session.py`、`memory/pg_store.py`、`observability/events.py` | Redis `from_url` 无 socket_timeout/keepalive/health_check；asyncpg pool 无 command_timeout/lifetime；events NATS `max_reconnect_attempts=0` 可疑 |

---

## 系统性解决方案（8 主题）

- **Theme 1 — 全链路 gRPC keepalive + 共享拨号/建服务工厂**（覆盖 #2/4/5/6/7/8/11/12）。新建 `runtime/grpcio.py`：`aio_channel` / `aio_server` / `run_aio_server`，统一 keepalive（每 20s ping、空闲也 ping）+ 大消息 + 并发上限。Go 三处 `NewClient` 加 `WithKeepaliveParams`，云网关 server 加 `ServerParameters` + 连接定期回收。`runtime/` 进每个 Python 镜像（`memory`/`llm-gateway` 另补 `PYTHONPATH=/app:...`）。
- **Theme 2 — 全服务优雅停机**（覆盖 #12）。Python `wait_for_termination()` → `run_aio_server`（SIGTERM→`server.stop(grace)`）；Go 用 `signal.NotifyContext` + `GracefulStop()`（云）/`http.Server.Shutdown`（边）。
- **Theme 3 — HMI 前端韧性**（覆盖 #1）。WS 逻辑抽到可测的 `hmi/src/ws.ts`：有界发送队列（不再静默丢消息）、请求看门狗（95s 兜底，杜绝永久"思考中"）、指数退避重连（1s→30s+抖动）、半开探测、"重连中"态。
- **Theme 4 — 接通熔断器 + 失败降级**（覆盖 #6）。`CircuitBreakerManager` 注入 `UnifiedDispatcher`：开路立即快速失败；云 Agent 异常不再 re-raise，降级为 `_failure`（与 edge/tool 一致）。
- **Theme 5 — 修 AgentClient 连接**（覆盖 #7）。channel 按 endpoint 复用 + keepalive（消除泄漏）；`fork()` 透传 `parent_meta`；正确捕获 `AioRpcError`。
- **Theme 6 — LLM 网关上游加固**（覆盖 #9）。复用单个 `httpx.AsyncClient`（连接池）；流式 idle/stall 超时；单次 timeout 收进网关窗口之下。
- **Theme 7 — 统一超时预算级联**。`HMI 看门狗 95s > 边/云网关 90s > Planner ≤90s > LLM(complete 60s/thinking 75s) > Agent latency_budget`。上层窗口 ≥ 下层，杜绝中途断流。
- **Theme 8 — 依赖连接加固**（覆盖 #13，P2）。Redis `socket_timeout/keepalive/health_check`；asyncpg `command_timeout/max_inactive_connection_lifetime`；NATS reconnect 对齐 collector。

---

## 分期执行

- **P0（治当前实测痛点）**：Theme 1 + 2 + 3 + 4 + 5。
- **P1**：Theme 6 + 7 + 新增韧性 e2e。
- **P2**：Theme 8 + Dashboard 熔断面板 + 文档。

## 要改的文件（模式 + 代表路径）

- **新增**：`runtime/grpcio.py`、`hmi/src/ws.ts`、`test/e2e_resilience.py`。
- **Python channel/server 替换**：约 12 个文件（见 Theme 1）。
- **Dockerfile**：全 Python 服务加 `COPY runtime`；`memory`/`llm-gateway` 补 `PYTHONPATH`。
- **Go**：`gateway/edge/main.go`、`gateway/cloud/main.go`。
- **核心逻辑**：`orchestrator/cloud/dispatch.py`、`agents/_sdk/agent_client.py`、`llm-gateway/providers.py`、`hmi/src/App.tsx`。

## 验证

- Python：`python -m pytest --import-mode=importlib`（守 884+）；`python test/smoke_edge.py`（13/13）。
- Go：`go build ./... && go vet ./...`。
- HMI：`cd hmi && npm test && npm run build`。
- 现有 E2E（需 `make up`）：`e2e_ws` / `e2e_context` / `e2e_process_region` / `e2e_central_hub_assertions`。
- 新增韧性 E2E：会话中途 `docker compose restart cloud-planner`/某 Agent，断言 channel 自愈、熔断开→闭、HMI 重连 + 看门狗兜底，无永久挂起。
- 重建提醒：改了源码的容器必须 `--build` 重建（本次几乎全量）。

## 边界与红线（不碰）

- 不动 `.env`（密钥）；只在 `.env.example` 增补带说明的可调项，代码内置安全默认值。
- 不做 mTLS/证书（保持 insecure，单列遗留生产缺口）。
- 不动 proto / DB schema / 数据迁移；不 `git push` / 不部署生产。
