# 智能座舱 Multi-Agent 架构设计方案

> 版本：v1.52（现状校准 + v2 目标分册；版本规则见附录 C）
> 日期：2026-09-26；源码校准基线 `47c62b44`，本次没有新增运行时实现。
> 读者对象：架构师、后端/端侧/算法开发、HMI 开发、测试、项目经理
> 范围：座舱 AI Agent 系统的整体架构、组件职责、接口契约、数据流、安全、选型、部署、分阶段落地路线
> 实现说明（2026-07-18 校准）：当前仓库完成的是该架构的工程化 PoC 主干；持久化注册
>（Registry PgStore）、首批真实 Provider（高德/和风/Exa/Tushare/api-football）、服务间
> mTLS 与会话鉴权（env 门控默认关）、Prometheus/OTel 导出均已落地；文中的 K8s、正式
> 沙箱、真实 VAL（SOME-IP/CAN、车规适配）仍是目标态。实现现状和差距以 `AGENTS.md`
> 与 [QA/发布交接](../reviews/2026-08-30-qa-closeout-handoff.md)为准。后续排序以 [路线图](../roadmap.md)为准。
> v2 目标独立写在 [目标分册](cockpit-agent-v2-target-architecture.md)，其中 Decide、ResultBundle、T1e 等均未实现。

---

## 0. 一页速读（TL;DR）

- **架构范式**：分层混合编排（Hierarchical Hybrid）。端侧"快系统"负责高频、确定性、安全敏感指令（车控/媒体）的毫秒级本地执行与离线兜底；云侧"慢系统"用 LLM Planner 编排复杂、多轮、跨域组合意图。
- **Agent 生态**：所有 Agent 实现统一 gRPC 契约 + 声明 Manifest，经注册中心即插即用。分为 `core`（系统内置、安全敏感）与 `ecosystem`（可插拔、可第三方：周边发现/停车/车书/行程/闲聊等）两类，安全等级不同。
- **云边协同**：规则快路径与云侧 Planner 分工；端侧 NLU 仍是 shadow，断网使用已实现的确定性本地能力，不承诺端侧模型或完整离线语音。
- **技术栈**：Go（接入网关，高并发/WebSocket）+ Python（编排器与各 Agent，AI 生态最好）+ gRPC（服务间同步调用）+ 事件总线（异步/广播/车控事件）+ React（座舱 HMI）。
- **后续节奏**：R0 基线 → R1 契约/可信状态 → R2 持久执行 → R4 车辆服务 → R5 集成试点；R3 端侧规划与 Jev 为可选支线。旧 Phase 1 保留作历史差距基线，R5 不等于量产。

---

## 1. 设计目标与约束

### 1.1 业务目标
1. 用户用自然语言（语音为主、触控/多模态为辅）完成座舱内绝大多数任务：车控、导航、娱乐、信息、以及不断扩展的生态服务（周边发现、停车缴费、车书问答、行程规划、闲聊等）。
2. 支持**跨 Agent 的组合意图**（一句话触发多个 Agent 协作完成一个目标）。
3. 生态可持续扩展：新增一个 Agent **不修改编排核心代码**，通过注册接入。

### 1.2 关键约束（架构硬约束，按优先级）
| 优先级 | 约束 | 含义 |
|---|---|---|
| P0 | **安全** | 车控类操作必须确定性执行、可权限校验、危险操作二次确认，绝不交由 LLM 自由决策直接下发 |
| P0 | **时延** | 高频指令（车控/媒体）端到端 < 500ms 且离线可用；复杂云端意图首响 < 1.5s |
| P0 | **可用性/降级** | 断网、云端故障时，车控/媒体/基础问答仍可用（端侧兜底） |
| P1 | **可扩展性** | Agent 即插即用，编排核心对 Agent 数量与种类无感 |
| P1 | **隐私合规** | 敏感数据（位置、车内音视频、支付）默认不出车；上云数据最小化、可审计 |
| P2 | **可量产** | 车规适配（SoC 算力约束、SOA/CAN 对接、OTA、资源占用、稳定性） |

### 1.3 非目标（YAGNI，本期不做）
- 不做完全自主的 AGI 式 Agent 自由协商（多 Agent 群聊式自组织）——量产不可控。
- 不自研 ASR/TTS/LLM 基础模型——通过 Gateway 接入成熟方案，保留可替换性。
- 不做跨车队的多车协同。
- 第一版不做端侧大模型微调闭环（端侧仅用现成小模型 + 规则）。

---

## 2. 总体架构

### 2.1 分层架构图

下图沿用既有总体分层，含尚未落地的 K8s、端侧 SLM 与真实总线目标；不是生产拓扑证明。
v2 的增量组件、语义权威和迁移边界见 [目标分册 §2](cockpit-agent-v2-target-architecture.md#2-目标数据流)。

```mermaid
flowchart TB
    subgraph UI["① 交互层 (HMI & 感知)"]
        Voice["语音: 唤醒/ASR/TTS"]
        Touch["触控 HMI (React)"]
        Sensor["多模态感知: 摄像头/车内传感"]
        App["Android 陪伴端 App (RN+Expo)<br/>手机 / 平板 / 折叠屏, 见 §2.4"]
    end

    subgraph EDGE["② 端侧 (车机 In-Vehicle, e.g. 高通8295)"]
        EdgeGW["Edge Gateway 接入网关 (Go)"]
        EdgeOrch["Edge Orchestrator 端侧编排器"]
        FastIntent["Fast Intent 快意图分类器<br/>(规则 + 端侧小模型)"]
        EdgeAgents["端侧核心 Agent<br/>车控 / 媒体 (本地执行)"]
        EdgeLLM["端侧 SLM 小模型 (降级兜底, 可选)"]
        LocalCtx["本地上下文 / 车辆状态缓存"]
    end

    subgraph VEH["③ 车控域 (Vehicle Domain)"]
        VAL["车控抽象层 VAL<br/>(SOME-IP / VSOA / CAN 适配)"]
        ECU["域控 / 各 ECU"]
    end

    subgraph CLOUD["④ 云侧 (Cloud, K8s)"]
        CloudGW["Cloud Gateway"]
        Planner["Cloud Planner<br/>LLM 编排器 (Supervisor)"]
        LLMGW["LLM Gateway<br/>多模型路由 / 降级 / 限流"]
        Registry["Agent Registry 注册中心"]
        Memory["记忆 / 用户画像服务"]
        Bus["事件总线 (异步/广播)"]

        subgraph CORE["核心 Agent (core)"]
            NavA["导航 Agent"]
            InfoA["信息 Agent"]
        end
        subgraph ECO["生态 Agent (ecosystem, 可插拔)"]
            ChatA["闲聊"]
            TripA["行程规划"]
            ManualA["车书 (RAG)"]
            NearA["周边发现"]
            ParkA["停车缴费"]
        end
    end

    Voice & Touch & Sensor --> EdgeGW
    App -->|"同一 WS/HTTP 契约, 同 user_id, 独立会话"| EdgeGW
    EdgeGW --> EdgeOrch --> FastIntent
    FastIntent -->|"快意图: 本地秒回"| EdgeAgents --> VAL --> ECU
    FastIntent -->|"慢意图: 上云"| CloudGW
    EdgeOrch -.->|"断网降级"| EdgeLLM
    EdgeOrch <--> LocalCtx
    CloudGW <-->|"双向流式长连接"| EdgeGW
    CloudGW --> Planner
    Planner --> LLMGW
    Planner --> Registry
    Planner --> Memory
    Registry -.发现.-> CORE & ECO
    Planner -->|"调度"| CORE & ECO
    CORE & ECO --> LLMGW
    Planner -.->|"车控指令下发 (校验后)"| CloudGW
    Bus -.广播.-> Planner & EdgeGW
```

> （2026-09-08 校准）图中补入第二个用户端节点 `App`；两端一脑的三条架构约束见 §2.4，
> 多端必须一致的最小契约面登记在 `docs/conventions.md` §9.33。此前图里只有车机屏一个用户端。

### 2.2 核心组件职责清单

| 组件 | 部署 | 语言 | 职责 | 一句话边界 |
|---|---|---|---|---|
| **Edge Gateway** | 端 | Go | 接入语音/HMI/感知，会话保持，端云长连接，本地限流 | 所有交互的入口；不含业务逻辑 |
| **Edge Orchestrator** | 端 | Python/C++* | 端侧编排：调 Fast Intent，决定本地处理 or 上云，本地结果聚合，降级控制 | 端侧大脑；只做路由与降级决策 |
| **Fast Intent** | 端 | 规则+小模型 | 把用户输入快速分类为「快意图/慢意图」并抽槽位，给置信度 | 只判类型与槽位，不执行 |
| **车控/媒体端侧 Agent** | 端 | Python/C++* | 高频确定指令的本地执行 | 确定性执行，经 VAL 操作车身 |
| **VAL 车控抽象层** | 端 | C++ | 屏蔽 SOME-IP/VSOA/CAN 差异，统一车控 API，权限/安全校验 | 唯一能碰车身信号的层 |
| **Cloud Gateway** | 云 | Go | 端云通道服务端，鉴权，会话路由，流式下发 | 云侧入口 |
| **Cloud Planner** | 云 | Python | 复杂意图理解、任务规划、多 Agent 编排、结果聚合与话术生成 | 云侧大脑；编排不执行 |
| **LLM Gateway** | 云 | Python/Go | 多模型路由、Prompt 管理、缓存、限流、降级、成本与配额 | 所有 LLM 调用的唯一出口 |
| **Agent Registry** | 云 | Go/Python | Agent 注册/发现/健康，能力索引（供路由用） | Agent 黄页 |
| **核心/生态 Agent** | 云 | Python | 各自领域的能力实现，实现统一 Agent 契约 | 单一职责，可独立部署/测试 |
| **记忆/画像服务** | 云 | Python | 短期会话上下文、长期用户画像、车辆上下文 | 偏好/关系的权威；车态与任务事实仍属于各执行服务 |
| **事件总线** | 云+端 | NATS/Kafka | 异步事件、车控状态广播、主动服务触发 | 解耦异步与广播 |

> \* 端侧编排器与端侧 Agent 的语言：PoC 阶段可用 Python 快速验证；量产阶段对时延/资源敏感的部分（VAL、Fast Intent 推理）建议 C++/Rust，详见 §11、§14。

---

### 2.4 用户端不止一个：座舱 HMI 与 Android 陪伴端（2026-08-28 补记）

本文长期只画了 `hmi/` 一个用户端，这是**历史遗漏**：`mobile/`（React Native + Expo
的 Android 陪伴端）自 2026-08-25 起就是**第二个正式用户端**，经同一 edge-gateway WS /
llm-gateway HTTP·WS 接入同一个后端大脑。补记于此，免得接手者按「只有车机屏」来设计。

**架构上要记住的只有三条**：

1. **一个大脑，两个端**：同 `user_id`（记忆/画像跨端共享，这是「一个大脑」的产品含义），
   各自独立会话（App 会话前缀 `app-`），独立 `AUTH_TOKENS` 条目（可单独吊销；手机默认
   档 scope 不含 `vehicle.control`）。主动消息按 user 推送到**所有在线端**。
   多端一致性的最小契约面登记在 `docs/conventions.md` §9.33。

2. **共享的是判据，不是 UI**。两端**不复制**消息/卡片契约与会话语义：`mobile/` 只读直引
   `hmi/src` 的纯逻辑模块（`@shared/*`），准入由台账 `mobile/shared-allowlist.json` +
   守卫测试机器守。**这条不是工程洁癖，是架构约束**——「这句话说完了没有」
   （`sileroEndpoint.mjs`）、「这句话该不该抓一帧」（`visionFrame.mjs::needsFrame`）、
   「响应归谁」（`requestRouting.mjs`）这类判据一旦分叉，同一个用户在两个端会得到两个
   答案，而**没有任何东西会红**。同 `runtime/` 那批端云共用判定的落点判据。

3. **端侧能力两端不必对等，红线必须对等**。M4 起 App 侧也有端侧 VAD（onnxruntime）与
   唤醒词（sherpa-onnx），走的是与 HMI **同一份模型、同一组阈值**；而 §9 的三条红线
   （S2S 挡位默认三段式 + 唤醒后交互窗内才采音频、视觉单帧默认关 + 命中触发词才抓、
   声纹不作鉴权因子）在**每一个用户端**上都必须成立，不因为「手机不是车机」而放宽。

> 手机形态特有的两条产品约束：**不做后台保活**（PoC 承诺前台交互档，离线投递属
> §10 挂账的厂商推送）；**常开麦是采集面**，必须用户在设置里显式打开、默认关。

> （2026-09-08 校准）App 侧的交互模型与修复批次不在本文展开，入口：
> [UX v2.2「以光球为锚的三层在场」](../design/2026-08-29-mobile-ux-v2-presence-redesign.md)
> （在场派生态 + 语音层 + 承诺面 + 尺寸类 × 折叠姿态 × 行车档；确认策略只投影 VAL，
> UI 不另设车速门禁）、[2026-09-07 完整评审](../reviews/2026-09-07-android-ux-full-review.md)
> 与 [AR01–AR11 分批处理](../design/2026-09-07-android-review-remediation-batches.md)。
> 上面第 2 条「判据只许一份」在 App 内部同样成立：采集事实（`captureFacts`）与播放事实
> （`playbackFacts`）各自只有一份声明源，来自设备与真实播放器，而不是由轮态或 FSM 反推——
> AR02 / AR03 修的正是「UI 的『已停止』未必阻止传输、派生的采集态未必等于真实麦克风状态」。

---

## 3. 端云职责切分（云边协同核心）

云边协同的本质是**意图分级路由**：不是所有请求都上云，也不是所有都本地。

### 3.1 意图分级

| 级别 | 典型例子 | 处理位置 | 时延目标 | 依赖网络 |
|---|---|---|---|---|
| **L0 即时控制** | 开空调、关车窗、调座椅、上一首、暂停 | 端侧 Fast Intent + 端侧 Agent | < 500ms | 否 |
| **L1 简单查询** | 现在几点、剩余电量、当前导航还有多远 | 端侧（命中本地数据）| < 500ms | 否 |
| **L2 单域复杂** | "导航去最近的快充站"、"放点适合下雨天的歌" | 云侧单 Agent | < 1.5s 首响 | 是 |
| **L3 跨域组合** | "找家顺路评分高的川菜馆订今晚的位" | 云侧 Planner 编排多 Agent | < 2s 首响 + 流式 | 是 |
| **L4 开放对话/知识** | 闲聊、车书问答、行程建议 | 云侧（RAG/LLM）| < 2s 首响 | 是 |

> **实现对应（2026-07-18 校准）**：当前实现把上述分级落地为三层运行模型——**T0 端侧快路径**（L0/L1，毫秒级本地执行、离线可用）、**T1 云端单次 DAG**（L2/L3/L4 一次规划后确定性执行）、**T2 有界 Agentic 循环**（需按中间结果调整计划时进入，迭代次数与时间预算受控）。该术语与 `AGENTS.md`、README 同源，详见 `docs/design/2026-06-14-cloud-central-orchestrator.md`。

### 3.2 路由决策（端侧 Fast Intent 的判定逻辑）

当前执行链使用确定性规则和受控能力声明，先处理来源/权限、问句与动作边界，再分流：

```text
可信输入 → 确定性 FastIntent + 能力/参数/权限检查
          ├─ 明确且允许本地执行 → 端侧执行 → VAL
          ├─ 需确认 / 复杂 / 未命中 → 云端主链
          └─ 断网 → 仅已实现本地能力；其余诚实告知不可用
端侧 NLU → shadow 观测，不改变上述执行结果
```

历史的“双阈值中间档先执行、再异步校验”伪码不再作为实现指南；
阈值和模型置信度不构成授权。可选 T1e 的实验与放量门见 [v2 分册 §7](cockpit-agent-v2-target-architecture.md#7-端侧与生态适配)。

> **实现对应（2026-07-30 校准，M5 P3a；2026-08-01 P3 收尾更新）**：上述双阈值伪码写下半年后
> **第一次拿到真概率**——端侧语义 NLU（`orchestrator/edge/nlu.py`，4 层中文 encoder ONNX，
> 35MB/4.8ms）已建成**识别侧**，产出真 softmax 置信度；但**执行侧刻意未接**，挡位停在
> `EDGE_NLU_MODE=shadow`（只算不用）。**`on` 挡位刻意不存在**：θ 按 transfer 车道标定到
> 85% 覆盖须付出约 1% 请求识别成错对象的代价（三闸挡危险动作、挡不住「合法但不是用户要的
> 动作」），放量前提与开工判据见 `AGENTS.md` §4.2 与 v2 路线图 R3。当前生产路由仍由确定性规则
> `fast_intent` 承担，置信度仍是硬编码字面量——本节伪码的完整兑现在 P3b。
>
> **影子的观测面（P3 收尾补完）**：判定落独立 `nlu.shadow` span，四条路径全挂
> （`path`=local/multi/mixed/cloud——**误接与漏接必须分得开**，误接发生在本地快路径且
> 危险得多），响应后 fire-and-forget 不占秒回；`nlu_vs_rule` **四态**
> （agree/differ/rule_miss/unmapped）；`nlu_gate` 记 θ 双阈值会落哪一档（**只记不用**）。
> ⚠ 四态之所以曾经名不副实：模型输出的是**语料标签空间**的中文对象、规则输出的是**它自己
> 那套** object（95 个、38 个连 VAL 都没有），直接比字符串使 `agree` 不可达——同一件事的
> 三套命名由 `orchestrator/edge/knowledge/nlu_objects.yaml` 等价类台账归并（人裁一次、
> 机器守不许悄悄漏）。**「A 与 B 一致吗」这类判定，先问「A 和 B 是同一个空间里的量吗」。**

### 3.3 降级策略矩阵

| 故障 | 表现 | 降级行为 |
|---|---|---|
| 断网 | 云不可达 | 车控/媒体正常；复杂意图回复"网络不可用，已为您本地处理基础指令"；可选端侧 SLM 兜底简单问答 |
| 云 Planner 故障 | 网络通但编排挂 | LLM Gateway 直连单 Agent 兜底；或返回澄清话术 |
| 某 Agent 故障 | 单领域不可用 | Planner 跳过该 Agent，降级到 `fallback`（如 chitchat）并告知用户 |
| LLM 超时 | 首响超 budget | 流式占位 + 重试到备用模型（LLM Gateway 负责） |

> **实现说明（R3.5，`test/e2e_degrade.py` 自动化验证后如实记录，非本表原意）**：
> 实际降级话术与上表措辞有出入——断网是"网络不太好，复杂请求暂时无法处理，不过车内控制依然可以
> 正常使用"（`orchestrator/edge/server.py`），云 Planner 故障是"云端处理异常，请稍后重试"
> （`gateway/cloud/main.go`）。"LLM Gateway 直连单 Agent 兜底"未实现（纯 aspiration）。"某 Agent
> 故障降级到 fallback" 仅适用于**规划期**（LLM 完全无法产出计划时的全局兜底），**执行期**单个
> Agent 调用失败不会重路由到 chitchat，而是该步标 FAILED、DAG 其余步骤继续（`executor.py` 不因
> 单步失败中断）。"LLM 超时" 的"重试到备用模型"对非流式 `Complete()` 成立；流式
> `CompleteStream()` 自 2026-07-17 起**首 token 前**同样按档位链降级（首 token 后不切，
> 半段话术不可拼接——R3.5 记录的缺口已由运行时硬化 P1 兑现，见 §8.1）。其余差异
> （executor 错误信息丢失等）属已知技术债，详见
> `docs/design/2026-07-03-r3.5-degrade-matrix-e2e.md` §6。

---

## 4. Agent 模型与契约（架构的可扩展性基石）

### 4.1 Agent 分类

| 类别 | trust_level | 例子 | 特点 |
|---|---|---|---|
| **core（核心）** | `system` / `first_party` | 车控、媒体、导航、信息、语音 | 系统内置，可碰敏感能力（车控），随系统发版 |
| **ecosystem（生态）** | `first_party` / `third_party` | 周边发现、停车缴费、车书、行程规划、闲聊 | 可插拔，独立发版，第三方可接入，权限受限沙箱 |

不同 trust_level 对应不同的权限上限与审核要求（见 §9）。

### 4.2 统一 Agent 契约（gRPC）

所有 Agent（无论 core/ecosystem，无论端/云）实现同一份 gRPC 服务定义，这是"即插即用"的前提。

```proto
// proto/cockpit/agent/v1/agent.proto
syntax = "proto3";
package cockpit.agent.v1;

// 每个 Agent 都必须实现的统一服务契约
service Agent {
  rpc Describe (DescribeRequest) returns (AgentManifest);          // 上报能力清单(供注册/路由)
  rpc Execute  (ExecuteRequest)  returns (ExecuteResponse);        // 同步执行一个意图
  rpc ExecuteStream (ExecuteRequest) returns (stream ExecuteEvent);// 流式执行(长任务/流式话术)
  rpc Health   (HealthRequest)   returns (HealthResponse);         // 健康检查
}

message ExecuteRequest {
  string request_id = 1;
  string session_id = 2;
  Intent intent     = 3;                 // 已识别意图与槽位
  ContextRef context = 4;                // 上下文引用(按需取，不全量传)
  repeated ModalityRef modalities = 5;   // 多模态输入引用(音频/图像URI)
  map<string, string> meta = 6;          // trace_id、locale、vehicle_id 等
}

message Intent {
  string name = 1;                       // 命名空间化: "navigation.search_poi"
  map<string, string> slots = 2;         // 槽位
  float confidence = 3;
  string raw_text = 4;
}

message ExecuteResponse {
  enum Status { OK = 0; NEED_CONFIRM = 1; NEED_SLOT = 2; FAILED = 3; REJECTED = 4; }
  Status status = 1;
  string speech = 2;                     // 给 TTS 的播报话术
  google.protobuf.Struct ui_card = 3;    // 给 HMI 的结构化卡片(可选)
  repeated AgentAction actions = 4;      // 需执行的动作(如车控指令、跳转)
  string follow_up = 5;                  // 多轮追问/澄清提示
  ErrorInfo error = 6;
}

message AgentAction {
  string type = 1;          // "vehicle.control" | "navigate" | "play" | "open_app" ...
  google.protobuf.Struct payload = 2;
  bool require_confirm = 3; // 危险动作需用户二次确认
}

message ExecuteEvent {     // 流式: 边想边说边做
  oneof event {
    string speech_delta = 1;       // 流式话术增量
    AgentAction action = 2;
    ExecuteResponse final = 3;     // 终态
  }
}
```

> 说明：`ContextRef` 与 `ModalityRef` 用"引用"而非全量传值——上下文与多模态原始数据由记忆服务/对象存储托管，Agent 按需拉取，降低传输量并满足隐私边界（见 §7、§9）。

### 4.3 Agent Manifest（能力声明，路由与治理的依据）

每个 Agent 用一份声明式 Manifest 描述自己。注册中心据此建立**能力索引**，Planner 据此做**语义路由**。

```yaml
# 教学示意（假想的第三方点餐 Agent，用于展示 require_confirm/支付权限等字段全貌）；
# 真实清单见 agents/<name>/manifest.yaml，接入样板参考 agents/nearby/、agents/navigation/
agent_id: food-ordering
version: 1.2.0
display_name: 点餐助手
category: ecosystem
trust_level: third_party
deployment: cloud            # edge | cloud
latency_budget_ms: 2000
fallback: chitchat           # 失败/拒绝时的降级 Agent

capabilities:
  - intent: food.search_restaurant
    description: 按菜系/位置/评分/价格搜索餐厅
    slots: [cuisine, location, rating_min, price_level, party_size]
    examples: ["找家川菜馆", "附近有什么好吃的", "人均一百以内的火锅"]
  - intent: food.reserve
    description: 预订餐厅座位
    slots: [restaurant_id, datetime, party_size]
    require_confirm: true     # 涉及承诺/费用，需二次确认

requires_permissions:         # 见 §9 权限模型
  - location.read
  - payment.invoke
  - network.external

# 端侧 Agent 额外声明本地意图白名单(用于 Fast Intent)
edge_intents: []
```

**路由如何用 Manifest**：Planner 把所有已注册 Agent 的 `capabilities`（intent + description + examples）作为可选"工具"提供给 LLM 做工具选择/规划；`trust_level` + `requires_permissions` 决定能否被调用与是否需确认；`latency_budget_ms` 用于超时与降级。

### 4.4 Agent 注册与发现

```mermaid
sequenceDiagram
    participant A as Agent 实例
    participant R as Agent Registry
    participant P as Cloud Planner
    A->>R: Register(manifest, endpoint)
    R->>A: Describe() 反向校验能力一致性
    R->>R: 写入能力索引 + 健康探测列表
    loop 心跳
        R->>A: Health()
        A-->>R: SERVING
    end
    P->>R: ResolveAgents(intent / 语义检索)
    R-->>P: 候选 Agent 列表(endpoint + manifest)
    Note over R: Agent 下线/不健康自动摘除；新 Agent 注册即可被路由
```

- **注册方式**：Agent 启动自注册（gRPC/HTTP 调 Registry），或经声明式部署（manifest 随容器部署被 Registry sidecar 上报）。
- **版本与灰度**：同一 `agent_id` 多版本并存，Registry 支持按 `vehicle_group` / 灰度比例路由。
- **生态接入**：第三方 Agent 提供符合契约的服务端点 + Manifest + 通过安全审核，即可上线，**不触碰编排核心代码**。

---

## 5. 编排器设计

编排是"双脑"：端侧 Edge Orchestrator（快、确定）与云侧 Cloud Planner（强、灵活）。

### 5.1 Edge Orchestrator（端侧编排器）
职责（只做决策与降级，不做复杂业务）：
1. 接收 Edge Gateway 来的标准化输入（文本 + 上下文引用）。
2. 调 Fast Intent 分类，按 §3.2 决策本地处理 or 上云。
3. 本地处理：调端侧 Agent → 经 VAL 执行 → 聚合结果 → 出话术。
4. 上云：建立/复用流式通道，转发请求，接收云端流式结果并驱动 TTS/HMI。
5. 降级控制：网络/云端异常时切端侧兜底。
6. 端侧多轮的短期上下文维护（最近 N 轮，本地缓存）。

### 5.2 Cloud Planner（云侧编排器 / Supervisor）

```mermaid
flowchart LR
    In["请求 (文本+上下文引用)"] --> NLU["意图理解 & 槽位补全<br/>(LLM, 含上下文)"]
    NLU --> Route{"路由决策"}
    Route -->|"单意图"| Single["选 1 个 Agent"]
    Route -->|"组合意图"| Plan["任务规划<br/>(DAG: 依赖/并行)"]
    Plan --> Resolve["ResolveAgents (Registry)"]
    Single --> Resolve
    Resolve --> Perm{"权限/信任校验"}
    Perm -->|"通过"| Exec["执行编排<br/>(顺序/并行/重试/超时)"]
    Perm -->|"拒绝/需确认"| Confirm["生成确认/拒绝话术"]
    Exec --> Agg["结果聚合 + 话术生成<br/>(LLM 改写为自然口语)"]
    Agg --> Out["流式输出: speech + ui_card + actions"]
    Exec -.失败.-> Fallback["降级到 fallback Agent"]
```

关键设计点：
- **规划用 LLM + 工具调用范式**：Agent 的 `capabilities` 即"工具"。Planner 让 LLM 输出一个**任务计划（DAG）**：哪些 Agent、顺序/并行、Agent 间参数依赖。
- **执行与规划分离**：LLM 只负责"规划"（决定调谁、传什么），实际"执行"由确定性的 Executor 完成（带超时/重试/熔断）。**LLM 不直接产生车控信号**——车控类 action 一律回流到端侧 VAL 经权限校验后执行（见 §9.1）。
- **结果聚合**：多 Agent 结果由 LLM 改写成连贯的口语化播报 + 结构化卡片，保证体验一致。
- **多轮与澄清**：缺槽位（`NEED_SLOT`）或需确认（`NEED_CONFIRM`）时，生成追问，挂起任务状态等待用户回复。

### 5.2.1 Planner 的三条智能供给通道与规则治理（2026-07-24 定稿归档；2026-07-26 Skill 层闭环；2026-07-29 范例库与规则退役合入；2026-08-04 依赖归一留账）

Planner 的智能供给全部声明式化，且**规则第一次有了出口**（设计详见 `docs/design/2026-07-24-eva-benchmark-intelligence-upgrade.md` 与 `docs/design/2026-07-28-intent-accuracy-data-flywheel.md`）。三条通道按权威链由硬到软：`route_hints`（LLM **之后**确定性改写）> `skills/`（LLM 之前的**知识**）> `skills/exemplars/`（LLM 之前的**数据**，最软）：

1. **规划知识 Skill 层（`skills/`，M0b；2026-07-26 补全闭环）**：领域组合知识与跨域判据从中央 system prompt 外迁为声明式文件——`guides/`（领域组合知识+few_shots，预筛 top-K 按需注入）、`policies/`（跨域规划**软约束**，常驻注入）、`workflows/`（v2 预留）。`SKILLS_MODE=off|shadow|canary|full`（默认 full）；中央 `_PLANNER_BASE` 只余通用规划契约。**加规划知识=投 skill 文件，不改编排核心**——与 route_hints（LLM 之后的确定性纠错）互补：skill 是 LLM 之前的知识供给。权威链（软硬分层）：VAL/payment/Runtime Policy > Capability Manifest > Plan Validator > PlannerPolicyPack（软）> PlanningGuide（软）。
   - **检索双通道（2026-07-26）**：`SKILLS_RETRIEVAL=lexical|hybrid`（默认 hybrid）——词法命中（keywords 显式信号）恒保留，语义通道以 guide `description` 向量余弦**补位** paraphrase（经 llm-gateway Embed，与 registry 语义路由同源；**fail-open** 回词法不堵规划）。档位与阈值（0.40）由 paraphrase 语料阈值扫描拍板（词法 0/11 → hybrid 11/11、零新增噪声），兑现 M0b「embedding 升级由召回数据决定」的悬空承诺。
   - **知识必须可证有效**：skill 自带 golden（`expect_intents` AND/`expect_any`/`expect_not`，项支持 `a|b` 容忍）经 `eval_skills` 三车道消费——离线检索门禁进 GitHub CI，live 车道（真 planner+真 LLM）以 `SKILLS_MODE=off` A/B 对照量化知识增益（首跑 Δ=+5/10）。`plan.skills` 归因名单反映**真实注入**（检索通道 `@lex`/`@vec`、超预算 `!clipped`）。
   - **分层边界的实证**：skill（LLM 前知识）教对了也会被 `policy: replace` 的 route_hint（LLM 后纠错）盖掉——live 车道首跑即抓到 nearby 设施发现 hint 的 guard 缺口踩掉 charging.find；holdout 车道次日再抓到 reminder「叫我」hint 劫持无「要是/如果」标记的条件句。凡「知识不生效」的 badcase，先查 hint 层再改知识。**分工口径（2026-07-27 采样方差实证）**：教科书形态用 route_hints 钉死（canonical 不该靠温度采样），skill 知识管 paraphrase 泛化。
   - **全生命周期闭合（2026-07-27 评审补强；同日二批补齐挂起链与归因）**：T2 再规划按 `plan.skills` 名单**继承**初规划注入的同一份知识（条件依赖类知识的决策恰发生在再规划轮），且**跨挂起成立**——`plan.skills` 随 `pending_plan` 持久化，补槽/确认恢复后的再规划不失忆；loader 对坏文件全面 fail-open（结构性坏文件跳过、非法标量回默认、重名先到者胜、改坏沿用 last-known-good；env 垃圾值回默认告警不崩启动）而 eval 文件级校验在 CI 是**阻断步**——运行时保知识可用性、门禁保主干整洁；golden 增 `holdout`（防 few-shot 原句自证）与 `expect_complexity`（adaptive 类知识的核心主张可断言），live 报告按 in-sample/holdout 拆分，**逐 skill 消融车道**做 per-guide 因果归因（full/off 分不清「知识的功」还是「hint 的功」；Δ=0 自动提示查 hint 覆盖）。hint 层教训沉淀：SOC 词形对手机/耳机等**设备**同样成立——replace hint 的 guard 必须排除非车辆主语（评审二批抓到的回归）。
   - **受限计划归一（2026-08-04）**：对“两步已选对，模型却随机漏 `depends_on/slot_refs`”这一窄类，guide 可声明 `plan_repairs`。它不是第四条路由通道：只能连接计划里已存在且唯一的 producer/consumer，不新增 intent、不覆盖已有槽位值，`shadow/off/!clipped` 资产不生效，多步歧义不猜。实际作用记入 `plan.skill_effects` 与 `cloud.planning.skill_effects`，不得把归一后绿灯计为模型原生正确；后续 manifest / validator / Runtime Policy / VAL 权威链不变。
2. **落域范例库（`skills/exemplars/`，M5 P1；2026-07-29 合入）**：Planner 的**第三条供给通道**，装的不是知识而是**数据**——一条 `话术 → 正确落域` 的记录，检索后作 few-shot 进 prompt。定位是权威链的**最软层**（在 PlanningGuide 之下）：只影响 LLM 判断，**不做任何硬路由**。
   - **它解决的是「改进循环的产物」问题**：在此之前，修一个落域 badcase 的标准产物是**正则**（`route_hints`），而规则只进不出——没有任何流程会问「这条 hint 模型现在自己会了吗」。范例把标准产物换成数据：**hint 写错是事故**（模型判对了也被 `replace` 踩掉），**范例写错只是噪声**（占了预算，删一行就没了）。故一个 badcase 三选一的默认答案是范例。
   - **机制整体复用 Skill 层范式**（hybrid 双通道检索 / 预算硬帽 / fail-open / `plan.exemplars` 归因 / T2 与挂起继承），Embed 出口与失败冷却与 skills **共用一份**（`orchestrator/cloud/embedding.py`——网关挂了两条通道一起回落词法，而不是各超时一次）。差异只在词法侧：范例文本仅 5-15 字，裸 Dice 会被功能词 bigram 支配，故用**语料自身的 IDF 加权**——不建停用词表（那是又一个只进不出的手工规则），投一个范例文件权重即自动重算。
   - **三个来源**：manifest 199 条 `examples` 一次性盘活（此前是死资产：不进 planner prompt，只喂 registry 打分）、badcase 标注一键转化（消费 `turns.gold_intents` 标注载体）、evolve 第四类提案（`route_error`/`slot_error` 默认产**范例草案**而非 regex 草案，原 bigram 拼 pattern 生成器随之退役——这是提案可应用率从 0% 起飞的路径）。
   - **一条实测出来的限度**：「一次修复自动传播到同族说法」**成立的前提是语料密度，不是单条范例的魔力**。首测中新范例修好的是它自己那个形态（精确锚），paraphrase 是被**已有范例 + 语义通道**接住的。飞轮的价值随条数增长，早期别指望单条见效。
3. **规则的出口：hint 退役流水线（M5 P2；2026-07-29）**——补上架构此前的结构性缺口「规则只进不出」。对每条 hint 的命中语料做**双臂裸跑**（A 臂全量、B 臂只摘这一条，评测侧过滤 agent 列表实现，**零运行时改动**）；B 臂仍落对 ⇒ 模型+范例已自己会了 ⇒ 退役。首轮把 `route_hints` 从 32 条降到 **12 条**。
   - **判定纪律（两条都是被数据逼出来的，不是设计时想到的）**：①**证据必须覆盖全部命中句**——抽 3 句时判 27 条可退役，覆盖全部命中语料后降到 21 条；**抽样的偏差方向是固定的：命中面越大越容易被放行，而那恰是风险最高的一批**。②**必须跨 provider 取交集**——单档跑出来的候选只证明那一档会，而 hint 的存在理由正是「弱 LLM 会漏/误路由」；实测两档交集把 3 条单档候选挡在门外。
   - **退役的代价必须记账**：这些召回断言原本是**阻断 pytest**，退役后保护改为端到端口径进 live 车道——**从「CI 阻断」降级为「人工触发」**。这不是可以忽略的细节，是退役换来的真实成本。
   - **安全面不由路由评测裁决**：`require_confirm=true` 的能力其 hint 一律不退（治理⑥）——**路由评测不构成安全证据**。
4. **落域指标 RoutingBench（M5 P2）**——兑现 §10 许诺过的「意图识别准确率/路由命中率」。四个离线 eval 是**回归闸**（防倒退，天天绿），不是**分布尺**（量进步）；RoutingBench 把散落语料统一到「话术→期望落域」口径，出 canonical/paraphrase 拆列与分域混淆矩阵。**读它必须配着三条限制**：语料可用量有限且**被排除的条数是隐藏分母**（每次报告都印）、域分布严重偏斜（前三域占七成，N1 涨不等于车控导航变好）、canonical 高分主要说明语料已被用来修过系统。
5. **结构化规划输出（`submit_plan`，M1a）**：规划轮经原生 function calling 强制输出合法 Plan JSON（单一 `submit_plan` 工具、named tool_choice，`PLANNER_TOOLCALL=on|off` 默认 on），替代文本补全+脆弱 JSON 截取。schema 顶层=既有计划协议、**不含 `require_confirm`**（确认权在 capability manifest ∨ action ∨ VAL 硬层，LLM 无权降级）；协议失败轮内降级，最坏调用数与旧路径持平。**降级链路按失败形态分两条（2026-08-10 校准）**：模型**能说话但没用工具**（有 content 无 tool_calls）→ 同轮文本抢救 + **重试一次工具通道**（`PLANNER_TOOLCALL_SALVAGE_RETRY=on|off` 默认 on），重试仍不成则回落抢救结果（`plan_mode=toolcall_salvage_kept`）；**协议异常 / provider 不认 tools**（无 content）→ 直接退 JSON 路径，这一档是弱 tool-calling 厂商的可用性保障。gate L1 双臂实测 MiniMax 走成 toolcall 由 51.3% 升至 85.5%（p=2.3e-08），墙钟 +38.5%。承载走既有 `CompleteRequest.tools`/`CompleteResponse.tool_calls` Struct 字段（V1 不改 proto；V2 真 agentic tool loop 需 proto 演进）。
   - 实施教训（V2 设计约束）：tool schema 与输出指令会三向改变模型输出分布（可选字段诱发多填、无说明 object 诱发少填、"写全"指令诱发编造占位值）——凡改 schema 必过旅程级行为对照。

### 5.2.2 执行治理：Task Ledger 与 Outcome Verifier（2026-07-25 定稿归档）

规划期声明式化（§5.2.1）之后，**执行期**补齐两件解锁件（设计详见 `docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md`）：

1. **Task Ledger（跨轮持久任务账本）**——「谁在替用户干活、干到哪了、还让不让它干」的唯一权威记录。
   - **分层不重叠**：`SessionState`（Redis）盖对话挂起窗（确认/补槽，秒-分钟）；Ledger（PG 表 `task_ledger`）盖**任务生命周期**（分钟-小时、跨会话跨重启）。存储否决 Redis 选 PG，因为「跨重启诚实」正是它的核心价值。
   - **落点在 SDK 侧**（`agents/_sdk/ledger.py`，挂 `BaseAgent.self.ledger`）：执行权本来就在 Agent 侧，登记权同侧则心跳/销单/预算都是进程内调用，无跨服务一致性问题。**接入长任务=调 `open`/`heartbeat`/`close` 三个函数，编排核心零改动**。v2 若编排器主动派发长任务，它成为同一存储契约的另一个客户端，消费面平移。
   - **cancel 走拉模式**：置态 + 心跳搭车读状态，后台任务自行收尾——不跨进程强杀、不建新推送通道。取消延迟上限 ≈ 一次心跳。
   - **预算与 deadline 是强制不是声明**：`budget` 里的 deadline/调用次数上限由心跳就地判定并截停，写 `stop_reason` 供区分「你叫停的 / 超时停了 / 预算用尽」三种话术。这把 Background 档守卫从口头承诺变成机制。
   - **中断诚实报告**：崩溃/重启后 active 任务永不心跳 → 查询侧惰性判 `orphaned` → 答「查到一半中断了，要不要重新查」。**v1 刻意不做 checkpoint 自动续跑**（重跑成本低于序列化中间态）。
   - **降级姿态**：PG 不可达时账本静默禁用，Agent 照常执行任务，只是受理话术不承诺可停可问。**刻意不做内存兜底**——承诺「可查询」而重启后答不上来，比没有账本更不诚实。
   - 契约登记：`docs/conventions.md` §9.6。

2. **Outcome Verifier（执行后对账）**——解「步骤 OK ≠ 结果达成」：车控步 VAL 层可能没落地、查询步可能拿了空数据，两者今天都以"成功"落地。
   - **声明式是铁律**：期望由 `capability.verification` 声明（proto `Capability` field 7），中央只实现通用求值器，**不得出现任何 agent_id/intent 字面量分支**——否则会长成第二个 `fast_intent.py`。与 route_hints 把领域路由知识搬回 Agent 是同一条哲学，由源码断言契约测试钉死。
   - v1 两个求值器：`schema`（`data_keys` 存在且非空，对症"空结果假 OK"）、`state_match`（对车况镜像逐键比对，对症"VAL 说成功、状态没落地"）。
   - **三态语义**：SAT 通过 / UNSAT 进 `on_fail` / **UNKNOWN 不定罪**（观测缺失≠没做成，防假警）。
   - `on_fail`：`report`（保持 **OK** 状态 + `data["_verify"]` 保留键 → 聚合器确定性拼接诚实口径，遵 R9 §9.5）、`retry`（**只对 `require_confirm=false` 的步开放**——副作用永不重放）。
   - 挂点是 executor 尾链（`dispatch → _to_result → 确认兜底闸 → 对账`）**外加两条流式直通路径**（engine D0 / loop T2）显式调用，且流式路径不重试（话术已流出）。
   - 求值源：`orchestrator/cloud/state_mirror.py` 只读订阅 NATS `vehicle.state.changed`（与 gateway/edge、collector、scene 三处镜像同源同形态），fail-open。

二者就位后，T2 有界循环预算由单值升级为按 `plan.complexity` **分档**（Interactive / Complex；Background 归 Ledger 语义不占循环预算），并落**重复副作用防抖**（`(intent, 解析后 slots)` 指纹撞上即回填、动作不重发）——对症"replan 对已完成步骤失忆而重复产出同一动作"，比原设想的"副作用步不进循环体"精准（不阉割 T2 对副作用任务的编排能力）且可测。

**执行治理的验收硬化（2026-07-26 总体验收合入）**——四条跨阶段组合面上被抓出并修正的语义，
从此是架构承诺（契约测试钉死，验收报告 `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`）：

1. **确认权只随「确认」注入**：挂起恢复分两种语义——wait_confirm 恢复（用户真说了「确认」）
   才注入 `confirmed` 标记；**补槽恢复绝不注入**（补槽答案「拿铁」不是确认，注入等于让危险步
   在用户从未见过金额/后果的情况下直接执行）。补槽重跑后照常二次挂起等真正的确认。
2. **超时 ≠ 失败**：步骤超时时 Agent 可能已执行完、只是响应没回来（副作用已发生）。超时结果
   同样打防抖指纹，同一 T2 循环内同指纹**不盲目重发**、回填诚实的「没拿到结果、没有重复执行、
   请核实」；真失败（Agent 明确报错=确定没做成）仍允许重跑——两类失败的重试语义相反，混同
   任一方向都错（重发→重复副作用；不重发→失败被复用成假成功）。
3. **一切旁路路径与主路径同闸**：流式直通（engine D0 / loop T2）不经 executor 尾链，故确认
   兜底闸（M0a）与 Verifier（M2）都必须在两条旁路上**显式对齐**——「声明了机制」不等于
   「机制覆盖了所有执行路径」，挂点必须枚举全部路径（M2 首验与本次验收各抓到一条漏网）。
4. **挂起态是防抖指纹的载体**：确认/补槽挂起横跨轮次，恢复侧的字段白名单必须携带
   `fingerprint`——否则任何跨过一次挂起的副作用步，其防抖在恢复轮静默失效。

### 5.2.3 槽值保真：下发前的原话回查（v1.28，QA 卡 Q12）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-3)。

### 5.2.4 省略式开关的确定性消解（v1.29，QA 卡 Q7）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-4)。

### 5.2.5 候选集上的聚合问答（v1.30，QA 卡 Q2）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-5)。

### 5.2.6 候选集下发面：文本入口与按钮入口收敛（v1.31，QA 卡 Q10）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-6)。

### 5.3 为什么"规划/执行分离"是 P0 安全要求
让 LLM 直接调用车控接口（function calling 直连车身）在量产不可接受：幻觉、注入攻击会变成真实的车辆动作。本设计中 LLM 的输出永远是"计划/意图"，所有副作用动作（尤其 `vehicle.control`）都要经过确定性的、可审计的 Executor + VAL 权限层（见 §9）。

---

### 5.2.7 能力缺席：从「声明了」到「可达」是一条五段链（v1.33，QA 卡 Q8）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-7)。

### 5.2.8 指代到人：占位与具名是同一个实体，救济路径要闭合（v1.34，person-pickup 卡）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-8)。

### 5.2.9 能力槽位的值域契约：声明了 ≠ 可达（v1.35，QA 卡 Q12 规格维 / B6 §4）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-9)。

### 5.2.10 候选集的「哪一组」：指代要闭合到组这一维（v1.36，QA I-030）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-10)。

### 5.2.11 尾部方差闭环：确定事实不经过模型转述（v1.37，MiniMax QA 复验）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-11)。

### 5.2.12 验收证据也必须收口到同一事务（v1.38，MiniMax QA 闭环）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-12)。

### 5.2.13 安全事实的登记与执行闸：不能挂在路由上（v1.39 建立；v1.46 闭合 dispatch 边界）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-13)。

### 5.2.14 系统持有的会话事实要有读出口，且出口在落域之前（v1.40，MiniMax QA 修复批 C4）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-14)。

### 5.2.15 会话状态机的归属判定：默认方向、参照系与准入（v1.41，MiniMax QA 修复批 C3/C10）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-15)。

### 5.2.16 复合句：每个诉求都要被单独看见一次（v1.42，MiniMax QA 修复批 C5/C6/C7/C8）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-16)。

### 5.2.17 说出口的每一句话都要对得上账（v1.44，MiniMax QA 修复批 C9/C11/C12/C13/C14）

[完整约束、机制与原始证据](detailed/planner-invariants.md#section-5-2-17)。

## 6. 核心数据流与时序（典型场景）

### 6.1 场景 A：车控指令（端侧快路径，离线可用）

```mermaid
sequenceDiagram
    actor U as 用户
    participant V as 语音/HMI
    participant EO as Edge Orchestrator
    participant FI as Fast Intent
    participant VA as 车控Agent
    participant VAL as 车控抽象层
    U->>V: "有点热，空调调到26度"
    V->>EO: text + context_ref
    EO->>FI: classify
    FI-->>EO: intent=hvac.set, slots{temp:26}, conf=0.93
    EO->>VA: Execute(hvac.set)
    VA->>VAL: setTemperature(26) [权限校验]
    VAL-->>VA: ok
    VA-->>EO: speech="已调到26度"
    EO->>V: TTS 播报 + HMI 反馈
    Note over EO,VAL: 全程本地, <500ms, 无网络依赖
```

### 6.2 场景 B：跨域组合意图（云端 Planner 编排多 Agent）

```mermaid
sequenceDiagram
    actor U as 用户
    participant EO as Edge Orchestrator
    participant CP as Cloud Planner
    participant Mem as 记忆服务
    participant Nav as 导航Agent
    participant Food as 点餐Agent
    U->>EO: "找家顺路评分高的川菜馆，订今晚7点两位"
    EO->>FI: classify
    FI-->>EO: 复杂意图(未命中本地)
    EO->>CP: 上云(text + context_ref{位置,路线})
    CP->>Mem: 取用户口味画像
    Mem-->>CP: {辣度:中, 人均:100}
    CP->>CP: LLM 规划 DAG: search_poi → reserve
    CP->>Nav: search_poi(川菜, 顺路, rating≥4.5)
    Nav-->>CP: 候选餐厅[3]
    CP->>Food: reserve(餐厅A, 今晚19:00, 2) [require_confirm]
    Food-->>CP: NEED_CONFIRM(费用/政策)
    CP-->>EO: 流式: "为您找到XX川菜馆,评分4.7,顺路。需要帮您订今晚7点两位吗?" + 卡片
    EO->>U: 播报 + 卡片
    U->>EO: "订吧"
    EO->>CP: confirm
    CP->>Food: reserve(confirmed)
    Food-->>CP: OK(订单号)
    CP-->>EO: "已订好,订单号..."
```

> 注（2026-07-18 校准）：本场景为教学示意（预订类 Agent 未接入）。当前仓库真实的同型链路：「顺路找餐厅」由导航×周边发现出候选、`waypoint_choice` 二次选择后并入途经点；涉费/危险动作的真实确认样例为停车缴费与场景创建（`require_confirm` 机制同图）。

### 6.3 场景 C：断网降级

```mermaid
sequenceDiagram
    actor U as 用户
    participant EO as Edge Orchestrator
    participant FI as Fast Intent
    participant SLM as 端侧小模型
    U->>EO: "讲个笑话" (此时无网络)
    EO->>FI: classify → 慢意图(本应上云)
    EO->>EO: 检测云不可达
    alt 端侧 SLM 可用
        EO->>SLM: 本地生成
        SLM-->>EO: 简短回复
    else 无 SLM
        EO->>U: "网络不太好,联网后我能陪您多聊聊"
    end
    Note over EO: 车控/媒体不受影响,始终本地可用
```

---

## 7. 上下文与记忆

记忆服务是**对话历史与长期画像**的真相源，分三层；**临时任务态（挂起 plan / 待确认 / 待补槽）与焦点态归编排器**（`orchestrator/cloud/session.py` 的 `SessionState`，非 memory 服务），由编排器侧 `ContextManager` 统一读写门面协调（见 [`docs/design/2026-06-25-context-system-redesign.md`](../design/2026-06-25-context-system-redesign.md) §4 归属表）：

| 层 | 内容 | 存储 | 生命周期 | 隐私 |
|---|---|---|---|---|
| **会话上下文（短期）** | 最近 N 轮对话（memory 服务）；当前任务状态、待补槽位、焦点态（编排器 `SessionState`）| Redis（云）+ 本地缓存（端） | 会话级（超时清理） | 端侧优先，敏感片段不上云 |
| **车辆上下文** | 实时车辆状态（车速、电量、位置、HVAC 状态…） | 端侧实时缓存，云侧按需快照 | 实时 | 位置等敏感，最小化上云 |
| **长期记忆 / 用户画像** | 偏好（口味、常去地点、音乐口味）、习惯、历史 | 云侧画像库（向量+结构化） | 持久（可遗忘/导出/删除） | 显式授权、可审计、可删除 |

设计要点：
- **working/core 装配层**：每个规划轮由编排器 `ContextManager` 把 catalog（registry 语义预筛 top-K）、对话历史、长期记忆召回、焦点态在**统一 token 预算**下按优先级装配成 Planner 上下文；初规划与再规划（T2 replan）复用同一装配。详见上述上下文重构设计稿。
- **活动任务的会话状态经结果保留键声明**（v1.25，G8）：Agent 用 `AgentResult.data` 的 `_route_session` 类保留键把「正在进行的任务」（如本次导航的目的地/途经点/时限/策略）交给焦点态，编排通用消费不认识领域字段；出口两个且分工固定——**prompt 只渲染名字与约束、绝不渲染坐标**（防模型转述时自己编），结构化事实经 `step.meta` 按 `context_scopes` 门控下发给消费 Agent（LLM 与客户端都写不到）。增量修改类意图（「途经点不去了/换条路」）据此有对象可指，做增量改道而非全新任务。契约 `docs/conventions.md` §9.1。
- **上下文按引用传递**：Execute 请求只带 `context_ref`，Agent 按需向记忆服务拉取所需片段（最小权限）；敏感片段（精确位置等）按 Agent manifest 声明的 `context_scopes` 最小化下发，未声明不发。
- **车书 Agent 的 RAG 知识库**属于"领域知识"而非用户记忆，单独建库。当前单车型
  静态手册用 hash 绑定的只读文件索引 + 中文 n-gram BM25/确定性重排；多车型规模或真实
  badcase 证明词法上限后，再沿同一 `KnowledgeRetriever` 契约迁移 pgvector/Milvus。检索成功
  后的LLM RuntimeError只允许一次受控重试；仍失败必须保留已核验PDF卡并诚实降级，不能把
  生成服务瞬断伪装成“未检索到”或裸`Agent内部错误`。
- **可遗忘**：用户可一键清除画像（合规要求），记忆服务提供删除/导出接口。

### 7.1 记忆图谱：带权偏好与关系边（2026-07-25 定稿归档）

长期记忆从「条目库」升级为「**带权偏好 + 关系边**」（设计详见
`docs/design/2026-07-25-m2-memory-graph-rfc.md`）。对症的是：偏好没有强度与时间维度
（「上个月随口说过一次想吃辣」与「每周三次点川菜」在召回里同权），实体之间没有关系边
（家人、常去地点互相孤立，「带我去接孩子放学」这类长链路无从接地）。

1. **偏好加权与衰减**——**加列不建新表**：`memory_item` 增 `weight`/`evidence_count`/
   `half_life_days`/`consent`。字段级对照后真缺口只有前三个，其余（predicate、confidence、
   `source_turn_ids` 证据溯源、`superseded_by` 冲突、privacy_level、occupant_id）本表已有；
   另建一张偏好表会推翻「分层记忆单表、kind 区分」的既有决策，且 supersede/隐私分级/
   GDPR 级联/召回打分都要重写一遍。
   - 强度 = `clamp(base(provenance) + 重复证据加成) × 时间衰减`。**显式偏好不衰减**
     （用户明说的偏好不因时间推移而失效）；Agent 推断类默认 90 天半衰期。
   - 巩固期语义从「等价→跳过」改为「**等价→加权**」——跳过让重复出现完全不留痕，
     正是「说过一次 vs 每周三次」同权的根因；就地更新**不刷新 `valid_from`**（那是衰减
     基准，刷新等于把陈年偏好洗成新的），冲突 supersede 时新条目**继承旧证据链**。
   - **存量兼容是硬约束**：`weight<=0`（升级前的全部条目与非语义记忆）一律回退
     `confidence` 口径，召回打分与上下文渲染逐字不变。

2. **关系边**——独立表 `memory_relation`（subject 不是当前用户、查询模式是**按实体名双向
   精确查**而非语义相似召回，与偏好是两类东西）。`rel` 为**封闭词表**
   （family / place_of / works_at / lives_at / owns / prefers_brand），词表外一律丢弃不猜。
   v1 只存边 + **一跳**查询，不做多跳推理、图算法、自动实体消歧。
   - **消费面先于存储面**：v1 只认有明确消费方的边——人称地点解析
     （「去接孩子放学」→ family 定人 → place_of 定地点）、召回注入升级、routine 加权。
     **查不到或有歧义一律诚实追问**，绝不用相似度猜（导航到错地方比查不到更糟）。

3. **生命周期强制项**：证据溯源（派生偏好可追到原始轮次）、`consent` 字段（敏感画像的
   授权来源）、冲突沿用谓词等价类 supersede，以及——**级联删除是红线**：合规硬删必须
   同事务删除关系边，否则家人关系与孩子学校（恰恰是最敏感的部分）会留在库里，那是
   假删除；导出接口同样必须包含关系边（**导出与删除必须对称**）。

4. **情绪信号刻意不进记忆层**：会话级情绪（短 TTL、不入长期画像、长期情绪画像需显式
   授权）本质是**会话态而非记忆**。它由 Planner 同轮附带输出、随 Final 结果透传给 HMI
   用于选择 TTS 情感参数，零存储零治理成本；若日后确需长期情绪画像，另按授权模型立项。

5. **声纹模板独立成表 `voiceprint`（M4 P4）**——与偏好那次「加列不建表」的结论相反，
   判据同样是字段级对照：声纹与记忆条目**没有一个共用字段**（无 predicate/text/confidence/
   supersede/召回语义），且 `memory_item.embedding` 服务于语义召回、维度与模型都不同，
   混进去必然污染召回。同 `memory_relation` 的理由结构。
   - 用 `REAL[] + dim` 而非 `vector(N)`：模板数量以「一辆车的乘员」计，全表余弦是微秒级、
     不需要 ANN 索引；而 `vector` 的维度写死在 DDL 里，换模型就要迁表。
   - **`model` 不匹配的模板视为失效**并提示重录——绝不拿旧模型的向量跟新模型的比余弦，
     那个数字不在同一个尺度上。
   - **级联删除同为红线**：声纹是生物特征，留在库里比留关系边更严重。
   - **名字必须同时落记忆**（`identity.name`）：只写 `voiceprint` 表的话，
     那张表除了识别比对**没有任何消费方会去读**——用户问「你知道我是谁」就答不出来。
     **存下来 ≠ 用得上**：新增一张表时先问「谁会读它」。

**G6/G7 演进（2026-08-14 EVA 二轮定稿归档）**：

- **「关于谁」与极性入列**：`memory_item.subject`（本人空串 / 家人 canonical 亲属称谓）
  与 `polarity`（like|dislike 闭集）。subject 与 `occupant_id` **正交**——后者是说话人，
  前者是内容指向（「我爸不喜欢空调太冷」= occupant 是我、subject=爸爸）；此前这个指向
  只活在自然语言字符串里，确定性代码读不出。冲突 supersede 与 Recall 均按 subject 收窄。
- **消费面原则（比字段更重要）**：存得下 ≠ 用得上——本仓两次实证「存了没人用」
  （`route.avoid_highway` 谓词教了抽取、全仓零消费方；`prefers_brand` 关系边同款）。
  每加一个记忆维度必须**同批交付确定性消费出口**，并配「记忆改变了行为」的断言
  （不是「记忆被注入了」）。本批四个出口：口味→检索前置（修「已参考您口味」只进话术
  的假个性化）、route.\* →路线策略、导航轨迹 episodic、历史指代放开 episodic 召回。
  **方向已编码在谓词名里的族（route.avoid_\*）消费方不得再按 polarity 过滤**——抽取把
  「不要走高速」标成 dislike 是合理语义，双重过滤会自己打架（真栈实锤后撤销）。
- **消费面的门禁本身也是消费面的一部分**（v1.26，E5）：记忆条目的 `scope` 与
  `predicate` **都由 LLM 写、都会漂**，任何一路单独当召回门禁都会静默漏掉整族记忆——
  真栈实锤：`place.avoid`／`poi.dislike`／`restaurant.no_queue` 三条 scope 明明是
  `profile.taste`，只因谓词没有 `taste.` 前缀，而消费方的 scope∧前缀是 **AND**，
  就永远进不了口味消费面（同一件事的另外两条证据一直是死的）。定式两条：消费侧按
  **scope 一路 + 谓词一路的并集**召回（同人称→地点的 `place_of ∪ works_at ∪ lives_at`），
  写入侧靠 `_PRED_CANON` 归一 + 抽取 prompt 钉死命名空间收敛源头。**查「存了没用上」时，
  三个过滤器（scope／谓词／subject）要逐个排，不能只看条目在不在库里。**
- **偏好要匹配到「关于谁」才能用**（v1.26，E2）：话里提到了人 ≠ 这条记忆是关于那个人的。
  「和老婆吃饭」命中「父母腿脚不便」并据此重排，属于假个性化的另一种形态——
  消费前必须按 `subject` 或称谓同义词把记忆与话里那个人对上，泛指（老人/长辈）才放宽。
- **记忆的主动出口是询问不是执行**：抽取当轮识别的未来事件（event_time 确定性校验：
  可解析、在未来、90 天内）→ 经主动治理器发**询问式**提醒建议卡（「要到时候提前提醒
  你吗？」，按钮 send_text 回发正常语音链）。零执行权红线在记忆侧同样成立：不自动建
  reminder，契约由用户点头建立；只在抽取当轮建议一次——memory 无定时器是刻意的。

契约细则（列语义、强度公式、封闭词表、级联红线、声纹判定与透传管道）登记在
`docs/conventions.md` §9.7 与 §9.11。

### 7.2 主动服务治理：唯一裁决点（2026-07-25 定稿归档）

「服务找人」的难点不是**产生**建议，而是**决定要不要现在说**。治理层缺席时，多个生产方
各自直发主动消息、各自节流、跨生产方零协调——三个 Agent 同时想说话，用户就被打扰三次。

**统一主动引擎**（服务 `proactive/`）是**「该不该现在打扰驾驶员」的唯一裁决点**：
生产方发 `agent.proactive.request`，治理器裁决后发既有 `agent.proactive`（下游网关与 HMI 零改动）。

| 设计承诺 | 内容 |
|---|---|
| **可以随时死掉** | 生产方走 NATS request/ack，**没人 ack 就直发老主题**。治理器故障 = 逐字回落到它上线前的行为，绝不静默吞掉用户显式约定的提醒。停容器即一键回退 |
| **单条字节级兼容** | 单条通过 = 剥掉治理键后原样转发——新机制不给既有链路引入任何行为差异 |
| **零领域字面量** | 优先级/情境断言/去重键/TTL 全由生产方在信封里声明；中央不含任何 agent_id/type 分支（与 route_hints / capability.verification 同一条哲学，源码断言测试钉死） |
| **不改写事实、零执行权** | 合并只做确定性拼接，全程零 LLM；产物只有话术 + 建议卡，执行永远经用户显式指令走正常语音链路 |

六道闸：情境断言投递期复核（三态，**unsat 与 unknown 一律丢**——生产方声称的前提证实不了
就不替它说）→ 跨生产方去重 → 免打扰时段 → 驾驶负荷延后 → 全局频控 → 同窗合并。

> **两处 unknown 判据方向相反，刻意如此**：闸 1 的 unknown 丢弃，因为那是**生产方自己声称**
> 的前提，无法证实就不该替它说；驾驶负荷闸的 unknown **放行**，因为「读不到车速」不是
> 「用户在忙」的证据——用缺数据定罪，会让治理器每次重启后的冷启动窗口静默吞掉一切。

**生产侧防抖与中央治理是两层，不互斥**：Agent 自身的节流管「同一件事别连着说」，
中央治理管「不同 Agent 之间别撞车」。新增主动生产方 = 拿到 NATS 连接后调
`runtime/proactive.publish_proactive` 并声明优先级，治理器与网关都不用改。

**验收修正（2026-07-26）**：① `dedup_key` 的语义是**触发实例**而非条目——提醒 snooze 保留
原条目 id，只按 id 去重会把「过 5 分钟再叫我」的第二次触发在去重窗内静默吞掉（直接违反
「绝不静默吞掉用户显式约定的提醒」），故 key 必须拼入触发时刻：同一次触发的重投判重、
跨次触发必不同。② 投递失败也要发裁决事件（`dropped/publish_error`），否则该消息在观测面
既非 delivered 也非 dropped、直接消失。③ **消费端（HMI）朗读与 S2S 模型音频的互斥是
HMI 侧责任**（两个播放器互不知情、同放即混音，且主动 TTS 的生命周期回调会误推语音 FSM。
M-C 收口后校准 2026-08-02：验收当时的止血是 S2S 进行中一律只出气泡；网关透传 `priority`
之后已升级为按档仲裁——`critical` 抢话直读、`user_contract` 排队待 S2S 空窗补播、其余仍
只出气泡，见 `hmi/src/proactiveSpeech.mjs`）；治理器侧按 `s2s_speaking` 情境位延后仍是
完整方案，已立卡未做。

契约细则（主题、信封、优先级四档、六道闸判据）登记在 `docs/conventions.md` §9.8。

### 7.3 生态接入：受控 MCP 桥（2026-08-12 商户工作流增补）

外部生态（点咖啡/订票类交易闭环）的接入成本不在协议，而在**治理**。为每个外部服务写一个
gRPC Agent 成本过高；动态放行注册又把准入权交给了外部。折中是**一个 `mcp-bridge` Agent
承载 N 个 MCP server，接入永远是人工准入**：

- **三重锁定**：server 版本逐字相等 + tool 白名单（server 多提供的直接忽略）+ tool schema 指纹。
  外部服务改了接口 → **拒载并告警**，重新人工审，不自动接受。
- **写操作生命周期五项缺一不接**：幂等键（= **请求指纹**，不得用每次都新的 task id）、
  订单状态机（复用 `task_ledger`，不建新表）、timeout/cancel（超时报“不确定”，写调用
  `retry_policy=never`，禁止凭猜测重放）、可达补偿（瑞幸再次确认取消；麦当劳官方无取消，
  只能放弃未支付并等待商户关闭）、审计。确认前远程写调用必须为零。
- **复合工作流隔离 LLM 与官方低层参数**：Planner 只产 `mcd.order` / `luckin.order` 等复合
  intent；桥内 `MerchantWorkflow` 用官方只读结果确定性构造嵌套商品/规格参数、金额与成功判定，
  Redis TTL 草稿保存预览并在确认时原子消费。官方低层写工具 `expose=false`，跨步引用只接受
  Executor 注入的可信 provenance，不能靠用户文本或 Planner 自填 code。草稿按
  `merchant_draft` 登记为可删除个人数据：value 不留 user/session 明文；确认消费同时建立共享
  Redis 操作租约。Memory 全量 ForgetUser 只额外协调商户草稿与 Planner 挂起/焦点两类短期状态：
  租约在飞时返回 pending，释放后重试并证明 owner 数据清零才 ACK。10 分钟 TTL 只是故障兜底；
  这不是 Task Ledger、支付、可观测或外部商户订单的全跨域删除 saga。
- **响应与支付入口均 fail-closed**：官方业务 envelope 用声明式 `success_predicate` 判成功，
  `result_map` 只归一订单号/状态/金额；原始长响应不进入话术。支付 URL 必须同时命中桥侧 server
  host 白名单和 payment-gateway 运行时白名单并成功登记，否则清除全部链接，只引导官方 App。
  展示支付入口不代表已支付，本系统不代用户执行最终付款。
- **权限**：一律 `trust_level: third_party`（硬上限表自动禁高危车控/精确位置/摄像头麦克风）；
  涉钱走 payment-gateway，Agent 不持凭证；写操作 `require_confirm`（M0a 中央闸强制落实）。
- **MCP 只做生态桥**：内部核心能力保持 gRPC 强类型低延迟，不迁 MCP。
- **演示数据永远标出来**：`demo` server 的产出打 `_prov.mode=mock` + 卡片角标 + 话术前缀。
  演示不是问题，把演示装成真实才是（与 §9.5 数据真实性铁律同源）。
- **当前账号边界**：麦当劳/瑞幸凭证是服务级全局 token/账号，只允许网关权威 scope 下的
  已认证主用户进入写流程；声纹或客户端 meta 不构成授权。多乘员独立商户账号、token 自动刷新
  与支付 host 的量产配置治理均后置；缺失时能力诚实缺席或链接 fail-closed。

契约细则（准入清单字段、锁定语义、生命周期强制项）登记在 `docs/conventions.md` §9.9。

### 7.4 形态接入：端到端语音（S2S）与确定性链的分工（2026-07-25 定稿归档）

端到端语音模型（听感与时延代际优于三段式 ASR→LLM→TTS）带来一个架构问题：**它天生想把
「听、想、答、做」一起包掉**，而「做」必须留在确定性链上。分工按四条硬约束定：

- **两端两个契约，换厂商不动上层**：对上是本侧事件协议（HMI 只认这层，永不随厂商变），
  对下是 `BaseS2SProvider` 抽象（每厂商一实现）。「先锁协议不锁厂商」在代码上就是这个形状。
- **三层状态机，我们侧权威**：HMI 语音 FSM（用户可感知状态的唯一权威）× 会话层（provider
  连接/轮次对账/重连）× 编排执行层（一切副作用）。**下层事件永远只是上层的输入，不直接驱动
  上层迁移**——否则 provider 的 `response.created` 会和本侧刚判定的 barge-in 撞成「打断了却又
  开始说」。推论：**本侧权威要靠「不给它输入」实现，不能靠约定**（故 SPEAKING 期不推流，
  provider 的自主打断就无从发生）。
- **provider session = 可丢弃缓存**：对话历史唯一真相源在本侧 memory；重连 = 新 session +
  近 N 轮摘要重注入。任何时刻杀掉 provider 会话都无损——这是重连、厂商切换、A/B 的共同地基。
- **单一出口 `escalate`，不注 capability 清单**：S2S 会话内**没有任何执行通道**。模型唯一的
  工具是把用户原话交回文本主链，此后 planner 校验 / 权限 / VAL / `require_confirm` 闸逐字全量
  生效。**S2S 是新的「话筒」，不是新的规划入口。** 不注入能力清单是因为 tool schema 会三向
  改变模型输出分布，而语音轮不过 planner 校验、没有旅程级护栏可兜；单工具把判定权压缩成二元
  （自答 or 移交），错误面只剩「该移交没移交」——它的最坏结果是「口头答应没办事」（体验事故），
  **绝无「没确认就执行」（安全事故不可能）**。
- **域灰度 = 收放工具描述的边界，不是运行时拦截**：判定点必须在模型生成前的工具选择；生成后
  拦截必然截断已播出的音频。灰度门槛是漏移交率（对抗集实测）而非主观感受。
- **通道拓扑的代价必须补偿**：S2S 会话走音频面直连（不经端网关代理，省一跳延迟与断点），
  代价是主链看不见这些轮次 → **文本副产品回灌 memory/obs 是强制项**，否则语音轮成为记忆与
  可观测的黑洞（自进化也随之失明）。被打断的轮次**只回灌已播出的部分**——模型生成完的全文
  不是用户听到的内容。
- **隐私口径的变化点要显式呈现**：三段式只上行定稿文本，S2S 上行原始音频（且仅在唤醒后的
  交互窗内）。故默认挡位是三段式，端到端需用户在设置里显式选择。

打断在这套分工下是**三层不同语义**（听感 / 在飞任务 / 工具调用中），混为一谈必出双播或幽灵
执行。其中最反直觉的一条：**工具调用中被打断 ≠ 回滚**——丢掉的只是播报权，已进确认链的动作
由确认链自己收束（用户打断常是为了补充，不是反悔）。

**验收硬化（2026-07-26）——确认链的「回路侧」与身份粒度**：

- **「确认/取消」必须有被保证送达确认闸的机制，不能指望模型判对**。主链挂起确认时，S2S
  会话对这个状态一无所知——把「确认」留给语音模型，它会当闲聊自答（「好的已确认」而后备箱
  没开；取消腿更险：「已为您取消」而挂起还活着）。兑现方式与「权威靠不给输入实现」同构：
  **确认窗内本侧一切定稿强制走确定性主链**并取消 provider 在飞生成（D5-2 红线在 S2S 挡位的
  兑现），工具描述的确认轮引导只是第二道防线。执行侧红线（S2S 无执行通道）守住≠回路闭合，
  两侧都要有机制。
- **身份是唤醒粒度，不是会话粒度**：S2S 会话常驻，而「谁在说话」每次唤醒都可能变。会话建立
  时的静态快照会把自答轮的记忆全记给一个人——说话人身份经独立上行帧随声纹识别落地即更新、
  唤醒窗结束归位默认身份，记忆回灌按当前说话人隔离（classic/逃逸轮走请求 meta，本就正确）。

契约细则（对上事件协议、执行分工、打断三层、回灌口径、型号红线）登记在 `docs/conventions.md` §9.10。

---

### 7.5 形态接入：身份与视觉（声纹 / 单帧图像，2026-07-26 定稿归档）

S2S 之后的两个形态接入，架构上各自只回答一个问题：**「这句话是谁说的」** 与
**「用户眼前是什么」**。两者共用同一条纪律——**新增的感知只能扩大个性化，不得扩大权限，
也不得扩大数据驻留**。

- **身份识别与授权是两件事（红线）**：说话人身份（`occupant_id`）只准进记忆域，
  **绝不进权限判定、VAL、确认闸与支付**。理由不止「声纹可被录音重放」——把识别接进授权，
  会让「认错人」的后果从「看到别人的口味偏好」升级成「替别人花钱、开车门」。一个可以
  40% 概率认不出的信号，不该成为安全判据。由源码级断言测试钉死。
- **模型面与数据面分家**：向量提取归模型出口（llm-gateway），模板存储与比对归记忆域
  （memory）。**生物特征模板绝不下发到无状态服务**——扩散出去就删不干净，而 GDPR 硬删
  级联是已立的红线。反过来，数据服务也不该长出模型依赖。**级联的边界是「一切个人数据」
  不止长期记忆**（2026-07-26 验收补口）：会话原文（对话逐字记录）同样是个人数据——须有
  TTL（默认 7 天）且随 ForgetUser 级联清除；长期记忆删了、原始对话永久留存，那不是删除
  是搬家。
- **识别的失败模式必须是「退回今天」**：认不出、分不开、话太短——一律落回默认身份
  （既有语义），而不是造一个新的空身份。**新能力的降级目标应当是它上线之前的行为**，
  这样「能力不可用」在产品上等价于「这个能力还没上线」，而不是一种新的坏状态。
- **感知的门控在采集侧**：视觉单帧默认不采，只有用户显式问出「那是什么」这类话才抓一帧。
  「后端判断需要图再回头拍」是既慢又错的形态——它把门控挪到了数据已经产生之后。
- **引用而非内容穿越系统边界**：图像本体只在网关内存里活两分钟，跨服务流动的是一个短 TTL
  的引用。这既是性能考虑（meta 撑爆），更是隐私考虑——**进了对话链的东西会进日志、进采集、
  进记忆**，而这三者的生命周期都远长于「问一句那是什么」。
- **拿不到就说拿不到，不要静默降级成另一件事**：引用失效时若只把文本发给多模态模型，
  它会对着空气答「看不清，画面有点模糊」——**假装看到了**。诚实降级的边界不只在「数据源
  失败」，也在「输入不完整」。
- **能力性 pin 优先于会话级偏好**：看图必须打到能看图的模型，不能跟随用户切换的聊天大脑。
  档位解析对不认识的模型名是静默回落，故这类"能力硬约束"要独立成档而非塞进通用档——
  否则一次瞬时失败就会打到一个做不了这件事的模型上，**且不会有任何报错**。
- **建库与查询必须同源采集**（2026-07-27 真机补口）：对信道敏感的模型（声纹嵌入、任何
  向量匹配），**注册用的采集链路与识别用的必须逐字相同**。真机实测同一批人：注册走
  MediaRecorder(webm/opus 有损)、识别走原始 16k PCM，同人余弦 0.73→0.48 **系统性差 0.2**，
  阈值卡在中间，且探针会塌向别人的模板——两个人被认成同一个。**类型系统看不出这个区别**
  （两端都是「音频」），只能靠源码级契约钉住采集参数与编码格式一致。推论：**自证功能必须
  走被证的那条路**——设置页「试一试」曾走更干净的通路，于是主链路已经认错人时它照样显示
  「听出来了」，唯一的自证手段成了假证人。
- **阈值不能拿代理数据标定**（同上）：合成 TTS 音色彼此共享信道特征、异人余弦高达 0.65，
  逼得阈值上抬到 0.62；真人真麦的异人分离度好得多（0.12），阈值反而该下放到 0.45。
  **代理数据不只是「不够准」，它可能把方向标反。** 任何用代理数据标定的常量，
  上线后必须在真实分布上复标一次，且判定分数要**留痕可查**（否则无从复标）。
- **系统已经知道的事实，不要交给 LLM 回答**（2026-07-27 真机补口，本仓库第三次撞同一条：
  墙钟 / 日期锚 / 说话人身份）：声纹已经认出这轮是谁，而车里只有一个会话、说话人会换——
  上一轮刚管别人叫过「阿灵」，这一轮 system 明写着泓舟，模型照样答「你是阿灵呀，刚才不是
  说了嘛」。**加强提示词实测无效**（两个方向各两次全错）：**system 提示打不过对话历史**，
  历史更近、更像既成事实。判据一句话：**这个答案系统已经知道了吗？知道就别问模型。**
- **祈使指令不接受「不是在跟你说话」的判定**（同上，R4.4 受话判定的边界修正）：
  「记住，我女儿叫小满」被判 `addressed=false` → 静默短路，用户**说了、没回、也没记**
  （拒识轮按设计不落库）。且它是间歇的——同一句某次连拒三次、换一批又只拒一条，
  是模型判定的方差。**用户用祈使句直接对助手下指令，这件事不需要模型判断**：
  句首祈使前缀确定性判为受话，其余仍由模型判。同族于上一条。

契约细则（声纹判定四态与阈值口径、透传管道、GDPR 级联；视觉帧引用、采集门控、权限分级）
登记在 `docs/conventions.md` §9.11 / §9.12。

#### 7.5.1 OwnerKey：从「知道谁在说话」到「数据面记得下来」（M-B，2026-08-01）

P4 把 `occupant_id` 从 HMI 贯到了 Cloud、Agent SDK 与 `AppendTurnRequest`。
但那整条链只存在于**请求控制面**：Redis 里的 Turn 仍只有 `role/text/ts`，
`profile.places` 读侧恒取 primary、写侧是共享 KV，reminder 全域零 occupant。
后果不是「识别不出说话人」——**是识别对了也存不下来**：同一 cabin session 换个人说话，
上一位的话会按当前说话人归档，而且在记忆里留下持久脏数据（修好识别不会自动修好数据，
这条 P4 真机第四批已经付过一次学费）。

M-B 把归属落到数据面，统一为 `OwnerKey = (user_id, occupant_id)`。四条判据值得单独记住：

- **空 occupant = primary，但绝不等于共享。** 共享必须由独立数据类型或显式 scope 表达
  ——靠缺省表达共享正是这一批缺陷的共同成因。同理 `GetSession.scope` 不传即
  `OWNER_ONLY`，跨乘员读取必须显式声明。
- **普通读写落 primary，owner 级删除缺 occupant 一律拒绝。** 两者的不对称是刻意的：
  读错一次只是少看到东西，删错一次是把别人的数据一起删了。
- **归属判定不交给 LLM。** 巩固窗口在进 extractor **之前**就按 owner 切好——模型看到的
  只是一段文本，它没有能力也不该负责判断这段话是谁说的。
- **全局扫描可以跨 owner，消费必须先分组。** `claim_due`/`claim_location` 由时钟与围栏
  驱动、与会话无关，跨 owner 原子领取是对的；但**一条 speech/card 只能属于一个人**，
  `items[0].user_id` 不能代表混合 owner 集合——此前两人同秒到点时，一个人会听到另一个
  人的提醒，整条消息还被记在第一个人名下。

兼容口径是**有损但方向收窄**：旧 Turn、旧 reminder、无主写入统一归 primary（不按文本、
时间或声纹猜），归了不可自动恢复，但绝不会变成「谁都能读」。
红线不变：`occupant_id` 仍只进记忆域，不参与任何鉴权、确认或 VAL 判定。
契约细则登记 `docs/conventions.md` §9.13。

---

## 8. 通信与协议

| 通信场景 | 机制 | 理由 |
|---|---|---|
| Agent 调用、编排器↔Agent、Registry | **gRPC**（proto 强类型，支持流式） | 强契约、高性能、跨语言（Go/Python） |
| 端↔云 | **gRPC 双向流 over QUIC/HTTP2**（断线重连、心跳） | 流式话术下发、低时延、弱网友好 |
| 异步事件 / 车控状态广播 / 主动服务触发 | **事件总线（NATS（推荐，轻量） 或 Kafka）** | 解耦、广播、削峰、主动场景 |
| HMI↔Edge Gateway | **WebSocket（语音流/事件）+ REST（控制）** | 前端友好、实时 |

> **实现现状（2026-07-02，PoC，与上表目标态的已知偏差，接手者以此为准）**：
> ① 端↔云为**进程内单条持久 gRPC 双向流 + corr_id 多路复用 + 15s 心跳 + 指数退避重连**
>    （R2.3；`orchestrator/edge/cloud_client.py`，每次重连重建 channel 走 `dns:///` 重解析换 IP
>    自愈，在途请求断连快速失败由上层降级）。原 `gateway/edge/main.go` 内未实例化的持久
>    ChannelClient 已作为死代码删除。
> ② 端云长连由 **Edge Orchestrator（Python）** 持有，非架构图中的 Edge Gateway（A3 组件漂移）；
>    HMI→编排为 WS→edge-orchestrator gRPC 直连。
> ③ HMI 的 **ASR/TTS/流式识别直连 llm-gateway HTTP(50059)**（`VITE_AUDIO_API_URL`），
>    绕过 Edge Gateway（「Edge Gateway 是所有交互入口」为目标态）。
> 详见 `docs/design/2026-07-02-r2.3-edge-cloud-persistent-channel.md`；鉴权（目标态）见审计 R3.1。

当前 PoC 已用 NATS 接通轻量可观测旁路，Topic 如下：
```
vehicle.state.changed       # VAL 状态 diff / 启动快照
obs.span                    # 端云路由、规划、执行、聚合 span（自动携带 session_id）
obs.metric                  # Agent 调用数、时延、错误率
obs.agent.health            # Registry 主动健康探测结果
obs.turn                    # 轮次收口：一次 Handle 一条（原话/话术/状态/路径，badcase 排查核心）
obs.llm                     # LLM 调用（llm-gateway 唯一出口收口：模型/tokens/时延/缓存/门控内容）
obs.log                     # 结构化日志（WARNING+ 与带 trace 的 INFO，防自激励）
obs.debug.vehicle.set       # 仅开发环境量：车速/电量/挡位/位置
```

`observability-collector` 订阅这些事件：内存聚合供实时 WS 推流，并把
turns/spans/llm_calls/logs 落 SQLite 持久化（`OBS_RETENTION_DAYS` 保留期、badcase 豁免），
经 REST 提供给独立 `dashboard`（会话→轮次→详情三级下钻）。事件不改 gRPC 契约；
完整 payload 与安全边界见 `docs/design/2026-06-15-observability-dashboard.md`（首版）与
`docs/design/2026-07-10-dashboard-badcase-observability-redesign.md`（badcase 贯通）。

### 8.1 LLM 网关多模型运行时（2026-07-17 定稿归档）

所有 LLM 调用的唯一出口（§2.2）在多厂商注册表之上的**调用语义契约**
（定稿并入自 `docs/design/2026-07-17-llm-runtime-hardening.md`，含决策卡与落地记录）：

- **注册表与「单一大脑」**：`llm_runtime.py` 按 env 装配已配置 key 的厂商
  （mimo/minimax/deepseek/qwen/anthropic-legacy），一套参数化 `OpenAICompatibleProvider`
  覆盖各家差异；全局 active 经 `POST /api/llm/provider` 切换、所有服务共用，
  **持久化 Redis `llm:active`**——重启/重建恢复上次选择，不回落 env 默认。
- **档位与降级**：调用方传档位哨兵（`""`=primary / `"@fast"`）而非具体模型名，按
  serving 厂商词表解析；同厂商 primary→fast 降级链。**429 单独分类**：
  Retry-After ≤ `LLM_429_WAIT_CAP_S`(2s) 且预算余量足→等一次重试同模型，否则跳过
  剩余档位映射 `RESOURCE_EXHAUSTED`（客户端 SDK 只对 UNAVAILABLE 做重连重试）；
  请求性 4xx→`INVALID_ARGUMENT`、超时→`DEADLINE_EXCEEDED`。流式首 token 前按档位链
  降级、首 token 后不切（§3.3 注）。
- **请求级 pin（meta 契约）**：`meta.llm_provider`（可选 `meta.llm_model`）逐请求指定
  厂商——WS 入口 `meta` 整链透传（cloud prefs 白名单→Agent ExecuteRequest.meta→SDK
  contextvar；planner/aggregator 经 engine 入口 contextvar），pin 到未配置厂商
  fail-closed。消费方：评测锁定（`e2e_journeys.py --provider`，漂移=报告作废）与
  dashboard 重放跨厂商 A/B。
- **健康与探针**：调用路径被动滚动窗口记账（`health.py`），`GET /api/llm/providers`
  附 health 块（HMI 设置页健康点）；`POST /api/llm/probe` 按需体检。**刻意无周期探活**。
  当前 `server.py::_backup_serving` 已支持受控 `LLM_BACKUP` 跨厂商链尾备份；
  请求级 pin 不跨厂商，流式首 token 后不切换。Jev Decide 将是独立路由，不复用聊天备份链。
- embedding 与 chat 解耦（`LLM_EMBED_*` 独立），切厂商不影响记忆语义召回。

---

## 9. 安全、权限与合规

### 9.1 车控安全（P0，重中之重）
- **唯一执行路径**：所有车身控制只能经 **VAL（车控抽象层）** 下发，VAL 是唯一能碰 SOME-IP/CAN 的组件。
- **LLM 不直连车控**：Planner/Agent 产出的是 `AgentAction(type=vehicle.control)` 的"意图"，由端侧确定性 Executor 提交给 VAL，VAL 做：① 指令合法性校验（范围/状态机）② 权限校验 ③ 安全态校验（如行驶中禁止某些操作）。
- **危险动作二次确认**：`require_confirm=true` 的动作（如开/关某些功能、涉及费用）必须用户显式确认。
- **安全态约束**：与车辆安全相关的操作遵循车辆功能安全要求（行驶状态门控、速度门控等），具体清单由车控域定义。

### 9.2 Agent 权限模型
- 权限以**能力点（permission scope）**声明（如 `location.read`、`payment.invoke`、`vehicle.control.hvac`、`network.external`）。
- Agent 在 Manifest 中 `requires_permissions` 声明所需权限；运行时由 Planner/网关做**强制校验**，越权调用直接 `REJECTED`。
- `trust_level` 决定权限上限：`third_party` 默认禁用 `vehicle.control.*`、强制网络出口白名单、运行在隔离沙箱。

### 9.3 数据隐私与合规
- **数据不出车默认原则**：车内音视频、精确位置等默认端侧处理；上云走最小化（如只传文本意图、模糊位置）。
- **可审计**：所有上云数据、Agent 调用、车控动作留链路追踪（trace）。
- **用户可控**：画像可查看/导出/删除；麦克风/摄像头有硬件级开关与状态指示。

### 9.4 LLM 安全
- Prompt 注入防护：用户输入与系统指令隔离；Agent 工具调用参数做 schema 校验。
- 内容安全：LLM Gateway 接入内容审核；车控相关输出走"白名单动作"而非自由文本解析。

### 9.5 数据真实性（provider 决议契约与卡片 provenance，2026-07-17 定稿归档）

「栈起来了」≠「栈是真的」——mock 是 CI/离线开发的合法公民，要治理的是**静默**
（定稿并入自 `docs/design/2026-07-17-data-authenticity-governance.md`；契约登记
`docs/conventions.md` §9.3/§9.4，接入规范 `docs/guides/provider-integration.md`）：

- **决议三铁律**：① 默认 env（无凭证）→ mock，CI/离线照跑；② 显式 real 意图
  （vendor env 显式非 mock，或配了该域凭证）+ 构造失败 → **fail-fast 启动即炸**，
  绝不静默回退 mock；③ 运行期真实源失败 → **诚实降级说拿不到**（FAILED 话术），
  绝不改供 mock 假数据（假 POI 可能被导航过去）。铁律③的运行期回退清扫历经三批
  （2026-07-17 news/nearby → 2026-07-24 M0a navigation/charging → **2026-07-26 验收
  抓到漏网第四家 trip-planner**，nightly badcase「重复占位站点」正是假 POI 充数的产物
  形态）；`_fallback` 字段结构性删除 + `not hasattr` 回归锁是根除的标准形态。
- **决议可审计**：每个 Provider 工厂收口输出统一行
  `provider[<domain>]=<vendor>(real)|mock`（`agents/_sdk/provenance.py::log_resolution`，
  顺带给 provider 盖来源章），全栈 `docker compose logs | grep "provider\["` 一屏审计。
- **严格栈**：`REQUIRE_REAL_PROVIDERS=on`（默认 off）时任何 mock 决议拒绝启动，含
  llm-gateway 侧 llm/embed/asr/tts 四闸；豁免域 `REQUIRE_REAL_EXEMPT`
  （默认仅 `parking`——停车数据源 ETCP 未接真；`knowledge` 自 2026-09-03 有真实
  SU7 手册索引后退出豁免）。
  `payment` 自 2026-08-11 真实化起是**独立决议域且不进豁免**（网关内自实现同口径
  决议行 `provider[payment]=…`，conventions §9.17）。
- **卡片 provenance**：外源数据 ui_card 统一携带保留键
  `_prov={mode: real|cached|degraded|mock|deterministic, vendor, fetched_at, note?}`
  （Struct 免 proto），HMI 徽章渲染（mock 醒目/degraded 灰/real 小字来源·取数时间）；
  **17 卡族已覆盖**（2026-08-27 拍板补登 air_quality / weather_alerts / life_indices，
  2026-09-03 补登真实手册 `manual`，
  2026-08-28 校准本行计数），trip_itinerary（停靠点级 grounded 布尔）、
  research_report（sources+权威编号）与内部数据卡**刻意不标**（已有更强或不适用的证据链）。
  `deterministic`（2026-08-27 泓舟拍板收编，2026-08-28 校准本行）= **内部确定性判据
  的产物**，与 degraded/mock **正交**——不是外部数据的降级，是「这答案怎么来的」的
  自我声明；只有登记过的内部确定性卡（当前 `safety_advice`）能打它。
- **泄漏探针**：`test/e2e_strict_stack.py`（run_e2e 清单内）——真栈五问断言外源卡
  `_prov` 全非 mock，并要求 SU7 手册问法落带车型/PDF 页引用的 real `manual` 卡，
  防「演示数据其实是假的」或“有实现但没进发布资产”的回归。
  ⚠ **「真栈不该有 mock」与「有 mock 必须承认」是两个立场**（2026-08-27 裁决，
  2026-08-28 校准本行）：前者是**部署形态期望**，后者是**诚实契约**（§9.17 的
  `payment_qr` 还强制要求打 mock）。合成一把尺子就会互相打脸——长会话 QA 探针
  因此把 mock 拆两档：该卡型已声明「mock 可接受」⇒ 记 WARN 不判 fail；
  mock **冒充** real ⇒ 仍判 fail。契约与判据面见 `conventions.md` §9.3。

---

## 10. 可观测性与质量

- **链路追踪**：`trace_id` 从 HMI → Edge → Cloud → Agent 全链路贯穿（OpenTelemetry）。
- **指标**：意图识别准确率、路由命中率（本地/云）、各 Agent 时延/成功率、LLM token/成本、降级触发率。
- **日志**：结构化日志，敏感字段脱敏。
- **当前 PoC 实现**：端/云关键节点把 span、指标、健康和车辆状态 diff
  best-effort 发布到 NATS；collector 内存聚合最近链路，Dashboard 实时展示。
  NATS 不可用时主链路不受影响。**badcase 排查贯通（2026-07-10）**：session/trace 全链路
  ID 贯通（HMI 每轮自生成 trace_id、气泡可复制直达 dashboard）、`obs.turn`/`obs.llm`/
  `obs.log` 内容级事件（`OBS_CONTENT_CAPTURE` 门控+统一脱敏）、collector SQLite 持久化
  与会话/轮次/日志检索 API、dashboard 四视图（会话下钻/总览/日志/badcase 收藏夹+重放）；
  Prometheus `/metrics` + OTel 桥接由 R3.6 落地（`--profile observability` 门控）；
  `obs.llm` 自 2026-07-17 增 `provider`/`requested_tier`/`pinned` 归属字段
  （collector `llm_calls` 落 `provider` 列——「哪个脑答的」按 trace 可审计）。
- **目标态差距**：告警、多车/多租户与正式鉴权（含 collector 鉴权边界）、采样与容量治理
  仍属于后续量产工作。
- **评测体系**：
  - 意图分类：标注集 + 离线准确率/召回。
  - 端到端：场景化测试集（车控/导航/组合意图/降级）回归。
  - Agent 质量：每个 Agent 自带契约测试（Describe/Execute 黄金用例）。
  - **评测可信度（2026-07-17）**：真 LLM 评测经 `--provider` 锁定 active 厂商 +
    逐旅程漂移守卫（漂移=报告作废退出码 1，且拦 `--write-baseline` 防混脑基线）；
    数据真实性经严格栈冒烟 + mock 泄漏探针把关（§9.5）。
  - **意图落域基线（2026-08-09 校准）**：L1/L2 采样不是同进程重复，而是公开 parent
    串行编排每层 2 个独立进程 × 3 样本；Planner 只提交请求级不透明 `capability_ref`，
    parent 从全部 repetition 重算 raw hallucination/escape/fallback 与 worker 身份。只有干净
    SHA、固定 provider/assets/gold、完整 L1+L2+L3 且 `eligible=True` 才能写
    `baseline_intent_adversarial.{json,md}`；两文件分别经临时文件原子替换，第二文件失败时
    回滚，但不承诺进程强杀下的跨文件事务。L3 还绑定单一新鲜报告、原始 Base64 字节及其
    SHA-256、当前 run/provider/lock/claim、受限时间窗与精确四段相对路径；畸形同级报告不会被
    静默忽略。当前正式对比/参考基线为 `f0af9c0` / DeepSeek all-layer 147/147。主模型 MiniMax
    在同一 SHA 的同口径结果为 141/147、`eligible=False`，
    两者不得平均或互相替代。该基线只量意图理解与落域，不量 Agent 业务结果和外部内容质量。
    报告是 fail-closed 审计证据而非数字签名：能改仓库与整套未签名 JSON 的主体仍可协同重写，
    最终信任根是 Git 提交、代码审查与远端历史。

---

## 11. 技术选型

| 领域 | 选型 | 理由 | 可替换项 |
|---|---|---|---|
| 接入网关 | **Go**（gin/grpc-go + gorilla/websocket） | 高并发、WebSocket、低内存 | — |
| 编排器/Agent | **Python**（FastAPI + grpcio + LangGraph/自研编排） | AI 生态最好，LLM/工具编排成熟 | 时延敏感端侧件可 Rust |
| Agent 间通信 | **gRPC + protobuf** | 强契约、跨语言、流式 | — |
| 事件总线 | **NATS（推荐）** / Kafka | NATS 轻量适合车云；Kafka 适合大数据量 | 二选一，按规模 |
| LLM | **云端 Claude / GPT 系（经 LLM Gateway）** | 能力强；Gateway 屏蔽差异、可多模型降级 | 任意，Gateway 抽象 |
| 端侧小模型 SLM | 端侧量化小模型（如 Qwen/Phi 量化版，跑在 8295 NPU） | 离线兜底 | 视 SoC NPU |
| ASR/TTS | 端侧流式 ASR + 云端增强；TTS 流式 | 低时延、离线兜底 | 厂商方案可替换 |
| 意图分类(Fast Intent) | 规则引擎 + 轻量分类模型（端侧） | 确定性 + 低时延 | — |
| 记忆/画像 | Redis（短期）+ PostgreSQL + 向量库（pgvector/Milvus） | 成熟 | — |
| RAG（车书） | 单车型：hash 绑定只读索引 + 中文 n-gram BM25/重排；多车型目标：pgvector/Milvus | 先闭合真实来源、页码引用和离线确定性；由规模/真实 badcase 触发向量化 | `KnowledgeRetriever` 保持可替换 |
| 车控抽象 | **C++**（对接 SOME-IP/AUTOSAR AP / VSOA / CAN） | 车规、实时 | 依平台 |
| HMI | **React + TypeScript**（车机 WebView/原生混合） | 复用 web 生态 | 视座舱方案 |
| 部署 | 云：K8s + Helm；端：容器/原生进程 + OTA | 标准化 | — |
| 可观测 | OpenTelemetry + Prometheus + Grafana + Loki/Tempo | 标准 | — |

---

## 12. 部署架构

### 12.1 云侧（K8s）
- 每个 Agent、Planner、LLM Gateway、Registry、记忆服务独立 Deployment + Service。
- 水平扩展：无状态服务（Planner/Agent）按负载扩缩；有状态（记忆/向量库）独立运维。
- 多环境：dev / staging / prod，配置经 ConfigMap/Secret，灰度经 Registry 路由。

### 12.2 端侧（车机）
- Edge Gateway / Edge Orchestrator / Fast Intent / 端侧 Agent / VAL 以进程（或容器，视车机 OS）部署。
- 资源约束：明确各组件内存/CPU/NPU 预算（量产阶段产出资源画像）。
- **OTA**：端侧组件、规则配置、阈值、本地意图白名单支持 OTA 下发（红线：OTA 配置变更需走发布流程，不在本设计内自动变更）。

### 12.3 端云通道
- 双向流式长连接（心跳、断线重连、指令幂等）。
- 鉴权：车辆设备证书 + 会话 token。

> **实现现状（2026-07-02，PoC）**：端云通道已为**持久多路复用 bidi + 15s 心跳 + 断线重连**
> （R2.3，持久客户端在 Edge Orchestrator，见 §8 实现现状）；**鉴权尚未落地**——Hello 仅带
> vehicle_id（`gateway/cloud/main.go` 注「PoC 简单通过」）、WS `CheckOrigin` 全放行、
> `user_id="u1"` 硬编码。会话鉴权最小闭环见审计 R3.1。

---

## 13. 工程目录结构（目标态与当前映射）

```
car-agent/
├─ CLAUDE.md                      # 项目规则(约束先行)
├─ docs/
│  └─ architecture/               # 本设计文档及拆分
├─ proto/                         # 所有 gRPC 契约(单一真相源)
│  └─ cockpit/
│     ├─ agent/v1/agent.proto
│     ├─ orchestrator/v1/...
│     └─ registry/v1/...
├─ gateway/                       # Go: Edge Gateway + Cloud Gateway
│  ├─ edge/
│  └─ cloud/
├─ orchestrator/
│  ├─ edge/                       # 端侧编排器 + Fast Intent
│  └─ cloud/                      # Cloud Planner
├─ llm-gateway/                   # LLM 多模型网关
├─ registry/                      # Agent 注册中心
├─ memory/                        # 记忆/画像服务
├─ observability/                 # NATS 事件出口 + collector
├─ agents/                        # 所有 Agent(统一脚手架)
│  ├─ _sdk/                       # Agent SDK(Base 类/契约实现/测试夹具)
│  ├─ vehicle/                    # 车控(core)
│  ├─ media/                      # 媒体(core)
│  ├─ navigation/                 # 导航(core)
│  ├─ info/                       # 信息(core)
│  ├─ chitchat/                   # 闲聊(eco)
│  ├─ trip-planner/               # 行程规划(eco)
│  ├─ manual-rag/                 # 车书(eco)
│  ├─ nearby/                     # 周边发现(eco, 原点餐域重构)
│  ├─ parking-payment/            # 停车缴费(eco)
│  └─ ...                         # 另有 deep_research/reminder/charging_planner/scene_orchestrator/road_safety 等，现共 12 个云 Agent
├─ vehicle-abstraction/           # C++: VAL 车控抽象层
├─ hmi/                           # React 座舱前端（车机屏）
├─ mobile/                        # RN+Expo Android 陪伴端（第二个用户端，见 §2.4）
├─ dashboard/                     # React 可观测仪表盘
├─ deploy/                        # Helm/compose/k8s 清单
│  ├─ docker-compose.yaml         # 本地一键起(PoC)
│  └─ helm/
├─ scripts/                       # 构建/codegen/proto 生成
└─ test/                          # 端到端场景测试集
```

当前 PoC 将车控/媒体端侧能力与 Python 模拟 VAL 放在 `orchestrator/edge/`；
尚未创建目标态的 `agents/vehicle/`、`agents/media/` 和 `vehicle-abstraction/`
（`agents/info/` 已落地）。不要因目录示意图而误判端侧独立模块已经存在；
另有 `security/`、`payment-gateway/`、`runtime/`（共享 gRPC 运行时）等
当前已存在的顶层目录未画入上图，职责见 `CLAUDE.md` §3。

**每个 Agent 的内部结构（统一模板）**：
```
agents/<name>/
├─ manifest.yaml          # 能力声明
├─ src/                   # 业务实现(实现 Agent 契约)
├─ prompts/               # 该 Agent 的 prompt(如需 LLM)
├─ tests/                 # 契约测试 + 黄金用例
├─ Dockerfile
└─ README.md              # 做什么/怎么用/依赖什么
```

---

## 14. 分阶段落地路线

2026-09-26 起以 [docs/roadmap.md](../roadmap.md)为唯一后续排期入口。
两份研究的采纳边界、CA2/JV 映射及人力假设均在那里维护；本节只保留架构依赖。

| 阶段 | 架构交付 | 关键依赖 |
|---|---|---|
| R0 | 固定版本与可重复证据；关键残余重证 | 当前主链，不因接模型重写基线 |
| R1 | 步骤范围/稳定目标/ResultBundle、能力契约、身份与逐信号时效、权限化 View | 原位扩展现有 Step/WorkingSet/Registry |
| R2 | 持久操作身份、一次确认、前置条件、因果对账、离线日志与恢复 | R1；副作用禁止无账本放行 |
| R3（可选） | T1e 受控短计划与目标板评测 | R1 后实验，写放量等 R2；无收益保留 shadow |
| R4 | 一种车辆服务驱动、能源/健康旅程、MCP 委托隔离 | R2 + OEM/真实服务条件；不依赖 R3 成功 |
| R5 | 集成试点、设备/版本兼容、灰度回滚 | 主线与双端验收；不等于量产认证 |

Jev 支线从 JV00–03 的基线/Decide/Snapshot/shadow 开始，之后逐项采纳建议；
不改变 T0、原话授权、确认、VAL、RetryController 和既有执行权。
24 周仅为 3–5 人持续投入且外部条件就绪的估算，个人维护范围见路线图。

### Phase 0 / Phase 1 / Phase 2 历史口径

Phase 0 的链路骨架与 Phase 1 的工程化主干已形成；完整 Phase 1 DoD 并未全部验收。
[原计划](phase1-implementation-plan.md)保留目标、WS 编号与差距，映射到 CA2 工作包后接续。
原 Phase 2 量产事项（真实总线、功能安全、资源/OTA、隐私、正式身份/沙箱）不能靠 R5 试点自动销账。

---

## 15. 风险与未决项

| 风险 / 输入 | 本轮裁决 | 接续 |
|---|---|---|
| 模型理解和置信度不可靠 | 原话、权限、确认与执行点校验独立；校准只增加建议 | CA2-02/09、JV03/07 |
| 单车镜像和陈旧观测 | 逐车辆/逐信号身份、时效、来源与因果分开 | CA2-06/10/12 |
| 持久化失效或未知副作用 | 写前准入，恢复先对账，不承诺 exactly-once | CA2-08/11 |
| Jev 中文收益、费用与时延未知 | 固定版本、同池 A/B、逐任务影子；未过门维持 off/shadow | JV00–09 |
| SoC/OEM 接口与人员未落实 | R0 登记；仿真/PC 不替目标板/实车签收 | CA2-14/18/22 |
| 手机/多人/正式身份与隐私 | 沿 Android 总表与 AM5 产品前提闭合，不靠声纹升级权限 | CA2-15/16/17/22 |

具体默认开关、未测项和回退见 [实施方案](../design/2026-09-26-cockpit-agent-v2-implementation-plan.md)。

---

## 附录 A：关键命名约定
- Intent 命名空间：`<domain>.<action>`，如 `hvac.set`、`navigation.search_poi`、`reminder.create`。
- Permission scope：`<resource>.<action>[.<sub>]`，如 `vehicle.control.hvac`、`location.read`。
- Agent ID：kebab-case，如 `charging-planner`。

## 附录 B：与既有 car-agent 资产的关系
本方案与历史 car-agent 工作（gRPC 微服务、Go 网关、Python Agent、React 前端）在技术栈上一致，可复用其工程经验；主要增量是：**云边分层混合编排**、**统一 Agent 契约 + 注册生态**、**规划/执行分离的车控安全模型**。

## 附录 C：版本记录

内容性设计合入（新增/改写章节、主题定稿归档）递增次版本；实现状态校准不递增，
在对应处标注绝对日期。完整记录已分拆到 [版本历史](detailed/architecture-version-history.md)，原次序保留。

| 版本 | 日期 | 内容 |
|---|---|---|
| v1.52 | 2026-09-26 | 采纳 v2 目标分册和 R0–R5/JV 路线；校准当前边界；详细 Planner 约束与旧版本记录分拆。只有文档变更，v2 尚未实现。 |
