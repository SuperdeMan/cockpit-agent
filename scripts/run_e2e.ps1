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
    @{ Name = "e2e_degrade";                Cmd = { python test/e2e_degrade.py } }
    @{ Name = "e2e_voice_loop";             Cmd = { python test/e2e_voice_loop.py } }
    @{ Name = "e2e_reminder";               Cmd = { python test/e2e_reminder.py } }
    # M3 P1：位置提醒围栏（建单→播种→进围栏触达→不重复）
    @{ Name = "e2e_geofence";               Cmd = { python test/e2e_geofence.py } }
    @{ Name = "e2e_scene";                  Cmd = { python test/e2e_scene.py } }
    # M3 P0：统一主动引擎（单条直通/DoD 合并/去重/断言复核/user_contract 豁免）
    @{ Name = "e2e_proactive";              Cmd = { python test/e2e_proactive.py } }
    # M3 P2：受控 MCP 桥（准入边界/只读/确认链/幂等/演示标注）
    @{ Name = "e2e_mcp";                    Cmd = { python test/e2e_mcp.py } }
    @{ Name = "e2e_rejection";              Cmd = { python test/e2e_rejection.py } }
    @{ Name = "e2e_tts_stream";             Cmd = { python test/e2e_tts_stream.py } }
    # M4 P1/P2：S2S 全双工最小闭环（自答/多轮上下文/escalate 逃逸/三层打断/回灌防黑洞）
    # 与韧性（宿主内 WS 代理注入断连 → 重连+摘要重注入 / DEGRADED 回落）——均需 DashScope key
    @{ Name = "e2e_s2s";                    Cmd = { python test/e2e_s2s.py } }
    @{ Name = "e2e_s2s_resilience";         Cmd = { python test/e2e_s2s_resilience.py } }
    # M4 P4 声纹多用户（M4 最后一条 DoD）；模型缺失自动 SKIP
    @{ Name = "e2e_voiceprint";             Cmd = { python test/e2e_voiceprint.py } }
    # M4 P4 视觉入口；无 DashScope key 自动 SKIP
    @{ Name = "e2e_vision";                 Cmd = { python test/e2e_vision.py } }
    # 旅程级（L3）：回归级必须绿；目标级红灯是能力标尺不拦退出码（--strict-target 才拦）。
    @{ Name = "e2e_journeys";               Cmd = { python test/e2e_journeys.py } }
    @{ Name = "e2e_real_providers";         Cmd = { python -m pytest test/e2e_real_providers.py -q -s } }
    @{ Name = "e2e_strict_stack";           Cmd = { python test/e2e_strict_stack.py } }
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
