# AR01～AR11 余项收口（2026-09-10 晚）

> 状态：**四件工程正题已实施并本机验证**；真机证据见 §2.4（绑固定包），真栈证据见 §3.4。
> 前序：[AR06～AR09 工程交付实施记录](2026-09-10-ar06-ar09-engineering-implementation.md)、
> [AR05 实施方案](2026-09-09-ar05-structured-contracts-implementation-plan.md)、
> [工程交付与集中验收安排](2026-09-10-android-goal-delivery-and-acceptance-plan.md)。

## 0. 一句话

**这一轮最重要的产出是推翻了上一轮自己的一个结论。** 上一轮记为「严重内存泄漏、超门槛 55×」
的 F1，是取证装置自己的走法造成的；App 的屏是会被正确释放的。真正的缺陷在旁边，小得多、
但**用户可达且无法自救**：从桌面 Shortcut 反复进入会一次一屏地堆下去。另外三件是
受限身份的解释面、`status` 的运行镜像对账、以及装置本身的修正。

## 1. 这一轮动了什么

| # | 事情 | 归属 | 提交 |
|---|---|---|---|
| 1 | 深链只推不弹 ⇒ 桌面 Shortcut 反复进入堆屏 | AR09（正题）| `ea509ff` + `046fb5c` |
| 2 | 受限身份 6 次 6 种说法、其中一条谎称已执行 | AR05（附录 B 归的账）| `f260103` |
| 3 | `status` 查不出「跑的是哪一份代码」 | AR06/发布验证面 | `d564bb0` |
| 4 | p3 探针量的不是它声称的东西 | AR09（装置）| `83c1288` |

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
「车况」→`xiaozhou://vehicle`。从桌面长按图标点「说话」：

| 点击次数 | Views | PSS | Native heap |
|---|---|---|---|
| 冷启动 | 151 | 207 MB | 49 MB |
| 5 | 1008 | 261 MB | 104 MB |
| 10 | 1833 | 333 MB | 137 MB |
| 20 | **3483** | **407 MB（+97%）** | 189 MB |

每点一次多约 165 个 View（一整个对话页），线性、无上限，**用户没有任何办法清掉**
（除非连按 20 次返回或杀进程）。

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

判据侧 20 条单测（`mobile/test/deepLinkIntent.test.ts`），含：
连点同一 Shortcut 50 次栈深恒定、两个 Shortcut 交替 50 次不堆积、
**反向验证**（不做规范化就会线性堆积到 21 层）、五个入口点逐个被接管、
非入口点逐个交回、语音参数每次到达都重新出现。

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

10 条单测（`orchestrator/cloud/tests/test_scope_blocked_explanation.py`），双向反向验证：

| 变异 | 判红的用例 |
|---|---|
| 关掉判据（`_scope_blocked_target` 恒返回 None）| 4 条正例全红（点名、话术、零 LLM、恢复出口）|
| 去掉分数门槛（低分也判「没权限」）| 「不冤枉」那条红 |

### 3.4 还没做的

真栈复验要 deploy（红线，需单独授权 dry-run→apply）。本轮**未 deploy、未 status/verify**，
本节没有任何真栈读数。附录 B 那张 6 次表是**修复前**的证据，不得当作修复后的读数。

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

## 5. 装置修正：p3 走用户走的路

`--nav back`（新默认）进页面后按返回键；`--nav deeplink` 保留旧走法，但它现在被明确标注为
**外部深链反复到达**这个独立场景，不再冒充「用户在页面间来回走」。
内存读数同时带上 `Views/ViewRootImpl/Activities/AppContexts`——**这一列才是把 F1 判断错的
那次和这次分开的东西**；没有它，读数说不出「涨的是不是活着的屏」。

## 6. 本机验证

| 面 | 结果 |
|---|---|
| mobile | `tsc` 0 / `eslint . --max-warnings 0` 0 / `jest` **875 passed, 81 suites**（批前 855/80）|
| orchestrator + security + registry | **2294 passed, 1 skipped**（`-p no:randomly`）|
| scripts（dev_stack + cloud_release + release_source_safety）| **315 passed, 3 skipped** |

## 7. 明确没做的事

- 没有 deploy、没有 status/verify，本文没有任何真栈读数；
- 没有推送任何提交；
- 没有取得唤醒率、首音时延读数（E-03/E-04 仍缺真人）；
- 没有招募 AR10 参与者（E-08）；
- 没有把 AR02～AR05 的任何未签收项标成已签收。
