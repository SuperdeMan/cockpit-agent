# v2 首批实施：R0 基线与 R1 任务结果

> 日期：2026-09-26。授权：用户要求按既定顺序推进，允许提交、推送及必要部署/真栈验证。
> 顺序：[实施方案](2026-09-26-cockpit-agent-v2-implementation-plan.md) PR-A → B → C → D。
> 本页记录实现与证据；目标与排期仍以[路线图](../roadmap.md)为准。

## 1. 当前进度

| 包 | 状态 | 证据 / 下一步 |
|---|---|---|
| CA2-01 / JV00 工具与种子 | 已提交/推送 `81fe1be4`、`fbcc6fa1` | 20 组 regression，独立 synthetic 用户、固定模型和 release、来源树 hash；不是 holdout |
| R0 初始测量 | 中断，不能称基线完成 | `5a2f4c9d` 第一次样本为量尺诊断；修正后跑到 V207 发现纯定义问触发 scene.activate 确认卡 |
| R0 问句写闸补口 | 已发布 `634c2878`，待专项样本 | dry-run 零阻断、status 5/5、运行 SHA 对齐、verify verified（`20260926T103016Z-634c287.json`） |
| CA2-02–04 | 待实现 | 按步骤归属、稳定身份、ResultBundle 顺序；不重开 PLANNER_GOALS |

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

## 3. 第一批契约实施边界

CA2-02 复用现有分句与 step_grounding，步骤业务读取范围与 safety_origin_text 的授权范围分开。
CA2-03 的服务端标识与来源关系沿现有 Plan/step_record/SessionState/活动任务帧保存；
未知语义覆盖明确 unknown，不根据步骤反推用户只提了这些诉求。

CA2-04 保留完整已呈现结果、卡片与引用，并将 TTS 摘要作为独立投影。
当前 `_resume_result` 刻意剥离自由文本/旧卡/动作，不能直接撤销这一隐私与防重约束；
完整答案走正常会话呈现/历史路径，恢复执行只保留必需依赖与事实引用。
HMI/Android 共用结果选择器与版本规则，各自渲染；旧客户端和旧记录均需明确兼容。
