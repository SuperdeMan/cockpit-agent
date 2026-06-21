# Edge Orchestrator（端侧编排器）

端侧"快系统"。内含 Fast Intent + 端侧车控/媒体 Agent + 模拟 VAL（单进程简化）。

## 流程
1. Fast Intent 对单意图或多意图片段做结构化分类。
2. 完全本地且无需确认的语义组经 `VAL` 秒回；本地轮 best-effort 写共享记忆。
3. 含导航、歌手/歌曲限定等慢片段的语义组完整上云，避免丢上下文或重复本地执行。
4. 云端可通过 `edge_call` 调用本车快能力；所有车控仍由 `EdgeCallExecutor → VAL` 执行。
5. 云端回流 action 做来源校验，已由 edge VAL 执行的动作只展示、不二次下发；未执行的
   `vehicle.control` 经 `edge_call.action_to_structured` 翻成 VAL 结构化命令走完整流水线
   （含安全门控），翻译失败再回退 legacy 串。
6. 云端不可达时给出降级提示，纯本地安全快路径仍可用。

## 安全约束
车控只经 `val.VAL` 下发（指令校验 + 安全态门控 + 高速禁开窗等）。

## Phase 1 已落地
- 端云双向流：Go Cloud Gateway（EdgeCloudChannel bidi）+ Go Edge Gateway（ChannelClient 重连+心跳+多路复用）
- `edge_call`→VAL、动作卡回传与防双发
- 混合意图语义分组、本地/云端分流、危险动作确认
- 连接状态追踪、端侧轮记忆与降级增强
- VAL 状态 diff/启动快照、route/VAL span 经 NATS best-effort 发出
- collector debug 仅允许 `speed_kmh/battery/gear/location` 四类模拟环境量

## 待办
- Fast Intent 接端侧小模型、规则/阈值 OTA。
- TODO(Phase2): VAL 接真实 SOME-IP/CAN；端侧件 C++/Rust 化。
