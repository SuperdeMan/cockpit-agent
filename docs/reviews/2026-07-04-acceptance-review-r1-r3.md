# R1–R3 验收复审报告（2026-07-04）

> 复审对象：`2026-07-02-repo-audit-and-roadmap.md` §4 的 R1（5 卡）+ R2（5 卡）+ R3（6 卡）共 16 张任务卡的执行结果（61 个 commit，e3eb162..c122527）。
> 复审性质：只读验收，未改任何代码。证据分四级：文档对照 → 代码证据（grep/通读）→ 行为验证（本地实跑）→ 外部独立验证（GitHub API 查 workflow 结论）。

---

## 0. 总体结论

**验收通过，R1–R3 共 16 张卡全部达成 DoD 或以合理纠偏达成等价效果。**

- 本地全量回归（2026-07-04 复审实跑）：**1046 passed / 9 skipped / 0 failed，exit 0，3m50s**——且**未做任何排除**，比 R3.6 落地记录当时"排除 4 处环境依赖测试后 897 passed"的状态更干净。
- smoke_edge 13/13；`eval_fast_intent` 39/39、`eval_route_hints` 8/8 对基线无回归（本地实跑）。
- GitHub Actions（经公开 API 独立查证，非采信文档）：CI 在 main 最近 5 次全部 success（含当前 HEAD `c122527`）；nightly-e2e 3 次 success，**其中 2026-07-03T19:26 一次是 schedule 自动触发**（证明定时门禁真实生效，不只是手动 dispatch）；文档声明的 run ID `28643924654`/`28639607108` 与 API 返回吻合。
- 执行过程中对原任务卡的 4 处**纠偏全部复核成立**（见 §2 各卡），且都如实记录了推翻原设计的理由——这是本次执行质量最值得肯定的部分：没有为了"完成卡片"而硬凑验收。
- 复审新发现 2 项轻微残留 + 汇总 6 项执行者已自记的已知边界（见 §3），**均不阻塞 R4**。

---

## 1. 复审方法

1. 通读活文档「执行进度」节与 10 份 `docs/design/2026-07-0*` 落地记录，提取每卡声明的 DoD 与证据。
2. 对每张卡做代码证据核对（存在性 + 关键语义抽查，重点通读了 `route_hints.py`、`auth.go`、`cloud_client.py`、`shared_state.py` 等新核心件全文）。
3. 行为验证：全量 pytest / smoke / 两个 eval 脚本本地实跑。
4. 外部验证：GitHub API 拉取两条 workflow 的 runs 结论，与文档声明比对。

---

## 2. 分卡复审结果

### R1 工程门禁与卫生 — ✅ 5/5 通过

| 卡 | DoD 核对 | 证据 |
|---|---|---|
| T1.1 CI 补全 | ✅ | `ci.yml`：聚合安装各 `requirements.txt`（D12 修复）、pytest 分组跑全部测试目录、`go-build-test` job（含 `go mod tidy` 远端修复 `bea1795`）、`frontend` job（npm ci/test/build）；GitHub main 最近 5 次 success |
| T1.2 死代码清理 | ✅ | `gateway/edge/main.go` 中 `ChannelClient` 0 引用；根 `providers/`、`debug-local.py`、`start-local.*` 均已删除 |
| T1.3 compose 生存性 | ✅ | `x-restart: &restart` 锚点，`docker compose config` 渲染后 **26 个服务带 restart: unless-stopped**；redis/postgres 补 healthcheck |
| T1.4 文档同步 | ✅ | conventions.md：trip-planner 行含 navigate/status/reschedule + 三意图详表；ticketing 改「50074 起，50073 已由 deep-research 实占」 |
| T1.5 media 判定统一 | ✅ | `edge_call.py::action_type_for`（`_MEDIA_OBJECTS` 单一清单）；server.py **4 处**调用点（302/361/457/543）全部收敛，含原先最窄的 CLOUD-DEGRADED 路径 |

### R2 架构还债 — ✅ 5/5 通过（质量高）

**T2.1 路由兜底机制化（最高优先架构债）— ✅ 通过**
- 编排核心 4 处领域硬编码**全部清除**，逐一核实：
  - `planning.py`：`_TRIP_*`/`_RESEARCH_*` 正则与 6 个 `_ensure_*` 全部消失；剩余 `trip-planner`/`charging` 字面量**仅存在于 `_PLANNER_SYSTEM` few-shot 示例**（执行者已声明为 D10 Prompt 债残留，判定合理——few-shot 示例不随 Agent 数量增长，性质是 prompt 资产而非路由债）；
  - `context.py`：`_ALWAYS_INCLUDE` → env `PLANNER_FALLBACK_AGENT` + 通用「声明了 route_hints 的 Agent 保留 catalog」判据；
  - `aggregator.py`：`_card_priority` → 读卡片自带 `display_priority`；
  - `progress.py`：`HEAVY_INTENTS` → `Step.heavy`（源自 manifest `capability.heavy`）。
- `RouteHintEngine`（147 行）通读：replace/append 策略、priority 降序稳定排序、guard 反例守卫、`$text`/`$N` slot 模板——**与原 `_ensure_*` 语义等价**（互斥 replace 命中即停 = 原 research>navigate>reschedule>status>modify 顺序；append 不终止 = 原 trip.plan 并列补步）；兜底步复用 `_validated_steps` 走与正常步同一装配路径（endpoint/权限/intent∈能力集校验），这个设计选择正确。
- proto：`AgentManifest.route_hints`(field 14) + `Capability.heavy`(field 6)，注释完整；走了先改 proto 再 codegen 流程。
- **DoD#2 铁律契约测试在位**（`test_planning.py:415`：全新假 Agent 仅靠 manifest route_hints 获得确定性路由）——这是防复发的关键固化。
- 真栈修复 `737ddef`（PgStore round-trip 丢 route_hints/heavy）经核实已在 `registry/store.py:382-414` 落地——**这类"真栈才暴露"的修复被补进来，说明验收不是纸面的**。

**T2.2 权限单轨化 — ✅ 通过，纠偏成立**
- `security/permission.py::check_permission` 为唯一决策点；`planning.py:385`（规划期 catalog 过滤）与 `dispatch.py:108`（执行期硬拒）同源复用；`engine._enforce_permissions` 空壳已删（engine.py:173 注释交代去向）。
- `PERMISSIONS_FAIL_OPEN` env 门控（默认 `true` 保持现状）+ `audit.py` 结构化事件。
- **纠偏复核**：原卡建议直接接线 `effective_scopes`（trust-cap ∩ granted）。执行者指出扁平交集不做父子 scope 覆盖会误拒 scene-orchestrator（需要 `vehicle.control` 父 scope 覆盖 `vehicle.control.hvac` 子 scope 的语义）——核对 `scopes.py::is_scope_covered` 的层次覆盖语义后**确认该纠偏正确**，"零行为变化单轨、trust-cap 推后"是对的取舍。

**T2.3 端云持久长连 — ✅ 通过**
- `cloud_client.py` 通读：进程内单条持久 bidi、`_pending: corr_id→Queue` 多路复用、`_reader` 单读循环（含就地服务云→端 edge_call）、15s 心跳、指数退避+抖动重连、重连重建 channel 走 `dns:///` 重解析、断连时在途请求快速失败；`handle()` 对外契约不变。R3.1 层 2 token 后来无缝挂进 Hello——分层正确的旁证。
- Go 死代码 ChannelClient 已删（与 T1.2 合并完成）。

**T2.4 info agent 拆域 — ✅ 通过**
- `agent.py` 1269 → **123 行**；`handlers/{weather 200, search 93, sports 337, news 433, stock 62, briefing 87}` + `_util 40`。mixin+MRO 方案保住"逻辑逐字不变"与测试零改动（文件尾重导出兼容旧 import 路径），验收口径（`pytest agents/info` 136 passed + 全量零回归）成立。

**T2.5 跨 Agent 状态键契约化 — ✅ 通过**
- `agents/_sdk/shared_state.py` 权威登记表（owner/reader/schema/TTL 齐全）+ `Context.save/load_shared_state` 封装读写前缀不对称；info/deep-research/trip-planner 三处全部改用常量（grep 复核：业务码中裸字面量仅剩注释/日志文案）；conventions.md §9 同步。

### R3 量产硬化 — ✅ 6/6 通过

**T3.1 会话鉴权 — ✅ 通过**
- `auth.go`（105 行）通读：token 表解析用 `SplitN(entry,":",4)` 正确保留 scope-csv 内部逗号；**401 在 WS Upgrade 之前**（连接不建立）；`stampScopes` 先删客户端可能伪造的 `granted_scopes` 再按 token 注入——网关唯一权威的语义实现正确；`AUTH_REQUIRED=false` 匿名回退逐字保持现状。
- `user_id="u1"` 硬编码已死，只剩 `AUTH_DEFAULT_USER_ID` env 默认值（语义正确：默认值≠硬编码）。
- 层 2：cloud-gateway 校 `CLOUD_CHANNEL_TOKENS`、edge `cloud_client` Hello 带 token，均核实在位。
- 已知边界（执行者自记，复审认同）：静态 token 无恒时比较、无轮换——属"真实 IdP"后续项，对 env 门控的 PoC 层可接受。

**T3.2 服务间 mTLS — ✅ 通过**
- `runtime/grpcio.py`：`GRPC_TLS` 门控默认关、共享 mesh 证书 + `ssl_target_name_override=cockpit-mesh`、新增 `bind_port`；`gateway/tlscfg` Go 侧对应；`scripts/gen-certs.{ps1,sh}` 在位。
- **安全红线核验：`git ls-files certs/` 仅 `.gitkeep` 入库**，本地已生成的 ca.key/server.crt 均未被跟踪，.gitignore 五种后缀全覆盖。✅

**T3.3 e2e 入 CI 门禁 — ✅ 通过，纠偏成立**
- `nightly-e2e.yml`（schedule + workflow_dispatch，零 secrets 纯 mock）+ `scripts/run_e2e.{sh,ps1}`；`make e2e` 从"收集不到任何 e2e 脚本的假目标"改为显式清单执行器（G2 的"假 e2e"修复）。
- **纠偏复核**：原卡写"跑 5 个断言型脚本"，执行者裁剪 case 的理由（mock 非 JSON 输出下无 route_hints 的 Agent 必然落 chitchat 兜底、memory 真 embedding 链路 mock 下逻辑必败）与 R2.1 后的路由机制现实一致，且**多纳入**了 trip/research/research_async 三个因 route_hints 而 mock 可靠的脚本——净覆盖比原卡更宽。裁剪判断成立。
- **外部独立验证**：schedule 触发的 run（2026-07-03T19:26）conclusion=success——定时门禁真实在跑。

**T3.4 意图路由评测基线 — ✅ 通过（注意口径）**
- `eval_fast_intent.py`/`eval_route_hints.py` + `docs/reviews/eval/` 基线（JSON+MD）+ CI 非阻塞 `intent-eval-baseline` job（`::warning::`），复审本地实跑 39/39、8/8 无回归。
- **纠偏复核**：飞书 1465 语料确已不可得（原始表 gitignore 且一次性消费），改用 39+8 条策展语料成立；验收演练（改坏正则触发告警后撤销）有记录。
- **口径提醒**（复审强调，防误读）：39/39=**策展回归集全过**，不是路由准确率——全量 1465 意图语料的真实覆盖率是 **72%**（`docs/design/2026-07-03-intent-coverage-gap-analysis.md` 已单独量化）。两个数字不可混用于汇报。

**T3.5 降级矩阵自动化 — ✅ 通过，两处纠偏均成立**
- `e2e_degrade.py` 四行齐（Agent 故障/LLM 超时/Planner 故障/断网），`LLM_MOCK_DELAY_MS` 零行为测试钩子核实（默认 "0"）。
- 纠偏①（Row 4 断言从"固定超时话术"改"变慢仍优雅响应"）：复核 engine.py D0 路径确认 chitchat 单步流式直通**确实绕开** executor 的 `step_timeout` 包装，原断言路径不存在——纠偏成立且诚实。
- 纠偏②同时是**新发现的真实缺口**：cloud-gateway pause/unpause（同 IP 冻结）后 edge-orchestrator 持久通道不自愈，e2e 里以显式 restart 兜底并在代码注释（e2e_degrade.py:308-314）如实记录。**这是 R2.3 持久通道的一个未覆盖恢复场景，列入 R4 前收尾**（见 §4）。

**T3.6 Prometheus/OTel 导出 — ✅ 通过（带一项未验证边界）**
- collector `/metrics`（`metrics_export.py` 手写文本格式，零新依赖）+ `otel_bridge.py`（复活 `setup_tracing()` 死代码桥接真实 span）+ compose `profiles: ["observability"]` 门控 prometheus/grafana + provisioning/dashboard JSON，均核实在位；真栈数据链路（真实 Agent 调用→/metrics 输出）有验证记录。
- 边界如实：**Grafana 可视化面板未验证**（本机网络对大镜像层下载系统性不稳，已交叉验证为环境问题）——留待环境恢复补验，不计失败。

---

## 3. 残留与已知边界（接手者备查）

### 3a. 复审新发现（轻微，均不阻塞）

| # | 发现 | 位置 | 建议 |
|---|---|---|---|
| N1 | **PermissionEngine 死注入**：R2.2 单轨化后，`main.py:49` 仍构造 `PermissionEngine()` 注入 `PlannerEngine(perms=...)`，`engine.py:51` 存 `self.perms` 但全文无任何使用——权限双轨清了，注入残骸没清 | orchestrator/cloud/{main,engine}.py | 下次动编排时顺手删参数（或留作 trust-cap 接线点，但应加注释声明意图）；类本身被 test_ws8_security 使用，保留 |
| N2 | `_fallback` 里 `"chitchat.talk"` 字符串作 capabilities 为空时的兜底 intent 默认值 | planning.py:324 | 可忽略（防御分支，实际 chitchat manifest 恒有 capability） |

### 3b. 执行者已自记的边界（汇总，均有出处）

| # | 边界 | 出处 | 性质 |
|---|---|---|---|
| K1 | **cloud-gateway pause/unpause 后 edge-orchestrator 持久通道不自愈**（换 IP 场景会自愈、同 IP 冻结不会），当前以 restart 兜底 | e2e_degrade.py / R3.5 落地记录 | 真实可靠性缺口，**建议 R4 前修** |
| K2 | `e2e_process_region.py` 既有失败（默认泊车态断言），与 R3.3 无关 | R3.3 落地记录 | 待修的既有测试问题 |
| K3 | Grafana 面板未验证（环境网络） | R3.6 落地记录 | 环境恢复后补验 |
| K4 | trust-cap（trust_level 上限强制）推迟 | R2.2 落地记录 | 等 scope 层次化/IdP 后接线；现 third_party 车控硬禁令仍在 |
| K5 | 静态 token 非量产 IdP；`PERMISSIONS_FAIL_OPEN`/`AUTH_REQUIRED`/`GRPC_TLS` 默认均为开发友好态，量产需翻转三开关 | R3.1/R3.2 落地记录 | 部署清单项，非代码缺口 |
| K6 | 端侧意图真实覆盖 72%（1465 语料），扩规则 vs 上 NLU 模型未定 | 2026-07-03-intent-coverage-gap-analysis.md | R4 路由质量主题的输入 |

### 3c. 顺带观察（非问题）

- 全量测试 1046/9/0 且不再需要 R3.6 时的 4 文件排除——本机今日全绿，此前"环境依赖失败"或已被 SKIP guard 收编或环境差异消失，无论哪种，当前门禁口径干净。
- 每卡一份落地记录 + 活文档进度节 + AGENTS.md/README 同步的纪律执行得很好，10 份 `2026-07-0*` 设计文档齐全，接手成本低。

---

## 4. R4 进入判断与优先级建议

**判断：可以进入 R4。** R1–R3 无阻塞性残留；§3 各项均为小尾或已声明边界。唯一建议的次序调整：先花 ≤1 天清一个「R4.0 收尾包」，再进能力演进——三件小事都趁热：

**R4.0 收尾包（S，先行）**
1. 修 K1：pause/unpause 同 IP 冻结场景的通道自愈（`cloud_client._reader` 对心跳超时/长时间无 Pong 的主动断流重建——现在只对连接错误重建）；修好后把 e2e_degrade Row 4 的 restart 兜底改回自愈断言。
2. 修 K2：`e2e_process_region.py` 既有断言失败。
3. 清 N1：PermissionEngine 死注入。

**R4 主线（按 ROI 排序）**

| 序 | 主题 | 内容与理由 | 规模 |
|---|---|---|---|
| 1 | **路由质量主题 = T4.1 向量路由 + K6 意图覆盖缺口 +（可选）D7 fast_intent 规则数据化** | 三件事同域合并做：① Registry `_score` 字符命中 → embedding 向量检索（llm-gateway→百炼已就绪，A6/D11 收账）；② 端侧覆盖 72% 按 gap analysis 决策扩规则 or 轻量 NLU；③ 若走扩规则，顺势把 fast_intent 规则外置 YAML（D7，量产 OTA 叙事）。**为什么第一**：产品本质是"听得懂"，覆盖率提升惠及所有能力；且 T3.4 评测基线刚建好，正是动路由的最佳窗口（先有尺子再动刀） | L |
| 2 | **T4.2 服务端流式 TTS + barge-in** | 语音座舱体验的分水岭（现为句级批量 TTS）；打断是车内真实刚需 | L |
| 3 | **T4.4a manual-rag 真实化** | pgvector/embedding 全就绪，车书问答是座舱高频展示场景；food/parking 无真实商户资源，维持沙箱不投入 | M |
| 4 | **T4.6 注入防护升级** | T3.4 评测框架就绪后建对抗集，正则黑名单 → 结构化隔离全链路强制；安全叙事收尾 | M |
| 5 | T4.3 端侧 SLM 离线兜底 | 重且依赖硬件叙事，仅当需要"断网智能"演示时提前 | L |
| — | T4.5 HMI P5 行车态 / P6 Dashboard | 外部阻塞（Figma 帧未出），有帧随时插队 | M |

---

*复审人：Claude（Fable 5），2026-07-04。本报告只读复审产出，未改动任何生产代码。*
