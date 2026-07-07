# 多 LLM 源接入 + TTS/ASR 扩展 + 赛事国旗（2026-07-07）

> 泓舟需求：扩展座舱 AI 接入面——多 LLM 源可运行时切换、TTS 新增 MiniMax 并把 MiMo 升流式、ASR 核对、
> 赛事卡补国旗。仅动 `llm-gateway/`、`hmi/`、`agents/info/`、env/compose；不改 proto/编排核心/车控链路。

## 1. 多 LLM 源 + 全局切换

**问题**：`llm-gateway` 原是「启动按 `LLM_PROVIDER` env 选单一 provider」，换厂商须改 env 重启。

**做法**：引入进程内 provider 注册表 + 全局 active 切换（座舱「单一大脑」模型，所有服务共用），
运行时经 HTTP 端点切换。

- `llm-gateway/providers.py::OpenAICompatibleProvider` 参数化两个 per-provider 差异（一套代码覆盖四家）：
  - `token_param`：`max_completion_tokens`（MiMo/MiniMax）| `max_tokens`（DeepSeek/Qwen）
  - `thinking_style`：`mimo`（`thinking:{type:disabled}`，含 MiniMax）| `qwen`（`enable_thinking:false/true`）| `none`（DeepSeek）
- **新增 `llm-gateway/llm_runtime.py`**（gRPC 与 HTTP 控制端点共用单例）：
  - `_PROVIDER_SPECS` 四家静态配置（endpoint/auth/token/thinking/key-env/模型档位，endpoint·model 均 env 可覆盖）
  - `resolve_models(requested)`：`""`→primary、`"@fast"`→fast、`"@primary/@deep"`→primary；**不认识的具体
    模型名→回落 primary**（防「切到 DeepSeek 却收到 chitchat 发来的 mimo 模型名」）
  - `set_active(provider, model)` / `status()`；默认 active = `LLM_PROVIDER`（`xiaomimimo→mimo`），无任何 key → mock
  - **embedding 解耦**：`embed_provider()` 独立按 `LLM_EMBED_*`（DashScope）建，与 active chat provider 无关
    ——**修掉「把 active 切到无 embedding 的 DeepSeek/MiniMax 会拖垮记忆语义召回」的潜在 bug**
- `server.py` 全部经 `llm_runtime`（active provider + 档位解析 + 独立 embed provider）；缓存 key 并入 active id 防串味
- `http_server.py`：`GET /api/llm/providers`（列表+可用性+active）、`POST /api/llm/provider`（切换）
- `agents/chitchat/src/agent.py`（唯一显式传 model 的调用方）改传**档位哨兵**（`""`/`@fast`），由网关按 active 解析
- HMI：设置页「助手设置」新增「AI 大脑（LLM 厂商）」两级选择（厂商→具体模型，仿 TTS 引擎→音色），
  切换即 `POST /api/llm/provider` 全局生效 + localStorage 持久；`App.tsx` 启动时把存的偏好重放回网关
  （网关重启回落 env 默认后恢复用户选择）。`settings.llmProvider` 空=跟随网关默认、非空=用户显式选定。

| 厂商 | endpoint | auth | token | thinking | key（env） | primary / fast |
|---|---|---|---|---|---|---|
| mimo | token-plan-cn.xiaomimimo…/chat/completions | api-key | max_completion_tokens | mimo | `LLM_API_KEY` | mimo-v2.5-pro / mimo-v2.5 |
| minimax | api.minimaxi.com/v1/chat/completions | bearer | max_completion_tokens | mimo | `MINIMAX_API_KEY` | MiniMax-M3 |
| deepseek | api.deepseek.com/v1/chat/completions | bearer | max_tokens | none | `DEEPSEEK_API_KEY` | deepseek-v4-pro / deepseek-v4-flash |
| qwen | dashscope…/compatible-mode/v1/chat/completions | bearer | max_tokens | qwen | 复用 `LLM_EMBED_API_KEY`/`DASHSCOPE_ASR_KEY` | qwen3.7-max / qwen3.7-plus |

**已知取舍**：全局切换是网关内存态（重启回落 env 默认，HMI 重放兜底）；多实例需 Redis（本 PoC 不做）。

**真栈 thinking 校准（2026-07-07 四家全配 key 后实测）**：
- MiMo/MiniMax-M3：`thinking:{type:disabled}` 关思考（thinking_style=mimo），content 干净。
- Qwen3.7：`enable_thinking:false` 关思考（thinking_style=qwen），content 干净。
- **DeepSeek v4-pro/flash 是推理模型**（`reasoning_content` 占 completion 预算）——原设 thinking_style=none
  时结构化任务的 content 会被 reasoning 饿空（token 紧时尤甚）。真栈探测发现 DeepSeek **同样认
  `thinking:{type:disabled}`**（`reasoning_effort:none` 报 400、`enable_thinking` 被忽略）→ 改
  **thinking_style=mimo**，结构化任务关思考拿干净 content（实测「用一句话介绍杭州」16 token 干净输出）。

## 2. TTS 扩展

MiMo/MiniMax 的 TTS API 都是「整段文本一次入」（不像 cosyvoice/qwen 支持增量喂），故新增共享
`providers._sentence_segments()` 句级切分器，把「文本增量流」聚成「整句流」逐段流式合成、边说边播。

- **MiniMax TTS（新增，key=`MINIMAX_API_KEY`，与 MiniMax LLM 同 key）**：`MiniMaxStreamingTTSProvider`
  → `POST /v1/t2a_v2` `stream:true`，SSE 逐 chunk 取 `data.audio`（hex）解码为 PCM。
- **MiMo TTS 升流式**：`MiMoStreamingTTSProvider` → `POST /v1/chat/completions` `stream:true` +
  `audio:{format:pcm16}`，SSE 取 `delta.audio.data`（base64 pcm16@24k）。原批处理 `/api/tts` 保留作回退。
- `TTS_STREAM_CATALOG` 加 `minimax`、`mimo` 升为流式条目；`build_tts_stream_provider` + `/api/tts/stream/info`
  统一从 catalog 生成。HMI `TTS_PROVIDER_FALLBACK` 同步（设置页两级选择自动多出两个引擎）。

## 3. ASR 核对（结论：无需改代码）

按 MiMo 最新文档核对 `MiMoASRProvider`：endpoint/`input_audio`/`asr_options.language`/`mimo-v2.5-asr`
均一致。**文档新增的 `stream:true` 只是输出文本流式、音频仍须整段一次性传入，不支持实时增量喂音频**
——不构成真正的实时 ASR。故真实时上屏保持 DashScope（qwen3-asr-realtime / fun-asr）引擎，MiMo ASR 逐字不动。

## 4. 赛事国旗

api-football 本就返回 `home_logo/away_logo`，但 HMI 用色块渲染、从未用；mock 亦无 logo。改为**后端按队名
注入国旗 emoji**（纯字符串、不依赖 api-football，mock/降级也有旗）：

- `agents/info/src/providers/sports_apifootball.py`：`_FLAGS`（中文队名→国旗 emoji）+ `flag_for()`，覆盖
  世界杯 2026 全部球队 + `_ZH_TEAMS` 全部国家（补 库拉索/海地 等新晋队）；英格兰/苏格兰/威尔士用地区旗序列。
- `handlers/sports.py::_fixture_dict` 注入 `home_flag/away_flag`。
- HMI `types.ts::SportsFixture` 加字段；`Cards.tsx::FixtureBoard` 的 `TeamSquare` 与进球明细行渲染国旗。

**赛事追问路由修复（真机漏例）**：「今天世界杯赛程」后追问「葡萄牙**那一场**看看详情」被 nearby.detail
的「看…详情」劫持成周边搜索（返回餐厅）。根因=info.sports pattern `(那|这|上一?|哪)\s*场` 与
nearby.detail guard `那场|…` 都漏「那一场」（中间的「一」）——上批修的是「那场」措辞。防御纵深两边都补
可选「一」（`(那|这|上一?|哪)\s*一?\s*场` / guard `那一?场|这一?场|…`）。回归 `test/test_sports_nearby_routing.py`
（对真实 manifest 跑 RouteHintEngine）+ eval 语料 2 例；真栈两轮验证：追问出葡萄牙 0-1 西班牙进球详情（Mikel Merino 90+1'）。

**平台修复（Windows 国旗字形）**：Windows 版 Chromium 缺国旗字形、会把 🇪🇸 退化成「ES」双字母。
自托管 **Twemoji Country Flags** web 字体（`hmi/public/fonts/TwemojiCountryFlags.woff2`，~76KB 仅含国旗
字形）修复：`styles.css` 加 `@font-face`（`unicode-range` 限定区域指示符/地区旗序列）+ `.au-flag` 工具类，
`Cards.tsx` 国旗元素套 `.au-flag`（队名文字自动回落系统字体）。headless Edge 截图实测：套字体前「ES/PT/US/
BE」，套字体后渲染为真国旗。真机 webview 本就正常，本字体使 Windows 演示环境也正确显示。

## 5. 验证

- 单测：`llm-gateway/tests/test_llm_runtime.py`（+9：per-provider body/注册表/档位解析/切换/qwen 复用 key）、
  `test_tts_stream.py`（+8：句切分/MiMo·MiniMax 工厂路由/SSE 解析）、`agents/info` 国旗（+1）、chitchat 档位化。
  全量 **1141 passed, 7 skipped**（较基线 1112 增 29，零回归）。
- HMI：`node --test` 119/119、`npm run build` 通过。
- Docker 真栈：见 AGENTS.md 记录（重建 llm-gateway+hmi+info，CDP 切厂商/切 TTS 引擎/赛事国旗）。
