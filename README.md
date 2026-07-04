# 智能座舱 Multi-Agent 系统

云边协同的智能座舱 AI Agent 工程。系统采用分层混合编排：

- **T0 端侧快路径**：车控、媒体等高频确定性指令本地执行，离线可用。
- **T1 云端 DAG**：复杂、跨域、多意图请求由 LLM Planner 一次规划后确定性执行。
- **T2 有界循环**：需要根据中间结果调整计划的请求进入有迭代和时间预算的循环。

所有 Agent 使用统一 gRPC 契约和 Manifest，经注册中心发现。云端只生成意图与计划，
所有车控最终都必须经过端侧 VAL 校验和执行。

## 界面预览（HMI · Aurora Glass 极光液态座舱）

横屏 1920×1080 两栏（左对话流 + 右「上下文舞台」随对话切换场景）、液态玻璃材质、极光签名渐变、「小舟」光球化身；信息卡按 Figma 源逐张重建，**气泡 ↔ 卡片 ↔ 右舞台**三者联动。

![待机欢迎态：两栏外壳 + 小舟光球 + 右舞台时钟/车况](docs/images/hmi-welcome.jpg)

| 天气（卡片 + 右舞台 WeatherStage 活场景） | 附近 POI（卡片 + 右舞台测距地图，「第N个」联动） |
|:---:|:---:|
| ![天气卡 + 天气舞台](docs/images/hmi-weather.jpg) | ![POI 卡 + 地图舞台](docs/images/hmi-map.jpg) |
| **行程规划（结构化行程卡 + 危险操作确认条 + 行程地图）** | **设置（横屏侧栏 + 语音识别引擎/模型切换）** |
| ![行程卡 + 确认条 + 行程地图](docs/images/hmi-trip.jpg) | ![设置横屏侧栏](docs/images/hmi-settings.jpg) |

> 截图均为**真实后端数据**（天气=和风、POI/行程=高德）。本地起栈后访问 `http://localhost:5173` 可交互体验——按住「小舟」光球即可语音**流式实时上屏**。

## 当前状态

截至 **2026-07-04**：

- Phase 1 的工程化 PoC 主干与云端中枢 P0-P3 已落地；原始 Phase 1
  计划中的量产级能力仍有明确 backlog。
- `DispatchToEdge`、T2 有界循环、确定性工具已实现；权限为规划期（catalog 过滤）+ 执行期
  （dispatch 硬拒）**同源单轨校验**（`security/permission.py::check_permission`，R2.2）。
- 端侧混合意图支持按语义组分流，本地动作与导航/媒体慢意图可在同一请求中协同执行。
- HMI 支持文字流式渲染和句子级增量 TTS：首个完整短句即可开始合成、后续音频顺序播放。
- **信息类 Provider 全面落地**：导航=高德 / 天气=和风(JWT) / 搜索=Exa 正文级检索(AnySearch→Bing 降级) / 新闻=Exa 优先(SerpApi 兜底) / 赛事=api-football / 股票=Tushare，真实凭证冒烟通过，无凭证回退 mock。搜索经接地合成（强制引用、无依据诚实弃权），新闻以 TTS 播报式编号速览呈现。
- **HMI 信息类 UI 卡片**：天气/股票/新闻/搜索/赛事/POI 结构化卡片（搜索/新闻为「气泡给结论、卡片给证据」），全链路 ui_card 透传。
- **复杂任务动态思考 + 过程区**：行程/深度调研/多步等按统一 `is_complex` 判据动态对 LLM
  开思考提质，HMI 气泡内嵌四阶段可折叠「过程区」（理解需求→规划步骤→执行任务→整理结果，
  行车/泊车双态、脱敏不露 reasoning）；普通车控/闲聊零过程零额外延迟。
- **记忆系统分层重构**：从 mock KV 升级为 pgvector 语义记忆——自动从对话抽取偏好/个人实体（宠物·家人称呼也能记），语义召回注入规划、闲聊记忆感知作答，主动 routine 建议经 NATS→HMI，常去地点收敛、隐私分级+一键删除；embedding 走 llm-gateway→阿里云百炼 text-embedding-v4（真语义实测，无 key 降级 lexical）。详见 `docs/design/2026-06-25-memory-system-redesign.md`。
- **上下文系统重构**：承接记忆重构后裸着的 working/core 层——统一 `ContextManager` 把 catalog（registry 语义预筛）、对话历史、长期记忆召回、结构化焦点态装配于统一 token 预算；跨轮指代靠结构化焦点态而非啃原文；敏感上下文（精确位置/电量）按 Agent manifest `context_scopes` 最小化下发。详见 `docs/design/2026-06-25-context-system-redesign.md`。
- **通讯链路量产级加固**：全链路 gRPC keepalive（共享 `runtime/grpcio.py` 工厂，空闲也 ping，根治依赖重启换 IP 后的断连/无响应）+ 全服务优雅停机 + HMI 韧性（指数退避重连/断线有界发送队列不丢消息/请求看门狗）+ 熔断接线（开路快速失败 + Dashboard 可视化）+ LLM 网关连接池/流式 stall + 依赖连接加固（Redis/PG/NATS）；并修复一处危险车控确认退化（catalog 预算裁剪误丢 edge 车控核心）。真栈韧性自愈验证：依赖换 IP 不重启依赖方即恢复（Python 侧 `_reset_channel` + Go 网关显式重连，dns:/// 自动重解析单独不可靠）。详见 `docs/design/2026-06-25-comms-link-hardening.md`。
- **行程规划结构化重构**：从「LLM 自由文本行程」升级为**结构化可执行行程对象**——LLM 只提议骨架、确定性流水线接地真实 POI + 按真实电量沿路线编织充电点 + 校验每日车程，消灭幻觉景点（对症 TravelPlanner 基准纯 LLM 规划 0.6% 通过率）；每个停靠点可一句话导航（「下一站」「导航去第二天的 X」）、支持局部改某天不漂移、在途状态查询与「时间不够」自动精简，行程状态落记忆服务跨轮存续。护城河是车辆接地 + 在途编排（而非行前研究）。详见 `docs/design/2026-06-26-trip-planner-redesign.md`。
- **信息域深度调研重构**：新建独立 `deep-research` Agent——把「LLM 提议/确定性落地」纪律下沉为四段流水线（LLM 拆多视角子问题→有界并行迭代检索→分节接地报告→渐进语音简报 + 可读报告卡），对症单轮检索的多跳天花板；联网查询分层（普通「搜一下」秒回、深问「深入调研 X」自动升档 research.run）；接地「我」（位置/画像作研究约束）、多轮深挖（「展开第 N 点」聚焦不重跑）、新闻（信源权威重排沉内容农场、相对时间归一为绝对、卡片一屏可扫读标题+摘要+来源时间）个性化与「详细讲讲第 N 条」深挖桥接、晨间主动早报雏形、**异步分钟级深调研**（明示「不急/查完告诉我」即秒级受理，后台跑更深报告越过 ~90s 同步上限，完成经 `agent.proactive` 把可读报告卡主动推回车机）、**信源质量加权**（域名权威分层：学术/官方/百科优先、权威媒体次之、内容农场下沉，报告来源与引用以最权威打头；深度异步对薄弱角度用 Exa 学术类目兜底）；检索/接地合成内核抽到 `_sdk` 与 info 共享。护城河是接地车辆 + 渐进语音 + 可落地产物（非「车机版 Perplexity」）。详见 `docs/design/2026-06-26-info-agent-deep-research-redesign.md`。
- **HMI 极光液态座舱重构 + 语音流式上屏（2026-06-30）**：座舱前端重构为「Aurora Glass · 极光液态座舱」（横屏两栏 + 右上下文舞台 + 液态玻璃 + 小舟光球 + A-8 线性图标全替 emoji），~20 张信息卡按 Figma 源逐张重建并经真后端全栈 e2e 验证（8 卡族真数据 + 过程区 + 确认条）；**语音按钮即小舟光球**，ASR 支持**流式实时上屏**（边说边在输入框逐字显示、松手定稿自动发送，失败无感回退批处理），引擎可在设置切换——DashScope 实时（`qwen3-asr-flash-realtime`/`fun-asr-realtime`，分别走 OpenAI-realtime 与 run-task 两套协议）/ MiMo 分块。详见 `docs/design/2026-06-29-figma-hmi-implementation-plan.md`、`docs/design/2026-06-30-asr-streaming-design.md`。
- **审计驱动的工程门禁、架构还债与量产硬化（R1-R3.6 全部完成，2026-07-02~04）**：按全仓审计
  [`docs/reviews/2026-07-02-repo-audit-and-roadmap.md`](docs/reviews/2026-07-02-repo-audit-and-roadmap.md)
  清完 R1（门禁与卫生 5 卡）+ R2（架构还债 5 卡）+ R3（量产硬化 6 卡，T3.1-T3.6）共 16 卡，
  **R3 至此全部完成**。
  **R1 工程门禁与卫生**：CI「绿=本地全量绿」（补齐测试目录 + 聚合 requirements +
  Go/前端构建 job）、compose `restart`+healthcheck、media action_type 判定统一、文档同步、死代码清理。
  **R2 架构还债**恢复关键承诺：①**路由兜底机制化**——新增 Agent 只靠 manifest `route_hints` 声明式
  路由、编排核心零领域 Agent/意图字面量，恢复「不改编排核心加 Agent」铁律；②**权限单轨化**——三处
  权限实现收敛为唯一 `check_permission`；③**端云持久长连**——Python `CloudClient` 单条 bidi 多路复用
  + 心跳 + 换 IP 自愈，删 Go 死代码；④**info agent 拆域**——1269 行巨类拆 `handlers/` mixin，
  `agent.py` 123 行；⑤**跨 Agent 状态键契约化**——`_sdk/shared_state` 登记 + typed helper。
  **R3 量产硬化**：①**会话鉴权**（R3.1）——静态 token 两层校验（HMI↔edge-gateway、edge↔cloud-gateway），
  env 门控默认关；②**服务间 mTLS**（R3.2）——gRPC 双向 TLS、单张共享 mesh 证书 + name override，
  env 门控默认关（**T3.1+T3.2 齐即安全链路无已知缺口**）；③**e2e 入 CI 门禁**（R3.3）——新
  nightly workflow 跑裁剪过的确定性 mock-safe 子集，`make e2e` 改本地全量清单执行器；
  ④**意图路由评测基线**（R3.4）——`test/eval_fast_intent.py`/`eval_route_hints.py` 产出端侧/
  云侧路由准确率与召回率报告，CI 新增非阻塞门禁；⑤**降级矩阵自动化**（R3.5）——新
  `test/e2e_degrade.py` 断言架构 §3.3 四行真实现状（断网/云 Planner 故障/单 Agent 故障/LLM 超时）；
  ⑥**Prometheus/OTel 导出**（R3.6）——collector 新增 `GET /metrics`（手写 Prometheus 文本格式）+
  桥接真实 OTel span 导出 + compose 首次引入 `profiles` 机制门控 Grafana 仪表盘（延迟/成功率/
  熔断状态）。各卡零回归、真栈/GitHub 实跑验证，落地记录见 `docs/design/2026-07-0{2,3,4}-*`。
- **R4.0 收尾包（2026-07-04）**：清验收复审 [`docs/reviews/2026-07-04-acceptance-review-r1-r3.md`](docs/reviews/2026-07-04-acceptance-review-r1-r3.md)
  的残留——①端云持久通道 `pause/unpause`（同 IP 冻结再解冻）自愈修复（真根因=应用层心跳强制重连时
  `_cancel_stream()` 令 `read()` 抛 `CancelledError`、被 `_run` 当任务取消打死重连循环；真栈解冻后 ~2s 自愈）；
  ②过程区 e2e 断言前复位车态使测试自足；③R2.2 单轨化后 `PermissionEngine` 死注入清理；④Grafana 面板网络
  恢复后补验（三面板经数据源代理真实出数）。详见 `docs/design/2026-07-04-r4.0-residual-cleanup.md`。
- 全量 pytest：**1050 passed, 7 skipped**（单一命令 `python -m pytest --import-mode=importlib`
  一次跑通，含 R4.0 收尾包 K1/K2/N1 与 +2 条端云通道自愈单测）。
- 端侧 smoke：**13 passed, 0 failed**；真栈 e2e：中枢断言 7/7 + 上下文 6/6 + 韧性自愈 2/2 + 行程规划 6/6 + 深度调研（深调研报告 + 多轮深挖 + 新闻深挖桥接 + 异步分钟级受理→主动推送报告卡）+ nightly GitHub 断言型 e2e（含 R3.5 降级矩阵四行）全绿；R3.6 真实 Agent 调用→`/metrics` 端到端数据链路真栈验证通过。
- Docker 全栈 **26 个服务**（含充能规划/场景编排/路况安全/深度调研等 Agent），全栈联调通过；
  另有 Prometheus/Grafana 两个可观测服务经 Compose `profiles: ["observability"]` 门控可选启用
  （`docker compose --profile observability up -d prometheus grafana`）。

详细交接状态见 [`AGENTS.md`](AGENTS.md)，工程约束见 [`CLAUDE.md`](CLAUDE.md)。

## 架构

```text
HMI
  │ WebSocket / ASR / TTS
  ▼
Edge Gateway ── Edge Orchestrator ── Fast Intent
                         │                │
                         │                └─ T0: VAL → 本地车控/媒体
                         │
                         └─ Cloud Gateway ── Cloud Planner
                                                ├─ T1 DAG Executor
                                                ├─ T2 LoopController
                                                ├─ Cloud Agents
                                                ├─ Deterministic Tools
                                                └─ DispatchToEdge → VAL
```

架构唯一真相源：
[`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。

## 安全铁律

1. 车控只能经 VAL 下发，LLM、Agent 和工具不得直接操作 CAN/SOME-IP。
2. LLM 只负责理解与规划，确定性 Executor/Dispatcher 负责执行。
3. 危险动作必须二次确认。
4. 新增 Agent 通过注册中心接入，不修改编排核心。
5. 密钥和 token 只放 `.env`，不得进入代码、日志或提交。
6. 修改协议先改 `proto/`，再重新 codegen；不要手改 `gen/`。

## 快速开始

依赖：Docker Desktop、Python 3.11+；本地开发另需 Go 1.24+、Node 20+、buf。

Linux/macOS：

```bash
cp .env.example .env
make proto
python test/smoke_edge.py
make up
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
./scripts/gen-proto.ps1
python test/smoke_edge.py
docker compose -f compose.yaml up --build
```

打开 [http://localhost:5173](http://localhost:5173) 使用 HMI；
[http://localhost:5174](http://localhost:5174) 打开可观测 Dashboard。未配置
`LLM_API_KEY` 时自动使用 MockProvider，基础链路仍可运行。
Dashboard 的车辆动态接口仅供本地演示；非开发环境必须设置
`DEBUG_VEHICLE_CONTROL=false`。

## 主要能力

- 62 个车控对象、150 条端侧意图 pattern，知识库驱动归一化、校验、安全门控和话术。
- 本地、云端混合多意图拆分，支持导航偏好、歌手等续接片段与主意图成组路由。
- 十一个云 Agent：导航、闲聊、点餐、停车支付、手册问答、行程规划、信息（天气/搜索/新闻/股票）、深度调研（多视角联网调研出带引用分节报告 + 渐进语音简报）、充能规划、场景编排、路况安全（含响应式主动播报）。
- 统一上下文装配（`ContextManager`：catalog 语义预筛 + 对话历史 + 长期语义记忆 + 结构化焦点态，统一 token 预算；敏感上下文按 manifest `context_scopes` 最小化下发）、对话记忆 + 长期语义记忆（自动学偏好/个人实体、pgvector 语义召回、可查可删）、确认/补槽续接、跨 Agent DAG、T2 自适应再规划。
- MiMo/Mock LLM Provider，MiMo ASR/TTS（批处理）+ **DashScope 实时流式 ASR**（qwen3/fun，识别上屏）+ MiMo 分块回退，webm→wav/PCM 后端流式转码。
- 复杂任务（行程/深度调研/多步）按统一 `is_complex` 判据**动态开思考**提质 + 气泡内嵌
  「过程区」四阶段折叠展示（理解需求→规划步骤→执行任务→整理结果，行车/泊车双态、脱敏不露 reasoning）；普通车控/闲聊零过程零额外延迟。
- HMI（Aurora Glass 极光液态座舱）流式文字、动作卡、记忆视图、**语音流式识别上屏**、九种音色和句子级增量播报。
- NATS 可观测事件、collector REST/WS、车辆状态 diff、端云 trace、Agent 健康/指标/熔断状态、
  debug 车辆动态与对照实验 Dashboard；collector `GET /metrics`（Prometheus 文本格式）+ 桥接
  真实 OTel span 导出，Grafana 仪表盘经 `--profile observability` 可选启用。
- 通讯链路韧性：全链路 gRPC keepalive + 优雅停机（依赖重启换 IP 自愈、不需重启依赖方）、
  HMI 退避重连 + 断线发送队列 + 请求看门狗、云端 Agent 熔断快速失败。

## 验证

```bash
python -m pytest --import-mode=importlib -q
python test/smoke_edge.py

cd hmi
npm test
npm run build

cd ../dashboard
npm test
npm run build
```

全栈运行后：

```bash
python test/e2e_ws.py
```

测试分布和环境说明见 [`test/README.md`](test/README.md)。

## 已知边界

- Cloud Gateway 的车辆长连接状态仍在单实例内存中，多实例需会话亲和或一致性路由。
- Registry 已实现 PgStore（PostgreSQL）持久化，内存 fallback 保留；各 Agent / edge / cloud-planner 周期重注册自愈，重启后自动补注册（无需人工）；多实例扩展仍待做。
- 导航（高德）、天气（和风 JWT）已接真实 Provider 并经真实凭证冒烟通过（接入规范见
  [`docs/guides/provider-integration.md`](docs/guides/provider-integration.md)）；餐饮/停车/手册仍为 mock，按环境接入。
- HTTP/MCP 外部工具及网络出口白名单尚未实现。
- 会话鉴权已由 **R3.1** 落地最小闭环：静态 token 两层校验（HMI WS `?token=` → edge-gateway 解析
  身份+`granted_scopes`、去 `user_id="u1"` 硬编码；Hello channel token → cloud-gateway 校验），env 门控
  `AUTH_REQUIRED` 默认关（配合 R2.2 `PERMISSIONS_FAIL_OPEN`）；真实 IdP/JWT 轮换/设备证书属后续。
- 轻量 span/指标/健康已接入 NATS Dashboard；**Prometheus/OTel 导出已由 R3.6 落地**（collector
  `GET /metrics` + 桥接真实 OTel span 导出 + Grafana 仪表盘，均经 `--profile observability`
  门控可选启用；Grafana 仪表盘已于 **R4.0**（2026-07-04）网络恢复后补验——三面板经数据源代理真实出数）；
  持久化 trace、告警规则、多车聚合与正式鉴权仍待实现。
- 当前 TTS 是“文本短句增量合成 + 顺序播放”，不是真正的服务端 PCM 音频流。
- 服务间 gRPC **已由 R3.2 支持双向 mTLS**（`GRPC_TLS` env 门控，默认关保持现状；`scripts/gen-certs.*`
  生成共享 mesh 证书，`GRPC_TLS=on` 全栈加密）；配合 R3.1 会话鉴权，**T3.1+T3.2 齐即安全链路无已知缺口**。
  证书轮换/per-service 证书/真实 IdP 属量产硬化后续。
- VAL 仍为 Python 模拟，真实 SOME-IP/CAN、车规资源约束和 OTA 属于后续量产阶段。

## 接手阅读顺序

1. [`AGENTS.md`](AGENTS.md)：当前进度、第一步和自检入口。
2. [`CLAUDE.md`](CLAUDE.md)：目录约定、安全红线和工程纪律。
3. [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)：架构唯一真相源。
4. [`docs/design/2026-06-14-cloud-central-orchestrator.md`](docs/design/2026-06-14-cloud-central-orchestrator.md)：云端中枢落地记录和后续清单。
5. [`docs/design/2026-06-15-observability-dashboard.md`](docs/design/2026-06-15-observability-dashboard.md)：可观测数据流、接口、安全边界与验收记录。
6. [`docs/dev-guide.md`](docs/dev-guide.md) 与 [`docs/conventions.md`](docs/conventions.md)：环境、端口、命名和调试。
7. 对应服务目录下的 README 和测试。
