# Android 语音输入采纳与拒识修复

日期：2026-09-11。用户反馈：同一噪声、同一句话，按住与轻点均会收入环境/旁人讲话；与系统助手的主要差异发生在最终采纳阶段。用户已授权按核实建议实施。

状态：代码、本地全量与 OPPO 验包完成，待云端发布及声学验收；交付对象：Android 与 Cloud Planner。关联：`mobile/src/features/chat/usePtt.ts`、`useHandsFree.ts`、`orchestrator/cloud/engine.py`、`docs/conventions.md` 输入拒识契约。

本批基于 `738ee9936847efcb642f2b917174ca35f635e5fc`，修改客户端接线与既有云端拒识入口。生产发布、设备包和声学效果分别取证，不以本地测试替代。

## 1. 已确认的问题

1. `HandsFreeController` 产出的来源与时长在 `useHandsFree` 和宿主回调中丢失；本地 `TurnMeta.source` 不进入线上请求。
2. 按住/轻点共用非空定稿直接发送路径；云端此前将未带来源的显式输入豁免拒识。
3. 候选选择/翻页等改写分支只保留自己的路由 meta，丢掉调用方附带的语音来源。
4. 云端 rejected final 只标气泡，没有结束播报等待；静音设置下甚至没有可停止的 TTS 会话，FSM 会等 100 秒兜底。

平台 `VoiceCommunication` 已接入，既有小米证据显示 AEC/NS 挂载；本批不更换音源、VAD/KWS 阈值或识别模型。

## 2. 最终行为

| 输入 | 请求来源 | 采纳策略 |
|---|---|---|
| Android 按住/轻点 | `ptt` | 当前播报参照与识别文本高度重合时，先放入输入框核对；其余定稿发送后，云端明确判 `addressed=false` 可拒识 |
| Android 免唤醒 | `voice_wake` / `voice_followup` / `voice_bargein` | 恢复来源与 `voice_utterance_ms`；原有本地回声过滤与云端非受话拒识各守原职责 |
| Android S2S 逃逸到主链 | `voice_s2s` | 主链请求带语音来源；不声称覆盖 S2S 模型已经自答的音频 |
| 文字、按钮、核对后手动发送 | 不自动带语音来源 | 保留显式文字请求行为，不继承上一语音轮 meta |
| 挂起确认/补槽 | 保留原续接路径 | 手动录音不因与提示文字相似被放入核对区；云端确认/补槽不迁入新规划拒识分支 |

`ptt` 刻意不使用 `voice_` 前缀：已有 Planner 对显式输入的重试和 actionability 的 hands-free 分类维持原语义，仅扩大拒识消费入口。旧客户端不带来源时仍兼容原行为。云端 `REJECT_NON_ADDRESSED` 开关仍有效。

本地回声匹配从 `voiceLoop.mjs` 导出同一份函数，保留既有归一/子序列/0.75 判据。手动录音只保存**按下时正在出声**的参照，停止后的新录音不拿陈旧播报作比较。参照文本与音频没有逐字时间对齐，故它只能决定“需要核对”，不能证明录到的一定是回声。保留已有输入框草稿；核对内容不自动生成业务请求。

最新轮 rejected final 清理过程区并结束无声等待；迟到旧轮不得停止新轮。明确的无声终态通过 `endSilentTurn` 通知语音 FSM，即使用户关闭播报也能进入下一轮。

## 3. 验证与边界

新增用例先在旧实现判红：Hook 来源丢失、候选改写丢 meta、手动录音的非受话豁免、拒识不收尾。另补真实宿主到最终请求帧的集成回归；用真实 SessionCore + SpeechController + VoiceLoop 在静音设置下复现并锁住连续三轮恢复。

2026-09-11 本批工作树的本地结果（基于上述 SHA，不借用生产 release 的历史数字）：

| 验证 | 结果 |
|---|---|
| Android `jest --runInBand` | 89 suites，962 passed；最终普通运行 39.537s，退出码 0 |
| Android `tsc --noEmit` / eslint `--max-warnings 0` | 均退出码 0 |
| HMI 全部 `src/*.test.mjs` | 333 passed，退出码 0；共享回声算法保持旧行为 |
| Cloud Planner 全目录，`TZ=UTC0 -n 4 --dist worksteal` | 1312 passed / 1 skipped，38.56s，退出码 0 |
| `smoke_edge` | 13 passed |
| skills / exemplars / L0 strict / capability integrity | 四道门禁通过；既有 skills 1/8 误召回、exemplars 3/167 域错配仍按原门槛记录，未放宽判据 |

两次中间 Android 全量虽然断言均通过，进程没有退出，不能计作完成。`--detectOpenHandles` 指向既有 `history.test.ts` 三处 SessionCore 定时器；该 fixture 未销毁故意保留的在飞/挂起会话。补 `afterEach` 释放后，最终普通命令正常退出；未用 `--forceExit`、skip 或更改产品超时绕过。

后续固定 SHA 的 Python 仓库全量及 APK 结果见 §5；仍没有真 ASR/Planner 噪声样本成绩，不记为生产验收。

本批仍不是完整声学受话检测：

- 端侧 fast-intent 命中、挂起会话续接不经过云端新规划拒识。环境声若被转写为一条完整合法指令，仍不能仅靠文字确定说话人。
- 非受话判定仍依赖当前 Planner；不确定时接受的策略未改变，未伪造置信度、未增加独立模型调用。
- 本地回声参照只覆盖本 App 的播放通路，不拥有其他 App/电视的播放参照，也不分离旁人声源。
- 连续拒识后的 `RejectPolicy` 自适应收紧仍属后续工作，本批完成的是输入来源、最终采纳与终态恢复。
- 新客户端需要本批服务端更新才能对 `ptt` 消费拒识；免唤醒来源接通可使用既有云端分支。未发布时不能当作手机已修好。

## 4. 真机验收协议

固定 APK hash、服务端 release、ASR/Planner provider/model、音量、距离与朝向；OPPO 主验，小米同包对照，分机型记录。每格至少重复三次，未测记未测，不编 dBA。

| 场景 | 检查 |
|---|---|
| 用户近讲，无背景/有风扇 | 正常采纳、无新增漏字/误拒 |
| 用户不说话，旁人闲聊/新闻播报 | 分别记录 ASR 原始转写、最终是否采纳、拒识依据、下一轮能否继续 |
| 本 App 正在播报时按住/轻点 | 疑似回声留待核对，零自动请求；已有草稿保留 |
| 播完后有意复述/追问 | 不因陈旧播报被拦；核对后显式发送可用 |
| 确认、取消、补槽 | 原 operationId 续接成立，不被回声文案截走；危险动作不作为本批无授权的测试动作 |
| 静音/有声设置各连续三次拒识 | 无多余 TTS，无等待卡死，下一轮正常 |

分别报告误采纳与误拒绝；代码回归通过不能转写成识别准确率或系统助手同等效果。

## 5. 固定提交、验包与发布准备

代码提交：`f8fd15152d78592e4e5625bab22d4bd5e654738d`。以下读数绑该提交，后续文档提交不改变代码/APK 身份。

- Python 固定口径 `TZ=UTC0 python -X utf8 -m pytest -q -n 8 --dist worksteal`：**8234 passed / 32 skipped / 13 warnings**，300.95s，退出码 0。警告包括 Starlette/httpx 弃用、WordPiece 弃用、一个 gRPC 未 await、audioop 弃用、已有正则 FutureWarning；未以升级全局依赖或 skip 隐藏。
- APK：**prod / f8fd15152 / 2026-09-11 21:24**。Gradle `BUILD SUCCESSFUL`，8m56s，1222 tasks（60 executed / 1162 up-to-date），退出码 0；SDK XML 及 Gradle 弃用提示保留。
- 本批纯 JS/TS/共享源码六文件。构建前核对原生配置、依赖、模型、`.env.local` 与既有 prod 镜像一致，运行语音资产检查；只增量复制六文件，沿构建脚本同等 Gradle 参数接续，`--no-daemon`，未运行 `/MIR`、prebuild/clean，未删除镜像缓存或修改环境文件。
- 验包：ZIP CRC、内嵌 JS bundle、variant/build、arm64-v8a + armeabi-v7a 的 KWS/ORT、签名均通过；非 DEBUGGABLE。APK **210982558 bytes**，SHA-256 **`6fc093a9cbcca2c9f051328c79089413649953f397ff1c7e69a18dd7b7666103`**。
- OPPO PEUM00：`install -r` 成功，安装时间 **2026-09-11 21:34:54**；设备侧安装文件 SHA-256 与上述包一致。启动 MainActivity 返回 ok，目标进程 AndroidRuntime/ReactNativeJS 错误 0 条。设备处于 Keyguard，未读到设置页正文，不把启动成功记为页面视觉或声学验收；未操作 Xiaomi。
- 云端只读 status：生产仍 **`d532c6d816e662fa60740565d6230774c02ff200`**，running SHA 相同，5/5 healthy、无 warning。本批目标 `f8fd151...` 的 deploy dry-run 通过，`blocking_changes=[]`，CI/CD 摘要未变；**未 push、未 apply**。

持久化证据位于 `%LOCALAPPDATA%\car-agent\artifacts\voice-acceptance-20260911-212413`：`verification.json`、构建脚本及 stdout/stderr、`build-result.json`、`apk-verification.json`、`device-verification.json`、`xiaozhou-prod-f8fd15152.apk`。发布预检查：仓库 `.artifacts/releases/f8fd15152d78592e4e5625bab22d4bd5e654738d/`。
