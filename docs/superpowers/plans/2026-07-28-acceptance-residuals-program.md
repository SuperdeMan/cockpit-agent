# M0a→M4 验收余项闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依次交付 M-A 至 M-D，关闭 `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md` §7 的 13 张 P1/P2 主卡，并用可复现的自动化、根 Compose 真栈和新鲜 canonical 证据逐卡回写。

**Architecture:** 先建立可信 E2E 尺子，再把所有个性化状态统一到 `OwnerKey=(user_id, occupant_id)`，随后以 Postgres 业务真相源补齐主动投递和异步任务恢复，最后在同一 owner/幂等/隐私约束上闭合 MCP 与 LLM provider capability。M-A → M-B → M-C → M-D 严格串行；每个里程碑先提交实现、测试和非证据文档，使 canonical inputs 干净，再查询运行时 active provider/model、执行唯一一次完整 canonical，最后仅提交证据文件并推送。

**Tech Stack:** Python 3.11/3.12、pytest/pytest-asyncio、asyncpg/PostgreSQL、gRPC/protobuf、NATS Core、Go gateway、React/Vite/Node test runner、Docker Compose、GitHub Actions。

---

## 0. 执行规则

- 设计真相源：
  - `docs/superpowers/specs/2026-07-28-acceptance-residuals-program-design.md`
  - `docs/superpowers/specs/2026-07-28-acceptance-residuals-ma-test-truth-design.md`
  - `docs/superpowers/specs/2026-07-28-acceptance-residuals-mb-occupant-isolation-design.md`
  - `docs/superpowers/specs/2026-07-28-acceptance-residuals-mc-reliable-delivery-design.md`
  - `docs/superpowers/specs/2026-07-28-acceptance-residuals-md-external-ecosystem-design.md`
- 执行顺序固定为 M-A → M-B → M-C → M-D。前一里程碑的两个提交都已推送且远端 HEAD 与本地一致，才可开始后一里程碑；不得并行实现、迁移、运行 destructive 场景或预先落地 M-D provider capability。
- 每个业务切片严格执行 RED → GREEN → REFACTOR：先新增能暴露原缺口的失败测试，确认失败原因正确，再写最小实现，再跑定向和受影响测试组。
- Windows 改 proto 时只改 `proto/`，运行 `.\scripts\gen-proto.ps1` 生成 `gen/`；不得手改生成文件。Windows 不运行 `make`，Go 测试只运行 M-A Task 5 建立的 `.\scripts\run_go_tests.ps1`。
- 数据库迁移与 destructive 场景严格按 M-B → M-C → M-D 串行。每个里程碑必须在同一个受保护
  PowerShell 调用中完成“只读 preflight → 工作区外备份 → apply → verify”；fatal conflict、
  备份失败或 verify 失败立即停止。明确标为 reportable 的 voiceprint/places 冲突按冻结策略留空/
  skip 后继续；任何类别都不自动删、并、改名或选 winner，也不得在 fatal 未清时启动后继里程碑。
- 根 `.env` 只读；真栈只通过仓库根 `docker compose -f compose.yaml ...`。禁止以 `deploy/docker-compose.yaml` 为首个 Compose 文件。
- milestone 是零容忍门禁：任一 child 或聚合结果出现 `SKIP`、`PASS_WITH_SKIPS`、partial coverage、未执行 case 或人工待判，当前里程碑立即失败；不得降级为警告、不得引用旧 canonical、不得写“主体完成”。
- 四个并发用户改动不属于本轮：
  - `docs/reviews/badcase/2026-07-26.md`
  - `docs/reviews/badcase/2026-07-27.md`
  - `docs/design/README.md`
  - `docs/design/2026-07-28-intent-accuracy-data-flywheel.md`
  所有暂存都列出精确路径，不使用会吸入它们的宽泛命令。
- 本总体计划与四份子计划必须在业务实现开始前已跟踪并保持只读；checkbox 进度只记录在外部
  任务状态中，不改写、不暂存计划文件。各里程碑提交 allowlist 必须排除对应 plan。
- 本程序已在 2026-07-28 取得用户对 schema/data migration、CI 变更、commit、push 和根
  Compose Docker 真栈验证的明确授权，执行时无需为这些已列明动作重复停下询问。授权不包含
  删除仓库文件或历史、rebase/reset/force-push、修改实际根 `.env`、公开生产部署或计划外数据删除。
- 每个里程碑固定两个提交：提交 1 只含实现、测试和非证据文档；提交后 canonical inputs 必须干净。随后从 `GET http://localhost:50059/api/llm/providers` 读取当前 active provider/model，执行不带 `--id` 的完整 canonical runner。提交 2 只含 canonical、验收报告、对应 spec 落地记录与 `AGENTS.md` 证据账本，然后推送。任何带 `--id` 的 direct-child regression 都不得带 `--canonical`，也不得覆盖 canonical。

## 1. 子计划与依赖

| 里程碑 | 子计划 | 进入条件 | 退出证据 |
|---|---|---|---|
| M-A 可信尺子 | `docs/superpowers/plans/2026-07-28-acceptance-residuals-ma-test-truth.md` | 基线 `2323 passed, 7 skipped`；核心栈运行 | 单一 manifest/runner、SKIP 第三态、隔离 E2E、动态源码守卫、fresh canonical |
| M-B 多乘员隔离 | `docs/superpowers/plans/2026-07-28-acceptance-residuals-mb-occupant-isolation.md` | M-A runner/canonical 可用 | A/B 双向隔离、OwnerKey 删除、声纹事务、Edge full/mixed owner 证据 |
| M-C 可靠触达与执行 | `docs/superpowers/plans/2026-07-28-acceptance-residuals-mc-reliable-delivery.md` | M-B owner migration 完成 | durable delivery/ACK/重投、S2S 仲裁、location 复核、report resource、Verifier uncertain |
| M-D 外部生态闭环 | `docs/superpowers/plans/2026-07-28-acceptance-residuals-md-external-ecosystem.md` | M-C Ledger owner-v2 cutover 完成 | Ledger 原子赢家、MCP operation 生命周期、provider capability 热切 |

## 2. 提交边界与证据文件

提交 1 的精确输入来自对应子计划全部 `Create`/`Modify` 路径，但排除下列证据文件。实现者必须用
子计划路径清单生成 allowlist；不得使用 `git add .`、`git add -A` 或顶层目录宽泛暂存。

每个里程碑的提交 2 只允许以下五类精确文件：

- `docs/reviews/eval/journeys_report.json`
- `docs/reviews/eval/journeys_report.md`
- `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`
- M-A：`docs/superpowers/specs/2026-07-28-acceptance-residuals-ma-test-truth-design.md`
- M-B：`docs/superpowers/specs/2026-07-28-acceptance-residuals-mb-occupant-isolation-design.md`
- M-C：`docs/superpowers/specs/2026-07-28-acceptance-residuals-mc-reliable-delivery-design.md`
- M-D：`docs/superpowers/specs/2026-07-28-acceptance-residuals-md-external-ecosystem-design.md`
- `AGENTS.md`

其中 spec 只记录实施状态、设计偏差和新鲜证据；`AGENTS.md` 只更新当前事实与新鲜测试账本。
架构、conventions、README、代码和测试必须进入提交 1，不能等 canonical 跑完后混入提交 2。
canonical outputs 不进入 canonical input digest，因此提交 2 后以“报告 code SHA 是祖先且 inputs
digest 不变”保持 fresh。

M-A 定义的 canonical input 根固定为：

```powershell
$canonicalInputPaths = @(
    'compose.yaml', 'go.mod', 'go.sum', 'deploy', 'scripts', 'test',
    'agents', 'orchestrator', 'gateway', 'llm-gateway', 'memory',
    'proactive', 'registry', 'runtime', 'observability', 'security',
    'skills', 'models', 'payment-gateway', 'proto', 'hmi'
)
```

提交 1 后执行 `git status --porcelain=v1 -- $canonicalInputPaths` 必须无输出。上列四个用户
文件不在该集合中，既不阻断 canonical，也不得进入任何提交；其他未暂存/未跟踪文件仍是阻断态。

### Task 1: 冻结基线与串行执行闸

**Files:**

- Read: `docs/superpowers/specs/2026-07-28-acceptance-residuals-program-design.md`
- Execute: `docs/superpowers/plans/2026-07-28-acceptance-residuals-ma-test-truth.md`
- Execute: `docs/superpowers/plans/2026-07-28-acceptance-residuals-mb-occupant-isolation.md`
- Execute: `docs/superpowers/plans/2026-07-28-acceptance-residuals-mc-reliable-delivery.md`
- Execute: `docs/superpowers/plans/2026-07-28-acceptance-residuals-md-external-ecosystem.md`

- [ ] **Step 1: 核对分支、用户文件与根 Compose**

```powershell
$ErrorActionPreference = 'Stop'
$branch = git branch --show-current
if ($branch -ne 'codex/acceptance-m0a-m4-residuals') {
    throw "unexpected branch: $branch"
}
git status --short --branch
docker compose -f compose.yaml config --quiet
docker compose -f compose.yaml ps postgres redis nats registry llm-gateway
```

预期：分支精确匹配；Compose 配置合法；四个并发用户文件状态保持原样；不读取或修改根 `.env`。

- [ ] **Step 2: 运行新鲜基线**

```powershell
python -m pytest --import-mode=importlib -q
npm --prefix hmi test
npm --prefix dashboard test
```

预期：Python 不低于已记录的 `2323 passed, 7 skipped`，HMI 与 Dashboard 零失败。这里的 pytest
历史 skip 只用于基线对账；从 M-A milestone runner 开始，任何里程碑选集的 skip/partial 都失败。

- [ ] **Step 3: 建立严格串行规则**

M-A 内的 synthetic owner 清理只验证测试尺子，不操作业务用户数据。业务 migration、L3/L4 删除、
delivery privacy saga、MCP cancel/compensate destructive 场景从 M-B 开始，严格按 M-B → M-C →
M-D 串行；同一时刻只允许一个执行者持有数据库迁移和 destructive 测试控制权。任何一步红灯都
停止，不派发后继里程碑子代理。

### Task 2: M-A 可信尺子——先让证据协议失败，再建立唯一 canonical 入口

**Files:**

- Plan: `docs/superpowers/plans/2026-07-28-acceptance-residuals-ma-test-truth.md`
- Spec: `docs/superpowers/specs/2026-07-28-acceptance-residuals-ma-test-truth-design.md`
- Canonical: `docs/reviews/eval/journeys_report.json`
- Canonical: `docs/reviews/eval/journeys_report.md`
- Evidence: `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`

- [ ] **Step 1: RED——只写 M-A 子计划列出的失败契约**

按 M-A Task 1～13 先创建测试与 fixture，不写生产实现，然后运行：

```powershell
python -m pytest scripts/tests/test_e2e_manifest.py scripts/tests/test_run_e2e.py scripts/tests/test_e2e_identity.py scripts/tests/test_e2e_stack_lease.py scripts/tests/test_e2e_profiles.py scripts/tests/test_run_go_tests_wrapper.py scripts/tests/test_e2e_canonical.py scripts/tests/test_e2e_arch_guard.py test/test_e2e_support.py llm-gateway/tests/test_e2e_identity.py -q
```

预期：因 manifest/runner/result protocol、identity gate、canonical freshness 或动态 AST 守卫尚未
实现而失败。若全部通过，说明 RED 没有暴露原缺口，停止并修正测试，不能开始 GREEN。

- [ ] **Step 2: GREEN——完成 M-A 实现并验证 Windows 唯一命令**

按 M-A Task 1～13 实现最小生产代码。M-A Task 5 必须先建立
`scripts/run_go_tests.ps1`，此后所有 Go 测试都经该脚本运行：

```powershell
python -m pytest scripts/tests test/test_e2e_support.py -q
.\scripts\gen-proto.ps1
.\scripts\run_go_tests.ps1 ./gateway/edge ./gateway/cloud
python -m pytest --import-mode=importlib -q
npm --prefix hmi test
npm --prefix hmi run build
npm --prefix dashboard test
npm --prefix dashboard run build
python scripts/run_e2e.py --check --milestone M-A --stale-policy warn
```

预期：所有命令退出 0；宿主 `go.mod`/`go.sum` hash 不变；runner 只有五个主分组，lane/full
不被当作 group。

- [ ] **Step 3: GREEN——根 Compose 真栈与 direct-child 隔离回归**

```powershell
docker compose -f compose.yaml up -d --build --no-deps edge-gateway llm-gateway memory
docker compose -f compose.yaml ps edge-gateway llm-gateway memory
python scripts/run_e2e.py --milestone M-A --parallel-isolation 2 --id e2e_memory --id e2e_voiceprint
python scripts/run_e2e.py --milestone M-A --lane milestone --full --stale-policy warn
```

预期：两个 direct child 和完整 milestone 都是 `PASS`；selected 等于 executed，skipped 为 0。
两个 `--id` 运行不带 `--canonical`，只能写 run artifact，绝不能刷新 canonical。

- [ ] **Step 4: 提交 1——实现、测试和非证据文档**

```powershell
$planPath = 'docs/superpowers/plans/2026-07-28-acceptance-residuals-ma-test-truth.md'
git ls-files --error-unmatch -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-A plan must be tracked before execution' }
git diff --exit-code -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-A plan is execution-time read-only' }
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-ma-test-truth-design.md',
    'AGENTS.md'
)
$implementationPaths = @(
    Select-String -Path $planPath -Encoding UTF8 -Pattern '^-\s+(?:Create|Modify):\s+`([^`]+)`' |
        ForEach-Object { $_.Matches[0].Groups[1].Value } |
        Where-Object { $_ -notin $evidencePaths } |
        Sort-Object -Unique
)
$missing = @($implementationPaths | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($missing.Count -ne 0) { throw "M-A planned paths missing: $($missing -join ', ')" }
git diff --check
git add -- $implementationPaths
$unexpected = @(git diff --cached --name-only | Where-Object { $_ -notin $implementationPaths })
if ($unexpected.Count -ne 0) { throw "M-A unexpected staged paths: $($unexpected -join ', ')" }
git commit -m 'test(e2e): make acceptance evidence explicit and reproducible'
$canonicalInputPaths = @(
    'compose.yaml', 'go.mod', 'go.sum', 'deploy', 'scripts', 'test',
    'agents', 'orchestrator', 'gateway', 'llm-gateway', 'memory',
    'proactive', 'registry', 'runtime', 'observability', 'security',
    'skills', 'models', 'payment-gateway', 'proto', 'hmi'
)
$dirtyCanonical = @(git status --porcelain=v1 -- $canonicalInputPaths)
if ($dirtyCanonical.Count -ne 0) { throw "M-A canonical inputs dirty: $($dirtyCanonical -join '; ')" }
$allowedUserPaths = @(
    'docs/reviews/badcase/2026-07-26.md', 'docs/reviews/badcase/2026-07-27.md',
    'docs/design/README.md', 'docs/design/2026-07-28-intent-accuracy-data-flywheel.md'
)
$unexpectedStatus = @(foreach ($line in @(git -c core.quotepath=false status --porcelain=v1 --untracked-files=all)) {
    $path = $line.Substring(3)
    if ($path -match ' -> ') { $path = ($path -split ' -> ', 2)[1] }
    if ($path -notin $allowedUserPaths) { $line }
})
if ($unexpectedStatus.Count -ne 0) { throw "M-A unexpected worktree state: $($unexpectedStatus -join '; ')" }
```

预期：提交成功；canonical inputs 无 staged、unstaged 或 untracked 输入。

- [ ] **Step 5: 查询 active 并执行唯一完整 canonical**

```powershell
$runtimeBefore = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
$provider = [string]$runtimeBefore.active.provider
$model = [string]$runtimeBefore.active.model
if ([string]::IsNullOrWhiteSpace($provider) -or [string]::IsNullOrWhiteSpace($model)) {
    throw 'M-A runtime active provider/model unavailable'
}
python scripts/run_e2e.py --milestone M-A --lane milestone --full --canonical --provider $provider --model $model --stale-policy error
if ($LASTEXITCODE -ne 0) { throw 'M-A canonical runner failed' }
$runtimeAfter = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
if ($provider -ne [string]$runtimeAfter.active.provider -or $model -ne [string]$runtimeAfter.active.model) {
    throw 'M-A active provider/model drifted during canonical run'
}
```

预期：总态精确为 `PASS`，无 `SKIP`、`PASS_WITH_SKIPS` 或 partial；freshness 立即复算通过。
provider/model 只来自只读控制面，不从 `.env` 推断。

- [ ] **Step 6: 提交 2——只提交 M-A 证据并推送**

回写 P1-06、P2-03、P2-04、P2-06 与两项误判后运行：

```powershell
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-ma-test-truth-design.md',
    'AGENTS.md'
)
git add -- $evidencePaths
$staged = @(git diff --cached --name-only)
$unexpected = @($staged | Where-Object { $_ -notin $evidencePaths })
if ($staged.Count -eq 0 -or $unexpected.Count -ne 0) {
    throw "M-A evidence staging invalid: $($unexpected -join ', ')"
}
git commit -m 'docs(review): refresh M-A canonical evidence'
git push origin codex/acceptance-m0a-m4-residuals
if ($LASTEXITCODE -ne 0) { throw 'M-A push failed' }
```

### Task 3: M-B 多乘员隔离——先做 owner RED，再执行第一段 migration/destructive

**Files:**

- Plan: `docs/superpowers/plans/2026-07-28-acceptance-residuals-mb-occupant-isolation.md`
- Spec: `docs/superpowers/specs/2026-07-28-acceptance-residuals-mb-occupant-isolation-design.md`
- Migration: `scripts/migrate_mb_occupant_isolation.py`
- Evidence: `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`

- [ ] **Step 1: 串行进入闸与 RED**

```powershell
git fetch origin codex/acceptance-m0a-m4-residuals
if ((git rev-parse HEAD) -ne (git rev-parse origin/codex/acceptance-m0a-m4-residuals)) {
    throw 'M-A two-commit result is not pushed; M-B is blocked'
}
python -m pytest memory/tests/test_owner_sessions.py memory/tests/test_extract.py memory/tests/test_privacy.py memory/tests/test_voiceprint_pg.py agents/reminder/tests/test_admin.py agents/reminder/tests/test_store.py orchestrator/edge/tests/test_local_turn_memory.py llm-gateway/tests/test_privacy.py scripts/tests/test_migrate_mb_occupant_isolation.py -q
```

先只写 M-B 子计划的 RED。预期：OwnerKey exchange、places/reminder 隔离、L1-L4、声纹事务或
migration preflight 失败；不得用 primary fallback 让 RED 假绿。

- [ ] **Step 2: GREEN、codegen 与全量**

```powershell
.\scripts\gen-proto.ps1
python -m pytest memory/tests agents/reminder/tests agents/scene_orchestrator/tests orchestrator/cloud/tests orchestrator/edge/tests llm-gateway/tests -q
.\scripts\run_go_tests.ps1 ./gateway/edge
python -m pytest --import-mode=importlib -q
npm --prefix hmi test
npm --prefix hmi run build
npm --prefix dashboard test
```

预期：零失败；生成代码只来自 proto codegen；occupant 仍不进入鉴权、确认或 VAL。

- [ ] **Step 3: M-B preflight、工作区外备份、apply、verify 同调用**

本步骤是程序第一个业务 migration/destructive 控制段，禁止并行：

```powershell
$env:POSTGRES_DSN = 'postgresql://cockpit:cockpit@localhost:5432/cockpit'
try {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $containerBackup = "/tmp/car-agent-mb-$stamp.dump"
    $backupPath = Join-Path $env:TEMP "car-agent-mb-$stamp.dump"
    $auditPath = Join-Path $env:TEMP "car-agent-mb-voiceprint-conflicts-$stamp.json"
    if ((Test-Path -LiteralPath $backupPath) -or (Test-Path -LiteralPath $auditPath)) {
        throw 'M-B backup/audit target already exists'
    }
    python scripts/migrate_mb_occupant_isolation.py --preflight --audit-output $auditPath
    $preflightExit = $LASTEXITCODE
    if (Test-Path -LiteralPath $auditPath) {
        icacls $auditPath /inheritance:r /grant:r "${env:USERNAME}:(R,W)"
        if ($LASTEXITCODE -ne 0) { throw 'M-B audit ACL failed; apply is blocked' }
    }
    if ($preflightExit -ne 0) { throw 'M-B preflight found fatal errors; apply is blocked' }
    docker compose -f compose.yaml exec -T postgres pg_dump -Fc -U cockpit -d cockpit -t memory_item -t reminder_item -t voiceprint -f $containerBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-B backup failed; apply is blocked' }
    $backupListing = docker compose -f compose.yaml exec -T postgres pg_restore -l $containerBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-B backup catalog is unreadable; apply is blocked' }
    foreach ($table in @('memory_item','reminder_item','voiceprint')) {
        if (-not ($backupListing -match "TABLE DATA public $table")) {
            throw "M-B backup missing table data entry: $table"
        }
    }
    docker compose -f compose.yaml cp "postgres:$containerBackup" $backupPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $backupPath) -or (Get-Item -LiteralPath $backupPath).Length -eq 0) {
        throw 'M-B external backup missing or empty; apply is blocked'
    }
    python scripts/migrate_mb_occupant_isolation.py --apply
    if ($LASTEXITCODE -ne 0) { throw 'M-B apply failed' }
    python scripts/migrate_mb_occupant_isolation.py --verify
    if ($LASTEXITCODE -ne 0) { throw 'M-B verify failed' }
} finally {
    Remove-Item Env:POSTGRES_DSN -ErrorAction SilentlyContinue
}
```

备份和受限冲突审计保留在系统临时目录，不进入 git。reportable conflict 报告精确计数后按
voiceprint 留 NULL、places skip 的冻结策略继续；只有 fatal 才停止。不得自动改名、合并、
覆盖或删除。

- [ ] **Step 4: 根 Compose 真栈与非 canonical 回归**

```powershell
docker compose -f compose.yaml up -d --build --no-deps memory reminder-agent scene-orchestrator-agent llm-gateway cloud-planner edge-orchestrator edge-gateway observability-collector hmi
docker compose -f compose.yaml ps memory reminder-agent scene-orchestrator-agent llm-gateway cloud-planner edge-orchestrator edge-gateway observability-collector hmi
python scripts/run_e2e.py --milestone M-B --id e2e_occupant_isolation --id e2e_gdpr --id e2e_geofence
python scripts/run_e2e.py --milestone M-B --lane milestone --full --stale-policy warn
```

预期：direct child 与 full selection 全部 `PASS`，无 skip/partial；`--id` 运行不刷新 canonical。

- [ ] **Step 5: M-B 两提交与 canonical**

提交 1 使用下列精确 allowlist 生成命令：

```powershell
$planPath = 'docs/superpowers/plans/2026-07-28-acceptance-residuals-mb-occupant-isolation.md'
git ls-files --error-unmatch -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-B plan must be tracked before execution' }
git diff --exit-code -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-B plan is execution-time read-only' }
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-mb-occupant-isolation-design.md',
    'AGENTS.md'
)
$implementationPaths = @(
    Select-String -Path $planPath -Encoding UTF8 -Pattern '^-\s+(?:Create|Modify):\s+`([^`]+)`' |
        ForEach-Object { $_.Matches[0].Groups[1].Value } |
        Where-Object { $_ -notin $evidencePaths } |
        Sort-Object -Unique
)
$missing = @($implementationPaths | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($missing.Count -ne 0) { throw "M-B planned paths missing: $($missing -join ', ')" }
git diff --check
git add -- $implementationPaths
$unexpected = @(git diff --cached --name-only | Where-Object { $_ -notin $implementationPaths })
if ($unexpected.Count -ne 0) { throw "M-B unexpected staged paths: $($unexpected -join ', ')" }
git commit -m 'feat(m4): enforce occupant ownership across memory and reminders'
$canonicalInputPaths = @(
    'compose.yaml', 'go.mod', 'go.sum', 'deploy', 'scripts', 'test',
    'agents', 'orchestrator', 'gateway', 'llm-gateway', 'memory',
    'proactive', 'registry', 'runtime', 'observability', 'security',
    'skills', 'models', 'payment-gateway', 'proto', 'hmi'
)
$dirtyCanonical = @(git status --porcelain=v1 -- $canonicalInputPaths)
if ($dirtyCanonical.Count -ne 0) { throw "M-B canonical inputs dirty: $($dirtyCanonical -join '; ')" }
$allowedUserPaths = @(
    'docs/reviews/badcase/2026-07-26.md', 'docs/reviews/badcase/2026-07-27.md',
    'docs/design/README.md', 'docs/design/2026-07-28-intent-accuracy-data-flywheel.md'
)
$unexpectedStatus = @(foreach ($line in @(git -c core.quotepath=false status --porcelain=v1 --untracked-files=all)) {
    $path = $line.Substring(3)
    if ($path -match ' -> ') { $path = ($path -split ' -> ', 2)[1] }
    if ($path -notin $allowedUserPaths) { $line }
})
if ($unexpectedStatus.Count -ne 0) { throw "M-B unexpected worktree state: $($unexpectedStatus -join '; ')" }
$runtimeBefore = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
$provider = [string]$runtimeBefore.active.provider
$model = [string]$runtimeBefore.active.model
if ([string]::IsNullOrWhiteSpace($provider) -or [string]::IsNullOrWhiteSpace($model)) {
    throw 'M-B runtime active provider/model unavailable'
}
python scripts/run_e2e.py --milestone M-B --lane milestone --full --canonical --provider $provider --model $model --stale-policy error
if ($LASTEXITCODE -ne 0) { throw 'M-B canonical runner failed' }
$runtimeAfter = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
if ($provider -ne [string]$runtimeAfter.active.provider -or $model -ne [string]$runtimeAfter.active.model) {
    throw 'M-B active provider/model drifted during canonical run'
}
```

回写 M-B 原卡后，提交 2 并推送：

```powershell
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-mb-occupant-isolation-design.md',
    'AGENTS.md'
)
git add -- $evidencePaths
$staged = @(git diff --cached --name-only)
$unexpected = @($staged | Where-Object { $_ -notin $evidencePaths })
if ($staged.Count -eq 0 -or $unexpected.Count -ne 0) { throw 'M-B evidence staging invalid' }
git commit -m 'docs(review): refresh M-B canonical evidence'
git push origin codex/acceptance-m0a-m4-residuals
if ($LASTEXITCODE -ne 0) { throw 'M-B push failed' }
```

### Task 4: M-C 可靠触达——在 M-B 完成后串行迁移 delivery/Ledger

**Files:**

- Plan: `docs/superpowers/plans/2026-07-28-acceptance-residuals-mc-reliable-delivery.md`
- Spec: `docs/superpowers/specs/2026-07-28-acceptance-residuals-mc-reliable-delivery-design.md`
- Migration: `scripts/migrate_mc_delivery.py`
- Evidence: `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`

- [ ] **Step 1: 串行进入闸与 RED**

```powershell
git fetch origin codex/acceptance-m0a-m4-residuals
if ((git rev-parse HEAD) -ne (git rev-parse origin/codex/acceptance-m0a-m4-residuals)) {
    throw 'M-B two-commit result is not pushed; M-C is blocked'
}
python -m pytest proactive/tests/test_store.py proactive/tests/test_delivery.py proactive/tests/test_conditions.py agents/_sdk/tests/test_ledger.py agents/deep_research/tests/test_report_store.py agents/reminder/tests/test_geofence.py orchestrator/cloud/tests/test_verify.py scripts/tests/test_migrate_mc_delivery.py -q
```

预期：durable commit、owner-v2 cutover、ACK、report resource、location 二次核验或
`EXEC_UNKNOWN` 合同失败。

- [ ] **Step 2: GREEN、codegen 与全量**

```powershell
.\scripts\gen-proto.ps1
python -m pytest proactive/tests agents/reminder/tests agents/deep_research/tests agents/_sdk/tests orchestrator/cloud/tests orchestrator/edge/tests observability/collector/tests -q
.\scripts\run_go_tests.ps1 ./gateway/edge
python -m pytest --import-mode=importlib -q
npm --prefix hmi test
npm --prefix hmi run build
npm --prefix dashboard test
npm --prefix dashboard run build
```

预期：零失败；`PRESENTED` 是唯一通知合同终态，`SPOKEN` 仅为独立观测。

- [ ] **Step 3: M-C preflight、writer freeze、工作区外备份、apply、部署、activate 与二次 verify 同调用**

```powershell
$env:POSTGRES_DSN = 'postgresql://cockpit:cockpit@localhost:5432/cockpit'
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = ''
try {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $containerBackup = "/tmp/car-agent-mc-$stamp.dump"
    $hostBackup = Join-Path $env:TEMP "car-agent-mc-$stamp.dump"
    if (Test-Path -LiteralPath $hostBackup) { throw 'M-C backup target already exists' }

    python scripts/migrate_mc_delivery.py --preflight
    if ($LASTEXITCODE -ne 0) { throw 'M-C preflight found conflicts; apply is blocked' }

    python scripts/migrate_mc_delivery.py --install-gate
    if ($LASTEXITCODE -ne 0) { throw 'M-C gate installation failed' }

    $freezeJson = python scripts/migrate_mc_delivery.py --freeze-writers
    if ($LASTEXITCODE -ne 0) { throw 'M-C writer freeze failed' }
    $freeze = $freezeJson | ConvertFrom-Json
    $freezeVersion = [int64]$freeze.freeze_version
    if ($freeze.phase -ne 'quiescing' -or $freezeVersion -lt 1) {
        throw 'M-C freeze marker is invalid'
    }

    python scripts/migrate_mc_delivery.py --preflight --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-C frozen preflight failed' }

    docker compose -f compose.yaml exec -T postgres pg_dump -Fc -U cockpit -d cockpit -t task_ledger -t task_ledger_migration_control -t reminder_item -t proactive_delivery -t research_report -f $containerBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-C backup failed; apply is blocked' }
    docker compose -f compose.yaml cp $('postgres:' + $containerBackup) $hostBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-C backup copy failed; apply is blocked' }
    $backupFile = Get-Item -LiteralPath $hostBackup
    if ($backupFile.Length -le 0) { throw 'M-C backup is empty; apply is blocked' }
    $backupListing = docker compose -f compose.yaml exec -T postgres pg_restore -l $containerBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-C backup catalog is unreadable; apply is blocked' }
    foreach ($table in @('task_ledger','task_ledger_migration_control','reminder_item','proactive_delivery','research_report')) {
        if (-not ($backupListing -match "TABLE DATA public $table")) {
            throw "M-C backup missing table data entry: $table"
        }
    }

    python scripts/migrate_mc_delivery.py --apply --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-C apply failed' }
    python scripts/migrate_mc_delivery.py --verify --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-C verify failed' }

    $env:WRITER_PROTOCOL = 'owner_v2'
    docker compose -f compose.yaml up -d --build deep-research-agent mcp-bridge
    if ($LASTEXITCODE -ne 0) {
        throw 'M-C owner-v2 writer deployment failed; writers remain quiesced'
    }

    $expectedWriters = @('deep-research-agent','mcp-bridge') | Sort-Object
    $composeConfig = (docker compose -f compose.yaml config --format json) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'M-C Compose config readback failed' }
    $declaredWriters = @(
        $composeConfig.services.PSObject.Properties |
            Where-Object { [string]$_.Value.environment.WRITER_PROTOCOL -eq 'owner_v2' } |
            ForEach-Object { $_.Name } |
            Sort-Object
    )
    if (@(Compare-Object $expectedWriters $declaredWriters).Count -ne 0) {
        throw "M-C declared writer set mismatch: $($declaredWriters -join ',')"
    }
    $runningWriters = @(
        docker compose -f compose.yaml ps --services --filter status=running deep-research-agent mcp-bridge |
            Sort-Object
    )
    if (@(Compare-Object $expectedWriters $runningWriters).Count -ne 0) {
        throw "M-C running writer set mismatch: $($runningWriters -join ',')"
    }
    docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
        --expected deep-research-agent:50073=deep-research:owner_v2 `
        --expected mcp-bridge:50076=mcp-bridge:owner_v2
    if ($LASTEXITCODE -ne 0) {
        throw 'M-C writer gRPC readiness/protocol check failed; writers remain quiesced'
    }

    python scripts/migrate_mc_delivery.py --activate-owner-v2 --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-C owner-v2 activation failed' }
    python scripts/migrate_mc_delivery.py --verify --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-C post-activation verify failed' }
} finally {
    Remove-Item Env:POSTGRES_DSN,Env:WRITER_PROTOCOL,Env:PROACTIVE_DELIVERY_DEFAULT_MODE,Env:PROACTIVE_SHADOW_SOURCES,Env:PROACTIVE_DURABLE_SOURCES -ErrorAction SilentlyContinue
}
```

该段与 M-B 已完成的 destructive 段不得重叠。任一步失败都保留 `quiescing` 与仓库外备份，
不自动解冻、不删备份、不选冲突赢家；二次 verify 未证明旧 writer 被数据库拒绝、primary 与
非-primary 新写均为 owner-v2 时，不开放非-primary，也不开始 M-D。

- [ ] **Step 3b: 按来源完成 shadow → durable cutover**

严格执行 M-C 子计划 Task 14 的 `Set-McDeliverySources` helper 和六段来源切换：Deep Research、
Reminder、road-safety、charging-planner/info/scene-orchestrator 逐批 shadow 后再 durable。
每次调用都显式注入 `PROACTIVE_DELIVERY_DEFAULT_MODE=legacy`、两个来源 allowlist，重建对应
服务；凡包含 Ledger writer 还显式注入 `WRITER_PROTOCOL=owner_v2`，并从运行中
`GET /config`/Agent Health 回读。Reminder 必须先部署 `dual_v1` writer 并完成
`pending→armed` backfill/最终约束。最终 durable allowlist 精确为
`charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator`，未列来源仍走
legacy；不得使用全局 durable 开关。

- [ ] **Step 4: 真栈故障矩阵与非 canonical full**

```powershell
$env:WRITER_PROTOCOL = 'owner_v2'
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'
docker compose -f compose.yaml up -d --build postgres nats proactive edge-gateway hmi reminder-agent deep-research-agent cloud-planner edge-orchestrator llm-gateway observability-collector dashboard
if ($LASTEXITCODE -ne 0) { throw 'M-C fault-matrix rebuild failed' }
docker compose -f compose.yaml ps postgres nats proactive edge-gateway hmi reminder-agent deep-research-agent cloud-planner edge-orchestrator llm-gateway observability-collector dashboard
if ($LASTEXITCODE -ne 0) { throw 'M-C fault-matrix status failed' }
$effective = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
if ($effective.default_mode -ne 'legacy' -or @($effective.shadow_sources).Count -ne 0 -or
    (@($effective.durable_sources | Sort-Object) -join ',') -ne
    'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator') {
    throw 'M-C fault-matrix source config mismatch'
}
docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
    --expected deep-research-agent:50073=deep-research:owner_v2 `
    --expected mcp-bridge:50076=mcp-bridge:owner_v2
if ($LASTEXITCODE -ne 0) { throw 'M-C fault-matrix writer readiness/protocol mismatch' }
python scripts/run_e2e.py --milestone M-C --id e2e_delivery_recovery --id e2e_proactive --id e2e_geofence --id e2e_research_async --id e2e_verify
if ($LASTEXITCODE -ne 0) { throw 'M-C direct child matrix failed' }
python scripts/run_e2e.py --milestone M-C --lane milestone --full --stale-policy warn
if ($LASTEXITCODE -ne 0) { throw 'M-C non-canonical milestone failed' }
```

预期：五个 direct child 与 full selection 均为 `PASS`；无 skip/partial；direct child 不写 canonical。

- [ ] **Step 5: M-C 两提交与 canonical**

```powershell
$planPath = 'docs/superpowers/plans/2026-07-28-acceptance-residuals-mc-reliable-delivery.md'
git ls-files --error-unmatch -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-C plan must be tracked before execution' }
git diff --exit-code -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-C plan is execution-time read-only' }
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-mc-reliable-delivery-design.md',
    'AGENTS.md'
)
$implementationPaths = @(
    Select-String -Path $planPath -Encoding UTF8 -Pattern '^-\s+(?:Create|Modify):\s+`([^`]+)`' |
        ForEach-Object { $_.Matches[0].Groups[1].Value } |
        Where-Object { $_ -notin $evidencePaths } |
        Sort-Object -Unique
)
$missing = @($implementationPaths | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($missing.Count -ne 0) { throw "M-C planned paths missing: $($missing -join ', ')" }
git diff --check
git add -- $implementationPaths
$unexpected = @(git diff --cached --name-only | Where-Object { $_ -notin $implementationPaths })
if ($unexpected.Count -ne 0) { throw "M-C unexpected staged paths: $($unexpected -join ', ')" }
git commit -m 'feat(m4): make proactive delivery and uncertain execution recoverable'
$canonicalInputPaths = @(
    'compose.yaml', 'go.mod', 'go.sum', 'deploy', 'scripts', 'test',
    'agents', 'orchestrator', 'gateway', 'llm-gateway', 'memory',
    'proactive', 'registry', 'runtime', 'observability', 'security',
    'skills', 'models', 'payment-gateway', 'proto', 'hmi'
)
$dirtyCanonical = @(git status --porcelain=v1 -- $canonicalInputPaths)
if ($dirtyCanonical.Count -ne 0) { throw "M-C canonical inputs dirty: $($dirtyCanonical -join '; ')" }
$allowedUserPaths = @(
    'docs/reviews/badcase/2026-07-26.md', 'docs/reviews/badcase/2026-07-27.md',
    'docs/design/README.md', 'docs/design/2026-07-28-intent-accuracy-data-flywheel.md'
)
$unexpectedStatus = @(foreach ($line in @(git -c core.quotepath=false status --porcelain=v1 --untracked-files=all)) {
    $path = $line.Substring(3)
    if ($path -match ' -> ') { $path = ($path -split ' -> ', 2)[1] }
    if ($path -notin $allowedUserPaths) { $line }
})
if ($unexpectedStatus.Count -ne 0) { throw "M-C unexpected worktree state: $($unexpectedStatus -join '; ')" }
$env:WRITER_PROTOCOL = 'owner_v2'
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'
docker compose -f compose.yaml up -d proactive charging-planner-agent deep-research-agent info-agent reminder-agent road-safety-agent scene-orchestrator-agent edge-gateway hmi
if ($LASTEXITCODE -ne 0) { throw 'M-C canonical source-mode rebuild failed' }
$deliveryRuntimeBefore = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
if ($deliveryRuntimeBefore.default_mode -ne 'legacy' -or
    @($deliveryRuntimeBefore.shadow_sources).Count -ne 0 -or
    (@($deliveryRuntimeBefore.durable_sources | Sort-Object) -join ',') -ne
    'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator') {
    throw 'M-C canonical effective source config mismatch'
}
docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
    --expected deep-research-agent:50073=deep-research:owner_v2 `
    --expected mcp-bridge:50076=mcp-bridge:owner_v2
if ($LASTEXITCODE -ne 0) { throw 'M-C canonical writer readiness/protocol mismatch' }
$runtimeBefore = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
$provider = [string]$runtimeBefore.active.provider
$model = [string]$runtimeBefore.active.model
if ([string]::IsNullOrWhiteSpace($provider) -or [string]::IsNullOrWhiteSpace($model)) {
    throw 'M-C runtime active provider/model unavailable'
}
python scripts/run_e2e.py --milestone M-C --lane milestone --full --canonical --provider $provider --model $model --stale-policy error
if ($LASTEXITCODE -ne 0) { throw 'M-C canonical runner failed' }
$runtimeAfter = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
if ($provider -ne [string]$runtimeAfter.active.provider -or $model -ne [string]$runtimeAfter.active.model) {
    throw 'M-C active provider/model drifted during canonical run'
}
$deliveryRuntimeAfter = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
if ([string]$deliveryRuntimeAfter.config_sha256 -ne [string]$deliveryRuntimeBefore.config_sha256) {
    throw 'M-C source config drifted during canonical'
}
```

回写 M-C 原卡后，提交 2 并推送：

```powershell
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-mc-reliable-delivery-design.md',
    'AGENTS.md'
)
git add -- $evidencePaths
$staged = @(git diff --cached --name-only)
$unexpected = @($staged | Where-Object { $_ -notin $evidencePaths })
if ($staged.Count -eq 0 -or $unexpected.Count -ne 0) { throw 'M-C evidence staging invalid' }
git commit -m 'docs(review): refresh M-C canonical evidence'
git push origin codex/acceptance-m0a-m4-residuals
if ($LASTEXITCODE -ne 0) { throw 'M-C push failed' }
```

### Task 5: M-D 外部生态——最后迁移 Ledger/MCP，并完成跨里程碑 canonical

**Files:**

- Plan: `docs/superpowers/plans/2026-07-28-acceptance-residuals-md-external-ecosystem.md`
- Spec: `docs/superpowers/specs/2026-07-28-acceptance-residuals-md-external-ecosystem-design.md`
- Migration: `scripts/migrate_task_ledger_md.py`
- Evidence: `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`

- [ ] **Step 1: 串行进入闸与 RED**

```powershell
git fetch origin codex/acceptance-m0a-m4-residuals
if ((git rev-parse HEAD) -ne (git rev-parse origin/codex/acceptance-m0a-m4-residuals)) {
    throw 'M-C two-commit result is not pushed; M-D is blocked'
}
python -m pytest agents/_sdk/tests/test_ledger_migration.py agents/_sdk/tests/test_ledger_postgres.py agents/mcp_bridge/tests/test_operation_store.py agents/mcp_bridge/tests/test_operation_lifecycle.py llm-gateway/tests/test_capabilities.py orchestrator/cloud/tests/test_planning_capabilities.py orchestrator/cloud/tests/test_operation_seed.py -q
```

预期：partial unique/INSERT-first、operation journal、query/cancel/compensate、capability snapshot 或
ABORTED 竞争合同失败。

- [ ] **Step 2: GREEN、codegen 与全量**

```powershell
.\scripts\gen-proto.ps1
python -m pytest agents/_sdk/tests agents/mcp_bridge/tests llm-gateway/tests orchestrator/cloud/tests -q
.\scripts\run_go_tests.ps1 ./gateway/edge ./gateway/cloud
python -m pytest --import-mode=importlib -q
npm --prefix hmi test
npm --prefix hmi run build
npm --prefix dashboard test
npm --prefix dashboard run build
```

预期：零失败；provider/model/tools/revisions 来自同一个运行时 snapshot，NONE 路径单次 JSON 且
不发送 tools。

- [ ] **Step 3: M-D owner-v2→quiescing、catalog 备份、apply、新 writer 验活与 activate 同调用**

```powershell
$compose = @('-f', 'compose.yaml')
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$containerBackup = "/tmp/car-agent-md-$stamp.dump"
$backupPath = Join-Path $env:TEMP "car-agent-md-$stamp.dump"
$catalogPath = Join-Path $env:TEMP "car-agent-md-$stamp.catalog"
$env:POSTGRES_DSN = 'postgresql://cockpit:cockpit@localhost:5432/cockpit'
$env:WRITER_PROTOCOL = 'owner_v2'
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'
$activated = $false
try {
    docker compose @compose up -d postgres
    if ($LASTEXITCODE -ne 0) { throw 'M-D postgres start failed' }

    $preflightRaw = python scripts/migrate_task_ledger_md.py --preflight --json
    if ($LASTEXITCODE -ne 0) { throw 'M-D owner-v2 preflight failed' }
    $preflight = $preflightRaw | ConvertFrom-Json
    if ($preflight.phase -ne 'owner_v2' -or [int]$preflight.schema_version -ne 2) {
        throw 'M-D requires the completed M-C owner-v2 control row'
    }
    $writerServices = @($preflight.writer_services | Sort-Object -Unique)
    if (($writerServices -join ',') -ne 'deep-research-agent,mcp-bridge') {
        throw "M-D writer inventory drift: $($writerServices -join ',')"
    }

    $freezeRaw = python scripts/migrate_task_ledger_md.py --quiesce --json
    if ($LASTEXITCODE -ne 0) { throw 'M-D quiesce failed' }
    $freezeVersion = [long](($freezeRaw | ConvertFrom-Json).freeze_version)
    if ($freezeVersion -le 0) { throw 'M-D freeze version invalid' }

    docker compose @compose stop $writerServices
    if ($LASTEXITCODE -ne 0) { throw 'M-D writer stop failed; database remains quiescing' }

    docker compose @compose exec -T postgres pg_dump -Fc -U cockpit -d cockpit `
        -t task_ledger -t task_ledger_migration_control -f $containerBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-D pg_dump failed; database remains quiescing' }
    docker compose @compose exec -T postgres pg_restore -l $containerBackup |
        Set-Content -LiteralPath $catalogPath -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'M-D pg_restore catalog failed' }
    $catalogText = Get-Content -Raw -Encoding utf8 -LiteralPath $catalogPath
    foreach ($relation in @('task_ledger', 'task_ledger_migration_control')) {
        if ($catalogText -notmatch "TABLE public $relation" -or
            $catalogText -notmatch "TABLE DATA public $relation") {
            throw "M-D backup catalog missing $relation table/data"
        }
    }
    docker compose @compose cp $('postgres:' + $containerBackup) $backupPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $backupPath) -or
        (Get-Item -LiteralPath $backupPath).Length -eq 0) {
        throw 'M-D repo-external backup failed'
    }

    python scripts/migrate_task_ledger_md.py --apply --freeze-version $freezeVersion `
        --backup-file $backupPath --backup-catalog-file $catalogPath
    if ($LASTEXITCODE -ne 0) { throw 'M-D apply failed; database remains quiescing' }

    docker compose @compose up -d --build postgres registry llm-gateway cloud-planner $writerServices
    if ($LASTEXITCODE -ne 0) { throw 'M-D writer rebuild failed; database remains quiescing' }
    docker compose @compose exec -T deep-research-agent python -c `
        "import os; assert os.getenv('PROACTIVE_DELIVERY_DEFAULT_MODE') == 'legacy'; assert os.getenv('PROACTIVE_SHADOW_SOURCES') == ''; assert os.getenv('PROACTIVE_DURABLE_SOURCES') == 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'"
    if ($LASTEXITCODE -ne 0) { throw 'deep-research proactive source config mismatch' }
    docker compose @compose exec -T deep-research-agent python -c `
        "from agents._sdk.ledger import LEDGER_WRITER_PROTOCOL; assert LEDGER_WRITER_PROTOCOL == 'owner-v2-insert-first-v1'"
    if ($LASTEXITCODE -ne 0) { throw 'deep-research writer protocol mismatch' }
    docker compose @compose exec -T mcp-bridge python -c `
        "import asyncpg; from agents._sdk.ledger import LEDGER_WRITER_PROTOCOL; assert LEDGER_WRITER_PROTOCOL == 'owner-v2-insert-first-v1'"
    if ($LASTEXITCODE -ne 0) { throw 'mcp writer protocol/dependency mismatch' }
    @'
import asyncio, os
import asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["POSTGRES_DSN"])
    try:
        async with conn.transaction():
            assert await conn.fetchval("SELECT 1") == 1
            assert await conn.fetchval(
                "SELECT to_regclass('public.mcp_operation')"
            ) == "mcp_operation"
    finally:
        await conn.close()
asyncio.run(main())
'@ | docker compose @compose exec -T mcp-bridge python -
    if ($LASTEXITCODE -ne 0) { throw 'mcp asyncpg/container PG probe failed' }
    docker compose @compose exec -T deep-research-agent python -m agents._sdk.writer_ready `
        --expected deep-research-agent:50073=deep-research:owner_v2 `
        --expected mcp-bridge:50076=mcp-bridge:owner_v2
    if ($LASTEXITCODE -ne 0) { throw 'M-D writer gRPC readiness failed' }

    python scripts/migrate_task_ledger_md.py --verify --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-D post-deploy verify failed' }
    python scripts/migrate_task_ledger_md.py --activate-owner-v2 --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-D activate failed; database remains quiescing' }
    $activeRaw = python scripts/migrate_task_ledger_md.py --preflight --json
    if ($LASTEXITCODE -ne 0) { throw 'M-D post-activation preflight failed' }
    $active = $activeRaw | ConvertFrom-Json
    if ($active.phase -ne 'owner_v2' -or [int]$active.schema_version -ne 2) {
        throw 'M-D post-activation control row mismatch'
    }
    $activated = $true
} finally {
    Remove-Item Env:POSTGRES_DSN,Env:WRITER_PROTOCOL,Env:PROACTIVE_DELIVERY_DEFAULT_MODE,Env:PROACTIVE_SHADOW_SOURCES,Env:PROACTIVE_DURABLE_SOURCES -ErrorAction SilentlyContinue
}
if (-not $activated) { throw 'M-D cutover did not activate' }
```

必须先证明 M-C control row 精确为 `schema_version=2, phase=owner_v2`；空表但 control
仍是 legacy/quiescing 也失败。发现 legacy active、未知 writer 或重复项立即停止，不选赢家、
不改 key；任一步失败都保持 quiescing 和仓库外 dump/catalog。

- [ ] **Step 4: M-D direct child，不刷新 canonical**

```powershell
$env:WRITER_PROTOCOL = 'owner_v2'
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'
try {
    docker compose -f compose.yaml --profile acceptance up -d --build postgres proactive registry llm-gateway cloud-planner mcp-bridge mcp-bridge-worker-a mcp-bridge-worker-b
    if ($LASTEXITCODE -ne 0) { throw 'M-D acceptance profile build failed' }
    docker compose -f compose.yaml --profile acceptance ps postgres proactive registry llm-gateway cloud-planner mcp-bridge mcp-bridge-worker-a mcp-bridge-worker-b
    if ($LASTEXITCODE -ne 0) { throw 'M-D acceptance profile status failed' }
    $deliveryRuntime = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
    if ($deliveryRuntime.default_mode -ne 'legacy' -or
        @($deliveryRuntime.shadow_sources).Count -ne 0 -or
        (@($deliveryRuntime.durable_sources | Sort-Object) -join ',') -ne
        'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator') {
        throw 'M-D non-canonical proactive source config mismatch'
    }
    docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
        --expected deep-research-agent:50073=deep-research:owner_v2 `
        --expected mcp-bridge:50076=mcp-bridge:owner_v2 `
        --expected mcp-bridge-worker-a:50076=mcp-bridge:owner_v2 `
        --expected mcp-bridge-worker-b:50076=mcp-bridge:owner_v2
    if ($LASTEXITCODE -ne 0) { throw 'M-D acceptance writer readiness/protocol mismatch' }
    python scripts/run_e2e.py --milestone M-D --id e2e_mcp --id e2e_planner_toolcall
    if ($LASTEXITCODE -ne 0) { throw 'M-D direct child failed' }
    python scripts/run_e2e.py --milestone M-D --lane milestone --full --stale-policy warn
    if ($LASTEXITCODE -ne 0) { throw 'M-D non-canonical milestone failed' }
} finally {
    Remove-Item Env:WRITER_PROTOCOL,Env:PROACTIVE_DELIVERY_DEFAULT_MODE,Env:PROACTIVE_SHADOW_SOURCES,Env:PROACTIVE_DURABLE_SOURCES -ErrorAction SilentlyContinue
}
```

预期：两个 direct child 与 full selection 都为 `PASS`；无 skip/partial；`--id` 命令不带
`--canonical`。两个 worker 共享 PostgreSQL、禁用 Registry 注册并固定暴露 50078/50079；
Registry 中的正式 `mcp-bridge` endpoint 不得被 worker 覆盖。

- [ ] **Step 5: 提交 1 并证明 canonical inputs 干净**

```powershell
$planPath = 'docs/superpowers/plans/2026-07-28-acceptance-residuals-md-external-ecosystem.md'
git ls-files --error-unmatch -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-D plan must be tracked before execution' }
git diff --exit-code -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-D plan is execution-time read-only' }
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-md-external-ecosystem-design.md',
    'AGENTS.md'
)
$implementationPaths = @(
    Select-String -Path $planPath -Encoding UTF8 -Pattern '^-\s+(?:Create|Modify):\s+`([^`]+)`' |
        ForEach-Object { $_.Matches[0].Groups[1].Value } |
        Where-Object { $_ -notin $evidencePaths } |
        Sort-Object -Unique
)
$missing = @($implementationPaths | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($missing.Count -ne 0) { throw "M-D planned paths missing: $($missing -join ', ')" }
git diff --check
git add -- $implementationPaths
$unexpected = @(git diff --cached --name-only | Where-Object { $_ -notin $implementationPaths })
if ($unexpected.Count -ne 0) { throw "M-D unexpected staged paths: $($unexpected -join ', ')" }
git commit -m 'feat(m4): close external operation and provider capability gaps'
$canonicalInputPaths = @(
    'compose.yaml', 'go.mod', 'go.sum', 'deploy', 'scripts', 'test',
    'agents', 'orchestrator', 'gateway', 'llm-gateway', 'memory',
    'proactive', 'registry', 'runtime', 'observability', 'security',
    'skills', 'models', 'payment-gateway', 'proto', 'hmi'
)
$dirtyCanonical = @(git status --porcelain=v1 -- $canonicalInputPaths)
if ($dirtyCanonical.Count -ne 0) { throw "M-D canonical inputs dirty: $($dirtyCanonical -join '; ')" }
$allowedUserPaths = @(
    'docs/reviews/badcase/2026-07-26.md', 'docs/reviews/badcase/2026-07-27.md',
    'docs/design/README.md', 'docs/design/2026-07-28-intent-accuracy-data-flywheel.md'
)
$unexpectedStatus = @(foreach ($line in @(git -c core.quotepath=false status --porcelain=v1 --untracked-files=all)) {
    $path = $line.Substring(3)
    if ($path -match ' -> ') { $path = ($path -split ' -> ', 2)[1] }
    if ($path -notin $allowedUserPaths) { $line }
})
if ($unexpectedStatus.Count -ne 0) { throw "M-D unexpected worktree state: $($unexpectedStatus -join '; ')" }
```

- [ ] **Step 6: 根 Compose 全栈执行最终跨里程碑 canonical**

这是程序唯一的最终跨里程碑真栈；不得用 included Compose、不得读 `.env` 取得 provider：

```powershell
$env:WRITER_PROTOCOL = 'owner_v2'
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'
try {
    docker compose -f compose.yaml --profile acceptance up -d --build
    if ($LASTEXITCODE -ne 0) { throw 'final root Compose build failed' }
    docker compose -f compose.yaml --profile acceptance ps

    $deliveryRuntimeBefore = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
    if ($deliveryRuntimeBefore.default_mode -ne 'legacy' -or
        @($deliveryRuntimeBefore.shadow_sources).Count -ne 0 -or
        (@($deliveryRuntimeBefore.durable_sources | Sort-Object) -join ',') -ne
        'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator') {
        throw 'final proactive source config mismatch'
    }
    docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
        --expected deep-research-agent:50073=deep-research:owner_v2 `
        --expected mcp-bridge:50076=mcp-bridge:owner_v2 `
        --expected mcp-bridge-worker-a:50076=mcp-bridge:owner_v2 `
        --expected mcp-bridge-worker-b:50076=mcp-bridge:owner_v2
    if ($LASTEXITCODE -ne 0) { throw 'final Ledger writer readiness/protocol mismatch' }

    $runtimeBefore = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
    $provider = [string]$runtimeBefore.active.provider
    $model = [string]$runtimeBefore.active.model
    if ([string]::IsNullOrWhiteSpace($provider) -or [string]::IsNullOrWhiteSpace($model)) {
        throw 'final runtime active provider/model unavailable'
    }
    python scripts/run_e2e.py --milestone M-D --lane milestone --full --canonical --provider $provider --model $model --stale-policy error
    if ($LASTEXITCODE -ne 0) { throw 'final cross-milestone canonical runner failed' }
    $runtimeAfter = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
    if ($provider -ne [string]$runtimeAfter.active.provider -or $model -ne [string]$runtimeAfter.active.model) {
        throw 'final active provider/model drifted during canonical run'
    }
    $deliveryRuntimeAfter = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
    if ([string]$deliveryRuntimeAfter.config_sha256 -ne [string]$deliveryRuntimeBefore.config_sha256) {
        throw 'final proactive source config drifted during canonical'
    }
} finally {
    Remove-Item Env:WRITER_PROTOCOL,Env:PROACTIVE_DELIVERY_DEFAULT_MODE,Env:PROACTIVE_SHADOW_SOURCES,Env:PROACTIVE_DURABLE_SOURCES -ErrorAction SilentlyContinue
}
```

预期：M-A 至 M-D 所有已生效 privacy target、13 张主卡和 journeys 全部在同一 full selection 中
通过；总态精确为 `PASS`，没有 `SKIP`、`PASS_WITH_SKIPS`、partial 或 stale；M-D metadata 的
`capability_source` 精确为 `gateway_rpc`，两枚 capability revision 前后不漂移，provider probe
的 upstream count 来自 Gateway 真实 attempt 而非静态推断。

- [ ] **Step 7: 提交 2——只提交最终证据并推送**

```powershell
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-md-external-ecosystem-design.md',
    'AGENTS.md'
)
git add -- $evidencePaths
$staged = @(git diff --cached --name-only)
$unexpected = @($staged | Where-Object { $_ -notin $evidencePaths })
if ($staged.Count -eq 0 -or $unexpected.Count -ne 0) { throw 'M-D evidence staging invalid' }
git commit -m 'docs(review): refresh M-D canonical evidence'
git push origin codex/acceptance-m0a-m4-residuals
if ($LASTEXITCODE -ne 0) { throw 'M-D push failed' }
```

### Task 6: 程序级只读收口

**Files:**

- Verify: `docs/reviews/eval/journeys_report.json`
- Verify: `docs/reviews/eval/journeys_report.md`
- Verify: `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`
- Verify: `AGENTS.md`

- [ ] **Step 1: 证明第二提交没有让 canonical 自陈旧**

```powershell
git fetch origin codex/acceptance-m0a-m4-residuals
if ((git rev-parse HEAD) -ne (git rev-parse origin/codex/acceptance-m0a-m4-residuals)) {
    throw 'final two-commit result is not pushed'
}
docker compose -f compose.yaml ps
$runtime = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
$provider = [string]$runtime.active.provider
$model = [string]$runtime.active.model
if ([string]::IsNullOrWhiteSpace($provider) -or [string]::IsNullOrWhiteSpace($model)) {
    throw 'final runtime active provider/model unavailable'
}
python scripts/run_e2e.py --check --milestone M-D --provider $provider --model $model --stale-policy error
git diff --check
git status --short --branch
```

预期：freshness 通过；HEAD 与远端一致；除四个并发用户文件外工作区干净。该步骤只读检查
canonical，不再次运行 canonical，因此不会制造第三个证据提交。

- [ ] **Step 2: 按新鲜证据关闭状态**

13 张主卡只允许：

- `已修`：本轮自动化、真栈与 canonical 全通过；
- `历史已修`：仅 P2-06 `few_shots` 且本轮复核通过；
- `误判更正`：仅 `PLANNER_TOOLCALL` 重启与 cloud-gateway 重试两项；
- `明确后置`：仅总设计明确排除的范围。

任一必做 case 未过、任一 migration/backup/verify 未完成、任一 milestone 出现 skip/partial 或
canonical stale，都保留原卡红灯与可复现 blocker，不写“主体完成”。

## 3. 完成定义

- [ ] M-A → M-B → M-C → M-D 顺序未被打破；migration/destructive 控制段没有并行。
- [ ] 四个里程碑各有且仅有两个提交：实现提交在前，证据提交在后；每组都已推送。
- [ ] 四次 canonical 分别精确使用：
  - `--milestone M-A --lane milestone --full --canonical --provider $provider --model $model --stale-policy error`
  - `--milestone M-B --lane milestone --full --canonical --provider $provider --model $model --stale-policy error`
  - `--milestone M-C --lane milestone --full --canonical --provider $provider --model $model --stale-policy error`
  - `--milestone M-D --lane milestone --full --canonical --provider $provider --model $model --stale-policy error`
  四条都无 `--id`；所有 direct-child regression 均未刷新 canonical。
- [ ] 每次 canonical 前 canonical inputs 干净，active provider/model 来自
  `GET /api/llm/providers`，运行前后无漂移。
- [ ] Windows 命令未直接调用 `make` 或 Go 工具链；proto 使用 `.\scripts\gen-proto.ps1`，
  Go 测试使用 `.\scripts\run_go_tests.ps1`。
- [ ] 所有 milestone 总态为精确 `PASS`，零 skip、零 partial、零 stale。
- [ ] 根 `.env`、密钥、外部备份、生产数据与四个并发用户文件均未进入提交。

## 4. 执行选择

计划完成后只有两种执行方式：

1. 本会话使用 `superpowers:subagent-driven-development`，每个 Task 派发独立实现子代理，并在
   RED、GREEN、提交 1、canonical、提交 2 五个闸点复核；
2. 新会话使用 `superpowers:executing-plans`，严格按 Task 1 → Task 6 顺序批次执行并在每个
   里程碑第二提交推送后建立检查点。
