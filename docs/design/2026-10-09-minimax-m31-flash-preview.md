# MiniMax-M3.1-Flash-Preview 接入（2026-10-09）

> 状态：接入实施中；默认模型不变，切不切以 §4 的 A/B 为准。

## 0. 结论

- 项目现有的 MiniMax key 能调 `MiniMax-M3.1-Flash-Preview`（官方 API 总览：预览版、仅 M Plan 与 MiniMax Code 可用，
  上下文 1,000,000）。它作为 MiniMax 档的第二个模型接入：可按请求指定（评测 pin），也可在「AI 大脑」里切换。
- **它关不掉思考**。网关给结构化任务（规划 JSON、工具调用、聚合改写）发的是 `thinking: {type: disabled}`，对它直接 400，
  错误码 2013：「requires adaptive thinking; thinking.type=disabled (including reasoning.effort=none) is not allowed」。
  所以接入必须带按型号的思考处理：调用方要关思考时，对这类型号改发 `reasoning_effort: "low"`，输出预算按开思考给足
  （思考与正文共用 completion 预算）。其他型号逐字不变。
- 缺省模型仍是 `MiniMax-M3`。

## 1. 探针（2026-10-09，本机直连 `api.minimaxi.com`，照网关原样构造请求）

| 调用 | MiniMax-M3 | M3.1-Flash-Preview |
|---|---|---|
| 关思考（网关现行写法） | 200，约 2.4 s | **400 / 2013** |
| 开思考（不发思考键） | 200；思考段以 `<think>` 内联在正文头部 | 200，约 2.2 s；正文干净，不内联 |
| `reasoning_effort: "low"` | — | 200 |
| 模型 ID 小写 | — | 同样认（大小写不敏感），按官方写法接 |

工具调用（4 句规划 × 2，`submit_plan` 工具）：

| 设置 | 中位 | 最长 | 规划正确 | 备注 |
|---|---|---|---|---|
| M3 关思考 | 2.52 s | 5.44 s | 8/8 | 现行生产 |
| Flash 原生思考 | 2.98 s | 10.61 s | 7/8 | 条件句「明天要是下雨就别开窗了」一次想了 585 token；另一次把「别开窗」规划成开窗 |
| Flash `effort=low` | 2.47 s | 6.78 s | 7/8 | 一次编了不存在的能力名 `get_weather` |

样本太小，不足以下结论，只说明两件事：`effort=low` 的延迟与 M3 持平；规划质量要在真实规划器上量。

## 2. 改动

- `llm-gateway/llm_runtime.py`：MiniMax 档的 `models` 加 `MiniMax-M3.1-Flash-Preview`，并声明 `thinking_required`；
  构造提供方时传入。
- `llm-gateway/providers.py`：`OpenAICompatibleProvider` 增加 `thinking_required_models`，`_build_body` 对名单里的型号：
  - 要关思考时，不发 `thinking: disabled`，改发 `reasoning_effort: "low"`，预算抬到至少 2048；
  - 要开思考时，与其他型号相同（不发思考键，原生自适应）。
- 不动 `.env`、`.env.example`、compose；缺省主档、快档仍是 `MiniMax-M3`。按请求指定新模型时，失败退回 M3（既有降级链）。

## 3. 验证

- 网关单测：
  - 新型号要关思考时，请求体是最低档 + 抬高的预算；
  - M3 的请求体逐字不变；
  - 运行时把新型号列入 MiniMax 档、可按请求指定、失败退 M3、名单接到了提供方。
- 变异：撤掉按型号分支、撤掉预算抬升、规格不传给提供方，三处都判红。

## 4. A/B 与切换判据（上线后）

- 两臂只差模型：同一份 v2 固定语料与核心旅程清单（只读车道），每次请求分别指定 `minimax:MiniMax-M3` 与
  `minimax:MiniMax-M3.1-Flash-Preview`。
- 比较项：
  - 旅程全过数与业务失败；
  - 零动作、零证据错误、零残留挂起（任一臂违反即否决）；
  - 规划失败、抢救与重试次数；
  - 端到端时延中位与尾部。
- 切换判据：新模型在正确性上不差于 M3（必过集零退化、旅程全过数不少），且时延不差，才建议切缺省。
  缺省怎么切：云端 `.env` 的 `MINIMAX_LLM_MODEL` 属于红线，要用户批准；改代码缺省同样等于换生产模型，也先报数再定。
