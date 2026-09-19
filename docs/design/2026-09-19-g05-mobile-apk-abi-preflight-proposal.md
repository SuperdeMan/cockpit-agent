# G-05 待批改法：`.github/workflows/mobile-apk.yml`（CI/CD 配置，未应用）

只动 `e2e-smoke` job，`debug-apk` job 不动。三处：

## 1. 模拟器步骤之前加 ABI 预检

```yaml
      # G-05（2026-09-19，GPT-6 评审 §七-3）：原生插件（sherpa KWS / onnxruntime）只打 arm64-v8a + armeabi-v7a，
      # 下面的模拟器是 x86_64。能不能装、装上后 .so 能不能经镜像的 ARM 转译加载，**由本 job 自己给读数**，不预设结论：
      #  ① 这里列出 APK 的 lib/ ABI 与模拟器 arch；② 安装失败时把 INSTALL_FAILED_NO_MATCHING_ABIS 翻成人话；
      #  ③ 冒烟后从 logcat 找 UnsatisfiedLinkError / dlopen failed，连同 ABI 清单一起上传。
      - name: APK ABI preflight (vs emulator arch)
        run: |
          apk=$(ls apk/*.apk | head -1)
          echo "apk=$apk"
          unzip -l "$apk" | awk '{print $4}' | grep '^lib/' | cut -d/ -f2 | sort -u | tee apk-abis.txt
          if grep -qx 'x86_64' apk-abis.txt; then echo "ABI_MATCH=native" | tee abi-verdict.txt
          elif grep -qx 'arm64-v8a' apk-abis.txt; then echo "ABI_MATCH=translation-required (arm64-v8a only; relies on the image's ARM translation)" | tee abi-verdict.txt
          else echo "ABI_MATCH=none" | tee abi-verdict.txt; fi
```

## 2. 模拟器 script 改成有判据的安装与收尾

```yaml
          script: |
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
            exit $rc
```

## 3. 工件多传四个文件

```yaml
          path: |
            ~/.maestro/tests/**
            mobile/metro.log
            hierarchy.txt
            apk-abis.txt
            abi-verdict.txt
            install.log
            logcat.txt
```

改完要：① 重批 CI/CD 发布摘要（`ci_cd` 类 digest 会变）；② 由你手动 dispatch 一次 `run_e2e=true` 才有读数——本机跑不了 Linux runner + 模拟器。
