# 座舱 Agent 可观测仪表盘 Implementation Plan

> **执行状态（2026-06-15）**：17 个任务已全部落地并完成全栈验收。
> 实际验证结果、实现偏差与剩余边界统一见
> `docs/design/2026-06-15-observability-dashboard.md` 的“落地记录”；本文件保留为
> TDD 实施过程参考，不再作为当前状态清单。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 car-agent 补一层可观测层并新增独立仪表盘前端，实时看车辆状态（含变更 diff）、车辆动态、请求链路走向、各 Agent 运行态，并支持"发指令→看链路→看状态变化"的对照实验。

**Architecture:** 复用架构 §8 规划但未接通的 NATS 事件总线作为汇聚通道；各服务在关键节点 `fire-and-forget` 发事件；新增 `observability-collector`（FastAPI）订阅聚合并对前端暴露 REST 快照 + WebSocket 增量；新增 `dashboard`（React）消费 collector 并复用现有 edge-gateway 入口发指令。**不改任何 `.proto`**。

**Tech Stack:** Python 3.11 + FastAPI + nats-py（后端）；React + TypeScript + Vite（前端）；NATS（事件总线，已在 compose）；pytest（后端测试）；vitest/tsc（前端测试）。

**设计真相源：** `docs/design/2026-06-15-observability-dashboard.md`（本计划严格据此展开）。

---

## 不变量（每个 task 都不得违反）

1. **车控只经 VAL**：仪表盘发指令走与 HMI 相同的 edge-gateway 入口；debug 只设环境量（speed_kmh/battery/gear/location），绝不写车控输出状态。
2. **埋点 best-effort**：所有 `emit_*` fire-and-forget、失败静默、不阻塞主链路、NATS 不可用不破坏离线。
3. **不改 proto**：可观测事件与 registry 健康都走 NATS JSON。
4. **不破坏现状**：每个后端 task 完成后 `python -m pytest --import-mode=importlib` 保持现有 325 passed 全绿。

---

## 文件结构（创建/修改一览）

**新增：**
| 文件 | 职责 |
|---|---|
| `observability/events.py` | `EventEmitter`：emit_span/state/metric/health，懒连 NATS，best-effort |
| `observability/tests/test_events.py` | events 单测 |
| `observability/collector/__init__.py` | 包标记 |
| `observability/collector/store.py` | `CollectorStore`：车辆状态镜像 / 链路环形缓冲 / agent 聚合 |
| `observability/collector/server.py` | FastAPI app：REST + WS + NATS 订阅 + debug 转发 |
| `observability/collector/main.py` | 启动入口（uvicorn） |
| `observability/collector/requirements.txt` | fastapi/uvicorn/nats-py/httpx |
| `observability/collector/Dockerfile` | 容器 |
| `observability/collector/tests/test_store.py` | store 单测 |
| `observability/collector/tests/test_server.py` | REST/debug 单测（TestClient） |
| `dashboard/` | React+TS Vite 应用（脚手架 + api client + 四区组件） |

**修改：**
| 文件 | 改动 |
|---|---|
| `orchestrator/edge/val.py` | `__init__(on_change=None)` + state 写入处回调 |
| `orchestrator/edge/server.py` | trace_id 贯穿 + route/val span emit |
| `orchestrator/edge/main.py` | 注入 publisher（队列→后台 task）+ 订阅 debug topic + 启动 snapshot |
| `orchestrator/cloud/engine.py` | planning/aggregate span |
| `orchestrator/cloud/dispatch.py` | 每 step span + metric emit |
| `orchestrator/cloud/loop.py` | t2.iter span |
| `registry/store.py` + `registry/main.py` | 健康 emit |
| `orchestrator/edge/requirements.txt`、`orchestrator/cloud/requirements.txt`、`registry/requirements.txt` | 加 `nats-py` |
| `deploy/docker-compose.yaml` | 加 `observability-collector` + `dashboard` 服务 |

> 前端组件的**逻辑与数据绑定**在本计划中给全（complete code）；**视觉样式**用语义 className 占位，最终由 `frontend-design` skill 在执行阶段统一打磨（深空座舱风格，呼应 `hmi/`）。这是设计阶段已确认的分工。

---

## 分卷导航

| 分卷 | 范围 | 状态 |
|---|---|---|
| [`P0/P1`](2026-06-15-observability-dashboard-p0-p1.md) | NATS/event emitter、collector、VAL 状态观测、Dashboard 骨架 | 已完成 |
| [`P2/P3`](2026-06-15-observability-dashboard-p2-p3.md) | trace/span、Agent 健康/指标、控制台闭环、回归验收 | 已完成 |

## 当前验收

- Python 全量：360 passed, 2 skipped（2026-06-15）。
- Dashboard：4 passed，Vite 生产构建通过。
- HMI：5 passed，Vite 生产构建通过。
- Docker Compose：20 个容器运行，NATS/collector/dashboard 已联调。
- Agent 健康摘除与恢复、车辆 debug 状态变化、端云 trace 均有全栈验证记录。

历史分卷中的代码片段、提交命令和中间测试数字按实施时原文保留，不作为当前事实源。
当前 collector 仍为单实例内存聚合；Prometheus/OTel、持久化 trace 与正式鉴权待后续落地。
