# manual-rag Agent (ecosystem / first_party)

车书助手：车型手册问答。**知识类 RAG 范本**——先检索(retrieve)再生成(generate)，答案受参考资料约束以抑制幻觉。

| intent | 说明 |
|---|---|
| `manual.query` | 基于车型知识库检索作答（胎压/保养/充电/功能操作等） |

## RAG 流程
`_retrieve(question)` 取相关片段 → 拼入 prompt → LLM 仅依据资料作答（资料无则明确告知）。

## 待办
- TODO(Phase1): mock 知识库替换为车型手册向量库（pgvector/Milvus）+ embedding + 重排；多车型隔离；引用出处展示。
