# AR05：结构化契约与能力摘要实施方案

> 状态：草案，方案已输出并落盘；产品代码尚未实施，AR05 尚未签收。
> 日期：2026-09-09。
> 交付对象：后端、网关、共享客户端逻辑与 Android 的后续实施者；由同一负责人汇总集成和验收结果。
> 关联：[AR05 分批范围](2026-09-07-android-review-remediation-batches.md#ar05)、[完整评审 R10 / R14](../reviews/2026-09-07-android-ux-full-review.md)、[AR04 实施与设备基线](2026-09-08-ar04-presentation-ack-implementation.md)。

本批按“契约定义 → 服务端产出 → 网关保真 → Android 消费 → 固定版本验收”推进。交付结果是用户能明确知道正在确认什么、还缺什么、为何不能继续、如何恢复，以及当前账号实际能做什么。沿用 AR04 第十五节的浮动在场设计。

## 1. 接手状态与范围

### 1.1 基线与授权

| 项目 | 2026-09-09 规划阶段的记录 |
|---|---|
| 源码 checkout | `D:/Personal/AI/Claude Code/产品/car-agent` |
| 评审源码 SHA | `4278a52febfdecb5188e79c25a674a8c8fe21418`；落文档前工作树干净，HEAD 与本地 origin/main 相同；本轮未 fetch |
| 真栈目标 | `target=cloud`；已读取根 `dev-stack.local` 并运行 `python scripts/dev_stack.py target show` |
| Android 实现基线 | AR04 第十五节：代码 `1c6780744be22151b04ef606eca961bbad060c26`，浮动宿主、闲置光球静帧及滚动内容余量 |
| 生产记录 | QA 交接页与 AR04 第十四节记录 release `573ad46d939bf655f5a0f4ef16579e2a9b80b087`；本次规划未重新读取线上 status/verify，也未重新验包或读取手机状态 |
| 本轮实际授权 | 用户要求先输出方案后停下，随后要求方案落到文件；本轮只完成文档落盘和接手指针同步 |
| 尚未执行 | 产品修改、反例运行、产品测试、codegen、构建、装机、业务 E2E、commit、push、生产部署 |

收到后续开始实施的指令后，按第 8 节顺序推进。当前文档不是已实施记录；不能把本轮静态检查、旧 release 的测试或 AR04 的设备读数写成 AR05 的验证结果。push、生产部署、环境/密钥/CI/CD、数据库迁移及设备敏感操作继续遵守项目现行授权边界。

### 1.2 本批处理

- R10：确认策略、真实补槽、结构化拒绝与服务降级、鉴权失效及恢复出口。
- R14 身份部分：真实会话身份和能力摘要、布局角色说明、首页系统推荐。
- 必要依赖：先验证 T0 权限校验覆盖面、补齐 Registry 声明恢复；两者直接影响摘要和补槽是否真实。
- 纳入 AR04 遗留的窄修复：Planner 技术失败后的错误归类和恢复，避免非法计划重试失败后误走闲聊/搜索。

AR02/AR03 的剩余设备矩阵、AR06 lint 与 release 自动化治理、AR07 KWS、AR08 时延、AR09 性能、AR10 全体验矩阵、AR11 正式账号/签名/推送仍归各自批次。AR05 验证可以复用已有回归锁，但不替这些批次整批签收。

### 1.3 必须先读的入口

1. [根 AGENTS.md](../../AGENTS.md)、[工程约定](../../CLAUDE.md)、[mobile README](../../mobile/README.md)。
2. [架构](../architecture/cockpit-agent-architecture.md) §2.4、§5.2.13；[多端与挂起契约](../conventions.md) §9.19、§9.20、§9.33、§9.35、§9.40。
3. AR04 实施记录第十四、十五节；[AR01 确认与取消](2026-09-07-ar01-confirmation-cancellation-implementation.md) 的台账、队列与准备阶段撤回约定。
4. [Android 构建与设备操作指南](../guides/android-build-and-device-validation.md)、[测试指南](../../test/README.md)。

## 2. 已核实的代码事实与待复现问题

以下定位基于评审源码 SHA，行号仅供定位，后续以函数和字段为准。没有运行时证据的条目必须先复现，不据此宣称生产故障已发生。

| 编号 | 现状与证据 | 对方案的影响 |
|---|---|---|
| F01 | [presence.ts](../../mobile/src/core/presence/presence.ts) `derivePresence` 将待确认项统一设为 `risk='high'`，到期取 `op.ts + PENDING_TTL_MS`；[pendingOps.mjs](../../hmi/src/pendingOps.mjs) 只有 ID/客户端时间的基础台账 | 改为服务端确认策略和真实截止时刻的投影，保持旧字段兼容 |
| F02 | [Agent proto](../../proto/cockpit/agent/v1/agent.proto) `ExecuteResponse.missing_slots` 已存在；[engine.py](../../orchestrator/cloud/engine.py) `_suspend` 保存缺槽；[orchestrator proto](../../proto/cockpit/orchestrator/v1/orchestrator.proto) `FinalResult` 和 [eventToMap](../../gateway/edge/main.go) 缺该下行面 | 不能只接 Android 类型，必须贯通生产、挂起、gRPC、JSON 和消费 |
| F03 | [engine.py](../../orchestrator/cloud/engine.py) `wait_slot` 换题时保留 `held_pending`；`test_engine_confirm.py::test_slot_interjection_keeps_pending` 已约束此行为 | 换题撤销当前补槽提示，不等于取消服务端任务；需要表达 active/held |
| F04 | [usePresence.ts](../../mobile/src/features/chat/usePresence.ts) 主要接 mic 权限、HF 服务降级、回声与发送未知；[FocusDock.tsx](../../mobile/src/features/chat/FocusDock.tsx) 缺完整恢复接线 | 降级种类有类型或画廊样本不等于实际用户流程已闭合 |
| F05 | [tts.ts](../../mobile/src/core/voice/tts.ts) 有 `onSilent`；[speech.ts](../../mobile/src/core/voice/speech.ts) `openSession` 没有接入该 hook | 主播报无声需提供用户提示；取消、主动静音不能被当成服务失败 |
| F06 | [AssistantProvider.tsx](../../mobile/src/features/assistant/AssistantProvider.tsx) 将 `cfg.token.slice(-4)` 作为用户显示；[SettingsScreen.tsx](../../mobile/src/features/settings/SettingsScreen.tsx) 用布局角色推导“不控车”；[共享默认示例](../../hmi/src/types.ts) 前两条为车控 | 身份、布局、授权和示例分开建模 |
| F07 | [gateway/auth.go](../../gateway/edge/auth.go) 按 token 注入 scope；[dispatch.py](../../orchestrator/cloud/dispatch.py) 调 `check_permission`；[edge/server.py](../../orchestrator/edge/server.py) `_execute_val_observed` 和本地快路径未见对应 scope 检查 | 静态发现，先离线构造无车控 scope 的真实 T0 路径反例，再确定修复；不能先承诺摘要中的“不可控车”已被全部出口执行 |
| F08 | [registry/store.py](../../registry/store.py) `_dict_to_manifest` 手写重建 capability，未还原 `slot_shapes`；现有往返测试未覆盖该字段 | 静态发现，先补复现并修声明往返，避免 Registry 重启后补槽判据丢失；同一适配器其他已声明字段一并对账 |
| F09 | AR04 第十三节记录 trace `21798d30258aa5bf`：非法工具 steps → 重试空计划 → `toolcall_degraded` → `chitchat.talk` → `info.search`；[planning.py](../../orchestrator/cloud/planning.py) 仍保留该技术兜底路径 | 本批为技术失败建立诚实终态和恢复入口；不把所有合法空计划归为错误 |
| F10 | [ws.mjs](../../hmi/src/ws.mjs) 默认对断连退避重连；[connectionTest.ts](../../mobile/src/core/api/connectionTest.ts) 只以 WS onopen/close 判定；网关鉴权拒绝发生在 Upgrade 前 | 必须有可区分鉴权拒绝与网络错误的只读查询；不能把所有 WS 握手异常判为 token 错误 |

## 3. 契约总表与共同不变量

本节是拟实现合同，不表示当前接口已存在。字段名、proto message 和版本号在步骤 1 中一次性冻结，再由同一组契约用例驱动各层；不得各端自行起名。

| 合同 | 最小语义 | 权威来源 |
|---|---|---|
| `confirm_policy` | risk、allowed_channels、动作/对象摘要、reason_code、expires_at；归属 operationId | 受控 capability、VAL、已保存的挂起记录 |
| `slot_request` | operationId、缺槽名/中文名、输入要求、可选建议值、active/held、截止时间 | Agent 结果、受控槽位声明、服务端挂起上下文 |
| `issues` | code、用户文案、影响能力、请求/操作归属、影响范围、可用恢复动作 | 权限/VAL/服务/传输的真实出口；客户端设备问题由实际设备结果产生 |
| `session_info` | 契约版本、认证状态、用户/关联车辆、授权来源、能力状态、生成时间/有效期 | 已认证会话和服务端运行时；不回传 token |

共同不变量：

1. `request_id` 决定回答归属，`operation_id` 决定挂起寻址，均不构成授权凭据。带 ID 对不上不能退到另一条任务。
2. `require_confirm`、`response_only`、服务端 `safety_origin_text` 的权威保持。LLM、用户 meta、补槽短句不能覆写策略或产生确认凭据。
3. `closed_operation_ids` 继续是服务端关闭事实；客户端到期只能停止交互并留痕，不能声称业务已完成或回滚。
4. 一轮可能既有成功动作又有失败问题，`issues` 必须保留步骤/操作归属，不能将整轮已执行部分改写为“未执行”。
5. 结构化字段优先表达事实；自然语言只供显示，不能用正则猜“这是拒绝/补槽/鉴权失败”。
6. JSON null、缺字段、未知枚举、空数组分别定义语义；不把 null 转成 `"None"`，不将缺省数值当可信权限或截止时刻。
7. schema 与解析判据在共享模块维护一份；Android 的页面布局与短期 UI 元数据仍留本端。避免再建一台独立会话状态机。

## 4. 确认与补槽闭环

### 4.1 确认策略

- 端侧风险复用 [capability_meta.py::risk_of](../../orchestrator/edge/capability_meta.py) 与 VAL 受控知识；云侧确认要求从 capability 声明和真实挂起原因合成，不新增第二份危险对象词表。
- 摘要取实际挂起步骤及已验证对象/槽值；缺结构化摘要时明确回退用户原话，不能把模型 goal 当执行事实或授权依据。
- 以 SessionStore 保存后的 `expires_at` 为权威。冻结时间单位与服务端时间参照，覆盖钟差、网络延迟、应用恢复和重复帧；客户端计时和恢复不能为同一操作续期。旧协议继续兼容既有 TTL。
- 渠道集合必须覆盖已有触控、文字和语音入口。渠道只说明回复如何输入，不代表设备可信或账号有权限；服务端恢复执行仍校验真实授权、挂起阶段和策略。
- `voice_forbidden` 等既有禁令不得因用户在 App 点了按钮而放宽。拟支持更严渠道规则时必须同时具备生产权威和执行校验，不能先在 UI 杜撰“只允许触控”。
- 指定确认在“准备中/排队/已发送/收到结果”各阶段仍能正确撤回、恢复或关闭。复用 AR01 的请求生命周期，不能仅从屏幕移走按钮就宣称操作已取消。

### 4.2 补槽与建议值

- 保留 Agent 已有 `missing_slots`，增加满足显示和回复的结构化说明。中文名与静态输入约束由能力声明提供；动态候选只能来自本次真实 Agent 结果/候选集。
- 建议值为空时提供文字/语音输入，不制造可点击的假选项，也不把整句 `follow_up` 无条件当成有效答案。
- 建立独立的补槽回复入口：指定 operationId、被回答的槽及输入，经正常请求链提交。服务端验证 owner、阶段、有效期和缺槽集合，只更新本次回答的槽；不能把一个值盲填到所有缺槽。
- 显式补槽回复不得被旧候选选择、本地定位征询或普通发送路由截走；普通自由文本仍沿用服务端现有话题/槽形状判定。
- `wait_slot` 恢复必须保持 `inject_confirmed=False`。补齐信息后若需二次确认，应产生新的明确确认项，不能借补槽直接执行。

### 4.3 active、held 与关闭

服务端已经允许插话后保留任务，本批以可恢复的显示合同承接：

| 事件 | 服务端事实 | 客户端结果 |
|---|---|---|
| 正在追问 | active wait_slot | 显示当前补槽 Dock/chips |
| 用户换题，原任务仍有效 | held wait_slot | 撤下当前追问/chips；待处理列表可继续选择原任务，不持续抢占当前问题 |
| 再次选择原任务 | 按原 operationId 验证并恢复 | 回到对应槽，不续期、不串候选 |
| 同一任务进入下一次追问 | 服务端关闭旧 operationId、下发新记录 | 原子更新旧/新项，旧 chips 失效 |
| 取消、淘汰、到期、完成 | 关闭或已失效 | 撤回可操作入口，留下必要解释；已排队的回复不得复活 |

active/held 必须由服务端状态变化提供明确更新或快照，不能靠客户端猜测话题。重连后的有效任务对账是本合同的一部分；快照只包含当前认证 owner 与当前会话，不能枚举他人任务。

## 5. 拒绝、降级与恢复

### 5.1 真实信号与用户出口

| 实际触发 | 显示范围/停留 | 恢复出口 |
|---|---|---|
| VAL 安全拒绝 | 对应操作的原因，用户可收起 | 条件改变后由用户重新发起；收起不表示执行 |
| 业务 scope 不足 | 当前账号与对应能力 | 查看能力/连接配置；不能引导到 Android 麦克风等系统权限 |
| mic/camera/location 权限拒绝 | 按具体权限保存，返回前台后重新查询 | 系统设置；设备权限拒绝与用户主动关闭功能分开 |
| ASR 批处理或 S2S 三段式回退 | 本轮及实际回退方式 | 语音设置；恢复或本轮结束后撤销提示 |
| TTS 未出声/中途失败 | 对应回答保留文字，说明播报状态 | 语音设置或用户主动重播；重播只处理音频，不重执行业务请求 |
| 回声防线关闭插话 | 当前会话，直到重新开启 | 沿用 AR03 的只停播与重新开启入口 |
| 请求错误/超时 | 对应气泡和短提示 | 只有明确可重试时提供重试；已执行部分或结果未知不自动整轮重发 |
| 发送状态未知 | 保留原请求身份，直到对账或超时 | 不自动重复发送，明确当前未确认结果 |
| token 被拒/配置损坏 | 应用级持续提示，配置入口始终可达 | 重新配置；成功前停止自动重连及旧请求自动补发 |

恢复动作采用受控 action kind，客户端只实现已支持的固定动作，不执行服务端提供的任意 URL、脚本或命令。每条 issue 绑定请求/操作或配置代际，旧回调不能污染新会话。

TTS 接入 `onSilent` 时必须区分用户静音、取消、前后台撤回与真正合成失败；已有音频中断不自动重播整段。保留播放事实 `playing/live` 及 AR03 停播顺序，不重写播放器。

### 5.2 Planner 技术失败的窄修复

以 F09 的模型输出序列建立确定性回归：非法计划且重试没有有效计划时，输出明确的技术失败和恢复入口，不再将该请求伪装成成功闲聊后转搜索。只改变该类失败分支。

必须保留并分别验证：合法不执行（例如完整否定句）、`addressed=false` 拒识、正常澄清、重试得到有效计划，以及既有有效 salvage 计划。`plan_mode` 与既有统计口径不借此次修复混写；必要的新原因字段单列。

审计 Edge `cloud_had_output`：带结构化终态但无 speech/actions 的失败也应被识别为已结算，不能落入“云端零输出 → 本地再执行”的兜底。混合意图已有的成功动作和 `closed_operation_ids` 必须保留。

## 6. 会话身份、能力摘要与连接恢复

### 6.1 只读查询面

建议新增 `GET /api/session`。由 edge-gateway 复用 `auth.resolveSession` 认证；token 通过认证请求传递，响应和日志不含凭证。内部使用显式、零业务副作用的查询契约沿既有端云通道读取会话/能力事实，不借普通聊天请求绕过、不调用 LLM、不追加聊天历史。

查询至少返回：协议支持版本、认证状态、服务端 user_id、关联 vehicle_id、授权来源、能力状态及快照时间/有效期。内部查询的请求/响应需要在 orchestrator/channel proto 及两侧代理一次性定义和贯通；网关不重新实现 Python 权限判据。

能力以完整注册目录和实际执行权限计算，不能用 Planner 当前轮语义 top-k 或预算裁剪后的 catalog 冒充全部能力。区分授权、服务可用性和用户关闭状态；Registry 注册并不证明对应车辆通道已在线，依赖不可核实时返回 unknown/unavailable，不宣称“可用”。查询依赖失败也不应把已成功认证误报为 token 失效。

### 6.2 授权事实与布局

- 复用 `security.permission.check_permission` 及同一份 scope 解析，先闭合 T0/云端差异，再用于摘要；匿名/PoC 默认放行与明确 token 授权分别标明来源。
- 不在本批修改 `PERMISSIONS_FAIL_OPEN` 或 token 配置；空 scope、未知权限、PoC 默认权限必须按既有规则如实建模，不能简化成“空数组=全部允许/全部拒绝”。
- `deviceRole` 保留持久化键值兼容，只影响布局；界面使用手持/支架/车载平板布局等说明，不因用户选择 `trusted-tablet` 宣称设备已可信绑定。
- 用户标识取服务端事实；没有姓名就显示标识，没有设备绑定证明就不补造。token 关联车辆应表述为服务端关联车辆，不等同于车辆所有权或硬件绑定完成。
- 系统示例绑定已验证能力，按能力状态和用户关闭项筛选。用户自定义短语保留为用户输入，不删除旧设置，也不为未验证短语标“可用”；不在手机端用语句关键词另写权限分类器。

### 6.3 连接与失效

- 区分网络/TLS/超时、明确认证拒绝、摘要暂不可取和旧服务端不支持查询。不能仅凭 RN WS 异常文本或 close code 1006 判定 token 无效。
- 明确认证拒绝后停止退避重连与旧队列补发，保留用户可理解的记录；重新配置成功再建立会话。已有静态 token 系统没有正式刷新机制，不在 UI 假造“自动续期”。
- 配置变更复用 `ensureWired`/`disposeWired` 的销毁边界，使旧摘要、旧挂起回复、准备中的位置/视觉、在途音频和迟到 issue 全部失效。不能把旧请求自动重放到另一账号或服务器。
- 能力摘要过期或断线后标明未知/待刷新，不能用缓存授权执行。重连须先处理身份、协议和有效挂起对账，再放行依赖这些事实的待发回复；不改变普通请求的 AR01 撤回语义。

## 7. 兼容与修改位置

### 7.1 版本兼容

| 组合 | 行为 |
|---|---|
| 新服务端 + 旧客户端 | 保留 `speech/need_confirm/operation_id/closed_operation_ids`；新增字段不能削弱服务端执行闸；旧客户端可能不具备全部恢复 UI |
| 新客户端 + 旧服务端 | 查询 404/明确不支持时进入已定义的 legacy 模式，沿用既有确认流程；不推断结构化策略或能力证明 |
| 新协议已声明支持但策略缺失/畸形 | 停止相应确认/补槽操作并提示恢复，不能默认为允许；取消与重新配置入口仍可达 |
| 查询超时或网络断开 | 标记未知/重连，不冒称旧版本或鉴权拒绝 |
| 升级前保存的挂起/Registry 数据 | 缺字段有兼容默认值；安全信息无法可信恢复时诚实关闭/拒绝，不从模型 goal 或用户 meta 补权限 |

proto 只做可兼容增量，先改 `proto/` 再生成 `gen/`，不手改或 force-add 生成物。新增字段若涉及 manifest，必须从 SDK loader、Registry 持久化、Step 装配到挂起恢复逐层对账。旧接口字段与新的规范字段必须有唯一转换入口，避免两套长期分叉。

### 7.2 预计修改面

| 责任层 | 路径/符号 | 具体任务 |
|---|---|---|
| 契约与 SDK | `proto/cockpit/{agent,orchestrator,channel}/v1/`；`agents/_sdk/{result,server,manifest}.py` | 定义回复/问题/会话查询合同，透传真实 Agent 结果，保持同步/流式同构 |
| 声明与权限 | `registry/store.py`；`security/permission.py`；`orchestrator/edge/capability_meta.py` | 声明往返、复用权限与风险判据；新通用解析按依赖闭包落共享位置 |
| 云端 | `orchestrator/cloud/{models,engine,session,server,executor,dispatch,planning,clients}.py` | 策略合成、挂起状态/查询、补槽恢复、问题汇总、窄技术失败出口 |
| 端侧与网关 | `orchestrator/edge/{server,edge_call,cloud_client,val}.py`；`gateway/{edge,cloud}/` | T0 权限、VAL 结构化原因、查询通道、全部终态/流式映射；保留 VAL 原执行规则 |
| 共享客户端 | `hmi/src/{types.ts,pendingOps.mjs,ws.mjs}` 及必要的纯解析模块；`mobile/shared-allowlist.json` | 版本/字段解析、挂起有效性、连接控制；保持 HMI 兼容消费与双端回归 |
| Android 状态与引擎 | `mobile/src/core/{api,session,presence,voice,location,vision}/` | 单一状态源、指定补槽、问题生命周期、真实设备/音频故障上报 |
| Android 视图 | `AssistantProvider/AssistantSurface`、`FocusDock/usePresence/ChatScreen`、`SettingsScreen`、`onboarding` | 接真实状态与恢复出口；首页推荐/身份文案；支持页复用浮动宿主 |

此表是责任地图，不要求无差别修改全部文件。新增 Agent 不得通过修改编排核心路由分支接入；本批查询是基础服务合同，不能伪装为领域 Agent 能力。

## 8. 执行顺序与完成判据

下表全部是待办。步骤 1 的反例与字段冻结完成前，不先批量铺 UI；每步通过必要检查后继续推进，不重复扩测已经闭合的同一问题。

| 步骤 | 依赖 | 交付内容 | 完成判据 |
|---|---|---|---|
| 0 接手复核 | 后续实施指令 | 核规则、SHA、工作树、target；读取服务 README；明确本批占用资源 | 不使用旧设备状态/旧测试数；他人改动保持原样 |
| 1 前置反例与合同冻结 | 0 | 复现 F07/F08；明确四份合同、渠道集合、时间单位、active/held、兼容与恢复动作 | 反例可重现；字段权威/缺省/过期/恢复/消费者齐全，记录最终 proto/JSON 字段表 |
| 2 生产端与恢复链 | 1 | 权限一致性、声明往返、确认/补槽、拒绝/降级、会话查询、Planner 窄修复 | 实际生产函数能产出每个状态；恢复保持 owner/operation/安全原文；部分成功不丢失 |
| 3 网关与共享逻辑 | 2 | 端云查询传输、final/issue 映射、版本适配、共享台账与重连控制 | gRPC → WS/HTTP → 共享解析保真；旧版本组合、重启恢复和迟到帧有反例 |
| 4 Android 消费 | 3 | Dock/chips/恢复出口、真实身份与推荐、ASR/TTS/权限事实接线 | 使用现有 SessionCore/Presence；闲置无空栏，任意指定待办可达，停止不新增采集 |
| 5 本地验证与冻结 | 4 | 第 9 节本地回归、自审、文档回填，冻结正式验包源码 | 检查有终态/退出码；测试属于同一树；拟发布差异和剩余项清楚 |
| 6 固定包与真栈验收 | 5、对应设备/发布授权 | 串行构建 prod release、OPPO 取证；后端发布后验证真实产出与恢复 | APK/安装哈希、客户端/服务端 SHA、逐条 trace、业务账与恢复均可追溯；缺格不整批签收 |

## 9. 验证与签收

### 9.1 必测矩阵

| ID | 场景 | 必须检查的事实 |
|---|---|---|
| V01 | 三条服务端挂起与本地位置征询并存，直接处理末项 | 指定 operationId、不串账、不调用无关定位授权；剩余条目保持 |
| V02 | 过期、重复点击、断线排队、取消后重连、配置切换 | 真实截止时间不续期；无效回复不补发；新身份不继承旧授权/操作 |
| V03 | 真实 Agent NEED_SLOT，含有建议/无建议、多槽及连续追问 | 槽名/值/候选归属正确；只补当前槽；补槽不会变成二次确认 |
| V04 | 补槽中换题、held 列表恢复、淘汰/取消/到期 | 当前 chips 撤销；仍有效任务可恢复；关闭旧 ID 后迟到回复无效 |
| V05 | 显式无车控权限、有权限、PoC 默认、伪造 meta/角色 | T0 各执行出口和云端与摘要一致；负例零动作/零 VAL 状态变化；正例防误伤 |
| V06 | SDK 同步/流式、Registry JSON 往返、挂起恢复、未知字段 | 字段保真，安全元数据不丢；旧记录兼容；不使用放宽断言或 skip 掩盖失败 |
| V07 | VAL/业务权限拒绝、设备权限拒绝、网络错误 | 原因类型正确、恢复入口正确；业务权限不足不指向系统权限页 |
| V08 | ASR/S2S 回退、TTS 无声/部分播放失败、静音/取消 | 真实问题可见；主动静音和取消不误报；恢复仅重播音频时不重复执行业务 |
| V09 | 非法计划+无有效重试、合法空动作/拒识/澄清、有效 salvage | 技术失败不误走闲聊/搜索；合法语义不回归；结构化空文本终态不触发本地再执行 |
| V10 | token 明确拒绝、查询失败、旧服务端、配置重建 | 鉴权拒绝后不无限重连；404 与网络异常分开；配置入口可达；旧队列不跨身份重放 |
| V11 | 部分成功+问题、旧轮关闭结果、operation 更新与新轮交错 | actions、closed_operation_ids、问题归属和可见状态不互相覆盖 |
| V12 | 对话/设置/车辆/地图，键盘、横屏、前后台、待办展开 | 一步停播和采集事实仍正确；新 Dock 不被层遮挡；处理结束不留空栏；提醒 ACK 不回归 |

F07/F08 首先由零业务网络的隔离反例验证。契约/扫描类回归必要时执行变异判红并按字节恢复。服务端状态必须由实际生产函数产生后走透传链验证；只向画廊塞一帧或只扫描源码不算闭环。

### 9.2 本地命令口径

当前仅文档落盘，不运行下列产品验证。实施时先检查生成物/依赖环境，使用实际修改面的定向测试，最终完成跨端必要回归：

```powershell
# 仓库根；任何真栈动作前先核 target
python scripts/dev_stack.py target show
git status --short --branch
git rev-parse HEAD
powershell -ExecutionPolicy Bypass -File scripts/check_android_env.ps1

# 仅在 proto 修改后
buf generate proto

# 后端与网关相关面；再补本批实际修改 Agent 的 tests
python -X utf8 -m pytest -q orchestrator/edge/tests orchestrator/cloud/tests security/tests registry/tests agents/_sdk/tests test/sdk
go test ./gateway/...

# Android 与共享模块两端
Push-Location mobile
npm run typecheck
node node_modules/jest/bin/jest.js --runInBand --silent
Pop-Location
Push-Location hmi
npm test
npm run build
Pop-Location
```

每个命令均须记录真实退出结果；前一条失败应先处理，不把最后一个命令的 exit 0 当整块成功。本批涉及共享执行与协议，最终按 [AGENTS.md](../../AGENTS.md) §6 完成四门禁、端侧 smoke 与固定 Python 全量口径；先冻结工作树，跑批期间不改代码。lint 按本批定向配置报告，不把 AR06 未建立的正式门禁写成已闭合。

```powershell
python test/smoke_edge.py
python test/eval_skills.py
python test/eval_exemplars.py
python scripts/check_intent_gate.py
python test/eval_capability_integrity.py

$env:TZ = 'UTC0'
Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
python -X utf8 -m pytest -q -n 8 --dist worksteal
```

### 9.3 固定包与真实业务证据

按 Android 操作指南串行使用共享构建镜像和测试手机。正式验包取 clean、已提交源码；固定 prod release，记录包内 build、签名、APK SHA-256、设备安装文件哈希与设置页构建身份。默认 OPPO 测试机，Xiaomi 仅按既有对照边界取证，不借另一设备结果。

真实契约验收需要对应后端版本。发布前完成可审查的差异、检查与 dry-run，再按用户授权 apply；提交部署命令不等于发布成功，独立 status/verify 后才取业务证据。长会话按完整 release SHA 运行；模型方差项至少 repeat 3。

真实确认、补槽与权限矩阵先使用隔离/受控测试对象；具名提醒、数据清理、商户操作和真实车控分别遵守授权边界，不以真实支付或商户写替代可控测试。检查 speech、actions、card、trace、closed/open operations 和最终业务账；多 operationId 实机组合需要服务端真实条目，不能用本地定位征询替代。

### 9.4 AR05 签收条件

- [ ] 四份合同已冻结并有全部生产者/传输/消费者及旧版本兼容证据。
- [ ] F07/F08 已复现、定性和处置；确认策略/补槽在 Registry 与挂起恢复后仍保真。
- [ ] V01–V12 逐格记录通过、失败或未验；每个声明状态有真实生产输入及失败后恢复流程。
- [ ] Android 保持 AR04 浮动宿主、AR03 一步停播、AR02 采集事实和 AR01 指定撤回边界。
- [ ] 客户端身份与推荐不伪装权限/可信绑定，摘要与实际授权执行一致。
- [ ] 本地代码验证、服务端 release、APK、设备证据分别绑定精确 SHA；未把历史测试借给当前版本。
- [ ] 完成后将最终合同更新到 conventions/架构及相应服务 README；本页保留实施记录，分批页回填真实状态。

## 10. 后续实施记录模板

实施时在本节追加每个已完成步骤的简短记录，不把全部日志抄进入口。原始测试/截图/探针文件按共享指南放仓库外。

| 字段 | 待填写 |
|---|---|
| 实施范围/步骤 | 本批实际完成范围与未完成项 |
| 源码 | checkout、分支、完整 SHA、工作树状态 |
| 前置发现 | F07/F08 的反例输入、观察、结论、修复位置 |
| 合同 | 最终字段名、版本、默认值、解析与兼容用例位置 |
| 本地验证 | 测试 SHA、命令、退出码、警告与失败归因 |
| 发布 | 服务端完整 SHA、dry-run/status/verify artifact；未部署则明确未部署 |
| APK/设备 | 构建参数、包内身份、哈希、设备角色/实际设置、安装回读 |
| 业务证据 | provider/model、trace、operationId、逐项 V01–V12、最终业务账 |
| 资源归还 | 本批进程/镜像/设备归属，临时设置恢复结果 |
| 下一步 | 剩余步骤、阻塞输入、精确文件入口 |

规划落盘状态：只新增本实施方案并更新接手链接；产品实现、部署和设备验证均未启动。后续从步骤 0 接续。

## 11. 冻结的字段表（步骤 1 产物，2026-09-09）

契约在此一次性冻结，各层按本表实现；字段名/默认值/缺省语义以本节为准，端各自起名一律视为缺陷。
proto 增量落 `proto/cockpit/orchestrator/v1/orchestrator.proto`（只加不改，旧字段语义不动）。

### 11.1 `ConfirmPolicy`（挂在 `FinalResult.confirm_policy`）

| 字段 | 类型 | 权威来源 | 缺省/缺字段语义 |
|---|---|---|---|
| `operation_id` | string | 服务端挂起记录 | 恒等于 `FinalResult.operation_id`；空=非挂起轮 |
| `risk` | string | `capability_meta.risk_of` + VAL 受控知识 | `low\|medium\|high`；空或未知枚举=**停止该确认操作并提示**，不默认允许 |
| `allowed_channels` | repeated string | 服务端渠道规则（含 `voice_forbidden` 等既有禁令） | `touch\|text\|voice`；空数组=只剩既有默认渠道，不放宽 |
| `action_summary` / `object_summary` | string | 已验证的挂起步骤对象/槽值 | 空=回退用户原话（由 `summary_source` 标明） |
| `reason_code` | string | 受控：`require_confirm\|safety_gate\|payment\|destructive` | 空=旧协议，按 `require_confirm` 处理 |
| `expires_at_ms` / `server_now_ms` | int64 | SessionStore 保存后的绝对时刻（epoch ms, UTC） | 0=未知 → 客户端回落既有 `PENDING_TTL_MS`，**不得自行续期** |
| `summary_source` | string | `capability\|user_utterance` | 空=`capability` |

### 11.2 `SlotRequest`（挂在 `FinalResult.slot_request`）

| 字段 | 类型 | 权威来源 | 缺省/缺字段语义 |
|---|---|---|---|
| `operation_id` | string | 服务端挂起记录 | 空=非补槽轮 |
| `slot` / `display_name` / `shape` | string | Agent `missing_slots` + 能力声明（`slot_shapes`） | `display_name` 空=显示 `slot`；`shape` 空=自由文本 |
| `suggestions` | repeated string | **本次真实 Agent 结果/候选集** | 空=只给文字/语音输入，不造假选项 |
| `state` | string | 服务端 `active\|held` | 空=`active`（旧协议） |
| `remaining_slots` | repeated string | 服务端挂起上下文 | 含本条；空=只缺本条 |
| `prompt` | string | 服务端追问话术 | 空=沿用 `follow_up` |
| `expires_at_ms` / `server_now_ms` | int64 | 同 ConfirmPolicy | 0=未知，不自行续期 |

### 11.3 `Issue`（`FinalResult.issues`，可多条）

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | string | 受控枚举：`permission.scope_missing` / `safety.val_rejected` / `service.degraded` / `transport.error` / `auth.rejected` / `planner.technical_failure` / `tts.silent` / `asr.fallback` / `device.permission_denied` |
| `message` | string | 用户可读文案。服务端问题由服务端产；设备问题由客户端按实际设备结果产 |
| `severity` | string | `info\|warning\|error`，空=`warning` |
| `scope` | string | `request\|operation\|session\|capability`，空=`request` |
| `request_id` / `operation_id` | string | 归属；一轮里既有成功动作又有失败问题时靠它分栏，**不得把已执行部分改写为未执行** |
| `affected_capabilities` | repeated string | 受影响能力 id |
| `recovery` | repeated `RecoveryAction` | `kind` 为受控值：`open_voice_settings` / `open_capability_settings` / `reconfigure_connection` / `retry_request` / `replay_audio` / `dismiss`；客户端只实现已支持的固定动作，**不执行服务端下发的任意 URL/脚本/命令** |

未知 `code` / 未知 `recovery.kind`：客户端显示 `message`、不提供该恢复入口，不猜语义。

### 11.4 `session_info`（`GET /api/session`，edge-gateway）

```json
{
  "contract_version": "ar05.1",
  "authenticated": true,
  "user_id": "u1",
  "vehicle_id": "v1",
  "authorization_source": "token | poc_default | fail_closed",
  "granted_scopes": ["vehicle.control"],
  "capabilities": [
    {"id": "edge-vehicle", "display_name": "车辆控制",
     "status": "available | unauthorized | unavailable | unknown",
     "reason_code": "scope_missing | offline | ..."}
  ],
  "generated_at_ms": 0,
  "expires_at_ms": 0
}
```

- 认证复用 `auth.resolveSession`；**响应与日志都不含 token**。
- `authorization_source` 直接来自 `security.session_scopes`：`token` 与 `poc_default` 不是一回事，摘要必须分开说。
- `status` 区分「没授权」「服务不在线」「取不到」——依赖不可核实时给 `unknown`/`unavailable`，不宣称可用。
- 404 / 明确不支持 = legacy 模式；超时/网络错误 = `unknown`，**不得判成 token 失效**。

### 11.5 共同不变量（实现期逐条对照）

1. `request_id` 决定回答归属，`operation_id` 决定挂起寻址，二者都不是授权凭据。
2. `require_confirm` / `response_only` / `safety_origin_text` 的权威不变，LLM 与补槽短句无权覆写。
3. `closed_operation_ids` 是服务端关闭事实；客户端到期只停交互并留痕，不宣称业务已完成或回滚。
4. 一轮可同时有成功动作与失败问题，`issues` 保留步骤/操作归属。
5. 结构化字段优先；自然语言只供显示，不得用正则猜「这是拒绝/补槽/鉴权失败」。
6. JSON null / 缺字段 / 未知枚举 / 空数组语义各自独立；null 不得字符串化为 `"None"`。
7. schema 与解析判据在共享模块**只留一份**；Android 只留页面布局与短期 UI 元数据。

## 12. 实施记录

### 12.1 步骤 0–1（2026-09-09）：接手复核、前置反例与合同冻结

| 字段 | 记录 |
|---|---|
| 实施范围 | 步骤 0 全部；步骤 1 的 F07/F08 复现与处置、四份合同字段冻结（§11） |
| 源码 | `D:/Personal/AI/Claude Code/产品/car-agent`，分支 `main`，起点 `4278a52febfdecb5188e79c25a674a8c8fe21418`（= 本地 origin/main）；工作树起始只含本方案落盘的文档改动 |
| target | `python scripts/dev_stack.py target show` → `cloud`；本轮零真栈动作、零部署、零设备 |

**F07（端侧 T0 无权限校验）——已复现、已修。**
反例（离线、零网络）：`meta.granted_scopes="location.read"` + 「打开车窗」→ 快路径 B 直接进 VAL，
`val.state["window"]=="open"`，并回一条 `vehicle.control` 动作。四个本地执行出口
（多意图 A、混合 A2、单意图 B、云端降级兜底）与云端回流分发全都不读该键。

处置：

- `security/session_scopes.py`（新增）：`granted_scopes` 解析 + `PERMISSIONS_FAIL_OPEN`
  兜底 + `POC_DEFAULT_SCOPES` 收敛为**全仓唯一一份**，返回 `(scopes, source)`；
  `orchestrator/cloud/context.py` 改为消费它，PoC 默认集不再有第二份副本。
- `orchestrator/edge/scope_gate.py`（新增）：需要哪个 scope 由
  `capabilities.build_edge_manifests()` 的 `edge_intents × requires_permissions` 回答，
  结构化命令回落 `edge_call.action_type_for`；判定仍走
  `security.permission.check_permission`。**新增端侧车控能力不用回来改这个文件。**
- `orchestrator/edge/server.py`：闸放在 `_execute_val_observed` 这个既有唯一收口
  （覆盖 A/A2/B/降级四条），快路径 B 的 legacy `edge_execute` 分支与
  `_dispatch_cloud_actions` 各补一处；被拒时零 VAL 调用、零状态变化、不下发动作、
  留 `permission_denied` 审计与 `val.execute status=err denied=scope` span。
- 话术只说业务授权不足，**不引导到系统权限页**（V07 前置）。
- `orchestrator/edge/Dockerfile` 增 `COPY security`（纯标准库，无新依赖）。

**F08（Registry 声明往返丢字段）——已复现、已修。**
反例：`slot_shapes` / `whole_utterance` / `RouteHint.scope` 在 `_manifest_to_dict` 存进了
JSON，`_dict_to_manifest` 根本没读 → registry 重启恢复后，wait_slot 槽值形状判据、
「同一份计划最多一步」的整句约束、接送 hint 的分句锚定同时静默失效。

处置：补齐三个字段的还原；`_manifest_to_dict` 的 dataclass 分支补齐
`slot_shapes/whole_utterance/kind/edge_intents/context_scopes/route_hints`。
**并把判据机制化**：`test_roundtrip_fixture_covers_every_declared_field` 走 proto
descriptor 断言夹具每个字段都是非默认值，`test_manifest_roundtrip_is_lossless_for_every_field`
断言 proto→dict→JSON→dict→proto 整条等价——这是该适配器第三次丢字段
（route_hints → verification → 本批），缺的不是断言而是判据。

**本地验证（同一工作树，代码 SHA 见提交）**

| 命令 | 结果 |
|---|---|
| `pytest -q orchestrator/edge/tests/test_edge_scope_gate.py` | 15 passed |
| 反向验证：禁用 `scope_gate.check_local_execution` 的 required 计算 | 8 条负例转红、7 条正例仍绿，文件按字节恢复（`RESTORE OK`） |
| `pytest -q registry/tests/test_store_roundtrip.py` | 9 passed |
| 反向验证：删 `_dict_to_manifest` 的 `whole_utterance` | 2 条逐字段对账转红，按字节恢复 |
| `pytest -q -n 8 orchestrator/edge/tests orchestrator/cloud/tests security/tests registry/tests` | 2244 passed, 1 skipped（先红 1 条：`test_voiceprint_not_auth` 的源码锚点随 granted 段搬家失效，已把红线跟到 `security/session_scopes.py` 与 `scope_gate.py`，并补两条新断言） |
| `smoke_edge` / `eval_skills` / `eval_exemplars` / `check_intent_gate` / `eval_capability_integrity` | 五项全 PASS（exit 0） |

**尚未执行**：proto 增量与 codegen、确认/补槽/issues/会话查询的生产端、网关与共享逻辑、
Android 消费、全量固定口径、构建装机、部署与真栈验收。AR05 未签收。

### 12.2 步骤 2–4（2026-09-09）：契约生产、端云贯通与 Android 消费

| 字段 | 记录 |
|---|---|
| 实施范围 | 步骤 2 全部（除下方"未做"列出的）、步骤 3 全部、步骤 4 全部；步骤 5 本地验证已跑，文档回填即本节 |
| 源码 | 分支 `main`，起点 `4278a52`；本节对应 `17bec2f` / `0556d42` / `0b278a5` / `eb34f7e` / `5421726` 及其后续提交 |
| target | `cloud`；本轮零真栈动作、零部署、零设备 |

**契约（proto 增量，只加不改）**：`FinalResult` 增 `confirm_policy` / `slot_request` /
`issues` / `held_operation_ids`；新增 `ConfirmPolicy`、`SlotRequest`、`Issue`、
`RecoveryAction`、`SessionInfoRequest/Response`、`CapabilityStatus`；
`EdgeOrchestrator` 与 `CloudPlanner` 各增 `DescribeSession`；channel 增
`session_info_request/response` 帧。冻结后按需补了三个字段，各自写明理由：
`ConfirmPolicy.target_intent`（端侧据此用 VAL 受控知识收窄渠道，危险知识不在云侧抄第二份）、
`SessionInfoResponse.summary_status/summary_reason`（**没有这一位就没法区分「云端此刻查不到」
和「你没有这些能力」**）、`FinalResult.held_operation_ids`（换题后仍有效的挂起，客户端撤哪一条
得有 id）。§11 字段表已同步。

**服务端**

- `orchestrator/cloud/contracts.py`：确认策略与补槽请求的唯一装配点。确认要求的权威是
  capability 声明与真实挂起状态；截止时刻取 SessionStore 落盘后的绝对时刻；摘要取已验证的
  挂起步骤对象/槽值，取不到才回退**任务起点**原话并在 `summary_source` 说明。
- 补槽建议值只取用户**真的看见了**的那份选择卡（复用 `context._is_choice_card` /
  `_candidate_items`），槽形状走 `slot_shape.shape_of`。
- `runtime/issues.py`：Issue 受控枚举、装配与 dict→proto 转换的跨服务唯一声明。
- 端侧四条本地路径共用一个 issue 收集器，在 `Handle` 唯一出口盖到 final 上；
  scope 不足产 `permission.scope_missing`（恢复出口指能力/连接配置，**不指系统权限页**），
  VAL 拒绝产 `safety.val_rejected`。
- F09 窄修复：非法计划且重试仍无有效计划时不再伪装成成功闲聊；`Plan.technical_failure`
  与 `plan_mode` 分列。合法空动作、`addressed=false` 拒识、澄清、重试成功、salvage、
  空计划诚实降级**六条路径逐条留了回归**。端侧 `cloud_had_output` 同时认结构化终态。
- 会话摘要：`security/capability_status.py` 把「注册目录 + 本次授权」算成能力状态，
  三种"不能用"分得开；云侧看不见车辆通道在不在，edge 能力一律 `unknown`，端侧用自己的
  事实覆盖；只读查询**不复用 `handle()`**（那条路会进 Planner、写聊天历史、可能调 LLM）。

**网关**：`eventToMap` 透传四个契约（空数组编码成 `[]` 不是 `null`）；
`GET /api/session` 与 WS 同一套 token 判定，响应与日志不含凭证，后端不可达回 503
且不编造摘要；云网关把 `session_info_request` 转给 Planner 的 `DescribeSession`。

**共享客户端**：`hmi/src/contracts.mjs`（已登记 allowlist）是四种"没有值"的唯一判据；
`pendingOps` 限龄改为服务端截止时刻优先——此前服务端说 60s 而本地按 300s，确认条会多活
4 分钟、点下去必被拒。`hmi/src/quickCommands.mjs` 是首页推荐的可用性筛选。

**Android**：承诺卡标题改服务端摘要优先；风险档/截止时刻取真实值，**没给的时候不许猜低**；
策略不可信时不给确认入口；补槽卡渲染真实建议值并有显式回复入口 `core.slotReply()`
（不经普通发送路由，免得被上一轮候选或定位征询截走）；结构化问题与设备事实分开渲染，
恢复出口只渲染客户端真的实现了的 kind；`retry_request` 只回填输入框不自动重发；
设置页新增「账号与能力（服务端）」，身份与隐私栏改用服务端 `user_id`；设备角色说明行
不再推断「不控车」；首页推荐按能力状态与用户开关筛，摘要不完整时不筛；
F05 播报无声按成因分档（合成失败 / 被打断 / 纯卡片轮），只有第一种提示用户。

**本地验证（同一工作树）**

| 命令 | 结果 |
|---|---|
| `pytest -q -n 8 --dist worksteal`（全量固定口径，TZ=UTC0，修文档证据前） | 8202 passed / 32 skipped / **1 failed** → 该条是接手前既有的文档证据漂移（`4278a52` 上同样红） |
| 同口径复跑（修后，7:50） | **8205 passed / 32 skipped / 0 failed** |
| `pytest -q -n 8 orchestrator/ security/ registry/ scripts/tests` | 3701 passed / 12 skipped |
| mobile `tsc --noEmit` + jest | exit 0；792 passed（75 suites） |
| hmi `npm test` + `npm run build` | 333 passed；build 成功 |
| 四道门禁 + `smoke_edge` | 全 PASS |
| 反向验证 | 停用 T0 闸→8 红；删 registry 还原字段→2 红；停用确认策略→3 红；停用 F09→2 红；无声分档恒判失败→2 红。五次都按字节恢复 |

**网关（2026-09-09 补跑，Go 1.27.0 windows/amd64）**

| 命令 | 结果 |
|---|---|
| `go build ./gateway/...` | exit 0 |
| `go vet ./gateway/...` | exit 0 |
| `go test ./gateway/...` | cloud / deployprofile / edge / tlscfg 四包全 ok |
| 新增 `gateway/edge/session_info_test.go` | 7 passed：token 权威覆写客户端伪造 scope、Bearer 头认证（凭证不进 URL）、响应与日志不含 token、`AUTH_REQUIRED` 才 401 而默认仍匿名放行、后端不可达回 503 且不编造摘要、契约字段逐个透传、非 GET 405 |
| 反向验证 | 把 `stampScopes` 换成信客户端查询串 → 3 条转红，按字节恢复 |

**未做（AR05 仍未签收）**

1. 步骤 6 全部：固定 prod release 构建、OPPO 取证、后端发布与真栈业务证据。
3. V01–V12 只在离线单测层面覆盖；真实多 operationId 实机组合、真实权限矩阵、
   ASR/S2S 回退与 TTS 真机盲听均未做。
4. 补槽的中文名 `display_name` 目前恒空（manifest 还没有这个声明面），客户端回落显示槽机器名。
5. `replay_audio` 恢复动作契约里有、客户端未实现，故不渲染该入口。
