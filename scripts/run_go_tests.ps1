param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Package
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$packages = @()
if ($null -ne $Package) {
    $packages += $Package
}
if ($packages.Count -eq 0) {
    $packages = @("./...")
}

function Test-GoPackagePattern {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }
    if ($Value -eq ".") {
        return $true
    }
    if (-not $Value.StartsWith("./", [System.StringComparison]::Ordinal)) {
        return $false
    }
    $suffix = $Value.Substring(2)
    if ($suffix.Length -eq 0 -or $suffix.Contains("\")) {
        return $false
    }
    foreach ($segment in $suffix.Split("/")) {
        if (
            $segment.Length -eq 0 -or
            $segment -eq "." -or
            $segment -eq ".." -or
            $segment -notmatch '^[A-Za-z0-9_.-]+$'
        ) {
            return $false
        }
    }
    return $true
}

foreach ($item in $packages) {
    if ($item.StartsWith("-", [System.StringComparison]::Ordinal)) {
        throw "Go package must not start with '-'"
    }
    if (-not (Test-GoPackagePattern -Value $item)) {
        throw "Go package must be a repository-relative package pattern"
    }
}

function Get-GoFileHashes {
    param([string]$Root)
    $modPath = Join-Path $Root "go.mod"
    $sumPath = Join-Path $Root "go.sum"
    $modHash = if (Test-Path -LiteralPath $modPath) {
        (Get-FileHash -LiteralPath $modPath -Algorithm SHA256).Hash
    } else {
        "<missing>"
    }
    $sumHash = if (Test-Path -LiteralPath $sumPath) {
        (Get-FileHash -LiteralPath $sumPath -Algorithm SHA256).Hash
    } else {
        "<missing>"
    }
    return "$modHash`n$sumHash"
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required to run Go tests"
}

$beforeHashes = Get-GoFileHashes -Root $repoRoot

# 容器里要跑的必须是 `go test "$@"`——包名靠这对**真引号**正确分词，而包名本身是作为
# 位置参数传进去的（`sh -c <cmd> sh @packages`），不拼进命令串：即使上面的
# Test-GoPackagePattern 哪天被放宽，也注入不进 shell 语法。这条不能动。
#
# 但这对引号在**本文件里怎么写**，取决于 PowerShell 把参数交给原生程序的方式：
#   - Legacy（Windows PowerShell 5.1）：参数被拼进命令行字符串，接收方用
#     CommandLineToArgvW 反解，所以源码里要写 `\"`，docker 才收到 `"`；
#   - Standard（pwsh 7 在非 Windows 上的默认）：参数**逐字**经 argv 交出去，
#     写 `\"` 就把反斜杠原样送进了容器 —— sh 里 `\"` 是字面量引号，
#     `go test` 会收到带引号的包名。
#
# **所以这条 wrapper 在 Linux 上一直是坏的，只是从来没人在 Linux 上跑过它**
# （CI 的 test_run_go_tests_wrapper argv 比对就是这么红的）。判据必须是「怎么传参」
# 而不是「什么系统」：pwsh 7 在 Windows 上默认也是 Standard（除非接收方是
# .cmd/.bat 一类，那才回落 Legacy——本仓 Windows 侧走 powershell.exe，不涉及）。
$argumentPassing = Get-Variable -Name PSNativeCommandArgumentPassing `
    -ValueOnly -ErrorAction SilentlyContinue
$quote = if ($null -ne $argumentPassing -and "$argumentPassing" -ne 'Legacy') {
    '"'
} else {
    '\"'
}
$containerCommand = "cp -a /src/. /work/ && cd /work && go mod tidy && go test $quote`$@$quote"
$dockerExit = 1
try {
    & docker run --rm `
        -v "${repoRoot}:/src:ro" `
        -w /work `
        golang:1.24-bookworm `
        sh -c $containerCommand sh @packages
    $dockerExit = $LASTEXITCODE
} finally {
    $afterHashes = Get-GoFileHashes -Root $repoRoot
    if ($beforeHashes -ne $afterHashes) {
        throw "Host go.mod or go.sum changed during read-only Go test wrapper"
    }
}
if ($dockerExit -ne 0) {
    throw "Dockerized Go tests failed with exit code $dockerExit"
}
