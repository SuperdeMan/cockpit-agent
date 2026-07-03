# 本地完整 e2e 脚本清单执行器（PowerShell 版，等价于 `make e2e` / scripts/run_e2e.sh）。
# 与 nightly-e2e.yml 的关系、Mock 下预期的合理失败/SKIP，见 run_e2e.sh 顶部注释与
# docs/design/2026-07-03-r3.3-e2e-ci-gate.md。
$ErrorActionPreference = "Continue"   # 不因单个脚本失败提前退出——要跑完全部再汇总
Set-Location (Join-Path $PSScriptRoot "..")

$collectorHealthz = "http://localhost:8092/healthz"
try {
    Invoke-WebRequest -Uri $collectorHealthz -UseBasicParsing -TimeoutSec 5 | Out-Null
} catch {
    Write-Host "collector 不可达（$collectorHealthz）——请先 make up 起全栈" -ForegroundColor Red
    exit 2
}

$steps = @(
    @{ Name = "e2e_ws";                     Cmd = { python test/e2e_ws.py } }
    @{ Name = "e2e_central_hub_assertions"; Cmd = { python test/e2e_central_hub_assertions.py } }
    @{ Name = "e2e_context";                Cmd = { python test/e2e_context.py } }
    @{ Name = "e2e_memory";                 Cmd = { python test/e2e_memory.py } }
    @{ Name = "e2e_resilience";             Cmd = { python test/e2e_resilience.py } }
    @{ Name = "e2e_process_region";         Cmd = { python test/e2e_process_region.py } }
    @{ Name = "e2e_trip";                   Cmd = { python test/e2e_trip.py } }
    @{ Name = "e2e_research";               Cmd = { python test/e2e_research.py } }
    @{ Name = "e2e_research_async";         Cmd = { python test/e2e_research_async.py } }
    @{ Name = "e2e_real_providers";         Cmd = { python -m pytest test/e2e_real_providers.py -q -s } }
)

$results = @()
foreach ($step in $steps) {
    Write-Host ""
    Write-Host "===== $($step.Name) =====" -ForegroundColor Cyan
    & $step.Cmd
    $results += [pscustomobject]@{ Name = $step.Name; RC = $LASTEXITCODE }
}

Write-Host ""
Write-Host "===== e2e 汇总 =====" -ForegroundColor Cyan
$fail = $false
foreach ($r in $results) {
    if ($r.RC -eq 0) {
        Write-Host ("  {0,-28} PASS" -f $r.Name) -ForegroundColor Green
    } else {
        Write-Host ("  {0,-28} FAIL(rc={1})" -f $r.Name, $r.RC) -ForegroundColor Red
        $fail = $true
    }
}
if ($fail) { exit 1 } else { exit 0 }
