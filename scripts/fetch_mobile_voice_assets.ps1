# M4 端侧语音资产取件（Android 陪伴端）：KWS 的 sherpa-onnx 原生件 + 唤醒词模型 + silero VAD。
# 等价定位同 scripts/fetch-voice-models.ps1（那份服务 hmi/ 与服务端声纹），本份只服务 mobile/。
#
# 三类产物，落点各不相同、理由各不相同：
#   1. sherpa-onnx 原生件 → mobile/modules/kws/android/{libs/*.jar, src/main/jniLibs/<abi>/*.so}
#      **不是直接放 .aar**：AGP 禁止 library 模块直接依赖本地 .aar
#      （`Direct local .aar file dependencies are not supported when building an AAR`
#      ——2026-08-28 实测撞到，报在 `:kws:bundleDebugAar`），因为那样产出的 AAR 里
#      不含被依赖 aar 的类与资源，是个坏包。⇒ 拆开：classes.jar 走 libs/，.so 走 jniLibs/。
#      **必须用 static-link-onnxruntime 版**：普通版 AAR 自带 libonnxruntime.so，会和 VAD
#      用的 onnxruntime-react-native 撞同名 .so；static 版把 ORT 静链进 JNI，arm ABI 下
#      根本不产出那个文件（解包实证）。x86/x86_64 那两个 ABI 本脚本**不落地**——
#      x86 恰恰是它唯一仍带 libonnxruntime.so 的 ABI，且真机与 CI 都不需要。
#   2. KWS 唤醒词模型（encoder/decoder/joiner/tokens/keywords）
#                            → mobile/modules/kws/android/src/main/assets/kws/
#      Android library 的 assets 会并进 APK，sherpa 直接经 AssetManager 读。
#      **刻意用 fp32 而非 int8**：与 HMI 逐字同一份模型，唤醒阈值（0.2/2.0）才继续成立。
#      int8 省 ~8MB、但换模型等于换掉阈值的前提，两件事一起改会让唤醒率问题无法定性。
#   3. silero_vad.onnx      → mobile/assets/models/
#      这份走 JS（onnxruntime-react-native + expo-asset），所以要在 RN 资产目录里。
#
# 幂等：已存在且非空即跳过。AAR 用 curl -C - 续传（本网络 GitHub ~100KB/s，36MB 约 5 分钟）。
# ⚠ 单次续传是安全的，但**不要**加 `--retry` 配 `-C -`（坑账 §9.37：重复字节会被插进文件中间）。
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$AAR_VERSION = "1.13.6"
$AAR_NAME = "sherpa-onnx-static-link-onnxruntime-$AAR_VERSION.aar"
$AAR_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/v$AAR_VERSION/$AAR_NAME"
$AAR_SHA = "01e87037afca2ed49085062aace5c012e60321e8e23e3a72b6d9ac02c843f66c"  # 2026-08-28 实测并 pin
$ABIS = @("arm64-v8a", "armeabi-v7a")

$kwsRoot = Join-Path $root "mobile\modules\kws\android"
$kwsLibs = Join-Path $kwsRoot "libs"
$kwsJni = Join-Path $kwsRoot "src\main\jniLibs"
$kwsAssets = Join-Path $kwsRoot "src\main\assets\kws"
$vadAssets = Join-Path $root "mobile\assets\models"
$cache = Join-Path $root ".cache\sherpa"
$hmiModels = Join-Path $root "hmi\public\models"
$kwsSrc = Join-Path $hmiModels "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"

foreach ($d in @($kwsLibs, $kwsJni, $kwsAssets, $vadAssets, $cache)) {
  if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force $d | Out-Null }
}

# ── 1. sherpa 原生件（下载 AAR → 拆 classes.jar + 两个 arm ABI 的 .so）─────────
$jarDst = Join-Path $kwsLibs "sherpa-onnx-classes.jar"
$soName = "libsherpa-onnx-jni.so"
$needExtract = -not (Test-Path $jarDst)
foreach ($abi in $ABIS) {
  if (-not (Test-Path (Join-Path $kwsJni "$abi\$soName"))) { $needExtract = $true }
}

if (-not $needExtract) {
  Write-Host "[kws] 原生件已在（classes.jar + $($ABIS -join '/') 的 $soName）"
} else {
  $aar = Join-Path $cache $AAR_NAME
  if ((Test-Path $aar) -and (Get-Item $aar).Length -gt 1MB) {
    Write-Host "[kws] 复用缓存 AAR: $aar ($([math]::Round((Get-Item $aar).Length/1MB,1)) MB)"
  } else {
    Write-Host "[kws] 下载 $AAR_NAME ..."
    & curl.exe -L --fail -C - -o $aar $AAR_URL
    if ($LASTEXITCODE -ne 0) { throw "AAR 下载失败（退出码 $LASTEXITCODE）：$AAR_URL" }
  }
  $sha = (Get-FileHash $aar -Algorithm SHA256).Hash.ToLower()
  if ($AAR_SHA -and $sha -ne $AAR_SHA) { throw "AAR sha256 不符：实测 $sha 期望 $AAR_SHA" }
  Write-Host "[kws] AAR sha256 = $sha"

  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip = [System.IO.Compression.ZipFile]::OpenRead($aar)
  try {
    $e = $zip.Entries | Where-Object { $_.FullName -eq "classes.jar" }
    if (-not $e) { throw "AAR 里没有 classes.jar" }
    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($e, $jarDst, $true)
    Write-Host "[kws] classes.jar -> libs/ ($([math]::Round((Get-Item $jarDst).Length/1KB,0)) KB)"
    foreach ($abi in $ABIS) {
      $entry = $zip.Entries | Where-Object { $_.FullName -eq "jni/$abi/$soName" }
      if (-not $entry) { throw "AAR 里没有 jni/$abi/$soName" }
      $dstDir = Join-Path $kwsJni $abi
      if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force $dstDir | Out-Null }
      $dst = Join-Path $dstDir $soName
      [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dst, $true)
      Write-Host "[kws] jni/$abi/$soName -> jniLibs/ ($([math]::Round((Get-Item $dst).Length/1MB,1)) MB)"
    }
  } finally {
    $zip.Dispose()
  }
}

# ── 2. KWS 模型 ───────────────────────────────────────────────────────────────
if (-not (Test-Path $kwsSrc)) {
  throw "找不到 $kwsSrc —— 先跑 scripts/fetch-voice-models.ps1（KWS 模型与 HMI 共用同一份下载）"
}
$MODEL_TAG = "epoch-12-avg-2-chunk-16-left-64"
$needed = @("encoder-$MODEL_TAG.onnx", "decoder-$MODEL_TAG.onnx", "joiner-$MODEL_TAG.onnx",
            "tokens.txt", "keywords.txt")
foreach ($f in $needed) {
  $src = Join-Path $kwsSrc $f
  $dst = Join-Path $kwsAssets $f
  if (-not (Test-Path $src)) { throw "KWS 源文件缺失: $src" }
  if ((Test-Path $dst) -and (Get-Item $dst).Length -eq (Get-Item $src).Length) { continue }
  Copy-Item $src $dst -Force
  Write-Host "[kws] 复制 $f ($([math]::Round((Get-Item $dst).Length/1MB,2)) MB)"
}

# ── 2b. KWS 自带测试音频（直灌探针用）───────────────────────────────────────────
# 为什么要它：真机上「零命中」有两种完全不同的成因——**引擎不认** vs **麦克风没听见**，
# 而它们在屏上长得一模一样。用模型自带的音频**绕过麦克风直灌**，一次就把两者分开。
# 体积很小（7 条共 ~1.1MB），值这个诊断力。
$kwsWavs = Join-Path $vadAssets "kws_test"
if (-not (Test-Path $kwsWavs)) { New-Item -ItemType Directory -Force $kwsWavs | Out-Null }
$wavSrc = Join-Path $kwsSrc "test_wavs"
if (Test-Path $wavSrc) {
  foreach ($w in @("0.wav","1.wav","2.wav","3.wav","4.wav","5.wav","6.wav","test_keywords.txt")) {
    $src = Join-Path $wavSrc $w
    $dst = Join-Path $kwsWavs $w
    if ((Test-Path $src) -and (-not (Test-Path $dst) -or (Get-Item $dst).Length -ne (Get-Item $src).Length)) {
      Copy-Item $src $dst -Force
      Write-Host "[kws] 测试音频 $w"
    }
  }
}

# ── 3. silero VAD ─────────────────────────────────────────────────────────────
$vadSrc = Join-Path $hmiModels "silero_vad.onnx"
$vadDst = Join-Path $vadAssets "silero_vad.onnx"
if (-not (Test-Path $vadSrc)) {
  throw "找不到 $vadSrc —— 先跑 scripts/fetch-voice-models.ps1"
}
if (-not (Test-Path $vadDst) -or (Get-Item $vadDst).Length -ne (Get-Item $vadSrc).Length) {
  Copy-Item $vadSrc $vadDst -Force
  Write-Host "[vad] 复制 silero_vad.onnx ($([math]::Round((Get-Item $vadDst).Length/1KB,0)) KB)"
} else {
  Write-Host "[vad] silero_vad.onnx 已在"
}

Write-Host ""
Write-Host "[done] M4 语音资产就位。下一步：scripts\build_mobile.ps1（新增原生依赖必须重建 + 重装 APK）"
