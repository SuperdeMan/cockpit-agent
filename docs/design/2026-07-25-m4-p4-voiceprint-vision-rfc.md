# M4 P4 子 RFC：声纹多用户 × 视觉入口

> 日期：2026-07-25
> 状态：**设计（待泓舟拍板 §10 决策点后编码）**
> 依据：母提案 `2026-07-24-eva-benchmark-intelligence-upgrade.md` §4.H / §6-M4；核心件子 RFC
> `2026-07-25-m4-s2s-fullduplex-rfc.md` §8「非核心件边界速写」——本文件是那三行速写的展开。
> 前序：M0a→M3 全部完成；M4 S2S 线 P0→P3 已落地并真栈验证。
> 范围：M4 剩下的两条并行线——**①声纹多用户**（兑现母提案 M4 尚未兑现的那条 DoD
> 「多用户记忆隔离旅程」）**②视觉入口**（「那是什么」单帧图片问答）。
> `sim.adas.*` 维持低优先 backlog（§8-6 拍板），不在本 RFC 范围。

---

## 0. TL;DR 与关键设计判断

侦查后最重要的一条事实：**记忆层的 occupant 维度早就全套就位、且 recall 本来就是按 occupant
硬隔离的**（`memory_item.occupant_id` 列 + `idx_mem_user` 索引 + `pg_store.recall`
的 `WHERE user_id=$1 AND occupant_id=$2`），而**从 HMI 到 Agent 的整条请求链从来没有传过
`occupant_id`**——`PlanContext` 和 `_sdk.Context` 里根本没有这个字段。

推论：**「多用户记忆隔离」缺的不是记忆能力，是身份来源和一条透传管道。** 这决定了本期的
形状——声纹线的大头不是模型，是把 `occupant_id` 诚实地接进既有链路而不碰任何安全判定。

八条锁定的判断：

1. **提取与存储分家**（§2.1）：网关做「音频→向量」（模型面），memory 做「向量→是谁」（数据面）。
   声纹模板是生物特征，必须归记忆域——否则 GDPR 删不干净（M2 `memory_relation` 级联删的同源红线）。
2. **声纹面走独立端点，不焊进 ASR/S2S 链路**（§2.2）：多传 60KB 换关键路径零侵入。
   S2S 会话层刚踩过端点死锁的坑，可选增强件不该再动它。**它必须可以随时死掉**——M3 选落点的
   同一条判据。
3. **边说边识别，不等定稿**（§3.2）：累到 1.5s 有效语音就发识别请求，用户还在说的时候结果已在路上。
   否则要么加首字延迟、要么首句认不出——两个都不接受。
4. **认不出就是认不出**（§3.3）：低于阈值、或 top1/top2 拉不开差距、或有效语音太短 → 一律
   诚实落 `primary`。**认错的代价是把 A 的记忆给 B 看**，比认不出严重一个量级。
5. **首个注册者绑定 `primary`**（§3.4）：否则主驾一注册声纹，历史记忆（全在 `primary` 名下）
   当场全部失联——这是本设计里最容易漏、后果最大的一个回归点。
6. **声纹不作鉴权因子（红线）**（§6.1）：`occupant_id` 只准进记忆域，**绝不进** `granted_scopes`／
   权限判定／VAL／确认闸。用源码级断言测试钉死（同 M3「零 kind 字面量」打法）。
7. **图片走短 TTL 帧引用，永不进对话链**（§5.1）：HMI 抓帧→网关内存 LRU 拿 `frame_id`→
   meta 只传引用。图片不进 proto、不进 obs、不进记忆、不落盘。
8. **采集门控在端侧**（§5.2）：默认不采，端侧命中视觉触发词才抓**一帧**。后端要图再回头拍是
   既慢又错的形态——隐私门控必须在采集侧。

---

## 1. 现状盘点（复用面与接缝）

| 资产 | 位置/形态 | 在本期的角色 |
|---|---|---|
| `memory_item.occupant_id` + `memory_relation.occupant_id`，recall/forget/query_relations **全部已按 occupant 过滤** | `memory/schema.sql:10,65`、`memory/pg_store.py:362-486,594-614` | **隔离能力已就位，本期只补身份来源**；无需改隔离逻辑 |
| `PlanContext`（无 occupant 字段）、`_sdk.Context(session_id,user_id,vehicle_id,memory)` | `orchestrator/cloud/models.py:107`、`agents/_sdk/server.py:63` | 本期加一个字段 + 一条透传路径 |
| meta 透传惯例：`build_context` 的 prefs 白名单 → `ExecuteRequest.meta` → SDK `get_current_meta()` contextvar | `context.py:468-482`、`agents/_sdk/clients.py:33-56` | occupant_id 沿这条既有管道走，**不改 proto 请求面** |
| GDPR `ForgetUser` 同事务级联删 `memory_relation` | `memory/pg_store.py:497-532` + `memory/tests/test_relation.py` | 声纹表照抄这条红线与它的契约测试 |
| llm-gateway 音频面：`/api/asr`、`/api/asr/stream`、`/api/tts`、`/api/s2s`（HTTP/WS 同门） | `llm-gateway/http_server.py` | 声纹面与视觉帧面落同一扇门，鉴权/部署口径复用 |
| HMI `pcmRing`（前滚缓冲，R4.3b B1 治漏字）+ 三路 mic 收敛单路共享流 | `hmi/src/pcmRing.mjs`、`handsFreeController.ts` | **首句 PCM 已经在内存里**，声纹取它不额外开麦 |
| voiceLoop 六态 FSM + `onMetric` 语义事件总线（S2S 期证明可零改动接新语义） | `hmi/src/voiceLoop.mjs`（457 行 / 143 node 测试） | **本期同样一字不改**；声纹与视觉都挂在 controller 层 |
| provider 决议契约（fail-fast + 决议日志 + `REQUIRE_REAL_PROVIDERS` 严格闸） | `docs/conventions.md` §9.4 | 声纹/视觉 provider 照此登记，拿不到模型即诚实禁用 |
| S2S 单工具 `escalate`：非授权域轮级逃逸回文本主链 | `llm-gateway/s2s/protocol.py` | 视觉走**同一条**逃逸路，S2S 面零职责（§5.4） |
| 新 Agent 标准流程（manifest 声明 capability/route_hints/verification，编排核心零改动） | `CLAUDE.md` §3 | vision Agent 照此建，端口 **50077**（现用到 50076） |

---

## 2. 声纹线：拓扑与落点

### 2.1 提取与存储分家（结构性决策一）

```
HMI(首句 PCM) ──POST /api/voiceprint/identify──> llm-gateway 声纹面
                                                    │ CAM++ ONNX：PCM → 192 维 embedding
                                                    ↓ gRPC IdentifySpeaker(embedding)
                                                 memory 服务
                                                    │ voiceprint 表：余弦比对 + 三态判定
                                                    ↓
                                          occupant_id / ""（认不出）
```

- **网关只做模型推理，不持有任何模板**。理由：模板是用户生物特征，扩散到无状态服务就删不干净；
  而 GDPR 硬删是本仓库已经立过的红线（M2 关系边）。反过来说，把 ONNX 推理放进 memory 服务也不对
  ——memory 是数据服务，不该长出模型依赖；且「所有模型调用的唯一出口是 llm-gateway」是既有架构承诺。
- **比对放 memory 而不是网关**：比对需要全部模板，放网关就得把模板下发出去（同上）。而模板数量
  以「一辆车的乘员」计（个位数），memory 侧全表余弦是微秒级，不需要 pgvector 索引。
- **不复用 `memory_item.embedding`**：那一列服务于语义召回、维度与模型都不同，混进去必然污染召回。
  这里的字段级对照结论与 M2「偏好加列不建表」相反——**声纹与记忆条目没有一个共用字段**（无
  predicate/text/confidence/supersede 语义），故建新表 `voiceprint` 是对的，同 `memory_relation`。

### 2.2 走独立端点，不旁路 ASR/S2S 流（结构性决策二）

被否掉的方案：在 `/api/asr/stream` 与 `/api/s2s` 的上行 PCM 上各旁路一份做声纹（零重复上行）。

否掉的理由：

1. 那是两条**刚验完的关键路径**，S2S 会话层上个批次才因为端点语义踩出「说什么都没回复」的死锁；
   为一个可选增强件去动它们，风险与收益不成比例。
2. 旁路要在两处分别实现、分别测试；独立端点**一处实现覆盖两条链路**（挡位无关）。
3. **它必须可以随时死掉**——M3 给主动引擎选落点时立的判据在这里同样成立：声纹服务不可用/模型
   缺失时，整条语音链路必须逐字回落到今天。焊进关键路径就做不到这一点。

代价：唤醒首句的 PCM 上行两次。量化：16kHz/16bit 单声道、首句取 ≤3s → **≤96KB**，与一张卡片图
同量级，且只在**唤醒后首句**发生一次（续问窗不重发，§3.2）。接受。

### 2.3 provider 决议与离线回退

| 档 | 触发条件 | 行为 |
|---|---|---|
| `campplus`（默认） | 模型文件存在且 onnxruntime 可用 | 真实推理 |
| `mock` | 显式配置 `VOICEPRINT_PROVIDER=mock` | 确定性伪 embedding（音频字节 → 稳定哈希 → 归一向量），供离线单测/CI |
| `disabled` | 模型文件缺失 / onnxruntime 导入失败 | **整条声纹面诚实禁用**：`/api/voiceprint/*` 返回 `{"enabled": false, "reason": ...}`；HMI 隐藏入口；`occupant_id` 恒 `primary` = 逐字回落今天 |

- 模型文件（CAM++ ONNX，~28MB）由 `scripts/fetch-voiceprint-model.ps1|sh` **多源**拉取
  （ModelScope → HF 镜像 → sherpa-onnx GitHub release），构建期 COPY 进镜像。
  **本机大文件下载系统性不可靠是有前科的环境事实**（R3.6 记录），故拉取脚本可重跑、失败不阻塞构建，
  由上表的 `disabled` 档兜底。
- `REQUIRE_REAL_PROVIDERS=on` 时 `mock` 档 fail-fast（对齐 §9.4 既有严格闸）；`disabled` 属
  「未配置真实源」不在严格闸范围（与 ASR/TTS 的 off 档同口径）。

---

## 3. 声纹：协议、时机与判定

### 3.1 对上协议（HTTP，llm-gateway 音频面）

```jsonc
GET  /api/voiceprint/info
  → {"enabled": bool, "provider": "campplus|mock|disabled", "occupants": [
       {"occupant_id":"primary","display_name":"泓舟","sample_count":3,"updated_at":...}],
     "min_speech_ms": 1500, "threshold": 0.62, "margin": 0.05}

POST /api/voiceprint/identify        // body: 二进制 PCM（16k mono s16le）；query: user_id
  → {"occupant_id": "occ-2", "display_name": "小雨", "score": 0.71, "decision": "accept"}
  → {"occupant_id": "primary", "score": 0.41, "decision": "below_threshold"}   // 诚实降级
  → {"occupant_id": "primary", "score": 0.66, "runner_up": 0.64, "decision": "ambiguous"}
  → {"occupant_id": "primary", "decision": "too_short"}

POST /api/voiceprint/enroll          // body: 二进制 PCM（多段拼接，段间由 offsets 分隔）
  // query: user_id, display_name, bind_primary=1|0
  → {"occupant_id": "occ-2", "sample_count": 3, "self_consistency": 0.83}
  → 409 {"error":"low_consistency", "self_consistency": 0.44}   // 三段互不像 = 录制有问题，拒绝建模板

DELETE /api/voiceprint/{occupant_id}?user_id=...&purge_memory=1
  → {"ok": true, "deleted_templates": 1, "deleted_memories": 27}
```

- `decision` 四态是**协议面的诚实性**：调用方（和 obs）能区分「认出了」「不够像」「两个人分不开」
  「话太短」，而不是只拿到一个 `primary` 说不清为什么。M2 Verifier 三态的同源思想。
- `self_consistency`：注册三段两两余弦的均值。太低说明录音里混了别人/噪声/中途换人——
  **宁可拒绝建模板，也不要建一个从此谁都认不准的模板**。

### 3.2 识别时机：边说边识别，一次唤醒锁一次（结构性决策三）

```
KWS 唤醒 / 按下说话
   └─> LISTENING 开始录音（pcmRing 已在前滚）
        └─> 累计有效语音 ≥ 1500ms 时【此刻用户还在说】
             └─> fire-and-forget POST /api/voiceprint/identify（不阻塞任何东西）
        └─> 本侧 VAD 判端点 → 定稿 → send()
             └─> send 前 await 识别结果，**超时上限 150ms**，超时即用上一次结果/primary
   └─> 本唤醒窗内（含 FOLLOWUP 续问）**不再重识**，occupant_id 锁定
   └─> 回 IDLE → 解锁；下次唤醒重识
```

为什么不「定稿后再识别」：那要么给首字延迟叠上一次网络往返，要么第一句永远认不出——而第一句
恰恰是信息量最大的一句（「我爱吃辣」这种话就说在第一句）。**用户说到第 1.5 秒时声纹所需的信息
已经够了，没有理由等他说完。**

为什么一次唤醒只识别一次、轮内不改判：中途改判会让同一段对话的前半截和后半截落进不同乘员的
记忆里——**比认错更糟，因为它同时污染两个人**。续问窗内换人是罕见情形，下次唤醒自然纠正。

### 3.3 三态判定与诚实降级（结构性决策四）

```
有效语音 < min_speech_ms                     → too_short        → primary
top1 < threshold                             → below_threshold  → primary
top1 ≥ threshold 且 (top1 - top2) < margin   → ambiguous        → primary
否则                                          → accept           → occ-N
```

- **一律降级到 `primary` 而不是 `guest`/`unknown`**：`primary` 是存量语义（今天所有记忆都在它名下），
  降级到它 = 逐字回落到今天的行为；降级到一个新身份会凭空造出一个空记忆空间，用户体感是「车失忆了」。
- `ambiguous` 这一档是专为家庭场景加的：两个成员声音接近时，**分不开就别分**。
- 四种 decision 全部进 obs span（`voiceprint.decision` / `score` / `runner_up`），
  供 M1b nightly 挖掘阈值调优的 badcase——**阈值不靠拍脑袋，靠线上分布**。

### 3.4 注册流程与「首个注册者绑定 primary」（结构性决策五）

入口：HMI 设置页新分组「乘员与声纹」。流程：添加乘员 → 填显示名 → 依次朗读 3 句不同的提示语
（各约 3 秒，句子不同以免录成同一段重放）→ 建模板。

**第一个注册的人默认绑定 `occupant_id = primary`**（UI 上明说「这是主驾/车主吗」，默认勾选）。
理由：今天全部存量记忆都在 `primary` 名下，若首个注册者拿到 `occ-1`，他一注册完，自己过去说过的
所有偏好、常去地点、家人关系当场全部失联——**这是本设计里后果最大的一个回归点**，必须在
注册这一步就堵死，而不是事后做数据迁移。

删除乘员时 `purge_memory` 默认开：用户说「删掉小雨」时的预期是「忘掉这个人」，不是「留着他的记忆
但认不出他」。UI 明示会一并删除该乘员的记忆条目与关系边。

### 3.5 数据契约（`voiceprint` 表，登记 conventions §9.11）

```sql
CREATE TABLE IF NOT EXISTS voiceprint (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL DEFAULT 'default',
    user_id       TEXT NOT NULL,
    occupant_id   TEXT NOT NULL,              -- 'primary' | 'occ-N'
    display_name  TEXT NOT NULL DEFAULT '',
    embedding     REAL[] NOT NULL,            -- L2 归一后的均值向量（维度由 provider 定，v1=192）
    dim           INT  NOT NULL,
    model         TEXT NOT NULL DEFAULT '',   -- 换模型即失效的依据（同 embedding_model 惯例）
    sample_count  INT  NOT NULL DEFAULT 0,
    self_consistency REAL NOT NULL DEFAULT 0,
    created_at    BIGINT NOT NULL,
    updated_at    BIGINT NOT NULL,
    UNIQUE (tenant_id, user_id, occupant_id)
);
```

- 用 `REAL[]` 而非 `vector(192)`：模板数量以个位数计，不需要 ANN 索引；且 `vector` 维度写死在
  DDL 里，换 provider（维度不同）就要迁表——`REAL[]` + `dim` 列让换模型只是数据失效不是 schema 变更。
- **红线**：`ForgetUser` 必须在同一事务里级联删本表，契约测试直接锁（照抄 `test_relation.py` 的写法）。
- `model` 不匹配当前 provider 的模板视为**失效**（识别时跳过并在 info 里标 `stale`），
  提示用户重录——绝不拿旧模型的向量跟新模型的向量比余弦，那个数字没有意义。

---

## 4. occupant_id 全链路透传与记忆隔离

### 4.1 管道（七个改动点，全部在既有惯例上）

```
HMI handsFreeController（识别结果，唤醒窗内锁定）
 └─ buildMeta() 加 occupant_id                        ①hmi/src/settings.tsx
     └─ WS → edge-gateway（meta 通用透传，无需改）
         └─ build_context 读 meta → PlanContext.occupant_id   ②context.py ③models.py
             ├─ ContextManager._recall(occupant_id=...)        ④context.py
             ├─ _append_turn(occupant_id=...)                  ⑤context.py（抽取按乘员归属）
             └─ prefs 白名单加 occupant_id → ExecuteRequest.meta ⑥context.py
                 └─ _sdk Context.occupant_id                    ⑦agents/_sdk/server.py + base.py
                     └─ recall / remember / resolve_person_place 带 occupant_id
```

改动面刻意小：**没有一个新机制**，全是把既有字段沿既有管道多传一段。`memory` 侧零改动
（隔离逻辑本来就在）。

### 4.2 隔离语义：v1 只做硬隔离，共享层不做

现有 recall 是 `occupant_id` 精确匹配，因此 occupant_id 一通，隔离**自动成立**：
乘员 B 召回不到乘员 A 的偏好。

**共享记忆（「家在 XX 小区」这类应该全家可见的事实）v1 明确不做**，理由：

- `memory_item.memory_level`（user|vehicle|occupant|session）这一列现状是**只写不读**——
  抽取管线恒写 `'user'`，recall 完全不看它。要做共享层就得同时改抽取分类（让 LLM 判「这条是个人的
  还是全车的」）+ recall 的过滤条件 + 一整套测试，是独立一期的体量。
- M2 的教训「**没有消费方的边不存**」反过来同样成立：现在没有任何 UI 或旅程要求跨乘员共享，
  先做一个读侧 OR 条件属于机制先于需求。
- 零回归：没注册声纹时 occupant_id 恒 `primary`，全部存量行为逐字不变。

写进 §9 余项，等真实使用暴露需求再定。

---

## 5. 视觉入口

### 5.1 短 TTL 帧引用（结构性决策七）

```
HMI 命中触发词 → 抓一帧 JPEG(≤1280px, q=0.8)
   └─ POST /api/vision/frame → {"frame_id":"vf_xxx","ttl_s":120}
       └─ send() meta 带 vision_frame_id
           └─ … → vision Agent → LLMClient.complete(vision_frame_id=...)
               └─ llm-gateway 按 id 取帧，拼多模态请求 → qwen-vl
```

- 帧存**网关进程内 LRU**（上限 16 帧 / TTL 120s），**不落 Redis、不落盘**——Redis 会持久化到磁盘，
  等于把车内外图像写进了存储。
- `meta` 只传引用不传内容：`HandleRequest.meta` 是 `map<string,string>`，塞几百 KB base64 会撑爆
  gRPC meta，还会整条进 obs 采集（`OBS_CONTENT_CAPTURE=on` 时）——那是隐私事故，不是性能问题。
- **proto 零改动**：图片从不进 proto，进 proto 的只有 16 字节的 id。走 `meta` 传引用与
  `trace_id`/`session_id` 同款（M1a「proto Struct 死字段够用、不改 proto」的同类判断）。
- obs 只记 `vision.frame_id` / 尺寸 / 字节数，**不记图**。

### 5.2 采集门控在端侧（结构性决策八）

HMI 端侧轻量触发词命中才抓帧（`那是什么`/`这是什么`/`这是啥`/`前面那个是什么`/`看看这个是什么`…），
**默认一帧都不采**；抓帧时 HMI 显示一次「已拍摄一帧用于识别」的可见提示。

为什么不做「后端判断要图 → 回头让 HMI 补拍」：① 多一个 RTT，而「那是什么」问的是**当下**，
半秒后的画面可能已经不是同一个东西；② 隐私门控放在后端等于「先采了再说」，与
CLAUDE.md §5「敏感数据默认不出车」的方向相反。**门控必须在采集侧。**

PoC 环境没有车外摄像头：用浏览器 `getUserMedia` 取一帧，HMI 与卡片上**恒显「模拟车外摄像头」**
标识（同 `sim.adas.*` 的诚实标注惯例、同 MCP 演示商户的三重标注惯例）。

### 5.3 vision Agent（新建，端口 50077）

```yaml
agent_id: vision
capabilities:
  - intent: vision.describe
    description: 看车外/车内摄像头的当前画面，回答「那是什么」这类关于眼前实物的问题
    slots: [question, frame_id]
    examples: ["那是什么", "前面那个建筑是什么", "这是什么车", "这是什么花"]
    verification: {mode: schema, on_fail: report, expect: {data_keys: [answer]}}
route_hints:
  - pattern: '(那|这|前面(那个)?|左边(那个)?|右边(那个)?).{0,4}(是什么|是啥|什么东西|什么牌子|什么车|什么花|什么建筑)|看看(这个|那个)'
    intent: vision.describe
    policy: replace
    priority: 58
    # guard 反例：POI 详情类「这家怎么样」归 nearby；卡片序号指代归既有链路
    guard: '这家|那家|第[一二三四五六七八九十\d]+个|详情|评分|人均|营业'
    slots: {question: "$text"}
```

- **拿不到帧就诚实说拿不到**（`frame_id` 缺失/过期 → 「我没拿到画面，再说一次我就看」），
  绝不用纯文本 LLM 编一个「那可能是一座写字楼」——铁律③在视觉上的直接推论。
- 当前 LLM provider 不支持图片输入时同样诚实降级（「当前大脑看不了图，去设置里换成通义可以看」），
  不静默退化成文本问答。

### 5.4 与 S2S 的关系：走同一条 escalate 路，S2S 面零职责

S2S 挡位下用户问「那是什么」→ S2S 模型判为需要执行/查实（`escalate`）→ `turn.escalated` →
HMI 按既有 `send(utterance)` 走文本主链 → **HMI 在 send 时命中触发词，照样抓帧带 frame_id**。
S2S 会话流里**不进任何图像**（视频流实时理解是 v2，成本与隐私都另议——§8 边界速写原文）。

这条自洽性是设计的一个检验点：视觉入口没有为 S2S 增加任何特例，两个挡位共用同一条路径。

---

## 6. 安全与隐私边界

### 6.1 红线：声纹不作鉴权因子（源码级断言测试钉死）

`occupant_id` 的可达范围**只有记忆域**：`recall` / `remember` / `AppendTurn` / `query_relations` /
`resolve_person_place`。它**不得出现在**：`granted_scopes` 的计算、`check_permission`、
VAL 的任何判定、`require_confirm` 的合成、payment-gateway。

落实：`test_voiceprint_not_auth.py` 做源码级断言——`security/`、`orchestrator/edge/val.py`、
`executor._enforce_capability_confirm` 的实现文本里不得出现 `occupant`；`build_context` 里
occupant_id 的赋值不得参与 `granted` 的任何分支。（打法同 M3「治理器零 kind 字面量」、
M2「Verifier 中央零领域字面量」。）

理由不只是「声纹可被录音重放」这一条技术理由——更重要的是**身份识别与授权是两件事**，
一旦声纹能提权，「认错人」的后果就从「看到别人的口味偏好」升级成「替别人花钱/开车门」。

### 6.2 隐私口径（与 S2S 的音频上行差异说明并列）

| 数据 | 出车？ | 门控 |
|---|---|---|
| 声纹注册音频 | 出（到 llm-gateway 提 embedding） | 用户主动在设置页录制；**原始音频用完即弃不落盘**，只存 192 维向量 |
| 识别用首句 PCM | 出 | 仅**唤醒后**首句、≤3s；与 classic 挡 ASR 的既有上行同性质（三段式本来就上行首句音频） |
| 声纹模板向量 | 存 PG | 不可逆推原音；GDPR 硬删级联 |
| 车外单帧图像 | 出（到 llm-gateway → qwen-vl） | **默认不采**，端侧触发词命中才抓一帧；网关内存 ≤120s；不进 obs/记忆/磁盘 |

隐私声明与设置页文案须同步更新这两条（延续 S2S 挡位「隐私声明显式呈现差异」的既定做法）。

### 6.3 不做

自研声学模型；声纹开锁/支付授权；人脸识别与任何视觉身份判定（与 6.1 同源）；连续帧/视频流理解；
情绪识别；跨车/跨账号的声纹共享；把 occupant_id 用于路由或权限。

---

## 7. 分期、DoD 与测试

**P4a-1 模型探针（先行，M1a/S2S-P0 同款打法）**
`test/e2e_voiceprint_probe.py`：★①模型可得性与加载耗时 ★②同人不同句余弦分布 ★③异人余弦分布
★④最短有效语音时长（1.0/1.5/2.0/3.0s 对比）★⑤三段注册的 self_consistency 分布。
**产出=把 §3.3 的 threshold/margin/min_speech_ms 三个数从"经验值"钉成实测值**，并按实测修订本文。
> 文档≠实测是本仓库接新模型的固定教训（ASR 双协议、qwen finish_reason、S2S 静默丢 tools 三次前科）。

**P4a-2 服务端**：网关声纹面（provider 抽象 + campplus/mock/disabled 三档）+ memory `voiceprint`
表与两个 RPC + GDPR 级联删。DoD：单测覆盖四态判定与级联删红线；`disabled` 档下整链逐字回落。

**P4a-3 透传与隔离**：§4.1 七点改动 + §6.1 源码断言测试。DoD：全量 pytest 零回归
（未注册声纹时 occupant_id 恒 primary ⇒ 存量行为不变，这是零回归的结构性保证）。

**P4a-4 HMI**：设置页「乘员与声纹」注册/改名/删除；controller 层边说边识别 + 唤醒窗锁定。
DoD：node 测试覆盖锁定语义与 150ms 超时兜底；**voiceLoop.mjs 零改动**（同 S2S 期）。

**P4a-5 真栈 DoD——多用户记忆隔离旅程**（兑现母提案 M4 最后一条 DoD）：
`test/e2e_voiceprint.py` 覆盖 ①注册主驾（绑 primary，存量记忆仍在）②注册第二乘员
③A 说「我爱吃辣」→ 存 A 名下 ④B 唤醒问「附近有什么好吃的」→ **召回里没有辣** ⑤A 再唤醒 → 辣回来
⑥认不出（噪声/陌生人）→ 诚实落 primary ⑦删除乘员 → 模板与其记忆同删。

**P4b 视觉**：`/api/vision/frame` + 网关多模态拼装 + vision Agent + HMI 触发词抓帧。
DoD：真栈 `test/e2e_vision.py`——真图片进真模型出真描述；帧过期诚实降级；provider 不支持图片时
诚实降级；`eval_route_hints` 不回退（新 hint 不劫持 nearby 详情句）。

---

## 8. 不做清单与风险

**不做**：跨乘员共享记忆层（§4.2，留余项）；声纹注册的语音入口（v1 只做设置页，语音口令留 v2）；
说话人分离/多音区（需要麦克风阵列，硬件不具备）；视频流理解；人脸/视觉身份；情绪识别；
声纹作鉴权因子；`sim.adas.*`（§8-6 维持 backlog）。

| 风险 | 缓解 |
|---|---|
| CAM++ ONNX 拉不下来（本机大文件下载前科） | 多源脚本 + `disabled` 档整链回落；mock 档保 CI；模型缺失不阻塞构建 |
| 阈值定得不准 → 家庭成员互认 | 探针先出实测分布再定；`ambiguous` 档「分不开就别分」；四态全进 obs 供 nightly 调优 |
| 主驾注册后存量记忆失联 | §3.4 首个注册者绑 `primary`（设计期堵死，非事后迁移）+ e2e 第①条专项断言 |
| 首句认不出导致「第一句话总是记错人」 | §3.2 边说边识别（1.5s 即发），send 前 150ms 软等待 |
| 识别错把 A 记忆给 B（隐私） | 三态诚实降级到 primary；ambiguous 档；**且 occupant_id 永不提权**（§6.1） |
| 视觉触发词误抓帧 | 端侧触发词收窄 + guard 让路 nearby；抓帧有可见提示；帧 120s 自灭不落盘 |
| 图片撑爆 meta / 进 obs | §5.1 只传引用；obs 只记 id 与尺寸（契约测试锁「meta 里不得出现 base64」） |
| 视觉模型不支持导致静默退化成瞎编 | 无帧/不支持一律诚实降级话术（铁律③），不进 LLM 猜 |

---

## 9. 余项（设计期就明确不在本期）

| 余项 | 说明 |
|---|---|
| 跨乘员共享记忆（`memory_level` 消费面） | §4.2：需同时改抽取分类 + recall 过滤 + 测试，独立一期体量；且当前无消费方 |
| 声纹注册的语音入口（「记住我的声音」） | 设置页先跑通，语音入口涉及注册期的多轮引导，v2 |
| 声纹随时间漂移的模板更新（增量巩固） | 需要「高置信识别的样本回灌模板」的治理（防把陌生人灌进模板），v2 |
| 真麦声学验收（识别率/误认率） | 浏览器声学层 CI 测不了，同 R4.3/S2S 惯例留泓舟 |
| 视觉的多轮追问（「它旁边那个呢」） | 需要帧的会话级驻留与指代解析，v2 |

---

## 10. 决策点（待泓舟拍板）

1. **首个注册者绑定 `primary`**（§3.4）——我认为必须这样，否则主驾一注册就丢全部历史记忆。确认？
2. **v1 不做跨乘员共享记忆**（§4.2）——「家在哪」这类事实乘客查不到，接受吗？还是要我一并做读侧
   `memory_level='vehicle'` 的共享回退？
3. **删除乘员默认连带删其记忆**（§3.4）——符合「忘掉这个人」的直觉，但不可逆。确认默认开？
4. **视觉 PoC 用浏览器摄像头模拟车外摄像头**（§5.2），卡片恒显「模拟」标识。确认？
