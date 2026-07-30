# skills/exemplars/ — 范例库（M5 P1，数据飞轮的核心新机制）

> 依据：`docs/design/2026-07-28-intent-accuracy-data-flywheel.md` §5-P1。
> **本文件是范例库契约的唯一真相源**（同 `skills/README.md` 之于 skill 层）。
> 实现 `orchestrator/cloud/exemplars.py`；门禁 `test/eval_exemplars.py`；
> 工具链 `scripts/exemplars.py`。

## 它解决什么

在此之前，修一个落域 badcase 的标准产物是**正则**——往目标 Agent 的 manifest 加一条
`route_hints`（全仓 32 条里 31 条是 `policy=replace`，在 LLM **之后**整条改写计划）。
后果不是「规则不好」，是**规则只进不出**：没有任何流程会问「这条 hint 模型现在自己会了
吗」，priority 空间靠跨 agent 手工避让，每加一条全局耦合深一分。

范例库把标准产物换成**数据**：一条 `话术 → 正确落域` 的记录。它被检索后作为 few-shot
进 planner prompt，**不做任何硬路由**。

| | route_hints | exemplars |
|---|---|---|
| 作用点 | LLM **之后** | LLM **之前**（prompt 内） |
| 作用方式 | 硬改写计划（replace/append） | few-shot 参考，模型仍可自行决定 |
| 写错的后果 | **事故**（模型判对了也被踩掉） | **噪声**（占了预算，仅此而已） |
| 出口 | **有了**（`test/hint_retirement.py` 双臂裸跑，M5 P2；首条退役=vision#0） | 删一行就没了 |

所以它是权威链的**最软层**：

```
VAL / payment-gateway / Runtime Policy
  > Capability Manifest（require_confirm / permissions）
  > Plan Validator
  > PlannerPolicyPack（软）
  > PlanningGuide（软）
  > Exemplar（最软——只是「别人这么说过，当时是这么落的」）
```

一个 badcase 三选一的判据：**路由错**（教科书形态、模型该会却总不会）→ route_hint；
**知识缺**（该拆没拆、该串没串）→ skill guide；**说法没见过**（同一件事换个说法就
落错）→ **exemplar**。默认选 exemplar——它是唯一一个写错了不会伤人的选项。

## 目录与 Schema

```
skills/exemplars/<domain>.yaml      # domain = intent 的域，如 nearby / navigation
```

```yaml
domain: nearby                      # 必须等于文件名（CI 硬校验）
exemplars:
  - text: 附近有什么咖啡店            # 用户原话（脱敏后）
    plan:                           # 正确落域。intent 必填且必须真实存在（CI 硬校验）
      - agent: nearby               #   agent 可省；首步 intent 的域必须等于文件 domain
        intent: nearby.search
        slots: {keyword: 咖啡店}     #   槽位骨架，可省（manifest 导入的一律无槽）
    source: trace                   # manifest | trace | manual（封闭集）
    added: 2026-07-29
    tags: [badcase]
    note: 被视觉 hint 劫持            # 可选，≤80 字
```

**只追加不插入**：`eid = <domain>#<1-based 序号>` 是 obs 归因（`plan.exemplars`）里的
标识，插队会让昨天日报里的 eid 指向别的条目。所有工具的写入路径都是「读 → 去重 → 追加」。

## 检索与注入

- **词法通道**：IDF 加权 Dice（bigram）。为什么不是裸 Dice——范例文本只有 5-15 字，
  裸 Dice 会被功能词 bigram 支配（首轮扫描实测「请问现在是什么时间」靠「现在」「什么」
  检回了 vision 范例）。另一条路是维护停用词表，但那正是这一期要消灭的东西；
  **IDF 是语料自己长出来的权重**，投一个范例文件即自动重算。
- **语义通道**（`EXEMPLARS_RETRIEVAL=hybrid`，默认）：query 与范例文本余弦，经
  llm-gateway `Embed`（与 skills/registry/memory 同源，共用 `orchestrator/cloud/embedding.py`
  出口与失败冷却——网关挂了两条通道一起回落词法，而不是各超时一次）。
  词法命中**恒保留**，语义只补位词法漏召的 paraphrase。**fail-open**：Embed 不可用
  → 该轮纯词法 + 30s 冷却，绝不堵规划。
- **向量后台预热**：范例是百量级，逐轮补齐要几十轮语义才生效；首次检索起一个后台任务
  分批填（失败即停、下轮再起），同时每轮的 query embedding **顺带捎带** 7 条未缓存范例。
  预热未完成不影响可用性——已填的参与语义、没填的走词法。
  ⚠️ **运行注意**：容器刚起的**头几秒**语义通道只是部分可用（200 条约需 25 次 Embed，
  实测真栈重建后紧邻的两条请求里第二条 `exemplars` 仍为空）。稳态无影响，但
  **短命进程（评测/CLI）必须先 `await store.warm_blocking()`**——不预热的 A/B 等于只测
  词法档，会系统性低估语义通道。首轮 N3 测量就栽在这里：翻正的句子 `inj` 是空的，
  那是采样方差不是范例的功。`test/eval_exemplars.py --live` 已内置预热。
- **同域去重在选取时生效**：同一 domain 最多进 1 条。「附近咖啡店」旁边摆一条 nearby
  和一条 navigation 的对照，比摆三条 nearby 有用得多。
- **预算硬帽** `EXEMPLAR_BUDGET`（默认 700 字符）+ `EXEMPLAR_TOP_K`（默认 3）。
- 注入位置：**规划知识块之后、上下文之前**。块内抬头写明「仅供参考不是规则；与上方
  规划知识冲突时以规划知识为准」——位置与文案一起表达优先级，不靠模型揣摩。
- **T2 再规划 / 挂起恢复继承**：按 `plan.exemplars` 实际注入名单重渲染（同 skills
  的 `render_for_names`），不重新检索。

## 三个来源

```bash
# ② manifest 199 条 examples 盘活（一次性；此前是死资产——不进 planner prompt，
#    只喂 registry 的逐字符打分）
python scripts/exemplars.py import-manifests --apply

# ① badcase 标注转化（消费 P0 落地的 /api/export/labels；一次标注两种资产：
#    这里出检索范例，P2 的 RoutingBench 直读同一 endpoint 出评测用例）
python scripts/exemplars.py from-labels --since 2026-07-29 --apply

# ③ evolve 第四类提案：route_error/slot_error 默认产**范例草案**（scripts/evolve.py）
python scripts/evolve.py all           # 草案落 .work/<date>/proposals/exemplar-*.yaml
```

来源③是 N4（提案可应用率）从 0% 起飞的路径：范例草案不改任何运行规则、天然过 CI 门禁，
人审只需回答一个问题——「plan 里的 intent 是不是这句话的正确落域」。
**治理⑤不变**：脚本只改工作区文件，入库仍走人审 + git。

`source` 是治理信息，不是装饰：`manifest` 是一次性盘活，**`trace` 才是飞轮在转的证据**
（`python scripts/exemplars.py stats` 看结构）。

## 金标裁定：地盘冲突（2026-07-30 补，台账 `boundaries.yaml`）

199 条初始金标来自 14 份 manifest 的 `examples`，而**manifest examples 是「我这个能力能答
这句」写出来的，天然不判别化**——写的人不会去想「这句是不是更该归别人」。批量导入时，
三处过期/重叠的地盘声明被一起激活成了「判定尺自相矛盾」：

| 冲突 | 谁在抢 | 后果 |
|---|---|---|
| 找充电站 | navigation.search_poi / charging.find / nearby hint | `nearby#1` 的 replace 把模型判对的 `charging.find` 踩成 `nearby.search`；而**退役判定把「金标错了」读成「规则失效了」**（那 4 条「带着 hint 也答错」） |
| 找个评分高的川菜馆 | navigation.search_poi / nearby.search | 两条 capability 描述几乎逐字重叠（都写「搜 POI（餐厅/充电站/加油站/停车场）」）→ planner 掷硬币 → **正则被拉来当裁判** |
| 有天气预警吗 | info.alerts / safety.weather_alert | 描述判别（后者是「对驾驶的影响」）但**例句一句也不体现驾驶角度**，只差语气词 |

三条可复用的判断：
- **盘活死资产的同时也会把死资产里的错误一起激活**；地盘搬家必须全局收口（旧声明会留在
  另一个 agent 的 examples 里、留在注释里）。
- **两个 capability 的描述重叠到分不开时，规则必然被拉来当裁判**——修描述才是修根因，
  给 guard 追词只是延后。判别化的判据用**产出形态**（给人挑的多候选 vs 给车用的单目标），
  不用类目枚举。
- **为某条规则写的语料，它的 gold 就是那条规则的输出**：双臂裸跑只能证明「摘掉它 gold 就
  不满足」，不能证明规则是对的。所以「带着 hint 也答错」那一档**必须由人裁定**。

裁定台账见 `boundaries.yaml`（含「为什么是台账不是阈值」的实测数据：假冲突的相似度可以
高于真冲突，词法与语义两个通道都是）。**只登记「判为两回事」；判为冲突的必须改金标。**

**改判（移域/删除）的记账**：范例是「只追加不插入」（eid=`<domain>#序号`），所以从文件
中间删一条会让其后条目的 eid 全部前移、昨天日报里的归因指向别的条目。改判时在 commit
里写清移动了哪些 eid；本次改判移动了 `navigation#15..#22`（删掉 #14「找个评分高的川菜馆」
所致，它已改判到 `nearby`）。

## 门禁（`test/eval_exemplars.py`，CI 阻断 + evolve nightly）

1. **契约静态校验（硬失败）**：顶层映射 / domain=文件名 / exemplars 非空列表 /
   每条 text·plan 齐 / **intent 真实存在**于 manifests ∪ 端侧意图集（typo 守卫）/
   首步 intent 的域=文件域 / source 封闭集 / tags 是列表 /
   **全局同句冲突**（同一句话在两处被标成不同落域＝语料自相矛盾，注进 prompt 是纯噪声）
   + **跨域近重复未裁定**（≥`boundaries.yaml` 的 `lex_min` 的跨域对必须在台账里被人裁过一次，
   且台账里两端文本已消失的陈旧条目也阻断——台账只进不出会腐烂）。
   运行时 loader 对这些一律 fail-open 跳过（保可用性），这里是硬失败（保主干整洁）。
   零网络：`lex_min` 只用词法通道，llm-gateway 不可达不能把 CI 变红。
2. **域路由探针**：拿**不在语料里**的句子（`mode_routing_cases` 设计上就「避开 manifest
   examples 原句」+ `route_hints_cases`）问「检回的范例指向的域对不对」，三个数——
   `hit`（域对）/ `miss`（**域错配，这才是伤害面**）/ `silent`（没检回，无害）。
   **域错配率 gate ≤20%**。
   反例概念在这里不成立：范例覆盖全域，「今天天气怎么样」召回 info 天气范例是正确命中
   不是噪声——衡量噪声只能看域错配。
3. `--scan`：词法/语义双阈值扫描。**默认值由它拍板**（同 skills 0.40 的来历）：
   首轮 166 例扫描（2026-07-29，真栈 Embed）：
   - **词法 0.34 是拐点**——0.30 多 1 hit 却多 3 miss，0.40 少 3 miss 却丢 15 hit；
   - **语义 0.65 是拐点**——0.70→0.65 是 +8 hit / +2 miss（4:1 划算），0.65→0.62 是
     +4/+4（1:1 不再划算），0.62→0.60 是 +0 hit / +1 miss（纯亏）；
   - 合起来相对纯词法：hit 63→**81**、miss 7→10、silent 96→75（命中时的域精度 ~89%）。
4. `--live --ab`：真 planner，`EXEMPLARS_MODE=full` vs `off` 域级对照——范例层有效性 Δ
   的唯一证据（信息性不 gate；同 `eval_skills --ab`）。基线
   `docs/reviews/eval/baseline_exemplars.json`。

## env

| 变量 | 默认 | 说明 |
|---|---|---|
| `EXEMPLARS_MODE` | `full` | full=注入｜shadow=只检索记录不注入（A/B）｜off=关。每轮实时读 |
| `EXEMPLARS_RETRIEVAL` | `hybrid` | hybrid=词法∪语义补位｜lexical=纯词法（零网络） |
| `EXEMPLAR_LEX_THRESHOLD` | `0.34` | IDF-Dice 下限，钳 (0,1] |
| `EXEMPLAR_SEM_THRESHOLD` | `0.65` | 余弦下限，钳 [0,1]。比 skills 的 0.40 高得多是应该的——skills 比「话术 vs guide 描述」跨文体，范例比「话术 vs 话术」同文体，余弦基线整体抬高 |
| `EXEMPLAR_TOP_K` | `3` | 单轮注入条数上限（重启生效） |
| `EXEMPLAR_BUDGET` | `700` | 注入块字符预算（重启生效） |
| `EXEMPLAR_EMBED_TIMEOUT` | `1.0` | 语义通道超时（秒） |
| `EXEMPLARS_DIR` | — | 覆盖范例根目录；缺省跟随 `SKILLS_DIR`/`<repo>/skills` 下的 `exemplars/` |

容器内 `skills/` 是只读挂载 → **投范例文件 30s 内生效**，不需要重建镜像。

## obs 归因

`cloud.planning` span 的 `exemplars` 属性，契约与 `skills` 对齐：
`<mode>:<eid>@lex:0.55` / `@vec:0.71`，超预算被裁加 `!clipped`。
badcase 先看这一行——**没检回 / 检回了没用对 / 检回了却被裁**是三种不同的失败。
