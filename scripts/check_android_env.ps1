# Android 陪伴端 App（mobile/）开工前置环境自检——对应实施计划
# docs/design/2026-08-24-mobile-app-implementation-plan.md §1 的 E1-E6。
#
# 为什么要有这个脚本：E1-E6 是一次性动作，但"装过了"和"现在还好使"是两件事
# （PATH 被别的安装器改、JDK 被升级、Tailscale 掉线、设备没插、licenses 没接受）。
# 逐条机器验，别靠人记得。
#
# 用法： powershell -ExecutionPolicy Bypass -File scripts\check_android_env.ps1
# 退出码：0=可开工（可能带 WARN）；1=有 FAIL，先修再开工。
# 输出一律 ASCII：本文件按仓库惯例无 BOM，PS 5.1 会把中文注释按 ANSI 解码（注释无所谓），
# 但输出串若用中文就会是乱码。

$ErrorActionPreference = "Continue"

$script:pass = 0; $script:warn = 0; $script:fail = 0

function Report($id, $name, $status, $detail) {
  if ($status -eq "PASS") { $script:pass++ }
  elseif ($status -eq "WARN") { $script:warn++ }
  else { $script:fail++ }
  Write-Host ("[android-env] {0,-3} {1,-15} {2,-4} {3}" -f $id, $name, $status, $detail)
}

# 用注册表当前值组装 PATH，避免"改了变量但本进程还是旧值"造成的假读数
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$env:Path = "$machinePath;$userPath"
$env:JAVA_HOME = [Environment]::GetEnvironmentVariable('JAVA_HOME', 'User')
$env:ANDROID_HOME = [Environment]::GetEnvironmentVariable('ANDROID_HOME', 'User')

# ---------- E1 JDK ----------
$javaOk = $false
if (-not $env:JAVA_HOME) {
  Report "E1" "JDK" "FAIL" "JAVA_HOME not set"
}
elseif (-not (Test-Path "$env:JAVA_HOME\bin\javac.exe")) {
  Report "E1" "JDK" "FAIL" "JAVA_HOME points at a non-JDK (no bin\javac.exe): $env:JAVA_HOME"
}
else {
  $v = ((& "$env:JAVA_HOME\bin\java.exe" -version 2>&1 | Select-Object -First 1) -replace '"', '')
  $major = 0
  if ($v -match 'version\s+(\d+)\.') { $major = [int]$Matches[1] }
  if ($major -eq 17) { Report "E1" "JDK" "PASS" "$v"; $javaOk = $true }
  elseif ($major -gt 17) { Report "E1" "JDK" "WARN" "$v (plan pins JDK 17; AGP/RN may reject newer)"; $javaOk = $true }
  else { Report "E1" "JDK" "FAIL" "$v (need JDK 17)" }
}

# ---------- E1 Android SDK ----------
if (-not $env:ANDROID_HOME) {
  Report "E1" "SDK root" "FAIL" "ANDROID_HOME not set"
}
elseif (-not (Test-Path $env:ANDROID_HOME)) {
  Report "E1" "SDK root" "FAIL" "ANDROID_HOME missing on disk: $env:ANDROID_HOME"
}
else {
  Report "E1" "SDK root" "PASS" $env:ANDROID_HOME

  $smgr = "$env:ANDROID_HOME\cmdline-tools\latest\bin\sdkmanager.bat"
  if (Test-Path $smgr) {
    if ($javaOk) {
      $sv = (& $smgr --version 2>&1 | Where-Object { $_ -match '^\d' } | Select-Object -Last 1)
      Report "E1" "sdkmanager" "PASS" "v$sv"
    }
    else {
      Report "E1" "sdkmanager" "WARN" "present but cannot run without a working JDK"
    }
  }
  else {
    Report "E1" "sdkmanager" "FAIL" "not at $smgr"
  }

  $adbSdk = "$env:ANDROID_HOME\platform-tools\adb.exe"
  if (Test-Path $adbSdk) { Report "E1" "platform-tools" "PASS" $adbSdk }
  else { Report "E1" "platform-tools" "FAIL" "missing: $adbSdk" }

  foreach ($c in @(
      @{ n = "platforms"; p = "$env:ANDROID_HOME\platforms" },
      @{ n = "build-tools"; p = "$env:ANDROID_HOME\build-tools" })) {
    if (Test-Path $c.p) {
      $names = @(Get-ChildItem $c.p -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
      if ($names.Count -eq 0) { Report "E1" $c.n "FAIL" "directory exists but has no installed package" }
      else { Report "E1" $c.n "PASS" ($names -join ", ") }
    }
    else {
      Report "E1" $c.n "FAIL" "missing: $($c.p)"
    }
  }

  if (Test-Path "$env:ANDROID_HOME\licenses\android-sdk-license") { Report "E1" "sdk licenses" "PASS" "accepted" }
  else { Report "E1" "sdk licenses" "FAIL" "not accepted; gradle will refuse to build" }
}

# ---------- E2 环境变量与 PATH ----------
$wantOnPath = @()
if ($env:JAVA_HOME) { $wantOnPath += "$env:JAVA_HOME\bin" }
if ($env:ANDROID_HOME) {
  $wantOnPath += "$env:ANDROID_HOME\platform-tools"
  $wantOnPath += "$env:ANDROID_HOME\cmdline-tools\latest\bin"
}
$allPathEntries = ($machinePath -split ';') + ($userPath -split ';')
$missing = @($wantOnPath | Where-Object { $allPathEntries -notcontains $_ })
if ($missing.Count -eq 0) { Report "E2" "PATH" "PASS" "jdk\bin + platform-tools + cmdline-tools on PATH" }
else { Report "E2" "PATH" "FAIL" ("not on PATH: " + ($missing -join " | ")) }

$gh = [Environment]::GetEnvironmentVariable('GRADLE_USER_HOME', 'User')
if ($gh) { Report "E2" "GRADLE_HOME" "PASS" $gh }
else { Report "E2" "GRADLE_HOME" "WARN" "unset; gradle cache grows under C:\Users\<you>\.gradle" }

# 长路径：gradle 中间产物（app/build/intermediates/.../dexBuilderDebug/out/...）很容易过 260。
# 这是【本机状态不是仓库状态】——换机器/重装要重设，所以让脚本守着，别靠人记得。
# 它不替代 subst：LongPaths 治"路径太长"，subst 治"路径有中文"，是两个病。
$lp = Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -ErrorAction SilentlyContinue
if ($lp -and $lp.LongPathsEnabled -eq 1) { Report "E2" "LongPaths" "PASS" "HKLM LongPathsEnabled=1" }
else { Report "E2" "LongPaths" "WARN" "HKLM LongPathsEnabled=0; deep gradle outputs may hit PathTooLongException (needs admin to set 1)" }

$glp = (& git config --global core.longpaths)
if ("$glp".Trim() -eq "true") { Report "E2" "git longpaths" "PASS" "core.longpaths=true" }
else { Report "E2" "git longpaths" "WARN" "git config --global core.longpaths is not true" }

# adb 重影：机器级 PATH 里另有一份 platform-tools 时，命令行拿到的 adb 不是 SDK 那份
# （Expo/gradle 走 ANDROID_HOME 那份，人手敲 adb 走 PATH 那份）。
# 真会出事的是【协议版本】不一致——"adb server version doesn't match this client; killing..."
# 两份互相杀 server，设备时有时无。包版本（Version 37.0.1-xxxx）不同不要紧，
# 判据只认第一行 "Android Debug Bridge version 1.0.NN"。
$adbCmd = Get-Command adb -ErrorAction SilentlyContinue
if ($adbCmd -and $env:ANDROID_HOME) {
  $sdkAdb = "$env:ANDROID_HOME\platform-tools\adb.exe"
  if ((Test-Path $sdkAdb) -and ($adbCmd.Source -ne $sdkAdb)) {
    $protoShell = "$(@(& $adbCmd.Source version | Select-String -Pattern 'Bridge version ')[0])".Trim()
    $protoSdk = "$(@(& $sdkAdb version | Select-String -Pattern 'Bridge version ')[0])".Trim()
    $pkgShell = "$(@(& $adbCmd.Source version | Select-String -CaseSensitive -Pattern '^Version ')[0])".Trim()
    $pkgSdk = "$(@(& $sdkAdb version | Select-String -CaseSensitive -Pattern '^Version ')[0])".Trim()
    if ($protoShell -eq $protoSdk) {
      Report "E2" "adb shadow" "PASS" "two copies, same protocol [$protoShell]; shell=$pkgShell sdk=$pkgSdk"
    }
    else {
      Report "E2" "adb shadow" "WARN" "protocol differs: shell [$protoShell] vs SDK [$protoSdk] - they will kill each other's server"
    }
  }
}

# ---------- E3 真机 ----------
if ($adbCmd) {
  $devLines = @(& $adbCmd.Source devices 2>&1 | Select-Object -Skip 1 | Where-Object { $_ -match '\S' })
  $ready = @($devLines | Where-Object { $_ -match '\sdevice$' })
  $unauth = @($devLines | Where-Object { $_ -match 'unauthorized' })
  if ($ready.Count -gt 0) {
    Report "E3" "devices" "PASS" ("{0} attached: {1}" -f $ready.Count, (($ready | ForEach-Object { ($_ -split '\s+')[0] }) -join ", "))
  }
  elseif ($unauth.Count -gt 0) {
    Report "E3" "devices" "WARN" "device attached but UNAUTHORIZED - confirm the USB debugging prompt on the device"
  }
  else {
    Report "E3" "devices" "WARN" "no device attached (fine until a real-device run is needed)"
  }
}
else {
  Report "E3" "devices" "FAIL" "adb not on PATH"
}

# ---------- E4 Tailscale + 云栈可达 ----------
$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (Test-Path $ts) {
  $st = @(& $ts status 2>&1)
  $stText = ($st -join "`n")
  if ($st.Count -gt 0 -and $stText -notmatch 'Logged out|stopped') {
    $androidPeers = @($st | Where-Object { $_ -match '\sandroid\s' })
    if ($androidPeers.Count -gt 0) {
      $online = @($androidPeers | Where-Object { $_ -notmatch 'offline' })
      if ($online.Count -gt 0) {
        Report "E4" "tailnet" "PASS" ("android peer online: " + (($online | ForEach-Object { ($_ -split '\s+')[1] }) -join ", "))
      }
      else {
        Report "E4" "tailnet" "WARN" ("android peer known but offline: " + (($androidPeers | ForEach-Object { ($_ -split '\s+')[1] }) -join ", "))
      }
    }
    else {
      Report "E4" "tailnet" "WARN" "no android peer in this tailnet yet"
    }
  }
  else {
    Report "E4" "tailnet" "FAIL" "tailscale not logged in / stopped"
  }
}
else {
  Report "E4" "tailnet" "FAIL" "tailscale.exe not found"
}

$envFile = Join-Path $PSScriptRoot "..\.env"
if (Test-Path $envFile) {
  $m = Select-String -Path $envFile -Pattern '^TAILNET_FQDN=(.+)$'
  if ($m) {
    $fqdn = $m.Matches[0].Groups[1].Value.Trim()
    try {
      $r = Invoke-WebRequest -Uri "https://${fqdn}:8443/healthz" -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
      Report "E4" "cloud :8443" "PASS" ("/healthz HTTP {0}" -f $r.StatusCode)
    }
    catch {
      $code = $null
      if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
      if ($code) { Report "E4" "cloud :8443" "WARN" ("/healthz HTTP {0} (server answered)" -f $code) }
      else { Report "E4" "cloud :8443" "FAIL" ("unreachable: {0}" -f $_.Exception.Message) }
    }
  }
  else { Report "E4" "cloud :8443" "WARN" "TAILNET_FQDN not in .env" }
}
else { Report "E4" "cloud :8443" "WARN" ".env not found at repo root" }

# ---------- E5 node / npm ----------
if (Get-Command node -ErrorAction SilentlyContinue) {
  $nv = (& node -v).TrimStart('v')
  $nmajor = [int](($nv -split '\.')[0])
  if ($nmajor -ge 20) { Report "E5" "node" "PASS" "v$nv" }
  else { Report "E5" "node" "FAIL" "v$nv (need >= 20)" }
  Report "E5" "npm" "PASS" ("v" + (& npm.cmd -v))
}
else {
  Report "E5" "node" "FAIL" "node not on PATH"
}

# ---------- E6 中文/空格路径预案 ----------
# 2026-08-25 实测：AGP 对非 ASCII 项目路径是【硬拒绝】不是"不稳"——
#   "Your project path contains non-ASCII characters" 直接 fail，逃生门只有
#   android.overridePathCheck=true。所以 subst 是必需项不是保险丝。
# 这里不查"当前有没有映射"（映射由构建脚本按需建、重启即失效），查的是【subst 这条路能不能走通】。
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$nonAscii = [regex]::Matches($repo, '[^\x00-\x7F]').Count
if ($nonAscii -eq 0) {
  Report "E6" "repo path" "PASS" "pure ASCII; native build can run in place"
}
else {
  $used = @(Get-PSDrive -PSProvider FileSystem | Select-Object -ExpandProperty Name)
  $free = @('Z', 'Y', 'X', 'W', 'V', 'U', 'T' | Where-Object { $used -notcontains $_ })[0]
  if (-not $free) {
    Report "E6" "subst" "WARN" ("repo path has {0} non-ASCII char(s); no free drive letter to test subst with" -f $nonAscii)
  }
  else {
    $null = subst "${free}:" $repo
    $ok = (Test-Path "${free}:\CLAUDE.md")
    $null = subst "${free}:" /D
    if ($ok) { Report "E6" "subst" "PASS" ("repo path has {0} non-ASCII char(s); subst works (tested on {1}: then released)" -f $nonAscii, $free) }
    else { Report "E6" "subst" "FAIL" ("subst {0}: mapped but repo not readable through it - native build has no escape hatch" -f $free) }
  }
}

Write-Host ""
Write-Host ("[android-env] SUMMARY  {0} pass / {1} warn / {2} fail" -f $script:pass, $script:warn, $script:fail)
if ($script:fail -gt 0) {
  Write-Host "[android-env] blocked: fix the FAIL rows before starting M0."
  exit 1
}
Write-Host "[android-env] ready for M0 (WARN rows are advisory)."
exit 0
