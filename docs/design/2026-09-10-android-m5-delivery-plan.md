# Android M5（AM5）交付计划 — 规划主体

> 状态：**规划主体，2026-09-10 落盘；未启动任何生产化实现，未取得任何对外发布授权。**
> 对应 [AR11 方案](2026-09-09-ar11-m5-delivery-roadmap-plan.md) 的 A11-1～A11-4；A11-5 定稿需回填
> [AR10](2026-09-09-ar10-fixed-release-ux-acceptance-plan.md) 的实际验收结论，本文用「待 AR10 校准」标出。
> 代码基线：本文引用的每一条实现事实都在 `7481cb5`（含 AR06/AR07/AR08 工程批）上读过，
> 引用处给出文件路径；没读到的一律写「未核实」，不按印象填。
> 命名：本文的 **Android M5 = 移动端对外交付阶段**，不是项目已落地的 M5 数据飞轮；工作包统一 `AM5-*`。

## 1. 这份文档解决什么

AR11 方案已经把生产化拆成五个包。它缺的是**能直接派工的那一层**：每项任务的输入、输出、前置、
代码落点、DoD、测试、授权边界与工作量依据，以及发布/迁移/回退的可审查步骤。本文补这一层。

**完成本文不代表任何生产能力已实现。** 登录、厂商推送、正式签名、上架一件都没做，也不该被读成做了。

## 2. 已核实的当前事实（每条都指向读过的代码）

| 面 | 现状（已核实） | 缺口 |
|---|---|---|
| 连接配置 | [`core/config/types.ts`](../../mobile/src/core/config/types.ts)：`{preset, fqdn?, edgeUrl, audioUrl, token}`；[`storage.ts`](../../mobile/src/core/config/storage.ts) 把 `token` 存 `expo-secure-store`（key `xiaozhou.server-token.v1`），其余进 AsyncStorage（`xiaozhou.server-config.v1`），任一缺失按未配置 fail-closed | 没有登录、没有续期/吊销、没有设备注册、没有账号切换语义 |
| 身份来源 | [`gateway/edge/auth.go`](../../gateway/edge/auth.go)：`AUTH_TOKENS` 静态表，条目 `;` 分隔、每条 **`token:user_id:vehicle_id:scope-csv` 四段**，畸形条目**拒绝启动**；`granted_scopes` 只由 token 注入，客户端伪造值先剥后写 | 静态长期 token = 没有生命周期。连接中吊销、设备解绑、token 轮换都没有链路 |
| 权限判据 | [`security/session_scopes.py`](../../security/session_scopes.py)：解析 `granted_scopes` 的**唯一**入口，返回 `source ∈ {token, poc_default, fail_closed}`；`POC_DEFAULT_SCOPES` 是 PoC 兜底全开集 | 量产必须 `PERMISSIONS_FAIL_OPEN=false`，且每个执行出口都要真的读它（AR05 F07 的原账：端侧 T0 快路径曾经从没读过） |
| 主动投递 | [`gateway/edge/main.go`](../../gateway/edge/main.go)：下行帧带 `delivery_id`/`delivery_ids`/`priority`；上行 `proactive_ack` 按 `delivery_ids` 回执 | 只有**前台 WS**。App 不在前台就没有任何触达通道 |
| 包与签名 | [`app.config.ts`](../../mobile/app.config.ts)：`package=com.xiaozhou.companion`、`version=0.1.0`、`scheme=xiaozhou`、CNG（`android/` 不入库）、`experiments.typedRoutes+reactCompiler`；[`build_mobile.ps1`](../../scripts/build_mobile.ps1) 用**模板 debug.keystore** 签 release，产物落 `D:\Android\builds\apk\` | 没有正式签名、没有 versionCode 递增规则、没有更新与回退链路 |
| 隐私/SDK | [`app/map.tsx`](../../mobile/src/app/map.tsx)：高德 `AMapSdk.init(AMAP_KEY)` 是**进程级一次**且**硬编码同意**（AR06 已把守卫从组件 ref 搬到模块级，同意语义未变）；AR02 的视觉单帧只在内存 | 「真正同意之前不初始化需同意的 SDK」尚未成立；数据清单未逐项落盘 |
| 观测 | [`core/obs/turnTimeline.ts`](../../mobile/src/core/obs/turnTimeline.ts) + [`latencyStats.ts`](../../mobile/src/core/obs/latencyStats.ts)（AR08 新增，有界只读、事件带时钟域）、[`presenceTrail`](../../mobile/src/core/presence/presenceTrail.ts)、`SpeechController.turnReports()`、`/turn-timeline` 只读页 | 没有崩溃/ANR 采集，没有上行遥测，没有按版本/设备分层 |

**未核实、后续必须现场读的：** 云端 `.env` 实际 `AUTH_TOKENS` 条目数与 scope 分布、
高德控制台已登记的签名指纹、目标设备的厂商推送可用性。本文不替它们填值。

## 3. 五个包的可派工任务

每项写：**输入 → 输出 / 前置 / 代码面 / DoD / 测试 / 授权边界 / 工作量依据**。
工作量单位是「有效工程日」（1 人专注 1 天），**外部等待单列**，两者不相加。
估算依据只写可数的东西（要改的出口个数、要写的用例个数、已有同形态实现的耗时），不写拍脑袋的总天数。

### AM5-01 账号、设备身份与可信权限

> 责任：身份/后端主责；mobile 接入；安全复核鉴权覆盖；QA 做跨身份反例。

| ID | 任务 | 输入 → 输出 | 前置 | 代码面 | DoD | 工作量依据 |
|---|---|---|---|---|---|---|
| 01-1 | 冻结身份契约 | 现有 `AUTH_TOKENS` 四段语义 → `IdentityContract` 文档：`account_id / user_id / device_id / vehicle_binding` 关系、token 期限/续期/吊销、权限更新传播时限 | 无 | 文档 + `security/` 注释 | 四个 id 的关系图无歧义；每个 id 的**发放方**与**失效方**各只有一个 | 1–2 日：只写契约，不实现 |
| 01-2 | 鉴权覆盖盘点 | 全部客户端入口 → 覆盖表（入口 × 是否校验 token × 是否读 `session_scopes`） | 01-1 | `gateway/edge/`、`gateway/cloud/`、`llm-gateway/`、`security/` | 主 WS、会话 HTTP、ASR/TTS/S2S、视觉、业务恢复**逐个**有结论；缺口逐条列 | 0.5–1 日：入口数可数（现已知 ≥6 类），逐个读一遍 |
| 01-3 | 登录接入（浏览器授权码 + PKCE） | IdP 决策（§5 D1）→ 登录/退出/续期/离线恢复 | 01-1、D1 已定 | `mobile/src/core/config/`、`core/api/`、引导页 | 六条真链路用例：登录/取消/续期/过期/吊销/设备解绑 | 3–5 日：一套 OAuth 客户端 + 6 条用例；**外部等待**：IdP 租户开通 |
| 01-4 | 账号/服务器切换的销毁边界 | 切换事件 → 旧会话音频停、挂起回复清、异步准备撤销、摘要清空 | 01-3 | `core/session/wiring.ts` 的 `ensureWired/disposeWired`、`AssistantProvider` | 切换后旧账号的摘要/挂起/音频**零残留**；AR06 已把摘要清空提前到渲染期（切换后不再有一帧旧身份） | 1–2 日：销毁点已存在，补的是覆盖与用例 |
| 01-5 | 连接中吊销 | 服务端吊销 → 失效界限 + 主动阻止后续写 | 01-2 | `gateway/edge/`、`security/` | 吊销后**服务端**拒绝；客户端关入口只算体验，不算判据 | 2–3 日：要动网关会话生命周期 |
| 01-6 | 内部长效 token 迁移 | 现有内部用户 → 受控过渡方案 | 01-3 | 运维 + 文档 | 不静默改 `.env`、不批量切生产权限；每个被迁移身份可回滚 | 1 日方案 + **外部等待**：用户确认迁移窗口 |

**接口草案（AM5-01）**

```text
GET  /api/session            # 已存在（AR05）：返回身份与能力摘要
POST /auth/device/register   # 拟新增：{device_id, platform, app_build} → {device_token, expires_at}
POST /auth/token/refresh     # 拟新增：{refresh_token} → {access_token, expires_at, scopes}
POST /auth/token/revoke      # 拟新增：{device_id | session_id}
```

`granted_scopes` 仍**只由服务端注入**，客户端任何自报值继续先剥后写（`auth.go::stampScopes` 的现有语义不动）。

### AM5-02 公网连接、设备投递与恢复

> 责任：平台/网关主责；提醒/主动服务与 mobile 协作；QA 负责离线、多端与去重。

| ID | 任务 | 输入 → 输出 | 前置 | 代码面 | DoD | 工作量依据 |
|---|---|---|---|---|---|---|
| 02-1 | 公网入口冻结 | 现 Tailnet 形态 → 域名/TLS/认证/限流/超时/健康检查/降级清单 | D0 范围已定 | `deploy/`、`gateway/edge/` | 每项有值、有回退步骤；**先出运维变更与验证/回退步骤，再申请基础设施授权** | 2–3 日；**外部等待**：域名与证书 |
| 02-2 | 设备注册与推送 token 生命周期 | 01-1 的 `device_id` → 注册/更新/失效/退出登录/注销可追溯 | 01-1、02-1 | `gateway/edge/`、拟新增通知桥 | provider token **不得**当用户 id 使用；轮换与重装后仍能定位同一设备 | 2–3 日 |
| 02-3 | 投递合同 | 现有 `delivery_id` → `delivery_id` + 设备维 attempt + TTL + 去重/重试上限 + 清理 | 02-2 | `gateway/edge/main.go`、`agents/reminder/`、`proactive/` | 同一 delivery 不重复业务、不重复播报；过期通知不复活旧操作 | 2–3 日：现有 `delivery_ids` 幂等已存在，补的是设备维与 TTL |
| 02-4 | 状态分层 | 单一「已投递」→ accepted / provider_delivered / app_received / notification_posted / **presented** / handled | 02-3 | `proactive/`、mobile 呈现层 | **`proactive_ack` 不因 SDK 返回成功提前发**；默认仍保持既有「App presented」语义，改语义要新的产品合同 | 1–2 日 |
| 02-5 | 离线与多端矩阵 | 02-3/02-4 → 双端在线/离线、重复投递、token 轮换、重装、被系统终止、过 TTL | 02-4 | QA | 每格有原始 delivery 证据；**不承诺所有 OEM 强停后可靠自启** | 3–4 日执行 |

**投递状态机（草案）**

```text
accepted ──▶ provider_delivered? ──▶ app_received ──▶ notification_posted ──▶ presented ──▶ handled
   │                                        │                                    ▲
   └──▶ expired(TTL)                        └──▶ dropped(dedup / 旧账号 / 已 handled)
（provider_delivered 只有在渠道能给回执时才有；拿不到就写 NOT_AVAILABLE，不猜）
```

### AM5-03 正式签名、版本与更新

> 责任：发布/平台主责；mobile 负责 CNG/兼容；产品定渠道；QA 验升级与回退。

| ID | 任务 | 输入 → 输出 | 前置 | 代码面 | DoD | 工作量依据 |
|---|---|---|---|---|---|---|
| 03-1 | 正式签名与版本规则 | 当前 debug.keystore 模板签名 → applicationId / 签名所有权与保管 / versionCode 单调递增 / 版本命名 | D4 渠道已定 | `app.config.ts`、`build_mobile.ps1` | 敏感签名材料不进仓库/日志；开发 / 灰度 / 正式是否分 package 有明确裁决 | 1–2 日 + **外部等待**：密钥保管方案 |
| 03-2 | 旧内部包的过渡 | 现装机身份 `com.xiaozhou.companion`（debug 签名）→ 覆盖升级可行性结论 | 03-1 | 装机验证 | **默认视为不能直接覆盖**；给出「保留旧内部包」或「新包迁入」的具体选择与数据安排；不自动卸载、不清数据 | 0.5 日核实 + 1 日方案 |
| 03-3 | 高德指纹登记 | 03-1 的正式指纹 → 高德后台登记 → 装机验证地图可用 | 03-1 | 无代码 | 正式包地图可用；**不复制维护生成的 `android/`** | 0.5 日 + **外部等待**：高德后台操作 |
| 03-4 | 更新链路 | 03-1 → 更新包校验 / 分批放量 / 网络中断 / 低存储 / 下载失败 / 安装取消恢复 | 03-1 | 发布流水线 | 数据格式前后兼容有结论；**回退 = 发布更高 versionCode 的已知好实现**，不假设可安装低版本 | 3–4 日 |
| 03-5 | OTA 评估（独立决策） | 03-4 可靠之后 → runtimeVersion/原生指纹/更新签名/渠道/启动失败恢复 | 03-4 | 拟新增更新服务 | **新原生依赖必须新 APK**，不热更给缺模块的旧包 | 评估 1 日；实施另立 |

**已核实的构建身份链（03 的验收基线，AR06 本轮实跑过）**：本地 APK SHA-256 = 设备
`/data/app/.../base.apk` 的 `sha256sum`；`dumpsys package` 的 `flags` 不含 `DEBUGGABLE`；
设置页 `build-label` 读出 `v<版本> · <variant> · <sha9> · <时间>`，出现 `Metro` 即说明 JS 来自开发服务器。
这四条已经是可复用的验包判据，03 只需把「签名指纹」一条加进去。

### AM5-04 隐私、SDK 初始化与系统交互

> 责任：产品/隐私角色定义承诺，mobile/后端落实；安全与 QA 验撤销与数据路径。
> 正式法律文本与渠道要求由相应责任人审核，本文不宣称合规认证。

| ID | 任务 | 输入 → 输出 | 前置 | 代码面 | DoD | 工作量依据 |
|---|---|---|---|---|---|---|
| 04-1 | 数据清单 | 现有采集面 → 逐项：目的/触发/处理方/存储/TTL/关闭/导出/删除/失败恢复 | 无 | 文档 | 麦克风原始音频、ASR 文本、S2S 音频、单帧图像、位置、记忆画像、诊断、推送地址**逐项**有行 | 2 日：8 类数据 × 9 个字段 |
| 04-2 | SDK 同意闸 | `map.tsx` 的硬编码同意 → 真正同意前不初始化 | 04-1 | `app/map.tsx`（现为模块级 `ensureAmapInit`，改同意闸只需在它前面加判据） | 拒绝后仍有可用的非地图流程；**不能只加声明 UI 而库内照旧初始化** | 1–2 日 |
| 04-3 | 撤销与零上传 | AR02 内存视觉 + AR04 后台/锁屏撤回 → 撤销权限或关闭功能后新上传为零 | 04-1 | 采集/上传出口 | 有反例证据；改留存承诺要先给数据路径与理由，**不无声改「零落盘」** | 1–2 日 |
| 04-4 | 锁屏与账号隔离 | 通知内容 → 默认脱敏；解锁/换账号后重新鉴权取数据 | AM5-02 | 通知桥 | 过期通知打不开他人内容；**不强制开通知才能用前台功能** | 1–2 日 |
| 04-5 | 系统入口逐项裁决 | AR11 §3 的表 → 首发只保留 Shortcuts；QS Tile / Live Updates / Widgets / 默认助理各留独立进入条件 | D5 | `plugins/with-shortcuts` 已存在 | 未采用的能力**从承诺里明确移除**，不是留着当模糊承诺 | 0.5 日 |

### AM5-05 观测、性能准入与试用反馈

> 责任：平台观测/QA 主责，mobile 提供崩溃与性能信号，产品负责优先级与支持承诺。

| ID | 任务 | 输入 → 输出 | 前置 | 代码面 | DoD | 工作量依据 |
|---|---|---|---|---|---|---|
| 05-1 | 遥测字段合同 | AR08/AR09 已有字段 → 上行摘要白名单 | AR09 定稿 | `core/obs/`、`observability/collector/` | 原始音视频/凭证/完整敏感请求**不进遥测**；未知写 unknown 不写默认值 | 1–2 日：字段已在 `turnTimeline` 里成形，补的是上行白名单 |
| 05-2 | 崩溃/ANR 接入 | 采集 SDK 决策（§5 D6）→ 符号化 + 版本/设备/网络分层 | 05-1 | 平台采集适配 | 至少一条**可控**崩溃在非生产环境贯通到符号化 | 2–3 日 + **外部等待**：SDK 选型 |
| 05-3 | 放量准入表 | AR07/08/09/10 采用指标 → 准入门槛 + 真实内存/耗电/长期前后台要求 | AR10 结论 | 文档 | **短期 0 crash 不算低事故率**：必须带会话数、用户时长、观察窗口 | 1 日（待 AR10 校准） |
| 05-4 | badcase 与反馈闭环 | 05-1 → 入口 + trace 关联 + 脱敏 + 分派/复现/修复/回归 | 05-1 | dashboard | 用户授权的反馈资料与自动指标**分开** | 2 日 |
| 05-5 | 异常响应与回退条件 | 05-3 → 确认/权限/隐私破坏立即停扩；普通性能指标按预定窗口 | 05-3 | 文档 | 执行暂停/回退仍走实际发布责任与授权 | 0.5 日 |

## 4. 依赖、顺序与并行边界

```text
D0 产品边界（AR10 结论 / 试用人群 / 渠道 / 设备 / 触达承诺 / 数据范围）
      │
      ├─▶ D1 契约层：AM5-01(1,2) 身份契约与覆盖盘点 ‖ AM5-04(1) 数据清单
      │        │
      │        ├─▶ D2 发布骨架：AM5-03(1,2,3) 签名/版本/指纹
      │        └─▶ AM5-02(1,2) 公网入口与设备注册
      │                 │
      │                 ├─▶ D3 端到端：AM5-01(3,4,5) 登录与销毁边界
      │                 │            AM5-02(3,4) 投递合同与状态分层
      │                 │            AM5-04(2,3,4) 同意闸与撤销
      │                 │            AM5-05(1,2) 遥测与崩溃
      │                 └─▶ AM5-03(4) 更新链路
      │                          │
      └──────────────────────────┴─▶ D4 放量裁决（全部必选 DoD + 实际支持矩阵 + 回退演练）
```

**可并行**：契约设计、测试材料、本地实现按文件分工。
**必须串行裁决**（一个集成负责人）：共享 schema/网关、CI、云端发布、构建镜像、手机占用。
不要让多个执行体同时改身份与配置源。

**无循环依赖检查**：D1 不依赖 D2/D3 的任何产出；AM5-03 不依赖 AM5-02；AM5-05 只消费其他包的字段，不反向约束它们。

## 5. 开工前必须关闭的决策

| 编号 | 决策 | 本文建议默认 | 谁定 | 不定的影响 |
|---|---|---|---|---|
| D1 | 身份来源 | 复用已有/标准 IdP，系统浏览器 PKCE（[RFC 8252](https://www.rfc-editor.org/rfc/rfc8252)） | 身份/产品 | AM5-01-3 无法开工；user_id 映射与记忆归属悬空 |
| D2 | 首发范围 | 受控邀请 + AR10 已证设备/系统范围 | 产品 | 设备矩阵与推送投入无法定；不得自行宣布公开可用 |
| D3 | 后台触达 | 通知 + 按需恢复，前台仍 WS | 产品定「哪些提醒必须通知」；平台选渠道 | AM5-02 全包悬空 |
| D4 | 签名/包名/渠道 | 正式签名，内部包明确过渡；先 APK 更新 | 发布角色 | AM5-03 全包悬空；不能假设原包可覆盖升级 |
| D5 | 系统入口 | 除 Shortcuts 外按具体任务后排 | 产品 | 不打包强做 Tile/Live Updates/Widgets |
| D6 | 数据/遥测 | 最小摘要、用户可控、限定留存 | 隐私/产品 | AM5-05-2 选型悬空 |

这些是**进入条件**，不是本轮要做的事。本轮不需要真实密钥、不开云资源、不向他人发消息。

## 6. 发布、迁移与回退（可审查步骤，均未执行）

### 6.1 发布前置闸（沿用现有纪律）

1. 工作树 clean、已提交、`main` 可达 SHA；
2. 先 dry-run，再单独授权 `--apply`；
3. deploy 不自动 commit/merge/push；push 前列出完整 `origin/main..HEAD` 并单独授权；
4. 发布后独立 `status` / `verify`，并在该 SHA 上做必要复验——**「已提交部署任务」不是发布成功**。

### 6.2 客户端灰度

| 步 | 动作 | 判据 | 回退 |
|---|---|---|---|
| G0 | 冻结候选：源 SHA / APK SHA-256 / 签名指纹 / versionCode / 内嵌 build / 云端 release | 四项本地与设备两侧读数一致 | — |
| G1 | 内部设备安装 | 设备 `sha256sum` == 本地；非 DEBUGGABLE；`build-label` 无 Metro | 卸载重装上一候选 |
| G2 | 受控邀请放量 | AM5-05-3 准入表全绿 | 发布更高 versionCode 的已知好实现 |
| G3 | 扩范围 | 观察窗内无确认/权限/隐私破坏 | 同 G2，并停止扩大 |

**回退不是安装低版本**：Android 默认拒绝降级安装，回退的可执行形态是「把已知好实现重新打成更高 versionCode 再发」。这条要在 03-4 的用例里真的演练一次。

### 6.3 数据与配置迁移

- 三存储迁移只用 `scripts/cloud_data_migration.py`；final 前取得停写授权；replace 不 merge。
- 身份迁移（01-6）不静默改 `.env`、不批量切生产权限；每个被迁移身份可回滚。
- 客户端本地状态（`xiaozhou.server-config.v1` / SecureStore token）在换包或换账号时的处置写进 03-2 与 01-4，**不自动清数据**。

## 7. 本文没有做的事（防止被读成已完成）

- 没有实现登录、推送、正式签名、更新服务、遥测上行中的任何一项；
- 没有选定 IdP、推送渠道、崩溃采集 SDK，也没有联系任何供应商；
- 没有给出「总共 N 天上线」这种伪精确总数——每项估算只写依据，外部等待单列；
- AR10 的实际结论没有回填，§5 的 D2 与 §3 的 AM5-05-3 因此仍是待校准项；
- 五个包**都还没有被授权开工**。本文是可派工的计划，不是开工许可。

## 8. 定稿条件（A11-5）

1. AR10 出具实际验收报告，回填 D2（首发范围）与 05-3（放量准入）；
2. §5 的六项决策各有责任角色与结论；
3. 每个包标注「已可开工 / 待外部条件」，待外部条件的写清具体缺什么；
4. 工程工作量与外部等待仍分栏，不合并成一个日历承诺。
