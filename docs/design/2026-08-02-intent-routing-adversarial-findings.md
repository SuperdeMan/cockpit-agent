# 意图落域对抗测试发现清单（产品缺陷 + 修复批次记录）

> 日期：2026-08-02（首轮发现）／2026-08-03（修复批次收口 + 口径裁定期新发现）
> 状态：首轮发现**已修复并复验**，**逐条结论见 §5.5**（§5.4 的聚合数字有口径警告，
> 先读那一节的抬头）；首轮的 5 项残留在 §6（其中 6.1／6.2 已收口）；
> **2026-08-03 晚新增的产品侧账在 §7 —— 新会话先读那一节。**
> 来源：`test/eval_intent_adversarial.py` 发现轨（reference provider `minimax:MiniMax-M3`）
> 关联：规格 `docs/design/2026-08-02-intent-routing-adversarial-testing.md`、
> 评审 `docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md`（§10 起是新口径）、
> 运行手册 `docs/guides/intent-adversarial-testing.md`

本清单原本只登记**产品缺陷**。按实施计划的约定，建测试的那一批**不修生产路由**——
边建尺子边改被测对象，两边都说不清是谁变了。**修复批次（§5）是那一批的下半场。**

分类口径（设计 §11.5 / 实施计划 Task 16 Step 3）：

- `product_defect`：稳定复现的落域/入口/安全缺陷；
- `gold_error`：语料的 gold 写错了，改 gold 并降回 candidate；
- `capability_gap`：能力面本来就没有，落域再准也答不上来；
- `unstable`：三次结果分裂，**不登记为缺陷**；
- `infrastructure_error`：运行环境问题，修环境后重跑。

---

## 1. L0（零网络，确定性，一次红即结论）

L0 首跑 70 条证据单元、65 通过。5 条红灯全部定性为 `product_defect`。**5/5 已修复，L0 现 70/70。**

### 1.1 【高危】问功能被端侧当成指令执行

| 项 | 内容 |
|---|---|
| case | `ei.noise.question-about-control@l0`、`ei.noise.hypothetical@l0` |
| 原话 | 「这车的天窗最大能开多大」/「要是下雨了车窗会自动关吗」 |
| 期望 | `ingress=cloud`（是提问，不是指令） |
| 实际 | `ingress=edge_local`，**并且真的下发了车控动作** |
| 实测证据 | 「天窗最大能开多大」→ `state_delta={'sunroof': 'open'}`，1 个 `vehicle.control` 副作用；「车窗会自动关吗」→ 同样产生 1 个 `vehicle.control` 动作（state_delta 为空只是因为车窗本来就是关的） |
| 风险 | high。行驶中被误开天窗是真实安全问题，且用户完全没有下达指令 |

**判据**：端侧 `fast_intent` 认的是「对象 + 动作词」，疑问框架（「能……吗」「会……吗」
「最大……多大」）没有进入否决面。这一类不是落域偏好问题——**用户根本没有下指令**。

**✅ 已修**（`fast_intent.classify_structured`）：新增疑问/假设框架否决面，但**只盖控制类结果**。
这是本条修法的关键判据——「胎压是多少」「电量还有多少」「今天体感温度怎么样」都带疑问词，
一刀切会把好用的确定性秒回一起砍掉。**问的是「提问会不会被执行成写操作」，不是「这句话像不像问句」**。
收口放在 `classify_structured` **出口**而不是散在 30 个 return 上：判据是「结果是不是写操作」，
只有出口知道。方式/原因疑问词（怎么/如何）沿用 `_is_env_temp_query` 已有的同一条判据
——**带操作动词就仍算指令**（「温度如何调高」仍归空调），两处判据必须是同一条，
否则同一句话在两条路径上会得到相反结论。

### 1.2 本地 + 在线组合没有被拆开（漏接）

| case | 原话 | 期望 | 实际 |
|---|---|---|---|
| `ei.mixed.volume-reminder@l0` | 音量调小一点，提醒我八点开会 | `mixed` | `cloud` |
| `ei.mixed.seat-charging@l0` | 打开座椅加热，再找个充电站 | `mixed` | `cloud` |

对照组「打开空调并查一下天气」→ `mixed` 是通的，说明混合拆分机制在，只是这两类
子句没被 `split_and_classify_any` 认出来。风险 high 的原因不是错，而是**端侧秒回退化
成整句上云**：断网时这半条本地指令也跟着失效。

**✅ 已修**（真根因不在 `split_and_classify_any`，在 `server._group_mixed_intents`）：
拆分本来就拆对了，是**分组**把两个片段粘成了一组。旧实现对**所有** `_needs_cloud` 片段
一律附着到前一组——但那条规则本来只该服务**续接片段**（「周杰伦的」贴着「播一首歌」
才有意义）。新判据 `_starts_new_act`，两个正信号，都只把片段从「粘」推向「独立」，
缺证据时仍按保守的粘连处理：

1. **端侧认得它属于哪个云侧域**（`cloud_domain_of`：提醒/场景/记忆）——**认得出就说明它自成一句**。
   端侧一直知道「提醒我八点开会」是提醒诉求（`_is_reminder_utterance` 就是靠它否决的），
   只是用完就把这个知识扔了；
2. **分隔它的是顺承连词**（然后/再/并且/顺便…）——顺承引出新动作，补语跟在**裸逗号**后面。

反方向同样钉住（`test_regression_complex_intent.py`）：「打开空调，帮我播一首歌，周杰伦的」
里的「周杰伦的」必须**留在**主意图那一组——把补语撕下来才是真会答错的那一种。

### 1.3 端侧漏接一条车控说法

| case | 原话 | 期望 | 实际 |
|---|---|---|---|
| `ei.local.mirror@l0` | 把后视镜收起来 | `edge_local` | `cloud` |

`rear_view_mirror.fold` 是端侧能力，这句话没被端侧接住。风险 low，登记备查。

**✅ 已修**（`_to_legacy_name` / `classify`）：分类器与 VAL 一致地产出
`operate=set + mode=fold`，而 `LOCAL_INTENTS` 里登记的是 `rear_view_mirror.fold`
——名字该取自 **mode** 而不是 operate。旧实现拼出 `rear_view_mirror.set`，不在
`LOCAL_INTENTS` 里，于是整句上云。**分类器产出与 VAL 口径没动，动的只是名字映射。**

---

## 2. L1（真实 Planner，reference provider）

逐条证据在 `docs/reviews/eval/_ci-run-intent-adversarial.json`（gitignore 的运行工件，本地审计用）。

### 2.1 首轮读数（reference provider `minimax:MiniMax-M3`，warm 检索）

458 条 L1 证据单元，**400 通过（87.3%）**。

| 指标 | 值 | 分子/分母 |
|---|---:|---|
| `exact_plan_set_rate` | 91.3% | 418/458 |
| `required_group_recall` | 93.7% | 429/458 |
| `overroute_rate` | 5.5% | 25/458 |
| `forbidden_route_rate` | 1.1% | 5/458 |
| `dependency_pass_rate` | 99.8% | 457/458 |
| `relation_pass_rate` | 87.7% | 121/138 |
| `context_override_rate` | 100.0% | 7/7 |
| `capability_hallucination_rate` | **0.0%** | 0/458 |
| `instability_rate` | 3.1% | 14/458 |
| `clarify_balanced_accuracy` | 50.0% | 6/8 |

重复分类：pass 414 / stable_fail 27 / unstable 14 / critical_fail 3。

**seen 98.0%（49/50）vs unseen 86.0%（351/408）——12 个百分点的落差。**
这正是设计要量的东西：修复原句上的表现证明不了泛化。以后任何「落域准确率涨了」的
说法，都要先问是哪一档涨了。

`capability_hallucination_rate` 为 0 是个真结论：`_validated_steps` 那道能力集校验
确实在兜底（日志里能看到 `Intent trip.plan not in agent trip-planner capabilities,
dropping step` 这类丢步）。但它的代价见 2.2 的最后一条。

### 2.2 稳定缺陷（`stable_fail` / `critical_fail`，共 30 条）

按机制归簇，最重要的在最前面。**修复后的重新定性见每簇末尾——其中三簇的首轮归因是错的。**

#### 簇 A：否定与延缓语义没有被消费（4 条，两条 high risk）

| 原话 | 期望 | 实际 |
|---|---|---|
| 空调先别关 | 不动空调 | **`hvac.on`（去开了）** |
| 车门先别锁 | 不动门锁 | **`door_lock.close`（去锁了）** |
| 回家模式先别开 | 不动场景 | `scene.deactivate` |
| 帮我查下明天的天气，先别提醒我带伞 | 只查天气 | `reminder.create`（正好做了用户说不要的那件事） |

**这是本轮最值得修的一簇**：四条里三条落在车控/门锁/写操作上，而且失败方向是
**朝着执行去的**——「别做 X」被读成「做 X」。不是落域偏好问题，是安全语义问题。

顺带：「停车费先别交，我想先知道多少钱」被判 `critical_fail`——三次采样里有一次
真的规划了 `parking.pay`。单次翻面本身就是零容忍项。

**✅ 已修 3/4 + 1 条另有定性**：

- 新增**跨域 policy** `skills/policies/negation-and-deferral.yaml`。归 policy 不归 guide 的
  理由：否定是跨域语义，hvac / door_lock / scene / reminder / parking 全部命中，挂不到
  任何单个领域上。核心一句是**「别做 X」既不是做 X、也不是做 X 的反面**——
  用户在**阻止一个动作**，不是在下达相反的动作（实测 planner 的 goal 写的是
  「让空调保持打开（不要关闭）」，它理解对了，只是仍然出了一个动作步）。
- 配 `chitchat` 域**否定范例 3 条**。**说法刻意避开对抗语料原句**：把语料原句写成范例，
  那条 unseen 用例就变成 seen，之后再读「通过率涨了」就读不出泛化（§4.3-2 自己的纪律）。
- 第 4 条「先别提醒我带伞」**不是模型的错**：证据是 step id `s_hint_reminder_create`
  ——`reminder` 的 replace route_hint 把 planner 已经规划对的计划整条改写掉了。
  归入簇 B 一并处理（见下）。
- 「停车费先别交」**未收口，改定性为 `capability_gap`**，见 §6.1。

#### 簇 B：查询被读成写操作（4 条）

| 原话 | 期望 | 实际 |
|---|---|---|
| 明天要提醒我的事有哪些 | `reminder.list` | `reminder.create` |
| 接下来三天要提醒我什么 | `reminder.list` | `reminder.create` |
| 那条提醒我已经做完了 | `reminder.complete` | `reminder.create`（又建了一条） |
| 把交周报那条挪到后天下午 | `reminder.update` | `reminder.list` |

reminder 域的动词分工（list / create / update / complete）在真实说法上分不开。
`reminder.create` 的 intent 通过率 65.2%（n=23）是全域最弱之一。

**✅ 已修，且首轮的定性是错的。** 逐条抓 trace 后看到的是同一个 step id
`s_hint_reminder_create`，而 planner 自己的 `goal` **全都写对了**：

| 原话 | planner goal | 最终 steps |
|---|---|---|
| 明天要提醒我的事有哪些 | 查询明天的提醒列表 | `s_hint_reminder_create` |
| 那条提醒我已经做完了 | 把用户已完成的那条提醒标记为完成 | `s_hint_reminder_create` |
| 帮我查下明天的天气，先别提醒我带伞 | 查询明天的天气 | `s_hint_reminder_create` |

**分得开的是模型，踩掉它的是规则**——`reminder#1`（`提醒我|叫我|别忘了…` → replace
`reminder.create`，priority 56）。这条 hint 的 guard 已经补过四轮（回忆式 / 疑问式 /
条件式 / 赛事式），这是**第五类**（列表查询 / 完成 / 否定）。**规则的边界比说法的边界窄，
补下去没有尽头。**

修法按 M5 P2 的退役流水线走完整证据链（`test/hint_retirement.py`）：双臂裸跑
`minimax:MiniMax-M3` / `deepseek:deepseek-v4-flash` 各 ×2 轮，其 **18 条命中语料全覆盖**
（非抽样），B 臂（摘掉本条）**18/18 全对** → 退役。回归保护未删：三类代表形态
（绝对时点 / 地理触发 / 周期）已改端到端口径迁入 `mode_routing_cases.yaml`。
证据留档 `docs/reviews/eval/hint_retirement.*.only-reminder.{json,md}`。
⚠ 证据只覆盖上述两档；这一族 hint 当初是为 **MiMo** 写的，**切回 MiMo 须复验**。

「把交周报那条挪到后天下午」在首轮之后自行转绿，属首轮 `unstable` 的另一面。

#### 簇 C：危险动作被字面词直接触发（1 条）

「我要加油」→ **`fuel_tank_cover.open`**。用户在说需求（找加油站），系统去开油箱盖。
与 L0 那条「问天窗能开多大 → 真把天窗打开了」是同一族问题：**名词命中即动作**。

**✅ 已修（需要产品先裁一次）**。复跑时这句在 `fuel_tank_cover.open` 与
`navigation.search_poi` 之间摇摆——暴露出真正卡住的不是「名词命中即动作」，而是
**「找加油站」到底归谁没人裁过**（与 §4.1 的 `tu.nav.*` 是同一条边界）。
2026-08-03 泓舟裁定：**边界按动词划，不按对象划**——「找/搜/附近有什么」一律
`nearby.search`（周边发现），「导航去/带我去」才归 `navigation`（出行）。
登记进台账 `skills/exemplars/boundaries.yaml#nearby-navigation.find-vs-go`，
配双向各 2 条对照用例 + `nearby` 域范例 2 条。

裁定同时暴露并修掉一条**与裁定相反的存量金标**：`navigation#15「最近的加油站在哪」
→ navigation.search_poi`。台账规则是「判为地盘冲突请**改金标**，不许登记」——
范例是 few-shot，留一条与裁定相反的金标等于反过来教模型。已原地改写为出行侧说法
（不删除，删除会让 `navigation#16` 之后全部改号、历史 obs 归因指错条目）。

#### 簇 D：语义检索把领域 guide 召回到不相干的句子上（4 条）

`weather-outing` 以 `@vec:0.58` 被召回到「今天天气怎么样」、以 `@vec:0.52` 被召回到
「现在有没有暴雨预警」。词法档不会（L0 同句 guides 为空），**是语义通道的噪声**。

这条值得单独记一笔口径：**L0 与 L1 的检索档位不同**（L0 钉死词法、L1 走生产的
hybrid），所以同一条 `forbidden_skills` 断言在两层可以一绿一红——这不是矛盾，是
两个通道的真实差异被分层照出来了。

**✅ 已修，改的是 `description` 一行，知识本身一字未动。** guide 的 `description`
身兼**语义索引**，而原文案以「今天天气…」开头——语义通道当然会把裸问天气的句子拉进来。
改写成以**去处推荐**为主语、且不出现「天气」二字。**先量后改**（真栈 Embed 实测，
4 个候选 × 8 句）：

| 句子 | 原 description | 采用的新 description |
|---|---:|---:|
| 今天的天气适合去哪玩吗（golden） | 0.796 | 0.599 |
| 下雨天去哪儿玩好（golden） | 0.679 | 0.585 |
| 你确定下雨天还推荐我去公园吗（holdout） | 0.698 | 0.597 |
| 外面下着雨呢，还能去哪儿逛逛（holdout） | 0.584 | 0.613 |
| **今天天气怎么样**（不该召回） | **0.583** | **0.271** |
| **现在有没有暴雨预警**（不该召回） | **0.517** | **0.325** |
| **明天会下雨吗**（不该召回） | **0.584** | **0.327** |
| **接下来三天要提醒我什么**（不该召回） | **0.418** | **0.306** |

阈值 0.40：不该召回的四句全部降到 ≤0.327，四条 golden 仍 ≥0.585。
**这是索引优化，不是知识双写**——`knowledge` 与 `golden` 一行没动。

#### 簇 E：单点误路由（若干）

- 「昨晚国安打成几比几」→ `info.search`（赛事被浅搜劫持）
- 「第二条详细讲讲」（focus=info.news）→ `research.run`（深调研劫持追问）
- 「就近找个能逛的地方」→ `info.weather`
- 「轮胎气压是多少」→ `manual.query`
- 「如果明天下雨就提醒我带伞」→ 只查了天气，**没建提醒**（组合不完整，正是
  「goal 对而 steps 漏」的同型）

**逐条结论：**

- **「昨晚国安打成几比几」→ ✅ 已修**。既有赛事范例全是**赛事专名**（世界杯/欧冠/英超/西甲），
  没有一条是「中超球队 + 口语比分问法」——**这不是路由错，是说法没见过**，按 M5 分工
  投范例（`info#41`）。复跑 3/3 绿。
- **「第二条详细讲讲」→ ✅ 已修（需要产品先裁一次）**。真因是 `deep-research#1` 的
  replace hint，`hint_effect=replace` 实证，而 planner 的 goal 写的是
  「详细讲讲今天新闻的第二条」。更要紧的是**两套语料互相矛盾**：`mode_routing_cases.yaml`
  标了 `news_active` 明确要 `research.run`，对抗语料 `cs.more.news` 要 `info.news/search`。
  而 guard 是纯文本正则、看不到焦点——「第二条详细讲讲」在报告场景与新闻场景**逐字相同**，
  **一条分不开两个相反意图的 replace hint，按定义至少一半时间是错的**。
  2026-08-03 泓舟裁定：**「条/则」是列表项量词 → 就地展开；「点/节/部」是报告章节量词 →
  仍归深调研**。hint 量词集已相应收窄，`mode_routing` 的两条 `news_active` 用例改判。
- **「就近找个能逛的地方」→ 首轮之后自行转绿**（`bd.cn-attr.right.unseen` 现 PASS）。
- **「轮胎气压是多少」→ ✅ 已修**。真因是 `manual#4「胎压多少正常」` 以 `@vec:0.87`
  把它拉进说明书——两句表面几乎一样，差别只在**问当前读数**还是**问规格**。
  范例库对这种情形的解法是**摆对照**而不是去改那条本身没写错的范例：新建
  `skills/exemplars/tire_pressure.yaml` 2 条。
- **「如果明天下雨就提醒我带伞」→ ❗ 定性为 `gold_error`，改的是尺子不是系统**。
  原 gold 要求首轮就同时出 `info.weather` + `reminder.create`，这与**两处既有裁定**
  直接冲突：① `skills/guides/conditional-reminder.yaml`（v2，live 验过）明确写着
  「条件依赖必须 adaptive：先只规划查询步，**不要把 Y 无条件排进计划**」，其 golden
  就是 `expect_not: [reminder.create]`；② 同一份语料里 **stable** 的
  `cp.adaptive.rain-umbrella`「明天要是下雨就提醒我带伞」写的正是「首轮只查 + replan 补提醒」。
  实测 planner 的 goal 是「查询明天天气，若下雨则创建带伞提醒」——**它做对了**。
  已按 `cp.adaptive.rain-umbrella` 的形态补上 replan 段，把「条件不许被漏掉」这个真主张
  断在该断的地方（replan 轮必须补出 `reminder.create`）。
  **教训**：同一份语料里两条同形态用例断言相反，是「金标自相矛盾」的又一例——
  写新用例前先搜一遍同族已有的裁定。

#### 簇 F：能力归属错误导致合法步被整条丢弃（1 条，机制值得单列）

「调高音量」→ 计划里是 `volume.inc`，但 planner 把它派给了 `edge-media`，而
`volume.*` 属于 `edge-vehicle`。`_validated_steps` 按「intent ∈ 该 agent 能力集」
校验，于是**整步被丢**，计划退化成 `chitchat.talk`。

日志证据：`Intent volume.inc not in agent edge-media capabilities, dropping step`。

**判据**：能力集校验挡住了幻觉（hallucination 0%），但它对「intent 对、agent_id 猜错」
这类错误的处理是**静默丢步**而不是纠正到正确的 agent。丢一步和幻觉一步，对用户是
同一件事——都没做成。这条建议单独评估：intent 唯一时是否应按 intent 反查 agent。

**✅ 已修（机制级）**：`_validated_steps` 新增归位——**intent 在全清单里唯一归属时归位，
否则仍然丢**。归位不发明能力（intent 必须真实存在于某个已注册**且已过权限过滤**的
agent），归属有歧义（≥2 家）或压根不存在时不猜，`capability_hallucination_rate: 0%`
这条保证一分不减。归位同时换 endpoint/部署位——只改字符串会调不通。
守卫 `test_planning_intent_rehome.py` 5 条，**已反验**：回退 `planning.py` 后
「该归位」的 2 条红、「该丢」的 3 条仍绿。

---

## 3. 本套件自身被抓到的缺陷（首跑 7 个已当批修；修复批次又添 2 个）

这两条不是产品缺陷，是**测试自己**的缺陷，按「守红线的测试自己要被审计」当批修掉：

1. **确定性层不该有 `unstable`**。L0 无模型参与，低风险案例只跑一次，失败被
   `Counter` 判成「不稳定」——于是一条确定的缺陷既进不了修复清单也进不了门禁。
   修法：`classify_repeats(deterministic=True)`，L0 一次红即 `stable_fail`。
2. **范例检索静默降级成纯词法**。`orchestrator/cloud/embedding.py` 默认连
   `llm-gateway:50052`（容器内主机名），从宿主跑会被 `ALL_PROXY` 兜走并超时，
   Embed 失败后 fail-open 回词法。整轮 L1 于是测的不是生产装配却照样出数。
   修法：`--retrieval-state warm` 且范例档位是 hybrid 时，预热 0 条记基础设施错误、
   退出码 2。**这正是设计要防的「静默降级让报告看起来正常」**。
3. **判定失败却只留首轮证据**。高风险案例固定跑 3 次，首轮过、二三轮翻车时报告里会
   出现「`stable_fail` 但断言全绿」——诊断当场归零，还得回头重跑才知道错在哪。
   修法：分类为失败时留**第一条失败轮**的快照与断言。
4. **L2 嵌套事件循环**。`run_l2_case` 是 async，内部又 `asyncio.run(session.save())`
   与 `asyncio.run(_collect())`；在运行中的 loop 里再 run 直接抛，7 条 L2 全被吞成
   基础设施错误、`cases=0`。同一个坑在 `EngineHarness` 那儿踩过一次，这次在更外面
   一层——补了断言钉死「seed / Edge servicer / Engine 三段都必须是同步的」。
5. **取消判定要求 `is_confirmation`**。engine 的取消分支在规划之前就 return，裸
   「取消」不带 HMI 标记也走同一条路（否定词优先）；要求带标记等于把语音取消判成
   `execute`。修法：按「无 planner 调用 + 无 agent 调用 + 话术是取消」识别。

五条里有三条（1、3、4）的共同形态是**失败被记成了别的东西**：不稳定、无证据、
基础设施错误。这类缺陷比误判更危险——它们让报告看起来正常。

**修复批次又添一例（第 8 个，2026-08-03）**：`orchestrator/cloud/tests/test_multi_intent.py`
的 mock planner 按**整条 user message** 里有没有「天气」二字分派计划——而 user message
里除了用户原话还有能力清单、规划知识块、范例块。于是本批新增一条 policy（示例句里
带「明天的天气」）就让「打开空调」这条**单意图**用例测出了一个根本不存在的多意图 bug。
修法：只按 `用户说: ` 之后的原话分派。**同族**：`test_engine_focus.py` 也按整条
user message 判「再」，暂未命中但同一形态。

**第 9 个（2026-08-03）——又一次「失败被记成了别的东西」，而且正是 3.2 修过的那一类。**
全量发现轨中途撞上网关 `all models failed`，`PlanBuilder._fallback` 按设计兜底成
`chitchat.talk`，于是 **6 条组合意图用例被记成 `stable_fail`**（重跑 6/6 全绿）。
首跑自查时只在**范例预热**那一处防住了「静默降级让报告看起来正常」（§3-2），
**逐轮的规划调用没防**——同一条判据没有铺满它该铺的面。

修法 `_reject_unreached_planner`：判据用 `raw_llm` 而不是「计划长得像兜底」——
只要模型回过话 `raw_llm` 就非空（JSON 路径存原文、toolcall 路径存序列化 arguments），
**`raw_llm` 为空却仍出了计划，只可能来自 `_fallback`**，即两次调用都没拿到任何输出。
模型「回了但答得不好」照常算产品失败，一分不放水。两个方向都已反验。

**这条判据值得单独记住**：本套件设计时就写过「每加一个『拿不到结果』的分支，
先问它会被记成什么」。`_fallback` 就是这样一个分支——它是**生产侧正确**的设计
（LLM 抽风时仍有回应），但在**评测侧**它把「通道坏了」伪装成了「模型判错了」。
同一段代码在两个语境下的正确性可以是相反的。

---

## 4. 不在本清单、但属于同一批待办的三类

本文件只装**产品缺陷**。接手修复前先看这三类，它们不在上面：

### 4.1 需要产品拍板才能定 gold 的 9 条（已降回 `candidate`）

降级理由逐条写在语料的 `provenance.gold_revision_reason` 里。**不是测试写错了，是产品
还没定**——测试不该替产品拍板，所以宁可降级也不猜。

| case id | 原话 | 要定的事 |
|---|---|---|
| `nq.landmark.bare` / `nq.landmark.explicit` / `nq.city.bare` / `nq.city.explicit` | 「华润大厦」「上海」「导航到华润大厦」 | `CLARIFY_ENABLED` 生产默认 off，裸地名当前落 `navigation.search_poi`、裸城市名被判非受话走 `reject`。**这个开关翻不翻？**另外「导航到 X」落 `search_poi` 而非 `navigate_to`，需要确认 navigation 两个 intent 的分工 |
| `tu.nav.search-vs-detail` / `tu.nav.detail-vs-search` | 「搜一下附近的地铁站有哪些」 | `nearby.search` 与 `navigation.search_poi` 抢地盘，**台账里没有这条裁定** |
| `bd.manual-search.left` / `bd.manual-search.right` | 「车里这个黄色警告灯是什么意思」 | `manual.query` 与 `vision.describe` 抢地盘，**台账里也没有** |
| `ex.colloquial.dark` | 「有点看不清路了」 | 该开大灯还是开雨刷 |

中间两组建议直接补进 `skills/exemplars/boundaries.yaml`，与已有 18 条同一形态：
**人裁一次并登记，机器只负责「不许悄悄新增」**。裁完把对应 case 改回 `reviewed`
并补 `reviewed_by: human` / `reviewed_at`。

**✅ 9/9 已裁定并恢复 `reviewed`**（2026-08-03 泓舟；末行那条当日稍晚补裁）：

| 组 | 裁定 | 落地 |
|---|---|---|
| `nq.landmark.*` / `nq.city.*` | 澄清开关 **on** | ⚠ **本条的前提是错的**：`CLARIFY_ENABLED` **生产一直是 on**（`.env.example` 与 compose 自 2026-07-08 真栈 CDP 验收后即 `${CLARIFY_ENABLED:-on}`，运行中的容器实测也是 on）。「生产默认 off」读的是**代码兜底值**——而代码兜底与部署缺省不一致，**任何不经 compose 起的进程（评测/单测/CLI）测的都不是生产装配**。已把 `planning.py` / `engine.py` 的兜底缺省对齐到 on。另：模型光有开关还是不澄清，`_CLARIFY_SECTION` 补了一条判据——**缺的是槽位就照常执行，缺的是动词才澄清**（「整句只有一个名词、动词完全缺失」是典型歧义）。「导航到 X」两个 navigation intent **都算落对** |
| `tu.nav.*` | 找地点归 **`nearby.search`**（找=发现，导航=出行） | 台账 `nearby-navigation.find-vs-go` + 双向各 2 例 + `nearby` 范例 2 条 + 修掉与裁定相反的 `navigation#15` |
| `bd.manual-search.*` | **`manual.query`**（描述已足以定位对象就不用再看一眼；判据是隐私侧的——抓帧是敏感动作，能不抓就不抓） | 台账 `manual-vision.described-light-vs-unknown-light` + 双向各 2 例 + `manual` 范例 1 条 |
| `ex.colloquial.dark` | ✅ **已裁定（2026-08-03，`cb21c89`）**：走澄清卡 | 已恢复 `reviewed`，见 §6.2。**至此 9/9 全部裁定完毕，语料里 `candidate` 归零**（132 stable + 419 reviewed） |

台账每新增一条裁定，契约要求**双向各 2 例**——机械地就是 +8 条用例，suite 上限
随之 520→540。**边界裁定的对照用例是台账的兑现物，不是可裁的冗余**；上限该跟着
裁定数走，不是反过来。

⚠ 补台账时踩到一个坑值得记：给 `manual/vision` 边界加的「右侧对照」范例
（「仪表盘上亮的这个是什么」）以 `@vec:0.66` 被检回到左侧那句上，**当场把
`bd.manual-search.left` 打红**。**对照范例离对面太近就不是对照，是干扰**——
已撤回，右侧改用既有的 `vision#2「看看这个是什么」`（同样表达「无描述的实指」，
但不共享「仪表盘」这个把两侧粘在一起的语境词）。

### 4.2 测试体系自身的未达项 → 见规格 §21.8

`docs/design/2026-08-02-intent-routing-adversarial-testing.md` §21.8 逐条列了 5 项：

1. **L3 证据未取得**——`scripts/run_e2e.py` 在本机以 `lease_protocol` /
   `identity_cleanup` 失败。这是既有 e2e 运行器的环境路径，本批次没改过它，
   **修它属于 e2e 运行器的账，不属于对抗套件**；
2. **正式 baseline 未生成**——被资格闸正当拒绝（连带 1）；
3. **stable 113 < 设计要求的 120**；
4. 9 条 candidate（即 4.1）；
5. **发现轨主跑用了 `--ablations off`**，红灯只有首偏离点、尚未逐条建立因果证据。
   要归因先跑：`--layer l1 --live … --ablations on-failure --case <id>`。

**本批次进展**：第 4 项 8/9 收口（见 §4.1）；第 1/2/3/5 项**未动**，仍是 e2e 运行器与
下一批晋级的账。

### 4.3 接手修复时的两条纪律

1. **修落域 badcase 的默认产物是范例与知识，不是正则**（`skills/exemplars/`、
   `skills/guides/`、`boundaries.yaml`）——hint 写错是事故，范例写错只是噪声。
2. **改完先跑发现轨对照，别只跑被修的那几条**。本批次的 seen 98.0% / unseen 86.0%
   就是基线：如果修完 seen 涨了而 unseen 没动，那修的是记忆不是能力。

**本批次对这两条的践行与补充见 §5.3。**

---

## 5. 修复批次（2026-08-03）

### 5.1 改了什么

| 层 | 产物 | 对应 |
|---|---|---|
| 端侧规则 | `fast_intent`：疑问框架否决（只盖写操作）、`cloud_domain_of`、顺承连词分组信号、后视镜名字映射；`server._starts_new_act` | §1.1 §1.2 §1.3 |
| 规划知识 | 新增 policy `negation-and-deferral`；`weather-outing` description 重写 | §2.2 簇 A / 簇 D |
| 范例（8 条净增 + 2 条修正） | `chitchat` 否定 ×3、`nearby` 找地点 ×2、`tire_pressure` 新域 ×2、`manual` ×1、`info` 赛事 ×1；修正 `navigation#15`；撤回一条干扰范例 | §2.2 簇 C / 簇 E |
| 规则退役与收窄 | `reminder#1` 退役（双档 ×2 轮 18/18 全覆盖）；`deep-research#1` 量词收窄 | §2.2 簇 B / 簇 E |
| 编排机制 | `_validated_steps` intent 唯一归属时归位；`CLARIFY_ENABLED` 代码兜底对齐部署缺省；`_CLARIFY_SECTION` 补「缺动词 vs 缺槽位」判据；`SKILL_BUDGET` 2400→2600 | §2.2 簇 F / §4.1 |
| 边界台账 | 新增 2 条裁定 + 双向各 2 例（+8 用例，suite 上限 520→540） | §4.1 |
| 语料修正 | `nq.condition.if-rain` gold 改为 adaptive 两段式；`tu.nav.detail-vs-search` 放宽；`mode_routing` 两条 `news_active` 改判 | §2.2 簇 E |
| 新增守卫 | `test_fast_intent_adversarial.py` 17 条、`test_planning_intent_rehome.py` 5 条、`test_skills_budget_headroom.py` 2 条、`test_regression_complex_intent.py` +2 条 | 全部 |

**规则净变化：10 → 9**（退役 1 条，收窄 1 条，新增 0 条）。北极星 N2「规则净增速率 ≤0」本批成立。

### 5.2 一个非预期的连带缺陷：常驻 policy 会挤掉 guide

新增 policy 落库后 L0 立刻掉一条（`ki.navigation-with-stop.hit`）。根因是
`render_skills_block` 先无条件铺 policy、再按检索序塞 guide，**两者共用一个
`SKILL_BUDGET`**——于是「加一条 policy」这个看起来纯加法的动作，把当轮**最相关**的
guide 记成了 `!clipped`。算术：policies 1047 + 块头 14 + `navigation-with-stop` 1428
= 2489 > 2400。

修法两步：policy 先从 506 字压到 277 字（**常驻的东西每轮都在付钱**），再把预算提到 2600。
更要紧的是**把这次的算术钉成红线**——`test_skills_budget_headroom.py` 断言「常驻总量 +
最大的那条 guide 必须放得进预算」，**已反验**（`SKILL_BUDGET=2400` 时 2 条全红）。
它守的不是某个具体数字，是「加 policy 要连带看 guide 的头寸」这条纪律。

**判据入册：名单诚实（`!clipped`）是这次能当场发现的唯一原因。** 如果注入名单谎称
已注入，这个缺陷会一直潜伏到某条 badcase 复发。

### 5.3 三条纪律的践行

读数见 §5.4（**聚合数字有口径警告，逐条结论看 §5.5**）：

1. **修落域 badcase 的默认产物是范例与知识**——本批 8 条净增范例、1 条 policy、
   1 条 description 重写；**规则只退役与收窄，一条没加**。
2. **改完跑发现轨对照，不只跑被修的那几条**——§5.4 是全量 526 条的对照，不是 18 条子集。
3. **范例说法一律避开对抗语料原句**——否则 unseen 用例被洗成 seen，
   「通过率涨了」就再也读不出泛化。这一条是本批自己加的纪律，写进各范例文件的注释里。

### 5.4 全量对照（发现轨，minimax:MiniMax-M3，warm 检索）

> ⚠⚠ **这一节的聚合数字不是权威结论。** 修复批次跑完当天，另一路独立评审
> （`docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md`，评审对象
> `26f59e1..de6ef22`，即**本批修复之前**的尺子）判定该套件**不通过 gate/baseline 验收**，
> 并点名下列指标口径不成立：`exact_plan_set_rate`（实为整轮 `judgement.passed`，
> retrieval 失败也记成 plan 失败）、`seen/unseen`（**3 条完全相同的 utterance+context
> 同时落在两个 cohort**，不是严格隔离读数）、`capability_hallucination_rate`
> （实为 post-validation escape rate）、`instability_rate`（分母含只跑过一次的证据单元，
> 只能叫已观察到的下界）。
>
> **本批没有修尺子**（评审建议的修复顺序 §5 是独立一批的账，见 §6.5）。所以：
>
> - **可信的是逐条证据**：每一条产品缺陷都按原始断言单独复现、单独验证修复——
>   这正是评审自己写的「30 条稳定产品缺陷**不因本 review 自动推翻**；仍应逐条按原始
>   断言复现，不能只看聚合报告」。**§5.5 的逐条表才是本批的结论。**
> - **下面的聚合表只作趋势参考**，且每个数字都按评审的裁定标注了它到底能说明什么。
>   **尤其：不要引用「unseen 涨了 5.6 点」当泛化证据**——cohort 隔离本身有缺陷，
>   这个数字要等语料按 canonical fingerprint 去泄漏、重新晋级后才能重算。

⚠ 另外两条读数口径（与上面的评审无关，是这一跑自己的情况）：

1. **分母变了**：458 → 468。本批加了 8 条边界对照用例，另有 8 条 candidate 裁定后恢复
   `reviewed` 进入 live 车道。**两次跑的不是同一个选集**，绝对条数不可直接相减。
2. **这一跑中途撞上网关 `all models failed`**（日志 7 次）。`_fallback` 按设计兜底成
   `chitchat.talk`，于是 6 条组合意图用例被记成 `stable_fail`。**全部单独重跑，6/6 绿**
   （`cp.dep.four-way` / `cp.dep.three-way` / `cp.dep.order-then-status` /
   `cp.dep.weather-then-navigate` / `os.detail.poi` / `os.detail.shop`）。
   下表「修复后」列给**实测值**，另列一行给扣掉这 6 条基础设施伤亡后的产品读数。
   这一条已经机制化，见 §3 第 9 条。

| 指标 | 首轮（458 条） | 修复后（468 条，实测） | 这个数能说明什么 |
|---|---:|---:|---|
| **原始 evidence unit 通过数** | 400 / 458 | 425 / 468；扣 6 条网关伤亡后 **431 / 468** | ✅ **评审明确认可**「可作原始 evidence unit 结果」。这是本表唯一可直接引用的一行 |
| 重复分类 | stable_fail 27 / critical_fail 3 / unstable 14 | stable_fail 11（扣伤亡 **5**）/ critical_fail 2 / unstable 19 | ✅ 同上，逐条可复现；**27→5 是本批最实的改善** |
| `required_group_recall` | 93.7% | 95.4% | ⚠ 仅趋势。它是纯 plan 断言，受 P1-1 影响较小，但仍受 cohort/重复口径牵连 |
| `overroute_rate` | 5.5% | 3.6% | ⚠ 仅趋势 |
| `forbidden_route_rate` | 1.1% | 0.9% | ⚠ 仅趋势 |
| `relation_pass_rate` | 87.7% | 90.2% | ❌ **不可引用**：P1-5 说 relation 不进重复分类、失败与 `repeat_status` 自相矛盾 |
| `clarify_balanced_accuracy` | 50.0%（6/8） | 100.0%（8/8） | ⚠ 分母只有 8，其中 4 条正是本批恢复的裁定用例。**只证明「开关与判据接对了」** |
| `exact_plan_set_rate` | 91.3% | 93.2% | ❌ **不可引用**（P1-1：实为整轮通过率） |
| `capability_hallucination_rate` | 0.0% | 0.0% | ⚠ 按 P1-3 应读作 **post-validation escape rate**。对本批仍有意义——簇 F 改的正是 validator 本身，**逃逸率没被这次放宽推高** |
| `instability_rate` | 3.1% | 4.1% | ⚠ 按 P1-1 只是**已观察到的下界**，两跑重复覆盖不同。**但方向变差这件事本身要记着** |
| seen 98.0% / unseen 86.0% | — | seen 96.0% / unseen 90.2% | ❌ **不可引用**（P1-7：3 条相同输入跨 cohort 泄漏）。**本批不拿它当泛化证据** |

**几条必须说清楚的：**

- **`stable_fail` 27 → 5 是本批最实的改善**，且它不依赖任何被质疑的口径——每条都是
  按原始断言逐条复现、逐条验证的（§5.5）。
- **`instability_rate` 变差是本批唯一明确的负向信号**，不掩饰。虽然按 P1-1 它只是下界、
  两跑重复覆盖不同，**但新增的抖动集中在本批新恢复/新增的边界用例上**——它们本来就站在
  两个域的分界线上，天然更容易抖。方差变大值得下一批盯着。
- **seen 掉的那 1 条**是 `bd.ns-poi-road.right.seen`「路上怎么样」→ `safety.driving_advice`
  而 gold 要 `safety.road_condition`（safety 域内的兄弟意图之选）。**不能声称与本批无关**：
  本批新增的常驻 policy 与 `_CLARIFY_SECTION` 那条判据**改变了每一次规划的 prompt**。
  没有单变量证据就不写「无关」——留给下一批用消融归因。
- **本批原本想拿 unseen 的涨幅当泛化证据，现在撤回这个说法。** 为泛化做的那件事仍然做了、
  且值得保留（**范例说法一律避开对抗语料原句**，见 §5.3-3），但**证明它奏效的那把尺子
  自己有隔离缺陷**——等 P1-7 修完重新晋级后再算。这正是 §4.3-2「读任何『落域涨了』
  先问哪一档涨了」的下一层：**先问那一档本身分得干净吗**。

### 5.5 逐条结论（本批的真结论）

| 首轮红灯 | 层 | 修法 | 复验 |
|---|---|---|---|
| `ei.noise.question-about-control` / `ei.noise.hypothetical` | L0 | 疑问框架否决（只盖写操作） | ✅ 绿，零副作用 |
| `ei.mixed.volume-reminder` / `ei.mixed.seat-charging` | L0 | `_starts_new_act` 分组判据 | ✅ 绿，本地半条真秒回 |
| `ei.local.mirror` | L0 | 名字取自 mode | ✅ 绿 |
| `nq.hvac-keep.dont` / `nq.lock-quote.negated` / `nq.scene-home.hold` | L1 | 否定 policy + chitchat 范例 | ✅ 绿 |
| `nq.umbrella.negated` | L1 | `reminder#1` 退役 | ✅ 绿 |
| `bd.ir-news-tmr.right` / `bd.ir-fc-3d.right` / `os.reminder.complete` | L1 | 同上（同一条 hint） | ✅ 绿 |
| `cs.more.news` | L1 | `deep-research#1` 量词收窄（裁定后） | ✅ 绿 |
| `os.refuel.fuel` | L1 | find-vs-go 裁定 + nearby 范例 | ✅ 绿 |
| `os.state.tire` | L1 | `tire_pressure` 对照范例 | ✅ 绿 |
| `ki.weather-outing.miss` / `ki.guide.no-recall-on-alerts` | L1 | description 重写（先量后改） | ✅ 绿 |
| `ki.hint.no-hijack-sports` | L1 | info 赛事范例 | ✅ 绿（3/3） |
| `os.raise.volume` | L1 | `_validated_steps` 归位 | ✅ 绿 + 单测反验 |
| `nq.condition.if-rain` | L1 | **gold 错**，改 adaptive 两段式 | ✅ 绿，replan 轮真出 `reminder.create` |
| 8 条 §4.1 candidate | L1 | 泓舟裁定 + 台账 + 双向对照 | ✅ 全绿并恢复 `reviewed` |
| `nq.parking-negate` | L1 | — | ❌ 未收口，改定性 `capability_gap`（§6.1） |
| `ex.colloquial.dark` | L1 | — | ⏸ 未裁定，维持 `candidate`（§6.2） |

---

## 6. 仍未收口（5 项）

### 6.1 `nq.parking-negate`「停车费先别交，我想先知道多少钱」——`capability_gap` ✅ **已收口（2026-08-03）**

> **补的是能力，不是描述。** `parking.query_fee` 已落地（manifest capability + agent
> 分支 + 3 条契约测试 + 覆盖 2 正例/2 硬负例/1 对照）。provider 侧 `get_fee` 本来就在，
> 缺的一直只是能力面上的声明。实测这句话现在落 `parking.query_fee` 3/3——
> **用户问的那件事第一次被答上了**，不是「躲开了错的」而是「做对了」。
> 顺带印证 §6.1 原文那句：模型被迫从目录里选，只能选到唯一那个。
>
> 以下为原始定性，保留备查。


改定性：**不是落域问题，是能力面没有这个东西**。`parking-payment` 只有 1 个 capability
（`parking.pay`），**根本没有「查停车费多少钱」这个能力**——用户问的那件事，系统答不了。
模型被迫从目录里选，只能选到唯一那个。

`negation-and-deferral` policy 已注入、`parking#2「把停车费付了」`仍以 `@vec:0.74`
把它往 `parking.pay` 拉，两相抵消后仍是 `critical_fail`。

**已有的安全兜底**：`parking.pay` 的 `require_confirm=true`，硬层会先问一句才付钱
——**规划错了但钱不会自己出去**。这不是「所以没关系」，是说它的严重度是「答非所问」
而不是「误付款」。

**修法建议（下一批）**：给 `parking-payment` 补一个 `parking.query_fee` 读能力。
按 CLAUDE.md §3 的流程走（manifest 声明 + SDK 实现 + 契约测试），属新增能力，不在修复批范围。

### 6.2 `ex.colloquial.dark`「有点看不清路了」 ✅ **已收口（2026-08-03，`cb21c89`）**

> **泓舟裁定走澄清卡**，判据**写成两面**：先说程度性缓解照常直接做一个、绝不反问，
> 再说互斥且有代价才澄清。只写一面必然把「有点闷」「太吵了」一起带进反问。
> **分界不是「是不是状态句」，是「猜错有没有实际代价」**——干挡风上刮雨刷是有害的，
> 开窗还是开空调都只是程度不同的缓解。
> **先量后改**：7 条 `ex.colloquial.*` 各跑 3 次前后对照，`dark` 1/3→3/3、`noisy`
> 2/3→3/3；`stuffy` 掉一条触发了事先定的回退线，**没有机械执行**（n=3 分不开真回退
> 与采样噪声，且失败产物与本次改动的语义无关），补测 6/6，合计 8/9。详见评审 §10.10。
>
> 以下为原始定性，保留备查。

该开大灯还是开雨刷（雨天看不清是合理推断）。本批只问了 4 个问题，这条没问，
**不替产品拍板**，维持 `candidate`。

⚠ 另有一处连带：`CLARIFY_ENABLED` 兜底缺省翻 on 后，这类天然歧义句更可能走澄清卡，
定 gold 时要把 `decision.allowed` 一起定，别只定 intent。

### 6.3 两条 `unstable`（按口径**不登记为缺陷**，但记在这里备查）

- `ki.hint.no-hijack-sports` 首轮之后一度 `stable_fail`，投范例后复跑 3/3 绿；
- `tu.nav.detail-vs-search`「广州东站这个站点的详细信息给我看看」在
  「先搜再取详情」与「只搜」之间摇摆。gold 已放宽到「详情步必须在、允许额外步」
  ——**「先搜到再取详情」是更好的计划**（`poi_detail` 要 `poi_id`），把 search 列进
  forbidden 会把一个更好的计划判成红。

### 6.4 尺子自身的 12 条问题——独立评审另开一批（**三批硬化已落地，复审仍未通过**）

`docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md`（3 条 P0 / 7 条 P1 /
2 条 P2）审的是「**测试能不能证明结论**」，不是否认它抓到的产品 badcase。
**本批一条都没修**——修尺子和修被测对象同批进行，正是这套体系一开始就禁止的事
（「边建尺子边改被测对象，两边都说不清是谁变了」）。按评审建议的顺序另开一批：
先封假绿（baseline 资格闸 / L2 合并 Edge 副作用 / 多轮 fail-closed / L3 唯一 run 目录），
再修尺子（plan-only exact、ingress AND、gold 维度归因、raw hallucination、repeat coverage），
接通诊断，最后清语料账（canonical fingerprint 去 cohort 泄漏、重新晋级）。

> **落地状态（2026-08-03 另批）**：修复方按 12 条逐项处理并补反向构造，专项单测 120→178；
> 生产路由/Skill/Exemplar/Hint/manifest 一个字节未改（**这批只动尺子**，与上一句的分批
> 纪律一致）。逐条对照表、四条新发现（含**原句泄漏比评审点名的多 4 倍**、空选集原来是
> 绿的）见该评审报告 **§7**；**独立复审 §8 只接受 4/12 完整关闭，仍有 2 P0 / 5 P1**，
> 第三批 `8f06db5` 与覆盖补丁 `cd3646b` 又把专项测试增至 201。最新独立复审 **§9**
> 接受 Engine、指标分母、L1 适用性、relation 扩跑与唯一输入 coverage 已关闭，但仍有
> **2 P0 / 2 P1**（baseline 比较源/gold 摘要、Planner 重试 raw 对齐、L3 run/code/lock
> 身份），因此本清单仍不得标“全部收口 / baseline-ready”。
> 对本清单的影响只有一条需要注意：**本文件 §5.4 的全量对照数字仍是 2026-08-02 的旧口径
> 读数**，新口径下 `exact_plan_set_rate` / seen-unseen / `instability_rate` 都要等一次
> 固定 provider 的 live 全量才存在。
>
> **最终状态（2026-08-03 晚，六批后）**：三轮独立复审的 P0/P1 **全部关闭**，
> 专项单测 **231**；新口径 L1 全量读数已存在（评审 §10）。
> 此后又收口三条自立的账：`clause_commute` 口径裁定（§10.12）、
> 「恰好 N 次副作用」契约字段（§10.13）、`stable` 规模 104 → **122**（§10.14）。
> ⚠ **但 baseline 仍不得标 ready，而且前置多了一条**：除 L3 证据外，
> **现有 stable 集合里还有两条稳定红**（§10.14.4）——见下方 §7。

**本批顺手兑现了其中两条**（都是修复过程中自己撞上的，不是照单认领）：

- **P2-2 跨盘输出崩溃**——本批第一条命令就撞上（repo 在 D:、输出指到 C: 的 scratchpad，
  `os.path.relpath` 抛 `ValueError`，且是**整跑跑完之后**才炸）。评审独立复现了同一条。
- **§3 第 9 条 `planner_unreached`**——评审 P1-1 说的是指标口径，这条是它的邻居：
  网关不可达时 `_fallback` 的兜底计划被记成了产品失败。已机制化。

### 6.5 一条只记账、不改的观察

`PlanBuilder._fallback` 的语义路由分支取 `a.manifest.capabilities[0].intent`
——**能力清单的第一项是声明顺序，不是用户意图**。R4.4 D5-2 已经在**分数**那一侧
补过一次（低分不硬执行），但**选哪个 intent** 这一侧仍然是按位置取。
本批**没有**证据显示它在真实流量里被触发（评测侧 `registry_fn` 返回空、生产侧
chitchat 全局兜底优先命中），所以**不改**——没有消费方证据的修改是猜测。
记在这里，等它真的出现在某条 trace 里再动。

---

## 7. 2026-08-03 晚（口径裁定期）新增的发现清单

> 本节只列**产品侧、未收口**的账。尺子侧三条已收口的裁定见评审 §10.12–§10.14 与
> 规格 §22.6/§22.7；完整流水见 `docs/agents-history.md` §6。
> 沿用本文件既有分工：**这里是发现的单一入口，修复动作另开批。**

| # | 发现 | 定性 | 出处 |
|---|---|---|---|
| 7.1 | `ex.homophone.aircon`「把空条打开」→ **两趟各 3/3 落 `sunroof.open`** | **说开空调开了天窗。** 同音字被解成了另一个车控对象；它是 `stable`、2026-08-02 晋级时通过＝**回归** | 评审 §10.14.4 |
| 7.2 | `nq.umbrella.both`「帮我查下明天的天气，然后提醒我带伞」→ 两趟各 3/3 只出 `info.weather` | **组合漏第二步**（§10.8 同族）。同为 `stable` 回归 | 评审 §10.14.4 |
| 7.3 | `cp.reminder-weather.swapped`「查下**明天**天气，再提醒我**八点**开会」→ 3/3 把 `time_text` 写成「明天早上八点」 | **子句间槽位串味**：前一子句的时间限定词串到了后一子句上（base 是「八点」）。**旧 relation 口径下它被噪声掩埋成 `unstable`**，改成对照 `supp(base)` 之后才浮出成 `stable_fail` | 评审 §10.12.5 |
| 7.4 | 一次畸形 `depends_on: [["s0"]]` **打死整趟 L1 跑批** | ✅ **已修（`50c2b3f`）**。根因不是没做防御，是**防御只做到最外层容器**；同族第二处崩在**执行期**（`slot_refs` value 被 `.split(".")`） | 本文 §7.4 |

### 7.1–7.2 的共同点：门禁集自己在退化

两条都是 `stable`，都在 2026-08-03 晚的两趟独立 live 里稳定红。这直接证伪了一个
被默认成立的前提——**「stable 补到 120，正式 baseline 就只差 L3 了」**。

**判据：规模闸绿了不等于门禁绿了。** `--strict` 判的是语料规模，live 门禁判的是
stable 全绿。把前者的绿读成「baseline 只差 L3」，会漏掉一整类回归。
**这条前置比 L3 更硬**——L3 是运行器环境的账，这两条是被测对象自己的红。

修法按 M5 范式：7.1 是单句落错域 → 投**范例**（`skills/exemplars/`）；
7.2 与 §10.8 同族，真因很可能同样在 **guide 制造漏步**，先拉检索名单再定。

### 7.3 它同时证伪了一个提议

评审 §10.11 原提议是「把槽位摘出语义签名」（理由：签名已刻意排除 step id 与文风，
槽位同理）。**若照做，7.3 会永久不可见**——两侧 intent 集合完全相同，
差别全在槽位。所以裁定是**槽位留在签名里**，改的是「拿一次采样代表一个句子的行为」
这个更底层的问题。

### 7.4 已修，但判据要留下

**模型输出是不可信输入，防御要一路防到真正会被拿去 hash / 拿去 split 的那个值，
不是防到最外层容器为止。**

旁边的注释已经推理过一次同样的事（「`depends_on` 非 list 会被逐字符迭代」），
但 `[["s0"]]` **是** list、`isinstance` 检查照过，一路走到 `dep in valid_ids` 才崩，
而 `_parse_and_validate_data` 在 `build()` 里没有任何异常防护——**一次畸形模型输出
就把整条规划抛出去**。非 str 元素直接丢而不是 `str()` 转：转出来的 `"['s0']"`
匹配不上任何步骤，却会在日志里留下一个不存在的 id，**丢弃比转换更诚实**。

### 7.5 三条够不上晋级、留在预选池的候选（不是缺陷，是证据不足）

`cp.hvac-news.swapped`（B 趟 relation 红）、`nq.hvac.reported`（A 趟红）、
`nq.match.lastweek`（A 趟 `unstable`）。按规矩要**两趟独立进程都过**才晋级，
下次晋级先看它们。另有 `cp.adaptive.weather-outing` 两趟都过、**因声明 `l3` 而被挡**
——L3 那条账一还上它就能晋级（唯一输入 122 → 123）。

---

## 8. 修复批次（2026-08-03 深夜）：§7.1 / §7.2 两条 P0，产品侧

> 沿用本文件的分工：§7 是发现，这一节是**修复动作**。本批**只动生产**
> （`skills/exemplars/hvac.yaml` 新建、`skills/guides/conditional-reminder.yaml` 改写、
> 该 guide 的契约单测跟着改），语料与对抗运行器一个字节未改。

### 8.1 §7.1「把空条打开」→ 真因不是「模型不认同音字」，是这个域一条范例都没有

诊断第一行就是指纹：`exemplars=[]`。**不是阈值没够着，是 `skills/exemplars/` 下
连一条车控范例都不存在。** 往上追一层就明白为什么：范例库最初的 199 条金标全部来自
**14 份云侧 agent manifest 的 `examples`**，而车控是端侧能力、从来没有 manifest
examples——于是「车控」这一整片在范例库里是空白。

平时这不要紧（车控走端侧快路径，根本不经 planner）；但**同音字恰恰是快路径认不出、
必然上云的那一类**，此时云侧 planner 面对的是一个它没见过的词，而范例层无话可说。

修法：新建 `skills/exemplars/hvac.yaml` 两条，教的是「空条＝空调这个**对象**」，
动作留给模型自己判（`空条开到二十四度`→`hvac.set` / `车里太闷了，先开一下空条`→`hvac.on`），
**刻意避开语料原句**——`ex.homophone.aircon` 是 `unseen_transfer`，用原句等于教背诵。

**复验（两趟独立进程）**：单案例 `--repeat 3` **3/3**；`--tag hvac` 全族 38 条里它
同样通过。检回的是 `hvac#2@vec:0.69`——**语义通道命中、词法完全没够着**，
这正是「转移得过去」而不是「字面对上了」的证据。

**顺带把「是不是我改坏的」这条排干净**：`--tag hvac` 38 条里 6 条红
（`cp.hvac-news.swapped` / `cs.topic-switch.mid-trip` / `ex.colloquial.hot` /
`nq.hvac.keep-volume` / `nq.hvac.reported` / `tu.hvac.dec-vs-inc`），
**全部 `exemplars=[]`**；再回查改动前的 L1 全量存档，这几条当时**也是 `exemplars=[]`**。
两头都空 → 连「新范例改了 IDF、压掉了别的范例」这条唯一的间接通道也排除了。
（注入了 hvac 范例的 6 条用例全部通过。）

### 8.2 §7.2「查天气然后提醒带伞」漏第二步 —— 它**不是回归**

立账时按「2026-08-02 晋级时通过、现在稳定红」定性成回归。**逐条拉证据后这个词用错了。**

| | 通过的那次（`13e7e3f`） | 失败的两趟（`50c2b3f`，各 3/3） |
|---|---|---|
| 检索名单 | `conditional-reminder@lex:14` + 三条常驻 policy | **逐字相同** |
| 范例名单 | `reminder#28@vec:0.80`, `info#23@vec:0.67` | **逐字相同** |

**注进模型的东西一个字节没变，红绿差异只能是采样。** 之前那次是蒙对的。

> **判据：说「回归」之前先证明输入变了。** 检索名单是现成的对照物——
> 它逐字相同时，「以前是绿的」不构成「有人改坏了」的证据，只说明这句话一直站在
> 判定边界上。（同族第二次用上「goal/名单是免费对照物」这个思路。）

真因在 guide 自己身上，且形态与 §10.8 `multi-day-trip` 完全一致——**知识把判据写窄了**：

    旧：- **并列**：提醒本身已经有**明确时间**，再用『再/顺便/另外』查询天气

于是「没说时间」被当成了条件句的**证据**，`查下天气，然后提醒我带伞` 落进条件分支、
提醒被整个吞掉。用户说了两件事只做成一件，而他要到提醒该响的时候才发现。

**修法：判据从「有没有说时间」换成「有没有条件词/否定词」，并且写成三分。**

⚠ **只修「并列」这一分会把否定句一起翻正**——`nq.umbrella.negated`
「帮我查下明天的天气，**先别**提醒我带伞」吃的是同一条 guide，放宽并列规则会让它
多做一件用户明说不要的事。所以判据必须是**条件 / 否定 / 顺承并列**三分，
且否定分支单配一条金标（`看下明早的空气质量，别提醒我戴口罩了`）。

**复验（同一趟里连兄弟用例一起跑，单跑目标用例是自证）**：

| 用例 | 形态 | 结果 |
|---|---|---|
| `nq.umbrella.both` | 顺承并列，提醒无时间 | **3/3 通过**（修复目标） |
| `nq.umbrella.negated` | 否定 | 3/3 保持通过（未被翻正） |
| `cp.adaptive.rain-umbrella` / `nq.condition.if-rain` | 条件 | 3/3 保持 adaptive 只出查询步 |
| `cp.reminder-weather.base` | 并列有时间 | 3/3 保持通过 |
| `cp.dep.four-way` | 四件事并列 | 3/3 保持通过 |

### 8.3 §7.3 定性更正：「明天早上八点」不是子句间串味，至少不全是

原定性（评审 §10.12.5）：「前一子句的时间限定词串到了后一子句上」。**串味说预测的是
『明天』，实测拿到的是整串『明天早上八点』——而『早上』在原话的两个子句里都不存在。**

往下查一层，这个字符串有两个现成的来源，都在 prompt 里：

1. `skills/exemplars/reminder.yaml#28` 的槽位**逐字就是** `{title: 带伞, time_text: 明天早上八点}`
   ——而 `nq.umbrella.both`（原话里**一个时间词都没有**）产出的正是
   `{title:"带伞", time_text:"明天早上八点"}`，**两个槽位一起照抄**；
2. `agents/reminder/manifest.yaml` 的 capability description 写着
   「支持绝对时间（**明天早上八点**/周五下午三点）」，而 **description 是渲进 planner
   catalog 的**（`context.py::_catalog_item` 非 edge 分支带 `desc`）——它在**每一次**规划里。

所以形态是「**示例里的字面被当成了默认值**」，不是子句间搬运。

修法写进同一条 guide：`time_text` 只取提醒那半句的原话字面；另一半句的『明天/后天』
是**查询**的日期限定词；描述与范例里的『明天早上八点』是**格式示范不是默认值**；
用户没说时间就留空（时间解析归 `timeparse`，不归规划——同「系统持有的事实不交给 LLM」）。

**复验结果把这两个成分分开了**：`cp.reminder-weather.swapped` 从 **3/3 红降到 1/3 红**，
且幻影从「明天早上八点」变成「**明天八点**」——**『早上』整个消失了**。
即：**『早上』是照抄示例字面（已修掉），『明天』才是真的子句串味（仍在）**。
剩下这 1/3 按本套件规矩是 `unstable`，不进修复清单，账留在 §6。

### 8.4 本批新发现（未修，另开）：`hvac.*` 与 `aircon.*` 是同一个动作的两个名字

`edge_call._to_structured` 里有一行 `{"hvac": "aircon"}` 的对象改名。实测：

    hvac.dec   -> data={'object': 'aircon', 'operate': 'dec', ...}
    aircon.dec -> data={'object': 'aircon', 'operate': 'dec', ...}     # 逐字相同

两个 intent 名解出**完全相同的执行数据**，而 `VEHICLE_INTENTS` 把两套名字**都**注册进
能力面，端侧 catalog 又**只渲染意图名、不渲染描述**（M5 P3 收尾实测 Δ=0 后的有意决定）。
**planner 面对两个无法区分的同义工具，只能掷硬币。**

后果直接落在语料上：gold 只写 `hvac.dec` 的用例，会在模型选到 `aircon.dec` 时红。

| 用例 | 改动前存档 | 本次 | 说明 |
|---|---|---|---|
| `tu.hvac.dec-vs-inc`「温度再低两度」 | `hvac.dec` 通过 | `aircon.dec` 红 | 方向对、对象对，**只是名字换了一个** |
| `ex.colloquial.hot`「有点热」 | `aircon.dec` | `aircon.dec` | 见下 |

> ⚠ **运行手册 §10 里「『有点热』落 `hvac.inc`、方向反了」那条 P2 描述已经不成立。**
> 它现在产出的是 `aircon.dec`——**方向是对的**，红在 gold 的 `any_of` 只收 `hvac.*`。

**判据：一部分「不稳定」其实是别名分裂，不是模型抖动。** 同一个动作有两个等价名字时，
模型在两者间摇摆会被 `instability_rate` 记成方差——去查 `repeat_status` 之前，
先问「这两个不同的答案是不是同一件事」。

两条修法各有代价：① 语料 gold 收两个别名（治标但正确——它们确实等价，而且
`fast_intent` 自己产出的就是 `aircon.dec`）；② 端侧能力面去重、只暴露一套名字
（治本，但要先盘 `aircon.*` 的全部消费方，端侧快路径正在产出它）。

**2026-08-04 收口了 ① 的一半**（`64f4a96`，泓舟授权按推荐做）。两边都存在的别名对
只有 inc/dec（`aircon.on/off/set` 并不存在）。补的是**零覆盖影响且严格更严**的那部分：

- **所有 `forbidden_intents` / `forbidden_after` 补别名孪生（5 处）**——
  ⚠ **这半边此前是漏的**：`tu.hvac.dec-vs-inc` 禁 `hvac.inc` 却不禁 `aircon.inc`，
  模型选别名说反方向就抓不住。**别名分裂不只制造假红，也在削弱否定断言。**
- **已是多成员的 `any_of` 补别名（3 处）**，含 `ex.colloquial.{hot,cold}`——
  它们实测产出的正是 `aircon.dec`/`aircon.inc`，方向本来就是对的。

**当时没做**：`tu.hvac.{inc-vs-dec,dec-vs-inc}` 的 `any_of` 仍是单成员——放宽会把
`hvac.dec` 的单成员正例从 2 掉到 1，打破覆盖矩阵的 `positive: 2`。

**✅ 随后走了 ②（治本，`5d95ceb`），上面这个两难因此消失。**
删掉 `aircon.inc/dec`、统一到 `hvac.inc/dec`（能力面 78 → **76**；风速
`aircon.wind_speed.*` 保留，那不是同义词）。语料侧删除对它们的引用**是被迫的**
（intent 已不存在，契约的 typo 守卫会拦），不是为了绿而改 gold。
**live 复验**（重建 edge-orchestrator 后 registry 报 72 caps）：
`ex.colloquial.{hot,cold}` + `tu.hvac.{dec-vs-inc,inc-vs-dec}` **4/4 通过、
instability 0%**——这四条此前都是随机红（`tu.hvac.dec-vs-inc` 在 3 趟分布里 7/9）。
⚠ **这四条都不在 §10 那 18 条里**（`tu.hvac.*` 与 `ex.colloquial.hot` 是 `reviewed`，
根本不进 gate 选集；`ex.colloquial.cold` 是 `stable` 但三趟里本就全绿）——
它们红在**发现轨**。本节初版写成「§10 的 18 条里 A2 与 A9 的一部分由此收口」，
**那是没核就写的**，已删。
> **判据：说「这个修复关掉了那批账里的几条」之前，先核这几条在不在那批账里。**
> 两个集合都叫「不稳定」，但一个是 gate、一个是 discovery。

> **判据：一个动作只能有一个名字——尤其当能力面只靠名字区分时。**
> 而且它本来就是历史意外不是设计：`fast_intent` 的 aircon 分支里
> `close→hvac.off`、`set→hvac.set`、`open→hvac.on`，偏偏 `inc/dec→aircon.*`
> ——**同一个对象在同一段代码里有两套前缀**。

> 顺带一条元判据：**语料里那条 2026-08-02 的 `gold_revision_reason` 已经写对了根因，
> 却只改了两条用例。** 「发现了」和「改遍了」是两件事——发现根因时要问一句
> **「同一形态还有几处」**，而不是只修当时手上那条。

### 8.5 gate 全量 L1 读数：两条 P0 都绿了，但「stable 全绿」这条前置**仍未满足**——性质变了

修完之后跑了一趟 `--suite gate --layer l1 --live --repeat 3`（132 条 stable / 唯一输入 122，
`minimax:MiniMax-M3`）：

| | 读数 |
|---|---|
| 通过 | **110 / 116 证据单元（94.8%）** |
| 重复分类 | pass 110 · **stable_fail 2** · unstable 4 |
| 两条 P0 | `ex.homophone.aircon` → `hvac.on` ✅ · `nq.umbrella.both` → 两步齐 ✅ |
| 其它 | `post_validation_escape_rate` 0% · `fallback_plan_rate` **5.2%（6/116）** |

**两条 stable_fail 逐条复跑之后都翻面了**——这是本节最该记住的一句：

- `cp.adaptive.rain-umbrella`「明天要是下雨就提醒我带伞」：gate 那趟 3/3 红，红点**不在计划上**
  （计划正确、`complexity=adaptive` 正确），红在 **adaptive 第二轮空转**——`replans: [[]]`，
  拿到「明天有小雨」之后没补出提醒步。而**独立一趟里它通过**，检索名单逐字相同。
- `cp.dep.menu-then-order`「看看演示商户的菜单，然后点一份招牌」：gate 那趟 3/3 红，
  独立复跑 `[True, True, False]`＝`unstable`。消融四臂全部 `causal: none`，
  **`no-skills` 臂同样 stable_fail ⇒ 证伪了「被那条不相干的 `navigation-with-stop@vec:0.42`
  干扰」这个假设**（该假设是我先提出来的，写在这里因为它错得很典型：看见一条不该出现的
  guide 就当它是元凶）。

于是前置的性质从「**两条稳定红＝被测对象自己的错**」变成「**一批边界用例在采样噪声上抖**」。
两者都写不出正式 baseline（资格闸要求无 `stable_fail` **且**无 `unstable`），但排查方向完全不同。

> **判据：门禁跑不绿的原因可以只是方差。**
> 2026-08-03 晚立的判据是「规模闸绿 ≠ 门禁绿」；这一条是它的另一半——
> **门禁红也不等于有稳定缺陷**。一条 `stable` 用例在两个独立进程之间 3/3 ↔ 0/3 地翻，
> 说明它当初的「两趟都过」买到的置信度比想象中低。
> 晋级纪律要的两趟独立进程是**必要不充分**条件。

### 8.6 补 unseen 侧语料时撞到的两件事

补 10 条 unseen 用例（`6eb9bab`）时有两条账值得单独留：

1. **不变性关系不能建在会产生自由文本槽位的句子上。** 三条新写的 `relation: invariant`
   全红，而它们的**绝对 gold 全部满足**——差的是签名里的槽位：同一句话两次调用把
   `date` 从「明天」渲成「明天早晨」，`info.news` 自发多出一个 `limit: "5"`。
   `invariant` 比的是签名逐字相等，于是它量到的是**槽位渲染方差**，不是落域不变性。
   已改用绝对 gold 表达同一机制（理由逐条写进用例注释）。
   这是 §10.12 那条判据的第二次适用：**换一把尺子，噪声底就得重新量。**
2. **`SKILL_BUDGET` 的裁剪第二次现场现形**：`bd.nn-find-go.right.pharmacy` 的检索名单里
   `multi-day-trip@vec:0.45!clipped` 与 `conditional-reminder@vec:0.44!clipped`
   **同时**被裁。评审 §10.8 结尾记过「守卫应该守到 top-K 而不是 top-1」，
   当时没有消费方证据所以没动；这里是第二个现场。仍不改——但账厚了一层。

---

## 9. 尺子批（2026-08-04）：两条都是「这条断言在量它想量的东西吗」

> **只动尺子与测试基础设施**，生产与语料一个字节未改。
> 提交 `0fac11f`（隐私扫描范围）+ `5575257`（relation 签名分级）。

### 9.1 「本机 32 条既有红」是误判——真因是扫描走进了仓库的第二份 checkout

全量 `pytest` 跑出 **33 failed / 3901 passed**，唯一不在 `scripts/tests/` 的那条报
`duplicate privacy candidate entries: [memory_item, payment_order, voiceprint, ...]`。

**报错文案听起来像隐私登记表出了严重问题，真实原因只是磁盘上有第二个 checkout。**
`.claude/worktrees/intent-adversarial/`（Claude Code 的 `EnterWorktree` 建在那儿）里有
一份完整仓库，而 `_PRIVACY_EXCLUDED_DIRS` 写的是 `.worktrees`（带点）——对真实布局无效，
于是 `runtime/privacy_registry.py` 被读了两遍。

**同目录差分实证**：`test_e2e_stack_lease.py` 旧排除表 **10 failed** / 新排除表 **61 passed**。
同文件、同目录、同进程数。修复后全量 **3934 passed / 11 skipped / 0 failed**。

> **判据：差分证明的是「不是这批引入的」，不是「这是环境问题不用管」。**
> 上一次的对照法（`git stash` 后同目录重跑，对 clean HEAD 得出「32 条逐条一致」）
> **方法完全正确**，但两边共用同一个环境成因——差分按定义看不见它。
> 「逐条一致」只排除了「本批引入」这一个假设，它没有排除「两边都坏着」。

修法两道闸：① 名字清单补 `worktrees` / `.claude`；② **任何自带 `.git` 的子目录都按另一份
checkout 处理**——名字清单必然滞后于布局，而「有没有 `.git`」是 checkout 的**定义**
不是它的命名习惯（worktree 的 `.git` 是文件、clone 的是目录，`exists()` 两者都认）。
守卫 `scripts/tests/test_e2e_privacy_scan_scope.py` 5 条，先反验过（旧排除表下 4/5 红）。

### 9.2 一个签名不能同时服务两个方向相反的断言

`relation` 的四种关系共用同一个**含槽位的宽签名**，而它们对「差异」的要求方向相反，
于是**同一份槽位噪声在一边制造假红、在另一边制造假绿**：

| 方向 | 关系 | 槽位噪声的后果 |
|---|---|---|
| `∈`（主张「没变」） | `invariant` / `clause_commute` | **假红**——换个说法问同一件事，槽位文本本来就不同 |
| `∉`（主张「变了」） | `route_flip`（103 条）/ `context_override`（7 条） | **假绿**——槽位一抖就算「行为被换掉了」 |

**先量再改，假绿实测到一例**：`cs.more.research`「展开讲讲第二点」与 base `cs.more.news`
「第二条详细讲讲」**都落 `research.run`**——路由一模一样，`context_override` 的
`must_differ` 却判绿，因为两侧 slots 不同。发现轨 109 条对照里，variant 与 base 意图序列
完全相同的有 3 条，其中 1 条判了绿。

⚠ **这更正了规格 §22.6 末尾的定性**。那里把 `route_flip` 的假绿归给采样覆盖
（「要靠提高 `repeat_coverage` 才抓得住，**那是成本决策，不是口径问题**」）——
实测表明它**也是**口径问题，而且口径这一半是免费修的。
> **判据：「用采样覆盖兜住」是最后一招，先问这条断言是不是在量它想量的那个东西。**

裁定与影响面见规格 **§22.8**。一句话：主断言用**路由签名**，槽位另立
`relation.<type>.slots` 且只在「槽位本来就该相同」的场合生效（`clause_commute` 恒开；
`invariant` 仅当两侧原话相同）。**这不是放宽**——§22.6 的成果（`cp.reminder-weather.swapped`
的「明天早上八点」必须现形）原样保住，`route_flip` 反而变严，且红灯指得更准
（主断言与槽位断言分开，一眼看出红的是路由还是槽位）。

### 9.3 §8.6 那条账因此可以销了

§8.6 记的是「三条新写的 `relation: invariant` 因自由文本槽位必抖，已改用绝对 gold」。
**根因不在那三条用例，在口径**：语料里 22 条 `invariant` 有 **13 条两侧原话不同**，
它们一直在用一把量渲染方差的尺子量落域不变性。§9.2 修的正是这个。
那三条用例的绝对 gold 写法仍然成立（更直白地表达了机制），不回退。

### 9.4 自己犯的错：`git worktree remove` 把主仓的 `models/nlu/` 一起清空了

删掉 `.claude/worktrees/intent-adversarial/`（一份已全合入 main、无未提交改动的残留 checkout）
之后，主仓的 `models/nlu/` 与 `models/voiceprint/` **被清空**，两个 `.gitkeep` 显示为
`D`（deleted）。

- 全仓 Python 代码**没有任何 `rmtree`**，逐一排除了测试；
- 时间窗只包含 `git worktree remove` 这一条会做递归删除的命令；
- worktree **天生缺 `models/`**（运行手册环境坑一节早有此记载），因此此前很可能有人
  在 worktree 里建了指向主仓 `models/` 的 junction——**Windows 上递归删除会跟着 junction
  删到目标**。⚠ 机制是**推断**（worktree 已删，无法回验），但结果是确定的。

**实际损失**：`models/nlu/` 的 `edge_nlu.onnx` + `labels.json` + `vocab.json`，
**以及 `models/voiceprint/` 的 CAM++**。端侧日志留有铁证 NLU 当时在：
`edge NLU ready：8 域 / 83 对象`（2026-08-03T04:21:36）。

> ⚠ **本节初版写「CAM++ 无损」，那句是错的**，值得留档：当时的依据是
> `docker exec llm-gateway ls /app/models/voiceprint/` 还能看到那 28MB。
> 但 `llm-gateway` **不挂 models bind mount**——它那份是 `COPY` 烤进镜像的，
> 主机上那份**同样已经没了**，只是**下一次 `--build` 才会现形**。
> **判据：容器里还在，不等于源还在。** 查「有没有丢」要查**构建输入**，
> 不能查**运行时快照**——尤其当二者一个是 bind mount、一个是 COPY 时。
> （`hmi/public/models/` 的 KWS/VAD 不在 `models/` 下，确未受影响。）

两者均已恢复：`scripts/fetch-edge-nlu-base.sh` + 重训导出（§9.5）、
`scripts/fetch-voice-models.sh`（CAM++ 28,281,138 字节，与镜像内那份逐字节同大小）。

**影响**：端侧 NLU 只在 shadow 挡位（决议 disabled、不进决策链），**无功能回归**；
但 M5 P3b 的开工判据「错对象率 <0.3%」正是从 `nlu.shadow` span 算的，这条数据流断了。
运行中的容器还持着内存里的模型，**重启即失效**。
恢复＝`scripts/fetch-edge-nlu-base.*` 拉底座 + `scripts/train_edge_nlu.py` 重训导出。

> **判据：删除一个目录之前，先问它里面有没有指向别处的链接。**
> 「这份 checkout 已全合入、工作树干净」证明的是**它自己**没有未保存的工作，
> 不证明**删除它的动作**不会波及别处。两次安全检查（`main..branch` 为空、
> `status --porcelain` 为空）都做了，**而它们都只看仓库内容，不看文件系统形态**。

### 9.5 重训恢复端侧 NLU：模型回来了，**读数回不来** —— 训练脚本不记依赖版本

§9.4 删掉的 `models/nlu/` 已恢复：`scripts/fetch-edge-nlu-base.sh` 拉底座（35.5MB，
ModelScope 十几秒）+ `scripts/train_edge_nlu.py --lane both --export models/nlu`，
ONNX↔torch parity **8/8**（含端侧那份纯 Python 分词器）。

**但读数和被删的那版对不上：**

| 车道 | 被删的那版（`86ec5d7` 报告，2026-07-30） | 重训（2026-08-04） |
|---|---|---|
| transfer domain / **object** / both | 86.0% / **64.9%** / 62.4% | 87.1% / **55.8%** / 54.4% |
| holdout domain / **object** / both | 99.1% / **95.8%** / 95.4% | 97.8% / **86.9%** / 85.9% |

逐个排掉变量：**语料两次都是 8579 条**、**holdout 划分两次都是 7339 / 1240**、
**种子在 `run_lane` 里每车道重置**（不是全局设一次，所以与 `--lane both` 的车道顺序无关）、
超参一致。剩下的唯一变量是 **ML 栈版本**（现为 torch 2.13.0+cpu / transformers 5.14.1 /
onnxruntime 1.28.0 / numpy 2.5.1）。

> **判据：同语料同种子同划分，读数仍可以差 9 点——训练产物的可复现性要靠固定依赖，
> 不是靠固定种子。** 而这件事之所以查了半天才归因，是因为**报告 meta 里根本没记版本**。
> 项目在 skills eval 上早有同一条纪律（「跑批条件全进 `meta`，缺了跨 run 对比就是拿
> 苹果比橘子」），**训练侧漏了**。已补 `_toolchain()` 进 `payload["toolchain"]` 与
> 报告抬头，并在抬头写明「跨 run 比读数之前先对这一行」。

**影响与不影响**：端侧 NLU 只在 shadow 挡位、决议 disabled，**没有功能回归**；
θ 门控（`nlu_gate`）按真概率保留，模型变弱表现为**保留比例下降**而不是错得更多
（transfer θ≥0.5 保留 27.2% 精度 90.8%）。M5 P3b 的开工判据「错对象率 <0.3%」是从
`nlu.shadow` 真实流量算的，**用这版模型算出来会偏悲观**——读那个数时要一并读本节。

⚠ **未做**：没有去追回原来的 95.8%。理由是代价与收益不成比例（要么把 ML 栈钉回
2026-07-30 的版本，要么调参碰运气），而这条链路的产品决议本来就是「刻意不接执行侧」。
账留在这里，不假装它等价。

---

## 10. gate 稳定性分布（2026-08-04，3 趟 × repeat 3 = 9 样本/条）

§8.5 立的判据是「门禁红也不等于有稳定缺陷——一条 `stable` 用例能在两个独立进程之间
3/3 ↔ 0/3 地翻」。**跑三趟之后，那句话的机制部分是错的。**

| 分类 | 条数 | 占比 |
|---|---|---|
| 稳绿（三趟都 pass） | 97 | 83.6% |
| **跨进程翻面（有趟全绿、有趟全红）** | **0** | **0%** |
| 稳红（三趟都 stable_fail） | 0 | 0% |
| 进程内抖（某趟三次不一致） | 18 | 15.5% |
| 其它（第 2 趟一次网关降级，`planner_unreached` 正确归成基础设施） | 1 | 0.9% |

**零跨进程翻面。** 门禁不绿的原因不是「进程级状态」，而是这 18 条**本来就不稳定**——
而它们晋级时都过了。

### 10.1 根因是算术：「两趟独立进程」只买到 **2 个样本**

`suites.yaml` 的 `gate.normal_repeats: 1`——**一条通过的用例每趟只跑 1 次**，
`failure_repeats: 3` 只在失败后才扩。所以「两趟独立进程都过」＝ **2 个样本**。

> 一条真实通过率 93% 的用例，2 个样本全过的概率是 **86%**；6 个样本全过降到 65%。
> **判据：「独立跑两趟」说的是进程数，不是样本数；置信度由样本数决定。**

已机制化：`stabilized_at >= 2026-08-04` 的晋级必须声明
`provenance.stabilized_samples ≥ 6`（两趟各 `--repeat 3`），
契约层校验 + 3 条测试（含 `True` 是 `int` 子类那个坑）。
**存量 132 条按日期豁免**——一次性判它们违约既不真实也不可执行，它们的账在 §10.3。

### 10.2 18 条不是随机分布的，**一半以上落在早就已知的两个最弱攻击族上**

| 攻击族 | 条数 | 机制 |
|---|---|---|
| **A4 组合** | **7** | adaptive 第二轮 ×2 · 并列 ×2 · 依赖两步 ×2 · 换序 ×1 |
| **A9 表达攻击** | **4** | 同音字 ×2 · 漏标点多意图 ×2 |
| A5 上下文 | 3 | 陈旧历史 ×2 · 指代 ×1 |
| A3 否定/时态 | 3 | 取消其一 · 否定域 · 时态 |
| A2 | 1 | 主体翻转 |

**A4 + A9 = 11/18。** 而 P1 待办里挂着的正是「A4 83.3% / A9 83.0%，两个最弱攻击族」。
所以**「门禁 18 条抖」与「A4/A9 最弱」是同一件事的两种读法**——前者是后者在门禁上的
表现形式。这条一并回答了「A4/A9 新读数」那条待办：它的读数就是这张表。

逐条通过率（`unstable` 按下界 1/3 计，所以是**保守下界**）：

| 用例 | ≥ | 用例 | ≥ |
|---|---|---|---|
| `nq.dinner-music.drop-music` | 1/9 | `ex.nopunct.two-intent` | 5/9 |
| `cp.weather-music.swapped` | 3/9 | `cp.adaptive.range-check` | 7/9 |
| `ex.nopunct.three-intent` | 3/9 | `cp.dep.search-then-detail` | 7/9 |
| `cp.adaptive.rain-umbrella` | 5/9 | `cp.hvac-news.base` | 7/9 |
| `cp.dep.menu-then-order` | 5/9 | `cp.volume-forecast.base` | 7/9 |
| `cs.another.trip` | 5/9 | `cs.news.stale-trip` | 7/9 |
| `cs.weather.stale-restaurant` | 5/9 | `ex.homophone.charging` | 7/9 |
| `ex.homophone.navigate` | 5/9 | `nq.hvac.keep-volume` | 7/9 |
| | | `nq.match.past` / `os.charge-place.phone` | 7/9 |

### 10.3 **不降级**——理由，以及它的代价

按新判据（6 样本）这 18 条都不达标，机械做法是降回 `reviewed`。**实测代价**：
`stable` 132 → 114、唯一输入 **122 → 104**，比 `min_cases=120` 差 16 个，
`--strict` 重新变红——等于把 2026-08-03 晚刚收口的规模 P0 原样打开。

但**降级会删掉门禁对最有价值那片区域的覆盖**：这 18 条不是「gold 写错了」，
是**被测对象在它已知最弱的两个攻击族上不稳定**。把它们移出门禁，门禁就看不见
A4/A9 了——而那正是它该看的。

> **判据：`unstable` 是被测对象的属性，不是语料质量的属性。**
> 「这条用例难」和「这条用例坏」是两件事（§22.7 已立），这里是第三种：
> **「这条用例对，而系统在它上面不稳定」**——它既不该被修 gold，也不该被移出门禁。

**代价必须写明**：正式 baseline 的资格闸要求无 `stable_fail` **且**无 `unstable`，
所以在这 18 条被**产品修稳**之前 baseline 写不出来。这不是尺子的账，
而且它现在有**明确的攻击目标**：7 条组合 + 4 条表达攻击，且已按机制聚成族
（同族两三条往往一个修法，同 2026-08-03 深夜那批 guide 修复的杠杆）。

### 10.4 顺带修掉一条我自己写太严的断言

§9.2 新加的 `relation.invariant.slots` 第一版用**逐字相等**，实测把
`cs.weather.stale-restaurant` 判红：两侧都是「今天天气怎么样」，base 给 `{date: 今天}`、
带陈旧历史的变体给 `{}`——**少一个可从原话恢复的可选槽位不是「历史串进槽位」**。
改成**子集语义**：variant 不得引入 base 从没产生过的 `(intent, 槽位名, 取值)`。
`clause_commute` 保持逐字相等（同样的词换顺序，槽位本就该完全相同）。

> 判据：**「没变」这个命题要分清「没多出来」和「没少」**——串味是**引入**，不是缺失。

---

## 11. L3 不再是「运行器的账」——它跑通了（2026-08-04）

规格 §21.8-1 与运行手册 §8 一直写着：**「L3 证据未取得——既有 e2e 运行器在本机
`lease_protocol` / `identity_cleanup` 失败」**，并把它归成「运行器的账，不是对抗套件的」。

**实测：那句话已经不成立。** 直接跑对抗套件用的那条命令
（`python scripts/run_e2e.py --id e2e_journeys --provider minimax --model MiniMax-M3`）
**跑完了**，产出完整的 `journeys_report.json`：

| | |
|---|---|
| 回归级 | **15/15** |
| 目标级 | **14/20** |
| 红灯 | B1-2 · B2-3 · B3-1 · B3-3 · B5-1 · B5-2 |
| **对抗套件 L3 选集（A1-1 / A1-2 / A2-2a / A5-3 / B2-1 / B3-3）** | **5/6 通过，只有 B3-3 红** |

**极可能是 §9.1 那个修复顺带解开的**：`lease_protocol` 归属的
`scripts/tests/test_e2e_stack_lease.py` 正是被隐私清单重复读打红的那 10 条，
而 `run_e2e.py` 走同一套 manifest 校验。⚠ 这条因果是**推断**——没有在修复前重跑过
运行器，所以不写成定论。但两件事的时间关系与根因通路都对得上。

> **判据：把一条红归成「别人的账」之后，要留一个复查它的触发器。**
> 这条账挂了整整两天，期间没人再跑过那条命令——**归因一旦写进文档就没人重验**，
> 而它可能早就被别的修复带好了。同族判据第二次出现（§9.1 的「差分证明的是
> 『不是这批引入的』」也是「结论写下来之后没再被质疑」）。

**baseline 前置因此重排**：

| 前置 | 旧状态 | 现状 |
|---|---|---|
| L3 证据 | 「取不到，属运行器」 | **取得了**；L3 选集 5/6，唯一红是 `B3-3` |
| gate 全量无 `stable_fail`/`unstable` | 18 条不稳定（§10） | 不变，仍是主要前置 |

### 11.1 `B3-3` —— 现在唯一挡着 L3 的那条

`B3-3 记忆×车控参数化（调到我喜欢的温度）`：

    → 记住，我最喜欢的空调温度是26度   好的，我记下了——您最喜欢的空调温度是26度。
    → （sleep 40，等抽取）
    → 把空调调到我喜欢的温度          **「度」**          ← 整句回复只有一个「度」字
    ✗ 终态车况: hvac_temp=20 期望 26

**症状本身就是线索**：回复只剩一个「度」字，像是话术模板 `{value}度` 拿到了空值；
终态 20 不是 26，也不是用户说过的任何数。**存是存进去了**（第一轮回话确认了 26），
所以问题在**召回或填值**那一段，不在抽取。

**不归因给本轮改动**：本轮动过的端侧只有 `aircon.inc/dec` → `hvac.inc/dec`，
而这条用的是 `hvac.set` + 记忆召回填值；两者不共享代码路径。⚠ 但也**没有反验**
（没在改动前跑过 journeys），所以这句是「查过看起来无关」，不是「证明无关」。

其余 5 条红（B1-2 / B2-3 / B3-1 / B5-1 / B5-2）不在 L3 选集里，其中 B5-1/B5-2 的
失败话术是「周边搜索服务暂时不可用」「暂时无法确定…对应的具体地点」——**指向高德侧**，
不是落域问题。历史记录里 journeys 曾达 target 20/20（`agents-history.md` §1），
所以这 6 条要逐条重跑定性，**别拿一次跑批的红当结论**（本套件自己的第 1 步纪律）。

### 11.2 `B3-3` 定性完成：两条独立的账，第二条更值得记

从 obs 里把同一句话的两次记录拉出来对比（`turns` 表，`user_text like '%我喜欢的温度%'`）：

| | speech | intents | actions | `cloud.planning` 的 plan |
|---|---|---|---|---|
| 通过那次 | `26度` | `hvac.set` | 1 | `slots: {"temperature": "26"}` |
| **失败那次** | **`度`** | `hvac.set` | 1 | **`slots: {}`** |

**① 模型算出来了却没写进槽位。** 失败那次的 `llm_raw` 里
`goal` 写的是「把空调调到用户最喜欢的温度（**26度**）」——**26 就在 goal 里**，
记忆召回完全正常，抽取也正常（第一轮回话确认过）。丢的是**从 goal 到 slots 那一步**。

> 这是「**goal 是免费的对照物**」的第三例（前两例：weather-outing 的 goal 说推荐而
> steps 无推荐步、组合意图漏第二步）。而这一例**机器可判到值一级**：
> **goal 文本里有数字、对应 slots 为空** —— 比前两例的「缺步」更容易写成检测器。

**② 更值得记的：Outcome Verifier 判了 `sat`。**

    step.verify {"step_id":"s1","intent":"hvac.set","mode":"state_match","verdict":"sat"}

因为 `capabilities.py` 的 `_VERIFICATION["hvac.set"]` 只声明了
`{"hvac_on": "true"}`——**「设定为 N 度」这个动作的验证，核的是「空调开着」**。
于是「set 了但没设成」在 Verifier 面前是成功的。

> **判据：验证的强度必须匹配主张的强度。** `hvac.set` 主张的是「温度=N」，
> 而它的 state_match 只核了一个布尔开关——这是「**断言否定命题守不住肯定性质**」
> （M5 P3 收尾立的判据）在执行验证面上的同族：**核了一个比主张弱的东西，
> 等于没核**。M2 Verifier 首验时抓到的是「挂点漏了执行路径」，这次是
> **挂上了、但核的不是那件事**——比漏挂更难发现，因为它一直在报绿。

**两条修法都有代价，未做，理由写在这里**：

- 让验证核到温度，要 `Verification.expect` 支持**动态期望**（取该步 slots 的值）。
  当前 `_verification_for(intent)` 是**按 intent 静态构造**的 Struct，拿不到 slots
  ——这是 M2 Verifier 的协议级扩展，不是一行。
- 让「无温度值的 `hvac.set`」不执行（VAL 回 NEED_SLOT 追问几度），
  **会与 B3-3 自己的 gold 冲突**：该用例明确 `speech_not: [多少度, 请问, 几度]`
  ——它要的就是「不问，直接按记忆做」。所以这条要先由产品裁定「记忆缺失时是追问还是兜底」。

**顺带**：speech 渲染成单字「度」本身是用户可见缺陷（模板 `{value}度` 拿到空值）。
它同时是**最便宜的探针**——一个只剩单位没有数值的回复，肯定有一个槽位是空的。

---

## 12. 产品批（2026-08-04 下午）：§10 那 18 条的取证与修复

> §10 把 18 条不稳定用例量清了，但**只量到分布，没量到根因**——它给的是
> 「A4 占 7、A9 占 4」这种统计口径。本节是逐条拉证据之后的结果：**根因过半不是模型抖，
> 是那个域压根没有知识**。

### 12.1 先取证：统计口径看不见的三条已经变成稳定红

`--suite gate --layer l1 --live --repeat 5`，18 条 + relation 自动带上的 5 条 base
（`retrieval_degraded 0/249`，健康跑）：

| 分类 | 条数 |
|---|---|
| pass | 13 |
| **stable_fail** | **3**（`cp.dep.menu-then-order` 0/5 · `nq.dinner-music.drop-music` 3/5 · `os.charge-place.phone` 3/5）|
| unstable | 7 |

§10 那张表把 `cp.dep.menu-then-order` 记成 ≥5/9、`os.charge-place.phone` 记成 ≥7/9，
而单独拿 5 个样本跑它们分别是 **0/5** 和 3/5。

> **判据：`unstable` 是一个混装标签，拆开之前不知道里面装的是什么。**
> 「进程内三次不一致」既可能是 51% 的边界句，也可能是 10% 通过率的稳定缺陷——
> 前者要等，后者要修，而**分布口径把两者压成了同一个词**。
> 量分布是为了排优先级，不能代替逐条拉证据。

### 12.2 过半根因是「这个域没有知识」

逐条看诊断行里的 `exemplars=` 与 `skills=`，形态高度一致：

| 用例 | 首偏离 | 根因 | 产物 |
|---|---|---|---|
| `cp.dep.menu-then-order` **0/5** | 只出 `shop.menu` 漏第二步 | **整个 shop 域一条范例都没有**（`exemplars=[]`），且没有任何知识讲「要看过菜单才知道点哪款」 | 新建 `skills/exemplars/shop.yaml`（8 条）+ `skills/guides/shop-order-flow.yaml` |
| `nq.match.past` 1/5 | 落 `info.search` | 7 条赛事范例**要么带赛事专名要么带球队名**，没有一条是「那场比赛」这种**泛指**——而泛指正是时态攻击的载体 | `info#42`「昨天那场球谁赢了」 |
| `ex.homophone.navigate` | 落 `navigation.search_poi` | 50 条导航范例的动词**一律写作「导航」**，同音字把词法通道整条打断（对既有范例最高 0.167） | `navigation#26`「到航去机场」 |
| `ex.homophone.charging` | 落 `nearby.search` | 同音字 +**两条台账裁定在这句话上打架**（见 §12.3） | `charging#10/#11` + 新台账条目 |
| `os.charge-place.phone` 3/5 | **空计划** | 模型看得出「不是给车充电」，但没有任何知识告诉它接下来该干什么 | `nearby#27`「找个能给手机充电的咖啡馆」 |
| `nq.dinner-music.drop-music` 3/5 | 多出 `media.pause` | policy 已写「不得规划 X 的反面」**且当轮确实注入了**——是「进了没用对」：policy 的例子全是车控 open/close 对，而媒体的反面（`pause`）读起来像「顺手收拾一下」 | `nearby#28`「找家火锅店，歌就不用放了」 |

> **判据：`exemplars=[]` 有两种意思，先分清是「检索没够着」还是「这个域是空的」。**
> 2026-08-03 `ex.homophone.aircon` 是后者（hvac 域天然空白），本批 `shop` 是同一形态的
> 第二例，而 `nq.match.past` / `ex.homophone.navigate` 是**第三种**：域里有范例，
> 但**没有一条覆盖这个说法族**。三种的修法都是投范例，但**投什么完全不同**。

### 12.3 一条边界的真正位置：两条既有裁定的相交处

`ex.homophone.charging`「附近哪里能冲电」落 `nearby.search`，检索名单里最近的是
`nearby#25`「油不多了，附近哪儿有加油站」——那条教的正是「附近哪儿有 X → nearby」。

台账里两条裁定**各自都对，在这句话上却正面打架**：

| 裁定 | 说的是 |
|---|---|
| `nearby-navigation.find-vs-go` | 边界**按动词划**，「找/搜/附近哪儿有」一律 nearby.search（连加油站都归 nearby）|
| `charging-nearby.charger-vs-*` | 充电桩归 `charging.find` |

**缺的不是任何一条，是它们相交处的那一条。** 2026-08-04 泓舟裁定并登记
`charging-nearby.charger-vs-gas-station`：**「按动词划」是通则，充电桩是特例**，
理由是**能力差异不是措辞差异**（charging.find 读真实 SOC、按空闲排序、可作导航途经点；
加油站没有这层车端状态耦合，所以它留在通则里）。配套双向对照 4 条
（`bd.cn-charger-gas.*`，两侧刻意同句式只换对象）。

> **判据：两条规则都对，不代表它们的交集有定义。** 台账登记的应该是**边界**，
> 而边界只在两条规则相遇的地方才存在——各自成立的两条裁定之间可以留着一个洞。

⚠ 这条边界**是被门禁逼出来的**：写下「附近哪儿有冲电的地方」这条范例时，
`test/eval_exemplars.py` 当场报 IDF-Dice 0.361 ≥ `lex_min` 未裁定并阻断。
先用规避写法（「哪儿能冲电」，与加油站只有 0.118）跑通，裁定拿到后再改回碰撞写法。

### 12.4 两条真产品缺陷：引用写全了，依赖没声明

`cp.dep.search-then-detail`「搜一下附近的火锅店，再看看第一家的详情」的计划其实是对的：
`nearby.search` → `nearby.detail{poi_id: s1.data.items.0.id}`。红在 **`depends_on` 是空的**。

这不是断言挑剔，是**执行期真会坏**：`executor._topo_layers` 只看 `depends_on`，
两步会被排进同一层**并行下发**；s2 去取 s1 结果时 s1 还没回来，引用解析成 None，
字面量 `s1.data.items.0.id` 当成真 POI id 发给了下游。

同一条链上还有第二个缺陷：那串路径**同时**写在 `slots` 和 `slot_refs` 里，
而 `_resolve_slot_refs` 的「已有值不覆盖」把它当成已有值**跳过**了。

两处都按归一修（`PlanBuilder._derive_depends_on_from_refs` + executor 的同值判定），
与「`depends_on` 非 list 归空」「intent 唯一归属时归位」同一族：
**把模型输出里自相矛盾的部分补成一致，不发明任何路由。**

> **判据：引用了另一步的输出，就是依赖的定义。** 一份计划同时说「我要用 s1 的结果」
> 和「我不依赖 s1」时，它自相矛盾，不是有歧义——归一有唯一正确解。
> 判形状用 `<步骤id>.data.` 而不是「含点号」：普通槽值里点号很常见。

### 12.5 「设为 N 度」缺 N 时追问不猜（泓舟裁定）

§11.2 把这条写成「两种修法互斥」。**拆开之后并不互斥**——「planner 把值弄丢了」和
「记忆里确实没有值」是两件事，B3-3 属前者：

- **记忆里有值** → 值必须落进 `slots`（`implicit-vehicle-control` 补一句：
  只写在 goal 里等于没写，执行侧只读 slots）；
- **记忆里确实没有值** → `NEED_SLOT` 追问几度。

落地形态是**知识库声明 + 通用消费**：`commands.yaml` 的 `aircon` 加一行
`value_required_operates: [set]`，`EdgeCallExecutor._missing_required_value` 通用读取
（零对象/意图字面量，三个不触发情形：带 `mode`、带 `attr`、对象没声明 `attrs`）。
B3-3 的 `speech_not: [多少度, 请问, 几度]` 因此**不受影响**——那一轮记忆里有 26。

顺带修掉那个单字「度」：它是 `{value}度` 拿到空值的渲染，现在那条路径根本不会执行。

**并补一个检测器**（只观测不改行为）：`cloud.planning` span 新增
`goal_value_dropped`——goal 文本里有数字、而全部 step 的槽位里一个数字都没有。
这是「goal 是免费的对照物」的第三例，也是**第一例机器可判到值一级**的；
前两例（goal 说推荐而 steps 无推荐步 / 组合意图漏第二步）只判得到「缺步」。

### 12.6 尺子盲区：两道门禁只认 manifest 里写着的能力

写 shop 范例时 `test/eval_exemplars.py` 报「`shop.menu` 不存在于任何 manifest」，
写 shop guide 时 `test/eval_skills.py` 报同样的话。真相是 `mcp-bridge` 的
`capabilities: []` **是有意的**——它的能力由 `servers.yaml` 准入清单在启动期合成。

后果不是「少校验一点」，是**整个 shop 域既写不了范例也写不了 guide golden**，
而 `cp.dep.menu-then-order` 恰恰是那个域里 0/5 的稳定红。两道门禁的 `_known_intents`
已补读 `agents/*/servers.yaml`。

> **判据：「能力从哪里声明」和「能力写在哪个文件」是两件事。**
> 清单只认一种声明形态时，另一种形态的域会**安静地**失去整层机制——
> 没有报错，只有「这个域一直没有范例」。

### 12.7 自己踩的两个坑

**① 一趟降级的跑批差点把结论全带偏。** 修完第一次复验，读数是
`cp.dep.menu-then-order` 0/5→3/3、而 `cp.adaptive.rain-umbrella` 4/5→**0/3**，
当时已经开始怀疑是自己改的 guide 把 adaptive 弄坏了。看 `meta` 才发现
`warmed_exemplars: 0`、`retrieval_degraded: 196/196`——**embed 整趟不可用**，
那一跑退化成纯词法档，`@vec` 检回的范例一条都没有。套件自己是诚实的：
两条 `[infra]` 行 + `EXIT=2`，是我的 wrapper 用 `echo "EXIT=$?"` 把退出码吃掉了。

> **判据：读结论之前先读 `meta`，这条对「变好了」和「变坏了」一样适用。**
> 运行手册 §4 第 0.5 步写的是「绿灯也要看」，这次差点栽在**红灯**上——
> 降级同样会制造假红，而假红比假绿更容易被当成「我刚才改坏了」。

**② 加知识和加 policy 一样会挤掉知识。** 给 `conditional-reminder` 补完第二轮判据，
它的 `knowledge` 从 1215 涨到 **1504** 字符，于是在 `SKILL_BUDGET=2600` 下
**整条被裁出注入**——`ki.conditional-reminder.hit` 当场红（L0，零网络）。
压回 1202 后恢复。

> **判据：预算是全局的，「我只是把一条知识写清楚点」和「我加了一条常驻 policy」
> 对预算是同一件事。** 既有教训只记了后者（policy 常驻挤 guide），
> 这次是**同一条知识把自己挤了出去**。补完知识必须回头量一次注入块长度。

### 12.8 复验读数（两趟独立进程 × `--repeat 3` = 6 样本/条）

两趟都健康（`retrieval_degraded 0`、`warmed_exemplars 239`）：

| | 修前（1 趟 × repeat 5） | 修后 B1 | 修后 B2 |
|---|---|---|---|
| pass | 13 | **20** | **22** |
| stable_fail | **3** | **0** | **0** |
| unstable | 7 | 3 | 1 |

18 条里 **14 条 6/6 全过**，其中三条原 `stable_fail` 全部转绿
（`cp.dep.menu-then-order` 0/5 → 6/6、`nq.match.past` 1/5 → 6/6、
`os.charge-place.phone` 3/5 → 6/6）。**剩 4 条**：

| 用例 | 6 样本 | 现状 |
|---|---|---|
| `cp.adaptive.rain-umbrella` | 5/6 | 再规划轮偶发 `done:true`（观察里写着「明天有小雨」却读成条件不成立）|
| `ex.homophone.charging` | 5/6 | 范例已检回（`charging#10@lex:0.43`）仍偶发选 nearby——**不是检索问题** |
| `nq.dinner-music.drop-music` | 5/6 | 失败形态**变了**：从「多出 media.pause」变成「空计划」（否定被过度应用）|
| `cs.news.stale-trip` | 4/6 | ⚠ **修前是 5/5**。红在 `relation.invariant.slots`（`topic:"今天"`，base 没产生过），与本批改动无关——见下 |

⚠ **诚实边界，三条**：

1. **修前 5 样本 / 1 进程，修后 6 样本 / 2 进程，采样不同口径不同。** 三条原
   `stable_fail`（0/5、1/5、3/5 → 6/6）是强证据；原本就 4/5 的那些转 6/6 是弱证据，
   其中一部分完全可能是运气。
2. **`cs.news.stale-trip` 从 5/5 变成 4/6，不许说成「无关」。** 查过：它红在
   `topic:"今天"`，而「今天」来自用户原话不是陈旧历史——是 planner 在同一句话上的
   槽位取值方差，本批没有任何改动碰 info.news。但**「查过看起来无关」不是「证明无关」**，
   这条挂账。它同时暴露 `invariant.slots` 的一个边：`supp(base)` 只有 3 个样本时，
   base 自己的槽位分布覆盖不全，**变体引入的新取值可能只是 base 没抽到**。
3. **6 样本是晋级线不是「修好了」。** 按 §10.1 的算术，真实通过率 93% 的用例
   6 样本全过的概率是 65%——这批读数配得上「可以进 baseline 前置的下一轮」，
   配不上「这条已经稳了」。

### 12.9 第三个自己踩的坑：清掉「死条目」，它其实没死

顺手清理别名统一的残留时，把 `LOCAL_INTENTS` 里的 `aircon.inc/dec` 删了，理由写的是
**「`_to_structured` 早已改产 `hvac.inc/dec`，这两项从此没有任何产出方」**。

**那句话是没核就写的。** `5d95ceb` 只改了 `_structured_to_legacy` 一处，而
`_to_legacy_name`（**体感冷热复合意图**走的那条路）仍在拼 `f"aircon.{operate}"`——
**同一个「aircon 对象 + inc/dec」在两个命名点各产一个名字**。

它长期不显形，恰恰因为 `LOCAL_INTENTS` 把两个名字都收着。删掉别名的那一刻，
「我感觉有点冷帮我把空调温度和风速都调一下」当场 `is_local=False` **整句上云**，
`test_climate_feeling_*` 变红——而且**失败之后整个 pytest 挂住**
（`_drive` 的流在断言炸掉时没收干净），于是它一开始看起来像「测试环境卡了」。

修法是把两处一起改到 `hvac.inc/dec`，并**把规范名补进 `LOCAL_INTENTS`**——
那张表此前**只登记了别名**，所以治本改完之后端侧反而认不出自己产出的规范名。

> **判据一：清理死条目之前，先证明它真的死了。**
> `grep "aircon.inc"` 搜不到 `f"aircon.{operate}"`——**搜字面量搜不到拼接点**。
> 一个名字有几个产出方，要按「谁在拼这个前缀」找，不是按「谁写了这个字符串」找。

> **判据二（第二次适用）：发现根因时要问「同一形态还有几处」。**
> 2026-08-04 上午那批已经立过这条（`gold_revision_reason` 写对了根因却只改了两条用例）。
> 这次是它的反面样本——**清理死条目恰好是那个会把漏网点照出来的动作**，
> 而我在照出它之前先给它写了一句「没有产出方」的定性。

### 12.10 L3 复跑：`B3-3` 转绿，红灯从 6 条降到 2 条

修完之后重跑 `python scripts/run_e2e.py --id e2e_journeys --provider minimax --model MiniMax-M3`
（edge-orchestrator / cloud-planner 已按改动重建）：

| | 2026-08-04 上午 | 本批之后 |
|---|---|---|
| 回归级 | 15/15 | **15/15** |
| 目标级 | 14/20 | **18/20** |
| 红灯 | B1-2 · B2-3 · B3-1 · **B3-3** · B5-1 · B5-2 | **B3-1 · B3-2** |

**`B3-3` 通过**：「把空调调到我喜欢的温度」→ 回复「26度」、终态 `hvac_temp=26`。
上午那条「回复只剩一个『度』字」的形态没有再出现。

⚠ 顺带这是 `$slot:` 动态期望的**端到端活证据**，不只是单测：obs 里那一轮的
`step.verify` 是 `hvac.set / state_match / **sat**`。`sat` 只在**全部键都解析成功
且都匹配**时才产生（任一键解析不出就是 UNKNOWN），所以它证明了动态键真的走通了
registry → planner → executor → 车况镜像这一整条，而不只是「声明写进去了」。

**P2「另 5 条 journeys 红灯逐条重跑定性」由此得到大半答案**：
`B1-2` / `B2-3` / `B5-1` / `B5-2` 本次**全部通过**——它们是方差，不是稳定缺陷
（`B5-1/B5-2` 上午的失败话术「周边搜索服务暂时不可用」本来就指向高德侧）。

**仍红两条，性质不同：**

- **`B3-1` 行程×天气反向驱动修改**：第三轮「哪天要下雨的话，把那天的安排换成室内的」
  回了一段天气解释 + `forecast` 卡，而 gold 要 `trip_itinerary` 卡。
  **查了天气、没改行程**——这是「条件依赖第二轮该出被推迟的那件事」的**同一族**
  （与 `cp.adaptive.rain-umbrella` 同机制，只是发生在 trip 域）。
- **`B3-2` 低电量长途导航主动补能**：「导航去广州塔」被解析成
  **「广州仄仄科技有限公司」**——地标解析错，与落域无关；话术因此也没带
  续航/电量/充电字样。**这条上午不在红灯名单里**，是本次新出现的。

⚠ 按本套件自己的纪律（**别拿一次跑批的红当结论**），这两条各自单独重跑了一次，
结论记在下一节。

### 12.11 `B3-1` / `B3-2` 单独重跑的定性：一条是 gold 依赖天气，一条在高德侧

两条各自单独重跑一次（`E2E_JOURNEY_IDS=B3-1,B3-2`），**都复现了红**，但复现出的**形态
和第一次不一样**，而这个差异本身就是定性：

**`B3-1` —— gold 依赖当天真实天气，它只有在「真的下雨」时才可判。**

| | 第一次 | 单独重跑 |
|---|---|---|
| 天气 | 「珠海**今天有中雨转雷阵雨**」 | 「珠海**这几天都没有雨**」 |
| 回复 | 天气解释 + `forecast` 卡 | 「行程不用调整」，**无卡** |
| 判定 | ❌ `cards_any` 要 `trip_itinerary` | ❌ 同一条断言 |

**第二次的行为是对的**：用户说「哪天要下雨的话，把那天的安排换成室内的」，而这几天不下雨
⇒ **不改行程才是正确答案**。可 gold 无条件要求出 `trip_itinerary` 卡，于是正确行为也被判红。
第一次才是真缺陷（**真的下雨了，仍然没改行程**——与 `cp.adaptive.rain-umbrella` 同机制，
只是发生在 trip 域）。

> **判据：断言的可判性不能依赖跑批当天的外部世界。**
> 这是「上下文依赖 badcase 不标扁平 gold」（2026-08-02 weather-outing 那条）的**第二例**，
> 而且更隐蔽：那次是上下文，这次是**真实天气**——同一条用例在两天里量的不是同一件事，
> 红灯也因此指向两个完全不同的结论。修法要么给这条用例注入确定性天气，
> 要么把 gold 写成条件式（下雨→改行程 / 不下雨→明说不用改），**不能两者都不做**。

**`B3-2` —— 两次都卡在「广州塔」这个地标解析上，与落域无关。**

| | 第一次 | 单独重跑 |
|---|---|---|
| 「导航去广州塔」 | 解析成**「广州仄仄科技有限公司」** | 「暂时无法确定「广州塔」对应的具体地点」 |

一次错认、一次认不出，**两次都没走到「低电量该不该提补能」那一步**——那才是这条用例
想测的东西。它指向高德侧的地标解析（同 `docs/design/2026-07-07-complex-intent-landmark-parking-fixes.md`
记过的「地标解析并发挤高德 QPS」一族），**不是落域问题，不进本套件的账**。

⚠ 它**上午不在红灯名单里**（上午 6 条红是 B1-2 / B2-3 / B3-1 / B3-3 / B5-1 / B5-2），
所以它是本次新出现的；但连续两次同一位置失败，**不是采样方差**。

**P2「另 5 条 journeys 红灯逐条重跑定性」到此收口**：
`B1-2` / `B2-3` / `B5-1` / `B5-2` **本批全部通过** ⇒ 方差，不是稳定缺陷；
`B3-1` 是**语料问题**（gold 依赖真实天气）＋一次真缺陷（下雨了没改行程）；
`B3-2` 在**高德侧**。

### 12.12 收尾自查撞到的：`pytest test/`（目录粒度）不是有效基线

全量根跑（`python -m pytest --import-mode=importlib`，项目文档基线命令）**只有 1 条红**
——而且那条是**门禁按设计拦我的**（台账条数断言 20，我加了一条 ruling；它的原注释就写着
「这里红一次就是提醒『裁定加了，兑现物加了吗』」。已先证明兑现物在场
—— `validate_boundary_coverage` 零错误、左右各 2 条 `reviewed` 对照 —— 再改成 21）。

但收尾自查时顺手跑了一次 `pytest test/`，红 **15** 条，差点当成回归。拆开是两件事：

- **3 条是我自己的环境变量**：`test_eval_intent_adversarial_cli.py` 用
  `subprocess(text=True)` 按系统默认编码解子进程输出，父进程设了 `PYTHONIOENCODING=utf-8`
  就 `UnicodeDecodeError`。**2026-08-03 记过同一个坑，我又踩了一遍**——
  **为了自己看得舒服而改的环境变量，也是环境变量。**
- **12 条是裸名 import 被劫持**：`test/support/intent_adversarial_runtime.py` 的
  `from server import EdgeOrchestratorServicer` 解析到了 **`llm-gateway/server.py`**
  （回溯里直接印出「LLM Gateway gRPC 服务」的模块 docstring）。触发方是某条先跑的
  e2e 模块把 `llm-gateway` 插进 `sys.path`；根跑时收集次序不同、`orchestrator/edge` 先赢。
  **不是本批引入**——该文件与那行 import 本批一字未动，且单跑该文件 36/36 全过。

> **判据：裸名 import 的胜负由 `sys.path` 次序决定，而次序由选集决定。**
> 所以「换个选集再跑一遍」不是补跑回归，是**换了一把尺子**。它与既有那条
> 「对照基线必须与被测环境同分母」是同一条的另一面：那次是分母变小（worktree 缺
> `gen/python`），这次是**分母变小反而多出红灯**。

已立卡（`AGENTS.md` §4.1），本批不修——修它要动多处共享的 import 管道，属另一批。
