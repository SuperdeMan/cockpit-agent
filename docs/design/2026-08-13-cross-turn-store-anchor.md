# 跨轮门店锚定（真实商户）

- **状态**：✅ 已实施（2026-08-13，泓舟拍板「直接开做」）
- **交付对象**：编排层（`orchestrator/cloud/executor.py` + `context.py`）与 mcp-bridge 商户 workflow
- **关联**：`agents/mcp_bridge/src/merchant/luckin.py::_trusted_store`、
  `orchestrator/cloud/executor.py::_resolve_slot_refs`、`docs/conventions.md` §9.9、
  会话 `demo-2goetq`（2026-08-12）

---

## 1. 现状与证据

真实商户下单/看菜单要求一个**可信门店三元组**（`store_name` / `store_longitude` /
`store_latitude`）。可信的定义是确定性的，不是约定俗成的：

- 生产者：`executor._resolve_slot_refs` 在解析 `slot_refs` 时重建 provenance，写进
  `step.meta["_trusted_slot_refs"]`（`executor.py:509`）。
- 消费者：`LuckinWorkflow._trusted_store` 要求三个槽位**全部**来自
  `producer_intent == "nearby.search"`，且 ref 路径的
  `<step>.data.items.<N>` 前缀**必须是同一个**（`luckin.py` 约 1200 行起）。
- 每一轮开始时 `step.meta.pop("_trusted_slot_refs", None)`（`executor.py:434`），
  注释写明「任何陈旧/伪造值先丢弃」。

**所以跨轮锚定不是没做，是被显式排除的。** 这条不变量挡住两类事故：

1. LLM 或客户端凭空编一组坐标 → 拿座舱 GPS 或臆造坐标去商户建单；
2. name/lng/lat 从**不同**候选拼接 → 在 A 店的名字下用 B 店的坐标下单。

### 实测（2026-08-13，真栈，本机）

| 说法 | 结果 |
|---|---|
| 单轮组合「先查附近的瑞幸，再点一杯冰美式」 | 计划内 `nearby.search → luckin.order` 带 `slot_refs`，**可信链成立** |
| 两轮：先「帮我查一下附近的瑞幸咖啡」，再「在最近那家帮我点一杯冰美式」 | 第二轮回 `请先查询附近的瑞幸门店并选择一家` |

第二行就是用户在 `demo-2goetq` 里遇到的形态：门店明明刚看过，下一句却要重查。
`9899486a8773d577`（「这家店的菜单帮我看看」答出演示商户数据）是同一个洞的另一面——
`luckin.menu` 已于 2026-08-13 补上，但它同样绑 `deptId`，同样需要门店锚定。

## 2. 问题

**用户心智里的「这家店」是跨轮的，系统的可信链是单轮的。** 两者之间没有桥，
于是每一轮都要用户重新把门店说一遍——而用户不会这么说话。

## 3. 目标

让「这家店 / 最近那家 / 刚才那家」在**下一轮**仍能解析成门店，且
**不放松第 1 节那条不变量的任何一条**：坐标只能来自服务端持有的、由
`nearby.search` 真实产出的某一条公开 POI；客户端与 LLM 都不得供给或影响该值。

## 4. 方案

### 4.1 核心判据

> **跨轮延续的是「服务端记得上一轮取回了哪些门店」，不是「让模型把坐标再说一遍」。**

差别是全部：前者只把 provenance 的**时间窗**从一轮放宽到一个会话；后者是把
provenance 交给不可信输入，等于取消它。

### 4.2 落点

复用既有焦点态（`context.update_focus(session_id, plan, results)`，服务端 Redis，
客户端不可写），新增一格 `focus.last_places`：

```
last_places = {
  producer_intent: "nearby.search",
  fetched_at: <ts>,
  session_owner: <user 摘要>,
  items: [ {name, lng, lat}, ... ]      # 原样取自该轮 nearby.search 的 data.items
}
```

- 只在 `nearby.search` 步骤 OK 时写入；其他意图不写。
- TTL 与焦点态一致（过期即失效，不续期——「刚才那家」本来就有时效）。
- **只存 name/lng/lat 三个标量**，与 §9.9 里「挂起步只保留下游实际引用且安全的标量」
  同一口径；不存卡片、话术、POI id 之外的任何东西。

### 4.3 消费

`executor._resolve_slot_refs` 末尾追加一条**兜底**（顺序很重要：本轮 plan 内的
`slot_refs` 优先，只有本轮没有生产者时才看 focus）：

1. 本步 intent 属于声明了门店槽的商户 workflow，且三个门店槽在本轮**没有**被
   plan 内 `slot_refs` 填上；
2. `focus.last_places` 存在、未过期、`session_owner` 与本轮认证 owner 一致；
3. 从 `items` 里按用户话里的序数/店名线索选一条（无线索则取第 0 条——与今天
   plan 内 `items.0` 的行为一致，不新增歧义）；
4. 用该条的三个值**覆盖**槽位，并写入与 plan 内路径同构的 provenance：
   `_trusted_slot_refs = {store_name: {ref: "focus.last_places.items.<N>.name",
   producer_intent: "nearby.search"}, ...}`。

消费侧 `_trusted_store` 的正则要同步放行 `focus.` 前缀，**且仍然强制三个槽位同前缀
同下标**——这条一个字都不能松。

### 4.4 明确不做

- ❌ 不接受计划里出现的字面坐标（今天也不接受，本方案不改这条）。
- ❌ 不把座舱 GPS 当门店坐标兜底。
- ❌ 不跨 session、不跨 owner 复用（多乘员隔离，见 M-B 那批的判据）。
- ❌ focus 里不存 `deptId`——官方门店 id 每次仍由 `queryShopList` 现查，
  避免把商户内部 id 缓存成事实。

## 5. 分阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | `focus.last_places` 写入 + 单测 | ✅ 只认 `nearby.search`、只留三标量；坏数据整条丢弃；「只有门店列表」的焦点也落盘 |
| P1 | executor 兜底解析 + provenance 同构写入 | ✅ 5 条断言：补槽与同构 provenance／本轮生产者优先／不覆盖用户本轮门店／**声明了 ref 却没解析成功不许被补**／半条门店整条丢 |
| P2 | 消费侧正则放行 + 同前缀同下标强断言 | ✅ 5 组混拼构造反例全部被拒（换下标／混两种来源／生产者不是 nearby／缺下标／换容器），且**拒绝在碰商户接口之前落定** |
| P3 | 对抗语料补两轮上下文用例 | ⬜ 未做（`context_state.yaml` 的 route_flip 对）|

### 实施差异（与原方案的两处偏离，都记在这里）

1. **落点不是新写一条通路，而是接既有的那条。** 引擎里 `_apply_focus_meta` 已经确立了
   「用系统持有的会话焦点补全 Planner 省略的结构化上下文」这条既有做法，本批只是把
   门店三元组加进同一条路——但 provenance 走 `PlanContext.focus_places` 而不是
   `step.meta`：后者会被 `_resolve_slot_refs` 每轮 pop 掉（那个 pop 本身是对的，
   它挡的是计划里的伪造值），而 `PlanContext` 是服务端对象，LLM 与客户端都写不到。
2. **TTL 与 owner 校验沿用焦点态既有机制**，没有为门店单独造一套——
   `save_focus/load_focus` 已经带 `owner_user_id`，再加一层只会多一处要同步的地方。

## 6. 风险

1. **最大风险是把不变量修松了而测试还是绿的。** P2 的「混拼必须被拒」是这一条的
   兑现物，必须是**构造出来的反例**，不能只测正路。
2. 「最近那家」的序数解析可能与既有 `ordinalSelectIn` 语义冲突（见
   `nearby-discovery-redesign` 那批的「第N个」多套语义）——P1 先只支持
   「无线索取第 0 条」与显式店名匹配，序数留到 P3 再谈。
3. focus TTL 过期后的话术要诚实：应回到「请先查询附近的瑞幸门店」，
   不能假装记得。

## 7. 备选（已否）

- **让 planner 把门店坐标写进 slots**：等于把 provenance 交给 LLM，第 1 节两类事故
  全部回来。否。
- **在 mcp-bridge 内部缓存上次门店**：桥不知道 owner/session 的权威边界，且会绕过
  编排层的 provenance 重建——把安全判定放到消费方内部，正是 B1 那笔账的形状。否。
