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
                commands.yaml 里每个对象还声明 `require_confirm`（危险动作，B1 已下沉成
                VAL 的执行判据）、`effect`（read/write）与 `edge_intents`（**端侧意图名单
                的唯一声明处**，`VEHICLE_INTENTS` 由它派生，B4）；`risk` 刻意**不落声明**、
                由 `capability_meta.risk_of` 派生——B1 把危险与否收敛成了 require_confirm
                这一个权威，第二份声明只会漂移
                + nlu_objects.yaml **对象等价类台账**：语料中文标签／VAL object／规则
                object 三套命名归并，人裁一次机器守「不许悄悄漏」，同 boundaries 形态
                + capability_exemptions.yaml 能力完整性门禁的豁免台账，逐对象逐车道、
                禁通配符、必须写 reason）；
                cloud/ 云端 Planner（+ retry_policy.py **重试规则的唯一声明处**：13 条
                RetryPolicy 按声明序求值，**加重试规则=改表不改主循环**——同 skills 那条
                「加规划知识=投 skill 文件不改编排核心」；清单与代码表由测试逐列比对，
                改一处不改另一处即红；消融开关 `PLANNER_RETRY_DISABLE`（B5）
                + stream_state.py D0/T2 **流式判定的唯一实现**，两条路径共用状态推进
                与三条决策函数——判定抄两份正是 B1 那个 bug 的成因（B5）
                + actionability.py 可执行性**形态**判定，**shadow 只写观测不进决策**：
                特征全是封闭虚词类、不许出现任何领域词（源码断言从 commands.yaml
                派生词表比对）；REJECT 声明但 v1 不产出（B6）
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
payment-gateway/  统一支付网关（Agent 不持支付凭证；2026-08-11 真实化：providers/
                支付宝当面付+微信 v3 Native 双渠道自实现、worker.py 轮询推进终态、
                **Capture=确认后亮码**、PAYMENT_REAL_SCENES 场景白名单 fail-closed
                ——契约 conventions §9.17，改支付面先读它）
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

### 新增一个**端侧车控能力**的标准流程（B4，与新增 Agent 是两件事）
1. `python scripts/gen_capability_skeleton.py <object> --display-name <中文名> --operates open,close`
   ——它复用门禁的车道函数算出缺口，产**待办清单不是成品**（刻意不落盘：这些内容全是人裁的，
   会被生成器覆盖的文件留不住人改过的东西）。
2. 按骨架填 `commands.yaml`（含 `require_confirm`/`effect`/`edge_intents`）、`responses.yaml`、
   `nlu_objects.yaml`；话术 key 按 `<object>_<on|off|操作>_success` 命名即可自动接上。
3. 人写四处生成不了的：`fast_intent.py` 触发规则（+ `LOCAL_INTENTS`，那是**路由**判定，
   与 `edge_intents` 这个**能力目录**是两个问题）、VAL `_simulate` 分支（档位/开度型必须写，
   开关型可落通用兜底）、对抗覆盖（每 intent 正 2/硬负 2/对照 1）、迁移探针基线。
4. 跑 `test/eval_capability_integrity.py` + `scripts/check_intent_gate.py` +
   `test/smoke_edge.py` + `pytest orchestrator/edge/tests`。
   **漏一处就有具名红灯**——这是 B4 存在的理由：除雾能力那次漏了对抗覆盖，
   因为「要改哪些地方」只活在某个人的记忆里。

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

**模型输出是不可信输入，防御要一路防到真正会被拿去 hash / 拿去 split 的那个值，
不是防到最外层容器为止。** 2026-08-03 实证：`depends_on` 已有「非 list 就归空」的防御，
但 `[["s0"]]` **是** list、`isinstance` 照过，一路走到 `dep in valid_ids` 才崩
`TypeError: unhashable`，把整趟规划抛了出去（`50c2b3f`）。归一时**非法元素直接丢，
不做 `str()` 转换**——转出来的值匹配不上任何东西，却会在日志里留下一个不存在的 id。

## 7. 当前阶段

**Phase 1 工程化 PoC（截至 2026-08-04）**，运行模型 T0 端侧快路径 / T1 单次 DAG / T2 有界
Agentic 循环。已落地并验收的主题：工程化主干与云端中枢（P0-P3）、R2-R4 硬化（架构还债/安全/
语音回路/拒识澄清）、可观测台（badcase 排查贯通）与旅程级验证体系（L3 journeys + L4 HMI CDP）、
智能化升级 M0a→M4（数据真实性、Skill 层、submit_plan、自进化 nightly、Task Ledger + Outcome
Verifier、记忆图谱、统一主动引擎、受控 MCP 桥、S2S 双语音链路、声纹多用户 + 视觉入口）、
M5 数据飞轮（落域范例库、hint 退役出口、RoutingBench、跨域边界裁定台账、端侧语义 NLU shadow）、
支付基础设施真实化四批（2026-08-11：支付宝/微信双渠道扫码收单 + 商户收银登记 +
麦当劳/瑞幸官方 MCP 真机激活只读三件 + 端到端体验层 speech_mode，契约 §9.17/§9.9，
流水 history §28）。

三次全量验收留档，均通过：M0a→M4 总体验收
`docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`（其 §7 的 13 张主卡已于 2026-08-01 全部
收口）；M5 全量评审 `docs/reviews/2026-07-30-review-m5-data-flywheel.md`（零 P0/P1）；验收实现
复核 + badcase 智能化评审 `docs/reviews/2026-08-02-review-acceptance-impl-and-badcase-intelligence.md`
（13/13 为真；「落域准确 ≠ 智能」标本与全声明式修法）。

十一条日常工作规则（从上述验收沉淀，做落域/意图类工作先记住）：
- **不要为了某个模型的问题去改动 gate 案例集**（泓舟 2026-08-10）。案例集是**尺子**，
  描述「我们要求系统做到什么」；某个 provider 当下做不到是被测对象的读数，不是尺子该
  让步的理由。与规格「换出带病候选」不矛盾——那条针对**被测对象错了**，
  而「用例难、模型弱」正是要求留在门禁里的一类。动案例集前还要问一句：
  **正式 baseline 还认不认得这个案例集**（一变就带 `removed_cases`，baseline 写入被冻结）。
- **加了知识要拿对照跑证伪，不能只看它有没有被注入。** 2026-08-10 实测：一条 guide 四次
  全部成功注入（`@lex:11` 未被裁），通过率却 4/10 → 1/10，退回后回到 7/10（p≈0.02）。
  **「知识在场」和「知识有用」是两件事**；只看注入名单会把有害改动读成中性。
  推论：**「多给点信息总不会更差」不是证据**——加知识还会挤 `SKILL_BUDGET`，
  把更相关的 guide 挤成 `!clipped`。
- **示范输出形状之前先确认当前输出通道。** few-shot 抄错通道（文本形状 vs
  `PLANNER_TOOLCALL=on` 的 submit_plan schema，后者刻意不含 `clarify`）会让模型输出被判
  解析失败、退成兜底——把「模型判断错」变成「模型说不出话」，还凭空制造未声明兜底。
- **修落域 badcase 的默认产物是范例与知识**（`skills/exemplars/`、`skills/guides/`、
  `boundaries.yaml` 台账），不是正则——hint 写错是事故，范例写错只是噪声。
- **组合意图先拿 goal 对照 steps**（goal 说推荐而 steps 无推荐步＝可检测的缺口信号，
  2026-08-02 评审报告 §2.4）。`cloud.planning` 的 `goal_value_dropped` 是它**判到值一级**
  的机器版（goal 里有数字而全部 slots 无数字，见 `docs/conventions.md` §8.1）。
- **量分布是为了排优先级，不能代替逐条拉证据**（2026-08-04）。`unstable` 是个混装标签：
  「51% 的边界句」和「10% 通过率的稳定缺陷」在分布口径里长得一模一样——
  单独跑一遍才看得出哪些其实已经是稳定红。
- **诊断行里 `exemplars=[]` 有两种意思**：检索没够着 / **这个域压根是空的**。
  后者真实发生过两次（hvac 域、shop 域），且第二次是**门禁自己造成的**——
  typo 守卫只读 `manifest.yaml`，而 `mcp-bridge` 的能力由 `servers.yaml` 启动期合成。
  判据：**「能力从哪里声明」和「能力写在哪个文件」是两件事。**
  同族第三例（2026-08-10）：新增 active intent 却没补对抗覆盖，而 `--list` 只**展示**
  gap、`--strict` 才**阻断**——**同一个门禁在两种模式下严厉程度不同，报绿之前先确认
  自己跑的是哪种模式**；配套老账：`cmd | tail; echo $?` 拿到的是 `tail` 的退出码。
- **记录一个缺陷不等于修它**（2026-08-10）。「查穿衣指数」被判成股指，这件事作为反例
  在 `nlu_objects.yaml` 里躺了很久（用来说明「不许把真 badcase 洗成 agree」），
  **缺陷本身一直没人修**。台账里的反例读起来很像已处理项——**定期问一句「这条是记着
  的，还是修好的」**。修好之后那道断言还要留着，它从记录变成回归探针。
- **读任何落域数之前先看输出通道**（2026-08-10）。走成 `toolcall` 与掉进 `salvage`
  的轮，模型分别是在 schema 里和自由文本里作答，**输出分布不是一回事**——同一条用例
  MiniMax 内部 `toolcall` 91% vs `salvage` 50%（p≈0.036）。走成的比例还是 **provider
  属性**（MiniMax 45~48% / DeepSeek 100%），所以它也是**唯一该跨 provider 比的那类指标**：
  协议层指标跨档比有意义，语义层（通过率）跨档比没有意义。
- **A/B 之前先证明两臂真的不同**（2026-08-10）。给 `replan()` 加了形参、测试替身没跟着
  传，24 个样本两臂**逐字相同**，读数方向还正好是「看起来变差了」——差一点就据此否掉
  自己刚写对的守卫。**「我改了东西」不等于「被测的那条路径变了」**，先验证再读数。
- **诊断出一个洞，不等于这个洞就是病因**（2026-08-10）。「空调先别关」检不回任何范例
  （0.305 vs 阈值 0.34）是可测量的事实；补上一条稳定能被检回的范例之后，通过率
  15/18 → 16/18、**p=1.000**，按纪律退回。**「缺 X」与「补上 X 就能修」是两个命题**，
  第二个要单独证。

当前事实、测试基线、活跃待办与交接摘要统一维护在 `AGENTS.md` §4.0（新会话从那里开始）；
逐批历史流水在 `docs/agents-history.md`（只进不出）；设计与落地记录见 `docs/design/`（索引
`docs/design/README.md`）。原始量产级目标和未完成项见
`docs/architecture/phase1-implementation-plan.md`，不要把当前 PoC 验收等同于该计划全部 DoD
已完成。
