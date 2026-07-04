#!/usr/bin/env bash
# 从源码构建 sherpa-onnx KWS（唤醒词）WASM 运行时——R4.3 用（无预构建可得，见设计卡 §9）。
# 产物：sherpa-onnx-wasm-kws-main.{js,wasm,data} + sherpa-onnx-kws.js（拷进 hmi/public/voice-probe/kws/）。
# WASM 二进制 ~31MB 不入库；此脚本即「生成脚本」，沿 certs/ 的先例（gitignore + 脚本重现）。
#
# 前置（一次性）：
#   emscripten SDK 6.0.2（实测可用；官方 build 脚本注 4.0.23，6.0.2 亦通）：
#     git clone https://github.com/emscripten-core/emsdk && cd emsdk
#     ./emsdk install 6.0.2 && ./emsdk activate 6.0.2 && source ./emsdk_env.sh
#   cmake ≥3.15 + ninja（Windows 无 make，故本脚本用 -G Ninja；Linux/mac 可改回 make）。
#
# 用法：EMSCRIPTEN=/path/to/emsdk/upstream/emscripten bash scripts/build-kws-wasm.sh
set -e
: "${EMSCRIPTEN:?请先 source emsdk_env.sh 或显式设 EMSCRIPTEN=<emsdk>/upstream/emscripten}"
SRC="${SHERPA_SRC:-$HOME/sherpa-onnx}"
OUT="${1:-hmi/public/voice-probe/kws}"           # 产物落点（相对本仓根，默认探针目录）
MODEL_TAG="sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"

# 1) 源码
[ -d "$SRC" ] || git clone --depth 1 https://github.com/k2-fsa/sherpa-onnx.git "$SRC"

# 2) KWS 模型入 assets（build 用 --preload-file 把 assets 烤进 .data；只留 epoch-12-avg-2）
cd "$SRC/wasm/kws/assets"
if [ ! -f encoder-epoch-12-avg-2-chunk-16-left-64.onnx ]; then
  curl -fL -o m.tar.bz2 "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/$MODEL_TAG.tar.bz2"
  tar xf m.tar.bz2 && mv $MODEL_TAG/encoder-epoch-12-avg-2-*.onnx $MODEL_TAG/decoder-epoch-12-avg-2-*.onnx \
    $MODEL_TAG/joiner-epoch-12-avg-2-*.onnx $MODEL_TAG/tokens.txt ./
  rm -rf "$MODEL_TAG" m.tar.bz2 *epoch-99* *int8* 2>/dev/null || true
fi

# 3) 构建（emcmake + Ninja）
cd "$SRC"
export SHERPA_ONNX_IS_USING_BUILD_WASM_SH=ON
rm -rf build-wasm-simd-kws && mkdir build-wasm-simd-kws && cd build-wasm-simd-kws
emcmake cmake -G Ninja -DCMAKE_INSTALL_PREFIX=./install -DCMAKE_BUILD_TYPE=Release \
  -DSHERPA_ONNX_ENABLE_PYTHON=OFF -DSHERPA_ONNX_ENABLE_TESTS=OFF -DSHERPA_ONNX_ENABLE_CHECK=OFF \
  -DBUILD_SHARED_LIBS=OFF -DSHERPA_ONNX_ENABLE_PORTAUDIO=OFF -DSHERPA_ONNX_ENABLE_JNI=OFF \
  -DSHERPA_ONNX_ENABLE_C_API=ON -DSHERPA_ONNX_ENABLE_TTS=OFF -DSHERPA_ONNX_ENABLE_WEBSOCKET=OFF \
  -DSHERPA_ONNX_ENABLE_GPU=OFF -DSHERPA_ONNX_ENABLE_WASM=ON -DSHERPA_ONNX_ENABLE_WASM_KWS=ON \
  -DSHERPA_ONNX_ENABLE_BINARY=OFF -DSHERPA_ONNX_LINK_LIBSTDCPP_STATICALLY=OFF ..
ninja && ninja install

# 4) 拷运行时到本仓（回到调用时的仓根需用绝对路径；此处提示手动 cp）
echo "[build-kws-wasm] 产物在 $SRC/build-wasm-simd-kws/install/bin/wasm/："
ls -lh install/bin/wasm/
echo "[build-kws-wasm] 请拷 sherpa-onnx-kws.js + sherpa-onnx-wasm-kws-main.{js,wasm,data} → <repo>/$OUT/"
echo "  运行时关键词（免训练，pinyin token）：'x iǎo zh ōu x iǎo zh ōu @小舟小舟'（小=x iǎo / 舟=zh ōu，对 assets/tokens.txt）"
