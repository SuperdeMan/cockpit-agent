**AR01 确认与取消实施记录｜2026-09-07**

> 状态：客户端修复、本地回归与 OPPO release 样本验证完成；本批未推送或部署，未执行真实业务多操作 E2E。
> 用户于 2026-09-07 启动 AR01；基线 `dbd1540db841da562ca7edb0925ecb58e79d502d`，分支 `fix/ar01-confirmation-cancellation`。
> 范围：[分批建议 AR01](2026-09-07-android-review-remediation-batches.md#ar01)，仅 R03/R04/R05。原发现保留在[完整评审](../reviews/2026-09-07-android-ux-full-review.md)。
> 本页记录实现、精确版本和验收边界；截图、日志、JSON 与临时复现脚本全部在仓库外。

**问题与最终行为。**

| 问题 | 本批修复 | 证明范围 |
|---|---|---|
| R03 指定业务确认被位置征询截走 | 有 operationId 只处理该业务项，点击时校验台账/TTL；无 ID 才处理本地位置征询。保留三条容量与服务端关闭权威 | 两条业务操作与位置征询并存，分别确认/取消；权限调用、上行 operation_id、剩余台账逐项断言 |
| R04 取消只改 UI，真实队列/异步回调仍派发 | 定位/视觉准备前登记请求身份；撤回真实未发帧，取消/销毁后的迟到准备结果失效；计数来自实际发送/丢弃回调 | 等定位、同意位置后的等待、视觉回调、断网撤回/重连、TTL、满队列、迟到终态与连续轮次隔离 |
| R04 创建顺序与发送顺序不同 | 默认选择最新创建的请求；若它已发送，改按实际最后发送的请求定位。只有连接当前轮能发送会话级 cancel，且不离线排队 | 先发问 A 等定位，后发问 B 先发送，A 定位完成后再发送；打断 A，B 的迟到抢占回执独立结算 |
| R05 其他待办不可达 | “另有 N 个待处理”展开可滚动原生列表，按稳定 ID 操作每项；全部结束后卸载列表，下一组不继承展开态 | 组件测试验证第三项、重排/到期、位置项 ID、关闭与重开；真机样本验证另列 |

网关现有事实为每连接只保留最新在飞请求；新 user 帧会抢占上一轮，cancel 帧只取消连接当前轮。本批沿用该协议。已发请求的本地“已打断”表示停止本端等待和尝试取消，保留已接收动作，不表示业务已回滚。点名的迟到 cancelled/error 不得终止下一轮；服务端 closed_operation_ids 即使对应回答已被本地丢弃，仍更新操作台账。

共享 `hmi/src/ws.mjs` 增加可选 `send(frame, hooks)`、`discardQueued(requestId)`、`sendIfOpen(frame)`；hooks 不上行 JSON。flush 前复核请求有效性，发送观察回调异常不重发已写帧。被撤回的未发送确认只恢复仍有效、未被服务端关闭的原操作；发送异常按状态未知处理，避免恢复成可重复确认。

视觉准备改由 SessionCore 管理请求身份。摄像头/上传的物理中止和零落盘仍在 AR02；本批只证明迟到 frame_id 不再触发该请求。没有新增依赖，没有修改 proto、网关执行协议、VAL、数据库、CI/CD 或环境/密钥。

**版本与本地验证。**

| 版本 | 含义 |
|---|---|
| `dbd1540db841da562ca7edb0925ecb58e79d502d` | AR01 启动基线，已推送的评审文档 HEAD |
| `ead802e21a29c5fc08d8db22b372dc86d653b374` | 第一版修复提交；HMI 测试/构建证据锚；该版 APK 构建成功但被后续补充修复取代，未装机 |
| `f4647d0fb87bf2928fa26e12151cd8ace0b14e25` | 最终代码，补齐异步准备造成发送乱序时的取消归属；mobile 全量、类型检查与最终 APK 使用此 SHA |
| 后续文档提交 | 只回填验收与入口；不转称上述测试 SHA，也不表示云端发布 |

| 检查 | 结果 | 证据边界 |
|---|---|---|
| 原反例 | R03 和两条 R04 离线复现；最初 14 条请求回归与 4 条 Dock 交互回归在修前失败 | 原始评审外部脚本 + 正式回归；无真实业务写 |
| 发送乱序反例 | 新回归在 `ead802e` 的实现上失败，修复后通过 | 明确断言注入的 socket 收到的最后一帧为 cancel，而非只看 UI |
| mobile 全量 | `f4647d0`：57 suites / 574 tests，72.608s，exit 0 | 含 20 条新增请求生命周期用例、4 条 Dock 交互用例及既有共享守卫；测试期间代码不变 |
| TypeScript | `f4647d0`：tsc --noEmit，exit 0 | mobile 和共享类型 |
| HMI 全量 | `ead802e`：304/304，11.299s，exit 0 | 包含 WS 新增 6 条；`ead802e..f4647d0 -- hmi/src hmi/package.json` 无差异，保留原测试 SHA |
| HMI 构建 | `ead802e`：Vite build 成功，2.77s | 有 >500kB chunk 提示，未改阈值 |
| 定向冷缓存/句柄检查 | 第一版新增会话/Dock 测试 22/22，27.589s，exit 0，无具体句柄报告 | 最终全量仍有 Jest 退出延迟提示，最终 exit 0；不把提示记为已解决 |
| Android 环境 | 18 pass / 0 warn / 0 fail | 本轮环境检查，非业务验收 |

三条旧测试前提已校正：连接 open 不等于队列已发，改为逐条 onSent；确认来源测试先建立有效操作；离线默认取消最新请求。组件测试的 RN Modal/ScrollView 冷加载移到 collect 阶段后独立复查通过，没有删除测试或放宽超时/行为断言。无关的全仓 lint 治理留在 AR06。

**APK 与设备验证。**

| 检查 | 本轮读数 |
|---|---|
| 最终构建 | `f4647d0fb`，prod release；Gradle 23m03s，exit 0；脚本 2026-09-07 16:59:02–17:23:43 |
| APK | `D:\Android\builds\apk\xiaozhou-companion-prod-release-f4647d0fb-20260907-1723.apk`，约 210 MB，内嵌 bundle 4,176,776 bytes |
| APK SHA-256 | `30a7c45492cccaa50f8450c61a971e71530a6e2df5f51ba4b98bce040b9b2170`；设备安装文件与本地产物哈希一致 |
| 源码一致性 | 测试后源文件哈希、构建镜像文件哈希均无漂移；工作树在构建期间保持 clean |
| 设备 | OPPO PEUM00 / Android 14，外屏 988×1972、density 440，系统字号 1.0；本批未操作 Xiaomi 对照机 |
| 安装回读 | install -r 成功；lastUpdateTime 从 2026-09-07 10:45:38 变为 17:24:37；无 DEBUGGABLE |
| 设置页构建行 | `v0.1.0 · prod · f4647d0fb · 2026-09-07 17:00`，XML 与截图均可读 |
| 列表可达性 | “另有 3 个待处理”打开 4 项列表；第三项取消、第二项确认可点击；滚动后位置项允许按钮完整可达；按钮实测高 132px = 48dp |
| 关闭与恢复 | 关闭按钮收起；再次展开后 Android 系统返回收起；截图复核；本轮没有改动连接、语音、动效或系统设置 |
| 稳定性与结束状态 | 本次 App 进程 crash buffer 的 FATAL 计数为 0；结束本批样本进程并返回 Launcher |

构建仍走 `scripts/build_mobile.ps1 -Release -Variant prod -CompileJobs 1`，保持 arm64-v8a、armeabi-v7a 和现有签名。最初一次构建在会话继续前中断，未取得成功终态，不计通过；普通重试又因 Windows error 1455（commit 内存不足）在 JVM 启动时失败。随后仅对构建进程设置 `-Xms128m -Xmx1024m -XX:MaxMetaspaceSize=512m -XX:ActiveProcessorCount=2` 和 Kotlin in-process。没有改系统内存配置或停掉其他会话的 Metro。第一版小堆构建成功用时 33m59s，其 APK 不用于最终代码验收。

最终构建保留 SDK XML 版本、NODE_ENV、Gradle 废弃项、NO_COLOR/FORCE_COLOR 与高德库无法 strip 等提示；没有调阈值或抹去日志。动态画廊的 UIAutomator dump 曾报 `could not get idle state`：按实读截图坐标打开列表后，Modal 内 XML 可正常读取，后续按 testID 定位按钮、滚动并以截图复核关闭。未通过关闭动效来隐藏该限制，它不代表 AR09 的观测/负载问题已经关闭。

设备样本使用 `xiaozhou:///state-gallery?only=attention-multiple`，三条业务样本与一条位置征询，标“仅样本”，onConfirm/onCancelTurn 均为 no-op。真机读数只证明 release 呈现与触达；操作 ID、真实本地传输队列与取消隔离由 SessionCore × GatewaySession × 共享 WS 集成测试证明，其中仅 socket/位置等外部 I/O 被替换。

原始证据目录为 `C:\Users\Super\.codex\artifacts\car-agent-ar01-2026-09-07`，包含最终测试日志、文件哈希、构建结果、设备 XML/PNG；不是仓库可提交目录。

**交接与剩余边界。**

- 本批关闭 R03/R04/R05 已证明的客户端缺陷；没有执行真实车控/商户业务，也没有把样本页当作生产多操作 E2E。
- AR02 接续摄像头/上传物理中止、零落盘及能力关闭后的采集边界；AR03 处理 final 后纯停播与横屏出口；整体真机/外部用户验收在 AR10。
- 其他 AR 批次未启动。本批代码在本地分支提交，push/生产部署仍按项目规则另行授权；原评审文档的推送授权不扩展为代码发布。
