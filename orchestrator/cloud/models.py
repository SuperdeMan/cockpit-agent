"""Planner 编排引擎数据结构。WS3 核心。"""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field, replace as _dc_replace
from enum import Enum


def step_fingerprint(intent: str, slots: dict | None) -> str:
    """`(intent, 归一化 slots)` 指纹——**全仓唯一的一份复用/防抖键**（评审 2026-09-19 W04）。

    两个消费方问的是同一个问题「这一步和那一步是不是同一件事」：
      · 执行侧 `DagExecutor._fingerprint`：同轮内同一副作用不重发（M2 P2 防抖）；
      · 规划侧 `planning._completed_observation_steps`：再规划不得重复已完成的**同参**读取。
    此前规划侧只按 intent 判，于是「已查深圳天气 ⇒ 广州天气也算重复」；执行侧那份
    早就是 (intent, slots)——同一件事两份键，粗的那份出了错。
    slots 排序后序列化：槽位顺序不该影响同一性判定；序列化失败退到 sorted items 字符串。
    """
    try:
        payload = json.dumps(slots or {}, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        payload = str(sorted((slots or {}).items()))
    raw = f"{intent}|{payload}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


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
    # 该 capability 声明的槽位名（planning._validated_steps 从 manifest 装配）。
    # **进程内字段**：不进 _serialize_plan、不随 ExecuteRequest 下发——目前唯一消费方是
    # executor._anchor_store_from_focus 的门控（只有声明了门店三槽的商户 workflow 才吃
    # 跨轮门店锚定；此前锚定对所有步骤生效，把门店槽注进了 chitchat/nearby，
    # demo-mkemhn 2fd09d52/44943f00 实证）。
    declared_slots: list[str] = field(default_factory=list)
    # C3：capability 声明的**槽位值形状**（`槽位名 -> 形状名`，planning 从 manifest 装配）。
    # 判据本体在 `slot_shape.py`（零领域词），这里只带名字。**进程内字段**——它不进
    # `_serialize_plan`，而是在挂起那一刻由 `_suspend` 把**待补那几个槽**的形状抄进
    # SessionState：形状要回答的是「当时问的那个槽期望什么」，跟着挂起走比跟着计划走准。
    slot_shapes: dict[str, str] = field(default_factory=dict)
    # 整句型能力（QA 余项，2026-08-29）：这一步消费的是整句原话，一次把这句话里的
    # 事全办完 ⇒ **同一份计划里最多一步**。声明在 capability，编排通用消费。
    whole_utterance: bool = False
    # 直接回答能力：只能返回零动作 OK/FAILED；manifest 是唯一权威。
    response_only: bool = False
    # 能力效果 `""|read|write`（评审 W11，2026-09-20）：从 capability.effect 装配（LLM 字段不读）。
    # 消费方：W07 任务帧的 `kind`、终态账本。未声明 = 启发式（结果带 actions / require_confirm）。
    effect: str = ""
    # W12 诉求账本（评审 F07 / §3.1 goal_id）：这一步**负责哪几条诉求**（`Plan.goals` 的 1 起序号）。
    # 模型自报、校验后只留合法序号；空 = 模型没填（fail-open：系统不据此判漏）。**进程内字段**，
    # 漏诉求的判定在规划轮当场做（`engine.goal_gap`），不随挂起持久化。
    covers: list[int] = field(default_factory=list)
    # 批 6 W16-b（评审 §7.1 留项，2026-09-20）：**这一步被规划时的那句用户原话**。服务端持有——engine 在
    # `planner.build` 之后与 `safety_origin_text` 同处盖章、T2 replan 步盖任务起点、澄清预解析步盖用户
    # 选定的那句；LLM / Agent / 客户端都写不到。随 `step_record` 持久化；旧记录没有它时 `_restore` 用
    # 持久化的 `safety_origin_text` 回填（同一条「只允许服务端持有的文本回填」规则）。
    # 消费方只有一个判据 `step_raw_text`：续接轮里**还没跑的下游步**读它当 `Intent.raw_text`，
    # 而不是读另一步的槽答案——修前 `reminder.create`（槽 `title=有堵车`）在续接轮读到「去宝安机场的路」，
    # 答「好的，有堵车。什么时候提醒你？」；读自己的起点原话「只要有堵车就提醒我」才走得到诚实拒绝。
    # 空 = 旧记录 / 没盖章 ⇒ 退回本轮原话（逐字同旧）。
    origin_text: str = ""
    # 评审四轮（2026-09-24）：这一步能力在 Registry 里的**描述**（`_validated_steps` 从 manifest 装配，LLM 写不到）。
    # 唯一判据 `step_grounding` 拿它判「哪一句话点名了这一步」：问句闸按分句归属（R4-03）、纯应答只接受提议点名的那一步（R4-01）。
    # 随 `step_record` 持久化——确认续接的安全原点复核走同一个问句闸，挂起前保留下来的后备箱，确认后还得认得出它依据哪一句。
    capability_description: str = ""
    # **进程内字段**：这一步是本轮续接的那条挂起（`_restore` 按 `pending_step_id` 打标）。它看到的
    # `raw_text` 必须仍是本轮原话——补槽答案 / 「确认」就在里面（reminder 在 pending 下读 raw 解时间）。
    resumed: bool = False
    # M2 Outcome Verifier：执行后对账期望，从 capability.verification 装配（LLM 字段不读，
    # 同 require_confirm 权威链）。空 dict = 不验（缺省，零行为变化）。
    # schema: {"mode","timeout_ms","on_fail","max_attempts","expect":{...}}——**用 dict 不用
    # proto**：Step 会随挂起态序列化进 Redis，且求值器只需读值，dict 免 proto 往返。
    verification: dict = field(default_factory=dict)
    # Agent manifest 声明需要的敏感上下文片段（location | vehicle_state）；
    # 编排下发时按此最小化（未声明则不下发精确位置/电量）。
    # 运行期注入、随 ExecuteRequest.meta 下发给 Agent（如确认续接的 {"confirmed":"true"}）。
    # 不持久化进 SessionState——confirmed 只在确认那一轮由 engine 注入，防止陈旧确认被重放。


def step_record(step: "Step") -> dict:
    """Step → 可落 Redis 的持久化记录（挂起计划 / 澄清选项预解析步共用的**唯一一份**键集）。

    `meta` 故意不持久化：confirmed 标记只在确认那一轮由 `_restore` 注入，防止重放；
    `declared_slots` / `slot_shapes` 是进程内字段（见字段注释）。`Step(**record)` 可原样还原。
    """
    return {
        "id": step.id, "agent_id": step.agent_id, "endpoint": step.endpoint,
        "kind": step.kind, "deployment": step.deployment,
        "intent": step.intent, "slots": dict(step.slots or {}),
        "depends_on": list(step.depends_on or []),
        "slot_refs": dict(step.slot_refs or {}), "require_confirm": step.require_confirm,
        "response_only": bool(getattr(step, "response_only", False)),
        "effect": str(getattr(step, "effect", "") or ""),
        "latency_budget_ms": step.latency_budget_ms,
        "required_permissions": list(step.required_permissions or []),
        "trust_level": step.trust_level,
        "context_scopes": list(step.context_scopes or []),
        # W16-b：起点原话跟着步走过挂起（下游步续接时读它，不读槽答案）；`resumed` 是进程内标记不落盘
        "origin_text": str(getattr(step, "origin_text", "") or ""),
        # 评审四轮：能力描述跟着步走过挂起（续接轮的问句闸复核要用它判这一步依据哪一句）
        "capability_description": str(getattr(step, "capability_description", "") or ""),
        # M2 Verifier：确认后重跑的正是最该对账的车控步——挂起态不带上它，
        # 「用户确认→执行→没生效」这条最危险的路径反而不验（纯 dict，JSON 安全）
        "verification": dict(step.verification or {}),
    }


def step_raw_text(step: "Step", ctx) -> str:
    """Agent 在 `Intent.raw_text` 里看到的那句话——**全仓唯一的一份判据**（批 6 W16-b）。

    · 本轮续接的那一步（`resumed`）看本轮原话：补槽答案 / 「确认」就在里面；
    · 其余步看自己被规划时的那句原话（`origin_text`）；
    · 没有起点原话（旧记录 / 没盖章）退回本轮原话——行为逐字同旧。
    新计划里每一步的起点原话就是本轮原话，所以只有续接轮（补槽 / 确认 / T2 续接）才会换。
    `safety_origin_text` 刻意不参与：它是授权边界，不是「这一步从哪句话来」。
    """
    current = str(getattr(ctx, "raw_text", "") or "")
    if getattr(step, "resumed", False):
        return current
    return str(getattr(step, "origin_text", "") or "") or current


def step_call_context(step: "Step", ctx):
    """下发这一步时传输层看到的 ctx：`raw_text` 换成 `step_raw_text` 的裁决。

    同一句时返回 ctx **本身**（零拷贝、零行为变化）；不同才做一份只活到这次调用的浅拷贝
    （`dataclasses.replace`：PlanContext 的字段全是 init 字段，list 字段共享引用）。
    三条执行路径各接一处（dispatcher 云端调用 / engine `_stream_single_step` / loop T2 单步流式），
    edge 下发本就不带 raw_text。
    """
    if ctx is None:
        return ctx
    text = step_raw_text(step, ctx)
    if text == str(getattr(ctx, "raw_text", "") or ""):
        return ctx
    return _dc_replace(ctx, raw_text=text)


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
    # 结果来源只由 Executor 用当前 Step.intent 盖章。Agent、Planner 与客户端都无权
    # 自报该字段；商户工作流据此校验跨步门店引用来自 nearby.search。
    source_intent: str = ""
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
    # AR05 F09：**这一份计划是规划技术失败之后兜出来的**（模型两轮都没给出合法计划）。
    # 与 `plan_mode` **分列**，不混写既有观测口径：plan_mode 记的是"走的哪条通道"，
    # 这一位记的是"这条计划代表的是失败"。真栈实录 trace 21798d30258aa5bf——非法工具
    # steps → 重试空计划 → toolcall_degraded → chitchat.talk → info.search：技术失败被
    # 伪装成一次成功闲聊，再转成一次搜索，用户既不知道出了什么事，也没有恢复入口。
    # ⚠ **只标这一种**：合法空动作（完整否定句）、addressed=false 拒识、正常澄清、
    # 重试后拿到有效计划、既有有效 salvage 都不是技术失败，一律不标。
    # 两条例外（2026-09-14，方案 §5.2）：网关在用 MockProvider（栈里没有规划模型，兜底
    # 是设计路径）、route_hints 命中（计划是规则裁决）——planning.py 落位处各有说明。
    technical_failure: bool = False
    # R4.4 受话判定：False=LLM 判「非对助手说的」（仅 hands-free 语音源 + REJECT 开时被 engine 消费）。
    # 缺省 True = fail-open（弱 LLM/旧 prompt/mock 不输出该字段时行为与今天逐字一致）。
    addressed: bool = True
    # R4.4 路由歧义澄清：{"question": str, "options": [{"label","send_text"}]}；与非空 steps 互斥
    # （steps 非空时忽略 clarify，母卡 D6-2>D6-3）。None = 无澄清。
    clarify: dict | None = None
    # 取消闸（QA 余项，2026-08-29）：这句取消话没能落到任何可执行的东西上，
    # 值是用户说的那个宾语。engine 据此出**诚实追问**而不是让兜底编一句
    # 「已经取消啦」。空串 = 本轮与它无关。
    cancel_unresolved: str = ""
    # AR05 解释面（2026-09-10 真栈实录）：这一轮请求落在一个**因当前身份缺 scope 而被
    # 过滤掉**的能力上。值是那条能力的 agent_id，`scope_blocked_name` 是给用户看的名字。
    # 为什么必须单独立位而不是让它掉进既有降级：越权能力对 LLM 不可见（这是对的），
    # 于是 LLM 只能凭空解释——受限身份连问 6 次「导航去广州塔」得到 6 种说法，其中一次是
    # 「已为你规划路线」而 `actions` 为空（说了没做），一次把内部错误串原样吐给用户，
    # 6 次没有一次提到真实原因。而真实原因**服务端自己知道**：同一 token 查 `/api/session`
    # 就写着 `navigation → unauthorized / scope_missing`。
    # ⇒ 系统持有的事实不交给 LLM 答。空串 = 本轮与它无关。
    scope_blocked: str = ""
    scope_blocked_name: str = ""
    # 观测（badcase 排查）：Planner LLM 最后一次原始输出。仅供 cloud.planning span
    # 门控采集（engine），不参与任何编排逻辑；解析失败走 fallback 时它保留失败现场。
    raw_llm: str = ""
    # M0b Skill 层：本轮检索/注入的 skill 名单（"<mode>:<name>"），仅供 cloud.planning
    # span 观测（badcase 归因：知识没进上下文还是进了没用对）。
    skills: list[str] = field(default_factory=list)
    # 声明式 plan_repairs 实际改动记录。它只连接已有步骤，不新增 intent/覆盖槽位；单独
    # 留痕是为了分开「模型原生接对」与「soft skill 归一后接对」。
    skill_effects: list[str] = field(default_factory=list)
    # 评审三轮追加批 F（F-1）：能力编号笔误归位记录（"<所写 ref>><归位 ref>"）。只供 cloud.planning span 观测
    # ——「模型原生选对」与「编号笔误被校验归位」必须分开看得见。
    ref_rehomed: list[str] = field(default_factory=list)
    # M5 P1 范例库：本轮检索/注入的范例名单（"<mode>:<eid>@lex|vec:分数"，超预算记
    # !clipped），契约与语义逐项对齐 skills。同样只供 span 归因，不参与编排逻辑。
    exemplars: list[str] = field(default_factory=list)
    # M1a submit_plan 结构化输出：本轮走的输出通道，仅供 cloud.planning span 观测
    # （A/B 协议层指标聚合）。json=纯文本路径（PLANNER_TOOLCALL=off 恒此值）；
    # toolcall=工具 arguments 直入（含已支持协议下的结构化重试）；
    # toolcall_salvage=模型无视工具、同轮文本抢救；toolcall_fallback=工具协议不可用后的
    # 第 2 轮 JSON 路径；toolcall_degraded=两轮全失败走 _fallback。
    plan_mode: str = "json"
    # B6 §2 shadow：可执行性形态判定 `<execute|clarify|reject>|<confidence>`。
    # **只写观测、不进任何决策**——它的全部价值就是不生效，直到分歧样本的对照实验
    # 证明它该接管（canary 要泓舟单独拍板，B6 §5 第 4 条）。
    actionability: str = ""
    # B5 §3：本轮命中的重试策略名（声明序，可重复——同一条可能两轮都命中）。
    # **不换 `plan_mode` 口径**（那会让既有 findings 读数不可比）：归因新增一列，
    # 回答的是「哪条守卫判掉了这一版」，而 plan_mode 回答的是「最后走的哪条通道」。
    retry_policies: list[str] = field(default_factory=list)
    # 落域可观测（仅供 span/评测，不参与编排）：本轮 wire **有没有真的给出**合法
    # complexity。`_wire_to_plan` 里 `wire.get("complexity", "simple")` 是个静默默认，
    # 于是「通道没给这个字段」与「模型判了 simple」被压成同一个值——而 toolcall 通道
    # 有 schema 强制、salvage/fallback 通道没有。分不开这两件事就查不动
    # 「首轮该 adaptive 却判了 simple」那一族（findings §23）。
    complexity_declared: bool = True
    # M2 P2：本轮用户情绪（会话级，不入记忆）。planner 同轮附带输出（R4.4 addressed/
    # clarify 同款 fail-open），随 final 透传给 HMI 选 TTS 情感参数。空=neutral。
    emotion: str = ""
    # 数据飞轮 P0 落域可观测（仅供 cloud.planning span，不参与编排逻辑）：
    # hint_effect=route_hints 对本轮计划的实际作用（""=未命中 / noop=命中但 LLM 已对 /
    # fill=空计划补步 / fill_over_clarify=盖掉澄清补步 / replace / append）——D3「replace
    # 绕过澄清」的裁决数据从这里来。
    hint_effect: str = ""
    # catalog_stats=能力目录渲染统计 {chars_full, chars_final, dropped:[agent_id]}——
    # D1「预算裁剪静默丢域」从此可见；空 dict=本轮未采集。
    catalog_stats: dict = field(default_factory=dict)
    # 服务端持有的**任务起点原话**，只用于跨 replan/escalate 的安全判定。
    # 与 raw_text 分开：挂起续接时 raw_text/PlanContext.raw_text 必须继续承载当前槽答案，
    # 而本字段从最初请求起保持不变并随 pending_plan 持久化。LLM goal/reason 无权写它。
    # 放在末尾以保持既有 Plan 位置参数契约不变。
    safety_origin_text: str = ""
    # W06（2026-09-20）对话行为标签：planner 额外输出的顶层 `acts`（prompt-only、fail-open，
    # 词表 `planning.ACTS`）。缺省 [] = 今天的行为。唯一决策消费方是 engine 的改口合并
    # （`correct` ⇒ 从活动任务继承缺槽）；其余标签只进 span 观测。
    acts: list[str] = field(default_factory=list)
    # W07：这一份计划**修改的是哪个活动任务**（engine 在改口合并时置 `{"task_id", "revision"}`），
    # `extract_focus` 据此把任务帧记成同一个 task_id 的下一版而不是新任务。空 = 新任务。
    task_patch: dict = field(default_factory=dict)
    # W13 F09-b（2026-09-20）：规划的某一轮里模型**自己说过要澄清**（goal 带澄清标记 / 裸对象被
    # 包成动作），却没交出合法澄清卡、最后落了 `_fallback`。与 `technical_failure` 同真时，engine
    # 把终态从「技术失败 + 重试」改成「我听到了 X，但没听清要拿它做什么」——那是歧义不是故障。
    # 真栈 CL1（2026-09-20）：「云岚国际中心」1/3 次走的正是这条路。
    clarify_wanted: bool = False
    # W12 诉求账本：模型按原话截出的**肯定诉求**列表（≤ 6 条、每条 ≤ 40 字）。与 `Step.covers` 一起
    # 回答「哪条诉求没有步骤承接」——这是评审 F07 要的 goal ledger 的最小形式：goal_id = 序号，
    # source_span = 原话截段。缺省 [] = 模型没填（旧 prompt / 弱模型），系统不猜。
    goals: list[str] = field(default_factory=list)


@dataclass
class ReplanDecision:
    """One bounded-loop decision: stop, or execute the next validated batch."""
    done: bool
    steps: list[Step] = field(default_factory=list)
    skill_effects: list[str] = field(default_factory=list)
    # 评审二轮 R5：依赖「被拒诉求的再试」的下游步——前置没满足，不执行、也不删边让它变根节点；
    # 记 intent 名供观测（`t2.iter` span）。
    blocked: list[str] = field(default_factory=list)
    # 评审三轮 R3-06：`{运行时步骤 ID: 模型给的局部 ID}`——planner_local_id 与 runtime_step_id 分开记，只供观测。
    local_ids: dict[str, str] = field(default_factory=dict)

    def to_plan(self, goal: str = "", safety_origin_text: str = "") -> Plan:
        return Plan(steps=self.steps, complexity="adaptive", goal=goal,
                    safety_origin_text=safety_origin_text,
                    skill_effects=list(self.skill_effects))


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
    # Runner-issued capability for synthetic E2E memory extraction. It stays
    # outside prefs so it cannot reach Agents as metadata.
    e2e_memory_capability: str = ""
    granted_permissions: list[str] = field(default_factory=list)
    is_confirmation: bool = False
    # 本轮确认/取消指向哪一条挂起（QA 卡 Q1-B）。空 = 语音兜底/旧客户端，
    # 按「最近一条挂起」寻址；非空对不上 = 诚实拒绝。
    operation_id: str = ""
    # ── 以下两项是**本轮 scratch**（不来自请求、不下发 Agent、不持久化）──
    # 本轮真正续接上的那条挂起（Q1-C）：收口时只清它，其余挂起原样保留。
    pending_operation_id: str = ""
    # 本轮结束/淘汰掉的挂起 id，随 final 回传 HMI 撤掉对应确认条。
    # **由服务端权威给出**——HMI 猜「这一轮是不是把某条挂起消费掉了」必然猜错。
    closed_operation_ids: list[str] = field(default_factory=list)
    # C3-D：本轮**被放弃**的那条挂起的人话名字（连续追问到上限）。
    # 空 = 没放弃过。**必须有话术**——静默丢弃就是 Q1-C 那条「淘汰必须有话术」的同一件事。
    abandoned_pending_label: str = ""
    # C3-D：本轮续接进来的那条挂起「在问什么、问过几次」——
    # `{"step_id":…, "missing":[…], "retry":N}`。`_suspend` 拿它判**这次是不是同一个问题
    # 又问了一遍**：同一步换了个槽再问是**进展**（商户流程「先问门店再问餐品」正是如此），
    # 只有 step 与待补槽集**都没变**才算原地打转，计数才 +1。
    pending_slot_probe: dict = field(default_factory=dict)
    trace_id: str = ""
    # **当前这一轮**的用户原话，透传给 Agent（补槽轮就是“深圳/拿铁/确认”）。
    # 安全判定不得复用它；跨挂起不变的任务起点在 safety_origin_text。
    # 批 6 W16-b：续接轮里**还没跑的下游步**不读它——读自己的 `Step.origin_text`（判据 `step_raw_text`，
    # 传输层经 `step_call_context` 拿一份换了 raw_text 的浅拷贝）；本字段本身在整轮里不变。
    raw_text: str = ""
    # HMI 会话级偏好（model_pref/answer_length/assistant_name/memory_enabled），
    # 来源 HandleRequest.meta，调用 Agent 时并入 ExecuteRequest.meta 透传。
    prefs: dict[str, str] = field(default_factory=dict)
    # M5 P2-D2 端云透传：端侧 fast_intent 的初判（"<intent>|<conf>"，无判定则空）。
    # **只作观测与分歧挖掘，不进 prompt**——Shadow NLU 实测端侧规则臂 domain 准确率
    # 75.9%、LLM 91.2%，把更差的判断塞进更好的模型的上下文是负期望的赌。
    # 刻意**不留 env 开关**：那会变成一个没人测过却随时可能被打开的分支；真要开就改代码，
    # 并且必须附 A/B 数据（性质由 test_edge_nlu_divergence.py 源码级断言守住）。
    edge_nlu: str = ""
    # QA 卡 Q7-OR2（2026-08-16）**同轮**执行事实：端侧在这一轮已经经 VAL 执行掉的动作名
    # （`hvac.off` 这种）。混合意图路径下端侧先执行本地那半、再把剩下的片段上云，
    # 而剩下那半可能是个**没有对象的碎片**——「关闭空调然后打开，按顺序执行」上云的
    # 是「打开，按顺序执行」，对象在同一轮的另一个组里。真栈实测：云侧就此落兜底，
    # 答「我不能帮你执行操作」，而它 4 秒前刚关了空调。
    #
    # **刻意不进 `prefs`**（同 `edge_nlu`/`e2e_memory_capability` 的理由）：prefs 会
    # 随 ExecuteRequest 下发给全部 Agent，而这是编排自己的上下文，Agent 不该看见。
    # 跨轮的同一件事走 memory 会话轮次的 `actions`（Q6 已存），不在这里。
    edge_executed: list[str] = field(default_factory=list)
    # 端侧上一条纯本地 exchange 的一次性边界标记。只供焦点相邻性判定，
    # 不进 prefs、不下发 Agent；由 edge 剥离客户端同名 meta 后自行签发。
    previous_local_exchange: str = ""
    previous_local_actions: list[str] = field(default_factory=list)
    # 评审四轮 R4-04：与上面两份名字**同一事实**的执行目标 `[{command, positions}]`（端侧 VAL 真执行过之后自己盖的，
    # 客户端同名 meta 在端侧入口剥掉）。「关掉」反向的是**那几个位置上的**那个动作——名字里没有位置，
    # 修前 `window.open`（副驾）之后的「关掉」确定性成全车的 `window.close {}`。同 `edge_executed`：不进 prefs、不下发 Agent。
    edge_executed_targets: list[dict] = field(default_factory=list)
    previous_local_targets: list[dict] = field(default_factory=list)
    # 跨轮门店锚定（2026-08-13）：上一轮 `nearby.search` 取回的公开 POI 列表
    # （只留 name/lng/lat 三标量）。**服务端持有、LLM 写不到**——这正是它能充当
    # 可信来源的全部理由：延续的是「服务端记得取回过哪些门店」，不是让模型把坐标再说一遍。
    # 消费方 executor._resolve_slot_refs，契约见 docs/design/2026-08-13-cross-turn-store-anchor.md。
    focus_places: list[dict] = field(default_factory=list)
    # focus_places 的取回时刻（epoch 秒）。update_focus 的粘性接力让门店列表跨任意多轮
    # 存活（防「第一个」抹空焦点，2026-08-13），代价是设计文档「过期即失效」的时效
    # 承诺被架空——executor 按本字段限龄（MERCHANT_STORE_ANCHOR_MAX_AGE_S），
    # 0 = 来源没有时间戳（旧焦点数据），按过期处理。
    focus_places_ts: float = 0.0
    # 服务端权威的任务起点原话；不来自 Agent/LLM，不下发替代 raw_text。
    # 放在末尾以保持既有 PlanContext 位置参数契约不变。
    safety_origin_text: str = ""
    # W10（2026-09-20）**本轮 scratch**：这一轮是在回应哪一次澄清——`{"question": str,
    # "labels": [str]}`。止损判据 `planning.clarify_is_progress` 拿它判「模型又问的是不是
    # 同一个问题」：同题不再问、换题可以再问。空 = 本轮不是澄清续接。
    clarify_probe: dict = field(default_factory=dict)
    # W13 / W14（2026-09-20）**本轮 scratch**：这一轮真正执行的步**全部**是 `response_only`
    # （谈话，按声明不可能改变世界）。终态账本据此标 `answer_only`；执行性声明拦截
    # （`runtime.execution_claim`）只在它为真且零动作时才动手——那是唯一「按声明必假」的形态。
    answer_only: bool = False
    # W12 **本轮 scratch**：规划轮账本判出的、没有步骤承接的诉求原话截段。`run()` 在完成类 final 上
    # 据此补一句「「X」这部分这次没有处理到」并把终态记成 partial；进 T2 / 改派时清空。
    goal_gap: list = field(default_factory=list)
    # 批 5 W19（2026-09-20）：请求级**历史视窗 pin**（`meta.planner_history_exchanges`，1–6 对；
    # 0 = 用部署缺省 `PLANNER_HISTORY_EXCHANGES`）。与 D2 的 `llm_provider` / `llm_model` 同一定位：
    # 评测 / 重放 A/B 的单变量入口——此前视窗只能靠改 env 重新部署来比较。预算（`_CTX_BUDGET`）
    # 仍是硬上限，pin 只放宽视窗不放宽预算。**刻意不进 `prefs`**（Agent 不该看见编排自己的实验旋钮）。
    history_exchanges: int = 0


@dataclass
class SessionState:
    """多轮挂起态（待确认 / 待补槽 / 待选择），Redis 持久。"""
    phase: str                    # "wait_confirm" | "wait_slot" | "wait_clarify"
    # 本条挂起的寻址键（QA 卡 Q1-B）。随 FinalResult 下发、HMI 原样回传，
    # 挂起表（Q1-C）按它定位。**不是授权凭据**——恢复执行仍以本轮已认证
    # user_id 为准，SessionStore 也仍按 owner 分键。
    operation_id: str = ""
    # owner 只用于 SessionStore 的隐私索引/删除边界；恢复执行仍以本轮
    # PlanContext 的已认证 user_id 为准，绝不把持久化字段当成授权。
    owner_user_id: str = ""
    pending_plan: dict = field(default_factory=dict)  # 序列化的 Plan
    pending_step_id: str = ""
    missing_slots: list[str] = field(default_factory=list)
    # C3-A：`missing_slots` 里每个槽的值形状（`槽位名 -> 形状名`，capability 声明）。
    # 续接轮 `_is_topic_change` 据此判「这句话长得像不像这个槽的值」。
    # 空 = 该能力没声明形状，行为与本机制诞生前逐字一致。
    slot_shapes: dict[str, str] = field(default_factory=dict)
    # C3-D：这条挂起**连续追问了几次都没填上**。每次「续接进来 → 又 NEED_SLOT 同一步」
    # 就 +1；到上限即放弃挂起、按全新请求规划（黑洞的止损底线）。
    slot_retry: int = 0
    completed_results: dict = field(default_factory=dict)  # step_id -> StepResult dict
    ttl_seconds: int = 300   # 确认/补槽挂起 TTL：行程等慢流程每轮数十秒+用户阅读，90s 太短致确认过期
    # 本条挂起的绝对截止时刻（epoch 秒，SessionStore 首次落盘时算）。挂起表（Q1-C）
    # 里多条共用一个 Redis key，**TTL 若只挂在 key 上，再存一条就等于给旧条续命**
    # ——「挂起窗口以首次挂起时刻起算、插话不无限续命」那条纪律会被无声架空。
    expires_at: float = 0.0
    # W10：`phase=wait_clarify` 的问题与选项——`{"question": str, "options": [{"label", "send_text",
    # "step"?: step_record}]}`。`step` 是 planner 在澄清那一刻用 catalog 预解析好的单步
    # （`capability_ref` + slots 都经 `_validated_steps`），用户点选后**零 LLM 直接执行**；
    # 没有 `step` 的选项退回「send_text 重新规划」。老部署读到本字段（未知键）会整条跳过。
    clarify: dict = field(default_factory=dict)
    # 评审三轮 R3-01 B（2026-09-23）：挂起那一刻服务端生成的**已校验步骤摘要**——Registry 能力描述 + 槽值，
    # 与确认卡 `confirm_policy.action_summary` 同一句（`contracts.action_summary`）。点名确认的**裁决面只认它**，
    # 不认模型的 goal 与任务原话。空 = 旧记录 / 描述取不到：engine 现取一次，仍为空就判点名不兼容（裸「确认」不受影响）。
    # 老部署读到本字段会整条跳过（同 `clarify`，fail-safe：那一刻读不到挂起，不会误执行）。
    action_summary: str = ""
    # 评审四轮 R4-01（2026-09-24）：**提出这条挂起的那一轮**（`ctx.request_id` = 那一轮的 exchange）。纯应答（「好的 / 嗯」）
    # 只在这条挂起就是最近那个提示时才算对它的授权——对得上最近一轮才算；之后插过话（历史里有更新的 exchange / 上一轮是
    # 端侧本地轮）就是在答别的。显式「确认」不受影响（R2：插话之后回头说「确认」照旧找回它）。空 = 旧记录，证明不了 ⇒ 纯应答不授权。
    prompt_exchange_id: str = ""


class CyclicPlan(Exception):
    """计划成环。"""
    pass
