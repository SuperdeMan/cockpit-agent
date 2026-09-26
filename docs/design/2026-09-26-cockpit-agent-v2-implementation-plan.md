# Cockpit Agent v2 与 Jev 分批实施方案

> 日期：2026-09-26。状态：**方案已落盘；下面所有新增实现任务均未开始**。
> 基线：`47c62b44d335a3c76da90f89f05fa2fc887c2742`，main；本次只改文档。
> [路线图](../roadmap.md)决定优先级，[目标架构](../architecture/cockpit-agent-v2-target-architecture.md)决定边界。
> CA2-01–22 与 JV00–09 沿用两份研究编号，是可领取的本地任务，不是已创建的 GitHub Issue。

## 1. 对代码的复核与研究修订

| 复核落点（均为既有路径） | 基线实际形态 | 落地要求 |
|---|---|---|
| `proto/cockpit/llm/v1/llm.proto` | Complete/CompleteStream/Embed，无 Decide | JV01 追加独立 RPC，保留旧接口，生成 Python/Go |
| `orchestrator/cloud/models.py` | Step 已含 kind/deployment/effect/origin_text/verification；`step_record` 不带 covers | CA2-02/03 扩展唯一序列化点；不新造 Plan 模型 |
| `orchestrator/cloud/planning.py::_goals_enabled` | `PLANNER_GOALS` 默认 off，有 A/B 负结果记录 | 稳定身份独立于模型分解；不得借 v2/Jev 重开该实验 |
| `orchestrator/cloud/progress.py::step_summary`、`engine.py` 挂起收尾 | 摘要用于完成前序步骤呈现，普通回答取首句/60 字 | CA2-04 保存完整结果并派生摘要，迁移覆盖所有收尾出口 |
| `orchestrator/cloud/context.py` | WorkingSet 与读取状态已有实现 | Snapshot/View 从此投影；不另外查询历史构造第二事实源 |
| `observability/events.py`、edge server、Cloud/scene state_mirror、gateway/edge、collector | 单车广播和多个消费者；Cloud 镜像整包 `_updated_at` | CA2-06 先列消费者再跨链兼容迁移，不能只改 Cloud 字典 |
| `orchestrator/cloud/verify.py` | schema/state_match，SAT/UNSAT/UNKNOWN；不带完整动作因果证明 | CA2-10 增强观测契约；保留未知与明确失败的区别 |
| `agents/_sdk/ledger.py` | PG 账本，open/heartbeat/close/cancel，整体 best-effort | CA2-08 按任务持久承诺分档，副作用不能沿用无账本放行 |
| `orchestrator/edge/nlu.py`、`val.py` | NLU off/shadow；Python 模拟 VAL | CA2-13/14 是有门槛的增量；CA2-18 才接真实驱动 |
| `agents/manual_rag/src/providers/local_index.py` | retrieve/retrieve_sections 均在返回前 `_materialize`，最多 10 条，rank 0 影响配图 | JV05 先抽文本/资源引用候选，最终排序后一次附图 |
| `agents/manual_rag/src/agent.py::_route_if_unsure/_merge_routed`、`toc_router.py` | 09-26 已有目录路由、词法首页保留；复合仪表灯问句承接主语 | 原研究 JV05 必须接最新组合检索链，不能覆盖这些修复 |
| `agents/mcp_bridge/src/admission.py`、`memory/README.md` | 已有 schema 准入、OwnerKey；未知声纹回 primary 是存量语义 | 复用准入；“未知声音不读敏感个人记忆”是 CA2-15 的待实现变更，不能当现状 |

现有发布记录为 `5a2f4c9d674fb27c98539f1d57ff8cc93bca4c76`，本次没有运行线上 status/verify，
不作现场仍运行该 SHA 的新证明。手册检索已改善但规划方差仍有红样本；不得用“模型接入”代替现有问题重证。

## 2. 交付规则与共同完成定义

角色是职责分工建议，没有指定姓名或假设人员到位：RT=运行时后端，EDGE=车端/车辆接口，
MODEL=模型与评测，UI=HMI/Android，QA=验证，SEC=权限/隐私，OPS=发布。R0 确认实际承担者。

每个任务交付可独立审查的代码/契约、双向边界用例、兼容性说明、精确版本证据和回退方案。
有持久化的字段必须核对：声明/SDK → Registry 或传输 → Step → 挂起序列化/恢复 → T1/D0/T2 → Agent/VAL → 结果呈现。
一次 PR 尽量只改一个权威对象；跨路径接线必须整批覆盖，不能把半条安全边界上线。

状态回填使用“未开始 / 实现中 / 离线通过 / 真栈通过 / 已部署 / 已签收 / 阻塞于具体条件”，
同时列出尚未执行的验证。mock、仿真、真实 Provider、OEM 沙箱与实车各自标明。
脚本、字段、开关若尚未实现，必须标“拟新增”，不能把建议命令贴成现成运行入口。

## 3. v2 工作包清单

### 3.1 R0/R1：先补契约与事实

| ID / 主责 | 范围与具体产物 | 依赖 | 完成判据与回退 |
|---|---|---|---|
| CA2-01 / QA+RT | 固定 baseline manifest：源码/部署/APK、provider/model、prompt/catalog/policy/知识 hash、fixture/split、环境；复用现有探针重证关键残余 | 无 | 同输入两轮可比较，失败/skip/预检拒发分列；不改变业务；JV00 使用同一 manifest |
| CA2-02 / RT | Step-owned input_scope：turn/span/clause 来源与解析版本，起点原话、安全原点、业务投影分开；先沿现有 step_raw_text/step_grounding 接入 | 01 | 混合问/做/否定/补槽与引用标题不串域；未知映射保持旧语义或澄清，不能按猜测给写授权；可停新投影 |
| CA2-03 / RT | 服务端稳定 task/goal ID 与 revision；统一 step_record/SessionState 往返；改口/撤销关系保留终态 | 02 | 挂起重启后身份稳定；旧记录显式 unknown 覆盖；不打开 PLANNER_GOALS、不用随机 ID 证明理解完整；兼容读旧记录 |
| CA2-04 / RT+UI | ResultBundle、完整结果/引用保留、WS 兼容序列化、HMI/mobile 共享选择器、TTS 摘要；不新建第二 UI 台账 | 02,03 | 确认轮保留手册答案与卡；确认/取消后已完成兄弟结果仍在；重连无重复、旧 revision 不覆盖；可退旧展示但不能删事实 |
| CA2-05 / RT+EDGE | Capability v2 增量：effect、参数类型/单位/区域、车型/软件版本、前置条件、幂等/验证声明；迁移清单与 Registry 往返 | 01 | 新写能力缺声明拒准入，旧能力逐项登记；存在/可用/授权分开；含描述-only 变化的路由回归；不一次改名所有 Agent |
| CA2-06 / EDGE+RT | 认证车辆身份、source epoch/seq、逐信号时效/质量/来源；生产者/路由/全部镜像兼容接线 | 01；上线消费受 05 约束 | 两车隔离、乱序/重启/局部陈旧通过；旧单车事件仅显式仿真适配；新鲜度缺失不放行新安全写档 |
| CA2-07 / RT+SEC | WorkingSet 的联邦权限化 View；各权威读取状态/引用、pinned 条目与 prompt 投影分开 | 05,06 | 未授权字段进入模型前剔除；found/none/unavailable/off 不混；撤权即时收紧；可退旧只读路径 |
| CA2-12 / QA+EDGE | 有状态 SimulatedVehicleDriver/故障 harness（拟新增）；基于既有模拟 VAL，注入 ACK 丢失、状态改变与重启 | 06；契约参考 05 | 不返回固定 OK；source=simulated，随机种子/事件轨迹可重放；持续为 R2/R3/R4 提供故障验证 |

CA2-05 必须单列“为了回答而建立任务/规划”与“改变车辆/商户/用户数据”的副作用类别。
`effect=write` 不能直接推出“凡问句一律禁止”：既有研究已发现这会误伤充电规划等正当请求。
先用现有 effect 字段兼容，再在契约 PR 冻结更细分类及消费方；模型不提供该分类权威。

### 3.2 R2：执行承诺可恢复、可核实

| ID / 主责 | 范围与具体产物 | 依赖 | 完成判据与回退 |
|---|---|---|---|
| CA2-08 / RT | 扩展 SDK Ledger 的 task/step/operation 生命周期、plan_revision、证据引用和持久准入档 | 03,05；故障验收用 12 | 副作用提交前 durable admission；PG/本地日志失败不继续新写；只读 best-effort 明示；schema 实施另授权 |
| CA2-09 / SEC+RT+EDGE | 一次确认绑定主体/车辆/operation/参数摘要/能力版本/revision/期限；原子消费与执行点复核 | 05,06,08 | 跨端重复点击、过期、改参、换车、确认期间挡位改变全部拒绝旧授权；停止新写后才能回退 |
| CA2-10 / RT+EDGE | Verifier 逐信号质量/时效与动作关联；ACK/state-satisfied/observed/verified 分开输出 | 06,08 | 动作前就已满足目标不能伪造因果；UNKNOWN 在 ResultBundle 明示；T1/D0/T2 一致；退旧显示也保留证据 |
| CA2-11 / EDGE+RT | 车端本地操作日志与云端协调同步，接收端幂等键、恢复查询、未知结果处理 | 08,09,10,12 | 执行后落库前崩溃不盲重放；网络中断不丢 operation；不宣称 exactly-once；回滚保留日志可读取 |
| CA2-15 / RT+SEC | 偏好修订、工作记忆失效、多人隔离、跨端任务绑定；未知声纹隐私投影 | 03,07 | 显式修改胜过旧偏好；任务结束不复活旧约束；未认证声音不读敏感个体记忆；forget 与在途治理同代际失效 |

### 3.3 R3–R5：模型、车辆与试点

| ID / 主责 | 范围与具体产物 | 依赖 | 完成判据与回退 |
|---|---|---|---|
| CA2-13 / EDGE+MODEL | T1e 规划后端和预算接口（拟新增），沿同一能力/计划/执行语义，先只读 | 05,07,12；写放量需 R2 | 越权/缺能力不猜；步数/deadline 有界；默认 shadow；关闭不影响 T0 |
| CA2-14 / MODEL+QA | 规则、固定能力表示、Schema、小模型/云模型对照；中文/多轮/ASR/未见能力 | 01,13、目标板 | 风险—覆盖、完全正确率、冷暖 p50/p95/p99、内存/温升/语音抢占；无净收益不放量 |
| CA2-16 / UI+RT | 两端 ResultBundle、S2S 原话交接、停播/取消/设备停止分开、视觉引用时效、主动打扰预算 | 04,07；持久取消用 08/09 | 电话/音频焦点、打断、前后台、重连、旧帧、多人场景通过；复用 Android 总表，不重写状态机 |
| CA2-17 / SEC+RT | MCP 桥 audience/scope、用户/车辆委托、句柄归属、版本指纹、外源返回隔离与出口约束 | 05,09 | 注入内容不能修改权限/车辆/操作参数权威；跨 owner 句柄拒绝；旧服务级账号边界仍如实标注 |
| CA2-18 / EDGE | 一种 AAOS/OEMServiceDriver，发现/读/订阅/执行/结果查询；型号配置与 provenance | 05,06,09,10,12、OEM 接口 | 同一操作经确认/VAL/真实接口/状态对账；无能力明确 unavailable；沙箱不能签成实车 |
| CA2-19 / RT+EDGE | 能源—导航—充电及健康—车书—服务建议两条闭环，复用既有领域 Agent | 07,08,18；外部适配用 17 | 原始数据有时效/来源；站点变化可重规划；告警解释不声称排除故障；预约有真实接口再写 |
| CA2-20 / RT | 有合作需求时做 A2A 边界 Task/cancel/deadline/auth/error 映射与固定版本 fixture | 08,17、真实合作对象 | 与既有任务语义一致；可停 adapter；内部 gRPC 保留；无合作对象不占主线 |
| CA2-21 / QA+MODEL | 贯穿各阶段的失败家族、变形测试、多样性搜索、holdout 隔离、脱敏回流 | 01 起 | 修复带同族与反向回归；训练/标注/评测分离；不只加一条命中正则或累计 PASS |
| CA2-22 / OPS+QA | 协议/模型/目录/设备兼容矩阵、灰度、休眠/压力/撤回演练、车型接入与试点报告 | 主线、16/17/19；AM5 产品前提 | 对账在途动作后可回退；设备包/服务 SHA 分开；已知边界与支持范围签收；CI/CD/部署另授权 |

## 4. 首轮 PR 拆法

以下为推荐提交边界和工时估计，包含定向测试，不含等待；按一名熟悉对应模块的实现者估算，
R0 取数后再校准。多个包会触碰同一模型/持久化文件，按顺序合入。

### PR-A：CA2-01 + JV00，基线冻结（3–5 人日）

1. 新建拟定的 baseline manifest 与标注说明，固定实际 checkout，不复制本文件基线当现场状态。
2. 核对现有 `probe_qa_regression.py`、`probe_history_window.py`、手册探针、意图门禁和 Android 验包能力；
   用它们取证。需要的新 evaluator 再新增，不复制请求/签名/清理框架。
3. 把“混合句回答丢失、问句误执行、改口后旧值、旧确认寻址、车态陈旧、重复副作用”分类重证。
4. 固定主要业务指标、各阶段时延、cost/eligible/skip 口径；新测试集按家族切分，列出已看过的回归集。

DoD：离线可重复证据包与尚缺现场项清单均齐；两份基线采样可逐项比较。
需要线上/设备/真人的格可以保留待测，但 R0 退出必须满足路线图中冻结的适用格，不能用缺失值冒充 0。

### PR-B：CA2-02，步骤消费范围（3–5 人日）

先在现有 `models.py`、`step_record` 与 `step_call_context` 上定唯一字段语义，
再接 planning/engine/dispatcher/loop 和受影响 Agent。测试原话不可伪造、补槽不污染兄弟步骤、
一个 step 可消费多个相关分句，以及 `whole_utterance` 能力不被强行切碎。
默认兼容旧记录；保留服务端完整原话供安全闸，不把 resolved_question 交作授权依据。

### PR-C：CA2-03，稳定目标和 revision（3–5 人日）

实现服务端身份、来源引用、挂起/恢复、改口和撤销后的稳定映射；
`covers=[]` 的旧记录不按“全部完成”补齐。先保证事实能装得下，再单独评估目标分解质量。
依赖字段进程内→Redis→恢复全链 round-trip；不开 `PLANNER_GOALS`，不新增规划重试。

### PR-D：CA2-04，结果集合贯通（5–8 人日）

1. 将完成结果与摘要分开；检查 engine 挂起、D0、T2、取消、失败和终态所有输出出口。
2. 补传输与旧客户端投影；两端复用共享纯逻辑，HMI/mobile 分别渲染。
3. 案例：“打开后备箱，再告诉我空调有哪些模式” → 手册答案/引用保留 + 后备箱待确认；
   用户取消后仍能追问手册内容，不能只剩确认话术。
4. TTS 可短播，卡片/任务结果保留全量；重连、乱序 delta 和旧 revision 必须可重放验证。

DoD：两个客户端看到同一事实集合，动作/卡片/话术/trace 对账；未取得设备证据标“离线通过”而非签收。

### PR-E/F：CA2-05 / CA2-06+12，能力与车态（各 5–8 人日）

E 先盘点既有能力与声明缺口，冻结类型/单位/版本/缺省值，再做兼容往返。
F 先冻结事件和模拟生产者，在同一版本把所有消费者接齐；两个虚拟车、局部停止更新、乱序/重启是首要反例。
生产多车路由与 ACL/配置变更另审，不能用测试 fixture 的 vehicle_id 冒充认证隔离。
随后 CA2-07 汇合，不把 Vehicle Context View 写进 Memory 充作新事实库。

### PR-J1/J2/J3：JV01 / JV02 / JV03（3–5 / 3–5 / 4–6 人日，标注与外部等待另计）

先 provider+验证器，再快照/失效/预算，最后 shadow 接点和 evaluator；
无凭证时用 fake provider 验协议和故障，不能编造 Jev 质量数据。
J 系列可在 PR-A 后独立推进，但消费主线新契约时必须等待接口冻结；首轮不主动采纳模型输出。

## 5. Jev 工作包细化

| ID / 主责 | 交付物与建议落点（未有者为拟新增） | 依赖 / 验收 |
|---|---|---|
| JV00 / QA+MODEL | CA2-01 共用 manifest；分任务 rubric、数据政策、预算与中文标签说明 | 无；原始研究 2500 标注单位 + 600 挑战项 + 30 长会话为扩展目标，不冒充已建数据 |
| JV01 / RT | llm.proto Decide；`runtime/decision_contract.py`、gateway decision_provider/service/specs；SDK/Cloud 复用 gRPC 连接 | JV00；Noul/Choice/Score 显式类型、pin/ID/数值/分布/完整性校验；旧接口兼容 |
| JV02 / RT+SEC | `decision_support.py`、WorkingSet 投影、请求绑定、脱敏、有界 shadow、限流/成本与观测 | JV01；撤权/换人/打断/候选更新/晚到/队列满可复现；零额外执行与主链阻塞 |
| JV03 / MODEL+QA | actionability 异步 shadow；拟新增 `scripts/eval_decisions.py`、`test/eval_corpus/decisions/` | JV02；覆盖 eligible/skipped_by_path/observed，中文校准与分歧人工判定；actionability.py 无网络 |
| JV04 / MODEL+RT | skills/exemplars 可选候选稳定重排、原预算渲染与归因 | JV03；A 原池/B 扩池原排/C 同 B 池 Jev 排；比较 C-B；policy/词法保留项不淘汰 |
| JV05a / RT+QA | RAG 候选/附图纯重构，覆盖 lexical、目录路由、视觉与复合问句 | JV02、最新 CA2-01；A0=原链，A1=重构 off；文本/页/顺序/图/hash/异常等价 |
| JV05b / MODEL+RT | RAG 文本候选重排接点，最终附图一次性预算；证据充分性/答案复核另留后批 | JV05a；同池 A1/A2、知识 hash、整本门禁；视觉精确命中与硬 pinned 项不进淘汰 |
| JV06 / MODEL+RT | 合法 capability 和可选历史 exchange 排序 | JV04；对接 CA2-03/07 后复用稳定引用；完整必需能力召回不降，route_hint 和执行目录不裁 |
| JV07 / RT+MODEL | plan_semantics shadow；后续一条语义触发接原 retry_policy | JV03,JV06；主动模式只在首次副作用前可完整复核的路径；spy dispatcher、共享 deadline、最多一次，不加外层 while |
| JV08 / RT+SEC | memory 候选异步 review，复用抽取/OwnerKey/forget | JV02、数据策略；不直接写/删、不吞显式记住/忘记；同意代际与旧任务失效 |
| JV09 / QA+OPS | 每任务独立会话桶、故障演练、off 与观测/账单对照 | 对应任务过门；一个在线车道一批发布，不把 JV04/05 一起启用归因 |

JV05a 需区分两种承诺：对原有 top_k 的 off 接口行为严格等价；扩大候选池和按最终排序统一图预算
是单独实验变量，不能因为其修复了旧图分配而把收益全归 Jev。词法首页的保留规则如需改变，另开消融并复验，首批不改。

### 5.1 网关契约与故障清单

- 请求：内部 request/exchange、原话/状态/scope/candidate/consent 指纹绑定；任务 allowlist、rubric 版本、payload schema、剩余预算。
  caller 身份由服务鉴权提供，不接受 payload 自报。
- 响应：model_requested/model_used、typed answers、task/rubric、status/reason_code、usage（缺失 unknown）、latency；
  `decision_applied` 在真正消费处记，网关成功响应不等于业务已应用。
- 模式：全局 off 禁止全部外呼；shadow 丢样不阻塞；advise 只管被批准的建议范围；canary 是分流，不是新模型语义。
- 有效性：迟到、候选内容/顺序改变、模型/rubric 漂移、未知 ID、半批响应、NaN 一律不应用；重排整批退原顺序。
- 在线禁 SDK 隐式重试，429/529/超时直接弃权；不做周期付费探活；所有副本共享账户预算。
- 动态缓存仅请求内；原文/记忆/地址/key 不作默认日志或 Prometheus label；发出请求后本地超时仍可能计费。
- `.env.example`/运行配置仅在实现与批准后落地；本方案中的 DECISION_* 都是拟定名称，不是可立即设置的功能。

### 5.2 中文校准与收益门

按会话/原始 badcase/改写家族/手册章节分组切分，开发/校准/冻结测试建议 50/25/25。
已有 135 条手册留出集已被研究/修复查看，后续当回归集使用；新结论另冻结未调参的 holdout。
先标出 ambiguous/unresolvable，高风险和分歧样本双人审定；没有复核者时该部分保持未签收。

每任务分别报告：采纳比例、采纳子集错误率、误澄清/静默丢请求、完整多能力召回、同池重排指标、
最终任务成功、端到端首音/完成 p50/p95/p99 与增量成本。阈值在 calibration 集冻结，
不把 `confidence>0.8` 当通用授权，不把全部弃权记作 100% 准确。

critic 的 precision≥95% 仅是研究建议的启动目标，必须同时给置信区间、漏检和有效采纳数。
未达到收益/风险/时延要求则留 shadow 或 off；Jev 不可用回现有基线不等于该基线已获新的安全认证。

## 6. 最小迁移顺序与回滚

| 变更面 | 先做 | 再切换 | 回退必须保留 |
|---|---|---|---|
| proto/Manifest/Step | 增量字段、兼容读、旧数据 fixture、统一序列化 | 全链支持后启用需要新语义的能力 | 旧字段、版本识别、来源/确认不造假 |
| ResultBundle | 后端存全量结果、旧字段投影、共享前端解析 | 新客户端启用分组结果展示 | 完整事实与证据；旧客户端不支持的新确认不得执行 |
| 车辆事件 | schema/仿真、认证映射、版本化消费者 | 受控切生产者与路由，逐车辆核对 | epoch/seq、逐信号质量、未知状态；不默认为默认车 |
| Task/Operation | 兼容 schema 设计与迁移 dry-run（需授权）、持久准入 | 一个副作用任务类型先灰度 | 在途操作 ID/日志/确认消费记录，先停写对账再回退 |
| Jev | off 等价、shadow、校准、单车道 canary | 每项按门槛 advise | 原目录/排序/知识、既有安全检查；off 后零新增请求 |
| T1e / 车辆驱动 | shadow/只读/仿真、目标板或 OEM 沙箱 | 车型限定能力，执行点重验 | T0 与基线驱动、未完成操作对账，不假报成功 |

不做破坏性就地数据变换作为首步。数据库 schema、`.env`、CI/CD 和云端 apply 属独立授权事项；
本轮用户已授权文档提交/推送，不能把这份计划的落盘当作后续线上写入授权。

## 7. 验收矩阵

### 7.1 核心旅程与故障

| 用例家族 | 关键前置/扰动 | 机器必须核对的事实 | 归属 |
|---|---|---|---|
| 混合问/做/否定 | 定义座椅加热 + 空调 22 度 + 后备箱别动 | 只执行被授权的空调；回答定义；多目标逐项有结论 | 02–05 |
| 挂起不丢答案 | 后备箱待确认 + 手册多段答案，随后取消/追问/重连 | 已完成全文/引用仍在；取消只作用对应 operation | 03/04/09 |
| 世界改变再确认 | 触屏修改、挡位改变、跨端重复确认、改参/换车 | 旧确认无效，执行点拒不满足的前置；零二次副作用 | 06/09/11/12 |
| 消息与观测异常 | 两车并行，电量新/挡位旧，乱序、重复、重启 epoch | 不串车、不刷新无关信号；陈旧/无关联保持 unknown | 06/07/10 |
| 崩溃与 ACK 丢失 | 动作已发生但回执丢、写日志前崩溃、云网断开 | 不盲重试；可查询则对账，不可查则待核实 | 08/10/11 |
| 能源导航长任务 | 站点下线、偏好更新、路线改口、重启 | 约束不丢、已发生导航不重复、来源/时效可追溯 | 15/18/19 |
| 语音/视觉 | S2S interpretation 与 final 转写相反；barge-in；旧帧；电话抢焦点 | 原话权威；停播不假称取消；过期帧不当当前事实 | 02/16 |
| 外源注入 | 手册/网页/MCP 返回“忽略权限”，跨 owner 句柄 | 外源只作数据；权限、目标车辆与授权原点不变 | 07/17 |
| Jev 故障与晚到 | 429/529/坏模型/半批/NaN/换人/撤权/队列满 | 整批退基线或 stale；主链不堵、不双执行、不复活旧记忆 | JV01–03/08/09 |
| RAG 排名与图片 | 第 6 候选升第 1、视觉命中、未知专名、目录路由 | 按最终顺序附图且预算统一；hard veto/pinned/数值闸保持 | JV05 |
| critic 调度边界 | T1/T2/D0/降级/挂起恢复；原重试已用尽 | 适用路径零先发；最多一次语义触发；未覆盖路径明确 skipped | JV07 |

Jev 研究 J001–J030 全部保留为 JV 契约验收种子；实际映射由 evaluator 清单登记，
其中 J001/002、J010/011、J013/014、J020–022、J025/026、J027/028 是首批必含的成对反例。
长会话按真实前置状态标注，不能把对话拆成孤立句再声称覆盖挂起/续接。

### 7.2 门槛与版本证据

R0 冻结 200 条核心旅程 × 5 次的计划、采样独立性与适用范围，目标至少 95% 的旅程 5 次均达标。
安全合同必过集零新增误执行/越权/串车/确认重放/重复副作用；同时报告误拒和任务损失，不能靠一律拒绝过门。
Jev 的 A0/A1/A2 和扩大候选池的 B/C 对照分开；真实副作用只有一臂执行，另一臂只回放计划。

每份报告至少登记：代码 SHA、部署 SHA、设备包 SHA、chat 与 decision 实际模型、rubric/calibration hash、
目录/提示词/策略/知识/协议版本、原始输入 split、路径覆盖、开始/结束状态、skip/失败、动作与清理、原始 artifact。
观察到零失败只对相应数据集成立；无硬件/无真 API/无真人的格写未测。

### 7.3 已有命令与后续新增入口

下面均是现有离线入口；按改动范围选择，不因文档规划执行真栈：

```powershell
python scripts/dev_stack.py target show
python -X utf8 -m pytest -q orchestrator/cloud/tests --import-mode=importlib
python -X utf8 -m pytest -q agents/manual_rag/tests --import-mode=importlib
python test/smoke_edge.py
python test/eval_skills.py
python test/eval_exemplars.py
python scripts/check_intent_gate.py
python test/eval_capability_integrity.py
```

全量固定口径仍看 AGENTS.md §6；移动端设备取证沿既有操作指南。
`eval_decisions.py`、`probe_decisions.py` 和 v2 故障 harness 尚待实现，不能现在运行这些拟新增入口。
新契约门需要并入 CI 时，先完成本地实现与验证，再带具体 CI diff 请求授权。

## 8. 依赖与不可替代的外部输入

| 缺失输入 | 影响任务 | 可先完成的工作 |
|---|---|---|
| 实际人力、目标板、OEM 型号/接口与许可 | R0 排期、14/18/22 | 契约与仿真；不把 24 周估计转成个人承诺 |
| Jev 凭证、实际额度/数据保留约定、调用预算 | 真 API 校准、shadow/advise | fake provider、输入脱敏、离线 evaluator；真收益保持未测 |
| 车型前置条件、TTL、幂等/回执/查单语义 | 06/09–11/18 | 可执行的模拟故障与语义草案；不猜量产阈值 |
| 数据授权、标注复核者、未污染 holdout | 01/21、JV03–08 | 合成合同/故障集、标注协议；真实日志不自动传新供应商 |
| 真人声学与双端设备、AM5 身份/投递条件 | 16/22 | 共享逻辑/模拟设备检查；不把单测通过当体验签收 |

## 9. 外部依据复核（2026-09-26）

本轮复核了 [Jev 模型页](https://docs.typesafe.ai/models)、[HTTP 契约](https://docs.typesafe.ai/api)
与[置信度定义](https://docs.typesafe.ai/confidence)：固定 `jev-1.13.0`、独立 systemone 接口、文本输入、
Noul 不带独立 confidence；中文需项目单独验证。没有发起付费推理，价格/限流上线前再读官方当前值。

[AOSP VSIDL](https://source.android.com/docs/automotive/sdv/core-areas/vsidl)确认基于 protobuf 的服务定义与 SOME/IP 映射边界；
[A2A v1.0](https://a2a-protocol.org/latest/whats-new-v1/)仅作为可选协议基线；
[MCP 安全指南](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)用于 CA2-17 的准入/委托设计。

[车辆函数调用研究](https://arxiv.org/abs/2609.09476)支持比较固定表示与 Schema 的实验思路，
[多样性测试研究](https://arxiv.org/abs/2609.23209)支持按失败类型组织搜索。
以上是采用方法的依据，不是本项目性能、安全或量产效果证明；其余行业材料保留在原研究及其访问时点下。
