#!/usr/bin/env bash
# 生成 gRPC 代码。等价于 `make proto`。需安装 buf: https://buf.build
set -euo pipefail
cd "$(dirname "$0")/.."
echo "[gen-proto] buf generate proto ..."
buf generate proto
echo "[gen-proto] done -> gen/python, gen/go"
