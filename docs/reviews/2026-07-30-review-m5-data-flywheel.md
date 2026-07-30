# M5 数据飞轮全量评审（P0→P3a，14 提交）

> 日期：2026-07-30
> 范围：`5e2c276..eeb4013`——RFC [`2026-07-28-intent-accuracy-data-flywheel.md`](../design/2026-07-28-intent-accuracy-data-flywheel.md) 的全部实现提交（P0 四提交 / P1 / P2 三提交 / P2 收口 / P3a / 四个记账提交），113 文件 +33892/−724
> 方法：RFC 逐项声称 ↔ 代码对照核验 + 关键 SQL/正则/并发路径亲读 + 门禁与全量单测亲跑。评审当日会话限额中断了并行分查，全部核验由单线程完成——**下方每条结论都有亲证，无转述**
> 结论：**批准入账。声称与实现高度一致，零 P0/P1 级缺陷、零红线违规、零假绿测试（本机口径）。** 3 条 P2 级记账缺陷当日修复（§4），INFO 级观察项已立卡或注释留痕（§5）

---

## 1. 实测证据（评审当日亲跑）

| 项 | 结果 |
|---|---|
| 全量单测（`make test` 同命令） | **2408 passed / 7 skipped**（6min26s） |
| targeted 门禁（evolve 13 组 / catalog 预算 / edge NLU 21 条 / 端云分歧断言 / exemplars 契约） | 75 passed（本机有模型，tokenizer parity 真跑了 transformers 对照） |
| `python test/eval_exemplars.py` | 204 条契约 OK + boundaries 门禁 + 域路由探针错配 2.5%（上限 20%）→ PASS |
| planning 铁律契约（`test_planning.py`） | 19 passed（「不改编排核心」仍钉死） |
| 规则存量实数 | **10 条**（info 3 / deep-research 2 / 其余五家各 1），= 「32→12→10」声称；`mcp-bridge#0`（require_confirm）确实保留 |
| 证据文件 | `baseline_exemplars.json` / `baseline_routing.json` / `hint_retirement.{minimax,deepseek}.json` + 收口日的 `.only-nearby` 局部复验报告全部在库，时间戳吻合 |

## 2. 逐阶段核验结论（声称全部成立的部分）

**P0**：`forbidden_hit` 只扫动态内容（`evolve.py` 的 `dyn` 仅案族原话+归因 note），ASCII
环视词边界让 `eval` 不再误伤裸 `val` 且中文禁词照常命中；degraded 信号优先读
`turns.plan_mode` 列、旧行回落详情接口；triage 批失败重试一次。collector 乱序合并**两个
方向都安全**：span 先到建骨架行，turn 后到的 UPSERT 只覆盖 `_TURN_FIELDS`
（intents/plan_mode/gold/badcase 均不在其中）；`gold_intents` 清理豁免在 `db.py::cleanup`
的 SQL 亲验（`badcase=1 OR gold_intents != ''` 全保留）。catalog 预算默认 16000，
`test_catalog_budget.py` 加载全部真实 manifests。

**P1**：IDF 加权 Dice 实现正确（未知 bigram 给最高权重——方向对：陌生 query 不判强匹配）；
同域去重在**选取时**生效（k×4 候选再去重截断）；`!clipped` 诚实归因；fail-open 全层
（parse LKG / 检索异常跳过 / 预热失败停手）；`embedding.py` 按 loop 重建 stub 属实；
T2 再规划/挂起恢复三断点（to_plan/serialize/restore）在 `engine.py` 一次做全。
**门禁与检索同源**：boundaries 门禁与运行时检索用的是同一个 `lex_score`，不存在两份实现漂移。

**P2**：`--limit-per-hint 0`=全覆盖（旧 `[:0]` 缺陷在 help 里留账）；`--intersect` 拒收
`.only-` 局部报告、`--from` 显式例外纪律不降级；`retire_hints.py` 文本级手术保注释；
退役注释模板完整（判据/全覆盖声明/MiMo 警告/迁移去向/`git log -S` 恢复命令——nearby 与
charging 抽查一致）；manifest loader 五列表字段 `or []`（`agents/_sdk/manifest.py`）；
防回加测试真实（载入全部真实 manifests 断言能力仍在、hint 不在，`_kw_pattern` 也有
`assert not hasattr` 钉死）；registry bigram+长度归一有真性质测试（desc 加长三倍断言
top-1 不变）；shadow 六态判定「无金标不装懂」（只报 diverges/agrees）。

**P2 收口**：boundaries.yaml 台账 18 对如数登记，「假冲突相似度可高于真冲突」四行实测数据
压阵，`lex_min=0.35` 反验与反例（0.288 的对靠「按对判不按条判」兜住）都在台账头；
「判为冲突不许登记」由结构强制（`rulings` 只收「两回事」裁定）；info#0 指代判据确在
guard 位（回溯绕过理由注释完整）；catalog 保护 14→13 改断言并留四条理由。

**P3a**：`mode()` 白名单只认 off/shadow、**无 on 挡**、异值回落 shadow；WordPiece 从
vocab.json 读（不猜行号）；缺任一文件即 disabled、懒加载单例带锁、推理在线程池、异常
全吞——**影子对主链零影响在代码层面成立**；`_assert_onnx_parity` 导出即验；transfer 车道
真调 `classify_structured` 判规则命中，「训练集里没有的类必然全错」偏斜有记账；compose
`../models:/app/models:ro` 只读挂载。P2-D2 的 `meta._edge_nlu`（fast_intent 规则初判透传）
与 P3a 影子（模型判定落 span）职责分明、互不混用。

## 3. 红线核验

- 车控路径零变化：hint 退役/范例注入/NLU 影子都不触碰 planner→executor→VAL→确认闸；
- `mcp-bridge#0`（shop.order，`require_confirm=true`）拒绝按路由评测退役——「路由评测
  不构成安全证据」的裁决被遵守；
- 端侧 NLU 无执行通道、无 `on` 挡位、θ 不参与任何权限判定；`test_edge_nlu_divergence.py`
  源码级断言「端侧初判不进 prompt、不留 env 开关」在跑且绿。

## 4. 发现：P2 级记账缺陷（评审当日已修，随本报告同提交）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | AGENTS.md 引用救援分支旧 hash：P2 收口写 `49335b7`（两处）、P3a 写 `1a9a0c1`——它们只在 `wip/m5-p3-rescue` 上（双 agent 摘 HEAD 事故的产物），main 上是 cherry-pick 后的 `47eac1c` / `86ec5d7`，按号查证据会落空 | 三处全部换成 main hash |
| 2 | 架构文档零 P3a 痕迹：v1.14 只覆盖 P1+P2，§3.2 双阈值伪码段（「写了半年第一次拿到真概率」的正主）无任何注记；CLAUDE.md 却把 P3a 挂在 v1.14 名下 | 架构文档 bump **v1.15**（§3.2 补实现对应注 + 版本记录行），CLAUDE.md 同步 |
| 3 | 设计→实现演化未回写 RFC：P1 门禁段写「每条范例自动生成 golden 进 RoutingBench」，实现反转为**范例默认不进 N1 分母**（拿被注入 prompt 的范例当评测金标是系统自证；`--include-exemplars` 仅诊断）——实现更正确但文档未更 | RFC P1 门禁段补演化更正注 |

顺手修的代码级小项（同提交）：`lex_score` 每次调用 `max(idf.values())` 的 O(V) 冗余
（调用方算一次传入，检索与门禁两处；改后 gate 分数逐条不变）；`collector/server.py` 两处
cleanup 注释漏提 gold 豁免；`embedding.py` 补「旧 channel 不显式 close」的有意不做注释；
`skills/exemplars/README.md` 补注入面警示（范例 text 原样进 prompt，人审+CI 是当前控制面，
**任何来源绕过人审自动入库前必须重估**）。

## 5. INFO 级观察项（不要求动作，已留痕）

- **tokenizer parity 在 CI 上条件性 skip**（无模型/无 transformers 即整体 skip）——守
  「训练/推理分词同源」的硬测试只在有模型的开发机站岗；第二层防护是训练导出时
  `_assert_onnx_parity` + vocab.json 同源落盘。→ **已立卡** AGENTS.md §4.0（与「live 车道
  进 CI」同议）。
- **`nlu.theta_high/theta_low` 运行时零消费方**（shadow 不 gating，属 P3b 脚手架）——正是
  本仓「没消费方的契约会潜伏」教训的形态。→ 已并入 P3b 卡（接线前先验 clamp）。
- 语料漂移：RFC 记探针 166 例/范例 200 条，现为 161/204——历史时点记录不改，下次校准对齐。
- evolve 07-29 日报里两条 badcase 是 mock 回显（当时栈处 mock 态的评测流量）——归因诚实
  （unknown 不硬编），非缺陷。

## 6. 方法论小结（评审视角）

这批提交最值得沿用的形态是：**每一条「不许悄悄 X」都配了机器门禁**（hint 不许悄悄回加
=manifest 断言；跨域对不许悄悄新增=boundaries 门禁；台账不许腐烂=陈旧条目阻断；
`_kw_pattern` 不许复活=hasattr 断言），而每一条「有意不做」都写了判据与复议条件。
评审自身的教训一条：**记账文档引用提交号必须在合入 main 后回填**——救援分支的 hash
在事故当日是对的，cherry-pick 之后就成了断链（本次 §4-1 的根源）。
