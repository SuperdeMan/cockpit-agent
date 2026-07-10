# Dashboard 整体优化：badcase 排查导向的可观测重构（会话 / 轮次 / 日志贯通）

> 状态：⚪ 设计稿，待泓舟确认分期范围后落地。
> 诉求来源：泓舟 2026-07-10——「整体优化 dashboard；明确要增加日志以及 sessionid 等，
> 方便排查定位 badcase」。
> 关联：`docs/design/2026-06-15-observability-dashboard.md`（可观测台首版）、
> `docs/design/2026-07-03-r3.6-observability-prometheus-otel-export.md`（Prometheus/OTel 导出）。

## 1. 背景与目标

现在的 dashboard 是「演示视角」：能看链路形状（哪些节点跑了、多久、成败）、车辆状态、
Agent 健康。但真实使用中的排查场景是「badcase 视角」：

> 真机上某句话答错了 / 误拒了 / 卡片不对——这句话是哪个 session 的哪一轮？Planner
> 当时规划了什么？LLM 原始输出是什么？哪个 Agent 返回了什么？当时日志里有没有 WARNING？

这条工作流今天走不通。目标：**从 HMI 看到 badcase，到定位根因，不超过 1 分钟、不开
`docker logs`。**

边界不变：dashboard 仍是开发/演示可观测台，**不进车控执行主链路**；观测链路全部
best-effort（复用 `observability/events.py` 的有界队列+丢弃机制），挂了不影响主链路。

## 2. 现状盘点（已核实，非假设；含 file:line）

事件管线：各服务经 NATS 发 4 类事件（`obs.span` / `obs.metric` / `obs.agent.health` /
`vehicle.state.changed`）→ collector（FastAPI）内存聚合 → WS `/stream` 推 dashboard。

1. **trace 有形状没内容**。span attrs 只有 `intent/agent_id/kind/deployment`
   （`orchestrator/cloud/dispatch.py:64-74`）或 `complexity/steps`
   （`orchestrator/cloud/engine.py:205-212`）。用户原话、Planner 产出的 plan、Agent 返回、
   最终话术全都不在 trace 里。Planner 的 LLM 原始输出只进 stdout 且截断 500 字符
   （`orchestrator/cloud/planning.py:224`）。
2. **session 维度完全缺失**。`session_id` 在 proto 全链路都有
   （`proto/cockpit/orchestrator/v1/orchestrator.proto:21`），但 span 事件不带
   （`observability/events.py:119-140` 无此字段），collector/dashboard 不能按会话分组。
3. **HMI 定位不到 trace**。HMI 不生成 trace_id（edge orchestrator 兜底生成，
   `orchestrator/edge/server.py:33-40`），网关回程 `eventToMap` 也不回传
   （`gateway/edge/main.go:214-244`，`FinalResult` 无 meta 字段）。真机 badcase 只能按
   时间窗猜。反例：dashboard 自己的 CommandBar 就是自生成 trace_id 放 `meta.trace_id`
   （`dashboard/src/components/CommandBar.tsx:18-30,75`），说明 meta 通道现成可用。
4. **零持久化**。`CollectorStore` 纯内存、上限 200 条 trace（`observability/collector/
   store.py:10-14`），collector 重启即清空。上午的 badcase 下午就找不到了。
5. **结构化日志是死代码**。`setup_structured_logging`（`observability/logging.py:46`，
   含 trace_id 注入 + 敏感字段脱敏）全仓只有 `observability/__init__.py:3` 导出，**无任何
   服务调用**。实际各服务是自由文本 stdout，排查=逐容器 `docker logs | grep` 按时间戳
   人肉对齐。dashboard 无日志面板。
6. **LLM 调用是黑箱**。llm-gateway 的 meta 只消费 `caller`/`thinking`
   （`llm-gateway/server.py:44,58`），trace_id 不传入、不发 span；token/时延/缓存命中/降级
   只进 `cost_tracker` 全局统计，无法按请求回看。
7. **检索约等于零**。`/api/traces` 只有 `limit`（`observability/collector/server.py:72-78`）；
   不能按文本 / session / intent / agent / 状态 / 时间过滤。
8. **无 badcase 工作流**。不能标记、备注、导出、重放；不能把一条 badcase 变成回归用例。
9. **语音链路不可见**。ASR 引擎/定稿/时延、TTS provider/首帧、唤醒/拒识策略状态都不在
   观测面里，而真机 badcase 相当比例源头是「听错了」（R4.3/R4.4 经验）。
10. 顺手小问题：`parent_id` 采了但前端不用（span 平铺不分层）；无 trace 总耗时；无
    trace_id 的 span 全进 `"unknown"` 桶（`store.py:21`）；`route_hits/degrade/llm_tokens`
    collector 已存（`store.py:37-49`）但前端 `onMetric` 不取、不显示
    （`dashboard/src/App.tsx:127-152`）。

另有可复用资产：memory 服务已存对话轮（`memory/store.py:53 append_turn`，Redis），但那是
业务上下文，保留策略/查询形态都不适合排查，不复用它做观测（见 §10 不做项）。

## 3. 设计原则

1. **badcase 排查是第一公民**：信息架构从「面板堆」改成「会话 → 轮次 → 详情」的下钻主线。
2. **零 proto 改动**：trace_id 走 `meta`（既有透传通道）+ WS JSON 加字段；不碰
   `orchestrator.proto`。
3. **单点埋点**：turn 收口在 edge orchestrator 一处（T0/T1/T2/确认续接/D0 流式全覆盖）；
   LLM 收口在 llm-gateway；日志收口在 logging Handler。不进每个 Agent（延续「SDK 改一处
   全覆盖」模式），不触碰「新增 Agent 不改编排核心」铁律。
4. **best-effort 铁律**：全部新事件走既有 `EventEmitter` 有界队列；collector/NATS 挂了，
   主链路行为零变化（既有契约测试模式覆盖）。
5. **内容即敏感数据**：用户原话/plan/LLM 内容的采集持久化受 env 门控 + 统一脱敏，PoC
   默认开、量产默认关；**只采 ASR 定稿文本，绝不采音频**（安全红线：敏感数据不出车的
   同族约束——观测数据同样全部留在本机 compose 栈内）。
6. **轻量存储**：SQLite 单文件放 collector 容器卷。不复用业务 PostgreSQL——观测数据的
   写放大、保留期、清理策略都不该耦合业务库；collector 目前零 DB 依赖、可独立运行，这个
   性质值得保留。（若将来要多实例 collector 再迁 PG，schema 平移即可。）

## 4. ID 体系（贯通设计的地基）

```
session_id ──── 一次 HMI 页面会话（现状：'demo-'+random，hmi/src/App.tsx:35，保持不变）
   └─ trace_id ──── 一轮请求（= 一次 Handle 调用 = 一个 turn，turn 主键即 trace_id）
        └─ span ──── 链路节点（既有 obs.span，补 session_id 字段）
```

变更点（全部小改）：

| # | 位置 | 改动 |
|---|---|---|
| 1 | `hmi/src/App.tsx` | 每条消息发送前自生成 trace_id 放 `meta.trace_id`（学 CommandBar），并挂到本地消息对象上；气泡长按/调试模式显示短 id + 复制 |
| 2 | `observability/events.py` | `emit_span(..., session_id="")`、payload 加 `session_id`；新增 `emit_turn` / `emit_llm` / `emit_log`（见 §5） |
| 3 | `orchestrator/edge/server.py`、`orchestrator/cloud/{engine,dispatch,loop}.py` | 各 emit_span 调用点补传 `ctx.session_id` / `request.session_id` |
| 4 | `agents/_sdk/clients.py`（LLMClient）+ planner 内部 LLM 调用 | 调 llm-gateway 时 meta 注入 `trace_id`/`session_id`（`observability/tracing.py:82 inject_trace_meta` 现成） |
| 5 | `llm-gateway/server.py` | 读 meta 的 trace_id/session_id，发 `obs.llm` 事件 |

trace_id 生成职责不变（HMI 带则透传，不带则 edge orchestrator 兜底 `_ensure_trace_id`），
只是让 HMI 从「不知道自己的 trace」变成「自己造、自己留底」。

## 5. 事件模型（新增 3 个 NATS subject，沿用既有订阅模式）

### 5.1 `obs.turn` —— 轮次收口事件（badcase 排查的核心数据）

**发射点：`orchestrator/edge/server.py::Handle` 单点收口**。理由：edge 是所有请求的漏斗
（本地快路径、混合、转发云端、确认续接、D0 流式、adaptive 循环的事件都流经它），在这里
累积 `speech_delta`/`final` 一次成型，云端任何内部路径变化都不影响 turn 完整性。

```jsonc
{
  "trace_id": "…",            // 主键
  "session_id": "…",
  "ts": 1720…,                // 请求开始
  "duration_ms": 1234.5,
  "user_text": "帮我找充电桩",   // = HandleRequest.text（语音输入即 ASR 定稿文本）
  "is_confirmation": false,
  "input_source": "voice_wake", // meta 透传（R4.4 已有该字段）
  "path": "cloud",             // local | mixed | cloud —— edge 判定
  "speech": "为您找到 3 个…",    // final.speech（含流式增量累积）
  "ui_card_type": "place_list",
  "actions": 1,
  "status": "ok",              // ok | err | rejected | clarify | need_confirm | cancelled | timeout
  "error": ""                  // 异常摘要（如有）
}
```

`user_text`/`speech` 受 `OBS_CONTENT_CAPTURE` 门控（§8），off 时只留长度与 hash。

### 5.2 `obs.llm` —— LLM 调用事件

**发射点：`llm-gateway/server.py` Complete/CompleteStream**（唯一 LLM 出口，天然收口）。
字段：`trace_id, session_id, caller, model_requested, model_used, prompt_tokens,
completion_tokens, latency_ms, cache_hit, thinking, status, error`；门控下附
`prompt_tail`（messages 末条截断 ~500）与 `content_head`（输出截断 ~800）。
Planner 规划、Agent 生成、聚合器改写……每一跳 LLM 都自动按 trace 归档，
`planning.py:224` 那条「截 500 进 stdout」的孤儿日志从此有了结构化归宿。

### 5.3 `obs.log` —— 结构化日志采集（P1）

分两步：

1. **激活死代码**：各 Python 服务入口调用 `setup_structured_logging()`（一行/服务），
   stdout 变结构化 JSON 且自动带 trace_id（`logging.py:27-31` 现成逻辑）——`docker logs`
   立刻从纯文本升级为可 grep trace_id 的结构化流，这一步零新机制、纯激活。
2. **NATS 转发 Handler**：`observability/logging.py` 新增 `NatsLogHandler`，把日志复制一份
   经 EventEmitter 发 `obs.log`。门槛 `LOG_SHIP_LEVEL`（默认 `WARNING`；带 trace_id 的
   `INFO` 也发，保证 trace 详情页有内容）。**必须排除 `obs.*`/`nats` logger 命名空间**，
   否则发送失败的日志会再触发发送——自激励循环（已知坑，写进实现注释与单测）。
   字段：`ts, service, level, logger, msg, trace_id, session_id`。

Go 网关（edge/cloud-gateway）日志量小，P1 不接，`docker logs` 兜底；P2 视需要补
（现有 `log.Printf` 改 slog JSON + trace_id 字段）。

## 6. collector：持久化 + 查询 API

### 6.1 SQLite schema（collector 容器卷 `/data/obs.db`，WAL 模式）

```sql
turns(trace_id TEXT PK, session_id, ts, duration_ms, user_text, speech, status,
      path, input_source, is_confirmation, ui_card_type, actions, error,
      badcase INTEGER DEFAULT 0, note TEXT DEFAULT '')
spans(rowid, trace_id, span_id, parent_id, ts, service, node, status,
      duration_ms, attrs_json)          -- attrs 原样 JSON，含门控内容
llm_calls(rowid, trace_id, ts, caller, model_used, prompt_tokens,
      completion_tokens, latency_ms, cache_hit, status, content_json)
logs(rowid, ts, service, level, logger, msg, trace_id, session_id)
-- 索引：turns(session_id, ts)、turns(status, ts)、spans(trace_id)、
--       llm_calls(trace_id)、logs(trace_id)、logs(ts)
```

内存 `CollectorStore` 保留（实时 WS 推送、/metrics 不变），SQLite 是旁路追加写
（asyncio + `aiosqlite` 或线程池 stdlib sqlite3，批量 flush）。保留期
`OBS_RETENTION_DAYS=7`，启动 + 每日定时清理。文本检索用 `LIKE` 起步（PoC 量级足够，
FTS5 留作扩展）。

### 6.2 新增 API（现有 `/api/traces*`、`/stream`、`/metrics` 全部保持兼容）

```
GET  /api/sessions?limit=&q=                会话列表（首末时间/轮数/错误数/badcase数）
GET  /api/sessions/{id}/turns               会话内轮次流水
GET  /api/turns/{trace_id}                  轮次详情 = turn + spans + llm_calls + logs
GET  /api/search?q=&status=&agent=&intent=&session=&from=&to=&badcase=
POST /api/turns/{trace_id}/badcase          {badcase: bool, note: str}
GET  /api/logs?trace_id=&service=&level=&q=&limit=   全局日志检索
GET  /api/export/{trace_id}                 单轮完整 JSON（排查/回归用例一键素材）
```

WS `/stream` 增播 `{"type":"turn",...}`（会话视图实时刷新用）。

## 7. Dashboard 前端信息架构重构

单页四视图（左侧窄导航切换），从「一屏面板堆」改为「排查主线 + 保留演示页」：

```
┌──────┬────────────────────────────────────────────────────┐
│ 总览 │  ① 总览 Live（现有五面板保留 + §9 小修）              │
│ 会话 │  ② 会话（badcase 主视图，默认页）                     │
│ 日志 │     Sessions 列表 → 轮次时间线 → 轮次详情三栏          │
│ 收藏 │  ③ 日志（全局 tail：service/level/trace/关键词过滤）   │
└──────┴  ④ Badcase 收藏夹（标记列表 + 导出 + 重放）           ┘
```

**② 会话视图（核心）**——三级下钻：

- **会话列表**：session_id、起止时间、轮数、错误/拒识数、badcase 数；支持按文本搜
  （命中轮次的会话）。
- **轮次时间线**：对话流水样式（用户说 / 系统答 / 卡片类型 / 状态徽标
  ok·err·rejected·clarify·need_confirm / 耗时 / path 徽标 local·cloud），确认续接轮
  与母轮相邻呈现；点击进详情。
- **轮次详情三栏**：
  - 左：会话上下文（前后各 3 轮，快速判断多轮语境问题）；
  - 中：**span 瀑布图**（按 ts+parent_id 分层、时间条宽=duration、色系沿用现有
    LEGEND；替代现在的平铺列表）；
  - 右：**内容检查器**——用户原话（+input_source）、plan（`cloud.planning` span 的门控
    attrs：plan JSON / LLM raw 截断）、各 step 结果摘要、LLM 调用列表（caller/model/
    tokens/时延/cache，可展开内容）、最终 speech + ui_card、**关联日志**（该 trace_id
    的 logs tail）；底部操作条：⭐标记 badcase（带备注）、📋 导出 JSON、🔁 重放。

**④ Badcase 工作流**：

- 标记：轮次详情/时间线一键 star + 备注，落 SQLite；
- 导出：`/api/export/{trace_id}` 单轮全量 JSON 复制——直接可贴 issue 或做回归用例素材
  （与 `test/`、`scripts/eval_*.py` 语料格式对齐的转换脚本 P2 可选）；
- 重放：复用 CommandBar 通道原话重发（新 trace_id、session 标记 `replay-of:<旧id>`），
  详情页提供新旧两轮并排对照。

## 8. 内容安全与门控

| 项 | 值 | 说明 |
|---|---|---|
| `OBS_CONTENT_CAPTURE` | compose 默认 `on`，`.env.example` 注明量产必须 `off` | 控制 user_text/speech/plan/LLM 内容的采集与持久化；off 时事件仍发但内容字段只留 `len`+`sha256[:8]`（链路形状排查不受影响） |
| 脱敏 | 抽 `observability/redact.py` | 把 `logging.py:12-17` 的 SENSITIVE_PATTERNS 提出来，`StructuredFormatter` 与 `EventEmitter._emit`（对 turn/llm/log 三类）共用；密钥/token/长数字不落观测库 |
| 音频 | 永不采集 | 只存 ASR 定稿文本；观测数据全部留在本机 compose 栈 |
| 边界声明 | 沿用 dashboard/README 既有措辞 | collector/dashboard 属开发工具，非开发环境须置于鉴权边界后（与 `DEBUG_VEHICLE_CONTROL` 同级约束） |
| 保留期 | `OBS_RETENTION_DAYS=7` | 定时清理四张表 |

## 9. 顺手修（不单独立项，随 P0/P1 带走）

1. span 瀑布替代平铺（§7）；trace 头显示总耗时 + session 短标。
2. `route_hits/degrade/llm_tokens` 前端补显示（AgentInfo 类型 + onMetric 补字段，数据
   collector 早就有）。
3. `"unknown"` trace 桶在 UI 明示为「未带 trace_id 的孤儿 span」。
4. CommandBar 发送的 session 固定 `dashboard-observability` → 改带日期后缀，避免和历史
   混在一个会话里越滚越长。
5. `deploy/docker-compose.yaml` 给 collector 加 named volume（SQLite 落盘）；顺手给全栈
   加 docker 日志轮转 anchor（`json-file max-size=10m,max-file=3`）——现在没配，长跑
   演示机日志会无限膨胀。

## 10. 明确不做（YAGNI，延续 R3.6 决策）

- **不上 Loki/Jaeger/Tempo/ELK** 重型栈——PoC 单机规模，SQLite+NATS 自建链路够用且
  零新服务；Prometheus/Grafana profile 维持现状。
- **不做采样**——全量采集，量级（个位数用户）撑得住；量产再谈。
- **不复用 memory 服务的对话轮做观测源**——业务上下文与观测数据的保留、脱敏、查询
  形态不同，混用会把 GDPR 硬删逻辑复杂化。
- **不做 collector 鉴权/多用户**——维持「开发工具置于鉴权边界后」的既有边界声明。
- **不改 proto、不动编排核心的路由/聚合逻辑**——全部是埋点与旁路。
- HMI 不做完整调试面板（只加 trace_id 生成 + 气泡复制入口）；「车机上直接标 badcase」
  语音指令留 P2 之后再评估。

## 11. 分期与验收

### P0 —— ID 贯通 + turn 事件 + 持久化 + 会话视图（核心价值一步到位）

改动面：`events.py`（+session_id/emit_turn）、`edge/server.py`（turn 收口）、
`engine/dispatch/loop`（span 补 session_id、planning span 挂门控 plan 内容）、
`hmi`（trace_id 自生成+气泡复制）、collector（SQLite + sessions/turns/search/export API +
WS turn 推送）、dashboard（导航壳 + 会话三级视图 + span 瀑布）。

**验收（真栈）**：
1. HMI 发「帮我找充电桩」→ dashboard 会话视图实时出现该轮 → 点开见 span 瀑布 +
   plan 内容 + 最终话术；
2. HMI 气泡复制 trace_id → 搜索框粘贴直达该轮详情；
3. `docker restart observability-collector` 后 `/api/sessions` 数据仍在；
4. `make test` 全绿 + 既有 e2e 不回归（NATS 拔掉主链路无感的契约测试补 turn 版）。

### P1 —— 日志贯通 + LLM 黑箱打开

改动面：`logging.py`（NatsLogHandler + 各服务入口激活）、`clients.py`/planner
（LLM meta 注入）、`llm-gateway/server.py`（obs.llm）、collector（logs/llm_calls 表 +
API）、dashboard（日志视图 + 详情页 LLM 面板/关联日志）。

**验收（真栈）**：停掉一个 Agent 容器制造失败 → 该轮详情「关联日志」直接看到对应
WARNING（不开 docker logs）；轮次详情能看到 Planner 那次 LLM 调用的 model/tokens/
时延/输出头部；日志自激励循环单测通过。

### P2 —— badcase 工作流闭环 + 语音链路 + 打磨

标记/备注/导出/重放 + 对照视图；ASR（引擎/时延/定稿，llm-gateway http_server
asr-stream 处埋点）与 TTS（provider/首帧时延，R4.2 已有日志字段结构化）span；
保留期清理定时任务；Go 网关结构化日志（可选）。

**验收（真栈）**：真机复现一个 badcase → HMI 复制 trace_id → dashboard 标记+备注 →
导出 JSON 内含完整链路 → 重放对照新旧两轮。

### 规模粗估

| 期 | 后端 | 前端 | 新依赖 |
|---|---|---|---|
| P0 | ~400 行（events/edge/collector） | ~600 行（导航壳+会话视图+瀑布） | aiosqlite（或 stdlib） |
| P1 | ~300 行 | ~250 行 | 无 |
| P2 | ~250 行 | ~400 行 | 无 |

全程不改 proto、不加服务、不动车控链路。
