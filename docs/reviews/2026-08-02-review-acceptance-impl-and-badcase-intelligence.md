# 评审：验收报告实现结果核验 + badcase 三连的智能化评估与修复（2026-08-02）

> 两件事一份报告：① 核验 `2026-07-26-acceptance-review-m0a-m4.md` 的实现结果（13 张主卡
> 四批收口质量）；② 以泓舟标注的三个 badcase（`f53d2e58`/`c0d1a8d2`/`4799fb1f`）为标本，
> 评估「落域准确之外，系统的回答是否真符合人类预期」，并按项目铁律完成修复与真栈复验。

## 结论先行

- **① 13/13 张主卡收口为真**：逐卡核到 file:line 级实现 + 测试，抽跑 224 条相关测试全绿；
  未发现实现缺失、测试造假、判据失效。4 条低危发现（1 文档漂移 + 1 注释错位 + 1 措辞漂移
  已随本批修复；1 残余风险入账见 §1.2）。
- **② 三个 badcase 是同一个结构性缺口的三次显形**：「天气×去哪玩」是组合意图，而系统
  每一层的供给都是单域的。**落域全对（RoutingBench 口径下 `info.weather`/`nearby.search`
  都算「对」），回答全错**——这正是「落域准确 ≠ 智能」的标本价值。
- **修复全部走声明式供给，编排核心零改动**：guide（组合判据）+ exemplar（自含形态）+
  nearby agent（室内扇出 + 话术承接天气）+ manifest（`weather_context` 槽）。真栈复验
  三句原话全部翻正，冷启动组合链（adaptive 两轮：查天气→按结果推荐→聚合成一段自然
  回答）打通；guide golden live 车道 7/7（in-sample 3/3、holdout 4/4）。

---

## 1. 验收报告实现结果核验

### 1.1 核验矩阵（13 张主卡）

方法：通读验收报告 §7/§9/§10.2/§11.2/§12.2 → git log 定位四批收口提交 → 逐卡核实现
（file:line）与测试（含「M-A 式怀疑」：mock 是否盖断言 / 是否只断否定命题 / skip 是否
静默变绿）→ 抽跑关键测试。

| 卡 | 实现锚点 | 结论 |
|---|---|---|
| P1-① 记忆写侧说话人标注（M-B） | `memory/store.py:140-155,579-591`；`test_owner_sessions.py` 双向隔离断言 | PASS |
| P1-② profile.places occupant 维度（M-B) | `memory/store.py:318-415`（owner-scoped，非 primary 永不读 KV） | PASS |
| P1-③ MCP 订单查询/取消（M-D） | `agents/mcp_bridge/servers.yaml:54-78` + 幂等键查单 `src/agent.py:269-281` | PASS |
| P1-④ 主动×S2S + 深调研持久重投（M-C） | `proactive/delivery_store.py`（presented 才算完成）+ `gateway/edge/main.go:476-485` 补投 + `hmi/src/proactiveSpeech.mjs` 按档仲裁 | PASS |
| P1-⑤ Verifier FAILED 步查镜像改判（M-C） | `executor.py:261-294` 三边界；反向命题有测试（`test_verify.py:475` 确定失败不许翻案） | PASS |
| P1-⑥ journeys canonical 重跑机制化（M-A） | `run_e2e.py:1050-1166` 资格闸 + 陈旧检测 | PASS |
| P2-① 记忆面板 identity/删除语义（M-B） | `pg_store.py:508-531`（跨 owner 回 not_found 不泄露归属） | PASS |
| P2-② enroll 重名检查（M-B） | `schema.sql:115-119` partial unique + 存量规范形实时重算 | PASS（原子性=明确未做，判据今天仍成立） |
| P2-③ SKIP 第三态（M-A） | `run_e2e.py:1531-1541`：未声明的 skip 直接判 `skip_forbidden`——比卡面更强 | PASS |
| P2-④ 源码铁律 AST 白名单（M-A） | `e2e_contract.py:5975-5983` 从 manifest 动态取词表；guard 自身有测试 | PASS |
| P2-⑤ location 提醒 ttl（M-C） | 到地 15min / 到点 2h，`test_geofence.py:87-96` 断言严格更短 | PASS（机制形态与卡面不同，报告 §11.1 已明示归因） |
| P2-⑥ few_shots 空契约 | `skills.py:104,113,116` 已实装 | PASS（历史已修） |
| P2-⑦ toolcall provider 能力位（M-D） | `llm_runtime.py:171-176,311-321` 每请求现读 | PASS（机制在位、当前无档声明 False，dormant 属预期） |

抽跑：`test_owner_sessions/test_privacy/test_extract` 33 passed；`test_governor/test_delivery_store`
49 passed；`test_verify` 49 passed；`test_bridge/test_ledger` 73 passed；`test_toolcall`
20 passed；`proactiveSpeech.test.mjs` 10 passed。全量收集 3658 与账面吻合。

**总评：四批收口质量高于报告字面**——多数卡带反向断言与防退化源码锁（如
`test_ledger.py:435` 防「先查再插」回退、`test_bridge.py:687` AST 只查可执行常量）。

### 1.2 发现与处置（按严重度）

1. **DRIFT（已修，本批）**：架构文档 §7.2 主文仍写「S2S 进行中主动消息一律只出气泡」，
   而 M-C #4 后实际是按 `priority` 仲裁（critical 抢话 / user_contract 排队补播 / 其余
   气泡）。已按「校准加日期注」规则更新主文（changelog/conventions 本就已是新口径）。
2. **残余风险（入账不实现）**：`admission.py:123` 对 `compensate_tool` 仍只校验声明非空，
   「补偿工具必须自身被准入」的可达性校验未机制化——M-D 的修法是把 order.cancel 放进
   清单（个案）+ 测试钉死 demo-coffee 四 intent 集合（防复发）。§12.4 判据「校验的是
   声明，没人校验可达性」对未来新接入的 server 依然成立。**下一个 MCP server 接入时
   必须顺手机制化**（admission 通用校验：write 工具的 compensate_tool 必须指向同 server
   内已准入工具）。
3. **措辞漂移（已修，本批）**：验收报告 §10.2 未做表里的 `runtime/privacy_registry.py`
   其后已在 M-A 批落地——未做的是消费它的删除 saga，不是该文件。已在原文加更正注。
4. **注释错位（已修，本批）**：`admission.py` 中 compensate_tool 的行内注释挂在了
   confirm_prompt 字段上，已归位。

---

## 2. badcase 三连：智能化评估与修复

### 2.1 取证还原（会话 demo-e0n7go，2026-08-01）

| 轮 | 用户 | 落域 | 系统回答 | 问题 |
|---|---|---|---|---|
| f53d2e58 | 今天的天气适合去哪玩吗？ | info.weather | 「宝安区当前中雨，24℃…」 | 只报天气，没答「去哪」 |
| c0d1a8d2 | 这样的天气适合去哪玩啊？ | info.forecast | 「未来1天大雨转中雨…」 | 同上（用户已在重复提问） |
| 94fea4bb（未标注） | （同句重发） | nearby.search | 推荐碧海湾**公园**、红树林公园 | 落到推荐了，但雨天推户外 |
| 4799fb1f | 你确定下雨天还推荐我去公园吗？ | nearby.search | 推荐碧海湾公园、**彩虹沙滩**…10 家 | 被质疑后变本加厉 |

**关键证据**（obs spans + llm_calls）：

- planner 的 `goal` 两次都写对了（「根据今天天气推荐适合游玩的地点」「根据当前雨天天气
  推荐适合去玩的地方」）——**模型理解了问题，步骤却只拆出单域一步**。缺的不是理解，
  是供给。
- 四轮 planning span 的 `skills` 只有两条常驻 policy、`exemplars` 全空——面对组合意图，
  规划层手上零知识、零范例（nearby 域范例全是「找店/详情/订位」族）。
- 质疑轮 planner 其实**改对了方向**（`category=室内景点, keyword=雨天适合`），但 nearby
  agent 的类目表没有「室内」概念，子串扫描命中「景点」（`"景点" in "室内景点"`）→
  搜出去的还是户外公园——**计划被数据层背叛**。
- 单步计划的回答就是 agent 模板话术（「为您找到 N 家…」），没有任何承接「下雨」语境的
  机制（无 weather_context 槽）。

**定性：四层失因，每层单独看都「没错」，合起来对人就是「三次都没听懂」**——
① 规划知识缺（无「天气×去哪玩」组合判据）② 范例缺（该族零覆盖）③ 能力缺（类目表
无室内组）④ 话术缺（不承接天气前提）。落域指标测不出其中任何一层：f53d 落
info.weather 在单轮口径下甚至算「对」。

### 2.2 修复设计（全声明式，编排核心零改动）

按「路由错→hint / 知识缺→guide / 说法没见过→exemplar」三分法，这是**知识缺**为主、
能力缺为辅：

| 层 | 产物 | 内容 |
|---|---|---|
| 规划知识 | `skills/guides/weather-outing.yaml`（新） | 组合判据三分支：**已知天气**（对话里刚查过/话里自带）→ 单步 `nearby.search` 类目按天气选（雨雪雷高温→室内、好天气→景点/公园）+ `weather_context` 槽；**未知天气** → `complexity=adaptive` 先只出查询步、replan 按结果补推荐步（同 conditional-reminder 先例）；**质疑轮**是纠错不是闲聊→改推室内绝不原样再推户外。golden 7 条（含 4 holdout + 纯天气负例边界） |
| 范例 | `skills/exemplars/nearby.yaml` +2（#23/#24） | 只钉**自含天气词**的形态（「下雨天去哪儿玩好」「你确定下雨天还推荐我去公园吗」→ 室内+weather_context）。刻意不给「今天的天气适合去哪玩吗」投范例——范例表达不了 adaptive，投了会跟 guide 教的形态自相矛盾（P2「金标自相矛盾」同款坑） |
| Agent 能力 | `agents/nearby/src/agent.py` | ① 类目表补室内组（室内哨兵 + 商场/博物馆/科技馆/图书馆/游乐/KTV/温泉/水族馆…），**插入序修类目抢地盘**（设施类目在室内组前：「商场停车场」仍归停车；室内组在「景点」前：「室内景点」不再被子串抢走）；② `category=室内` 走**多类目扇出**（商场/电影院/博物馆串行检索——高德免费档 QPS 紧——交错合并保类型多样性，单类失败不整轮失败）；③ `weather_context` 话术承接（雨→「雨天不太适合户外，推荐附近这些室内去处：…」；好天气+户外→「天气不错，适合出去走走」） |
| Manifest | `agents/nearby/manifest.yaml` | slots + `weather_context`；desc 补「去哪玩」判据一句；examples +2。同时修了同族潜伏缺陷：「附近有什么商场」此前会退化成美食检索 |

### 2.3 复验证据（真栈，2026-08-02，深圳当日实况小雨）

**原句重放**（session replay-c75a70，宝安 GPS）：

| 轮 | 修复前 | 修复后 |
|---|---|---|
| 今天天气怎么样？（守门） | 纯天气 ✓ | 纯天气 ✓（不过度触发） |
| 今天的天气适合去哪玩吗？ | 只报天气 | 「**雨天不太适合户外，推荐附近这些室内去处：深圳前海壹方城、CGV影城(壹方城IMAX店)、宝安博物馆**，壹方城评分4.9」+ 混合类型 place_list 卡 |
| 这样的天气适合去哪玩啊？ | 只报预报 | 同上（对话内已知天气→单步室内） |
| 你确定下雨天还推荐我去公园吗？ | 再推公园+沙滩 | 改推室内三类 + 「雨天不太适合户外」承接 |
| 冷启动首句「今天的天气适合去哪玩吗？」 | —— | 「深圳宝安区今天有小雨，25℃左右，东北风2级，**不太适合户外活动。可以考虑去壹方城逛逛或者看场电影，宝安博物馆也是个不错的室内选择**」（adaptive 两轮：info.weather → replan 补 nearby.search → 聚合合成） |

**obs 归因**（每层都在设计位置起作用）：轮内已知天气 → `weather-outing@lex:27` 单步室内；
质疑轮 → guide **语义**检回（`@vec:0.66`，keywords 按设计不含「推荐/公园」）+ 范例
`nearby#24@lex:1.00`；冷启动 → `cx=adaptive`，t2.iter `replans:1, results:2`，aggregate
`path:adaptive`。

**门禁与回归**：nearby 单测 45（+7）；`eval_skills` 离线 10/10 召回、反例噪声无新增；
live 车道 golden 7/7（in-sample 3/3、holdout 4/4——含质疑轮、真 paraphrase、纯天气负例），
邻近 guide（conditional-reminder/trip/charging）无回归；`eval_exemplars` 214 条契约 OK、
域错配率 2.5%（4 miss 全部存量）。全量套件见 AGENTS.md §4.0 当前状态。

**标注资产**：三轮 badcase note 已回填修复记录；`4799fb1f` 标 gold=`nearby.search`
（自含语义可单轮评测）；f53d/c0d1 **刻意不标 gold**——它们的正确形态依赖对话上下文
（已知天气→nearby / 冷启动→adaptive 先查天气），扁平 gold_intents 表达不了，硬标会与
guide golden 自相矛盾（判定尺打架，P2 教训）。

### 2.4 判据入册（可复用）

1. **落域准确率测不出「没回答问题」**。三轮的落域在单轮口径下都「对」，但用户三次没
   得到答案。评「智能」要看**回答对问题的覆盖**，组合意图的尺子是「计划是否覆盖 goal」
   ——planner 自己写的 goal 就是免费的对照物（goal 说「推荐游玩地点」而 steps 里没有
   任何推荐步，这就是可检测的缺口信号）。
2. **「模型理解了但没这么做」的修法是给供给，不是换模型**。goal 全对、步骤全错，说明
   缺的是「这类问题的正确计划长什么样」的示范——投 guide/few-shot 后同一个弱模型直接
   做对（deepseek/minimax 双档复验）。
3. **计划对了还会被能力层背叛**：抽象类目（室内）在关键词检索面前会静默退化（子串命中
   「景点」），修法不是让 planner 换词，是给 agent 建「类目组→扇出」的真实能力。类目
   子串扫描的**插入序就是优先级**，新增类目必须推演既有句式的抢地盘面（「商场停车场」）。
4. **质疑轮是纠错信号，不是闲聊**：用户挑战「你确定…？」时，重复原答案是最坏回应。
   声明式修法=guide 教「改推+承接」，exemplar 钉该形态，agent 话术承接前提——三层
   都不需要编排核心知道「质疑」这个概念。
5. **上下文依赖的 badcase 不能标扁平 gold**（会与 adaptive 类知识的金标打架）——
   该族的评测载体是 guide golden（expect_complexity）不是 turns.gold_intents。

### 2.5 已知边界（明确未做，附判据）

- **好天气分支未经真栈复验**（当日深圳实况小雨，无法自然构造）：机制上走既有
  `category=景点/公园` 路径 + 「天气不错」承接话术（有单测），风险面小；下个晴天
  顺手看一眼即可。
- **室内扇出类目固定三类**（商场/电影院/博物馆）：刻意不做可配置——先让答案对，
  等 badcase 证据再谈个性化（如按乘员画像偏 KTV/展览）。
- **`weather_context` 依赖 planner 填槽**：不填只是少一句承接话术，推荐本身仍正确
  （fail-open，与 skill 层「软层写错=噪声」同姿态）。
- **aggregate 话术未做天气数字的强制携带**：冷启动聚合由 LLM 组织（实测自然带出
  「小雨，25℃」），不加确定性拼接——「系统持有的事实不交 LLM」适用于执行结果对账，
  不适用于本就来自工具结果的播报内容。
