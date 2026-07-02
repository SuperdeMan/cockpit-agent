"""Planner 编排引擎数据结构。WS3 核心。"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEED_CONFIRM = "need_confirm"
    NEED_SLOT = "need_slot"


@dataclass
class Step:
    """DAG 计划中的一个步骤。"""
    id: str                       # 计划内唯一，如 "s1"
    agent_id: str
    endpoint: str = ""            # 由 Registry 解析填充
    kind: str = "agent"           # agent | tool | edge_fast：调度语义（UnifiedDispatcher 路由依据）
    deployment: str = "cloud"     # cloud | edge：传输路由依据（edge→经该车 bidi 通道下发）
    intent: str = ""
    slots: dict[str, str] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)   # 依赖的 step id
    slot_refs: dict[str, str] = field(default_factory=dict)
    # 参数依赖：{"slot名": "s1.data.items.0.id"}
    require_confirm: bool = False
    status: StepStatus = StepStatus.PENDING
    latency_budget_ms: int = 5000
    meta: dict[str, str] = field(default_factory=dict)
    required_permissions: list[str] = field(default_factory=list)
    trust_level: str = ""
    context_scopes: list[str] = field(default_factory=list)
    heavy: bool = False           # 重域能力（capability.heavy）：命中即开思考+过程区（progress.is_complex）
    # Agent manifest 声明需要的敏感上下文片段（location | vehicle_state）；
    # 编排下发时按此最小化（未声明则不下发精确位置/电量）。
    # 运行期注入、随 ExecuteRequest.meta 下发给 Agent（如确认续接的 {"confirmed":"true"}）。
    # 不持久化进 SessionState——confirmed 只在确认那一轮由 engine 注入，防止陈旧确认被重放。


@dataclass
class StepResult:
    """单个步骤的执行结果。"""
    step_id: str
    status: StepStatus
    speech: str = ""
    ui_card: dict | None = None
    actions: list[dict] = field(default_factory=list)
    follow_up: str = ""
    data: dict = field(default_factory=dict)   # F3：结构化结果，供后续 step 的 slot_refs 取值
    missing_slots: list[str] = field(default_factory=list)  # F12：NEED_SLOT 时声明缺失的槽位名
    error: str = ""


@dataclass
class Plan:
    """LLM 产出的 DAG 执行计划。"""
    steps: list[Step]
    raw_text: str = ""
    complexity: str = "simple"    # simple | adaptive：复杂度分诊（simple→T1 直执行, adaptive→T2 循环）
    goal: str = ""                # T2 再规划的锚点（一句话用户目标）；simple 时可空


@dataclass
class ReplanDecision:
    """One bounded-loop decision: stop, or execute the next validated batch."""
    done: bool
    steps: list[Step] = field(default_factory=list)

    def to_plan(self, goal: str = "") -> Plan:
        return Plan(steps=self.steps, complexity="adaptive", goal=goal)


@dataclass
class PlanContext:
    """一次编排调用的上下文。"""
    request_id: str = ""
    session_id: str = ""
    user_id: str = ""
    vehicle_id: str = ""
    granted_permissions: list[str] = field(default_factory=list)
    is_confirmation: bool = False
    trace_id: str = ""
    raw_text: str = ""  # 用户原始话术，透传给 Agent（供 fallback 槽位提取）
    # HMI 会话级偏好（model_pref/answer_length/assistant_name/memory_enabled），
    # 来源 HandleRequest.meta，调用 Agent 时并入 ExecuteRequest.meta 透传。
    prefs: dict[str, str] = field(default_factory=dict)


@dataclass
class SessionState:
    """多轮挂起态（待确认/待补槽），Redis 持久。"""
    phase: str                    # "wait_confirm" | "wait_slot"
    pending_plan: dict = field(default_factory=dict)  # 序列化的 Plan
    pending_step_id: str = ""
    missing_slots: list[str] = field(default_factory=list)
    completed_results: dict = field(default_factory=dict)  # step_id -> StepResult dict
    ttl_seconds: int = 300   # 确认/补槽挂起 TTL：行程等慢流程每轮数十秒+用户阅读，90s 太短致确认过期


class CyclicPlan(Exception):
    """计划成环。"""
    pass
