# AGENTS.md — 接手者（人 / AI agent）入口导航

> 你（开发者或 AI 协作者）接手本项目时**先读这一份**。它告诉你：项目是什么、铁律、现在真实进展到哪、第一步做什么、改完怎么自检。
> 工程约定的最高权威是 [`CLAUDE.md`](CLAUDE.md)；架构唯一真相源是 [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。本文件与它们冲突时以它们为准。

---

## 1. 30 秒了解项目

云边协同的智能座舱 multi-agent 系统。**分层混合编排**：端侧"快系统"秒回高频/安全敏感指令（车控/媒体）并离线兜底；云侧"慢系统"用 LLM Planner 编排复杂/跨域/多轮意图。所有 Agent 实现统一 gRPC 契约 + Manifest，经注册中心即插即用。

阶段：**Phase 1 工程化 PoC 主干、云端中枢 P0-P3 与轻量可观测台已落地**（2026-06-15）。
持久化/多实例、mTLS/沙箱、完整 OTel 等仍是后续工作；**真实外部能力已接入首批**
（导航=高德、天气=和风含 JWT/EdDSA 鉴权，无凭证回退 mock；2026-06-20 已用真实凭证端到端
冒烟通过）。当前全量单测 517 passed, 6 skipped；compose 新增 info-agent（全栈联调待 `make up`）。

---

## 2. 项目地图（先看文档，再看代码）

| 想了解 | 看这里 |
|---|---|
| 为什么这么设计（全局）| `docs/architecture/cockpit-agent-architecture.md` |
| 接下来分几步做、怎么验收 | `docs/architecture/phase1-implementation-plan.md` |
| 核心模块怎么编码 | `docs/architecture/detailed/ws{3,4,6,8}-*.md` |
| **怎么接真实 provider（高德/和风样板）** | `docs/guides/provider-integration.md` |
| **怎么扩 info 能力 / 加新独立 Agent 并打通** | `docs/design/2026-06-20-info-agent-expansion.md`、`docs/design/2026-06-20-standalone-agents-roadmap.md` |
| 前瞻设计 / 问题分析（多意图、ASR、车控、云端中枢、可观测）| `docs/design/` |
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
| 全量测试 `python -m pytest --import-mode=importlib` | ✅ 517 passed, 6 skipped（2026-06-20 实测；+22 provider/HTTP/JWT 单测） |
| 端侧 Smoke 测试 `test/smoke_edge.py` | ✅ 13/13 通过 |
| HMI TTS 单测 / 构建 | ✅ Node 5/5；`npm run build` 通过 |
| Dashboard 单测 / 构建 | ✅ Node 10/10；`npm run build` 通过 |
| `gen/`（gRPC 生成代码）| ✅ 已生成（`buf generate proto`） |
| Go 网关 | ✅ Go 1.24 编译通过，Docker 全栈运行 |
| Agent Provider 适配 | ✅ 7 Agent（新增 info 天气）接入统一工厂；导航=高德 / 天气=和风（JWT/EdDSA）真实适配已落地并**真实凭证冒烟通过**（`_sdk/http.py` 统一超时/重试/熔断 + provider 调用 span），无凭证/失败回退 mock；端到端见 `test/e2e_real_providers.py` |
| 安全/权限/编排/协作/支付 | ✅ PoC 链路落地；真实 token、正式沙箱与真实支付仍待接入 |
| 可观测 | ✅ NATS 事件、collector REST/WS、车辆 diff、端云 span、Agent 健康/指标与独立 Dashboard；collector/registry 重启经周期快照与周期重注册自愈；Prometheus/OTel 导出仍待做 |
| 熔断 | ⚠️ 基础实现存在，生产化接线与演练待做 |
| LLM 调用 | ✅ MiMo API 已验证连通（同步+流式）；未配 key 时走 MockProvider |
| 确认闭环（F1） | ✅ 端到端打通（HMI→网关→编排器→Agent） |
| Docker 全栈联调 | ✅ 20 个容器全部运行；NATS healthcheck、collector、dashboard 通过 |
| E2E 测试 | ✅ 4 条标准链路有历史通过记录；2026-06-14 另完成 2 条慢意图/复杂意图场景全栈回放 |
| 车控知识库 | ✅ commands.yaml 62 对象 + entities.yaml 532 实体 + responses.yaml 78 条话术；VAL 结构化执行流水线（归一化→校验→安全门控→模拟→选话术）+ answer_length 简繁切换；车窗开合度 inc/dec、大灯行驶中禁关（drive_restricted_off）、电量查询端侧确定性应答 |
| 端侧意图覆盖 | ✅ 150 条意图 pattern（fast_intent），覆盖 62 对象（车控/媒体/蓝牙/WiFi/电话/广播/音乐/视频/导航/360环视等）；飞书公版数据全量导入（1465 意图） |
| 多意图拆分 | ✅ 端侧按语义组分流：本地动作走 VAL，导航路线偏好、歌曲/歌手等续接片段与主意图完整上云；云侧 Planner DAG 强化 |
| ASR/TTS | ✅ HTTP 代理 + MiMo ASR/TTS + webm→wav 转码 + 9 音色；HMI 句子级增量合成与顺序播放 |
| HMI（前端） | ✅ 「深空座舱 HUD」组件化 + 设置页 + 流式渲染 + 记忆视图 + 语音按钮 |
| 开放域流式 + 模型分层 | ✅ engine 单步 ExecuteStream 直通 + chitchat 快模型/兜底；降规划延迟待做 |
| 对话上下文/指代 | ✅ engine 写对话记忆 + 规划注入历史；端侧本地轮 best-effort 写共享记忆 |
| 飞书数据全量导入 | ✅ lark-cli 拉取 5 张公版表（意图 1465 条 + 分类 400 + 词库 5185 + 响应 3000 + 兜底 34）；3 个生成脚本可重跑（`scripts/gen_commands_yaml.py` / `generate_entities.py` / `generate_responses.py`） |

**结论**：Phase 1 工程化 PoC 主干、云端中枢 P0-P3 与轻量可观测台已通过当前仓库验收
（2026-06-15）。这不等同于原始 Phase 1 量产级 DoD 全部完成；差距以
`docs/architecture/phase1-implementation-plan.md` 顶部状态说明和本节待办为准。

**已完成**：云端中枢 P0-P3、统一 dispatcher、Gateway
`DispatchToEdge`、端 `edge_call`→VAL、T2 有界循环、确定性工具、PoC 默认 scope、
可观测接线、混合意图语义分组、多步反馈、端侧轮记忆、危险动作确认、句子级增量
TTS、慢意图计划完整性与复杂混合意图回归；另已落地 NATS 可观测出口、collector、
车辆状态/动态、分布式链路、Agent 健康/指标与独立 Dashboard，以及实时流修复、
车速/档位自洽联动、collector 周期快照自愈、registry 重启后能力周期重注册自愈；并经专项 E2E 可观测验证（`test/e2e_observability.py`）修复一批末端执行缺陷（天窗程度/媒体播放/座椅并列拆分/流式直通 step span 等）；并补齐中枢 P0 测试覆盖：多轮上下文/等待态 span 进程内单测 + 全栈断言脚本 `test/e2e_central_hub_assertions.py`（P0-1~5）；P1 再补上 collector 重启快照自愈、端侧本地轮记忆 best-effort 的进程内回归，并在全栈断言加入 trace 全链贯穿校验（P1-8）；P2 再建数据驱动语料层——L0 安全门控/车控对象矩阵/多意图边界 88 条参数化 + L1 媒体/开放域流式 + nightly 真实 LLM 跨 Agent 组合/多轮指代 4 条（默认 skip，需 `make up` + 宿主 `LLM_API_KEY`）。2026-06-17 另做仪表盘车辆状态面板重构（分组 + 按类型渲染 + 空调/氛围灯/媒体三合一聚合 + 氛围灯真实颜色修复 + 面板有界滚动不挤占 Agent 区）与一批车控细化（车窗相对开合度 inc/dec 与"开条缝"、大灯行驶中只禁关 drive_restricted_off、电量查询端侧确定性应答、风速档位话术、planner 禁止把未匹配的状态查询硬套成胎压）。
详见 `docs/design/` 落地记录。

**待做**：Registry/Cloud Gateway 持久化与多实例扩展、其余 Agent 真实 Provider
（food/parking/manual-rag）与支付/权限 token、正式 third-party 沙箱与网络白名单、
Prometheus/OTel 导出与完整熔断、真正的服务端 PCM 流式 TTS、真实 SOME-IP/CAN。

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
| HMI / TTS | `cd hmi && npm test && npm run build` |
| Dashboard / 可观测 | `cd dashboard && npm test && npm run build`；全栈后查 `http://localhost:8092/healthz` 与 `http://localhost:5174` |
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
