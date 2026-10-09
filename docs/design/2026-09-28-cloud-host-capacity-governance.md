# 云主机容量治理：发布产物保留、构建缓存上限与容量可观测

> 状态：**P0、P1 已实现并在云端启用**（基础设施锚 2026-09-29 起为 `7c34debf`，对应 `1eb2bcf1`；§4.7 规则已修订）。
> 首次策略回收为手动 apply；发布 `55165e50` 时发布事务内的自动回收已首跑通过。P2 已安装并启用（`a53034b6`，2026-09-28 18:39 CST，主机级 `host-capacity-gc` 与 journald 上限，见 §5）；P3 未启动
> **P2 修订（2026-10-09）**：按总量封顶适得其反。每一轮真删了记录的 prune 都会让两个项目的下一次构建大面积冷启动，本项目的缓存与 release 镜像因此一起膨胀（§5「P2 修订」）。
> 经用户批准，已一次性精确清理本项目失效缓存，可用 36.54 → 60.26 GiB。GC 改为：可用低于 target 才回收，一次清到 `prune_to_free_gib`，保底 `reserved_space_gib`，未达标就降一档再试（§4.4）。
> drone 用户 2026-10-09 已同意（条件即保底与退避两条）。代码与测试已完成，经基础设施锚重批与主机安装授权后才生效
> 交付对象：发布链维护者（`scripts/cloud_release*.py`、`scripts/dev_stack.py`、`deploy/cloud/**`）；§4.4、§4.5 与 §4.8 是主机级事项，需与同机 drone-agent 取得共识
> 关联：[`deploy/cloud/README.md`](../../deploy/cloud/README.md)、`deploy/cloud/remote-build.sh`、`activate-release.sh`、`backup.sh`、`scripts/cloud_release_lib.py`；
> 本方案的起点是 2026-09-28 的只读盘点与两轮清理（[history「2026-09-28：云主机容量清理」](../agents-history.md)、[QA 交接 §2](../reviews/2026-08-30-qa-closeout-handoff.md)），
> 以及 [09-27 daemon panic 事故](../reviews/2026-09-27-buildkit-history-incident.md)

## 1. 现状与证据（2026-09-28 只读实测）

**容量与门槛**。系统盘 118 GiB（ext4，另有约 5% 保留块），本项目与 drone-agent 共用；远端构建要求可用 ≥ 30 GiB
（`remote-build.sh:7`、`:66–74`）。09-20 以来有 6 次发布前因这道闸临时清理（history 09-20/21、09-23、09-24 两次、09-27、09-28）。
今天清理前可用 31.28 GiB，两轮清理后 48.08 GiB。

**占用构成**（清理前，`ctr -n moby` 按层核算）：

| 类别 | GiB | 说明 |
|---|---|---|
| BuildKit 构建缓存快照 | 26.4 | drone-agent 15.1 / 本项目 7.6 / 共用底层 3.7 |
| 镜像解包层 | 19.9 | 本项目 23 个 release 9.2 / drone 58 个镜像 9.2 / 基础镜像 1.5 |
| 压缩 blob | 12.2 | 镜像 7.5，缓存与其他 4.6 |
| `/opt/car-agent` | 5.3 | 备份 1.6、release 目录 1.4、incoming 1.4（含 1.13 GB 首版归档）、builds 0.6 |
| `~/drone-agent` | 8.9 | 其中 `artifacts/` 7.5 |
| 系统与日志 | 约 6 | journald 0.99、apport 0.63（drone 容器的 core dump） |

**增长**。现存快照中 09-21 之后新建的约 38 GiB（约 4.7 GiB/天）；近 72 h 写入的构建缓存约 16.9 GiB（两项目合计）；
本项目 09-25 → 09-28 发布 22 次；备份最多一天 17 套（每次激活一套 + 每日定时一套），单套约 12.4 MiB，随数据增长。

**决定方案形态的机制事实**

1. Docker 29.1.3 使用 containerd 镜像存储：镜像引用的 1158 个快照全部是解包出的 `sha256:<chainID>`，构建缓存是另一套随机 ID 快照；
   大于 50 MB 的 92 个镜像层有 73 个在缓存里有同尺寸孪生，另有压缩 blob。本地构建层最多落盘三份，缓存对运行中的镜像是纯冗余。
2. `docker system df` 在此不可信（镜像「可回收」为 -113%，drone 镜像 unique 加总超过整块盘），不能拿它做容量决策。
3. 层跨 release 高度共享：沿用「保留最近 6 份」只能腾出约 3 GiB；保留 3 份、删 20 份实得 8.5 GiB。
4. 缓存按时间回收有限：`until=24h`（含 `--all`）只回收 5.48 GiB——命中缓存只刷新叶子记录，剩下 811 条 ≥24 h 记录中
   793 条（21.7 GB）是 24 h 内记录的祖先；不带 `--all` 还会跳过与镜像同层的 shared 记录并锁住其父链。能兑现上限的是按总量回收。
5. 服务器上没有 `/etc/docker/daemon.json`，BuildKit 用默认 GC；实测它没有把缓存压在 30 GiB 闸之上。
6. dockerd 未开 live-restore：09-27 daemon 重启时两项目 37 个容器全部停止（事故记录 §1）；共享主机禁用 buildx history API。
7. 现行规则明文禁止自动删除：`deploy/cloud/README.md:51–52`（自动任务只列候选；任何 release、镜像归档、备份、数据卷清理都要
   先列精确对象再批准）、`:243` 与 `:261`（失败产物不自动清理）、`:278–284`；`CLAUDE.md:143`、`AGENTS.md:68` 要求数据删除与系统配置逐轮授权。

## 2. 问题

根因不是某次发布太大，而是**只有写入、没有与写入同频的回收**：

1. **发布产物只增不减**：每次发布新增 26 个镜像和 26 个别名 tag（`remote-build.sh:356–358`）、一个 release 目录、`builds/<sha>` 里三份源码
   （transport / upload / src）、一个 incoming 上传包；失败产物按规定永久保留。回收只能靠人工逐项批准，跟不上每天 5–15 次发布。
2. **备份只列候选不删除**（`backup.sh:202–204`），而每次激活前都强制备份一次（`activate-release.sh:128–133`、`:221`）。
3. **构建缓存没有有效上限**。在 containerd 存储下它对已构建镜像是冗余副本，又是两个项目都在写的主机共享资源。
4. **同机共租没有预算**：drone-agent 的占用更大，任何一方增长都会让本项目撞闸；缓存、journald、崩溃转储这类主机级资源没有明确责任方。
5. **不可见**：`status` 不报容量，撞闸时才知道；手工操作留下的散落文件（incoming 根下引导包、home 与 /tmp 暂存）无人认领。

## 3. 目标与非目标

- **G1 稳态余量**：本项目 ≤20 次发布/天、drone 保持当前节奏时，稳态可用 ≥ 45 GiB；连续两周不做人工清理，发布不被 30 GiB 闸挡住。
- **G2 可审计的自动回收**：只按已批准的策略回收策略内的对象类别；每次回收落证据（保留集、对象清单、释放字节）；
  策略外对象仍逐项批准；数据卷、`.env` 与业务数据永不自动删除。
- **G3 先预警**：`status` 报可用空间，低于 40 GiB 给 warning；有一条只读命令能给出按项目、按类别的真实占用。
- **G4 主机级资源有上限、有责任方**：构建缓存、journald、崩溃转储各有上限，并与 drone-agent 书面共识。

非目标：不降低 30 GiB 闸；本轮不更换存储驱动、不扩盘、不做异机备份（§7 另列）。

## 4. 方案

### 4.1 单一声明源

新增 `deploy/cloud/retention-policy.json`，远端脚本、本地 CLI 与测试只读这一份（它属于 `deploy/cloud/**`，随基础设施批准锚审批）。初值：

| 键 | 初值 | 依据 |
|---|---|---|
| `releases.keep_activated` | 3 | 当前 + 2 个回滚目标；更早版本因 schema 与行为漂移已无回滚价值，层共享使多留的收益也很小 |
| `releases.pinned` | `[]` | 人工钉住的已知好版本，不占 3 个名额 |
| `releases.failed_ttl_hours` | 72 | 失败构建 / 激活的诊断窗口，替代「永久保留」 |
| `backups.keep_all_hours` | 48 | 近期每套都留 |
| `backups.daily_days` / `backups.weekly_weeks` | 14 / 8 | 更早的按天、按周各留最新一套 |
| `backups.min_complete_sets` | 3 | 任何时候不少于 3 套完整备份 |
| `build_cache.prune_to_free_gib` | 60 | 2026-10-09 起取代 `max_used_space: 20GB`：可用低于 `target_free_gib` 时一次清到这条线；与 target 之差至少 15 GiB，大于回收后两个项目各一次冷构建的增量（§4.4）；主机级，需 drone 共识 |
| `build_cache.reserved_space_gib` | 10 | 回收时缓存保底（`--reserved-space`）：可用偏紧的起因常在缓存之外，保底避免把两个项目的缓存整个清空；drone 2026-10-09 提出 |
| `capacity.warn_free_gib` / `capacity.target_free_gib` | 40 / 45 | 在 30 GiB 闸之上留 10–15 GiB 余量；target 同时是构建缓存回收的触发线 |

### 4.2 发布产物保留（本项目，发布事务内）

**保留集** = 当前 release ∪ `pinned` ∪ 按最近一次成功激活时间排序的前 `keep_activated` 个 release（时间取
`shared/evidence/releases/<sha>/state-*-{VERIFIED,ROLLED_BACK}.json`，写入点 `activate-release.sh:159–181`）
∪ 仍被任何容器的 compose `working_dir` 标签指向的 release 目录（今天的 `releases/4c1f479` 即此情形——postgres / redis / nats
自 08-16 创建后未重建；改成通用规则，不写死 SHA）。

**退役一个 release** 的对象（同一事务、同一份证据）：两族镜像 tag；`releases/<sha>`；`incoming/releases/<sha>-*`；
`builds/<sha>`——先把 `image-inventory.*`、`upload/manifest.json`、`upload/checksums.sha256` 移入 `shared/evidence/releases/<sha>/build/`，再删目录。
删掉同名构建目录后该 SHA 仍可重新构建（现在残留会被 `remote-build.sh:100` 的 `build directory already exists` 挡住）。

**当前 release 的瞬态产物**：激活写入 `VERIFIED` 后立即删 `builds/<sha>/{src,transport.tar,upload/source.tar}` 与本次
`incoming/releases/<upload>/transport.tar`。它们只在激活时被读（`activate-release.sh:117`、`:214`），回滚只读 release 目录与镜像（`:240–265`）。

**别名 tag**：已激活版本的构建工程别名 `car-agent-release-<sha>-<svc>:latest` 在回收收尾时去掉（只在它与同服务的
release tag 指向同一镜像时才去，只删标签不删镜像）；退役时两族 tag 一起删。`remote-build.sh` 保持零删除（原方案想在构建里去别名，
与「构建永不删除」的既有规格冲突，改由 retention 收尾完成）。

**失败产物**：从未成功激活的 SHA（构建失败、`VERIFY_FAILED_ROLLED_BACK`、中断留下的 `.staging-*`、只上传未构建的包——含基础设施批准留下的上传目录，2026-09-29 补）超过 `failed_ttl_hours` 后按同一路径退役。

**守卫**（任一不满足即跳过该对象并记入证据）：持 release 锁且迁移 fence 为空；绝不触碰当前 release、保留集及其镜像；
镜像只按 tag 删除、不带 `-f`，被任何容器使用即跳过；路径须匹配固定正则、非符号链接、位于固定根下；
缺状态证据的 release 只报告、不自动退役；全程不调用 buildx history API。

**挂点**：`remote-release.sh` 的 `deploy` 在 `activate_release` 成功后、`rollback` 成功后各调用一次；保留集按激活时间排序，
所以回滚不会删掉较新的版本；激活失败时不调用，失败现场留到 TTL。另提供 `remote-release.sh retention --dry-run|--apply`，
本地 `cloud_release` / `dev_stack` 增加 `retention` 子命令，缺省 dry-run。实现为 `deploy/cloud/retention.py`
（纯函数计算保留集与计划，执行器可注入），每次运行写 `shared/evidence/retention/<UTC>.json`。

**稳态**：本项目发布相关占用约为 3 套镜像（约 3–4 GiB）+ 3 个 release 目录（约 0.2 GiB）+ KB 级构建证据。

### 4.3 备份轮转

在 `backup.sh` 一次**成功**备份之后（仍在锁内）按 GFS 选择保留集：48 h 内全部保留；更早的每天保留最新一套（14 天）、每周保留最新一套（8 周）；
任何时候至少 3 套完整备份；刚生成的这一套绝不删除。以「整套」为单位删除（manifest + postgres + redis + observability），
超过 24 h 的 `.partial` 一并删除；`cleanup-candidates.txt` 改为记录本轮计划与结果。按当前单套大小，稳态约 0.8–1.0 GiB。

同盘备份防不住主机丢失，这也是本地只需短保留的原因；异机备份另立（§7）。

### 4.4 构建缓存回收（主机级，需与 drone-agent 共识；2026-10-09 修订）

- **做法**：主机级 systemd timer `host-capacity-gc` 每小时一次，随机延迟不超过 10 分钟。同一 timer 兼做 §4.5 的 core dump 清理，所以不叫 buildcache。每轮按下面的顺序决定：
  1. 量 BuildKit 根目录（`/var/lib/docker/buildkit`）所在文件系统的可用空间：`statvfs` 的 `f_bavail`，与构建闸 `df --output=avail` 同口径。本机它与 containerd、`/opt/car-agent` 同属一个文件系统。
  2. 可用不低于 `capacity.target_free_gib`（45 GiB）时，**完全不调用 prune**，状态记 `not_needed`，同时清除退避状态。
  3. 退避生效时状态记 `deferred`，并在 JSON 里写 warning，提示需要人工清理缓存以外的数据。退避的条件是：上一次回收后可用仍未达到目标线，而当前可用还不低于「上次回收后可用 − 5 GiB」。不做「隔 24 h 重试」：空间没继续下降时重试，只会削掉保底之上新长出的缓存、让下一次构建冷启动，腾不出多少空间。
  4. 其余情况执行 `docker buildx prune --builder default --force --all --min-free-space <字节> --reserved-space <字节>`，一次清到可用 ≥ `build_cache.prune_to_free_gib`（60 GiB），缓存至少留 `build_cache.reserved_space_gib`（10 GiB）。回收后若仍低于目标线，把「回收后可用」写进状态文件（systemd `StateDirectory`，`/var/lib/host-capacity-gc/`）。
  - 三个值都读自 §4.1，经 car-agent 的 `retention.py` 校验。
  - BuildKit 的 `--min-free-space` 比较的是 `Bfree`，含 root 保留块，本机约 4.9 GiB。所以传入值 = 目标线 + 当轮实测的保留块字节。两个参数都按字节传，避开 `GB` 被按 1024 进制解析的单位坑。
  - 每轮一行 JSON 写入 journald，记 `available_before` 与 `buildx du` Total；真 prune 时另记 `available_after`、`total_after` 与实际传入的 `--min-free-space`。失败时 unit 进入 failed。
  - 锁规则不变：
    - 对本项目 release 锁与 drone 的 `/home/ubuntu/drone-agent/stack.lock` 各做一次非阻塞 flock 探测，拿到后立即释放；要 prune 时任一被占就跳过本轮（drone 侧 09-28 提出，避免在对方批次或部署中途清缓存）。
    - prune 期间不持有任何锁。两个项目取锁都不等待：car 用 `flock -n`，drone 四处获取方（部署 / 批次、控制台实时运行、任务台监管者、账本）都用 `fcntl.flock(LOCK_EX | LOCK_NB)`，持锁会让对方撞上的获取直接失败。
    - 锁文件缺失或不是普通文件时，本轮失败退出、不 prune（drone 已确认）。
- **放在哪**：源码在 `deploy/host/`（[安装清单与步骤](../../deploy/host/README.md)），不在 `deploy/cloud/**` 的基础设施锚内，也不随发布安装。
  主机级配置归两个项目共用；若进本项目的锚，每次调整都要重批锚，还会逼所有工作树先同步 main 才能部署。安装与更新按系统配置逐次授权。
- **为什么按可用空间触发、而不按总量封顶**（2026-10-09 实测，§5「P2 修订」）：
  1. **删除会让缓存失效**：任何一轮真删了记录的 prune，都会触发 BuildKit 的 `ReleaseUnreferenced`，把重载失败的缓存键解绑——记录还占盘，却再也命中不了。按总量封顶意味着缓存一到上限就每小时删一点，于是每次发布都冷构建：本项目 8–28 分钟（原约 1 分钟），每次新增约 3.7 GB 缓存；各 release 只共享基础层，每套独占约 2.9 GB。缓存和镜像一起膨胀。
  2. **上限压不住总量**：`--max-used-space` 只计 Private，与镜像共享的记录不计，Total 一直停在约 35 GB。
  3. **单用 `--min-free-space` 也不行**：空间充足时 BuildKit 令 keepBytes = 总量，`pruneOnce` 仍删 1 条，照样触发解绑。所以「要不要删」必须由脚本先判断。
  4. **两条线要拉开距离**：间距要大于回收后两个项目各一次冷构建的增量（本项目约 6.5 GiB），否则回收后第一次冷构建就把可用压回触发线以下、连环触发。`retention.py` 要求至少 15 GiB。
  5. **保底与退避**：可用偏紧的起因常在缓存之外（镜像、证据、账本），这时只清缓存到不了目标线。不保底会清空两个项目的缓存；不退避会退化成「每小时整清一次」。这两条是 drone 2026-10-09 提出、drone 用户同意的条件。
- **为什么现在不改 daemon.json**：`builder.gc` 需要重启 dockerd；未开 live-restore 时会停掉两个项目的全部容器（09-27 已发生过），
  而共享主机的 daemon 刚出过 panic。这项列入 P3，与 live-restore、日志轮转放在同一个维护窗口里一次完成。
- **为什么不放进本项目的发布事务**：缓存是全局的，放进来等于本项目单方面清 drone 的缓存，而且只在我们发布时才会触发。
- **代价**：每次回收后，两个项目的下一次构建都会冷启动。本项目约 20 分钟、新增约 6.5 GiB。按 09-29 → 10-04 未回收期间约 2.3 GB/天的缓存增长估算，回收约每 2–4 天一次。

### 4.5 主机日志与崩溃转储（主机级，需共识）

- journald：drop-in `/etc/systemd/journald.conf.d/60-host-capacity.conf` 设 `SystemMaxUse=1G`（现 0.99 GiB，默认上限可到 4 GiB），
  只重启 `systemd-journald`，不影响容器。
- apport core dump：由同一 host timer 删除 `/var/lib/apport/coredump` 里超过 7 天的 `core.*` 普通文件（不递归），**但要等 drone 确认后才启用**——
  drone 正用现存 5 个 core 取栈排查 `sim/collect.py` 的 SIGABRT，在那之前主机上的 core 一律不碰（已满足，见 §4.8）。
- 容器日志：推迟到 P3 与 daemon 级默认值一起做。应用容器每次发布都会重建，日志实测最大约 6 MB；为此改 `compose.cloud.yaml`
  还会让 postgres / redis / nats 重建，不值得。

### 4.6 容量可观测

- `dev_stack status` 增加独立的 `capacity` 字段（可用字节、预警线、构建闸、等级 `ok` / `warn` / `below_build_gate`），
  **不进 `warnings`、不改变 `status` 与退出码**：`probe_qa_long_sessions.py:867–884` 要求 status 为 ok 且零 warning，
  容量提示若进 `warnings`，会在低于 40 GiB 时拦下所有长会话验收。数据来自远端预检已返回的 `disk_available_bytes`
  （`cloud_release_lib.py:1616–1643`，`inspect_cloud_status` 已调用），不改远端字段集合。
- 新增只读 `capacity` 命令，固化这次的核算方法：用 `ctr` 的快照 usage 和镜像→快照图，按本项目 / drone / 基础镜像 / 构建缓存分列；
  列出本项目各类目录大小，以及 `/opt/car-agent` 下不属于已知布局的散落项。不调用 buildx history，不以 `docker system df` 的 unique/reclaimable 作结论。

### 4.7 规则修订（批准后先改文档、再改实践）

- `deploy/cloud/README.md:51–52` 改为：「按 `retention-policy.json` 的已批准策略，发布产物（含失败产物）与备份由发布 / 备份事务自动轮转，
  每次写证据；策略外对象——数据卷、`.env`、业务数据、迁移包、镜像归档、worktree——的删除仍须先列精确对象并逐项批准。」
  `:243`、`:261`、`:278–284` 同步修改（P1 已落地，见 §5）。
- `CLAUDE.md:143` 与 `AGENTS.md:68` 补一句：按已批准保留策略自动轮转的发布产物与备份，不属于需要逐轮授权的「数据删除」；
  策略本身的修改、策略外对象与数据卷仍需授权。构建缓存的主机级上限属于 P2，另按系统配置授权。
- `docs/dev-guide.md` 增加容量排查一节（内容取 §8）。

### 4.8 与 drone-agent 的分工（对接方：drone-agent 当前的 Claude 会话，2026-09-28 起）

**drone 侧已答复（2026-09-28）**：
- 自行清理：16:17–16:23 CST 按记录 ID 只删 drone 已被取代版本的私有构建缓存 292 条 / 18.64 GB（排除被镜像引用与当前版本仍依赖的父层），
  builder 总量 34.4 → 15.8 GB；artifacts 中 59 个 ULog 以 zstd 无损压缩（1.09 → 0.30 GB）；删 8 个中间版本共 34 个镜像标签与 11 个传输包。
  drone 读到 16:23 时主机可用约 70 GB。
- 保留约定（drone 用户批准）：镜像保留当前版本与各里程碑，不按「最近 N 套」（现 12 个版本、58 个标签 + M0 基础镜像）；
  已停容器由 compose 管理、可写层约 10 MB；`artifacts/` 是已提交记录引用的证据，不按时间删，ULog 定期压缩。
- 技术意见：按总量 20 GB 做 LRU 对 drone 够用（重建代价大的只有 ROS 2 基础层，PX4 工具链在 M0 基础镜像里不受缓存清理影响）；
  每小时可以，但要同时避让 `~/drone-agent/stack.lock`（已并入 §4.4）；不调用 buildx history；需重启 dockerd 的改动只在双方约定的维护窗口做。
- **drone 用户已确认（2026-09-28 晚）**：a) 缓存上限 20 GB、每小时一次、避让两个项目的锁；b) journald `SystemMaxUse=1G`；
  c) `/var/lib/apport/coredump` 超过 7 天自动删除。现存 5 个 collector core 由 drone 在真实栈验证修复（`sim/collect.py` 改为 `os._exit(0)` 退出）后自行删除。
  drone 构建文件中 apt 索引每个提交重建（每次部署缓存约涨 1 GB）的根因修复因冻结门禁暂缓，期间由 a) 兜住。
- drone 修复验证通过后，于 2026-09-28 17:44 CST 删除这 5 个 core（约 633 MB；修复记在 drone 仓库 D067），`/var/lib/apport/coredump` 现为空。
  此后 drone 有重任务会先与本项目约窗口。
- 安装仍需本项目用户对 P2（系统配置红线）单独授权；安装前先与 drone 约定窗口。

## 5. 分阶段落地

| 阶段 | 内容 | 需要的授权 | 交付与验证 |
|---|---|---|---|
| P0 可观测 | §4.6：`status` 容量字段与 warning、只读 `capacity`；§8 写入 dev-guide | 常规提交与推送（只改 `scripts/`、`docs/`，不涉基础设施锚） | 单测覆盖字段校验与阈值；真机只读跑一次 `capacity`，与本次 ctr 读数对账 |
| P1 本项目保留 | §4.1–4.3、§4.7 规则修订 | 本方案批准；`deploy/cloud/**` 基础设施锚重批一次；随后 deploy `--apply` | 纯函数单测 + 沿用 `_run_cloud_bash` 替身写法的 bash 集成测试；先在真机跑只读 `retention --dry-run` 逐项对账再发布；验收见 §6 |
| P2 主机级 | §4.4 缓存上限 timer（避让两个项目的锁）；§4.5 journald 与 core dump；§4.8 与 drone 取得共识 | 系统配置授权（红线）+ drone 用户确认；不涉基础设施锚 | 安装后手动触发首轮，记录前后 `buildx du` Total、可用空间与下一次构建耗时 |
| P3 可选 | daemon.json（`builder.gc`、`live-restore`、日志默认值）在一个维护窗口里一次重启；评估 containerd snapshotter 取舍与扩盘 | 维护窗口 + 两项目停机授权 | 单独方案 |

**P0 实现记录（2026-09-28）**：`scripts/cloud_capacity.py`（预警线 40 GiB；构建闸是 `remote-build.sh` `MIN_DISK_BYTES` 的镜像声明，由测试对账；
远端探针只调用 `du` / `ctr … ls|usage` / `docker ps` / `docker buildx du`，由测试钉住只读白名单；归属计算为本地纯函数）；
`dev_stack status` 增加 `capacity`，`dev_stack capacity` 输出按项目归属的读数；`docs/dev-guide.md` 增加「云主机容量」。
新增 19 条单测、`test_dev_stack.py` 新增 5 条，两处关键逻辑的变异（不扣容器链、不扣其他 release 的共享层）各判红；`scripts/tests` 全量 1542 passed / 11 skipped。
真机只读读数（本仓 `7b346c90` 构建进行中）：可用 65.18 GiB（ok）；本项目镜像 3.54 GiB、文件 1.79 GiB；其他镜像 14.59 GiB（18 个仓库）；
构建缓存 11.29 GiB（BuildKit 口径 15.83 GB）；散落项 6 个：`shared/` 下 3 份 `.env.bak*`（密钥副本）、两份首版引导残留、旧的 `shared/release.lock`，用户已批准删除。
首次执行撞上另一会话的发布锁而未执行，17:56 CST 在发布锁内删除。随后 capacity 把 P1 安装的 `shared/retention-policy.json` 报成散落项：
已知布局是手写清单，P1 加了安装目标却没同步它。已补入，并加测试与预检 `REQUIRED_INSTALLED` 对账，未知条目清零。

**P1 实现（2026-09-28，待基础设施审批与发布）**：`deploy/cloud/retention-policy.json`（单一声明源，`cloud_capacity` 的预警线也改读它）；
`deploy/cloud/retention.py`（宿主上唯一的删除点：纯函数算计划，执行前逐对象重验根目录 / 名字 / 非符号链接 / 当前版本 / 在用镜像 /
compose 工程目录，构建证据先迁再删，调用方以 `--lock-fd` 证明持有事务锁，apply 写证据）；`remote-release.sh` 在 deploy / rollback 成功后回收，
新增 `retention --dry-run|--apply`；`backup.sh` 以 GFS 轮转替换「列 7 天候选」，自身仍零删除；`REQUIRED_INSTALLED` 与 `SHARED_SCRIPT_NAMES`
增加 `retention.py` 与策略文件（16 个安装项）；本地 `dev_stack retention`（缺省 dry-run）。规则按 §4.7 修订 `deploy/cloud/README.md`、
`CLAUDE.md`、`AGENTS.md`。相对批准稿的三处收窄：迁移包仍人工逐项批准（低频、与迁移证据绑定）；容器日志上限推迟到 P3；别名 tag 改在 retention 收尾去掉。
测试：`test_retention.py` 23 条（本机 2 条需 Linux 的符号链接 / `/proc` 用例跳过，由 CI 覆盖），三处守卫变异各判红；`remote-release.sh` 事件序列、
`backup.sh` 规格、引导清单与 CLI 用例同步更新。

**P1 首次真机 apply 暴露的共租竞态（2026-09-28 17:2x CST）**：`48007778` 审批通过（锚 `af65f910`）、dry-run 逐项对账无误后执行 apply，
releases 删完标签（`0a589e14` 两族 52 个、`56409fef` / `56f92838` 别名 52 个；当前版本的 26 个别名因镜像在用被跳过）后，
在重读容器时失败：`docker ps` 与 `docker inspect` 之间同机 drone 正在部署、删掉了容器，批量 inspect 整体报错 ⇒ retention 以 error 退出，
目录与证据未动；`retention` 动作随 `set -e` 中止，backups 没跑；本地 CLI 只显示一个空的 failed。修复（`retention.py` 重新列表重试、
仍失败则逐个 inspect 且只跳过确已消失的容器；`retention` 动作两类各自执行并汇总退出码；CLI 缺哪份记录就标 missing 并透出错误原文）
随后重新审批发布。判据：**共租主机上任何「先列再查」的读取都要容忍对方的容器随时消失；只有「确实已不存在」才能跳过。**

**P1 启用（2026-09-28 17:3x CST）**：`887b983c` 审批通过（锚 `af65f910` → `a3346202`，只换 `remote-release.sh` 与 `retention.py`），
dry-run 复核后 `dev_stack retention --apply`：releases 退役 `0a589e14` 的 release / 构建目录、`33c2a731` 的构建目录，
收尾三个保留版本的构建工作区与 `7b346c90` 的上传包（7 个目录；构建证据与 `33c2a731` 的续建日志移入 `evidence/releases/<sha>/build/`；
当前版本的 26 个别名因镜像在用按设计跳过）；backups 按 GFS 保留 24 套、删 95 套 380 个文件（1.0 GiB → 233 MB）。
证据 `shared/evidence/retention/20260928T093322Z-releases.json` 与 `…093331Z-backups.json`；status 5/5 零 warning、可用 59.44 GiB；
verify `20260928T093523Z-7b346c9.json`。
锚更换后，基于 `887b983c` 之前提交的 car-agent 工作树再部署会被判基础设施不匹配，需先同步 main。

**发布事务内自动回收首跑（2026-09-28 17:47 CST）**：与 drone 约定在其 M2 复跑结束后发布 `55165e50`（应用代码与 `7b346c90` 相同）。
证据 `shared/evidence/retention/20260928T094743Z-releases.json` 与 `…094708Z-backups.json`。回收 reason=deploy：保留当前 + `7b346c90` / `56409fef` + `4c1f479`（compose 工程目录），退役 `56f92838`，收尾 `55165e50` 与已停用的 `7b346c90`
（删 52 个 tag、3 个目录）；激活前备份走新 `backup.sh`，25 套均在策略内、未删。status 5/5 零 warning、可用 59.95 GiB；
verify `20260928T094959Z-55165e5.json`。残余：审批用的上传目录属于从未激活的 SHA，按「无激活证据只报告」规则不会自动删，需人工清或在策略中补一类。

**P2 实现与安装（2026-09-28，`a53034b6`）**：`deploy/host/` 下有 `host_capacity_gc.py`、`host-capacity-gc.service` / `.timer`、journald drop-in 与 README，
不在基础设施锚内。drone 确认：它四处取锁都用 `fcntl.flock(LOCK_EX | LOCK_NB)`，锁文件常驻不删；同意「只探测、立即释放、prune 期间不持锁」，
也同意「锁文件缺失时整轮失败」。测试 `scripts/tests/test_host_capacity_gc.py` 15 条；真 flock 用例需要 Linux，本机跳过，改在主机上用临时文件补验；
两处锁逻辑的变异都被测试判红。`scripts/tests` 全量 1595 passed / 14 skipped。
18:38–18:39 CST 安装。先跑预检，全部通过：暂存文件哈希一致、4 个目标不存在、临时文件上的 flock 语义符合预期、`systemd-analyze verify` 通过、
对真实主机 dry-run 显示两把锁空闲且零错误。安装后：journald 重启为 active，写入读回正常，drop-in 生效（占用 1013M）；
timer 下次触发 19:04:57 CST；首轮 `Result=success`，回收 0B（Total 前后都是 20.23GB）；64 个容器的 ID 前后一致。
**单位**：docker 的字节参数按 1024 进制解析（go-units `RAMInBytes`），`20GB` 实为 20 GiB；`buildx du` 按 1000 进制显示，
所以 du 显示超过约 21.47GB 才会回收，首轮回收 0B 与此一致。§6 第 4 条按这个口径读。下一次构建的耗时待下次发布时记录。
证据在 `.artifacts/cloud-capacity-20260928/p2-host-capacity-gc/`。

**P2 首夜与收尾批（2026-09-29，`1eb2bcf1`）**：
- **定时运行**：安装后 17 轮（含 19:04 首次定时触发）全部 success、零错误，没有因锁被占而跳过的轮次。每轮回收 0B：Total 从 20.23GB 到 20.6GB，仍在实际上限以内。
  另一会话当晚两次发布（`b095caca`、`01cf47cd`），事务内回收都退役了 `7b346c90`，服务器始终恰 3 套 release 镜像。
- **构建耗时基线**：主机证据没有记录构建开始时间，用「上传清单写入 → 镜像清单写入」近似。`7b346c90` 真实重建 11 分 13 秒；
  全缓存的 `55165e50` 33 秒、`b095caca` 51 秒、`01cf47cd` 39 秒。GC 至今没有淘汰过任何缓存，所以这些数只能当基线；
  P2 对构建耗时的影响，要等 du 首次超过约 21.47GB、发生回收后的那次构建再对照。
- **补漏**：只上传、从未构建的 SHA 以前只报告不删，基础设施批准每次都留下一个这样的目录。现按失败产物处理，`failed_ttl_hours` 后退役（§4.2）；
  `deploy/cloud/README.md` 原本就写着「上传中断……72 h 后由保留策略退役」，这次是实现补上了文档已承诺的行为。
- **批准工具入库**：`scripts/cloud_infra_approval.py` 与 `dev_stack infra-approval`，取代 8 次审批所用的会话内临时脚本。
  用它重新生成 `887b983c` 那次的审批：候选锚、安装文件、路径与摘要逐字节一致，只有 apply 脚本头部注释不同。
  首次真实使用即批准本批：锚 `a3346202` → `7c34debf`，只装 `retention.py`，回读一致。之后发布闸对 `1eb2bcf1` dry-run 零阻断；
  保留策略 dry-run 把三个审批上传目录（`48007778`、`887b983c` 与本次）列为「在过期窗口内」，前两个在 10-01 下午后由发布事务回收。

**P2 修订（2026-10-09）**：证据与分析脚本在 `.artifacts/cloud-capacity-20261009/`。

- **现象**：可用 36.54 GiB（warn 档）。构建缓存 `buildx du` Total 34.85 GB，其中 Private 21.45 GB、Shared 13.4 GB。
  本项目 release 镜像 10.51 GiB（3 套加失败的 `b147b737`）；09-28 时 3 套只有 3.54 GiB。
  按缓存树归属落盘字节：本项目 974 条 20.39 GiB，drone 67 条 3.38 GiB。
- **时间线**：
  - 09-28 → 10-04 GC 每轮回收 0B（Private 未到 20 GiB），本项目发布约 1 分钟。
  - 10-04 16:01Z 首次非零回收之后，10-05 至 10-09 的 16 次发布构建，加上被本地时限杀掉的 `b147b737`，在「上次构建之后发生过非零回收」的条件下全部从 FROM 之后的第一步重跑，耗时 8–28 分钟。
  - 对照：唯一一次两次构建之间没有回收的 `2fd8d135` 用了 1 分钟；10-04 首次回收前的 3 次各 1 分钟。
  - 10-07 那次 29 分钟被时限杀掉，原因也是这个。
- **机理**：
  - BuildKit v0.26.2 的 `Controller.Prune` 只要本轮删掉 ≥1 条记录，就调用 `ReleaseUnreferenced`；重载失败（`Exists` 为 false）的结果被 `Release`。
  - 签名是记录还在、键已解绑：缓存里同一 apt 步骤、同一父记录有 8 份，各 627 MB、use=1。
  - drone 的 `/opt/drone-venv` 步骤在 10-05 19:01Z、10-09 04:02Z 两次回收前后各重建一份；10-04 之前同类记录各被复用 17–34 次，drone 侧已核实。
  - 并非全部键都被解绑：个别如 `COPY go.mod`、dashboard 的 `WORKDIR` 活过了回收，但 apt / pip / npm 这些贵的步骤次次重跑。
  - 两个项目的链都挂在无快照（blob-only）的基础镜像层记录上；具体是哪一步载入失败没有定位，不影响结论与改法。
- **处置**：
  - 经用户批准，2026-10-09 08:31–08:35Z 在本项目事务锁内执行，drone 锁只探测（free）。用 `docker buildx prune --all --filter 'id=^(…)$'` 精确删本项目失效缓存 1002 条（快照 20.32 GiB）。
  - 删除集闭合：集合外没有任何记录以集合内记录为祖先。保留 `source.local` 与 pip 缓存挂载。执行前用只读 `buildx du --filter` 核对命中 1002 条。
  - 结果：BuildKit 报回收 28.39 GB，剩余 0；可用 36.54 → 60.26 GiB，Total 34.85 → 6.46 GB。镜像、容器、release 未动；`status` 5/5 零 warning。
- **改法与状态**：
  - 见 §4.4。策略 `build_cache.max_used_space: 20GB` 改为 `prune_to_free_gib: 60` + `reserved_space_gib: 10`（`deploy/cloud/**`，需重批基础设施锚）。
  - `host_capacity_gc.py` 改触发、参数与退避状态，service 增加 `StateDirectory`（系统配置，需授权）。
  - drone 用户已同意。

## 6. 验收

1. 连续 14 天（或至少 20 次发布）不做人工清理，发布从未被 30 GiB 闸挡住，稳态可用 ≥ 45 GiB。
2. 任意时刻本项目 release 镜像集 ≤ 3 + pinned；已激活 release 在 `builds/`、`incoming/` 里没有重复源码包；失败产物存活不超过 72 h。
3. 备份套数与大小符合 GFS 预算，至少 3 套完整备份，最新一套能通过 `backup.sh` 现有的恢复校验。
4. P2 修订之后：
   - 可用不低于 target 的轮次一律 `not_needed`，不调用 prune；
   - 两次构建之间没有发生回收时，本项目发布构建 ≤ 2 分钟；
   - 每次回收后可用 ≥ `prune_to_free_gib`；达不到时进入退避，且有 warning；
   - 回收间隔以天计，若短于 2 天，先查增长来源再调两条线。
   （原第 4 条「Total 不超过 20 GiB 上限加一次构建」已作废：`--max-used-space` 不计 Shared，从未成立，见 §5「P2 修订」。）
5. 每次自动回收都有证据 JSON，抽查可复现；守卫测试覆盖当前版本、保留集、在用镜像、pinned、迁移 fence、compose 工程目录引用、数据卷与 `.env`。
6. `status` 在低于 40 GiB 时给出 warning；`capacity` 的读数与 ctr 手工核算一致。
7. 回滚到保留窗口内的上一版：`rollback` dry-run 通过；是否做一次真机回滚演练另行授权（README 记录回滚至今未在真机演练）。

## 7. 风险与开放项

- **删错**：纯函数实现、先 dry-run 对账，路径与 tag 用正则白名单，不用 `-f`，逐对象记证据；首个启用的发布之后立即跑 `status` / `verify` 并核对保留集。
- **回滚目标变少**：保留 3 个并支持 pinned；要调大 N 只需改声明源（再走一次锚审批）。
- **缓存回收拖慢构建**：每次回收后两个项目各冷构建一次（§4.4 代价）。回收频率与两条线按 GC journal 的实测调；P3 升级 BuildKit 后，要复核「删除即解绑」是否仍成立。
- **共租协作**：P2 / P3 需要 drone 侧同意；在那之前本项目只治理自己的对象，drone 的增长仍可能让本项目撞闸，所以 P0 预警要先上。
- **基础设施锚审批成本**：`deploy/cloud/**` 每改一次都要重批（09-22 那次走了三轮）；P1 的改动集中在一次审批里完成。
- **开放项**：异机备份（同盘备份防不住主机丢失）；是否扩盘；containerd snapshotter 的取舍（改回 overlay2 能去掉「一层三份」，但两个项目都要重建镜像、重启 daemon）。

## 8. 附：容量排查手册（写入 dev-guide 之前以此为准）

1. **只读测量**：`df -h /`；`ctr -n moby snapshots --snapshotter overlayfs usage`（单位为字节）配合 `ctr -n moby images ls` / `content ls`
   （按 `gc.ref.*` 标签连出镜像→快照）；`docker buildx du --builder default` 只看 Total。**不要**调用 `docker buildx history`（会导致 daemon panic），
   **不要**拿 `docker system df` 的 unique / reclaimable 下结论。
   构建变慢先看 `journalctl -u host-capacity-gc -o cat`：两次构建之间有过非零回收，下一次就会大面积冷启动。
   缓存按项目归属：`buildx du --verbose` 的 Parents 连出缓存树，按树里各步骤的描述判归属，再用 `ctr … usage -b` 取字节。
   快照 key 就是 BuildKit 记录 ID；基础镜像层记录没有快照（blob-only）。
2. **回收顺序**（收益与风险从好到差）：
   1. 已激活 release 的重复源码与上传包；
   2. 保留窗口外的 release 镜像集（两族 tag）与目录；
   3. 窗口外的备份；
   4. 构建缓存：先精确删已失效的记录（`--all --filter 'id=^(…)$'`，先用只读 `buildx du --filter` 核对命中数），再按可用空间回收（`--all --min-free-space`，它比的是 `Bfree`）；
   5. 全清 `buildx prune -af`（下次发布无缓存重建，本项目实测约 75 min）。

   注意：任何真删了记录的 prune 都会让两个项目的下一次构建大面积冷启动（§4.4），非必要不手动 prune。
3. **永远不做**：`docker system prune -a`、`docker image prune -a`、`docker volume prune`、`compose down -v`——它们是全局操作，
   会删掉 drone 的镜像、构建要用的基础镜像，或者数据卷。
4. **执行纪律**：持 release 锁、先落精确清单；删后核对 current、容器、卷、模型与构建证据，再跑 `status` 和 `verify`；证据放 `.artifacts/cloud-capacity-<date>/`。
