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
$containerCommand = 'cp -a /src/. /work/ && cd /work && go mod tidy && go test \"$@\"'
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
