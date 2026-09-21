#!/usr/bin/env bash
# Mobile APK 工作流的模拟器离线冒烟（G-05）。逻辑与 2026-09-19 写进 workflow `script:` 的那段逐字相同，
# 搬到文件里的唯一原因：reactivecircus/android-emulator-runner 把多行 `script:` **逐行**当独立命令跑，
# `if … fi` 被拆开 ⇒ `/usr/bin/sh: Syntax error: end of file unexpected (expecting "fi")`
#（G-05 第二次 dispatch run #35563352811，2026-09-21：装包已 Success，冒烟一行没跑）。
# 三个读数由本脚本自己给：① ABI（上一步 apk-abis.txt / abi-verdict.txt）② 安装 ③ 冒烟后 logcat 里的 .so 加载失败。
set -u
adb reverse tcp:8081 tcp:8081
adb install -r apk/*.apk 2>&1 | tee install.log
if grep -q 'INSTALL_FAILED_NO_MATCHING_ABIS' install.log; then
  echo "::error::APK 没有模拟器 arch 的 .so 且镜像没有 ARM 转译（见 apk-abis.txt）——给插件加 x86_64 ABI，或换带转译的镜像"
  exit 1
fi
grep -q '^Success' install.log || { echo "::error::adb install 失败"; exit 1; }
adb logcat -c
maestro hierarchy > hierarchy.txt || true
maestro test mobile/e2e/ --include-tags offline && rc=0 || rc=$?
adb logcat -d > logcat.txt || true
if grep -qE 'UnsatisfiedLinkError|dlopen failed' logcat.txt; then
  echo "::error::原生 .so 加载失败（见 logcat.txt）：ARM 插件在 x86_64 镜像上没有跑起来"
  exit 1
fi
echo "ndk_translation lines: $(grep -c ndk_translation logcat.txt || true)"
exit "${rc:-1}"
