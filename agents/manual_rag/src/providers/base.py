"""车书知识库 Provider 接口。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass

#: 词法命中「有把握」的覆盖率线（2026-09-26 口语召回批：开发集错命中 18 条里 12 条低于
#: 0.7；路由只补不否决，对正确命中的代价只是多一次调用）。低于它、或主题词不在该页章节
#: 路径里，词法结果只算近似，Agent 再按目录路由一次。
CONFIDENT_COVERAGE = 0.7


@dataclass(frozen=True)
class ManualImage:
    """已校验、可直接展示的手册视觉证据；只允许 PNG/JPEG data URI。"""

    asset_id: str
    caption: str
    description: str
    page_start: int
    media_type: str
    data_uri: str
    sha256: str
    width: int
    height: int
    bbox: tuple[float, float, float, float]
    role: str
    # visual_alias = 用户描述命中受控视觉目录；page_evidence = 文本命中页的同页配图。
    match_kind: str


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
    images: tuple[ManualImage, ...] = ()
    # 用户自己的词在这一页的覆盖率（0–1，只有真实索引填写）；Agent 据此判断词法命中
    # 是否可信、要不要再按目录路由补一次。0 = 未知（mock/web）或来自目录路由。
    coverage: float = 0.0
    # 用户的主题词是否出现在这一页的章节路径里；只在正文里撞上主题词的命中不算有把握。
    section_hit: bool = False

    @property
    def confident(self) -> bool:
        return self.coverage >= CONFIDENT_COVERAGE and self.section_hit


class KnowledgeRetriever(ABC):
    @abstractmethod
    async def retrieve(self, query: str, vehicle_model: str = "",
                       top_k: int = 4) -> list[Chunk]:
        """检索相关知识片段。"""
        ...
