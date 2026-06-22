# 联网搜索质量重构 + 信息卡片重设计

- **状态**：已落地（2026-06-22）。P1-P5 + 二/三/四轮实测修复全部完成，真实 Exa/api-football/MiMo 端到端验证；全量 680 passed + HMI 19/19/build 通过
- **交付对象**：info Agent 开发者 + HMI 前端开发者
- **关联代码**：`agents/info/src/agent.py`（`_search`/`_news`/`_summarize_sources`）、`agents/info/src/providers/`（`base.py`/`search_any.py`/`news_serpapi.py`/`__init__.py`）、`hmi/src/components/Cards.tsx`、`hmi/src/components/ChatView.tsx`、`hmi/src/types.ts`
- **关联文档**：[2026-06-20-search-news-redesign.md](2026-06-20-search-news-redesign.md)（前序，本文取代其卡片方案）、[2026-06-20-info-agent-expansion.md](2026-06-20-info-agent-expansion.md)、`docs/guides/provider-integration.md`
- **取代**：本文的卡片方案**取代** 2026-06-20-search-news-redesign.md（`search_answer`/`news_digest` 重复结论的设计）。

---

## 1. 现状与证据

### 1.1 链路
- 搜索：`SEARCH_VENDOR=anysearch`，`AnySearchProvider`（`search_any.py`）`POST /v1/search` → `{title,url,snippet,source}`。
- 新闻：`SerpApiNewsProvider`（`news_serpapi.py`）Google News / Baidu News，AnySearch 兜底。
- 合成：`agent.py:314` `_summarize_sources` 把「标题（来源）：snippet」喂 LLM（经 llm-gateway，模型由 env 配置，项目默认 MiMo `mimo-v2.5-pro`），产出 speech。
- 卡片：LLM 成功 → `search_answer`/`news_digest`（`agent.py:441`/`:479`）；失败 → 旧 `search_list`/`news_list`。
- 渲染：`ChatView.tsx:87` 气泡渲染 `msg.text`(=speech)；`:93` 紧跟 `CardRenderer`。

### 1.2 实测问题（用户用例）
> 问「今天世界杯赛程及结果」→ 答复给出「西班牙vs沙特00:00、比利时vs伊朗03:00…」等**大量事实性错误**（编造的对阵与时间）。

## 2. 问题根因（已定位，非参数问题）

| # | 根因 | 证据 | 影响 |
|---|---|---|---|
| R1 | **Prompt 主动禁止弃权** | `agent.py:340-345`：比分类「即使资料只包含部分比赛，也要把已知比分告诉用户，**不要说'无法确认'或'没有数据'**」；`:372`「不要轻易说'无法确认'——先把已知信息告诉用户」 | snippet 无真实比分时，模型被指令禁止弃权 → 编造看似合理的赛程。**幻觉首因**。 |
| R2 | **只喂 snippet，从不读正文** | `agent.py:433` 仅传 `{title}（{source}）：{snippet}`；Provider 只取 `snippet`（`search_any.py:61`） | 1~2 句稀疏摘要、跨日期混杂、常为历史背景 → 模型补全填空。**质量天花板根本限制**。 |
| R3 | **查询改写=硬编码关键词** | `agent.py:40` `_fresh_search_query`：命中「今晚/赛程」拼「当日赛程」+日期 | 脆弱、不泛化；无时效过滤，实时与历史资料混排。 |
| R4 | **卡片重复结论（设计写死）** | `card.answer`==`speech`（`agent.py:444`+`:450`）；`Cards.tsx:279` 渲染 `card.answer`，气泡已渲染 `msg.text` | 同一段话出现两遍。 |
| R5 | **来源时展开时折叠=两套卡** | LLM 成功走 `search_answer`（来源折叠 `Cards.tsx:275`），失败退化 `search_list`（全平铺 `:257`） | 同类查询 UI 行为不一致。 |

> 模型不是瓶颈（Opus 完全能遵循「有依据才答、否则弃权 + 标注来源」）。瓶颈是**喂给它的原料（R2）**与**逼它别弃权的指令（R1）**。2026-06-21 的修补（去省略号/列表格式/定位）均为表层，未触 R1/R2。

## 3. 目标

让联网搜索对**新闻、赛事、概念解释、信息查询**达到「ChatGPT 级」可用：答案基于正文级原料、有来源、不确定时诚实弃权；卡片承载证据而非复读结论。

## 4. 方案

### 4.0 产品决策（2026-06-22 确认）
- **检索**：Exa 作主搜索（`contents.text` 直接返回正文），AnySearch 降为兜底搜索 + 其 `extract` 用于正文补抓；保留 Bing/mock 末端兜底。
- **实时结构化事实**：接 **api-football** 覆盖赛事比分/赛程。
- **卡片**：气泡给结论（语音同步），卡片只给证据（关键数据 + 来源 + 时效 + 置信度），**绝不复读结论**。

### 4.1 检索管线（`_search` 重构）

```
query
 └─(0) 赛事路由：命中赛事意图 → api-football（见 4.3），直接产结构化结果，不进通用搜索
 └─(1) 查询规划（LLM，haiku 快档）：产出 {search_query, recency_days, kind}
        kind ∈ {realtime, news, concept, lookup}；取代 _fresh_search_query 关键词拼接
 └─(2) 检索：Exa /search，contents.text(maxCharacters≈1800) + numResults 5~6
        + startPublishedDate(由 recency_days 推) ；kind=news 加 category=news
        失败 → AnySearch /v1/search → Bing → mock
        （可选增强）某结果正文为空 → AnySearch extract 该 url 补抓【待契约】
 └─(3) 接地合成（LLM，opus）：见 4.2，输入正文（非 snippet）
        产出 {answer, key_points[], confidence, used_sources[]}
 └─(4) 结果：speech=answer；ui_card=证据卡（见 4.4），不含 answer 文本
```

### 4.2 接地合成原则（核心，修 R1/R2）
System：「你是严谨的车载信息编辑。只能依据提供的资料作答，**资料未覆盖的部分必须明确说明未获取到，禁止编造对阵、比分、时间、数字、因果**。」
要求：
1. 用中文给结论，先核心后展开；不说「根据搜索结果」。
2. **每条关键事实标注来源序号 [1][2]**（对应输入资料编号）；无对应来源的陈述不得输出。
3. **资料不足以回答时，直接说明「未能从检索到的资料中确认 X」**——删除所有「不要说无法确认/先把已知信息告诉用户」类指令。
4. 返回结构化 JSON：`{answer, key_points:[...], confidence: high|medium|low, used_sources:[idx...]}`（confidence 由「关键问句被来源覆盖的比例」自评）。
5. 列表类（新闻/比分）`key_points` 逐条；解释类 `answer` 连贯成段。

### 4.3 赛事 Provider（api-football，新增）
- Host `https://v3.football.api-sports.io`，头 `x-apisports-key: <API_FOOTBALL_KEY>`。
- 响应：`response[].{fixture:{id,date,status:{short,long,elapsed}}, league:{id,name,round}, teams:{home:{name,logo},away:{name,logo}}, goals:{home,away}}`。
- 映射 → `SportsFixture{league, league_id, round, home, away, home_logo, away_logo, home_goals, away_goals, status, status_text, elapsed, kickoff}`。
- **查询策略（关键，免费档实测得出）**：**按日期单查 `/fixtures?date=YYYY-MM-DD&timezone=Asia/Shanghai`（不带 league/season），再客户端按 `league_id` 过滤**。原因：
  - `date+league` 必须带 `season`，而 api-football **免费档不开放 2026 等当前赛季**（报 `Free plans do not have access to this season, try from 2022 to 2024`）；
  - 但**单日期查询**在「今天±1」窗口内免费档放行（实测 2026-06-22 返回 158 场全联赛，含 `World Cup | Spain 4-0 Saudi Arabia | 已结束`）。
  - 该策略对免费/付费档都适用，付费档亦可正常返回。
- 路由：`info` 新增 `info.sports` 能力（manifest 声明，供 Planner 路由）+ `_search` 内 `_maybe_sports(query)` 兜底检测（命中已知赛事+意图词才走，避免误伤普通搜索）。
- **已知局限**：api-football 队名为英文（Spain/Saudi Arabia），中文化需额外映射或 LLM（后者会重新引入命名幻觉风险），暂保留英文（准确优先）；后续可加固定映射表本地化。

### 4.4 新卡片契约（取代旧四类，修 R4/R5）
**原则**：气泡=结论；卡片=证据；来源呈现全局统一（默认显示前 3，多余「更多 N 条」展开）。

| 卡片 type | 字段 | 说明 |
|---|---|---|
| `search_result` | `query, key_points[], sources[{title,url,source,published}], freshness, confidence` | 通用搜索/概念/查询。**无 answer 字段**（结论在气泡）。 |
| `news_brief` | `topic, items[{title,source,published,snippet}], freshness` | 新闻。结论在气泡，卡片列头条证据。 |
| `sports_scores` | `title, fixtures[{league,round,home,away,home_logo,away_logo,score,status,kickoff}], freshness, source` | 赛事结构化比分/赛程。 |

- 公共元素：右上 `⏱ 时效`（最新来源时间/「刚刚」）、底部 `置信度` 徽标（low 时给醒目「未充分核实」态）。
- 旧 `search_answer`/`news_digest`/`search_list`/`news_list`：HMI 保留渲染兼容一个迭代，Agent 不再产出；下个迭代删。

### 4.5 Provider/数据结构改动
- `base.py`：`SearchResult` 增 `published: str`、`content: str`（正文，区别于 `snippet`）；新增 `SportsFixture` dataclass + `SportsProvider` ABC。
- 新增 `search_exa.py`（ExaSearchProvider）、`sports_apifootball.py`（ApiFootballProvider）。
- `search_any.py`：保留 search；新增 `extract(url)`【待契约】。
- `__init__.py`：`build_search_provider` 改为 Exa→AnySearch→Bing→mock；新增 `build_sports_provider`（api-football→mock）。
- `.env.example`：新增 `EXA_API_KEY`、`EXA_BASE_URL`、`API_FOOTBALL_KEY`、`API_FOOTBALL_HOST`；AnySearch extract 注释。**真实 key 由用户写入 `.env`（gitignored），不进仓库。**

## 5. 分阶段落地
- ✅ **P1 检索质量（最高优先）**：`SearchResult` 扩展 content/published + `ExaSearchProvider` + 工厂改 Exa 主；`_search` 改为「正文 + 接地合成（引用+弃权）+ `_plan_search` 时效窗口」；删除旧 `_summarize_sources`「逼答」逻辑；单测含诚实弃权用例。
- ✅ **P2 赛事**：`ApiFootballProvider` + `info.sports` + `_maybe_sports` 路由（命中赛事+意图词才走结构化源）+ `sports_scores` 卡；单测。
- ✅ **P3 新闻**：`_news` 套用接地合成 → `news_brief` 证据卡。
- ✅ **P4 卡片/HMI**：`search_result`/`news_brief`/`sports_scores` 新契约 + `Cards.tsx` 证据卡组件（去重 + 统一来源「前3+更多」+ 时效/置信度徽标）+ 样式；HMI 19/19 + `vite build` 通过。
- ⏳ **P5 AnySearch extract**：待用户补契约后，作 Exa 正文为空时的补抓兜底接入。
- 横切：✅ manifest（+info.sports）、✅ `.env.example`（EXA/API_FOOTBALL）；AGENTS.md 测试计数/能力更新中。

## 6. 验收
- [ ] 「今天世界杯赛程及结果」→ 命中 api-football，给**真实**当日对阵/比分/状态；无数据的场次明确标注未开赛/未获取，**不编造**。
- [ ] 概念解释类（如「解释一下 MoE」）→ 基于 Exa 正文，结论连贯、关键句有来源；资料不足时诚实说明。
- [ ] 「今天有什么新闻」→ 气泡给摘要结论，卡片列头条证据，不重复。
- [ ] 卡片不再复读气泡结论；来源呈现全场景一致；low confidence 有醒目态。
- [ ] Exa/api-football 故障 → 降级链生效，不击穿主链。
- [ ] `pytest` 全绿 + HMI `npm test && npm run build` 通过。

## 7. 风险
- **延迟**：查询规划(haiku)+Exa 正文+合成(opus) 串行。规划用 haiku、Exa `numResults≤6`/`maxCharacters≈1800`、合成 `max_tokens` 受限；必要时规划与检索合并或并行预取。
- **成本**：Exa 按检索+正文计费、api-football 有配额。工厂按 env 开关，缺 key 自动降级 mock，不阻断 PoC。
- **赛事覆盖**：已用「按日期查+客户端过滤」绕过免费档赛季门限，今天±1 的赛事可用并实测真实比分（Spain 4-0 等）；但免费档仍有配额上限、且日期窗口仅限今天附近——超额、超窗口或非足球赛事 → 回落通用搜索 + 诚实弃权（不编造）。付费档可放宽。
- **AnySearch extract 契约未定**：P5 才接，先不挡 P1-P4。

## 8. 二轮实测修复（2026-06-22，用户验证后）

| # | 实测问题 | 根因 | 修复 |
|---|---|---|---|
| R2-a | 「解析下 Loop Engineering」等输出退化 | **合成 LLM 超时**：5×1800 字符正文 + max_tokens 900 触发 `DEADLINE_EXCEEDED`，退化为 snippet 拼接（Exa 检索本身正常） | `_synthesize_grounded` 裁剪正文 5×1000、max_tokens 600、timeout 25s |
| R2-b | 「今天有哪些值得关注的新闻」结果差 | 新闻走 serpapi/百度「今日热点」→ 聚合页标题 + 同标题重复 3 条 + 半月前旧闻 | `_news` 改 **Exa 优先**（category=news + recency 1 天 + 正文级）→ serpapi → anysearch；`_dedup_news` 按标题去重 |
| R2-c | 「明天世界杯」返回今天已结束 | `_sports_date` 只读清洗过的 `slots["query"]`，丢了「明天」 | 改用 `intent.raw_text` 做赛事识别与日期判断 |
| R2-d | 赛事卡无国旗、队名英文 | HMI 未渲染 logo；provider 直出英文名 | `FixtureRow` 渲染 `home_logo/away_logo`；provider 加国家队英→中映射（静态、无幻觉） |
| R2-e | 卡片「下面又是总结」 | `search_result` 卡含 `key_points`，与气泡结论重复 | 卡片去掉 `key_points`，只留来源+时效+置信度（提示用户硬刷新清旧 bundle） |
| R2-f | — | AnySearch extract 契约确定（MCP `tools/call`） | `AnySearchProvider.extract(url)` 经 `POST /mcp` JSON-RPC；`_search` 中 Exa 正文为空时 best-effort 补抓前 3 条 |

> 实测佐证：Exa 对「动态数据流架构/全球首款」命中理想马赫 M100 真实资料（ithome/新浪/腾讯云）；对「Loop Engineering」命中 segmentfault/woshipm/bnext 全文——检索从不是瓶颈，合成超时才是。

## 9. 三轮实测修复（2026-06-22，用户二次验证后）

| # | 实测问题 | 根因 | 修复 |
|---|---|---|---|
| R3-1 | 多要点答复挤成一段、可读性差 | 合成把「1. … 2. …」塞在一行（气泡 CSS 已 `pre-wrap`，本可换行） | 合成 prompt 要求多条目**每条单独成行**（`\n` 分隔），解释类仍连贯段落 |
| R3-2 | 无效城市「当前未知的」返回 mock 假天气 | `_weather` 等真实 provider 失败 → fallback `MockWeatherProvider` 编造 | 5 个天气子处理器（weather/forecast/alerts/indices/air_quality）真实 provider 失败 → **诚实报错**，删除 mock 兜底（预警尤其不可谎报"无"） |
| R3-3 | 「我在哪里」拿不到定位 | HMI `shouldRequestLocationConsent` 的 `LOCATION_DEPENDENT_TERMS` 不含「我在哪/当前位置」→ 不弹授权 → 无坐标 | 加入「我在哪/当前位置/我的位置/这是哪/我的方位」触发定位授权 |
| R3-4 | 「今天世界杯」OK，追问「明天的呢」卡死 | 单步流式直通无整体超时；跟进句 `raw_text`「明天的呢」无赛事名 → 不路由 sports → 落慢合成（旧 timeout 25s，体感卡死） | `_maybe_sports/_sports` 用 **query 槽位 + raw_text 组合**识别赛事与日期（跟进句路由到快 sports）；合成 timeout 25→20s 收敛 |

> **环境注意（#2a 天气定位优先）**：`navigator.geolocation` 需**安全上下文（HTTPS 或 localhost）**——用局域网 IP（如 `http://192.168.x.x:5173`）访问 HMI 时浏览器禁用定位，坐标取不到 → 天气退回问城市。定位类功能验证请用 `localhost` 或为 HMI 配 HTTPS。

## 10. 四轮：新闻改为「编号速览列表」（2026-06-22）

实测反馈：「今天有哪些值得关注的新闻」给的是**很短的气泡摘要 + 需点开的标题卡**，不符合座舱看新闻诉求。座舱诉求 = 一屏扫到约 10 条带一句话摘要的列表。

改造（`_news`）：
- 取 ~10 条（无 topic limit=10；Exa recency 2 天凑覆盖面 + 标题去重）。
- **一次 LLM 调用**产出 `{overview, summaries{编号:一句话}}`（`_summarize_news_list`）——逐条一句话摘要只依据该条正文，不编造、不张冠李戴；失败回退该条首句。
- **气泡 = overview 一句总览**；**`news_brief` 卡 = 编号 1~N 列表**，每条「标题（可点开原文）+ 一句话摘要（直接可见）+ 来源·时间」，HMI `<ol>` 计数徽标，显示全部 ~10 条（>10 才折叠）。
- 与"卡片不复读结论"不冲突：气泡是总览、卡片是逐条速览，二者不重复。
