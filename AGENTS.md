# AGENTS.md — 接手者（人 / AI agent）入口导航

> 你（开发者或 AI 协作者）接手本项目时**先读这一份**。它告诉你：项目是什么、铁律、现在真实进展到哪、第一步做什么、改完怎么自检。
> 工程约定的最高权威是 [`CLAUDE.md`](CLAUDE.md)；架构唯一真相源是 [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。本文件与它们冲突时以它们为准。

---

## 1. 30 秒了解项目

云边协同的智能座舱 multi-agent 系统。**分层混合编排**：端侧"快系统"秒回高频/安全敏感指令（车控/媒体）并离线兜底；云侧"慢系统"用 LLM Planner 编排复杂/跨域/多轮意图。所有 Agent 实现统一 gRPC 契约 + Manifest，经注册中心即插即用。

阶段：**Phase 1 工程化代码已落地，Docker 全栈联调通过**（2026-06-13）。268 测试通过 + E2E 4 条链路通过。

---

## 2. 项目地图（先看文档，再看代码）

| 想了解 | 看这里 |
|---|---|
| 为什么这么设计（全局）| `docs/architecture/cockpit-agent-architecture.md` |
| 接下来分几步做、怎么验收 | `docs/architecture/phase1-implementation-plan.md` |
| 核心模块怎么编码 | `docs/architecture/detailed/ws{3,4,6,8}-*.md` |
| 前瞻设计 / 问题分析（多意图、上下文、ASR、开放域延迟、车控指令、云端中枢）| `docs/design/` |
| 工程规则与铁律 | `CLAUDE.md` |
| 怎么搭环境、codegen、单服务调试 | `docs/dev-guide.md` |
| intent/scope/端口/错误码/env 速查 | `docs/conventions.md` |
| 怎么验证 | `test/README.md` |

代码目录职责见 `CLAUDE.md` §3；每个服务子目录都有自己的 README。

---

## 3. 铁律（违反即视为 bug，详见 CLAUDE.md §5）

1. **车控只经 VAL**。任何组件（含 LLM/Agent）不得直接碰 CAN/SOME-IP。
2. **LLM 不直连车控**：LLM 只产"意图/计划"，车控由确定性 Executor 经 VAL 权限校验后执行（规划/执行分离）。
3. **危险动作二次确认**（`require_confirm=true`）。
4. **不改编排核心来加 Agent**：Agent 经注册中心被发现，新增 Agent 不动 orchestrator。
5. **密钥/token 不进代码、不进 commit、不进日志**；用 `.env`（已 gitignore）。
6. **改 proto 先改 `proto/` 再 codegen**，不要手改生成代码。

---

## 4. ⚠️ 当前真实状态（别假设没验证的东西能跑）

| 项 | 状态 |
|---|---|
| 全量测试 `python -m pytest --import-mode=importlib` | ✅ 268 passed, 2 skipped |
| 端侧 Smoke 测试 `test/smoke_edge.py` | ✅ 13/13 通过 |
| `gen/`（gRPC 生成代码）| ✅ 已生成（`buf generate proto`） |
| Go 网关 | ✅ Go 1.24 编译通过，Docker 全栈运行 |
| Agent Provider 适配 | ✅ 6/6 Agent 全部接入并注册 |
| 安全/权限/编排/协作/支付 | ✅ 全部落地 |
| 可观测/熔断 | ⚠️ 代码已实现，待接线（Phase 2） |
| LLM 调用 | ✅ MiMo API 已验证连通（同步+流式）；未配 key 时走 MockProvider |
| 确认闭环（F1） | ✅ 端到端打通（HMI→网关→编排器→Agent） |
| Docker 全栈联调 | ✅ 18 个容器全部运行 |
| E2E 测试 | ✅ 4 条链路通过（车控/导航/闲聊/点餐） |
| 车控知识库 | ✅ commands.yaml 61 对象 + entities.yaml 532 实体 + responses.yaml 74 条话术；VAL 结构化执行流水线（归一化→校验→安全门控→模拟→选话术）+ answer_length 简繁切换 |
| 端侧意图覆盖 | ✅ 150 条意图 pattern（fast_intent），覆盖 61 对象（车控/媒体/蓝牙/WiFi/电话/广播/音乐/视频/导航/360环视等）；飞书公版数据全量导入（1465 意图） |
| 多意图拆分 | ✅ 端侧 split_and_classify（连接词切分+保守路由+actions 返回）+ 云侧 Planner DAG 强化（并行/串行判定） |
| ASR/TTS | ✅ HTTP 代理 + MiMo ASR/TTS + webm→wav 后端转码（ffmpeg）+ 9 音色 |
| HMI（前端） | ✅ 「深空座舱 HUD」组件化 + 设置页 + 流式渲染 + 记忆视图 + 语音按钮 |
| 开放域流式 + 模型分层 | ✅ engine 单步 ExecuteStream 直通 + chitchat 快模型/兜底；降规划延迟待做 |
| 对话上下文/指代 | ✅ engine 写对话记忆 + 规划注入历史（仅云侧链路；端侧快意图未入记忆） |
| 飞书数据全量导入 | ✅ lark-cli 拉取 5 张公版表（意图 1465 条 + 分类 400 + 词库 5185 + 响应 3000 + 兜底 34）；3 个生成脚本可重跑（`scripts/gen_commands_yaml.py` / `generate_entities.py` / `generate_responses.py`） |

**结论**：Phase 1 全部验收标准达成 + 飞书公版全量导入（61 对象/150 意图）+ 多意图拆分 + ASR 转码 + answer_length 话术简繁（2026-06-14）。前瞻设计见 `docs/design/`，Phase 2 backlog 见 `docs/reviews/2026-06-11-review-fixes.md`。

**进行中（2026-06-14 起）**：云端中枢升级（复杂意图：理解→规划→异构调度 车端快思考/Agent/工具），设计见 `docs/design/2026-06-14-cloud-central-orchestrator.md`。P0-P3 主体代码已落地：统一 dispatcher、Gateway `DispatchToEdge`、端 `edge_call`→VAL、端侧能力注册、T2 有界循环、内置工具与执行层权限硬校验。**最终验收暂缓**：最后一批收尾改动后尚未跑全量/Smoke/Go/Docker/E2E；准确证据、未验证范围和后续 P0 待办见设计文档“落地记录”。当前最重要的未完成项是从可信 token/设备身份向 `granted_scopes` 接线，禁止用默认全授权绕过 fail-closed。

---

## 5. 第一步（任何人接手都先做这个）

```bash
cp .env.example .env        # 可选填 LLM_API_KEY；不填走 mock 也能跑
make proto                  # 生成 gen/python + gen/go（没有它什么都跑不起来）
python test/smoke_edge.py   # 验证端侧逻辑（无需 docker，应 13/13 通过）
make up                     # 起全栈（首次需调试，见 docs/dev-guide.md）
```
环境/工具没装齐、Windows 无 make、单服务调试 → 看 `docs/dev-guide.md`。

---

## 6. 改完怎么自检（提交前必做）

| 改了什么 | 自检 |
|---|---|
| 任何 Python | `python -m py_compile <改动文件>`；相关 `python -m pytest <agent>/tests` |
| 端侧逻辑（fast_intent/val/edge_agents）| `python test/smoke_edge.py` |
| proto | `make proto` 重新生成，确认 codegen 无错 |
| 端到端链路 | `make up` 后 `python test/e2e_ws.py` |
| 新增 Agent | 契约测试（参考 `agents/navigation/tests`）+ 在 compose 注册 |

不要为了"让它跑起来"注释报错或加绕过标记——找根因（CLAUDE.md §6）。

---

## 7. 最常见任务：新增一个 Agent（最短路径）

1. 复制 `agents/navigation/` 结构到 `agents/<snake_name>/`（包目录 snake_case，agent_id kebab-case）。
2. 改 `manifest.yaml` 声明能力/权限/trust_level/deployment。
3. 继承 `agents/_sdk` 的 `BaseAgent`，实现 `handle()`（**别重写 gRPC/注册**，SDK 已封装）。
4. 写 `tests/` 契约测试。
5. 在 `deploy/docker-compose.yaml` 注册服务（分配新端口，见 `docs/conventions.md` 端口表）。
6. **不改编排核心**——注册后 Planner 自动可路由。

详见 `agents/_sdk/README.md` 与 `CLAUDE.md` §3。

---

## 8. 给 AI 协作者的工作方式

- 动手前读 `CLAUDE.md` + 本文件 + 相关 WS 细化文档；大改动先在设计文档对齐。
- 严格守目录约定与命名（`docs/conventions.md`），不要发明新结构。
- 改接口先改 `proto/` 再 codegen；不手改 `gen/`。
- 每次改动跑对应自检（§6），用证据说话，别声称"应该能跑"。
- 遇到与文档冲突的现状，**先指出冲突**再动手，不要默默绕过。
- 落地某个 WS 前，建议用 `writing-plans` 把该 WS 细化文档转成带 checklist 的实施计划。
