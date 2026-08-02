# 意图落域对抗测试首轮发现清单（产品缺陷，另批修复）

> 日期：2026-08-02
> 状态：待修复排期
> 来源：`test/eval_intent_adversarial.py` 首轮发现轨（reference provider `minimax:MiniMax-M3`）
> 关联：`docs/design/2026-08-02-intent-routing-adversarial-testing.md`

本清单只登记**产品缺陷**。按实施计划的约定，建测试的这一批**不修生产路由**——
边建尺子边改被测对象，两边都说不清是谁变了。

分类口径（设计 §11.5 / 实施计划 Task 16 Step 3）：

- `product_defect`：稳定复现的落域/入口/安全缺陷；
- `gold_error`：语料的 gold 写错了，改 gold 并降回 candidate；
- `capability_gap`：能力面本来就没有，落域再准也答不上来；
- `unstable`：三次结果分裂，**不登记为缺陷**；
- `infrastructure_error`：运行环境问题，修环境后重跑。

---

## 1. L0（零网络，确定性，一次红即结论）

L0 首跑 70 条证据单元、65 通过。5 条红灯全部定性为 `product_defect`。

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

**不在本批修**：修法属于端侧规则面（`orchestrator/edge/fast_intent.py` 的疑问句否决），
需要单独的语料与回归，且按 M5 的纪律应优先考虑声明式产物而非再加一条正则。

### 1.2 本地 + 在线组合没有被拆开（漏接）

| case | 原话 | 期望 | 实际 |
|---|---|---|---|
| `ei.mixed.volume-reminder@l0` | 音量调小一点，提醒我八点开会 | `mixed` | `cloud` |
| `ei.mixed.seat-charging@l0` | 打开座椅加热，再找个充电站 | `mixed` | `cloud` |

对照组「打开空调并查一下天气」→ `mixed` 是通的，说明混合拆分机制在，只是这两类
子句没被 `split_and_classify_any` 认出来。风险 high 的原因不是错，而是**端侧秒回退化
成整句上云**：断网时这半条本地指令也跟着失效。

### 1.3 端侧漏接一条车控说法

| case | 原话 | 期望 | 实际 |
|---|---|---|---|
| `ei.local.mirror@l0` | 把后视镜收起来 | `edge_local` | `cloud` |

`rear_view_mirror.fold` 是端侧能力，这句话没被端侧接住。风险 low，登记备查。

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

按机制归簇，最重要的在最前面。

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

#### 簇 B：查询被读成写操作（4 条）

| 原话 | 期望 | 实际 |
|---|---|---|
| 明天要提醒我的事有哪些 | `reminder.list` | `reminder.create` |
| 接下来三天要提醒我什么 | `reminder.list` | `reminder.create` |
| 那条提醒我已经做完了 | `reminder.complete` | `reminder.create`（又建了一条） |
| 把交周报那条挪到后天下午 | `reminder.update` | `reminder.list` |

reminder 域的动词分工（list / create / update / complete）在真实说法上分不开。
`reminder.create` 的 intent 通过率 65.2%（n=23）是全域最弱之一。

#### 簇 C：危险动作被字面词直接触发（1 条）

「我要加油」→ **`fuel_tank_cover.open`**。用户在说需求（找加油站），系统去开油箱盖。
与 L0 那条「问天窗能开多大 → 真把天窗打开了」是同一族问题：**名词命中即动作**。

#### 簇 D：语义检索把领域 guide 召回到不相干的句子上（4 条）

`weather-outing` 以 `@vec:0.58` 被召回到「今天天气怎么样」、以 `@vec:0.52` 被召回到
「现在有没有暴雨预警」。词法档不会（L0 同句 guides 为空），**是语义通道的噪声**。

这条值得单独记一笔口径：**L0 与 L1 的检索档位不同**（L0 钉死词法、L1 走生产的
hybrid），所以同一条 `forbidden_skills` 断言在两层可以一绿一红——这不是矛盾，是
两个通道的真实差异被分层照出来了。

#### 簇 E：单点误路由（若干）

- 「昨晚国安打成几比几」→ `info.search`（赛事被浅搜劫持）
- 「第二条详细讲讲」（focus=info.news）→ `research.run`（深调研劫持追问）
- 「就近找个能逛的地方」→ `info.weather`
- 「轮胎气压是多少」→ `manual.query`
- 「如果明天下雨就提醒我带伞」→ 只查了天气，**没建提醒**（组合不完整，正是
  「goal 对而 steps 漏」的同型）

#### 簇 F：能力归属错误导致合法步被整条丢弃（1 条，机制值得单列）

「调高音量」→ 计划里是 `volume.inc`，但 planner 把它派给了 `edge-media`，而
`volume.*` 属于 `edge-vehicle`。`_validated_steps` 按「intent ∈ 该 agent 能力集」
校验，于是**整步被丢**，计划退化成 `chitchat.talk`。

日志证据：`Intent volume.inc not in agent edge-media capabilities, dropping step`。

**判据**：能力集校验挡住了幻觉（hallucination 0%），但它对「intent 对、agent_id 猜错」
这类错误的处理是**静默丢步**而不是纠正到正确的 agent。丢一步和幻觉一步，对用户是
同一件事——都没做成。这条建议单独评估：intent 唯一时是否应按 intent 反查 agent。

---

## 3. 本套件自身在首跑中被抓到的 7 个缺陷（已当批修，不需再修）

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

### 4.3 接手修复时的两条纪律

1. **修落域 badcase 的默认产物是范例与知识，不是正则**（`skills/exemplars/`、
   `skills/guides/`、`boundaries.yaml`）——hint 写错是事故，范例写错只是噪声。
2. **改完先跑发现轨对照，别只跑被修的那几条**。本批次的 seen 98.0% / unseen 86.0%
   就是基线：如果修完 seen 涨了而 unseen 没动，那修的是记忆不是能力。
