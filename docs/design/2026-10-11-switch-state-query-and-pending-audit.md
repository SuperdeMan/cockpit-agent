# 开关现状问答与挂起中的执行审计：两条登记缺陷的修复

- 状态：代码已提交本地分支（未推送、未部署）；用户 2026-10-11：「项目里已登记你直接能做的也可以做一下」
- 来源：[QA 交接](../reviews/2026-08-30-qa-closeout-handoff.md) §5「2026-10-09 / 10 夜间新增 ⑤ 香氛状态问答答不出」；
  [核心旅程冻结](2026-10-09-v2-core-journey-freeze.md) §11 的 P10 / P13（挂起中问「我刚才让你做了什么」）
- 范围：端侧（分类器、VAL、服务出口）、`runtime/question_shape.py`、`runtime/session_facts.py`、云端编排的会话事实读出口

## 1. 香氛状态问答答不出

### 1.1 现象

核心旅程 C02（P3 写车道）：「打开香氛」之后问「香氛现在开着吗」。`5afd98fd` 那趟答「我看不了当前状态，要不你直接问"香氛状态"试试？」，
`c50e22ed` 那趟答「开着呢」——两趟都落在 `chitchat.talk`。旅程期望只拦「关着 / 没有开」，所以一直是绿的，缺口靠人读出来。

### 1.2 根因（逐段）

1. 端侧：`orchestrator/edge/fast_intent.py` 香氛分支只有「含"关"就关、带档位就设、否则一律开」，状态问句被认成 `fragrance.open`，
   再被出口的问句闸（`classify_structured`：写操作 ∧ 非指令问句 ⇒ None）拦下上云——端侧没有任何「问现状」的意图。
2. 云端：规划器原始输出 `{"addressed": true, "goal": "查询香氛当前是否处于开启状态", "steps": []}`——理解对了，**没有能力可派**
   （端侧只声明了电量、胎压两种查询），只好交给闲聊兜底。
3. 闲聊：系统提示明写「你看不到这辆车的任何实时读数…如实说你这边看不到」，所以答「看不了」是按设计弃权；答「开着呢」是模型按上文猜的，
   恰恰违反了那条约束。车态里有 `fragrance` 键（`orchestrator/edge/vehicle_driver.py`），没有一条路去读它。

### 1.3 修法

| 段 | 改动 |
|---|---|
| 判据（唯一一份） | `runtime/question_shape.py::asks_switch_state`：说法（`SWITCH_STATE_WORDS` / 自带问形的 `SWITCH_STATE_ASKS`、「开着还是关着」）∧ 问形 ∧ 说法之后没有另起的操作分句；句首查看壳（「帮我看看 / 查一下」）先剥掉再判；祈使标记、处置式（「能把香氛开了吗」）不认。零对象词 |
| 意图 | `fast_intent.py` 香氛分支在开 / 关之前先认状态问句 → `query/fragrance`；`LOCAL_INTENTS` 加 `fragrance.query` |
| 声明 | `commands.yaml` 香氛 `operates` 加 `query`、`edge_intents` 加 `fragrance.query`（意图级效果由操作名推导为 read，对象级仍是 write）；迁移探针 `_BEFORE_MIGRATION` 签收 +1 |
| VAL | `_SWITCH_STATE_QUERIES`（对象 → 开关键、档位键、档位说法，目前只有香氛）：只读、不改状态；不过写操作门控（行车限制 / 低电量禁高耗电 / 倒挡 / 儿童锁管的都是「做」）；开关键读不到或不是布尔 ⇒「暂时读不到香氛状态」，不按缺省当「关着」念；读到时带来源说明（「（模拟车读数）」，同电量） |
| 话术 | `fragrance_query_success`（「香氛现在{value}」，`{value}` = 「开着，浓度3档」/「开着」/「关着」）、`fragrance_query_unavailable` |
| 服务出口 | 只读意图执行成功也**不回传动作**（`server.py::_reports_action`，判据 `runtime.intent_effect.is_write_intent`），四个本地出口（快路径 A / A2 / B、云端降级兜底）同一条；本轮意图名照旧记在 turn 上 |

为什么要动服务出口：动作 = 「这一轮对车 / 媒体做了什么」，下游三处都这么读——执行审计（「刚才执行了什么」）、上云的执行事实账本、
核心旅程判定（只读车道出现任何动作都是失败；写车道未声明的动作记 `undeclared_action`）。修前快路径给电量 / 胎压查询也回传动作，
测试与云端上下文却一直按「无动作的一轮」建模它（`test_local_turn_memory`、`orchestrator/cloud/context.py` 的 actionless battery query）。
不改这里，C02 第二轮会从「绿但答得差」变成 `undeclared_action:fragrance.query`；审计会说「执行过：香氛现在开着吗」。

**连带变化（客户端可见）**：电量、胎压的本地查询不再回传动作，HMI / App 不再为它们出一张「执行：胎压监测 查询」的回执卡（播报不变）。
云端规划派到端侧的查询步（EdgeCall 路径）不在本次范围。

对抗覆盖：`fragrance.query` 与 `fragrance.on/off/set` 同族（非危险、端侧确定性快路径、不经 LLM 落域），按 2026-08-02 的概括授权判据
进 `coverage_exemptions.yaml`（不是逐条人裁），回归由本次新增的单测与端侧参数化语料承担。

### 1.4 没有覆盖的问法

- 「香氛是什么状态」「香氛的状态是什么」：出口的解释 / 定义问闸（`asks_for_explanation`，第五维）先把「X 是什么」交云端；那道闸共享给电量、胎压，
  本次不动。云端能力目录里已有 `edge-vehicle/fragrance.query`，规划可以派到它（未做真栈标定）。
- 只问档位（「香氛开了几档」「香氛浓度多少」）不在「开关现状」判据里，照旧上云。
- 其余开关对象（氛围灯、座椅加热…）的「X 开着吗」同样没有能力；要支持时在 `_SWITCH_STATE_QUERIES` 加一行 + 声明 + 分支调用，判据复用。

## 2. P10：挂起中问「我刚才让你做了什么」，审计不提在等确认的操作

### 2.1 根因

`orchestrator/cloud/engine.py::_session_fact_answer` 的执行审计出口只读执行史账本（`runtime/session_facts.py::audit_answer`），
挂起表只接给了「还有待确认的操作吗」那条出口。于是挂起「打开油箱盖」时问「我刚才让你做了什么」，答「这次对话里我还没有执行过任何操作」，
等确认的那件事一字不提（P13 `known_red`；P10 只钉「不得说已打开」，一直是过的）。

### 2.2 修法

- `audit_answer(history, *, with_time=False, pendings=None)`：收挂起表摘要，念成「…；「打开油箱盖」等你确认，还没执行。」
  （有执行史时是「…。另外「打开油箱盖」等你确认，还没执行。」）。不传 / 空表 / 读不到 ⇒ 逐字是修前的答案（chitchat 兜底位不传）。
- 「「X」等你确认」的措辞抽成 `_pending_clauses`，挂起读出口与审计共用一份：同一条挂起在两处是同一个说法。
- 编排层回答审计时与挂起读出口同一份取数（`_pending_digest`）；读不到只报执行史，不替它说「没有」。只读，确认 / 取消闭环不变。

## 3. 验证（代码 `370d5eab` 端侧 / `d1fc804e` P10，本地；未推送、未部署）

- 新增测试：`runtime/tests/test_switch_state_question.py` 31 条（问法正 17 / 反 14）、`orchestrator/edge/tests/test_fragrance_state_query.py` 14 条
  （原话 → 意图 → VAL → 话术，只读不改状态，门控不拦问现状，经端侧服务不回传动作、本轮 intents 仍记查询，「打开香氛」照旧回传动作，电量查询同规则）、
  `runtime/tests/test_session_facts.py` +4、`orchestrator/cloud/tests/test_engine_session_facts.py` +3（挂起中审计念出等确认的事、不说已打开、不消费挂起；
  执行史与挂起并报；挂起表读不到只报执行史）、端侧对象语料识别 +2 句（同时断言 VAL 收下 `query`）。
- 变异（撤掉修复，新测试必须红）：分类器不认状态问句 → 3 红；只读也回传动作 → 3 红；撤掉写操作门控豁免 → 1 红；审计不传挂起 → 2 红。
  第一条首跑时那句退化成上云，测试环境里云端调用没有截止时间、挂住了——已给该测试的服务桩加云端替身，退化时当场变红而不是挂住。
- 签收点（有意新增的能力面）：迁移探针 `_BEFORE_MIGRATION` +1；能力契约线上清单 157 → 158（新增即严格契约，`new_strict`）；
  规划器目录 157 → 158 条、+31 字符 → 14168、16k 余量 1832、零裁剪（描述是机械生成的「查询香氛」）；对抗覆盖豁免 +1（概括授权判据）。
- 门禁：skills、exemplars、L0 strict 2/2（discovery 638 / gate 129 唯一输入，名额未动）、capability integrity 均通过；`test/smoke_edge.py` 13/13；
  端侧测试 1266 passed / 2 skipped。
- 全量（`TZ=UTC0`，`-n 4 --dist worksteal`，`d1fc804e` + 本文与索引两处未提交文档）：**11650 passed / 37 skipped**、0 failed，9 min 03 s。
  比 `1778e22e` 的 11594 多 56 条，即本次新增测试数；skip 数不变。
- 客户端：移动端名称对账测试（`mobile/test/controlNames.test.ts`）在隔离工作树里跑不起来（jest 不爬 `.claude/` 下的路径），
  用 Node 按同一解析方式复核：83 个端侧意图首段全部落到对象中文名、动作词齐全（`fragrance.query` = 香氛 / 查询）。

### 3.1 上线后要核的

- 真栈 C02 第二轮答「香氛现在开着…」且动作为空（写车道判定不再出 `undeclared_action`）；P13 答句提到「油箱盖」、P10 仍不说已打开。
  核过之后再摘清单里 P13 的 `known_red`、给 C02 第二轮加 `speech_any: [开着]` 钉住——清单改动属于冻结尺子，本次不动。
- HMI / App：电量、胎压本地查询不再出「执行：… 查询」回执卡（播报不变）——这是本次唯一的客户端可见变化。

### 3.2 顺带看到、未处理（与本次判据无关的既有行为）

- 「香氛为什么开着」：原因问带操作字，问句闸按指令放行，端侧执行成打开香氛（修前即如此）。
- 「帮我看看香氛」：剥掉查看壳后没有状态问法，照旧按打开执行（修前即如此，句子本身也有歧义）。
- 本地查询的授权仍沿用 `edge-vehicle` 整个 manifest 的 `vehicle.control`（电量、胎压同此），读写分权不在本次范围。
