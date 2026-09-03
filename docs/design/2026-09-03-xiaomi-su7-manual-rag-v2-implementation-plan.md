# Xiaomi SU7 真实手册 RAG v2：问句落域与视觉证据 implementation plan

> 状态：实施中（2026-09-03）  
> 基线：`origin/main=9774932384c23cc27d39759891b39fbe9fe1235d`；生产 release
> `a406e222b3fe08ea462c06ccf676d0698f1f443a`  
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
- 构建器从同页正文截取该正式名称到下一名称之间的手册说明，作为受控 `description`。
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
