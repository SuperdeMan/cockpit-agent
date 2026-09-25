"""PlannerEngine：编排主循环（规划→校验→执行→聚合）。

WS3 §3。串联 planning / executor / aggregator / session。
多轮确认闭环（F1）：NEED_CONFIRM 挂起后，确认轮只重跑挂起步骤（已完成结果种子化），
且 confirmed 标记严格限定在挂起那一步——后续 require_confirm 步骤各自再走确认（架构 §9.1）。
"""
from __future__ import annotations
import asyncio
import dataclasses
import json
import logging
import os
import re
import time
import uuid
from types import SimpleNamespace
from typing import AsyncIterator

from .models import (Plan, Step, StepResult, StepStatus, PlanContext, SessionState,
                     step_call_context, step_record)
from .planning import PlanBuilder, clarify_is_progress, is_voice_input_source
from .executor import DagExecutor
from .aggregator import Aggregator, MdDeltaSoftener, strip_markdown_speech
from .session import (
    CLEAR_UNAVAILABLE, PENDING_UNAVAILABLE, SAVE_FENCED, SAVE_OK, SessionStore)
from .loop import LoopController
from .stream_state import (
    StreamTracker, allow_unary_fallback, emitted_anything, outcome_uncertain,
)
from .pending_cancel import detect_cancel, is_standalone_cancel
from .clients import set_llm_pin
from . import candidate_query
from .reply_position import reply_position
from .admission import judge_addressed
from .superseded import rewrite_goal, superseded_values
from . import slot_shape
from runtime import memory_read, session_facts
from runtime.execution_claim import (
    CLAIM_STRIPPED_SPEECH, ExecutionClaimGate, execution_claim, strip_execution_claims)
from runtime.affirmation import ACK_WORDS, PARTICLE_RE, consists_of, is_bare_acknowledgment
from runtime.memory_directive import is_memory_directive
from runtime.clause_split import split_clauses
from runtime.cntime import CN_NUM_CHARS, cn_int
from runtime.outcome import category_of, outcome_of_results
from runtime.polarity import is_negated_directive
from runtime.question_shape import is_imperative_opening, is_non_directive_question
from runtime.safety_signal import alert_level, alert_resolved, driver_state
from runtime.session_constraints import (constraint_recall_answer, constraints_in,
                                         describe_constraints,
                                         is_constraint_recall_question,
                                         is_pure_constraint_statement,
                                         merge_constraints, phrase_of)
from .context import (ContextManager, active_task_live, build_context, candidate_downlink,
                      candidate_set_for,
                      references_a_candidate, resolve_candidate_scope,
                      retired_candidate_hit, safety_alert_active,
                      WEATHER_CONTEXT_INTENTS, normalize_weather_city_slot,
                      _POC_DEFAULT_SCOPES)
from . import contracts
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
#: 流式只流了话术、final 却没到时的收口话术（D0 与 escalate 改派共用一句；自进化的兜底话术模式认它，别改字）
_STREAM_LOST_FINAL_SPEECH = "抱歉，刚才没说完，请再试一次。"


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
# 追加批 I（2026-09-24）：应答词与语气面下沉到 `runtime.affirmation`（planner 出口的纯应答写步闸读同一份）；
# 这里只加确认 / 下单 / 支付 / 选定类——它们在挂起语境里是授权，在候选列表之后本身就是请求，不算应答。
_YES_WORDS = ("确认", "确定", *ACK_WORDS, "订吧", "订了", "付吧", "支付", "下单", "就这家", "就它")
_YES_WORDS_BY_LEN = tuple(sorted(_YES_WORDS, key=len, reverse=True))
#: 裸确认的语气面：判据与理由（评审二轮 R1 / 三轮 R3-01 A）见 `runtime.affirmation.PARTICLE_RE`。
_CONFIRM_PARTICLE_RE = PARTICLE_RE
#: 「可以吗 / 确认吗 / 行不行」——**只问能不能、不含任何别的内容**的确认询问（W01）。
#: 剥掉疑问尾词后剩下的必须就是一个肯定词或一个「X不X」能力问法；「可以换第二天的安排吗」
#: 剥完还有内容，不在这一档。
_CONFIRM_ASK_TAIL_RE = re.compile(r"[吗么呢嘛啊呀？?！!。．.\s]+$")
_CONFIRM_ASK_FORMS = frozenset({
    "行不行", "可不可以", "好不好", "能不能", "确认不确认", "是不是", "对不对", "确定不确定"})
#: 「确认+点名」里夹在肯定词与点名之间 / 尾随的填充（W01）：「确认一下那个明晚的吧」
#: 剥完剩「明晚」。只有虚词与标点，零领域词。
_CONFIRM_FILLER_RE = re.compile(
    r"^(?:一下|吧|呀|啊|那个|这个|那条|这条|那笔|这笔|的|[，,、\s])+"
    r"|(?:那个|这个|那条|这条|那笔|这笔|的|吧|呢|啊|呀|一下|[，,、。！!\s])+$")

# ── 点名的召回与裁决（评审三轮 R3-01 B，2026-09-23）──────────────────────────────
# 召回（`_pending_names`，二元片段）只负责**找候选**；授权要过裁决（`_naming_coverage`）：点名余量去掉零领域虚词后，
# 每个非数字字都要落在 ≥2 字、且是**已校验步骤事实**子串的片段里，每个数字串都要作为完整数字出现在事实里。
# 修前挂着「打开后备箱」时，「确认关闭后备箱 / 锁上后备箱 / 打开车窗」都靠共享片段命中唯一候选、注入了 confirmed。
# 二字阈值改三字没用——「后备箱」照样共享；判据是「点名说的每一样东西都得是这一步本来就有的」。
#: 零领域虚词：只修饰、不带动作 / 对象 / 数量。裁决前从点名余量里剥掉（事实那一侧不剥）。
_NAMING_NEUTRAL_RE = re.compile(
    r"那个|这个|那条|这条|那笔|这笔|那件|这件|那次|这次|一下|刚才|刚刚|方才|帮我|替我|给我|麻烦"
    r"|请|把|将|的|了|吧|呢|啊|呀|嘛|就|再|那|这")
_NAMING_PUNCT_RE = re.compile(
    r"[\s，,、。．.！!？?~～·…:：;；\"'“”‘’「」『』《》〈〉（）()\[\]【】\-—_/|]+")
_CN_NUMERAL_RUN_RE = re.compile(rf"[{CN_NUM_CHARS}零〇]+")
_DIGIT_RUN_RE = re.compile(r"\d+")


def _naming_core(text: str, *, neutral: bool) -> str:
    """点名 / 事实的比较形态：小写、去空白标点、（点名一侧）去零领域虚词、中文数字转阿拉伯数字（两侧同一规则）。"""
    t = _NAMING_PUNCT_RE.sub("", str(text or "").lower())
    if neutral:
        t = _NAMING_NEUTRAL_RE.sub("", t)

    def _arabic(m: re.Match) -> str:
        value = cn_int(m.group(0))
        return str(value) if value is not None else m.group(0)

    return _CN_NUMERAL_RUN_RE.sub(_arabic, t)


def _naming_coverage(needle: str, facts: list[str]) -> float:
    """点名余量被事实覆盖的比例（0–1）。数字只认整串相等（「50」不被「500」覆盖），其余字只认
    ≥2 字的事实子串片段（单字不算——「锁」「关」单字到处都有）。"""
    n = _naming_core(needle, neutral=True)
    cores = [c for c in (_naming_core(f, neutral=False) for f in facts or []) if c]
    if not n or not cores:
        return 0.0
    covered = [False] * len(n)
    numbers = {d for c in cores for d in _DIGIT_RUN_RE.findall(c)}
    for m in _DIGIT_RUN_RE.finditer(n):
        if m.group(0) in numbers:
            covered[m.start():m.end()] = [True] * (m.end() - m.start())
    for i in range(len(n) - 1):
        for j in range(len(n), i + 1, -1):
            if any(n[i:j] in c for c in cores):
                for k in range(i, j):
                    if not n[k].isdigit():         # 数字只由上面的整串比较覆盖
                        covered[k] = True
                break
    return sum(covered) / len(n)


class _SpokenConfirm:
    """`_resolve_spoken_confirm` 的返回值（见其 docstring）。"""
    __slots__ = ("kind", "target", "candidates", "named")

    def __init__(self, kind: str, target=None, candidates=None, named: str = ""):
        self.kind = kind
        self.target = target
        self.candidates = list(candidates or [])
        self.named = named

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


def _admission_timeout_s() -> float:
    """轻量受话判定的上限（秒）：它只是一道把关，慢了按判不出处理（受话），不让「确认」干等（A/B 里单次最长 10.5 s）。
    代码缺省 3.0，不进 `.env.example`（部署闸按路径硬阻断）；非法值回落缺省。"""
    try:
        value = float(os.getenv("ADMISSION_TIMEOUT_S", "") or 3.0)
    except ValueError:
        return 3.0
    return value if value > 0 else 3.0


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
    - planner 把槽值**转述**过（「瑞幸」→「luckin」）时子串够不着；
    - 修饰分句（「联网查询」「至少五百字」）不是一个诉求，却是一个分句。

    2026-09-20 拿到生产分布（830 轮里 134 条命中，逐条看 ≥95% 误报）后去掉三类**可判定**的
    误报：整句型能力 / 整句透传槽（chitchat 的 `text`、兜底）——一步吃整句；步数 ≥ 分句数
    ——每个分句都可能有自己的步（槽值转述时子串够不着，但那不是漏步）；单步已填槽数 ≥ 分句数
    ——「导航去深圳湾公园，晚上7点前到」的 destination + arrive_by 两个槽正是两个分句。
    修饰分句那一类判不掉（它与真诉求在形态上无差别），所以这一列仍只观测、不出用户可见话术
    （评审 W12 记账，设计文档 §5）。**误报的代价只是一位观测，漏报的代价是缺陷继续隐形。**

    零领域字面量：不认识任何 intent，也不认识任何槽名。
    """
    steps = getattr(plan, "steps", None) or []
    clauses = [c for c in split_clauses(text) if not is_negated_directive(c)]
    if len(clauses) < 2 or not steps:
        return ""
    if len(steps) >= len(clauses):
        return ""
    if any(bool(getattr(s, "whole_utterance", False)) for s in steps):
        return ""
    whole = re.sub(r"\s+", "", str(text or ""))
    values = [v for s in steps for v in (s.slots or {}).values()
              if isinstance(v, str) and len(v.strip()) >= _COVER_MIN_LEN]
    if whole and any(whole in re.sub(r"\s+", "", v) for v in values):
        return ""
    if len(steps) == 1 and len(values) >= len(clauses):
        return ""
    uncovered = sum(1 for c in clauses
                    if not any(v.strip() in c for v in values))
    return f"{uncovered}/{len(clauses)}" if uncovered else ""


def goal_gap(plan, text: str) -> list[str]:
    """哪些诉求**没有步骤承接**（W12，评审 F07）→ 原话截段列表；判不出 / 没证据 ⇒ []。

    账本由模型自报（`Plan.goals` + `Step.covers`），系统只做四道核对，缺一不报：
      ① goals ≥ 2（单诉求有步就是被承接）；② **每一步**都填了 covers（少填一步 = 模型没参与账本，
      不据此判漏）；③ 某条诉求不在任何一步的 covers 里；④ 那条诉求与所有槽值互不包含（≥2 字）
      ——模型漏标了一步实际负责的诉求时，槽值会替它作证。整句型能力 / 整句透传槽的步吃整句，不报。
    生产分布里按分句猜 ≥95% 误报的三类（整句透传、单步双槽、修饰分句）在这里都由模型账本区分。
    """
    steps = list(getattr(plan, "steps", None) or [])
    goals = [str(g) for g in (getattr(plan, "goals", None) or []) if str(g).strip()]
    if len(goals) < 2 or not steps:
        return []
    if any(bool(getattr(s, "whole_utterance", False)) for s in steps):
        return []
    if any(not (getattr(s, "covers", None) or []) for s in steps):
        return []
    whole = re.sub(r"\s+", "", str(text or ""))
    values = [re.sub(r"\s+", "", v) for s in steps for v in (s.slots or {}).values()
              if isinstance(v, str) and len(v.strip()) >= _COVER_MIN_LEN]
    if whole and any(whole in v for v in values):
        return []
    covered = {index for s in steps for index in (s.covers or [])}
    gap: list[str] = []
    for index, span in enumerate(goals, 1):
        if index in covered:
            continue
        compact = re.sub(r"\s+", "", span)
        if any(v in compact or compact in v for v in values):
            continue
        gap.append(span)
    return gap


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


def _context_stats_attrs(working_set) -> dict:
    """`WorkingSet.context_stats` → span 属性（只取计数与布尔，缺席时空 dict）。"""
    stats = getattr(working_set, "context_stats", None) if working_set is not None else None
    if not isinstance(stats, dict) or not stats:
        return {}
    out = {}
    for key in ("ctx_chars", "history_chars", "history_pairs_kept",
                "history_pairs_dropped", "history_exchanges"):
        value = stats.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            out[key] = value
    if stats.get("history_trimmed"):
        out["history_trimmed"] = "true"
    # 批 5 W17：两格读态（found / none / unavailable / off）——生产里「记忆到底多常读不到」的读数；
    # 值域封闭（`runtime.memory_read.READ_STATES`），不是用户内容。
    for key in ("history_state", "memory_state"):
        value = stats.get(key)
        if isinstance(value, str) and value in memory_read.READ_STATES:
            out[key] = value
    return out


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


def _claim_gate_attrs(gate) -> dict:
    """流式声称闸的观测（评审三轮 R3-04）：首次放行等待与拦下处数；没挂闸 / 没放行过就不发。"""
    if gate is None or gate.first_hold_ms is None:
        return {}
    return {"claim_gate_hold_ms": int(round(gate.first_hold_ms)),
            "claim_gate_removed": int(gate.removed)}


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
                # W13 终态账本：每条 final 出口都声明了自己是哪一种结局（内部键，这里剥掉）。
                outcome_kind = str(ev.pop("_outcome", "") or "")
                # Q1-C：本轮关掉了哪几条挂起，由服务端权威告诉 HMI（撤确认条）。
                # 空则不发键——多一个恒空字段就是多一处噪声。
                if ctx.closed_operation_ids:
                    ev["closed_operation_ids"] = list(ctx.closed_operation_ids)
                # Q6：本轮真实执行了什么，随 assistant 轮一起落库。
                # 取 final 帧而不是 step_result——**用户看到的那份就是这一份**，
                # 中途被聚合器丢掉的步不该出现在审计回答里。
                executed_actions = _executed_action_names(ev.get("actions"))
                # C4-A：这一轮的数据源事实与动作事实同源同格，一起落库。
                turn_sources = _turn_sources(ev.get("ui_card"))
                # C11-C：「话里有一次执行，账上没有」——观测列，以及 W14 里唯一动手的那一种
                # （谈话步 + 零动作 ⇒ 按声明必假，剥掉声称句）。
                # 挂在这里而不是聚合器：final 有四条出口（adaptive/stream/reactive/E），
                # 而它们全都从这个 for 里流过——**同一条纪律写成注释还是写成结构，
                # 差别就是会不会有第三次**（`_deterministic_reply` 那条老账）。
                await self._emit_execution_claim(ctx, ev, executed_actions)
                outcome_kind = self._apply_goal_gap(ctx, ev, outcome_kind)
                if ev.get("speech"):
                    assistant_speech = ev["speech"]
                await self._emit_outcome(ctx, outcome_kind, ev, executed_actions)
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

    #: W14：谈话步的声称句全部剥空之后的诚实话术（零领域词）。
    _CLAIM_STRIPPED_SPEECH = CLAIM_STRIPPED_SPEECH     # 一句话只留一份（runtime.execution_claim，流式出口共用）

    @staticmethod
    async def _emit_execution_claim(ctx, event: dict, actions: list) -> None:
        """本轮 final 说了「已为您…」却零动作 ⇒ 出一位 obs 观测（C11-C，shadow）。

        判据本体在 `runtime/execution_claim.py`（形态、零领域词），探针 C16-2 复用
        同一份。命中就发一条 span，不命中一个字都不发（多一个恒空字段就是多一处噪声）。

        W14（评审 §7，2026-09-20）：生产 830 轮的分布——6 条命中，4 条是 engine 自己的
        `pending_cancel` 出口（真关掉了一条挂起，是尺子误报），2 条是 chitchat 零动作声称
        执行（「可以，已为您执行」「已为您避开此路段。已为您重新规划路线：…7.4公里」）。
        两条真阳性都满足同一条**按声明必假**的判据：这一轮执行的步全是 `response_only`
        （`ctx.answer_only`）∧ 零动作 ∧ 话术命中执行性声明。只拦这一种：按句剥掉声称句，
        剥空了换成固定的诚实话术；其余形态（信息类能力的「已为您规划 3 天行程」是真的）照旧只观测。
        剥掉的是**模型编的执行事实**，不是润色——把假话删掉与把失败改写成成功方向相反。
        """
        if actions:
            return
        speech = str(event.get("speech") or "")
        family = execution_claim(speech)
        if not family:
            return
        intercepted = False
        if getattr(ctx, "answer_only", False):
            cleaned, removed = strip_execution_claims(speech)
            if removed:
                intercepted = True
                logger.warning("response-only turn claimed an execution (%s); "
                               "stripped %d sentence(s): %r", family, removed, speech[:80])
                event["speech"] = cleaned or PlannerEngine._CLAIM_STRIPPED_SPEECH
        try:
            await obs_events.get_emitter("cloud").emit_span(
                ctx.trace_id, "cloud.execution_claim",
                attrs={"family": family,
                       **({"intercepted": "true"} if intercepted else {})})
        except Exception as e:      # 观测绝不阻塞主链路
            logger.debug("execution claim obs failed: %s", e)

    @staticmethod
    def _apply_goal_gap(ctx, event: dict, kind: str) -> str:
        """W12：这一轮完成了，但账本说有诉求没人承接 ⇒ 如实补一句、出 `goal.uncovered`、终态记 partial。

        只在**完成类** final 上说（挂起 / 澄清 / 拒识那些出口本来就没完成任何事）；话术是固定句式，
        引用的是模型按原话截出的那一段，不改写。
        """
        gap = list(getattr(ctx, "goal_gap", None) or [])
        if not gap or kind not in ("completed", "partial") or event.get("operation_id"):
            return kind
        named = "、".join(f"「{span}」" for span in gap[:2])
        hint = f"{named}这部分这次没有处理到，需要的话再单独说一次。"
        event["follow_up"] = PlannerEngine._append_hint(event.get("follow_up"), hint)
        issues = list(event.get("issues") or [])
        issues.append(contracts.build_issue(
            contracts.ISSUE_GOAL_UNCOVERED,
            f"{named}没有步骤承接，本轮只处理了其余部分。",
            severity=contracts.SEVERITY_INFO,
            request_id=ctx.request_id))
        event["issues"] = issues
        logger.info("goal gap on a completed turn: %s", gap)
        return "partial"

    @staticmethod
    async def _emit_outcome(ctx, kind: str, event: dict, actions: list) -> None:
        """W13 终态账本：每条 final 在唯一出口发一条 `cloud.outcome`（collector 合并成 `turns.outcome`）。

        `kind` 由各出口声明（`runtime.outcome` 词表）；没声明的记 `unknown` 并告警——账本上
        「不知道」必须与任何一种结局分得开，静默补一个默认值就是评审 §6.3 说的「摘要改写事实」。
        """
        kind = kind or "unknown"
        if kind == "unknown":
            logger.warning("final without an outcome kind (speech=%r)",
                           str(event.get("speech") or "")[:60])
        try:
            await obs_events.get_emitter("cloud").emit_span(
                ctx.trace_id, "cloud.outcome",
                attrs={"kind": kind, "category": category_of(kind) or "unknown",
                       "actions": len(actions or []),
                       "answer_only": "1" if getattr(ctx, "answer_only", False) else "0"})
        except Exception as e:      # 观测绝不阻塞主链路
            logger.debug("outcome obs failed: %s", e)

    async def _orchestrate(self, request, ctx: PlanContext, text: str,
                           mem_on: bool) -> AsyncIterator[dict]:
        """规划→校验→执行→聚合。yield 事件：{"kind": "speech"|"action"|"final", ...}"""
        plan: Plan | None = None
        seed_results: list[StepResult] = []
        agents = []
        working_set = None  # 新规划轮由 ContextManager 装配；确认/补槽续接保持 None
        # 补槽答案替换掉的旧槽值 `{旧: 新}`（判据在 `superseded`）：续接进 T2 循环时不许再追旧值
        superseded: dict[str, str] = {}

        # A. 多轮续接：存在挂起的待确认会话时，判定本轮是否在回应确认
        # R2（中断-恢复，Q1 口径）：插话**不清除**挂起——插话轮正常处理，挂起在 TTL 内
        # 可回头「确认」/裸答案续接；新话轮若再产生挂起，_suspend 单槽覆盖旧挂起
        # （确认条 UI 也只有一个，语义一致）。held_pending 贯穿本轮：完成路径经
        # _settle_session 跳过 clear，并在 final 上补一句软提醒。
        held_pending = None
        entries, pending_state = await self.session.load_all_result(
            ctx.session_id, owner_user_id=ctx.user_id)
        pending = self._address_pending(entries, ctx.operation_id)

        # 评审二轮 R8：挂起表**读不到**时，「没有挂起」这句话是假的。带寻址键的确认、裸确认词
        # 与挂起读出口三条出口都要说「暂时读不到」，绝不下发 `closed_operation_ids`（那条挂起
        # 可能还在），也绝不执行。普通请求照旧进规划（fail-open：一次存储故障不该让整轮不可用）。
        if pending_state == PENDING_UNAVAILABLE and (
                ctx.operation_id or ctx.is_confirmation
                or self._intercepts_as_confirm(text)):
            logger.warning("pending table unavailable on a confirm-shaped turn")
            await _emit_engine_lifecycle(
                ctx, "cloud.pending_unavailable", "system.pending_unavailable")
            yield {"kind": "final",
                   "speech": session_facts.PENDING_UNAVAILABLE_SPEECH,
                   "actions": [], "_outcome": "pending_unavailable"}
            return

        # Q1-B：确认帧带了寻址键却对不上任何挂起 → **诚实拒绝**。
        # 不静默打给当前挂起（I-013 全局确认命中旧请求），也不清掉它——
        # 那一下不是冲它来的。空 operation_id（语音兜底/旧客户端）不走这条。
        if ctx.operation_id and pending is None:
            logger.info("Confirmation addressed a pending that is gone (%s)",
                        ctx.operation_id[:16])
            await _emit_engine_lifecycle(
                ctx, "cloud.pending_missing", "system.pending_missing")
            # 批 5 W18：客户端还举着这条挂起的确认条（真栈：沉默 600 s 后它在服务端过期了），
            # 服务端说它已经不在——对客户端它就是关掉了：`closed_operation_ids` 点名它，
            # 撤确认条 / 探针清理台账都读这个键，不读话术。
            ctx.closed_operation_ids.append(ctx.operation_id)
            yield {"kind": "final",
                   "speech": "这条确认对应的操作已经不在了，麻烦您再说一遍需求。",
                   "_outcome": "pending_missing"}
            return

        # W01（评审 F01，2026-09-19）：**没有寻址键的确认**先问「它在说哪一条」，
        # 而不是按「最近一条」猜。三种形态各有出口，判据全在 `_resolve_spoken_confirm`：
        #   · 恰好一条 wait_confirm ⇒ 就是它（哪怕更新的那条是 wait_slot——此前裸「确认」
        #     会落进 wait_slot 分支被当成槽值，确认与补槽混在一起）；
        #   · ≥2 条 wait_confirm 且没点名 ⇒ 问一次，零动作、零关闭、两条都留着；
        #   · 一条 wait_confirm 都没有 ⇒ 「没有待确认的操作」，并提醒还在等补充的那条。
        confirm_resolved = False   # W01：本轮的确认已在挂起表里寻址完成（点名 / 唯一所指）
        if entries and not ctx.operation_id:
            # 评审三轮 R3-01 B：点名确认的裁决面是已校验步骤摘要；旧记录没有就现取一次（只在有点名余量时）。
            # 端侧对整句的解析只做否决（`_edge_nlu` 是端侧自己盖的章，客户端同名键在端侧入口剥掉）。
            await self._ensure_confirm_facts(entries, text, ctx.is_confirmation)
            # 评审四轮 R4-01：纯应答（「好的 / 嗯 / 可以」）回答的是**最近那一问**。最新一条挂起是待确认、且它就是最近那个
            # 提示（提出它的那一轮就是最近一轮）才算对它的授权；证明不了就不授权——修前真栈：挂着「打开后备箱」时插话听笑话，
            # 助手问「还要再听一个吗」，用户「好的」⇒ 后备箱被打开（RS34 3/3）。只在这一种句子上多读一次历史。
            ack_binds = False
            if (not ctx.is_confirmation and is_bare_acknowledgment(text)
                    and getattr(entries[-1], "phase", "") == "wait_confirm"):
                ack_binds = await self._pending_is_latest_prompt(ctx, entries[-1], mem_on)
            spoken = self._resolve_spoken_confirm(
                text, ctx.is_confirmation, entries,
                edge_intent=str(getattr(ctx, "edge_nlu", "") or "").split("|", 1)[0].strip(),
                ack_binds=ack_binds)
            if spoken.kind == "ack":
                # 这一声应答不是冲挂起来的：按插话保留（R2），交规划去接最近那一问，末尾提醒还在等的那条
                logger.info("Bare acknowledgment is not addressed to the pending %s",
                            (getattr(pending, "operation_id", "") or "")[:16])
                held_pending = pending
                pending = None
            if spoken.kind == "mismatch":
                # 点名召回到了挂起，却与它的已校验步骤对不上（「确认关闭后备箱」而挂着的是打开后备箱）——
                # **不是授权**，也不按插话把这句交给规划去猜：念出等确认的是什么、怎么确认 / 作废。零动作、零关闭。
                labels = "、".join(self._pending_label(s) for s in spoken.candidates)
                await _emit_engine_lifecycle(
                    ctx, "cloud.pending_mismatch", "system.pending_mismatch")
                if len(spoken.candidates) == 1:
                    speech = (f"我这边等您确认的是{labels}；您说的「{spoken.named}」我没法确定就是它，"
                              "所以这次没有执行。是它的话请说「确认」，不需要就说「取消」。")
                else:
                    speech = (f"我这边等您确认的有{labels}；您说的「{spoken.named}」对不上其中任何一条，"
                              "所以这次没有执行。请点选对应的确认条，或说出要确认哪一条。")
                yield {"kind": "final", "speech": speech, "actions": [],
                       "held_operation_ids": [
                           s.operation_id for s in spoken.candidates if s.operation_id],
                       "_outcome": "pending_mismatch"}
                return
            if spoken.kind == "ambiguous":
                labels = "，还是".join(
                    self._pending_label(s) for s in spoken.candidates)
                await _emit_engine_lifecycle(
                    ctx, "cloud.pending_ambiguous", "system.pending_ambiguous")
                yield {"kind": "final",
                       "speech": f"您要确认{labels}？请点选对应的确认条，或说出要确认哪一条。",
                       "actions": [],
                       "held_operation_ids": [
                           s.operation_id for s in spoken.candidates
                           if s.operation_id],
                       "_outcome": "pending_ambiguous"}
                return
            if spoken.kind == "asking":
                # 确定性读出口（同 `system.pending_state` 族）：念出挂着的是什么、怎么确认。
                # 话术与「还有待确认的操作吗」共用 `session_facts.pending_answer`。
                await _emit_engine_lifecycle(
                    ctx, "cloud.pending_state", "system.pending_state")
                yield {"kind": "final",
                       "speech": session_facts.pending_answer(
                           self._digest_of(spoken.candidates)),
                       "actions": [],
                       "held_operation_ids": [
                           s.operation_id for s in spoken.candidates
                           if s.operation_id],
                       "_outcome": "pending_asked"}
                return
            if spoken.kind == "none":
                await _emit_engine_lifecycle(
                    ctx, "cloud.no_pending", "system.no_pending")
                final = {"kind": "final",
                         "speech": "当前没有待确认的操作。您可以重新告诉我需求。",
                         "actions": [], "_outcome": "no_pending"}
                self._append_pending_hint(final, entries[-1])
                yield final
                return
            if spoken.kind == "one":
                pending = spoken.target
                confirm_resolved = True
            elif spoken.kind == "named_miss":
                # 「确认订单」而挂着的是解锁：点名了却没点到——**不是授权**。
                # 按插话处理（R2 保留挂起），绝不落回「最近一条」去执行。
                held_pending = pending
                pending = None

        # Q1-A：取消判定对两条分支是**同一件事**，所以在分岔之前判一次。
        # 此前 wait_confirm 走「词占据整句」、wait_slot 走子串+复合余量，
        # 「取消刚才解锁」在前者判不出取消（I-046）。判据全在 pending_cancel。
        just_cancelled = False
        if pending and not self._is_cancel_index_answer(text, pending):
            cancelled = detect_cancel(text)
            # 评审二轮 R2（2026-09-22）：极性与问句都在改状态**之前**判。
            #   · 「不要取消 / 别取消」是在保留挂起——此前剥掉「取消」再剥掉「不要」余量为空 ⇒ 纯取消；
            #   · 「怎么取消 / 取消了吗」是在问——此前按复合取消先清挂起，询问本身改变了状态；
            #   · 「取消刚才咖啡订单」在两条挂起并存时按点名绑定，点不到 / 点到多条就问，不清另一条。
            # 带余量的「不要取消，改成明晚」不进取消出口，余下的话按既有分支（插话 / 换题）走。
            if cancelled.act == "keep" and not cancelled.remainder:
                await _emit_engine_lifecycle(
                    ctx, "cloud.pending_kept", "system.pending_kept")
                yield {"kind": "final",
                       "speech": f"好的，不取消，{self._pending_label(pending)}"
                                 f"还在等您{self._pending_ask_word(pending)}。",
                       "actions": [],
                       "held_operation_ids": [pending.operation_id] if pending.operation_id else [],
                       "_outcome": "pending_kept"}
                return
            if cancelled.act == "ask":
                await _emit_engine_lifecycle(
                    ctx, "cloud.pending_state", "system.pending_state")
                yield {"kind": "final",
                       "speech": "还没有取消。" + session_facts.pending_answer(
                           self._digest_of([pending])),
                       "actions": [],
                       "held_operation_ids": [pending.operation_id] if pending.operation_id else [],
                       "_outcome": "pending_asked"}
                return
            if cancelled.cancelled and cancelled.target and len(entries) >= 2:
                hits = [s for s in entries if self._pending_names(s, cancelled.target)]
                # 评审三轮 R3-01 B′：召回到多条时按覆盖度挑唯一最高者，并列才问（取消方向 fail-safe）
                hits = self._best_named(hits, cancelled.target)
                if len(hits) == 1:
                    pending = hits[0]
                else:
                    labels = "，还是".join(
                        self._pending_label(s) for s in (hits or entries))
                    await _emit_engine_lifecycle(
                        ctx, "cloud.pending_ambiguous", "system.pending_ambiguous")
                    yield {"kind": "final",
                           "speech": f"您要取消{labels}？请点选对应的确认条，或说出要取消哪一条。",
                           "actions": [],
                           "held_operation_ids": [
                               s.operation_id for s in (hits or entries) if s.operation_id],
                           "_outcome": "pending_ambiguous"}
                    return
            if cancelled.cancelled:
                just_cancelled = True
                cleared = await self._close_pending(ctx, pending)
                if not cancelled.compound:
                    await _emit_engine_lifecycle(
                        ctx, "cloud.pending_cancel", "system.pending_cancel")
                    if cleared == CLEAR_UNAVAILABLE:
                        # 评审三轮 R3-05：删除那一刻后端不可用——能证明的是「本进程不会再执行它」（墓碑），
                        # 不能证明「已从记录里删掉」。不说「已取消」，也不让用户猜要不要再说一次。
                        yield {"kind": "final",
                               "speech": f"好的，{self._pending_label(pending)}不会执行了。",
                               "actions": [], "_outcome": "cancel_unconfirmed"}
                        return
                    # 多条挂起并存时说清取消的是哪一条（W01 的对照面）：裸「取消」
                    # 仍按最近一条处理——撤销方向是 fail-safe 的，但不能让用户猜。
                    which = (self._pending_label(pending)
                             if len(entries) >= 2 else "")
                    yield {"kind": "final",
                           "speech": (f"好的，已为您取消{which}。" if which
                                      else "好的，已为您取消。"),
                           "_outcome": "cancelled"}
                    return
                # 复合句（「算了咖啡不买了，**先去加点油**」）：取消只作用于挂起，
                # 其余内容按全新请求继续处理——不 return、不进确认/补槽/话题分支。
                logger.info(
                    "pending cancel with compound remainder (%d chars); "
                    "continuing as fresh request", len(cancelled.remainder))
                pending = None

        # W10：澄清续接——上一轮系统问了「你要哪一种」，这一轮先看是不是在**选**。
        # 目标挂起：带寻址键就只认它；不带就认最近一条 wait_clarify。解出选项 ⇒ 关掉挂起，
        # 有预解析 step 的直接当已恢复计划执行（零 LLM），没有的用选项的完整指令重新规划
        # （带 `clarify_resume=1` 与 `clarify_probe`，止损判据据此判「又问同一个问题」）。
        # 解不出 ⇒ 换题：挂起保留（R2），新话正常规划。
        clarify_pending = self._clarify_target(entries, ctx.operation_id, pending)
        if clarify_pending is not None and pending is not None:
            option = self._resolve_clarify_choice(text, clarify_pending)
            # 评审四轮 R4-02：不带寻址键、只说「第几个」的回复归**最近那个提示**。这条澄清之后又出过补槽问题 / 新候选列表，
            # 「2 / 第二个」就是在答那个更新的——按插话保留它（R2），回复交给更新的提示（补槽分支 / 带候选的规划）。
            # 选项 label / send_text 原文是点名了这个问题的选项，照接；带寻址键（点旧卡）走 `_clarify_target` 的精确命中。
            if (option is not None and not ctx.operation_id
                    and self._clarify_reply_is_positional(text)
                    and not await self._clarify_is_latest_prompt(
                        ctx, entries, clarify_pending, mem_on)):
                logger.info("Positional reply %r is not for the older clarify %s; a newer prompt owns it",
                            text[:10], (clarify_pending.operation_id or "")[:16])
                option = None
            # 评审四轮 R4-07 第二步：选中一项就执行它——背景里一句「第一个」会执行澄清卡的第一项。语音说出的选择先过受话判定，
            # 判非受话 ⇒ 拒识、澄清原样保留。点卡（没有语音来源）不问。
            if option is not None and not await self._voice_admitted(
                    ctx, text, exit_name="clarify_choice", mem_on=mem_on):
                async for ev in self._reject_not_addressed(ctx):
                    yield ev
                return
            if option is not None:
                await self._close_pending(ctx, clarify_pending)
                await _emit_engine_lifecycle(
                    ctx, "cloud.clarify_choice", "system.clarify_choice")
                ctx.clarify_probe = {
                    "question": str((clarify_pending.clarify or {}).get("question") or ""),
                    "labels": [str(o.get("label") or "")
                               for o in (clarify_pending.clarify or {}).get("options") or []
                               if isinstance(o, dict)],
                }
                chosen_text = str(option.get("send_text") or "").strip() or text
                logger.info("Clarify choice resolved (%s): %s",
                            (clarify_pending.operation_id or "")[:16], chosen_text[:40])
                # 之后的规划 / Agent 看到的是**用户选定的那条完整指令**；本轮原话（「第一个」）
                # 只留在 run() 的历史落库里。
                text = chosen_text
                ctx.raw_text = chosen_text
                pending = None
                if isinstance(option.get("step"), dict):
                    try:
                        plan = Plan(
                            steps=[Step(**option["step"])], raw_text=chosen_text,
                            goal=chosen_text,
                            # 用户点选了系统展示过的这条指令，它就是这次任务的起点原话
                            safety_origin_text=chosen_text)
                        for s in plan.steps:
                            s.origin_text = chosen_text          # W16-b：预解析步从这句话来
                        seed_results = []
                    except TypeError as exc:
                        logger.warning("Pre-resolved clarify step is unreadable (%s); replanning", exc)
                        plan = None
                if plan is None:
                    ctx.prefs["clarify_resume"] = "1"
            elif pending is clarify_pending:
                held_pending = pending
                pending = None

        if pending and pending.phase == "wait_confirm":
            # 点名式确认（「确认明晚那个」）整句不是裸肯定词，`_confirm_reply` 会判成插话；
            # 寻址那一步已经认定它就是对这条挂起说「是」。
            reply = ("yes" if confirm_resolved
                     else self._confirm_reply(text, ctx.is_confirmation))
            if reply == "yes":
                # 评审四轮 R4-07 第一步：执行挂起步之前，语音说出的确认先过受话判定——修前背景里一句「确认」/ 挂起就是最近提示时
                # 一句「好的」直接执行挂起的危险操作。判非受话 ⇒ 拒识、挂起原样保留（真用户再说一次照常执行）。
                # 全局确认条按钮（`is_confirmation`）是一次点击，不问。
                if not ctx.is_confirmation and not await self._voice_admitted(
                        ctx, text, exit_name="confirm", mem_on=mem_on):
                    async for ev in self._reject_not_addressed(ctx):
                        yield ev
                    return
                plan, seed_results = self._restore(pending, inject_confirmed=True)
                if plan is None:
                    await self._close_pending(ctx, pending)
                    await _emit_engine_lifecycle(
                        ctx, "cloud.pending_expired", "system.pending_expired")
                    yield {"kind": "final",
                           "speech": "刚才的操作已过期，麻烦您再说一遍需求。",
                           "_outcome": "pending_expired"}
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
                # 评审四轮 R4-07 第二步：这句话会被当成槽值去执行——补槽窗口里别人说的一句话不该变成目的地 / 提醒标题。
                # 语音来源先过受话判定，判非受话 ⇒ 拒识、挂起原样保留（也不算一次没接上的重问）。
                if not await self._voice_admitted(ctx, text, exit_name="slot_fill", mem_on=mem_on):
                    async for ev in self._reject_not_addressed(ctx):
                        yield ev
                    return
                # 补槽恢复绝不注入 confirmed——补槽答案不是确认（见 _restore docstring）
                plan, seed_results = self._restore(pending, inject_confirmed=False)
                if plan is None:
                    await self._close_pending(ctx, pending)
                    await _emit_engine_lifecycle(
                        ctx, "cloud.pending_expired", "system.pending_expired")
                    yield {"kind": "final",
                           "speech": "刚才的操作已过期，麻烦您再说一遍需求。",
                           "_outcome": "pending_expired"}
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
                        answers = {slot_name: self._slot_answer(slot_name, text)
                                   for slot_name in (pending.missing_slots or [])}
                        # 评审四轮（`4438ea6b` RS39）：答案替换了 Agent 用不了、才追问的那个旧值 ⇒ 模型写的 goal 跟着用户改，
                        # 之后这一轮循环不许再追旧值（判据与两处消费见 `superseded` 模块）
                        superseded = superseded_values(step.slots, answers)
                        step.slots.update(answers)
                        break
                if superseded:
                    plan.goal = rewrite_goal(plan.goal, superseded)
            if pending is not None:
                logger.info(
                    "Resuming plan for session %s (slot fill step %s, text=%s)",
                    ctx.session_id, pending.pending_step_id, text[:20])
        elif not just_cancelled and (
                ctx.is_confirmation or self._intercepts_as_confirm(text)):
            # 带确认标记，或裸"确认/取消"，但没有挂起任务（TTL 过期/上一步异常/重复点击）。
            # `just_cancelled` 排除复合取消刚清掉挂起的那一路——那句话的余量是新请求，
            # 不能因为它带确认标记就被答成「当前没有待确认的操作」。
            # 关键：裸确认词绝不下交 Planner——否则会借历史把"确认"重规划成上一意图的重复
            # 执行（反复 trip.modify），即用户报告的"确认后又改一遍并再次要确认"死循环。
            await _emit_engine_lifecycle(
                ctx, "cloud.no_pending", "system.no_pending")
            yield {"kind": "final",
                   "speech": "当前没有待确认的操作。您可以重新告诉我需求。",
                   "_outcome": "no_pending"}
            return

        new_plan = plan is None
        if plan is not None:
            # 恢复轮只从 pending_plan 取得任务起点；本轮 text 仍留在 ctx.raw_text
            # 给 Agent 填槽，绝不能把“深圳/拿铁/确认”升级成副作用授权依据。
            ctx.safety_origin_text = str(
                getattr(plan, "safety_origin_text", "") or ""
            )
            kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps(
                plan.steps, ctx.safety_origin_text,
            )
            if blocked:
                logger.warning(
                    "Restored plan has side-effecting step(s) incompatible with "
                    "its safety origin %s; dropping before dispatch",
                    [step.intent for step in blocked],
                )
                plan.steps = kept
                if not kept:
                    await self._close_pending(ctx, pending)
                    await _emit_engine_lifecycle(
                        ctx, "cloud.safety_origin_blocked",
                        "system.safety_origin_blocked",
                    )
                    speech = (
                        "刚才挂起的操作和最初请求不一致，为安全起见没有执行，"
                        "请重新发起。"
                        if ctx.safety_origin_text else
                        "刚才挂起的操作缺少可验证的原始请求，为安全起见没有执行，"
                        "请重新发起。"
                    )
                    yield {"kind": "final", "speech": speech,
                           "_outcome": "safety_origin_blocked"}
                    return
        if plan is None:
            # ws8 P1: 注入检测——疑似 prompt injection 时拦截，不进 Planner
            from security.injection import detect_injection
            if detect_injection(text):
                logger.warning("Prompt injection detected, rejecting: %s", text[:80])
                await _emit_engine_lifecycle(
                    ctx, "cloud.injection_reject", "system.injection_reject")
                yield {"kind": "final",
                       "speech": "抱歉，您的请求包含异常内容，无法处理。",
                       "_outcome": "injection_rejected"}
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
                                 "我列出来之后再说「第几个」就能接上。",
                       "_outcome": "candidate_missing"}
                return

            # 批 5 W18-a：点名的是一批**已经不在手边**的候选（被第 4 批顶掉 / 过期）⇒ 说它不在，
            # 绝不让 `newest_candidate_set` 顶替作答。修前「万象城那批第二家评分多少」零方差地答出
            # 南山书城那批的第二家——名字与评分都真实存在，比编造更难发现（评审 F03「过期后可说明
            # 需重新查询，禁止悄悄换成另一家」）。判据两段：点名了墓碑 ∧ 这句话确实在引用候选
            # （句首序数，或候选聚合形态）；活着的组被点名时永远优先（`retired_candidate_hit` 内判）。
            if not named_candidates:
                retired = retired_candidate_hit(text, working_set.focus)
                if retired and (references_a_candidate(text)
                                or candidate_query.is_candidate_aggregate_question(
                                    text, (live_candidates or {}).get("items"))):
                    name = str(retired.get("place_hint") or retired.get("label") or "")
                    logger.info("Named a retired candidate set (%s): %s", name, text[:40])
                    await _emit_engine_lifecycle(
                        ctx, "cloud.candidate_missing", "system.candidate_missing")
                    yield {"kind": "final",
                           "speech": f"「{name}」那批已经不在手边了，我只留最近几批。"
                                     f"要的话我重新查一下，再说「第几个」就能接上。",
                           "actions": [], "_outcome": "candidate_missing"}
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
                yield {"kind": "final", "speech": aggregate, "_outcome": "fact_answered"}
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
                yield {"kind": "final", "speech": speech, "_outcome": "fact_answered"}
                return

            # 批 5 W18-b：「我今天说过不吃辣吗」问的是**这次会话里**自己说过的约束——系统持有的
            # 事实（`focus.session_constraints`），同一族的第四条读出口。有账才劫持：说话人自己一个
            # 键都没有时交回规划（长期记忆里有没有是另一件事，chitchat 带召回去答）。
            if is_constraint_recall_question(text):
                recalled = constraint_recall_answer(
                    getattr(working_set.focus, "session_constraints", None) or {})
                if recalled:
                    logger.info("Deterministic constraint recall: %s", text[:40])
                    await _emit_engine_lifecycle(
                        ctx, "cloud.constraint_recall", "system.constraint_recall")
                    yield {"kind": "final", "speech": recalled, "actions": [],
                           "_outcome": "fact_answered"}
                    return

            # 批 5 W17：在问记忆、而记忆**读不到**（RPC 失败 / 服务自报后端掉线）⇒ 说查不到，
            # 不让 chitchat 拿着一份「兜底的空」去答「你没说过」。读得到（哪怕是空的）照旧进规划。
            if (memory_read.is_memory_recall_question(text)
                    and working_set.memory_state == memory_read.UNAVAILABLE):
                logger.warning("memory question while memory is unavailable: %s", text[:40])
                await _emit_engine_lifecycle(
                    ctx, "cloud.memory_unavailable", "system.memory_unavailable")
                yield {"kind": "final", "speech": memory_read.MEMORY_UNAVAILABLE_SPEECH,
                       "actions": [], "_outcome": "memory_unavailable"}
                return

            # W13 F09-a：**纯偏好陈述**（「我不吃辣，也不想排长队」）是系统持有的事实的登记，
            # 不是一个要规划的请求。真栈三批四次：MiniMax-M3 下 3/4 落技术失败出口、1 次被规划成
            # 一次搜索——而登记本身（`_register_input_facts`）每次都成功了，用户拿到的却是一句报错。
            # 判据在 `runtime.session_constraints.is_pure_constraint_statement`（每个分句都在谈
            # 口味 / 排队）；带安全信号的句子让给安全那条路；记忆关掉时焦点不落盘，「记下了」
            # 会是假话，退回正常规划。致谢按登记后的投影念（合并旧值），零 LLM。
            input_source = ctx.prefs.get("input_source", "")
            if (mem_on and is_pure_constraint_statement(text)
                    and not (alert_level(text) or driver_state(text) or alert_resolved(text))):
                # 评审二轮 R3（2026-09-22）：这条出口排在 planner 之前，语音来源的这句话就**跳过了
                # 受话判定**——乘客对别人说的「我不吃辣」被登记、被应答、还落进普通历史。语音来源先走
                # 既有的受话判定（planner `addressed`，与其他语音轮同一条路），判非受话 ⇒ 同一条拒识
                # 出口（零登记、零落库）；受话了才致谢。planner 交出的步 / 技术失败一概不用——短路诞生
                # 的理由（MiniMax-M3 下 3/4 落技术失败）照旧成立。文字 / 按钮来源没有受话问题，零 LLM。
                if not await self._voice_admitted(ctx, text, exit_name="constraint_noted",
                                                  working_set=working_set):
                    async for ev in self._reject_not_addressed(ctx):
                        yield ev
                    return
                await self._register_input_facts(ctx, text, mem_on)
                await _emit_engine_lifecycle(
                    ctx, "cloud.constraint_noted", "system.constraint_noted")
                yield {"kind": "final",
                       "speech": await self._constraint_ack(ctx, text),
                       "actions": [], "_outcome": "constraint_noted"}
                return

            plan = await self.planner.build(
                text, working_set, ctx,
                granted_permissions=ctx.granted_permissions)
            # 新任务的起点只能由 engine 使用本轮服务端 request.text 盖章。
            # Planner/Agent 给出的 goal/reason 即使看起来更像指令也没有这项权威。
            plan.safety_origin_text = text
            ctx.safety_origin_text = text
            # W16-b：每一步的起点原话也在这里盖（同一权威、同一处）。新计划里它就是本轮原话，
            # 只有这份计划挂起后在续接轮跑到的下游步才会真正用到它（`models.step_raw_text`）。
            for s in plan.steps:
                s.origin_text = text

            # 语音来源 + LLM 判非受话 → 静默拒识（route_hints 兜底的 steps 一并作废）。
            # Android 手动录音显式带 ptt；按下按钮只授权采集，不代表背景话就是给助手的请求。
            # 文字/按钮和旧客户端没有来源，保留原行为；确认/补槽仍走前面的续接分支。必须在
            # `if not plan.steps` 之前——addressed=false 时 steps 恰为空，否则先走空计划话术+TTS
            # 令拒识失效（母卡实施计划 §0-4）。
            if (is_voice_input_source(input_source)
                    and not plan.addressed and _reject_enabled()):
                async for ev in self._reject_not_addressed(ctx):
                    yield ev
                return
            # 评审四轮 R4-07 第一步：确定性早退交出的计划没经过受话判定（焦点省略开关，零 LLM）——免唤醒下背景里一句「关掉」
            # 会反向执行上一个控制。语音来源另问一次；受话了照旧执行这份确定性计划（不换成模型那份）。
            if getattr(plan, "admission_skipped", False) and not await self._voice_admitted(
                    ctx, text, exit_name="focus_ellipsis", working_set=working_set):
                async for ev in self._reject_not_addressed(ctx):
                    yield ev
                return

            # AR05 F09：规划技术失败**不许伪装成一次成功的闲聊**。
            # 真栈 trace 21798d30258aa5bf：非法工具 steps → 重试空计划 → toolcall_degraded
            # → chitchat.talk → info.search，用户拿到的是一段跑题回答，既不知道出了什么事，
            # 也没有恢复入口。这里给它一个诚实终态 + 可重试的恢复动作。
            # ⚠ 判据窄到只剩一种形态：**技术失败标记 ∧ 兜底真给出了步**。
            # 空计划仍走下面既有的澄清/取消未命中/「没听清」三条路，一个字不变。
            if plan.steps and getattr(plan, "technical_failure", False):
                await self._register_input_facts(ctx, text, mem_on)
                # W13 F09-b：模型在某一轮里**自己说过要澄清**（`clarify_wanted`）却没交出合法
                # 澄清卡——这不是故障，是「听到了对象、不知道要拿它做什么」。真栈 CL1：
                # 「云岚国际中心」1/3 次走到这里，用户听到的是「换个说法再说一次」外加一张
                # 重试卡。诚实的终态是把听到的东西念回去、说清缺的是什么；不出 retry issue。
                if getattr(plan, "clarify_wanted", False):
                    await _emit_engine_lifecycle(
                        ctx, "cloud.unresolved_object", "system.unresolved_object")
                    heard = str(text or "").strip()[:20]
                    yield {
                        "kind": "final",
                        "speech": f"我听到了「{heard}」，但没听清要拿它做什么——"
                                  f"说完整一点我就能办。",
                        "actions": [], "_outcome": "unresolved_object",
                    }
                    return
                await _emit_engine_lifecycle(
                    ctx, "cloud.planner_technical_failure", "system.planner_failure")
                yield {
                    "kind": "final",
                    "speech": "这次我没能把您的请求拆成可以执行的步骤，换个说法再说一次就行。",
                    "issues": [contracts.build_issue(
                        contracts.ISSUE_PLANNER_TECHNICAL_FAILURE,
                        "规划没有产出可执行的步骤，本轮没有执行任何操作。",
                        severity=contracts.SEVERITY_ERROR,
                        request_id=ctx.request_id,
                        recovery=[(contracts.RECOVERY_RETRY_REQUEST, "换个说法再试")])],
                    "_outcome": "planner_failure",
                }
                return

            if not plan.steps:
                # 规划轮在这里提前结束的四条出口（授权缺失 / 澄清 / 取消未命中 / 没听清）都
                # 不会走到 `update_focus`——输入侧事实的登记不能跟着出口一起丢（见方法注释）。
                await self._register_input_facts(ctx, text, mem_on)
                # AR05 解释面：这一轮要的能力**这个账号没有授权**。理由是服务端自己持有的
                # 事实（`/api/session` 摘要面同一条 `scope_missing`），所以话术也由服务端出，
                # 不交给 LLM——真栈实录：受限身份连问 6 次同一句得到 6 种说法，其中一次
                # 「已为你规划路线」而 actions 为空、一次把内部错误串吐给用户、0/6 提到
                # 真实原因。**说了没做**是本仓最不能接受的那一类。
                # 恢复出口指能力设置而**不是系统权限页**（conventions §9 第 7 条：
                # 业务 scope 与设备权限是两件事，指错地方等于让用户去关一个不存在的开关）。
                if getattr(plan, "scope_blocked", ""):
                    name = (getattr(plan, "scope_blocked_name", "")
                            or plan.scope_blocked)
                    await _emit_engine_lifecycle(
                        ctx, "cloud.scope_blocked", "system.scope_blocked")
                    yield {
                        "kind": "final",
                        "speech": f"当前账号没有「{name}」这项能力的授权，"
                                  f"所以这件事我没有去做。可以在能力设置里看看"
                                  f"这个账号现在有哪些能力。",
                        "issues": [contracts.build_issue(
                            contracts.ISSUE_PERMISSION_SCOPE_MISSING,
                            f"当前账号缺少「{name}」所需的授权，本轮没有执行任何操作。",
                            severity=contracts.SEVERITY_WARNING,
                            request_id=ctx.request_id,
                            affected_capabilities=[plan.scope_blocked],
                            recovery=[(contracts.RECOVERY_OPEN_CAPABILITY_SETTINGS,
                                       "查看账号能力")])],
                        "_outcome": "permission_missing",
                    }
                    return
                # R4.4 D6-3：路由歧义澄清（CLARIFY 开 + 本轮非 clarify_resume 深度=1 才生效）。
                # P0 时 CLARIFY_ENABLED 默认 off → 恒 None，行为=今天；P1 翻 on 后短路出卡。
                # W10：续接轮（`clarify_resume=1`）原则上不再问；**唯一例外**是服务端知道
                # 上一问是什么（`clarify_probe`）且这一张换了问题——止损限制的是「同一个问题
                # 没有进展」，不是全任务只许问一次。
                resume_turn = ctx.prefs.get("clarify_resume") == "1"
                clarify = plan.clarify if (_clarify_enabled() and (
                    not resume_turn
                    or (ctx.clarify_probe
                        and clarify_is_progress(ctx.clarify_probe, plan.clarify)))) else None
                if clarify:
                    await _emit_engine_lifecycle(
                        ctx, "clarify", "system.clarify")
                    yield await self._suspend_clarify(ctx, plan, clarify, text)
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
                                     f"说得再具体点我就能取消。",
                           "_outcome": "cancel_unresolved"}
                    return
                # R4.4 D5-2：诚实降级话术（含 fallback 低分不硬执行的场景），比「无法处理」更引导重说。
                # （批 8 ②：会话里有未解除安全告警的空计划轮在 `planning.build` 出口的安全闸二里已被
                # 改写成兜底谈话，与「这句本身是安全信号」同一条闸、同一份 `_talk_only_plan`——走到
                # 这里的空计划没有安全前提。）
                await _emit_engine_lifecycle(
                    ctx, "cloud.no_plan", "system.no_plan")
                yield {"kind": "final", "speech": "抱歉，我没听清您想让我做什么，可以换个说法吗。",
                       "_outcome": "no_plan"}
                return

            # 用系统持有的会话焦点补全 Planner 省略的结构化上下文，再记录 trace；
            # 这样观测到的是下游真正执行的计划，不是补全前的半成品。
            # W06 × W07：改口先合并——模型标了 correct 且单步与活动任务同 intent ⇒
            # 缺的槽从活动任务继承（新值优先），任务帧记成同一 task_id 的下一版。
            self._apply_task_patch(plan, working_set.focus)
            self._apply_focus_meta(plan, working_set.focus)
            # W13 / W14：这一轮的步是不是全都只会「说」（按声明不可能改变世界）
            ctx.answer_only = bool(plan.steps) and all(
                bool(getattr(s, "response_only", False)) for s in plan.steps)
            # W12：没有步骤承接的诉求（只对 simple 计划算；adaptive 的第二阶段本来就等观察）
            ctx.goal_gap = [] if plan.complexity != "simple" else goal_gap(plan, text)
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
                    # 追加批 F（F-1）：能力编号笔误被校验归位（模型原生选对与归位后选对分开看）
                    **({"ref_rehomed": ",".join(plan.ref_rehomed)}
                       if getattr(plan, "ref_rehomed", None) else {}),
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
                    # W02 可观测：本轮真正进 prompt 的上下文规模与历史裁剪（评审 W05
                    # 「按实际请求记录渲染规模」）。纯计数，不含内容。
                    **_context_stats_attrs(working_set),
                    # W06 / W07：对话行为标签与「这一轮是不是改口合并」（枚举值，不过内容门控）
                    **({"acts": ",".join(plan.acts)} if getattr(plan, "acts", None) else {}),
                    # W12 诉求账本可观测：模型报了几条诉求、每一步是否都填了 covers、判出几条漏承接
                    **({"goals_declared": len(plan.goals),
                        "covers_filled": "1" if all(
                            (getattr(s, "covers", None) or []) for s in plan.steps) else "0"}
                       if getattr(plan, "goals", None) else {}),
                    **({"goal_gap": len(ctx.goal_gap)} if ctx.goal_gap else {}),
                    **({"task_patch": "true", "task_revision": plan.task_patch.get("revision", 0)}
                       if getattr(plan, "task_patch", None) else {}),
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
            ctx.goal_gap = []          # T2 会补第二阶段，规划轮的账本不再作数
            if not agents:
                agents = await self.clients.list_agents()
            # 批 8 ①：T2 完成轮的收口与 E 路径同一份（清续接挂起 / 写焦点 / 补挂起软提醒）。
            # 此前这条出口在 `loop.run` 之后直接 return，三件事一件没做——continuity T63/T69：
            # 「接孩子后去万象城」四轮 T2 两次 navigate，「取消导航」答「当前没有正在进行的导航」。
            settled = self._loop_settler(ctx, plan, held_pending, mem_on)
            async for event in self.loop.run(
                    goal=plan.goal or text or plan.raw_text,
                    initial_plan=plan,
                    agents=agents,
                    ctx=ctx,
                    user_text=text or plan.raw_text,
                    seed_results=seed_results,
                    working_set=working_set,
                    show_process=show_process, thinking=complex_task,
                    settle=settled.settle, superseded=superseded):
                if event.get("kind") == "final":
                    if settled.done:
                        self._decorate_loop_final(event, plan, held_pending, ctx)
                    await obs_events.get_emitter("cloud").emit_span(
                        ctx.trace_id,
                        "aggregate",
                        attrs={"path": "adaptive"},
                    )
                yield event
            return

        # D0. 单步新规划走流式直通（task 4：开放域"边想边说"，秒级反馈）。
        # 仅对全新单步计划开启；确认续接/多步计划保持 executor 路径，不动 F1 闭环。
        # 单步**重**任务（独立的 info.search / trip.plan：`complex_task` 只因 heavy 而真）也走这里
        # （2026-09-13，性能评审 §3.4）：过程区的 execute 段在本路径里同样发（下方 show_process），
        # 而合成本身可以边出边流——真栈读数里深调研 700 字一次到齐、首段有效文本 5.7–17.5s，
        # 全花在等一次 2.7–6.1s 的合成上。多步 / adaptive 仍走 executor 与 T2。
        if (new_plan and plan.complexity == "simple" and len(plan.steps) == 1
                and not ctx.is_confirmation
                and plan.steps[0].kind == "agent"
                and plan.steps[0].deployment == "cloud"
                # M0a-3：capability 声明 require_confirm 的步不走流式直通——D0 会把
                # 流中 action 直接放行，绕开 executor 的确认兜底闸；走 executor 路径。
                and not plan.steps[0].require_confirm):
            step = plan.steps[0]
            # 单步流式直通的本体在 `_stream_single_step`（与 escalate 改派共用一份，2026-09-18）：
            # 过程区 running / 槽位解析 / 流事件转发 / response_only 闸 / 对账 / 来源 / span / 过程区 done 都在里面
            ssink: dict = {}
            async for ev in self._stream_single_step(step, ctx, show_process, ssink):
                yield ev
            stream: StreamTracker = ssink["stream"]
            final_sr: StepResult | None = ssink.get("final_sr")
            if final_sr is not None:
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
                               "speech": "这个需要联网查询，刚才没查成，请再说一次。",
                               "_outcome": "escalate_failed"}
                        return
                if final_sr.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                    yield await self._suspend(final_sr, results, plan, ctx)
                    return
                await self._settle_session(ctx, held_pending)
                if mem_on:
                    await self.context.update_focus(
                        ctx.session_id, focus_plan, results,
                        user_id=ctx.user_id,
                        exchange_id=ctx.request_id,
                        occupant_id=getattr(ctx, "occupant_id", ""))
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
                yield {"kind": "final", **final, "_outcome": outcome_of_results(results)}
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
                    yield {"kind": "final", **final, "_outcome": "uncertain"}
                    return
                # 只流了话术：话已经说了一半，重跑会播两遍。
                yield {"kind": "final", "speech": _STREAM_LOST_FINAL_SPEECH,
                       "_outcome": "stream_lost"}
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
                step_speech = strip_markdown_speech(step_result.speech)
                # 评审二轮 R4：E 路径的逐步播报同样在释放前过闸——谈话步 + 零动作的声称句不出门。
                talk_step = next((s for s in plan.steps if s.id == step_result.step_id), None)
                if (talk_step is not None and bool(getattr(talk_step, "response_only", False))
                        and not step_result.actions):
                    step_speech, _held = strip_execution_claims(step_speech)
                if step_speech:
                    yield {"kind": "speech", "delta": step_speech + "。"}

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
            ctx.goal_gap = []          # 同上：升级到 T2 之后由再规划补
            metrics.record_intent("reactive_upgrade", 0, True)
            logger.info("Reactive upgrade: simple→T2 for session %s",
                        ctx.session_id)
            if not agents:
                agents = await self.clients.list_agents()
            settled = self._loop_settler(ctx, plan, held_pending, mem_on)   # 批 8 ①，同 adaptive
            async for event in self.loop.run(
                    goal=plan.goal or text or plan.raw_text,
                    initial_plan=None,
                    agents=agents,
                    ctx=ctx,
                    user_text=text or plan.raw_text,
                    seed_results=results,
                    working_set=working_set,
                    show_process=show_process, thinking=complex_task,
                    settle=settled.settle):
                if event.get("kind") == "final":
                    if settled.done:
                        self._decorate_loop_final(event, plan, held_pending, ctx)
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
                exchange_id=ctx.request_id,
                occupant_id=getattr(ctx, "occupant_id", ""))  # 焦点态供下轮指代
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
        yield {"kind": "final", **final, "_outcome": outcome_of_results(results)}

    async def _voice_admitted(self, ctx: PlanContext, text: str, *, exit_name: str,
                              working_set=None, mem_on: bool = True) -> bool:
        """这句话是不是对助手说的——**规划之前就出口**的确定性分支的受话判定（唯一实现，评审四轮 R4-07）。

        语音来源 + 拒识开：一次**轻量判定**（`admission`：助手上一句 + 这句原话 → `addressed`，快模型档、关思考、判据与规划器
        `_ADDRESSED_SECTION` 同一组句子）。`6e64b767` 借的是一次完整规划，语音「确认」23 ms → 3142 ms。护栏与规划器同款：
        「记住…」这类祈使指令恒判受话、不问模型；按住说话（显式输入）判非受话再问一次，两次都否才算。
        其余来源 / 拒识关 ⇒ True，零调用。判定调用失败 / 解析不出 ⇒ 按受话处理（fail-open，同其余语音轮的缺省）。
        消费方：纯偏好陈述（评审二轮 R3）、焦点省略开关、确认、澄清选择、补槽。取消刻意不接：它只丢挂起不执行，误拒一句真「取消」
        反而把危险操作的挂起留下来。每次判定发一个 `cloud.voice_admission` span（出口 + 结果 + 耗时）。
        """
        source = ctx.prefs.get("input_source", "")
        if not (is_voice_input_source(source) and _reject_enabled()):
            return True
        started = time.monotonic()
        if is_memory_directive(text):
            verdict = True
        else:
            previous = await self._previous_assistant_text(ctx, working_set, mem_on)
            verdict = await self._bounded_judgment(text, previous, source)
            if verdict is False and not str(source).startswith("voice_"):
                verdict = await self._bounded_judgment(text, previous, source)
        addressed = verdict is not False
        await obs_events.get_emitter("cloud").emit_span(
            ctx.trace_id, "cloud.voice_admission",
            attrs={"exit": exit_name, "addressed": "1" if addressed else "0",
                   "verdict": "unavailable" if verdict is None else ("1" if verdict else "0"),
                   "admission_ms": str(round((time.monotonic() - started) * 1000)),
                   "owner": "cloud-engine"})
        return addressed

    async def _bounded_judgment(self, text: str, previous: str, source: str) -> bool | None:
        """一次轻量判定，超过 `_admission_timeout_s()` ⇒ None（判不出，按受话处理）。"""
        try:
            return await asyncio.wait_for(
                judge_addressed(self._admission_complete, text, previous, source),
                timeout=_admission_timeout_s())
        except asyncio.TimeoutError:
            logger.warning("admission judgment timed out after %.1fs", _admission_timeout_s())
            return None

    async def _admission_complete(self, messages: list[dict]) -> str:
        """轻量判定的模型调用：快档、温度 0、只要一个 JSON 对象。没有 LLM 客户端 ⇒ 空串（判定按不可用处理）。"""
        complete = getattr(self.clients, "llm_complete", None)
        if complete is None:
            return ""
        return await complete(messages, max_tokens=32, model="@fast", temperature=0.0)

    async def _previous_assistant_text(self, ctx: PlanContext, working_set=None,
                                       mem_on: bool = True) -> str:
        """助手上一句：有工作集就从它的历史取，没有（确认那条出口）就读一次最近一对历史；都读不到 ⇒ 空串。"""
        history = list(getattr(working_set, "history", None) or []) if working_set is not None else None
        if history is None:
            if not mem_on:
                return ""
            try:
                history, _state = await self.context._history(ctx, exchanges=1)
            except Exception as exc:            # 读不到就按「没有上一句」判，判定照做
                logger.debug("admission: history unavailable (%s)", exc)
                return ""
        for message in reversed(history or []):
            if isinstance(message, dict) and message.get("role") == "assistant":
                return str(message.get("text") or "")
        return ""

    @staticmethod
    async def _reject_not_addressed(ctx) -> AsyncIterator[dict]:
        """语音来源 + 受话判定为「不是对助手说的」⇒ 静默拒识（唯一出口：规划轮与纯偏好短路共用）。
        `_rejected` 让 `run()` 跳过落库；卡片让客户端知道这一轮被拒而不是丢了。"""
        await obs_events.get_emitter("cloud").emit_span(
            ctx.trace_id, "rejected",
            attrs={"reason": "not_addressed",
                   "intent": "system.rejected",
                   "owner": "cloud-engine"})
        yield {"kind": "final", "speech": "",
               "ui_card": {"type": "rejected", "reason": "not_addressed"},
               "_rejected": True, "_outcome": "not_addressed"}

    async def _constraint_ack(self, ctx, text: str) -> str:
        """纯偏好陈述的致谢：按**登记后的投影**念（合并了之前说过的），词表与焦点块共用。"""
        stated = constraints_in(text)
        merged: dict = {}
        try:
            focus = await self.context._load_focus(
                ctx.session_id, ctx.user_id,
                occupant_id=getattr(ctx, "occupant_id", ""))
            merged = dict(getattr(focus, "session_constraints", None) or {}) if focus else {}
        except Exception as exc:                       # 焦点读不到就只念这一句说的
            logger.debug("constraint ack: focus unavailable (%s)", exc)
        if not merged:
            merged = merge_constraints({}, stated)
        words = describe_constraints(merged)
        waived = [phrase_of(key, None) for key, value in stated.items()
                  if key != "others" and value is None and phrase_of(key, None)]
        if words and waived:
            return f"好的，{'、'.join(waived)}；这次{'、'.join(words)}，找地方的时候我按这个来。"
        if words:
            return f"好的，这次{'、'.join(words)}，找地方的时候我按这个来。"
        if waived:
            return f"好的，{'、'.join(waived)}。"
        return "好的，记下了。"

    async def _pending_digest(self, ctx) -> list[dict] | None:
        """挂起表 → 读出口能念的最小形状 `[{"what","phase"}]`；读不到返回 **None**。

        取 `load_all` 而不是 `load`：用户问的是「还**有没有**」，只答最新那一条
        等于把「还有几条」答错——挂起表本来就是多条（Q1-C）。

        ⚠ **「读不到」与「读到了、是空的」必须分开报**（C2 第二次沉淀的那条）：
        两者都返回 `[]` 就会让一次 Redis 故障说出「当前没有待确认的操作」——
        一句听起来很确定的假话，而用户正是靠它决定要不要重说一遍。
        """
        try:
            entries, state = await self.session.load_all_result(
                ctx.session_id, owner_user_id=ctx.user_id)
        except Exception as e:
            logger.debug("pending digest unavailable: %s", e)
            return None
        if state == PENDING_UNAVAILABLE:
            return None            # R8：读不到 ≠ 没有（`load_all` 的 `[]` 曾把两者混成一句）
        return self._digest_of(entries or [])

    @staticmethod
    def _digest_of(entries: list) -> list[dict]:
        """挂起表 → `[{"what","phase"}]`。目标描述与 `_pending_label` 同源同截断
        （goal，没有就退到任务起点原话）——同一条挂起在两处出现时必须是同一个称呼，
        否则用户会以为是两件事。"""
        out: list[dict] = []
        for state in entries:
            goal = ""
            try:
                plan = state.pending_plan or {}
                goal = str(plan.get("goal") or plan.get("raw_text") or "")
            except AttributeError:
                pass
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
        # 批 5 W17：账本**读不到**（历史读态 unavailable）与账本是空的分开报。
        # 数据源出口本就「有账才劫持」——读不到就是没账，照常进 Planner（域内直答仍在）；
        # 执行史没有第二个能答的人，读不到只能说「查不到」，绝不说「没有记录」。
        history_unavailable = (getattr(working_set, "history_state", "")
                               == memory_read.UNAVAILABLE)
        # **有账才劫持**：账本空说明这一轮之前没有任何外部数据卡，那就不是
        # 「系统持有的事实」，照常进 Planner——`info.stock` 那条域内直答
        # （重判 5：确定性面早就都在）比一句「我没记到」有用得多。
        # 这也是本族三条里唯一带这个条件的：挂起与执行史**没有第二个能答的人**。
        if (session_facts.is_provenance_question(text)
                and session_facts.latest_sources(history)):
            return ("data_provenance", session_facts.provenance_answer(history))
        if session_facts.is_execution_audit_question(text):
            if history_unavailable:
                return ("execution_audit", memory_read.HISTORY_UNAVAILABLE_SPEECH)
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

    async def _stream_single_step(self, step: Step, ctx: PlanContext,
                                  show_process: bool, sink: dict) -> AsyncIterator[dict]:
        """单步云端 Agent 的流式直通——D0（新单步计划）与 escalate 改派**共用这一份**（2026-09-18）。

        yield 过程区 running / speech / action / 过程区 done 事件；结果经 sink 回传：
          sink["stream"]   StreamTracker：流出面状态（能不能回退 unary / 结果确不确定），异常时也一定在
          sink["final_sr"] 已过 response_only 闸、已对账（Verifier，不重跑）、已标来源的 StepResult；流没给 final 时缺席
        调用方按 `allow_unary_fallback` / `outcome_uncertain` 决定回退还是收口，两条路径的判定表只有一份。

        流式直通**绕过 executor**，所以 executor 里挂的槽位解析在这条路上不生效。2026-08-13 实证：跨轮门店锚定
        挂在 `_resolve_slot_refs` 上，而 `luckin.menu`（require_confirm=false）恰好走这条路——诊断日志一行都
        没打出来，因为那个函数根本没被调用。**新增挂点必须枚举全部执行路径**（本项目第二次踩：M2 的 Verifier
        也在这里漏过一次）——把两条路收敛成这一个函数，正是为了让「枚举」只剩一处。
        """
        if show_process:
            yield self._progress("execute", phase_label(step.intent),
                                 status="running", step_id=step.id)
        self.executor._resolve_slot_refs(step, {}, ctx)
        started = time.monotonic()
        # B5 §4：流出面状态与「能不能回退 / 结果确不确定」的判定与 T2 共用一份（`stream_state`）。
        stream = StreamTracker()
        sink["stream"] = stream
        softener = MdDeltaSoftener()   # 流式增量剥 **/`（final 由 compose 出口彻底清理）
        # 评审二轮 R4：谈话步（按声明不可能执行）的增量在**首次对外释放之前**过句级闸——W14 只在 final
        # 上剥，「已为您关闭」「车窗。」两个增量早就到了屏幕 / TTS。判据与 final 那份同源，T2 同一条闸。
        gate = ExecutionClaimGate() if bool(getattr(step, "response_only", False)) else None
        final_sr: StepResult | None = None
        response_violation: StepResult | None = None
        try:
            # 总截止是 `call_agent_stream` 的缺省（clients.AGENT_STREAM_TIMEOUT_S，60s）：流式 Agent
            # 边生成边流，长回答会超过旧的 30s，然后走「只流了话术」那档、把已流出的整段替掉
            async for kind, payload in self.clients.call_agent_stream(
                    step.endpoint, step.intent, step.slots,
                    step_call_context(step, ctx), step.meta):   # W16-b：这一步的起点原话
                if kind == "speech":
                    payload = softener.feed(payload)
                    if gate is not None:
                        payload = gate.feed(payload)
                    # 记的是**软化之后**的增量：softener 会把悬空的 `*` 扣下一拍，那一拍用户什么都没看到。
                    # 「流出过输出」必须指用户真的收到了东西，否则一个空串就能把 unary 回退整条关掉。
                    stream.on_speech(payload)
                    if payload:
                        yield {"kind": "speech", "delta": payload}
                elif kind == "action":
                    if bool(getattr(step, "response_only", False)):
                        response_violation = self.executor._enforce_response_only(
                            step,
                            StepResult(
                                step_id=step.id,
                                status=StepStatus.OK,
                                actions=[{"type": "stream_action"}],
                            ),
                        )
                        continue
                    stream.on_action()
                    yield {"kind": "action", "action": payload}
                elif kind == "final":
                    final_sr = DagExecutor._to_result(step.id, payload)
        except Exception as e:
            logger.warning("Single-step stream failed (%s); caller decides fallback", e)
        if gate is not None:
            tail = gate.feed(softener.flush()) + gate.flush()
            stream.on_speech(tail)
            if tail:
                yield {"kind": "speech", "delta": tail}
            if gate.removed:
                logger.warning("response-only stream held %d execution claim sentence(s) "
                               "before release (%s)", gate.removed, step.intent)

        if response_violation is not None:
            final_sr = response_violation
        elif final_sr is not None:
            final_sr = self.executor._enforce_response_only(step, final_sr)
        if final_sr is None:
            return
        # 评审三轮 R3-04：发布即权威——闸拦过东西时，这一步的话术就是闸**实际放出去**的那份（全拦 ⇒ 诚实话术），
        # final / 落库不再对 Agent 的全文另用一套切法重剥（切法不同，用户听到的与存下来的就可能不同）。
        if gate is not None and gate.removed and final_sr.status == StepStatus.OK:
            final_sr.speech = gate.released or CLAIM_STRIPPED_SPEECH
        stream.on_final()
        # M2 Verifier：流式直通不经 executor._exec_step，必须在此显式对账，否则 capability 声明了
        # verification 却静默不生效（真栈首验实测：weather 走 D0 流式，一条 step.verify span 都没有）。
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
            duration_ms=(time.monotonic() - started) * 1000,
            attrs={"intent": step.intent, "agent_id": step.agent_id,
                   "kind": "agent", "deployment": "cloud", "via": "stream",
                   # W16-b 可观测（与 dispatcher / loop 同一格）：换了起点原话才出现
                   **({"raw_text_from": "origin"}
                      if step_call_context(step, ctx) is not ctx else {}),
                   # R3-04：句级等待的代价量出来——第一个增量进门到第一次放行（首字时延里闸占的那一截）
                   **_claim_gate_attrs(gate)})
        # 过程区的「完成」事件与 executor 路径同款（同一 step_id 合并 running→done）
        if show_process and final_sr.status in (
                StepStatus.OK, StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
            summary = step_summary(step, final_sr)
            if final_sr.status == StepStatus.NEED_CONFIRM:
                summary = (summary or "已生成方案") + "（待确认）"
            elif final_sr.status == StepStatus.NEED_SLOT:
                summary = summary or "需要补充信息"
            yield self._progress("execute", phase_label(step.intent),
                                 summary=summary, status="done", step_id=step.id)
        sink["final_sr"] = final_sr

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

        # Agent 的改派声明仍是非可信控制面输入：目标步已先经 manifest 权威装配，
        # 但在发过程事件、记 span 或交 executor 之前，还必须用服务端持有的本轮原话
        # 过同一份问句副作用闸。esc.reason / 原计划 goal / Agent speech 都不具备
        # 这项安全权威。两条消费路径（D0 与普通 executor）在此唯一接收点汇合。
        safety_origin_text = str(
            getattr(ctx, "safety_origin_text", "") or ""
        )
        kept, blocked = PlanBuilder._filter_safety_origin_side_effect_steps(
            steps, safety_origin_text,
        )
        if blocked:
            logger.warning(
                "Question-shaped utterance escalated into side-effecting step(s) %s; "
                "dropping before dispatch",
                [step.intent for step in blocked],
            )
        if kept:
            mini = Plan(
                steps=kept,
                raw_text=ctx.raw_text,
                safety_origin_text=safety_origin_text,
            )
        else:
            # 全部被拦时只允许 declaration-backed、未确认且能再次通过同一 guard
            # 的 response-only 能力回答。没有这样的出口就保持 sink 为空，零 dispatch。
            if not safety_origin_text:
                return
            mini = self.planner._talk_only_plan(safety_origin_text, agents)
            if mini is None:
                return
            mini.safety_origin_text = safety_origin_text
        steps = mini.steps
        # W13 / W14：改派后真正执行的是 mini 计划，谈话与否按它算；规划轮的诉求账本不再对应这些步
        ctx.answer_only = bool(steps) and all(
            bool(getattr(s, "response_only", False)) for s in steps)
        ctx.goal_gap = []
        show_esc_process = is_complex(mini)
        if show_esc_process:
            for s in mini.steps:
                s.meta = {**s.meta, "thinking": "on"}
        await obs_events.get_emitter("cloud").emit_span(
            ctx.trace_id, "escalate",
            attrs={"intent": esc["intent"], "reason": esc.get("reason", "")})
        results: list[StepResult] = []
        step0 = steps[0]
        running_sent = False
        if (len(steps) == 1 and step0.kind == "agent"
                and step0.deployment == "cloud" and not step0.require_confirm):
            # 改派后的单步云端 Agent 与 D0 **同一份**流式直通（2026-09-18）。此前一律经 executor 走 unary：
            # chitchat 流式起步 → `<search>` 改派 → info.search 整段只在 final 里到达，屏上一次性上屏
            # （真机 `app-2652cv` 三轮 783 / 497 / 311 字），而直连的 info.search 计划走 D0 是逐片流的。
            # **同一个 Agent 在两条路上必须同一种流法。** 资格条件与 D0 逐字相同（edge 步 / 需确认步照旧走 executor）。
            ssink: dict = {}
            async for ev in self._stream_single_step(step0, ctx, show_esc_process, ssink):
                yield ev
            running_sent = show_esc_process
            stream: StreamTracker = ssink["stream"]
            sr = ssink.get("final_sr")
            if sr is not None:
                if isinstance(sr.data, dict):
                    sr.data.pop("_escalate", None)   # 单跳预算：二跳声明不消费（结构性防环）
                results.append(sr)
                if sr.status in (StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT):
                    yield await self._suspend(sr, results, mini, ctx, prior=prior)
                    sink["suspended"] = True
                    return
                sink["results"] = results
                sink["plan"] = mini
                return
            if not allow_unary_fallback(stream.state):
                # 流出过输出却没收到 final：与 D0 同一档处置，不回退重跑（播两遍 / 副作用两遍）
                if outcome_uncertain(stream.state, stream.got_final):
                    sink["results"] = [await self.executor.stream_uncertain_result(step0, ctx)]
                    sink["plan"] = mini
                    return
                yield {"kind": "final", "speech": _STREAM_LOST_FINAL_SPEECH,
                       "_outcome": "stream_lost"}
                sink["suspended"] = True   # 终态已给出，调用方直接 return
                return
            # 零输出（Agent 不支持流式 / 建连失败）→ 与 D0 同款：安全回退到 executor 路径
        if show_esc_process and not running_sent:
            yield self._progress("execute", phase_label(steps[0].intent),
                                 status="running", step_id=steps[0].id)
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
        # I-024 第二层（2026-08-30）：修前**挂起轮从来不写焦点**——三个调用点都是
        # `yield await self._suspend(...)` 紧跟 `return`，`update_focus` 在它们**之后**。
        # 于是把「可见选择卡的候选」收进 `extract_focus`（§9.39 C）之后，
        # 那份候选**仍然到不了存储**：真栈实测重列仍答「没有您刚才那页选项的记录」。
        # ⇒ **抽取改对了，而调用方在它之前就返回了**——同一形态的第三例
        # （前两次：安全告警登记在 clarify/no_plan 的 return 之后；
        # `_refresh_active` 刷成了用户没看见的那份）。
        #
        # 当时判据面刻意窄到「只有挂起步自己出的是选择卡时才写」，普通挂起一概不写。
        # 评审四轮（`4438ea6b` 真栈 RS39，2026-09-25）证否了「普通挂起不写」：C5-B 让兄弟步的动作随挂起 final 发出去，
        # 而那一轮的事实一件都没登记——T2 循环续接导航之后又挂起，下一句「取消导航」答「当前没有正在进行的导航」，
        # 车其实在导航（「导航去公司，顺便提醒我买牛奶」、提醒缺时间也是同形）；同样丢掉的还有已执行的控制目标、原话里的告警与约束。
        # ⇒ 挂起轮与完成轮用**同一份抽取**登记本轮已执行的事实（挂起步本身不是 OK，抽取不会把它当成做完了）。
        # 仍然守住 C10-A「第 N 个只许指向用户最后一眼看到的那份列表」：这一轮 final 的卡片只是挂起步自己的，
        # 候选集**只收挂起步**（它是选择卡时就是原来那条；兄弟步的列表用户没以卡片看见过，不收）。
        if ctx.prefs.get("memory_enabled", "true") != "false":
            try:
                await self.context.update_focus(
                    ctx.session_id, plan, results,
                    user_id=ctx.user_id, exchange_id=ctx.request_id,
                    occupant_id=getattr(ctx, "occupant_id", ""),
                    candidates_from={step_result.step_id})
            except Exception as exc:            # 焦点是 best-effort，绝不拖垮挂起
                logger.debug("focus update on a suspend failed: %s", exc)
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
        # 本轮续接上来的那条要关掉——补槽追问「再问一次」是同一件事的下一步，
        # 不该在挂起表里占两格（Q1-C）。评审三轮 R3-05：「关旧」与「开新」是**同一次整表写**
        # （`save_pending_result(replaces=)`）；此前先删旧再存新，存失败时两头落空。它已被本轮消费，
        # 写成没写成都进 `closed_operation_ids`（没写成时 store 给它立进程内墓碑，不会被再确认一次）。
        replaces = ctx.pending_operation_id
        if replaces:
            if replaces not in ctx.closed_operation_ids:
                ctx.closed_operation_ids.append(replaces)
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
        # 评审三轮 R3-01 B：确认卡上那句「这次要确认的是什么」（Registry 能力描述 + 槽值）同时落进挂起态，
        # 作为点名确认的裁决面——服务端生成、不经模型。一次 Registry 往返，卡片与挂起态共用。
        describe = (await self._capability_describer()
                    if step_result.status == StepStatus.NEED_CONFIRM else None)
        pending_state = SessionState(
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
            action_summary=(contracts.action_summary(pending_step, describe)
                            if describe is not None else ""),
            # 评审四轮 R4-01：提出这条挂起的那一轮——纯应答只在它就是最近那个提示时才授权
            prompt_exchange_id=str(ctx.request_id or ""),
        )
        save_status, evicted = await self.session.save_pending_result(
            ctx.session_id, pending_state, replaces=replaces)
        if save_status != SAVE_OK:
            # 两种都 fail-closed（不给确认条、不执行），但**说的是不同的事**（评审二轮 R8）：
            # 写栅栏是隐私清理正在进行，连不上后端只是这一步存不下。
            fenced = save_status == SAVE_FENCED
            return {
                "kind": "final",
                "speech": ("正在清除你的数据，这次操作没有保存，请稍后重新发起。" if fenced
                           else "这一步需要等你确认，但会话状态暂时存不下来，"
                                "所以我没有往下执行，稍后再说一次。"),
                "follow_up": ("数据清除完成后可以重新尝试。" if fenced
                              else "稍后再说一次就行。"),
                "actions": [],
                "ui_card": None,
                "need_confirm": False,
                "_outcome": "store_fenced" if fenced else "store_unavailable",
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
        # AR05：把「正在确认什么 / 还缺什么 / 到什么时候」摆成结构化契约，
        # 让客户端不必再从话术里用正则猜。截止时刻取 SessionStore 落盘后的绝对时刻
        # （`pending_state.expires_at`），客户端只读不续期。
        final_event = {
            "kind": "final",
            "speech": (brief + (step_result.speech or "")) if brief else step_result.speech,
            "follow_up": follow_up,
            "actions": actions,
            "ui_card": step_result.ui_card,
            "need_confirm": step_result.status == StepStatus.NEED_CONFIRM,
            "operation_id": operation_id,
            "_outcome": ("pending_confirm" if step_result.status == StepStatus.NEED_CONFIRM
                         else "pending_slot"),
        }
        if step_result.status == StepStatus.NEED_CONFIRM:
            final_event["confirm_policy"] = contracts.build_confirm_policy(
                operation_id=operation_id, step=pending_step, state=pending_state,
                # 回退原话取**任务起点**（`plan.safety_origin_text`，跨挂起不变），
                # 不是本轮的「确认」二字；两者都没有才退到本轮原话。
                user_text=(getattr(plan, "safety_origin_text", "")
                           or ctx.safety_origin_text or ctx.raw_text),
                # 打磨批 G（裁决 J4）：人话摘要取 Registry 目录里这条能力的描述
                # （端侧由 commands.yaml 机械生成，云侧不抄词表）；取不到就回退原话
                describe=describe)
        elif step_result.missing_slots:
            final_event["slot_request"] = contracts.build_slot_request(
                operation_id=operation_id, step=pending_step,
                step_result=step_result, state=pending_state)
        return final_event

    @staticmethod
    def _clarify_target(entries: list, operation_id: str, pending):
        """这一轮该对着哪条 `wait_clarify` 判选择：带寻址键只认它；不带认最近一条澄清挂起。"""
        wanted = str(operation_id or "").strip()
        if wanted:
            return pending if (pending is not None
                               and getattr(pending, "phase", "") == "wait_clarify") else None
        for state in reversed(entries or []):
            if getattr(state, "phase", "") == "wait_clarify":
                return state
        return None

    @staticmethod
    def _clarify_position(text: str) -> int | None:
        """裸序数 / 纯数字 /「N 号」→ 第几项（1 起）；判据唯一一份在 `reply_position`（规划的序数写步闸读同一份）。"""
        return reply_position(text)

    @staticmethod
    def _clarify_reply_is_positional(text: str) -> bool:
        """这句话是不是一个**位置性**的选择（「第二个 / 2 / 二号方案」）——它只说「第几个」，不说是哪个问题的第几个。"""
        return PlannerEngine._clarify_position(text) is not None

    async def _clarify_is_latest_prompt(self, ctx: PlanContext, entries: list, state,
                                        mem_on: bool) -> bool:
        """这条 wait_clarify 是不是**最近那个提示**（评审四轮 R4-02）：它是挂起表最新一条，且焦点里没有比它更新的候选列表。

        修前不带寻址键的「2 / 第二个」一律归最新一条澄清，哪怕之后又出了补槽问题或刚列过一份新候选——旧问题无条件优先于
        新问题（真栈 `ecbeed28` RS33 三趟都答成旧澄清的第二项）。挂起创建时刻 = `expires_at - ttl_seconds`
        （SessionStore 首次落盘时算）；候选批带 `ts`。证明不了（旧记录没盖截止时刻 / 记忆关 / 焦点读不到）⇒ 按修前行为算它最新。
        """
        if entries and entries[-1] is not state:
            return False
        expires = float(getattr(state, "expires_at", 0.0) or 0.0)
        ttl = float(getattr(state, "ttl_seconds", 0) or 0)
        if expires <= 0 or ttl <= 0 or not mem_on or not getattr(self.context, "session", None):
            return True
        focus = await self.context._load_focus(
            ctx.session_id, ctx.user_id, occupant_id=getattr(ctx, "occupant_id", ""))
        sets = list(getattr(focus, "candidate_sets", None) or []) if focus is not None else []
        newest = max((float(s.get("ts") or 0.0) for s in sets if isinstance(s, dict)),
                     default=0.0)
        return newest <= expires - ttl

    @staticmethod
    def _resolve_clarify_choice(text: str, state) -> dict | None:
        """这句话选的是哪一项（W10）：裸序数 / 纯数字 / 选项 label / 选项 send_text 原文
        （老客户端回发的正是它）。解不出、序数越界 ⇒ None（不猜）。"""
        options = [o for o in (getattr(state, "clarify", None) or {}).get("options") or []
                   if isinstance(o, dict)]
        if not options:
            return None
        t = str(text or "").strip().rstrip("。！!？?").strip()
        if not t:
            return None
        index = PlannerEngine._clarify_position(t)
        if index is not None:
            return options[index - 1] if 1 <= index <= len(options) else None
        for option in options:
            if t in (str(option.get("label") or "").strip(),
                     str(option.get("send_text") or "").strip()):
                return option
        return None

    async def _suspend_clarify(self, ctx: PlanContext, plan: Plan, clarify: dict,
                               text: str) -> dict:
        """澄清轮落一条 `wait_clarify` 挂起并构造 final（W10）。"""
        # 续接上来的那条已经消费完，不在表里占两格（同 `_suspend`，评审三轮 R3-05：关旧开新同一次整表写）
        replaces = ctx.pending_operation_id
        if replaces:
            if replaces not in ctx.closed_operation_ids:
                ctx.closed_operation_ids.append(replaces)
            ctx.pending_operation_id = ""
        operation_id = f"op-{uuid.uuid4().hex[:16]}"
        state = SessionState(
            phase="wait_clarify",
            owner_user_id=ctx.user_id,
            operation_id=operation_id,
            pending_plan={
                "steps": [], "raw_text": str(text or ""),
                "goal": str(getattr(plan, "goal", "") or text or ""),
                "safety_origin_text": str(ctx.safety_origin_text or text or ""),
            },
            clarify={"question": str(clarify.get("question") or ""),
                     "options": [dict(o) for o in (clarify.get("options") or [])
                                 if isinstance(o, dict)]},
            prompt_exchange_id=str(ctx.request_id or ""),     # 评审四轮：提出它的那一轮
        )
        save_status, evicted = await self.session.save_pending_result(
            ctx.session_id, state, replaces=replaces)
        if save_status != SAVE_OK:
            # 评审三轮 R3-05 顺手：这里此前把**任何**保存失败都说成「正在清除你的数据」——二轮 R8 只修了 `_suspend`
            fenced = save_status == SAVE_FENCED
            return {"kind": "final",
                    "speech": ("正在清除你的数据，这次操作没有保存，请稍后重新发起。" if fenced
                               else "我想先问清楚你要哪一种，但会话状态暂时存不下来，稍后再说一次。"),
                    "actions": [], "ui_card": None,
                    "_outcome": "store_fenced" if fenced else "store_unavailable"}
        final = {
            "kind": "final",
            "speech": clarify["question"],
            "actions": [],
            "operation_id": operation_id,
            "ui_card": contracts.build_clarify_card(
                operation_id=operation_id, clarify=clarify, state=state),
            "_outcome": "clarify",
        }
        if evicted is not None:
            ctx.closed_operation_ids.append(evicted.operation_id)
            final["follow_up"] = self._append_hint(
                "", f"（{self._pending_label(evicted)}已过期，需要的话再说一次。）")
        return final

    async def _capability_describer(self):
        """intent → Registry 目录里这条能力的 `description`（打磨批 G）。

        目录是端侧 / 各 Agent 注册时带上来的——端侧车控的描述由 `capabilities.py::_describe`
        从 `commands.yaml` 机械生成（「打开后备箱」），所以云侧**不需要也不许**另抄一份对象名。
        Registry 取不到时返回 None：摘要回退用户原话，挂起本身不受影响（best-effort）。
        """
        try:
            agents = await self.clients.list_agents()
        except Exception as exc:  # noqa: BLE001 —— 目录只影响摘要人话，绝不拖垮挂起
            logger.debug("capability describer unavailable, falling back to utterance: %s", exc)
            return None
        table: dict[str, str] = {}
        for agent in agents or []:
            manifest = getattr(agent, "manifest", None)
            for cap in (getattr(manifest, "capabilities", None) or []):
                intent = str(getattr(cap, "intent", "") or "")
                desc = str(getattr(cap, "description", "") or "").strip()
                if intent and desc and intent not in table:
                    table[intent] = desc
        return table.get

    @staticmethod
    def _pending_label(state) -> str:
        """挂起的人话名字：取 pending_plan.goal，没有就退回中性说法。"""
        goal = ""
        try:
            plan = state.pending_plan or {}
            # goal 是模型的一句话目标；没有时退回**任务起点原话**（W01 的消歧问句要
            # 念得出每一条是什么，「更早那条」在两条并列时等于没说）。
            goal = plan.get("goal") or plan.get("raw_text") or ""
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

    async def _close_pending(self, ctx: PlanContext, pending) -> str:
        """关掉一条挂起，并记进本轮的 `closed_operation_ids`（Q1-C）；返回删除结果（`session.CLEAR_*`）。

        **只清这一条**：挂起表里其余的与本轮无关，清掉它们等于把用户还惦记着的
        另一件事悄悄抹掉——正是单槽时代那个语义（`_suspend` 覆盖旧挂起）的换皮。

        评审三轮 R3-05：删除有四种结果，四种都意味着「这条不会再被执行」——删掉了 / 本来就不在 / 隐私写栅栏挡着读 /
        后端删不掉但 store 已立进程内墓碑——所以都进 `closed_operation_ids`；**话术**按能证明的那一种说（调用方读返回值）。
        """
        if pending is None:
            return ""
        op = getattr(pending, "operation_id", "") or ""
        state = await self.session.clear_result(
            ctx.session_id, owner_user_id=ctx.user_id, operation_id=op)
        if state == CLEAR_UNAVAILABLE:
            logger.warning("pending %s could not be deleted; tombstoned in-process", op[:16])
        if op and op not in ctx.closed_operation_ids:
            ctx.closed_operation_ids.append(op)
        return state

    def _loop_settler(self, ctx: PlanContext, plan: Plan, held_pending, mem_on: bool):
        """T2 完成轮的收口回调（批 8 ①）。返回一个带 `settle` 协程与 `done` 旗子的小对象：
        loop 在合成 final 之前调 `settle(steps, results)`；旗子让调用方只给**完成轮**的 final
        补挂起软提醒（挂起路径不调 settle，旗子保持 False，`_suspend` 自己出的 final 一个字不动）。

        为什么不在 loop 里直接写焦点：焦点 / 挂起表 / 软提醒都是 engine 的状态，loop 只知道
        自己跑了哪些步；E 路径的顺序是 `_settle_session → update_focus → compose → 补提醒`，
        这里逐字同序。"""
        engine = self

        class _Settler:
            done = False

            async def settle(self, loop_steps, results):
                await engine._settle_session(ctx, held_pending)
                if mem_on:
                    # 焦点按 plan.steps 抽（候选批 / 任务帧 / 目的地），再规划批的步不在初计划里
                    # ⇒ 合成一份「本轮真正跑过的全部步」的视图；raw_text / acts / task_patch 照旧。
                    merged = list(plan.steps)
                    merged.extend(step for step in loop_steps
                                  if not any(step is kept for kept in merged))
                    try:
                        await engine.context.update_focus(
                            ctx.session_id, dataclasses.replace(plan, steps=merged), results,
                            user_id=ctx.user_id, exchange_id=ctx.request_id,
                            occupant_id=getattr(ctx, "occupant_id", ""))
                    except Exception as exc:        # 焦点是 best-effort，绝不拖垮已完成的回答
                        logger.warning("focus update after the T2 loop failed: %s", exc)
                self.done = True

        return _Settler()

    def _decorate_loop_final(self, final: dict, plan: Plan, held_pending, ctx: PlanContext) -> None:
        """T2 完成轮 final 的收尾修饰，与 E 路径逐字同款：挂起软提醒 / 放弃提示 / 情绪。"""
        self._append_pending_hint(final, held_pending)
        self._append_abandoned_hint(final, ctx.abandoned_pending_label)
        if getattr(plan, "emotion", ""):
            final["emotion"] = plan.emotion

    async def _settle_session(self, ctx: PlanContext, held_pending) -> None:
        """本轮正常收口时的会话清理（R2）：插话轮（held_pending 非空）**不清挂起**——
        用户 TTL 内回头「确认」/裸答案仍可续接。

        Q1-C 后清理范围收窄成**本轮真正续接上的那一条**（`ctx.pending_operation_id`）：
        以前这里 `clear()` 清的是整个 key，多槽下会连带抹掉两件不相干的挂起。
        不刷新 TTL：挂起窗口以首次挂起时刻起算，插话不无限续命。"""
        if held_pending is None and ctx.pending_operation_id:
            # R3-05：续接执行完却删不掉（后端那一刻不可用）⇒ store 立进程内墓碑——再说一次「确认」不会把它重复执行
            state = await self.session.clear_result(
                ctx.session_id, owner_user_id=ctx.user_id,
                operation_id=ctx.pending_operation_id)
            if state == CLEAR_UNAVAILABLE:
                logger.warning("settled pending %s could not be deleted; tombstoned in-process",
                               ctx.pending_operation_id[:16])
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
    def _pending_ask_word(state) -> str:
        """这条挂起在等用户做什么（软提醒 / 保留话术共用一份）。"""
        phase = getattr(state, "phase", "")
        return ("确认" if phase == "wait_confirm"
                else "选择" if phase == "wait_clarify"
                else "继续补充")

    @staticmethod
    def _append_pending_hint(final: dict, held_pending) -> None:
        """插话轮的 final 补软提醒：告知挂起还在（Q1 决策的配套——插话后 HMI 确认条
        已被新消息顶掉，不提示的话用户忘了挂起、说「确认」会显得凭空执行）。原地改 final。

        AR05 §4.3：软提醒之外**再给一个结构化事实** `held_operation_ids`——话术是给人听的，
        客户端要撤哪一条追问得有个 id。两者同一处产出，不会一个说了一个忘了。"""
        if held_pending is None or not isinstance(final, dict):
            return
        held_id = str(getattr(held_pending, "operation_id", "") or "")
        if held_id:
            existing = list(final.get("held_operation_ids") or [])
            if held_id not in existing:
                existing.append(held_id)
            final["held_operation_ids"] = existing
        goal = ""
        try:
            goal = (held_pending.pending_plan or {}).get("goal") or ""
        except AttributeError:
            pass
        what = f"「{goal[:20]}」" if goal else "刚才的操作"
        hint = f"对了，{what}还在等你{PlannerEngine._pending_ask_word(held_pending)}。"
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
        # 评审 F01（2026-09-19）：**问句形态永远不是授权**。「可以吗 / 确认吗 / 行吗」
        # 里的肯定词占据了整句，旧判据照样判 yes——它没把「询问」和「授权」分开。
        # 判据复用 `runtime.question_shape`（唯一实现、零领域词）：礼貌请求
        # （「请确认」带祈使标记）仍是指令，不在否决面内。
        if is_non_directive_question(t):
            return None
        # 「词占据整句」（评审二轮 R1）：全句只能由肯定词与语气面组成，剥完一个实质字都不剩。
        # 「第二天行程换一个」「可以换X」「行程」「确认函」「可以改」都剩了实质内容 ⇒ 不是确认。
        if PlannerEngine._bare_affirmation(t):
            return "yes"
        return None

    @staticmethod
    def _bare_affirmation(t: str) -> bool:
        """整句是否只由肯定词 + 语气面组成，**且至少有一个肯定词**（「好的，确认吧」「嗯可以」「行啊」→ True；
        「行程」「确认函」「好像不对」→ False；「啊 / 唉 / 请 / 那 / 。」→ False——评审三轮 R3-01 A：
        语气面只能修饰，不能独立授权，修前剥空即 True）。剥离循环在 `runtime.affirmation.consists_of`。"""
        return consists_of(t, _YES_WORDS_BY_LEN)

    @staticmethod
    def _address_pending(entries: list, operation_id: str):
        """寻址键非空 ⇒ 只认精确命中（Q1-B，对不上就是 None，绝不回落）；
        空 ⇒ 最近一条（语音兜底 / 旧客户端的既有语义）。"""
        wanted = str(operation_id or "").strip()
        if wanted:
            return next((s for s in entries if s.operation_id == wanted), None)
        return entries[-1] if entries else None

    @staticmethod
    def _resolve_spoken_confirm(text: str, flagged: bool, entries: list,
                                edge_intent: str = "", ack_binds: bool = True) -> "_SpokenConfirm":
        """无寻址键的确认在挂起表里**指向谁**（W01）。

        返回 `kind`：
          · `""`           —— 这句话不是确认，本函数不表态；
          · `"one"`        —— 唯一所指（`target`）；
          · `"ambiguous"`  —— ≥2 条 wait_confirm 且没点名 / 点名同时兼容多条（`candidates`）；
          · `"none"`       —— 是确认词，但一条 wait_confirm 都没有；
          · `"named_miss"` —— 「确认+点名」一条都没召回到（按插话走）；
          · `"mismatch"`   —— 点名召回到了候选，但没有一条与点名兼容（评审三轮 R3-01 B：`candidates` =
                              召回到的那些、`named` = 点名余量）。**不是授权**，确定性出口念出等确认的是什么；
          · `"asking"`     —— 「可以吗 / 确认吗 / 行不行」：在**问**有没有 / 能不能确认
                              （`candidates` = 全部 wait_confirm）。真栈 2026-09-19 CF7：
                              这句交给规划会落 chitchat，1/2 次答「可以，已为您执行」——
                              零动作却声称做了。系统自己知道挂着什么，不该让模型答。

        点名通道两段（R3-01 B）：肯定词开头、余量 ≥2 字 ⇒ **召回**（`_pending_names`，只找候选）⇒ **裁决**
        （`_named_confirm_compatible`：点名的每一样东西都得是这一步已校验摘要里本来就有的；`edge_intent` =
        端侧对整句的解析，点出另一个 intent 一票否决、从不授权）。序数（「第一个」）不接——它在 wait_slot
        语境里是选择卡的答案。

          · `"ack"`        —— 评审四轮 R4-01：**纯应答**（「好的 / 嗯 / 可以」，不含确认 / 下单 / 支付 / 选定类），而最新一条
                              挂起不是待确认、或它不是最近那个提示（`ack_binds=False`，engine 读历史判）。它回答的是最近那一问，
                              不是这笔事务：调用方按插话保留挂起、交规划。「确认」这类事务词不受影响。
        """
        confirms = [s for s in entries if getattr(s, "phase", "") == "wait_confirm"]
        t = (text or "").strip().lower()
        if confirms and not flagged and PlannerEngine._is_confirm_ask(t):
            return _SpokenConfirm("asking", candidates=confirms)
        if flagged and not is_standalone_cancel(t):
            bare, remainder = True, ""
        else:
            bare, remainder = PlannerEngine._split_confirm_prefix(t)
            if not bare and not remainder:
                return _SpokenConfirm("")
        if bare and not flagged and is_bare_acknowledgment(t):
            newest = entries[-1] if entries else None
            if (ack_binds and newest is not None
                    and getattr(newest, "phase", "") == "wait_confirm"):
                return _SpokenConfirm("one", target=newest)
            return _SpokenConfirm("ack")
        if remainder:
            if not confirms:
                # 「好的，明天早上八点」——没有任何待确认时，肯定词开头的长句是**补槽答案 /
                # 普通请求**，本函数不表态（否则 wait_slot 的答案会被当成点名落空的确认）。
                return _SpokenConfirm("")
            hits = [s for s in confirms
                    if PlannerEngine._pending_names(s, remainder)]
            if not hits:
                return _SpokenConfirm("named_miss")
            compatible = [s for s in hits if PlannerEngine._named_confirm_compatible(
                s, remainder, edge_intent)]
            if len(compatible) == 1:
                return _SpokenConfirm("one", target=compatible[0])
            if len(compatible) >= 2:
                return _SpokenConfirm("ambiguous", candidates=compatible)
            return _SpokenConfirm("mismatch", candidates=hits, named=remainder)
        if len(confirms) == 1:
            return _SpokenConfirm("one", target=confirms[0])
        if len(confirms) >= 2:
            return _SpokenConfirm("ambiguous", candidates=confirms)
        return _SpokenConfirm("none")

    @staticmethod
    def _is_confirm_ask(t: str) -> bool:
        """「可以吗 / 确认吗 / 行不行」：问句形态且剥掉尾词后只剩一个肯定词 / 「X不X」问法。"""
        if not t or not is_non_directive_question(t):
            return False
        core = _CONFIRM_ASK_TAIL_RE.sub("", t).strip()
        return bool(core) and (core in _YES_WORDS or core in _CONFIRM_ASK_FORMS)

    @staticmethod
    def _split_confirm_prefix(t: str) -> tuple[bool, str]:
        """`(是不是裸确认, 点名余量)`。问句形态先否决（同 `_confirm_reply`）。

        「确认」→ (True, "")；「确认明晚8点那个」→ (False, "明晚8点")；
        「确认吧」→ (True, "")（语气尾剥掉）；「第二天行程换一个」→ (False, "")。
        """
        if not t or is_standalone_cancel(t) or is_non_directive_question(t):
            return False, ""
        # 裸确认 = 肯定词 + 语气面、剥完什么都不剩（「好的，确认吧」也在此列——此前被拆成点名「确认」）。
        if PlannerEngine._bare_affirmation(t):
            return True, ""
        # 「肯定词 + 余量」：「确认订单」余量 2 字是点名；「行程」剩一个实质字「程」，
        # 既不是语气尾也不够点名 ⇒ 两边都不是（评审二轮 R1：剩一个实质字也不是裸确认）。
        for word in _YES_WORDS_BY_LEN:
            if t.startswith(word):
                rest = _CONFIRM_FILLER_RE.sub("", t[len(word):])
                if len(rest) >= 2:
                    return False, rest
                return False, ""
        return False, ""

    @staticmethod
    def _pending_names(state, needle: str) -> bool:
        """**召回**：余量是否可能在说这条挂起——goal、任务起点原话、已校验步骤摘要三处。

        整串子串命中，或余量的任一二元片段命中（「咖啡订单」对「订一杯拿铁咖啡」靠「咖啡」）——
        取消 / 确认的点名都是用户随口的称呼，很少与 goal 逐字相同；单字不算（评审二轮 R2）。
        ⚠ 这只是召回（评审三轮 R3-01 B）：「关闭后备箱」对「打开后备箱」照样召回得到，授权与否由裁决判。"""
        haystacks = [h.lower() for h in PlannerEngine._naming_haystacks(state) if h]
        needle = str(needle or "").strip().lower()
        if len(needle) < 2 or not haystacks:
            return False
        grams = {needle} | {needle[i:i + 2] for i in range(len(needle) - 1)}
        return any(g in h for h in haystacks for g in grams)

    @staticmethod
    def _naming_haystacks(state) -> list[str]:
        plan = getattr(state, "pending_plan", None) or {}
        return [str(plan.get("goal") or ""), str(plan.get("raw_text") or ""),
                str(getattr(state, "action_summary", "") or "")]

    @staticmethod
    def _pending_step(state):
        """挂起那一步的 `(intent, slots)` 视图（从持久化计划里取；取不到返回 None）。"""
        plan = getattr(state, "pending_plan", None) or {}
        wanted = str(getattr(state, "pending_step_id", "") or "")
        for item in plan.get("steps") or []:
            if isinstance(item, dict) and str(item.get("id") or "") == wanted:
                slots = item.get("slots") if isinstance(item.get("slots"), dict) else {}
                return SimpleNamespace(intent=str(item.get("intent") or ""), slots=dict(slots))
        return None

    @staticmethod
    def _named_confirm_compatible(state, needle: str, edge_intent: str = "") -> bool:
        """**裁决**：点名余量与这条挂起的已校验步骤是否兼容（评审三轮 R3-01 B）。

        事实只取 `state.action_summary`——挂起那一刻服务端生成的能力描述 + 槽值（确认卡上给用户看的同一句）。
        **LLM goal 与任务原话只进召回，不进裁决**：原话里可能正有与这一步相反的词（「打开后备箱，别关闭车窗」时
        「确认关闭车窗」会被原话覆盖）。摘要为空（旧记录现取也取不到）⇒ 不兼容：点名不能授权，裸「确认」仍可。
        `edge_intent`（端侧对整句的规则解析）只做否决：它点出另一个 intent ⇒ 不兼容；与这一步相同也不替摘要授权。
        """
        step = PlannerEngine._pending_step(state)
        if edge_intent and step is not None and step.intent and edge_intent != step.intent:
            return False
        summary = str(getattr(state, "action_summary", "") or "")
        return bool(summary) and _naming_coverage(needle, [summary]) >= 1.0

    @staticmethod
    def _best_named(hits: list, needle: str) -> list:
        """点名取消在 ≥2 条召回里挑**覆盖度唯一最高**的那条（R3-01 B′）；并列返回并列的全部（调用方问一次）。

        取消方向 fail-safe，覆盖面用召回那三处（goal / 原话 / 摘要）。「取消刚才拿铁咖啡」在拿铁 / 美式并存时
        不再白问一次；「取消刚才那杯咖啡」两条同分，照旧问。"""
        if len(hits) < 2:
            return list(hits)
        scored = [(_naming_coverage(needle, PlannerEngine._naming_haystacks(s)), s) for s in hits]
        top = max(score for score, _ in scored)
        return [s for score, s in scored if score == top]

    async def _ensure_confirm_facts(self, entries: list, text: str, flagged: bool) -> None:
        """旧记录（本字段诞生之前挂起的）没有 `action_summary`：点名确认时现取一次 Registry 描述补上（只在内存里）。

        只在这句话真有点名余量时才去取——裸「确认」与普通插话不多一次 Registry 往返。取不到就留空，裁决判不兼容。"""
        if flagged:
            return
        missing = [s for s in entries or []
                   if getattr(s, "phase", "") == "wait_confirm"
                   and not getattr(s, "action_summary", "")]
        if not missing:
            return
        _bare, remainder = self._split_confirm_prefix((text or "").strip().lower())
        if not remainder:
            return
        describe = await self._capability_describer()
        if describe is None:
            return
        for state in missing:
            step = self._pending_step(state)
            if step is not None:
                state.action_summary = contracts.action_summary(step, describe)

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
    def _intercepts_as_confirm(text: str) -> bool:
        """这句话在**没有挂起 / 挂起表读不到**时要被拦成「当前没有待确认的操作 / 暂时读不到」：裸确认 / 裸取消，但**纯应答除外**。

        评审四轮 R4-01：「好的 / 嗯 / 可以」回答的是最近那一问——它可能是闲聊里的「还要继续讲吗」，交规划去接；只有带事务词的
        （「确认 / 好的，确认吧 / 下单」）才是冲着一笔事务来的，照旧诚实报过期（那道闸挡的是「确认」被借历史重规划成上一意图）。"""
        return PlannerEngine._is_bare_confirm_word(text) and not is_bare_acknowledgment(text)

    async def _pending_is_latest_prompt(self, ctx: PlanContext, state, mem_on: bool) -> bool:
        """这条挂起是不是**最近那个提示**：提出它的那一轮就是最近一轮（评审四轮 R4-01）。

        · 挂起没盖提出它的那一轮（旧记录）⇒ 证明不了 ⇒ False；
        · 端侧签发了「上一轮是本地轮次」⇒ 挂起之后插过话 ⇒ False；
        · 记忆关 / 客户端根本没有轮次读取能力 ⇒ 没有账可对，按修前行为 ⇒ True；
        · 读一次历史：最近一轮的 exchange 就是它 ⇒ True；读不到 ⇒ False（fail-safe：纯应答不授权，显式「确认」照旧可用）。
        """
        stamp = str(getattr(state, "prompt_exchange_id", "") or "").strip()
        if not stamp:
            return False
        if str(getattr(ctx, "previous_local_exchange", "") or "").strip():
            return False
        if not mem_on:
            return True
        read_state, latest = await self._latest_exchange(ctx)
        if read_state == memory_read.OFF:
            return True
        return read_state != memory_read.UNAVAILABLE and latest == stamp

    async def _latest_exchange(self, ctx: PlanContext) -> tuple[str, str]:
        """`(读态, 最近一轮的 exchange id)`：读一次最近一对历史（`runtime.memory_read` 的四态）。"""
        turns, read_state = await self.context._history(ctx, exchanges=1)
        for turn in reversed(turns or []):
            exchange = str(turn.get("exchange_id") or "").strip() if isinstance(turn, dict) else ""
            if exchange:
                return read_state, exchange
        return read_state, ""

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
        # 安全信号是**系统持有的事实**，定义上就不是某个待补槽的值（2026-09-19 真栈，
        # QA T47 收口）：充电规划的 `dest_choice` 挂起把「检查过了，机油灯已经灭了，恢复正常了」
        # **整句填进 `destination`**，答成「暂时无法获取前往…的路线」——而解除扫描只在云侧
        # 规划轮跑，这一句被挂起吃掉 ⇒ 焦点里的机油灯永远清不掉（3 趟里 1 趟）。反方向同样
        # 成立且更危险：挂起期间说「机油灯亮了」会被当地址吞掉、**登记不上**——C1-B 那条
        # 「登记挂在输入上」被一次挂起绕过。判据复用 `runtime.safety_signal`（唯一实现），
        # 排在形状判据之前：任何槽的形状都不该把一条安全陈述认作自己的值。
        if alert_level(t) or driver_state(t) or alert_resolved(t):
            return True
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
        # 「再给我看一眼刚才那份可选项」**定义上就不是某个待补槽的值**（2026-08-30）。
        # 真栈实录：充电规划出了 `dest_choice` 选择卡之后，
        # 「请重新列出刚才可以选择的项目」被整句填进 `destination` 槽，答成
        # 「暂时无法获取前往**请重新列出刚才可以选择的项目**的路线」（2/2 复现）
        # ——与 `index` 那个黑洞同族，只是换了个槽。
        # ⚠ **只收「重列」不收序数**：裸「第一个」正是选择卡的**合法答案**
        # （上面那段 `*_choice` 白名单就是为它写的），收进来会把整条选店流程修死。
        # 词表复用 `candidate_query.RELIST_RE`——同 C3-B 那笔，判据只许有一份。
        if candidate_query.RELIST_RE.search(t):
            return True
        if any(k in t for k in ("为什么", "为何", "什么原因")):
            return True
        # 批 5 W18 真栈（continuity T21）：路况补槽挂起把「把全车门解锁」整句当路线吞掉，
        # 答「为您找到 0 个把全车门解锁 路况」——用户的车控指令就此消失（方向 fail-safe，但
        # 「未完成的不会失踪」被违反）。「把 / 将 + …」处置式与「请 / 麻烦 + …」礼貌祈使都是
        # 新指令，不是槽值；判据在 `runtime.question_shape`（唯一实现，零领域词）。
        if is_imperative_opening(t):
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
            # 2026-09-19 真栈：dest_choice 挂起把重复的「规划去广州路上的补能…」整句当地址。
            "规划",
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

    async def _register_input_facts(self, ctx, text: str, mem_on: bool) -> None:
        """规划轮**提前结束**时，仍把本轮原话里的输入侧事实登记进焦点。

        C1-B 立的判据是「登记挂在输入上，不挂在路由上」，可登记本身住在 `extract_focus`
        里，而 `extract_focus` 只在 `update_focus` 被调到时才跑——技术失败终态（F09）、
        授权缺失、澄清、取消未命中、「没听清」这几条出口都在它之前 `return`。
        真栈（release `0d414816`，T47 收口探针）：「检查过了，机油灯已经灭了，恢复正常了」
        那一轮 planner 技术失败 ⇒ 解除陈述没跑到焦点 ⇒ 下一句仍答「未解除的机油灯」；
        反方向同样成立：告警句若恰好落在这几条出口上，会话里就**不知道**灯亮过。

        做法是把「输入那一半」单独跑一遍：一份空步计划只带 `raw_text`，`extract_focus`
        只会从原话扫出安全告警 / 驾驶员状态 / 解除陈述 / 会话偏好；什么都没扫出时它返回
        `None`，`update_focus` 原样不动焦点。判据一个字不复制，只是保证它被调到。
        焦点是 best-effort，绝不拖垮出口话术。
        """
        if not mem_on:
            return
        try:
            await self.context.update_focus(
                ctx.session_id, Plan(steps=[], raw_text=str(text or "")), [],
                user_id=ctx.user_id, exchange_id=ctx.request_id,
                occupant_id=getattr(ctx, "occupant_id", ""))
        except Exception as exc:
            logger.debug("input-fact registration on an early exit failed: %s", exc)

    @staticmethod
    def _apply_task_patch(plan: Plan, focus) -> None:
        """改口精确修改对象（W06 × W07）。四个前提缺一不合并：模型标了 `correct`；
        本轮恰好一步；活动任务帧还活着；两者同 intent。合并 = 新值优先、只补缺槽；
        结果写回 `plan.task_patch` 让 `extract_focus` 记成同一任务的下一版。
        没有标签就不猜——误继承一个陈旧目的地比漏继承更危险。"""
        if "correct" not in (getattr(plan, "acts", None) or []):
            return
        if len(plan.steps) != 1:
            return
        task = getattr(focus, "active_task", None) if focus is not None else None
        if not active_task_live(task):
            return
        step = plan.steps[0]
        if str(task.get("intent") or "") != str(step.intent or ""):
            return
        inherited = {str(k): str(v) for k, v in (task.get("slots") or {}).items()
                     if isinstance(v, (str, int, float)) and not isinstance(v, bool)}
        merged = {**inherited, **{k: v for k, v in (step.slots or {}).items()}}
        added = sorted(set(merged) - set(step.slots or {}))
        step.slots = merged
        plan.task_patch = {"task_id": str(task.get("task_id") or ""),
                           "revision": int(task.get("revision") or 1) + 1}
        logger.info("Correction patch on task %s (rev %s): inherited %s",
                    plan.task_patch["task_id"], plan.task_patch["revision"], added)

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
            # W16-b：被续接的那一步打标（它读本轮原话——槽答案 / 「确认」就在里面）；其余步没有
            # 起点原话的旧记录用**持久化的服务端文本**回填——与下面 safety_origin_text 的滚动升级
            # 规则同一条：只认 `safety_origin_text` / `raw_text`，goal 是 LLM 写的，无权冒充原话。
            persisted_origin = str(
                state.pending_plan.get("safety_origin_text", "")
                or state.pending_plan.get("raw_text", "") or "")
            for s in steps:
                s.resumed = (s.id == state.pending_step_id)
                if not s.origin_text:
                    s.origin_text = persisted_origin

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
                # Rolling-upgrade compatibility is deliberately narrow: only the
                # persisted server-owned raw_text may backfill an older record.
                # goal is LLM-controlled and must never become an authorization source.
                safety_origin_text=(
                    state.pending_plan.get("safety_origin_text", "")
                    or state.pending_plan.get("raw_text", "")
                ),
                complexity=state.pending_plan.get("complexity", "simple"),
                goal=state.pending_plan.get("goal", ""),
            )
            restored.skills = list(state.pending_plan.get("skills") or [])
            restored.skill_effects = list(state.pending_plan.get("skill_effects") or [])
            restored.exemplars = list(state.pending_plan.get("exemplars") or [])
            # 旧记录没有这一键 ⇒ False（与修前行为一致）；只认严格的 True
            restored.replan_batch = state.pending_plan.get("replan_batch") is True
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
        # 步的持久化键集在 `models.step_record`（澄清选项的预解析步共用同一份，W10）。
        # meta 故意不持久化：confirmed 标记只在确认那一轮由 _restore 注入，防止重放
        return {
            "steps": [step_record(s) for s in plan.steps],
            "raw_text": plan.raw_text,
            "safety_origin_text": str(
                getattr(plan, "safety_origin_text", "") or ""
            ),
            "complexity": plan.complexity,
            "goal": plan.goal,
            # T2 知识继承跨挂起（2026-07-27 评审二批）：不存 skills 的话，补槽/确认恢复后
            # 的再规划会丢初规划注入的规划知识（replan 按 plan.skills 重渲染，见 loop.py）
            "skills": list(plan.skills or []),
            "skill_effects": list(getattr(plan, "skill_effects", []) or []),
            "exemplars": list(getattr(plan, "exemplars", []) or []),   # 同款（M5 P1）
            # 这一份是不是 T2 再规划出来的一批（续接时循环据此不再套首轮 adaptive 纠偏，见 `Plan.replan_batch`）
            "replan_batch": bool(getattr(plan, "replan_batch", False)),
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
