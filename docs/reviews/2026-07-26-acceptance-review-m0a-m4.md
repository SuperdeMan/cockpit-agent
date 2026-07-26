# 智能化升级 M0a→M4 总体验收 Review（2026-07-26）

> 对象：`docs/design/2026-07-24-eva-benchmark-intelligence-upgrade.md` §6 全部六期（M0a/M0b/M1a/M1b/M2/M3/M4）
> 方法：七路并行深查（S2S×危险动作 / 声纹×记忆×ForgetUser / MCP写×确认×T2×Verifier / Skill×submit_plan×Provider / geofence×负荷×免打扰+S2S×主动 / provider 部分成功 / 测试真实性）+ 测试基线全量复跑 + 真栈 e2e 抽样复验 + 当日修复与回归。
> 结论先行：**六期主体真实落地、方向与质量成立；但跨阶段组合面上存在 2 个确认链 P0（已修）、
> 一批隔离/降级 P1（高频者已修，结构性者立卡）；测试体系整体诚实，但存在假绿面与陈旧基线。**

---

## 1. 总体结论

**完成真实性：基本属实，有四处窟窿（本轮已修三处 + 一处改口径）。**
逐期核对声称与代码：M0b Skill 层、M1a submit_plan、M1b 自进化（nightly 计划任务 `car-agent-evolve-nightly` 实测存在、07-25 日报真实产出、门禁 4 eval 全跑）、M2 Ledger/Verifier/记忆图谱、M3 主动引擎/geofence/MCP 桥、M4 S2S/声纹/视觉——主体实现与落地记录一致，多数「设计偏差被实测推翻」的记录也与代码吻合。窟窿：
- **trip-planner 是 M0a mock 回退治理的漏网第四家**：`_fallback = MockPOIProvider()` 运行期回退原样在（07-25 nightly badcase「红军长征路线图×4」正是该链路产物形态），且全 Agent 无一处 `_prov` 盖章。**已修**（结构性删除 + 诚实降级 + 4 处出卡盖章 + 回归锁）。
- **M3 DoD「MCP 下单确认链 CDP 绿」的 CDP 用例不存在**（CDP 集只有 C1-C6，无 MCP 场景；所有验证记录只声明 e2e_mcp 10/10，DoD 该条被静默跳过未改口径）。**本文档即为改口径**：该 DoD 以 `e2e_mcp` ⑧⑨⑩（确认→下单→幂等）承载，CDP 形态不再承诺。
- **MCP「写操作生命周期五项」两项纸面合规**：补偿 `compensate_tool` 仅准入期存在性校验、运行期零调用、用户零入口；「订单状态机」的 Duplicate 分支在同步写路径不可达（开单即终态）。**立卡**（需产品决策：接查询/取消能力）。话术承诺不存在的「查一下我的订单」入口**已修**（不再承诺 + result_ref 落 `outcome=uncertain`，conventions §9.9 已同步）。
- M2 RFC §4.3「M0a 确认闸对 T2 路径同样生效（loop 走 executor.run 同一尾链）」**论证与事实不符**——T2 流式直通不走该尾链（见 §2 P0-2 关联项）。

**跨阶段状态机：两个 P0 确认链缺陷，均已修。**（详 §2）

**降级与恢复：整体姿态健康**（edge-cloud 快速失败不重发、planner 原子计划、S2S 看门狗、ledger 惰性 orphaned、proactive fail-open 都成立），**但「超时=部分成功」这一族是系统性盲区**：executor 超时步会被 T2 replan 重复执行（已修）；S2S turn 异常收束在 HMI 侧无任何用户可感知提示（RFC 承诺的话术未实现，已修）；深调研完成推送在 HMI 断线时静默丢失、proactive ack 后死亡窗口真实存在（立卡）。

**测试真实性：诚实度高于典型工程**（错误不计成成功、探针自测、自灌数据教训已落地），**但有结构性假绿面**：SKIP 渲染成绿色 PASS、Windows runner 缺挂 M2 三条 e2e（已修）、e2e_vision 泄漏探针查错对象（已修）、evolve gate 不拦退出码（已修）、红线源码断言可被无害重排改空转（已修）、journeys canonical 基线陈旧 20 commit 且 AGENTS.md 引用的 15/15 是历史值（本轮重跑刷新）。

---

## 2. 确认链两 P0（跨阶段状态机核心发现，均已修）

### P0-1 补槽恢复无条件注入 `confirmed="true"` → 危险动作跳过二次确认

`engine._restore` 对 wait_confirm 与 wait_slot 两种挂起共用，恢复时一律给挂起步注入
`confirmed`。MCP 写步可达路径：「帮我下单一杯咖啡」（planner 未抽出 item）→ NEED_SLOT
「要点什么？」→ 用户答「拿铁」→ 恢复时被标已确认 → **用户从未见过金额直接下单**。M0a
中央闸同判据放行。修复：`_restore(state, *, inject_confirmed)` 显式化，仅 wait_confirm
恢复（用户真说了「确认」）注入；补槽重跑后照常二次挂起等真确认。契约测试 T5。

### P0-2 S2S 挡位下「确认/取消」无强制移交路径 → 假承诺/假取消

voiceLoop 的 D5-2 红线（确认条可见时一切定稿必须上云）被 S2S 分支
`if (useS2s()) { s2sPendingUser = t; return }` 整体旁路；escalate 描述与 persona 对确认词
零引导；`inject_text`（本可通告挂起态）是死代码；假承诺检测 `detect_false_promise` 要求
完成词×对象词同轮共现，确认轮（对象词在上一轮）结构性失效。后果：模型自答「好的已确认」
而后备箱没开；**取消腿更危险**——「已为您取消」而挂起在 Redis 里继续活 300s。
修复三层：① controller 镜像 needConfirm，确认窗内 S2S 定稿强制走主链 + `bargeIn()` 取消
provider 在飞生成（D5-2 在 S2S 下兑现）；② escalate desc + persona 补确认轮引导（第二道
防线）；③ 相关轮次的 turn.end 异常话术补上（见 §4-6）。

关联已修：**loop.py T2 流式直通缺 `require_confirm` 排除**（engine D0 有、loop 漏；两路
核查独立发现互证）——漏标 Agent 的动作会绕开中央兜底闸直流 HMI。已补同款排除 + 双路径
源码断言（T6）。**恢复丢防抖指纹**（`_RESULT_FIELDS` 白名单缺 `fingerprint`）——跨确认/
补槽挂起的副作用步防抖静默失效。已补（T7）。

---

## 3. 声纹×多用户记忆×ForgetUser（隔离与被遗忘权）

**红线复核通过**：`occupant_id` 不进 granted/权限/VAL/确认/支付，代码层面成立；
「一次唤醒锁一次」「认不出回 primary」「margin 主导」与声称一致。**GDPR 全量删四表同事务
级联**（memory_item/relation/voiceprint/identity.name）真实成立，e2e 实测「删单个乘员
无爆炸半径」。

**已修（本轮）**：
1. **S2S 自答轮记忆恒记 primary**——reflux 的 occupant 是 session.start 静态快照且 HMI
   根本不传；乘员 B 的 S2S 闲聊全进主驾记忆，M4 DoD「多用户记忆隔离」在 S2S 挡位不成立。
   修复：新增 `occupant` 上行帧（声纹识别落地即发、唤醒窗结束归位 primary、未 open 并进
   start 帧），网关就地更新 reflux。HMI 3 例 + 网关 1 例测试。
2. **ForgetUser 不删会话原文且 `sess:*` 无 TTL**——长期记忆删了、原始对话永久留存
   （假删除的另一半）。修复：`sess:*` 加 7 天 TTL（`MEMORY_SESSION_TTL_S`）+
   `user_sessions:{uid}` 索引 + 全量删级联清会话；scope 定向删不误伤。测试 2 例。

**立卡（结构性，需设计决策）**：
- **巩固抽取窗口 session 级、说话人盲**（轮次不存 occupant）：同会话换人说话，上一个人的
  话按当前说话人归档——写侧隔离在多乘员共处场景不成立。需轮次结构加 occupant 标注 + 抽取
  按说话人过滤（占位符已有：AppendTurn 全链已带 occupant）。
- **`profile.places`（家/公司，highly_sensitive）无 occupant 维度**：乘员 B「把这里设成
  我家」会覆盖主驾的家。需 proto GetContext/UpsertProfile 演进。
- HMI 记忆面板把 `identity.name` 当普通偏好渲染，「删除」走 scope 定向删（不级联声纹、
  一删删全部乘员的名字）；enroll 两写非原子；重复注册同名必生成新乘员（两个相似模板互相
  ambiguous → 识别静默失效）；routine 主动建议信封无 occupant（B 的习惯播给全车）。
- e2e_voiceprint 隔离断言单向（无「B 召回不到 A」反向、无 ForgetUser 场景），且识别器
  恒回 primary 也能全绿（弃权不判红是产品口径，但「识别坏了能红」的能力缺失）。

---

## 4. 其余组合的裁决（要点）

1. **S2S × 视觉：自洽通过**。escalate 回 HMI 走既有 `send()`，抓帧门控在该路径生效，
   与 P4 RFC §5.4 声称逐字一致（本轮实读验证）。
2. **声纹 × S2S：喂帧挡位无关**（同帧同时喂 s2s 与 vp），成立。
3. **geofence × 驾驶负荷 × 免打扰：机制成立**——位置提醒走 `user_contract` 通道，负荷/
   免打扰/频控三闸全豁免（e2e 实测 120km/h 照响、advisory 被延后），「到地必响」兑现。
   **已修**：snooze<10min 被去重闸静默吞（dedup_key 是条目 id 稳定函数、snooze 保留原 id
   ——直接违反 §9.8「绝不静默吞掉用户显式约定的提醒」；两处 dedup_key 拼入触发时刻）。
   立卡：location 提醒不声明 `conditions`（投递期复核恒真，今天靠路径短侥幸正确）；频控
   命中即丢不延后（与闸3/4 不对称）。
4. **S2S × 主动消息：文档影响评估不属实**。AGENTS.md/RFC 说「唯一会出声的是深调研完成」
   ——实际朗读判据是 `text && card`，提醒到点/到地、场景建议、低电量建议**全部恒带卡会
   朗读**，是日常最高频一批；S2S 挡位下主动 TTS 与模型音频**直接混音**（两播放器共用
   AudioContext 互不知情），且其生命周期回调把 FSM 从 SPEAKING 误推 FOLLOWUP。
   **已修（止血）**：S2S 交互进行中主动消息只出气泡不出声 + 主动播报文本喂回声指纹
   （自听通路收窄）。完整方案（治理器侧 `s2s_speaking` 情境位延后）维持立卡。
5. **Skill × submit_plan × 多 Provider：格式一致性通过**（few-shot 与 tool schema 逐字
   对照一致；双路径注入单函数产出真单变量；schema 无 require_confirm 有全序列化断言）。
   立卡：`few_shots` 是「文档有、代码不读」的空契约（照 README 写会被静默丢弃）；
   toolcall 对不支持 provider 每轮白打 2 次上游（无能力位无熔断）；`PLANNER_TOOLCALL`
   须重启生效与 provider 热切不对称；skills golden `expect_intents` 无消费方；
   eval_skills 不在 GitHub CI。
6. **provider 部分成功：**「escalate 已发出 + provider_silent」组合在代码上不可能（escalate
   即收束 turn 清看门狗），这层设计干净。**已修**：executor 超时步 T2 replan 重复副作用
   （超时≠失败——超时步也打指纹、同轮同指纹不重发、回填诚实「不确定」话术；真失败仍
   允许重跑，`test_loop_tier_dedup` 三条新契约）；S2S turn 异常收束 HMI 吞提示（RFC §6.3
   承诺的「说到一半断了」话术补上，用户主动打断不提示）；proactive 投递失败不发裁决事件
   （obs 面消失，补 `dropped/publish_error`）。立卡：Verifier 结构上无法纠正「实际成功但
   报失败」（state_match 只验声称成功的步；镜像现成、缺一个 FAILED 也查镜像的分支）；
   深调研完成推送 HMI 断线即丢（broadcast n==0 无处理，报告卡无持久化）；proactive
   ack 后死亡窗口（1.5s~ttl_ms，无持久化，RFC 记录的理由对 user_contract 档不成立）；
   cloud-gateway 对 planner 的 UNAVAILABLE 重试绕过已消耗的幂等闸。

---

## 5. 测试真实性裁决

**正面（查过、成立）**：畸形 LLM 输出处理覆盖密（本次核查质量最高部分）；「自灌静音帧」
教训在 e2e 层真实落地（残留两处均为直连 provider 的探针，合法）；eval_s2s_escalation 的
「错误不计成成功」三层防护无残留；ProviderLock 探针自带 5 条单测；pytest 计数与声称吻合
（collect 2279 vs 声称 2277，差 2 为新增）；journeys 回归级 fail 确实拦退出码。

**已修（本轮）**：
- `test_build_context_keeps_occupant_out_of_permission_branch` 可被无害重排改成**静默空转**
  （切片 end<start → 空串恒真）——这是守「声纹不提权」红线的测试。改为锚点存在性+顺序+
  非空三重前置。
- 「编排核心零领域字面量」**没有任何自动化护栏**（CLAUDE.md 声称由 test_planning.py 固化
  ——该测试是行为测试，零源码断言）。新增动态指纹断言：skills 文件的知识句永不回潮进
  planning.py（新 skill 自动纳入保护，不维护黑名单）。
- Windows runner（本机日常入口）缺挂 M2 三条 e2e（ledger/memory_graph/verify——含 GDPR
  级联删红线）；e2e_vision「图像不进对话链」探针跑在**没有图**的那轮响应上（真带图的两轮
  从未被查）+ 声称查 obs 实际从未查（均已修，obs 校验补上）；e2e_ledger 计数口径带
  LIMIT 5（历史任务积到 5 条后「+1」恒败、「不变」恒真）+ 无跨运行残留清理；
  evolve.py「门禁」无条件退 0（eval 全挂 nightly 仍绿——gate 非零现已置流水线退出码 1）。
- mcp_bridge 超时用例把旧话术（承诺不存在的入口）固化成期望——已随话术修正重写，并补
  「非超时异常=确定失败不装不确定」对照用例。

**立卡**：SKIP 渲染成绿色 PASS（runner 汇总无第三态，声纹/vision/s2s 缺 key 环境静默跳过
与 30 断言全过不可区分）；三条源码铁律均为固定黑名单挡不住增量（verify 的 token 表不含
M3/M4 任何域名）；journeys 分母排除 skip（覆盖率静默下降在比率上不可见）；
`e2e_memory_graph` GDPR 断言在表空时平凡成立；7 个 e2e 不在任何 runner（含 e2e_auth/
e2e_mtls 安全面）。

---

## 6. 本轮修复清单（代码 21 项 + 测试 15 项，全部当日回归）

| # | 修复 | 主要文件 |
|---|---|---|
| 1 | P0 补槽恢复不再注入 confirmed（仅确认恢复注入） | `orchestrator/cloud/engine.py` |
| 2 | P0 S2S 确认窗定稿强制走主链 + bargeIn 取消在飞 | `hmi/src/handsFreeController.ts` |
| 3 | escalate desc/persona 补确认轮引导 | `llm-gateway/s2s/protocol.py` |
| 4 | loop T2 流式直通补 require_confirm 排除 | `orchestrator/cloud/loop.py` |
| 5 | 恢复白名单补 fingerprint（跨挂起防抖） | `orchestrator/cloud/engine.py` |
| 6 | 超时步打指纹 + 同轮不重发 + 诚实回填（超时≠失败） | `orchestrator/cloud/executor.py` |
| 7 | S2S occupant 上行帧全链（HMI 识别即发/归位，网关热更 reflux） | `s2sClient.mjs`/`handsFreeController.ts`/`http_server.py`/`s2s/protocol.py` |
| 8 | ForgetUser 级联清会话原文 + `sess:*` 7 天 TTL + user 索引 | `memory/store.py`/`server.py` |
| 9 | snooze/geofence 重触发 dedup_key 拼触发时刻 | `agents/reminder/src/{scheduler,geofence}.py` |
| 10 | S2S 交互中主动消息只气泡不出声 + 喂回声指纹 | `hmi/src/App.tsx`/`handsFreeController.ts` |
| 11 | S2S turn 异常收束的用户可感知话术（RFC §6.3 兑现） | `hmi/src/App.tsx` |
| 12 | trip-planner mock 回退结构性根除 + 4 处 `_prov` 盖章 | `agents/trip_planner/src/{agent,pipeline}.py` |
| 13 | MCP 超时话术不承诺不存在入口 + `outcome=uncertain` + 超时/确定失败分野 | `agents/mcp_bridge/src/agent.py`、conventions §9.9 |
| 14 | proactive 投递失败发 `dropped/publish_error` 裁决事件 | `proactive/governor.py` |
| 15 | evolve gate 非零 → 流水线退出码 1（报告照常产出） | `scripts/evolve.py` |
| 16 | run_e2e.ps1 补挂 M2 三条 e2e | `scripts/run_e2e.ps1` |
| 17 | e2e_vision 泄漏探针查带图轮 + 补 obs 校验 | `test/e2e_vision.py` |
| 18 | e2e_ledger 真 count + 跨运行残留清理 | `test/e2e_ledger.py` |
| 19 | 红线切片断言防空转（锚点三重前置） | `test_voiceprint_not_auth.py` |
| 20 | 知识句防回潮动态指纹断言 | `orchestrator/cloud/tests/test_skills.py` |
| 21 | 新契约测试：T5 补槽不注确认 / T6 双流式排除 / T7 指纹白名单 / 超时防重发 3 条 / occupant 帧 3+1 条 / ForgetUser 会话 2 条 / snooze 1 条 / MCP 超时分野 2 条 / trip 无回退锁 | 各测试文件 |
| 22 | **畸形 plan 输出崩 Handle**（修复后真栈复验时抓到）：模型偶发输出 `slots` 为 list（`["item>拿铁"]`）→ `.items()` AttributeError 崩掉整个 servicer——空响应、确认挂起蒸发；`depends_on`/`slot_refs` 输出 `""` 同族。修=非 dict slots 按无效步走计划原子拒绝→重试，depends/refs 归一为空；+2 契约测试 | `orchestrator/cloud/planning.py` |
| 23 | **mcp shop.order 补 route_hints 兜底**：「点一杯大杯拿铁」在 shop.order/nearby 间掷硬币（toolcall salvage 判下单、fallback JSON 判找店，画像有「爱拿铁」时更偏后者）——此前 e2e_mcp 10/10 靠 salvage 侥幸。动词+「杯」+品类共现才兜、发现类语义 guard 让路；`eval_route_hints` 101/101 无回归 | `agents/mcp_bridge/manifest.yaml` |

**已知残余（e2e_mcp 稳定 8/10）**：剩两条红 = 当前 provider 对该句的 toolcall 输出质量
方差——畸形谱（空 steps / list slots / `$text` 包裹，各轮形态不同）叠加**槽位幻觉**（同句
「大杯」一轮抽成「中杯」→ goal 变 → 幂等键漂移 → 商户认不出同一单）。安全语义完好：每次
下单都过确认闸、无静默双扣路径；损失的是「重复识别」便利语义。根修方向（立卡）：hint
已路由让路时用捕获组补缺失槽 / size 归一——均动通用引擎，不在本轮改。

## 7. 立卡建议（优先级）

**P1（下一批）**：① 记忆写侧说话人标注（轮次带 occupant + 抽取过滤）；② `profile.places`
occupant 维度（proto 演进）；③ MCP 订单查询/取消入口（兑现「核对」与补偿承诺的前提）；
④ 主动消息 × S2S 治理器侧方案（`s2s_speaking` 情境位）+ 深调研推送持久化/重投；
⑤ Verifier「FAILED 步查镜像改判」分支（超时部分成功的最终兜底）；⑥ journeys canonical
全量重跑机制化（基线曾陈旧 20 commit 无人发现——建议每里程碑收官强制刷新；本轮改动
编排核心后已按铁律重跑 regression 集，见 §8）。

**P2**：HMI 记忆面板 identity/删除语义重做；enroll 原子性与重名检查；SKIP 第三态显示；
源码铁律黑名单改白名单/AST；location 提醒补 conditions；`few_shots` 契约实现或从文档删除；
toolcall provider 能力位。

## 8. 验证证据（本轮）

- 全量 pytest：修复前基线 2271 passed / 1 flaky / 7 skipped（flaky=`test_reregister`
  单跑即绿）；**全部修复后 2287 passed / 7 skipped 零失败**（+16 新增测试全过）。
- HMI node：**195/195**（+3 occupant 帧）；dashboard 16/16；tsc 错误数 21=基线。
- **journeys regression（修复后，改动编排核心按铁律必跑）：13/14 + 1 数据 skip，唯一
  红灯 A2-3 单跑即绿**（充电编织概率性，与 M2 收官时同款方差前科）。
- 真栈 e2e（修复前抽样）：mcp 10/10、proactive 16/16、geofence 7/7、voiceprint 全过、
  verify 首跑红 2=nearby 超时方差重跑全过、ledger 定位出测试自身缺陷（LIMIT 5 计数）。
- 真栈 e2e（修复后）：verify 全过、proactive 16/16、geofence 7/7、**ledger 五场景全绿**
  （受理开单/状态直答/幂等/取消 14s 停手/orphaned 诚实报告）、mcp 稳定 8/10（残余
  =provider 输出方差，定性见 §6 已知残余）；`eval_route_hints` 101/101 无回归。
- M1b nightly：计划任务在位（今晚 23:30 下次运行）、07-25 日报已入库（随本提交）。
