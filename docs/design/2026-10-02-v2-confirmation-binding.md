# CA2-09：挂起一次消费与确认绑定

> 状态：2026-10-02 契约冻结稿，实现中；不改数据库 schema（只扩展 Redis 挂起表的 JSON 字段）。
> 依赖：[CA2-05 能力契约](2026-09-27-v2-capability-contract.md)、[CA2-06 车辆身份](2026-09-27-v2-vehicle-state-and-simulation.md)、
> [CA2-08 持久准入](2026-10-01-v2-durable-operation-admission.md)。排序见[路线图](../roadmap.md)。

## 1. 缺口（2026-10-02 复核）

| 现状 | 后果 |
|---|---|
| 确认 / 补槽 / 澄清选择三条续接路径都是「读到挂起 → 恢复 → 派发 → 事后删除」，没有认领 | 双击、语音加点按、客户端超时重发会让两个回合各自派发同一步。CA2-08 真栈探针已直接看到：两轮确认都关闭了同一条挂起 |
| 挂起表按整表「读-改-写」落 Redis | 并发写者后写覆盖，删除也不能证明「只有我消费了它」 |
| 确认只按 owner / session 寻址 | 不绑定车辆、参数、能力版本和计划版本；CA2-08 只为 durable 能力在执行方补了参数绑定 |

durable 能力的重复派发已被执行方挡住；端侧 VAL 写（后备箱、门锁）、提醒、场景、导航和补槽后的写仍会执行两次。

## 2. 冻结的契约

### 2.1 一次消费（全部续接路径）

- 恢复派发之前，回合必须对目标挂起赢得一次 **claim**：`SessionStore.claim_result(session, owner, operation_id, token)`。
- 记录新增 `claimed_by`（本回合随机 token）与 `claimed_at`。Redis 用 WATCH（挂起表 key + owner 隐私栅栏）→ 读 → 校验
  → MULTI SET（保留剩余 TTL）→ EXEC；被并发修改则重读重试，最多 3 次。内存兜底在同一事件循环内同步比较并写入。
- 结局：`claimed` / `already_claimed` / `absent`（已删除或已过期）/ `unavailable` / `fenced`。只有 `claimed` 继续恢复。
- 认领不会在挂起过期前失效：认领者在派发后崩溃，之后的「确认」只得到「正在处理或结果未知」，**不会再派发一次**。
  认领者收尾照旧按 operation_id 删除（`_settle_session` / 再挂起的 `replaces`）。
- 未认领的挂起照旧参与寻址、提醒与取消；取消已认领的挂起只说明它已在处理，不宣称撤回已发出的动作。

### 2.2 确认绑定（wait_confirm）

挂起时写入 `confirmation`：

`{v:1, vehicle_id, step_id, operation_id, capability_revision, params_sha256, task_id, plan_revision, binding_sha256}`

- `operation_id` 是 CA2-08 的执行身份（非 durable 步为空），不是客户端回传的挂起寻址键；参数摘要沿用 `runtime.operation.params_digest`。
- 执行点复核：执行器在槽引用解析之后、派发之前，对带 `confirmed` 的那一步按当前请求车辆与最终参数重算；
  任何一项不同即不派发，回「确认的内容与将要执行的不一致，为安全起见没有执行」。
- 旧挂起没有 `confirmation`：按原行为放行并记日志（滚动发布窗口内的在途确认不被打断）。
- 端侧执行点不变：VAL 在执行时自己重读行驶状态，确认不能覆盖车端联锁。

## 3. 兼容与回滚

- 只增加挂起表 JSON 字段，不碰 PostgreSQL、不加 Redis key 前缀，隐私登记不变。
- 旧版本读到含新字段的条目会整条跳过（既有 `_decode` 语义）：回滚后这些挂起不可见、也就不会被执行，用户需重新发起。
- 停用：去掉认领调用即回到旧语义；绑定缺失时本就兼容。

## 4. 验证计划

1. 存储：内存兜底与 fakeredis（scratchpad 隔离 venv）上的认领语义，含并发认领、过期、隐私栅栏与 WATCH 冲突重试。
2. 引擎：同一挂起的两个并发确认 / 补槽 / 澄清选择各只派发一次；第二个回合零动作、不关闭挂起。
3. 绑定：换车、参数在恢复后变化、能力版本变化均不派发；旧记录放行；VAL 在确认后挡位改变时拒绝。
4. 反向验证、全量、四门禁；真栈只读核对 + 是否复跑演示商户双确认另行征得授权。
