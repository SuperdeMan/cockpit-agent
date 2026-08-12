# 麦当劳 / 瑞幸 MCP 全业务流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户从自然语言完成麦当劳与瑞幸的真实选店、选品、预览、确认、创建未支付订单、支付入口展示和查单，并让瑞幸完成确认取消；不执行最终付款。

**Architecture:** 保持 `Intent.slots=map<string,string>`，只向 Planner 暴露复合商户 intent。新增 `MerchantWorkflow` 在 mcp-bridge 内编排官方低层工具，使用 Redis TTL 快照和 opaque `checkout_token`；候选卡可以携带 token，标准确认恢复则按可信的用户、会话与商户 current pointer 原子消费，不依赖客户端回传 token。所有商品 code、嵌套请求、金额、成功判定与结果脱敏都由确定性 codec 完成。真实创建采用 `local_at_most_once`，Task Ledger + single-flight 防本地重复，HTTP 写请求不自动重放，超时只标 `uncertain`。

**Tech Stack:** Python 3.11、asyncio、gRPC Agent SDK、MCP Streamable HTTP、Redis、PostgreSQL Task Ledger、PyYAML、pytest、React/TypeScript、Vite、Node test、Docker Compose、CDP。

> **本轮授权已取得（2026-08-12）：** 用户明确要求直接在 `main` 实施，并授权本计划范围内的 commit、push、真栈、真实官方 MCP 写调用和浏览器验证。真实取证最多创建三笔未支付订单：瑞幸 locator discovery 1 笔、瑞幸浏览器整链 1 笔、麦当劳浏览器整链 1 笔；两笔瑞幸都在验收内取消，麦当劳不付款等待自动失效。若同一瑞幸订单可以同时完成 discovery 与 HMI 证据则减少为两笔总数。授权不外溢到 `.env`、密钥、数据库 schema/迁移、数据删除或 git 历史改写。
>
> 设计权威：`docs/design/2026-08-12-merchant-mcp-full-flow.md`。实施期间不修改本计划 checkbox；进度记录在任务计划中。

---

## 文件结构

新增：

- `agents/mcp_bridge/src/merchant/models.py`：商户工作流公共 dataclass、金额与结果白名单。
- `agents/mcp_bridge/src/merchant/drafts.py`：Redis TTL 草稿、会话隔离和 single-flight。
- `agents/mcp_bridge/src/merchant/base.py`：工作流基类、工具调用、候选/确认/付款卡公共逻辑。
- `agents/mcp_bridge/src/merchant/mcdonalds.py`：麦当劳工具链与 codec。
- `agents/mcp_bridge/src/merchant/luckin.py`：瑞幸工具链与 codec。
- `agents/mcp_bridge/tests/test_merchant_*.py`：按公共层、麦当劳、瑞幸拆分测试。
- `hmi/src/merchantActions.mjs` / `.test.mjs`：商户卡按钮到上行语义的纯函数。
- `test/e2e_merchant_mcp.py`：独立于 demo 的 opt-in 真实商户旅程。
- `test/test_e2e_merchant_mcp.py`：runner 开关、脱敏输出和 mock-safe 全链单测。

修改：

- `agents/mcp_bridge/src/{mcp_client,admission,agent}.py`、`servers.yaml`、`requirements.txt`。
- `deploy/docker-compose.yaml`：给 mcp-bridge 注入既有 `REDIS_URL` 并等待 Redis healthy。
- `hmi/src/components/{Cards,ChatView}.tsx`、`hmi/src/cards.css`；现有 `App.tsx` / WebSocket / gateway / proto 的普通按钮 `false`、确认按钮 `true` 上行链直接复用。
- `skills/exemplars/{mcd,luckin}.yaml`、必要的 guide、对抗语料与规模守卫。
- `docs/conventions.md`、架构/实现计划入口、支付设计实施记录、`docs/agents-history.md`。

---

### Task 1：MCP 结果、传输、准入与第三方隐私硬化

**Files:**

- Modify: `agents/mcp_bridge/src/mcp_client.py`
- Modify: `agents/mcp_bridge/src/admission.py`
- Modify: `agents/mcp_bridge/src/agent.py`
- Modify: `agents/mcp_bridge/servers.yaml`
- Test: `agents/mcp_bridge/tests/test_http_client.py`
- Test: `agents/mcp_bridge/tests/test_admission_http.py`
- Test: `agents/mcp_bridge/tests/test_bridge.py`
- Test: `agents/mcp_bridge/tests/test_payurl_register.py`

- [ ] 写 RED：
  - `content[0].text` 为完整 JSON object 且 `structuredContent` 为空时解析为 `data`；数组、标量、前后混杂文案、重复 JSON key 不解析。
  - `httpx.TimeoutException` 归一成独立 `McpTimeout(sent=true|false)`，不丢失“不确定”语义。
  - `call_tool(..., retry_on_session_loss=False)` 遇 404 不重握手重放；读工具默认仍可重握手一次。
  - `admit()` 拒绝 `write=true` 但 `require_confirm=false`；`idempotency_mode=upstream` 无真实 `idempotency_key_arg` 拒绝；`local_at_most_once` 非 `retry_policy=never` 拒绝。
  - `compensate_policy=tool` 的补偿工具 schema 拒载时，创建工具二阶段同步拒载；允许明确的 `terminal` 取消。
  - 真实第三方读写均不发 `_owner_user_id`；只有 `forward_owner=true` 的 demo 发送。
  - 账号型官方工具没有网关权威 scope 时不出站：查询要求 `merchant.read`，工作流预览/创建/取消要求 `merchant.write`；客户端伪造 `granted_scopes` 已由现有 edge gateway 测试证明会被剥离。
  - 所有声明槽位/实时 schema required 都满足才出站。
  - 外部 payload 不能覆盖卡片 `type/server/tool/merchant/_prov`。
  - `pay_url_locator` 存在而 `pay_url_hosts=[]` 时拒载；支付登记失败或 host 不合法时卡片不含原始 URL。

```powershell
python -m pytest --import-mode=importlib agents/mcp_bridge/tests/test_http_client.py agents/mcp_bridge/tests/test_admission_http.py agents/mcp_bridge/tests/test_bridge.py agents/mcp_bridge/tests/test_payurl_register.py -q
```

预期：新增断言在现实现上失败，失败原因分别指向 JSON 数据为空、timeout 类型丢失、写请求发生第二次、准入未拒绝、owner 被透传或 URL 仍在卡片中。

- [ ] 最小实现：
  - 新增 `McpTimeout(McpError)`，属性 `sent: bool`。
  - `_request(..., retry_on_session_loss: bool)` 只在该参数为真时处理 404 重握手。
  - `call_tool()` 接受同名 keyword；所有写路径传 `False`。
  - 使用 `json.loads(..., object_pairs_hook=strict_object)` 解析唯一文本块，只有顶层 `dict` 才补 `data`。
  - `ToolSpec` 增加 `expose`、`forward_owner`、`required_scopes`、`idempotency_mode`、`retry_policy`；`WorkflowSpec` 同样声明 `required_scopes`；`COMPENSATE_POLICIES` 增加 `terminal`。
  - `admit()` 分“单工具 schema/字段校验”和“补偿工具最终准入校验”两阶段。
  - `_Binding` 保留实时 `input_schema`；参数 builder 校验 schema 根 `required`。
  - `_card()` 先过滤 payload 的保留键，再最后写可信元数据。
  - `_register_merchant_payment()` 将空 host 白名单和网关失败都视为“不出链接”。

- [ ] GREEN + 全桥回归：

```powershell
python -m pytest --import-mode=importlib agents/mcp_bridge/tests -q
```

预期：全部通过，且现有 demo owner 隔离、确认、幂等与 namespace admin 测试保持通过。

- [ ] 规格审查、代码质量审查通过后提交：

```powershell
git add agents/mcp_bridge/src/mcp_client.py agents/mcp_bridge/src/admission.py agents/mcp_bridge/src/agent.py agents/mcp_bridge/servers.yaml agents/mcp_bridge/tests
git commit -m "fix: 硬化真实商户 MCP 写入边界"
```

---

### Task 2：商户工作流契约、Redis 草稿和通用交互

**Files:**

- Create: `agents/mcp_bridge/src/merchant/__init__.py`
- Create: `agents/mcp_bridge/src/merchant/models.py`
- Create: `agents/mcp_bridge/src/merchant/drafts.py`
- Create: `agents/mcp_bridge/src/merchant/base.py`
- Create: `agents/mcp_bridge/tests/test_merchant_base.py`
- Modify: `agents/mcp_bridge/src/admission.py`
- Modify: `agents/mcp_bridge/src/agent.py`
- Modify: `agents/mcp_bridge/requirements.txt`
- Modify: `deploy/docker-compose.yaml`
- Modify: `orchestrator/cloud/models.py`
- Modify: `orchestrator/cloud/executor.py`
- Modify: `orchestrator/cloud/clients.py`
- Test: `orchestrator/cloud/tests/test_executor.py`
- Test: `orchestrator/cloud/tests/test_context_scopes.py`

- [ ] 写 RED，定义期望 API：

```python
draft = MerchantDraft(
    token="opaque", merchant="luckin", user_id="u1", session_id="s1",
    store={"id": "602825", "name": "门店"}, items=[{"name": "生椰拿铁", "quantity": 1}],
    amount_cents=2000, upstream_args={"deptId": 602825, "productList": []},
    schema_digest="sha", created_at=1.0,
)
await store.put(draft)
assert await store.consume("opaque", user_id="u2", session_id="s1", merchant="luckin") is None
assert await store.consume("opaque", user_id="u1", session_id="s1", merchant="mcdonalds") is None
assert (await store.consume("opaque", user_id="u1", session_id="s1", merchant="luckin")).amount_cents == 2000
assert await store.consume("opaque", user_id="u1", session_id="s1", merchant="luckin") is None
```

再覆盖 `consume_current(user_id="u1", session_id="s1", merchant="luckin")`：标准确认恢复没有 token 槽时仍原子消费当前快照；新 preview 覆盖 current pointer 后，旧 token 不得通过 current 路径执行。覆盖随机 token、TTL、用户/会话/商户隔离、一次性 consume、Redis 不可用时写能力 fail-closed、同 token 并发只允许一个调用者进入创建区。

同时写 cloud RED：`Clients._merge_meta()` 必须丢弃 preferences / step meta 中伪造的 `granted_scopes`，只从 `PlanContext.granted_permissions` 重建；空权限集合不得遗留 scope，合法 `merchant.read,merchant.write` 必须原样到达 Agent。

```powershell
python -m pytest --import-mode=importlib agents/mcp_bridge/tests/test_merchant_base.py -q
```

预期：因 merchant package 不存在而失败。

- [ ] 实现公共模型：
  - `MerchantDraft`、`MerchantItem`、`MerchantResult`、`MerchantChoice`。
  - 金额只用 `amount_cents:int`，任何 float 转换都用 `Decimal(str(value))` 并按 HALF_UP 转分。
  - `MerchantResult.ledger_ref()` 只返回 `server/order_id/status/amount_cents/merchant/store_name`。

- [ ] 实现 `RedisDraftStore`：
  - key `mcp:merchant:draft:{token}`，value 不含 token/用户明文日志。
  - current key `mcp:merchant:current:{owner_digest}:{session_digest}:{merchant}` 只指向最新 token；`SET EX` 默认 600 秒。
  - 消费使用 Lua 校验 owner/session/merchant、current pointer 与 draft 后同时删除，避免旧草稿和并发双消费。
  - 测试通过依赖注入 fake Redis；生产从 `REDIS_URL` 创建连接。

- [ ] 实现 `MerchantWorkflow` 基类：
  - 统一 `prepare(intent, ctx)`、`confirm(ctx, token="")`、`cancel(intent, ctx)` 接口；标准确认轮 token 为空时只允许 `consume_current(user, session, merchant)`。
  - 只读内部工具调用默认允许一次 session 重握手；创建/取消传 `retry_on_session_loss=False`。
  - 公共候选卡 `merchant_choices`、预览卡 `merchant_order_preview`、标准化订单卡和确定性中文摘要。
  - 支付、Ledger、业务错误、uncertain 走公共路径。
  - 账号型内部工具调用统一检查 `granted_scopes`：查询 `merchant.read`，预览/创建/取消 `merchant.write`；客户端 meta、非空默认 user_id 和声纹都不能替代网关 scope。

- [ ] 实现可信跨步引用来源：
  - `StepResult` 增加仅在编排进程内使用的 `source_intent`；每次 agent step 返回后由 Executor 用当前 `step.intent` 覆盖，不能采用 Agent 或 Planner 自报值。
  - `_resolve_slot_refs()` 将 `{slot: {ref, producer_intent}}` JSON 写入 `step.meta["_trusted_slot_refs"]`；Planner schema 不允许 `meta`，因此用户文本不能伪造。
  - 瑞幸只接受同一 `nearby.search` step、同一 `items.N` 下的 `name/lng/lat` 引用；直接槽位坐标或不同索引组合拒绝。

- [ ] 实现权威 scope 续传：
  - `Clients._merge_meta()` 先删除 preferences、Planner/step meta 中的 `granted_scopes`，再仅从 `ctx.granted_permissions` 写入排序去重后的值。
  - Agent 只信任这条 cloud 重建的 meta；默认非空 `user_id`、声纹和客户端自报 scope 均不能授权真实商户账号调用。

- [ ] 扩展 `ServerSpec.workflows` / `WorkflowSpec`，仅当 `required_tools` 全部最终准入时注册高层 capability；内部工具 `expose=false` 不进入 Registry。

- [ ] 运行 GREEN：

```powershell
python -m pytest --import-mode=importlib agents/mcp_bridge/tests/test_merchant_base.py agents/mcp_bridge/tests/test_admission_http.py agents/mcp_bridge/tests/test_bridge.py orchestrator/cloud/tests/test_executor.py orchestrator/cloud/tests/test_context_scopes.py -q
docker compose -f compose.yaml config --quiet
```

预期：测试通过，Compose 解析 exit 0；mcp-bridge 环境包含 `REDIS_URL=redis://redis:6379/0` 且 depends_on Redis healthy。

- [ ] 审查后提交：

```powershell
git add agents/mcp_bridge/src/merchant agents/mcp_bridge/src/admission.py agents/mcp_bridge/src/agent.py agents/mcp_bridge/requirements.txt deploy/docker-compose.yaml agents/mcp_bridge/tests/test_merchant_base.py orchestrator/cloud/models.py orchestrator/cloud/executor.py orchestrator/cloud/clients.py orchestrator/cloud/tests/test_executor.py orchestrator/cloud/tests/test_context_scopes.py
git commit -m "feat: 建立商户订单预览工作流"
```

---

### Task 3：麦当劳选店、菜单、计价和创建工作流

**Files:**

- Create: `agents/mcp_bridge/src/merchant/mcdonalds.py`
- Create: `agents/mcp_bridge/tests/test_merchant_mcdonalds.py`
- Modify: `agents/mcp_bridge/servers.yaml`
- Modify: `agents/mcp_bridge/src/agent.py`

- [ ] 写 RED，使用脱敏的官方响应 fixture 覆盖：
  - 常用门店/关键词门店映射 `storeCode/beCode/storeName/workStatus`；多门店返回选择而不静默取第一家。
  - `query-meals` 商品名匹配；多候选最多 3 项；打烊/无商品给可行动错误。
  - 详情中的套餐层级、`roundList`、`modification` 只从官方值构造。
  - `calculate-price` 输出决定最终 `items`、`takeWayCode`、原价/优惠/应付。
  - 第一次 `mcd.order` 只调用只读四步、create 次数 0，返回 `NEED_CONFIRM` 和预览卡。
  - 确认消费原快照，`create-order` 恰好一次；重复确认不再次调用。
  - MCP protocol ok 但业务 `success=false/code!=0` 记失败。
  - timeout 记 uncertain，不重试；麦当劳确认摘要包含“不支付会自动失效”。

```powershell
python -m pytest --import-mode=importlib agents/mcp_bridge/tests/test_merchant_mcdonalds.py -q
```

预期：因 `McDonaldsWorkflow` 不存在而失败。

- [ ] 在 `servers.yaml` 锁定内部工具：
  - `query-nearby-stores 235a50ac3f94`
  - `query-meals ccfacc629a1f`
  - `query-meal-detail 2dc343f13d94`
  - `calculate-price b25bd7d955a0`
  - `create-order 0bee66827dd1`
  - `order-list 9e2fcd061b03`
  - 保留 `query-order 6c5c0def4de0`

`create-order` 声明 `local_at_most_once + retry never + abandon_unpaid + payH5Url + m.mcd.cn`；低层工具全部 `expose=false`，只有 workflow 注册 `mcd.order`。

- [ ] 实现 McD codec 与 workflow，输入槽固定为：
  - `item_query`、`quantity`、`store_hint`、`city`、`pickup_mode`、`checkout_token`。
  - 数量限制 1..20；缺 item 只追问 `item_query`；门店歧义只追问 `store_hint`。
  - `orderType/beType/searchType` 由受审常量构造，不由 LLM 填。
  - 结果 locator 固定为菜单 code `query-meals.data.categories[*].meals[*].code`，名称/展示价按 code 关联同响应的 `query-meals.data.meals[code]`；详情是 `query-meal-detail.data` 直对象；试算金额 `calculate-price.data.price`（分）、取餐 `calculate-price.data.takeWayList[*].code`、订单 `create-order.data.orderId`、支付 `create-order.data.payH5Url`；`create-order.data.orderDetail.realTotalAmount` 仅在真实 create 与试算金额对账后作为金额回退。所有麦当劳响应还须满足 `success=true && code=200`。

- [ ] GREEN：

```powershell
python -m pytest --import-mode=importlib agents/mcp_bridge/tests/test_merchant_mcdonalds.py agents/mcp_bridge/tests -q
```

- [ ] 审查后提交：

```powershell
git add agents/mcp_bridge/src/merchant/mcdonalds.py agents/mcp_bridge/src/agent.py agents/mcp_bridge/servers.yaml agents/mcp_bridge/tests/test_merchant_mcdonalds.py
git commit -m "feat: 打通麦当劳订单工作流"
```

---

### Task 4：瑞幸门店、规格、预览、创建与取消工作流

**Files:**

- Create: `agents/mcp_bridge/src/merchant/luckin.py`
- Create: `agents/mcp_bridge/tests/test_merchant_luckin.py`
- Modify: `agents/mcp_bridge/servers.yaml`
- Modify: `agents/mcp_bridge/src/agent.py`

- [ ] 写 RED，fixture 逐字采用 2026-08-12 只读真机结构：
  - `queryShopList $.data[]` 的 `deptId/deptName/longitude/latitude/workStatus`。
  - `searchProductForMcp` 多候选；`queryProductDetailInfo` 完整属性树。
  - “热/少冰/半糖”等只匹配 `canSelected=1` 的官方子属性。
  - 每次 `switchProduct` 都使用上一次响应的新 `skuCode`；实测回归锁定 `SP2077-01134 -> SP2077-01090`。
  - `previewOrder` 从 `shopInfo`、`productInfoList[].additionDesc`、`totalInitialPrice/privilegeMoney/discountPrice` 生成卡片。
  - 首轮 create=0；确认后 create=1；创建结果标准化后可供 status 回填。
  - `luckin.order_cancel` 无订单号时只回填同商户最近确定订单，先返回确认；确认后 cancel=1。
  - 打烊、售罄、不支持规格、草稿过期、timeout、业务 code 错误和跨商户引用全部 fail-closed。

```powershell
python -m pytest --import-mode=importlib agents/mcp_bridge/tests/test_merchant_luckin.py -q
```

预期：因 `LuckinWorkflow` 不存在而失败。

- [ ] 在 `servers.yaml` 锁定内部工具：
  - `queryShopList cee4310bfb4d`
  - `searchProductForMcp c4426494a931`
  - `switchProduct f2bb3c6177ff`
  - `queryProductDetailInfo dfb46188253d`
  - `previewOrder 6ef3aceeab1f`
  - `createOrder 6e7211974147`
  - `queryOrderDetailInfo 095c2b916b52`
  - `cancelOrder 095c2b916b52`

后二者 fresh `tools/list` 已分别核对；指纹相同是因为 fingerprint 只计算 inputSchema，而两者输入 schema 都是同一个 `required orderId:string + additionalProperties:false`，不是复制工具名或响应 schema。

`createOrder` 声明 `local_at_most_once + retry never + compensate tool=cancelOrder`；`cancelOrder` 声明 `terminal`。创建与取消低层工具均 `expose=false`，高层 capability 为 `luckin.order` / `luckin.order_cancel`。

- [ ] 实现 Luckin codec，输入槽固定为：
  - `item_query`、`quantity`、`store_name`、`store_longitude`、`store_latitude`、`temperature`、`ice`、`sweetness`、`milk`、`checkout_token`、`order_id`。
  - 经纬度只接受 `_trusted_slot_refs` 证明来自同一 `nearby.search.data.items.N` 的门店名/经纬度；单独由 Planner 填的坐标拒绝。
  - 创建请求经预览映射 `deptId/productList/couponCodeList`，longitude/latitude 取已选择的官方门店公开坐标。

- [ ] GREEN：

```powershell
python -m pytest --import-mode=importlib agents/mcp_bridge/tests/test_merchant_luckin.py agents/mcp_bridge/tests -q
```

- [ ] 审查后提交：

```powershell
git add agents/mcp_bridge/src/merchant/luckin.py agents/mcp_bridge/src/agent.py agents/mcp_bridge/servers.yaml agents/mcp_bridge/tests/test_merchant_luckin.py
git commit -m "feat: 打通瑞幸订单与取消工作流"
```

---

### Task 5：意图理解、跨步门店引用与能力尺子

**Files:**

- Modify: `skills/exemplars/mcd.yaml`
- Modify: `skills/exemplars/luckin.yaml`
- Create or Modify: `skills/guides/merchant-ordering.md`
- Modify: `test/eval_corpus/intent_adversarial/cases/capability_catalog.yaml`
- Modify: `test/eval_corpus/mode_routing_cases.yaml`
- Modify: relevant adversarial case YAML and `test/eval_corpus/intent_adversarial/suites.yaml`
- Modify: `orchestrator/cloud/tests/test_catalog_budget.py`
- Test: `orchestrator/cloud/tests/test_exemplars.py`
- Test: relevant planning/skill tests

- [ ] 写 RED：
  - catalog 必须出现 `mcd.order/luckin.order/luckin.order_cancel`。
  - 品牌下单不能选择 `shop.order`；营养查询不能选择 `mcd.order`；附近门店不能直接创建订单。
  - 瑞幸无门店时生成 `nearby.search -> luckin.order`，后一步 `slot_refs` 分别引用同一 `items.0.name/lng/lat`；可信来源由 Executor meta 产生，不让 LLM 填 `location_source`。
  - 每个新 active intent 至少 2 正例、2 硬负例、1 对照。
  - 保留现有“能力缺席”case id，但将当前金标翻转为已接入；不删除历史边界。

```powershell
python -m pytest --import-mode=importlib orchestrator/cloud/tests/test_exemplars.py orchestrator/cloud/tests/test_catalog_budget.py -q
python test/eval_capability_integrity.py
python scripts/check_intent_gate.py --strict
```

预期：新增 intent 在 catalog/覆盖门禁中先失败。

- [ ] 实现 exemplar/guide/语料：
  - 麦当劳下单示例明确 `item_query/store_hint/quantity/pickup_mode`。
  - 瑞幸示例明确跨步依赖，不把 current GPS 写入槽位。
  - 取消、查单、营养、附近和无品牌 demo 均有硬负例。
  - 若唯一输入超过现上界，按新增用例实数抬 `max_cases`，同时更新预算测试期望；不删 case 压数字。

- [ ] GREEN，并运行离线 planning 定向回归：

```powershell
python -m pytest --import-mode=importlib orchestrator/cloud/tests -q
python test/eval_capability_integrity.py
python scripts/check_intent_gate.py --strict
```

- [ ] 审查后提交：

```powershell
git add skills/exemplars skills/guides test/eval_corpus orchestrator/cloud/tests/test_catalog_budget.py
git commit -m "feat: 补齐真实商户下单意图边界"
```

---

### Task 6：HMI 商户候选、订单预览、确认和订单动作

**Files:**

- Create: `hmi/src/merchantActions.mjs`
- Create: `hmi/src/merchantActions.test.mjs`
- Modify: `hmi/src/components/Cards.tsx`
- Modify: `hmi/src/components/ChatView.tsx`
- Modify: `hmi/src/cards.css`
- Modify: `hmi/src/types.ts`
- Test: existing HMI Node tests

- [ ] 写 RED 的纯函数测试：

```javascript
assert.equal(actionFor({ kind: 'cancel_luckin', orderId: 'L1' }).text, '取消瑞幸订单 L1')
assert.equal(actionFor({ kind: 'choose_store', merchant: '瑞幸', name: '迪美店' }).text,
  '选择瑞幸门店：迪美店')
```

同时锁定：麦当劳只有“放弃支付”，瑞幸才有“取消订单”；无 `qr_svg` 时 CTA 是“打开安全支付链接”而非“扫码支付”。

```powershell
node --test hmi/src/merchantActions.test.mjs
```

预期：模块不存在而失败。

- [ ] 实现卡片和动作：
  - `CardRenderer` 将 `onAction` 传给 `merchant_choices/merchant_order_preview/payment_qr/mcp_order`；预览卡自身不生成“确认”按钮。
  - 预览卡使用现有 Aurora 变量做收据式层级：品牌/门店、商品规格、价格分隔；全局 `ConfirmBubble` 提供确认/取消主次按钮，触控目标至少 44px，金额和订单号可读。
  - `ChatView` 根据 `ui_card.type` 选择商户确认文案，不再显示“已泊车 · 危险操作”。
  - 现有 `App.send -> dispatch(text,false)` 与 `App.confirm -> dispatch(reply,true)` 原样复用，不修改 `App.tsx`，不新增旁路写接口。
  - 外链按钮只消费后端已经白名单化的 `pay_url/qr_content`。

- [ ] GREEN + 构建：

```powershell
npm --prefix hmi test
npm --prefix hmi run build
```

预期：Node 全部通过，Vite build exit 0，无 TypeScript 错误。

- [ ] 审查后提交：

```powershell
git add hmi/src
git commit -m "feat: 完善真实商户订单交互"
```

---

### Task 7：离线全链、真实响应 locator、真栈和浏览器取证

**Files:**

- Create: `test/e2e_merchant_mcp.py`
- Create: `test/test_e2e_merchant_mcp.py`
- Modify: `test/e2e_manifest.yaml`
- Modify: `test/README.md`
- Modify: `agents/mcp_bridge/servers.yaml`（仅用真实创建响应确定瑞幸 locator/host）
- Modify: merchant tests with sanitized create/query/cancel fixtures
- Modify: `test/hmi_cdp/run_cases.mjs` or add merchant case module
- Create: ignored evidence under `docs/reviews/eval/_ci-run-*`

- [ ] 写离线 E2E RED：用 fake MCP clients 贯通两家完整旅程，断言确认前 write=0、确认后 create=1、重复帧仍 1、status 同 order_id、瑞幸 cancel 二次确认且跨商户不回填。

```powershell
python -m pytest --import-mode=importlib test/test_e2e_merchant_mcp.py -q
```

预期：独立 runner/fixture 未实现而失败。

- [ ] 实现 `test/e2e_merchant_mcp.py`：
  - 默认只跑 mock-safe deterministic 旅程。
  - `--live-readonly` 只做 initialize/tools/list 与预览。
  - `--live-create-unpaid --acknowledge-real-orders` 才允许创建真实未支付单；缺双开关 exit 2。
  - 输出结构化 case result，绝不打印 token、完整 pay URL、优惠券或原始商户响应。

- [ ] 在营业时间运行真实瑞幸创建探针：
  1. 用已验证的公开门店坐标、商品、switch 后 SKU 和 preview 输出构造 `createOrder`。
  2. 仅记录响应字段路径、类型、业务 code、订单号 locator、金额 locator、支付 URL hostname；不记录 URL query/path。
  3. 把脱敏 fixture 写入测试，锁定准确 `result_map/pay_url_locator/pay_url_hosts`。
  4. 用真实 orderId 调 `queryOrderDetailInfo` 并调 `cancelOrder` 清理 discovery 订单，验证取消终态。
  5. locator/host 锁入配置并重建后，再由浏览器整链创建第二笔瑞幸订单，展示真实支付卡、查单并经 HMI 二次确认取消；不得用 discovery 订单的静态卡替代浏览器确认→创建证据。

- [ ] 在营业时间运行真实麦当劳：选店 -> 菜单 -> 详情 -> 计价 -> 浏览器确认 -> create-order -> 付款卡 -> query-order；不付款。若账户常用门店关闭，选择官方返回的营业门店，不伪造门店。

- [ ] 使用根 Compose 重建相关服务（不改 `.env`）：

```powershell
docker compose -f compose.yaml up -d --build mcp-bridge payment-gateway registry cloud-planner cloud-gateway edge-orchestrator edge-gateway hmi
python -u test/e2e_merchant_mcp.py --live-readonly
python -u test/e2e_merchant_mcp.py --live-create-unpaid --acknowledge-real-orders --max-real-orders 3
```

运行真实创建时，用当前 PowerShell 进程临时设置 `PAYMENT_EXTERNAL_PAY_HOSTS` 后重建 payment-gateway；不写根 `.env`。

- [ ] CDP 浏览器验收：
  - 截图门店/商品候选、预览、确认、支付卡、查单、瑞幸取消。
  - 抓确认上行帧 `is_confirmation=true`。
  - 麦当劳/瑞幸卡片无“已泊车”文案；无 SVG 时不写“扫码”。
  - 订单号、金额、品牌一致，两个商户不串单。

- [ ] 真实链失败时不调整测试绕过；记录具体外部前提。真实链全绿后提交 locator、fixtures 和 E2E：

```powershell
git add agents/mcp_bridge/servers.yaml agents/mcp_bridge/tests test/e2e_merchant_mcp.py test/e2e_manifest.yaml test/README.md test/hmi_cdp
git commit -m "test: 锁定真实商户端到端订单旅程"
```

---

### Task 8：契约、架构、全量回归、最终审查与推送

**Files:**

- Modify: `docs/conventions.md`
- Modify: `docs/architecture/cockpit-agent-architecture.md`
- Modify: `docs/architecture/phase1-implementation-plan.md`
- Modify: `docs/design/2026-08-11-payment-infrastructure-and-merchant-mcp.md`
- Modify: `docs/design/2026-08-12-merchant-mcp-full-flow.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md` only if its current-state snapshot requires synchronization
- Modify: `docs/agents-history.md`

- [ ] 更新文档：
  - §9.9 记录内部工具/复合 workflow、`local_at_most_once`、写 no-retry、timeout uncertain、两阶段补偿准入、owner 不出域、JSON-in-text 和 result_map。
  - §9.17 记录空 host fail-closed、登记失败不回退原链接、商户支付入口不代表已支付。
  - 架构/frontdoor/当前状态同步能力与明确限制：单商户账号、无最终付款、麦当劳无取消。
  - 支付设计文档补本批逐步实测证据；history 只追加事实，不复制设计全文。

- [ ] 运行新功能定向验证：

```powershell
python -m pytest --import-mode=importlib agents/mcp_bridge/tests orchestrator/cloud/tests -q
npm --prefix hmi test
npm --prefix hmi run build
python test/eval_capability_integrity.py
python scripts/check_intent_gate.py --strict
python scripts/run_e2e.py --check
```

- [ ] 运行完整项目基线并保存 exit code/计数：

```powershell
python -m pytest --import-mode=importlib
```

不得用 `pytest test/` 代替根基线，不得设置 `PYTHONIOENCODING=utf-8`。

- [ ] 最终规格审查逐条对照用户目标与设计 §1/§9；最终代码质量审查检查从本计划基准 SHA 到 HEAD 的全部 diff。所有 Critical/Important 必须修复并复审。

- [ ] 验证工作树只含计划内提交、无 token/URL/证据垃圾：

```powershell
git status --short --branch
git log --oneline --decorate -10
git diff origin/main...HEAD --check
git grep -n -I -E "Bearer [A-Za-z0-9._-]{12,}|https://[^ ]+\?(token|code|sign)=" -- . ':!docs/superpowers/plans/2026-08-12-merchant-mcp-full-flow.md'
```

- [ ] 最终提交文档并直接推送 main：

```powershell
git add AGENTS.md CLAUDE.md docs
git commit -m "docs: 收口真实商户 MCP 全流程证据"
git push origin main
```

只有 push 成功、`origin/main` 指向最终 HEAD、真实两家旅程和浏览器证据都成立，才能宣告目标完成。

---

## 计划自审

- 规格覆盖：设计 §1 的两家业务链、HMI、意图与写安全分别由 Task 1-7 覆盖；Task 8 负责真相源与最终证据。
- 无范围替代：真实创建、查单、瑞幸取消和浏览器确认均为硬验收，不允许用 mock 或只读探针替代。
- 类型一致：Planner 仅传字符串；嵌套参数始终保存在 `MerchantDraft.upstream_args: dict`；金额统一 `amount_cents:int`；候选卡可携带 `checkout_token:str`，标准确认恢复不依赖客户端 token，只原子消费可信 current draft。
- 外部未知被限定为证据闸：瑞幸创建响应 locator/host 必须由获准真实未支付单取得；在此之前不猜字段、不放宽 host。
- 红线：不改 `.env`、不改数据库 schema、不删除文件、不重写 git 历史。

---

## 实施记录（2026-08-12）

> 按本计划头部约定，执行期没有回填 checkbox。本节只登记完成面、真实偏差和最终收口前仍需
> 补的证据；最终测试数字以实际命令退出码为准。

- Task 1–6 的实现面已完成：MCP 严格解析/准入/写安全、Redis 草稿与复合 workflow、麦当劳/
  瑞幸确定性 codec、HMI 卡与动作、能力/意图资产均已落地。两家业务都到创建未支付订单、
  展示受控支付入口与查单；瑞幸支持再次确认取消，麦当劳官方无远程取消；不执行最终付款。
- 官方 `initialize + tools/list` 与只读预览已在真栈复核；浏览器已有麦当劳支付链接卡和瑞幸
  订单预览证据。
- 真实创建不是原定最多 3 笔，而是 5 笔（瑞幸 3、麦当劳 2）。增加发生在官方 create
  locator/host 契约发现与浏览器确定性拒绝/续接排障；三笔瑞幸均已取消，两笔麦当劳均由商户
  自动取消，无最终付款。这个偏差不回写头部授权预算，保留原计划与实际之间的差异。
- 旧版 C9 的“收到回复即绿”无法证明状态语义，且曾把官方明确终态重述为“没查到/待回传”；
  这些旧截图不计入最终验收。收紧后的 C9 已分别精确命中瑞幸“已取消”和麦当劳
  “订单已取消”，且查询帧均为 `is_confirmation=false`。
- Task 8 已完成冻结工作树验证：根全量 **5408 passed / 14 skipped / 0 failed**（退出码 0，
  23m25s）；mcp-bridge **385 passed**、隐私/旅程 manifest **168 passed**、HMI node
  **253/253** 且 production build 通过。最终审查补齐真实订单号移除、显式门店两步可信 POI、
  履约态按钮、认证 scope 运行手册和 `merchant_draft` 隐私库存/owner 删除路径。商户确认操作
  在飞时删除返回 pending，租约释放后重试并证明短期状态清零才 ACK；这不是全 registry saga。提交与 push
  只以 Git 历史为证，本段不预写运行状态。
- 合成 Docker 真栈在不调用商户业务工具的前提下复验该删除契约：活跃租约阶段 HTTP 为
  `503 + pending/retryable`，精确释放后同 owner 重试为 200，最终会话/草稿为零且旧租约不可授权。
- PoC 边界保持：商户 token/账号是服务级全局凭证，payment host 依赖运行时安全配置；不做
  多乘员独立商户账号、token 自动刷新或最终付款。
