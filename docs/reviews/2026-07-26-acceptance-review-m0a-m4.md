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

> **进度（2026-08-01）**：⑥ 由 M-A 收口（§9）；①② 与 §3 一批 occupant 卡由 M-B 收口
> （§10）；④⑤ 与 §4-3/§4-6 一批触达卡由 M-C 收口（§11）；**只剩 ③ MCP 订单查询/取消
> 入口**（属 M-D 外部生态，仍开放）。P2 七卡：P2-03/P2-04 由 M-A、P2-01/P2-02 由 M-B、
> P2-05 由 M-C 收口，P2-06 历史已修；**P2-07 toolcall provider 能力位仍开放**（M-D）。

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

---

## 9. M-A 余项收口（2026-07-31）

M-A 后续批次把本报告 §5 的“测试真实性”遗留落实为统一 manifest runner、签名隔离、
privacy inventory、真实 provider/语音/安全 profile 以及可复算 canonical 协议，并同步吸收
main 的 M5 数据飞轮（`87edc13` 是当前分支祖先）。落域问题的处理原则已经切换为：
说法漏召优先追加 `skills/exemplars/`，跨轮组合知识进入 `skills/guides/`，结构化事实缺失
补 context/focus；不为验收回添已退役 `route_hints`。

最后一轮全量 canonical `e2e-20260731134142-36ed01d2643c` 在提交 `62e980d` 上得到
**31/34 entries PASS、0 SKIP**，未获得 promotion；三条失败分别是 journeys B5-2 最新列表
详情、scene verify 主动汇报、vision 短视觉问句。其后没有用重复全量跑“抽到一次全绿”：

- `66e4af7` 修复最新列表首项回填、scene verify 全局去重键（改为 user+activation 实例，
  `priority=user_contract`），并把视觉 badcase 作为 trace exemplar 进入 M5 数据车道；
- 定向真栈 `e2e-20260731143444-07fa95a42836`：**scene 26/26、vision 全通过、B5-2 通过**；
- `8f02ca5` 让 focus 持久化地图已解析目的地及 waypoint 候选，并升级
  `navigation-with-stop` guide，修复顺路候选后的裸序号续接；
- 定向真栈 `e2e-20260731145952-48c4da9d937f`：journeys **target 20/20**，B5-1 与 B5-2
  同时通过；总计 34/35，唯一红灯是无关的 B1-4 杭州天气省略追问采样回落深圳。
  B1-4 在上一轮全量已经通过，且本轮改动不触及天气链，故分类为既有 LLM 方差，不重新打开
  本批范围。

产品负责人在 2026-07-31 明确裁决：停止为了满足初始重型流程而反复执行同一套用例，以
**全量覆盖证据 + 失败项定向复验 + 相关单测**完成 M-A。这个裁决只豁免本次 M-A 的“必须再抽
一次 34/34 canonical promotion”手续，不改变 runner 的真实性协议，也不允许把未 promotion
写成 canonical 全绿。最终证据口径因此是：

- 全仓回归（最后一次业务改动前）：Python **3493 passed / 13 skipped**，HMI **215/215**
  并 build，Dashboard **17/17** 并 build，Go 全过；
- 最后一批相关回归：场景/上下文/E2E 协议 **384 passed**，focus **34 passed**，
  skill golden 8/8，exemplar 契约 **212 条 / 13 域**；
- 最后一批真栈：scene 26/26、vision 全通过、journeys target 20/20；真实 provider、
  S2S、声纹、auth、mTLS 的通过证据来自上述最后一次全量 canonical。

**M-A 状态：按负责人裁决完成。** 未生成新的 canonical promotion，B1-4 继续作为普通
RoutingBench/journeys badcase 进入后续数据飞轮，不阻塞 M-A，也不在本次收口里加规则。

---

## 10. M-B 多乘员数据隔离收口（2026-08-01）

M-A 关的是 §5「测试真实性」；M-B 关的是 §3 与 §7 P1-01/P1-02 这一族 **occupant 缺口**。
一句话定性：**M4 P4 让系统知道「谁在说话」，但那只到请求控制面——数据面存不下来，
等于没识别**。Redis 里的 Turn 只有 `role/text/ts`，于是同一 cabin session 换个人说话，
上一位的话会按当前说话人归档，而且在记忆里留下持久脏数据。

### 10.1 已修（本批）

| # | 缺口 | 修复 |
|---|---|---|
| 1 | 巩固窗口 session 级、说话人盲（§3 立卡） | Turn 存完整 OwnerKey + `turn_id`/`exchange_id`；**抽取窗口在进 extractor 之前按 owner 切好**（归属不交给 LLM）；节流键从 session 级改 `(session,user,occupant)`——否则「A 说三轮、B 说第四轮」会在只说过一句的 B 名下触发 |
| 2 | 历史跨乘员共享 | `GetSession.scope` 缺省 OWNER_ONLY，`ALL_OCCUPANTS` 须显式且只供管理视图；`last_n` 是**过滤后**上限，切中 exchange 时整体舍弃最旧的半个（只留 assistant 半句会让抽取把助手的话当用户偏好） |
| 3 | `source_turn_ids` 拿 session_id 顶替 | 改存真实 turn id。它是 `weighting.evidence_count` 的输入——填 session_id 时永远数出 1，「说过一次 vs 每周三次」的区分**从未生效过** |
| 4 | `profile.places` 无 occupant 维度（P1-02） | 唯一真相源改 owner-scoped `memory_item place.*`；upsert 变 per-key patch（不再整块 map 覆盖）；primary dual-read legacy KV **只补新表缺失的 key**，非 primary 永不读 KV |
| 5 | reminder 全域零 occupant（§3 同根） | `reminder_item.occupant_id` + owner 索引（加法式 DDL）；CRUD/list/序号态全按 OwnerKey；**`claim_due`/`claim_location` 仍跨 owner 原子领取但消费必须先分组**——此前整批共用一条 speech 且 `user_id` 取 `due[0]`，两人同秒到点时一个人会听到另一个人的提醒 |
| 6 | 端侧快路径轮次不带身份 | `_record_local_turn` 从 `request.context`/`meta` 取 OwnerKey，一次本地请求写成一个完整 exchange |
| 7 | 重复注册同名必生成新乘员（§3 立卡） | 显示名同账户唯一（NFKC/trim/折叠/casefold + partial unique index）。**重名＝同一个人被分成两个 occupant，两条相似模板互相顶成 `ambiguous`、判定恒回 primary**——真机反馈过的「谁说话都认成同一个」有这一层。存量冲突组只报不改（`name_conflict`），系统不自动加后缀选赢家 |
| 8 | HMI 记忆面板单行删除按 scope 扩大（§3 立卡） | 新增 `DeleteMemoryItem`（OwnerKey+item id）：跨 owner 回 `not_found`（回「不是你的」会泄露它属于谁），缺 occupant 回 `missing_owner`（绝不推断 primary、更不扩大成 user-all），`identity.name` 回 `managed_memory`，同事务清悬空关系边。面板默认只列当前乘员 |

契约登记 `docs/conventions.md` §9.13；proto 纯追加、`buf breaking` 对 main 通过。

### 10.2 明确未做（本批范围裁决）

原 superpowers M-B 计划是 15 个 task（跨域 saga、privacy registry 协议、observability
脱敏、Reminder/Scene admin gRPC、迁移 CLI + `pg_dump` 备份仪式、真栈全矩阵）。
产品负责人 2026-08-01 裁决简化执行，按「**先修真的会产生错误行为的缺陷**」切分：

| 未做项 | 为什么不做 |
|---|---|
| L2/L3/L4 跨域删除 saga + `runtime/privacy_registry.py` | GDPR 完备性，不是当前错误行为。单行删除的爆炸半径（真 bug）已由 L1 关掉 |
| observability 四表 owner 列与原文脱敏 | 同上；且 obs 原文已有保留期与 `gate_content` 兜底 |
| ReminderAdmin / SceneAdmin 管理 gRPC | 只为 L3/L4 saga 服务，随之后置 |
| 独立迁移 CLI + preflight/备份流程 | 两处 schema 变更都是加法式 `ALTER ... IF NOT EXISTS`，随服务启动幂等应用；建独立 CLI 是为「有损重分配」准备的仪式，而本批**不重分配任何数据**（旧行一律归 primary） |
| 真栈多乘员 E2E 矩阵 | 隔离契约已由单测钉在**行为**上（A/B 双向、跨 owner 拒绝、同名不串、分组不合卡）。真栈矩阵是覆盖面证据，价值在回归而非发现 |
| 声纹注册单事务（advisory lock） | 故障窗＝两次写之间的毫秒级崩溃，后果「模板在、名字没有」且用户可用改名自愈；做成单事务须把 conn 穿透 `remember()`（它当前还在事务内等 embedding provider），风险大于收益 |

### 10.3 一条值得记住的判据

**兼容的方向永远是收窄，不是放开。** 旧 Turn、旧 reminder、无主写入统一归 `primary`
——这是有损归属迁移，归了就不可自动恢复；但它绝不能变成「谁都能读」。
同理：普通读写缺 occupant 落 primary，而 **owner 级删除缺 occupant 一律拒绝**——
读错一次只是少看到东西，删错一次是把别人的数据一起删了。

---

## 11. M-C 可靠触达收口（2026-08-01）

M-A 关「测试真实性」，M-B 关「多乘员归属」，M-C 关的是 §4-6 与 §7 P1-04/P1-05、
§4-3 P2-05 这一族。核心命题一句话：**`publish 成功 ≠ 用户收到`**。

### 11.1 已修（本批）

| # | 缺口 | 修复 |
|---|---|---|
| 1 | proactive ack 后死亡窗口（§4-6 立卡） | 新增 `proactive_delivery` 账本，`critical`/`user_contract` **落库后才 ack**。M3 RFC 记的理由是「生命周期以秒计，落库不值当」——那句话对 advisory/ambient 成立，对用户合同不成立：「到点提醒我」的生命周期不是秒，是**直到我看见**。落库失败时 ack 里如实带 `durable=false`，不假装持久接管 |
| 2 | 深调研完成推送 HMI 断线即丢（§4-6 立卡） | 网关此前只把在线 HMI 数写进一行日志。现在 HMI 连上即请求补投，账上没销的重发一遍。**不需要为报告单独建表**——正文早已在记忆里，丢的只是那张卡，而卡在 payload 里 |
| 3 | 「发出去了」与「用户看见了」不可分 | `delivery_id` 随信封走到 HMI，HMI 幂等呈现后回执；**只有 `presented` 是合同完成**，WebSocket write 成功不能被提升为「用户看见了」。合并组带整组凭据、一次回执销整组——凭据随消息走，重启后仍对得上账 |
| 4 | 主动消息 × S2S（§7 P1-04） | 验收当时的止血是一刀切「全都只出气泡」。**一刀切的根因不在 HMI 判断力，在网关把 `priority` 吞掉了**——HMI 信封里只有 speech/card/advisory，分不出哪条是「后备箱没关」哪条是「路况还行」。透传后按档分流：critical 抢话、user_contract 排队待空闲补播、其余只出气泡 |
| 5 | Verifier 无法纠正「实际成功但报失败」（§7 P1-05） | 对 transport-uncertain 失败查一次世界状态。三条边界：只认超时这一族（**Agent 明确报的错是确定失败，不许被状态翻案**——那个状态可能是别的原因造成的）、只认声明式 `state_match`（schema 验的是本次响应，而响应根本没回来）、**不改 status**（世界状态证明得了「它发生了」，证明不了「这一步成功了」）。聚合器换一句诚实话术，**不伪造成功话术**——合成「后备箱已打开」需要领域知识，而编排核心零领域字面量 |
| 6 | 频控命中即丢不延后（§4-3 立卡） | 改走 `_suppress`，与闸3/4 对称。原注释「窗口是小时级，延后没意义」不成立——窗口是**滑动**的，一小时前那条一滚出去就有名额，而 tick 每几秒复评一次 |
| 7 | location 提醒不声明 conditions（§4-3 立卡） | **判断有变化**：durable 重投让消息可以在账上躺到 HMI 下次连上，**「陈旧内容被补播」因此从理论风险变成真风险**（此前投递路径只有 1.5s 合并窗，所以「不声明」侥幸没出过事）。但真正的保护不是加地理谓词——三态求值器的算子集与 scene solver 有等价契约，加算子要两边同步；用 `ttl_ms` 直接兜住陈旧补播（到点 2h / 到地 15min），谓词记账后置 |

### 11.2 明确未做（本批范围裁决）

原 M-C 计划是 16 个 task。按「**先修真的会产生错误行为的缺陷**」切分后未做：

| 未做项 | 判据 |
|---|---|
| 多实例 outbox worker、present lease、`state_version` 并发协议 | 为高并发准备的机制。**一辆车一个 HMI，量级个位数**——引入它们要付的复杂度换不来对应的正确性 |
| HMI IndexedDB 原子收件箱 | 幂等呈现用内存里的凭据集合就够；跨页面刷新的补投由服务端账本负责，客户端再存一份是第二真相源 |
| 影子模式与分来源渐进 cutover | 本批 durable 只覆盖两档且 fail-open 路径逐字保留（无 PG 即回落旧行为），灰度机制保护的是一个已经能一键回退的改动 |
| `research_report` 表与受控读取 API | 报告正文早就在记忆里（`_save_task`），丢的只是那张卡，而卡在 payload 里 |
| Task Ledger `owner_v2` forward-only cutover | 与可靠投递无因果关系，是被计划顺带绑进来的 |
| 真栈故障注入矩阵 | 三条关键路径已在真栈逐条验过（见 11.3），故障注入的价值在回归而非发现 |

### 11.3 真栈证据

根 compose，重建 proactive/edge-gateway/reminder-agent 后实测：

- 账本表在真库建成；`ack = {"accepted":true,"durable":true,"delivery_id":...}`；
- **HMI 不在线时投出的用户合同消息停在 `dispatched`**——已发出仍算没送到；
- 发 `replay` → 补投 1 条并携带原凭据 → 回执 → 状态 `presented`；再 replay 补投 0 条；
- `docker compose restart proactive` → 日志「投递账本恢复 1 条未送达消息」；
- 缺 `POSTGRES_DSN`/缺 asyncpg 两种情况都如实打 WARNING 并把 durable 报成 off
  （**降级可见**这一条是顺带验到的，不是设计时特意去验的）。

验证数据已清理（`proactive_delivery` 归零）。

### 11.4 两条判据

**① 「延后没意义」这类注释要看它依赖的前提还成不成立。** 闸5 频控写着「窗口是小时级，
延后也没意义」——但窗口是滑动的，tick 每几秒复评一次，前提从一开始就不对。
同理 M3 那句「主动消息生命周期以秒计」：它描述的是 advisory，被当成了全部四档的性质。
两处都不是笔误，是前提没被复查。

**② 「跑了相关子集」不等于跑了回归。** 本批实现完先跑了 proactive/cloud/reminder/HMI
全绿就提交了，全量跑出来 **33 failed**——根因是新建的 `proactive_delivery` 表存了个人
数据（payload 里的话术与卡片摘要）却没登记进 M-A 的隐私清单，而那套守卫在
`scripts/tests/` 与 `test/test_remaining_e2e_protocol.py`，恰好是「相关子集」之外。
**动 schema 的批次，守卫多半不在被动的那几个包里。**
顺带印证了清单机制的设计意图：同一份东西写三处（runtime `PRIVACY_TARGETS`、
manifest、测试里的硬编码期望+计数），互相比对、连顺序都比——它防的正是元数据自证，
代价就是加一个 target 要动三处，这是它**该有的**摩擦。
