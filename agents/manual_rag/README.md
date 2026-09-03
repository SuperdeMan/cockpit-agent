# manual-rag Agent (ecosystem / first_party)

车书助手：从**指定车型的真实用户手册**检索可核验片段，再由 LLM 仅依据片段生成简短
回答。2026-09-03 起已有 Xiaomi SU7 2024 用户手册的本地只读索引实现；mock 只保留给
CI 和无私有手册资产的离线开发。

| intent | 说明 |
|---|---|
| `manual.query` | 胎压、保养、充电、功能操作、应急处置等车型手册问答 |

## 运行链路

```text
PDF -> scripts/build_manual_index.py -> models/manual_rag/*.json.gz
    -> ManualIndexRetriever -> 中文 n-gram BM25 + 章节/短语/覆盖率重排
    -> Chunk(source_type=manual, section_path, PDF page, vehicle_model)
    -> grounded prompt -> numeric claim guard -> speech + manual card
```

确定性护栏：

- 索引绑定源 PDF SHA-256、车型、手册版本和内容 SHA-256，并须与 tracked
  `resources/manual_catalog.yaml` 的人工批准指纹一致；任一不一致即启动失败；
- 显式 real 配置缺文件/损坏/错车型时 fail-fast，绝不回 mock；
- 未出现的显著 Latin 产品名或多词协议名（如 `CarPlay`、`Android Auto`）零命中；
- 低相关查询零命中且不调 LLM；
- 真实手册答案里的带单位/小数数值必须能在本轮引用片段核对，否则整段弃权；
- 安全告警继续由 `runtime/safety_signal.py` 的确定性分级建议前置；
- 卡片带章节、PDF 页码、车型、源/内容 hash 和 `_prov.mode=real`。

## 私有索引资产

源 PDF 与抽取正文不进入 Git。生成索引放 `models/manual_rag/`，该目录只跟踪说明和
`.gitkeep`，`*.json.gz` 全部 ignored。在线镜像不安装 PDF 解析器；`pypdf` 只属于离线建库。

当前本机使用的输入基线：

| 字段 | 值 |
|---|---|
| document | `SU7用户手册` |
| vehicle_model | `xiaomi-su7-2024` |
| revision | `2024-04-15` |
| PDF pages | 278 |
| source SHA-256 | `ef16d204c2ad711b2aa6c2a5f2a6607cfc2d47ed3f5d5a4e1db4085f75e4705d` |
| output | `models/manual_rag/xiaomi-su7-2024.v1.json.gz` |

## 构建索引

请在隔离虚拟环境或临时 target 中安装构建期依赖，不要污染系统 Python：

```powershell
python -m venv .artifacts/venvs/manual-rag
.artifacts/venvs/manual-rag/Scripts/python -m pip install -r agents/manual_rag/requirements-ingest.txt
.artifacts/venvs/manual-rag/Scripts/python -X utf8 scripts/build_manual_index.py `
  --pdf 'D:\path\to\2024-小米SU7-Pro-Max-用户手册.pdf' `
  --output 'models\manual_rag\xiaomi-su7-2024.v1.json.gz' `
  --expected-sha256 'ef16d204c2ad711b2aa6c2a5f2a6607cfc2d47ed3f5d5a4e1db4085f75e4705d'
```

默认拒绝覆盖已有索引；确认输入后才使用 `--force`。相同 PDF 与参数的输出应逐字节相同。

## Provider 配置

```text
KNOWLEDGE_VENDOR=local
MANUAL_INDEX_PATH=/app/models/manual_rag/xiaomi-su7-2024.v1.json.gz
KNOWLEDGE_VEHICLE_MODEL=xiaomi-su7-2024
```

- `KNOWLEDGE_VENDOR=mock`：仅 CI/离线演示；
- `local|manual|file`：真实只读索引，缺失或校验失败即启动失败；
- `pgvector`：仍未实现，显式选择会 fail-fast；当多车型规模或真实 badcase 证明词法召回
  达到上限时，再以现有 retrieval corpus 为 A/B 尺子迁移。

根 `.env` 是唯一运行时配置源；不要在本目录复制 `.env`。本机 Docker build 会把工作区中
已核验的 ignored 索引复制到 `/app/models/manual_rag/`；但当前 cloud release 的
`source.tar` 只来自目标 commit，**不会携带 ignored 资产**。cloud profile 已接入现有
shared-model bootstrap：索引先按固定 SHA 安装到 `/opt/car-agent/shared/models/manual_rag/`，
再以只读 volume 挂载；bootstrap、基础设施批准锚与 deploy 仍须逐次授权并验证。

## 验证

```powershell
python -X utf8 -m pytest -q agents/manual_rag `
  scripts/tests/test_build_manual_index.py scripts/tests/test_eval_manual_rag.py
python -X utf8 scripts/eval_manual_rag.py `
  --index models/manual_rag/xiaomi-su7-2024.v1.json.gz `
  --cases test/eval_corpus/manual_rag_retrieval.yaml `
  --output .artifacts/manual-rag/xiaomi-su7-2024-retrieval.json
```

真实评测必须同时核对 top 页和关键正文；“页号碰对”不算通过。实现与证据边界见
`docs/design/2026-09-03-xiaomi-su7-manual-rag-implementation-plan.md`。
