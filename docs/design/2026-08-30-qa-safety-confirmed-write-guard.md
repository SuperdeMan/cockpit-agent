# QA 收尾：安全问句禁止进入需确认的云侧写能力

> 状态：**已批准，待落地**（2026-08-30）
> 交付对象：Cloud Planner / QA 探针维护者
> 关联：`AGENTS.md` §4.1/§4.2、`docs/agents-history.md` §84、
> `orchestrator/cloud/planning.py::_question_write_edge_steps`、
> `orchestrator/cloud/tests/test_question_write_guard.py`

## 1. 现场与证据

云端 release `343934bab66c23f83575cee998eb6f64a9f45f3e` 的 MiniMax 长会话里，
`information` persona 第 24 轮出现：

```text
用户：红色机油灯亮了还能继续开吗
意图：luckin.order
回答：想点哪一款瑞幸饮品？
```

该步骤进入挂起态，两轮后的安全追问继续被瑞幸补槽消费。同一句话在
干净会话 `--repeat 3` 三次都正确，所以这不是稳定的单轮落域错，而是只在长上下文中
暴露的高代价方差。

现有 C1 安全闸为：

```text
非指令问句 ∧ 端侧步骤 ∧ 写意图 ⇒ 丢弃该步骤，交给全局兜底 Agent 回答
```

`luckin.order` 是 `deployment=cloud` 且 `require_confirm=true`，因此绕过了只看
`deployment/kind` 的第一道闸。

## 2. 目标与非目标

### 2.1 目标

1. 用户是在询问、而不是下指令时，不得进入任何需二次确认的云侧步骤。
2. 保留现有端侧写闸全部行为，包括其已明记的礼貌问句代价。
3. 不误伤 `manual.query`、`info.search`、`chitchat.talk` 等云侧读取或应答步骤。
4. 混合计划只丢弃违规步骤，已合法的读取/安全应答步骤保留。
5. 沿用现有兜底回答和 `question_write_blocked` 观测签名，不改变历史报表口径。

### 2.2 非目标

1. 不在本批给全部云侧 capability 新增 `effect=read|write` 契约。
2. 不拦截所有 `require_confirm=false` 的云侧副作用；它们需要正式 effect 契约后才能无歧义扩面。
3. 不修改 `runtime/question_shape.py` 对礼貌请求的既有裁决。
4. 不调整商户确认、权限、幂等或支付流程。

## 3. 方案

### 3.1 唯一判据

将内部私有方法 `_question_write_edge_steps` 改名为
`_question_side_effect_steps`，使名字与新的行为边界一致。其输入输出形状不变：输入
`steps + text`，返回应被丢弃的 `Step` 对象列表。

选中条件为：

```text
is_non_directive_question(text)
∧
(
  ((step.deployment == "edge" 或 step.kind == "edge_fast")
   ∧ is_write_intent(step.intent))
  或
  step.require_confirm
)
```

这个判据复用三份已有权威：

- 句形：`runtime.question_shape.is_non_directive_question`；
- 端侧读写：`runtime.intent_effect.is_write_intent`；
- 需确认能力：由 manifest/servers 装配到 `Step.require_confirm`。

不使用“对全部云侧步骤调 `is_write_intent`”的简化方案。当前操作名只将
`query/locate` 声明为只读，会把 `search/menu/talk/status` 等云侧只读能力误判为写。

### 3.2 出口行为

既有出口保持不变：

1. 按对象身份从 `plan.steps` 中删掉选中步骤；
2. 保留其他合法步骤；
3. 记录 `question_write_blocked`；
4. 若已无步骤，调 `_talk_only_plan`，由带安全信号判据的全局兜底 Agent 给出建议；
5. 后续的“安全信号 + 空计划”闸与取消闸的顺序不变。

### 3.3 具体边界

| 输入 / 步骤 | 预期 |
|---|---|
| “红色机油灯亮了还能继续开吗” + `luckin.order(require_confirm=true)` | 丢弃订单步骤，改走安全应答 |
| 同句 + `manual.query` | 保留 |
| 同句 + `info.search` | 保留，不因 `is_write_intent` 的操作名粒度被误杀 |
| 同句 + `chitchat.talk` | 保留 |
| “帮我点一杯生椰拿铁” + `luckin.order(require_confirm=true)` | 保留，继续走确认流程 |
| 安全问句 + `manual.query` + `luckin.order` | 只丢弃 `luckin.order` |
| 普通问句 + 端侧只读步骤 | 保留 |
| 普通指令 + 端侧写步骤 | 保留 |

## 4. 测试设计

实施必须按 TDD 顺序进行。

### 4.1 RED

先在 `orchestrator/cloud/tests/test_question_write_guard.py` 新增以下用例，且在生产代码改动前
单独运行，确认因“云侧 `require_confirm` 步骤未被选中”而失败：

1. 云侧 `luckin.order(require_confirm=true)` 被选中；
2. 完整 `build()` 把安全问句中的 `luckin.order` 替换为 `chitchat.talk`；
3. 混合计划仅删除 `luckin.order`，保留 `manual.query`。

### 4.2 GREEN 与误伤对照

最小实现通过 RED 后，再补齐对照：

1. `info.search` 和 `chitchat.talk` 即使按当前操作名判据会被当作“写”，仍不得被拦截；
2. `require_confirm=false` 的云侧步骤保持旧行为；
3. 带“帮我/麻烦/请”指令标记的瑞幸下单继续可达；
4. 现有四向对照（问句+端侧写 / 指令+端侧写 / 问句+端侧读 / 问句+云侧读）全绿；
5. `question_write_blocked` 观测签名保持。

### 4.3 反向验证

在本批实施记录中留一次反向验证：临时将新增的
`or step.require_confirm` 条件移除，确认新增的云侧安全用例精确转红，既有端侧用例不受影响；然后恢复实现再跑绿。

## 5. 实施面

### 5.1 代码与测试

- 修改 `orchestrator/cloud/planning.py`：判据扩面、方法改名、日志/注释与真实行为对齐。
- 修改 `orchestrator/cloud/tests/test_question_write_guard.py`：RED/GREEN/对照/反向验证用例。

### 5.2 契约与状态文档

- 更新 `docs/architecture/cockpit-agent-architecture.md` §5.2.13：安全问句闸从
  “端侧写”扩到“端侧写 + 需确认的云侧能力”；是对既有章节的校准，不新建第二份安全规则。
- 更新 `docs/conventions.md` 的对应契约段。
- 在 `docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md` 和 `AGENTS.md`
  记录实施终态、本地读数与真栈授权检查点。
- 实施完成后将本文档状态改为“已归档”。

## 6. 验证层级

### 6.1 本地必跑

1. 新增 RED 用例单跑，保留预期失败证据；
2. `python -m pytest -q orchestrator/cloud/tests/test_question_write_guard.py`;
3. `python -m pytest -q orchestrator/cloud/tests/test_planning_safety_talk.py orchestrator/cloud/tests/test_planning_cancel_gate.py`;
4. `python -m pytest -q orchestrator/cloud/tests`;
5. 四道离线门禁：Skill / Exemplar / L0 strict / capability integrity；
6. 按 `AGENTS.md` 固定口径跑全量 pytest，且读数必须属于最后一次改动后的 HEAD。

### 6.2 真栈验证（需单独授权）

本轮不把“本地全绿”写成“真栈已修好”。真栈顺序为：

1. 按部署规则产出干净、已提交、main 可达的 SHA；
2. 推送前列出 `origin/main..HEAD` 全部提交，另取 `git push` 授权；
3. 部署前输出受控路径摘要，另取 deploy `--apply` 授权；
4. 部署后运行精确 release 的 `status` 和统一 `verify`;
5. 干净会话反例 + 原长会话 `information` persona 双层复验；
6. 确认零商户草稿、零挂起操作、清理失败为空。

## 7. 风险与止损

| 风险 | 止损 |
|---|---|
| `require_confirm` 被误用为“全部写操作”的第二份声明 | 文档只主张“需确认的高代价步骤”，不主张它覆盖全部写操作 |
| 云侧只读能力被 `is_write_intent` 误杀 | 云侧分支只看 `require_confirm`，不看操作名 |
| 礼貌请求被判成问句 | 保留 `DIRECTIVE_MARKERS` 判据和两向用例；无标记的问句尾代价延续既有裁决 |
| 只验干净会话，结论再次假绿 | 必须复跑原 `information` 长会话；干净会话只是对照 |
| 发版时带走并行 mobile 提交 | 推送前列出完整 `origin/main..HEAD`，对并行提交取得明示授权 |

## 8. 完成判据

只有同时满足以下条件，这条安全欠账才能划掉：

1. RED 用例在旧实现上按预期失败；
2. 最小实现后，新用例、既有误伤对照、Cloud Planner 回归、离线门禁与全量 pytest 全绿；
3. 精确部署 SHA 的 `status`/`verify` 通过；
4. 干净会话与原长会话双层验证里，安全问句不再进入任何需确认的写能力，且用户拿到分级安全建议；
5. 商户草稿、挂起操作和探针副作用全部归零；
6. `AGENTS.md` 不再把“修复批闭合”写成“QA 全绿”。
