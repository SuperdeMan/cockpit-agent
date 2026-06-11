# 2026-06-11 全项目 Review 修复清单

> 本文档是 2026-06-11 全量代码 review 的修复交接清单。**接手人（人或 Agent）从这里继续工作。**
> 规则：每修完一项，更新该项的「状态」并在末尾「修复日志」补一行；发现新问题按同样格式追加条目。
> 修复优先级：P0 = 核心链路跑不通 / 安全承诺未兑现；P1 = 验证体系失效；P2 = 代码级缺陷；P3 = 文档漂移。

## 如何验证

```bash
# 一条命令全量测试（conftest.py 已配好 PYTHONPATH，--import-mode=importlib 解决重名）
python -m pytest test/ orchestrator/cloud/tests/ security/tests/ observability/tests/ agents/ --import-mode=importlib -q
python test/smoke_edge.py
# 或
make test
```

基线（2026-06-11 F6-F9 修复后）：114 passed；smoke 13/13。

---

## P0 — 核心链路断裂

### F1. 多轮确认闭环端到端不可达 ✅ 已修复（2026-06-11，见修复日志）

**现象**：旗舰场景「找川菜馆订位」只能走到"需要帮您订吗？"，用户无论说什么都无法完成下单。

**断点证据**（修复前）：
- `hmi/src/App.tsx`：不渲染 `final.need_confirm`/`follow_up`，无确认交互，不发送 `is_confirmation`。
- `gateway/edge/main.go` `wsRequest`/`Request()`：WS JSON 不解析、HandleRequest 不设置 `IsConfirmation`。全仓库该字段无任何生产者。
- `orchestrator/cloud/clients.py` `call_agent()`：不向 Agent 传 `meta`，确认标记无通道。
- `agents/food_ordering/src/agent.py` `_reserve()`：无条件返回 NEED_CONFIRM，没有"已确认→调 provider.reserve()"分支（Mock provider 的 `reserve()` 一直存在但从未被调用）。`agents/parking_payment` 的 `_pay()` 同病。
- `orchestrator/cloud/engine.py` `_resume_plan()`：忽略 `SessionState.completed_results`，恢复后整个 DAG 从头重跑。

**修复方案**（已按此实施）：
1. HMI：`need_confirm` 的 final 消息渲染「确认/取消」按钮，点击发送 `{text, session_id, is_confirmation: true}`；同时渲染 `follow_up`。
2. Edge Gateway：`wsRequest` 增加 `is_confirmation`，`Request()` 透传到 `HandleRequest.IsConfirmation`。
3. Edge Orchestrator：`is_confirmation=true` 的请求跳过 Fast Intent 直接上云（确认必须回到挂起会话所在的云端）。
4. Engine：
   - 恢复时用 `completed_results` 种子 executor 的 `done`，**只重跑挂起步骤及其后继**；
   - 仅给 `pending_step_id` 那一步注入 `meta={"confirmed":"true"}`（确认严格限定单步，后续 require_confirm 步骤各自再走确认，符合架构 §9.1）；
   - 取消词（取消/不用/算了…）→ 清会话 + 取消话术；
   - 带确认标记但无挂起会话（TTL 过期）→ 明确话术，不把"确认"二字拿去重新规划；
   - 语音兜底：存在挂起会话时，短肯定话术（确认/订吧/好的…）即使没带标记也按确认处理；答非所问则清会话按新请求走。
5. Executor：`run(plan, ctx, done=None)` 支持种子结果；已在 done 中的步骤不再执行；`step.meta` 透传给 call_agent。
6. `clients.call_agent` 增加 `meta` 参数 → `ExecuteRequest.meta`。
7. food_ordering `_reserve` / parking_payment `_pay`：`meta.confirmed == "true"` 时真正调 provider 完成交易，返回 OK + 凭证。

**验收**：`orchestrator/cloud/tests/test_engine_confirm.py` 覆盖：确认完成下单且不重跑已完成步骤、取消、过期、语音短语确认。e2e 场景待 docker 联调后用 `test/e2e_ws.py` 的订位用例验证。

**遗留**：NEED_SLOT（补槽）续接仍未闭环——`StepResult`/proto 没有 missing_slots 字段，engine 无法把用户回复填进缺失槽位。需要 proto 增字段 + engine wait_slot 分支，单独排期（见 F12）。

### F24. cloud-planner 容器启动即崩（import 体系冲突）✅ 已修复（2026-06-11）

**证据**（修复前）：Dockerfile `WORKDIR /app/orchestrator/cloud` + `CMD python main.py`，而 main.py 平铺 `from engine import ...`、engine.py 却是相对 import `from .models import ...` → 启动即 `ImportError: attempted relative import`；且镜像未 COPY `security/`（engine 依赖 `security.permission`）、PYTHONPATH 不含 /app。**该容器从未成功启动过**——also 佐证整栈从未联调。

**修复**：main.py/server.py 统一为包内相对 import；Dockerfile 改 `WORKDIR /app` + `COPY security` + `PYTHONPATH=/app:/app/gen/python` + `CMD python -m orchestrator.cloud.main`。验证：`PYTHONPATH=root:gen/python python -c "import orchestrator.cloud.main"` 通过（容器内行为同构）。

### F2. 权限体系在生产路径上是死代码 ✅ 部分修复（2026-06-11）

**修复**：
1. `_build_context` 从 `HandleRequest.meta["granted_scopes"]`（逗号分隔）解析 `granted_permissions`——权限有了来源。
2. `_filter_by_permission` 改 fail-closed：`granted=[]` 只放行无权限要求的 Agent（如 chitchat）；`None` 才表示不启用。third_party+vehicle.control 硬禁令无论授权都执行。
3. engine.run 加 `_enforce_permissions` 占位（当前因 Step 不含 manifest 无法做运行时校验，依赖规划阶段过滤）。

**遗留**：执行层二次校验（perms.check 硬拒绝）需 Step 增加 manifest 缓存，归入 F3 proto 批次。

### F3. 跨步参数传递 slot_refs 结构性断裂 ✅ proto 已修复（2026-06-11），代码接线待做

**已修复**：`ExecuteResponse` proto 增加 `google.protobuf.Struct data = 7` + `repeated string missing_slots = 8`（F12），codegen 通过。

**待做**：SDK `_result_to_proto` 填充 data；executor `_to_result` 读取 data；Agent 改造（navigation search_poi 放 data）。这部分改动涉及 SDK 和所有 Agent，单独排期。

### F4. 计划校验漏洞：intent 全局校验 + 静默替换 ✅ 已修复（2026-06-11）

**修复**：构建 `agent_id → set(intents)` 映射，按 agent 校验 intent；不属于该 agent 能力集的 intent 直接丢弃该 step（不替换）；全部丢弃则走语义路由降级。去掉了 `_build_intent_set` 的全局集合引用。

### F5. 三处接线断点：端侧链路在组网层面断开 ✅ 已修复（2026-06-11，Go 未编译验证）

**修复**（方案 A，贴架构文档）：
- **W1 修复**：edge gateway `main()` 改为连接 `EDGE_ORCHESTRATOR_ADDR`（gRPC），WS handler 改调 `EdgeOrchestrator.Handle`——快意图走端侧编排器本地秒回，慢意图由端侧编排器上云。
- **W2 修复**：`cloud_client.py` 重写为走 `EdgeCloudChannel.Connect` bidi 协议（发 Hello → 收 HelloAck → 发 Request → 收 Event → final 结束），与 cloud-gateway 的帧协议对齐。

**遗留**：
- **Go 未本地编译验证**（环境无 Go toolchain），靠人工语法复核。CI 环境（ubuntu-latest）有 Go，push 后 CI 会暴露编译问题。
- **cloud_client.py 的 bidi 流是逐请求模式**（每次 handle 新建流），不是持久长连。Phase 2 需要实现持久 bidi + 多路复用 + 断线重连（对应 Go ChannelClient 的 connectLoop/pingLoop/recvLoop 逻辑）。
- **e2e 验收待 docker 联调**：`test/e2e_ws.py` 链路 1（车控快路径）的 vehicle.control 预期在端侧编排器接上后应能满足。

### F6. `make test` 带 `|| true`，测试永远绿 ✅ 已修复（2026-06-11）

**修复**：去掉 `|| true`，合并为单条 `python -m pytest`，纳入 agents/ 和 --import-mode=importlib。

## P1 — 验证体系失效

### F7. 测试基建：根目录 pytest 直接挂 ✅ 已修复（2026-06-11）

**修复**：根 `conftest.py` 注入 repo root + gen/python 到 sys.path；`--import-mode=importlib` 解决 test_agent.py 重名收集冲突；目标达成：`python -m pytest ... agents/ --import-mode=importlib` 一条命令全量通过。

### F8. manual_rag 测试从未通过 ✅ 已修复（2026-06-11）

**修复**：断言改为校验 chunks 内容含"胎压"（source 是章节名，不含关键词是正确行为）。

### F9. CI 不覆盖 agents/*/tests ✅ 已修复（2026-06-11）

**修复**：CI Unit tests 步骤改为单条 `python -m pytest ... agents/ --import-mode=importlib`，去掉手工 PYTHONPATH。

### F10. 缺失的契约测试 ⬜ 部分修复

**证据**：`agents/parking_payment/tests/` 是空目录；`agents/trip_planner/` 无 tests/——违反 CLAUDE.md §3「新增 Agent 必须写契约测试」。
**进展**：parking_payment 已随 F1 补 `tests/test_parking_payment_agent.py`（find/确认支付闭环）。trip_planner 仍缺。

### F11. 测试数字三处口径矛盾 ✅ 已修复（2026-06-11）

**修复**：AGENTS.md 改为不写具体数字，写「`python -m pytest ... --import-mode=importlib` 全绿」；CLAUDE.md §7 指向修复文档；README 保持 87/87 不改（Phase 0 时期数字，不再更新）。

### F12. NEED_SLOT 补槽续接未闭环 ✅ proto 已修复（2026-06-11），engine 分支待做

**已修复**：`ExecuteResponse` proto 增加 `repeated string missing_slots = 8`，codegen 通过。

**待做**：engine wait_slot 分支把用户文本（或经 LLM 抽槽）填入挂起 step 的对应槽位后续跑。依赖 F3 的 SDK data 接线。

## P2 — 代码级缺陷

### F13. Go 网关并发写同一 gRPC stream（数据竞争）⬜ 未修复

**证据**：`gateway/cloud/main.go:88` 每请求 `go handleRequest`，多 goroutine 对同一 bidi stream 并发 `Send`——grpc-go 明确禁止并发 SendMsg。edge 侧同病：`gateway/edge/main.go` `Request()` 持 RLock 并发 Send，`pingLoop` 又在另一 goroutine Send。
**修复方案**：每个 stream 一个发送 mutex（或单写者 goroutine + channel）。cloud/edge 两处都要。

### F14. edge `_dispatch_cloud_actions` 死代码 + 拒绝不改话术 + payload 类型错误 ✅ 已修复（2026-06-11）

**修复**：payload 用 `AsMap()`（原生类型）；VAL 执行结果/拒绝真正替换 final.speech；去掉构建后丢弃的 dispatched_actions。

### F15. `clients.call_agent` 每次新建 channel 不关闭 ✅ 已修复（2026-06-11）

**修复**：`_ch_agents` 字典按 endpoint 复用 channel（与 registry/llm 一致）。

### F16. SDK 状态映射 fail-open ✅ 已修复（2026-06-11）

**修复**：`_STATUS.get(res.status, _STATUS_DEFAULT)` 默认 3（FAILED），fail-closed。

### F17. executor gather 异常分支丢 step_id ✅ 已修复（2026-06-11）

**修复**：用 `zip(runnable, results)` 还原 step_id，异常/非 StepResult 结果都能关联到正确的步骤。

### F18. 幂等 Seen/Mark 非原子 ⬜ 未修复

**证据**：`gateway/cloud/main.go:104` 先 `Seen` 后 `Mark`（TOCTOU）。
**修复方案**：合并为 `MarkIfNew`（Redis SETNX / 内存版加锁）。

### F19. CircuitBreaker 与 observability 零接线 ⬜ 未修复

**证据**：`circuit.py` 与 `observability/` 都有实现+测试，但没有任何服务 import/使用（`main.py:29` 直接用裸 `clients.call_agent`；全仓库无 `setup_tracing` 调用）。
**修复方案**：DagExecutor 的 call 路径包熔断（按 endpoint）；各服务 main 里 `setup_structured_logging()` + trace_id 经 `HandleRequest.meta`/`ExecuteRequest.meta` 贯穿。或者明确决定 Phase 2 再接、从「已完成」叙事中移除。

### F23. payment-gateway 的 Capture 链路天生不可达 ✅ proto 已修复（2026-06-11），SDK 接线待做

**已修复**：`AuthorizeResponse` proto 增加 `string confirm_token = 4`，codegen 通过。store.py 的 capture 现在有了 token 来源。

**待做**：SDK 加 PaymentClient → food/parking 的 confirmed 分支从 provider 直付切到 Authorize/Capture。

## P3 — 文档/配置漂移

### F20. CLAUDE.md §3 目录表与实际不符 ✅ 已修复（2026-06-11）

**修复**：补充 security/、payment-gateway/、observability/、gen/；vehicle-abstraction 标注为 Phase 2 规划，当前由 orchestrator/edge/val.py 模拟。

### F21. compose 与 .env.example 默认 LLM provider 不一致 ✅ 已修复（2026-06-11）

**修复**：compose 默认改为 xiaomimimo + mimo-v2.5-pro（与 .env.example/README 一致，已验证可用）。

### F22. 旧版 `orchestrator/cloud/planner.py` 是带坑的死代码 ✅ 已标注（2026-06-11）

**修复**：文件头标注「不可运行，call_agent 签名不匹配，一跑就 TypeError」。删除需经用户确认（CLAUDE.md 红线）。

---

## 建议执行顺序

1. ~~F1 确认闭环~~（已完成）
2. F6+F7+F8+F9（验证体系，半天内可完成，先做——没有可信回归，其余修复无保障）
3. F5 接线（端侧链路组网，e2e 的前提）
4. F2 权限接线 + F4 计划校验（安全承诺）
5. F3+F12+F23（proto 增字段，同批做，跑 codegen）
6. F13–F19（代码缺陷，独立可并行）
7. F11+F20+F21+F22（文档收尾，最后统一）

## 修复日志

| 日期 | 项 | 修复人 | 说明 |
|---|---|---|---|
| 2026-06-11 | F1 | Claude (review session) | 确认闭环全链路打通：engine resume 种子化+单步 confirmed meta+取消/过期/换话题分支+语音短肯定兜底；executor done 种子+meta 透传；clients.call_agent 加 meta；HMI 确认/取消按钮+follow_up 渲染；edge gateway 透传 is_confirmation（**Go 未本地编译验证**，环境无 toolchain）；edge orchestrator 确认请求跳过快路径；food/parking 确认后真实调 provider 完成交易；e2e_ws.py 加链路4 确认用例。 |
| 2026-06-11 | F2 | Claude (review session) | 权限接线：_build_context 从 meta["granted_scopes"] 解析 granted_permissions；_filter_by_permission 改 fail-closed（空列表只放行无权限 Agent）；engine 加 _enforce_permissions 占位。执行层二次校验待 Step 增 manifest 缓存。 |
| 2026-06-11 | F3 | Claude (review session) | proto：ExecuteResponse 增加 data=7（Struct）+ missing_slots=8。codegen 通过。SDK/executor/Agent 接线待做。 |
| 2026-06-11 | F4 | Claude (review session) | 计划校验：构建 agent_id→intents 映射，按 agent 校验 intent；不匹配则丢弃 step（不替换）；去掉全局 valid_intents 引用。 |
| 2026-06-11 | F5 | Claude (review session) | 端侧链路接线修复（方案 A）：edge gateway WS handler 改调 EdgeOrchestrator.Handle（gRPC）；cloud_client.py 改走 EdgeCloudChannel bidi 协议。Go 未编译验证，人工复核。 |
| 2026-06-11 | F6 | Claude (review session) | Makefile 去掉 `|| true`，合并为单条 `python -m pytest` 纳入 agents/ + --import-mode=importlib。 |
| 2026-06-11 | F7 | Claude (review session) | 根 conftest.py 注入 PYTHONPATH；--import-mode=importlib 解决 test_agent.py 重名收集冲突；`python -m pytest` 一条命令全量通过。 |
| 2026-06-11 | F8 | Claude (review session) | manual_rag 测试断言改为校验 chunks 内容含"胎压"（source 是章节名，不含关键词是正确行为）。2/2 passed。 |
| 2026-06-11 | F9 | Claude (review session) | CI Unit tests 改为单条 pytest 纳入 agents/，去掉手工 PYTHONPATH。 |
| 2026-06-11 | F11 | Claude (review session) | 测试数字口径统一：AGENTS.md 不写具体数字，写「全绿」；CLAUDE.md §7 指向修复文档。 |
| 2026-06-11 | F12 | Claude (review session) | proto：ExecuteResponse 增加 missing_slots=8。codegen 通过。engine wait_slot 分支待做。 |
| 2026-06-11 | F14 | Claude (review session) | edge _dispatch_cloud_actions：payload 用 AsMap()；VAL 结果/拒绝真正替换 speech。 |
| 2026-06-11 | F15 | Claude (review session) | clients.call_agent 按 endpoint 复用 channel（之前每次新建泄漏）。 |
| 2026-06-11 | F16 | Claude (review session) | SDK status 映射默认改 3（FAILED），fail-closed。 |
| 2026-06-11 | F17 | Claude (review session) | executor zip(runnable, results) 还原 step_id，异常分支不再丢步骤。 |
| 2026-06-11 | F20 | Claude (review session) | CLAUDE.md §3 目录表补充 security/、payment-gateway/、observability/、gen/。 |
| 2026-06-11 | F21 | Claude (review session) | compose 默认 LLM 改为 xiaomimimo + mimo-v2.5-pro（与 .env.example 一致）。 |
| 2026-06-11 | F22 | Claude (review session) | planner.py 标注「不可运行，签名不匹配」。删除需经用户确认。 |
| 2026-06-11 | F23 | Claude (review session) | proto：AuthorizeResponse 增加 confirm_token=4。codegen 通过。SDK PaymentClient 接线待做。 |
| 2026-06-11 | F24 | Claude (review session) | cloud-planner 容器启动修复：包内相对 import 统一 + Dockerfile（WORKDIR /app、COPY security、`python -m orchestrator.cloud.main`）。 |
| 2026-06-11 | F10(部分) | Claude (review session) | parking_payment 补 3 个契约测试（含确认支付闭环）。trip_planner 仍缺。 |
| 2026-06-11 | 验证 | Claude (review session) | 全量 114 passed + smoke 13/13，一条 `python -m pytest` 命令全绿，不需要手工 PYTHONPATH。 |
