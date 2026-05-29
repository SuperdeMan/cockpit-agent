# Edge Orchestrator（端侧编排器）

端侧"快系统"。PoC 内含 Fast Intent + 端侧车控/媒体 Agent + 模拟 VAL（单进程简化）。

## 流程
1. `fast_intent.classify` 判定快/慢意图。
2. 快意图（车控/媒体，高置信）→ `edge_agents` 经 `VAL` 本地执行 → 秒回（离线可用）。
3. 慢意图 → `cloud_client` 转发 Cloud Gateway → Cloud Planner，流式回传。
4. 云端不可达 → 降级提示；车控仍可用。

## 安全约束
车控只经 `val.VAL` 下发（指令校验 + 安全态门控）。LLM/云端产出的车控 action 在量产中也须回流到 VAL 校验执行（本 PoC 中车控走端侧本地路径）。

## 待办
- TODO(Phase1): 拆分独立的端侧车控/媒体 Agent；Fast Intent 接端侧小模型；阈值 OTA；端云双向流式长连接（断线重连/心跳）。
- TODO(Phase2): VAL 接真实 SOME-IP/CAN；端侧件 C++/Rust 化。
