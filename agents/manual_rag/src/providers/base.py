"""车书知识库 Provider 接口。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Chunk:
    content: str = ""
    source: str = ""       # 来源（章节/页码）
    score: float = 0.0     # 相关性分


class KnowledgeRetriever(ABC):
    @abstractmethod
    async def retrieve(self, query: str, vehicle_model: str = "",
                       top_k: int = 4) -> list[Chunk]:
        """检索相关知识片段。"""
        ...
