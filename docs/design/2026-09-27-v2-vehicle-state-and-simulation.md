# CA2-06+12：车辆身份、逐信号观测与有状态仿真

> 状态：首版离线验证、v1 兼容发布与固定语料前测完成；2026-09-28 来源签名已获授权、启用并完成本包真栈复验；整体 QA 仍非全绿，见 §8.4。起点 `e120b931`。
> 代码/提交/推送与必要发布已有授权；签名三键及 Compose 透传的具体范围见 §7，数据库 schema 不变。
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

状态：2026-09-28 用户在本节具体配置范围呈现后指示“继续”，按该范围执行；三键已登记并经部署生效；签名、隔离及固定语料证据见 §8.4。

代码/提交/推送/必要部署已有用户授权；以下运行配置依照 AGENTS.md §3.2 单独列明并取得上述接续授权。
具体范围（不扩展至其它环境键、CI/CD 或系统配置）：

| 目标 | 批准变更 |
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

登记证据：云端原文件字节前缀、root/0600 与基础设施批准摘要均保持；仅追加三键，私钥未导出。
公钥 SHA-256 为 `209dae8574fb76737558d258581ec8e8f6cbf8dec515186d682741d4b0d4e74a`；
现场内存中的签名/验签往返通过，没有发布测试车态。构建源码的 `.env` 由 remote-build 创建为空文件，
不向构建上下文注入运行密钥。Compose 接线反向测试覆盖缺公钥、私钥扩散及 service env_file 泄漏。

可重复只读验收：`python scripts/probe_vehicle_state_live.py --expected-sha <40位发布SHA> --expected-authentication signed --output <新证据路径>`。
同一探针可显式选择 `unsigned-simulator` 复核旧版；两种证据不能互借。探针只查询观测、建立身份连接及向
未绑定车辆发送普通问候以验证拒绝，无车控命令、确认或 NATS 写入。

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
`authenticated=false/source_kind=simulated`。这个 `89b19956` 兼容版本尚未启用来源签名；不能据旧读数宣称签名上线，后续签名证据单列 §8.4。
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


### 8.4 签名车道启用与真栈复验（2026-09-28）

本批 release、全量测试与专项 runner 均为 `7b346c908e67e9bfe79d8c769f43a5895e8612a5`；
代码变更 `d72be998`，合并已发布的容量治理方案后冻结为本批 SHA。后续容量治理提交与文档提交不借用这份测试数。
当前 status、verify、本地全量与 CI 数字统一见 [QA 交接 §2](../reviews/2026-08-30-qa-closeout-handoff.md#2-当前发布与证据边界)。
本批构建 26/26 镜像，未做额外清理；预检约 65.5 GiB，构建后约 59.75 GiB；没有调用构建历史 API。

| 签名验收面 | 实际证据 |
|---|---|
| 配置范围 | 仅登记 §7 三键；公钥策略在 23 个服务可见，11 个必要消费者逐项一致；KEY_ID/PRIVATE_KEY 只给 edge-orchestrator |
| 运行身份 | 26 个 release 镜像均为本批 SHA；30 个运行镜像的 image Env 均没有烘入签名材料 |
| 实际消息 | 只读订阅 `vehicle.state.changed`，收到真正的周期快照；独立 Ed25519 验签通过。使用收到的字节在独立内存中验证篡改和重放拒绝，未向 NATS 发布测试消息 |
| 消费与隔离 | 仓库探针 `scripts/probe_vehicle_state_live.py`，signed 档 16/16：HTTP/WS 来源、新鲜度、同车投影一致；未绑定车没有其它车事实，RPC 拒绝不携带 driving/动作，审计为 rejected/0 动作 |
| 状态边界 | 27 个信号元数据、26 个有效值；位置 unavailable。前后状态摘要相同，source_kind 始终 simulated；没有实车、OEM 或动作因果证明 |
| 客户端边界 | 本批未重新做浏览器渲染、APK/设备或声学验收；CI 构建/测试与运行证据分开 |

统一 verify 首趟的 E2E 为 7/8：长期记忆问句被 Planner 转成无必要澄清，trace
`remote-mem-fe8fc53bd31544d0a820842fff2d4cd0`，未派业务 Agent。底层发布检查独立通过后，
同一 release 的统一 verify 再跑通过；没有因此标记该规划方差已修。
最初 HTTP/WS 探针与 verify 并发，被发布锁前置检查挡住，未进入业务请求；等待锁释放后串行 16/16。
两次失败工件均保留。主仓并发改动时误启动的首趟全量已中止，正式全量在干净隔离树重跑，不使用中止读数。

固定语料维持原 corpus hash，20 组 ×3：**60/60 case run、103 测量轮（含取消）**；
已记录 212 条模型调用，均 pinned `minimax:MiniMax-M3`。零动作、26 键车态零差异、零 evidence failure、
零 open operation；首尾 release 连续，runner 未变。混合请求首轮手册呈现 **8/9**，失败来自下表的已派发超时。
V211/V220 的 6 个本轮回答保留完整胎压来源条件；这不是前版六句 18 轮或图标专项的重跑。

原始自动判红 **6 轮**，不修改原报告。逐条 trace 复核：

| 样本 / trace | 本轮观察到的原因；均未据此关闭旧缺陷 |
|---|---|
| V202 r1 t1 / `fda95d61744049b2bc57dff4a7c28966` | 两步都在计划中，手册已经开始词法检索，随后 15 s 超时；ResultBundle 为 unknown/unavailable。raw“未派发”是缺完成 span，不能照字面归因 |
| V210 r1 / `498117cc35ba41de928686e319890063` | 首次空计划重试后落 chitchat；泛答空调温度设置，没有手册结果 |
| V201 r2 t3 / `80dce26eef6d40d08013650655a6a177` | history=found，手册已派发；目录模型 DEADLINE_EXCEEDED，raw 词法回落为空，取消后的回忆问题未完成 |
| V209 r2 / `4ecb6598a11641d79201ae9b297e8ddf` | 明确的手册说明问句被转成澄清，没有回答 |
| V216 r3 t2 / `fb9a9e07a8a7430c84bec64122d03847` | 两次原生 tool call 都把 steps 写成 dict 而非 list，落 planner_technical_failure |
| V217 r3 t2 / `f10817180ad2481894c51f73e88f5546` | 首次 Provider HTTP 529，重试 addressed=false；“改问广州”未答，不是关键词误报 |

人工逐轮复核另发现 **4 轮原问题未完成**，自动判据没有报红，不能用 raw 通过数覆盖：

| 样本 / trace | 补充观察 |
|---|---|
| V201 r1 t3 / `98ab4c169fee48c4beb4a5a5c3cc5c2f` | 反问“空调有哪些模式是指哪种”，只重复关键词，没有回忆答案 |
| V207 r1 / `8f2c24fec862478bb15ded71833d3d7b` | 明确问含义，却追问要解释还是开启 |
| V207 r2 / `dd4b1d9e847048c9aff0e3c24ade6988` | 继续反问想了解哪个方面，没有解释 |
| V207 r3 / `f94d4774864b477986f9c5a5a3117eec` | 报手册未查到该模式，改讲对外放电；原功能解释仍有覆盖缺口，不据此断言编造了车辆事实 |

因此本轮为 6 个原始红轮加 4 个补充未完成轮；不是整体 QA 全绿。原知识、规划、受话与依赖故障继续按 R0 活项处理，
来源签名不能代替这些修复。下一包按路线图进入 CA2-07；不全局关闭已有补槽收益的 retry。

证据均在主仓本地 ignored 目录 `.artifacts/vehicle-state-signing/`，clone 不自带；总索引为
`7b346c90-evidence-manifest.json`。关键 SHA-256：

- 真实消息/密钥隔离 `7b346c90-runtime-source.json`：`7aedb9d926244cd8396c82d47f73e835b5be79cd2289afc807d31924b86e9779`。
- HTTP/WS `7b346c90-live-readonly-serial.json`：`46fa383a93d45ed07b22be11d152caad69ba11dd813ed0f8ae7e02bbc992cb07`。
- 固定语料原始 `7b346c90-baseline.json`：`b48786f11ce96f526657ff97c40d1c49f3dc2e87f61d411a9f369c6d60655097`。
- 人工复核 `7b346c90-baseline-review.json`：`5af7a9b9c5c4a060835fad7c61631538ebd2c2c4583484749e481951372c4e04`。
