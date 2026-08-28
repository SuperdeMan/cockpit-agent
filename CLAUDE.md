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
| 移动端 mobile/ | React Native + Expo (TypeScript) |
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
                + slot_shape.py 槽位**值形状判据的唯一实现**（C3）：`wait_slot` 续接问的是
                「这句话长得像不像这个槽的值」，长得不像即换题。**形状名由 Agent 在 manifest/
                servers.yaml 的 `slot_shapes` 声明、判据本体在这里且零领域词**（源码级断言守）；
                值域不在运行期校验——桥的镜像够不着这个模块，抄一份过去就是第二份声明，
                改成由 `tests/test_slot_shape.py` 逐条比对全部声明方（契约 §9.35）
                + candidate_query.py 候选集**聚合问答的唯一实现**，四种算子：最值（哪家最晚
                关门）／合计（两个价格）／**序数取值**（第 N 个多少钱）／**重列**（「重新列出
                刚才可以选择的项目」——没有它这句话会进 Planner **重搜一遍**，C4-C）
                ——确定性、零 LLM、挂在 plan 构建**之前**；与 I-052 那条弃权守卫**方向相反、判据同源**
                （契约 conventions §9.27）。**第三种算子是被两条守卫之间的缝逼出来的**：
                前两种只认聚合、弃权那条只管零候选，于是「有候选的单项查询」两头不管，
                模型从缝里编了一个价格出来 ⇒ **凡是「系统持有的事实」，判据面就得是闭合的**。候选项白名单
                `_CANDIDATE_ITEM_KEYS` 是**与产生方的契约**，字段名不许猜——改产生方
                item 字段要同步 `test_candidate_sets.py::_PRODUCER_SHAPES`。
                候选集**下发给 Agent** 走 `context.candidate_downlink` 的最小投影
                （只有序号与名字）+ `engine._apply_focus_meta` 按 `context_scopes:
                [candidates]` 门控——「值得跨轮留住」与「可以离开编排」是两个问题，
                两张表刻意分开（契约 §9.28）。**「这句话在说哪一组」的判定住在
                `context.resolve_candidate_scope`（唯一实现）**：组标签由产生方经
                保留键 `_candidate_label` 声明（判据同 `_fallback`——编排看不出
                `mcd.menu` 那组该叫「麦当劳」，把商户名写进编排核心正是 R2.1 禁的），
                零命中逐字退回 `newest_candidate_set`。下发面选组是**另一份判据**
                （`candidate_set_for`，按步的 intent 域，零领域词）——编排知道这一步
                姓什么，但不知道用户嘴里那个词姓什么，合成一条会逼编排认识商户名
                （契约 §9.32）
llm-gateway/    LLM 多模型网关（所有 LLM 调用的唯一出口）；音频面同门：批/流式 ASR·TTS
                + s2s/ 端到端语音会话（M4；协议/provider/会话/回灌四层，换厂商只加 provider 子类）
                + speaker_embed.py 声纹提取（音频→向量，**不持模板**）
                + vision_frames.py 视觉单帧内存库（图像只在此活 120s，**不落盘不落 Redis**）
registry/       Agent 注册中心
memory/         记忆/画像服务（+ offer_admission.py G7 询问式提醒建议的**准入判据唯一声明处**：
                「用户说出了时刻（不是日期缺省的 00:00）+ 至少提前 30 分钟 + 剥完时间词还剩一件事」，
                零 LLM；判据取**形态**不取关键词——天气查询被挡住不是因为它长得像查询，
                是因为它**没有时刻**。契约 conventions §9.30）
agents/         所有 Agent；_sdk/ 是公共 SDK，每个 Agent 一个子目录
                （+ mcp_bridge/knowledge/merchant_specs_observed.yaml **商户规格组真机观测台账**：
                `servers.yaml` 的 `input_schema` 声明的官方组名/项名必须在它里面出现过，
                方向单向、由 `scripts/probe_merchant_specs.py` 扫出来——**外部系统持有的值域，
                我们这一侧的声明必须有机器闸对着真机**，写进契约的仍然可以是猜的，契约 §9.31）
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
mobile/         Android 陪伴端 App（React Native + Expo，TypeScript）——与 hmi/ 共存的第二个
                用户端，同一后端大脑；只读引用 hmi/src 纯逻辑模块，共享台账
                mobile/shared-allowlist.json，改共享面两边一起看。
                ⚠ 它有**自己的密钥落点** `mobile/.env.local`（已 gitignore，与根 `.env` 并列
                而非替代）：目前放高德 Android key，构建期经 `app.config.ts` 注入 AndroidManifest。
                同红线——不进代码、不进 commit、不进日志。
                ⚠ 引入**原生支撑的组件**（地图/SVG/音频这类带 ViewManager 的）前必须先探原生在场：
                原生缺席时 Fabric 在挂载期原生线程抛，React ErrorBoundary **兜不住**、整屏红屏
                （计划文档坑账 §9.27；M5 的 OTA 只推 JS 不推原生，同形态）
dashboard/      React 开发/演示可观测台（不进入车控执行主链）
deploy/         docker-compose / helm / k8s
scripts/        codegen、构建辅助（含 gen-certs.* 生成 mTLS 证书）、自进化流水线 evolve.py（M1b，nightly 经 Task Scheduler）
runtime/        共享运行时（gRPC keepalive/优雅停机/mTLS 工厂；+ 主动消息出口 proactive.py——全 Python
                服务经此建 channel/server、发主动消息）。**「同一件事只许一份实现」的那些判定住在这里**，
                落点判据是**镜像依赖闭包**（端侧镜像没有 `agents/`、云侧编排镜像也没有，而它们都 `COPY runtime`）：
                clock.py 业务时区墙钟（容器 TZ=UTC，写「几点」前先读它）／polarity.py 指令极性
                （「车窗别开」，端侧与 reminder 共用；其 `NEG_WORDS` 也是云侧 actionability
                「禁止式否定」特征的词表来源，不许抄第二份）
                ／clause_split.py 中文复合句**分隔符表的唯一声明处**（C5/C6）：端侧拆
                「一句话里几条车控指令」、云侧拆「几个诉求」——**表共用、语义不共用**，
                端侧特有的「和」二次拆分/并列对象展开/场景句拦截仍留在端侧
                ／cntime.py 中文时间词（时段词·日词含英文别名·
                中文数字·12h→24h 修正；此前 timeparse/timewindow/weather 三份各自演化，同一句话给三个答案）
                ／slot_fidelity.py 下发前的**槽值原话回查**（planner 转述丢限定词，逐维补、三道闸，
                唯一挂点 `executor._resolve_slot_refs`；同处还有 `undeclared_slots`——**契约**比原话
                少了一维时只观测不改值，判据零领域词）／openhours.py 营业时间窗口解析
                （nearby 筛「此刻开着的」与云侧算「谁最晚」共用一份；跨零点归一成 >1440 可直接
                比大小；判不出返回 None 不是 0——0 会让「时间未知」赢下「哪家最早关门」）
                ／safety_signal.py 安全信号判据（告警等级·告警名字·驾驶员状态；2026-08-27
                从 `agents/_sdk` 迁入——**第四个消费方是云端编排的输入侧扫描，而云侧镜像没有
                `agents/`**）／question_shape.py 「这句话是在问还是在下指令」（端云共用；云侧
                「问句 + 写车控步」安全闸的输入，判据零领域词、由源码级断言守）
                ／intent_effect.py 只读操作名的唯一声明处（对象级 `effect_of` 与意图级
                `is_write_intent` 是同一件事的两个粒度，集合只许有一份）
                ／session_facts.py 「系统持有的会话事实」的判据与话术（挂起状态·数据源·
                执行史三条读出口；2026-08-28 从 `agents/chitchat/src/audit.py` 迁入——
                **两个消费方够不着彼此**：编排层的确定性短路与 chitchat 的兜底闸。
                Q6 把闸建在 chitchat 里，别的域接走就够不着，判据一直对、只是没人能用）
                ／profile.py 部署形态闸／admission.py
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
   - **会追问自由文本槽的 Agent**（返回 `NEED_SLOT` + `missing_slots`）在该 capability 加 `slot_shapes`（`槽位名 -> 形状名`，C3）。补槽续接的默认是「**槽值必须长得像这个槽**」，声明了形状，用户下一句长得不像时编排判换题、不再把整句塞进槽里；不声明则行为与此前逐字一致。形状名的值域是 `orchestrator/cloud/slot_shape.py::SHAPES`（当前 `order_id` / `item_name`），拼错由 `tests/test_slot_shape.py` 当场报红。**别在 Agent 里自己判「这句是不是答案」**——那是编排的事，判据抄两份就会给同一句话两个答案（契约 §9.35）。
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
   ⚠ **规则产的 `operate`/`attr`/`mode` 必须与该对象的 `operates`/`attrs`/`modes` 对得上**
   ——方向盘加热就是在这里断的：规则产 `operate=open`，而它的 `operates` 是 `[set,inc,dec]`，
   声明齐全、名字也对、端侧照样秒回「暂不支持哦」（QA I-004，契约 §9.29 的五段链）。
   新对象**要在 `orchestrator/edge/tests/corpus/vehicle_objects.yaml` 留一条识别语料**，
   那条语料同时验「认出哪个对象」与「这条命令 VAL 收不收」。
4. 跑 `test/eval_capability_integrity.py` + `scripts/check_intent_gate.py` +
   `test/smoke_edge.py` + `pytest orchestrator/edge/tests`。
   **漏一处就有具名红灯**——这是 B4 存在的理由：除雾能力那次漏了对抗覆盖，
   因为「要改哪些地方」只活在某个人的记忆里。
   ⚠ **门禁覆盖 ①②⑤ 三段，不覆盖 ③④**（规则产得出命令 / 命令过得了校验）：它逐条跑的是
   `edge_call.decode_intent` 那**一个**产出方且跳过 `_validate_command`。③④ 由
   `test_classifier_exit_parity.py` 与 `test_corpus_objects.py` 的两条 VAL 校验断言守。
   ⚠ **③′「规则吐的对象名知识库认不认」是第三条**（2026-08-28 补，QA N8）：
   上面两条都只走**有人写过用例的那些对象**，而这一段断了的时候恰恰没人写过用例
   （「胎压是多少」因此长期答「暂不支持哦」）。`test_rule_object_reachability.py`
   改成**从产出方静态盘点**（AST 取全部 `_s(...)` 的对象名），不需要有人先想到写用例。

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

### 可切换远程真栈红线（切云已于 2026-08-18 完成）

**当前形态**：`dev-stack.local` = **`target=cloud`**；精确云端 release、健康状态与当前验收结论
只查 `AGENTS.md` §4.0，**本文件不维护易腐 SHA/计数**。本地 Docker 已按授权停止（**只 stop
不 down**：容器、卷、镜像、迁移包全部保留，可随时回切）。
下列红线**与当前形态无关，长期有效**——它们约束的是动作，不是某一次切换。

真栈动作前先运行 `python scripts/dev_stack.py target show` 确认目标。
任何真栈动作（包括运行 E2E、Compose 或 `make up`）前，必须由统一入口按仓库根目录定位并读取
`dev-stack.local`。它是仓库根目录的 Git-ignore 文件，不能按当前工作目录误判缺失：文件
缺失按 `target=local` 处理，文件损坏则 fail closed。改动任何脚本目录或 manifest 前同样
必须读取该文件。只允许 `target=local|cloud`，不得在其中保存
token、密码、私钥或 URL。`target=cloud` 禁止启动本地 Compose，本地仅用于编辑、单测、静态
检查和 Vite；`target=local` 继续只用根 `compose.yaml` / `make up`，根 `.env` 仍是唯一运行时
来源。

cloud deploy 只接受干净、已提交、main 可达的 SHA，不自动 commit/merge/push；`git push`
仍需单独授权。未显式标记 `remote_safe` 的 E2E 不得在 cloud 缺省运行；
`remote_mutating=true` 只允许精确 `--id` + `--allow-mutating`；该开关不替代支付、商户写、
真实车控、删数据或系统配置的本轮人工红线授权。**不得擅自切换 `target`——两个方向都算**：
写 `dev-stack.local` 需要当轮授权（2026-08-18 那次切 cloud 就是这么走的）；
也不得停止另一个 agent 正在使用的本地 Docker。

本地三存储迁云使用 `scripts/cloud_data_migration.py`（**首次真实迁移已于 2026-08-18
完成**，工具契约对后续任何再迁同样有效）：online 不停本地写入，final 必须取得
停写授权并确认其他 agent 已结束；两阶段均为 replace 而非 merge。`apply/rollback` 只有显式
`--apply` 才允许写入。迁移不修改 `.env`、安全组、Tailscale、CI/CD、systemd 或 schema，
不删除任何本地/云端卷、备份、release、镜像或迁移包；三存储失败必须整组恢复。
- 敏感数据（车内音视频、精确位置、支付）默认不出车，上云最小化。
  - **唯一的受控例外：S2S 挡位下上行原始音频**（三段式只上行定稿文本）。三个条件同时成立才允许：① 设置默认 `classic`，须用户在 HMI 显式选择；② 仅在**用户主动唤醒后的交互窗内**采集（未唤醒不采）；③ 隐私声明与设置文案显式呈现该差异。任何绕过这三条的音频上行都是红线违规。
  - **视觉单帧同款三条件**（M4 P4）：设置默认关 + **端侧命中视觉触发词才抓一帧**（未命中一帧都不采）+ 文案说清。图像只在网关内存活 TTL 秒，**不落盘不落 Redis、不进 obs、不进记忆**；`camera.frame`（单帧）与 `camera.read`（连续流，维持 ❌ 禁）是两个 scope，别混用。
- **声纹不作鉴权因子**（M4 P4 红线）：`occupant_id` 只准进记忆域（recall/remember/AppendTurn/relation），**绝不进** `granted_scopes` / 权限判定 / VAL / `require_confirm` 合成 / 支付。源码级断言测试 `orchestrator/cloud/tests/test_voiceprint_not_auth.py` 钉死。理由不止「声纹可被录音重放」——**身份识别与授权是两件事**，识别错了只该损失个性化，不该损失安全。

## 6. 开发与验证

```bash
make proto        # 由 proto/ 生成 Go/Python 代码（改 proto 后必跑）
make up           # docker-compose 起全栈(PoC)      ← 仅 target=local
make down         # 停                               ← 仅 target=local
make test         # 运行各服务单测 + 契约测试        ← 两档都能跑，不需要 Docker
make e2e          # 端到端场景测试                   ← 仅 target=local
```
Windows 无 make 时用 `scripts/gen-proto.ps1`、`scripts/run_e2e.ps1` 等价替代（见 README）。

> ⚠ **当前 `target=cloud`，标了「仅 target=local」的三条现在不能直接跑**（起本地 Compose
> 是红线）。云档对应动作：整栈状态 `python scripts/dev_stack.py status`、
> 端到端 `python scripts/run_e2e.py --target cloud`（缺省只跑 2 条 `remote_safe`）、
> 前端 `python scripts/dev_stack.py hmi` / `dashboard`。
> **要回本地真栈**：`target set local` → 人工启动 Docker Desktop → `make up` → `status`。
> 单测与静态检查两档都不需要 Docker。详见 `docs/dev-guide.md` §可切换真栈。

**工程纪律**：改完主动跑 `make test`；不要注释报错或加绕过标记来"让它跑起来"，找根因；大改动先在设计文档对齐再动手。

**测试若替被测系统提供了某个前提，那条前提就不再被验证；验证多轮系统必须跑
「失败态之后再说一句」和 ≥3 轮。** 2026-08-13 实证两例：① `merchant.read/write`
在 `servers.yaml` 里被要求，却**全仓没有任何发放入口**，真实商户下单在实栈里从来不可达
——而 `test/e2e_merchant_mcp.py` 自己往 meta 塞 `granted_scopes`，于是唯一能抓到这个洞的
检查被测试自己短路，绿了两个月。② 把商户拒绝改成 `NEED_SLOT`（声明缺的三个门店槽用户
**永远填不了**）造成会话挂起、吞掉后续每一句，问麦当劳答瑞幸——而我的探针全是干净会话
与顺利路径，挂起黑洞只在**拒绝之后**才出现；焦点被覆盖只在**第三轮**才暴露。
推论：**happy path 和干净会话证明不了会话状态是对的**，CDP 类用例也不能只验到
「卡片渲染出来」——bug 常常活在卡片之后。

**模型输出是不可信输入，防御要一路防到真正会被拿去 hash / 拿去 split 的那个值，
不是防到最外层容器为止。** 2026-08-03 实证：`depends_on` 已有「非 list 就归空」的防御，
但 `[["s0"]]` **是** list、`isinstance` 照过，一路走到 `dep in valid_ids` 才崩
`TypeError: unhashable`，把整趟规划抛了出去（`50c2b3f`）。归一时**非法元素直接丢，
不做 `str()` 转换**——转出来的值匹配不上任何东西，却会在日志里留下一个不存在的 id。

## 7. 当前阶段

**Phase 1 工程化 PoC**，运行模型 T0 端侧快路径 / T1 单次 DAG / T2 有界 Agentic 循环。
已落地并验收的主题只列名目（能力细节见架构文档，逐批流水 `docs/agents-history.md`，
本节 2026-08-19 起不再复述批次叙述）：工程化主干与云端中枢（P0–P3）、R2–R4 硬化、
可观测台与旅程级验证（L3 journeys + L4 HMI CDP）、智能化升级 M0a→M4（数据真实性 /
Skill 层 / submit_plan / 自进化 nightly / Task Ledger + Outcome Verifier / 记忆图谱 /
统一主动引擎 / 受控 MCP 桥 / S2S 双语音链路 / 声纹多用户 + 视觉入口）、M5 数据飞轮
（落域范例库 / hint 退役 / RoutingBench / 跨域边界台账 / 端侧语义 NLU shadow）、
EVA 指令集两轮对标（history §33–§40）、支付基础设施真实化 + 麦当劳/瑞幸官方 MCP
复合工作流（**系统不执行最终付款**，契约 §9.17/§9.9）、商户 badcase 收口
（history §30–§32.2）。两家商户凭证仍是服务级全局 token/账号、支付 host 依赖运行时
安全配置——PoC 限制而非多乘员量产账号模型。

**探索式真实用户 QA 规则**：2026-08-15 的 533 轮/58 问题批与四阶段编号序列已经归档，
不再写成“进行中”。当前进度和接手入口只查 `AGENTS.md` §4.0/§4.1；最新 MiniMax 云端复验
的问题与 trace 只查 `docs/reviews/2026-08-26-minimax-cloud-qa-findings.md`，不得把探针自动 PASS
数写成业务全绿。历史根因与契约演进查 history §41–§71。
⚠ 该轮与 EVA 批验的不是同一个面：EVA 验「能力面有没有那个维度」，QA 验**会话状态、
归属、审计与真实性**——两边读数不矛盾。
⚠ 2026-08-19 云端复跑掀开的「记忆召回失效」当日修完——真根因是 `AUTH_TOKENS` 漏写
user_id 段（网关 fail-closed 守卫 + 云端 E2E 记忆探针已补，history **§59**）。

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
