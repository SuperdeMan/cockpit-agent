# 数据真实性治理：禁静默回退 mock + 卡片 provenance 契约

- **状态**：落地中——P0 + P1 已落地并真栈验证（2026-07-17，见 §8）；P2（严格闸 + 泄漏探针 + 其余卡族推广）待做
- **交付对象**：后续实现者（人 / AI agent）
- **关联**：各 Agent `src/providers/__init__.py` 工厂、`llm-gateway/llm_runtime.py`、`agents/_sdk/result.py`、`agents/_sdk/server.py`、`orchestrator/cloud/aggregator.py`、`hmi/src/types.ts`、`docs/conventions.md` §9、`docs/guides/provider-integration.md`、`test/e2e_real_providers.py`
- **姊妹篇**：[2026-07-17 多模型运行时硬化](2026-07-17-llm-runtime-hardening.md)（「pin 住的请求绝不静默漂移」与「真实数据绝不静默变假」是同一条原则的两面）

## 0. 缘起：外部建议逐条判定

| # | 建议 | 现状 | 判定 |
|---|---|---|---|
| 1 | 生产模式禁止静默回退 mock | 全部 provider 工厂「构造失败回退 mock（不阻断 PoC）」；AGENTS.md 铁律第 47 行专门警告「不得直接以 deploy/docker-compose.yaml 启动，否则真实 Provider 可能静默回退 mock」——**文档规则，无 runtime 强制** | **采纳，改形为三层**（D2）：①「显式 real 意图 + 构造失败 → fail-fast」默认开启；②`REQUIRE_REAL_PROVIDERS` 严格栈开关（默认关，CI mock 车道零破坏）；③启动期 provider 决议统一 log 可审计。「生产模式」一词改为「严格栈」——本项目尚无量产环境，mock 离线跑通是 CI 的**特性**（R3.3 nightly 零 secrets 依赖它），要治的是「静默」不是「mock」 |
| 2 | 所有卡片携带 真实/缓存/降级/模拟 标记 | 无统一标记。ui_card 是自由 Struct（`agent.proto:76`），聚合器整卡透传（`aggregator.py:94-110`）——**加保留键零 proto 改动**，且已有 `_escalate` 保留键先例（conventions §9.1） | **采纳**（D1）：ui_card `_prov` 保留键 + HMI 统一徽章。「所有卡片」改为「试点 3 族→分批推广」——23 个卡族一次性铺是机械劳动堆积，先钉契约 |
| 3 | 来源和更新时间 | 部分已有且更细：search 卡 sources/freshness/confidence（`types.ts:145-148`）、weather 卡 `update_time`（`types.ts:221`）、news 逐条 source/publish_time——「卡片给证据」是 2026-06-22 信息卡重设计的既定原则 | **采纳并入 `_prov`**：`vendor`+`fetched_at` 统一兜底；已有更细字段的卡不动（不搞两套时间）。本建议本质是把 info 域的诚实先例推广为全域契约 |

**本项目已有的诚实基因**（本规划是推广不是引进）：搜索接地合成的「诚实弃权」、记忆无 embedding 时「诚实降级 lexical」（`.env.example:48`）、赛事赛季回退标注、nearby「缺字段不编造」、journey 记分卡的「诚实率」维度。缺的只是**结构化、机器可查**的表达。

## 1. 现状与证据：mock 面全景盘点

| 域 | 工厂/入口 | 静默回退点 | 分类（P0 须逐项核实） |
|---|---|---|---|
| info·天气 | `agents/info/src/providers/__init__.py:33-55` | vendor=qweather 或有凭证但**构造失败** → mock | 假数据 |
| info·搜索 | 同上 `:58-87`（Exa→AnySearch→Bing→mock） | 逐级 init 失败静默降级 | 假数据（链内降级=degraded） |
| info·新闻/赛事/股票 | 同上 `:90-162` | 同模式 | 假数据 |
| navigation POI | `agents/navigation/src/providers/__init__.py:11-22` | 同模式 | 假数据 |
| nearby / charging_planner | 各自 `src/providers/__init__.py` | 同模式 | 假数据 |
| parking_payment | `src/providers/` | 支付通道 mock | **设计即模拟**（payment-gateway PoC 契约如此；parking.find 重复 mock 已于 07-07 删除） |
| manual_rag | `src/providers/mock.py` | 本地语料检索 | 待分类（若语料=真实手册文本，属「本地真数据」非造假） |
| LLM | `llm_runtime.py:150-154` | 全部无 key → MockProvider 且**成为 active** | 假数据（mock 话术最具欺骗性） |
| embedding | `llm_runtime.py:84-99` | 无 key → MockProvider 伪向量 | ✅ 已核实无害：维度探测挡住伪向量 → lexical（见下方落地核实 ①） |
| ASR/TTS | `.env.example:57-90` `auto` 链 | 全链不可用 → mock | 假数据（但有「auto 桥接」缓解） |

> **落地核实（2026-07-17 P0）**：① embedding 疑点解除——MockProvider 伪向量固定 384 维
> （`llm-gateway/providers.py:63`），memory `_probe_embedder` 按 `EMBED_DIM`（默认与 compose
> 均 1024）做维度对齐探测，不符即忽略该源、诚实降级 lexical（`memory/pg_store.py:165-186`），
> `.env.example:48` 的承诺成立；残余边缘=显式把 `EMBED_DIM` 设成 384 会让伪向量撞维度，
> 留给 P2 严格闸覆盖。② 盘点漏项：news 存在**运行期** mock 回退（`_news_from_provider`
> 真实源失败改喂 MockNewsProvider 假头条）——P0 已删除，改为诚实空列表，与
> weather/alerts/stock 既有口径对齐。

**事故背书**：①AGENTS.md:47 的 compose 起法铁律本身就是一次静默回退事故的疤痕；②07-14 场景编排真栈首跑抓到「mock LLM 盖住 prompt 缺陷」——mock 不但骗演示，还骗测试；③journey 体系专设「诚实率」记分维度，说明真实性已是本项目的显性质量目标。

## 2. 问题

1. **「栈起来了」≠「栈是真的」**：compose 起法错、凭证损坏、镜像缺包，任何一种都无声变 mock，靠人肉看数据「不太对劲」才发现。机器可判定的事实（provider 决议结果）目前只活在 warning 日志里。
2. **卡片无 provenance**：演示/评审/badcase 排查时无法一眼区分真实、降级、模拟；dashboard 轮次详情里的 ui_card JSON 同样无此信息。
3. **降级路径只活在话术里**：赛季回退、薄证据、lexical 召回等降级发生时，话术有标注但无结构化标记——记分卡「诚实率」只能靠断言话术措辞，脆弱。

## 3. 目标与非目标

**目标**：① 显式要求真实数据的栈，任何 mock 决议都在启动期炸响或被标记，绝无静默；② 外源数据卡片带机器可查的 provenance（模式/来源/取数时间）；③ CI 全 mock 车道零破坏。

**非目标**：不改 proto（Struct 够用）；不做话术级「本数据为模拟」免责播报（徽章足矣，别污染语音 UX）；不一次性改造全部 23 卡族；不删除 mock provider（它们是 CI 与离线开发的合法公民，问题在「静默」）。

## 4. 方案（决策卡）

### D1 `_prov` 保留键契约（conventions §9 新增小节登记，仿 `_escalate` 先例）

```jsonc
// ui_card 顶层保留键（card_group 时打在成员卡上）
"_prov": {
  "mode": "real" | "cached" | "degraded" | "mock",
  "vendor": "amap" | "qweather" | "exa" | "api-football" | "tushare" | "mock" | ...,
  "fetched_at": "2026-07-17T10:30:00+08:00",   // 数据获取时刻，非渲染时刻
  "note": "赛季回退 2024/25"                    // 可选，degraded/cached 时说明原因或缓存龄
}
```

- `degraded`=真实数据但经降级路径（备选 vendor、赛季回退、薄证据、lexical 召回）；`cached` 当前**无生产者**（栈内尚无数据缓存层），纳入词表做前向兼容，禁止无缓存装缓存。
- 生产点：各域 provider 基类加只读属性 `vendor_id` / `is_mock`（mock 子类置 true）；新建 `agents/_sdk/provenance.py::attach(card, provider, mode="real", note="")` 一行盖章。SDK **不自动盖**（`_to_struct` 收口处不知道 provider 是谁），靠 conventions 约定「凡展示外源数据的卡必须带 `_prov`」+ 契约测试兜底。
- 已有细粒度字段（search sources/freshness、weather update_time）保留，`_prov` 是兜底不是替代。

### D2 禁静默回退（三层，层1/层3 默认开）

- **层1 显式意图 fail-fast（默认开启，无开关）**：工厂判定「显式 real 意图」——vendor env 显式非 mock（`POI_VENDOR=amap`、`WEATHER_VENDOR=qweather`）或该域唯一凭证 env 非空（`EXA_API_KEY` 等「配了 key 即意图」）——此时**构造失败改 raise**（容器启动即炸、日志可读），不再回退 mock。`.env.example` 默认全 mock/空 → 无凭证的本地开发与 CI 永不触发。凭证在但上游 4xx/超时等**运行期**失败不在此列（走既有 FAILED 诚实话术，不是造假）。
- **层2 严格栈开关**：`REQUIRE_REAL_PROVIDERS=on`（默认 off）——任何工厂返回 mock 即 raise，含 LLM runtime 落 mock active、ASR/TTS auto 链落 mock。豁免清单 `REQUIRE_REAL_EXEMPT`（默认 `parking-payment`：支付模拟是产品事实，其卡照标 `_prov.mode="mock"`——诚实 + 不挡演示）。
- **层3 启动可见性（默认开启）**：每个工厂决议输出统一格式一行日志 `provider[weather]=qweather(real)` / `provider[poi]=mock`，全栈 `docker compose logs | grep "provider\["` 一屏审计。可选加一条 obs 事件供 dashboard 展示（P2，低优）。

### D3 HMI 统一徽章

`types.ts` 加 `Provenance` 类型（各卡 `_prov?: Provenance`，用交叉类型免逐卡手写）；卡片容器统一渲染（无统一容器则加薄 wrapper）：
- `mock` → 醒目琥珀徽章「模拟数据」（演示时一眼识破）
- `degraded` → 灰徽章「降级 · <note>」
- `cached` → 「缓存 · N 分钟前」
- `real` → 不打扰：footer 小字 `来源 · 更新时间`（已有同类自有字段的卡不重复渲染）

### D4 验证网

- **契约测试**：试点 Agent 单测断言出卡带 `_prov`；SDK 层断言 `_to_struct` 与 engine 侧 `MessageToDict` round-trip 不丢 `_prov`（历史坑：ui_card 跨 Agent 透传须 MessageToDict）；聚合器 card_group 成员保留 `_prov`。
- **e2e**：`test/e2e_real_providers.py` 扩两个场景——①严格栈冒烟：`REQUIRE_REAL_PROVIDERS=on` 起栈 → 全服务健康 + 抽 3 张卡 `_prov.mode=="real"`；②mock 泄漏探针：严格栈上任意轮次出现 `mode=="mock"`（豁免域除外）即红。
- **文档 ripple**：`docs/guides/provider-integration.md`（接真实 provider 常青指南）补两步：声明 `vendor_id`/`is_mock`、出卡处 `attach()`；conventions §9 登记 `_prov`；`.env.example` 注释新 env。

## 5. 分阶段落地

| 阶段 | 内容 | 预估 | DoD |
|---|---|---|---|
| **P0** | D2 层1+层3 + §1 盘点表逐项核实（含 embedding 伪向量疑点）+ conventions 登记 `_prov` 契约 | 半天 | 工厂 fail-fast 分支单测（显式意图×构造失败→raise；无意图→mock 照旧）；CI mock 车道全绿（nightly 子集不受影响）；全栈启动日志 grep 出全部 provider 决议 |
| **P1** | D1 生产点 + D3 徽章，试点 3 族：weather / place_list（nearby 真高德）/ search_result | 1 天 | 试点契约测试 + round-trip 测试绿；真栈 CDP：真实卡见来源/时间角标、把 AMAP_KEY 改坏起 mock 栈见琥珀徽章 |
| **P2** | D2 层2 严格栈开关 + D4 e2e 两场景 + 其余卡族分批推广（poi_list/sports/stock/news/charging/trip…） | 1 天（推广可再分批） | `REQUIRE_REAL_PROVIDERS=on` 起栈冒烟过；泄漏探针进 `make e2e` 清单 |

## 6. 验收

```bash
pytest agents -q -k "provider or prov"            # 工厂 fail-fast + _prov 契约
docker compose up -d && docker compose logs | grep "provider\["   # 层3 决议审计
REQUIRE_REAL_PROVIDERS=on docker compose up -d    # 层2：缺凭证服务启动即炸（预期行为）
python test/e2e_real_providers.py --strict        # 严格栈冒烟 + 泄漏探针
```

## 7. 风险与开放问题

- **fail-fast 误伤**：判定必须是「显式意图」而非「有任一 env」；`.env.example` 默认值保证默认路径零触发。风控点=有人配了半套和风 JWT（有 project_id 没私钥）→ 按层1 语义就该炸（配了即意图），启动日志须把缺哪个 env 说人话。
- **`_prov` 键在链路被丢**：ui_card 经 Struct↔dict 多跳（agent→engine→aggregator→gateway→HMI），D4 round-trip 测试钉死；聚合器已核实整卡透传（`aggregator.py:94-110`）。
- **徽章视觉**：用现有 Aurora Glass token 直接做小徽章，不开 Figma 回路（成本不对称）；若泓舟后续要精修再走设计。
- **embedding 疑点**（已闭）：P0 核实为无害——memory 维度探测（384≠1024）已把伪向量挡在召回之外，无需改动；仅当显式 `EMBED_DIM=384` 时会撞维度，P2 严格闸（`REQUIRE_REAL_PROVIDERS=on` 禁 embed mock）覆盖该边缘。
- **LLM mock 的边界**：层2 管住「mock 成为 active」；但**不**给 chitchat 话术打 `_prov`（LLM 生成内容不是「外源数据」，语言本身无真值可标——搜索/新闻卡的证据链已由 sources 字段承担）。

## 8. 落地记录

- **P0 ✅（2026-07-17 当日，随规划批准即落地）**：`agents/_sdk/provenance.py`（`fail()` +
  `log_resolution()`）+ 六份工厂改造（info×5 / navigation / nearby / charging_planner /
  manual_rag / parking_payment）+ news **运行期** mock 回退删除（§1 落地核实 ②）+
  conventions §9.3（`_prov` 契约）/§9.4（决议契约）登记 + 25 条工厂契约单测（每域：默认
  env 全 mock 不炸回归 + 显式意图×缺凭证/构造失败/未接入 vendor → raise）。
- **验证**：全量 pytest **1661 passed / 7 skipped** 零失败；真栈重建 8 容器后
  `docker compose logs | grep "provider\["` 一屏 10 域决议——weather=qweather、search=exa、
  news=serpapi、stock=tushare、sports=api-football、poi/place/charging=amap 全 real，
  knowledge/parking=mock，与 .env 凭证事实一致。
- **实现期发现**：① §1 盘点表最初漏了 news 的**运行期**回退（`_fallback_news`）——工厂盘点
  只看构造期是盲区，运行期口径已并入 §9.4 契约；② TODO 型 vendor（baidu/pgvector/etcp）
  显式指定时同样 fail-fast 说清「未接入」，不再静默装聋。
- **P1 ✅（2026-07-17 当日）**：`provenance.attach()`（provider 章/字符串源两用、card_group
  成员盖章、degraded/cached 显式传 + note）+ **全部工厂决议点盖来源章**（`log_resolution`
  扩 provider 参数——决议=日志+章一处收口，P2 其余卡族推广就绪）+ 试点三族出卡盖章
  （weather / search_result / place_list·place_detail）+ HMI `ProvBadge`（mock 琥珀醒目
  「模拟数据」/ degraded·cached 灰标 / real 不打扰小字「来源 · 取数时间」角标）+ types.ts
  `Provenance` 契约；**顺带删 nearby 运行期 mock 回退**（search/detail 真实源失败改诚实
  FAILED 话术——假餐厅可能被用户导航过去，代价不对称；news 之后的同类第二例，运行期
  盲区自此双双清零）。
- **P1 验证**：+5 `_prov` 契约单测（试点卡带章 / Struct↔dict 往返不丢键 / card_group 成员章 /
  degraded+note）+3 nearby 诚实降级单测；真栈 WS 探针（重建 llm-gateway/info/nearby/hmi 后）：
  天气卡 `_prov={mode:real, vendor:qweather}`、周边卡 `_prov={mode:real, vendor:amap}`
  全链路到端（agent→engine→聚合→网关→WS）；HMI `vite build` + node 例全过。
