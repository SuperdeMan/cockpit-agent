# 测试与验证

## 1. 端侧纯逻辑 smoke（无需 docker）
```bash
python test/smoke_edge.py
```
验证 Fast Intent 分类、模拟 VAL 安全门控、端侧执行链。**当前结果：13 passed, 0 failed。**

## 2. 全量测试（一条命令，无需 docker）
```bash
python -m pytest --import-mode=importlib -q
```
`conftest.py` 已配好 PYTHONPATH，`--import-mode=importlib` 解决 test_agent.py 重名。
**当前结果不在本文件维护**——数字变得比这份说明快，抄一份必然陈旧（这一行以前就写着
一个落后两批的 4601）。唯一真相源是 [`AGENTS.md` §4.0](../AGENTS.md) 的「最新后端全量基线」，
那里同时给出**较上一个 SHA 的净增量与逐条点号**（§4.3：净增量要跟同一个 SHA 比，
不能跟文档里那个数比）。skip 含 nightly 真实 LLM 默认跳过。
注意 CI 按分组进程隔离跑（见 `.github/workflows/ci.yml` run_group），本地单命令与 CI
口径一致；前端数字同样见 §4.0。

### 测试分布
| 模块 | 文件 | 覆盖 |
|---|---|---|
| 车控知识库 | `orchestrator/edge/tests/test_val_knowledge.py` | YAML 加载、实体归一化、命令校验、安全门控、响应选择 |
| Fast Intent 扩展 | `orchestrator/edge/tests/test_fast_intent_extended.py` | pattern、结构化输出、旧格式兼容 |
| 多意图切分/路由 | `orchestrator/edge/tests/test_multi_intent_split.py`、`test_server_dispatch.py` | 本地并行、语义分组、云回退、危险动作确认 |
| 端侧编排 | `orchestrator/edge/tests/` | 混合意图、VAL、状态 diff、trace、debug 环境量白名单、本地轮记忆 best-effort 写入 |
| 数据驱动语料 | `orchestrator/edge/tests/corpus/` + `test_corpus_*.py` | 安全门控逐对象、车控对象矩阵、多意图拆分边界（88 条参数化，秒级）|
| 云端中枢 | `orchestrator/cloud/tests/` | DAG、T2 循环、统一调度、edge call、工具、权限、上下文 |
| 慢意图完整性回归 | `orchestrator/cloud/tests/test_regression_intent_integrity.py` | 当前话术透传、非法计划原子拒绝、默认 scope |
| 复杂混合意图回归 | `orchestrator/edge/tests/test_regression_complex_intent.py` | 中文温度、出发指令归组、本地/云端职责边界 |
| Registry | `registry/tests/` | 注册/路由、主动健康探测、摘除与恢复、健康事件 |
| 可观测 | `observability/tests/`、`observability/collector/tests/` | emitter 断线恢复、collector 聚合与重启快照自愈恢复、REST/WS、debug 校验 |
| ASR 转码 | `llm-gateway/tests/test_transcode.py` | wav 透传、webm 转码、回退 |
| Agent | `agents/*/tests/` | 各 Agent 契约测试 |
| 分层记忆（单点） | `memory/tests/test_pg_store.py`、`test_store.py`、`test_extract.py`、`test_server_rpc.py`、`test_routine.py` | 写读/过滤/时序-lite、画像与 places 收敛、四分类抽取治理+PII黑名单、RPC 映射、routine 聚合 |
| 分层记忆（复杂场景） | `memory/tests/test_scenarios.py` (8) | 多轮偏好演化、多乘员隔离、隐私三档、临时偏好过期、routine 阈值、抽取纵深防御、合规导出/被遗忘权、planner 召回契约 |
| Agent SDK | `test/sdk/` | 跨 Agent 协作、周期重注册（registry 重启后自愈补注册）|
| ASR E2E | `test/test_asr_e2e.py` (4) | wav/webm/空音频/voices（需 API key，无 key 跳过） |

## 3. HMI 单测与构建

```bash
cd hmi
npm test
npm run build
```

`npm test` 覆盖语音回路、流式 TTS、卡片交互、设置与协议护栏。**当前结果：225/225；
Vite 生产构建通过（2026-08-09 实测）。**

## 4. Dashboard 单测与构建

```bash
cd dashboard
npm test
npm run build
```

覆盖 trace 聚合、状态 diff、Agent 健康与环境量 debug 交互。**当前结果：17/17；
Vite 生产构建通过（2026-08-09 实测）。**

## 5. 端到端测试（需 docker compose 起全栈）
```bash
pip install websockets
python test/e2e_ws.py                     # 4 条标准链路（车控/导航/闲聊/确认）
python test/e2e_observability.py          # 人工巡检：中枢分发→执行→仪表盘（collector 三维观测）
python test/e2e_central_hub_assertions.py # 断言型：P0-1~5 中枢链路/状态/确认 + trace 全链贯穿(P1-8)自动断言
python test/e2e_memory.py                 # 断言型：记忆 6 链路（真 embedding 语义/planner 召回注入/chitchat 宠物/隐私定向/合规/主动 routine→NATS），自清理可重入
python test/e2e_context.py                # 断言型：上下文 6 链路（注入拦截/裸确认兜底/危险确认闭环+catalog保edge车控/续航查询不跌闲聊/trip.plan兜底/trip.modify兜底）
python test/e2e_process_region.py         # 复杂任务过程区四阶段+脱敏、普通任务零过程；浏览器式连接（验证 WS 长任务保活）
python test/e2e_resilience.py             # 断言型：依赖服务 --force-recreate 换 IP 后系统自愈（不重启依赖方），验证全链路 gRPC keepalive
python test/e2e_trip.py                    # 断言型：行程规划 6 轮（结构化卡+真实 POI 接地+跨轮持久化+确认收尾+改某天不漂移+下一站导航+在途状态/精简）
python test/e2e_research.py                # 断言型：深度调研（research_report 分节报告+真实来源、多轮「展开第N点」聚焦深挖、普通搜索不被劫持、新闻「详细讲讲第N条」深挖桥接）
python test/e2e_research_async.py          # 断言型：异步分钟级深调研（明示「不急/查完告诉我」→秒级受理 ack→后台 deep 流水线越过 90s 上限→NATS agent.proactive 主动推送带 card 报告卡，真栈 9 节/36 源/~3031 字）
python test/e2e_rejection.py               # 断言型：R4.4 拒识主链（hands-free 语音源乘客对话→rejected 卡+空 speech 不落库；正常受话照常应答）——需真 provider，mock 模式自动 SKIP
python test/e2e_ledger.py                  # 断言型：M2 Task Ledger 五场景（受理开单→进度查询「话术与账本逐字一致」→幂等去重不双跑→cancel 后台停手→**重启容器后 orphaned 诚实报告**）
python test/e2e_verify.py                  # 断言型：M2 Outcome Verifier 声明式执行后对账（state_match 对 NATS 车况镜像 / schema 拿到真东西；三态 UNKNOWN 不定罪）
python test/e2e_memory_graph.py            # 断言型：M2 记忆图谱五场景（偏好加权/关系边入图/人称地点解析/查不到诚实追问/**GDPR 级联删除红线**）
python test/e2e_proactive.py               # 断言型：M3 统一主动引擎（单条直通字节级兼容/**同窗两条合并成一条**/卡片 card_group/跨生产方去重/情境断言投递期复核/user_contract 豁免 vs advisory 延后/全局频控）——开跑前重启 proactive 拿净初态（治理器刻意无持久化）
python test/e2e_geofence.py                # 断言型：M3 位置提醒（地点经 nearby 真解析出坐标→围栏外只播种→进围栏触达一次→不重复）；地点解析走**邻近搜索**，故先压一次市区位置作前提
python test/e2e_mcp.py                     # 断言型：M3 受控 MCP 桥（准入边界=清单外工具不进注册中心/只读真数据+演示商户三重标注/下单先确认/幂等不双扣）——开跑前重启桥拿演示商户净初态
python test/e2e_merchant_mcp.py --live-readonly  # 人工 opt-in：复核麦当劳/瑞幸 initialize + tools/list 与只读预览，绝不进自动车道
python test/e2e_merchant_mcp.py --live-create-unpaid --merchant mcdonalds --acknowledge-real-orders --max-real-orders 1  # 人工写车道：必须显式选择 mcdonalds|luckin；只创建未支付订单，单次硬上限 1~3，需预先给出清理方案且不得最终付款
python test/e2e_payment.py                 # 断言型：支付真栈闭环（§9.17 批 2）：「交停车费」→确认话术=网关快照金额→payment_qr 卡（mockpay 码+SVG+mock 角标）→mock 渠道自动支付后再缴费答「已经支付过」（worker 推进 captured 与幂等防双付的黑盒合证）——milestone 车道（落域依赖真 LLM）
python test/e2e_obs.py                     # 断言型：badcase 排查观测链路 16 断言（obs.turn 落库/plan 门控采集/obs.llm/日志按 trace 关联/badcase 标记检索导出/重启 collector 持久化）
python test/e2e_voice_loop.py              # 断言型：语音回路后端契约（/api/asr/stream PCM 直传 partial→final→done + vad_silence_ms 透传 + TTS round-trip）——浏览器声学层 CI 测不了，留真麦
python test/e2e_tts_stream.py              # 断言型：R4.2 服务端流式 TTS（cosyvoice 首帧延迟 G1 门槛 + cancel 收尾）——需 DashScope key
python test/e2e_s2s.py                     # 断言型：M4 S2S 全双工最小闭环（自答闭环/多轮上下文/escalate 逃逸零音频/①听感打断零残包/③工具调用中打断不播报/unsupported 回落/回灌 memory+obs 且逃逸轮不重复写）——需 DashScope key（2026-07-25）
python test/e2e_voiceprint.py              # 断言型：M4 P4 声纹多用户（**M4 最后一条 DoD「多用户记忆隔离旅程」**）——首个注册者绑 primary 且存量记忆一条不少/识别/太短与静音诚实降级/B 的偏好主驾查不到/「你知道我是谁」叫得出名字/换乘员后危险动作照样确认/删除即忘掉这个人。声纹模型缺失自动 SKIP。**⚠️ 它跑的是真实 `uid=u1` 且开头会清空该用户的全部声纹模板**（净初态），有真人已录入的机器上别随手跑——会把真人的三段样本换成 TTS 音色（2026-07-27 补注）
python test/e2e_vision.py                  # 断言型：M4 P4 视觉入口——纯色图进真模型出真颜色（正确答案唯一可断言，比拿风景照让它描述强）/没帧与帧过期一律诚实降级不编造/响应体零图像字节。需 DashScope key（2026-07-26）
python test/e2e_s2s_resilience.py          # 断言型：M4 S2S 韧性（宿主内 WS 代理注入断连→重连+摘要重注入/IN_TURN 断连诚实收束/持续不可达→DEGRADED 回落）——需 DashScope key；不改 .env 不给生产协议加后门（2026-07-25）
python test/e2e_degrade.py                 # 断言型：架构 §3.3 降级矩阵四行（单 Agent 故障/LLM 超时/云 Planner 故障/断网）——docker 级故障注入 + 严格 try/finally 恢复，务必放在其它 e2e 脚本之后跑
python test/e2e_auth.py                    # 断言型：会话鉴权（需 AUTH_REQUIRED=true + token，非默认栈配置）
python test/e2e_mtls.py                    # 断言型：服务间 mTLS（需 GRPC_TLS=on + scripts/gen-certs.*，非默认栈配置）
python test/e2e_journeys.py                # 旅程级（L3）：跨 Agent 自主执行 × 全场景连续对话（见下节）；--provider <pid> 锁定 active LLM（评测中途漂移=报告作废、退出码 1）
python -m pytest test/e2e_real_providers.py -q -s   # 无需 docker：真实三方 provider 冒烟（按 key 自动 skip）
python test/e2e_strict_stack.py            # 断言型：数据真实性——严格栈冒烟 + mock 泄漏探针（三问外源卡 _prov 全 real；active=mock 自动 SKIP，属 live 车道）（2026-07-17）
python test/e2e_planner_toolcall.py        # 协议探针：各 provider tool-calling 真实行为矩阵（M1a submit_plan；named tool_choice/arguments 合法性/finish_reason，--providers 指定逐家 pin）（2026-07-24）
python test/e2e_voiceprint_probe.py        # 模型探针：M4 P4 声纹可分性——同人/异人余弦分布、混淆对、端到端识别率与**阈值扫描**、最短有效语音、分布外音频。**产出是三个阈值的实测值**（改阈值前必重跑）；需宿主 pip install sherpa-onnx（2026-07-26）
python test/e2e_s2s_probe.py               # 协议探针：M4 S2S provider（omni realtime）行为矩阵——tools 支持度/escalate 分流/cancel 残包/回注不双播/多轮上下文/首音频时延；--case 单跑、--model 换厂商验证「锁协议不锁厂商」（2026-07-25）
```

### 5.1 旅程级测试（L3）与 HMI CDP 层（L4）

设计与语料口径：`docs/design/2026-07-14-journey-e2e-test-system.md`。

```bash
python test/e2e_journeys.py                       # 全部旅程（live 栈，真 key）
python test/e2e_journeys.py --level regression    # 仅回归级（必须 100% 绿，红=回归）
python test/e2e_journeys.py --level target        # 仅目标级（能力标尺，允许红——红灯=工程 backlog）
python test/e2e_journeys.py --lane mock           # ⚠ 2026-08-01 起 mock 车道为空，直接失败（见下）
python test/e2e_journeys.py --id A4-2,B4-2        # 指定旅程
python test/e2e_journeys.py --list                # 列语料不执行
node test/hmi_cdp/run_cases.mjs                   # L4：HMI 二次交互 CDP 用例（渲染/点击→WS 帧断言）
```

- 语料在 `test/journeys/*.yaml`（regression=保护存量 / target=定义目标能力）；新增旅程改语料
  不改 runner，schema 严格校验（拼错断言键直接拒跑）。
- ⚠ **`lane: mock` 当前为空，`e2e_journeys` 已退出 nightly（2026-08-01）**。原有两条
  （A4-2/B4-2）的「mock-safe」判据写的是「**route_hints 确定性路由**」——把「有规则撑着」
  当成了「不依赖模型」，而那些 hint 已按跨 provider 数据退役（M5 P2），nightly 随即连红三次。
  **新判据：mock-safe ⟺ 这条路径不经过模型判断**（端侧快路径 / 兜底 Agent / 确定性解析与
  流程态短路 / 协议传输层四类），**「它有 hint 撑着」不算**。要重新填充 mock 车道请按此判据。
  `--lane mock` 选不出旅程会**直接失败**——空选集不算绿。
- 报告落 `docs/reviews/eval/journeys_report.{json,md}`（含 active LLM 声明与时延基线——
  **跨 provider 结果不可直接对比**）；失败轮自动标 collector badcase，dashboard 收藏夹可重放下钻。
- 运行纪律：全栈起后 settle ≥40s；**禁与 docker build 并发**；外部数据源断供（api-football
  超时等）按语料内 `skip_journey_if_speech_any` 约定判 SKIP 不判 FAIL。
- L4 前置：宿主装有 Edge/Chrome（`CDP_BROWSER` 可指定路径）、宿主 5173 未被本地 vite 占用；
  截图证据落 `test/hmi_cdp/shots/`（gitignore）。
- **⚠️ CDP 假麦克风只能验接线，验不了音质**（2026-07-27 实测）：
  `--use-file-for-fake-audio-capture` 喂 WAV 时**装置本身不保真**——同一段 TTS 文件直取的
  三段自洽度 0.83/0.73/0.81，经假麦采进浏览器后 ASR 只能转出零星几个字、与源文件的声纹余弦
  仅 **-0.03**（换 48kHz / 降 12dB / 关 EC-NS-AGC 都不救）。故它可用于「请求有没有按预期
  发出去、格式对不对」这类接线断言，**识别率/音质类结论一律以真麦为准**。
  **测试装置也要先被验证**，否则它会以产品缺陷的面目出现（差点据此继续改产品）。

### 5.2 灰度门槛评测（M4 S2S）

```bash
python test/eval_s2s_escalation.py            # 移交判定准确率（文本路径，~15s/24 条）
python test/eval_s2s_escalation.py --audio    # 真实音频路径（含转写误差，与线上一致，慢 ~6×）
python test/eval_s2s_escalation.py --desc "…" # §6.2 灰度调参：换 escalate 描述做对照
```

- 语料 `test/eval_corpus/s2s_escalation_cases.yaml`；配置**生产同源**（直接用
  `s2s.protocol.escalate_tool()` 与 `persona()`，不写评测专用 prompt）。
- **漏移交率 ≥95% 是灰度扩域的硬门槛**（RFC §9 P3）；误移交只入基线不设指标——它的代价是
  多一次主链往返，远轻于「口头答应没办事」。
- 对抗重点是**夹在闲聊里的动作句**（「今天真热啊，把空调开低一点」），直白句谁都判得对。
- 基线（2026-07-25，`qwen3.5-omni-flash-realtime`）：两条路径均 **24/24=100%**，
  文本 613ms/轮、音频 2160ms/轮。
- **provider 错误不算模型判断**：命中 provider 侧错误的条目重试一次，仍失败则单列并**排除出
  分母**（把调用失败记作「模型选择自答」会让指标朝好看的方向失真）；错误率 >10% 直接判
  「报告作废」，退出码 2。

E2E 清单的唯一真相源是 `test/e2e_manifest.yaml`，统一入口是 `scripts/run_e2e.py`；PowerShell、
shell 与 `make e2e` 都只是参数透传，不再各自维护脚本数组。常用门禁：

> 远程策略字段固定为 `remote_safe` / `remote_mutating`。E2E 运行前由统一入口按仓库
> 根目录读取 `dev-stack.local`。
> **2026-08-18 起 cloud 车道已在真实云主机跑通**：`--target cloud` 不带 `--id` 时选中
> `remote_safe and profile == "root"` 的 **2 条**（`e2e_protocol_smoke`、
> `e2e_remote_safe`），实测 **2/2 PASS**。⚠ manifest 里 `remote_safe: true` 是 **3 条**，
> `e2e_tts_stream` 因 profile 不是 `root` **不进 cloud 缺省面**——
> **「标了 remote_safe」不等于「cloud 会跑它」**，读覆盖面要看 dry-run 的 `selection`，
> 不要数 manifest。`remote_mutating: true` 当前 **0 条**。

manifest 的三种状态不允许默认：`true/false` 表示 cloud 缺省可读；`false/true`
只允许精确 `--id` + `--allow-mutating`；`false/false` 在 cloud 永久拒绝，直到完成远程
端点化和独立 review。cloud 禁止本地 Compose、非 root profile、signed identity 和 fixture
pre-step。每轮使用独立 run/user/session，结果记录 target/release/case 策略与 `e2e`
锁摘要；不记 token、私钥或完整远程日志。

```powershell
python scripts/run_e2e.py --target cloud --dry-run
python scripts/run_e2e.py --target cloud --id e2e_remote_safe

# 仅当 manifest 已标 remote_mutating:true 且取得本轮精确人工授权
python scripts/run_e2e.py --target cloud --id e2e_reviewed_mutation --allow-mutating
```

`--allow-mutating` 不覆盖支付、商户写、真实车控、数据删除或系统配置的人工红线授权。

```bash
python scripts/run_e2e.py --check --milestone M-A --lane milestone --stale-policy warn
python scripts/run_e2e.py --lane ci --full --stale-policy warn
python scripts/run_e2e.py --lane nightly --full --stale-policy warn
python scripts/run_e2e.py --milestone M-A --lane milestone --full --stale-policy warn
```

CI 另有**四条确定性 blocking 门禁**（零 LLM、零网络，与上面的 e2e 车道不同准入）：

```bash
python test/eval_skills.py                   # skill 契约
python test/eval_exemplars.py                # 范例契约 + 域路由探针
python scripts/check_intent_gate.py          # 意图对抗 L0（strict）——唯一入口，别接管道读退出码
python test/eval_capability_integrity.py     # 端侧车控能力完整性（六维逐对象，B4）
```

每个 child 必须原子写入结构化结果；退出码只接受 `0`（已执行）或 `77`（整项跳过），runner 不解析
stdout 里的“PASS/SKIP”。milestone 与 canonical 禁止 `SKIP`/`PASS_WITH_SKIPS`，因此缺凭证、
缺硬件或 provider 不可用不会被包装成绿灯。`--id` 是局部诊断，不具备 full/canonical 资格。

canonical 仅在代码输入已提交、provider/model 由运行中控制面锁定、完整 milestone 通过时更新
`docs/reviews/eval/journeys_report.{json,md}`；dirty、筛选运行、运行时档位漂移或 stale digest
都会拒绝覆盖基线。真栈必须从根 `compose.yaml` 启动；在 worktree 验收时，runner 只从
`E2E_STACK_ROOT` 指向的根 `.env` 补齐子进程运行变量（显式进程变量优先），新增敏感值立即进入
脱敏集合，既不打印也不写工件。runner 不修改 `.env`，子脚本也不得自行读取第二份环境文件。

## 5.3 离线评测与分布尺（`docs/reviews/eval/README.md` 是口径唯一真相源）

```bash
# 回归闸（零网络，CI 阻断；「别倒退」）
python test/eval_fast_intent.py        # 端侧规则
python test/eval_route_hints.py        # 云侧确定性路由
python test/eval_mode_routing.py       # 四模式端到端口径 + 确定性子集
python test/eval_registry_resolve.py   # registry top-1
python test/eval_skills.py             # 知识层契约 + 检索 golden
python test/eval_exemplars.py          # 范例层契约 + 域路由探针（M5 P1）

# 取证脚本（零 LLM、零网络，**不是**准入闸——不进 CI blocking）
python test/eval_actionability.py      # B6 可执行性形态判定：CLARIFY 召回 vs 假阳性两侧
                                       # ⚠ 假阳性的分母是「除澄清金标外的全部轮」——
                                       # 分母挑得越干净它越好看，改口径前先读脚本里那段理由

# 分布尺 N1（真栈，回答「这个月变聪明了吗」，**不是**回归闸）
python test/routing_bench.py                        # 零成本：语料覆盖 + 隐藏分母 + 域偏斜
python test/routing_bench.py --live --write-baseline # 出 domain_hit_rate 与分域混淆矩阵

# 规则的出口（真栈，退役判定）
python test/hint_retirement.py                                  # 干跑：盘点每条 hint 的命中语料
python test/hint_retirement.py --live --provider <A> --model <m> # 双臂裸跑（按 provider 分文件）
python test/hint_retirement.py --intersect                       # **跨 provider 取交集**才算候选
python scripts/retire_hints.py --apply                           # 按交集执行退役（默认 dry-run）
```

**三条最容易踩的**：①`--live` 车道会**全局 pin** active provider，跑完记得还原（脚本末尾有还原步，
被中止时要手工确认）；②退役判定**必须跨 provider 且覆盖全部命中句**，抽样会系统性高估；
③软层 A/B 的 Δ **只能在实际注入的子集上算**——未注入的两臂 prompt 逐字相同，翻面是采样方差。

### 5.3.1 探索式 QA 复现迷你集（`scripts/probe_qa_regression.py`，2026-08-15）

2026-08-15 探索式真实用户 QA 轮 58 个问题的**红绿对照基线**。取证脚本、不进 CI；
卡与阶段计划见 [`docs/design/2026-08-15-qa-exploratory-root-cause-cards.md`](../docs/design/2026-08-15-qa-exploratory-root-cause-cards.md)。

```bash
python scripts/probe_qa_regression.py --list                 # 38 例 / 63 轮 / 7 组（Q12 批 +slot 组 SL1-SL4）
# 汇总行的 [det]/[var] 是**确定性观测**（Q6 加）：末轮话术每次取样是否逐字相同。
# 它不参与 PASS/FAIL——「由确定性 handler 回答」这个主张，最直接的证据就是零方差。
# ⚠ 但 [var] 不一定是坏事：Q5 的出处披露只要求**出处**确定，正文本来就该由 LLM 说得自然。
python scripts/probe_qa_regression.py --mapping              # Q13：两个分类出口一致性（纯函数，不用起栈）
python scripts/probe_qa_regression.py --group negation       # 按组跑
python scripts/probe_qa_regression.py --repeat 3 --out base.json   # **定基线用这条**
```

**七条纪律，全部由这批自己的自伤沉淀（照做，别重新发明）：**

1. **persona 必须换 `user_id`，只换 `session_id` 证明不了任何隔离。**
   `reminder_item` **根本没有 `session_id` 列**，memory 同理——owner 是 `(user_id, occupant_id)`。
   QA 那轮 5 类 persona 全是同一个 `u1/primary`，于是「跨 persona 污染」看起来像泄漏，
   实际是**默认查询范围 = 全时段全会话**。换 user 要么配 `AUTH_TOKENS`，要么走签名 e2e 身份车道；
   WS 客户端**设不了 user_id**（`gateway/edge/auth.go` 匿名回落进程默认）。
2. **话术层的断言只能用形态判据**（有无动作 / 是否问句 / 是否逐字重复上一轮 / 动作名），
   **不能用关键词排除**。首跑 5 条假绿全是这么来的——同一条用例三次取样三种措辞
   （「其余行程不变」→「其他保持不变」→「其余保留」），按报告原文写的排除词一条没触发。
   探针为此有 `differs_from_turn` 原语：**「逐字重复上一轮」是「没回答问题」唯一可靠的机械判据**。
3. **单次取样不能当基线，一律 `--repeat >= 3`。** SF4 单次 PASS、三次实测 **0/3**
   ——差一点把一条稳定红降级成方差面。报读数用「n/N + 方差标记」，不报单次结论。
4. **客户端的东西必须走 CDP 车道。** 位置前置闸在 `hmi/src/App.tsx` 的 `send()` 里、
   `dispatch()` 之前；响应归属靠浏览器内的 FIFO。**WS 探针是从闸后面进来的**，
   对这两族跑多少轮都是假绿——同 §「测试若替被测系统提供了某个前提，那条前提就不再被验证」。
5. **「换个名字的关键词排除」还是关键词排除**（2026-08-16 Q6 批，尺子迭代四版、
   前三版都假绿）。审计问答的判据依次是：关键词排除 → `speech_has:["车窗"]`
   （「**关了车窗**」照样 PASS，方向说反判不出）→ 反向词表 + 「否认执行」词表
   （「车窗**没动**，音乐也没停，**没法真的控制车**」三个说法全不在表里）→
   **正向判据**（对象词附近必须出现该动作的**正确方向词**）。
   > **否认执行的表达空间比任何词表都大。** 正向判据的价值不是更准，而是
   > **把失败模式从假绿翻成假红**——模型换个说法而没带正确方向词就会红，我会去看话术；
   > 假绿永远不会有人去看。**宁可假红。**
   > 配套原语：`reflects_actions`（判动作名与方向，对措辞免疫）、`speech_not_regex`。
   > ⚠ 这四次迭代本身就是 Q6 的论据：**话术层判据验证不了「系统说的是不是真的」**，
   > 所以那类问题必须做成确定性 handler，而不是继续调尺子。
6. **用例要与它自己的上一次取样隔开，隔离标记必须是模型不愿意扔掉的东西**
   （2026-08-16 Q12 批）。SL1 建的是**持久**提醒，而「跨轮同名 + 再提醒 = 改期」
   是**正确行为**——于是第 2 次取样跑去改第 1 次留下的那条，3/3 被读成 1/3。
   探针为此有 `{run}` 占位（话术与判据同批替换，别只换一边），但**纯数字后缀会被
   planner 当噪声抹掉**，换成「代号 xxxxxx 的评审会」才保住。
   > 这与第 4 条那个「探针替被测系统提供前提」相反：**这里取消的是探针自己造出来的前提**。
   > 配套原语：`card_text_has` / `card_text_not` / `card_items_at_least`
   > ——**「说了两条」和「真有两条」话术层分不开，卡片层分得开**（I-008 现场原样：
   > speech 说「15:30 和 16:00 各提醒你一次」，卡片里两张同一个 id、第二张 `updated`）。
7. **一轮 = 「这一轮不再有新事件」，不是「收到第一个 final」**（2026-08-17 Q7 残余批，
   **尺子口径改过一次，读旧读数时注意**）。**混合意图路径一轮会发两个 `final`**：
   端侧先回本地那半、再把非本地片段上云，云侧回来又是一个。原实现拿到第一个就返回，
   于是「端侧执行了 A、云侧执行了 B」这一整类轮次**探针从来只看见 A**——
   OR2 即使修好也读不出来。现在收到 final 后进一个短 idle 窗（0.6s）继续收，
   窗内有事件就切回长超时等下一个 final，尾段总预算 25s；合并语义**刻意保守**
   （`actions` 全量合并、`speech` 追加，`need_confirm`/`card_type`/`operation_id`
   只在首个 final 为空时才由后续填）⇒ **单 final 的用例逐字不变**。
   > ⚠ **首版改完行为其实没变**（收到 `speech_delta` 后没切回长超时，0.6s 就超时返回，
   > 而第二个 final 在 5.2s），**差点宣布「尺子已修」**。
   > **改完尺子要验证它真的看见了新东西**——「我改了尺子」不等于「尺子变了」。
   > 单测 `scripts/tests/test_probe_qa_regression.py` 钉住合并语义。

## 5.4 意图与落域对抗套件（`test/eval_intent_adversarial.py`）

回答的问题和上面两类都不同：**意图是否完整、落域是否正确、决策链在哪里首次偏离**。

> 📘 **接手人先读运行手册：[`docs/guides/intent-adversarial-testing.md`](../docs/guides/intent-adversarial-testing.md)**
> ——怎么跑、红了怎么查、修 badcase 的产物是什么、加用例的自查清单、晋级与 baseline 的完整前置。
> 本节只留命令与口径要点。

规格 `docs/design/2026-08-02-intent-routing-adversarial-testing.md`，语料契约
`test/eval_corpus/intent_adversarial/README.md`，
**发现清单 + 修复批次记录** `docs/design/2026-08-02-intent-routing-adversarial-findings.md`，
**尺子硬化与独立复审记录**
`docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md` §7 / §8 / §9。

> **当前正式状态（2026-08-09）**：干净 `f0af9c0`、
> `deepseek:deepseek-v4-flash` 的 gate/all/live 父 bundle 147/147，L1/L2 各
> 2 个独立进程 × 每进程 3 样本，raw hallucination/escape/instability 均 0；资格函数
> `eligible=True`，首份**对比/参考模型** `docs/reviews/eval/baseline_intent_adversarial.{json,md}`
> 已写入。同一 SHA 的主模型 `minimax:MiniMax-M3` 为 141/147、raw hallucination 8/121、
> 6 条 `unstable`、无 `stable_fail`、`eligible=False`，不得被 DeepSeek 基线替代。
> **语料规模与 L0 读数不在本文件维护**——它每批都变，抄在这里必然滞后（这一行本身就曾
> 落后两批：写着 discovery 561 条 / 522 唯一输入 / L0 76/76，实际已是 574 / 535 / 81）。
> 当前值只查 `AGENTS.md` §4.0 的证据表。旧 113/117、104/122/123 与 raw 6/117 只保留在
> 历史 review/findings，不得当当前值。
> baseline 只绑定该 provider、embedding、资产与 SHA，不证明主模型、Agent 业务结果、外部内容
> 或跨模型平均质量。当前（2026-08-11）真实 LLM 只接受 MiniMax M3（主）与 DeepSeek V4 Flash（对比）；
> MiMo key 已失效，Qwen 不在批准模型内。

口径三条要点（改口径就要回头看所有依赖它的数）：

- **证据单元**：单轮 `case_id@layer`、多轮 `case_id#<轮号>@layer`、L3 `case_id@l3`。
  `turns` 里每一轮都会被执行，同一条 case 的多轮跑在同一个会话上。
- **规模单位是唯一输入不是条数**（`utterance+context` 指纹）；报告同时印 `cases=` 与
  `distinct_inputs=`。
- **`null` 不是 0**：没有 plan gold 的层不进 `exact_plan_set` 分母，没重复过的单元不进
  `instability_rate` 分母，没有 raw 通道的层不进幻觉率分母。分母为 0 时值是 `null`。

```bash
# ① 零网络 L0：契约 + 覆盖矩阵 + Edge ingress + Hint + 词法检索 + catalog 预算
python test/eval_intent_adversarial.py --suite discovery --layer l0 --strict

# ② reference-provider L1/L2（真实 Planner / 完整 Edge→Engine 链）
export LLM_GATEWAY_ADDR=localhost:50052        # 宿主跑必须显式给，见下方第 4 条
export EXEMPLAR_EMBED_TIMEOUT=8 SKILL_EMBED_TIMEOUT=8
python test/eval_intent_adversarial.py --suite discovery --layer l1 --live \
    --provider <p> --model <m> --temperature 0.3 --timeout 45 --ablations on-failure
python test/eval_intent_adversarial.py --suite discovery --layer l2 --live \
    --provider <p> --model <m>

# ③ 单案例复现（诊断用；每条报告的 expected.repro 里都印了这行）
python test/eval_intent_adversarial.py --case <case-id> --layer l1 --live \
    --provider <p> --model <m> --repeat 3 --diagnose

# ④ 选集与缺口速览（不跑模型）
python test/eval_intent_adversarial.py --suite discovery --layer l0 --list

# ⑤ 正式 gate / baseline（不可带 case/tag/cohort/risk/repeat 过滤器）
python test/eval_intent_adversarial.py --suite gate --layer all --live \
    --provider <reference-provider> --model <m> --strict
python test/eval_intent_adversarial.py --suite gate --layer all --live \
    --provider <reference-provider> --model <m> --strict --write-baseline
```

**六条纪律**：

1. **candidate 永远不进 live 层**。`--live` 只选 `suites.yaml` 的 `live_statuses`
   （reviewed/stable）——未经人裁的 gold 不配消耗真实模型，更不配进指标。
2. **seen / unseen 分开报，且按输入事实判**。同源原句与机械变体共享 `family_id`，但
   `family_id` 只防得住「作者记得它们同源」——换一个 family id，同一句原话就能同时进
   remediation 与 holdout（实测有 13 条这么漏过）。另有两条硬闸：同句不得跨 cohort、
   `unseen_transfer` 的原话不得字面出现在 `skills/` 下被注入的知识里。
   第二条**只证伪不证实**（Route Hint 是正则，对不上字面），所以 seen 一侧不设对称断言。
3. **gate L1/L2 的独立性由进程身份证明**。公开 parent 串行跑每层 2 个进程 × 每进程
   3 样本；同进程 repeat 3 不能代替第二进程。跨进程同错才是 `stable_fail`，只在一进程翻面
   是 `unstable`。**L0 例外**：无模型参与，一次红就是结论；L3 按授权 claim 只跑一次。
4. **宿主 embedding 三项必须同时给**。`orchestrator/cloud/embedding.py` 默认连
   `llm-gateway:50052`（容器内主机名），从宿主跑会被 `ALL_PROXY` 兜走并超时，范例检索
   静默降级成纯词法；同时把 `EXEMPLAR_EMBED_TIMEOUT` / `SKILL_EMBED_TIMEOUT` 设为 8 秒。
   缺任一项会以基础设施错误退出码 2 结束。从 worktree 跑 L3/all 时还要用
   `E2E_STACK_ROOT` 指向持有根 `.env` 的主 checkout。
5. **真实副作用永不执行，但替身必须留得下证据**。L2 的 Agent/VAL 全是 fake/spy；
   **确认闸内的能力一旦真被调到就自己造副作用**（不再依赖测试显式注入
   `confirmed_response`——真实 L2 从不注入，那条断言曾因此在最危险的一类动作上恒为真）。
   Edge 侧另看「需要二次确认的对象有没有被端侧执行」（`VAL._simulate` 探针 +
   生产自己的 `_need_confirm()` 判定）。
   仅此还不够：**副作用面只看动作有没有落地**，替身恰好不产生动作时它仍然恒真。
   所以危险用例必须同时写 `expected.engine`——那个 Agent 有没有被够着、挂起有没有落库。
6. **`domain_hit_rate` 与 `exact_plan_set_rate` 不可直比**。前者是 RoutingBench 的历史
   趋势口径（任一期望域命中即通过），后者要求全部必要组 + 无禁选 + 无未授权额外项 +
   依赖与关键槽位齐全。

## 6. Nightly 真实 LLM 语料（默认 skip）

复杂多意图、跨 Agent 组合、多轮指代依赖真实 LLM，单列 nightly，不进普通 PR 门禁：

```bash
make up
export LLM_API_KEY=...      # 宿主侧配置作为 nightly 开关
python -m pytest test/nightly -m nightly -v
```

未设 key 或全栈未起时用例自动 skip（不连网络、不拖慢普通全量）。详见 [`test/nightly/README.md`](nightly/README.md)。

## PoC 验收清单
| # | 链路 | 输入 | 期望 |
|---|---|---|---|
| 1 | 车控快路径 | 打开空调26度 | 端侧秒回，返回 `vehicle.control` 动作 |
| 2 | 云端导航 | 附近的充电站 | Planner 路由到导航 Agent，NEED_SLOT 追问关键词 |
| 3 | 云端闲聊 | 讲个笑话 | Planner 路由到闲聊 Agent，流式回复 |
| 4 | 确认闭环 | 订川菜馆今晚7点两位 → 确认 | 点餐 Agent 返回结果 → 确认 → 完成下单 |
| 5 | 多意图 | 打开空调并播放音乐 | 端侧拆分两个意图并行执行，话术合成 |
| 6 | 结构化车控 | 打开座椅加热 / 氛围灯设为蓝色 | 端侧秒回，走知识库校验+话术 |
| 7 | 危险车控确认 | 解锁车门 | 上云进入二次确认，确认后才经 VAL 执行 |
| 8 | ASR 转码 | POST /api/asr format=webm | ffmpeg 转码后正常返回文本 |

> 注：未配置 `LLM_API_KEY` 时 LLM Gateway 用 MockProvider，链路可跑通但复杂意图能力受限。

## 云数据迁移工具专项

迁移工具测试只使用 fake runner 和静态 shell 契约，不连接真实 Docker 数据卷、SSH 或云端：

```bash
python -m pytest scripts/tests/test_cloud_data_migration.py \
  scripts/tests/test_cloud_release.py \
  scripts/tests/test_cloud_deploy_assets.py -q
bash -n deploy/cloud/transaction-lock.sh deploy/cloud/backup.sh \
  deploy/cloud/remote-release.sh deploy/cloud/activate-release.sh \
  deploy/cloud/remote-data-migration.sh
python -m compileall -q scripts/cloud_data_migration.py scripts/cloud_data_migration_lib.py
```

测试必须锁定：online 零 stop/restart/down；final 缺少 `--quiesce-local --apply` 时仅列服务、不
生成快照；plan 和无 `--apply` 的 apply 不上传、不停服务；远端路径与 migration ID 拒绝注入；
三存储迁移失败整组恢复；任何路径都不得删除卷、备份、release、镜像或迁移包。专项全绿只证明
fake/static 契约，不得写成真实数据或云端迁移已经验证。
