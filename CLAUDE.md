# CLAUDE.md — 智能座舱 Multi-Agent 项目规则

> 本文件是项目的最高工程约定，所有人（含 AI 协作者）在本仓库工作必须遵守。
> 调整规范时：先改本文档，再改实践，不要反过来。

## 1. 项目是什么

云边协同的智能座舱 AI Agent 系统。架构范式为**分层混合编排**：端侧"快系统"处理高频/确定/安全敏感指令（车控、媒体），云侧"慢系统"用 LLM Planner 编排复杂、跨域、多轮意图。所有 Agent 实现统一 gRPC 契约 + Manifest，经注册中心即插即用。

**完整设计见 `docs/architecture/cockpit-agent-architecture.md`（架构唯一真相源）。** 任何与该文档冲突的实现都视为 bug。

## 2. 技术栈（不要随意偏离）

| 层 | 语言/框架 |
|---|---|
| 接入网关 gateway/ | Go (grpc-go + websocket) |
| 编排器 orchestrator/、Agent agents/、各 AI 服务 | Python 3.11 (grpcio + FastAPI) |
| 车控抽象层 vehicle-abstraction/ | C++（PoC 阶段可用 Python 模拟） |
| HMI hmi/ | React + TypeScript + Vite |
| 服务间通信 | gRPC（proto/ 为单一真相源） |
| 异步/广播 | NATS |
| 存储 | Redis（短期）、PostgreSQL + pgvector（长期/向量） |

## 3. 目录约定（什么放哪）

```
proto/          gRPC 契约——所有接口的唯一真相源，改接口先改这里再 codegen
gateway/        Go 接入网关（edge/ 端侧，cloud/ 云侧）
orchestrator/   edge/ 端侧编排+FastIntent（+ knowledge/ VAL 车控知识库：commands/entities/
                responses.yaml，对象含 display_name——端侧 capability 描述由它机械生成；
                + nlu_objects.yaml **对象等价类台账**：语料中文标签／VAL object／规则
                object 三套命名归并，人裁一次机器守「不许悄悄漏」，同 boundaries 形态）；
                cloud/ 云端 Planner
llm-gateway/    LLM 多模型网关（所有 LLM 调用的唯一出口）；音频面同门：批/流式 ASR·TTS
                + s2s/ 端到端语音会话（M4；协议/provider/会话/回灌四层，换厂商只加 provider 子类）
                + speaker_embed.py 声纹提取（音频→向量，**不持模板**）
                + vision_frames.py 视觉单帧内存库（图像只在此活 120s，**不落盘不落 Redis**）
registry/       Agent 注册中心
memory/         记忆/画像服务
agents/         所有 Agent；_sdk/ 是公共 SDK，每个 Agent 一个子目录
skills/         Planner 规划知识声明式载体（M0b）：guides/ 领域组合判据（双通道检索注入）、
                policies/ 跨域软约束（常驻）。**加规划知识=投 skill 文件不改编排核心**；
                golden 必填进 CI 门禁（含 holdout），契约见 skills/README.md（唯一真相源）
                + exemplars/ **范例库**（M5 P1）：`<domain>.yaml` 存「话术→正确落域」，
                检索后作 few-shot 进 prompt——权威链**最软层**，不做硬路由。
                **修落域 badcase 的默认产物是范例不是正则**（写错只是噪声，hint 写错是
                事故）；契约见 skills/exemplars/README.md，门禁 test/eval_exemplars.py
security/       权限引擎、scope 定义、内容审核、注入防护
payment-gateway/  统一支付网关（Agent 不持支付凭证）
proactive/      统一主动引擎：主动消息的全局治理器（频控/免打扰/驾驶负荷/同类合并）
observability/  可观测模块：NATS 事件出口、collector、trace/日志/指标
hmi/            React 座舱前端
dashboard/      React 开发/演示可观测台（不进入车控执行主链）
deploy/         docker-compose / helm / k8s
scripts/        codegen、构建辅助（含 gen-certs.* 生成 mTLS 证书）、自进化流水线 evolve.py（M1b，nightly 经 Task Scheduler）
runtime/        共享运行时（gRPC keepalive/优雅停机/mTLS 工厂；+ 主动消息出口 proactive.py——全 Python 服务经此建 channel/server、发主动消息）
docs/           架构与设计文档
test/           端到端场景测试
gen/            codegen 产出（gitignore，不要手动编辑）
certs/          服务间 mTLS 证书（gitignore；scripts/gen-certs.* 生成，仅 .gitkeep 入库）
models/         本地推理模型（gitignore，scripts/fetch-*.* 拉取或本地训练，仅 .gitkeep 入库）：
                voiceprint/ 声纹 CAM++ ONNX；nlu/ 端侧语义 NLU（M5 P3a，`scripts/train_edge_nlu.py`
                产出 edge_nlu.onnx + labels.json + vocab.json；底座经 scripts/fetch-edge-nlu-base.*
                从 **ModelScope** 拉——HF 在国内拉不动）。**缺失不阻塞**——决议 disabled、整链回落
```

> 注：`vehicle-abstraction/` 在架构文档中规划，当前 PoC 阶段 VAL 实现位于 `orchestrator/edge/val.py`（Python 模拟）。

### 新增一个 Agent 的标准流程（必须遵守）
1. 在 `agents/<name>/` 下按模板建目录（参考 `agents/navigation/`）。
2. 写 `manifest.yaml` 声明能力、权限、trust_level、deployment；需要精确位置/电量/车外画面等敏感上下文的 Agent 还要声明 `context_scopes`（`location`/`vehicle_state`/`vision`），否则编排最小化下发会剥掉这些键。
   - **确定性路由（R2.1）**：弱 LLM 会漏/误路由该 Agent 的重域意图时，用 `route_hints` 声明兜底（`pattern`/`intent`/`policy`=`replace`\|`append`/`priority`/`guard`/`slots`；`slots` 值支持 `$text`=原话、`$1..`=捕获组）——编排核心 `orchestrator/cloud/route_hints.py::RouteHintEngine` 通用消费，**取代**过去在 `planning.py` 加正则兜底的做法。
   - **重域能力**（需开思考+过程区，如多轮检索/LLM 重生成）在该 capability 标 `heavy: true`（编排 `progress.is_complex` 据此判定）。
   - 出**主卡**的 Agent 在 `ui_card` 加 `display_priority`（`0`=主卡多意图下独显 / `1`=交互候选 / 缺省 `2`=普通信息卡），聚合器据此择优。
3. 继承 `agents/_sdk` 的 `BaseAgent` 实现业务逻辑，**不要重新实现 gRPC 契约**。
4. 写 `tests/` 契约测试 + 黄金用例。
5. 在 `deploy/docker-compose.yaml` 注册服务。
6. **不要修改编排核心代码**——Agent 通过注册中心被发现，编排对 Agent 无感；确定性路由 / 重域标记 / 卡片优先级全由步骤 2 的 manifest 声明式字段表达，**不在 `planning`/`context`/`aggregator`/`progress` 加硬编码**（R2.1 已把历史硬编码全部机制化，铁律已由 `test_planning.py` 契约测试固化）。执行后想把请求**改派**给别的能力（如「这题需要联网才能答」），用 `AgentResult.data["_escalate"]` 保留键声明——engine 通用消费、每轮最多一跳（协议登记 `docs/conventions.md` §9.1，契约测试 `test_engine_escalate.py`），同样不改编排核心。

## 4. 命名约定
- Intent：`<domain>.<action>`，如 `hvac.set`、`navigation.search_poi`。
- Permission scope：`<resource>.<action>[.<sub>]`，如 `vehicle.control.hvac`。
- Agent ID（manifest 内）：kebab-case，如 `charging-planner`。
- Python 包目录：snake_case，如 `agents/charging_planner/`（对应 agent_id `charging-planner`）。
- proto package：`cockpit.<service>.v<n>`。
- Python 模块 snake_case，Go 包小写，TS 组件 PascalCase。

## 5. 安全红线（架构级，违反即拒绝合并）
- **车控只能经 VAL 下发**。任何组件（含 LLM/Agent）不得直接操作 CAN/SOME-IP。
- **LLM 不直连车控**：LLM 只产出"意图/计划"，车控动作由确定性 Executor 经 VAL 权限校验后执行（规划/执行分离）。
- 危险动作（`require_confirm=true`）必须用户二次确认。
- **端到端语音（S2S）会话内不得有任何执行通道**（M4）：语音大模型唯一的工具是 `escalate`——把用户原话交回文本主链，此后 planner 校验 / 权限 / VAL / `require_confirm` 闸逐字全量生效。**不得把 capability 清单注入 S2S 会话的 tools**（那等于把执行判定权交给一个不过 planner 校验的模型）。S2S 是新的「话筒」，不是新的规划入口。
- 密钥/token 不进代码、不进 commit、不进日志；用 `.env`（已 gitignore），模板见 `.env.example`。
- 敏感数据（车内音视频、精确位置、支付）默认不出车，上云最小化。
  - **唯一的受控例外：S2S 挡位下上行原始音频**（三段式只上行定稿文本）。三个条件同时成立才允许：① 设置默认 `classic`，须用户在 HMI 显式选择；② 仅在**用户主动唤醒后的交互窗内**采集（未唤醒不采）；③ 隐私声明与设置文案显式呈现该差异。任何绕过这三条的音频上行都是红线违规。
  - **视觉单帧同款三条件**（M4 P4）：设置默认关 + **端侧命中视觉触发词才抓一帧**（未命中一帧都不采）+ 文案说清。图像只在网关内存活 TTL 秒，**不落盘不落 Redis、不进 obs、不进记忆**；`camera.frame`（单帧）与 `camera.read`（连续流，维持 ❌ 禁）是两个 scope，别混用。
- **声纹不作鉴权因子**（M4 P4 红线）：`occupant_id` 只准进记忆域（recall/remember/AppendTurn/relation），**绝不进** `granted_scopes` / 权限判定 / VAL / `require_confirm` 合成 / 支付。源码级断言测试 `orchestrator/cloud/tests/test_voiceprint_not_auth.py` 钉死。理由不止「声纹可被录音重放」——**身份识别与授权是两件事**，识别错了只该损失个性化，不该损失安全。

## 6. 开发与验证

```bash
make proto        # 由 proto/ 生成 Go/Python 代码（改 proto 后必跑）
make up           # docker-compose 起全栈(PoC)
make down         # 停
make test         # 运行各服务单测 + 契约测试
make e2e          # 端到端场景测试
```
Windows 无 make 时用 `scripts/gen-proto.ps1`、`scripts/run_e2e.ps1` 等价替代（见 README）。

**工程纪律**：改完主动跑 `make test`；不要注释报错或加绕过标记来"让它跑起来"，找根因；大改动先在设计文档对齐再动手。

## 7. 当前阶段

**最新（2026-08-01）**：2026-07-26 总体验收报告 §7 的 **13 张主卡全部收口**——
M-A 测试真实性 / M-B 多乘员隔离（OwnerKey）/ M-C 可靠触达（`publish 成功 ≠ 用户收到`）/
M-D 外部生态（`声明存在 ≠ 能用`），四批全部合入 main。每批的「明确未做」逐条附判据
在验收报告 §9/§10.2/§11.2/§12.2。**✅ GitHub CI 已收口**（`176dd20` / run #232 七个 job
全绿，`#217` 之后第一次）——破点是 M-A 那批把 **Windows 假设写死了**，但最后 7 条里
**两条是真代码缺陷**：首次 canonical 晋升在 Linux 上必崩、go wrapper 的 `\"` 转义只对
Windows PowerShell 的 Legacy 传参成立。**它们能躲这么久，都是因为一段
`if os.name == "nt": return` 把校验整层跳过了**——判据、Linux 复现步骤见
`AGENTS.md` §4「CI / nightly 现状」。**nightly 同日收口**（`c75df13`）：连红三次的根因是
M5 P2 的 **hint 退役抽掉了 mock 车道的确定性基础**——mock 里没有模型，那些「端到端
路由」断言一直是正则在撑。判据改写为「**mock-safe ⟺ 这条路径不经过模型判断**」
（端侧快路径／兜底 Agent／确定性解析／协议层四类），**「有 hint 撑着」不算**。

截至 2026-07-30，Phase 1 工程化 PoC 主干、云端中枢 P0-P3、R2-R4 硬化主题（架构还债/
安全/语音回路/拒识澄清等）、可观测台（badcase 排查贯通：会话/轮次/日志/LLM + SQLite
持久化）与**旅程级验证体系**（L3 journeys + L4 HMI CDP）已落地，运行模型为
T0 端侧快路径 / T1 单次 DAG / T2 有界 Agentic 循环。

**智能化升级（对标超级 Eva，母提案 `docs/design/2026-07-24-eva-benchmark-intelligence-upgrade.md`）
M0a→M4 全部完成**，主线是「把每一种智能供给都声明式化」：

| 期 | 交付 | 一句话 |
|---|---|---|
| M0a | 数据真实性 + 确认兜底 | 运行期 mock 回退按铁律③改诚实降级；`require_confirm` 中央强制落实 |
| M0b | 规划知识 Skill 层 | 领域知识外迁 `skills/`，**加规划知识=投 skill 文件不改编排核心** |
| M1a | `submit_plan` 结构化输出 | 原生 function calling 强制合法 Plan，`_extract_json` 工程债退役 |
| M1b | 自进化 v1 + Shadow NLU | badcase→归因→补丁提案→eval 门禁→人审（nightly）；端侧切换建议出数据 |
| M2 | 执行治理 + 记忆图谱 | **Task Ledger**（长任务可查可停可诚实报告中断）+ **Outcome Verifier**（声明式执行后对账）+ T2 分档 + **偏好加权与关系边** |
| M3 | 主动治理 + 生态接入 | **统一主动引擎**（六路主动收敛到一个治理器：情境断言投递期复核/跨生产方去重/驾驶负荷延后/同窗合并成一条，**fail-open**=治理器缺席即回落直发）+ **位置提醒**（围栏边沿）+ **受控 MCP 桥**（人工准入+版本锁定+写操作生命周期五项） |

| M4 S2S 线 | 端到端语音双链路 | **单一出口 `escalate`**——语音大模型直接听直接答（首音频 609ms），需要执行或查实时信息的一律交回确定性链；**S2S 会话内没有任何执行通道**，车控不绕 VAL/确认闸。域灰度=收放工具描述不做运行时拦截 |
| M4 P4 | 声纹多用户 + 视觉入口 | **声纹**让「谁在说话」真实化（唤醒后首句边说边识别、一次唤醒锁一次、认不出一律回 primary），每个乘员记忆各自独立——**声纹绝不作鉴权因子**；**视觉**让「那是什么」能问，端侧命中触发词才抓一帧，**图像永不进对话链**（proto 里只有 frame_id） |

**M4 已收官，两条 DoD 都已兑现**（语音双链路可切换 / 多用户记忆隔离旅程）。
**M0a→M4 已通过总体验收（2026-07-26）**：七路跨阶段组合深查 + 测试真实性抽查，两个确认链
P0 与一批组合缺陷当日修复，结构性遗留已立卡——验收报告见
`docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`。

**M5 数据飞轮 P0-P2 + P3a 已落地（2026-07-29/30，架构文档 v1.15）**，主线是把「修落域 badcase 的
标准产物」从**正则**换成**数据**，并给规则装上出口。母提案
`docs/design/2026-07-28-intent-accuracy-data-flywheel.md`：

| 期 | 交付 | 一句话 |
|---|---|---|
| P0 | 修断点、立尺子 | evolve 提案半环闭合（此前 100% 自触发降级、上线四天零产出）；落域指标接活；`turns.gold_intents` 标注载体；catalog 预算修复（旧值把 navigation 整域裁出 prompt） |
| P1 | **落域范例库** `skills/exemplars/` | Planner 第三通道、权威链**最软层**——只作 few-shot 不做硬路由。**hint 写错是事故，范例写错只是噪声**，故 badcase 的默认修法是投范例 |
| P2 | 度量驱动治理 | **RoutingBench**（分布尺 N1，与只防倒退的回归闸分开）+ **hint 退役流水线**（规则第一次有出口，`route_hints` 32→12）+ 强模型影子分诊 + 端云分歧驱动标注 |

**三条会反复用到的判据**（细节见 RFC 与 `docs/reviews/eval/README.md`）：
- 软层 A/B 的 **Δ 只能在「实际注入」的子集上算**——未注入的两臂 prompt 逐字相同，翻面是采样方差；
- 规则退役**必须跨 provider 取交集 + 覆盖全部命中句**——抽样的偏差方向固定：命中面越大越容易被放行；
- **N1 涨不等于系统变好**：语料有隐藏分母、域分布偏斜七成集中在三个域、canonical 高分是 hint 钉出来的。

**P2 余项「nearby 规则群内讧」已收口 + P3a 端侧 NLU 识别侧已落地（2026-07-30）**：

| 期 | 交付 | 一句话 |
|---|---|---|
| P2 收口 | 地盘裁定台账 | 「内讧」真根因是**金标自相矛盾**（P1 把过期的地盘声明批量导入成了金标）而非规则打规则；规则存量 **12→10**；机制修复=`skills/exemplars/boundaries.yaml` 跨域边界裁定台账 + CI 阻断门禁（**相似度不能机械判定冲突**——假冲突可以比真冲突更像，故人裁一次、机器只管「不许悄悄新增」） |
| P3a | 端侧语义 NLU（**只到 shadow**） | 4 层中文 encoder（35MB/4.8ms）给出真概率，θ_high/θ_low 第一次可接。**DoD 未达线且刻意不追**：到 85% 需 θ=0.8，代价是约 1% 请求在端侧识别成**错的对象**（三闸挡危险动作，挡不住「合法但不是用户要的动作」）——产品决策，不是技术选择 |

**三条新判据**：**先测天花板再训模型**（可行性判据先行，且测量本身要人工抽查上偏）／
**校准的单调性是同分布性质**（transfer 车道 θ≥0.95 精度反低于 0.9——输入一偏，最自信那档最不可信）／
**两个评测口径都要印**（holdout 95.8% vs transfer 64.9%，差 31 点就是「同分布」的含金量）。

**M5 P0→P3a 已通过全量评审（2026-07-30）**：14 提交逐项核验，零 P0/P1 缺陷、零红线违规，
报告 `docs/reviews/2026-07-30-review-m5-data-flywheel.md`。

**P3 收尾已合入（2026-08-01）**——评审两条 INFO 项 + P3a 三张卡收口，**不改路由行为**，
两条判据入册：**「A 与 B 一致吗」先问「A 和 B 是同一个空间里的量吗」**（影子拿语料中文标签
与规则英文 object 直接比，`agree` 从来没出现过，而 P3b 的错对象率正要拿这档当分母；
补 `orchestrator/edge/knowledge/nlu_objects.yaml` 等价类台账后 agree 0%→68.8%）／
**「多给点信息总不会更差」不是证据**（78 条判别化描述渲进 planner catalog，跨 minimax +
deepseek 两档 25 条语料 ×2 轮 **Δ=0 零翻面**，代价 +1462 字符/次规划 → 否掉；真正有效应的
是 registry 语义兜底——泛化描述下「打开空调」的 top-1 竟是 scene-orchestrator，
而那是 LLM 失败时的兜底规划路径）。余项与下一步见 `AGENTS.md` §4.0。

当前事实、测试证据和待办统一维护在 `AGENTS.md`（§4 顶部有「当前进度与下一步」交接区）；
设计与落地记录见 `docs/design/`（索引 `docs/design/README.md`）。原始量产级目标和未完成项见
`docs/architecture/phase1-implementation-plan.md`，不要把当前 PoC 验收等同于该计划
全部 DoD 已完成。
