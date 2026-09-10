# AR01～AR11 余项收口（2026-09-10 晚）

> 状态：**五件全部闭合**——四件工程正题 + E-02。真机证据见 §8（绑固定包 `1f241c8c5`），
> 真栈证据见 §3.4 / §3.5 / §4（绑生产 release `74852a7`，已 deploy、status ok、verify verified）。
> 用户在本轮单独授权了 push、deploy（含 CI/CD digest）与 E-06 的车控负例用例。
> 前序：[AR06～AR09 工程交付实施记录](2026-09-10-ar06-ar09-engineering-implementation.md)、
> [AR05 实施方案](2026-09-09-ar05-structured-contracts-implementation-plan.md)、
> [工程交付与集中验收安排](2026-09-10-android-goal-delivery-and-acceptance-plan.md)。

## 0. 一句话

**这一轮最重要的产出是推翻了上一轮自己的一个结论。** 上一轮记为「严重内存泄漏、超门槛 55×」
的 F1，是取证装置自己的走法造成的；App 的屏是会被正确释放的。真正的缺陷在旁边，小得多、
但**用户可达且无法自救**：从桌面 Shortcut 反复进入会一次一屏地堆下去。另外三件是
受限身份的解释面、`status` 的运行镜像对账、以及装置本身的修正。

## 1. 这一轮动了什么

| # | 事情 | 归属 | 提交 | 真机/真栈 |
|---|---|---|---|---|
| 1 | 深链只推不弹 ⇒ 桌面 Shortcut 反复进入堆屏 | AR09（正题）| `ea509ff` `046fb5c` `fa6e87d` | ✅ §8.1 |
| 2 | 受限身份 6 次 6 种说法、其中一条谎称已执行 | AR05（附录 B 归的账）| `f260103` `1f241c8` | ✅ §3.4（6/6 逐字相同）|
| 3 | `status` 查不出「跑的是哪一份代码」 | AR06/发布验证面 | `d564bb0` `a06e9f0` | ✅ §4（首批读数当场派上用场）|
| 4 | p3 探针量的不是它声称的东西 | AR09（装置）| `83c1288` | ✅ §8.2 |
| 5 | E-02：02/06 的 Dock 前提未真栈验 | AR06 后置清单 | —（无需改代码）| ✅ §8.3 |
| 6 | E-06：受限身份车控端到端负例 | AR05 V05 | —（②的副产品）| ✅ §3.5（两条闸各验一遍、车态零变化）|

---

## 2. F1 更正：泄漏不存在，堆屏存在

### 2.1 先证伪，再定因

上一轮的读数是真的：30 次 `chat→settings→vehicle→map` 之后 PSS 212MB→1309MB、静置两分钟不回落。
被读成了「路由挂载留下的东西不释放」。

**第一件该做而没做的事是看对象计数。** `dumpsys meminfo` 的 Objects 段里就写着答案：

| 循环数（chat/settings/vehicle）| Views | ViewRootImpl | Activities | PSS |
|---|---|---|---|---|
| 冷基线 | 151 | 1 | 1 | 207 MB |
| 5 | 2651 | 1 | 1 | 316 MB |
| 10 | 5151 | 1 | 1 | 433 MB |
| 15 | 7651 | 1 | 1 | 507 MB |

**每轮恰好 +500 个 View，完全线性，而 Activity 恒为 1。** 这不是「每次挂载漏一点」的形状，
是「屏根本没被卸载」的形状——120 个屏活在导航栈里，内存当然一直涨。

### 2.2 单变量：只差按不按返回键

App 里跨页入口全是 `Link`（push）＋返回键（pop），**没有**「回对话页」的链接。
所以用户的真实路径带 pop，而探针不带。把这一格换掉，其余全部不动：

| 臂（15 轮）| Views 轨迹 | PSS | Native heap |
|---|---|---|---|
| 只推不弹（旧协议）| 151 → 2651 → 5151 → **7651** | 207 → **507 MB** | 49 → 256 MB |
| 推了就弹（用户路径）| 151 → 420 → 520 → 420 → **静置后 151** | 207 → **256 MB** | 49 → 89 MB（非单调，回落）|

Views 逐字回到冷基线 151。**屏是会被正确释放的，F1 描述的那个缺陷不存在。**

C.2 的「空闲 99% 卡顿」是 F1 的后果，因此也随之改写：它描述的是**堆了 120 个屏之后**的
进程，而那个状态在正常导航里到不了。

### 2.3 真正的缺陷（F1′）：深链是「再开一份」，不是「送我过去」

expo-router 对**运行中**收到的 URL 发 `NAVIGATE` 且不带 `pop`，而 StackRouter 只在
「目标恰好是栈顶」时复用现有屏，否则一律推新屏（现读 `expo-router@57.0.16` 的
`StackClient.js` / `getActionFromState.js` 逐行核过）。`xiaozhou://voice` 更糟：
先推一个 voice 屏，再被 `Redirect` 的 replace 换成**第二个对话页**。

**用户可达**：`plugins/with-shortcuts.js` 声明了两个桌面 Shortcut——「说话」→`xiaozhou://voice`、
「车况」→`xiaozhou://vehicle`。

修复前三臂（固定包 `d425b9c2d`，OPPO PEUM00，每臂各自从冷进程起算）：

| 到达次数 | A：voice | B：voice/vehicle 交替 | C：未接管的调试路由交替 |
|---|---|---|---|
| 冷启动 | 151 views / 207 MB | 151 / 208 MB | 151 / 208 MB |
| 5 | 1008 / 263 MB | 874 / 246 MB | 4170 / 445 MB |
| 10 | 1833 / 322 MB | 1335 / 282 MB | 8531 / 629 MB |
| 20 | **3483 / 415 MB** | **2490 / 342 MB** | **16911 / 983 MB** |

每到达一次多约 165 个 View（一整个对话页），线性、无上限，**用户没有任何办法清掉**
（除非连按 N 次返回或杀进程）。

> **为什么对照臂要「交替」而不是连点同一条**：StackRouter 在「目标恰好是栈顶」时会复用，
> 所以连点同一条路由本来就不涨——用它当对照会测不出任何东西。会涨的形态是**目标与栈顶不同**：
> A 臂之所以连点也涨，是因为 voice 屏被 `Redirect` 换成了对话页，栈顶每次都变。

### 2.4 修法与判据

判据一句话：**深链是「把我送到一个目的地」，不是「再开一份」。** 落在
[`mobile/src/core/nav/deepLink.ts`](../../mobile/src/core/nav/deepLink.ts)（纯判据、可单测）
＋ [`mobile/src/app/+native-intent.tsx`](../../mobile/src/app/+native-intent.tsx)（唯一副作用出口）：

- `voice` 不是目的地而是一条指令 ⇒ 规范化成对话页的参数，不占一屏；
- 进任何目的地之前先回栈底的对话页 ⇒ 栈深恒 ≤ 2，点一万次也一样；
- **只接管真正的入口点**（`/`、`/voice`、`/vehicle`、`/settings`、`/map`）。e2e 用深链进的
  调试页与 dev client 的启动链接**原样交回 router**——为了修一个它们没有的问题去改一条
  正在工作的路径，代价永远大于收益；
- 冷启动（`initial=true`）一律交回：那时栈还不存在，插手只会把它建歪。

两步必须由我们自己按序发，**不能**「先弹栈再把 URL 交回 router」：`router.*` 的动作进
`routingQueue` 由渲染时排空，而返回 href 会让 linking listener 当场同步派发 NAVIGATE，
两者先后不可控——NAVIGATE 先落地就会被随后排空的弹栈连目的地一起弹掉。

**同时修掉一个会被这次修复引入的回归**：`voice` 参数原本按**组件实例**一次性消费
（`voiceParamConsumed` ref）。今天每次深链都新建一个对话页，所以每次都能升层；改成回到
**同一个**对话页之后，实例级 ref 会让第二次点「说话」永远不再升层。改为消费**参数本身**
（升层后 `router.setParams({voice: undefined})`），下一次到达重新写入才是一次真实变化。

判据侧 21 条单测（`mobile/test/deepLinkIntent.test.ts`），含：
连点同一 Shortcut 50 次栈深恒定、两个 Shortcut 交替 50 次不堆积、
**反向验证**（不做规范化就会线性堆积到 21 层）、五个入口点逐个被接管、
非入口点逐个交回、语音参数每次到达都重新出现，以及**桌面 Shortcut 声明源与入口点不许漂移**
（读 `plugins/with-shortcuts.js` 本身，不另抄名单；从 `ENTRY_POINTS` 拿掉 `/vehicle` 后这条当场判红）。

## 3. AR05 解释面：没权限就说没权限

### 3.1 症状与根因

[附录 B](2026-09-10-ar06-ar09-engineering-implementation.md) 实录：只带 `location.read` 的身份
连问 6 次「导航去广州塔」，`actions` 6/6 为空（**安全面成立**），但话术 6 种，其中
#1「好的，已为你规划路线前往广州塔」——**说了没做**，#4 把内部错误串
`unsupported datetime format` 原样吐给用户，**0/6** 提到真实原因。

根因不是话术不好，是**理由被丢掉了**：`planning.py` 规划前按权限过滤 catalog
（这是对的，越权能力不该暴露给 LLM），但过滤完「为什么少了这条能力」没跟出来，
LLM 只剩凭空解释一条路。而理由服务端一直有：同一 token 查 `/api/session` 就写着
`navigation → unauthorized / scope_missing`。

> 同一形态本仓已经踩过一次：`planning.py` 里那段 Q7 注释写着「同一句话三次取样给出三种结果，
> 其中一次是 chitchat 声称『已为您关闭天窗』而 action 为空」。当时的结论是**确定性成计划、
> 一次 LLM 都不调**。这次是同一条判据在解释面上的兑现。

### 3.2 修法

`_filter_by_permission` → `_partition_by_permission`：过滤照旧，但**被挡下的那半留在手里当理由**。
这一轮请求若落在其中一条能力上，直接给确定性终态，**一次 LLM 都不调**：

- `Issue(code=permission.scope_missing, severity=warning, scope=request)`，`affected_capabilities` 点名那条能力；
- 话术由服务端出，明说「没有授权」且**明说没有去做**；
- 恢复出口指**能力设置**，不指系统权限页（业务 scope ≠ 设备权限，`conventions.md` §9 第 7 条）。

「这句话想要哪条能力」复用**已有的**答案源：Registry 语义路由 top-1 ＋ 既有门槛
`CLARIFY_FALLBACK_MIN`。**不写新词表、不加新阈值。** 分不清就返回 None ——
宁可退回今天的行为，也不冤枉一次「你没权限」。没有被挡下的能力时（绝大多数身份）
这条路一步都不走，连那次语义路由都不发。

### 3.3 判据与反向验证

11 条单测（`orchestrator/cloud/tests/test_scope_blocked_explanation.py`），双向反向验证：

| 变异 | 判红的用例 |
|---|---|
| 关掉判据（`_scope_blocked_target` 恒返回 None）| 4 条正例全红（点名、话术、零 LLM、恢复出口）|
| 去掉分数门槛（低分也判「没权限」）| 「不冤枉」那条红 |

另有一条守卫挂在**真实 manifest** 上：正常身份（PoC 默认 scope）一条能力都不该被挡下，
否则每一轮都会多发一次语义路由。实测 14 个 Agent 全放行、被挡下 0 个；换成只有
`location.read` 的受限身份则放行 2、挡下 12。让它变坏的方式是「某个 Agent 新增了一条
PoC 默认没给的 scope」——那种改动不碰这批文件里的任何一个，只有对着真声明才拦得住。

### 3.4 真栈复验（deploy 后，绑 `74852a7`）

用户在本轮单独授权了 push 与 deploy（含 CI/CD digest）。发布落地后用**同一个受限 token**
复跑附录 B 的同题六次：

| | 修复前（`d425b9c`，附录 B）| 修复后（`74852a7`）|
|---|---|---|
| `actions` | 6/6 为空 | 6/6 为空（安全面不变）|
| 话术 | **6 次 6 种**，含一条「已为你规划路线」（说了没做）与一条内部错误串 | **6/6 逐字相同**|
| 提到真实原因 | **0/6** | **6/6** |
| `issues[].code` | 无 / `planner.technical_failure` | 6/6 `permission.scope_missing` |

真栈实际下行（原样）：

```json
{"actions": [], "need_confirm": false,
 "speech": "当前账号没有「导航助手」这项能力的授权，所以这件事我没有去做。可以在能力设置里看看这个账号现在有哪些能力。",
 "issues": [{"code": "permission.scope_missing", "severity": "warning", "scope": "request",
             "affected_capabilities": ["navigation"],
             "message": "当前账号缺少「导航助手」所需的授权，本轮没有执行任何操作。",
             "recovery": [{"kind": "open_capability_settings", "label": "查看账号能力"}]}]}
```

`display_name` 落到「导航助手」而不是机器名 `navigation`，恢复出口是客户端**真的实现了**的
`open_capability_settings`（`AssistantProvider.onIssueAction` → 设置页，那里就是能力逐条状态）。

⚠ 取证纠错：第一版探针读 final 的 `text` 字段，于是六次都报「话术为空」。
下行里字段名是 `speech`。**差点把探针的读法写成产品的读数**——这一轮第三次遇到同一类事。

### 3.5 E-06：受限身份的车控端到端负例（真栈，用户单独授权）

两种形态分开发，因为它们证明的不是同一件事——只看到「没执行」可能只是**确认闸**拦的：

| 语料 | `actions` | `confirm_policy`/`operation_id` | `issues[].code` | `affected` | 走的是哪条闸 |
|---|---|---|---|---|---|
| 「打开后备箱」（`require_confirm=true`）| `[]` | **都没有生成** | `permission.scope_missing` | `edge-vehicle`（agent id）| **云侧本次新增的判据**——scope 闸**先于**确认闸 |
| 「打开空调26度」（无需确认；闸失效即当场执行）| `[]` | 无 | `permission.scope_missing` | `vehicle.control`（scope 名）| **端侧 T0 闸**（AR05 步骤 0 既有），压根没到云端 |

**车态两次读数逐字段相同**（`hvac_on=false`、`hvac_temp=24`、`trunk=closed`、`door_lock=locked` …），
零执行、零挂起残留、无需还原。

两条入口落在两条不同的闸上，但客户端拿到的是**同一个 code、同一种恢复出口**——
契约面一致而实现分层，这正是 AR05 想要的形状。

## 4. `status` 的运行镜像对账

同日一次生产回退：手工 compose 漏设 `RELEASE_SHA`，compose 从 `.env` 取到一个月前的值，
edge-gateway 换成八月的镜像、`/api/session` 404 数分钟。而 `status` 全程 **5/5 healthy、零 warning**
——`current` 软链没动、五个健康端点都答了。

> **健康检查回答的是「它活着吗」，回答不了「活着的是哪一份代码」。**

远端 preflight 现在把运行中的 `car-agent-release/*` 容器**实际使用的镜像 tag** 一并报出来
（同一次 `docker inspect`，不多一次远端调用），云侧 status 拿它和记录的 release 对账。
三种情况**分开报**，不合成一句：

- 不一致 → `running release image <a> does not match the recorded release <b>`；
- 容器之间彼此不一致（半量替换后剩下的那半）→ `running release images disagree`；
- 读不到 → `running release images are unknown`。

任一 warning ⇒ `status` 判 `degraded`（退出码 1）。**「没读到」不许显示成「一致」。**

反向验证：把「不一致就报」那一行关掉，`test_cloud_status_catches_a_release_that_did_not_take_effect`
当场判红。

**真栈首批读数**（本轮 deploy 前后各一次）：

| 时刻 | `release_sha` | `running_release_sha` | status |
|---|---|---|---|
| apply 失败之后（用来判断生产有没有被动过）| `d425b9c…` | `d425b9c…` | ok / 5 healthy / 零 warning |
| deploy 落地之后 | `74852a7…` | `74852a7…` | ok / 5 healthy / 零 warning |

第一次读数当场派上了用场：一次 apply 报 `runtime` 失败，**这一列直接回答了「生产有没有被动过」**
——两列一致且仍是旧 SHA ⇒ 没动过，于是重试是在一个已核实的干净状态上，不是盲目原地重试。
在这条列存在之前，同样的问题只能靠猜。

## 5. 装置修正：p3 走用户走的路

`--nav back`（新默认）进页面后按返回键；`--nav deeplink` 保留旧走法，但它现在被明确标注为
**外部深链反复到达**这个独立场景，不再冒充「用户在页面间来回走」。
内存读数同时带上 `Views/ViewRootImpl/Activities/AppContexts`——**这一列才是把 F1 判断错的
那次和这次分开的东西**；没有它，读数说不出「涨的是不是活着的屏」。

## 6. 本机验证

全量固定口径（`$env:TZ='UTC0'`；`python -X utf8 -m pytest -q -n 8 --dist worksteal`）：

| 面 | 结果 |
|---|---|
| **全量 pytest** | **8231 passed / 32 skipped / 0 failed**，309s（上一基线 8205/32，绑 `00d1925`）|
| mobile | `tsc` 0 / `eslint . --max-warnings 0` 0 / `jest` **876 passed, 81 suites**（批前 855/80）|
| hmi | `npm test` **333 pass / 0 fail** |
| gateway | `go build` / `go vet` / `go test ./gateway/...` 全绿 |
| 四道门禁 + 端侧 smoke | skills ✅ / exemplars ✅（hit 64 miss 3，域错配 1.8%）/ L0 strict 2/2 / capability integrity ✅ / `smoke_edge` 13 passed |

⚠ 全量第一趟红了 3 条（`test_probe_qa_long_sessions.py`）：`StackStatus` 加一列之后
**还有一个我没找到的构造点**——那份测试自己搭了一个「健康云端快照」。
分目录跑的三个套件都覆盖不到它，只有全量能露。已修（`fa6e87d`）。
> 判据：**加一个必填字段就要把构造点找全，而"我 grep 过了"不是找全**——
> 漏掉的那个恰好在一个名字里没有 `dev_stack` 的文件里。

## 7. 余项去向（承接 [AR06～AR09 §6](2026-09-10-ar06-ar09-engineering-implementation.md) 的 E 清单）

| item_id | 本轮变化 | 现在卡在哪 | 已备好的产物 | 直接执行步骤 | 成功判据 |
|---|---|---|---|---|---|
| ~~E-01~~ | — | — | — | — | 上一轮已闭合 |
| ~~E-02~~ | **本轮闭合**，见 §8.3 | — | — | — | 两趟 RC=0、开关两次回读均为设定值 |
| E-03 | 不变 | **真人说话** | KWS 参数入口、回读、四分栏计数器 | 开实验会话 → A/B/C 各 10 次近场 | 每臂四栏齐；分母由人记 |
| E-04 | 不变 | **外部时基 + 真人** | `attachMeasuredOnset` 入口、分桶统计 | 同一时基录「说完」与「扬声器首音」→ 回填 | `firstAudioSource=acoustic` 样本 ≥ 每桶每臂 |
| E-05 | **P3 协议已修正并复测**（§8.2）、**动效归因已闭合**（§8.4）；P0/P5 仍是上一轮读数 | P1 缺离线灌数 harness；P2 需真人；P4 需真实低电量；系统级动画缩放那一臂被 ColorOS 的 `WRITE_SECURE_SETTINGS` 锁死 | 修正后的 `probe_ui_perf.py`（`--nav`＋对象计数）| P1 的做法已定：仿 `card-gallery` 加一条**只读调试路由**，用**真的** chat 列表组件渲染 N 条合成消息（不向生产发请求）；其余按协议 | 逐场景有固定包证据；两档分列 |
| ~~E-06~~ | **本轮闭合**，见 §3.5（用户单独授权该用例）| — | 受限 token 文件已在本机 `%LOCALAPPDATA%\car-agent\artifacts\ar10-restricted-token\token.txt`；`probe_session_scope.py` 只读 | 见 AR05 §9.5 V05 | 端侧 T0 拒绝、云侧 dispatch 拒绝、零 VAL、零挂起残留 |
| E-07 | 不变 | 旧 release 环境 + 一次 Redis 重启 | — | AR05 §9.5 V06/V10 | 挂起恢复保真；新客户端不误报 |
| E-08 | 不变 | 参与者与授权 | AR10 准备材料 | 按 §4 脚本 | 逐格原始记录 |
| ~~AR05 请求面真栈复验~~ | **本轮闭合**，见 §3.4 | — | 完整 `origin/main..HEAD`、受限 token、`probe_session_scope.py` | deploy 后用受限 token 复跑附录 B 的 6 次同题 | 6/6 `actions=[]` **且** 6/6 出 `permission.scope_missing`、零「已为你…」类话术 |
| ~~`status` 对账真栈复验~~ | **本轮闭合**，见 §4 | — | — | `python scripts/dev_stack.py status` | 输出含 `running_release_sha` 且等于 release，零 warning |

## 8. 真机执行结果

### 8.0 包身份与冻结条件

| 项 | 值 |
|---|---|
| APK | `D:\Android\builds\apk\xiaozhou-companion-prod-release-1f241c8c5-20260910-1335.apk`（210,755,666 B）|
| APK SHA-256 | `4c2c16bd837fa3ca30ea3ecb70ff2aa642fd6cd4421ed1a289b791818fba6985` |
| 设备侧回读 | `/data/app/~~ggks37MpwzQXfL1Y7Qlrtg==/…/base.apk` 的 `sha256sum` **与本地逐字节相同** |
| 包身份 | `variant=prod`、`build=1f241c8c5`、签名 SHA-1 `5e8f1606…`、`flags` 不含 `DEBUGGABLE`、`lastUpdateTime=2026-09-10 13:36:24` |
| 代码边界 | `git diff --name-only 1f241c8..<本轮末> -- mobile hmi/src` 只含 `mobile/test/`（不进 bundle）⇒ 包内容即该 SHA 的 mobile 树 |
| 设备 | test / OPPO PEUM00 / Android 14 / `919fd6f9`；活跃屏外屏 60Hz ⇒ 帧预算 16.667ms |
| 真栈 | `target=cloud`，设备在 tailnet（`ping 100.64.0.1` 通）；运行中的是既有 release `d425b9c`，**本轮未 deploy** |

### 8.1 F1′ 三臂对照（修复前后同设备、同装置、同协议）

| 到达 20 次 | 修复前 `d425b9c2d` | 修复后 `1f241c8c5` |
|---|---|---|
| A：`xiaozhou://voice`（Shortcut「说话」）| 3483 views / 415 MB | **183 views / 248 MB** |
| B：voice/vehicle 交替 | 2490 / 342 MB | **278 / 253 MB** |
| C：**故意不接管**的调试路由交替 | 16911 / 983 MB | **16911 / 979 MB** |

A 臂在 x5 就到 183 并**从此不再增长**（x10、x20 同为 183）。
C 臂的 views **逐字相同（16911）**——这一格才是这张表的分量所在：
装置照旧测得出「涨」，所以 A/B 的「不涨」不是仪器失灵；而且它证明本次改动
**确实没碰**入口点之外的任何一条路由。

### 8.2 AR09 P3：修正协议下的路由循环（E-05 的 P3 格）

30 次 `chat→settings→vehicle→map`，`--nav back`（进页面后按返回键 = 用户真实路径）：

| 轮次 | 起点 PSS | 静置 2min 后 | 增长 | Views | Native heap | Graphics |
|---|---|---|---|---|---|---|
| 第 1 趟（冷进程起算）| 224.0 MB | 312.7 MB | **+88.7 MB（+39.6%）** | 151 → **151** | 48.8 → 112.4 MB | 32.8 → 36.8 MB |
| 第 2 趟（接着上一趟的暖进程）| 314.8 MB | 315.8 MB | **+1.0 MB（+0.3%）** | 151 → **151** | 112.3 → **106.2 MB** | 36.75 → 36.89 MB |

**第二趟是决定性的那一趟。** 同样 30 轮、同样协议，只差「从冷进程还是暖进程起算」：
泄漏会再加一个 +89MB，实测只加了 1MB，Native heap 还降了 6MB。
⇒ 第一趟那 +88.7MB 是**首次触达成本**（代码段换页 +20MB、原生分配器与地图 GL 面首用），
不是累积。AR09 §4.1 的门槛要**在预热之后**量才有意义——从冷进程量会把首用成本
算成累积，这正是上一轮把 F1 读错的同一类错误的温和版本。

帧（第 1 趟 / 第 2 趟）：10446 帧 4.10% janky、p50 17ms、p95 28ms、missed vsync 32 ／
10453 帧 3.71% janky、p50 16ms、p95 27ms、missed vsync 22。60Hz 预算 16.667ms。
对照上一轮那个被撑大的进程（99.4% janky、p50 69ms），**健康进程在预算之内**。

### 8.3 E-02：02/06 的 Dock 前提真栈闭合

| 趟 | 前提（`set_switch.py` 回读）| 流 | 结果 |
|---|---|---|---|
| A | `uxV2Dock` before=True → **after=False, OK** | `02-danger-confirm-cancel.yaml` `-e APP_LAUNCH_MODE=release` | **RC=0**：release 启动路径（阴性断言「开发服务器」不可见通过）→「打开后备箱」→ 确认条出现且**两个按钮都在** → 取消 → 两个都消失 |
| B | `uxV2Dock` before=False → **after=True, OK** | `06-confirm-dock.yaml` `-e APP_LAUNCH_MODE=release` | **RC=0**：`dock-confirm` + `dock-countdown` + `presence-capsule` 三者同时在场 → 取消 → Dock 消失 |

两条流**都走取消**，全程零车控执行。开关最终值 `true` 与开工前一致，无需还原。

> **顺带定死一条操作判据**：`set_switch.py` 第一次跑报 `NOT_FOUND`，manual `uiautomator dump`
> 也被 SIGKILL——**整机**都 dump 不出来，连桌面都不行。logcat 给出真因：
> `UiAutomationService … already registered!`——**上一次 Maestro 留下的 driver 还占着
> UiAutomation 连接**。`am force-stop dev.mobile.maestro{,.test}` 之后立刻恢复。
> ⇒ Maestro 与任何走 `uiautomator` 的工具**互斥**；两者交替使用时，切换前必须先停 driver。
> 这也是「取证装置互相污染」的又一实例：报出来的症状（找不到某个开关）指向被测对象，
> 真因却在另一件工具上。

### 8.4 E-05 补一格：对话页的空闲渲染归因（`reduceMotionForce` 开/关）

[C.5](2026-09-10-ar06-ar09-engineering-implementation.md) 把这一格记成「两条路都断」。
**其中一条现在通了**——`set_switch.py` 走 `uiautomator` 按 testID 设值并回读，
而它此前失败的真因是 §8.3 那条（Maestro driver 占着 UiAutomation），不是设置页本身。
于是这个 App 内开关的单变量 A/B 可以做了：

| `reduceMotionForce` | 帧数 / 180s 空闲 | janky | p50 | PSS 末 |
|---|---|---|---|---|
| **off**（默认）| **10792（≈60fps 满帧）** | 0.01%（1 帧）| 17 ms | 226.7 MB |
| **on**（强制静止）| **0** | — | 未测到（没有帧就没有分位数）| 226.5 MB |

⇒ 对话页空闲时的持续渲染**完全来自动效层**，App 自带的开关能把它归零；
两臂 PSS 逐 MB 相同，**动效的代价在帧不在内存**。
上一轮 F3 记的「只有对话页在空闲时持续渲染」由此从「背景事实」变成**已归因**。
开关已回读还原为开工前的 `false`。

⚠ 仍未测：ColorOS 锁 `WRITE_SECURE_SETTINGS` ⇒ 系统级动画缩放那一臂照旧改不了；
本格只覆盖 App 内开关。

### 8.4 本轮没有做的设备动作

未改任何**系统**设置、未动权限矩阵、未采集音视频、未操作 Xiaomi 对照机；
唯二改动的是 App 自己的两个开关（`uxV2Dock`、`reduceMotionForce`），都逐次回读、都已还原为开工前的值；
临时文件（`/sdcard/*.xml`）已删除；Maestro driver 已停，`uiautomator` 复检可用。

## 9. 明确没做的事

- 没有取得 KWS 唤醒率读数（E-03，本轮用户未安排）；
- 没有做首音声学校准（E-04）——需要外部时基装置与真人；
- 没有招募 AR10 参与者、没有向任何人发送邀请（E-08）；
- 没有跑 P1（500 条本地消息，缺离线灌数 harness）、P2（需真人）、P4（需真实低电量）；
- 系统级动画缩放那一臂仍被 ColorOS 的 `WRITE_SECURE_SETTINGS` 锁死，只做了 App 内开关；
- 没有验旧服务端 / Redis 重启后的挂起恢复（E-07，缺旧 release 环境）；
- 没有改任何系统设置、`.env`、安全组、Tailscale、systemd 或数据库 schema；
- 没有把 AR02～AR05 的其余未签收项标成已签收——本轮闭合的是 V05/V07 的两条缺口与 E-02/E-06，
  V06（Redis 恢复）、V08（真人听音）、V09（技术失败不可稳定复现）、V10（旧服务端）照旧未闭合。
