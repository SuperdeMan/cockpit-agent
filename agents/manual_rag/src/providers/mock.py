"""Mock 车书知识库。关键词匹配。"""
from __future__ import annotations
from .base import KnowledgeRetriever, Chunk

_KB = {
    "胎压": Chunk(content="本车型推荐胎压：前后轮均为 2.4–2.5 bar（冷胎）。仪表盘可实时查看各胎压。",
                  source="第3章·轮胎保养", score=0.95),
    "保养": Chunk(content="首次保养建议在行驶 5000km 或 3 个月（以先到为准），之后每 1 万公里保养一次。",
                  source="第5章·保养计划", score=0.9),
    "充电": Chunk(content="支持交流慢充与直流快充。快充从 30% 到 80% 约需 30 分钟，建议日常充至 80%。",
                  source="第2章·充电指引", score=0.92),
    "泊车": Chunk(content="自动泊车：低速(<15km/h)经过车位时，中控提示可泊车，点击启动后松开方向盘即可。",
                  source="第4章·智能驾驶", score=0.88),
    "carplay": Chunk(content="连接 CarPlay：用数据线连接手机至中控 USB，或在『设置-互联』开启无线 CarPlay。",
                     source="第6章·车机互联", score=0.85),
}


class MockKnowledgeRetriever(KnowledgeRetriever):
    async def retrieve(self, query: str, vehicle_model: str = "",
                       top_k: int = 4) -> list[Chunk]:
        q = query.lower()
        hits = [v for k, v in _KB.items() if k in q]
        if not hits:
            return [Chunk(content="（未检索到高相关条目，建议联系客服）", source="", score=0.0)]
        return hits[:top_k]
