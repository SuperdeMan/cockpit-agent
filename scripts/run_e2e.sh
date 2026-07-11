#!/usr/bin/env bash
# 本地完整 e2e 脚本清单执行器。由 `make e2e` 调用（也可直接 bash scripts/run_e2e.sh）。
#
# 与 .github/workflows/nightly-e2e.yml 的关系（刻意不同，勿合并）：
#   nightly 跑「裁剪、无需任何密钥即可确定性全绿」的子集（--case 过滤掉依赖真实 LLM
#   路由/embedding 的用例）；本脚本跑「全量」，假定本机 .env 可能配了真实 key，追求更
#   完整的真实覆盖。若你的 .env 未配置 LLM_API_KEY/LLM_EMBED_API_KEY，以下用例预期
#   会合理失败或（e2e_memory 链路1/3）优雅 SKIP，这是 Mock 下的已知限制，不是回归。
#   详见 docs/design/2026-07-03-r3.3-e2e-ci-gate.md。
#
# 前置：`make up` 起好全栈。依赖：pip install websockets（多数服务 requirements.txt 已含）。
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."   # 仓库根

COLLECTOR_HEALTHZ="http://localhost:8092/healthz"
if ! curl -sf "$COLLECTOR_HEALTHZ" >/dev/null 2>&1; then
    echo "collector 不可达（$COLLECTOR_HEALTHZ）——请先 make up 起全栈" >&2
    exit 2
fi

declare -a NAMES=()
declare -a RESULTS=()
overall=0

run_step() {
    local desc="$1"; shift
    echo ""
    echo "===== ${desc} ====="
    "$@"
    local rc=$?
    NAMES+=("$desc")
    if [ "$rc" -eq 0 ]; then
        RESULTS+=("PASS")
    else
        RESULTS+=("FAIL(rc=${rc})")
        overall=1
    fi
}

run_step "e2e_ws"                     python test/e2e_ws.py
run_step "e2e_central_hub_assertions" python test/e2e_central_hub_assertions.py
run_step "e2e_context"                python test/e2e_context.py
run_step "e2e_memory"                 python test/e2e_memory.py
run_step "e2e_resilience"             python test/e2e_resilience.py
run_step "e2e_process_region"         python test/e2e_process_region.py
run_step "e2e_trip"                   python test/e2e_trip.py
run_step "e2e_research"               python test/e2e_research.py
run_step "e2e_research_async"         python test/e2e_research_async.py
run_step "e2e_degrade"                python test/e2e_degrade.py
run_step "e2e_voice_loop"             python test/e2e_voice_loop.py
run_step "e2e_reminder"               python test/e2e_reminder.py
run_step "e2e_rejection"              python test/e2e_rejection.py
run_step "e2e_tts_stream"             python test/e2e_tts_stream.py
run_step "e2e_real_providers"         python -m pytest test/e2e_real_providers.py -q -s

echo ""
echo "===== e2e 汇总 ====="
for i in "${!NAMES[@]}"; do
    printf '  %-28s %s\n' "${NAMES[$i]}" "${RESULTS[$i]}"
done

exit $overall
