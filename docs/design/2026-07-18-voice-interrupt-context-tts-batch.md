# 2026-07-18 语音打断漏首字 + 上下文关联 + TTS 播报三主题批次

> 状态：已落地（本文档随实现同批提交）。
> 触发：泓舟真机使用反馈三问题——①唤醒模式下播报中打断会漏掉第一个字；
> ②会话 demo-i9c92i 上下文关联差；③TTS 播报整体体验（唤醒应答音色不一致 /
> 多意图车控反馈语 / 长内容断播疑虑）。

## 1. 打断漏首字（hmi 语音回路）

### 根因

SPEAKING 态 VAD barge-in 链路：用户开口 → VAD 判定延迟（silero 起播去抖 64ms+若干帧）
→ `bargeInMinMs=300ms` 打断确认窗 → `_bargeInFire` 才 stopTTS + 开 ASR。此时用户已说了
~400-550ms，而 `openAsr(resume=true)` 固定注入 `RESUME_PRE_ROLL_MS=200ms` 前滚缓冲——
**只够补 VAD 判定延迟，盖不住确认窗**，首字（普通话单字 ~150-250ms）必丢。

### 修复（`voiceLoop.mjs` + `handsFreeController.ts`）

- FSM 在 SPEAKING 收到 `vadSpeechStart` 时记录 `_speechStartAt`；`_bargeInFire` 进
  `_enterListening(fromBargeIn=true)` 时算出 `sinceSpeechStartMs = now - _speechStartAt`
  随 `onOpenAsr({resume, sinceSpeechStartMs})` 带给控制器（判据用 `speechAlreadyStarted`
  而非时间戳真值——fake clock now()=0 也成立，同 `_currentUtteranceMs` 手法）。
- 控制器 pre-roll 改动态：`min(200 + sinceSpeechStartMs, MAX=1200ms)`（pcmRing 容量
  1500ms 内留余量），barge-in 路径回取到 speech 起点，首字找得回。
- 非 barge-in 的续问/宽限续说传 0 → 维持原 200ms 短 pre-roll（不带上轮 TTS 尾音）。
- KWS 唤醒进入维持 pre-roll=0（P4 定论：回取会把唤醒词本身喂进 ASR→「小周」误上屏；
  KWS 解码窗自身的 ~100-200ms 尾差属声学层，pcm 帧自 openAsr 起已全量缓冲直发）。
- 附带收益：VAD barge-in 抢在 KWS 前触发时（唤醒词打断播报），动态 pre-roll 现在能取到
  **完整**唤醒词（而非 200ms 截出的「舟小舟」残段），`stripLeadingWakeWord` 按整词剥更稳。

测试：`voiceLoop.test.mjs` +3（barge-in 带 300ms 耗时 / 续问传 0 / 短开口清陈旧起点），45/45。

## 2. demo-i9c92i 上下文关联（12 轮 5 处失败的系统性修复）

### 病灶盘点（collector 真实 trace）

| 轮 | 用户 | 系统行为 | 根因 |
|---|---|---|---|
| 3/4/12 | 「明天还会下雨吗」×2、「明天的呢」 | 三连答**今天实况** | Planner 已产出 `info.weather {date:明天}`（trace 可证），但 `_weather` **从不消费 date 槽位** |
| 6 | （世界杯问句后）「明天呢？」 | 错绑回天气域、还答今天 | 纯信息轮不落焦点（`extract_focus` 对无对象/POI 轮返 None）、prompt 无省略式追问规则，LLM 对 2 轮裸历史掷硬币 |
| 9 | 「你猜一猜…结果大概是怎么样呢」 | 重播赛程列表 | `_PREDICTIVE_HINT` 无「猜」族 → 未让路搜索 |
| 10 | 「预测…这一场…结果」 | 答成**决赛**预测（问的是季军赛） | 让路后原话裸传检索，「这一场」指代未锚定到上文场次 |

### 修复

1. **weather date 槽位落地**（`agents/info/src/handlers/weather.py`）：
   `_requested_day_offset(slots.date, raw_text)` 解析 明天/后天/大后天/明早晚/周X/下周X/
   周末/ISO（槽位优先、原话兜底，周X 口径与 `sports._sports_date` 一致）。offset>0 →
   从 `overview.forecast`（和风 3d，已在拉）取该日作答：`_day_answer` 意图先答（该日
   会不会下雨/适不适合出行）+「{地点}{明天}{白天}转{夜间}，{低}~{高}℃{风}」；超预报窗
   → 诚实「还查不到，临近再问」，**绝不拿今天实况顶包**。卡片契约不变（weather 卡含
   forecast 区）。
2. **省略式追问延续上一轮**（编排通用机制，无 Agent 硬编码）：
   - `planning.py` prompt 通用规则 +1 条：省略句（「明天呢」）=对最近对话**最后一轮**换
     时间/对象重问，沿用其意图能力只换槽位。
   - `context.py`：`Focus.last_intent` 纳入 `is_empty` 判定（纯信息轮也落焦点）并渲染
     「上一轮意图=info.sports」进焦点块——给 LLM 确定性的最近域信号，不再依赖裸历史。
3. **预测让路补全 + 指代锚点**（`sports.py`）：
   - `_PREDICTIVE_HINT` += 猜一猜/猜猜/你猜/猜测/胜算/赢面/会赢/能赢/几比几。
   - 新 `_predictive_anchor`：让路搜索前把「这场/那一场/明天那场」解析成具体对阵——
     联赛（原话→对话历史回填）+ 日期（`_sports_date`；纯指代按今天→明天顺序试，免费档
     恰放行 ±1 天）→ fixtures 取到 1-2 场 → query 追加「（用户问的是明天的FIFA 世界杯
     季军赛：A vs B）」。解析不出返回空串按原话走，绝不阻塞。

测试：weather 纯函数 +2 组（offset 10 断言/day answer 5 断言）+ handler 级 +2
（明天答预报无「当前」/大后天超窗诚实）；sports +1（猜族命中 + 今天空明天有场次的
指代锚点拼装）；context 契约更新 +1（info 轮 last_intent 落焦点并渲染）。

## 3. TTS 播报整体优化

### 3a 唤醒应答与正文音色不一致

根因：唤醒/退场提示音走批处理 `/api/tts`（仅 MiMo），流式引擎（cosyvoice/qwen/minimax）
的音色批处理没有 → 旧逻辑一律回落 MiMo「冰糖」，与正文（默认 cosyvoice 龙小淳）不同声。

修复（`audio.ts` + `handsFreeController.ts` + App effect）：`prepareCueSet` 增 provider 参；
流式引擎经**一次性 `/api/tts/stream` 会话**（start→text→finish 收全 PCM 拼 WAV blob）用
选定引擎+音色预合成提示语，逐条串行（防并发顶限）；单条失败回落 MiMo 批处理（保真人声）；
wake 全空才抛回退 beep（原契约不变）。App 的 refreshWakeCue effect 依赖补 `ttsProvider`。

### 3b 多意图车控反馈语

现状：批次内每动作独立选话术再「，」拼接。默认 short/standard 选 brief →「开了，好的」
（无法归属是谁的回执）；detailed 选 full 且随机 →「已为您打开空调，已为您打开车窗」
（礼貌式堆叠冗长）。

修复（`val.py` + `server.py`）：`VAL.execute(..., multi=True)`（快路径 A 多意图、混合路径
A2 由 server 按批次规模传入）→ `_pick_response` 强制 full 并**优先名词式变体**（跳过
已为您/已将/正在为您开头；仅礼貌式变体的模板回退原样）、去随机。合并读作
「空调已开启，天窗已打开」。单意图行为不变。测试 `test_val_multi_speech.py` +5。

### 3c 长内容断播排查与修复

服务端证据：72h llm-gateway `TTS stream` 日志零 error 零 cancel（服务端合成链路健康；
长回复 first=11-48s 是上游 LLM 时延非 TTS）。真实断口在 **HMI 客户端**三处：

1. **混合意图轮丢云端段（最大实锤）**：本地回执 final 先到 → `finishTTSReply` 把流式
   会话收尾；随后云端 speech_delta/final 仍灌进这条**已收尾的会话**（finish 帧已发，
   文本进不去）→ **云端回答整段无声**。批处理路径反而有「先播反馈尾巴再播总结」语义，
   流式路径缺失。
2. **divergent final 整段重发**：final 与流式增量不同（md 剥法差异等）时旧逻辑
   `tail=full` 整段重发 → 复读。
3. **主动播报（异步深调研报告）**：空闲时 `finishTTSReply(text)` 无活跃会话 → 静默不响；
   忙时灌死会话丢失。

修复（`audio.ts` 段链机制）：
- `StreamingTtsSession.spent`（done/fellBack/disposed/finish 已请求）+ `finish()` 返回
  divergent 布尔（`speechCovered` 归一化比对：化妆品级差异=已覆盖不重播；两段话=链下段）。
- 段链 `chainSegs`：spent 后到达的 delta/final、divergent 的最终文本、排队的主动播报
  各成待播段；当前会话 `completion` 后逐段**轮转**成新流式会话接着播（`_armRotate`/
  `_rotate`，同参数同音色；轮转段失败回退批处理）。`stopTTS`（barge-in/新一轮）清链。
- `markTtsMaybeEnd` 对链上有待播段时重挂 250ms 再判——段间空隙不误发 onEnd（FSM 不会
  在多段播报中途掉出 SPEAKING）；链清空后正常收尾，不悬死。
- 新 `queueTTS`：主动播报专用——空闲即播、流式忙时链其后、批处理忙时按序入播放队列，
  **绝不打断在播回复**（旧行为之一是被动打断源）。App proactive 分支切换到它。
- 纯函数 `normSpeech/speechCovered` 落 `ttsQueue.mjs`（node 可测，+3 例）。

遗留（有意不做）：流式中途 provider 错误且已播出部分音频时，`fallback` 维持「不整段重
合成防复读」——已播文本与 PCM 无法精确对齐，重播风险大于收益；72h 日志零发生，观测到
再议。

## 4. 验证

- hmi：`npm test` 143/143（+6：voiceLoop 3 + ttsQueue 3）；`npm run build` 过；tsc 仅
  预存 `.mjs` 无声明噪声类（TS7016/TS2305 与 cardMath 同款），无新增错误类别。
- Python：orchestrator/cloud + agents/info + orchestrator/edge 776 passed；全量套件见
  AGENTS.md §4 最新计数。
- 真栈：重建 hmi / cloud-planner / info-agent / edge-orchestrator 容器后 WS 探针复验
  demo-i9c92i 病灶句（明天天气/省略追问/预测指代），4/4。

## 5. 第二轮真机反馈（同日，泓舟复验发现两问题）

### 5a 天气卡片仍以今天实况为主视觉（ad377bed/be27f935/ae9477c5）

§2 修复后 speech 已按日作答，但 weather 卡头部大字仍是今天实况温度。修复：

- 后端 `_weather` 命中未来日时在卡片下发 `focus`（date/label/该日预报字段），
  今天实况字段原样保留（契约向后兼容，旧 HMI 忽略新字段仍可渲染）。
- HMI `WeatherCard` 类型扩 `focus?`；`WeatherCardView` focus 模式：头部城市旁「明天」
  chip + 大字改温度区间（26~30°C）+ 文案该日「雷阵雨转多云」；遥测格换该日字段
  （湿度/风向/降水/紫外线/夜间 + 一格「现在」保留今天实况小字）；3 日预报条高亮焦点日。

### 5b 联网查询年份漂移（f11aa344：「今年世界杯」查成 2024）

根因：**planner prompt 没有任何日期锚**，LLM 把「今年」按训练先验改写成绝对年份
「2024年世界杯决赛预测」灌进 `slots.query`，检索与合成整轮被污染（合成靠自身
「当前时间」勉强自救但答案已是 2022/2024 拼盘）。三道修复：

1. `planning.py` `_date_line()`：规划/再规划 prompt 注入「当前日期：…（今年=2026年）」
   （日粒度，防每分钟扰动；时刻仍归端侧墙钟直答）+ 时效段新规则「相对时间只按当前
   日期换算，绝不凭训练记忆改写年份」。
2. `search.py` `fix_relative_year()` 确定性护栏（第二道防线）：原话含今年/去年/明年/
   前年/后年而 query 的「20XX年」与换算不符且非原话自带 → 改写成换算年份（只动
   「20XX年」词形不碰裸数字/型号）。
3. 复验时抓到第三层缺口：「更看好哪支球队」不含预测词表任何词 → 结构化赛程接走答
   「今天没有比赛安排」，或 planner 落 chitchat 答「我不太清楚」——`_PREDICTIVE_HINT`
   放宽（裸「看好」「夺冠」，词表只在赛事域内消费不伤泛句）+ info manifest 新增
   route_hint d)（联赛/决赛词×预测词共现 → info.sports，经让路→带锚点检索，杜绝
   planner 在 chitchat 与检索之间掷硬币）。

验证：路由三护栏（sports/nearby 契约 3/3、mode_routing 确定性 57/57、route_hints
84/84 无回归、registry_resolve 15/15 无回归）；真栈复验「深圳明天天气」卡带
focus.label=明天、「后天的呢」focus.label=后天、「今年世界杯决赛你更看好哪支球队」
→ search_result 卡 + 本届（2026）半决赛真实赛果接地的双方赢面分析，零 2024。
