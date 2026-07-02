# WS4 · 端云流式通道 — 实现级细化

> 依据：`phase1-implementation-plan.md` WS4、`cockpit-agent-architecture.md` §3.3、§8、§12.3
> 目标：把端↔云通道做到弱网可用、可鉴权、可降级，细化到可编码。读者：网关/端侧开发。
> 现状基线：Edge Orchestrator 每次请求新建 channel 调 Cloud Gateway（`server-streaming`，无连接复用/重连/鉴权）。
>
> **实现补充（2026-06-14）**：中枢调用车端快能力已落地。`channel.proto` 包含
> `EdgeCall`/`EdgeResult`，Cloud Gateway 已实现 `DispatchToEdge`，端侧在同一 bidi
> 流中经 VAL 执行并回传。本文 §1 帧清单是原始设计快照，新增帧以
> [`../../design/2026-06-14-cloud-central-orchestrator.md`](../../design/2026-06-14-cloud-central-orchestrator.md)
> §4.5 和代码为准。
>
> **实现补充（2026-07-02 · R2.3 持久长连）**：方案 B（持久多路复用）**已落地**——但持久
> `ChannelClient` 位于 **Edge Orchestrator（Python `orchestrator/edge/cloud_client.py`）**，
> 而非本文 §3 所画的 `gateway/edge/`（Go 网关那份 `ChannelClient` 从未接线，已作为死代码删除；
> 真实链路 HMI—WS→ edge-gateway —gRPC `EdgeOrchestrator.Handle`→ edge-orchestrator —bidi→
> cloud-gateway）。edge-orchestrator 维持**单条持久 bidi**：`corr_id` 多路复用、15s 心跳、
> 指数退避重连、每次重连重建 channel 走 `dns:///` 重解析（换 IP 自愈）、在途请求断连快速失败由
> 上层降级。**下方 §3「组件与职责」的 `gateway/edge/ChannelClient` 行为历史设计，以本注与代码为准。**
> 落地记录见 [`../../design/2026-07-02-r2.3-edge-cloud-persistent-channel.md`](../../design/2026-07-02-r2.3-edge-cloud-persistent-channel.md)。

---

## 1. 连接模型决策

| 方案 | 说明 | 取舍 |
|---|---|---|
| A. per-request server-streaming（现状） | 每条用户请求新开 gRPC 流，结束即关 | 简单，但无法承载主动下发（云→端推送）、握手开销、弱网重建代价 |
| **B. 持久双向流 + 多路复用（选用）** | 端云间维持 1 条长生命周期 gRPC **bidi stream**，所有请求/响应/主动事件按 `correlation_id` 多路复用 | 支持主动下发(proactive)、低重建开销、心跳易做；复杂度可控 |

**决策：B**。新增一个会话层 proto，承载多路复用与心跳；CloudPlanner.Handle 的"每请求语义"封装在帧里。

### 新增 proto（`proto/cockpit/channel/v1/channel.proto`）
```proto
syntax = "proto3";
package cockpit.channel.v1;
import "cockpit/orchestrator/v1/orchestrator.proto";

service EdgeCloudChannel {
  // 端发起的长连接双向流。一条连接复用所有请求与主动下发。
  rpc Connect (stream UpFrame) returns (stream DownFrame);
}

message UpFrame {
  string correlation_id = 1;            // 关联一次请求-响应
  oneof body {
    Hello hello = 2;                    // 握手（首帧）
    cockpit.orchestrator.v1.HandleRequest request = 3;
    Ack ack = 4;                        // 对 down 帧的确认（幂等/可靠性）
    Ping ping = 5;
  }
}
message DownFrame {
  string correlation_id = 1;
  oneof body {
    HelloAck hello_ack = 2;
    cockpit.orchestrator.v1.HandleEvent event = 3;
    Proactive proactive = 4;            // 主动下发（如低电量提醒）
    Pong pong = 5;
  }
}
message Hello { string vehicle_id = 1; string session_resume_token = 2; map<string,string> meta = 3; }
message HelloAck { bool ok = 1; string reason = 2; }
message Ack { string frame_id = 1; }
message Ping { int64 ts = 1; }
message Pong { int64 ts = 1; }
message Proactive { string type = 1; string speech = 2; }
```

> 兼容：Edge Orchestrator 内部对上层仍暴露 `EdgeOrchestrator.Handle`；通道层只替换"端→云"这一跳的传输。Cloud Gateway 实现 `EdgeCloudChannel`，把 `request` 帧解复用后转 `CloudPlanner.Handle`，结果帧回填 `correlation_id`。

---

## 2. 组件与职责

```
gateway/edge/   ChannelClient   维持到云的 bidi 长连；重连；心跳；correlation 多路复用；幂等去重
gateway/cloud/  ChannelServer   握手鉴权；解复用 -> CloudPlanner.Handle；主动下发；会话登记
```

端侧 `ChannelClient` 对 Edge Orchestrator 暴露一个本地接口 `call(request) -> stream events`，内部映射为通道帧。

---

## 3. 心跳与连接健康

- **应用层心跳**：端每 `Tping=10s` 发 `Ping`，云回 `Pong`。连续 `3` 次无 Pong（或 gRPC keepalive 超时）判定连接失效 → 触发重连。
- **gRPC keepalive**（传输层兜底）：`keepalive_time=10s, keepalive_timeout=5s, permit_without_stream=true`。
- 健康状态机：`CONNECTING → CONNECTED → DEGRADED(无 pong) → RECONNECTING → CONNECTED`。`DEGRADED/RECONNECTING` 即触发降级（§5）。

---

## 4. 断线重连 + 幂等

**重连策略**：指数退避 + 抖动，`min=0.5s, max=15s`，`delay=min(max, base*2^n)+rand(0,0.5s)`。重连成功后发 `Hello{session_resume_token}` 恢复会话上下文（云侧据 token 找回挂起的多轮状态）。

**幂等（指令不重复执行）**：
- 每个 `UpFrame.request` 带全局唯一 `correlation_id`（端生成，含 vehicle_id+单调序号）。
- 云侧维护 `seen_correlation_ids`（Redis，TTL 10min）。重连后端可能重发未确认请求；云侧命中已处理 → 直接回放缓存结果（或回 `already_done`），不重复编排/不重复下发车控。
- 端侧对**车控类 down action** 也做幂等：按 `correlation_id + action 序号` 去重，避免重连后重复执行。

```python
# 云侧解复用伪码
async def on_request(frame):
    if await idem.seen(frame.correlation_id):
        await replay_cached(frame.correlation_id); return
    await idem.mark(frame.correlation_id, ttl=600)
    async for ev in planner.Handle(frame.request):
        await idem.cache_event(frame.correlation_id, ev)   # 供重连回放
        await send_down(frame.correlation_id, ev)
```

---

## 5. 降级矩阵落地（§3.3）

降级由 Edge Orchestrator 依据"连接健康 + 调用结果"决策，集中在 `degrade.py`：

| 触发条件 | 检测点 | 行为 |
|---|---|---|
| 断网 / 连接 DEGRADED/RECONNECTING | ChannelClient 状态 | 慢意图不出端：返回"网络不可用，已本地处理基础指令"；车控/媒体走端侧本地（始终可用） |
| 云连上但 Planner 错误 | down 帧 error / 超时 | 重试 1 次 → 仍失败回澄清话术；可选端侧 SLM 兜底简单问答 |
| 单 Agent 故障 | （云内 WS3 处理） | 云侧 Planner 跳过/降级 fallback，端侧透传结果 |
| LLM 超时 | down 首帧超 budget | 云侧 LLM Gateway 重试备用模型；端侧显示"思考中"占位 |

**关键不变量**：无论云侧状态如何，端侧 Fast Intent 命中的车控/媒体指令永远本地执行、永不阻塞在网络上。

```python
class Degrader:
    def route(self, intent, channel_state) -> str:
        if intent and is_local(intent.name):     # 车控/媒体永远本地
            return "local"
        if channel_state in ("DEGRADED", "RECONNECTING", "OFFLINE"):
            return "degrade"                      # 不出端
        return "cloud"
```

---

## 6. 鉴权（设备证书 + 会话 token）

- **传输层**：端云之间 mTLS。车辆设备证书（一车一证，制造/激活时下发）作为客户端证书；Cloud Gateway 校验证书链 + 吊销列表(CRL/OCSP)。
- **应用层**：`Hello` 帧带会话 token（JWT，短期，含 vehicle_id/scope/exp）。云侧校验签名与有效期，绑定连接。token 过期 → 云回 `HelloAck{ok=false, reason="token_expired"}`，端走 token 刷新流程后重连。
- **授权**：连接级确定 `granted_permissions`（由 token 的 scope 决定），随 `HandleRequest.context` 传入，供 WS8 权限校验。

> PoC→Phase1 过渡：先 token（JWT）跑通鉴权与授权链路；mTLS 证书体系可与车厂 PKI 对齐后接入（标 `TODO: mTLS PKI`）。

---

## 7. 边界与异常

| 情况 | 处理 |
|---|---|
| 重连风暴（大量车同时断连） | 退避 + 抖动；云侧连接限流；分批重连 |
| 半开连接（端以为通、实则断） | 心跳超时主动断开重连 |
| token 过期但流仍在 | 云侧定期校验，过期则发控制帧要求刷新；宽限期内不中断当前请求 |
| 重连后会话已 TTL 过期 | `session_resume_token` 失效 → 作为新会话，提示用户重说 |
| correlation_id 冲突 | 端生成保证单调唯一；云侧冲突直接拒绝该帧 |

---

## 8. 测试点（DoD）

**单元**：退避算法（边界/封顶/抖动范围）；幂等去重（重复 correlation_id 命中缓存）；降级路由表（各状态×意图类型）。

**集成（需端云起）**：
- 正常：长连接复用多请求，correlation 正确配对。
- 弱网注入（toxiproxy/tc）：丢包/延迟下心跳触发 DEGRADED → 重连 → 会话恢复。
- 断网：拔网后车控本地秒回、慢意图给降级话术；恢复后自动重连。
- 幂等：重连重发同一请求，车控只执行一次（断言 VAL 调用计数）。
- 鉴权：无效/过期 token 被拒；mTLS 证书非法被拒。

**混沌**：随机断连/延迟/重启 cloud-gateway，端到端不崩、无重复车控。

---

## 9. 任务清单（建议拆 PR）

1. `channel.proto` + codegen；Cloud Gateway 实现 `EdgeCloudChannel`（解复用→Planner，回填 correlation）。
2. 端侧 `ChannelClient`：bidi 长连 + 多路复用 + 对内 `call()` 接口（替换现 cloud_client 直连）。
3. 心跳 + 健康状态机 + gRPC keepalive 参数。
4. 重连（退避抖动）+ `Hello` 会话恢复。
5. 幂等：correlation 去重 + 车控 action 去重 + 重连回放缓存。
6. `degrade.py` 降级矩阵 + 与 Fast Intent/本地路径联动。
7. 鉴权：JWT token 握手与授权注入（mTLS 标 TODO 对齐 PKI）。
8. 弱网/断网/混沌测试套件。
