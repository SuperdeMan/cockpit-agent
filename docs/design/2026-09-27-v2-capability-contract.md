# CA2-05：能力契约、兼容准入与调用版本

> 实施中，基于 `b05d9d18`。沿既定 PR-E 推进；提交/推送/必要部署已有授权。
> 不改变数据库 schema、运行配置或已有确认权威。发布和验收按独立 SHA 登记。

## 1. 当前问题

云侧 YAML 只有 53 项，完整目录还包括 MCP 受控合成、端侧生成和确定性工具，共 156 项。
旧 effect=write 同时表示研究/行程规划、用户资源写和外部交易；缺省还依赖运行结果启发式。
字段存在、能力当前可用、请求已获授权是三件事，不能相互代替。

## 2. 首版边界

- Capability 字段 12 增加受 `runtime.capability_contract` 严格校验的 Struct，version=2。
  保留旧 effect/slots/verification wire；不改描述、示例和规划目录呈现，不额外增加模型调用。
- effect 分 read / information_task / state_change / external_write。read 不等于零缓存、零日志或免费调用。
  信息任务包括为回答构建计划/报告；确认、取消与旧读写判据继续沿现有入口执行。
- parameters 描述现有 map<string,string> 的类型、单位、区域与可选数值界限；不隐式换算单位，
  不替 Agent 补槽。旧自然语言槽按实际 string 形态登记，不冒充已完成物理量归一。
- applicability 的 unspecified 与 not_vehicle_specific 明确分开；车型/软件列表是声明，不是身份或在线证明。
  带适配限制的写能力在可信车辆上下文落地前拒绝执行，不从客户端 meta 猜测车型。
- preconditions 只引用 permission/confirmation/handler/val 既有权威；idempotency 只声明已知语义，
  unknown 不改成保证，元数据不启用新重试。verification 引用既有 Verification，避免维护两份期望。

## 3. 兼容与实际调用

冻结 156 项旧接口语义指纹与迁移后的契约指纹，保存在 runtime/capability_migration.json。
描述/示例不进 ABI 指纹，但仍受路由回归；权限、确认、只响应、部署、槽与验证元数据进指纹。
旧已审能力可缺新字段；未知新能力缺契约拒绝注册。legacy 额外槽策略仅开放给冻结接口，不能由新能力自报。

新字段经 SDK loader → Registry JSON 往返/重载 → Step → 挂起恢复贯通。
调用方携带契约摘要，接收方在业务 handle / stream 第一个事件之前核对当前契约和参数。
摘要只证明版本一致，不授予权限，也不代替一次确认或 operation_id。
新能力遇旧调用方拒绝；已冻结旧接口继续兼容。缺字段、损坏字段和不认识的版本分别处理，不能全部回落 legacy。

端侧从 commands.yaml / 既有解码器派生，MCP 从人工准入声明派生；不相信外部 tool 返回的效果分类。
T0 仍经本地 VAL 权威，内部调用不透传父能力的摘要冒充子能力版本。

## 4. 验证安排

1. 逐项清单、无效/未知字段、legacy 和 description-only 变化的反向用例。
2. SDK/Registry proto-JSON-proto、旧记录、新字段丢失与挂起恢复。
3. 普通、D0、T2、改派、工具、端侧和 Agent 内部协作出口；错误参数/旧版本在首次副作用前停止。
4. 现有四门禁、全量与精确发布的小集复验；不执行交易、真实车控、数据迁移。

车型绑定、信号时效、持久操作、因果验证仍分别由 CA2-06/08/10 承接；本包不能因此标记完整 v2 已验收。


## 5. 实现与离线证据

- 156 项声明首版：read 39、information_task 6、state_change 102、external_write 9。
  这是配置可声明的集合，含可能未启用的 MCP server，不等于线上健康能力数量。
- `python scripts/capability_inventory.py --check` 走生产 builder，检查所有当前生产者都有完整契约；
  CLI 拒绝覆盖冻结的 capability_migration.json。已以移除单项 contract 做反向验证，原扫描会红。
- 53 项云侧 YAML 去掉新增 contract 后与 b05d9d18 逐项相等；旧描述/示例/槽/权限没有改写。
  全量目录投影在有/无 contract 时相等，保持 Planner 输入不变。
- Registry 兼容视图在关键词打分/top-k、语义 MAX/LIMIT 之前过滤；只读 SQL 加条件，无 schema 变更。
- `EdgeCall.contract_query` 没有执行 intent，传过旧中继/旧节点也不能变成控制动作；Python 与 Go 均有保护。
- 前一轮家族回归 332 failed / 4580 passed / 11 skipped：319 处直接是旧属性代理虚构 contract，
  其余云侧失败随之产生，另有 3 个 MCP 新工具测试缺显式效果声明。已修为检查真实字段存在；
  显式坏字段仍拒绝。MCP 测试补符合新准入规则的声明，原业务断言未放宽。
- 修后家族回归 4917 passed / 11 skipped，134.00 s；随后补旧读取方 top-k/语义过滤边界，
  相关 114 passed，Go 网关测试通过。主线全量与精确 release 真栈尚待后续登记。

剩余边界：legacy 参数策略保留未结构化的历史槽别名；未明确物理量标未明确；
前置条件引用既有执行点，未引入新谓词语言；幂等未知不等于可重试，校验声明不等于实际验证结果。
本包未改数据库 schema、密钥、环境或 CI/CD，也未开放新商户写/支付或真实车控。
