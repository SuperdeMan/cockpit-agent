**AR02 采集与隐私实施记录｜2026-09-07**

> 状态：客户端修复、本地回归与 OPPO prod release 定向验证完成，代码已合入并推送 main；**AR02 尚未整批签收**，R02/R07 完整设备矩阵及旧缓存清理见末节。范围仅 R01 / R02 / R07；启动基线 `9297848`。用户已授权本批提交与推送，没有生产部署、环境/密钥/CI/CD/数据库修改或真实车控、商户写、付款。
> 输入：[完整评审](../reviews/2026-09-07-android-ux-full-review.md)、[AR02 批次](2026-09-07-android-review-remediation-batches.md#ar02)、[AR01 实施记录](2026-09-07-ar01-confirmation-cancellation-implementation.md)。

**实现范围。**

- R01：开发操作诊断从页面挂载和执行函数两处门控，只有明确 dev 变体放行。链接仅能预填参数，录音、播放与切播放器须在页面按键发起；prod 保留只读诊断。
- R02：固定 expo-camera 57.0.4，沿用已有 patch-package 增加 Android 内存 JPEG 路径；在原生写文件之前返回内存字节，不通过 file URI 或临时缓存。旧二进制没有内存能力标记时禁止挂载采集端。保留默认关闭、共用触发词、单次抓帧和服务器 TTL。
- R02：SessionCore 的请求拥有 AbortController，取消/销毁/终态中止准备；视觉关闭、后台、超时中止权限等待、相机等待和上传，迟到结果不能发出依赖图像的请求。多个请求不争用同一个相机。
- R07：麦克风物理开关与 ASR/S2S 上行从实际设备/传输出口发布事实；隐私轴直接订阅，视觉与麦克风不互相覆盖。补齐关闭后权限/初始化回调复活与 ASR 迟到定稿边界。

**实施前冻结的验证安排。**

1. 本地反例：权限等待取消、拍照迟到、上传中止、关闭/重新开启、超时、下一轮恢复；六种免唤醒状态与两种上行路径、视觉并发；prod/staging/未知变体诊断门控。
2. mobile Jest 全量与 TypeScript；原生补丁检查及编译。共享 HMI 没有实现变化时不扩大为后端全量或部署。
3. clean 已提交树构建 prod release；OPPO test 独占装机，回读包内 SHA、安装 SHA256、设置构建行。逐入口检查无手势零采集，读取实际相机/麦事实、缓存/上传计数；所有设备证据绑定新 APK。
4. 成功/失败/超时/取消/进程中断的设备格分别记录。没有实际取得的读数保持未验；本地模拟与样本页不代替设备采集证据。

KWS 模型/阈值、ASR/TTS 选型、声学通路与其他 AR 批次不变。后续填写精确提交、构建和证据，不转借 AR01 的包读数。

**最终实现。**

| 问题 | 行为与责任边界 |
|---|---|
| R01 | `developmentDiagnosticsEnabled()` 仅明确 dev 放行；页面挂载和执行函数分别复核。prod/staging/未知变体不挂载 voice-spike/debug 操作工具；dev 链接也只预填，按键才执行。native-spike 的触感/提示音限 dev。保留只读状态与样本入口 |
| R02 内存路径 | `memoryOnly` 在 Expo Android 原生层先于普通、skipProcessing、pictureRef、fastMode 路径分流；内存解码、按 CameraX rotationDegrees 校正、缩至最长边 1280、重编码无 EXIF 的 JPEG/base64。无 URI/文件写入，JS 用字节数组直接上传。旧二进制缺 `supportsMemoryOnly` 时，在申请权限/挂载相机前拒绝 |
| R02 取消 | SessionCore 请求 AbortController 传入采集/上传；能力撤回信号保留到真实发送前，上传完成后仍能撤回依赖该帧的队列请求。关闭视觉、后台、12 秒超时、请求取消/销毁使结果失效；相机按轮次挂载，旧 ready/error 不污染新轮 |
| R07 | 麦克风启动/停止确认与 ASR/S2S 实际发送出口按 owner 发布事实；麦、相机、音频上行与画面上传分开呈现。采集点组合显示并发事实，激活日志取真实开启信号。recorder/micBus/ASR/HandsFree/TapTalk/usePtt/useHandsFree 共同作废迟到回调 |

普通拍照会缓存文件，base64 选项不会取消写盘；依据锁定源码和 [SDK 57 Camera 文档](https://docs.expo.dev/versions/v57.0.0/sdk/camera/)。补丁通过现有 patch-package 管理，package.json 明确 expo-camera 从源码构建，防止预编译 AAR 绕过。详见[原生补丁说明](../../mobile/patches/README.md)。

相机事实来自 CameraX：OPEN 置真，CLOSING 保留，直到 CLOSED 才置假；不把 React 卸载当物理关闭。原生请求通过 CAS 只终结一次，含尚未进入协程即取消。已进入 Bitmap 的同步操作无法硬中断；客户端也不能撤销服务器已经接收的数据。本批保证后续上传/回调/依赖请求失效，不承诺完整擦除运行时 RAM。新增 `/capture-status` 只读状态、计数与 Camera 缓存总量，不录音、不拍照、不上传；进程重启清零原生请求计数，`started` 不等于曝光次数。

**精确版本与检查。**

| SHA | 本轮证据 |
|---|---|
| `1b0f94deceae98fc0d25f1d83a7ba2b77ba6085e` | 主修复；65 suites / 656 tests，73.549s，exit 0；TypeScript exit 0；首份 APK 与初轮设备验证 |
| `70365389eccdc19a72d567480770393fb4ec76e4` | 最终代码，仅补齐设置页 S2S 本机监听说明；65 suites / 656 tests，135.513s，exit 0；TypeScript exit 0；最终 APK |
| 后续文档提交 | 只回填入口与证据，不转称上述测试/APK 的新版本 |

命令为 `node node_modules/jest/bin/jest.js --runInBand` 与 `node node_modules/typescript/bin/tsc --noEmit`，cwd=mobile。原生补丁 10 条守卫包含 8 个反向缺陷注入；补丁正反应用、上游/还原 Git blob hash 一致。普通源码 diff 检查通过；统一 diff 的空上下文行保留，按补丁应用检查有效性。没有放宽断言、删除用例或跳过失败，没有把 Android lintVital 当成 AR06 的 ESLint 门禁。

新增反例涵盖权限等待关闭/后台/卸载、旧 ready/error、迟到图片/文件 URI、上传 abort、超时、并发保护、准备后真实队列撤回、取消/销毁、麦租约启停、ASR headers/body、S2S 实际 send、旧 WS、三轮恢复及真实 PTT hook。Presence 覆盖六种 FSM × 两挡位 × 相机并发 × 音频事实；本地外部 I/O 可控替代不能关闭真机矩阵。

**APK 与构建。**

| 项目 | 读数 |
|---|---|
| 最终 APK | `D:\Android\builds\apk\xiaozhou-companion-prod-release-70365389e-20260907-2051.apk`；210,671,846 bytes |
| 最终 SHA-256 | `20a83de597798f5b7f4e93f6987254c5f92e39c56c433dd7639ad26a2cdd2bab`；设备安装文件同哈希 |
| 构建行 | `v0.1.0 · prod · 70365389e · 2026-09-07 20:44`，metro=false；内嵌 bundle 4,204,448 bytes |
| 设备/安装 | OPPO PEUM00 / Android 14，外屏 988×1972、density 440；麦/相机权限预先已授；install -r 后 lastUpdateTime=2026-09-07 20:51:42，非 DEBUGGABLE，同签名、双 ABI、KWS/ORT 原生件齐全；Xiaomi 未操作 |
| 首份 APK | `1b0f94dec`，安装 20:26:37；SHA-256 `bd365350130e8c4a4f762db17bb982d59ef828cab256414ca6f0dbc708615108` |
| 云端独立读数 | `a09c73a5da3181708279bc1f3e90acb1519606a0`；5/5 healthy、warnings=[]；当前主模型配置 `minimax:MiniMax-M3`；没有部署 Android SHA 到云端 |

初次按 `build_mobile.ps1 -Release -Variant prod -CompileJobs 1` 构建，1GB 堆在 D8 `mergeExtDexRelease` 报 Java heap space，exit 1。确认原生/JS 完成且 337 个源码/镜像文件哈希一致后，在同一镜像沿脚本 §5 参数以进程内 2GB 堆接续：11m34s、exit 0。最终两行说明更新仅同步对应 JS 源和新构建身份，原生不变，6m24s、exit 0；没有重新 prebuild、清缓存或修改系统虚拟内存。按脚本 §6 同等检查包内身份、bundle、ABI、语音库和签名后另存 APK。

保留 SDK XML、NODE_ENV、NO_COLOR/FORCE_COLOR、Gradle 废弃项与高德库无法 strip 提示。初次失败与两次成功的结果 JSON 分开保存，不能把失败重试写成一趟成功。

**设备定向验证。**

| 验收项 | 结果与证据锚 |
|---|---|
| prod 无手势入口 | `70365389e`：14 个入口/参数组合逐项通过；麦/ASR/S2S/拍照/上传累计数均 0、PID 连续、缓存数/字节不变，每项刷新原生计数后读取。包含 voice-spike 自动/仅播放器参数、debug、native-spike、轨迹/状态/卡片/材质页、设置、引导、车辆、地图、voice、首页 |
| 冷启动原始深链 | 两包分别复验 auto=stutter/load=hf/player=node 冷启动，均明确拒绝，随后累计数全 0；最终包构建身份为 70365389e |
| 单帧链 | 两包分别从普通 Composer 发“看看这是什么”，各自 native started/completed=1/1、uploadsStarted/uploadsCompleted=1/1，相机回到 CLOSED，未新增麦录音。Camera 缓存仍 2 个/4,408,670 bytes，零新增。两轮答案均为画面全黑，没有已知目标，不算物体识别正确性通过 |
| 后台撤回 | `1b0f94dec`：发起后立即 Home，系统 camera service 见 CONNECT 后 CLOSED；拍摄/上传累计数不增加，回前台显示“画面采集已停止，本轮没有发送”，缓存不变。撤回点在 ready/拍照请求前，不冒充所有在途阶段 |
| 麦克风事实 | 两包均完成启停对账。首包 AudioFlinger 的活动录音线程为 VOICE_COMMUNICATION / VOIP_TX，与隐私栏“开启、本机处理未上传”一致。最终包 120ms 轻触进入一次 ASR 上行，采集点为上传色、asrSegments=1；关闭后 mic/asr/s2s 全 false、micStarts/micStops=1/1 |
| 说明 | 最终包 XML 已明确“免唤醒待机时麦克风仍在本机监听，不上传音频” |

污染/限制单列：第一次 Maestro 未结束即切页，样本作废。Maestro 结束后 instrumentation 仍可能持有 UiAutomation，直接 uiautomator 曾使取证辅助进程报 already registered；这是辅助进程，不能当 App 崩溃。后续先结束本轮驱动再读 XML。动态页多次无法 idle，没有关闭动效冒充默认配置。普通 adb tap 未触发 RNGH 光球，120ms 无位移手势成功；不据零时长注入失败判断 App 轻点坏了。Windows 合成天气语句未取得完整声学问答，不计 KWS、首音或六态通过。

证据目录：`%LOCALAPPDATA%\car-agent\artifacts\AR02-20260907`，含 checks/构建 JSON 与日志、APK 元数据、337 文件哈希、逐项 XML/PNG、系统采集状态和 Maestro 结果；不保存原始相机 JPEG、麦克风 PCM、token 或密钥。

**未完成项。**

- R01 已完成本批 prod 入口修复与定向验证。
- R02/R07 客户端已修；完整设备矩阵仍待：真实权限等待撤回、拍摄/上传中的失败与超时、拍摄/上传中结束进程、六种免唤醒状态 × classic/S2S × 视觉并发。组件/传输模拟及部分设备读数不能替代这些格。
- 旧版本遗留 2 个 Camera 缓存文件、4,408,670 bytes，首次采集前已存在，新路径未新增；尚未清理。已请求用户授权通过 Android 仅清理该 App 缓存（保留应用数据和连接配置），未收到答复前不执行。
- 已请求已知、无敏感内容的拍摄目标，尚未收到；黑帧结果只证明传输/缓存边界，内容和方向需另验。
- AR03/AR04 分别处理停播/横屏与跨页宿主/前后台/ACK，本批未启动。AR02 整批签收前先补设备矩阵，不将定向结果升级成 QA 全绿。

本轮结束时保留 OPPO 最终常驻包，免唤醒恢复关闭、视觉保持本轮开始时的开启设置；没有改模型、阈值或系统设置。测试驱动与采集均停止，测试资源归还。后续先核包与设置，再继续末节矩阵。
