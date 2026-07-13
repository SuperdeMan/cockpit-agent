# 2026-07-13 — LLM 消耗排查 · 去 MiMo 硬绑定 · 真机 badcase 五修复（落地记录）

- 状态：已落地（当日完成，全量 1406 passed / 7 skipped；各项均真栈验证）
- 交付对象：已由本记录对应实现交付；后续接手者按此了解机制与契约
- 关联：`docs/conventions.md` §6/§9.2、`AGENTS.md` §4 两行主题、memory `llm-token-consumption-audit`

## 1. LLM 消耗排查（结论 + 方法论）

**问题**：泓舟怀疑默认 mimo 在闲置时被持续消耗（疑 key 泄露）。

**结论**：栈内**无任何闲置持续调 LLM 的入口**。obs.db `llm_calls` 三天数据夜间时段 0 次；消耗全部对应使用时段，构成 = eval 跑批直打网关（caller 空）+ memory 抽取隐形放大（caller 空）+ HMI 批处理 TTS（MiMo 字符）。密钥已轮换 + 全栈重建（容器内 key 哈希比对确认）。

**方法论（复用）**：查 `docker exec …collector… /data/obs.db` 的 `llm_calls` 按 caller/model/小时分组，`prompt_tail` 抽样定性；音频面看 llm-gateway aiohttp access log。

**衍生两优化**：
- **合成会话跳过抽取**：`AppendTurnRequest` 无 meta → session_id 前缀契约（`eval-`/`e2e-`/`replay-`/`nightly-` 等，env `MEMORY_EXTRACT_SKIP_PREFIXES`，登记 conventions §9.2）；`memtest-` 刻意豁免供 `e2e_memory` 验证抽取链路。短期轮次存取不受影响。
- **caller 归属补全**：直连 llm-gateway `Complete` 一律带 `meta["caller_service"]`（memory-extract / eval-*；**别用 `caller`——那是限流桶键**，惯例同 planner/SDK `_stamp_obs_meta`）。

## 2. Registry 长期不健康自动剔除

**问题**：nearby 重构（07-05）后 food-ordering 残留注册被 PgStore 持久化，端点永死 → 每 5s 刷一条 unhealthy WARNING 刷了 8 天。

**机制**（`registry/store.py`）：
- 告警只打健康→不健康**转变沿**（PG status 写同理），不随探测周期重复；
- 连续失败达 `REGISTRY_EVICT_FAIL_COUNT`（默认 120 ≈ 10min）**整体注销**：内存 + PG 双删（`_evict` 钩子，PgStore 级联后台任务）。活 Agent 每 10s 重注册重建记录（fail_count 归零）自动豁免——只有真消失的注册（改名/下线）会被剔除，这一类问题根治。

## 3. 批处理 ASR/TTS 去 MiMo 硬绑定

**问题**：批处理工厂只认 `LLM_PROVIDER∈{xiaomimimo,mimo}`，chat 换家即静默降级 mock（HMI 回退路径/唤醒提示音哑掉）。

**机制**（`llm-gateway/providers.py`）：`ASR_PROVIDER`/`TTS_PROVIDER` env（默认 `auto`：MiMo 可用→MiMo 现状不变；否则**桥接流式引擎**——新增 `StreamBridgeASRProvider`（WAV→裸 PCM 帧→流式引擎→定稿文本，`_wav_pcm_data` 容忍 ffmpeg pipe 占位 size）与 `StreamBridgeTTSProvider`（整段文本→聚 PCM→封 WAV；跨引擎音色自动回落引擎默认，与 HMI settings 同名回落双侧防御）。`MIMO_AUDIO_BASE_URL` 端点可配；`/api/voices` 缺省跟随实际批引擎。gRPC 面（Transcribe/Synthesize）与 HTTP 面共用工厂。

## 4. 真机 badcase 五修复

| trace | 现象 | 根因 | 修复 |
|---|---|---|---|
| 6d29929e | 搜索回答=原文倾倒+拦腰截断 | 合成 422 全败→`fallback_brief` 整段拼 snippet | 兜底重写（跳 SEO 标题/样板行、`clip_sentence` 句边界收口、限长、明示未归纳指向卡片）+ `build_materials` 消毒控制字符/孤立代理对 + **llm-gateway chat 4xx 捕获响应体进异常** |
| f555cde3 | 「未来几天会下雨吗」只回模板+念完整逆地理地址+`：；`双标点 | forecast 纯模板 speech | `_forecast_answer` 确定性意图先答（雨/雪/冷热/风，零 LLM 零延迟）+ `_day_label` 今天/明天/后天 + `_speech_place` 地名收敛市区级（卡片仍全名） |
| —（UI） | 右舞台电量/续航/挡位写死 62%/430km/P | ContextualStage 占位 mock | edge-gateway 订阅 `vehicle.state.changed`（复用 edge 既有 30s 周期全量快照，编排器零改动）合并镜像→HMI **连上即推** + 变更去重广播 `{type:"vehicle_state",state}`；HMI `vehicleStage.mjs`（缺数据诚实 `--`，续航=`range_km` 优先/电量×550 折算） |
| a3fad033 | 预测类兜底不答问题 | **4xx 响应体捕获当轮兑现：MiniMax 422 = `input new_sensitive (1026)` 内容风控**（检索源夹带敏感站正文整包被拒；编码/体量/赔率假设均否定） | `grounded_synthesis` 识别风控拒收（sensitive/data_inspection/content_filter）→ **收窄权威 top-2 重试一次**；llm-gateway 4xx(400/403/413/422)→`INVALID_ARGUMENT`（SDK 只重试 UNAVAILABLE，不再白打第二遍） |
| 361f6e72 | 「今天体感温度怎么样」3ms 开空调（**问天气误触车控**） | 端侧裸「温度」子条件劫持疑问句 | `_is_env_temp_query` 三层让路：查/几度/多少既有排除不动基线；体感/气温/天气/室外语境**无条件**；怎么样/如何疑问式仅**无操作动词**时（「温度如何调高」仍归空调）+ 天气查询分支补体感/气温/疑问式→info.weather；`eval_fast_intent` 57/57 零回归 |

## 5. 验收与坑

- 全量 `python -m pytest`：**1406 passed, 7 skipped**（当日 +48：registry 3 / 音频工厂桥接 20 / 抽取跳过+归属 2 / 兜底消毒 4xx 7 / 天气 8 / 温度让路 5 / Go+HMI 另计）；HMI node 137/137 + build；Go docker 编译过。
- 真栈：密钥轮换 key 哈希比对、eval- 会话 4 轮 AppendTurn 零 LLM 调用、WS 连上即收 `vehicle_state(battery=72,gear=P)`、「深圳未来几天会下雨吗」意图先答、「今天体感温度怎么样」→「深圳南山区…体感35℃」、世界杯预测重放出带引用真合成。
- **坑**：① Python 测试导入禁裸 `sys.path` 插 `src`——`providers`/`handlers` 这类通用包名会劫持 `sys.modules`，污染 llm-gateway 同名模块的收集（走 `agents.info.src.…` 全包路径）；② provider 假响应对象须带 `status_code`（chat 4xx 检查新契约）。

## 6. 未做/后续

- MiniMax 内容风控的**来源级**规避（如敏感域名进 `source_quality` 低档/黑名单）——现靠权威收窄重试兜住，若高频再议。
- HMI 右舞台续航依赖折算常量（满电 550km）；VAL 提供真实 `range_km` 信号后自动优先（HMI 已就绪）。
- Dashboard「LLM 视图」按 caller_service 分组展示（数据已齐，视图未加）。
