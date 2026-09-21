#!/usr/bin/env bash
# Mobile APK 工作流的模拟器离线冒烟（G-05）。逻辑与 2026-09-19 写进 workflow `script:` 的那段同源，
# 搬到文件里的原因：reactivecircus/android-emulator-runner 把多行 `script:` **逐行**当独立命令跑，
# `if … fi` 被拆开 ⇒ `/usr/bin/sh: Syntax error: end of file unexpected (expecting "fi")`
#（G-05 第二次 dispatch run #35563352811，2026-09-21：装包已 Success，冒烟一行没跑）。
# 三个读数由本脚本自己给：① ABI（上一步 apk-abis.txt / abi-verdict.txt）② 安装 ③ .so 加载。
#
# 第三次 dispatch（run #35611616585）的账：hosted runner 的 x86_64 模拟器冷启 771s、软件渲染，Maestro 的 Android driver
# 15s 内没起来（`AndroidDriverTimeoutException`）⇒ 两条 flow 立刻红、App 根本没被拉起，`ndk_translation lines: 0` 是空读数。
# 所以：③ 的读数不再系在 Maestro 身上——装完先自己 `am start` 一次、等 25s、看 logcat 有没有 .so 加载失败（onnxruntime /
# audio-api / worklets 在进程起来时就 dlopen；sherpa KWS 要开免唤醒才加载，这里覆盖不到，如实写）；Maestro 的 driver 超时放宽到 3 分钟。
set -u
export MAESTRO_DRIVER_STARTUP_TIMEOUT=180000
PKG=com.xiaozhou.companion
adb reverse tcp:8081 tcp:8081
adb install -r apk/*.apk 2>&1 | tee install.log
if grep -q 'INSTALL_FAILED_NO_MATCHING_ABIS' install.log; then
  echo "::error::APK 没有模拟器 arch 的 .so 且镜像没有 ARM 转译（见 apk-abis.txt）——给插件加 x86_64 ABI，或换带转译的镜像"
  exit 1
fi
grep -q '^Success' install.log || { echo "::error::adb install 失败"; exit 1; }

# ③ .so 加载：不靠 Maestro，自己把 App 拉起来读 logcat
adb logcat -c
adb shell am start -W -n "$PKG/.MainActivity" 2>&1 | tee launch.log
sleep 25
echo "app pid after 25s: $(adb shell pidof "$PKG" || echo none)"
{ echo "===== launch (am start, 25s) ====="; adb logcat -d; } > logcat.txt || true
if grep -qE 'UnsatisfiedLinkError|dlopen failed' logcat.txt; then
  echo "::error::原生 .so 加载失败（见 logcat.txt）：插件在 x86_64 镜像上没有跑起来"
  grep -E 'UnsatisfiedLinkError|dlopen failed' logcat.txt | head -5
  exit 1
fi
echo "launch: no UnsatisfiedLinkError / dlopen failed; ndk_translation lines: $(grep -c ndk_translation logcat.txt || true); libs loaded by $PKG: $(grep -cE "$PKG.*(Loaded|loaded) lib|nativeloader.*$PKG" logcat.txt || true)"
adb shell am force-stop "$PKG"

# Maestro 离线冒烟（App 起得来、路由通、卡片渲染器不崩）
adb logcat -c
maestro hierarchy > hierarchy.txt || true
maestro test mobile/e2e/ --include-tags offline && rc=0 || rc=$?
{ echo "===== maestro ====="; adb logcat -d; } >> logcat.txt || true
if grep -qE 'UnsatisfiedLinkError|dlopen failed' logcat.txt; then
  echo "::error::原生 .so 加载失败（见 logcat.txt）：插件在 x86_64 镜像上没有跑起来"
  exit 1
fi
echo "maestro rc=${rc:-1}; ndk_translation lines: $(grep -c ndk_translation logcat.txt || true)"
exit "${rc:-1}"
