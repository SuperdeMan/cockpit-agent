#!/usr/bin/env bash
# M4 端侧语音资产取件（Android 陪伴端）：KWS 的 sherpa-onnx 原生件 + 唤醒词模型 + silero VAD。
# 等价于 scripts/fetch_mobile_voice_assets.ps1（本机构建走 ps1，CI 走本份）。
# 理由与落点逐条见 ps1 的头注，这里不复述；只强调两条最容易搞错的：
#   · **必须 static-link-onnxruntime 版**（普通版 AAR 自带 libonnxruntime.so，会和
#     onnxruntime-react-native 撞同名 .so）；
#   · **不放 .aar 本体**，拆成 classes.jar + jniLibs/<abi>/*.so
#     （AGP 禁止 library 模块直接依赖本地 .aar：`Direct local .aar file dependencies
#     are not supported when building an AAR`）。
#
# 依赖：curl、unzip、python3（解 zip 用 python 的 zipfile，避免 unzip 版本差异）。
# 前置：hmi/public/models/ 下的 KWS 模型与 silero_vad.onnx —— 先跑
#       `bash scripts/fetch-voice-models.sh`（两边共用同一份下载）。
set -euo pipefail
cd "$(dirname "$0")/.."
root="$(pwd)"

AAR_VERSION="1.13.6"
AAR_NAME="sherpa-onnx-static-link-onnxruntime-${AAR_VERSION}.aar"
AAR_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/v${AAR_VERSION}/${AAR_NAME}"
AAR_SHA="01e87037afca2ed49085062aace5c012e60321e8e23e3a72b6d9ac02c843f66c"  # 2026-08-28 实测并 pin
ABIS=(arm64-v8a armeabi-v7a)
SO_NAME="libsherpa-onnx-jni.so"
MODEL_TAG="epoch-12-avg-2-chunk-16-left-64"

kws_root="$root/mobile/modules/kws/android"
kws_libs="$kws_root/libs"
kws_jni="$kws_root/src/main/jniLibs"
kws_assets="$kws_root/src/main/assets/kws"
vad_assets="$root/mobile/assets/models"
cache="$root/.cache/sherpa"
hmi_models="$root/hmi/public/models"
kws_src="$hmi_models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"

mkdir -p "$kws_libs" "$kws_jni" "$kws_assets" "$vad_assets" "$cache"

# ── 1. sherpa 原生件 ──────────────────────────────────────────────────────────
need_extract=0
[ -s "$kws_libs/sherpa-onnx-classes.jar" ] || need_extract=1
for abi in "${ABIS[@]}"; do
  [ -s "$kws_jni/$abi/$SO_NAME" ] || need_extract=1
done

if [ "$need_extract" = "0" ]; then
  echo "[kws] 原生件已在（classes.jar + ${ABIS[*]} 的 $SO_NAME）"
else
  aar="$cache/$AAR_NAME"
  if [ ! -s "$aar" ]; then
    echo "[kws] 下载 $AAR_NAME ..."
    curl -L --fail -C - -o "$aar" "$AAR_URL"
  else
    echo "[kws] 复用缓存 AAR: $aar"
  fi
  sha="$(python3 -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$aar")"
  if [ -n "$AAR_SHA" ] && [ "$sha" != "$AAR_SHA" ]; then
    echo "[kws] AAR sha256 不符：实测 $sha 期望 $AAR_SHA" >&2
    exit 1
  fi
  echo "[kws] AAR sha256 = $sha"
  python3 - "$aar" "$kws_libs" "$kws_jni" "$SO_NAME" "${ABIS[@]}" <<'PY'
import sys, os, zipfile
aar, libs, jni, so = sys.argv[1:5]
abis = sys.argv[5:]
with zipfile.ZipFile(aar) as z:
    with z.open('classes.jar') as f, open(os.path.join(libs, 'sherpa-onnx-classes.jar'), 'wb') as o:
        o.write(f.read())
    print('[kws] classes.jar -> libs/')
    for abi in abis:
        name = f'jni/{abi}/{so}'
        d = os.path.join(jni, abi)
        os.makedirs(d, exist_ok=True)
        with z.open(name) as f, open(os.path.join(d, so), 'wb') as o:
            o.write(f.read())
        print(f'[kws] {name} -> jniLibs/')
PY
fi

# ── 2. KWS 模型 ───────────────────────────────────────────────────────────────
if [ ! -d "$kws_src" ]; then
  echo "找不到 $kws_src —— 先跑 bash scripts/fetch-voice-models.sh" >&2
  exit 1
fi
for f in "encoder-$MODEL_TAG.onnx" "decoder-$MODEL_TAG.onnx" "joiner-$MODEL_TAG.onnx" tokens.txt keywords.txt; do
  [ -f "$kws_src/$f" ] || { echo "KWS 源文件缺失: $kws_src/$f" >&2; exit 1; }
  if [ ! -f "$kws_assets/$f" ] || [ "$(stat -c%s "$kws_assets/$f")" != "$(stat -c%s "$kws_src/$f")" ]; then
    cp -f "$kws_src/$f" "$kws_assets/$f"
    echo "[kws] 复制 $f"
  fi
done

# ── 2b. KWS 自带测试音频（直灌探针用；理由见 ps1 同名小节）────────────────────
kws_wavs="$vad_assets/kws_test"
mkdir -p "$kws_wavs"
if [ -d "$kws_src/test_wavs" ]; then
  for w in 0.wav 1.wav 2.wav 3.wav 4.wav 5.wav 6.wav test_keywords.txt; do
    src="$kws_src/test_wavs/$w"
    [ -f "$src" ] || continue
    if [ ! -f "$kws_wavs/$w" ] || [ "$(stat -c%s "$kws_wavs/$w")" != "$(stat -c%s "$src")" ]; then
      cp -f "$src" "$kws_wavs/$w"
      echo "[kws] 测试音频 $w"
    fi
  done
fi

# ── 3. silero VAD ─────────────────────────────────────────────────────────────
[ -f "$hmi_models/silero_vad.onnx" ] || { echo "找不到 $hmi_models/silero_vad.onnx —— 先跑 bash scripts/fetch-voice-models.sh" >&2; exit 1; }
if [ ! -f "$vad_assets/silero_vad.onnx" ] || \
   [ "$(stat -c%s "$vad_assets/silero_vad.onnx")" != "$(stat -c%s "$hmi_models/silero_vad.onnx")" ]; then
  cp -f "$hmi_models/silero_vad.onnx" "$vad_assets/silero_vad.onnx"
  echo "[vad] 复制 silero_vad.onnx"
else
  echo "[vad] silero_vad.onnx 已在"
fi

echo
echo "[done] M4 语音资产就位。"
