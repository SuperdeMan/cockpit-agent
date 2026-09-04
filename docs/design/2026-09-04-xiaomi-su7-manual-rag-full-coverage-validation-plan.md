# Xiaomi SU7 整本手册 RAG 全覆盖验证计划

> 状态：**全范围验证已完成；本地修复候选待发布验证**（2026-09-04）
> 初始基线：`origin/main@aa9f73a`；实现提交 `55414e0`；全量前合入主线 `54a7afe`
> 当前生产 release：`7b594f379c1fbfb5156c55c4e0dc573957b49d28`；`434a046` 结果保留作历史对照
> 私有输入：`2024-小米SU7-Pro-Max-用户手册.pdf`，SHA-256=`ef16d204…e4705d`

## 1. 验证结论的边界

“整本手册范围”定义为手册中有限、可枚举的源页面、文本页、目录路径与受控视觉资产全部有
验证结果；它不等于对所有可能自然语言问法证明 100% 准确。原 36 题仍作为真实用户泛化集
独立报告，不把目录标题或正文原句生成的覆盖题混入泛化准确率。

## 2. 五层覆盖面

| 层 | 分母 | 判据 |
|---|---:|---|
| 源文件重建 | 278 页 | 从原 PDF 重提文本和视觉，必须与 `.mrag` 的 index/visual manifest 逐字一致；每个未形成文本 chunk 的页面有明确页号 |
| 文本页可达性 | 269 个 chunk/物理页 | 从每页正文独立选择高 IDF 原文锚，目标页必须进入 Top-4；Top-1 单列，不用 Top-4 洗成 Top-1 |
| 索引路径召回 | 160 个唯一 `section_path` | 验证构建器合并后的在线引用路径全部可达；Top-1 单列 |
| PDF 目录叶子 | 187 个三级 outline 叶子 | 同页多主题拆成独立问题，目标物理页及原子路径词必须同时命中；这是“整本章节覆盖”的主分母 |
| 视觉完整性/语义 | 350 个放置、299 个 blob、17 个 skipped；35 个唯一受控 caption | 包内 hash/MIME/大小全验；受控 caption/alias 问法必须返回对应图片。装饰图只验完整性，不伪造语义标签 |

另跑现有 36 题（27 main / 9 holdout，29 正例 / 7 负例）作为用户问法泛化层。所有层分别出
分母、失败明细和延迟，不合并成一个“总准确率”。

## 3. 实施任务

1. 新增 `manual_rag_full_coverage.yaml`，冻结 source/index/section/page/visual 的数量与 digest；
2. 新增 `eval_manual_rag_full_coverage.py`：源 PDF 重建对账、全页锚、160 个索引路径、187 个
   outline 叶子、全视觉语义和原 36 题分层报告；
3. 扩展通用 retrieval evaluator，使其能断言精确 `section_path`，并补反向单测；
4. 先运行当前实现形成 RED；若是产品缺口，只做最窄修复并保留反例；若是题目不成立，修改
   评价口径并写清理由，不为追求全绿降低阈值；
5. 离线全绿后，先以生产 `434a046` 跑目录路径和视觉语义 WS 探针；并行 mobile 发布把生产推进
   到 `7b594f37` 后，再按同一口径完整重跑，逐轮检查单一 manual 卡、approved real provenance、
   预期页/路径/图片、零 action、零 need_confirm 和完整车态 diff={}；
6. 代码、生产 release 与 artifact 分栏。任何新修复的 push/deploy 仍需单独授权。

## 4. 验收线

- PDF source hash/page count、重建 index 与 visual manifest：逐字一致；
- 文本页 Top-4：269/269；索引路径 Top-4：160/160；outline 叶子 Top-4：187/187；
- 视觉资产完整性：350/350，299/299 blob；17/17 skipped 有受控原因；
- 受控视觉语义：35/35；原 36 题：36/36；
- 生产 WS：outline 叶子187/187、视觉35/35，所有轮次零动作、零确认、车态不变；
- 任何一层未达线时，结论必须写“已完成全范围验证但存在 N 个失败”，不得写整本全绿。

## 5. 实施结果

### 5.1 分母校准与探针安全修正

- PDF 原生 outline 共 229 个节点，其中 187 个三级叶子；34 个物理页同时开启多个叶子。
  原先按索引 `section_path` 得到的 160 是合并引用路径，不能当作整本目录分母；两项现已分栏。
- 初版真栈 query 是“请查…+章节名”的名词短语，`请` 会进入指令形态；13 轮试跑中
  `空调滤芯更换 / 检查冷却液` 被执行成一次 `hvac.on`。基线本来已经 `hvac_on=true`，所以
  车态 diff={}，但 action 本身仍是失败；探针立即中止，没有发送反向车控。
- 最终 query 使用“我想知道 SU7 用户手册里…？”；187/187 均通过 question-shape，且
  FastIntent 187/187 返回 None。探针现以 action、need_confirm、任一完整车态差异为硬停止。
- 裸目录标题压力批为 157/187，仅作为被推翻的评价器基线保留；最终准确率只取自然化批。

### 5.2 原 PDF 与离线全范围

- 源 PDF 278 页重提后与现有 `.mrag` **逐字一致**：269 个文本页/chunk，未形成文本 chunk 的
  9 页为 `[1,2,8,28,56,180,210,222,278]`；content SHA 未变。
- 视觉重提与包内 manifest/blob **逐字一致**：350/350 放置、299/299 去重 blob；17/17 skipped
  均保留受控原因。
- 候选代码离线结果：页面 Top-4 269/269（Top-1 267）；索引路径 Top-4 160/160（Top-1 152）；
  outline 叶子 Top-4 187/187（Top-1 158）；受控视觉35/35；原自然问法36/36；显式手册来源
  route hint 222/222。
- Artifact：`.artifacts/manual-rag-full-coverage/offline-full-candidate.json`，SHA-256=
  `01ebb3c34bff7a6273176c268bfa7ff5440ff7d6a4ef9947475bb92439fa80d8`。

### 5.3 历史生产 `434a046`：章节范围已测完但非全绿

- 187 个 outline 叶子首轮 **177/187（94.65%）**；p50=11259.685ms、p95=21429.625ms、
  max=38864.255ms。
- 10 个首轮失败各补两次：9 个为 **2/3**，`动力性参数`为 **1/3**；没有 0/3 的稳定失败。
  这证明所有187个叶子都至少一次走通生产完整链路，但不能把它写成“187/187稳定通过”。
- 主批及两次失败复验共207轮，action=0、need_confirm=0、probe error=0、完整26项车态 diff={}。
- 主 artifact：`.artifacts/manual-rag-full-coverage/live-outline-naturalized-187-434a046.json`，
  SHA-256=`af0c100f332e312b5f9f710dfb36caca9a570eb9e3d76635253274f490aa47d5`；两次复验 SHA 为
  `b66f3c36…077ff`、`606c6354…2e83`。

### 5.4 历史生产 `434a046`：视觉存在5个稳定缺口

- 受控视觉首轮 **28/35（80.00%）**；p50=11006.899ms、p95=22202.695ms、
  max=29050.506ms。
- `位置灯 / 左转向 / 右转向 / 后雾灯 / 近光灯` 三次均返回 manual 零命中卡，**0/3 稳定失败**；
  根因是三字 caption 未进入运行时受控视觉匹配表。
- `冷却液温度过高指示灯 / 胎压监测报警指示灯` 首轮 RuntimeError，后两轮均通过，均为2/3。
- 主批及两次失败复验共49轮，同样 action=0、need_confirm=0、probe error=0、车态 diff={}。
- 主 artifact：`.artifacts/manual-rag-full-coverage/live-visual-35-434a046.json`，SHA-256=
  `5b5a2e40776fde5da3c2714672c88d880028f974ef80b209bfeca117cc62d17f`；两次复验 SHA 为
  `c8a8a2bb…2896`、`2587a63c…ab92`。

### 5.5 当前生产 `7b594f37`：整本复跑结果

- 2026-09-04 状态回读为5/5 endpoint healthy、零 warning；本轮真栈严格绑定完整 release
  `7b594f379c1fbfb5156c55c4e0dc573957b49d28`、`minimax:MiniMax-M3`。
- 187个 outline 叶子首轮 **181/187（96.79%）**；p50=8167.950ms、p95=14633.654ms、
  max=22570.331ms。6个首轮失败后两轮均6/6，故6项都是2/3、没有0/3稳定章节失败；其中
  `哨兵模式`首轮是一次 opening-handshake timeout，其余是模型落到澄清/非manual出口的方差。
- 章节主批与失败复验共199轮，action=0、need_confirm=0、完整26项车态diff={}；主批有上述
  1次transport probe error，不能写成零transport error。主artifact
  `.artifacts/manual-rag-full-coverage/live-outline-naturalized-187-7b594f37.json`，SHA-256=
  `4381283cbec65de900ec0a1c95dc2e10fca1de042a04a6e242da9513307eb4f5`；两次复验SHA为
  `f0f77c53…aeac9`、`5143338e…cd3f`。
- 35个受控视觉首轮 **30/35（85.71%）**；p50=7149.769ms、p95=11311.487ms、
  max=12530.943ms。`位置灯 / 左转向 / 右转向 / 后雾灯 / 近光灯`仍为0/3稳定失败，三轮均
  缺PDF页、图片页与caption；其余30项首轮即返回预期图片。
- 视觉主批与失败复验共45轮，action=0、need_confirm=0、probe error=0、车态diff={}。主artifact
  `.artifacts/manual-rag-full-coverage/live-visual-35-7b594f37.json`，SHA-256=
  `b6ff52faac9f4e22996b7bc63e360351b363ec2f058f6f9fe94a0e1fe8659cb6`；两次复验SHA为
  `0e457caa…d7da1`、`32b6059f…f3def`。
- 真栈探针新增显式`--kind natural`并直接复用原36题契约。整批在联网前被安全预检拦下7条旧
  表述（如“充电口怎么手动打开”“后备箱怎么应急打开”会被FastIntent视为控制命令），因此
  **没有**把当前release写成36/36，也没有为了补数字放宽安全闸。
- 用户点名的两条安全问法单独各跑3次：`雨刮器怎么打开`为3/3，三次均命中PDF第95页及
  `前风挡雨刮拨杆开关操作示意`；`我的仪表上有个小人背着把宝剑的灯亮了是怎么回事`为3/3，
  三次均命中PDF第193页及`安全带未系提醒指示灯`。6轮均零action/确认/probe error、车态diff={}。
  六个artifact SHA依次为`66e72148…908cb`、`fe73d9bc…515c`、`3aefd6a5…775d`、
  `3022151d…b572`、`44f113b1…c5be`、`2e97a64a…abb9`。

### 5.6 本地修复候选与发布边界

- `local_index.py` 只让三字正式 caption 在“图标/指示灯/仪表/亮灯”等视觉语境下匹配；
  “后雾灯怎么打开”反例仍命中操作页。现有 `.mrag` 已包含这些 caption，包体与基础设施 hash
  均无需变化，离线视觉由30/35升至35/35。
- manifest 0.3.2 新增“SU7/小米SU7 + 手册 + 具体主题 + 问号”的窄显式来源 hint；明确
  “帮我打开/关闭/设置”由 guard 保留原车控计划。`eval_route_hints` 110/110，187章节+35视觉
  的显式手册问法离线222/222。
- 候选代码SHA `62533dd9dca2e193e6f64c33ec6462cd974e69c4` 的纯串行全仓结果为
  **7855 passed / 34 skipped / 4 warnings / 0 failed**，日志SHA-256=
  `72f1d16843e30c6bf9df971e53296531cf896020aeefb64246d642cb1ed6fe42`；架构扫描器同时排除了
  `.artifacts`验证环境，并以反向用例锁住，避免第三方site-packages污染门禁。
- 当前生产是 `7b594f37`、5/5 endpoint healthy；候选尚未push/deploy，故生产结论仍是章节首轮
  96.79%、视觉85.71%、5个视觉caption稳定失败，**不是整本全绿**。当前release没有成功的
  统一verify artifact，不得沿用2026-09-03 `434a046`的verified字样冒充本轮证据。
