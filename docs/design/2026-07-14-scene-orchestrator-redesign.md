# 场景编排 Agent（scene-orchestrator）重设计：从静态预置激活器到「用户造场景 + 策略引擎」

- **状态**：**v2.1 评审修正版（2026-07-14）；P0 已落地并真栈验证（e2e_scene 14/14）**，落地纠偏见 §0.5，实施计划见姊妹篇 `2026-07-14-scene-orchestrator-implementation-plan.md`。v2 增量（泓舟提出「根据目标/环境/执行结果动态决定做什么、先做什么、失败后怎么办」）：Policy DSL v2 + 激活期 Ground·Solve 确定性具象化 + Verify-Repair 失败闭环，对应 D9/D10/D11 与 §4.1/§5.2/§5.3。**v2.1 修正（泓舟评审 4 指摘，全部成立）**：① DSL 与 PG 字段不一致（owner↔user_id、goal/guards 缺列）→ §4.1/§4.2 对齐；② 环境缺失条件视为满足会让互斥分支同时生效（两条 hvac 互相覆盖的实 bug）→ §5.2 改三态求值 unknown=exclude+话术告知、guard missing 降级 confirm；③ 单一 SCENE_ACTIVE 与异步 Verify 竞态（新激活/退出覆盖后旧 verify 错账假警）→ §5.3 activation_id 代际护栏（R4.3b epoch 同款）+ 对账清单随 task 闭包传参 + 进程内单飞；④ 退出恢复按原始场景而非本次实际执行 → §5.4 恢复基准改 `solved_actions`（快照采集同源），防覆盖用户手动调整。
- **交付对象**：Claude Code（评审通过后按 §11 分阶段清单执行）
- **关联代码**：`agents/scene_orchestrator/`（0.1.0 现状，本次重构对象）、`orchestrator/edge/knowledge/commands.yaml`（VAL 对象/操作唯一真相源，编译白名单来源）、`orchestrator/edge/server.py:660-705`（云端 action 回流分发：仅 `vehicle.control*` 交 VAL）、`orchestrator/edge/edge_call.py::action_to_structured`（场景动作→VAL 结构化翻译）、`orchestrator/edge/fast_intent.py:45-48`（scene_mode 端云分工决策注释）、`agents/reminder/src/{scheduler,store,timeparse}.py`（时间调度/PG 存储/中文时间解析样板）、`agents/road_safety/src/agent.py:43-100`（NATS 事件订阅+节流+proactive 样板）、`memory/routine.py`（习惯聚合先例）、`gateway/edge/main.go:315-333`（agent.proactive→HMI 投递）、`agents/reminder/manifest.yaml:43-73`（route_hints 全要素样板）
- **关联文档**：`docs/design/2026-06-20-standalone-agents-roadmap.md` §3.2/§8（0.1.0 原设计与 VAL 命令对齐闭环）、`docs/conventions.md` §1/§2/§5/§9（落地时登记）、`docs/design/2026-07-11-reminder-agent-design.md`（D2/D3/D4 决策直接复用）、`CLAUDE.md` §3/§5（新增 Agent 流程与安全红线）

---

## 0.5 落地纠偏（2026-07-14 P0 实施记录：设计与代码事实不符之处）

设计成文时对几处现状的假设与代码不符，实施时按**代码事实**纠偏，结论如下（后续 P1-P3 以此为准）：

| # | 设计原文 | 代码事实 | 落地做法 |
|---|---|---|---|
| A1 | §5.2 Ground「读环境：`ctx.fetch("vehicle_state")`」 | **memory 没有 `vehicle_state` scope**。manifest 的 `context_scopes: [vehicle_state]` 只控制一个 meta 键（`vehicle_battery`）是否下发（`orchestrator/cloud/clients.py::_SENSITIVE_SCOPE`）。车况真相源是端侧 VAL 经 NATS `vehicle.state.changed` 广播（增量 diff + 每 `OBS_SNAPSHOT_INTERVAL`=30s 全量快照），gateway/collector 都是订阅建镜像 | 新增 `src/state_mirror.py`：`on_start` 订阅一条 NATS，进程内维护全量车况镜像。**P0 就要它**（deactivate 的激活前快照靠它），不是 P2——设计把订阅排在 T-P2.3 是错的。订阅按「一条多消费方」写好（`on_change(cb)`），P2/P3 直接挂回调 |
| A2 | §5.2e/§5.4「尾缀 `scene_mode.set {mode:<key>}` / `off`」 | `commands.yaml` 的 `scene_mode.modes` 是**闭合枚举**（nap/camping/…），既没有 `off`，更容不下用户自建场景键——VAL `_validate_command` 会拒，`edge_call` 会把 mode 静默丢掉 | `commands.yaml` 的 `scene_mode.modes` 改为**开放值域**（`modes: []` + 注释）：用户可造场景 ⇒ 场景键是运行期数据不是出厂词表；scene_mode 只是状态标记、零安全面，权威场景集在本 Agent |
| A3 | §11「P0 交付判据：真栈 e2e」 | **端侧快路径会把造场景的句子整句劫走**：「创建钓鱼模式：氛围灯调到10%，空调22度」被 `fast_intent` 当本地多意图车控**当场执行**（灯真调暗、空调真开），请求根本没上云 | `fast_intent` 顶部加**场景管理句护栏**（`_is_scene_management`）：造/改/删「X模式」的句子一律 None → 交云端。句中的车控词是**场景内容**不是当下指令。同一条分工在文件头 45-48 行已有先例（`scene_mode.set` 刻意不入 `LOCAL_INTENTS`）。D8 的端侧模式词（运动/省电…）仍留端侧 |
| A4 | D10「`_dispatch_cloud_actions` speech 逐条覆盖是真缺陷 → 用 P2 的 Verify-Repair 事后诚实汇报对症」 | 信息其实就在分发器里：循环内 `new_speech` 被逐条覆盖，导致 ①失败被后续成功盖掉（D10 说的）②**场景的总结话术被最后一条动作的 VAL 通用应答顶成「好的」**（每次激活必现） | 直接在 `_dispatch_cloud_actions` 修根：单条动作仍用 VAL 精确应答（含真实温度/档位，逐字保持现状）；**多条动作保留云端总结话术**；**任何拒绝都要浮出来、不被后续成功掩埋**。P2 的 Verify-Repair 仍有价值（VAL 接受了但状态没落地、驻车补做、幂等重试），二者互补 |

**顺带修掉的两个既有真缺陷**（都由本次真栈 e2e 暴露）：
- `scenes.yaml` 预置场景的 `mode: auto/quiet/external_circulation`、`color: warm_orange` **全在 VAL 词表之外**，被 `edge_call` 静默丢弃——即「露营模式说了开外循环，其实没开」。已改为词表内取值，并加契约测试 `test_builtin_scenes_are_catalog_valid` 逐条钉死（这正是 D3「词表唯一真相源」要根治的漂移）。
- 聚合器对 `FAILED` 只取 `error` 码拼「抱歉，处理失败」，**会把 Agent 的诚实话术整个丢掉**（`aggregator.py:119-121`）。故本 Agent 面向用户的拒绝一律用 `OK` 状态（与 v1「没有找到X场景」既有做法一致），`FAILED` 只留给真·内部错误。

---

## 0. 决策纪要（推荐结论，待泓舟拍板）

| # | 决策 | 推荐结论 | 未选路径及理由 |
|---|---|---|---|
| D1 | 产品定位 | **补齐第二代（用户自定义场景）为主线，第三代（LLM 一句话造场景）作为创建入口一并落地**：`scene.create`「帮我建一个钓鱼模式：座椅放平、开外循环、氛围灯调暗」一句话生成场景，存为用户资产可反复唤起 | 未选「只扩预置场景库」：多写几个 YAML 不改变产品代际，行业已证明自定义是分水岭（理想任务大师 120+ 动作/50+ 条件、任务码社区自发繁荣）；未选「直接做全自动触发引擎」：触发运行时依赖场景对象模型先定型，P2 再上（见 D6） |
| D2 | LLM 角色 | **编译器，不是执行器**：LLM 只在 `scene.create` 创建期把自然语言编译成场景 DSL（JSON），逐动作过 VAL 词表白名单校验 + NEED_CONFIRM 回读确认后落库；**激活/触发/执行全程零 LLM**，动作经既有确定性链路（编排 actions → 端侧 `action_to_structured` → VAL 归一/校验/安全门控） | 未选「激活时 LLM 现场展开动作」：违反规划/执行分离红线（CLAUDE.md §5），且同一场景每次执行结果必须确定可预期；未选「纯表单/HMI 编辑器创建」：车内语音免手是本项目主交互，表单编辑器留 P3 可选 |
| D3 | 编译白名单来源 | **构建期 COPY `orchestrator/edge/knowledge/commands.yaml` 进 scene agent 镜像作只读词表**（对象/操作/参数枚举），编译时静态校验每条动作；不可翻译的动作**剔除并在回读中诚实告知**（「放舒缓音乐我还做不到，已跳过」） | 未选「云侧重新维护一份动作词表」：两处真相必然漂移，0.1.0 已吃过这个亏（roadmap §8：scenes.yaml 命令词表未对齐 VAL 导致动作全部静默失效）；未选「不校验，执行期靠 VAL 丢弃」：用户确认时看到的动作执行时静默消失，信任崩塌 |
| D4 | 存储 | **自有 PG 表 `scene_item`**（asyncpg，同 PG 实例独立表，照抄 `reminder/store.py`；无 PG 诚实降级内存态+警示日志）；预置 4 场景仍留 `scenes.yaml` 随镜像发布，运行时合并视图，**用户同名场景遮蔽预置** | 未选 profile KV：场景是跨会话持久资产且 P2 触发器需要「全用户枚举 enabled 触发器」，KV 无此索引（与 reminder D2 同理）；未选「预置也迁 PG」：出厂预置随版本 OTA 演进，留文件便于发版管理 |
| D5 | deactivate 真恢复 | **激活时快照受影响状态键（经 `context_scopes: [vehicle_state]`），deactivate 产生恢复动作；快照缺键退「反向默认表」**（seat 复位/volume 恢复 50/ambient_light 关/hvac auto 24）。恢复动作里含座椅等危险类时照走 NEED_CONFIRM | 未选「维持现状嘴炮」：「退出午休模式」座椅还躺平着，是功能性欺骗；未选「纯反向默认表」：用户激活前空调本来 22 度，退出被改成 24 度，体验違和——快照优先、默认表兜底 |
| D6 | 自动触发安全模型（P3） | **询问式触发**：触发器命中只产生 `agent.proactive` 建议卡（「检测到电量低于20%，要开启省电出行模式吗？」+ 按钮 send_text「开启省电出行模式」），**执行永远经用户显式指令走正常语音链路**（权限/确认/VAL 门控全不绕过）。触发路径自身不下发任何车控动作 | 未选「静默自动执行」：自动化规则在行车环境直接动车身是量产不可接受的安全面（即便 VAL 有门控，也不该让触发器成为第二条执行入口）；per-scene「免确认自动执行」留 P3 且限非车控动作 |
| D7 | 触发调度归属（P3） | **scene agent 进程内自治**：时间触发照抄 reminder 调度（poll + 原子领取），事件触发照抄 road-safety（on_start 订阅 `vehicle.state.changed` + 节流）；两个 watcher 各 ~70 行 | 未选「委托 reminder 代管时间触发」（scene 创建一条 source=scene 的提醒）：跨 Agent 写数据、提醒列表混入非用户契约条目、删场景要级联删提醒，耦合大于复用收益；未选「独立 trigger 服务」：PoC 单实例过度工程（reminder D3 同理） |
| D8 | 端侧冲突边界 | **route_hints 的 guard 显式排除端侧模式词**：驾驶/运动/舒适/经济/雪地/性能模式归 `driving_mode.set`、省电/电量模式归 `power_mode.set`（均为端侧 LOCAL_INTENTS 本地秒回），场景编排只接命名生活场景。`scene_mode.set` 端云分工维持 fast_intent.py:45 现状：端侧只设状态位，编排恒在云端 | 未选「场景编排抢所有带『模式』的话」：会把毫秒级端侧车控劫持成 1.5s 云端往返，违反 L0 时延约束 |
| D9 | 动态智能的位置（v2 增量核心） | **「聪明在编译期，可靠在运行期」两段式**：LLM 编译期产出**带环境条件与目标断言的策略（Policy DSL v2，§4.1）**；激活期新增确定性 **Ground·Solve** 步（§5.2）——读环境（vehicle_state/时间）→ 逐动作 `when` 求值裁剪 → **幂等跳过已达成项** → guards 前置检查 → custom_params 覆盖 → 产出本次具体动作序列。同一场景在 35℃ 和 5℃ 展开不同动作，但**同环境同结果、全程零 LLM**。范式复用 trip-planner 已验证的「LLM 提议骨架 / 确定性 Ground·Solve」四段流水线 | 未选「激活/执行期 LLM 现场规划」：不可预期 + 违反规划/执行分离（D2 不动摇）；未选「在 Agent 内做通用目标规划」：**临时目标的动态编排本来就是 Cloud Planner 的职责**（T1 DAG/T2 有界循环），在 Agent 里再造 Planner 是架构倒退——scene 与 Planner 的分工是「Planner 负责第一次的聪明，场景负责每一次的可靠」（D11）；未选「运行时动态重排序」：车控动作间真实依赖极少，执行序由编译期定死（人类直觉序 + `when` 裁剪），可预期性优先 |
| D10 | 失败闭环形态（v2 增量核心） | **验证-修复（Verify-Repair）后台闭环**：动作下发后 handle 已返回（现状架构），故起后台 task（deep_research 异步先例）等待 3~5s → 消费 NATS `vehicle.state.changed`/查 `vehicle_state` 镜像 → 对比编译期产出的 `assert` 期望态 → 未达成项按声明的 `on_fail` 分类处置：`skip`=汇总诚实播报（proactive）/ `retry_suggest`=建议卡带「重试」按钮（send_text 回发激活原话，**幂等 solve 自动只补缺失项**）/ `defer_p`=挂驻车补做 pending（gear=P 事件触发建议卡）。**全程 fail-open**：拿不到状态就静默跳过，绝不假警 | 未选「改端云协议加动作级回执流」：动 proto+网关+编排三层，成本远超收益，vehicle.state.changed 广播已是现成反馈通道（road-safety 消费先例）；未选「后台直接重发动作」：**执行入口唯一性不可破**——proactive 通道只有 speech+card 无 actions，这不是限制而是安全架构的正确性，repair 一律以建议卡回到正常语音链路；未选「不做闭环维持现状」：现状 `_dispatch_cloud_actions`（server.py:696-701）循环内 `new_speech` 逐条覆盖，**多动作场景中间某条被 VAL 拒绝、后续成功会把失败话术覆盖掉——失败对用户完全静默**，是实测可复现的真缺陷，verify 闭环直接对症 |
| D11 | 与 Planner 的闭环叙事 | **临时智能 → 沉淀 → 可靠复用**三段桥：临时目标句（「我有点困想睡会」且无既有场景）走 Planner 现状编排；执行后经「把刚才这些存成午休模式」（P1 会话沉淀）固化为场景；此后每次激活走确定性 Ground·Solve + Verify-Repair。scene-orchestrator 不接管临时目标 | 未选「scene agent 抢接目标句」：与 Planner 职责冲突且重复建设；route_hints 只接「X模式」词形，目标句自然落 Planner |

---

## 1. 问题与价值

### 1.1 现状：0.1.0 是一代产品（静态预置激活器），七处硬伤

`agents/scene_orchestrator/` 现状：4 个预置场景（回家/露营/午休/浪漫）写死 `scenes.yaml`，语音激活展开为动作序列经 VAL 执行。链路本身是通的（roadmap §8 已闭环「Agent 产动作→VAL→执行」红线验证），但产品形态停在第一代：

| # | 硬伤 | 证据 |
|---|---|---|
| 1 | **用户不能造场景**：「帮我建一个钓鱼模式」无法响应，场景集合 = 出厂 4 个 | `scenes.yaml` 静态加载，无写路径 |
| 2 | **deactivate 是嘴炮**：只回「已退出XX」话术，座椅还躺着、灯还暗着 | `agent.py:118-126` 无任何恢复动作 |
| 3 | **manifest 声明的 `custom_params` 代码从未消费**：「开启午休模式但温度 26」参数覆盖不存在 | `agent.py::_activate` 只读 `slots["scene"]` |
| 4 | **无 route_hints**：「露营模式」三字短语全靠 LLM 语义路由，弱 LLM 漏路由无兜底（reminder/nearby 等后来的 Agent 都补了） | `manifest.yaml` 无 route_hints 节 |
| 5 | **无触发器**：只能语音激活，「到家自动」「低电量建议」「每天午休时间」不存在——而 reminder（时间）和 road-safety（事件）已各自单点验证了两类触发 | 全仓 grep 无 scene 触发路径 |
| 6 | **激活不写 `scene_mode` 状态位**：VAL 明明有 `scene_mode` 对象（`commands.yaml:413`），车辆状态镜像/右舞台不知道「当前在露营模式」，deactivate 也无从判断当前场景 | `_activate` 动作列表无 scene_mode.set |
| 7 | **场景无 owner 概念**：无 per-user 隔离，未来多用户/分享全无地基 | scenes.yaml 全局共享 |

### 1.2 行业调研：场景编排三代演进，自定义是分水岭

**第一代：预置模式**。特斯拉露营/宠物/哨兵模式、蔚来小憩模式为代表——厂商写死的场景包，用户只有开关权。0.1.0 就在这一代。

**第二代：用户自定义 TCA（Trigger-Condition-Action）自动化**。标杆是[理想汽车「任务大师」](https://zhuanlan.zhihu.com/p/705815629)：座椅/驾驶/门窗/灯光/导航/语音/蓝牙/应用等 10+ 领域功能联动，**120+ 执行动作、50+ 触发条件**，条件全部满足即自动按序执行动作；任务可导出「任务码」在社区分享（[42号车库任务码专区](https://www.42how.com/en/label/2818)、[汽车之家 29 个任务码帖](https://club.autohome.com.cn/bbs/thread/3a3e30c92ba193f2/105567998-1.html)），用户自发生态证明了这个能力的真实需求密度。智能家居侧 Home Assistant / iOS 快捷指令是同一范式的成熟参照。

**第三代：LLM 生成 + 主动建议**。[理想任务大师 2.0](https://ai.zol.com.cn/911/9118390.html)（基座模型 MindGPT）实现**一句话语音生成自动化任务**——用户说需求，车端自行「编程」出任务（[新浪科技报道](https://finance.sina.com.cn/tech/roll/2024-10-25/doc-inctucpv5632775.shtml)）；OTA 7.0 又加「AI 任务大师单次版」，一句话搞定即时/延时组合控制（[新出行 OTA 7.0](https://www.xchuxing.com/article/145042)）。华为鸿蒙座舱把 200+ 车内功能封装为原子服务、场景引擎动态编排（[36氪智能座舱分析](https://auto-time.36kr.com/p/1894892690297345)），MoLA 架构打通意图理解到硬件执行（[汽车之心](https://www.autobit.xyz/news/4339.html)）。学术侧 NL→TAP 规则生成已有成体系工作：[ChatIoT 零代码 trigger-action 程序生成](https://www.emnets.cn/zh/publication/ubicomp-24-chatiot/chatiot.pdf)、[LLM 生成 Home Assistant 自动化](https://arxiv.org/html/2505.02802v1)、[微调 LLM 做端用户 TCA 定制](https://link.springer.com/chapter/10.1007/978-3-031-95452-8_2)——共同结论：**LLM 负责把自然语言编译成结构化规则（先拆 trigger/action 再逐段生成、schema 校验、白名单对齐），运行时执行保持确定性**。这与本项目「规划/执行分离」红线天然同构。

**对本项目的映射**：我们不做任务大师的全量对标（50+ 条件面板是触屏产品形态），而是取其内核——**场景 = 用户可自然语言创建、可持久复用、可被触发建议的命名动作包**，并用本项目已有的安全架构（VAL 门控/NEED_CONFIRM/权限模型）做出差异化：**创建期 LLM 编译 + 执行期全确定性**，这条纪律理想公开材料里没有强调，却是量产安全叙事的核心卖点。

**v2 差异化升级（超越而非追平）**：任务大师的「条件」是**触发条件**（何时执行），动作序列本身仍是静态录制——夏天创建的露营模式到冬天还是开冷气。本设计把条件下沉到**执行策略**（如何执行：`when` 环境分支、`guards` 前置检查、幂等跳过）并引入**目标断言**（执行到什么程度算成：`assert` 期望态 + Verify-Repair 闭环）。场景从「录制的宏」升级为「声明式期望态 + 确定性调和器」——同一个午休模式，35℃ 自动走制冷分支、行车中座椅动作被拒会诚实告知并在驻车后主动补做建议。这套 desired-state reconciliation 心智（K8s 在车内场景的映射）行业车机产品尚无对标，且全程不违反规划/执行分离。

### 1.3 为什么现在做、为什么值得做

- **全部零件已就位，纯组装**：LLM 网关（编译）、VAL 词表（白名单）、NEED_CONFIRM（确认链）、PG 样板（reminder store）、时间解析（timeparse）、事件订阅（road-safety）、proactive→HMI（四个先例）、route_hints 机制——§3 盘点全部有先例，无新机制。
- **补齐助手人格的「养成感」**：小舟能查能导能规划能提醒，但用户无法「教」它——自定义场景是用户在系统里沉淀个人资产的第一个入口，直接提升留存与人格黏性。
- **是后续主动智能的地基**：memory routine 已能聚合习惯（`routine.py`「周一星巴克」式建议），但建议无处落地——有了用户场景对象，「您最近三天午休都放平座椅+静音，存成午休模式吗？」才有闭环（§11 P4）。

## 2. 目标与非目标

### 目标
- G1 **一句话造场景**（`scene.create`）：「创建一个钓鱼模式：座椅放平、开外循环、氛围灯调到 10%」→ LLM 编译为动作序列 → 白名单校验 → 回读确认 → 存 PG，之后「开启钓鱼模式」随叫随到。
- G2 **激活闭环补全**：用户场景与预置合并匹配；激活附带 `scene_mode.set` 状态位；`custom_params` 真消费（「开启午休模式，温度 26」确定性参数覆盖）。
- G3 **deactivate 真恢复**：快照 + 反向默认表混合恢复，危险恢复动作照走确认。
- G4 **管理闭环**：list（区分「我的/内置」）、update（「把钓鱼模式的温度改成 24」）、delete（二次确认）。
- G5 **确定性路由**：route_hints 兜底「X模式」短语，guard 排除端侧 driving_mode/power_mode 词面（D8）。
- G6 **自动触发**：时间触发（「每天 12 点半」）+ 事件触发（电量/挡位/位置），**询问式**建议卡（D6），不新增执行入口。
- G7 **不改编排核心**：全部经 manifest 声明式字段（route_hints/context_scopes/ui_card）+ Agent 内部实现，零 orchestrator 改动（media 动作端侧分发扩展除外，见非目标与 §5.2 边界说明）。
- G8 **场景策略引擎（v2 增量）**：激活期确定性 Ground·Solve（环境分支求值 + guards + 幂等跳过 + 参数覆盖）+ 执行后 Verify-Repair 闭环（期望态验证 → 诚实汇报 / 幂等重试建议 / 驻车补做建议）——「做什么」随环境自适应、「先做什么」编译期定序运行期裁剪、「失败后」有声明式处置路径（D9/D10）。

### 非目标（明确不做）
- **行车中新增任何执行豁免**：安全门控完全依赖既有 VAL 流水线，本设计不引入任何绕过。
- **运行时自由规划**：激活/执行/修复期零 LLM、零现场发明动作——动态性全部来自编译期声明的策略结构（`when`/`assert`/`on_fail`/`fallback` 均在创建时经白名单校验落库）。临时目标的开放式编排归 Cloud Planner（D9/D11），scene agent 永不接管。
- **场景市场/任务码分享**：owner 字段留好地基，分享/导入是 P4+ 可选（涉及第三方内容审核，超出 PoC）。
- **HMI 可视化场景编辑器**：语音为主，触屏编辑器 P4 可选。
- **端侧离线触发**：触发 watcher 在云侧 Agent，断网时触发不工作（与 reminder 同边界，量产需端侧镜像，留档接受）。
- **P0 不含 media 动作**：端侧 `_dispatch_cloud_actions` 只回流 `vehicle.control*`（server.py:675），`media.play` 不在云端 action 回流路径上；P0 编译时诚实剔除并告知，P1 扩端侧分发一类后放开（改动在端侧执行器，不碰云端编排核心，属 R4.1b 端侧对象化同类改动，落地前单独确认）。
- **多乘员场景归属**：跟随 memory occupant 预留，不实装。

## 3. 现状扩展点盘点（全部有先例，零新机制）

| 需求 | 现成机制 | 证据 |
|---|---|---|
| NL→结构化编译 | SDK `LLMClient`（llm-gateway Complete，JSON 输出 + 失败重试样板遍地） | reminder timeparse LLM 兜底、trip propose 骨架 |
| 动作白名单 | VAL 知识库对象/操作/参数枚举 | `orchestrator/edge/knowledge/commands.yaml`（60+ 对象）；`edge_call.py::action_to_structured` 的翻译规则即校验规则 |
| 动作→执行 | 编排 actions 聚合 → 端侧 `_dispatch_cloud_actions` → VAL 归一/校验/安全门控 | roadmap §8 已闭环；`server.py:660-705` |
| 危险动作确认 | `NEED_CONFIRM` + SessionState 挂起（TTL 300s） | 0.1.0 `_activate` 已用；trip/nearby 先例 |
| PG 持久 + 内存降级 | 同 PG 实例独立表 + asyncpg | `agents/reminder/{schema.sql,src/store.py}` 照抄 |
| 中文时间→触发时刻 | `reminder/src/timeparse.py`（绝对/相对/recur 全解析） | reminder 单测 50 个用例背书 |
| 时间触发调度 | poll + SQL 原子领取（claim_due） | `reminder/src/scheduler.py`（72 行） |
| 事件触发订阅 | `on_start` 连 NATS 订 `vehicle.state.changed` + 分类节流 | `road_safety/src/agent.py:43-100` |
| 建议卡推送+朗读 | NATS `agent.proactive`（带 card） → edge-gateway → HMI | `gateway/edge/main.go:315-333`；reminder fired 卡按钮 send_text 先例 |
| 「第N个」跨轮指代 | shared_state profile KV | conventions §9（新增 `SCENE_ACTIVE` 键需登记） |
| 弱 LLM 漏路由兜底 | manifest route_hints | `reminder/manifest.yaml:43-73` 全要素样板 |
| 车辆状态快照 | manifest `context_scopes: [vehicle_state]` 最小化下发 | context 系统重构（2026-06-25）；charging 先例 |
| 当前场景状态位 | VAL `scene_mode` 对象（set/查询/responses 全备） | `commands.yaml:413`、`val.py:623-626`、entities `scene_modes` |

## 4. 场景对象模型（DSL）与存储

### 4.1 Scene DSL v2（编译目标 / 存储格式，JSON Schema 语义）

**设计原则**：动态性 = 平铺的可选声明字段（`when`/`assert`/`on_fail`），不是嵌套分支树——LLM（含弱模型）编译得动、确定性求值器 ~100 行写得出、schema 校验器一眼验得完。「互斥环境分支」用两条相反 `when` 的动作表达（夏冬各一条 hvac），表达力够用且验证简单。

```yaml
scene:
  id: "usr-a1b2c3"            # builtin 场景用 yaml key（go_home 等）
  user_id: "u1"               # 与 PG 列同名（v2.1 修正①）；builtin 不入库，运行时合并视图中 user_id=""
  name: "钓鱼模式"
  aliases: ["钓鱼", "钓鱼模式"]
  description: "座椅放平 + 外循环 + 氛围灯 10%"   # LLM 编译时生成，供 list 播报
  goal: "在湖边车里舒服地钓鱼休息"                 # v2：目标语句留档（回读播报 + P4 语义匹配）
  source: "user"              # builtin | user | derived(P4 习惯沉淀)
  status: "enabled"           # enabled | disabled
  guards:                     # v2：激活前置检查（Solve 第一步；不满足→提示并按 mode 处置）
    - { key: "battery", op: "gte", value: 20,
        mode: "confirm",      # confirm=NEED_CONFIRM 提示后可继续 | block=拒绝激活
        message: "电量偏低" }
  actions:                    # 有序执行（编译期定序，运行期只裁不排，D9）
    - type: "vehicle.control" # P0 词表：vehicle.control | navigate；P1 +media.*
      command: "seat.recline" # 必须命中 commands.yaml 对象/操作白名单（D3）
      params: { position: "front_left", angle: "160" }
      require_confirm: true   # 编译期依 §8.1 危险动作表强制标注，LLM 说了不算
      # ↓ v2 四个可选字段，全部缺省时行为与 v1 逐字一致
      when: { key: "gear", op: "eq", value: "P" }      # 环境条件不满足/读不到（unknown）→本次跳过该动作
                                                       # （三态求值见 §5.2③b；on_missing:include 预留不实现）
      assert: { key: "seat.front_left.angle", op: "eq", value: "160" }  # 期望态断言（Verify 用）
      on_fail: "defer_p"      # skip(缺省,汇总播报) | retry_suggest(建议卡重试) | defer_p(驻车补做建议)
      tags: ["comfort"]       # 预留（P4 裁剪策略）
    - type: "vehicle.control"
      command: "hvac.set"
      params: { temperature: "22", mode: "cool" }
      when: { key: "cabin_temp", op: "gte", value: 28 }   # 夏分支
    - type: "vehicle.control"
      command: "hvac.set"
      params: { temperature: "26", mode: "heat" }
      when: { key: "cabin_temp", op: "lt", value: 15 }    # 冬分支（两条互斥 when 表达分支）
  triggers: []                # P0 恒空；触发期见 §7
  created_at: 1783400000
  updated_at: 1783400000
  use_count: 3                # 激活次数，P4 习惯建议排序用
```

**`when`/`assert`/`guards` 的 key 词表白名单**（编译期与 command 同等强度校验，防 LLM 幻觉键导致条件永真/永假）：`battery` / `gear` / `speed_kmh` / `location.city` / `hour`（本地时）+ `vehicle_state` 镜像内的对象状态键（`seat.*`/`hvac.*`/`ambient_light.*`/`volume`/`window.*`…，落地时以 VAL state 键全集为准生成，随 commands.yaml 同一构建期 COPY 进镜像）。`op` 词表：`eq/ne/lt/lte/gt/gte/in`。不在白名单的 key → 编译期剔除该字段并回读告知（条件降级为无条件，动作保留）。

**兼容性**：v2 四字段全可选，`actions` 基础条目 schema 与现有 `scenes.yaml` 一致（`_build_action` 无需改），预置 4 场景零迁移。**P0 即用 v2 schema 落库**（PG JSONB 返工成本 > 字段留空成本），P0 编译器可先只产平铺动作（不产 when/assert），P2 教会编译器产策略字段——存储零迁移。

### 4.2 存储：PG 表 `scene_item`（照抄 reminder 模式）

```sql
CREATE TABLE IF NOT EXISTS scene_item (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  name        TEXT NOT NULL,
  aliases     JSONB NOT NULL DEFAULT '[]',
  description TEXT NOT NULL DEFAULT '',
  goal        TEXT NOT NULL DEFAULT '',          -- v2.1 修正①：DSL goal 对应列
  source      TEXT NOT NULL DEFAULT 'user',
  status      TEXT NOT NULL DEFAULT 'enabled',
  guards      JSONB NOT NULL DEFAULT '[]',       -- v2.1 修正①：DSL guards 对应列（与 actions/triggers 平级）
  actions     JSONB NOT NULL,
  triggers    JSONB NOT NULL DEFAULT '[]',
  created_at  BIGINT NOT NULL,
  updated_at  BIGINT NOT NULL,
  use_count   INT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_scene_user ON scene_item (user_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scene_user_name ON scene_item (user_id, name);
```

> **字段对齐纪律（v2.1 修正①）**：DSL 顶层键与 PG 列**一一同名**（id/user_id/name/aliases/description/goal/source/status/guards/actions/triggers/created_at/updated_at/use_count），store 的 to_row/from_row 是无脑映射，不做改名翻译——防两侧漂移。

- 无 PG → 内存降级 + 「重启丢失」警示日志（reminder store 先例）。
- **合并视图**：`_match_scene` 匹配顺序 = 用户场景（精确 key/name/alias → 模糊）→ 预置场景（同上）；用户同名遮蔽预置（D4）。0.1.0 的 `get_close_matches` 模糊匹配保留，cutoff 不放宽（宁 NEED_SLOT 不误激活）。

### 4.3 当前场景态：shared_state 键 `SCENE_ACTIVE`

```
SCENE_ACTIVE (scene_active): owner=scene-orchestrator（activate 写 / deactivate 清 / verify·触发读写）
value: { scene_id, scene_name, activated_at,
         activation_id: "<uuid4>",              # v2.1 修正③：激活代际，verify/deferred 写入前必校验
         snapshot: { "seat.front_left.angle": "90", ... },   # 按 solved 动作集受影响键采集（修正④）
         solved_actions: [ {command, params, assert?, on_fail?} ],   # 本次 Solve 结果 = 恢复基准 + Verify 对账清单
         deferred: [ {command, params, reason} ] }                   # 驻车补做队列（P2 记录 / P3 投递）
```

**竞态防护（v2.1 修正③，R4.3b epoch 代际护栏同款思想）**：`SCENE_ACTIVE` 是单槽，新激活/退出会覆盖/清除它，而 Verify 是 3~5s 后的异步任务——旧 verify 醒来直接读单槽会拿到新场景数据错账、或对已退出场景发假警。三层防护：① verify task **创建时经闭包携带** `activation_id + solved_actions`（对账清单不依赖回读）；② task 醒来第一步读 `SCENE_ACTIVE.activation_id` 比对，不一致（被新激活覆盖/被 deactivate 清除）→ 静默放弃；③ 进程内单飞：agent 持 `_verify_tasks[user_id]`，新 activate/deactivate 先 cancel 旧 task（cancel 与代际双保险，覆盖 cancel 竞窗）。`deferred` 写入同样先校验代际再写。shared_state 是远端 KV 无原子 read-modify-write，PoC 单实例 + 单飞下够用；多实例需换带版本号的存储，留档为已知边界。
- 落 conventions §9 表 + `shared_state.py` 常量（登记制）。
- `snapshot` 来源：激活时 `ctx.fetch("vehicle_state")` 取受影响键当前值；取不到的键记 `null`，deactivate 时退反向默认表（D5）。

## 5. 意图面与各 intent 设计

意图全集（conventions §2 需更新）：

| intent | 变化 | 槽位 | 说明 |
|---|---|---|---|
| `scene.create` | **新增** | name, spec | 一句话创建（§5.1）；require_confirm 语义（保存前回读） |
| `scene.activate` | 增强 | scene, custom_params | 合并匹配 + 参数覆盖 + scene_mode 状态位（§5.2） |
| `scene.deactivate` | 重写 | scene | 快照恢复（§5.4） |
| `scene.update` | **新增** | scene, modification | 改动作/参数/别名（§5.5） |
| `scene.delete` | **新增** | scene | 删用户场景，NEED_CONFIRM；预置场景只能 disable |
| `scene.list` | 增强 | — | 区分「我的场景 / 内置场景」，带 scene_list 卡 |

### 5.1 `scene.create`：编译闭环（本次核心增量）

```
「帮我创建一个钓鱼模式：座椅放平，开外循环，氛围灯调到10%」
  ①解析：LLM(primary 档) 编译 raw_text → 候选 Scene JSON
     - prompt 携带白名单摘要：commands.yaml 对象/操作/参数枚举的紧凑渲染（构建期 COPY，D3）
     - 输出约束：只准从白名单选 command；params 只准用该对象声明的键；无法映射的用户诉求
       放入 unsupported[] 数组，禁止编造
  ②校验（确定性，LLM 说了不算）：
     - 逐条 action：command 在白名单 && params 键合法 && 值在枚举/范围内（超范围夹紧）
     - require_confirm 按 §8.1 危险动作表强制改写
     - 名字冲突：与端侧模式词（驾驶/运动/省电…）或既有场景同名 → 追问换名/确认覆盖
     - 全部动作被剔 → FAILED 诚实告知（不存空场景）
  ③回读确认（NEED_CONFIRM）：
     「将创建钓鱼模式，共 3 个动作：座椅放平（执行时需确认）、空调外循环、氛围灯亮度10%。
       『放舒缓音乐』我还做不到，已跳过。保存吗？」 + scene_card 卡片列出动作清单
  ④确认 → 落 PG，follow_up 教育：「以后说『开启钓鱼模式』就行」
```

- 缺 spec（只说「创建一个钓鱼模式」）→ NEED_SLOT：「钓鱼模式里要做哪些事？」，追问轮把 name 存 pending（reminder `REMINDER_PENDING` 同款两轮续接，键 `SCENE_PENDING` 一并登记）。
- LLM 两次解析失败 → FAILED 诚实降级，不猜。
- 编译是低频重操作，走 primary 模型不走 @fast；heavy 不标（单次 LLM 调用 + 确定性校验，无多轮检索，不到 is_complex 门槛）。

### 5.2 `scene.activate` 增强：Ground·Solve 确定性具象化（D9）

激活流水线从「查表→平铺下发」升级为四段（全程零 LLM，每段确定性）：

```
① Match   合并匹配（§4.2 顺序），激活成功 use_count += 1
② Ground  读环境：ctx.fetch("vehicle_state") + meta 透传（电量/位置）+ 本地时 hour
          → 环境快照 env{}；取不到的键记 missing（后续按 fail-open 求值）
③ Solve   对 Policy 确定性求值，产出本次动作序列（**条件三态：满足 / 不满足 / unknown**，v2.1 修正②）：
          a. guards 逐条：满足→过；确凿不满足→按 mode（block=REJECTED 诚实拒绝 /
             confirm=并入 NEED_CONFIRM 话术）；**key unknown→一律降级 confirm 提示**
             （「电量数据读不到，仍要开启吗？」）——block 拦截只在确凿证据下发生
          b. 逐动作 when 求值：满足→保留；不满足→跳过（回读话术标注「本次跳过：座椅调整
             （行车中）」）；**unknown→跳过并话术告知**（「车内温度读不到，空调分支已跳过」）。
             fail-closed 的理由：若按满足处理，互斥分支对（夏 cool/冬 heat 两条相反 when）在
             缺数据时会**同时生效、后条覆盖前条**——实 bug；跳过+告知让"消失"透明化
          c. 幂等跳过：assert 已达成的动作剔除（重复激活/触发撞车/「重试」都天然只补缺；
             assert 键读不到→不剔，照常执行）
          d. custom_params 覆盖：raw_text 数值确定性解析（温度→hvac.temperature、亮度→
             ambient_light.brightness、角度→seat.angle），只覆盖已有同对象动作不新增；
             解析不出就忽略（不 LLM 兜底）
          e. 序不重排（编译期人类直觉序），尾部追加 scene_mode.set {mode:<key>}（val.py:623）
          f. 求值后动作集为空 → OK +「都已就绪，无需调整」（幂等激活的诚实反馈）
④ Execute 生成 activation_id（uuid4，v2.1 修正③）；**按 solved 动作集**（非场景全量，v2.1 修正④）
          快照受影响键 + solved_actions 一并写 SCENE_ACTIVE → actions 下发（NEED_CONFIRM 分支
          行为不变：危险动作先确认再整包执行）→ 注册 Verify 后台任务（§5.3，闭包携带
          activation_id + solved_actions 对账清单，不依赖回读）
```

执行链路完全复用现状：AgentResult.actions → 聚合 → 端侧 `_dispatch_cloud_actions`（仅 vehicle.control 交 VAL，navigate 归 HMI）→ VAL 安全门控（行车禁座椅放平、低电量门控等既有规则自动生效）。Solve 的 `when` 裁剪是**体验优化不是安全机制**——安全兜底永远在 VAL（§8 分层不变）。

### 5.3 Verify-Repair：执行后验证与修复闭环（D10，v2 增量）

**动机（现状真缺陷）**：`_dispatch_cloud_actions`（server.py:696-701）循环内 `new_speech` 被逐条覆盖——场景 5 个动作、第 2 条被 VAL 安全门控拒绝、第 3~5 条成功，最终 speech 是第 5 条的成功话术，**失败对用户完全静默**。0.1.0 的场景越丰富这个缺陷越严重。

**时序约束**：handle 返回时动作尚未执行（回流端侧是 handle 之后的编排/下发阶段），验证必须后台异步：

```
activate ④ 注册后台 task（deep_research 异步先例：不依赖请求级 ctx，自持 MemoryClient 重建；
           闭包携带 activation_id + solved_actions，v2.1 修正③）
  ├─ 同 user 旧 verify task 先 cancel（进程内单飞 _verify_tasks[user_id]）
  ├─ 等待窗口 3~5s（动作到端 + VAL 执行 + state diff 广播回来）
  ├─ 代际校验：读 SCENE_ACTIVE.activation_id ≠ 闭包携带值（被新激活覆盖 / 被 deactivate 清除）
  │   → 静默放弃（防错账/假警）
  ├─ 取实际态：优先消费 on_start 订阅的 vehicle.state.changed 增量镜像（§7 与触发 watcher
  │   共用同一条 NATS 订阅）；镜像无数据 → ctx.fetch("vehicle_state") 兜底一次
  ├─ 对账：闭包 solved_actions 中带 assert 的动作逐条比对（不回读单槽拿清单）
  │   ├─ 全达成 → 静默结束（不打扰；HMI 已有执行反馈）
  │   └─ 未达成项按 on_fail 分类：
  │       skip（缺省）      → 合并一条 proactive 诚实汇报：「午休模式已开启，但座椅调整
  │                          没有生效（可能是行车安全限制）」
  │       retry_suggest    → proactive 建议卡 + 按钮「再试一次」（send_text=「开启<场景>」；
  │                          幂等 solve 保证只补缺失项，重试语义干净）
  │       defer_p          → 写驻车补做 pending（SCENE_ACTIVE.deferred[]，写前再校验代际，
  │                          不匹配丢弃）；gear=P 事件到达时发建议卡「已停车，把座椅放平
  │                          补上吗？」（复用 D6 询问式，触发 watcher 消费；P2 前仅记录不投递）
  └─ 任何一步取不到数据/异常 → 静默放弃（fail-open 铁律：verify 是增强，绝不假警、
      绝不因旁路失败影响主链）
```

**边界（写死，防机制膨胀）**：
- Repair **不新增执行通道**：proactive 只有 speech+card，所有「重试/补做」经 send_text 回到正常语音链路（权限/确认/VAL 门控全量重走）——执行入口唯一性是安全架构的正确性而非限制（D10）。
- Verify 只对声明了 `assert` 的动作负责；`assert` 键不在 vehicle_state 镜像 → 该条跳过验证。
- NATS best-effort、状态镜像最终一致：等待窗口后仍无数据视为「无法验证」而非「失败」。
- 每次激活至多一条 verify 汇报（合并未达成项），不逐条轰炸；同场景 verify 汇报与触发建议共用节流窗。

### 5.4 `scene.deactivate` 重写

```
读 SCENE_ACTIVE →
  无 → 「当前没有开启场景模式」
  有 → 先 cancel 在飞 verify task（修正③单飞），再**对 solved_actions（本次实际下发集，
       v2.1 修正④）**逐条生成恢复动作——不按场景原始 actions：
        · when 跳过未执行的动作（如行车中的座椅）不在 solved 里 → 不恢复（座椅本来没动，
          按原始场景全量恢复会把用户中途手动调整覆盖掉）
        · deferred / verify 已确认未生效的动作 → 跳过恢复（没生效无从恢复）
        · snapshot 有该键 → 恢复到快照值
        · snapshot 缺键 → 反向默认表（seat→复位90 / volume→50 / ambient_light→off /
                          hvac→auto 24 / fragrance→off / 其余 → 跳过并在话术说明）
      + scene_mode.set {mode: "off"}（entities.yaml scene_modes 需含 off，落地时核对）
  恢复动作含危险类（座椅）→ NEED_CONFIRM「将退出露营模式并把座椅调回原位，确认吗？」
  执行后清 SCENE_ACTIVE
```

### 5.5 `scene.update` / `scene.delete`

- update：定位场景（名字/「刚才那个」经 SCENE_ACTIVE）→ 修改类型二分：**参数级**（「温度改成 24」）确定性解析直接改；**动作级**（「加一个开香氛」「去掉座椅放平」）走 §5.1 的编译+校验+回读小闭环（只对增量动作）。预置场景 update → 引导「复制为我的场景再改」（builtin 只读，用户改动落 user 同名遮蔽）。
- delete：仅 user 场景可删，NEED_CONFIRM；预置场景「删除」→ status=disabled（list 不再展示、activate 不再匹配）。

## 6. 路由与冲突（route_hints）

```yaml
route_hints:
  # 激活：「开启/打开/进入/切换到 X模式」或裸「X模式」
  - pattern: '(开启|打开|进入|切换到|来个|启动)\s*[一-龥]{1,6}模式|^[一-龥]{2,6}模式$'
    intent: scene.activate
    policy: replace
    priority: 54            # 低于 reminder(56)/sports 追问(58)；高于泛 chitchat
    guard: '驾驶模式|运动模式|舒适模式|经济模式|雪地模式|越野模式|性能模式|省电模式|电量模式|动能回收|飞行模式|勿扰模式|静音模式|专注模式|哨兵|是什么|什么意思|怎么(用|开|关)'
    slots: { scene: "$text" }
  # 创建：「创建/新建/帮我建/存一个 X模式」
  - pattern: '(创建|新建|自定义|帮我建|建一个|做一个|存成?|设一个)[^。]{0,4}模式'
    intent: scene.create
    policy: replace
    priority: 55
    guard: '是什么|什么意思'
    slots: { name: "$text", spec: "$text" }
  # 退出：「退出/关闭/取消 X模式」
  - pattern: '(退出|关闭|取消|结束)\s*[一-龥]{0,6}模式'
    intent: scene.deactivate
    policy: replace
    priority: 54
    guard: '驾驶模式|省电模式|勿扰|静音|飞行'
    slots: { scene: "$text" }
```

- **guard 词面是 D8 的落点**：驾驶/动力类模式词是端侧 LOCAL_INTENTS（`driving_mode.set`/`power_mode.set`），云端 hint 必须让路——但注意这些话根本到不了云端（端侧秒回），guard 防的是「置信度低走云」的边角与 eval 语料回归。
- pattern 为方向性草案，落地以 `test/eval_corpus/route_hints_cases.yaml` 加用例实测收敛（R3.4 流程），重点回归：「打开运动模式」（端侧）、「开启午休模式」（scene）、「省电模式怎么开」（manual/chitchat）、「创建一个下雨模式」（scene.create）。
- `scene.list` 不加 hint（「有哪些场景模式」语义路由 + examples 足够，误抢风险大于收益）。

## 7. 触发运行时（P3，v2 分期重排：策略引擎先行）

### 7.1 Trigger DSL

```yaml
triggers:
  - type: time              # 复用 reminder timeparse：fire_at + recur
    spec: { recur: "daily", at: "12:30" }
  - type: event             # 订阅 vehicle.state.changed（road-safety 样板）
    spec: { key: "battery", op: "lt", value: 20 }       # 电量阈值
  - type: event
    spec: { key: "gear", op: "eq", value: "P" }          # 挡位
  - type: event
    spec: { key: "location.city", op: "enter", value: "深圳" }  # 位置进入（city 级，PoC 精度）
```

事件词表先窄（battery/gear/location.city 三键），走通再扩；条件组合（AND）P4 展望。

### 7.2 运行时（两个 watcher，进程内自治，D7）

- **时间**：poll（默认 30s，场景触发不需要 reminder 的 5s 精度）→ 枚举 enabled 场景的到期 time trigger → 原子领取 → 发建议卡 → recur 滚动（照抄 scheduler.py）。
- **事件**：on_start 订阅 `vehicle.state.changed`，changes 与全体 enabled event trigger 匹配（**边沿触发**：仅在「从不满足→满足」变沿发一次，防 battery=19 持续风暴）；同场景节流 30 分钟（road-safety `_should_broadcast` 同款）。
- **一条订阅三个消费方（实施要点）**：`vehicle.state.changed` 的 NATS 订阅在 on_start 只建一条，同时喂 ① 状态增量镜像（Verify 对账数据源，§5.3，P2 就要）② 事件触发匹配（本节，P3）③ 驻车补做投递（`SCENE_ACTIVE.deferred[]` 非空且 gear→P 变沿 → 补做建议卡，P3）。P2 落地时订阅层就按多消费方写好，P3 只挂新回调。
- **触发产物 = 建议卡（D6）**：`agent.proactive {type:"scene_suggest", speech:"电量低于20%了，要开启省电出行模式吗？", card:{type:"scene_card", context:"suggest", actions:[{label:"开启", send_text:"开启省电出行模式"},{label:"不用", send_text:""}]}}`——用户点「开启」回发原话，走正常语音链路激活（**幂等 solve 顺带消化触发与手动撞车：后到的激活只补差异**）。**触发路径零执行权**。

## 8. 安全模型（分层，无新豁免）

| 层 | 机制 | 归属 |
|---|---|---|
| 创建期 | LLM 输出白名单校验（command/params/**when·assert·guards 的 key 同表校验**）+ require_confirm 强制标注（§8.1 表）+ `on_fail`/`mode` 枚举校验 + 回读确认落库 | scene agent（本设计） |
| 激活期 | Solve 确定性求值（零 LLM，D9）；危险动作 NEED_CONFIRM（既有）；权限 `requires_permissions: [vehicle.control, navigation.control, profile.read, profile.write]` 经编排 granted_scopes 校验（既有）。**Solve 裁剪是体验优化不是安全机制** | 编排/权限引擎 + scene agent |
| 执行期 | 端侧 VAL 归一→校验→**安全门控**（行车禁座椅放平/低电量门控/范围夹紧）——唯一执行路径不变 | VAL |
| 修复期（P2） | Verify 只读不写车（对账+播报）；repair 一律建议卡 send_text 回正常语音链路重走全量校验，**不新增执行通道**（D10）；fail-open 绝不假警 | scene agent 后台 task |
| 触发期（P3） | 询问式：只产建议卡，无执行权（D6）；边沿触发+节流防风暴 | scene agent watcher |
| 注入面 | 场景名/描述是用户内容，进 LLM prompt（create/update 编译）时按注入防护惯例隔离；**激活/执行/修复路径不进 LLM**，无注入面 | scene agent |

### 8.1 危险动作强制确认表（编译期改写，LLM 无权决定）

`require_confirm=true` 强制：`seat.*`（位移）、`trunk.*`/`frunk.*`、`door_lock.*`、`window.*`（整窗开）、`charging_port.*`、`fuel_tank_cover.*`；其余默认 false。表内对象来自 commands.yaml，落地时与 VAL 安全门控清单核对一遍取并集。

## 9. HMI 卡片

- **`scene_card`**（新增，Struct 免改 proto）：`{type:"scene_card", context:"created|confirm|activated|suggest", name, description, actions_preview:[{label, danger}], buttons:[{label, send_text}]}`——create 回读/激活反馈/触发建议三态复用一张卡。`display_priority: 1`（交互候选级）。
- **`scene_list`**（新增）：`{type:"scene_list", mine:[...], builtin:[...]}`，条目可点（send_text「开启X模式」）。
- 右舞台联动（可选增强，P2 之后）：`ContextualStage` 按 `vehicle_state.scene_mode` 换氛围（露营=暖橙/午休=暗光），HMI 已有 vehicle_state 镜像桥（2026-07-13 车况动态化），纯前端增量，不阻塞主线。

## 10. 验收与测试

- **单测**（`agents/scene_orchestrator/tests/`）：编译校验（白名单剔除/参数夹紧/require_confirm 强制改写/全剔 FAILED/**when·assert 幻觉键剔除**）、匹配遮蔽（用户同名盖预置）、参数覆盖、deactivate（**恢复基准=solved_actions：when 跳过项不恢复、用户手动调整不被覆盖**·快照/默认表两路）、update 两类修改、delete 确认、store 降级；**Solve 求值器**（guards block·confirm 两模式 + **unknown 降级 confirm**/when 三态裁剪 + **unknown=exclude+告知**/**互斥分支对缺数据不双发**/幂等跳过/空动作集诚实反馈/求值纯函数注入 env 全离线可测）；**Verify 对账**（全达成静默/on_fail 三路处置/无数据静默放弃/汇报合并/**代际不匹配静默放弃/新激活 cancel 旧 task**）；P3 watcher 边沿/节流/建议卡 payload。预估 +80~100 例。
- **route_hints eval**：`route_hints_cases.yaml` 加正反例（§6 四组），跑 `eval_route_hints.py` 不回归存量。
- **真栈 e2e**（`test/e2e_scene.py` 新增）：①「创建钓鱼模式：氛围灯调暗+空调外循环」→ 回读确认 → 确认 → 「开启钓鱼模式」→ 车辆状态镜像氛围灯/空调变化 + scene_mode=钓鱼；②「退出」→ 状态恢复快照值；③「开启午休模式温度26」→ hvac 26 非 24；④ P2 策略：`POST /api/debug/vehicle` 压行车态（speed>0）→ 激活含座椅动作场景 → 座椅动作被 when/VAL 拦 → 收到 verify 诚实汇报 proactive；压驻车再激活 → 幂等只补座椅项；⑤ P3 触发：压电量 <20 → 收到 scene_suggest proactive。session 前缀用 `e2e-`（§9.2 跳过记忆抽取）。
- **既有回归**：全量 pytest + `smoke_edge`（确认 driving_mode/power_mode 端侧路由零回归）。

## 11. 分期计划（v2 重排：策略引擎提前到 P2，触发挪 P3）

| 期 | 内容 | 交付判据 |
|---|---|---|
| **P0 用户造场景** | scene.create 编译闭环（D2/D3，**落库即 v2 schema、编译器先只产平铺动作**）+ PG store（D4）+ 合并匹配/遮蔽 + activate 补 scene_mode 状态位与快照 + deactivate 真恢复（D5）+ route_hints（D8/§6）+ scene_card/scene_list + conventions/shared_state 登记 | 单测 + eval 不回归 + 真栈 e2e ①② |
| **P1 管理与覆盖** | scene.update/delete + custom_params 参数覆盖 + media 动作放开（端侧分发扩一类，单独确认）+「把刚才这些存成X模式」会话沉淀（读 ctx.history() 回执话术编译，走同一校验闭环，D11 沉淀桥） | e2e ③ + 会话沉淀真栈 1 例 |
| **P2 场景策略引擎（v2 增量核心）** | 编译器产 `guards/when/assert/on_fail`（key 白名单校验）+ Solve 求值器（裁剪/幂等/空集反馈，§5.2）+ Verify-Repair 后台闭环（NATS 镜像订阅单消费方版 + 诚实汇报 + retry_suggest 建议卡 + deferred 仅记录，§5.3） | 真栈 e2e ④（行车拦截→诚实汇报→驻车幂等补做）+ Solve/Verify 单测 |
| **P3 询问式触发** | Trigger DSL + 时间/事件双 watcher + scene_suggest 建议卡（D6/D7/§7）+ **驻车补做投递**（deferred 消费，§7.2 第三消费方） | e2e ⑤ + 边沿/节流单测 |
| **P4 展望（不承诺）** | routine 习惯沉淀建议（memory routine → 「存成模式？」）、环境源扩天气（跨 Agent 调 info，确定性消费）、激活期环境显著偏离时 opt-in「要我调整一下吗」（用户确认后走 update 小编译，LLM 有显式触发+确认不破 D2 精神）、per-scene 免确认自动执行（限非车控）、任务码分享、HMI 编辑器、条件组合 AND | — |

---

## 附：与 0.1.0 的兼容清单

- `scenes.yaml` schema 不变，4 预置场景照跑；`_build_action`/`_action_desc` 保留。
- 既有 3 intent 名不变，existing 契约测试仅 deactivate 断言需更新（从嘴炮话术改为恢复语义）。
- manifest 增量：capabilities +3、route_hints、`context_scopes: [vehicle_state]`、requires_permissions 补 `profile.read/profile.write`（shared_state 走 profile KV；注意 reminder 落地时踩过 `_POC_DEFAULT_SCOPES` 缺授权坑，核对 `orchestrator/cloud/context.py`）。
- 端口 50069、agent_id、部署位不变；compose 增 PG 依赖 env（POSTGRES_DSN，reminder 同款）。
