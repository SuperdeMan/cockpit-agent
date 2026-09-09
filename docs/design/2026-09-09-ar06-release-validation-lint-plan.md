# AR06：Release 验证与 lint 实施方案

> 状态：**草案，未实施**；日期：2026-09-09。
> 交付对象：mobile 工程、测试工程及接手 Agent。对应 R13；为 AR02～AR05 的设备补证解除工具阻塞。
> 基线、通用授权及证据格式见[接续路线](2026-09-09-android-ar06-ar11-execution-roadmap.md)。

## 1. 要交付的结果

从干净 checkout 按锁定依赖运行 TypeScript/Jest/ESLint，并在一份可离开 Metro 的 prod release 上重复启动、输入、设置前提、卡片与取消流程。每个失败能分清工具、前提、产品和外部服务，不再把 dev-client 读数当成常驻包验证。

本批建立本地检查和 release 验证能力。CI 接入给出具体差异及运行成本后单独执行；正式签名、商店发布、OTA 属 AR11 后续工作包。

## 2. 当前代码事实

| 入口 | 已核对的事实 | 本批处理 |
|---|---|---|
| [package.json](../../mobile/package.json) | `lint=expo lint`；已有 ESLint 9 / eslint-config-expo 57；仓库未跟踪 mobile ESLint 配置 | 先固定配置，再取得当前诊断；历史 63 errors/43 warnings 不作当前基线 |
| [app.config.ts](../../mobile/app.config.ts) | React Compiler 开启；Reanimated 4.5.1；CNG | 处理规则冲突时保持 Compiler 与动画的真实语义 |
| [open-app.yaml](../../mobile/e2e/subflows/open-app.yaml) | 全部流共用 dev-client 深链及 localhost:8081 | 入口显式分 dev-client / release，不猜安装包类型 |
| [02](../../mobile/e2e/02-danger-confirm-cancel.yaml) / [06](../../mobile/e2e/06-confirm-dock.yaml) | 分别依赖 `uxV2Dock=false/true`，流内未建立前提 | 流前设置、UI 回读、流后恢复原值 |
| [e2e 配置](../../mobile/e2e/config.yaml) | 01～09 分 offline/online/manual；部分会改网络或进入危险动作确认 | 不把整个目录当安全 smoke；逐条登记副作用与授权 |
| [diagnostics.ts](../../mobile/src/core/diagnostics.ts) | 操作诊断只允许显式 dev 变体 | prod 自动化不新增自动录音、执行命令或改播放器的深链 |
| [buildInfo.ts](../../mobile/src/core/buildInfo.ts) / [构建脚本](../../scripts/build_mobile.ps1) | 已有变体、SHA、时间、bundle/原生件检查 | 扩充回读，不重新发明包身份 |
| [.github/workflows/ci.yml](../../.github/workflows/ci.yml) / [APK workflow](../../.github/workflows/mobile-apk.yml) | mobile 检查有 tsc/Jest；APK 主要 assembleDebug、E2E 手动触发 | CI 变更与本地工程完成分开登记 |

## 3. ESLint 固定口径

1. 拟新增 `mobile/eslint.config.js`，基于仓库锁定版本的 `eslint-config-expo/flat`。实施时先恢复锁定依赖并检查实际导出；本轮未安装依赖或验证本机 node_modules。锁定 `package-lock.json`，本批不升级 Expo/RN。
2. 范围包括 `src/`、`modules/` 的 TS、`test/`、`plugins/` 与根配置脚本；按运行环境分组，忽略生成的 Android、node_modules、二进制资产与证据目录。不能把整个测试/原生桥 JS 目录排除来减数字。
3. 推荐本地固定入口为 `eslint . --max-warnings 0`；报告按文件域、规则和错误/警告分列。此为拟议完成门槛，尚未运行。默认不做全仓格式化，不加入新格式化依赖。
4. 分类修复：真实 Hook/闭包/副作用问题修实现；未用符号和依赖整理做小改；原生可选模块加载等规则适配限具体文件/语句，注明为什么在本进程调用顺序恒定及现有行为验证。
5. Reanimated 的 `.value` 用法逐处评估，优先采用已安装版本支持的 `get()/set()`；读写仍应在 effect/worklet/事件回调中，不能只换语法而继续在 render 内作副作用。官方对此有专门的 [React Compiler 说明](https://docs.swmansion.com/react-native-reanimated/docs/core/useSharedValue/)。
6. 不全局关闭 hooks/immutability、不关闭 React Compiler 来消除告警。大规模性能重构归 AR09；若 lint 指向真正生命周期错误，本批窄修并通知 AR09 更新基线。

配置方式参考 [Expo ESLint 指南](https://docs.expo.dev/guides/using-eslint/)，实施时以本地安装版本的导出及 `--print-config` 结果为准，不执行可能首次生成配置的 `expo lint` 来充当只读盘点。

## 4. 自动化入口与前提

### 4.1 分开入口，共用业务步骤

拟新增 `subflows/open-app-dev.yaml`、`subflows/open-app-release.yaml`，保留现有入口作为显式分发器或兼容 dev 包装。入口参数名建议 `APP_LAUNCH_MODE=dev-client|release`；未知值直接报前提失败。

| 轨道 | 启动及断言 | 能证明什么 |
|---|---|---|
| dev-client | 预检 Metro、只对该测试设备配置的转发、打开开发深链、等待实际页面 | 开发行为与调试接线 |
| prod release | 按包名启动主 Activity，检查嵌入 build、无 Metro 文案、正确 variant；不得发送开发深链 | 常驻交付包能独立运行 |
| offline smoke | 本地画廊与导航，不主动发业务/启采集；与手机是否已有网络分开 | 包、路由、渲染可用；不是服务端业务结果 |
| online | 先核 target、云端 SHA、设备连接、摘要权限和当前无关待办 | 带真实输入/输出的选定流程 |
| manual | 说话、系统特殊状态、听辨或实体折叠 | 明确人工步骤，不计自动化通过数 |

注意 [Maestro launchApp](https://docs.maestro.dev/reference/commands-available/launchapp) 默认权限处理和停 App 行为。测试驱动不得悄悄“全授权限”；权限态用专门场景设置、回读并还原。不要默认 `clearState`、卸载或清 SecureStore。新装场景使用明确可重置的测试环境，所需删除/系统操作依项目规则授权。

### 4.2 中文输入是第一个设备探针

先固定 Maestro/driver/IME 版本；在 **prod、真实 Composer、发送前**填入一条无副作用中文（如“介绍广州的历史”），逐字回读，再正常点发送并对账请求。

- 官方 [Known issues](https://docs.maestro.dev/extra-materials/troubleshooting/known-issues) 仍列 Android `inputText` 仅支持 ASCII。必须现场试验实际版本，不能把旧成功记录当能力保证。
- 先验证已安装工具的 UI 文本设置或系统剪贴板粘贴通道；仅使用批准的专用 driver/测试输入工具。`pasteText` 的内部剪贴板与系统剪贴板不同，不能假定互通，见[官方说明](https://docs.maestro.dev/reference/commands-available/pastetext)。
- 若需新全局 CLI、IME 或系统输入设置，先把候选版本、安装路径、具体用途和恢复方式准备好，再按红线请求授权。也可由用户手动输入，后续断言继续自动；该格标人工辅助，不冒充全自动。
- 输入失败发生在工具层时保留 `BLOCKED`。不能改成英文语料后声称中文流程通过，也不能在 prod 加“URL 自动发业务”的后门。

### 4.3 Dock 与配置升级

02/06 各自通过现有设置 UI 建立并回读开关，不依赖上一次测试残留。结束后恢复原值；失败时外层 runner 也执行可用的恢复步骤并回读。若未来采用启动参数，只能驱动既有非敏感 UI 前提，需真实应用接收逻辑与断言，不能以参数名存在就认为设置已生效。

补配置场景：原版本设置缺新字段、配置不完整、凭证明确失效、服务器配置重建。使用既有默认/兼容策略；改变存储 schema/迁移不在本批默认范围。权限失败、404 旧服务端、网络未知仍遵守 AR05 的不同语义。

## 5. 实施步骤与产物

| 步骤 | 工作 | 完成产物 |
|---|---|---|
| A06-0 | 读取规则/target，核工具、依赖、工作树，盘点已安装 Maestro 与 driver | 基线记录、当前 lint 分类表、资源占用表；不碰设备设置 |
| A06-1 | 固定 ESLint 配置与 npm 命令，逐类修复 | clean checkout 可复现的 0 error/0 warning；适配理由清单 |
| A06-2 | dev/release 分入口、02/06 设置与回读、测试副作用清单 | 流与 runner 参数契约；工具失败单独分类 |
| A06-3 | 中文输入小探针、错误前提负例 | 确认可用路径，或具体人工/工具前置；不先跑长业务流程 |
| A06-4 | 按共享构建指南冻结源码、构建并验包、OPPO 指定流程 | APK hash/签名/ABI/原生注册/bundle/设备 build；单项流程证据 |
| A06-5 | 提供 CI 接入的精确差异和成本 | lint 接现有 mobile job、release smoke 手动档的提案；获授权后再改 workflow 并取实际结果 |
| A06-6 | 文档回填与交接 | 本地/设备/CI 三栏状态；AR02～AR05 可接续的输入工具说明 |

CI 成本先量本地 lint 时长；release job 涉模型下载、双 ABI 原生构建、地图构建键和 ARM 设备，不能直接把现有 x86 emulator/debug job 改名当成 prod 验证。建议先接零网络 lint，release 设备验收维持手动触发；未获 CI 修改授权时明确“本地门禁成立、CI 未接入”。

## 6. 验收矩阵

| ID | 场景 | 判据 |
|---|---|---|
| A06-V01 | 干净 checkout、锁定依赖 | tsc/Jest/lint 退出 0；lint 文件范围明确；无首次运行自动生成遗漏 |
| A06-V02 | lint 负例 | 在隔离试验输入注入禁用模式，指定规则判红；恢复后通过，不提交变异 |
| A06-V03 | dev/release 启动 | 两条各验证；prod 不发开发深链，Metro 不可用仍可进入主屏 |
| A06-V04 | 中文输入 | 输入框逐字一致、正常发送、requestId 对得上；不是 UI 样本注入 |
| A06-V05 | 02/06 前提 | 各建正确 Dock 状态并恢复；错前提时先报前提，不等业务超时 |
| A06-V06 | 必要 release 流 | 04/09 离线、01 文本、08 键盘；02/06 只在授权的确认→取消场景运行并检查零执行及挂起归零 |
| A06-V07 | 包身份 | 设备与本地 APK SHA-256 一致、非 DEBUGGABLE、prod、内嵌 bundle、PackageList/Expo 模块注册实际可用 |
| A06-V08 | 冷启动/升级/重建配置 | 已有配置保留且默认兼容；新账号不接旧请求；新装/删除前置单列，不自动清数据 |
| A06-V09 | prod 诊断回归 | 已有 AR02 非自动采集门禁不退化；无外部链接新采集/自动执行业务 |
| A06-V10 | CI | 精确修改授权、实际执行结果齐才标已接入；仅提案不得标 CI green |

本地计划命令（A06-1 落地后，PowerShell，在 `mobile/`）：

```powershell
npm run typecheck
npm test -- --runInBand
npm run lint
```

实现时保存每条命令退出码。重点沿用 `diagnosticRoutes`、`sharedAllowlist`、`ar05Contracts`、`ar05Dock` 和会话取消测试；只有本批改变的行为需要补新回归。改共享纯逻辑时追加 HMI 测试/build，纯 flow/lint 改动不跑云端全量。

## 7. 完成边界与 Goal 文本

本地工程、prod 设备验证、CI 接入分别出状态。R13 的 release 路径必须有真实包证据；无中文或无设备时只能交本地部分。CI 若本轮未授权可单独挂账，但不得写“CI lint 门禁已闭合”。

执行方式以[工程交付与集中验收安排](2026-09-10-android-goal-delivery-and-acceptance-plan.md)为准。下面两种目标在启动时选择；默认采用工程交付目标，本文完整验收矩阵仍保留。

### 工程交付 Goal（默认）

> 按 AR06 方案完成工程交付：固定 ESLint 与检查口径，修复相关问题，完成 dev/release 分轨、中文输入预检、Dock 前提与验包工具。把已授权且具备条件的候选构建和设备自动验证实际跑完；CI、系统变更、新装/删除等需要额外授权的操作先准备具体材料，继续其他独立工作。结束交代码、测试、可用 APK/证据及精确后置清单，不以未完成的人工或 CI 项阻断普通工程任务，也不把它们标通过。

### 完整验收 Goal（另行启动）

> 接手并实施 AR06，先读本方案与 Android 构建指南，核对当前树和工具。完成 ESLint 固定口径、dev/release 自动化分轨、中文输入与 Dock 前提闭环；本地检查通过后，在冻结的 prod release 上完成本方案设备矩阵并落证据。CI 修改先准备具体差异与成本再按项目红线处理。未执行的设备/CI 项如实保留，不借历史通过数，不关闭 AR02～AR05 的业务余项。

实施记录追加：采用范围、代码/测试 SHA、lint 分项、APK/设备身份、各 V 项结果、CI 状态、失败/阻塞、资源恢复、下一步。记录格式采用[接续路线 §6](2026-09-09-android-ar06-ar11-execution-roadmap.md)。
