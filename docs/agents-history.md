# AGENTS.md 历史流水档案（只进不出）

> 本文承接 [`AGENTS.md`](../AGENTS.md) §4 的逐批次历史记录，2026-08-02 从该文件整体迁出：
> AGENTS.md 只保留当前快照与活跃余项，**新批次收口时把完整记录追加到本文对应章节**
> （各章节内最新在前），并在 AGENTS.md §4.0 刷新摘要——不要再往 AGENTS.md 里堆流水。
> 正文中「§4.0 / 上方 / 下方」等相对指代按迁出时原文保留，指的是迁出前 AGENTS.md 的结构。
>
> **最新批次在 §23**（2026-08-10 夜，§4.2 延后索引推进：端侧能力面两个缺口 + 范例
> clarify 型 + 工具通道处置拍板）；同日六批连续记在 **§15–§23**（P1/P0 收敛 → 路径 1
> 否定 → 路径 3 落地与回退 → 三项 P2 收口 → 生产侧范例修复 → 两条新 P2 → 通道定性
> → 换档 DeepSeek 对照 → §4.2 延后索引推进）。章节号按主题排、不按时间排，
> 已有 §2/§3 被 AGENTS.md 正文引用，故新批次追加在末尾而不重排编号。
>
> 各主题的架构级结论以架构文档附录 C 版本记录（v1.2→v1.19，按主题索引）为准；
> 设计与落地记录见 `docs/design/`（索引 `docs/design/README.md`）；评审报告见 `docs/reviews/`。

## 1. 智能化升级期与数据飞轮（2026-07-24 → 2026-08-01：M0a→M4、总体验收、真机批次、Skill 层、M5）

**智能化升级 M0a→M4 全部完成**（母提案
`docs/design/2026-07-24-eva-benchmark-intelligence-upgrade.md` §6 分期，各期落地记录逐条在案）。
**M4 的两条 DoD 都已兑现**：「语音双链路可切换」由 S2S 线（P0→P3）兑现，
**「多用户记忆隔离旅程」由 P4 声纹兑现**。

**M4 S2S（2026-07-25）的两句话**：端到端语音有了承载物——闲聊/常识由语音大模型直接听直接答
（首音频 P50 609ms），而**需要执行或查实时信息的请求由模型 `escalate` 交回确定性主链**，车控
没有一条路径绕过 planner→executor→VAL→确认闸。双链路可切换（设置默认 classic，s2s 需用户显式
开——它上行原始音频，是隐私口径变化点）。子 RFC `docs/design/2026-07-25-m4-s2s-fullduplex-rfc.md`
的 **§3.5 是协议冻结基线、§11 是落地记录**（含五处设计偏差）。

**M4 P4（2026-07-26）的两句话**：**声纹**让「谁在说话」真实化——唤醒后首句边说边识别
（1.5s 即发，一次唤醒锁一次），`occupant_id` 沿既有 meta 管道贯到 memory，每个乘员的偏好各自
独立；**认不出一律回 primary**（=P4 之前的行为），**声纹绝不作鉴权因子**（源码级断言钉死）。
**视觉**让「那是什么」能问——端侧命中触发词才抓一帧，**图像永不进对话链**（proto 里只有 16 字节
frame_id）。子 RFC `docs/design/2026-07-25-m4-p4-voiceprint-vision-rfc.md` **§11 是落地记录**
（含六处被实测推翻的设计）。

**P4 落地当天的真机三修（2026-07-26，`d45289e`）**：①我引入的回归——为等声纹结果给
`onSend` 加了 150ms 软等待，把它变成异步，破坏了「用户气泡由 send **同步**接管」的不变量
（`_finalizeSend` 是先 onSend 再进 THINKING），对话窗里回答与问题错位。**修法是整个删掉
而不是缩短**：识别在说到 1.5s 时就发出、端点还要再等 800ms 静音尾，这 150ms 几乎不生效
——为一个几乎不生效的优化牺牲一条不变量是亏的。②名字只写在 `voiceprint` 表里，
**那张表除了比对没有任何消费方会读** → 补写 `identity.name` 记忆 + `occupant_name` meta。
③注册交互缺「进行中反馈」与「完成后自证」→ 每段独立可重录 + 倒计时 + **「试一试」当场验**。

**两条最值得记住的实测结论**：
- 声纹：**真正起作用的控制量是 margin 不是 threshold**（thr∈[0.45,0.70] 端到端结果完全相同）。
  同性别音色分布重叠，但被 `ambiguous` 档拦成 primary——**认错率恒 0%**。
- 视觉：**当前聊天大脑看不了图**（`qwen3.7-max` 对多模态 content 直接 400），而档位解析对不认识
  的模型是**静默回落 primary**——故视觉必须走独立 `qwen-vl` 档 + 请求级 pin，否则会静默打到
  看不了图的模型上且毫无报错。

**总体验收（2026-07-26）**：M0a→M4 全线过了一次跨阶段组合验收——七路深查（S2S×危险
动作 / 声纹×记忆×ForgetUser / MCP写×确认×T2×Verifier / Skill×submit_plan×Provider /
geofence×负荷×免打扰 / S2S×主动 / provider 部分成功）+ 测试真实性抽查。**结论：六期主体
真实落地；组合面抓出 2 个确认链 P0（补槽恢复跳过二次确认 / S2S 挡位「确认·取消」被模型
自答成假承诺）与一批隔离/降级 P1，当日修复 21 项代码 + 15 项测试**；结构性遗留（记忆写侧
说话人盲、places 无 occupant、MCP 查单/补偿入口、主动×S2S 治理器侧方案等）已立卡。
全文见 `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`（§6 修复清单 / §7 立卡）。
两条更正：AGENTS.md 此前引用的「journeys regression 15/15」是历史时点值（canonical 基线
一度陈旧 20 commit，当前口径见验收报告 §8）；「主动消息唯一会出声的是深调研」评估不属实
（朗读判据是 text&&card，提醒/场景/低电量都恒带卡）。

**M-A 余项已按负责人裁决收口（2026-07-31）**：当前分支已包含 main 的 M5 数据飞轮，
最终三项 residual 分别按 M5 exemplar、Skill guide 与结构化 focus 修复；定向真栈
scene **26/26**、vision 全通过、journeys **target 20/20**（B5-1/B5-2 同轮通过）。
最后一次完整 canonical 为 31/34、0 skip，后续未再为了“抽到全绿”重复整套流程，也**没有**
伪造 promotion；唯一新红 B1-4 是前轮已绿的天气省略追问采样方差，留给后续数据飞轮。
负责人明确接受“最后全量覆盖 + 失败项定向复验 + 相关单测”作为本次 M-A 完成证据，详情见
验收报告 §9。后续落域/意图问题继续走 exemplars/guides/boundaries，不恢复退役 hint。

**声纹面真机第二批（2026-07-26，泓舟反馈「删除失效 + 名字变成乘客」）**：两个都不在声纹
逻辑里。①**删除失效的根因是 CORS**——`Access-Control-Allow-Methods` 只有 `GET, POST,
OPTIONS`，而声纹删除是全 HMI 唯一的 `DELETE`，浏览器 preflight 直接把请求挡在门外，
**服务端零日志、e2e 全绿**（e2e 从服务端发请求，根本不过 CORS）。修法不是补一个方法，
而是让白名单与「app 实际注册了哪些方法」自动对齐（`test_http_cors.py`）。
②**名字不是没写进去，是被重录冲掉的**——库里躺着 4 条 superseded 的「泓舟」和 1 条现行的
「乘客」：HMI 的称呼框空着就静默兜底成「乘客」，服务端 upsert 又无条件覆盖 `display_name`。
三处一起改：称呼必填、空名按「这次不改名」处理、**新增改名 RPC**（`RenameVoiceprint` +
`PATCH /api/voiceprint/{id}`）——**用户反复重录正是因为没有改名入口**。顺带修掉两处不诚实：
删 primary 的确认框原来承诺「忘掉全部记忆」而服务端对 primary 永不 purge；删掉模板后注册
写的那条 `identity.name` 会残留，助手继续管一个已经认不出的人叫旧名。

**Skill 层闭环补全（2026-07-26，泓舟点题「skill 落地不完善、体验没达智能化预期」）**：
审计坐实三类断裂——治理闭环（few_shots 空契约 / golden `expect_intents` 无消费方 / eval
不在 CI / `plan.skills` 名单在裁剪前生成会谎称已注入）、检索停在纯词法（paraphrase 实测
**0/11**，「embedding 升级由召回数据决定」的数据车道根本不存在）、覆盖停在首批搬家（M0b
后零新增 skill，净增智能=0）。本批全部补齐：**检索双通道**（hybrid 默认：词法恒保留+语义
补位，fail-open 回词法；阈值 0.40 由 paraphrase 阈值扫描拍板，召回 0/11→**11/11** 零新增
噪声）；**golden 有了消费方**（eval_skills live 车道真栈真 LLM 逐条断言，expect 三键 AND/
ANY/NOT + `a|b` 容忍；**full 10/10 vs off 5/10，Δ=+5** 的知识有效性证据落基线）；few_shots
实装；归因诚实化（`@lex`/`@vec`/`!clipped`）；eval 进 GitHub CI；新增首个净增知识 guide
`charging-strategy`。**live 首跑即抓到分层真 bug**：「快没电了附近找个快充」被 nearby 设施
发现 hint（replace）在 LLM **之后**踩掉规划对的 charging.find——guard 漏了「没电/电量低」
SOC 线索，已修（纯发现仍归 nearby 富卡）；教训入册（skills/README + 架构 v1.10）：**「知识
不生效」的 badcase 先查 hint 层再改知识**。同路验收卡里 toolcall provider 能力位与
`PLANNER_TOOLCALL` 热切不对称两条**未动**（属 submit_plan 侧，维持 P2 立卡）。
scene-automation 候选 guide 被否：scenes 无触发器机制，「每次上车自动…」在系统里没有承载物
——**知识必须真**，宁缺毋滥。

**评审补强（2026-07-27）**：外部评审六项逐条核实后采纳 4.5 项——运行时容错闭合（两个真
崩溃 + last-known-good）、eval 文件级校验升 **CI 阻断步**（并补漏 scripts/proactive 两个
CI 分组）、**T2 replan 知识继承**（按 plan.skills 重渲染）、golden `holdout`/
`expect_complexity`（live 拆 in-sample/holdout 防原句自证）、compose 挂载 skills/ +
参数透传；驳回 Pydantic/hash 钉扎/步骤级全断言/「现在就建 Workflow」（理由在
skills/README 实装记录）。holdout 首跑再抓一个 hint 劫持（reminder「叫我」吃掉无标记
条件句），采样方差把充电 canonical 沉入 route_hints——**「canonical 归 hint、paraphrase
归知识」**正式入册。live full 14/14（holdout 5/5）vs off 8/14（Δ=+6）。
**二批（同日）**：设备充电误接回归修复（hint guard 排除非车辆主语）、挂起链继承补真
（plan.skills 随 pending_plan 持久化）、env 垃圾值不崩启动、`--ablate` 逐 skill 消融
车道（per-guide 因果归因——知识与 hint 的功劳第一次可分账）。live 16/16 vs off 12/16。
**三批（同日）**：to_plan 漏链补上（replan 产物挂起再恢复不再失忆，全链集成测试）、
charging hint 换正向锚（黑名单枚举不完，设备负例钉进阻断 pytest）、env 范围钳制、
消融重做为独立因果指标（`ablation` 键+`expect_ablation_effect`；**更正**：conditional
Δ=+1 在族容忍下不成立，仅 navigation 有跨 run 因果证据）。
**四批（同日）**：惠州句复跑 3/3 分类为采样方差（`--only/--repeat` 新增）；归因带检索分
（`@lex:23`/`@vec:0.52`）+ 渲染改相关度序（priority 降级同分定序）；分句首锚被「前一分句
藏主语」绕过→收紧句首/感叹前缀/「车」后；min_score 下限 1（钳 0 仍全量放行）+ isfinite
拒 nan/inf；live 数字以 baseline.json 为准不再冻结进文档。

**声纹面真机第三批（2026-07-26，「两个人的声音还是认成同一个」；RFC §11.8）**：不是模板问题
（两个模板互相余弦 0.27，分得很开）。三层叠加：①**探针里大半不是人声**——`vad.onFrame` 是
原始帧旁路**不做语音门控**，而识别器按「累计 1.5s 有效语音」的语义直接数帧，真机时序是
「唤醒→播『在呢』→用户才开口」，嵌入被稀释到谁都认不出→恒回 primary（修：只收 VAD 语音段的帧
+ 端点补发一次，否则短问句「你知道我是谁」约 1.2s 会从「认错」退化成「永不识别」）；
②**阈值是拿合成音色标的、方向标反了**——真人同人 0.52/异人 0.12，而 0.62 把同人一并卡掉；
合成音色异人高达 0.65 逼阈值上抬，真人分离度好得多反该下放，故 **0.62→0.45**（margin 不动）；
③**判定在生产里完全不可见**——identify 无日志，且 obs collector 的 `apply_metric` 是固定键
白名单把 `vp_*` 全丢了（RFC 承诺的「四态进 obs」实际没落地，已立卡）→ 网关与 memory 各补一行，
后者打**全量排名**（降级时 top1 身份本会丢，那正是调阈值最需要的数）。
④拍板：**认不出就不叫名字**——`occupant_id` 照旧回落 primary（记忆归属对），但称呼是断言，
只有 `accept` 才下发。多人测试须**换人重新唤醒**（同窗内续问/打断刻意不重识）。

**声纹面真机第三批·收口（2026-07-27，RFC §11.8.1）**：上面四条修完仍认不出，但泓舟发现
**设置页「试一试」能分得开**——这个差分一刀切开了问题：两条路唯一的区别是音频通路。
注册与「试一试」走 MediaRecorder **webm/opus 有损**，主链路识别走 **原始 16k PCM**；
同一批人实测 **webm 0.73/0.74 vs PCM 0.48/0.53，系统性差 0.2**，阈值正好卡在中间，
且 PCM 探针会塌向别人的模板（两人都判成 occ-2）。**声纹嵌入吃信道特征，模板与探针不同源
就不在一个空间里比余弦。** 修：新增 `hmi/src/pcmRecorder.mjs`（AudioContext 16k + 与 vadEngine
同一个 worklet + 同一组 EC/NS/AGC），**注册/试一试/识别三条路统一到一条信道**，契约测试源码级
钉死；配套补**行内「重录」按钮**（带原 occupant_id，否则重录分到新身份、记忆分家）与
**头尾静音裁剪 + 「只听到 X 秒人声」反馈**。三条可复用判断：①**要比对的两端必须同源采集**
（类型系统看不出 webm 与 PCM 的区别）；②**自证要走被证的那条路**——「试一试」走了更干净的
通路，于是在主链路已认错的情况下照样报「听出来了」，唯一的自证手段成了假证人；
③**差分是最省力的定位器**，两条路只差一个变量时那个变量就是答案。
⚠️ **需真麦复验**：Chrome 假麦克风装置本身不保真（同一 TTS 文件直取自洽度 0.83/0.73/0.81，
经假麦采进来后 ASR 只转出零星几个字、与源文件余弦 -0.03，换 48k/降 12dB/关 EC-NS-AGC 都不救），
故它只用于验接线（已验「试一试」确实发 `format=pcm16le`），识别率仍待真麦。
**换信道后两位已录乘员必须用「重录」按钮重录**（保留身份与记忆）。

**声纹面真机第四批（2026-07-27 真麦复验，RFC §11.8.2）**：同信道修完后重录复验，主链路日志
显示**识别已经对了**（`accept occ-2 0.4745/0.1229` 与 `accept primary 0.6665/0.2030`，两人分开），
但用户看到的仍是「都认成阿灵」。两个独立原因：①**「试一试」压根没跑识别**——四次调用零判定
日志，卡在 `too_short` 早返回：它只录 2 秒，切掉头尾静音后不足网关要求的 1.5 秒**有效语音**，
自证功能变成永远说「太短了」（修：录音窗与注册同长 4 秒）。**改了「有效时长」口径就要回头看
所有依赖时长的地方**。②**识别对了但答案被对话历史盖掉**——车里只有一个会话而说话人会换，
`ctx.history(4)` 里上一位的称呼比 system 提示更近；受控复现：新会话注入泓舟答泓舟 ✓、
会话里先聊过阿灵再注入泓舟答「你是阿灵呀，刚才不是说了嘛」✗。**先试加强提示词，两个方向各
两次全错**；改为**确定性直答**（`chitchat._identity_answer`，同 `_clock_answer` 一族）后两方向
全对。**系统自己持有的事实不要交给 LLM 回答**（本仓库第三次撞同一条）；**prompt 打不过上下文**。
③顺带发现脏数据（非代码缺陷）：识别错乱期写下的记忆已互相串（primary 名下有「用户自称阿灵」），
召回的 occupant 隔离本身是好的——**识别缺陷会在记忆里留下持久痕迹，修好识别不会自动修好数据**。

**当前主线：数据飞轮（M5），P0-P2 已落地、P3a 识别侧已落地**（设计
`docs/design/2026-07-28-intent-accuracy-data-flywheel.md`）。定性：准确率的
「badcase→改 manifest」治标循环不是架构错，是**改进循环的产物是正则不是数据**。

**P0（2026-07-28，`611351b`）**：evolve 提案半环闭合（治理③只扫动态内容+词边界，
degraded 改读 SQLite，triage 批失败重试）／落域可观测接活（`turns.intents`/`plan_mode`
+ `hint_effect`/`catalog_chars`）／gold 标注载体（`turns.gold_intents` + label/export API +
dashboard 入口）／catalog 预算 8000→16000（旧值把 navigation 整域裁出 prompt）／
vision「帮我看看」过宽分支两侧同步删除。

**P1 范例库（2026-07-29，分支 `feat/m5-p1-exemplar-store`）**：`skills/exemplars/` 落地
——**修落域 badcase 的默认产物自此是数据不是正则**。第三通道 `orchestrator/cloud/exemplars.py`
（骑 skills 的 hybrid 检索/预算/fail-open/归因/继承范式，共享 `embedding.py` 出口与冷却）；
三来源全通（manifest 199 条死资产盘活 + collector 标注转化 + evolve 第四类提案，
`_kw_pattern` 正则生成器退役）；门禁 `test/eval_exemplars.py` 进 CI 阻断步。
**三条最值得记住的**：①阈值全由 166 例探针扫描拍板（lex 0.34 / sem 0.65），词法用
**IDF 加权 Dice**——裸 Dice 在 5-15 字短文本上被功能词支配，而修法**不是停用词表**
（那正是这一期要消灭的规则工厂），是让语料自己长出权重；②**A/B 的 Δ 只能在「实际注入」
子集上算**——未注入的两臂 prompt 逐字相同，首轮 60 例里 3 次翻面有 2 次栽在这上面；
③N3 首测 4/5=80% 达线，但**80% 不是新范例的功劳**：新范例修好的是它自己那个形态，
paraphrase 靠的是已有 manifest 范例 + 语义通道接住——「一次修复自动传播」**成立的前提是
语料密度**。真栈 A/B（预热后 62 例注入子集，注入率 100%）：full 62/62 vs off 61/62，**可归因 Δ=+1、可归因回归 0**；默认 `EXEMPLARS_MODE=full` 的理由
是「零可归因回归 + 机制本身就是交付物」，不是「已证明有增益」。

**P2 度量驱动治理（2026-07-29，同分支）**：**N1 尺子立起来了**——`test/routing_bench.py`
首测 **189/192=98.4%**（canonical 100% / **paraphrase 96.5% 为主指标**），基线
`docs/reviews/eval/baseline_routing.json`。**但这个数必须配着三条限制读**：语料只有 192 条
可用（未纳入的六份语料与逐条排除的 56 例每次都印——**被排除的条数是隐藏分母**）、
域偏斜前三域占 70%（navigation 仅 5 例，**N1 涨不等于车控导航变好**）、98.4% 主要说明
语料已被用来修过系统（canonical 100% 是 hint 钉出来的）。
`test/hint_retirement.py` 给规则装上了**出口**（双臂裸跑，评测侧过滤 agent 列表零运行时
改动；provider 必须 pin、n=1 不做判定、产物是提案不改仓库）。首跑 32 条里 **23 条是退役
候选**——**这是发现不是决定**：单档跑出来的候选只证明那一档会，而 hint 的存在理由正是
「弱 LLM 会漏/误路由」，换个模型立刻回退完全可能。按 DoD 只退一条，其余等**跨 provider 交集**。
**规则存量 32→12**：`vision#0` 首条单独退役，其余 19 条按**跨 provider 全覆盖交集**批量退役
（`scripts/retire_hints.py`）。判定方法被数据逼着收紧了两次：**抽样→全覆盖**（每条只抽 3 句时
27 条候选，覆盖全部命中语料后降到 21——**抽样偏差方向固定：命中面越大越容易被放行，
而那正是风险最高的一批**）；**单档→跨 provider 交集**（补 deepseek-v4-flash 后交集 20 条，
3 条只在单档成立被挡下）。⚠ 两个必须记账的限制：MiMo 无可用 key 做不了第三档而这批 hint
当初正是为它写的（**切回 MiMo 须复验**）；召回断言原本是**阻断 pytest**，退役后保护改到
live 车道，**从「CI 阻断」降级成「人工触发」**（已立卡）。过程修掉三个自造缺陷：空
`route_hints:` 让 manifest loader **启动即崩**（`.get(k,[])` 对 None 不生效）／迁移判据把
「initial 非空」当护栏（正确判据是 `expect==initial` vs `expect!=initial`）／汇总行报候选数
而非实际执行数。首条退役的两个教训：
**证据要加厚到全部命中句**（抽样只取 3 句，实际命中 6 句；补验第 5 句 `这个坐标是什么地方`
反而暴露该 hint 正在**劫持**导航句——与它当初制造的咖啡店 badcase 同类）；
**退役的是规则不是回归保护**（四句命中语料降级迁进 mode_routing_cases 改端到端口径，
因为 hint 没了「施加 hint 后应得 X」的断言恒等于什么都没做）。
另有四项：`_always_include` 补 `category: core`（**D1 根治**——navigation/road-safety
首次进保护集；且修掉「hint 退役会顺手删掉 catalog 保护」这个 P2 自造的隐患）／registry
打分换 bigram 归一（**解除了 deep-research「desc 刻意不加长」那条倒果为因的历史约束**，
性质测试钉住）／端云 `_edge_nlu` 透传 + 分歧信号自动进日报／evolve `shadow` 强模型分诊。
**三处有意不做并附判据**：`PLANNER_CATALOG_TOP_K` 不下调（补 core 后 16 个 agent 里 14 个
受保护，检索化没有空间，杠杆在先让保护集瘦下来）／端侧初判不进 planner prompt（端侧
75.9% < LLM 91.2%，负期望，且**不留 env 开关**免得变成没人测过的分支）／8590 语料的
「可服务率 79/120」**不可与 Shadow NLU 91.2% 直比**（那是封闭集分类，这是受能力面约束的
真规划；语料含「第一页」「火车票查询」这类我们没有的功能）。

**P2 余项「nearby 规则群内讧」已收口（2026-07-30，`47eac1c`，规则存量 12→10）**：真根因
**不是规则打规则，是三处地盘声明互相矛盾而 P1 把它们批量导入成了金标**。`nearby#0` 的注释写着
「不含充电/加油/停车等设施类目」，而 `nearby#1`（2026-07-19 周边发现重构加的）专门认设施类目
——注释与实现互相否定；旧声明同时留在 navigation.examples / charging.examples 里，P1「盘活
199 条死资产」时**把过期声明一起激活成了金标**，于是 `ineffective`（带着 hint 也答错）读出来的
其实是「金标错了」。**教训：盘活死资产的同时也会把死资产里的错误一起激活。** 机制根因更深一层：
`nearby.search` 与 `navigation.search_poi` 的描述几乎逐字重叠 → planner 掷硬币 → **正则被拉来
当裁判**。三起裁定（充电桩发现归 charging.find／加油站交回模型／餐饮发现归 nearby）+ 顺带扫出
第二起同类冲突（info.alerts vs safety.weather_alert 抢「有天气预警吗」，例句只差语气词）。
**机制修复＝跨域边界裁定台账 `skills/exemplars/boundaries.yaml` + CI 阻断门禁**：先试机械判定
**不成立**（假冲突的相似度可以高于真冲突：lex 0.450/cos 0.885 的「搜固态电池最新进展 ↔ 深入
调研固态电池现状」是四模式路由的核心区分，比 0.483/0.773 的真冲突还高），故改成「人裁一次并
登记，机器只负责不许悄悄新增」；`lex_min=0.35` **拿三起真冲突反验都能抓到**（0.435/0.407/0.689）
——**先验证门禁抓得住它本来该抓的缺陷再启用**。另修 info#0 的真劫持（「查一下这个地点」，
用结构性判据「句尾纯指代短语＝没有可检索的实词」而非给 guard 追词；⚠ **必须放 guard 不能放
pattern 前瞻**——前瞻会被回溯绕过）、`retire_hints.py` 漏迁 mode_routing det 断言、
`--limit-per-hint 0` 静默切成空列表三个工具缺口。一条方法论入册：**为某条规则写的语料，
它的 gold 就是那条规则的输出**——双臂裸跑不能证明规则是对的，「带着 hint 也答错」那档必须人裁。

**P3a 端侧语义 NLU：识别侧已建成、执行侧刻意未接（2026-07-30，`86ec5d7`）**。
**先测天花板再训模型**（新方法论）：`test/eval_edge_coverage_ceiling.py` 让强档 LLM 提议、
**代码按 commands.yaml 结构核验**，160 条 miss 抽样 → 天花板 **89.2%（识别）/ 86.3%（本地执行）**；
⚠ 已量化上偏（结构核验挡得住幻觉 object、挡不住语义错配，人工抽查约 14% 错配）→ 折算真实
天花板 ~88%/85%，**恰好压线无余量**。底座 `iic/nlp_structbert_backbone_tiny_std`（4 层/35MB，
**ModelScope 拉取不是 HF**——HF LFS 实测 28-47KB/s、ModelScope 3MB/s）。
**两个评测口径**：holdout（同分布上界）object **95.8%** vs transfer（规则命中→规则漏判，
决策依据）object **64.9%**——**差 31 点就是「同分布」两个字的全部含金量**，报告永远两行都印。
**θ 扫描新知**：holdout 上校准完美单调，transfer 上 θ≥0.95 精度反降到 82.3%（低于 0.9 的 94.4%）
——**校准的单调性是同分布性质，输入一偏最自信那档最不可信**。
**DoD 未达成且本期刻意不追**：θ=0.8 才能到 86.4%，代价是**约 1% 的请求在端侧识别成错的对象**
（三闸挡危险动作，挡不住「合法但不是用户要的动作」）——这是产品决策不是技术选择，
故挡位停 shadow。**真栈已验**（容器内 `edge NLU ready：8 域/83 对象`、4.8ms/条、
span 落 `nlu_vs_rule` 三态），**影子第一条观测就抓到一个真 badcase**（见下方立卡）。
刻意不做三件：**没有 `EDGE_NLU_MODE=on`**（缺对象桥接与 operate 抽取，齐备前不留这个值）／
影子只挂上云那一支（⚠ 代价：规则**误接**在本地那一支、影子看不见，而误接最危险）／
模型不烤进镜像（compose `models/` 只读挂载）。

**M5 全量评审已过（2026-07-30）**：P0→P3a 全部 14 个提交逐项核验——声称与实现一致、
全量 2408 passed / 7 skipped、无 P0/P1 级缺陷、无红线违规；3 条记账缺陷（本节此前引用的
两个提交号是 `wip/m5-p3-rescue` 救援分支的旧 hash、架构文档缺 P3a 版本注、RFC 一处
设计→实现演化未回写）当日修复。报告 `docs/reviews/2026-07-30-review-m5-data-flywheel.md`
（含 INFO 级观察项与立卡清单；**§7 记 2026-08-01 的评审后续**——其中一条把本报告放行过的
归因证伪了，评审自身的教训是：「A 与 B 一致吗」这类判定要单独问一句
**「A 和 B 是同一个空间里的量吗」**，声称↔代码对照对跨模块口径天生偏弱）。

**M5 P3 收尾已合入（2026-08-01，分支 `feat/m5-p3-closeout`）**：P3a 三张卡 + 评审两条
INFO 项一起收口。**本批没有一行改变路由行为**，全部是让「已经在发生的事」变得可测；
但过程中**证伪了 P3a 的一条归因，并修掉一个让影子数据整体失真的缺陷**。四句话：

- **判别化描述的收益在 registry 不在 planner**。78 条描述改成从 VAL 知识库机械生成
  （`display_name` × intent 名末段 × 中间段，解码走 `edge_call.decode_intent`——
  **与 executor 真执行同一个解码器**）。但「planner 看到 74 个文本等价的工具」这条归因
  **被双臂差分推翻**：渲进 catalog 后跨 minimax/deepseek 两档、25 条语料 ×2 轮
  **Δ=0 且 100 次对照零翻面**，代价 +1462 字符/次规划 → 否掉渲染，并把这条负结果写成
  护栏（`test_catalog_budget.py`，要推翻请附跨 provider 双臂数据）。真正有效应的是
  **registry 语义兜底**（按 capability 粒度 embed）：泛化描述下「打开空调」的 top-1 是
  `scene-orchestrator`(0.517)、「把音量调大一点」edge-vehicle 连前三都没进——而那正是
  **LLM 失败时的兜底规划路径**；换判别化描述后 0.675/0.670 回到 top-1。
  ⚠ 这缺陷一直躲在弱断言后面：那两条 golden 只有 `forbid_top1: parking-payment`，
  **断言否定命题守不住肯定性质**（已补 `expect_top1`，语义层 20/22→21/22）。
  原始 badcase 的真根因另有其人：**能力面根本没有除雾意图**，描述治不了缺能力。
  证据 `docs/reviews/eval/edge_capability_desc_ab.md`。
- **影子的 `agree` 状态在生产里从来没出现过**。三套对象命名（语料 83 中文标签／VAL 65
  英文 object／规则 95 个、38 个连 VAL 都没有），`_nlu_shadow` 拿第一套和第三套直接
  `==`，规则一命中就恒 `differ`——**而 P3b 的错对象率正要拿这一档当分母**。补
  `knowledge/nlu_objects.yaml` 等价类台账后：**agree 0%→68.8%、differ 24.8%→6.3%**，
  中间 18.5pp 全是命名差异被记成分歧。
- **影子挂满四条路径**（响应后 fire-and-forget，落独立 `nlu.shadow` span）。`path`
  让**误接与漏接分得开**。顺带修掉多意图路径的第二类结构性假分歧（单标签模型 vs 多意图句）。
- **两条 INFO 项收口**：分词 parity 冻结 golden 进库（CI 零依赖必跑，启用前反验注入缺陷
  都能抓到）；θ 从零消费方变成有读者（影子落 `nlu_gate` 三档，只记不用，同时攒 P3b 判据）。

**nightly 收口（2026-08-02，`c75df13`）——M5 P2 退役 hint 的第二笔账，当时没记**：
nightly 自 `#30`（2026-07-29）连红三次，根因**一个**：那批 route_hints 退役（32→12→10）
把 **mock 车道的确定性基础**抽掉了，而退役判据是在**真 LLM 双臂**下取的证，从没覆盖过
mock 车道。mock 里 MockProvider 只回显原话 → 规划必走 `planning._fallback` → 兜底 chitchat，
于是 5 个脚本同时失效（trip 6 项／context 2 例／degrade agent_down／journeys A4-2+B4-2／
ws 链路4a）。**那些「端到端路由」断言此前一直是正则在撑，不是系统在撑。**
证据最硬的是 journey A4-2 自己的注释：「mock-safe：**route_hints 确定性路由**」——
**判据写的就是「有 hint」**（旅程体系设计文档 §4.3 的定义同样如此）。
判据已改写（见 §4.0 末），按新判据收窄后 mock 全栈实跑 nightly **9/9 PASS**。
⚠ ws 链路4a 靠的是 nearby 的 hint（2026-07-30 才退），所以它从 `#31` 起才红——
而 `#31` 起 workflow 换了 runner 不再逐条报 `::error::`，**这一条一直没被看见**。


### 1.x 意图落域对抗测试首轮发现的修复批次（2026-08-03）

清单与逐条记录见 `docs/design/2026-08-02-intent-routing-adversarial-findings.md`
（**该文件是这一批的单一入口**，§5 是改了什么、§6 是没收口的）。这里只留三句话与四条判据。

**三句话**：① L0 五条红灯全修（70/70），其中「问天窗能开多大真把天窗打开了」这条高危的修法
不是加否决词，而是**给否决面定作用域**——只盖写操作，不盖查询（「胎压是多少」也带疑问词，
一刀切会把好用的秒回一起砍掉）；② L1 的 30 条稳定缺陷降到 5 条，**但其中三簇的首轮归因是错的**
（详见下方判据一）；③ 本批**规则净减一条**（`reminder#1` 退役、`deep-research#1` 收窄、
新增 0 条），北极星 N2 成立。

**判据一：`goal` 是免费的裁判，用它先分清「模型错了」还是「规则踩了」。**
簇 B 被首轮定性成「reminder 域动词分工分不开」，实测 planner 的 `goal` 逐条写对
（「查询明天的提醒列表」「把那条标记为完成」），steps 却统一是 `s_hint_reminder_create`
——**分得开的是模型，踩掉它的是规则**。同族还有簇 A 的第 4 条与簇 E 的「第二条详细讲讲」。
一条 `replace` hint 在**逐字相同**的两个场景里指向相反意图时（报告章节 vs 新闻列表项，
guard 是纯文本正则看不到焦点），它按定义至少一半时间是错的——这不是补第五轮 guard 的问题。

**判据二：加一条常驻 policy 会静默挤掉一条 guide。**
`render_skills_block` 先无条件铺 policy 再按检索序塞 guide，**共用一个 `SKILL_BUDGET`**。
新 policy 落库后 L0 当场掉一条：policies 1047 + 块头 14 + `navigation-with-stop` 1428
= 2489 > 2400。**能当场发现的唯一原因是注入名单诚实**（`!clipped` 不谎称已注入）。
修法是压 policy（506→277）+ 提预算，更要紧的是把这次的算术钉成红线
（`test_skills_budget_headroom.py`，已反验）。

**判据三：范例说法一律避开语料原句。**
把对抗语料的原句写成范例，那条 unseen 用例就变成 seen，之后再读「通过率涨了」
就读不出泛化。本批 8 条净增范例一条原句都没抄。⚠ 同族反例：给 manual/vision 边界加的
「右侧对照」范例离左侧那句太近（`@vec:0.66`），当场把左侧用例打红——
**对照范例离对面太近就不是对照，是干扰**，已撤回。

**判据四：同一段代码在生产侧正确、在评测侧可以是错的。**
全量跑中途撞上网关 `all models failed`，`PlanBuilder._fallback` 按设计兜底成
`chitchat.talk`——**生产侧完全正确**（LLM 抽风时仍有回应），但在评测侧它把「通道坏了」
伪装成「模型判错了」，6 条组合意图用例被记成 `stable_fail`（重跑 6/6 绿）。
已机制化 `_reject_unreached_planner`，判据用 `raw_llm` 为空而不是「计划长得像兜底」。
这是本套件自查清单的第 9 条，也是「**每加一个『拿不到结果』的分支，先问它会被记成什么**」
这条老判据的又一次兑现。

⚠ **读数口径**：同日另一路独立评审
（`docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md`，对象是**本批修复
之前**的尺子）判定该套件不通过 gate/baseline 验收，`exact_plan_set_rate` / `seen-unseen` /
`capability_hallucination_rate` / `relation_pass_rate` / `instability_rate` 口径均不成立。
**本批一行没动尺子**（修尺子与修被测对象同批进行正是该体系明令禁止的事），因此只引用评审
明确认可的两项：**原始 evidence unit 通过数**与**逐条复现**。评审的 12 条另开一批。


## 2. 验收余项批次 M-B / M-C / M-D（2026-08-01）

**M-B 多乘员数据隔离已落地（2026-08-01，分支 `codex/acceptance-mb-occupant-isolation`）**：
验收报告 §7 的 P1-01/P1-02 与 §3 的一批 occupant 缺口收口。定性：**M4 P4 让系统知道
「谁在说话」，但那只到请求控制面——数据面存不下来，等于没识别**。落地五件：
①**Turn 带 OwnerKey 与 exchange**（`turn_id` 幂等、异内容 `turn_conflict` 保留原文；
一次请求+可见回复共用 `exchange_id`，重试是重放不是新一轮）；②**历史默认 OWNER_ONLY**
（`last_n` 是过滤后上限，切中 exchange 整体舍弃最旧的半个——只留 assistant 半句会让
抽取把助手的话当用户偏好），classic/S2S/端侧三条路径全部带 owner；③**抽取窗口在进
extractor 之前按 owner 切好**（归属不交给 LLM）+ 节流键带 owner + `source_turn_ids`
存真实 turn id（此前填 session_id → `evidence_count` 永远数出 1）；④**places 收敛为
owner-scoped `memory_item`**（per-key patch 不整块覆盖；primary dual-read 只补缺失
key、非 primary 永不读 legacy KV）；⑤**reminder owner 化**（schema 加法式补列；
`claim_due`/`claim_location` 仍跨 owner 原子领取但**消费必须先按 OwnerKey 分组**——
此前整批共用一条 speech 且 user_id 取 due[0]，两人同秒到点时一个人会听到另一个人的
提醒）。另加**声纹显示名同账户唯一**（重名＝同一个人被分成两个 occupant，两条相似模板
互相顶成 ambiguous → 真机「谁说话都认成同一个」有这一层）与 **L1 精确删除**
（取代「单行删除复用 scope 删」——删自己的名字会删光全车的名字）。
契约见 `docs/conventions.md` §9.13。
**本批明确未做**（GDPR 完备性与验收仪式，不是当前会产生错误行为的缺陷）：跨域
L2/L3/L4 删除 saga 与 privacy registry 协议、observability 四表 owner 与原文脱敏、
ReminderAdmin/SceneAdmin 管理服务、独立迁移 CLI 与 pg_dump 备份流程、真栈多乘员 E2E
矩阵、声纹注册单事务。原 superpowers 计划的 15-task 重流程按产品负责人裁决简化执行。

**M-C 可靠触达已落地（2026-08-01，分支 `codex/acceptance-mc-reliable-delivery`）**：
关的是 §4-6 与 §7 P1-04/P1-05、§4-3 P2-05。核心命题一句话：**`publish 成功 ≠ 用户收到`**。
生产方到用户之间有三处会静默丢——①治理器 ack 后进程死（待发/延后队列全在内存；M3 RFC
写的「生命周期以秒计」描述的是 advisory，被当成了全部四档的性质）；②HMI 断线时
`broadcast` 的返回值只进了一行日志，n==0 即蒸发；③S2S 说话时主动语音被一刀切成只出气泡，
**而一刀切的根因是网关把 `priority` 吞掉了**，HMI 分不出哪条该抢话。
修法：`proactive_delivery` 账本（只给 critical/user_contract）+ **落库后才 ack** +
`delivery_id` 随信封走 + HMI 回执销账 + 连上补投 + 重启恢复（与补投共用同一份账）；
网关透传 priority 后 HMI 按档仲裁（critical 抢话 / user_contract 排队待空闲补播）。
另两件：**Verifier 对 transport-uncertain 失败查世界状态**（三条边界：只认超时族——
Agent 明确报的错是确定失败不许被状态翻案；只认 state_match；**不改 status**，聚合器换
一句诚实话术但不伪造成功话术）；**闸5 频控改延后**（与闸3/4 对称——原注释「窗口是小时级
延后没意义」不成立，窗口是滑动的）。契约见 `docs/conventions.md` §9.8 可靠投递段。
**真栈已验**：断线时消息停在 `dispatched`（已发出仍算没送到）→ 补投携带原凭据 → 回执后
`presented` → 再补投 0 条；重启日志「投递账本恢复 1 条未送达消息」；缺 DSN/缺 asyncpg
两种降级都如实报 `durable=off`。
**本批明确未做**：多实例 outbox worker 与 present lease/state_version 并发协议（一辆车
一个 HMI，量级个位数）、HMI IndexedDB 收件箱、影子与分来源灰度、`research_report` 表
（报告正文早在记忆里）、Ledger owner-v2 cutover、真栈故障注入矩阵、位置提醒的地理谓词
（求值器算子集与 scene solver 有等价契约，本批用 ttl 兜住陈旧补播这个真实风险）。
**过程记账**：实现完先跑了受影响子集全绿就提交，全量跑出来 **33 failed**——新表存了
个人数据没登记进 M-A 隐私清单，而守卫在 `scripts/tests/`，恰好在「相关子集」之外。
**动 schema 的批次，守卫多半不在被动的那几个包里。** 清单要改三处（runtime
`PRIVACY_TARGETS` / manifest / 测试里的硬编码期望与计数），顺序与计数都比——
那是防元数据自证的设计，摩擦是有意的。

**M-D 外部生态已落地（2026-08-01，分支 `codex/acceptance-md-external-ecosystem`）
——2026-07-26 验收报告 §7 的 13 张主卡至此全部有归属**。三张卡里只有一张是用户真的
缺能力：**下了单之后没有任何办法查它或取消它**。
①`order.cancel` 从一开始就在商户侧存在、也被 `order.create` 声明为 `compensate_tool`，
但**从没进过准入清单**——补偿因此只在准入期被校验存在性、运行期零调用、用户零入口。
放进清单它才是能力（**声明存在 ≠ 能用**），仍走确认闸，不做未经确认的自动补偿。
②新增 `order.get`，按订单号**或幂等键**查——幂等键那条是关键的一半：下单超时那一单
根本没有订单号，但幂等键是我们生成的、商户按它索引，「到底下没下成」由此第一次可以
核对。③超时话术把承诺加回来了，顺序是**先有能力再有话术**。
④**Ledger 原子幂等**：`open()` 的「先查再插」允许两个实例同时拿到执行权（对写操作
就是双下单），改为活跃态 partial unique + `ON CONFLICT DO NOTHING`，竞争输了按
Duplicate 而非失败处理。⑤**provider `supports_toolcall` 能力位**：声明式、缺省 True、
不认识的档不定罪、每次请求现读（热切不沿用旧能力）——此前不支持的 provider 每轮
白打 2 次上游。
**本批明确未做**：`mcp_operation` 独立业务状态表（**商户是订单状态的真相源，本地镜像
就是第二真相源**——本批最重要的一个减法）、Ledger owner-v2 cutover 仪式（不重分配任何
数据，只加了一个索引）、HMI operation card、双 bridge profile、GDPR retained-audit、
声明式 patch_missing。契约见 `docs/conventions.md` §9.9。


## 3. CI 收口的逐条修复记录（2026-08-01）

| 项 | 状态 |
|---|---|
| CI 破点 | `#217` 绿（`87edc13`）→ `#218` 红（`449c5d1`），中间是 **M-A 那 20 个提交**。与 M-B/M-C/M-D 无关；**`#232`（`176dd20`）收口** |
| 已修 ① 环境泄漏 | `test_remaining_e2e_protocol._load()` 加载 `e2e_real_providers.py` 时，后者 **import 期** `os.environ.setdefault` 把 .env 灌进同进程；monkeypatch 还原不了它。后续 charging-planner 用例把 provider 决议从 mock 翻成 real → 真调用拿假 key 失败 → 无卡 → 红。**group 1 已由 CI 确认转绿** |
| 已修 ② 平台假设 | `test_run_go_tests_wrapper` 硬编码 `%SystemRoot%\System32\WindowsPowerShell`（Ubuntu `KeyError`）；假 docker 只写 `docker.cmd`（Linux 上打到真 docker，`go: downloading` + pwsh 超时）；`test_e2e_stack_lease` 用 `mkdir()` 建 0o755 目录，而 Linux 侧先校验 0o700/0o600 → **那两条断言在 Windows 上从来没被真正执行过** |
| 已修 ③ CI 可诊断性 | 此前只报「pytest group FAILED: <组名>」，而 **job 日志需要 admin 权限**（`/actions/runs/{id}/logs` 返回 "Must have admin rights"）。改成把 `FAILED/ERROR` 行逐条升成 `::error::` annotation（公开可读）。**没有这一步就只能靠本地复现去猜** |
| 已修 ④ **首次 canonical 晋升在 Linux 上必崩**（真缺陷，4 条） | `_promote_canonical_report` 是**先恢复事务、后建目录**（mkdir 在 `write_report_pair` 里），首次晋升时 `docs/reviews/eval` 还不存在 → `_cleanup_report_transaction` 末尾的 `_fsync_directory` 对不存在的目录 `os.open` → `FileNotFoundError` → 被 `main` 的宽 `except OSError` 吞成泛化的 `canonical_promotion_failed`，**四条用例的具体拒绝理由全部丢失**。修在 `recover_report_transaction`：**目录不存在 ⇒ 从来没有过事务，无操作返回**；刻意**不**让 `_fsync_directory` 容忍 ENOENT（事务目录在写入过程中消失是真事故）。带反验过的回归测试 |
| 已修 ⑤ **go wrapper 在 Linux 上一直是坏的**（真缺陷） | `run_go_tests.ps1` 写死 `go test \"$@\"`。**Windows PowerShell 5.1 是 Legacy 传参**（参数拼进命令行串、接收方 `CommandLineToArgvW` 反解），`\"` 正好还原成 `"`；**pwsh 7 在非 Windows 上默认 Standard**，逐字经 argv 交出去 → 反斜杠原样进容器 → sh 里 `\"` 是字面量引号 → `go test` 收到带引号的包名。判据改成「**怎么传参**」（`$PSNativeCommandArgumentPassing`）而不是「什么系统」——pwsh 7 在 Windows 上默认也是 Standard |
| 已修 ⑥ 又两条写死 Windows 假设 | `test_e2e_identity` 用 `mkdir()` 建 0o755，而 `replace_private_file` 头一件事就是校验父目录 0o700；`test_e2e_stack_lease` 期望 `match="regular"`，但 POSIX 上是 `_require_posix_private_metadata` **先判类型再判权限**先开的火。⚠ 上一版试图 `chmod 0o600`「绕过权限层去够 S_ISREG」——**绕不过去**：对目录而言那条 S_ISREG 在 POSIX 上不可达，它是 Windows 侧的岗哨。改成按平台断言真正开火的那一层，**不把 match 放宽成谁拒的都行**（放宽等于不再钉住是哪一层在守） |
| 逐条比对基线（2026-08-01） | run **#228**（`399046f`，M5 P3 收尾**之前**）与 **#229**（`d6fbca3`）／**#230**／**#231** 的失败集合**四次逐条相同**——M5 P3 收尾（4 个新测试文件、~40 条用例，含无依赖的分词 golden）在 Ubuntu 上全过，**零新增红**。留这行是为了下一个人不必再自己比一次 |
| nightly | **✅ 已收口（2026-08-01，`c75df13`）**：自 `#30`（2026-07-29）连红三次，根因**一个**——M5 P2 那批 route_hints 退役（32→12→10）把 mock 车道的确定性基础抽掉了，而退役判据是在**真 LLM 双臂**下取的证，从没覆盖 mock 车道。mock 全栈里 MockProvider 只回显原话 → 规划必走 `_fallback` → 兜底 chitchat，于是 5 个脚本同时失效（trip / context 2 例 / degrade agent_down / journeys A4-2+B4-2 / ws 链路4a）。**那些「端到端路由」断言此前一直是正则在撑。** 修法=按新判据收窄（下方），mock 全栈实跑 **9/9 PASS**。⚠ 下一次定时跑（UTC 18:00）才会在 CI 上确认 |

## 4. 历史状态总表与早期批次（2026-06 → 2026-07：主干、R1-R4 硬化、各域重构）

| 项 | 状态 |
|---|---|
| 全量测试 `python -m pytest --import-mode=importlib` | ✅ **3647 passed, 11 skipped, 0 failed**（2026-08-02 实测=M5 P3 收尾 + CI 收口 + nightly 收口三批后，main@`c75df13`。本批净 **+50**[判别化描述契约 16／对象等价类台账门禁 15／影子四路径与 θ 档 16（`test_nlu_shadow_paths.py`）／分词冻结 golden 1／catalog 端侧只渲染意图名的负结果护栏 1／`recover_report_transaction` 目录不存在即无操作 1]，另有一批断言**按新意图改写而非删除**不计净增（manifest 清单冻结、degrade 目标从 trip-planner 改 chitchat、journeys nightly 覆写从「有」改「无」）。⚠ 本批**没有一行改变路由行为**。此前 **3597 passed, 11 skipped, 0 failed**（2026-08-01 实测=M-D 外部生态落地后，分支 `codex/acceptance-md-external-ecosystem`。本批净 **+16**[MCP 查单/取消/补偿 9（含真栈三修的槽位问法与确认词各 1、AST 源码断言 1）／Ledger 原子幂等 2／provider 能力位 4]。**真栈跑出三个单测测不到的缺陷**：mcp-bridge 镜像从来没装过 asyncpg（既有，M3 起幂等受理一直关着）、取消追问用了下单的词、确认词说错动作——见验收报告 §12.3b。此前 **3581 passed, 11 skipped, 0 failed**（2026-08-01 实测=M-C 可靠触达落地后，分支 `codex/acceptance-mc-reliable-delivery`。本批净 **+33**[投递账本 12／治理器可靠投递 9／HMI 语音仲裁 10（node）／Verifier transport-uncertain 6／提醒投递 ttl 2 — 与既有用例合并后计数如上]。**本轮先跑受影响子集全绿就提交，全量抓到 33 failed**（新表未登记隐私清单，守卫在 `scripts/tests/`），修完复跑归零；上一版记的 wrappers 间歇红本轮未复现。此前 **3548 passed, 11 skipped**（2026-08-01 实测=M-B 多乘员隔离落地后，分支 `codex/acceptance-mb-occupant-isolation`。本批净 **+35**[owner 轮次 12（含删乘员清原文 2）／抽取归属 2／端侧 owner 2／云侧历史 3／places owner 4／SDK 透传 3／reminder store 隔离 5／scheduler 分组 3／geofence 分组 3（新建文件）／声纹判重 6／L1 精确删除 6 — 与既有用例合并后计数如上]。**一条间歇性红灯已定性并立卡**：`test_e2e_wrappers_ci.py::test_python_and_powershell_preserve_final_json_selection_argv_and_rc[args0-0]` 间歇红（6 次整文件跑里红 2 次）。抓到失败那次的输出后定性清楚：红的**不是 rc**（两个 wrapper 恒 0，直接跑 10/10），是 `assert python_json == ps_json` —— 两个子进程对「canonical 为什么 stale」给出不同答案：`canonical_recompute_failed` vs `canonical_metadata_missing`（`stale: True` 两边一致）。前者是 `run_e2e.py:1009` 把 `ManifestError|OSError|ValueError` 吞成的一个**理由值**；同一计算在进程内连跑 30 次零异常、稳定给出 `canonical_metadata_missing`，故偶发只出现在 spawn 出来的子进程里。**这条路径只有在 canonical 已经 stale 时才可达**——那是任何有未合入提交的特性分支的常态，main/M-A worktree 上 canonical 是新鲜的，比较恒等、缺陷不可见。结论：**M-A runner 的 staleness 路径的既有健壮性缺口，被「有未合入提交」暴露而非由本批引入**（本批未触及 `scripts/run_e2e.py`/wrapper/manifest，`test_e2e_manifest.py` 168 条全绿）。修法方向=瞬时失败该重试或如实上抛，不该伪装成一条 canonical 判定理由。此前 **2379 passed, 7 skipped**（2026-07-29 实测=数据飞轮 P1+P2 全部落地后，main@857d38a。轨迹：P0 后 2347 → P1 **+16**[范例层契约：最软层容错/IDF 加权/同域去重/归因诚实/语义 fail-open 与补位/T2 与挂起继承/与 SkillStore 的目录边界/阈值钳制] → P2 **+21**[registry 打分性质 5（**加长描述不得改变 top-1**）+ 端云分歧 5 + 影子分诊判定表 10 + plan_summary 分歧列 1] → hint 批量退役 **−5**（删 7 个断言已退役机制的召回测试、加 2 个退役记录测试，**可对账**：2384−7+2=2379）。六个离线 eval 全 exit=0（route_hints 84/84、mode_routing det 55/55、registry_resolve 15/15）。此前 **2347 passed, 7 skipped**（2026-07-28 实测=数据飞轮 P0 落地后，分支 `feat/m5-p0-data-flywheel`。python 侧本批 **+24**[evolve 提案半环修复 4（词边界不误伤 eval/三类模板不自触发/动态禁区仍生效/SQLite 详情形状）+ obs 标注载体 10（span↔turn 合并双顺序/gold 标注与批量导出/清理豁免/export 路由不被参数路由遮蔽）+ catalog 裁剪 stats 2 + **D1 契约 2**（真实 manifests 实证：8000 预算下 navigation 等全部无 route_hints 的 agent 被整域裁出 prompt / 16000 零裁剪）+ hint_effect 分类 6]；dashboard vitest **17**（+1 gold 标注保存）+ tsc 零错；HMI node visionFrame **10**（+1 LBS 句不抓帧反例锁）；eval_route_hints **122/122**（语料 +6：vision 劫持 guardrail×2 + 召回×4，基线随语料刷新）、eval_fast_intent 57/57 无涉及；见下方「数据飞轮 P0」行）；此前 **2323 passed, 7 skipped**（2026-07-27 实测=声纹面真机第四批 + 乘员维度盘点后。python 侧本批 **+6**[chitchat 身份直答 4（认出即直答 / 认不出不硬答 / 不劫持含「我是谁」的别的意图 / 礼貌前缀与语气词）+ planner **祈使指令不接受 not_addressed** 2（七种祈使前缀全覆盖 / 「我不记得了」这类陈述不得借此绕过拒识）]；HMI node **214**（+11：pcmRecorder 源码级契约——采集约束与 vadEngine 逐字一致、两个端点默认必须 pcm16le、设置页不得再用 MediaRecorder、头尾静音只切两端不在中间挖洞）；见下方「声纹面真机第四批」行）；此前 **2306 passed, 7 skipped**（2026-07-26 实测=声纹面真机第三批修复后。python 侧本批 **+2**[**真人实测值钉成回归**：同人 0.5243/异人 0.1157 必须 accept、纯噪声 -0.0585 必须拒]；主战场在 HMI node **203**（+9：语音段门控 4 + 端点补发 3 + 认不出不叫名字 1 + 既有 16 条改按真实调用序喂帧——**它们此前默认「帧即有效语音」，正是放过这个 bug 的原因**）；见下方「声纹面真机第三批」行）；此前 **2304 passed, 7 skipped**（2026-07-26 实测=Skill 层闭环补全后，本批净 **+6**：test_skills 13→19 用例[few_shots 渲染进注入块/未知顶层键告警不拒载/被裁 guide 诚实标注不谎称注入/hybrid 语义补位 paraphrase/embedding 不可用 fail-open 回词法/plan_skills 名单通道+裁剪契约]）；此前 **2298 passed, 7 skipped**（2026-07-26 实测=声纹面真机第二批修复后，本批净 **+11**：memory 声纹 6[空名保留已有名/同名重录不堆积记忆/改名改表也改记忆/改名不动模板/改名拒空名与未知乘员/删 primary 撤回注册写的名字] + 网关 CORS 契约 5[**注册了什么方法就必须允许什么方法**/删除与改名可达/真跑一次 OPTIONS 验响应头/两条路由存在]。注：下方 2189 是 2026-07-25 时点值，其间总体验收批次的新增用例未单独登记，故差值不止本批）；此前 **2189 passed, 7 skipped**（2026-07-25 实测=M4 S2S 线 P0→P3 落地后，净 **+67**：S2S 网关 67[对上协议/事件映射逐条/工厂拒 tools 不支持型号/三层打断含残包丢弃与 abandoned/重连重注入与 DEGRADED/**turn 悬挂看门狗**/回灌两条易漏项（escalated 轮不重写 memory、被打断轮只存已播增量）/**源码级铁律 3**（会话层零领域字面量、协议恰好一个工具、inject 不得 create response）] - 其余并入既有用例；HMI 另有 node **171**（143 既有 + s2sClient 28）；见下方「M4」行与 `docs/design/2026-07-25-m4-s2s-fullduplex-rfc.md` §11.4）；此前 **2122 passed, 7 skipped**（2026-07-25 实测=M3 全期落地后，净 **+81**：主动治理器 22[六道闸/合并/延后/三态与 scene 对齐/**零 kind 字面量源码断言**] + 主动客户端与迁移护栏 6[fail-open 三态/**全仓不得直发老主题**] + 低电量生产方 8 + 位置提醒 28[placeparse 词形与 ETA 让路/haversine/围栏边沿播种/**原子领取不重复触达**/Agent 级诚实追问] + MCP 桥 17[三重准入/写操作五项/真子进程协议往返/演示标注三重冗余] 见下方「M3」行与 `docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md` §10.6）；此前 **2041 passed, 7 skipped**（2026-07-25 实测=M2 后半记忆图谱落地后，净 **+119**：偏好加权 40[weighting 纯函数 22 + 巩固期加强 7 + 注入渲染契约 11]、关系边 41[封闭词表/一跳解析/**GDPR 级联红线** 28 + 人称目的地 13]、routine 时间加权 5、emotion 21 + TTS instruct 4 + 其余并入既有用例；见下方「M2 后半：记忆图谱」行）；此前 **1922 passed, 7 skipped**（2026-07-25 实测=M2 核心件 P0/P1/P2 落地后，净 **+135**：Task Ledger 59[`_sdk/test_ledger.py` 36 纯函数+分支接线 / deep-research `test_ledger_integration.py` 23 话术与流水线] + Outcome Verifier 44[`test_verify.py`：两求值器三态、retry 授权、**中央零领域字面量源码断言**、**流式直通必须调对账**、即插即用、R9 口径、聚合器话术] + 云侧车况镜像 8 + T2 分档与防抖 17 + pipeline 心跳/停手钩子 4 + registry round-trip 保 verification 3 - 其余并入既有用例；见下方「M2 核心件」行与 `docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md` §9）；此前 **1739 passed, 9 skipped**（2026-07-24 实测③=M0b Full Migration 后，净 +1 用例[默认 full 契约]；skip 9=7+2 环境敏感项让路——本跑与 planner 容器重建并发，同实测①波动形态，零失败；此前 **1740 passed, 7 skipped**（2026-07-24 实测②真栈在场，含 **M0b Skill 层 +11**（`test_skills.py`：加载/词法检索命中/反例静默/渲染预算/**即插即用契约**/SKILLS_MODE 四态注入）；skip 9→7 证实实测①的 +2 确系全栈未起的环境波动（栈起即转 passed）；此前 **1727 passed, 9 skipped**（2026-07-24 实测①，含 **M0a 数据真实性+确认兜底批 +12**：navigation outage 诚实降级 4 + 无 mock 兜底字段结构测 1、charging_planner outage 2 + 字段结构测 1（原「Provider 失败→降级 mock」用例改写为诚实契约）、编排 `test_capability_confirm.py` 4（LLM 不可增减确认级 / Agent 漏标 manifest 兜底 / 下游只升不降 / confirmed 不开执行旁路）；nearby 试点 2 条断言 FAILED→OK 对齐 R9 契约；**skip 7→9 为环境可达性波动（本次全栈未起），与本批无关、栈起复核**；见 `docs/design/2026-07-24-eva-benchmark-intelligence-upgrade.md` §6 M0a 落地记录）；此前 **1717 passed, 7 skipped**（2026-07-19 实测，含**赛事域进球明细+预测焦点批次 +5**——info 进球指代/焦点日期锚定/完赛短路 3 + 路由回归「(这\|那)场×预测词」召回与 guard 反例 2，见下方「赛事域进球明细+预测焦点批次」行与 `docs/design/2026-07-19-sports-goal-detail-and-predictive-focus.md`）；此前 **1712 passed, 7 skipped**（2026-07-18 实测，含**三轮真机反馈：提醒句被体感共现规则劫持修复 +6**——edge `_is_reminder_utterance` 提醒话术全局让路 + HVAC 显式词形收窄（978f756），见 `docs/design/2026-07-18-voice-interrupt-context-tts-batch.md` §6）；此前 **1706 passed, 7 skipped**（2026-07-18 实测，含**语音打断/上下文关联/TTS 播报三主题 +10 与二轮反馈 +1**（`fix_relative_year` 纠偏 6 断言；天气卡 focus / 预测词表放宽并入既有用例）：weather 日期感知纯函数 2 + handler 级「明天答预报/超窗诚实」2 + sports 猜族预测让路+指代锚点 1 + context 焦点 last_intent 契约更新 1（替换原 empty-returns-none）+ VAL 多意图名词式话术 5，见下方「语音打断/上下文/TTS 播报批次」行与 `docs/design/2026-07-18-voice-interrupt-context-tts-batch.md`；另 hmi node 143（+6：voiceLoop barge-in 动态 pre-roll 3 + ttsQueue speechCovered 3））；此前 **1629 passed, 7 skipped**（2026-07-15 实测，含**旅程遗留卡收口 +13、尾项 +6、墙钟 badcase +5 与 reminder 原话优先护栏 +2**（kind=todo 槽位盲信 @M3 canonical 抓到，显式「提醒/叫我」不被槽位改写成待办）：A1-4 挂起前缀契约 6（`test_suspend_prior.py`：前缀/单步不变/续接不复读/短回执过滤/loop 跨迭代/流式+种子排除）+ B3-3 记忆 7（M1 场景参数确定性过滤×3 + M2 谓词等价类 supersede + 显式陈述立即抽取 + 归一断言更新×2）+ timeparse 段位默认 6（「日+段位」明早/明晚/明天下午默认时刻成单，裸日期/裸段位仍追问）+ chitchat 墙钟直答 5（`_clock_answer` 词形/口语时刻分段/handle+stream 零 LLM/锚含星期时刻），见「旅程级验证体系」行遗留卡收口段）；此前 **1603 passed, 7 skipped**（2026-07-15 实测，含**旅程红灯修复三批 +24**：R4 回忆式 guard/R9 降级契约改 OK×3 更新/Q3 日期解析×2/Q2 余量边界/R1 强校验×3+城市门×2/R2 中断-恢复契约×4/R3 焦点中心解析/R5 端侧让路×5/R7 REMINDABLE 写入+一刻钟/R8 雨天室内双分支/B2-3 序号回填，见下方「旅程级验证体系」行红灯修复段）；此前 **1579 passed**（scene custom_params 槽位消费修复 +3：真栈 plan 原样 fixture/原话优先/垃圾槽值忽略）；此前 **1576 passed, 7 skipped**（2026-07-14 实测，含**场景编排重设计主题 +161**：scene 单测 159[catalog/store/compiler/solve/verify/triggers/agent 七件套，含评审修复回归 8] + 端侧场景句护栏/分发修根/VAL 氛围灯修复等 edge 增量 + server_dispatch 拒绝浮出用例，明细见下方「场景编排重设计」行与 `docs/design/2026-07-14-scene-orchestrator-redesign.md` §0.5/§0.6）；此前 **1415 passed, 7 skipped**（2026-07-13 实测，含 **badcase ⑥ 天气×出行 +8**（实时意图先答 7 + road-safety 无目的地一般建议 2 - 原 NEED_SLOT 契约 1）、**LLM 消耗归属视图 +1**（collector llm_summary 聚合/SUM 排序/盲区标注）与 **真机 badcase 五修复 +23**（下午续批：合成风控收窄重试 3 + 温度问句让路 5）（grounding 兜底/消毒 6 + chat 4xx 响应体 1 + 天气意图先答 8）与 **LLM 消耗排查主题 +25**：registry 转变沿告警/长期不健康剔除 3 + 批处理音频工厂矩阵/流式桥接/WAV 提取 20 + 合成会话跳过抽取/抽取归属 2；此前 **1335 passed, 7 skipped**（2026-07-12 实测，含**四模式路由与回答质量重设计 +29**：eval_mode_routing 确定性子集与语料 schema、engine escalate 契约 6、chitchat marker/流式缓冲/depth 5、搜索质量（薄证据重试/新鲜度重排/top6）5、研究深化（backtrack 合并/种子跳检/save urls）5、news thinking/livecrawl 2、planner prompt 拼接 1 等，明细见下方「四模式路由与回答质量」行与 `docs/design/2026-07-12-mode-routing-and-answer-quality.md`）；此前 **1306 passed, 7 skipped**（2026-07-11 实测，含**智能提醒 Agent 主题 P0+P1a+P1c 全量 +137**：reminder 单测 102[timeparse 50+lead/store 10/scheduler 6/agent 36 含跨域 8]+sports 生产者 4+集成期改动，明细见下方「智能提醒 Agent」行与 `docs/design/2026-07-11-reminder-*` 三篇）；此前 1169 passed, 7 skipped（2026-07-08 实测，含 **R4.4 P0 拒识 + P1 澄清 +23**：**P0 拒识主链**（置信度三段式「低置信=拒识」：受话判定合并进 Planner 同一次 LLM 调用[`addressed` fail-open]，engine 对 hands-free 语音源[`meta.input_source=voice_*`]+`addressed=false` 静默短路[rejected 卡·空 speech·不 TTS·不落库不进画像]，显式输入永不拒；真栈 `e2e_rejection.py` 2/2、`eval_rejection.py --live` @ mimo 正例误拒 3.4%/负例拦截 88.9%/JSON 0%）；**P1 澄清主链**（「中置信=澄清」：真歧义句出 `intent_choice` 卡[零会话状态·深度=1·`clarify_resume` 抑制]，`_fallback` 语义 top-1 加 `CLARIFY_FALLBACK_MIN` 分数门槛[低分诚实降级不硬执行 capabilities[0]]；真栈 WS CLARIFY on →「处理一下停车的事」出「缴纳停车费 vs 找附近停车场」卡，**CLARIFY off 默认交付零行为变化**；`--clarify --live` 反例误澄清 0/17）；解析/门槛 `test_planning_reject.py` +15、engine 短路/落库/澄清 `test_engine_reject.py` +8；`CLARIFY_ENABLED` 默认 off 真栈 CDP 验收后翻 on；见 `docs/design/2026-07-07-r4.4-rejection-and-clarification.md`）；此前 1146 passed（2026-07-07 实测，含 **多 LLM 源 + TTS 扩展 + 赛事国旗 + 真机修复 +34**：**复杂多意图 3 修复**（navigation 地标 navigate 并发挤高德 QPS 超时→budget 5s→20s + 地标解析走 @fast、Planner 把「像笋」错猜成「京基100」写 dest 绕过解析器→`_correct_planner_landmark` 用原话官方名覆盖 +2、parking-payment 的 `parking.find` 是与 nearby 重复的 mock→移除·停车发现归 nearby 真高德；见 `docs/design/2026-07-07-complex-intent-landmark-parking-fixes.md`）；`llm-gateway/tests/test_llm_runtime.py` 多 provider 注册表/per-provider body 构造/档位解析/全局切换/qwen 复用百炼 key +9、`test_tts_stream.py` 句级切分/MiMo·MiniMax 流式 TTS 工厂路由与 SSE 解析/**MiniMax status=2 汇总帧去重防双份** +9、赛事国旗 +1、`test/test_sports_nearby_routing.py` **赛事追问「那一场…详情」不被周边劫持**（对真实 manifest 跑 RouteHintEngine）+3、chitchat 档位化改断言；真栈四家 LLM 全通（含 **DeepSeek v4 推理模型须 `thinking:{type:disabled}` 关思考防 content 被 reasoning 饿空**）、**MiniMax/MiMo TTS 试听须进 `hmi/src/audio.ts::STREAMING_TTS_PROVIDERS`**、**Windows Chromium 国旗缺字形→自托管 Twemoji Country Flags 字体修复**；见 `docs/design/2026-07-07-llm-asr-tts-multiprovider-and-sports-flags.md`。此前 1112 passed, 9 skipped（2026-07-06），含 **R4.2 服务端流式 TTS +16**（`llm-gateway/tests/test_tts_stream.py`：帧构造/mock provider/工厂路由/FakeWS 全循环，全离线）；2026-07-04 为 1069 passed, 7 skipped，含 **R4.1 路由质量主题**：P0 Registry 真语义路由（+16）+ 语义重排修 P0 遮蔽（+3）+ P3 纯 pattern 扩规则（B1 气象/B2 设置页族，规则改动不新增 pytest 用例）+ R4.1b P0 端侧对象化 3 对象（不新增 pytest 用例，靠 edge_regressions/真栈护栏）——共 +19，详见 §4 末尾 R4.1 行；含 R4.0 收尾包 K1/K2/N1（+2 通道自愈单测 `test_cloud_client_reconnect.py`）；此前 2026-07-03 为 1037 passed, 6 skipped；含 R2 架构还债 R2.1-R2.5 + R3.1 会话鉴权 + R3.2 服务间 mTLS + R3.3 e2e 入 CI 门禁 + R3.4 意图路由评测基线（新增 `test/test_eval_common.py` +7）+ R3.5 降级矩阵自动化（R3.3/R3.5 不新增 pytest 用例，计数不变），详见本表末行「全仓审计与 Roadmap」），以及 2026-06-27 的**信源名单扩展+新闻质量/时效/展示/繁转简**[采纳 `docs/research/2026-06-27-source.md` 扩 tier 名单(官方数据/统计/标准/学术基础设施+权威媒体，仅静态白名单不落运行时评分DB)；新闻二轮收敛：综合要闻走 **Google News 头条+Exa 合并**(Exa 语义检索对今日头条方差大且多返门户版块页、Baidu 多旧闻垃圾)、`_extract_news_subject` 子串判防泛新闻误抽伪 topic 走 Exa、`_rank_news_quality` 沉农场+时效+**来源多样性上限(每源≤3，不按 tier 优先防单源刷屏)**、`_normalize_publish_time` 相对转绝对 ISO+`_recent_only` 丢>3 天旧闻、`_is_junk_news` 按首段滤门户版块名、卡片补 summary(近重复去重)+清「-36氪/｜公視新聞網」尾巴+`clean_snippet` 去 markdown(防「# 中东突变！」)、**繁→简 zhconv**(台/港源标题转简体；先试 LLM 转换稳定 DEADLINE 改确定性库)；真栈「今天有哪些值得关注的新闻」**最佳跑 10 来源/今日/农场 0/话题多样**(Exa 综合查询方差大、稳定多源需策展 News API)]；含**信源质量加权**[`_sdk/source_quality` 域名权威分层 3 学术/官方/百科·2 权威媒体·1 默认·0 内容农场→深调研 synthesize 合成前重排每子问题证据(定 top-N 入材料)+`_assign_global_sources` 全局权威编号([1]=最权威)、共享 `grounded_synthesis` info.search 同源、深度异步薄结果用 Exa research-paper 类目学术兜底；真栈探针前5来源平均档位 1.00→2.80]；含**异步分钟级深调研**[显式延后信号→受理即返回+后台 `deep=True` 流水线（子问题 9/合成 4000 tok）越过 ~90s 同步上限+经 NATS `agent.proactive` 推**带 card 的报告卡**（网关纯 JSON 透传无需改 proto），真栈受理秒回→分钟后 9 节/36 源/~3031 字主动推送；可发现性=同步出报告后 follow_up 主动教「慢慢查、查完告诉我」转异步]；含信息域深调研 P0-P2+实测修复[新闻个性化画像排序/「详细讲讲第N条」深挖桥接 research.run/主动早报雏形(晨间起步发 agent.proactive)；独立 deep-research Agent 四段流水线 + 接地「我」位置反查/画像召回 + 多轮深挖「展开第N点」聚焦不重跑 + 存记忆 + 上线后实测修复(合成关思考防超时退化/去电量约束防主题漂移/去 livecrawl+简短子问题防 Exa 超时/清网页噪声纯文本)，deep_research 20 + 编排 research.run/深挖路由 3 + 端侧「电池」误匹配收窄 2，info 切 _sdk 共享内核零回归]；含 trip-planner 结构化重构 P0/P1/P2；早前复杂任务 thinking 透传/过程区 is_complex 与摘要脱敏单测；含 info/导航 provider、位置授权与反地理、天气预警/空气质量、UI 卡片链路、股票 A/港/美股、Exa 正文级检索+接地合成诚实弃权、api-football 赛事路由（按日期查+中文队名）+「第N场/队名→进球详情」（射手/分钟，剔除罚丢点球）+「射手榜」（topscorers 赛季回退标注）+「总/历史射手榜」改写 query 走搜索+多轮联赛 history 回填、导航顺路用餐 stop_category→waypoint_choice 候选选择→navigate.waypoints+route_plan 路线卡、新闻 Exa 优先+去重、AnySearch extract、搜索/新闻/赛事证据卡、充电高德沿途途经点规划+charging_route 卡、泛地点高德候选二次确认（dest_choice）、导航视觉地标经共享件解析地图官方名+name_matches 校验（拒高德对俗称返回的邻近无关 POI）、类目搜索不被整句多意图劫持、充电按目的地（地标先解析官方名）搜途经点+聚合器并入 navigate.waypoints/去重、聚合器卡片择优、独立 Agent、ws2/ws8、场景动作经 VAL 执行、road-safety 主动播报节流回归、行程规划结构化重构 P0/P1（LLM 提议骨架→确定性接地/求解四段流水线、每停靠点可导航 trip.navigate/下一站、结构化 edit-op 加删站、落 memory，见下方 trip-planner 行）、确认词「占据整句」判定（"行程"含"行"等子串不再误判成确认）、孤儿确认不重规划、跨 Agent meta 透传（定位/电量）+ 子 Agent ui_card Struct→dict 修复） |
| **数据飞轮 P0：修断点立尺子（2026-07-28，候选 M5 首期）** | ✅ **五件全落地**（分支 `feat/m5-p0-data-flywheel`；设计与三问三答定性见 `docs/design/2026-07-28-intent-accuracy-data-flywheel.md`——运行时架构没错，**改进循环的产物是正则不是数据**，规则面:知识面≈40:1，数据→智能回路五断点）。①**evolve 提案半环从未闭合过**：治理③禁区裸词 `"val"` 对提案全文子串匹配，撞上三类草案模板自带的「eval 语料候选」与治理⑥「require_confirm」样板——结构化提案 100% 自触发降级、上线四天 0 产出；修=只扫动态内容（案族原话+归因 note）+词边界，07-26/27 真实 triaged 回填从全纯报告变产出 6 份草案（「记住」族 8 案聚成 guide 草案）；伴生：degraded 信号弃 200 条内存环走 SQLite（collector 重启历史挖不到的根因）、triage 批失败重试一次（07-27 的 8/23 unknown 全因单次瞬时失败）。②**落域可观测**：turns 增 intents/plan_mode 列——collector 在 `insert_span` 收到 cloud.planning 时按 trace_id 合并（turn 事件由端侧收口、天然不含云侧规划信息，存储层汇合与事件顺序无关）；engine span 增紧凑 intents + hint_effect（noop/fill/**fill_over_clarify**/append/replace——D3「hint 盖澄清」的裁决数据）+ catalog_chars/dropped；nightly 日报新增「落域分布」段（path/plan_mode/域计数）。③**标注载体**：turns.gold_intents（与 badcase 同级：UPSERT 不碰、保留期豁免——标注是长期复利资产）+ `POST /api/turns/{id}/label`、`GET /api/export/labels`、`GET /api/intents/observed` + dashboard 标注入口——**一次标注=评测用例+检索范例+训练标注三资产的原料**。④**D1 catalog 预算裁剪**：全量注入（16 agent≤top_k 20 预筛恒 no-op）+8000 字符预算从尾裁「无 route_hints」的 agent——navigation/manual-rag/parking-payment/road-safety 被整域裁出 prompt，保护判据与领域重要性无关，「正常情况下根本不触发裁剪」旧注释假设已随 M3/M4 加 agent 失效；修=预算 16k + 真实 manifests 契约测试固化（兼作预算再被追上时的响铃——转红该走 P2 检索化）+ 裁剪进 span 观测。⑤**示范修复（规则收窄而非增生）**：vision「帮我看看」过宽分支**两侧同步删**（manifest hint + HMI 抓帧触发词——badcase 07-26/27 连续两天「帮我看看附近有什么咖啡店」被劫持，且该触发词会**抓帧+上传**，采集面过宽即隐私面过宽）；收窄后 LBS 句由 nearby 自己的 hint 确定性接住（nearby.search）、真视觉句（含出生 badcase「那是什么」）零回归。**遗留到合并后**：真栈重建 cloud-planner/collector/dashboard 容器、用 catalog_chars 首轮真实值复核 D1 线上表现 |
| **数据飞轮 P1：落域范例库 Exemplar Store（2026-07-29）** | ✅ **修 badcase 的标准产物从正则换成数据**（`skills/exemplars/` 13 域 200 条 + `orchestrator/cloud/exemplars.py` 第三通道 + `scripts/exemplars.py` 三来源 + `test/eval_exemplars.py` CI 阻断 + evolve 第四类提案，`_kw_pattern` 正则生成器退役）。定位=权威链**最软层**：只作 few-shot 不做硬路由，**hint 写错是事故（模型判对了也被 replace 踩掉），范例写错只是噪声**。机制骑 skills 范式，Embed 出口与失败冷却**与 skills 共用一份**（网关挂了两条通道一起回落，而不是各超时一次）。词法用**语料自身 IDF 加权**而非停用词表（裸 Dice 在 5-15 字短文本上被功能词支配，实测「请问现在是什么时间」检回 vision 范例；停用词表＝又一个只进不出的手工规则）。阈值全由 166 例探针扫描拍板（lex 0.34 / sem 0.65）。真栈验证：`出场怎么交钱` 从 `navigation.search_poi` → `parking.pay` 且 require_confirm 闸照常触发。**三条方法论**：①软层 A/B 的 **Δ 只能在实际注入子集上算**（未注入两臂 prompt 逐字相同，首轮 60 例 3 次翻面有 2 次是方差，总 Δ 显负而可归因 Δ=+1）；②**短命进程必须先预热向量**否则 A/B 只测了词法档（N3 首测就栽在这）；③**「一次修复自动传播到同族」的前提是语料密度不是单条魔力**——N3=4/5=80% 达线，但这 80% 靠的是已有范例+语义通道，不能记在新范例账上。顺带修三个真缺陷：grpc aio channel 是 loop-bound 换 loop 静默失效 / 向量预热在短命进程跑不完 / `from-labels` 把 `{count,labels}` 当裸数组读 |
| **数据飞轮 P2：度量驱动治理 + 规则第一次有出口（2026-07-29）** | ✅ **N1 尺子立起来、`route_hints` 32→12**。①`test/routing_bench.py` 分布尺（首测 189/192=98.4%，canonical 100% / **paraphrase 96.5% 为主指标**）——**必须配三条限制读**：可用语料有限且**被排除条数是隐藏分母**（每次都印）、域偏斜前三域占七成（navigation 仅 5 例，**N1 涨不等于车控导航变好**）、canonical 高分是 hint 钉出来的。②`test/hint_retirement.py` **规则的出口**（双臂裸跑，B 臂只摘一条=评测侧过滤 agent 列表，零运行时改动）。判定纪律被数据逼着收紧两次：**抽样→全覆盖**（抽 3 句判 27 条可退役，覆盖全部命中语料降到 21——**抽样偏差方向固定：命中面越大越容易被放行，恰是风险最高的一批**）、**单档→跨 provider 交集**（补 deepseek-v4-flash 后交集 20 条，3 条单档候选被挡下）。实际退役 19 条（`require_confirm` 的 `shop.order` 跳过——**路由评测不构成安全证据**）。③`_always_include` 补 `category: core` 根治 D1（navigation/road-safety 首次进保护集；并修掉「hint 退役会顺手删掉 catalog 保护」这个自造隐患）。④registry 打分换 bigram 归一，**解除 deep-research「desc 刻意不加长」那条倒果为因的历史约束**，性质测试钉住。⑤端云 `_edge_nlu` 透传 + 分歧信号自动进日报（**分歧轮是信息量最大的标注样本**）。⑥evolve 强模型影子分诊（**没有金标时不假装能判对错**）。**三处有意不做并附判据**：catalog TOP_K 不下调（补 core 后 14/16 受保护，检索化没空间）／端侧初判不进 prompt 且**不留 env 开关**（75.9%<91.2% 是负期望；留开关会变成没人测过却随时可能被打开的分支）／8590「可服务率」不可与 Shadow NLU 91.2% 直比（封闭集分类 vs 受能力面约束的真规划）。**两个记账项**：MiMo 无可用 key 做不了第三档而这批 hint 正是为它写的（**切回须复验**）；**召回保护从「CI 阻断」降级为「live 车道人工触发」**（已立卡）。过程修三个自造缺陷：空 `route_hints:` 让 manifest loader **启动即崩**（`.get(k,[])` 对 None 不生效）／迁移判据错把「initial 非空」当护栏（正确判据 `expect==initial` vs `expect!=initial`）／汇总行报候选数而非实际执行数 |
| **Skill 层评审四批：方差分类 + 归因带分 + 锚收紧句首 + env 收尾（2026-07-27）** | ✅ **两项采纳、一项按评审自己的判定树先分类**。①惠州边界句（上轮基线唯一红）：新增 `--only/--repeat` 后固定 provider@minimax 复跑 **3/3 全过——分类为采样方差**（历史 6 轮 1 败）非稳定失败，评审判定树里的重干预不强制；仍采纳其取证与秩序两建议：**归因名单带检索分**（`@lex:23` 词法分/`@vec:0.52` 余弦——「multi-day 是 0.41 边缘共召回还是 0.70 强召回」一眼可判，此前只能靠复跑）+ **渲染/裁剪改按检索相关度序**（此前按 priority 重排：高 priority 弱相关 guide 排到强相关前面放大干扰、预算先裁更相关的；priority 降级为检索同分定序）。②**三批的分句首锚被绕过坐实**（评审实测「剃须刀坏了，快没电了，找地方充一下」照样劫持——前一分句藏主语，`[，。；！？]` 锚形同虚设）：收紧为**句首**（白名单感叹前缀 糟了/糟糕/哎呀/哎哟/完了/不好）或「车」后；倒装句（「附近找个快充，快没电了」）让给 LLM+知识层；剃须刀/助听器负例 + 糟了正例进阻断 pytest（12 负 4 正）与语料（route_hints **116/116**）。③env 钳制收尾（评审两处都实测属实）：`SKILL_MIN_SCORE` 下限 1 不是 0——**score≥0 恒真，钳 0 仍等于全量放行**（无关文本放行全部 guide 实测复现）；`_env_float` 加 `math.isfinite`——nan 穿过上下限比较、`SKILL_EMBED_TIMEOUT=inf` 让超时失效。文档教训：README 冻结 live 数字必过期（16/16 被基线 15/16 打脸）——改为**以 baseline.json 为准**（meta 含跑批条件与 commit）。cloud tests 47 相关文件全绿 |
| **Skill 层评审三批：挂起链最后一环 + hint 正向锚 + 消融因果化（2026-07-27，四项全采纳）** | ✅ **全落地**。①**T2 继承漏链坐实**（评审指认的链路逐行核对属实）：`decision.to_plan()` 新建 Plan `skills=[]`，loop 挂起时传给 suspend 的正是它——「初始→replan→NEED_SLOT→恢复→再规划」仍失忆，二批的 round-trip 测试只盖了手工 Plan；修=loop 内 to_plan 产物继承 `initial_plan.skills`（一行）+ 全链集成测试（`test_replan_plan_inherits_skills_through_suspend_chain`：replan 收到名单/挂起计划带名单/序列化恢复仍在）。②**设备劫持只修一半坐实**：plan 形态漏 guard（「去露营手机怎么充电」$1=露营手机 照样劫持）、find 黑名单枚举不完（游戏机/电动牙刷）；修=plan hint 补设备 guard + find 换**正向锚**（`(?:^\|[，。；！？\s]\|车)` 前置——「手机快没电」里 SOC 短语前是「机」天然出局，黑名单降级为「电动车」类以车结尾主语的保险带）+ **负例钉进阻断 pytest**（`test_route_hints.py` 用真 manifest 断言 8 负 3 正——评审点破 eval 语料在 continue-on-error 步、语料回归 CI 不红）。③**env 范围校验**：`SKILL_MIN_SCORE=-5`（词法全量放行）/`SKILL_SEM_THRESHOLD=1.5`（语义静默全关）/`SKILL_EMBED_TIMEOUT=-2`（必超时）都被原样接受——比崩溃更难发现；修=`_env_int/_env_float` 加上下限钳制+告警+单测（阈值 [0,1]、超时 ≥0.1、min_score ≥0）。④**消融报告三处失真全认**：baseline 记的是父提交且 5 条 expected 过期 / conditional Δ=+1 在族容忍下不成立（当时失败=严格钉 info.weather，族内方差被记成知识功劳，**更正批3行的该结论：仅 navigation 有跨 run 因果证据**）/ 消融失败被计成普通失败拉低总通过率、基线 diff 把「消融变通过」当 improvement——方向全反；修=消融重做为**独立因果指标**（per-holdout `full_pass/without_pass/causal_effect=full∧¬without` 落 baseline `ablation` 键，不进 cases）+ `expect_ablation_effect` golden 键（只准标 hint 够不到的 holdout，未兑现 ⚠ 不 gate）+ meta 补齐 provider/温度/检索档/阈值/SKILLS_MODE + **基线在代码提交后重生成**（baseline.commit=代码 commit）。cloud tests 389 全绿、route_hints 113/113 |
| **Skill 层评审二批：设备充电回归 + 挂起链继承 + 逐 skill 消融（2026-07-27，四项全采纳）** | ✅ **全落地真栈复验**。①**设备充电误接（真回归，评审属实）**：一批引入的 charging.find hint 对「手机/笔记本/耳机快没电找地方充」同样命中（SOC 词形不分主语）——被劫持成车辆找桩；修=guard 补设备词（手机\|手表\|耳机\|电脑\|笔记本\|平板\|相机\|无人机\|充电宝\|电瓶车）+ route_hints 语料负例 ×3（108/108 基线刷新）+ guide 知识边界「只有车的补能归充电域」+ 设备负例 holdout golden（live 实测 → nearby.search ✓ 知识层与 hint 层双守）。②**挂起链失忆（属实）**：`_serialize_plan`/`_restore` 不带 skills——补槽/确认恢复后的 T2 再规划丢知识，v1.11「全生命周期闭合」在该链上超前；修=`plan.skills` 随 `pending_plan` 持久化 + round-trip 测试。③**env 垃圾值崩启动（属实）**：`SKILL_BUDGET=oops` 炸 import；修=`_env_int/_env_float` 回默认+告警（实测 oops→2400 只警不炸），top_k 下限钳制。④**归因升级（其质疑被自家数据证实**：charging canonical 被 hint 钉死后 off 档也过，full/off 分不清知识与 hint 的功）：新增 `--ablate` 逐 skill 消融车道（full − 该 skill 跑其 holdout；Δ=0 自动标「查 hint 覆盖」），每 guide ≥1 holdout 升**静态门禁**；charging 补 hint 够不到的真 paraphrase holdout（「去趟惠州中间要不要补个电」）。**消融首跑归因**：conditional/navigation **Δ=+1**（真知识增益）、charging-canonical/multi-day/freshness **Δ=0**（hint/能力清单已覆盖——车道设计目的达成：知识与 hint 的功劳第一次可分账）；顺带按 multi-day 先例给 conditional golden 天气族容忍（严格钉 info.weather 会把族内方差记成知识功劳）。终态 live **16/16（in-sample 9/9 + holdout 7/7）** vs off 12/16；test_skills 组 40 全绿；单次消融 n=1 属信息性、退役判定需跨 run 证据（README 治理车道 4） |
| **Skill 层评审补强：容错闭合 + T2 继承 + holdout 门禁（2026-07-27，外部评审六项逐条核实后裁决）** | ✅ **采纳 4.5 项、驳回 3 子项，全部落地真栈复验**。**核实成立并修**：①两个真崩溃（评审复现属实）——skill 文件顶层写成数组 `AttributeError`、`priority: high` `ValueError`，都发生在 fail-open try **之外**，一个坏文件打穿「坏文件跳过不崩规划」承诺；修=解析全链兜底 + 非法标量回默认告警 + **重名先到者胜** + 目录/type 不一致告警 + **last-known-good**（热更新改坏沿用上一版，删除才下线）；运行时宽容 vs `eval_skills` 文件级校验车道**硬失败**双轨（坏文件到不了 main）。②CI 门禁不真——eval_skills 在 `continue-on-error` 观测步里；修=拆独立阻断步 `Skill contract gate`（「不阻塞先观测」只适用于会漂移的意图基线），顺带补漏 **scripts/tests、proactive/tests 两个从没进过 CI 的分组**。③golden 全 in-sample（评审点数 4/10 原句重合，实核更多）——增 `holdout`（词法盲区真改写，检索车道跳过、live 按 in-sample/holdout 拆分报告）与 `expect_complexity`（adaptive 类知识的核心主张可断言）。④T2 replan 失忆——`replan()` 按 `plan.skills` 名单重渲染继承（`render_for_names`；shadow/`!clipped` 不进；两处 mock planner 签名同步）。⑤部署接线——compose 挂载 `skills/:ro`（容器投文件 30s 生效不重建）+ SKILL_* 以 `${VAR:-}` 空默认透传（默认值只活 skills.py 一处，代码侧 `or` 兜底空串）。**驳回**：Pydantic（stdlib 校验够用不加依赖）、skill 版本 hash 钉进 Plan（replan 与初规划相隔秒级 YAGNI）、步骤级/槽位级全断言（provider 方差下过脆，只收 complexity）、WorkflowTemplate 现在建（维持后置三条件，评审提议的首个 workflow「天气→条件提醒」正是 conditional-reminder+adaptive 已覆盖的流程）。**holdout 首跑当场抓真 bug**：「看下明晚会不会下雨，要下就叫我收衣服」被 reminder「叫我」hint（replace）劫持成只剩 `reminder.create`——**比无知识档还差**（LLM 规划对了被 LLM 后的 hint 踩掉，guard 只认「要是/如果」不认无标记条件句）；修=guard 补 `会不会/有没有/下不下`。**采样方差实证**：「去惠州怎么充电」在 temp 0.3 下跨 run 偶发翻 nearby——charging 教科书形态按 R2.1 惯例 hint 化（`去(X)怎么充电`→charging.plan 带 $1 destination / SOC 焦虑找桩→charging.find），**「canonical 归 hint、paraphrase 归知识」分工口径入册**（skills/README+架构 v1.11）。终态：live full **14/14（in-sample 9/9 + holdout 5/5）** vs off 8/14（Δ=+6，off 档两条条件句正确暴露 `complexity=simple≠adaptive`）；route_hints **105/105**（+4 用例）基线刷新；test_skills 19→25；cloud tests 385 全绿 |
| **Skill 层闭环补全：检索双通道 + golden 消费方 + 知识扩容（2026-07-26，泓舟点题「skill 落地不完善、体验没达智能化预期」）** | ✅ **审计→机制→eval 拍板→live 证据→真栈部署全链闭环**。**审计五断裂**（对照 §4.A 与验收立卡）：few_shots「文档有代码不读」/ golden `expect_intents` 零消费方 / eval_skills 不在 GitHub CI / `plan.skills` 名单渲染**前**生成（超预算被裁也谎称已注入）/ 检索纯词法且「embedding 升级由 shadow 数据决定」的数据车道不存在；另：M0b 后零新增 skill（机制建成没人投过新文件）。**机制**（`orchestrator/cloud/skills.py`）：检索双通道 `SKILLS_RETRIEVAL=lexical\|hybrid`（默认 hybrid——词法命中恒保留、语义按 guide description 余弦**补位** paraphrase，经 llm-gateway Embed 与 registry 同源；**fail-open**=Embed 挂/超时回词法+30s 冷却；同轮同模型嵌入、网关换 embedding 模型缓存整体失效重嵌）；few_shots 解析+渲染（plan dict→紧凑 JSON 输出形态示范）；未知顶层键告警不拒载；`render_skills_block` 返回 (block,injected,clipped)、名单**渲染后**生成（`@lex`/`@vec` 通道 + `!clipped` 被裁标注）；`plan_skills` 转 async（planning 调用点 await；根 conftest 钉单测 lexical 保离线确定）。**eval 先行拍板**：新 paraphrase 语料 11 条（逐条避开 keywords 字面）——词法 **0/11** 坐实盲区；阈值扫描 **0.40=11/11 召回且零新增案例噪声**（0.45→9/11、0.35 噪声 1/8→3/8）→ 默认档 hybrid + `SKILL_SEM_THRESHOLD=0.40`（description 补「典型表面形态」当语义索引救回剩余漏召）。**live 车道（golden 消费方）**：真 PlanBuilder+真 LLM@minimax、catalog=真 manifests+边端合成（hvac.\* 真在清单上）；契约=expect_intents AND（项支持 `a\|b`）/expect_any/expect_not + 静态校验（expect 意图必须真实存在——typo 守卫）；**full 10/10 vs SKILLS_MODE=off 5/10（Δ=+5）**：充电 plan/find 分流、导航顺路单步、导航+充电两步、「我有点冷」→hvac 全是知识翻正；基线 `docs/reviews/eval/baseline_skills.json`。**live 首跑抓到分层真 bug**：「快没电了附近找个快充」few-shot 教对了仍输——nearby 设施发现 hint（`policy: replace`）在 LLM **之后**踩掉 charging.find，guard 漏「没电/电量(低\|不足\|不多\|不够)/补电」SOC 线索；修 nearby manifest guard（纯发现「附近有没有充电站」仍归 nearby 富卡），eval_route_hints 101/101 无回归——教训入册：**「知识不生效」先查 hint 层再改知识**。**知识扩容**：首个净增 guide `charging-strategy`（charging 无 route_hints 路由纯 LLM + target_b 校准佐证「去X怎么充电→charging.plan 6/6」；few_shots 吃自己狗粮）；**scene-automation 候选否掉**（scenes.yaml 无触发器机制，「每次上车自动…」没有真实承载物——知识必须真）；golden 契约迁移=implicit-vehicle-control 改 expect_any（三互斥意图 AND 语义本来就错）+ freshness 放宽 `info.sports\|info.search`（policy 主张是「必须联网」，族内选择归 mode_routing 语料管）+ conditional-reminder 补 expect_not: reminder.create（知识的另一半）。**登记**：eval_skills 进 ci.yml intent-eval-baseline（离线车道零网络）；.env.example SKILL_\* 六项注释登记（活跃行压默认值教训）；compose `SKILLS_RETRIEVAL` 透传；skills/README.md 契约重写（双通道/三键/归因/三车道/实装记录）；架构 **v1.10**（§5.2.1 闭环补全+分层边界实证）。**同路未动**（维持 P2 卡）：toolcall provider 能力位、`PLANNER_TOOLCALL` 热切不对称——属 submit_plan 侧。验证：test_skills **19**（few_shots 渲染/未知键告警/裁剪诚实/语义补位/fail-open/名单契约/防倒灌指纹）+ 全量 **2304 passed, 7 skipped** + 离线 eval PASS + live 10/10；坑=CLI `--retrieval` 默认值悄悄把 live 档钉死 lexical（对 env 开关的隐性覆盖，default=None 才对）、ProviderLock 端点是 `/api/llm/providers` |
| **声纹面真机第四批 + 乘员维度盘点（2026-07-27；RFC §11.8.2/§11.8.3，架构 v1.13）** | ✅ **泓舟真麦确认可区分**。第四批两条：①**「试一试」压根没跑识别**（四次调用零判定日志，卡在 `too_short` 早返回——它只录 2 秒，切掉头尾静音后不足 1.5 秒**有效语音**，自证功能变成永远说「太短了」；修=录音窗与注册同长 4 秒）。**改了「有效时长」口径就要回头看所有依赖时长的地方**。②**识别对了但答案被对话历史盖掉**——受控复现：新会话注入泓舟答泓舟 ✓、会话里先聊过阿灵再注入泓舟答「你是阿灵呀，刚才不是说了嘛」✗；**先试加强提示词，两个方向各两次全错**→改**确定性直答**（`chitchat._identity_answer`，同 `_clock_answer` 一族）后全对。**系统已经知道的事实不交给 LLM 回答**（本仓库第三次）；**prompt 打不过上下文**。③按泓舟要求**修了拒识误伤**：句首祈使前缀（记住/记一下/别忘了/帮我记…）**确定性判为受话，不问模型**，两次都判不受话才落 chitchat 兜底——真栈 8 条祈使记忆指令 **0 空回**（修前同一句某次 3/3 被拒）。④**乘员维度全面盘点**（临时乘员 occ-8/occ-9 实测后清理）：**通的**=记忆写入归属 / 跨乘员读隔离 / 召回层过滤 / 关系边隔离 / **权限红线（occ-8 车控照常 need_confirm）** / routine 按乘员；**没通的四处已立卡**（places 不分乘员=RFC 宣传「常去地点各自独立」未兑现 / reminder 无 occupant / HMI 记忆面板与「忘掉全部」不分乘员 / 端侧快路径 AppendTurn 不带身份）。⑤清理了识别错乱期写下的三条串味记忆（泓舟名下的「用户自称阿灵」等） |
| **声纹面真机第三批：两个人的声音还是认成同一个（2026-07-26；RFC §11.8）** | ✅ **三层原因全修，真人分数已可见**。不是模板问题（两模板互相余弦 0.27、自洽度 0.834/0.822）。①**探针里大半不是人声**——`vad.onFrame` 是原始帧旁路**不做语音门控**，识别器却按「累计 1.5s 有效语音」的语义直接数帧；真机时序是「唤醒→播『在呢』→用户才开口」，嵌入被稀释到谁都认不出→恒回 primary。修：只收 VAD 语音段的帧（controller 转发 `onSpeechStart/End`，`arm` 带「此刻是否已在说」）+ **端点补发一次**（短问句约 1.2s 攒不够 1.5s，否则从「认错」退化成「永不识别」，症状一样）。**契约 §9.11 原文就写着「有效语音」，实现拿到的却是不含门控的原始帧——同一个词在两层不是一件事，而两边都是 `Float32Array`，类型系统看不出来**。②**阈值方向标反**——真人同人 0.5243/异人 0.1157，0.62 把同人一并卡掉；合成音色异人高达 0.65（TTS 共享信道特征）逼阈值上抬，真人分离度好得多反该下放 → **0.62→0.45**，真人值钉成回归测试；margin 不动（一个数据点不同时拧两个旋钮）。③**判定在生产里完全不可见**——identify 无日志，且 collector 的 `apply_metric` 是固定键白名单把 `vp_*` 全丢了（RFC 承诺的「四态进 obs 供 M1b 挖掘」**实际没落地**，已立卡）→ 网关补一行、memory 补一行**全量排名**（降级时 top1 身份本会丢，那正是调阈值最需要的数）。④拍板**认不出就不叫名字**：`occupant_id` 照旧回落 primary（记忆归属对），但称呼是断言，只有 `accept` 才下发，一处收口 classic/S2S 共用。**多人测试须换人重新唤醒**（同窗内续问/打断刻意不重识——本批三条 trace 里泓舟那条是 `voice_bargein`，压根没触发识别） |
| **声纹面真机第二批：删除失效 + 名字被冲掉（2026-07-26；RFC §11.7）** | ✅ **两条真机反馈全修，真栈复验**。①**删除失效的根因是 CORS 不是删除**——`Access-Control-Allow-Methods` 只有 `GET, POST, OPTIONS`，而声纹删除是全 HMI 唯一的 `DELETE`，浏览器 preflight 直接挡下：**服务端零日志、`curl -X DELETE` 成功、e2e 全绿**（e2e 从服务端发，不过 CORS）。修法是让白名单与 `app.router` 注册的方法自动对齐（`llm-gateway/tests/test_http_cors.py`），不是补一个方法。②**名字不是没写进去是被重录冲掉的**——库里 4 条 superseded 的「泓舟」+1 条现行「乘客」：HMI 空称呼静默兜底成「乘客」× 服务端 upsert 无条件覆盖 × **没有改名入口所以用户反复重录**。四处一起修：称呼必填 / 空名按「不改名」处理 / 同名重录不重复写 `identity.name` / 新增 `RenameVoiceprint`+`PATCH /api/voiceprint/{occ}`。③顺带两处不诚实：删 primary 的确认框承诺「忘掉全部记忆」而服务端永不 purge；删掉模板后注册写的名字残留（改为逐字撤回注册自己写的那条，不误伤对话里说过的身份陈述）。真栈复验：preflight 回 `GET, POST, PATCH, DELETE, OPTIONS`、PATCH 改名后表与 `identity.name` 两处同步 |
| **M4：端到端语音 S2S（2026-07-25，P0→P3；子 RFC `docs/design/2026-07-25-m4-s2s-fullduplex-rfc.md`，§3.5=协议冻结基线 / §11=落地记录）** | ✅ **P0→P3 全落地真栈验证**（P4 声纹+视觉未实施）。**选型是被实测钉死的不是被文档定的**：`session.created` 不校验 model（传假名字也返回 session、只 echo 名字回落默认配置），逐个实测 tools 支持度才发现 `qwen3-omni-flash-realtime`（无 `.5`）**静默丢弃 session.update 的 tools**——车控句它口头自答「我可以帮你开空调」而不移交，单工具契约在它上面整体不成立；**两个型号的官方文档都写「支持函数调用」**。工厂对该族 fail-fast 拒绝。**P0 协议探针** `e2e_s2s_probe.py` 七组 12 断言 → 冻结事件映射表 + 量测（首音频 P50 609ms/max 703ms、首文本 P50 328ms、输出 24kHz 靠字节数反推——协议不声明采样率）；额外挖出两个 RFC 未列的风险点：**R1** 回注 `function_call_output` 不发 `response.create` → provider 完全静默（「逃逸轮结果只为上下文连续不为播报」的前提成立；若自动续说就与主链 TTS 双播、设计整体返工）、**R2** 悬挂 `function_call` 不坏会话（回灌失败无需补偿）。**P1 最小闭环**：网关 `llm-gateway/s2s/` 四层（对上协议/provider 抽象+qwen 实现/L-Session 状态机/回灌）+ `/api/s2s`；HMI `s2sClient.mjs` + 一组新效果回调 + 设置挡位。**voiceLoop 一字未改**——`onOpenAsr` 从「建 ASR 连接」变成「开收音门」、`onEndpoint` 从「请引擎定稿」变成「请 provider 收尾」（**当初错做成空操作，就是下面那个真机死锁**）；R4.3b 的退出词/语气词/误唤醒本地治理靠 **`onMetric` 语义事件总线**接住（FSM 判为噪声的句子额外发 `barge_in`，否则一句「嗯」会被 S2S 当一轮答出来）。**协议补一个类型** `escalated_result`：原设计漏了「逃逸轮执行结果怎么回 S2S 会话」，不补则多轮连续性断在每个逃逸轮上。**收音门控比原设计更保守**=只在 LISTENING 期推流——provider `interrupt_response=true` 会自主判打断，SPEAKING 期推流就与「本侧权威」打架；**不给它输入才是本侧 100% 权威的实现方式，靠约定不行**。**真栈才暴露的三缺陷**：① turn 悬挂无收口（客户端慢读→下行 send 背压→事件泵阻塞→provider 侧数据丢→该 turn 永不 done，HMI 干等到 voiceLoop 100s 兜底）→ 加 turn 看门狗诚实收 `turn.end(error, provider_silent)`，**不追究成因、任何原因导致的悬挂都在此收口**；② 重连直接覆盖 `self.provider` 泄漏 aiohttp ClientSession（由「Unclosed client session」暴露）；③ e2e 自身「先推完再读」不是真实客户端行为（浏览器 WS 总在读），顺序写法自造背压把整轮回答弄丢。**④ 真机首验的死锁（最严重，`f7ad60b` 已修）**：s2s 下 `onEndpoint` 被做成空操作，理由写的是「provider server VAD 自驱轮次」——但 server VAD 靠**连续静音输入**判端点，而 HMI 在本侧 VAD 判到端点后就停止推流，provider 永远等不到静音：turn 开了却永不收束，**泓舟真机上「说什么都没有回复」**（日志一串「turn 悬挂超 45s」）。修法=端点判定权留本侧（与 classic 的 `onEndpoint → asr.stop()` 同构）、经新增上行帧 `audio_done` 请网关收尾，`commit_audio()` 按厂商补静音尾（长度须 > `silence_duration_ms`）。**两条该避免它的教训**：① **答案在代码和记忆里都有却两处都没查**——`DashScopeRealtimeASRProvider` 注释写着「须 > silence_duration_ms 才生效」，`asr-streaming-redesign` 记忆也写着「流末追静音」，**同一个 realtime 壳的坑在同一个仓库里复发**；② **e2e 把它整个掩盖了**——脚本自灌 13 帧静音，替生产代码做了它没做的事。**e2e 模拟客户端就得只做客户端做的事，生产缺的那一步必须在测试里也缺出来**；三个脚本已全部改走生产收尾路径，改完同一套断言立刻复现死锁。**P2 的 journeys lane 改道**：RFC 原写「journeys 新 lane 4 条」，但 journeys runner 是文本驱动、跑不了音频通道 → 改在 `e2e_s2s.py` 覆盖四形态，断言更强（能验残包与回灌）；中途断连用**宿主内 WS 代理**注入（不改 `.env`、不给生产协议加测试后门）。**P3 灰度门槛** `eval_s2s_escalation.py` + 24 条语料（对抗重点是**夹在闲聊里的动作句**「今天真热啊，把空调开低一点」，直白句谁都判得对）、配置生产同源 → **移交率 14/14=100%**、自答 10/10、613ms/轮，门槛 ≥95% 达标。验证：全量 **2189 passed/7 skipped**（+67 零回归）、HMI node **171**、`e2e_s2s` **25/25** + `e2e_s2s_resilience` **11/11**（断连 625ms 重连+摘要重注入+重连后记得断线前的话），两个新 e2e 已挂 run_e2e。**未做**：声纹/视觉（P4）、真麦声学验收、重叠对话（明确不做，v1 回合制） |
| **M4 P4：声纹多用户 + 视觉入口（2026-07-25/26；子 RFC `docs/design/2026-07-25-m4-p4-voiceprint-vision-rfc.md` §11=落地记录）** | ✅ **两条线全落地真栈验证；M4 两条 DoD 至此全部兑现**（「多用户记忆隔离旅程」由本批兑现）。**侦查推翻了这一期的重心**：记忆层的 occupant 维度早就全套就位、`recall` 本来就是 occupant 精确过滤——**缺的从来不是记忆能力，是身份来源和一条透传管道**（`PlanContext`/`_sdk.Context` 里压根没有 `occupant_id` 字段）；于是 memory 侧零改动，只把既有字段沿既有 meta 管道多传一段（七个改动点）。**P4a 声纹**：网关 `speaker_embed`（音频→192 维向量）× memory `voiceprint`（向量→是谁 + 模板表 + GDPR 级联删）**分家**——模板绝不下发到无状态服务，生物特征扩散了就删不干净；识别走**独立端点不旁路 ASR/S2S 流**（那两条刚验完、S2S 上批才踩出端点死锁，可选增强件不该焊进关键件，代价是首句 ≤96KB 重复上行）；**边说边识别**（累计 1.5s 即发、此刻用户还在说，send 前软等 ≤150ms，绝不为认人拖慢首字）；**一次唤醒锁一次**（轮内改判会让同一段对话前后半截落进不同乘员，**比认错更糟因为同时污染两个人**）；**四态判定，accept 之外一律回 `primary`**（不是 guest——那等于凭空造一个空记忆空间，用户体感是「车失忆了」）；**首个注册者绑 `primary`**（存量记忆全在它名下，首个注册者若拿 occ-1，他自己过去说过的一切当场失联——堵在分配这一步，不做事后迁移）。**探针把三个阈值从经验值钉成实测值并推翻一个想当然**：4 音色×6 句的单句余弦分布**是重叠的**（同人 p5≈0.49 / 异人 p95≈0.65，同性别合成音色互相很像、冰糖×茉莉中位 0.59），但端到端识别（三段均值模板 vs 单句探针）在 thr∈[0.45,0.70] **结果完全相同**——**真正起作用的控制量是 margin 不是 threshold**；两次跑认对 83%/100%、**认错率恒 0%**（混淆对被 `ambiguous` 档拦成 primary 而不是认错人，「分不开就别分」在真数据上兑现）。`min_consistency` 按实测从 0.55 下调 0.50（**原值高于合格注册下界 0.532，会误拒合法注册**）。另：管路自检时纯音之间 cos=0.91 曾让我担心噪声撞模板，真探针里静音/白噪只有 0.01~0.17——纯音是「有结构的周期信号」才被映到相近区域。**红线「声纹不作鉴权因子」由源码级断言 9 条钉死**（`occupant_id` 不得进 granted_scopes/权限/VAL/确认闸/支付）；理由不止「可被录音重放」——**身份识别与授权是两件事**，识别错了只该损失个性化。**P4b 视觉**：新 Agent `agents/vision`（50077，`vision.describe`）+ 网关内存帧库；**图像永不进对话链**（proto 里只有 16 字节 `frame_id`，图像本体 TTL 120s 内存 LRU、**不落 Redis 不落盘**——Redis 会持久化到磁盘等于把车内外图像写进存储；meta 塞 base64 还会整条进 obs 采集）；**采集门控在端侧**（命中触发词才抓一帧、默认一帧都不采、抓完立刻关摄像头——后端要图再回头拍是既慢又错的形态）。**两条被实测逼出来的设计修正**：① **看图必须走独立 `qwen-vl` 档**——实测 `qwen3.7-max` 对多模态 content **直接 400**（`Unexpected item type in content`），而 `resolve_models_for` 对不认识的模型名是**静默回落 primary**，不独立成档就会把看图请求打到看不了图的模型上**且不会有任何报错**；② **帧过期必须显式失败**——网关静默只发文本时 VL 模型答「看不清，画面有点模糊」，**它在假装看到了一张模糊的图**，比说不出更糟（用户没法判断真假）→ `FrameUnavailable` → `FAILED_PRECONDITION`，Agent 明说「刚才那一眼已经过去了」并与「模型挂了」分开说。**新 scope `camera.frame` ≠ `camera.read`**（后者是连续流、conventions §3 维持 ❌ 禁），沿 `location.read`/`location.precise` 精度分级先例——**不为了让 Agent 可路由就把连续流权限偷偷放开**。**实施中修掉的缺陷**：① `occupant_id` 透传断在 **prefs 白名单**（真栈才暴露：记忆全落 primary、隔离形同虚设），同类问题在 `vision_frame_id` 上重犯一次、第二次顺手挂进 `_SENSITIVE_SCOPE` 做最小化下发；② `.gitkeep` 被自己的 ignore 规则吃掉（`models/**` 排除父目录后 Git 无法重新包含）→ **新克隆 `COPY models` 会构建失败**；③ **模块同名劫持两次**（`voiceprint` 与 `server`，均单独跑绿/全量跑红，与 providers 通用包名劫持同族）；④ 源码级断言把**我自己写的注释**判成违规（查「不得出现 Redis」命中「不落 Redis」）——**源码断言要查用法不是查行文**，改成先 `ast` 剥文档字符串。验证：全量 **2266 passed/7 skipped**（M4 S2S 基线 2189，净 **+77**）、HMI node **193**（+22）、tsc 21=基线、build 通过；真栈 `e2e_voiceprint` **7/7**（含「存量记忆 11→11 一条不少」「B 的偏好主驾查不到」「换乘员后后备箱照样要确认」）+ `e2e_vision` **6/6**（纯绿图→「这是绿色」、纯黄图→「是黄色的」），两个新 e2e 已挂 run_e2e（模型/key 缺失自动 SKIP）。**未做**：跨乘员共享记忆（拍板 v1 不做）、真麦声学验收、声纹语音注册入口/模板漂移巩固/视觉多轮追问（均 v2）。**真机三修（2026-07-26，`d45289e`）**：①**我引入的回归**——为等声纹结果给控制器 `onSend` 加 150ms `settle()` 软等待，把它变成异步，破坏了「真实用户气泡由 send **同步**接管」的不变量（`voiceLoop._finalizeSend` 是先 onSend 再进 THINKING，App 的 `onOrbState` 离开 LISTENING 即清 ghost 靠的就是它）→ 对话窗回答与问题错位。**修法是整个删掉不是缩短**：识别在说到 1.5s 时发出、端点还要再等 800ms 静音尾，这 150ms 几乎不生效——**为一个几乎不生效的优化牺牲一条不变量是亏的**；node 测试加护栏挡它被加回来。可复用判断：**给既有回调加 await 前先问调用方有没有依赖「同步完成」**，FSM 效果回调尤其危险（后面通常紧跟一次状态迁移）。②**名字只写在 `voiceprint` 表里答不出「你知道我是谁」**——那张表除了识别比对没有任何消费方会读；补 `EnrollVoiceprint` 写 `identity.name` 记忆（改名 supersede、删乘员随记忆一起没）+ `occupant_name` 沿同一条 meta 管道注入 chitchat。可复用判断：**存下来 ≠ 用得上**，新增表先问「谁会读它」。③**注册交互**缺的是「进行中反馈」与「完成后自证」两件事而非美观 → 三句独立成行带倒计时与状态、可单段重录、**「试一试」录 2 秒当场回答听出来是谁**（四态各有话术）；identify 端点因此也接受 webm。验证：pytest **2270**、node 192、`e2e_voiceprint` **8/8**（+「你知道我是谁」）、journeys 14/14。 |
| **M3：统一主动引擎 + 位置提醒 + 受控 MCP 桥（2026-07-25，P0/P1/P2 单日完成；子 RFC `docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md`）** | ✅ **P0/P1/P2 全落地真栈验证**（§9 三决策点泓舟拍板：独立服务 / 三期全做 / 免打扰默认不启用）。**P0 统一主动引擎**（新服务 `proactive/`，契约登记 conventions §9.8）：治理器上线前六个生产方各自直发 `agent.proactive`、节流各自为政、跨生产方零协调，免打扰/驾驶负荷/同类合并**一个都不存在**；现在收敛到六道闸+合并窗口+延后队列。**落点判据是「它必须可以随时死掉」**——生产方经 `runtime/proactive.py` 走 NATS request/ack，没人 ack 就直发老主题（真栈手验 `NoRespondersError` 毫秒级回落，低电量建议照常到 HMI），停容器 = 一键回退。**单条通过 = 剥治理键后原样转发**（Go 网关与 HMI 零改动的字节级保证）。**情境断言在投递时刻三态复核，unsat 与 unknown 一律丢**（生产方声称的前提证实不了就不替它说，顺带解决陈旧建议）；**驾驶负荷闸的 unknown 故意放行**——唯一背离「unknown 不打扰」处，理由是镜像冷启动最长一个快照周期全空，拿缺数据定罪会把主动链路静默掐死一分钟。**零 kind 字面量**由源码断言钉死、**迁移不许漏**由全仓字面量断言钉死。**DoD 场景补齐**：此前没有任何生产方会因为低电量说话（road-safety 只看天气预警）→ 新增 charging-planner 低电量顺路建议，与 scene 低电量触发同窗到达合并成一条（真栈实测一条消息里两件事）；拿不到桩就整条不发（铁律③）。**P1 位置提醒**（reminder P1b 断链）：`kind=location` + 地点数据存既有 `extra` JSONB（**不加新列**——只在按 kind 选出后被读）+ ETA 族词形主动让路（防两条链路抢同一句话）+ 地点四级解析（画像→关系边→nearby 真解析坐标→落空）+ **解析不出诚实追问，绝不存永不触发的提醒** + **首次观测只播种不触发**（人已经在目的地时创建，立刻响一声不是用户要的）。**P2 受控 MCP 桥**（`agents/mcp_bridge/`，契约登记 conventions §9.9）：三重准入=版本逐字锁定+tool 白名单（server 多提供的直接忽略）+schema 指纹；**写操作五项缺一不接**=幂等键**用请求指纹不用 task_id**（task_id 每次都新等于没有幂等）/订单状态机复用 `task_ledger` 不建表/timeout 报「不确定」不假装成败/`compensate_tool` 缺失即拒载/审计进结构化日志；演示商户**三重诚实标注**（`_prov.mode=mock`+卡片角标+话术前缀）。**第三方互操作已验**：官方 `@modelcontextprotocol/server-everything` v2.0.0 握手/列表/调用全通，并确认 MCP 区分「协议错误」与「工具执行错误（result.isError）」两条路径（我们两条都处理）。**顺手修的存量缺陷**：road-safety 把每 30s 全量车况快照当成「进入新区域」，一个查不到的地名每 30 秒打一次和风 400 → **打开共享的 qweather 熔断器** → 天气域整片垮（journeys B1-4/B3-4 因此变红），改为位置真的变了才评估。**编码期五处设计偏差记账**（RFC §10.1）：删掉无消费方的 `merge_group`、免打扰默认改空、位置提醒不加新列、MCP 槽位词表与工具参数名经 `arg_map` 解耦（LLM 自然填 `item` 不填商户内部词 `sku`）、road-safety 存量修复。验证：全量 **2122 passed/7 skipped**（+81 零回归）、`e2e_proactive` 16/16 + `e2e_geofence` 7/7 + `e2e_mcp` 10/10（均已挂 run_e2e）、journeys regression **14/14 @minimax**、`eval_route_hints` 101/101。**未做**：治理器无持久化 / 合并话术确定性拼接（LLM 改写留 v2）/ `obs.proactive.decision` 无消费方 / 位置提醒依赖 debug 注入的 location（PoC 无真实 GPS 流）/ MCP 首批是演示商户 |
| **M2 后半：记忆图谱（偏好加权 + 关系边 + 生命周期强制项）（2026-07-25，同日紧接核心件；子 RFC `docs/design/2026-07-25-m2-memory-graph-rfc.md`）** | ✅ **P0/P1/P2 全落地真栈验证**（§8 三决策点泓舟拍板「都按你建议的来」）。**对母提案 §4.D 的两处修正已兑现**：① **不建 `preference` 新表**——字段级对照后真缺口只有 weight/evidence_count/half_life 三个，其余（predicate/confidence/**source_turn_ids 证据溯源**/superseded_by/privacy_level/occupant_id）`memory_item` 全都有；建表会推翻 2026-06-25 刚做的「两表合并为单表」决策，且 supersede/隐私分级/GDPR 级联/召回打分要重写一遍（极易漏级联删除）。② **emotion 不进记忆层**——母提案自己给的约束（短 TTL+不入画像+需授权）已把它排除在记忆之外，而真正缺信号的是 M1b 已就绪的情感 TTS 参数面（要的是当轮情绪不是画像）。**P0 偏好加权**：`memory/weighting.py` 纯函数（**显式偏好不衰减**——用户明说的凭什么因为久了就不算数；推断类 90 天半衰期；重复证据每次 +0.1 封顶 +0.4，让「每周三次点川菜」0.7 反超「说过一次爱吃辣」0.6）+ 巩固期**「等价→加权」取代「等价→跳过」**（今天的跳过让重复出现完全不留痕，是 C4 根因）+ 就地更新**不刷新 `valid_from`**（那是衰减基准，刷新等于把陈年偏好洗成新的）+ 冲突 supersede 时**继承旧证据链** + 召回打分与注入两段式渲染（确定性人话强度词「常用/明确说过/偶尔提过」不进 LLM，top-N 3→5）；**存量兼容是硬要求**：weight=0（M2 前全部条目+非 semantic）逐字回 confidence 口径，契约测试锁死。**P1 关系边**：`memory_relation` **独立成表**（subject 非用户、查询是实体双向精确查而非相似召回）+ **封闭 rel 词表**（family/place_of/works_at/lives_at/owns/prefers_brand，词表外一律丢弃不猜）+ 抽取 `_relation` 保留键分流 + `QueryRelations`/`ResolvePersonPlace` 两 RPC + navigation 人称目的地消费——**「导航去接孩子放学」→ 廊坊阳光小学真栈走通**（母提案 §1.2-E2 的 Eva 例子）；**查不到/有歧义一律诚实追问绝不猜**（导航到错学校比问一句更糟）。**红线**：GDPR `forget_user` **同事务级联删关系边**——只删 memory_item 的话，家人关系与孩子学校（最敏感的那部分）留在库里就是假删除；`ExportUser` 同样带 relations（导出必须与删除对称）。**P2**：routine 时间加权（半衰期 30 天，比偏好的 90 天短——习惯本就该更快过期；**两段判据**=裸频次够 + 有效计数没凉透，直接拿衰减计数比阈值会让「连续三天」卡在 2.93<3）+ planner emotion → `FinalResult.emotion` → HMI 存**下一轮**的 TTS 语气（本轮 TTS 在 final 前已开播，当轮改不了）；**emotion 走 prompt-only 不进 submit_plan schema**（B4-1 教训复用：模型对 schema 结构的响应强于 description，旁路字段不值得冒行为漂移风险，契约测试锁）。**真栈三坑（只有真栈才暴露）**：①亲属称谓自然语言变体——LLM 抽成「用户的女儿」而查询侧是精确匹配（刻意的，模糊会把「小雨」匹到「小雨点」）→ 存成变体就永远查不到，修法是入库归一；②口语说「接我妈」不说「接妈妈」——词表漏裸「妈/爸」，更隐蔽的是 filler 词表漏「我」致剥完剩个「我」被当实质内容、整条链路静默不触发；③播报要用自然称谓（「不知道**妈**平时在哪」读着别扭）。验证：全量 pytest 零回归、`e2e_memory_graph.py` **五场景全绿**（偏好加权/关系边入图/人称地点解析/诚实追问/**GDPR 级联删除红线**）已挂 run_e2e、journeys regression 13/14+1 数据真空 skip（两次跑各红一条不同的、都单跑即绿=既有方差：A2-3 充电编织概率性、B3-4 和风 provider 瞬时不可达）。**未做**：`consent` 列 v1 只写不读（消费在 M3/M4）、多跳推理/实体消歧/跨用户关系（无消费方）、偏好加权不追溯存量（只在再次巩固时进入加权体系） |
| **M2 核心件：Task Ledger + Outcome Verifier + T2 分档（2026-07-25，单日完成；子 RFC `docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md`）** | ✅ **P0/P1/P2 全落地真栈验证**。**P0 Task Ledger**（`agents/_sdk/ledger.py` + PG `task_ledger`，契约登记 conventions §9.6）：长任务从黑箱变成「谁在替用户干活、干到哪了、还让不让它干」的权威记录——**Background 档六守卫的三张空头支票（deadline/cancel/预算）全兑现**；挂在 `BaseAgent.self.ledger`，**接入=调三个函数、编排核心零改动**（对母提案「编排器侧新模块」的落点修正，理由子 RFC §2.1）；cancel 走**拉模式**（心跳搭车读状态，零新通道）；PG 不可达时诚实降级（照常干活，只是话术不承诺可停可问，**刻意不做内存兜底**——账本的价值就是跨重启诚实）。deep-research 首批接入 + `research.status`/`research.cancel` 两 capability + 收窄 route_hints（「别查了」整句锚定 + guard 排除提醒/导航域）。**真栈五场景全绿**：受理开单（预算落库）→ 状态查询（**话术里的进度与账本逐字一致，任务状态是系统持有的事实不让 LLM 编**）→ 幂等去重（连说两遍不双跑）→ cancel 16s 内后台停手 → **重启容器后 orphaned 诚实报告**。**P1 Outcome Verifier**：proto `Capability` field 7 + `Verification` message，全链走 route_hints 同款路（YAML→manifest→register→**PgStore round-trip 补映射**→resolve→`_validated_steps`→executor，**LLM 字段一律不读**）；两求值器 `schema`（拿到真东西）/`state_match`（世界真的变了），三态 **UNKNOWN 不定罪**；**防 fast_intent 化由源码断言测试钉死**（中央零 agent_id/intent 字面量 + 临时 manifest 投新 capability 即生效）；首批 `hvac.*`/`info.weather`/`nearby.search` 三处试点。真栈：hvac.on **state_match sat**（NATS 镜像确认）+ nearby.search schema sat。**P2**：T2 按 `plan.complexity` 分档（Interactive 2次/8s、Complex 3次/12s）+ **重复副作用防抖**（`(intent, 解析后 slots)` 指纹撞上即回填、**动作不重发**——比原「副作用步不进循环体」精准且可测）。**三处设计偏差编码期推翻并记账**（子 RFC §9.1）：① 子 RFC「engine 已持有 NATS 镜像」是事实错误（编排器侧只有 obs 出站、无订阅）→ 新建 `orchestrator/cloud/state_mirror.py` 只读镜像；② 挂点漏了 **engine D0 / loop T2 两条流式直通路径**——真栈首验实测「weather 走流式，一条 verify span 都没有」，声明了却静默不生效（已补 + 源码断言防回归）；③ report 口径增「Agent 已按 R9 诚实降级则不重复念」判据。**接线坑**：`.env.example` 与 compose 里 `PLANNER_LOOP_MAX_ITERS/BUDGET_MS` 原本是**活跃的全局覆盖**，不清空则分档完全不生效（已改留空，填上=一键回退）。验证：全量 **1922 passed/7 skipped**、journeys 全量 @minimax **回归 12/14+1 数据真空 skip / 目标 15/18**（旧 canonical 13/18）**P95 19.1s vs 基线 25.5s 未劣化**（远优于 ≤10% 增量门槛）——两条回归红灯逐条复验为方差非本卡引入（A4-2 单跑 ✅ 属 wait_push 收帧时序；A3-1 同句连打三次 **2 绿 1 红**，抽风那次 planner 把「昨晚欧冠决赛比分」规划成 info.search 却漏填 query→反问「您想搜什么？」，属 M1a 已登记「tool schema 诱发少填」族）；**新增 L3 旅程 A6-1/A6-2 2/2 绿**（受理→查进度[答出「检索中 8/9 个子问题」真进度]→喊停 / 连说两遍答「已经在查了」）；`eval_route_hints` 98/98（+11）、`e2e_ledger.py`/`e2e_verify.py` 两个新 e2e 已挂 run_e2e。**未做（诚实清单）**：UNSAT→retry→report 不在真栈验（首批三处声明当前不会自然 unsat=这两个 Agent 今天没有假完成，verifier 是防回归护栏；该路径 44 条契约测试逐条锁）；`hvac.*` state_match 触发面窄（单句车控走端侧快路径不上云，只有混合多意图句才规划出云侧 hvac 步）；Complex 档只放到第一档，3-4次/15s 与 Interactive 跟进等下轮双指标；记忆图谱另出子 RFC |
| **M1b 自进化 v1 + Cloud Shadow NLU（2026-07-24，同日紧接 M1a；RFC `docs/design/2026-07-24-m1b-self-evolution-shadow-nlu-rfc.md`）** | ✅ **两条 DoD 均达成，运行时零改动**。**A Shadow NLU**：`test/eval_shadow_nlu.py`（批 10×并发 4/断点续跑/ProviderLock）全量 8590 @minimax——规则 hit 75.9% vs **LLM domain 准确率 91.2%**（unknown 0.4%）；切换建议=**navi（净增 42.2%）+setting（净增 24.3%×最大流量）为 Edge Semantic NLU 最大收益域**，information/media 规则已强不动，base 双失属开放语义归云 chitchat；与 07-03 缺口分析交叉验证一致；报告 `docs/reviews/eval/shadow_nlu_report.md`。**B 自进化流水线**：`scripts/evolve.py` mine（五路信号：badcase 标记/兜底话术/澄清卡/plan_degraded/即时重述，合成会话前缀默认剔除）→triage（LLM 归因 9 类封闭集，脱敏+数据定界+截断）→propose（hint/guide/corpus 草案，PROPOSAL_FORBIDDEN 白名单硬闸，不自动改仓库不自动 git）→gate（四离线 eval）→report；**首跑当日真栈 28 轮归因与实际事故全对上**（false_clarify 8=B4-1/data_source 5=B3-4/slot_error 2=B1-4），**并挖出未知族「取消观看的提醒」失败×7（待泓舟审）**；首份日报 `docs/reviews/badcase/2026-07-24.md`。**C 情感 TTS 能力面**：cosyvoice instruction/rate 可选参数（env 注入默认，缺省帧字节级不变；参数名待接线时探针）。单测 +15、全量 **1786 passed/7 skipped**；坑=报告层必须同句案族合并（重放轮刷屏）、_kw_pattern 贪婪分词跨句对不齐须 bigram 滑窗。**同日深夜遗留全清+闭环首案**：日报族「取消观看的提醒」四层根因修复（世界杯 07-19 结束数据真空窗+A2-2a skip 表漏「没有查询到」假跑+speech_any 过宽假 PASS+**reminder 五处 FAILED→OK=R9 契约第四 Agent，已正式登记 conventions §9.5**，真栈双验证 ✓）；shadow 残留 20 条=相似句批诱发数组闭合截断→parse 补闭合修复+max_tokens 1600，**8579/8579 全覆盖**指标不变；nightly=Task Scheduler `car-agent-evolve-nightly` 每日 23:30 真栈触发 LastTaskResult=0（evolve 补栈未起优雅 SKIP）；架构文档 **v1.3**（§5.2.1 Skill 层+submit_plan 合入）。下游=Edge NLU 立项（navi+setting 先行）随 M2 |
| **M1a `submit_plan` 结构化规划输出 V1（2026-07-24，同日紧接 M0b；RFC `docs/design/2026-07-24-m1a-provider-toolcall-rfc.md`）** | ✅ **全落地真栈验证，`PLANNER_TOOLCALL` 默认 on（2026-07-24 泓舟拍板翻正；off=JSON 回退档）**。四件套：providers（`complete_tools` 独立方法基类 fail-open——刻意不改 `complete` 四元组契约、存量 fake 零波及 + `normalize_tool_calls` arguments string→object 畸形丢弃不抢救）→ server（`CompleteRequest.tools` Struct 透传 + `tool_calls` 回填——proto 死字段首次启用不改 proto；带 tools 跳缓存）→ clients（`llm_complete_tools` + `_destruct_nums` 还原 Struct 整数防 slots "24"→"24.0" 假漂移）→ planning（`_submit_plan_tools` named 强制 + `_parse_and_validate_data` 校验单源 + 轮内降级 toolcall→salvage→JSON→degraded，最坏 2 次调用=现状上限；`Plan.plan_mode`→span 归因）。**协议探针 `test/e2e_planner_toolcall.py` 四家 16/16**（named tool_choice 全尊重；qwen `finish_reason` 恒 stop 怪癖已钉——按 tool_calls 置位判断勿按 finish 分支）。**A/B @minimax**：mode_routing on 三轮 171/175/173 vs off 173/174 持平（方差带）、arguments 畸形 360 例全程 **0**、降级例功能层全 PASS 零静默丢失；**journeys regression 15/15**。**三个 toolcall 独有 badcase（全靠旅程级抓出，mode_routing/协议探针全看不见）**：B4-1 误澄清=schema 可选字段诱发**多填**（description 约束无效→删结构、clarify 退回 prompt-only 触发面=R4.4 原始形态）；B1-4 丢继承槽=空 object 诱发**少填**（「函数入参只传需要的」先验→_TOOLCALL_SECTION 形态 few-shot）；B3-4 编造占位值「当前位置」=「写全」指令诱发**编造**（B1-4 修复引入的回归，qweather 400；few-shot 补负向约束）——**教训：tool schema/输出指令三向改变模型输出分布（多填/少填/编造），改 schema 必过 journeys 行为对照**。单测 +30（llm-gateway test_toolcall 16 + cloud test_planning_toolcall 14）；全量 **1771 passed/7 skipped**（收尾终跑，基线 1741+30；云端套件 253）；eval_mode_routing --live 接 toolcall 通道（开关在 eval 进程 env、per-case `pm:` tag 归因）。replan 工具化=V1.1 待办 |
| **M0b Skill 层三步制收官：Shadow → Canary A/B → Full Migration（2026-07-24，同日紧接 M0a）** | ✅ **全部完成**。**步② A/B（同 provider @minimax 组内对照）**：mode_routing --live 对照 off=174/177 vs canary=**176/177（guardrail 15/16→16/16，反超 2 例）**，唯一共同失例（麒麟电池对比判 research）两组同错=provider 边界方差；journeys regression canary **15/15** 持平——DoD②达标且正收益。**步③ Full Migration**：删 `_PLANNER_BASE` 遗留领域块 126 行（planning.py 604→478、base 124→59 行「减半」DoD 达成）、slim 升格唯一 base、`_planner_system` 无参收敛；`SKILLS_MODE` 默认翻 **full**（skills.py/compose/.env.example；off/shadow 降为 debug/研究档会缺知识已注明）；迁移附带修 4 测试（判据契约重写为「唯一来源=policy skill 防倒灌」+ engine focus/context 三条钉 off——假 LLM 关键词匹配/裸子串负断言被 policy 文本「空调」「最近对话」误触发，测试与注入文本巧合冲突非行为回归）；真栈 full 档冒烟 PASS（span 名单 `full:multi-day-trip,full:freshness-and-depth,...` 完整，行程编排含充电编织；冒烟顺手建的带伞提醒已清）。**步①与机制明细**：`orchestrator/cloud/skills.py`（SkillStore mtime 热更/纯词法检索[keywords+bigram，零网络离线确定，embedding 升级由 shadow 召回数据决定]/预算渲染）+ `SKILLS_MODE=off\|shadow\|canary\|full`（A/B 期默认 shadow，步③后默认 full）+ `_PLANNER_BASE_SLIM` 双路径（步③后收敛单 base） + `Plan.skills`→`cloud.planning` span `skills` 属性（badcase 归因）；载荷 guides×3（multi-day-trip/navigation-with-stop/conditional-reminder）+ policies×2（freshness-and-depth/implicit-vehicle-control），knowledge 自 `_PLANNER_BASE` 逐字迁移；Dockerfile `COPY skills`+pyyaml+compose 接线。验证：test_skills 11 + `test/eval_skills.py` 离线召回 5/5·反例误召回 1/6（纯导航句召回 stop guide=可接受噪声）+ **真栈两探针 span 记录 `shadow:multi-day-trip`/`shadow:conditional-reminder` 且行为零变化**；坑=collector span 的名字段是 `node`、attrs 是 repr 字符串 |
| **M0a 数据真实性+确认兜底（2026-07-24，智能化升级 M0a——设计 `docs/design/2026-07-24-eva-benchmark-intelligence-upgrade.md`，泓舟拍板开工顺序 M0a→M0b→M1a→M1b）** | ✅ **三张 P0 卡全落地（外部评审对 main@2fd9aa6 核实的存量缺口）**：①navigation 四处运行期 mock 回退（search/reverse_geocode/locate/poi_detail 遇 ProviderError 回退 `_fallback` 且 `attach(self.poi)` 盖真章）→ §9.5 铁律③诚实降级 OK 话术、`_fallback` 字段删除（结构性根除）；②charging_planner 三处（find/find_near_destination/plan）同治，「服务坏了」与「真没有」话术区分；③nearby 试点 2 处 FAILED→OK（**三 Agent 共坑：executor 不映射 error、聚合器单步 FAILED 只读 `r.error`，诚实话术被吞成裸「处理失败」**）；④**capability `require_confirm` 中央落实**：`_validated_steps` 从 manifest 读入（LLM 计划字段不读，不可降也不可升）+ `executor._enforce_capability_confirm` 兜底闸（Agent 漏标返 OK+动作 → 改判 NEED_CONFIRM 扣动作）+ engine D0 流式直通排除 require_confirm 步（流中 action 会绕闸）；⑤strict_stack 探针 +充电条目；⑥`skills/README.md` 三型（guide/policy/workflow）schema 契约定稿；**基线冻结**=升级前 canonical journeys 基线钉 2026-07-15 报告（回归 15/15 / 目标 13/18 @M3 / P95 25.5s），M0b canary 对照以此为准；真栈复验（journeys/strict_stack）留栈起时。TDD 全程红→绿，云端编排套件 227 无回归 |
| 端侧 Smoke 测试 `test/smoke_edge.py` | ✅ 13/13 通过 |
| HMI 单测 / 构建 | ✅ `node --test` 143/143（2026-07-18 实测，最新 +6 见「全量测试」行 2026-07-18 段；此前 127/127 含 **R4.4 P2 `rejectPolicy.mjs` 连续拒识收紧 +3 例**：≥2 减半续问窗/≥3 仅唤醒词/成功复位清零/基准随设置同步，`voiceLoop.mjs` `setVadBargeInDisabled` 关 VAD 打断但 wake() 仍打断 +2；**R4.4 P0 `voiceLoop.mjs` 定稿 onSend 带 hands-free 来源 +3 例**：wake/followup/bargein 三条聆听进入路径记 `_listenSource`，定稿 `onSend(text,{source,utteranceMs})` → App 拼 `meta.input_source` 上云做拒识判定；含 **R4.2 `pcmPlayer.mjs` 流式 TTS PCM 调度 +7 例**：首片攒 jitter 起播/后续无缝拼接/underrun 从 now 重建/barge-in 停/int16→float32 归一化，Web Audio 注入；poi_list 序号「第N个」选择解析、卡片几何、TTS 队列、ws 重连等；**R4.3 `voiceLoop.mjs` 语音回路 FSM +20 例**：全迁移路径/误唤醒静默回收/dismiss 与云端 F1 分界/barge-in 三态护栏+连续自触发降级/配置注入；**R4.3 `sileroEndpoint.mjs` VAD 端点判定 +10 例**：静音尾/滞回/起播去抖/配置注入）；`npm run build`/`tsc` 通过（含 Aurora Glass 重构、语音光球、ASR 流式上屏、**R4.3 语音回路大脑+设置4键+Orb armed/listening+VAD 真集成（onnxruntime-web 单线程 vadEngine/AudioWorklet/handsFreeController 接进 App）+ 唤醒词「小舟小舟」自建 sherpa-onnx KWS WASM 接进 App（COEP:credentialless，唤醒真麦验收通过，`scripts/build-kws-wasm.sh` 重现，二进制 gitignore）+ 修 ws.mjs 重连 Illegal invocation 真 bug**；信息证据卡 search_result/news_brief/sports_scores；`.mjs` 无声明文件为预存噪声）。**R4.3 收尾（2026-07-05，真栈 Docker 容器端到端验证）**：唤醒后人声播报（预合成多提示音随机播，TTS 关回退 beep）+ hands-free 识别文字实时上屏（ghost 气泡边说边出）+ 自定义唤醒词预设（小舟小舟/你好小舟/小舟你好/你好阿段，拼音 token 对模型 tokens.txt 逐一核验无 OOV）+ 三路 mic 收敛单路共享流（VAD/KWS/ASR 共用一次 getUserMedia，消除 AEC 互扰）+ KWS 播报态按 D6 抑制自触发 + 流式 ASR 失败批处理兜底（兑现代码里一直未实现的「失败回退批处理」）+ off→dashscope 回退 model 修复；后端契约护栏 `test/e2e_voice_loop.py`（`/api/asr/stream` 流式协议 + `/api/tts`，TTS→ASR round-trip 真栈 PASS，已接 `run_e2e.{ps1,sh}`）；声学命中率/误唤醒/回声打断留真麦人工验收（设计卡 §9）。**R4.3b 语音回路硬化（2026-07-05，真麦反馈 5 问题 + 4 审计发现）P0-P3 全落地**：P0 正确性（enable/disable epoch 代际护栏修「StrictMode×enable 竞态孤儿控制器致首唤醒双份」、THINKING 四死锁解除+App 四终局补 turnEnded、ASR 回调代际 token 修「陈旧回调劫杀下一轮」、pendingId 单槽→FIFO+仅最新轮驱动 TTS/确认、三引擎 start() starting 同步标志堵并发穿透）；P1 体验（新 `utteranceHeuristics.mjs` 承载判定：退出词「退下吧/没事了」去尾语气词后**精确**匹配本地退场不上云·needConfirm 时照发走 F1、filler+短语音双门槛、端点宽限合并「导航去…西溪湿地」续说拼接·完整句直发不拖慢、recorder 先行覆盖 ws 握手窗）；P2 链路（**3 处后端加法真栈 e2e 通过**：B1 PCM 直传跳 ffmpeg 支持前滚缓冲根治漏字·`pcmRing.mjs`、B2 `vad_silence_ms` 透传 qwen3 server_vad 治本「客户端静音尾对默认引擎终于生效」、B3 edge-gateway WS 并发读+`{type:cancel}`→`cancelled` 取消在飞请求真打断 THINKING）；P3 obs 语音指标（`voiceMetrics.mjs` localStorage 计数供真麦验收）+ 设置文案（静音尾三档语义/退下说明）+ 主卡 D6 A4 勘误。`voiceLoop.mjs` FSM 34 例（+U3/U5/U2 打断/宽限合并/指标）、+`utteranceHeuristics` 10 +`pcmRing` 7 +`voiceMetrics` 4；真栈 e2e_voice_loop（PCM+vad_silence_ms）+ e2e_ws（cancel）全过、Go 编译过、三容器 --build 重建。**P4 两轮真麦反馈修复**：①首轮 5 现象——wake 路径 pre-roll=0 治「唤醒词被识别成同音字（小舟→小周）误上屏」（P2 前滚缓冲取错方向=命中点往回取恰是唤醒词，只续说路径注入短 200ms）、恢复唤醒提示音（删掉 P1 过度激进的 `chime` inSpeech 跳过，唤醒词刚说完 VAD 必 triggered 导致几乎总跳过）、`matchExitWord` 改「占据整句+slack」+`isFiller` 去标点放宽（容忍 ASR 同音「退下把」/尾标点「嗯，」，仍不吞「退出导航」）、partial 对 filler 不上屏；②二轮续修——filler/短语音/空定稿改 `_gotoFollowup`（进续问窗继续听，orb 仍聆听态、可直接接着说、8s 无接话才回待机），退出词判定提前到 filler 之前（否则「退下」因短被当继续聆听），`_gotoFollowup` 补 `_closeAsr`。**已合并 main（merge `17e388e`）并 push**；node 例 119（+R4.2 pcmPlayer 7；voiceLoop FSM 37 + utteranceHeuristics 10 + pcmRing 7 + voiceMetrics 4 + 既有）。见 `docs/design/2026-07-05-r4.3b-voice-loop-hardening.md`。PCM 路径真麦命中率留 §10 泓舟验收 |
| Dashboard 单测 / 构建 | ✅ vitest 16/16（含 **LLM 消耗归属视图 +2**：fmtTokens 万位收敛/归属表渲染+未归属高亮）；`npm run build` 通过。**第五视图「LLM」**（2026-07-13）：collector `GET /api/llm/summary`（caller×model 聚合，坑=SQLite ORDER BY 裸列名取组内任意行而非 SUM 须重写聚合表达式）+ 时间窗 1h/24h/7d/30d + 汇总块 + 「(未归属)」红色高亮盯防（conventions §9.2 应恒为零），跟随 turn 事件实时刷新 |
| `gen/`（gRPC 生成代码）| ✅ 已生成（`buf generate proto`） |
| Go 网关 | ✅ Go 1.24 编译通过，Docker 全栈运行 |
| Agent Provider 适配 | ✅ 10 Agent 接入统一工厂；导航=高德（POI/路线/逆地理/详情+模糊地标LLM解析；视觉地标经共享件 `_sdk/landmark` 解析为**地图官方名**（如中国华润大厦而非俗称华润春笋大厦）+ name_matches 校验，拒高德对俗称返回的邻近无关 POI（如 V东滨店）；多意图里类目搜索（如充电桩）不被整句原文劫持、不双导航；顺路用餐 navigate_to.stop_category（或 raw_text『那附近找餐厅』兜底识别）→真实餐厅候选 waypoint_choice 卡，用户选「第N个」→navigate_to.waypoint 落 navigate.waypoints + 出 route_plan 路线卡（高德 get_route(waypoints) 真实全程距离/时长）；聚合器优先 waypoint_choice 卡）/ 天气=和风（JWT/EdDSA）/ 搜索=Exa正文级检索（AnySearch→Bing→mock 降级）+接地合成（榜单/统计等时效敏感查询开 Exa livecrawl 抓实时页、合成只照最权威源不混冲突数字）/ 新闻=SerpApi+接地合成 / 赛事=api-football（实时比分/赛程，league=1 世界杯；追问「第N场/某队+谁进的球」→ /fixtures/events 拉进球射手与分钟、剔除罚丢点球；「射手榜」→ /players/topscorers（免费档仅 2022-2024，试本届→回退最近可用并标注赛季）；「总/历史射手榜」→ 改写 query 走通用搜索接地合成（赛季 API 给不了累计历史榜）；联赛上下文多轮 history 回填）/ 股票=Tushare(A股)+新浪行情(港美股降级) / 充电=高德（充电站 POI + 路线几何；charging.plan = 出发地→**沿途途经充电点**→目的地，按电量续航在真实路线上取点搜真实站；目的地过泛（市/省/区/县）先经高德 POI 候选二次确认具体地点（dest_choice 卡，「第N个」回填槽位续接规划）再规划；无定位诚实提示、无 key 降级 mock；信息建议、不发导航动作；出 charging_route 时间线卡，聚合器多卡时优先展示它；charging.find 带目的地→按目的地搜（地标目的地先经共享件解析官方名）、最优站作为导航途经点（data.waypoint，聚合器并入 navigate.payload.waypoints 并对重复导航去重）；高德免费档 QPS 限流偶发→回退 mock）；错误话术用户友好化；AgentClient 护栏跨进程修复 |
| 真机 bug 修复批次（2026-07-06，泓舟真栈发现 6 项）| ✅ 全部修复+真栈验证+离线单测（`agents/info/tests/test_bug_fixes.py` +6）：①**赛事追问被点餐劫持**——「今天世界杯赛程」后「巴西那场帮我看看详情」被 nearby.detail 的 `看…详情` 贪婪 hint 抢走→给 nearby.detail 加 sports guard + info manifest 新增 sports 追问 route_hint（priority 58>nearby 55，guard 排除电影/演唱会等「场」歧义），真栈现出该场进球详情；②**猫名记忆答不知道**——猫名「Cookie」抽取成 `episodic` 而 chitchat 只召 `kinds=["semantic"]`→扩为 `["semantic","episodic"]`，真栈答出 Cookie；③**多日游误走周边 + 天气联动**——「珠海玩两天推荐景点」被 nearby.search 抢→guard 加多日行程词（`N天/行程/自驾/度假`）让 trip.plan 生效；**并补天气联动能力增强**：trip-planner 进程内复用 info 和风 provider，规划时取目的地多日预报（`plan_weather` 按「明天/周末」等对齐预报窗口、超窗口诚实置空）织进 propose（LLM 雨天优先室内/就近景点，软约束）+ 每天填 `Day.weather` 卡片/话术展示，compose 给 trip-planner 补 QWeather env。真栈「珠海周末」→「已结合天气…第1天 多云 28-34℃…第2天 雷阵雨 26-32℃」（和风 7d 覆盖），第3天超窗口优雅缺省；④**「下一场某队」列今日赛程**——`_sports_date` 只认明天/昨天→加 `_next_team_match` 日期扫描（免费档 `next`/`season` 均门控，只放行 date；扫今起窗口命中即停、免费档只开放近两天故命中 date-gate 即停）+ 诚实告知无数据（不列今日无关场），真栈葡萄牙→「明天 03:00 vs 西班牙」、阿根廷→诚实无数据；⑤**腾讯误标 A 股深证**——HMI 按 symbol 前缀瞎猜且硬编码「A股主板」→`Quote` 加 `market` 字段（provider 权威定，`market_label()` 00700→港股/600519.SH→上证·A股）+ HMI 渲染真值，真栈腾讯→港股；⑥**充电舞台 4 站名重叠**——`ContextualStage` 充电站名中心锚点横排相邻重叠→长名截断 + 相邻站名交错两级垂直排布，CDP 截图 4 站不重叠 |
| 周边发现 Agent 重构（food-ordering→nearby，2026-07-05）| ✅ **P0 + 两轮真机实测修复 + 出站白名单代理全落地，真高德端到端 + CDP 验证；已合并 main（merge `b0ffac9`）+ push，8 提交** 。把 mock 点餐重构为**基于高德 POI 2.0 的通用周边发现** Agent（`agents/nearby/`，端口 50063）：`nearby.search`（类目参数化，餐饮/酒店/景点/影院/停车/充电/加油，菜系·品牌·评分·**人均区间**·营业中·排序过滤）+ `nearby.detail`（评分/人均/电话/营业时间/特色/图片 + 导航·拨打按钮）+ `nearby.order`（**诚实预留桩不假下单**，给电话+导航兜底）。自持富数据 `AmapPlaceProvider`（补 navigation 薄 provider 丢的 `business.cost/tel/opentime/photos`，`show_fields=business,photos`）；与 navigation 按「**发现 vs 出行**」经 manifest `route_hints` 声明式切分（guard 让沿途充电规划归 charging、缴费归 parking、出行动词归 navigation）、**不改编排核心**；新增 `place_list`/`place_detail` 卡渲染（旧 `restaurant_list` 卡 HMI 从未渲染）。真机实测修复：①价位改**区间**（『一百左右』→[60,140]，剔太便宜/太贵/无人均，**原话解析优先于 LLM 槽位**）②充电/停车关键词剥查询动词（『帮我查一查停车场』→停车场）③`open_now` 营业中过滤（`base.is_open_now` 跨零点/多段/24h/北京时区）④「第N个详情」透传高德 POI id（`meta.nearby_poi_id`）精确取详情 + 裸序号选择『点一下第九个』经 `ordinalSelectIn` 接住（修返回第一个/列表外 POI）。**安全**：真栈发现 ws8 第三方出站代理是空壳（envoy 反向代理不支持 CONNECT），换 `deploy/egress-proxy.py`（stdlib CONNECT 正向代理 + 域名白名单，实测 amap 200 / google 403），nearby 出站真正受白名单约束。**验证**：nearby 单测 31（agent+provider 黄金响应）+ nav node 6 + HMI build + registry resolve 基线重算 15/15 + 真栈真高德（美食/酒店/火锅/充电/停车真数据）+ CDP 实测「点一下第九个」→ 列表第 9 项精确命中；见 `docs/design/2026-07-05-nearby-discovery-redesign.md` |
| 四模式路由与回答质量（2026-07-12，泓舟发起「深度搜索/新闻/联网查询/直答」重设计）| ✅ **9 切片全落地（f210eb2 起连续提交），eval 先行 + 全声明式 + 唯一编排改动为通用机制**。**问题**：四模式（chitchat 直答/info.search 联网/info.news 新闻/research.run 深调研）进入全靠 Planner 一次 LLM 裸猜——prompt 无「直答vs联网」判据、时效性判断在路由层缺席、chitchat 是失败/降级/权限过滤统一落点（系统性偏向陈旧直答）、确定性护栏只保 research（search/news 0 条）、四模式边界零评测。**P0 路由**：①新评测 `test/eval_mode_routing.py` + 语料 122 条五桶（typical/boundary/adversarial/followup/guardrail，`--live` 真 PlanBuilder 端到端最终 intent + 混淆矩阵，离线确定性子集直调 RouteHintEngine；expect_mode 支持 `a\|b` 双容忍 + weather 族归并防标注噪声）；②manifest 判别化（chitchat 限定「不随时间变化的常识」、info.search「凡答案会随时间变化都用本能力」、info.news「看一批」、info.stock 限定证券）；③planner prompt「时效与深度」通用段（不点名 agent）；④info manifest 两条 route_hints（priority **59** 避开 trip.modify=60 同级 agent_id 字典序反转；search=句首动词 `搜索(?!引擎)`+`搜(?!索)` 双前瞻+大 guard 表，news=browse动词/裸新闻/话题式三分支+打开/关闭收进句内前瞻防误拦多意图句），route_hints 语料 28→47。**效果（受控对照 @minimax:MiniMax-M3）**：live 101/120(84.2%)→**115/120(95.8%)**，16 例改善（常识过度联网恢复直答/search 被越级 research 压回/时效误吸直答消除），followup 10/10；确定性子集 36/57→**57/57**。**P1 直答**：chitchat 日期锚点+「不确定就明说绝不编造」+depth slot 模型分档（知识类升 primary）；**引擎级 escalate 通用机制（泓舟拍板）**：`AgentResult.data["_escalate"]` 保留键（conventions §9.1），engine D0/executor 双挂点有界一跳改派、经 `_validated_steps` 装配走 executor（heavy 预算/过程区/权限自动带出）、单跳防环/streamed 忽略防二次回答，chitchat `<search>` marker+流式头部缓冲零播报；契约测试 6 条。**P2 质量**：search 薄证据一轮重试（剥口语前缀）+`rerank_fresh_authority`（recency>0 时窗口内优先于旧高权威）+合成 top5→6+低置信 follow_up 教「深入调研」；news `_summarize_news_list` 补 thinking=False（存量 bug：heavy meta thinking=on 泄漏）+话题新闻 livecrawl；research plan 数量对齐 cap（5-6/8-9）+backtrack「薄就合并追一轮」+**深挖种子复用**（save 每节 citations urls≤3→「展开第N点」extractor 并行取上轮正文作 sq0 预置证据，investigate 对带证据子问题跳检索；旧数据无 urls 向后兼容）。**坑**：①registry 离线路由按「query 字符∩capability 文本」打分，desc 加长会全局抬分（deep-research 加一句判据把「自驾游路线安排」从 trip-planner 吸走，eval_registry_resolve 抓到→desc 恢复原句）；②rejection/clarify 基线是 @mimo 采的，@MiniMax 直比会假回归——stash 隔离实验证实拦截率差异纯 provider 属性（新旧 prompt @MiniMax 完全同分且新 prompt 误拒 3.4%→0%）；③**评测与 docker build 并发会污染 live 基线**（构建吃满 IO→LLM 超时→fallback chitchat 假失败）；④llm-gateway 重建后 active provider 回落 env 默认（运行时切换是内存态）。四套回归 gate：rejection/clarify/registry_resolve/mode_routing。**收尾续修（同日，泓舟真机反馈两项）**：①**MiniMax 开思考泄 `<think>` 内联**（四家×complete/stream×开关思考探针：仅 MiniMax 泄漏，思考段内联 content 头部而非 reasoning_content 字段）→ provider 出口统一剥（`strip_think_block` 纯函数 + `ThinkStreamStripper` 流式头部状态机，未闭合=截断在思考里诚实置空）；②**markdown 判断=speech 不上渲染、后端出口硬剥**（TTS 是第一消费者渲染救不了念星号 + Aurora Glass 契约气泡短结论/结构归卡片 + 各家 md 输出不稳定）——四层收口：aggregator.compose（unary 全路径）+ engine 流式 `MdDeltaSoftener`（**/` 跨 chunk）+ TTS 漏斗 `_strip_md_tts`（`_sentence_segments` 句子组装后剥 + 批处理入口）+ research 报告卡 body 出口剥（[N] 引用保留）；③连带修 **parse_synth 截断 JSON 抢救**（MiniMax 长 answer 撑爆 600 tok → 整段 `{"answer":...` 原始 JSON 被当话术上屏，真栈实测；抢救 answer 已生成部分、降 low 置信，绝不念 JSON 外壳）。坑=**编排侧不能 import agents/_sdk**（cloud-planner 镜像不含 agents/，真栈 ModuleNotFoundError 崩启动循环重启——共享函数三处自持实现配对注释：`_sdk/grounding`↔`orchestrator/cloud/aggregator`↔`llm-gateway/providers`）。真栈 @minimax 复验：思考 ON complete/stream 零泄漏、搜索/新闻/直答 speech 无 md/think/JSON 外壳、且日志抓到 route_hint 真实打回一次 MiniMax 误路由（chitchat→info.search）+ chitchat depth=deep 引导生效。**badcase 0f4105c4（泓舟真机 trace）**：调研 speech 上千字原始维基堆砌+报告卡 md 残留——obs.llm 定位=synthesize completion 打满 2400 tok → JSON 截断 → 整份退化 `_fallback_report` 堆原文（该路径从未剥 md）；且旧要求 5-7节×250-450字≈3150字**结构性超预算**。三针修：`_parse_report` 截断抢救（正则抢救 summary+已完整 section 逐块 loads，gaps 标「被截断」）+ 预算对齐（sync 5-6节×180-300字 落 2400 tok 内；deep 8-9节×300-500字 + 4000→6000 tok）+ fallback 可读性收敛（节选剥 md 截 200 字+省略号+「合成暂不可用」诚实标注）；三条路径 heading/body 全过 md 剥离。真栈复现原场景 @MiniMax 两轮全过（speech 174/222 字干净结论）。**badcase 6ce027fe**：speech 断在「马拉多纳的」——completion 185/600 非 token 截断，是 MiniMax 在 JSON 字符串里写**裸英文双引号**（"上帝之手"）致整份非法，旧抢救按「下一个引号」取值把转义病误当截断病拦腰截断→新 `extract_json_str_field` **边界式提取**（引号+下一个已知字段名/收尾括号才算结尾，裸引号保留为文本；返回是否闭合区分转义病/真截断），parse_synth 抢救 + `_parse_report` summary/section 抢救（`_rescue_section` 块 loads 失败转边界式）全部切换 + 两合成 prompt 软约束「引用用中文引号」；真栈原句复验 @MiniMax 126 字完整闭合。**badcase 736e4bba/1de7e50c（@deepseek 同会话两轮）**：赛事预测答非所问——①「判断半决赛结果/谁胜出」类**预测前瞻**问题被结构化赛果源接走（api-football 只有已定事实+免费档只开今天±1天），把 1/4 决赛赛果当「半决赛」还错推决赛对阵→`_PREDICTIVE_HINT` 让路机制（`_maybe_sports` 命中返 None 不劫持、`_sports` 直达转 `_search` 原话整句作 query）+ manifest desc 反界定；②赛果 speech 从不带比赛阶段（round 只进卡片）→聚合 LLM 脑补阶段→`_round_zh` 映射，列表首句+单场详情 head 都点明「四分之一决赛/半决赛」。真栈复验 @deepseek：预测句给出正确半决赛对阵（法西/英阿）+诚实「决赛对阵尚未产生」，事实句带阶段标注。全量 1360 passed/7 skipped。见 `docs/design/2026-07-12-mode-routing-and-answer-quality.md` §8 |
| 真机 badcase 三修复：搜索兜底/天气意图先答/车况动态化（2026-07-13 泓舟提报 2 trace + 1 UI）| ✅ ① **搜索兜底可读化**（trace 6d29929e「预测谁晋级决赛」：MiniMax 422 秒拒合成→兜底把两篇原文 snippet 整段倾倒+拦腰截断直达用户）：fallback_brief 重写（跳 SEO 标题/样板行、clip_sentence 句边界收口、限长、明示未归纳+指向卡片）+ build_materials 进 prompt 前消毒控制字符/孤立代理对（同模板同体量 11:08 成功 11:10 换一批来源即 422 的头号嫌疑；赔率内容风控假设已实验否定）+ llm-gateway chat 4xx 捕获响应体进异常（下次 422 直接可诊断），test_grounding_fallback +6/4xx +1；② **天气意图先答**（trace f555cde3「未来几天会下雨吗」只回模板+整段念逆地理地址+「：；」双标点）：\_forecast_answer 确定性规则（雨/雪/冷热/风四类，零 LLM 零延迟）先答再接逐日摘要、\_day_label 今天/明天/后天、\_speech_place 地名收敛市区级（卡片仍全名），真栈「深圳未来几天会下雨吗」→「会下雨，这3天每天都有雨，出门记得带伞。深圳未来3天：今天小雨转中雨…」，test_weather_answer +8（坑=测试导入须走全包路径，裸 sys.path 插 src 会让 providers 通用包名劫持 sys.modules 污染 llm-gateway 测试收集）；③ **HMI 右舞台车况动态化**（原写死 62%/430km/P）：edge-gateway 订阅 vehicle.state.changed（复用 edge 既有 30s 周期全量快照，编排器零改动）合并镜像→HMI 连上即推+变更去重广播 vehicle_state，HMI vehicleStage.mjs 纯逻辑（缺数据诚实 --，续航=range_km 优先/电量×550 折算）+ IdleStage 动态渲染，Go vehstate_test +2、node +4（137/137）、真栈 WS 探针连上即收 battery=72/gear=P；**下午续批 2 badcase**：④ trace a3fad033「预测世界杯冠军」兜底不答问题——4xx 响应体捕获当轮兑现：MiniMax 422 真因=input new_sensitive(1026) 内容风控（检索源夹带敏感站正文整包被拒，编码假设否定）→ grounded_synthesis 识别风控拒收自动收窄权威 top-2 重试一次（_is_content_rejection 覆盖 sensitive/data_inspection/content_filter），llm-gateway 4xx(400/403/413/422) abort 改 INVALID_ARGUMENT（SDK 不再 UNAVAILABLE 重试白打一遍），test_grounding_fallback +3，真栈重放出真合成带引用直答；⑤ trace 361f6e72「今天体感温度怎么样」被端侧裸「温度」子条件 3ms 劫持成开空调（**问天气误触车控执行**）→ _is_env_temp_query 三层让路（查/几度/多少既有排除不动基线 + 体感/气温/室外语境无条件 + 怎么样/如何疑问式仅无操作动词时）+ 天气查询分支补体感/气温/疑问式→info.weather，「温度如何调高」仍归空调，test_fast_intent_extended +5、eval_fast_intent 57/57 零回归，真栈重放「深圳南山区当前多云，气温32℃，体感35℃」；⑥ trace 11db5215「今天天气怎么样，适合出行吗」只机械播报——三层叠加根因（实时天气无意图先答/路由随模型漂/多步 plan 中 road-safety NEED_SLOT 反问吞掉天气答案）三层修：\_weather_answer 实时版意图先答（出行/雨/雪/冷热，实况+当日预报+预警）、info manifest 天气×出行 route_hint（priority 57，**字符类不排逗号**跨子句共现，eval_route_hints 52/52 +5case）、road-safety 无目的地改一般性出行建议（零 LLM 不反问）；三形态真栈重放全过 |
| LLM 消耗排查 + registry 长期不健康剔除 + 批处理音频引擎可配（2026-07-13）| ✅ 排查结论：**栈内无闲置持续消耗 LLM 的入口**（obs.db llm_calls 3 天数据夜间时段 0 次；大头=eval 直打网关与 memory 抽取，两者 caller 为空）。落地：① registry「不健康」告警只打转变沿 + 连续失败 REGISTRY_EVICT_FAIL_COUNT（默认 120≈10min）自动剔除（内存+PG 级联，根治 food-ordering→nearby 改名残留每 5s 刷 WARNING 8 天；活 Agent 10s 重注册豁免；test_health_probe +3）；② 批处理 ASR/TTS 工厂去 MiMo 硬绑：ASR_PROVIDER/TTS_PROVIDER env（auto=LLM_PROVIDER 为 MiMo 系→MiMo 否则桥接流式引擎），新增 StreamBridgeASR/TTSProvider（WAV↔PCM 帧桥接、跨引擎音色防御）、MIMO_AUDIO_BASE_URL 端点可配、/api/voices 缺省跟随批引擎（test_batch_audio_providers +20）；③ 合成会话跳过记忆抽取：session_id 前缀契约（eval-/e2e-/replay-/nightly- 等，登记 conventions §9.2，env MEMORY_EXTRACT_SKIP_PREFIXES）→ memory 服务不烧 token、不把测试对话沉淀进真实画像，短期轮次存取不受影响；e2e_memory routine 链路改用 memtest- 前缀豁免；真栈验证 eval- 会话 4 轮 AppendTurn 零 LLM 调用；④ LLM 调用归属补全：memory-extract / eval-mode-routing / eval-rejection 直连 Complete 补 meta[caller_service]（仅观测、不扰动限流桶键 caller），obs.llm 消耗归属盲区闭合；.env.example/compose/conventions 已文档化 |
| 智能提醒 Agent（reminder，2026-07-11）| ✅ **P0 + P1a 体验闭环全落地，真栈 e2e 7/7 全绿**。**P1a（P0 审查后泓舟批「直接开干」，同日落地）**：`reminder.update` 改时间（改到/推迟/提前，标题/序号定位+多条 `_clarify_multi` 澄清+缺新时间存 `REMINDER_PENDING(action=update)` 下轮裸时间续接改原条目）；**snooze 改期原条目**（「稍后10分钟」按钮 send_text 与「过10分钟再提醒我」→ `_reschedule_target` 同名 fired 尸体收编、显式「再提醒」连 pending 也改期，根治 P0 按钮取巧造成的 fired 堆积——e2e 4b 断言 snooze 后列表仍 1 条）；**重复规则 `recur`**（每天/每个工作日/每周X：`parse_recur`→触发后 `next_recur_fire` 滚动下一次 pending、停机错过跳过不补发、工作日首触发落周末顺延周一、「完成」只确认本次不杀系列「取消」才结束、卡片带 `recur_label` 标签）；「下个月/这个月」区间查询；澄清话术带动词（「取消/完成/改第几条」）+ cancel/update hint 补序号词形（裸「取消第二条」确定性路由）；审查修复 B1 `_list`「大后天」被"后天"截胡 / B3「2月30号」非法日期诚实 FAIL（不炸内部错误不误落 N号 分支）/ N2 LLM ISO 带时区不误标 / N1 conventions 改名同步；「过N分钟」无"后"缀词形入 timeparse。验证增量：reminder 单测 69→**94**（timeparse 50/store 10/scheduler 6/agent 28）、`eval_route_hints` **26/26**（+9：update 正例、「空调改到26度/音量改到50」guard 反例、recur/snooze/序号续接词形）、真栈 `e2e_reminder.py` **7/7**（+4b snooze 无尸体）+ recur/update 真栈探针（「每天早上八点吃药」→"每天 08:00…首次明天"、「把吃药改到晚上九点」→同条改期、清空确认闭环）、全量 pytest 见下、HMI node+build 绿（`recur_label` 芯片 + `updated` 卡态）。**P1c 跨域提醒（同日，真机 badcase trace 703f095f/b3ecd195 驱动，泓舟批「开干」）**：「第一场提醒我观看」原来反问"什么时候提醒你？"（开赛时间在上一轮赛程数据里无人交接）→ 标准化 `REMINDABLE_ACTIVE` 共享状态（产"未来事件"的域 opt-in 写 `{source,label,ts,items:[{title,fire_at}]}`，**items 序=卡片渲染序含已开赛占位**；现 sports `_save_remindable` 卡驱动收割两处调度层，trip/charging 即插）+ reminder `_from_remindable` 消费（缺时间时**先于 LLM 兜底**查：「第N场」按序/「这场/开赛」指代单取多项反问/`parse_lead` 提前量缺省 10 分钟/已开赛诚实/pending 续接轮同查/纯指代不当标题）；真栈复现原场景**一轮成单**"挪威 vs 英格兰 明天 05:00 开始，提前 10 分钟提醒你"；reminder 单测 →102、sports 生产者 +4、eval 28/28（+2）；坑=`_ORDINAL_RE` 后缀类须含「场」。此前 P0：真栈 e2e 6/6 全绿。新建独立 `reminder` Agent（`agents/reminder/`，端口 50074，core/first_party/cloud，intent `reminder.create/list/complete/cancel`）：确定性中文时间解析 `timeparse.py`（相对「20秒/一个半小时后」+ 绝对「明天8点」+ 周期，解析不出走 LLM @fast 兜底 → 仍缺时间出 NEED_SLOT 追问、创建回读确认）；PostgreSQL 持久化（自持 `reminder_item` 表，asyncpg）+ 内存 fallback（诚实降级）；进程内 asyncio 调度器 `atomic claim_due` 保 at-most-once 触发；到点经 NATS `agent.proactive`→edge-gateway→HMI 主动触达带卡（`reminder_card`/`card_group`）；跨轮状态走 profile KV（`REMINDERS_ACTIVE`/`REMINDER_PENDING`）；清空 NEED_CONFIRM→确认续接。确定性路由用 manifest `route_hints`（4 条，create/list/complete/cancel）声明式兜底，**未改编排核心**。HMI：新增 `reminder_list`/`reminder_card` 卡（`Cards.tsx`）+ `AgendaStage` 双形态舞台（单日时间线 / 多日分组清单，`reminderStage.mjs` 纯逻辑）+ `AGENT_CATALOG` 补「智能提醒」。**真栈集成 bug 修复**：`orchestrator/cloud/context.py::_POC_DEFAULT_SCOPES` 缺 `profile.read/profile.write`（reminder manifest 声明这两个合法 first_party scope，但 PoC 无 token 时的默认授权集不含 → `_filter_by_permission` 在规划前把 reminder 剔出 agent_map → LLM 与 route_hints 都看不到 → 全兜底 chitchat）；补进类目级默认授权集（非 agent 级路由硬编码，符合设计 §10「profile.*，无新 scope」）。验证：reminder 单测 68 + HMI node 133 + `eval_route_hints` 17/17 无回归 + 全量 `pytest --import-mode=importlib` 1268 passed/7 skipped + 真栈 `test/e2e_reminder.py` **6/6**（创建回读→到点 NATS 触达带卡→列表诚实呈现→完成→清空确认闭环）。**真机硬化（2026-07-11 泓舟验收）**：cancel/complete 标题命中多条时旧 `_resolve_target` 取 `hits[0]` 静默少删、聚合器又谎报删除数量（真机误感"全删了"，实为少删+话术不实）→ 方案乙 `_resolve_targets` 返回全部候选、多条走 `_clarify_multi`（NEED_SLOT 反问列候选+写 `REMINDERS_ACTIVE` 支持「第 N 条」续接）、唯一命中才执行（reminder 单测 68→69，真栈端到端澄清+续接验证；多条选择性删除仍属 P1 边界）。同轮修 llm-gateway provider 置灰误诊——根因是启动误在 `deploy/` 目录跑 compose 读了旧 `deploy/.env`（只含 `LLM_API_KEY`）覆盖根 `.env` 的全部 key，改用规范入口 `docker compose -f compose.yaml`（根 .env）`--force-recreate llm-gateway` 后 mimo/minimax/deepseek/qwen 全 `available:true`，并删除陷阱文件 `deploy/.env`（§ 运维铁律：docker 操作一律走 `make up`/根 `compose.yaml`，禁 `cd deploy` 直跑）。见 `docs/design/2026-07-11-reminder-agent-design.md` 与 `-implementation-plan.md` |
| 新增 Agent（ws2 P0 + standalone-agents） | ✅ charging-planner（50068）/ scene-orchestrator（50069）/ road-safety（50072）已建，含 manifest/providers/tests/Dockerfile |
| **场景编排重设计 P0-P3 + 全量评审收口（2026-07-14）** | ✅ **全部落地并真栈验证，已 push（d7c3992→4d77462 共 9 提交）**：`test/e2e_scene.py` **26/26**、全量 **1576 passed**、eval route_hints **70/70** + fast_intent 57/57、smoke_edge 13/13、HMI CDP 真栈（scene_list 卡+露营确认+取消）。**P0 造场景闭环**：`scene.create` 一句话造场景（LLM 仅创建期编译 NL→动作，过 VAL 词表白名单+危险动作强制确认+回读 NEED_CONFIRM+落 PG `scene_item`；**激活/退出/触发零 LLM**）/ 用户场景遮蔽同名预置 / activate 尾缀 `scene_mode.set` 状态位+按动作集采车况快照 / **deactivate 真恢复**（按 `SCENE_ACTIVE.solved_actions` 还原到快照值）/ update·delete·list / route_hints（guard 让路端侧 driving_mode·power_mode）/ scene_card·scene_list 两卡。**P1**：custom_params 本次参数覆盖（「开启午休模式，温度26」）+「把刚才这些存成X模式」会话沉淀（D11 桥）+ media 动作放开（端侧 `_dispatch_cloud_actions` 回流扩 media.control 一类）。**P2 策略引擎**：Ground·Solve 三态求值（sat/unsat/**unknown≠满足**——防互斥分支缺数据双发）+幂等跳过已达成+空集诚实反馈；Verify-Repair 后台对账（activation_id 代际护栏+单飞 cancel，on_fail=skip 诚实汇报/retry_suggest 重试建议卡/defer_p 驻车补做，fail-open 绝不假警，**repair 不新增执行通道**）——**首跑即抓到存量 VAL 真 bug**（ambient_light 设色分支提前 return，四个预置场景的亮度从未生效过，已修）。**P3 询问式触发**：时间 poll+事件边沿+节流双 watcher，**只发建议卡零执行权**（D6）；触发/verify/驻车补做共用一条 NATS 订阅。**落地纠偏 4 处**（设计与代码事实不符，`docs/design/2026-07-14-scene-orchestrator-redesign.md` §0.5）：①memory 无 `vehicle_state` scope → 新增 `src/state_mirror.py` 订阅 NATS 建进程内车况镜像 ②`commands.yaml` scene_mode 改开放值域 ③**端侧 fast_intent 劫持场景句**（造场景句+激活句「开启午休模式，温度26」两波都堵：护栏早于空调分支+`_split_parts` 不拆场景句）④`_dispatch_cloud_actions` speech 逐条覆盖修根（单条 VAL 精确应答/多条保留云端总结/**拒绝不被后续成功掩埋**）。**落地后全量代码评审六修复**（§0.6，随 `f436521` 入库、叙述 `4d77462` 补档）：hour 时区经 `BUSINESS_TZ` 与 watcher 同源（容器 UTC 差 8 小时）/ `recur=once` 触发器消费即熄（原会滚成 daily）/ 事件路径 10s 场景缓存（防车速广播 DB 风暴）/ verify 复合断言日志对 list 调 .get 崩 / manifest 补 media.control 声明 / route_hints 两 guard（update 加句首激活动词防「温度改成26」被当改场景**持久**改默认值 + deactivate 补性能模式）；评审同时核实确认链=engine `_restore` 重入式自洽（activate 确认轮重新 Solve+快照+verify 无旁路）。坑：聚合器吞 FAILED 话术（诚实拒绝一律用 OK）、mock LLM 盖不住「prompt 没插策略说明」（加 test_prompt_actually_carries 钉死喂给模型的内容）、e2e 断言防「蒙对」（前置压值+scene_mode 断言） |
| **旅程级验证体系 L3/L4（2026-07-14/15，泓舟发起「跨 Agent 自主执行+全场景连续对话」验证主题）** | ✅ **P0-P2 落地并真栈验证（commits `6470209`→`c93f34b`+后续），设计 `docs/design/2026-07-14-journey-e2e-test-system.md`（§10 落地记录）**。**L3 旅程层**：`test/e2e_journeys.py` 数据驱动 runner（say/confirm/press[从实收卡片取 send_text=等价点击]/wait_push[常驻 WS 收 proactive]/env/new_session/docker_stop 故障注入；断言状态化优先：action(list)/action_absent/vehicle 终态/cards/any_of 双容忍 + 全局零泄漏与断链禁词；失败自动标 collector badcase 供 dashboard 重放；报告含 active provider + 记分卡 + 时延基线）+ `test/journeys/*.yaml` 语料 **33 条**（回归级 15=必须绿 / 目标级 18=能力标尺允许红）。**回归级 15/15 真栈收敛全绿**（外部数据源断供按 skip 语义）；**目标级首跑 7 绿 11 红→红灯清单 9 工作项**（§10 表待泓舟三选一评审）：**R1 POI 解析就近关键词命中压过知名地标/城市语义（横切之王 5 例：广州塔→广州仄仄科技/宝安机场→北环大道入口/大梅沙→红树林/惠州→惠州出口/那附近停车场→呼和浩特万象天地，建议按回归级修）**、R2 确认挂起被插话清除（engine.py:128，中断-恢复缺失；B2-2 证明 agent 层 REMINDER_PENDING 模式活过清挂起=修复参照）、R3 焦点位置迁移缺失（「那边天气」答当前定位）、R4 reminder hint 无疑问式 guard（「提醒我什么来着」被当新提醒）、R5 记忆陈述句无人接（「记住我最喜欢26度」被端侧温度劫持当场执行）、R6 条件 DAG 丢步、R7 navigation 不产 REMINDABLE/ETA（「到之前提醒我」→「暂不支持哦」）、R8 trip×weather 反向修改未实现（雨天换室内=原样端回）、R9 provider 故障零降级（裸「处理失败」）+Q2 charging 直达判定零余量（10%→50km 对 47.7km 判足够）+Q3 sports 日期界外答非所问；**意外的绿**=单句搜店→导航直达/单句赛程→提醒成单/部分失败诚实回执/「就去第二家」/副驾对象继承（plan `position:passenger`）/补槽挂起+插话续接。**首跑抓到存量真 bug 已修**：scene `custom_params` 槽位声明了从不消费（LLM 归一化路由变体下参数覆盖丢失，确认后拿默认值；e2e_scene 恰走 route_hint 原句路径故绿——**路由变体依赖缺陷只有旅程层多次真跑才暴露**；修=原话优先+槽位兜底，scene 单测 162）。**L4 CDP 层**：`test/hmi_cdp/`（Node22 零依赖 driver，headless Edge，`webSocketFrameSent` 实拦帧；**不加 testid**按可见文本定位）7 用例 **6✅1⏭️**（确认条 is_confirmation 帧→trunk 真开/「点一下第二个」改写帧+nearby_poi_id 透传/scene 按钮+取消链/到点推送卡渲染+完成按钮帧/过程区门控/舞台车况联动；C2b 前提被 R1 压掉判 SKIP 非 HMI 缺陷）。**runner 协议事实两则**：混合意图一次请求多个 final（本地段先 final 云段再 final，宽限窗收齐合并）、HMI 二次交互=合成文本发送（例外仅 is_confirmation/nearby_poi_id）。**收官全量跑（33 条连续，19min）：回归 13/13+2skip（api-football 当晚断供，skip 语义生效）、目标 7/18 且 11 红与首跑完全一致=红灯集稳定可复现；记分卡 autonomy 13/18·continuity 11/20·honesty 3/3；时延基线 65 轮 P50 5.7s/P95 40s**。mock 车道 2 条（A4-2 reminder 全链/B4-2 scene 激活）挂 nightly；`make e2e` 清单已接入。**红灯修复三批（2026-07-15，泓舟拍板「9 项全按建议修」+Q1-Q4 按建议，commits `96093ad`→`44ebe95`）**：批次1 快赢=R4 回忆式 guard（「提醒我什么来着」不再建单）/R9 provider 故障回落接地搜索+诚实降级改 OK（聚合器吞 FAILED 话术同坑，真栈双源齐挂实测层层诚实）/Q3 日期解析补齐（昨晚/下周X 自然周口径+界外按所问日期诚实）/Q2 充电直达 15% 余量+短途尾缓冲不吞补电点；批次2=**R1 POI 地标语义**（`_dest_matches` 包含式强校验+去偏置重搜+`geocode_level` 行政级门，真栈五连转正：广州塔/宝安机场/大梅沙/惠州 dest_choice/万象天地）+**R2 中断-恢复**（engine 插话不清挂起+软提醒+单槽覆盖，B2 组三态全绿；测试防假绿=插话轮须不自挂起的单步计划）+**R3 焦点迁移**（B1-2 被 R1 连带修复；B1-3 真根因=nearby 地名 location 交无城市偏置 geocode 全国歧义到呼和浩特→`_resolve_center` 坐标偏置搜名+包含校验）+B3-2 补能 advisory；批次3=**R7 导航 ETA→REMINDABLE**（A2-4 转绿「到达宝安机场 12:23 提前 15 分钟提醒」+端侧电话分支对提醒让路+一刻钟 lead）+**R8 雨天改室内**（Day.weather 确定性定位+全部雨天合并一次 propose 防 90s 超时，B3-1 转绿）+R5 偏好陈述让路（端侧+scene 反界定+planner 偏好槽位护栏；**残余=记忆层 M1 场景参数漏进偏好[钓鱼模式22度被抽成最喜欢22度]/M2 显式新偏好未时序覆盖→立卡**）+R6 条件链（hint guard 排除条件连词+prompt 判据示例；obs 实证 adaptive→T2 查到雨自主补建 reminder，**残余=挂起话术未携带前序天气结论→立卡**）+B2-3 序号回填（新共享键 `CHARGING_DEST_CHOICES` 登记 §9）。**最终账本：回归级 15/15、目标级 16/18（首跑 7/18）——11 红修到只剩 2 张已立卡残余（A1-4 `_suspend` 话术携带/B3-3 记忆 M1M2）**；**B5-1「一次通勤」14 轮整旅程首次全绿**（「到公司之前提醒我交周报」一轮成单 12:51 提前 10 分钟/「提醒我什么来着」正确列清单/「不用了取消吧」多条候选澄清）；复验抓到 **R2 次生黑洞**（wait_slot 不清后 `_is_topic_change` 判不出疑问式→全被当补槽答案）三针修：语境取消词表 `_SLOT_CANCEL_RE`+疑问式判换话题+REF 放宽「到X之前」；坑=mock KV 不回读须 KV 钉内存（本批抓 2 个假绿单测收紧）、canonical 报告只随全量跑覆盖（--force-report）。**遗留卡收口（2026-07-15，两张全清，A1-4/B3-3 真栈转绿）**：卡①=`engine._suspend` 增 `prior`（本轮新完成且未播报的步骤结果）→挂起 final 前缀脱敏简报（复用 `result_summary` 口径+md 剥净；防双重播报三道闸=挂起步自身不进前缀/续接种子切掉/T2 已流式播报按 `id()` 排除——步 id 跨 replan 可撞名不用 step_id），接线 engine executor/escalate/loop 四处，真栈探针「查天气+无时间提醒」→「深圳当前阴，气温29℃…。好的，带充电线。什么时候提醒你？」（修复前只有后半句）；卡②=记忆三针修：**M1** 抽取黑名单补场景参数（prompt 明令+确定性后置过滤：偏好类候选的数字/颜色锚点只能溯源到「模式/场景」用户话轮且无「记住/我喜欢」口吻→丢弃；PG 实证病灶=8 条 demo-* 会话钓鱼模式条目以 semantic/conf1.0/profile.preference 入库污染温度召回，2 条现行软标 superseded 清理）+**M2a** 显式记忆陈述（记住/我喜欢/我习惯）用户轮绕过 4 轮节流立即抽取（**真根因=一问一答 2 轮会话永远凑不满节流窗，26 度从未入库**）+**M2b** 常用车控偏好谓词归一 canonical（climate.temperature 等四类）+consolidate 冲突查找按谓词等价类回退 supersede（治 LLM 自由造词致新旧并存）；真栈 B3-3 旅程转绿（26 落地终态校验过）+PG 回读 canonical 现行。挂起密集旅程批 A2-3/A5-3/B2-1/B2-2/B3-1 全绿无双重播报。**收口尾项两件（2026-07-15 泓舟拍板按建议修）**：①A1-4 ②「明早」追问→timeparse 段位默认时刻 `_SEG_DEFAULT_HOUR`（dawn6/am8/noon12/pm15/eve20，**仅「日+段位」同时在场**才默认成单+display 回读可改期；裸日期/裸段位仍 NEED_TIME——「晚上」过点会滚明晚语义存疑故不默认），真栈 A1-4 理想形态「明天有雨…已经帮你设好明早8点的带伞提醒了」单句条件链零追问、A1-3 显式八点回归绿；②B2-3 前提翻面→**不为测试掰产品路由**（navigation 顺路途经点流对「路上/找个」形态是更优体验；根源=charging 无 route_hints 纯 LLM 掷硬币），照 B5-2 先例改用例前提为 charging 例句形态「去惠州怎么充电」（真栈探针 6/6 稳出 dest_choice），B2-3 两采样转绿（插话+「第一个」回填惠州站）。**墙钟 badcase 同日修复**（B2-3 插话轮暴露，obs 复核两采样**全错**：实际 14:25 答 14:43/10:06——14:43 只是编得像）：两层病灶=chitchat 锚只有日期被问钟点必编造 + planner 会把「现在几点了」误路由 info.search（exa 答不了墙钟→降级话术）；三件套修=chitchat `_clock_answer` 确定性直答（钟点/日期/星期**占据整句**才拦，防劫持「几点提醒我」；handle/stream 双路零 LLM）+ 锚补星期时刻 + **manifest route_hints 钉路由**（priority 61>info 59，R2.1 声明式不改编排核心）；`route_hints_cases` +8 84/84 基线刷新（原停 07-12 47 条）；真栈三采样「下午2点52分」@14:52 全对 2.2s 秒回、对照闲聊仍走 LLM。**收口后 canonical 全量重跑（2026-07-15 15:51，@MiniMax-M3——run 期间全局 provider 被切 M3，当日早间采样均 @mimo）**：**回归级 15/15 跨 provider 站住**、目标级 13/18（与 @mimo 16/18 不可直比）、时延 P50 4.9s/P95 25.5s；收口修复 B2-1/B2-3/B3-1/B3-2/B3-3/A2-4 在 M3 全绿=机制跨 provider 成立；5 红三分类：**B2-2 真缺陷已修**（M3 planner 误填 kind=todo、agent 盲信槽位静默跳过时间追问→「原话优先」护栏：显式提醒/叫我永远走定时提醒，+2 单测复跑绿）、A2-1 禁词被顺路补能话术误伤（语料摘除，复跑绿）+B5-2 裸序号方差按 B1-1 先例 retry:1（复跑绿）、A1-4 条件链 M3 方差与 B5-1 跨旅程残留（8 条当日测试提醒已定向软取消）记录不改。全量 pytest **1629 passed** |
| trip-planner 重构（结构化行程，2026-06-26）| ✅ **P0/P1/P2 全落地并合并 main**（merge `43d57b0`）。从「LLM 自由文本行程」重构为**结构化可执行行程对象**（`models.Trip→Day→Stop→Leg`）+「LLM 提议/确定性落地」四段流水线（`pipeline.py`：propose 只产骨架·只选参考 POI 池名字防幻觉→ground 接地真实 POI+name_matches 拒挂错名（接不到标 grounded=False 不臆造）→solve 算相邻车程+超日上限顺延+按真实 SoC 沿路线编织充电点→narrate 出话术+`trip_itinerary` 卡）；充电编织纯函数 `charging_planner/weave.py`；状态落 memory（profile KV `trip_active`）去 Agent 内存态；进程内复用 navigation POIProvider（跟随 charging 先例）。**P1**：`trip.navigate`（每停靠点一句话可导航——『下一站』按 cursor 推进 /『导航去第N天的X』/ HMI 行程卡停靠点可点）+ `trip.modify` 升级结构化 edit-op（加/删具体停靠点、跨天去重、只改受影响天）+ planning.py `_ensure_trip_navigate` 确定性路由。聚合器 `_card_priority` 给 `trip_itinerary` 高优先槽。确认轮直接收尾不死循环。**P2（在途编排）**：`trip.status`（在途进度：在第几站/下一站/还剩几站/全程补电几次，只读）+ `trip.reschedule`（时间不够/太累了/提前回→确定性砍尾部停靠点或最后一天，二次确认；注意"不要太累"是 plan 慢节奏偏好不触发）+ planning `_ensure_trip_status`/`_ensure_trip_reschedule` 路由（行程兜底重构为有序循环 导航>重排>状态>修改>新规划）+ modify 单天重规划跨天去重。详见 `docs/design/2026-06-26-trip-planner-redesign.md`(+ p0-implementation-plan)。真栈 `test/e2e_trip.py` 6 轮全过（结构化卡+真实 POI 接地+持久化跨轮+确认收尾+改某天不漂移+下一站导航+在途状态+在途精简）；compose 已给 trip-planner-agent 注入 AMAP_KEY/POI_VENDOR（真实 POI：西溪湿地/西湖/都江堰等），无 key 诚实降级 mock。**真实使用 UX 修复（2026-06-26）**：①modify 第N天返回同结果→`_replace_stop` 结构化换站（取未用 POI 或"换成X"目标）；②确认过期"当前没有待确认的操作"→SessionState TTL 90s→300s；③过程区首轮"编排行程：未完成"→engine 对 NEED_CONFIRM/NEED_SLOT 也发 done 事件（带"（待确认）"标注）；④泛地点（惠州海边）把民宿/别墅当景点→build_poi_pool 过滤住宿名 + ground() 把接地成住宿的景点整条丢弃（真栈：景点列表无住宿类）。latency_budget 40s |
| Registry 持久化（ws2 P0） | ✅ PgStore 实现（PostgreSQL），内存 fallback 保留；AgentClient 经 Registry 动态解析 endpoint |
| 安全门控增强（ws8 P0） | ✅ VAL 补充：高速禁开车窗/天窗、低电量禁高耗电、倒车禁非安全车控、儿童锁后排锁定 |
| 搜索质量重构 + 卡片重设计（2026-06-22） | ✅ Exa 正文级检索 + 接地合成（强制引用、无依据诚实弃权，删除旧「逼答」prompt）；新增 info.sports 经 api-football 给真实比分/赛程（按日期查+客户端过滤，免费档可用；队名英→中映射+国旗）；新闻改 Exa 优先+去重；卡片范式改为「气泡给结论、卡片只给证据」——search_result/news_brief/sports_scores（来源前3+更多、时效+置信度），消除结论复读。二轮修复合成超时/「明天」日期/卡片要点重复/AnySearch extract(MCP)。详见 `docs/design/2026-06-22-search-quality-and-card-redesign.md` |
| 信息域深调研重构（独立 deep-research Agent，2026-06-26）| ✅ **P0 已落地**：新建独立 `deep-research` Agent（`agents/deep_research/`，端口 50073，intent `research.run`，latency 85000）——四段流水线（LLM 提议 3-5 个 STORM 多视角子问题→确定性有界并行迭代检索 asyncio.gather+空结果换宽 query 再追一轮→分节接地报告(全局来源去重编号/无依据标 gaps)→一段式语音简报 + `research_report` 卡），对症「单轮检索多跳天花板」。检索/接地合成内核抽到 `agents/_sdk/{grounding,retrieval}.py` 注入式共享（info `_search` 切到共享内核、**零回归 122 passed**；搜索 provider 仍归 info、deep-research 进程内复用，避免 `_sdk→agent` 反向依赖）。`progress.py` HEAVY_INTENTS + `aggregator._card_priority` 给 research_report 独显槽 + `planning._ensure_research_step` 确定性兜底（触发词收窄=深入/深度/全面/系统+调研/研究/分析/对比，**不劫持普通"搜一下/查一下"**）。护城河=接地「我」(位置/电量/行程/画像)+渐进语音+可落地产物，非「车机版 Perplexity」。**P1 已落地**：`constraints` 注入位置坐标反查城市 + memory 画像语义召回；多轮研究上下文（落 memory `research_active`，「展开第N点/再深入第2节」聚焦上轮对应小节深挖、不重跑整份调研，编排补 `_RESEARCH_FOLLOWUP_RE` 路由）；报告「记一下」存记忆钩子。**紧前修复**：端侧 fast-intent 裸「电池」过度匹配成电量查询（劫持含「电池」的调研）→ 收窄为须与电量级/状态词同现。**上线后实测修复（3 问题，日志定根因）**：①调研只用一个信源+堆网页原文=分节合成**开思考**+大材料 40s 超时退化兜底→`synthesize` 改 **thinking=False**（深度在多轮检索不在合成步）；②「loop engineering」跑偏成「锂电池/电量72%」=P1 注入的 `vehicle_state=电量%` 污染子问题→`_constraints` 删电量注入、位置仅地理相关才注入、画像 min_score↑、plan 强约束紧扣主题不引入主题外领域；③`exa timeout` 大量=长句子问题+livecrawl×5 并发→子问题≤25字像搜索词、研究检索不开 livecrawl/不收窄时效；④网页噪声→`_clean_excerpt` 剔导航+合成 body 纯文本无 markdown；⑤**info.search 同源修复**（用户「同样处理」）：`grounded_synthesis`(info 与深调研共用的 _sdk 接地内核)默认 **thinking=False**，info.search 大页面不再合成超时堆原文(实测 13s 干净合成)；⑥**报告太短(~985字)**：子问题 5→6/检索 4→5/材料每节证据 2→3·正文 600→1000/`synthesize` max_tokens 1400→2400·要求每节 250-450 字综合多条来源 → 实测 **2153 字/6节/23源/59s**（压 85s 预算内，真·超深须异步 P2）；信源排查确认确是 Exa(Google/MS/IBM/腾讯云/BAAI/学术，混少量内容农场)。修复后真栈：loop engineering→准确定义 AI 工程范式、动态数据流→诚实纠正「并非全球首款」、报告深 2.2 倍。**P2 已落地**：①新闻个性化（`info._news` recall 画像兴趣→命中置顶）；②**深挖某条桥接**（info 落 `news_active`+编排 `_RESEARCH_FOLLOWUP_RE` 加「第N条/这条新闻」→research.run，deep_research `_resolve_news_deepen` 取第N条标题做调研；研究深挖 `_ORDINAL_RE` 去「条」专属新闻，真栈验证「看新闻→详细讲讲第2条」对该新闻出报告）；③主动早报雏形（`info.on_start` 订阅 vehicle.state.changed，晨间起步发 `agent.proactive`→edge 网关广播 HMI，复用 road-safety 范式）。测试：deep_research 21 + 编排路由 3 + 端侧电池 2 + 新闻 P2 4；真栈 e2e 全过。详见 `docs/design/2026-06-26-info-agent-deep-research-redesign.md` |
| conventions.md 同步 | ✅ Agent 清单表 + Intent 全集 + 端口表已更新（含 4 个新 Agent + trip.modify + charging.* + scene.* + safety.*） |
| 安全/权限/编排/协作/支付 | ✅ PoC 链路落地。**权限已单轨化（R2.2，已合并 main `0be9991`）**：三处权限实现（planning 内联过滤/dispatch 内联校验/PermissionEngine 死壳）收敛为唯一决策 `security/permission.py::check_permission`，规划期 `_filter_by_permission` + dispatch 执行期同源复用；删 `engine._enforce_permissions` 空壳；fail-open 由 env `PERMISSIONS_FAIL_OPEN` 门控（默认 on 保持现状，量产翻 false fail-closed）+ 结构化审计 `fail_open_default_scopes`。纠偏见 `docs/design/2026-07-02-r2.2-permission-single-track.md`（接 effective_scopes 会因扁平交集误拒 scene-orchestrator，取零行为变化单轨、trust-cap 强上限推迟）。**会话鉴权最小闭环已落地（R3.1，已合并 main `f38b4db`）**：静态 token 两层校验（层1 HMI↔edge-gateway `AUTH_TOKENS` 表→`meta.granted_scopes`+身份、去 `user_id="u1"` 硬编码；层2 Hello `CLOUD_CHANNEL_TOKEN`↔cloud-gateway `CLOUD_CHANNEL_TOKENS`），env 门控 `AUTH_REQUIRED` 默认关（保持现状），granted_scopes 由 token 注入不再只靠 fail-open；未改编排核心/proto（R2.2 已备好 `context.py` 消费）；真栈无 token→401、带 token e2e 全过、token'd 请求无 `fail_open_default_scopes`；见 `docs/design/2026-07-02-r3.1-session-auth.md`。**服务间 mTLS 已落地（R3.2，已合并 main `37817c8`）**：gRPC 双向 TLS，`GRPC_TLS` 门控默认关；单张共享 mesh 证书 + name override（`ssl_target_name_override`/`ServerName` 固定 `cockpit-mesh`）适配 agent 动态 hostname；Python 共享工厂 `runtime/grpcio.py`（`aio_channel` secure + `bind_port`）+ Go `gateway/tlscfg`；证书 `scripts/gen-certs.*` 生成（gitignore）；真栈 `GRPC_TLS=on` 全栈起 + `e2e_ws` 加密链路 + insecure 探针被拒（强制）；见 `docs/design/2026-07-02-r3.2-service-mtls.md`。**至此 T3.1+T3.2 齐，安全链路无已知缺口。剩余硬化**：真实 IdP/JWT 轮换/设备证书、per-service 证书轮换、正式沙箱与真实支付 |
| 可观测 | ✅ NATS 事件、collector REST/WS、车辆 diff、端云 span、Agent 健康/指标与独立 Dashboard；collector/registry 重启经周期快照与周期重注册自愈。**Prometheus/OTel 导出已由 R3.6 落地**（collector `GET /metrics` 手写 Prometheus 文本格式 + `otel_bridge.py` 桥接真实 OTel span，`GRPC_TLS`/`AUTH_REQUIRED` 同款 env 门控风格；compose 首次引入 `profiles: ["observability"]` 门控 prometheus/grafana，默认 `make up` 不受影响）；真栈数据链路已验证；**Grafana 可视化面板已由 R4.0/K3 补验（2026-07-04，三面板经 Grafana 数据源代理真实出数）**（见 `docs/design/2026-07-03-r3.6-observability-prometheus-otel-export.md` 与 `2026-07-04-r4.0-residual-cleanup.md`）。**badcase 排查贯通（2026-07-10，P0-P2 全落地+真栈验证）**：①ID 贯通——session_id 经 contextvar（`tracing.set_session_id`，与 trace 同模式，edge/cloud/SDK 入口 set 一次全事件/日志自动携带）、HMI 每轮自生成 trace_id 随 meta 上行+气泡角标复制（零 proto 改动）；②内容级事件——`obs.turn` 轮次收口（edge Handle wrapper 单点，本地/混合/上云/确认续接全覆盖，status=ok/rejected/clarify/need_confirm/cancelled/err/empty）+ `obs.llm`（llm-gateway 唯一出口收口：模型/tokens/时延/缓存/门控 prompt 尾+输出头；观测归属用独立 `caller_service` 键不扰动限流桶 `caller`）+ `obs.log`（`NatsLogHandler`：WARNING+ 恒发、带 trace 的 INFO 也发、obs.*/nats 排除防自激励；`setup_structured_logging` 死代码全服务激活，stdout JSON 自动带 trace/session）+ `cloud.planning` span 挂门控 plan JSON/LLM raw（`Plan.raw_llm`）+ `asr.stream` span（引擎/时延/门控定稿）；③collector SQLite 持久化（stdlib 零新依赖、WAL、named volume `obs-data` 重启不丢；turns UPSERT 保 badcase 人工标记；`OBS_RETENTION_DAYS` 默认 7 天清理、badcase 豁免）+ 新 API `/api/sessions`、`/api/sessions/{id}/turns`、`/api/turns/{id}`、`/api/search`、`/api/logs`、`/api/export/{id}`、POST badcase；④dashboard 四视图重构——会话三级下钻（默认页；搜索粘 trace_id 直达）/总览（原五面板+补显 route_hits/degrade/llm_tokens）/日志/badcase 收藏夹（一键重放对照）；⑤内容采集 `OBS_CONTENT_CAPTURE` 门控+统一脱敏 `observability/redact.py`（量产 off 只留长度+哈希）。真栈 `test/e2e_obs.py` **16/16** + CDP 前端 **12/12**；踩坑=llm-gateway/payment 镜像缺 nats-py 致 obs.llm 静默丢失（requirements 补包+events.py ImportError 改响一声永久禁用）、SDK import observability 后 8 个 Dockerfile 补 COPY。见 `docs/design/2026-07-10-dashboard-badcase-observability-redesign.md` |
| 熔断 | ✅ 已接入 `dispatch`（cloud/edge 调用前 `breaker.allow()`、开路快速失败 REJECTED/`circuit_open` 不再吃满超时、收响应=存活/异常=失败记账）；单 Agent 失败降级为 FAILED step 不炸整条 DAG。阈值经 `CIRCUIT_*` env 可调 |
| 通讯链路加固（comms hardening，2026-06-26）| ✅ P0/P1/P2 全落地。**P0**：①全链路 gRPC keepalive（新 `runtime/grpcio.py` 共享工厂，Python 全 channel/server + Go 网关三处 dial + 云网关 server params；空闲也 ping，根治"依赖重启换 IP 后需重启依赖方"的断连/无响应）；②全服务优雅停机（Python `run_aio_server` SIGTERM→`server.stop(grace)`、Go `GracefulStop`/`http.Shutdown`）；③HMI 韧性（`hmi/src/ws.mjs` 指数退避重连+断线有界发送队列不再静默丢消息+请求看门狗杜绝永久"思考中"）；④熔断接线（见上）；⑤AgentClient channel 复用+keepalive 消除每调用泄漏、`fork()` 透传 parent_meta。**P1**：LLM 网关上游 httpx 连接池复用（complete/stream/embed/ASR/TTS）+ 流式 per-chunk stall 超时；上游超时由调用方 gRPC deadline 自治理（cap 75s<90s 窗口），超时 abort `DEADLINE_EXCEEDED` 避免 SDK "UNAVAILABLE 重试一次"致延迟翻倍（曾因激进 deadline×0.9 派生令 info/trip 接地合成爆 step 预算"处理超时"，已回退修复 commit `2c2fd43`）。**P2**：依赖连接加固（Redis socket_timeout/keepalive/health_check/retry、asyncpg command_timeout/lifetime、NATS events reconnect -1）。**安全修复**：危险车控确认退化根因=catalog 预算裁剪丢掉 edge 车控核心（edge-vehicle 74 caps 撑爆预算）→ LLM 看不到 trunk → 空计划回退 chitchat 幻觉；已修（edge 核心紧凑渲染+裁剪保护 edge 核心∪always-include，`render_catalog`）。**真栈验证**：`test/e2e_resilience.py` 2/2（换 IP ~0s/~2s 自愈不重启依赖方）；全量 891 passed；smoke 13/13；中枢断言 7/7 + e2e_context 6/6（含 `dangerous_trunk_confirm` 转通过）。设计见 `docs/design/2026-06-25-comms-link-hardening.md`。**Dashboard 熔断面板**：dispatch 把 breaker 状态并入 Agent 指标→collector→Dashboard 渲染"熔断开/半开"徽标（真栈验证：并发突发打满阈值→`/api/agents` 返回 circuit=open；注意 registry 健康剔除常先于熔断触发，属正常分层防御）。**Go 网关换 IP 自愈补强（2026-06-26）**：三处 Go dial（edge→cloud、edge→orch、cloud→planner）之前用裸 host:port 走 passthrough resolver（解析一次、永不重解析），依赖容器**重建换 IP 后仍全请求"云端处理异常"需手动重启网关**（之前"根治换 IP"只覆盖了有 `_reset_channel` 的 Python 侧）——先改 `dnsTarget()` 强制 `dns:///`（连接失败重解析 DNS）；**但实测 dns:/// 单独并不可靠**（依赖换 IP 后 grpc-go 长时间不自愈；此前"~7s 自愈"系误判——探针走"你好"端侧快路径根本没打到 cloud-planner）。**真正修复**：仿 Python `_reset_channel` 给 cloud-gateway 加显式重连 `reconnectPlanner()`（`handleRequest` 遇 `Unavailable` 关旧 conn+新建强制重解析、重试一次，受锁保护幂等），真栈验证部署新 cloud-gateway 换 IP 后未重启 edge 即全链路自愈。另修复 corrID 撞车（请求挂起根因）：edge-orchestrator `cloud_client.py` 曾用 `id(request)`（Python 内存地址，对象 GC 回收后地址复用→不同请求拿到相同 corrID）→ cloud-gateway 幂等（**本就有 10min TTL**）误判重复、`handleRequest` 静默 return 不回包致客户端挂起；改 `uuid4` 根治。**mTLS/证书已由 R3.2 落地**（服务间 gRPC 双向 TLS，`GRPC_TLS` env 门控默认关；见安全/权限行与 `docs/design/2026-07-02-r3.2-service-mtls.md`）|
| LLM 调用 | ✅ MiMo API 已验证连通（同步+流式）；未配 key 时走 MockProvider；**思考(thinking) 动态开关**：`LLM_DISABLE_THINKING` 仅作全局默认，复杂任务经 `meta["thinking"]` 动态开思考（provider 不发 disabled 键 + token 抬到 2048，reasoning 留后端不下发），SDK `LLMClient` 从请求 `_current_meta` 自动判定（**所有 Agent 自动覆盖、无需改业务码**），Planner DAG JSON 恒不开。**多 LLM 源 + 全局切换（2026-07-07）**：`llm-gateway/llm_runtime.py` provider 注册表（MiMo/MiniMax-M3/DeepSeek v4-pro·flash/阿里百炼 qwen3.7 plus·max，百炼复用现有 embedding key），一套参数化 `OpenAICompatibleProvider`（`token_param`/`thinking_style`/auth 覆盖四家差异）；全局 active 经 `POST /api/llm/provider` 运行时切换（座舱「单一大脑」，所有服务共用），HMI 设置页「AI 大脑」两级选择（厂商→模型）+ 启动重放；chitchat 改传档位哨兵（`""`/`@fast`）由网关按 active 解析防跨厂商误发；**embedding 解耦**（独立按 `LLM_EMBED_*` 建，切非百炼 chat 厂商不影响记忆召回）。见 `docs/design/2026-07-07-llm-asr-tts-multiprovider-and-sports-flags.md` |
| 复杂任务过程区 + 动态思考 | ✅ 统一判据 `is_complex`（adaptive / 多步 / 含调研型重意图）同时驱动①动态开思考②过程区；engine 发 `ProcessUpdate` 四阶段脱敏事件（理解需求→规划步骤→执行任务[running 占位「正在查询天气…」+done 按 step_id 合并]→整理结果，**绝不含 prompt/reasoning/参数**）→ proto oneof `progress` → Go 网关 `eventToMap` → HMI 气泡内嵌折叠条（进行中显示已完成阶段概要+进行中步骤、完成默认折叠可展开四阶段时间线）；Edge 按 VAL 车速/档位标注 `driving` 做行车/泊车双态门控（行车极简不可展开）；普通车控/闲聊/单条轻查询零过程零额外延迟；两网关端到端超时 30s→90s、heavy Agent budget 放宽以容纳思考。**WS 长任务保活**：复杂任务执行期可能 30s+ 无 WS 流量，edge-gateway 对 HMI 连接加服务端周期 Ping（15s）防 idle 掐断丢过程区/最终答案（端到端 `test/e2e_process_region.py` 全过，后端/网关已验证投递过程区）。详见 `docs/design/2026-06-24-complex-task-thinking-and-process-region.md` |
| 确认闭环（F1） | ✅ 端到端打通（HMI→网关→编排器→Agent）；确认词判定改「占据整句」（`len≤词长+slack`），修掉"行程"含"行"、"可以换X"含"可以"、"不要去X"含"不要"被子串误判成确认/取消；挂起任务丢失时裸"确认/取消"不再被重规划成上一意图重复执行 |
| Docker 全栈联调 | ✅ 24 个容器全部运行（含 3 个新 Agent）；NATS healthcheck、collector、dashboard 通过 |
| E2E 测试 | ✅ 4 条标准链路有历史通过记录；2026-06-14 另完成 2 条慢意图/复杂意图场景全栈回放 |
| 车控知识库 | ✅ commands.yaml 62 对象 + entities.yaml 532 实体 + responses.yaml 78 条话术；VAL 结构化执行流水线（归一化→校验→安全门控→模拟→选话术）+ answer_length 简繁切换；车窗开合度 inc/dec、大灯行驶中禁关（drive_restricted_off）、电量/续航查询端侧确定性应答（『还能跑多远/续航/能跑多少公里』等剩余里程问法→battery.query 走端侧，不漏到云端被弱 LLM 误判闲聊；『开车去X多远』是距离查询不误命中）|
| 端侧意图覆盖 | ✅ 150 条意图 pattern（fast_intent），覆盖 62 对象（车控/媒体/蓝牙/WiFi/电话/广播/音乐/视频/导航/360环视等）；飞书公版数据全量导入（1465 意图） |
| 多意图拆分 | ✅ 端侧按语义组分流：本地动作走 VAL，导航路线偏好、歌曲/歌手等续接片段与主意图完整上云；云侧 Planner DAG 强化 |
| ASR/TTS | ✅ HTTP 代理 + MiMo ASR/TTS(批处理) + webm→wav 转码 + 9 音色；HMI 句子级增量合成与顺序播放。**流式识别上屏（2026-06-30）**：WS `/api/asr/stream` + 流式 ffmpeg，引擎经 `ASR_STREAM_PROVIDER`/请求可切（工厂按模型名路由）——DashScope 实时 qwen3（`qwen3-asr-flash-realtime-2026-02-10`**全小写**，OpenAI-realtime 协议 `/realtime` base64）/ fun（`fun-asr-realtime`，run-task 协议 `/inference` 二进制帧）/ MiMo 分块回退；HMI 边说边上屏、松手定稿自动发送、失败无感回退批处理。真栈 e2e：qwen3 5partial/fun 4partial/mimo 均出「今天杭州天气怎么样？」+ fake-mic 浏览器上屏→助手出天气卡。坑：qwen3 id 须全小写（CamelCase 送音频 1011）、fun 与 qwen3 端点/协议不同。见 `docs/design/2026-06-30-asr-streaming-design.md`。**服务端流式 TTS + barge-in（R4.2，2026-07-06，P0-P3 全落地+真栈闭合）**：WS `/api/tts/stream`（文本增量入→meta+PCM 二进制帧+done），DashScope 双引擎经 `TTS_STREAM_PROVIDER` 可切——cosyvoice-v3-flash（run-task，469ms 首帧，默认）/ qwen3-tts-flash-realtime（realtime，含北京/上海/四川方言）；MiMo 批处理保留回退。HMI 音色选择重设计为**引擎→音色两级**（`settings.ttsProvider`+voiceId，同 ASR 引擎范式），`pcmPlayer.mjs` PCM 分片无缝拼播、失败无感回退句级批处理。barge-in v1=既有 stopTTS 三触发点（发新消息/按麦/hands-free FSM）关流式 WS 发 cancel→供应商取消。首音提速 4.7~7.2×（批处理 3375ms→流式 469/719ms）。真栈：`test/e2e_tts_stream.py`（cosyvoice 首帧 532ms/G1 达标/cancel 收尾）+ 真浏览器 CDP 三态（流式 40 帧+meta+done / 无 key→unsupported→batch / barge-in 发 cancel+关 WS）。见 `docs/design/2026-07-04-r4.2-streaming-tts-bargein.md`。**TTS 扩展 + ASR 核对（2026-07-07）**：新增 **MiniMax TTS**（`MiniMaxStreamingTTSProvider`→`/v1/t2a_v2` stream，hex 音频，与 MiniMax LLM 同 `MINIMAX_API_KEY`）+ **MiMo TTS 升流式**（`MiMoStreamingTTSProvider`→chat `stream:true`+`pcm16@24k`），二者 API 均「整段文本一次入」→ 共享 `providers._sentence_segments` 句级切分逐段合成边说边播；`TTS_STREAM_CATALOG`/`/api/tts/stream/info`/HMI `TTS_PROVIDER_FALLBACK` 增 minimax + mimo 升流式（设置页两级选择自动多两引擎）。**ASR 核对结论：MiMo `stream:true` 仅输出文本流式、音频仍须整段一次性传入，不构成实时增量 ASR → 真实时上屏保持 DashScope，MiMo ASR 逐字不动**（诚实核对，非 bug） |
| HMI（前端） | ✅ 组件化 + 设置页 + 流式渲染 + 记忆视图 + 语音按钮 + **信息类 UI 卡片**（天气/股票/搜索/新闻/深调研/POI/路线/充电/行程/赛事，Gateway→Cloud→Edge 全链路 ui_card 透传）。**视觉重构（2026-06-30）：Aurora Glass · 极光液态座舱（横屏 1920×1080 两栏 + 右上下文舞台 + 液态玻璃 + 极光签名渐变 + 小舟光球）——P0 设计系统 / P1 两栏外壳与舞台 / P2 ~20 卡(A-3~A-5) / P3 对话动态六态(A-6) / P4 设置横屏侧栏(A-7) / P5 浅色主题(§12 契约) / A-4 信息卡按源重建 / A-5 右舞台数据驱动地图(POI 测距环·route 流动虚线·charging SoC·行程按天) / A-8 图标库(39 设计图标→`Icon.tsx`+`icons.gen.ts`，补齐 16 个→`icons.custom.ts`，4 态含 aiMoment 极光，emoji 全替换；图标已推回 Figma A-8 页) 均已落地+push（commits `2ad83e3`→`39e65a4`，types.ts 数据契约不动，`npm run build`/`node --test` 38/38 绿，Edge 截图逐屏核对）。**✅ 已重建 hmi 容器 + 真后端全栈 e2e 验证（2026-06-30）**：CDP 驱 headless Edge 打真后端，天气/POI/股票/新闻/调研/赛事/充电/行程 8 卡族真数据渲染 + 过程区 + 确认条 + 4 地图舞台全对；**语音按钮换成小舟光球**（au-mic 按钮本体即 AuroraOrb，state 驱动）+ **剩余 21 处 emoji 全替 A-8 线性图标**（补 5 个 custom：search/newspaper/clock/check-circle/settings）+ **ASR 流式识别上屏**（见上 ASR/TTS 行）。待做：P5 行车态变体(A-8 帧未出)、P6 Dashboard(B 帧未出)；A 类数据缺口(搜索/新闻类目芯片等需先扩 types.ts)。见 `docs/design/2026-06-29-figma-hmi-implementation-plan.md`** |
| 开放域流式 + 模型分层 | ✅ engine 单步 ExecuteStream 直通 + chitchat 快模型/兜底；降规划延迟待做 |
| 对话上下文/指代 | ✅ engine 写对话记忆 + 规划注入历史 + **注入长期偏好记忆**；端侧本地轮 best-effort 写共享记忆 |
| 记忆系统（分层重构，2026-06-25）| ✅ 从 mock KV 重构为分层语义记忆：单表 `memory_item`+pgvector；自动抽取偏好/个人实体（四分类写策略+抽取黑名单+PII 防护，宠物/家人称呼可记）、`superseded_by` 时序-lite、语义召回注入 planner、chitchat 记忆感知作答、routine→`agent.proactive`（edge 网关 NATS→HMI WS 投递）、places 镜像收敛（navigation 零触碰）、隐私分级+GDPR 硬删。**embedding 走 llm-gateway→阿里云百炼 text-embedding-v4**（1024 维，真语义实测：字面零重叠也能召回）；无 `LLM_EMBED_API_KEY` 诚实降级 lexical。HMI 记忆页展示真学到的偏好/地点/经历、可删。**测试**：8 例复杂场景集（`memory/tests/test_scenarios.py`）+ 6 链路断言型全栈 E2E（`test/e2e_memory.py`，真栈 6/6）。详见 `docs/design/2026-06-25-memory-system-redesign.md` + 实施计划 |
| 上下文系统重构（2026-06-25）| ✅ 承接记忆重构后裸着的 working/core 层，5 期全落地（883 passed/6 skipped，零回归）：①统一 `ContextManager`（`orchestrator/cloud/context.py`）装配 catalog/历史/记忆/焦点，统一字符预算 + catalog 语义预筛（agent 数 ≤K no-op、收益随规模兑现）；②结构化焦点态 `Focus`（对象/位置/属性/上个 POI，独立 Redis 存、跨轮指代）；③`build_context`/`append_turn`/`_history`/`_recall` 收归门面；④敏感上下文按 manifest `context_scopes` 最小化下发（proto field 13，cloud unary 路径过滤，edge/stream 不动）。两处取舍（不做 prefs 类型重写、Phase 4 过滤边界）+ e2e 抓出并修复的一处回归（预筛误丢 edge 车控→危险动作确认退化，已修：K 默认 20 + edge 核心始终保留）见 `docs/design/2026-06-25-context-system-redesign.md` §8。**真栈 e2e 验证**：中枢断言 7/7 + e2e_ws 4 链路 + 上下文断言 6/6（`test/e2e_context.py`）全过 |
| 飞书数据全量导入 | ✅ lark-cli 拉取 5 张公版表（意图 1465 条 + 分类 400 + 词库 5185 + 响应 3000 + 兜底 34）；3 个生成脚本可重跑（`scripts/gen_commands_yaml.py` / `generate_entities.py` / `generate_responses.py`） |
| 全仓审计与 Roadmap（2026-07-02）| 📋 见 `docs/reviews/2026-07-02-repo-audit-and-roadmap.md`：架构一致性 8 偏差 + 20 技术债 + 8 测试/量产缺口 + 四阶段 Roadmap（R1 门禁与卫生→R2 架构还债→R3 量产硬化→R4 能力演进）。**R1 全 5 卡已落地并合并 main**：media action_type 统一（T1.5 `edge_call.action_type_for`）/ compose `restart`+healthcheck（T1.3）/ 文档同步（T1.4）/ CI 补全「CI 绿=本地全量绿」（T1.1，pytest 分组隔离+聚合 requirements+Go/前端 job）/ 删孤儿脚本+空目录（T1.2）。**R2.1 恢复「编排对 Agent 无感」铁律 P0–P2c 已落地**：编排核心 `planning.py` 的 6 处路由确定性兜底（`_ensure_research_step`/`_ensure_trip_navigate·status·reschedule·modify`/`_ensure_trip_step`/`_extract_trip` + 全部 `_TRIP_*`/`_RESEARCH_*` 正则）**全部机制化**——proto 加 `RouteHint`/`Capability.heavy`（P0）、新增通用 `orchestrator/cloud/route_hints.py::RouteHintEngine`（P1，priority 降序/replace 互斥/append 并列/guard/$text·$N 模板）、research + trip.navigate/status/reschedule/modify **逐字**迁各 Agent `manifest.route_hints`（P2a/b）、trip.plan 迁 append hint + 目的地抽取搬入 `agents/trip_planner/src/extract.py`（P2c，触发门控逐字验证 12 例与原 `_extract_trip` 决策一致）+ DoD#2 契约测试。**P3/P4/P5 亦已落地**：P3 HEAVY_INTENTS→`capability.heavy`（Step.heavy 经 _validated_steps 落地，progress.is_complex 读之）；P4 card 优先级→card 自带 `display_priority`（aggregator 通用取值，删硬编码卡类型表）；P5 `_ALWAYS_INCLUDE`→env `PLANNER_FALLBACK_AGENT` + 通用「有 route_hints 的 Agent 始终留 catalog」（`_always_include`）。**至此 planning/context/aggregator/progress 四处硬编码全清，编排核心零领域 Agent/意图字面量**（chitchat 仅 env 默认值）。**真栈修复**：registry PgStore round-trip 曾丢 `route_hints`/`heavy`/`context_scopes`（`_dict_to_manifest` 补映射，737ddef，单测用 MockAgent 漏此路径、须真栈才暴露）。**验证**：全量 **998 passed / 6 skipped**；重建 6 镜像后**真栈 `e2e_trip`（trip.plan/navigate/status/reschedule/modify）+ `e2e_research`（research.run/深挖/普通搜索不劫持）全过**。残留 `_PLANNER_SYSTEM` trip 少样例属 D10 prompt 管理（非 D5，不随 Agent 数增长）。**R2.2 = T2.2 权限单轨化已完成并合并 main（`8999cba`/`0be9991`）**：三处权限实现→唯一 `check_permission`（规划期过滤+dispatch 执行期同源）、删 `engine._enforce_permissions` 空壳、fail-open 加 env `PERMISSIONS_FAIL_OPEN` 门控+结构化审计；对审计"接线 effective_scopes"纠偏——扁平 `cap & granted` 不做父子覆盖会误拒 scene-orchestrator，取零行为变化单轨、trust-cap 强上限推迟 R3.1；全量 1014 passed+真栈 `e2e_ws` 4/4；见 `docs/design/2026-07-02-r2.2-permission-single-track.md`。**R2.3 = T2.3 端云持久长连已完成并合并 main（`c7cdc01`/`ae8638d`）**：Python `CloudClient`（edge-orchestrator）逐请求建流→进程内单条持久 bidi + corr_id 多路复用 + 15s 心跳 + 指数退避重连（每次重连重建 channel 走 dns:/// 重解析换 IP 自愈）+ 在途断连快速失败降级；云侧 `channelServer.Connect` 本就多路复用未改；删 Go 死代码 `gateway/edge/ChannelClient`（~250 行，A2/D2，含 A3 文档补记「持久通道归属 edge-orchestrator」）；全量 1016 passed+edge-gateway 镜像 go build 通过+真栈 e2e_ws 4/4+持久性探针（3 云请求仅 1 hello）+换 IP 自愈探针（force-recreate cloud-gateway 未重启 edge 即自愈）；见 `docs/design/2026-07-02-r2.3-edge-cloud-persistent-channel.md`。**R2.4 = T2.4 info agent 拆域已完成并合并 main（`def815a`/`18e6f73`）**：1269 行 `InfoAgent` 巨类按域拆成 `agents/info/src/handlers/{weather,search,sports,news,stock,briefing}` mixin + 共享 `_util`，`agent.py` 只留意图分发+公共件+provider 装配（1269→123 行）；域方法经 self 靠 MRO 逻辑逐字不变、文件尾向后兼容重导出历史 helper（测试零改动）；manifest/端口/行为不变；`pytest agents/info` 136 passed + 全量 1016 passed 零回归；见 `docs/design/2026-07-02-r2.4-info-agent-split.md`。**R2.5 = T2.5 跨 Agent 状态键契约化已完成并合并 main（`9b1167c`/`0b390a6`）**：三个隐性契约键（news_active/research_active/trip_active）登记入 `agents/_sdk/shared_state.py`（常量+owner/reader/schema 表）+ `conventions.md §9`，`Context` 加 `save_shared_state`/`load_shared_state` 封装读写 `profile.` 前缀不对称，info/deep-research/trip-planner 全改常量+helper（业务码零裸字面量，grep 仅 shared_state.py+文档）；全量 1016 passed 零回归。**至此 R2 架构还债 R2.1–R2.5 全部完成。** **R3.1 = T3.1 会话鉴权最小闭环已落地并合并 main（`f38b4db`）**：解决 D1（P0-#1 全链路零鉴权）的鉴权/token/user_id 部分——静态 token 两层校验全 env 门控默认关（`AUTH_REQUIRED`）、`granted_scopes` 由 token 注入、去 `user_id="u1"` 硬编码；**未改编排核心/proto**（R2.2 已备好 `context.py` 消费 `meta.granted_scopes`）；全量 1018 passed + 真栈默认 `e2e_ws` 4/4 + 秒模式 `e2e_auth` ALL PASS（无 token→401、token'd 请求无 `fail_open_default_scopes`）；见 `docs/design/2026-07-02-r3.1-session-auth.md`。**R3.2 = T3.2 服务间 mTLS 已落地并合并 main（`37817c8`）**：gRPC 双向 TLS，`GRPC_TLS` 门控默认关；单张共享 mesh 证书 + name override 适配 agent 动态 hostname；Python 共享工厂 `runtime/grpcio.py`（`aio_channel` secure + `bind_port`，7 处 server 绑定切换 + 修 1 stray）+ Go `gateway/tlscfg`（两网关 3 dial + cloud server）；`scripts/gen-certs.*` 生成证书（gitignore）；compose 挂 19 mesh 服务；**未改 proto/编排核心**；全量 1030 passed + Go build/test（含 tlscfg）+ 默认 `e2e_ws` 4/4（非破坏）+ mTLS 模式全栈起 + `e2e_ws` 加密链路 + `e2e_mtls`（云端 mTLS 通 + insecure 探针被拒=强制）；见 `docs/design/2026-07-02-r3.2-service-mtls.md`。**至此 T3.1+T3.2 齐，安全链路无已知缺口（D1 收官）。** R3.3–R4.0 见下方续段 |

**R3.3 = T3.3 e2e 入 CI 门禁已落地并合并
main（`e54a914`/`cb70239`/`25b85aa`）**：新 `.github/workflows/nightly-e2e.yml`（schedule+
workflow_dispatch，全 mock 零 secrets）跑裁剪确定性子集（ws/central_hub[3 case]/context[4 case]/
memory/resilience/trip/research/research_async，比卡片字面 5 个多纳入 mock 下同样可靠的
trip/research/research_async）；纠偏：卡片字面 5 个脚本纯 mock 下不能整份跑通（无 route_hints 的
Agent 兜底落 chitchat），用 `--case` 过滤 + `e2e_memory.py` 三条依赖真实 LLM/embedding 的链路补
SKIP guard 解决；首次 GitHub 实跑发现链路 2「planner召回注入」遗漏（弱字面重叠召回同样依赖真实
embedding，前期分析漏判），修复后二次实跑全绿（run 28639607108，3m59s）；`make e2e` 改用
`scripts/run_e2e.{sh,ps1}` 本地全量清单执行器（原 `cd test && pytest -q` 收集不到任何
`e2e_*.py`）；**未改编排核心**；全量 1030 passed 零回归；见 `docs/design/2026-07-03-r3.3-e2e-ci-gate.md`。
**R3.5 = T3.5 降级矩阵自动化已落地并合并 main（`0355b1b`/`02a4896`）**：新 `test/e2e_degrade.py`
刻画架构 §3.3 四行真实现状——单 Agent 故障（trip-planner-agent stop/start，唯二 mock 下路由确定的
Agent，断言可观测 span status 而非聚合器话术原文）/ LLM 超时（`MockProvider` 新增
`LLM_MOCK_DELAY_MS` 测试钩子）/ 云 Planner 故障（cloud-planner stop/start）/ 断网（cloud-gateway
pause/unpause）。真实跑（非纸面设计）暴露两处需推翻重来：①原计划断言命中
`aggregator._ERROR_FRIENDLY["step_timeout"]` 固定话术不成立（chitchat 走 D0 流式直通不受
executor 超时管辖，heavy Agent 预算又放宽到 200s 测不出来），改断言"系统变慢时仍优雅响应"这一更
朴素但真实成立的性质；②额外发现第 4 处非本卡引入的缺口——cloud-gateway pause/unpause 后
edge-orchestrator 不像"换 IP"场景那样自愈，恢复步骤加显式重启兜底（不修，记录留后续）。**未改
编排核心**；全量 1030 passed 零回归 + GitHub `workflow_dispatch` 一次实跑全绿（run
28643924654，9m17s，未像 T3.3 那样需要二次修复）；见 `docs/design/2026-07-03-r3.5-degrade-matrix-e2e.md`。
**R3.4 = T3.4 意图路由评测基线已落地并合并 main**（`feat/r3.4-intent-eval-baseline`）：新
`test/eval_fast_intent.py`（端侧 `classify_structured`/`split_and_classify_any`）+
`test/eval_route_hints.py`（云侧 `RouteHintEngine`，复用生产同款 `PlanBuilder._validated_steps`
装配路径）直调既有函数产出准确率/召回率报告（JSON+Markdown），基线入
`docs/reviews/eval/`；`ci.yml` 新增非阻塞 `intent-eval-baseline` job（`::warning::` 告警不拦
PR）。**对卡片"飞书 1465 意图库"的纠偏**：原始表已 gitignore 且磁盘不存在，只一次性用于生成
`commands.yaml`/`entities.yaml`，未保留标注语料；改用现有 `orchestrator/edge/tests/corpus/`
+ 新增 `test/eval_corpus/` 历史回归转录（edge 39 条/route_hints 8 条），飞书全量语料列为后续
增强不阻塞验收。**关键发现**：`route_hints_cases.yaml` 预期值用 `--dump` 对真实
`agents/trip_planner/manifest.yaml` 实测校验（不照抄 `test_route_hints.py` 简化版单测
fixture）后发现"导航去第2天换一个"真实行为是被同句命中的 `trip.modify`（无 guard）接管，
不是简化单测暗示的"guard 拦下=空路由"，已按实测钉入基线。两套逻辑均不经 LLM，"跌破阈值"
落地为逐例回归比对；验收演练（临时改坏电池共现词检查+`deep-research` pattern）均精确触发
告警后撤销。**未改 `fast_intent.py`/`route_hints.py`/编排核心任何业务逻辑**；全量
**1037 passed/6 skipped**（+7）零回归；已合并 main 并 push，GitHub Actions
`intent-eval-baseline` job 随 push-to-main 实跑确认全绿；见
`docs/design/2026-07-03-r3.4-intent-eval-baseline.md`。
**R3.6 = T3.6 Prometheus/OTel 导出已落地**（`feat/r3.6-observability-export`）：collector 新增
`GET /metrics`（手写 Prometheus 文本暴露格式，零新依赖，六个 `cockpit_agent_*` 指标覆盖时延/
错误率/熔断态/健康）+ `otel_bridge.py`（复用 `observability/tracing.py::setup_tracing()`——
此前完整实现但从未被任何服务调用的死代码，桥接 NATS `obs.span` 为真实 OTel span，trace_id
sha256 哈希成确定性 128-bit ID 保证同 trace 分组，不做字节级父子 SpanContext 链接因为现状
`parent_id` 几乎不被真实调用点填充）+ `deploy/docker-compose.yaml` 新增 `prometheus`/
`grafana` 两服务（**本仓首次引入 Compose `profiles` 机制**，`profiles: ["observability"]`
门控，默认 `make up` 不受影响）+ Grafana provisioning 与三面板 dashboard JSON（延迟/成功率/
熔断状态曲线）。**真栈数据链路已验证**：对真实运行的 26 容器技术栈跑 `test/e2e_ws.py`
制造真实流量，`/metrics` 正确输出 `nearby` 等 Agent 的真实调用数/延迟；OTLP 三个新
依赖（`opentelemetry-api/sdk/exporter-otlp-proto-grpc`）经容器内 `pip install` 验证零版本
冲突。**Grafana 可视化面板未在本次会话验证**——本机网络环境当前对大文件/大数据块持续下载
不稳定（pip 装 grpcio、docker 拉 prometheus/grafana 镜像层均卡死，交叉换阿里云 PyPI 镜像+
daocloud Docker 镜像源验证过是本机网络环境问题、非代码/依赖问题），经用户确认按当前验证
程度收尾，留待环境恢复后补验证。**全量回归 897 passed/5 skipped 零失败**——排除 4 处与本卡
无关的预先存在环境依赖测试（`test/test_asr_e2e.py` 需真实 LLM API、`llm-gateway/tests/
test_transcode.py` 需本机未装的 ffmpeg 二进制、`observability/tests/test_events.py`/
`agents/info/` 疑似受本机真实 NATS/服务可达性影响行为不同，均未修复只排除验证范围；诊断
踩坑：`cmd | tail; echo $?` 拿到的是 tail 退出码非真实进程退出码，需用 `${PIPESTATUS[0]}`
才能正确识破"空输出但成功"的假象）。**未改 `orchestrator/cloud/{engine,dispatch,loop,
circuit}.py`/`observability/metrics.py`/`agents/_sdk/*`/`observability/collector/
store.py` 任何现有逻辑**；见 `docs/design/2026-07-03-r3.6-observability-prometheus-otel-
export.md`。**至此 R3 量产硬化全部完成（T3.1-T3.6）。**
**R4.0 收尾包已完成（2026-07-04，见 `docs/design/2026-07-04-r4.0-residual-cleanup.md`）**：清验收复审
（`docs/reviews/2026-07-04-acceptance-review-r1-r3.md`）§4 三项残留 + §3b 一项已知边界——**K1** 端云持久
通道 pause/unpause（同 IP 冻结再解冻）不自愈（真根因=app 心跳强制重连时 `_cancel_stream()` 令 `read()` 抛
`CancelledError`，被 `_run` 当任务取消 re-raise 打死重连循环；换 IP 场景走 grpc keepalive 的 `AioRpcError`
故不中招）→ `_run` 用 `_closing` 区分「流被取消/任务被取消」+ `_open()` 有界超时，真栈解冻后 ~2s 自愈、
e2e_degrade Row 4 由 restart 兜底改回自愈断言、+2 双向守护单测；**K2** `e2e_process_region.py` 默认泊车态
断言在长期共享栈上因 VAL 调试态污染而失败 → 断言前经 collector `POST /api/debug/vehicle` 复位泊车态使测试
自足（污染态 fail→复位 pass 双向验证）；**N1** R2.2 单轨化后 `PermissionEngine` 死注入彻底删除（编排层不再持
权限引擎，留注释声明 trust-cap 未来扩 `check_permission` 而非复注入）；**K3** Grafana 面板本次网络恢复完成
验证（起 observability profile，Grafana→Prometheus→collector 三面板经数据源代理真实出数）。**未改 proto/
编排核心/架构**；全量 1050 passed。R4 主线见审计。 |
| R4.1 路由质量（P0-P3，2026-07-04）| 🚧 **P0 Registry 真语义路由 + P1 Resolve 评测基线 + P2 语料资产化+覆盖率报告 + P3 纯 pattern 扩规则（B1 气象/B2 设置页族）已落地**（本地合并 main、**未 push**，待泓舟发话）。**P0**（`d596da9`，全在 `registry/`+compose 一处）：registry 语义检索从 **sha256 伪向量**换为经 llm-gateway→百炼 text-embedding-v4 的**真向量**（按 capability 粒度 `agent_capability_vec` 表 + text_hash 去重防周期重注册打爆 API + `SEMANTIC_MIN_SIM` 下限 + query 向量 FIFO/TTL 缓存 + 维度读 `LLM_EMBED_DIMENSIONS` 不符 DROP 重建 + 启动时序按需重探），**顺带修 §1.1 低频误路由 bug**（registry 镜像从未装 sentence-transformers→每次注册写 sha256 伪随机向量→关键词低分 query 被无下限追加的伪随机 Agent 接走）；无 embedding 源/llm-gateway/PG 不可达**诚实降级关键词路径、绝不哈希伪语义**（nightly 纯 mock 零感知）。**P1**（`1d35877`）：`test/eval_registry_resolve.py` + 20 条 golden 用例 + 基线入 `docs/reviews/eval/baseline_registry_resolve.{json,md}` + `ci.yml intent-eval-baseline` 非阻塞挂钩；离线关键词层 15/15（`--dump` 校准），`--semantic` 直连活栈跑全量。**P2**（`c67dc7f`）：`scripts/gen_intent_corpus.py` 经 lark-cli 重拉意图表 1465 行 → `test/eval_corpus/feishu_intents_full.jsonl` **8590 条唯一说法**（拆行全局去重、可重跑幂等）；`eval_fast_intent.py --corpus full` 出覆盖率报告 `docs/reviews/eval/coverage_fast_intent.{json,md}`——**总体 72.04% 复现 gap-analysis 72.0%（自校验通过）**、端侧应接子集 75.6%（§5.3 甄别：导航播报=true/搜路线=false、交互裸「取消」=false 规则化进 gen 防重跑冲掉）；CI 挂钩（总体跌 >1pt→`::warning::`）。验证：registry 23 passed + 全量 **1066 passed/7 skipped**（+16 零回归；P1/P2 只加不被 pytest 收集的 eval/gen 脚本）+ 真栈重建 registry 后 `agent_capability_vec` **39 行/12 agent 全 1024 维真向量** + 直调 `resolve_semantic` **4/4 语义 top-1**（「补能」→charging-planner 纯语义改写句）+ `e2e_ws` 4 链路非破坏。**P3**（纯 pattern 扩规则，分批「先反例后规则」+ 覆盖率/39基线/13smoke/全量四重护栏）：**B1 气象并入天气类**（`f7df497`，排除气象局=地点/预警=云端 alerts，+2.02pt）+ **B2 设置页/界面开合族**（`2db210a`，page catch-all 补关闭方向+界面/页面通用兜底+read-content guard，**修「打开设置里的隐私协议给我读一下」被误接成 page/settings 的既有劫持**，+1.68pt）→ 覆盖率 **72.04%→75.74%（+3.70pt）**；策展基线 39→50 全 PASS，全量仍 1066（fast_intent 规则改动零回归）。**P3 真栈发现（推翻设计前提）**：设计 B2 估 +400~500 的大头「辅助驾驶 482」实为 **ADAS 功能开关长尾**（限速提示/手持电话监测/危险驾驶监测…12+ 种各≤12 句）每个需独立 VAL 对象；**B3 导航播报**（navigation 是 online_only，需新建 navi_broadcast 端侧对象）+ 空气净化同属「端侧对象化」——非 quick-win 扩规则，**82% 目标另立卡 + 触发 §7 K6 重评估**（泓舟拍板诚实收官本轮扩规则）。**P0 真栈发现（已修）**：语义引擎核心正确，但曾被既有关键词 `_score`（`0.3+0.05×字符交集`对中文普遍 ≥0.5、压住 `best_score<0.5` 钩子 + 只追加不重排）**遮蔽**，完整 `ResolveAgents` 路径 15/20——**已由语义重排修复**（`dea88ce`：server `ResolveAgents` 无精确 intent 命中时总是跑语义，top sim ≥ `SEMANTIC_PROMOTE_SIM`=0.5 则语义排序在前纠正关键词噪声 top-1、否则保守追加；精确命中 1.0 不覆盖；无源 byte 一致）→ 真栈 `--semantic` **15/20→20/20**、+3 单测、全量 1069 passed 零回归。见 `docs/design/2026-07-04-r4.1-routing-quality.md` §10 |
| 数据真实性治理（P0，2026-07-17）| ✅ **禁静默回退 mock（层1+层3）已落地并真栈验证**：`agents/_sdk/provenance.py` fail-fast（显式 real 意图——vendor env 显式非 mock 或配了该域凭证——下构造失败抛 `ProviderConfigError` 启动即炸；默认 env 永不触发，CI mock 车道零破坏）+ 10 域工厂统一决议日志 `provider[<domain>]=<vendor>(real)|mock`（真栈重建 8 容器后 grep 一屏：weather=qweather/search=exa/news=serpapi/stock=tushare/sports=api-football/poi=amap/place=amap/charging=amap 全 real，knowledge/parking=mock 符合 .env 事实）；**顺带删 news 运行期 mock 回退**（真实源失败改诚实空列表，与 weather/alerts/stock 口径对齐）；embedding 伪向量疑点核实无害（memory 维度探测 384≠1024 挡住→lexical）；ui_card 保留键 `_prov` 契约登记 conventions §9.3/§9.4（生产点在 P1）。+25 工厂契约单测，全量 1661 passed/7 skipped。**P1 同日落地并真栈验证**：`provenance.attach()`+全工厂来源章（`log_resolution` 扩 provider 参数，推广就绪）+试点三族出卡盖章（weather/search_result/place_list·place_detail）+HMI `ProvBadge`（mock 琥珀「模拟数据」醒目/degraded·cached 灰/real 小字来源·取数时间角标）+**删 nearby 运行期 mock 回退**（search/detail 真实源失败改诚实 FAILED——假餐厅可能被导航过去；news 后同类第二例，运行期盲区清零）；+8 单测；真栈 WS 探针天气卡 `_prov={real,qweather}`、周边卡 `{real,amap}` 全链路到端。**P2 同日落地并真栈验证**：`REQUIRE_REAL_PROVIDERS` 严格闸双侧（agents `log_resolution` 决议收口拒 mock + llm-gateway llm/embed/asr/tts 四闸；豁免 `REQUIRE_REAL_EXEMPT` 默认 parking,knowledge；compose python-env 锚下发；真栈一次性容器抹凭证实拒 `provider[stock]` mock）+ `_prov` 推广至 13 外源卡族（forecast/stock_quote[东财降级如实标 degraded]/news_brief/sports×2/poi_list×多/route_plan/poi_detail/charging_route；trip_itinerary、research_report、内部数据卡**刻意不标**——卡内已有更强证据链）+ 泄漏探针 `test/e2e_strict_stack.py` 进 run_e2e 清单（真栈三问全 real；mock 栈自动 SKIP）。全量 1695 passed/7 skipped。**已归档入架构 §9.5**；决策与落地记录见 `docs/design/2026-07-17-data-authenticity-governance.md` |
| 多模型运行时硬化（P0，2026-07-17）| ✅ **active 持久化 + 评测 provider 锁定已落地并真栈验证**：llm_runtime active 选择持久化 Redis `llm:active`（重启/重建不再回落 env 默认——07-12「重建回落」事故根治；真栈 restart 后 minimax:MiniMax-M3 保持；Redis 缺包/不可达降级内存态不拒启）；`eval_common.ProviderLock`（pin + 逐旅程/跑后漂移守卫，漂移=报告作废、退出码 1）接入 `e2e_journeys.py --provider` 与 `eval_mode_routing.py --live --provider`（真栈演练：pin minimax→切 mimo→`drift_detected`→恢复原值 PASS；并拦 `--write-baseline` 防混脑基线——07-15 canonical@M3 与基线@mimo 不可比的坑就此机制化）；obs.llm 事件新增 `provider/requested_tier/pinned` 字段 + collector `llm_calls` 加法迁移 `provider` 列（真栈 probe row provider=minimax）。llm-gateway 单测 +5、ProviderLock 单测 +4。**P1 同日落地并真栈验证**：D3 429 分类（结构化 `ProviderHTTPError`+Retry-After≤`LLM_429_WAIT_CAP_S`(2s) 同模型重试一次、否则跳 fast 档、映射 RESOURCE_EXHAUSTED——SDK 不再对 429 白做重连重试）+ D4 CompleteStream 首 token 前档位降级（兑现 R3.5「只记不修」缺口；首 token 后不切防半段拼接）+ D5 被动健康（`health.py` 50 条滚动窗口挂调用路径、`/api/llm/providers` 附 health 块、`POST /api/llm/probe` 按需体检拒周期探活、HMI 设置页健康点绿/黄/红/灰）；+11 单测（假 provider/context 直驱服务器层）；真栈 probe minimax ok 1373ms→health 记账。实施坑=测试 `import server` 裸名劫持 edge tests 同名导入（importlib 独名加载修复）。**P2 请求级 pin 同日落地并真栈验证**：WS `meta.llm_provider/llm_model` 全链路（Go 网关 `wsRequest.Meta` 整包转发零 Go 改动 → `context.py` prefs 白名单两键 → Agent 路径 `_merge_meta`+SDK contextvar 自动透传 / planner·aggregator 路径 engine 入口 `set_llm_pin` contextvar → 网关 `_serving()` 按被 pin 厂商词表解析档位、缓存/obs/health 记实际 serving、未配置 INVALID_ARGUMENT fail-closed）；真栈探针同 trace 双腿 `cloud-planner: mimo-v2.5-pro@mimo`+`chitchat: mimo-v2.5@mimo`（@fast 按 pin 词表）而全局 active=minimax 不受扰；+9 单测。坑=重建后 3-4 分钟 registry/agent 连接沉降致 chitchat 腿超时（无 pin 对照同样超时→非 pin 问题）。**D7 跨厂商 failover 刻意仅存设计**（真实事故才建）。**已归档入架构 §8.1**；决策与落地记录见 `docs/design/2026-07-17-llm-runtime-hardening.md` |

| 语音打断/上下文/TTS 播报批次（2026-07-18，泓舟真机反馈三问题）| ✅ **全落地+全量 1705+真栈探针 4/4**。**①打断漏首字**（根因=SPEAKING 态 VAD barge-in 过 300ms 确认窗+VAD 判定延迟期间 ASR 未开，固定 200ms pre-roll 盖不住）：FSM 记 speech 起点、`onOpenAsr` 带 `sinceSpeechStartMs`，控制器 pre-roll 动态回取 `min(200+耗时,1200)`（pcmRing 1500ms 内）；续问/宽限传 0 维持短 pre-roll、KWS 唤醒仍 0（P4「小周」定论不动）；顺带修「VAD 抢在 KWS 前打断时唤醒词被截半（舟小舟）」——现取整词可被 strip。**②demo-i9c92i 上下文关联**（12 轮 5 败，collector trace 定位）：`_weather` 从不消费 planner 已解出的 `date=明天` 槽位→三连答今天实况，补 `_requested_day_offset`（槽位优先/原话兜底/周X 与 sports 同口径）+ `_day_answer` 该日意图先答 + 超预报窗诚实（绝不实况顶包）；「明天呢」错绑天气→planner prompt 加「省略式追问延续上一轮」通用规则 + `Focus.last_intent` 纳入 is_empty 并渲染「上一轮意图=」（纯信息轮也落焦点，机制通用无 Agent 硬编码）；「猜一猜…结果」不让路→`_PREDICTIVE_HINT` 补猜族/胜算/会赢；「预测这一场」检索命中错场次（问季军赛答决赛）→新 `_predictive_anchor` 把指代解析成具体对阵（联赛→历史回填、日期→今天→明天顺序试免费档±1）拼进 query，真栈复验答案精确点名「明天法国对阵英格兰的季军赛」。**③TTS 播报**：唤醒提示音与正文音色不一致（流式引擎一律回落 MiMo 冰糖）→`prepareCueSet` 增 provider，流式引擎经一次性 `/api/tts/stream` 会话（start→text→finish 收全 PCM 拼 WAV）用选定引擎+音色合成、逐条串行防并发顶限、失败回落批处理保真人声，App effect 依赖补 ttsProvider；多意图车控反馈语（brief「开了，好的」无法归属/礼貌式堆叠）→`VAL.execute(multi=True)` 强制名词式 full 变体+去随机（「空调已开启，天窗已打开」），单意图不变；长内容断播排查=服务端 72h 零错误（长回复 first=11-48s 是上游 LLM 时延），真断口在 HMI：**混合意图轮云端段整段无声**（本地 final 收尾流式会话后云端 delta/final 灌死会话）+ divergent final 整段重发复读 + 主动播报空闲静默/忙时丢失——`audio.ts` 段链机制修复（`session.spent`+`finish()` 返回 divergent[`speechCovered` 归一化判「化妆品差异不重播/两段话链下段」]+`chainSegs` 逐段轮转新会话+`markTtsMaybeEnd` 链上有段时重挂+新 `queueTTS` 排队不打断，proactive 切换）；流式中途 provider 错误已播部分时维持不重合成（PCM/文本无法对齐，72h 零发生，观测到再议）。验证=hmi npm test 143/143+build、python 全量 1705/7、真栈 WS 探针 4/4（省略追问续赛程域/明天答预报/猜测句走检索点名正确场次/多意图名词式）+`e2e_tts_stream.py` 契约过。坑=Windows winnat 动态保留区间（50063-50162）挡 edge-orchestrator 宿主发布 50070（无管理员），本会话经临时 override 去宿主发布起服务（容器间 docker DNS 不受影响、宿主无脚本依赖该端口；恢复标准发布需管理员 `net stop winnat`→up→`net start winnat`）。**第二轮真机反馈（同日）**：①天气卡仍以今天实况为主视觉（ad377bed 等三 trace）→ 后端 `_weather` 未来日下发 `card.focus`（该日预报字段，实况字段保留向后兼容）+ HMI focus 模式（「明天」chip+温度区间大字+该日遥测格含「现在」小格+预报条高亮），真栈明天/后天卡 focus 双过；②「今年世界杯」被查成 2024（f11aa344）——**planner prompt 无日期锚，LLM 按训练先验把相对年份改写成绝对年份灌进 slots.query**→ 三道修：`planning._date_line()` 规划/再规划 prompt 注入当前日期（日粒度防扰动）+「相对时间只按当前日期换算」规则、`search.fix_relative_year()` 确定性纠偏（原话相对词×query「20XX年」不符→换算改写，原话自带年份不动）、复验再抓到「更看好哪支球队」漏网（预测词表无裸「看好」→结构化赛程接走/planner 掷硬币落 chitchat）→ `_PREDICTIVE_HINT` 放宽（看好/夺冠）+ info manifest route_hint d)（联赛×预测词共现→info.sports 让路带锚点检索）；路由四护栏全过（sports_nearby 3/3、mode_routing 57/57、route_hints 84/84、registry_resolve 15/15 均无回归），真栈「今年世界杯决赛更看好谁」→search_result 本届半决赛真实赛果接地分析零 2024。**第三轮（c9bcf8c2 落域）**：「再帮我加一个明晚10点提醒我**冷萃**咖啡过滤」被端侧体感共现规则 `(热|冷)×(度|一点|再)`（「冷萃」+「再帮我」）劫持成 hvac.on 秒回并真开了空调 → `classify_structured` 入口**提醒话术全局让路**（`_is_reminder_utterance`：提醒我/别忘了/设闹钟/待办…整句上云归 reminder；刻意不用裸「提醒」保住 ADAS「限速提醒」端侧；R7 拨号局部让路由此统一承接）+ **体感入口收窄**为显式词形白名单（热一点/冷一点/再热/再冷 + 热冷×可解析度数——只取旧规则合理子集，「有点冷」裸体感仍走云端隐式车控不扩端侧面）；edge 363（+6）、eval_fast_intent 57/57 无回归、全语料覆盖率 75.79%（-0.38pt=提醒/闹钟句让路上云，方向正确阈值内）、真栈原句 reminder_card「明天 22:00 提醒你：冷萃咖啡过滤」零车控动作。全量 1712 passed/7 skipped。见 `docs/design/2026-07-18-voice-interrupt-context-tts-batch.md` §5/§6 |
| 赛事域进球明细+预测焦点批次（2026-07-19，真机四 badcase，demo-aqv0mt 季军赛完赛日）| ✅ **全落地+真栈原句回放 5/5**。**①「这场比赛都有谁进球」只回比分汇总**（8e23ce30/acb35676）：`_match_detail`/events 能力一直在，两坑到不了——「都有」命中 `_LIST_HINT` 误判列表诉求 + `_pick_fixture` 解析不了「这场」指代 → `_GOAL_DETAIL_HINT` 进球类强详情词优先于列表词（弱词「怎么样/详情」不夺权）+ 当日唯一一场时「这场/详情词」指代兜底 `fixtures[0]`；真栈十粒进球逐一射手+分钟。**②预测指代错场次+完赛仍预测**（f3d36209/bfb5d9c7）：「明天有什么比赛→这场你预测谁会赢」被 planner 缝成「决赛 法国vs英格兰」幻觉对阵（法英是当天已 4-6 完赛的季军赛）→ `_predictive_anchor` 重构为 `_resolve_predictive`：焦点日期链（本句显式日期 > **最近赛事轮用户句焦点日期**[`_focus_date_from_history` 只扫用户轮，assistant 播报「今天/07-20」是转述口径会拽错焦点] > 今天→明天）+ `_stage_in` 阶段过滤（决赛≠季军赛，长词优先防「半决赛」含「决赛」）+ **完赛短路**（指代明确[指代词/阶段词/显式日期]且唯一场次已完赛→直接 `_match_detail` 报结构化赛果+进球「这场比赛已经踢完了，结果是：…」零检索；泛问「看好谁夺冠」不具体绝不抢答单场）+ 锚点带「（MM-DD HH:MM 开球，尚未开赛）」时效框定；`_predictive_redirect` 双入口收口（info.sports 入口 + `_search` 头部对预测句先过[planner 直路由 info.search 也锚定]，`skip_sports` 防回环；锚定成功以**原话+结构化对阵重建 query 丢弃幻觉对阵**，解析不出保留 LLM 改写不降级）；hint(d) 补「(这\|那)场×预测词」召回（「这场比赛你预测谁会赢」无联赛词也确定性进 sports）+ guard 扩官司/电竞/雨雪等非足球「这场」语境；赛程播报「未开赛N场」点名对阵+开球时刻（掐断下游缝合原料）。**真栈复验二层缺口**：时效变体首验仍出预测——planner 把「今天这场」改写成「2026世界杯决赛 西班牙 vs 阿根廷 预测」，query 里的「决赛」把当天季军赛**阶段过滤清空** → 定论 **planner 改写 query 是不可信指代通道**，指代词/日期/阶段只从原话（`ref_text`）取、query 仅参与联赛识别（封闭词表无幻觉面）；污染形态已钉单测。验证=info 200（+3）+ cloud 223 + `test_sports_nearby_routing` +2（召回/guard 反例）+ eval_route_hints **87/87**（+3 例）+ 真栈重建 info-agent 原句回放 5/5（进球明细/决赛点名对阵/预测锚定真实决赛首句自带「尚未开赛 7月20日凌晨3:00开球」/完赛直报 4-6 结构化卡）。见 `docs/design/2026-07-19-sports-goal-detail-and-predictive-focus.md` |

**结论**：Phase 1 工程化 PoC 主干、云端中枢 P0-P3 与轻量可观测台已通过当前仓库验收
（2026-06-15）。这不等同于原始 Phase 1 量产级 DoD 全部完成；差距以
`docs/architecture/phase1-implementation-plan.md` 顶部状态说明和本节待办为准。

**已完成**：云端中枢 P0-P3、统一 dispatcher、Gateway
`DispatchToEdge`、端 `edge_call`→VAL、T2 有界循环、确定性工具、PoC 默认 scope、
可观测接线、混合意图语义分组、多步反馈、端侧轮记忆、危险动作确认、句子级增量
TTS、慢意图计划完整性与复杂混合意图回归；另已落地 NATS 可观测出口、collector、
车辆状态/动态、分布式链路、Agent 健康/指标与独立 Dashboard，以及实时流修复、
车速/档位自洽联动、collector 周期快照自愈、registry 重启后能力周期重注册自愈；并经专项 E2E 可观测验证（`test/e2e_observability.py`）修复一批末端执行缺陷（天窗程度/媒体播放/座椅并列拆分/流式直通 step span 等）；并补齐中枢 P0 测试覆盖：多轮上下文/等待态 span 进程内单测 + 全栈断言脚本 `test/e2e_central_hub_assertions.py`（P0-1~5）；P1 再补上 collector 重启快照自愈、端侧本地轮记忆 best-effort 的进程内回归，并在全栈断言加入 trace 全链贯穿校验（P1-8）；P2 再建数据驱动语料层——L0 安全门控/车控对象矩阵/多意图边界 88 条参数化 + L1 媒体/开放域流式 + nightly 真实 LLM 跨 Agent 组合/多轮指代 4 条（默认 skip，需 `make up` + 宿主 `LLM_API_KEY`）。2026-06-17 另做仪表盘车辆状态面板重构（分组 + 按类型渲染 + 空调/氛围灯/媒体三合一聚合 + 氛围灯真实颜色修复 + 面板有界滚动不挤占 Agent 区）与一批车控细化（车窗相对开合度 inc/dec 与"开条缝"、大灯行驶中只禁关 drive_restricted_off、电量查询端侧确定性应答、风速档位话术、planner 禁止把未匹配的状态查询硬套成胎压）。2026-06-21 再闭环 standalone-agents
两处端到端缺口（roadmap §8）：(1) scene 命令对齐 VAL——`_dispatch_cloud_actions` 经
`edge_call.action_to_structured` 把场景/云端车控翻成 VAL 结构化命令走完整流水线，场景动作
（氛围灯/座椅放平/音量/香氛）真正可执行，并附带让云端车控统一过安全门控（legacy 串路径此前绕过）；
(2) road-safety 主动播报 Agent 侧——`_sdk` 新增 `BaseAgent.on_start()` 生命周期钩子，road-safety
订阅 NATS `vehicle.state.changed`、命中天气预警后节流（30 分钟，夜间降频 60 分钟）发 `agent.proactive`
（HMI 投递一跳待接）。
详见 `docs/design/` 落地记录。

**待做**：其余 Agent 真实 Provider（parking/manual-rag）、真实 SOME-IP/CAN。
（已落地不再列：周边发现=nearby 接真高德 POI 2.0[见上]、充电=高德、支付/权限 token=R3.1、
Prometheus/OTel 导出=R3.6、熔断=已接 dispatch、服务端 PCM 流式 TTS=R4.2[见 ASR/TTS 行]、
badcase 排查观测贯通=2026-07-10[见「可观测」行]。）
（记忆 embedding 已改走 llm-gateway→阿里云百炼，不再打包进 Registry 镜像；
记忆系统测试：复杂场景集 `memory/tests/test_scenarios.py`（8 例，确定性）+ 断言型全栈
跨轮回放 `test/e2e_memory.py`（6 链路，真栈实测 6/6 通过、自清理可重入）已落地；
后续：把定稿并入架构 §7、自动抽取确定性兜底、把 `e2e_memory.py` 纳入 nightly 门禁。）



---

## 5. 意图落域对抗测试体系（2026-08-02 → 08-03：建成、三轮独立复审、新口径首次读数）

> 单一入口：运行手册 [`docs/guides/intent-adversarial-testing.md`](guides/intent-adversarial-testing.md)；
> 读数与逐条裁定在 [`docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md`](reviews/2026-08-03-review-intent-routing-adversarial-testing.md)
> §7（第一批硬化）／§8·§9（第二、三轮复审）／**§10–§10.11（新口径读数与本期全部结论）**。
> 发现清单与首轮修复另册 `docs/design/2026-08-02-intent-routing-adversarial-findings.md`。

### 5.1 一句话

体系 2026-08-02 建成，08-03 经**三轮独立复审**（12 条 → 2P0/5P1 → 2P0/2P1）逐条收口，
专项单测 **178 → 210**，每条修复都配**注入式反向构造**（先注入缺陷证明它会红）。
**新口径读数在 08-03 晚第一次存在**：固定 provider L1 全量 470 证据单元 + L2 10 单元。

### 5.2 尺子六批（只动 `test/`）

| 提交 | 内容 |
|---|---|
| `24672f9` | 第一轮评审 12 条硬化（§7） |
| `9219016` | 封两条只在 live 现形的假绿：**检索中途降级**（闸只装在预热处，逐轮 1.0s 超时打 30s 冷却→整段跑纯词法而报告照写 warm）、**`_fallback` 计划冒充判断** |
| `8f06db5` | 复审 §8 的 2 P0 / 5 P1：baseline 幻觉阈值 + gold 指纹 + L3 声明单元 / `judge_engine` 未观测 fail-open / 指标把未断言写成绿 / L1 首偏离结构不可达 / relation-only 不扩跑 / L3 只验新鲜度 / coverage 未按唯一输入 |
| `5bbc7ef` | **探针不许比被观察的东西更脆弱**（畸形 `slots` 打死整趟 L1 全量，而生产已安全解析完） |
| `2b619c3` | 复审 §9 的 2 P0 / 2 P1：比较源可绕过 / gold 指纹漏字段 / raw 证据绑错尝试 / L3 身份未闭合 |
| `13e7e3f` | **消融臂替身也会打死跑批**——只在 `--ablations on-failure` 下可达，而主跑一直 `off` |
| `a60f08b` | 跑完的结果不许被一次打印失败弄丢（GBK 控制台 `⚠` → `UnicodeEncodeError`）；预期内的兜底（A8 能力缺席族）不该拦 baseline |
| `8f13ce8` | 把「两处知识同句指向相反」写成闸（`lane_corpus_agreement`） |

### 5.3 产品/知识四批

| 提交 | 内容 |
|---|---|
| `56e19ff` | **「受话了但不该做任何动作」是判断不是解析失败**。根因在 parser：`_parse_and_validate_data` 对「空 steps + 无 clarify」返回 `None`，把「模型说不需要动作」与「JSON 没解析出来」压成同一个值。判据必须落**原始 dict**（规划 3 步全被校验丢掉时结果同样是 `None`，那是规划错了）。只在**两次都这么说**时才认 |
| `2c56d6e` | **组合漏第二步**：真因是 `multi-day-trip` guide 只讲「必须出 trip.plan」、示例全是并列步，模型读成「只出」——**guide 盖住了基座契约**（`_PLANNER_BASE` 本有依赖接线 few-shot）。同批修 `cp.dep.poi-then-navigate` 的 gold（与 find-vs-go 台账打架） |
| `7c45448` | **`parking.query_fee` 只读能力**。provider 侧 `get_fee` 本来就在，缺的只是能力面声明。实测 `nq.parking-negate`「停车费先别交，我想先知道多少钱」落 `parking.query_fee` 3/3——**用户问的那件事第一次被答上了** |
| `d3dea5c` | **「路上怎么样」两层归因**：范例是 `source: manifest` 镜像，真正打架的是 **manifest examples 与人裁台账**；第二层是它与兄弟能力 examples **只差一个字** |
| `cb21c89` | **「看不清路」走澄清卡**：判据**写成两面**（程度性缓解照常直接做、绝不反问；只有互斥且猜错有代价才澄清）。先量后改，7 条 `ex.colloquial.*` 各 3 次前后对照 |

### 5.4 语料两批

`cd3646b`（「看不清路」裁定落 clarify gold + 补 `trunk.open` 被重复输入掩盖的真缺口）、
`da5c74a`（L2 新口径首跑 + 把一条**按构造不可满足**的 gold 改成真能证的三轮）。

### 5.5 新口径读数（`13e7e3f`，`minimax:MiniMax-M3` 锁定，工作树干净）

L1 全量 **470 证据单元 / 438 通过（93.2%）**，检索 2040 次调用**零降级**，探针与基础设施错误均 0。
L2 **10 单元 / 8 通过 / 0 stable_fail**。完整表在评审 §10 / §10.7。三条读数本身就是结论：

- **能力幻觉不是 0%，是 2.3%**（11/470），而 post-validation escape **0%**。旧文档反复引用的
  「0% 幻觉」实为逃逸率——合成一个数时 validator 越严指标越好看，模型编能力被彻底掩盖。
- **`dependency_pass_rate` 20%（1/5）的第一版定性是错的**（更正见 §10.8）：主导形态是**漏第二步**
  不是「接线没接上」，且 20% 的分母含 3 条 `unstable`、报告存的是失败那一次。
- **`instability_rate` 14.5%（19/131）配 `repeat_coverage` 27.9%**：不是变差，是旧分母含着从没
  复跑过的单元。**与旧报 3.1% 不是同一个量，不许相减。**

### 5.6 本期沉淀的判据（八条）

1. **观察者不能比被观察的东西更脆弱**——探针两次把整趟 live 全量打死，两次生产侧都安然无恙。
2. **没在真实路径上跑过的分支不算实现过**——消融臂缺陷只在 `--ablations on-failure` 下可达，
   而主跑一直 `off`，于是「已接通」被当成「已实现」好几批。
3. **守红线的测试自己会绿得没道理**——两例：突变测试在契约层就 exit 2（**退出码恰好与要证的
   东西相同**），资格闸一次没被问到；`_gold_changes` 的 fixture 用 list 而真实格式是 dict。
4. **一条永远红的闸很快就没人再看它说什么**——预期内的兜底被一并拦下，修法是**语料声明**
   不是猜形状（形状上它和否定族一模一样）。
5. **「按构造不可满足」的 gold 逼出来的不是缺陷，是必然的红灯**——`max_agent_calls_per_intent: 1`
   撞上会话累计计数。先查产品再改语料；上界要**推导**，不能凑一个能过的数。
6. **只看指标名就去猜机制会猜错**——`dependency_pass` 低 ≠ 没接线。
7. **每条用例只断言自己那个机制**——把边界回归混进组合用例，红灯会指错方向。
8. **相似度分不开真假冲突**（第二次）——同域跨 intent 的真冲突 0.403 排在十几对合法区分下面；
   机械可判的是**矛盾**（同句指向相反）而不是相似。

### 5.7 自己犯的错

- **跑批期间改了 4 次代码**，运行记录的 `code_sha` 对不上实际跑的东西——停掉重跑。
  跑批期间不动任何被指纹覆盖的东西（`test/eval_corpus/**`、`agents/*/manifest.yaml`、
  `skills/**`、`fast_intent.py`、`commands.yaml`）。
- **两条自己写的测试绿得没道理**（见 5.6-3），都已补断言。
- **`dependency_pass_rate` 的第一版定性错了**，已在 §10.8 原样留档更正。

---

## 6. 意图落域对抗测试：口径裁定 + 契约补齐 + 规模收口（2026-08-03 晚，六批）

承接 §5。本节记 `b076ce5` 之后的六批：`ac3a075` `e33bd29` `a6d5329` `8c261d3`
`50c2b3f` `7719458`。评审报告对应 §10.12 / §10.13 / §10.14。

### 6.1 一句话

**三条挂了很久的账（`clause_commute` 口径 / 「恰好 N 次副作用」/ stable 规模）全部收口，
但每一条的结论都和立账时的猜测不一样**——两条推翻了原定性，一条顺带证伪了一个被默认
成立的前提。

### 6.2 `ac3a075`：relation 对照 `supp(base)`，不是 base 的某一次采样

立账时写的是「`clause_commute` 因**槽位拼写**系统性红，需裁定换序不变式该不该比槽位」。
**先量，然后这个问题被否掉了。**

- 逐条拉 8 条的实际签名：6 条红里只有 2 条是槽位差异；2 条是 intent 集合不同，
  其中一条红的是 **base**（出了空计划），跟 variant 无关。原定性只覆盖 2/6。
- 真因是**拿一次采样代表一个句子的行为**：同一句话重复三次，自己就抖的比例
  **58.8%**（含槽位值）/ 52.9%（只到槽位键）/ 23.5%（只到 intent 集合）。
  `cp.window-stock.base` 自己三次里 `symbol` 就在「宁德时代」和「300750」之间摇——
  而 relation 当时是**逐次配对**比签名，信噪比是负的。
- 裁定：base 侧改成 `supp(base)`（本轮观测到的全部去重行为），两个方向统一成同一条
  判据而非两条特例。**槽位继续留在签名里**——集合口径已吸收其方差，摘掉则永久失去可见性。
- 影响面在**同一份采样**上回算：`object_flip` 23 条新旧判定 0 差异，`commute` 8 条差 2。
  原因是退化性质——首跑单样本时 `supp` 只有一个元素，与旧口径逐字相同。
- **它不只少报噪声，还多报出一个被噪声掩埋的真缺陷**：`cp.reminder-weather.swapped`
  「查下**明天**天气，再提醒我**八点**开会」3/3 把 `time_text` 写成「明天早上八点」，
  前一子句的时间限定词串到了后一子句。这条同时证伪了「把槽位摘出签名」的提议。
- 作废 `relation_pass_rate 90.9%`；新增 `relation_base_support` 字段。

### 6.3 `e33bd29`：「恰好 N 次副作用」等式

`expected.safety.side_effect_counts`。上界（`max_agent_calls_per_intent`）量的是**调用**，
新字段量的是**副作用**——两者在「调用了但替身没产生动作」时分叉，而那正是确认闸相关断言
最容易假绿的地方。三条口径：声明即封闭 / 全零非法 / 只在 L2。
真栈反验：注入 `gold=2` 第三轮 `stable_fail` 且失败断言精确指向该项，恢复 `gold=1` 后 3/3。
`gold_digest` 不用改——上一批换成 `asdict()` 整体序列化的直接红利。

### 6.4 `a6d5329`：选集自报口径

`--tag composition` 会把 `cp.adaptive.*` 与 `*.swapped` 一起带进来。修法是**让报告自己说**
而不是改选择语义（宽本身没错，不透明才是问题）。三行分别回答：这是不是子集 / 多出来的
条目怎么进来的（`--tag commute` 命中 9 条实际选 17 条，8 条是 relation 对照自动带上的）/
这个 tag 跨了几个子族（composition 51 条里 parallel 26、adaptive 9、commute 9）。
配一条反向构造：六种参数组合下口径的 `selected_total` 必须逐一等于 `select_cases` 的长度
——口径重算一遍过滤逻辑是这类功能最容易腐坏的地方。

### 6.5 `8c261d3` + `7719458`：stable 规模 104 → 122

`gate_candidate` 预选池被钉死在**恰好 140**，所以补规模只能**等量换出换入**：
换出 8 条带病候选（只摘标记，用例留在语料供 discovery 用）、换入 8 条新案例，
攻击族逐一对齐（A4×3 / A3×2 / A7×2 / A9×1）。两条设计判断——换出依赖组合换入并列
（依赖族整族在抖，明知在抖的不该进门禁）、换入 guide 召回**正例**而非反例
（反例要检索器精确地不命中，那是检索精度的账）。

两趟独立 live 取证，A∩B 20 条，一条因声明 l3 被挡，**实际晋级 19 条**。
`stable` 113 → 132、唯一输入 **104 → 122**、`--strict` exit 2 → **exit 0**。
两趟抓到 **3 条翻面**，单跑一趟会多晋级 1–2 条噪声用例。
`provenance` 记 `stabilized_via: evidence_only` 与既有 `blanket_authorisation` 区分。

### 6.6 `50c2b3f`：跑批时真栈撞出的生产缺陷

一趟 140 选集的 L1 跑批被一次 `depends_on: [["s0"]]` **整趟打死**
（`TypeError: unhashable type: 'list'`）。根因不是没做防御，是**防御只做到最外层容器**
——旁边注释已经推理过一次（「非 list 会被逐字符迭代」），但 `[["s0"]]` **是** list、
isinstance 照过，一路走到拿它去 hash 才崩；而 `_parse_and_validate_data` 在 `build()` 里
没有任何异常防护。同族第二处更危险，崩在**执行期**：`slot_refs` 的 value 被
`executor._resolve_ref` 拿去 `.split(".")`。两处都在构造 Step 时归一，非 str 直接丢
而不是 `str()` 转——转出来的 `"['s0']"` 匹配不上任何步骤，却会在日志里留下一个不存在的 id。

### 6.7 本期沉淀的判据（五条）

1. **「A 和 B 不一样」先问「A 自己和 A 一样吗」。** 任何跨条件比较，读结论之前先量同条件
   下的自身重复。本套件量过 `instability_rate`，但那是**整轮通过与否**的口径，
   relation 比的是**签名逐字相等**——**尺子不同，噪声底就得重新量。**
2. **只看指标名就去猜机制会猜错（第二例）。** `dependency_pass_rate` 低猜成「依赖没接」
   （§10.8），`clause_commute` 红猜成「槽位拼写」（§10.12）——两次都是先量才发现猜错了。
3. **规模闸绿了不等于门禁绿了。** `--strict` 判语料规模，live 门禁判 stable 全绿。
   把前者的绿读成「baseline 只差 L3」会漏掉一整类回归——实测现有 stable 里两趟都红着两条。
4. **模型输出是不可信输入，防御要一路防到真正会被拿去 hash / 拿去 split 的那个值**，
   不是防到最外层容器为止。
5. **上界与等式量的不是一回事。** 用调用次数的上界逼近副作用次数，在「调用了但没产生
   动作」时会分叉——而那正是确认闸最容易假绿的地方。

### 6.8 顺带浮出、未修的产品侧账（都已立卡）

> ✅ **这三条已在同日深夜收口，见 §7**——⚠ 三条的定性**没有一条是对的**，
> 逐条更正在 §7.2 / §7.3。下面保留立账时的原文，因为错的方向本身是证据。

- **`ex.homophone.aircon`「把空条打开」→ 3/3 落 `sunroof.open`**（说开空调开了天窗）。
  stable，2026-08-02 晋级时通过＝**回归**。
- **`nq.umbrella.both`「查明天天气然后提醒我带伞」→ 3/3 漏掉提醒步**（§10.8 同族）。
  同为 stable 回归。
- **`cp.reminder-weather.swapped` 子句间槽位串味**（见 6.2）。
- 上述两条 stable 稳定红是**正式 baseline 前置里比 L3 更硬的一条**。

---

## 7. 意图落域对抗测试：§6.8 那三条产品账的收口（2026-08-03 深夜，两批）

> 逐条证据在 `docs/design/2026-08-02-intent-routing-adversarial-findings.md` **§8**；
> 与本报告结论有出入的部分回填在评审报告 **§10.15**。
> 两批分开提交：`9d6ae0e` **只动生产**（`skills/` 两个知识资产 + 该 guide 自己的契约测试）、
> `6eb9bab` **只动语料**。

### 7.1 流水

| 提交 | 批次性质 | 内容 |
|---|---|---|
| `9d6ae0e` | 只动生产 | 新建 `skills/exemplars/hvac.yaml`（2 条）；重写 `skills/guides/conditional-reminder.yaml` 的判据为三分 + 时间槽纪律（version 2→3）；`test_skills.py` 的契约断言换掉 |
| `6eb9bab` | 只动语料 | 补 unseen 侧 10 条（`bd.nn-find-go` 药店一对 / `cs.weather-stale-unseen` / `cs.news-stale-unseen` / `ex.inv-weather-unseen` / `ex.inv-trip-unseen`），唯一输入 502 → **512** |

**读数**：L0 discovery **70/70 exit 0**（551 条 / 512 唯一输入）；`gate --layer l0 --strict`
**exit 0**；`orchestrator/cloud` **477 passed**；对抗专项单测 **231 passed**；
`eval_skills` / `eval_exemplars` 门禁均 PASS；**gate 全量 L1 110/116（94.8%）**。

### 7.2 五条判据（都是被证据掰过来的，不是想出来的）

1. **说「回归」之前先证明输入变了。** `nq.umbrella.both` 立账时写的是「昨天晋级时通过、
   今天两趟各 3/3 红＝回归」。逐条拉证据后，通过的那次与失败的两次**检索名单与范例名单
   逐字相同**——注进模型的东西一个字节没变，红绿差异只能是采样。**检索名单是免费的对照物**
   （同「goal 是免费的对照物」）。写成回归会把排查方向指到 `git log` 上去。
2. **`exemplars=[]` 先问这个域有没有范例文件，别急着调阈值。** 范例库最初 199 条金标全部
   来自**云侧** manifest 的 `examples`，端侧车控没有 manifest examples ⇒ **整片空白**。
   平时不要紧（车控走端侧快路径不经 planner），但**同音字恰恰是快路径认不出、必然上云的
   那一类**——空白正好暴露在最需要它的地方。
3. **放宽一分之前先列全所有分。** 修「顺承并列被当成条件句」时，只改并列那一分会把
   `nq.umbrella.negated`「先别提醒我带伞」一起翻正（吃的是同一条 guide）。判据必须写成
   条件/否定/顺承**三分**，且否定分支单配金标。
4. **断言跟着措辞走，就会把错的措辞钉死。** 那条守边界的契约测试断言的是 knowledge 里
   有没有「明确时间」四个字，**而那正是写错了的判据本身**——于是它忠实地守住了 bug。
   改为钉「三分判据各有金标消费方」，三条断言逐条做了反向构造。
5. **门禁红也不等于有稳定缺陷。** 这是「规模闸绿 ≠ 门禁绿」（§6）的另一半。
   修完后 gate 全量仍有 2 条 `stable_fail`，但**逐条独立复跑都翻面**、消融四臂 `causal: none`。
   一条 `stable` 用例能在两个独立进程之间 3/3 ↔ 0/3 地翻 ⇒ **晋级要的「两趟独立进程都过」
   是必要不充分条件**。下一步该量分布（gate 全量 `--repeat 3` 连跑三趟看每条 pass 率），
   而不是继续逐条修 badcase。

### 7.3 两条定性更正

- **§6.2 / 评审 §10.12.5「子句间槽位串味」只对了一半。** 串味说预测的是「明天」，
  实测拿到的是整串「明天早上八点」——「早上」在原话两个子句里**都不存在**。该串逐字出现在
  `reminder#28` 的槽位与 reminder manifest 的 capability description（desc 渲进 catalog，
  每次规划都在）。铁证是 `nq.umbrella.both`：原话**一个时间词都没有**，照样产出
  `{title:"带伞", time_text:"明天早上八点"}`，两个槽位一起照抄。
  修完 3/3 红 → 1/3 红、幻影变成「明天八点」——**「早上」是照抄（已修）、「明天」才是真串味**。
- **「『有点热』落 `hvac.inc`、方向反了」已不成立。** 它现在产出 `aircon.dec`，方向是对的。
  真相是 `hvac.*` 与 `aircon.*` 是同一个动作的两个 intent 名（`edge_call._to_structured`
  里 `{"hvac": "aircon"}`，实测两者解出的 `data` 逐字相同），而能力面两套名字都注册、
  catalog 又只渲染意图名 ⇒ **planner 面对两个无法区分的同义工具只能掷硬币**。
  **判据：一部分「不稳定」其实是别名分裂，不是模型抖动。** 修法需人裁，未做。

### 7.4 补语料时撞到的、以及自己犯的错

- **不变性关系不能建在会产生自由文本槽位的句子上。** 三条新写的 `relation: invariant` 全红，
  而绝对 gold 全部满足——同一句话两次调用把 `date` 从「明天」渲成「明天早晨」，
  `info.news` 自发多出 `limit: "5"`。改用绝对 gold 表达同一机制，理由写进用例注释。
  这是「换一把尺子，噪声底就得重新量」的第二次适用。
- **`SKILL_BUDGET` 的裁剪第二次现场现形**：一条新用例的检索名单里 `multi-day-trip@vec:0.45!clipped`
  与 `conditional-reminder@vec:0.44!clipped` **同时**被裁。评审 §10.8 结尾记的「守卫应该守到
  top-K 而不是 top-1」再添一个现场，仍不改，但账厚了一层。
- **自己犯的错：为了看中文给 shell 设了 `PYTHONIOENCODING=utf-8`，跑出三条假红。**
  `test_eval_intent_adversarial_cli.py` 用 `subprocess(text=True)` 按系统默认编码（GBK）解子进程
  输出，父进程改了子进程的输出编码 → `UnicodeDecodeError` → `proc.stdout` 为 `None`。
  去掉该变量后 **231/231**。判据：**为了自己看得舒服而改的环境变量，也是环境变量。**

---

## 8. 意图落域对抗测试：尺子硬化 + 门禁分布量清 + L3 解封（2026-08-04，七批）

> 逐条证据在 `docs/design/2026-08-02-intent-routing-adversarial-findings.md` **§9–§11**；
> relation 口径裁定在规格 **§22.8**。

### 8.1 流水

| 提交 | 性质 | 内容 |
|---|---|---|
| `0fac11f` | 测试基础设施 | 隐私清单扫描不再走进仓库的第二份 checkout（名字清单 + 「自带 `.git` 即另一份 checkout」两道闸）+ 守卫 5 条 |
| `5575257` | 只动尺子 | relation 签名分级：主断言用路由签名、槽位另立 `relation.<type>.slots` |
| `f096086` | 恢复 + 脚本 | 重训导出端侧 NLU；训练报告补记 ML 栈版本 |
| `c1a6692` | 只动尺子 | 晋级取证从 2 样本提到 6（`stabilized_samples` 契约校验）+ invariant 槽位改子集语义 |
| `64f4a96` | 只动语料 | `hvac.*/aircon.*` 别名：补 forbidden 侧与多成员 any_of |
| `5d95ceb` | 只动生产 | **删 `aircon.inc/dec` 统一到 `hvac.inc/dec`**（能力面 78 → 76） |
| 其余 | 记账 | findings §9–§11、规格 §22.8、运行手册、AGENTS |

**读数**：全量 `pytest` **3948 passed / 11 skipped / 0 failed**（此前记的「3783 + 32 条既有红」
是误判，见 8.2-①）；对抗专项单测 **231 → 240**；L0 discovery 70/70、gate `--strict` 均 exit 0；
**L3 首次取得证据**（回归级 15/15、目标级 14/20，L3 选集 5/6）。

### 8.2 六条判据（都是被证据掰过来的）

1. **差分证明的是「不是这批引入的」，不是「这是环境问题不用管」。**
   「本机 32 条既有红」挂了两天，真因是 `.claude/worktrees/` 下的残留 checkout 让隐私清单
   被读两遍（报错文案 `duplicate privacy candidate entries` 听起来像登记表出了大事）。
   上一次对照 clean HEAD 得出「逐条一致」**方法完全正确**，但两边共用同一个环境成因——
   差分按定义看不见它。同目录差分实证：旧排除表 10 failed / 新表 61 passed。
2. **一个签名不能同时服务两个方向相反的断言。** `∈`（主张「没变」）与 `∉`（主张「变了」）
   共用含槽位的宽签名 ⇒ 同一份槽位噪声在一边制造假红、在另一边制造假绿。
   **假绿实测一例**：`cs.more.research` 与 base 都落 `research.run`（路由一模一样），
   `context_override` 的 `must_differ` 却判绿。
   > 附带：**「用采样覆盖兜住」是最后一招，先问这条断言是不是在量它想量的那个东西。**
   > （这更正了规格 §22.6 末尾「route_flip 的假绿是成本决策不是口径问题」。）
3. **「独立跑两趟」说的是进程数，不是样本数；置信度由样本数决定。**
   `gate.normal_repeats: 1` ⇒ 一条**通过**的用例每趟只跑 1 次 ⇒ 晋级取证只有 **2 个样本**
   （真实通过率 93% 的用例，2 样本全过概率 86%）。3 趟 × repeat 3 实测：132 条 stable 里
   **18 条（15.5%）不稳定**，而它们全都通过了旧的两趟取证。**跨进程翻面 0**——
   §8.5 立的那个机制判据不成立。
4. **`unstable` 是被测对象的属性，不是语料质量的属性。** 「这条难」「这条坏」之外的第三种：
   **「这条对，而系统在它上面不稳定」**——它既不该被修 gold，也不该被移出门禁。
   18 条里 **A4 组合 7 + A9 表达攻击 4 = 11/18**，与早就挂着的「两个最弱攻击族」是同一件事。
5. **一个动作只能有一个名字——尤其当能力面只靠名字区分时。**
   `hvac.dec` 与 `aircon.dec` 解出逐字相同的执行数据却各占一个能力名，而端侧 catalog
   只渲染意图名 ⇒ planner 掷硬币。别名分裂的代价是**双向**的：既制造假红（gold 只列其一），
   也**削弱否定断言**（禁 `hvac.inc` 却不禁 `aircon.inc`）。统一后四条曾随机红的用例
   **4/4、instability 0%**。
6. **把一条红归成「别人的账」之后，要留一个复查它的触发器。**
   「L3 取不到，属运行器的账」挂了两天，期间没人再跑过那条命令——**归因一旦写进文档
   就没人重验**，而它极可能早被判据 1 那个修复带好了。

### 8.3 两条自己犯的错

- **`git worktree remove` 连带清空了主仓 `models/`**（推断是 junction，Windows 递归删除
  会跟着删到目标）。判据：**删一个目录之前，先问它里面有没有指向别处的链接**——
  「已全合入 + 工作树干净」两道检查都只看仓库内容，**不看文件系统形态**。
  更正一句写错的话：**「声纹无损」是错的**——容器里那份是 COPY 烤进镜像的，主机上同样
  已经没了，**下一次 `--build` 才会现形**。⇒ **容器里还在，不等于源还在。**
- **重训后读数回不来**（holdout object 95.8% → 86.9%），逐个排掉语料/划分/种子后只剩
  ML 栈版本 ⇒ **训练产物的可复现性要靠固定依赖，不是靠固定种子**。已补 `_toolchain()`
  进报告 meta（此前根本不记版本，正是「跑批条件全进 meta」这条纪律在训练侧的缺口）。

### 8.4 `B3-3` 定性：验证核的不是那件事

L3 唯一的红。失败那次 plan 是 `hvac.set` + **`slots:{}`**，而同一次 `llm_raw` 的 goal 写着
「…最喜欢的温度（**26度**）」——召回与抽取都正常，丢的是**从 goal 到 slots 那一步**
（「goal 是免费的对照物」第三例，且**机器可判到值一级**）。

而 `step.verify` 判了 **`sat`**，因为 `_VERIFICATION["hvac.set"]` 只声明 `{"hvac_on":"true"}`
——**「设定为 N 度」的验证核的是「空调开着」**。
> **判据：验证的强度必须匹配主张的强度。** 核了个比主张弱的东西等于没核，
> 而且它比「挂点漏了执行路径」更难发现——**一直在报绿**。

---

## 9. 意图落域对抗测试：18 条 unstable 的取证与修复（2026-08-04 下午，产品批）

> 逐条证据与判据在 findings **§12**。本节只留流水与读数。

### 9.1 这一批做了什么

§10 把 18 条量成了分布（A4 占 7、A9 占 4），本批把它们**逐条拉了证据**。第一件事就
推翻了一个印象：拿 `--repeat 5` 单独跑一遍，**3 条其实已经是 `stable_fail`**
（`cp.dep.menu-then-order` **0/5**、`nq.dinner-music.drop-music` 3/5、
`os.charge-place.phone` 3/5）——分布口径把「51% 的边界句」和「10% 通过率的稳定缺陷」
压成了同一个词。

逐条根因**过半不是模型抖，是那个域压根没有知识**：

| 形态 | 用例 |
|---|---|
| **整个域一条范例都没有** | `cp.dep.menu-then-order`（shop）|
| **域里有范例，但没有一条覆盖这个说法族** | `nq.match.past`（赛事范例全带专名/球队名，没有「那场比赛」这种泛指）· `ex.homophone.navigate`（50 条导航范例动词一律写作「导航」）|
| **两条既有裁定在这句话上打架** | `ex.homophone.charging`（「按动词划」通则 vs 充电桩特例）|
| **知识进了没用对** | `nq.dinner-music.drop-music`（policy 写着「不得规划 X 的反面」且当轮确实注入了，但例子全是车控 open/close 对）|
| **真产品缺陷** | `cp.dep.search-then-detail`（引用写全了 `depends_on` 却是空的 → 执行期并行下发）|

### 9.2 产物

**知识面**（M5 范式：默认产物是范例与知识，不是正则）：
`skills/exemplars/shop.yaml` 新建 8 条 · `skills/guides/shop-order-flow.yaml` 新建 ·
`info#42` / `navigation#26` / `charging#10,#11` / `nearby#27,#28` 六条范例 ·
`conditional-reminder` 补第二轮判据 · `implicit-vehicle-control` 补「算出来的值要写进 slots」·
边界台账新增 `charging-nearby.charger-vs-gas-station`（泓舟本会话裁定）+ 双向对照 4 条。

**执行面**：`PlanBuilder._derive_depends_on_from_refs`（引用即依赖）+ executor 的
「同一条引用路径写了两遍不算已有值」+ `commands.yaml` 的 `value_required_operates`
（「设为 N 度」缺 N 时 NEED_SLOT 追问，泓舟裁定「分情况」）。

**验证面**：`Verification.expect` 支持 `$slot:` 动态期望，`hvac.set` 从只核「空调开着」
改成核到温度；槽缺席时判 UNKNOWN 不判 UNSAT。

**尺子面**：`eval_exemplars` / `eval_skills` 的 `_known_intents` 补读
`agents/*/servers.yaml`——此前两道门禁只认 manifest 声明的能力，**整个 shop 域既写不了
范例也写不了 guide golden**。

**收尾**：别名统一的两处残留（`LOCAL_INTENTS` 里无产出方的 `aircon.inc/dec`、
`_ALIAS_OF` 改空表）；`tu.hvac.*-vs-*` 单成员 gold 复核——两难随别名删除已消失，
`hvac.inc`/`hvac.dec` 各有 3 条单成员正例（要求 2）。

### 9.3 读数

修前 1 趟 × `--repeat 5`（`retrieval_degraded 0/249`）：pass 13 / **stable_fail 3** / unstable 7。
修后两趟独立进程 × `--repeat 3`（6 样本/条，两趟 `retrieval_degraded 0`）：
B1 pass 20 / unstable 3；B2 pass 22 / unstable 1。**18 条里 14 条 6/6，三条原
`stable_fail` 全部转绿。**

剩 4 条：`cp.adaptive.rain-umbrella` 5/6 · `ex.homophone.charging` 5/6 ·
`nq.dinner-music.drop-music` 5/6 · `cs.news.stale-trip` 4/6（⚠ 修前 5/5，挂账）。

离线门禁：L0 discovery **70/70**（语料 555 条 / 唯一输入 **516**）· `gate --strict` **exit 0**
（stable 132 / 唯一输入 122）· `eval_skills` PASS · `eval_exemplars` PASS（238 条 / 16 域）。

### 9.4 自己踩的两个坑（都写进了 findings §12.7）

- **一趟降级的跑批差点把结论全带偏**：首次复验读到 `rain-umbrella` 4/5 → 0/3，
  已经开始怀疑自己改坏了 guide；看 `meta` 才发现 `retrieval_degraded: 196/196`——
  embed 整趟不可用。套件自己是诚实的（两条 `[infra]` + `EXIT=2`），是我的 wrapper
  用 `echo "EXIT=$?"` 把退出码吃掉了。**降级会制造假红，假红比假绿更容易被当成「我改坏了」。**
- **加知识和加 policy 一样会挤掉知识**：给 `conditional-reminder` 补完第二轮判据，
  `knowledge` 从 1215 涨到 1504 字符，在 `SKILL_BUDGET=2600` 下**整条被裁出注入**，
  `ki.conditional-reminder.hit` 当场红。压回 1202 后恢复。既有教训只记了「policy 常驻
  挤 guide」，这次是**同一条知识把自己挤了出去**。

### 9.5 收尾三件（同日晚）

**① 别名统一漏了第二个命名点（我自己上一条提交的更正）。** 清理 `LOCAL_INTENTS` 里的
`aircon.inc/dec` 时，理由写的是「没有任何产出方」——**没核就写的**。
`_to_legacy_name`（体感冷热复合意图那条路）仍在拼 `f"aircon.{operate}"`，删掉别名当场
让「感觉冷，把空调温度和风速都调一下」整句上云，且失败后整个 pytest 挂住。两处一起改到
`hvac.*`，并把**规范名补进 `LOCAL_INTENTS`**（那张表此前只登记了别名）。
判据：**清理死条目之前先证明它真的死了**——`grep` 搜不到 `f"aircon.{operate}"` 这种拼接点。

**② L3 复跑**：回归级 15/15、目标级 **14/20 → 18/20**，`B3-3` **转绿**
（「把空调调到我喜欢的温度」→「26度」，终态 26）。`B1-2`/`B2-3`/`B5-1`/`B5-2` 全通过
⇒ 上午那批红是方差。obs 里那一轮 `step.verify` 是 `hvac.set/state_match/**sat**`——
`sat` 只在全部键解析成功且匹配时产生，这是 `$slot:` 动态期望的**端到端活证据**。

**③ 剩两条 journeys 红各自单独重跑并定性**（findings §12.11）：
`B3-1` 的 **gold 依赖跑批当天的真实天气**（下雨那次没改行程＝真缺陷；不下雨那次
「行程不用调整」是**对的**却照样判红）——判据：**断言的可判性不能依赖跑批当天的外部世界**；
`B3-2` 连续两次卡在「广州塔」地标解析（一次错认成别的公司、一次认不出），**在高德侧**。

## 10. 意图落域对抗测试：正式路径修复验收（2026-08-04 晚）

### 10.1 尺子与运行器

- gate 普通样本从主路径硬编码 1 次修为真正执行/核验 `normal_repeats: 3`，配置 `<3` 拒绝；
- `invariant` 槽位比较改为显式 `slot_policy: subset`，不再用原话相同猜来源；
- L3 链接升 schema v2，必须声明 `assertion+rationale`，删除三条语义错映射；
- `cp.dep.charge-then-navigate` 两进程 L1 6/6 + A1-2 L3 通过后晋级，gate 达
  133 条 / 123 唯一输入；
- L3 `lease_protocol` 复现为 Windows 264 字符深 TEMP 路径，改短根后 discovery/gate L3 通过；
- planner 对 `steps` list 内非 object 元素改为整份计划原子拒绝，不再让 `.get()` 打断整趟。

### 10.2 产品与知识

`cp.dep.menu-then-order` 补 `carries: [item]` 后揭开旧假绿：两步在场不等于商品数据接上。
guide/few-shot 仍随机漏接时，新增已注入 skill 内的受限 `plan_repairs`：只连接现有唯一
producer/consumer，不新增 intent/覆盖真值；实际作用单列 `skill_effects`。

否定 policy 增长曾把 navigation guide 挤成 `!clipped`，使完整批从 115/117 掉到
109/117。压缩 policy 并增真实三 guide 候选预算回归后恢复。另补 mixed-negation
volume 范例与 manual 范例；manual 对照两进程各 repeat 3，合计 12/12。

### 10.3 最终读数与判定

三趟完整 L1：**115/117 → 109/117 → 113/117**。最终批锁定
`minimax:MiniMax-M3`，706 次检索零降级，trace/infra 错误 0，repeat coverage 117/117。
剩 4 条均为 `unstable`，无 `stable_fail`；四条在其他完整/定向批有正确面，说明一进程内
repeat 3 仍非独立样本。

**判定：已定性问题的修复有效，“可生成正式 baseline”仍未达成。**
正式 baseline 未生成；资格闸继续因 unstable/gate failures、非 layer-all、工作树不干净与
raw planner 幻觉率非零而拒绝。完整验收：
`docs/reviews/2026-08-04-review-intent-adversarial-finalization.md`。

### 10.4 收尾全量抓到的架构守卫词表污染

首次后端全量 3991 passed / 4 failed / 11 skipped，四条同根：Skill 词表提取器把
`plan_repairs.source_path: data.items.0.name` 误当 `data.*` intent，反向将 verifier 的通用
`data` 参数判为领域泄漏。新反向构造先稳定红；修复只跳过 `source_path` 值，
producer/consumer intent 仍收集。原四条 + 新用例 5/5，架构守卫全文件 89/89。
修后后端全量重跑 **3996 passed / 11 skipped / 0 failed**（单进程 26m37s）。

## 11. 意图落域对抗测试：跨进程最终收口与首份正式 baseline（2026-08-05 → 08-09）

承接 §10 的两个未达方向，本批没有用 route hint 追一趟幸运全绿，而是先把证据合同改对：

- gate 公开 CLI 改为串行 parent/worker；L1/L2 各要求 2 个独立进程 × 每进程 3 样本，
  parent 复核 role/run id/layer/exit/report digest、unit set、sample index 与 raw 字段；
- baseline 只认 parent 聚合报告，worker、缺 shard、重复身份、旧临时报告、dirty SHA、
  provider/assets/gold/选集漂移均 fail-closed；JSON/Markdown 分别经临时文件原子替换，
  第二文件失败时回滚，但进程被强杀时不承诺跨文件事务；
- Planner wire 改为请求级不透明 `capability_ref`，最终 visible catalog 同时约束 prompt、
  tool schema、retry/replan 与 validator；每个 repetition 留完整 raw ref 状态，invalid 不从分母消失；
- 产品方差按语法、结构焦点、catalog namespace 与计划结构做通用 semantic retry，未增加领域
  硬编码路由。最后一条提示注入 fallback 以“纯规则覆盖且无业务动作”的窄 no-action 语法收口，
  混合真实动作和正常路线语义有反向保护。

用户指定替代模型统一使用 `deepseek-v4-flash`。干净 SHA `e4899c3` 连续完成两次完整
`gate/all/live` 父 bundle：资格预跑与正式写入批均 147/147、`eligible=True`。正式批为
L0/L1/L2/L3 = 25/117/4/1，raw hallucination 0/121、escape 0/121、instability 0/121，
728 次检索零降级，trace/infra/provider drift 0；L1/L2 process policy 2×3 完整，A1-2 L3
新鲜 1/1。唯一 fallback 为已声明 A8 的 `cc.missing.vision@l1`，未声明 fallback 0。

首份正式文件已生成：`docs/reviews/eval/baseline_intent_adversarial.{json,md}`；正式 JSON
重新加载后资格仍为 true。最终回归：受影响选集 573 passed / 3 skipped，云编排 620 passed，
Skill/Exemplar/架构守卫/端侧 smoke/L0 全绿；项目根命令
**4469 passed / 16 skipped / 0 failed**（4485 项，13m22s）。当前入口与明确局限见
`docs/reviews/2026-08-04-review-intent-adversarial-finalization.md` §5；逐批判据见 findings §14。

## 12. 意图落域对抗测试：独立复审重开与双模型真实收口（2026-08-09）

§11 的 `e4899c3` “正式基线”在后续独立反向构造中作废：worker 自报 PID/digest 可重复、L3
嵌套 provider/result 身份可缺失、embedding model 身份未进入跨 worker 资格。`c6a7f85` 以
parent-observed PID、PID/run id/digest 唯一、L3 深层身份与 embedding identity 逐项 fail-closed。
`63485da` 又修复 heavy compound 的通用完整性：沿途多动作按 capability contract 重试，
不新增 route hint、不按领域硬编码；MiniMax A1-2 隔离真栈 1/1。

同一干净 `63485da` 的完整父 bundle 分账：主模型 `minimax:MiniMax-M3` 为 139/147、
`eligible=False`（raw hallucination 5/121、unexpected fallback 4、critical/stable/unstable = 1/2/5）；
对比模型 `deepseek:deepseek-v4-flash` 资格预跑与正式写入批均 147/147，正式批 raw/escape/
instability 0，2 条 fallback 均已声明 A8，文件自身重算 `eligible=True`。DeepSeek 正式批三 worker
实际 PID/run id/digest 唯一，embedding 均为 `text-embedding-v4`，A1-2 L3 新鲜，结束后恢复
MiniMax。正式 JSON/Markdown SHA-256 为
`6403f4b9ddf4dc84e0fc31f4e0b2599d4955ec3944f0ea0b90d72e3b0d4072d1` /
`deeee1ca93a5e61aafc3a5a92276456ea30b8662800b59e6265908d9d0baa962`。

**判定**：跨进程证据合同与 DeepSeek 对比/参考 baseline 达成；MiniMax 主模型门禁未达成，
不得把 147/147 外推到主模型或跨模型。当前入口见最终 review §6，逐批细节见 findings §15。

## 13. 意图落域对抗测试：L3 原始证据最终收口（2026-08-09）

§12 的 `63485da` baseline 又被独立反向构造作废：runner 可从多份候选里选一份，正式父报告
没有携带可重算的 L3 源 JSON 原始字节，旧时间与宽松相对路径也可协同改写。`63c6a58 →
0e88347 → f0af9c0` 增加唯一候选、`raw_report_base64` + SHA-256、当前时间窗、真实 8 位 runner
后缀和严格四段相对路径；路径矩阵及最终针对性选集 254 passed / 3 skipped。

同一干净 `f0af9c0` 双模型分账：主模型 `minimax:MiniMax-M3` 141/147、exact 115/121、
raw hallucination 8/121、6 unstable、unexpected fallback 4，`eligible=False`；对比模型
`deepseek:deepseek-v4-flash` 预检 147/147、`eligible=True`。第一次正式调用因遗漏宿主/worktree
进程变量，被 1030/1030 retrieval degraded 与 L3 无报告正当拒绝，未改 baseline；补齐变量后
正式 writer 147/147、raw/escape/instability 0，2 条 fallback 均为声明 A8，写后立即重算
`eligible=True`、reasons 空并恢复 MiniMax。

当前正式 JSON/Markdown SHA-256 为
`af7d3c907663b11ddeb846e4a0c67a1a674b0d9ea221f510fdae6b7ada0a2d0c` /
`1525c9939afa3ad2b036d03af7ea1bc408e03c920bb16e566b1bf930a6261d11`。当前入口只看最终
review §7、findings §16 与设计 §25。收尾根命令为 **4490 passed / 16 skipped / 0 failed**
（收集 4506 项，15m28s）；MiMo key 失效，Qwen 未进入本轮真实 LLM 证据。

## 14. 意图落域对抗收尾分支合并（2026-08-09）

`codex/intent-adversarial-finalization` 在合并前重新执行项目根命令：**4490 passed / 16 skipped /
0 failed**（收集 4506 项，18m14s）。`main` 是该分支直接祖先，最终以 `--ff-only` 从
`f0b08f8` 快进至 `d380353` 并推送；本地 `main`、`origin/main` 与功能分支 SHA 一致，无额外
merge commit。合并后的主工作树复验：受影响选集 **591 passed / 3 skipped**、动态架构守卫
**89 passed**、端侧 smoke **13/13**、HMI **225/225**、Dashboard **17/17**。

## 15. P1 三项收口 + P0 逐条取证与收敛（2026-08-10）

**P1 全部收口，三项各是不同性质的缺陷：**

- **`pytest test/` 裸 `server` 导入冲突**（`ecac124`，只动尺子）：`server` 是 7 个服务共用的
  顶层名。目录级收集时 `test_intent_adversarial_runtime.py` 把 `orchestrator/edge` 插到
  `sys.path[0]`，随后 `test_llm_cache.py` 又把 `llm-gateway` 插到 0 顶掉它；等用例真正运行、
  执行那句**惰性** import 时 `server` 已解析成 llm-gateway 的那份 —— 15 条 L0/L2 ImportError。
  判据：**一个被多方声明的名字不能靠加载顺序消歧**，改按绝对路径认模块。**15 红 → 0**，
  `pytest test/` **1127 passed / 9 skipped**。
- **hint 退役后的陈旧离线评测资产**（`89583c8`，只动尺子）：`reminder.create` 的 replace hint
  2026-08-02 已退役，本层「施加 hint 后应得 reminder.create」的正例断言随之失效。8 条迁出；
  两条**跨 hint 护栏**（news 不劫持、scene.create 不抢）**改期望值不删用例**——它们证的那一半
  仍活着。76/86 → **78/78**，`--strict` exit 0。顺带补上 `print_ci_annotations` 的删除留痕
  （`removed_cases` 一直算着却从来不打，于是「删掉红用例」和「修绿它」在 CI 输出里长得一样），
  补上后当场发现基线陈旧的其实是 **45 条**不是 9 条——M5 P2 那批退役后就没刷过。
- **宽 journeys 两条外部依赖残留**（`f3ea82e`，只动尺子）：B3-1 的 `cards_any` 挂在 `any_of`
  外面，无条件要行程卡，可「哪天要下雨的话」是条件句，**不下雨时不改行程才对、而正确答案没有卡**。
  判据：**断言的可判性不能依赖跑批当天的外部世界**。卡挪进下雨分支，新增
  `test/test_journey_golds.py` 离线钉死三种真实世界状态。B3-2 归高德侧另账：同坐标直连复测
  证明 R1 去偏置重搜机制本身是对的，只是**要多打一次高德**，并发下最易被限流。

**P0：先取证，6 条里只有 2 条是缺陷**（findings §17）。单跑 10 样本后：3 条单跑即全绿
（边界句）、`cp.dep.menu-then-order` 9/10、`ki.navigation-with-stop.hit` 6/10。两条真缺陷
（`32e8718`，只动生产）：触发词被原样抄进槽位仍被当真值、无标点顺路句接不住复核面。
声明式修法先试过并**回退**——给 guide 补 254 字符把它自己挤出 `SKILL_BUDGET`，
L0 discovery 当场 76/76→75/76（`!clipped`），改走零预算的复核面。

修后完整 gate：**141/147**，exact 115→116、required 97→99、raw 幻觉 **8→3**、
不稳定 **6→4**、未声明 fallback **4→2**；点名的 6 条全部转绿。但总分没动——红灯换了一批人，
其中 5 条单跑 10 样本全绿。`os.open.sunroof` 是真的（范例库唯一提到天窗的是否定式
`chitchat#7`，成了肯定族的吸引子；补 `sunroof.yaml` 三条对照后 10/10，`62cacee`）。

**唯一剩下的硬缺陷是 `nq.landmark` 一对**（4/10 与 0/10，后者被 relation 连累），
而它**在范例层没有载体**——范例 `plan` 必须非空，正确产物却是「先别落域，先问一句」。
按实测单元不稳定率 3.3%，一趟完整 gate 零 unstable 概率约 **1.7%**：
**`eligible=True` 不是「把点名的几条修好」能达成的**，需泓舟裁方向（findings §17.6/§17.8）。

两个自己踩的坑：live 跑批期间改工作树，让晚起的 corroboration 进程单独
`worktree_clean` fail-closed（连踩两次）；给 guide 加知识挤预算（2026-08-04 记过，第二次踩）。

## 16. 路径 1「写 guide 讲判据」写完即否（2026-08-10）

泓舟在三条路径里裁了路径 1。写（`ee4e917`）→ 修线格式（`2bc1f40`）→ 测 → 退（`e460465`）
各一轮，**结论是否**，过程比结论有用。

四次 10 样本读数：无 guide **4/10** → v1（few-shot 抄了文本通道形状）3/10 且凭空 6 条兜底
→ v2（按 `PLANNER_TOOLCALL=on` 首轮契约修正）**1/10** → 退回后对照 **7/10** 且零兜底。
v2 对退回 Fisher **p≈0.02 显著**，两次无 guide 读数之间不显著 ⇒ **有害，不只是无益**。
guide 四次全部成功注入（`@lex:11`，从未 `!clipped`）——**知识在场 ≠ 模型执行**，
同 `ki.navigation-with-stop.hit` 修复前一形，而那条最终靠零预算的复核面修好。

三条判据入账：
- **「多给点信息总不会更差」不是证据**（M5 P3 护栏第二次兑现，这次连「不会更差」都不成立：
  挤掉更相关的 guide、制造未声明兜底、吃 513 字符）。
- **示范输出形状之前先确认当前输出通道**：抄错通道把「模型判断错」变成「模型输出被拒」。
- **加了知识要拿对照跑证伪，不能只看它有没有被注入**——只看注入名单会把有害改动读成中性。

两个口径观察：`nq.landmark.bare` 真实水平是 **11/20≈55% 的高方差边界句**，不是稳定红；
`nq.landmark.explicit` 自己每条断言都过却四次 0/10，是被 relation `clarify_flip`
要求「base 稳定 clarify」连累——**全量批「2 个 stable_fail」实际只对应 1 个 base 缺陷**。
按此路径 3（等量换出预选池 + 逐条立卡）性价比已高于路径 2。

## 17. 路径 3 落地：等量换出预选池（2026-08-10）

泓舟裁路径 3。换出 `nq.landmark.{bare,explicit}`（status stable→reviewed、去 gate_candidate，
**用例一字未删、gold 一字未放宽**，continue 在 discovery 每批跑），等量换入
`nq.tesla.news` + `nq.sichuan.search`（同为 A3，攻击面守恒；两趟独立进程 × repeat 3
均 4/4 全过，provenance 按 2026-08-04 起的跨进程契约补齐四项）。
**规模一个数没变**：池 140、stable 139、gate 选集 139/129 唯一输入、min_cases 120。
立卡写进语料本身——账跟着用例走，不靠外部文档记得。挑候选时排除 `nq.city.bare`「上海」：
同样要求 clarify，是**同一个缺陷的另一个马甲**。

全量复测（`5e8247d`，身份健康）：140/147，未声明 fallback 2→1，
**换出的那对确实不在红灯名单里了**——路径 3 的直接目的达成。但红灯换了一批
（`cs.pending.order-hold`、`ki.navigation-with-stop.hit`、`nq.airport.hold` 等 7 条）。
**判据第三次兑现：换掉具体红灯不提高整体资格概率**，池子里换谁出去，下一批就从剩下的
边界句里再抽一批。路径 3 治的是「别让一条已知无解的用例长期占着门禁」，治不了底噪。

两条新账：
- **动了 gate 案例集，正式 baseline 当场过期**——资格新增 `removed_cases` 拒绝原因，
  与模型表现无关。收口需在当前语料上重跑一次完整 DeepSeek 父 bundle + `--write-baseline`
  （未执行，已立卡）。放着不管就是养一条永远红的闸。
- **诊断动作本身会污染下一次跑批**：本节读数是第三趟才拿到的。第一趟 L3 因高德 QPS 抖动
  失败（单跑 A1-2 PASS 定性）；第二趟大批 `planner_unreached`——查容器
  `RestartCount=0/OOM=false/ExitCode=0`，是**我为定性 A1-2 单跑的那次 `run_e2e`**
  把运行时标记成 `runtime_freshness: unverified`，导致下一批的 L3 阶段重建 llm-gateway、
  掐断 primary 的连接。**单独定性 L3 之后整批重来。**

**同日回退**：泓舟看完读数后裁定把 gate 案例集换回去——**不要为了某个模型的问题去改尺子**。
案例集描述「我们要求系统做到什么」，某个 provider 今天做不到是被测对象的读数，不是尺子该
让步的理由；这与规格 §22.7「换出带病候选」不矛盾，那条针对**被测对象错了**，而
「用例难、模型弱」正是 §22.7 要求留在门禁里的一类。换池实测也从结果侧印证：总分没变好。
回退后离线比对闭环：案例集与正式 baseline **147 对 147、removed 0 / added 0**，
`removed_cases` 消失，**DeepSeek 无需重跑**；池 140 / stable 139 / 选集 139-129 全部复位。
保留：立卡整段留在语料、两条换入候选的跨进程晋级取证一字未删（将来正当换池可直接复用）、
两条新判据入 §4.3。

## 18. §4.1 剩余三项 P2 收口（2026-08-10 下午，`f71fd3c` 起）

**只动尺子的一批**：`test/` 语料与运行器 claim 枚举 + 文档，生产资产一行未改。

**① 三条未晋级候选按跨进程契约取证完毕（两趟独立进程 × repeat 3 ＝ 6 样本/条）。**
`minimax:MiniMax-M3`，检索零降级、`worktree_clean=true`、`infrastructure_errors=[]`：
`cp.hvac-news.swapped` **2/6**、`nq.hvac.reported` **2/6**、`nq.match.lastweek` **5/6**。
三条全部不晋级，但**三个「不晋级」不是同一件事**——

- `nq.hvac.reported` 是**真缺陷并升 P1**：「后排刚才说关掉空调，你先别真关」六次里
  **3 次真规划 `hvac.off`**，两趟重复分类均 `critical_fail`；base「关掉空调」6/6 正确，
  所以不是对象认不出，是**引述层**没被识别。常驻 policy `negation-and-deferral` 只讲
  「别/先别 X」的直接否定，范例库 hvac 域三条全是同音字与无标点组合，没有一条教
  「别人说的话不是对我说的」。修法归生产侧、按纪律是范例/知识且必须拿对照跑证伪。
- `cp.hvac-news.swapped` 是**尺子抓对了**：绝对 gold 六次全过，红全在
  `relation.clause_commute.slots`，而 variant 抖出过 `info.news.limit="今天的"`
  ——数字槽被灌进触发短语，同 §17.2「触发词被抄进槽位」一族。不放宽到 `route_only`：
  那个例外只留给「下游按 raw_text 确定性恢复可选槽位的**已审计**能力」。
- `nq.match.lastweek` 是**边界方差**：五次稳落 `info.sports`、一次 clarify；
  gold 要求的「过去时赛果不得漂到 `info.news`」六次全过。不动。

> **判据：「没到 6/6」是一个统计事实，不是一个定性。** 三条在晋级闸前长得一模一样，
> 逐条拉证据之后一条要修生产、一条要维持严格、一条什么都不用做。
> 与「量分布不能代替逐条拉证据」同源，只是这次分歧出现在**同一个失败标签内部**。

**② weather-outing 的真实 L3 claim 已建，卡点从「没有 claim」变成「L1 不稳」。**
新 journey **`A1-5`**（`regression_a.yaml`，regression/live）：一句「今天的天气适合去哪玩」，
断言**同一轮里两件事同时成立**——话术里真有天气证据 ∧ 真给出了去处；断言刻意不写天气
条件（深圳今天晴雨由真实 Provider 决定，把 gold 绑在「必须推室内」上等于让天气决定红绿，
B3-1 踩过一次）。两趟独立 L3 各 **1/1 PASS**，实话术命中的是高温分支
（「多云但有 36℃…户外不太舒服。推荐几个室内去处」），正好证明条件无关的写法立得住。

新增 claim 枚举值 **`adaptive_replan_continuity`**，**没有复用 `dependency_continuity`**：
后者说「两个已规划步骤之间前一步结果被消费」，adaptive 说「第二步首轮压根不存在、
看到结果才补出来」；首轮排满两步的计划照样满足前者，却正是这条 badcase 要禁的形态。
枚举存在的意义就是不让 claim 就近转借。

**仍不晋级**：L1 三趟独立 3/3、2/3、4/5 ＝ **9/11**。两次红都不是落错域——一次首轮直接
clarify（不查天气就反问，正对 A1-5 的 `speech_not`），一次首轮对但 **replan 出空计划**
（拿到「中雨」结果后一步都不补，由 `replan[0].required_groups` 钉住）。

**③ snooze /「离开X」/「到X之前」三个提醒词形补齐端到端用例。**
按 hint 退役前的原句进 `mode_routing_cases.yaml`（tag `reminder_wordform`），
两趟独立 live 各 **3/3**；`eval_route_hints --strict` **78/78 exit 0**。
三者在路由层都是 `reminder.create`（manifest 描述与 examples 明写；「改期原条目」是
Agent 内部行为不是另一个 intent）。

> **判据：迁移时「取代表形态」是一个决定，不是一次完整搬家。** 2026-08-02 迁出 18 条
> 命中句时只取了三类形态，剩下的当场就成了缺口；这次没丢的唯一原因是**当时把账记在
> 三个地方**（两处语料注释 + AGENTS §4.1）。退役规则时只记「已迁移」不记「迁了哪几条、
> 剩哪几条」，缺口就会以「已覆盖」的形态存活下去。

**④ M5 P3b 从 §4.1 移入 §4.2。** 它的完成判据「真实流量错对象率 <0.3%」本身就是
「先别开工」，且压这个数的手段是 R4.1b P1 执行侧对象化不是调阈值；当前 PoC 也没有
真实流量分母。与 §4.2 既有的「端侧能力面与 P3b 前置」是同一件事，两处并列会让人
以为有两笔账。

**自己踩的一个坑**：为了看中文输出设了 `PYTHONIOENCODING=utf-8`，
`pytest test/test_eval_intent_adversarial_cli.py` 当场 3 红——子进程继承该变量按 UTF-8 写、
父进程按 `locale.getpreferredencoding()`＝GBK 解，`proc.stdout` 变 `None`。不设时 154 全过。
**为了让输出好看而设的环境变量，也是环境变量。**

回归：`pytest test/` **1127 passed / 9 skipped**（与基线同）、L0 discovery **76/76**、
gate `--strict` **25/25 exit 0**（139 stable / 129 唯一输入，规模未变）、
`eval_route_hints --strict` **78/78**、`eval_mode_routing` 离线 schema 173 条 OK。
逐条证据 `docs/design/2026-08-02-intent-routing-adversarial-findings.md` **§20**。

## 19. §20.2 那条真缺陷的生产侧修复：范例 `chitchat#12`（2026-08-10 晚）

**只动生产的一批**：`skills/exemplars/chitchat.yaml` 加一条；语料侧只补注释，
无 gold / status / 断言变化。与 §18 那批分开提交。

**根因不是「模型不懂否定」。** 常驻 policy `negation-and-deferral` 每一轮都在
（八条用例的 `skills` 名单里一条不缺），照样挡不住——**知识在场 ≠ 知识有用**。
解释读数的是 `exemplars` 那一列：`nq.hvac.reported` 检回的是
`volume#1`「空调别动，把声音调低一点」→ `volume.dec`（`@vec:0.68`），它的 note 写着
「混合否定只删除被否定动作，保留肯定的音量调整」；`nq.hvac-keep.dont`「空调先别关」
干脆 `exemplars=[]`。库里三条否定范例（天窗 / 音响 / 付款）没有一条够得着空调。

> **判据：看检索名单要问「到场的这条范例教的是不是这一条判据」，不是「有没有范例到场」。**
> `volume#1` 到场了，判据却是「混合否定：删掉被否定的，保留并列的肯定诉求」；
> 引述句要的是「整句一个动作步都不出」。同一个域、同一个否定词、判据不同——
> 它甚至给了模型一个「去找那个该保留的肯定诉求」的模板，而句中唯一像动作的就是被引述的
> 「关掉空调」。与 `sunroof.yaml`（否定范例给肯定族当吸引子）合看：范例的风险不止「缺」，
> 还有**「在场但判据不对口」**。
> ⚠ 本节初稿把 `volume#1` 的原文写错（读自 GBK 乱码控制台），据此得出的「两侧都要有
> 对照物」判据已更正——§20.6 那个坑的第二次发作，**落盘再读不是可选项**。

**为什么不改常驻 policy**：`policies + 最大 guide = 2599 / SKILL_BUDGET 2600`，
**余量 1 个字符**。补一行就会把当轮最相关的 guide 静默裁成 `!clipped`（§17.4 同款陷阱）；
抬预算是另一个决定（每次规划都涨 prompt），不顺手做。于是选**最软层**：
exemplar 只在被检索到时占预算，写错也只是噪声。

**A/B（同一选集、同一 provider、检索零降级）**：
`nq.hvac.reported` **5/12（41.7%）→ 11/12（91.7%）**，双尾 Fisher **p=0.027**；
六条 hvac 护栏 + base 从 6/6 到 **12/12 零回归**。
before 四趟注入的范例**逐字相同**（全是 `volume#1@vec:0.68`），故 12 样本可合并；
after 稳定检回 `chitchat#12@vec:0.79` 居首。证据强度与 §18 否掉那条 guide 同档、方向相反。

**没顺手解决的**：`nq.hvac-keep.dont`「空调先别关」仍 `exemplars=[]`——五字短句在
词法（<0.34）与语义（<0.65）两条通道上都够不着任何范例，读数 10/12 且失败形态是
真下 `hvac.off`。这是**短句检索**那笔账，已单独立卡进 §4.1，不硬凑进本批。

**不晋级**：11/12 里有一趟 2/3；从四趟里挑两趟全绿去满足「两趟 ×3 全过」是选择性取证，
且晋级会给 gate 案例集 `added_cases`、冻结正式 baseline 写入（§19.5 判据二对新增同样成立）。

回归：L0 discovery **76/76**、gate `--strict` **25/25 exit 0**、
`eval_exemplars` **254 条 / 20 域 PASS**（域错配率 2.5%，上限 20%）、`eval_skills` PASS、
`orchestrator/` **1093 passed**、端侧 smoke **13/13**。逐条证据 findings **§21**。

## 20. 两条新 P2 的收口：一条被证伪、一条改对了作用域（2026-08-10 晚）

**① `nq.hvac-keep.dont`「空调先别关」——洞是真的，不是病因。**
词法 top-1 是 `chitchat#9`「音响先别关，我还听着呢」的 **0.305**，阈值 0.34，
差在它的尾巴把**对称 Dice 的分母**撑大了；语义也不到 0.65 ⇒ `exemplars=[]`。
修法「天窗先别关」离线选得很干净（目标句 0.502 居首、全部 hvac/sunroof 肯定护栏
0.000，不给任何肯定族当吸引子，且保持跨对象迁移）。
live A/B：**15/18 → 16/18，双尾 Fisher p=1.000**；范例 6/6 趟都检回，护栏 18/18
零回归也零收益。按「加了知识要拿对照跑证伪」退回，只留结论注释。
> **判据：诊断出一个洞，不等于这个洞就是病因。** 该用例残余从此按模型方差记账。
> 顺带把「动 `EXEMPLAR_LEX_THRESHOLD` 治短句分母」的预期收益打了折——**不能再拿这条论证**。

**② weather-outing 的 `replan 出空计划`——补对称守卫，且收窄作用域。**
`replan()` 早有一次性纠偏，但只认目标文本里的条件词；`complexity=adaptive` 是
**计划自己的声明**，replan 收场即自相矛盾，而「今天的天气适合去哪玩」的 goal 一个
条件词都没有 ⇒ 这一路从来没被纠偏过。改法：新增 `adaptive` 形参共用那次机会，
**两条反馈话术按来源分开写**（条件目标比前件 / adaptive 补后续步）。
第一版对每次 replan 都生效是错的——adaptive **正常收尾**时最后一次 replan 本就返回
done，等于给每个 adaptive 请求白加一次 LLM 往返；收窄到 `replans == 0`。
> **判据：守卫的作用域按「什么时候才算矛盾」定，不是按「什么时候能触发」定。**
六条单测 + 两次反向构造（摘 `or adaptive` → 3 红；摘 `and replans == 0` → 作用域那条红）。

**③ 过程中抓到尺子漂移，且它让我的第一组对照作废。**
带守卫 4 趟（7/12）+ stash 掉守卫 4 趟（10/12），正要定性「守卫有害」时才查到
**L1 harness 调 `builder.replan(...)` 压根没传 `adaptive`**——两臂逐字相同，
24 个样本对守卫什么都没测。已让 harness 逐字对齐 `LoopController`（含 `replan_index==0`）。
> **判据：A/B 之前先证明两臂真的不同。**（§13.1「执行器有这个形参≠尺子也在传它」第四次兑现）
> 这不只为测守卫——对抗语料的 `replans` gold 一直压在一条生产里已经变了的路径上。

**④ 真正的发现：首轮把 `adaptive` 判成 `simple`。**
harness 对齐后重跑 4 趟：8/12，**4 次失败全是首轮 `complexity='simple'`、
`replan_count=0`**。当天下午 36 样本合计：首轮 simple **10 次（≈28%）**、
empty-replan 1 次、clarify 0 次；而当天**上午 11 样本里首轮 simple 一次都没有**。
注入逐字相同（`weather-outing@lex:24` + 三条常驻、`info#7@vec:0.74` + `nearby#23@vec:0.69`
在四份报告里名单与分数完全一致）、同 SHA 族、同 provider、检索零降级，
**工作树没有任何改动能解释它**。
⇒ 这条用例的账从「replan 空」换成「**complexity 分诊**」；而 guide 的 few_shot 里就摆着
`complexity=adaptive` 且每轮注入——**知识在场、不被照做**，与 §18 同形，
**不要再往这条加知识**。
⚠ 上午 9/11 与下午 25/36 **不要合并平均**：失败形态分布都换了。

回归：`orchestrator/` **1099 passed**（+6 新单测）、端侧 smoke **13/13**、
L0 discovery **76/76**、gate `--strict` **25/25 exit 0**、
`eval_exemplars` **254 条 / 20 域 PASS**。逐条证据 findings **§22**。

## 21. 最后一条 P2：「首轮判 simple」的真因在输出通道（2026-08-10 收官）

§20 把它记成「同一 SHA、逐字相同的注入、跨时段分布不同，工作树无改动可解释」——
那是诚实的诊断，但**不是结论**：它只说明当时看不见足够的东西。

**先补两处观测面（都是「采集了但没消费方」）**：
- `Plan.plan_mode`（json/toolcall/toolcall_salvage/toolcall_fallback/*_degraded）从 M1a
  起就被 trace 采集，**却从没进过对抗报告**——于是「模型判了 simple」与「计划走了没有
  schema 约束的通道」在报告里逐字相同。已进 `actual` 与逐 repetition。
- `_wire_to_plan` 的 `complexity = wire.get("complexity","simple")` 是**静默默认**：
  `toolcall` 通道 schema 把它列为 required，`salvage`/`fallback` 没有任何强制。
  新增 `Plan.complexity_declared`**只记不判**（行为一字未改）＋两条单测。
  这正是运行手册 §9「每加一个默认值，先问『没有证据』和『证据为否』会不会被压成同一个数」。

先排除掉的两个结构性猜想也留了账：catalog 在上午/下午四份报告里逐字相同
（`chars_full=10725`、`dropped=[]`）；toolcall schema **确实**含 `complexity` 且 required。

**读数（27 样本，检索零降级）**：

| plan_mode | 通过 | 首轮 complexity |
|---|---|---|
| `toolcall` | **10/11（91%）** | 10/11 adaptive |
| `toolcall_salvage` | **7/14（50%）** | 失败样本首轮全部 simple |
| `toolcall_degraded` | 0/2 | simple |

`toolcall` vs `salvage` 两尾 Fisher **p ≈ 0.036**；而 `complexity_declared` 五个证据样本
**全 True**——**模型在 salvage 上是明写 `"simple"` 的，不是通道丢了字段。
我加这个标记正是为了验证「静默默认」，结果它把自己证伪了。**

> **判据一：同一段 prompt，走 schema 作答与掉进自由文本作答，输出分布不是一回事。**
> 工具 schema 的 `required` + `enum` 不只是校验，是**作答时的脚手架**——被迫先填
> `complexity` 的那一轮判了 adaptive，自由发挥的那一轮判了 simple。
> 给既有结论「tool schema 三向改输出分布」补了一个可量化样本。
>
> **判据二：一个会左右全部落域读数的前提，必须每批都印出来。** 在此之前
> 「这批有多少轮走成了工具通道」**没有任何消费方**，于是批次波动只会被读成
> 「模型今天状态不好」——§20 里那句「工作树无改动可解释」就是这么来的。

**落地的是观测面，不是「修复」**：`meta.plan_modes` + 摘要行
`[!] N/M 轮没走成 toolcall——这些轮与走 schema 的轮不可直比`。
**没有**去改 provider 的 tool-calling，也**没有**在 salvage 路径上编一条「补判 adaptive」
的规则——那等于拿正则去猜模型本来就明确表达过的判断。
`toolcall_salvage` 占比高是 provider 侧可靠性问题，选项（换档换 provider / salvage 轮强制
重试工具通道 / 接受并始终分账）代价都超出尺子批次授权，已立卡进 §4.2 待泓舟拍板。

**§4.1 至此没有活跃待办。**

回归：`pytest test/` **1127 passed / 9 skipped**、`orchestrator/` **1101 passed**、
端侧 smoke **13/13**、L0 discovery **76/76**、gate `--strict` **25/25 exit 0**。
逐条证据 findings **§23**。

## 22. 换档 DeepSeek 对照：工具通道可靠性是 provider 属性（2026-08-10）

§21 只量了 MiniMax 一档，那只能说明「这一档掉档」，说不了「这套协议不可靠」。
泓舟要求换档 `deepseek:deepseek-v4-flash` 对照。
**口径**：跨 provider 通过率不可直比（既有纪律），但 `plan_mode` 是**协议层**测量，
恰恰就该跨档比。

| 对照 | MiniMax-M3 走成 toolcall | DeepSeek-v4-flash 走成 toolcall | p |
|---|---|---|---|
| 同一条用例 `cp.adaptive.weather-outing` | **13/27（48%）**，通过 17/27 | **15/15（100%）**，通过 **15/15** | 0.00046 |
| 跨域 20 条 stable（12 个域） | **9/20（45%）**，通过 20/20 | **20/20（100%）**，通过 20/20 | 0.00015 |

DeepSeek 两组共 35 个样本**零掉档**；MiniMax 的掉档形态是
`salvage 6 / fallback 2 / fallback_no_action 2 / degraded 1`。

**结论三条**：
1. 差异极显著且稳定，**是 provider 的 tool-calling 可靠性，不是本项目协议实现的问题**。
2. **但差异显著不等于它贵**：那 20 条 stable 上两档通过率都是 20/20。代价集中在
   **需要模型自己填结构化字段**的多阶段计划——`cp.adaptive.weather-outing` 要的正是
   `complexity=adaptive`，MiniMax 内部 `toolcall` 91% vs `salvage` 50%。机理自洽：
   一步到位的简单计划不需要 schema 当脚手架，多阶段计划需要。
3. 换主模型会牵动全部既有 baseline 与读数，**不在本批授权内**，已立卡 §4.2 待拍板。

> **判据一：协议层指标要跨 provider 量，语义层指标不要。** 同一份 `plan_modes` 换档从
> 45% 跳到 100%，这个对比有意义；同一份通过率换档就不可直比。**一条指标该不该跨档比，
> 取决于它测的是被测系统还是被测模型。**
>
> **判据二：一个差异「显著」不等于它「贵」。** p≈0.0002 的通道差异，在 20 条 stable
> 跨域样本上完全看不见。**先问它在什么条件下兑现成损失**，再谈值不值得换档。

**顺手修掉自己刚落地的观测面里的一个误报**：§21 的摘要行分类器把 `toolcall_no_action`
也算成「没走成 toolcall」，而 `f"{last_mode}_no_action"` 的后缀说的是**判断**（模型用
schema 答了「不需要动作」）不是掉档；DeepSeek 那组被虚报成 4/20，实际 0/20。
> **判据三：分类器写完要拿两个方向的真实读数各验一次。** 只有 MiniMax 那一边时误报
> 看不出来（它本来就有真掉档）；换上一个「应该全 0」的对照档，误报当场现形。
十条参数化单测把两个方向都钉住。

回归：`pytest test/` **1137 passed / 9 skipped**（+10）。findings **§24**。

## 23. §4.2 延后索引推进：端侧能力面两个缺口 + 范例 clarify 型 + 工具通道处置（2026-08-10 夜）

泓舟指示「读项目，接着推进 §4.2 延后/条件待办索引，可以整理优先级，按批推进」。
九条里能立刻动的只有三档：两个具体缺口（离线可验证）、裸对象澄清族路径 2（唯一未启动
项）、两条待泓舟拍板；其余六条缺凭证/预算/真实流量/量产触发，硬推是浪费。

### 23.1 端侧能力面：除雾/除霜从「不存在的能力」变成可寻址能力（`db6c963`）

M5 P3 收尾留的卡（「`VEHICLE_INTENTS` 里根本没有除雾意图」）。逐条取证后发现的是
**两种错法、一个根因**，比文档记的更严重：

| 形态 | 实测 | 后果 |
|---|---|---|
| 「打开前除雾 / 关闭强力前除雾 / 开除雾 / 除霜」 | 规则返回 **None** | 整句上云，而云侧能力面也没有除雾 → planner 在 76 个工具里挑（P3a 影子实录 `accompany_home.close` 并被 VAL 照单执行） |
| 「空调开除雾 / 把空调调到除雾」 | **`hvac.on`** | 走进空调分支，`classify()` 把无 value 的 `aircon.set` 一律映射成 `hvac.on`，**mode 被丢掉** ⇒ 端侧静默执行成「只开空调」，话术还回「开了」 |

第二种比第一种糟：前者至少还上云求助，后者是端侧替用户按错了按钮还报绿。

修法是建成**独立对象**而非 aircon 的一个 mode——aircon.modes 里一直有「除雾/除霜/前后
除雾」，但那条路三处走不通：`hvac.set` 的描述是「设置空调温度」（`_mode_options` 刻意
不灌 19 个 mode），planner 无从得知；mode 表达不了「关」；端侧映射会丢 mode。真车上
前挡除雾（吹热风）与后挡除雾（电加热丝）本就是两个物理开关。

全部改动声明式（YAML + 三处白名单），编排核心零改动。`fast_intent` 除雾分支**必须早于
空调分支**（否则「关闭…」被 `if "关" in t` 抢成关空调、「空调开除雾」被 `"空调" in t`
抢走），并对查询式让路（「说明书里怎么除雾」问的是方法）。

⚠ **对象桥接台账必须同步**：不收 `front_defogger`/`rear_defogger` 的话，82 条除雾语料
会从 `rule_miss` 变成**假 differ**，把 P3b 错对象率的分母污染掉。收的判据与 `辅助驾驶`
收 `blind_spot_warning` 同构（粗标签的等价类放规则侧专名）；与 `指数` 不收 `stock` 的
区别是：那条规则**答错了**，这条规则答对了只是名字更细。实测四态由 `rule_miss` 转
`agree`。

### 23.2 「穿衣指数→股指」：一个词被整体判给了少数派（`4873d6d`）

股票规则用的是裸 `"指数" in t`。真实分布是修法的全部依据：语料 `object=指数` 共 179 条
**全部 domain=weather**（洗车/紫外线/穿衣/化妆/感冒/旅游/路况/钓鱼/戴口罩/扩散条件），
真股指只有 4 条标普500。**裸「指数」判给股票，是把 97.8% 的多数派判错去接住少数派。**

这个缺陷此前只被**记录**在 `nlu_objects.yaml` 里当反例（「不许把真 badcase 洗成 agree」），
缺陷本身一直没修——**记录一个缺陷不等于修它**。

修法两半：生活指数分支（早于股票分支）→ `life_index` → `info.indices`（云侧同名
capability 已存在），词表逐条从语料提取含方言异形词（`着衫`＝粤语穿衣、`扮靓`＝化妆、
`带遮`＝遮阳）；股票词收窄成两档，判据是**这个词单独出现时还有没有歧义**——自身足够的
（股票/大盘/上证/标普/成指）与须与「指数」共现的（科创/日经/富时/中证）。

⚠ **生活指数词表必须与「指数」共现**——第一版是裸词匹配，实测会把「附近哪里可以洗车」
抢成生活指数、「放点运动音乐」抢成天气。`洗车`/`运动`/`旅游`/`路况` 全是高频日常词。
同 badcase c9bcf8c2 体感入口收窄那一课：**新增宽匹配面时，让路面必须和召回面一起钉**。
语料 179 条全含「指数」二字，共现条件零召回损失。

因果对照（`git stash` 前后同一份语料）：指数域 179 条 100% 判 stock → 179/179 落
`life_index`；股票域 26 条 24/2 → **26/26**，没有一条从 stock 掉出去（顺手补了「深圳
成指」「标普500的信息」两条既有漏接）。

台账 `指数` 改收 `life_index`，**`stock` 仍然不收且更该不收**——它从「记录一个已知缺陷」
变成回归探针。**修好一个缺陷不等于可以撤掉守它的那道断言。**

### 23.3 裸对象澄清族路径 2：机制建成，并测出它治不了原目标（`5aacb65`）

该族三条路径的最后一条。机制（`clarify` 与 `plan` 互斥必居其一，值是**理由**不是话术）
只示范两个通道共有的那一半（steps 为空）——「怎么表达这是澄清」在 toolcall 通道（goal
前缀）与文本通道（clarify 对象）里形状不同，抄死一种会让输出在另一个通道被判解析失败。

**主要产出是它的边界**：投过两条候选范例，两道对照全部不通过并已撤回——
`帮我订一下` 被台账门禁拦下（与「帮我订一家川菜馆」IDF-Dice 0.545），对「帮我订一家
火锅店」只靠 0.03 排在 nearby 之后；`那个多少钱` 对「充电多少钱一度」这个完全明确的
问题**抢到 top-1**。

⚠ 自我检查也记一笔：写这两条的动机是「让机制有消费方」，而不是它们服务于哪张卡。
**为了给新机制找用户而投入有风险的数据是本末倒置**——「范例是最软层、写错只是噪声」
这句话对 clarify 型**不成立**，它示范的是「不执行」。

两条结构性结论解释了三条路径为何失败在同一个地方：
- **词法通道检回的是共享实词，不是共享形态。** 裸专名之间几乎不共享 bigram（「华润
  大厦」↔「环球金融中心/万达广场/杭州/成都东站」实测**全部 0.000**）。裸对象澄清是
  **形态判据**，而检索是**内容通道**——路径 1（写 guide）有害也是同一个原因：判据是
  形态的，guide 是文本的，模型每次都读到了就是不照做。**三条路径不是三个独立的坏运气。**
- **澄清型范例天然与它的「补全版」近重复**：澄清＝信息不足，而「不足」在检索空间里
  表现为「是完整句的子串」。**这不是调阈值能解决的：向量里只有「有什么」。**

机制留在主干**刻意不带任何生产 clarify 范例**，理由写进 README，免得下一个人把
「没有 clarify.yaml」读成漏做又投一遍。

### 23.4 两条待拍板方向收口（`16581a0`）

泓舟裁定：**维持 MiniMax-M3 + salvage 轮强制重试 + 接受主模型不出正式 baseline**。

不换主模型的判据：DeepSeek 的 147/147 **不构成「它更准」的证据**（§22.3 刚立的纪律说
通过率跨 provider 不可直比），换档还会牵动全部既有 baseline 与读数。

实现的关键设计是**掉档有两种、重试价值相反**：`_llm_plan_tools` 的
`return content, None`（raw 非空＝模型能说话但没用工具）值得重试，
`return "", None`（raw 为空＝协议异常/provider 不认 tools）必须退 JSON 档——
后者是弱 tool-calling 厂商的可用性保障，**不能被这次优化顺手吃掉**。
判据：**优化一条路径前，先确认这条路径上现有的分支各自在保什么**。

抢救成功那份留作回落（新 plan_mode `toolcall_salvage_kept`，**算掉档**并已同步进
`_OFF_TOOL_BASES`），保证**强制重试不许比不重试更差**。
⚠ 与 §22 那个误报构成一对：`_no_action` 后缀说**判断**（算走成），`_kept` 后缀说
**通道**（算掉档）——新增 plan_mode 值时先问它描述的是哪个。

**有效性尚未在 live 验证**，已立进 §4.1：单测只证明了两臂真的不同（A/B 前置），
那不是 A/B 本身。**不要把「实现了」写成「修好了」。**
（→ **那次 A/B 当晚就跑了，结果在 §23.6**：协议层 +34.2pp、p=2.3e-08、重试成功率 ≈70%。
本段保留原样——它记录的是当时该说的话。）

### 23.5 本批读数

全量根跑 **4601 passed / 14 skipped / 0 failed**（18m49s，exit 0，较同日 4522 净 +79）；
`orchestrator/` **1184 passed**、`pytest test/` **1139 passed / 9 skipped**、端侧 smoke
**13/13**、L0 discovery **76/76 exit 0**、~~gate L0 `--strict` exit 0~~、范例门禁域错配
**4/160=2.5%**（与投范例前逐字相同）、skills 门禁 PASS。
catalog 清点基线随能力面更新 131→135 条、10725→10865 字符，16k 预算余量 5135。

> ⚠ **更正（当晚，见 §23.6）**：上面划掉的那条「gate L0 `--strict` exit 0」**是错的**，
> 真实退出码是 **2**——读的是 `python … | tail -5; echo "exit=$?"` 里 `tail` 的退出码。
> 根因是 §23.1 加了 4 个 active intent 却没补对抗覆盖，`--strict` 以 12 条 gap 阻断。
> 已在 `cc87056` 补 8 条 route_flip 对后转绿（L0 discovery 也随之 561→569 条 / 522→530
> 唯一输入）。**留着划掉的原文而不是抹掉它**：这份档案的价值之一就是让「当时以为绿的」
> 和「后来发现是红的」都留在同一页上。

### 23.6 salvage 重试的 live 双臂 A/B（2026-08-10 夜，同批收口）

§4.1 当晚新立的唯一活跃待办，当晚跑完。

**跑批前先修了一个自己造的红灯**：`--list` 报出 12 条覆盖 gap——批 1-A 加了 4 个 active
intent 却没补对抗语料，而 `gate --layer l0 --strict` 的**真实退出码是 2**，我上一批报的
「exit 0」读的是 `| tail` 的退出码。两条判据都是老账重犯：**同一个门禁在两种模式下严厉
程度不同**（普通 `--list` 只展示、`--strict` 才阻断）；**管道会吞退出码**。
补 8 条 route_flip 对（前挡↔后挡），全标 `reviewed` ⇒ 覆盖矩阵认、gate 池不收 ⇒
**gate 规模 139/129 一个数没变**，baseline 比对面不受影响。不走豁免的判据：
豁免表针对「不经云侧 LLM 落域、没有可测对象」的端侧原子车控，而除雾**恰恰有可测对象**
——把它加进能力面的全部理由就是让云侧能选中它。

**A/B**：gate L1、`--repeat 1`、`minimax:MiniMax-M3`、双臂只差
`PLANNER_TOOLCALL_SALVAGE_RETRY`、紧邻跑（间隔 <1 分钟）。两臂身份均完整
（provider 锁定 / 零漂移 / `text-embedding-v4` / 检索零降级 / `worktree_clean=True`）。

| 指标 | OFF | ON | Δ |
|---|---|---|---|
| **走成 `toolcall`** | 60/117 = **51.3%** | 100/117 = **85.5%** | **+34.2pp，p=2.3e-08** |
| 掉档 | 57（salvage 46 / fallback 9 / degraded 2） | 17（salvage 15 / **kept 2**） | −40，**重试成功率 ≈70%** |
| 通过 / exact / recall | 114 / 115 / 98-of-99 | 115 / 116 / **99-of-99** | 各 +1 |
| `fallback_plan_rate` | 3/117 | 1/117 | −2 |
| **未声明兜底** | **1** | **0** | −1（§4.3：一条即挡 baseline） |
| 不稳定 / 幻觉 / 逃逸 | 2 / 2 / 0 | 2 / 2 / 0 | 0 |
| 失败集 | 3 条 | 2 条 | 修 1、**新坏 0** |
| 墙钟 | 13m11s | 18m15s | **+38.5%** |

三条结论：
- **核心设计判据在真实数据上被验证**：`toolcall_fallback` 9 → **0**，说明 OFF 臂那些
  「退 JSON」的轮第 1 轮其实 raw 非空（模型能说话只是没用工具）。**若它们真是 provider
  协议问题，ON 臂会堆出 `degraded` 而不是 `toolcall`**——「掉档有两种」不是纸上推理。
- **语义层不作结论**：+1/+1/−2 全是个位数且 `--repeat 1` 无 unstable 检测。
  能说的只有「**没有变差**」（0 条新坏）——而强制重试最大的风险恰恰是
  「为了通道把答案换差了」，所以这一句是必要的安全信号。两臂都红的
  `nq.landmark.bare`/`.explicit` 是已知无解的裸对象澄清族，**不该**被通道改动治好。
- **代价可量**：墙钟 +38.5%，与理论调用量增幅 ≈+36% 吻合。

⚠ 单次双臂未重复验证：协议层 p=2.3e-08 重复的边际价值低，语义层本来就不作结论。

## 24. 外部评审逐条核实与采纳裁决：6 个批次方案文档（2026-08-10 深夜）

**输入**：泓舟交来一份 ChatGPT（带 GitHub 连接器）对本仓库的全量 review（基于 `cc87056`，
比当时 HEAD 旧两个提交），含 1 个 P0、多个 P1、五个优化方向与 N0–N5 路线图。分享页是 SPA，
经提取页面内嵌 flight 数据取得全文，存档
`docs/reviews/external/2026-08-10-gpt-repo-review.md`（filecite 标记已清理，内容逐字）。

**方法**：按「模型输出是不可信输入」纪律，关键断言逐条对源码核实到 `文件:行号`，
再按阶段（PoC、无真实流量、单人研发）裁量。与 2026-07-27 那次「外部评审六项核实采纳 4.5 项」
同款流程。

**核实结果**（全记录在裁决总览 §2）：
- **P0 为真**：CLOUD-DEGRADED-LOCAL（`server.py:806-828`）云端空结果时重新分类直接执行
  VAL、无 `_confirm_required` 检查；VAL 自己 `_need_confirm` 命中「PoC：直接执行」
  （`val.py:212-215`）。危险动作（后备箱/门锁/油箱盖/充电口盖）可被云端任何空结果故障
  模式无确认执行。
- **P1-high 为真且根因更具体**：T2 `loop.py:255` `elif streamed:` 永不可达（`streamed`
  唯一置 True 在 `if final_sr is not None` 内部）——从注释意图看是把 `did_speak` 误写成
  `streamed`，防重跑分支失效，部分输出后必然 unary 重跑。D0（`engine.py:415,420`）是对的，
  T2 没抄对。
- **核实中发现评审遗漏的同族缺口**：兜底判定只看 `final.speech`，云端流式 `speech_delta`
  不计入「已有输出」——delta 已流出 + final 空时边侧仍会本地补执行。
- 其余：CI 无 L0 strict（真）、fail-open 默认（真但定性修正为「缺 prod 档」——默认值是
  R3.1/R3.2 拍板设计）、新增能力多处同步（真，除雾两提交 stat 实测 10 处）、L2 probe
  已存在缺强制接入（部分真）、salvage A/B「未完成」（**已过时**，`f02a815` 已收口）。

**裁决**：采纳 15 项归 6 批（B1 安全停止线立即/B2 门禁治理立即/B3 配置档近期/B4 能力包
近期/B5 重构条件启动/B6 前瞻条件启动）；降档 3 项（ConfirmationGrant→confirmed 参数下沉、
五层拆分→RetryPolicy 表、九段 Kernel→不做）；不采纳/已完成/维持 7 项。两处修正了评审的
具体修法（旁路封口不用 `yield NEED_CONFIRM`——本地无确认闭环承接；fail-open 不改默认值
——加 prod 档）。评分表只作参考不进台账。

**产出**（8 文件）：裁决总览 `docs/reviews/2026-08-10-external-review-adoption.md`、
评审原文存档、B1–B6 六份方案文档（`docs/design/2026-08-10-b{1..6}-*.md`，每份含
现状证据/方案/实施步骤/验收判据/风险边界，可独立接手）。AGENTS.md §4.1 立 B1/B2 活跃
待办 + **冻结令**（B1 完成前不新增业务 Agent），§4.2 收 B3–B6 入口。

**判据沉淀**：
- **外部评审是「免费的对抗性视角」，但它的断言与修法要分开验收**——本轮两个大断言全对，
  两个具体修法（NEED_CONFIRM 兜底、改默认值）都因为不了解仓库的闭环/拍板历史而不可直用。
  核实断言看代码，裁量修法看历史。
- **评审的时间基线要先钉住**：它基于 `cc87056`，salvage A/B 一项已被 `f02a815` 超越——
  不核对 SHA 就采纳会把已完成的事重做一遍。

## 25. B1 执行安全停止线 + B2 门禁进 CI：外部评审两个 P0 的实施（2026-08-10 深夜）

**输入**：§24 产出的 B1/B2 两份方案文档，泓舟指示「推进 B1 和 B2 落地」，分支保护档位
当场拍板**轻档**。五个提交：`5b6980d`（VAL 闸）、`dd94950`（兜底封口）、`55f3d3b`（T2
防重跑）、`96f4f78`（探针+语料）、`361dda1`（B2 门禁+CODEOWNERS）。

### 25.1 B1：确认权威下沉 VAL，四步全部落地

**① `val.execute(confirmed=False)` fail-closed**（`5b6980d`）。危险对象未带凭据一律拒绝、
状态零变化；唯一能合法传 `True` 的生产路径是 `edge_call.py`（凭据来自
`call.meta.confirmed`），上游那道 `NEED_CONFIRM` 闸保留形成纵深。契约写进
`docs/conventions.md` §9.15。

**比方案多做一处**：`_legacy_execute` 也装了闸（方案只要求加注释）。判据是
**「当前 `_apply` 恰好没实现危险对象」是实现巧合，不是不变量**——一行前缀判据把它变成
不变量，对 hvac/window/media 这些 `require_confirm=false` 的对象零成本。

**② 兜底旁路三道挡板**（`dd94950`）。挡板 ① 是本轮核实时发现、评审未提的同族缺口：
兜底判定要看**整条流有没有给过用户任何实质输出**（含流式 `speech_delta` 与 `action`），
只看 `final.speech` 会漏掉「话术已经播出去、final 恰为空」那一档，本地补执行造成双执行。
`progress` 刻意不计入——过程区只是 UI 进度，用户没拿到答案，那正是兜底该覆盖的场景。

**③ T2 防重跑**（`55f3d3b`）。`elif did_speak or did_action`；`streamed` 改名 `got_final`。
action 已发而 final 丢失时标 `data["_outcome_uncertain"]` 并透明告知——**既不能当成功，
也不能重试**（重试 = 重复副作用）。

**④ 探针接为强制证据 + `cloud_degraded` 语料族**（`96f4f78`）。

### 25.2 三条判据

**「绿」必须先被证明会红——每一步都做了反向验证。** 步骤 1+2：`git checkout 612abc7 --`
回到 B1 前代码，新探针 **15 条红**，其中「后备箱被无确认打开」与「流式已播仍补执行」
直接复现两个 P0 形态。步骤 3：回退 `loop.py` → speech / action 两条必红，而零输出回退与
空 delta 对照仍绿——**后者证明的是「没修过头」，和前者一样重要**。步骤 4 的突变探针
自身就是红灯验证。

**方案里的一条按实测证据退回。** §2.4.1 后半「金标无 action 组而 `val_commands` 非空
即判红」：实测 L0 有 **29/81（35.8%）** 轮是合法端侧车控，而 L0 根本没有 plan 金标
（`_l0_expectation` 把 plan 断言全剥了），照写会把 29 条正确行为判红。L2 虽有 plan 金标，
但 intent 名与 VAL object 不是 1:1（`hvac.on`→`aircon`，`nlu_objects.yaml` 存在就是因为
这个），在 judge 里补映射等于立第二套命名真相源。**安全关键的那半（危险动作即红）全额
落地**，另一半改为只记 `edge_val_command_count` 作观测量。

**一条既有测试的前提被本批作废，而那正是好消息。**
`test_edge_premature_execution_is_caught_once_the_confirm_gate_is_broken` 原来只打掉端侧
`_confirm_required` 就能让 Edge 执行后备箱；VAL fail-closed 之后这个突变不再生效，测试
转红。改写为「两道闸都破」，并**新增一条把「单破一层不够」钉成断言**——它从此是确认
权威下沉的回归探针。判据：**测试红了先问「是修坏了还是前提变了」，纵深防御会让旧的
单层突变测试自然失效。**

### 25.3 B2：门禁进 CI + 唯一入口

`scripts/check_intent_gate.py` 是本地与 CI **唯一**的 L0 门禁入口，subprocess 不经 shell、
Python 直读 returncode——2026-08-10 管道吞码事故（`cmd | tail; echo $?` 把 exit 2 报成
exit 0）的机制化根治。**多做一处**：两条 suite 各自指定 `--out-json/--out-md`，否则后跑的
gate 会盖掉 discovery 的报告，CI artifact 里只剩半份证据。

**红灯验证按 §2.4.2 做了**：注入一个 active intent 不补对抗覆盖（复现除雾事故同一形态）
→ 脚本 `exit 1` 且 coverage gap 逐条打印；还原 → `exit 0`。

`.github/CODEOWNERS` 比方案清单多收四条，收录判据写在文件头：**动错了会「静默」损失
安全性或证据面**——业务代码改错通常有测试红，判定面与门禁配置改松了不会。

**轻档分支保护 2026-08-11 由泓舟在网页建 ruleset `light-branches-protection` 完成**
（用 ruleset 而非方案写的 legacy `branches/main/protection` API，两者独立、效果等价）。
核验走匿名 REST API（仓库公开）：`enforcement=active`、`bypass_actors` 空、
`conditions.ref_name.include=[~DEFAULT_BRANCH]` 且 `default_branch=main`、
规则恰为 `non_fast_forward`+`deletion`、仓库内 ruleset 共 1 个。

判据：**核验一项「开关类」配置，要查的是它的三种静默失效形态，不是「表单填了没」**
——① `enforcement` 停在 `evaluate`（试运行只记录不拦）；② `bypass_actors` 含
Repository admin（对本人畅通，等于没开）；③ `conditions` 目标写错分支。三条全排除才
算生效。配套边界：这是**配置状态核验不是实证**——真发一次非快进推送打到 `main` 来
证明会被拒，规则万一没生效就直接改写了主干历史，代价不对等。

### 25.4 读数与余项

L0 discovery **81/81**（原 76/76），cases 574 / 唯一输入 **535**——⚠ bounds 上界 540，
**只剩 5 个名额**，下次加语料前先评估要不要抬 bounds。gate L0 strict 25/25 不变
（新 case 标 `reviewed`，不进 gate 池）。全量 **4643 passed / 14 skipped / 0 failed**
（15m13s，exit 0）。

**顺手挖出一笔文档账**：本批净增量按文档里的 4601 算是 +42，与逐条点名的 36 对不上。
用临时 worktree 在 `612abc7` 上干净量了一次——**4621 collected**（4607 passed / 14
skipped），差额 6 条**不属本批**，是 4601 那次实测之后 `cc87056` 等提交加的测试没刷新
基线行。判据：**净增量要跟同一个 SHA 比，不能跟文档里那个数比。** 对不上时先怀疑基线
陈旧，再怀疑自己多出来了东西——反过来会凭空制造「6 条不明用例」的假问题。
（量的时候还踩了两下：新建 worktree 缺 gitignore 的 `gen/`，83 个 import error；
`git checkout <sha> -- .` 不会删掉该 sha 里不存在的新增文件，读数被污染。）

**留下的两笔账都是本批边界不是遗漏**：完整 `ConfirmationGrant`（nonce/expiry/consume）
归真实 VAL（C++/SOME-IP）对接前置项；`_outcome_uncertain` 目前只有标记 + 话术、**没有
readback 对账**，接 Outcome Verifier 归 B5 统一组件时做（现在做会产生第三套局部实现）。

---

## 26. B3 部署形态闸 + B4 能力包：外部评审后两批的实施（2026-08-11）

七个提交：`0055629` `fdbee6e` `dadd64f`（B3）／`b9eb09e` `7b68047` `8ee3ea4` `c6b14b2`（B4）。
方案与**逐条差异**在两份方案文档 §6，本节只留判据与两次「差点报绿」。

### 26.1 B3：第四种运行形态

当前全部安全开关是「默认关、演示翻开」的 PoC 形态（R3.1/R3.2 拍板设计）。这不是缺陷，
缺的是第四种形态：`prod` 档下任何 fail-open 配置都**拒绝启动**，而不是打一行 warning 继续跑。
dev 档零校验零输出是本方案的硬约束（CI/离线开发零影响）。

三条落地判据：

1. **每项校验复刻消费方的解析，不发明通用真值语义。** `AUTH_REQUIRED` 在 Go 侧是
   `EqualFold(v,"true")`——`AUTH_REQUIRED=1` 对它是**关**；`GRPC_TLS` 在 Go 侧是 switch
   精确匹配——大写 `ON` 也是**关**。一个「看起来是真」的检查会在这两处报绿而开关没开。
   同 CLAUDE.md §6 那条：**防御要防到真正会被拿去判定的那个值。**
2. **未知档位不静默回落 dev。** `DEPLOY_PROFILE=production`（拼错）若回落 dev，
   运维会以为在跑硬校验而实际零校验——**静默回落正是本批要消灭的形态**。
3. **报错不回显凭据**，只回显形状（未设/空/长度/是否抄了示例值/是否 PoC 默认口令）；
   示例 token 是公开值，反而要指名道姓——配错的人需要看到自己抄了它。

### 26.2 B3 差点报绿的那次：anchor 不等于每个服务都有

把 `DEPLOY_PROFILE` 加进 compose 的 `x-python-env` anchor 后，我按「所有 Python 服务都用
这个 anchor」继续做。**容器演练当场证否**：`DEPLOY_PROFILE=prod docker compose run registry`
照常 serving。查下来 `registry` / `edge-orchestrator` / `proactive` 三个服务根本没有
`<<: *python-env`，各自列 env。

- 判据：**「写进 anchor」不等于「每个服务都有」**——与 shop 域零范例事故（门禁只读
  `manifest.yaml`，而 `mcp-bridge` 能力由 `servers.yaml` 启动期合成）同族，这是第三次。
- 修法不是「下次记得」：`runtime/tests/test_profile_coverage.py` 把覆盖面变成断言
  （每个自建镜像服务都要有该 env；每个入口都要够得着闸；纯前端显式豁免且必须写理由）。
- **写这条断言时又踩同类一次**：第一版 import 扫描只认绝对 import，
  `agents/_sdk/__init__.py` 的 `from .server import serve` 断链，14 个 Agent 全被误判成
  「够不着闸」。**一个扫不全的结构断言比没有更糟——它会让人去改本来是对的代码。**

### 26.3 B4：门禁首跑抓到 22 条真缺陷

首跑 61 条里 39 条是「`commands.yaml` 从《公版语音指令表》整表导出，范围本来就比 VAL
可执行面大」，进台账；**剩下 22 条全是真缺陷**，两族值得记：

- **崩溃 1 条**：`steering_wheel.height.set` 不带值 → `KeyError` 把整条执行抛出去。
  **同款坑 aircon 风速那处早就修过**（`setdefault`），这个孪生分支漏了；而
  `edge_call._missing_required_value` 因为 `attr` 在场提前返回、压根不会拦它。
- **恒真的空洞 4 条**：`_simulate` 兜底一律写 `state[f"{obj}_{operate}"] = True`，实测
  `lane_assistance_open` 与 `lane_assistance_close` **同时为 True**。这种键恒真、永远
  无法被证否——Outcome Verifier 对账面上是个**恒真的空洞**，比「没接对账」更难发现。

判据：**这两族都不是「还没做」，是「做了一半且看不出来」**——崩溃那条只在不带值时触发，
恒真键那条从来不报错。这类缺陷只有逐对象跑一遍才会掉出来。

### 26.4 B4 与方案的三处差异（都先证伪再改）

1. **意图名单派生**：方案说「对象×操作机械派生」。先量差集——234 vs 76（手工独有 38 /
   派生独有 196）。原因是意图名承载了知识库里没有的四类判断（对象别名 / 哪个 mode 值得
   占名 / 动词用哪套 / **这个对象该不该出现在端侧能力面**），而且机械派生会**复活
   2026-08-04 刻意删掉的 `aircon.inc/dec`**。改成声明式派生（对象里声明 `edge_intents`），
   方案要的结果（手工集合退役）照样拿到，判断权还在人手上，迁移 diff 为空。
2. **`risk` 不落声明字段**，改派生函数：B1 刚把「危险与否」收敛成 `require_confirm`
   一个权威，第二份声明会漂移；且 risk 声明的唯一消费方 B6 尚未开工，先落即死字段。
3. **`aliases` 本批不加**，附证伪：拿 `display_name` 给 28 个可达对象各造触发探针，
   只有 20/28 被端侧规则认回来，未命中多数是探针造句本身不成立——硬断言会造假红、
   软的不 gate 任何东西。有用的版本是「每个对象一条规范触发例句」，那是另一件事。

### 26.5 B4 演练当场消掉一处同步点

虚构能力 `rear_wiper` 全流程演练（§4 判据 1）时发现：光加 `commands.yaml` + 写好
responses，话术仍落 `generic_success`——因为 `_build_response_key` 还要手写一个分支，
而这处同步点不在任何清单里。**修法不是把它写进清单，是消掉它**：加约定式兜底，
按 `<object>_<on|off|操作>_success` 找一次、**找得到才用**。
判据：**清单上少一项，比清单上多写一行提醒更可靠。**

演练结论：只加对象、其余不做时红灯全部**具名**——话术 ×2、等价类 ×1、迁移探针 ×1、
台账陈旧项 ×1、L0 对抗覆盖 ×6（逐 requirement 报 `has 0, need 2`，正是除雾那次漏掉的那道）。

### 26.6 反向验证与顺带记档

反向验证两头做：B3 Python 侧 29 条单项突变 + Go 侧 12 条（含 role 隔离两向）、
B4 8 条突变，逐条证明**在对应车道**变红；对照组在每次突变前后都全绿。
一处纠正：动作段拼错（`trunk.opne`）**不由** `edge_intents` 那条断言抓（它照样解得出对象），
由「验证定义」车道抓——**一条断言抓什么要以实测为准，不以命名为准。**

顺带记档：**`LOCAL_INTENTS`（路由：这句归端侧还是上云）与 `edge_intents`（能力目录：
planner 看得见哪些工具）是两个问题，不要合并。** 实测 `LOCAL_INTENTS` 164 条里有 87 条
不在能力目录里——端侧接得住、planner 规划不到；这与台账里那 14 条「是欠账」高度重合。

### 26.7 本批读数

根全量 `python -m pytest --import-mode=importlib`：**4775 passed / 14 skipped / 0 failed**
（单进程 22m09s，退出码 0）。较 B3/B4 前（`cadd084`，4643）净 **+132**，**逐条点得上号**：
`test_profile` 55 + `test_profile_coverage` 27 + `test_privacy_defaults` 5 +
`test_admission` 24 + `test_capability_gaps` 18 + `test_vehicle_intents_migration` 3 = 132。
零不明用例——这次是跟**同一个 SHA** 比的（上一批那笔「6 条不明用例」的账就是跟文档里的
陈旧数字比出来的）。

分组：edge **579**（B1 后 558）、registry **65**、cloud **655**、agents **993**、
`runtime/tests` **87**；端侧 smoke **13/13**；能力门禁 exit 0；L0 门禁 **2/2 exit 0**；
`eval_fast_intent` 57/57、`eval_route_hints` 78/78、skills / 范例门禁 PASS；
`run_e2e.py --check` 与 `--lane ci --full` 均 exit 0；Go 侧 build/vet/test 全绿
（容器内 `golang:1.24`，本机无 Go 工具链）。
CI blocking 门禁从三条增至**四条**（新增能力完整性）。

---

## 27. B5 重试表驱动 + 流式统一 / B6 可执行性 shadow：外部评审末两批的实施（2026-08-11）

裁决与批次索引见 [`reviews/2026-08-10-external-review-adoption.md`](reviews/2026-08-10-external-review-adoption.md)；
逐条落法与偏差见方案文档 §6（[B5](design/2026-08-10-b5-planner-retry-stream-refactor.md)、
[B6](design/2026-08-10-b6-actionability-forward.md)）。本节只记过程与判据。

三个提交：`191d3b4` B5 §4 流式统一 + §4.2 readback；`d1fd704` B5 §3 重试表驱动；
`21794d0` B6 §2/§3 可执行性 shadow。

### 27.1 触发条件当时并未命中

B5（加新重试规则/新流式路径前必做）与 B6（真实流量分母或该族再成主要矛盾）的启动条件
**都没有满足**，两批由泓舟直接指示推进。三份文档（两份方案头部 + 裁决总览 §4）同款留痕。
**判据：条件写在方案里是为了防「为做而做」，人明确要做时它不构成阻拦；但留痕必须做**
——否则下一次读到「条件启动」的人会以为条件曾经满足过，这条治理机制就被稀释了。

### 27.2 B5 §4：bug 活在推进面，所以推进也得共享

B1 修的那个 `elif streamed:` 永不可达，**不是手滑，是同一张判定表被抄了两份的必然**。
方案 §4.1 只给了纯函数，落地多加一个 `StreamTracker`——只共享判定函数、让 D0/T2 各自
推进状态，等于把出过事的那一半留在原地。

统一后 D0 获得两处**行为变化**（不是纯重构，各自单独取证）：

1. action 已发出而 final 丢失时，D0 此前说「请再试一次」——**邀请用户把一个有副作用的
   动作发第二遍**，正是 B1 在 T2 修掉的形态，而 D0 一直原样留着。
2. D0 此前对**软化前**的 speech 事件置 `streamed=True`：softener 扣下悬空 `*` 的那一拍
   用户什么都没看到，却足以把 unary 回退整条关掉。

`FINAL_RECEIVED` 没进枚举：草图既把它列成第四态、又传 `got_final` 形参，是同一件事的
两份声明（B4 判据第二例）。「流出过什么」与「拿没拿到 final」正交，且拿到 final 之后
仍要用「播过话术没」。

**§4.2 幂等键就地收口**：不新造 `command_id`。`_exec_step` 早就给 `step_timeout` 打指纹，
理由原文是「副作用可能已发生，不打指纹 replan 重出同一动作会被原样重发」——**流断丢 final
与超时是同一种处境**，而 B1 合成的那份不确定结果没有指纹。补上即闭合，B1 §6 担心的
「第三套局部实现」根本不必出现。

### 27.3 B5 §3：行为等价怎么证

13 条守卫变成 `retry_policy.py` 的表。**先从重构前的代码提清单入库**（方案附录 A），
再让代码表与它逐列比对——`test_inventory_matches_the_code_table` 比 5 个字段而不只比
name（只比 name 会漏掉「清单说 attempt_limit=1、代码写了 2」），并配一条先断行数=13
防空扫。

**差分取证**：21 条场景在表驱动前后两份代码上各跑一遍，逐字比对重问次数 / 每一轮回灌
给模型的 user prompt 全文 / plan_mode / 计划步骤 / clarify——**21/21 逐字一致**。

⚠ 但首版语料只触发了 11 条策略（`multi_action_omitted` 用了「并且」，不在
`_MULTI_ACTION_CONNECTOR_RE` 里；`open_close_polarity_inverted` 用了不存在的
`cap_0007`，被 schema 校验先接走）。**先验证覆盖再读结论**——补足后才 13/13，
不然那 21/21 对那两条一个字都没说。

四处字段不落（`metric_tag`/`risk_class`/`preserve_previous`/`validation_errors`），
逐条理由在附录 A.1。核心是 B4 那条判据的延伸：**落地前逐字段问「它有没有第二个真消费方」**
——`metric_tag` 有 11 条会与 `name` 逐字相同，`risk_class` 在 B1 把安全判定全搬出
planning.py 之后没有一条重试用得上。

消融开关 `PLANNER_RETRY_DISABLE` **先证明是活的再交付**（§3.2 第 4 条）：关掉已知有效的
`salvage_wire_accepted` 必须与 `PLANNER_TOOLCALL_SALVAGE_RETRY=off` 表现一致；
关掉极性守卫必须让反向计划直接落地。未知策略名直接抛，不静默当「什么都没关」。

### 27.4 B6：形态判据，以及一条断言当场抓到的退化

前三条修法的尸检给出了正解：**检索是内容通道，而「裸对象」是形态判据**。分类器只量
封闭虚词类，零检索零网络。

**「不是字面模式表」被落成机器判据**：从 `commands.yaml` 派生领域词表（对象 id /
display_name / edge_intents 段）比对语法标记。**首跑就抓到「导航」**——它同时是谓词和
VAL 对象名。按纪律删掉，零代价（真实导航句必带趋向介词或别的谓词）。
不写这条断言，这套东西会一点一点退化回对象特判，而且没人会注意到那一刻。

**三条读数纪律比读数本身重要**：

- **分母挑得越干净，假阳性越好看。** 首版按「有 plan 金标的轮」取分母读出漂亮的
  **0/472**——那个口径把 `ei.*` 端侧 ingress（「暂停」「锁车门」，恰恰是零谓述标记的
  裸动词短语）整批挡在分母外。改成「除澄清金标外的全部轮」才是 **4/574（0.70%）**。
- **确定性判据的 p 值分母是人为的。** 与 planner 的 11/20 摆一起算出 p=1.23e-3 只是
  为了同表可读；真正的内容是「零方差命中 vs 45% 漏判」。
- **唯一漏判不属于本族，不合并成一个好看的总分。**「有点看不清路了」与金标执行的
  「有点热」形态逐字同构，没有形态差异可用；它要的是「唯一默认动作是否存在」那个
  catalog 特征，归 canary 前的独立一步。

shadow 铁律用源码断言保证：`planning.py` 里 `actionability` 只许出现两次，且写入必须在
`plan.plan_mode` 之后——**把「不可能影响计划」变成结构事实，不靠人记得**。
`REJECT` 声明但 v1 不产出，一条断言钉住，防有人看见枚举就顺手补个恒不命中的分支。

### 27.5 本批读数

根全量 `python -m pytest --import-mode=importlib`：**4864 passed / 14 skipped / 0 failed**
（28m08s，退出码 0）。较 B5/B6 前（`abc3f49`，4775）净 **+89**，逐条点得上号：
`test_stream_state` 20 + `test_loop`（readback）1 + `test_retry_policy` 31 +
`test_actionability` 14 + `test_labels`（shadow 分歧后缀）1 +
`test_profile_coverage`（镜像构建闭包，见 §27.6）22 = 89。零不明用例。

分组：edge+registry **644**（579+65，未变）、cloud **721**（B3/B4 后 655，+66）、
`runtime/tests` **109**（87 +22）、observability **73**（+1）；端侧 smoke **13/13**；
L0 门禁 **2/2 exit 0**；能力完整性门禁 exit 0；`eval_fast_intent` 57/57、
`eval_route_hints` 78/78、skills / 范例门禁 PASS；新增离线回放 `eval_actionability`
（零 LLM、零网络，不进 CI blocking——它是取证脚本不是准入闸）。

### 27.6 真栈演练（同日补做）：抓到一个 B3 埋的真缺陷

B5/B6 合入后按 `make up` 全量重建 30 个容器演练。**动机不是「跑一遍安心」，而是有一处
单测结构上覆盖不到**：`turns.actionability` 的加法式迁移只在**已存在**的 obs.db 上才有
意义，而单测用 `:memory:` 永远是新建表。演练确认：既有 302 turns + 2377 spans 完整保留、
新列就位。

**抓到的真缺陷（不是本批引入，是 B3 埋的）**：`observability/collector` 与 `proactive`
两个服务的入口 import 了 `runtime.profile`（B3 加的部署形态闸），但它们的 Dockerfile
**没有 `COPY runtime`**。既有容器跑的是加闸之前的镜像，于是这处断裂在 **40 小时里毫无
症状**；一重建就 `ModuleNotFoundError` 起不来，三条 e2e 随之红。

> **判据：「代码里 import 得到」和「镜像里拷进去了」是两件事。**
> `test_profile_coverage` 那条「够得着闸」的断言照过——**它读的是仓库里的源码**。
> 这是 B3 自己那条「写进公共位置≠每个消费方都有」在 **Docker 层**的复发（同族第四次）。
> 修法是把构建闭包也变成断言（`test_service_image_contains_the_runtime_package`，
> 突变验证过：去掉 `COPY runtime` 当场红），不是「下次记得」。

**B5/B6 在真栈上的正向证据**：`turns` 表实测 `plan_mode` 三档齐全
（`toolcall`/`toolcall_salvage`/`toolcall_degraded`——表驱动后三条通道都还在走）、
`actionability` 列真实写入（`execute|0.95` 等），B6 §5 第 1 条由真栈而非单测坐实。
`e2e_ws`/`e2e_obs`/`e2e_context`/`e2e_process_region`/`e2e_proactive`/`e2e_ledger`/
`e2e_scene` 全 PASS。

**顺带纠正一处读数口径**：`run_e2e.py --lane ci --full` **只选 1 个用例**
（`e2e_protocol_smoke`）。B3/B4 那批记的「`--lane ci --full` exit 0」字面没错，
但比听起来薄得多——引用它时不要当成 e2e 全绿。

**`e2e_verify` 5 条红逐条定性为前提失效**，不是回归，立卡在 §4.2：该用例的前提是
「必须用混合多意图句才能规划出云侧 hvac 步」，而今天 `route.mixed` span 记着
`local_actions:1`——**端侧把车控那半自己执行了**，云侧只剩 `nearby.search`。
对账链本身是好的（按文本直查该 trace，`step.verify{mode:schema,verdict:sat}` 在）。
B5/B6 结构上不可能造成它：那个拆分决定发生在云端被调用之前。

## 28. 支付基础设施真实化四批：双渠道收单 + 商户 MCP 真机激活 + 端到端体验修复（2026-08-11）

> 方案/10 条裁决/逐批实施记录的唯一权威：`docs/design/2026-08-11-payment-infrastructure-and-merchant-mcp.md` §2/§6.1–6.5；契约 conventions §9.17（网关）+ §9.9（桥 transport/补偿两态/支付链接闭环）。本章只留流水与判据线索。

**背景**：泓舟拍板三批全做并当日填齐凭证（支付宝沙箱/瑞幸 token/麦当劳 token；微信商户号后置）。起点：payment-gateway 是「已建成从未接线」的孤儿服务（proto 在、server 漏回传 confirm_token 致 Capture 结构性不可达、store 纯内存、全仓零调用零测试零渠道字样）；真实跑的停车缴费是 Agent 内 mock 回执；桥只支持 stdio。

**四个提交**（全部当日推 main）：
- **批 1 `d964e1d`**（网关核心）：支付宝当面付/微信 v3 Native 双渠道自实现（httpx+cryptography，微信公钥模式优先+平台证书懒加载 TOFU）、store Redis 化 9 态状态机、Capture=确认后亮码、轮询 worker（zset 续轮停机不丢钱）+proactive user_contract 回执、PAYMENT_REAL_SCENES fail-closed、隐私四处+redact 同步、容器加固四件套、凭证只注入本服务。测试五件 96 条全离线（自造密钥对锁签名/AES-GCM/状态机/幂等/worker）。附带修 test_eval_intent_adversarial_cli 三条 PYTHONIOENCODING 环境敏感（编码两端钉死断言未动）。基线 4864→4960。
- **批 2+3 `94f7afc`**（parking 闭环+HMI+桥 http）：parking 切网关（confirm_token 幂等重取时序——engine 刻意不持久化 step.meta，token 只活在 handle 栈内；provider 删 pay() 还 TODO 债+回归钉）；幂等新语义（查找先于校验+可重付终态 remap——幂等防双付不防重试）；HMI payment_qr/payment_receipt/parking_fee 三卡（**二维码 SVG 网关生成**，前端零 QR 依赖）+ mcp_order/mcp_result 存量欠账清偿；桥 HttpMcpClient（SSE 分帧/会话存续/404 重握手/token 永不进日志）+admission ${VAR} 缺 env 拒载+compensate 两态+存在性校验+pay_url_locator 支付登记（域名白名单双层防钓鱼、登记失败不阻断出卡）。e2e_payment 真栈 3/3（「再缴费→已支付过」一句话合证 worker 推进与防双付）。两批共享文件多合并 commit（硬拆=单独 checkout 不可运行的半截历史）；批 2 独立全量因批 3 开工被判**混合快照作废**（pytest 跑着改文件）。基线 4960→4994。
- **批 3b `4a5cab0`**（凭证到位真机激活）：tools/list 现场核实（麦当劳 29 工具**无取消**→abandon_unpaid 坐实/瑞幸 8 工具**有 cancelOrder**——补偿两态被两家各占其一验证）；两家都是强多步 API（结构化 items/productList+storeCode/deptId 前置链）→ **v1 只激活只读三件**（mcd.menu 营养/mcd.order_status/luckin.order_status），下单归二期（编排结构化参数能力面）；瑞幸 queryShopList required 精确经纬度撞 third_party 禁 location.precise 红线结构性不可激活；门店发现归 nearby 不为激活数量留打架 intent。新机制 const_args；修 _resolve_order_ref args 层键名未走 arg_map；修能力门禁自身 GBK 崩溃。尺子全套：范例两域、语料 18 条（**cohort leakage 四句改写**——unseen 句与范例逐字相同被契约抓出；boundary 台账兑现双向各 2 例；relation invariant 3 条）、boundaries 两裁定（品牌词是判据）、suites 上界 540→560（第三次先例）、三清单哨兵有意更新（catalog 138/chars 11081/台账 23）。**沙箱真实联调抓修 GBK 验签 bug**（支付宝网关 GBK 响应×UTF-8 验签字节——中文出现必炸、全 ASCII 侥幸绿、MockTransport 结构性盖不住）。真栈三 server 准入+双商户真实问答冒烟。中途踩「compose 根入口」老坑一脚（-f deploy/... 读不到根 .env 插值——拒载路径反而先被真栈验证）。基线 4994→4996。
- **批 3c `6f06b41`**（端到端体验修复，泓舟指示「验证并让它合理」）：真栈 WS 五句取证抓四处不合理修后复验——`speech_mode: summarize`（「巨无霸多少大卡」从念 **6638 字英文 API 文档**变「巨无霸一份是513大卡。」12 字；LLM 不可用回落截断+见屏幕）、读路径缺引用改 NEED_SLOT 追问（账本回填的 order_id/幂等键算有引用——改坏一次被 61 条桥测试当场抓回）、mcp_result 卡瘦身 13KB→1.2KB、落域双修（demo shop.order 描述判别化——端到端实锤 planner 抢单真因是描述太像通用点餐；mcp-bridge#0 guard 增品牌让路词，专项回归 eval_route_hints 78/78+eval_fast_intent 57/57 兑现「动它须专项回归」）。顺带修**账本跨商户污染**（mcp_order 不分商户、demo 单号被灌给麦当劳查单把错配伪装成「用户没单」——result_ref 记 server 归属+回填只认同商户）。语料期望实测校准（「在麦当劳点个巨无霸套餐」forbidden 移出 mcd.menu——落营养并诚实说明是合理出路；当天 authored 未实测的期望按实测修正非让步）。chars_full 哨兵 11118（demo 描述加长 37 字符）。
- **沙箱定档**：泓舟扫码成功（真码可识别）但沙箱钱包内支付失败=支付宝沙箱当日服务级故障（查单同时段 48+ 连续 20000）；precreate 真码/query 语义/close/GBK 验签修均真栈验证，PAID+退款段待恢复重跑探针（已带浏览器自动弹大码——qr.alipay.com 链接会重定向不展示码、终端 ASCII 会截断，实测教训）。

**本批新判据**（详见记忆与 design §6）：pytest 跑着改文件=混合快照读数作废；幂等防双付不防重试；「机制通了」和「体验合理」隔着一次真栈端到端（落域全对数据全真，用户听到的仍可以是 API 文档朗读）；配置激活也要 tools/list 真机核实（报道 28 工具真机 29）；Windows GBK 宿主是本仓常驻放大器（当日同族三连：CLI 测试 env 敏感/能力门禁 ✓ 自崩/支付宝 GBK 验签）。

## 29. 麦当劳 / 瑞幸官方 MCP 复合工作流：未支付订单闭环（2026-08-12）

> 设计与逐项裁决：`docs/design/2026-08-12-merchant-mcp-full-flow.md`；契约：conventions
> §9.9。本文只记实施与真实验证事实，不把未实际执行的回归或浏览器复验写成绿灯。

**实现面**：桥新增复合商户 workflow，Planner 只见麦当劳/瑞幸业务 intent，官方低层工具
隐藏。门店、商品、规格、嵌套参数、计价与业务成功判定均由确定性 codec 完成；Redis TTL
草稿按用户/会话/商户隔离并在确认时原子消费；其商品/规格/金额/公开门店坐标按
`merchant_draft` 登记为可删除个人数据。确认消费同时建立共享 Redis 操作租约；Memory 全量
ForgetUser 的 MCP/Cloud 两个短期状态 responder 只在共享 Redis 可达时启用。删除若遇确认写在飞
则返回 pending，租约释放后重试并证明草稿清零才 ACK；10 分钟 TTL 只作故障兜底。写调用采用 `local_at_most_once`、single-flight
与 Task Ledger，禁止自动重放，超时只报 uncertain。支付入口经 bridge 与 payment-gateway
双层 host 白名单；查单结果由 `success_predicate + result_map` 归一为订单号/状态/金额白名单，
不让原始商户正文进入 HMI 话术。HMI 已有候选、订单预览、确认、支付链接、查单与取消动作；
意图资产覆盖点单/营养/附近/查单/取消及无品牌硬负例。

**真栈事实**：官方 `initialize + tools/list` 现场仍为麦当劳 29 工具、瑞幸 8 工具。只读预览
分别拿到真实营业门店、商品/规格与计价；浏览器留下麦当劳支付链接卡、瑞幸订单预览等证据。
本轮未执行最终付款。为锁定官方 create 响应路径并排查浏览器拒绝/续接问题，实际共创建
5 笔未支付订单（瑞幸 3、麦当劳 2），超过设计原定最多 3 笔；这是受控验证中的透明偏差，
不是将预算上限事后改写。三笔瑞幸均已取消，两笔麦当劳均由商户自动取消；未留下活动订单。

**证据纪律与边界**：早期 C9 只检查“收到回复”，真实商户已返回明确终态时，模型重述仍可能
说“没查到/待回传”；这些截图不算查单终态证据。收紧后的 C9 必须设置并命中精确预期状态，
最终只读浏览器复验分别命中瑞幸“已取消”和麦当劳“订单已取消”，且两次查询帧均保持
`is_confirmation=false`。两家 token/账号当前都是服务级全局凭证，不是多乘员独立账号；
payment host 也依赖运行时安全配置，空配置 fail-closed。密钥、完整支付 URL、订单号和地址
均不写入版本库文档。

**本批判据**：协议调用成功不等于商户业务成功，必须声明式判业务 envelope；旧浏览器脚本
“有回复即绿”不构成语义证据；真实写探针超过原预算时应停止盲目重跑、清理全部订单并逐笔
对账，而不是隐藏额外写调用。

**最终回归与审查**：冻结工作树执行根全量得到 **5408 passed / 14 skipped / 0 failed**
（23m25s，退出码 0）；mcp-bridge **385 passed**，隐私/旅程 manifest **168 passed**，HMI
node **253/253** 且 production build 通过。独立对抗审查抓出的真问题均按 RED→GREEN 收口：
真实订单号不得入库；瑞幸显式门店仍须 `nearby.search -> luckin.order` 可信 POI 两步；制作中/
待取餐等履约态不显示注定失败的撤销按钮；商户 scope 只能由认证主用户获得；Redis checkout
草稿登记 `merchant_draft`，删除与确认写通过 fence + active lease 线性化：delete-wins 时远程写为
0，operation-wins 时首次删除返回 pending、操作完成后重试才清零；Planner 挂起/焦点只保留恢复
所需的安全 slot-ref 投影。该联动只覆盖本批新增的两类短期状态，不冒充仍后置的全 registry saga，
demo/外部商户订单仍按 external reference 生命周期处理。根 `.env` 未修改，最终付款未执行。
合成 Docker 真栈只直接 seed owner-bound SessionStore/RedisDraftStore，不调用任何商户业务工具：
活跃租约存在时 ForgetUser 得到 503 pending；精确 release 后重试得到 200，最终 Planner 会话与
商户草稿均为零、旧租约授权失败，并按本次随机 owner 的精确 key 清理合成证据。

## 30. 商户 badcase 收口七批：授权链打通 + 门店菜单 + 跨轮锚定 + 两次自伤修复（2026-08-13）

入口是泓舟给的三个现象（会话 `demo-2goetq`）：「当前账号还没完成商户授权」、附近的瑞幸里
混着非瑞幸门店、「这家店的菜单」答出演示数据。七个提交推 main：`5819ca5` / `c2f8965` /
`c595d99` / `4ba36db` / `50a5ee0` / `10e6074` / `1f16260`。

### 30.1 三个现象的根因都不在它们看起来的地方

**① 「缺少商户授权」——能力从来没被授予过，跟账号无关。**
`luckin.order` / `mcd.order` 声明 `required_scopes: [merchant.write]`，而
`merchant.read`/`merchant.write` **全仓没有任何发放入口**：不在 `security/scopes.py::ALL_SCOPES`，
也不在 `orchestrator/cloud/context.py::_POC_DEFAULT_SCOPES`。运行时 audit 逐字为证
（`event=fail_open_default_scopes` 的 `required` 列表只有 11 项）。
而 `test/e2e_merchant_mcp.py:260` 自己往 meta 塞 `granted_scopes`——
**唯一能抓到这个洞的检查，正好被测试自己短路了。**
判据推广：**凡是测试「替被测系统提供了某个前提」，那条前提就不再被验证。**

**② 附近的瑞幸混进非瑞幸——不是瑞幸 MCP，是高德，而且品牌是我们自己丢的。**
span 写着 `provider.amap.place_around vendor=amap`，瑞幸 MCP 那轮根本没被调到。
`nearby._build_keyword` 在非餐饮类目分支用干净类目词覆盖了 planner 只填在 `keyword` 里的
「瑞幸咖啡」，实际检索词是「咖啡厅」（话术「找到 10 家**咖啡厅**」即证据）。
下游更危险：同 plan 的 `luckin.order` 用 `slot_refs` 直取 `items.0` 当下单门店——
品牌一丢就是拿别家咖啡店去瑞幸下单。
修法收紧成「短前缀（无虚词/数量词）+ 已知类目别名」，宁可漏认退回类目词也不把整句灌给高德；
**第一版曾假设 `_strip_qualifiers` 能剥掉整句，被同批写的反向护栏当场证否**
（`人均百元的停车场` 剥完还是整句）。

**③ 「这家店的菜单」答出演示数据——目录里没有更好的选项，模型没选错。**
瑞幸/麦当劳都没有暴露「看菜单」的只读能力，「菜单」在整个能力目录里只对应演示商户的
`shop.menu`。补了 `luckin.menu`（复用同一条门店可信链）与 `mcd.menu`（见 §30.4）。

### 30.2 真机取证纠正的四处臆测（成本一个容器内脚本，收益四个真 bug）

- `searchProductForMcp` 的 `query=""` 被商户判 `code=1000 非法参数` → 无 item_query 时给种子实词；
- 它一次最多回 **3 条**，是「搜商品」不是「列全菜单」→ 话术不许说成菜单/全部；
- 价格字段是 `estimatePrice`/`initialPrice`，第一版写的 price/salePrice/discountPrice
  **真机一个都不存在**；
- 高德 POI 名与瑞幸官方 `deptName` 是**两套写法**（官方认「瑞幸咖啡(前海华强金融大厦店)」，
  不认「luckin coffee 瑞幸咖啡(华润前海23F内部店)」，而空名 + 同坐标返回 8 家）
  → 按名查空时用**坐标**再查一次，可信链不放松（距离一致性 + 同名匹配两道闸原样生效）。

判据：**照常见命名猜字段，是最容易被真机否掉的一类假设。**

### 30.3 跨轮门店锚定：方案、两次落地失败、最终形态

方案 `docs/design/2026-08-13-cross-turn-store-anchor.md`。核心判据一句话：
**跨轮延续的是「服务端记得取回过哪些门店」，不是「让模型把坐标再说一遍」**——
后者等于取消 provenance。落点接既有的 `_apply_focus_meta` 那条路，但 provenance 走
`PlanContext.focus_places`（服务端对象，LLM 与客户端都写不到），因为 `step.meta` 会被
`_resolve_slot_refs` 每轮 pop（那个 pop 本身是对的，它挡的是计划里的伪造值）。

落地过程中被真栈连着否掉三个假设：

1. **挂点漏了一条执行路径。** 锚定挂在 `executor._resolve_slot_refs` 上，加诊断日志后真栈
   **一行都没打出来**——`luckin.menu`（`require_confirm=false`）走的是 **D0 流式直通**，
   那条路绕过 executor。补在 D0 分支里显式解析一次，并加源码级回归探针。
   **新增挂点必须枚举全部执行路径**——本项目第二次踩（M2 的 Outcome Verifier 同款）。
2. **悬空引用是跨轮的表达方式，不是计划缺陷。** 第一版守卫「声明了 slot_ref 却没解析成功
   → 让路」会让锚定永远不触发，因为第二轮模型产的正是 `slot_refs: {store_name:
   "s0.data.items.0.name"}` 而 `s0` 是**上一轮**的步骤 id。正确判据看生产者在不在本轮 `done` 里。
3. **Planner 给的门店值只当线索不当值。** 另一种真栈形态是
   `slots: {store_name: "<抄来的店名>", store_longitude: "s1.data.items.0.lng"}`
   ——坐标是没解析成的 ref 字面串。两者都没有 provenance，消费侧本来就会拒，
   所以「slots 里已经有门店了」不是「已有门店」而是死路。改成：名字当线索、值只从服务端
   持有的列表取；名字对不上就不锚定（诚实失败），**绝不拿第 0 条顶替用户点名的店**。
   provenance 里的下标必须是**实际命中**那条——写死 0 会让「同前缀同下标」变成一句谎话。

### 30.4 麦当劳同步：`mcd.menu` 让位给当店菜单

对账后发现麦当劳只同步了一半。补齐拒绝话术后，两处真缺口用真机取证定了性：官方
`query-meals` 返回的是真正的**全店菜单**（`categories` + `meals`，每条带
`name`/`image`/`currentPrice`/`originalPrice`，图在 `menu-img.mcd.cn`），比营养表能回答用户
真正在问的「有什么、多少钱」。于是 `mcd.menu` 让给当店菜单，营养成分表改名 `mcd.nutrition`
——**它返回的一直是营养不是菜单**，旧名让「麦满分多少钱」只能回「这个接口里只有营养信息」
（trace `c523c303`）。

图片白名单沿用 `pay_url_hosts` 的既有形态（`image_hosts` 精确 hostname，不合规静默丢弃退回
纯文字），判定提到 `MerchantWorkflow` 基类两家共用一份——两份判定必然漂移。

真栈又抓到两处我方实现错误：`or products` 的兜底把无关餐品挂在用户问的名字下面
（「猪柳蛋麦满分多少钱」答成「…的"猪柳蛋麦满分"有：蘸酱韩式甜辣酱鸡块…」，**比答不出来糟得多**）；
planner 会把整句塞进 `item_query`，菜单路径补反向包含兜住、**下单路径刻意不放宽**。

### 30.5 两次自伤：为躲一句话术换来功能不可用

**这是本批最该记住的一段。** 「拒绝落在 OK 上会被中央确认闸拼成
『…不能执行。这个操作需要您确认后才会执行，确定继续吗？』」是个真问题，但前两次修法都错了：

| 做法 | 结果 |
|---|---|
| 拒绝落 OK（原状） | 自相矛盾的一句 |
| 改用 `NEED_SLOT`（`c2f8965`） | **真栈证否**：挂起会话、吞掉后续每一句 |
| 显式保留键 `_refused`（`1f16260`） | ✅ |

`NEED_SLOT` 声明缺 `store_name/lng/lat`，而这三个槽**按设计只能来自 nearby.search 的可信 POI，
用户永远填不了**。引擎据此挂起会话，此后每一句都被当补槽答案吃掉
（`Resuming plan ... (slot fill step s1, text=看麦当劳(科苑南路餐厅)的详情)`）——
问麦当劳答瑞幸，「第一个」「选择瑞幸门店：X」也全被吞（会话 `demo-f1hkwr` / `demo-r6qjf4`）。
**拿「话术不好看」换来了「功能不可用」。** 同形态老账（「请先查询附近的瑞幸门店」那句在本批
之前就是 `NEED_SLOT`）一并修掉。判据入库：**不许把用户填不了的东西声明成 `missing_slots`。**

最终形态走显式保留键（§9.1 登记，与 `_escalate` 同族）：闸认这个键**只免除追加问句、
不免除扣动作**——自称拒绝却带 `actions` 的结果照旧改判 `NEED_CONFIRM` 并记警告。
动安全件的全部安全论证是一句可断言的话：**未声明该键的 Agent 逐字零行为变化**
（`test_unmarked_results_keep_the_old_behaviour_verbatim`）。

同批还修掉第二个自伤：**焦点每轮从当前 plan 重建**，turn 2 只要没有搜索步就把 turn 1 存的
门店抹成空；再深一层，**salvage/replan 轮里引擎递给 `update_focus` 的是重规划后的 plan**，
`nearby.search` 根本不在里面——这解释了同一句话走 `toolcall` 能通、走 `toolcall_salvage`
就断。改成门店列表**粘性**（只有新的 nearby.search 才替换）+ 从 **results 的 `source_intent`**
取（执行器盖的章，比调用方递来的 plan 可靠）。

### 30.6 验证方式本身的教训

两个自伤都是**泓舟打回来才发现的**，而我上一轮说过「真栈已验通」。原因是：
**探针从来没测过「拒绝之后再说一句」**——全是干净会话或顺利路径，而挂起黑洞只在失败态之后
才出现；两轮测试也测不出焦点覆盖，因为第二轮恰好紧邻搜索轮；CDP 用例同样只跑到
「卡片渲染出来」，没跑「卡片之后还能不能继续对话」。

沉淀成一条纪律：**验证多轮系统必须跑「失败态之后再说一句」和 ≥3 轮，
happy path 和干净会话证明不了会话状态是对的。** 已钉成回归：
`test_store_refusals_never_suspend_the_session`、
`test_last_places_survive_a_turn_that_did_not_search`、
`test_places_come_from_result_provenance_not_the_plan_object`、
C10 的「选品后换话题仍正常」。

### 30.7 门禁连着抓了五条（都不是我事先想到的）

- 新增 active intent `luckin.menu` / `mcd.nutrition` 各要补 2 正 / 2 硬负 / 1 对照；
- 新范例与 `unseen_transfer` 用例撞句 → **cohort leakage**（且是在我已跑绿一次之后才红的
  ——新增的知识改变了前提）。规矩：范例保持自然说法，**改用例侧的措辞**；
- 跨域近重复要求登记边界：「附近的**瑞幸**有什么可以点的」首步是 `nearby.search`、
  「这家**麦当劳**有什么可以点的」直接 `mcd.menu`。判为**真边界不是地盘冲突**——瑞幸官方菜单
  绑 `deptId` 只能由可信 POI 映射，麦当劳按 `store_hint` 文本查店，**是两家接口形态的差异**。
  已登记 `luckin-mcd.menu-store-resolution` 并按契约补齐双向各 2 例；
- 语料上界四次递进 560→564→568→570→571，每次的占用理由都写在 `suites.yaml` 头部；
- CI 那条时区红灯：`assert now.tzname() == "Asia/Shanghai"` **只断了本机那条分支**
  （Windows 无 tzdata 走定偏移 → `Asia/Shanghai`；Linux/CI 走 ZoneInfo → `CST`）。
  改断 `str(tzinfo)`，两边实测过。

### 30.8 环境侧留档

- **高德间歇性不可达**：本机到 `restapi.amap.com` IPv4 连接超时、IPv6 无路由
  （`connect=0.000000s`），而百度 90ms 正常、DNS 解析正常。**不是配额也不是代码**。
  排查中一度误读成「配额被探针打光」并据此在 `c595d99` 的 commit message 里写了
  「没跑通真栈整轮」，已在后续提交更正。判据：**拿不到结果时先分清「资源没了」和「这次没成」。**
- CDP 安全用例集（C1/C3/C5/C6）在全栈重建后首轮红、复跑即绿——是**已记录的
  registry 重注册期假红**形态，不是回归。

## §31 2026-08-13 demo-mkemhn 十九轮复盘六批收口（商户 MCP + HMI 交互）

入口是泓舟的指示：「阅读 demo-mkemhn 这一轮次里的所有会话，系统性优化麦当劳/瑞幸
MCP + HMI 交互」。逐轮拉 trace（span/llm_calls/logs 级）取证后归并 8 条根因，
方案与逐轮登记表在 `docs/design/2026-08-13-demo-mkemhn-merchant-hmi-hardening.md`。

### 31.1 取证结论里最值得留的四条

- **兜底链会自己编造事实**。planner 线契约按「步骤字段与五键精确相等」判，MiniMax
  高频漏写 `slot_refs`/`depends_on` → 整份计划被丢 → 重试打光 → chitchat 兜底，
  而 chitchat 拿着对话历史生成了「已为您找到 10 家瑞幸门店，请选择其中一家」
  （无列表无卡片，3650e2b5）和「请确认：前门大街店，应付 10.90 元」（不存在的
  待确认单，2fd09d52）。**掉底不可怕，掉底后模型冒充系统才可怕。**
- **聚合 LLM 会替系统许诺**。两条「请先查询附近的瑞幸门店」拒绝被改写成
  「已选定。我现在去获取这家店的公开 POI 坐标，获取成功后再帮你做后续操作」
  （78b635db）——系统本轮不会再做任何事，这句话全是假的，还把内部术语说给了用户。
- **位置丢失是静默级联**：HMI 浏览器定位刷新失败把 `locationEnabled` 悄悄置 false
  → 请求不带坐标 → nearby 走高德 `/v5/place/text` 全国检索 → 北京什刹海/前门/清华
  被报成深圳用户的「最近」；用户纠正「不是北京哦」也无从生效，
  因为**系统不知道自己少了什么**（59b34983/cffc84fd/44943f00）。
- **锚定实现与设计文档差三处**：门控缺席（门店三槽被注进 chitchat/nearby 步）、
  粘性接力让「TTL 过期即失效」形同虚设、双过期后指名门店/候选卡按钮全进死路。

### 31.2 六批修复（各批一句话 + 提交）

- **A 编排层**（`d7829c3`）：缺席容器键按空值补齐（未知键/身份键照旧整份拒绝）；
  锚定门控=capability 声明门店三槽才吃焦点（planning 装配 `declared_slots`，进程内
  字段不下发）+ 限龄 `MERCHANT_STORE_ANCHOR_MAX_AGE_S`（缺时间戳按过期）；聚合
  prompt 增诚实约束（失败如实/禁承诺/禁编造）；`place_list` 进安全计数分支，挂起
  前缀不再 60 字符腰斩门店名。
- **B nearby**（`6362491`）：无任何搜索中心时品牌/品类发现诚实降级（不打 provider、
  不冒充「附近」）；指名门店（括号或 ≥4 字+门店后缀）仍按名检索但话术说「按名称
  找到」；`data.center` 标 vehicle/slot/none；`_build_keyword` 不再把整店名改写成
  类目词。
- **C mcp-bridge**（`01e42ba`）：双过期+店名线索 → `_escalate` 改派 nearby.search
  按名取回（engine 既有一跳；坐标可信链零放松）；拒绝话术去「POI 坐标」术语；
  luckin.menu 整句 item_query 退种子词+反向包含（只放宽只读菜单）；
  `_readable_speech` 相关性打包取代盲截 3000 字（营养表条目在截断点外=「没查到」）。
- **D 知识**（`a1fd506`）：选店闭环范例三式×两商户（候选卡按钮句式/指名门店菜单/
  换店保商品）+ 纯发现范例；guide 营养口径 `mcd.menu`→`mcd.nutrition`（改名后
  guide 还在教旧账）+「这家=焦点门店」判据；shop.menu 目录描述补真实品牌排除；
  chitchat system 增防编造执行结果守则。
- **E HMI**（`c2910e6`）：预览卡（创建确认挂起中）补「换一家门店」chip——发
  「换一家{品牌}门店，还是点{商品}」与范例句式闭环；place_list 品牌店行内
  「看菜单」直达；纯函数落 `merchantUi.mjs`。
- **F 语料**：两条新边界裁定（纯发现 vs 发现+看单；换店保商品两家接线形态）
  双向各 2 例，上界第五次递进 571→579（理由在 `suites.yaml` 头部，旧尺子零删除）。

### 31.3 验证

分批实测：orchestrator cloud 焦点/执行器/规划新增守卫全绿（executor/context 76、
planning 族 203+2 新）、nearby 56、mcp-bridge **404**（基线 396 +8）、HMI node
**257/257**（+3）+ Vite build；四条 blocking 门禁全绿——skills 22/22、范例（域错配
2.4% 持平）、L0 strict **2/2**（discovery 618 条/579 唯一输入恰好用满、gate 25/25）、
能力完整性 exit 0。全量根跑见 §4.0 快照刷新。

### 31.4 判据沉淀

- **验证多轮系统「失败态之后再说一句"的第三种形态**：失败态之后掉进兜底的那一句。
  前两次自伤盯的是挂起黑洞与焦点覆盖，这次是兜底 LLM 冒充系统作答——探针要把
  「planner 连续丢计划」也当成一个可注入的失败态。
- **「系统知道自己少了什么」是诚实降级的前置条件**：nearby 明知无位置却说
  「为您找到」，用户的纠错就永远打不中要害。降级话术必须点名缺的是什么。
- **固定句式的 UI 按钮是范例库的天然客户**：候选卡按钮文本恒定，给它一条范例
  等于把这条交互焊死在正确的计划形态上——比教会模型理解一万种变体便宜得多。

## §32 2026-08-13 demo-3ukshz 二轮收口（麦当劳定位选店 / 菜单全量 / 规格定制）

一轮修复真栈可见生效后泓舟复测（demo-3ukshz，12 轮）提三点，加逐轮 trace 与
二轮探针共定位六处；方案表与逐条修法在设计文档 §5
（`2026-08-13-demo-mkemhn-merchant-hmi-hardening.md`）。一句话版：

- **①麦当劳附近**：nearby.search→`store_hint` 文本链（GPS 红线不动），桥侧
  `_store_keyword` 剥高德名，旧「附近」example 撤下，默认店话术必须披露；
- **②菜单全量**：瑞幸多种子聚合（接口每词最多 3 条无全量面，话术口径「在售
  不止这些」）；麦当劳分类导航（`category` 槽 + 分类 chips + 「共 N 款、M 分类」）；
- **③规格**：预览卡 `spec_options` 只含 `_SPEC_GROUPS` 四族（下单链真消费得动的
  组才上卡——改不动的组做成按钮就是必然失败的入口），HMI chip 发
  「在{店}点一杯{品}，要{规格}」与范例闭环；菜单卡「第N个」HMI 直达下单句；
- **④挂起黑洞变体**（二轮探针当场抓到，4.3 纪律第三次兑现）：麦当劳单候选也挂起
  「请选择一家」，吞掉后续两句新意图——唯一候选直选 + `_is_topic_change` 补
  句中问式（有什么/哪些）与「动词+数量+量词+宾语」两条零领域字面量判据；
- **⑤**口味画像重复播去重；**⑥遗留**：多步聚合仍会丢 `_refused` 步的那句拒绝、
  附近+菜单组合轮菜单卡被 place_list 压后（两条都记录未修）。

验证：cloud+bridge+nearby+chitchat 合跑 **1274 绿**（后含 topic-change 批 1198 复跑）、
HMI **258**+build、四门禁全绿（catalog 11828→**11866**：mcd.menu 描述改写=撤掉
「就近选一家」假承诺 + category 槽）；真栈探针「附近的麦当劳」首次答出真附近门店
（高新中五道，不再是碧海君庭）。

判据沉淀：**「挂起黑洞」的判据要在每次新增 NEED_SLOT 面时重问一遍**——这次是
「单候选也出选择卡」造出了新的挂起面；以及**探针要在修完后立刻跑**，④正是
二轮探针在「已经全绿」的树上当场抓到的。

### 32.1 打磨项收口（2026-08-14 上午，营业时段全旅程真栈验证）

两条遗留 + 探针连揪的第三跳，全部收口（设计文档 §5 表 ⑥/⑥b/⑦）：

- **⑥ 聚合丢拒绝句**：`_refused` 步与 `_VERIFY_NOTE` 同一原则——不进 LLM 材料、
  确定性原样附加（恰好一次）；全员被拒零 LLM；follow_up 优先取拒绝步。
  真栈组合轮实测拒绝句原样到达。**判据沉淀：诚实约束写进 prompt 不等于诚实——
  系统持有的事实要走确定性通道，LLM 只组织它没资格删改的内容。**
- **⑥b 菜单卡被压**：`luckin.menu`/`mcd.menu` 卡标 `display_priority: 0`。
- **⑦ 静默换店三跳**（每修一跳探针再揪下一跳，「失败态后再说一句」的连环版）：
  planning 补 `_derive_store_hint_edges`（refs/deps 双缺→同层并行踩空）→ executor
  补 `_hint_store_from_plan`（同轮 nearby 产出填 store_hint，刻意不吃跨轮焦点防
  品牌错配）→ mcd「唯一候选直选」在 hint 未命中时静默顶替点名的店（官方测试租户
  查无该店时返回默认店列表）——改为诚实查无 + 确定句式出路，仅无 hint 允许直选。
  **事实边界留档：麦当劳 MCP 测试账号官方可点门店当前仅碧海君庭一家**，
  「真点附近那家」受商户侧数据限制，系统侧已把查无诚实化。

营业时段全旅程真栈：瑞幸聚合 10 款菜单主卡 / 预览温度 chips / **改规格生效
（冰→热）** / 换店句走商户流 / 取消；麦当劳附近 10 家真店 + 诚实查无句原样附加 +
「选择麦当劳门店：X」一句直达菜单与分类导航（105 款 14 分类，分类过滤 9 款）。
聚焦回归 cloud+bridge+nearby **1260 绿**（新增 aggregator 2 / executor hint 1 /
planning 边 1 / mcd 3）。

### 32.2 「仅碧海君庭」勘误（2026-08-14，泓舟质疑推动）——是配置错误不是商户边界

§32.1 里「麦当劳 MCP 测试账号官方可点门店仅碧海君庭一家」是**错误结论**，由泓舟
「官方说明深圳很多店都支持」的质疑推动重查后推翻。真机 schema 取证：
`query-nearby-stores` 的 **`searchType=1 = 搜索收藏餐厅`**（servers.yaml 一直写死
的档，碧海君庭=测试账号唯一收藏）、`searchType=2 = 按位置搜索`（city+keyword
必填，缺 city 报 600058）；searchType=2 实测 top1 即目标门店，官方深圳覆盖良好。

根因链四跳与修法在设计文档 **§5.1**：写死 searchType → 动态选档；city 无来源 →
Place/items/焦点 `last_places` 增第四个安全标量 `city`（跨品牌无错配面，坐标锚
同款时效门控）；补全早退缺口 → city 独立于 hint；裸 `$` 占位符残渣 → 解析放行 +
残渣当未填。真栈终态：「附近的麦当劳有什么菜单」→ 高新中五道餐厅 109 款 8 分类。

判据沉淀两条：
- **「接口返回了数据」≠「接口在按你以为的语义工作」**——参数枚举值的语义必须以
  官方 schema 描述逐个核对；写死枚举值前先问「另一档是什么意思」。searchType=1
  返回碧海君庭返回得好好的，掩盖了「查的是收藏夹」整整三天。
- **下错误结论比不下结论代价高**：「仅一家店」写进了 AGENTS/history/commit
  message 三处，勘误也要三处（本节 + AGENTS 段落改写 + 设计文档 §5.1）。
  「拿不到结果时先分清资源没了和这次没成」的姊妹判据：**拿到了结果也要分清
  「它只有这些」和「我只要了这些」**。

聚焦回归 1261 绿（context 焦点 city 1 / executor city 兜底 2 / mcd escalate 1 /
searchType 选档 2 新增或改写）；wrappers_ci 单红复核为并行 Docker build 的
负载性假红（隔离复跑 6/6）。

## §33 2026-08-14 EVA 指令集二轮对标：缺口分析 + 五批实施（批 A-E 全合入）

**入口**：缺口分析 `docs/design/2026-08-14-eva-round2-capability-gaps.md`（25 条竞品语料
逐条比对，缺失≈17，归 11 簇；定性=缺的是执行面维度不是规划智能）→ 实施计划
`docs/design/2026-08-14-eva-round2-implementation.md`（§7 实施记录）。泓舟确认方向并
授权 commit/push；P3 簇（G4 主题行程/G8 导航会话状态/G9 trip 跨城市）维持另立 RFC，
G10 订座/票务维持搁置。

**五批 commit**：
- 批 A `51c4992` 存量问题七条：nearby 评分**先排后截**（此前「评分最高」被偷换成「最近
  10 家里评分最高」）；trip.navigate「第一站」全程序数（`and day_n` 短路 + 剥壳残留
  「第N站」当店名，两测试先红后绿实证）；主动治理器 `_flush` 按 owner 分组（跨乘员同窗
  消息此前会合成一条记在一人名下）；死槽位五处摘除（B4 纪律）；conventions **§9.18**
  条件提醒创建时求值记账 + navigation/parking README 勘正。
- 批 B `04ff730` 导航域五刀：G1 `arrive_by` 到达时限（ETA 三档量化话术 + 绕行Δ + 顺路
  候选逐家 ETA + REMINDABLE「出发前往」反向环，reminder 按出发/到达词形择项）；G2 顺路
  候选改**真沿途**（路线 45% 里程采样点，回落目的地附近时话术如实说）；G3 landmark 扩
  俗称封闭词表（秋裤/裤衩/小蛮腰/开瓶器）+自然地物×形容共现+prompt 城市约束最高优先；
  G11 route_pref→高德 v3 strategy（该参数此前全仓从未使用；「风景」诚实降档）；G9 多
  途经点 、/和 连写保序。范例 +5。
- 批 C `ad0f2e8` 记忆消费面 G6：proto MemoryItem 加 `subject`（关于谁——「我爸不喜欢
  空调冷」的「爸爸」此前只活在字符串里）与 `polarity`（like/dislike 闭集），
  RecallRequest.subject 过滤，冲突 supersede 按 subject 收窄；抽取输出三新字段 +
  route.* 谓词归一。四个消费出口逐环配「记忆改变行为」断言：①nearby 修**假个性化**
  （口味从「搜完拼话术」改为检索前偏置 + 负偏好软降权 + 「和老婆吃饭」subject 并取）
  ②navigation route.* 记忆→strategy（「以后都不走高速」持续生效）③导航成功落 episodic
  轨迹④planner「上次/那次」放开 episodic 召回。catalog 锚点 11866→11928（净+62 留痕）。
- 批 D `32fcdaf` G7 询问式主动：抽取当轮的未来事件（event_time 确定性校验：未来 90 天内）
  → `reminder_card(context=offer)` 建议卡「要到时候提前提醒你吗」——**零执行权不破**：
  不自动建 reminder，「要的」按钮 send_text 回发正常语音链；标题剥双时间词防解析咬错；
  new_ids 门控只在抽取当轮建议一次；过去事件永不打扰。HMI reminder_card 扩 offer 态。
- 批 E `7d47959` G5：类目表补动物园族（「带孩子看看动物」此前兜底错搜「美食」）；
  「餐饮」默认改为仅饮食信号下成立、全无信号诚实追问；氛围词（安静）→环境标签+评分
  软重排 + 「地图没有安静度数据」诚实话术。范例 +2。

**四条实施期修正**（方案 vs 实测，详见实施计划 §7）：自埋「23点→3点」错拆被自己的
测试抓住；饮食信号面首版过窄（「川菜馆/火锅」够不着）被两条既有测试打红——诚实追问的
代价是把「本来就是餐饮」的判定面配齐；迟到话术首版引导「途经点不去了」（G8 未实施的
能力）被改成「直接导航去X」——**话术只许诺今天走得通的路**；catalog 预算锚点按其自身
纪律更新。

**验证面**：分组全绿——navigation 96 / nearby 64 / reminder 157 / trip 47 /
charging 42 / proactive 62 / memory 215 / orchestrator-cloud 全套（catalog 锚点 10）/
_sdk 31 / HMI node 258 + Vite build；四门禁绿（L0 strict 2/2、能力完整性、范例 291 条
域错配 2.4%、exemplars）。全量基线见 AGENTS.md §4.0（本批后重测）。Go 侧零改动未重测。
真栈 journeys 全量与容器 rebuild 未在本批执行；memory proto 变更后首次 `make up`
需 `--build` 重建 memory 及依赖 `_sdk` 的服务。

### §33.1 2026-08-14 真栈端到端验证 + 批 F 三修（EVA 语料 13 轮）

`make up --build` 全量重建后，EVA 语料 13 轮经 WS 真栈跑通（探针形态与逐轮读数见
实施计划 **§8**）。首轮 8 通 5 有发现，当日修三条并复验全通：F1 demo 咖啡 replace
hint 缺导航语境让路词（「导航…路上买杯咖啡」整条计划被改写成 demo 下单，trace
21f99cb3——hint 在 LLM 之后动手，guide/范例拦不住）；F2 route.* 记忆消费不得按
polarity 过滤（抽取把「不要走高速」合理标成 dislike，方向已编码在谓词名里）；
F3 轨迹写入补挂 search_poi 自动导航分支（挂点枚举教训第三次应验）。复验样板轮：
「导航去东方之门，路上买杯咖啡，五点前要到」一轮内给出时限判定（预计13:25，早约
215 分钟）+ 真沿途候选（歇马桥段）+ 逐家 ETA + 记忆不走高速自动叠加。B1 记忆链
全通（subject=老婆的粤菜偏好 抽取→落库→消费）。**留档未修**：目的地接地就近包含
误伤家族一天三见（虹桥机场→如家停车场/外滩→星空艺术馆/滴水湖→雅悦酒店，存量
R1 家族，修法需单独取证）；G7 询问式的真栈触发面（不带「记住」的顺嘴提及）本轮
语料未覆盖。探针种入 u1 的数据已清（11 记忆+1 关系边+1 提醒）。

### §34 2026-08-14 晚：接地卡 R1 二期收口 + G7 真栈补验 + journeys 全量重跑

接手会话按 §4.1 交接项当日收口，四提交 `f3df5bb` / `4e7310b` / `cc7e5ba` / 文档批。

**接地卡 R1 二期（`f3df5bb`，「先取证再动码」的活标本）**：真高德三形态取证
（near limit=3/10、near=None）**否掉了卡上首选 D1**——五条红例（虹桥/浦东机场、
外滩、滴水湖、西湖）的本体根本不在 near 候选集里（limit=10 都没有），候选集内重排
修复数 0/5；红例族取证中扩到千岛湖（→上海的千岛湖鱼头馆）与东湖（→东湖公寓）；
「站/公园」族实测现状绿（既有重搜已救）刻意不扩面。**还抓出立卡时不知道的行政级
新红例族**：高德 geocode 对多义裸名返回错省行政区划——「西湖」level=区县、坐标是
台湾苗栗西湖乡（现状会直接导去台湾）、「东湖」→南昌东湖区、「金山」→台湾新北金山区，
而「佛山」市级正确。落地：D2 类目锚词复核（机场/湖/滩，逐条红例背书）＋候选集内
名字+类目双匹配（零 API，「东湖」→#2 东湖绿地，比重搜接到武汉东湖更合就近意图）＋
wide 双匹配扫列表（「滴水湖」全国序 #1 是同名地铁站）＋主干级名字匹配（「虹桥机场」
与「上海虹桥国际机场」隔着「国际」不构成连续包含——剥锚词后主干 ≥2 字配类目复核，
名字管专名、类目管类目）；行政级分支区县级仅「有定位且 ≤150km」可信、市级以上不动
（跨城导航合法）。D3 恒做对照搜索不需要了——重搜只在类目失配时触发。验收：对抗
迷你集 **14/14 真高德全绿**（红 7 修复/绿 7 无回归含 R1 一期全部既得）、navigation
107、route_hints 80/80、fast_intent 57/57。

**D4（`4e7310b`，独立 commit）**：历史指代（词形与 planner `_EPISODIC_REF_RE`
同组，两侧注释互指对齐义务）+名字对齐 → episodic 轨迹坐标直取，跳过整条 POI 搜索链；
坐标不经 LLM（防编造）；名字对不齐不做语义猜；search_poi 自动导航分支**枚举过并
有意未挂**。navigation 111（新增 4 条全部断言「记忆改变了行为」）。

**真栈复验 5/5**（重建 navigation 容器后 WS 探针）：接地卡四条 trace 输入全部接到
本体——「不走高速送我去虹桥机场」→上海虹桥国际机场（原如家停车场）、「导航去虹桥
机场」同、「风景路线去外滩」→外滩本体+降档话术（原星空艺术馆 8 层）、「带我去上次
我们去过的那个湖」→坐标与前一轮「导航去滴水湖」的轨迹逐位一致（address「您去过的
地方」即 D4 构造字面，零重搜）；「不走高速」记忆偏好在后四轮自动叠加（G6 无回归）。

**G7 真栈补验（`cc7e5ba`）**：用「不带记忆指令的顺嘴提及」补验批 D 的询问式机制，
抓到两个只有真栈才暴露的缺陷：① 抽取 prompt **无日期锚**——event_time 要 LLM 把
「下周五/下个月15号」换算成 ISO 绝对时间，无锚只能借 planner 回复轮的复述或按训练
先验猜年（容器内采样：「下周五提车」8 次 0 给出；措辞「日期/星期与时刻」还被模型读
成必要条件）。锚+措辞修后两形态各 4/4 且日期正确（planning.py 日期锚同款判据）。
② offer 卡 time_display 用 `time.localtime` 在 **UTC 容器**里把「9月15日00:00」
显示成「9月14日16:00」，且会经「要的」按钮 send_text **传导成真错提醒**——宿主机
UTC+8 跑单测永远不红；修固定车机 UTC+8，新断言用固定 epoch 任何时区判同一结果。
修后真栈复验：顺嘴「我们下个月15号要去北京出差」两轮凑满节流窗（4 次 AppendTurn）
→ proactive 推送 `event_reminder_offer`「我记下了：…（9月15日00:00）。要到时候提前
提醒你吗？」——**G7 询问式通道真栈首次亮相**，零执行权形态正确。memory 217。
**留档待议**：planner 把顺嘴陈述接成 reminder NEED_SLOT 追问「什么时候提醒你？」，
与 G7 offer 双通道抢话——落域宽度问题，处置默认走范例/边界台账不是 hint。

**journeys 全量重跑（EVA 二轮欠账）**：干净首跑 **35/37**（回归级 15/16、目标级
20/21），两红 trace 级定性——A3-2（回归级）「详细讲讲第2条」planner 指代解析正确
（goal 完整点名第 2 条新闻标题）但把深挖派给 `info.search` 浅搜索出 search_result 卡
（该轮走 salvage 通道）；B1-3（目标级）「那附近有停车场」planner 规划裸
`nearby.search(keyword=停车场)` 无中心槽，「那附近=万象天地」迁移失效搜出当前位置
福田的停车场。两条 **7/25 存档报告均为绿**、两跑皆红=稳定存量回归（引入窗口
7/25→8/14 三周，B1-3 高嫌疑 8-13 焦点批重构），结构上与本批（navigation 接地/
memory 抽取）零交集——已立 §4.1 待办。⚠ 二跑（31/37）与全量 pytest 并行执行、
读数被负载污染（B5-1「顺路咖啡 0 个」等 4 条增量红不可定性）——「诊断动作污染
下一次跑批」的又一形态，二跑只用于确认 A3-2/B1-3 复现，增量红不采信。

**全量基线**：`python -m pytest --import-mode=importlib` **5518 passed / 14 skipped**
（净增 +18 逐条点上号：批 F `15eac3c` navigation +1，本批 test_dest_grounding 11 +
test_visited_coords 4 + memory 2）。⚠ 首跑 scripts/tests/test_e2e_stack_lease.py
12 条红=与 journeys 二跑并行的 lease 冲突假红（那些测试模拟 runner lease 树而
journeys 真持有 stack lease），隔离复跑 61 passed / 2 skipped 全绿。四条 blocking
门禁全绿（L0 strict 25/25、能力完整性、skills、范例域错配 2.4%）。

### §35 2026-08-14 深夜：journeys 两条红收口（A3-2 尺子对齐 / B1-3 确定性化 / A1-5 分级勘误）

§34 立卡的两条红当晚取证修复，三提交 `53ee48b` / `de6128f` / A1-5 降级批。
**两条的取证都推翻了 §34 的初判**（「稳定存量回归、引入窗口三周」）：

**A3-2 =两把尺子打架，不是回归**（`53ee48b`）。取证链：deep-research 侧
`_resolve_news_deepen`（NEWS_ACTIVE 桥接）机制完好；断链在 planner 落域——而
2026-08-02 泓舟已裁定「条」是列表项量词（deep_research manifest route_hints 注释
留痕：宽 hint 把「第二条详细讲讲」replace 成 research.run 是劫持；同日对抗语料
`cs.more.news` 金标 `any_of:[info.news, info.search]`、L0 门禁 CI blocking）。
即本跑落 `info.search` **按对抗尺子是对的**；7/25 journeys 的绿靠的是当时未收窄的
宽 hint，裁定后 journeys 三周未重跑，8-14 才炸出矛盾。处置：journeys 轮 2 话术改
「深入调研一下第2条新闻的来龙去脉」——带显式调研信号（hint `调研一下` 命中），
NEWS_ACTIVE 桥接端到端覆盖不丢；原句 info 域行为由对抗语料守，不需 journeys 再守。
真栈探针：research_report 卡 + 5 过程区事件，调研内容正是列表第 2 条（华是科技
收购案）。**判据：测试红了先问「是修坏了还是前提变了」——8-02 的产品裁定改变了
这句话的正确答案，第三把尺子没跟着换。**

**B1-3 =同族能力只建了一半，不是哪批弄断的**（`de6128f`）。取证链：焦点写入完好
（extract_focus 导航轮落 last_destination + 解析坐标）；engine 早已按 manifest
`context_scopes:[location]` 把 `focus_destination_*` 三键注进步骤 meta；**weather
侧有指代判定+确定性消费**（`_DESTINATION_DEICTIC_RE`，B1-2「那边天气」稳定绿），
**nearby 侧从来没接**——一直靠 planner LLM 看焦点 prompt 填 location 槽的软路径，
7/25 绿是方差绿、8-14 两跑红是方差红。§34「高嫌疑 8-13 焦点批」被证否。处置：
nearby `_near` 补第二级——显式 location 槽 > 地点指代词（那附近/那边/那儿/那里/
目的地/终点，与 info 同族）+焦点坐标 > GPS；焦点坐标 LLM 与客户端都写不到，信任
链与 info 同款。nearby 67 绿（+3：指代用焦点中心/普通「附近」不被劫持/无焦点回落
GPS）；真栈复验：福田出发导航万象天地后「那附近有停车场吗」搜出南山十家（#2 即
万象天地地下停车场），零福田污染。

**终验全量 journeys（单独跑，不并行）**：A3-2 / B1-3 双绿。新读数暴露 A1-5
（8-10 新增、直接标 regression 级）三跑 1 绿 2 红——红轮 trace 0b26b11f 实锤
`llm_raw={"addressed":true,"steps":[]}` + `no_action_unconfirmed` 重试两次仍空 +
weather-outing guide 注入在场没用 → chitchat 兜底 → escalate info.search。这是
findings §23/§24 档案化的主模型方差面（走成 toolcall 是 provider 属性 45~48%），
**达不到「回归级=100% 绿」的稳定性门槛，属分级标错不是回归**——regression→target
降级（L3 claim 链按 journey_id 引用不受影响；能力没退化：首跑绿、claim 两趟 1/1
在档）。换算后**回归级 15/15 全绿**、目标级 19/22；A2-1（中间结果传递）/B5-1
（14 轮级联，第 12 轮取消提醒依赖第 7 轮建单成功）7/25 绿过、本日三跑各偶红，
target 级方差 backlog 观察，不立卡。

## §36 2026-08-15 EVA 二轮余项 P3 簇收口：G8 路线会话 / G4 主题行程 / G9 跨城市 + 待议项

泓舟授权处理 EVA 二轮余项（§4.2 索引行），当日闭环：三个 P3 簇各立独立 RFC 并实施
合入，留档待议项（G7 双通道抢话）按默认方向收口，badcase 日报归档。提交序列：
`36a0ff2`（日报归档）/ `74e6beb`（G8）/ `d26705e`（G4）/ `63a565b`（G9）/
`03f7e33`（边界收口）+ 收尾文档批。G10 订座票务维持搁置（诚实桩，不为对标造假）；
谓词别名清洗维持条件触发未启动。

**G8 导航会话状态与增量改道**（RFC `2026-08-15-g8-navigation-route-session.md`，
唯一动编排核心的簇）：结果保留键 `_route_session`（conventions §9.1 第五行）→
`Focus.active_route`，三条纪律逐条复用门店锚定先例（粘性接力不续期 ts / prompt
只渲染名字与时限绝不渲染坐标 / 消费方按 ts 限龄 `ROUTE_SESSION_MAX_AGE_S` 2h）；
`_apply_focus_meta` 把 JSON 注给 location scope 步（LLM 与客户端写不到）；新 intent
`navigation.reroute` 四操作（删/加途经点、换策略、改目的地走 `_find_destination`
全套 R1 接地），未点名约束保持并重出 G1 时限判定；navigation 全部 **6 条** navigate
路径盖章（挂点枚举教训这次先做）。落域：exemplars navigation#33-35、台账
`navigation-trip.reroute-vs-modify`、L0 +5 reviewed（suites 579→584 第六次适用）、
catalog 143→144 条。

**G4 主题行程**（RFC `-g4-trip-theme-retrieval.md`）：`extract_theme` 三族（书名号/
「跟着X游」/「同款/取景地」）+ 主题算出行 trigger（「跟着《太平年》游杭州」此前
四信号一个不中）；`build_theme_pool` LLM 只产名字候选（≤8 宁少勿错）→「{城市}{名}」
优先高德接地 + `name_matches` 拒错名 → 通过者并入池——**池的封闭纪律不变、入池
来源多一路**；接地成功才标 `Trip.theme`。v1 刻意不做联网检索主题候选（接地校验
才是质量闸门）。suites 584→586（第七次）。

**G9 trip 跨城市**（RFC `-g9-trip-multi-city.md`）：`Trip.cities` 保口述序 +
`Day.city`；`extract_cities`（逐抓+连写拆分+BLOCK）；propose 按城列池、day 标 city
收敛到城市集（缺标按序均摊，不臆造）；ground 按城池取坐标；**solve 补跨天衔接
leg**——此前天与天之间不建驾驶段，充电编织对全程最长的跨城段是盲的、SoC 递推
断开（对单城市行程同样成立的改进）。既有 trip 测试零改动通过=单城市不变的证明。
v1 刻意不做顺路重排序（城市序=口述序）。suites 586→587（第八次）。

**待议项收口（G7 双通道抢话）**：不是新裁定——批 D（`32fcdaf`）已拍板 reminder 的
创建入口是用户对 offer 卡的肯定答复；本批把它兑现到落域层：exemplars chitchat +2、
台账 `chitchat-reminder.statement-vs-request`、L0 双向各 2 全部 reviewed 新增
（**复用既有 stable 打标签需要改 forbidden、会碰 baseline 比对面——不动 gate
案例集**），suites 587→591（第九次）。

**读数**：后端全量 **5559 passed / 14 skipped**（单独跑 13m36s）。较留档 5518 净
+41 = **基线陈旧 3**（5518 实测于 ca933f3，其后 `de6128f` B1-3 nearby 又加 3 条
没刷新——「对不上先怀疑基线陈旧」第二次应验，worktree 对比取证：改动三套件
collect 989-951=38 与本批新测试数吻合）+ 本批 **38**（G8 route_session_focus 8 +
reroute 13、G4 extract 4 + pipeline 5、G9 extract 3 + pipeline 5）。全量中台账
条数锚（`test_boundary_ledger_maps_...` 27→29）按其自述仪式红一次——「裁定加了，
兑现物加了吗」——兑现物已证（L0 strict 双向检查 exit 0）后补断言即绿，不是回归。
`check_intent_gate` 2/2 exit 0（discovery 81/81、**591 唯一输入恰好用满**；gate
25/25 不变）；能力完整性/eval_skills/route_hints 80/80/fast_intent 57/57/
exemplars（299 条、域错配 2.4%）全绿；HMI **258/258** + Vite build（types.ts 扩
`Day.city`/`theme`/`cities` 并渲染）。catalog 锚 **144 条 / 12418 字符**（三批各自
更新，测试注释逐笔点上号）。架构 **v1.25**（§7 会话状态保留键设计要点 + 附录 C）。

**四条实施发现**：① `_THEME_TAG_RE` lazy 捕获吞句首动词（「去打卡繁花同款」→
「去打卡繁花」）——lazy 从**最左起点**扩张而不是从目标词起；② `_TRIP_DEST_RE`
lookahead 缺「再/然后/接着」时「先去杭州再去苏州」第一城被吞成「杭州再去苏州」；
③ `_TRIP_MULTI_DEST_RE` 末段贪婪把「玩两天」吞进城市名——三条同族：**中文无空格
分词下，捕获组的边界只能靠 lookahead 白名单兑现，「反正 lazy 会停」是错觉**。
④ 操作层：`grep pattern && cat >> file << EOF` 的 grep 无匹配会让整条 && 链短路、
heredoc 静默不执行，而链尾独立的 `echo done` 照样打印成功——**「命令输出了 done」
不等于「中间每一步都执行了」**，本批 §36 首次落盘就这么丢过一次（写后 grep 验证
才发现）。

**真栈验证（同日，重建 cloud-planner/navigation/trip 三容器 + WS 探针两轮）**：
G8 同 session 三轮全通——「顺路买杯咖啡」→「咖啡不买了先去趟加油站」（**增量
兑现**：加油站插入途经点、目的地保持，EVA 动态重规划语料真栈首通）→「换条不走
高速的路」（策略切换且途经点保持）。G4/G9 出卡后**各抓修一处**（首轮探针的价值）：
① solve 顺延新建的天不带 city（「玩三天」顺延成 4 天第 4 天无城标）——继承前一天
city 一行修；② planner 把**整句**填进 theme 槽（话术念出嵌套书名号）——slot 值
补确定性清洗，判据：**planner 填的槽也是模型输出，slot 优先的前提是 slot 值可信，
直用前先过清洗**。复验双绿（三天城标齐/「已按《太平年》主题」）。真栈抓修批 +2
测试（trip 66 分套件核验）→ 基线 **5561**。附带观察：「万象天地」在探针位置接到
公交站 POI——接地卡 R1 家族已知形态（商场类目锚词未覆盖），不立新卡。

## §37 2026-08-15 《超级EVA指令集》全量端到端验证：25 语料真栈计分 + 七处抓修

EVA 二期（§33/§36）收口后，泓舟要求「更新栈然后端到端验一下指令集内容，看看有无
遗漏」。栈全量重建（30 容器）后按指令集逐条真栈 WS 探针（默认身份 u1；记忆链用
抽取合法前缀 `demo-eva2e-*`——`probe-*` 在 skip 表里，Part 六 用它抽取会被静默跳过）。
完整计分卡与逐条读数：
`docs/reviews/2026-08-15-eva-instruction-set-e2e-verification.md`。

**结论**：25 条语料 ✅12 / ⚠️10 / 🔒3 面（车控联动、订座票务、主动执行=红线/搁置
立场，非能力缺失）。指令集主干（时间约束/真沿途/模糊推断/主题/多城/增量改道/记忆
消费）全部有真栈读数；⚠️ 半数是已档案化余项（G1 反推窗/G5 属性维/G10），半数是
新记的整合面（报告 §3 六项遗留，未立卡）。

**七处抓修**（三提交）：
1. **跨厂商备份档 `LLM_BACKUP`**（`911b7c4`，泓舟中途指定 deepseek-v4-flash）：
   探针首轮撞上 MiniMax 上游抖动，整批「处理失败」——厂商内降级链 fast=primary
   同名（实际单档）、备档 MiMo key 已失效，一抖即整请求死。server.py 尝试链
   元组化 `(provider, aid, model)` 追加备份厂商一跳；**429 从「跳过剩余全部」改
   「跳过同厂商剩余档」**（限流是厂商级，换厂商正是对症）；pinned 恒不跨（pin=
   不许漂移）；终态按最后一次失败定性。8 条对照测试。
2. **方向词目的地拦截**（trip，`4b975bd`）：「北上追春天」被 planner 臆断
   destination=北方→「北方追春天」——搜「北方 景点」出北方车辆集团、和风把「北方」
   配到**阿拉伯语区**。全等拦截第一轮被「北方追春天」穿透——**planner 臆断槽值的
   形态比预想更野，拦截要按前缀不按全等**。修后逐字=EVA 期望行为（「想去北方玩呀
   ——具体想去哪个城市？」）。
3. **描述性历史指代→轨迹候选**（navigation，`4b975bd`）：「看夜景的那个地方」——
   planner 把原话整句填进 dest，按 D4 纪律诚实放弃「暂时无法确定」。升级：描述性
   dest（那个/地方/去过的）+轨迹名匹配不上 → 列最近去过（episodic 确定性消费、
   按名去重 ≤5）让用户挑——比放弃多一步、又不替用户拍板。真栈列出滴水湖/天安门/
   万象天地/外滩/虹桥五个真实轨迹。
4. **范例占位符被逐字抄**（`4b975bd`）：navigation#32 slots 里的
   「记忆里上次去过的那个地点」占位描述被 planner 原样抄进槽——**范例的 slots 教的
   是「填什么值」不是「这里该有个值」**，示意性文字会被当真值；slots 改空即愈
   （「范例写错只是噪声」的活例）。
5. **复合取消句吞句**（engine，收尾批）：wait_slot 挂起中「算了咖啡不买了，先去
   加点油，但还是别迟到」命中 `_SLOT_CANCEL_RE` 即整句吞掉、**4.6ms 直回「已为您
   取消」**（trace 63f6bf43）——EVA 三§3 动态重规划的入口形态正是「取消+新请求」
   复合句。修法：剥取消词/标点/语气尾后余量 ≥6 字=复合句，清挂起后按全新请求继续；
   纯取消句（「那个提醒不用了，取消吧」剥后 5 字）行为逐字不变（对照测试双向锁）。
   「验证多轮系统必须跑失败态之后再说一句」第三次应验。
6. **家人位置陈述误落 set_place**（范例）：「我女儿在深圳市南山实验小学上学」被
   反问「您想设置哪个常用地点？比如家或公司」——navigation.set_place 的
   「我家在X」例句族把家人位置陈述也吸走了，而它是**记忆抽取的输入**（关系图谱
   「接女儿」靠它）；误路由还产生 wait_slot 挂起把后续教学链整链拖死。chitchat
   范例 ×2 收口，修后「好的，记下了，孩子在南山实验小学读书呢」→ 复合句正确解析
   出学校。
7. **「找杯咖啡」被派去下单**（范例）：luckin.order 的槽校验错误「数量需要是 1 到
   20 之间的整数」原样当话术回给用户——发现≠下单（台账 nearby-luckin 族），
   nearby 范例锁发现语义；raw 校验文案直出另记遗留③（话术层）。

**亮点读数**（真栈首次全链）：六#3 粤菜链（陈述→抽取→「和老婆吃饭」出 7 家粤菜
——记忆改变结果集）；六#4 路线偏好链（「去公司」→「记得您平时不走高速」）；
一#9 六城保序 trip 卡（苏州→无锡→南京→济南→潍坊→北京，趵突泉/风筝之乡→潍坊
全接对）；三§2 全五条绿（滴水湖直达/轨迹候选/动物园族/诚实氛围重排/太平年落杭州）。

**教训**：① 探针 session 前缀先对表——`probe-*` 在抽取 skip 表里，Part 六 记忆链
用它跑会「存了但永远不抽取」，假阴性无声；② 上游抖动期的读数不可定性（「拿不到
结果先分资源没了还是这次没成」），备份档落位后才复跑；③ **D 组读数用的是旧镜像**
——cloud-planner 是 D 组跑完才重建的，D7c 的吞句恰好反证了修前行为（读数与镜像
版本要对得上号）。

基线：全量 **5574 passed / 14 skipped 零红**（14m52s 单独跑），较 5561 净 +13
逐条点上号（backup 8 / trip 方向词 1 / navigation 轨迹候选 2 / engine 复合取消 2）。
四条 blocking 门禁全绿；exemplars 302 条。

## §38 2026-08-15 EVA 验证遗留六卡收口：P1–P6 立卡处理 + 端到端终验

泓舟指示「预留 6 项立卡并处理，处理后端到端验证」。六卡文档
`docs/design/2026-08-15-eva-e2e-residual-cards.md`（逐卡缺口/方案/实施记录/终验），
验证报告 §5 增补终态计分：**✅ 17 / ⚠️ 5 / 🔒 3 面**（首轮 ✅12/⚠️10）。

**逐卡一句话**：
- **P1 个人地点作途经点**：waypoint 逐 token 三级解析（人称→关系图谱 / 别名→画像 /
  其余 POI 搜索），未知→诚实教学问。真栈「先送孩子去学校再去公司」从快速路垃圾
  变诚实引导。
- **P2 多城点名 POI 入池**：`trip.plan` 加 `must_visit` 槽——逐名接地（直搜→
  landmark 俗称解析）+按池质心归城+propose 必去 hint+**确定性补插**（「点了名的
  不许丢」与「池外不臆造」互补）。真栈大长句六点名 POI 全进行程（大秋裤→东方之门）。
- **P3 槽校验文案**：`base.parse_quantity` 共享容错（中文数字/全角/量词尾），
  边界不松话术改人话；参数化 21 测。
- **P4 散句 relation**：确定性前置抽取（两族句式，LLM 挂了照常返回）+
  `resolve_person_place` 放宽 place_of∪works_at∪lives_at（**只查 place_of 是
  「存得下用不上」第 N 例——LLM 正确抽成 works_at 也查不到**）。真栈全链：
  「我老婆平时在深圳湾万象城上班」→「去接我老婆」导航到万象城。
- **P5 负偏好降权**：串联两根因——抽取丢店名（prompt 补「店名以助手确认句为准」）
  + **消费门禁结构性漏洞**（口味召回只认正餐类目，咖啡搜索整个被排除——
  「这家咖啡太酸」恰是指令集语料却永不降权；`_TASTE_CATS` 宽集合消费、菜系偏置
  仍限正餐、recall top_k 3→5）。真栈终验三立方从推荐消失+「不合口味的已排后」。
- **P6 方差面**：范例四投（组合接送/接配偶/先后序/省略动词发现句），
  不加 hint 不动 gate；组合句真栈两连全通。

**过程抓修恶性缺陷一枚**：「我老婆平时在深圳湾万象城上班」被 planner 映射成
set_place(公司=万象城)**改写了用户本人的公司常用地点**（家人位置陈述踩写操作）——
`_set_place` 人称守卫：长词+「我妈/我爸」代词组合（单字不裸匹配防「妈湾路」地名
误伤），含人称词只口头记下**绝不写画像**，双向对照单测。

**判据沉淀**：
- **消费门禁的类目集合也是消费面**——机制、数据、话术都对，门禁集合漏一个类目
  就整族失效且无声（P5 咖啡族；与「只查 place_of」同型：P4）。查「存了为什么
  没用上」时，**谓词、类目、scope 三个过滤器逐个排**。
- 抽取是响应后异步，「等 25s 再查」有竞态——**链路正确性与时延是两件事**，
  边落库后直查 RPC 分离验证（resolve 三连中）。
- 容器重建后紧邻两句拒识=registry 重注册窗假红（第 N 次应验），隔 30s 复跑即绿。

**读数**：全量 **5613 passed / 14 skipped 零红**（20m14s 单独跑），较 5574 净
+39 逐条点上号（navigation 6 + trip 4 + bridge 21 + memory 7 + nearby 1）；
catalog 锚 12508（must_visit 槽 +90，条数 144 不变）；exemplars **306 条**门禁绿；
四条 blocking 门禁全绿；真栈终验 Z6b/Z7/W2/W3/X3/V1/V2/V4/V7 逐条在案
（scratchpad JSONL）。⚠ 首趟全量在 catalog 锚更新前启动、会假红一条——**后台跑批
与工作树改动互斥**（§4.3 live 纪律的全量版），停掉重跑才是干净读数。

### §38.1 归档：AGENTS.md §4.0 历史对账段全文移入（2026-08-15 洁癖整理）

> 以下五段自 AGENTS.md §4.0 原文移入（只进不出）：EVA 二轮五批（另见 §33/§33.1）、
> 商户链路三段（另见 §30–§32.2）、B5/B6 后分组实测与真栈演练（另见 §27/§28）。
> 段内数字均为**批次时点读数**，当前数以 AGENTS.md §4.0 基线段与证据表为准。

**2026-08-14 EVA 指令集二轮对标五批**（缺口分析获批当日实施，流水 history **§33**、
方案 `docs/design/2026-08-14-eva-round2-capability-gaps.md` + 实施计划同日档 §7）：
批 A 存量七条（nearby 评分先排后截／trip「第一站」序数／主动治理器跨 owner 不合并／
死槽位摘除／conventions §9.18）；批 B 导航五刀（`arrive_by` 时限求解+ETA 量化+出发提醒
反向环／顺路候选真沿途 45% 采样／landmark 俗称+自然地物／`route_pref`→高德 strategy
——该参数此前全仓从未使用／多途经点保序）；批 C 记忆消费面（proto `subject`/`polarity`
+ 四个确定性消费出口，修 nearby **假个性化**：口味检索前置+负偏好软降权+subject 并取）；
批 D 未来事件→询问式提醒建议（`reminder_card` offer 态，零执行权不破）；批 E 类目扩展
（动物园族）+「餐饮」默认仅饮食信号下成立+氛围软重排。catalog 锚点 11866→**11928**；
HMI node **258** + Vite build；四门禁绿。**真栈已验**（同日 `make up --build` 30 容器
+ EVA 语料 13 轮 WS 探针，读数与批 F 三修见实施计划 **§8** / history **§33.1**）：
样板轮「导航去东方之门，路上买杯咖啡，五点前要到」一轮给出时限判定+真沿途候选+
逐家 ETA+记忆不走高速叠加；「老婆喜欢粤菜」subject 链抽取→消费全通。批 F 三修=
demo 咖啡 hint 补导航语境让路词（整条计划曾被 replace 成 demo 下单）/route.* 记忆
消费撤销极性过滤（方向在谓词名里）/轨迹写入补挂 search_poi 分支（挂点枚举第三次
应验）。当日晚接手会话已收口两项：目的地接地就近包含误伤家族（R1 二期，见 §4.1 状态段）
与 G7 询问式真栈补验（抓修抽取 prompt 日期锚缺失+offer 显示时区错一天两个真 bug，
`cc7e5ba`——G7 通道真栈首次亮相「我记下了…要到时候提前提醒你吗？」）。

**2026-08-13/14 商户链路收口聚焦实测**（demo-3ukshz 二轮 + 打磨 + searchType
勘误，逐批流水 history **§32/§32.1/§32.2**、方案与勘误 设计文档 §5/§5.1）：
最终态一句话——瑞幸：聚合菜单主卡（多种子 ≤12 款、「在售不止这些」口径）、
预览规格 chips（只上 `_SPEC_GROUPS` 四族）、改规格真栈生效（冰→热）、换店/取消；
麦当劳：**「附近的麦当劳」真栈答出高新中五道餐厅**（109 款、8 分类导航）——
「仅碧海君庭」曾是错误结论，真因是 `searchType` 写死 1=**搜索收藏餐厅**（勘误
§32.2；判据：**「接口返回了数据」≠「接口在按你以为的语义工作」**）；
`_refused` 拒绝句由聚合器确定性附加（不进 LLM，恰好一次）；焦点 `last_places`
扩第四个安全标量 `city`。聚焦回归 cloud+bridge+nearby **1261 绿**、
HMI **258/258**+Vite build、四门禁全绿（catalog **11866**）。

**2026-08-13 demo-mkemhn 六批聚焦实测**：mcp-bridge **404 passed**（+8）、
`orchestrator/cloud` **786**（含锚定门控/限龄/线契约归一/位移防御新守卫）、
nearby **56**（+4）、HMI node **257/257**（+3）且 Vite build 通过；四条 blocking
门禁全绿（L0 strict discovery 618 条/579 唯一输入恰好用满、gate 25/25；范例域错配
2.4% 持平）；真栈抽验 4/4——无位置不再报异地门店、纯发现句诚实降级、
「选择瑞幸门店：X」一轮直达菜单、营养查询给最接近条目。

**2026-08-13 商户 badcase 批聚焦实测**：mcp-bridge **396 passed**、
`orchestrator/cloud` 焦点/执行器新增回归 6 条、HMI node **254/254** 且 Vite build 通过；
CDP **C10**（只读选品卡，不创建订单）端到端通过——选品卡 3 款 / 商品图 2/3 张
**真加载**（`naturalWidth>0`，不是断言 `src` 存在）/ 帧文本正确且非确认帧 /
**选品后换话题仍正常**。C1/C3/C5/C6 全绿（首轮红、复跑即绿 = registry 重注册期假红）。
⚠ C2a/C10 会被高德间歇不可达阻塞——本机到 `restapi.amap.com` IPv4 超时、IPv6 无路由，
百度 90ms 正常，**不是配额也不是代码**；用例把这种情况报成「前置降级，非卡片结论」。

（2026-08-12 商户批的逐项读数已归档 history **§29/§30**，不再在本节留对账段。）

**2026-08-11 分组实测**（B5/B6 后）：edge **579**、cloud **721**、registry **65**、
agents **993**、`runtime/tests` **109**、observability **73**；端侧 smoke **13/13**；
L0 门禁 **2/2 exit 0**（discovery 81/81、gate strict 25/25）；能力门禁 exit 0；
`eval_fast_intent` 57/57、`eval_route_hints` 78/78、skills / 范例门禁 PASS
（范例域错配 4/160=2.5%）；新增离线回放 `test/eval_actionability.py`
（零 LLM、零网络，**不进 CI blocking——它是取证脚本不是准入闸**）。
**Go 侧与前端数字未随本批重测**：Go `go build ./...` + `go vet` +
`go test ./gateway/deployprofile` 全绿、HMI `node --test` **225/225**、
Dashboard vitest **17/17** 分别是 B3/B4（2026-08-11）与 2026-08-09 实测——
B5/B6 只动 Python 侧（`orchestrator/cloud/` 与 `observability/collector/db.py`）。

**真栈演练（2026-08-11，`make up` 全量重建 30 容器）**：`e2e_ws` / `e2e_obs` /
`e2e_context` / `e2e_process_region` / `e2e_proactive` / `e2e_ledger` / `e2e_scene`
**全 PASS**；`turns` 表实测 `plan_mode` 三档齐全（`toolcall` / `toolcall_salvage` /
`toolcall_degraded`）、`actionability` 列真实写入（`execute|0.95` 等）——B6 §5 第 1 条
「shadow 进 obs 且可检索」由真栈而非单测坐实；既有 302 turns + 2377 spans 在
加法式迁移后完整保留。⚠ **`--lane ci --full` 只选 1 个用例**（`e2e_protocol_smoke`），
引用它的 exit 0 时别当成 e2e 全绿。`e2e_verify` 5 条红已逐条定性为**前提失效**（见 §4.2）。
**本次演练抓到一个 B3 埋的真缺陷**：collector 与 proactive 的 Dockerfile 没
`COPY runtime`，加闸后**一重建就起不来**（见 §4.3 同名条目）。

## §39 2026-08-15 EVA 余项立卡 E1–E5：§4.2 除 G10 外全部处理

泓舟指示「从 §4.2 EVA 余项接手，除 G10 维持搁置，其余立卡进行处理」。卡文档
`docs/design/2026-08-15-eva-backlog-cards-e1-e5.md`（逐卡缺口/方案/实施/读数）。
架构 **v1.26**（§7.1 增两条记忆消费面要点）。

**逐卡一句话**：
- **E1 事件时刻反推用餐窗**（G1 余项）：`agents/_sdk/timewindow.py` 作时刻解析的**唯一
  实现**（navigation `_parse_arrive_by` 原样迁入，对外名字不变），新增 `parse_event_time`
  （只认「时刻+事件词」，与「X 点前到」刻意互斥）与 `dining_window`（离席=事件−路上预留、
  入座=离席−用餐时长）。nearby 消费：话术给窗口 + 按**入座时刻**筛营业中（用真实
  `opentime_today`，不是近似）+ `data.dining_window`。**路上预留是明说的假设、必须念出来**；
  来不及就说来不及，不压缩窗口凑数。
- **E2 停车便利/无障碍/不排队**（G5 余项）：原话显式 + **记忆驱动**两路触发；停车便利度=
  前 K=4 家各查一次周边停车场（300m 内计数，串行有界）软重排；无障碍/排队**没有数据就说
  没有**。`data.access` 落结构化。
- **E3 归城校正 + 主题接地可观测**：`correct_stop_cities` 按坐标把排错城那天的 stop 搬回
  归属城首日（双阈值 1.5×/30km 保守，单城零影响）；`build_theme_pool` 改返回
  `(pool, stats)`，`data.theme_grounding` 给命中率读数。
- **E4 provider 方差面**：新增只读探针 `scripts/probe_plan_variance.py`（三句形×R 轮真栈 WS
  + obs 取 `plan_mode`），**不进 CI、不当准入闸**；维持不加 hint、不动 gate 案例集。
- **E5 谓词别名老账**：`_PRED_CANON` 扩 7 canonical/18 别名（全部来自真栈实测谓词）+ 抽取
  prompt 钉死口味族命名空间 + nearby 口味召回改**两路并集** + `scripts/memory_predicate_cleanup.py`
  （dry-run 默认，`--apply`/`--supersede-dups` 分别授权）。

**E5 的取证是本批最有价值的一段**：§4.2 写的启动条件是「出现消费方误伤再启动数据清洗」——
实测 u1 库里 `place.avoid`「以后不要推荐三立方…咖啡太酸」/ `poi.dislike` / `restaurant.no_queue`
「老婆不喜欢排队」三条 **scope 明明是 `profile.taste`**，只因谓词不带 `taste.` 前缀，而
`pg_store._score` 里 scope 与谓词前缀是 **AND**，就永远进不了口味消费面。**P5 那次降权能
兑现，只是因为恰好另有一条 `taste.coffee` 也带了店名——同一件事的另外两条证据一直是死的。**
真栈证据：「晚上和老婆找个地方吃饭」→「地图没有实时排队数据，这条我按不上」
（`restaurant.no_queue` 第一次被读到）。泓舟授权**只做①归一不做②去重**，理由取自 dry-run
自己的输出：②会把「用户有一只叫 Cookie 的宠物（宝宝），喜欢睡在窗台旁」这类**信息更全的
旧条目**标成被取代——**那些行不是重复，是同谓词的不同事实**。`--apply` 已执行 18 行。

**E3 连修四轮才让校正够得着**（每轮只暴露一个障碍，上一个不修就看不见下一个）：
① 点名 POI 混进城市序（planner 把「大秋裤→东方之门」同时填进 `destination` 与 `must_visit`，
「东方之门」成了一座城、第 2–5 天全标它）→ `_drop_named_pois_from_cities`，判据是**归一后
精确相等**不是包含（「苏州园林」不等于「苏州」）；② 6 城只排 3 天、四座城一天都没分到 →
`_days_for_cities`（多城且用户没说天数 → 天数取城数；⚠ 判「说没说」不能用 `_norm_days`，
它把非数字剥掉、「三天」会被读成 0，那就成了替用户改需求）；③ `solve` 日上限顺延
`insert(0)` 把无锡那天溢出的三个点塞进**南京那天** → 下一天是别城就原地插一天、城市跟着走
（单城 city 全空、判据短路，行为逐字照旧）；④ 末轮五城全部归位，唯「潍坊风筝博物馆」仍在
济南那天——**日志实锤 `pool search '潍坊 景点'/'潍坊 美食' failed: CUQPS_HAS_EXCEEDED_THE_LIMIT`
⇒ 该城没有质心 ⇒ 不参与归城判定**，是本机制的已知边界不是静默失败。

**三处过程抓修**（都不是单测能抓到的，全靠真栈/取证）：
1. **假个性化第二形态**：E2 首版触发条件是「话里提到了人」，于是「和老婆吃饭」命中了
   「父母腿脚不便」——系统声称考虑了一件根本不适用的事。修法=记忆必须**确实关于话里那个人**
   （subject 命中或称谓同义词命中），泛指「老人/长辈」才放宽；对照断言双向锁。
2. **探针自己抽掉了一个前提**：E4 首跑漏传位置 meta，D2a 5/5 全是问句、读起来像「过度澄清
   100%」，实际是 nearby 的**位置缺席诚实降级**。补位置后 5/5 直接出列表。
   「测试替被测系统提供前提」的**反面**同样成立——抽掉前提也是没在测要测的东西。
3. **话术把类目别名当检索词**：planner 把「吃饭」填进 keyword 时它被拿去问高德、话术念成
   「为您找到 10 家**吃饭**」。剥壳后若正好是类目别名就换 canonical 词（吃饭/餐厅→美食）。

**顺带被既有门禁抓了一次**（正面例子）：清洗脚本的 docstring 里写了两处
`docker exec car-agent-memory-1 …`，全量里 `scripts/tests/test_e2e_container_names.py`
当场报红——容器名的 project 段派生自启动目录（本地 `car-agent`、CI checkout
`cockpit-agent`），**连注释里的跑法也算数**。改成按 compose service 名寻址
（`docker compose -f compose.yaml exec -T memory …`）并真跑通验证。
这条守卫是 nightly #33 那笔账留下的，2026-08-15 又替新脚本挡了同一个坑。

**读数**：E4 首跑（MiniMax-M3，每句形 5 轮）R7b 5/5 有动作+卡片、D2a 5/5 出卡片、R8b 5/5 解析
出「深圳湾万象城」，**15/15 走成 toolcall、一次方差都没复现**；同日 E1 探针另见 1 次
`toolcall_degraded` 落 chitchat ⇒ 方差是真的但本批分母下没抓到，维持档案化不加码。
E1 真栈 4/4（含复跑三连）、E2 真栈 3/3、E3 六城长句四轮取证 + 主题池读数
`theme pool《太平年》: 6/7 candidates grounded`（唯一漏掉的是高德限流不是主题知识缺——
这个读数的用处正是一眼分开「正常降级」「链坏了」「被限流」三种长得一样的结果）。
全量 **5659 passed / 14 skipped 零红**（20m36s）。
范例 `skills/exemplars/nearby.yaml` +1（**307 条 / 22 域**，域错配率 2.4% 不涨）。

## §40 2026-08-15 EVA 指令集双档复跑：时区族系统缺陷 + 三处修复

泓舟指示「再真栈跑一下超级EVA指令集」，并加跑 `deepseek-v4-flash` 作对照。
25 条语料 × 两档各 27 轮，报告
`docs/reviews/2026-08-15-eva-dual-provider-rerun-and-fixes.md`。跑完已切回主模型。

**对照档立刻兑现了它的用途**：同一条语料两档相反=模型方差，两档逐字同错=系统缺陷。
一#1（MiniMax 导航到济南同名小学 2004km / DeepSeek 落对）、一#2、一#9、三§2#5 是方差；
三§3 方向相反（MiniMax 对、DeepSeek 错）；而**时限 ETA 与「不要太辣」两档逐字同构地错**
——系统缺陷就此定位。**「两档是否同时错」可以跨 provider 比，通过率不能**。

**缺陷 1：容器 TZ=UTC，所有「几点」判定整体偏 8 小时**（最严重）。真栈原文：
「预计 **05:17** 到达，比您要求的 17:00 早约 **703 分钟**」。本仓**早就写下过这条规则**
（scene ×2 与 memory/server 三处注释，后者还写着「宿主机 UTC+8 跑单测永远不红」），
G1 时间约束族仍复发——**同一件事有三份各自正确的实现，就迟早会有第四份是错的**。
影响 8 处：timewindow / navigation `_fmt_clock` / cloud context 焦点渲染 / road_safety
夜间窗 / **proactive 免打扰时段** / memory routine / trip 起始日 / 股票交易日。
修法=新增 `runtime/clock.py` 作唯一实现，8 处迁入，另 6 处各自定义的 UTC+8 常量收敛为
import；配两条源码级守卫（禁裸 localtime/mktime/datetime.now()、禁第二份时区定义）。

⚠ **守卫自己出过两次错，都是反向验证抓的**：① 首版没剥注释，**误伤了记录这条规则的
注释本身**；② 改成 tokenize 后用**空格拼接** token，`time.localtime(` 变成
`time . localtime (`，**注入缺陷时纹丝不动**。「注入会红 + 对照仍绿」两头都做，
才发现第二个——**恒绿的断言比没有断言更糟**。

**缺陷 2：当轮明说的忌口被记忆偏好压过**。「不要太辣」两档都推川菜：`_NO_SPICY_RE`
连「不要太辣」都不匹配（旧正则要求「不…吃/沾辣」），匹配了也不挡 `like_cuisine` 偏置。
这是**假个性化的第三种形态**——不是「声称参考却没参考」，也不是「把别人的偏好套上来」，
而是**记忆压过用户当轮明说的约束**。判据：记忆是背景、这句话是前景，冲突时前景赢并说出来。

**缺陷 3：画像 `place.company` 是脏的**——活跃值「深圳湾万象城桔子水晶酒店」，
写入时刻 2026-08-15 10:09:28，正是 P4 那枚恶性缺陷（老婆工作地被写成本人公司）。
**守卫防住新增、存量没清**，DeepSeek 走别名路径直接命中。已反转那次 supersede（不删行）。

**顺带修同族更大一个洞：约束词被当检索词**。「不辣」→ 搜出一串「**辣**可可·现炒黄牛肉」、
「适合带老人」→ 搜出**家政公司**、「晚饭」→ 一串赛百味。修法=餐饮检索词取正面白名单
（菜系/菜品/食材字眼），认不出退回干净类目词。⚠ 首版只补了 `keyword` 分支，
`cuisine` 是**更早的早退分支**、守卫够不着，复验时一字不差复现——**改完必须复跑
同一条真栈探针**。另修 `_ACCESS_RE` 允许插入语（「停车最好方便一点」此前不触发）。

**修后真栈复验**：D1「预计 13:39 到达，比您要求的 17:00 早约 **201** 分钟」（正确）；
B3「19:00的电影——建议17:30入座…为您找到 10 家**美食**（…已按周边停车便利度排序；
不合口味的已排后）」；E6「10 家美食…按周边停车便利度排了个序」；E4 公司地点复位。

**留下不修（已定性）**：复合句里的人称接送解析失效（「接爸妈去吃饭」两档都红，
`_person_destination` 的「剩不剩实质内容」判据用在整句上天然失效）——存量，另立卡；
provider 方差面维持档案化不加 hint；关系图谱两条方向反了的边无消费方受害，记账不修。

## §40.1 2026-08-15 洁癖整理：AGENTS §4.0/§4.1 完成态归档索引

本次整理把 `AGENTS.md` 两处**已完成批次的叙述**收走（那两节的约定是「只留状态、
不留流水」，而它们各自累积了 60–90 行批次记叙）。**没有复制一份新流水**——下列每段
在 history 里本来就有权威版本，这里只留索引，方便按主题回查：

| 被收走的段 | 权威流水 | 设计/评审文档 |
|---|---|---|
| §4.0 EVA 二轮余项 P3 簇收口 | **§36** | `design/2026-08-15-g8-navigation-route-session.md` / `-g4-trip-theme-retrieval.md` / `-g9-trip-multi-city.md` |
| §4.0 指令集全量端到端验证 + 遗留六卡 | **§37 / §38** | `reviews/2026-08-15-eva-instruction-set-e2e-verification.md`、`design/2026-08-15-eva-e2e-residual-cards.md` |
| §4.0 EVA 余项立卡 E1–E5 | **§39** | `design/2026-08-15-eva-backlog-cards-e1-e5.md` |
| §4.0 双档复跑 + 三处修复 | **§40** | `reviews/2026-08-15-eva-dual-provider-rerun-and-fixes.md` |
| §4.1 journeys 两条红收口 | **§35** | `test/journeys/*.yaml` 注释 |
| §4.1 目的地接地 R1 二期 | **§34** | `design/2026-08-14-dest-grounding-containment-card.md` §7 |
| §4.1 EVA 指令集二轮对标六批 | **§33 / §33.1** | `design/2026-08-14-eva-round2-capability-gaps.md`、`-implementation.md` §7/§8 |
| §4.1 支付基础设施真实化四批 | **§28** | `design/2026-08-11-payment-infrastructure-and-merchant-mcp.md` §2/§6 |
| §4.1 商户 badcase 收口七批 + 两轮 HMI 复盘 | **§30 / §31–§32.2** | `design/2026-08-13-cross-turn-store-anchor.md`、`-demo-mkemhn-merchant-hmi-hardening.md` §5/§5.1 |

**刻意留在 §4.1 的**（它们不是流水，是仍然成立的约束或仍要动手的事）：
复合句人称接送解析立卡（新，见 `design/2026-08-15-person-pickup-resolution-card.md`）、
支付余项两条 + 商户量产边界（服务级全局凭证 / `PAYMENT_EXTERNAL_PAY_HOSTS` fail-closed /
不执行最终付款）、外部评审 B1–B6 的契约面六条与两条 ⚠ 纪律、对抗语料余量 0。

判据（与 §38.1 同）：**归档是把叙述换成索引，不是再抄一遍**。§4.0/§4.1 里凡是能用
「一句状态 + 一个 history 号」表达的，就不该占接手者的第一屏。

### §40.2 修复后终态计分（同日重跑）

§2/§3 修复合入并重建容器后整套 25 条重跑：**✅18 / ⚠️7 / 🔒3 面**（报告 §6 有逐条表）。

**⚠ 不能与六卡批的 ✅17/⚠️5 直接相减**——两轮是不同时点的独立采样，差额大头是已知方差面
翻面（一#1/三§3 的人称解析在 **DeepSeek 同轮是对的**；一#4/三§2#5 的主题标注本轮
`build_theme_pool` 零候选 ⇒ 诚实降级普通行程，降级本身是设计）。**单批读数只排优先级，
不作定性**——这条纪律这次是对自己用的：修完想报一个更好看的数，先得问它是不是同一把尺子。

**能确定的结构性变化三条**（双档或修前后各验过一次）：
① 三§1#3 ⚠️→✅——「19:00的电影——建议17:30入座、18:30前吃完，预留30分钟路上时间。
为您找到 10 家美食（**您说了不要辣，这次就不按平时爱吃的川菜找了**；您提到出行不太方便
——地图没有无障碍/台阶数据，已按周边停车便利度排序；不合口味的已排后）」；
② 六#6 ⚠️→✅（无障碍记忆驱动 + 停车便利度排序 + 检索词不再是「晚饭」）；
③ 时间族全线正确——三§1#1「预计 14:26 到达，比您要求的 **18:00** 早约 213 分钟」，
注意「6点前」现在解析成 18:00 而不是 06:00。

**仍 ⚠️ 的七条逐条有归属，无新缺陷**：一#1/三§3 → 人称接送立卡；一#4/三§2#5 → 主题标注
方差（E3 已把命中率做成 `data.theme_grounding` 读数）；一#5 → 人称卡 + 车控 🔒；
六#1 → 车控 🔒；一#9 → **同一个已知边界**，日志实锤 `pool search '南京 景点'/'南京 美食'
failed: CUQPS_HAS_EXCEEDED_THE_LIMIT` ⇒ 南京无池质心 ⇒ 长江大桥不参与归城判定
（上一轮轮到潍坊、这轮轮到南京——**是高德限流轮着来，不是判据不稳**）。

## §41 2026-08-15 探索式真实用户 QA 轮：根因立卡 Q1–Q13 + 阶段 0/1

上游是一份**独立于 EVA 四批**的黑盒/灰盒探索测试报告
（`reviews/2026-08-15-exploratory-real-user-qa-deepseek-minimax.md`；533 turns ×
5 persona × 两档，58 个去重问题 P0×3 / P1×44 / P2×10）。它验的东西也和 EVA 不同：
**EVA 验「能力面有没有那个维度」，这轮验「会话状态、归属、审计与真实性」**——
所以它抓到的三个 P0 与 §33–§40 的读数不矛盾，两者测的不是同一个面。

方案与全部读数在 `design/2026-08-15-qa-exploratory-root-cause-cards.md`，
本节只记流水与不在那份里的过程事实。

### §41.1 分类：58 症状 → 13 个根因

12 张卡（Q1–Q12）按根因分，**不按症状分**——报告按 T0/T1/T2 分档会把同一个根因拆到
三个档里。四个根因吃掉 34 个问题：焦点与候选集每轮重建只存名字（13）、会话状态单槽
无绑定（5）、能力缺席→就近误执行（6）、安全与真实性只有 prompt 没有护栏（8）。
**Q13 是阶段 0.2 的探针翻出来的，不在报告的 58 条里**（见 §41.3）。

**四处与报告定性不同的重判**，每处都改变了「该修什么」：

| # | 报告说 | 实测 |
|---|---|---|
| 1 | I-044/I-028 是幻觉 | **是真实存量记忆**。`person.child`「用户的女儿在深圳市南山实验小学上学」、`taste.dislike_place`「以后不要推荐三立方(南山创维店)」逐字在 u1 库。真病在下一层：`memory_relation` **20 行 / `superseded_by` 全 0 / 4 条 `X --family--> X` 自环 / 同一个孩子三个学校 / 主宾颠倒** |
| 2 | I-036 是 LLM 用常识覆盖手册 | **是忠实转述了一份 mock 手册**（`manual_rag/src/providers/mock.py:6` 逐字写着 2.4–2.5 bar，source「第3章·轮胎保养」）。且 manual-rag / road-safety / chitchat 三个「答案本身就是内容」的 Agent `_prov` 覆盖为 **0** |
| 3 | I-057 可观测丢了实际 provider | **没丢**（`llm-gateway/server.py:270` 已按实际 serving 厂商落 obs），只剩 HMI 展示 ⇒ 降 P3 |
| 4 | I-021 确认前创建了真实订单 | **不成立**，见 §41.2 |

### §41.2 阶段 0.1：I-021 的三重证据

| 证据面 | 读数 |
|---|---|
| `task_ledger` | 订单 `1030837030000753499156095268` 属 session **`demo-7acjk0`**、created_at **2026-08-12 06:01:39Z（北京 14:01）**；`created_at > '2026-08-14'` 全表**只有一条**（`demo-s680o9` 的瑞幸单，北京 08-15 09:24） |
| `obs.turns` | QA 八个 session 全在 **08-15 15:40–17:42**；`demo-7acjk0` 只有 3 轮、在 **08-12**。ledger 的 UTC 戳与 obs 逐秒吻合，换算交叉验证过 |
| `mcp-bridge` 日志 | 覆盖北京 10:07–17:34（含 QA 窗），**零 `create-order` 调用** |

⇒ **QA 轮没有创建过任何真实订单**。I-021 = I-026（查单不绑当前 session/draft），
整项移出 T0，报告 §0 那句「麦当劳链路发生一次确认前创建真实订单」不成立。

**元教训**：**「查到了一个真实副作用」不等于「这次操作产生了它」**。查单/查提醒/查记忆
一旦不绑会话，就会把**历史**副作用搬进当前上下文，与「刚刚发生」无法区分——
本轮至少 5 个问题（I-021/026/044/028/045）是这同一个形态。

### §41.3 阶段 0.2：迷你集与它翻出来的四件事

`scripts/probe_qa_regression.py`（31 例 / 52 轮 / 6 组 + `--mapping` + `--repeat`）。
**诚实分车道**：Q4 位置闸与 Q3 并发归属在客户端 JS 里，WS 探针从闸后面进来，
跑多少轮都是假绿 ⇒ 必须 `test/hmi_cdp/`；`user_id` 客户端也设不了（网关匿名回落
进程默认），XS 组只能测同 user 跨 session。

- **Q13（新卡，报告里没有）**：`classify()` 与 `_to_legacy_name()` 两个分类出口
  **15/38 处不一致、7 处 `is_local` 相反**。后果——媒体域单句一直在绕云
  （`music.pause` 不在 `LOCAL_INTENTS`）、「下一首」单句本地复合句上云、
  双闪与前后除雾在复合句里必然丢。⚠ **`fast_intent.py:1788` 2026-08-04 就写过
  「发现根因时要问同一形态还有几处」，当时只修了 aircon 一处**——今天的答案是 15 处。
  Q13 是 Q7/Q8 的上游，排在阶段 3 最前面。
- **N4**：安全对话第三轮「慢一点开可以吗」被执行成 `volume.dec`（复验另见一次
  `wiper.speed.inc`）。⚠ 归因在阶段 1 被实测纠正：端侧 `classify` 返回 **None**，
  **不是端侧劫持**，是云侧 planner 就近挑工具（Q8 形态）。
- **N5**：I-011 的真根因不是「失败清空候选」——那次重搜**没失败**，泛化兜底搜出
  10 家「美食」**合法覆盖**了川菜候选。修法与报告写的完全不同。
- **N7**：「胎压补到多少」被端侧「暂不支持哦」拦截 ⇒ I-036 的方差一半来自端云分流。

### §41.4 阶段 0.4：存量清洗 dry-run（泓舟已授权，`--apply` 排在入口闸之后）

`scripts/qa_data_hygiene.py` 四族逐族独立授权，dry-run **10 条**：自环 4 / 主宾颠倒 1 /
单值谓词冲突 2 / `fire_at<=0` 仍 pending 3。

**④ 那三条闭合了 I-056 的整条因果链**：「妈妈住杭州」「停车位在B2」**根本不是提醒**，
是陈述句被 `reminder.create` 吃掉；`fire_at=0` 让它们永远 pending 且按升序**永远置顶**
——报告写的「返回妈妈住杭州、停车位B2、最便宜瑞幸价格等**其他会话提醒**」逐字就是这三条，
不是跨会话泄漏，是三条永远不会触发的伪提醒。

⚠ **dry-run 当场劝退一条判据**（E5 那条的第二例）：③ 首版把 `爸妈--family-->爸爸`
和 `爸妈--family-->妈妈` 判成冲突，**并准备把「妈妈」标成失效——直接丢掉一个真实的人**。
修法=③ 只对**单值语义**谓词生效（`works_at`/`lives_at`/`place_of`）。
判据：**「同一个 subject 有多个 object」不是冲突，除非那个谓词本身是单值的。**

### §41.5 阶段 1（T0 安全与不可逆）：五项落地

全程 TDD 先红再绿。① manual-rag 零命中短路（**一次 LLM 都不调**）+ `Chunk.source_type` +
非真实手册来源换 prompt；② 安全信号分级 + **无权威手册时不进 LLM**；
③ `Focus.safety_alert` + 保留键 `_safety_alert`（契约 `conventions.md` §9.1）+ 粘性接力 +
限龄 + 渲染最前 + **广播给所有步**；④ road-safety 新增 **`safety.driver_state`** 能力
（疲劳/饮酒/不适，确定性零 LLM）；⑤ chitchat 兜底同款闸（**`handle` 与 `handle_stream`
两条路径都挂**）+ `parking.pay` 缺 plate/order/amount fail-closed。

**真栈 `--repeat 3`**：SF1 **3/3** / SF2 **3/3** / SF4 **0/3→3/3** / SF5 **3/3** /
SF3 0–1/3（方差带）。**SF3 剩余失败全是澄清轮，原始危险症状（`volume.dec` /
`wiper.speed.inc` / 答天气）一次都没复现**——归落域方差（Q13/Q8），不是 Q9 护栏没生效。

**过程中的三件事**（都不在原计划里）：

1. **我自己引入了一个缺陷，真栈当场抓到**：`driver_state(text) or "fatigue"` 让
   「慢一点开可以吗」被答成「您现在的状态不适合继续开——**困倦时**的反应时间和酒后接近」。
   用户从头到尾没说自己困——**系统声称了一件用户根本没说的事**，与 nearby 那几例
   假个性化同族。判据：**认不出就返回空，绝不回落到某一档**；配具名回归断言。
2. **「改实现不等于加能力」**：疲劳判据先落了地，但没有 manifest 声明时 planner
   **根本路由不过来**，「困到睁不开眼」三次取样落到闲聊/拒识/音量。补 `safety.driver_state`
   后 SF4 才 0/3→3/3。连带三处留痕按惯例做完（语料 591→**596**、catalog 锚 144→**145**、
   字符锚 12508→**12615**），门禁还当场抓到我编的一个不存在的意图名 `media.volume_dec`。
3. **判据收敛 + 误杀面**：manual-rag 一份、road-safety 一份、chitchat 要第三份时立刻提取
   `agents/_sdk/safety_signal.py`（§4.3 时区族那笔账的**预防版**）。接上 chitchat 后
   马上发现原判据用 `"灯亮"` 通配——**「大灯亮了」「氛围灯亮着好看」会被答成「请降低车速、
   就近检查处理」**，因为 chitchat 兜底看到的是**全部**流量。改成逐个列举具名警示灯 +
   告警现象动词；`test_safety_signal.py` 里**不该命中的用例占一半**。

### §41.6 两个跑批操作坑（都是自伤，写下来免得再犯）

- **我在全量跑批期间并行做了 docker build 和真栈探针**——正是 §4.3 记着会造假红的条件。
  那轮跑了 30+ 分钟（基线 16m34s）仍未结束，读数作废、停掉重跑。
  **「全量要单独跑」这条对自己也算数。**
- **话术层断言写窄了三次**（首跑 5 条假绿 + SF3 复验 1 条）。同一条纪律在同一天里
  自伤两次：按报告原文写关键词排除，模型换个说法就绕过（AU2 三次取样三种措辞）。
  最终定式：**话术层只能用形态判据**（有无动作 / 是否问句 / 是否逐字重复上一轮 / 动作名），
  为此给探针加了 `differs_from_turn` 原语。另配一条：**单次取样不能当基线**——
  SF4 单次 PASS、`--repeat 3` 实测 0/3，差一点把稳定红降级成方差面。

## §42 2026-08-16 探索式 QA 阶段 2（T1 共享状态机）：Q1 A→B→C + Q3 + Q4

接手入口与卡：`AGENTS.md` §4.1 ①、
[`docs/design/2026-08-15-qa-exploratory-root-cause-cards.md`](design/2026-08-15-qa-exploratory-root-cause-cards.md)
（§4「阶段 2 实施记录」是逐项落点与断言表，本节只留流水与判据）。
契约登记 `docs/conventions.md` **§9.19**（挂起寻址）/ **§9.20**（WS 帧归属）。

### §42.1 Q1-A 取消判定收敛（I-046）

`wait_confirm` 走「词占据整句」（`len(t)<=len(k)+3`）、`wait_slot` 走子串+复合余量
——**同一件事的两条分支，§37 那批只修了后者**。于是「取消刚才解锁」6 字判不出取消，
挂起一直活着（用户报告：第三次单独说「取消」才清除）。

收敛到 `orchestrator/cloud/pending_cancel.py`，判定移到两条分支**分岔之前**。

> **判据（新）**：**「收敛」不是把一条抄给另一条，是把两条的并集写成一份。**
> 直接让 `wait_confirm` 复用 `wait_slot` 那套会换一个洞——`不订/不付/先不/不了`
> 只在前者词表里。按歧义度分 STRONG（子串安全）/ WEAK（只在占据整句时算）两层：
> 那条整句规则**不是删掉，是作用域收窄到它真正该防的那半**。

### §42.2 Q1-B 寻址键 + Q1-C 挂起表（I-013/I-051/I-037①）

顺序按卡走，没有换：B 是 C 的寻址键，先做 C 会得到「有三个槽但仍然靠猜」的状态机。

- B：proto 两个字段 + `SessionState.operation_id` + `load(operation_id=…)` 对不上返回
  None + engine 诚实拒绝（**既不执行也不清活挂起**）+ 网关/HMI 双向透传。
- C：`_PENDING_CAPACITY=3`、`save_pending()` 回传淘汰项（**淘汰要有话术**）、
  `clear(operation_id=…)` 只清一条、`expires_at` **逐条**限龄。
  HMI 侧 `pendingOps.mjs` 台账 + 确认条按 id 渲染 ⇒ **可同屏多条**。

> **判据（新）**：`closed_operation_ids` 是被逼出来的第三个字段。HMI 拿到一条普通
> final 无从判断「刚才那条挂起是不是被这轮消费掉了」，猜错就是一条已作废的确认条
> 继续挂在屏幕上等人点。**服务端知道，那就让服务端说**——同「系统持有的事实
> 绝不让 LLM 答」的形态，只是这次的消费方是前端不是模型。

> **判据（新）**：多条挂起共用一个 Redis key 时，**TTL 只挂在 key 上 = 再存一条就给
> 旧条续命**，「挂起窗口以首次挂起时刻起算」那条纪律会被无声架空。截止时刻必须逐条存。

### §42.3 Q3 响应归属 / Q4 位置闸（都在客户端 JS 里，只能走 CDP 车道）

- Q3：`request_id` 随帧上行、网关盖在**每一帧**上；`requestRouting.mjs` 登记簿
  （**带了 id 却对不上 = 丢帧，不回落 FIFO**）；看门狗 `Map` 每轮一只；
  网关抢占旧轮时**点名**回 `cancelled`（此前无声消失 + 单槽看门狗刚被清 = 永久转圈）。
- Q4：`location.mjs` 拆 ORIGIN（要出发地，显式目的地不豁免）/ ANCHOR（有显式地点即豁免）
  两族 + 否定邻接判据（≤4 字间隔，「别忘了提醒我到公司充电站补电」不误伤）；
  裸 `充电`/`续航` 移出词表。**刻意不加第二份「车控对象白名单」**——B4 那条判据。

### §42.4 读数

- 后端全量 `python -m pytest --import-mode=importlib`：**5739 passed / 14 skipped 零红**
  （26m34s；较阶段 1 的 5706 净 **+33**，逐条点号：`test_pending_cancel.py` 11 +
  `test_pending_operation_id.py` 7 + `test_pending_table.py` 12 +
  `test_engine_confirm.py` +3。`orchestrator/cloud` 套件 811→**844** 独立实测对得上）。
  > ⚠ 本节初稿写的是「5745 / +39」——**那是算出来的不是测出来的，而且算错了**。
  > §4.3 有「净增量要跟同一个 SHA 比」，但漏了更前面的一句：**基线数只能来自一次
  > 真实跑批**。写一个还没测到的数进文档，下一个人没有任何办法看出它是预测。
- HMI 单测 `node --test src/*.test.mjs`：**279 passed**（新增 `pendingOps` 8 +
  `requestRouting` 8 + `location` 5）。
- Go：`go build ./gateway/... && go vet && go test ./gateway/...` 四包全 ok。
- QA 迷你集 confirm 组 `--repeat 3`：**18/18**（CF1 red→3/3、CF5 red→3/3、
  CF6 新增拒绝路径 3/3）。
- CDP `C11/C12/C13` × 3 轮：**9/9**。

### §42.5 两处自伤（都当场抓到）

1. **尺子写错**：Q4 首版加了一条「周末去杭州两天 → 需要定位」的断言，实测 false——
   **收窄前也是 false**（旧词表同样不含裸「去」）。那是我凭空加的一条**扩大**闸的要求，
   与 Q4 的方向相反。改尺子并在用例里留痕（§4.3「尺子写错必须改」）。
2. **探针不可靠会伪装成用例失败**：CDP 驱动 `connect()` 在文档还停在 `about:blank`
   时读 `localStorage` 抛 SecurityError，整趟一条用例没跑就退出。三轮里烧掉过一整轮，
   输出长得像「用例挂了」。修在驱动里（等真 origin，且探针自己吞异常——
   `eval` 遇 exceptionDetails 是直接 rethrow 的，不 try 的话 `waitFor` 第一轮就崩、
   退化成没有重试）。**探针本身不可靠时，它给的每个读数都要打折。**

## §43 2026-08-16 探索式 QA 阶段 3 · Q13：两个分类出口收敛成唯一实现

卡与逐项落点见
[`…qa-exploratory-root-cause-cards.md`](design/2026-08-15-qa-exploratory-root-cause-cards.md)
§4「阶段 3 · Q13 实施记录」。本节只留流水与判据。

**为什么排在 Q7/Q8 之前**：端侧把结构化意图翻成意图名有两个出口（单句 `classify()`
与分段 `_to_legacy_name()`），实测 **15/38 处产出不同、7 处 `is_local` 相反**——
同一句话在单句形态与复合句形态下走**不同的路**。在它收敛之前修 Q7 的极性维度，
会在两条不一致的路径上各修一遍。

### §43.1 收敛

`_to_legacy_name` 成为唯一实现，`classify()` 删掉自己那张 142 行对象表。
命名口径的权威是 `commands.yaml` 的 `edge_intents`（B4 唯一声明处）：
on/off 还是 open/close 由它定。方向信息（`下一首`/`上一首` 的 data 逐字相同）
走 `classify_structured` 盖的 `_raw_text`。

> **判据（新）**：**「收敛」不是把一条抄给另一条，是把两条的并集写成一份。**
> Q1-A 那批已经应验过一次（词表并集），这次是对象表。

> **判据（新）**：把信息塞进**被传递的对象**而不是**函数形参**。形参会被某个调用方
> 忘了传（§4.3 那条 `replan()` 加形参、测试替身没跟着传，两臂逐字相同还差点据此
> 否掉自己刚写对的守卫）；对象自带的键，忘不了。

### §43.2 三件不在计划里的事

1. **dashcam 从来没被分段路径路由过**（`_to_legacy_name` 产 `dashcam.on`，白名单里是
   `dashcam.open`），单句路径靠 `classify()` 的 `else` 兜底恰好拼对、把它盖住。
   > **抓到它的是 L0 语料不是单测**——语料测 **ingress 路由**，单测测**名字**。
   > **名字对不对与走哪条路是两层断言。**
2. **两条测试从红变成假绿**：`test_cloud_degraded_fallback` 用「播放音乐」当
   「会上云的可执行原话」，收敛后它变成端侧快路径，断言照样成立但**云端兜底那条
   P0 探针一次都没走到**。假绿让一条回归探针悄悄退休，比红更贵。
3. **动了一条 gate 语料的原话（不是期望）**：那条 case 的前提被本批消灭了——
   不是要求没被满足，是**探针够不着它要测的东西**。id/family 不动 ⇒ baseline id 集合
   不变、不触发 `removed_cases`；1:1 交换后唯一输入仍 596/596。

### §43.3 刻意没做的守卫

「每条 `edge_intents` 都必须能被命名实现产出」实测 6 条不可达里 **5 条是枚举伪报**
（对象别名 / attrs 命名），要落地得配豁免台账 = 第二份声明（B4 判据）。
`derive_edge_intents` 的 docstring 早写明意图名承载四类 `commands.yaml` 里没有的判断
——这是那句话的第二次应验。改为三条守卫，长期有效的是前两条：
源码级「只许一处命名实现」、40 行金标行为锁、16 条死条目断言。

### §43.4 读数

- 两个出口分歧 **15/38 → 0/38**（`scripts/probe_qa_regression.py --mapping`）。
- 端侧套件 579 → **674**；`orchestrator/edge + cloud` 合跑 **1518**。
- 后端全量 **5836 passed / 14 skipped 零红**（26m50s；较 5739 净 **+97** 逐条点号：
  守卫文件 93 + dashcam 两行金标各占 golden/分段两个参数化 = 4）。
- L0 门禁 discovery **81/81** / gate **25/25**，唯一输入 **596**（未变）；
  `test/smoke_edge.py` 13/13、`test/eval_capability_integrity.py` exit 0。
- 真栈（重建 edge-orchestrator 后）：「暂停音乐」**154ms 端侧秒回 `media.pause`**、
  「打开行车记录仪」5ms `dashcam.open`、「打开氛围灯」5ms `ambient_light.on`。

### §43.5 一处自伤：我自己造了 192 条假红

全量首趟 **192 failed**——这个数**逐字**是 §4.0 记过的假红签名（`python` 不在子进程
PATH 时 e2e manifest 校验失败、`scripts/tests` 整族连坐，日志里 185 处
`executable does not exist: python`）。上一趟设了 PATH、这趟起进程时漏了。
**记过的坑照样会再踩，因为踩它的方式变了**（上次是 shell，这次是 `Start-Process`
的子进程环境）——「红了先问是不是前提变了」在这里省下的是一次误判定性。

## §44 2026-08-16 探索式 QA 阶段 3 · Q2 第一批：候选集升格为一等对象

卡与逐项落点见
[`…qa-exploratory-root-cause-cards.md`](design/2026-08-15-qa-exploratory-root-cause-cards.md)
§4「阶段 3 · Q2 实施记录」。契约 `docs/conventions.md` **§9.1**（保留键 `_fallback`）
与 **§9.1b**（候选集四条纪律）。本节只留流水与判据。

### §44.1 交付了什么、**没**交付什么

交付：`Focus.candidate_sets` 台账（来源/版本/时效/结构化属性，容量 3、TTL 900s）、
`last_choices` 改为派生视图、`_fallback` 保留键、序数优先绑非兜底、
「零可引用候选时不进 Planner 的确定性弃权」。

**没交付**：I-018/I-023 要的确定性 handler（「哪家最晚关门」「两个价格合计」）。
属性存下来了，算它的人还没写。
> **别把「数据在了」读成「问题解决了」。** 卡里那句「候选集是给**确定性消费方**用的
> 结构化事实」同时也是这批的边界——载体和消费方是两件事，写在这里免得下一个人
> 看到「Q2 已完成」就以为那两条也好了。

### §44.2 三处我自己写错、被实测按住的

1. **合并键漏了一维，N5 换个地方原样复发**：台账去重首版按
   `(source_intent, purpose)`，「川菜 → 兜底美食」两轮同键，兜底当场把点名那份挤掉。
   > **兜底那份与点名那份不是同一件事的两个版本，是两种东西。**
2. **`_fallback` 判据首版方向写反**：判「检索词退成干净类目词」，把**用户点名的类目**
   （「附近的停车场」——干净类目词正是对的检索词）一起标成兜底。
   > **标反方向比漏标贵**：真候选被当成兜底会永远排在序数解析之后。
   > 判据要落在「**有没有一个用户说了、我们却丢掉的词**」上，不落在检索词长什么样上。
3. **单一信号够不着一半**：planner 有时**一个具体词都不填**，nearby 没有可比对的东西。
   补第二个信号「类目是不是猜的」，两个取或。⚠ 第二个信号的 haystack
   **刻意不含 category 槽**——那是 planner 填的不是用户说的，算进去它永远不触发。

### §44.3 尺子错了三次（同一条用例）

CD2 的判据从「没说『没有列表』」→「必须点到第 1 轮卡片第 2 项」→「不得点到第 2 轮
那一组」，每一步都是被实测逼的：

- 原判据**连续三次假绿**：它确实答出了店名，答的是**兜底那份**的。
  > **「答了一个名字」和「答对了那一份的那一个」是两件事**，话术层分不开，
  > 卡片层分得开——判候选绑定要读**结构化的卡片 items**，不读话术。
- 改读卡片后全红：卡片是 `•`（U+2022）、话术是 `·`（U+00B7），我在做逐字子串匹配。
  **系统对了、尺子认不出**（同 SF3 那次把正确回答判成红）。
- 再改还红一条：模型说主干名没带分店括注（「辣宴•老坛酸菜鱼」vs「…(汉京金融中心店)」）。
  **那是同一家店。**

最终拆成 Q2 真正主张的那一条：**不得点到兜底那份**；「答得好不好」归别的卡
（§4.3「把『不再危险』和『回答完美』分开报」的同一形态）。
> 配套一条新原语：被引用的那一轮**没有候选卡**时探针**出提示、不静默判绿**——
> 一个什么都没证明的样本不该被当成证据（E4「探针替被测系统抽掉一个前提」的同族）。

### §44.4 读数

- QA 迷你集 candidate 组 `--repeat 3`：CD1 **3/3**、CD2 **2/3**（残余是「逐字重复
  上一轮」= 答非所问，**不是绑错候选**）、CD3 **0/3 → 3/3**（三次话术**逐字相同**
  ——确定性守卫的意义正在于此：正确性不该取决于这次模型怎么想）。
- 单测：`test_candidate_sets.py` **21** 条（含注入缺陷验红：序数绑定回落最近一份 +
  白名单改全量透传 → 3 红，回退 → 全绿）、nearby **+6**（99）。
- 后端全量 **5863 passed / 14 skipped 零红**（28m10s；较 5836 净 **+27** 逐条点号：
  21 + 6）。`orchestrator/cloud` **865**、edge+cloud+nearby 合跑 **1642**。

## §45 2026-08-16 探索式 QA 阶段 3 · Q7：端侧极性 / 顺序 / 省略

卡见 [`…qa-exploratory-root-cause-cards.md`](design/2026-08-15-qa-exploratory-root-cause-cards.md)
§4「阶段 3 · Q7 实施记录」。本节只留流水与判据。

### §45.1 三个维度，交付两个半

- **极性**（I-039）：新 `orchestrator/edge/polarity.py`，判据接进 `classify_structured`
  的**出口收口**（与既有「问句不许被执行成写操作」同一道闸）。负极性写操作
  **不产出本地意图**，整句诚实上云。
  > **「别开」的反面不是「关」，是什么都不做。** 映射成反向意图只是换了一个
  > **有副作用**的错误动作——卡里那句「宁可慢一点，不要按反」的落点就在这里。
  > ⚠ 本模块是极性判据的**唯一实现**，Q11 的「别建提醒」守卫按卡的要求共用它。
- **顺序/省略（段内）**（I-040）：`_backfill_object` —— 判不出对象的段用**上一段的
  对象**再判一次（对象名从 `commands.yaml` 的 `display_name` 派生）。三道安全闸。
- **并列对象**（I-005）：`_expand_paired_objects` —— 「前后 X」拆成前、后两段，
  **两半必须解出两个不同的对象**才拆（「前后座椅加热」两半都是 `seat`，拒绝）。
- **未交付**：OR2（补语跟着主意图上云后、云侧要用**同轮**上下文解出「打开」）与
  EL1（**跨轮**省略「关掉」，按卡的分工走 Q2 焦点的云侧确定性消费）。

### §45.2 我差点上线一个「静默丢用户内容」的优化

为了让 OR2 别整句上云，加过一条「没有指令内容的段直接跳过」。它当场把
「打开空调，帮我播一首歌，**周杰伦的**」里的补语也丢了——端侧随机放一首歌，
用户点的歌没放成。**是既有回归探针
`test_trailing_qualifier_still_travels_with_its_head` 抓到的，不是我自己看出来的。**

> **判据（新）**：**「这一段没有指令内容」不等于「这一段可以丢」。**
> 补语没有动作，但它带着用户说的东西。整段上云只是慢一点，静默丢内容是答错。
> 已撤销并把这条钉成断言（`test_no_content_segment_is_never_silently_dropped`）。

### §45.3 回填的安全闸从两道加到三道，也是被实测按出来的

首版「关闭空调然后打开，**按顺序执行**」把第三段回填成第三个 `aircon.open`
——**发明了一个用户没说的动作**。补第三道闸「这一段有没有贡献信息」。

> **判据（新）**：这类「用分类器自己问一个元问题」的探针，**探针对象要从知识库派生
> 且必须选对**。首版拿上一段的对象当探针，「空调」这个裸词默认就解成 open，
> 于是**真的「打开」也被判成填充语**。选「裸词本身判不出意图」的对象才有分辨力。
> 三道闸一道都不能少——每一道都是被一次实测按出来的。

### §45.4 EL1 暴露出一条 Q1-A 的回归（我引入的）

「不用了，关掉」被答成「当前没有待确认的操作」：`is_standalone_cancel` 靠
「词长 + 松弛量 3」判整句，而 `不用了` 是 3 字 ⇒ **6 字的「不用了，关掉」也算整句**。
收紧成「逗号后面还有实质内容就不是裸取消」。

> **判据（新）**：**「词占据整句」这种量具，词越短越不可靠**——松弛量是常数，
> 短词的相对松弛就大到能塞下另一个完整分句。先看有没有第二个分句，再谈词长。

### §45.5 读数

- QA 迷你集 negation 组 `--repeat 3`：**12/36 → 30/36**。
  NG1/NG2/NG3/NG4 全部 red（NG3 是**假绿**）→ **3/3**；OR1、OR3 red → **3/3**；
  正向对照 NG5/NG6/EL3 仍 3/3（**没修过头**）；OR2、EL1 仍 red（形态已变，见 §45.1）。
  ⚠ NG3 的 red 是 Q13 换来的：收敛前它靠「名字不在白名单」侥幸不执行，
  **Q13 让缺陷第一次诚实显形**，Q7 才修得着。
- 后端全量 **5901 passed / 14 skipped 零红**（26m52s；较 5863 净 **+38** 逐条点号：
  `test_polarity.py` 26 + `test_segment_backfill.py` 11 + `test_pending_cancel.py` 1）。
  edge **713**、cloud **866**；`smoke_edge` 13/13、能力完整性门禁 exit 0、
  L0 门禁 discovery 81/81 + gate 25/25。

## §46 2026-08-16 探索式 QA 阶段 3 · Q5：关系写入闸 + 任务查询范围（口径 B）

契约 `docs/conventions.md` **§9.21**（查询范围口径）/ **§9.22**（关系写入闸）。

### §46.1 交付了什么、没交付什么

交付：写入闸四道（自环[family 除外]/主宾角色/槽名泄漏/置信度）+ 单值谓词
`superseded_by`（**列一直存在却从未写过**——「同一个孩子三个学校」、也就是
I-044「幻觉」的真身）+ 任务查询默认范围收窄（「从现在起」，过期与 `fire_at<=0`
的伪提醒另计**并报数**）。

未交付：**出处披露仍是方差**（真栈两次取样一次说「你之前提过」、一次直接答
「南山实验小学～」）。做成确定性要一个「本轮答案是否由记忆驱动」的机制，独立一件事。

### §46.2 泓舟拍板：方案 B——隔离维度是 owner 不是 session

`reminder_item` **刻意不加 `session_id` 列**。理由：车机上同一个人换轮次仍是同一个人
的提醒，按 session 切会让「我昨天设的提醒呢」查不到；真正该隔离的维度已由 owner 成立。
配套三条写进 §9.21：默认「从现在起」/ 收窄不等于隐藏（报数 + 「看全部」出口）/
**话术不得假装范围是本次对话**。

XS1 的考点据此改过并留痕——**契约变了，旧期望描述的行为已经不该存在**（同 CF5 那次）。

### §46.3 卡上的定性被推翻一处，而清洗脚本正准备照着它删数据

卡 §3-Q5 把 `family(老婆→老婆)` 记成「自环边，零信息」。读消费方才发现
**它是「没有名字的人」的表示法**：`store.resolve_person_place` 靠 family 边的
**object** 反查人实体。写入闸首版把它拒了，`test_resolve_person_place_via_works_at`
与 `test_deterministic_place_relation_without_name` **当场红**。
`qa_data_hygiene.py` 的 ① 族已改成 `rel <> 'family'`。

> **判据（新）**：**清洗脚本删的是数据，而数据是不是垃圾要问消费方，
> 不能只看它长得像不像垃圾。** 这是 dry-run 第三次劝退一条判据
> （前两次：单值谓词冲突、`fire_at<=0`），也是「读源码推翻报告定性」在**卡自己**
> 身上的第一次应验——**卡是上一轮的结论，不是这一轮的前提**。

> **判据（新）**：supersede 只写 `superseded_by` **不写 `valid_to`**——
> `memory_relation` 根本没有那一列。首版照着相邻的 `memory_item` supersede 抄，
> 真库上会直接报错。**读 schema，别照着相邻实现抄。**

### §46.4 读数

- QA 迷你集 session 组 `--repeat 3`：XS2 3/3、XS4 2/3（方差）；
  XS1 按新口径重测见下一批；XS3 仍红（出处披露是方差，见 §46.1）。
- 单测：`memory/tests/test_relation_write_gate.py` **22** 条（含注入验红：
  关掉 supersede → 红，回退 → 绿）、reminder **+3**（160）。
- 后端全量 **5925 passed / 14 skipped 零红**（21m39s；较 5901 净 **+24** 逐条点号：
  memory 229→**250**、reminder 157→**160**）。

## §47 2026-08-16 QA 卡 Q11：提醒准入（否定守卫 + 写入闸）

阶段 4 的这张**提前做**——它是数据清洗 `--apply` 的最后一道前置（泓舟拍板顺序：
先修入口再清数据）。

- **否定守卫**：「别建提醒」**共用 Q7 的极性词表**（`runtime.polarity.NEG_WORDS`），
  卡 §3-Q11 明写同源。实测库里逐字躺着「接爸妈去吃饭，**别建提醒**」建成的那条。
- **写入闸**：`kind=time` 且 `fire_at<=0` → `InvalidReminder`。**抛而不是静默丢**
  ——调用方必须知道自己没建成才谈得上诚实追问；静默丢会造出「系统说建好了、
  库里没有」这种更难查的形态。
- **双重否定例外**：「别忘了提醒我八点开会」真实语义是**要建**。

### §47.1 共享模块的落点是查 Dockerfile 决定的

`runtime/polarity.py`（从 `orchestrator/edge/` 移过来），**不是** `agents/_sdk/`
——端侧镜像里**没有** `agents/`，而两边都 `COPY runtime`。
> B3 那次「代码里 import 得到 ≠ 镜像里拷进去了」在 40 小时里毫无症状。
> **这次先查依赖闭包再选目录**，而不是先放好再等它炸。

### §47.2 `不用` 只在提醒域补进词表

它在共享词表里被**刻意排除**（「不用了」全局歧义——那是取消挂起，归
`pending_cancel`），但宾语是「提醒/闹钟」时不歧义。
> **这不是第二份词表**：本域引用共享的 `NEG_WORDS` 再补一个只在本域成立的词，
> 它没有自己的否定词定义。判据是「有没有第二处**定义**」，不是「有没有第二处使用」。

### §47.3 写入闸生效后我自己那条展示层测试当场红

它用 `store.add` 造 `fire_at=0`。改成绕过闸直接塞存储，理由写进 docstring：
> **闸挡的是新写入，存量脏数据还在库里**（清洗 `--apply` 才管那些）。
> 两件事分开验，否则「闸建好了」会被误读成「用户不会再看到那三条」。

### §47.4 读数

后端全量 **5929 passed / 14 skipped 零红**（22m09s；较 5925 净 **+4**：
reminder 160→**164**）。

## §47.5 2026-08-16 洁癖整理：QA 阶段 2/3 七批完成态归档索引

> ⚠ 本节原编号是 §47.1，与上方 §47 的子节 **§47.1/§47.2/§47.3 逐个撞车**
> （2026-08-16 数据清洗批发现并修正）。AGENTS.md 两处「归档索引 §47.1」
> 想指的是本节，读者却会先撞上「共享模块的落点」那节。
> **编号撞车不会报错，只会让引用悄悄指向另一件事**——同 B4 那条
> 「不加第二份表达同一件事的声明」在**文档编号**上的形态。

AGENTS §4.1 ① 的阶段表原来把七批的完成叙述全塞在两个单元格里。按既有约定
（§38.1/§40.1 两次整理的同款）**把叙述换成索引**：§4.1 只留**逐卡状态与残余**，
落点/判据/读数回本文件与卡。**归档不是再抄一遍**。

| 批 | 流水 | 一句话 |
|---|---|---|
| 阶段 2（Q1 A→B→C / Q3 / Q4） | **§42** | 取消判据收敛 + `operation_id` 寻址 + 挂起表；帧带 `request_id`；位置闸拆 ORIGIN/ANCHOR |
| Q13 两分类出口收敛 | **§43** | 分歧 15/38→0/38；媒体域端侧快路径**第一次真正生效** |
| Q2 候选集升格 | **§44** | 台账（来源/版本/时效/结构化属性）+ `_fallback` 声明 + 无候选确定性弃权 |
| Q7 端侧语义维度 | **§45** | 极性不产出本地意图 / 段内省略回填 / 并列对象展开 |
| Q5 写入闸 + 范围口径 B | **§46** | 四道闸 + 单值 supersede；隔离维度是 owner 不是 session（泓舟拍板） |
| Q11 提醒准入 | **§47** | 否定守卫共用 `runtime/polarity.py` + `fire_at<=0` 写入闸 |

**契约面新增六条**（改这些面之前先读）：`conventions.md` **§9.19** 挂起寻址 /
**§9.20** WS 帧归属 / **§9.1** 保留键 `_fallback` / **§9.1b** 候选集 /
**§9.21** 提醒查询范围口径 B / **§9.22** 关系写入闸。

### §47.6 本轮沉淀、且**不属于任何一张卡**的六条判据

写在这里而不是 §4.3，是因为它们讲的是**怎么改**不是**怎么读数**；
§4.3 已经很长，再塞会稀释那批读数纪律。下一次动这套系统前值得先看一眼。

1. **「收敛」不是把一条抄给另一条，是把两条的并集写成一份。** Q1-A 直接让
   `wait_confirm` 复用 `wait_slot` 词表会**换一个洞**（`不订/不付/先不` 只在前者表里）；
   Q13 的对象表同理。按歧义度分层，而不是二选一。
2. **「这一段没有指令内容」不等于「这一段可以丢」。** 为省一次上云加的「填充段跳过」
   当场丢了「…播一首歌，**周杰伦的**」的补语——**是既有回归探针抓到的，不是我看出来的**。
   整段上云只是慢一点，静默丢内容是答错。
3. **卡是上一轮的结论，不是这一轮的前提。** 卡把 `family(老婆→老婆)` 记成
   「自环零信息」、清洗脚本 ① 族正准备删——读消费方才发现它是「没有名字的人」的
   表示法。**清洗脚本删的是数据，而数据是不是垃圾要问消费方，不能只看它长得像不像垃圾。**
4. **共享模块的落点由镜像依赖闭包决定。** `polarity.py` 放 `runtime/` 不放
   `agents/_sdk/`，因为端侧镜像里没有 `agents/`。**先查 Dockerfile 再选目录**
   （B3 那次「代码里 import 得到 ≠ 镜像里拷进去了」40 小时无症状的预防版）。
5. **「有没有第二处定义」才是判据，不是「有没有第二处使用」。** reminder 引用共享
   `NEG_WORDS` 再补一个只在本域成立的 `不用`，不算第二份词表。
6. **读 schema，别照着相邻实现抄。** relation 的 supersede 照 `memory_item` 抄了
   `valid_to`，而 `memory_relation` 根本没有那一列——真库上直接报错。

### §47.7 尺子在本轮里错了六次，每次都是尺子自己错

CD2 三次（话术判据**连续三次假绿** → 卡片 `•` vs 话术 `·` 逐字匹配 → 模型说主干名
没带分店括注）、CD3 一次（关键词表把「哪家」「没法」这类**正确弃权**判成红）、
Q4 一次（凭空加了一条**扩大**闸的断言，与本卡方向相反）、CF5 一次（为单槽写的期望
在挂起表落地后要求系统说一件不真的事）。

> 与「不为某个模型的问题改 gate 案例集」不冲突：那条针对**被测对象做不到**，
> 这六次是**尺子认不出对的**或**契约已经变了**。**尺子写错必须改，且要留痕。**

配套新增探针原语：`names_item_from` / `not_names_item_from` / `no_clock_time` /
`op_from` / `op_literal` / `closes_op_from` / `has_operation_id`，以及
**「前提不成立时出提示、不静默判绿」**——一个什么都没证明的样本不该被当成证据。

## §48 2026-08-16 QA 卡接手顺序第 0 步：存量数据清洗 `--apply`（逐族授权）

排在 Q10/Q6/Q5 残余之前**不是因为它简单**，是因为它改变后面所有卡的读数
（AGENTS §4.1 ① 接手顺序 #0）。前置条件在 §46/§47 落地：Q5 关系写入闸 + Q11 提醒
准入闸都已就位 ⇒ 现在清是「清完不会再脏」。

### §48.1 泓舟逐族拍板与执行终态

| 族 | 卡上 dry-run | 本次 dry-run | 裁定 | 终态 |
|---|---|---|---|---|
| ① 自环关系边 | 4 | **0** | — | 0（`rel <> 'family'` 判据修正后自然归零，见 §47.6-3） |
| ② 主宾颠倒 | 1 | 1 | **apply** | 0（DELETE `大楼A栋 --works_at--> 用户`） |
| ③ 单值谓词冲突 | 2 组 | 2 组 | **只清「女儿」组** | 1 组（「孩子」组保留） |
| ④ `fire_at<=0` 仍 pending | 3 | 3 | **apply** | 0（置 cancelled，不删行） |

`superseded_by` 列**首次有值**——此前全表 0 条写过，supersede 机制从未在真库上生效过。

**③ 两组性质相反，这正是它必须人裁的理由**：
- 「女儿」组 =「深圳市南山实验小学」vs「南山实验小学」，**同一所学校**差个城市前缀 ⇒ 清。
- 「孩子」组 =「南山外国语学校」vs「深圳南山实验小学」，**两所不同的学校**、且两条都
  产生在 QA 轮窗口内 ⇒ 不动，等实体归一（「孩子」与「女儿」很可能是同一个人被拆成
  两个 subject，**实体归一不在清洗脚本职责内**）。

### §48.2 把人裁结论写成参数，而不是靠跑脚本的人记得

③ 原本是「整族一次清」。本批加 `--conflict-subject <subject>`（可重复），并让
**③ 的 `--apply` 在没点名 subject 时直接 exit 2**；点名了但那个 subject 不在冲突组里
**也 exit 2**（「认不出就拒绝，不静默跳过」——B3 那条的第 N 次应用）。

> **判据**：脚本自己 docstring 里写着「必须人裁」，那这句话就该是**一道闸**而不是
> 一行注释。同 B1 那条「可选断言等于把最该红的一类托付给『写用例的人记得加一行』」
> ——**写在注释里的纪律不是纪律**。
>
> 反向验证两头做：A) 不点名 → exit 2 且未写库；B) 点名「隔壁老王」→ exit 2 且未写库；
> C) 点名「女儿」→ supersede 1 条、明确打印「点名 1/2 组」。

### §48.3 清洗当场翻出 person-pickup 卡的一条硬证据

AGENTS §4.1 ② 留着一个悬案：那张卡写「关系边里存的是全名、是 planner 转述丢了城市」，
而 QA 轮 psql 取证发现库里**同时**有带城市与不带城市两条边 ⇒ 「**可能是召回了那条本来
就不带城市的边**，动码前重新取证是哪一条被召回」。

**本批把这个悬案关掉了，答案是「两条都不是」**：不带城市那条边已 supersede，现行边
只剩「深圳市南山实验小学」；而清洗后 XS3 三次取样仍有两次答**「南山实验小学」**。

⇒ **召回源根本不是 `memory_relation`**。库里还躺着 `memory_item` 的同源第二份存储：

| id | kind/scope | text |
|---|---|---|
| `3b2713f4…` | semantic / `profile.person` | 用户的女儿在**南山实验小学**上学 |
| `b9a0e860…` | episodic / `episodic.general` | 今天要五点前到**南山实验小学**接女儿 |

> **判据**：**清洗脚本只碰它认得的那张表**。`memory_relation` 清干净了，
> 同一件事在 `memory_item` 里的第二份表达一个字没动——**「同一件事的两份存储」
> 在清洗面上的形态**（B4 那条「不加第二份声明」的数据版）。
> 修 person-pickup 卡前要处理的是这两条，不是关系边。

### §48.4 两处工具与文档的漂移，顺手修掉

1. **`--relation-inverted` 的召回面比注释窄**。docstring 把三条形态相似的脏边都写成
   「实测」，实际判据（两端**同时**命中反向特征）只抓得到一条：
   `公司 --lives_at--> 大楼A栋`（「公司」不在人称词表）与 `深圳 --place_of--> 出发地`
   （「出发地」不在人称词表）**判不出、也不该判**。
   > **漏是「宁可漏不误杀」的设计内行为，但把设计内的漏写成待清项就是文档漂移**
   > ——读的人会以为跑完这一族库里就干净了。
2. **`§47.1/§47.2/§47.3` 在本文件里各有两个不同的内容**（§47 的子节 vs 洁癖整理段）。
   AGENTS.md 两处「归档索引 §47.1」想指的是后者。已把洁癖整理段整体改为
   **§47.5/§47.6/§47.7**。**编号撞车不会报错，只会让引用悄悄指向另一件事。**

### §48.5 脚本够不着、已立卡不做的两条

| 项 | 现状 | 归属 |
|---|---|---|
| **N3 错误导航被记成轨迹事实** | `2026-08-15 导航去过济南市南山实验小学` **两条**仍在 `memory_item` | Q5 残余。脚本 docstring 明写画像/情景类事实「判据是这条事实对不对，只能人裁，不能靠模式匹配」 |
| **同一个人两个称谓两个城市** | `妈妈 --lives_at--> 苏州` 与 `用户的妈妈 --lives_at--> 杭州` | Q5 实体归一。③ 按**字面 subject** 分组，两条分属两组各自「无冲突」；**别为了让它抓到就把分组键改成模糊匹配**——那会把「爸爸」「爸妈」并成一组 |

AGENTS §4.1 ① 的接手顺序 #0 把 N3 列为清洗理由之一，但脚本实际不覆盖它
——**这条已在 §4.1 与脚本 docstring 两处写清楚，免得下一个人以为清完就没有了**。

### §48.6 清洗后复跑同一条探针（改数据必须复跑）

`scripts/probe_qa_regression.py --repeat 3`，`minimax:MiniMax-M3`：

| 组 | 用例 | 阶段 0 基线 | 清洗后 | 说明 |
|---|---|---|---|---|
| session | XS1 (I-045/I-056) | red | **2/3** | 方差面 |
| session | XS2 (I-056) | red | **3/3** | |
| session | XS3 (I-044 出处披露) | red | **0/3** | **比 §4.1 记的「方差」更差**：三次全无出处。Q5 残余的真实读数 |
| session | XS4 (I-021/I-026 查单绑会话) | red | **1/3** | **下一张卡 Q10 的分母** |
| candidate | CD1 / CD2 / CD3 | red / red / red 0/3 | **3/3 / 3/3 / 3/3** | CD2 由 §44 的 2/3 升到 3/3 |

> **XS3 的读数被本批改硬了**：AGENTS §4.1 逐卡表把 Q5 出处披露记成「方差（两次取样
> 一次说『你之前提过』、一次直接答店名）」，清洗后三次取样**一次都没有出处**。
> 这与「单次取样不能当基线」是同一条纪律的另一面——**旧的方差结论也可能是采样不足**。

## §49 2026-08-16 QA 卡 Q10：查单/取消绑会话（I-021 + I-026 + I-037）

接手顺序第 1 步。**卡上「双入口不等价」那半未做**，理由在 §49.6 —— 它的依赖
（候选集下发通道）经取证并**不成立**，AGENTS §4.1 里「依赖已满足」那句话只对本批
这三块成立。

### §49.1 首偏离：写路径绑了会话，读路径没绑

`_resolve_order_ref` 只按 `user_id` 取账本最近一单，**连 `session_id` 都没传进来**；
而同一个文件里的 `_backfill_write_slots`（补偿类写操作）早就有「优先本 session」。
于是干净 session 问「我刚才那笔订单是什么」拿到**三天前**那笔——报告据此写下
「确认前创建了真实订单」这个 P0（阶段 0.1 已用三重证据推翻）。

> **元判据**：「查到了一个真实副作用」不等于「这次操作产生了它」。
> 查单不绑会话，就会把历史副作用搬进当前上下文，与「刚刚发生」无法区分。

### §49.2 新模块 `order_ref.py`：范围从**原话**判，零 LLM

| 档 | 触发 | 行为 |
|---|---|---|
| `SESSION` | 「刚才」「这次」「这单」… | 只认本 session；没有就**不出站**、诚实说没有 |
| `HISTORY` | 日期、「之前」「上次」… | 照旧取最近 |
| `NEUTRAL` | 都没有 | 优先本 session；回落历史**但话术标注日期** |

**两档同时命中判 `HISTORY`**（「刚才我说的那笔 8 月 12 号的订单」）：日期是更具体的
限定，「刚才」修饰的是「我说」。**具体限定优先于指代。**

`SESSION` 词表**刻意窄**，因为误判代价不对称：判成 SESSION 而用户要历史单 ⇒ 系统说
「给我订单号」，**用户还有出路**；判成 NEUTRAL 而用户要本会话那单 ⇒ 历史单被端上来，
**用户没有任何线索能发现**。所以「上一单/那笔」这类模糊词一律留在 NEUTRAL。

### §49.3 同一件事，本仓有四处——**三处有洞，第四处只护住一家**

| # | 落点 | 状态 |
|---|---|---|
| 1 | `agent._resolve_order_ref`（读） | 连 session 都没绑 → 本批修 |
| 2 | `agent._backfill_write_slots`（通用写） | 优先本 session **但回落历史** → 本批修 |
| 3 | `luckin._owned_order`（瑞幸写） | 同上，**真栈实测就是它捞出历史单的** → 本批修 |
| 4 | `luckin._explicit_order_id`（指代占位符） | 判据**只有瑞幸有**，通用写路径没有 → 本批收敛 |

三个循环的**过滤条件**确有正当差异（商户字段名 `server` vs `merchant`、墓碑语义、
可取消状态白名单），所以本批**共享规则不合并循环**：回落规则的唯一定义处是
`order_ref.allows_history_fallback()`，配一条**源码级守卫**
（`test_the_fallback_rule_has_exactly_one_definition`）禁止任何一处就地写
`scope == SESSION`。要不要合并循环本身，留给下一批带真机证据决定。

> 判据取的是 §4.3 那条「三份各自正确的实现迟早有第四份是错的」的**中间形态**：
> 这三份不是各自正确，是**各自都有同一个洞**。

### §49.4 真栈第 3 次取样才暴露的绕过口

XS8 修完从 0/3 升到 2/3，剩下那一轮读出来是：

> （演示商户）准备取消订单 **刚才那笔订单** 并退款，确认吗？

**planner 把 `order_id` 槽填成了字面量「刚才那笔订单」**。槽位非空 ⇒ 账本回填根本
不被调用 ⇒ **会话范围守卫整个被绕过**，而那串字符还会被拿去调商户 API。

> 这是既有判据「planner 改写 query 是不可信指代通道」（sports 批）在**槽值**上的形态，
> 也是 CLAUDE.md §6 那条「防御要一路防到真正会被拿去用的那个值」的第 N 次应验。
> **单测跑绿了三轮才被真栈第 3 次取样抓到**——`--repeat 3` 不是形式。

### §49.5 时区守卫：加一个模块，翻出两件事

查单话术要说「这是您 8 月 15 日下的那笔」，日期错一天用户就核对不上，
所以 `agents/mcp_bridge/src/agent.py` 必须进 `_WALL_CLOCK_MODULES`。加的时候发现：

1. **守卫的正则有个逃逸口**：尾部写死 `\)\s*\)`，要求 `timedelta(hours=8)` 后**紧跟**
   收尾括号。`timezone(timedelta(hours=8), "Asia/Shanghai")` **多一个参数就绕过去了**。
   已收紧（注入原写法验红 → 还原验绿）。
   > 这条守卫此前**扫不到任何真实违规**，读起来却像这块被守着。
   > **写完扫描类断言要问的不只是「它会不会红」，还有「它够不够得着现实里的写法」。**
2. **一次被既有断言按住的误收敛**：顺手把 `mcdonalds._shanghai_timezone()` 改成
   `BUSINESS_TZ`，`test_default_clock_is_explicit_shanghai_time…` 当场红——
   那条断言守的正是「**显式上海时区** vs 随便一个 +8」。
   已全部回退，并在两处写下理由。
   > **判据**：`BUSINESS_TZ` 是**车机业务墙钟**，`_shanghai_timezone` 是**麦当劳中国的
   > 营业时区**。PoC 里同值 UTC+8，**语义不同**——多时区时前者跟车走、后者不跟。
   > **「看起来是第二份定义」和「真的是同一件事」是两回事：判同不同要问语义，不是比数值。**
   > 这与 §49.3 那次「确实是同一件事」并列着看才完整。

### §49.6 「双入口不等价」那半**没做**，且它的依赖不成立

AGENTS §4.1 接手顺序 #1 写着「最小且依赖已满足（**Q2 候选集已就位**）」。取证结论：
**`Focus.candidate_sets` 只活在云侧 `context.py`，没有任何下发到 Agent 的通道**
（`mcp-bridge` 的 `context_scopes: []`，而候选集本来也不在那三个 scope 的枚举里）。

所以「文本路径先在当前候选集里做确定性匹配」要先建**跨层下发面**，
而那正是 Q2 残余（确定性消费方，接手顺序 #7）要建的东西。

> **顺序建议改为**：Q2 残余（建确定性消费方 + 下发面）→ 再回头做 Q10 的双入口收敛。
> 本条已回写进 §4.1，免得下一个人按「依赖已满足」开工后卡在同一处。

I-024/I-020 里还有**不依赖候选集**的小修法（门店无 city 时退回收藏列表、商品名匹配），
但那是逐条 badcase 修，不是卡上那条「收敛到同一结构化解析链」的根因修法，
且都需要真实商户 token 才验得了——**不混进本批**。

契约登记 `docs/conventions.md` **§9.23**（订单引用的会话范围）。

### §49.7 读数

单测 mcp-bridge **433 → 478**（+45 逐条点号：新文件
`test_order_session_scope.py` 45 条）。反向验证三处各做两头：
① 读路径不回落（注入 → 2 红 / 26 绿）② 写路径不回落（注入 → 1 红 / 30 绿）
③ 规则唯一定义守卫（注入就地比较 → 红）。

后端全量 **5974 passed / 14 skipped 零红**，较 5929 净 **+45** 逐条点号
（新文件 `test_order_session_scope.py` 45 条；`--co` 实测 5988 collected = 5943 + 45）。

⚠ **分两段跑的合计，如实标注**：`--ignore=scripts/tests` 段 5345/10（11m12s）+
`scripts/tests` 段 629/4（10m32s）= 5974/14，分母 5355 + 633 = 5988 与全集对上。
原因是**后台全量连续两次在同一位置（76%）被中止**——76% 正是 `scripts/tests`
那族拉真子进程的起点，与 §4.0 既有的「scripts/tests 要隔离复跑」同源。
两段分母等于全集，但**段 1 的 import 顺序与一趟根跑不同**，下次能一趟跑完时重取。

QA 迷你集（`--repeat 3`，`minimax:MiniMax-M3`，容器已重建三次）：

| 用例 | 卡前 | 本批 | 说明 |
|---|---|---|---|
| XS4 查单不回落历史 | **1/3** | **3/3** | 判据同批强化过：原来只排除订单号，**模型答一句澄清也算 PASS**，补了 `card_type: ""` 形态判据 |
| XS7 泛指查历史要标日期（新增） | — | **2/3** | 失败轮落到了 deep-research 检索，**落域方差不是 Q10** |
| XS8 取消不捞历史单（新增） | **0/3** | **3/3** | 三次都诚实追问 |

⚠ **把「不再捞历史单」和「答得好」分开报**：XS4 的 3/3 里有两轮是「没听清」、
XS7 有一轮走了检索——那些是**落域方差**（Q8/Q13 族）。本批主张的是前者，
断言也只压前者。

## §50 2026-08-16 QA 卡 Q6：执行事实账本 + 审计问答的确定性出口（I-047 / I-038）

接手顺序第 2 步。卡上点名的前置「**先量清楚写入量再定表**」先做了，
量出来的结果直接否掉了「新建一张表」这个默认选项。

### §50.1 写入量量清楚了，结论是「别建表」

obs 实测（38 天）：**2754 轮 / 763 个动作**，有动作的轮次**只占 24%**，
每轮动作数 1 个占 **88.6%**、最多 5 个；最忙一天（QA 轮当天）1105 轮 / 253 个动作。

⇒ **这个量落 PG 是过度设计。** 而 `store.append_turn` 已经具备这条链需要的全部东西
——TTL、user 索引、OwnerKey、幂等键、`ltrim(-50)`，**端侧与云侧都已经在调它**。
加一个 `actions` 字段是纯增量，**卡上那句「别做成只涨不清的表」由既有机制自动满足**，
不必新定保留期。

### §50.2 卡上没写的硬约束：40% 的动作云侧根本看不到

obs 按 path 实测：`local` **273 轮 / 313 个动作**、`cloud` 226/264、`mixed` 172/198。
**纯 local 轮不上云** ⇒ 台账若只建在云侧 Focus，端侧车控（最该被审计的那类）
永远查不到。这条是量 obs 时才显形的，卡上没有。

落在会话轮次上正好同时覆盖三条路径——端侧那条 `AppendTurn` 早就在调，只是没带动作。

### §50.3 落点与改动面

| 层 | 改动 |
|---|---|
| proto | `AppendTurnRequest.actions=10`、`Turn.actions=9`（**读写对称**：存下来而读不到等于没存） |
| memory | `append_turn(actions=…)` + `_clean_actions` 归一（非法元素**直接丢不做 `str()`**）+ **`actions` 进幂等比对集** |
| 端侧 | `_record_local_turn(actions=…)`、`_MemoryClient.append(actions=…)`、`_executed_names` |
| 云侧 | engine 从 **final 帧**收动作（用户看到的那份）→ 随 assistant 轮落库；`context`/`clients` 经既有签名探测透传 |
| 消费 | `agents/chitchat/src/audit.py`——**确定性、零 LLM** |

动作名口径三处统一（`payload.command` 回退 `type`）：端侧 `_executed_names`、
云侧 `_executed_action_names`、探针 `_action_names`。**不同口径会让审计回答与
badcase 面板各说各话。**

### §50.4 三处被真栈按住的错，两处是同一条老纪律

**① `actions` 必须进幂等比对集。** 首版没进——同 `turn_id` 改动作会静默成功。
**一条被悄悄改过的执行记录比没有记录更糟**：审计会照着它回答，用户无从发现。

**② 按位置猜「这个动作属于哪句话」→ 张冠李戴。** 真栈答出
**「执行过 2 个操作：暂停音乐、暂停音乐」**。查库才看清端侧写入是 fire-and-forget，
真实落库顺序是 `userA → userB → assistantA → assistantB`，
「往前找最近一条 user 轮」两次都拿到「暂停音乐」。
而 **`exchange_id` 本来就是干这个的**——M-B 契约逐字写着它把 user 请求与其可见回复
「绑成一个**不可拆的账目单元**」。
> **判据**：写位置启发式之前先问一句「有没有一个字段就是干这个的」。
> ⚠ 我的单测没抓到它，因为**测试历史是理想顺序**——探针替被测系统提供了「顺序正确」
> 这个前提（§4.3 那条的又一例）。已补 `test_actions_bind_by_exchange_id_not_by_position`。

**③ 只在 `handle` 里加闸 = 没加，本仓第三次。** 真栈首验只有 **1/3** 命中确定性回答，
另两轮 `plan_mode=toolcall_salvage_no_action`、span 里写着 **`via: stream`**
——走的是 `handle_stream`。
而 `chitchat/src/agent.py` 里**原本就有一条注释**写着
「两条路径都要挂……**只在 handle 里加闸等于没加**」并点名已踩两次（M2 Ledger、商户 badcase）。
**我就在那条注释下面踩了第三次**，因为改的是 `handle`、根本没读到 `handle_stream`。
> **判据**：同一条纪律写成注释还是写成结构，差别就是会不会有第三次。
> 修法不是「这次记得」，是把四个确定性前置（钟点/身份/安全/审计）收敛成唯一入口
> `_deterministic_reply`，两条路径各自调它，配源码级守卫
> `test_both_paths_share_one_deterministic_gate`（注入历史错法验红）。

### §50.5 尺子在本卡上错了四次，全部留痕

审计问答的验收判据迭代了四版，**前三版都有假绿**：

| 版本 | 判据 | 被什么绕过 |
|---|---|---|
| v1 | 关键词排除（「没开成/归零/继续播放」） | 立卡时就假绿 |
| v2 | `speech_has: ["车窗"]` | 「**关了车窗**」含「车窗」⇒ **方向说反照样 PASS** |
| v3 | 反向词表 + 「否认执行」词表 | 「车窗**没动**，音乐也没停，**没法真的控制车**」全不在词表里 |
| v4 | **正向判据**：对象词附近必须出现该动作的**正确方向词** | 通过（另加否定式与名词「开关」的歧义消解） |

> **判据**：v2/v3 换了名字，本质仍是关键词排除——**否认执行的表达空间比任何词表都大**。
> v4 把失败模式从假绿翻成假红：模型换个说法而没带正确方向词就会红，我会去看话术；
> 而假绿永远不会有人去看。**宁可假红。**
>
> ⚠ 四次迭代本身就是本卡的论据：**话术层判据验证不了「系统说的是不是真的」**，
> 这正是它必须做成确定性 handler 的理由。v4 定稿后拿**七条真栈实录**逐条对过
> （含立卡时那条原始 badcase），全部符合预期。

同批还修了探针汇总里一个自伤：`[det]/[var]` 确定性观测**刚算出来就被下一行的
`note =` 覆盖掉**（改 `+=`）——Q6 的核心证据丢在自己的汇总行里。

### §50.6 读数

| 用例 | 阶段 0 基线 | 本批 | 说明 |
|---|---|---|---|
| AU1（I-047 执行审计） | red（v4 尺子下 1/3） | **3/3 `[det]`** | 三次话术**逐字相同**：「这次对话里执行过 2 个操作：打开车窗、暂停音乐。」 |
| AU2（I-042 不得构造任务状态） | red（旧尺子 0/3） | **3/3 `[det]`** | **已由 Q2 的确定性弃权守卫解决**，本批只是把尺子改对 |

⚠ **AU2 那 3/3 不是本批的功劳**：旧判据要求 `is_question=True`，而系统答的是
Q2 落地的确定性弃权（「我这边没有可以引用的列表。你先说要找什么…」）——
**比问句更硬却被判 0/3**。被测对象做对了、尺子认不出，同 CD3/SF3 那两次。

### §50.7 合入云发布工作线（merge，非本卡产出）

推 Q6 时远端已有 **19 个提交**（`deploy/cloud/`、`scripts/cloud_*`，私有云发布流程，
另一条工作线）。**零文件重叠**，走 `merge` 不走 `rebase`（后者是 CLAUDE.md 红线），
无冲突。

⚠ **合并后重跑了一次全量**——「零文件重叠」不等于「零交互」（conftest / 共享 fixture
仍可能相互影响）。实测段 1 与合并前**逐字相同**（5370/10），差额全部落在段 2
（629 → **740**），证实那批确实只在 `scripts/tests` 下。

基线因此变成 **6110 / 24**（`--co` 6134），但**本轮产出只占 +25**：
合并带入 +111 passed / +10 skipped。**两条工作线的净增量必须分开记账**，
否则下一个人按 §4.3「净增量要跟同一个 SHA 比」去对就永远对不上。

## §51 2026-08-16 QA 卡 Q5 残余：记忆驱动的回答说出出处（I-044 / I-028）

接手顺序第 3 步。**直接复用 Q6 建好的形态**——确定性后处理，不是提示词。

### §51.1 直接成因是系统自己下的指令

`_memory_context` 注入 prompt 时逐字写着
「已知用户信息（仅在与问题相关时自然引用、勿生硬复述、**勿暴露这是系统记忆**）」。

**不是模型忘了说出处，是我们让它别说。** 那句话当年是为了「自然」，
代价是**真记忆在用户眼里与幻觉不可区分**——QA 轮正是据此把 I-044/I-028 记成
「幻觉/凭空生成记忆」，而 psql 取证证明库里逐字有那条记忆。

### §51.2 判据分两半，缺一半就退化成假个性化

落点 `agents/chitchat/src/mem_source.py`，确定性纯函数：

1. **回答与某条记忆有 ≥3 字的公共内容** ⇒ 回答确实用了它；
2. **那段内容不在用户这句话里** ⇒ 否则「你女儿的事我不清楚」也会因为共有「女儿」
   被判成记忆驱动，系统于是声称参考了一条它根本没用的记忆。

> 本仓已记过三种假个性化形态（声称参考却没参考 / 把别人的偏好套在你身上 /
> 记忆压过当轮明说）。**第二半判据就是专防第一种。**
> 真栈 session 全组复跑证明它够窄：XS1/XS2/XS4/XS8 一条都没被误加出处。

**出处是追加不是改写**（`ans + "（这是您8月15日提过的）"`）——让模型重说一遍
等于把确定性又交回给它。时间走 `runtime.clock`（容器 TZ=UTC，裸 `fromtimestamp`
会在跨日边界说错一天）。

### §51.3 这次守卫写在犯错之前

Q6 刚为「只在 handle 里加闸」踩了本仓第三次。所以本卡**先写断言再接线**：
`test_both_paths_append_provenance` 断言 `handle` 与 `handle_stream` 都调
`with_provenance`（注入「抽掉 stream 那半」验红）。
配套 `test_prompt_no_longer_tells_the_model_to_hide_memory` 是**行为锁**——
那句「勿暴露」不许回来，否则出处披露会被它抵消。

> 与 `test_both_paths_share_one_deterministic_gate` 是一对：那条管**前置**
> （零 LLM 直答），这条管**后处理**（LLM 答完之后的确定性追加）。

⚠ 流式路径的出处只能作**尾包补发**：判据要看完整回答，而正文早已流出去了。
这也是选「追加」而非「前缀」的另一个理由。

### §51.4 读数

**XS3 0/3 → 3/3**：三次话术的正文不同（LLM 生成）、**出处部分逐字相同**
（「（这是您8月15日提过的）」）。

⚠ 汇总行打的是 **`[var]` 而不是 `[det]`**，这是**预期的**：Q5 的主张是
「**出处**确定性」不是「整句确定性」——正文本来就该由 LLM 说得自然。
**别把这个 `[var]` 读成没做到。**

同批 session 全组复跑：XS1 3/3 / XS2 3/3 `[det]` / XS4 3/3 / XS7 2/3（失败轮走了
检索，落域方差）/ XS8 3/3。
⚠ XS2 现在由 **Q6 的审计出口**接住了（「我这次让你做了什么」确实在问执行史，
答案也是对的）——两张卡的产物在同一条用例上叠加，读数时要分得清是谁在答。

### §51.5 读数与净增量点号

后端全量 **6127 passed / 24 skipped 零红**（分两段：5385/10 + 742/14，
对 `--co` 6151）。较 6110 净 **+17**，⚠ **两条来源**：
本轮产出 **+15**（`test_memory_provenance.py`），另 **+2** 来自开工前 pull 进来的
`f5099f9`（HMI 本地语音模型随云发布交付，落在 `scripts/tests` 下）。
**不分开点号，下一个人会把远端的东西算成本轮多出来的用例。**
