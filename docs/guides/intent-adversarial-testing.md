# 意图落域对抗测试运行手册 —— 接手人从这里开始

- **类型**：常青指南（evergreen guide）。这是跑这套对抗测试、读它的数、往里加用例的唯一标准流程。
- **适用对象**：任何要用这套尺子量落域质量、或要修一条落域 badcase 的人或 Agent。
- **关联代码**：`test/eval_intent_adversarial.py`（入口）、`test/support/intent_adversarial_*.py`（契约/裁判/trace/运行时/报告）、`test/eval_corpus/intent_adversarial/`（语料）
- **关联文档**：规格 `docs/design/2026-08-02-intent-routing-adversarial-testing.md`（唯一真相源）、语料契约 `test/eval_corpus/intent_adversarial/README.md`、发现清单 `docs/design/2026-08-02-intent-routing-adversarial-findings.md`、独立评审与尺子硬化记录 `docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md`、最终修复验收 `docs/reviews/2026-08-04-review-intent-adversarial-finalization.md`（**当前状态入口**）

> **黄金法则**：这套东西回答的是「**意图是否完整、落域是否正确、决策链在哪里首次偏离**」。
> 它不回答 Agent 业务实现对不对、provider 返回的内容准不准、回复文风好不好。
> 拿它去证明后面那三件事，得到的一定是错结论。

---

## 0. 先读这一节：现在能引用什么

> 当前状态以 2026-08-10 批为准：findings
> [`§17`](../design/2026-08-02-intent-routing-adversarial-findings.md)；正式 baseline 与
> 身份契约仍看 2026-08-09 收口补记
> [`docs/reviews/2026-08-04-review-intent-adversarial-finalization.md`](../reviews/2026-08-04-review-intent-adversarial-finalization.md)。
> 批次演进、旧口径与修复细节只查 findings §13–§16、2026-08-03 独立评审和
> `docs/agents-history.md`，不要把中间读数抄成当前结论。

| 证据 | 当前口径 |
|---|---|
| L0 discovery | **76/76**；561 条 / 522 唯一输入 |
| gate 规模 | **139 stable / 129 唯一输入**；L0 strict **25/25，exit 0** |
| DeepSeek 对比/参考 gate | **147/147**（`f0af9c0`）：L0 25、L1 117、L2 4、L3 1；L1/L2 各 **2 个独立进程 × 每进程 3 样本**；正式 baseline `eligible=True` |
| MiniMax 主模型 gate | **141/147**（`32e8718`）；exact **116/121**、required **99/103**；raw 幻觉 **3/121**、校验后逃逸 **0/121**；不稳定 **4/121**；`pass 141 / unstable 4 / stable_fail 2`；`eligible=False` |
| 检索与运行身份 | 两批均锁定各自 provider、`text-embedding-v4` 身份完整、检索零降级、trace/infra/provider drift 0 |
| L3 gate | A1-2 在两模型均 **1/1**；正式 baseline invocation 新鲜、exit 0，只证明该授权 case/claim |
| fallback | DeepSeek 正式批 **2/122**，均为语料声明过的 A8，未声明 0；MiniMax **11/122**，其中未声明 **2** |
| 回归 | `orchestrator/` **1093 passed**、`pytest test/` **1127 passed / 9 skipped**；端侧 smoke **13/13**；项目根全量 **4490 passed / 16 skipped / 0 failed**（2026-08-09 实测） |

⚠ **MiniMax 三批 141/141/140 都不是同一批红灯。** `f0af9c0` 点名的 6 条 unstable 在
`32e8718` 全部转绿，红灯换成另一批边界单元（其中 5 条单跑 10 样本全绿）；`5e8247d`
换池后又换一批。按实测单元不稳定率 3~5%，一趟完整 gate 恰好零 unstable 的概率是个位数
百分比——**读主模型报告先看是哪几条，再看总分**（findings §17.6/§19.2）。

⚠ **上表 MiniMax 那一行是 `32e8718` 的读数，与当前代码差一个 `skills/exemplars/sunroof.yaml`**
（该范例已单独取证 10/10）。`5e8247d` 那批 140/147 是**换池态**下跑的，换池随后已回退
（findings §19.5），其案例集与当前不一致，**不要拿来当当前读数**。
严格说当前 SHA 没有对应的全量读数——需要时重跑一次，别挪用相邻批次。

首份正式**对比/参考模型** baseline 已写入
[`docs/reviews/eval/baseline_intent_adversarial.{json,md}`](../reviews/eval/baseline_intent_adversarial.md)，
其自身重算 `eligible=True`。`110/116`、`113/117`、raw `6/117` 与旧 seen/unseen 涨幅
均是历史批次，不得引用为当前状态。baseline 只绑定该 provider、资产与代码快照，不外推
MiniMax 主模型、Agent 业务结果、外部 Provider 内容或跨模型平均质量。当前真实 LLM 验证只用
`minimax:MiniMax-M3`（主模型）与 `deepseek:deepseek-v4-flash`（对比模型）；MiMo 凭证失效，
Qwen 不在本轮批准的证据模型内。

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
# 从 git worktree 跑 L3/all 时还要指向持有根 .env 的同仓库主 checkout；只设置这个进程变量，
# 不复制或修改 .env。普通主目录运行无需另设
export E2E_STACK_ROOT=/absolute/path/to/car-agent
make up                      # live 层需要全栈
```

- `--live` 必须**同时**显式给 `--provider` 与 `--model`，不接受跟随网关默认。
- worktree 漏设 `E2E_STACK_ROOT` 时，L1/L2 可能仍能访问网关，但 L3 runner 会因找不到根 `.env`
  fail-closed，因此整批不可引用。
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

当前预期：discovery **76/76 exit 0**；gate `--strict` **25/25 exit 0**
（gate 唯一输入 **129** ≥ `min_cases=120`）。
⚠ **这个绿只说明语料规模够了**——它判的是规模，不是 stable 全绿。
当前 live 必须按模型分账：DeepSeek 对比轨 147/147，MiniMax 主模型 141/147，见文首快照与
最终 review §7。

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

⚠ **加完知识必须跑对照，不能只确认它被注入了。** 2026-08-10 实测：一条新 guide 四次
全部成功注入（`@lex:11`，从未 `!clipped`），通过率却 4/10 → 1/10，退回后回到 7/10
（Fisher p≈0.02）。**「知识在场」和「知识有用」是两件事**；只看注入名单会把有害改动
读成中性（findings §18）。对照的最小形态就是**退回后再跑一次同样的样本数**。

⚠ **示范输出形状之前先确认当前输出通道。** few-shot 抄错通道（文本形状 vs
`PLANNER_TOOLCALL=on` 的 submit_plan schema）会让模型输出被判解析失败、退成兜底——
把「模型判断错」变成「模型说不出话」，还凭空制造未声明兜底（findings §18.3）。

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
   ⚠ **2026-08-04 收紧**。原文是「两趟独立进程都通过」，而旧配置 `gate.normal_repeats: 1`
   意味着**一条通过的用例每趟只跑 1 次**，那句话实际只买到 **2 个样本**：
   一条真实通过率 93% 的用例，2 个样本全过的概率是 **86%**。
   后果实测（findings §10）：9 个样本下，132 条 stable 里 **18 条（15.5%）不稳定**，
   而它们全都通过了旧的两趟取证。
   > **判据：「独立跑两趟」说的是进程数，不是样本数；置信度由样本数决定。**
   当前 gate 本身也固定 `normal_repeats: 3`，执行计划与 baseline 资格闸共同消费该值；
   配成 1 或 2 会在 suite 加载时直接失败，不能再靠一次幸运通过进入日常门禁。
3. `provenance` 补齐 `stabilized_provider` / `stabilized_at` / `evidence_report`
   + **`stabilized_samples`（整数 ≥6）**——契约层硬校验，`stabilized_at >= 2026-08-04`
   起生效；存量按日期豁免（它们的账在 findings §10.3，按机制逐族处理，不是被悄悄放过）；
4. relation base 也必须是 stable（契约会拦）。

---

## 8. 生成正式 baseline 的完整前置

`--write-baseline` 只接受一次**干净、完整、不可筛选**的运行：

```bash
python test/eval_intent_adversarial.py --suite gate --layer all --live \
    --provider <reference-provider> --model <m> --write-baseline
```

资格闸必须同时满足：

- 不带 `--case/--tag/--cohort/--risk/--repeat`，选集等于完整 stable 声明集；
- provider/model 锁定且无漂移，code SHA、资产指纹、工作树身份完整；
- parent 实测子进程 PID 必须与 worker 自报一致，PID、run id 与报告 digest 全部唯一；
- 所有 worker 的 embedding provider/model 身份必须可识别且一致；
- 重复与跨进程采样契约完成，覆盖缺口和被删除证据单元均为空；
- 无 `stable_fail` / `critical_fail` / `unstable`，raw planner 幻觉率满足门限；
- L1+L2+L3 都来自本次调用，L3 非空且 invocation/result 的 run/code/provider/model/lock/claim 身份一致；
- L3 候选恰好一份且当前新鲜；内嵌原始 Base64 报告能重算同一 SHA-256，结构字段与原始字节一致，
  相对路径严格绑定本次 run（只允许 runner 的 8 位临时后缀），畸形同级报告、额外目录或 `..` 均拒绝；
- 已有 baseline 时不允许逐例回退，拒绝结果只写 rejected 诊断文件，不碰正式文件。

2026-08-09 已按这条命令在干净 `f0af9c0` 上按当前 L3 原始字节/摘要/时间/精确路径契约
重新取证并写出正式对比/参考 baseline；provider 为
`deepseek:deepseek-v4-flash`。同一 SHA 的主模型 `minimax:MiniMax-M3` 报告为
`eligible=False`，不能借 DeepSeek baseline 宣称主模型已达标。这不是以后可以手工更新的授权：
每次写入仍必须重新跑完整父 bundle，最终以报告中的 `eligible=True` 为唯一放行信号，
不以既有 baseline、某一层单跑全绿或手工判断代替。

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

| 优先级 | 待办 | 完成判据 |
|---|---|---|
| P0 | 裸地名澄清族（留在门禁内，已立卡） | `nq.landmark.bare` 合并 **11/20≈55%**（高方差边界句，不是稳定红）、`nq.landmark.explicit` 自己每条断言都过、被 relation 连累；同族第三条 `nq.city.bare` 未进池。**路径 1「写 guide」实测否掉**（§18：4/10→1/10→退回 7/10，p≈0.02 有害）；**路径 3「换出预选池」执行并全量验证后回退**（§19.5：总分没变好且会冻结 baseline 写入）。判据：**不要为了某个模型的问题去改尺子**。路径 2 未启动 |
| P0 | 主模型 `eligible=True` 的方向 | 不是跑批问题：单元不稳定率 3.3% ⇒ 一趟零 unstable 概率约 1.7%。先决定压底噪 / 改口径 / 接受主模型不出正式 baseline（findings §17.6） |
| P2 | `cp.adaptive.weather-outing`：**首轮把 `adaptive` 判成 `simple`** | L3 已还清；`replan 空计划`已补确定性守卫（§22.2/§22.3）。当前主要问题在**首轮 complexity 分诊**：2026-08-10 下午 36 样本 **10 次（≈28%）**首轮判 simple，上午 11 样本 **0 次**，注入逐字相同、工作树无改动可解释（§22.5）。⚠ **不要再加知识**——guide few_shot 里就摆着 `complexity=adaptive` 且每轮注入 |

2026-08-10 已收口（不再是待办）：三条未晋级候选**全部按跨进程契约取证完毕**——
`cp.hvac-news.swapped` 2/6（尺子抓对了 `limit="今天的"`，维持严格口径）、
`nq.hvac.reported` 2/6（转真缺陷，**同日已修**：补范例 `chitchat#12`，
A/B 5/12 → 11/12、p=0.027、七条护栏零回归，findings §21）、
`nq.match.lastweek` 5/6（边界方差，不动）；
weather-outing 的 L3 claim 已建（见上）；snooze /「离开X」/「到X之前」三个提醒词形
已补端到端用例，两趟各 3/3（findings §20.1/§20.4/§20.5）。
`nq.hvac-keep.dont` 的「短句检索够不着」已定性并**证伪了修法**：洞是真的
（0.305 vs 0.34，对称 Dice 惩罚短句），但补上够得着的范例后 15/18 → 16/18、
p=1.000，已退回（§22.1）。**该用例残余按模型方差记账，不要再当检索问题查。**

2026-08-10 收口：`f0af9c0` 点名的 6 条 unstable 全部转绿（两条真缺陷 §17.2/§17.3、
三条单跑即全绿、一条随修复转绿）；`os.open.sunroof` 的范例吸引子已补对照（§17.7）；
宽 journeys 两条外部依赖残留已收（B3-1 gold 改条件式，B3-2 归高德侧）。
已完成项和中间数字只在 findings / review / agents-history 留档，不再回填本节。

## 11. 自查清单（提交前逐条过）

- [ ] 这一批是**只动尺子**还是**只动生产**？提交信息说清楚了吗？
- [ ] 改了语料/知识资产 → 跑过 `--layer l0`（discovery 与 gate 各一次）了吗？
- [ ] 改了口径 → 规格 §12 改了吗？引用过旧数的文档标注作废了吗？
- [ ] 新写的断言，**先注入缺陷验证过它会红**吗？（否定命题守不住肯定性质）
- [ ] 报出的每个比率，分子分母都看过吗？分母为 0 的地方写的是 `null` 不是 100% 吧？
- [ ] 做 A/B 之前，**证明过两臂真的不同**吗？（2026-08-10：给 `replan()` 加了形参，
      harness 没跟着传，24 个样本两臂逐字相同，差点据此否掉自己的守卫——§22.4）
- [ ] 跑 pytest 时**没有**带 `PYTHONIOENCODING=utf-8` 吧？带了会让拉子进程的用例
      在 Windows 上假红（子进程按 UTF-8 写、父进程按 GBK 解，findings §20.6）。
- [ ] 全量 `pytest` 有红 → **与 clean HEAD 逐条对照过**吗？
      对照法是 `git stash` 后**同目录**重跑（先 `git diff > 备份.patch`，pop 后 diff 对拍）；
      **不能用新建 worktree** —— 它缺 `gen/python` 等 gitignore 产物，分母不同。
