# 多模型运行时硬化：评测锁定、active 持久化、429/流式降级与健康可视

- **状态**：落地中——P0（D1/D6/D8）已落地并真栈验证（2026-07-17，见 §8）；P1（D3/D4/D5）/P2（D2/D7）待做
- **交付对象**：后续实现者（人 / AI agent）
- **关联**：`llm-gateway/llm_runtime.py`、`llm-gateway/server.py`、`llm-gateway/providers.py`、`llm-gateway/http_server.py`、`agents/_sdk/clients.py`、`test/e2e_journeys.py`、`test/eval_common.py`；前作 [2026-07-07 多 LLM 源](2026-07-07-llm-asr-tts-multiprovider-and-sports-flags.md)、[R3.5 降级矩阵](2026-07-03-r3.5-degrade-matrix-e2e.md)（CompleteStream 无备用模型重试的缺口在此首次记录、当时决定只记不修）
- **姊妹篇**：[2026-07-17 数据真实性治理](2026-07-17-data-authenticity-governance.md)（「pin 住的请求绝不静默漂移」与「真实数据绝不静默变假」是同一条原则的两面）

## 0. 缘起：外部建议逐条判定

外部阅读仓库后提了 5 条「多模型运行时重构」建议。逐条对照现状后的结论——**本项目不缺多模型运行时（2026-07-07 已建成），缺的是它的四块硬化**：

| # | 建议 | 现状 | 判定 |
|---|---|---|---|
| 1 | 请求/会话级模型选择 | 只有全局 active（进程内存态，`llm_runtime.py:106-108`）；档位哨兵 `""`/`"@fast"` 已是请求级**档位**选择 | **改形采纳**：不做面向驾驶员的逐请求选模型（座舱产品刻意是「单一大脑」）；做 `meta["llm_provider"]` 请求级 pin，消费方=评测锁定与 dashboard badcase 重放 A/B，排 P2（见 D2） |
| 2 | 按任务自动选快/推理模型 | **已基本存在**：档位哨兵（chitchat 默认 `@fast`，`agents/chitchat/src/agent.py:86-93`；地标解析 `@fast`，`agents/_sdk/landmark.py:51`）+ thinking 按 `is_complex`/manifest `heavy` 动态开（`clients.py:24-30` contextvar 自动透传）+ `model_pref` meta 已有 | **拒绝新增机制**：无「某调用点想换档但换不了」的真实伤口；Planner 换 `@fast` 有路由质量风险（route_hints 的存在就是弱模型误路由的伤疤），须 eval 先行证明 parity 再谈。仅补 obs 档位审计字段（D6） |
| 3 | Provider 健康探测 | HMI 设置页 `available` = 有 key（`llm_runtime.py:124`），≠ 健康；dashboard 有逐调用 obs.llm 但无按厂商聚合健康面 | **改形采纳**：被动健康统计 + 按需手动探针（D5）。**拒绝周期主动探活**——对 4 家付费 API 定时烧 token，在 PoC 规模下纯浪费 |
| 4 | 429/超时自动切换 | 同厂商 primary→fast 降级链已有（仅 unary，`server.py:102-129`）；429 与连接错误同走 UNAVAILABLE（`server.py:140-147` 注释明示）；CompleteStream 单模型无降级（`server.py:151`，R3.5 已记录）；无 Retry-After 处理 | **改形采纳**：429 分类 + 流式连接期降级默认做（D3/D4）；**跨厂商自动切换默认不做**（D7 仅存设计、真实故障咬到再建）——不同厂商行为差异大（thinking 风格/路由质量/语气），静默换厂商正是姊妹篇要治的「静默降级」，与「单一大脑」确定性冲突 |
| 5 | 评测时模型锁定 | `e2e_journeys.py:116-122` 只**声明** active provider 进报告（「跨 provider 结果不可直接对比」），不锁定、不检测漂移 | **强采纳**（本规划最高价值项，D1+D2 合力）：两次真实事故——07-12 llm-gateway 重建后 provider 静默回落 env 默认、07-15 canonical 重跑 @M3 与基线 @mimo 不可比（泓舟会随手切 active）——全是它 |

## 1. 现状与证据（先承认已有的）

2026-07-07 落地的多 LLM 源已覆盖建议的「注册表」部分，不重做：

- **注册表**：mimo/minimax/deepseek/qwen（+legacy anthropic、无 key 兜底 mock），一套 `OpenAICompatibleProvider` 参数化覆盖四家（`llm_runtime.py:28-64`）。
- **全局 active + HMI 两级切换**：`POST /api/llm/provider`（`http_server.py:554-569`）；进程内存态，重启回落 env，HMI 载入时重放上次选择（`llm_runtime.py:9-11`）。
- **档位解析**：`""`→primary、`"@fast"`→fast、不认识的具体模型名→回落 primary（`llm_runtime.py:175-191`）；同厂商降级链 primary→fast（仅 `Complete`）。
- **缓存按 provider:model 分域**（`llm_runtime.py:193-195`、`server.py:89-91`）；embedding 与 chat 解耦。
- **obs.llm 收口**：网关唯一出口逐调用发事件（model/caller_service/latency/tokens，`server.py:35-57`）。
- **评测报告声明 provider**：`e2e_journeys.py:700,723,738`。

## 2. 问题（每条都有真实伤口）

- **P-1 active 是进程内存态**：llm-gateway 重建/重启 → 静默回落 env 默认。07-12 教训：改代码重建镜像后，后续调用全部换了脑子而无人知晓；HMI 重放只在 HMI 开着时救场，eval/无头场景裸奔。`llm_runtime.py:9-11` docstring 自己写着「多实例需 Redis，本 PoC 不做」。
- **P-2 评测只声明不锁定**：报告写了 provider，但跑到一半被切（人为切换或 P-1 的回落）不会被发现，产出的是**混脑报告**。07-15 canonical 重跑因此与基线不可直比，靠人肉「跑全量前先看 active provider」纪律硬扛。
- **P-3 CompleteStream 无备用模型**：`server.py:151` 只取 `models[0]`，失败直接 abort。R3.5 降级矩阵已把它记为真实缺口（当时决定只记不修）。chitchat 是 D0 流式直通主路径，首当其冲。
- **P-4 429 与连接故障同路**：providers 抛 `provider HTTP 429`（`providers.py:289-291`）→ 网关归入 UNAVAILABLE（`server.py:147`）→ SDK 误判为连接失效，白做一次 channel 重建重试（`clients.py:89-98`），且重试会把 primary+fast 整链再打一遍——限流时雪上加霜。无 Retry-After 读取。
- **P-5 「可用」≠「健康」**：设置页绿灯只代表配了 key；厂商侧故障/持续超时/限流只有翻 dashboard 逐调用记录才能发现。

## 3. 目标与非目标

**目标**：① 评测报告 100% 单脑可信（锁定+漂移即作废）；② active 选择跨重启存活；③ 限流/超时按语义分类处理，流式获得与 unary 同级的连接期降级；④ 厂商健康一眼可见。

**非目标**：不做面向用户的逐请求选模型；不做周期后台探活；不默认开启跨厂商自动切换；不改 proto、不改编排核心（全部改动收敛在 llm-gateway 进程内 + SDK 客户端 + 评测脚本）。

## 4. 方案（决策卡）

### D1 active 持久化（Redis）
`set_active` 写 `llm:active`（provider+model）到 Redis（`REDIS_URL` 栈内现成）；`_build()` 末尾读回覆盖 env 默认（读不到/Redis 不可达 → 保持现状并 log warn，**不因 Redis 挂而拒启**）。顺带兑现 docstring 里「多实例需 Redis」的注记。HMI 载入重放逻辑保留（幂等）。
⚠️ 依赖闭包：llm-gateway 的 requirements/Dockerfile 目前可能无 redis 客户端——按 07-10 nats-py 静默丢事件的教训，**加依赖必须核查该镜像的完整依赖闭包**。

### D2 请求级 provider pin（meta 管道）
`meta["llm_provider"]`（可选 `meta["llm_model"]`）：网关 `Complete/CompleteStream` 若见此键——registry 里有该 provider → 用它做档位解析与调用（缓存键、obs 事件均记**实际 serving** 的 provider）；没有 → `INVALID_ARGUMENT` **fail-closed**（pin 的意义就是不许静默漂移，与姊妹篇同一原则）。SDK 侧沿 thinking 先例从 contextvar meta 自动透传（`clients.py` 改一处全 Agent 覆盖）。**Pinned 请求永不参与任何降级切换厂商**（与 D7 互斥的硬规则）。
排 P2：首个消费方是 dashboard badcase 重放的跨厂商 A/B 对照；评测锁定不等它（D8 全局 pin 已够用）。落地前须先验证 meta 键在 WS→edge→cloud→agent 链路的透传面（实现时的第一件事，勿假设）。

### D3 429/超时分类
providers 抛结构化异常（带 `status_code`、`retry_after`），网关：
- **429** → 若 `Retry-After ≤ 2s` 且剩余预算充足，等一次后重试**同一模型**；否则跳过本厂商剩余档位（限流通常是账号级，fast 档大概率同样 429），映射 gRPC `RESOURCE_EXHAUSTED`。SDK 对 `RESOURCE_EXHAUSTED` 不做 channel 重建重试（那是连接语义），错误信息带「限流」字样供上层话术诚实降级。
- 超时→DEADLINE_EXCEEDED、请求性 4xx→INVALID_ARGUMENT 维持现状（07-13 已修好的语义不动）。

### D4 流式连接期降级
`CompleteStream` 在**首 token 前**失败 → 按 `resolve_models` 链重试下一档；**首 token 后不切**（半段话术不可拼接，宁可 abort 让调用方走既有失败路径）。兑现 R3.5 记录的缺口。

### D5 健康可视（被动统计 + 按需探针）
runtime 内每 provider 滚动窗口计数：`{ok, err, timeout, rate_limited, last_error, last_ok_at, ewma_latency_ms}`（挂在调用路径上顺手记账，可并入现有 `metrics.cost_tracker` 或新建 `health.py`）。`GET /api/llm/providers` 返回 health 块；HMI 设置页厂商行加状态点（绿=近期成功 / 黄=近期有失败 / 灰=未配置）。另加 `POST /api/llm/probe {provider}`：按需打一条 1-token Complete 返回 ok/latency——演示前手动体检用，**不做定时任务**。

### D6 obs 补齐
`obs.llm` 事件加三个字段：`provider`（serving 厂商 id）、`requested_tier`（原始 model 参数，审计谁在用什么档）、`pinned`（bool）。dashboard LLM 视图按 provider 过滤属锦上添花，低优。

### D7 跨厂商 failover（仅存设计，默认不建）
若未来真实厂商故障咬到演示：env `LLM_FAILOVER=off` 默认关；开启时仅对 unary Complete、仅在整链失败后按健康序试下一厂商；pinned 请求豁免；obs 必带 `failover_from`；响应 `model_used` 如实暴露。**触发条件=真实事故，不预建**。

### D8 评测锁定（本规划的靶心）
`eval_common.py` 加共享工具 `ProviderLock`：
1. 运行开始：`--provider X` 时 `POST /api/llm/provider` 钉住（D1 后跨网关重启也稳）；未指定则记录当前 active。
2. 每条 journey/case 结束：`GET /api/llm/providers` 核对 active 未漂移（一次 GET，开销可忽略）。
3. 漂移 → 该 run 标记 `provider_drift=true`、报告顶部红字、退出码非零（报告作废重跑，这是特性：评测期间人在 HMI 切脑本来就该炸）。
接入 `test/e2e_journeys.py` 与 `test/eval_mode_routing.py --live`（两个真 LLM 评测入口）；报告 meta 加 `provider_locked/drift_detected`。

## 5. 分阶段落地

| 阶段 | 内容 | 预估 | DoD |
|---|---|---|---|
| **P0** | D1 + D8 + D6 | 半天 | llm_runtime 单测（持久化 round-trip、Redis 不可达降级）；`docker compose restart llm-gateway` 后 `GET /api/llm/providers` active 不回落；journeys 带 `--provider` 跑通、手工切 provider 复现 drift 红字；全量 `make test` 绿 |
| **P1** | D3 + D4 + D5 | 1 天 | providers 异常分类单测；`MockProvider` 加 `LLM_MOCK_429`/首 token 前失败测试钩子（沿 R3.5 `LLM_MOCK_DELAY_MS` 先例）驱动 e2e：429 不触发 SDK 重连重试、流式首 token 前降级到 fast 档成功出流；HMI 设置页状态点 CDP 截图 |
| **P2** | D2（先做 meta 透传面验证）；D7 视事故触发 | 1 天 | pin 命中/未配置 fail-closed 单测；dashboard 重放带 pin 的 A/B 真栈演示 |

每阶段惯例：改 llm-gateway/HMI 必 `--build` 重建对应容器（无卷挂载）；改共享依赖查全部消费镜像的依赖闭包。

## 6. 验收

```bash
pytest llm-gateway/tests agents/_sdk -q          # 单测
python test/e2e_journeys.py --lane mock --provider mock   # 锁定字段进报告
docker compose restart llm-gateway && curl :50059/api/llm/providers  # active 不回落
python test/eval_mode_routing.py --live --provider mimo   # live 评测锁定
```

## 7. 风险与开放问题

- **Redis 依赖面扩大**：llm-gateway 首次连 Redis；断连必须降级为现状（内存态）而非拒启。
- **429 等待与步骤预算互动**：D3 的 ≤2s 等待须尊重 `context.time_remaining` 预算级联（comms hardening P1 已有机制），不许把等待叠进用户可感延迟上限之外。
- **meta 透传面未核验**（D2）：WS 入口→edge→cloud→agent 是否逐跳保留任意 meta 键，实现前先做链路验证；不通则 D2 范围收缩为「云内调用方（dashboard 重放直打 planner）可 pin」。
- **评测锁定与人共用栈**：run 期间 HMI 切脑=报告作废。写进 AGENTS.md 验证纪律即可，不做「禁止切换」的强锁（demo 栈上锁死切换伤害更大）。

## 8. 落地记录

- **P0 ✅（2026-07-17 当日，随规划批准即落地）**：
  - **D1**：`llm_runtime.py` `_redis_client()/_load_persisted()/_persist_active()`——读回时校验
    provider 在注册表、model 在该厂商词表，不合法保持 env 默认；Redis 缺包/不可达降级内存态
    （仅告警不拒启）；`REDIS_URL` 已在 compose `python-env` 锚里，零 compose 改动；
    llm-gateway requirements 增 `redis>=5.0,<7`（依赖闭包检查过：仅该镜像消费）。
  - **D8**：`eval_common.ProviderLock`（stdlib urllib，保持 eval_common 零业务依赖）；
    `e2e_journeys.py --provider`（pin→逐旅程 `check`→报告 `provider_lock` 块+md 作废红字→
    漂移退出码 1）；`eval_mode_routing.py --live --provider` 同款，且漂移**拦住
    `--write-baseline`**（不许把混脑报告写成基线）。
  - **D6**：`observability/events.py::emit_llm` 增 `provider/requested_tier/pinned`；
    `collector/db.py` `llm_calls` 增 `provider` 列（`_ensure_column` 加法迁移，兼容既有
    named-volume 旧库）。
- **验证**：单测 llm-gateway +5（FakeRedis round-trip / 未知 provider 回落 / Redis 挂降级 /
  无 REDIS_URL 旁路）、ProviderLock +4；全量 **1661 passed / 7 skipped**。真栈：重建后首次
  启动回落 env 默认（持久化为空，footgun 最后一次生效）→ POST minimax 后
  `docker compose restart llm-gateway` → active 保持 `minimax:MiniMax-M3`；漂移演练
  pin→切 mimo→`drift_detected=true`（含 from/to/at）→恢复原值 PASS；probe Complete 一条 →
  collector `llm_calls` 落 `provider='minimax'`。
