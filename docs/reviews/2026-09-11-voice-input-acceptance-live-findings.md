# Android 语音采纳发布与真栈核实

日期：2026-09-11。结论：**授权发布已完成；语音拒识效果仍未闭合。**

## 1. 发布身份

- 用户授权推送 `f8fd15152d78592e4e5625bab22d4bd5e654738d` 与 `34de6dd77f9399917d70f4c9cc621af4217776d4`，并将云端发布到前者；两条提交已推送。
- dry-run → apply 均使用固定 `f8fd151...`；`blocking_changes=[]`，无新增 CI/CD 或基础设施批准项。
- 独立 status：release SHA 与 running SHA 均为 `f8fd151...`，5/5 endpoint healthy、warnings=[]。
- 独立 verify：`verified`，artifact `.artifacts/dev-stack-verifications/20260911T134859Z-f8fd151.json`。
- OPPO 已安装的 prod 包仍为 `f8fd15152`，APK SHA-256 `6fc093a9cbcca2c9f051328c79089413649953f397ff1c7e69a18dd7b7666103`。没有为发布改包或重跑声学录音。

## 2. 本次方法

手工限定的 WebSocket 探针，从 ASR 定稿之后注入文本；不是实际麦克风、不是 Android UI 自动化。请求带 `input_source` 与 `memory_enabled=false`，固定真实 provider/model **`minimax:MiniMax-M3`**，独立会话中连续测试背景话与正常请求，至少重复三次。

旧 `e2e_rejection` 的 manifest 未标 remote_safe，本轮未运行该旧脚本。续测先检查语料不会命中本地动作，再运行只读内容；每轮检查 actions、pending、speech、speech_delta，开始/结束核对 release 和 provider/model。

## 3. 首轮暴露的端侧动作误判（仅记录，未修）

1. `ptt` +「他昨天跟我说那个项目黄了」：静默拒识，0 actions。
2. `ptt` +「欢迎收听今天的节目，本台记者为您报道新闻。」：约 0.97s 返回 `media.control / media.play` 和「已为您完成操作」，**失败**，trace `ba82484fa8c54b9a`。探针在见到 action 后立即停止，未继续原矩阵。

源码定位：`orchestrator/edge/fast_intent.py` 的新闻分支只要有「新闻/头条」和「播/放/听」就判播放；其余部分表达也缺省播放。这条确定性路径不经过云端新规划拒识。

该动作经 PoC VAL 将模拟 `media` 置为 `playing`。本会话用精确 `media.stop` 停止本次意外播放，回读 `media=stopped`，其余车态字段逐字段不变。**原探针没有采前状态，因此不能宣称完整还原到探针前初态。** 原始失败报告保留，没有替换语料后将它记作通过。

## 4. 云端分支续测（与首轮单列）

去掉会触发媒体动作的词组，以「本台记者报道，项目建设已经进入第二阶段。」单独检查云端受话分支；乘客句与正常句保持不变。

| 来源 | 语料 | 期望 | 结果 |
|---|---|---|---|
| ptt | 他昨天跟我说那个项目黄了 | 静默拒识 | **1/3** |
| ptt | 本台记者报道，项目建设已经进入第二阶段。 | 静默拒识 | **1/3** |
| ptt | 用一句话解释什么是白噪声 | 正常回答 | **3/3** |
| voice_followup | 他昨天跟我说那个项目黄了 | 静默拒识 | **1/3** |
| voice_followup | 本台记者报道，项目建设已经进入第二阶段。 | 静默拒识 | **3/3** |
| voice_followup | 用一句话解释什么是白噪声 | 正常回答 | **3/3** |

本矩阵 18 轮中 12 轮符合预期：正常请求 6/6，背景话仅 6/12 被静默拒识。续测各轮 0 actions、0 pending，前后车态一致，release/provider/model 连续。这个小样本不折算为声学准确率，也不与首轮换分母合并。

## 5. 失败通道

- 乘客句四次漏拒：ptt 的 `595fbaaeb715485c`、`5452ae3434484fa6`，以及 voice_followup 的 `9e5f791273db4393`、`3ad20961daa748af`。trace 的 retry_policies 均为 `no_action_unconfirmed,no_action_unconfirmed`，最终是 `chitchat.talk`。前两条 plan_mode 为 `toolcall_salvage_no_action`，后两条为 `toolcall_no_action`；回复为失落/失望类安慰。
- 播报句两次漏拒：ptt 的 `5d068f03162c462d`、`aa9e9be6800d4a62`，落 `system.planner_failure`，返回「这次我没能把您的请求拆成可以执行的步骤……」。没有动作，但没有静默丢弃。
- 有成功拒识实例，证明新来源字段和拒识出口已到达；**不能据此认为所有输出通道都能保留拒识意图**。上述 trace 只证明最终经过的路径，不将未获取的模型原始隐藏意图写成事实。

## 6. 后续范围

当前仍需独立处理两类问题：

1. **确定性快捷规则的受话边界**：以首轮失败语料及正常「播放新闻」作正反对照，检查请求语气与被引用/播报内容；不能把「识别到关键词」等同为用户指令。
2. **Planner 无动作/拒识的保真**：逐条追 `no_action_unconfirmed`、toolcall/salvage 与技术失败出口，明确“不应回应”如何区别于“该回应但计划失败”，同时保留真实情绪表达、确认/取消和补槽。

先立上述失败样本的回归，再改判据；不直接靠提高 VAD 阈值或加入播报关键词黑名单宣称解决。真正识别说话对象仍需要同条件声学证据，不能从文字注入样本推导。

**2026-09-14 处置（`696899b5`；同日经用户授权随 `9ced633b` 发布，status 5/5、verify verified）**：

1. 端侧：新增 `runtime/reported_speech.py::is_reported_speech`——判据是**语域**（说话人在对听众播报 / 转述：本台 / 记者报道 / 欢迎收听 / 各位听众 / 为您播报 / 以上是 / 据报道…），零领域词（源码级断言），与 `question_shape` / `polarity` 同一族、同一落点：`classify_structured` 出口只盖写操作；`split_and_classify_any` 拆句前先判整句（「本台记者提醒您，请打开车窗」第二段单独看是干净指令）。首轮失败原话作为回归第一条（`orchestrator/edge/tests/test_reported_speech_gate.py`）；「播放新闻 / 我要听体育新闻 / 来段新闻」逐字不变。
2. 云侧：语音来源（`ptt` / `voice_*`）+ 播报语域 + 模型两轮都没拆出步 ⇒ `Plan(addressed=False)` 走既有静默拒识（`planning.py`，`plan_mode` 后缀 `_register_not_addressed`），不再落 chitchat 安慰或 `planner.technical_failure`。文字输入、模型真拆出步、掉档轮抢救出的计划三种都不动。§4 矩阵里播报句两格（ptt 1/3、voice_followup 3/3）部署后期望 3/3、3/3。
3. **没修的那一半**：乘客句「他昨天跟我说那个项目黄了」没有播报语域，文本上与「用户在向助手转述」不可区分，仍归模型（ptt 1/3、voice_followup 1/3 不变）——那是声学问题，按 §4 协议要真人 + 背景源。发布后只读复跑 24 轮（同探针同判据，零动作零挂起、车态不变）：播报句 `本台记者报道…` ptt **3/3**、voice_followup **3/3**；首轮失败原话 `欢迎收听今天的节目，本台记者为您报道新闻。` 端侧不再执行 `media.play`，云侧 ptt 2/3、voice_followup 3/3（1 次模型规划成新闻摘要，有步则放行）；乘客句 ptt 0/3、voice_followup 1/3 不变；正常请求 6/6。逐轮见[剩余待办总表 §6](../design/2026-09-14-android-remaining-todos.md)。

证据目录：`%LOCALAPPDATA%\car-agent\artifacts\voice-acceptance-20260911-212413`。首轮 `live-probe-result.json`，续测 `live-probe-guarded-result.json`，模拟媒体补偿 `probe-media-compensation.json`，失败 trace 摘要 `live-trace-diagnostics.json`，对应探针脚本同目录。
