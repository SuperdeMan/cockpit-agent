# CA2-06+12：车辆身份、逐信号观测与有状态仿真

> 状态：实施中；起点 `e120b931`。代码/提交/推送与必要发布已有授权；签名启用涉及的运行配置另列审查。
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

首版代码验证中，最终提交 SHA、全量与门禁、部署 SHA、artifact 和设备证据分栏补充。
当前没有 CA2-06/12 的云端签名启用或设备验收证据；旧发布状态仍以 QA 交接 §2 为准。
