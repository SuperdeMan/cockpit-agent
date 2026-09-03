# Xiaomi SU7 真实手册 RAG v2：问句落域与视觉证据 implementation plan

> 状态：**v2 已生产；v2.1 真栈落域修复已完成本地验证，待受控发布**（2026-09-03）
> 基线：`origin/main=3ae1622ab8d25118d288e231a0b2ef0a40ab669d`；生产 release
> `f2dcb46fe6764f4087982e1216d7c1da98ab88f5`；修复候选
> `b3a2aedd3c360c230709551502e5568e8bba8286`  
> 工作分支：`feat/manual-rag-v2-grounded-visuals`  
> 输入：`D:\Personal\AI\Claude Code\产品\2024-小米SU7-Pro-Max-用户手册.pdf`

## 0. 结论与验收口径

v1 已把 `MockKnowledgeRetriever` 换成真实 PDF 文本索引，但真实用户验收暴露出三条断链：

1. `雨刮器怎么打开` 的无标点 ASR 形态被端侧当 `wiper.on`，带问号形态又被云侧规划成
   `chitchat.talk`；检索器虽能命中 PDF 95，用户仍拿不到手册答案。
2. `小人背着把宝剑的灯` 在文本索引中零命中，生产闲聊错误回答成安全气囊故障；同一 PDF
   第 193 页的图标表证明它对应安全带未系提醒。
3. PDF 有 358 个图片放置（316 个唯一原始对象），但 v1 构建器只调用 `extract_text()`；
   `manual` 卡也没有 HMI 专用渲染出口。

因此 v2 的完成标准不是“索引里有真实文本”，而是以下五项同时成立：

- 无标点操作方法问句不产生任何车控 action；
- 弱模型落错时仍由 Agent 自声明的窄 route hint 落到 `manual.query`；
- 受控视觉俗称能召回唯一的正式图标名称、说明、页码和原图；
- 普通文本命中页如雨刮 PDF 95 能返回同页操作示意图；
- 座舱 HMI 与 Android 都能显示 manual 证据卡，未知/超限/损坏图片 fail closed。

## 1. 设计决策

### 1.1 路由不进编排核心：安全闸 + skill/exemplar + 窄 hint

- `runtime/question_shape.py` 增加零领域词的句形：`对象 + 怎么/如何 + 打开/开启/关闭/使用…`
  是方法询问；`怎么把…打开`、`帮我/替我/现在…打开` 仍是指令。
- 新增 `skills/guides/manual-help-boundary.yaml`，向 Planner 解释“操作方法咨询 / 仪表图标
  含义 / 立即车控 / 真实照片识别”的产出边界；向 `skills/exemplars/manual.yaml` 只追加与
  生产原句不同的两个改写样本，让 hybrid exemplar 通道学习说法泛化。
- `agents/manual_rag/manifest.yaml` 声明两条窄 `replace` hint：
  - 车辆部件/功能的操作方法问法；
  - 仪表/状态栏告警灯或图标的含义问法。
- 反例必须覆盖明确车控、手机/软件操作、天气/新闻、实际拍照识别，防止 manual 抢域。
- skill 与 exemplar 是主泛化层，但属于软约束；`runtime` 仍承担“不误执行”，hint 只兜已经
  复现的高风险 canonical 句形。后续按 `hint_retirement.py` 双臂重复证据决定是否退役 hint。

### 1.2 文本索引兼容，视觉资产单独成包

不建数据库、不改 proto。保留 schema v1 文本 bundle，新增 deterministic `.mrag` ZIP：

```text
index.json                         # 原文本 bundle 的 canonical JSON
visual-assets.json                 # 来源 SHA、资产元数据、caption/aliases/description、全量 hash
assets/<sha256>.jpg|png            # 去重后的原始 JPEG / 确定性 PNG
```

- 旧 `.json.gz` 继续可读，图片为空；显式 v2 包损坏不回退 v1/mock。
- ZIP entry 固定时间、权限、顺序和压缩方式，同输入逐字节一致。
- 每个图片 blob、视觉 manifest 和包文件均有 SHA-256；运行时读图再次核验 blob hash。
- JPEG 直接无损提取；小尺寸 RGB/RGBA `FlateDecode` 用 Python stdlib 生成 PNG。
- 本手册 7 个无法由 pypdf 解码的 LZW 对象及超大无浏览器编码对象显式计入
  `skipped_assets`，不伪称 100% 图片覆盖。

### 1.3 视觉语义只接受受控目录

新增 `agents/manual_rag/resources/visual_assets.yaml`，绑定源 PDF SHA：

- 警告灯表按物理页与从上到下的图标顺序声明正式名称；构建时数量不一致直接失败。
- 高歧义俗称由人工声明，例如“背宝剑的小人”→“安全带未系提醒指示灯”；不在线调用 VLM
  猜图标，也不把模型猜测写回目录。
- 高歧义项的 `description` 由人工对照同页手册正文审定并进入同一视觉 manifest hash；
  不让构建器用表格文本顺序猜说明边界。
- 普通页面图片使用章节路径作为保守 caption；没有目录匹配时只能按已命中文本页返回，
  不能仅凭图片猜答案。

### 1.4 图片不进入提示词，卡片有硬上限

- `Chunk` 增加结构化 `images`，但 LLM context 只拼正文、来源与已审定视觉名称。
- 视觉俗称命中且带手册说明时走确定性回答，避免 LLM 在同一页的安全带/气囊两行间改判。
- 卡片最多 2 张图；单图原始数据不超过 640 KiB，总原始数据不超过 768 KiB。
- 只允许 `image/jpeg`、`image/png`；前端再次校验 data URI 协议、长度与 base64 形状，
  不接收 SVG/HTTP/任意 URI。
- 卡片保留 source、PDF 物理页、caption、blob SHA 与 `_prov.mode=real`。

## 2. 文件级实施清单

### 2.1 规则、路由与语料

- `runtime/question_shape.py`、`runtime/tests/test_question_shape.py`
- `orchestrator/edge/tests/test_fast_intent_adversarial.py`
- `skills/guides/manual-help-boundary.yaml`、`skills/exemplars/manual.yaml`
- `agents/manual_rag/manifest.yaml`
- `test/eval_corpus/route_hints_cases.yaml`
- `test/eval_corpus/manual_rag_retrieval.yaml`

### 2.2 建库、格式与 Provider

- `agents/manual_rag/src/index_format.py`
- `scripts/build_manual_index.py`
- `agents/manual_rag/resources/visual_assets.yaml`
- `agents/manual_rag/src/providers/base.py`
- `agents/manual_rag/src/providers/local_index.py`
- `agents/manual_rag/src/agent.py`
- 对应 builder/provider/agent/evaluator 测试

### 2.3 两端 manual 卡

- `hmi/src/types.ts`
- `hmi/src/manualCard.mjs` + node 测试
- `hmi/src/components/Cards.tsx`
- `mobile/src/features/cards/miscCards.tsx`
- `mobile/src/features/cards/CardRenderer.tsx`
- mobile 卡型完整性与渲染测试

### 2.4 文档与私有资产

- `agents/manual_rag/README.md`、`models/manual_rag/README.md`
- `docs/conventions.md`、架构版本记录、`docs/design/README.md`
- 真实 `.mrag` 只生成到 ignored `models/manual_rag/`，不提交 PDF、正文或图片。

## 3. RED → GREEN 验证矩阵

| 层 | 必测正例 | 必测反例 |
|---|---|---|
| 句形/端侧 | `雨刮器怎么打开` → 不执行 | `帮我打开雨刮器` → 仍为 `wiper.on` |
| route hint | 雨刮方法、背宝剑告警 → `manual.query` | 手机 App 怎么打开、拍照看灯、明确车控不抢域 |
| retrieval | 雨刮 → PDF 95 + 示意图；背宝剑 → PDF 193 + 安全带图标 | 气囊/安全带不串图；未知俗称零命中 |
| agent | visual alias 确定性答正式名称与手册说明 | 图片 hash/类型/大小非法时不出图、不编答案 |
| 客户端 | HMI/Android 显示图、caption、页码、real provenance | SVG/HTTP/超长 data URI 拒绝 |
| 真实 PDF | rebuild deterministic；文本 31 例不回归；新增两例通过 | LZW/超大不支持对象有明确 skipped 统计 |
| 端到端 | 独立 session：manual 卡、real、预期页、图片、0 action | `chitchat.talk`、空来源、任何 action 均失败 |

完成后依次运行：manual/builder 专项、runtime/edge/route hints、HMI node+TypeScript+build、
mobile Jest+TypeScript、四道 blocking 门禁，最后按内存余量跑固定全量。

## 4. 本轮授权边界

本轮只在独立 worktree 实现、生成 ignored 私有包并做本地/离线验证。为保证发布不会继续加载
v1，代码库内受控的 `runtime-models.json`、bootstrap hash 与 Compose 默认路径会同步到 v2 包；
但不会修改根 `.env`、数据库 schema、CI/CD，不会在远端安装该资产，也不会 merge、push 或
deploy。生产 E2E 需在代码提交后另行展示精确提交清单，并按项目红线单独取得 push/deploy 授权。

## 5. 实施结果与证据

### 5.1 精确实现 SHA 与私有资产

- 实现提交：`f2dcb46fe6764f4087982e1216d7c1da98ab88f5`；分支
  `feat/manual-rag-v2-grounded-visuals`，未 push、未 deploy。
- `.mrag` 大小 64,886,928 bytes；包 SHA-256=
  `648cdf3d1d5001f199fce12e3983f3d016d929f772d0eb8aa058512dcd4400ed`。
- 文本 content SHA 保持 `530b8538…233b5`；视觉 manifest SHA=
  `be594128e827afb207dc611f389a14a1d626d542df0ae17b77dc0da4c8676511`。
- 269 个文本 chunk；350 个可展示图片放置、299 个去重 blob；17 个 skipped 明细为
  7 个 LZW 与 10 个超像素上限 Flate。两个独立输出的包 SHA 完全相同。

### 5.2 目标问法闭环

| 问法 | 结果 |
|---|---|
| `雨刮器怎么打开` | 端侧分类返回 None；route hint 可把 chitchat 计划改为 `manual.query`；检索 PDF 95；确定性回答“轻按雨刮拨杆开关…车辆控制 > 雨刮调节”；返回该页 JPEG；LLM 0 次 |
| `我的仪表上有个小人背着把宝剑的灯亮了是怎么回事` | route hint → `manual.query`；受控视觉别名 → PDF 193“安全带未系提醒指示灯”；返回对应 PNG 和手册说明；LLM 0 次 |
| `帮我打开雨刮器` | 仍为 `wiper.on`，没有被 manual 抢域 |
| 手机 App 方法 / 真实照片识别 | route hint 反例保持 chitchat/vision，不扩大手册与摄像头边界 |

真实 retrieval 扩展为 **36/36**：main 27/27、holdout 9/9，p95 23.104ms；包含雨刮、
安全带/气囊混淆对照、未知“小人拿雨伞”零命中和图片 caption/page 断言。

### 5.3 工程验证

- manual/runtime/edge/cloud route 专项：**180 passed**；发布资产接线：
  **349 passed / 1 skipped**。
- 五道门禁：edge smoke 13/13；skills 23/23；exemplars 316 条、域错配率 2.4%；
  L0 discovery 85/85、gate 25/25；capability integrity PASS。
- HMI：node **298/298**，Vite production build PASS；SSR 断言真实 manual 图、caption、页码，
  并反验 SVG/HTTP 不渲染。仓库既有 `tsc --noEmit` 在原根工作树同样有 `.mjs` 声明缺失、
  `audio.ts` BlobPart 与 `cardMath` 导出等错误，本批没有把它冒充通过。
- Android：Jest **49 suites / 488 tests PASS**，`npm run typecheck` PASS；共享模块台账、
  card registry 与画廊样本均已更新。
- 固定 `-n 8` 在本机可用内存约 4GB 时两次分别为 7823 pass + 1 MemoryError、
  7822 pass + 2 资源压力失败；3 个失败用例串行全部 PASS。按实际资源降为 `-n 4` 的完整批：
  **7824 passed / 34 skipped / 7 warnings**（437.23s）。这不是 `-n 8` 全绿读数，不能换名。

实际 PDF 中导出的雨刮 JPEG 与安全带 PNG 已视觉核对正确。尝试使用会话内浏览器做完整卡片
截图时没有可用浏览器实例，因此本轮 UI 证据为 SSR、两端测试、Vite build 与原图核对；生产
HMI 截图留给发布后的真实 WS 验收。

### 5.4 v2 当时尚未完成的发布边界（历史）

本节记录实现完成当时的边界：生产仍为 `a406e22` 文本版，v2 包尚未安装到远端 shared models，三条
生产 WS 探针（冷态胎压、无标点雨刮、背宝剑图标）也尚未在新 release 上执行。对应
`e2e_strict_stack.py` 已升级为必须同时断言 `manual + real + 预期页 + 预期图片 + 0 action`；
发布前不得把本地结果写成生产闭合。v2 后续已发布为 `f2dcb46`；当前状态由下方 §6 接管。

## 6. v2.1：生产 36 题扩面后的落域修复

### 6.1 生产证据与问题拆分

release `f2dcb46` 上以独立 session/trace 跑完整 retrieval corpus：主集 27、holdout 9，正例
29、负例 7。确定性检索控制组仍为 **36/36**；生产中只要进入 `manual.query`，首轮 26/26、
含错例复验累计 **33/33** 的页码、正文、图片或零命中均正确。自然问法的精确落域只有
**26/36（72.22%）**，所以端到端 RAG 同为 26/36；失败在检索前，不能归因给 BM25，也不能
用换向量库处理。

10 个首轮错域中，`胎压报警怎么办` 3/3 被端侧当 `tire_pressure.query`，`三元锂电池平时
充到多少` 3/3 被当 `battery.query`；`防滑链应该装在哪个轮子上`、`紧急情况怎么呼叫救援`、
`支持 Android Auto 吗` 均 0/3 落 manual。另有 5 条在 1/3–2/3 间波动。最严重的是
`空调滤网怎么换` 被端侧执行成 `hvac.on`；因测试前未记录车态，未擅自发反向动作。

原始证据位于 ignored `.artifacts/manual-rag-live-validation/`。复核时修正两条探针假红：
`7°C` 与 `7℃` 应按检索器同源 NFKC 比较；安全告警会在“手册未查到”前追加处置建议，负例
应判 chunk/image 为空而不是要求话术从固定前缀开始。

### 6.2 修复实现

- `runtime.question_shape` 只增加“换、哪个/哪侧、什么要求”等零领域句形；`空调滤网怎么换`
  因此不再进入写操作。明确“帮我打开/设置”仍是指令。
- `fast_intent` 分开当前状态与手册知识：胎压报警处置不再抢成当前胎压；电池容量、充电目标
  不再抢成当前 SOC，`胎压现在多少`、`电池还有多少` 仍保持端侧查询。
- `manual-help-boundary` v2 扩到规格建议、维护、冬季用胎、兼容性和车内 SOS；manual exemplar
  只追加生产原句的实质改写。manifest 0.3.0 增窄 canonical hint，同时保留实时车况、设备
  兼容性、手机求救、研究话题和明确执行命令反例。
- `e2e_strict_stack` 从三题子集升级为直接消费完整 36 题 corpus，并核对单一 manual 卡、real
  provenance、页码、NFKC 正文、图片、负例零命中和零 action；corpus 进入 canonical digest。

### 6.3 本地验证

- TDD RED：新增边界精确得到 **7 failed / 163 passed**；实现后同组 **170 passed**。
- route hints **103/103**；edge eval **69/69**；edge smoke **13/13**；skill golden **24/24**；
  exemplar 契约 **323 条**，未见范例域错配率仍为 1.8%；L0 discovery 85/85、gate 25/25；
  capability integrity PASS；E2E CHECK OK。
- 首次完整批在候选 `2f5af9c` 得到 **7831 passed / 34 skipped / 2 failed / 5 warnings**；两红
  同源为 manual guide 与常驻 policy 合计 2794 字符，超过 2600 预算，运行时会 `!clipped`。
  未放大全局预算，而是删除与 exemplar 重复的两条 few-shot；合计降到 2450，余量 150，
  budget/skill/exemplar 专项 **94 passed**。
- 最终精确代码 SHA `b3a2aedd3c360c230709551502e5568e8bba8286`，在本机仅约 2.5GB 可用
  内存下以 `-n 2 --dist worksteal` 完整运行：**7833 passed / 34 skipped / 5 warnings**，
  721.49s；日志 `.artifacts/manual-rag-live-routing-fix/full-b3a2aed-n2.log`，SHA-256=
  `6bfd14fdb0bfe240efd0eff1bb247b541c3b578568933c005b7d55267bd23f61`。这是低并发完整批，
  不是固定 `-n 8` 读数。

### 6.4 发布与生产复验边界

当前生产仍是 `f2dcb46`，上述修复尚未 push/deploy；`.mrag` 包和 cloud infrastructure 未变，
无需重装模型或修改 schema。后续发布必须单独授权 push 和 deploy，并锁定 `b3a2aed`：dry-run
无阻断后部署，独立 status/verify；真栈复验先记录车态，再跑 36/36 和高风险 repeat 3，要求
单域 `manual.query`、内容 36/36、零 action、前后车态 diff=0。未经该证据不得称生产闭合。
