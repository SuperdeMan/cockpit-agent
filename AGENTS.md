# AGENTS.md — 接手者（人 / AI agent）入口导航

> 你（开发者或 AI 协作者）接手本项目时**先读这一份**。它告诉你：项目是什么、铁律、现在真实进展到哪、第一步做什么、改完怎么自检。
> 工程约定的最高权威是 [`CLAUDE.md`](CLAUDE.md)；架构唯一真相源是 [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。本文件与它们冲突时以它们为准。

---

## 1. 30 秒了解项目

云边协同的智能座舱 multi-agent 系统。**分层混合编排**：端侧"快系统"秒回高频/安全敏感指令（车控/媒体）并离线兜底；云侧"慢系统"用 LLM Planner 编排复杂/跨域/多轮意图。所有 Agent 实现统一 gRPC 契约 + Manifest，经注册中心即插即用。

阶段：**Phase 1 工程化 PoC 主干、云端中枢 P0-P3、R2-R4 硬化主题、可观测台（badcase 排查贯通）与旅程级验证体系（L3 journeys + L4 HMI CDP）已落地**（2026-07-15）。
持久化/多实例、沙箱、告警等仍是后续工作（**服务间 mTLS 已由 R3.2 落地**，env 门控默认关）；**真实外部能力已接入首批**
（导航/POI=高德、天气=和风含 JWT/EdDSA 鉴权，无凭证回退 mock；2026-06-20 起真实凭证端到端
冒烟通过）。当前全量单测 **2270 passed, 7 skipped**（2026-07-26 单命令实测；前端另有 hmi node 192 +
dashboard 16）——各主题的测试增量与提交散列见 §4 对应行，不在此处堆历史。
**记忆系统已分层重构**（从 mock KV → pgvector 语义记忆 + 自动抽取 + 真实语义召回，详见 §4 与
`docs/design/2026-06-25-memory-system-redesign.md`）。

---

## 2. 项目地图（先看文档，再看代码）

| 想了解 | 看这里 |
|---|---|
| 为什么这么设计（全局）| `docs/architecture/cockpit-agent-architecture.md` |
| 接下来分几步做、怎么验收 | `docs/architecture/phase1-implementation-plan.md` |
| 核心模块怎么编码 | `docs/architecture/detailed/ws{3,4,6,8}-*.md` |
| **怎么接真实 provider（高德/和风样板）** | `docs/guides/provider-integration.md` |
| **怎么扩 info 能力 / 加新独立 Agent 并打通** | `docs/design/2026-06-20-info-agent-expansion.md`、`docs/design/2026-06-20-standalone-agents-roadmap.md` |
| 前瞻设计 / 问题分析（多意图、ASR、车控、云端中枢、可观测）| `docs/design/` |
| 工程规则与铁律 | `CLAUDE.md` |
| 怎么搭环境、codegen、单服务调试 | `docs/dev-guide.md` |
| intent/scope/端口/错误码/env 速查 | `docs/conventions.md` |
| 怎么验证 | `test/README.md` |

代码目录职责见 `CLAUDE.md` §3；每个服务子目录都有自己的 README。

---

## 3. 铁律（违反即视为 bug，详见 CLAUDE.md §5）

### 唯一运行环境

- 根目录 `.env` 是唯一的运行时环境与密钥来源；不得复制、维护或依赖 `deploy/.env`。
- 全栈只允许用 `make up` 或 `docker compose -f compose.yaml ...` 启动；根 `compose.yaml` 显式加载根 `.env`，并以 `deploy/` 为 included Compose 的项目目录以保持构建路径不变。
- 不得直接以 `deploy/docker-compose.yaml` 为首个 Compose 文件启动，否则真实 Provider 可能静默回退 mock。

1. **车控只经 VAL**。任何组件（含 LLM/Agent）不得直接碰 CAN/SOME-IP。
2. **LLM 不直连车控**：LLM 只产"意图/计划"，车控由确定性 Executor 经 VAL 权限校验后执行（规划/执行分离）。
3. **危险动作二次确认**（`require_confirm=true`）。
4. **不改编排核心来加 Agent**：Agent 经注册中心被发现，新增 Agent 不动 orchestrator。
5. **密钥/token 不进代码、不进 commit、不进日志**；用 `.env`（已 gitignore）。
6. **改 proto 先改 `proto/` 再 codegen**，不要手改生成代码。

---

## 4. ⚠️ 当前真实状态（别假设没验证的东西能跑）

### 4.0 当前进度与下一步（新会话从这里开始）

> 截至 **2026-08-01**。下方长表是逐批次的历史流水（**只进不出**，查证据用）；这一小节是
> **交接摘要**——接手者先读这里，再按需下钻。

**最新状态（2026-08-01）**：2026-07-26 验收报告 §7 的 **13 张主卡全部收口**
（M-A 测试真实性 / M-B 多乘员隔离 / M-C 可靠触达 / M-D 外部生态，四批全部合入 main）。
每批的「明确未做」逐条附判据在验收报告 §9/§10.2/§11.2/§12.2——**未做不等于没账**。
全量 3597 passed / 11 skipped / 0 failed；HMI 225/225 + build；Go 网关 vet+test 通过。

**M5 P3 收尾已合入（2026-08-01）**：P3a 三张卡 + 2026-07-30 评审两条 INFO 项收口
（详见下方「M5 P3 收尾」段）。**本批不改路由行为**，但证伪了 P3a 的一条归因
（判别化描述的收益在 registry 兜底路径不在 planner，跨两档 Δ=0）、修掉了一个
**让影子 `agree` 状态从未出现过**的比较口径缺陷。

**✅ GitHub CI 已收口（2026-08-01，`176dd20` / run #232 七个 job 全绿）**，是 `#217`
之后第一次绿。最后 7 条里**有两条是真代码缺陷不是测试问题**——首次 canonical 晋升在
Linux 上必崩（先恢复事务、后建目录）、go wrapper 在 Linux 上一直是坏的（`\"` 转义只对
Windows PowerShell 的 Legacy 传参成立）。**它们能躲这么久，都是因为一段
`if os.name == "nt": return` 把校验整层跳过了。** 复现步骤与三条判据见 §4.0 末
「CI/nightly 现状」。**nightly 也已收口**（`c75df13`）：连红三次的根因是
**M5 P2 的 hint 退役抽掉了 mock 车道的确定性基础**——那些「端到端路由」断言一直是
正则在撑；判据已改写为「**mock-safe ⟺ 不经过模型判断**」。

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

**M5 待办（都不阻塞）**：

| 待办 | 出处 | 说明 |
|---|---|---|
| ~~**78 个端侧 capability 只有 2 句描述**~~ | P3a 影子第一条观测 | **已收口（`627f34c`）**，但**卡片归因不成立**：planner 侧 Δ=0 跨两档零翻面，收益在 registry 兜底路径。详见上方与 `docs/reviews/eval/edge_capability_desc_ab.md` |
| **能力面缺除雾意图** | P3 收尾（原 badcase 真根因） | `commands.yaml` 的 aircon 有 除雾/除霜/内外循环 等 mode，但 `VEHICLE_INTENTS` 里**没有任何除雾 intent**——`关闭强力前除雾` 无论哪一臂都只能在错误答案之间抖（实测 A 臂 `hvac.off`×3/`chitchat`×1，B 臂 `accompany_home.close`×3/`hvac.off`×1）。**这是缺能力不是缺描述**。补它要动 fast_intent + LOCAL_INTENTS + VAL 三处，属新增车控能力，非 P3 范围 |
| **P3b：operate 抽取 + 放量** | P3a | ~~对象桥接~~ 已落地（`nlu_objects.yaml`，`603127b`）。剩 operate 抽取（开/关/调到 N，准入判据②该走规则）+ 放量。开工判据不变：**错对象率压到 <0.3%**；压它的手段是补 R4.1b P1 执行侧对象化，不是调阈值。**新变化：这个数第一次可以从真实流量算出来**——`nlu.shadow` span 现在同时落 `path`（误接/漏接）、`nlu_vs_rule` 四态、`nlu_gate` 三档，`path!=cloud ∧ gate=high ∧ differ` 就是错对象率的上界 |
| ~~**影子盲区：规则误接看不见**~~ | P3a | **已收口（`0944659`）**：四条路径全挂，响应后 fire-and-forget |
| ~~**tokenizer parity 在 CI 上条件性 skip**~~ | 2026-07-30 评审 | **已收口（`fc88a21`）**：冻结 golden（260 例 + 行为等价词表子集 1096 条）进库，CI 零依赖必跑。⚠ 它守的是**算法**；生产词表正确性仍由导出时 `vocab.json` 同源 + `_assert_onnx_parity` 守——**换底座后本测试仍会绿**，该做的是重生成 fixture 并人审 diff |
| **规则把「穿衣指数」判成股指** | P3 收尾扫出 | `指数` 标签 179 条 100% 被 `fast_intent` 判成 `stock`（「查深圳的穿衣指数」→ 股指）。桥接表**刻意不收**这条等价（收进去等于把真 badcase 洗成 agree）。按 M5 范式修法是投范例/收窄规则，不是加 guard 词 |

| 待办 | 出处 | 说明 |
|---|---|---|
| **召回保护从 CI 阻断降级为人工触发** | P2 退役 | 19 条 hint 的召回断言原本是**阻断 pytest**（理由：语料在 continue-on-error 观测步），退役后保护改端到端口径进 `mode_routing_cases.yaml`，而那是 **live 车道不在 CI**。要补回来得让 live 车道进 CI（真栈+LLM，成本另议）。这是退役换来的真实成本，不是可忽略的细节 |
| **MiMo 第三档复验** | P2 退役 | 这批 hint 当初正是为 MiMo 写的（`route_hints.py` 开篇），但本环境 MiMo 无可用 key（探针 `all models failed`），证据只覆盖 minimax + deepseek。**切回 MiMo 须复验**——每条退役的原位注释都写了这句与 `git log -S` 恢复方法 |
| ~~**nearby 规则群内讧**~~ | P2 双臂裸跑 | **已收口（2026-07-30，`47eac1c`）**：真根因是金标自相矛盾，不是规则打规则。详见上方 |
| **`mcp-bridge#0`（shop.order）退役** | P2 交集 | 两档都判可退役，但 `require_confirm=true` → 治理⑥要求专项安全回归，**路由评测不构成安全证据**。等安全回归 |
| **3 条单档候选** | P2 交集 | `deep-research#0`、`info#4`、`trip-planner#1` 只在单档成立，被交集正确挡下。补第三档 provider 后再判 |
| **N1 域偏斜** | P2 RoutingBench | 语料前三域占七成，navigation 仅 5 例、hvac 1 例——**这些域的 N1 几乎测不出东西**。P3b 的对象桥接恰好要这些域的数据，可合并推进 |
| **catalog 检索化** | P2 有意不做 | TOP_K 未下调。**受保护集合已从 14 瘦到 13**（nearby 退光 hint 后失去资格，2026-07-30）——这正是 P2 说的杠杆方向：先让保护集瘦下来，不是硬调 top_k。⚠ 附带风险已记账在 `test_catalog_budget.py`：预算若再被追上，被裁的将含**周边发现这个高频功能** |
| **`gold_intents` 供给** | P1/P2 | 标注载体、导出 API、dashboard 入口、`from-labels` 工具链全部就位，但**真实标注量目前只有个位数**——机制建好≠供给在增长（skill 层同款教训）。飞轮真正转起来要看 `source=trace` 的范例条数 |

其余可选方向（都不阻塞，按需取用）：
- `sim.adas.*` 演示域——**低优先 backlog，非 M4 DoD**（2026-07-24 §8-6 拍板）。
- 真麦声学验收（S2S 打断手感 / 声纹识别率与误认率）——浏览器声学层 CI 测不了，同 R4.3 惯例留泓舟。
- 下方三张余项表里的条目。

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

**M4 P4 的已知余项**

| 余项 | 出处 | 说明 |
|---|---|---|
| 跨乘员共享记忆 | P4 RFC §4.2 / §10-2 | **拍板 v1 不做**：现有条目 `memory_level` 恒为 `user`，做读侧共享=全部共享=隔离归零；真共享层要改抽取分类，是独立一期。现状是第二乘员查不到主驾存的「家在哪」，各自教一次 |
| `min_consistency` 的窗只有 0.05 | P4 RFC §11.2-1 | 合格注册最低 0.53、三段不同人最高 0.48，取 0.50 居中。合成音色的段间一致性偏高，真人语音很可能要重调 |
| 声纹语音注册入口 / 模板漂移巩固 | P4 RFC §11.5 | v1 只做设置页录入；「记住我的声音」的多轮引导、以及「高置信样本回灌模板」的治理都留 v2 |
| 视觉多轮追问（「它旁边那个呢」） | P4 RFC §11.5 | 需帧的会话级驻留 + 指代解析，v2 |
| **obs 丢掉 `vp_*` 指标字段** | P4 RFC §11.8（2026-07-26 发现） | collector 的 `apply_metric` 是固定键白名单（count/avg_ms/error_rate/…），网关发的 `vp_decision`/`vp_score`/`vp_runner_up`/`vp_occupant` 全被丢掉——RFC 承诺的「四态全进 obs 供 M1b nightly 挖掘调阈值」**实际没落地**。当前靠两侧日志兜住（网关一行 + memory 一行全量排名）。真要做 nightly 调优得给 metric 开自由键或单列一张表 |
| 真人阈值只有一个会话的样本 | P4 RFC §11.8 | 0.45 是按一次真机实测（同人 0.52/异人 0.12）复标的，样本量=1。identify 日志现在有分数了，攒够线上分布再收敛 threshold/margin |
| **既有缺陷（非 P4 引入）**：显式记忆陈述**间歇被拒识** | P4 RFC §11.5 / §11.9（2026-07-27 定性修正） | **此前的归因是错的**：不是「planner 出空计划、记忆其实存进去了」，而是 **R4.4 拒识**——planner 判 `addressed=false`（当成乘客间闲聊）→ 静默短路，**整轮零回话且按设计不落库不进画像**（比原描述严重：说了、没回、也没记）。实测 `status=rejected`：「记住，我女儿叫小满」某次 3/3 被拒、换一批 6 条又只拒 1 条——**是 LLM addressed 判定的方差**。建议修法：**「记住/记一下/别忘了」这类祈使前缀确定性判为 addressed，不问模型**（同「系统持有的事实不交给 LLM」一族——用户用祈使句直接对助手说话，是不是在跟你说话不需要模型判断） |
| ~~**常去地点（家/公司）不分乘员**~~ | 2026-07-27 乘员维度盘点 | **已收口（M-B，2026-08-01）**：唯一真相源改为 owner-scoped `memory_item place.*`，`GetContextRequest`/`UpsertProfileRequest` 加 `occupant_id`，SDK `fetch/save_profile` 透传；upsert 变 per-key patch（不再整块 map 覆盖），primary dual-read legacy KV 只补缺失 key，**非 primary 永不读 KV** |
| ~~**提醒（reminder）无 occupant 维度**~~ | 同上 | **已收口（M-B）**：`reminder_item.occupant_id` + owner 索引，CRUD/list/cancel/序号态全按 OwnerKey；scheduler/geofence 领取后**先按 OwnerKey 分组再发 payload**，卡片 action 带 `reminder_id`+`owner_occupant_id`。M3 余项「多用户维度的免打扰/频控」仍在（治理器侧，属 M-C） |
| **HMI 记忆面板：单行删除已收口，L2-L4 未做** | 同上 | **M-B 已修单行删除**：面板默认只列当前 occupant，删除改走 `DeleteMemoryItem`（OwnerKey+item id），`identity.name` 只读引导到声纹设置。**仍缺**「清空该乘员学到的记忆 / 删除该乘员全部数据 / 删除该用户全部数据」三级入口——它们要跨 memory/reminder/scene/obs 四域 saga，属 M-B 明确后置项 |
| ~~端侧快路径的轮次不带身份~~ | 同上 | **已收口（M-B）**：`_record_local_turn` 从 `request.context`/`meta` 取 OwnerKey，一次本地请求写成一个完整 exchange（`<request_id>:user` / `:assistant:0`）。仍不触发抽取（端侧快路径多为车控/媒体，本就无偏好可抽）——那是有意的，不是缺口 |

**M4 S2S 的已知余项**

| 余项 | 出处 | 说明 |
|---|---|---|
| **主动消息与 S2S 播报的交叉未做** | M4 RFC §4.4 / §11.5 | RFC 说「把 `s2s_speaking` 情境位接进 M3 断言源」，但上报要建网关→proactive 的新链路（非零成本），且 P1/P2 DoD 未列。影响很小：HMI 上绝大多数主动消息只出气泡不播报，唯一会出声的是「异步深调研完成」。真要做仍应在治理器侧延后——critical 类**应该**抢话，HMI 侧排队区分不了 |
| 真麦声学验收 | M4 RFC §11.5 | 打断手感 / 唤醒后首字 / S2S 音色接受度——浏览器声学层 CI 测不了，同 R4.3 惯例留泓舟 |
| 穿过 `/api/s2s` 那一跳的断连注入 | M4 RFC §11.5 | 会话层重连已用宿主内代理注入真断线验过（真 provider）；容器级网络故障注入价值不抵成本 |
| `s2s_false_promise` 接自进化 | M4 RFC §11.5 | 检测已进 obs span，nightly 挖掘该族 badcase 待 M1b 流水线加一条规则 |
| 重叠对话（抢答式全双工） | M4 RFC §10 | 明确不做：v1 语义仍回合制（全双工红利先兑现在零建连延迟/无缝续听/多轮免重注入） |

**M3 的已知余项（都不阻塞 M4，按需取用）**

| 余项 | 出处 | 说明 |
|---|---|---|
| 治理器无持久化 | M3 RFC §7 | 重启丢待发/延后队列与频控计数；这些消息生命周期以秒计，落库不值当（e2e 正是靠「重启即净初态」做重置） |
| 合并话术是确定性拼接 | M3 RFC §7 | LLM 改写合并留 v2，且必须限定「只重述不新增事实」 |
| `obs.proactive.decision` 无消费方 | M3 RFC §5 | 事件已在发，dashboard 主动治理视图后置 |
| 位置提醒依赖 debug 注入的 location | M3 RFC §3 | PoC 无真实 GPS 流（road-safety 的「进入新区域」同一条路）；接真车 GPS 后围栏模块零改动 |
| MCP 首批是演示商户 | M3 RFC §10.7 | 真实商户 BD 是非技术依赖；resources/prompts/sampling、HTTP/SSE transport、动态放行均不做 |
| 多用户维度的免打扰/频控 | M3 RFC §7 | `occupant_id` 恒 primary，无消费方 |
| 主动消息的偏好学习 | M3 RFC §7 | 「这类别再提醒我」有价值，但要先有治理器产生的数据 |

**M2 的已知余项（都不阻塞后续，按需取用）**

| 余项 | 出处 | 说明 |
|---|---|---|
| T2 Complex 档只放到第一档 3 次/12s | 核心件 RFC §4.2 | `3-4 次/15s` 与 Interactive 跟进等下一轮 journeys 双指标数据；回退=改一行 env |
| `consent` 列只写不读 | 记忆图谱 RFC §3.1 | 为敏感画像预留，消费在 M3/M4 |
| 偏好加权不追溯存量 | 记忆图谱 RFC §10.4 | 老条目只有再次被巩固时才进加权体系（批量回填要重算全部历史证据，收益不抵风险） |
| Ledger 的 HMI 任务中心面板 | 核心件 RFC §7 | proactive 推送已覆盖告知；dashboard 任务视图可选后置 |
| checkpoint 自动 resume / NATS cancel 推送 / readback 求值器 | 核心件 RFC §7 | v1 刻意不做，理由在案 |
| 多跳图推理 / 实体消歧 / 跨用户关系 | 记忆图谱 RFC §7 | 无消费方，不做 |
| `sim.adas.*` 演示域 | 母提案 §8-6 | 低优先 backlog，**非 M4 必做 DoD** |

**三条跨期的方法论**（M2/M3 沉淀，后续直接适用）：
1. **消费面先于存储面**——存了没人查就是死数据。新机制先定「谁消费、消费什么」，
   没有消费方的东西不建。（M3 复用两次：删掉无人消费的 `merge_group`；位置提醒
   字段级对照后发现 `extra` JSONB 够用，**不加新列**。）
2. **挂点清单要枚举所有执行路径**——Verifier 按设计挂在 executor 尾链，真栈首验才发现
   engine D0 / loop T2 两条流式直通绕过它，声明了却静默不生效。新增执行期钩子必查全路径。
3. **周期性全量快照会把「变更回调」变成「定时器」**（M3 新增）——`vehicle.state.changed`
   每 30s 一次全量快照，挂在上面、语义是「变了才做」的消费方**必须自己比对上一次**。
   road-safety 是唯一漏掉的那个：车停着不动也每 30 秒查一次天气预警，一个查不到的地名
   就能**打开共享的 qweather 熔断器**，把整个天气域拖垮（journeys 因此红 3 条）。

---

| 项 | 状态 |
|---|---|
| 全量测试 `python -m pytest --import-mode=importlib` | ✅ **3597 passed, 11 skipped, 0 failed**（2026-08-01 实测=M-D 外部生态落地后，分支 `codex/acceptance-md-external-ecosystem`。本批净 **+16**[MCP 查单/取消/补偿 9（含真栈三修的槽位问法与确认词各 1、AST 源码断言 1）／Ledger 原子幂等 2／provider 能力位 4]。**真栈跑出三个单测测不到的缺陷**：mcp-bridge 镜像从来没装过 asyncpg（既有，M3 起幂等受理一直关着）、取消追问用了下单的词、确认词说错动作——见验收报告 §12.3b。此前 **3581 passed, 11 skipped, 0 failed**（2026-08-01 实测=M-C 可靠触达落地后，分支 `codex/acceptance-mc-reliable-delivery`。本批净 **+33**[投递账本 12／治理器可靠投递 9／HMI 语音仲裁 10（node）／Verifier transport-uncertain 6／提醒投递 ttl 2 — 与既有用例合并后计数如上]。**本轮先跑受影响子集全绿就提交，全量抓到 33 failed**（新表未登记隐私清单，守卫在 `scripts/tests/`），修完复跑归零；上一版记的 wrappers 间歇红本轮未复现。此前 **3548 passed, 11 skipped**（2026-08-01 实测=M-B 多乘员隔离落地后，分支 `codex/acceptance-mb-occupant-isolation`。本批净 **+35**[owner 轮次 12（含删乘员清原文 2）／抽取归属 2／端侧 owner 2／云侧历史 3／places owner 4／SDK 透传 3／reminder store 隔离 5／scheduler 分组 3／geofence 分组 3（新建文件）／声纹判重 6／L1 精确删除 6 — 与既有用例合并后计数如上]。**一条间歇性红灯已定性并立卡**：`test_e2e_wrappers_ci.py::test_python_and_powershell_preserve_final_json_selection_argv_and_rc[args0-0]` 间歇红（6 次整文件跑里红 2 次）。抓到失败那次的输出后定性清楚：红的**不是 rc**（两个 wrapper 恒 0，直接跑 10/10），是 `assert python_json == ps_json` —— 两个子进程对「canonical 为什么 stale」给出不同答案：`canonical_recompute_failed` vs `canonical_metadata_missing`（`stale: True` 两边一致）。前者是 `run_e2e.py:1009` 把 `ManifestError|OSError|ValueError` 吞成的一个**理由值**；同一计算在进程内连跑 30 次零异常、稳定给出 `canonical_metadata_missing`，故偶发只出现在 spawn 出来的子进程里。**这条路径只有在 canonical 已经 stale 时才可达**——那是任何有未合入提交的特性分支的常态，main/M-A worktree 上 canonical 是新鲜的，比较恒等、缺陷不可见。结论：**M-A runner 的 staleness 路径的既有健壮性缺口，被「有未合入提交」暴露而非由本批引入**（本批未触及 `scripts/run_e2e.py`/wrapper/manifest，`test_e2e_manifest.py` 168 条全绿）。修法方向=瞬时失败该重试或如实上抛，不该伪装成一条 canonical 判定理由。此前 **2379 passed, 7 skipped**（2026-07-29 实测=数据飞轮 P1+P2 全部落地后，main@857d38a。轨迹：P0 后 2347 → P1 **+16**[范例层契约：最软层容错/IDF 加权/同域去重/归因诚实/语义 fail-open 与补位/T2 与挂起继承/与 SkillStore 的目录边界/阈值钳制] → P2 **+21**[registry 打分性质 5（**加长描述不得改变 top-1**）+ 端云分歧 5 + 影子分诊判定表 10 + plan_summary 分歧列 1] → hint 批量退役 **−5**（删 7 个断言已退役机制的召回测试、加 2 个退役记录测试，**可对账**：2384−7+2=2379）。六个离线 eval 全 exit=0（route_hints 84/84、mode_routing det 55/55、registry_resolve 15/15）。此前 **2347 passed, 7 skipped**（2026-07-28 实测=数据飞轮 P0 落地后，分支 `feat/m5-p0-data-flywheel`。python 侧本批 **+24**[evolve 提案半环修复 4（词边界不误伤 eval/三类模板不自触发/动态禁区仍生效/SQLite 详情形状）+ obs 标注载体 10（span↔turn 合并双顺序/gold 标注与批量导出/清理豁免/export 路由不被参数路由遮蔽）+ catalog 裁剪 stats 2 + **D1 契约 2**（真实 manifests 实证：8000 预算下 navigation 等全部无 route_hints 的 agent 被整域裁出 prompt / 16000 零裁剪）+ hint_effect 分类 6]；dashboard vitest **17**（+1 gold 标注保存）+ tsc 零错；HMI node visionFrame **10**（+1 LBS 句不抓帧反例锁）；eval_route_hints **122/122**（语料 +6：vision 劫持 guardrail×2 + 召回×4，基线随语料刷新）、eval_fast_intent 57/57 无涉及；见下方「数据飞轮 P0」行）；此前 **2323 passed, 7 skipped**（2026-07-27 实测=声纹面真机第四批 + 乘员维度盘点后。python 侧本批 **+6**[chitchat 身份直答 4（认出即直答 / 认不出不硬答 / 不劫持含「我是谁」的别的意图 / 礼貌前缀与语气词）+ planner **祈使指令不接受 not_addressed** 2（七种祈使前缀全覆盖 / 「我不记得了」这类陈述不得借此绕过拒识）]；HMI node **214**（+11：pcmRecorder 源码级契约——采集约束与 vadEngine 逐字一致、两个端点默认必须 pcm16le、设置页不得再用 MediaRecorder、头尾静音只切两端不在中间挖洞）；见下方「声纹面真机第四批」行）；此前 **2306 passed, 7 skipped**（2026-07-26 实测=声纹面真机第三批修复后。python 侧本批 **+2**[**真人实测值钉成回归**：同人 0.5243/异人 0.1157 必须 accept、纯噪声 -0.0585 必须拒]；主战场在 HMI node **203**（+9：语音段门控 4 + 端点补发 3 + 认不出不叫名字 1 + 既有 16 条改按真实调用序喂帧——**它们此前默认「帧即有效语音」，正是放过这个 bug 的原因**）；见下方「声纹面真机第三批」行）；此前 **2304 passed, 7 skipped**（2026-07-26 实测=Skill 层闭环补全后，本批净 **+6**：test_skills 13→19 用例[few_shots 渲染进注入块/未知顶层键告警不拒载/被裁 guide 诚实标注不谎称注入/hybrid 语义补位 paraphrase/embedding 不可用 fail-open 回词法/plan_skills 名单通道+裁剪契约]）；此前 **2298 passed, 7 skipped**（2026-07-26 实测=声纹面真机第二批修复后，本批净 **+11**：memory 声纹 6[空名保留已有名/同名重录不堆积记忆/改名改表也改记忆/改名不动模板/改名拒空名与未知乘员/删 primary 撤回注册写的名字] + 网关 CORS 契约 5[**注册了什么方法就必须允许什么方法**/删除与改名可达/真跑一次 OPTIONS 验响应头/两条路由存在]。注：下方 2189 是 2026-07-25 时点值，其间总体验收批次的新增用例未单独登记，故差值不止本批）；此前 **2189 passed, 7 skipped**（2026-07-25 实测=M4 S2S 线 P0→P3 落地后，净 **+67**：S2S 网关 67[对上协议/事件映射逐条/工厂拒 tools 不支持型号/三层打断含残包丢弃与 abandoned/重连重注入与 DEGRADED/**turn 悬挂看门狗**/回灌两条易漏项（escalated 轮不重写 memory、被打断轮只存已播增量）/**源码级铁律 3**（会话层零领域字面量、协议恰好一个工具、inject 不得 create response）] - 其余并入既有用例；HMI 另有 node **171**（143 既有 + s2sClient 28）；见下方「M4」行与 `docs/design/2026-07-25-m4-s2s-fullduplex-rfc.md` §11.4）；此前 **2122 passed, 7 skipped**（2026-07-25 实测=M3 全期落地后，净 **+81**：主动治理器 22[六道闸/合并/延后/三态与 scene 对齐/**零 kind 字面量源码断言**] + 主动客户端与迁移护栏 6[fail-open 三态/**全仓不得直发老主题**] + 低电量生产方 8 + 位置提醒 28[placeparse 词形与 ETA 让路/haversine/围栏边沿播种/**原子领取不重复触达**/Agent 级诚实追问] + MCP 桥 17[三重准入/写操作五项/真子进程协议往返/演示标注三重冗余] 见下方「M3」行与 `docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md` §10.6）；此前 **2041 passed, 7 skipped**（2026-07-25 实测=M2 后半记忆图谱落地后，净 **+119**：偏好加权 40[weighting 纯函数 22 + 巩固期加强 7 + 注入渲染契约 11]、关系边 41[封闭词表/一跳解析/**GDPR 级联红线** 28 + 人称目的地 13]、routine 时间加权 5、emotion 21 + TTS instruct 4 + 其余并入既有用例；见下方「M2 后半：记忆图谱」行）；此前 **1922 passed, 7 skipped**（2026-07-25 实测=M2 核心件 P0/P1/P2 落地后，净 **+135**：Task Ledger 59[`_sdk/test_ledger.py` 36 纯函数+分支接线 / deep-research `test_ledger_integration.py` 23 话术与流水线] + Outcome Verifier 44[`test_verify.py`：两求值器三态、retry 授权、**中央零领域字面量源码断言**、**流式直通必须调对账**、即插即用、R9 口径、聚合器话术] + 云侧车况镜像 8 + T2 分档与防抖 17 + pipeline 心跳/停手钩子 4 + registry round-trip 保 verification 3 - 其余并入既有用例；见下方「M2 核心件」行与 `docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md` §9）；此前 **1739 passed, 9 skipped**（2026-07-24 实测③=M0b Full Migration 后，净 +1 用例[默认 full 契约]；skip 9=7+2 环境敏感项让路——本跑与 planner 容器重建并发，同实测①波动形态，零失败；此前 **1740 passed, 7 skipped**（2026-07-24 实测②真栈在场，含 **M0b Skill 层 +11**（`test_skills.py`：加载/词法检索命中/反例静默/渲染预算/**即插即用契约**/SKILLS_MODE 四态注入）；skip 9→7 证实实测①的 +2 确系全栈未起的环境波动（栈起即转 passed）；此前 **1727 passed, 9 skipped**（2026-07-24 实测①，含 **M0a 数据真实性+确认兜底批 +12**：navigation outage 诚实降级 4 + 无 mock 兜底字段结构测 1、charging_planner outage 2 + 字段结构测 1（原「Provider 失败→降级 mock」用例改写为诚实契约）、编排 `test_capability_confirm.py` 4（LLM 不可增减确认级 / Agent 漏标 manifest 兜底 / 下游只升不降 / confirmed 不开执行旁路）；nearby 试点 2 条断言 FAILED→OK 对齐 R9 契约；**skip 7→9 为环境可达性波动（本次全栈未起），与本批无关、栈起复核**；见 `docs/design/2026-07-24-eva-benchmark-intelligence-upgrade.md` §6 M0a 落地记录）；此前 **1717 passed, 7 skipped**（2026-07-19 实测，含**赛事域进球明细+预测焦点批次 +5**——info 进球指代/焦点日期锚定/完赛短路 3 + 路由回归「(这\|那)场×预测词」召回与 guard 反例 2，见下方「赛事域进球明细+预测焦点批次」行与 `docs/design/2026-07-19-sports-goal-detail-and-predictive-focus.md`）；此前 **1712 passed, 7 skipped**（2026-07-18 实测，含**三轮真机反馈：提醒句被体感共现规则劫持修复 +6**——edge `_is_reminder_utterance` 提醒话术全局让路 + HVAC 显式词形收窄（978f756），见 `docs/design/2026-07-18-voice-interrupt-context-tts-batch.md` §6）；此前 **1706 passed, 7 skipped**（2026-07-18 实测，含**语音打断/上下文关联/TTS 播报三主题 +10 与二轮反馈 +1**（`fix_relative_year` 纠偏 6 断言；天气卡 focus / 预测词表放宽并入既有用例）：weather 日期感知纯函数 2 + handler 级「明天答预报/超窗诚实」2 + sports 猜族预测让路+指代锚点 1 + context 焦点 last_intent 契约更新 1（替换原 empty-returns-none）+ VAL 多意图名词式话术 5，见下方「语音打断/上下文/TTS 播报批次」行与 `docs/design/2026-07-18-voice-interrupt-context-tts-batch.md`；另 hmi node 143（+6：voiceLoop barge-in 动态 pre-roll 3 + ttsQueue speechCovered 3））；此前 **1629 passed, 7 skipped**（2026-07-15 实测，含**旅程遗留卡收口 +13、尾项 +6、墙钟 badcase +5 与 reminder 原话优先护栏 +2**（kind=todo 槽位盲信 @M3 canonical 抓到，显式「提醒/叫我」不被槽位改写成待办）：A1-4 挂起前缀契约 6（`test_suspend_prior.py`：前缀/单步不变/续接不复读/短回执过滤/loop 跨迭代/流式+种子排除）+ B3-3 记忆 7（M1 场景参数确定性过滤×3 + M2 谓词等价类 supersede + 显式陈述立即抽取 + 归一断言更新×2）+ timeparse 段位默认 6（「日+段位」明早/明晚/明天下午默认时刻成单，裸日期/裸段位仍追问）+ chitchat 墙钟直答 5（`_clock_answer` 词形/口语时刻分段/handle+stream 零 LLM/锚含星期时刻），见「旅程级验证体系」行遗留卡收口段）；此前 **1603 passed, 7 skipped**（2026-07-15 实测，含**旅程红灯修复三批 +24**：R4 回忆式 guard/R9 降级契约改 OK×3 更新/Q3 日期解析×2/Q2 余量边界/R1 强校验×3+城市门×2/R2 中断-恢复契约×4/R3 焦点中心解析/R5 端侧让路×5/R7 REMINDABLE 写入+一刻钟/R8 雨天室内双分支/B2-3 序号回填，见下方「旅程级验证体系」行红灯修复段）；此前 **1579 passed**（scene custom_params 槽位消费修复 +3：真栈 plan 原样 fixture/原话优先/垃圾槽值忽略）；此前 **1576 passed, 7 skipped**（2026-07-14 实测，含**场景编排重设计主题 +161**：scene 单测 159[catalog/store/compiler/solve/verify/triggers/agent 七件套，含评审修复回归 8] + 端侧场景句护栏/分发修根/VAL 氛围灯修复等 edge 增量 + server_dispatch 拒绝浮出用例，明细见下方「场景编排重设计」行与 `docs/design/2026-07-14-scene-orchestrator-redesign.md` §0.5/§0.6）；此前 **1415 passed, 7 skipped**（2026-07-13 实测，含 **badcase ⑥ 天气×出行 +8**（实时意图先答 7 + road-safety 无目的地一般建议 2 - 原 NEED_SLOT 契约 1）、**LLM 消耗归属视图 +1**（collector llm_summary 聚合/SUM 排序/盲区标注）与 **真机 badcase 五修复 +23**（下午续批：合成风控收窄重试 3 + 温度问句让路 5）（grounding 兜底/消毒 6 + chat 4xx 响应体 1 + 天气意图先答 8）与 **LLM 消耗排查主题 +25**：registry 转变沿告警/长期不健康剔除 3 + 批处理音频工厂矩阵/流式桥接/WAV 提取 20 + 合成会话跳过抽取/抽取归属 2；此前 **1335 passed, 7 skipped**（2026-07-12 实测，含**四模式路由与回答质量重设计 +29**：eval_mode_routing 确定性子集与语料 schema、engine escalate 契约 6、chitchat marker/流式缓冲/depth 5、搜索质量（薄证据重试/新鲜度重排/top6）5、研究深化（backtrack 合并/种子跳检/save urls）5、news thinking/livecrawl 2、planner prompt 拼接 1 等，明细见下方「四模式路由与回答质量」行与 `docs/design/2026-07-12-mode-routing-and-answer-quality.md`）；此前 **1306 passed, 7 skipped**（2026-07-11 实测，含**智能提醒 Agent 主题 P0+P1a+P1c 全量 +137**：reminder 单测 102[timeparse 50+lead/store 10/scheduler 6/agent 36 含跨域 8]+sports 生产者 4+集成期改动，明细见下方「智能提醒 Agent」行与 `docs/design/2026-07-11-reminder-*` 三篇）；此前 1169 passed, 7 skipped（2026-07-08 实测，含 **R4.4 P0 拒识 + P1 澄清 +23**：**P0 拒识主链**（置信度三段式「低置信=拒识」：受话判定合并进 Planner 同一次 LLM 调用[`addressed` fail-open]，engine 对 hands-free 语音源[`meta.input_source=voice_*`]+`addressed=false` 静默短路[rejected 卡·空 speech·不 TTS·不落库不进画像]，显式输入永不拒；真栈 `e2e_rejection.py` 2/2、`eval_rejection.py --live` @ mimo 正例误拒 3.4%/负例拦截 88.9%/JSON 0%）；**P1 澄清主链**（「中置信=澄清」：真歧义句出 `intent_choice` 卡[零会话状态·深度=1·`clarify_resume` 抑制]，`_fallback` 语义 top-1 加 `CLARIFY_FALLBACK_MIN` 分数门槛[低分诚实降级不硬执行 capabilities[0]]；真栈 WS CLARIFY on →「处理一下停车的事」出「缴纳停车费 vs 找附近停车场」卡，**CLARIFY off 默认交付零行为变化**；`--clarify --live` 反例误澄清 0/17）；解析/门槛 `test_planning_reject.py` +15、engine 短路/落库/澄清 `test_engine_reject.py` +8；`CLARIFY_ENABLED` 默认 off 真栈 CDP 验收后翻 on；见 `docs/design/2026-07-07-r4.4-rejection-and-clarification.md`）；此前 1146 passed（2026-07-07 实测，含 **多 LLM 源 + TTS 扩展 + 赛事国旗 + 真机修复 +34**：**复杂多意图 3 修复**（navigation 地标 navigate 并发挤高德 QPS 超时→budget 5s→20s + 地标解析走 @fast、Planner 把「像笋」错猜成「京基100」写 dest 绕过解析器→`_correct_planner_landmark` 用原话官方名覆盖 +2、parking-payment 的 `parking.find` 是与 nearby 重复的 mock→移除·停车发现归 nearby 真高德；见 `docs/design/2026-07-07-complex-intent-landmark-parking-fixes.md`）；`llm-gateway/tests/test_llm_runtime.py` 多 provider 注册表/per-provider body 构造/档位解析/全局切换/qwen 复用百炼 key +9、`test_tts_stream.py` 句级切分/MiMo·MiniMax 流式 TTS 工厂路由与 SSE 解析/**MiniMax status=2 汇总帧去重防双份** +9、赛事国旗 +1、`test/test_sports_nearby_routing.py` **赛事追问「那一场…详情」不被周边劫持**（对真实 manifest 跑 RouteHintEngine）+3、chitchat 档位化改断言；真栈四家 LLM 全通（含 **DeepSeek v4 推理模型须 `thinking:{type:disabled}` 关思考防 content 被 reasoning 饿空**）、**MiniMax/MiMo TTS 试听须进 `hmi/src/audio.ts::STREAMING_TTS_PROVIDERS`**、**Windows Chromium 国旗缺字形→自托管 Twemoji Country Flags 字体修复**；见 `docs/design/2026-07-07-llm-asr-tts-multiprovider-and-sports-flags.md`。此前 1112 passed, 9 skipped（2026-07-06），含 **R4.2 服务端流式 TTS +16**（`llm-gateway/tests/test_tts_stream.py`：帧构造/mock provider/工厂路由/FakeWS 全循环，全离线）；2026-07-04 为 1069 passed, 7 skipped，含 **R4.1 路由质量主题**：P0 Registry 真语义路由（+16）+ 语义重排修 P0 遮蔽（+3）+ P3 纯 pattern 扩规则（B1 气象/B2 设置页族，规则改动不新增 pytest 用例）+ R4.1b P0 端侧对象化 3 对象（不新增 pytest 用例，靠 edge_regressions/真栈护栏）——共 +19，详见 §4 末尾 R4.1 行；含 R4.0 收尾包 K1/K2/N1（+2 通道自愈单测 `test_cloud_client_reconnect.py`）；此前 2026-07-03 为 1037 passed, 6 skipped；含 R2 架构还债 R2.1-R2.5 + R3.1 会话鉴权 + R3.2 服务间 mTLS + R3.3 e2e 入 CI 门禁 + R3.4 意图路由评测基线（新增 `test/test_eval_common.py` +7）+ R3.5 降级矩阵自动化（R3.3/R3.5 不新增 pytest 用例，计数不变），详见本表末行「全仓审计与 Roadmap」），以及 2026-06-27 的**信源名单扩展+新闻质量/时效/展示/繁转简**[采纳 `docs/research/2026-06-27-source.md` 扩 tier 名单(官方数据/统计/标准/学术基础设施+权威媒体，仅静态白名单不落运行时评分DB)；新闻二轮收敛：综合要闻走 **Google News 头条+Exa 合并**(Exa 语义检索对今日头条方差大且多返门户版块页、Baidu 多旧闻垃圾)、`_extract_news_subject` 子串判防泛新闻误抽伪 topic 走 Exa、`_rank_news_quality` 沉农场+时效+**来源多样性上限(每源≤3，不按 tier 优先防单源刷屏)**、`_normalize_publish_time` 相对转绝对 ISO+`_recent_only` 丢>3 天旧闻、`_is_junk_news` 按首段滤门户版块名、卡片补 summary(近重复去重)+清「-36氪/｜公視新聞網」尾巴+`clean_snippet` 去 markdown(防「# 中东突变！」)、**繁→简 zhconv**(台/港源标题转简体；先试 LLM 转换稳定 DEADLINE 改确定性库)；真栈「今天有哪些值得关注的新闻」**最佳跑 10 来源/今日/农场 0/话题多样**(Exa 综合查询方差大、稳定多源需策展 News API)]；含**信源质量加权**[`_sdk/source_quality` 域名权威分层 3 学术/官方/百科·2 权威媒体·1 默认·0 内容农场→深调研 synthesize 合成前重排每子问题证据(定 top-N 入材料)+`_assign_global_sources` 全局权威编号([1]=最权威)、共享 `grounded_synthesis` info.search 同源、深度异步薄结果用 Exa research-paper 类目学术兜底；真栈探针前5来源平均档位 1.00→2.80]；含**异步分钟级深调研**[显式延后信号→受理即返回+后台 `deep=True` 流水线（子问题 9/合成 4000 tok）越过 ~90s 同步上限+经 NATS `agent.proactive` 推**带 card 的报告卡**（网关纯 JSON 透传无需改 proto），真栈受理秒回→分钟后 9 节/36 源/~3031 字主动推送；可发现性=同步出报告后 follow_up 主动教「慢慢查、查完告诉我」转异步]；含信息域深调研 P0-P2+实测修复[新闻个性化画像排序/「详细讲讲第N条」深挖桥接 research.run/主动早报雏形(晨间起步发 agent.proactive)；独立 deep-research Agent 四段流水线 + 接地「我」位置反查/画像召回 + 多轮深挖「展开第N点」聚焦不重跑 + 存记忆 + 上线后实测修复(合成关思考防超时退化/去电量约束防主题漂移/去 livecrawl+简短子问题防 Exa 超时/清网页噪声纯文本)，deep_research 20 + 编排 research.run/深挖路由 3 + 端侧「电池」误匹配收窄 2，info 切 _sdk 共享内核零回归]；含 trip-planner 结构化重构 P0/P1/P2；早前复杂任务 thinking 透传/过程区 is_complex 与摘要脱敏单测；含 info/导航 provider、位置授权与反地理、天气预警/空气质量、UI 卡片链路、股票 A/港/美股、Exa 正文级检索+接地合成诚实弃权、api-football 赛事路由（按日期查+中文队名）+「第N场/队名→进球详情」（射手/分钟，剔除罚丢点球）+「射手榜」（topscorers 赛季回退标注）+「总/历史射手榜」改写 query 走搜索+多轮联赛 history 回填、导航顺路用餐 stop_category→waypoint_choice 候选选择→navigate.waypoints+route_plan 路线卡、新闻 Exa 优先+去重、AnySearch extract、搜索/新闻/赛事证据卡、充电高德沿途途经点规划+charging_route 卡、泛地点高德候选二次确认（dest_choice）、导航视觉地标经共享件解析地图官方名+name_matches 校验（拒高德对俗称返回的邻近无关 POI）、类目搜索不被整句多意图劫持、充电按目的地（地标先解析官方名）搜途经点+聚合器并入 navigate.waypoints/去重、聚合器卡片择优、独立 Agent、ws2/ws8、场景动作经 VAL 执行、road-safety 主动播报节流回归、行程规划结构化重构 P0/P1（LLM 提议骨架→确定性接地/求解四段流水线、每停靠点可导航 trip.navigate/下一站、结构化 edit-op 加删站、落 memory，见下方 trip-planner 行）、确认词「占据整句」判定（"行程"含"行"等子串不再误判成确认）、孤儿确认不重规划、跨 Agent meta 透传（定位/电量）+ 子 Agent ui_card Struct→dict 修复） |
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

### CI / nightly 现状（2026-08-01，**两条都已收口**）

**✅ CI 已收口**：run **#232**（`176dd20`）**七个 job 全绿**（含此前被 fail-fast 取消的
python-tests 3.12），是 `#217` 之后的第一次绿。破点到收口共 15 次红。
**✅ nightly 已收口**：`c75df13`，mock 全栈实跑 **9/9 PASS**（此前 4 红）——⚠ 下一次
定时跑（UTC 18:00 ≈ 次日 02:00 CST）才会在 CI 上得到确认。

**判据先行：本地全绿 ≠ CI 绿。** 本地习惯单进程跑全量，CI 是 **Ubuntu + 分组跑**
——两个差异各自藏着一类缺陷。这轮最后 7 条里，**有两条是真代码缺陷不是测试问题**
（首次 canonical 晋升在 Linux 上必崩、go wrapper 在 Linux 上一直是坏的），
它们能在 Windows 上躲这么久，都是因为一段 `if os.name == "nt": return` 把校验整层跳过了。

**怎么复现 Linux CI（下次直接照做，比读 annotation 快得多）**：
```bash
git bundle create /tmp/repo.bundle --all          # 要带真历史，git archive 不含 .git
docker run -d --name ci-repro python:3.12 sleep infinity   # 用完整镜像，slim 没有 git
docker cp /tmp/repo.bundle ci-repro:/tmp/ && docker exec ci-repro sh -c \
  'git clone -q /tmp/repo.bundle /repo && pip install -q pytest pytest-asyncio pyyaml cryptography websockets'
docker exec ci-repro sh -c 'cd /repo && python -m pytest scripts/tests/ -q --import-mode=importlib'
```
`test_run_go_tests_wrapper` 还需要 pwsh（`_powershell()` 找不到就 skip，**skip 会伪装成绿**）：
从 GitHub Releases 拉 `powershell-7.4.6-linux-x64.tar.gz` 解到 `/opt/microsoft/powershell/7`
（国内约 35KB/s，用 `curl -C -` 断点续传分多次拉）。
⚠ 容器里有 5 条会假红（无 init 收割僵尸进程的三条 reap 用例 + 两条缺 test 支撑模块），
**它们不在 CI 失败集里**——比对时以 CI 的清单为准，别追容器自己的噪声。

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
| 怎么查（仍然有效） | `python scripts/ci_annotations.py [run_id]` 读 annotation（**免 admin**；不带参数=最新一次 run，刚 push 时那次可能还在跑，要显式给 id）。⚠ **annotation 只有 pytest 的摘要行，长断言会被截断**（go wrapper 那条的 argv diff 就看不全）——**真要定位就起 Linux 容器**（上方复现步骤），在容器里加一行 `traceback.print_exc` 三秒就看见了。⚠ **不要把诊断细节塞进 `canonical_rejection_reasons`**——那是契约字段，已有测试锁死「只有这一项」，前人试过被三条测试拦下（改走 stderr 也撞了 runner 的输出契约） |
| nightly | **✅ 已收口（2026-08-01，`c75df13`）**：自 `#30`（2026-07-29）连红三次，根因**一个**——M5 P2 那批 route_hints 退役（32→12→10）把 mock 车道的确定性基础抽掉了，而退役判据是在**真 LLM 双臂**下取的证，从没覆盖 mock 车道。mock 全栈里 MockProvider 只回显原话 → 规划必走 `_fallback` → 兜底 chitchat，于是 5 个脚本同时失效（trip / context 2 例 / degrade agent_down / journeys A4-2+B4-2 / ws 链路4a）。**那些「端到端路由」断言此前一直是正则在撑。** 修法=按新判据收窄（下方），mock 全栈实跑 **9/9 PASS**。⚠ 下一次定时跑（UTC 18:00）才会在 CI 上确认 |
| nightly 复现法 | 不要动 `.env`（红线）。**把空 key `export` 给跑 runner 的那个 shell**——子进程继承，而 compose 插值里 shell 优先于 `.env`：`export LLM_API_KEY= LLM_EMBED_API_KEY= MINIMAX_API_KEY= DEEPSEEK_API_KEY= DASHSCOPE_ASR_KEY=` → 重建 llm-gateway（`--force-recreate --no-deps`）→ `python scripts/run_e2e.py --lane nightly --full --stale-policy warn`。⚠ **只在 `docker compose` 那一行加前缀是不够的**：`e2e_degrade` 自己会重建 llm-gateway，会把真 key 读回来（我第一次就栽在这，跑出三条假绿）。跑完再 `--force-recreate` 一次即恢复真 provider |

**mock 车道的判据（2026-08-01 被推翻并改写，nightly 收口的核心产物）**：
原判据是「确定性路由：**route_hints 可达**」（旅程体系设计文档 2026-07-14 §4.3）
——它把「有规则撑着」当成了「不依赖模型」，而 route_hints 是**会被数据退役的**。
**新判据：mock-safe ⟺ 这条路径不经过模型判断。** 只有四类：①端侧快路径
（`fast_intent` + VAL，零 LLM）②兜底 Agent（`PLANNER_FALLBACK_AGENT`，
`planning._fallback` 第一分支由**结构**保证被路由到）③确定性解析与流程态短路
（timeparse / 挂起态裸确认 / 注入检测）④协议传输层。**「它有 hint 撑着」不算。**
更一般的一条：**在 mock 栈上断言「模型选对了 Agent」，测的永远是规则不是系统。**

**四条可复用判据**：
1. 跨平台 CI 里，被 `os.name == "nt"` 提前 return 掉的校验，正是另一边会先触发的那一层
   ——**「本地绿」只证明本地那条分支绿**。这轮 7 条里有 6 条是这个形状。
2. **一段「吞掉整类异常」的兼容代码，会连它不该吞的那一种一起吞掉。**
   `_fsync_directory` 对 nt 吞 `OSError` 本意是「Windows 没有目录 fd」，结果把
   「目录根本不存在」也吞了——于是一个顺序缺陷（先恢复后建目录）在 Windows 上
   永远不可见。吞异常要按**具体错误码**吞，不按平台整段吞。
3. **拿不到失败理由时，先问「是没有诊断通道，还是没有那个环境」。** 这批红了半个月，
   期间试过往契约字段塞理由、试过走 stderr，都被拦下；真正的解法是花二十分钟起一个
   Linux 容器——**在能复现的地方，`traceback.print_exc` 就够了**。
4. **一个治理动作（退役规则、改判据、收窄清单）要问一句「谁在靠它」。** M5 P2 退役
   hint 时记了「离线 eval 的召回保护降级为人工触发」这笔账，但**没人问 nightly 靠不靠
   这些 hint**——结果它当晚就红，连红三次没定位。退役/收窄之前，先 grep 一遍谁把它
   当前提写进了注释里（A4-2 的「mock-safe：route_hints 确定性路由」就明写着）。

---

## 5. 第一步（任何人接手都先做这个）

```bash
cp .env.example .env        # 可选填 LLM_API_KEY；不填走 mock 也能跑
make proto                  # 生成 gen/python + gen/go（没有它什么都跑不起来）
python test/smoke_edge.py   # 验证端侧逻辑（无需 docker，应 13/13 通过）
make up                     # 起全栈（首次需调试，见 docs/dev-guide.md）
```
环境/工具没装齐、Windows 无 make、单服务调试 → 看 `docs/dev-guide.md`。

---

## 6. 改完怎么自检（提交前必做）

| 改了什么 | 自检 |
|---|---|
| 任何 Python | `python -m py_compile <改动文件>`；相关 `python -m pytest <agent>/tests` |
| 端侧逻辑（fast_intent/val/edge_agents）| `python test/smoke_edge.py` |
| HMI / TTS | `cd hmi && npm test && npm run build` |
| Dashboard / 可观测 | `cd dashboard && npm test && npm run build`；全栈后查 `http://localhost:8092/healthz` 与 `http://localhost:5174`；badcase 贯通链路 `python test/e2e_obs.py`（turn 落库/obs.llm/日志关联/badcase/重启持久化） |
| proto | `make proto` 重新生成，确认 codegen 无错 |
| 端到端链路 | `make up` 后 `python test/e2e_ws.py` |
| 新增 Agent | 契约测试（参考 `agents/navigation/tests`）+ 在 compose 注册 |

不要为了"让它跑起来"注释报错或加绕过标记——找根因（CLAUDE.md §6）。

---

## 7. 最常见任务：新增一个 Agent（最短路径）

1. 复制 `agents/navigation/` 结构到 `agents/<snake_name>/`（包目录 snake_case，agent_id kebab-case）。
2. 改 `manifest.yaml` 声明能力/权限/trust_level/deployment；**若 Agent 需要精确位置/电量等敏感上下文，必须声明 `context_scopes`**（`location` / `vehicle_state`，含调子 Agent 透传的 propagator）——否则编排按最小化下发会剥掉这些键。
3. 继承 `agents/_sdk` 的 `BaseAgent`，实现 `handle()`（**别重写 gRPC/注册**，SDK 已封装）。
4. 写 `tests/` 契约测试。
5. 在 `deploy/docker-compose.yaml` 注册服务（分配新端口，见 `docs/conventions.md` 端口表）。
6. **不改编排核心**——注册后 Planner 自动可路由。

详见 `agents/_sdk/README.md` 与 `CLAUDE.md` §3。

---

## 8. 给 AI 协作者的工作方式

- 动手前读 `CLAUDE.md` + 本文件 + 相关 WS 细化文档；大改动先在设计文档对齐。
- 严格守目录约定与命名（`docs/conventions.md`），不要发明新结构。
- 改接口先改 `proto/` 再 codegen；不手改 `gen/`。
- 每次改动跑对应自检（§6），用证据说话，别声称"应该能跑"。
- 遇到与文档冲突的现状，**先指出冲突**再动手，不要默默绕过。
- 落地某个 WS 前，建议用 `writing-plans` 把该 WS 细化文档转成带 checklist 的实施计划。
