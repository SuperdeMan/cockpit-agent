# WS6 补充设计：搜索 / 新闻结果呈现重设计 —— 从"罗列链接"到"直接结论"

- **状态**：草案（2026-06-20）
- **交付对象**：info Agent 开发者 + HMI 前端开发者
- **关联代码**：`agents/info/src/agent.py`（`_search`/`_news`/`_summarize_sources`）、`hmi/src/components/Cards.tsx`（`SearchCardView`/`NewsCardView`）、`agents/info/src/providers/base.py`（`SearchResult`/`NewsItem`）
- **关联文档**：`docs/guides/provider-integration.md`、`docs/design/2026-06-20-info-agent-expansion.md`

---

## 1. 现状与问题

### 1.1 当前实现

Agent 层**已有** LLM 合成逻辑（`agent.py:251-295` `_summarize_sources`）：
- 拿到搜索/新闻原始结果后，喂给 LLM 生成连续文本摘要
- 用 `_LIST_MARKER` 正则剥离列表格式
- speech 输出已是连续文本（不是列表）

**但 HMI 卡片仍然展示完整结果列表**（`Cards.tsx:145-161` `SearchCardView`、`:116-141` `NewsCardView`），把所有 title/url/snippet 逐条渲染——这才是用户感知的"罗列一堆链接"。

### 1.2 用户感知的问题

> "联网搜索给到的结果应该都是直接的结果"
> "目前给出的还是搜索的列表，agent/LLM 的本质是能总结结果给到用户"

问题不是 Agent 话术（已改），而是**卡片没有同步改为结论式**。

### 1.3 问题拆解

| 层 | 现状 | 问题 |
|---|---|---|
| **Provider** | AnySearch/SerpApi 返回 `{title, url, snippet}` 列表 | 数据本身没问题，是原材料 |
| **Agent 话术** | `_summarize_sources` LLM 合成连续文本 | ✅ 已修复（speech 是结论） |
| **Agent ui_card** | 仍传完整 `items[]` 列表 | 卡片展示的仍是列表 |
| **HMI 卡片** | `SearchCardView`/`NewsCardView` 逐条渲染 items | 用户看到一堆标题+链接，体验差 |

## 2. 目标

**搜索/新闻的核心交互范式：Agent 给结论，卡片给补充。**
- 语音播报（speech）= 连续结论文本（✅ 已实现）
- 主卡片 = **摘要卡**（结论 + 关键数据点，一屏可读）
- 来源/详情 = **可展开的来源列表**（折叠状态，需要时点开看原始链接）

## 3. 方案

### 3.1 Agent 侧改造（`agent.py`）

**搜索 `_search`**：
```
speech: LLM 合成的连续结论（已有）
ui_card:
  type: "search_answer"（新类型，区别于旧 search_list）
  answer: str          # LLM 合成的结论文本（与 speech 相同）
  sources: list[...]   # 来源列表（折叠展示，非主视觉）
  query: str
```

**新闻 `_news`**：
```
speech: LLM 合成的连续结论（已有）
ui_card:
  type: "news_digest"（新类型，区别于旧 news_list）
  summary: str         # LLM 合成的摘要
  headlines: list[...] # 精简头条（最多3条，仅标题+来源，无 snippet）
  topic: str
```

**设计原则**：
- `speech` 和 `ui_card.answer/summary` 内容一致（语音=视觉，不矛盾）
- 卡片主视觉是**结论文本**，不是列表
- 原始来源降级为辅助信息（折叠/底部小字）

### 3.2 HMI 卡片改造（`Cards.tsx`）

**新 `SearchAnswerCard`**：
```
┌─────────────────────────────────┐
│ 🔍 世界杯赛程                    │  ← query
│                                 │
│ 巴西3-0海地，摩洛哥1-0苏格兰，   │  ← answer（LLM 结论，主视觉）
│ 美国2-0澳大利亚。               │
│                                 │
│ ▸ 3 条来源                      │  ← 可展开的来源
└─────────────────────────────────┘
```

展开后：
```
│ 1. Sofascore - 世界杯2026实时比分  │
│ 2. LiveScore - 足球即时比分        │
│ 3. FIFA - 官方赛程                 │
```

**新 `NewsDigestCard`**：
```
┌─────────────────────────────────┐
│ 📰 今日热点                      │  ← topic
│                                 │
│ 6月20日多条投资舆情引发关注，     │  ← summary（LLM 摘要，主视觉）
│ 科技板块与新能源车相关消息较多。   │
│                                 │
│ · 6月20日新闻早知道              │  ← 精简头条（仅标题）
│ · 今日投资舆情热点               │
│ · 科技板块消息汇总               │
└─────────────────────────────────┘
```

### 3.3 渐进迁移策略（向后兼容）

- Agent 侧：新 `type: "search_answer"` / `"news_digest"`，旧 `search_list` / `news_list` **保留不删**（其他场景可能用）
- HMI 侧：`CardRenderer` 新增两个 case，旧 case 保留
- 迁移期间两种卡片类型共存，无 breaking change

## 4. 分阶段落地

- **P0（立即）**：Agent `_search`/`_news` 的 `ui_card` 改为新类型 + HMI 新卡片组件。改造量小，效果立竿见影。
- **P1**：旧 `search_list`/`news_list` 卡片标记 deprecated；HMI 统一到结论式。
- **P2**：搜索结果的质量闭环——收集用户反馈（"这个回答有用吗"），反馈到 provider 选择和 LLM prompt 优化。

## 5. 验收

- [ ] "今天世界杯赛程" → speech 是连续结论（不是列表）+ card 是 `search_answer`（结论在上、来源折叠）
- [ ] "今天热点新闻" → speech 是连续摘要 + card 是 `news_digest`（摘要在上、头条精简）
- [ ] 旧 `search_list`/`news_list` 场景不受影响（向后兼容）
- [ ] 无 LLM 时降级：speech 仍是前 2 条 snippet 拼接，card 退化为旧列表
- [ ] `pytest` 全绿 + HMI `npm test && npm run build` 通过

## 6. 风险

- **LLM 合成质量**：模型偶尔仍会用列表格式——`_LIST_MARKER` 正则已兜底，但需持续观察
- **延迟增加**：合成多一次 LLM 调用（~1-2s）——用 `temperature=0.2, max_tokens=260` 控制
- **卡片信息密度**：摘要卡可能太简略——来源折叠是平衡点
