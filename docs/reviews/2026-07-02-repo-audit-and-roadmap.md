# 全仓库审计与后续 Roadmap（2026-07-02）

> 范围：架构一致性审计 / 技术债清单 / 测试体系与量产级缺口 / Roadmap 任务拆解。
> 方法：通读架构基线（`docs/architecture/cockpit-agent-architecture.md`、`phase1-implementation-plan.md`、`AGENTS.md`）后逐模块核对实现（orchestrator cloud/edge、gateway、agents、_sdk、security、registry、memory、llm-gateway、deploy、test、CI）。
> 用法：§4 的任务卡可直接作为 Claude Code 会话的输入逐个执行。

---

## 执行进度（活文档 · 截至 2026-07-04 · 新会话先读这里）

> 本节随执行更新。接手方法：读本节 → 看 §4 中**未打 ✅** 的任务卡 → `git log --oneline` 对照 commit。
> 动编排核心前遵 `CLAUDE.md`「大改动先 Plan Mode」。现状另见 `AGENTS.md §4` 与记忆
> `r2.1-route-hints-mechanization.md`（Claude Code memory）。
>
> **✅ 2026-07-04 验收复审通过（R1–R3 全部 16 卡）**：独立复审报告见
> [`2026-07-04-acceptance-review-r1-r3.md`](2026-07-04-acceptance-review-r1-r3.md)（本地全量 1046 passed/0 failed
> 实跑 + GitHub API 独立查证 CI/nightly 全绿；2 项轻微残留 N1/N2 + 6 项已知边界 K1–K6 汇总其 §3；
> **R4 准入=通过**，建议先做 ≤1 天「R4.0 收尾包」（修 K1 pause 自愈 / K2 process_region / 清 N1 死注入）再进 R4 主线，优先级见其 §4）。

**✅ 已完成并合并 main（已 push origin）：**

- **R1 · 工程门禁与卫生（T1.1–T1.5 全 5 卡）** — 解决 D8/D4/D12/D16/A3/A8 + G1（CI 覆盖）。
  T1.5 media 统一 `44d9608` · T1.3 compose 生存性 `c5d2e41` · T1.4 文档同步 `9939a92` · T1.1 CI 补全 `b63aa1b` · T1.2 清理 `a204257`。
- **R2.1 = T2.1 · 路由兜底机制化（P0–P5，最高优先架构债）** — 解决最大风险 **A1/D5**（铁律「不改编排核心加 Agent」已恢复）+ D10 的 HEAVY/card 部分。
  编排核心 `planning`/`context`/`aggregator`/`progress` 四处领域硬编码**全清**：路由兜底 → 各 Agent `manifest.route_hints` + 通用 `RouteHintEngine`；`HEAVY_INTENTS` → `capability.heavy`；卡片优先级 → card `display_priority`；`_ALWAYS_INCLUDE` → env `PLANNER_FALLBACK_AGENT` + 通用「有 route_hints 的 Agent 留 catalog」。
  commit `63e1382`(proto)/`5600bc6`(引擎)/`541c941`(research+trip 路由)/`f48be86`(DoD#2 契约测试)/`cab4500`(trip.plan+抽取搬 Agent)/`2e6eeaa`(P3/P4/P5)/`737ddef`(registry round-trip 真栈修复)。
  验证：全量 **998 passed / 6 skipped** + 真栈 `e2e_trip`/`e2e_research` 全过。
- **R2.2 = T2.2 · 权限单轨化（A4/D3）** — 权限双轨（实为三处实现：planning 内联过滤 / dispatch
  内联校验 / PermissionEngine 死壳）收敛为**唯一决策** `security/permission.py::check_permission`，
  规划期 `_filter_by_permission` 与执行期 `dispatch` 同源复用；删 `engine._enforce_permissions`
  空壳；fail-open 加 env `PERMISSIONS_FAIL_OPEN` 门控（默认 `on` 保持现状，量产翻 `false`
  fail-closed）+ 结构化审计 `fail_open_default_scopes`。**对审计原话的纠偏**：直接接线
  `effective_scopes` 会因 `cap & granted` 扁平交集不做父子覆盖而误拒 `scene-orchestrator`
  （first_party 需父 scope `vehicle.control`），故取**零行为变化单轨**、trust-cap 强上限推迟 R3.1。
  commit `8999cba`(实现)/`0be9991`(merge)。验证：全量 **1014 passed / 6 skipped**（+16 用例）
  + 真栈 `e2e_ws` 4/4；落地记录 `docs/design/2026-07-02-r2.2-permission-single-track.md`。
- **R2.3 = T2.3 · 端云持久长连（A2/D2，含 A3 文档补记）** — Python `CloudClient`（edge-orchestrator）
  从逐请求建流升级为**进程内单条持久 bidi + corr_id 多路复用 + 15s 心跳 + 指数退避重连**，每次重连
  重建 channel 走 `dns:///` 重解析（换 IP 自愈），在途请求断连快速失败由上层降级；云侧
  `channelServer.Connect` 本就支持多路复用故未改。删 Go 死代码 `gateway/edge/ChannelClient`
  （~250 行，从未实例化）。commit `c7cdc01`(实现)/`ae8638d`(merge)。验证：全量
  **1016 passed / 6 skipped**（+2 用例）+ edge-gateway 镜像 `go build` 通过 + 真栈 `e2e_ws` 4/4 +
  **持久性探针**（3 云请求 cloud-gateway 仅 1 次 hello）+ **换 IP 自愈探针**（force-recreate
  cloud-gateway 新 IP → 未重启 edge 即自愈、新容器 1 次 hello）；落地记录
  `docs/design/2026-07-02-r2.3-edge-cloud-persistent-channel.md`。
- **R2.4 = T2.4 · info agent 拆域（D6）** — 1269 行 `InfoAgent` 巨类按域拆成
  `agents/info/src/handlers/{weather,search,sports,news,stock,briefing}` mixin + 共享 `_util`；
  `agent.py` 只留意图分发 + 公共件（城市解析/定位标注）+ provider 装配（**1269 → 123 行**）。
  域方法经 `self` 相互调用靠 MRO **逻辑逐字不变**；文件尾向后兼容重导出历史 helper（`from
  agents.info.src.agent import X` 路径不变、测试零改动）。manifest/端口/行为不变。commit
  `def815a`(实现)/`18e6f73`(merge)。验证：`pytest agents/info` 136 passed + 全量 **1016 passed / 6 skipped** 零回归；
  落地记录 `docs/design/2026-07-02-r2.4-info-agent-split.md`。
- **R2.5 = T2.5 · 跨 Agent 状态键契约化（A5）** — 三个隐性契约键（`news_active`/`research_active`/
  `trip_active`）**权威登记**入 `agents/_sdk/shared_state.py`（常量 + owner/reader/schema 表）+
  `docs/conventions.md §9`；`Context` 加 `save_shared_state`/`load_shared_state` 封装「写 `profile.<key>`、
  读 `profile.<key>` 命名空间」的前缀不对称；info/deep-research/trip-planner 全改用常量+helper。
  commit `9b1167c`(实现)/`0b390a6`(merge)。验收：业务码零裸字面量（grep 仅 `shared_state.py`+文档）；
  全量 **1016 passed / 6 skipped** 零回归。**至此 R2 架构还债（R2.1–R2.5）全部完成。**
- **R3.1 = T3.1 · 会话鉴权最小闭环（D1，P0-#1）** — 静态 token 两层鉴权全 env 门控、默认关（`AUTH_REQUIRED`
  默认 `false` 逐字保持现状）。层 1 `gateway/edge/auth.go`（`AUTH_TOKENS` 表→`Context.UserId/VehicleId`
  + `meta.granted_scopes`，网关对该键唯一权威剔除客户端伪造值，**去 `user_id="u1"` 硬编码**）；层 2
  cloud-gateway 校 Hello `session_token`∈`CLOUD_CHANNEL_TOKENS` + edge `cloud_client` Hello 带
  `CLOUD_CHANNEL_TOKEN`；HMI `ws.mjs::appendToken` + `VITE_WS_TOKEN`。**未改编排核心/proto**——R2.2 已
  备好 `context.py` 消费 `meta.granted_scopes` + `PERMISSIONS_FAIL_OPEN` 开关，本卡只把真实 scope 喂进去。
  验证：全量 **1018 passed / 6 skipped**（+2）+ Go build+`go test ./gateway/...` + HMI 42/42 + build；
  真栈默认模式 `e2e_ws` 4/4（非破坏）+ 秒模式 `test/e2e_auth.py` ALL PASS（无 token→401 / 带 token 车控
  +云端 / token'd 请求 cloud-planner **无** `fail_open_default_scopes`=scope 来自 token / `memory recall for u1`）。
  **仍 insecure gRPC（mTLS=R3.2）、静态 token 非量产 IdP。** 分支 `feat/r3.1-session-auth`（已 merge main `f38b4db` + push）；
  落地记录 `docs/design/2026-07-02-r3.1-session-auth.md`。
- **R3.2 = T3.2 · 服务间 mTLS（D1 剩余项）** — 服务间 gRPC 双向 TLS，全 env 门控默认关（`GRPC_TLS` 未设=insecure
  逐字保持现状）。**单张共享 mesh 证书 + name override**（`ssl_target_name_override`/`ServerName` 固定
  `cockpit-mesh`）解决 agent 动态容器 hostname + 免枚举 SAN。Python 共享工厂 `runtime/grpcio.py`（`aio_channel`
  secure + 新 `bind_port`，7 处 server 绑定切换 + 修 1 处 stray）；Go 新共享包 `gateway/tlscfg`（两网关 3 dial +
  cloud server）；`scripts/gen-certs.{ps1,sh}`（证书 gitignore）；compose `x-certs-vol` 挂 19 mesh 服务 + `GRPC_TLS` env。
  **未改 proto/编排核心逻辑**。验证：全量 **1030 passed/6 skipped**（+12）+ Go build+test（含 tlscfg）+ 默认模式
  `e2e_ws` 4/4（非破坏）+ mTLS 模式全栈 26 起 + `e2e_ws` 4/4 加密链路 + `test/e2e_mtls.py` ALL PASS（云端 mTLS 通 +
  insecure 探针被拒=强制）。已合并 main（`37817c8`）；落地记录 `docs/design/2026-07-02-r3.2-service-mtls.md`。
  **至此 T3.1+T3.2 齐，安全链路无已知缺口**（D1 收官；剩真实 IdP/JWT 轮换、per-service 证书轮换属后续硬化）。
- **R3.3 = T3.3 · e2e 入 CI 门禁（G2）** — 新 `.github/workflows/nightly-e2e.yml`（`schedule`+`workflow_dispatch`，
  全 mock 模式零 secrets），跑裁剪过的确定性子集（`ws`/`central_hub_assertions`[3 case]/`context`[4 case]/
  `memory`/`resilience`/`trip`/`research`/`research_async`，比卡片字面 5 个多纳入 trip/research/research_async——
  三者因 `route_hints`+确定性 fallback 生成器在 mock 下同样可靠）。**对卡片原描述的纠偏**：卡片字面 5 个脚本
  不能整份跑——MockProvider 非 JSON 输出下 planning 兜底路由到 chitchat（除非 Agent 声明 `route_hints`，全仓仅
  trip_planner/deep_research 声明），导致 central_hub 的导航/媒体 case、context 的 battery_query case、
  memory 链路 1/2/3（均依赖真实 embedding 或真实 chat）在纯 mock 下逻辑上必然失败；用 `--case` 过滤 + 给
  `e2e_memory.py` 三条链路补 SKIP guard（复用其链路 6 已有惯例）解决。`make e2e` 同步从空跑的
  `cd test && pytest -q`（收集不到任何 `e2e_*.py`）改为 `scripts/run_e2e.{sh,ps1}` 本地全量清单执行器。
  **未改编排核心**。commit `e54a914`(实现)/`cb70239`(文档)/`25b85aa`(首跑发现链路2遗漏后修复)。验证：全量
  **1030 passed/6 skipped** 零回归 + 本地 `run_e2e.sh` 对真实 key 栈 10/10（另发现 1 处与本卡无关的既有失败
  `e2e_process_region.py` 默认泊车态断言，已记录不顺手修）+ **GitHub `workflow_dispatch` 二次实跑全绿**
  （run `28639607108`，3m59s，含"断言型 e2e 套件"步骤本身 success）；落地记录
  `docs/design/2026-07-03-r3.3-e2e-ci-gate.md`。
- **R3.5 = T3.5 · 降级矩阵自动化（G4）** — 新 `test/e2e_degrade.py` 刻画架构 §3.3 四行真实现状：
  单 Agent 故障（`trip-planner-agent` stop/start，唯二 mock 下路由确定的 Agent 之一，断言可观测 span
  status 而非聚合器话术原文）/ LLM 超时（`llm-gateway/providers.py::MockProvider` 新增
  `LLM_MOCK_DELAY_MS` 测试钩子）/ 云 Planner 故障（`cloud-planner` stop/start，断言"云端处理异常"）/
  断网（`cloud-gateway` pause/unpause，断言车控秒回+"网络不太好"降级话术）。**真实跑（非纸面设计）
  暴露两处需要推翻重来**：① Row 4 原计划断言命中 `aggregator._ERROR_FRIENDLY["step_timeout"]` 固定
  话术不成立——chitchat 走 `engine.py` D0 单步流式直通、不受 executor 层超时包装管辖，重域 Agent 的
  "heavy"预算又被刻意放宽到 200s 都测不出来，改断言"LLM 变慢时系统仍优雅响应、不挂不崩"这一更朴素但
  真实成立的性质；② **额外发现第 4 处真实缺口**（研究阶段 3 处之外）：`cloud-gateway` pause/unpause
  后 `edge-orchestrator` 持久 channel 不会像"换 IP"场景那样自愈（日志反复 `Missed too many pongs` 后
  仍 `cloud channel not connected`），恢复步骤加显式 `restart edge-orchestrator` 兜底（不修此缺口，
  同前 3 处一视同仁记录留后续）（**✅ 此缺口已由 R4.0/K1 于 2026-07-04 修复**——真根因=app 心跳强制重连时
  `_cancel_stream()` 令 `read()` 抛 `CancelledError`、被 `_run` 当任务取消打死重连循环，真栈解冻后 ~2s 自愈,
  e2e_degrade Row 4 已改回自愈断言）。**未改编排核心**。commit `0355b1b`(实现)/`02a4896`(文档)。验证：
  本地三轮验证收敛到 4/4 全过 + 全量 **1030 passed/6 skipped** 零回归 + **GitHub `workflow_dispatch`
  一次实跑即全绿**（run `28643924654`，9m17s，未像 T3.3 那样需要二次修复——本地已提前发现并修正两处
  问题）；落地记录 `docs/design/2026-07-03-r3.5-degrade-matrix-e2e.md`。
- **R3.4 = T3.4 · 意图路由评测基线（G3）** — 新 `test/eval_fast_intent.py`（端侧 `fast_intent`）+
  `test/eval_route_hints.py`（云侧 `RouteHintEngine`），直调既有函数产出准确率/召回率报告
  （JSON+Markdown），基线入 `docs/reviews/eval/`；`.github/workflows/ci.yml` 新增非阻塞
  `intent-eval-baseline` job（`::warning::` 告警、不拦 PR，对应 roadmap"不阻塞，先观测"）。
  **对卡片原文的纠偏**：卡面写的"飞书 1465 意图库"标注语料实际不可得——原始表已 gitignore
  且磁盘不存在，只一次性用于生成 `commands.yaml`/`entities.yaml`，未保留标注语料；改用现有可得
  数据源（`orchestrator/edge/tests/corpus/` 29 条 + 新增 `test/eval_corpus/` 历史回归案例转录，
  共 39 条 edge + 8 条 route_hints），"补全飞书全量语料"列为后续增强、不阻塞验收。**关键实现
  发现**：`route_hints_cases.yaml` 的预期值不能照抄 `test_route_hints.py` 简化版单测 fixture——
  对着真实 `agents/trip_planner/manifest.yaml` 用 `--dump` 核实后发现"导航去第2天换一个"在真实
  manifest 下不是"guard 拦下=空路由"，而是被同一句话命中的 `trip.modify`（无 guard）接管，
  已按实测结果钉入基线。两套逻辑都是纯规则引擎（不经 LLM），"跌破阈值"落地为逐例回归比对
  （不是模糊统计阈值）；验收演练（临时改坏电池共现词检查 + `deep-research` route_hint pattern）
  均精确触发 `::warning::` 后撤销。**未改 `fast_intent.py`/`route_hints.py`/编排核心任何业务
  逻辑**。全量 **1037 passed/6 skipped**（+7，新增 `test/test_eval_common.py`）零回归；已合并
  main 并 push，GitHub Actions `intent-eval-baseline` job 随 push-to-main 实跑确认全绿；见
  `docs/design/2026-07-03-r3.4-intent-eval-baseline.md`。
- **R3.6 = T3.6 · Prometheus/OTel 导出（G5-G8）** — collector 新增 `/metrics`（手写 Prometheus
  文本暴露格式，零新依赖，`cockpit_agent_{calls_total,latency_seconds_avg,error_rate,
  circuit_state,healthy,health_fail_count}` 六个指标）+ `otel_bridge.py`（复用 `observability/
  tracing.py::setup_tracing()`——此前完整实现但从未被调用的死代码，桥接 NATS `obs.span` 事件为
  真实 OTel span，trace_id 用 sha256 哈希成确定性 128-bit ID 保证同 trace 分组，不做字节级父子
  SpanContext 链接因为现状 `parent_id` 几乎不被真实调用点填充）+ `deploy/docker-compose.yaml`
  新增 `prometheus`/`grafana` 两服务（本仓首次引入 Compose `profiles: ["observability"]`
  机制，默认 `make up` 不受影响）+ Grafana provisioning 与三面板 dashboard JSON（延迟/成功率/
  熔断状态）。**真栈数据链路已验证**：对真实运行的 26 容器技术栈跑 `test/e2e_ws.py` 制造流量，
  `/metrics` 正确输出真实 Agent 调用数/延迟（`food-ordering` 实测）；OTLP 三个新依赖经容器内
  `pip install` 验证零版本冲突。**Grafana 可视化面板未在本次验证**——本机网络环境当前对大文件/
  大数据块持续下载不稳定（pip 装 grpcio、docker 拉 prometheus/grafana 镜像层均卡死，换阿里云/
  daocloud 镜像源验证过是环境问题非代码问题），经用户确认先按当前验证程度收尾，留待环境恢复后
  补验证。**全量回归 897 passed/5 skipped 零失败**（排除 4 处与本卡无关的预先存在环境依赖测试：
  `test/test_asr_e2e.py` 需真实 LLM API、`llm-gateway/tests/test_transcode.py` 需本机没装的
  ffmpeg 二进制、`observability/tests/test_events.py`/`agents/info/` 疑似受真实 NATS/服务
  可达性影响行为不同——均未修复，只是排除出本次验证范围）。**未改 `orchestrator/cloud/{engine,
  dispatch,loop,circuit}.py`/`observability/metrics.py`/`agents/_sdk/*`/`observability/
  collector/store.py` 任何现有逻辑**；见 `docs/design/2026-07-03-r3.6-observability-
  prometheus-otel-export.md`。**至此 R3 量产硬化全部完成（T3.1-T3.6）。**

**⬜ 未完成（新会话可接续，按优先级）：**

| 任务 | 关联审计项 | 规模 | 备注 |
|---|---|---|---|
| ✅ ~~**R4.0 收尾包**~~ | K1/K2/N1（复审 §4）+ K3 | S | **已完成（2026-07-04）**，见 `docs/design/2026-07-04-r4.0-residual-cleanup.md`：K1 通道 pause/unpause 自愈（真根因=app 心跳强制重连 CancelledError 打死 `_run`，真栈 ~2s 自愈）/ K2 过程区 e2e 复位车态自足 / N1 PermissionEngine 死注入删 / K3 Grafana 三面板经数据源代理验证；全量 1050 passed |
| **R4.1 路由质量主题** | A6/D11/D7/K6 | L | ✍️ 设计已出：`docs/design/2026-07-04-r4.1-routing-quality.md` |
| **R4.2 流式 TTS + barge-in** | T4.2 | L | ✍️ 设计已出：`docs/design/2026-07-04-r4.2-streaming-tts-bargein.md` |
| R4 其余（T4.3/T4.4/T4.5/T4.6） | — | 见 §4 | 按需排期 |

**残留小尾**：`orchestrator/cloud/planning.py::_PLANNER_SYSTEM` 内一处 trip few-shot 示例属 **D10（Prompt 管理）**，非 D5 路由债、且不随 Agent 数增长——暂留，纳入未来 Prompt 资产化工作。

---

## 0. 结论速读

- **主干健康**：分层混合编排（T0/T1/T2）、统一契约+注册发现、规划/执行分离、危险动作确认、VAL 唯一车控路径——这五条架构承诺在代码层面成立，工程质量高于典型 PoC（keepalive/优雅停机/熔断/幂等都做了）。
- **最大架构风险不是某个 bug，而是一条铁律的系统性侵蚀**：「新增 Agent 不改编排核心」已事实失守——编排核心至少 4 处硬编码特定 Agent/意图知识（planning 正则兜底、_ALWAYS_INCLUDE、卡片优先级、HEAVY_INTENTS）。根因是弱 LLM 路由不可靠，每上一个重域 Agent 都要在核心加确定性兜底。**不机制化，这条债会随 Agent 数量线性增长**。
- **第二梯队问题**（截至审计时；括号内为后续处置）：端云"长连接"实为逐请求建流（持久版是 250 行死代码）→ **R2.3 已改持久多路复用 + 删死代码 ✅**；权限引擎双轨（PermissionEngine 从未被调用）→ **R2.2 已单轨化 ✅**；全链路零鉴权（PoC 已知）→ 待 R3.1；两个 1200-1600 行巨型文件（info agent、fast_intent）→ **info agent 已由 R2.4 拆域 ✅**，fast_intent 待 R2/R3 后续。
- **测试量大（973 单测）但门禁虚**：CI 只跑约一半测试目录，Go/前端不构建，断言型 e2e 全部手动，意图路由质量无评测基线。
- Roadmap 建议按「R1 门禁与卫生 → R2 架构还债 → R3 量产级硬化 → R4 能力演进」推进，R2.1（路由兜底机制化）是唯一必须优先的架构性任务。

---

## 1. 架构一致性审计

### 1.1 与架构基线一致的部分（验证通过，从简）

| 架构承诺 | 实现证据 |
|---|---|
| 分层混合编排 T0/T1/T2 | `orchestrator/edge/server.py` 快路径 A/A2/B；`cloud/engine.py` simple DAG + D0 流式直通；`cloud/loop.py` 有界循环（max_iters=2、budget 5s，env 可调） |
| 统一 gRPC 契约 + Manifest + 注册发现 | `proto/cockpit/agent/v1/agent.proto` 与架构 §4.2 一致（扩展 data/missing_slots/kind/context_scopes 均有注释）；`agents/_sdk/server.py` 自注册 + 周期重注册 |
| 规划/执行分离（LLM 不直连车控） | LLM 只产 JSON DAG → `planning._validated_steps` 校验 intent∈能力集（fail-closed，整计划拒绝）→ `executor.py` 确定性执行 → vehicle.control 回流端侧 `server._dispatch_cloud_actions` → VAL 门控 |
| 危险动作二次确认 | 端侧 `_confirm_required` 不秒回；云端 NEED_CONFIRM 挂起/恢复，`_restore` 的 confirmed 标记严格限定挂起步、不持久化防重放 |
| 车控只经 VAL | `edge_call.action_to_structured` → `val.execute` 全走归一化→校验→安全门控流水线 |
| 上下文分层与最小化 | ContextManager 统一装配 + 焦点态 + context_scopes 最小化下发（cloud unary 路径，edge/stream 不过滤——已在设计稿注明取舍） |
| 可观测 PoC | NATS best-effort span/metric/health/state + collector + dashboard，与 §10「当前 PoC 实现」描述一致 |
| 通讯加固 | `runtime/grpcio.py` 统一 keepalive/优雅停机；Go 侧 dns:///+显式 reconnect；熔断接线 dispatch |

### 1.2 架构偏差清单

#### A1（最重要）「编排对 Agent 无感」铁律系统性侵蚀 — ✅ 已由 R2.1 恢复（见顶部「执行进度」）

CLAUDE.md §3 / 架构 §1.1-3：新增 Agent 只通过注册接入，**0 改编排核心**（Phase 1 DoD #2）。现状编排核心至少 4 处硬编码特定 Agent/意图知识：

| 位置 | 硬编码内容 |
|---|---|
| `orchestrator/cloud/planning.py` | ~10 组领域正则（_TRIP_*、_RESEARCH_*）+ 6 个 `_ensure_*` 确定性兜底，字面量引用 `trip-planner`/`deep-research`/`chitchat` agent_id 与 trip.plan/modify/navigate/status/reschedule、research.run 意图 |
| `orchestrator/cloud/context.py` | `_ALWAYS_INCLUDE = ("chitchat", "trip-planner")`；`_CONTROL_FOCUS` 控制域表 |
| `orchestrator/cloud/aggregator.py` | `_card_priority` 硬编码 charging_route/trip_itinerary/research_report/poi_list 卡片类型 |
| `orchestrator/cloud/progress.py` | `HEAVY_INTENTS = {trip.plan, trip.modify, info.search, info.news, research.run, charging.plan}` |

- **根因**：弱 LLM（MiMo）路由不可靠 → 每个重域 Agent 上线都需要确定性兜底 → 兜底只能写在 planning.py。这不是代码风格问题，是「架构承诺」与「模型现实」的结构性矛盾。
- **影响**：新增重域 Agent 的实际成本 = Agent 本体 + 编排核心改动 + 正则相互作用回归（已发生过：裸「电池」劫持调研、「行程」含「行」误判确认——每次都是正则打架）。Agent 数量增长后此文件将不可维护。
- **建议**：把「确定性路由兜底」降为**通用机制**、把「领域知识」搬回 Agent 侧（manifest 声明式 route_hints / capability 属性），见任务 R2.1。

#### A2 端云通道：架构要求持久多路复用长连，实现是逐请求建流 — ✅ 已由 R2.3 落地（见顶部执行进度）

- 架构 §8/§12.3：「gRPC 双向流长连接（心跳、断线重连、多路复用）+ 设备证书/会话 token 鉴权」。
- 现实：`orchestrator/edge/cloud_client.py` 每个上云请求新建 bidi 流（hello 握手 → request → final 后关闭），文件头注释自认「Phase 2：持久长连 + 多路复用」。
- **`gateway/edge/main.go` 里已实现的持久 ChannelClient（connectLoop/pingLoop/recvLoop/多路复用，~250 行）从未被实例化 = 死代码**。实际 HMI→编排链路是 WS→edge-orchestrator gRPC 直连。
- 鉴权：Hello 只带 vehicle_id（cloud/main.go 注释「PoC 阶段简单通过」）；WS `CheckOrigin` 全放行；`UserId: "u1"` 硬编码单用户。
- 影响：每请求多一次握手 RTT；无认证（PoC 已知，但架构图与实现的差异未在架构文档标注）。

#### A3 组件归属漂移（文档未记录）

- 架构图：Edge Gateway 持端云长连。实现：Edge Orchestrator（Python）持有。
- HMI 的 ASR/TTS/流式识别直连 llm-gateway HTTP(50059)（`VITE_AUDIO_API_URL`），绕过 Edge Gateway——架构说 Edge Gateway 是「所有交互的入口」。
- 均属合理的 PoC 捷径，但应在架构文档「实现说明」里补记，避免接手者按图索骥。

#### A4 权限模型双轨制，PermissionEngine 是死代码 — ✅ 已由 R2.2 收敛（见顶部执行进度）

- `security/permission.py` 的 PermissionEngine（trust_level 上限 × 用户授权 × token scope 三源交集）被注入 `PlannerEngine.__init__`，但**生产代码从未调用**（仅测试引用）；`engine._enforce_permissions` 是空壳（自注 Phase 2）。
- 实际生效的是另一轨：`planning._filter_by_permission`（规划期，fail-closed）+ `dispatch.py` 的 `is_scope_covered` 散点校验 + third_party 硬禁令。
- `context._POC_DEFAULT_SCOPES` fail-open：请求不带 granted_scopes 时默认授予全部常用权限（有 warning log 与量产注记）。
- `docs/architecture/detailed/ws8-security-permission.md` 声称「统一 PermissionEngine、规划/执行双层校验」→ **文档与实现不符**。
- 建议：单轨化（见 R2.2）。

#### A5 跨 Agent 隐式耦合通道（memory KV 私有契约）— ✅ 已由 R2.5 契约化（见顶部执行进度）

- info 写 `profile.news_active` → deep_research 读之（「详细讲讲第 N 条」桥接）；`trip_active`、`research_active` 同模式。
- 这些跨 Agent 状态键没有任何声明位置（manifest 不声明、conventions.md 不记录），属于隐性契约——改 key/换存储会静默断链。
- 建议：conventions.md 建「跨 Agent 状态键」章节 + `_sdk` typed helper（见 R2.5）。

#### A6 Registry「语义路由」实为字符命中打分

- `registry/store.py::_score`：query 的字符集合与 capabilities 文本做交集计数（`0.3 + 0.05*hits`）。WS2 规划的向量检索未做。
- 影响：catalog 语义预筛（context.py）与规划失败 fallback 的 top-1 都靠它，中文单字命中噪声大；Agent 数 >20 触发预筛后质量不可信。embedding 基础设施已就绪（llm-gateway→百炼 v4），升级成本低（见 R4.1）。

#### A7 时延目标事实性搁置

- 架构 P0 约束：云端复杂意图首响 <1.5s / L3 <2s。现状：两网关端到端超时放宽至 90s，heavy Agent latency_budget 40-85s，靠过程区覆盖等待。
- PoC 的合理取舍（弱模型+免费档 API），但架构的 P0 时延表应加「当前现实」列，否则验收标准悬空。

#### A8 目录与文档小漂移

- 根目录存在**空的 `providers/` 目录**——不在 CLAUDE.md §3 目录约定中。
- `docs/conventions.md` intent 全集缺 trip.navigate / trip.status / trip.reschedule（manifest 与 AGENTS.md 均已有）。
- conventions.md「规划中 ticketing（50073）」与 deep-research 实占的 50073 端口冲突。
- 根目录 `debug-local.py` / `start-local.ps1` / `start-local.sh` 未入目录约定，与 dev-guide 的关系待确认（要么记录要么删）。

### 1.3 安全红线逐条审计

| 红线（CLAUDE.md §5） | 结论 |
|---|---|
| 车控只经 VAL | ✅ 成立（含云端场景动作回流 edge_call 路径） |
| LLM 不直连车控 | ✅ 成立（DAG 校验 fail-closed + action payload 校验 + VAL 终校验） |
| 危险动作二次确认 | ✅ 成立（端侧不秒回 + 云端闭环 + confirmed 不重放） |
| 不改编排核心加 Agent | ❌ **失守**（A1） |
| 密钥不进代码/commit/日志 | ✅ 基本成立（.env gitignore；日志抽查无 key；compose 内 PG 密码 `cockpit` 硬编码属 PoC 默认凭证，量产需 secret 化） |
| 敏感数据最小化上云 | ✅ 机制落地（context_scopes；edge/stream 路径不过滤已注明取舍） |

---

## 2. 当前代码问题 / 技术债清单

标注：P0=量产阻断或明确的坏味道放大器；P1=影响可维护性/正确性风险；P2=卫生。

### P0

| # | 债 | 位置 | 说明 |
|---|---|---|---|
| D1 | 全链路零鉴权 — ✅ 由 R3.1+R3.2 收官 | gateway/{edge,cloud}, context.py, runtime/grpcio.py | **R3.1** 会话鉴权最小闭环（WS/Hello 静态 token 校验、`granted_scopes` 由 token 注入、去 `user_id="u1"` 硬编码）+ **R3.2** 服务间 mTLS（gRPC 双向 TLS，`GRPC_TLS` 门控）——**T3.1+T3.2 齐即「安全链路无已知缺口」**。二者均 env 门控默认关、不破坏现有开发流。**后续硬化**（非阻断）：真实 IdP/JWT 轮换/设备证书、per-service 证书轮换 |
| D2 | 端云逐请求建流 + 死代码 | cloud_client.py / gateway/edge/main.go | 见 A2。死代码要么删要么用，不能两份并存 |
| D3 | 权限双轨 | security/permission.py vs dispatch/planning | 见 A4。双轨=改一处漏一处 |
| D4 | compose 无 restart 策略、无资源限制 | deploy/docker-compose.yaml | `restart:` 出现 0 次；服务 crash 后不自愈（演示中风险高，一行/服务即可修） |

### P1

| # | 债 | 位置 | 说明 |
|---|---|---|---|
| D5 | 路由兜底正则堆积 | planning.py（603 行） | A1 同根。每个重域 Agent O(1) 项核心改动+正则互扰回归 |
| D6 | info agent 巨类 — ✅ R2.4 已拆 | agents/info/src/agent.py（1269→123 行）+ handlers/ | 天气/搜索/新闻/赛事/股票五域 + 早报 + 画像排序 ~60 方法一个类。**R2.4 拆成 handlers/ mixin，域间隔离**，见执行进度 |
| D7 | fast_intent 单文件规则引擎 | orchestrator/edge/fast_intent.py（1558 行） | 规则数据与引擎逻辑未分离（VAL 侧已用 knowledge/*.yaml，fast_intent 仍代码内嵌）。量产要 OTA 下发白名单/阈值，代码内嵌无法 OTA |
| D8 | media action_type 判定三处重复且清单不一致 | orchestrator/edge/server.py | 快路径 A/A2 用 9 对象清单（含 audiobook/opera/news/video/TV），CLOUD-DEGRADED 兜底只有 ("media","music","radio")，快路径 B 只有 ("media",)——同一意图不同路径打不同 action type，HMI 侧行为不一致是潜伏 bug |
| D9 | 宽异常吞噬 | 全仓 | 非测试代码 177 处 `except Exception`（28 处直接 pass）。观测 best-effort 合理；但业务路径混用同一模式，故障静默化。缺「可吞/不可吞」的分类约定 |
| D10 | Prompt 散落无管理 | planning.py/aggregator.py/各 Agent | 架构 §2.2 说 LLM Gateway 负责「Prompt 管理」；实际几百行 prompt 字符串内嵌各处，无版本/评测锚点。弱模型下 prompt 是核心资产，改 prompt 无回归手段 |
| D11 | Registry 打分弱 | registry/store.py | 见 A6 |
| D12 | CI 依赖手工清单与 requirements 漂移 | .github/workflows/ci.yml | CI `pip install` 手写包清单（缺 zhconv/redis/asyncpg 等），与 8 份 requirements.txt 平行维护；应改 `pip install -r` 聚合 |
| D13 | 镜像互含全量代码 | agents/*/Dockerfile | 每个 Agent 镜像 `COPY agents /app/agents`（含所有其他 Agent），镜像肥大、边界模糊。PoC 可接受，量产需拆 |
| D14 | 会话态序列化脆弱 | engine.py `_suspend` | `completed_results={r.step_id: r.__dict__}` 直接 dict 化 dataclass，字段演进时静默丢/多字段（_restore 有白名单兜底，风险可控但值得收紧为显式 to_dict） |
| D15 | HMI 卡片单文件 | hmi/src/components/Cards.tsx（1115 行） | 10+ 卡族一个文件，A 类数据缺口扩展（types.ts 扩字段）都会碰它 |

### P2

| # | 债 | 位置 |
|---|---|---|
| D16 | 空 `providers/` 目录、debug-local.py/start-local.* 未入约定 | 根目录 |
| D17 | SDK/服务启动用 print 而非 logging（17 处） | agents/_sdk/server.py 等 |
| D18 | conventions.md 漂移（trip P1/P2 意图缺失、ticketing 端口冲突） | docs/conventions.md |
| D19 | e2e 脚本重复的 Windows GBK 编码 hack | test/e2e_*.py |
| D20 | `_extract_json` 首 `{` 尾 `}` 截取，多 JSON/嵌套围栏时脆弱（有重试+fallback 兜底） | planning.py |

---

## 3. 测试体系与量产级缺口

### 3.1 现状盘点（客观强项）

- **单测规模**：103 个测试文件，全量 973 passed / 6 skipped。覆盖编排核心全模块、各 Agent 契约+provider、memory 8 场景集、SDK 韧性（重注册/熔断/护栏）、安全 scope。
- **数据驱动语料层**：端侧 corpus 88 条参数化（安全门控/车控对象矩阵/多意图边界）+ nightly 真实 LLM 语料 4 条（默认 skip）。
- **断言型全栈 e2e**：central_hub 7/7、context 6/6、memory 6/6、resilience 2/2、trip 6 轮、research、process_region、observability——但全部需手动 `make up` 后跑。
- **前端**：HMI node --test 38/38 + 构建；dashboard 10/10。Go 仅 cloud idempotency 一个测试。
- smoke_edge 13/13 无需 docker，作为最快回归锚点很好用。

### 3.2 缺口清单（G 编号，量产视角）

| # | 缺口 | 事实 | 量产要求（Phase1 DoD 对照） |
|---|---|---|---|
| G1 | **CI 门禁覆盖不全** | ci.yml pytest 只跑 `test/ orchestrator/cloud/tests security/tests observability/tests agents/`——**漏 orchestrator/edge/tests、memory/tests、registry/tests、llm-gateway/tests**；Go 网关不 build 不 test；HMI/dashboard 不 build 不 test；无 lint/类型检查 | CI 绿必须等价本地全量绿 |
| G2 | **e2e 零门禁** | 断言型 e2e 全部手动；`make e2e`（`cd test && pytest`）实际收集不到 `e2e_*.py`（不匹配 `test_*` 模式）——**命名让人误以为在跑 e2e** | DoD#8：场景回归 ≥95% 入 CI 门禁 |
| G3 | **意图路由质量无评测基线** | 飞书 1465 条意图库在仓但未变成 fast_intent 准确率报告；planner 路由正确率无标注集；兜底正则「不误伤」只有零散单测（且已多次出过误伤回归） | 架构 §10 评测体系第一条 |
| G4 | 降级矩阵未系统化验证 | resilience e2e 只覆盖「依赖换 IP」2 条；§3.3 四行降级矩阵（断网/云故障/Agent 故障/LLM 超时）无自动化用例 | DoD#4 |
| G5 | 无时延/负载基线 | P0 约束 <500ms/<1.5s 无任何守护测量；无压测脚本 | 架构 §1.2 P0 |
| G6 | 覆盖率不可见 | 无 coverage 报告——973 个测试的盲区（Go 网关、http_server ASR 流、VAL 长尾分支）未知 | 工程常规 |
| G7 | slot_refs 数据形状无契约 | planner 依赖 `s1.data.items.0.id` 这类路径，但 Agent data 结构无 schema/契约测试，重构 data 字段会静默断编排 | 契约测试应双向 |
| G8 | 注入/对抗测试薄弱 | injection.py 为正则黑名单（易误伤「扮演角色」类正常输入、漏间接注入）；无对抗评测集 | ws8 / 架构 §9.4 |

---

## 4. Roadmap 与 Claude Code 任务拆解

> 原则：先修「放大器」（门禁+死代码），再还架构债（恢复铁律），再补量产硬化，最后扩能力。
> 每张任务卡可直接粘贴给 Claude Code 执行；卡内「验收」即完成判据。规模：S≈半天内，M≈1-2 天，L≈3-5 天。

### R1 · 工程门禁与卫生（防守，全部 S/M，建议 1 周内清完）

**T1.1 CI 补全到「CI 绿=本地全量绿」（M）** ✅ 已完成（`b63aa1b`）
- 背景：G1/D12。
- 任务：① pytest 目录对齐本地全量（加 orchestrator/edge/tests memory/tests registry/tests llm-gateway/tests）；② 依赖改为聚合安装各 requirements.txt；③ 加 `go build ./... && go test ./...`（gateway）；④ 加 hmi/dashboard 两个 job：`npm ci && npm test && npm run build`；⑤（可选渐进）ruff check 只对新改动文件。
- 验收：GitHub Actions 一次运行覆盖 973 单测 + Go + 前端构建全绿；故意注释掉一个 edge 测试断言能让 CI 变红。

**T1.2 死代码与杂物清理（S）** ✅ 已完成（`a204257`，保守版：删空 providers/ + 孤儿脚本；gateway ChannelClient 保留待 R2.3 定去留）
- 背景：A2/D16。
- 任务：① 删除 `gateway/edge/main.go` 未实例化的 ChannelClient（整段挪入 `docs/design/` 附录留作 R2.3 参考）；② 删根目录空 `providers/`；③ 审计 debug-local.py / start-local.ps1 / start-local.sh：仍被 dev-guide 引用则入 CLAUDE.md §3 目录表，否则删。
- 验收：`go build ./...` 通过；CLAUDE.md §3 与根目录实际内容一一对应。

**T1.3 compose 生存性（S）** ✅ 已完成（`c5d2e41`）
- 背景：D4。
- 任务：全部长驻服务加 `restart: unless-stopped`；postgres/redis 加 healthcheck；（可选）关键服务加 mem 限额。
- 验收：`docker kill` 任一 Agent 容器后自动拉起，e2e_ws 4 链路仍过。

**T1.4 文档同步（S）** ✅ 已完成（`9939a92`）
- 背景：A3/A8/D18。
- 任务：① conventions.md 补 trip.navigate/status/reschedule、修 ticketing 端口；② 架构文档「实现说明」补记：端云通道现为逐请求流（目标态持久多路复用）、HMI 音频直连 llm-gateway、长连由 edge-orchestrator 持有；③ ws8 detailed 文档改为如实描述当前单轨校验。
- 验收：文档检索上述关键词与代码一致；AGENTS.md §4 增补一行指向本审计。

**T1.5 media action_type 判定统一（S）** ✅ 已完成（`44d9608`）
- 背景：D8。
- 任务：orchestrator/edge/server.py 三处内联判定收敛为一个 `_action_type(obj)` 帮助函数，对象清单以 VAL knowledge/commands.yaml 的媒体类对象为准。
- 验收：新增单测覆盖三条路径同一对象得同一 action_type；`python test/smoke_edge.py` 13/13。

### R2 · 架构还债（核心，恢复铁律）

**T2.1 路由兜底机制化——恢复「编排对 Agent 无感」（L，最高优先）** ✅ 已完成（P0–P5，含 card `display_priority`/`capability.heavy`/always-include→env；真栈 e2e 全过；见顶部「执行进度」）
- 背景：A1/D5。这是本次审计唯一「不做会持续恶化」的架构任务。
- 方案：manifest 增加声明式路由提示，编排核心只留通用引擎：
  ```yaml
  # agents/trip_planner/manifest.yaml 新增
  route_hints:
    - pattern: "第\\s*[一二两三四五六七八九十\\d]+\\s*天[^，。！？]*?(换|改|调整|...)"
      intent: trip.modify
      slots: {modification: "$text"}     # $text=原话, $1..=捕获组
      policy: replace                     # replace=取代误规划 | append=并列补步 | route=单步路由
      priority: 30                        # 冲突时高优先
      guard: "换|改|调整|删|加"            # 可选反例守卫（对应 _TRIP_NAV_BLOCK_RE 语义）
  ```
  - planning.py 的 `_ensure_*` 与正则全部迁到对应 Agent 的 manifest；核心新增通用 `RouteHintEngine`（加载所有已注册 manifest 的 hints，按 priority 排序应用，逻辑与现 `_ensure_*` 顺序语义等价）。
  - `progress.HEAVY_INTENTS` → manifest capability 加 `heavy: true`（proto Capability 增字段或经 description 约定，建议 proto 加 `bool heavy = 6`，走「先改 proto 再 codegen」流程）。
  - `aggregator._card_priority` → 卡片 payload 自带 `display_priority`（Agent 出卡时声明），聚合器通用取值。
  - `context._ALWAYS_INCLUDE` → chitchat 经 env `PLANNER_FALLBACK_AGENT`；trip-planner 不再需要 always-include（hint 引擎经 registry resolve 目标 Agent，不依赖 catalog 预筛结果）。
- 迁移纪律：正则**逐字搬运**不改语义；每搬一组跑对应回归（test_planning/test_regression_intent_integrity/trip/research 相关）。
- 验收：① `grep -n "trip-planner\|deep-research\|research\.run\|trip\." orchestrator/cloud/planning.py` 无领域字面量（chitchat 仅存于 env 默认值）；② 973 全量零回归；③ 演练：新建一个假 Agent 仅靠 manifest route_hints 即可获得确定性路由（写成契约测试固化 DoD#2）。

**T2.2 权限单轨化（M）** ✅ 已完成并合并 main（`8999cba`/`0be9991`；见顶部执行进度。实施取**零行为变化单轨**——纠偏见 `docs/design/2026-07-02-r2.2-permission-single-track.md` §2）
- 背景：A4/D3。
- 任务：二选一并执行——推荐**接线 PermissionEngine**：① Step 已带 trust_level/required_permissions，dispatch 改调 `perms.check()`（AuthContext 由 PlanContext 构造）；② planning._filter_by_permission 复用同一 engine 的判定函数；③ 删 engine._enforce_permissions 空壳或使其真校验；④ _POC_DEFAULT_SCOPES 加 env 开关 `PERMISSIONS_FAIL_OPEN=true`（默认保持现状，量产翻转），warning 升级为结构化审计事件。
- 验收：test_ws8_security 全过 + 新增「dispatch 层越权硬拒」用例；全仓只剩一处权限判定实现。

**T2.3 端云持久长连（M/L）** ✅ 已完成并合并 main（`c7cdc01`/`ae8638d`；见顶部执行进度。持久客户端落在
Edge Orchestrator Python 侧、非架构图的 Go 网关；Go 死代码 ChannelClient 已删；换 IP 自愈 + 持久性
真栈探针均过。落地记录 `docs/design/2026-07-02-r2.3-edge-cloud-persistent-channel.md`）
- 背景：A2/D2。
- 任务：cloud_client.py 升级为持久 bidi + 多路复用 + 心跳 + 断线重连（逻辑可翻译自 T1.2 归档的 Go ChannelClient）：进程内单连接常驻，corr_id 复用 uuid4，请求映射 `corr_id → asyncio.Queue`；连接断开时在途请求快速失败并由上层降级话术兜底。
- 验收：e2e_resilience 新增用例——请求进行中重启 cloud-gateway，连接自愈且后续请求 <1 次握手；e2e_ws 4 链路过；对比日志确认不再每请求 hello。

**T2.4 info agent 拆域（M）** ✅ 已完成并合并 main（`def815a`/`18e6f73`；见顶部执行进度。agent.py 1269→123 行，mixin 拆域零行为变化，落地记录 `docs/design/2026-07-02-r2.4-info-agent-split.md`）
- 背景：D6。
- 任务：`agents/info/src/` 拆 `handlers/{weather,search,news,sports,stock}.py` + `briefing.py`（早报/proactive），agent.py 只留意图分发与公共件（城市解析/定位标注）。对外 manifest/端口/行为不变。
- 验收：info 全部既有测试零回归（`pytest agents/info`）；agent.py ≤300 行。

**T2.5 跨 Agent 状态键契约化（S）** ✅ 已完成并合并 main（`9b1167c`/`0b390a6`；见顶部执行进度。`agents/_sdk/shared_state.py` 常量登记 + `Context.save/load_shared_state` 封装 + conventions §9；业务码零裸字面量）
- 背景：A5。
- 任务：① conventions.md 新章节「跨 Agent 状态键」登记 news_active/research_active/trip_active（owner/reader/schema/TTL）；② `_sdk` 增 typed helper（如 `ctx.shared_state("news_active")`）封装 key 拼写。
- 验收：grep 字面量 key 只出现在 helper 与文档。

### R3 · 量产级硬化（Phase 2 前奏）

**T3.1 会话鉴权最小闭环（M）** ✅ 已完成（`feat/r3.1-session-auth`；真栈默认 `e2e_ws` 4/4 + 秒模式 `e2e_auth` ALL PASS；落地记录 `docs/design/2026-07-02-r3.1-session-auth.md`）
- 背景：D1。
- 任务：静态 token 起步：HMI WS 连接带 `?token=`（env 注入）→ edge-gateway 校验并注入 user_id/vehicle_id → Hello 带 token → cloud-gateway 校验 → granted_scopes 从 token 声明解析（PoC 用 env 配置的 scope 映射）。移除 user_id="u1" 硬编码。
- 验收：无 token 的 WS/Hello 被拒；e2e 带 token 全过；granted_scopes 不再来自 _POC_DEFAULT_SCOPES（fail_open 开关默认关闭）。
- 落地：两层鉴权全 env 门控、默认关（`AUTH_REQUIRED` 默认 `false` 逐字保持现状）。层 1 `gateway/edge/auth.go`（`AUTH_TOKENS` 表→身份+`meta.granted_scopes`，网关对该键唯一权威去客户端伪造，去 `user_id="u1"` 硬编码）；层 2 cloud-gateway 校 Hello `session_token`∈`CLOUD_CHANNEL_TOKENS`、edge cloud_client Hello 带 `CLOUD_CHANNEL_TOKEN`；HMI `appendToken`+`VITE_WS_TOKEN`。**未改编排核心/proto**（R2.2 已备好 `context.py` 消费 `meta.granted_scopes`+`PERMISSIONS_FAIL_OPEN`）。验收真栈：无 token→401 拒、带 token 车控+云端过、token'd 请求 cloud-planner **无** `fail_open_default_scopes`（scope 来自 token）。全量 1018 passed/6 skipped、Go build+test、HMI 42/42。**仍 insecure gRPC（mTLS=R3.2）、静态 token 非量产 IdP**。

**T3.2 服务间 mTLS（M）** ✅ 已完成（`feat/r3.2-service-mtls`；真栈 `GRPC_TLS=on` 全栈 26 起 + `e2e_ws` 4/4 走加密链路 + `e2e_mtls` ALL PASS；落地记录 `docs/design/2026-07-02-r3.2-service-mtls.md`）
- 背景：AGENTS.md 自认「唯一遗留生产缺口」。
- 任务：脚本生成自签 CA+证书（scripts/gen-certs.*）；runtime/grpcio.py 增 `secure_channel/secure_port`（env `GRPC_TLS=on` 切换）；Go 侧对应。默认关（不破坏现有开发流），compose profile `tls` 演示开。
- 验收：`GRPC_TLS=on` 下全栈起、smoke+e2e_ws 过；抓包确认加密。
- 落地：服务间 gRPC 双向 TLS，全 env 门控默认关（`GRPC_TLS` 未设=insecure 逐字保持现状）。**单张共享 mesh 证书 + name override**（`ssl_target_name_override`/`ServerName` 固定 `cockpit-mesh`）解决 agent 用动态容器 hostname 注册 endpoint、免枚举 SAN。Python 经共享工厂 `runtime/grpcio.py`（`aio_channel` secure + 新 `bind_port`，7 处 server 绑定切换 + 修 1 处 stray channel）；Go 新共享包 `gateway/tlscfg`（两网关 3 dial + cloud server）；`scripts/gen-certs.{ps1,sh}` 生成证书（gitignore）；compose `x-certs-vol` 挂 19 个 mesh 服务 + `GRPC_TLS` env。**未改 proto/编排核心逻辑**。验证：全量 **1030 passed/6 skipped** + Go build+test（含 tlscfg）+ 默认模式 `e2e_ws` 4/4（非破坏）+ mTLS 模式 `e2e_ws` 4/4 加密链路 + `e2e_mtls`（云端 mTLS 通 + insecure 探针被拒=强制）。**取舍**：共享证书非量产（应 per-service+SPIFFE/轮换）；HMI WS 明文（范围外）。**至此 T3.1+T3.2 齐，安全链路无已知缺口。**

**T3.3 e2e 入门禁（M）** ✅ 已完成（`feat/r3.3-e2e-ci-gate` 已 merge main；GitHub `workflow_dispatch`
二次实跑全绿 run `28639607108`；落地记录 `docs/design/2026-07-03-r3.3-e2e-ci-gate.md`；见顶部「执行进度」）
- 背景：G2。
- 任务：① 新 GitHub Actions nightly workflow：compose up（无 LLM key 走 mock）→ 顺序跑断言型 e2e（central_hub/context/memory/ws/resilience）→ 产物上传日志；② `make e2e` 改为显式执行 e2e 脚本清单（修正「假 e2e」）；③ PR 门禁保留 smoke_edge。
- 验收：nightly 在 GitHub 实际跑通一次全绿；本地 `make e2e` 输出与脚本清单一致。
- 落地：卡片字面 5 个脚本经验证不能在纯 mock 下整份跑通（route_hints 只有 trip_planner/deep_research 声明，
  其余 Agent 在 MockProvider 非 JSON 输出下兜底落 chitchat）——改为 `--case` 过滤 central_hub/context 的
  mock-safe 子集 + `e2e_memory.py` 三条依赖真实 LLM/embedding 的链路补 SKIP guard，另纳入 mock 下同样可靠的
  trip/research/research_async（route_hints+确定性 fallback）。首次实跑发现链路 2「planner召回注入」遗漏
  （弱字面重叠召回同样依赖真实 embedding，前期分析漏判为 mock-safe），修复后二次实跑全绿。`make e2e` 改用
  `scripts/run_e2e.{sh,ps1}` 本地全量清单执行器。**未改编排核心**。

**T3.4 意图路由评测基线（M）** ✅ 已完成（`feat/r3.4-intent-eval-baseline`；见顶部「执行进度」；
落地记录 `docs/design/2026-07-03-r3.4-intent-eval-baseline.md`）
- 背景：G3。
- 任务：① `test/eval_fast_intent.py`：以飞书 1465 意图库+88 corpus 为标注集，输出准确率/召回率报告（JSON+markdown），基线入库；② planner 确定性路由（route_hints 命中）准确率同法；③ CI 比对基线，跌破阈值告警（不阻塞，先观测）。
- 验收：跑一次生成基线报告入 `docs/reviews/eval/`；故意改坏一条正则能被评测抓到。
- 落地：飞书 1465 语料不可得（已 gitignore 且磁盘不存在），改用现有 `orchestrator/edge/tests/corpus/`
  + 新增 `test/eval_corpus/` 历史回归转录（edge 39 条 / route_hints 8 条）；`test/eval_common.py`
  报告基础设施 + `eval_fast_intent.py`/`eval_route_hints.py` 两个评测入口 + `ci.yml` 新增非阻塞
  `intent-eval-baseline` job。`route_hints_cases.yaml` 预期值经 `--dump` 对真实 manifest 实测校验
  （不照抄简化单测 fixture，发现并钉入一处真实交叉命中行为）。验收演练（改坏电池共现词检查/
  `deep-research` pattern）均精确触发告警。未改任何业务逻辑；全量 1037 passed/6 skipped 零回归；
  已合并 main 并 push，GitHub Actions `intent-eval-baseline` job 实跑确认全绿。

**T3.5 降级矩阵自动化（M）** ✅ 已完成（`feat/r3.5-degrade-matrix` 已 merge main；GitHub
`workflow_dispatch` 一次实跑全绿 run `28643924654`；落地记录
`docs/design/2026-07-03-r3.5-degrade-matrix-e2e.md`；见顶部「执行进度」）
- 背景：G4。
- 任务：e2e_degrade.py 覆盖 §3.3 四行：断网（pause cloud-gateway→车控仍本地秒回+降级话术）、云 Planner 故障、单 Agent 故障（FAILED step 不炸 DAG+fallback）、LLM 超时（mock 慢响应→占位+降级）。
- 验收：4 用例断言型全过并纳入 nightly。
- 落地：单 Agent 故障选 trip-planner（唯二 mock 下路由确定的 Agent，断言可观测 span status 而非
  聚合器话术原文——executor.py 丢 error.message 导致快速失败话术是通用"处理失败"）；LLM 超时给
  `MockProvider` 新增 `LLM_MOCK_DELAY_MS` 测试钩子，但真实跑发现原计划"命中固定超时话术"不成立
  （chitchat 走 D0 流式直通不受 executor 超时管辖，heavy Agent 预算又放宽到测不出来），改断言
  "系统变慢时仍优雅响应"这一更朴素但真实成立的性质；真实跑还额外发现一处非本卡引入的缺口——
  `cloud-gateway` pause/unpause 后 `edge-orchestrator` 不像"换 IP"场景那样自愈，恢复步骤加显式
  重启兜底（不修，记录留后续）。**未改编排核心**。

**T3.6 Prometheus/OTel 导出（M）** ✅ 已完成（`feat/r3.6-observability-export`；见顶部「执行进度」；
落地记录 `docs/design/2026-07-03-r3.6-observability-prometheus-otel-export.md`）
- 背景：架构 §10 目标态。
- 任务：collector 增 `/metrics`（Prometheus 格式，聚合现有 NATS 指标）；可选 OTLP span 导出开关；compose 加 prometheus+grafana profile 与最小 dashboard json。
- 验收：Grafana 能看到 Agent 时延/成功率/熔断状态曲线。
- 落地：`/metrics` 手写 Prometheus 文本格式（零新依赖）+ `otel_bridge.py`（复用此前从未调用的
  `tracing.py::setup_tracing()` 死代码）+ compose 首次引入 `profiles` 机制门控 prometheus/
  grafana + Grafana provisioning 与三面板 dashboard JSON。真栈数据链路（真实 Agent 调用→
  `/metrics`）已验证；**Grafana 可视化面板因本机网络环境限制（大文件/镜像拉取不稳定，经
  阿里云/daocloud 镜像源交叉验证确认是环境问题非代码问题）未在本次会话验证**，经用户确认
  按当前程度收尾。全量回归 897 passed/5 skipped 零失败（排除 4 处与本卡无关的预先存在环境
  依赖测试）。未改编排核心/`observability/metrics.py`/`agents/_sdk` 任何现有逻辑。

### R4 · 能力演进（产品向，按需排期）

> 2026-07-04 验收复审（见顶部执行进度指针）确认 R4 准入。**排序最高两项已出详细设计**（可直接接手执行）：
> - **R4.1 路由质量主题**（= T4.1 + K6 意图覆盖 + D7-lite）→ [`docs/design/2026-07-04-r4.1-routing-quality.md`](../design/2026-07-04-r4.1-routing-quality.md)（P0 Registry 真向量·顺带修 hash 伪向量现存 bug / P1 resolve 评测 / P2 8683 语料资产化+覆盖率报告 / P3 quick-win 扩规则 72%→≥82%；裸「取消」不得端侧接住的坑已写死）
> - **R4.2 流式 TTS + barge-in** → [`docs/design/2026-07-04-r4.2-streaming-tts-bargein.md`](../design/2026-07-04-r4.2-streaming-tts-bargein.md)（P0 CosyVoice 探针硬 gate / P1 WS 流式端点 / P2 HMI PCM 播放+无感回退 / P3 打断 v1 确定性+v2 语音实验性）
> - 开工顺序建议：先做验收复审 §4 的「R4.0 收尾包」（K1 pause 自愈 / K2 process_region / N1 死注入，≤1 天），再进 R4.1 → R4.2。

| 任务 | 内容 | 前置 |
|---|---|---|
| **R4.1 = T4.1+K6+D7-lite 路由质量主题（L）✍️ 设计已出** | Registry 真语义路由（llm-gateway embed，删 hash 伪向量）+ 飞书 8683 语料资产化 + 覆盖率 72%→≥82% quick-win 扩规则；NLU 路径 defer 带触发条件 | 无（设计定稿） |
| **R4.2 = T4.2 服务端流式 TTS + barge-in（L）✍️ 设计已出** | DashScope CosyVoice 流式（复用 fun-asr 已破解的 run-task 协议）+ WS /api/tts/stream + HMI PCM 调度播放 + 打断；探针硬 gate 先行 | 无（设计定稿） |
| T4.3 端侧 SLM 离线兜底（L） | 断网简单问答（架构 §3.3 可选项）；先做端侧模型基准测试（风险 R1） | — |
| T4.4 剩余 mock 真实化（M×3） | food/parking 真实平台或沙箱；manual-rag 换 pgvector 车书库（多车型隔离+出处） | — |
| T4.5 HMI P5 行车态 / P6 Dashboard（M） | 等 Figma A-8 行车态帧 / B 帧 | 设计稿 |
| T4.6 注入防护升级（M） | 正则黑名单 → 结构化隔离（wrap_data_section 全链路强制）+ 对抗评测集；误伤样本（「扮演导游」）回归 | T3.4 评测框架 |

### 推进建议

1. **先 R1（一周内）**：T1.1 CI 补全是所有后续任务的安全网，必须第一个做。
2. **R2.1 单独开分支、分 Agent 逐个迁移**（trip → research → 卡片/HEAVY_INTENTS → always-include），每步全量回归；这是唯一动编排核心的高风险任务，用 Plan Mode 先出迁移清单再动手。
3. R3 各任务相互独立，可穿插在能力开发之间；T3.1+T3.2 做完即可宣称「安全链路无已知缺口」。
4. 每完成一张卡，同步更新 AGENTS.md §4 状态表与本文件的勾选状态。
