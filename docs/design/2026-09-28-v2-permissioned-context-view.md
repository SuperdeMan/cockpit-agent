# CA2-07：WorkingSet 权限化上下文视图

> 状态：实现与离线验证中，尚未发布。代码起点 `01d7d3ea`；本轮复核云端起点为 `55165e50`。
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
其语音会话主体授权接入归 CA2-16/22 的身份与跨端边界，未因本包关闭。没有接入的全局 IdP/token 撤销，以及已经发到供应商的数据回收不冒充已实现。

## 4. 验证要求

- 缺 scope 不调用相应事实服务；scope 缺失、关记忆、真正空结果与后台不可用分别核对。
- 恶意 step.meta 不能恢复被剔除的字段或伪造当前传感器；接收方不声明、主体无权限均拒绝。
- 读取/模型等待中撤权、换车/换人、取消与过期，旧结果不进入下一个模型调用、客户端或执行出口。
- 车辆读取用真实共享校验器的视图与有界有效期；测试替身的模拟输入不代替来源认证证据。
- 保留旧确认/取消、声称闸、来源原话和完整结果回归；正向测试显式提供读权限，缺权限反例独立运行。
- 按精确提交完成门禁、全量、部署预检和真栈；真实账号配置、存储 schema 与 CI/CD 均不在本包变更范围。
