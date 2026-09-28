# CA2-06+12：车辆身份、逐信号观测与有状态仿真

> 状态：首版离线验证、v1 兼容发布与固定语料前测完成；来源签名启用待配置授权。起点 `e120b931`。
> 代码/提交/推送与必要发布已有授权；没有修改 `.env`、Compose、密钥或数据库 schema。
> 不改变数据库 schema，不把模拟状态、接收回执或签名当成真实车辆动作证明。

## 1. 本次要关掉的缺口

- `vehicle.state.changed` 只有 changes/source/trace，多个消费者直接合并全局 dict；Cloud 的整包更新时间会让新电量刷新旧挡位。
- Cloud Gateway 现有 channel token 允许表不绑定 vehicle_id；Edge CloudClient 还会按首个请求改握手车辆。
- Gateway 向全部 WS 客户端广播同一车态；Collector、scene、主动引擎及各业务观察器都需要同时改接线。
- VAL 已有真实的内存状态变化，但缺少可重放的部分信号静默、坏质量、乱序、重启和 ACK 丢失实验。

## 2. 冻结的信任边界

新事件使用版本 2 信封。载荷含 vehicle_id、source_id、source_epoch、epoch_started_at_ms、source_seq、
emitted_at_ms、snapshot 和逐信号 value/observed_at_ms/quality/unit；operation_id 可选，不代替授权。
签名覆盖原始载荷字节，传输用 base64，避免 Python/Go 数字序列化差异造成验签分歧。
使用已有 SDK 依赖的 Ed25519/cryptography，Go 使用标准库；不自制密码算法。

信任表从受控配置给出 key_id → 公钥、车辆、来源、来源类型、优先级、逐信号有效期与通道 token 指纹绑定。
签名正确只证明某个已登记来源发布了这些值；source_kind=simulated 始终保留，不提升为实车。
私钥仅给生产者，读取方只需要公钥。无配置时保留明确绑定 v1 的旧模拟车道，身份标未认证；
坏配置、坏版本、坏签名或不匹配身份拒绝，不能回落旧车道。生产多车启用和 ACL 不冒充已经完成。

逐信号缓存以 vehicle/source 分区，epoch 内 seq 去重，换 epoch 需新的快照和递增的启动时间；
旧 epoch 晚到不能覆盖新状态。来源之间按受控优先级选择，不能比较裸 seq。
新电量不刷新旧挡位；有效期由读取方策略确定，过期/坏质量不进入值投影，保留可解释元数据。
签名时间还要过时钟容差与新鲜度检查，缓存使用单调时钟计算剩余有效期。
模拟车道的兼容有效期不是 OEM 安全时效规范；新的安全写不能凭旁路镜像自行取得执行权。

## 3. 生产者和消费者

| 位置 | 改动与边界 |
|---|---|
| VAL / edge server / observability emitter | 同一模拟状态持有者；发布版本化采样，变更与快照走同一源序列；不复制第二个真实状态 |
| Edge CloudClient / Cloud Gateway | 车辆由服务端绑定；请求不能改握手身份；通道认证与车态来源绑定分别校验 |
| Cloud mirror / Outcome Verifier | 按请求车辆读取，逐信号陈旧为 unknown；观测满足不等于因果证明 |
| scene mirror / solve / verify / triggers | 激活与恢复读取正确车辆；事件回调保留身份，不串其它车辆的运行中场景 |
| proactive / charging / road-safety / info / reminder | 同一入站判据；明确车辆范围和未知值，不能用一个车辆事件驱动另一车辆的提醒或建议 |
| Go Edge Gateway / HMI / mobile | 按已解析的会话车辆分发；过期值从投影移除；保留模拟与质量信息；旧帧仅走明确兼容分支 |
| Collector / dashboard | 车辆维度查询与观测元数据；调试面仍为受限 PoC，不新增操作权限 |

## 4. 有状态仿真

SimulatedVehicleDriver 保持现有 VAL 的初始值和合法命令结果，VAL 仍负责全部指令/确认/安全检查。
故障 harness 使用可注入时钟、种子和独立车辆：部分信号静默、坏质量、乱序/重复、来源重启、
状态已变但 ACK 丢失。记录观测与回执两条事实，ACK 丢失必须报告 unknown，不自动重放命令。
故障只在离线 harness 显式注入，不增加生产远程故障或车控入口；持久幂等/恢复账本仍由 CA2-08–11 承接。

## 5. 验证与发布顺序

1. 共享 Python 校验/缓存与 Go 对照向量，反向验证错车辆、签名篡改、乱序、旧 epoch、未来时钟和局部陈旧。
2. 两车仿真与所有消费者接线；关闭/旧记录/混部边界单独验证，真实身份不能由 fixture 自报。
3. 相关测试、Go、客户端契约、四道门禁与固定全量；不在测试运行期间修改被测工作树。
4. 形成签名私钥生成/注入、公钥信任表与部署透传的具体变更，取得配置授权后再启用。
5. 按精确 SHA 进行健康/签名来源/只读状态与固定语料复验。仿真不冒充 OEM/实车验收。

当前故障与旧 QA 残余仍由 QA 交接维护；本包不重开已闭合的开发批，也不以新增字段代替真实证据。

## 6. 已形成的接入契约

载荷字段为 `vehicle_id/source_id/source_epoch/epoch_started_at_ms/source_seq/emitted_at_ms/snapshot/signals`，
时间使用 Unix 毫秒，seq 为 epoch 内递增正整数。每个 signal 必须带 `key/value/observed_at_ms/quality/unit`；
quality 只允许 good/uncertain/unavailable，读取方派生 stale。单源最多 256 个键，载荷最多 128 KiB。
信封字段为 `version=2/key_id/payload/signature/changes`；payload 为 UTF-8 JSON 原始字节的 base64，
Ed25519 签名内容为 `cockpit.vehicle-state.v2\0` 加载荷字节。外层 changes 在签名模式下为空，不能当权威数据。

- 权威读取为 `store.view(vehicle_id)`，返回完整 `state` 与逐信号 `signals` 元数据。只有 good 且未过期的值进入 state。
- 首个未知 epoch 需要快照；新 epoch 启动时间必须更大。相同样本换 seq 不延长原有效期，单调时钟防本地墙钟回退。
  接收方重启为空，拒绝早于启动时间 5 秒以上的帧；未持久化接收序列，不能声称跨崩溃永久防重放。
- 受控高优先级来源覆盖的字段，在首帧缺席、重启缺项或过期时保持 unknown，不静默采纳低优先级来源。
  覆盖范围由接收策略的 TTL 键表决定；多来源模拟策略用 `*` 会覆盖全部字段，须明确审查。真实/沙箱来源必须逐键声明单位和 TTL。
- scene Ground、快照与 Verify 都传 ctx.vehicle_id；SCENE_ACTIVE 原键只归历史 v1，其它车辆用 `:vehicle:<编码ID>`。
  每车一份权威状态，回滚读写继续一致；无 schema 变更或批量迁移。Verify/触发边沿/节流按车辆区分；时间建议仍为显式 v1 兼容档。
- 主动引擎按 payload.vehicle_id 读取车况、去重、合并；无车辆身份的普通提醒不借用 v1 状态。
- WS 先下发服务端 `session_identity`，再发车辆投影。投影带 `version=2/vehicle_id/projection_epoch/revision/observation/state`，
  HMI/mobile 拒绝串车、旧 revision 和其它 gateway epoch；断连清空值，网关即使没有新 NATS 事件也在过期后推送空缺。
- Collector GET `/api/vehicle/state?vehicle_id=v1` 保留纯值；GET `/api/vehicle/observation?vehicle_id=v1` 提供元数据；
  `/stream?vehicle_id=v1` 分车、序列化读取与发送，过期检查间隔 1 秒。Dashboard 当前 UI 固定 v1，未知值不补 0/P/OFF。
  调试接口仍只属于现有 PoC 模拟环境，不提供新生产故障或实车控制接口。
- 旧无版本帧只适配明确的 v1 模拟来源，版本化信任表启用后拒绝降级；旧客户端和旧服务器混部不宣称有新质量保证。
  整版回滚到旧 WS 时，已绑定新 epoch 的客户端需刷新/重新打开应用，避免静默接受降级帧。

关键验证：`runtime/tests/test_vehicle_state.py`、`gateway/vehiclestate/state_test.go` 共读
`test/fixtures/vehicle_state_vectors.json`；`test/test_vehicle_state_consumers.py` 覆盖消费者入口。
`scripts/probe_vehicle_state_simulation.py` 固定 seed/时钟并输出事件轨迹；其密钥是公开测试夹具，禁止加入运行信任表。
测试同时记录命令回执 unknown 和观察到的值变化，不能把值匹配改称因果验证。

## 7. 签名启用配置审查

状态：待授权。

代码/提交/推送/必要部署已有用户授权；以下运行配置依照 AGENTS.md §3.2 **单独申请**，尚未执行。
具体范围：

| 目标 | 待执行变更 |
|---|---|
| `/opt/car-agent/shared/.env`（release 根 .env 的现有链接目标） | 仅新增 VEHICLE_STATE_TRUST、VEHICLE_STATE_KEY_ID、VEHICLE_STATE_PRIVATE_KEY；保留其它配置及 0600 权限 |
| 新来源身份 | key_id=`val-simulator-v1-20260927`，vehicle_id=`v1`，source_id=`val-simulator`，kind=`simulated`，priority=100 |
| 接收策略 | ttl_ms：`*`/battery=180000，speed_kmh/gear/location=90000；单位 speed_kmh=km/h、battery/volume=%、hvac_temp/cabin_temp=degC |
| 通道绑定 | 对已有 CLOUD_CHANNEL_TOKEN 计算带 domain 的 SHA-256，登记至上述 v1 来源；不更换原通道凭据 |
| 新密钥 | 授权后生成 Ed25519 随机密钥，仅写云端 shared .env；私钥不输出、不进源码、证据或本地根 .env |
| `deploy/docker-compose.yaml` | x-python-env、collector、proactive、两个 gateway、edge-orchestrator 透传公共 VEHICLE_STATE_TRUST；仅 edge-orchestrator 额外获得 KEY_ID/PRIVATE_KEY |
| `/opt/car-agent/shared/release-infrastructure.json` | 复核现有部署脚本/资产批准摘要；该摘要只覆盖 deploy/cloud，Compose 透传不改变它时保留原文件，不做无必要的写入 |

新增 Compose 公共行是 `VEHICLE_STATE_TRUST: ${VEHICLE_STATE_TRUST:-}`；edge 私有行分别为
`VEHICLE_STATE_KEY_ID: ${VEHICLE_STATE_KEY_ID:-}`、`VEHICLE_STATE_PRIVATE_KEY: ${VEHICLE_STATE_PRIVATE_KEY:-}`。
不修改 `.env.example` 或 CI/CD。该 TTL 是 30 秒快照周期下的模拟接收策略，不能冒充 OEM 安全信号规范。

启用前复核云端 VEHICLE_ID=v1、现有通道 token 非空且属于允许集；已有新键时停止并比对，不覆盖未知配置。
私钥只在已批准的配置写入阶段生成；公钥/策略/指纹可公开验证，测试私钥永不部署。
同一 release 接齐所有消费者后验证签名来源、身份拒绝、只读状态及固定语料。
整版回滚可继续使用旧代码/旧 Compose；新配置键在旧代码中不生效，保留密钥供审计与再次升级，不临时删除。

## 8. 验证登记

### 8.1 实现与离线验证

代码提交：`8b420da7`（主体）、`6fd47994`（串车拒绝不携带行驶事实）、`b98c9b60`（移动展示断言）、
`89b19956bea2378d492cedff78fad75b8f814efa`（登记来源的优先级在缺席/重启时保持）。已推送 main。

| 验证面 | 绑定版本 / 结果 |
|---|---|
| 后端全量 | `89b19956`，10148 passed / 34 skipped / 9 warnings，4727.69 s，UTC0 / `-n 8 --dist worksteal` |
| 跳过与警告 | Windows/POSIX、未起的本地 Redis/ASR、真实 LLM、OTel 等环境跳过；工作树另缺 NLU vocab 两项，所以是 34 而非主仓历史 32；Starlette 弃用与 reminder 的既有 AsyncMock 未 await 警告保留 |
| 端侧与四门禁 | `89b19956` 全过；smoke 13；L0 strict discovery 85/85、gate 25/25；skills 反例噪声 1/8 与 exemplars 3 miss 仍按原门槛记录 |
| Python/Go 观测契约 | 共享 29 组 wire 场景；含篡改、错绑定、乱序、epoch、单调过期与主来源缺席；Go 五包通过 |
| 有状态实验 | `89b19956-simulation-seed12.json`，16/16；ACK 丢失报告 unknown，模拟状态可已变化，不重发同一操作；不证明持久幂等或实车因果 |
| HMI / Dashboard | `89b19956`：HMI 358；Dashboard 19；两者生产构建通过。HMI tsc 与 `e120b931` 均 25 项旧错误、零新增，不报类型全绿 |
| Android 代码 | `b98c9b60`：112 suites / 1159 tests，tsc/lint 通过；mobile tree OID `d99789915afaaf36080e28e9367ce13d2f648d59` 与 `89b19956` 一致；未重新构建/验收 APK |

两次全量中断与早期失败不当验收证据。旧镜像接口桩按车辆参数修正；Windows PowerShell 5.1
子进程继承 PS7 的模块路径导致真实 Get-FileHash 缺席，只隔离测试子进程环境，13 项 wrapper 测试过，真实脚本不变。
Jest 程序化启动缺 NODE_ENV=test 曾导致手势库误判；最终使用标准 CLI、仅覆盖 Windows 工作树的等价 testMatch，未改应用判据。
完整证据索引为根仓 `.artifacts/vehicle-state-v2/89b19956-local-manifest.json`，不把旧失败/中断日志归给最终候选。

### 8.2 兼容车道真栈

`89b19956` 已部署；26/26 镜像完成，独立 status 的发布/运行 SHA 一致、5/5 healthy、零 warning，
统一 verify `verified`，`20260928T020451Z-89b1995.json`，`e2e_remote_safe` / `minimax:MiniMax-M3`。
发布前磁盘约 35.4 GiB，完成后约 31.0 GiB；`89b19956` 这次发布过程没有清理路径/缓存，也未调用构建历史 API。
后续聚合修复的容量处置与新 release 单独记录在 [修复 §5](2026-09-28-result-speech-fidelity.md#5-发布与真栈逐条复核)。

只读专项 `.artifacts/vehicle-state-v2/89b19956-live-readonly-retry.json` **15/15**：27 个信号元数据、26 个有效值，
位置为 unavailable；HTTP/WS 同车投影一致，未绑定车辆的投影为空，实际 RPC→WS 返回 permission_denied 且没有 driving 字段。
审计记录为 vehicle_identity_rejected、rejected、0 动作；前后车态 SHA-256 同为
`73858714ad02ccf345e0dd08357ae20cfda62bfe1571d8d8f1affddcbad48131`，首尾 release 连续。
首趟探针 session 缺编号被既有格式闸关闭；修正实验会话为 `<user>-session-1` 后重跑，原失败 artifact 保留。

**连接身份认证与观测来源认证分开**：上述连接使用既有 signed E2E，车态仍是显式 v1 模拟兼容来源，
`authenticated=false/source_kind=simulated`。线上 Ed25519 来源签名尚未启用，不能据这些读数宣称签名上线。
浏览器控制连接未能建立，未完成实际渲染或设备复验；构建/单测不能替代它们。

### 8.3 固定语料复验

同一 release/runner `89b19956` 完成 60/60 case run、101 测量轮；228 条已记录模型调用均 pinned
`minimax:MiniMax-M3`。零动作、零车态差异、零证据错误与残留挂起，首尾版本连续，runner 未变。
原始 JSON SHA-256：`8d3b81fb2dcb57c8d678714819ebf81929676ad59ae65fc0def32860aaf9d271`。

原始判红 6 轮；逐条复核为 **5 个业务问题 + 1 个关键词误报**，不改写原报告：

| 样本 / trace | 复核结论 |
|---|---|
| V201 r2 t3 / `e8636a04bcbd42f48bcd3646544044bd` | Planner 正确派手册，目录模型给 off_topic/空章节，词法也无结果；真实空检索 |
| V207 r2 / `d111d84b0c5e4b6fabf66b82d84f3606` | 正确解释车内空调、通风和休息用途，未重复“露营”；关键词误报，零动作/确认 |
| V216 r2 t2 / `c8806031412e4732b19fc7ebea205d98` | 自动模式后续问句落到闲聊，通用解释有内容，但不满足本车手册连续性合同 |
| V201 r3 t1 / `00e03cd628fd4719bb1d68018f1d9a0c` | 两步均已规划，手册已开始检索，随后 15 s 超时；ResultBundle 保留 unknown/unavailable。raw“未派发”按完成 span 判断，不能据此说根本没调用 |
| V210 r3 / `58fe5db2116448048021b22508350ffc` | 空计划重试后仍未派手册，闲聊仅提示语音控制说法；真实落域缺口 |
| V211 r3 / `93ca23cedb514784a3210bd95e1d039d` | 手册正确区分推荐 2.9 bar 和阈值 2.3 bar，聚合将推荐值改成 2.3；必须优先修复的数值角色错误 |

混合请求首轮手册派发/呈现为 8/9；超时样本不能用前版的 9/9 覆盖。
数值错误另按 [原文保护修复](2026-09-28-result-speech-fidelity.md)处理，其他残余继续进入原 QA/手册入口；
CA2-06/12 验证不等于整体 QA 全绿。原文件与复核保留在 `.artifacts/vehicle-state-v2/89b19956-baseline*.json`。
当前发布和修复后证据统一见 [QA 交接 §2](../reviews/2026-08-30-qa-closeout-handoff.md#2-当前发布与证据边界)。
