# Nightly 真实 LLM 语料

复杂多意图、跨 Agent 组合、多轮指代依赖**真实 LLM** 规划，mock 跑没意义，所以单列在
nightly：默认 skip，不进普通 PR 门禁，定期（或发版前）用真实 key 跑。

## 运行
```bash
make up                       # 起全栈（容器内已配 LLM_API_KEY）
export LLM_API_KEY=...         # 宿主侧同样配置，作为 nightly 开关
python -m pytest test/nightly -m nightly -v
```
未设置宿主 `LLM_API_KEY` 或全栈未起时，用例自动 skip（不连网络、不拖慢普通全量）。

## 覆盖（`corpus_llm.yaml`）
| 用例 | 验 |
|---|---|
| nav_plus_poi_search | 导航 + 途中 POI 的跨 Agent 组合（spec 7.3）|
| restaurant_search_reserve | 餐厅搜索→预订链路（spec 7.3）|
| destination_coreference | 多轮指代：第二轮续接首都机场、不退化为闲聊（spec 7.4）|
| complex_mixed_departure | 超复杂混合 + 出发，本地车控状态确定、导航上云（用户原始例子精简版）|

## 约定
- 断言只绑定**必达节点 / 确定的本地状态 / 少量关键词**，不绑完整话术（容忍 LLM 波动）。
- 语料结构与 `test/fixtures/central_hub_cases.json` 一致，断言逻辑复用
  `test/e2e_central_hub_assertions.py`。
- 失败表示「真实 LLM 行为漂移或回归」，人工研判，不作为 PR 阻塞。
