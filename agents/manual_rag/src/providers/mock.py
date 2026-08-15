"""Mock 车书知识库。关键词匹配。

⚠ **这里的每一条都是演示语料，不绑定任何车型**（`source_type="mock"`）。
2026-08-15 QA 轮实测：下面那条胎压内容被逐字转述给用户、并被称作「本车型推荐」
——报告因此把它定性成「LLM 用常识覆盖手册真实性」，实际是系统忠实转述了演示数据。
消费面（`agent.py`）据 `source_type` 决定措辞，**这里只负责如实标注自己是什么**。
"""
from __future__ import annotations
from .base import KnowledgeRetriever, Chunk

_KB = {
    "胎压": Chunk(content="常见乘用车冷胎胎压参考区间 2.2–2.5 bar；**具体数值以车辆铭牌为准**。仪表盘可实时查看各胎压。",
                  source="演示语料·轮胎", score=0.95, source_type="mock"),
    "保养": Chunk(content="首次保养建议在行驶 5000km 或 3 个月（以先到为准），之后每 1 万公里保养一次。",
                  source="演示语料·保养", score=0.9, source_type="mock"),
    "充电": Chunk(content="支持交流慢充与直流快充。快充从 30% 到 80% 约需 30 分钟，建议日常充至 80%。",
                  source="演示语料·充电", score=0.92, source_type="mock"),
    "泊车": Chunk(content="自动泊车：低速(<15km/h)经过车位时，中控提示可泊车，点击启动后松开方向盘即可。",
                  source="演示语料·智能驾驶", score=0.88, source_type="mock"),
    "carplay": Chunk(content="连接 CarPlay：用数据线连接手机至中控 USB，或在『设置-互联』开启无线 CarPlay。",
                     source="演示语料·车机互联", score=0.85, source_type="mock"),
}


class MockKnowledgeRetriever(KnowledgeRetriever):
    async def retrieve(self, query: str, vehicle_model: str = "",
                       top_k: int = 4) -> list[Chunk]:
        q = query.lower()
        hits = [v for k, v in _KB.items() if k in q]
        # 零命中返回**空列表**，不再返回「（未检索到高相关条目…）」那条哨兵。
        # 哨兵的问题是它长得像一段参考资料：调用方会把它拼进 prompt 的【参考资料】，
        # 于是模型在「什么资料都没有」的情况下作答，而不是被短路掉。
        return hits[:top_k]
