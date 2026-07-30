#!/usr/bin/env bash
# 拉取端侧 NLU 的底座 encoder（M5 P3）：iic/nlp_structbert_backbone_tiny_std
# （4 层 / hidden 256 / 8.9M 参数 / 35MB）→ models/nlu/base/
#
# **为什么源是 ModelScope 而不是 HuggingFace**：本机实测 HF 的 LFS blob 无论直连还是
# hf-mirror.com 都只有 28-47KB/s（96MB 要 40 分钟，且 huggingface_hub 会卡在 0 字节），
# ModelScope 实测 3MB/s——35MB 十几秒。同 fetch-voice-models 的双源思路，只是这里国内源更快，
# 所以主源就是它。curl -C - 断点续传，重跑幂等。
#
# 模型二进制 gitignore、切勿提交（体积 + 许可卫生）；沿 certs/ 与 models/voiceprint 先例。
# 拉不到不是致命错：端侧 NLU 决议 disabled，整链回落 1727 行规则（与声纹 disabled 同款姿态）。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DST="$ROOT/models/nlu/base"
BASE="${EDGE_NLU_BASE_URL:-https://www.modelscope.cn/models/iic/nlp_structbert_backbone_tiny_std/resolve/master}"
mkdir -p "$DST"

for f in config.json vocab.txt pytorch_model.bin; do
  out="$DST/$f"
  if [ -s "$out" ] && [ "$f" != "pytorch_model.bin" ]; then
    echo "  已存在，跳过 $f"; continue
  fi
  echo "[fetch] $f -> models/nlu/base/"
  if ! curl -fL -C - --retry 3 --connect-timeout 20 --progress-bar -o "$out" "$BASE/$f"; then
    echo "  ⚠ 下载失败：$f（重跑本脚本续传；缺失时端侧 NLU 自动 disabled）" >&2
  fi
done

python - "$DST" <<'PY'
import json, sys, pathlib
d = pathlib.Path(sys.argv[1])
cfg = d / "config.json"
if not cfg.is_file():
    sys.exit("✗ config.json 缺失")
c = json.loads(cfg.read_text(encoding="utf-8"))
w = d / "pytorch_model.bin"
print(f"[ok] 层数 {c.get('num_hidden_layers')} / hidden {c.get('hidden_size')} / "
      f"vocab {c.get('vocab_size')} / 权重 {w.stat().st_size/1e6:.1f}MB" if w.is_file()
      else "⚠ 权重缺失，重跑续传")
PY
echo "[fetch-edge-nlu-base] done（模型已 gitignore，切勿提交）"
