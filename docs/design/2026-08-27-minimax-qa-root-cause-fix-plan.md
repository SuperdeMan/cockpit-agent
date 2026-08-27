# 2026-08-26 MiniMax 云端长会话 QA：17 张问题卡的根因分析与修复方案

> **性质：方案文档，本轮零实施**（泓舟 2026-08-27 指示：只分析根因、输出修复方案，代码一行不改）。
> 来源：[`docs/reviews/2026-08-26-minimax-cloud-qa-findings.md`](../reviews/2026-08-26-minimax-cloud-qa-findings.md)
> （被测 release `c7c211b`，5 persona × 315 轮，自动计分 282/33 + 手工漏检）。
> 本文把那 17 张症状卡（P0×2 / P1×13 / P2×2）收敛成 **16 张根因卡 C1–C16**，每卡给
> 机制（已定位到行）、修法方向、验收与影响面。**全部行号在 `c7c211b`..HEAD 区间核对过**
> ——此后 main 只合入了 mobile / 文档 / TTS 传输层提交，问题面代码一行未动。
> 取证方式：artifact 逐轮回放（`.artifacts/dev-stack-verifications/qa-minimax-long-sessions.json`）
> + 七路只读代码调查 + 两次云端只读回读。**没有任何写操作**。

## 0. 接手须知（动手前先读）

- **先修哪张**：见 §4 实施顺序。C1（安全域）与 C2 的两个端侧真 bug 排最前——它们是本轮
  唯二的「确定性车控误执行」。
- **云端车态处置**（P0-02 的遗留）：2026-08-27 只读回读（collector `/api/vehicle/state`）
  显示车态已被 QA 之后的流量改动（`hvac_on=true`、`media=playing`，与探针基线和探针终态
  都对不上）。**不要再做「恢复到 08-26 基线」的考古**——按 C2 修完探针的诊断出口后，
  下一轮跑批自然会重打基线快照。若要现在归位，先只读快照、再按红线单独取写授权。
- **提醒存量处置（P1-07 的遗留）✅ 已完结**：清单 2026-08-27 只读出具（u1 ACTIVE 90 条，
  `.artifacts/reminder-cleanup-inventory-20260827.txt`）→ **泓舟点名「90 条全清」→ 当日执行**：
  单事务按 ID 点名 `UPDATE 90`（置 `cancelled` + `extra.reason="expired_sweep"` +
  `extra.batch="20260827-hongzhou-approved-90"`，不物理删除），事务内验证 UPDATED=90、
  **u1 ACTIVE 归零**；终态 u1 = cancelled 189 + done 13。执行与验证输出
  `.artifacts/sweep-apply-20260827.result.txt`。⚠ 残余小账：Redis `reminders_active`
  可能还缓存着旧序数列表直到下次 list/刷新覆盖——C10-A（参照系统一）落地时一并消化。
- 修任何一张卡后：跑该卡「验收」栏 + 全量 `python -m pytest -q -n auto --dist worksteal`
  （固定口径见 `AGENTS.md` §4.0）+ 对应组的真栈迷你集 `scripts/probe_qa_regression.py --group <g>`。
- **本文档的行号是 2026-08-27 时点的**。接手时先 `git log --oneline -- <file>` 确认该文件
  没被并行工作线动过，动过则以符号（函数名/判据名）重新定位。

## 1. 一页结论

**17 张症状卡不是 17 件事。** 收敛后有四个根因族贡献了大半：

| 根因族 | 一句话 | 覆盖症状卡 |
|---|---|---|
| **会话事实没有确定性读出口** | 候选集/数据源/执行史/挂起状态都在系统手里，但「重新列出」「数据源是什么」「哪些执行了」「还有待确认的吗」没有一条确定性通道，落到 chitchat/planner 就地编造或答非所问 | P1-04(后半)、P1-10、P1-13、P1-16、P1-07(追问轮) |
| **LLM 兜底路径零护栏 + 能力缺席→就近挑工具** | 安全问句被规划成车控写步、总结被规划成 manual.query、部分取消被规划成全清草稿——catalog 里最像的那个工具就是终点；chitchat 接住后还会编造执行与来源 | P0-01、P1-11、P1-13、P2-17、P1-08(T21) |
| **多意图复合句没有覆盖度机器判据** | 「先查X再点Y」「接A顺便B」的第二个诉求丢了，只有 prompt 里一句软约束，`_goal_value_dropped` 只判数字 | P1-03、P1-04(前半) |
| **单槽/粘性焦点在长会话下的既有缝** | 告警单槽无严重级比较、天气城市同域空槽轮清零、旧候选集/旧焦点吞掉新请求 | P0-01(续接轮)、P1-05、P1-06、P1-16 |

**与 findings 文档定性不同的七处重判**（逐条取证在案，直接改变该修什么）：

| # | findings 定性 | 实测 | 影响 |
|---|---|---|---|
| 重判 1 | P0-02「collector 无法回读车辆恢复终态，终值未知」 | **回读通道一直是好的**。`_settled_vehicle_state` 要求终态「≡基线且稳定两次」才返回，否则一律返回 `{}`（`scripts/probe_qa_long_sessions.py:1207-1240`）——把「读不到」与「读到了但不等于基线」合并成一句谎话，逐键 diff 分支（:1665-1669）成死代码。真正到不了基线的原因是**两个端侧真 bug**（新发现 N1/N2）：恢复轮 T56「关闭**后**挡风玻璃除雾」被执行成 `front_defogger.close`、T59「关闭音乐」只能落 `media.pause` | 「探针基建问题」拆成 **1 个 P1 级车控误执行 + 1 个恢复词表缺口 + 1 个探针诊断出口缺陷**，三个落点三个修法 |
| 重判 2 | P1-06「天气城市相邻性错误，漂移到上海」 | 不是相邻性算错。「上海」逐字来自 `memory/store.py:21` 的 **mock 车辆位置**（`{"city":"上海","road":"延安高架"}`）——road-safety 的 `safety.weather_alert` 是全仓**唯一**还保留 `ctx.fetch("vehicle.location")` 回退的天气路径（info/navigation 都已显式移除）；而焦点城市之所以没顶上，是接力规则在「同域但本轮空槽」时主动清零 `last_city`（`context.py:1330-1333` 第二个条件） | 修法从「地理校验」变成 **拔掉 mock 数据回退 + 修接力条件**；这是「运行期 mock 回退是盘点盲区」（history）在数据面的又一例 |
| 重判 3 | P1-08「显式导航起点未进入路线」 | 两层：① planner 按活动路线焦点选了 `navigation.reroute`，而 **reroute 的契约里根本没有 origin 维**（manifest.yaml:52，navigate_to/estimate 有），agent 侧起点硬编码「当前位置」（agent.py:1655/1660）；② 前一轮 T21 chitchat **编造**「已经为您重新计算路线，从华侨城欢乐海岸出发…全程约1.6公里」（新发现 N4，探针未判红）。另外 `runtime/slot_fidelity.py:40-50` 与探针 SL4 的 why 字段仍写着「manifest 没有 origin 槽」——**该事实 2026-08-20 起已过期**，判据注释没跟着改 | origin 修在 reroute 契约与实现上，不是回查层；编造归 C11；两处过期注释一并清 |
| 重判 4 | P2-17「序数取消仅回复处理失败，未给恢复指引」 | ① 落错域：「第二个先取消，其他继续」说的是**提醒批**，planner 就近挑了 `shop.preview_discard`——一个**零槽、整会话全清**语义的能力（`servers.yaml:25-32`，handler 不读任何 slot）；若当时有商户草稿，会**全清而不是清第二个**，只是碰巧无草稿走了 FAILED；② 桥其实返回了有内容的话术（「未能确认本次订单预览清理完成…」），被聚合器单步 FAILED 分支整段换成「抱歉，处理失败。」（`aggregator.py:172-178`；`AgentResult` 无 error 字段、follow_up/ui_card 不透传） | 拆成 **提醒批部分取消能力缺席（C10）+ 聚合器失败出口吞信息（C11）**；且 preview_discard 用 FAILED 本身违反「话术型拒绝用 OK」约定（§9.5） |
| 重判 5 | P1-10「股票来源追问丢标的、报错 provider/time」 | 确定性面**早就都在**：`info.stock` 有来源直答（`stock.py:11-14/88-91` `_PROVENANCE_MARKERS`）、engine 有焦点 symbol 回填、且有一条**逐字同句**的既有测试（`test_engine_focus.py:214`「数据源和更新时间是什么」）。断的是**路由那一跳**——MiniMax 把这句落到 chitchat（stock 能力零 route_hint、零范例），护栏全部建在「被路由到之后」 | 修法不是再加护栏，是把「来源/执行史」类会话元问题收进**确定性读出口**（C4），路由之前就短路 |
| 重判 6 | P1-04「候选重列不稳定」 | 「重新列出」无确定性通道是真的（`candidate_query.py` 四类算子无一覆盖，`_REFERENCE_RE` 不含「列出/项目」）；但上游还有一刀 findings 没记：**T18 的第二意图（点生椰拿铁）被 planner 整个吞掉**、探针未判红——重列请求本来就不该发生（探针是在按钮缺失时兜底发的它）。且 T19 重规划时 LLM 凭空把检索地点定到**青岛平度** | 症状卡拆到 C4（重列通道）与 C5（多意图覆盖）两张根因卡 |
| 重判 7 | P0-01 按「安全域落错」一体记 | 同一句「红色机油灯亮了怎么办」在四个 persona 走了**三条不同的错法**：cloud planner 产 `warning_light.close` 执行（family T28/adv T32）、manual.query+安全前缀（vehicle T34/family T61，行为对但 mock 手册）、road-safety 天气建议（info T24——它**不扫本轮告警词**，会话告警存储又只由 manual-rag 路径写入）。**极端方差本身就是「无确定性护栏」的签名**（§4.3「方差本身是能力缺席的签名」） | 一张症状卡三条成因链，C1 里分三段修 |

**七个 findings 未记、本次取证新发现的缺陷**（编号 N，并入对应根因卡）：

- **N1 后挡除雾错字**：`orchestrator/edge/fast_intent.py:424` 后除雾判定词表写的是「**档**风玻璃」（木字旁错字），「关闭后**挡**风玻璃除雾」匹配不上 → 掉进 front_defogger 分支，**真实执行了关前挡除雾**（vehicle 恢复轮 T56 实录，`actions=["front_defogger.close"]`）。→ C2
- **N2 media「stopped」语音不可达**：`LOCAL_INTENTS` 只有 `media.play/pause/next/prev`（fast_intent.py:18），「关闭音乐」落 `media.pause`——探针基线 `media=stopped` 靠语音永远恢复不回去（commands.yaml:1403 明明声明了 stop/close operates）。→ C2
- **N3 「胎压应该补到多少」端侧误接管**：adv T30 落端侧 `tire_pressure.query` 秒回「**暂不支持哦**」（探针放行）；info T23 同族句混合路径下端侧先答「暂不支持该控制指令」再由云端补答，**双话术拼接**。知识问句被端侧状态查询规则抢走。→ C2
- **N4 chitchat 编造改路线**：family T21「从深圳欢乐海岸出发」→ chitchat 编造「已经为您重新计算路线…不走高速，全程大约1.6公里，路上大概7分钟」——零 navigation 调用、零动作，探针 fails=[]。防编造 prompt 只枚举「查询/下单/订座/支付」，不含导航（`agents/chitchat/src/agent.py:197-200`）。→ C11
- **N5 告警信号重复拼接**：`agents/_sdk/safety_signal.py:71` 无条件 `f"{system}{hit}"`，而具名灯本身已含系统名 →「红色机油灯」产出 `机油机油灯`、「水温灯」产出 `水温水温灯`，原样进焦点、卡片与播报话术。既有测试只断言长度 ≤12，抓不住。→ C1
- **N6 空气质量卡的 update_time 是厂商摘要不是时间**：`agents/info/src/providers/qweather.py:284` `metadata.get("updateTime") or metadata.get("tag")`——新版 airquality 端点没有 updateTime，永远落到 `tag`（64 位 hex 署名摘要），T3 卡片实录。→ C9
- **N7 探针恢复判据把「关错对象」放行**：恢复轮 judge 只查 `need_confirm=False`（probe:1630-1631），T56 关错对象、T59 落 pause 都绿——恢复动作与目标键的一致性没人看。→ C2/C16

## 2. 症状卡 → 根因卡映射

| 症状卡（findings） | 根因卡 | 首偏离层 |
|---|---|---|
| P0-01 红色机油灯 | **C1** | 安全告警链（登记/分级/消费三段）+ cloud planner 无问句写闸 |
| P0-02 vehicle cleanup 未证明恢复 | **C2** | 端侧 fast_intent 规则 ×2 + 探针诊断出口 |
| P1-03 person-pickup 长上下文失焦 | **C5**（多意图覆盖）+ **C6**（接人边界/焦点劫持）+ **C7**（trip 接地） | cloud planning |
| P1-04 SP1 候选重列 | **C4**（重列通道）+ **C5**（第二意图丢失） | cloud engine/candidate_query |
| P1-05 CD6 旧餐单吞三轮 | **C3** | cloud engine `wait_slot` 插话判定 |
| P1-06 天气城市漂移 | **C9** | memory mock 数据 + road-safety 回退 + 焦点接力 |
| P1-07 reminder 取消错域/错对象/残留 | **C10**（+追问轮归 C4） | reminder agent + 准入闸 + 存量治理 |
| P1-08 显式起点未进路线 | **C8**（+T21 编造归 C11） | navigation reroute 契约 |
| P1-09 行程否定约束改坏天数 | **C7** | trip-planner modify/status |
| P1-10 股票来源追问 | **C4** | 路由前无确定性短路 |
| P1-11 charging plan 当普通导航 | **C14**（落域）+ **C11**（编造） | cloud planning（否定 policy 缝隙） |
| P1-12 provenance 20 失败行 | **C15** | 契约裁决（探针口径 vs §9.3） |
| P1-13 五轮总结丢域 | **C4** | 能力缺席 + 就近挑工具 |
| P2-14 口味偏好与推荐相反 | **C12** | nearby 判据用错变量 + 会话内偏好无通道 |
| P1-15 「5 点」解释成凌晨 | **C13** | `agents/_sdk/timewindow.py` 消歧 + `_deadline_note` 无荒谬值闸 |
| P1-16 恢复轮旧焦点占用 | **C4** | 挂起状态查询无确定性出口 |
| P2-17 序数取消处理失败 | **C10**（能力缺席）+ **C11**（聚合器失败出口） | planner 就近挑 + aggregator |
| （贯穿）探针漏检 7 处 | **C16** | 探针判据面 |

---

## 3. 逐卡

### C1 · 安全告警链：登记、分级、消费三段全是缝（P0-01，P0）

**覆盖**：family T28 / adv T32（`warning_light.close` 误执行）、vehicle T35-36 / family T62-63（「机油机油灯」）、family T29-30 / adv T33-34（旧黄灯顶掉红灯）、info T24-25（答成雨天建议）。

**机制（已定位到行）**：

1. **登记是路由的副作用，不是输入的性质**。会话告警唯一载体 `Focus.safety_alert`（单槽，`orchestrator/cloud/context.py:294-299`），唯一写入通道是 Agent 在 data 里declare 保留键 `_safety_alert`（`context.py:1191-1195`）；全仓只有 manual-rag / road-safety(驾驶员状态分支) / chitchat 三个声明方。**车控步没有 data 通道** ⇒ T28 被规划成 `warning_light.close` 的那一轮，「红色机油灯」这个事实整个没进系统——后续三轮 road-safety 消费的还是 T25 胎压轮留下的「黄灯」。
2. **单槽 + 最后写入者胜 + 无严重级比较**。`context.py:1194-1195` 无条件覆盖（amber 可顶掉 critical）；粘性接力 `context.py:1327-1329` 只看「本轮有没有新告警」，与等级无关。
3. **road-safety 不看本轮原话**。`agents/road_safety/src/agent.py:21` 只 import 了 `driver_state`，`alert_level/alert_signal` 一个没导入；`safety.driving_advice` 的分支序是 驾驶员状态 → 会话告警 → **天气建议**（`agent.py:192-218`）——info persona 没有 manual 轮、告警存储为空，于是「红色机油灯亮了还能继续开吗」直落天气分支拉 qweather 答雨天建议。
4. **planner 侧「机油灯 → warning_light.close」的语义陷阱**。端侧能力进 catalog 时**刻意只渲染 intent 名**（`orchestrator/cloud/planning.py:117-125`，省 token 的双臂实验裁的）——planner 看到的字面就是 `warning_light.close`，没有「双闪」二字；「红色机油灯（=warning light）」与它字面高度相似。road-safety 的 route_hint 只锚「还能继续开吗/慢一点开」整句（`agents/road_safety/manifest.yaml:51-56`），manual-rag 零 hint 零告警范例，`skills/exemplars/safety.yaml` 六条全是别的形态 ⇒ 这句话在全链路上纯靠 LLM，三向方差（§1 重判 7）。
5. **云侧没有「问句 + 写车控」闸**。端侧有（`fast_intent.py:319-334` 问句写动作整句上云），但云端 planner 把它规划回 `step.edge` 执行时无对应物：`actionability.py` 是 shadow 铁律不进主链、`Step` 无 effect 字段、`capability_meta.effect_of` 只有端侧消费、`warning_light` 又是 require_confirm/drive_restricted/voice_forbidden 三闸全 false 的对象——执行链全绿。
6. **「机油机油灯」拼接 bug**：`agents/_sdk/safety_signal.py:63-71`，`ALERT_CONTEXT` 里的具名灯（机油灯/水温灯）本身含系统名，`CRITICAL_SYSTEMS` 又独立扫出「机油」，line 71 无条件前缀拼接。实测：`红色机油灯亮了怎么办` → `机油机油灯`，`水温灯亮了` → `水温水温灯`。

**修法方向**：

- **A（必做，止血闸）**：cloud 侧补「问句形态 + 写效果车控步」的确定性守卫。落点在 `planning` 的计划校验段（`_validated_steps` 附近）：step 目标是端侧车控 intent（可从 `VEHICLE_INTENTS` 派生，零领域词）∧ `capability_meta.effect_of` = write ∧ 原话是非祈使问句（复用端侧 `_is_non_directive_question` 的判据——**下沉成共享实现，别抄第二份**，同 B5 `stream_state` 那条）⇒ 丢弃该步改走澄清/安全链。这与 R2.1「不改编排核心加硬编码」不冲突：判据零领域词、从知识库派生，与 `_goal_value_dropped` 同一形态但**进决策**——它守的是安全红线「LLM 不直连车控」的残余缝（LLM 仍在产计划，问句执行掉的是「规划/执行分离」里本该被确定性拦下的那类）。
- **B（必做，登记搬家）**：告警登记从「manual-rag 路由副作用」改为**输入扫描**——在 engine 组装上下文处对 `raw_text` 跑 `alert_level/alert_signal`（纯函数、零 LLM），命中即写 `focus.safety_alert`，与路由结果无关。road-safety 的 `_driving_advice/_driver_state_intent` 同时补「本轮原话扫描」优先于会话存储（import `alert_level`）。
- **C（必做）**：`focus.safety_alert` 写入走**严重级比较**：critical 不被 amber 覆盖（同槽比较，不需要多槽）；`_valid_safety_alert` 处加一行序。
- **D（必做）**：修 `alert_signal` 拼接——具名灯命中时不再前缀系统名（或 `hit.startswith(system)` 即不拼）。补断言测试钉具体返回值（现有 `test_signal_is_a_name_not_a_sentence` 只查长度）。
- **E（建议）**：给 road-safety/manual-rag 补告警咨询的 route_hints 或范例（「X灯亮了怎么办/还能开吗」→ safety.driving_advice）。**范例优先**（工作规则：修落域默认产物是范例）；hint 只在 A 闸落地后仍见方差时考虑。
- **F（记账不做）**：catalog 只渲染 intent 名是拿实验数据裁过的（Δ=0 省 1462 字符），**不要因本卡回退**；A 闸挡住写步后，落到 manual/safety 的方差由 E 收。

**验收**：SF3 组真栈 `--repeat 3`：「红色机油灯亮了怎么办」零 actions、告警登记为 critical、后续「还能继续开吗/慢一点开」答停车级、alert 字段=「机油灯」；对照组「打开双闪/关闭双闪」仍走端侧秒回（A 闸不误伤祈使句）；「大灯亮了」不触发告警（既有误杀面测试保持绿）。新增：cloud 层问句写步守卫的单测（正 2/负 2：问句+写=拦，祈使+写=放，问句+读=放，「怎么打开双闪」=拦——它是 manner 问句）。

**影响面**：A 闸改 `orchestrator/cloud/planning.py`（校验段，非路由段）——R2.1 铁律的边界要在 PR 里写清楚：这是**安全不变量**放唯一出口（B1 判据），不是给某 Agent 的路由特判。B 改 engine/context + road-safety。D 是纯函数修字符串。全部零新依赖。

---

### C2 · 端侧车控规则三处真 bug + 探针恢复链诊断出口（P0-02 + N1/N2/N3/N7）

**覆盖**：vehicle persona `after_cleanup={}` / `verified=false`；恢复轮 T56/T59；adv T30、info T23。

**机制（已定位到行）**：

1. **N1 错字**：`orchestrator/edge/fast_intent.py:423-425` 后除雾判定 `re.search(r"后\s*(挡|窗|玻璃|风挡|档风玻璃)?\s*(除雾|除霜)", t)`——词表里是「**档**风玻璃」（木字旁），真实话术「后**挡**风玻璃除雾」四字连写匹配不上（「挡」后面跟着「风玻璃」，单字「挡」+紧跟「除雾」的形态也不成立）→ else 落 `front_defogger`。**恢复轮 T56 实录执行了 `front_defogger.close`**，rear_defogger 从此停在 true，探针终态永远≠基线。短说法「后除雾」能匹配（可选组缺省），所以业务开的时候是对的——**开对关错**。
2. **N2 media**：音乐分支「关/停」落 `media.pause`（`fast_intent.py:1012+` 一族），`LOCAL_INTENTS` 无 `media.stop`（:18）；VAL `media` 对象明明声明了 `stop/close` operates（`commands.yaml:1403-1412`）。基线 `media=stopped` 是 VAL 初始态，被播放/暂停过一次后语音永远回不去。
3. **N3 tire_pressure**：「胎压应该补到多少」被端侧 `tire_pressure.query` 接管，VAL 回「暂不支持哦」（adv T30，纯 local）；「不知道具体车型时，标准胎压应该是多少」混合路径下端侧先答「暂不支持该控制指令」、云端 chitchat 再答一遍，**两段拼接**（info T23）。这是「知识问句 vs 状态查询」的边界：问「应该补到多少」是推荐值（手册域），不是读当前胎压。B4 门禁不覆盖③④段（规则产得出命令/命令过得了校验），这是 §9.29 五段链在 query 型上的新例。
4. **探针诊断出口**（重判 1）：`_settled_vehicle_state(expected=baseline)` 匹配不上一律 `{}`（`probe_qa_long_sessions.py:1207-1240`）→ 上层只报「collector 无法回读」，逐键 diff（:1665-1669）死代码；恢复轮 judge 只查 `need_confirm=False`（:1630-1631），关错对象照绿（N7）。
5. **连带**：`test_corpus_objects` 的语料没有「后挡风玻璃除雾」长说法（B4 流程要求新对象留识别语料，这个对象的语料只有短形）。

**修法方向**：

- **A（必做）**：改正 `fast_intent.py:424` 词表：`(挡|窗|玻璃|风挡|挡风玻璃|档风玻璃)`——保留错字形并列（用户口误/ASR 变体照收），并把可选组允许多词（`(?:挡风)?玻璃` 类写法）。**同步在 `orchestrator/edge/tests/corpus/vehicle_objects.yaml` 补「关闭后挡风玻璃除雾」长形语料**（对抗覆盖：正 2 = 前/后长形，硬负 1 = 「后备箱」不碰除雾）。
- **B（必做）**：「关闭音乐/停止播放」映射到 `media.stop`（VAL 已声明），`LOCAL_INTENTS` 补 `media.stop`；或裁决「pause 即视为已停」并让探针 `_vehicle_value_matches` 接受 paused≈stopped 的恢复等价——**推荐前者**（能力面本来就有 stop，缺的只是规则出口；后者是把尺子改宽）。按 §7.1 SOP 走（migration 探针基线要动）。
- **C（必做）**：`tire_pressure.query` 规则加「推荐值问法」让路：句含「应该/该补/标准/多少合适/建议」时返回 None 上云（同「查询式让路」先例 `fast_intent.py:419-422`）。云侧 manual-rag 本来就有胎压条目。
- **D（必做，探针）**：`_settled_vehicle_state` 拆两个出口——读不到（连 `{}` 都拿不回）报「collector 不可达」；读到但不匹配时**返回最后一次读数**，让逐键 diff 真跑起来，failures 报「rear_defogger: actual=true expected=false」这一级（「复合判据的诊断出口要拆开报」——android-m3 批已沉淀过同判据）。恢复轮 judge 增加「actions 里的对象=目标 state_key 对应对象」的一致性断言（N7）。
- **E（建议）**：全量扫一遍 fast_intent 词表里的形近错字（档/挡、燥/躁类），产出一次性报告——N1 这种错字靠肉眼永远扫不完，写个「词表字与 commands.yaml display_name/语料字集对照」的检查脚本更划算。

**验收**：`python test/smoke_edge.py` + `pytest orchestrator/edge/tests`（新增语料转绿）+ `test/eval_capability_integrity.py` + `scripts/check_intent_gate.py --strict`；探针侧单测 `scripts/tests/test_probe_qa_long_sessions.py` 补「不匹配时返回末次读数 + 逐键 diff 可见」用例；下一轮长会话跑批 vehicle cleanup `verified=true` 或 failures 逐键点名。

**影响面**：A/B/C 都是端侧规则，走 §7.1 五段链自检（含迁移探针基线更新）。D 只动探针。

---

### C3 · wait_slot 补槽把新意图整句吞成槽值（P1-05）

**覆盖**：merchant T44-46（旧餐单三连吞）；同机制历史案（2026-08-13 麦当劳答瑞幸）。

**机制（已定位到行）**：

1. T44 planner 给 `mcd.menu` 填了 `item_query="全部"`（LLM 自填；范例教的是无槽，`skills/exemplars/mcd.yaml:87-94`），桥的 `_menu_matches` 严格「查询词⊂商品名」匹配不上 → NEED_SLOT「没查到"全部"」（`mcdonalds.py:934-939`）。**「有什么可以点的」本该走空槽=整份菜单那条路**（`mcdonalds.py:947-1002`），一个「全部」把它变成了搜索词。
2. 挂起后，`_is_topic_change`（`engine.py:1288-1372`）判「附近的川菜馆」「麦当劳的第二个和川菜的第二个哪个贵」**都不是换话题**——前者不以动作动词开头（动词表无「附近」）、后者的「哪个」不在疑问词表（表里只有「哪些」）。判 False 即原样整句塞进 `item_query`（`_slot_answer` 对自由文本槽原样返回，engine.py:1375-1386），**且 plan 复原路径跳过全部守卫**——注入检测、`candidate_query` 短路、planner 一个都不过（engine.py:335-350 → 366），这就是 T45 span 零 LLM 直达桥的原因。
3. 讽刺的是：**「这是新检索」的词表仓库里已经有**——`candidate_query.py:59-60` 的 `_NEW_SEARCH_RE`（`附近|周边|就近|旁边|这边|哪里有|…|重新搜`），但 `_is_topic_change` 不消费它。**同一判据两份实现各自演化**（B1 那个 bug 的成因原型）。

**修法方向**：

- **A（必做，方向反转）**：对自由文本槽，把默认从「除非证明是换话题，否则当槽值」反转成「**槽值必须长得像这个槽**」。先例就在同一个函数里：`order_id` 槽只接受两种显式形状、其余一律换题（engine.py:1309-1320，注释写得很完整）。落法：给槽声明**值形状契约**——`servers.yaml`/manifest 的槽位加 `slot_shape`（如 `item_name`：长度 ≤12、不含 `_NEW_SEARCH_RE`/疑问词/逗号分句），`_is_topic_change` 通用消费（声明式，不改编排核心的判据本体）。这与 B6 `input_schema` 是同族（值域契约），且 `mcd.menu` 的 `item_query` 已有 `candidate_slot` 声明可搭。
- **B（必做，低成本先行）**：`_is_topic_change` 合并 `_NEW_SEARCH_RE` 词表（从 `candidate_query` import，**单一来源**）+ 疑问词表补「哪个」。这两行能立刻救 T45/T46 这两类，但**治标**——词表竞赛没有终点，A 才是形态修法。
- **C（必做，桥侧防御）**：`mcd.menu` 把「全部/所有/都有啥/整份菜单」归一成空 `item_query`（消费方防御，同「认不出退回干净类目词」判据）；`luckin` 同族排查。
- **D（建议）**：NEED_SLOT 挂起加**同域计数器**：同一个挂起被连续 ≥2 句都填不上（每次都 NEED_SLOT 循环）时放弃挂起、全新规划——黑洞的止损底线。

**验收**：`test_engine_confirm.py` 新增：「附近的川菜馆」「麦当劳的第二个和川菜的第二个哪个贵」在 mcd.menu 挂起下判换题（现在没有任何一条断言「附近开头的名词短语是换题」——B 组调查确认）；桥侧「有什么可以点的→整份菜单」「全部→整份菜单」用例；真栈 `--group candidate` 复跑 CD6 3/3。反向对照：「拿铁」「巨无霸」这类真菜品名仍然被当槽值续接（别把补槽修死）。

**影响面**：engine `_is_topic_change` + servers.yaml 槽声明 + mcp-bridge。A 的 slot_shape 是新契约面，落 `docs/conventions.md` §9.34（下一个空号）。

---

### C4 · 系统持有的会话事实没有确定性读出口（P1-04 后半 / P1-10 / P1-13 / P1-16 / P1-07 追问轮）

**覆盖**：merchant T19-20（重列→重搜出青岛）、info T41（来源→chitchat 编「东方财富 19:23」）、T44（「刚才查到的」→反问代码）、T43（编造自我纠错）、T55（五轮总结→manual mock）、vehicle T51/info T56（「还有待确认的操作吗」→「嗯」/答学校地址）、family/info T37（「刚才实际改了哪一条」→整表）。

**机制（已定位到行）**：

1. **这族问题问的都是系统自己手里的事实**：候选集在 `focus.candidate_sets`、来源在卡片 `_prov`+span、执行史在 `AppendTurn.actions`（Q6 账本）、挂起在 `SessionState`。但读出口各缺一块：
   - 「重新列出」：`candidate_query.py` 四类算子（最值/跨组比较/合计/序数取值）无重渲染；`_REFERENCE_RE` 不含「列出/项目」→ 不劫持 → 进 planner 重搜，LLM 顺手编了个城市（青岛平度）。且候选台账白名单只留 `id/name/price` 等 13 键（`context.py:674-679`），菜单卡的 `send_text/categories/subtitle` 落库即丢——**想重渲染也没料**。
   - 「数据源/更新时间」：确定性直答在 `info.stock` 内部（重判 5），路由不到就没有；**执行账本只记动作名**，provider/`_prov` 不入账（G 组核实：info 域零 action，`_prov` 只活在卡上、全仓 orchestrator/memory 无一处读它）。这正是 `AGENTS.md` §4.2「会话级数据源/降级账本（I-033）」那行等的启动条件——**它当时写『出现第二个消费方再做』，本轮一口气来了四个**（T41/T43/T44/T55）。
   - 「哪些执行了」：chitchat 审计闸 `is_execution_audit_question` 的判据要求「回顾指代 ∧ 执行询问」双命中（`audit.py:32-36`），「总结这五轮里哪些执行了」的「这五轮」不在回顾词表、T37「刚才实际改了哪一条，时间是什么」落到了 reminder.list（planner 侧）——审计闸建在 chitchat 里，**别的域接走就够不着**。
   - 「还有待确认的操作吗」：`system.no_pending` 只在裸确认词/确认帧触发（`engine.py:353-364`），**问句形态没有出口** → chitchat →「嗯」。
2. **落不到确定性出口的都掉进 chitchat**，而 chitchat 只有 4 轮纯文本历史（`agent.py:243-245`，actions/卡片/_prov 都不进 prompt）——它连想如实回答都没有材料，编造是结构性的（T41 编 provider、T43 编自我纠错）。

**修法方向**（判据一句话：**凡是系统持有的事实，判据面就得是闭合的**——§9.27 那条的第四次扩面）：

- **A（必做，账本扩维）**：Q6 执行账本加「数据源」维——每轮把外部卡的 `_prov`（vendor/mode/fetched_at + 卡型）与降级事实随 `AppendTurn` 落库（复用 `actions` 的载体形态：会话轮次、不建新表；写入点在 engine 落账处从 final 帧的 ui_card 收集，与 `_executed_action_names` 同位）。这就是 §4.2 I-033 行的正身，落地后把该行销账。
- **B（必做，读出口收口）**：把 chitchat 审计闸升级成**编排层的确定性短路族**（与 `candidate_query` 同挂点、plan 构建之前）：① 挂起状态问句（含「待确认」「还有没确认的」+ 问形）→ 读挂起表直答；② 数据源问句（`_PROVENANCE_MARKERS` 从 stock.py 下沉共享）→ 读 A 的账本直答；③ 执行史问句（现有 audit 闸搬家/复用，回顾词表补「这N轮/这几轮」）。**零 LLM、有账才答、无账明说**——同 Q6 的「话术层判据验证不了真话」教训，读出口只念账本。
- **C（必做，重列算子）**：`candidate_query` 增第五类算子「重列」（`重新列出|再列一遍|再显示|刚才(的)?(选项|列表|候选)`+指代闭合到组，复用 §9.32 的 `resolve_candidate_scope`），从候选台账重渲染**文字清单**（序号+名字+价格——台账里有的那几键），并明说「按钮请看刚才那张卡」。**不要为重渲染扩白名单**——白名单 13 键是与产生方的契约（§9.27），扩它要同步 `_PRODUCER_SHAPES`，且「重列」的用户诉求是「再看一眼可选项」，文字清单已闭环。
- **D（建议）**：stock 查询产出进候选集（`data.items` 形态给 symbol/price/market_time 一行），让「刚才查到的行情」走既有候选消费面——比给 stock 单独造焦点便宜。

**验收**：四组各 `--repeat 3` 真栈：重列（同轮候选逐字复现、零 provider 调用）、来源追问（vendor/market_time 与上一轮卡 `_prov` 逐字一致——探针已有 `stock_provenance_from` 判据）、五轮总结（答案里的每个动作在账本里、账本外零动作）、「还有待确认的操作吗」（有挂起报挂起、无挂起说没有，`differs_from_turn` 兜底）。对照：真的新检索（「再搜一下附近的川菜」）仍进 planner。

**影响面**：engine 短路族挂点与 `candidate_query` 同位（`cloud.candidate_aggregate` 旁），是既有形态的扩面不是新机制；账本 proto 不动（actions 载体复用则 memory/store 加一列 JSON 值——**若动 AppendTurn 字段要走 proto 流程**，先评估复用 actions 列表塞 `"_prov:vendor=…"` 型编码 vs 新字段，推荐新字段、别做魔法字符串）。

---

### C5 · 多意图复合句没有覆盖度机器判据（P1-04 前半 / P1-03 部分）

**覆盖**：merchant T18（「先查瑞幸，再点生椰拿铁不加糖」→ 只执行 nearby，下单意图消失；同句 T21 却成功出双步——**同句两态就是零护栏的读数**）、family T50（「接孩子放学，顺便找麦当劳，5点到校」→ nearby 无 keyword 反问类目、navigate 没发出）。`AGENTS.md` §4.2「多意图复合句被单域吞掉」那行等的「稳定可复现的族」**本轮凑齐了**。

**机制（已定位到行）**：

1. 覆盖度只有 prompt 软约束（`planning.py:545-552`「提交前逐个核对每个肯定诉求…」，守卫测试只断言这段话在 prompt 里）；唯一机器判据 `_goal_value_dropped`（`engine.py:107-122`）**只判数字、只做观测**，且注释自认「组合意图漏第二步只能判到缺步」而没有实现。
2. `plan_repairs`（guide 的 `dependency_slot_ref`）只接线不补步：`skills.py:701-704` 要求生产/消费步**都已存在且唯一**，T18 消费步压根没出 ⇒ 静默 continue，零观测。
3. T50 的次生灾害：nearby 步丢了「麦当劳」槽 → NEED_SLOT 反问 → **一个步挂起会把整轮挂住**，同轮的 navigate 步没机会执行（engine 挂起语义），复合句被一个补槽问题整体劫持。
4. route_hints 的接送复合 hint（`navigation/manifest.yaml:96`）逐字覆盖了 T50 原句形态，但同句两次一对一错——hint 的 `policy: replace` 在 planner 已产出同 intent 步时的合并语义，与「另一半诉求（nearby）由谁保证」之间没有契约（hint 只产 navigate_to 一步，nearby 那半仍靠 LLM）。

**修法方向**（§4.2 那行写的方向仍然对：**换判定形态，不逐句补范例**）：

- **A（必做，判据形态）**：把「肯定分句覆盖度」做成**机器观测→再进决策**的两步走。第一步（本批）：确定性拆句（顿号/「再/顺便/然后/先…后」连接词，复用 `_expand_paired_objects` 的拆句家族），对每个肯定分句跑一遍 fast_intent/nlu 域粗判，若某分句的域在 steps 里零覆盖 ⇒ obs 记 `clause_uncovered`（与 `goal_value_dropped` 并列）。**先拿真实分布**（B6 shadow 的纪律：「诊断出一个洞不等于这个洞就是病因」——覆盖率多少、误报多少，两周观测再谈接管）。
- **B（必做，独立于 A）✅ 泓舟 2026-08-27 拍板放行**：挂起不冻结兄弟步——NEED_SLOT 只挂起**该步及其下游依赖**，无依赖的兄弟步照常执行完再挂（T50 的 navigate 与 nearby 无依赖）。动 engine 挂起语义（恢复时跳过已完成步——`completed_results` 机制已存在，缺的是「挂起前先跑完无依赖步」的调度顺序），实施时过 `test_engine_*` 全族回归 + 真栈多意图迷你集对照。
- **C（建议）**：A 的观测数据成熟后，`clause_uncovered` 升级成 salvage 级重试（把漏的分句单独再规划一轮，同 `PLANNER_TOOLCALL_SALVAGE_RETRY` 的形态——重试成功率 ≈70% 的先例）。
- **D（不做）**：继续加接送句形 hint/范例——person-pickup 批已刻意停手（「再按句形逐条补就是 per-utterance 调参」），本卡维持该裁定。

**验收**：A 的 obs 列上线后拿本轮五句真栈复跑看命中（T18/T50 应报 `clause_uncovered`，T21/T52 成功轮不报）；B 单独验「nearby 挂起 + navigate 无依赖」时 navigate 照发、恢复轮补槽后不重复导航。迷你集 `--group pickup`（person-pickup 卡建的）+ SP1 组复跑。

**影响面**：A 在 engine 观测面（零决策），B 动执行调度（**是这批里唯一动编排执行语义的**，要过 `test_engine_*` 全族回归）。

---

### C6 · 接人链路：确定性边界窄 + 旧焦点劫持复合句（P1-03 其余）

**覆盖**：family T47（「接爸妈去吃饭」→ 川菜列表，零接人）、family T53（「接孩子后去万象城」→ 商场列表）、family T55（「先去接我妈，再找家川菜馆」→ 被三轮前的「万象城」焦点劫持成「哪个城市的万象城？」）、info T54（「找不到就问我，不要猜城市」→ 被执行成 reroute 加途经点）。对照：family T48 同形态句（换成儿子）走对了（navigation 挂起问儿子位置）——**同形态两态，方差签名**。

**机制（已定位到行）**：

1. 确定性接送 hint 只锚**两种整句形态**（`navigation/manifest.yaml:87-101`）：裸接送句（后面不能带任何东西）与「接送+顺路词+停靠类目」句。「接爸妈**去吃饭**」「接孩子**后去万象城**」「先去接我妈**，再找家川菜馆**」都不在锚内（注释明写「带后续目的地的复合句均不命中」——**当时是刻意的**，把复合句留给了 LLM）⇒ 全靠 planner，长上下文（万象城/上海外滩焦点在场）时 LLM 被焦点带跑。
2. T55 的劫持链：T53/T54 把「万象城」「上海外滩」写进了焦点/历史，T55 planner 读到后把「先去接我妈」丢了、抓住「川菜馆」+旧「万象城」组合出「万象城附近的川菜」并反问城市——`_explicit_positive_pickup_person`（`navigation/src/agent.py:218-226`）这类接人判定都在 **navigation agent 内部**，路由不到 navigation 就永远不执行。
3. info T54 是另一族：整句是**约束陈述**（「如果找不到她的地点就直接问我，不要猜另一个城市」），没有任何本轮动作请求，planner 却产出 `navigation.reroute` 加了个咖啡途经点。`actionability.py` 对它恒判 EXECUTE（疑问/谓词标记判据，`actionability.py:190-195`），且 shadow 不进主链——**REJECT 声明了但 v1 不产出**的成本第一次在真栈兑现成误执行。

**修法方向**：

- **A（必做）**：接送判定前移到编排层做**分解**而不是整句锚定：确定性识别「接送分句」（`(接|送)+亲属词`，正向前缀判据复用 `_POSITIVE_PICKUP_PREFIX_RE`——**从 navigation agent 下沉共享**，别抄第二份），命中即把接送分句剥出来走 person-pickup 链（问位置/记忆解析），剩余分句按 C5 的拆句继续。这样「接X + 任意后续」都先保住接人那一半，复合句的另一半交给正常规划。落法仍是声明式：route_hints 的 `pattern` 支持「分句级锚定」是机制扩展（route_hint engine 加 `scope: clause`），不是往 planning.py 写死。
- **B（必做，焦点让路）**：本轮原话含接送分句时，`context` 渲染进 prompt 的**候选集/购物焦点降权或不渲染**（接人优先于旧商场/商户焦点——「值得跨轮留住」与「这轮该不该注入」是两个问题，§9.28 已有同款分离先例）。
- **C（必做，约束陈述）**：给 actionability 的 CLARIFY/EJECT 面补「条件式约束陈述」形态（「如果…就…」「找不到就问我」+ 否定尾），**先 shadow 观测**（B6 铁律不动摇），同时用低成本止血：这类句式在 planner prompt 的意图拆分段落加一条「条件式指示不产生本轮动作步」——prompt 层先挡，shadow 数据攒够了再谈确定性接管（canary 需泓舟拍板，B6 §5 边界不变）。
- **D（建议）**：T47 里 nearby 回答还带着一串「记得您提到过家人行动不太方便…已按周边停车便利度排序」的记忆装饰——接人没接、个性化倒很热闹，修 A 后自然消失，不单独动。

**验收**：person-pickup 迷你集扩四句（T47/T53/T55/T54 原句），`--repeat 3`：接送分句 100% 走 person-location 问询或记忆解析、约束陈述句零动作；对照组 T48/T52 原句保持绿；「不是去接孩子，直接回家」负向句不触发（正向前缀判据既有测试保持）。

**影响面**：route_hint engine 的 clause 级扩展是机制变更（R2.1 的机制化路线内——扩声明式引擎本身合法，硬编码某个 Agent 才违规）；B 动 context 渲染优先级。

---

### C7 · trip：modify 无不变量、status 无按天读、接地无城市锚（P1-09 + adv T48 接地）

**覆盖**：info T17（「第二天有哪些安排」→ 答游标进度）、T18（「不要把珠海排到广州前面」→ 3 天变 4 天 + 跨城混排 + 待确认）、adv T48（「接孩子后去万象城」→ 杭州万象城 1 天行程）。

**机制（已定位到行）**：

1. **modify 的否定式顺序约束没人解析**：`_REMOVE_RE/_ADD_RE/_replace` 三张词表都不含「排在…前面/顺序/先…再…」（`trip_planner/src/agent.py:89-90/507`，逐条实测 None）⇒ 必然落路径③**整程重规划**（`agent.py:399-401`）。路径③只传 6 个位置参数，`cities/theme/must_visit` **全丢**——多城逐城建池退化成把「深圳、广州、珠海 景点」当一个高德关键词搜（`pipeline.py:56-58`），跨城混排的池子就是第 3 天「深圳华侨城+广州永庆坊」的来源；多城 propose 的保序指令也只写在多城分支里（`pipeline.py:440-446`），路径③永远走不到。
2. **days 无不变量，反被 solve 反写**：单日超 480 分钟上限时 `solve` 新建整天并 `trip.days = len(itinerary)`（`pipeline.py:715-731`）——跨城混排把 drive_min 撑爆 ⇒ 3 天「合法地」变 4 天。`_modify` 没有任何新旧 diff/无变化短路（唯二的 no-op 短路在 reschedule 与雨天分支）。
3. **status 读不了天**：`_status` 不读 raw_text 不读槽（manifest `trip.status` slots 为空），只报扁平游标（`agent.py:630-654`）；「第N天」解析器 `_modify_day/_find_by_day_ordinal` 就在同文件（:764/:722），`_status` 一个没调。卡片里 `itinerary[].day_index` 结构化数据齐全、服务端零问答消费方（candidate_query 刻意不劫持行程内部序数——那个边界是对的，缺的是 trip 自己的读路径）。
4. **接地无城市锚**：`build_poi_pool` 四个调用点 `near` 恒 None（`agent.py:171/174/198/208`，注释明写是设计：「靠关键词『{dest} 景点』定位」）⇒ `place_text` 全国序，「万象城 美食」命中杭州；唯一校验 `name_matches` 是子串包含，「万象城」⊂「杭州万象城店」直接放行。navigation 的 R1 那套（区县级 150km 可信闸、去偏置重搜、类目复核）**零复用**——「同一件事只许一份实现」在接地上的欠账。
5. **「接孩子后去万象城」落 trip.plan 的吸引子**：manifest 描述含「先去A再去B」「带娃」、examples 有唯一一条不带天数的「周末带娃去近郊玩」、无排除子句（`trip_planner/manifest.yaml:11-25`）；days 缺失不追问（`_norm_days("")=0`，天数交 LLM 自由决定）；navigation 的接送 hint 契约测试**明确断言这句不命中**（设计上留给 LLM 的地界）。归 C6 的 A 修分解后，这条句子根本不该到 trip.plan——本卡只修「万一到了也不该开去杭州」。

**修法方向**：

- **A（必做，接地城市锚）**：trip-planner 建池与逐点接地挂**城市域约束**：多城行程逐城池已有城市名可用；单目的地（「万象城」这类裸 POI 名）时以**当前城市**为锚先搜（`place_around` near=当前位置 或 city 参数），searchless 才放宽到全国并**必须过一道跨城披露**（「按杭州的万象城规划的，对吗？」——NEED_SLOT 确认而不是直接排行程）。优先评估**复用 navigation 的 R1 组件**（`_find_destination` 的救济链已经打磨过三轮）而不是 trip 自己再写一份——落点判据同 runtime 收敛那条：两份接地实现迟早有一份是错的。
- **B（必做，modify 不变量）**：`_modify` 加「未提及维度守恒」闸：修改文本里没提天数 ⇒ 重规划结果 days 必须等于原 days，不等则**回炉一次**（把「保持 N 天」显式塞进重规划 prompt）仍不等则如实说「按您的要求调整会放不下，是否接受变成 M 天」进确认——**把静默扩天变成显式选择**。路径③补传 `cities/theme/must_visit`（丢上下文是纯 bug，一行参数的事）。
- **C（必做，no-op 短路）**：`_modify` 在结构化编辑与重规划**之前**加「约束已满足」检查：否定式顺序约束（「不要把A排到B前面」）先解析成序对（A,B），对照现行程 `cities` 序——已满足 ⇒ 直答「现在就是广州在珠海前面，没动您的方案」零重规划零确认。解析器判据零领域词（城市名从 trip.cities 取，不写死）。
- **D（必做，按天读）**：`trip.status` 契约加 `day` 槽（或新增只读 `trip.day_detail`），`_status` 复用 `_find_by_day_ordinal` 按天渲染 stops——**读侧缺席，写侧的结构化数据早就够了**（Q7 那条「缺的只是读侧」的同款）。
- **E（建议）**：trip.plan 描述补排除子句（「单点当天出行/接送不归本条」），与 navigation 的判别化描述对齐——修落域默认产物是描述与范例，不是 hint。

**验收**：INF-TRIP 组 `--repeat 3`：T17 按天列出第二天三站、T18 零动作零确认直答已满足、修改后天数守恒；接地：「接孩子后去万象城」即便落到 trip 也不得产出非当前城市的行程（新增跨城披露分支单测）；`test_pipeline.py` 补路径③参数保全 + days 守恒用例（现在**零测试覆盖路径③**——D 组调查确认）。

**影响面**：trip-planner 单 Agent 为主；A 若复用 navigation R1 组件要评估下沉 `agents/_sdk`（两个 Agent 共用）——镜像依赖闭包判据（两者都 COPY agents/，可行）。

---

### C8 · 显式出发地：reroute 契约缺 origin 维（P1-08）

**覆盖**：family T22（「从深圳欢乐海岸出发去世界之窗」→ reroute，卡片 `origin:"当前位置"`）。前一轮 T21 的 chitchat 编造归 C11。

**机制（已定位到行）**：

1. `origin` 槽 2026-08-20（Q8 第 8 步）加在了 `navigate_to` 与 `estimate` 上（manifest.yaml:30/43），**`reroute` 没有**（:52，槽位只有 remove_waypoint/add_waypoint/destination/route_pref）；agent 侧 `_reroute` 起点硬编码当前位置——话术字面量 `"当前路线：当前位置 → "`（agent.py:1655-1656）、卡片字面量 `"origin": "当前位置"`（:1660）、算路取 meta GPS（:1640-1648）。
2. planner 选 reroute 没错：活动路线在场（T21 之前的导航轮建立了 route session），manifest 描述明确「改目的地归 reroute」（:46-51）+ 焦点渲染「当前正在导航」（`context.py:523-541`）。**错的是这个 intent 少一维**——用户在导航中说「从X出发去Y」是要「换起点重算」，契约表达不了。
3. `navigate_to` 的 origin 链路是通的且有全套测试（`test_estimate.py:87` 用的就是 T22 原句）——但那些测试的 context 都没有活动路线，**跑不到 T22 的真实条件**（语料 `tu.nav.go-with-origin-not-estimate` 的 forbidden 列表也没含 reroute，C 组调查确认）。
4. 两处过期判据注释（重判 3）：`runtime/slot_fidelity.py:44-50`「manifest 根本没有 origin 槽」、探针 `probe_qa_regression.py` SL4 的 why 字段——事实已变，注释没改。

**修法方向**：

- **A（必做）**：`reroute` 契约补 `origin` 槽（manifest + 描述句「用户明说从X出发时填 origin」），`_reroute` 消费它：解析失败 NEED_SLOT 诚实问（复用 `_resolve_point` 的「绝不悄悄回落当前位置」语义，agent.py:331-340），成功则起点/话术/卡片三处一起换（同 `_route_plan_to` 的三处一起换先例，:1329 注释里点名的覆盖面账顺手补上）。
- **B（必做）**：语料 `tu.nav.go-with-origin-not-estimate` 补一个 **active_route 在场**的变体（context 带活动路线，期望 reroute+origin 或 navigate_to+origin 二选一、forbidden「卡片 origin=当前位置」）；探针 SL4 加 `card_text_not` 已有，把 why 字段的过期定性改掉。
- **C（必做）**：改掉 `slot_fidelity.py:44-50` 的过期段——注意**只改注释里的事实陈述，不动「地点不回查」的判据本身**（出发地修在能力契约层，回查层维持只管时间维的裁定）。

**验收**：`test_reroute.py` 新增 origin 消费 + 解析失败诚实问两条；真栈 SL3/SL4 组 `--repeat 3` 卡片 `origin` 字段=欢乐海岸坐标名；对照「换条避堵的路」不带 origin 仍走当前位置。

**影响面**：navigation 单 Agent + manifest；零编排改动。

---

### C9 · 天气城市漂移：mock 位置数据 + 唯一残留回退 + 接力清零（P1-06 + N6）

**覆盖**：info T4（预警答上海）、T5（洗车指数答上海）、T3/T4/T5 缺 `_prov`、话术「：；」「：，」空标点、T3 卡 `update_time` 是 64 位 hex。

**机制（已定位到行）**：

1. **上海的唯一来源是 mock 数据**：`memory/store.py:21` `"vehicle.location": {"lat": 31.23, "lng": 121.47, "city": "上海", "road": "延安高架"}`。road-safety `_weather_alert` 在 city 槽为空时 `ctx.fetch("vehicle.location")` 回退（`road_safety/src/agent.py:358-383`）——**全仓唯一**还留着这条回退的天气路径（info 侧 `agent.py:106` 注释明写「不再使用 vehicle.location 的 mock 默认值」，navigation 注释点名拒绝「绝不回退编造 上海」）。拿到的还是整串 JSON 字符串，靠 `normalize_city_slot` 恰好剥出「上海」。
2. **焦点城市没顶上**是接力条件的缝：`context.py:1330-1333` 的粘性接力要求 `focus.last_intent not in WEATHER_CONTEXT_INTENTS`——即「本轮是天气域但槽为空」时**不接力、last_city 清零**（T2「明天呢」这类槽空续问轮就把深圳丢了）；`extract_focus` 只在**槽非空**时记 city（:1089-1092）。到 T4 时 `last_city` 已空，`_apply_focus_meta` 无城可注 → agent 回退 mock。T5 的上海则是 LLM 从 T4 的答案文本里学来的（污染自我延续）。
3. **`_prov` 缺章**：同一个 `weather.py` 里 5 个 handler，2 个盖章（weather/forecast）3 个漏（`weather_alerts` :378 / `life_indices` :404 / `air_quality` :429）——恰好是 QA 抓到的三张。契约 §9.3 的必带清单也没登记这三个卡型（契约与实现一起漏）。
4. **N6**：`qweather.py:284` `metadata.get("updateTime") or metadata.get("tag")`——新版空气质量端点 metadata 只有 `tag`（厂商署名摘要），or 永远落到 hex。
5. **空标点**：`weather.py:371-374`（alerts）与 :397-400（indices）把带「：」的前缀项放进 join 列表——同文件 `_forecast`（:338-342）已修过同款并留了注释，这两处是漏网。
6. 顺手账：`_indices`/`_air_quality` 用裸 `city`（未过 `_display_city`），GPS 路径下会把「114.06,22.54」坐标串念进话术（E 组调查发现，本轮没触发但形态在）。

**修法方向**：

- **A（必做）**：road-safety `_weather_alert` 删掉 `vehicle.location` 回退，对齐 info 的语义：无 city → 用 meta 的 GPS 坐标（`current_location_from_meta`），再没有 → NEED_SLOT 诚实问城市。**mock 位置数据留在 memory/store 里没问题（别的测试在用），但生产路径一个消费方都不许剩**——修完全仓 grep `vehicle.location` 核销消费方清单。
- **B（必做）**：接力条件修正：本轮是天气域、槽为空且没有中断时，`last_city` **应当接力**（把 :1330-1333 的第二个条件从「非天气域」改成「非天气域，或天气域但本轮未产出新城」——具体判据写成「本轮 extract 没记到新 city 就沿用上一轮」，并保住既有的相邻性中断清零逻辑，`test_weather_after_unrelated_turn_does_not_inherit_stale_city` 必须仍绿）。
- **C（必做）**：三张卡补 `attach()` 盖章 + §9.3 必带清单补登 `air_quality/weather_alerts/life_indices`；N6 改成 `updateTime` 缺失时**不落 update_time 字段**（署名摘要不是时间，宁缺勿假——同「认不出返回空」判据）。
- **D（必做）**：两处 join 改成前缀不进 parts（照 `_forecast` :338-342 的既有修法）；`_indices`/`_air_quality` 过 `_display_city`。
- **E（探针配套）**：INF-WEATHER 组补「答案城市 ∈ {原话城市, 焦点城市}」的判据（本轮探针只查了 `_prov`，城市漂移全靠人工漏检兜出来，归 C16）。

**验收**：INF-WEATHER 五轮 `--repeat 3` 全绿且城市恒深圳；`test_engine_focus.py` 补「天气续问轮（槽空）不丢城市」用例；`test_agent.py`（road-safety）补「无 city + 无 GPS → NEED_SLOT 而非上海」（现有 `test_weather_alert_needs_city` 用空 context，恰好没测回退分支——E 组调查确认）；三卡 `_prov` 断言。

**影响面**：road-safety / info / context 接力条件。B 动焦点接力是共享面，`test_context.py`/`test_engine_focus.py` 全族回归。

---

### C10 · 提醒域：序数参照系分裂 + 按标题取消裸奔 + 无任务性准入 + 过期存量无治理（P1-07 + P2-17 落域半边）

**覆盖**：family T17（按标题取消落 chitchat）、T59（取消到「刚才那个提醒现在几点」）、T18/T60（3 条 09:00 重复 + 假提醒标题 + 80 条过期）、info T37（「改了哪一条」→整表）、family T34（「第二个先取消，其他继续」→ shop.preview_discard「处理失败」）。

**机制（已定位到行）**：

1. **「第N条」有三个互不相同的参照系**（T59 的真根因，F 组调查的决定性事实）：`_refresh_active` 无参形式取 `list_split(from_ts=0)` **整表最老 10 条**（80 条过期垃圾打头，`reminder/src/agent.py:846-852`），而 `_list` 写的是用户刚看到的未来项、`_clarify_multi` 写的是澄清候选——**取消成功后（`agent.py:791` 无参刷新）参照系被静默换成过期垃圾表**。T59 planner 产出 index=1 ⇒ 命中「刚才那个提醒现在几点」（上一轮 QA 留下的垃圾）并取消。`store.get` 还不过滤 status（可选中 done/cancelled）。
2. **按标题取消**是纯子串 LIKE（`store.py:296-306`），planner 转述宽词即误伤；且 cancel 的 route_hint 已于 2026-07-29 退役、examples 不进 prompt（`planning.py:117-125` 只渲染 intent/slots/description）⇒ T17 这类「重复同句取消剩余项」的长上下文形态 100% 靠 LLM，落到 chitchat 反问。既有同形用例 `test_batch_reminder_cleanup_crosses_agent_clarify_and_engine_resume` 跑绿的前提是**空库**——80 条存量下的参照系重播种不在覆盖面。
3. **没有「这句话是不是一件待办」的准入**：写入闸只判 `fire_at>0`（`store.py:158-161`）+ 否定守卫两条；问句「刚才那个提醒现在几点」与事实陈述「用户计划2026年国庆…4天行程」作 title 一路放行。Q11 卡的 ③（任务性判据）当时只落了 offer 侧（`memory/offer_admission.py` 管主动建议），**create 侧没有对应物**。
4. **过期无治理**：状态机 `pending→fired` 到头，fired 永远算 ACTIVE、永远参与 find_by_title 与序数参照；hygiene 脚本只召回 `fire_at<=0`；探针无 reminder 清理、隔离只靠标题里的 run 号。80 条存量是多轮探针的沉积。
5. **T37 读路径缺席**：reminder 不产 actions（账本恒空）、audit 闸 `_NOT_AUDIT_RE` 显式排除含「提醒」的问句（`audit.py:37-46`）、`_apply_update` 成功后立刻清掉唯一记着「改了哪条」的 pending 态（`agent.py:494`）——「刚才改了哪一条」在系统里没有任何可读的事实。归 C4 的账本扩维一并收（reminder 的 update/cancel/create 以动作形态入账）。
6. **P2-17 落域半边**：提醒批的「部分取消」（第二个先取消其他继续）没有能力承载——planner 就近挑了 `shop.preview_discard`（描述里有「取消」+「预览」）。能力缺席→就近误执行的标准形态（Q8 族）。

**修法方向**：

- **A（必做）**：统一序数参照系——`_refresh_active` 的无参形式改成**与 `_list` 同一口径**（未来项优先的那份），或干脆删无参形式、所有调用点显式传「用户刚看到的列表」；`store.get` 按 ACTIVE 过滤。**一个「第N条」只许指向用户最后一眼看到的东西**（候选集下发面 §9.28 的同一判据）。
- **B（必做）**：按标题取消加**精确度阶梯**：先逐字相等，再子串唯一命中，多命中澄清（已有），**子串命中也要在话术里复述全名**（已做）+ 探针补「复述的 title 必须与用户点名的 title 对得上」判据（C16）。cancel 描述句补「按标题取消时 title 填用户点名的原文」。
- **C（必做）**：create 侧任务性准入：title 是问句形（疑问词/「几点」尾）或第三人称事实陈述形（「用户计划/已确认」开头）⇒ 拒建并诚实说（判据取**形态**不取关键词，与 `offer_admission` 同族但独立实现面——那边判的是「值不值得 offer」，这边判的是「这是不是一件事」）。
- **D（必做，数据治理）✅ 泓舟 2026-08-27 拍板放行**：过期条目加终态流转，形态定为**复用
  `cancelled` + `extra.reason="expired_sweep"`**（零 schema/枚举变更——status 枚举扩散到
  全部查询面的影响大于收益，`extra` 里的 reason 足够区分「用户取消」与「系统沉积清扫」；
  这同时绕开了 schema 红线）；fired 超过 N 天（建议 N=7）由 hygiene 脚本「过期沉积」召回族
  批量置之，dry-run 先行。**存量 90 条已于 2026-08-27 清完**（泓舟点名「全清」后单事务
  按 ID 执行：UPDATE 90、u1 ACTIVE 归零、审计标记 `extra.batch="20260827-hongzhou-approved-90"`；
  清单与执行证据在 `.artifacts/reminder-cleanup-inventory-20260827.txt` 与
  `sweep-apply-20260827.result.txt`）。**接手者剩两件**：hygiene 脚本的「过期沉积」召回族
  机制化（用同一个 `extra.reason` 约定，别再发明第二个标记）；
  探针跑批结束补 reminder 清理段（按 run 号标题匹配自己建的、逐条取消并验证计数回落）。
- **E（必做）**：`reminder.create_batch`/`create` 补跨轮幂等：同 owner + 同 title + 同 fire_at 的 pending 已存在 ⇒ 收编不新建（现在三条 09:00 重复是设计结果——「同轮不收编」的裁定保留，跨轮重复该挡）。
- **F（建议）**：提醒批部分取消能力：`reminder.cancel` 已有 index 槽，缺的是「取消第N个 + 其余继续」的复合形态进 planner 的通道——范例补一条（「第二个先取消，其他继续」→ reminder.cancel(index=2)），不新增 intent。

**验收**：SL1 组 `--repeat 3` 四轮全绿且**取消的 title 与点名一致**；「第二个先取消」落 reminder.cancel(index=2) 且其余项保留；准入：两类垃圾 title 拒建（正 2/硬负 2：真任务「参加评审会」「交周报」照建）；`test_agent.py` 补「80 条过期存量在库时序数仍指向用户可见列表」用例（把 F 组指出的空库前提补掉）。

**影响面**：reminder agent + store（D 的状态流转动 schema 或语义，**按红线先问泓舟**——status 枚举扩展属 schema 变更类）；探针清理段。

---

### C11 · chitchat 执行性编造 + 聚合器两处出口缺陷（N4 + P1-10 编造半边 + P2-17 话术半边）

**覆盖**：family T21（编「已重新计算路线…1.6公里」）、info T48（编「为您规划路线…请确认是否发起导航？」）、info T41（编「东方财富 19:23」）、info T43（编自我纠错「我把上证指数当成沪深300报给你了」——T42 真卡就是沪深300，没错过）、family T34（「抱歉，处理失败。」吞掉桥的完整话术）。

**机制（已定位到行）**：

1. **防编造 prompt 的枚举面窄**（`agents/chitchat/src/agent.py:194-200`）：禁语只有「已为您找到/已选定/已下单」+「请确认」**开头**，能力枚举只有「查询、下单、订座、支付」——「重新计算路线/规划路线/加途经点」全在枚举外；T48 的「请确认是否发起导航？」在句尾不在句首。`:190` 的「绝不编造」限定在**时效性事实**，来源/执行史/自我纠错都不覆盖。且「你没有查询能力」这句对来源追问**方向还反了**——上一轮系统明明查了。
2. **单步聚合零校验**（`aggregator.py:172-188`）：单步结果话术不过 LLM 不过任何判定直接透传；诚实约束只写在多步聚合的 system prompt 里。全仓没有「speech 含执行完成词但 actions 为空 ⇒ 拦/标」的校验器（planner 注释里记过同形态动机，修法只覆盖了车控省略族）。
3. **单步 FAILED 出口吞信息**（`aggregator.py:172-178`）：`r.error` 恒空（`AgentResult` 无 error 字段、`_to_result` 不设）⇒ `_ERROR_FRIENDLY.get("", "处理失败")` 字面量；`follow_up/ui_card` 不透传。桥那句「未能确认本次订单预览清理完成，请稍后再试」写了等于没写。另 `preview_discard` 用 FAILED 表达业务性拒绝本身违反 §9.5「话术型拒绝用 OK」。
4. chitchat 只有 4 轮纯文本历史（C4 已述）——它编造的材料学自污染后的对话文本，越编越自洽。

**修法方向**：

- **A（必做）**：chitchat 防编造条款改成**形态判据**而不是禁语清单：「你没有任何执行/检索/规划能力；**凡是描述『系统已经做了什么/正在做什么/将要做什么』的句子都不许说**，包括路线、订单、提醒、来源；用户问这类事实时引导明确指令或如实说不知道」。禁语清单式 prompt 已被两轮 QA 各绕过一次（demo-mkemhn 那批堵了交易话术、这批从导航钻出来）——**换成类别否定**。同时补一条「不得虚构自己此前犯过的错误来道歉」（T43 形态）。prompt 断言测试从「含『绝不编造』」升级为逐条款存在性断言。
- **B（必做）**：单步 FAILED 出口改为**透传 Agent 话术**：`r.speech` 非空时用它（Agent 的失败话术就是给用户看的），仅在空话术时落 `_ERROR_FRIENDLY`；`follow_up` 一并透传。同批把 `preview_discard` 的业务性拒绝改回 OK 状态（§9.5 对齐，全仓 grep FAILED+话术型拒绝一次清）。
- **C（建议，观测先行）**：聚合器出口加「执行性声明 vs actions 为空」的**观测列**（判据零领域词：句含「已为您/已经为您 + 动词」类执行完成形态且本轮 actions 为空 ⇒ obs 布尔位），两周分布出来再谈拦截——直接拦的误伤面（转述历史、复述用户的话）没量过。
- **D（不做）**：给 chitchat 喂执行账本让它「如实转述」——账本读出口走 C4 的确定性短路，**别把事实交回 LLM 转述**（Q6 四版尺子的教训）。

**验收**：chitchat prompt 条款断言测试；T21/T48/T43 三句真栈 `--repeat 3` 零执行性声明（探针判据见 C16）；family T34 复跑话术=桥原话（含恢复指引）；`test_aggregator.py` 补单步 FAILED 透传用例。

**影响面**：chitchat prompt + aggregator 两个出口 + mcp-bridge 状态语义（B 里 preview_discard 改 OK 要同步它的探针期望）。

---

### C12 · 会话内偏好不进当轮判据，话术反着说（P2-14）

**覆盖**：info T28-T32（「不吃辣不排队」→ 推川菜/酸菜鱼 + 「按您的口味优先川菜」+ 「您这轮没说口味」）。

**机制（已定位到行）**：

1. **判据用错变量**：`nearby/src/agent.py:499-506`——决定「要不要按记忆口味改写检索词」时，挡板条件是 `turn_no_spicy`（**本轮原话**的不辣正则），而不是记忆里的 `taste["no_spicy"]`。当记忆同时含「爱吃川菜」与「不吃辣」（多轮 QA 沉积的画像）而本轮原话没说辣时：`:505` 拿川菜当检索词、`:594` 又按记忆 no_spicy 把川菜排后、`:951` 再拼「记得您说过不吃辣」——**三句自相矛盾话术是同一轮确定性拼出来的**。
2. **T28 的当轮陈述到不了 T29**：nearby 只读 `intent.raw_text`（当轮），不读历史；session 级偏好存储不存在（prefs 白名单无口味键、Focus 无此格）；唯一通道是异步记忆抽取绕 PG 一圈，T29 相隔几秒未必落库。
3. **T32「您这轮没说口味」**：chitchat 历史窗只有 4 turns（≈2 个 exchange），T28 已滑出窗口——模型**看不见** T28，不是撒谎是失忆。
4. 「按您的口味优先川菜」的旧画像来自多轮 QA 的沉积（`taste.` 谓词族 recall），画像本身合法——问题全在消费面。

**修法方向**：

- **A（必做）**：修 `:499-506` 的判据：`turn_no_spicy or taste.get("no_spicy")` 都应挡住「把辣系菜当检索词」——记忆里两条冲突偏好并存时，**限制性偏好赢过扩张性偏好**（不吃辣 > 爱吃川菜），并把话术改成「您说过不吃辣，这次没按平时的川菜找」。
- **B（必做）**：会话内偏好通道——在 Focus 加 `session_constraints`（当轮陈述的偏好/忌口，确定性正则抽取：`_NO_SPICY_RE` 一族本来就在，抽取点挪到编排层入焦点、TTL 同 focus），nearby/merchant 消费时**前景赢过记忆**（§4.3「记忆是背景，当轮说的是前景」的机制化——那条判据 2026-08-15 就写下了，一直没有载体）。
- **C（建议）**：chitchat 历史窗从 4 提到 8-12（成本可控，`_build_messages` 一处），或元问题（「我这轮说过什么」）走 C4 的确定性读出口。
- **D（探针配套）**：INF-PREFERENCE 组补判据：陈述过忌口后，推荐列表首位不得是该忌口系（形态判据：卡片 items[0].tags/category 与忌口词族的对照）；「按您的口味」话术出现时账面必须真有对应偏好被消费（归 C16）。

**验收**：INF-PREFERENCE 五轮 `--repeat 3`；`test_agent.py`（nearby）补「记忆同时含爱川菜+不吃辣 ⇒ 不拿川菜当检索词」（现有 `test_current_turn_no_spicy_overrides_remembered_cuisine` 只盖了当轮原话那半——F 组调查确认）。

**影响面**：nearby 一处判据 + context 焦点新键（B 是共享面新契约，登 §9.x）。

---

### C13 · 到达时限：裸时刻双候选皆过时滚成次日凌晨 + 荒谬余量无闸（P1-15）

**覆盖**：family T8（「5点我要到学校」→ 按次日 05:00 判「早593分钟」，speech 自己都说「应该是把5点当成凌晨5点了」）。

**机制（已定位到行）**：

1. `agents/_sdk/timewindow.py:68-69`：裸 1-11 点无段位按「未来最近一次」消歧——**两个候选（05:00/17:00）都已过时** `_at(1, hour)` 滚到**次日同数字小时**=凌晨 05:00。18:53 说「5点到学校」，语义上是「今天 17 点（已经错过）」，正确输出是「已过 17 点、赶不上」，而不是把时限静默改到明天凌晨。
2. `agents/navigation/src/agent.py:309-310` `_deadline_note`：`margin >= 5` 一律「早约{margin}分钟」，**无荒谬值上限**——593 分钟照播，还引发聚合 LLM 现场自我吐槽。

**修法方向**：

- **A（必做）**：`parse_clock_time` 对「裸时刻 + 双候选皆过时」的分支返回**带标记的过时解**（如返回今天 17:00 并由返回形态标注 `elapsed=True`，或返回 None 让上层走「时限已过」话术）——**别让滚日改变小时语义**（滚到明天该是 17:00 还是 5 点本身无解，说明这个分支就不该猜）。注意它是共享实现（navigation/nearby/reminder 多消费方），改语义要跑 `test_timewindow/test_cntime/test_timeparse` 全族并逐消费方核对（reminder 的「明早5点提醒我」滚日是**对的**——它带「明早」段位词，不在本分支；改动只收窄「裸时刻双过时」一种形态）。
- **B（必做）**：`_deadline_note` 加荒谬余量闸：`margin` 超过某阈值（如 6h）⇒ 不播「早约N分钟」，改播「您说的{原话时刻}按今天算已经过了/要到明天了，请确认」——**把解析疑点交还用户，不把它包装成精确数字**。
- **C（探针配套）**：PU5 判据补「late_min/margin 的绝对值 ≤ 合理界」与「时限解析结果与原话时刻同数字时必须同半天」（归 C16）。

**验收**：`test_timewindow.py` 新增双过时分支用例（14:00 说 5 点=17:00 不变；20:00 说 5 点=标记过时；20:00 说「明早5点」=次日 05:00 不变）；真栈 T8 原句复跑话术含「已过/来不及」而非「早593分钟」。

**影响面**：`agents/_sdk/timewindow.py` 是共享唯一实现——改它是本批风险最集中的一处，消费方逐个核对（navigation G1 / nearby 用餐窗 / 事件时刻）。

---

### C14 · charging.plan 复合否定句落域（P1-11 落域半边）

**覆盖**：info T48（「规划去广州路上的补能，但先不要启动导航」→ chitchat 编造，期望 charging.plan）。

**机制（已定位到行）**：

1. `charging.plan` 能力**存在且语义就是沿途分段补能**（`charging_planner/manifest.yaml:26`），实现本来就不发导航动作（`agent.py:356-358` 注释明写 advisory）——但这个「只规划不导航」的性质**没写进 manifest 描述**，planner 无从知道「先不要启动导航」与 charging.plan 相容。
2. 否定 policy 的既有 golden 把「先别导航去机场」整句钉给 chitchat（`negation_quotation.yaml:136-148`）——「肯定与否定并存只保留肯定部分」的条款在，但这个句形（肯定的规划诉求 + 否定的执行方式）没有一条范例/语料覆盖；旧 hint（已退役）也要求「怎么充电」词形，本句即使 hint 在场也不命中。
3. 「去广州路上的补能」被 LLM 读成目的地「广州路补能」是次生症状——落对域后 charging.plan 的 destination 槽就是「广州」。

**修法方向**：

- **A（必做）**：`skills/exemplars/charging.yaml` 补范例：「规划去广州路上的补能，但先不要启动导航」→ charging.plan(destination=广州)、note 写明「否定的是启动导航，不是规划本身」；对抗语料补正 2/硬负 2（硬负：「先别规划充电」→ 零步；「附近的充电站」→ charging.find）——L0 语料上界要占额度，按 `suites.yaml` 纪律先写占用理由。
- **B（必做）**：manifest 描述补「本能力只出建议卡，不发起导航——『先不要导航』类限定与本能力相容」。
- **C（编造半边归 C11）**。

**验收**：INF-CHARGING 组 `--repeat 3` T48 落 charging.plan 且零 navigate 动作；对照「先别规划充电」零步。加语料后跑 `scripts/check_intent_gate.py --strict` + 双向对照（工作规则：加了知识拿对照证伪）。

**影响面**：纯声明式（范例 + 描述 + 语料），零代码。

---

### C15 · provenance 契约裁决：探针口径与 §9.3 互相矛盾（P1-12）

**覆盖**：20 个失败行——manual mock ×5、road-safety deterministic ×11、info 三卡缺章 ×4（缺章的修法在 C9，本卡裁决口径）。

**机制（已定位到行）**：

1. **`deterministic` 不是 §9.3 的合法 mode**（词表只有 real/cached/degraded/mock），road-safety 两处显式打它（`road_safety/src/agent.py:292-294/325-328`）——**实现先于契约发明了一个值**；而它想表达的东西（「本回答由确定性判据产生、未经模型、未经外部数据」）契约里没有承载。
2. **探针把 mock 一律判红**（`probe_qa_long_sessions.py:441-444` 无条件），而 §9.3 与 §9.17 明文要求 mock **如实标注即合法**（`payment_qr` mock 必须打 mock）——两把尺子方向相反。真栈里出现 mock（manual-rag 无真手册）是 **PoC 数据现实**，探针的立场（真栈不该有 mock）与契约的立场（有 mock 必须承认）说的是两件事：前者是**部署形态期望**，后者是**诚实契约**。
3. `safety_advice` 卡型在 §9.3 既不在必带清单也不在豁免清单——契约空白，探针只好按「出现了 `_prov` 就查 mode 合法性」执行（mode 检查对所有卡无条件生效）。

**✅ 裁决已下（泓舟 2026-08-27 拍板：按下列建议 ①+②+③ 全部采纳；「真栈出现如实标注的
mock」= 记 WARN 不判 fail，冒充 real 才是 fail）。契约侧已同批落 `docs/conventions.md`
§9.3（`deterministic` 收编 + mock 两档 QA 口径 + 三卡型补登），探针侧词表/卡型表随
C16 批实施。** 原裁决建议原文保留如下：

- **①（推荐）`deterministic` 收进 §9.3 合法 mode**：它与 degraded/mock 正交（不是外部数据的降级，是**内部确定性产物的自我声明**），road-safety 的用法语义完全正当——「按会话未解除告警给出，未经模型生成」正是可审计性想要的。同时把 `safety_advice` 登记为「内部确定性卡，_prov 可选、出现则 mode 必为 deterministic」。探针词表加 `deterministic`。**11 行失败就地转绿且不是放宽**——是契约补登一个实现早已需要的值。
- **② mock 的探针语义拆两档**：`mode=mock` 不再一刀判红，改判「**该卡型是否声明了 mock 可接受**」——manual-rag 在真手册接入前 mock 是已知形态（QA 报告里也承认「PoC 限制」），探针对它降为 WARN 级（计数不判 fail）；但 mock **冒充** real（无 `_prov` 或标错）维持红。判据落在探针的卡型×模式期望表——**给探针建卡型清单**，正好把 §9.3 的必带清单机械化成探针判据（一份声明两个消费方）。
- **③ manual-rag 的长期解**：真车型手册接入前，零命中话术已经诚实（「手册里没有查到」+安全前缀）；把 mock 语料的 `source` 展示为「演示语料」在 UI 上已有（§9.3 重判 2 那次修的）。**不引入新工作，等真手册。**
- ~~裁决需要泓舟确认的一句话：「真栈出现如实标注的 mock，算不算 QA 失败」~~
  **✅ 已答（2026-08-27）：不算 fail、记 WARN；未标注或标错 mode 才是 fail。**

**验收**：裁决落 §9.3（改契约文档）→ 探针词表/卡型表同步 → 复跑 33 行对账中 provenance 族清零或转 WARN；`test_probe_qa_long_sessions.py` 的三条 provenance 用例随口径更新。

**影响面**：契约文档 + 探针；road-safety 代码零改动（若裁决①）。

---

### C16 · 探针判据硬化：七处漏检 + 诊断出口（贯穿卡）

**覆盖**：本轮「自动计分 282 PASS」里混着的假绿。逐处（全部已在上文各卡定位）：

1. **SF3 首轮 `expect: {}`**（`probe_qa_regression.py:435`）——「红色机油灯亮了怎么办」执行了 `warning_light.close` 照样绿。同 case 后面几轮早就学过「`no_actions` 是硬要求」（:444-449 注释），首轮漏了。→ 补 `no_actions: True` + `intent_any: [manual.query, safety.driving_advice, safety.driver_state]`。
2. **T21 编造执行声明**——SL3 轮 fails=[]。→ 新判据：`chitchat.talk` 轮的 speech 含执行完成形态（「已(经)?为您…/已按…规划」类**形态**词表）而 actions 为空 ⇒ 红。这是话术层判据，但它判的是**形态**（说了做没做）不是措辞，符合 §4.3 纪律。
3. **T59 取消错对象**——reminder.cancel 成功即绿，没对「取消的 title 与用户点名的 title」。→ SL1 组补 `speech_has: [用户点名的标题]`（探针知道自己点名了什么）。
4. **T30「暂不支持哦」**——SF2 轮 fails=[]。→ 补「知识问句不得以『暂不支持』收场」的形态判据（`differs_from_turn` 家族：回答里没有任何数值/建议内容）。
5. **RECOVERY 轮零判据**——「还有待确认的操作吗」答「嗯」/学校地址都绿。→ 补 `is_question or speech 含「没有待确认/还有」族 or differs_from_turn` 的最低线；C4 落地后改为断言确定性出口命中（`system.no_pending` 族 intent）。
6. **恢复轮动作-目标一致性**（N7）——`judge` 补「actions 里的对象前缀 == 该恢复命令的 state_key 对应对象」。
7. **INF-WEATHER 城市漂移**——五轮只查了 `_prov`。→ 补「答案城市 ∈ {原话城市, 会话内出现过的城市}」判据（卡片 city 字段可机读）。
8. **诊断出口拆分**（C2-D 已列）：`_settled_vehicle_state` 的两种失败分开报。

**修法方向**：以上逐条落 `probe_qa_regression.py` / `probe_qa_long_sessions.py`；每条新判据**先拿本轮 artifact 回放验证**（应红的轮回放变红、当时真绿的轮保持绿——探针自己的「注入验红 + 对照仍绿」两头验证），再进下一轮跑批。**别忘了 §4.3 的反向教训**：SF3 词表曾写窄误伤正确回答——新增 speech 类判据一律用形态词族并留放宽记录位。

**验收**：`scripts/tests/test_probe_qa_long_sessions.py` 每条判据两向用例；对本轮 artifact 干跑（回放模式）产出「修正后计分」——预期 33 fail 之外新增 ~10 行红（漏检转正），作为下一轮的基线对照。

**影响面**：纯探针；不动生产代码。

---

## 4. 实施顺序建议

> 排序判据沿用 QA 上一轮沉淀的那几条（原 AGENTS.md §4.1，2026-08-27 已归档 `docs/agents-history.md` **§72.1**）：先安全与真实性、前置排在复用它的批之前、建立形态的那步排在照它做的那几步之前、探针判据与被验对象同批落。

| 批 | 内容 | 为什么排在这里 |
|---|---|---|
| **1** | **C1**（安全告警链 A-D）+ **C2**（端侧三 bug + 探针诊断出口）+ **C16 的 1/6/8** | 本轮唯二的确定性车控误执行 + 安全域降级；C16 对应判据同批落，修完立刻有尺子验。C2 的三个规则修正都是小改动，先摘 |
| **2** | **C4**（账本扩维 + 确定性读出口族 + 重列算子）| 它是「建形态」的那一步——C10 的 T37、C11 的 D、C12 的 C 都消费它；也是覆盖症状卡最多的一张（5 张） |
| **3** | **C3**（wait_slot 反转 + slot_shape 契约）+ **C10**（提醒域 A/B/C/E/F）| 会话状态机族；C10-D（过期治理/schema）单独拎出等泓舟拍板 |
| **4** | **C6**（接人分解 + 焦点让路）+ **C5**（覆盖度观测 + 挂起不冻结兄弟步）+ **C7**（trip 三修）+ **C8**（reroute origin）| 复合句族。C5-A 的观测先行、C5-B 动执行语义要单独评审；C7-A 评估复用 R1 组件 |
| **5** | **C9**（天气/模板）+ **C12**（偏好）+ **C13**（时限）+ **C14**（charging 范例）+ **C11**（chitchat/聚合器）| 相互独立的单点修，可并行/穿插 |
| **6** | **C15**（provenance 裁决）+ **C16 其余** | 裁决要泓舟一句话；探针全量硬化后对本轮 artifact 回放出「修正后计分」，作为验收全批的对照基线 |

**三处拍板 ✅ 已于 2026-08-27 由泓舟裁定（均按本方案推荐项）**：① C15——如实标注的 mock
记 WARN 不判 fail、`deterministic` 收编 §9.3（契约已同批改，探针随 C16 落）；② C10-D——
过期流转用 `cancelled + extra.reason="expired_sweep"`（零 schema 变更），**90 条存量已按
「全清」点名当日执行完**（UPDATE 90 / u1 ACTIVE 归零，证据见 §0 与
`.artifacts/sweep-apply-20260827.result.txt`）；
③ C5-B——挂起不冻结兄弟步放行（实施时全族回归）。**本方案自此无外部阻塞项**。

**两条已立的 §4.2 账本轮销掉**：「会话级数据源/降级账本（I-033）」启动条件（第二个消费方）已满足 → 随 C4 落地销账；「多意图复合句被单域吞掉」启动条件（稳定可复现族）已满足 → 随 C5 落地销账。

## 5. 本轮沉淀的判据（供 AGENTS.md §4.3 择录）

- **登记不能是路由的副作用**。告警、焦点、账本这类「系统必须知道的事实」，写入要挂在**输入或产出的形态**上，不能挂在「恰好走了哪条路由」上——路由是有方差的，事实不能跟着抖（C1）。
- **同一症状在多 persona 下呈现三种错法，本身就是「无确定性护栏」的读数**——修方差先找该确定性的那一层，不是逐个 persona 调（C1/C6）。
- **恢复/清理链路的词表要与业务链路同源**。探针恢复命令撞上端侧词表错字才暴露 N1——反过来说明**恢复链是免费的对抗测试**，它的失败要逐键报因（C2）。
- **「读不到」与「读到了但不对」永远分开报**（C2，第二次沉淀——android-m3 那批的判据在探针上复发）。
- **自由文本槽的默认方向是错的**：「除非证明是换话题，否则当槽值」在词表竞赛里永远落后；`order_id` 的「槽值必须长得像这个槽」才是可收敛的方向（C3）。
- **每一份「这是新请求」的判据词表只许有一份实现**——`_NEW_SEARCH_RE` 与 `_is_topic_change` 各自演化正是 B1 形态在词表层的复发（C3）。
- **「第N条」只许指向用户最后一眼看到的列表**。任何后台刷新参照系的动作都是在给序数指代埋雷（C10）。
- **禁语清单式的防编造 prompt 每轮 QA 都会被绕过一次**——换类别否定（「凡描述系统做了什么的句子」），并配观测列（C11）。
- **限制性偏好赢过扩张性偏好；当轮陈述赢过记忆——且要有载体**。「前景赢过背景」写进 §4.3 一年了，没有 session_constraints 这个载体它就只是一句话（C12）。
- **裸时刻两候选皆过时，滚日不该改变小时语义**——解析器没把握的分支应该交还用户，不该给出一个精确但荒谬的数（C13）。


