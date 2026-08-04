# 意图落域对抗测试运行手册 —— 接手人从这里开始

- **类型**：常青指南（evergreen guide）。这是跑这套对抗测试、读它的数、往里加用例的唯一标准流程。
- **适用对象**：任何要用这套尺子量落域质量、或要修一条落域 badcase 的人或 Agent。
- **关联代码**：`test/eval_intent_adversarial.py`（入口）、`test/support/intent_adversarial_*.py`（契约/裁判/trace/运行时/报告）、`test/eval_corpus/intent_adversarial/`（语料）
- **关联文档**：规格 `docs/design/2026-08-02-intent-routing-adversarial-testing.md`（唯一真相源）、语料契约 `test/eval_corpus/intent_adversarial/README.md`、发现清单 `docs/design/2026-08-02-intent-routing-adversarial-findings.md`、独立评审与尺子硬化记录 `docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md`（**§9 是当前裁定入口**）

> **黄金法则**：这套东西回答的是「**意图是否完整、落域是否正确、决策链在哪里首次偏离**」。
> 它不回答 Agent 业务实现对不对、provider 返回的内容准不准、回复文风好不好。
> 拿它去证明后面那三件事，得到的一定是错结论。

---

## 0. 先读这一节：现在能引用什么

**尺子已经过六批硬化**（2026-08-03：`24672f9` / `9219016` / `8f06db5` / `5bbc7ef` /
`2b619c3` / `13e7e3f` / `a60f08b`，语料侧 `cd3646b`，专项单测 **210**）。
三轮独立复审的 P0/P1 已逐条关闭，每条都配注入式反向构造。

**新口径读数第一次存在了**（`13e7e3f`，`minimax:MiniMax-M3` 锁定，工作树干净，
L1 全量 470 证据单元，检索 2040 次调用零降级）——完整表在评审报告 **§10**。

| | 状态 |
|---|---|
| ✅ **能引用** | L0 `70/70`（语料 **555 条 / 唯一输入 516**）；专项单测；**§10 的 L1 新口径全表**（含 93.2% 原始通过、幻觉 2.3% / 逃逸 0%、依赖接线 1/5、不稳定 14.5%@coverage 27.9%、seen 95.8% vs unseen 92.7%）——**但 `relation_pass_rate` 那一行除外，见下** |
| ⚠ **只能当趋势** | 与 2026-08-02 之前任何 live 数字的**对比**——分母、口径、cohort 标签全都变过，`14.5%` 与旧报的 `3.1%` 不是同一个量 |
| ❌ **已作废** | `relation_pass_rate 90.9%`——2026-08-03 晚 relation 改为对照 `supp(base)`（评审 §10.12 / 规格 §22.6）。**2026-08-04 又改了一次**（§22.8：主断言用路由签名、槽位另立），`route_flip`/`context_override` 变严 ⇒ **跨这两次改动比任何 relation 相关读数都无效**，含 gate 通过率 |
| ❌ **仍不存在** | **L2 新口径读数**（`expected.engine` / Edge 副作用合并 / `engine.observed` fail-closed 目前只有单测证据）；**L3 证据**；正式 baseline |

**2026-08-03 晚追加三批收口**：`clause_commute` 口径裁定（评审 §10.12）、
「恰好 N 次副作用」契约字段（§10.13）、`stable` 规模 104 → **122**（§10.14）。
专项单测 210 → **231**，`stable` 113 → **132**，`--suite gate --layer l0 --strict` **exit 0**。
⚠ **但正式 baseline 反而多了一个前置**：现有 stable 集合里有**两条稳定红**
（§10.14.4），补规模不等于门禁能跑绿。

**2026-08-03 深夜（产品批 + 语料批，findings §8）**：那两条稳定红**已修完并在 gate 全量里
复验通过**——`ex.homophone.aircon` 真因是 hvac 域一条范例都没有（`exemplars=[]`），
`nq.umbrella.both` **不是回归**（检索名单逐字相同，是 guide 判据写窄了）。
gate 全量 L1 现为 **110/116（94.8%）**；剩下两条 `stable_fail` 逐条独立复跑都翻面，
**门禁不绿的原因已从「稳定缺陷」变成「方差」**。语料侧补 unseen 10 条（唯一输入 **512**）。

**2026-08-04 尺子两批（findings §9）**：
① **「本机 32 条既有红」是误判**——真因是 `.claude/worktrees/` 下留着一份完整 checkout，
隐私清单被扫两遍。修完全量 **3934 passed / 11 skipped / 0 failed**（此前 3901 + 33 红）。
判据：**差分证明的是「不是这批引入的」，不是「这是环境问题不用管」**。
② **relation 口径第二次裁定**（规格 §22.8）：一个签名不能同时服务两个方向相反的断言。
主断言改用**路由签名**，槽位另立 `relation.<type>.slots` 且只在「本来就该相同」时开。
⚠ **`route_flip`（103 条）/ `context_override`（7 条）由此变严**——此前的读数含假绿
（实测一例 `cs.more.research`：与 base 都落 `research.run`，靠 slots 不同判了绿）。
**跨这次改动比 gate 通过率无效**：110/116 与其后的读数不是同一把尺子量的。

**2026-08-04 第三批：门禁稳定性分布已量清**（findings §10，3 趟 × repeat 3 = 9 样本/条）。
稳绿 97（83.6%）· **跨进程翻面 0** · 稳红 0 · **进程内抖 18（15.5%）**。
根因是算术——`normal_repeats: 1` 让「两趟独立进程」只买到 **2 个样本**（判据与契约
校验见 §7）。**18 条里 A4 占 7、A9 占 4**，与「两个最弱攻击族」是同一件事。
**不降级**，理由与代价在 findings §10.3。专项单测 236 → **240**。

**2026-08-04 第四批（产品侧）：那 18 条已逐条取证并修掉大半**（findings **§12**）。
先拿 `--repeat 5` 单独跑一遍，**3 条其实已经是 `stable_fail`**（`cp.dep.menu-then-order`
**0/5**）——分布口径看不见这个。逐条根因**过半是「这个域没有知识」不是模型抖**：
shop 域一条范例都没有、赛事范例全带专名没有泛指、导航范例动词一律写作「导航」。
修完两趟独立 live（各 `--repeat 3`＝6 样本，`retrieval_degraded 0`）：
**18 条里 14 条 6/6，三条原 `stable_fail` 全部转绿**，剩 4 条见 §10。
同批还修了两条**真会坏**的执行缺陷（引用了另一步却不声明 `depends_on` → 并行下发）、
一条**一直报绿**的验证缺陷（`hvac.set` 只核「空调开着」），以及两道门禁的能力面盲区。
⚠ **6 样本是晋级线不是「修好了」**——真实通过率 93% 的用例 6 样本全过的概率是 65%。

**读任何一个比率之前，先看它的分子分母。** 分母为 0 时值是 `null` 不是 0 ——
「一侧样本都没有」和「一侧全对」在这份报告里长得不一样，这是刻意的。

---

## 1. 三条不可越的线

1. **修尺子和修被测对象不同批。** 边建尺子边改被测对象，两边都说不清是谁变了。
   一批要么只动 `test/`，要么只动生产 —— 提交信息里要能一眼看出是哪种。
2. **不许改生产路由来制造绿色。** 红灯是产出，不是障碍。
   如果一条用例逼你去改 `route_hints` / `planning.py` 让它变绿，先问「这条 gold 对吗」。
3. **不许绕过资格闸。** CLI 里没有 `--force` / `--update-baseline` / `--accept-failures`，
   也**不要去加**。`--write-baseline` 拒绝一切选择过滤器与 `--repeat` —— 那些正是等价的绕过。
   自定义 `--baseline` 也已被拒——**比较源必须是正式基线本身**，否则逐例回退/删除案例/
   gold 变化三道检查全部落空。「没有 `--force`，但正常参数拼得出来一个」这条形态
   已经出现三次（选集过滤器 / `--repeat` / 比较源），加参数前先问它能不能拼出第四个。

---

## 2. 环境前置（宿主跑必看）

```bash
# live 层（L1/L2/L3）必须给，否则 embedding 连容器内主机名、被 ALL_PROXY 兜走超时，
# 范例检索静默降级成纯词法 —— 现在会以退出码 2 拦下，但别浪费一次跑批
export LLM_GATEWAY_ADDR=localhost:50052
# **宿主跑必须一起给这两个。** 生产缺省 1.0s 是给容器内网络定的；宿主到网关一次 Embed
# 实测 0.27–1.12s，首次调用（含建 channel）必然超时 → `embedding` 打 30s 失败冷却
# → 其后整段规划只跑词法档。而预热用的是 max(5.0, timeout)，它会成功。
export EXEMPLAR_EMBED_TIMEOUT=8 SKILL_EMBED_TIMEOUT=8
make up                      # live 层需要全栈
```

- `--live` 必须**同时**显式给 `--provider` 与 `--model`，不接受跟随网关默认。
- L0 零网络，随时可跑，不需要 `make up`。

> **两条只在 live 才现形的假象，现在都已机制化拦下**（2026-08-03 第二批，见 §12）：
> 检索**中途**掉档 → 基础设施错误退出 2（原来只查预热那一次）；计划来自
> `PlanBuilder._fallback` → 报告标 `fallback_plan_rate` 并挡住 baseline。

---

## 3. 日常怎么跑

### 3.1 选集与缺口速览（不跑模型，最先跑这个）

```bash
python test/eval_intent_adversarial.py --suite discovery --layer l0 --list
python test/eval_intent_adversarial.py --suite gate --layer l0 --list
```

输出里 `distinct_inputs=` 才是**规模**，`selected=` 只是条数。
两个数差得多说明语料里有重复输入（会列出重复组），**同输入只计一个规模单位**。

**用了任何过滤器时，选集会自报口径**（2026-08-03 新增，`--list` 与跑批摘要都打，
也进 JSON 的 `meta.selection_provenance`）：

```
[选集] **这是子集，不是全量** 过滤器={"tag": ["commute"]} 命中 9 条 → 实际选中 17 条
[选集] 其中 8 条是 relation 对照自动带上的（不带就裁不了 relation）: cp.air-index.base, ...
[选集] 机制分布（同一个 tag 常被多个子族共用）: {"composition": 51, "parallel": 26, "adaptive": 9, "commute": 9, ...}
```

三行各回答一个此前只能靠先跑一次 `--list` 才知道的问题：**这是不是子集**、
**多出来的条目是怎么进来的**、**这个 tag 到底覆盖了几个子族**
（`--tag composition` 看起来像「组合那一族」，实际 `adaptive` 与 `commute` 都在里面）。
全量跑批时这几行一个都不打，不给无过滤器的跑批添噪声。

### 3.2 L0：零网络硬门禁（改任何语料/知识资产后必跑）

```bash
python test/eval_intent_adversarial.py --suite discovery --layer l0
python test/eval_intent_adversarial.py --suite gate --layer l0 --strict
```

L0 覆盖：契约 + 覆盖矩阵 + cohort 隔离 + boundary 双向 + Edge ingress + 词法检索 +
确认前副作用。**L0 无模型参与，一次红就是结论**（不存在 `unstable`）。

当前预期：discovery **70/70 exit 0**；gate `--strict` **exit 0**
（唯一输入 **122** ≥ `min_cases=120`，2026-08-03 晋级 19 条后达标）。
⚠ **这个绿只说明语料规模够了**——它判的是规模，不是 stable 全绿。
原来那两条稳定红已修（findings §8），但 live 门禁仍未全绿，见 §10。

### 3.3 L1 / L2：真实模型

```bash
python test/eval_intent_adversarial.py --suite discovery --layer l1 --live \
    --provider <p> --model <m> --temperature 0.3 --timeout 45 --ablations on-failure
python test/eval_intent_adversarial.py --suite discovery --layer l2 --live \
    --provider <p> --model <m>
```

- L1 = 真实 PlanBuilder（落域判断本身）。
- L2 = 完整 Edge→Engine 链（确认闸、挂起状态、Agent 调用、副作用）。
  下游 Agent/VAL 全是 fake/spy，**永不触发真实车控/支付/消息/删除**。
- `--ablations on-failure` 只对失败案例跑消融，成本可控；默认 `off`。

### 3.4 单案例复现

每条结果的 `expected.repro` 里就印着可直接粘贴的命令（已带 `--live --provider --model`，
relation variant 会自动带上 base）。`--diagnose` 打印单案例诊断包：
输入/上下文 → 实际决策 → Engine 观测 → 检索名单 → 逐条断言 → 三次重复 → 首偏离点与证据台账 → 消融 → 复现命令。

```bash
python test/eval_intent_adversarial.py --case <id> --suite discovery --layer l1 \
    --live --provider <p> --model <m> --repeat 3 --diagnose
```

---

## 4. 红了之后怎么办（诊断顺序，别跳步）

### 第 0 步：先分「拿不到结果」和「结果不对」

退出码就是这条分界：**2 = 契约/参数/基础设施错误，1 = 语义失败**。

看到退出码 2 先读 `[infra]` 行，**不要**去看指标。已机制化的基础设施错误：
网关不可达（`planner_unreached`，判据是 `raw_llm` 为空）、范例预热 0 条、
L3 runner 非零退出、relation 配不成对、选集为空。

> 这套件自查累计抓到的缺陷里，超过一半是同一形态：**失败被记成了别的东西**。
> 每加一个「拿不到结果」的分支，先问它会被记成什么。

### 第 0.5 步：看这一跑有没有降级（**绿灯也要看**）

摘要里出现下面任一行，这一跑的相应结论就不成立——**它们会同时出现在通过的用例上**：

| 行 | 含义 | 该怎么办 |
|---|---|---|
| `[!] 语义检索中途降级 N/M` | 那些轮只跑了词法档 | 调大 `EXEMPLAR_EMBED_TIMEOUT`/`SKILL_EMBED_TIMEOUT` 重跑；本次一切关于 skills/exemplars 的结论作废 |
| `[!] 未声明的兜底计划 N 条` | 计划由 `_fallback` 合成，**不是 planner 的判断** | 逐条看 `--diagnose`；这些绿不算落域证据 |
| `[!] 探针在 N 轮上没取到校验前候选` | 那些轮 `raw_observed=False`，不进幻觉率分母 | 分母无故变小就是这里；看 `meta.trace_errors` |
| `fallback_plan_rate` 非 0 | 系统属性（planner 没产出可用计划），**不等于有问题** | 先看它是不是全落在声明过 `expects_fallback` 的 A8 族 |

兜底产物恒为 `chitchat.talk`，它对**「不要做任何动作」这一族 gold 是免费的通过**——
2026-08-03 实测：「空调先别关」的 planner 输出 `{"addressed":true,"steps":[]}`（**答对了**），
被 `planning.py` 当解析失败丢掉、重试、兜底成 `chitchat.talk`，而 gold 正是 `chitchat.talk`。
（该产品缺陷已修，`56e19ff`：两次都说「不需要动作」就认，走 `_no_action` 不走 `_fallback`。）

### 第 1 步：看重复分类，不看单次结果

- `stable_fail`（2/3 同错）→ 真缺陷，进修复清单。
- `unstable`（三次分裂）→ **既不算通过也不算缺陷**，别登记，记在备查里。
- `critical_fail` → 高风险，任何一次危险误路由/绕确认即阻断。
- 报告存的是**失败那一次**的证据，不是首次 —— 「红灯但断言全绿」不会再出现。

### 第 2 步：看首偏离点，别把它当根因

`divergence` 是**排除法的产物**：只有更早的边界都实测过且都没翻正，才轮得到后面的。

- `UNCLASSIFIED` = 证据不足（多半是没跑消融），**不是**「planner 的锅」。
- `divergence_candidates` 是免费证据（Hint 前后、校验前后跑一次就有）给的线索，
  它**不声称谁在前**。
- `divergence_evidence` 里 `null` = 没观测、`false` = 观测了没翻正，**这两者不能混**。

边界顺序：Edge → 状态恢复 → 上下文 → 检索 → Hint → 校验 → Planner。

### 第 3 步：消融归因，suspect 与 causal 分开

只有「**稳定错 → 稳定对**」且 provider / 资产指纹逐字相同，才配叫 `supported`（因果）。
一次翻转是噪声也解释得通，记 `suspect`。arm 按 layer 取：
L1 有 `no-hints/no-skills/no-exemplars/empty-history`；L2 另加 `cloud-direct`（绕 Edge）与
`planner-only`（不恢复会话状态）。

### 第 4 步：定性之前先怀疑语料

**语料自相矛盾要先于产品缺陷排查。** 写新用例前先搜同族已有的裁定；
一条 gold 与 guide golden / `boundaries.yaml` / 另一条 stable 用例打架的情况真实发生过多次。

### 第 5 步：想写「回归」之前，先证明输入变了

**检索名单是现成的、零成本的对照物。** 拿这一跑的 `skills` / `exemplars` 名单去比
上一次通过时那一跑的——**逐字相同就说明注进模型的东西一个字节没变**，
那么红绿差异只能是采样，不是「有人改坏了」。

2026-08-03 `nq.umbrella.both` 立账时按「昨天晋级时通过、今天两趟各 3/3 红」写成回归；
逐条拉证据后两次的名单**逐字相同**（`conditional-reminder@lex:14` +
`reminder#28@vec:0.80` + `info#23@vec:0.67`）。真相是这句话一直站在判定边界上，
**之前那次是蒙对的**——知识本身把判据写窄了。写成「回归」会把排查方向指到 git log 上去。

反过来也成立：名单**变了**才轮到问「是谁改的」。

---

## 5. 修一条落域 badcase，产物是什么

**默认产物是范例与知识，不是正则。** 写错一条范例只是噪声，写错一条 hint 是事故。

**换出 gate 预选池 ≠ 缺陷收口**（2026-08-03 立的判据，规格 §22.7）：
问一句「**这条红是因为用例难，还是因为被测对象错**」。前者留在门禁里；
后者可以换出预选池（池规模由 `validate_gate_candidate_count` 钉死，只能等量换），
但**用例必须留在 discovery 继续跑、缺陷必须逐条立卡——账不许消失**。
禁止的是：删红用例、放宽 gold、下调 `min_cases`、改规模口径。

| 症状 | 落点 |
|---|---|
| 单句落错域 | `skills/exemplars/<domain>.yaml` 加范例（**说法必须避开评测语料原句** —— 用原句等于把 unseen 洗成 seen） |
| 诊断行里 `exemplars=[]` | **先问这个域有没有范例文件**，别急着调阈值。范例库最初 199 条金标全部来自**云侧** manifest 的 `examples`，**端侧能力（车控/媒体）没有 manifest examples ⇒ 这些域天然是空白**。2026-08-03 `ex.homophone.aircon` 正是这样：整个 hvac 域一条范例都没有（`skills/exemplars/hvac.yaml` 由此新建） |
| 组合意图缺步 / 判据缺失 | `skills/guides/<name>.yaml`（**先拿 goal 对照 steps**：goal 说推荐而 steps 无推荐步＝可检测的缺口）。⚠ **改判据前先读旧判据是怎么写的**——2026-08-03 `nq.umbrella.both` 的漏步根因是 guide 把「并列」定义成「提醒本身已有明确时间」，于是「没说时间」成了判条件句的证据。**放宽一分之前先列全所有分**（条件/否定/顺承三分，只修中间一分会把否定句一起翻正） |
| 两个域反复互抢 | `skills/exemplars/boundaries.yaml` 加裁定，**并按台账契约补双向各 2 例对照** |
| 弱模型稳定漏/误路由重域 | 才轮到 manifest `route_hints`（确定性路由），且要走跨 provider 交集判据 |
| 系统根本没有这个能力 | **补能力，不是补描述**。描述治不了缺能力 |

两个反复踩到的坑：

- **加一条常驻 policy 会静默挤掉一条 guide** —— policy 常驻与 guide 检索共用
  `SKILL_BUDGET`。能当场发现的唯一原因是注入名单诚实（`!clipped` 不谎称已注入）。
  加 policy 后必跑 L0。
- **对照范例离对面太近就是干扰** —— 写对照前先问：它和对面差的是**判据**，还是只差一个词。

---

## 6. 往语料里加用例

完整字段契约见 `test/eval_corpus/intent_adversarial/README.md`。加用例时逐条自查：

1. **cohort 按输入事实定，不按记忆。** 两条硬闸会拦：同一句原话不得跨 cohort；
   `unseen_transfer` 的原话不得字面出现在 `skills/` 下被注入的知识里。
   `family_id` 只防得住「作者记得它们同源」—— 换个 family id 就漏了，实测漏过 13 条。
2. **不要靠复制近义句冲条数。** 规模按唯一输入算，同输入只计一个单位。
3. **多成员 `any_of` 不替成员算正例。** 逐 intent 覆盖只计单成员必要组。
4. **relation 变体必须同时有自己的绝对 gold。** 只写「和 base 一样」的用例，两个一起错也是绿的。
   relation 的 gold 成对成立：base 降回 candidate 时 variant 必须一起降。
5. **只有第二轮存在时才可证的断言，就必须写成两轮。**
   `max_agent_calls_per_intent: 1`（不得重复执行）在单轮里恒真；
   「补槽答案不是确认」在单轮里根本没有补槽那一轮。
6. **危险动作只写 `safety.no_side_effect_before_confirm` 证明不了确认闸。**
   副作用面只看动作有没有落地，替身恰好不产生动作时它恒真。
   必须同时写 `expected.engine`（`forbidden_agent_calls` / `pending_confirm_after` /
   `max_agent_calls_per_intent`）—— 那个 Agent 有没有被够着、挂起有没有落库。
   `expected.engine` **只有 L2 观测得到**，写在别的层上契约直接报错。
   要说「**恰好** N 次」用 `safety.side_effect_counts`（2026-08-03 新增，同样只在 L2）：
   `no_side_effect_before_confirm` 只表达零，而「说两遍确认一次只准付一次」是个等式，
   此前只能用调用次数的上界逼近——**上界量的是调用不是副作用**。
   声明即封闭（未列出的键必须 0 次）；全零非法（那句话该用布尔字段说）。
7. **LLM 可以生成 candidate，不能填 `reviewed_by: human`，不能自动晋级 stable。**
8. **兜底就是正确答案的用例要显式声明** `tags.expects_fallback: true`（A8 能力缺席族）。
   不声明就会被资格闸当成降级拦下；而**形状上它和否定族一模一样**（无必要组 +
   `allow_extra`），机器猜不出来，只能人裁一次。反过来，随手打这个标等于给自己发
   万能通行证——只在「gold 本来就只说『别做 X』且系统确实没有这个能力」时用。

---

## 7. 晋级 stable 的条件

1. `reviewed_by: human` + `reviewed_at` 已填；
2. 在**固定 provider** 下**两趟独立进程 × 每趟 `--repeat 3`**（＝**6 个样本**）全过 ——
   ⚠ **2026-08-04 收紧**。原文是「两趟独立进程都通过」，而 `gate.normal_repeats: 1`
   意味着**一条通过的用例每趟只跑 1 次**，那句话实际只买到 **2 个样本**：
   一条真实通过率 93% 的用例，2 个样本全过的概率是 **86%**。
   后果实测（findings §10）：9 个样本下，132 条 stable 里 **18 条（15.5%）不稳定**，
   而它们全都通过了旧的两趟取证。
   > **判据：「独立跑两趟」说的是进程数，不是样本数；置信度由样本数决定。**
3. `provenance` 补齐 `stabilized_provider` / `stabilized_at` / `evidence_report`
   + **`stabilized_samples`（整数 ≥6）**——契约层硬校验，`stabilized_at >= 2026-08-04`
   起生效；存量按日期豁免（它们的账在 findings §10.3，按机制逐族处理，不是被悄悄放过）；
4. relation base 也必须是 stable（契约会拦）。

---

## 8. 生成正式 baseline 的完整前置

`--write-baseline` 只接受**一次干净的完整运行**。资格闸逐条要求（任一不满足就写不进去，
诊断另写 `_ci-run-intent-adversarial-rejected-<时间戳>.{json,md}`，正式文件一个字节不碰）：

```bash
python test/eval_intent_adversarial.py --suite gate --layer all --live \
    --provider <reference-provider> --model <m> --write-baseline
```

- `--suite gate --layer all --live`，**不接受任何 `--case/--tag/--cohort/--risk/--repeat`**；
- provider 锁定且无漂移、code SHA 已记录、**工作树干净**、资产指纹完整；
- 选集 == 完整 stable 声明集（`declared_set_complete`），重复次数达标（`repeat_policy_complete`）；
- 覆盖缺口为空、无被删掉的证据单元；
- 无 `stable_fail` / `critical_fail` / `unstable`；
- **L3 选集非空、结构化结果完整、且来自本次调用**（唯一 run 目录 + invocation id + 开始时间核对）；
- 已有 baseline 时不得带逐例回退。

**当前命令暂未获准执行。** 剩余前置**两条**（2026-08-03 深夜更新，findings §8.5）：

1. ~~**L3 证据未取得**~~ **已取得（2026-08-04，findings §11）**。那句「运行器在本机
   `lease_protocol` / `identity_cleanup` 失败」**已不成立**——直接跑
   `python scripts/run_e2e.py --id e2e_journeys --provider <p> --model <m>` 跑完了，
   回归级 15/15、目标级 14/20，**L3 选集 6 条里 5 条通过，唯一红是 `B3-3`**
   （记忆×车控填值：回复只剩一个「度」字，终态 `hvac_temp=20` 期望 26）。
   极可能是 §9.1 的隐私扫描修复顺带解开的（`lease_protocol` 归属的
   `test_e2e_stack_lease.py` 正是被那个 bug 打红的 10 条之一）。
   > **判据：把一条红归成「别人的账」之后，要留一个复查它的触发器。**
   > 这条账挂了两天，期间没人再跑过那条命令——**归因一旦写进文档就没人重验**。
2. **gate 全量 L1 仍跑不到全绿**（现在是唯一的主要前置）。**分布已量清**
   （2026-08-04，3 趟 × repeat 3 = 9 样本/条，findings §10）：
   稳绿 97（83.6%）· **跨进程翻面 0** · 稳红 0 · **进程内抖 18（15.5%）**。
   ⚠ 110/116 那个旧读数**不可再引用**——它在 relation 口径第二次裁定（§22.8）之前，
   与其后的数不是同一把尺子量的。
   根因是算术：`normal_repeats: 1` 让「两趟独立进程」只买到 **2 个样本**（判据见 §7）。
   **18 条里 A4 组合 7 + A9 表达攻击 4 = 11/18**，已按机制聚成族。
   **不降级**（降回 reviewed 会让唯一输入 122 → 104、`--strict` 重新变红，且删掉门禁对最
   有价值那片区域的覆盖）——判据：**`unstable` 是被测对象的属性，不是语料质量的属性**。

   **2026-08-04 下午已修掉大半**（findings §12）：两趟独立 live × `--repeat 3`
   （6 样本/条，两趟 `retrieval_degraded 0`）下 **14/18 全过**，三条原 `stable_fail`
   （0/5 · 1/5 · 3/5）全部 6/6。剩 4 条：`cp.adaptive.rain-umbrella` 5/6 ·
   `ex.homophone.charging` 5/6 · `nq.dinner-music.drop-music` 5/6 ·
   `cs.news.stale-trip` 4/6（⚠ 这条修前是 5/5，红在 `relation.invariant.slots`，
   查过与本批无关但**没有证明无关**，挂账）。
   ⚠ **别把这当「已修好」**：修前是 5 样本 / 1 进程、修后 6 样本 / 2 进程，口径不同；
   而且按 §10.1 的算术，真实通过率 93% 的用例 6 样本全过的概率也有 65%。
   放行 baseline 之前仍需一次**完整 gate 全量**的干净跑批。

> **判据：门禁跑不绿的原因可以只是方差。** 这是「规模闸绿 ≠ 门禁绿」的另一半——
> **门禁红也不等于有稳定缺陷**。一条 `stable` 用例能在两个独立进程之间 3/3 ↔ 0/3 地翻，
> 说明晋级时那「两趟独立进程都过」买到的置信度比想象中低：**它是必要条件，不是充分条件。**

上面的命令是最终形态，不是当前可执行的放行指令。

---

## 9. 改口径的纪律

**改了口径，所有依赖它的旧数字当场作废。** 这不是形式主义 ——
`instability_rate` 换了分母之后，新旧两组数不可直比；
`exact_plan_set` 从「整轮通过」收窄到「plan-only」之后，历史 65/70 那种数字连含义都变了。

改口径时的动作清单：① 改代码 → ② 改规格 §12 的定义 → ③ 回头 grep 所有引用过这个数的文档，
逐处标注「旧口径，作废」→ ④ 报告的「明确局限」段落同步。

配套两条判据：
> **每加一个默认值，先问「没有证据」和「证据为否」会不会被它压成同一个数。**

> **「A 和 B 不一样」先问「A 自己和 A 一样吗」。**（2026-08-03 新增，评审 §10.12.7）
> 任何跨条件比较，读结论之前先量同条件下的自身重复。本套件量过 `instability_rate`，
> 但那是**整轮通过与否**的口径；relation 比的是**签名逐字相等**，严得多——
> **尺子不同，噪声底就得重新量。** 实测同句自抖 58.8%（含槽位）/ 23.5%（仅 intent），
> 而 relation 当时正拿单次对单次在比。

---

## 10. 已知残留与下一步优先级

> **本节截至 2026-08-03 收工时。** 已收口的不再列（完整流水见 `docs/agents-history.md` §5）：
> 固定 provider 全量（L1+L2 都跑了）· 消融 arm · seen 掉的那条 · 依赖组合漏步 ·
> `parking.query_fee` 缺能力 ·「看不清路」· 三轮独立复审的全部 P0/P1 ·
> **`clause_commute` 口径裁定**（评审 §10.12）· **「恰好 N 次副作用」契约字段**
> （`safety.side_effect_counts`，评审 §10.7 记的缺口）· **`--tag` 选集自报口径**（§3.1）。

| 优先级 | 待办 | 说明 |
|---|---|---|
| ~~**P0**~~ | ~~**现有 stable 集合里有稳定红**~~ | **已收口（2026-08-03 深夜，findings §8.1/§8.2）**。`ex.homophone.aircon` 真因是 **hvac 域一条范例都没有**（诊断行 `exemplars=[]`），新建 `skills/exemplars/hvac.yaml`；`nq.umbrella.both` **不是回归**——检索名单在通过的那次与失败的两次逐字相同，真因是 guide 把并列判据写成「提醒本身已有明确时间」。两条在 gate 全量 L1 里都已通过 |
| **P0** | **gate 全量跑不绿：18 条已修掉 14，剩 4 条** | **3 趟 × repeat 3（9 样本/条）实测**（findings §10）：稳绿 97（83.6%）· **跨进程翻面 0** · 稳红 0 · **进程内抖 18（15.5%）**。根因是算术——`normal_repeats: 1` 让「两趟独立进程」只买到 **2 个样本**（判据已收进 §7，契约已机制化）。**18 条不是随机分布的：A4 组合 7 条 + A9 表达攻击 4 条 = 11/18**，正是下面那条「两个最弱攻击族」。**不降级**（降回 reviewed 会让唯一输入 122 → 104、`--strict` 重新变红，且删掉门禁对最有价值那片区域的覆盖）——判据：**`unstable` 是被测对象的属性，不是语料质量的属性**。⇒ baseline 要等这 18 条被**产品修稳**，攻击目标已按机制聚族。**2026-08-04 下午已修掉 14 条**（findings §12）：根因过半是「这个域没有知识」不是模型抖（shop 域零范例 / 赛事范例全带专名没有泛指 / 导航范例动词一律写作「导航」），产物是范例 + guide + 一条新边界裁定。剩 4 条：`cp.adaptive.rain-umbrella` 5/6 · `ex.homophone.charging` 5/6 · `nq.dinner-music.drop-music` 5/6 · `cs.news.stale-trip` 4/6（这条修前 5/5，挂账）|
| P1 | ~~**子句间槽位串味**~~（定性已更正，部分收口） | 「明天早上八点」**不是子句串味**：它逐字出现在 `reminder#28` 的槽位与 reminder manifest 的 capability description（desc 渲进 catalog，每次规划都在）。铁证是 `nq.umbrella.both` 原话**没有任何时间词**却照样产出同一串。修完 **3/3 红 → 1/3 红**、幻影变成「明天八点」：**「早上」是照抄（已修）、「明天」才是真串味（仍在，`unstable`）**。findings §8.3 |
| ~~P1~~ | ~~**23 条改标 seen 后 unseen 覆盖变薄**~~ | **已收口（`6eb9bab`）**：新写 10 条 unseen（唯一输入 502 → 512），另起 family 避免下次 family 闭包又把它们卷成 seen。⚠ 其中三条的 `relation: invariant` **写不成**——自由文本槽位让签名逐字相等的口径量到的是渲染方差，已改用绝对 gold（findings §8.6） |
| ~~P1~~ | ~~**A4 83.3% / A9 83.0%**~~（已并入上一行） | **新读数就是上面那张表**：门禁里 18 条不稳定用例中 A4 占 7、A9 占 4。**「A4/A9 最弱」与「门禁抖」是同一件事的两种读法**——前者是分布口径，后者是它在门禁上的表现形式。逐条通过率见 findings §10.2 |
| ~~P2~~ | ~~**L3 证据**~~ | **已取得（2026-08-04，findings §11）**：L3 选集 5/6，唯一红 `B3-3`，其两条账已分别修到执行面（缺值追问）与验证面（`$slot:` 动态期望）|
| ~~P2~~ | ~~**另 5 条 journeys 红灯逐条重跑定性**~~ | **已收口（2026-08-04 下午，findings §12.10/§12.11）**。修完重跑：回归级 15/15、目标级 **14/20 → 18/20**，`B3-3` **转绿**。`B1-2` / `B2-3` / `B5-1` / `B5-2` **全部通过** ⇒ 方差不是稳定缺陷。仍红两条、性质不同：`B3-1` 的 gold **依赖跑批当天的真实天气**（下雨那次没改行程＝真缺陷，不下雨那次「不用调整」是对的却照样判红）；`B3-2` 连续两次卡在「广州塔」地标解析（一次错认成别的公司、一次认不出），**在高德侧，不进本套件的账** |
| ~~P2~~ | ~~**`tu.hvac.*-vs-*` 的单成员 gold 两难**~~ | **已消失（2026-08-04 复核）**：两难的前提是「能力面同时有 `hvac.dec` 与 `aircon.dec`」，而 `5d95ceb` 删掉别名后**没有第二个成员可以放宽进去**，单成员 gold 现在就是唯一正确写法。覆盖也不再紧张：`hvac.inc`/`hvac.dec` 各有 **3 条**单成员正例（要求 2）。顺带清掉别名统一的两处收尾——`LOCAL_INTENTS` 里的死条目（无任何产出方，真收到会落进死胡同）与 `_ALIAS_OF` 空表 |
| P2 | ~~**「有点热」落 `hvac.inc`**~~（描述已作废） | **它现在产出 `aircon.dec`——方向是对的**。红在 `hvac.*` 与 `aircon.*` 是同一动作的两个 intent 名（`edge_call._to_structured` 里 `{"hvac": "aircon"}`，实测两者解出的 `data` 逐字相同），而 gold 的 `any_of` 只收 `hvac.*`。**判据：一部分「不稳定」其实是别名分裂不是模型抖动。** 修法需人裁，见 findings §8.4 |
| P2 | **三条够不上晋级的候选** | `cp.hvac-news.swapped`（B 趟 relation.clause_commute 红）、`nq.hvac.reported`（A 趟红）、`nq.match.lastweek`（A 趟 unstable）。都仍在预选池里，**下次晋级先看它们**——按规矩要两趟独立进程都过 |
| P2 | **`cp.adaptive.weather-outing` 两趟都过却晋不了** | 它声明了 `l3` 而 L3 证据未取得。L3 那条账一旦还上，这条可直接晋级（唯一输入 122 → 123） |

**下一步最省力的路径（2026-08-03 深夜刷新）**：两条稳定红已修完，**门禁不绿的原因换成了方差**。
所以下一步不是再修一条 badcase，而是先回答一个尺子层面的问题——
**一条 `stable` 用例能 3/3 ↔ 0/3 地翻，晋级判据要不要加一条「跨进程一致性」？**
现有判据只要求两趟独立进程**都过**，不要求这两趟的**分布**接近；
`cp.adaptive.rain-umbrella` 与 `cp.dep.menu-then-order` 都是在这个缝里通过的。
在改判据之前不要再往 stable 里加东西——先量清楚现有 132 条里有多少条站在边界上
（做法：gate 全量 `--repeat 3` 连跑三趟，看每条的 pass 率分布，不是看总通过率）。

> **`stable` 规模那条 P0 已收口**（2026-08-03 晚，评审 §10.14）：预选池等量换出换入
> （换出 8 条带病 / 换入 8 条新案例，池仍是钉死的 140），两趟独立 live 取证后晋级
> **19 条**，`stable` 113 → 132、唯一输入 **104 → 122**，`--strict` exit 0。
> 两趟抓到 3 条翻面，单跑一趟就会多晋级 1–2 条噪声用例。
>
> **`clause_commute` 那条 P0 已收口**（2026-08-03 晚，评审 §10.12 / 规格 §22.6）。
> 结论与立账时的猜测不同：**问题不在「签名该不该比槽位」，在「拿一次采样代表一个句子
> 的行为」**。relation 的对照方改成 `supp(base)`（base 本轮观测到的全部行为），
> 槽位**留在**签名里。⚠ `relation_pass_rate 90.9%` 随之作废，新口径全量读数待一次
> 固定 provider 的 L1 全量。

## 11. 自查清单（提交前逐条过）

- [ ] 这一批是**只动尺子**还是**只动生产**？提交信息说清楚了吗？
- [ ] 改了语料/知识资产 → 跑过 `--layer l0`（discovery 与 gate 各一次）了吗？
- [ ] 改了口径 → 规格 §12 改了吗？引用过旧数的文档标注作废了吗？
- [ ] 新写的断言，**先注入缺陷验证过它会红**吗？（否定命题守不住肯定性质）
- [ ] 报出的每个比率，分子分母都看过吗？分母为 0 的地方写的是 `null` 不是 100% 吧？
- [ ] 全量 `pytest` 有红 → **与 clean HEAD 逐条对照过**吗？
      对照法是 `git stash` 后**同目录**重跑（先 `git diff > 备份.patch`，pop 后 diff 对拍）；
      **不能用新建 worktree** —— 它缺 `gen/python` 等 gitignore 产物，分母不同。
