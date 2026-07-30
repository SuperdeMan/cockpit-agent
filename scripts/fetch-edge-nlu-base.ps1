# 拉取端侧 NLU 的底座 encoder（M5 P3）——等价于 fetch-edge-nlu-base.sh，见该文件的注释。
# 源是 ModelScope 而不是 HuggingFace：本机实测 HF LFS 28-47KB/s、ModelScope 3MB/s。
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dst = Join-Path $root "models\nlu\base"
$base = if ($env:EDGE_NLU_BASE_URL) { $env:EDGE_NLU_BASE_URL } else {
  "https://www.modelscope.cn/models/iic/nlp_structbert_backbone_tiny_std/resolve/master" }
New-Item -ItemType Directory -Force -Path $dst | Out-Null

foreach ($f in @("config.json", "vocab.txt", "pytorch_model.bin")) {
  $out = Join-Path $dst $f
  if ((Test-Path $out) -and ($f -ne "pytorch_model.bin")) {
    Write-Host "  已存在，跳过 $f"; continue
  }
  Write-Host "[fetch] $f -> models/nlu/base/"
  curl.exe -fL -C - --retry 3 --connect-timeout 20 --progress-bar -o $out "$base/$f"
  if ($LASTEXITCODE -ne 0) {
    # 拉不到不阻塞：端侧 NLU 决议 disabled，整链回落规则（同声纹 disabled 先例）
    Write-Warning "  下载失败：$f（重跑本脚本续传；缺失时端侧 NLU 自动 disabled）"
  }
}

$cfg = Join-Path $dst "config.json"
if (Test-Path $cfg) {
  $c = Get-Content $cfg -Raw | ConvertFrom-Json
  $w = Join-Path $dst "pytorch_model.bin"
  $mb = if (Test-Path $w) { "{0:N1}MB" -f ((Get-Item $w).Length / 1e6) } else { "缺失" }
  Write-Host "[ok] 层数 $($c.num_hidden_layers) / hidden $($c.hidden_size) / vocab $($c.vocab_size) / 权重 $mb"
} else {
  Write-Warning "config.json 缺失"
}
Write-Host "[fetch-edge-nlu-base] done（模型已 gitignore，切勿提交）"
