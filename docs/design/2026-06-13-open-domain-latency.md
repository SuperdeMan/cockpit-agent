# 开放域响应慢：模型分层 + 流式贯通 + 即时反馈

- **状态**：已落地并实测（2026-06-14）：流式贯通 + 模型分层 + chitchat 兜底 + 空响应重试 + 即时反馈全部上线；剩"降规划延迟快路径"待做（见文末落地记录）
- **交付对象**：后续开发者 / Agent
- **关联代码**：`llm-gateway/providers.py`、`.env.example`、`deploy/docker-compose.yaml`、`orchestrator/cloud/engine.py`、`orchestrator/cloud/executor.py`、`agents/chitchat/src/agent.py`、`hmi/src/App.tsx`、`hmi/src/components/ChatView.tsx`
- **现象**：开放域请求（"讲个笑话"、"我今天有点不开心"）响应慢、体验"卡死"。

---

## 1. 根因（模型 + 交互双重问题）

### ① 模型偏重
- 默认主模型 `mimo-v2.5-pro`（`.env.example:10`、compose `LLM_MODEL_PRIMARY` 默认 `mimo-v2.5-pro`，`docker-compose.yaml:45`），偏推理/thinking。**开放域闲聊用重模型 → 首 token 慢、整体慢**。闲聊根本不需要推理深度。

### ② 交互非流式（体验"卡死"的主因）
- `chitchat` **有** `handle_stream`（`agents/chitchat/src/agent.py:35`），但 `engine.run()` 走 `executor.run()` → `agent.handle()`（**非流式**），**整段生成完才** `yield {"kind":"final"}`（`engine.py:134`）。
- 旧 HMI 也**不消费** `speech_delta`。于是用户发出"讲个笑话"后面对**长时间空白**，直到整段回复一次性弹出——慢被放大成"卡死"。

### ③ 路由无快速兜底
- 开放域统一走云端慢系统（planner → chitchat），端侧无低延迟兜底。

---

## 2. 方案（三管齐下）

### A. 模型分层（对应 HMI 设置「对话模型 fast/deep/auto」）
- **开放域/闲聊/情绪** → **fast** 档（如 `mimo-v2.5`，非 thinking），低延迟。
- **复杂规划/工具调用** → **deep** 档。
- **auto**：按意图/agent 选档（chitchat→fast，planner 多步→deep）。
- `llm-gateway` 已有 primary/fallback 概念，扩展为**按 tier 选模型**；HMI 已把 `meta.model_pref` 透传（`hmi/src/settings.tsx: buildMeta`），后端据此选档。Agent 也可在 manifest 声明默认档位。

### B. 流式贯通（核心体验修复）
- 让**单 step 且 agent 支持 `handle_stream`** 的请求走流式：把 agent 的 speech delta 经 `HandleEvent.SpeechDelta` 逐段下发。
- 链路下游**已就绪**：cloud-gateway/channel 透传 `Event`，edge-gateway `eventToMap` 已映射 `speech_delta`（`gateway/edge/main.go`），**HMI 新版已消费**（`ChatView` 流式 caret 渲染，`App.tsx: handleEvent` 处理 `speech_delta`）。
- **唯一缺口在 engine**：`engine.run()` 经 executor 只走非流式 `handle`。需新增"流式直通"路径——当 plan 为单 chitchat step（或 agent 声明可流式）时，直接驱动 `handle_stream`，把 delta 以 `{"kind":"speech"}` yield，最后再 `final`。

### C. 即时反馈 ✅ 2026-06-13 已落地（纯前端）
- HMI 发送后**立刻插入"思考中"占位**（EQ 动画 + "正在思考…"，`ChatView: ThinkingDots`），慢响应不再是死寂空白；若后端流式下发则占位转为逐字流式。**这部分已交付，无需后端配合即生效**。

### D. 情绪场景专门优化
- "我今天有点不开心"应走**情绪陪伴**：chitchat 的 system prompt 已"温暖、简洁、口语化"（`agents/chitchat/src/agent.py:13`）。建议叠加：情绪类走 fast 档 + 共情优先（先共情再开放问题），避免长篇。

---

## 3. 分阶段落地

| 阶段 | 内容 |
|---|---|
| **P1** | ✅ 即时反馈（已交付）；后端流式贯通（engine 单 step 流式直通 chitchat）；模型分层最小版（chitchat 强制 fast 档） |
| **P2** | `meta.model_pref` 全量接入（fast/deep/auto 按意图择优）；情绪意图识别 + 共情模板 |
| **P3** | 端侧开放域快速兜底（小模型/模板）；首 token 延迟指标纳入可观测 |

---

## 4. 验收

- **流式**：发"讲个笑话"后**亚秒级**出现"思考中"，随后**逐字**流出（首 token 明显早于整段完成）。
- **模型分层**：HMI 切 fast/deep 生效；闲聊默认走 fast、延迟显著下降。
- **情绪**：发"我今天有点不开心"得到**共情且简洁**的回应，不长篇大论。
- 指标：闲聊首响应延迟（首 token）作为回归指标（接 task：可观测接线后纳入）。

---

## 5. 风险与取舍

- **流式直通的边界**：只对**单 step 可流式 agent** 开；多 step DAG 不流式（先聚合再播报），避免乱序。
- **fast 档质量**：低延迟模型可能牺牲质量；auto 档要保证规划/工具类仍走 deep。
- **TTS 与流式**：HMI 已按完整短句增量合成并顺序播放；仍不是真正的服务端 PCM
  音频流，首句时延和队列积压需在真实车载网络继续测量。

---

## 落地记录（2026-06-14）

**已实现并实测**：
- engine 单步计划**流式直通**（`Agent.ExecuteStream`，`engine._orchestrate`），逐段下发 `speech_delta`；多步/确认续接保持 executor，F1 闭环零改动；流式不可用回退 unary。
- chitchat **模型分层**：开放域默认 `LLM_MODEL_FAST`，`model_pref=deep` 才用 primary；`answer_length`/`assistant_name` 经 meta 生效。
- `_fallback` **兜底到 chitchat**（系统全局 fallback），规划 LLM 抽风时仍有回应。
- chitchat **空响应兜底重试**（MiMo 偶发空 `content`）。
- HMI 即时反馈 + 流式渲染（已随前端重构上线）。
- 实测：开放域连续多次均正常流式（10–16 段增量，非空）。

**关键新发现（2026-06-13 验证的延迟结论）**：
> 首 token 实测 **5–12s，几乎全部耗在 Planner**——它对**每个云侧请求**都先用 `mimo-v2.5-pro`（推理模型）做规划，chitchat 流式本身很快（增量 <1s 出完）。即"开放域慢"的真正瓶颈不是回复模型，而是**规划器用了重推理模型且对所有请求一视同仁**。`mimo-v2.5-pro` 还会间歇性返回空 `content`，触发重试/兜底，进一步拖慢。

**下一步（降规划延迟，未做）**：
1. **开放域快路径**：在 Planner 前用廉价分类（端侧/关键词/小模型）识别"纯闲聊/情绪"，直接路由 chitchat，跳过 LLM 规划。
2. **规划器换快模型**：给 Planner 用 `LLM_MODEL_FAST`——但实测 `mimo-v2.5` 对严格 JSON 规划提示会返回空，需要先调 prompt（更强约束 + 更大 token 预算）后再切。
3. 首 token 延迟纳入可观测指标。
