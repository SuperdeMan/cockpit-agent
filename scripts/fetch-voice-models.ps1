# 下载本仓库用到的本地推理模型：R4.3 端侧语音（KWS 唤醒词 + silero VAD，→ hmi/public/models/）
# 与 M4 P4 服务端声纹（CAM++ ONNX，→ models/voiceprint/）。等价于 fetch-voice-models.sh。
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
$only = if ($args.Count -gt 0) { $args[0] } else { "" }   # 可选：只拉某一个（如 `… voiceprint-campplus`）
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pref = if ($env:VOICE_MODEL_SOURCE) { $env:VOICE_MODEL_SOURCE } else { "github" }

# name / dest(相对仓库根) / file / github / mirror / sha256(空=跳过校验) / archive(tar.bz2 解包)
# P0 实测（2026-07-04）：GitHub 主源两个模型均 200 可下，VAD 已下载并 pin sha256；
# hf-mirror 的 VAD repo 路径实测 401（repo 名有误），强制 mirror 模式前需 P0 修正——GitHub 主源已足够。
# P4 实测（2026-07-25）：声纹模型仅 GitHub release 有 ONNX（ModelScope 官方仓库只有 PyTorch 权重、
# hf-mirror 的 campplus repo 路径 404）；GitHub 在本机实测约 25KB/s，28MB 需十余分钟——**必须靠
# `curl -C -` 续传多次重跑**，这正是本脚本幂等的价值。拉不到不阻塞：网关声纹面自动决议 disabled。
$models = @(
  @{ name = "silero-vad"; dest = "hmi/public/models"; file = "silero_vad.onnx";
     gh = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx";
     mirror = "https://hf-mirror.com/csukuangfj/sherpa-onnx-vad-models/resolve/main/silero_vad.onnx";
     sha = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"; archive = $false },
  @{ name = "kws-zipformer"; dest = "hmi/public/models"; file = "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2";
     gh = "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2";
     mirror = "https://hf-mirror.com/csukuangfj/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/resolve/main/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2";
     sha = ""; archive = $true },
  @{ name = "voiceprint-campplus"; dest = "models/voiceprint"; file = "campplus_zh-cn_16k-common.onnx";
     gh = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx";
     mirror = "https://hf-mirror.com/csukuangfj/speaker-embedding-models/resolve/main/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx";
     sha = "f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11"; archive = $false }
)

function Try-Download($url, $out) {
  Write-Host "  [try] $url"
  curl.exe -fL -C - --retry 3 --connect-timeout 20 --progress-bar -o $out $url
  return ($LASTEXITCODE -eq 0)
}

foreach ($m in $models) {
  if ($only -ne "" -and $only -ne $m.name) { continue }
  $dst = Join-Path $root ($m.dest -replace '/', '\')
  New-Item -ItemType Directory -Force -Path $dst | Out-Null
  $dst = (Resolve-Path $dst).Path
  $out = Join-Path $dst $m.file
  Write-Host "[fetch] $($m.name) -> $($m.dest)/$($m.file)"

  if ((Test-Path $out) -and $m.sha -ne "") {
    $h = (Get-FileHash -Algorithm SHA256 $out).Hash.ToLower()
    if ($h -eq $m.sha) { Write-Host "  已存在且 sha256 匹配，跳过。"; continue }
  }

  if ($pref -eq "mirror") { $primary = $m.mirror; $secondary = $m.gh } else { $primary = $m.gh; $secondary = $m.mirror }
  if (-not (Try-Download $primary $out)) {
    Write-Host "  主源失败，回退备源…"
    if (-not (Try-Download $secondary $out)) {
      # 拉不到不是致命错：消费方（HMI 端侧语音 / 网关声纹面）都有「模型缺失即诚实禁用」的档，
      # 在这里 throw 会让「顺手补一个模型」变成「整个脚本白跑」。重跑即续传。
      Write-Warning "  两源均失败：$($m.name)（可重跑本脚本续传；缺失时消费方自动降级）"
      continue
    }
  }

  $got = (Get-FileHash -Algorithm SHA256 $out).Hash.ToLower()
  if ($m.sha -ne "" -and $got -ne $m.sha) { throw "sha256 不匹配：期望 $($m.sha)，实测 $got" }
  Write-Host "  实测 sha256=$got  （sha 未 pin 时请回填本脚本 + 设计卡 §9）"

  if ($m.archive) {
    Write-Host "  解包 $($m.file) …"
    tar -xjf $out -C $dst
  }
}

Write-Host "[fetch-voice-models] done（模型已 gitignore，切勿提交）"
