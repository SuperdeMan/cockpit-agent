# 本地模式启动各服务（不依赖 Docker）
# 用法：.\start-local.ps1
# 前置：已装 Python 依赖（pip install grpcio protobuf pyyaml httpx redis）
# 停止：Ctrl+C 或关闭各终端窗口

$ROOT = $PSScriptRoot
$env:PYTHONPATH = "$ROOT;$ROOT\gen\python"

# 加载 .env
if (Test-Path "$ROOT\.env") {
    Get-Content "$ROOT\.env" | Where-Object { $_ -notmatch '^\s*#' -and $_ -match '=' } | ForEach-Object {
        $parts = $_ -split '=', 2
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
    Write-Host "[start-local] .env loaded" -ForegroundColor Green
}

# 本地模式覆盖：服务地址改为 localhost
$env:REGISTRY_ADDR = "localhost:50051"
$env:LLM_GATEWAY_ADDR = "localhost:50052"
$env:MEMORY_ADDR = "localhost:50053"
$env:CLOUD_PLANNER_ADDR = "localhost:50054"
$env:CLOUD_GATEWAY_ADDR = "localhost:8080"
$env:REDIS_URL = ""  # 无 Redis 时用内存兜底
$env:LOG_LEVEL = "info"

Write-Host ""
Write-Host "=== 启动服务 ===" -ForegroundColor Cyan
Write-Host "  1. Registry          :50051" -ForegroundColor Gray
Write-Host "  2. LLM Gateway       :50052 (含 ASR/TTS)" -ForegroundColor Gray
Write-Host "  3. Memory            :50053" -ForegroundColor Gray
Write-Host "  4. Cloud Planner     :50054" -ForegroundColor Gray
Write-Host "  5. Edge Orchestrator :50070" -ForegroundColor Gray
Write-Host ""
Write-Host "Go 服务（Gateway）需要 Docker 或 Go toolchain，本地模式跳过" -ForegroundColor Yellow
Write-Host "HMI 需要 Node.js，本地模式跳过" -ForegroundColor Yellow
Write-Host ""
Write-Host "按 Ctrl+C 停止所有服务" -ForegroundColor Red
Write-Host ""

# 启动函数
function Start-Service($Name, $ScriptPath, $Port) {
    Write-Host "Starting $Name on :$Port ..." -ForegroundColor Green
    Start-Process -FilePath "python" -ArgumentList $ScriptPath -WorkingDirectory $ROOT -NoNewWindow -PassThru | Out-Null
}

# 1. Registry
Start-Service "Registry" "registry\main.py" 50051

# 2. LLM Gateway (含 ASR/TTS)
Start-Service "LLM Gateway" "llm-gateway\main.py" 50052

# 3. Memory
Start-Service "Memory" "memory\main.py" 50053

# 4. Cloud Planner
Start-Service "Cloud Planner" "orchestrator\cloud\main.py" 50054

# 5. Edge Orchestrator
Start-Service "Edge Orchestrator" "orchestrator\edge\main.py" 50070

Write-Host ""
Write-Host "所有服务已启动（后台进程）" -ForegroundColor Green
Write-Host "验证：python test\smoke_edge.py" -ForegroundColor Cyan
Write-Host "停止：Get-Process python | Stop-Process" -ForegroundColor Red
