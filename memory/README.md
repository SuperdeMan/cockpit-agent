# Memory 服务

上下文的唯一真相源：短期会话、车辆上下文、**长期分层语义记忆**。上下文按 scope 取数（隐私最小化）。

2026-06-25 从 mock KV 重构为分层语义记忆，完整设计见
[`docs/design/2026-06-25-memory-system-redesign.md`](../docs/design/2026-06-25-memory-system-redesign.md)。

## 分层
- **L0 会话**：`AppendTurn` / `GetSession`（Redis，连不上自动降级内存）。**轮次带 OwnerKey**（M-B）。
- **L1 车辆上下文**：`GetContext(scopes)` 按 scope 返回片段（敏感 scope 脱敏，如 `vehicle.location` 只给城市级）。
  ⚠ **`vehicle.location` 的默认值是 PoC mock（上海·延安高架），生产路径上不许有消费方**
  （2026-08-28 清掉最后一条：road-safety 的天气预警此前在 city 槽为空时回退它，
  真栈答出「上海当前有1条天气预警」而用户问的是深圳，下一轮模型又从这句答案里学会了
  上海）。定位一律走 `meta` 的本轮 GPS（`agents/_sdk/location.current_location_from_meta`），
  取不到就诚实问一句。**留着这份 mock 是为了测试，不是为了兜底。**
- **L2 语义画像**：稳定偏好/个人实体（`taste.*`、`person.pet` 等），带 `predicate`、向量化、`superseded_by` 时序-lite。
- **L3 情景**：显著事件，聚合源。
- **L4 程序记忆**：从 L3 高频行为派生 routine，经 `agent.proactive` 主动建议。

## 接口（见 `proto/cockpit/memory/v1/memory.proto`）
| RPC | 用途 |
|---|---|
| `GetContext` / `AppendTurn` / `GetSession` | 车辆上下文 + 会话短期记忆（`AppendTurn` 带 `user_id` 时每 4 轮触发异步抽取巩固；三者均按 OwnerKey，`GetSession.scope` 缺省 OWNER_ONLY） |
| `UpsertProfile` | 写画像字段；`places` 按 OwnerKey 落 highly_sensitive `memory_item place.*`，**per-key patch 不整块覆盖** |
| `DeleteMemoryItem` | L1 精确删除：OwnerKey + item id 删一条，同事务清派生关系边 |
| `Remember` | 写语义/情景记忆（抽取管线或 Agent 显式） |
| `Recall` | 语义召回（向量 + scope/occupant + 时序融合；`predicate_prefix` 精确优先，`min_score/min_confidence/max_age_days` 阈值） |
| `ResolvePersonPlace` | 人称 → 常去地一跳解析（`family` 边找实体 → `place_of ∪ works_at ∪ lives_at` 找地点）。**查不到或有歧义一律返回 not found，调用方须诚实追问**——导航到错地方比查不到更糟。⚠ **匿名占位与具名是同一个人**（2026-08-20）：`女儿--family-->女儿` 是「无名的人」的表示法，用户后来说「我女儿叫小雨」再存 `小雨--family-->女儿` ⇒ 两个 subject 指向同一个人，旧判据数成两个人判歧义、**一跳解析对该称谓永久失效**。现按「占位不算独立的人、**具名主体 ≥2 才是真歧义**」分组，地点在合并后的实体上取并集；**「地点必须唯一否则返回 not found」那道闸没动**——放宽识别不等于放宽授权 |
| `ForgetUser` / `ExportUser` | 合规：被遗忘权（硬删）/ 数据导出 |

## 存储与 embedding
- **PostgreSQL + pgvector**：单表 `memory_item`（`schema.sql`，`kind` 区分 semantic/episodic/procedural）。无 `POSTGRES_DSN` 降级纯内存（lexical 召回）。
- **embedding 走 llm-gateway → 阿里云百炼 text-embedding-v4**（1024 维，`EMBED_DIM` 配置）。无 `LLM_EMBED_API_KEY` 时**诚实降级 lexical，绝不哈希伪语义**喂规划。
- 关键文件：`pg_store.py`（向量存储）、`store.py`（门面）、`extract.py`（四分类抽取治理 + PII/坐标黑名单）、`routine.py`（routine 派生）、`server.py`（gRPC）。

## 隐私
- 三档 `privacy_level`：`normal` / `sensitive`（用户主动告知的个人实体，可泛化召回）/ `highly_sensitive`（家/公司精确地址，泛化召回排除、仅 scope/predicate 定向可读）。
- 抽取黑名单：精确坐标、电话/证件号（PII）、第三方隐私、Agent 推断的敏感画像 → 丢弃。

## 测试
- 单点单测：`tests/test_pg_store.py`、`test_store.py`、`test_extract.py`、`test_server_rpc.py`、`test_routine.py`（内存兜底，不连 PG/Redis）。
- 复杂场景集：`tests/test_scenarios.py`（8 例：偏好演化/多乘员隔离/隐私三档/过期/routine/抽取纵深/合规/召回契约）。
- 全栈断言 E2E：`../test/e2e_memory.py`（6 链路，连真栈，自清理可重入）。


## 声纹模板（`voiceprint.py` + `voiceprint` 表，M4 P4）

契约登记 `docs/conventions.md` §9.11。**存的是向量不是音频**——原始音频在 llm-gateway
提完 embedding 即弃，永不跨服务、永不落库。

- `voiceprint.py` 是**纯计算层**（归一/余弦/模板合成/三态判定/occupant_id 分配），
  同 `weighting.py`/`relation.py`，可脱离 PG 单测。
- **判定四态**：`accept` / `below_threshold` / `ambiguous`（top1-top2 < margin）/ `no_templates`，
  **accept 之外一律回 `primary`**——不是 guest 也不是 unknown。primary 是存量语义，降到它
  等于逐字回落到 P4 之前；造新身份等于凭空多一个空记忆空间，用户体感是「车失忆了」。
- **首个注册者拿 `primary`**：存量记忆全在它名下，若首个注册者拿 occ-1，他自己过去说过的
  一切当场失联。堵在分配这一步，不做事后迁移。
- **红线**：`ForgetUser` 必须同事务级联删本表（声纹是生物特征，留着比留关系边更严重）；
  删单个乘员默认连带删其记忆（「忘掉这个人」），但 **`primary` 永不 purge**——
  删单个乘员不该有清空全车的爆炸半径，要清空走 `ForgetUser`。
- 阈值由 `test/e2e_voiceprint_probe.py` 实测钉死。实测结论：**真正起作用的控制量是 margin
  不是 threshold**（thr∈[0.45,0.70] 端到端结果完全相同）。
- **名字（2026-07-26 真机第二批）**：`display_name` 与 `identity.name` 记忆是同一件事的两个
  落点，写在一起改在一起。①`RenameVoiceprint` 只改称呼不动模板——改名绑在「重录三段」上，
  用户就会为改名反复走注册流程（真机上的名字丢失正是这么发生的）；②`EnrollVoiceprint` 收到
  **空名按「不改名」处理**，保留已有的，且同名重录不再重复写记忆；③删除乘员时**撤回注册
  自己写的那条名字**（逐字匹配 `_identity_text`，不误伤对话里说过的别的身份陈述）——
  primary 不 purge 记忆，但模板都删了还留着名字，助手会继续管一个认不出的人叫旧名。

## 多乘员数据归属（OwnerKey，M-B）

契约登记 `docs/conventions.md` §9.13。一句话：**M4 P4 让系统知道「谁在说话」，
M-B 让数据面记得下来**——识别对了却存不下来，等于没识别。

```text
OwnerKey = (user_id, occupant_id)      # 空 occupant = primary，绝不等于共享
```

- **Turn 存完整 owner + exchange + 两维事实**：`{turn_id, exchange_id, user_id,
  vehicle_id, occupant_id, role, text, ts, actions, sources}`。后两维是**系统持有的
  事实的账本**：`actions` 是这一轮真实执行了哪些动作（Q6，契约 §9.24），
  `sources` 是这一轮用了谁的数据、降没降级（C4-A，契约 §9.34）——两者都归一后落库、
  都进幂等比对集（**一条被悄悄改过的记录比没有记录更糟**，审计会照着它回答）。一次请求 + 它可见的回复共用一个 `exchange_id`
  （cloud/edge 用 request_id、S2S 用 s2s turn_id），所以**重试是重放不是新一轮**；
  同 turn_id 异内容抛 `TurnConflict` 并保留原 Turn。
- **读默认 OWNER_ONLY**：`last_n` 是过滤后的上限，切中 exchange 时整体舍弃最旧的
  半个——只留 assistant 那半句会让抽取把助手说的话当成用户偏好归档。
- **抽取窗口在进 extractor 之前就按 owner 切好**：归属判定不交给 LLM，它看到的只是
  一段文本。巩固节流键也带 owner，否则「A 说三轮、B 说第四轮」会在只说过一句的 B
  名下触发一次巩固。
- **places 的唯一真相源是 owner-scoped `memory_item place.*`**：primary 在 backfill
  完成前 dual-read legacy KV 但只补新表缺失的 key；**非 primary 永不读 legacy KV**。
- **旧数据统一归 primary**，不按文本/时间/声纹猜 —— 有损但方向永远是收窄不是放开。
- 删除：owner 级删除**缺 occupant 一律 `missing_owner`**，绝不推断 primary、更不扩大
  成 user-all。L1 走 `DeleteMemoryItem`，跨 owner 回 `not_found`（回「不是你的」本身
  会泄露它属于谁），`identity.name` 回 `managed_memory`。
- 红线不变：`occupant_id` 只进记忆域，不参与任何鉴权/确认/VAL 判定。
