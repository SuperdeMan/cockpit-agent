# Xiaomi SU7 真实车型手册 RAG implementation plan

> 状态：**已归档（生产 release `a406e22` 已验证）**（2026-09-03）
> 交付对象：`manual-rag` Agent、离线手册索引构建工具、检索评测与真实性治理
> 关联：`agents/manual_rag/`、`scripts/build_manual_index.py`、
> `docs/architecture/detailed/ws6-real-capabilities-and-agent-collaboration.md` §3、
> `docs/conventions.md` §9.3/§9.4、
> `docs/reviews/2026-08-30-qa-closeout-handoff.md` §5

## 0. 结论与边界

本批把 `MockKnowledgeRetriever` 的**生产可选实现**替换为一个真实、只读、可审计的
Xiaomi SU7 车型手册索引 Provider。运行链路为：

```text
用户提供 PDF
  -> 离线抽取、清洗、按 PDF 页切块、绑定目录章节与源 SHA
  -> deterministic gzip JSON 索引包（本地私有资产，不进 Git）
  -> ManualIndexRetriever 启动期完整性校验
  -> 中文字符 n-gram BM25 召回 + 章节/短语/覆盖率重排 + 低相关 fail-closed
  -> Chunk(source_type=manual, 章节, PDF 页码, 车型)
  -> 既有“只依据参考资料”生成链 + `manual` 卡片 citations / `_prov=real`
```

本批**不做**以下动作：

- 不建 pgvector/Milvus 表，不改数据库 schema，不执行数据迁移；
- 不修改根 `.env`，不部署、不 push；
- 不把源 PDF 或完整抽取文本提交到 Git。手册导言明确限制复制/提取/再发布，索引包因此
  作为 `models/manual_rag/` 下的 ignored 私有运行资产管理；
- 不声称当前生产 release 已升级。代码与本地索引验证完成后，生产仍需单独走受控发布。

## 1. 输入证据与现状

### 1.1 源手册审计

| 项目 | 实测值 |
|---|---|
| 输入文件 | `D:\Personal\AI\Claude Code\产品\2024-小米SU7-Pro-Max-用户手册.pdf` |
| PDF 标题 | `SU7用户手册` |
| 页数 | 278 |
| 文本层 | 277 页有文本，合计 138,222 字符；无需 OCR |
| 目录 | PDF outline 完整，覆盖导言、驾驶、智驾、充放电、规格、保修保养等章节 |
| 源 SHA-256 | `ef16d204c2ad711b2aa6c2a5f2a6607cfc2d47ed3f5d5a4e1db4085f75e4705d` |
| PDF 元数据时间 | `2024-04-15T15:10:07+08:00` |

抽样核对已确认文本层能保留关键事实及 PDF 页码，例如：轮胎压力 2.9 bar（PDF 245）、
定期保养每 1 年或 20,000 公里（PDF 251）、三元锂/磷酸铁锂充电限值 80%/100%
（PDF 213）、智能泊车进入与操作步骤（PDF 163–164）、雨刮片更换（PDF 261）。
手册不含 `CarPlay`，只声明小米互联互通与部分安卓手机的 `CarLink`（PDF 185–186）；
`CarPlay` 问题必须零命中，不能拿“手机互联”近似回答。

### 1.2 现有实现

- `KnowledgeRetriever` 与 `Chunk.source_type` 接口已经存在；
- `MockKnowledgeRetriever` 只有 5 条演示语料，默认工厂决议为 mock；
- `KNOWLEDGE_VENDOR=pgvector` 当前明确 fail-fast 为 TODO；
- Agent 已有四道必要护栏：零命中不调 LLM、来源类型降权、安全告警确定性前缀、卡片
  `_prov`；这些护栏保留；
- QA 当前把 `manual` 卡的 mock 当“已知 WARN”，严格栈默认也豁免 `knowledge`。真实实现
  可用后，这两项必须退出临时豁免，否则“写了 real Provider”不等于发布会使用它。

基线验证前先刷新本机 ignored proto 生成物；随后 `python -X utf8 -m pytest -q
agents/manual_rag` 为 **13 passed**。此前 11 个失败来自本机 `gen/python` 落后于源码中的
`Capability.response_only`，不是本任务代码缺陷。

## 2. 为什么本批不直接上 pgvector

架构目标态写的是“向量库 + 重排”，但当前问题本质是让一个 278 页、单车型、低频更新的
静态手册成为**真实且可追溯的依据**。直接上 pgvector 会同时引入 schema/data migration、
embedding 模型一致性、在线 query embedding、数据库可用性和运维备份五个新变量；本批又没有
数据库变更授权。

当前选择是文件索引内的中文 n-gram BM25 + 确定性重排：

- 单手册全量仅约 14 万字，内存加载与全量词项统计足够快；
- 不依赖网络、密钥、数据库或本地 embedding 模型，CI 与离线验收可复现；
- 中文双字 n-gram 不依赖分词库，配受控同义词扩展解决“胎压/轮胎压力”等手册词与口语词差异；
- 对未出现的显著 Latin 词和低覆盖查询 fail closed，可机械锁住 `CarPlay`/错误车型等近似误答；
- `KnowledgeRetriever` 接口不变，未来多车型规模或真实召回数据证明词法上限后，可增加
  pgvector 实现而不改 Agent。

这不是把目标态永久降成关键词搜索。量产触发条件是：车型数大于 1、真实 badcase 证明同义
改写无法靠受控扩展覆盖，或单索引加载/检索超过时延预算；届时再以本批 golden corpus 为
A/B 尺子迁移向量召回。

## 3. 目录与资产约定

本批新增目录先按以下约定建立：

| 路径 | 只放什么 | 是否进 Git |
|---|---|---|
| `agents/manual_rag/resources/` | 可信手册 catalog 指纹、检索停用短语、受控同义词和车型别名等小型声明 | 是 |
| `models/manual_rag/` | 由真实 PDF 构建的只读索引包；不放源 PDF | 否，仅跟踪 README/`.gitkeep` |
| `test/eval_corpus/manual_rag_retrieval.yaml` | 查询、期望页和零命中反例，不复制大段手册正文 | 是 |
| `.artifacts/manual-rag/` | 本轮评测 JSON、临时对照与工具依赖 | 否 |

索引包默认名为 `xiaomi-su7-2024.v1.json.gz`。内容不带绝对路径、不带构建时间，gzip
时间戳固定为 0；相同 PDF 与参数应产生逐字节相同的文件。

## 4. 索引与运行时契约

### 4.1 索引 schema v1

```jsonc
{
  "schema_version": 1,
  "document": {
    "document_id": "xiaomi-su7-2024-user-manual",
    "title": "SU7用户手册",
    "publisher": "小米汽车",
    "vehicle_model": "xiaomi-su7-2024",
    "vehicle_aliases": ["SU7", "小米SU7", "Xiaomi SU7", "SU7 Pro", "SU7 Max"],
    "revision": "2024-04-15",
    "source_file": "2024-小米SU7-Pro-Max-用户手册.pdf",
    "source_sha256": "...",
    "source_pages": 278,
    "content_sha256": "..."
  },
  "chunks": [{
    "chunk_id": "xiaomi-su7-2024-user-manual:p0245",
    "page_start": 245,
    "page_end": 245,
    "section_path": ["车辆规格", "规格与参数", "车轮与轮胎参数"],
    "content": "..."
  }]
}
```

构建器按 PDF 物理页切块并移除页脚；空白/纯封面页不入索引。页粒度保留表格上下文和
PDF 可核验页码，避免固定字符窗口把“数值”和“单位/车型条件”切开。`content_sha256`
覆盖按稳定顺序序列化后的所有 chunk，Provider 启动时重算。

### 4.2 Provider 决议

- 默认 `KNOWLEDGE_VENDOR=mock` 不变，保证无私有手册资产的 CI/离线开发仍可启动；
- 显式 `KNOWLEDGE_VENDOR=local` 时读取 `MANUAL_INDEX_PATH`；未配置则用仓库/镜像内
  `models/manual_rag/xiaomi-su7-2024.v1.json.gz`；
- 文件缺失、schema 不支持、hash 不符、chunk/page 非法、配置车型与索引车型不符，统一
  `ProviderConfigError` 启动即失败，绝不回 mock；
- 索引自带 hash 之外，还必须与 tracked `resources/manual_catalog.yaml` 中人工批准的
  document/model/revision/source/content 指纹逐项一致；自洽但未登记的索引不得盖 real 章；
- 校验成功后才调用 `log_resolution("knowledge", <document_id>, real=True)`；因此
  `_prov.mode=real` 的含义是“本轮引用了通过完整性校验的真实手册索引”，不是只看文件名；
- 运行时只读内存结构，不写索引，不动态抓网页，不跨车型回退。

### 4.3 召回、重排与拒答

1. Unicode NFKC、大小写、空白归一；移除纯问法填充短语；
2. 从受控资源加载同义词扩展，例如 `胎压 -> 轮胎压力/充气压力`、
   `自动泊车 -> 智能泊车`、`充电上限 -> 充电限值`；
3. 中文按双字 n-gram、Latin/数值按完整 token 建 BM25；章节路径与正文分开计分，章节命中
   只加权、不替代正文证据；
4. 以原短语命中、query token IDF 覆盖率、章节命中进行确定性重排；同页不重复；
5. 查询里的显著 Latin token（排除索引声明的车型别名）若语料完全不存在，直接零命中；
6. 低于最小覆盖率或最低分直接零命中，不拿“同领域但答不了该问题”的页凑 `top_k`；
7. 返回分数归一到 0–1，`Chunk.source` 固定包含标题、章节和 `PDF第N页`。

### 4.4 Agent 输出

- 调 `retrieve(question, vehicle_model=...)`，空 model 使用索引默认车型；显式错车型零命中；
- prompt 中每段参考资料带稳定序号与来源，仍要求只依据资料作答；
- 对真实手册答案做确定性数值接地复核：带单位或小数的数值不在本轮引用片段中则整段
  弃权并保留原文卡，不能靠 prompt 请求模型自律；
- `manual` 卡保留兼容字段 `sources/chunks`，新增 `document/vehicle_model/page_start/
  page_end/section_path/score` 供审计；
- `_prov` 增 `data_time=<manual revision>`、`data_time_label=手册版本`；
- 零命中、安全告警和非真实来源的既有 fail-closed 行为不放宽。

## 5. 实施顺序

| 阶段 | 变更 | 完成判据 |
|---|---|---|
| P0 ✅ | 先落本计划与目录/资产约定 | 方案、红线、回滚和验收均可执行 |
| P1 ✅ | 先写 Provider/构建器/评测红测 | 缺文件、hash 损坏、错车型、CarPlay 误召回、真实页召回均先能红 |
| P2 ✅ | 实现 deterministic PDF 构建器 | 278 页输入产出合法索引；同参两次 hash 相同；绝对路径不泄漏 |
| P3 ✅ | 实现 `ManualIndexRetriever` 与工厂 `local` 分支 | real 决议、完整性/信任锚校验、BM25+重排、零命中和车型隔离通过 |
| P4 ✅ | Agent 引用与 provenance 增强 | 卡片页码/章节/车型/手册版本齐；数值接地与既有四道安全护栏不回归 |
| P5 ✅ | 真实手册构建和 retrieval golden eval | 主集 23/23、holdout 8/8；报告落 `.artifacts/` |
| P6 ✅ | 收口真实性治理与文档 | `knowledge` 退出严格栈默认豁免；`manual` mock 退出 QA WARN 白名单；架构/指南/交接同步 |
| P7 ✅ | 本地验证 | 专项、相邻契约、五道门禁和全量按风险分层跑完；结果绑定当前工作树 |

## 6. 验收矩阵

### 6.1 构建与完整性

- 源 SHA 不符时构建拒绝；
- 索引 schema/hash/chunk/page 任一破坏时 Provider 拒绝启动；
- 相同输入两次构建产生相同 SHA-256；
- 索引 JSON 不含源绝对路径、token、密钥或构建机信息；
- 源 PDF 和索引文件均为 ignored，`git status` 不出现它们。

### 6.2 真实语料检索

至少覆盖以下问法，并核对 chunk 正文而非只核对页号：

| 问法 | 期望证据 |
|---|---|
| 胎压应该打多少 | PDF 245，轮胎压力 2.9 bar |
| 胎压报警怎么办 | PDF 257，低于 2.3 bar/停车联系服务中心 |
| 充电上限设多少 | PDF 213，三元锂 80%/磷酸铁锂 100% |
| 多久保养一次 | PDF 251，每 1 年或 20,000 公里，以先到者为准 |
| 自动泊车怎么开启 | PDF 163–164，车速/入口/操作步骤 |
| 雨刮片怎么换 | PDF 261，维护模式和四步更换 |
| 制动液多久换 | PDF 252，2 年/4 万公里节奏 |
| 怎么用手机解锁 | PDF 9–11，手机钥匙/NFC/蓝牙流程 |
| 怎么连 CarPlay | **零命中**；手册无 CarPlay |
| 机油灯亮了怎么办 | 手册语料**零命中**，但 Agent 仍由现有安全判据给确定性处置 |

### 6.3 回归与门禁

```powershell
python -X utf8 -m pytest -q agents/manual_rag scripts/tests/test_build_manual_index.py `
  scripts/tests/test_eval_manual_rag.py scripts/tests/test_probe_qa_long_sessions.py
python scripts/eval_manual_rag.py --index models/manual_rag/xiaomi-su7-2024.v1.json.gz `
  --cases test/eval_corpus/manual_rag_retrieval.yaml `
  --output .artifacts/manual-rag/xiaomi-su7-2024-retrieval.json
python test/eval_skills.py
python test/eval_exemplars.py
python scripts/check_intent_gate.py
python test/eval_capability_integrity.py
```

专项全部通过后再跑仓库固定全量口径。`target=cloud` 下不启动本地 Compose；本轮未部署，
因此不把本地结果转借给生产 release。

## 7. 风险、回滚与发布条件

| 风险 | 控制 |
|---|---|
| PDF 表格抽取顺序失真 | golden case 同时检查页号与关键正文；页粒度保留表格上下文 |
| 词法召回同义改写漏召 | 受控 aliases + 实际 badcase 驱动；不无界扩词；未来用同 corpus A/B 向量召回 |
| 低相关页被模型说成答案 | Latin 缺词闸、IDF 覆盖率闸、最低分闸、零命中不调 LLM |
| 错车型引用 | 索引 model/aliases 元数据 + 配置 mismatch 启动失败 + retrieve model filter |
| 索引被替换仍打 real | 内部 hash 重算 + tracked `manual_catalog.yaml` 批准指纹双闸通过后才盖 real 章 |
| 手册内容进入仓库历史 | `.gitignore` 逐层排除；只跟踪 README/`.gitkeep`；验证 `git status` |
| 生产仍在用 mock | QA mock 白名单撤销、严格栈豁免撤销；发布前必须看到 real 决议与 real 卡 |
| ignored 索引不进 `source.tar` | 复用既有 shared-model SHA 清单与 preflight；单独 bootstrap 到远端，再由 cloud profile 只读挂载 |

回滚只需把 `KNOWLEDGE_VENDOR` 明确切回 `mock`（仅开发/离线）或回退代码提交；real 配置下
绝不自动回 mock。生产发布的必要条件是：经单独授权的私有资产通道或预置只读挂载把
hash 已核对的索引交给只读挂载，启动日志为 `provider[knowledge]=... (real)`，真实 `manual`
卡 `_prov.mode=real`，并完成 exact release 的只读问答验收。现有 cloud release 只传 Git
commit 生成的 `source.tar`，不会携带 ignored 索引；本计划不替代发布链变更或 deploy 授权。

## 8. 实施记录（2026-09-03）

### 8.1 交付

- `scripts/build_manual_index.py`：惰性加载 `pypdf`，校验源 SHA，读取 278 页文本层与
  outline，清页脚后生成 269 个页级 chunk；输出无绝对路径/构建时间；默认拒绝覆盖；
- `index_format.py`：schema v1、chunk/content/source hash、deterministic gzip；
- `resources/manual_catalog.yaml`：tracked 信任锚，固定 SU7 的标题、车型、版本、页数、
  source/content 指纹；自洽但未登记的索引不能打 real；
- `ManualIndexRetriever`：中文双字 n-gram BM25、受控 aliases、规格/周期意图扩展、章节/
  短语/IDF 覆盖率重排、Latin token/多词产品名/错车型/低相关 fail closed；
- Agent manifest 升至 `0.2.0` 并声明 `response_only=true`；prompt、卡片与 `_prov` 带
  稳定引用，真实手册生成答案增加数值接地闸；
- `eval_manual_rag.py` + retrieval corpus：主集 23 条、独立改写/负例 holdout 8 条，
  同时校验 top 页、正文和零命中，不以页号碰巧命中洗绿；
- Docker/Compose 具备本机打包与显式 `local` 配置；源 PDF 和真实索引正文均保持 ignored。

### 8.2 索引证据

| 项目 | 最终值 |
|---|---|
| source SHA-256 | `ef16d204c2ad711b2aa6c2a5f2a6607cfc2d47ed3f5d5a4e1db4085f75e4705d` |
| source pages / indexed chunks | 278 / 269 |
| content SHA-256 | `530b8538484d076cccb3739bde80fec3927b15514a65c7abfd3ad56fdad233b5` |
| index SHA-256 / size | `b290fde73a2e1c3eced1f80e4fbb423d00a1150504ae82605709d22831406cfa` / 133,061 bytes |
| deterministic rebuild | 第二路径重建 SHA 与主索引逐字节相同 |
| retrieval | main 23/23，holdout 8/8；最终报告 p50 13.713ms / p95 22.828ms / max 22.931ms |
| handler probe | 启动行 `provider[knowledge]=xiaomi-su7-2024-user-manual(real)`；胎压 top source=PDF 245；CarPlay 零命中且 LLM 调用数不增加 |

评测报告：`.artifacts/manual-rag/xiaomi-su7-2024-retrieval.json`；重建对照：
`.artifacts/manual-rag/xiaomi-su7-2024-rebuild-v1.json.gz`。两者均是 ignored 本机证据。

### 8.3 工程验证

| 层 | 结果 |
|---|---|
| RED | 新测试最初因 `index_format` / `local_index` / evaluator 尚不存在而 collection error |
| manual-rag + builder/evaluator 专项 | 38 passed |
| 相邻契约 | 548 passed / 1 既有 `audioop` warning（含 provenance、response_only、Planner 写闸、catalog、E2E 协议） |
| edge smoke | 13 passed / 0 failed |
| skills | 22/22；反例误召回 1/8，按既有门槛 PASS |
| exemplars | 314 条契约；域错配 2.4% < 20%，PASS |
| intent gate | discovery 85/85（676 cases / 634 distinct）；gate 25/25（139 / 129） |
| capability integrity | PASS；manifest 53 / servers 20 / edge 85 |
| compile/YAML/Compose/diff | compileall、4 个新增/修改 YAML、Compose config、`git diff --check` 均通过 |
| 最终 release 全量 | `7796 passed / 34 skipped / 11 warnings`，255.90s，clean clone exact `a406e22`，`TZ=UTC0`、未设置 `PYTHONIOENCODING` |

11 个 warning 仍是既有类别：8 Starlette、1 gRPC test fixture、1 audioop、1 regex；
clean clone 未复制 ignored NLU 模型，因此没有触发根工作区的 2 条 WordPiece warning。
本行只属于 exact `a406e22`，不向其他 SHA 转借。

### 8.4 发布边界

- 未修改根 `.env`、数据库、密钥、Tailscale、systemd 或 CI workflow；
- `test/e2e_strict_stack.py` 的 manifest 明确为非 remote-safe/real signed profile，云端 runner
  正确拒绝，未用 `--allow-mutating` 绕过；改用容器内无持久化 Agent 探针和单轮生产 WS；
- 原工作区在发布期间持续出现并发 mobile 提交/文档改动，全部保留且未并入本批；push
  使用精确 `<sha>:main`，发布从独立 clean clone 执行，没有 reset/rebase/stash；
- 根工作区一次 `verify` 因隔离 clone 推送后尚未 fetch 新对象而产出 `unknown` artifact；
  fetch 后同一 remote release 重跑为 `verified`，失败不是远端健康或 E2E 失败。

### 8.5 发布接线增量（授权后）

- `runtime-models.json`、`MODEL_BOOTSTRAP_FILES` 与 inline remote preflight 三表加入同一
  index SHA，漂移测试保持三表相等；
- cloud profile 固定 `KNOWLEDGE_VENDOR=local`、车型与容器路径，并把
  `/opt/car-agent/shared/models/manual_rag` 只读挂入 `/app/models/manual_rag`；
- cloud release / deploy assets / dev-stack 专项 **491 passed / 4 skipped**；真实 Compose
  merge 回读同时保留 `/certs:ro` 与 `/app/models/manual_rag:ro`；
- 接线后固定全量 **7797 passed / 32 skipped / 13 warnings**（300.17s），warning 类别不变。

### 8.6 生产发布与真栈读数

- 基础设施批准摘要：`5314f8961ef2a8bdbdcbd3f5645cc3aa3c3746beeadcc727fe328f4698535efd`；
  bootstrap index SHA `b290fde73a2e1c3eced1f80e4fbb423d00a1150504ae82605709d22831406cfa`；
- Windows checkout 首次把 cloud compose 以 CRLF 上传，批准锚按 Git blob LF hash 对账失败；
  从 `git show <sha>:deploy/cloud/compose.cloud.yaml` 生成 artifact 后修复，preflight
  `blocking=0 / bootstrap=ready`。旧 compose/批准锚与错误 CRLF 文件均保留在 staging；
- 首发 `423ed23` 5/5 healthy 且 verify 通过，但生产 WS 带“请查…用户手册、冷态…”前缀
  只召回 PDF 256，未带 2.9 bar 参数页；新增 RED 为 30/31；
- `a406e22` 把“请查/帮我查”归问法壳，补“冷态胎压→轮胎压力参数”词义映射，并让
  evaluator 可要求多张互补页面；retrieval 31/31、专项 124 passed、exact 全量见 §8.3；
- 最终 production release `a406e222b3fe08ea462c06ccf676d0698f1f443a`，回滚点 `423ed23`；
  status 5/5 healthy、零 warning；统一 verify `verified`，artifact
  `.artifacts/dev-stack-verifications/20260903T053150Z-a406e22.json`，
  `e2e_remote_safe` / `minimax:MiniMax-M3`；
- 容器内同一带前缀问法返回页 `[245,256,257,255]`、speech 含 2.9 bar、real provenance、
  零动作；生产 WS 独立 session repeat 3 为 3/3，逐轮 `manual + real + SU7 + PDF245/256 +
  2.9 bar + plain speech + zero actions`；CarPlay 负例 1/1 sources 为空、零动作；
- 两次探针假红来自 Windows PowerShell stdin 把中文字面量/页码断言编码成 `?`；改用环境
  变量或 ASCII `\uXXXX` 后同一生产结果通过。该问题属于验证工具输入，不是 Provider。
