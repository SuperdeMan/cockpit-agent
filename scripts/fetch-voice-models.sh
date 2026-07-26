#!/usr/bin/env bash
# 下载本仓库用到的本地推理模型：R4.3 端侧语音（KWS 唤醒词 + silero VAD，→ hmi/public/models/）
# 与 M4 P4 服务端声纹（CAM++ ONNX，→ models/voiceprint/）。等价于 fetch-voice-models.ps1。
# 双源可切（GitHub release 主源 / hf-mirror.com 国内镜像）+ curl -C - 断点续传 + sha256 校验。
# 模型二进制 gitignore、切勿提交（体积 + 许可卫生）；沿 certs/ 的「gitignore + 生成脚本」先例。
# 设计见 docs/design/2026-07-04-r4.3-wake-vad-fullduplex.md §4 D7。
#
# 用法：
#   bash scripts/fetch-voice-models.sh                 # 默认 GitHub 主源，失败自动回退 hf-mirror
#   VOICE_MODEL_SOURCE=mirror bash scripts/fetch-voice-models.sh  # 强制国内镜像优先
#   需 curl；KWS 归档解包需 tar + bzip2。
#
# 注：精确 release tag / 文件名 / sha256 由 R4.3 P0 探针实测后 pin（约束先行）——当前 EXPECTED_SHA256
#     留空表示「下载后打印实测 sha256 供你回填本脚本 + 设计卡 §9」，不因未 pin 而阻断下载。
set -euo pipefail
cd "$(dirname "$0")/.."
root="$(pwd)"
pref="${VOICE_MODEL_SOURCE:-github}"
only="${1:-}"          # 可选：只拉某一个（如 `bash scripts/fetch-voice-models.sh voiceprint-campplus`）

# 每行：name | dest_dir(相对仓库根) | filename | github_url | mirror_url | expected_sha256(空=跳过校验) | is_archive(1=tar.bz2 解包)
# P0 实测（2026-07-04）：GitHub 主源两个模型均 200 可下，VAD 已下载并 pin sha256；
# hf-mirror 的 VAD repo 路径实测 401（repo 名有误），强制 mirror 模式前需 P0 修正——GitHub 主源已足够。
# P4 实测（2026-07-25）：声纹模型仅 GitHub release 有 ONNX（ModelScope 官方仓库只有 PyTorch 权重、
# hf-mirror 的 campplus repo 路径 404）；GitHub 在本机实测约 25KB/s，28MB 需十余分钟——**必须靠
# `curl -C -` 续传多次重跑**，这正是本脚本幂等的价值。拉不到不阻塞：网关声纹面自动决议 disabled。
MODELS=(
  "silero-vad|hmi/public/models|silero_vad.onnx|https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx|https://hf-mirror.com/csukuangfj/sherpa-onnx-vad-models/resolve/main/silero_vad.onnx|9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6|0"
  "kws-zipformer|hmi/public/models|sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2|https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2|https://hf-mirror.com/csukuangfj/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/resolve/main/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2||1"
  "voiceprint-campplus|models/voiceprint|campplus_zh-cn_16k-common.onnx|https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx|https://hf-mirror.com/csukuangfj/speaker-embedding-models/resolve/main/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx|f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11|0"
)

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}';
  elif command -v shasum   >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}';
  else echo ""; fi
}

# 单源下载（curl -C - 续传）；成功返回 0
try_download() {
  local url="$1" out="$2"
  echo "  [try] $url"
  curl -fL -C - --retry 3 --connect-timeout 20 --progress-bar -o "$out" "$url"
}

for row in "${MODELS[@]}"; do
  IFS='|' read -r name destdir file gh mirror sha archive <<<"$row"
  [[ -n "$only" && "$only" != "$name" ]] && continue
  dst="$root/$destdir"
  mkdir -p "$dst"
  out="$dst/$file"
  echo "[fetch] $name -> $destdir/$file"

  # 已存在且（若已 pin）校验通过 → 幂等跳过
  if [[ -f "$out" && -n "$sha" ]]; then
    if [[ "$(sha256_of "$out")" == "$sha" ]]; then echo "  已存在且 sha256 匹配，跳过。"; continue; fi
  fi

  # 双源：按 pref 定主备，主失败自动回退
  if [[ "$pref" == "mirror" ]]; then primary="$mirror"; secondary="$gh"; else primary="$gh"; secondary="$mirror"; fi
  if ! try_download "$primary" "$out"; then
    echo "  主源失败，回退备源…"
    if ! try_download "$secondary" "$out"; then
      # 拉不到不是致命错：消费方（HMI 端侧语音 / 网关声纹面）都有「模型缺失即诚实禁用」的档，
      # 在这里 exit 1 会让「顺手补一个模型」变成「整个脚本白跑」。重跑即续传。
      echo "  ✗ 两源均失败：$name（可重跑本脚本续传；缺失时消费方自动降级）" >&2
      continue
    fi
  fi

  # 校验 / 打印实测 sha256
  got="$(sha256_of "$out")"
  if [[ -n "$sha" && "$got" != "$sha" ]]; then
    echo "  ✗ sha256 不匹配：期望 $sha，实测 $got" >&2; exit 1
  fi
  echo "  实测 sha256=$got  （EXPECTED_SHA256 未 pin 时请回填本脚本 + 设计卡 §9）"

  # 归档解包
  if [[ "$archive" == "1" ]]; then
    echo "  解包 $file …"
    tar -xjf "$out" -C "$dst"
  fi
done

echo "[fetch-voice-models] done（模型已 gitignore，切勿提交）"
