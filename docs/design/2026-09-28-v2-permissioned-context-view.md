# CA2-07：WorkingSet 权限化上下文视图

> 状态：Cloud/Agent 首版已实现、部署并完成权限专项；业务 QA 仍非全绿。代码起点 `01d7d3ea`，本轮云端起点 `55165e50`；发布与精确证据见 §5。
> 复用既有事实服务、Scope 父子覆盖、WorkingSet 和隐私删除通知，不新增数据库、授权服务或运行开关。

## 1. 冻结的字段与授权表

| 数据 | 主体读取权限 | 权威与消费者边界 |
|---|---|---|
| 个人记忆、会话历史、个人焦点/约束/任务 | profile.read，且本轮 memory_enabled 未关闭、有 owner | Memory/SessionState 持有；WorkingSet 与 SDK 读取前后都检查；未读为 off，不伪装为空或服务故障 |
| 当前位置、结构化目的地/路线/位置候选 | location.read；历史焦点还需 profile.read | 当前输入只从 PlanContext.prefs 取，step.meta 不得冒充传感器；收件 Agent 还需声明 location |
| 车辆观测 | vehicle.read.state，且 vehicle_id 匹配 | 复用 CA2-06 的校验镜像；只投影 good、有界未过期信号，保留 source_kind/来源/有效期。写权限不推导出读权限 |
| 单帧视觉引用 | camera.frame | 仍只传引用；收件 Agent 需声明 vision，现有图像 TTL/采集门继续负责实际图像 |
| 候选、会话约束 metadata | profile.read | 收件 Agent 分别声明 candidates/session_constraints；不改变 candidate_downlink 的字段白名单 |
| 安全拦截所需告警 | 保留受控等级/时刻 | 无个人读取权限时仅保留通用告警约束，剥离详细文本；授权过滤先于 pinned/预算，不能削弱执行层联锁 |

会话/记忆中的不透明正文按 owner 的 profile.read 管理；location.read 管当前传感器与结构化位置视图，
不是对全部自然语言历史自动识别位置后逐词擦除。持久化删除、跨来源用途治理和正式账号生命周期仍属 CA2-15/22。
本轮实测主用户已具备这些读权限，另一个 v1 身份仅有 location.read；不修改 AUTH_TOKENS 或任何 .env。

## 2. 数据流与旧接口

- ContextManager 按主体权限读取历史/记忆，再装配同一 WorkingSet；source_states 保留 found/none/unavailable/off 与来源。
- 模型入口绑定本轮视图；受限字段先剔除，随后沿用原焦点/记忆/历史渲染与预算。车辆视图只作为按需引用，不无条件灌入每轮 Planner prompt。
- Unary、D0、T2、改派及恢复共用最终 metadata 投影；先合并再过滤，主体权限与接收方声明取交集；未声明接收需求不默认放行。
- 车态旧标量不再作为事实权威。按需从已验证镜像派生小范围 vehicle_observation，含 vehicle_id、质量、来源和有效期；SDK 复核绑定/有效期后才提供兼容 vehicle.battery 等读取。没有证明时 unavailable，不退到 Memory KV 的旧车态值。
- SDK 的个人记忆、历史和 profile KV 读取也受同一主体策略约束，避免 Planner 已剔除而业务 Agent 再读回来。
- Road-safety 的实际车态读取补齐 vehicle_state 需求声明。它的四项契约转为 revision 2 / 显式 read / 未声明参数拒绝；road_condition 另声明代码已消费的可选 origin。冻结的 legacy 迁移清单保持不动。

现有格式化函数与旧只读接口保留，缺少权限/来源信息不会生成默认事实；执行目录、VAL、require_confirm、
safety_origin_text 和效果判据仍各自权威。内部 vehicle_observation 是来自校验镜像的 RPC 投影，不是第二套数字签名协议或对外授权凭据。

## 3. 失效与晚到结果

RequestView 只保存本轮身份/权限快照与读取有效期，不存另一份会话或事实库。身份、车辆、乘员、请求或权限变化后，
旧视图不能重新绑定；模型调用前后、RPC 发出/收回和流式事件放行前均复核。已消费的车态有效期还限制相关 RPC 的截止时间。
同一 owner/session 收到新的收窄权限、关闭记忆或换车请求时，取消旧在途读取；既有隐私删除通知也使该 owner 的本进程在途视图失效并取消 RPC。
取消、到期和迟到不自动重新执行动作；已提交操作仍需按原回执/Verifier 对账，不能把丢弃结果写成没有执行。

本包覆盖 Cloud WorkingSet/Planner、D0/T2、Agent SDK 和对应模型调用；S2S 的直接历史重建并不经 WorkingSet，
其语音会话主体授权接入归 CA2-16/22 的身份与跨端边界，未因本包关闭。既有 Tailnet 调试/管理 HTTP 的 Memory 读取也不因模型投影生效而获得完整 API 授权，本包不作该声明。没有接入的全局 IdP/token 撤销，以及已经发到供应商的数据回收不冒充已实现。
（2026-10-03 更新：S2S 会话主体与直接历史已在 [CA2-15 S2](2026-10-02-v2-memory-identity-governance.md) 按 token 主体收口；collector 调试面要运维令牌，见 [调试面访问](2026-10-03-v2-collector-access.md)。）

## 4. 验证要求

- 缺 scope 不调用相应事实服务；scope 缺失、关记忆、真正空结果与后台不可用分别核对。
- 恶意 step.meta 不能恢复被剔除的字段或伪造当前传感器；接收方不声明、主体无权限均拒绝。
- 读取/模型等待中撤权、换车/换人、取消与过期，旧结果不进入下一个模型调用、客户端或执行出口。
- 车辆读取用真实共享校验器的视图与有界有效期；测试替身的模拟输入不代替来源认证证据。
- 保留旧确认/取消、声称闸、来源原话和完整结果回归；正向测试显式提供读权限，缺权限反例独立运行。
- 按精确提交完成门禁、全量、部署预检和真栈；真实账号配置、存储 schema 与 CI/CD 均不在本包变更范围。


## 5. 发布与证据（2026-09-28）

实现提交 `0265bc40f1293be2d19ccc7d700f181ec3786da6`；合入并发主线的 P2 容量治理后，
最终 release / 全量测试 / 固定语料 runner 均为 `b095caca46a4a4188e2b26927327117c62326acb`。
旧实现提交的全量读数与合并候选分开；正式本地全量、四道门禁、smoke 和 CI 均通过，数字只维护
[QA 交接 §2](../reviews/2026-08-30-qa-closeout-handoff.md#2-当前发布与证据边界)。未改 .env、token、CI/CD 或数据库 schema。

### 5.1 权限专项与业务回答分开

发布前 `55165e50` 的同一合成会话先完成正常问答，再用仅 network.external 的签名身份请求回忆；
旧版仍回传校验数字，trace `4e86f221527d44cc8b6297a6be46f53d`。这是真实可复现的旧路径越权读取，
不是用空库证明隔离。数据只属于本次 e2e 命名空间，未使用真实用户隐私内容。

发布后最终探针版本 3 完成 3 组 ×4 轮：每组先通过普通问答建立历史，再由 Memory 接口只读核对该
测试 owner 的记录确实存在；随后测试允许读取、受限身份（含伪造读权限 metadata）、主动关闭记忆。

- 权限检查 **60/60**；其中禁止读取/关闭记忆的 6 个场景均为 `cloud.memory_off`、零模型调用、未回显历史数字。
- 12 轮全部零动作，首尾车态摘要相同、release 连续；没有执行确认、车辆状态注入或删除数据。
- 正向最终回答 **2/3**。另一轮 `8739b114e6c146129ec0c0256914adb0` 声称没有记录，但存储存在、Planner 侧 history=found；
  现有 trace 不保存该次 Agent 的完整历史输入，根因未裁定，不直接归因于权限层或模型。总报告的业务 passed=false 保留。

早期探针记录不丢弃：最初共享 QA helper 拒绝其白名单之外的 metadata，负例未发出；专用只读请求构造器随后固定允许测试字段，
共享 helper 不改。发布后两次早期探针分别在正向最终回答/模型可见性检查处遇到规划异常后停止。
最终版本将“数据真实存在、权限是否阻断”与“模型是否正确回答”分栏，并独立核对存储前提；不把不同版本的通过数拼接。
这也是为什么本节只关闭权限机制，不关闭 R0 回忆/规划缺陷。

### 5.2 契约与车辆观测

在线 17 个 Agent / 155 项能力；旧读取方仅隐藏 4 项已迁移的 Road-safety 能力，其余目录保持一致。
新/旧契约、非法数值、流式首事件拒绝、四项旧 Road-safety 版本拒绝及端侧只读契约查询共 9 个探针通过；
旧版本在进入业务前被拒绝，无动作。冻结的 156 项历史迁移记录未改，当前 152 项仍兼容旧字段缺省，4 项使用显式新契约。

签名车辆只读探针 **16/16**：HTTP/WS 身份/来源/时效一致，错车无事实且被拒绝；27 个信号、26 个有效值，
位置 unavailable，源仍为 simulated；前后车态相同。它不代表 OEM 或实车验收。

### 5.3 固定语料与逐条复核

原 corpus hash 不变，20 组 ×3 = **60 case run / 99 测量轮（含取消）**；207 条已记录模型调用全部 pinned
`minimax:MiniMax-M3`。零动作、零车态差异、零证据错误、零 open operation；release 连续，runner 未变。
混合手册呈现 9/9，V211/V220 六个回答保留已登记的胎压完整条件；没有重跑旧版图标专项或整本事实审计。

原始自动判红 2 轮：

| 样本 / trace | 复核 |
|---|---|
| V207 r1 / `edf200150db54f8b89b62bfc94521e30` | 已解释停车休息时维持舒适的用途，仅未重复“露营”；关键词误报 |
| V210 r1 / `a56e4753ff0641ce81f159ee565e730f` | 首次缺 steps 后重试落 chitchat；泛答温度指令，未给手册结果。同轮还观察到 Embed 超时，不能把所有原因合为一个 |

人工另发现两项自动判据未报红的业务未完成：

| 样本 / trace | 复核 |
|---|---|
| V207 r3 / `f8af0a39abdb40cda252da1c200bdb82` | 列内置场景和开启说法，没有解释露营模式含义 |
| V214 r3 / `97ef206982134dabb8f18a6bf102cb48` | 手册已派发，生成层把前备箱容量当作后备箱回答，出现“后备箱的载物空间在前备箱”；属于主体/答案保真问题 |

本轮因此登记 3 个真实业务未完成轮，加上权限专项的一次正向回答残余，分别保留原入口；不从不同分母宣称整体提升比例。
Scope/S2S/正式身份边界、Android 设备与声学均不借此签收。下一包按路线图进入 CA2-08 的持久准入方案。

### 5.4 工件

根仓本地 `.artifacts/context-view/`（ignored，clone 不自带），总索引 `b095caca-evidence-manifest.json`；
保留源码/runner/模型/脚本版本和前测失败。关键 SHA-256：

- 固定语料 `b095caca-baseline.json`：`232294038ad2d39826befc88e329cc3ff435a5c759df73b3976a81cb0de7cd87`。
- 逐条复核 `b095caca-baseline-review.json`：`5ac35194599e2380f0de9fbe68e7f15ca126b5aef0f7500530fa68bd176aa168`。
- 权限最终版本 `b095caca-permissions-v3.json`：`819a4928b6857f91f4f491d500fefddd1a1990bf411b57ef58dc38845258e2b8`。
- 契约 `b095caca-live-contract.json`：`61d539c0fddc46cdccf7f40d167d217c36b2f2e7156328aebe3a1a4920e2dbb6`。
- 签名观测 `b095caca-signed-observation.json`：`d69cf0ddb40a8383bf5f5defb5f87cc30fbd2affc4ca5c4c21af6b2aa92b4edd`。
