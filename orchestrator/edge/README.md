# Edge Orchestrator（端侧编排器）

端侧"快系统"。内含 Fast Intent + 端侧车控/媒体 Agent + 模拟 VAL（单进程简化）。

## 流程
1. `fast_intent.classify` 判定快/慢意图。
2. 快意图（车控/媒体，高置信）→ `edge_agents` 经 `VAL` 本地执行 → 秒回（离线可用）。
3. 慢意图 → `cloud_client` 转发 Cloud Gateway → Cloud Planner，流式回传。
4. 云端回流的 `vehicle.control` action → 分发到 `VAL` 执行（规划/执行分离）。
5. 云端不可达 → 降级提示；车控仍可用。

## 安全约束
车控只经 `val.VAL` 下发（指令校验 + 安全态门控 + 高速禁开窗等）。

## Phase 1 已落地
- 端云双向流：Go Cloud Gateway（EdgeCloudChannel bidi）+ Go Edge Gateway（ChannelClient 重连+心跳+多路复用）
- 云端 action 分发：车控类回流到 VAL 执行
- 连接状态追踪 + 降级增强

## 待办
- TODO(Phase1): 拆分独立的端侧车控/媒体 Agent；Fast Intent 接端侧小模型；阈值 OTA。
- TODO(Phase2): VAL 接真实 SOME-IP/CAN；端侧件 C++/Rust 化。
