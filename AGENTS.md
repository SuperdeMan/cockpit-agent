# AGENTS.md — 接手者（人 / AI agent）入口导航

> 你（开发者或 AI 协作者）接手本项目时**先读这一份**。它告诉你：项目是什么、铁律、现在真实进展到哪、第一步做什么、改完怎么自检。
> 工程约定的最高权威是 [`CLAUDE.md`](CLAUDE.md)；架构唯一真相源是 [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。本文件与它们冲突时以它们为准。

---

## 1. 30 秒了解项目

云边协同的智能座舱 multi-agent 系统。**分层混合编排**：端侧"快系统"秒回高频/安全敏感指令（车控/媒体）并离线兜底；云侧"慢系统"用 LLM Planner 编排复杂/跨域/多轮意图。所有 Agent 实现统一 gRPC 契约 + Manifest，经注册中心即插即用。

阶段：**Phase 1 工程化 PoC**。主干与云端中枢、R2-R4 硬化主题、可观测台（badcase 排查贯通）、
旅程级验证体系（L3 journeys + L4 HMI CDP）、智能化升级 M0a→M4（S2S 双语音链路 / 声纹多用户 /
统一主动引擎 / 受控 MCP 桥 / Task Ledger + Outcome Verifier / Skill 层，已过跨阶段组合总体验收）
与 M5 数据飞轮（P0→P3a + P3 收尾）均已落地；首批真实外部能力在线（高德 / 和风 / Exa / Tushare /
api-football，无凭证回退 mock）。当前全量测试基线与批次证据见 §4.0 快照；逐批历史流水在
[`docs/agents-history.md`](docs/agents-history.md)。

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
| 历史批次流水（只进不出，查证据用） | `docs/agents-history.md` |

代码目录职责见 `CLAUDE.md` §3；每个服务子目录都有自己的 README。

---

## 3. 铁律（违反即视为 bug，详见 CLAUDE.md §5）

### 唯一运行环境

- 根目录 `.env` 是唯一的运行时环境与密钥来源；不得复制、维护或依赖 `deploy/.env`。
- 全栈只允许用 `make up` 或 `docker compose -f compose.yaml ...` 启动；根 `compose.yaml` 显式加载根 `.env`，并以 `deploy/` 为 included Compose 的项目目录以保持构建路径不变。
- 不得直接以 `deploy/docker-compose.yaml` 为首个 Compose 文件启动，否则真实 Provider 可能静默回退 mock。

1. **车控只经 VAL**。任何组件（含 LLM/Agent）不得直接碰 CAN/SOME-IP。
2. **LLM 不直连车控**：LLM 只产"意图/计划"，车控由确定性 Executor 经 VAL 权限校验后执行（规划/执行分离）。
3. **危险动作二次确认**（`require_confirm=true`）。
4. **不改编排核心来加 Agent**：Agent 经注册中心被发现，新增 Agent 不动 orchestrator。
5. **密钥/token 不进代码、不进 commit、不进日志**；用 `.env`（已 gitignore）。
6. **改 proto 先改 `proto/` 再 codegen**，不要手改生成代码。

---

## 4. ⚠️ 当前真实状态（别假设没验证的东西能跑）

### 4.0 当前快照（2026-08-09）

意图落域对抗测试按这个顺序接手：运行手册
[`docs/guides/intent-adversarial-testing.md`](docs/guides/intent-adversarial-testing.md) → 最终验收
[`docs/reviews/2026-08-04-review-intent-adversarial-finalization.md`](docs/reviews/2026-08-04-review-intent-adversarial-finalization.md)
→ 逐批证据 `docs/design/2026-08-02-intent-routing-adversarial-findings.md` §16。
历史流水只查 [`docs/agents-history.md`](docs/agents-history.md)，不要再抄回本文件。

**最新后端全量基线**：`python -m pytest --import-mode=importlib`
**4490 passed / 16 skipped / 0 failed**（收集 4506 项，单进程 18m14s）。
HMI `node --test` **225/225**、Dashboard vitest **17/17**、端侧 smoke **13/13**、
Go 网关 vet+test 通过；HMI / Dashboard 数字为 2026-08-09 实测，Go 数字沿用 2026-08-04
批次；本轮没有改前端、Go、`.env` 或 CI。

| 意图落域证据 | 当前可引用事实 |
|---|---|
| L0 discovery | **76/76**，561 条 / 522 唯一输入 |
| gate 规模 | **139 stable / 129 唯一输入**，L0 strict **25/25，exit 0** |
| 对比模型正式 baseline | [`baseline_intent_adversarial.json`](docs/reviews/eval/baseline_intent_adversarial.json)；干净 `f0af9c0`，锁定 `deepseek:deepseek-v4-flash`，由当前 L3 原始字节/摘要/时间/精确路径契约重新取证并写入 |
| DeepSeek 完整 gate | **147/147**：L0 25、L1 117、L2 4、L3 1；exact **121/121**，raw 幻觉/校验后逃逸/不稳定均 **0/121**；L1/L2 各 **2 个独立进程 × 每进程 3 样本** |
| MiniMax 主模型 gate | **141/147**；exact **115/121**，raw 幻觉 **8/121**、逃逸 **0/121**，6 条均为 `unstable`、无 `stable_fail`，资格 `eligible=False` |
| L3 gate | A1-2 在两模型均 **1/1**；正式 baseline 的 invocation 新鲜、exit 0，只证明该授权 case/claim |
| fallback | DeepSeek 正式批 **2/122**，均为语料声明过的 A8，未声明 fallback **0**；MiniMax **11/122**，其中未声明 **4** |
| 代码回归 | 最终针对性选集 **254 passed / 3 skipped**；动态架构守卫 **89 passed**；端侧 smoke **13/13**；Skill / Exemplar / L0 门禁均通过 |

首份正式 baseline 已存在，但它是 **DeepSeek 对比/参考模型**在固定 provider、资产与代码快照下的
意图理解与落域证据；不证明 MiniMax 主模型、Agent 业务结果、外部 Provider 内容或跨模型平均质量。
MiniMax 本轮即使 process/provider/embedding/L3 身份完整，仍因 `gate_failures`、raw 幻觉、未声明
fallback 与 `unstable_results` 被资格闸拒绝。后续写入仍必须由一次新的完整父报告
明确 `eligible=True`，不得手工改正式文件。

### 4.1 活跃待办（只列仍需行动的）

| 优先级 | 待办 | 完成判据 |
|---|---|---|
| **P0** | 收敛 MiniMax 主模型完整 gate 的 6 个 `unstable` 单元 | 不用 route hint 追单批全绿；在新的独立进程证据中同时消除不稳定、raw 幻觉与未声明 fallback，主模型报告才可 `eligible=True` |
| **P1** | 修正 `pytest test/` 的裸 `server` 导入冲突 | 目录选集不再因 `sys.path` 顺序把 `EdgeOrchestratorServicer` 解析到 llm-gateway；项目正式基线仍以根命令为准 |
| **P1** | 清理 hint 退役后的陈旧离线评测资产 | 2026-08-09 新跑 `eval_route_hints.py` 为 76/86、9 个唯一 baseline regression；只迁出已退役的 reminder recall 断言并刷新该层 baseline，等价用例继续留在 `mode_routing_cases.yaml` 走端到端保护，`--strict` 最终 exit 0 |
| **P1** | 清理宽口径 journeys 两条外部依赖残留 | B3-1 的 gold 不再依赖跑批当天真实天气；B3-2 的广州塔地标解析在高德侧另账处理 |
| **P2** | 补 weather-outing 的真实 L3 claim | 不复用语义不对应的旧 journey；新增真正验证 weather→nearby 连续性的授权旅程后再晋级 |
| **P2** | 三条未晋级候选重新取证 | `cp.hvac-news.swapped`、`nq.hvac.reported`、`nq.match.lastweek` 按当前跨进程契约取证 |
| **P2** | M5 P3b：端侧 NLU operate 抽取与放量 | 真实流量错对象率 <0.3% 后再开工；其余延后/条件项看 §4.2，量产级总纲看 `docs/architecture/phase1-implementation-plan.md` |

### 4.2 延后 / 条件待办索引（不进入当前主线）

> §4.1 只放正在行动的事项；本表保留尚未启动、等待外部条件或明确后置的入口。条件满足后先晋级 §4.1 并补完成判据；历史流水仍只查 `docs/agents-history.md`。

| 主题 | 当前状态 / 启动条件 | 权威入口 |
|---|---|---|
| 端侧能力面与 P3b 前置 | 除雾 intent 仍缺席；“穿衣指数→股指”仍是规则错配。对象桥接、operate 抽取和真实错对象率 <0.3% 齐备后才放量 | [M5 P3 收尾](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) §P3 收尾 |
| live 路由回归进 CI | hint 退役后的召回保护目前是 live 人工车道，不是 CI 阻断；有稳定凭证、预算与 provider 方差处置后再接 CI | [旅程体系](docs/design/2026-07-14-journey-e2e-test-system.md) §4.3、[评测说明](docs/reviews/eval/README.md) §规则退役 |
| `route_hints` 继续退役 | 当前实数 **11**；`mcp-bridge#0` 必须先过专项安全回归。旧三条单档候选不得按历史索引直接执行；当前只用 MiniMax 主模型 / DeepSeek 对比，MiMo key 失效不阻塞主线，切换 provider 时重新全覆盖取交集 | [M5 P2](docs/design/2026-07-28-intent-accuracy-data-flywheel.md)、[评测说明](docs/reviews/eval/README.md) §规则退役 |
| M5 后续杠杆 | catalog 检索化当前是“有意不做”；16k 预算再次裁剪或保护集显著变瘦时重评。gold→范例现走 CLI；P4 仅在范例 ≥2k 且 N1 平台期 ≥2 周时启动 | [M5 P2/P4](docs/design/2026-07-28-intent-accuracy-data-flywheel.md) |
| M-B / M-C / M-D 明确后置项 | 13 张验收主卡已清零；跨域删除 saga、完整隐私管理/迁移仪式、持久治理扩面与 MCP 生产化覆盖按 GDPR 完备性、量产迁移或新消费方触发 | [总体验收](docs/reviews/2026-07-26-acceptance-review-m0a-m4.md) §10.2/§11.2/§12.2、[OwnerKey 契约](docs/conventions.md) §9.13 |
| M2 / M3 产品化边界 | Ledger 自动续跑/任务中心，以及主动治理持久化、偏好学习、dashboard、远距 geocode 与真实商户均为显式未做；出现真实消费方或产品阶段后另立卡 | [M2 RFC](docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md) §9.6、[M3 RFC](docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md) §10.7 |
| M4 声纹 / 视觉余项 | 真麦校准、语音注册入口、模板漂移治理、视觉多轮与 `vp_*` 指标消费仍未收口；进入真机量产验收或 v2 前启动 | [M4 RFC](docs/design/2026-07-25-m4-p4-voiceprint-vision-rfc.md) §11.5/§11.8、[声纹契约](docs/conventions.md) §9.11 |

### 4.3 读数纪律

- `110/116`、`113/117`、raw `6/117`、旧 seen/unseen 对比均是历史口径/批次，**不得当当前结果**。
- `domain_hit_rate` 只要求命中交集；`exact_plan_set_rate` 要求必要组齐全、禁选为空、无额外项，二者不可直比。
- L1/L2 的正式 gate 必须是两个独立进程、每进程 3 样本；同进程 repeat 3 不能替代第二进程。
- baseline 的 `repeat_coverage=121/122` 是 L1/L2 121 个 live 单元重复、L3 按设计只跑一次；`process_policy_complete=true`，不是缺 shard。
- `fallback_plan_rate` 非零不自动失败；只有语料显式声明的 A8 能力缺席族可接受，未声明 fallback 一条即挡 baseline。
- Live 跑批前按运行手册 §2 同时设置 provider、model 与 embedding 三项；少一项整批证据作废。
- `pytest test/` 不是项目基线命令；换选集会改变 import 顺序与分母，不能拿来替代根跑。

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
| Dashboard / 可观测 | `cd dashboard && npm test && npm run build`；全栈后查 `http://localhost:8092/healthz` 与 `http://localhost:5174`；badcase 贯通链路 `python test/e2e_obs.py`（turn 落库/obs.llm/日志关联/badcase/重启持久化） |
| proto | `make proto` 重新生成，确认 codegen 无错 |
| 端到端链路 | `make up` 后 `python test/e2e_ws.py` |
| 新增 Agent | 契约测试（参考 `agents/navigation/tests`）+ 在 compose 注册 |

不要为了"让它跑起来"注释报错或加绕过标记——找根因（CLAUDE.md §6）。

---

## 7. 最常见任务：新增一个 Agent（最短路径）

1. 复制 `agents/navigation/` 结构到 `agents/<snake_name>/`（包目录 snake_case，agent_id kebab-case）。
2. 改 `manifest.yaml` 声明能力/权限/trust_level/deployment；**若 Agent 需要精确位置/电量等敏感上下文，必须声明 `context_scopes`**（`location` / `vehicle_state`，含调子 Agent 透传的 propagator）——否则编排按最小化下发会剥掉这些键。
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
