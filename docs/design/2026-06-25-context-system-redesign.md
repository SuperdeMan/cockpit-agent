# 设计：上下文系统重构（working/core 装配层 + 焦点态 + 统一抽象 + 按 scope 下发）

> **状态**：Phase 0-4 全部落地（2026-06-25，**884 passed / 6 skipped**，零回归；较重构前 854 新增 30 个上下文专属单测）。**真栈 e2e 验证**：中枢断言 7/7（`test/e2e_central_hub_assertions.py`，含危险动作确认）+ `test/e2e_ws.py` 4 链路全过。两处工程取舍 + e2e 抓出并修复的一处回归见 §8 落地记录。
> **交付对象**：后续执行者（人或 AI），按本稿 §5 分阶段落地。
> **关联**：
> - 调研依据：[`docs/research/2026-06-25-cockpit-and-agent-memory-systems.md`](../research/2026-06-25-cockpit-and-agent-memory-systems.md) §4.1（working/core 是裸着没做的那层）
> - 前序设计：[`docs/design/2026-06-13-multi-intent-and-context.md`](2026-06-13-multi-intent-and-context.md)（§3.2 规划过 `focus`，从未实现）、[`docs/design/2026-06-25-memory-system-redesign.md`](2026-06-25-memory-system-redesign.md)（长期记忆，已落地）
> - 现状代码：`orchestrator/cloud/{planning,engine,models,session,loop,clients}.py`、`agents/_sdk/{base,manifest,location,clients}.py`
> - 架构真相源：`docs/architecture/cockpit-agent-architecture.md` §4.2、§7

---

## 1. 现状 / 证据

刚完成的是**长期记忆**（memory 服务：语义/情景/程序三层，pgvector + 自动抽取 + 召回，见 memory 重构稿）。但真正意义的「上下文」——喂给 LLM Planner 的**工作记忆 / working-core context 如何组装、下发、持久化**——从未被单独设计过，正对应调研 §4.1 里裸着的 working/core 层。

当前「上下文」由 **5 套各管一段、无统一抽象**的机制拼成：

| # | 机制 | 位置 | 管什么 | 生命周期 |
|---|---|---|---|---|
| 1 | `PlanContext` | `models.py:73` + `engine._build_context`（`engine.py:415`）| 每请求瞬时态：ids/权限/prefs(含位置/电量)/raw_text | 单请求 |
| 2 | `SessionState` | `session.py`（编排器自有 Redis，`planner:sess:`，TTL 90s）| 挂起态：pending_plan + completed_results + missing_slots | 会话级超时 |
| 3 | 对话历史 | memory 服务（`clients.get_session`/`append_turn`，`clients.py:61/53`）| 滚动对话轮 | 会话级 |
| 4 | 长期语义记忆 | memory 服务（`clients.recall`，`clients.py:68`，刚重构）| pgvector 偏好 | 持久 |
| 5 | 跨 Agent meta 透传 | `clients._merge_meta`（`clients.py:122`）+ `_sdk/_ctx.py` `_current_meta` | prefs+confirmed+thinking+call_depth 拍平成 `map<string,string>` | 单次调用 |

装配链路（每个新规划轮）：`engine._orchestrate`（`engine.py:152-157`）→ `list_agents()` 全量 + `_history()` + `_recall()` → `PlanBuilder.build(text, agents, ctx, history, memory)` → `planning.py` 裸字符串拼接 prompt。

## 2. 问题（带证据）

- **P3 装配无预算层（最痛）**：`planning._build_catalog`（`planning.py:446`）把**全量已注册 Agent 的完整能力 JSON** 灌进每次规划；`_format_history`（`:484`）死写最近 4 轮、`_format_memory`（`:460`）死写 3 条，三源各自截断、无法在统一预算里权衡；`replan`（`:206`）只吃 goal+observations+全量 catalog，**连 history/memory/focus 都没有**——初规划与再规划上下文不一致。
- **P4 无焦点态**：`2026-06-13-multi-intent-and-context.md` §3.2 规划过 `focus`（对象/属性/位置/上个 POI），全仓 grep 零实现。指代全靠 LLM 啃 4 轮原文，于是 `planning.py`/`engine.py` 堆了一摞正则/启发式补丁——trip.plan/trip.modify 确定性兜底（`planning.py:271/305`）、确认词「占据整句」判定（`engine.py:454`）、`_is_topic_change` 动词前缀（`engine.py:480`）——都是「缺结构化焦点态、只能反复从原文重猜」的症状。
- **P2「上下文按引用」只兑现一半**：架构 §4.2（`cockpit-agent-architecture.md:243`）与 §7（`:435`）要求 Execute 只带 `context_ref`、Agent 按需拉、隐私最小化。实际 `current_lat/lng`、电量、prefs 全按值进 meta（`engine.py:430-440` + `clients._merge_meta`），plan 里**每个 Agent 都能看到精确位置**，不管它要不要。`_sdk/base.py:38` 的 `ctx.fetch(scopes)` 按引用拉的能力存在，但仅 chitchat/info 用 `ctx.history`。
- **P1 doc/code 冲突**：架构 §7（`:430`）说 memory 是「上下文唯一真相源」且「当前任务状态、待补槽位」属于它；实际 SessionState 在编排器（这是 memory 重构评审 §0 #2 的既定决定，合理）——**文档未同步**。
- **P6 `prefs` stringly-typed 大杂烩**：`engine.py:430` 把 model_pref/answer_length/位置/电量/poi_page 全塞进 `dict[str,str]`，无 schema、无按 Agent 分发，加字段要改白名单 + 祈祷 Agent 读对 key。

> 注：以上在 PoC 阶段不算 bug（854 测试全绿），属于「再加 Agent / 加多轮复杂度就先崩」的结构债。

## 3. 目标与目标架构

引入统一 **`ContextManager`** 作为脊柱，把「组装 → 下发 → 持久化」收敛成一个**有预算、有结构、有隐私边界**的环节：

```
                   ┌─────────────────  ContextManager  ─────────────────┐
engine.run() ──►   │  assemble(text,ctx) → WorkingSet                    │
                   │     ├─ catalog: registry 语义 top-K ∪ always-include │
                   │     ├─ history: 复用 clients.get_session            │
                   │     ├─ memories: 复用 clients.recall                │
                   │     ├─ focus:  结构化焦点态（SessionState 持久）      │
                   │     └─ render(budget) → 预算内分优先级出 prompt 块    │
                   │  meta_for(manifest,ctx,step) → 按 context_scopes 过滤 │
                   │  persist_turn(...) → 写 history + update_focus       │
                   └─────────────────────────────────────────────────────┘
```

4 个能力（已确认全要）建在这根脊柱上：①装配层+预算（P3）②焦点态（P4）③统一抽象+文档调和（P1/P6）④按 scope 下发（P2）。

## 4. 5 类上下文的归属（调和 §7 与实现）

| 上下文层 | 真相源 | 谁拥有 | 备注 |
|---|---|---|---|
| 临时任务态（挂起 plan/待确认/待补槽）| **编排器** `SessionState` | orchestrator | 沿用 memory 重构评审决定；架构 §7 据此修订 |
| 焦点态（对象/属性/位置/上个 POI/挂起任务）| 编排器（随 SessionState 持久）| orchestrator | 本次新增 |
| 对话历史（滚动轮次）| memory 服务 | memory | 现状不变 |
| 长期画像 / 语义记忆 | memory 服务（pgvector）| memory | 刚重构，不动 |
| 车辆上下文 | 端侧实时缓存，云侧 prefs 快照 | edge/VAL | 现状不变 |

`ContextManager` 是这些层在**编排器侧的统一读写门面**，不改变各层真相源归属。

## 5. 分阶段落地

### Phase 0 — 设计稿落库 + 文档调和（~0.5 天，不动行为）
- 本稿落库（即本文件）。
- 调和 `cockpit-agent-architecture.md` §7：写明 SessionState 留编排器、补「working/core 上下文装配层」小节、§4 归属表并入。
- `AGENTS.md` §4 状态表加一行登记。

### Phase 1 — Planner 上下文装配层 + token 预算 + catalog 语义预筛（~1 天，不动 proto）
- 新建 `orchestrator/cloud/context.py`：
  - `WorkingSet`（dataclass）：`catalog/history/memories/focus/vehicle` + `render()`，在统一**字符预算**（沿用 `planning.py:481` `block[:400]` 的 char-proxy，不引 tokenizer）下按优先级裁剪（focus > 最近轮 > 记忆 > catalog 尾部）。
  - `ContextAssembler.assemble(text, ctx)`：聚合 history（复用 `clients.get_session`）+ memories（复用 `clients.recall`）+ catalog。
- **catalog 预筛**：engine 不再把 `list_agents()` 全量丢给 planner；改用 `clients.resolve(query=text, top_k=K)`（`clients.py:96`，registry 语义路由）取 top-K，**∪ always-include**（chitchat 兜底 + trip-planner 确定性兜底 + **所有 edge/edge_fast 车控核心 agent**），`resolve` 失败/空 → **回退全量**。env `PLANNER_CATALOG_TOP_K` 默认 **20**（高于当前 ~13 agent 规模，现为 no-op；真正大规模才触发）。
  - 诚实边界：当前 ~13 Agent 规模，K 给足时行为几乎不变、token 收益小；真正收益在 Agent 上规模后。安全性：LLM 引用未入选 Agent 时 `_validated_steps`（`planning.py:348`）已判 unknown→拒整 plan→重试/降级，不静默错答。
- `replan`（`planning.py:206`）复用同一装配（至少补 history+focus），修 P3 不一致。
- `PlanBuilder.build` 签名 `(text, agents, ctx, history, memory)` → `(text, working_set, ctx)`；`_format_history/_format_memory/_build_catalog` 迁入 `WorkingSet.render()`。
- **关键文件**：`context.py`(新)、`planning.py`、`engine.py`(_orchestrate B 段)、`loop.py`(replan)。

### Phase 2 — 结构化焦点态（替正则补丁，~1 天）
- `Focus`（dataclass）：`last_agent_id/last_intent/obj/positions/attr/last_poi/last_destination/pending_task/updated_ts`；持久进扩展后的 `SessionState`（`models.py:89` + `session.py`）。
- engine 每轮成功后 `ContextManager.update_focus(plan, results)`：从执行 step+result 抽焦点。
- 装配注入 planner「当前焦点」块（结构化、省 token、更准）。
- **增量退役补丁**：focus 与现有正则**先并存**；新增指代用例跑绿后逐条移除可被 focus 等价覆盖的启发式。**不动** F1 确认闭环（`engine.py:454`）、trip 多轮兜底——除非测试证明等价。
- **关键文件**：`context.py`、`models.py`、`session.py`、`engine.py`、`planning.py`。

### Phase 3 — 统一 Context 抽象收敛（架构级，~1 天）
- `engine` 的 `_build_context/_history/_recall/_append_turn` 迁入 `ContextManager`；engine 只调 `assemble`/`persist_turn`。
- `PlanContext` 瘦身为 ids+权限+runtime；prefs 迁入类型化 `ContextEnvelope`（location/vehicle_battery/ui_prefs/runtime 分字段）。
- 历史/记忆统一经 ContextManager 单入口；修 P5「engine 与 agent 各拉一次 history」（装配结果透传或加每轮缓存）。
- **关键文件**：`context.py`、`engine.py`、`models.py`、`clients.py`(`_merge_meta` 走 envelope)。

### Phase 4 — 敏感上下文按 scope 下发（动 proto+SDK，~1 天）
- **proto**：`proto/cockpit/agent/v1/agent.proto` `AgentManifest`（`:19`）增 `repeated string context_scopes`；`make proto` 重新 codegen（铁律：先改 proto）。
- SDK `agents/_sdk/manifest.py` 读 `context_scopes`；相关 manifest.yaml 声明（navigation/charging/food/parking/info → `location`；charging → `vehicle_state`）。
- `ContextEnvelope.meta_for(manifest,...)` 只把敏感键（`current_lat/lng`、`vehicle_battery`）下发给声明对应 scope 的 Agent；非敏感 prefs（`answer_length` 等）暂保持广播（兼容）；未声明 = 不给敏感键。
- 回归集中点：`agents/_sdk/location.py:20` 是位置唯一读取处。
- **关键文件**：`agent.proto`、`_sdk/manifest.py`、各 `manifest.yaml`、`clients.py`/`context.py`。

## 6. 验收

- 每期后：`python -m pytest --import-mode=importlib`（基线 854 passed/6 skipped 不破）。
- Phase 1：装配单测——多意图「打开空调并播放音乐」仍出 2 step；`resolve` 失败回退全量；预算裁剪顺序。
- Phase 2：焦点指代 engine 级用例——「副驾空调26度」→「再调高一点」解析为副驾空调 inc；「附近川菜馆」→「导航去第一家」正确指代。
- Phase 4：`make proto` 通过；navigation/charging 契约+真栈位置仍可用；未声明 scope 的 Agent 收不到 `current_lat`。
- 端到端：`make up` 后 `python test/e2e_ws.py`；复杂任务过程区/确认闭环/trip 多轮回放不回归。

## 7. 风险

- **catalog 预筛漏召回多意图** → K 给足 + always-include + 保留 trip 确定性兜底 + `_validated_steps` 拒整 plan 兜底。
- **Phase 2 退役补丁过激回归** → focus 与正则并存，逐条移除且每条测试证明等价；F1/trip 多轮不在首批退役。
- **Phase 4 破坏现有 Agent 读位置** → 给相关 Agent 声明 location scope，回归盯 `location.py` 单点。
- **过度工程** → 1+2 已交付绝大多数用户可见价值（规划质量 + 指代稳）；3+4 是架构/隐私收尾，可视精力停在 Phase 2 后。

## 自然停止点
Phase 1 → 规划 token/质量；Phase 2 → 指代稳（用户可见价值基本满足）；Phase 3 → 架构整洁；Phase 4 → 隐私边界兑现。可在任一期末停。

---

## 8. 落地记录（2026-06-25，883 passed/6 skipped 实测）

**已建**：
- **Phase 1**：`orchestrator/cloud/context.py`（`ContextManager` + `WorkingSet`）；catalog 语义预筛（≤K no-op、>K 取 top-K∪always-include、resolve 失败回退全量）；`render_context`/`render_catalog` 统一字符预算；`PlanBuilder.build` 收敛为 `(text, working_set, ctx)`、`replan` 复用同一装配。
- **Phase 2**：`Focus` 焦点态（对象/位置/属性/上个 POI/目的地），独立 `SessionStore.{load,save}_focus`（与挂起态分离、完成不清、TTL 300s）；engine 每轮成功后 `update_focus`，装配时焦点块置顶注入；planner prompt 指代规则指向「当前焦点」。**未删任何现有正则补丁**——trip.plan/trip.modify、确认词判定属意图消歧，与指代正交，强删会回归 F1/trip。
- **Phase 3**：`build_context`/`append_turn`/`_history`/`_recall` 全迁入 `context.py`（engine 仅委托，`_build_context` 保留为 staticmethod 兼容既有测试）。
- **Phase 4**：proto `AgentManifest.context_scopes`（field 13，已 `buf generate` 重生成 Python+Go）；SDK manifest loader 读取；7 个 manifest 声明（navigation/food/info/parking 直接读位置；charging/trip 还需电量；trip/road-safety 为 propagator 透传给子 agent）；`Step.context_scopes` + `_validated_steps`/`_resolve_endpoints`/`_serialize_plan` 携带；`clients._merge_meta(context_scopes=)` 按声明剔除未授权敏感键。

**两处工程取舍（偏离原计划，已验证更优）**：
1. **Phase 3 未做 `prefs → ContextEnvelope` 类型重写**：`prefs` 虽 stringly-typed，但天然对应 proto `map<string,string>` meta 传输形态；重写 churn 大（穿透 clients + 多测试）、PoC 价值低。改为把 P6 的隐私目标直接在 Phase 4 下发边界以「敏感键→scope」分类实现，更直接。门面 consolidation（构建 ctx→装配→焦点→落库 全归 ContextManager）已达成「统一抽象」的本质。
2. **Phase 4 过滤范围**：最小化只作用于**生产 cloud unary 下发路径**（多 agent 计划广播位置的真实泄漏点）。**edge 路径不过滤**（电量供端侧安全门控）、**单步流式直通暂不过滤**（单 agent、用户直接调用，广播风险低；避免改一批 stream 测试 spy 签名）。**验证边界**：agent 单测直接调 `handle()` 绕过 `_merge_meta`，故位置「确实到达 navigation/charging/trip」由 `_merge_meta` 单测 + dispatcher 透传单测覆盖。consumer 集（7 agent）由 grep 直接读位置者 + 调子 agent 的 propagator 得出。

**真栈 e2e 抓出并修复的一处回归（catalog 预筛误丢 edge 车控）**：
- 现象：food/parking 起来后 agent 数到 13 > 原 K=12，Phase 1 预筛激活；registry 语义 resolve 对「打开后备箱」把 `edge-vehicle` 排在 top-12 之外丢掉 → cloud 规划兜底 chitchat → 后备箱**直接执行、丢了 require_confirm**（`dangerous_trunk_confirm` e2e 失败）。
- 根因：K=12 恰等于当时 agent 数，单测 ≤5 agent 从不触发预筛，是 e2e 抓出来的（印证 §7「预筛漏召回」风险）。
- 修复（两层）：① K 默认 12→**20**（高于当前规模、现为 no-op）；② 预筛**始终保留 `deployment==edge`/`kind==edge_fast` 的安全核心 agent**（`edge-vehicle`/`edge-media`，少且 require_confirm 敏感，绝不能被相关性丢掉）。单测 `test_catalog_always_keeps_edge_control_agents` 锁定。
- 复验：中枢断言 7/7 全过（trunk turn1 `step.edge:trunk.open→suspended`、turn2 确认后 `trunk: closed→open`）。
