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

⛔⛔ **两条的前提互斥，一趟跑不可能都绿——回归清单必须带前提**（2026-09-05 实证）：
`uxV2Dock=true` 时跑 02 会**稳定红在 `extendedWaitUntil: confirm-cancel` 45s 超时**
（前面 `tapOn: composer-input` / `inputText` / `hideKeyboard` / `tapOn: composer-send` **全部 COMPLETED**，
所以看着很像回归）；关掉开关复跑立刻 rc=0。⇒ **跑 02 与 06 要分两趟、各自先设好 `uxV2Dock`**。
B5 计划 T16 步骤 4 把清单抄成了一句「01/02/03/06/08/09 各 rc=0」而没带前提，据它判红会误判成回归。

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

### B4「形态落地」新增：**07 内屏双栏**（2026-09-02 真机，rc=0 + 反向验证）

Maestro **没有 adb 命令**——`device_state` 由外部先设、跑完还原，两步都要回读：

```powershell
adb shell cmd device_state state 3 ; adb shell cmd device_state print-state       # 期望 OPENED(3)
D:/Android/tools/maestro-dist/maestro/bin/maestro.bat test --no-reinstall-driver mobile/e2e/07-tablet-two-pane.yaml
adb shell cmd device_state state reset ; adb shell cmd device_state print-state   # 回物理姿态
```

判据物是 `stage-pane` 与标题文本「舞台 · 双栏」，不是「屏上看起来有两栏」。反向验证已做：
把 `sizeClass.ts` 的 `TWO_PANE_MIN_WIDTH` 临时改 9999 热载再跑 ⇒ **红在 `stage-pane`**，还原后复跑绿。

**⑥ `device_state` 的强制值与机身物理姿态不一致时，被「激活」的那块屏可能物理是关的 ⇒ `screencap` 全黑**
（B4-6 实测：机身合着强制 `state 3`，两块屏都抓出全黑；机身展开强制 `state 0` 同理）。
`state reset` 回的是**物理**姿态，不是你上一次设的值——`print-state` 回读才知道现在是哪种。
另：`input keyevent 82` 在应用前台时会**打开 RN dev menu**（拿它解锁屏幕的话记得再按一次 BACK 关掉）。

### B5「语音层去底栏」把手带手势：正反两条的取证写法（2026-09-05 真机）

B5-12 把「收起」从底栏那枚 `voice-sheet-collapse` 键改成**顶缘把手带**（testID 沿用），
Pan 也从整层挪到把手带上。**这条改动的判据是一对正反手势，缺任何一条都证不完**：

| # | 手势 | 期望 | 本轮实测 |
|---|---|---|---|
| 正 | 从**把手带**向下拖 > 80dp | 收起 | ✅ 层消失（`b5-12-pos.png`） |
| **反** | 从**层内内容区**（大球那一带）向下拖同样距离 | **不收起**，层高纹丝不动 | ✅ 层顶逐像素未动（`b5-12-neg.png`） |
| 正 | 轻点把手带 | 收起 | ✅（`b5-12-tap.png`，另存 tap 前的 `b5-12-t0.png` 证明层原本升着） |
| 正 | 点暗区 | 收起 | ✅（`b5-12-scrim.png`） |

反例是这条改动的**全部意义**：Pan 挂在整层时，层内 ScrollView 滚到顶后再下拖会和整层 Pan 打架
（B4 §6.4 实测）。反例绿才说明「限定在把手带」真的生效了。

```powershell
$adb = "$env:ANDROID_HOME\platform-tools\adb.exe"
# 升层：走深链，**不开麦**（§12.2 红线，B5 §6.2 T11 已验），单人可做、零采集
& $adb shell 'am start -a android.intent.action.VIEW -d "xiaozhou://voice"'
# 手势：**单进程慢速** input swipe（分进程发 motionevent 各带 downTime，RNGH 认不出是一次拖）
& $adb shell "input swipe 540 1474 540 1874 600"    # 把手带 → 收起
& $adb shell "input swipe 540 1739 540 2139 600"    # 层内   → 不收起
```

⚠ **每条手势前后各截一张**：只截「后」那张会把「本来就没升起」读成「收起了」（演员没上场的第一形态）。

**三条本轮撞到的仪器坑**（都会让人把「读数没变」误读成「改动没生效」）：

1. ⛔ **Metro 的 fast-refresh 不一定推到设备**：改完 `VoiceSheet.tsx` 热载后再 dump，
   拿到的 XML 与改动前**逐字节相同**（同为 58239 B）。`am force-stop` + 重发
   `xiaozhou://expo-development-client/?url=http%3A%2F%2F127.0.0.1%3A8081` 重取 bundle 之后才变。
   **判别式：比两份 dump 的字节数/hash**，相同就是没推到，不是改动没生效。
2. ⛔ **force-stop 会把 dev-client 的已连服务器清掉**，重启只落到 `DevLauncherActivity`
   （看起来像坑 74 的 Metro OOM，但 `/status` 仍是 `packager-status:running`）。
   ⇒ 先核 Metro 活着，再用上面那条 dev-client 深链把它接回去。
3. ⛔ **Maestro 主包会自己消失**：2026-09-04 记的是 `dev.mobile.maestro` + `.test` 两个都在，
   2026-09-05 上午复核只剩 `.test`，`hierarchy` / `test` 一律
   `INSTALL_FAILED_USER_RESTRICTED`（`--no-reinstall-driver` 也拦不住 `hierarchy`，它照样先装）。
   ⚠ **但「`pm list packages | grep maestro` 数两条」不是判据**（B5 §6.3 泓舟在场轮纠正）：同日下午
   USB 安装放行后 `pm list packages` 522 条里**搜不到** maestro、`pm path` 两个都空，而
   `maestro hierarchy` 正常工作。**真判据是 `maestro hierarchy --no-reinstall-driver` 的退出码**，
   rc≠0 才找设备主人放行 USB 安装（MIUI 放行约 10 分钟 / 重启后自动关回，放行后立刻跑）。

**`uiautomator` 这条路在本轮是通的**（Maestro 不通时的替代）：设置 → 实验室 → 开
「减少动效（强制）」**（App 内设置，不是系统设置）**，同一屏立刻从「拿不到 idle」变成
56–60KB 完整树。用完记得关回去并回读。B5 §6.2 坑 ⑬ 把它记成了系统设置，是错的。

**02 与 06 的前提互斥——回归清单必须带前提**（B5 §6.3 泓舟在场轮实测）：02 验的是**气泡内**确认
（v1 路径，实验室「承诺面 Focus Dock」= **关**才有效），06 验的是**承诺面**（同一开关 = **开**）。
一趟里两条不可能同时绿：照抄「01/02/03/06/08/09 各 rc=0」时 02 先红（`confirm-cancel` 45s 未出现、
前面 tapOn 全 COMPLETED），关掉开关复跑才 rc=0——**这是配置态互斥，不是回归**。⇒ 分两趟、各自设好
开关、跑完回读开关并还原。B5 六条读数（主线包锚 `2026-09-04 23:41:40`）：01 132.0s / 02 158.1s（Dock 关）/
03 191.4s / 06 165.2s（Dock 开）/ 08 110.5s / 09 250.2s，全部 rc=0。

**`composer-send` 两态（B5-13 发送与打断合一）**：testID 不变，五条 online 流仍在闲时点它。
闲时 ⬆ 极光渐变、`content-desc="发送"`；忙时 ■ 琥珀底 + 琥珀边框、`content-desc="打断"`，点它 ⇒
气泡定格「已打断」（灰字，不改红）、键回 ⬆。单人取证：发一句**英文**长问题（`adb shell input text`
送不了中文，B5 坑 ⑫；中文用 Maestro `inputText`），流式回答期间 `maestro hierarchy` 读
`composer-send` 的 `content-desc`，`input tap` 它，再 dump 一次看「已打断」。阴性：旧 pill
`voice-sheet-interrupt` 必须 **NOT FOUND**（`target_probe` rc=2）。C 身份闲时仍 disabled、忙时可点。

**深链 `xiaozhou://voice` 冒烟（B5-8 Shortcuts 落点；§12.2 只升层不开麦）**：
1. 免唤醒**关**，`dumpsys audio | grep "active? true"` 先立阴性基线 = **0 条**（免唤醒开着时常开麦流会占住
   这条判据、零分辨力，B5 坑 ⑰）；
2. `am start -a android.intent.action.VIEW -d "xiaozhou://voice"` ⇒ 回对话页 + 语音层升起；
3. 再读 `active? true` 仍 **0 条** = 红线成立；收起后再发同一条深链应**再升一次**（「新一次进入」语义）。
   ⚠ 别的路由要**三斜杠**（`xiaozhou:///native-spike`；双斜杠时路由名被当 URI host 吃掉，B5 坑 ⑭），
   `voice` 例外（落点是 `Redirect`，两种都到）。真实入口：桌面长按图标 → 「和小舟说话」（MIUI 取
   `longLabel`）→ 冷启动到对话页 + 层升起 + `active? true` 0 条（B5 §6.2 T11 泓舟在场实测）。

T17 五态小样本的材料截取流（thinking / attention / speaking 三条 Maestro + 取法表）在
`e2e/artifacts/b5-sample/_capture/`（gitignore），**2026-09-05 晚已在主线包上跑通、七张材料截齐**（`s1..s7`，
状态栏已裁、编号已打乱，映射只在 `_capture/mapping.private.txt`）。跑这类「抓瞬态」流的五条经验：

1. ⛔ **每个 `tapOn` 压 `waitToSettleTimeoutMs: 200`**：极光常驻动画永不 settle，缺省一个 tap 等 20–40s
   （实测 tap 输入框 33s、tap 发送 37s），等完 thinking 早过了。
2. ⛔ **收键盘点 IME 自己的「收起 ˅」钮**（本机输入法工具栏右端 ≈ `91%,65%`）：`tapOn: point: "50%,30%"`
   在当前构建**收不掉**（上面 B4 那条写法已失效，`mInputShown` 仍 true）；`pressKey: Back` 仍不能用。
   先收键盘再点发送，材料里才没有键盘。
3. **Maestro 2.9 的 `takeScreenshot` 不落在 cwd**，在 `~/.maestro/tests/<时间戳>/<流名>/takeScreenshot/<name>.png`。
4. 要长播报别直接问「广州历史」——会被追问成三选一卡片，TTS 只有 1.6s；点卡片里的「闲聊口述简史」
   拿长回答（播报「总是」下 `AudioTrackImpl [fine]` 30s+）。
5. 设置页 `scrollUntilVisible` 以「播报」**标签**为目标会停在 chip 行还在屏外的位置（点下去点的是标签，
   什么都没改——回读截图才发现）；以行里最后一个 chip「静音」为目标才整行在屏内。

⚠ **别人的 8081 Metro 会让 dev-client 停在「Refreshing…」蓝条**（bundle 已加载、App 可用，但顶栏被盖住，
截图不能用；force-stop + 重连也不清）。**不停它**——另起 `npx expo start --dev-client --port 8082`
（`CI=1` 关 watch 也无妨），`adb reverse tcp:8082 tcp:8082`，dev-client 深链改指 `127.0.0.1%3A8082`，蓝条即消失。
收尾停掉自己那个、`adb reverse --remove tcp:8082`。

⚠ Git Bash 里 `adb pull /sdcard/x.png` 会被 MSYS 把 `/sdcard` 改写成 `D:/Program Files/Git/sdcard`
（`failed to stat remote object`），前面加 `MSYS_NO_PATHCONV=1` 或改用 PowerShell。

### B4「行车档」真机验收（T13）：三条取证通道 + 两处卡点

**⛔ 卡点①：Maestro 的 driver 在本机装不上，Maestro 类读数全部取不到。**
设备上只剩 `dev.mobile.maestro.test`，主包 `dev.mobile.maestro` 不在，装它一律
`INSTALL_FAILED_USER_RESTRICTED: Install canceled by user`。解锁、亮屏、应用前台都试过，**照样拒**
——所以上面 B2/B3 记的「解锁后就过了」是巧合或另有条件，**与锁屏无关**。唯一站得住的结论是
报错文案里的 "canceled by user" 不是「有人点了取消」。**要跑 06/07/08/09 得先在设备的开发者选项里
放行 USB 安装**（需设备主人授权）。在此之前用下面的 uiautomator 路径替代。

**⛔ 卡点②：`adb shell input text` 送中文直接抛 NPE**，不是「要加转义」——这条路在本机不存在：

```
java.lang.NullPointerException: Attempt to get length of null array
    at com.android.commands.input.InputShellCommand.sendText     # KeyCharacterMap 里没有这些字符
```

后果不小：「说一句会出过程区的复杂任务」是 §11.2 B4 ②③ 四格取证的**共同前提**。试过的替代都不通：
ADBKeyboard 要装 APK（与 Maestro driver 同一堵墙）、`service call clipboard` 的 parcel 格式随版本变、
`am start` 没有可带文本的路由。⇒ **要么人手输，要么另立一条输入通道**（B5 的账）。

**通道一：目标尺寸读实 `tools/target_probe.py`（走 uiautomator XML，不走 Maestro）**

```powershell
# ⚠ 先在设置 → 实验室里开「减少动效（强制）」，否则对话页 uiautomator 只吐到 ComposeView 一层
#    （3.7KB、零 RN 节点）——那一屏永远不 idle。开了之后同一屏是 80KB 完整树。
adb shell uiautomator dump /sdcard/ui.xml ; adb pull /sdcard/ui.xml
adb shell wm density                       # 取 Physical/Override density（本机 480）
python mobile/e2e/tools/target_probe.py ui.xml --density 480 --min 56 `
  composer-orb composer-send dock-accept dock-cancel followup-chip voice-sheet-collapse driving-card-button
```

退出码 0 全过 / 1 有不达标 / 2 找不到某个 id（**演员没上场**）。
**阴性对照是必须的**：行车档关了再跑同一条，`composer-send` 应该 44.0dp FAIL（rc 0→1）——
探针量得出差别才算探针活着。`presence-capsule` 视觉 26dp 靠 hitSlop，在这里必然 FAIL，那是
**读法的限制**不是缺陷（Scanner 才量无障碍树上的可点区域，装它要授权）。

**通道二：行车 `driving` 帧——云栈 debug 注入（零后端改动）**

```powershell
$fqdn = (Select-String -Path .env -Pattern '^TAILNET_FQDN=' | ForEach-Object { $_.Line.Split('=')[1].Trim() })
Invoke-RestMethod -Method Post -Uri "https://$fqdn`:8446/api/debug/vehicle" -ContentType 'application/json' -Body '{"key":"speed_kmh","value":30}'
Invoke-RestMethod -Uri "https://$fqdn`:8446/api/vehicle/state"     # 回读；做完一定还原 speed_kmh=0 + gear=P
```

白名单只有 `speed_kmh / battery / gear / location / cabin_temp`（`orchestrator/edge/server.py`）。
⚠ **只有会出过程区的复杂任务才带这个标**——`driving` 住在 `type:'process'` 帧上，简单轮没有它。

**通道三：四个实验室开关在取证里各自的用途**

| 开关 | 取证里干什么 |
|---|---|
| 减少动效（强制） | ① §11.2 B4 ④ 的静帧读数；② **让 `uiautomator dump` 能 idle**（意外收益，见通道一） |
| 行车档（手动） | 不依赖云栈就能造行车形态；但**验「Edge 会不会标」必须关掉它**，否则分不清是哪一支 |
| 常亮 | §6 触发③ 建议胶囊的三条件之一（身份 C + 横屏 + keep-awake） |
| 免唤醒 | 语音轮的入口；PTT 那条路要关掉它 |

**两条一般纪律（本轮各中一次）**

- **音频类读数每轮回读 `dumpsys audio` 的 `Devices:` 行**：音量对不代表从你以为的喇叭出
  （本轮路由跑到 `bt_a2dp(80)`，怎么放都听不见）。这是「音频不触发」惯犯的第三种形态，前两次都是音量 0。
- **靠坐标驱动的取证，坐标必须现取现用**：文案换行会把下面的控件整体推走（角色 C 的说明行变两行 ⇒
  行车档开关从 y=1213 移到 y=1266），按旧坐标 tap **打空是静默的**。把 `uiautomator dump` 取 bounds
  做进脚本，坐标就再没错过。

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
