# 本地 Docker 持久化数据迁移到云端设计

日期：2026-08-16

状态：泓舟已书面复核通过（2026-08-17 对话确认）

适用范围：当前 Windows Docker Desktop 真栈到腾讯云 Tailnet-only demo 真栈的数据迁移

## 1. 目标

把当前正在运行的本地真栈持久化数据迁移到云服务器，使云端成为后续调试、HMI、Android 开发与 badcase 分析使用的数据主副本，同时满足：

1. 第一阶段迁移不停止、不重启、不改写本地 Docker Desktop 容器，避免影响仍在本地验证的另一个 agent。
2. 迁移采用“云端先备份、本地快照完整替换云端”的语义，不做逐行合并。
3. PostgreSQL、Redis 与 Collector SQLite 必须来自同一轮有编号、可校验的迁移批次。
4. 导入失败时可恢复到迁移前云端状态。
5. 第一阶段完成后本地仍可继续使用；另一个 agent 结束后再做第二阶段最终覆盖。
6. 不删除本地卷、历史卷、云端备份或迁移包。

## 2. 固定决策

- 迁移源只取当前运行容器实际挂载的三个活跃数据卷。
- 云端现有数据先完整备份，再由本地快照完整替换。
- 不合并两边记录；主键、会话 ID、关系边、Redis 状态键与观测 trace 不做猜测性去重。
- 57 条 `pending` 提醒按原状态迁移并在云端原样激活。
- 1 个 `enabled` 自定义场景按原状态迁移并在云端原样激活。
- 第一阶段使用在线一致性快照；快照完成后本地产生的新数据不会自动同步。
- 第二阶段在本地写入停止后重新生成完整快照并再次覆盖云端，作为最终切换点。
- 当前未挂载的 6 个历史 PostgreSQL 匿名卷不导入现行数据库，也不删除；它们单独留待只读归档核查。
- 当前活跃本地和云端 `voiceprint` 均为 0 条；验收必须诚实报告 0 条，不能把模型文件或声纹 fixture 计作已迁移声纹。

## 3. 已确认的数据基线

以下数字是 2026-08-16 设计阶段的只读快照，不是执行时的固定验收值；执行时必须重新采集。

| 数据面 | 本地 Docker | 云端 |
|---|---:|---:|
| PostgreSQL 数据库大小 | 14,146,583 bytes | 9,870,359 bytes |
| `memory_item` | 370 | 2 |
| `memory_relation` | 17 | 0 |
| `reminder_item` | 327 | 0 |
| `task_ledger` | 123 | 0 |
| `proactive_delivery` | 65 | 0 |
| `scene_item` | 1 | 0 |
| `voiceprint` | 0 | 0 |
| Redis 键 | 3271 | 33 |
| Collector `turns` | 3138 | 54 |
| Collector `spans` | 18134 | 355 |
| Collector `llm_calls` | 9372 | 111 |
| Collector `logs` | 22288 | 480 |

本地 Redis 键前缀基线为 `sess` 3021、`profile` 204、`user_sessions` 32、`payment` 7、`planner` 6、`llm` 1。不得在日志或迁移报告中打印键值、会话正文、token、声纹向量或用户隐私内容。

提醒状态基线为 `cancelled` 222、`done` 12、`fired` 36、`pending` 57。57 条待触发提醒属于同一用户，触发范围为 2026-08-17 03:30:00 至 2026-10-01 00:00:00（Asia/Shanghai）。迁移后触发属于已批准的数据语义，不应被迁移脚本擅自取消或隔离。

## 4. 迁移对象与非迁移对象

### 4.1 迁移对象

1. PostgreSQL `cockpit` 数据库的完整逻辑备份，包括 Registry、记忆、关系、提醒、任务账本、主动投递、场景和声纹表。
2. Redis DB 0 的完整一致性快照，包括键类型、值、TTL 与过期语义。
3. Collector `/data/obs.db` 的一致性 SQLite 快照，包括 WAL 中已经提交的数据。

### 4.2 不作为 Docker 数据迁移的对象

- `test/eval_corpus/**`、journey YAML、exemplar、skill 与其他 Git 语料；它们随代码 release 交付。
- Silero VAD、KWS、CAM++ 声纹模型与 Edge NLU 模型；它们由受控模型 manifest 交付。
- NATS 临时消息、容器 stdout、BuildKit 缓存、镜像层、Go/npm/pip 缓存。
- 已退出 Prometheus 容器的历史指标卷；它不进入当前云端业务数据面。
- 6 个未挂载的六月 PostgreSQL 匿名卷；它们不与当前活跃数据库混合。

## 5. 两阶段迁移

### 5.1 第一阶段：在线快照迁移

本地 Docker 保持运行。PostgreSQL 使用 `pg_dump -Fc` 获取事务一致的逻辑快照；Redis 生成同一时点的 RDB；Collector 使用 SQLite online backup API 获取包含已提交 WAL 内容的数据库副本。

第一阶段完成后：

- 云端包含本轮快照时点之前的全部本地数据。
- 本地继续可用，另一个 agent 不受切换影响。
- 本地与云端从此刻开始允许分叉；不宣称双向或持续同步。
- 云端新增数据可能在第二阶段最终覆盖时被本地最终快照替换，因此第二阶段前不得把云端新产生的数据当作唯一副本。

### 5.2 第二阶段：最终覆盖与主副本切换

另一个 agent 完成本地真栈验证后，先停止本地业务写入，再重新执行完整快照和云端覆盖。第二阶段验收通过后，云端才成为默认真栈和默认数据主副本；本地 Docker 数据继续保留，直到泓舟单独批准清理。

## 6. 迁移批次与工件

每轮迁移生成唯一 `migration_id`，格式为 UTC 时间戳加源 commit 短 SHA。一个批次包含：

- PostgreSQL custom dump。
- Redis RDB。
- Collector SQLite backup。
- 不含隐私正文的 manifest：源/目标版本、文件名、字节数、SHA-256、表行数、Redis 键数、Collector 行数、生成时间。
- 执行与验收状态文件。

迁移包不得进入 Git、镜像、日志或普通 release archive。传输使用现有 SSH/SCP；云端导入目录权限为 `0700`，文件权限为 `0600`。本机迁移包只能位于 Git 忽略目录，并收紧为当前 Windows 用户可读。

第一、第二阶段均不自动删除迁移包。最终清理必须列出本机和云端精确路径并重新取得批准。

## 7. 执行前置门禁

执行写操作前必须全部满足：

1. 本地三个源容器健康且源卷与设计盘点一致。
2. 云端 `current`、30 个运行容器、Tailscale Serve、备份 timer 和磁盘空间正常。
3. 本地与云端 PostgreSQL major version、`vector` 扩展、业务表、列类型、主键及索引兼容。
4. 本地与云端 Redis RDB 格式兼容。
5. Collector schema 兼容，`PRAGMA integrity_check` 返回 `ok`。
6. 当前无云端 release、rollback、备份或远程 E2E 独占事务。
7. 服务器可用空间足以同时保留迁移前备份、导入包和恢复工作区。
8. 迁移 manifest 的全部 SHA-256 在传输前后相同。

任一门禁失败都必须在停止云端业务写入之前退出。

## 8. 云端替换事务

1. 先运行现有 `car-agent-backup.service`，确认本轮生成 PostgreSQL dump、Redis RDB 与 Collector SQL gzip，且全部非空并可读取。
2. 获取与发布、回滚、备份、远程真栈验证互斥的云端独占锁。
3. 停止会写 PostgreSQL、Redis 或 Collector 的应用服务，形成维护窗口；不重启整机、不修改 Tailscale、安全组或 `.env`。
4. 在应用写入停止后再次确认云端备份完成。
5. PostgreSQL 以 `--clean --if-exists --no-owner --no-privileges --exit-on-error` 语义恢复本地 custom dump。
6. Redis 停止后用本地 RDB 替换活动数据集，并让云端 `appendonly yes` 从该数据集重新建立 AOF；不得让旧 AOF 覆盖新 RDB。
7. Collector 停止后用本地 SQLite backup 替换 `obs.db`；旧 WAL/SHM 只能在已有迁移前备份且目标数据库完整性通过后处理。
8. 在应用服务尚未恢复写入时执行静态计数、TTL、schema 与完整性核对。
9. 启动完整云端项目，执行发布级验证和业务抽样。

预计维护窗口为 5 至 15 分钟。构建镜像不是本事务的一部分；迁移不改变应用 release SHA。

## 9. 验收

### 9.1 静态验收

- PostgreSQL 业务表行数与源 manifest 一致；`agents`、capability vector 等可重建派生表在服务启动后允许收敛增长，但不得造成业务数据丢失。
- `memory_item`、`memory_relation`、`reminder_item`、`task_ledger`、`proactive_delivery`、`scene_item`、`voiceprint` 必须逐表核对。
- Redis 初始键数、前缀分布与 TTL 语义匹配；服务启动后的自然写入要与初始恢复读数分开记录。
- Collector 四张表在启动前与源一致，启动后新增的健康探针记录单独计数。
- PostgreSQL、Redis 与 SQLite 完整性检查通过。

### 9.2 运行验收

- 云端完整 release verify 通过。
- HMI、Dashboard、Edge WSS、Audio API 与 Collector HTTPS/WSS 可达。
- 选取一个既有用户读取记忆与关系，结果来自云端；不得在报告中输出正文。
- 57 条待触发提醒仍为 `pending`，1 个场景仍为 `enabled`，Reminder 与 Scene 服务正常。
- 声纹仍为 0 条时如实报告；声纹模型可用不等于声纹数据已迁移。
- 本地 Docker 容器在第一阶段前后保持运行，源卷未变化、未删除。

## 10. 失败与恢复

任一步失败时停止继续写入，并按迁移前云端备份恢复 PostgreSQL、Redis 和 Collector 三个数据面。恢复必须作为一组完成，不能只恢复其中一个而留下跨存储不一致。

恢复后重新启动原云端 release 并执行完整 verify。若恢复也失败，保留现场、导入包、备份和日志，停止自动动作，不尝试清理卷、重建 schema 或反复覆盖。

## 11. 明确不做

- 不做双向同步、CDC、逻辑复制或长期 Redis 主从。
- 不在 4C/8G 服务器同时运行第二套完整开发栈。
- 不修改数据库 schema。
- 不修改根 `.env`、云端 `.env`、安全组、Tailscale Serve 或 systemd 配置。
- 不清理本地 Docker 卷、旧匿名卷、云端备份、release、镜像或迁移包。
- 不把个人数据写进 Git、测试报告正文或命令输出。
