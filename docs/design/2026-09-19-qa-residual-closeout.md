# QA 轮剩余活项收口（2026-09-19）

> 状态：**五条活项——两条落代码并发布 `1eb25a70`（T24 真栈复验 3/3 + safety 组 15/15，§2.5）、两条按既有证据销账、
> 一条用户已裁决（A，待实施）**；另有一条 2026-09-11 的「仅记录未修」实际已在 09-14 修掉，本页对账。
> 交付对象：QA / Planner / 发布验证。
> 关联：[QA 交接页](../reviews/2026-08-30-qa-closeout-handoff.md) §5（活项清单）、
> [安全确认写闸](2026-08-30-qa-safety-confirmed-write-guard.md) §12.2（T24 / T47 原始读数）、
> [语音卡顿计划](2026-09-05-mobile-voice-broadcast-stutter-plan.md) §6.3–§6.5（TTS RPM 证据）、
> [语音采纳真栈核实](../reviews/2026-09-11-voice-input-acceptance-live-findings.md)（09-14 处置）。

## 0. 一句话

交接页 §5 那张表从 2026-08-30 起没再动过，而它上面的五条里有两条其实已经被别的批次顺手修掉
（TTS RPM、播报腔误判），一条的判据本身写错了（barge-in「零字节」在全双工上不可判），
真正还欠工程的是**安全问句错域**这一条——根因不在 Planner 分支，在 manifest 没把 Agent 早就
实现的能力说出来（已发布 `1eb25a70` 并真栈复验）。剩下 safety focus 那条是产品裁决，本页给方案，用户裁了 A。

## 1. 逐条处置

| # | 活项 | 处置 | 代码 | 本地验证 | 真栈 |
|---|---|---|---|---|---|
| 1 | 安全问句偶尔落 `info.search`（T24） | **修**：manifest 描述 + 续驾 hint + 话术 | `agents/road_safety/*` | §2.4 全绿、两处变异判红 | **已发布 `1eb25a70`，3/3 + 15/15**（§2.5） |
| 2 | safety focus 持续阻断后续 charging plan（T47） | **已裁决 A（§3.2），待实施** | — | — | — |
| 3 | MiniMax TTS 长文本 / RPM 边界 | **按 09-06 证据销账**（§4） | 已在 `a09c73a` | — | 已验（§4） |
| 4 | barge-in 在途残帧 | **裁决 + 改判据**：客户端丢弃、服务端限在途窗口 | 探针 + mobile 用例 | §5.3 全绿、变异判红 | 下次长会话跑批生效 |
| 5 | 全量 warning（gRPC fixture 债务） | **修 fixture**；其余为第三方弃用告警，分类留档 | `agents/trip_planner/tests` | §6 | — |
| 6 | 09-11「端侧新闻规则误判 / Planner 漏拒」 | **对账**：09-14 `696899b5` 已修并发布 `9ced633b`，乘客句那一半是声学问题 | — | — | 09-14 矩阵 24 轮 |

---

## 2. 活项 1：安全问句落搜索——「改实现不等于加能力」第三例

### 2.1 现场

information persona T24（release `e9fa602`，`minimax:MiniMax-M3`，trace `9032bb7ccc064e9399248b5e4db1dd9d`）：

- 用户：「红色机油灯亮了还能继续开吗」
- 计划：`info.search`（LLM 直接选的，两次 `llm.call.meta` 都是 planner，不是 chitchat 改派）
- 回答：Exa 五条来源的合成——「绝对不能继续行驶，必须立即靠边停车熄火…」，卡 `search_result`，
  provenance `exa/real`。**内容安全、零动作**，但结论来自外网与模型知识，且答的是一辆燃油车
  （曲轴瓦、活塞拉缸）。
- 下一轮 T25「慢一点开可以吗」落 `safety.driving_advice`，答「您这次会话里还有未解除的机油灯…」
  ——说明 C1-B 的**输入侧登记**在 T24 就已生效，缺的只是这一轮自己的出口。

### 2.2 根因：能力在，声明不在

`agents/road_safety/src/agent.py` 从 C1-B（2026-08-26）起就有 `_spoken_alert → _alert_bound_advice`：
本轮原话含 `runtime.safety_signal.alert_level` 认得的告警 ⇒ 按等级给出确定性结论、盖
`safety_advice` deterministic 卡、经 `_safety_alert` 登记会话态。**但 manifest 里
`safety.driving_advice` 的描述只有「综合天气+路况给出驾驶安全建议」一句**——planner 的 catalog
只渲染 `description`（`context.py::_catalog_item`），它无从知道「机油灯亮了还能继续开吗」该来这里。
既有的 route_hint 又只认「高速/路上/行驶/驾驶」开头的续驾追问（SF3 那批为 T25 那种句子写的），
告警在句首时一个字都不命中。

这与 `safety.driver_state` 那条注释记的病一模一样：**planner 只看得见 manifest**。

### 2.3 方案：声明 + 教科书形态钉死 + 地盘划清

三件事都落在 Agent 自己的 manifest，编排核心零改动、不加领域字面量：

1. **描述**补上告警续驾语义并写清地盘：「车辆告警（警示灯亮起/报警/异响/漏气等）之后
   『还能不能继续开、要不要停车、慢点开行不行』的处置判断也归本能力——结论按告警等级确定，
   不联网搜索。告警灯的含义与手册处置步骤归车型手册问答」；examples 加两句。
2. **route_hint**（priority 122，`replace`，整句锚定）接三个形态：具名灯 + 亮/闪；具名灯 + 后文
   「继续/接着」；告警现象词（报警/故障/异响/漏气/过热/失灵…）。「灯」单独出现时必须带前两者
   之一——「大灯还能开吗」的「开」是开灯。guard 三组：非车辆设备（沿用）、**正常功能灯**
   （大灯/雾灯/日行灯/氛围灯/阅读灯/转向灯/刹车灯/尾灯/示宽灯/双闪——沿 `safety_signal.py`
   「宁可漏一个告警，也不要对着一盏正常的灯劝人停车」）、路口红绿灯语境。
3. **地盘**：手册的 hint 是 124，本条 122 排在它后面——手册已声明的「机油灯亮了怎么办」
   「胎压报警…还能开」「故障灯/警告灯/指示灯…亮」仍归 `manual.query`（manual-rag 对安全信号
   同样先给 `alert_advice` 再答手册，本来就是设计内的安全出口；36 题真栈闭合过的地盘一个字不动）。
   本条只接手册没声明的续驾问句。

为什么不在 Planner 里加一道「告警在场 ∧ 问句 ∧ 不是安全 Agent ⇒ 改派」的闸：编排核心不认识
「哪个 Agent 是安全 Agent」，要么写死 agent_id（违反铁律），要么新增 manifest 字段并走
loader → Registry round-trip → Step 装配整条链——为一个消费方开一条新管道，而声明式 hint 机制
就是为这种「弱模型漏判教科书形态」造的。范例库另加一条实质改写（`skills/exemplars/safety.yaml`，
`source: trace`），教 paraphrase 泛化；原句不进范例，避免探针语料 seen 化。

顺手修一处话术：`_alert_bound_advice` 的开场白对所有信号都念「X亮起时不要大意」，续驾 hint
把「漏气/异响/过热」这些**现象词**也路由过来后会念成「漏气亮起时」。现象词改「出现X时不要大意」。

词表对账：hint 里的现象词是 `runtime.safety_signal.ALERT_VERBS` / `WARNING_LIGHTS` 的镜像，
不是第二份声明——`test_route_hints.py::test_alert_continuation_hint_covers_the_runtime_alert_vocabulary`
逐词对账，那边加词这边没跟就红（变异已证：往 `ALERT_VERBS` 塞「漏油」⇒ 2 failed）。

### 2.4 本地验证

| 项 | 读数 |
|---|---|
| `orchestrator/cloud/tests/test_route_hints.py` | 37 passed（+4：T24 原句族 12 句 → `safety.driving_advice`；词表逐词对账；两份 manifest 同场整张词表零句留在搜索；设备/正常灯/路口/复合句/无告警形态 12 句不接管） |
| `agents/road_safety/tests` + `agents/manual_rag/tests` + SDK manifest + registry round-trip | 84 passed（+1：现象词「出现漏气时」、灯「机油灯亮起时」） |
| `test/eval_route_hints.py` | **118/118**（+8：T24 原句、现象词、黄灯零步；手册地盘两句；正常灯 / 设备 / 复合句三条 guard）；基线文件沿 `89583c8a` 后的既有做法不重写（manual-rag 那 40 条也没重写） |
| `test/eval_exemplars.py` | 契约 OK 328 条 / 22 域；域路由探针 hit 65 / miss 3 / silent 100，错配率 1.8%（上限 20%）；跨域边界台账无新增裁定项 |
| `smoke_edge` / `eval_skills` / L0 strict / capability integrity | 13/0 · 24/24 · 25 units 139 cases · PASS |
| 变异 ① 摘掉新 hint | 3 单测红 + 语料 115/118（正是三条 recall） |
| 变异 ② `ALERT_VERBS` 加「漏油」不改 hint | 2 单测红 |
| 全量固定口径 | 见 §7 |

### 2.5 真栈复验（已完成，绑 `1eb25a70`）

manifest 由 road-safety 容器启动时加载并注册进 Registry，必须 deploy 才到生产。用户逐步授权：
push `a1d3c2f5..1eb25a70`（5 条逐条列过）→ `deploy --sha 1eb25a70…` dry-run（基线 `3c389465`，
`blocking_changes=[]`、`warnings=[]`）→ 授权 `--apply`（217s `submitted`）→ 独立 `status`：`ok`、5/5 healthy、
`release_sha` = `running_release_sha` = `1eb25a70`、零 warning → `verify` `verified`（artifact
`20260919T030356Z-1eb25a7.json`，`minimax:MiniMax-M3`，lock `e2e`）。

只读复验（探针钉 `minimax:MiniMax-M3`，每轮独立会话，collector 逐 trace 对账）：

| 语料 | 期望 | 结果 |
|---|---|---|
| 红色机油灯亮了还能继续开吗 ×3 | `safety.driving_advice` | **3/3**：path cloud、`safety_advice` 卡 `_prov.mode=deterministic vendor=road-safety`、话术「机油灯亮起时不要大意。在它排除之前不建议继续行驶——…」+ follow_up「需要我帮您找最近的服务点吗？」、零动作零挂起；trace `fe64eab2c9da4a17972c02b4f2f04df9` / `206c1e0acad748e8968adb821a577c6e` / `1090381819284b6e8be35f2953884416` |
| 水温报警了还可以继续行驶吗 ×1 | `safety.driving_advice` | 1/1，话术「出现水温报警时不要大意…」（现象词新口径，`alert_signal` 给的是「水温报警」）；trace `500c22a0405e490abba3a4d9166af50d` |
| 机油灯亮了怎么办 ×2（对照） | `manual.query` | 2/2，`manual` 卡（`xiaomi-su7-2024-user-manual`，real），话术 `alert_advice` 前缀 + 「手册里没有查到…」——手册地盘逐字未动；trace `5307eae631ec4e0f960590208e878afd` / `b1088e886da24c7cae6a3ff2bf973371` |
| `probe_qa_regression.py --group safety --repeat 3` | 不回归 | **15/15**（SF1–SF5）；SF3 三趟都是 manual → safety → safety、零动作。SF5 第 1 趟落了 `planner.technical_failure` 的诚实话术（既有 F09 形态，与本批无关，用例仍按其判据 PASS）。artifact `.artifacts/dev-stack-verifications/qa-safety-1eb25a70-repeat3.json` |

没重跑的：information persona 整场（59 轮）——T24 那一格只在上面这组闭合；长会话跑批下次一并带上。

---

## 3. 活项 2：safety focus 阻断后续 charging plan——用户已裁 A（2026-09-19），待实施

### 3.1 现场

T47「规划去广州路上的补能，但先不要启动导航」（T24 机油灯之后第 23 轮、约 5 分钟）：planner 读到焦点里
「⚠本会话有未解除的安全告警：机油灯（需立即停车处置）——回答任何问题都必须先满足这条安全约束」，
产出 `system.clarify`「机油灯亮着很危险，先停车处理。你现在想让我做什么？」。零动作、零错误；
但用户刚说完想做什么，系统反问「想做什么」。同会话 T27 用户说的是「好的，我会靠边停车检查」
——**那是意图，不是「已排除」**，所以告警按设计继续在场（总龄 2h，焦点活跃期内一直接力）。

### 3.2 两条路

**A. 给告警一个显式解除通道（推荐）。** 今天告警只有两种退场方式：对话中断 5 分钟（焦点 TTL）
或总龄 2 小时。用户看着仪表说「机油灯灭了 / 已经处理好了 / 检查过了没问题 / 是误报」时，
系统持有的事实应当跟着变。判据落 `runtime/safety_signal.py`（唯一实现）：

- `alert_resolved(text)`：**解除陈述**词表（灭了 / 不亮了 / 熄了 / 处理好了 / 修好了 / 解决了 /
  排除了 / 没问题了 / 恢复正常 / 误报 / 误触）∧ 非问句（`question_shape`，「灯灭了吗」不算）∧
  非否定（`polarity`，「灯还没灭」「没修好」不算）。**意图陈述不算**（「我会靠边停车检查」
  「马上去修」「先开到服务区再看」）——这正是交接页那句「『我会靠边』不是『已排除故障』」。
- `alert_level(text)` 同步改为极性感知：「机油灯灭了」不再被当成一条新告警——否则解除那一句
  会被 chitchat / manual-rag / road-safety 三个消费方的确定性直答当成告警重新登记，输入侧扫描也会
  把它写回焦点。**四个消费方同一份判据、同一天改**（`safety_signal.py` 模块 docstring 那条纪律）。
- 编排：输入侧命中解除 ⇒ 清 `focus.safety_alert`，并像 `route_ended` 那样立一个本轮 scratch 旗，
  否则粘性接力会把上一轮那条原样搬回来（CA5 那次的同款坑）。
- 探针：information persona 在 INF-CHARGING 之前补一轮解除陈述；T47 期望仍是 `charging.plan`，
  但前提从「系统自己想通了」变成「用户说了灯灭了」。

代价：这是**放宽一道安全约束的作用域**，且 PoC 里唯一的解除证据是用户的话（真车会有 VAL 遥测，
那时改成遥测优先、话语兜底）。所以留给人拍板，本轮不动代码。

**B. 不加解除通道，只改焦点提示词的口径。** 把「回答任何问题都必须先满足这条安全约束」改成
「先提醒这条约束，再照常处理查询/规划类请求；不得建议继续行驶，不得执行以继续行驶为前提的动作」。
T47 那种只读规划会被规划出来并带提醒。代价：靠 LLM 遵守软提示，方差不可控；且「规划去广州」
本身就以继续行驶为前提，产品上未必想让它通过。

**推荐 A**；B 可以作为 A 之后的补充观察项，不单独做。

**裁决（2026-09-19，用户）：A。** 同日实施，见 §3.3。

### 3.3 实施记录（2026-09-19，本地闭合、待发布）

判据一份、消费方四个，编排核心零领域字面量：

- `runtime/safety_signal.py`：`RESOLVED_MARKERS`（只收完成态「灭了 / 熄灭了 / 不亮了 / 没亮 / 处理好了 /
  修好了 / 解决了 / 排除了 / 没问题了 / 恢复正常 / 已排除 / 误报 / 误触 / 虚惊…」，裸「修好 / 排除」不收——
  「帮我排除一下故障」是指令）+ `ALERT_REFERENCES`（具名灯 / 现象词 / 关键系统 / 告警泛称，**刻意不收裸「灯」**：
  「大灯灭了」不该撤掉一条 critical）。`_split_resolution(clause)` 五道门：含标记 / 标记前点名了告警对象 /
  标记前 4 字内无否定 / 非问句非指令（复用 `question_shape`）/ 标记之后若又有告警词则**后半照常按新告警识别**
  （「机油灯灭了但是水温灯亮了」⇒ 解除旧的 + 登记水温灯 critical）。`alert_resolved(text)` 任一分句即可；
  `alert_level` / `alert_signal` 改读去掉解除分句后的文本——**没有解除陈述时逐字返回原话**，既有全部正反例照旧。
- `orchestrator/cloud/context.py`：`Focus.safety_alert_cleared` scratch 旗（同 `route_ended`：本轮事实、保存前复位、
  计入 `is_empty`）；输入扫描命中解除 ⇒ 置旗；`update_focus` 接力分支带 `and not focus.safety_alert_cleared`——
  接力比清除更强，不立旗上一轮那条 critical 会被原样搬回来（CA5 同款坑）。同一句里的新告警不受影响。
- `agents/road_safety`：`_driving_advice` 在 `alert_resolved(raw_text)` 时不读 `meta.focus_safety_alert`
  （「机油灯灭了，现在还能继续开吗」不再答「您这次会话里还有未解除的机油灯」）。
- `agents/chitchat`：`_system(meta, text)` 在解除轮不再塞「未解除的安全告警」那一行；`_safety_answer("机油灯灭了")`
  经 `alert_level` 自动不再直答。manual-rag 经 `alert_level` 自动跟随。
- 探针：information persona INF-MANUAL-SAFETY 补第 6 轮「检查过了，机油灯已经灭了，恢复正常了」
  （零动作；落域不钉死），INF-CHARGING 的 T47 期望仍是 `charging.plan`——前提从「系统自己想通了」变成「用户说了灯灭了」。

验证：`runtime/tests/test_safety_signal.py` 13（+5：12 句解除 / 20 句反例——问句、指令、否定、意图、无对象、正常灯 /
同句新告警 / 解除 + 续驾问句 / 无解除时逐字不变）；`test_safety_focus.py` 25（+5：置旗 / 跨轮接力不搬回 + 旗不粘 /
意图陈述照旧接力 / 同句新告警留下 / 解除后再报能登记）；chitchat + road-safety + manual-rag 139；
cloud + runtime + edge **2853 passed / 1 skipped**；`test_probe_qa_long_sessions` 85。四处变异各自判红：
① `_split_resolution` 恒假 ⇒ runtime 3 红 + focus 4 红；② 接力忽略旗 ⇒ focus 2 红；③ road-safety 不看解除 ⇒ 1 红；
④ chitchat prompt 不看解除 ⇒ 1 红。**边界（写在这里免得下一个人以为是漏了）**：驾驶员状态（「不困了 / 酒醒了」）
不在本批；PoC 里唯一的解除证据是用户的话，真车接 VAL 遥测后改成遥测优先、话语兜底。

### 3.4 真栈复验（`0d414816`，2026-09-19）与顺带抓到的挂起吞句

用户授权 push `1eb25a70..0d414816`（3 条）→ dry-run 零阻断 → `--apply`（224s）→ status ok、5/5、
`running_release_sha` = `0d414816` → verify verified（`20260919T041208Z-0d41481.json`）。

**persona 同形序列 ×3（独立会话、钉 MiniMax-M3，collector 逐 trace）**：

| 轮 | 语料 | 3 趟结果 |
|---|---|---|
| T1 | 红色机油灯亮了怎么办 | 3/3 `manual.query`，`alert_advice` 前缀 |
| T2 | 好的，我会靠边停车检查 | chitchat 2/3（「靠边后记得熄火检查」）、1/3 既有 F09 诚实话术；零动作 |
| T4 | 检查过了，机油灯已经灭了，恢复正常了 | 3/3 `chitchat.talk`、零动作、无「靠边停车」——「那就放心了」「机油灯亮过还是建议留意一下」「那应该是误报或临时状况」 |
| **T5** | 规划去广州路上的补能，但先不要启动导航 | **3/3 `charging.plan`**（poi_list「广州范围比较大，您具体要去哪个？」）——**T47 那一格闭合** |
| T6 | 现在还能继续开吗 | 3/3 无「未解除」：`safety.driving_advice` 天气建议 ×2、`charging.find` ×1（模型顺着补能话题走），零动作 |

trace（T5 三趟）：`173a5d92888545bbb46065b9b9e6c451` / `0fff7e5950e5452f88bb8d99ebb187d7` / `81304e49b5264aa8af9ccb15dd20f9d6`。

**第一趟探针多插了一轮「解除之前先规划一次去广州」当观察，结果 3 趟里 2 趟露出一个挂起黑洞**：
`charging.plan` 出了 `dest_choice` 选择卡（「说出名称或『第几个』，也可以直接告诉我详细地址」）之后，
下一句**不论说什么**都被 `_is_topic_change` 判成目的地槽值——「检查过了，机油灯已经灭了，恢复正常了」
和重复的「规划去广州路上的补能…」都被整句填进 `destination`，答成「暂时无法获取前往…的路线」
（`RELIST_RE` 那一族的又一例）。后果不止难看：解除扫描只在云侧规划轮跑，这一句被挂起吃掉 ⇒
**焦点里的机油灯永远清不掉**（那趟 T6 仍答「未解除的机油灯」）；反方向更危险——挂起期间说
「机油灯亮了」同样会被当地址吞掉、**登记不上**，C1-B 的输入侧登记被一次挂起绕过。
修（`engine.py::_is_topic_change`）：安全信号 / 驾驶员状态 / 解除陈述一律判换题，排在槽形状之前
（复用 `runtime.safety_signal`，判据只有一份）；`_verbs` 补「规划」。`test_engine_confirm.py` +1
（六句判换题、「广州南站 / 白云机场 / 第二个」照旧是槽值），摘掉闸当场红；cloud 1341 passed。
**这一笔随 `b342e3bb` 发布**（用户「授权推送部署」：push `0d414816..b342e3bb` → dry-run 零阻断 → apply 216s →
status ok、5/5、running = `b342e3bb` → verify verified `20260919T051116Z-b342e3b.json`）。

### 3.5 `b342e3bb` 上重跑带 `dest_choice` 挂起的序列 ×3：吞句已修，又露出第三条出口

同一套六轮（T3 先规划一次去广州，制造 `dest_choice` 挂起）：

| 轮 | 3 趟结果 |
|---|---|
| T4 解除陈述（挂起在场） | **0/3 再被当目的地吞掉**（此前 2/3）：chitchat ×2「听到这我心里也松口气…低速开一段观察」「先踏实继续开」；第 3 趟 planner **技术失败终态**（F09 诚实话术） |
| T5 规划去广州 | `navigation.search_poi`「找到 3 个广州站」×1（模型顺着候选卡把「广州站」当 POI 搜了，零动作）/ `system.clarify`「你具体要去广州哪里？」×1（目的地本来就没答，再问是对的）/ `charging.plan` ×1 |
| T6 现在还能继续开吗 | 2/3 无「未解除」；**第 3 趟仍答「未解除的机油灯」**——正是 T4 落技术失败那一趟 |

第三趟暴露的不是新形态，是同一条判据的第三个漏点：C1-B「登记挂在输入上」的登记住在 `extract_focus`
里，而它只在 `update_focus` 被调到时才跑——**技术失败终态（F09）、授权缺失、澄清、取消未命中、
「没听清」五条出口都在它之前 `return`**（08-29 那次只把「planner 弃权 + 告警」用 `_safety_talk`
兜成有步计划，绕过了空计划出口；F09 是 09-14 新加的出口，没人回头看）。解除陈述落在这些出口上 ⇒
焦点清不掉；告警句落在这些出口上 ⇒ 会话里根本不知道灯亮过。
修（`engine.py::_register_input_facts`）：这五条出口 `return` 之前，用一份只带 `raw_text` 的空步计划跑一遍
`update_focus`——`extract_focus` 只会从原话扫出告警 / 驾驶员状态 / 解除 / 会话偏好，什么都没扫出时返回 `None`、
焦点原样不动；判据一个字不复制。`test_engine_input_facts.py` +4（技术失败轮解除清掉 / 「没听清」轮告警登记上 /
澄清轮解除清掉 / 普通「没听清」轮焦点不动），摘掉两处调用 3 红；cloud 1345 passed。

**随 `ca4bf370` 发布并真栈复验**（用户「授权推送部署」：push `b342e3bb..ca4bf370` → dry-run 零阻断 → apply 196s →
status ok、5/5、running = `ca4bf370` → verify verified `20260919T054445Z-ca4bf37.json`）。带 `dest_choice` 挂起的
六轮序列 ×4：解除陈述 **0/4** 被吞（chitchat ×3 + **技术失败终态 ×1**）；**第 4 趟正是 `b342e3bb` 上红的那个形态
——解除句落到 planner 技术失败出口——这次 T6「现在还能继续开吗」答的是天气建议，没有「未解除」**（trace 见
`live_t47_probe_result_hazard_ca4bf370.json`，rep 4）。T6 四趟零「未解除」。T5 的落点仍是模型方差（`charging.plan`
出路线 / 出候选卡 / `system.clarify` 再问站点各占其一，零动作；目的地本来没答，再问是对的）。

---

## 4. 活项 3：MiniMax TTS 长文本 / RPM——已由 09-06 三层修法闭合，按证据销账

交接页写的是「原 887 字样本命中 `rate limit exceeded (RPM)`；原长文本、并发配额和盲听仍需独立复验」。
2026-09-06 语音卡顿计划 §6.3–§6.5 已经把这三样都做了（生产 release `a09c73a`，status 5/5、verify verified）：

| 交接页要求 | 证据（绑 SHA） |
|---|---|
| 原长文本复验 | 网关 `a05cb5f`：1218 字 26 句、81.8s 音频完整、无 error；`cf7091c`：931 字 22 句、204.6s 音频完整、`sim_underruns=0`、总空白 0；`a09c73a`：931 字整段一次到达 **2 请求**（修前 79 请求、4 次 ×60s 等待、4 个 ~20s 空白）、首片 0.63s、204.6s 音频 8.4s 送完 |
| 并发配额 | `_shared_rpm_bucket(api_key, rpm)` 进程级按账号共享滑窗（MiniMax 的 RPM 是账号级，之前每条流一个桶 ⇒ 前后脚两段回答互不知情、第二段撞 1002）；单测「两条流共用配额、第二条等窗口而非撞 1002」 |
| 盲听 | 泓舟人耳：OPPO 对话页重放 ✅（§6.4，无卡顿无嗡嗡播完整）；Xiaomi 对话页「介绍广州的历史，详细一点」176.2s 音频、underruns 0、gaps []，「这次是真修好了」（§6.5，修前同页同问 4 个 17–26s 空白） |

机制上三层：句末分段 + 60s 滑窗限速 + 1002 重连续传（`481f7d4`）→ 客户端音频余量驱动合并
（`60a72a2`/`cf7091c`）→ 首片前只攒不发 + 预算见底合并 + 账号级共享桶（`a09c73a`）。

**仍开、不并进本条**：混合意图轮（本地回执 + 云端总结两段）的盲听没做；长会话探针的 TTS 采样车道
（`audit_minimax_tts_sample`）自 `e9fa602` 后没在新 release 上重跑。前者是独立形态，后者跟着下次长会话跑批。

---

## 5. 活项 4：barge-in 在途残帧——裁决

### 5.1 现场

`e9fa602` 长会话的 TTS 打断审计：cancel 后仍收到 6144 / 8192 字节，但分别在 16 / 31ms 内连接关闭。
探针判据 `post_cancel_audio_bytes != 0 ⇒ 红`。交接页留的两问：客户端该不该丢弃 cancel 后的帧；
服务端判据要不要零字节。

### 5.2 裁决

- **客户端必须丢弃，且已经在丢弃。** HMI `audio.ts::StreamingTtsSession.stop()` 与 mobile
  `tts.ts::TtsSession.stop()` 都先置 `disposed`、发 cancel、关 socket、停播放源；`onMessage` 首行
  `if (this.disposed) return`。CDP C14 在真栈上证明 provider cancel + 本地 `AudioBufferSource.stop`
  同时发生。mobile 补一条用例钉住「cancel 之后到达的 6144 / 8192 字节不进播放器、不改收尾」
  （`mobile/test/voiceTts.test.ts`）；变异（摘 `disposed` 守卫 + stop 后不置空 player）只红这一条。
- **服务端判据不能要求零字节。** 全双工 WebSocket 上客户端的 cancel 帧与网关已经 `send_bytes`
  出去的那一片在线上交错，没有确认往返就没有任何一方能保证零；`http_server.py` 收到 cancel 即
  `break` → `finally` 取消 provider task，能停的已经停了。要守的是「**及时停**」：残帧只能是在途的
  那一两片，之后不能再来。判据改成「最后一片残帧到达时刻 ≤ 在途窗口（1s）」+ 原有「≤5s 关闭」。
  窗口取 1s：一条没被取消的流每秒持续吐几十片，与「一两片在途」在这条尺子上分得开；实测 16 / 31ms
  留了 30× 余量。有残帧却没记时刻（旧探针形态）按红——「没量」不等于「在窗口内」。

### 5.3 改动与验证

`scripts/probe_qa_long_sessions.py`：`_BARGE_IN_FLIGHT_MS = 1000`；审计结果新增 `post_cancel_frames`
/ `post_cancel_last_frame_ms`；`validate_tts_barge_in` 按窗口判。`scripts/tests/test_probe_qa_long_sessions.py`
85 passed（改 1：在途 31ms 不红、窗口后仍来红、未计时红、旧的三连红原样）。mobile `voiceTts.test.ts`
22 passed。

---

## 6. 活项 5：全量 warning

`9a3b6f2f` 时全量 13 warnings、4 类。本轮先定位再分类：

| 类 | 数 | 出处 | 处置 |
|---|---|---|---|
| gRPC `coroutine 'UnaryUnaryCall._invoke' was never awaited` | 1 | `agents/trip_planner/tests/test_agent.py::test_modify_rainy_days_swapped_indoor`：真 `LLMClient` 在三次 `asyncio.run` 间共用一个 agent，第二趟两次 UNAVAILABLE（经系统代理 `127.0.0.1:10808` 各等一轮连接超时，单测 ~9s）后 channel 留在已关闭 loop 上，第三趟在死 loop 上建 call ⇒ 永不 await。不是产品缺陷（生产每个 Agent 进程一个长命 loop） | **修 fixture**：显式给「不可达」LLM 替身——同一条 `_fallback_skeleton` 兜底路径、零网络、零跨 loop 复用。trip_planner 99 passed，`-W error::RuntimeWarning` 下零告警，耗时 9.86s → 0.73s |
| `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2` | ×N（按 worker 数重复） | `fastapi/testclient.py:1`，第三方 | 留着。消除它要换测试依赖（`httpx2`）——装依赖是红线，另立授权；不用 `filterwarnings` 把它藏掉 |
| `audioop` DeprecationWarning | 1 | `test/e2e_voice_loop.py` 用标准库 `audioop`（3.13 移除） | 留着记账：e2e 工具，3.12 仍可用；升 3.13 前要换 `numpy` 重采样 |
| re `FutureWarning: Possible nested set` | 1 | **我们自己的**：`scripts/tests/test_e2e_target.py:449` 用 `[[]` / `[]]` 写字面方括号 | **修**：改转义写法 `os\.environ\["…"\]`，匹配面逐字相同；`-W error::FutureWarning` 下 26 passed |
| WordPiece Deprecation | 2 | `transformers/tokenization_bert.py`，第三方 | 留着记账 |

处置后本机全量应为 **11 warnings**（Starlette ×8 + WordPiece ×2 + audioop ×1）；确切条目以 §7 为准。

---

## 7. 全量读数（本机 `TZ=UTC0`）与两条顺带发现

本机当天 **commit 被一个 WindowsTerminal 进程占掉 33.5GB**（09-18 同款，用户的终端进程、不动），
commit 余量 3–4GB、物理余量 2–3GB。四趟全量里三趟死在这上面，只有一趟跑完：

| 趟 | 口径 | 结果 | 定性 |
|---|---|---|---|
| ① 改前基线 | `-n 8`（多加了 `-W default`） | gw2 在 `scripts/tests/test_run_e2e.py::test_canonical_run_injects_metadata…` 崩 ⇒ xdist INTERNALERROR，只跑到 6715 | worker 被 MemoryError 打死；该文件串行 111 passed |
| ② 改后 | `-n 8` 固定口径 | **9 failed / 8370 passed / 32 skipped / 12 warnings**（562.95s） | 9 红 = 2 真 + 7 环境，见下 |
| ③ 改后（含两条顺带修） | `-n 8` 固定口径 | gw5 崩（`bash: fork: Resource temporarily unavailable`、pytest 自己格式化 traceback 时 MemoryError） | 主机 commit 耗尽 |
| ④ 改后 | `-n 4` | `no tests ran` | **我自己的错**：PowerShell 会话 cwd 还停在 `mobile/`（跑 tsc 时 `Set-Location` 没退回），不是内存 |
| ⑤ 改后最终树 | `-n 6`，`Start-Process` 分离跑（Bash 工具 10 分钟上限会杀前台命令） | **4 failed / 8374 passed / 33 skipped / 10 warnings**（685.88s） | 4 红全在 `scripts/tests/test_cloud_release.py`：`git init` 退出码 3221225773（`STATUS_COMMITMENT_LIMIT`）、`OSError: [WinError 1455] 页面文件太小`——主机 commit；+1 skip = `test_e2e_stack_lease.py` 里 `mklink /J` 子进程因同一原因失败转 `pytest.skip`；10 warnings = Starlette ×6（按 worker 数）+ WordPiece ×2 + audioop ×1 + 一条 `PytestUnhandledThreadExceptionWarning`（`_readerthread`，同源）。**该两文件串行复跑 229 passed / 2 skipped（两条是 Windows 常规 skip）** |

合并读法：最终树上**没有一条代码引起的红**，但本机今天**给不出一趟完整绿的固定口径读数**——
等 WindowsTerminal 那 33.5GB commit 释放后再跑一趟 `-n 8` 补进 AGENTS.md。趟 ⑤ 的 8374 + 4 = 8378
与趟 ② 的 8370 + 9 = 8379 差的正是那 +1 skip，收集面一致。

**⑥ 用户重开终端释放 commit 后（余量 33.8GB），最终发布树 `82c8c7fa`（= `ca4bf370` + docs）固定口径 `-n 8`：
8397 passed / 1 failed / 31 skipped / 12 warnings，272.31s。** 那 1 红 `test_e2e_stack_lease.py::
test_parallel_owner_cleanup_failure_overrides_both_passes`（`calls_before_manual_recovery == 0`）：同文件串行
61 passed / 2 skipped，单独 `-n 8` 连跑三趟各红 2–3 条**不同**用例（`test_parallel_fast_second_finishes_before_slow_first`
等，`rc == 1`）——parallel-owner 那组用例把**真仓库根**传给 `runner.main(repo_root=…)`，于是各 worker 去抢同一把
仓库级身份 OS 锁（`e2e_stack_lease.identity_lock_path`），谁没抢到就 `identity_busy`。这正是 AGENTS §6.1 点名的
「OS lock 污染读数」形态，是那组测试的隔离债（该用 `tmp_path` 当 repo_root 或 patch 锁路径），与本轮改动无关、未修。
31 skipped 比 32 基线少 1：AGENTS §6.1 的 32 是「本地 Docker 停」口径，此刻 Docker Desktop 在跑。12 warnings：
Starlette ×8、WordPiece ×2、audioop ×1、一条 stack-lease 子进程读线程 `UnicodeDecodeError`（GBK 字节）告警。

趟 ② 的 9 红逐条：

- `scripts/tests/test_cloud_deploy_assets.py::test_release_status_docs_record_deployed_non_green_checkpoint`
  ——**自 09-18 `0021ff86` 起就红**：那次 AGENTS.md §4.0 改写把 guard 钉的字面「5/5 endpoint healthy」
  写没了（`git show 1dc8b79d:AGENTS.md` 有、`0021ff86` 起无），而 09-18 三批都「全量本日未跑」，没人看见。
  处置：字面补回 status 行（事实没变，status ok 就是 5/5）。同时这条 guard 也钉着交接页的
  `rate limit exceeded (RPM)` 字面——§4 那行改写时保留为历史证据。
- `orchestrator/cloud/tests/test_catalog_budget.py::test_request_ref_mapping_holds_the_real_live_inventory`
  ——**本批引入、预期内**：描述加长 +96 字符，13759 → 13855，余量 2241 → 2145；按该测试的记账惯例补一行。
- 其余 7 条全在 `scripts/tests/test_run_e2e.py` / `test_e2e_canonical.py`，`MemoryError` 于
  `e2e_contract._canonical_bytes` 的 `handle.read(MAX_CANONICAL_INPUT_BYTES + 1)`：CPython 按请求长度
  预分配，**每个文件的摘要都先要 64MiB commit**，8 个 worker 一起要就炸；串行 159 passed。
  处置：改 `read(size_before + 1)`——守卫语义不变（长了读到 size+1 与 size_after 不等、短了读少同样不等），
  只是不再为每个小文件预分配 64MiB。这是被主机 commit 耗尽逼出来的一处真实低效，不是把环境问题当代码问题。

12 warnings（比 `9a3b6f2f` 时少 1 = 那条 gRPC）：Starlette `httpx2` ×8（按 worker 数重复）、
WordPiece ×2、`audioop` ×1、`scripts/tests/test_e2e_target.py:449` regex `Possible nested set` ×1。

---

## 8. 文档同步

- QA 交接页 §1 / §2 / §5：五条活项逐条改状态；09-11 那条「仅记录未修」对账为 09-14 已修。
- `AGENTS.md` §4.1 活项表同步；§4.0 发布快照在 deploy 之后再改。
- `docs/agents-history.md` 追加本轮。
