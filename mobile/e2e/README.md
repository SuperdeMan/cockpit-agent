# mobile/e2e — Maestro flow（M3-5）

```bash
maestro test mobile/e2e/ --include-tags offline   # 零后端依赖，CI 与本地都能跑
maestro test mobile/e2e/ --include-tags online    # 需真栈（target=cloud + 设备在 tailnet）
maestro test mobile/e2e/01-text-weather.yaml      # 单条
```

## 运行前提（缺一条就会以看起来无关的方式失败）

1. **Metro 在跑**：`cd mobile && npx expo start --dev-client`
2. **`adb reverse tcp:8081 tcp:8081`**（真机走 USB 时必须）。⚠ 设备 USB 抖一下这条就没了，
   症状是 dev-client 报 `ConnectException`——**看起来像 Metro 挂了**（坑账 §9.37 同族）。
3. **云栈可达**（`online` 那三条）：`python scripts/dev_stack.py target show` = cloud，
   设备上的 Tailscale 已登录同一 tailnet。
4. 每条 flow 都先跑 `subflows/open-app.yaml` 经 dev-client 深链连 Metro。**这不是仪式**：
   dev build 的 `launchApp` 打开的是 DevLauncherActivity，被测对象根本没在跑，
   而失败信息看起来像「App 里没有这个按钮」。

## 状态：flow 已写，**没有实跑读数**（2026-08-28）

`maestro.zip` 315MB，本网络 ~30–50KB/s，两次下载都断在中途（且 `curl -C -` 配 `--retry`
会把重复字节插进文件中间，坑账 §9.37）。**语法是逐条对着 cli-2.9.0 源码核过的**
（`YamlFluentCommand.kt` 的字段表 / `Commands.kt::AirplaneValue` / `WorkspaceConfig.kt`），
但**没跑过就是没跑过**，不许当成通过。

## 装 CLI

下 `maestro.zip`（GitHub releases），**下完先对 release 里的 `checksums_sha256.txt`**，
解压后把 `bin` 加进 PATH。需要 JDK（本机 JDK 17 已装，见实施计划 §1.1）。

## 已知风险：常驻动画 vs UiAutomator

**对话主屏永远有一个循环动画**（Composer 的 Aurora 光球；欢迎态还有 88dp 大球与背景 blob）。
实测后果：`adb shell uiautomator dump` 在主屏**稳定拿不到树**（连试 4 次都只有 1 个 node），
而设置页/地图页正常——因为 `uiautomator dump` 要等窗口 idle，而这里永远不 idle。

Maestro 的 Android driver 走 `AccessibilityNodeInfo`（不等 idle），**大概率不受影响**——
但这是**推断，没实测**。第一次跑之前先验一句：

```bash
maestro hierarchy     # 主屏能不能拿到完整树、testID 有没有落成可匹配的 id
```

**如果 `id:` 匹配不到**，把 flow 里的 `tapOn: {id: ...}` 换成文本匹配
（`tapOn: "发送"` / placeholder 文本），并把这条结论写回本文件——
届时 testID 那套「驱动用 id、断言用文本」的取舍就得重新权衡。

## 判据取舍（写 flow 时的三条）

- **驱动用 testID，断言用文本**：文案会变（Aurora 那批重画过发送键），而断言要验的
  正是语义内容。
- **flow ③ 的「补达」只能断言挂起态消失**（`msg-pending`）：断言回答文本会被**用户自己
  那条气泡**满足（同一句话同时在屏上），立刻绿而消息可能还卡在队列里。
- **flow ③ 断网后必须等够 ~35 秒再发**：RN 的 WebSocket 在飞行模式下 `onclose` 不来，
  连接靠 App 探活判死（实测 **25.7s**）。早发的那条会落进已知残留窗口、根本不入队 ⇒
  flow 稳定红，而红的原因是那个已知窗口、不是回归。

## 为什么有第四条离线冒烟

前三条**全要真栈**，而 CI runner 没有 Tailscale（给 CI 配 auth key 是红线动作）。
在 CI 里挂那三条只会稳定红、然后被人加 `continue-on-error` 忽略掉——
**一条永远红的检查等于没有检查**。所以 CI 跑 `04-offline-smoke`（画廊渲染，零后端依赖），
它证明「App 起得来、路由通、卡片渲染器不崩」，**证明不了整链，也不许拿它冒充整链**。
