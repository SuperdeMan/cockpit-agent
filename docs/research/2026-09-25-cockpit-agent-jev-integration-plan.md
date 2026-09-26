# Cockpit Agent × Jev：语义判别层接入方案

> **2026-09-26 采纳更新**：本文保留原研究快照与建议状态；项目已按
> [统一路线图](../roadmap.md)、[目标架构](../architecture/cockpit-agent-v2-target-architecture.md)和
> [实施方案](../design/2026-09-26-cockpit-agent-v2-implementation-plan.md)采纳并拆解。
> 新增实现尚未开始。实施以最新代码为准，重点差异是稳定 goal 身份不重开 PLANNER_GOALS、
> Jev RAG 接点需覆盖 09-26 的目录路由/主语承接/词法首页保留；原排期不是交付承诺。


版本：方案 v1.0｜日期：2026-09-25（UTC+08:00）

代码基线：`SuperdeMan/cockpit-agent@b7364f76148fe6f35ddb42088f76bf77af0a2954`。该提交时间为 2026-09-25 02:47:59（UTC+08:00）。这是本次读取到的 main，不代表已核验部署环境。

状态：设计建议，尚未实现。本次读取了关键代码、现有约定及 Jev 官方文档，未调用 Jev 付费 API，未运行仓库测试、真实车控或线上发布。所有候选池大小、超时和验收目标都是待验证的建议值，不是实测收益或服务承诺。

## 0. 决策摘要

新增一个独立于聊天模型的、可弃权的语义判别能力。它只提供判别、排序和复核建议，不生成执行授权，不代替 Planner、VAL、确认、真实执行对账或端侧 T0。

首批范围：统一 Decide 网关、只读快照、离线评测、actionability shadow、Skill/Exemplar 与 manual-rag 重排。能力候选、上下文筛选、计划复核和记忆治理在首批证明有收益后分别推进。

不新增 Jev 业务 Agent，不把 Jev 放入 HMI 的全局聊天模型切换列表，不重新实现会话状态机，不为了接入重新打开 `PLANNER_GOALS`。

### 0.1 本次核验到的项目事实

| 现状 | 对方案的影响 |
|---|---|
| `proto/cockpit/llm/v1/llm.proto` 目前只有 Complete、CompleteStream、Embed | Decide 是拟新增的独立 RPC，不伪装为聊天补全 |
| `actionability.py` 明确是 shadow，启用需单独裁决 | 先保持旧逻辑并行观测，不让 Jev 直接拒识或执行 |
| `skills.py` / `exemplars.py` 已支持混合召回、预算、归因 | 接入重排，不另造检索系统；Jev 关闭不能关闭原来的 Skill |
| `WorkingSet` 是编排上下文胶囊，并区分读取状态 | 只做任务投影，不重新查一份历史制造第二真相源 |
| 第四轮修复强调 S2S 最终转写、回复指向、原话依据 | 不将模型 interpretation 当原话；不得让 Jev 重新授权旧确认 |
| `PLANNER_GOALS` 因已有 A/B 负结果默认 off | 计划检查不能强迫主 Planner 新增 goals/covers 输出 |
| 车书 `retrieve()` 默认 4 条、当前最多返回 10 条，并在检索阶段附图 | 初期最多用 10 条候选；文本重排和最终附图需要解耦 |
| 记忆有 OwnerKey、隐私级别和删除约束 | Jev 只能处理授权后的候选，不决定归属、权限或删除范围 |

## 1. Jev 外部契约与约束

核验日官方模型为 `jev-1.13.0`；`jev-latest` 是会移动的别名，生产与评测固定版本。接口为 `POST /v1/systemone`，请求结构是 `model + state + questions`，不是 `/chat/completions`。

Jev 只接受文本或文本化的结构化状态；英语是主要训练语言，中文需要单独验证。官方列出否定/隐含条件、多跳、数字、日期、无关长上下文与对抗输入等边界。本项目不能把“结构化响应”当成“理解正确”的证明。

| 原语 | 用途 | 正确读取方式 |
|---|---|---|
| Choice | 真正互斥的有限选项 | 选择值、全分布、confidence；多意图不要硬做单选 |
| Noul | 一个明确命题是否成立 | `noul=P(true)`，没有独立 confidence 字段；多个 Noul 不要求和为 1 |
| Score | 有顺序的描述性等级 | 分布与期望等级；score 不是正确率，confidence 也不是独立校验器 |

官方限制：总请求 64k token，state 加最长问题 32k token；Choice 最多 255 个选项；Score 2–10 个等级。系统应采用远小于厂商上限的工作预算；上限并不保证大输入的质量。

问题 ID 仅用于返回值关联，模型看不到该 key。`capability_x` 这样的 key 不足以告诉模型在判断谁；instructions 必须明确指向候选对象及判据。

生产禁用 SDK 隐式重试。`RetryPolicy(max_retries=0)` 可关闭重试，实时链由本项目统一总预算控制。官方 SDK 的请求/响应 body 不自动脱敏，不能开启正文 debug 日志后误以为所有隐私都被隐藏。

## 2. 架构与责任边界

```text
可信入口：文本 / ASR final / S2S 最终转写
    │
    ├─ 确定性取消、确认寻址、安全检查、端侧 T0 → 原路径
    │
    └─ 云侧请求
        ├─ 权限过滤、历史/记忆读取、WorkingSet、合法候选
        ├─ DecisionSnapshot：任务所需的只读投影
        ├─ Decide：actionability / capability / skill / context 建议
        ├─ 原 Planner → 原结构、权限、来源、依赖校验
        ├─ 可选 plan_semantics 复核 → 现有 RetryController
        └─ 原 Executor → 原 Agent / VAL / 确认 / 执行对账
                                │
                                └─ manual-rag 专项：检索 → 重排 → 附图 → 生成 → 原护栏

所有 Decide 都经过现有 llm-gateway 进程中的独立决策路由。
```

### 2.1 三层分工

**领域层**拥有“问什么、候选是什么、如何消费建议”。Manifest、commands、Skill、RAG、记忆既有声明继续是权威，禁止在 Jev 配置再抄一份领域能力表。

**网关层**拥有任务模板、类型校验、供应商适配、连接、限流、预算和用量。禁止前端传入任意 rubric 或修改模型 endpoint；内部调用者身份由服务鉴权验证，不信 payload 中自报的 caller_id。

**确定性决策层**拥有采纳条件、稳定排序、降级和状态有效性检查。Jev 返回高概率只能增加一条建议，不能扩大授权。

### 2.2 接入模式

| 模式 | 是否调用 Jev | 是否影响用户结果 |
|---|---|---|
| off | 否 | 必须与旧链业务结果等价 |
| shadow | 抽样/离线调用 | 否；只记录对照，绝不提交另一份副作用 |
| advise | 是，受预算限制 | 仅作用于已批准的排序/提示 |
| canary | 不是另一种模型语义，而是部署分流配置 | 固定会话桶中的 advise 或复核行为 |

任务自身 mode 与部署采样比例分开。`DECISION_SKILL_MODE=off` 仅关闭 Jev，不改变 `SKILLS_MODE=full`；`DECISION_EXEMPLAR_MODE=off` 也不改变原范例注入。

## 3. 统一协议与数据模型

### 3.1 网关 RPC

在现有 `LLMGateway` 上新增 `Decide(DecideRequest) returns (DecideResponse)`。先改 proto，再生成 Python/Go；旧调用方不需要改动既有 RPC。

以下是拟新增的字段草案；不是声称仓库已存在同名字段。生产实现使用明确消息与枚举，结构化任务 payload 只在校验后的边界使用 Struct。

```text
DecideRequest
  request_id
  binding                   # 仅内部：调用/快照绑定，绝不原样发送供应商
    exchange_id
    state_fingerprint
    scope_fingerprint
    consent_epoch
    original_request_fingerprint
  tasks[]
    task_id                 # allowlist：actionability、skill_relevance 等
    rubric_version
    payload                 # 按 task 的 schema 校验，拒绝任意额外字段
  budget_ms                 # 服务端上限再次截断；不是客户端授权

DecideResponse
  request_id
  state_fingerprint
  model_requested
  model_used
  results[]
    task_id
    rubric_version
    status                  # ok / abstain / disabled / unavailable / timeout /
                            # invalid_response / stale / privacy_filtered
    answers[]               # Noul / Choice / Score 的显式 oneof
    decision_applied        # 网关默认为 false，调用方在真实消费后记观测
    reason_code
  usage
    input_tokens            # 缺失为 unknown，不用 0 冒充
    output_tokens
  latency_ms
```

原始概率、校准后概率、项目采纳结论分别保存，不覆盖。`abstain` 是本项目适配层的结论，不假设厂商原生返回该状态。

### 3.2 DecisionSnapshot

Snapshot 是 WorkingSet 与当前有效任务状态的只读投影，不是新的长期会话数据库。

```json
{
  "schema_version": 1,
  "utterance": {
    "current_text": "不用了，先去加油，但还是别迟到",
    "source": "asr_final",
    "original_request_text": "接孩子放学，五点前要到学校",
    "original_request_available": true
  },
  "conversation": {
    "history_state": "found",
    "memory_state": "found",
    "pending_candidates": [],
    "focus": {"kind": "active_route", "destination_ref": "place_1"},
    "constraints": [{"id": "c1", "text": "五点前到学校", "required": true}],
    "already_executed": []
  },
  "candidates": []
}
```

只发送任务必需字段，不默认发送上述完整对象。`original_request_text` 仅在可信会话链确认属于当前任务时提供；缺失明确标注，不猜测补齐。S2S 的 interpretation 不作为原话，不进入本轮执行授权依据；如做离线诊断，放到独立、不参与决策的数据集。

内部 owner/session/user/vehicle 标识不必发送供应商；需要区分实体时使用请求级不透明别名。原文中的精确住址、电话、令牌、支付内容等按任务数据策略过滤或直接跳过该任务，不能只删 metadata 却把敏感值留在 utterance。

### 3.3 快照有效性

消费建议前，在同一个执行上下文检查 request/exchange、原话摘要、能力目录、候选内容和顺序、pending/焦点、授权/同意代际、模型与 rubric 版本。已有版本字段直接复用；没有版本字段时使用只读内容 fingerprint，禁止为了本功能再造第二套会话时序。

打断、会话关闭、用户切换、删除记忆、授权撤回、候选更新时取消尚未完成的工作；晚到结果标 stale，只留脱敏统计，不能写回新一轮。TTL 只能清理缓存，不能作为状态一致性的唯一判断。

### 3.4 响应校验

校验模型 pin、任务 ID、问题 ID 集、answer type、候选集合、所有数值有限且在范围内、分布和容差、Choice 是否为最大概率选项、Score 等级与 rubric 是否一致。未知 ID、缺项、NaN、越界、跨模型漂移一律拒绝采纳。v1 对一个重排批次要求完整有效；部分失败整批退回基线，不让“只有打到分的候选”获得优势。

## 4. 能力 A：Actionability 与对话行为判断

### 4.1 输入

当前可信原话、来源/显式激活信号、实际待补槽字段、最近提示及其寻址信息、当前焦点、必需的完整历史 exchange。不能只传一句话然后要求模型判断完整会话意图。

### 4.2 判别拆分

对可共存的行为分别用 Noul：是否有立即动作请求、是否咨询操作方法、是否在补充当前合法槽位、是否表达本轮约束、是否只有对象而缺少动作。只有真正互斥的问题才用 Choice，并保留 unknown/insufficient_context。

“执行请求”只是语义特征，不等于系统可以执行。权限、能力、槽位、确认、安全状态仍由原链处理。“有点热”这样的隐含诉求必须独立标注，不能强行归入显式指令或裸对象。

### 4.3 与旧代码关系

`actionability.py` 继续是纯函数和既有 shadow 逻辑，不在其中加入网络调用。新的异步 coordinator 在规划准备阶段取相同快照，分别记录 legacy verdict、Jev features 和后续真实结果。对照口径保留旧 execute/clarify/reject，但新特征不直接覆盖 `plan.actionability` 的历史含义。

显式按键、唤醒后的请求不能因为低概率被静默丢弃。`not_addressed`、`unclear`、`unsupported` 是不同原因。Hands-free 的非对助手说话判断单列评测；低置信度不是 OOD 证明。

入口覆盖必须单列：文本、按键、唤醒、免唤醒、S2S 移交，以及确认/取消/补槽/澄清的前置出口。只在 PlanBuilder 观测，不代表覆盖全部会话流量。先记录 eligible/skipped_by_path/observed，不为凑覆盖率把低时延或安全前置出口搬到 Jev 之后；S2S 自答路径不强行新增一次决策调用。

### 4.4 初期输出消费

shadow 只记录。后续获准后，才将高质量澄清建议交给原有澄清生成与状态流程；不新增前置 `if score < 0.5: return`。旧确认的绑定仍由确定性寻址判断，Jev 不得把“好的”绑定到隔轮的后备箱确认。

## 5. 能力 B：合法 capability 候选建议

### 5.1 位置与来源

从现有权限过滤后的 Registry/能力声明构造候选；保留完整执行校验目录、原 hint 扫描目录与 Planner 可见目录的分离。不得让 Jev 排名决定某条 route_hint 是否存在。

初期只改变 Planner 可见的可选候选顺序，不按概率硬删能力，更不新增 capability。评分输入包括受控描述、读写属性、已声明的前提、slots 定义，而不是模型自由生成的能力解释。

### 5.2 多标签，不强制单选

对每个候选问：“它能否承接本轮至少一项真实诉求，且没有违反原话的否定/咨询语境？”多个候选可以同时为 true。咨询语境只排除不相符的执行能力，不能排除真正回答操作方法的 `manual.query` 等只读能力；判据按候选声明的读写语义解释，不把“所有问句”统一当不适用。

输出内部稳定 candidate_id，再由代码映射回本次 catalog 的 capability_ref。若重新构建 catalog，旧 ref 全部作废；不能把 cap_0007 当跨请求稳定身份。

核心/兜底能力、当前有效续接所需能力、声明式依赖的保留规则由代码产生。不存在显式依赖元数据时不让 Jev 猜依赖闭包；保留较宽候选目录。

### 5.3 降级与未来裁剪

未知、无匹配、候选缺项、低置信、超时均回原目录。未来裁剪必须另外做“所有必需能力同时被召回”的评测，不只看 top1 或平均 recall。能力目录增长不是立即采用 aggressive top-K 的理由。

## 6. 能力 C：Skill / Exemplar 适用性重排

### 6.1 接入位置

现有词法/语义召回 → Jev 对可选 guide / exemplar 评分 → 原预算和渲染 → Planner。

常驻 policy、强制保留项不参与淘汰；已有依赖检查、lexical 保留和渲染预算语义保持。Exemplar 仍只是 prompt 范例，不允许 Jev 选中后复制其 plan 直接执行。

### 6.2 问题设计

使用 0–3 级 Score：0 无关/不适用；1 同话题但不能指导当前请求；2 对其中一项诉求有帮助；3 对当前目标、前提和会话阶段直接有帮助。矛盾/否定冲突另用 Noul，避免“无关”和“有害”混为一类。

`support_mass = P(level=2) + P(level=3)` 只是该 rubric 下的排序信号，不宣称它是生产正确率。排序建议：先保留不可裁项，再按 support_mass、期望等级降序，最后按原名次和 ID 稳定打破平局。不要直接把 BM25、余弦和概率相加；它们不是同一量纲。

### 6.3 召回和重排的实验拆分

A：原候选与原排序。B：更大候选池、仍用原排序。C：与 B 完全相同候选池，用 Jev 排序。只有 C 对 B 的差值才是重排的增量。初期可建议 guide 8、exemplar 12，最终注入仍服从既有条数与字符预算；这些是实验参数，不是必须采用的默认值。

Jev 不能救回召回池之外的知识；不能为了提高表面效果把 frozen holdout 内容投成新范例。

### 6.4 归因

分别记录 retrieved_ids、pre_rank、post_rank、pinned_ids、rendered_ids、clipped_ids、真正发生的 skill_effects。被检索到、被重排、被注入和最终影响计划是四件不同的事。

## 7. 能力 D：manual-rag 重排与引用检查

### 7.1 不改知识库格式

保留 `.mrag`、既有词法召回、车型/版本/hash 检查、ASCII 专名零命中、视觉别名目录与数值护栏，不引入向量数据库作为前提。

### 7.2 必须先修正的接入形状

当前 `local_index.py::retrieve` 在排序后立即占用图片数量/字节预算，且部分同页配图行为依赖 rank==0。简单获取 10 个已附图 Chunk 后再排序，会让新 top1 没有应附的图片，或图片预算被丢弃项提前耗光。

拟做纯重构：

```text
原始召回/硬过滤 → 文本及资源引用候选（最多 10）
                → 确定性视觉精确命中：走原直答，不进 Jev
                → 可选 Jev 重排 → 选最终 4 条（实验参数）
                → 按最终顺序附图与统一图片预算 → 原生成与护栏
```

先证明关闭 Jev 时重构前后的页码、正文、来源、图片、顺序、hash、零命中与异常语义完全一致，再加入模型。

### 7.3 模型只看到什么

问题、章节、文本片段、车型/版本描述、候选 ID；不发送图片/base64、整个 PDF 或原始音频。精确视觉映射跳过 Jev；车型和 hash 验证结果不能由模型重新解释。

片段任务只判断“是否包含回答这一问题所需证据”，不问它凭常识回答车型问题。zero-hit 不调用；安全处置继续前置且不等待重排。

### 7.4 三层逐步启用

第一层仅重排；第二层对最终片段集合判断证据是否充分；第三层检查生成答案是否有无依据主张。不要首版三层全串行开。

后两层高置信识别缺证时，保留真实卡片并诚实说明不足；接口失败或模型弃权回原有已批准链路，不谎称“手册没写”。检查通过也不能覆盖 `_ungrounded_numeric_claims`；数值出现只证明部分接地，不是语义正确认证。

## 8. 能力 E：上下文与长期记忆候选治理

### 8.1 上下文选择

只处理经过 OwnerKey、scope、隐私、时效和来源过滤的候选。必须保留的任务约束、pending、真实执行结果、当前定位可用状态及焦点由代码标为 pinned，不进入模型淘汰池。

对可选历史按完整 exchange 排序，不单独留下 assistant 半句。不能打断否定或条件从句；内容过大时整项退回基线，不通过截掉末尾“不要执行”来压缩。

原始事实 capsule 与模型 prompt projection 分开：Jev 只改变模型看到的可选部分，不得改变确定性候选问答、确认和执行审计读到的事实。两者都引用同一快照；不新增第二份自行演化的 focus。

`found / none / unavailable / off` 保持不同。“记忆拿不到”不能变成“用户没有这条记忆”。

### 8.2 长期记忆治理

保持现有抽取器产生候选与原黑名单先行；Jev 再对合法候选提建议：稳定偏好/临时约束/一次性指令、是否与同 owner 同谓词的旧候选重复、是否明显冲突、是否有用户原话依据。

首版只做 shadow/review 标记。精确重复继续代码处理；语义近似不自动 merge/supersede/delete。显式“记住/忘记”不被 Jev 的“重要性”过滤。所有写入仍走原接口与证据治理。

记忆持久化是副作用，不能归为无风险排序。该任务不进入语音关键路径；沿现有巩固调度处理，且删除用户或撤回授权后待执行任务与缓存一起失效。

## 9. 能力 F：计划语义检查

### 9.1 不是 Outcome Verifier

现有 verify 检查真实执行是否达成，Jev 只检查执行前的计划有没有误解用户。两者必须使用不同字段与指标。

检查点在原硬校验之后、任何新副作用之前。对 T2 只检查本轮新提出的 delta，不重新执行已经落账的步骤；若端侧已经执行了一半，另半云计划的澄清不能把已完成事实说成没执行。

### 9.2 不依赖 goals/covers

输入原话全文、已有可信原话片段/索引、活动约束、合法计划与能力描述。可用现有分句机制生成候选 spans，但保留全文和连接关系；“有分句”不等于“每段都是独立动作”。

不要求主 Planner 新输出 goals，不把模型 goal 自述当完整诉求真相，不借 Jev 接入开启已有负结果的 `PLANNER_GOALS`。

### 9.3 问题集合

| 问题 | 输出 | 消费边界 |
|---|---|---|
| 是否遗漏明确诉求 | 全句 Noul；必要时每个候选 span 一条 | 不能自动补一步 |
| 某 step 是否引入用户没有请求的副作用 | 每 step Noul | step ID 来自代码枚举 |
| 某 step 是否违反明确否定/咨询语境 | 每 step Noul | 不覆盖原硬守卫 |
| 既有活动约束是否被计划丢弃 | 每条合法 constraint Noul | ETA/时间数值比较仍在代码 |
| 是否错误续接对象 | 候选关系判别 | 不直接改 operation_id |

模型不会自由返回“缺少的工具调用”；问题按已有步骤/约束 ID 命名，instructions 明确指向对应对象。需要展示解释时由代码用固定 reason_code 与原文片段生成，不额外请求自由文字。

### 9.4 重试接线

先 shadow。获准后新增一条可消融的 `SEMANTIC_CRITIC_CONCERN` 类触发（拟新增名称），接入 `retry_policy.py`，不是再造外层 while。

每个用户请求最多一次由 Jev 引发的重新规划，并共享既有 attempt/deadline；原预算已耗尽不额外赠送次数。复核反馈只含疑点和原话片段，不含厂商臆造的正确动作。修改后重新完整硬校验。

对于模型已高置信指出的潜在额外副作用，在没有预算完成复核时，选安全澄清而不是冒险执行；Jev 不可用则回原已批准基线，不把它宣称为新的安全认证。已发生副作用只对账，不重复执行。

### 9.5 流式与提前执行路径

“执行前检查”必须在实际调度边界成立，不能只在最终 Plan 对象上补一段检查。实现前枚举 T1、T2、D0/流式提前执行、降级计划和挂起恢复的首次副作用出口；用 spy dispatcher 验证检查完成之前没有相应步骤被调度。

初版仅在能确保完整计划尚未产生新副作用的云侧路径启用主动 critic；其它路径先 shadow，并记录 `skipped_streaming_early_dispatch`。逐步扩展时再比较两种独立方案：缓冲当前需复核的步骤，或只做可隔离步骤的局部语义检查。不能把局部检查成绩叫作“全计划诉求覆盖”，不能为等 Jev 阻塞 T0 或即时取消。原结构、安全与确认校验在各流式出口继续生效。

## 10. 性能、限流、缓存与失败策略

### 10.1 不增加六个串行调用

首版只开启一条在线改动车道。后续同一快照、同一授权范围、相同供应商数据等级的前置判别可以合批；禁止跨 owner 合并。依赖另一个判别结果的新问题必须另一个阶段求值，不能认为并行问题会互相读取答案。

建议 v1 每轮最多两次在线供应商调用，第二次在 RAG 专项或计划复核之间按任务选择；记忆离线调度不挤占语音预算。

初始实验预算：在线新增总阻塞 ≤500ms，单次前置 ≤300ms，RAG ≤350ms，critic ≤300ms；实际 timeout 取阶段上限、全局剩余预算、原请求 deadline 三者最小值。shadow 独立预算可为 1500ms，不阻塞主链。全部值以实际部署机测试调整，不是官方时延。

### 10.2 Shadow 要真实隔离

只读快照、有界队列、低优先级并发、独立额度。拥塞时丢 shadow 样本并记 dropped，不阻塞业务；生产执行只有一份，旁路最多生成计划或离线计算，不调用车控、下单、记忆写接口。

### 10.3 限流与重试

按账户总额度对所有副本限流，同时限制请求数与 token 估计；以供应商 usage 校正用量，日预算超额禁用可选调用。429、529、网络错误在在线车道直接弃权/回基线；离线任务可遵循 Retry-After 在总预算内有界退避。没有周期付费探活。

语义错误不要当作传输重试。参数/鉴权失败发配置告警，不循环请求。程序 bug 不能统一吞为“供应商不可用”；仍须显式报错并在上层按既有故障治理处置。

### 10.4 缓存

v1 动态会话决策只允许请求内缓存。Key 至少覆盖任务/model/rubric/calibration、规范化真实输入、候选 ID/内容/顺序、状态与授权指纹。不得仅用 utterance 做全局缓存。静态车书 query-cache 必须绑定车型、版本、源/content hash、rubric 与匿名化策略；默认不启用包含私人原话的跨会话缓存。

### 10.5 失败矩阵

| 故障 | 行为 |
|---|---|
| 全局 off | 不发请求，业务输出回基线 |
| timeout/限流/不可达 | 回基线；不自动换聊天模型假装 Jev |
| 响应缺项/非法分数/未知 ID | 当前批次整批不采纳 |
| 模型或 rubric 漂移 | 不采纳并告警，评测批作废 |
| 状态更新/打断/换人/撤权 | 标 stale，禁止应用旧结果 |
| 空候选 | 跳过模型；按原零命中/原规划处理 |
| 低置信/相互矛盾 | 项目层 abstain，不静默吞请求 |
| 数据不允许发第三方 | privacy_filtered，原链处理 |
| 明确潜在副作用疑点但复核预算耗尽 | 执行前进入已批准的澄清路径，不自行造修复动作 |

## 11. 数据、校准与验收

### 11.1 第一批数据规模（建议）

| 数据桶 | 建议标注单位数 |
|---|---:|
| actionability / 对话行为 | 400 |
| 多能力候选适用性 | 400 |
| Skill / Exemplar | 350 |
| 上下文相关性与必要项 | 300 |
| 记忆候选治理 | 300 |
| 车书片段与引用 | 350 |
| 计划语义缺陷 | 400 |
| 合计 | 2500 |

另建至少 600 条安全/故障/时序挑战项与 30 组 50–100 轮长会话。规模是启动建议，不足以直接证明极低误执行率；高风险切片不够时继续 shadow。

标注单位是带前置状态的请求/候选/计划，不是只写一句话和一个域名。历史生产记录先脱敏、取得相应使用授权；高风险和分歧项双人确认。可保留 ambiguous/unresolvable，不强行给错误单标签。

按会话、原始 badcase 家族、同义改写家族、文档章节分组切分；建议开发/校准/冻结测试 50/25/25。同族不得穿透 split。新范例不得取自 frozen test。挑战集与自然分布集分开报告。

### 11.2 校准

逐任务、模型版本、rubric、语言与候选构成评估 Noul/Choice 的可靠性；保存原始与校准后分数。阈值从 dev/calibration 选择，冻结后在 holdout 验证。

不存在全局 `confidence > 0.8` 的通用开关。Noul 采用上下阈值，中间弃权；Choice 可联合 top1、top1-top2 差、分布及场景条件；Score 使用等级分布，不把等级值当概率。

分别报告 coverage（实际采纳比例）与 risk（采纳子集错误率）；全弃权不能宣称系统准确率 100%。更换模型、问题模板、翻译方案或候选规模都要重测。

### 11.3 三组核心对照

A0：真实旧链。A1：只完成纯重构、Jev off。A2：同样候选与预算、Jev on。验证 A0=A1 后，才比较 A2。候选池扩大另加独立对照，不能混成“Jev 收益”。

固定 chat provider/model、skills/exemplars 版本、状态快照、代码 SHA、Jev/rubric/calibration hash。动作计划可离线回放，真实副作用绝不双臂执行。在线分流按会话固定，防止同一会话中来回切臂污染上下文。

### 11.4 建议发布门槛

| 维度 | 要求 |
|---|---|
| 业务等价 | off 与纯重构前业务输出在冻结确定性用例中完全一致 |
| 权限/归属/过期 | 测试集中越权、旧结果采纳、跨 owner 泄漏、绕确认、重复副作用均为 0 |
| 候选 | 所有必需能力的完整召回率不低于原方案；新增误裁为 0 |
| 重排 | 预注册的主要相关性指标优于同池基线，且最终任务成功率无退化 |
| Actionability | 明确请求静默丢弃为 0；澄清精度、召回及不必要澄清分开报告 |
| Critic | 可考虑启动目标：被采纳疑点 precision≥95%，同时报告置信区间和漏检；不足只 shadow |
| RAG | 页码/内容/图片/来源门禁保持；错误有据回答与数值幻觉不增加 |
| 时延 | 端到端首音频与完成时延 p50/p95/p99 均测，原 T0 不等待 Jev |
| 故障 | 关闭 key、429/529、超时、错误模型、坏 JSON、撤权、打断均可确定性复现 |

以上“0”仅指给定测试集中的观测门槛，不是生产风险为零的声明。收益采用同会话配对统计，不能只挑展示成功的例子。

## 12. 最小测试矩阵

| ID | 场景 | 必须保持的结果 |
|---|---|---|
| J001 | 新会话“上海” | 不凭空猜要导航；对照澄清建议 |
| J002 | 正在补目的地时“上海” | 可作为合法补槽，不再当裸对象 |
| J003 | “雨刮器怎么打开” | 车书咨询，零动作 |
| J004 | “打开雨刮器” | 回原执行链，Jev 不直接执行 |
| J005 | “打开后备箱，再说说空调有哪些模式” | 方法问句不吞前半独立动作，危险动作仍确认 |
| J006 | “别打开车窗，只解释怎么开” | 零车控 |
| J007 | “导航去公司，路上找充电站” | 多诉求完整保留，不能只选 charging |
| J008 | “如果堵车就别走高速” | 条件/约束不被直接丢掉或无条件执行 |
| J009 | “如果找不到她的地点就问我，不要猜” | 不发明新导航动作 |
| J010 | 旧后备箱确认 + 新闲聊追问 + “好的” | 不确认旧危险操作 |
| J011 | 旧澄清 + 新候选列表 + “第二个” | 依当前确定性回复指向，不抢旧提示 |
| J012 | “不用了，关掉空调” | 取消与新请求不互相吞掉 |
| J013 | S2S 转写与 interpretation 极性相反 | 只采用最终转写授权依据 |
| J014 | Jev 等待中 barge-in | 晚到答案不得进入新一轮 |
| J015 | 回答时 Memory unavailable | 不声称用户没说过或没有偏好 |
| J016 | 候选批次被替换 | 旧 candidate_id/序数建议失效 |
| J017 | Jev 返回不存在能力 ID | 整批拒绝采纳 |
| J018 | Noul=NaN、Choice 概率缺项 | invalid_response，不排序 |
| J019 | 429/529/timeout | 单次失败回基线，不叠加无限重试 |
| J020 | 车书未知产品名/错车型 | 原零命中不被重排救成伪支持 |
| J021 | 车书第 6 条重排为第 1 条 | 最终附图与新排名一致，预算不被丢弃项占用 |
| J022 | 受控图标别名命中 | 原确定性图文直答，不再问模型 |
| J023 | “今天不想吃辣” | 不直接变成永久不吃辣 |
| J024 | “记住我以后都吃清淡的” | 显式记忆不被重要性评分吞掉 |
| J025 | 两名乘员同一句偏好 | 隔离缓存与归属，不能跨 owner 去重 |
| J026 | 用户忘记后 pending consolidation 晚到 | 不重建已经删除的记忆 |
| J027 | Planner 已用完一次重试 | Jev 不能再套新重试循环 |
| J028 | 端侧已执行车控、云侧计划需澄清 | 真实已执行动作仍入账并如实汇报 |
| J029 | 重排器只返回半数结果 | 整批回原排序 |
| J030 | 全局聊天模型切换 | Jev pin 与 Embed 不受其影响 |

## 13. 拟新增文件与改动范围

路径是建议，不是当前已有文件清单；既有同职责实现必须优先复用。

```text
proto/cockpit/llm/v1/llm.proto              # 修改：Decide RPC 与显式类型
runtime/decision_contract.py              # 新增：纯类型/只读绑定；无网络、无领域词
llm-gateway/decision_provider.py           # 新增：唯一 Jev 外部调用
llm-gateway/decision_service.py            # 新增：schema、模板、限流、归一
llm-gateway/decision_specs/*.yaml          # 新增：版本化判别 rubric，不复制能力表
orchestrator/cloud/decision_support.py     # 新增：调度/预算/任务投影与只读建议
orchestrator/cloud/actionability.py        # 尽量不改纯函数；保留原 shadow
orchestrator/cloud/planning.py             # 修改：只接收获准的候选/复核建议
orchestrator/cloud/context.py              # 修改：从同一 WorkingSet 导出可选投影
orchestrator/cloud/skills.py               # 修改：可选重排接点，原开关不变
orchestrator/cloud/exemplars.py            # 修改：可选重排接点，原开关不变
orchestrator/cloud/retry_policy.py         # 后续修改：单条可消融 semantic trigger
agents/manual_rag/src/providers/local_index.py # 修改：候选与附图解耦
agents/manual_rag/src/decision_review.py    # 新增：RAG 专项问法与受控消费
agents/manual_rag/src/agent.py             # 修改：调用接点，原护栏保留
memory/decision_review.py                  # 后续新增：候选 shadow/review，不直写
memory/extract.py                          # 后续修改：合法候选旁路
agents/_sdk/clients.py                     # 修改：复用连接的 Decide client
orchestrator/cloud/clients.py              # 修改：复用连接的 Decide client
observability/                            # 修改：事件/脱敏/用量与消费归因
scripts/eval_decisions.py                  # 新增：离线/真 API 分车道
scripts/probe_decisions.py                 # 新增：不含真实副作用的端到端探针
test/eval_corpus/decisions/                # 新增：分组 split + 标注规范
```

不新增外部端口、独立部署服务、前端必改字段或新数据库作为 v1 前提。Dashboard 可后续增加决策详情，但用户端不应展示未经校准的“理解准确率”。

## 14. 实施任务包

| 包 | 工作 | 依赖 | 完成判据 |
|---|---|---|---|
| JV00 | 冻结主链和数据/版本/关键反例 | 无 | 保存基线与可重复测量命令，不修改主链 |
| JV01 | Decide proto、provider、response validator | JV00 | 真/假 provider 分离；密钥缺失与所有异常有契约测试 |
| JV02 | Snapshot、状态绑定、脱敏、预算、观测 | JV01 | 撤权/晚到/跨 owner/shadow 隔离测试通过 |
| JV03 | Actionability shadow 与统一 evaluator | JV02 | 同快照分歧表、中文校准报告、无业务行为变化 |
| JV04 | Skill/Exemplar 重排 | JV03 | 同池 A/B；原预算/policy/failover 不变 |
| JV05 | RAG 纯重构与重排（独立 PR） | JV02 | off 等价、图片预算正确、整本相关门禁保持 |
| JV06 | 能力候选与上下文建议 | JV04 | 完整召回不降、原话与 pinned 状态不丢 |
| JV07 | 计划语义 shadow，之后单触发复核 | JV03、JV06 | 不依赖 goals/covers；预算/零重复副作用契约通过 |
| JV08 | 记忆候选 review | JV02、数据策略批准 | 不直写/删；owner/forget 链路通过 |
| JV09 | 分任务灰度、故障演练、回滚 | 各任务自己的门槛 | 一键 off 真正生效，证据绑定 SHA/模型/rubric |

JV04 与 JV05 可以在独立模块并行开发，但不要同批上线，不然无法归因。发布、push、云端 apply、使用真实个人数据或产生真实副作用均按现有独立授权流程，不因本设计自动获得授权。

## 15. 建议配置示例

以下是拟新增配置，先实现并校验，再写入 `.env.example`；示例不包含密钥值。

```dotenv
DECISION_PROVIDER=typesafe
DECISION_MODEL=jev-1.13.0
DECISION_ENABLED=false
DECISION_ACTIONABILITY_MODE=shadow
DECISION_CAPABILITY_MODE=off
DECISION_SKILL_MODE=off
DECISION_EXEMPLAR_MODE=off
DECISION_CONTEXT_MODE=off
DECISION_MANUAL_RAG_MODE=off
DECISION_PLAN_CRITIC_MODE=shadow
DECISION_MEMORY_MODE=off
DECISION_ONLINE_BUDGET_MS=500
DECISION_MAX_ONLINE_CALLS=2
DECISION_SHADOW_SAMPLE_RATE=0.05
TYPESAFE_API_KEY=
```

全局 false 时连 shadow 也不发请求；开启要求非空 key。key 只在 llm-gateway 可读的根运行时配置中消费，不进 APK/HMI/日志。生效模式从服务端受控配置解析，客户端 meta 无权强行开启。

## 16. 观测、费用与收益归因

### 16.1 每次调用与采纳分开记账

通过原有 trace_id 关联 `decision.request`、`decision.result`、`decision.applied` 三类事件（名称是建议）。记录 task、模型实际版本、rubric/calibration hash、模式、eligible 原因、候选数量、请求/状态指纹、供应商 latency、排队时长、added_blocking_ms、input_tokens、cache_hit、弃权原因与真正消费结果。原话、记忆、地址和 key 不作为默认日志字段。

建议聚合指标：

| 指标 | 目的 |
|---|---|
| eligible / skipped_by_path / sampled / observed | 防止只观测 Planner 后误报全入口覆盖 |
| apply_rate / abstain_rate / invalid / stale / timeout | 判断模型可用性与实际参与程度 |
| legacy_vs_jev_disagreement | 聚焦人工复核，不把分歧直接视为旧方案错误 |
| retrieved / reordered / rendered / effect_applied | 分清排序改变与业务收益 |
| critic_triggered / retry_allowed / retry_exhausted | 查出循环、失控和无效复核 |
| added_blocking_ms 与端到端首音频/完成延时 | 区分供应商快与用户体验快 |
| input_tokens / billed_requests / cost_by_task | 度量真实增量成本 |

指标维度只放有限枚举。request_id、原文和候选 ID 留在受控 trace 中，不做 Prometheus 高基数 label。用量缺失标 unknown；供应商请求已经发出后，即使本地因超时弃权，也不能默认当作免费调用。

### 16.2 成本测算

核验日输入单价为 $0.042/百万 token，输出免费。按实际供应商 usage 结算估计，不只计算用户那句话：state、instructions、criteria、重复请求和 shadow 都属于需统计的输入开销。

```text
估算模型费用 = 所有请求的计费输入 token 合计 / 1,000,000 × 0.042 美元
```

举例（仅为假设）：每月 100 万轮，其中 30% 进入 Jev；进入的每轮合计 4,000 个计费输入 token，已经包含该轮所有 Jev 调用，则约 $50.40/月。未包含税、网络、工程、观测和其它模型费用；实际采纳比例与 token 数必须通过真请求测量。

是否省钱需要另外计算：被真正省掉的主模型 token/调用与错误重试成本，减去新增 Jev、复核重规划与基础设施成本。只是加一次 Jev 后仍把全部材料喂给主模型，通常不能据此声称降本；“主模型输入减少”也须同时满足任务效果不退化。

## 附录 A：一个正确的供应商请求形状

仅演示“候选是否与诉求相关”，不表示已经校准或可以执行。

```json
{
  "model": "jev-1.13.0",
  "state": {
    "utterance": "导航去公司，路上找个充电站",
    "candidates": {
      "candidate_a": {"description": "规划前往指定目的地的导航路线"},
      "candidate_b": {"description": "沿既定路线检索可用充电站"}
    }
  },
  "questions": {
    "relevant_a": {
      "type": "noul",
      "instructions": "仅依据 state.utterance，state.candidates.candidate_a 是否能够承接用户明确提出的至少一项诉求？这里只判断语义相关，不判断权限，不执行。",
      "criteria": {
        "true": "用户确实提出与该能力匹配的诉求；不能仅因话题相同认定匹配。",
        "false": "没有对应诉求，或者该动作被明确否定，或者只是咨询操作方法。"
      }
    },
    "relevant_b": {
      "type": "noul",
      "instructions": "仅依据 state.utterance，state.candidates.candidate_b 是否能够承接用户明确提出的至少一项诉求？这里只判断语义相关，不判断权限，不执行。",
      "criteria": {
        "true": "用户确实提出与该能力匹配的诉求；不能仅因话题相同认定匹配。",
        "false": "没有对应诉求，或者该动作被明确否定，或者只是咨询操作方法。"
      }
    }
  }
}
```

候选 ID 到实际能力的映射只在本请求内持有。上面的 key 不承担语义，instructions 显式引用了对象。复杂条件、引用句和无标点语句必须通过项目语料验证，不能把此例当生产万能模板。

## 附录 B：给编码 Agent 的执行约束

先读取 CLAUDE.md、AGENTS.md、本方案及最近会话修复文档，确认实际 checkout SHA；main 变化时先比较相关文件，不默认为本方案已覆盖新改动。

按 JV00→JV03 先完成一批，不一次接入所有业务路径。先写失败/边界测试，再实现；默认关闭在线行为。网络只在 llm-gateway；actionability/runtime 判据保持纯函数；Jev 不注册为业务 Agent，不修改 chat provider 的全局切换；不新增另一套路由词表、授权规则、会话状态机或 Planner 重试循环。

每个 PR 说明：改动范围、未触及边界、用例与精确 SHA、实际运行命令及结果、尚未运行项目、默认开关、回滚方式。没有 API 密钥就完成 mock/契约和离线管道，不编造真 Jev 分数。真实调用、push、部署、生产写与数据删除都遵循仓库原有独立授权要求。

## 附录 C：证据与资料

以下代码链接全部固定到本次基线 SHA；文档网站按 2026-09-25 核验，实施时若版本变化需重新检查。

- 项目当前提交：https://github.com/SuperdeMan/cockpit-agent/commit/b7364f76148fe6f35ddb42088f76bf77af0a2954
- 工程与安全约束：https://github.com/SuperdeMan/cockpit-agent/blob/b7364f76148fe6f35ddb42088f76bf77af0a2954/CLAUDE.md
- LLM 契约：https://github.com/SuperdeMan/cockpit-agent/blob/b7364f76148fe6f35ddb42088f76bf77af0a2954/proto/cockpit/llm/v1/llm.proto
- Actionability：https://github.com/SuperdeMan/cockpit-agent/blob/b7364f76148fe6f35ddb42088f76bf77af0a2954/orchestrator/cloud/actionability.py
- Skills：https://github.com/SuperdeMan/cockpit-agent/blob/b7364f76148fe6f35ddb42088f76bf77af0a2954/orchestrator/cloud/skills.py
- Exemplars：https://github.com/SuperdeMan/cockpit-agent/blob/b7364f76148fe6f35ddb42088f76bf77af0a2954/orchestrator/cloud/exemplars.py
- Cloud 编排：https://github.com/SuperdeMan/cockpit-agent/blob/b7364f76148fe6f35ddb42088f76bf77af0a2954/orchestrator/cloud/README.md
- 重试唯一实现：https://github.com/SuperdeMan/cockpit-agent/blob/b7364f76148fe6f35ddb42088f76bf77af0a2954/orchestrator/cloud/retry_policy.py
- goals/covers 负结果与默认关闭：https://github.com/SuperdeMan/cockpit-agent/blob/b7364f76148fe6f35ddb42088f76bf77af0a2954/docs/conventions.md
- 第四轮会话修复：https://github.com/SuperdeMan/cockpit-agent/blob/b7364f76148fe6f35ddb42088f76bf77af0a2954/docs/design/2026-09-24-conversation-review-round4-remediation.md
- 手册检索与附图：https://github.com/SuperdeMan/cockpit-agent/blob/b7364f76148fe6f35ddb42088f76bf77af0a2954/agents/manual_rag/src/providers/local_index.py
- 手册回答与护栏：https://github.com/SuperdeMan/cockpit-agent/blob/b7364f76148fe6f35ddb42088f76bf77af0a2954/agents/manual_rag/src/agent.py
- 记忆约束：https://github.com/SuperdeMan/cockpit-agent/blob/b7364f76148fe6f35ddb42088f76bf77af0a2954/memory/README.md
- Jev 模型、价格、语言与限制：https://docs.typesafe.ai/models
- HTTP 契约：https://docs.typesafe.ai/api
- 概率与置信度：https://docs.typesafe.ai/confidence
- 模型已知边界：https://docs.typesafe.ai/model-jaggedness/jev-1.13
- 异步 SDK 与日志边界：https://docs.typesafe.ai/sdk/python/api/clients/async
- SDK 重试：https://docs.typesafe.ai/sdk/python/api/retries
- 重排示例：https://docs.typesafe.ai/cookbooks/rerank_typesafe
- Skill 建议示例：https://docs.typesafe.ai/cookbooks/skill_suggestion

供应商声明不使用客户请求/响应训练，不等于默认零留存；生产私人数据调用前应确认数据处理范围、保留期、部署区域、合同与用户同意要求。不把通用隐私承诺推断成已满足本项目要求。
