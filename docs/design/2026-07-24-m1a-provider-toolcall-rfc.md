# M1a 子 RFC：Provider tool-calling 兼容 + `submit_plan` 结构化规划输出 V1

> 日期：2026-07-24
> 状态：实施中（依据 `2026-07-24-eva-benchmark-intelligence-upgrade.md` §4.B / §6-M1a，泓舟已拍板开工顺序 §8-7；M1a 开工首件事=本 RFC，v1.2 既定）
> 范围：**V1 单一 `submit_plan` 工具做结构化输出**。不做 V2 真 agentic tool loop（多轮 tool 消息、流式 tool delta、proto 演进），不动 T2 预算，不动执行语义。

---

## 0. TL;DR

1. 规划轮的 LLM 出口从「文本补全 + `_extract_json` 硬解析」升级为「原生 function calling 强制输出合法 Plan JSON」。**零语义变化**：schema 顶层 = 现 JSON 协议顶层（complexity/goal/addressed/steps/clarify），下游 `_validated_steps`/DAG Executor/VAL/确认链全不动；schema **不含 `require_confirm`**（确认权不在 LLM，M0a 已中央落实）。
2. 承载走**现有 proto Struct 字段**（`CompleteRequest.tools` field 5 / `CompleteResponse.tool_calls` field 6，从未使用），不改 proto。线格式取 OpenAI 形状（四家直通零转换，legacy anthropic 由 provider 转换）。
3. 改动面四件：`providers.py`（新增 `complete_tools`，**不改存量 `complete` 契约**）→ `server.py`（透传/回填）→ `orchestrator/cloud/clients.py`（`llm_complete_tools`）→ `planning.py`（`PLANNER_TOOLCALL=on|off` 灰度双路径，off 默认）。
4. 降级链**轮内闭合**：toolcall 协议失败（HTTP 4xx/无 tool_calls/arguments 畸形）→ 同轮内容抢救 → 下轮直接 JSON 路径。最坏 2 次 LLM 调用与现状重试上限一致，不加延迟预算。
5. DoD 两层分 provider 统计（母提案 §4.B）：协议层 = tool call 成功率/schema 通过率/解析错误归零；功能层 = mode_routing 122 + journeys regression 15 对照不低于 JSON 路径、P95/token 增量在预算内。

---

## 1. 现状（file:line）

- `proto/cockpit/llm/v1/llm.proto:35/45`：`CompleteRequest.tools`、`CompleteResponse.tool_calls` 均为 `google.protobuf.Struct`，**从未有生产者/消费者**。`Message` 无 `tool_call_id/name`、`CompleteChunk` 无工具增量——足够 V1（单轮单工具），不够 V2（外部评审结论，母提案 §9 已采纳）。
- `orchestrator/cloud/planning.py:441-444`：`_extract_json` 从首 `{` 截到末 `}`；`build()` 重试 1 次后 fallback（`planning.py:177-193`）。历史工程债：合成 JSON 截断、裸引号边界抢救（memory：mode-routing 批次）。
- `orchestrator/cloud/clients.py:115-141`：`llm_complete` 纯文本进出；请求级 pin（`meta.llm_provider/llm_model`）、trace/caller_service 已收口在此。
- `llm-gateway/providers.py:295-336`：`OpenAICompatibleProvider._build_body` 按 `token_param/thinking_style` 参数化四家差异；`complete` 返回 4 元组 `(content, model_used, finish_reason, usage)`。
- `llm-gateway/server.py:100-205`：`Complete` 带缓存/限流/429 语义/降级链（primary→fast）；`tools` 字段不读。
- Planner 调用恒 `thinking=False`（`clients.py:118-119`：结构化 JSON 不能被 reasoning 吃空）、`temperature=0.3`。

## 2. Provider 兼容矩阵（2026-07-24 文档调研；★=待真机探针钉死）

线上四家（`llm_runtime._PROVIDER_SPECS`）+ legacy anthropic + mock：

| provider | 端点/模型 | tools 请求 | tool_choice | 响应 tool_calls | 思考交互 | 已知坑 |
|---|---|---|---|---|---|---|
| **mimo** | token-plan-cn / mimo-v2.5-pro | OpenAI 形状（官方多轮示例确认） | `"auto"` 官方示例确认；named 强制 ★ | OpenAI 形状，`arguments`=JSON string | thinking 模式伴随 `reasoning_content` + tool_calls；我们恒关思考 ★ | **多轮 tool 历史（assistant.tool_calls + role:tool 回传）400 Param Incorrect（GitHub XiaomiMiMo/MiMo#44）——V1 单轮不回传 tool 消息，免疫**；V2 前置依赖该 bug 修复 |
| **minimax** | api.minimaxi.com / MiniMax-M3 | OpenAI 形状；**拒收 deprecated `function_call`** | `"auto"` 支持；named/required ★ | OpenAI 形状，`finish_reason="tool_calls"` | thinking 缺省开、我们发 `thinking:{type:disabled}` 关；关思考下 tool call ★ | 部分 OpenAI 参数（presence_penalty 等）静默忽略；`<think>` 内联泄漏已有 provider 层剥离 |
| **deepseek** | api.deepseek.com / v4-pro·v4-flash | OpenAI 形状 | 文档含 tool_choice；取值明细 ★ | OpenAI 形状 | **V3.2 起 thinking 模式官方支持 tool use**；我们恒关思考，风险更低 | strict 模式（schema 硬约束）是 beta：须换 `api.deepseek.com/beta` base_url——**V1 不用**（不动生产 base_url；软 schema + 下游校验已足） |
| **qwen** | dashscope compatible-mode / qwen3.7-max·plus | OpenAI 形状（Model Studio 文档：通用文本模型均支持 function calling） | auto/none/named（文档）；★ | OpenAI 形状；支持 `parallel_tool_calls` | `enable_thinking:false` 恒发（thinking_style=qwen）；兼容模式 function calling 与关思考正交 ★ | Omni-Realtime 系不支持 tool_choice——不涉及（文本模型） |
| **anthropic**（legacy） | Claude SDK | **专有形状**：`[{name, description, input_schema}]` | `{"type":"tool","name":...}` 强制原生支持 | `content` 里 `tool_use` block，**`input` 是 object 非 string**；`stop_reason="tool_use"` | 与 extended thinking 正交（未接线） | 仅 `LLM_PROVIDER=anthropic` 时注册；泓舟栈无 key，代码路径实现但不进 A/B |
| **mock** | — | 忽略 | — | 恒空（`complete_tools` 基类默认） | — | 无 key 栈走 JSON 回退路径，离线 e2e 不破 |

**矩阵结论**：四家线格式同构（OpenAI Chat Completions tool calling），差异集中在 ①named/required `tool_choice` 的真实支持度、②关思考下是否稳定出 tool_calls——两者都无法从文档钉死（本仓库教训：ASR 双协议、DeepSeek thinking 探测都是文档≠实测），**统一进真栈探针**（§7）。设计上用「双保险」使探针结果只影响效果不影响正确性：prompt 明确指令 + named tool_choice；任何一环失效都落回 JSON 抢救/回退，行为不劣于现状。

> **★ 已钉死（2026-07-24 真栈探针 `test/e2e_planner_toolcall.py`，4 家 × 4 句形态 16/16）**：
> named `tool_choice` 四家全部尊重、arguments 全部合法 object、必填字段（addressed/steps 形态）全齐、
> 关思考下 tool_calls 稳定。唯一差异：**qwen 的 `finish_reason` 恒 `"stop"` 而非 `"tool_calls"`**
> （DashScope 兼容模式怪癖）——消费端按 tool_calls 置位判断、不依赖 finish_reason，无影响；
> 写进契约注释防未来有人按 finish_reason 分支。

## 3. 协议设计

### 3.1 Struct 线格式（网关内部契约，不改 proto）

```jsonc
// CompleteRequest.tools（Struct）——OpenAI 形状直载
{
  "tools": [ { "type": "function", "function": { "name": "...", "description": "...", "parameters": { /* JSON Schema */ } } } ],
  "tool_choice": "auto" | "none" | "required" | {"type": "function", "function": {"name": "..."}}
}

// CompleteResponse.tool_calls（Struct）——网关归一化后回填
{
  "tool_calls": [ { "id": "call_x", "name": "submit_plan", "arguments": { /* 已解析为 object */ } } ]
}
```

归一化规则（provider 出口统一，任何调用方不再管各家差异）：
- OpenAI 系：`message.tool_calls[].function.arguments`（JSON string）→ `json.loads` 为 object；**畸形丢弃该条 + warning**（刻意不做字符串抢救——服务端约束下畸形率本应趋零，抢救属 JSON 路径的历史债，不带进新通道）。
- anthropic：`tool_use` block 的 `input` 天生 object，直取；`id`/`name` 对位。
- 全部畸形/无 tool_calls → 回填空 + `content` 原样返回，调用方按无工具调用处理。
- `finish_reason` 透传（OpenAI 语义 `"tool_calls"`；anthropic `stop_reason="tool_use"` 原样）。

### 3.2 `submit_plan` 工具 schema（flat=现 JSON 协议顶层，语义零漂移）

参数顶层**就是**现 JSON 协议的顶层对象——`_parse_and_validate` 的 dict 校验部分直接复用，A/B 对照才是「只差输出通道」的单变量实验：

```jsonc
{
  "name": "submit_plan",
  "description": "提交本轮规划结果。这是唯一合法的输出通道。",
  "parameters": {
    "type": "object",
    "properties": {
      "complexity": {"type": "string", "enum": ["simple", "adaptive"]},
      "goal": {"type": "string"},
      "addressed": {"type": "boolean"},   // 受话判定，协议要求必须输出
      "steps": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "agent_id": {"type": "string"}, "intent": {"type": "string"},
        "slots": {"type": "object"},
        "depends_on": {"type": "array", "items": {"type": "string"}},
        "slot_refs": {"type": "object"}
      }, "required": ["id", "agent_id", "intent"]}},
      "clarify": { /* 仅 CLARIFY_ENABLED=on 时并入（与 _CLARIFY_SECTION 同门控同频）*/ }
    },
    "required": ["addressed", "steps"]
  }
}
```

- **无 `require_confirm`**（v1.2 拍板：确认权不在 LLM；`_validated_steps` 只读 capability manifest，M0a 已落实）。
- slots 值宽进：`_validated_steps` 既有 `str(v)` 归一兜底，schema 不过度约束（strict/additionalProperties 是 DeepSeek beta 专属要求，V1 不用）。
- `tool_choice` 恒发 named 强制 `{"type":"function","function":{"name":"submit_plan"}}` + prompt 指令双保险；某家不认（4xx/忽略返回文本）→ §4 降级链承接，fallback 率进分 provider 统计。

### 3.3 system prompt 适配（双路径共享领域协议）

toolcall 模式**不改** `_PLANNER_BASE`/受话段/澄清段（JSON 协议描述同时是 schema 语义说明，双路径共享=A/B 变量隔离），仅末尾追加 `_TOOLCALL_SECTION`：「上述所有输出协议一律通过调用 `submit_plan` 工具提交（顶层 JSON 对象=工具参数）；禁止以文本输出 JSON 或解释」。

## 4. 端到端改动面与降级链

```
planning.build()                                # PLANNER_TOOLCALL=on 且 llm_tool_fn 注入时
  └─ clients.llm_complete_tools(messages, tools)   # pin/trace/caller_service 与 llm_complete 同源
       └─ gRPC Complete(tools=Struct)              # server._serving/缓存/429/降级链复用
            └─ provider.complete_tools(...)        # 新方法；基类默认=complete + 空 tool_calls
```

- **providers.py**：`BaseProvider.complete_tools(messages, model, temperature, max_tokens, tools, tool_choice, thinking, timeout_s) -> (content, model_used, finish_reason, usage, tool_calls)`；基类默认回落 `complete`+`[]`（Mock/未覆盖 provider fail-open）。`OpenAICompatibleProvider` 注入 body["tools"]/["tool_choice"] + 归一化解析；`AnthropicProvider` 做形状转换。**存量 `complete` 4 元组契约零变化**（改签名会波及全仓 fake/测试，权衡见 §8-1）。
- **server.py**：`Complete` 读 `request.HasField("tools")` → MessageToDict → 选 `complete_tools`；tool_calls 回填 `CompleteResponse.tool_calls`。**带 tools 的请求跳过缓存**（planner 上下文轮轮不同命中率≈0，跳过换正确性；缓存键改造留 V2）。`CompleteStream` 不支持 tools（planner 走 unary；带 tools 打 stream 记 warning 忽略，边界写进注释）。
- **clients.py**：`llm_complete_tools(messages, tools, max_tokens) -> (content, tool_calls)`；meta 构造与 `llm_complete` 提公共 helper。
- **planning.py**：`PlanBuilder(llm_fn, registry_fn, llm_tool_fn=None)`——第三参可选，**存量测试/spy 零波及**；`_parse_and_validate` 拆出 dict 直入的 `_parse_and_validate_data`（字符串版内部调用它，校验语义单源）。
- **降级链（轮内闭合，最坏 2 次调用=现状重试上限）**：
  1. 第 1 轮 toolcall：拿到合法 arguments → `plan_mode="toolcall"`；
  2. 无 tool_calls 但 content 可解析（模型无视工具直接文本 JSON）→ 同轮抢救，`plan_mode="toolcall_salvage"`；
  3. 异常/均失败 → 第 2 轮直接 JSON 路径 → `plan_mode="toolcall_fallback"`；再失败走既有 `_fallback`（chitchat/语义路由）。
  4. `PLANNER_TOOLCALL=off`（默认）→ 纯既有路径，`plan_mode="json"`，字节级零变化。
- **replan（T2）不在 V1**：触发率低、schema 另立（done/steps），机制同构留 V1.1；A/B 聚焦主规划单变量。

## 5. 观测

- `Plan.plan_mode` 新字段（models.py，观测专用不参与编排），engine 的 `cloud.planning` span attrs 增 `plan_mode`（与 M0b `skills` 属性同点位，`engine.py:232-246`）——A/B 报告从 obs.db 按 provider×plan_mode 聚合。
- llm-gateway `obs.llm` 既有事件天然覆盖（模型/tokens/时延按 trace 归档）；toolcall 请求的 `content_head` 为空时以 tool_calls 名单补记（provider 层 content 为空是 tool call 的正常形态）。

## 6. 灰度与运营口径

- `PLANNER_TOOLCALL=on|off`，**默认 off**：`.env.example` + `deploy/docker-compose.yaml`（cloud-planner 服务）。
- 开关是**全局**的（不做 per-provider 矩阵配置——复杂度不换收益）；哪些 provider 达标启用是 A/B 报告之后的运营决策：达标 → 默认翻 on；个别 provider 协议层不达标 → 报告记录，切到该 provider 演示时手动 off（与「单一大脑手动 pin，不自动 failover」既有决策同风格）。

## 7. 真栈探针与 A/B 计划

1. **协议探针** `test/e2e_planner_toolcall.py`：经网关（请求级 pin 逐家）发带 `submit_plan` 的 Complete×代表句（单意图/多意图/依赖/受话 false），输出矩阵报告：tool_calls 返回率、arguments 可解析率、必填字段齐全率、named tool_choice 是否被尊重（返回文本=被无视）。**钉死 §2 的 ★ 项。**
2. **功能 A/B（分 provider，先 @minimax 与 @mimo 两主力）**：
   - `eval_mode_routing --live` 122 条：on vs off 组内对照（同 provider 锁定，M0b 步②同款打法）；
   - `journeys --provider <pid>` regression 15：on 下必须 15/15；
   - P50/P95 与单轮 token：obs.db `llm_calls` 聚合，预算=P95 增量 ≤10%、单轮 token 增量 ≤15%（tools schema 进 prompt 的固有成本，超预算即报告并保 off）。
3. **DoD（M1a 收口）**：协议层=启用 provider 解析错误归零（探针+eval 全程 0 次 arguments 畸形/0 次 `_extract_json` 抢救介入）；功能层=mode_routing 不低于对照、journeys 15/15、预算内；单测契约绿+全量回归不劣化。

## 8. 决策记录（本 RFC 内的取舍）

1. **`complete_tools` 独立方法而非改 `complete` 5 元组**：`async def complete(` 在仓内 fake/测试 8+ 处按 4 元组契约实现，改签名全仓波及；独立方法基类 fail-open 默认，存量零波及、新契约测试面独立。
2. **线格式取 OpenAI 形状**：四家直通零转换；anthropic 单家在 provider 层转换（`input_schema`/`tool_use`）——转换放最少数一侧。
3. **arguments 网关归一化为 object、畸形不抢救**：抢救逻辑（截断/裸引号）是 JSON 文本路径的历史债；tool-calling 的价值就是服务端约束，畸形=协议失败，诚实回退并计数（协议层指标），不把新通道建在抢救上。
4. **带 tools 跳缓存**：正确性优先（tools 不进缓存键会串味），planner 缓存命中率本就≈0。
5. **flat schema（顶层=现 JSON 协议）**：`submit_plan(plan: …)` 包一层 plan 参数会让消费方多拆一层且 token 更贵；flat 让 `_parse_and_validate_data` 直接复用，语义单源。
6. **replan 不进 V1**：控制变量 + 触发率低；V1.1 机械扩展（`submit_replan`）。
7. **clarify 进 schema 与 `_CLARIFY_SECTION` 同门控**：off 时 schema 无 clarify 属性，防 schema 反向引导模型输出澄清（prompt/schema 两侧一致性）。

## 9. 风险

| 风险 | 缓解 |
|---|---|
| 某家 named tool_choice 不支持 → 每轮 4xx 白打一跳 | 轮内降级闭合（最坏 2 次=现状上限）；探针先行钉死，不达标 provider 不启用 |
| 关思考下 tool call 行为未知（MiniMax/MiMo） | 探针项；失败形态=返回文本 JSON，salvage 路径承接零丢失 |
| tools schema 抬 prompt token 成本 | A/B token 预算硬指标（≤15%），超即保 off |
| 模型经工具输出的 slots 类型漂移（number/bool） | `_validated_steps` 既有 `str(v)` 归一；schema 不设 strict |
| MiMo 多轮 tool 历史 400（issue #44） | V1 单轮不回传 tool 消息，结构性免疫；V2 前置条件记录在案 |

## 10. 不做清单（V2 边界，均已在母提案 §4.B 定界）

真 agentic tool loop（模型期待工具结果继续推理）、typed `ToolDefinition`/`tool_call_id`/tool result message、流式 tool-call delta、幂等键与调用账本、checkpoint/resume、DeepSeek strict beta、per-provider 开关矩阵、replan 工具化（V1.1）。

---

## 11. 落地记录（2026-07-24 同日实施 + 真栈验证）

**实现**：四件套按 §4 落地——`providers.py`（`normalize_tool_calls` + `BaseProvider.complete_tools` 基类 fail-open + OpenAI/Anthropic 两实现，`_post_chat` 抽共用）、`server.py`（`_tools_spec` 解析 + 透传/Struct 回填 + tools 跳缓存 + obs content_head 以工具名单补记）、`clients.py`（`_stamp_llm_meta` 抽共用 + `llm_complete_tools` + **`_destruct_nums` 还原 Struct 整数**——protobuf Struct 数字恒 double，不还原则 slots `"24"→"24.0"` 假漂移）、`planning.py`（`_submit_plan_tools`/`_TOOLCALL_SECTION`/`_llm_plan_tools` + `_parse_and_validate` 拆出 dict 直入的 `_parse_and_validate_data` 校验单源 + build() 轮内降级）。`Plan.plan_mode` + engine span 属性；`eval_mode_routing --live` 接 toolcall 通道（llm_tool_fn 直连版 + plan_modes 聚合 + per-case `pm:` tag）——**开关在 eval 进程 env**（builder 是本地代码），A/B 免重建容器。

**离线验证**：新增单测 30（`llm-gateway/tests/test_toolcall.py` 16 + `orchestrator/cloud/tests/test_planning_toolcall.py` 14，badcase 修复迭代含 clarify 消费契约）；全量 pytest 首跑 1770、收尾终跑 **1771 passed / 7 skipped**（基线 1741+30）零回归。

**协议探针**（§7-1，`test/e2e_planner_toolcall.py`，4 家 × 4 句形态）：**16/16**——named tool_choice 四家全部尊重、arguments 全合法、必填字段全齐、关思考下稳定；qwen `finish_reason` 恒 `"stop"` 怪癖已钉（§2 ★ 注）。

**功能 A/B**（@minimax=MiniMax-M3 组内对照，mode_routing --live 177 例 × 两轮）：

| 轮 | off（对照） | on（实验） | on plan_modes（120 live 例） |
|---|---|---|---|
| R1 | 173/177（97.7%） | 171/177（96.6%） | toolcall 112 / salvage 5 / degraded 3 |
| R2 | 174/177（98.3%） | **175/177（98.9%）** | toolcall 107 / salvage 7 / fallback 3 / degraded 3 |
| R3（B4-1/B1-4 修后 prompt） | — | 173/177（97.7%） | toolcall 103 / salvage 10 / fallback 5 / degraded 2 |
| 聚合 | 347/354（98.0%） | 519/531（97.7%，中心值 173 vs off 173.5） | arguments 畸形 **0**（360 on 例全程） |

- on/off 差异在方差带内且两轮方向相反；R1 on 独有 FAIL（固态电池→research、麒麟电池→chitchat 等）在 **R2 off 里同样出现**，重放 3× 复测不复现——边界句自身抖动，非 toolcall 系统性漂移。
- **降级链全兜住**：R2 全部 salvage/fallback/degraded 13 例功能层 PASS，零静默丢失。degraded 全是 guardrail 怪句（「搜一下你叫什么名字」类）——LLM 对其出空/非法计划在 off 侧同样存在（无观测标签而已），fallback+route_hints 链同源接住。
- 注：R3 后 prompt 又加一句占位值负向约束（B3-4 修复）；该句为纯限制性语义（禁编造），mode_routing 判别面理论无涉，未再跑 R4——最终 prompt 形态以 journeys regression 15/15 作全链路功能验证（见下）。
- **journeys 抓到三个 toolcall 独有 badcase（B4-1 / B1-4 / B3-4），同根因族「schema/指令改变输出分布」，均已修，最终 regression 15/15 全绿**：
  - **B4-1 误澄清（多填族）**：「把音量调到15」→「我刚才让你把音量调到多少」被反问澄清（期望直答 15；off 下从不误触，重放 4 轮 2-3 FAIL=系统性）。根因=schema 把 clarify 变成「摆在眼前的可选字段」，结构可见性把误澄清率从 0 抬到 ~50-66%。修法两轮：①clarify description 带满「绝大多数请求明确」约束——**压不回去**（仍 2/4 FAIL）；②**clarify 移出 schema、退回 prompt-only 触发面**（=R4.4 验收原始形态；软 schema 下模型按 prompt 在 arguments 带 clarify 属额外字段合法，`_parse_and_validate_data` 照常消费，契约测试锁定）→ B4-1 重放 **4/4 绿**。
  - **B1-4 丢继承槽（少填族，B4-1 的反向）**：「明天杭州天气」→「那后天呢」只带 date 丢 city，执行错落定位城市深圳（重放 3/3 稳定 FAIL）。根因=schema 里 slots 是无说明空 object + 工具输出形态自带「函数入参只传需要的」先验 → 省略式追问只写变化槽；JSON 路径靠 prompt few-shot 引导写全从不丢。修法两轮：①slots description 写明「完整参数表+继承写全」——只抬到 1/3；②**`_TOOLCALL_SECTION` 加形态 few-shot**（抽象 A市 示例、不嵌 agent/intent 字面量守 Full Migration 铁律，纠正「入参增量」先验）→ B1-4 重放 **4/4 绿**。
  - **B3-4 编造占位值（B1-4 修复引入的回归，「过度写全」变体）**：「今天天气怎么样，适合出行吗」（无城市句）——few-shot 教「写全」后模型把 city 填成字面量**「当前位置」**→ qweather city_lookup 对假城市名 HTTP 400 → FAILED「抱歉，处理失败」（trace `8143c835ae55442a` 定位；JSON 路径 city 留空由 Agent 定位反查从不炸）。全量跑连挂两次、单独重放 3/3 绿（采样不总触发）——**靠 obs trace 的 plan 字段抓到**（老教训再验证）。修=few-shot 补负向约束「写全指上下文里实际有的值；没有的槽直接省略、绝不编占位值」（对应 base 既有偏好留空原则）→ B3-4 3/3 + B1-4 2/2（继承写全未削弱）。
  - **通用教训（进 V1.1/V2 设计约束）**：tool schema 与输出指令都不是中立的编码格式——会三向改变模型输出分布：可选字段诱发**多填**（B4-1）、无说明 object 诱发**少填**（B1-4）、「写全」指令诱发**编造**（B3-4）；description 约束对 MiniMax-M3 基本无效，**删结构（B4-1）或形态 few-shot 带正负例（B1-4/B3-4）才有效**。凡改 schema/输出指令必须过 journeys 行为对照，不能只看协议成功率与 mode_routing——三个 badcase 全部只有旅程级多轮/执行链才暴露。
  - **修后最终验证：journeys regression 15/15 全绿**（@minimax 锁定、PLANNER_TOOLCALL=on 容器 env、`--no-report` 不动 canonical 基线）；此前三次全量各挂一例（B4-1→B1-4→B3-4）的迭代过程如上。

**DoD 判定**：
- 协议层 ✅（口径修订）：**arguments 畸形归零**（探针 16 + eval 240 on 例全程 0）+ 全链观测可归因（plan_mode span/eval 聚合 + per-case）。原字面「0 次 `_extract_json` 抢救介入」修订——salvage ~5% 是 MiniMax-M3 无视 named tool_choice 直接文本作答的**模型行为**（非我们可控、非事故），属设计内降级且全部被接住；「消灭脆弱解析」的实质=畸形不再产生失败，已达成。
- 功能层 ✅：mode_routing 三轮 on（171/175/173）vs 两轮 off（173/174）持平（方差带）、无静默丢失；**journeys regression 15/15 全绿**（三个 toolcall 独有 badcase 修复后）。时延/token：toolcall 请求 schema 固有增量在 max_tokens 800 预算内未触截断（360 on 例零 length 截断），P95 未见劣化信号（eval 墙钟两臂同量级）。
- **灰度决策：`PLANNER_TOOLCALL` 默认保持 off**。协议收益已证实、功能持平，翻默认 on 属产品节奏决策，材料齐、留泓舟拍板（与 M0b canary「达标才翻」同款流程；差异：M0b 是 +2 反超、本卡是持平+协议债退役收益）。
