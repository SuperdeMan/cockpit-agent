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
    # M2 Outcome Verifier：执行后对账期望，从 capability.verification 装配（LLM 字段不读，
    # 同 require_confirm 权威链）。空 dict = 不验（缺省，零行为变化）。
    # schema: {"mode","timeout_ms","on_fail","max_attempts","expect":{...}}——**用 dict 不用
    # proto**：Step 会随挂起态序列化进 Redis，且求值器只需读值，dict 免 proto 往返。
    verification: dict = field(default_factory=dict)
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
    # M2 P2 重复副作用防抖：本结果对应的 (intent, slots) 指纹。**只对产生了 actions 的
    # OK 结果写**——T2 放宽后 replan 可能对已完成的副作用步失忆而重复产出（弱模型的
    # 典型失败），指纹随结果走，executor 下一轮撞上即回填不重放。空串=不参与防抖。
    fingerprint: str = ""


@dataclass
class Plan:
    """LLM 产出的 DAG 执行计划。"""
    steps: list[Step]
    raw_text: str = ""
    complexity: str = "simple"    # simple | adaptive：复杂度分诊（simple→T1 直执行, adaptive→T2 循环）
    goal: str = ""                # T2 再规划的锚点（一句话用户目标）；simple 时可空
    # R4.4 受话判定：False=LLM 判「非对助手说的」（仅 hands-free 语音源 + REJECT 开时被 engine 消费）。
    # 缺省 True = fail-open（弱 LLM/旧 prompt/mock 不输出该字段时行为与今天逐字一致）。
    addressed: bool = True
    # R4.4 路由歧义澄清：{"question": str, "options": [{"label","send_text"}]}；与非空 steps 互斥
    # （steps 非空时忽略 clarify，母卡 D6-2>D6-3）。None = 无澄清。
    clarify: dict | None = None
    # 观测（badcase 排查）：Planner LLM 最后一次原始输出。仅供 cloud.planning span
    # 门控采集（engine），不参与任何编排逻辑；解析失败走 fallback 时它保留失败现场。
    raw_llm: str = ""
    # M0b Skill 层：本轮检索/注入的 skill 名单（"<mode>:<name>"），仅供 cloud.planning
    # span 观测（badcase 归因：知识没进上下文还是进了没用对）；不参与编排逻辑。
    skills: list[str] = field(default_factory=list)
    # M1a submit_plan 结构化输出：本轮走的输出通道，仅供 cloud.planning span 观测
    # （A/B 协议层指标聚合）。json=纯文本路径（PLANNER_TOOLCALL=off 恒此值）；
    # toolcall=工具 arguments 直入；toolcall_salvage=模型无视工具、同轮文本抢救；
    # toolcall_fallback=第 2 轮 JSON 路径；toolcall_degraded=两轮全失败走 _fallback。
    plan_mode: str = "json"
    # M2 P2：本轮用户情绪（会话级，不入记忆）。planner 同轮附带输出（R4.4 addressed/
    # clarify 同款 fail-open），随 final 透传给 HMI 选 TTS 情感参数。空=neutral。
    emotion: str = ""


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
    # M4 P4 声纹多用户：本轮说话人。默认 "primary"=今天的行为（未注册声纹/认不出都落它）。
    # **只进记忆域**（recall/remember/AppendTurn/relation），绝不参与权限与确认判定——
    # 声纹不是鉴权因子（RFC §6.1 红线，`test_voiceprint_not_auth.py` 源码级钉死）。
    occupant_id: str = "primary"
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
