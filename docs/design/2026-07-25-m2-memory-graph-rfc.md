# M2 子 RFC：记忆图谱（偏好加权 + 关系边 + 生命周期强制项）

> 日期：2026-07-25
> 状态：**✅ §8 三个决策点已拍板（2026-07-25 泓舟：「都按你建议的来」）——按 §6 分期 P0→P1→P2 实施中**。
> 拍板结论：① 偏好层**加列**不建 `preference` 新表；② emotion **不进记忆层**，改 planner 同轮
> 输出会话级信号直供 TTS；③ 关系边**本轮做**（按 §6 分期依次推进，P0 无新表先行）。
> 含**对母提案 §4.D 的两处修正**（§2.1 / §2.3），母提案 §6 已同步指针。
> 依据：母提案 `2026-07-24-eva-benchmark-intelligence-upgrade.md` §2.2-C4 / §4.D / §6-M2
> 前序：M2 核心件（Task Ledger + Outcome Verifier）已收口（`2026-07-25-m2-task-ledger-outcome-verifier-rfc.md`）
> 范围：M2 后半——记忆从「条目库」升级为「带权偏好 + 关系边」，含 v1.2 评审既定的生命周期强制项。

---

## 0. TL;DR

1. **真缺口只有三样，不是一整套新体系**：现状 `memory_item` 已有 predicate / confidence /
   `source_turn_ids`（证据引用）/ `superseded_by`（冲突）/ occupant_id / privacy_level /
   use_count。母提案 `preference` 表要的字段里，**只有 `weight` / `evidence_count` /
   `half_life`（衰减）是真的没有**——「说过一次爱吃辣」与「每周三次点川菜」同权，根因就是这三个。
2. **建议不建 `preference` 新表，改为 `memory_item` 加三列 + 巩固期聚合**（§2.1）。2026-06-25
   记忆重构刚**把两表合并成单表**（kind 区分），半个月后没有新证据就拆回去，是在推翻自己；
   而级联删除 / 隐私分级 / supersede / 召回打分全都能复用，不用写第二遍。
3. **`relation` 建新表**（§2.2）——它和偏好不同：subject 不是用户（「小雨 × 是女儿」「XX小学 ×
   是小雨的学校」），查询模式是**按实体双向查**而非相似召回，塞进单表 JSONB 会很别扭。
   但 v1 只存边 + 一跳查询，不做多跳推理。
4. **建议 emotion 收窄为「会话级情绪信号直供 TTS 选参」，不进记忆层**（§2.3）。母提案自己
   给的约束（短 TTL、不入长期画像、长期画像需显式授权）已经把它排除在「记忆」之外；而
   M1b 的情感 TTS 参数面正缺一个信号源——那是它真正该去的地方。
5. **消费面先于存储面**（§4）：图谱存了没人查就是死数据。v1 只认三个明确的消费方——
   召回注入升级（结构化偏好摘要）、`navigate_to` 的人称地点解析（「去接孩子放学」）、
   routine 建议加权。**没有消费方的边不存。**
6. 生命周期强制项（§5，v1.2 评审既定）：证据溯源、consent、**级联删除**——GDPR 硬删原始
   证据时派生偏好必须随之删除或重算，这条是硬闸不是选项。

## 1. 现状与接缝（2026-07-25 侦查，file:line）

| 现状 | 位置 | 对设计的含义 |
|---|---|---|
| `memory_item` **单表 kind 区分**（semantic/episodic/procedural），2026-06-25 重构时**特意把两表合并** | `memory/schema.sql:5-35` | 再拆 `preference` 表要有新证据才成立——§2.1 论证为什么没有 |
| 已有字段：`predicate` / `confidence` / `source_turn_ids`（**证据轮次，可追溯纠错**）/ `superseded_by` / `occupant_id` / `privacy_level` / `use_count` / `last_used_at` / `valid_from` / `expires_at` | 同上 | 母提案 `preference(subject,predicate,object,weight,confidence,evidence_count,last_seen,half_life)` 里**只有 weight/evidence_count/half_life 缺** |
| 已有 `entities JSONB`（情景实体 `["西湖","杭州"]`） | `schema.sql:31` | 它是**扁平实体列表**不是边（无 rel、无方向）；relation 不能直接骑它 |
| 巩固：等价跳过 / 冲突 supersede，**按谓词等价类**匹配（`predicate_class`，B3-3 M2 修复） | `memory/store.py:225-257`、`extract.py:41-67` | 偏好加权的接缝就在这里：命中等价类时今天是「文本相同→跳过」，**改为「相同→加权」** |
| 召回打分：`score = base(向量/lexical) × confidence`，高敏非定向不召回，top_k 截断 | `pg_store.py:332-375` | weight 进这个公式即生效，无需改召回架构 |
| 注入：**「最多 3 条」原始条目**，格式 `- [tag \| conf \| prov] text`，预算 400 字符 | `orchestrator/cloud/context.py:154-175` | 母提案要的「结构化偏好摘要」就是改这个渲染器 |
| `forget()` 只 DELETE `memory_item` | `pg_store.py:432-452` | **新表必须同事务级联删**，否则 GDPR 硬删后派生数据仍在（§5 硬闸） |
| routine 派生：情景事件按 (action, place, 时段) 聚合，`min_count=3` 出建议 | `memory/routine.py:47-75` | 已经是「频次→置信」的雏形，偏好加权与它同构（可共用衰减函数） |
| 抽取四分类 + 黑名单（一次性命令/未确认地址/精确坐标/PII/敏感画像丢弃） | `extract.py:1-56, 141-192` | emotion 若入库要过同一套治理——§2.3 论证为什么不值得 |
| `occupant_id` 恒 `'primary'`（声纹未落地，M4） | 全栈 | schema 维度已在，v1 只需**不写死**，不为它单独做功能 |

## 2. 三个设计决策（含两处对母提案的修正建议）

### 2.1 决策一：偏好层**不建新表**，`memory_item` 加三列

**母提案原文**：新表 `preference(subject, predicate, object, weight, confidence, evidence_count, last_seen, half_life)`。

**建议修正为**：`memory_item` 增 `weight REAL DEFAULT 0` / `evidence_count INT DEFAULT 1` /
`half_life_days REAL DEFAULT 0`（0=不衰减），语义只对 `kind='semantic'` 生效。

理由：

1. **字段级对照后，新表只多三样**。`subject`=现状 (user_id, occupant_id)；`object`=现状
   `text`+`value_json`；`predicate`/`confidence`/`last_seen`(≈`valid_from`) 全都已有；
   `evidence_count` 现状可从 `source_turn_ids` 数出来（只是没物化）。为三个字段建一张表、
   一套 DAO、一套召回、一套级联删除，成本远大于收益。
2. **它会推翻半个月前的合并决策**。`schema.sql` 头注写着「两表合并为单表，kind 区分」
   （2026-06-25 重构），当时的理由（一套召回、一套治理、一套隐私分级）今天全都还成立。
   没有新证据就拆回去，是设计反复而不是演进。
3. **复用面很大**：supersede 时序、`privacy_level` 高敏不泛化召回、`expires_at`、
   `use_count`、GDPR `forget()`、`export()`——新表全都要重写一遍，且**极易漏级联删除**
   （§5 的硬闸风险）。
4. **加列是加法迁移**：`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，缺省值让存量条目行为
   逐字不变（weight=0 → 按 §3.2 回退到 confidence，与今天一致）。`pg_store._ensure_schema`
   已有 ALTER 先例（`schema.sql:227` 的 embedding 维度迁移）。

**反方观点（如果泓舟要建表）**：真正建表的理由只有一个——如果未来偏好要支持
**subject 不是当前用户**（「我太太不吃辣」）。那时 `memory_item` 的 (user_id, occupant_id)
表达不了「关于第三人的偏好」。**但那件事的正确落点是 §2.2 的 relation 表**（第三人是实体），
不是再造一张偏好表。故本决策不堵那条路。

### 2.2 决策二：`relation` **建新表**，但 v1 只存边 + 一跳

关系边与偏好是两类东西：

| | 偏好 | 关系边 |
|---|---|---|
| subject | 恒=当前用户/乘员 | 任意实体（人/地点/设备） |
| 查询 | 语义相似召回 top-K | **按实体名双向精确查**、一跳展开 |
| 生命周期 | 有衰减、会被 supersede | 长期稳定（「小雨是我女儿」不衰减） |
| 消费 | 注入 planner 上下文 | 解析话术里的人称/指代（§4.2） |

塞进 `memory_item` 要么污染召回（关系边被当偏好召回进 prompt），要么加一堆 `kind='relation'`
的特判。**建 `memory_relation` 表**（schema 见 §3.3）。

**v1 边界**：只存边、只做**一跳**查询（「小雨」→ `school` → 「XX小学」）。
不做多跳推理、不做图算法、不做自动实体消歧——那些没有消费方（§4）。

### 2.3 决策三：建议 emotion **不进记忆层**，改为会话级信号直供 TTS

**母提案原文**：抽取流水线增可选 `emotion` 标签，默认短 TTL（会话级/24h），不入长期画像；
形成长期情绪画像必须用户显式授权。

**建议修正为**：**不在记忆层做 emotion**。理由：

1. 母提案自己给的约束已经把它排除在「记忆」之外了——短 TTL + 不入长期画像 + 需显式授权，
   剩下的就是「本次会话的情绪状态」，那是**会话态**不是记忆。为它走一遍抽取→治理→入库→
   召回→过期清理的全流程，是把会话态硬塞进长期存储。
2. **真正缺信号的地方是 TTS**：M1b 已经把 cosyvoice 的 `instruction`/`rate` 参数面做好了
   （`TTS_INSTRUCT_DEFAULT`/`TTS_SPEED_DEFAULT`，conventions §6 已登记「按情绪标签动态选参
   的接线留 M2 emotion」）。它需要的是**当前这轮**的情绪，不是画像。
3. **隐私风险与收益不匹配**：情绪是敏感数据，入库就要过 consent/导出/删除全套；而它的唯一
   已知消费方（TTS 选参）根本不需要持久化。

**替代方案（建议 v1 做这个）**：Planner 已经在同一次 LLM 调用里输出 `addressed`/`clarify`
（R4.4 先例），加一个可选 `emotion` 字段（`neutral|happy|tired|urgent|frustrated`，
fail-open 缺省 neutral）→ 随 final 事件传到 HMI → HMI 选 TTS instruct 参数。
**零存储、零治理成本、直接兑现 E1「情感化表达」的可感知部分**。若日后确需长期情绪画像，
再按母提案的授权模型单独立卡。

## 3. 数据模型

### 3.1 `memory_item` 加三列（加法迁移）

```sql
ALTER TABLE memory_item ADD COLUMN IF NOT EXISTS weight          REAL NOT NULL DEFAULT 0;
ALTER TABLE memory_item ADD COLUMN IF NOT EXISTS evidence_count  INT  NOT NULL DEFAULT 1;
ALTER TABLE memory_item ADD COLUMN IF NOT EXISTS half_life_days  REAL NOT NULL DEFAULT 0;
ALTER TABLE memory_item ADD COLUMN IF NOT EXISTS consent         TEXT NOT NULL DEFAULT '';
```

- `weight`：0-1，**巩固期算出的强度**（见 §3.2）。0 = 未参与加权（存量条目/非 semantic），
  召回时回退到 `confidence`——存量行为逐字不变。
- `evidence_count`：支撑该偏好的**独立轮次数**（物化 `source_turn_ids` 的计数，避免每次召回都数）。
- `half_life_days`：0=不衰减（默认，行为不变）；按 provenance 分档（§3.2）。
- `consent`：`''`=未特别声明（沿用 privacy_level 治理）；`explicit`=用户显式授权过的敏感画像。
  为 §5 的 consent 强制项预留，**v1 只写不读**（读的地方是 M3/M4 的敏感画像功能）。

### 3.2 权重与衰减（纯函数，可离线单测）

```
weight = clamp(base(provenance) + reinforce(evidence_count), 0, 1) × decay(age, half_life)

base:       user_stated=0.6   agent_inferred=0.3
reinforce:  min(0.4, 0.1 × (evidence_count - 1))     # 每次重复 +0.1，封顶 +0.4
decay:      half_life=0 → 1.0（不衰减）
            否则 0.5 ^ (age_days / half_life_days)
half_life:  explicit 偏好=0（不衰减，用户明说的就该长期有效）
            inferred 偏好=90 天
            temporary 偏好=沿用现有 expires_at（硬过期，不走衰减）
```

**关键语义**：
- 「说过一次爱吃辣」= user_stated + count 1 → weight 0.6
- 「每周三次点川菜」= agent_inferred + count 8 → 0.3 + 0.4 = 0.7，**超过前者**
- 半年前推断的一次性偏好 = 0.3 + 0 → ×0.5^(180/90) = 0.075，**自然沉底**
- **explicit 不衰减**是刻意的：用户明说的偏好凭什么因为久了就不算数？

召回打分改为 `score = base × effective_confidence`，其中
`effective_confidence = weight if weight > 0 else confidence`（存量兼容）。

### 3.3 `memory_relation` 新表

```sql
CREATE TABLE IF NOT EXISTS memory_relation (
  id            TEXT PRIMARY KEY,
  tenant_id     TEXT NOT NULL DEFAULT 'default',
  user_id       TEXT NOT NULL,
  occupant_id   TEXT NOT NULL DEFAULT 'primary',
  subject       TEXT NOT NULL,          -- 实体名（"小雨"）；归一化后的显示名
  rel           TEXT NOT NULL,          -- 封闭词表，见下
  object        TEXT NOT NULL,          -- 实体名或字面值（"女儿" / "XX小学"）
  object_ref    TEXT NOT NULL DEFAULT '',  -- 可选：指向 memory_item.id 或 profile.places 键
  confidence    REAL NOT NULL DEFAULT 1.0,
  provenance    TEXT NOT NULL DEFAULT 'user_stated',
  privacy_level TEXT NOT NULL DEFAULT 'sensitive',   -- 家人/地点默认敏感
  consent       TEXT NOT NULL DEFAULT '',
  source_turn_ids TEXT NOT NULL DEFAULT '',          -- 证据溯源（§5 强制）
  valid_from    BIGINT NOT NULL DEFAULT 0,
  superseded_by TEXT,
  created_at    BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_subject ON memory_relation (user_id, occupant_id, subject);
CREATE INDEX IF NOT EXISTS idx_rel_object  ON memory_relation (user_id, occupant_id, object);
```

**`rel` 是封闭词表**（防 LLM 自由造词，与 `predicate_class` 同一条教训）：

| rel | 例 | 消费方 |
|---|---|---|
| `family` | 小雨 —family→ 女儿 | 人称解析（§4.2） |
| `place_of` | 小雨 —place_of→ XX小学 | 「去接孩子」→ 目的地 |
| `works_at` / `lives_at` | 我 —lives_at→ 家 | 复用既有 `profile.places` |
| `owns` | 我 —owns→ 特斯拉Model Y | 车书问答的车型接地 |
| `prefers_brand` | 我 —prefers_brand→ 星巴克 | 周边发现排序 |

新增 rel 要先登记在本表（conventions 同步），**中央不为具体 rel 写分支**。

## 4. 消费面（先于存储面定，没有消费方的边不存）

### 4.1 召回注入升级：结构化偏好摘要

`_render_memory`（`context.py:154`）从「最多 3 条原始条目」升级为**两段式**：

```
已知用户偏好（按强度排序，仅在相关时参考）：
- 空调温度 26℃（常用）        ← weight ≥ 0.7 的高权偏好，人话渲染
- 不走高速（偶尔提过）         ← weight < 0.5 的低权偏好，措辞弱化
相关记忆：
- [taste.spicy | 0.72] 用户喜欢吃辣
```

- 高权偏好用**确定性人话模板**（不进 LLM），低权偏好措辞弱化让模型自己判断要不要用。
- 预算仍 400 字符；**条数从 3 放宽到 5，但高权优先**——今天 top-3 会被一条久远的推断偏好挤掉真正常用的。
- 契约测试锁：weight=0 的存量条目渲染与今天逐字一致。

### 4.2 人称地点解析：「去接孩子放学」

`navigation.navigate_to` 的 destination 解析链上加一跳（**在 Agent 侧，不改编排核心**）：
话术含人称词（孩子/女儿/儿子/老婆/我妈…）且 destination 解析不到具体地点 →
查 `memory_relation`：人称 →`family`→ 实体名 →`place_of`→ 地点 → 交给既有地标解析。
查不到 → **诚实追问**「你说的是谁？我还不知道 TA 在哪上学」，绝不猜。

这是母提案 §1.2-E2 举的 Eva 例子（「带我去接孩子放学」）的最小可用形态，也是**关系边唯一
非做不可的理由**——没有这条链路，relation 表就是死数据。

### 4.3 routine 建议加权

`routine.detect_routines` 的 `min_count=3` 硬阈值改为**按 weight 决策**：复用 §3.2 的
衰减函数，让「上个月密集出现但最近没有」的 routine 自然沉底，不再拿旧频次骚扰用户。

## 5. 生命周期强制项（v1.2 评审既定，硬闸）

| 项 | 要求 | 落地 |
|---|---|---|
| **证据溯源** | 派生偏好可追到原始轮次 | `source_turn_ids` 已有；加权时**必须累加而非覆盖**（今天 supersede 会丢旧证据）；relation 同字段 |
| **consent** | 敏感画像需记录授权来源 | 新增 `consent` 列，v1 只写不读（消费在 M3/M4） |
| **冲突处理** | 沿用谓词等价类 supersede | 已有，加权时改为「等价→加权」而非「等价→跳过」 |
| **级联删除** | GDPR `ForgetUser` 硬删原始证据时，派生 preference/relation **必须随之删除或降权重算** | **`forget()` 同事务删 `memory_relation`**；`export()` 同样带上。**契约测试直接锁**：删完再查两张表都必须为空——这是本 RFC 唯一的红线级测试 |

> 现状 `forget()` 只删 `memory_item`（`pg_store.py:432`）。新表落地当天如果忘了这一行，
> GDPR 删除就是**假删除**——关系边（家人、孩子学校）恰恰是最敏感的那部分数据。

## 6. 分期与 DoD

**P0 偏好加权（不含新表，风险最低）**
加三列 + `weight/decay` 纯函数 + 巩固期「等价→加权」+ 召回打分接入 + `_render_memory` 两段式。
DoD：纯函数单测（base/reinforce/decay/边界）；**存量兼容契约测试**（weight=0 → 打分与渲染
逐字不变）；真栈——同一偏好说三次后 weight 上升且注入措辞从「偶尔提过」变「常用」。

**P1 关系边 + 人称地点解析**
`memory_relation` 表 + 抽取产 relation 候选（封闭 rel 词表）+ `forget/export` 级联 +
navigation 人称解析一跳 + 查不到诚实追问。
DoD：**级联删除契约测试（红线）**；rel 词表外的候选被丢弃；真栈——「我女儿叫小雨，她在
XX小学上学」→「去接孩子放学」导航到 XX小学；未登记人称→诚实追问不猜。

**P2 routine 加权 + emotion 直供 TTS**（§2.3 的替代方案）
DoD：旧 routine 自然沉底不再骚扰；planner 输出 emotion → HMI 选 TTS instruct（fail-open）。

每期收口跑全量 pytest（当前基线 1922/7 skipped）+ `e2e_memory.py`。

## 7. 不做清单（v1 边界）

多跳图推理与图算法（无消费方）；自动实体消歧/共指消解（「小雨」和「女儿」是不是同一人由用户
话术显式给出，不猜）；跨用户关系（隐私）；`preference` 独立表（§2.1）；长期情绪画像
（§2.3，需授权模型另立卡）；声纹驱动的真实 occupant 维度（M4）；图谱可视化面板。

## 8. 决策点（2026-07-25 已拍板：三条全部按建议执行）

1. **偏好层加列 vs 建 `preference` 新表** → **✅ 加列**（§2.1）。理由：字段级对照后只缺
   weight/evidence_count/half_life 三个；建表会推翻 2026-06-25 刚做的单表合并决策，且
   supersede/隐私分级/GDPR 级联/召回打分全要重写一遍（极易漏级联删除）。
2. **emotion 是否进记忆层** → **✅ 不进**，改为 planner 同轮输出会话级信号直供 TTS（§2.3）。
   与母提案 §4.D 字面表述有出入，但与它自己给的约束（短 TTL、不入画像、需授权）一致。
3. **P1 关系边是否本轮做** → **✅ 本轮做**，按 §6 分期 P0（无新表、风险最低）先行，
   P1 关系边与 `forget()` 级联同 commit 落地（§5 红线，不拆期）。

## 9. 风险

| 风险 | 缓解 |
|---|---|
| 加权改变既有召回顺序，扰动已绿的旅程（B3-3 记忆族） | weight=0 存量兼容 + 灰度 env `MEMORY_WEIGHTING=on\|off` + journeys regression 必绿 |
| 巩固期「等价→加权」写放大（每轮都 UPDATE） | 仅在抽取巩固触发时（每 4 轮/显式陈述），非每轮 |
| 关系边被 LLM 造词污染 | `rel` 封闭词表 + 词表外丢弃（`predicate_class` 同款教训） |
| **级联删除漏做 = GDPR 假删除** | §5 红线级契约测试；新表与 `forget()` 同一个 commit 落地，不拆期 |
| 人称解析猜错导航到错地方 | 查不到**诚实追问**，绝不用相似度猜；解析结果仍走既有地标校验 |
| 关系边默认敏感致召回不到 | `privacy_level=sensitive`（非 highly）+ 人称解析走**定向查询**不走泛化召回 |


---

## 10. 落地记录（2026-07-25，P0/P1/P2 单日完成）

### 10.1 实现与设计的一致性

三个拍板决策全部按设计落地，无偏差：偏好层**加列**（`memory_item` +4 列）、
emotion **不进记忆层**（planner 同轮输出 → HMI 下一轮 TTS 语气）、关系边**建新表**
（`memory_relation` + 封闭 rel 词表 + 一跳解析）。

设计未写、实现补上的两处：
- **emotion 走 prompt-only 不进 submit_plan schema**——B4-1 两轮教训（「模型对 schema
  结构的响应强于 description 文本」，可选字段诱发多填）在此复用：emotion 是旁路信号
  （只喂 TTS 选参、不影响 steps），更不值得冒行为漂移的风险。契约测试锁死
  `test_emotion_never_enters_tool_schema`。
- **routine 加权改为两段判据**（裸频次 ≥ min_count 且有效计数 ≥ min_count×0.5）——
  直接拿衰减后的有效计数比阈值有边界坑：连续三天做同一件事衰减后是 2.93 < 3，用户感知
  就是「我明明连着三天都做了，它没认出来」。正确语义是两件事：真的发生够多次 + 这个
  习惯还活着。

### 10.2 分期落地

| 期 | 内容 | 测试 |
|---|---|---|
| P0 偏好加权 | `memory/weighting.py`（纯函数：base/reinforce/decay/effective_confidence/证据合并）+ schema 加 4 列 + 巩固期「等价→加权」（就地更新、不刷新 `valid_from`）+ 冲突时继承证据链 + 召回打分接入 + 注入两段式渲染（强度人话词，top-N 3→5） | 单测 40（weighting 22 + reinforce 7 + 渲染 11） |
| P1 关系边 | `memory_relation` 表 + `memory/relation.py`（封闭 rel 词表 + 亲属称谓归一 + 一跳解析）+ 抽取 `_relation` 保留键分流 + `QueryRelations`/`ResolvePersonPlace` 两 RPC + **GDPR 同事务级联删** + navigation 人称目的地消费 | 单测 41（relation 28 + 人称目的地 13） |
| P2 | routine 时间加权（半衰期 30 天，比偏好的 90 天短——习惯本就该更快过期）+ planner emotion（prompt-only）→ `FinalResult.emotion` → HMI 存下一轮 → TTS instruct（措辞表在 llm-gateway 侧） | 单测 30（routine 5 + emotion 21 + TTS instruct 4） |

### 10.3 验证

| 项 | 结果 |
|---|---|
| 全量 pytest | **2041 passed / 7 skipped**（M2 核心件后基线 1922，净 **+119** 零回归） |
| `test/e2e_memory_graph.py` 真栈五场景 | ✅ 全绿：偏好加权（weight 0.6、evidence_count 物化、**同一偏好只有一条现行条目**）→ 关系边入图（`小雨-family-女儿` / `小雨-place_of-阳光小学`）→ **「导航去接孩子放学」→ 廊坊阳光小学**（母提案 §1.2-E2 的 Eva 例子走通）→ 未登记人称诚实追问 → **GDPR 级联删除（红线）删前 2 → 删后 0** |
| journeys regression | 见下 |

**真栈踩坑三条（都只有真栈才暴露）**：
1. **亲属称谓的自然语言变体**：LLM 抽出来是「用户的女儿」，而查询侧是精确匹配（刻意的，
   模糊会把「小雨」匹到「小雨点」）→ 存成变体就永远查不到。修法是**入库时归一**
   （`normalize_kinship` 剥「用户的/我的」前缀 + 映射 canonical），查询保持精确。
2. **口语里说「接我妈」不说「接妈妈」**：① 人称词表漏了裸「妈/爸」；② 更隐蔽的是
   `_PERSON_FILLER_RE` 没有「我」——剥完「妈」剩个「我」被当成实质内容，整条链路
   静默不触发。裸单字不会误伤：「大妈家门口的超市」剥掉「妈」剩「大超市」仍有实质内容。
3. **播报要用自然称谓**：「我还不知道**妈**平时在哪」读着别扭 → `_PERSON_DISPLAY`
   映射成「妈妈」。

### 10.4 未做与边界

- **`consent` 列 v1 只写不读**（设计既定）：消费在 M3/M4 的敏感画像功能。
- **多跳推理、实体消歧、跨用户关系**：§7 不做清单，无消费方。
- **emotion 只影响下一轮**：本轮 TTS 在 final 之前就已流式开播，当轮改不了语气。
  「用户上一句烦躁 → 这一句安抚着说」是可实现且语义诚实的形态；HMI 只存内存不落盘
  （它是会话态不是画像）。
- **偏好加权对存量数据不追溯**：`weight=0` 的老条目按 confidence 打分，只有再次被
  巩固时才进入加权体系——刻意不做批量回填（回填要重算所有历史证据，收益不抵风险）。
