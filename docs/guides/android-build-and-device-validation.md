# Android 构建、取证与跨工具交接

适用于本项目 Windows + PowerShell 的 Android 工作流，供 Claude Code、Codex 和人工开发共同使用。核对日期：2026-09-07。可执行行为以 [build_mobile.ps1](../../scripts/build_mobile.ps1)、[mobile_device.ps1](../../scripts/mobile_device.ps1) 为准；设备角色和日常使用见 [mobile/README.md](../../mobile/README.md)。本页保存可复用步骤，逐批 SHA、耗时、失败样本和验收结果保留在各实施记录中，本页主要实证依据是 [AR01](../design/2026-09-07-ar01-confirmation-cancellation-implementation.md)。

## 1. 先确认版本与资源归属

在目标 checkout 的仓库根执行：

```powershell
Get-Content -LiteralPath dev-stack.local
python scripts/dev_stack.py target show
git status --short --branch
git rev-parse HEAD
powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1
```

- `target=cloud` 时本地可以编辑、单测和构建 Android，不能启动本地 Compose。
- 每个开发批次明确源码 checkout、改动范围和负责人。独立文件的编辑/审查可以分工；集成与验收由一个负责人汇总。
- **同机原生构建串行**。不同 worktree 也会写同一个 `D:\Android\builds\xiaozhou-mobile`，并共享 `hmi` 镜像与 `.cxx`。脚本含 `robocopy /MIR` 和 `gradlew --stop`，不能把“分支隔离”当成“构建隔离”。启动前确认没有其他会话使用这些目录或相关 Gradle daemon。
- **测试手机串行使用**。先按角色解析设备；不要沿用别人的序列号、PID 或上一轮的前台状态。OPPO 为 test，Xiaomi 为用户主用的 compare；具体边界以 mobile README 为准。
- 先完成代码回归、自审与提交，再冻结用于正式验包的树。构建期间不改源码或切分支；新增修复后旧 APK 不再代表新代码。纯文档提交可领先 APK，分别记 SHA，不必为文档重建包。

交接时要告诉下一位负责人哪些构建/设备资源仍被占用；不能只说“在跑”。项目经验入库共享，临时证据放仓库外，避免依赖某个工具的私人记忆。

## 2. 构建入口与缓存边界

```powershell
# 普通 dev-client：JS 由 Metro 提供
powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1
# 可脱离 Metro 使用的常驻包
powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1 -Release -Variant prod
```

脚本把 `mobile/` 镜像到 ASCII 单根，并把 `hmi/src` 镜像到相邻目录，使 `@shared/*` 的 release bundle 能解析。中文原路径、subst 双根和 junction 的历史问题见 [原实施计划](../design/2026-08-24-mobile-app-implementation-plan.md) §9.11–12；不在原目录绕开现有构建入口另跑一套 Gradle。

原生中间产物由 [gradle_cxx_staging.init.gradle](../../scripts/gradle_cxx_staging.init.gradle) 放到 `D:\Android\builds\cxx\<模块>`。这是 release 路径长度和缓存保留的共同边界：debug 通过不能证明 release 的路径预算通过。

缓存只能减少重做，不能保证每次“增量秒级”。CNG prebuild 可能重新生成 `android/`；CMake 参数变化也会改变配置哈希。尤其调整 `-CompileJobs N` 会切换 `.cxx/<hash>`，所以同一验收候选应尽量固定参数。不要把 `-Clean` 或删除缓存当作内存不足、长时间无日志的默认处理。

## 3. 低内存构建：并发与 JVM 分开处理

先看可用物理内存与 commit 余量；Windows 的 JVM 启动错误 1455 不能仅用“物理内存还有几 GB”排除。

```powershell
Get-CimInstance Win32_OperatingSystem |
  Select-Object FreePhysicalMemory, FreeVirtualMemory
Get-Process |
  Sort-Object PrivateMemorySize64 -Descending |
  Select-Object -First 8 Id, ProcessName, WorkingSet64, PrivateMemorySize64
```

上面 WMI 的 Free 值单位为 KB，进程内存列单位为 bytes；进程 private bytes 用于寻找占用者，不等同于整机 commit 总量。不得依据旧记录中的 PID 停进程；先核当前归属，不停止其他会话的 Metro/Gradle。

| 情形 | 处理 |
|---|---|
| 编译峰值过高，机器被多会话占用 | 使用 `-CompileJobs 3`；它限制 Ninja 编译池，并把 Gradle 与 Metro workers 限为 2 |
| 仍很紧张，或 JVM 启动报 1455 / failed mmap | 本轮验证过 `-CompileJobs 1` 加下面的小堆参数；这些只对当前构建子进程设置 |
| 日志暂时不动，但 compiler PID、对象文件或日志时间持续变化 | 继续等待；不能按固定几分钟阈值认定挂死 |
| 进程已结束而没有成功终态/产物验证 | 记录中断或失败；查退出结果后再决定重试，不能补写成成功 |

[低内存 init script](../../scripts/gradle_low_memory.init.gradle) 不会设置 JVM 堆。AR01 的 1455 出现在编译开始前，最终采用 `-Xms128m -Xmx1024m -XX:MaxMetaspaceSize=512m -XX:ActiveProcessorCount=2` 与 Kotlin in-process 才走完。此配置是本项目验证过的退让档，不是对所有内存占用都有效的保证；不要把它写入全局环境、系统配置或仓库的环境文件。

AR02 在相同 1GB 堆下完成原生/JS 后，D8 `mergeExtDexRelease` 报 `Java heap space`。该失败需要增加本次 Gradle 子进程的堆；降低 Ninja 并发不能解决 DEX 堆不足。本轮 `-Xmx2048m`、其他参数不变后通过，详细结果见 [AR02](../design/2026-09-07-ar02-capture-privacy-implementation.md)。先确认物理内存与 commit 余量，只停止本轮已结束构建的自有 daemon，不修改系统虚拟内存。

如果失败已停在后段，可以在确认源码、原生补丁、生成工程及镜像哈希都未变化后，保留镜像产物，沿 `build_mobile.ps1` §5 的实际 Gradle 参数接续，再完整执行 §6 同等验包。不要重新执行 `/MIR` 或 prebuild 后仍假定原生中间产物全部保留。AR02 核对了 337 个文件，接续保留原 build SHA/时间；随后仅两行 JS 文案更新时，单独同步该文件并重新注入身份。这个实证不允许跳过源码一致性检查，也不允许将旧 APK 归给新 SHA。

## 4. 长构建要留下可接续的结果

交互工具的 session/cell ID 可能随会话中断失效。建议把包装脚本、stdout、stderr、结果 JSON 放在同一个仓库外目录，后台进程结束后仍可读取。目录可统一使用 `%LOCALAPPDATA%\car-agent\artifacts\<批次>-<时间>`，Claude Code 与 Codex 共用；也可以沿用本批已经登记的仓库外位置。

下面是小堆档包装示例，保存为该目录的 `run-build.ps1`，由仓库根作为工作目录启动。它不修改构建脚本和全局配置；要换普通并发档时，在本批记录中明确实际参数。

```powershell
$ErrorActionPreference = 'Continue'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$mobileStarted = Get-Date
$mobileSha = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $mobileSha) { throw 'Not a repository' }
if (@(git status --porcelain).Count -ne 0) { throw 'Use the clean verified tree' }
$mobileExit = 1
try {
    $env:GRADLE_OPTS = '-Dorg.gradle.jvmargs="-Xms128m -Xmx1024m -XX:MaxMetaspaceSize=512m -XX:ActiveProcessorCount=2 -Dfile.encoding=UTF-8" -Dorg.gradle.project.kotlin.compiler.execution.strategy=in-process'
    & powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_mobile.ps1 -Release -Variant prod -CompileJobs 1
    $mobileExit = $LASTEXITCODE
} catch {
    Write-Output $_.Exception.Message
} finally {
    @{
        sha = $mobileSha
        startedAt = $mobileStarted.ToString('o')
        endedAt = (Get-Date).ToString('o')
        exitCode = $mobileExit
        compileJobs = 1
        heap = '128m/1024m'
    } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $PSScriptRoot 'result.json')
}
exit $mobileExit
```

启动示例（先把包装脚本写入所选目录；每次构建使用新目录）：

```powershell
$mobileRepo = (Get-Location).Path
$mobileEvidence = Join-Path $env:LOCALAPPDATA 'car-agent\artifacts\ARxx-YYYYMMDD-HHMMSS'
$mobileRunner = Join-Path $mobileEvidence 'run-build.ps1'
if (-not (Test-Path -LiteralPath $mobileRunner)) { throw 'Write the runner first' }
$mobileArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $mobileRunner))
$mobileProcess = Start-Process -FilePath powershell.exe -ArgumentList $mobileArgs -WorkingDirectory $mobileRepo -WindowStyle Hidden -RedirectStandardOutput (Join-Path $mobileEvidence 'build.log') -RedirectStandardError (Join-Path $mobileEvidence 'build.err.log') -PassThru
$mobileProcess | Select-Object Id, StartTime
```

接续时先查看该目录 `result.json` 是否存在，再结合已登记的 PID/开始时间、日志尾部判断进程状态。没有结果文件只能说明“尚未取得终态”，也可能是包装进程被终止；不要只盯工具的旧 session ID。长任务每次等待控制在 60 秒内并更新进展，避免把会话长期锁在一次等待中。

含中文的 `.ps1` 按项目约定保存为 UTF-8 with BOM，`.gradle` / `.properties` 保持无 BOM；用 `Get-Content -Encoding UTF8` 读 UTF-8 日志。向子进程 stdin 传中文/JSON 时显式设置 `$OutputEncoding`，不要把终端显示正常当作编码未损坏的证明；PNG 另按 [§6](#6-设备取证的几个实测边界) 的二进制方式保存。

成功要求同时满足：子进程退出码 0、Gradle `BUILD SUCCESSFUL`、脚本验包通过、带本次 SHA 的最终 APK 存在。`$LASTEXITCODE` 应在被测命令后立即保存；PowerShell 把 stderr 显示为 `NativeCommandError` 不一定意味着命令失败。反过来，测试汇总 PASS 但进程尚未退出，也不能先填 exit 0。

AR01 留下过 SDK XML、NODE_ENV、NO_COLOR/FORCE_COLOR、Gradle 废弃项、高德库无法 strip 等提示，并分别完成了成功终态与验包。以后仍按具体日志判断，不把这些名字设成通用忽略名单。

## 5. 验包与装机：以实际产物为准

构建脚本会检查 KWS/ORT `.so`、release 内嵌 bundle、`assets/app.config` 的 variant/build SHA，并打印签名指纹和最终 APK 路径。当前保留 arm64-v8a 与 armeabi-v7a；不能为了跑快而缩减 ABI 或改变签名后仍沿用原验收结论。

1. 从本次构建日志取实际 APK 路径，不按目录修改时间猜“最新包”。
2. 正式验包用 clean 的已提交代码；`-dirty` 包不能冒充精确提交。必要时保存源码与构建镜像文件哈希，确认构建期间没有被其他会话覆盖。
3. 按角色运行 `scripts\mobile_device.ps1 -Role test -Install <实际APK>`；同签名 `install -r` 保留已有连接配置。设备权限或签名问题单独处理，不自动清数据/卸载。
4. 回读 `lastUpdateTime`、非 DEBUGGABLE、设置页最底部 `v… · prod · <sha> · <时刻>`。固定 versionCode 不代表两次安装内容相同。
5. 需要严格绑定时，比对本地 `Get-FileHash -Algorithm SHA256` 与设备 `pm path com.xiaozhou.companion` 返回的安装文件 `sha256sum`；先读取路径，再哈希，不硬编码 `/data/app/...`。

`assets/app.config` 还含构建配置；核对时只输出必要的 variant/build 字段，不把整份配置或 token 写入日志。prod release 内嵌 bundle，可以脱离 Metro 验证；dev-client 的验证结果不能直接移给 release。

## 6. 设备取证的几个实测边界

| 现象 | 可复用做法 |
|---|---|
| OPPO 折叠屏截图空白/不是当前屏 | 先查 `adb -s <设备> shell dumpsys SurfaceFlinger --display-id` 和当前显示状态，再用 `screencap -p -d <物理display-id>`；显示 ID 不写成永久常量 |
| PowerShell 5 重定向 PNG 后文件损坏 | 用 Python `subprocess` 捕获 `adb exec-out screencap` 的二进制 stdout，再 `Path.write_bytes`；不要用文本重定向保存 PNG |
| 需要 XML，又不想在手机落临时文件 | 本轮 OPPO 实测 `adb -s <设备> exec-out uiautomator dump /dev/tty` 可读 XML；`/proc/self/fd/1` 只返回成功提示，不能当作取得 XML |
| 动态画廊报 `could not get idle state` | 记录限制。AR01 按实读截图定位打开 Modal 后，静态列表 XML 可读；不要用猜测坐标点击，也不要把关闭动效后的读数当默认配置结果 |
| Maestro 后运行 uiautomator 报 `UiAutomationService already registered` | CLI 退出后设备 instrumentation 可能仍占用连接。确认本轮 Maestro 已终结，再关闭本轮 `dev.mobile.maestro` / `dev.mobile.maestro.test` 驱动后读 XML；不能一边跑流程一边切页或另接 UiAutomation。辅助进程 FATAL 不能算 App 崩溃 |
| adb 零时长 tap 未触发 RNGH 光球 | 先核当前页面和坐标；AR02 用同点 120ms 的触摸序列触发轻点收音。不能把注入未触发直接判为产品缺陷，也不能在 Modal/导航动画尚未结束时点击被遮挡的控件 |
| ColorOS 内屏仍是窄竖屏 / 平行窗口 | 同时读 active input viewport、App window bounds 和 `native-spike` 的 dp/layout；物理屏变大不等于 App 得到宽窗口。AR04 实测默认兼容窗约 392dp，经授权切全屏才得到 652dp 与 drawer/tabletop。切换可能要求应用重启，需重建样本；恢复入口为系统通知“恢复”或设置→大屏专区→兼容模式 |
| 物理旋转和折叠接得很快 | 每个姿态保持后取证，并比较采样前后 base/committed state 与 active viewport。变化期间的 XML/截图可能来自相邻姿态；AR04 曾取到两张相同截图，不能按不同前置状态重复签收 |
| Git Bash 改写 Android 文件路径 | adb 操作用 PowerShell；避免 MSYS 把 `/sdcard/...` 改成宿主路径 |
| App 卡 connecting、宿主云服务却正常 | 先检查手机 Tailscale 是否在线、手机侧 DNS/连接是否正常。两台设备都曾静默掉线；广播不一定能拉起客户端，前台打开后才恢复。此现象不等于 APK 构建失败 |

设备操作前核当前前台、通话和角色；按实读 XML/testID 或截图坐标操作。截图、XML、录音、探针 JSON 和临时脚本保存在仓库外，结束时归还本批占用的测试资源，保留接手所需证据。

样本画廊的按钮可能是 no-op，只能证明布局和触达。业务 operationId、发送队列、设备采集、服务器执行结果需要各自的证据；“UI 上已打断”不证明业务回滚，“样本 mic=off”不证明物理麦克风关闭。

## 7. 跨会话交接最小字段

每批实施记录保留以下信息；长日志和截图用仓库外位置定位，不复制进入口文件：

```text
批次与范围：ARxx；已修/未验/留给下批的部分
代码：checkout、分支、完整 SHA、工作树状态
验证：测试 SHA、命令、退出码、警告/失败与样本限制
构建：variant、CompileJobs/JVM 参数、开始/结束时间、结果文件
运行中资源：负责人、PID/开始时间、镜像目录、设备角色（完成则写已归还）
产物：APK 实际路径、SHA-256、包内 build、设备设置页 build
设备：机型/系统/形态/设置；安装回读；样本验证还是实际业务
证据：仓库外目录与必要文件名；不含密钥
集成：已合入/已推送/未部署的实际边界；下一批入口
```

共享纯逻辑修改需要同时验证 mobile 与 HMI 消费方。测试全部结束后再写最终状态；Jest 退出延迟或冷加载问题按实际结果保留，不能靠放宽超时、删用例或跨 SHA 借用结果收口。更多执行纪律见 [AGENTS.md](../../AGENTS.md)。
