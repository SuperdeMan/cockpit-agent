# 多意图拆分 + 对话上下文/指代消解

- **状态**：已全部落地（2026-06-14）：上下文 + 多意图（M1 云侧 DAG + M2 端侧切分 + M3 黄金用例）
- **交付对象**：后续开发者 / Agent
- **关联代码**：`orchestrator/edge/fast_intent.py`、`orchestrator/cloud/planning.py`、`orchestrator/cloud/engine.py`、`orchestrator/cloud/session.py`、`orchestrator/cloud/executor.py`、`agents/chitchat/src/agent.py`、`memory/`
- **关联文档**：`docs/architecture/detailed/ws3-planner-engine.md`、本目录车控指令架构文档

---

## 1. 现状（两个独立缺口）

### A. 多意图：一句话多指令无法可靠拆分
- 端侧 `fast_intent.classify()`（`fast_intent.py:20`）是**单意图规则**：从头匹配第一个命中就返回，"打开空调并播放音乐"只会命中空调。
- 云侧 `planning.py` 的 LLM Planner **理论上支持 DAG 多 step**（prompt 写了"组合意图拆成多步"，`planning.py:17-18`），但：
  - 端侧单意图命中后**本地秒回、不上云**，多意图根本到不了云端 Planner；
  - **没有任何多意图语料 / 黄金用例**验证拆分质量；
  - 没区分**并行**（空调+音乐互不依赖）与**串行**（先搜店再订位，靠 `depends_on`）。

### B. 上下文：对话历史完全没进规划
- `session.py` 只存**挂起的确认/补槽状态**（`SessionState`：phase/pending_step_id/missing_slots/completed_results），**不存对话历史**。
- `engine.run()` 调 `planner.build(text, agents, ctx, …)`（`engine.py:95`）时**不传历史**；`PlanBuilder.build` 也不接收历史。
- 唯一用到历史的是 `chitchat` 在 agent 内部读 `ctx.history(4)`（`agents/chitchat/src/agent.py:25`）——**只闲聊有记忆，编排无记忆**。
- 结果：指代类必然失败——"再调高一点"（指代上轮的对象/属性）、"还是刚才那家"（指代上轮 POI）、"换个颜色"（指代氛围灯），Planner 拿到的只有这一句话，无从解析。

---

## 2. 目标

1. **多意图**：用户一句话含多条指令时，可靠拆成多个原子意图，按并行/串行正确编排，话术聚合自然。
2. **上下文**：跨轮指代可消解；规划时能利用最近对话焦点（对象/位置/agent/实体）；与长期记忆分层。

---

## 3. 方案

### 3.1 多意图

**端侧（fast path）**
- `fast_intent` 增加**切分层**：检测连接词（`并`/`同时`/`然后`/`再`/`顺便`/`接着`/`，`）+ 多动词，把整句切成候选子句，逐句 `classify`。
- 路由规则：
  - 全部子意图都在本地白名单（控制类/媒体）→ **本地并行执行**，秒回聚合话术；
  - 含任一非本地意图 → **整句上云**，交给云侧 Planner 统一拆分（避免端云各拆一半）。

**云侧（slow path）**
- `planning.py` 已有 DAG，强化点：
  - prompt 明确**并行 vs 串行**判定（无数据依赖→无 `depends_on`；有→用 `slot_refs` 串接，已支持）；
  - 引入**指令类型语义**（控制类/引导类/播报类，来源见车控指令架构文档 §2.2）：控制类立即执行、引导类开界面、播报类查询并播报，决定 executor 执行与 aggregator 聚合策略；
  - **多意图黄金用例评测集**（端到端，覆盖：纯并行车控、串行跨域、控制+播报混合）。
- `executor.py` 已支持**拓扑分层并行**，无需改；`aggregator.py` 负责把多 step 结果合成一句自然话术（"已为您打开空调并开始播放音乐"）。

**与车控指令表的衔接**：指令表的"控制类/引导类/播报类"就是多意图组合的官方分类，落地时直接复用。

### 3.2 上下文

分两层，**短期会话记忆**（本任务重点）与**长期画像**（memory 服务，已存在）：

**短期会话记忆（Redis，扩展 session.py）**
- `SessionState`（或新增 `ConversationState`）增加：
  - `history`: 最近 N 轮 `[{role, text, ts}]`（滚动窗口，TTL）；
  - `focus`: 最近焦点实体 `{object, positions, attr, agent_id, last_poi…}`——每轮执行成功后由 engine 写入。
- `engine.run()` 每轮**写入** history + 更新 focus；规划前**读取**。

**规划注入**
- `PlanBuilder.build(text, agents, ctx, history, focus, …)` 新增上下文参数；
- planner prompt 增加"对话上下文"段：最近 N 轮摘要 + 当前焦点；
- 指代消解（Phase 1 交给 LLM）：把 focus 注入 prompt，"再调高一点"→ LLM 复用 `{object:aircon, attr:temperature, positions:[副驾]}` 产出 `operate:inc`。Phase 2 再做显式 slot carryover（确定性补槽）。

**分层与开关**
- 短期（Redis，TTL，会话内）vs 长期（pgvector，跨会话画像，`memory/` 服务）；
- 受 HMI 设置 `memory_enabled` 控制（已经 WS `meta.memory_enabled` 透传，见 `hmi/src/settings.tsx` `buildMeta`）——关则不写长期画像、不注入历史。

---

## 4. 分阶段落地

| 阶段 | 多意图 | 上下文 |
|---|---|---|
| **P1** | 云侧 Planner 多意图拆分强化 + 黄金用例；端侧暂只做"含非本地→整句上云" | 短期会话记忆（history+focus）落 Redis；planner prompt 注入；LLM 指代消解 |
| **P2** | 端侧切分层 + 本地并行执行 | 显式 slot carryover；长期画像注入（按 memory_enabled） |
| **P3** | 端侧轻量 NLU 联合切分 | 跨会话个性化（结合 memory pgvector） |

---

## 5. 验收

- **多意图**："打开空调并播放音乐"→ 两个 step 并行执行 + 合成话术；"找川菜馆订位"→ 串行（搜→订）`depends_on` 正确；黄金用例集通过率达标。
- **上下文**：连续两轮"把副驾空调调到26度"→"再调高一点"，第二轮正确解析为对副驾空调温度 `inc`；"附近川菜馆"→"导航去第一家"正确指代。
- 回归：`orchestrator/cloud/tests/` 新增多意图与上下文用例；`python -m pytest … --import-mode=importlib` 全绿。

---

## 6. 风险

- **端云双拆分冲突**：务必"含非本地意图就整句上云"，不要端云各拆一半。
- **历史注入膨胀 token**：N 轮窗口 + 摘要，不要灌全量历史；focus 比全文更省更准。
- **指代歧义**：LLM 消解有上限，Phase 2 的确定性 carryover 才是兜底；歧义时应反问而非乱猜（命中确认红线）。
- **隐私**：长期画像受 `memory_enabled` 控制，敏感数据默认不出车（架构 §安全红线）。

---

## 落地记录（2026-06-14）

**已实现（上下文半部）**：
- engine 每轮按 **用户→助手顺序写入 memory**（`engine.run` 外层包装；规划阶段只读到此前历史，当前句不污染指代）。
- 规划注入：`PlanBuilder.build(history=...)` 把最近 4 轮拼入 prompt + 新增指代消解规则；planner 据此解析"再调高点/还是刚才那家"。
- `memory_enabled` 透传开关控制整轮读写；会话偏好（`prefs`）经 `ExecuteRequest.meta` 下发 Agent。
- chitchat 经自身 `ctx.history(4)` 自动获得对话记忆（同一 session_id）。
- 记忆可视：HMI 记忆视图读 `/api/memory/session|context`（见 hmi）。
- 测试：`test_engine_context.py`（写入顺序/历史注入/开关）。

**已知边界**：端侧快意图直接处理的车控（如"打开空调"）**不经云引擎、不入对话记忆**——记忆当前只覆盖云侧链路。补全需在 edge orchestrator 也写 `AppendTurn`。

**多意图全部落地（2026-06-14）**：M1 云侧 DAG 强化 + M2 端侧切分层 + M3 黄金用例全部完成。

---

## 多意图详细待办（按此执行）

> 目标：一句话含多条指令时（"打开空调并播放音乐"、"找川菜馆订今晚的位"）可靠拆分、按并行/串行编排、话术自然合成。**上下文半部已完成**，本节只列多意图。

### M1. 云侧 DAG 多意图强化 ✅（2026-06-14 已落地）
- [x] **强化 planner prompt**（`orchestrator/cloud/planning.py:_PLANNER_SYSTEM`）：含并行/串行判定规则 + 指令类型语义（控制类/引导类/播报类）+ 3 个具体示例。
- [x] **执行层无需改**：`executor.py` 已是 Kahn 拓扑分层 + `asyncio.gather` 层内并行。
- [x] **聚合**：`aggregator.py` 已对 `len(results)>1` 走 LLM 合成连贯话术。
- [x] **注意**：多 step 计划不走流式直通——已在 `engine` 实现，无需改。

### M2. 端侧切分层 ✅（2026-06-14 已落地）
- [x] **`orchestrator/edge/fast_intent.py` 增切分**：`split_and_classify()` 检测连接词（并/同时/然后/再/顺便/接着/，）+ 多动词，切子句逐句 classify。
- [x] **路由纪律**：全部本地→并行执行；含任一非本地→整句上云。保守策略，任何不确定都交云侧。
- [x] **server.py 快路径**：`Handle()` 先调 `split_and_classify()`，多意图并行执行+话术合成。
- [x] **测试**：23 个用例覆盖本地并行/云回退/单意图/边界情况。

### M3. 黄金用例评测集 ✅（2026-06-14 已落地）
- [x] `orchestrator/cloud/tests/test_multi_intent.py`（4 tests，`_Spy` 模式）：
  - 纯并行车控："打开空调并播放音乐" → 2 个独立 step、并行；
  - 串行跨域："找川菜馆订今晚的位" → 搜→订 `depends_on` 正确；
  - 控制+播报混合："打开空调顺便看看今天天气" → 并行；
  - 单意图透传："打开空调" → 1 step，无多余调用。
- [x] 端侧切分用例：`test_multi_intent_split.py`（23 tests）覆盖本地并行/云回退/单意图/边界。

### 分期建议
- **先 M1 + M3**（纯云侧，半天内可验证，不动端侧）；
- **再 M2**（端侧切分，需联调 smoke）。

### 风险（落地时注意）
- 端云双拆分冲突 → 严守 M2 路由纪律。
- 历史注入 + 多意图同时塞 prompt → token 膨胀，控制历史窗口（已限 4 轮）。
- 歧义多意图（"打开空调和座椅" 指座椅什么？）→ 缺槽走 NEED_SLOT 追问，不乱猜（命中确认红线）。
