# Android AR06～AR11 接续路线与 Goal 入口

> 状态：**方案草案，2026-09-09 已落盘；六批均未开始实施或验收**。
> 交付对象：后续接手的 Agent、mobile/后端工程师、QA 与产品负责人。
> 源码核对基线：`dc756d9ae9532a0934fdb6ccb9963634ad7ea197`，开工时 `main` 工作树干净、与本地 `origin/main` 相同，`target=cloud`。
> 本轮工作：阅读项目、核对代码与历史证据、拆解方案。未运行云端 status/verify、未读设备安装状态、未构建/装机、未执行业务或创建持续 Goal。
> 本页只管理六批方案的依赖与启动方式；批次状态仍以[原分批页](2026-09-07-android-review-remediation-batches.md)为入口，实施证据写回各批文档。
> **2026-09-10 执行方式更新**：[工程交付 Goal 与集中验收安排](2026-09-10-android-goal-delivery-and-acceptance-plan.md)。默认先连续完成工程与自动验证，真人/特殊设备/最终发布集中处理；正式验收条件保留。

## 1. 接手判断

建议先完成 **AR06 的固定检查与 release 验证入口**，随后推进 **AR07 实验装置、AR08 计时/瓶颈修复、AR09 观测/性能修复**，同时准备 AR10 材料与 AR11 技术规划。工程工作不等待前批全部人工签收；正式 A/B 和整体性能测量仍冻结变量、顺序取数。AR10 正式整体验收在必要修复汇入同一候选后开始；AR11 最终版采用其实际结果。

AR02～AR05 的未签收项没有消失。AR06 可以解除工具和输入阻塞，但不能顺带把权限反例、采集矩阵、多操作或真人体验签收。不要把六批设成一个“持续修改直到全部绿”的无限目标：工程实现、设备实验、真人验收、生产化规划具有不同完成条件。

## 2. 已阅读的系统边界与代码接线

| 系统面 | 当前实现与权威来源 | 对六批的约束 |
|---|---|---|
| 工程与部署 | [AGENTS.md](../../AGENTS.md)、[CLAUDE.md](../../CLAUDE.md)、[构建指南](../guides/android-build-and-device-validation.md) | cloud 档不启本地 Compose；构建镜像/设备串行占用；发布、凭证与系统变更遵守项目授权 |
| 两端一脑 | [架构 §2.4](../architecture/cockpit-agent-architecture.md)、[约定 §9.33/§9.42](../conventions.md) | HMI/mobile 共用消息、确认、请求归属与语音判据；各自 UI、独立会话；改共享件验证两个消费方 |
| 主请求 | `mobile/src/core/session/` → `core/api/gateway.ts` → `gateway/edge/` → edge/cloud 编排 | 身份、requestId、operationId 与取消贯穿链路；UI 反馈不能代替服务端账 |
| 语音输入 | `recorder.ts` → `micBus.ts` → `vad.ts` / `kws.ts` / `handsFree.ts`；原生 `modules/kws/` | 物理麦链路与引擎直灌分栏；KWS 本地判定，ASR/S2S 只在允许的交互窗上行 |
| 语音输出 | `SpeechController` → `TtsSession` → `QueuePcmPlayer`，服务端 `llm-gateway/` | 区分有效文本、PCM 收到、排定起播、声学出声；段链与账号级 RPM 修复保持回归 |
| 应用内呈现 | `AssistantProvider` / `AssistantSurface` / `ProactivePresenter`、`usePresence` | AR04 已提升应用级宿主；支持页浮动在场、空闲光球静帧；不能重新引入常驻两栏 |
| 事实与诊断 | `captureFacts` / `playbackFacts`、`presenceTrail`、`SpeechController.turnReports()`、collector | 复用既有事实，补可关联的只读观测；不另造状态机或放开 prod 自动采集深链 |
| 交付边界 | `mobile/app.config.ts`、`core/config/storage.ts`、`gateway/edge/auth.go` | CNG；内部包/正式签名分开；手填 token 不是正式账号体系 |

已结合[原 Android 方案](2026-08-23-hmi-android-app-plan.md)、[M0～M4 任务与坑账](2026-08-24-mobile-app-implementation-plan.md)、[完整评审 R01～R15](../reviews/2026-09-07-android-ux-full-review.md)、B3/B5 与[语音修复记录](2026-09-05-mobile-voice-broadcast-stutter-plan.md)，核对本次涉及的源码、测试、自动化与构建入口。本文不是全仓代码审计或新一轮 QA 报告。

## 3. 不能继续照抄的历史状态

| 已确认的冲突或缺口 | 接手处理 |
|---|---|
| AGENTS §4.0 与 AR05 发布记录指向 `d425b9c2d6209adbb5ec317d90f862796bde44e7`；QA 交接页顶部及部分 AR04 入口仍写 `573ad46` 为当前生产 | 本轮仅引用“文档记录”。真栈工作前独立 status/verify；不得依据段落较新就冒充实时核实，也不把旧测试数转借 |
| 原分批页 AR05 正文曾写步骤 2～6 未做，顶部却已记录步骤 0～6 实施 | 本轮将该正文指向 AR05 最新实施与逐格签收记录；保留其原始历史章节 |
| AR05 的“真栈闭合 3 格”指若干子项，§9.5 的 V01～V12 没有一行全部子项闭合 | 后续按子项登记 `PASS/FAIL/NOT_RUN`，不以汇总数字判整批通过 |
| Maestro 旧记录有中文输入成功样本，但当前接手记录无可用中文通道 | AR06 先做同工具版本、同包、同设备的小探针；官方仍列 Android `inputText` 非 ASCII 限制，安装 CLI 不等于解决输入 |
| `SpeechController.firstAudioMs` 起于实际发送后的 `begin`；队列播放器回调在排定首片时触发 | AR08 保留旧字段语义，新增端到端和声学校准口径，禁止将它直接解释为说完到听见 |
| KWS 两端当前均为 threshold=0.2、score=2.0，架构写明同模型同阈值 | AR07 的实验参数与生产默认分开；若手机需要独立声学配置，先形成有证据的架构修订，不暗改 HMI 或复制规则 |

## 4. 六份方案及启动顺序

| 批次 | 方案 | 可以先做 | 正式完成的关键依赖 |
|---|---|---|---|
| AR06 | [Release 验证与 lint](2026-09-09-ar06-release-validation-lint-plan.md) | 固定 ESLint、自动化分轨、中文输入预检、包身份检查 | 一个冻结 prod release 在测试机上复现必要流程；CI 改动另按具体方案授权 |
| AR07 | [KWS 独立 A/B](2026-09-09-ar07-kws-ab-validation-plan.md) | 实验配置与计数、协议和语料准备 | 稳定麦链路、真人说话、同包 A/B、选定参数后的 prod 复验 |
| AR08 | [首音时延](2026-09-09-ar08-first-audio-latency-plan.md) | 端到端时间线、分桶语料与离线解析 | 固定声学路径、真实 provider/model、声学首音证据；后端变更须在获准发布后复验 |
| AR09 | [UI 性能与观测](2026-09-09-ar09-ui-performance-observability-plan.md) | 只读有界观测、代码订阅盘点 | AR04 宿主、AR06 验证口径、AR07/08 选定实现固定后重新测基线 |
| AR10 | [固定包整体 UX 验收](2026-09-09-ar10-fixed-release-ux-acceptance-plan.md) | 材料、计分表、设备与任务矩阵 | AR01～AR09 相关修复汇合；五位真人、支持范围及特殊权限/反例条件 |
| AR11 | [M5 交付规划](2026-09-09-ar11-m5-delivery-roadmap-plan.md) | 技术盘点、责任角色、外部依赖与方案取舍 | AR10 实际结论、产品发布范围、必要商业与基础设施决策；本批交付计划而非上线 |

工程顺序：`AR06 → AR07/08/09 工程任务 + AR10 准备 + AR11 技术规划 → 候选冻结`；同一工作树与共享模块修改仍串行集成。正式签收顺序保持 `AR07 → AR08 → AR09 受影响复验 → AR10 → AR11 基线定稿`。真实 KWS 基线/首音声学校准应在装置就绪后集中取得，不能等优化结论写完才测。被测负载改变时，相关 A/B 在新基线重取。

### 4.1 前序未签收项的去向

| 原归属 | 仍需接续 | 六批怎样消费 |
|---|---|---|
| AR01/AR04 | 多个真实 operationId + 本地位置征询并存、指定末项操作 | AR06 提供可用输入；AR10 重验业务台账，结果回填原批，不用画廊销账 |
| AR02 | 视觉失败/超时/中断与并发采集完整设备矩阵 | AR07 先确认本次所用麦链路正确；AR10 核全部已承诺的隐私组合 |
| AR03 | 多段、S2S、主动播报、系统 200% 字号、横屏交叉、盲听 | AR08 补语音分段证据；AR10 进行整机和真人交叉验收 |
| AR04 | 当前宿主上的服务端多操作组合；既有折叠/Keyguard 证据需保留原包身份 | AR09 不重做宿主；AR10 在最终候选重验关键契约，不借旧包全绿 |
| AR05 | §9.5 各行剩余子项；受限 token、旧服务端/Redis 恢复、对话卡片设备证据 | AR06 只解工具阻塞；权限/受控故障依赖仍单列，AR10 入场前核对 |

## 5. 后续 Goal 怎么设

默认采用[工程交付 Goal](2026-09-10-android-goal-delivery-and-acceptance-plan.md)；可以选单批，也可以覆盖六批的工程任务，按依赖穿插推进。每份方案的目标文本区分为：

1. **工程目标**：所有可独立完成的实现、必要检查、工具和授权范围内的构建/设备验证完成；人工/外部项准备成可直接执行的产物。只标工程与已实际执行的验证完成，不标整批签收。
2. **验收目标**：在指定 APK/云端 SHA 上执行该批矩阵、保留原始证据、逐项判定。只有目标要求的指标和证据都齐才可标完成。

AR10 的交付是如实完成验收并给出结论，可以产出“不通过”报告；若用户 Goal 要求“通过”，失败报告不等于达成该 Goal。AR11 的目标是可分工的交付计划，不要求在规划阶段把生产功能全部实现。

目标模板不设置 token 预算、不自动触发 Goal 工具，不把未来授权预先扩展到 push/生产部署、凭证、数据库、删除或系统设置。后续用户明确要求实施该方案时，已有授权范围内持续推进；只有具体步骤命中项目红线或确需真人/外部输入时才交回所缺信息。

缺少外部条件时，先完成独立工作，登记具体所缺条件、可交付产物和接续命令；不无限重复失败，不把 NOT_RUN、模拟输入或替代设备标 PASS。依赖真实声学数据的调参结论不能凭空完成，但不阻塞其他工程任务；只有必要的输入与具体红线动作在其发生前处理，其他人工验收集中到候选稳定后。

## 6. 所有批次共用的证据规则

本次新增数值阈值均是**建议工程门槛**，不是历史已批准指标或已取得结果。后续采样前冻结采用值和场景；需要调整就写新协议版本，不能看过结果后降低旧门槛。

每个验收行至少保留：

```text
case_id / protocol_version / attempt_id / status(PASS|FAIL|NOT_RUN|BLOCKED)
source_sha / test_sha / apk_sha256 / embedded_build / installed_build
device_role / model / Android / viewport_dp / posture / settings_fingerprint
cloud_release_sha / actual_provider_model / network / started_at / ended_at
request_id / trace_id / operation_id / delivery_id（适用时）
expected / observed / evidence_files / side_effects_and_cleanup
failure_or_pollution_reason / next_owner_role / resume_condition
```

- 只保存非敏感配置指纹；凭证不输出、不散列成公开身份，不把原始音频/图像/完整用户话术写入常态诊断。
- 原始输出在仓库外 `%LOCALAPPDATA%\car-agent\artifacts\ARxx-<时间>\`；仓库文档保存摘要、文件名、hash 与重现方法。受控测试素材与回归脚本可入库。
- 模型样本至少 repeat 3；A/B 先记录顺序、缓存、并发与配额状态。失败分母不能被成功重试覆盖。
- APK、服务端、测试与文档 SHA 分栏；换包/改云端/改声学路径开新轮。版本不一致的读数只作历史对照。
- 新扫描门禁必须反向验证会红；行为测试覆盖可观察结果，不能只扫描调用名称。
- 文档改动只做链接、结构和差异检查；实现后按修改面运行 mobile/HMI/后端检查。具体命令见各批与 [AGENTS §6](../../AGENTS.md)。

## 7. 方案交付后的状态回填

每批实施时在文末追加记录，更新[分批页](2026-09-07-android-review-remediation-batches.md)对应行；入口只保留短链接。R15 的完整承诺对账归 AR10，不在此规划批清空历史欠账。最终 Android 当前交接页采用 AR10 的实际候选与结论，替换“下一步”指针时仍保留原实施记录。

本次方案检查：以落盘后的链接/文件检查及 `git diff --check` 为准；未取得任何新的产品 PASS。外部技术资料已在对应方案内就近引用官方来源；执行时仍须核对仓库锁定版本与设备实际行为。
