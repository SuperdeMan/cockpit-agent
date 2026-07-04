# 下载 R4.3 端侧语音模型（KWS 唤醒词 + silero VAD）到 hmi/public/models/。等价于 fetch-voice-models.sh。
# 双源可切（GitHub release 主源 / hf-mirror.com 国内镜像）+ curl -C - 断点续传 + sha256 校验。
# 模型二进制 gitignore、切勿提交（体积 + 许可卫生）；沿 certs/ 的「gitignore + 生成脚本」先例。
# 设计见 docs/design/2026-07-04-r4.3-wake-vad-fullduplex.md §4 D7。
#
# 用法：
#   powershell -File scripts/fetch-voice-models.ps1                              # 默认 GitHub 主源
#   $env:VOICE_MODEL_SOURCE="mirror"; powershell -File scripts/fetch-voice-models.ps1  # 国内镜像优先
#   需 curl.exe（Win10+ 自带）；KWS 归档解包需 tar（Win10+ 自带 bsdtar）。
#
# 注：精确 release tag / 文件名 / sha256 由 R4.3 P0 探针实测后 pin（约束先行）——sha 留空表示
#     「下载后打印实测 sha256 供你回填本脚本 + 设计卡 §9」，不因未 pin 而阻断下载。
$ErrorActionPreference = "Stop"
$dst = Join-Path $PSScriptRoot "..\hmi\public\models"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
$dst = (Resolve-Path $dst).Path
$pref = if ($env:VOICE_MODEL_SOURCE) { $env:VOICE_MODEL_SOURCE } else { "github" }

# name / file / github / mirror / sha256(空=跳过校验) / archive(tar.bz2 解包)
# P0 实测（2026-07-04）：GitHub 主源两个模型均 200 可下，VAD 已下载并 pin sha256；
# hf-mirror 的 VAD repo 路径实测 401（repo 名有误），强制 mirror 模式前需 P0 修正——GitHub 主源已足够。
$models = @(
  @{ name = "silero-vad"; file = "silero_vad.onnx";
     gh = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx";
     mirror = "https://hf-mirror.com/csukuangfj/sherpa-onnx-vad-models/resolve/main/silero_vad.onnx";
     sha = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"; archive = $false },
  @{ name = "kws-zipformer"; file = "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2";
     gh = "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2";
     mirror = "https://hf-mirror.com/csukuangfj/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/resolve/main/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2";
     sha = ""; archive = $true }
)

function Try-Download($url, $out) {
  Write-Host "  [try] $url"
  curl.exe -fL -C - --retry 3 --connect-timeout 20 --progress-bar -o $out $url
  return ($LASTEXITCODE -eq 0)
}

foreach ($m in $models) {
  $out = Join-Path $dst $m.file
  Write-Host "[fetch] $($m.name) -> hmi/public/models/$($m.file)"

  if ((Test-Path $out) -and $m.sha -ne "") {
    $h = (Get-FileHash -Algorithm SHA256 $out).Hash.ToLower()
    if ($h -eq $m.sha) { Write-Host "  已存在且 sha256 匹配，跳过。"; continue }
  }

  if ($pref -eq "mirror") { $primary = $m.mirror; $secondary = $m.gh } else { $primary = $m.gh; $secondary = $m.mirror }
  if (-not (Try-Download $primary $out)) {
    Write-Host "  主源失败，回退备源…"
    if (-not (Try-Download $secondary $out)) { throw "两源均下载失败：$($m.name)" }
  }

  $got = (Get-FileHash -Algorithm SHA256 $out).Hash.ToLower()
  if ($m.sha -ne "" -and $got -ne $m.sha) { throw "sha256 不匹配：期望 $($m.sha)，实测 $got" }
  Write-Host "  实测 sha256=$got  （sha 未 pin 时请回填本脚本 + 设计卡 §9）"

  if ($m.archive) {
    Write-Host "  解包 $($m.file) …"
    tar -xjf $out -C $dst
  }
}

Write-Host "[fetch-voice-models] done -> $dst  （模型已 gitignore，切勿提交）"
