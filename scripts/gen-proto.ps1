# 生成 gRPC 代码（Windows）。等价于 `make proto`。需安装 buf: https://buf.build
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
Write-Host "[gen-proto] buf generate proto ..."
buf generate proto
Write-Host "[gen-proto] done -> gen/python, gen/go"
