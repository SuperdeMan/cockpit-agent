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

## 状态：**4/4 全部跑通**（2026-08-28 真机）

`4/4 Flows Passed in 7m 43s`——01 天气 2m10s / 02 危险动作 2m43s / 03 断网补达 2m33s /
04 离线冒烟 16s。三条 online 先各自单跑通过，再整目录复跑一遍。

### UX v2.1 B1 新增三条：**06 / 08 / 09 全部跑通**（2026-08-30 真机，各单跑）

| 流 | tag | 验的是 | 读数 |
|---|---|---|---|
| `06-confirm-dock` | online | 危险动作 → **承诺面**钉住确认（倒计时可见）→ 取消 → Dock 消失 | 退出码 0，墙钟 **193.7s** |
| `08-keyboard-no-hide` | online | 键盘弹出时发送键仍在树里、可点（**刻意不 hideKeyboard**） | 退出码 0，墙钟 **126.0s** |
| `09-state-gallery` | offline | 状态画廊：三个新光球态 + 承诺面 + 离线队列 + 降级行渲得出来 | 退出码 0，墙钟 **326.4s / 323.9s**（两趟） |

三条读数的口径：**墙钟含 JVM 启动、driver 连接与 dev bundle 首载**（dev build 现编，
release 会短很多），不是 Maestro 自己报的 flow 时间。09 那 5 分半**几乎全在 `repeat 6 × scroll`**
——两趟差 0.8%，是稳定的，不是抖动。

### UX v2.1 B2 收口复跑（2026-08-31 真机，HEAD 含 T12–T14）

| 流 | tag | rc | 墙钟 | 备注 |
|---|---|---|---|---|
| `09-state-gallery` | offline | **0** | **315s** | 与 B1 的 326.4/323.9s 同量级 |
| `08-keyboard-no-hide` | online | **0** | **133s** | 闸 G3 前半；B1 是 126.0s |
| `06-confirm-dock` | online | **0** | **207s** | `dock-confirm` / `dock-countdown` / `presence-capsule` 三条断言全过 ⇒ **语音层加进对话屏后没有遮住 Dock**（B1 是 193.7s） |
| `05-voice-sheet-ptt` | manual | **1** | 101s | 红在 `assertVisible: voice-sheet`；**不是功能坏**，是没人说话时的竞态，见该文件头注与 B2 计划 §6.4 |

⚠ **跑之前先关 dev-client 的 Tools button**（见下节），否则 `scrollUntilVisible` 会点开 Expo dev 菜单。

⚠ 06 与 02 验的不是同一件事：**02 = 气泡内确认**（v1 路径，实验室开关 `uxV2Dock` 关掉时仍有效），
**06 = 承诺面**。两条都要留着——回滚路径没有测试守着就只是一句话。

⚠ 「到期留痕」（300s）**刻意不在 06 里等**：那条由 `mobile/test/sessionStore.test.ts` 用假时钟守。
共享 TTL `pendingOps.mjs::PENDING_TTL_MS` **不许为了缩短 e2e 去改**——hmi 也在读它。

CI：`.github/workflows/mobile-apk.yml` 跑的是 `maestro test mobile/e2e/ --include-tags offline`，
所以 09 **不改 workflow 就自动进** 那个 job（04 也在）。⚠ 但那个 job 挂在 `workflow_dispatch`
的 `run_e2e` 开关下，**不是每次 push 都跑**——别把「进了 CI」读成「每次都跑」。

**跑法（必须带 `--no-reinstall-driver`）**：

```bash
maestro test --no-reinstall-driver mobile/e2e/                      # 全部四条
maestro test --no-reinstall-driver --include-tags online mobile/e2e/
```

⚠ **第一次在一台新设备上跑**：Maestro 要装自己的 driver APK，MIUI/HyperOS 会弹
`AdbInstallActivity`（**只给 5 秒、默认选中「拒绝」**）。有效做法是**循环触发让它反复弹**、
人看到就点「继续安装」，而不是去掐那 5 秒。装上之后**全程带 `--no-reinstall-driver`**——
Maestro 每个 session 都会重装 driver，不带这个开关就每跑一条都要人点一次。

**实跑当场抓到的三个问题**（都已修在 flow 里，留作后来人的判据）：

1. **`inputText` 之后必须 `hideKeyboard`**：MIUI 的输入法是独立窗口且顶到最上层，
   Maestro 此刻抓到的 hierarchy 里**只有键盘**，App 元素一个都不在树里 ⇒ 紧接着的 `tapOn`
   报「Element not found」，而元素其实好好地在屏上。
2. **`setAirplaneMode` 的值是 `enabled`/`disabled`（小写）**：判据在
   `YamlSetAirplaneModeDeserializer`，**不是** `Commands.kt::AirplaneValue` 那个内部枚举
   （`Enable`/`Disable`）。⇒ 核 YAML 关键字要看反序列化器，不是看内部枚举。
3. 见上面的 `--no-reinstall-driver`。

### B3 重建后（2026-09-01 起，APK 含三件新原生 + 平台 AEC）

B3 那一趟 `-Clean` 重建换掉了设备上的包。下面四条是**跑 flow 之前**要核的，
每条都对应一次实测过的失败形态，不是仪式。

**① 包锚：语音类读数一律带 `lastUpdateTime`**

```bash
adb shell dumpsys package com.xiaozhou.companion | grep lastUpdateTime
```

AEC 补丁 `96a6830` 的提交时刻是 **2026-08-29 17:27:50**——只有 `lastUpdateTime` 晚于它的
APK 才含平台 AEC。B2 整批用的包是 17:22:24 的，**差 5 分钟就没有**，那一批的唤醒率／回声／
端点读数因此全部作废重取。⇒ **没记包锚的语音读数视为无效**。
（B3 §6.1–§6.3 的锚是 `2026-09-01 19:21:55`；B3′ spike 验完装回主线包后锚变成
`2026-09-02 16:45:36`——APK 内容同源，变的只是安装时刻。）

**② Maestro driver 在不在**（重装 APK 不会删 driver，但要核过才知道）

```bash
adb shell pm list packages | grep maestro        # dev.mobile.maestro + .test 两条
D:/Android/tools/maestro-dist/maestro/bin/maestro.bat test <flow> --no-reinstall-driver
```

driver 不在就要重装，MIUI 的 ADB 安装确认弹窗要人点。

**③ 两个取证屏的深链**（B3 新增，设置页「调试」段也有入口）

```bash
adb shell am start -a android.intent.action.VIEW -d "xiaozhou://native-spike"   # 折叠姿态 + 触感四种 + 原生在场
adb shell am start -a android.intent.action.VIEW -d "xiaozhou://blur-spike"     # 材质四块对照（③ 是真模糊路径）
```

⚠ **深链在 bundle 加载完之前发会被吞掉**：USB 掉线、手折导致进程重启之后，应用会落回
`DevLauncherActivity`，要先

```bash
adb shell am start -a android.intent.action.VIEW -d "xiaozhou://expo-development-client/?url=http%3A%2F%2Flocalhost%3A8081"
```

重连 Metro，**等应用真起来**（logcat 出现 `Running "main"`）再发上面那条——立刻发的那次
没有反应，别当成路由缺陷。`am start -n .../.MainActivity` 只能把它拉到 dev launcher，回不到 MainActivity。

**④ 设备系统开关会静默吞掉被测行为**（已发生三次，同一族）

```bash
adb shell settings get system haptic_feedback_enabled   # 0 ⇒ 振动全部 IGNORED_FOR_SETTINGS
adb shell dumpsys audio | grep -A2 "STREAM_MUSIC"       # 扬声器 0 ⇒ 听不到 = 以为没播报
```

两条都**不报错、不崩、日志里看着像成功**（`Starting vibrate` 照打），只有读到最后那行
`ended with status IGNORED_FOR_SETTINGS` 才看得出。抬音量在本机**只有** `input keyevent 24`
+ `dumpsys audio` 回读一条路（`adb shell media volume` 不存在，`cmd media_session` 静默失败），
且必须**解锁 + 应用前台**时按。

**⑤ 截图取物理 display id**（折叠屏，B3′ 又踩一次）

```bash
adb shell dumpsys display | grep -oE "displayId=[0-9]+|uniqueId='local:[0-9]+'"
adb shell screencap -p -d <物理id> /sdcard/x.png && adb pull /sdcard/x.png
```

`-d 0` 会报 `Display Id '0' is not valid`（那是逻辑 id）；而
`dumpsys SurfaceFlinger --display-id | head -1` 取到的是**内屏**（本机
`4630946481727302019`，合盖时 state OFF ⇒ 抓出 2224×2488 全黑图）。活跃外屏是
`4630947090644569220`（1080×2520）。**全黑先查 `dumpsys power` 的 `mWakefulness`，
息屏+锁屏与抓错屏是两件事。**

## CLI 在哪（本机）／换机器怎么装

**本机不用装**：Maestro **2.9.0** 的 dist 一直在 `D:/Android/tools/maestro-dist/maestro/bin/maestro.bat`
（zip 原件在 `D:/Android/tools/maestro.zip`，314,827,824 B = GitHub `cli-2.9.0` 原大小），
设备上的 driver（`dev.mobile.maestro` + `dev.mobile.maestro.test`）也还在 ⇒ 直接带
`--no-reinstall-driver` 用绝对路径调即可。

> ⚠ **「不在 PATH」≠「不在本机」**：B2 计划 §6.3 曾据 `which maestro` 与 `~/.maestro/bin` 为空
> 判定「CLI 已不在本机」，并把「装回来」列成需要授权的阻塞项。2026-08-31 复核：`~/.maestro/`
> 里只有 `deps/`、`tests/`、`analytics.json` 这些**运行期状态**，从来就不放 CLI 本体；
> dist 在别处。判据应该是 `find` 整盘，不是 `which`。（顺带实测：直连 GitHub 拉 300MB 的
> `maestro.zip` 只有 **111 KB/s**（10s 拿到 1,112,220 B），`ghfast.top` 更慢（8s / 294,067 B），
> `gh-proxy.com` / `ghproxy.net` 直接失败 ⇒ 真要重下得先找镜像，别硬拉。）

换机器时：下 `maestro.zip`（GitHub releases），**下完先对 release 里的 `checksums_sha256.txt`**，
解压后把 `bin` 加进 PATH。需要 JDK（本机 JDK 17 已装，见实施计划 §1.1）。
⚠ 下载别把 `--retry` 和 `-C -` 一起用（会把重复字节插进文件中间，坑账 §9.37）。

## ⚠ dev-client 的悬浮 Tools 按钮会截走手势

dev build 右上角那颗悬浮齿轮（expo-dev-client 的 **Tools button**）是**盖在 App 之上**的独立视图。
Maestro 的 `scrollUntilVisible` 用屏幕中线附近的 swipe，实测会**抓到它并弹出 Expo dev 菜单**，
表现成「flow 莫名其妙失败 / 点到了别的东西」。取证前先关掉：`adb shell input keyevent 82`
打开 dev 菜单 → 关 **Tools button** → 关闭菜单。判据不要靠眼睛：关前后对同一矩形做
`png_probe region`（本机读数 880,180–1030,330：关前 `avg(46.6,48.0,52.6)` / 亮像素 **8.85%**，
关后 `avg(6.0,8.0,14.0)` / **0.00%**）。

## 已知风险：常驻动画 vs UiAutomator

**对话主屏永远有一个循环动画**（Composer 的 Aurora 光球；欢迎态还有 88dp 大球与背景 blob）。
实测后果：`adb shell uiautomator dump` 在主屏**稳定拿不到树**（连试 4 次都只有 1 个 node），
而设置页/地图页正常——因为 `uiautomator dump` 要等窗口 idle，而这里永远不 idle。

Maestro 的 Android driver 走 `AccessibilityNodeInfo`（不等 idle），**实测不受影响**
（2026-08-28 证实：同一块主屏，`uiautomator dump` 只给 1 个节点，Maestro 抓到 199KB 完整树，
`composer-input`/`composer-send` 都能按 id 匹配到——RN 的 testID 在 Android 上落成了可匹配的 id）。
换设备/换 RN 版本后仍建议先验一句：

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
