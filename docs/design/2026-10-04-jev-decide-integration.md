# Jev 判别层接入：执行计划（JV01 → JV03）

> 2026-10-04。状态：JV01 已部署 `b84628b5`（缺省全关、零外呼，云端核对确认）；首轮离线中文评测完成（§8）；JV02/JV03 首片（受话 shadow）已上线 `3f948538`，范围含真实用户（§9、§10）。按用户 2026-10-04「把 jev 接入的计划也排进来」立项，并已提供 API 凭证
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

> **2026-10-04 用户决定**：第 2、3、4 项批准，且第 4 项选「包含真实用户」——真实会话的原话（不带身份标识）也发给 typesafe.ai 做受话对照
> （`DECISION_SHADOW_SCOPE=all`）。第 1 项不需要（cloud 档不用本地栈；本机评测只在进程内注入凭证）。

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

## 7. JV01 实施记录（2026-10-04）

- 外部事实（当天核对官方文档）：固定版本 `jev-1.13.0`（`jev-latest` / `jev-preview` 目前都指向它，生产不用别名）；
  `POST https://api.typesafe.ai/v1/systemone`，`Authorization: Bearer`；返回 noul / choice（choice、probabilities、confidence）/
  score（score、legend、probabilities、confidence）与 usage；错误 401 / 422 / 429 / 529；输入 $0.042 / 百万 token、输出免费；
  官方限流 100K token/s、80 请求/s；英语是主训练语言，中文需验证。
- 交付：`llm.proto` 新增 `Decide` 与显式消息（状态枚举、Noul / Choice / Score 的 oneof、用量 known 标记）；
  `runtime/decision_contract.py`（任务规格、payload 校验、整批答案校验，纯函数）；`llm-gateway/decision_specs.py`（版本化 allowlist，
  JV01 只有 `smoke` 冒烟任务）、`decision_provider.py`（唯一外呼，零重试，长连接复用，请求头与正文不进日志；确定性假 provider）、
  `decision_service.py`（总开关、任务 allowlist、payload 拒绝、预算按服务端上限截断、并发外呼与整体截止、用量记账）；网关 `Decide` 接线。
  SDK / Cloud 的 Decide 客户端随第一个消费方（JV02 / JV03）一起加，不提前放没有调用方的代码。
- 配置（服务端受控，客户端 meta 无权开启）：`DECISION_ENABLED`（缺省 false，false 时零外呼）、`DECISION_TASKS`（逗号分隔的任务 allowlist，缺省空）、
  `DECISION_MODEL`（缺省 `jev-1.13.0`）、`DECISION_MAX_BUDGET_MS`（缺省 1500）、`DECISION_BASE_URL`、`TYPESAFE_API_KEY`。
- 本机真请求冒烟（`scripts/smoke_decide.py`，凭证只注入这一个进程，5 句合成文本）：首轮每次新建连接，经本机代理建连 7–17 s、三条超时；
  改为长连接复用、直连后首个请求 3.4 s（含跨境 TLS 握手），之后 250–281 ms；每次约 355 个输入 token。
  判断方向全部合理（「打开空调」0.98、「空调怎么打开？」0.07、「把车窗关上」0.98、「今天深圳天气怎么样」0.05、「别开车窗，只说说怎么开」0.29）。
  云端主机的网络路径（是否需要代理、建连与稳态时延）在 §5 第 2、3 项获批后单独测。
- 测试：契约 6 条（规格自检、payload、问题渲染、整批合法、任一项不合格整批作废 12 种、模型漂移与等级写法）；网关 11 条（缺省零外呼、
  任务闸先于外呼、合法路径的类型化答案与用量、预算截断、慢供应商按预算超时、整批作废与用量未知、错误映射不重试、请求形状、
  厂商错误分类、无凭证不碰网络、服务端接线）。变异 16 处：15 处判红，1 处为等价变异（非 OK 时答案本就为空，答案守卫是冗余防御）。

## 8. JV03 首轮离线中文评测（2026-10-04，本机进程内注入凭证，仅仓库测试语料）

`scripts/eval_decisions.py`：评测专用任务规格（不进网关 allowlist），每题一个 Noul，中英两种问法各问一遍；
语料是仓库里的标注集——拒识 47 条（`rejection_cases.yaml`，29 条对助手说话）、澄清 32 条（`clarify_cases.yaml` + 对抗语料里
「必须澄清 / 禁止澄清」且无上下文的轮，9 条应澄清）。158 次调用，输入 64,672 token（约 $0.003），时延 p50 266 ms / p95 890 ms（并发 4）。

| 任务（问法） | AUC | 0.5 阈值准确率 | 弃权带 0.3–0.7：采纳 / 采纳错误 | 弃权带 0.2–0.8：采纳 / 采纳错误 |
|---|---|---|---|---|
| 受话（英文） | 1.00 | 97.9% | 72% / 0% | 49% / 0% |
| 受话（中文） | 0.997 | 91.5% | 77% / 2.8% | 57% / 0% |
| 需要追问（英文） | 0.89 | 84.4% | 81% / 11.5% | 56% / 5.6% |
| 需要追问（中文） | 0.89 | 84.4% | 84% / 11.1% | 59% / 5.3% |

- 受话判定质量高：中文问法漏掉的是情绪表达（「好烦啊今天」「好无聊啊」——产品定义里算对助手说的）与上下文指代，英文问法基本判对。
  ⇒ JV03 的首个 shadow 任务选「受话」，与规划器现有的 `addressed` 判定同快照对照，规格用英文问法。
- 需要追问：漏判的三条全是「能力层面的二义」（「找个充电的地方」附近充电站 vs 沿途补能、「我想去趟三里屯」导航 vs 周边、
  「附近有什么好玩的帮我安排一下」周边 vs 行程）——不给候选能力就判断不出；误报是依赖上下文的指代（「巴西那场帮我看看详情」）。
  ⇒ 不单独上线，等 JV06 把候选能力放进 state 后再评。
- 样本很小（47 / 32），只作方向判断，不作阈值冻结；校准与冻结要按研究方案 §11 扩到分组切分的标注集。

## 9. JV02 / JV03 首片：受话 shadow（2026-10-04）

- 网关 allowlist 加 `addressed` v1（§8 里表现最好的英文问法）；云端客户端 `Clients.decide` 复用到 llm-gateway 的现有连接。
- `orchestrator/cloud/decision_support.py`：规划之后（拿到规划器自己的 `addressed`）异步问一次，结果落 `decision.shadow` span：
  状态、`p_true`、与规划器是否一致、是否语音来源、token 数——**不带原话**。只观测、不等、不改结果；失败只记一笔。
- 配置（cloud-planner 读，服务端受控）：`DECISION_ADDRESSED_MODE`（off 缺省 | shadow）、`DECISION_SHADOW_SCOPE`
  （synthetic 缺省：只对合成 E2E 会话——运行器签发的 E2E 能力或 `e2e-` 合成用户；all 要先定 §5 第 4 项的数据策略）、
  `DECISION_SHADOW_BUDGET_MS`（1500）、`DECISION_SHADOW_MAX_INFLIGHT`（4，满了丢样本记 dropped，不排队）。
  网关侧仍要 `DECISION_ENABLED=true`、`DECISION_TASKS=addressed` 与凭证才会真外呼。
- 测试：缺省关闭与范围外零调用、合成会话对照规划器且 span 不带原话、范围 all、失败只记不抛、并发上限丢样本、超长 / 空文本跳过、
  引擎在规划后带规划器结论调度（结果照旧由规划器决定）。变异 9 处全部判红。
- 上云（用户已批准，见 §5）：`deploy/docker-compose.yaml` 给 llm-gateway 透传 `DECISION_ENABLED` / `DECISION_TASKS` / `DECISION_MODEL` /
  `DECISION_MAX_BUDGET_MS` / `TYPESAFE_API_KEY`、给 cloud-planner 透传 `DECISION_ADDRESSED_MODE` / `DECISION_SHADOW_SCOPE`（缺省值都是关闭）；
  云端共享 `.env` 写入凭证与 `DECISION_ENABLED=true`、`DECISION_TASKS=addressed`、`DECISION_ADDRESSED_MODE=shadow`、`DECISION_SHADOW_SCOPE=all`。
  云端主机到 Jev 直连实测首个请求 0.56 s、复用连接 0.21 s，不需要代理。

## 10. 受话 shadow 上线（`3f948538`，2026-10-04）

- 验证：`8eb4e892` 精确 SHA 全量 11052 / 35 / 12（1 个 xdist worker 在 YAML 解析时进程崩溃、该文件单独重跑 23/23；另一趟卡死定位为 xdist 启动时 `platform._wmi_query` 挂住，环境问题）、五道门禁、变异 9 条全部判红；CI 全绿；`3f948538`（compose 透传）相关测试 1904 条通过、CI 全绿；部署 `3f948538`：status 5/5，verify `20261004T131707Z-3f94853.json`；云端核对网关判别已启用（任务 addressed、`jev-1.13.0`、凭证已配置）、cloud-planner 为 shadow / all；真栈 shadow 探针（`scripts/probe_decision_shadow.py`，12 句只读：查询 / 情绪 / 乘客对话 / 播报，一半语音来源）PASS：12/12 有 shadow、11 OK + 1 超时，Jev 判对 10/11、规划器判对 9/12（把播报腔、对孩子说的话、乘客对话判成对助手说的），零动作、span 零原文；shadow 时延约 600 ms 为主、另有 1.3–1.4 s 一簇。
- 观察：规划器在文字输入上倾向判「对助手说的」（打字本来就是对助手说），所以文字轮的分歧不代表规划器错；语音来源的分歧才是拒识判定真正要比的。
  离线校准与阈值冻结要按语音来源分开统计。
- 已修（下一个发布）：shadow 缺省预算与网关上限放到 3000 ms（shadow 不阻塞主链；1.5 s 时约 1/12 慢样本超时，偏向慢请求）；
  探针最初选的「你昨天看的那部电影叫什么来着」被端侧规则按「电影」本地开播（模拟车 v1 媒体变 playing，未复原）——问句判据补上
  回忆式句末「来着」（`runtime.question_shape.RECALL_TAILS`，端云共用；云侧换话题判据早就这么认），端侧三条分类路径都不再产出本地动作；
  发布闸的基础设施摘要只覆盖 `deploy/cloud/**`，`deploy/docker-compose.yaml` 变更在锚不变时直接放行（本次已获用户明确批准；闸本身的口子另登记，改发布闸属 CI/CD，要单独批准）。

