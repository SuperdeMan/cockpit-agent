"""深度调研数据模型（P0）。

把一次调研从「单轮检索 + 一段合成」升级为结构化对象：
ResearchTask → SubQuestion → Evidence，最终产出分节 Report。
所有卡片、（P1）多轮追问、落地动作都作用在这个对象上。

序列化要点（对齐 trip_planner/models.py）：
- `to_dict()` 用 `dataclasses.asdict` 递归转纯 dict——同一份序列化供 memory 持久化（P1）与
  `research_report` 卡（`Report.card_dict()`）。
- `from_dict()` 容错重建，全部 `.get` 带默认。
- 接不到资料的 SubQuestion 标 `status="gap"`、`Report.gaps` 诚实标注，绝不臆造来源/结论。
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict


# STORM 式多视角：调研子问题从这些视角展开，提升覆盖与结构。
PERSPECTIVES = ("背景", "对比", "风险", "最新进展", "适配用户")


@dataclass
class Evidence:
    """一条接地证据（来自一次检索的一个来源）。"""
    idx: int = 0                    # 全局来源编号（合成阶段统一分配）
    title: str = ""
    url: str = ""
    source: str = ""                # 来源域名
    published: str = ""             # 发布时间 ISO（可空）
    excerpt: str = ""               # 正文/摘要节选


@dataclass
class SubQuestion:
    """一个带视角的调研子问题及其证据。"""
    sq_id: str = ""
    text: str = ""
    perspective: str = ""           # 见 PERSPECTIVES
    status: str = "pending"         # pending|searching|answered|gap
    evidence: list = field(default_factory=list)   # list[Evidence]
    finding: str = ""               # 该子问题接地结论（合成后回填，可空）
    confidence: str = "medium"      # high|medium|low

    def grounded(self) -> bool:
        return bool(self.evidence)


@dataclass
class Section:
    """报告的一节（对应一个子问题/视角）。"""
    heading: str = ""
    body: str = ""
    citations: list = field(default_factory=list)  # [来源 idx]
    confidence: str = "medium"


@dataclass
class Report:
    """分节调研报告。summary 行车 TTS 用、sections 泊车/手机可读。"""
    summary: str = ""               # 一段式结论（≤2-3 句，TTS）
    sections: list = field(default_factory=list)   # list[Section]
    sources: list = field(default_factory=list)    # [{idx,title,url,source,published}]
    overall_confidence: str = "medium"
    gaps: list = field(default_factory=list)        # 诚实标注未覆盖
    freshness: str = ""

    def card_dict(self, question: str = "") -> dict:
        """`research_report` 卡 = report + type + question（ui_card 自由 Struct，免改 proto）。"""
        return {"type": "research_report", "question": question, **asdict(self)}


@dataclass
class ResearchTask:
    """一次深度调研。"""
    task_id: str = ""
    session_id: str = ""
    user_id: str = ""
    question: str = ""
    constraints: dict = field(default_factory=dict)  # {location,vehicle_state,profile_prefs,time_now}
    status: str = "planning"        # planning|investigating|synthesizing|done|failed
    plan: list = field(default_factory=list)         # list[SubQuestion]
    report: dict | None = None      # Report.to_dict（P1 持久化用）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ResearchTask":
        d = d or {}
        plan = [_subq_from_dict(x) for x in (d.get("plan") or []) if isinstance(x, dict)]
        return cls(
            task_id=d.get("task_id", "") or "",
            session_id=d.get("session_id", "") or "",
            user_id=d.get("user_id", "") or "",
            question=d.get("question", "") or "",
            constraints=dict(d.get("constraints") or {}),
            status=d.get("status", "planning") or "planning",
            plan=plan,
            report=d.get("report") if isinstance(d.get("report"), dict) else None,
        )


def _evidence_from_dict(d: dict) -> Evidence:
    return Evidence(
        idx=int(d.get("idx", 0) or 0),
        title=d.get("title", "") or "",
        url=d.get("url", "") or "",
        source=d.get("source", "") or "",
        published=d.get("published", "") or "",
        excerpt=d.get("excerpt", "") or "",
    )


def _subq_from_dict(d: dict) -> SubQuestion:
    return SubQuestion(
        sq_id=d.get("sq_id", "") or "",
        text=d.get("text", "") or "",
        perspective=d.get("perspective", "") or "",
        status=d.get("status", "pending") or "pending",
        evidence=[_evidence_from_dict(x) for x in (d.get("evidence") or [])
                  if isinstance(x, dict)],
        finding=d.get("finding", "") or "",
        confidence=d.get("confidence", "medium") or "medium",
    )
