# 云主机容量治理：发布产物保留、构建缓存上限与容量可观测

> 状态：**已批准，待实施**（2026-09-28：用户认可 §4.1 初值；主机级事项由 drone-agent 当前的 Claude 会话对接）。
> §4.7 的规则修订随 P1 落地；在那之前现行「逐项批准」规则继续有效，服务器与 Docker 配置不变
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
| `imports.applied_ttl_days` | 14 | 迁移包在 APPLIED 且独立 verify 通过后的保留期 |
| `build_cache.max_used_space` | `20GB` | BuildKit 自身口径；主机级，需 drone 共识（§4.4） |
| `capacity.warn_free_gib` / `capacity.target_free_gib` | 40 / 45 | 在 30 GiB 闸之上留 10–15 GiB 余量 |

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

**别名 tag**：`build_release` 打完 `car-agent-release/<svc>:<sha>` 后立即去掉构建工程的 `car-agent-release-<sha>-<svc>:latest`
（只删标签不删镜像）；此后不再有「两族 tag 都要删、只删一族层不释放」的坑。

**失败产物**：从未成功激活的 SHA（构建失败、`VERIFY_FAILED_ROLLED_BACK`、中断留下的 `.staging-*`）超过 `failed_ttl_hours` 后按同一路径退役。

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

### 4.4 构建缓存上限（主机级，需与 drone-agent 共识）

- **做法**：主机级 systemd timer（`host-buildcache-gc`，每小时一次）执行
  `docker buildx prune --builder default -f --all --max-used-space <cap>`，`cap` 读自 §4.1；本项目 release 锁被占用时跳过本轮；
  每轮结果写 journal 与证据。
- **按总量而不按时间**：§1 第 4 条已实测时间过滤会被祖先链锁住；`--max-used-space` 由 BuildKit 按最近使用排序回收到上限，
  `--all` 覆盖与镜像同层的 shared 记录。
- **为什么现在不改 daemon.json**：`builder.gc` 需要重启 dockerd；未开 live-restore 时会停掉两个项目的全部容器（09-27 已发生过），
  而共享主机的 daemon 刚出过 panic。这项列入 P3，与 live-restore、日志轮转放在同一个维护窗口里一次完成。
- **为什么不放进本项目的发布事务**：缓存是全局的，放进来等于本项目单方面清 drone 的缓存，而且只在我们发布时才会触发。
- **代价**：上限以外的冷缓存被清，个别构建会重跑对应步骤；上线前后记录构建耗时做对照。

### 4.5 主机日志与崩溃转储（主机级，需共识）

- journald：drop-in 设 `SystemMaxUse=1G`（现 0.99 GiB，默认上限可到 4 GiB），只重启 `systemd-journald`，不影响容器。
- apport core dump：由同一 host timer 删除 `/var/lib/apport/coredump` 里超过 7 天的文件；根因（drone `sim/collect.py` 的 SIGABRT）由 drone 侧修。
- 容器日志：本项目在 `compose.cloud.yaml` 为各服务设 `json-file` 的 `max-size: 10m`、`max-file: 3`（随 P1 发布生效）；daemon 级默认值放 P3。

### 4.6 容量可观测

- `dev_stack status` 增加 `disk_available_bytes`，并在低于 `warn_free_gib` 时给 warning。远端预检已返回这个字段
  （`cloud_release_lib.py:1616–1643`，`dev_stack.py:183–220` 的发布路径已在用），`status` 只需接上；
  字段集合是精确校验的（`cloud_release_lib.py:1255`、`:1659`），两端一起改。
- 新增只读 `capacity` 命令，固化这次的核算方法：用 `ctr` 的快照 usage 和镜像→快照图，按本项目 / drone / 基础镜像 / 构建缓存分列；
  列出本项目各类目录大小，以及 `/opt/car-agent` 下不属于已知布局的散落项。不调用 buildx history，不以 `docker system df` 的 unique/reclaimable 作结论。

### 4.7 规则修订（批准后先改文档、再改实践）

- `deploy/cloud/README.md:51–52` 改为：「按 `retention-policy.json` 的已批准策略，发布产物、失败产物、备份与迁移包由发布 / 备份事务自动轮转，
  每次写证据；策略外对象、数据卷、`.env` 与业务数据的删除仍须先列精确对象并逐项批准。」`:243`、`:261`、`:278–284` 同步修改。
- `CLAUDE.md:143` 与 `AGENTS.md:68` 补一句：按已批准保留策略自动轮转的发布产物、构建缓存与备份，不属于需要逐轮授权的「数据删除」；
  策略本身的修改仍需授权。
- `docs/dev-guide.md` 增加容量排查一节（内容取 §8）。

### 4.8 需要 drone-agent 侧做的（列给该项目，不在本仓实施）

镜像集保留（例如最近 3 套），并清理旧运行留下的已停容器；`artifacts/` 设保留期；修复 `sim/collect.py` 的崩溃；
认可 §4.4 的缓存上限和 §4.5 的主机日志、core dump 上限。取得共识前，本项目不单方面改主机级设置。
对接方：drone-agent 当前的 Claude 会话（2026-09-28 起）；共识结果回填本节。

## 5. 分阶段落地

| 阶段 | 内容 | 需要的授权 | 交付与验证 |
|---|---|---|---|
| P0 可观测 | §4.6：`status` 容量字段与 warning、只读 `capacity`；§8 写入 dev-guide | 常规提交与推送（只改 `scripts/`、`docs/`，不涉基础设施锚） | 单测覆盖字段校验与阈值；真机只读跑一次 `capacity`，与本次 ctr 读数对账 |
| P1 本项目保留 | §4.1–4.3、§4.5 的容器日志、§4.7 规则修订 | 本方案批准；`deploy/cloud/**` 基础设施锚重批一次；随后 deploy `--apply` | 纯函数单测 + 沿用 `_run_cloud_bash` 替身写法的 bash 集成测试；先在真机跑只读 `retention --dry-run` 逐项对账再发布；验收见 §6 |
| P2 主机级 | §4.4 缓存上限 timer；§4.5 journald 与 core dump；§4.8 与 drone 取得共识 | 系统配置授权（红线）+ drone 侧认可 | 安装后手动触发首轮，记录前后 `buildx du` Total、可用空间与下一次构建耗时 |
| P3 可选 | daemon.json（`builder.gc`、`live-restore`、日志默认值）在一个维护窗口里一次重启；评估 containerd snapshotter 取舍与扩盘 | 维护窗口 + 两项目停机授权 | 单独方案 |

## 6. 验收

1. 连续 14 天（或至少 20 次发布）不做人工清理，发布从未被 30 GiB 闸挡住，稳态可用 ≥ 45 GiB。
2. 任意时刻本项目 release 镜像集 ≤ 3 + pinned；已激活 release 在 `builds/`、`incoming/` 里没有重复源码包；失败产物存活不超过 72 h。
3. 备份套数与大小符合 GFS 预算，至少 3 套完整备份，最新一套能通过 `backup.sh` 现有的恢复校验。
4. P2 之后，`buildx du` 的 Total 不超过上限加一次构建的增量。
5. 每次自动回收都有证据 JSON，抽查可复现；守卫测试覆盖当前版本、保留集、在用镜像、pinned、迁移 fence、compose 工程目录引用、数据卷与 `.env`。
6. `status` 在低于 40 GiB 时给出 warning；`capacity` 的读数与 ctr 手工核算一致。
7. 回滚到保留窗口内的上一版：`rollback` dry-run 通过；是否做一次真机回滚演练另行授权（README 记录回滚至今未在真机演练）。

## 7. 风险与开放项

- **删错**：纯函数实现、先 dry-run 对账，路径与 tag 用正则白名单，不用 `-f`，逐对象记证据；首个启用的发布之后立即跑 `status` / `verify` 并核对保留集。
- **回滚目标变少**：保留 3 个并支持 pinned；要调大 N 只需改声明源（再走一次锚审批）。
- **缓存上限拖慢构建**：上限按实测调参，发布记录构建耗时做对照。
- **共租协作**：P2 / P3 需要 drone 侧同意；在那之前本项目只治理自己的对象，drone 的增长仍可能让本项目撞闸，所以 P0 预警要先上。
- **基础设施锚审批成本**：`deploy/cloud/**` 每改一次都要重批（09-22 那次走了三轮）；P1 的改动集中在一次审批里完成。
- **开放项**：异机备份（同盘备份防不住主机丢失）；是否扩盘；containerd snapshotter 的取舍（改回 overlay2 能去掉「一层三份」，但两个项目都要重建镜像、重启 daemon）。

## 8. 附：容量排查手册（写入 dev-guide 之前以此为准）

1. **只读测量**：`df -h /`；`ctr -n moby snapshots --snapshotter overlayfs usage`（单位为字节）配合 `ctr -n moby images ls` / `content ls`
   （按 `gc.ref.*` 标签连出镜像→快照）；`docker buildx du --builder default` 只看 Total。**不要**调用 `docker buildx history`（会导致 daemon panic），
   **不要**拿 `docker system df` 的 unique / reclaimable 下结论。
2. **回收顺序**（收益与风险从好到差）：已激活 release 的重复源码与上传包 → 保留窗口外的 release 镜像集（两族 tag）与目录 → 窗口外的备份
   → 构建缓存按总量回收（`--all --max-used-space`）→ 全清 `buildx prune -af`（下次发布无缓存重建，本项目实测约 75 min）。
3. **永远不做**：`docker system prune -a`、`docker image prune -a`、`docker volume prune`、`compose down -v`——它们是全局操作，
   会删掉 drone 的镜像、构建要用的基础镜像，或者数据卷。
4. **执行纪律**：持 release 锁、先落精确清单；删后核对 current、容器、卷、模型与构建证据，再跑 `status` 和 `verify`；证据放 `.artifacts/cloud-capacity-<date>/`。
