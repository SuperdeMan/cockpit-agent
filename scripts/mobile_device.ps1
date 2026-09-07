# mobile_device.ps1 — 两台验证真机的角色解析与装机（mobile/README.md「验证设备」节）
#
# 角色只认厂商（ro.product.manufacturer），不认序列号：序列号是机器状态不进仓库，
# 换一台同厂商的机器约定不变。
#   test    = OPPO   测试机（adb 驱动的探针 / Maestro / e2e 默认落这里）
#   compare = Xiaomi 对照机（泓舟主用手机；只做对比验证，不跑改设备状态的探针）
#
# 用法：
#   powershell -File scripts\mobile_device.ps1 -List                        # 在线设备与角色
#   powershell -File scripts\mobile_device.ps1 -Role test                   # 打印该角色的序列号（供 adb -s）
#   powershell -File scripts\mobile_device.ps1 -Role test -Install <apk>    # 装机 + 回读（lastUpdateTime / DEBUGGABLE）
# 退出码：0 成功；1 角色不在线 / 装机失败 / 回读不符。
#
# 「装上了」的判据是回读：lastUpdateTime 必须变、装 release 时 flags 不得含 DEBUGGABLE——
# `adb install` 打 Success 只说明 PackageManager 收下了，不说明装的是你以为的那份
# （同「改设置没回读的那次就是没生效的那次」）。
#
# ⚠ 本文件带中文注释，保持 UTF-8 with BOM（build_mobile.ps1 同款判据）。输出串全 ASCII。
param(
    [ValidateSet('test', 'compare')]
    [string]$Role,
    [string]$Install,
    [switch]$List
)

$ErrorActionPreference = 'Stop'

# 角色映射只此一份；要加第三台在这里加，别在文档里另抄一份序列号表
$RoleByManufacturer = @{ 'OPPO' = 'test'; 'Xiaomi' = 'compare' }
$Package = 'com.xiaozhou.companion'

function Fail([string]$msg) {
    Write-Host "[mobile-device] FAIL  $msg" -ForegroundColor Red
    exit 1
}
function Info([string]$msg) {
    Write-Host "[mobile-device] $msg"
}

# adb 优先取 SDK 那份（与 gradle/Expo 同源）；没有 ANDROID_HOME 就用 PATH 上的
$adb = 'adb'
if ($env:ANDROID_HOME) {
    $sdkAdb = Join-Path $env:ANDROID_HOME 'platform-tools\adb.exe'
    if (Test-Path $sdkAdb) { $adb = $sdkAdb }
}

# ---- 在线设备清单（只取 state=device；unauthorized / offline 不算在线）----
$devices = @()
foreach ($line in (& $adb devices -l | Select-Object -Skip 1)) {
    if ($line -notmatch '^(\S+)\s+device\b') { continue }
    $serial = $Matches[1]
    $mfr = "$(& $adb -s $serial shell getprop ro.product.manufacturer)".Trim()
    $model = "$(& $adb -s $serial shell getprop ro.product.model)".Trim()
    $rel = "$(& $adb -s $serial shell getprop ro.build.version.release)".Trim()
    # ⚠ 不能叫 $role：PowerShell 变量名不分大小写，会把参数 $Role 覆盖成最后一台的角色
    #   （首版实测：只有 Xiaomi 在线时 -Role test 也返回它的序列号、退出码 0）
    $devRole = $RoleByManufacturer[$mfr]
    if (-not $devRole) { $devRole = '-' }
    $devices += [pscustomobject]@{ serial = $serial; manufacturer = $mfr; model = $model; android = $rel; role = $devRole }
}

if ($List -or -not $Role) {
    if ($devices.Count -eq 0) { Info 'no device attached (state=device)'; exit 0 }
    foreach ($d in $devices) {
        Info ("{0,-8} {1,-12} {2,-8} {3,-14} Android {4}" -f $d.role, $d.serial, $d.manufacturer, $d.model, $d.android)
    }
    exit 0
}

$hit = @($devices | Where-Object { $_.role -eq $Role })
if ($hit.Count -eq 0) {
    $seen = ($devices | ForEach-Object { "$($_.manufacturer)/$($_.serial)" }) -join ', '
    if (-not $seen) { $seen = 'none' }
    Fail "role '$Role' not attached (attached: $seen)"
}
if ($hit.Count -gt 1) { Fail "role '$Role' matches $($hit.Count) devices; unplug one" }
$dev = $hit[0]

# 只要序列号：走 stdout，供 `$serial = & scripts\mobile_device.ps1 -Role test` 捕获
if (-not $Install) {
    Write-Output $dev.serial
    exit 0
}

# ---- 装机 + 回读 ----
if (-not (Test-Path $Install)) { Fail "apk not found: $Install" }
$apkPath = (Resolve-Path $Install).Path
$before = "$(& $adb -s $dev.serial shell dumpsys package $Package | Select-String 'lastUpdateTime=' | Select-Object -First 1)".Trim()
if (-not $before) { $before = '(not installed)' }
Info "install -r -> $($dev.role) $($dev.manufacturer) $($dev.model) ($($dev.serial))"
Info "before: $before"
$out = @(& $adb -s $dev.serial install -r $apkPath)
$out | ForEach-Object { Info $_ }
if ($LASTEXITCODE -ne 0 -or (($out -join ' ') -notmatch 'Success')) {
    Info "device-side switches: MIUI/HyperOS = developer options 'USB install' + 'USB debugging (security settings)'; ColorOS = allow install via USB"
    Info "fallback (no adb install permission): $adb -s $($dev.serial) push `"$apkPath`" /sdcard/Download/  then tap the file on the phone"
    Fail "adb install failed (exit $LASTEXITCODE)"
}

$dump = @(& $adb -s $dev.serial shell dumpsys package $Package)
$after = "$($dump | Select-String 'lastUpdateTime=' | Select-Object -First 1)".Trim()
$flags = "$($dump | Select-String '^\s+flags=\[' | Select-Object -First 1)".Trim()
$ver = "$($dump | Select-String 'versionName=' | Select-Object -First 1)".Trim()
if ($after -eq $before) { Fail "lastUpdateTime unchanged after install: $after" }
Info "after:  $after"
Info "$ver"
Info "$flags"
if (($apkPath -match 'release') -and ($flags -match 'DEBUGGABLE')) {
    Fail 'installed package is DEBUGGABLE but a release apk was requested'
}
Info 'OK'
