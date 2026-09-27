# v2 首批实施：R0 基线与 R1 任务结果

> 日期：2026-09-27。授权：用户要求按既定顺序推进，允许提交、推送及必要部署/真栈验证。
> 顺序：[实施方案](2026-09-26-cockpit-agent-v2-implementation-plan.md) PR-A → B → C → D。
> 本页记录实现与证据；目标与排期仍以[路线图](../roadmap.md)为准。

## 1. 当前进度

| 包 | 状态 | 证据 / 下一步 |
|---|---|---|
| CA2-01 / JV00 工具与种子 | 已提交/推送 `81fe1be4`、`fbcc6fa1` | 20 组 regression，独立 synthetic 用户、固定模型和 release、来源树 hash；不是 holdout |
| R0 初始测量 | 两次发现并修复误执行边界；完整小基线已采集 | `d9970d9a`，20 组 ×3、100 测量轮、11 个自动判红轮、零误动作/车态变化，见 §2.5 |
| R0 问句写闸补口 | 已发布并完成三次采样 | `634c2878` 纯定义问、`d9970d9a` 限定解释句；不把旧中断趟拼入完整基线 |
| CA2-02 | 已发布，小集真栈通过 | `e2632e64`；[步骤范围](2026-09-26-v2-step-input-scope.md) |
| CA2-03 | 已发布，小集真栈通过 | `ce3a2632`；[任务身份](2026-09-26-v2-task-identity.md)，云侧全族 2120 passed / 1 skipped |
| CA2-04 | 已发布，原丢卡样本 9/9；设备验收待补 | [结果集合](2026-09-27-v2-result-bundle.md)；原确认授权、最小恢复存储与短 TTS 保持 |

## 2. R0 必须先修的已重现问题

### 2.1 证据与根因

正式前测：runner `fbcc6fa1`、release `5a2f4c9d674fb27c98539f1d57ff8cc93bca4c76`、
请求 pin `minimax:MiniMax-M3`。V201–V203 的手册 Agent 都实际调用，但挂确认时无手册卡，
与 CA2-04 的结构性缺口一致；暂不把话术含答案判成结果完整。

V207：“露营模式是什么意思？”（trace `57cbbee135fd48f397df97ebb9926b04`）。
模型 goal 写“解释”，步骤却是 `scene.activate`；用户收到场景激活确认卡与动作提议，
27 项车态没有变化，未确认执行。探针停止后已点名取消 `op-e3e94cf230bf44e9`，收到 closed_operation_ids。
原测量保存为 `.artifacts/v2-runtime/baseline-5a2f4c9d-02.json`，取消另记 post_stop_cleanup，不改写原失败。

根因：已有闸按端侧写 / manifest require_confirm 选候选；scene.activate 声明 effect=write，
确认由 Agent 依据具体场景动态提出，因此未入原候选。规范问已扩展 effect，但纯定义问未扩展。

### 2.2 最小修法与边界

- 在 `runtime.question_shape` 复用既有定义句形新增 `is_definition_only_question`；所有分句都须为问句。
- 只为这类句子把已有云侧问句写闸扩到声明 effect=write 的步骤，各 dispatch-bound 消费方沿原链复用。
- 显式研究/介绍/搜索任务保留原行为：目前 effect 同时含改状态与创建信息任务，不能一刀切。
  方法问、条件指令、混合问/做不因本补口扩大为全禁；更细的副作用契约归 CA2-05。
- 原话授权、确认、VAL、response_only 均不变；不新增 Agent 路由或模型调用。

先增加五个定义问集成反例：旧代码 5 failed；改后相关 315 passed。
追加混合句/标题/显式工作边界后定向 291 passed（含量尺测试）。
全量固定口径：9968 passed / 4 failed / 32 skipped / 11 warnings，363.97 s；四条失败均在
`test_run_go_tests_wrapper.py` 的 Windows PowerShell 子进程：继承 PowerShell 7 模块路径时找不到 Get-FileHash。
仅该测试子进程的 PSModulePath 指定 Windows 自带模块目录后，原文件 13/13 通过；没有改测试断言或系统配置。
不将两次运行合写成一次“全量全绿”。日志 `.artifacts/v2-runtime/r0-definition-full-suite.log`。
四门禁通过（skills/exemplars/L0 strict/capability integrity），smoke_edge 13/13。
现有 1179 条语料离线扫描有 34 条命中新定义问范围，端侧原判据未修改。
`634c28786360a7b297d197d9d81fdd84308bb821` 已于 2026-09-26 发布，运行 SHA 对齐、status 5/5 零 warning，
统一 verify `verified`（`20260926T103016Z-634c287.json`）。三次基线样本随后回填。

### 2.3 量尺修正

首趟 `baseline-5a2f4c9d-01.json` 缩掉 navigation/media/profile 权限，使 scene 的 Agent 级准入提前拒绝；
旧车态检查又未收后视镜加热字段，27 键被误判不完整。这趟仅为仪器诊断，不能作产品失败基线。
已恢复普通非交易权限面，仍无 merchant.write/payment.invoke；所有定义用例都禁止自动确认。
共享 `_settled_vehicle_state` 增 `include_unmanaged` 只读全信号档，原恢复车辆口径不变。
相关量尺测试 101 passed；新增缺口按失败→仅取消已知挂起→停测，不自动发反向车控。

正式补测前澄清 V207 的业务判据：露营模式也可能指本项目内置场景，不能强制其一定走车书。
seed revision `2026-09-26b` 改为回答含主题、零动作、无未请求的确认；其它用例不变。
探针也将“未请求却出确认卡”单独判红，即使 actions 为空。旧 raw artifact 和旧语料 SHA 保留，不能拼成同一冻结集成绩。

### 2.4 V208：否定前缀之后的“只解释”被端侧执行

`634c2878` 修后测量的 V207 已零动作/零确认；V208
“别打开车窗，只解释怎么打开车窗”在端侧拆分后将第二段执行成 window.open，模拟 VAL 的 window 发生变化。
trace `49307834d3c34dc6a92276245a217fcd`，artifact `.artifacts/v2-runtime/baseline-634c2878-01.json`。
探针立即停止，无挂起，无自动反向车控。该趟不能计为完整基线。

根因是共享解释句形前缀漏“只/仅/仅仅”，混合拆分的第二段因此被当操作。
修法只扩原解释句形；全句只含否定前缀和解释分句时也按解释处理，含另一条正向指令时保留原行为。
端侧和云侧继续共用一份判据。五条新增反例在旧代码上全红；改后定向 343 passed。
本轮全量：9986 passed / 32 skipped / 11 warnings，331.24 s（本测试进程使用 Windows 自带模块路径，未改系统配置）；
四门禁全过、smoke_edge 13/13。日志 `.artifacts/v2-runtime/r0-explanation-full-suite.log` 与 `r0-explanation-gates.log`。

量尺同时补强：每轮保存完整 vehicle_before/vehicle_after，而非只有差异键。
旧 artifact 没有保存具体前态值，不声称已精确恢复原态；后续部署重建模拟 VAL 后重新冻结完整状态。
这里只涉及本项目内存模拟 VAL，没有连接真实车控总线。
`d9970d9a5f19507381ff3658c23101e2a84d61c3` 已发布；status 5/5、运行 SHA 对齐、
verify `verified`（`20260926T112324Z-d9970d9.json`）。推送曾遇 GitHub 连接超时，带时限重试后成功；未重复 apply。

### 2.5 完整小基线：先固定结构性缺口与规划方差

release `d9970d9a5f19507381ff3658c23101e2a84d61c3`；runner
`9aa5216bd09a848785d0d942c2ce4d76c38fc971`；请求固定 `minimax:MiniMax-M3`。
20 个 regression 用例各独立重复 3 次，共 60 个 case run、100 个测量轮（含点名取消）。
11 个轮次自动判红、零证据缺失、零动作、27 项车态无变化、零残留挂起；release 连续且 runner 未变。

| 原红 | 三次采样 | 归因与承接 |
|---|---|---|
| V201/V202/V203 首轮 | 每条 3/3 缺手册卡，共 9 轮 | 手册已调用、挂确认后未完整呈现；CA2-04 |
| V207 第 3 次 | 字面回答缺“露营”，无动作/确认 | 09-27 逐句复核：已描述过夜、通风和供电，是关键词误报；保留 raw 判红 |
| V211 第 3 次 | 未调用手册、缺卡与 2.9 数值 | 规划方差；一次失败有多个断言，不重复计作多个失败轮 |

请求到 final 的 p50/p95/p99 为 4656.5/10828/13328 ms；含取消和探针 0.6 s 尾部等待，
不是纯模型时延或新 SLA。分位数取排序后 `floor((n-1)*q)`。

artifact：`.artifacts/v2-runtime/baseline-d9970d9a-01.json`，SHA-256
`3ecf83ba34325c596b9de0b2c4ddff8a67120cf12c54ea7ac4b222766e23f985`；
corpus `814db2246d6c8e564bf50f9616ff990d612f5b5f6f414f792bfe4e458f3b6b08`，
assets `687bb2b186317b25e875ab95ed5ee9ee0f05765929a161403bd29c6170af284b`。
它是首批迁移前测，非 holdout、非 200 旅程总验收；APK、全量运行配置证明、Jev 中文收益仍未测。

## 3. 第一批契约实施边界

CA2-02 复用现有分句与 step_grounding，步骤业务读取范围与 safety_origin_text 的授权范围分开。
CA2-03 的服务端标识与来源关系沿现有 Plan/step_record/SessionState/活动任务帧保存；
未知语义覆盖明确 unknown，不根据步骤反推用户只提了这些诉求。

CA2-04 保留完整已呈现结果、卡片与引用，并将 TTS 摘要作为独立投影。
当前 `_resume_result` 刻意剥离自由文本/旧卡/动作，不能直接撤销这一隐私与防重约束；
完整答案走正常会话呈现/历史路径，恢复执行只保留必需依赖与事实引用。
HMI/Android 共用结果选择器与版本规则，各自渲染；旧客户端和旧记录均需明确兼容。


## 4. 首批合入与部署阻断（2026-09-27）

本节保留首次容量受阻与授权清理的过程；清理重试后的当前发布和业务证据见 §5。

候选 `023639328912ffbfd58b5dc3710349be89cb59e3` 已推送 main；其父实现提交为
`f4d3b8d73ff31a5a6b42b841d41ed5da314d6130`。代码、测试与运行证据分栏：

| 验证面 | 精确版本 / 结果 |
|---|---|
| 后端完整测试 | `02363932`，10034 passed / 32 skipped / 11 warnings，657.81 s；工作树全程冻结 |
| HMI | `f4d3b8d7` 正式构建 + 350/350；与候选 hmi tree 均为 `95d9ed3f6db4c185d0800abb9de1195d14cd2180` |
| Android | `f4d3b8d7` 112 suites / 1157 tests、typecheck、lint 全过；与候选 mobile tree 均为 `2d6df3f6230b8f59ce8edae8071038051aa996e7` |
| 网关 | `f4d3b8d7` 四个 gateway 包测试通过；与候选 gateway tree 均为 `29e1b509b358bd514fa2d97ef25a557b4c223a7e` |
| 四门禁与 edge smoke | `f4d3b8d7` 全过；不转写成候选 SHA 的另一趟测试 |
| Android 设备 | 预检 16 pass / 2 warn；无 USB 设备、Android Tailnet 设备离线。本批未构建/安装/验收 APK |
| 云端部署 | 候选 dry-run 无 blocking_changes；apply 返回 runtime failure，独立容量检查复现 insufficient disk capacity，尚未切换 |
| 线上保持 | 独立 status 仍为 `d9970d9a`，release/running 一致、5/5 healthy、零 warning；不是候选验证结果 |

证据清单 `.artifacts/v2-runtime/r1-verification-manifest-02363932.json`，SHA-256
`dba29c4c86c822325c943414d1e5ab884da6a8f343cdeccfd9e3418d9702e85c`；各日志摘要在清单内。
首跑 `f4d3b8d7` 的单条商户租约时序失败与隔离工作树的 Android 超时仍保留，不改写为首次全绿。

云端可用约 28.72 GiB，小于受控构建入口的 30 GiB 门槛；dry-run 的无源码阻断不等于容量门通过。
已准备固定清单：14 份旧构建的 src/归档、9 份旧上传包，共 51 个路径、2.754 GiB。
只清理源码/归档，保留所有 release、回滚镜像、当前/前一版构建、构建 manifest/checksum/image inventory、存储和其他项目。
候选 `.artifacts/v2-runtime/deploy-cleanup-proposal-02363932.json` 的 SHA-256 为
`a4f2178eb966a0685b46a9236d32a350af7ff1b4e4b3934ba9743af952549adc`；用户已对这 51 个精确路径单独授权；清理完成，72 份保留元数据的 hash 未变，
current 仍是旧版，磁盘由 30,792,359,936 增至 33,749,192,704 bytes（约 31.43 GiB）。
结果 `.artifacts/v2-runtime/deploy-cleanup-result-02363932.json`；未删除发布版本、镜像或存储。

已完成授权与 current/占用/路径边界检查、清单内清理；接续：原 SHA dry-run/apply →
独立 status/verify → 同 20 组语料 repeat 3，并读取新增结果快照与取消前后身份。不能降低容量闸或用旧样本填新 release。
下一包仍按路线图 CA2-05；只读盘点已有 53 项云侧能力，其中 30 项未显式声明 effect，需结合加载器缺省逐项裁决。


## 5. 发布后固定语料复验（2026-09-27）

生产 `023639328912ffbfd58b5dc3710349be89cb59e3`；独立 status 为 ok、5/5 healthy、零 warning，
release/running SHA 一致。统一 verify 为 verified：`20260927T023202Z-0236393.json`，
`e2e_remote_safe` / `minimax:MiniMax-M3`。这次包含 CA2-02–04，不代表全部 v2 已验收。

后测 runner 为 `845e240e1763f37dbe1118f5db6ae1f0bee3afd9`；同一语料 hash、同一模型，
20 组 ×3 = 60 个 case run、99 个测量轮，零 evidence failure、零动作、27 项车态零差异、零 open operation，
release 连续且 runner 未变。比前测少的 1 轮是 V206 r2 不再有挂起，因此自动取消清理被跳过；业务问题没有删减。

| 检查面 | 结果 / 边界 |
|---|---|
| 原结构性丢卡 | V201/V202/V203 首轮 9/9：手册完整正文与卡均保留，同时维持后备箱待确认 |
| 结果/身份协议 | 15 次关闭挂起的 task_id/来源引用保持、revision 递增；9 份已完成兄弟结果只带 reference，不丢失、不重播；附加协议核对零错误 |
| 真实来源 | 本集手册卡 `source_type=manual`、`_prov.mode=real`；车辆仍是模拟 VAL，未接实车总线 |
| 原始自动判定 | 2 个红轮；保留原始 verdict，不改 artifact、不把退出码 0 当全绿 |
| 逐条业务复核 | 1 个规划失败、1 个关键词误报，详见下表；不按 99/100 的不同分母宣布整体提升比例 |
| 时延 | p50/p95/p99 = 4719/11203/12484 ms，仍含取消与 0.6 s 尾部等待；不是严格性能 A/B 或 SLA |

| 样本 / trace | 原始结果 | 复核 |
|---|---|---|
| V215 r1 t2，`1d9e7e99b3754e9fbce63a0d3cd6e58f` | manual_not_dispatched / manual_not_presented | “座椅加热在哪里打开？”之后追问“它有几档？”；模型 `steps=[]` 转闲聊，泛答加热/通风档位，没有查手册。另两遍通过。仅记录，尚未修复此规划方差 |
| V201 r3 t3，`cfd94f076b5247fdb36d0b15467ffb5a` | answer_missing:空调 | 取消后问“刚才你说空调有哪些模式？”；完整复述自动、制冷/制热、吹风、循环、同步与通风功能，只未重复主题名。回忆有效，字面判据误报 |

同时复核前测 V207 r3（`771031a26c904229bc55387f304fc62d`）：已描述空调、通风、供电与过夜用途，
只是没有“露营”字样，也属关键词误报；前测真正残余仍包括 V211 未派手册与 9 轮丢卡。
字面关键词判据的这两个局限本轮仅记录；量尺改版应另冻结版本，历史 artifact 重算只能标回放。

证据（均为本机 ignored 工件，clone 不自带）：

- 原始后测 `.artifacts/v2-runtime/baseline-02363932-01.json`，SHA-256
  `85ef11a74ff2c5fe95e2f196dd18a37363bd12146cd7f715ed61331e6e3afeae`。
- 附加协议核对/人工复核 `.artifacts/v2-runtime/result-contract-checks-02363932.json`，SHA-256
  `12b6f9526f813f779d5185f45cd6b659fc49226b8d2a9fff94c71fb4474f3fcd`。
- corpus `814db2246d6c8e564bf50f9616ff990d612f5b5f6f414f792bfe4e458f3b6b08`；
  本 release assets `dafdf11a6b63e7cd280e977ae90661115f5c5528e05007d4d5aa4949ec1aef4e`。

首批 A–D 已形成可运行、可复核的迁移闭环。下一包按路线图推进 CA2-05，再 CA2-06+12、CA2-07；
指代规划残余、量尺误报、Android 新包/设备、跨端历史与扩展故障矩阵继续分项登记，不能写成 QA 全绿。
