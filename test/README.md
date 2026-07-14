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
**当前结果：1200 passed, 7 skipped（2026-07-10 实测；skip 含 nightly 真实 LLM 默认跳过）。**
注意 CI 按分组进程隔离跑（见 `.github/workflows/ci.yml` run_group），本地单命令与 CI
口径一致；前端另有 `hmi` 127 + `dashboard` 14（node/vitest）。

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

`npm test` 覆盖增量 TTS 切句、final 去重、顺序播放和取消。**当前结果：5 passed；
Vite 生产构建通过。**

## 4. Dashboard 单测与构建

```bash
cd dashboard
npm test
npm run build
```

覆盖 trace 聚合、状态 diff、Agent 健康与环境量 debug 交互。**当前结果：4 passed；
Vite 生产构建通过。**

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
python test/e2e_obs.py                     # 断言型：badcase 排查观测链路 16 断言（obs.turn 落库/plan 门控采集/obs.llm/日志按 trace 关联/badcase 标记检索导出/重启 collector 持久化）
python test/e2e_voice_loop.py              # 断言型：语音回路后端契约（/api/asr/stream PCM 直传 partial→final→done + vad_silence_ms 透传 + TTS round-trip）——浏览器声学层 CI 测不了，留真麦
python test/e2e_tts_stream.py              # 断言型：R4.2 服务端流式 TTS（cosyvoice 首帧延迟 G1 门槛 + cancel 收尾）——需 DashScope key
python test/e2e_degrade.py                 # 断言型：架构 §3.3 降级矩阵四行（单 Agent 故障/LLM 超时/云 Planner 故障/断网）——docker 级故障注入 + 严格 try/finally 恢复，务必放在其它 e2e 脚本之后跑
python test/e2e_auth.py                    # 断言型：会话鉴权（需 AUTH_REQUIRED=true + token，非默认栈配置）
python test/e2e_mtls.py                    # 断言型：服务间 mTLS（需 GRPC_TLS=on + scripts/gen-certs.*，非默认栈配置）
python test/e2e_journeys.py                # 旅程级（L3）：跨 Agent 自主执行 × 全场景连续对话（见下节）
python -m pytest test/e2e_real_providers.py -q -s   # 无需 docker：真实三方 provider 冒烟（按 key 自动 skip）
```

### 5.1 旅程级测试（L3）与 HMI CDP 层（L4）

设计与语料口径：`docs/design/2026-07-14-journey-e2e-test-system.md`。

```bash
python test/e2e_journeys.py                       # 全部旅程（live 栈，真 key）
python test/e2e_journeys.py --level regression    # 仅回归级（必须 100% 绿，红=回归）
python test/e2e_journeys.py --level target        # 仅目标级（能力标尺，允许红——红灯=工程 backlog）
python test/e2e_journeys.py --lane mock           # mock-safe 子集（nightly 跑的就是它）
python test/e2e_journeys.py --id A4-2,B4-2        # 指定旅程
python test/e2e_journeys.py --list                # 列语料不执行
node test/hmi_cdp/run_cases.mjs                   # L4：HMI 二次交互 CDP 用例（渲染/点击→WS 帧断言）
```

- 语料在 `test/journeys/*.yaml`（regression=保护存量 / target=定义目标能力）；新增旅程改语料
  不改 runner，schema 严格校验（拼错断言键直接拒跑）。
- 报告落 `docs/reviews/eval/journeys_report.{json,md}`（含 active LLM 声明与时延基线——
  **跨 provider 结果不可直接对比**）；失败轮自动标 collector badcase，dashboard 收藏夹可重放下钻。
- 运行纪律：全栈起后 settle ≥40s；**禁与 docker build 并发**；外部数据源断供（api-football
  超时等）按语料内 `skip_journey_if_speech_any` 约定判 SKIP 不判 FAIL。
- L4 前置：宿主装有 Edge/Chrome（`CDP_BROWSER` 可指定路径）、宿主 5173 未被本地 vite 占用；
  截图证据落 `test/hmi_cdp/shots/`（gitignore）。

一次跑全部脚本：`make e2e`（本地全量清单，`scripts/run_e2e.sh` / `run_e2e.ps1`；假定 `.env` 可能
配了真实 key，未配置时部分用例按记忆系统既有 SKIP 约定优雅跳过或合理失败，非回归）。
`.github/workflows/nightly-e2e.yml` 跑的是**裁剪、无需任何密钥即可确定性全绿**的子集（`--case`
过滤掉依赖真实 LLM 路由/embedding 的用例）。两者刻意不同，脚本清单的单一真相源是文件本身，不在
本 README 手工重复维护第二份列表；细节见 `docs/design/2026-07-03-r3.3-e2e-ci-gate.md`。

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
