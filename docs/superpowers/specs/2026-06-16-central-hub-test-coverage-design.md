# 中枢测试覆盖设计

> 状态：P0 已落地（L0 单测 + L1 全栈断言 5/5）；P1 进程内缺口已补——collector 重启
> 快照自愈、端侧本地轮记忆 best-effort 回归，L1 增 trace 全链贯穿断言（P1-8）；P1 其余
> （真实 LLM 指代理解等）按设计留 nightly/人工巡检，P2 语料持续扩展。2026-06-16
> 日期：2026-06-16
> 范围：Cloud Planner / Unified Dispatcher / Edge 回流 / VAL / 可观测 collector / Dashboard 观测链路
> 参考提交：`c4b4097a4d851cae8bc81b0513bf02082e971f58`

## 1. 背景

`c4b4097a` 新增 `test/e2e_observability.py`，用 collector 从三条线观察一次指令：

- `obs.span`：中枢分发链路，例如 `route.*`、`cloud.planning`、`step.agent|edge|tool`、`aggregate`。
- `/api/vehicle/state`：VAL 执行前后的车辆状态 diff。
- final 结果：Agent 执行状态、`need_confirm`、安全门控反馈。

这次专项测试已经证明“中枢分发到末端执行再到仪表盘”可以跑通，也暴露并修复了天窗、媒体、氛围灯、`和` 字并列拆分、流式单步 span、`NEED_CONFIRM/NEED_SLOT` span 状态等缺陷。

现有脚本仍偏人工巡检：它会打印链路和状态，但缺少自动断言。下一步应把中枢关键能力沉淀为可持续回归的测试集，避免后续改 Planner、Dispatcher、Edge、VAL 或观测层时把核心链路打断。

## 2. 目标

1. 把中枢最重要的安全和调度边界转成自动化断言。
2. 保持测试分层：快单测守契约，全栈 E2E 守真实接线，人工专项脚本保留深巡检能力。
3. 覆盖正向链路、负向链路、故障恢复和观测一致性。
4. 覆盖多轮交互上下文：历史注入、指代续接、补槽/确认挂起、换话题、session 隔离与记忆开关。
5. 避免所有质量门槛都依赖真实 LLM。需要 LLM 的测试单独标记为全栈专项，不进入默认快速 CI。

## 3. 非目标

- 不在本轮引入 Prometheus、OTel、Tempo 或持久化 trace。
- 不改 proto 契约。
- 不把真实外部 Provider、真实支付、真实 SOME-IP/CAN 纳入本测试设计。
- 不把 Dashboard 视觉验收作为中枢测试的阻塞条件；Dashboard 组件已有单测，E2E 只验证 collector 数据可被消费。

## 4. 测试分层

### 4.1 L0：进程内契约测试

位置：

- `orchestrator/cloud/tests/`
- `orchestrator/edge/tests/`
- `observability/collector/tests/`
- `observability/tests/`

职责：

- 不启动 docker。
- 不依赖真实 LLM。
- 用 stub 精确验证中枢函数级行为。

新增重点：

- `UnifiedDispatcher` 对 `NEED_CONFIRM` 和 `NEED_SLOT` 发出的 step span 必须是 `wait`。
- 单步流式直通必须补 `step.agent:<id>` span。
- `PlannerEngine` 规划时只注入此前历史，本轮用户文本不提前污染历史；结束后按 user -> assistant 顺序写入。
- `memory_enabled=false` 时不读历史、不写历史。
- 挂起确认/补槽时只重跑挂起 step，不重跑已完成 step；换话题时丢弃挂起任务并按新请求规划。
- edge 回流 `_origin=edge_val` action 不允许二次下发 VAL。
- `split_and_classify_any` 对本地并列车控拆分，对非 local 语义保持完整上云。
- collector debug 白名单只允许环境量，不允许写车控输出状态。

### 4.2 L1：断言型全栈 E2E

新增文件建议：

- `test/e2e_central_hub_assertions.py`
- `test/fixtures/central_hub_cases.yaml`

职责：

- 需要 `make up` 后运行。
- 复用现有 WebSocket 入口和 collector REST。
- 每条 case 明确声明期望的 span、禁止出现的 span、车辆 diff、final 状态和确认态。

核心机制：

```yaml
- name: t0_hvac_local
  text: 打开空调26度
  expect_spans:
    - route.local
    - val.execute
  forbid_spans:
    - cloud.planning
  expect_state:
    hvac_on: true
    hvac_temp: 26
  expect_need_confirm: false
```

runner 行为：

1. 可选调用 collector debug API 设置环境量。
2. 生成 trace_id，发到 edge gateway WebSocket。
3. 等待 final 和 collector 落库。
4. 拉取 `/api/traces/{trace_id}` 与 `/api/vehicle/state`。
5. 对 span 顺序、状态、车辆 diff、确认态做断言。

### 4.3 L2：人工深巡检专项

保留并增强：

- `test/e2e_observability.py`

职责：

- 继续打印完整链路、耗时、diff 和话术。
- 用于真实 LLM、复杂多意图、演示前巡检。
- 不作为默认 CI 阻塞项。

增强建议：

- 支持 `--case <name>`。
- 支持 `--json-out <path>` 输出结构化报告。
- 当实际链路缺少 span 或 diff 时，用明显失败摘要标出。

## 5. P0 测试集

P0 是中枢生命线，优先落地。

| 编号 | 场景 | 输入 | 核心断言 |
|---|---|---|---|
| P0-1 | T0 本地闭环 | `打开空调26度` | 出现 `route.local`、`val.execute`；不出现 `cloud.planning`；`hvac_on=true`、`hvac_temp=26` |
| P0-2 | 云端单 Agent | `导航去北京南站` | 出现 `route.cloud`、`cloud.planning`、`step.agent:navigation`、`aggregate` |
| P0-3 | 混合意图 | `打开主驾座椅加热，然后导航去首都机场` | 本地座椅状态改变；云端只处理导航子请求；同一 trace 有 local 和 cloud 节点 |
| P0-4 | 危险动作确认 | `打开后备箱` | 第一轮 `need_confirm=true` 且 `trunk` 不变；确认轮后 `trunk=open` |
| P0-5 | 安全门控 | debug `speed_kmh=130` 后 `打开车窗` | `val.execute` 为 `err` 或 final 表示拒绝；`window` 不变 |
| P0-6 | 防双发 | 云端 edge_call 已在端侧 VAL 执行 | `_origin=edge_val` action 仅展示，不二次执行 VAL |
| P0-7 | 等待态 span | Agent 返回 `NEED_CONFIRM` / `NEED_SLOT` | step span 状态为 `wait`，不是 `err` |
| P0-8 | 权限边界 | third_party 请求 `vehicle.control` | dispatch 前 `REJECTED`，cloud/edge/tool transport 都不被调用 |
| P0-9 | 多轮确认上下文 | 订餐/开后备箱第一轮挂起，第二轮 `确认` | 已完成 step 不重跑；挂起 step 带 `confirmed=true` 重跑；session 清理 |
| P0-10 | 多轮补槽上下文 | 第一轮 `导航去` 或 `订个餐厅` 缺关键槽，第二轮补目的地/时间人数 | span 先 `wait`，补槽轮复用原 plan；最终进入正确 Agent |
| P0-11 | 换话题取消挂起 | 第一轮危险确认挂起，第二轮问 `附近有什么充电站` | 不执行原危险动作；清掉挂起任务；按新请求重新规划 |

## 6. P1 测试集

P1 保障复杂路径、故障恢复和观测可信度。

| 编号 | 场景 | 核心断言 |
|---|---|---|
| P1-1 | collector 重启自愈 | collector 清空后，等待 edge snapshot；`/api/vehicle/state` 恢复非空 |
| P1-2 | registry 重启自愈 | registry 重启后，周期重注册恢复 Agent 列表；planner 可继续 resolve |
| P1-3 | NATS 不可达 | 主链路不失败；EventEmitter 不抛错、不阻塞 |
| P1-4 | edge transport 失败 | edge step 返回 `FAILED edge_unreachable`，不误报成功 |
| P1-5 | 流式失败回退 | ExecuteStream 失败但未产生 delta 时回退 unary；已有 delta 无 final 时不重复执行 |
| P1-6 | T2 有界循环 | adaptive plan 执行初始 batch、replan、聚合；预算耗尽返回 best effort |
| P1-7 | 部分失败跳过 | DAG 上游失败后依赖步骤 skipped，独立步骤可继续 |
| P1-8 | trace 一致性 | 前端 meta trace_id 从 gateway 贯穿到 edge、cloud、collector |
| P1-9 | 历史注入指代 | 第一轮 `导航去首都机场`，第二轮 `换成最快路线` | 第二轮 planner prompt/plan 带此前目的地上下文；不会把 `最快路线` 当独立闲聊 |
| P1-10 | session 隔离 | session A 说目的地，session B 说 `换成最快路线` | B 不继承 A 历史，需澄清或走 fallback |
| P1-11 | 记忆关闭 | meta `memory_enabled=false` 连续两轮 | 不读取历史、不写入历史；第二轮无法依赖第一轮指代 |
| P1-12 | 本地轮记忆 best-effort | 第一轮本地车控 `空调调到24度`，第二轮 `再低一点` | 本地/云端可拿到必要上下文；最终 `hvac_temp` 下降一档 |

## 7. P2 语料扩展

P2 用于持续扩大中枢场景覆盖，不要求一次全部落地。

### 7.1 连接词和多意图

- `座椅加热和座椅通风安排上`：拆成两个本地车控。
- `导航去北京和上海`：不按本地并列拆，整句上云。
- `播一首周杰伦的歌`：歌手限定不被拆掉。
- `空调关上，然后导航去欢乐谷，走最快路线，再播周杰伦的歌`：本地和云端语义组正确分流。

### 7.2 车控对象覆盖

- 空调温度、风速、开关。
- 车窗、天窗百分比、遮阳帘。
- 座椅加热、通风。
- 氛围灯开关、设色、亮度。
- 媒体播放、暂停、下一首、音量。
- 雨刮、后视镜、方向盘加热。

### 7.3 云端 Agent 组合

- 导航 + 餐厅搜索。
- 餐厅搜索 + 预订确认。
- 行程规划 + 导航 + 天气。
- 开放域 chitchat 流式。
- 工具调用 `math.eval` / `datetime.parse`。

### 7.4 多轮上下文语料

- 目的地指代：
  - 第一轮：`导航去首都机场`
  - 第二轮：`换成最快路线`
  - 期望：第二轮仍围绕首都机场，不丢目的地。
- POI 指代：
  - 第一轮：`帮我找附近评分高的粤菜馆`
  - 第二轮：`就第一家，订今晚七点两位`
  - 期望：第二轮复用候选餐厅上下文，进入预订确认。
- 补槽：
  - 第一轮：`订个川菜馆`
  - 第二轮：`今晚七点，两个人`
  - 期望：第一轮 `NEED_SLOT`，第二轮补槽后继续原 plan。
- 挂起后换话题：
  - 第一轮：`打开后备箱`
  - 第二轮：`算了，导航去最近的充电站`
  - 期望：后备箱不打开；新请求走导航。
- 混合跨轮：
  - 第一轮：`打开空调，然后导航去公司`
  - 第二轮：`途中找个咖啡店`
  - 期望：第二轮沿用导航上下文，新增途中 POI，而不是独立搜索任意咖啡店。
- 记忆关闭：
  - 第一轮：`导航去首都机场`，meta `memory_enabled=false`
  - 第二轮：`换成最快路线`，meta `memory_enabled=false`
  - 期望：第二轮不应隐式知道首都机场。

## 8. 断言规范

每个断言型 E2E case 至少声明下列字段中的一部分：

- `expect_spans`：必须出现的节点。
- `forbid_spans`：禁止出现的节点。
- `expect_span_status`：某节点必须是 `ok`、`wait` 或 `err`。
- `expect_state`：执行后的车辆状态。
- `expect_state_unchanged`：安全门控或确认前必须不变的字段。
- `expect_need_confirm`：final 是否需要确认。
- `expect_speech_contains`：必要话术片段，避免过度绑定完整自然语言。
- `setup`：只允许 collector debug 白名单环境量。
- `turns`：多轮 case 的请求列表，每轮可带独立 text、meta、is_confirmation 和期望。
- `expect_session_cleared`：确认/取消/换话题后挂起态应被清理。
- `expect_agent_call_counts`：多轮确认中验证已完成 step 不重跑。

Span 顺序只对关键因果关系做要求：

- 本地路径：`route.local` 必须早于 `val.execute`。
- 云端路径：`cloud.planning` 必须早于 `step.*`，`step.*` 必须早于 `aggregate` 或 `suspended`。
- 等待态：`step.*[wait]` 必须配套 final `need_confirm` 或 `follow_up`。
- 多轮路径：同一 session 的第二轮可以使用新 trace_id，但必须能通过 session 状态证明它续接或清理了上一轮挂起上下文。

多轮 case 示例：

```yaml
- name: confirm_resume_without_rerun
  turns:
    - text: 订川菜馆今晚七点两位
      expect_need_confirm: true
      expect_spans:
        - cloud.planning
        - step.agent:food-ordering
      expect_span_status:
        step.agent:food-ordering: wait
    - text: 确认
      is_confirmation: true
      expect_need_confirm: false
      expect_session_cleared: true
      expect_agent_call_counts:
        food.search_restaurant: 1
        food.reserve: 2
```

## 9. 运行策略

默认快速验证：

```bash
python -m pytest --import-mode=importlib orchestrator/cloud/tests orchestrator/edge/tests observability/tests observability/collector/tests
```

全栈断言验证：

```bash
make up
python test/e2e_central_hub_assertions.py
```

人工深巡检：

```bash
python test/e2e_observability.py
```

真实 LLM 复杂多意图建议保留在人工深巡检或 nightly，不进入普通 PR 快速门禁。

## 10. 通过标准

P0 完成后应满足：

- 中枢正向链路自动断言覆盖 T0、云端单 Agent、混合意图、危险确认、安全门控。
- 关键等待态、权限拒绝、防双发、多轮确认/补槽/换话题都有进程内单测。
- 全栈断言脚本能在 collector 中证明 span 和车辆状态一致。

P1 完成后应满足：

- collector、registry、NATS、edge transport、流式失败等故障路径有回归。
- 历史注入、session 隔离、记忆关闭和本地轮记忆都有回归。
- 中枢不再只验证“成功一次”，而是验证“坏情况下边界仍正确”。

## 11. 风险与控制

| 风险 | 控制 |
|---|---|
| E2E 受真实 LLM 波动影响 | 默认断言 case 选确定性强的路径；真实 LLM 复杂场景放专项 |
| 过度绑定自然语言话术 | 只断言必要关键词、状态和 span，不断言完整话术 |
| 全栈测试慢 | P0 E2E 控制在 5-8 条；大语料放 P2 或 nightly |
| collector best-effort 有延迟 | runner 支持轮询等待 trace/span 到齐，设置明确超时 |
| 环境状态污染 case | 每条 case 用 debug reset 必要环境量；状态断言优先使用字段级预期 |
