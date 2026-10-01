# CA2-09：挂起一次消费与确认绑定

> 状态：2026-10-02 已部署 `56c5519f`（status 5/5、verify verified）；不改数据库 schema，只扩展 Redis 挂起表的 JSON 字段。
> 车端 `confirm_state` 复核随同一 release 上线。真栈并发写探针本包未复跑，见 §5。
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

## 5. 实现与证据（2026-10-02）

| 验证面 | 精确版本 / 结果 |
|---|---|
| 专项 | 云侧 `test_pending_claim.py` 14（存储认领、确认/补槽/澄清/取消四条路径的并发、绑定与恢复、客户端伪造快照）；端侧 `test_confirm_state.py` 4 |
| Redis 路径 | `test/probe_pending_claim_redis.py` 在 scratchpad venv 的 fakeredis 上 6/6：8 路并发认领只有 1 个赢家、其它条目与 JSON 列表形状不被改写、TTL 保留、过期/栅栏、WATCH 冲突后重试成功（断言 EXEC 恰好两次） |
| 反向验证 | 13 处注入缺陷全部判红。首轮漏掉「澄清路径去掉认领」：内存存储在删除前没有 await，第二个回合根本读不到挂起；改用读挂起时让出事件循环的存储后转红 |
| 本地全量 | `56c5519f`：10446 passed / 35 skipped / 11 warnings（287.20 s）；四门禁、smoke 13/13、`capability_inventory --check` |
| 发布 | dry-run 零阻断（无 schema 摘要），apply submitted；status ok、release/running 均为 `56c5519f`、5/5、零 warning；verify `20261001T172435Z-56c5519.json`（e2e_remote_safe / MiniMax-M3）verified |
| 固定语料 | 固定语料 20×3：60/60 完成、101 轮，业务红 4（3 轮未走手册；V207 抢救重试轮原样抄了 `planning.py` 的澄清结构示例「云岚国际中心」，与本包无关），证据错误 0、open operations 0，217 次 LLM 全为 minimax/MiniMax-M3，零动作、零车态变化；取消挂起 3/3 按寻址关闭；p50/p95/p99 7047/17375/28375 ms（p50 升高来自模型侧：规划调用中位 1404→2596 ms） |

真栈「两个并发确认」没有复跑：上次的演示商户写授权已用于 CA2-08；需要时用 `scripts/probe_operation_double_confirm.py`
另取授权。期望读数是第二个回合由编排器答「这条操作已经在处理了，我没有重复执行」，执行方只收到一次确认派发。

## 6. 已知边界

- 认领者在派发后崩溃，之后的确认只得到「这条操作已经在处理了，我没有重复执行」，直到挂起过期（300 s）；结局查询与恢复对账归 CA2-11。
- 车端只比对档位与是否行驶；更细的车型前置条件（车速阈值、门锁与充电口联动等）等真实车型接口，不猜量产阈值。
- 旧挂起没有绑定或快照时兼容放行，窗口不超过挂起 TTL（`SessionState.ttl_seconds`=300 s）。
- 规划器抢救重试轮会原样抄提示词里的澄清结构示例（`planning.py`「云岚国际中心」，固定语料 V207 r3 trace `5145fc3b40804654b6243afe5af9c4f4`）：
  首次调用的计划是对的，重试结果覆盖了回落候选。属于 R0 规划质量残余，不在本包修；改提示词须按规划知识 A/B 走新提交。
