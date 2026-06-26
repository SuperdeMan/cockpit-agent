# deep-research Agent — 深度调研

独立的「深度调研」一等 Agent：把一个主题拆成多视角子问题、并行联网检索正文级资料、
产出**带引用的分节报告** + **一段式语音简报**。对症单轮检索（`info.search`）在多跳/对比/
时间线问题上的结构性天花板（漏后跳证据）。

> 与 `info` 的分工：`info.search` 是**单轮快查**（秒级，天气/简单事实/概念）；本 Agent 是
> **有界多轮深调研**（数十秒，"深入调研/全面对比/系统了解 X"）。编排层按措辞分层路由
> （`planning._ensure_research_step` 确定性兜底，弱 LLM 误判时纠偏到 `research.run`）。

## 能力
| intent | 说明 | slots |
|---|---|---|
| `research.run` | 对一个主题做深度调研，产出分节报告卡 + 语音简报 | query / topic / question |

## 四段流水线（`src/pipeline.py`）

「LLM 提议 / 确定性落地」——事实全部确定性产出，LLM 只在 (a) 提议子问题、(c) 受约束合成：

1. **plan** — LLM 把问题拆成 3-5 个**带视角**子问题（STORM 多视角：背景/对比/风险/最新进展/适配用户），
   只产 JSON、不产结论；解析失败 → 确定性兜底（单子问题=原问题）。thinking 关（结构化 JSON）。
2. **investigate** — 确定性**有界并行**迭代检索：每子问题经 `_sdk/retrieval` 检索正文级资料
   （`asyncio.gather` 并行压延迟）；空结果换更宽 query 再追一轮（`max_subq≤5 × max_rounds≤2`）。
3. **synthesize** — 复用 `_sdk/grounding` 的「强制引用 + 无依据弃权」内核，升级为**分节报告**
   （每子问题一节，结论+引用编号+置信度，全局来源去重编号，未覆盖写进 `gaps`）。thinking 自动开（深合成）。
4. **brief** — 确定性渲染：一段式 TTS 简报（行车听）+ `research_report` 卡（泊车/手机读）。

## 座舱差异化（护城河）
不做「车机版 Perplexity」，而是 **接地「我」+ 渐进语音 + 可落地产物**：把车辆上下文（位置/电量/行程）
与画像偏好作为研究的隐含约束（P0 接时间/电量，**P1 接位置/画像**）；行车给语音简报、泊车给可读报告；
报告可（P1）存记忆/推手机/转导航。复用已建的**四阶段过程区**做进度反馈，proto 不用改。

## Provider 复用
进程内复用 **info 的搜索 provider**（`build_search_provider`：Exa 正文级→AnySearch→Bing→mock 降级链）
与正文补抓（`build_extractor`），跟随 `trip_planner→navigation` 先例——info 拥有搜索 provider，本 Agent 复用、
不重复造轮子。真正中立的检索/接地合成内核抽在 `agents/_sdk/{retrieval,grounding}.py`，与 info 共享。

## 测试
```bash
python -m pytest agents/deep_research/tests --import-mode=importlib -q
```
单测用 fake llm/search，不联网、不打真实 LLM：plan 解析/视角/兜底、investigate 有界并行/gap、
synthesize 分节引用/全局来源/诚实 gaps/兜底、brief 双态产物、agent 端到端 + manifest 一致性。

## 阶段
P0 已落地（四段流水线 MVP + 分层路由 + 报告卡）。P1（接地画像/多轮研究上下文/推手机）、
P2（新闻深挖桥接/异步分钟级深调研）见 `docs/design/2026-06-26-info-agent-deep-research-redesign.md`。
