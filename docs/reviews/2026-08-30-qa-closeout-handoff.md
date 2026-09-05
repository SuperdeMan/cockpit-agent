# QA 轮当前交接：已闭合范围、生产证据与剩余活项

> 状态：**开发批与安全主链已闭合，QA 验收仍非全绿**
> 更新时间：2026-09-05（manual-rag 整本范围生产闭合）
> 受众：接手 QA、Planner、Info、语音/TTS 或发布验证的人
> 历史流水：[`docs/agents-history.md`](../agents-history.md)（只追加，不在本页复述逐批过程）

## 1. 一句话结论

探索式 QA 的编号卡、MiniMax 修复批、QA 余项批和安全确认写闸都已完成开发与发布；
当前生产 release 为 `9a3b6f2f08657464c5049a5abf8f6e989e398bce`，`status` 5/5 healthy、
零 warning，统一 `verify` 为 `verified`。manual-rag 已完成整本章节187/187与视觉35/35生产
闭合；安全问句错域、安全 focus 恢复、TTS/barge-in 与测试 warning 仍是独立活项，因此整个
项目仍不得写“QA 全绿”。

## 2. 当前发布与证据边界

| 项目 | 当前事实 |
|---|---|
| 远端 `main` / QA 文档 HEAD | 运行 `git rev-parse origin/main`；允许以纯 docs/test 提交领先生产 release |
| 生产 release | `9a3b6f2f08657464c5049a5abf8f6e989e398bce` |
| 上一生产基线 | `805711cff74b79b23324180ed22f7e026f95e481`；rollback命令未在本轮实际执行 |
| 部署状态 | 5/5 endpoint healthy，零 warning |
| 统一验证 | `verified`；artifact `.artifacts/dev-stack-verifications/20260904T163045Z-9a3b6f2.json`；`e2e_remote_safe`，`minimax:MiniMax-M3` |
| exact-code 全量 | `7861 passed / 34 skipped / 5 warnings`，0 failed；5条warning对应4类已知项，Starlette按2个worker重复 |
| manual-rag | source `ef16d20…e4705d`、v2包`648cdf3…400ed`不变；生产独立章节187/187、视觉35/35、雨刮/背宝剑各3/3，零动作/确认/probe error，车态diff={} |
| 证据边界 | 全量、部署、status、verify与最终手册真栈都绑定`9a3b6f2f`；后续纯docs/test提交不得冒充release |

`423ed23` 与 `a406e22` 是 v1 发布历史；`b3a2aed` 是 v2 首次生产 release；`434a046` 闭合
完整36题并保留最近成功统一verify。旧 release 的单次结果和专项数字不得写成当前证据。

本页所有 `.artifacts/` 路径都是**根仓本地 ignored 证据**，不随 git clone 移植；路径缺失时
必须按本页命令和精确 release 重跑，不能把“文档记过”当成 artifact 仍在。

## 3. 已闭合的 QA 范围

### 3.1 探索式真实用户 QA

- 2026-08-15 的 533 个真实业务轮、58 个问题已经完成去重、根因分类和立卡；
- Q1–Q13、Q5 残余、person-pickup 和编号序列最后一条 I-024 均已完成；
- 根因卡与重判保留在
  [`2026-08-15-qa-exploratory-root-cause-cards.md`](../design/2026-08-15-qa-exploratory-root-cause-cards.md)；
- 原始双模型报告保留在
  [`2026-08-15-exploratory-real-user-qa-deepseek-minimax.md`](2026-08-15-exploratory-real-user-qa-deepseek-minimax.md)。

### 3.2 MiniMax 云端修复批

- 2026-08-26 原始长会话自动读数为 282 PASS / 33 FAIL，但自动 PASS 不等于业务正确；
- C1–C16 六批、M1–M6 余项和 B1–B7 全量收尾已经落地；
- 完整根因、批次设计和真栈读数查
  [`2026-08-27-minimax-qa-root-cause-fix-plan.md`](../design/2026-08-27-minimax-qa-root-cause-fix-plan.md)；
- 原始问题/trace 查
  [`2026-08-26-minimax-cloud-qa-findings.md`](2026-08-26-minimax-cloud-qa-findings.md)。

### 3.3 安全确认写闸

以下执行出口已经共用同一份问句副作用判据：

1. Planner focused/normal build；
2. adaptive replan receiver；
3. Agent `_escalate` mini-plan；
4. fallback capability scan；
5. D0/T2 流式 action 出口；
6. 挂起恢复后的再规划与改派。

`Capability.response_only` 由 manifest 声明，经 Registry round-trip、`Step` 装配和恢复态保持；
Executor 在 dispatch 前拒绝 `response_only + require_confirm`，在 dispatch 后拒绝 action、
`NEED_CONFIRM` 和 `NEED_SLOT`。`safety_origin_text` 由服务端盖章并跨 replan/suspend/restore
保持，LLM `goal/reason` 与当前补槽短句都没有安全授权权威。

专题设计与实现证据查
[`2026-08-30-qa-safety-confirmed-write-guard.md`](../design/2026-08-30-qa-safety-confirmed-write-guard.md)。

### 3.4 Planner null 槽

长会话中 MiniMax 曾输出 `limit:null`；旧装配将它写成字符串 `"None"`，Info Agent
执行 `int("None")` 后返回“Agent 内部错误：ValueError”。release `a729b98` 的修法是在
`_validated_steps` 中把 JSON null 当成“未提供”，同时保留 0、false 和空字符串。

长会话探针也已补上通用守卫：任何以“Agent 内部错误”开头的话术必须判红，不能再藏在
自动 PASS 中。

## 4. 生产复验

### 4.1 安全专项（release `e9fa602`）

`probe_qa_regression.py --group safety --repeat 3`：5 例、15/15 PASS。

- 红色机油灯首轮、跨轮追问和“慢一点开”均给出停车/熄火建议；
- 疲劳驾驶后拒绝提醒仍不允许继续危险驾驶；
- 零动作、零挂起、未进入商户写能力。

Artifact：`.artifacts/dev-stack-verifications/qa-safety-e9fa602-repeat3.json`。

### 4.2 完整 information persona（release `e9fa602`）

官方长会话结果：57/59 PASS、1 warning、零中止、零 cleanup failure、fallback=0、
104 次 LLM 全 pinned。

清理结果：

- 测试提醒完成创建 → 改期 → 取消，最终列表为 0；
- 活动导航最终发出 `navigate_cancel`；
- 零 open operation；
- 全程无商户 intent/预览卡，因此无商户草稿；
- release 首尾均为 `e9fa602`。

Artifact：`.artifacts/dev-stack-verifications/qa-long-information-e9fa602.json`。

### 4.3 新闻专项（release `a729b98`）

3 个干净会话、每个 5 个新闻业务轮：15/15 无 internal error，零中止、零 cleanup failure、
release 连续。Artifact：`.artifacts/dev-stack-verifications/qa-news-repeat3-a729b98.json`。

### 4.4 真实车型手册（历史生产 `434a046`，36 题已闭合）

- 278 页 `SU7用户手册` 生成 269 个文本 chunk、350 个图片放置 / 299 个 blob；v2 包
  `648cdf3d…400ed` 经 shared-model bootstrap 只读挂载，启动决议仍为 approved real provider；
- release `434a046` 的完整 36 题精确落域 36/36、内容 36/36；14 个高风险问法全部 3/3，
  合计 64/64；
- 64 轮均为单一 `manual` 卡、approved real provenance、零 action/need_confirm/probe error；逐轮
  读取完整车态，最终 diff={}；雨刮与安全带俗称继续返回正确手册图片；
- 统一 verify、5/5 endpoint healthy、零 warning；verify artifact
  `.artifacts/dev-stack-verifications/20260903T130534Z-434a046.json`。手册真栈 artifact
  `.artifacts/manual-rag-live-validation/20260903T130655Z-final-434a046.json`，SHA=`3fed8c94…d63a`；
- exact 代码本地全量 7833/34/4，确定性 retrieval 36/36。这里关闭的是原36题基准项，不代表
  整本手册范围全绿；2026-09-04 扩面结果见下节。

### 4.5 整本手册范围复核（历史生产 `7b594f37`）

- 原 PDF 重建与 `.mrag` 逐字一致；离线候选的269页、160索引路径、187 outline叶子、35视觉
  语义、原36题均全绿，证明当前失败不支持“应改向量库”的结论；
- 当前生产自然化187叶子首轮181/187（96.79%）；6条失败后两轮均6/6，全部为2/3，
  无0/3稳定章节失败，但不能写187/187稳定通过；其中哨兵模式首轮有1次opening-handshake timeout；
- 当前生产视觉30/35（85.71%）；位置灯、左右转向、后雾灯、近光灯为0/3稳定零命中；
- 章节主批+失败复验199轮、视觉主批+失败复验45轮均action=0、need_confirm=0、完整26项
  车态diff={}；章节主批有上述1次transport probe error，视觉为零。初版不安全名词短语探针曾发
  一次幂等`hvac.on`，已中止并由双安全预检取代，不能藏掉；
- 原36题当前整批有7条旧表述在联网前被安全预检拒绝，故不报当前36/36；用户点名的雨刮与
  “小人背宝剑”安全问法已各3/3，均返回预期PDF页和图片，零动作、车态不变；
- 本地候选只扩三字caption的视觉语境匹配，并在manifest增加明确SU7手册来源的窄hint；离线
  视觉35/35、显式来源222/222；该候选随后发布并继续修复LLM故障降级，终态见下节。证据与hash见
  `docs/design/2026-09-04-xiaomi-su7-manual-rag-full-coverage-validation-plan.md` §5。

### 4.6 整本手册生产闭合（当前生产 `9a3b6f2f`）

- 0.3.2首次发布到`805711cf`后，五个三字caption稳定缺口消失；但章节主批184/187、视觉
  34/35仍出现4次`Agent 内部错误：RuntimeError`。Trace证明均已落`manual.query`且Agent执行
  失败，不是路由或BM25问题；
- 0.3.3对LLM RuntimeError做一次有界重试，配额/参数/鉴权类不重试；仍失败时返回已检索的真实
  PDF卡与诚实降级话术，ValueError等编程异常继续显式失败。精确代码全量7861/34/5，0 failed；
- 发布后独立章节批 **187/187**，p50=9365.659ms、p95=14446.685ms、max=34710.202ms；视觉
  **35/35**，p50=7886.125ms、p95=13456.285ms、max=21204.482ms；
- `雨刮器怎么打开`与“小人背宝剑”各3/3，分别稳定返回PDF第95/193页与对应图片；所有正式轮
  action=0、need_confirm=0、probe error=0、完整26项车态diff={}；
- 统一verify、5/5 status均通过。章节主artifact SHA=`508756d6…baf80`，视觉SHA=
  `7c05d04a…e0762`，全量日志SHA=`ab40f81e…d91df`，verify SHA=`7a521902…ad44`。

## 5. 当前活项

| 活项 | 当前证据 | 下一步 / 启动条件 |
|---|---|---|
| 安全问句偶尔落 `info.search` | information T24 回答内容安全、零动作，但未走 manual/safety 域，也没有手册 provenance | 单独设计“安全出口 vs 搜索出口”的落域规则；不得只把 `info.search` 加进允许名单洗绿 |
| safety focus 持续阻断后续 charging plan | T47 在机油灯告警后落 `system.clarify`，没有执行错误动作 | 产品裁决：什么证据可以解除安全 focus；“我会靠边”不是“已排除故障” |
| MiniMax TTS 长文本 RPM | 同一 887 字真实回复两次产出可播放 PCM，但末尾均报 `rate limit exceeded (RPM)` | 供应商配额/节流策略；不要继续无界重试 |
| barge-in 在途残帧 | cancel 后仍收到 6144 / 8192 字节，但分别在 16 / 31ms 内关闭 | 明确客户端是否应丢弃 cancel 后缓冲帧；再决定服务端判据是否要求零字节 |
| 全量 warning | `9a3b6f2f`全量5条、4类：Starlette按2个worker重复，另有gRPC fixture、audioop、regex各1 | 与 QA 安全主链分开治理；gRPC 条目是 test-only fixture 债务 |

以上活项是独立问题，不反推安全确认写闸未上线；同样也不能因为安全闸已上线就把它们写成已关闭。

## 6. 证据纪律

1. release、全量测试和真栈 artifact 必须绑定同一个明确 SHA；
2. 邻近 SHA 的单测不能转借给已部署 SHA；纯 docs/test 提交要明确写“HEAD 领先 release”；
3. 探针自动 PASS 不是业务正确，必须读 `fails`、trace、actions、card 和 cleanup；
4. 单次采样不能当基线；有模型方差的轮至少 `--repeat 3`；
5. 回放只证明“当前尺子会如何重判旧话术”，不证明当前系统会生成同样话术；
6. 长会话必须显式 `--expected-sha`，默认 HEAD 在 docs/test 领先 release 时会正确拒绝；
7. 商户写、支付、真实车控、数据删除和系统配置仍要单轮人工授权。

## 7. 接手路径

只处理当前 QA 活项时，按以下顺序：

1. 本页 §2、§5；
2. 安全专题设计 §12：
   [`2026-08-30-qa-safety-confirmed-write-guard.md`](../design/2026-08-30-qa-safety-confirmed-write-guard.md)；
3. MiniMax fix plan 的最终状态与部署后读数；
4. 对应 artifact 和 collector trace；
5. 需要历史原因时再查 `docs/agents-history.md` §84–§88。

不要从已完成的 superpowers implementation plan 继续顺序执行；它们是实施记录，不是当前待办。
