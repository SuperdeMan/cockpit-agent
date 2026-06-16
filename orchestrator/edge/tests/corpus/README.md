# 测试语料（corpus）

数据驱动的进程内测试语料。每个 `*.yaml` 是一类语料，配套同名 `test_corpus_*.py` 用
`@pytest.mark.parametrize` 驱动，跑在进程内（不连 docker / 真实 LLM），秒级、进每次 PR 门禁。

## 约定（新增语料先读这里）
- 语料只描述「输入 + 期望」，**不写断言逻辑**；断言逻辑在 runner。
- 一行一条语料，尽量覆盖一个维度的**边界**，不堆相似项。
- 数据源是 `../../knowledge/*.yaml`（车控对象 / 实体 / 话术）；扩车控对象时同步扩语料。
- 真实 LLM / 全栈语料**不放这里**——放 `test/nightly/`（真实 key）与 `test/fixtures/`（全栈断言）。

## 文件
| 语料 | runner | 覆盖 |
|---|---|---|
| `safety_gate.yaml` | `../test_corpus_safety.py` | voice_forbidden / drive_restricted / require_confirm / 高速车窗 逐对象门控 |
| `vehicle_objects.yaml` | `../test_corpus_objects.py` | 自然语句→意图识别、结构化→VAL 执行 |
| `multi_intent.yaml` | `../test_corpus_multi_intent.py` | 连接词拆分 / 不拆 / 歌手保留 / 混合分流边界 |
