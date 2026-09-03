"""车书知识库 Provider 接口。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Chunk:
    content: str = ""
    source: str = ""       # 来源（章节/页码）
    score: float = 0.0     # 相关性分
    # 来源**类型**，与 `source`（来源的名字）是两回事。消费面据它决定能不能把这段
    # 内容表述成「本车型手册」——QA 轮 I-036 的根因正是这一格缺失：mock 演示语料
    # 带着「第3章·轮胎保养」一路走到用户面前，被称作车型手册的推荐值。
    #   manual = 真实车型手册；web = 联网检索；mock = 演示语料（**不绑定任何车型**）
    source_type: str = "manual"
    # 真实手册引用的结构化定位。mock/web 可留空，兼容原接口。
    document_id: str = ""
    vehicle_model: str = ""
    page_start: int = 0
    page_end: int = 0
    section_path: tuple[str, ...] = ()


class KnowledgeRetriever(ABC):
    @abstractmethod
    async def retrieve(self, query: str, vehicle_model: str = "",
                       top_k: int = 4) -> list[Chunk]:
        """检索相关知识片段。"""
        ...
