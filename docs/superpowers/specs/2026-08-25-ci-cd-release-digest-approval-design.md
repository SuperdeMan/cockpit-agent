# CI/CD 变更的一次性发布摘要批准设计

日期：2026-08-25

状态：**已实现、已推送、已部署**。最终机制与分时点证据见
`docs/agents-history.md` §71；被测 release `c7c211b` 的长会话不是 QA 全绿，问题只查
`docs/reviews/2026-08-26-minimax-cloud-qa-findings.md`。

适用范围：`car-agent` 腾讯云私网 demo 的固定发布工作流

## 1. 问题与目标

本设计启动时，云端运行 release `7a0e03aadbc7409d542d0567a4ed87924f56083a`。MiniMax QA 闭环代码已
提交并推送，但从已部署 SHA 到目标 main 的累计差异包含：

- `.github/workflows/ci.yml`
- `.github/workflows/mobile-apk.yml`

两项均被 `classify_changed_path()` 归为 `ci_cd`。现有发布器只允许应用代码，或与远端
`release-infrastructure.json` 完全一致的 infrastructure 摘要；`ci_cd` 没有任何批准通道，
所以目标计划正确地返回 `plan_rejected`。

本设计增加一个**一次性、精确摘要批准**：人工先审查目标提交中的完整 workflow 树，再把该树的
SHA-256 摘要显式传给本次计划/部署。发布器只在摘要逐字一致时放行 `ci_cd`；部署成功后新 SHA
成为下次比较基线，不需要留下永久放行状态。

成功标准：

1. 默认行为不变：没有显式摘要时，任何 `ci_cd` 差异仍 `plan_rejected`。
2. 批准只绑定目标提交的完整 `.github/workflows/**` Git 字节；新增、删除、改名或内容变化都会
   改变摘要。
3. 批准只影响 `ci_cd`。`runtime_config_contract`、`database_schema`、`secret_material` 与未批准
   infrastructure 继续硬阻断。
4. 摘要必须由命令行显式传入，不读环境变量、不写仓库、不持久化为可复用绕过。
5. dry-run、release manifest 与 `dev_stack` 输出都保留 target/approved 两个摘要，形成审计链。
6. 不修改两份 workflow 的内容，不修改 `.env`、数据库、Tailscale、systemd、安全组或密钥。

## 2. 方案比较与决定

### 2.1 采用：一次性 exact digest 命令行批准

新增参数：

```text
--approve-ci-cd-sha256 <64 位小写十六进制摘要>
```

优点：

- 每个新 workflow 字节集合都必须重新取得摘要与人工授权，不能常驻打开。
- 不需要在云主机新建批准文件，也不需要为一次累计差异扩大 bootstrap/基础设施安装面。
- 批准摘要与目标 SHA 一起进入 release manifest；普通 deploy 无法改变目标 Git 字节。
- 本次发布成功后，已部署 SHA 前进，当前两份 workflow 自然不再是后续发布差异。

代价：批准不跨发布复用；这是刻意的安全属性。

### 2.2 不采用：远端持久 `release-ci-cd.json`

它与 infrastructure anchor 形态相似，但 CI workflow 不会安装到云主机。为它增加远端状态字段、
root-owned 文件、bootstrap/更新命令和恢复路径，复杂度高于本次一次性授权的收益；持久批准在成功
发布后也没有继续存在的必要。

### 2.3 拒绝：仓库内批准 manifest

目标提交可以同时修改 workflow 和批准文件，形成自我批准。除非再引入独立签名密钥，否则不能
构成发布边界；本阶段不新增签名基础设施。

## 3. 摘要契约

### 3.1 输入集合

从**受审目标 commit**读取所有被 Git 跟踪的 `.github/workflows/**` 文件。不得读取工作树，
不得只摘要 diff 中的两条路径；摘要完整目标树才能覆盖删除、改名和“另加一个 workflow”这三种
绕过形态。

源文件枚举使用目标 SHA 的 `git ls-tree -r -z`，解析 `<mode> <type> <object>\t<path>` 并只接受
`.github/workflows/` 下的普通 blob（mode `100644|100755`）；symlink、submodule、重复/非法路径或
解析失败均拒绝。文件内容使用 `git show <target_sha>:<path>`。路径保留 Git 的大小写与 `/` 分隔，
不得从工作树读取。

### 3.2 规范化与聚合

对每个文件计算原始 Git blob 字节 SHA-256，形成：

```json
{
  ".github/workflows/ci.yml": "<sha256>",
  ".github/workflows/mobile-apk.yml": "<sha256>"
}
```

再按 UTF-8、key 排序、`separators=(",", ":")` 生成 canonical JSON，对该字节串再做 SHA-256。
这与 infrastructure digest 的路径→文件摘要→聚合摘要两层口径一致，但两种类别使用独立函数和
独立字段，不能混成一个“所有受控变化”总开关。

目标提交没有 workflow 文件时，target digest 为 `None`。当前仓库存在两份，因此本批必为 64 位
摘要。

### 3.3 批准判定

`make_release_plan()` 新增：

```text
target_ci_cd_digest
approved_ci_cd_digest
```

规则：

1. `approved_ci_cd_digest` 非空但不是 64 位小写十六进制：configuration error。
2. 本次 changed paths 没有 `ci_cd`，却传了批准摘要：configuration error，防止脚本或操作者把
   参数永久写死。
3. 存在 `ci_cd` 差异且未传批准摘要：保持原 `plan_rejected`。
4. target/approved 摘要不一致：保持 `ci_cd` blocking changes，计划输出两个摘要供核对。
5. 只有 target/approved 逐字相等时，跳过 `ci_cd` blockers。
6. 其他类别按现有逻辑独立判定；一个摘要匹配不能覆盖任何非 `ci_cd` blocker。

批准的是**目标 workflow 树**，不是某个路径名，也不是“允许 CI/CD”布尔值。

## 4. CLI、数据流与审计

### 4.1 CLI

`scripts/cloud_release.py plan/deploy` 与统一入口 `scripts/dev_stack.py deploy` 都增加同名参数，
默认空。参数只来自当前命令行；刻意不支持环境变量，避免 shell/profile 把批准变成常驻配置。

计划流程：

```text
clean main target SHA
  -> remote preflight / deployed SHA
  -> deployed..target changed_paths
  -> target infrastructure digest
  -> target full CI workflow digest
  -> make_release_plan(exact optional approvals)
  -> dry-run output / artifact / apply
```

### 4.2 输出字段

`ReleasePlan`、`cloud_release.py` JSON、`dev_stack.py` allowlist 与 release manifest 增加：

```text
target_ci_cd_sha256
approved_ci_cd_sha256
```

字段只含摘要，不含 workflow 正文。既有 `changed_paths` 与 `blocking_changes` 保留，所以审计者能
同时看到“哪些路径变了”“目标树摘要是什么”“本次人工传入了什么”。

### 4.3 远端边界

远端 `remote-release.sh` 不新增批准命令，不写批准文件，也不修改 GitHub workflow。它只接受本地
已生成、checksums 绑定且 `plan_status=ready` 的 release artifact；manifest 中新增审计字段不会
改变 build/activate/verify/rollback 状态机。

批准值不包含秘密，可以出现在命令历史和脱敏 evidence 中。SSH 凭证、token、`.env` 仍不进入
manifest 或命令参数。

## 5. 错误处理与安全边界

- 错误摘要、摘要漂移、目标 SHA 改变：fail closed；操作者必须重新运行无批准 dry-run 取得新摘要。
- 同时出现 schema、secret、runtime config 或未批准 infrastructure：即使 CI 摘要正确也拒绝。
- apply 前远端 release 已变化：现有 preflight/计划重新计算机制负责拒绝陈旧前提，不复用旧计划。
- build/activate/verify 失败：沿用现有保留现场与自动回滚；一次性 CI 批准不会写入远端状态。
- 成功后不自动删除 release、artifact、镜像或备份。

## 6. 测试策略

实施必须按 TDD，至少覆盖：

1. 完整 workflow 树摘要确定、与工作树无关；文件内容、增删、改名均改变摘要。
2. 无批准时现有 CI blocker 原样存在。
3. exact 摘要只移除 `ci_cd` blocker。
4. 缺失、错误、非法、陈旧与无 CI 变化时多传摘要均 fail closed。
5. exact CI 批准不能覆盖 schema、secret、runtime config 或 stale infrastructure。
6. `ReleasePlan`、artifact manifest、已有 artifact 校验与 JSON 输出保留 target/approved 字段。
7. `cloud_release.py`、`dev_stack.py` 参数解析/透传/输出 allowlist 对称；没有环境变量入口。
8. dry-run 不产生远端写调用；只有既有 `deploy --apply` 才进入远端事务。
9. 变异验证：去掉摘要比较、把比较改成 truthy、让 CI 批准覆盖全部类别，三种突变必须各自有红灯。

验证层级：

```powershell
python -m pytest -q scripts/tests/test_cloud_release.py scripts/tests/test_dev_stack.py
python -m pytest -q scripts/tests/test_cloud_deploy_assets.py
python -m pytest -q -n auto --dist worksteal
```

发布治理修改完成后必须再次独立 code review；不得只因目标 deploy 能通过就判设计正确。

## 7. 实施与真实发布顺序

1. 写失败测试，证明当前 `ci_cd` 无批准通道。
2. 实现 digest、计划判定、CLI/`dev_stack` 透传和 manifest 审计字段。
3. 更新云发布设计、`deploy/cloud/README.md`、`docs/dev-guide.md`、`AGENTS.md` 与历史流水。
4. 定向测试、全量测试、独立复审、白名单提交并推送 main。
5. 真栈动作前运行 `python scripts/dev_stack.py target show`。
6. 对新目标 SHA 先运行**不带批准**的 deploy dry-run，取得 target CI 摘要并确认 blockers 仅为已
   授权的两份 workflow。
7. 使用 exact 摘要运行带批准的 dry-run；必须变为 ready/dry_run，且其他 blocker 为空。
8. 使用相同目标 SHA 与摘要运行 `deploy --apply`。任何字段漂移都停止，不从旧输出猜摘要。
9. 运行 status/verify，要求云端 release 等于目标完整 SHA、5/5 healthy、验证 case 非空。
10. 执行 MiniMax-only 五 persona 长会话与 HMI C14；二者 start/end 均绑定该 release。
11. 核对车态、提醒、导航、挂起与商户草稿终态，最后更新 §69 真栈证据。

## 8. 非目标

- 不为 `runtime_config_contract`、`database_schema`、`secret_material` 增加批准参数。
- 不改变 workflow 内容，不触发或重跑 GitHub Actions，不管理 GitHub token/权限。
- 不建立通用 `--force`、`--allow-controlled-changes` 或类别列表参数。
- 不把批准摘要写进 `.env`、`dev-stack.local`、仓库 tracked 配置或远端共享状态。
- 不修改现有 infrastructure anchor 的语义。
- 不借本批执行 rollback、清理 release/镜像/备份或数据库迁移。
