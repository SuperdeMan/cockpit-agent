# 场景编排 Agent 重设计实施计划（可直接接手开工版）

> **状态（2026-07-14）**：✅ **已执行完毕**——P0-P3 当日全落地并真栈验证（e2e_scene 26/26、全量 1576 passed），随后全量代码评审六修复亦已入库。实际落地与本计划的出入（vehicle_state 非 memory scope 改 NATS 镜像、`state_mirror.py` 提前到 P0、端侧场景句护栏为计划外新增等）以 `2026-07-14-scene-orchestrator-redesign.md` **§0.5 落地纠偏 / §0.6 评审修复**为准。本文保留作实施过程参考。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 `docs/design/2026-07-14-scene-orchestrator-redesign.md`（**v2.1 评审修正版**，D1-D11）：P0 用户造场景闭环 → P1 管理与覆盖 → P2 场景策略引擎（Ground·Solve + Verify-Repair）→ P3 询问式触发。本计划 P0 为 Task 级可开工粒度，P1/P2/P3 为承接概要。

**Architecture:** 重构既有 `agents/scene_orchestrator/`（50069，first_party/core，agent_id/端口/部署位不变）：LLM 仅创建期编译 NL→Scene DSL v2（白名单=构建期 COPY `orchestrator/edge/knowledge/commands.yaml`），激活/执行/修复全程零 LLM；自有 PG 表 `scene_item`（asyncpg，无 PG 内存降级）+ 预置 `scenes.yaml` 合并视图（用户同名遮蔽）；执行链路完全复用现状（AgentResult.actions → 编排聚合 → 端侧 `_dispatch_cloud_actions` → VAL 安全门控）；**不改 proto / 编排核心 / 网关**。

**Tech Stack:** Python 3.11（agents/_sdk BaseAgent、asyncpg、nats-py）、React+TS（types.ts 契约卡片）、PG JSONB。

**实施者必读（本仓惯例 + 本主题特有，违者返工）：**
1. 工作目录=仓库根；先确保 `gen/` 存在（`make proto` 或 `scripts/gen-proto.ps1`）。
2. **依赖闭包**：`asyncpg` 不在 `_sdk/requirements.txt`——经 `agents/scene_orchestrator/requirements.txt` 叠加（Task 6，reminder 同款），**不动** `_sdk/requirements.txt`。共享模块加新消费方必查 Dockerfile/requirements 闭包（dashboard 主题坑：llm-gateway 缺 nats-py 事件静默丢）。
3. Docker 无卷挂载：改源码后必须 `docker compose -f compose.yaml up -d --build scene-orchestrator-agent`（`--force-recreate` 不够）。
4. **词表唯一真相源**：动作白名单只能来自 COPY 进镜像的 `commands.yaml`（D3）——禁止在云侧手写第二份对象/操作表（0.1.0 词表漂移坑，roadmap §8）。本地跑测试用仓库相对路径回退（Task 1）。
5. **manifest 改完必跑 registry resolve 基线**：registry 语义路由中 description 长度即权重（mode-routing 主题坑），capabilities 描述改动可能扰动其他 Agent 的路由。
6. 容器重建后 **等 ≥10s 再跑 e2e**（`AGENT_REREGISTER_INTERVAL=10`，reminder 主题坑：立跑因 registry 未完成重注册假失败）。
7. **`_POC_DEFAULT_SCOPES` 核对**（`orchestrator/cloud/context.py`）：manifest `requires_permissions` 新增 `profile.read/profile.write` 后确认在 PoC 默认授权集内（reminder 落地踩过：合法 scope 不在默认集 → 规划前被剔除 → 全兜底 chitchat）。
8. 测试导入禁裸 `sys.path` 插 `src`（2026-07-13 坑：providers 通用包名劫持 sys.modules）；统一 `python -m pytest --import-mode=importlib`。
9. LLM JSON 输出解析容错参照既有样板（markdown fence 剥离/截断抢救，`orchestrator/cloud/planning.py` `_parse_and_validate` 与 2026-07-12 主题「合成 JSON 截断/裸引号边界式抢救」）；两次失败 FAILED 诚实降级，不猜。
10. 后台 task 不依赖请求级 ctx：自持 `MemoryClient` 重建 Context（deep_research 先例 `agents/deep_research/src/agent.py`）。
11. e2e/探针 session_id 一律 `e2e-` 前缀（conventions §9.2 跳过记忆抽取）。
12. 文件 UTF-8（用 Write/Edit 工具，不用 `Out-File`）；新 Python 文件先 `python -m py_compile` 再跑测试。
13. 提交在 main、**不 push**（push 需泓舟明示）。
14. 既有 `tests/test_agent.py` 的 deactivate 断言需按新语义更新（从嘴炮话术改为恢复动作），其余 v1 断言（list/activate/NEED_CONFIRM）必须继续绿——预置 4 场景零迁移是硬约束。

---

## 文件结构（先定边界再动手）

```
agents/scene_orchestrator/
  manifest.yaml            # v0.2：6 intent + route_hints + context_scopes + 权限（Task 7）
  main.py                  # 不变（serve 入口）
  Dockerfile               # + COPY commands.yaml 词表 + requirements 叠加（Task 6）
  requirements.txt         # 新增：asyncpg>=0.29
  schema.sql               # 新增：scene_item 表（幂等，v2.1 全列）
  scenes.yaml              # 不变（预置 4 场景，builtin）
  knowledge/               # 构建期 COPY 产物目录（gitignore 或 COPY 目标，见 Task 1）
  src/agent.py             # 重构：6 handler + 合并匹配 + 快照/恢复
  src/catalog.py           # 新增：词表加载 + validate_action/validate_condition + 反向默认表
  src/store.py             # 新增：SceneStore（PG/内存双后端，字段一一同名映射）
  src/compiler.py          # 新增：LLM 编译 + 确定性校验 + require_confirm 强制改写
  src/solve.py             # P2：纯函数求值器（三态 when/guards/幂等）
  src/verify.py            # P2：后台对账 task（代际护栏 + 单飞）
  tests/                   # test_catalog / test_store / test_compiler / test_agent（扩）
                           # P2 + test_solve / test_verify
agents/_sdk/shared_state.py          # +SCENE_ACTIVE +SCENE_PENDING（Task 7）
hmi/src/types.ts                     # +SceneCard/SceneListCard + union + AGENT_CATALOG（Task 5）
hmi/src/components/Cards.tsx         # +SceneCardView/SceneListCardView + 2 case（Task 5）
deploy/docker-compose.yaml           # scene-orchestrator-agent 补 POSTGRES_DSN env（Task 6）
test/e2e_scene.py                    # 真栈闭环（Task 8）
test/eval_corpus/route_hints_cases.yaml  # 场景正反例（Task 7）
docs/conventions.md / docs/design/README.md / AGENTS.md  # 登记（Task 9）
```

责任边界：`catalog` 只管词表与校验（无 IO 依赖，词表文件路径注入）；`store` 只管 user 场景 CRUD（不碰 builtin/NATS）；`compiler` 只管 NL→DSL（LLM 客户端注入，mock 可测）；`solve` 纯函数（env dict 注入）；`verify` 只读对账+proactive（不产 actions）；`agent.py` 只做编排与话术。

---

## P0：用户造场景闭环

### Task 0：基线检查

- [ ] **Step 0.1** 记录基线（后续零回归对照）：

```bash
python -m pytest --import-mode=importlib -q 2>&1 | tail -3
python -m pytest agents/scene_orchestrator/ -v --import-mode=importlib 2>&1 | tail -5
cd hmi && npm test 2>&1 | tail -3 && cd ..
```

- [ ] **Step 0.2** 读实现依据（不读完不许写码）：`orchestrator/edge/knowledge/commands.yaml` 顶层结构（`objects:` 下对象→操作/参数）、`orchestrator/edge/edge_call.py::action_to_structured` 的可翻译判据（这是校验器的对齐目标）、`orchestrator/edge/server.py:660-705`（回流分发）、`agents/reminder/src/store.py`（双后端样板）、`agents/reminder/src/agent.py` 的 `_cancel` 确认模式与 `REMINDER_PENDING` 两轮续接（create 确认/追问照此）。

### Task 1：词表构建件 `src/catalog.py`（编译白名单地基，D3）

- [ ] **Step 1.1** 先写测试 `tests/test_catalog.py`：合法动作过（`hvac.set`+temperature）、幻觉对象剔（`massage.on`）、幻觉参数键剔、枚举值/范围夹紧、`seat.recline` 强制 `require_confirm=true`（§8.1 表覆盖 LLM 输出）、词表文件缺失时诚实抛错（不静默空词表）。
- [ ] **Step 1.2** 实现：`load_catalog(path=None)`——路径解析顺序：显式参数 → `SCENE_CATALOG_PATH` env → 镜像内 `agents/scene_orchestrator/knowledge/commands.yaml` → 仓库相对 `orchestrator/edge/knowledge/commands.yaml`（本地开发/CI 回退）。`validate_action(action, catalog) -> (ok, cleaned, reason)`：`command` 按 `obj.operate` 拆分对 `objects` 存在性校验、params 键在对象声明内、值经枚举/范围夹紧；`DANGER_COMMANDS`（§8.1：`seat.*`/`trunk.*`/`frunk.*`/`door_lock.*`/`window.*` 整开/`charging_port.*`/`fuel_tank_cover.*`）强制改写 `require_confirm`。`RESTORE_DEFAULTS` 反向默认表（seat→90/volume→50/ambient_light→off/hvac→auto 24/fragrance→off）。
- [ ] **Step 1.3** `python -m pytest agents/scene_orchestrator/tests/test_catalog.py -v --import-mode=importlib` 全绿。

### Task 2：存储 `src/store.py` + `schema.sql`

- [ ] **Step 2.1** 先写测试 `tests/test_store.py`（内存后端跑全部逻辑；PG 分支结构同 reminder 靠 e2e 覆盖）：CRUD、`(user_id, name)` 唯一冲突、按 user 枚举 enabled、`use_count` 递增、无 PG 降级警示日志、**to_row/from_row 字段一一同名（v2.1 修正①：id/user_id/name/aliases/description/goal/source/status/guards/actions/triggers/created_at/updated_at/use_count，禁止改名翻译）**。
- [ ] **Step 2.2** `schema.sql` 照设计 §4.2（含 goal/guards 列）；`SceneStore` 仿 `reminder/src/store.py`（asyncpg 池、启动幂等建表、内存 fallback）。
- [ ] **Step 2.3** 测试全绿 + `py_compile`。

### Task 3：编译器 `src/compiler.py`

- [ ] **Step 3.1** 先写测试 `tests/test_compiler.py`（LLM 客户端 mock 注入）：正常编译（名/描述/goal/动作数）、`unsupported[]` 剔除并保留告知文案（「放舒缓音乐」P0 media 不支持）、LLM 输出幻觉 command 被 `validate_action` 剔、**全部动作被剔 → 返回不可保存标记**（agent 层 FAILED）、markdown fence/截断容错、两次解析失败 → 编译失败、危险动作 require_confirm 被强制改写（LLM 说 false 也改 true）。
- [ ] **Step 3.2** 实现：prompt 携带 `catalog_digest()`（对象/操作/参数枚举紧凑渲染，控制在 ~2000 字内）+ few-shot 一例；输出契约 `{name, description, goal, actions[], unsupported[]}`；走 `self.llm` primary 档（低频重操作，不 @fast）；**P0 不产 when/assert/on_fail/guards**（P2 再教，但落库 schema 即 v2 全列——空值前向兼容）。
- [ ] **Step 3.3** 测试全绿。

### Task 4：`src/agent.py` 六 handler 重构

- [ ] **Step 4.1** 先扩测试 `tests/test_agent.py`：
  - create：带 spec 一轮出 NEED_CONFIRM 回读（含动作清单+剔除告知）→ `meta.confirmed` 重入落库（参照 reminder `_cancel` 确认模式）；只有名字 → NEED_SLOT + 写 `SCENE_PENDING` → 下轮 spec 续接合并；与端侧模式词/既有场景同名 → 追问换名。
  - activate：用户场景遮蔽同名预置；激活动作尾缀 `scene_mode.set`；`SCENE_ACTIVE` 写入含 `activation_id`（uuid4）+ `solved_actions`（P0=全部下发动作）+ `snapshot`（按 solved 集受影响键，`ctx.fetch("vehicle_state")` 缺键记 null）；`use_count` 递增；危险动作 NEED_CONFIRM 分支不变（v1 断言保绿）。
  - deactivate：**恢复基准=`SCENE_ACTIVE.solved_actions`（v2.1 修正④）**——snapshot 有键恢复快照值、缺键走 `RESTORE_DEFAULTS`、含座椅 NEED_CONFIRM、尾缀 `scene_mode.set off`、执行后清 `SCENE_ACTIVE`；无激活场景 → 诚实话术。**更新既有嘴炮断言**。
  - list：mine/builtin 分组 + `scene_list` 卡 payload。
- [ ] **Step 4.2** 实现（`_build_action`/`_action_desc`/`_match_scene` 模糊匹配保留；合并视图=用户精确/别名/模糊 → 预置同序）。
- [ ] **Step 4.3** scene 全部单测绿：`python -m pytest agents/scene_orchestrator/ -v --import-mode=importlib`。

### Task 5：HMI 卡片

- [ ] **Step 5.1** `types.ts`：`SceneCard{type:"scene_card", context:"created"|"confirm"|"activated"|"suggest", name, description, actions_preview:[{label,danger}], buttons:[{label,send_text}]}`、`SceneListCard{type:"scene_list", mine:[], builtin:[]}`，入 union；`AGENT_CATALOG` 补场景条目。
- [ ] **Step 5.2** `Cards.tsx` 两个渲染组件 + switch case；列表条目点击 `send_text("开启X模式")`（`intent_choice`/`place_list` 先例）。
- [ ] **Step 5.3** `cd hmi && npm test && npm run build && npx tsc --noEmit` 零新错。

### Task 6：镜像与部署

- [ ] **Step 6.1** `requirements.txt`（asyncpg）+ Dockerfile：叠加安装 + **`COPY orchestrator/edge/knowledge/commands.yaml agents/scene_orchestrator/knowledge/`**（构建上下文是仓库根——先核对 compose 里该服务 build context，与其他 Agent 一致为根则直接 COPY 可行）。
- [ ] **Step 6.2** `deploy/docker-compose.yaml` scene-orchestrator-agent 服务块补 `POSTGRES_DSN` env + depends_on postgres（reminder 同款）。
- [ ] **Step 6.3** `docker compose -f compose.yaml build scene-orchestrator-agent` 构建通过，容器内 `knowledge/commands.yaml` 存在。

### Task 7：manifest v0.2 + 状态键登记 + 路由收敛

- [ ] **Step 7.1** `manifest.yaml`：capabilities 6 intent（examples 供语义路由；description 精炼——**长度即权重**）；`route_hints` 按设计 §6 三条（activate/create/deactivate，guard 排除端侧模式词 + 「是什么/怎么用」）；`context_scopes: [vehicle_state]`；`requires_permissions: [vehicle.control, navigation.control, profile.read, profile.write]`（media.control P1 放开时再加回）。
- [ ] **Step 7.2** `shared_state.py` + conventions §9 登记 `SCENE_ACTIVE`/`SCENE_PENDING`（schema 照设计 §4.3）。
- [ ] **Step 7.3** 核对 `orchestrator/cloud/context.py::_POC_DEFAULT_SCOPES` 覆盖上述 scope（必读 7）。
- [ ] **Step 7.4** `test/eval_corpus/route_hints_cases.yaml` 加正反例：「开启午休模式」「来个钓鱼模式」「创建一个下雨模式：关窗加除雾」「退出露营模式」为正；「打开运动模式」（端侧 driving_mode）「省电模式怎么开」（manual/chitchat）「勿扰模式」为反。跑 `python test/eval_route_hints.py --dump` 实测收敛 pattern/guard（存量 47 例零回归）。
- [ ] **Step 7.5** 跑 registry resolve 基线（必读 5）：`python test/eval_registry_resolve.py`（或 AGENTS.md 登记的等价命令）不回归。

### Task 8：真栈 e2e `test/e2e_scene.py`

- [ ] **Step 8.1** 起全栈 `docker compose -f compose.yaml up -d --build scene-orchestrator-agent`（改过共享面则全栈 build），**等 ≥10s**（必读 6）。
- [ ] **Step 8.2** 用例（WS 驱动，session=`e2e-scene-<ts>`，样板 `test/e2e_reminder.py`）：
  ① create：「帮我创建一个钓鱼模式：氛围灯调到10%，空调外循环」→ 断言 NEED_CONFIRM 回读含 2 动作 → 发「确认」→ 落库成功话术；
  ② activate：「开启钓鱼模式」→ 轮询 collector `GET /api/vehicle/state` 断言 ambient_light/空调变化 + `scene_mode` 已置；
  ③ deactivate：「退出钓鱼模式」→ 断言状态恢复激活前值 + scene_mode=off；
  ④ 遮蔽：创建同名「露营模式」（只有灯光动作）→ 激活走用户版（无座椅确认）。
- [ ] **Step 8.3** 接入 `scripts/run_e2e.ps1|sh` 清单。

### Task 9：回归与登记收尾

- [ ] **Step 9.1** 全量 `python -m pytest --import-mode=importlib -q` 对照 Task 0 基线零回归；`smoke_edge` 13/13（driving_mode/power_mode 端侧路由不回归）。
- [ ] **Step 9.2** 登记：conventions §1（scene 行 intent 列更新）/§2（+create/update/delete 行）/§9；`docs/design/README.md` 状态更新；AGENTS.md §4 加行。
- [ ] **Step 9.3** commit（main，**不 push**），message 含 `feat(scene)` + 测试证据。

---

## P1：管理与覆盖（承接概要，P0 验收后开工）

- **T-P1.1 update/delete**：参数级确定性改（「温度改成24」）/动作级走编译小闭环；delete NEED_CONFIRM、builtin 只 disable。测试：两类修改 + 预置引导「复制为我的」。
- **T-P1.2 custom_params 参数覆盖**：activate 的 raw_text 数值解析（温度/亮度/角度三类起步），只覆盖同对象动作。e2e ③′「开启午休模式温度26」→ hvac 26。
- **T-P1.3 会话沉淀**（D11 桥）：「把刚才这些存成X模式」→ `ctx.history()` 取近 N 轮助手回执话术 → 走 compiler 同一校验闭环 → NEED_CONFIRM 回读。route_hint：`存成.{0,4}模式`。
- **T-P1.4 media 动作放开**：**跨组件改动单独确认后做**——端侧 `_dispatch_cloud_actions` 扩 `media.*` 分发一类（R4.1b 端侧对象化同类，不碰云端编排核心），catalog 白名单同步放开，浪漫模式补音乐动作。

## P2：场景策略引擎（v2 增量核心；开工前泓舟确认 D9/D10 无变）

- **T-P2.1 编译器策略字段**：prompt 教产 `guards/when/assert/on_fail`；`validate_condition` 按条件 key 白名单（`battery/gear/speed_kmh/location.city/hour` + vehicle_state 镜像键，随词表同源生成）剔幻觉键；`on_fail`/`mode` 枚举校验。
- **T-P2.2 `src/solve.py` 纯函数求值器**：三态求值（**unknown：when→exclude+告知话术、guard→降级 confirm**，v2.1 修正②）、互斥分支对缺数据不双发（单测点名）、assert 幂等跳过（键读不到不剔）、空集诚实反馈；`(scene, env, custom_params) -> (actions, notes, confirm_items)` 全离线可测。
- **T-P2.3 NATS 镜像订阅**：on_start 单条 `vehicle.state.changed` 订阅、**多消费方壳**（P2 只挂镜像消费方；触发/deferred P3 挂）——road-safety 样板 + `OBS_SNAPSHOT_INTERVAL` 全量快照兜底恢复。
- **T-P2.4 `src/verify.py` 后台对账**：闭包携带 `activation_id + solved_actions`（v2.1 修正③）、醒来先代际校验、`_verify_tasks[user_id]` 单飞 cancel、on_fail 三路处置（skip 汇报合并 / retry_suggest 建议卡 / defer_p 写 deferred 前再校验代际）、全路径 fail-open；proactive payload 纯 JSON（reminder scheduler 样板）。**activate/deactivate 同步接线**：新激活 cancel 旧 task、deactivate cancel + 恢复时跳过 verify 确认未生效项（v2.1 修正④）。
- **T-P2.5 真栈 e2e ④**：`POST /api/debug/vehicle` 压行车态 → 激活含座椅场景 → 断言收到 verify 诚实汇报 proactive；压驻车再「开启」→ 断言只补座椅项（幂等）。

## P3：询问式触发（承接概要）

- Trigger DSL（time/event 三键起步）+ 时间 watcher（30s poll，reminder scheduler 样板）+ 事件 watcher（边沿触发 + 30min 节流，挂 T-P2.3 订阅壳）+ `scene_suggest` 建议卡（零执行权，D6）+ deferred 驻车投递（第三消费方）+ e2e ⑤（压电量 <20 → scene_suggest）。

---

## 总 DoD（P0 收口判据）

```bash
python -m pytest agents/scene_orchestrator/ -v --import-mode=importlib   # 新单测全绿（含既有 v1 兼容）
python test/eval_route_hints.py                                          # 存量零回归 + 场景新例通过
python -m pytest --import-mode=importlib -q                              # 全量对照基线零回归
python test/smoke_edge.py                                                # 13/13（端侧模式词不被劫持）
docker compose -f compose.yaml up -d --build scene-orchestrator-agent && sleep 12
python test/e2e_scene.py                                                 # 真栈 ①②③④ 全过
```

设计依据与决策原文：`docs/design/2026-07-14-scene-orchestrator-redesign.md`（v2.1，D1-D11 + 四修正记录在状态行与 §4/§5 行内标注）。
