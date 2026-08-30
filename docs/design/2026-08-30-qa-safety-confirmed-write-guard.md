# QA 收尾：安全问句禁止进入需确认的云侧写能力

> 状态：**落地中（本地验证完成，待 push/deploy/真栈）**（2026-08-30）
> 交付对象：Cloud Planner / QA 探针维护者
> 关联：`AGENTS.md` §4.1/§4.2、`docs/agents-history.md` §84/§85、
> `orchestrator/cloud/planning.py::_question_side_effect_steps`、
> `orchestrator/cloud/planning.py::_apply_question_side_effect_guard`、
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

旧现场中的 C1 安全闸为：

```text
非指令问句 ∧ 端侧步骤 ∧ 写意图 ⇒ 丢弃该步骤
```

旧实现意图在丢弃后交给全局兜底回答；v1.45 已将它收紧为“只接受 unconfirmed talk，
没有合格能力时保持空计划 fail closed”。

`luckin.order` 是 `deployment=cloud` 且 `require_confirm=true`，因此绕过了只看
`deployment/kind` 的第一道闸。

## 2. 目标与非目标

### 2.1 目标

1. 用户是在询问、而不是下指令时，不得进入任何需二次确认的云侧步骤。
2. 保留现有端侧写闸全部行为，包括其已明记的礼貌问句代价。
3. 不误伤 `manual.query`、`info.search`、`chitchat.talk` 等云侧读取或应答步骤。
4. 混合计划只丢弃违规步骤，已合法的读取/安全应答步骤保留。
5. 沿用 `question_write_blocked` 观测签名；仅在存在 unconfirmed talk capability 时回答，
   否则以空计划 fail closed。

### 2.2 非目标

1. 不在本批给全部云侧 capability 新增 `effect=read|write` 契约。
2. 不拦截所有 `require_confirm=false` 的云侧副作用；它们需要正式 effect 契约后才能无歧义扩面。
3. 不修改 `runtime/question_shape.py` 对礼貌请求的既有裁决。
4. 不调整商户确认、权限、幂等或支付流程。

## 3. 方案

### 3.1 唯一判据

当前内部私有方法是 `_question_side_effect_steps`（设计时旧名为 `_question_write_edge_steps`），
名字与扩展后的行为边界一致。其输入输出形状不变：输入 `steps + text`，返回应被丢弃的
`Step` 对象列表。

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

所有 `build()` 出口统一经 `_apply_question_side_effect_guard` 终结：常规出口与 focused
deterministic early return 不得各写一份安全判据。终结器行为为：

1. 按对象身份从 `plan.steps` 中删掉选中步骤；
2. 保留其他合法步骤；
3. 记录 `question_write_blocked`；
4. 若已无步骤，只尝试 `_talk_only_plan`；仅当它返回经 manifest 装配且
   `require_confirm=false` 的 talk 步骤时补回答；
5. 找不到合格 talk 时保持空计划 fail closed；focused 被拦后绝不再走 registry fallback；
6. 常规出口后续的“安全信号 + 空计划”闸与取消闸顺序不变。

兜底构造同样受 manifest 权威链约束：`_talk_only_plan` 与 registry fallback 产生的
`Step` 必须经 `_validated_steps` 装配，不能手工构造后把 `require_confirm` 丢成默认假值；
`_talk_only_plan` 若选中的 capability 本身 `require_confirm=true`，必须返回 `None`，
不能在主守卫之后重新引入需确认副作用。

### 3.3 具体边界

| 输入 / 步骤 | 预期 |
|---|---|
| “红色机油灯亮了还能继续开吗” + `luckin.order(require_confirm=true)` | 丢弃订单步骤；有 unconfirmed talk 时回答，无则空计划 fail closed |
| 同句 + `manual.query` | 保留 |
| 同句 + `info.search` | 保留，不因 `is_write_intent` 的操作名粒度被误杀 |
| 同句 + `chitchat.talk` | 保留 |
| “帮我点一杯生椰拿铁” + `luckin.order(require_confirm=true)` | 保留，继续走确认流程 |
| 安全问句 + `manual.query` + `luckin.order` | 只丢弃 `luckin.order` |
| focused 早退 + 问句形态写步骤 | 走同一终结器；被拦后不得 registry fallback |
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

质量审查追加 focused RED：构造 focused early return 的问句写计划，并让 registry 返回
confirmed cloud capability；旧实现会在拦截后重新走 registry，把 `luckin.order` 引回计划。

### 4.2 GREEN 与误伤对照

最小实现通过 RED 后，再补齐对照：

1. `info.search` 和 `chitchat.talk` 即使按当前操作名判据会被当作“写”，仍不得被拦截；
2. `require_confirm=false` 的云侧步骤保持旧行为；
3. 带“帮我/麻烦/请”指令标记的瑞幸下单继续可达；
4. 现有四向对照（问句+端侧写 / 指令+端侧写 / 问句+端侧读 / 问句+云侧读）全绿；
5. `question_write_blocked` 观测签名保持；
6. `_talk_only_plan` 与 registry fallback 经 `_validated_steps` 后保留
   `require_confirm`，confirmed fallback 被拒绝，普通 fallback 行为不变；
7. focused 与常规出口都调用 `_apply_question_side_effect_guard`；focused 被拦后 registry
   调用次数为零；缺失或 confirmed talk 时得到空计划。

### 4.3 反向验证

在本批实施记录中留一次反向验证：临时将新增的
`or step.require_confirm` 条件移除，确认新增的云侧安全用例精确转红，既有端侧用例不受影响；然后恢复实现再跑绿。
另对 fallback 做同形反向验证：临时恢复手工 `Step` 构造，确认 registry fallback 与
`_talk_only_plan` 的 confirmed capability 用例精确转红；恢复 `_validated_steps` 装配后再跑绿。
质量审查再补一轮 focused 反向验证：恢复旧 focused 分支的“拦截后 `_fallback`”行为，
确认 `test_focused_question_does_not_bypass_confirmed_fallback_guard` 精确转红；恢复统一终结器后
守卫全文件转绿。

## 5. 实施面

### 5.1 代码与测试

- 修改 `orchestrator/cloud/planning.py`：判据扩面、方法改名、日志/注释与真实行为对齐；
  抽取 `_apply_question_side_effect_guard` 作为 focused + normal 所有 build 出口的统一终结器；
  focused 被拦后不再 registry fallback；`_talk_only_plan` 与 registry fallback 统一经
  `_validated_steps` 装配，并拒绝 confirmed talk，零步无合格 talk 时保持 fail closed。
- 修改 `orchestrator/cloud/tests/test_question_write_guard.py`：RED/GREEN/对照/反向验证用例。

### 5.2 契约与状态文档

- 更新 `docs/architecture/cockpit-agent-architecture.md` §5.2.13：安全问句闸从
  “端侧写”扩到“端侧写 + 需确认的云侧能力”；是对既有章节的校准，不新建第二份安全规则。
- 更新 `docs/conventions.md` 的对应契约段。
- 在 `docs/design/2026-08-27-minimax-qa-root-cause-fix-plan.md` 和 `AGENTS.md`
  记录实施终态、本地读数与真栈授权检查点。
- 完成判据全部满足后将本文档状态改为“已归档”。

## 6. 验证层级

### 6.1 本地必跑

1. 新增 RED 用例单跑，保留预期失败证据；
2. `python -m pytest -q orchestrator/cloud/tests/test_question_write_guard.py`;
3. `python -m pytest -q orchestrator/cloud/tests/test_planning_safety_talk.py orchestrator/cloud/tests/test_planning_cancel_gate.py`;
4. `python -m pytest -q orchestrator/cloud/tests`;
5. 四道离线门禁：Skill / Exemplar / L0 strict / capability integrity；
6. 按 `AGENTS.md` 固定口径跑全量 pytest，且读数必须属于最后一次改动后的 HEAD。

### 6.2 真栈验证（需单独授权）

本轮不把“本地验证完成”写成“真栈已修好”。真栈顺序为：

1. 按部署规则产出干净、已提交、main 可达的 SHA；
2. 推送前列出 `origin/main..HEAD` 全部提交，另取 `git push` 授权；
3. 部署前输出受控路径摘要，另取 deploy `--apply` 授权；
4. 部署后运行精确 release 的 `status` 和统一 `verify`;
5. 干净会话反例 + 原长会话 `information` persona 双层复验，并证明部署中的默认 chitchat
   配置确实给出分级安全建议；
6. 确认零商户草稿、零挂起操作、清理失败为空。

## 7. 风险与止损

| 风险 | 止损 |
|---|---|
| `require_confirm` 被误用为“全部写操作”的第二份声明 | 文档只主张“需确认的高代价步骤”，不主张它覆盖全部写操作 |
| 云侧只读能力被 `is_write_intent` 误杀 | 云侧分支只看 `require_confirm`，不看操作名 |
| fallback 手工构造 `Step`，把 manifest 的确认字段丢成默认假值 | 所有 fallback 统一经 `_validated_steps`；confirmed talk 明确返回 `None`，并用反向验证锁住 |
| focused early return 绕过常规出口，或被拦后 registry 把 confirmed capability 引回 | focused + normal 共用 `_apply_question_side_effect_guard`；focused 被拦后禁止 registry fallback |
| 把“安全回答”误写成必然结果，配置缺失时为出话术重新放宽安全边界 | 只接受 unconfirmed talk；缺失或 confirmed 时空计划 fail closed；默认 chitchat 的分级建议留给部署验收 |
| 礼貌请求被判成问句 | 保留 `DIRECTIVE_MARKERS` 判据和两向用例；无标记的问句尾代价延续既有裁决 |
| 只验干净会话，结论再次假绿 | 必须复跑原 `information` 长会话；干净会话只是对照 |
| 发版时带走并行 mobile 提交 | 推送前列出完整 `origin/main..HEAD`，对并行提交取得明示授权 |

## 8. 完成判据

只有同时满足以下条件，这条安全欠账才能划掉：

1. RED 用例在旧实现上按预期失败；
2. 最小实现后，问句守卫、fallback 权威字段、focused/normal 统一终结器、既有误伤对照与
   相邻规划用例通过；Cloud Planner 全族、四道离线门禁与全量 pytest 均 rc=0，且读数绑定
   明确的代码与契约 SHA；
3. 精确部署 SHA 的 `status`/`verify` 通过；
4. 干净会话与原长会话双层验证里，安全问句不再进入任何需确认的写能力，用户拿到分级安全建议；
5. 商户草稿、挂起操作、探针副作用与清理失败全部归零；
6. `AGENTS.md` 不再把“修复批闭合”写成“QA 全绿”。

## 9. 核心实现记录（本地验证完成，待真栈）

核心实现已分四笔提交落地：

1. `a83fa88`：`MockAgent.require_confirm` 改为显式 bool，避免 `MagicMock` 恒真污染确认边界测试；
2. `ab88f4e`：落地 `_question_side_effect_steps`，在非指令问句下拦截
   `(edge/edge_fast ∧ is_write_intent) ∨ Step.require_confirm`，接入当时两处常规调用点；
   `plan_mode` 继续使用 `question_write_blocked`，并覆盖 confirmed cloud、正常指令、
   unconfirmed cloud 与 mixed 计划；
3. `01cc57c`：质量审查发现 fallback 手工 `Step` 丢失 `require_confirm`，将
   `_talk_only_plan` 与 registry fallback 收敛到 `_validated_steps`，并让 confirmed talk
   明确拒绝，闭合守卫之后的旁路；
4. `1105829d518c87732c963d0b7672731e08e2319a`：第二轮质量审查发现 focused early return
   绕过常规终结路径，且旧分支在拦截后会走 registry fallback；抽取
   `_apply_question_side_effect_guard` 供 focused + normal 共用，focused 被拦后不再 registry，
   零步只接受 unconfirmed talk，否则保持空计划 fail closed。

TDD 与反向验证证据保存在 gitignore 的本地 artifact 中：

- `.artifacts/qa-safety-confirmed-write/tdd-red-old-guard.log`：旧守卫下 3 条 cloud confirmed
  用例按预期转红（3 failed / 4 passed / 22 deselected）；
- `.artifacts/qa-safety-confirmed-write/tdd-green-restored.log`：恢复实现后 29 passed；
- `.artifacts/qa-safety-confirmed-write/tdd-red-edge-control.log`：反向条件下既有端侧四向对照
  4 passed，证明新增 cloud 条件没有替代旧 edge 判据；
- `.artifacts/qa-safety-confirmed-write/fallback-red-old-construction.log`：恢复 fallback 手工构造后
  2 条权威字段用例按预期转红；
- `.artifacts/qa-safety-confirmed-write/fallback-green-restored.log`：恢复 `_validated_steps` 后
  31 passed；
- `.artifacts/qa-safety-confirmed-write/focused-fallback-red.log`：恢复旧 focused fallback 后，
  confirmed registry capability 被重新引入，目标用例 1 failed / 31 deselected；
- `.artifacts/qa-safety-confirmed-write/focused-fallback-green.log`：恢复统一终结器后 32 passed。

这些 ignored artifact 只是本地 RED/GREEN 运行记录，**不是 commit 证据**；可追溯实现仍以
上述四个生产代码提交为准。两轮文档收口为 `660153a` / `dfad687`，测试绑定审计对象是
`dfad68730b50d094993c328d33cb774d29642e16`。

## 10. 本地审计证据与剩余边界

### 10.1 审计读数

| 层级 | 命令 / 结果 |
|---|---|
| targeted | `test_question_write_guard.py` + 相邻规划/安全/取消/focus：**159 passed**，2.77s，rc=0 |
| Cloud Planner 全族 | `python -m pytest -q orchestrator/cloud/tests`：**1235 passed / 1 skipped**，60.81s，rc=0 |
| 四道离线门禁 | Skill **22/22**；Exemplar **314**；strict discovery **85/85**（cases=676, distinct=634）；gate **25/25**（cases=139, distinct=129）；capability PASS；全部 rc=0 |
| 全量 | `python -m pytest -q -n 8 --dist worksteal`：**7723 passed / 32 skipped / 13 warnings**，449.89s，rc=0 |

`7723 - 7712 = +11`，准确来自 `test_planning.py` +1、
`test_question_write_guard.py` +10；没有把已部署 `343934b` 的 7712 证据转借给本地候选。

### 10.2 manifest、哈希与可移植性边界

本地验证清单是
`.artifacts/qa-safety-confirmed-write/verification-manifest-utf8.json`，自身 SHA256=
`add919e7a16a838b700b45a1a0b6767226fc28df8a6138ac9d125c927889fc65`。可读 blocking artifact
`blocking-gates-readable-utf8.log` 的实际 SHA256=
`7f47f1c048f8d101445648f275e8401d998876f814d0c0732571caf5f1aac06a`。
manifest 与其列出的日志均在 `.artifacts/`，是 **ignored/local-only** 证据，不是可移植的 commit
证据；deprecated 乱码 raw 不作权威来源。

### 10.3 warning 定性

全量共 **13 warnings**：StarletteDeprecation×8、WordPiece Deprecation×2、gRPC
`UnaryUnaryCall._invoke was never awaited` Runtime×1、audioop Deprecation×1、regex Future×1。
其中只有这 1 条 gRPC RuntimeWarning 已由 `warning-investigation-utf8.log` 稳定定性并固化
（SHA256=`a1a95fd4c9bee3ce7814482d5430f8e6d87414694274416b61331a2f84cc6067`）：
trip 测试 fixture 跨多个 `asyncio.run` 复用 loop-affine gRPC channel；测试绑定代码范围
`15ff116..dfad687` 相关 diff 为空。其余 12 条按原始类别保留，本轮未逐条消除。
当前证据支持该 gRPC warning 是“pre-existing、test-only 独立债务”，没有证实生产持久 loop
受影响；反过来也**不能用这份定性声称生产安全已证明**。修 test fixture 另立，不归本安全闸生产回归。

### 10.4 完成判据状态

- 判据 **1–2 已满足**：旧实现 RED、三层最小实现、误伤对照与反向验证均有本地证据。
- 判据 **3–5 仍保持 pending**：尚未 push/deploy，未取得精确新 SHA 的 `status` + 统一
  `verify`/remote-safe；未跑干净会话 3/3 与原 `information` persona；未核验零商户草稿、
  零挂起、零探针副作用与清理失败为空。
- 判据 **6 已满足**：`AGENTS.md` 已明确分开“既定开发批闭合”与“QA 验收未全绿”。

当前云端只读复核仍是 `343934bab66c23f83575cee998eb6f64a9f45f3e`：`status` ok、
5/5 healthy、零 warning；本地候选不在其中。本轮只读状态 artifact
`cloud-status-predeploy-343934-utf8.log` SHA256=
`890ccdcffc3fc9d6f6d15ab1617fd6c2f4df7802448306d824d15a7f3ab9e761`。

因此当前只能写“本地验证完成，待 push/deploy/真栈”，不得写成“云端已修”“QA 全绿”或
该候选已经 `verified`。
