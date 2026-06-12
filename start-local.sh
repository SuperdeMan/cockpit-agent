#!/bin/bash
# 本地模式启动各服务（不依赖 Docker）
# 用法：bash start-local.sh
# 前置：已装 Python 依赖（pip install grpcio protobuf pyyaml httpx redis）
# 停止：taskkill //F //IM python.exe（Windows）或 kill $(jobs -p)（Linux/Mac）

set -e
cd "$(dirname "$0")"

# 加载 .env
set -a
source .env 2>/dev/null || true
set +a

# 本地覆盖
export PYTHONPATH="$(pwd):$(pwd)/gen/python"
export REGISTRY_ADDR="localhost:50051"
export LLM_GATEWAY_ADDR="localhost:50052"
export MEMORY_ADDR="localhost:50053"
export CLOUD_PLANNER_ADDR="localhost:50054"
export REDIS_URL=""  # 无 Redis 用内存兜底

echo "=== 启动服务 ==="
echo "  PYTHONPATH=$PYTHONPATH"
echo "  LLM_PROVIDER=$LLM_PROVIDER"
echo "  LLM_API_KEY=${LLM_API_KEY:0:8}..."
echo ""

# 启动函数
start_svc() {
    echo "[start] $1 ($2)"
    python "$2" &
    sleep 1
}

start_svc "Registry"          "registry/main.py"
start_svc "LLM Gateway"       "llm-gateway/main.py"
start_svc "Memory"            "memory/main.py"
start_svc "Cloud Planner"     "orchestrator/cloud/main.py"
start_svc "Edge Orchestrator" "orchestrator/edge/main.py"

echo ""
echo "=== 所有服务已启动（后台） ==="
echo "验证：python test/smoke_edge.py"
echo "停止：taskkill //F //IM python.exe"
echo ""

# 等待所有后台进程
wait
