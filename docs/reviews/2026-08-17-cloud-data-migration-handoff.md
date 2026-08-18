# 云端数据迁移与远程开发交接

> 状态日期：2026-08-17。本文是这次真实迁移现场与后续接手顺序的唯一交接页。
> 工具契约仍以 `docs/dev-guide.md` §8 和 `deploy/cloud/README.md` 为准；设计取舍见两份
> `2026-08-16` spec/plan。不要从计划中的未勾选项推断真实云端状态。

## 1. 当前结论

| 项目 | 当前状态 |
|---|---|
| 本地三存储迁云 | **未完成**；第二次 final 在 `redis-restore` 失败 |
| 云端数据安全 | 已按迁移前同一批备份整组回滚，三存储均记录 `restored/started/verified=true` |
| 云端服务 | 30/30 容器运行，无 unhealthy/restarting |
| 云端迁移 fence | 已清除 |
| 云端应用 release | `585537f6d82c637eba1487dd83bf1e77ec05bcc6`，数据迁移没有改变 release |
| 仓库 main/origin | `d6afb108e6370af5bb2bb89ef773ca23d7ea4313` |
| 开发栈目标 | 仓库根 `dev-stack.local` 不存在，按规则仍是 `target=local` |
| 本地 Docker | 未退出；业务写入容器已停止，PostgreSQL、Redis 两个数据容器仍健康运行 |

因此当前不能宣称云端已经包含本地测试语料、记忆、声纹或 Collector 数据，也不能切换
`target=cloud` 或退出 Docker Desktop。上一轮明确约定失败后不做第三次盲重试，已遵守。

## 2. 保留现场

所有原始快照、兼容转换产物、云端 import、迁移前备份、失败现场、release、镜像和卷均保留，
没有执行清理。

本机批次位于 `.artifacts/cloud-data-migrations/`：

- `20260817T134508Z-585537f-final`：原始 final 快照，保持不变。
- `20260817T135809Z-585537f-final`：PostgreSQL 隔离兼容副本；仅在副本删除已废弃的
  `agents.embedding`。
- `20260817T140455Z-585537f-final`：Collector 隔离兼容副本；仅在副本按云端现行 schema
  重建 `turns`、`llm_calls` 并按字段名复制全部记录。
- `20260817T144914Z-585537f-final`：第一次兼容迁移包；最终已恢复为 `ROLLED_BACK`。
- `20260817T153743Z-585537f-final`：第二次 final 迁移包；当前失败现场。

第二次云端批次目录：
`/opt/car-agent/shared/imports/20260817T153743Z-585537f-final/`。
其 `status.json` 与 `journal.json` 均为 `ROLLED_BACK`；原始失败记录为
`failed_step=redis-restore, rc=1`。迁移前备份 stamp 为 `20260817T153933Z`，三份备份摘要已写入
上述两个状态文件。后续以文件内摘要为准，不把摘要或数据正文重复抄进其他文档。

云端受控基础设施的实际安装清单以
`/opt/car-agent/shared/release-infrastructure.json` 为准。不要只看仓库脚本就假设云端已经安装
同一版本；任何新修复都要先经过基础设施摘要校验与受控安装，再进行真实迁移。

## 3. 唯一未决问题

> ✅ **2026-08-18 已定性并修复（代码侧）**，本节保留原文作为当时的证据边界。
> 两条根因、取证链与回归见
> [`docs/design/2026-08-18-redis-migration-identity-root-causes.md`](../design/2026-08-18-redis-migration-identity-root-causes.md)：
> **RC1** = `store_identity_evidence.py::_load_key` 的 key-control 上限写死 1 MiB，
> 而真实 manifest 是 7.3 MB ⇒ `redis-restore` rc=1；**回滚传的是云端自己那份小
> backup-manifest，恰好在阈值内——这就是「apply 失败而 rollback 成功」的全部原因**。
> **RC2** = 逻辑指纹取 `sha1(DUMP(key))`，`DUMP` 对 `hashtable` 编码的对象按进程随机
> 桶序序列化 ⇒ 同一个 rdb 两次加载指纹不同，修完 RC1 后**紧接着**会倒在身份比对。
> ⚠ 下面第 5 条「补聚焦回归」已完成（三条，反向验证都做了）；**第 7 条仍未做**——
> `deploy/cloud/**` 已变更，必须先受控安装并更新摘要，且要重新生成 final 批次。

第二次 final 的 PostgreSQL replace 已开始，Redis apply 随后在 `redis-restore` 失败；自动 rollback
则成功恢复并验证了 PostgreSQL、Redis、Collector。现有证据只能说明“apply 路径处理本地 Redis
包失败”，不能据此宣称是 RDB 损坏、AOF 转换、权限、挂载或数据内容中的任何一个具体根因。

下一位 agent 不要直接发起第三次云端 apply。先在隔离、可丢弃的 Redis 容器/卷中使用保留的
`redis.rdb` 复现 apply 路径，并遵守：

1. 输入包只读，禁止修改原始 final 快照。
2. 不连接或挂载云端活动 Redis 卷，不停止云端服务。
3. 先比对 apply 与已成功 rollback 路径的镜像、挂载、owner/mode、RDB 冷启动和 AOF 建立步骤。
4. 只记录错误类别、退出码和摘要，不输出 key、value、会话正文、token 或声纹向量。
5. 找到可复现根因后先补聚焦回归；只修这一条，不顺带改业务代码。
6. 相关专项、shell 语法、CLI help 和 diff-check 通过后再提交。
7. 新提交需要单独完成云端基础设施安装/摘要更新；真实 apply 前重新展示批次、备份目标、
   预计停写范围并取得本轮授权。

## 4. 接手顺序

1. 读 `AGENTS.md`、本文、`docs/dev-guide.md` §8、`deploy/cloud/README.md` 的数据迁移章节。
2. 确认 `git status --short` 干净，核对 `HEAD` 与 `origin/main`；不要在脏工作树部署。
3. 运行 `python scripts/dev_stack.py target show`。当前必须仍显示 local；若不是，先停止并审计。
4. 只读检查第二次批次的 status/journal、active migration fence、云端 release 和 30 个容器状态。
5. 按 §3 在隔离环境复现并修复 Redis apply；未复现前不改代码。
6. 生成新的 final 批次，不复用旧 ID；依次执行 `plan`、`apply` dry-run 并核对实际源聚合。
7. 取得新授权后才执行 `apply --apply`。失败仍按同一备份整组回滚，禁止只修一个存储后继续。
8. 只有 `APPLIED`、独立 `verify`、cloud release verify 和 remote-safe E2E 全部通过，才进入切换。

## 5. 迁移成功后的云端开发激活

下列动作现在**不得执行**。满足 §4 第 8 步并取得用户确认后，按固定次序执行：

```powershell
python scripts/dev_stack.py target set cloud
python scripts/dev_stack.py target show
python scripts/dev_stack.py status
python scripts/dev_stack.py verify
python scripts/run_e2e.py --target cloud --id e2e_remote_safe
```

验收通过后才人工停止本地项目容器并退出 Docker Desktop；只停止，不删除本地卷、镜像、迁移包
或兼容副本。保留本地数据作为可回切副本，清理必须另列精确对象并重新授权。

cloud 模式下的日常路径：

- Python/Go/Node 单测和静态检查直接在本机运行，不需要 Docker。
- `python scripts/dev_stack.py hmi` / `dashboard` 只启动本机 Vite，经 Tailnet HTTPS/WSS 连接云端；
  KWS/VAD 仍在电脑或手机浏览器本地推理。
- 后端更新只部署干净、已提交、main 可达的 SHA：先
  `python scripts/dev_stack.py deploy --sha HEAD` dry-run，取得部署授权后再加 `--apply`，最后 `verify`。
- cloud E2E 缺省只运行 `remote_safe`。任何 `remote_mutating` 都需要精确 `--id`、
  `--allow-mutating` 和本轮人工授权；支付、商户写、真实车控、删除和系统配置不能由开关代替授权。

需要回到本地真栈时：

```powershell
python scripts/dev_stack.py target set local
# 人工启动 Docker Desktop
make up
python scripts/dev_stack.py status
```

工具只切换目标，不自动启动/停止 Docker，也不修改根 `.env`。

## 6. 完成判据

- [x] Redis apply 在隔离环境有稳定复现、根因和聚焦回归。
      （2026-08-18：**两条**根因，非一条；隔离复现走完整条 apply 路径证明包与工具链都不坏；
      三条回归 + 一条既有断言按新语义改判，反向验证全做。见上文 §3 注与设计文档。）
- [ ] 新 final 批次真实 apply 为 `APPLIED`，没有残留 fence。
- [ ] PostgreSQL、Redis、Collector 的 pre/post evidence 与独立 verify 通过。
- [ ] 云端仍为预期 release，30/30 容器健康，备份 timer 与 Tailnet 入口正常。
- [ ] remote-safe E2E 证据包含实际 release/provider/model/case/lock identity。
- [ ] 用户确认后写入 `target=cloud`，本机 HMI/Dashboard 能联调云端。
- [ ] 本地项目容器停止且 Docker Desktop 退出；本地卷和全部迁移工件仍保留。
