# 商户链路与 HMI 收口（demo-mkemhn 十九轮全量复盘）

- **状态**：实施中（2026-08-13，泓舟指示「系统性优化麦当劳/瑞幸 MCP + HMI 交互」）
- **素材**：会话 `demo-mkemhn`（2026-08-13 16:04–18:33，19 轮），逐轮 trace 已拉取
  span/llm_calls/logs 级证据；配合全仓代码走读（merchant workflow / executor 锚定 /
  planner 校验 / aggregator / nearby / HMI）
- **关联**：`2026-08-13-cross-turn-store-anchor.md`（锚定不变量，本批不放松）、
  `2026-08-12-merchant-mcp-full-flow.md`、conventions §9.9/§9.17

---

## 1. 逐轮现象登记（问题轮，trace 前 8 位）

| # | 时刻 | 用户说 | 系统表现 | 定性 |
|---|---|---|---|---|
| 3 | 16:04 | 看看这家的菜单 | 答**演示商户**菜单（拿铁 22 元起）；焦点明明是瑞幸创智天地大厦店 | 落域错（shop.menu 抢走）`c4a82439` |
| 4 | 16:52 | 帮我看下附近的瑞幸咖啡，我要点一杯 | 掉 chitchat→escalate 网搜→报出**上海**门店列表，还建议用户「用高德 App」 | 计划被丢+兜底链放大 `f868a7ce` |
| 5 | 16:58 | 我要在附近的瑞幸点一杯咖啡，帮我看下菜单 | 出候选卡，但话术「为您找到 10 家…推荐…，…。找到多家可能的瑞幸门店，请选择其中一家。」冗长且带截断省略号 | 挂起前缀拼接缺陷 `a3063711` |
| 6 | 18:01 | 选择瑞幸门店：深铁金融科技大厦店（候选卡按钮文本） | 「已选定。我现在去获取这家店的**公开 POI 坐标**，获取成功后再帮你做后续操作」——之后什么都不会发生 | 焦点/草稿双过期→拒绝，聚合 LLM 编了个承诺 `78b635db` |
| 7 | 18:01 | 查询附近的瑞幸咖啡 | 「已为您找到 10 家瑞幸门店，请选择其中一家。」**无列表无卡片** | 计划被丢→chitchat 凭历史编造 `3650e2b5` |
| 8 | 18:02 | 你没有列出门店 | chitchat 从历史里抄出跳号列表（1,2,4,5,7…） | 同上的连带 `8ce6c46c` |
| 9 | 18:03 | 深铁金融科技大厦店这家瑞幸咖啡，我要看看菜单 | 「请先查询附近的瑞幸门店并选择一家，我只会使用该公开门店 POI 的坐标。」 | 指名门店无法恢复可信链+术语外泄 `b69a16d9` |
| 10 | 18:05 | 帮我看看附近的麦当劳，我想看看菜单 | 高德 place_around 超时×2，诚实降级；mcd.menu 用默认店兜住 | 环境（本机高德间歇不可达，§4.0 已记）`ba3908fd` |
| 14 | 18:27 | 蘸酱麦辣大四角的热量是多少 | 「没查到这个品项」——它刚出现在菜单和订单预览里 | 营养表≠菜单 + 3000 字符盲截断 `c47671f5` |
| 15 | 18:29 | 帮我看看我附近最近的瑞幸咖啡的菜单 | 报出**北京什刹海店**并声称「离得最近」 | 位置丢失→place_text 全国检索冒充附近 `59b34983` |
| 16 | 18:32 | 我想在最近的瑞幸点一杯埃塞瑰夏冷萃 | 北京店订单预览；话术里「瑞幸咖啡(什刹海新…。」截断残句 | 同上 + `_first_sentence` 60 字硬截 `cffc84fd` |
| 17 | 18:33 | 我想要换一个 | chitchat 编造「为您重新推荐…请确认：前门大街店…应付 10.90 元」——**不存在这笔待确认单** | planner 两次 no-action→chitchat 幻觉出交易话术 `2fd09d52` |
| 18 | 18:33 | 我附近的，不是北京哦 | 「你想了解这家瑞幸店的什么？」 | 首次规划已读对（「用户否定北京需先定位」），retry 反而降级成错误 clarify `4a4f3b1e` |
| 19 | 18:33 | 看看我附近瑞幸的菜单 | 清华清芬园店（还是北京）冒充「附近」 | 同 15 `44943f00` |

当前 `/api/vehicle/state` 实测 `location: null`——HMI 浏览器定位刷新失败后
`locationEnabled` 被静默置 false（`hmi/src/App.tsx:804/813`），此后每轮请求都不带
`current_lat/lng`。

## 2. 根因归并（8 条，按层）

1. **nearby 无位置时不诚实**：`providers/amap.py:122-126` 无位置走 `/v5/place/text`
   全国检索，`agents/nearby/src/agent.py:368` 话术仍说「为您找到 N 家」，从不区分
   「附近」与「全国」。北京店三连（#15/16/19）全由此来。用户纠错（#18）也无从生效——
   系统根本不知道自己错在哪。
2. **planner 线契约过严 + 弱模型**：`planning.py:1796` 要求步骤字段与
   `_PLANNER_STEP_FIELDS` **精确相等**，MiniMax 常漏 `slot_refs`/`depends_on`（缺省语义
   本就无歧义），整份计划被丢→重试再失败→chitchat 兜底（#4/#7 直接由此产生）。
3. **chitchat 兜底会编造交易事实**：掉进 chitchat 的轮（#7/#17），模型凭对话历史生成
   「已找到 10 家门店」「请确认订单」这类**它没有能力兑现**的话术。
4. **聚合 LLM 会替系统许诺**：`_AGGREGATE_SYSTEM`（aggregator.py:67）没有任何诚实约束，
   把两条拒绝改写成「我现在去获取…获取成功后再帮你做后续操作」（#6）。
5. **门店锚定的三处工程缺口**（不变量本身不动）：
   a. `_anchor_store_from_focus` 对**所有**步骤生效（设计文档 §4.3 明写「本步 intent
      属于声明了门店槽的商户 workflow」），实测把门店三槽注进 `chitchat.talk` 和
      `nearby.search`（#17/#19 日志）；
   b. `last_places` 经 update_focus 粘性接力后**永不过期**（设计文档说「TTL 过期即失效」，
      接力让它跨任意时长）；
   c. 焦点（300s）与选店草稿（600s）双过期后，指名门店/候选卡按钮全部走进
      「请先查询附近的瑞幸门店」死路（#6/#9），拒绝话术还带内部术语「公开 POI 坐标」。
6. **挂起前缀硬截断**：`progress.py:_first_sentence(speech, 60)` 逐字符截；nearby 的
   卡是 `place_list`，不在 `result_summary` 安全计数分支里（只认 `poi_list`），于是
   「瑞幸咖啡(什刹海新…。」这种残句直接进 TTS（#5/#16）。
7. **mcd.nutrition 无确定性防线**：营养表与当店菜单是两个数据源，商品名两套；
   `_readable_speech` 把 `(text+json)[:3000]` 盲截后交 LLM 自由作答——条目在 3000 字外
   就是「没查到」（#14）。
8. **知识/范例缺口**：候选卡按钮文本「选择瑞幸门店：X」、指名门店要菜单、纯发现句
   「查询附近的瑞幸咖啡」在范例库全无覆盖；`merchant-ordering` guide 还留着
   「营养→`mcd.menu`」的过期口径（2026-08-13 已改名 `mcd.nutrition`）。

## 3. 修法（六批）

### 批A 编排层（orchestrator/cloud）

- **A1 planning 步骤字段归一**：缺 `slot_refs`/`depends_on`/`slots` 三个容器键时按
  空值补齐（缺省语义唯一，不是猜测）；**多出的未知键仍整份拒绝**（那才是要挡的
  「模型自造语法」）。日志留痕 `normalized missing step fields`。
- **A2 progress 安全计数补 `place_list`**：`已找到 N 个地点`，挂起前缀不再逐字符截
  门店名。
- **A3 aggregator 诚实约束**：system prompt 增加「失败/拒绝如实转述；不得替系统承诺
  任何『接下来会去做』的动作（本轮不会再做任何事）；不得编造步骤结果里没有的门店/
  订单/价格」。
- **A4 锚定门控**：planning `_validated_steps` 按 capability 声明为步骤盖章
  `_store_slots_declared`（slots 同时含三门店槽），executor `_anchor_store_from_focus`
  只对盖章步骤生效——机制化，零领域字面量。
- **A5 锚定时效**：`Focus.last_places_ts`（nearby.search 写入时间戳，接力原样携带），
  超过 `MERCHANT_STORE_ANCHOR_MAX_AGE_S`（默认 1800s）不再锚定——回到设计文档
  「『刚才那家』本来就有时效」的本意；缺时间戳按过期处理。

### 批B nearby 诚实降级

- 无车辆位置且无 location 槽时：**品牌/品类发现类检索不再全国搜**，诚实告知拿不到
  当前位置并引导（说位置/开定位）；**指名门店**（关键词含「(」或以 店/餐厅/门店 结尾）
  仍可按名检索，但话术说「按门店名找到」，不说「附近」。`data.center` 标注
  `vehicle|slot|none`，观测可查。

### 批C mcp_bridge

- **C1 拒绝话术去术语**：`luckin.py:1383` 改为用户语言（不出现 POI/坐标）。
- **C2 指名门店自愈（escalate 一跳）**：`_resolve_store` 无可信链但 slots 里有
  store_name 线索时，声明 `_escalate={intent: nearby.search, slots:{keyword:…}}`——
  engine 既有机制一跳改派，本轮先把门店列表真实取回（同时写回焦点），下一句锚定即可用。
  装配失败时保留诚实拒绝话术，无回归面。
- **C3 luckin.menu 匹配放宽对齐 mcd**：只读菜单路径补「商品名 ⊂ 整句」反向包含
  （`mcdonalds._menu_matches` 同款，下单路径不放宽）。
- **C4 营养查询相关性打包**：`_readable_speech` 的 material 不再盲截 3000——按用户
  问句与条目的字符 bigram 相关性优先打包，问句里点名的品项只要在数据里就必然进
  prompt；system prompt 补「没查到时列出名称最接近的一两条供确认」。
- **C5 过期注释修正**：`_reselect_store` docstring 中「中央闸会追加确定继续吗」与
  实现（`_refused` 免追加）矛盾，改齐。

### 批D 知识/范例（skills）

- luckin.yaml 增：候选卡按钮句式「选择瑞幸门店：X」→ `nearby.search(keyword=瑞幸咖啡 X)
  → luckin.menu(slot_refs items.0)`；指名门店看菜单同款；「换一家瑞幸门店，还是点X」→
  `nearby.search → luckin.order(item_query=X)`。
- mcd.yaml 增：「选择麦当劳门店：X」→ `mcd.menu(store_hint=X)`；「看看麦当劳(X)的菜单」
  → `mcd.menu(store_hint=X)`；「换一家麦当劳门店，还是点X」→ `mcd.order(item_query=X)`。
- nearby.yaml 增：「查询附近的瑞幸咖啡」→ `nearby.search`（纯发现边界，#7 原句）。
- merchant-ordering guide：营养口径改 `mcd.nutrition`（golden 同步）；补「『这家』
  指对话焦点门店：焦点是瑞幸/麦当劳门店时用对应真实商户能力，shop.* 仅演示商户」。
- servers.yaml `shop.menu` description 补排除条款（与 shop.order 同款：真实品牌不适用）。
- chitchat `_system` 增守则：不得声称已执行/已查询任何操作、不得输出「请确认」类
  交易话术——兜底轮的幻觉面收掉。

### 批E HMI

- 订单预览卡（`merchant_order_preview`）补「换一家门店」chip：发
  「换一家{品牌}门店，还是点{首个商品}」——把 #17 那种模糊的「换一个」变成有确定
  语义的按钮（语音说「换一个」仍由批D/chitchat 守则兜住不再编造）。
- `place_list` 行内对瑞幸/麦当劳门店补「看菜单」按钮：发「看看{门店名}的菜单」，
  与批D范例闭环——发现→看单→点单三步全部可点按完成。
- 复用 `merchantUi.mjs` 纯函数层实现，`node --test` 同步补用例。

### 明确不做

- ❌ 不放松 cross-turn-store-anchor 的任何不变量（坐标仍只来自服务端持有的
  `nearby.search` 产出；名字只当线索）。
- ❌ 不把座舱 GPS 下发给商户 MCP（`context_scopes: []` 不动）。
- ❌ 不为「换一个」写 clarify 型范例（§4.2 已证：内容贫乏句检索不稳定，机制在但
  刻意零生产范例——按钮化是正解）。
- ❌ 不禁用 demo-coffee（e2e/CDP 依赖它验证写生命周期）；只做目录描述与知识边界。
- ❌ 不动 L0 对抗语料（上界 571 已用满；本批产物是范例与代码修复，不占语料额度）。

## 4. 验收

- 单测：mcp-bridge / orchestrator cloud / nearby / HMI node --test 全绿；
  eval_exemplars、eval_skills、能力完整性门禁 exit 0。
- 多轮判据（4.3 读数纪律）：「失败态之后再说一句」与 ≥3 轮场景在新增测试里显式覆盖
  （拒绝→指名门店→escalate 取回→锚定成功）。
- 真栈抽验：demo 会话重演 #6/#7/#9/#14/#15 五个形态。
