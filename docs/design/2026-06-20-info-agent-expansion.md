# info Agent 能力扩展：联网搜索 / 新闻 / 股票（只读聚合）+ 票务独立

- **状态**：草案（2026-06-20）。`info.weather` 已落地并真实凭证冒烟通过；本文规划其余 info.* 与票务边界。
- **交付对象**：后续开发者 / Agent，按 §5 分阶段落地。每个能力接 provider 时**遵循** [`docs/guides/provider-integration.md`](../guides/provider-integration.md)。
- **关联代码**：`agents/info/`（现有范本）、`orchestrator/edge/fast_intent.py`、`orchestrator/cloud/planning.py:119`（动态 catalog）、`payment-gateway/`、`proto/cockpit/payment/v1/payment.proto`
- **关联文档**：`docs/architecture/detailed/ws6-real-capabilities-and-agent-collaboration.md`、`docs/conventions.md`、`CLAUDE.md` §5

---

## 1. 现状与证据

- `info` Agent 已建（`agents/info/`，端口 50067，trust `first_party`，能力 `info.weather`）。Provider 三层范式 + `_sdk/http.py` + 可观测均已就绪。
- 边侧 `fast_intent.py` 对各「准 info 能力」现状**各不相同**（落地时按此对症，别假设统一）：

| 能力 | 边侧现状（`fast_intent.py`） | 含义 |
|---|---|---|
| 天气 | `:147` / `:1432` → `info.weather` | ✅ 已收敛，有 agent |
| 股票 | `:984` `_s("information","query","query","stock")`；`:1380` online_only | ⚠️ 已上云但**孤儿意图**（无 agent、命名为 `information/stock` 未对齐 `info.*`） |
| 新闻 | `:861-867` "新闻"→`media.play/news`；`:1375` news 归 `media.*` | ⚠️ 当前被当**媒体音频播放**，与「新闻摘要(只读信息)」是两件事，需消歧 |
| 联网搜索 | 无任何 pattern | ❌ 边侧完全没有，需新增分类 |

- 云端 Planner **动态**从 registry 构建能力 catalog 并按各 agent 真实 capabilities 校验（`planning.py:119`、`:201`）——**新增 info.* 意图，agent 声明后即可被路由，零编排核心改动**。

## 2. 问题

1. 用户问股票/搜索/新闻摘要，云端**没有 agent 接**（同 `info.weather` 落地前的"天气四头蛇"问题）。
2. `stock` 是孤儿意图、命名未对齐；`news` 语义和媒体播放撞车——不先约定，落地必歧义。
3. 票务涉及交易/支付，若混进 info（只读信息 agent）会破坏 trust 分级与安全红线。

## 3. 目标与边界决策

- **info = 只读信息聚合**（first_party、低风险）：`info.weather`(已) + `info.search` + `info.news` + `info.stock`。统一 `info.<action>` 命名空间。
- **票务等交易类独立**成 `ticketing` Agent（third_party、经支付网关、强制二次确认），**不进 info**。与现有 food-ordering/parking-payment 交易范式一致。
- 命名：复用 `info.*`；落地时**收敛边侧到 info.***（同 weather 做法），消除 `information/stock`、`media/news` 歧义。

## 4. 方案

### 4.1 能力清单（落地蓝图）

| 意图 | Provider 接口（新建于 `agents/info/src/providers/`） | slots | scope | 边侧要做 | 真实厂商候选 |
|---|---|---|---|---|---|
| `info.search` | `SearchProvider.search(query)->list[SearchResult]` | query | network.external | **新增**分类：搜索类问句→`info.search` | Bing/Google/博查/厂商搜索 API |
| `info.news` | `NewsProvider.headlines(topic,limit)->list[NewsItem]` | topic, limit | network.external | **消歧**：摘要/头条→`info.news`；"播新闻"仍走 `media.*` | 聚合资讯 API |
| `info.stock` | `StockProvider.quote(symbol)->Quote` | symbol/name | network.external | **收敛** `information/stock`→`info.stock` | 行情 API |

> 三者都是「只读、网络出站」，scope 仅需 `network.external`（已在 PoC 默认授予集，`engine.py`）。每个能力 = 在 `info` manifest 加一条 capability + 新 Provider 接口（base/mock/real）+ `agent.py` 加一个 `_handler`，接 provider 严格走 [provider-integration 指南](../guides/provider-integration.md)。

### 4.2 各能力骨架（决策已定，实现细节留执行者）

**`info.search`（联网搜索，建议 P0——可作其他能力的底座）**
- `SearchProvider.search(query, limit=5, meta=None) -> list[SearchResult{title,url,snippet,source}]`
- `agent.py`：缺 query→NEED_SLOT；调 provider→拼播报话术 + `ui_card{type:"search_list",items}` + `data` 供编排引用。
- 边侧：新增"搜一下/查一下 X 是什么/帮我搜"等 pattern→`info.search`（online_only）。开放域兜底也可由 Planner 路由到它。

**`info.news`**
- `NewsProvider.headlines(topic="", limit=5, meta=None) -> list[NewsItem{title,summary,source,publish_time}]`
- **消歧**：保留 `media.play/news`（音频播报）；新增"新闻摘要/头条/今天发生了什么"→`info.news`（文本摘要）。在 `fast_intent.py:861` 区分"播/听"(media) vs "看/摘要/头条"(info)。
- 长摘要可流式（Agent 重写 `handle_stream`，参考 chitchat）。

**`info.stock`**
- `StockProvider.quote(symbol, meta=None) -> Quote{name,price,change_pct,...}`；可选 `index()` 查大盘。
- **收敛**：`fast_intent.py:984` 的 `information/query/stock` 映射到 `info.stock`（同 weather 收敛手法，并清理 engine 里可能的孤儿 scope）。
- symbol 解析（"茅台"→代码）可在 provider 内做或借 `info.search`。

### 4.3 票务独立 Agent（`ticketing`，交易类）

- **为什么独立**：交易/支付 ≠ 只读信息。trust=`third_party`，必须经**支付网关**、**强制二次确认**，与 food-ordering/parking-payment 同范式。混进 info 会破坏安全红线。
- 端口从 **50068** 起（`conventions.md` §5）。能力示意：`ticketing.search`（查票，只读）→ `ticketing.book`（下单，`require_confirm`）。
- 支付走 `payment-gateway`（`proto/cockpit/payment/v1/payment.proto`：`Authorize`→返回 `payment_id`+`require_confirm`→用户确认→`Capture`）。Agent **不持支付凭证**。
- 详细落地参考 [新增独立 Agent 设计](2026-06-20-standalone-agents-roadmap.md) §「交易类 Agent 范式」与 ws6 §2。

### 4.4 与现有架构打通（每个能力都要确认）
1. 在 `agents/info/manifest.yaml` 加 capability（intent + slots + examples）——Planner 动态 catalog 自动可见可校验。
2. real provider 严格走 [provider-integration 指南](../guides/provider-integration.md)（_sdk/http、工厂、降级、可观测、测试）。
3. 边侧若该能力是端可判的关键词，按 §4.1「边侧要做」加 `fast_intent` 分类并收敛命名；smoke_edge 13/13 不破。
4. 若要被别的 agent 协作调用（如 trip-planner 取天气/搜索），确认 `agents/_sdk/agent_client.py` 的 `port_map`/`<AGENT_ID>_ENDPOINT` 可解析（info=50067 已补）。

## 5. 分阶段落地

- **P0 `info.search`**：底座能力，开放域问答可路由到它。新 Provider+mock+real+边侧分类+测试。
- **P1 `info.stock`、`info.news`**：收敛/消歧边侧命名；各自 Provider+real+测试。
- **P2 `ticketing` 独立 Agent**：交易范式（支付网关+确认），见 standalone-agents-roadmap。

每阶段 DoD：`pytest` 全绿 + 该能力真冒烟（`test/e2e_real_providers.py` 加一条，无 key skip）+ 边侧改动 `smoke_edge.py` 13/13 + `conventions.md` 意图表/端口表更新。

## 6. 验收
- 无凭证：各 info.* 回退 mock，全链不阻断。
- 有凭证：真冒烟返回真实数据（非 mock 签名），Dashboard 见 `provider.<vendor>.*` span。
- 边侧命名收敛后 `grep` 无 `information/stock`、无 news 歧义残留；Planner 能路由新意图且校验通过。

## 7. 风险
- **新闻消歧**：媒体播报 vs 信息摘要边界要清晰，否则用户"听新闻"被错路由到 info。
- **搜索内容安全**：联网搜索结果需经内容审核（`security/`），避免注入/不良内容直出 TTS。
- **行情合规**：股票数据源的免责声明与频率限制；不做投资建议。
- **票务**：务必走支付网关 + 确认，绝不让 info/ticketing 持支付凭证。
