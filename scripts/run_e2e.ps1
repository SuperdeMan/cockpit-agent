$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$runner = Join-Path $repoRoot "scripts/run_e2e.py"

Push-Location -LiteralPath $repoRoot
try {
    & python $runner @args
    $runnerExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

exit $runnerExitCode
