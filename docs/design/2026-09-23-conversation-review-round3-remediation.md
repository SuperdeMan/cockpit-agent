# 对话评审第三轮：逐条重证与分批落地

- 状态：**已落地**（2026-09-23，用户授权提交 / 推送 / 部署 / 真栈验证）。批 A（R3-01）`416e47bc` → 批 B（R3-02 + R3-05）`926d4c17` →
  批 C（R3-03 + R3-06）`5c729fbc` → 批 D（R3-04）`c99a9a74` → 真栈逼出的 reminder 拆步修正（第一版 `a2d03dde` 随 `c99a9a74` 发布、第二版 `1255f7b2`）；
  当前生产 `1255f7b2`。顺序同评审「建议实施顺序」，各批的落地记录接在方案后面；收口读数见 §5.1.1，顺带发现（仅记录）见 §7
- 交付对象：云侧编排（`orchestrator/cloud`）、`runtime/`、端侧一处 meta 卫生（`orchestrator/edge/server.py`）、探针；HMI / Android 零改动
- 关联：评审原文 [`docs/reviews/2026-09-23-cockpit_conversation_review_round_3.md`](../reviews/2026-09-23-cockpit_conversation_review_round_3.md)
  （冻结 `7e41fcf2` = 当时 main）；上一轮 [`2026-09-22-conversation-review-round2-remediation.md`](2026-09-22-conversation-review-round2-remediation.md)；
  接手 `AGENTS.md` §4

## 0. 结论

评审冻结的 `7e41fcf2` 就是当前 `main`，六组缺口的代码事实**逐条成立**（§1 是本地复算读数，不是转述），R3-01 B 还多核出一个更糟的形态：
**对象不同也授权**——挂着「打开后备箱」时「确认打开车窗」靠二元片段「打开」命中唯一候选，同样注入 confirmed。

裁决与评审一致：不推翻 DAG / Skill / VAL，不开 `goals/covers`，不扩 Agent。每条先写复现原问题的失败测试，判据只留一份；
每批验收都含「原正常对照 + 新反例 + 组合 / 故障路径」，变异各自判红。

**落地结果**（2026-09-23 下午）：六条全部修掉、各自发布并真栈复验（§2.1.1 / §3.1.1 / §4.1.1 / §5.1.1）。R3-05 的三条新出口与 R3-03 的再规划分支
在云端没有稳定触发场景（Redis 正常 / 这组句子多数走 E 路径），证据是离线用例 + 变异。真栈额外逼出一条 reminder 拆步缺陷（§4.2），两版修完，
收紧后的 RS27 判据下 3/3。R3-04 要的时延读数：24 个流式谈话轮的闸首次放行等待 p50 ≈ 0.25 s、p90 0.6 s、最大 0.83 s（§5.1.1）。

## 1. 逐条重证（对 HEAD `7e41fcf2`，本地复算 2026-09-23）

复算脚本在 scratchpad（不入库），读数如下：

| 评审 | 本地读数 | 结论 |
|---|---|---|
| R3-01 A 语气词单独授权 | 唯一一条 `wait_confirm` 下，「啊 / 唉 / 请 / 那 / 。/ 的 / 了 / 哈 / 就 / 一下」`_bare_affirmation` 全 True、`_resolve_spoken_confirm` 全 `kind=one`（⇒ `_restore(inject_confirmed=True)`）；「行程 / 确认函 / 可以改」False（上一轮修好的仍好） | 成立 |
| R3-01 B 二元片段点名 | 挂着「打开后备箱」：「确认关闭后备箱」「确认锁上后备箱」`kind=one`；**「确认打开车窗」也 `kind=one`**（「打开」）。端侧对这几句判 `trunk.close` / `door_lock.close`，而后备箱 / 门锁都 `require_confirm` ⇒ 必然上云，真栈同样可达 | 成立，且对象不同也中 |
| R3-02 SET + DELETE 同句 | `constraints_in("今天想吃辣，排不排队都行")` = `{no_spicy: False, no_queue: None}`；`extract_focus` 先 `merge_constraints({}, …)` ⇒ `{no_spicy: False}`，与旧 `{no_spicy: T, no_queue: T}` 合并得 `{no_spicy: False, no_queue: True}`——排队限制复活；致谢话术因此会念出用户刚撤掉的「不想排队」 | 成立 |
| R3-03 二字交集误删 | 拒绝 `{title: 深圳下雨就通知我}` 之后，`reminder.create {title: 深圳客户会议, time_text: 明天八点}` 被 `_retries_refused_goal` 判为再试（「深圳」）；对照 `reminder.list {scope: 明天}` 保留 | 成立 |
| R3-04 长度释放拆标记 | 160 字无标点前缀 + 「已」为第一包、「为您关闭车窗。」为第二包：闸放行（`removed=0`，合并文本含声称）；同一文本一次喂入被拦（`removed=1`）；final 按句剥又删一句 | 成立 |
| R3-05 删除失败仍宣布关闭 | `session.clear` 替身返回 False，`_close_pending` 后 `closed_operation_ids=['op-1']`；`_settle_session` / `_suspend` / `_suspend_clarify` 同样不看返回值；`_suspend` 先删旧再存新，存失败两头落空；`_suspend_clarify` 仍把任何保存失败说成「正在清除你的数据」（R8 只修了 `_suspend`） | 成立 + 一处同族 |
| R3-06 T2 步骤 ID 撞名 | `done={"r1": 旧天气结果}` 下新批 `[r1 reminder.create]` 派发 0 次；只改 ID 为 `r2` 派发 1 次。`extract_focus` 与 `_resolve_slot_refs` 同样按裸 ID 取结果 ⇒ 撞名时串结果 | 成立 |

## 2. 批 A：R3-01 授权两个必要条件（明确肯定 + 目标兼容）

| 子项 | 本批做什么 | 刻意不做 |
|---|---|---|
| A 明确肯定 | `_bare_affirmation` 改成**必须至少消费一次肯定词**：语气面（`_CONFIRM_PARTICLE_RE`）只能修饰，剥空而一个肯定词都没吃到 ⇒ False。「嗯」本身在 `_YES_WORDS` 里（产品口径：「嗯」是肯定），从语气面里拿掉，否则它会先被当语气剥掉、「嗯」单说反而不算。`_is_bare_confirm_word` / 挂起读不到那条出口同源受益（「啊」不再被拦成「没有待确认」） | 不改 `_YES_WORDS`（「就这家 / 就它」在语气面优先剥「就」之后本来就到不了，维持现状——修它等于放宽授权面）；不改按钮确认 |
| B 目标兼容 | 点名确认拆成两段：**召回**（`_pending_names`，二元片段，goal / 原话 / 确认摘要都算——只负责找候选）与**裁决**（`_named_confirm_compatible`：点名余量去掉零领域虚词后，每个非数字字都要落在 ≥2 字、且是**已校验步骤事实**子串的片段里；每个数字串都要作为完整数字出现在事实里；端侧对整句的解析（`edge_nlu`）若点出**另一个 intent**，一票否决）。**事实只取服务端持有的已校验步骤摘要**：挂起那一刻 `contracts.action_summary`（Registry 能力描述 + 槽值——确认卡上给用户看的同一句），新增 `SessionState.action_summary` 落盘；旧记录缺它时现取一次，取不到就判不兼容。结果：恰一条兼容 ⇒ 确认它；≥2 条兼容 ⇒ 问哪一条（同 `pending_ambiguous`）；召回到但一条都不兼容 ⇒ 新出口 `system.pending_mismatch`（念出等确认的是什么、说「确认」执行 / 说「取消」作废；零动作、零关闭）；召回不到 ⇒ 照旧按插话 | **不用 LLM goal、也不用任务原话做裁决面**：原话里可能有与这一步相反的词（「打开后备箱，别关车窗」时「确认关车窗」会被原话覆盖）；不把二字阈值改成三字（「后备箱」照样共享）；`edge_nlu` 只做否决、从不授权 |
| B′ 取消点名 | 同一召回函数服务点名取消：≥2 条挂起时按裁决面上的**覆盖度排序**，唯一最高者绑定，并列就问（「取消刚才拿铁咖啡」在拿铁 / 美式并存时不再白问一次）；只召回到一条仍绑它 | 取消方向照旧 fail-safe：单条挂起时「取消刚才 X」仍清它（二轮批 A 的裁决不变） |
| 顺手（端侧） | `server.py` 与 `_edge_executed` 那三个键同处 pop 掉客户端带来的 `_edge_nlu`——它从此是端侧自己盖的章，云侧才能拿它当否决信号 | 不改 `edge_nlu` 「不进 prompt」的架构约束（`test_edge_nlu_divergence` 照旧钉着） |

代价写明：点名确认的措辞与确认卡不同（「确认解锁」对卡片「打开车门锁（全车）」）现在会得到一句确定性的「我这边等您确认的是…」而不是直接执行
——精确优先于召回；裸「确认 / 好的，确认吧 / 嗯可以」与按钮确认一字不变。

探针 RS25（`--cases RS25 --repeat 3`）：「打开后备箱」→「啊」零动作、挂起仍在 →「确认关闭后备箱」零 `trunk.open`、听到等确认的是打开后备箱 →
「确认」才执行并关掉第 1 轮那条；sid 1「把全车门解锁」→「确认锁车门」零 `door_lock.open` →「好的，确认吧」执行。

### 批 A 验收

- 先红测试：语气词单独十句零确认注入、挂起仍在；相反动作 / 不同对象 / 不同位置 / 不同金额零注入；原正常对照（「确认 / 好的，确认吧 / 嗯可以 / 嗯 /
  确认明晚8点那个」）照常；取消点名排序；`edge_nlu` 否决；旧记录无摘要时现取、取不到判不兼容。
- 变异：肯定词计数去掉 / 「嗯」放回语气面 / 裁决退回召回 / 数字不按整串 / 否决去掉 / 取消排序去掉，各自判红。
- 定向 + 四门禁 + smoke_edge + 全量固定口径；真栈 push → dry-run → apply → status / verify → RS25 ×3。

### 2.1 落地记录（2026-09-23）

| 条 | 做了什么 | 本地证据 |
|---|---|---|
| A | `engine.py`：`_bare_affirmation` 记「有没有吃到肯定词」，剥空而没吃到 ⇒ False；「嗯」移出 `_CONFIRM_PARTICLE_RE`（它是肯定词） | 新 `test_engine_confirm_authority`：语气词 14 句（静态三函数 + 解析 `kind=""`）、其中 6 句走引擎零 confirmed 注入且挂起仍在；13 句正常对照（「确认 / 好的，确认吧 / 嗯可以 / 嗯 / 嗯嗯 / 请确认 / 哎，好的…」）照常执行；无挂起时「啊」交规划、不被拦成「没有待确认」 |
| B | `engine.py`：`_naming_core` / `_naming_coverage`（虚词表、数字整串、≥2 字片段）、`_pending_names` 召回加摘要、`_named_confirm_compatible` 裁决、`_resolve_spoken_confirm(edge_intent=)` 出 `mismatch` / 兼容多条出 `ambiguous`、`_ensure_confirm_facts`（旧记录现取）、`_suspend` 把确认卡那句摘要落进挂起态（与 `confirm_policy` 共用一次 Registry 往返）、`system.pending_mismatch` 出口；`models.SessionState.action_summary`；`runtime/outcome.py` `pending_mismatch`；`server.py` 入口 pop 客户端的 `_edge_nlu` | 相反动作 / 不同对象 5 句、同名 5 句、位置与金额 6 条（「左后 vs 右后」「50 / 300 vs 30」「三十元 = 30元」）、单字不作数 3 条（「锁车门」对「打开车门锁（全车）」）、goal / 原话只进召回 1、无摘要不授权 1、端侧否决 3、两条兼容 ⇒ 问 / 点名选中 2、召回不到 ⇒ 插话 1；引擎端到端：三句矛盾点名零注入 + 零 LLM + 挂起仍在 + 随后裸「确认」照常、同名点名执行、摘要落盘、旧记录现取 / Registry 挂了判不兼容、否决、裸确认不受否决 |
| B′ | `engine.py`：`_best_named`，取消分支 ≥2 条召回时按覆盖度取唯一最高 | 静态（拿铁 / 美式：「拿铁咖啡」绑拿铁、「那杯咖啡」并列）+ 引擎「取消刚才拿铁咖啡」清拿铁、美式留着 |
| 既有测试改写（留痕） | `test_spoken_confirm_can_name_one_of_two_pendings`：本文件替身计划对两句都写死 `今晚7点`、能力描述是机器名 ⇒ 摘要为空，按新规则点名不能授权——那正是本批要挡的形态；改成把两条挂起的摘要写成真实挂起会有的样子，验的仍是「点名选中那一条」。`test_pair_quoting_confirm_vs_authorising` 的挂起补上 `action_summary` | 两条均绿 |
| 探针 | RS25（语气词 / 点名矛盾 / 门锁同形，第 4 轮寻址确认证明前两句没消费挂起）；`test_the_round_three_counterexample_set_is_fixed` 钉住第三轮反例集（随批追加）；长会话探针的引擎节点表加 `cloud.pending_mismatch` | `--list` 通过 |

定向读数：新文件 62 passed；cloud + runtime + 对比对 2480 passed / 1 skipped；edge + scripts 2402 passed / 11 skipped；四门禁 + smoke_edge 13/13；
**八处变异各判红**（肯定词计数去掉红 21、「嗯」放回语气面红 2、裁决退回召回红 14、数字不按整串红 1、否决去掉红 2、取消排序去掉红 1、
摘要不落盘红 1、单字当片段红 1——最后这条第一趟是绿的：没有一条测试盯着「锁车门 ⊂ 打开车门锁」这种反方向，补了测试才判红）。
**全量固定口径（批 A 工作树，`TZ=UTC0` `-n 6`）：9098 passed / 0 failed / 32 skipped / 10 warnings，314 s。**（其后只改了一处 docstring 示例，
相关 166 条复跑绿。）

#### 2.1.1 发布链与真栈读数（2026-09-23 12:5x–13:1x，MiniMax-M3）

| release | 发布链 | 真栈 |
|---|---|---|
| `416e47bc` | push `7e41fcf2..416e47bc`（恰一条）→ dry-run 零阻断（基线 `86c43998`）——可用磁盘 27.5 GiB，低于远端构建的 30 GiB 闸 ⇒ 只读盘点后请用户批准清理（批准「只删 104 份旧构建记录、保留最近 6 份 release 的」）；执行前复核发现同机另一项目刚释放约 29 GB 容器层、可用 58.2 GB，**没有删任何东西**（批准留作本轮后续发布再被挡时用）→ apply `submitted` → status ok 5/5 零 warning、`release_sha` = `running_release_sha` → verify `verified`（`20260923T051015Z-416e47b.json`，minimax / MiniMax-M3，lock e2e） | `--cases RS25 --repeat 3`（artifact `.artifacts/probe-round3-batchA-416e47bc.json`）：**3/3 [det]** |

逐条读（自动 PASS 之外看话术与动作）：

- T2「啊」三趟零动作，交规划后落「这次我没能把您的请求拆成可以执行的步骤…」（planner 对无意义输入给不出步）——修前按代码它会把后备箱打开。
- T3「确认关闭后备箱」三趟逐字同款「我这边等您确认的是「打开后备箱」；您说的「关闭后备箱」我没法确定就是它，所以这次没有执行。…」，零动作、零关闭；
  端侧把这句判成 `trunk.close`（`require_confirm` ⇒ 上云），云侧点名召回到了打开后备箱那条、裁决不兼容。
- T4 寻址确认三趟 `trunk.open` 并关掉 T1 那条——前两句都没消费挂起。
- sid 1：T6「确认锁车门」三趟同款出口、零 `door_lock.open`；T7「好的，确认吧」三趟 `door_lock.open`（二轮 R1 的正面对照不回退）。

## 3. 批 B：R3-02 补丁 / 快照分开 + R3-05 删除三态与提交语义

| 条 | 本批做什么 | 刻意不做 |
|---|---|---|
| R3-02 | `extract_focus` 里 `session_constraints` 一律存**本轮补丁**（`constraints_in` 原样，带 `None` 墓碑，含 `others`），不再提前 `merge_constraints({}, …)`；`update_focus` 是唯一把补丁合进旧快照、再归一成「说过且仍有效」的地方（旧逻辑本来就在那里归一）。owner 投影 / 回存不动 | 不加词表、不为这一句写特例；不改 `constraints_in` 的抽取 |
| R3-05 删除三态 | `SessionStore.clear_result` → `deleted / already_absent / unavailable / privacy_fenced`（`clear()` 保留布尔形状委托它）；`_write` 抛错不再冒到编排层 | — |
| R3-05 可靠墓碑 | 删不掉（`unavailable`）时在 store 里记**进程内墓碑**（operation_id → 那条挂起的截止时刻）：`load_all_result` 过滤掉墓碑条，下一次整表重写（`save_pending_result` / `clear_result`）自然不再写回它——删不掉的那条再也不会被确认 / 续接复活，后端恢复后的第一次整表写就把它真正删掉（读路径不顺手写，避免读出口带副作用） | 不跨进程：`cloud-planner` 单副本，进程重启丢墓碑（且此刻后端刚好恢复、挂起未过期、用户又说「确认」）是记账的残余风险 |
| R3-05 回执 | `_close_pending` / `_settle_session` 返回删除结果；只有 `deleted / already_absent / privacy_fenced / 已立墓碑` 才进 `closed_operation_ids`；取消出口按能证明的状态说：删掉了 ⇒「已为您取消」，只立了墓碑 ⇒「好的，X 不会执行了」（不说「已取消」、不让用户猜要不要再说一次）；新 outcome `cancel_unconfirmed` | 墓碑都立不起来的形态不存在（它是进程内状态） |
| R3-05 提交语义 | `_suspend` / `_suspend_clarify` 的「关旧开新」改成**一次整表写**：`save_pending_result(…, replaces=旧 op)` 在同一次写里去掉旧条、加新条；写失败 ⇒ 旧条（本轮已消费）立墓碑、`closed_operation_ids` 点名它，新条没存 ⇒ 按 R8 话术说存不下；`_suspend_clarify` 也改用三态（修掉「正在清除你的数据」那句误报） | — |

探针 RS26：「我不吃辣，也不想排队」→「今天想吃辣，排不排队都行」（致谢不许念「不想排队」）→「我今天说过不吃辣吗」（读出口只剩「想吃辣」）。
R3-05 的三条新出口在云端没有触发场景（Redis 正常），证据是离线用例 + 变异（同二轮 R8 的边界）。

### 批 B 验收

- R3-02：同一句 SET + DELETE、两个维度互换、仅 DELETE、仅 SET、未提及保持、`others` 子对象 SET + DELETE、owner 隔离，各一组。
- R3-05：初次读成功、删除时失败（取消 / 收口 / 澄清选中 / 过期四条出口）；删除成功但回执丢失（写已生效、调用抛错）；重挂起保存失败（旧条不复活、新条不假存）；
  成功取消后再次确认不能复活（含墓碑那一支）；后端恢复后墓碑条被真正删掉。
- 变异：提前归一放回 / 墓碑不过滤 / 回执不看结果 / 关旧开新拆回两步，各自判红。

### 3.1 落地记录（2026-09-23）

| 条 | 做了什么 | 本地证据 |
|---|---|---|
| R3-02 | `context.py::extract_focus`：`session_constraints` 存本轮补丁（`constraints_in` 原样，带 `None` 墓碑、含 `others`）；`update_focus` 是唯一「合进旧快照 → 归一」的地方（本来就在那里归一，只是前面先归一过一次） | 新 `test_constraint_patch` 10：补丁带墓碑、同句 SET + DELETE、两维互换、仅 DELETE、仅 SET、未提及保持、`others` 子对象 SET + DELETE、owner 隔离、首轮纯撤销不落键；引擎级：第二句致谢不再念「不想排队」、回问也不念 |
| R3-05 删除三态 + 墓碑 | `session.py`：`CLEAR_*` 四态 + `clear_result`（`clear()` 委托、布尔形状不变）；`_close_locally` / `_closed_locally` / `_forget_closed`；`load_all_result` 两条路径都过滤墓碑条；`_write` 抛错不再冒到编排层 | 新 `test_pending_clear_states` 14 条里的存储层 6 条：三态、布尔形状、删不掉 ⇒ 墓碑读不出、恢复后下一次整表写真的删掉、写已生效回执丢失 ⇒ 报 unavailable 但读不回来、`replaces` 一次写 |
| R3-05 回执 | `engine.py`：`_close_pending` 返回删除结果，四态都进 `closed_operation_ids`（都不会再执行）；取消出口删不掉 ⇒「好的，X 不会执行了。」+ 新 outcome `cancel_unconfirmed`（`runtime/outcome.py`）；`_settle_session` 同用 `clear_result` | 引擎 4 条：取消时删不掉不说「已为您取消」、后端恢复后再确认不复活；回执丢失的取消不复活；确认执行后删不掉不会被再确认一次；成功取消后确认不复活 |
| R3-05 提交语义 | `_suspend` / `_suspend_clarify`：「关旧开新」= `save_pending_result(replaces=)` 一次整表写，写失败旧条立墓碑；`_suspend_clarify` 改三态（此前任何保存失败都说「正在清除你的数据」——二轮 R8 只修了 `_suspend`） | 引擎 4 条：重挂起写失败 ⇒ 旧条留在存储里但读不出、新条没假存、话术说存不下；关旧开新写次数 = 1；澄清存不下不说隐私清除；澄清选中后删不掉不能再选一次 |
| 探针 | RS26（同句 SET + DELETE 的致谢与回问）；第三轮反例集 += RS26 | `--list` 通过 |

定向读数：新增 24 条；cloud + runtime + 对比对 + 探针 2557 passed / 1 skipped；四门禁 + smoke_edge 13/13；**六处变异各判红**
（提前归一放回红 6、墓碑不过滤红 6、取消回执不看删除结果红 1、删不掉不立墓碑红 4、关旧开新拆回两步红 1、替换失败不立墓碑红 2）。
「关旧开新拆两步」这条在行为层与一次写**等价**（全失败时旧条都读不出、新条都没存），只有写次数分得开，所以钉的是「一次写」。
**全量固定口径（批 B 工作树，`TZ=UTC0` `-n 6`）：9122 passed / 0 failed / 32 skipped / 10 warnings，301 s。**

#### 3.1.1 发布链与真栈读数（2026-09-23 13:2x–13:3x，MiniMax-M3）

| release | 发布链 | 真栈 |
|---|---|---|
| `926d4c17` | push `072a7898..926d4c17`（恰一条）→ dry-run 零阻断（基线 `416e47bc`，可用 57.0 GB）→ apply `submitted` → status ok 5/5 零 warning、`release_sha` = `running_release_sha` → verify `verified`（`20260923T053248Z-926d4c1.json`） | `--cases RS26,RS7,RS24,RS16 --repeat 3`（artifact `.artifacts/probe-round3-batchB-926d4c17.json`）：**12/12 [det]** |

- **RS26 3/3**（R3-02 活体）：第二句「今天想吃辣，排不排队都行」三趟逐字「好的，排不排队都行；这次想吃辣，找地方的时候我按这个来。」——修前按代码会念成
  「…这次想吃辣、不想排队…」；回问「我今天说过不想排队吗」三趟「您这次说过：想吃辣。…」。
- RS7 3/3（约束改口后念改口后的）、RS24 3/3（乘员隔离：B 三趟都没听到「您这次说过」）、RS16 3/3（「取消」走新的删除回执，删得掉 ⇒「好的，已为您取消。」，
  再「确认」⇒「当前没有待确认的操作」）——守护面不回退。
- R3-05 的删不掉 / 回执丢失 / 重挂起写失败三条新出口在云端没有触发场景（Redis 正常），证据是离线 14 条 + 六处变异。
- 顺带看到（不在本批）：RS24 第 1 趟 B 的回问被规划成 `info.search`，答「未能从检索到的资料中确认…」——落域方差，判据只要求不念 A 的约束。

## 4. 批 C：R3-03 拒绝绑定到原话分句 + R3-06 运行时步骤身份

| 条 | 本批做什么 | 刻意不做 |
|---|---|---|
| R3-03 | 被拒诉求的身份 = **任务起点原话里它所在的分句**（`safety_origin_text`，先按句末标点、再按 `runtime.clause_split` 那一份分隔符拆；每个槽值取最长公共子串最大的分句，并列都算）。再规划的同域新步：它的槽值落点**全在**被拒分句里 ⇒ 再试，丢；有任何一个落点在别的分句 ⇒ 独立诉求，照做；没有槽值 / 一个都落不下 ⇒ 原话只有被拒那些分句时算再试，否则只有写能力（`effect=write`，如空槽 `reminder.cancel`）算再试、读能力（`reminder.list {}`）照做。拒绝步自己落不到任何分句时退回强证据：同 intent 同槽指纹，或整值包含。依赖被丢步的下游照旧传递 blocked | 不再拿任意两字交集当身份；单分句里并列两个诉求（无标点、无连接词）仍按同一分句处理——证据不够时宁可丢，记为边界 |
| R3-06 | 再规划批一进 `replan` 就把模型给的局部 ID 换成**运行时步骤 ID**（`t<批次>-<局部 ID>`，与本轮已知全部 ID——结果、种子、初计划——冲突时再加后缀），同批内的 `depends_on` / `slot_refs` / `${…}` 占位一并重写；之后「已完成步复用」「被拒诉求再试」两道筛都在运行时 ID 上做（它们改写的前序引用本来就指向运行时 ID，不会再被同名局部 ID 截胡）。loop 把本轮已知 ID 传给 `replan`；映射进 `t2.iter` span | 不动业务幂等：执行侧 `(intent, slots)` 指纹防重与 ID 无关，换 ID 绕不过写幂等；初计划 ID 不改（它就是第一批，种子按它存） |

探针 RS27：「深圳下雨就通知我，另外明天早上八点提醒我和深圳客户开代号{run}的会」——事件触发诚实拒绝，同一轮里定时提醒照建（卡片带代号）。
R3-06 在真栈上要靠模型恰好复用 ID，确定性证据是离线三批固定输出（执行器 / 流式 / 回退 / 挂起恢复 / slot_refs）。

### 批 C 验收

- R3-03：评审反例（共用「深圳」）照做、控制样本照做、批 7 ① 与二轮 R5 的再试形态仍拦（「堵车提醒」、空槽 `reminder.cancel`）、空槽读能力照做、单分句原话退回旧口径、
  下游 blocked 不删边。
- R3-06：三批固定 planner 输出重复 `r1/r2`：不漏步、不重复副作用、不串结果（`slot_refs` 读到本批的 r1）；单步流式 / unary 回退 / 挂起恢复后再规划 / 引用前批观察 ID 都对。
- 变异：落点退回二字交集 / 空槽一律再试 / 不换 ID / 换 ID 不重写引用，各自判红。

### 4.1 落地记录（2026-09-23）

| 条 | 做了什么 | 本地证据 |
|---|---|---|
| R3-03 | `planning.py`：`_origin_clauses`（句末标点 + `runtime.clause_split` 那一份分隔符）、`_longest_common_run` / `_grounding`（每个槽值取最长公共子串最大的分句）、`_RefusedGoal`（领域 / 实质 / 来源分句 / 指纹）；`_retries_refused_goal` 按「同指纹 ⇒ 来源分句包含 ⇒ 落不下时看原话还有没有别的分句、空槽写能力」的强弱顺序判；`_overlaps`（二字交集）删掉；`replan` 的原话取服务端 `safety_origin_text`，没有才退到目标 | 新 `test_refused_goal_identity` 10：分句与落点两条纯函数、评审反例（共用「深圳」）照做、控制样本照做、只落在被拒分句的再试照丢、空槽写能力照丢、空槽读能力照做（二轮 R5 记下的边界关掉）、单分句原话一律再试、被拒步落不下只认强证据、下游 blocked 不删边；二轮 R5 与批 7 ① 的既有 8 条原样绿 |
| R3-06 | `planning.py`：`assign_runtime_ids`（`t<批次>-<局部>`，与本轮已知 ID 撞了加后缀，同批 `depends_on` / `slot_refs` / 两种槽值占位一并改写）；`replan(taken_ids=, batch_tag=)` 在「已完成步复用」「被拒诉求再试」两道筛之前换 ID；`_rewrite_completed_ref` 补占位写法；`ReplanDecision.local_ids`；`loop.py` 传本轮已知 ID 与批次标签、对不认识标签的规划器只改撞名的兜底、`t2.iter` span 记 `local_ids` | 新 `test_loop_step_identity` 9（真 `PlanBuilder.replan` 三批固定输出 + 真 `DagExecutor`）：三批重复 r1 不漏步不串结果、同一副作用换 ID 仍只执行一次、引用前批观察的运行时 ID 照读、单步流式批与后续执行器批不撞、流式失败回退不撞、挂起后续跑不撞种子 ID、loop 兜底、一轮结果 ID 唯一、纯函数改写全部引用；四个固定签名的规划器替身补 `**_kwargs`（loop 会把 TypeError 咽成「Replan call failed」） |
| 探针 | RS27（评审反例原句：拒绝诚实、共用「深圳」的会议提醒照建、清理）；第三轮反例集 += RS27 | `--list` 通过 |

定向读数：新增 19；cloud + runtime + 对比对 + 探针 2576 passed / 1 skipped；四门禁 + smoke_edge 13/13；**七处变异各判红**
（落点取任意共享两字的分句红 4、空槽一律再试红 1、拒绝不绑原话红 3、replan 不换 ID 红 2、loop 无兜底红 1、换 ID 不改引用红 4、占位不改写红 1）。
**全量固定口径（批 C 工作树，`TZ=UTC0` `-n 6`）：9141 passed / 0 failed / 32 skipped / 10 warnings，295 s。**

#### 4.1.1 发布链与真栈读数（2026-09-23 13:4x–14:1x，MiniMax-M3）

| release | 发布链 | 真栈 |
|---|---|---|
| `5c729fbc` | push `926d4c17..5c729fbc`（批 B 文档 + 批 C 两条）→ dry-run 零阻断（基线 `926d4c17`）→ apply `submitted` → status ok 5/5 零 warning、`release_sha` = `running_release_sha` → verify `verified`（`20260923T055455Z-5c729fb.json`） | `--cases RS27,RS22,RS13,RS14 --repeat 3`（artifact `.artifacts/probe-round3-batchC-5c729fbc.json`）：**7/12**——逐条归层见下 |

- **RS14 2/3**：两趟都走 T2，再规划批里的那一步以运行时 ID 执行（trace `t2.iter local_ids: t1-r1<r1`，`step.verify step_id=t1-r1`），「取消导航」→「已结束到深圳湾公园的导航」；
  失败那趟 T1 在**初次规划**就落 `unresolved_object`（「我听到了…但没听清要拿它做什么」），没进 T2——与本批无关（批 8 读数同为 2/3，那次是「adaptive 却零步」）。
- **RS13 3/3**：路况 + 事件触发诚实拒绝，批 7 ① 守护面不回退。
- **RS22 1/3**：两趟红都在第二句的**初次规划**——一趟出澄清卡（「设置堵车提醒 / 列出明天提醒」二选一）、一趟 `unresolved_object`，trace 里没有任何步骤跑过；
  通过那趟 T2 仍是「先念明天那条 + 诚实拒绝」。规划层方差，不是本批改动的层。
- **RS27 1/3，逼出一条真缺陷（§4.2）**：planner 把原句拆对了两步（`{title: 深圳下雨就通知我, time_text: ""}` + `{title: 和深圳客户开代号X的会, time_text: 明天早上八点}`，
  `toolcall_salvage`、E 路径、没进 T2——R3-03 的再规划路径这句话走不到），库里却落了**两条一模一样**的 08:00「深圳下雨就通知我，提醒我和深圳客户开代号X的会」；
  第 3 趟规划直接出澄清卡。三趟留下的 3 条残留提醒已用探针同一身份按产品路径（列出 → 按序号取消 → 再列出为空）清掉。
- R3-03 的确定性证据仍是离线 10 条 + 三处变异；R3-06 在真栈上看得见（上面 RS14 那条 `local_ids`），执行正确性的证据是离线 9 条。

### 4.2 真栈逼出：拆步之后 reminder 仍按整句补标题、借时间（RS27，2026-09-23 下午）

| 现象 | 真因 | 修法 | 刻意不做 |
|---|---|---|---|
| 两步各建了一条同标题提醒，事件那一步没被拒 | `_create` 里 ① `_fuller_title(槽标题, 整句)`：「宁长勿短」拿**整句**的抽取结果补全，两步的短标题都是它的子串 ⇒ 都补成合并标题；② 事件那步 `time_text` 为空 ⇒ `parse_time_text(整句)` 借到另一个诉求的「明天早上八点」⇒ 建成定时提醒，走不到事件拒绝——二轮批 B「别的诉求的字不归这一步」只修到了拒绝判定 | `_title_scope`：planner 给了标题、标题恰好落在一个分句里（`runtime.clause_split` 那一份），**且别的分句自己就是一个完整诉求**（事件触发句，或自带时间 + 「提醒/通知…我」+ 抽得出事项）⇒ 这一步只认自己那半句：标题补全在这半句里做；这半句是事件触发、自己又没给时间 ⇒ 诚实拒绝（`_event_refusal`，唯一一份话术） | 不改时间回退本身（同一诉求铺在几个分句上时——「创建一条定时提醒，2分钟后提醒我，提醒内容是X」——时间本来就在别的分句）；不改没有槽标题的单步路径（二轮批 B 的边界用例一字不变） |

证据：`agents/reminder/tests/test_agent.py` +5（事件那步拒绝零落库、会议那步标题不串、两步合起来恰一条、C10-B「宁长勿短」与「内容是…」续写照旧）；
第一版判据把「2分钟后提醒我」也当成另一个诉求，既有用例 `test_create_explicit_title_excludes_request_wrapper` 当场红——改成「抽得出自己的事项」才算；
三处变异各判红（范围恒取整句红 3、拆步事件拒绝去掉红 2、任何「提醒我」都算另一个诉求红 1）。

**第二版（`1255f7b2`，`c99a9a74` 真栈逼出）**：`c99a9a74` 上 RS27 三趟里两趟事件请求仍被建成 08:00 提醒——planner 换了两种写法：
`{title: 深圳下雨, time_text: 深圳下雨就通知我}`（事件短语塞进时间槽，第一版「`time_text` 为空才拒」那条分支走不到）与
`{title: 深圳下雨通知我}`（比分句少一个「就」，逐字子串找不到它的分句 ⇒ 范围退回整句）。修法两处：
① 这一步的分句 = 与槽标题最长公共子串最大的那一句（唯一最大且 ≥ 2 字，并列就不收窄）；
② 原话已拆成几个诉求时，事件分句自己没给时间就拒绝，**不看 planner 往 `time_text` 里写了什么**——那个时间只可能属于另一个诉求，或者就是事件短语本身。
四种 planner 写法钉成参数化用例（含把另一个诉求的「明天早上八点」抄进这一步的时间槽）；五处变异各判红（范围恒取整句、拒绝去掉、任何「提醒我」都算
另一个诉求、分句按逐字子串找、拒绝让位给时间槽）。真栈见 §5.1.1：收紧判据后 RS27 ×3 = 3/3，第 2、3 趟正好是 `5c729fbc` / `c99a9a74` 上出错的两种写法。

## 5. 批 D：R3-04 流式闸切包不变 + 发布即权威

| 条 | 本批做什么 | 刻意不做 |
|---|---|---|
| 切包不变 | `ExecutionClaimGate` 重写为**只由文本决定**的释放：≤ 160 字的句子整句判（同今）；超长句（含终止符 > 160）进分段模式——只释放「任何可能在后面补全的声称都够不着」的前缀（保留最后 15 个非空白字：声称标记的最大非空白跨度，由测试从三条正则推出），声称一旦成立，丢弃**从它起点到所在分句结束**的那一段，其余照放。一次喂入与任意切包给出逐字相同的输出 | 不改声称判据本身（三条正则、零领域词）；不串联模型审稿 |
| 发布即权威 | `strip_execution_claims` 改成同一个闸一次喂入（判据只有一份实现）；D0 与 T2 流式出口在谈话步流过闸时，把**闸实际放行的文本**写回结果（全被拦 ⇒ 诚实话术），final / 落库从它来，不再对全文换一套分句重剥 | 不改非谈话步 |
| 时延 | 闸记录「第一个增量进门 → 第一次放行」的等待（`claim_gate_hold_ms`）进流式 span；真栈量一组谈话轮的分布，写进本节，不做「无体验损失」的承诺 | — |

探针：RS21 复跑（增量与 final 同时零声称）；时延读数来自 collector 的 span。

### 批 D 验收

- 性质测试：一组语料（含超长句、声称跨包、声称跨越释放边界、句内多声称、空白插入）× 随机切包 ≥ 500 次，输出逐字等于一次喂入、且不含任何声称句；
  `strip_execution_claims` 与闸一次喂入逐字相同；D0 / T2 的 final 与增量拼接一致。
- 变异：保留窗口改 0 / 超长句退回整段放行 / final 重新全文剥，各自判红。

### 5.1 落地记录（2026-09-23）

| 条 | 做了什么 | 本地证据 |
|---|---|---|
| 切包不变 | `runtime/execution_claim.py`：`ExecutionClaimGate` 重写——≤ 160 字的句子整句判（同二轮）；超长句进分段模式，只放「保留区」之前的前缀（`CLAIM_SPAN_MAX = 15` 个非空白字），声称成立就丢掉从它起点到所在分句结束的那一段（句末标点照放），其余照放；`released` 记用户实际收到的全部文本；`first_hold_ms` 记首次放行等待。三条正则合成 `_CLAIM_RE`（最左一处）；`strip_execution_claims` 改成同一个闸一次喂入；诚实话术 `CLAIM_STRIPPED_SPEECH` 只留一份 | 新 `test_claim_gate_invariance` 13：13 段语料 × 45 种随机切包 = 585 次，放出文本与删掉处数都与一次喂入逐字相同、且不含任何声称；评审反例（160 字 +「已」/「为您关闭车窗。」）；strip = 一次喂入；保留区从三条正则推导（`re._parser` 去掉 `\s*` 后求最大宽度）；超长干净句分段放；首次放行等待量得到；超长句里只丢声称那一分句（不残留「关闭车窗」） |
| 发布即权威 | `engine._stream_single_step` / `loop` T2 流式：闸拦过东西时，这一步的话术 = 闸实际放出去的那份（全拦 ⇒ 诚实话术），final / 落库不再对 Agent 全文另切一遍；`step.agent` span 加 `claim_gate_hold_ms` / `claim_gate_removed` | D0：评审切法不出客户端且 final = 流出；全拦 ⇒ 共用诚实话术；**Agent 的 final 全文与增量不一致时 final 仍取流出的那份**（这一条才分得开「发布即权威」与「切包不变」）；span 带等待；T2 同两条 |
| 既有测试改写（留痕） | `test_gate_releases_an_overlong_clean_sentence_without_waiting`：二轮版钉的「超长干净句整段放、flush 为空」正是评审点名的放行规则，改成「只留保留区、合起来逐字相同」 | 绿 |

定向读数：cloud + runtime + chitchat + scripts + 对比对 4096 passed / 12 skipped；四门禁 + smoke_edge 13/13；**六处变异各判红**
（无保留区（二轮规则）红 4、保留区窄于最宽声称红 1、超长句只丢标记不丢到分句结束红 1——第一趟是绿的，补了「不残留关闭车窗」才判红、D0 final 重剥全文红 1、
T2 final 重剥全文红 2、span 不带等待红 1）。
**全量固定口径（批 D + §4.2 工作树，`TZ=UTC0` `-n 6`）：9159 passed / 0 failed / 32 skipped / 10 warnings，312 s。**
§4.2 第二版工作树（`1255f7b2`）：**9162 passed / 0 failed / 32 skipped / 10 warnings，318 s**。

#### 5.1.1 发布链与真栈读数（2026-09-23 14:2x–15:2x，MiniMax-M3）

| release | 发布链 | 真栈 |
|---|---|---|
| `c99a9a74`（批 D，连同 §4.2 第一版 `a2d03dde`） | push `5c729fbc..c99a9a74` → dry-run 零阻断（基线 `5c729fbc`）→ apply `submitted` → status ok 5/5 零 warning、`release_sha` = `running_release_sha` → verify `verified`（`20260923T063207Z-c99a9a7.json`） | `--cases RS21,RS22,RS27 --repeat 3`（`.artifacts/probe-round3-batchD-c99a9a74.json`）：RS22 3/3、RS21 1/3、RS27 2/3（判据漏判，见下） |
| `1255f7b2`（§4.2 第二版） | push `c99a9a74..1255f7b2` → dry-run 零阻断（基线 `c99a9a74`）→ apply `submitted` → status ok 5/5 零 warning → verify `verified`（`20260923T064644Z-1255f7b.json`）；15:2x 复读 status ok 5/5、`running_release_sha` = `1255f7b2` | RS22 ×3 3/3 + RS27 ×3 0/3（`.artifacts/probe-round3-reminder-1255f7b2.json`，逐条归层见下）；探针判据收紧后 RS27 ×3 **3/3**（`.artifacts/probe-round3-rs27-strict-1255f7b2.json`） |

- **RS21 1/3（`c99a9a74`）**：三趟的执行性声称判据都没红。两趟红在第二句「从深圳欢乐海岸出发，不走高速」被 planner 规划成导航并执行（探针判据
  `actions_exclude: navigate`）：一趟起终点都是欢乐海岸、一趟目的地编成「天安门」全程 2390 km（通过那趟是追问「您想算到哪里的路程？」）——落域 / 槽位方差，与闸无关，列入 §7。零动作的第一、三句三趟都没有「已为您…」。
- **RS22 6/6**（两个 release 各 3/3）：「先念明天那条 + 诚实拒绝堵车订阅」；批 C 那次的 1/3（红在初次规划）这次没复现。
- **RS27 在 `c99a9a74` 上探针判 2/3，但判据漏了缺陷**：列表轮只查会议那条在不在，而两趟的列表卡里躺着「深圳下雨就通知我（明天 08:00）」
  「深圳下雨通知我（明天 08:00）」——planner 换了两种写法，第一版 `_title_scope` 没盖到（§4.2 第二版）。判据随之收紧：列表轮加 `card_text_not: ["下雨"]`，
  另钉一条离线用例证明它对这张卡判红（`test_rs27_listing_goes_red_when_the_event_half_was_stored_as_a_timed_reminder`，拿掉判据即红）。
- **RS27 在 `1255f7b2` 上第一组 0/3，三条红都不在 reminder 执行层**：① planner 只规划了会议那一步（没有事件步），会议提醒标题干净；清理句
  「取消代号X的会的提醒」planner 没给出 `reminder.cancel` ⇒ 取消闸落 `cancel_unresolved`；② planner 把「深圳下雨就通知我」改写成
  「先查深圳明天是否下雨，若下雨则通知」，T2 三批再规划各建一条（`t1-r1<r1` / `t2-r2<r2` / `t3-t1_r3<t1-r3`）：会议那条丢了代号（「与深圳客户开会」），
  另外建了用户没说过的「带伞」「明天有雨出门带伞」；③ 初次规划 `unresolved_object`。三趟留下的 3 条提醒已按产品路径清掉。
- **收紧判据后 RS27 ×3 = 3/3**：第 1 趟 planner 规划天气 + 会议；**第 2 趟正是 `5c729fbc` 上出错的写法**（`{title: 深圳下雨就通知我, time_text: ""}` + 会议），
  **第 3 趟正是 `c99a9a74` 上出错的写法**（`{kind: location, title: 深圳下雨, time_text: 深圳下雨就通知我}` + 会议）——两趟事件那步都被诚实拒绝
  （「我只能按时间或地点提醒…」，outcome `partial`），库里只有会议那条，清理句取消成功。
- **R3-04 时延读数**（`1255f7b2`；collector `step.agent` span 的 `claim_gate_hold_ms` = 第一个增量进闸 → 第一次放行）：两组共 32 句闲聊 / 问答，
  24 轮走流式谈话步（其余 8 轮落端侧、road-safety 或规划失败，不经闸）：

  | n | p50 | p90（nearest-rank） | 最大 | 0 ms |
  |---|---|---|---|---|
  | 24 | 247 ms | 598 ms | 830 ms | 4 轮（第一个增量已含完整首句） |

  读法：这是**首字**被闸推迟的量，也就是句级等待的全部代价——它从二轮批 B 起就存在，批 D 没有改变 ≤ 160 字句子的行为。**首音**的推迟不超过它：
  网关 TTS 的首段本来就要等第一个软断点（`_minimax_segments`），闸只是把它推到首句句末。同批 step 时长 1.2–5.9 s。
  若要再压，可选路是把「保留区」放行（只扣最后 15 个非空白字）用于所有句子而不只超长句——代价是声称的删除粒度从整句变成「从声称起点到分句末」；
  本轮不做，列为可选项。artifact `.artifacts/gate-latency-1790146597.json` / `.artifacts/gate-latency-1790146905.json`。

## 6. 评审「体验与评测意见」的处置

| 意见 | 处置 |
|---|---|
| continuity 70/71 只是 71 个检查点；L1 107/117、L2 3/4 是诊断 | 同意，本仓记录一直按「检查点 / 诊断读数」写；本轮每批只引用与本批相关的读数，不借数 |
| 空计划失败的归层不单独证明「代码变更完全无影响」 | 同意；有疑问再做冻结模型与资产的成对消融，不作本轮前提 |
| 「接孩子后去万象城」两阶段要持有第二阶段 | 记录评审给的判据（持有第二阶段、知道何时继续、用户可见恢复路径），产品口径仍未定，本轮不动 |
| 不扩 Agent、不开负收益 goals/covers | 照做 |

## 7. 顺带发现（不在本轮范围，仅记录、未修）

时延取样的 32 句与 RS27 复跑里看到的、与本轮六条无关的问题，逐条带 trace 留给下一轮：

| 现象 | 层 | 证据 |
|---|---|---|
| 「推荐三部适合全家看的电影」端侧执行 `media.play`（视频），答「好的」 | 端侧规则：视频分支里 `"看" in t` ⇒ play，推荐请求被当成播放指令；写操作的问句闸不认「推荐…」 | session `probe-qa-gatelat-1790146597-5`；模拟车的视频已用同一身份「关闭视频」复原（`media.stop`） |
| 「给我讲讲新能源车冬天续航为什么会下降」答「电量72%」 | 端侧规则：`"续航" in t` ⇒ `battery.query`；「为什么 / 原理」这类知识问句只对写操作和除雾让路，查询不让 | `probe-qa-gatelat-1790146597-9` |
| 「怎么缓解开车时的疲劳」与「开长途前要注意些什么」答同一段天气快照 | 云侧落 `safety.driver_state` / `safety.driving_advice`，road-safety 两个意图都只给天气 +「注意劳逸结合」 | `…-1790146597-6` / `…-3` |
| 「如果你能去旅行，你想去哪里」13.5 s，话术里冒出「前面那个小问题出了点状况」 | 假设句被当成条件计划（日志 `Preserved adaptive control metadata`）⇒ T2；chitchat `depth=deep` 的首个增量 2.5 s 内没到 ⇒ 流式 `DEADLINE_EXCEEDED`、unary 重跑同样超时 ⇒ 再规划出第二个 chitchat 才答上，聚合器把失败那一步念了出来 | `probe-qa-gatelat-1790146905-15`（cloud-planner 日志 07:04:00–07:04:13 UTC）；R3-06 的运行时 ID 在这趟正常（`t1-r1` 执行成功，闸读数就来自它） |
| 「用两句话介绍一下深圳」`planner_failure`、「周末在家可以学点什么新技能」`no_plan` | 规划方差（前一句在上一组成功） | `…-1790146905-2` / `…-11` |
| 「从深圳欢乐海岸出发，不走高速」（没说去哪）2/3 被执行成导航：一趟起终点相同、一趟编出「天安门」2390 km | planner 替用户填目的地 / navigation 不拒起终点相同 | RS21 `c99a9a74` 第 1、3 趟（`.artifacts/probe-round3-batchD-c99a9a74.json`） |
| RS27：planner 把事件订阅改写成「查天气 + 下雨就提醒带伞」，T2 多批各建一条同义提醒 | 规划层：条件提醒 guide 的「持久订阅」判别在这句上失手；换了标题的同义提醒挡不住 | §5.1.1 第一组第 2 趟 |
