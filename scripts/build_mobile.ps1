# build_mobile.ps1 — Android 陪伴端 App 原生构建（实施计划 M0-2）
#
# 为什么长这样（坑账 §9，全部实测）：
#   1. 仓库路径含中文：原生构建在 ASCII 镜像工作区（D:\Android\builds）进行——subst 双根
#      与真实中文路径单根两个形态均实测不可用，定案证据链见下方 ---- 1. 注释
#   2. gradle wrapper 默认分发源 34KB/s，腾讯镜像 3.6MB/s → prebuild 后先换 distributionUrl
#   3. AGP 构建期内置 SDK 下载器 32KB/s → 缺的 SDK 包用 android CLI 预装（~1MB/s）
#   4. android CLI 退出码不可信 → 一律验产物不验退出码；已安装的包再 install 会崩，先查后装
#   5. Maven Central 本网络 DNS 不可达 → CN 镜像 init script 前置（gradle_cn_mirrors.init.gradle）
#   6. JVM 默认字符集 GBK 会把 node 的 UTF-8 输出解成乱码路径 → jvmargs 强制 file.encoding=UTF-8
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1            # debug APK
#   powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1 -Release   # release（M5 前用 debug keystore）
#   powershell -ExecutionPolicy Bypass -File scripts\build_mobile.ps1 -Clean     # prebuild --clean（重生成 android/）
#
# ⚠ 本文件带中文注释，必须保持 UTF-8 with BOM（坑账 §9.2：无 BOM 会被 PS 5.1 按 GBK
#   有状态解码吞行，代码静默不执行）。而下方写 gradle-wrapper.properties 时反过来必须无 BOM。

param(
    [switch]$Release,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$MobileReal = Join-Path $RepoRoot 'mobile'

function Fail([string]$msg) {
    Write-Host "[build-mobile] FAIL  $msg" -ForegroundColor Red
    exit 1
}
function Info([string]$msg) {
    Write-Host "[build-mobile] $msg"
}

# Git Bash 的真实路径（react-native-audio-api 的 downloadPrebuiltBinaries 在 Windows
# 分支写死了 C 盘那个绝对路径，本机 Git 装在 D 盘 ⇒ 起进程失败，见坑账）。
# 探测顺序刻意是「git.exe 反推」优先：PATH 上叫 bash.exe 的还有 WSL 启动器
# （System32）和商店存根（WindowsApps），拿到那两个比拿不到更糟。
function Resolve-GitBash {
    $gitExe = (Get-Command git.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    if ($gitExe) {
        # ...\Git\cmd\git.exe 或 ...\Git\mingw64\bin\git.exe → 往上找到 Git 根
        $dir = Split-Path -Parent $gitExe
        $up1 = Split-Path -Parent $dir
        foreach ($root in @($up1, (Split-Path -Parent $up1))) {
            if (-not $root) { continue }
            $cand = Join-Path $root 'usr\bin\bash.exe'
            if (Test-Path $cand) { return $cand }
        }
    }
    foreach ($c in (Get-Command bash.exe -All -ErrorAction SilentlyContinue)) {
        if ($c.Source -and $c.Source -notmatch 'System32|WindowsApps' -and (Test-Path $c.Source)) {
            return $c.Source
        }
    }
    return $null
}

# ---- 0. 前置 ----
if (-not (Test-Path (Join-Path $MobileReal 'node_modules'))) {
    Fail "mobile/node_modules 不存在——先在 mobile/ 里跑 npm install"
}
if (-not $env:ANDROID_HOME) { Fail 'ANDROID_HOME 未设置（先跑 scripts\check_android_env.ps1）' }
if (-not $env:JAVA_HOME) { Fail 'JAVA_HOME 未设置（先跑 scripts\check_android_env.ps1）' }

# ---- 1. 路径形态：ASCII 镜像工作区（2026-08-25 六连败收敛出的定案）----
# 三个形态全部实测排除后仅剩此路：
#   ① subst 双根：require.resolve 不解析 subst（产 X:）、RN CLI fs.realpathSync.native
#     解析 subst（产 D:中文），同一构建两套盘符根——codegen / KGP 增量编译等每个做
#     Path.relativize 的子系统逐一炸「different roots」，鼹鼠打不完；
#   ② 真实中文路径单根 + overridePathCheck：AGP 那个检查的原判是对的——CMake PCH
#     把中文路径以错误编码写进生成头文件（<B2><FA>? 字节残骸），clang 打不开，
#     NDK 链路真实断裂（worklets buildCMakeDebug 实证）；
#   ③ junction：native realpath 同样穿透，等价 ①。
# ⇒ 唯一单根且全 ASCII 的形态：构建前把 mobile/ 增量镜像到 ASCII 路径，在镜像里
#   prebuild+gradle。debug 构建不读 src/ 与 hmi/（JS 不打包），镜像自洽；
#   -Release 要打 JS bundle 时此形态需再评估（M5 前用不上，坑账已记）。
# 落点跟随既有惯例（SDK/缓存都在 D:\Android）；/XD 排除的 android|.expo 在镜像侧
# 保留不删（robocopy /MIR + /XD 语义），原生增量编译得以延续。
$BuildRoot = 'D:\Android\builds\xiaozhou-mobile'
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
Info "镜像 mobile/ -> $BuildRoot（robocopy /MIR，首次分钟级、增量秒级）"
# ⚠ /XD 传裸名字是【按目录名匹配全树】——`/XD android` 会把 node_modules 里每个包的
#   android/ 原生源码目录一并排除（2026-08-25 实测翻车：config-plugins/build/android
#   被排空，prebuild 直接 Cannot find module）。必须用绝对路径只挡这两个顶层目录：
#   源侧 android/（仓库里若有残留生成物，不参与镜像）与镜像侧 android/.expo
#   （prebuild/gradle 的产物，/MIR 不许把它们当「多余文件」删掉）。
robocopy $MobileReal $BuildRoot /MIR /XD "$MobileReal\android" "$MobileReal\.expo" "$BuildRoot\android" "$BuildRoot\.expo" /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "robocopy 镜像失败（exit $LASTEXITCODE）" }
$MobileX = $BuildRoot

# ---- 2. expo prebuild（CNG：android/ 是生成物，真相源是 app.config.ts + plugins）----
Push-Location $MobileX
try {
    # prebuild 判定受管文件变化时会整目录 Clear 重生成，而活着的 gradle daemon 握着
    # android/ 里的文件锁 ⇒ EBUSY（2026-08-25 实测）。先停 daemon（秒级，幂等）。
    if (Test-Path 'android\gradlew.bat') {
        Push-Location 'android'
        try { & .\gradlew.bat --stop --console=plain | Out-Null } catch { }
        Pop-Location
    }
    $env:CI = '1'   # 非交互
    $prebuildArgs = @('expo', 'prebuild', '-p', 'android', '--no-install')
    if ($Clean) { $prebuildArgs += '--clean' }
    Info "npx $($prebuildArgs -join ' ')"
    & npx.cmd @prebuildArgs
    if ($LASTEXITCODE -ne 0) { Fail "expo prebuild 失败（exit $LASTEXITCODE）" }
    if (-not (Test-Path 'android\gradlew.bat')) { Fail 'prebuild 未产出 android/gradlew.bat' }
    # 统一盘符根由 config plugin（plugins/with-unified-drive-root.js）在 prebuild 内产出，
    # 这里只做机器断言：标记必须在，缺了就是插件没接上（app.config.ts plugins 少了它）
    if ((Get-Content 'android\app\build.gradle' -Raw).IndexOf('with-unified-drive-root') -lt 0) {
        Fail 'app/build.gradle 缺 with-unified-drive-root 标记——config plugin 未生效'
    }

    # ---- 3. gradle wrapper 换腾讯镜像（默认源 34KB/s；.properties 必须无 BOM）----
    $wrapperProps = 'android\gradle\wrapper\gradle-wrapper.properties'
    $txt = [System.IO.File]::ReadAllText((Resolve-Path $wrapperProps))
    $patched = $txt -replace 'https\\://services\.gradle\.org/distributions/', 'https\://mirrors.cloud.tencent.com/gradle/'
    if ($patched -ne $txt) {
        [System.IO.File]::WriteAllText((Resolve-Path $wrapperProps), $patched, (New-Object System.Text.UTF8Encoding $false))
        Info 'gradle distributionUrl -> mirrors.cloud.tencent.com'
    } else {
        Info 'gradle distributionUrl 已是镜像或格式变化（未改动）'
    }

    # ---- 3b. gradle.properties 补 -Dfile.encoding=UTF-8（同样必须无 BOM）----
    # 实测（2026-08-25 首构失败根因）：expo autolinking 由 gradle 经 `providers.exec` 起 node
    # 输出 UTF-8 JSON，`asText` 按 JVM 默认字符集（本机 GBK）解码——若路径里出现中文
    # （node 的 realpath 会把 subst 盘解析回 D:\...\产品\... 真实路径），JVM 内就是乱码路径，
    # 报「projectDirectory 不存在」。日志里路径看着是干净中文，是 GBK 解码→GBK 编码
    # 字节保真的回环假象，别被它骗。Java 17 认 -Dfile.encoding。
    $gradleProps = 'android\gradle.properties'
    $gp = [System.IO.File]::ReadAllText((Resolve-Path $gradleProps))
    $gpDirty = $false
    if ($gp -match '(?m)^org\.gradle\.jvmargs=(.*)$') {
        if ($Matches[1] -notmatch 'file\.encoding') {
            $gp = $gp -replace '(?m)^(org\.gradle\.jvmargs=.*)$', '$1 -Dfile.encoding=UTF-8'
            $gpDirty = $true
            Info 'gradle.properties: jvmargs += -Dfile.encoding=UTF-8'
        }
    } else {
        $gp = $gp.TrimEnd() + "`norg.gradle.jvmargs=-Dfile.encoding=UTF-8`n"
        $gpDirty = $true
        Info 'gradle.properties: 新增 org.gradle.jvmargs=-Dfile.encoding=UTF-8'
    }
    if ($gpDirty) {
        [System.IO.File]::WriteAllText((Resolve-Path $gradleProps), $gp, (New-Object System.Text.UTF8Encoding $false))
    }

    # ---- 4. 预装缺失 SDK 包（别让 AGP 构建期现拉）----
    # 版本真相源：SDK 57 起生成的 build.gradle 不再带 ext 版本块（expo-root-project 插件
    # 构建期注入），静态可解析的权威表是 react-native/gradle/libs.versions.toml。
    $sdk = $env:ANDROID_HOME
    $androidCli = Join-Path $sdk 'cmdline-tools\latest\bin\android.exe'
    if (-not (Test-Path $androidCli)) { Fail "android CLI 不存在：$androidCli" }
    $versionsToml = 'node_modules\react-native\gradle\libs.versions.toml'
    if (-not (Test-Path $versionsToml)) { Fail "$versionsToml 不存在（node_modules 装全了吗）" }
    $toml = Get-Content $versionsToml -Raw

    function TomlVal([string]$key, [string]$raw) {
        $mm = [regex]::Match($raw, "(?m)^\s*$key\s*=\s*`"([^`"]+)`"")
        if ($mm.Success) { return $mm.Groups[1].Value }
        return $null
    }

    $toInstall = @()
    $compileSdk = TomlVal 'compileSdk' $toml
    if ($compileSdk) {
        if (-not (Test-Path (Join-Path $sdk "platforms\android-$compileSdk"))) { $toInstall += "platforms;android-$compileSdk" }
    } else { Info 'WARN libs.versions.toml 没解析出 compileSdk（AGP 可能构建期自拉，慢）' }

    $bt = TomlVal 'buildTools' $toml
    if ($bt) {
        if (-not (Test-Path (Join-Path $sdk "build-tools\$bt"))) { $toInstall += "build-tools;$bt" }
    }

    $ndk = TomlVal 'ndkVersion' $toml
    if ($ndk) {
        if (-not (Test-Path (Join-Path $sdk "ndk\$ndk"))) { $toInstall += "ndk;$ndk" }
    } else { Info 'WARN 没解析出 ndkVersion——若构建期 AGP 自拉 NDK 会极慢，注意观察' }

    # C++ 依赖（reanimated/worklets）要 CMake；AGP 缺省要 3.22.1
    $hasNativeCpp = (Test-Path 'node_modules\react-native-reanimated') -or (Test-Path 'node_modules\react-native-worklets')
    if ($hasNativeCpp -and -not (Test-Path (Join-Path $sdk 'cmake'))) { $toInstall += 'cmake;3.22.1' }

    foreach ($pkg in $toInstall) {
        Info "预装 SDK 包：$pkg（android CLI；退出码不可信，装完验产物）"
        # ⚠ 不加 2>&1：PS 5.1 对原生命令的 stderr 重定向会包成 ErrorRecord，
        # 在 $ErrorActionPreference='Stop' 下可能把正常进度输出当错误终止脚本
        & $androidCli sdk install $pkg | Out-Host
        $parts = $pkg -split ';'
        $artifact = Join-Path $sdk ($parts -join '\')
        if ($pkg -like 'platforms;*') { $artifact = Join-Path $sdk "platforms\$($parts[1])" }
        if (-not (Test-Path $artifact)) { Fail "SDK 包 $pkg 安装后产物缺失（$artifact）" }
    }
    if ($toInstall.Count -eq 0) { Info 'SDK 包齐全，无需预装' }

    # ---- 5. gradle 构建 ----
    $task = 'assembleDebug'
    $variantDir = 'debug'
    if ($Release) { $task = 'assembleRelease'; $variantDir = 'release' }
    Push-Location 'android'
    try {
        # 双保险第二层：node 模块解析不做 realpath，autolinking 产出的路径留在 X: 盘
        # （纯 ASCII）——既避开上面的字符集解码坑，也避开 AGP 对含中文 projectDir 的
        # 硬拒绝（included 模块工程的 projectDir 若被解析回 D:\...\产品\... 就会撞上）。
        # 本仓库 node_modules 无 symlink 依赖，preserve-symlinks 行为等价。
        # ⚠ 不要设 NODE_OPTIONS=--preserve-symlinks（2026-08-25 实测反例）：它只拦模块解析的
        # realpath，拦不住 RN CLI config 里显式的 realpathSync——结果 expo 侧路径留在 X:、
        # RN 社区库路径在 D:，codegen relativize 直接炸「different roots」。统一让全部
        # node 侧路径落 D: 真实路径：file.encoding=UTF-8 修复后 JVM 能正确处理中文路径，
        # AGP 的非 ASCII 检查实测只看根工程（X:），模块工程 D: 中文可过。
        if ($env:NODE_OPTIONS) { Remove-Item Env:NODE_OPTIONS -ErrorAction SilentlyContinue }
        # 国内镜像前置（repo.maven.apache.org 本网络 DNS 不可达，2026-08-25 实测）
        $initScript = Join-Path $PSScriptRoot 'gradle_cn_mirrors.init.gradle'
        # react-native-audio-api 把 Git Bash 路径写死在 C 盘（2026-08-26 M2-1 实测：
        # 本机 Git 在 D 盘 ⇒ downloadPrebuiltBinaries 起不了进程，整构建停在 preBuild）。
        # 真实路径在这里探测、经 -P 传给 init script 覆盖；探不到就不传，
        # 让库自己的默认值决定成败（不静默造一个假路径出来）。
        $gradleArgs = @($task, '--console=plain', '-I', $initScript)
        $bashExe = Resolve-GitBash
        if ($bashExe) {
            Info "audio-api bash: $bashExe"
            $gradleArgs += "-PaudioApiBashPath=$bashExe"
        } else {
            Info 'WARN 没探到 Git Bash——react-native-audio-api 的预编译产物下载可能失败'
        }
        Info "gradlew $task（首跑会拉依赖，几分钟量级；CN 镜像 init script）"
        & .\gradlew.bat @gradleArgs
        if ($LASTEXITCODE -ne 0) { Fail "gradle $task 失败（exit $LASTEXITCODE）" }
    } finally {
        Pop-Location
    }

    # ---- 6. 验产物 ----
    $apkDir = Join-Path $MobileX "android\app\build\outputs\apk\$variantDir"
    $apk = Get-ChildItem $apkDir -Filter '*.apk' -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $apk) { Fail "构建报成功但 $apkDir 下无 APK——按坑账验产物原则判失败" }
    Info "APK：$($apk.FullName)"
    Info "安装：adb install -r `"$($apk.FullName)`""
} finally {
    Pop-Location
}
