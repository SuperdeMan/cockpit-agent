"""PlannerEngine：编排主循环（规划→校验→执行→聚合）。

WS3 §3。串联 planning / executor / aggregator / session。
多轮确认闭环（F1）：NEED_CONFIRM 挂起后，确认轮只重跑挂起步骤（已完成结果种子化），
且 confirmed 标记严格限定在挂起那一步——后续 require_confirm 步骤各自再走确认（架构 §9.1）。
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
import uuid
from typing import AsyncIterator

from .models import Plan, Step, StepResult, StepStatus, PlanContext, SessionState
from .planning import PlanBuilder
from .executor import DagExecutor
from .aggregator import Aggregator, MdDeltaSoftener, strip_markdown_speech
from .session import SessionStore
from .loop import LoopController
from .stream_state import (
    StreamTracker, allow_unary_fallback, emitted_anything, outcome_uncertain,
)
from .pending_cancel import detect_cancel, is_standalone_cancel
from .clients import set_llm_pin
from . import candidate_query
from . import slot_shape
from runtime import session_facts
from runtime.execution_claim import execution_claim
from runtime.clause_split import split_clauses
from runtime.polarity import is_negated_directive
from .context import (ContextManager, build_context, candidate_downlink,
                      candidate_set_for,
                      references_a_candidate, resolve_candidate_scope,
                      safety_alert_active,
                      WEATHER_CONTEXT_INTENTS, normalize_weather_city_slot,
                      _POC_DEFAULT_SCOPES)
from .progress import (is_complex, phase_label, result_summary, step_summary,
                       task_summary, plan_steps_summary)
from observability import events as obs_events
from observability.metrics import metrics
from observability.redact import gate_content
from observability.tracing import set_session_id, set_trace_id

logger = logging.getLogger("planner.engine")

#: C3-D 止损底线：同一条挂起**连续问同一件事**这么多次还填不上，就放弃它、
#: 按全新请求重新规划。真栈 T44-T46 是这条线的由来——一条 `item_query` 挂起
#: 连吞三轮，每一轮都答「没查到"<用户刚说的那句话>"」，用户越说越远。
#: 换题判据（含 C3-A 的形状契约）总有漏网的说法，这条是**判据之外**的兜底：
#: 判据再漏，黑洞也只能吞掉有限轮。
SLOT_RETRY_LIMIT = int(os.getenv("CLOUD_SLOT_RETRY_LIMIT", "2"))


def _executed_action_names(actions) -> list[str]:
    """final 帧里的动作 → 可查询的动作名（Q6）。

    口径与端侧 `_executed_names`、obs、探针 `_action_names` 一致：
    **优先 `payload.command`，回退 `type`**。三处必须同口径——否则「刚才执行了什么」
    答的名字和 badcase 面板看到的对不上，用户与开发者会在两套词汇里各说各话。
    """
    out: list[str] = []
    for a in actions or []:
        if not isinstance(a, dict):
            continue
        payload = a.get("payload")
        name = str((payload if isinstance(payload, dict) else {}).get("command")
                   or a.get("type") or "").strip()
        if name:
            out.append(name)
    return out


def _turn_sources(ui_card) -> list[dict]:
    """final 帧的卡 → 这一轮的**数据源事实**（C4-A，2026-08-28）。

    `_prov`（契约 §9.3）此前**只活在卡上**：渲染完就丢，全仓 orchestrator/memory
    没有任何一处读它。于是「刚才那个行情的数据源是什么」这类问题手里根本没有材料，
    落到 chitchat 就地编一个——真栈 T41 编出「东方财富实时行情、19:23 前后」，
    而真实 provider 是 Tushare、行情日 20260826。**每一个字都是假的，语气却是确定的。**

    > 判据：**「这一轮用了谁、降级没降级」得像动作一样入账**（§4.2 I-033 那行的原判据）。
    > 别在披露层加话术——话术层判据验证不了「说的是不是真的」（Q6 那条）。

    取的是**用户看到的那张卡**（final 帧），不是中途某一步的结果：与
    `_executed_action_names` 同一条口径，被聚合器丢掉的东西不该出现在账本里。
    `card_group` 逐张收（同源场景 `attach()` 会把章打在每个成员卡上）。
    """
    cards = [ui_card] if isinstance(ui_card, dict) else []
    if cards and cards[0].get("type") == "card_group":
        cards = [c for c in (cards[0].get("items") or []) if isinstance(c, dict)]
    out: list[dict] = []
    for card in cards:
        prov = card.get("_prov")
        if not isinstance(prov, dict):
            continue
        record = {"card": str(card.get("type") or "")}
        for key in ("vendor", "mode", "fetched_at", "note",
                    "data_time", "data_time_label"):
            value = prov.get(key)
            if isinstance(value, str) and value.strip():
                record[key] = value.strip()
        if record.get("vendor"):
            # 无 vendor 的章在「来源是什么」上等于没有记录——落库只会让读出口
            # 报出一行空话。同 memory 侧 `_clean_sources` 的口径，两端一致。
            out.append(record)
    return out

# 取消判定已收敛到 `pending_cancel`（QA 卡 Q1-A）——**挂起态的两条分支
# （wait_confirm / wait_slot）曾各判各的**，于是「取消刚才解锁」6 字 > 2+3，
# 在 wait_confirm 下判不出取消、挂起一直活着（I-046）。词表与两条语境规则的
# 全部理由写在那个模块的 docstring 里，这里不复述、也不留第二份词表。

# 肯定话术词表（语音兜底；HMI 确认按钮走 is_confirmation 显式标记）。
# 否定侧不在这里——它是 `pending_cancel` 的职责。
_YES_WORDS = ("确认", "确定", "好的", "好啊", "可以", "订吧", "订了", "是的",
              "嗯", "行", "ok", "付吧", "支付", "下单", "就这家", "就它")

# M2 重复副作用防抖的 fingerprint 和可信来源 source_intent 会由
# ``_resume_result`` 显式保留；其它字段默认不进入挂起种子。
_RESULT_FIELDS = {"step_id", "status", "data", "fingerprint", "source_intent"}
_RESUME_OMIT = object()
_RESUME_URI_RE = re.compile(
    r"(?i)[a-z][a-z0-9+.-]*:(?://)?[^\s，。；,;]+")
_RESUME_URI_PREFIX_RE = re.compile(
    r"(?i)^[a-z][a-z0-9+.-]*:(?://)?")
_RESUME_SECRET_FRAGMENTS = (
    "token", "secret", "credential", "authorization", "cookie",
    "paymentid", "paymentreference", "payurl", "paymenturl",
    "qrcontent", "qrcode", "qrpayload", "phone", "email", "address",
    "recipient",
)

# _POC_DEFAULT_SCOPES 已迁入 context.py（此处 re-export 兼容既有 `from ...engine import _POC_DEFAULT_SCOPES`）。
__all__ = ["PlannerEngine", "_POC_DEFAULT_SCOPES"]


# R4.4：拒识/澄清 env 门控（模块级、实时读——env 翻转即刻生效，且测试可 monkeypatch）。
# REJECT 默认 on（作用域已被 hands-free opt-in + input_source 双重限定）；CLARIFY 默认 off
# （影响所有云端路由，比拒识作用域大，真栈验收后独立 commit 翻 on，母卡 §5）。
def _reject_enabled() -> bool:
    return os.getenv("REJECT_NON_ADDRESSED", "on").lower() != "off"


def _clarify_enabled() -> bool:
    # 兜底缺省与部署缺省对齐（`.env.example` / compose 都是 on）——两处不一致时，
    # 不经 compose 起的进程会静默测到另一套装配。见 planning.py 同名开关的注释。
    return os.getenv("CLARIFY_ENABLED", "on").lower() == "on"


_DIGITS_RE = re.compile(r"\d")


def _goal_value_dropped(plan) -> bool:
    """goal 文本里有数字，而计划的槽位里一个数字都没有 → 值在 goal→slots 那一步丢了。

    **只作观测信号，不改任何行为**（发一个 obs 布尔位）。判据刻意是「有没有数字」这种
    粗粒度：它要抓的形态是「模型明明算出来了却没写进去」，而**误报的代价只是一位观测**，
    漏报的代价是缺陷继续隐形——journeys `B3-3` 就靠人肉比对 `llm_raw` 才发现。

    零领域字面量：不认识任何 intent，也不认识任何槽名。
    """
    steps = getattr(plan, "steps", None) or []
    if not steps:
        return False                    # 没有步骤时「丢没丢值」无从谈起，另有检测器管缺步
    if not _DIGITS_RE.search(_goal_text(plan)):
        return False
    return not any(_DIGITS_RE.search(str(v))
                   for s in steps for v in (s.slots or {}).values())


#: 一个槽值要至少这么长才算「落在某个分句里」。1 字的值（"1"、"是"）在任何句子里
#: 都可能撞上，拿它判覆盖等于把噪声当信号。
_COVER_MIN_LEN = 2


def _clause_uncovered(plan, text: str) -> str:
    """多意图复合句的覆盖度**观测**（C5-A）。返回 `"未覆盖数/肯定分句数"`，无信号返回空串。

    ## 它要抓的形态

    「先查瑞幸，再点生椰拿铁不加糖」只出了 nearby 一步、下单意图整个消失（真栈 T18）；
    「接孩子放学，顺便找麦当劳」只出了 nearby、接人那半没了（T50）。**同一句话在另一个
    persona 下又出对了两步**——方差本身就是「零护栏」的读数。此前这件事只有 prompt 里
    一句软约束（「提交前逐个核对每个肯定诉求」），唯一的机器判据 `_goal_value_dropped`
    **只判数字**，且它自认「组合意图漏第二步只能判到缺步」而没有实现。

    ## 判据

    按 `runtime.clause_split`（分隔符表的唯一声明处）拆句 → 丢掉否定分句
    （`runtime.polarity`，「车窗别开」不是一个待覆盖的诉求）→ **肯定分句 ≥2 时才有信号**
    （这是个*多*意图判据；单句时它退化成「有没有槽值」，那是另一回事）→ 每个分句问一句：
    有没有任何一步的槽值落在它里面。

    ## 已知误报面（**先拿真实分布**，B6 shadow 的纪律）

    - **零槽步覆盖不了任何分句**（`navigation.locate` 这类）——它们会让所在分句报未覆盖；
    - planner 把槽值**转述**过（「瑞幸」→「luckin」）时子串够不着。

    两类都会抬高未覆盖率。这正是本批只发观测、不进决策的原因：先看两周真实分布里
    误报占多少，再谈 C 的 salvage 重试。**误报的代价只是一位观测，漏报的代价是缺陷继续隐形。**

    零领域字面量：不认识任何 intent，也不认识任何槽名。
    """
    steps = getattr(plan, "steps", None) or []
    clauses = [c for c in split_clauses(text) if not is_negated_directive(c)]
    if len(clauses) < 2 or not steps:
        return ""
    values = [v for s in steps for v in (s.slots or {}).values()
              if isinstance(v, str) and len(v.strip()) >= _COVER_MIN_LEN]
    uncovered = sum(1 for c in clauses
                    if not any(v.strip() in c for v in values))
    return f"{uncovered}/{len(clauses)}" if uncovered else ""


def _goal_text(plan) -> str:
    """从 `raw_llm` 里取 goal 字段；取不到就返回空串（观测信号，不值得为它抛异常）。"""
    try:
        data = json.loads(getattr(plan, "raw_llm", "") or "{}")
    except (json.JSONDecodeError, ValueError, TypeError):
        return ""
    return str(data.get("goal") or "") if isinstance(data, dict) else ""


def _edge_nlu_attrs(ctx, plan) -> dict:
    """端云分歧观测（M5 P2-D2）。端侧没判 → 不发（少一个恒空字段）。

    比的是**域**不是 intent：端侧的 `hvac.on` 与云侧的 `hvac.set` 是同一个判断的粗细之分，
    记成分歧只会把噪声灌进标注队列。真正值得人看的是「端侧说车控、云侧说闲聊」这种。"""
    raw = str(getattr(ctx, "edge_nlu", "") or "")
    if not raw:
        return {}
    edge_intent = raw.split("|", 1)[0]
    edge_dom = edge_intent.split(".")[0]
    cloud_doms = {s.intent.split(".")[0] for s in plan.steps if s.intent}
    return {"edge_nlu": raw,
            "edge_agree": "1" if (edge_dom and edge_dom in cloud_doms) else "0"}


def _actionability_attrs(plan) -> dict:
    """可执行性 shadow 的四元组（B6 §2，shadow 记录）。

    `actionability` = 形态判定 `<decision>|<conf>`；`actionability_planner` = planner
    这一轮实际的决策；`actionability_agree` = 两者一不一致——**分歧轮才是有信息量的
    标注样本**（同 `edge_agree` 的口径，让分歧在扫描时可见而不必逐轮补拉 span）。

    四元组的第四位 `human_gold` **不在这里发**：它今天由离线回放
    （`test/eval_actionability.py` 读对抗语料金标）供给；运行期那一路要等标注 API
    长出决策字段，而那个字段的写入方与 B6 自己的启动条件是同一件事（真实流量分母）。
    先落一个没有写入方的列只会是死字段（同 `complexity_declared` 不进 span 的判据）。
    """
    raw = str(getattr(plan, "actionability", "") or "")
    if not raw:
        return {}
    if plan.clarify is not None:
        planner_decision = "clarify"
    elif not plan.addressed:
        planner_decision = "reject"
    else:
        planner_decision = "execute"
    return {"actionability": raw,
            "actionability_planner": planner_decision,
            "actionability_agree": (
                "1" if raw.split("|", 1)[0] == planner_decision else "0")}


async def _emit_engine_lifecycle(ctx: PlanContext, node: str, intent: str) -> None:
    """Give deterministic engine-only turns an auditable owner and intent."""
    await obs_events.get_emitter("cloud").emit_span(
        ctx.trace_id,
        node,
        attrs={"intent": intent, "owner": "cloud-engine"},
    )


class PlannerEngine:
    """编排主循环。engine 是唯一持有全局状态的地方。"""

    def __init__(self, clients, planner: PlanBuilder, executor: DagExecutor,
                 aggregator: Aggregator, session: SessionStore, loop=None):
        self.clients = clients
        self.planner = planner
        self.executor = executor
        self.aggregator = aggregator
        self.session = session
        # 权限决策单轨化（R2.2）：唯一决策点是 security.permission.check_permission
        # （规划期 catalog 过滤 + dispatch 执行期硬拒同源复用），编排层不再持权限引擎。
        # trust-cap 强上限（K4）待 scope 层次化/IdP 后接线，届时扩 check_permission，不在此处复注入。
        self.context = ContextManager(clients, session)  # 上下文统一门面（装配+焦点态）
        self.loop = loop or LoopController(
            planner, executor, aggregator, self._suspend,
            stream_fn=getattr(clients, 'call_agent_stream', None))

    async def run(self, request) -> AsyncIterator[dict]:
        """编排主循环（外层）：委托 _orchestrate，并把本轮对话落库到 memory。

        对话记忆在本轮结束后按 用户→助手 顺序写入——规划阶段读到的是"此前"历史，
        当前这句不污染指代消解（task 2）。memory_enabled=false 时整轮不读写。
        """
        ctx = self._build_context(request)
        set_trace_id(ctx.trace_id)
        set_session_id(ctx.session_id)  # 云端进程内观测事件/日志自动带会话维度
        # 运行时硬化 D2：请求级 LLM pin——planner/aggregator 的 LLM 调用与 Agent 同脑
        set_llm_pin(ctx.prefs.get("llm_provider", ""), ctx.prefs.get("llm_model", ""))
        text = (getattr(request, "text", "") or "").strip()
        ctx.raw_text = text  # 透传给 Agent（供 navigate_to 等 fallback 槽位提取）
        mem_on = ctx.prefs.get("memory_enabled", "true") != "false"

        assistant_speech = ""
        executed_actions: list[str] = []
        turn_sources: list[dict] = []
        rejected = False
        async for ev in self._orchestrate(request, ctx, text, mem_on):
            # R4.4：剥离内部标记键，消费端（server.py）看不到；同时记本轮是否拒识。
            if ev.pop("_rejected", False):
                rejected = True
            if ev.get("kind") == "final":
                # Q1-C：本轮关掉了哪几条挂起，由服务端权威告诉 HMI（撤确认条）。
                # 空则不发键——多一个恒空字段就是多一处噪声。
                if ctx.closed_operation_ids:
                    ev["closed_operation_ids"] = list(ctx.closed_operation_ids)
                if ev.get("speech"):
                    assistant_speech = ev["speech"]
                # Q6：本轮真实执行了什么，随 assistant 轮一起落库。
                # 取 final 帧而不是 step_result——**用户看到的那份就是这一份**，
                # 中途被聚合器丢掉的步不该出现在审计回答里。
                executed_actions = _executed_action_names(ev.get("actions"))
                # C4-A：这一轮的数据源事实与动作事实同源同格，一起落库。
                turn_sources = _turn_sources(ev.get("ui_card"))
                # C11-C：「话里有一次执行，账上没有」的**观测列**（零决策）。
                # 挂在这里而不是聚合器：final 有四条出口（adaptive/stream/reactive/E），
                # 而它们全都从这个 for 里流过——**同一条纪律写成注释还是写成结构，
                # 差别就是会不会有第三次**（`_deterministic_reply` 那条老账）。
                await self._emit_execution_claim(ctx, ev, executed_actions)
            yield ev

        # R4.4：拒识轮 user+assistant 均不落库——不污染指代消解、不触发 memory 画像抽取
        # （母卡 D3；落库本就在编排循环之后，时序天然支持）。
        if mem_on and text and not rejected:
            occ = getattr(ctx, "occupant_id", "") or "primary"
            # 一轮请求与它可见的回复共用一个 exchange（M-B）：请求 id 就是天然的
            # exchange 键，缺失时才生成——重试因此是重放，不是又追加一轮新对话。
            exch = getattr(ctx, "request_id", "") or f"x-{uuid.uuid4().hex[:16]}"
            await self.context.append_turn(ctx.session_id, "user", text,
                                           ctx.user_id, ctx.vehicle_id, occ,
                                           ctx.e2e_memory_capability,
                                           turn_id=f"{exch}:user", exchange_id=exch)
            if assistant_speech:
                await self.context.append_turn(ctx.session_id, "assistant", assistant_speech,
                                               ctx.user_id, ctx.vehicle_id, occ,
                                               ctx.e2e_memory_capability,
                                               turn_id=f"{exch}:assistant:0",
                                               exchange_id=exch,
                                               actions=executed_actions,
                                               sources=turn_sources)

    @staticmethod
    async def _emit_execution_claim(ctx, event: dict, actions: list) -> None:
        """本轮 final 说了「已为您…」却零动作 ⇒ 出一位 obs 观测（C11-C，shadow）。

        判据本体在 `runtime/execution_claim.py`（形态、零领域词），探针 C16-2 复用
        同一份。**只观测不拦截**：直拦的误伤面（信息类能力的合法完成语、转述历史）
        没量过，先拿两周真实分布——同 B6 `actionability` 与 C5-A `clause_uncovered`
        的纪律。命中就发一条 span，不命中一个字都不发（多一个恒空字段就是多一处噪声）。
        """
        if actions:
            return
        family = execution_claim(str(event.get("speech") or ""))
        if not family:
            return
        try:
            await obs_events.get_emitter("cloud").emit_span(
                ctx.trace_id, "cloud.execution_claim",
                attrs={"family": family})
        except Exception as e:      # 观测绝不阻塞主链路
            logger.debug("execution claim obs failed: %s", e)

    async def _orchestrate(self, request, ctx: PlanContext, text: str,
                           mem_on: bool) -> AsyncIterator[dict]:
        """规划→校验→执行→聚合。yield 事件：{"kind": "speech"|"action"|"final", ...}"""
        plan: Plan | None = None
        seed_results: list[StepResult] = []
        agents = []
        working_set = None  # 新规划轮由 ContextManager 装配；确认/补槽续接保持 None

        # A. 多轮续接：存在挂起的待确认会话时，判定本轮是否在回应确认
        # R2（中断-恢复，Q1 口径）：插话**不清除**挂起——插话轮正常处理，挂起在 TTL 内
        # 可回头「确认」/裸答案续接；新话轮若再产生挂起，_suspend 单槽覆盖旧挂起
        # （确认条 UI 也只有一个，语义一致）。held_pending 贯穿本轮：完成路径经
        # _settle_session 跳过 clear，并在 final 上补一句软提醒。
        held_pending = None
        pending = await self.session.load(
            ctx.session_id, owner_user_id=ctx.user_id,
            operation_id=ctx.operation_id)

        # Q1-B：确认帧带了寻址键却对不上任何挂起 → **诚实拒绝**。
        # 不静默打给当前挂起（I-013 全局确认命中旧请求），也不清掉它——
        # 那一下不是冲它来的。空 operation_id（语音兜底/旧客户端）不走这条。
        if ctx.operation_id and pending is None:
            logger.info("Confirmation addressed a pending that is gone (%s)",
                        ctx.operation_id[:16])
            await _emit_engine_lifecycle(
                ctx, "cloud.pending_missing", "system.pending_missing")
            yield {"kind": "final",
                   "speech": "这条确认对应的操作已经不在了，麻烦您再说一遍需求。"}
            return

        # Q1-A：取消判定对两条分支是**同一件事**，所以在分岔之前判一次。
        # 此前 wait_confirm 走「词占据整句」、wait_slot 走子串+复合余量，
        # 「取消刚才解锁」在前者判不出取消（I-046）。判据全在 pending_cancel。
        just_cancelled = False
        if pending and not self._is_cancel_index_answer(text, pending):
            cancelled = detect_cancel(text)
            if cancelled.cancelled:
                just_cancelled = True
                await self._close_pending(ctx, pending)
                if not cancelled.compound:
                    await _emit_engine_lifecycle(
                        ctx, "cloud.pending_cancel", "system.pending_cancel")
                    yield {"kind": "final", "speech": "好的，已为您取消。"}
                    return
                # 复合句（「算了咖啡不买了，**先去加点油**」）：取消只作用于挂起，
                # 其余内容按全新请求继续处理——不 return、不进确认/补槽/话题分支。
                logger.info(
                    "pending cancel with compound remainder (%d chars); "
                    "continuing as fresh request", len(cancelled.remainder))
                pending = None

        if pending and pending.phase == "wait_confirm":
            reply = self._confirm_reply(text, ctx.is_confirmation)
            if reply == "yes":
                plan, seed_results = self._restore(pending, inject_confirmed=True)
                if plan is None:
                    await self._close_pending(ctx, pending)
                    await _emit_engine_lifecycle(
                        ctx, "cloud.pending_expired", "system.pending_expired")
                    yield {"kind": "final",
                           "speech": "刚才的操作已过期，麻烦您再说一遍需求。"}
                    return
                ctx.pending_operation_id = pending.operation_id
                logger.info("Resuming plan for session %s (confirm step %s)",
                            ctx.session_id, pending.pending_step_id)
            else:
                # 答非所问：用户插话——保留挂起按新请求处理（R2；原实现在此丢弃挂起，
                # 用户回头说「确认」只能得到「当前没有待确认的操作」，旅程 B2-1 抓到）
                held_pending = pending
        elif pending and pending.phase == "wait_slot":
            # F12：补槽续接——判定用户是在回答追问还是换了话题。
            # （取消已在上面的共用判据里处理完，此处只剩「答案 vs 换话题」。）
            if int(getattr(pending, "slot_retry", 0) or 0) >= SLOT_RETRY_LIMIT:
                # C3-D：同一个问题问到上限还没接上 —— **放弃它，别再问第四遍**。
                # 这一轮按全新请求走：黑洞的止损不是「判得更准」，是「吞不了太多轮」。
                # 放弃**必须有话术**（Q1-C「淘汰必须有话术」的同一条纪律），
                # 名字随 ctx 带到本轮 final 的 follow_up。
                logger.info("Abandoning pending %s after %d unanswered slot asks",
                            (pending.operation_id or "")[:16], pending.slot_retry)
                ctx.abandoned_pending_label = self._pending_label(pending)
                await self._close_pending(ctx, pending)
                pending, plan, seed_results = None, None, []
            elif self._is_topic_change(text, pending):
                # 答非所问：用户插话——保留挂起按新请求处理（R2，下轮裸答案仍可续接）
                held_pending = pending
                plan, seed_results = None, []
            else:
                # 补槽恢复绝不注入 confirmed——补槽答案不是确认（见 _restore docstring）
                plan, seed_results = self._restore(pending, inject_confirmed=False)
                if plan is None:
                    await self._close_pending(ctx, pending)
                    await _emit_engine_lifecycle(
                        ctx, "cloud.pending_expired", "system.pending_expired")
                    yield {"kind": "final",
                           "speech": "刚才的操作已过期，麻烦您再说一遍需求。"}
                    return
                ctx.pending_operation_id = pending.operation_id
                # C3-D：带上这条挂起当时**问的是什么**，`_suspend` 才分得清
                # 「同一个问题又问一遍」（计数 +1）与「同一步换了个槽再问」（进展，归零）。
                ctx.pending_slot_probe = {
                    "step_id": pending.pending_step_id,
                    "missing": sorted(pending.missing_slots or []),
                    "retry": int(getattr(pending, "slot_retry", 0) or 0),
                }
                # Phase 1 简单版：直接用用户原始文本填 slot（Agent LLM 能理解自然语言）
                for step in plan.steps:
                    if step.id == pending.pending_step_id:
                        for slot_name in (pending.missing_slots or []):
                            step.slots[slot_name] = self._slot_answer(
                                slot_name, text)
                        break
            if pending is not None:
                logger.info(
                    "Resuming plan for session %s (slot fill step %s, text=%s)",
                    ctx.session_id, pending.pending_step_id, text[:20])
        elif not just_cancelled and (
                ctx.is_confirmation or self._is_bare_confirm_word(text)):
            # 带确认标记，或裸"确认/取消"，但没有挂起任务（TTL 过期/上一步异常/重复点击）。
            # `just_cancelled` 排除复合取消刚清掉挂起的那一路——那句话的余量是新请求，
            # 不能因为它带确认标记就被答成「当前没有待确认的操作」。
            # 关键：裸确认词绝不下交 Planner——否则会借历史把"确认"重规划成上一意图的重复
            # 执行（反复 trip.modify），即用户报告的"确认后又改一遍并再次要确认"死循环。
            await _emit_engine_lifecycle(
                ctx, "cloud.no_pending", "system.no_pending")
            yield {"kind": "final",
                   "speech": "当前没有待确认的操作。您可以重新告诉我需求。"}
            return

        new_plan = plan is None
        if plan is None:
            # ws8 P1: 注入检测——疑似 prompt injection 时拦截，不进 Planner
            from security.injection import detect_injection
            if detect_injection(text):
                logger.warning("Prompt injection detected, rejecting: %s", text[:80])
                await _emit_engine_lifecycle(
                    ctx, "cloud.injection_reject", "system.injection_reject")
                yield {"kind": "final",
                       "speech": "抱歉，您的请求包含异常内容，无法处理。"}
                return

            # B. 新规划：经 ContextManager 统一装配（catalog 语义预筛 + 此前对话历史
            # + 长期偏好记忆，统一字符预算渲染）。失败子项各自降级，不阻塞规划。
            working_set = await self.context.assemble(
                text, ctx, mem_on=mem_on,
                granted_permissions=ctx.granted_permissions)
            agents = working_set.catalog

            # Q2/I-052：句首就在引用「第 N 个」，而**一份可引用的候选都没有**
            # → 确定性诚实弃权，**不进 Planner**。
            # 真栈原样复现过它不该发生的样子：无任何候选集时，「第一个营业到几点」
            # 被答成「个芙云朵蛋糕(南山京基百纳店)，评分3.9，人均23.00，
            # 今日营业10:00-22:00」——**一整条编出来的记录**。
            # 判据两条都必须成立（形态 + 事实），且形态锚在句首：
            # 「第二天第一个景点」指的是行程内部，不是上一份列表。
            # I-030：绑哪一组由**这句话点名了谁**决定，不是无条件绑最新那一组。
            # 卡上写的是「跨组比较做不了」，真栈取证的形态更严重——两家菜单并存时
            # 「麦当劳的第二个多少钱」被绑到瑞幸那组，零方差地答出「「生椰拿铁」
            # 16 元」：商品名与价格都真实存在，只是答的是另一家。**没有任何一处
            # 对不上，所以比编造更难被发现。** 零命中时退回旧口径、行为逐字不变。
            live_candidates, named_candidates = resolve_candidate_scope(
                text, working_set.focus)
            if references_a_candidate(text) and live_candidates is None:
                logger.info("Ordinal reference with no candidate set: %s", text[:40])
                await _emit_engine_lifecycle(
                    ctx, "cloud.candidate_missing", "system.candidate_missing")
                yield {"kind": "final",
                       "speech": "我这边没有可以引用的列表。你先说要找什么，"
                                 "我列出来之后再说「第几个」就能接上。"}
                return

            # Q2 残余：候选集上的**聚合问题**由确定性算子回答，同样不进 Planner。
            # 与上面那条**一正一反、同一个判据面**：那条是「引用了候选但一份都没有」，
            # 这条是「引用了候选而候选就在手里」。
            # 为什么不交给 Agent：落到哪个 Agent 都是错的——`nearby.search` 重搜一遍
            # 答的是**新一批**（CD1 首跑逐字重复上一轮整段列表就是这个形态），
            # chitchat 手里根本没有那些数。判据三段同时成立才劫持，见
            # `candidate_query.is_candidate_aggregate_question` 那段。
            aggregate = candidate_query.answer(text, live_candidates,
                                               named_candidates)
            if aggregate:
                logger.info("Deterministic candidate aggregate: %s", text[:40])
                await obs_events.get_emitter("cloud").emit_span(
                    ctx.trace_id, "cloud.candidate_aggregate",
                    attrs={"source_intent": str(
                        (live_candidates or {}).get("source_intent") or ""),
                        "intent": "system.candidate_aggregate",
                        "items": len((live_candidates or {}).get("items") or []),
                        # 取证面：这一轮点名了几组、绑的是不是点名的那一组。
                        # 「答错组」在话术层看不出来（名字与价格都真实存在），
                        # 只有把「按谁答的」记下来才查得了。
                        "named_groups": len(named_candidates)})
                yield {"kind": "final", "speech": aggregate}
                return

            # C4-B：**系统持有的会话事实**的确定性读出口族——挂起状态 / 数据源 /
            # 执行史。与上面两条候选短路同挂点、同判据面，理由也是同一条：
            # 这一族的病**不是模型答得不好，是它手里根本没有那些数**（chitchat 只
            # 拿得到 4 轮纯文本，actions/卡片/`_prov` 一个都不进 prompt）。
            # 所以短路必须在**落域之前**：Q6 把审计闸建在 chitchat 里，
            # 2026-08-26 T37 被 planner 接给 reminder.list，闸就够不着了。
            fact = await self._session_fact_answer(ctx, text, working_set)
            if fact:
                node, speech = fact
                logger.info("Deterministic session fact (%s): %s", node, text[:40])
                await _emit_engine_lifecycle(
                    ctx, f"cloud.{node}", f"system.{node}")
                yield {"kind": "final", "speech": speech}
                return

            plan = await self.planner.build(
                text, working_set, ctx,
                granted_permissions=ctx.granted_permissions)

            # R4.4 D6-1：hands-free 语音源 + LLM 判非受话 → 静默拒识（route_hints 兜底的 steps
            # 一并作废）。显式输入（push-to-talk/文本/候选选择）无 input_source，永不拒识。必须在
            # `if not plan.steps` 之前——addressed=false 时 steps 恰为空，否则先走空计划话术+TTS
            # 令拒识失效（母卡实施计划 §0-4）。
            if (ctx.prefs.get("input_source", "").startswith("voice_")
                    and not plan.addressed and _reject_enabled()):
                await obs_events.get_emitter("cloud").emit_span(
                    ctx.trace_id, "rejected",
                    attrs={"reason": "not_addressed",
                           "intent": "system.rejected",
                           "owner": "cloud-engine"})
                yield {"kind": "final", "speech": "",
                       "ui_card": {"type": "rejected", "reason": "not_addressed"},
                       "_rejected": True}
                return

            if not plan.steps:
                # R4.4 D6-3：路由歧义澄清（CLARIFY 开 + 本轮非 clarify_resume 深度=1 才生效）。
                # P0 时 CLARIFY_ENABLED 默认 off → 恒 None，行为=今天；P1 翻 on 后短路出卡。
                clarify = (plan.clarify if (_clarify_enabled()
                           and ctx.prefs.get("clarify_resume") != "1") else None)
                if clarify:
                    await _emit_engine_lifecycle(
                        ctx, "clarify", "system.clarify")
                    yield {"kind": "final", "speech": clarify["question"],
                           "ui_card": {"type": "intent_choice", **clarify}}
                    return
                # 取消闸（余项，2026-08-29）：这句取消话没落到任何可执行的东西上。
                # **「没听清」在这里是假的**——我们听清了，只是不知道他说的是哪一件事
                # （planner 看不见用户有哪些提醒/订单）。两句话的区别就是这一条闸的
                # 全部意义：不许答「已经取消啦」，也不该赖用户说不清楚。
                if getattr(plan, "cancel_unresolved", ""):
                    await _emit_engine_lifecycle(
                        ctx, "cloud.cancel_unresolved", "system.cancel_unresolved")
                    yield {"kind": "final",
                           "speech": f"我没找到和「{plan.cancel_unresolved}」"
                                     f"对得上的事，你说的是哪一件？"
                                     f"说得再具体点我就能取消。"}
                    return
                # R4.4 D5-2：诚实降级话术（含 fallback 低分不硬执行的场景），比「无法处理」更引导重说。
                await _emit_engine_lifecycle(
                    ctx, "cloud.no_plan", "system.no_plan")
                yield {"kind": "final", "speech": "抱歉，我没听清您想让我做什么，可以换个说法吗。"}
                return

            # 用系统持有的会话焦点补全 Planner 省略的结构化上下文，再记录 trace；
            # 这样观测到的是下游真正执行的计划，不是补全前的半成品。
            self._apply_focus_meta(plan, working_set.focus)
            # 跨轮门店锚定的**唯一入口**：把上一轮 nearby.search 的公开 POI 放上
            # PlanContext（服务端对象，LLM 与客户端都写不到）。executor 只在本轮 plan
            # 内没有生产者时才用它补门店三元组，并写同构 provenance——
            # 契约见 docs/design/2026-08-13-cross-turn-store-anchor.md。
            ctx.focus_places = list(
                getattr(working_set.focus, "last_places", None) or [])
            ctx.focus_places_ts = float(
                getattr(working_set.focus, "last_places_ts", 0.0) or 0.0)

            # C. 解析 endpoint（Registry）
            # badcase 排查内容级采集（OBS_CONTENT_CAPTURE 门控）：plan 结构 + LLM 原始输出。
            # 此前 LLM raw 只进 stdout 截 500 字符（planning.py），与 trace 无关联。
            clause_gap = _clause_uncovered(plan, text or plan.raw_text)
            await obs_events.get_emitter("cloud").emit_span(
                ctx.trace_id,
                "cloud.planning",
                attrs={
                    "complexity": plan.complexity,
                    "steps": len(plan.steps),
                    "plan": gate_content(json.dumps(
                        [{"id": s.id, "agent": s.agent_id, "intent": s.intent,
                          "slots": s.slots} for s in plan.steps],
                        ensure_ascii=False, default=str), 1200),
                    "llm_raw": gate_content(plan.raw_llm, 1200),
                    # M0b Skill 层注入名单（"<mode>:<name>"），badcase 归因用
                    **({"skills": ",".join(plan.skills)} if plan.skills else {}),
                    # 模型原生接对与声明式 skill 归一后接对必须分开观测。
                    **({"skill_effects": ",".join(plan.skill_effects)}
                       if getattr(plan, "skill_effects", None) else {}),
                    # M5 P1 范例库注入名单（"<mode>:<eid>@通道:分数"，被裁记 !clipped）：
                    # 「范例没检回 / 检回了没用对 / 检回了却被裁」三种失败一眼可分
                    **({"exemplars": ",".join(plan.exemplars)}
                       if getattr(plan, "exemplars", None) else {}),
                    # M1a：本轮规划输出通道（toolcall|toolcall_salvage|…|json），A/B 聚合用
                    "plan_mode": getattr(plan, "plan_mode", "json"),
                    # B5 §3：本轮命中的重试策略名（声明序）。与 plan_mode 是两个问题
                    # ——「哪条守卫判掉了这一版」vs「最后走的哪条通道」。重构前守卫命中
                    # 在观测上完全看不见，只能靠日志。
                    **({"retry_policies": ",".join(plan.retry_policies)}
                       if getattr(plan, "retry_policies", None) else {}),
                    # B6 §2 可执行性 shadow（主链零行为变化）
                    **_actionability_attrs(plan),
                    # 数据飞轮 P0 落域可观测：意图名是系统枚举值（非用户内容），紧凑发射
                    # 不过内容门控——collector 据此把落域合并进 turns 行（SQL 可聚合）。
                    "intents": ",".join(s.intent for s in plan.steps),
                    # **goal 是免费的对照物**（第三例，journeys B3-3）：模型在 goal 里
                    # 把值算出来了（「调到最喜欢的温度（26度）」），plan 却是
                    # `hvac.set` + `slots:{}`——终态不变、话术渲染成一个单字「度」。
                    # 前两例（goal 说推荐而 steps 无推荐步 / 组合意图漏第二步）只能判到
                    # 「缺步」，这一例**机器可判到值一级**：goal 文本里有数字、而全部
                    # step 的槽位里一个数字都没有。纯算术、零领域字面量，误报的代价只是
                    # 一个 obs 布尔位。
                    **({"goal_value_dropped": "true"} if _goal_value_dropped(plan) else {}),
                    # C5-A 多意图覆盖度（观测，零决策）：「肯定分句里有没有哪一段
                    # 一个 step 都没碰过」。与上面那条是同一族——前者判到**值**一级，
                    # 这条判到**诉求**一级，正是那条注释里写着「只能判到缺步」的那半。
                    **({"clause_uncovered": clause_gap} if clause_gap else {}),
                    # M5 P2-D2 端云分歧：端侧规则臂的初判 + 是否与云侧最终落域一致。
                    # **分歧轮是信息量最大的标注样本**——两个独立判断打架的地方，人看一眼
                    # 的边际收益最高。evolve mine 据此产 `edge_divergence` 信号进日报案族卡，
                    # 人按日报去 dashboard 标 gold（→ 范例 → 飞轮）。
                    **_edge_nlu_attrs(ctx, plan),
                    **({"hint_effect": plan.hint_effect}
                       if getattr(plan, "hint_effect", "") else {}),
                    # D1 裁剪可观测：目录渲染长度 + 被裁 agent（静默丢域从此有痕迹）
                    **({"catalog_chars": plan.catalog_stats.get("chars_final", 0),
                        **({"catalog_dropped":
                            ",".join(plan.catalog_stats.get("dropped", []))}
                           if plan.catalog_stats.get("dropped") else {})}
                       if getattr(plan, "catalog_stats", None) else {}),
                },
            )
            await self._resolve_endpoints(plan)
            # 权限校验按步在 dispatch 执行期硬拒（与规划期 catalog 过滤同源 check_permission），
            # 此处不再做计划级兜底（原 _enforce_permissions 为空壳，已移除）。

        # 统一「复杂任务」判据，驱动①动态开思考②过程区。普通车控/闲聊/单条轻查询
        # 不命中——零过程、零额外延迟（需求第 6 条）。
        complex_task = is_complex(plan)
        if complex_task:
            # 给每个 step 打 thinking=on：经 ExecuteRequest.meta → agent _current_meta →
            # SDK LLMClient 自动开思考，无需改各 Agent 业务码。确认续接的重跑步骤也受益。
            for s in plan.steps:
                s.meta = {**s.meta, "thinking": "on"}
        # 过程区只对「全新复杂任务」展示；确认/补槽续接是快速收尾，不再起一段过程区。
        # 四阶段：理解需求 → 规划步骤 →（执行任务）→ 整理结果。前两段在此发。
        show_process = complex_task and new_plan
        if show_process:
            yield self._progress("understand", "理解需求",
                                 summary=task_summary(plan), status="done")
            yield self._progress("plan", "规划步骤",
                                 summary=plan_steps_summary(plan), status="done")

        # D-T2. Adaptive plans enter the bounded loop. Confirmation resumes keep
        # their adaptive metadata and continue from the saved result seeds.
        metrics.record_intent(f"complexity.{plan.complexity}", 0, True)
        # 数据飞轮 P0：record_intent 此前从不记真实意图（只记 complexity./t2_loop 元标签）、
        # record_route 零调用——WS9「意图/路由」指标名存实亡。此处按步记真实意图分布；
        # 持久可查的尺子在 obs turns.intents 列（本内存计数供 snapshot/日志侧参考）。
        metrics.record_route("cloud")
        for s in plan.steps:
            metrics.record_intent(s.intent, 0, True)
        if plan.complexity == "adaptive":
            if not agents:
                agents = await self.clients.list_agents()
            async for event in self.loop.run(
                    goal=plan.goal or text or plan.raw_text,
                    initial_plan=plan,
                    agents=agents,
                    ctx=ctx,
                    user_text=text or plan.raw_text,
                    seed_results=seed_results,
                    working_set=working_set,
                    show_process=show_process, thinking=complex_task):
                if event.get("kind") == "final":
                    await obs_events.get_emitter("cloud").emit_span(
                        ctx.trace_id,
                        "aggregate",
                        attrs={"path": "adaptive"},
                    )
                yield event
            return

        # D0. 单步新规划走流式直通（task 4：开放域"边想边说"，秒级反馈）。
        # 仅对全新单步计划开启；确认续接/多步计划保持 executor 路径，不动 F1 闭环。
        # 复杂单步（如独立的 trip.plan / info.search）排除在外——走 executor 才能发过程区。
        if (new_plan and plan.complexity == "simple" and len(plan.steps) == 1
                and not ctx.is_confirmation and not complex_task
                and plan.steps[0].kind == "agent"
                and plan.steps[0].deployment == "cloud"
                # M0a-3：capability 声明 require_confirm 的步不走流式直通——D0 会把
                # 流中 action 直接放行，绕开 executor 的确认兜底闸；走 executor 路径。
                and not plan.steps[0].require_confirm):
            step = plan.steps[0]
            # 流式直通**绕过 executor**，所以 executor 里挂的槽位解析在这条路上不生效。
            # 2026-08-13 实证：跨轮门店锚定挂在 `_resolve_slot_refs` 上，而 `luckin.menu`
            # （require_confirm=false）恰好走这条路 —— 诊断日志一行都没打出来，
            # 因为那个函数根本没被调用。**新增挂点必须枚举全部执行路径**，
            # 这是本项目第二次踩（M2 那批的 Verifier 也在这里漏过一次）。
            self.executor._resolve_slot_refs(step, {}, ctx)
            _d0_start = time.monotonic()
            # B5 §4：流出面状态与「能不能回退 / 结果确不确定」的判定与 T2 共用一份
            # （`stream_state`）。此前 D0 一个 `streamed` 布尔、T2 三个布尔各写各的，
            # 判定表抄了两份，B1 修的就是其中一份抄错。
            stream = StreamTracker()
            softener = MdDeltaSoftener()   # 流式增量剥 **/`（final 由 compose 出口彻底清理）
            final_sr: StepResult | None = None
            try:
                async for kind, payload in self.clients.call_agent_stream(
                        step.endpoint, step.intent, step.slots, ctx, step.meta):
                    if kind == "speech":
                        payload = softener.feed(payload)
                        # 记的是**软化之后**的增量：softener 会把悬空的 `*` 扣下一拍，
                        # 那一拍用户什么都没看到。「流出过输出」必须指用户真的收到了东西，
                        # 否则一个空串就能把 unary 回退整条关掉（T2 一直是这个口径，
                        # 有专门的空 delta 对照测试；D0 此前不是——统一到严的这边）。
                        stream.on_speech(payload)
                        if payload:
                            yield {"kind": "speech", "delta": payload}
                    elif kind == "action":
                        stream.on_action()
                        yield {"kind": "action", "action": payload}
                    elif kind == "final":
                        final_sr = DagExecutor._to_result(step.id, payload)
            except Exception as e:
                logger.warning("Single-step stream failed (%s); falling back to unary", e)

            if final_sr is not None:
                stream.on_final()
                # M2 Verifier：流式直通不经 executor._exec_step，必须在此显式对账，
                # 否则 capability 声明了 verification 却静默不生效（真栈首验实测：
                # weather 走 D0 流式，一条 step.verify span 都没有）。
                # allow_retry=False：话术已经流给用户了，重跑会重复播报。
                final_sr = await self.executor._verify_outcome(
                    step, final_sr, ctx, allow_retry=False)
                final_sr = self.executor._stamp_source(step, final_sr)
                # 流式直通也补 step.agent span（否则单步云端 agent 链路缺这一跳）
                _pending = final_sr.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT)
                await obs_events.get_emitter("cloud").emit_span(
                    ctx.trace_id, f"step.agent:{step.agent_id}",
                    status="wait" if _pending else (
                        "ok" if final_sr.status == StepStatus.OK else "err"),
                    duration_ms=(time.monotonic() - _d0_start) * 1000,
                    attrs={"intent": step.intent, "agent_id": step.agent_id,
                           "kind": "agent", "deployment": "cloud", "via": "stream"})
                results = [final_sr]
                focus_plan = plan
                # 通用 escalate（一跳）：Agent 声明「这题我不该答，改派给 X」。仅当未播报过任何
                # 增量（streamed=False）才生效——已流出话术再改派会双重回答（agent 端零 delta
                # 才 escalate + 此处 streamed 忽略，双保险）。检测到即剥键（含忽略场景），
                # 防 F3 slot_refs/下游误引用保留键。
                esc = self._parse_escalate(final_sr)
                if esc is not None and isinstance(final_sr.data, dict):
                    final_sr.data.pop("_escalate", None)
                if emitted_anything(stream.state):
                    esc = None
                if esc is not None:
                    sink: dict = {}
                    async for ev in self._run_escalated(esc, ctx, agents, sink):
                        yield ev
                    if sink.get("suspended"):
                        return
                    if sink.get("results"):
                        results = sink["results"]
                        focus_plan = sink["plan"]
                    else:
                        # 改派装配/执行失败：原 speech 为空（agent 零播报），给诚实兜底话术
                        await self._settle_session(ctx, held_pending)
                        yield {"kind": "final",
                               "speech": "这个需要联网查询，刚才没查成，请再说一次。"}
                        return
                if final_sr.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                    yield await self._suspend(final_sr, results, plan, ctx)
                    return
                await self._settle_session(ctx, held_pending)
                if mem_on:
                    await self.context.update_focus(
                        ctx.session_id, focus_plan, results,
                        user_id=ctx.user_id,
                        exchange_id=ctx.request_id)
                final = await self.aggregator.compose(text or plan.raw_text, results)
                self._append_pending_hint(final, held_pending)
                self._append_abandoned_hint(final, ctx.abandoned_pending_label)
                # M2 P2：会话级情绪信号随 final 透传给 HMI 选 TTS 情感参数（不入记忆）
                if getattr(plan, "emotion", ""):
                    final["emotion"] = plan.emotion
                await obs_events.get_emitter("cloud").emit_span(
                    ctx.trace_id,
                    "aggregate",
                    attrs={"path": "stream"},
                )
                yield {"kind": "final", **final}
                return
            if not allow_unary_fallback(stream.state):
                # 流出过输出却没收到 final：不回退重跑，避免重复播报 / 重复副作用。
                if outcome_uncertain(stream.state, stream.got_final):
                    # **D0 补上 T2 已有的那一档（B5 §4 统一后的第一笔收益）**：
                    # action 已经发给用户了，再说「请再试一次」等于邀请用户把一个
                    # 有副作用的动作发第二遍——正是 B1 在 T2 修掉的那个形态，
                    # 而 D0 一直原样留着。查一次世界状态再定话术，并打指纹。
                    uncertain_sr = await self.executor.stream_uncertain_result(
                        step, ctx)
                    final = await self.aggregator.compose(
                        text or plan.raw_text, [uncertain_sr])
                    yield {"kind": "final", **final}
                    return
                # 只流了话术：话已经说了一半，重跑会播两遍。
                yield {"kind": "final", "speech": "抱歉，刚才没说完，请再试一次。"}
                return
            # 无任何流式事件（不支持/连接失败）→ 安全回退到下面的 executor 路径

        # D. 执行 DAG（确认续接时：已完成结果作种子，只跑剩余步骤）
        done_seed = {r.step_id: r for r in seed_results}
        results = list(seed_results)
        # 执行任务阶段：先为每个待执行步骤发「进行中」占位（HMI 折叠态显示「正在查询天气…」），
        # 各步完成后再发同 step_id 的「完成」事件（HMI 按 step_id 合并 running→done）。
        if show_process:
            for s in plan.steps:
                if s.id not in done_seed:
                    yield self._progress("execute", phase_label(s.intent),
                                         status="running", step_id=s.id)
        # C5-B：**不在第一条挂起上 return**——executor 现在会把无依赖的兄弟步
        # 跑完（NEED_SLOT 档）。这里记下第一条挂起、消费完再挂，兄弟步的话术与
        # 动作才有机会进这一轮的 final。
        suspend_at: StepResult | None = None
        async for step_result in self.executor.run(plan, ctx, done=done_seed):
            results.append(step_result)

            # 过程区：每步完成发一条脱敏「完成」进度（仅复杂任务）。
            # 完成事件：OK 正常完成；NEED_CONFIRM/NEED_SLOT 也算"本轮已产出方案"（待确认/补槽），
            # 否则过程区永远停在"未完成"（此步不会再有 done 事件，如行程规划/调整）。
            if show_process and step_result.status in (
                    StepStatus.OK, StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                step = next((s for s in plan.steps if s.id == step_result.step_id), None)
                if step is not None:
                    summary = step_summary(step, step_result)
                    if step_result.status == StepStatus.NEED_CONFIRM:
                        summary = (summary or "已生成方案") + "（待确认）"
                    elif step_result.status == StepStatus.NEED_SLOT:
                        summary = summary or "需要补充信息"
                    yield self._progress(
                        "execute", phase_label(step.intent),
                        summary=summary, status="done", step_id=step.id)

            # 非复杂任务每步完成后 yield 话术（HMI 流式显示）；复杂任务逐步信息走过程区，
            # 气泡只留最终答案，避免与过程区重复刷屏。整步文本此处就位，直接完整剥 md。
            if (step_result.speech and step_result.status == StepStatus.OK
                    and not complex_task):
                yield {"kind": "speech",
                       "delta": strip_markdown_speech(step_result.speech) + "。"}

            # 挂起：需确认/需补槽。prior=本轮新完成步（种子是上轮已播报过的，切掉）；
            # 非复杂路径逐步 speech 已流出，但那只在单步计划成立（多步即 is_complex），
            # 单步无前序——无双重播报面。
            if (step_result.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT)
                    and suspend_at is None):
                # 多步同轮挂起时按**声明序**取第一条，读数才稳定（同 escalate 的口径）。
                suspend_at = step_result

        if suspend_at is not None:
            yield await self._suspend(suspend_at, results, plan, ctx,
                                      prior=results[len(seed_results):])
            return

        # 通用 escalate（一跳）：executor 路径——多步计划里第一个声明改派的步结果被
        # escalated 结果替换，其余步结果保留进聚合（每轮预算 1 跳，与 D0 路径共享同一机制）。
        esc_i = next((i for i, r in enumerate(results)
                      if self._parse_escalate(r) is not None), None)
        if esc_i is not None:
            esc = self._parse_escalate(results[esc_i])
            if isinstance(results[esc_i].data, dict):
                results[esc_i].data.pop("_escalate", None)
            sink: dict = {}
            async for ev in self._run_escalated(esc, ctx, agents, sink,
                                                prior=results[len(seed_results):]):
                yield ev
            if sink.get("suspended"):
                return
            if sink.get("results"):
                results[esc_i:esc_i + 1] = sink["results"]
            # 装配失败：保留原步结果（speech 为空），其余步正常聚合——多步场景不用兜底话术
            # 压掉别的步产出

        if new_plan and await self._needs_replan(plan, results):
            metrics.record_intent("reactive_upgrade", 0, True)
            logger.info("Reactive upgrade: simple→T2 for session %s",
                        ctx.session_id)
            if not agents:
                agents = await self.clients.list_agents()
            async for event in self.loop.run(
                    goal=plan.goal or text or plan.raw_text,
                    initial_plan=None,
                    agents=agents,
                    ctx=ctx,
                    user_text=text or plan.raw_text,
                    seed_results=results,
                    working_set=working_set,
                    show_process=show_process, thinking=complex_task):
                if event.get("kind") == "final":
                    await obs_events.get_emitter("cloud").emit_span(
                        ctx.trace_id,
                        "aggregate",
                        attrs={"path": "reactive"},
                    )
                yield event
            return

        # E. 聚合 + 输出
        await self._settle_session(ctx, held_pending)
        if mem_on:
            await self.context.update_focus(
                ctx.session_id, plan, results,
                user_id=ctx.user_id,
                exchange_id=ctx.request_id)  # 焦点态供下轮指代
        if show_process:
            yield self._progress("synthesize", "整理结果",
                                 summary="合并各步结果生成回复", status="start")
        final = await self.aggregator.compose(
            text or plan.raw_text, results, thinking=complex_task)
        self._append_pending_hint(final, held_pending)
        self._append_abandoned_hint(final, ctx.abandoned_pending_label)
        if getattr(plan, "emotion", ""):
            final["emotion"] = plan.emotion
        await obs_events.get_emitter("cloud").emit_span(
            ctx.trace_id,
            "aggregate",
        )
        yield {"kind": "final", **final}

    async def _pending_digest(self, ctx) -> list[dict] | None:
        """挂起表 → 读出口能念的最小形状 `[{"what","phase"}]`；读不到返回 **None**。

        取 `load_all` 而不是 `load`：用户问的是「还**有没有**」，只答最新那一条
        等于把「还有几条」答错——挂起表本来就是多条（Q1-C）。

        ⚠ **「读不到」与「读到了、是空的」必须分开报**（C2 第二次沉淀的那条）：
        两者都返回 `[]` 就会让一次 Redis 故障说出「当前没有待确认的操作」——
        一句听起来很确定的假话，而用户正是靠它决定要不要重说一遍。
        """
        try:
            entries = await self.session.load_all(
                ctx.session_id, owner_user_id=ctx.user_id)
        except Exception as e:
            logger.debug("pending digest unavailable: %s", e)
            return None
        out: list[dict] = []
        for state in entries or []:
            goal = ""
            try:
                goal = str((state.pending_plan or {}).get("goal") or "")
            except AttributeError:
                pass
            # 目标描述与 `_append_pending_hint` 同源同截断——同一条挂起在两处
            # 出现时必须是同一个称呼，否则用户会以为是两件事。
            out.append({"what": goal[:20], "phase": getattr(state, "phase", "")})
        return out

    async def _session_fact_answer(self, ctx, text: str,
                                   working_set) -> tuple[str, str] | None:
        """「系统持有的会话事实」三条确定性读出口 → `(出口名, 话术)`，或 None（不劫持）。

        判据与话术都在 `runtime.session_facts`（唯一实现，chitchat 兜底位共用同一份）；
        **这里只做取数**——挂起表在编排手里，账本在 `working_set.history` 里。
        零 LLM、零网络（`load_all` 读的是会话自己的挂起键）。

        求值序 = 判据窄的排前面：挂起（三段）→ 数据源（两段 + 有账才劫持）→
        执行史（两段）。三者判据面互不重叠，顺序只影响可读性不影响结果。
        """
        if session_facts.is_pending_question(text):
            digest = await self._pending_digest(ctx)
            if digest is None:
                return ("pending_state", "我这会儿查不到待确认列表，稍后再问我一次。")
            return ("pending_state", session_facts.pending_answer(digest))
        history = list(getattr(working_set, "history", None) or [])
        # **有账才劫持**：账本空说明这一轮之前没有任何外部数据卡，那就不是
        # 「系统持有的事实」，照常进 Planner——`info.stock` 那条域内直答
        # （重判 5：确定性面早就都在）比一句「我没记到」有用得多。
        # 这也是本族三条里唯一带这个条件的：挂起与执行史**没有第二个能答的人**。
        if (session_facts.is_provenance_question(text)
                and session_facts.latest_sources(history)):
            return ("data_provenance", session_facts.provenance_answer(history))
        if session_facts.is_execution_audit_question(text):
            return ("execution_audit", session_facts.audit_answer(
                history, with_time=session_facts.asks_when(text)))
        return None

    @staticmethod
    def _parse_escalate(result: StepResult) -> dict | None:
        """解析 Agent 结果里的通用改派声明 `data["_escalate"]={"intent","slots","reason"}`。

        非法（缺 intent / slots 非 dict）→ None（忽略，不炸主链）。协议登记见
        docs/conventions.md「Agent→编排结果保留键」。不剥离键——消费点自行 pop。"""
        data = getattr(result, "data", None)
        esc = data.get("_escalate") if isinstance(data, dict) else None
        if not isinstance(esc, dict):
            return None
        intent = esc.get("intent")
        if not isinstance(intent, str) or not intent.strip():
            return None
        raw_slots = esc.get("slots")
        slots = ({str(k): str(v) for k, v in raw_slots.items()}
                 if isinstance(raw_slots, dict) else {})
        return {"intent": intent.strip(), "slots": slots,
                "reason": str(esc.get("reason") or "")}

    async def _run_escalated(self, esc: dict, ctx: PlanContext, agents: list,
                             sink: dict,
                             prior: list[StepResult] | None = None) -> AsyncIterator[dict]:
        """执行通用 escalate 改派（每轮最多一跳）。

        目标 intent 在 agent 目录里找到承接方后经 `PlanBuilder._validated_steps` 装配成单步
        mini-plan 交 executor 执行——heavy/latency_budget/权限自动带出（**绝不裸 call_agent**：
        其默认 10s 超时会打死 info.search 这类 50s 预算的重域步）；heavy 步照常发过程区事件。
        过程区/挂起 final 事件原样透传；结果经 sink 回传：
          sink["results"] 完成的 StepResult 列表（已剥离二跳 _escalate——结构性防环）
          sink["plan"]    mini-plan（焦点态更新用）
          sink["suspended"]=True 已 yield 挂起 final（调用方直接 return）
        装配失败（intent 无承接 Agent / 校验不过）→ sink 留空，调用方自行兜底。"""
        if not agents:
            agents = await self.clients.list_agents()
        agent_map = {a.manifest.agent_id: a for a in agents}
        aid = next((a.manifest.agent_id for a in agents
                    if any(c.intent == esc["intent"]
                           for c in a.manifest.capabilities)), "")
        steps = self.planner._validated_steps([{
            "id": "esc1", "agent_id": aid, "intent": esc["intent"],
            "slots": esc["slots"], "depends_on": [], "slot_refs": {},
        }], agent_map) if aid else []
        if not steps:
            logger.warning("Escalate target intent %r has no serving agent; ignored",
                           esc["intent"])
            return
        mini = Plan(steps=steps, raw_text=ctx.raw_text)
        show_esc_process = is_complex(mini)
        if show_esc_process:
            for s in mini.steps:
                s.meta = {**s.meta, "thinking": "on"}
            yield self._progress("execute", phase_label(steps[0].intent),
                                 status="running", step_id=steps[0].id)
        await obs_events.get_emitter("cloud").emit_span(
            ctx.trace_id, "escalate",
            attrs={"intent": esc["intent"], "reason": esc.get("reason", "")})
        results: list[StepResult] = []
        async for sr in self.executor.run(mini, ctx):
            if isinstance(sr.data, dict):
                sr.data.pop("_escalate", None)   # 单跳预算：二跳声明不消费（结构性防环）
            results.append(sr)
            if sr.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                yield await self._suspend(sr, results, mini, ctx, prior=prior)
                sink["suspended"] = True
                return
            if show_esc_process and sr.status == StepStatus.OK:
                yield self._progress("execute", phase_label(steps[0].intent),
                                     summary=step_summary(steps[0], sr),
                                     status="done", step_id=steps[0].id)
        sink["results"] = results
        sink["plan"] = mini

    @staticmethod
    def _progress(phase: str, label: str, summary: str = "",
                  status: str = "done", step_id: str = "") -> dict:
        """构造过程区事件。内容仅来自脱敏的步骤语义/结果，绝不含 prompt/reasoning/参数。"""
        return {"kind": "progress", "phase": phase, "label": label,
                "summary": summary, "status": status, "step_id": step_id}

    @classmethod
    def _sanitize_resume_value(cls, value):
        if isinstance(value, dict):
            clean = {}
            for key, item in value.items():
                compact = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if (compact.endswith(("url", "uri"))
                        or any(part in compact
                               for part in _RESUME_SECRET_FRAGMENTS)):
                    continue
                sanitized = cls._sanitize_resume_value(item)
                if sanitized is not _RESUME_OMIT:
                    clean[str(key)] = sanitized
            return clean
        if isinstance(value, (list, tuple)):
            clean = []
            for item in value:
                sanitized = cls._sanitize_resume_value(item)
                if sanitized is not _RESUME_OMIT:
                    clean.append(sanitized)
            return clean
        if isinstance(value, str):
            if _RESUME_URI_PREFIX_RE.match(value.strip()):
                return _RESUME_OMIT
            scrubbed = _RESUME_URI_RE.sub("", value).strip()
            return scrubbed if scrubbed else _RESUME_OMIT
        return value

    @staticmethod
    def _resume_data_paths(plan: Plan) -> dict[str, list[tuple[str, ...]]]:
        """Return the exact producer data paths needed after resume.

        ``completed_results`` exists only to resolve a later step's declared
        ``slot_refs``.  Persisting any other provider payload turns a short
        confirmation window into an accidental response archive.  Paths are
        therefore derived from the plan rather than from provider field names.
        """
        paths: dict[str, list[tuple[str, ...]]] = {}
        for step in plan.steps:
            refs = [
                (str(slot_name), value)
                for slot_name, value in (step.slot_refs or {}).items()
                if isinstance(value, str)
            ]
            for slot_name, raw in (step.slots or {}).items():
                if not isinstance(raw, str):
                    continue
                match = re.fullmatch(r"\$\{([^{}]+)\}", raw.strip())
                if match:
                    refs.append((str(slot_name), match.group(1)))
            for slot_name, ref in refs:
                parts = tuple(ref.split("."))
                if (len(parts) < 3 or parts[1] != "data"
                        or any(not part for part in parts)):
                    continue
                # The plan is LLM-authored, so declaring a slot_ref is not an
                # authority to retain PII/payment material.  Known sensitive
                # leaves fail closed even when explicitly referenced; the
                # resumed step must re-query or ask again instead.
                compact_parts = [
                    re.sub(r"[^a-z0-9]", "", part.lower())
                    for part in (slot_name, *parts[2:])
                ]
                if any(
                    fragment in compact
                    for compact in compact_parts
                    for fragment in _RESUME_SECRET_FRAGMENTS
                ):
                    continue
                paths.setdefault(parts[0], []).append(parts[2:])
        return paths

    @classmethod
    def _project_resume_data(cls, data: dict,
                             paths: list[tuple[str, ...]]) -> dict:
        """Project provider data to scalar leaves named by ``slot_refs``."""
        trie: dict = {}
        leaf = "__resume_leaf__"
        for path in paths:
            node = trie
            for part in path:
                node = node.setdefault(part, {})
            node[leaf] = True

        def project(value, node):
            if node.get(leaf):
                # Slots cross the transport as strings.  A dict/list terminal
                # is therefore not a valid scalar dependency and could retain
                # an arbitrary provider response; fail closed.
                if isinstance(value, (dict, list, tuple)):
                    return _RESUME_OMIT
                return cls._sanitize_resume_value(value)
            if isinstance(value, dict):
                clean = {}
                for key, child in node.items():
                    if key == leaf or key not in value:
                        continue
                    selected = project(value[key], child)
                    if selected is not _RESUME_OMIT:
                        clean[key] = selected
                return clean if clean else _RESUME_OMIT
            if isinstance(value, (list, tuple)):
                selected_by_index = {}
                for raw_index, child in node.items():
                    if raw_index == leaf:
                        continue
                    try:
                        index = int(raw_index)
                    except (TypeError, ValueError):
                        continue
                    if index < 0 or index >= len(value):
                        continue
                    selected = project(value[index], child)
                    if selected is not _RESUME_OMIT:
                        selected_by_index[index] = selected
                if not selected_by_index:
                    return _RESUME_OMIT
                clean = [None] * (max(selected_by_index) + 1)
                for index, selected in selected_by_index.items():
                    clean[index] = selected
                return clean
            return _RESUME_OMIT

        projected = project(data if isinstance(data, dict) else {}, trie)
        return projected if isinstance(projected, dict) else {}

    @classmethod
    def _resume_result(cls, result: StepResult,
                       data_paths: list[tuple[str, ...]]) -> dict:
        data = cls._project_resume_data(result.data, data_paths)
        return {
            "step_id": result.step_id,
            "status": result.status.value,
            # Free-form provider speech/follow-up is already surfaced by the
            # suspension final.  It is not required for slot resolution and
            # can contain phone/email/address data, so it is never persisted.
            "data": data,
            "fingerprint": result.fingerprint,
            "source_intent": result.source_intent,
        }

    async def _suspend(self, step_result: StepResult, results: list[StepResult],
                       plan: Plan, ctx: PlanContext,
                       prior: list[StepResult] | None = None) -> dict:
        """挂起待确认/待补槽：保存会话态并构造 final 事件。executor 与流式两路共用，
        保证 F1 多轮确认闭环行为一致。

        prior=本轮**新完成且尚未播报**的步骤结果（旅程 A1-4）：多步/adaptive 计划里
        前序结论只存在于各步 speech（复杂任务不逐步流出、聚合器在挂起时不会跑），
        挂起 final 又会整体替换 HMI 气泡——不前缀简报，用户就会被凭空追问
        （「查到雨才建提醒」却没听到有雨）。调用方负责剔除确认续接种子与已流式
        播报的结果，防双重播报；挂起步自身不进前缀（trip 确认话术本就是完整叙述）。"""
        # The pending step is always re-run from ``pending_plan``.  Keeping its
        # full result would create a second persisted copy of merchant checkout
        # tokens, store/specification data and amounts in ``planner:sess:*``
        # without contributing to restore.  Persist only completed dependency
        # results.  A choice step keeps a value-free card marker solely so a
        # spoken ordinal ("第一个") is still recognised as a slot answer.
        resume_paths = self._resume_data_paths(plan)
        completed = {
            r.step_id: self._resume_result(
                r, resume_paths.get(r.step_id, []))
            for r in results
            if r.step_id != step_result.step_id
        }
        pending_card = step_result.ui_card if isinstance(step_result.ui_card, dict) else {}
        purpose = str(pending_card.get("purpose") or "")
        card_type = str(pending_card.get("type") or "")
        if (
            step_result.status == StepStatus.NEED_SLOT
            and (purpose.endswith("_choice") or card_type == "merchant_choices")
        ):
            completed[step_result.step_id] = {
                "step_id": step_result.step_id,
                "status": step_result.status.value,
                "ui_card": {
                    "type": card_type,
                    "purpose": purpose,
                    "choice_kind": str(pending_card.get("choice_kind") or ""),
                },
            }

        # Q1-B：每条挂起自带寻址键，随 final 下发给 HMI 并由确认帧原样回传。
        # 本轮续接上来的那条先关掉——补槽追问「再问一次」是同一件事的下一步，
        # 不该在挂起表里占两格（Q1-C）。
        if ctx.pending_operation_id:
            await self.session.clear(
                ctx.session_id, owner_user_id=ctx.user_id,
                operation_id=ctx.pending_operation_id)
            if ctx.pending_operation_id not in ctx.closed_operation_ids:
                ctx.closed_operation_ids.append(ctx.pending_operation_id)
            ctx.pending_operation_id = ""
        operation_id = f"op-{uuid.uuid4().hex[:16]}"
        # C3-A：把**待补那几个槽**的值形状抄进挂起态——续接轮据此判「这句话长得
        # 像不像这个槽的值」。只抄待补的那几个：形状要回答的是「当时问的是什么」。
        pending_step = next(
            (item for item in plan.steps if item.id == step_result.step_id), None)
        declared_shapes = dict(getattr(pending_step, "slot_shapes", None) or {})
        slot_shapes = {name: declared_shapes[name]
                       for name in (step_result.missing_slots or [])
                       if name in declared_shapes}
        # C3-D：同一步、同一组待补槽又问了一遍 ⇒ 原地打转，计数 +1；
        # 换了槽（「先问门店再问餐品」）或换了步都是**进展**，归零。
        probe = ctx.pending_slot_probe or {}
        repeated = (
            step_result.status == StepStatus.NEED_SLOT
            and probe.get("step_id") == step_result.step_id
            and probe.get("missing") == sorted(step_result.missing_slots or []))
        saved, evicted = await self.session.save_pending(
            ctx.session_id, SessionState(
                phase=("wait_confirm"
                       if step_result.status == StepStatus.NEED_CONFIRM
                       else "wait_slot"),
                owner_user_id=ctx.user_id,
                operation_id=operation_id,
                pending_step_id=step_result.step_id,
                missing_slots=list(step_result.missing_slots),  # F12：保存缺失槽位名
                slot_shapes=slot_shapes,
                slot_retry=(int(probe.get("retry") or 0) + 1) if repeated else 0,
                completed_results=completed,
                pending_plan=self._serialize_plan(plan),
            ))
        if saved is False:
            # A concurrent privacy deletion owns the write fence.  Do not show
            # a confirmation UI for state that cannot be resumed safely.
            return {
                "kind": "final",
                "speech": "正在清除你的数据，这次操作没有保存，请稍后重新发起。",
                "follow_up": "数据清除完成后可以重新尝试。",
                "actions": [],
                "ui_card": None,
                "need_confirm": False,
            }
        await obs_events.get_emitter("cloud").emit_span(
            ctx.trace_id,
            "suspended",
            status=step_result.status.value,
            attrs={"step_id": step_result.step_id},
        )
        brief = self._prior_brief(prior or [], step_result)
        follow_up = step_result.follow_up
        if evicted is not None:
            # Q1-C：淘汰**必须有话术**。静默丢弃就是 B3 那条「认不出就用默认值」
            # 的确认版——用户以为那件事还在等他，其实系统早就忘了。
            ctx.closed_operation_ids.append(evicted.operation_id)
            follow_up = self._append_hint(
                follow_up, f"（{self._pending_label(evicted)}已过期，需要的话再说一次。）")
        # C5-B：兄弟步的动作也要发出去。挂起 final 此前只带挂起那一步的 actions
        # ——而那些步**已经执行过了**（结果就在 results 里），把动作扣下就变成
        # 「话说了、事没做」。合并走聚合器同一份 `compose_actions`（navigate 去重 +
        # 充电途经点注入），不在这里写第二份合并语义。
        actions = self.aggregator.compose_actions(
            [r for r in (prior or []) if r.status == StepStatus.OK] + [step_result])
        return {
            "kind": "final",
            "speech": (brief + (step_result.speech or "")) if brief else step_result.speech,
            "follow_up": follow_up,
            "actions": actions,
            "ui_card": step_result.ui_card,
            "need_confirm": step_result.status == StepStatus.NEED_CONFIRM,
            "operation_id": operation_id,
        }

    @staticmethod
    def _pending_label(state) -> str:
        """挂起的人话名字：取 pending_plan.goal，没有就退回中性说法。"""
        goal = ""
        try:
            goal = (state.pending_plan or {}).get("goal") or ""
        except AttributeError:
            pass
        return f"「{goal[:20]}」" if goal else "更早那条待确认的操作"

    @staticmethod
    def _append_hint(follow_up: str | None, hint: str) -> str:
        base = str(follow_up or "")
        return (base + (" " if base else "") + hint).strip()

    @staticmethod
    def _prior_brief(prior: list[StepResult], step_result: StepResult) -> str:
        """挂起前缀：前序已完成步的脱敏简报（安全计数/首句，同过程区口径）。

        身份比较（is）而非 step_id——T2 各轮 replan 的步 id 可能撞名；短回执
        （「好的」类）不值一播，滤掉。"""
        parts = []
        for r in prior:
            if r is step_result or r.status != StepStatus.OK:
                continue
            s = strip_markdown_speech(result_summary(r)).strip().rstrip("。！？!?；;，,")
            if len(s) >= 4:
                parts.append(s)
        return "；".join(parts) + "。" if parts else ""

    async def _close_pending(self, ctx: PlanContext, pending) -> None:
        """关掉一条挂起，并记进本轮的 `closed_operation_ids`（Q1-C）。

        **只清这一条**：挂起表里其余的与本轮无关，清掉它们等于把用户还惦记着的
        另一件事悄悄抹掉——正是单槽时代那个语义（`_suspend` 覆盖旧挂起）的换皮。
        """
        if pending is None:
            return
        op = getattr(pending, "operation_id", "") or ""
        await self.session.clear(
            ctx.session_id, owner_user_id=ctx.user_id, operation_id=op)
        if op and op not in ctx.closed_operation_ids:
            ctx.closed_operation_ids.append(op)

    async def _settle_session(self, ctx: PlanContext, held_pending) -> None:
        """本轮正常收口时的会话清理（R2）：插话轮（held_pending 非空）**不清挂起**——
        用户 TTL 内回头「确认」/裸答案仍可续接。

        Q1-C 后清理范围收窄成**本轮真正续接上的那一条**（`ctx.pending_operation_id`）：
        以前这里 `clear()` 清的是整个 key，多槽下会连带抹掉两件不相干的挂起。
        不刷新 TTL：挂起窗口以首次挂起时刻起算，插话不无限续命。"""
        if held_pending is None and ctx.pending_operation_id:
            await self.session.clear(
                ctx.session_id, owner_user_id=ctx.user_id,
                operation_id=ctx.pending_operation_id)
            if ctx.pending_operation_id not in ctx.closed_operation_ids:
                ctx.closed_operation_ids.append(ctx.pending_operation_id)

    @staticmethod
    def _append_abandoned_hint(final: dict, label: str) -> None:
        """C3-D：本轮放弃了一条问不动的挂起，**必须说一句**。原地改 final。

        静默丢弃就是 Q1-C 那条「淘汰必须有话术」的同一件事：用户以为那件事
        还在等他，其实系统早就忘了。"""
        if not label or not isinstance(final, dict):
            return
        final["follow_up"] = PlannerEngine._append_hint(
            final.get("follow_up"),
            f"（{label}问了几次都没接上，先放一放；需要的话重新说一次。）")

    @staticmethod
    def _append_pending_hint(final: dict, held_pending) -> None:
        """插话轮的 final 补软提醒：告知挂起还在（Q1 决策的配套——插话后 HMI 确认条
        已被新消息顶掉，不提示的话用户忘了挂起、说「确认」会显得凭空执行）。原地改 final。"""
        if held_pending is None or not isinstance(final, dict):
            return
        goal = ""
        try:
            goal = (held_pending.pending_plan or {}).get("goal") or ""
        except AttributeError:
            pass
        what = f"「{goal[:20]}」" if goal else "刚才的操作"
        ask = "确认" if held_pending.phase == "wait_confirm" else "继续补充"
        hint = f"对了，{what}还在等你{ask}。"
        follow = str(final.get("follow_up") or "")
        final["follow_up"] = (follow + (" " if follow else "") + hint).strip()

    # 对话落库(append_turn)/历史·记忆召回(_history/_recall)/上下文构建(build_context)
    # 均已迁入 context.py（ContextManager + 模块级 build_context），统一上下文生命周期。

    @staticmethod
    def _build_context(request) -> PlanContext:
        """委托 context.build_context（保留方法名供既有测试 engine._build_context 直接调用）。"""
        return build_context(request)

    @staticmethod
    def _confirm_reply(text: str, flagged: bool) -> str | None:
        """判定本轮是否在回应待确认任务。返回 "yes" | "no" | None（答非所问）。

        否定词优先（"确认取消"按取消处理）；HMI 按钮带显式标记即肯定；
        语音兜底只认短肯定话术，避免长句误判成确认。

        ⚠ 否定侧**只调 `pending_cancel`，不留第二份词表**（Q1-A）。挂起语境里
        真正生效的是 `_orchestrate` 里那次共用判定，这里的 "no" 只服务两个残留
        消费方：无挂起时的 `_is_bare_confirm_word`，以及历史上直接调本函数的测试。
        """
        t = (text or "").strip().lower()
        if is_standalone_cancel(t):
            return "no"
        if flagged:
            return "yes"
        # "词占据整句"判定：肯定词须近似为全句（len(t) ≤ 词长+slack），不做宽松子串包含。
        # 否则"第二天行程换一个"含"行"、"可以换X"含"可以"会被误判。
        if any(k in t and len(t) <= len(k) + 2 for k in _YES_WORDS):
            return "yes"
        return None

    @staticmethod
    def _is_bare_confirm_word(text: str) -> bool:
        """文本是否就是一句裸"确认/取消"（判定与语音兜底 _confirm_reply 完全一致）。

        无挂起任务时用于拦截：绝不能把裸"确认"交给 Planner——否则它会借对话历史把
        "确认"重规划成上一意图的重复执行（如反复 trip.modify），表现为"确认后又改一遍
        并再次要确认"的死循环。挂起任务丢失（TTL 过期/上一步异常/重复点击）时优雅兜底。

        ⚠ 这条**必须保持严格**（只认整句）：放宽了「取消当前导航」会被答成
        「当前没有待确认的操作」而不是去规划——与 Q4 位置闸同款的「前置闸替编排
        做意图判定」。它与挂起语境的宽判据同源一份词表，语境规则不同。"""
        return PlannerEngine._confirm_reply(text, False) is not None

    @staticmethod
    def _is_cancel_index_answer(text: str, pending: SessionState | None) -> bool:
        """Whether ``取消第一条`` answers an active ``*.cancel`` index prompt.

        The leading verb is normally a request to close the suspended operation.
        Once that same operation explicitly asks for an ``index``, however, the
        ordinal is the business slot answer.  Scope this exception to the pending
        cancel step and an exact ordinal shape so ordinary ``取消`` keeps its global
        fail-safe meaning.
        """
        if pending is None or "index" not in (pending.missing_slots or []):
            return False
        steps = (pending.pending_plan or {}).get("steps") or []
        step = next(
            (item for item in steps
             if str(item.get("id") or "") == pending.pending_step_id),
            {},
        )
        intent = str(step.get("intent") or "")
        if not intent.endswith(".cancel"):
            return False
        return bool(re.fullmatch(
            r"(?:取消|删除|删掉)\s*第[一二三四五六七八九十\d]+条(?:提醒|待办)?",
            str(text or "").strip(),
        ))

    @staticmethod
    def _is_topic_change(text: str, pending: SessionState | None = None) -> bool:
        """判定 wait_slot 状态下用户是否换了话题（答非所问）。

        典型场景：Agent 追问"您要去哪里？"，用户回答"讲个笑话"——这不是在补槽。
        判断方式：①文本以"动作动词"开头（讲/播/打开/关闭/搜/查…）→ 新意图；
        ②疑问/回忆式（什么来着/……吗/？）→ 新意图——问题不是槽位答案（旅程 B5-1：
        R2 保留挂起后「我刚才让你提醒我什么来着」被当 time_text 吃掉，挂起成黑洞）。
        否则视为槽位补充。
        """
        t = (text or "").strip()
        if not t:
            return False
        # C3-A **方向反转**：先问「这句话长得像不像这个槽的值」。形状由 capability
        # 声明（`slot_shapes`），判据本体是 `slot_shape.py` 的唯一实现（零领域词）。
        # 三值：不像=换题、定案=槽值、None=形状没意见，继续走下面的通用判据。
        # `order_id` 那条写路径身份闸是本机制的先例，已整体收编进形状表——
        # 它此前是这里唯一的硬编码，也是唯一把方向做对了的那一条。
        shaped = slot_shape.verdict(
            getattr(pending, "missing_slots", None) or [], t,
            getattr(pending, "slot_shapes", None))
        if shaped is not None:
            return shaped
        # 裸序号是对最近列表/候选的选择，不是任意历史 NEED_SLOT 的自然语言答案。
        # 旧挂起若抢占“第二个”，会把咖啡候选选择错误填进数轮前的 route 槽。
        # 唯一例外是挂起步骤自身刚给出了 *_choice 选择卡：此时序号正是该槽位的
        # 合法答案（B2-3：充电 dest_choice → 插问时间 → “第一个”）。
        if re.fullmatch(
            r"(?:第[一二三四五六七八九十\d]+(?:个|家|项|条|种)?|"
            r"[一二三四五六七八九十\d]+号(?:方案|选项|路线|店)?)",
            t,
        ):
            current = (
                (pending.completed_results or {}).get(pending.pending_step_id, {})
                if pending is not None
                else {}
            )
            card = current.get("ui_card") if isinstance(current, dict) else None
            purpose = card.get("purpose", "") if isinstance(card, dict) else ""
            card_type = card.get("type", "") if isinstance(card, dict) else ""
            if (
                isinstance(purpose, str) and purpose.endswith("_choice")
            ) or card_type == "merchant_choices":
                return False
            return True
        # 条件式提醒常把触发条件放在句首，动作词位于中后部；仍是完整新意图。
        if any(k in t for k in ("提醒我", "叫我", "通知我", "别忘了")):
            return True
        # 疑问/回忆式不是槽位答案。「有什么/哪些」是句中问式（demo-3ukshz 探针实证：
        # 麦当劳选店挂起把「附近的瑞幸有什么可以点的」当 store_hint 吃掉——尾字「的」
        # 躲过了旧的句尾判据）。
        if any(k in t for k in
               ("什么来着", "来着", "有什么", "有哪些", "哪些", "哪个")) \
                or t.endswith(("吗", "？", "?", "呢")):
            return True
        # C3-B：**「这是一次新检索」的词表只许有一份**——直接消费 `candidate_query`
        # 的那一份。此前它只在候选集聚合那一侧生效，于是「附近的川菜馆」在那边判成
        # 新检索、在这边被当成 `item_query` 整句吞掉（真栈 T45）。同一判据两份实现
        # 各自演化，正是 B1 那个 bug 的成因原型。
        if candidate_query.NEW_SEARCH_RE.search(t):
            return True
        if any(k in t for k in ("为什么", "为何", "什么原因")):
            return True
        # 「动词+数量+量词+宾语」是完整新指令（在X点一杯标准美式/来两份炒饭）——
        # 槽位答案是名词短语，不自带量词结构（同一次探针：整句新单被旧挂起吞掉）。
        # 量词后要求 ≥2 字宾语：裸「要两杯」仍是数量补槽的合法答案，不算换话题。
        if re.search(r"[点来买订][一二两三四五六七八九十\d]+"
                     r"[杯份个只碗盒瓶串支].{2,}", t):
            return True
        # 完整的新搜索请求可能以行程状语开头（“路上帮我找…”），不能被旧 wait_slot
        # 当成 route 等槽位答案吞掉。这里只识别“途中语境 + 找/搜”组合，避免把
        # “路上经过深南大道”这类真实路线答案误判为换话题。
        if re.search(r"(?:路上|途中|沿途|顺路).{0,8}(?:帮我)?(?:找|搜)", t):
            return True
        # 以动作动词开头 → 大概率是新意图（不是在回答补槽追问）
        _verbs = (
            "讲", "说", "播放", "暂停", "打开", "关闭", "关掉",
            "调高", "调低", "搜", "查", "订", "预订", "帮我",
            "导航", "带我去", "回家", "回公司", "回学校",
            "今天", "现在", "最近", "有没有", "怎么样", "多少",
        )
        return any(t.startswith(v) for v in _verbs)

    @staticmethod
    def _slot_answer(slot_name: str, text: str) -> str:
        """把追问回答归一成真正的槽值；普通自由文本槽保持原样。"""
        value = str(text or "").strip()
        if slot_name != "order_id":
            return value
        labelled = re.fullmatch(
            r"(?:订单号|单号)\s*(?:是|为|[:：])?\s*"
            r"([0-9A-Za-z][0-9A-Za-z_-]{2,63})",
            value,
            flags=re.IGNORECASE,
        )
        return labelled.group(1) if labelled else value

    @staticmethod
    def _apply_focus_meta(plan: Plan, focus) -> None:
        """把地图已解析的目的地焦点确定性下发给 location Agent。

        焦点原本只进 Planner prompt；弱模型忽略“那边”时，天气 Agent 会退回浏览器当前位置。
        坐标属于敏感 location 上下文，因此这里只向 manifest 已声明 location scope 的步骤注入，
        不广播给闲聊等无关 Agent。Agent 仍须按原话是否含地点指代决定是否消费。
        """
        if not focus:
            # 首轮也要收敛对象化槽；否则 Agent 虽能答对，焦点抽取却可能记不住城市。
            for step in plan.steps:
                if step.intent in WEATHER_CONTEXT_INTENTS:
                    city = normalize_weather_city_slot(
                        (step.slots or {}).get("city"))
                    if city:
                        step.slots["city"] = city
            return
        # MiniMax 偶发把 weather ``city`` 填成序列化对象。只有上一轮本身也是
        # 天气域时，缺槽或对象化槽才表示同域续接并可复用城市；跨过闲聊/导航等
        # 无关轮次后，旧城市已经失去指代资格，必须让 Agent 回到本轮显式位置/GPS。
        # 普通标量始终保持不动。
        if (getattr(focus, "last_city", "")
                and getattr(focus, "last_intent", "") in WEATHER_CONTEXT_INTENTS):
            for step in plan.steps:
                if step.intent not in WEATHER_CONTEXT_INTENTS:
                    continue
                raw_city = (step.slots or {}).get("city")
                malformed = isinstance(raw_city, dict) or (
                    isinstance(raw_city, str) and raw_city.strip().startswith("{"))
                if malformed:
                    payload = raw_city if isinstance(raw_city, dict) else {}
                    if not payload:
                        try:
                            decoded = json.loads(raw_city)
                            payload = decoded if isinstance(decoded, dict) else {}
                        except (TypeError, ValueError, json.JSONDecodeError):
                            payload = {}
                    candidate = normalize_weather_city_slot(raw_city)
                    road = str(payload.get("road") or "").strip()
                    raw_text = str(getattr(plan, "raw_text", "") or "")
                    explicitly_named = bool(
                        candidate and candidate in raw_text
                        and not (road and road in raw_text and candidate in road)
                    )
                    step.slots["city"] = (
                        candidate if explicitly_named else str(focus.last_city)
                    )
                elif not raw_city:
                    step.slots["city"] = str(focus.last_city)
        # 股票焦点的跨轮继承默认要求**上一轮本身就是股票轮**——跨过无关轮次后旧标的
        # 已经失去指代资格（「现在什么行情值得关注」不该被塞进三轮前那只票）。
        # C4（2026-08-28）：**原话带回顾指代时这条限制让路**。真栈 T44
        # 「只总结**刚才查到的**行情，不做投资建议」被中间那句「不要把生活指数当成
        # 股票指数」隔开 ⇒ `last_intent` 变成 chitchat ⇒ 不继承 ⇒ 反问「您想查询
        # 哪只股票或指数？」——**用户明确指了回去，系统却说不知道指的是谁。**
        # 判据是**形态**（回顾指代词，零领域词），且与 `session_facts` 的审计闸
        # 共用同一张词表：两处各写一份就会长出「这边认得那边不认得」的分歧。
        if (getattr(focus, "last_stock_symbol", "")
                and (getattr(focus, "last_intent", "") == "info.stock"
                     or session_facts.refers_to_an_earlier_turn(
                         getattr(plan, "raw_text", "") or ""))):
            for step in plan.steps:
                if step.intent == "info.stock" and not (step.slots or {}).get("symbol"):
                    step.slots["symbol"] = str(focus.last_stock_symbol)
        # 顺路停靠/“第二个”候选选择轮里，Planner 可能只给 stop_category/waypoint，
        # 省略已经确立的目的地。destination 是系统焦点中的事实，确定性补齐比让 LLM
        # 重猜安全；仅限这两类续接计划，绝不覆盖用户本轮显式目的地。
        if focus.last_destination:
            for step in plan.steps:
                if (
                    step.intent == "navigation.navigate_to"
                    and (step.slots.get("waypoint") or step.slots.get("stop_category"))
                    and not step.slots.get("destination")
                ):
                    step.slots["destination"] = str(focus.last_destination)

        # 最新列表后的指代详情（B5-2：「附近火锅」→「附近充电站」→「看第一个详情」）。
        # Planner 已正确落 nearby.detail，但弱模型常不产 name；焦点里的 last_poi 是上一轮
        # **最新成功列表**首项，属于系统持有的结构化事实，应确定性回填而不是再让 LLM 猜。
        # 用户明确给出的 id/name 永远优先，避免覆盖「看麦当劳详情」这类本轮实体。
        if focus.last_poi:
            for step in plan.steps:
                slots = step.slots or {}
                if (
                    step.intent == "nearby.detail"
                    and not any(slots.get(k) for k in
                                ("poi_id", "id", "name", "restaurant_name"))
                ):
                    slots["name"] = str(focus.last_poi)
                    step.slots = slots

        # G8 路线会话下发：与 focus_destination_* 同一门控通道（只注给声明 location
        # context_scope 的步；LLM 与客户端都写不到 step.meta），navigation.reroute
        # 在 Agent 侧从这份 JSON 确定性读活动路线——坐标不经 prompt。
        route = getattr(focus, "active_route", None) or {}
        if route.get("destination"):
            route_meta = {"focus_active_route": json.dumps(route, ensure_ascii=False)}
            for step in plan.steps:
                if "location" in (step.context_scopes or []):
                    step.meta = {**step.meta, **route_meta}

        # Q10 第 7 步：**候选集下发面**。与 focus_active_route 同一门控通道
        # （只注给 manifest 声明了 `candidates` context_scope 的步；LLM 与客户端
        # 都写不到 step.meta），消费方在 Agent 侧做确定性匹配——「第一杯」「巨无霸」
        # 由此解析成**按钮送出的那个规范名**，两条入口收敛到同一条解析链。
        #
        # ⚠ 这条通道 Q2 残余批**刻意没建**（history §58.6）：那批的消费方落在云侧
        # 短路里、不依赖下发，而 B4 判据是「无消费方的声明只会漂移」。本步才是它
        # 真正的消费方，所以到这里才落。
        #
        # 挂在 `_apply_focus_meta` 而不是 executor：三条执行路径里 D0 流式直通
        # 走的是 `call_agent_stream(..., step.meta)` 且 `context_scopes=None`
        # （`_merge_meta` 那条最小化在这条路上整个不生效）——写在 step.meta 上是
        # **唯一在全部路径上都成立**的做法。「新增挂点必须枚举全部执行路径」，
        # 本项目已经栽过三次。
        #
        # ⚠ **逐步选组，不是全局取最新**（I-030 段 A，2026-08-22）。此前一律下发
        # 最新那一组，于是「先看瑞幸菜单、再说在麦当劳点第一个」时 `mcd.order`
        # 那步拿到的是 `source_intent=luckin.menu`——桥侧按域前缀拒收
        # （**那一侧是 fail-safe 的，没翻错**），但麦当劳那组明明还在焦点里，
        # 用户的「第一个」就这么白丢了。判据是**结构的、零领域词**：
        # 步的 intent 域 == 组的 `source_intent` 域。
        for step in plan.steps:
            if "candidates" not in (step.context_scopes or []):
                continue
            candidates = candidate_downlink(candidate_set_for(
                focus, (step.intent or "").split(".", 1)[0]))
            if candidates:
                step.meta = {**step.meta, "focus_candidate_set": json.dumps(
                    candidates, ensure_ascii=False)}

        # C12-B 会话偏好约束下发：与候选集同一条门控通道（manifest 声明
        # `context_scopes: [session_constraints]` 的步才收得到）。
        # **不广播**——它与安全告警的取舍相反：告警是所有域都必须服从的约束，
        # 而忌口是个人数据，给不消费它的 Agent 只是多一处扩散面（最小化下发）。
        constraints = getattr(focus, "session_constraints", None) or {}
        if constraints:
            constraint_meta = {"focus_session_constraints": json.dumps(
                constraints, ensure_ascii=False)}
            for step in plan.steps:
                if "session_constraints" in (step.context_scopes or []):
                    step.meta = {**step.meta, **constraint_meta}

        # Q9 安全告警下发：**不按 scope 门控，广播给所有步**。
        # 与上面那条坐标下发的取舍正相反，理由也正相反：坐标是敏感数据，给多了是泄漏；
        # 安全告警不是数据是**约束**，给少了才是事故——QA 轮 SF3 实测，红色机油灯之后
        # 一句「现在在高速还能继续开吗」被 road-safety 的 `_general_advice` 按天气答成
        # 「天气状况良好，适合出行」，正因为那个分支根本不知道有告警。
        # 最该知道的恰恰是闲聊兜底那一类（它答的是「不提醒也不停车」）。
        alert = getattr(focus, "safety_alert", None) or {}
        if safety_alert_active(alert):
            alert_meta = {"focus_safety_alert": json.dumps(alert, ensure_ascii=False)}
            for step in plan.steps:
                step.meta = {**step.meta, **alert_meta}

        if focus.destination_lat is None or focus.destination_lng is None:
            return
        meta = {
            "focus_destination": str(focus.last_destination or focus.last_poi or ""),
            "focus_destination_lat": str(focus.destination_lat),
            "focus_destination_lng": str(focus.destination_lng),
        }
        for step in plan.steps:
            if "location" in (step.context_scopes or []):
                step.meta = {**step.meta, **meta}

    def _restore(self, state: SessionState, *,
                 inject_confirmed: bool) -> tuple[Plan | None, list[StepResult]]:
        """从挂起态恢复计划与已完成结果。

        挂起步骤本身（NEED_CONFIRM/NEED_SLOT 那条）不进种子——它要重跑；
        confirmed 只注入挂起那一步，不污染后续 require_confirm 步骤。

        **inject_confirmed 只有 wait_confirm 恢复（用户明确说了「确认」）才为 True。**
        wait_slot 恢复必须为 False——补槽答案（「拿铁」）不是确认；若这里也注入，
        require_confirm 步会在用户从未见过金额/后果的情况下直接执行（验收抓到的 P0：
        「下单一杯咖啡」→「要点什么？」→「拿铁」→ 无确认直接下单）。补槽重跑后该步
        照常返回 NEED_CONFIRM，走第二次挂起等真正的确认。
        """
        try:
            steps = [Step(**s) for s in state.pending_plan.get("steps", [])]
            if not steps:
                return None, []

            if inject_confirmed:
                for s in steps:
                    if s.id == state.pending_step_id:
                        s.meta = {**s.meta, "confirmed": "true"}

            # Keep the long-standing unbound-call compatibility used by small
            # contract tests and migration helpers (``_restore(None, ...)``).
            resume_paths = PlannerEngine._resume_data_paths(Plan(steps=steps))
            seeds: list[StepResult] = []
            for sid, d in (state.completed_results or {}).items():
                if sid == state.pending_step_id:
                    continue
                legacy = dict(d)
                # Read-time minimization is required as well: a rolling deploy
                # can encounter pending records written by the previous code.
                # Do not let legacy speech/cards/actions or full provider data
                # re-enter the execution/aggregation path.
                d = {
                    "step_id": str(legacy.get("step_id") or sid),
                    "status": legacy.get("status", "ok"),
                    "data": PlannerEngine._project_resume_data(
                        legacy.get("data") or {},
                        resume_paths.get(str(sid), []),
                    ),
                    "fingerprint": str(legacy.get("fingerprint") or ""),
                    "source_intent": str(legacy.get("source_intent") or ""),
                }
                d["status"] = StepStatus(d.get("status", "ok"))
                if d["status"] in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                    continue
                # 恢复种子只用于依赖解析、防重与最终话术/动作合成；它的
                # 卡片属于上一轮。挂起 final 当时已由 pending step 的确认/补槽卡
                # 整体替换，依赖生产者的发现列表既不是本轮新结果，也从未作为
                # 挂起卡展示。若让它继续进 Aggregator，display_priority=1 会压住
                # 确认后新产出的 payment_qr/mcp_order，造成「业务成功但 HMI 倒退」。
                # 因此只保留精确 slot_refs 投影、来源与防抖指纹；自由文本、动作和
                # 旧卡片一律不恢复。
                seeds.append(StepResult(**d))

            restored = Plan(
                steps=steps,
                raw_text=state.pending_plan.get("raw_text", ""),
                complexity=state.pending_plan.get("complexity", "simple"),
                goal=state.pending_plan.get("goal", ""),
            )
            restored.skills = list(state.pending_plan.get("skills") or [])
            restored.skill_effects = list(state.pending_plan.get("skill_effects") or [])
            restored.exemplars = list(state.pending_plan.get("exemplars") or [])
            return restored, seeds
        except Exception as e:
            logger.warning("Failed to restore plan: %s", e)
            return None, []

    async def _resolve_endpoints(self, plan: Plan):
        """为 plan 中没有 endpoint 的 step 解析 endpoint。"""
        for step in plan.steps:
            if step.endpoint:
                continue
            try:
                agents = await self.clients.resolve(query=step.intent, top_k=1)
                if agents:
                    resolved = agents[0]
                    step.endpoint = resolved.endpoint
                    manifest = resolved.manifest
                    step.kind = getattr(manifest, "kind", "") or step.kind
                    step.deployment = (
                        getattr(manifest, "deployment", "") or step.deployment)
                    step.required_permissions = list(
                        getattr(manifest, "requires_permissions", []) or
                        step.required_permissions)
                    step.trust_level = (
                        getattr(manifest, "trust_level", "") or step.trust_level)
                    step.context_scopes = list(
                        getattr(manifest, "context_scopes", []) or step.context_scopes)
                else:
                    logger.warning("No agent found for intent %s", step.intent)
            except Exception as e:
                logger.warning("Resolve failed for %s: %s", step.intent, e)

    @staticmethod
    def _serialize_plan(plan: Plan) -> dict:
        # meta 故意不持久化：confirmed 标记只在确认那一轮由 _restore 注入，防止重放
        return {
            "steps": [
                {"id": s.id, "agent_id": s.agent_id, "endpoint": s.endpoint,
                 "kind": s.kind, "deployment": s.deployment,
                 "intent": s.intent, "slots": s.slots, "depends_on": s.depends_on,
                 "slot_refs": s.slot_refs, "require_confirm": s.require_confirm,
                 "latency_budget_ms": s.latency_budget_ms,
                 "required_permissions": s.required_permissions,
                 "trust_level": s.trust_level,
                 "context_scopes": s.context_scopes,
                 # M2 Verifier：确认后重跑的正是最该对账的车控步——挂起态不带上它，
                 # 「用户确认→执行→没生效」这条最危险的路径反而不验（纯 dict，JSON 安全）
                 "verification": s.verification}
                for s in plan.steps
            ],
            "raw_text": plan.raw_text,
            "complexity": plan.complexity,
            "goal": plan.goal,
            # T2 知识继承跨挂起（2026-07-27 评审二批）：不存 skills 的话，补槽/确认恢复后
            # 的再规划会丢初规划注入的规划知识（replan 按 plan.skills 重渲染，见 loop.py）
            "skills": list(plan.skills or []),
            "skill_effects": list(getattr(plan, "skill_effects", []) or []),
            "exemplars": list(getattr(plan, "exemplars", []) or []),   # 同款（M5 P1）
        }

    async def _needs_replan(self, plan: Plan, results: list[StepResult]) -> bool:
        if any(result.data.get("replan") is True for result in results):
            return True
        steps = {step.id: step for step in plan.steps}
        for result in results:
            if result.status != StepStatus.FAILED:
                continue
            step = steps.get(result.step_id)
            if not step:
                continue
            try:
                alternatives = await self.clients.resolve(
                    intent=step.intent, top_k=2)
            except Exception:
                continue
            if len(alternatives) > 1:
                return True
        return False
