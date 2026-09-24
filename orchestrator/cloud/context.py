"""ContextManager：编排器侧「上下文」统一装配门面（working/core 层）。

设计见 docs/design/2026-06-25-context-system-redesign.md。

职责（Phase 1）：把喂给 Planner 的上下文从散落的字符串拼接收敛成一个**有预算、
有结构**的环节——
- catalog：registry 语义预筛 top-K（agent 数 ≤ K 时天然 no-op，收益随规模兑现）；
- history：复用 clients.get_session（getattr 兜底，缺失/失败返回空，不阻塞规划）；
- memories：复用 clients.recall（同上）；
- WorkingSet.render_*：在统一字符预算下按优先级渲染成 prompt 块。

后续 Phase：focus 焦点态（Phase 2）、_build_context/persist_turn 迁入（Phase 3）、
按 manifest context_scopes 下发（Phase 4）。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field, fields, asdict

from .models import PlanContext, step_fingerprint
from runtime import memory_read
from runtime.clock import hhmm as clock_hhmm
from runtime.safety_signal import (DRIVER_STATE_ADVICE, alert_level,
                                   alert_resolved, alert_signal, driver_state)
from runtime.session_constraints import constraints_in, merge_constraints, phrase_of
from runtime.slots import normalize_city_slot as normalize_weather_city_slot
from security.audit import AuditLogger
from security.session_scopes import (
    POC_DEFAULT_SCOPES, SOURCE_POC_DEFAULT, SOURCE_TOKEN, resolve_granted_scopes,
)

logger = logging.getLogger("planner.context")
_audit = AuditLogger()

# PoC 默认权限与 granted_scopes 解析已收敛到 `security/session_scopes.py`——端侧 T0
# 也要按同一份判据决定「这个账号能不能控车」（AR05 F07）。此处保留别名只为兼容既有引用。
_POC_DEFAULT_SCOPES = POC_DEFAULT_SCOPES
# 敏感上下文键：默认按值广播，Phase 4 起按 manifest context_scopes 最小化下发。
_SENSITIVE_CONTEXT_KEYS = (
    "current_lat", "current_lng", "current_accuracy_m",
    "current_location_at", "current_location_source", "vehicle_battery",
)

# 共享给焦点提取与 Engine 的同域续接判据；两处各写一份会在新增天气能力时漂移。
WEATHER_CONTEXT_INTENTS = frozenset({
    "info.weather", "info.forecast", "info.alerts", "info.indices",
    "info.air_quality", "safety.weather_alert",
})


def _adapt_append_turn_call(
    fn,
    session_id: str,
    role: str,
    text: str,
    *,
    user_id: str,
    vehicle_id: str,
    occupant_id: str,
    e2e_memory_capability: str,
    turn_id: str = "",
    exchange_id: str = "",
    actions=None,
    sources=None,
) -> tuple[list, dict]:
    """Build one compatible call without probing by execution.

    Signature inspection is advisory. If a callable is opaque, make exactly
    one modern call; in particular, never retry after stripping a capability.
    """

    args = [session_id, role, text]
    optional = {
        "user_id": user_id,
        "vehicle_id": vehicle_id,
        "occupant_id": occupant_id or "primary",
        "e2e_memory_capability": e2e_memory_capability,
        "turn_id": turn_id,
        "exchange_id": exchange_id,
        # Q6 执行事实。**走同一条签名探测**——测试替身与旧客户端没有这个形参，
        # 硬塞会 TypeError（§4.3「执行器有这个形参≠尺子也在传它」的反面形态）。
        "actions": list(actions or []),
        # C4-A 数据源事实。走同一条签名探测，理由逐字同上。
        "sources": [s for s in (sources or []) if isinstance(s, dict)],
    }
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return args, optional

    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    kwargs = {}
    for name, value in optional.items():
        parameter = parameters.get(name)
        if accepts_kwargs or (
            parameter is not None
            and parameter.kind in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        ):
            kwargs[name] = value
        elif (
            parameter is not None
            and parameter.kind is inspect.Parameter.POSITIONAL_ONLY
        ):
            args.append(value)
    return args, kwargs


async def _call_with_owner(fn, session_id: str, last_n: int, *,
                           user_id: str, occupant_id: str):
    """调 `get_session` 并在被调方支持时带上 OwnerKey。

    与 `_adapt_append_turn_call` 同一思路：签名探测只是**建议**——测试替身与旧客户端
    仍是 `(session_id, last_n)` 两参，给它们塞 kwargs 会直接 TypeError。不支持 owner
    的被调方退回旧行为（读到的仍是混合历史），但生产 `Clients.get_session` 支持，
    所以生产路径是隔离的。
    """
    optional = {"user_id": user_id, "occupant_id": occupant_id}
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return await fn(session_id, last_n, **optional)
    accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD
                         for p in parameters.values())
    kwargs = {k: v for k, v in optional.items()
              if accepts_kwargs or k in parameters}
    return await fn(session_id, last_n, **kwargs)


# 装配预算（字符近似，避免引入 tokenizer 依赖；沿用既有 block[:400] 的 char-proxy 思路）。
_CTX_BUDGET = int(os.getenv("PLANNER_CTX_BUDGET_CHARS", "1400"))   # 记忆+历史(+焦点)合计
_MEMORY_BUDGET = 400                                              # 记忆块上限（同旧 _format_memory）
# 历史视窗按**完整 exchange（一问一答）**计，不按消息条数计（评审 2026-09-19 F02 / W02）：
# 旧的 `history[-4:]` 会从第二对的回答开始截，把「用户问了什么」丢掉。
# 缺省 4 对（2026-09-21，用户裁决；此前 2 对 = 旧的 4 条）。依据是 W19-c 干净用户单变量实验
# （设计文档 §8.2.1，32 组 × 3 臂）：指代物所在那一对不在视窗内时 planner **0/48** 解出、在视窗内 23/48；
# 2 对在任何 ≥2 轮插话下必失，4 对与 6 对在 k=2 插话下读数相同（8/16 = 8/16），6 对只多救回 k=4 的插话。
# 预算 `_CTX_BUDGET` 仍是硬上限：长回答下 `_render_history_with_stats` 会整对从最旧丢起，
# 实际视窗可能小于 4——`context_stats.history_pairs_kept / history_pairs_dropped` 是读数，不是承诺。
_HISTORY_EXCHANGES = int(os.getenv("PLANNER_HISTORY_EXCHANGES", "4"))
# 数据飞轮 P0 D1 应急：8000 时代的假设「正常情况下根本不触发裁剪」已随 M3/M4 新增
# mcp-bridge/vision 失效——16 agent 全量渲染约 9.7k 字符，超算后从尾部裁非受保护 agent，
# 而保护判据（有无 route_hints）与领域重要性无关，navigation 等 4 个无 hint agent 会被
# 整域裁出 prompt（planner 从此看不见它们）。16k 下当前全量放得下；catalog 再长该走
# 检索化预筛（P2），不是继续抬预算。裁剪统计随 cloud.planning span 出观测。
_CATALOG_BUDGET = int(os.getenv("PLANNER_CATALOG_BUDGET_CHARS", "16000"))

# 全局兜底 Agent（LLM 抽风/规划失败时降级）由 env 指定，不再硬编码 agent_id（R2.1 P5）。
_FALLBACK_AGENT = os.environ.get("PLANNER_FALLBACK_AGENT", "chitchat")


def _always_include(a) -> bool:
    """catalog 语义预筛/预算裁剪都不得丢的 Agent（取代硬编码 _ALWAYS_INCLUDE，去领域字面量）：

    ①全局兜底 Agent（env `PLANNER_FALLBACK_AGENT`）；
    ②`category: core` 的 Agent——**领域重要性**（M5 P2 补）。

    ⚠ 2026-09-20（评审 W15）：「声明了 `route_hints`」**不再**是保护资格。此前它是机制依赖——
    RouteHintEngine 从 prompt 目录的 `agent_map` 里读 hints，manifest 被预筛丢掉它就看不到；
    于是退役一条 hint 会顺手改变目录裁剪（治理规则的动作有远处的副作用），而一条 hint 又能
    让一个非 core 的 Agent 永远占着 prompt 预算。现在 hint 扫描改读**权限过滤后的完整注册表**
    （`WorkingSet.registry_agents`，`PlanBuilder.build` 里另起一张 `hint_map`），被裁出 prompt
    的 Agent 它的 hint 照样命中、补出的步照样过 `_validated_steps`——**能力可见性与规则存亡
    从此是两件事**。`category` 是 manifest/proto/registry 已有的字段，用它即机制化，不新增管道。"""
    m = getattr(a, "manifest", None)
    return (getattr(m, "agent_id", "") == _FALLBACK_AGENT
            or str(getattr(m, "category", "")) == "core")


def assemble_budgeted_catalog(agents: list, renderer, stats: dict | None = None
                              ) -> tuple[list, str]:
    """Render and prune one capability catalog against the *actual* prompt text.

    ``renderer`` is deliberately supplied by the caller.  The legacy catalog and
    the planner's request-local capability-reference mapping have different wire
    shapes, but must share exactly one protection/drop policy.  Re-rendering after
    every drop also makes character accounting cover ref numbering, headings,
    punctuation and newlines instead of estimating from a different representation.

    Availability remains fail-open as before: a non-empty candidate set is never
    reduced below one agent, and an all-protected set may exceed the configured
    budget.  Entries are never truncated mid-capability.
    """
    visible = list(agents or [])
    protected = [_is_edge_core(a) or _always_include(a) for a in visible]
    ids = [str(getattr(getattr(a, "manifest", None), "agent_id", "") or "")
           for a in visible]
    rendered = renderer(visible)
    chars_full = len(rendered)
    dropped: list[str] = []

    while len(rendered) > _CATALOG_BUDGET and len(visible) > 1:
        idx = next((i for i in range(len(visible) - 1, -1, -1)
                    if not protected[i]), None)
        if idx is None:
            break
        visible.pop(idx)
        protected.pop(idx)
        dropped.append(ids.pop(idx))
        rendered = renderer(visible)

    if len(rendered) > _CATALOG_BUDGET:
        logger.warning(
            "catalog remains over budget (%d > %d chars): no removable agent",
            len(rendered), _CATALOG_BUDGET,
        )
    elif dropped:
        logger.warning("catalog over budget (%d chars): dropped agents %s",
                       _CATALOG_BUDGET, dropped)

    if stats is not None:
        stats.clear()
        stats.update({
            "chars_full": chars_full,
            "chars_final": len(rendered),
            "dropped": dropped,
        })
    return visible, rendered

# 控制类意图域 → (语义对象, 属性)，供焦点抽取（"再调高一点"指代上轮控制对象）。
_CONTROL_FOCUS = {
    "hvac": ("空调", "温度"), "window": ("车窗", "开度"),
    "ambient": ("氛围灯", "颜色"), "lighting": ("灯光", ""),
    "seat": ("座椅", ""), "volume": ("音量", "音量"),
    "media": ("媒体", ""), "sunroof": ("天窗", "开度"),
    "rear_view_mirror": ("后视镜", "开合"),
}
_POSITION_WORDS = ("主驾驶", "副驾驶", "主驾", "副驾", "后排", "左后", "右后", "前排")


@dataclass
class Focus:
    """跨轮对话焦点（指代消解用）。只记能可靠抽取的字段；空字段不注入 prompt。"""
    last_agent_id: str = ""
    last_intent: str = ""
    # 产生当前相邻焦点的 exchange。只用于证明「上一轮」真的是紧邻上一轮；不进 prompt。
    # 端侧本地轮不会更新 Redis Focus，但会写同一会话历史并携带 exchange_id。
    origin_exchange_id: str = ""
    obj: str = ""                                       # 语义对象，如 "空调"/"氛围灯"
    positions: list[str] = field(default_factory=list)  # ["副驾"]
    # 评审四轮 R4-04：**落盘这份焦点的那一轮**真正执行过的控制目标 `[{command, positions}]`（云侧执行的控制步 + 同轮端侧那半）。
    # 下一轮执行账本（只有名字）覆盖控制焦点时，位置从这里取——修前一律清空，云侧执行过的「副驾」也留不到「关掉」那一轮。
    # 短时引用（随 `_FOCUS_SHORT_TTL_S` 过期），相邻性断开时一起清。
    control_targets: list[dict] = field(default_factory=list)
    attr: str = ""                                      # "温度"/"颜色"...
    last_poi: str = ""                                  # 上个 POI（"还是刚才那家"）
    last_destination: str = ""                          # 上个导航目的地
    last_city: str = ""                                 # 上个天气/空气查询的显式城市
    last_stock_symbol: str = ""                         # 上个成功股票查询的标的
    # ⚠ 这两格现在是**派生视图**，不再独立抽取（Q2）：由 `candidate_sets` 里
    # **最近一份非兜底**候选算出来，只服务 prompt 渲染与既有消费方。
    # 独立抽取的老毛病是「任何一轮不产生候选就把上一份抹平」（I-019）。
    last_choice_purpose: str = ""                       # list | waypoint（最新候选卡的语义）
    last_choices: list[str] = field(default_factory=list)  # 最新候选名，按卡片顺序（最多 5 个）
    # Q2 候选集一等对象：最近 N 组，**按产生先后**（最后一组最新）。
    # 每组 = {source_intent, agent_id, purpose, ts, is_fallback, items:[{name, ...}]}。
    #
    # 为什么要升格：此前「候选」只是一个 `list[str]` 名字数组，且**每轮从当前 plan
    # 重建**。三条后果各自对应一族问题——
    #   · 每轮重建 ⇒ 任何一轮不产生候选就抹平上一份（I-019）；
    #   · 只存名字 ⇒ 卡片上明明渲染了营业时间/评分/价格，下一轮上下文里一个字都没有
    #     （I-018「称全部未查到营业时间」、I-023「不肯算 26.5+9.5」）。
    #     **卡片是终点**：结构化结果一旦渲染成卡片就不再是可消费的事实；
    #   · 无来源无版本 ⇒ nearby POI / 商户菜单 / 途经点 / 充电目的地共用一格，
    #     跨域「第二个」无从判断问的是哪一份（I-030、I-025①、I-053②）。
    #
    # **刻意不整组进 prompt**（同 `last_places` 那条纪律）：让模型看见结构化事实
    # 只会诱导它自己编。它是给**确定性消费方**用的。
    candidate_sets: list[dict] = field(default_factory=list)
    destination_lat: float | None = None                # 已解析目的地坐标（供“那边”确定性续接）
    destination_lng: float | None = None
    # 上一轮 nearby.search 取回的公开 POI（只留 name/lng/lat 三标量，最多 10 条）。
    # 真实商户下单/看菜单要求门店三元组来自「同一次 nearby.search 的同一条 item」，
    # 而该 provenance 每轮被执行器清掉——于是「先查附近的瑞幸」「在最近那家点一杯」
    # 两轮走不通。这一格把**服务端记得取回过哪些门店**这件事跨轮留住；
    # **刻意不进 prompt**（`_render_focus` 不渲染它）：它是给执行器补槽用的结构化事实，
    # 让模型看见只会诱导它自己编坐标。
    last_places: list[dict] = field(default_factory=list)
    # last_places 的取回时刻（epoch 秒）。粘性接力（update_focus）让列表跨任意多轮
    # 存活，时效只能靠这枚时间戳兑现——executor 按它限龄，过龄不锚定（诚实回到
    # 「请先查询附近门店」）。0 = 旧数据无时间戳，按过龄处理。
    last_places_ts: float = 0.0
    # G8 路线会话：navigation 成功发出 navigate 后经保留键 `_route_session` 声明的
    # 活动路线 {destination,lat,lng,waypoints,strategy,arrive_by_ts,ts}。与 last_places
    # 同三条纪律：粘性接力不续期、prompt 只渲染名字不渲染坐标、消费方（navigation
    # reroute）按 ts 限龄。空 dict = 无活动路线。
    active_route: dict = field(default_factory=dict)
    # Q9 安全告警会话态：任何 Agent 经保留键 `_safety_alert` 声明的**未解除**安全信号
    # `{level: critical|amber, signal, ts}`。同 active_route 三条纪律（粘性接力不续期、
    # 按 ts 限龄、编排不认识 Agent 私有字段）。
    # 存在的理由：QA 轮 SF3 三轮实测——红色机油灯之后第二轮答天气、第三轮执行音量。
    # **一次安全警告必须是会话状态，不能是一句话说完就没了。**
    safety_alert: dict = field(default_factory=dict)
    # C12-B 会话内偏好约束 `{no_spicy: bool, no_queue: True}`：用户**在这次会话里
    # 说出来**的忌口/偏好。判据与词表在 `runtime/session_constraints.py`（唯一实现）。
    # 与记忆画像的关系是**前景 vs 背景**：画像是「平时爱吃川菜」，这一格是
    # 「我今天说了不吃辣」——冲突时前景赢（§4.3 那条判据的载体，写下一年才有）。
    # 同 safety_alert 的粘性：普通轮不得把它抹掉，**只有用户改口才覆盖**。
    session_constraints: dict = field(default_factory=dict)
    # **本轮 scratch，不跨轮**（保存前由 `update_focus` 复位）：这一轮显式终止了活动路线
    # （保留键 `_route_session_end`，QA I-017）。
    # ⚠ 存在的理由是**接力比清除更强**：粘性接力的条件是 `not focus.active_route`，
    # 而「清空」恰恰让它成立 ⇒ 上一轮那条路线被原样搬回来，用户听到「已结束导航」、
    # 下一句「换条路」却仍在改它（真栈 CA5 第 3 轮 3/3 红，**单测没抓到**——
    # 我的两条断言都在同一份 results 里既 stamp 又 end，**替被测系统提供了「同轮」
    # 这个前提**，而真实场景是跨轮）。空 dict 表达不了「我是故意空的」，所以要一个旗子。
    route_ended: bool = False
    # **本轮 scratch，不跨轮**（同 `route_ended`）：这一轮用户明确说了告警已解除
    # （`runtime.safety_signal.alert_resolved`，QA T47 裁决 A，2026-09-19）。
    # 同样的理由：`safety_alert` 的粘性接力条件在合并里体现为「本轮为空 ⇒ 取旧」，
    # 而「解除」恰恰让本轮为空——不立旗，上一轮那条 critical 会被原样搬回来。
    safety_alert_cleared: bool = False
    # 批 5 W18-a（2026-09-20）：被顶掉 / 过期的候选批的**墓碑** `[{label, place_hint, source_intent, ts}]`。
    # 台账封顶 3 组、限龄 15 分钟——「万象城那批」被第 4 批顶掉之后，用户再点名它时
    # `resolve_candidate_scope` 零命中会退回最新那组，于是「万象城那批第二家评分多少」答的是
    # 南山书城那批的第二家、零方差（评审 F03「过期后可说明需重新查询，禁止悄悄换成另一家」）。
    # 墓碑只留名字（不留 items）：它的用途只有一个——认出「你说的那批已经不在手边了」。
    # 封顶 `_RETIRED_SETS_MAX`、限龄 `_RETIRED_TTL_S`；同键新版本（「换一批」）不是被顶掉，不立墓碑。
    retired_candidate_sets: list[dict] = field(default_factory=list)
    # W07（2026-09-20）活动任务帧：最近一个**任务性**步骤（非 response_only）执行后的账
    # `{task_id, intent, agent_id, slots, revision, outcome, ts, goal}`。它是「改口精确修改对象」
    # 的对象：`acts` 含 correct 且同 intent ⇒ 缺槽从这里继承、revision+1、task_id 不变。
    # 活动状态（不随短时引用过期），按自己的 `ts` 限龄 `_ACTIVE_TASK_TTL_S`；新任务替换它。
    active_task: dict = field(default_factory=dict)
    # 评审二轮 R7（2026-09-22）：焦点里**私有那一半**按乘员归属 `{occupant_id: {字段: 值}}`。
    # 历史与长期记忆早就按 `user_id + occupant_id` 读，焦点只按 `user_id + session_id`——
    # 同一辆车、同一账号、两位已识别乘员时，A 的「不吃辣」成了 B 问「我今天说过不吃辣吗」的答案，
    # 话术还说「您这次说过」。归属错了比答不出更难发现。
    # 只盖**明确私有**的两格（`_OWNED_FOCUS_FIELDS`）：会话约束与活动任务帧；
    # 共享车辆 / 路线状态（活动路线、安全告警、候选台账、上一轮意图）刻意仍然共享——
    # 它们是车上所有人看的同一块屏、同一条路。声纹只是归属线索，不参与权限 / 确认 / 支付。
    by_occupant: dict = field(default_factory=dict)
    # W09：这份焦点**落盘的时刻**（epoch 秒）。短时引用（对象 / 属性 / 位置 / 上个地点 /
    # 上个目的地 / 上个城市 / 上个标的 / 上一轮意图 / 最新候选视图）只活 `_FOCUS_SHORT_TTL_S`，
    # 读取时按它判；活动状态各按自己的 ts。0 = 旧数据没盖过章，按未过期读（滚动窗口内无害）。
    focus_ts: float = 0.0

    def is_empty(self) -> bool:
        # last_intent 也算有效焦点：纯信息轮（查赛程/天气）此前不落焦点，「明天呢」这类
        # 省略式追问就只能靠裸历史猜域（badcase demo-i9c92i 追问被错绑到天气）。
        return not (self.obj or self.positions or self.control_targets or self.attr
                    or self.last_poi or self.last_destination or self.last_city
                    or self.last_stock_symbol
                    or self.last_intent
                    or self.last_choice_purpose or self.last_choices
                    or self.candidate_sets
                    or self.retired_candidate_sets
                    or self.last_places or self.active_route or self.safety_alert
                    or self.session_constraints
                    or self.active_task
                    or self.route_ended
                    or self.safety_alert_cleared
                    or self.destination_lat is not None
                    or self.destination_lng is not None)


@dataclass
class WorkingSet:
    """一次规划轮装配好的工作上下文。catalog 是已（语义）预筛的 agent 列表。"""
    catalog: list = field(default_factory=list)        # ResolvedAgent 列表（含 .manifest/.endpoint）
    # W15：**完整**注册表（预筛 / 预算裁剪之前的那份）。route_hints 从这里扫，不从 prompt 目录扫
    # ——被裁出 prompt 的 Agent 的 hint 照样命中。空 = 旧调用方没给，退回 `catalog`。
    registry_agents: list = field(default_factory=list)
    history: list[dict] = field(default_factory=list)  # [{role, text, ts}]
    memories: list[dict] = field(default_factory=list) # [{text, scope, predicate, provenance, confidence}]
    # 批 5 W16 / W17：这两份**读取本身的结局**（`runtime.memory_read`：found / none / unavailable / off）。
    # 此前 `_history` / `_recall` 各自 `except Exception: return []`——「读到了、是空的」与「根本没读到」
    # 在胶囊上是同一个值，一次 PG / Redis 故障就会让读出口说出一句自信的「没有记录」。
    # 消费方：确定性读出口（执行史 / 数据源 / 记忆问句）按它选「查不到」而不是「没有」；
    # 随 `context_stats` 进 span，生产里「记忆到底多常读不到」从此有数。
    history_state: str = memory_read.NONE
    memory_state: str = memory_read.NONE
    # 批 5 W19：本轮历史视窗（对数）。0 = 部署缺省 `_HISTORY_EXCHANGES`；请求级 pin 落在这里。
    history_exchanges: int = 0
    focus: "Focus | None" = None                       # 结构化焦点态（指代消解）
    # 落域可观测（数据飞轮 P0）：render_catalog 回填 {chars_full, chars_final, dropped}
    catalog_stats: dict = field(default_factory=dict)
    # W02 可观测：render_context 回填 {ctx_chars, focus_chars, memory_chars, history_chars,
    # history_pairs_kept, history_pairs_dropped, history_trimmed}——「按实际请求记录渲染规模」
    # （评审 W05）从此有数，随 cloud.planning span 发出。
    context_stats: dict = field(default_factory=dict)
    # C6-B（2026-08-28，QA P1-03）：**本轮**要不要把粘性的地点/候选焦点注进 prompt。
    # 「值得跨轮留住」与「这一轮该不该注入」是两个问题（§9.28 已有同款分离先例：
    # 候选集的跨轮留存表与下发表刻意分开）。置位方是 `planning.build`——它按
    # **声明式**判据置位（本轮命中了某条 `scope: clause` 的 route_hint ⇒ 这一轮有一个
    # 自带完整语义的确定性诉求，不靠指代也说得清），编排核心不认识任何领域词。
    suppress_sticky_places: bool = False

    def render_context(self) -> str:
        """焦点 + 记忆 + 历史块，统一字符预算、按优先级裁剪。

        优先级：焦点 > 记忆 > 历史（焦点/画像比旧对话轮更值得留）。无焦点且预算内时输出与旧
        `_format_memory + _format_history` 逐字一致（不扰动既有 LLM 行为）。"""
        focus_block = _render_focus(self.focus,
                                    drop_sticky_places=self.suppress_sticky_places)
        mem_block = _render_memory(self.memories)
        budget_left = max(0, _CTX_BUDGET - len(focus_block) - len(mem_block))
        exchanges = int(self.history_exchanges or 0) or _HISTORY_EXCHANGES
        hist_block, hist_stats = _render_history_with_stats(
            self.history, budget=budget_left, exchanges=exchanges)
        out = focus_block + mem_block + hist_block
        self.context_stats = {
            "ctx_chars": len(out),
            "focus_chars": len(focus_block),
            "memory_chars": len(mem_block),
            "history_chars": len(hist_block),
            "history_state": self.history_state,
            "memory_state": self.memory_state,
            "history_exchanges": exchanges,
            **hist_stats,
        }
        return out

    @staticmethod
    def render_catalog(agents: list, stats: dict | None = None) -> str:
        """能力清单 JSON；超 catalog 预算时优先丢相关性最低的**非受保护** agent（从尾部找）。

        受保护 = edge 车控核心（edge-vehicle/edge-media）∪ 兜底 Agent（env）∪ core Agent（见 _always_include；W15 起 hint 不再是资格）。
        根因修复：edge-vehicle 有几十个 caps、渲染体积大，旧逻辑无差别 pop 尾部会把它或
        chitchat 丢掉——丢 edge 车控→危险动作规划空计划退化（dangerous_trunk_confirm）；丢
        chitchat→开放域兜底缺席、误路由到 info（cloud_chitchat_streaming）。

        ⚠️ 裁剪不是理论分支（数据飞轮 P0 D1）：能力面长到 16 agent 后全量渲染已超过旧
        8000 预算，被裁的是「无 route_hints」的 agent（navigation/manual-rag/parking/
        road-safety）——保护资格与领域重要性无关。被裁 agent 对 planner 完全不可见且
        步骤校验会拒绝它的 intent。故：①默认预算提到 16k；②每次裁剪回填 stats 并
        warning（cloud.planning span 可查），静默丢域从此可见。根治=P2 catalog 检索化。

        stats（可选 dict，原地回填）：{chars_full, chars_final, dropped: [agent_id]}。"""
        _, out = assemble_budgeted_catalog(
            agents,
            lambda visible: json.dumps(
                [_catalog_item(a) for a in visible], ensure_ascii=False),
            stats,
        )
        return out


def _is_edge_core(a) -> bool:
    """安全核心：edge/edge_fast 车控 Agent。catalog 预筛与渲染都须保它不被丢，
    否则危险车控的二次确认会退化成 chitchat 兜底。与 ContextManager 预筛判据一致。"""
    m = a.manifest
    return (getattr(m, "deployment", "") == "edge"
            or getattr(m, "kind", "") == "edge_fast")


def _catalog_item(a) -> dict:
    if _is_edge_core(a):
        # edge 车控核心 caps 多（78 个）；只渲染意图名（trunk.open 等），不带 slots/desc。
        # slot 由 planner 从用户原话推断（如"26度"→temp），无需 catalog 提示。
        #
        # ⚠ 2026-08-01 实测过「把判别化描述也渲进来」并**否掉了**（M5 P3 收尾）：
        # `capabilities.py` 已把 78 条描述从泛化改成判别化，理论上该让 planner 看见。
        # 双臂差分（唯一变量就是这一行，25 条口语+canonical 语料 ×2 轮 ×2 provider）：
        # minimax 22/25→22/25、deepseek 23/25→23/25，**Δ=0 且 100 次对照零翻面**。
        # 代价却是每次规划 +1462 字符。**intent 名本身就是判别性文本**
        # （`lane_departure_assistance.open` 与 `lane_assistance.open` 两档都分得开），
        # 所以「74 个文本等价的工具」这个说法对 planner 这一侧不成立。
        # 判别化描述真正的受益方是 **registry 语义兜底**（按 capability 粒度 embed），
        # 那条路上同一改动把「打开空调」的 top-1 从 scene-orchestrator 掰回 edge-vehicle
        # ——见 `test/eval_registry_resolve.py` 的车控 guardrail。
        caps = [{"intent": c.intent} for c in a.manifest.capabilities]
    else:
        caps = [{"intent": c.intent, "slots": list(c.slots), "desc": c.description}
                for c in a.manifest.capabilities]
    return {
        "agent_id": a.manifest.agent_id,
        "kind": getattr(a.manifest, "kind", "") or "agent",
        "deployment": getattr(a.manifest, "deployment", "") or "cloud",
        "capabilities": caps,
    }


# M2 P0 偏好加权：高权偏好用**确定性人话强度词**（不进 LLM——「有多常用」是系统持有的
# 事实，让模型自己揣摩会把「说过一次」和「每周三次」说成一样）。阈值与 weighting 的
# base 分档对齐：0.7=显式陈述被反复印证 / 0.5=显式说过一次 / 更低=推断且证据薄。
_STRENGTH_HIGH, _STRENGTH_MID = 0.7, 0.5
_MEMORY_TOP_N = 5          # 从 3 放宽：今天 top-3 会被一条久远的推断偏好挤掉真正常用的


def _strength_label(weight: float) -> str:
    if weight >= _STRENGTH_HIGH:
        return "常用"
    if weight >= _STRENGTH_MID:
        return "明确说过"
    return "偶尔提过"


def _render_memory(memory: list[dict] | None) -> str:
    """长期偏好记忆 → prompt 片段（≤_MEMORY_BUDGET）。

    两段式（M2 P0）：**带权偏好**按强度排序、用人话强度词渲染；未参与加权的条目
    （weight=0，即 M2 之前的存量条目与情景/程序记忆）走原格式的「相关记忆」段。

    **存量兼容**：全部条目 weight=0 时输出与加权前逐字一致（契约测试锁）——
    不扰动已绿的旅程（B3-3 记忆族）。
    勿向用户暴露置信度；高风险动作仍需确认（由执行层保证）。
    """
    if not memory:
        return ""
    weighted, plain = [], []
    for m in memory[:_MEMORY_TOP_N]:
        txt = (m.get("text") or "").strip()
        if not txt:
            continue
        try:
            w = float(m.get("weight") or 0)
        except (TypeError, ValueError):
            w = 0.0
        if w > 0:
            weighted.append((w, txt))
        else:
            tag = m.get("scope") or m.get("predicate") or ""
            prov = m.get("provenance") or ""
            try:
                conf = float(m.get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            plain.append(f"- [{tag} | {conf:.2f} | {prov}] {txt}")
    if not weighted and not plain:
        return ""
    weighted.sort(key=lambda x: x[0], reverse=True)
    weighted_lines = [f"- {txt}（{_strength_label(w)}）" for w, txt in weighted]
    plain_lines = list(plain[:3])

    def _compose(w_lines: list[str], p_lines: list[str]) -> str:
        parts = []
        if w_lines:
            parts.append("已知用户偏好（按强度排序，仅在与当前任务相关时参考）：\n"
                         + "\n".join(w_lines))
        if p_lines:
            head = ("相关记忆：" if w_lines
                    else "已知用户记忆（仅在与当前任务相关时参考，勿向用户暴露置信度）：")
            parts.append(head + "\n" + "\n".join(p_lines))
        return "\n".join(parts)

    # W02：**按条裁，不按字裁**。旧的 `block[:400]` 会把一条偏好切成半句
    # （「用户不吃花」）——半条事实比没有更糟。超预算就整条去掉：先去无权重的旧条目，
    # 再去强度最弱的那条；一条都放不下时输出空。
    block = _compose(weighted_lines, plain_lines)
    while block and len(block) > _MEMORY_BUDGET:
        if plain_lines:
            plain_lines.pop()
        elif weighted_lines:
            weighted_lines.pop()
        block = _compose(weighted_lines, plain_lines)
    if not block:
        return ""
    return block + "\n\n"


_HISTORY_HEAD = "最近对话（用于指代消解）：\n"
#: 句边界（W02 按句裁）：只认句末标点，零领域词。
_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")


def _pair_exchanges(history: list[dict] | None) -> list[list[dict]]:
    """消息流 → 完整 exchange 列表：一条 user 开一对，其后的 assistant 归它；
    没有前置 user 的 assistant（主动播报）自成一对。空文本的消息跳过。"""
    pairs: list[list[dict]] = []
    for msg in history or []:
        if not isinstance(msg, dict) or not str(msg.get("text") or "").strip():
            continue
        if msg.get("role") == "user" or not pairs or any(
                m.get("role") == "assistant" for m in pairs[-1]):
            pairs.append([msg])
        else:
            pairs[-1].append(msg)
    return pairs


def _history_lines(msgs: list[dict]) -> list[str]:
    lines = []
    for m in msgs:
        txt = str(m.get("text") or "").strip()
        if txt:
            who = "用户" if m.get("role") == "user" else "助手"
            lines.append(f"{who}：{txt}")
    return lines


def _history_block(pairs: list[list[dict]]) -> str:
    lines = [ln for pair in pairs for ln in _history_lines(pair)]
    return (_HISTORY_HEAD + "\n".join(lines) + "\n\n") if lines else ""


def _fit_last_exchange(msgs: list[dict], budget: int) -> tuple[str, bool, bool]:
    """最后一对也放不下时的收缩顺序（W02 + 评审二轮 R6）。返回 `(block, 是否裁过, 是否整对舍弃)`。

    **只裁助手那半**：回答是可再生成的，用户的请求不是。评审二轮 R6 的最小反例——
    「我想去机场。只查路线，不要启动导航。」在 32 字预算下被按尾部删句裁成「我想去机场。」：
    字数合规，**约束消失了，剩下的是一个相反的正向目标**。旧实现按「最长那条」裁、不看角色，
    所以用户那条最后的否定 / 纠正 / 预算 / 期限都可能被删掉。

    顺序：① 助手消息按句从尾部裁（长的先裁）；② 还放不下就整条丢掉助手消息，只留用户那条；
    ③ 用户那条自己都放不下 ⇒ **整对舍弃**并报 `omitted`——绝不只留一句助手的回答
    （那正是「只保留相反的正向目标」的另一种形态：用户问了什么不在了，答案还在）。
    """
    work = [dict(m) for m in msgs]
    assistant_idx = [i for i, m in enumerate(work) if m.get("role") != "user"]
    shrinkable = set(assistant_idx)
    trimmed = False
    while True:
        block = _history_block([work])
        if block and len(block) <= budget:
            return block, trimmed, False
        candidates = sorted(
            shrinkable, key=lambda i: len(str(work[i].get("text") or "")), reverse=True)
        if not candidates:
            break
        idx = candidates[0]
        sentences = _SENTENCE_RE.findall(str(work[idx].get("text") or ""))
        if len(sentences) <= 1:
            shrinkable.discard(idx)
            continue
        work[idx]["text"] = "".join(sentences[:-1]).strip()
        trimmed = True
    user_only = [m for m in work if m.get("role") == "user"]
    if user_only:
        block = _history_block([user_only])
        if block and len(block) <= budget:
            return block, True, False
        return "", True, True            # 用户那条放不下：整对舍弃，不留孤立的回答
    # 这一对里根本没有用户消息（主动播报自成一对）：按原口径留最新那条
    newest = work[-1:]
    block = _history_block([newest])
    if block and len(block) <= budget:
        return block, True, False
    return "", True, True


def _render_history_with_stats(history: list[dict] | None,
                               budget: int = _CTX_BUDGET,
                               exchanges: int | None = None) -> tuple[str, dict]:
    """最近对话 → prompt 片段 + 裁剪统计（W02）。

    视窗按完整 exchange 计（缺省 `_HISTORY_EXCHANGES` 对；W19 请求级 pin 经 `exchanges` 传入）；
    **预算是硬约束**：整对从最旧丢起，最后一对按句收缩，绝不出现「预算 0 仍渲染 2019 字符」。
    格式逐字沿用旧 `_format_history`。
    """
    exchanges = int(exchanges or 0) or _HISTORY_EXCHANGES
    pairs = _pair_exchanges(history)
    # `history_omitted`（评审二轮 R6）：最后一对里**用户那条**放不下 ⇒ 整对舍弃。
    # 它与 `history_trimmed` 不是一件事：裁的是可再生成的回答，舍弃的是一个够不着的请求。
    stats = {"history_pairs_kept": 0, "history_pairs_dropped": len(pairs),
             "history_trimmed": False, "history_omitted": False}
    if not pairs or budget <= 0:
        return "", stats

    def _done(block: str, kept: int, trimmed: bool = False,
              omitted: bool = False) -> tuple[str, dict]:
        # dropped = 历史里有的对数 − 渲染出来的对数（只要没进 prompt 就算丢，含视窗外的）
        # kept 只数**真正渲染出来的完整对**：孤立的助手行不算一对（评审二轮 R6）。
        stats["history_pairs_kept"] = kept
        stats["history_pairs_dropped"] = len(pairs) - kept
        stats["history_trimmed"] = trimmed
        stats["history_omitted"] = omitted
        return block, stats

    window = pairs[-max(1, exchanges):]
    while window:
        block = _history_block(window)
        if not block:
            return _done("", 0)
        if len(block) <= budget:
            return _done(block, len(window))
        if len(window) > 1:
            window.pop(0)
            continue
        block, trimmed, omitted = _fit_last_exchange(window[0], budget)
        return _done(block, 1 if block else 0, trimmed, omitted)
    return _done("", 0)


def _render_history(history: list[dict] | None, budget: int = _CTX_BUDGET) -> str:
    """最近对话 → prompt 片段（`_render_history_with_stats` 的无统计包装，既有调用方用）。"""
    return _render_history_with_stats(history, budget=budget)[0]


#: 活动任务帧的寿命（W07）：半小时没再碰它就不再是「正在处理的那件事」。缓存超时不是
#: 事实解除——它只是不再当改口的对象，业务系统里的路线 / 订单照旧。
_ACTIVE_TASK_TTL_S = 1800.0


def active_task_live(task: dict | None, *, now: float | None = None) -> bool:
    if not isinstance(task, dict) or not task.get("intent"):
        return False
    now = time.time() if now is None else now
    try:
        ts = float(task.get("ts") or 0)
    except (TypeError, ValueError):
        return False
    return 0 < ts and now - ts <= _ACTIVE_TASK_TTL_S


def task_writes(step, result=None) -> bool:
    """这一步是不是**写**任务（W07 帧的 `kind`）。

    声明优先（`Step.effect`，评审 W11）：`write` ⇒ 写、`read` ⇒ 读；未声明退回启发式
    「结果带 actions / 声明 require_confirm ⇒ 写」。`reminder.create` 这类不出 action 的云侧
    写此前被记成 read，下一轮一句查询就把它顶掉——改口的对象没了。
    """
    declared = str(getattr(step, "effect", "") or "").strip().lower()
    if declared == "write":
        return True
    if declared == "read":
        return False
    return bool(getattr(result, "actions", None)) or bool(
        getattr(step, "require_confirm", False))


def _task_frame(plan, step, outcome: str, kind: str) -> dict:
    """一个任务性步骤 → 任务帧。改口（`plan.task_patch`）沿用 task_id、版本 +1。

    `kind`：`write` = 改变了世界或还挂着（结果带 actions / 声明 require_confirm / 挂起中），
    `read` = 纯查询。接力规则在 `update_focus`：**写任务只被写任务顶掉，读任务顶不掉写任务**
    ——「导航去公园 → 查个天气 → 改成7点半」里用户改的是导航，不是天气。
    """
    patch = getattr(plan, "task_patch", None) or {}
    return {
        "task_id": str(patch.get("task_id") or f"task-{uuid.uuid4().hex[:8]}"),
        "intent": str(step.intent or ""),
        "agent_id": str(step.agent_id or ""),
        "slots": {str(k): v for k, v in (step.slots or {}).items()
                  if isinstance(v, (str, int, float)) and not isinstance(v, bool)},
        "revision": int(patch.get("revision") or 1),
        "outcome": outcome,
        "kind": kind,
        "ts": time.time(),
        "goal": str(getattr(plan, "goal", "") or getattr(plan, "raw_text", "") or "")[:40],
    }


#: 短时引用的寿命（W09）。与旧的 `_FOCUS_TTL=300` 同值——「上个对象是空调」五分钟后不再
#: 当指代锚是原来的语义，变的是它不再拖着活动状态一起消失。
_FOCUS_SHORT_TTL_S = 300.0
#: 短时引用字段：过期即回到缺省值。**不在名单里的就是活动状态**（候选台账 / 门店锚定 /
#: 活动路线 / 安全告警 / 会话约束 / 坐标随目的地一起走）。
_SHORT_TERM_FIELDS = (
    "obj", "attr", "positions", "control_targets", "last_poi", "last_destination", "last_city",
    "last_stock_symbol", "last_intent", "last_agent_id", "last_choices",
    "last_choice_purpose", "destination_lat", "destination_lng", "origin_exchange_id",
)


#: 评审二轮 R7：按乘员归属的字段。改这张表要同时改设计文档 §4 的边界表——
#: 「哪一半是共享的」是产品裁决，不是实现细节。
_OWNED_FOCUS_FIELDS = ("session_constraints", "active_task")
#: 归属不明时的默认乘员（与 `prefs["occupant_id"]` 的缺省同一个词）。
_DEFAULT_OCCUPANT = "primary"


def _occupant_of(occupant_id: str | None) -> str:
    return str(occupant_id or "").strip() or _DEFAULT_OCCUPANT


def _project_owned_fields(record: dict, occupant_id: str | None) -> dict:
    """存储记录 → 这位乘员看到的焦点（私有两格取自他自己的格子）。

    旧记录（没有 `by_occupant`）里的扁平私有值算 `primary` 的：别人读不到，
    而缺省乘员的连续性一字不变——归属不确定时不把未归属的偏好写进某位乘员名下。
    """
    out = dict(record or {})
    owned = out.pop("by_occupant", None)
    owned = owned if isinstance(owned, dict) else {}
    who = _occupant_of(occupant_id)
    mine = owned.get(who) if isinstance(owned.get(who), dict) else None
    if mine is None and not owned and who == _DEFAULT_OCCUPANT:
        return out                       # 旧记录 + 缺省乘员：行为逐字不变
    for name in _OWNED_FOCUS_FIELDS:
        value = (mine or {}).get(name)
        out[name] = value if value is not None else (
            {} if name in ("session_constraints", "active_task") else None)
    return out


def _merge_owned_fields(stored: dict, focus_record: dict,
                        occupant_id: str | None) -> dict:
    """本轮焦点 + 存储里别人的格子 → 要落盘的记录（私有两格只写说话人自己的）。"""
    out = dict(focus_record or {})
    owned = dict((stored or {}).get("by_occupant") or {})
    who = _occupant_of(occupant_id)
    legacy = {name: (stored or {}).get(name) for name in _OWNED_FOCUS_FIELDS}
    if not owned and any(legacy.values()):
        # 迁移：旧记录的扁平私有值归 `primary`（读侧同一条判据）
        owned[_DEFAULT_OCCUPANT] = {name: value for name, value in legacy.items() if value}
    mine = dict(owned.get(who) or {})
    for name in _OWNED_FOCUS_FIELDS:
        mine[name] = out.get(name)
    owned[who] = {name: value for name, value in mine.items() if value}
    if not owned[who]:
        owned.pop(who, None)
    out["by_occupant"] = owned
    return out


def expire_short_term(focus: "Focus | None", *, now: float | None = None) -> "Focus | None":
    """按 `focus_ts` 让**短时引用**过期，活动状态原样保留（W09）。原地改、返回同一对象。"""
    if focus is None:
        return None
    stamp = float(getattr(focus, "focus_ts", 0.0) or 0.0)
    now = time.time() if now is None else now
    if stamp <= 0 or now - stamp <= _FOCUS_SHORT_TTL_S:
        return focus
    defaults = Focus()
    for name in _SHORT_TERM_FIELDS:
        value = getattr(defaults, name)
        setattr(focus, name, list(value) if isinstance(value, list) else value)
    # 最新候选视图从台账重新派生：台账里仍活着的组（按各自 ts）照旧可被序数指代
    _derive_choice_view(focus)
    return focus


def _render_focus(focus, drop_sticky_places: bool = False) -> str:
    """结构化焦点 → 紧凑 prompt 块（仅非空字段）。供 LLM 在用户话术含指代时复用。

    `drop_sticky_places=True`（C6-B）：**这一轮不注入粘性的地点/候选焦点**
    （上个地点/上个目的地/上个城市/最新候选）。真栈 T55：三轮前的「万象城」与
    「上海外滩」还挂在焦点里，planner 读到后把「先去接我妈」整个丢了，抓住
    「川菜馆」+旧「万象城」组合出「万象城附近的川菜」并反问城市。
    **安全告警、车控对象焦点与活动路线不在让路名单里**——前者是这轮回答的前提
    （Q9），后两者是「这一句在说哪台设备/哪条路」的必要输入，让掉会制造新的洞。
    """
    if not focus or focus.is_empty():
        return ""
    parts = []
    # Q9：未解除的安全告警**排在最前**。它不是「上下文的一部分」，它是这轮回答的前提
    # ——QA 轮 SF3 实测，红色机油灯之后第二轮答天气、第三轮执行音量，正因为这一行不存在。
    alert = focus.safety_alert or {}
    if safety_alert_active(alert):
        grade = "需立即停车处置" if alert.get("level") == "critical" else "需尽快处理"
        sig = alert.get("signal") or "车辆告警"
        parts.append(f"⚠本会话有未解除的安全告警：{sig}（{grade}）"
                     f"——回答任何问题都必须先满足这条安全约束，不得被普通建议覆盖")
    # W02 受保护结构区：用户**在这次会话里说过的**约束/改口。此前它只经 meta 下发给 nearby，
    # planner 的 prompt 里一个字都没有——「不吃辣」说过之后模型规划下一步时并不知道。
    # 值是投影（`runtime.session_constraints` 的扁平键），话术按当前值渲染，改口后显示改口后的。
    constraints = focus.session_constraints or {}
    if constraints:
        # 词表与致谢话术共用 `runtime.session_constraints.phrase_of`（W13 F09-a）；
        # 「想吃辣」在这里带「今天」是焦点块自己的时间框架。
        words = []
        for key in ("no_spicy", "no_queue"):
            if key in constraints:
                phrase = phrase_of(key, constraints[key])
                if phrase:
                    words.append(("今天" + phrase) if (key, constraints[key]) == ("no_spicy", False)
                                 else phrase)
        if words:
            parts.append("本次会话约束=" + "/".join(words))
    # W07：活动任务帧——planner 判「这句是不是在改它」的对象（acts=correct 的前提）。
    # 只渲染意图、版本与最多四个标量槽（值截 20 字）；坐标之类不在槽里。
    task = focus.active_task or {}
    if active_task_live(task):
        pairs = []
        for key, value in list((task.get("slots") or {}).items())[:4]:
            if isinstance(value, (str, int, float)) and str(value).strip():
                pairs.append(f"{key}={str(value)[:20]}")
        revision = int(task.get("revision") or 1)
        seg = f"当前任务={task.get('intent') or ''}（第{revision}版，{task.get('outcome') or ''}）"
        if pairs:
            seg += "：" + "/".join(pairs)
        parts.append(seg)
    if focus.last_intent:
        parts.append(f"上一轮意图={focus.last_intent}")  # 省略式追问（「明天呢」）延续判据
    if focus.obj:
        parts.append(f"对象={focus.obj}")
    if focus.positions:
        parts.append("位置=" + "/".join(focus.positions))
    if focus.attr:
        parts.append(f"属性={focus.attr}")
    if focus.last_poi and not drop_sticky_places:
        parts.append(f"上个地点={focus.last_poi}")
    if focus.last_destination and not drop_sticky_places:
        parts.append(f"上个目的地={focus.last_destination}")
    if focus.last_city and not drop_sticky_places:
        parts.append(f"上个城市={focus.last_city}")
    if focus.last_stock_symbol:
        parts.append(f"上个股票标的={focus.last_stock_symbol}")
    if focus.last_choices and not drop_sticky_places:
        purpose = ("顺路途经点选择"
                   if focus.last_choice_purpose == "waypoint" else "列表选择")
        parts.append(f"最新候选用途={purpose}")
        newest = newest_candidate_set(focus, allow_fallback=True) or {}
        # W08：同一查询的第 N 批（「换一批」之后）——模型该知道用户已经看过前几批
        revision = int(newest.get("revision") or 1)
        tag = f"（同一查询第{revision}批）" if revision > 1 else ""
        parts.append("最新候选" + tag + "=" + "/".join(
            f"{idx}:{name}" for idx, name in enumerate(focus.last_choices, 1)))
        # W08：**较早的另一批**（同能力不同查询）也渲染出来，带它的称呼——「刚才万象城那批
        # 第二家」的参照系要在 prompt 里。只渲染一组、只渲染非兜底、只渲染名字。
        earlier = _earlier_candidate_set(focus, newest)
        if earlier:
            who = "/".join(x for x in (
                str(earlier.get("label") or ""), str(earlier.get("place_hint") or "")) if x)
            names = [str(i.get("name") or "") for i in earlier.get("items", [])][:5]
            names = [n for n in names if n]
            if names:
                parts.append(f"较早候选[{who or '另一批'}]=" + "/".join(
                    f"{idx}:{name}" for idx, name in enumerate(names, 1)))
    # G8：活动路线只渲染名字与时限，**绝不渲染坐标**（坐标进 prompt 只会诱导模型
    # 自己编——last_places 同款纪律）。这一行是 planner 分开「改当前路线
    # （navigation.reroute）」与「改行程（trip.modify）」的会话状态判据。
    route = focus.active_route or {}
    if route.get("destination"):
        seg = f"当前正在导航：目的地={route['destination']}"
        wp_names = [str(w.get("name") or "") for w in (route.get("waypoints") or [])
                    if isinstance(w, dict) and w.get("name")]
        if wp_names:
            seg += "（途经：" + "、".join(wp_names) + "）"
        try:
            ab = int(route.get("arrive_by_ts") or 0)
        except (TypeError, ValueError):
            ab = 0
        if ab > 0:
            # 焦点里渲染给模型看的时刻也要按业务时区（容器 TZ=UTC）——
            # 这里偏 8 小时，模型转述出去就是一句错的约束。
            seg += f"，须{clock_hhmm(ab)}前到达"
        parts.append(seg)
    if not parts:
        return ""
    return ("当前对话焦点（用于指代消解，仅在用户话术含指代/省略式追问时参考）：\n"
            + " ".join(parts) + "\n\n")


def _scan_positions(slots: dict) -> list[str]:
    """从槽位值里扫出座位/区域词（主驾/副驾/后排…）。

    长词先认、被已认出的长词包含的短词不再单算（「副驾驶」不会再多出一个「副驾」）——评审四轮 R4-04 起「关掉」按位置
    逐个反向，同一个位置被数两次就是同一个动作执行两次。"""
    found: list[str] = []
    for v in (slots or {}).values():
        s = str(v)
        for w in _POSITION_WORDS:
            if w in s and w not in found and not any(w in longer for longer in found):
                found.append(w)
    return found


def _first_poi(data: dict) -> str:
    """从结果 data 里尽力取第一个 POI/地点名（供"还是刚才那家"指代）。"""
    if not isinstance(data, dict):
        return ""
    items = data.get("items") or data.get("stops")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        it = items[0]
        return str(it.get("name") or it.get("title") or it.get("poi_name") or "")
    return str(data.get("name") or data.get("poi_name") or "")


def _valid_route_session(raw) -> dict:
    """校验并规范化 `_route_session` 保留键（G8）。非法返回空 dict。

    模型输出是不可信输入的同款纪律（CLAUDE.md §6）：防御要防到真正被拿去消费的
    那个值——waypoints 逐项校验，**非法元素直接丢、不做 str() 转换**。
    （该键由 navigation Agent 服务端构造、不经 LLM，但 data 是自由 Struct 通道，
    按不可信输入设防成本为零。）"""
    if not isinstance(raw, dict):
        return {}
    dest = str(raw.get("destination") or "").strip()
    try:
        lat, lng = float(raw.get("lat")), float(raw.get("lng"))
    except (TypeError, ValueError):
        return {}
    if not dest or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return {}
    waypoints = []
    for w in (raw.get("waypoints") or [])[:8]:
        if not isinstance(w, dict):
            continue
        name = str(w.get("name") or "").strip()
        try:
            wlat, wlng = float(w.get("lat")), float(w.get("lng"))
        except (TypeError, ValueError):
            continue
        if name and -90 <= wlat <= 90 and -180 <= wlng <= 180:
            waypoints.append({"name": name, "lat": wlat, "lng": wlng})
    out = {"destination": dest, "lat": lat, "lng": lng, "waypoints": waypoints,
           "strategy": str(raw.get("strategy") or "")}
    for key in ("arrive_by_ts", "ts"):
        try:
            v = int(raw.get(key) or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            out[key] = v
    out.setdefault("ts", int(time.time()))
    return out


#: 安全告警的**总龄**上限（秒）。⚠ 它不是「告警能活多久」——真实生效的是两个约束的
#: 交集：焦点态本身 `_FOCUS_TTL`（W09 起 7200s）**每成功一轮就续期**，而告警的 `ts`
#: **接力时原样携带、不续期**（同 last_places/active_route 纪律）。
#: 于是语义是：**对话持续活跃（轮间隔 ≤ 焦点 TTL）时告警一直在，但总龄超过本值即失效**。
#: 取 2h：一次未解除的警告在一次出行内应当一直可见；停一晚再上车不该还挂着上次的灯。
_SAFETY_ALERT_TTL = 7200
_SAFETY_LEVELS = ("critical", "amber")


def _valid_safety_alert(raw) -> dict:
    """校验并规范化 `_safety_alert` 保留键（Q9）。非法返回空 dict。

    同 `_valid_route_session`：**非法元素直接丢、不做 str() 转换**——转出来的值
    匹配不上任何东西，却会在日志里留下一个不存在的等级（CLAUDE.md §6）。
    `level` 必须是枚举内的值：「很严重」这种自由文本一律丢弃，
    否则下游按等级分支时会静默走到 else。
    """
    if not isinstance(raw, dict):
        return {}
    level = raw.get("level")
    if not isinstance(level, str) or level not in _SAFETY_LEVELS:
        return {}
    signal = raw.get("signal")
    signal = str(signal).strip() if isinstance(signal, (str, int, float)) else ""
    out = {"level": level, "signal": signal[:40]}
    try:
        ts = int(raw.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0
    out["ts"] = ts if ts > 0 else int(time.time())
    return out


def input_safety_alert(text: str) -> dict:
    """本轮原话里的安全信号 →`_safety_alert` 形状。认不出返回空 dict。

    **两类信号都要扫**：车辆告警（`alert_level`）与驾驶员状态（`driver_state`）。
    C1-B 立这条判据时写的是「登记挂在输入上，不挂在路由上」，可首版只扫了车辆告警
    ——于是**驾驶员状态的登记仍然是路由的副作用**，正是那条判据自己要消灭的形态。
    2026-08-29 取证（deployed `ed53f8f`，SF4 `--repeat 5`）：「困到睁不开眼了，
    还要开两个小时」有 **2/5** 落 `system.clarify`，那两轮会话里一个疲劳信号都没留下；
    紧接着的「别提醒我，继续开就行」于是答成「好的，我就不打扰你了，路上小心。」
    ——**用户可以拒绝被提醒，系统不可以跟着改口说不用停车**（chitchat 那条 prompt
    早就写着，只是那一轮它手里没有告警）。

    驾驶员状态优先：两类都命中时（「喝了酒，胎压灯还亮着」）取更不可让步的那一档。
    等级与名字取 `DRIVER_STATE_ADVICE`（唯一声明处），**不在这里另立一张表**
    ——road-safety 与 chitchat 声明 `_safety_alert` 时用的就是它，第二份必然漂移。
    """
    state = driver_state(text)
    if state:
        spec = DRIVER_STATE_ADVICE[state]
        return {"level": spec["level"], "signal": spec["signal"]}
    return {"level": alert_level(text), "signal": alert_signal(text)}


#: 严重级序。**只用于同槽比较**，不落盘、不进话术。
_SAFETY_RANK = {"amber": 1, "critical": 2}


def merge_safety_alert(current: dict, candidate: dict, *,
                       now: float | None = None) -> dict:
    """同一格里两个告警谁留下（C1-C，2026-08-26 QA）。

    原实现是**最后写入者胜**：一条 amber 能把仍然有效的 critical 顶掉，
    于是「红色机油灯」之后随便问一句带黄灯的话，会话里的安全约束就降级了。
    单槽不是问题，**没有比较**才是——所以这里加的是一次比较，不是第二个槽。

    三条规则，按顺序：
      · 现存那条已经**过期**（`safety_alert_active` 说了算）⇒ 新的直接顶上；
      · 新的严重级 **≥** 现存 ⇒ 新的赢（同级取新，它带着更新的 ts 与 signal）；
      · 否则保留现存——**降级要有理由，而「用户又说了一句别的」不是理由。**
    """
    if not candidate:
        return current
    if not current:
        return candidate
    if not safety_alert_active(current, now=now):
        return candidate
    new_rank = _SAFETY_RANK.get(str(candidate.get("level") or ""), 0)
    old_rank = _SAFETY_RANK.get(str(current.get("level") or ""), 0)
    return candidate if new_rank >= old_rank else current


def safety_alert_active(alert, *, now: float | None = None) -> bool:
    """告警是否仍在有效期内。消费方一律经此判定，不各自算一遍。"""
    if not isinstance(alert, dict) or alert.get("level") not in _SAFETY_LEVELS:
        return False
    try:
        ts = int(alert.get("ts") or 0)
    except (TypeError, ValueError):
        return False
    return ts > 0 and ((now or time.time()) - ts) <= _SAFETY_ALERT_TTL


# ── Q2 候选集 ────────────────────────────────────────────────────────────
#: 候选项里**允许跨轮留存**的字段。白名单而不是黑名单：`_resume_result` 已经为
#: 「整份 provider 负载落 Redis」付过一次学费（商户 token/电话/地址进了会话态）。
#: 名单本身就是「哪些事实值得跨轮消费」的声明——加字段要有真实消费方（B4 判据）。
#: 候选项留哪些结构化属性。**每个键必须是某个产生方真的产出的名字**，
#: 守卫 `test_candidate_sets.py::test_the_whitelist_is_derived_from_real_producers`
#: 拿逐字复刻的产生方形状比对，加键不登记即红。
#:
#: ⚠ **2026-08-19 修：这张表原本有 7 个死键**（`open_hours`/`business_hours`/
#: `opening_hours`/`distance`/`distance_m`/`tel`/`spec`）。它们是按常见命名**猜**的，
#: 与产生方一个都对不上——`nearby._item()` 出的是 `open_today`/`distance_km`。
#: 于是 §9.1b 声称留住的「营业时间」**一个字都没留住**，I-018「哪家最晚关门」
#: 在真栈里连数据都没有（CD1 判据升级后当场红）。
#: > 判据：**照常见命名猜字段最易被真机否**（商户 badcase 那批的同一条）。
#: > 白名单是与产生方的契约，期望必须从产生方派生，不能从直觉派生。
#:
#: ⚠ `lat`/`lng`/`city`/`address` 当前也没有消费方（跨轮门店锚定走的是
#: `last_places` 那条专门通道）。**本批刻意不动它们**：删它们是独立的收敛问题，
#: 混进来会让本批读数说不清自己证明了什么（同 CD2 那条「把两件事分开报」）。
#: Q10 第 7 步的双入口收敛已声明要用 `id` 做确定性匹配，那个键有近期消费方。
_CANDIDATE_ITEM_KEYS = (
    "id", "name", "lng", "lat", "city", "address", "category",
    # I-018/I-023 需要的结构化事实：卡片上渲染了、下一轮却一个字都没有的那些。
    # 消费方是 `candidate_query.py` 的四个聚合维度（关门/价格/评分/距离）。
    "open_today", "open_week", "rating", "cost", "price", "distance_km",
)
#: 一个会话最多留几组候选。同挂起表的理由：候选是**用户脑子里记得的东西**。
_CANDIDATE_SETS_MAX = 3
#: 候选集时效（秒）。同 `last_places` 三条纪律：粘性接力**不续期**，
#: 时效从产生那一刻起算——接力多少轮都不能让「刚才那家」变成「上周那家」。
_CANDIDATE_TTL_S = 900.0


#: 组标签（保留键 `_candidate_label`）的长度闸。声明成一整句话就不再是「称呼」——
#: 用户不会原样说出来，`label in text` 于是永远不命中，而它还会让 2 字前缀通道
#: 变得任意。<2 字视为**未声明**（同「未声明的产生方逐字零行为变化」那条）。
_CANDIDATE_LABEL_MAX = 20
_CANDIDATE_LABEL_MIN = 2


def _candidate_label(data: dict) -> str:
    """产生方声明的「这一组该怎么被称呼」（I-030 组指代的唯一来源）。

    判据与 `_fallback` **同一条**：编排看不出这一组该叫什么名字，产生方知道。
    `mcd.menu` 的卡上就写着 `merchant: "麦当劳"`、nearby 卡上写着 `keyword`，
    而 `data` 里一个字都没有——**和「菜单只进 ui_card 从没进过候选集」逐字同形**
    （§9.27 末段）。可被指代的事实两边都要有。
    """
    label = str((data or {}).get("_candidate_label") or "").strip()
    return label[:_CANDIDATE_LABEL_MAX] if len(label) >= _CANDIDATE_LABEL_MIN else ""


def _candidate_items(raw_items: list) -> list[dict]:
    """按白名单裁剪候选项。名字是唯一必需字段——没名字的项无从指代。"""
    out: list[dict] = []
    for item in raw_items[:10]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("title")
                   or item.get("label") or item.get("poi_name") or "").strip()
        if not name:
            continue
        kept = {"name": name}
        for key in _CANDIDATE_ITEM_KEYS:
            if key == "name" or key not in item:
                continue
            value = item[key]
            if isinstance(value, (str, int, float, bool)) and value != "":
                kept[key] = value
        out.append(kept)
    return out


def _live_candidate_sets(sets: list, now: float | None = None) -> list[dict]:
    """按各自的 ts 限龄。0 = 旧数据无时间戳，按过期处理（同 last_places_ts 口径）。"""
    now = time.time() if now is None else now
    return [s for s in sets
            if isinstance(s, dict)
            and 0 < float(s.get("ts") or 0) > now - _CANDIDATE_TTL_S]


#: 「这句话开头就在指代一个列表里的某一项」。**锚在句首**是刻意的：
#: 「第二天第一个景点安排什么」里的序数指的是行程内部，不是上一份候选列表。
#:
#: ⚠ 仓库里已经有三处序数正则，各自回答**另一个**问题，所以这里不复用也不合并：
#:   · `engine._is_topic_change` 的 `fullmatch` —— 「这是不是一句**裸**序号选择」；
#:   · `planning._FOCUS_DEPENDENT_ELLIPSIS_RE` —— 「这是不是焦点依赖的省略句」；
#:   · `actionability` 那条 —— 形态分类器的特征。
#: 本条问的是「**这句话在引用某一份候选**吗」（可以带别的内容：第一个营业到几点）。
#: 合并成一条会让四个判定互相牵连——B4 那条判据反对的是「两份声明说同一件事」，
#: 不是「不同的问题各自有判据」。
_CANDIDATE_REFERENCE_RE = re.compile(
    r"^(?:刚才|那个|这个|请问|帮我?看看|看看|查一下|问一下)?[，,]?\s*"
    r"第\s*[一二三四五六七八九十\d]+\s*(?:个|家|项|条|种|款)")


def references_a_candidate(text: str) -> bool:
    return bool(_CANDIDATE_REFERENCE_RE.match(str(text or "").strip()))


def newest_candidate_set(focus, *, allow_fallback: bool = False) -> dict | None:
    """序数指代该绑到哪一组（Q2 的核心判定）。

    **优先最近一份非兜底候选**——兜底/降级搜出来的那份不得顶替用户点名的那份
    （N5：I-011 里那次重搜根本没失败，泛化兜底合法地覆盖了川菜候选，
    于是「刚才列表里的第二家」拿到了兜底那份的第二家）。
    全是兜底时才退回最近一份（`allow_fallback` 由调用方决定要不要退）。
    """
    return _newest_of(_live_candidate_sets(
        getattr(focus, "candidate_sets", None) or []),
        allow_fallback=allow_fallback)


def _newest_of(sets: list[dict], *, allow_fallback: bool) -> dict | None:
    """一批候选组里该绑哪一组。**N5 判据住在这里**，所以组指代（I-030）挑出
    被点名的那几组之后仍然复用它——「兜底不得顶替点名那份」在**任何**取值域上
    都成立，不该因为多了一维筛选就丢掉。"""
    if not sets:
        return None
    for entry in reversed(sets):
        if not entry.get("is_fallback"):
            return entry
    return sets[-1] if allow_fallback else None


def _earlier_candidate_set(focus, newest: dict) -> dict | None:
    """台账里比 `newest` 早、且不是同一查询的最近一组非兜底候选（W08 渲染用）。"""
    sets = _live_candidate_sets(getattr(focus, "candidate_sets", None) or [])
    newest_key = candidate_merge_key(newest) if newest else None
    for entry in reversed(sets):
        if entry is newest or candidate_merge_key(entry) == newest_key:
            continue
        if entry.get("is_fallback"):
            continue
        return entry
    return None


#: 查询里「在哪一带找」的槽名（W08 地点提示）：产生方的 `_candidate_label` 只说品类/品牌
#: （「餐饮」），两次不同地点的同类检索靠它才分得开。零领域值，只有槽名。
#: `keyword` 垫底：真栈 CD8 三次取样里 planner 有一次把「万象城」填进 `keyword` 而不填
#: `location`——用户说的还是「万象城那批」，这一组得能被这么叫。
_PLACE_HINT_SLOTS = ("location", "destination", "near", "area", "city", "keyword")


def _place_hint(slots: dict | None, data: dict | None = None) -> str:
    """这批候选「在哪一带找」：产生方声明的 `_candidate_place`（批 7 追加）优先，其次从查询槽派生。

    真栈 continuity（2026-09-22）：planner 把「科技园附近的餐厅」填成 `{cuisine, sort}`——地名整个丢了，
    nearby 从原话锚定了科技园并搜对了，但这里只看槽 ⇒ 地点提示空 ⇒ 这批叫不出名 ⇒ 「科技园那批第二家」
    零命中退回最新那批。产生方知道它在哪搜的，编排不该替它从槽里猜。
    """
    declared = str((data or {}).get("_candidate_place") or "").strip()
    if _CANDIDATE_LABEL_MIN <= len(declared):
        return declared[:_CANDIDATE_LABEL_MAX]
    for key in _PLACE_HINT_SLOTS:
        value = str((slots or {}).get(key) or "").strip()
        if _CANDIDATE_LABEL_MIN <= len(value):
            return value[:_CANDIDATE_LABEL_MAX]
    return ""


def candidate_merge_key(entry: dict) -> tuple:
    """台账合并键（W08）：`(source_intent, purpose, is_fallback, query_signature)`。

    同能力**不同查询**（A 附近 / B 附近）是两批，共存；同查询再来一次（「换一批」）是
    同键新版本。旧键没有第四维，第二批当场顶掉第一批（评审 F03）。老记录没有
    `query_signature`（空串）⇒ 与旧键逐字同值。
    """
    return (entry.get("source_intent"), entry.get("purpose"),
            bool(entry.get("is_fallback")), str(entry.get("query_signature") or ""))


#: 墓碑（W18-a）：留几条、留多久。2h 与安全告警 / 会话约束同一档——「刚才那批」在一次出行里都还叫得出名。
_RETIRED_SETS_MAX = 6
_RETIRED_TTL_S = 7200.0


def _tombstone(entry: dict, now: float) -> dict | None:
    """被顶掉 / 过期的候选组 → 墓碑（只留名字）。叫不出名的组（无 label 无地点提示）不立——
    用户也点不了名。"""
    label = str((entry or {}).get("label") or "").strip()
    hint = str((entry or {}).get("place_hint") or "").strip()
    if len(label) < _CANDIDATE_LABEL_MIN and len(hint) < _CANDIDATE_LABEL_MIN:
        return None
    return {"label": label, "place_hint": hint,
            "source_intent": str(entry.get("source_intent") or ""), "ts": now}


def _live_tombstones(stones: list, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    return [t for t in stones
            if isinstance(t, dict) and 0 < float(t.get("ts") or 0) > now - _RETIRED_TTL_S]


def retired_candidate_hit(text: str, focus) -> dict | None:
    """这句话点名的是一批**已经不在手边**的候选吗 → 那条墓碑（或过期仍躺在台账里的那组），
    否则 None。

    只在**没有任何活着的组被点名**时才算：活着的同名组永远优先（「换一批」的新版本就是它）。
    判据复用 `label_hit`——名字通道只有一份。消费方（engine）据此走 `candidate_missing` 的
    第二种话术，而不是让 `newest_candidate_set` 顶替作答。
    """
    text = str(text or "")
    sets = list(getattr(focus, "candidate_sets", None) or [])
    live = _live_candidate_sets(sets)
    if any(label_hit(text, s) is not None for s in live):
        return None
    now = time.time()
    expired_in_place = [s for s in sets if s not in live and isinstance(s, dict)]
    stones = _live_tombstones(list(getattr(focus, "retired_candidate_sets", None) or []), now)
    for entry in reversed(expired_in_place + stones):
        if label_hit(text, entry) is not None:
            stone = _tombstone(entry, now) or {}
            stone["ts"] = float(entry.get("ts") or now)
            return stone
    return None


def label_hit(text: str, entry: dict) -> int | None:
    """这句话在**哪个位置**点名了这一组；没点名 → None（I-030 组指代）。

    **从组标签派生，不是第二张词表**——同 `candidate_query._named` 那条纪律：
    名字通道由候选集自己派生，用户换个说法它不会失效，也不需要编排知道
    「麦当劳」属于哪个域（那是 R2.1 明令不许写进编排核心的东西）。

    两条通道按序求值，都在**原话**上找（位置要能拿回来，跨组切句靠它）：
      · 整个标签——「麦当劳」「瑞幸」；
      · 标签的 **2 字前缀**——产生方声明「川菜馆」而用户说的是「川菜」。
    中文品牌/品类词的判别信息几乎都在前缀；放开到「任意公共子串」就等于放弃判据。
    """
    # W08：地点提示是第二条称呼通道——「万象城那批」与「科技园那批」标签都是「餐饮」，
    # 只有查询里的地点分得开它们。标签走整词 + 2 字前缀；地点提示另加**≥3 字公共子串**：
    # 真栈 CD8 第 2 次取样 planner 填的是 `location=深圳湾万象城`，用户说的是「万象城」——
    # 前缀「深圳」既对不上、也不该对上（它会命中任何提到深圳的句子）。3 字下限是为了
    # 把「万象城 / 科技园 / 欢乐海岸」留在通道里、把「深圳」这类泛地名挡在外面。
    label = str((entry or {}).get("label") or "").strip()
    if len(label) >= _CANDIDATE_LABEL_MIN:
        for needle in (label, label[:_CANDIDATE_LABEL_MIN]):
            at = str(text or "").find(needle)
            if at >= 0:
                return at
    hint = str((entry or {}).get("place_hint") or "").strip()
    if len(hint) >= _CANDIDATE_LABEL_MIN:
        at = str(text or "").find(hint)
        if at >= 0:
            return at
        return _common_run_position(str(text or ""), hint, _PLACE_HINT_MIN_RUN)
    return None


#: 地点提示与原话之间要有多长的公共子串才算点名（见 `label_hit`）。
_PLACE_HINT_MIN_RUN = 3


def _common_run_position(text: str, hint: str, min_run: int) -> int | None:
    """`text` 里第一处与 `hint` 有 ≥min_run 字公共子串的位置；没有 → None。字符串都很短，O(n·m)。"""
    best_at, best_len = None, 0
    for i in range(len(text)):
        for j in range(len(hint)):
            k = 0
            while i + k < len(text) and j + k < len(hint) and text[i + k] == hint[j + k]:
                k += 1
            if k > best_len:
                best_at, best_len = i, k
    return best_at if best_len >= min_run else None


def resolve_candidate_scope(text: str, focus) -> tuple[dict | None, list[dict]]:
    """「这句话在说哪一组候选」→ `(主组, 被点名的那几组)`（I-030）。

    ## 它修的是一个比卡上写的更严重的形态

    卡上写的是「跨组比较做不了」（答非所问）。真实形态是**跨组会给出一个算错的
    确定性答案**：两家菜单都在会话里时，「**麦当劳**的第二个多少钱」被
    `newest_candidate_set` 绑到瑞幸那组，零方差地答出「「生椰拿铁」16 元」
    ——商品名与价格都真实存在，只是答的是另一家。**比编造更难被发现**，
    因为没有任何一处对不上。

    根因是判据面上**根本没有「哪一组」这一维**：同第 7.5 步「留一条缝模型就编一个」
    的同族第二例，只是这次编的不是模型，是短路自己。
    ⇒ **凡是「系统持有的事实」，判据面就得是闭合的**——多一份候选就是多一维。

    ## 三条规则

    · 零命中 → 退回 `newest_candidate_set`，**行为逐字同旧**（没有标签的部署、
      没点名的句子，一个字都不变）；
    · 命中一组 → 就是它；
    · 命中多组 → 主组取**命中集里**那一组（仍走 `_newest_of`，N5 继承），
      **绝不越出命中集**。「附近的麦当劳」→「看看菜单」会产生两个都叫「麦当劳」
      的组（门店列表 + 菜单），它们是同一家商户的两份东西不是两家——此时用户
      指的是新的那份，而**关键在于两份都姓麦当劳，取哪份都不会拿瑞幸的事实作答**。

    第二个返回值给跨组算子用（`candidate_query`）：只有句子**点名了 ≥2 组**时
    才谈得上比较，那是比现状更严的条件，所以误伤面不因此扩大。
    """
    sets = _live_candidate_sets(getattr(focus, "candidate_sets", None) or [])
    named = [s for s in sets if label_hit(text, s) is not None]
    if named:
        return _newest_of(named, allow_fallback=True), named
    return _newest_of(sets, allow_fallback=True), []


def candidate_set_for(focus, domain: str) -> dict | None:
    """给**某一域的消费步**挑候选组：优先同域那一组，没有才退回最新那组（I-030）。

    判据是**结构的、零领域词**：步的 intent 域 == 组的 `source_intent` 域。
    下发面此前一律取最新那一组，于是「先看瑞幸菜单、再说在麦当劳点第一个」时，
    `mcd.order` 那一步拿到的是 `source_intent=luckin.menu` 的下发——桥侧
    `candidate_ref._belongs_to` 按域前缀拒收（**那一侧是 fail-safe 的，没翻错**），
    但麦当劳那组明明还在焦点里，用户的「第一个」就这么白丢了。

    退回最新那组是为了**逐字保持旧行为**：同域没有候选时下发什么都一样
    （消费方自己会按归属判据拒收），换成「什么都不发」反而是一处未经证据的收窄。
    """
    sets = _live_candidate_sets(getattr(focus, "candidate_sets", None) or [])
    prefix = str(domain or "").strip()
    same = [s for s in sets
            if prefix and str(s.get("source_intent") or "").split(".", 1)[0] == prefix]
    return _newest_of(same or sets, allow_fallback=True)


#: 候选集**跨层下发**给 Agent 时的投影（Q10 接手第 7 步的下发面）。
#: 与 `_CANDIDATE_ITEM_KEYS` 是**两张表，故意的**——那张回答「哪些事实值得跨轮留住」
#: （消费方在云侧，四个聚合维度都要数值），这张回答「哪些字段可以**离开编排**、
#: 进到一个 `trust_level: third_party` 的 Agent 里」。两个问题的答案不一样，
#: 合成一张表就会让「留住」自动等于「下发」。
#:
#: 默认只留 `index` 与 `name`；商户菜单额外留一个受限的 `id`：
#: · `lat`/`lng`/`city`/`address` —— 精确位置是红线级敏感上下文（CLAUDE.md §5），
#:   而桥的 manifest 连 `location` scope 都没有；候选集不能成为绕过它的第二条路。
#: · `open_today`/`rating`/`cost`/`price`/`distance_km` —— 它们的消费方
#:   （`candidate_query` 那四个聚合维度）**在云侧**，桥拿到也没人算。
#: · `id` —— 只在 `*.menu` 候选中下发。真栈出现同一菜单两项展示名逐字相同，规范名
#:   无法再唯一落项；按钮和语音都先收敛到同一候选项，再由该服务端商品码闭合身份。
#:   nearby 等位置候选仍不下发 id，避免它成为绕过 `location` scope 的 POI 定位通道。
#: 三条都是 B4 那句「加字段要有真实消费方，无消费方的声明只会漂移」的逐条应用。
_DOWNLINK_ITEMS_MAX = 10
_DOWNLINK_NAME_MAX = 40
_DOWNLINK_ID_MAX = 128


def candidate_downlink(entry: dict | None) -> dict | None:
    """候选集 → 下发给 Agent 的最小投影；没有可下发内容时返回 None。

    形状 `{"source_intent": str, "items": [{"index": 1, "name": str,
    "id"?: str}, ...]}`；`id` 只允许出现在 `*.menu` 候选。
    `index` 是**从 1 开始的卡片序号**——「第一杯」说的就是它。下发方给序号而不是
    让消费方去数数组下标，是因为这里会裁剪（`_DOWNLINK_ITEMS_MAX`），
    下标会随裁剪漂移而序号不会。

    `source_intent` 是消费方**唯一的归属判据**：桥只认自己那家商户产出的候选，
    否则「附近的瑞幸」之后一句「点第一个」会把一个 POI 名塞进 `item_query`。
    """
    source_intent = str((entry or {}).get("source_intent") or "")
    include_identity = source_intent.endswith(".menu")
    items = [it for it in ((entry or {}).get("items") or []) if isinstance(it, dict)]
    out = []
    for index, item in enumerate(items[:_DOWNLINK_ITEMS_MAX], start=1):
        name = str(item.get("name") or "").strip()[:_DOWNLINK_NAME_MAX]
        if name:
            projected = {"index": index, "name": name}
            raw_id = item.get("id")
            if include_identity and isinstance(raw_id, (str, int)) \
                    and not isinstance(raw_id, bool):
                candidate_id = str(raw_id).strip()[:_DOWNLINK_ID_MAX]
                if candidate_id:
                    projected["id"] = candidate_id
            out.append(projected)
    if not out:
        return None
    return {"source_intent": source_intent, "items": out}


def recent_control_execution(history, edge_executed=None) -> tuple[str, str, str] | None:
    """从**执行事实**解出「刚才操作的是哪个车控对象」→ `(对象, 属性, 意图名)`。

    **确定性纯函数、零 LLM、零网络**（QA 卡 Q7-EL1/OR2，2026-08-16）。

    ## 为什么非要它

    `Focus` 只由**云侧规划轮**构建（`update_focus(plan, results)`）。端侧本地快路径
    那 40% 的车控动作根本不上云——真栈对照实测：跑「打开天窗」后
    `planner:focus:*` **0 个 key**，跑「附近有什么好吃的」后 **1 个**。
    于是下一轮说「不用了，关掉」时，planner 手里**一个对象都没有**，只能从对话文本猜：
    三次取样分别是「无动作却答『关上了』」/「反向执行 `sunroof.open`」/ 正确。

    Q6 已经把这件事的事实源建好了（`AppendTurn.actions`，端侧本地轮与云侧规划轮各写各的），
    缺的只是**读**——`clients.get_session` 此前没把 `actions` 带回来。

    ## 取值规则

    · **同轮压过跨轮**：`edge_executed` 是本轮端侧刚执行掉的，比历史里任何一轮都近。
    · 同一组动作里取**最后一个**落在 `_CONTROL_FOCUS` 的（同 `extract_focus` 取
      「最近一个成功控制步」的口径）。
    · **零新映射表**——复用 `_CONTROL_FOCUS`，连取 namespace 的方式都与 `extract_focus`
      逐字相同（`intent.split(".")[0]`）。云侧镜像不 `COPY orchestrator/edge`，
      读不到 `commands.yaml`，这张表本来就是云侧的那一份。

    ⚠ **覆盖边界**：`_CONTROL_FOCUS` 只覆盖确有跨轮省略消费方的少数控制域，
    而 VAL 车控对象远多于此。
    表外对象解不出 ⇒ 返回 None ⇒ 退化成本函数存在之前的行为
    （fail-open）。**不引入新的不一致**——`extract_focus` 本来就是这个覆盖面。

    形状不可信（history 来自 gRPC）：非 dict / actions 非 list / 元素非 str 一律跳过，
    同 CLAUDE.md §6「防御要一路防到真正会被拿去用的那个值」。
    """
    groups: list[list[str]] = []
    if isinstance(history, (list, tuple)):
        trusted_turns = [turn for turn in history if isinstance(turn, dict)]
        latest_exchange = next((
            str(turn.get("exchange_id") or "").strip()
            for turn in reversed(trusted_turns)
            if str(turn.get("exchange_id") or "").strip()
        ), "")
        # Modern turns carry exchange_id. Once present, only the latest exchange
        # is adjacent; scanning through an actionless battery query to an older
        # window action corrupts a newer stock/weather focus. Legacy rows without
        # exchange ids retain the historical best-effort scan.
        if latest_exchange:
            trusted_turns = [
                turn for turn in trusted_turns
                if str(turn.get("exchange_id") or "").strip() == latest_exchange
            ]
        for turn in trusted_turns:
            raw = turn.get("actions")
            if isinstance(raw, (list, tuple)):
                groups.append([a for a in raw if isinstance(a, str) and a.strip()])
    if isinstance(edge_executed, (list, tuple)):
        groups.append([a for a in edge_executed if isinstance(a, str) and a.strip()])
    for names in reversed(groups):
        for name in reversed(names):
            domain = name.strip().split(".", 1)[0]
            if domain in _CONTROL_FOCUS:
                obj, attr = _CONTROL_FOCUS[domain]
                return obj, attr, name.strip()
    return None


#: 执行目标的尺寸上限（端侧签发，但仍按不可信输入收：一路防到真正被拿去用的值，CLAUDE.md §6）。
_TARGETS_MAX = 16
_TARGET_POSITIONS_MAX = 8
_TARGET_TEXT_MAX = 64


def parse_control_targets(raw) -> list[dict]:
    """执行目标 `[{command, positions}]` 的唯一解析处（评审四轮 R4-04）：JSON 串或列表都收，形状不对的元素整条丢。

    `command` 非空串；`positions` 只收非空短串（端侧给的是结构化命令里的中文位置词，如「副驾」）。
    """
    if isinstance(raw, str):
        if not raw.strip():
            return []
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            return []
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[dict] = []
    for item in list(raw)[:_TARGETS_MAX]:
        if not isinstance(item, dict):
            continue
        command = item.get("command")
        if not isinstance(command, str) or not command.strip() or len(command) > _TARGET_TEXT_MAX:
            continue
        positions = item.get("positions") or []
        if isinstance(positions, str):
            positions = [positions]
        if not isinstance(positions, (list, tuple)):
            continue
        clean = [p.strip() for p in positions[:_TARGET_POSITIONS_MAX]
                 if isinstance(p, str) and p.strip() and len(p) <= _TARGET_TEXT_MAX]
        out.append({"command": command.strip(), "positions": clean})
    return out


def target_positions(targets, intent: str) -> list[str]:
    """同一组执行目标里，**这个意图**做过的全部位置（按序去重）。

    这一组里同一意图有一次不带位置（全车 / 缺省范围）⇒ 返回空：那一次已经覆盖了更大的范围，不能把它收窄成某个位置。
    「打开主驾座椅加热，再打开副驾座椅加热」⇒ `[主驾, 副驾]`——只取最后一个位置，「关掉」就漏关一个。
    """
    positions: list[str] = []
    for target in parse_control_targets(targets):
        if target["command"] != intent:
            continue
        if not target["positions"]:
            return []
        for position in target["positions"]:
            if position not in positions:
                positions.append(position)
    return positions


def augment_focus_with_execution(
    focus,
    history,
    edge_executed=None,
    *,
    previous_local_exchange: str = "",
    previous_local_actions=None,
    edge_executed_targets=None,
    previous_local_targets=None,
) -> "Focus | None":
    """用最近执行事实刷新车控焦点；解不出时原焦点不动。

    会话历史按轮次有序，`actions` 是成功执行事实；同轮 `edge_executed` 又比历史更新。
    因此一旦解出，它就应覆盖 Redis 里可能由更早云侧轮次留下的控制对象。只填空会把
    「取不到对象」变成「稳定使用陈旧对象」（云侧调氛围灯→本地开天窗→关掉）。

    覆盖时 agent 清掉；**位置与意图取自同一条事实**（评审四轮 R4-04）：同轮端侧执行与上一轮本地轮次各带端侧签发的
    执行目标，历史轮次用那一轮自己落盘的 `control_targets`（云侧执行过的控制、同轮端侧那半都在里面）。修前一律清空，
    「打开副驾车窗」之后的「关掉」确定性成全车。取不到目标（只有名字的旧轮次）⇒ 位置为空，行为同修前——不猜，也不把
    旧对象的「副驾」粘到新对象上。地点、候选集、活动路线等其他正交焦点原样保留。
    """
    latest_history_exchange = ""
    history_exchange_ids: set[str] = set()
    if isinstance(history, (list, tuple)):
        history_exchange_ids = {
            str(turn.get("exchange_id") or "").strip()
            for turn in history if isinstance(turn, dict)
            and str(turn.get("exchange_id") or "").strip()
        }
        latest_history_exchange = next((
            str(turn.get("exchange_id") or "").strip()
            for turn in reversed(history)
            if isinstance(turn, dict)
            and str(turn.get("exchange_id") or "").strip()
        ), "")
    def apply_control(found, targets=None):
        obj, attr, intent = found
        out = focus if focus is not None else Focus()
        out.obj, out.attr = obj, attr
        # `last_intent` 与 obj 必须来自同一事实：省略守卫按它的 namespace 校验计划。
        out.last_intent = intent
        # 位置也是（R4-04）：只取同一条事实里这个意图做过的位置
        out.positions = target_positions(targets, intent)
        out.last_agent_id = ""
        if latest_history_exchange:
            out.origin_exchange_id = latest_history_exchange
        return out

    # Same-request edge execution is newer than both Redis focus and history.
    edge_found = recent_control_execution([], edge_executed)
    if edge_found is not None:
        return apply_control(edge_found, edge_executed_targets)

    signed_local_found = recent_control_execution([], previous_local_actions)
    if signed_local_found is not None:
        out = apply_control(signed_local_found, previous_local_targets)
        out.origin_exchange_id = str(previous_local_exchange or "").strip()
        return out

    origin = str(getattr(focus, "origin_exchange_id", "") or "").strip() \
        if focus is not None else ""
    previous_local = str(previous_local_exchange or "").strip()
    focus_memory_is_in_flight = bool(
        focus is not None and origin and history_exchange_ids
        and origin not in history_exchange_ids and not previous_local
    )
    # update_focus is saved before that exchange's memory append. If history
    # does not yet contain the origin at all, its older action cannot outrank
    # the newer Redis focus. A server-signed local boundary is the exception.
    local_boundary_is_in_flight = bool(
        previous_local and latest_history_exchange != previous_local
    )
    history_found = None if (
        focus_memory_is_in_flight or local_boundary_is_in_flight
    ) else recent_control_execution(history)
    if history_found is not None:
        # 账本解出的是最近一轮的动作；那一轮若也落过焦点（云侧执行 / 混合路径），它自己记下的执行目标就是位置的来源
        same_exchange = bool(origin) and origin == latest_history_exchange
        saved_targets = getattr(focus, "control_targets", None) if same_exchange else None
        return apply_control(history_found, saved_targets)

    if focus is None:
        return None
    if focus_memory_is_in_flight:
        return focus
    boundary = str(previous_local or latest_history_exchange or "").strip()
    if not boundary or boundary == origin:
        return focus

    # A newer actionless/local exchange is an explicit adjacency break. Keep
    # deliberately sticky ledgers/routes/places, but drop every field whose
    # semantics is “the immediately previous business/control turn”.
    focus.last_intent = ""
    focus.last_agent_id = ""
    focus.obj = ""
    focus.attr = ""
    focus.positions = []
    focus.control_targets = []
    focus.last_city = ""
    focus.last_stock_symbol = ""
    focus.origin_exchange_id = boundary
    return None if focus.is_empty() else focus


def _is_choice_card(card) -> bool:
    """这张卡是不是**用户看得见的选择卡**（序号是它的合法答案）。

    判据逐字同 `engine._suspend` 里那条：`purpose` 以 `_choice` 结尾，
    或 `type == "merchant_choices"`。**同一个问题只许有一份判据**——
    那边判「挂起时要不要留下这张卡的标记」，这边判「它的候选要不要进候选集」，
    问的都是「用户是不是看见了一份可以按序号选的列表」。
    """
    if not isinstance(card, dict):
        return False
    purpose = str(card.get("purpose") or "")
    return purpose.endswith("_choice") or str(card.get("type") or "") == "merchant_choices"


def extract_focus(plan, results) -> "Focus | None":
    """从本轮执行的 plan + 成功结果抽取焦点（best-effort，启发式）。

    控制类取最近一个成功控制步的对象/属性/位置；导航/搜索类取目的地与第一个 POI。
    全空返回 None（不持久、不注入）。绝不抛错——抽取失败由调用方吞掉。"""
    ok = {r.step_id for r in results if getattr(r, "status", None)
          and getattr(r.status, "value", "") == "ok"}
    # I-024（Q10 残余，2026-08-30）：**用户看得见的选择卡，它的候选也要进候选集。**
    # 商户选店卡是 `NEED_SLOT` 的产物（「要在哪家下？」），而本函数原先只扫成功步
    # ⇒ **门店候选集根本不存在** ⇒ 下一句「第一个」无处可解、`say_button` 也没按钮。
    #
    # 放宽面刻意窄到只剩一种形态：**NEED_SLOT ∧ 它的卡是选择卡**。
    # 判据逐字复用 `engine._suspend` 那条既有的（`purpose` 以 `_choice` 结尾
    # 或 `type == "merchant_choices"`）——那里认定「这张卡用户看得见、序号是它的
    # 合法答案」，这里问的是同一个问题，**不许写第二份**。
    # ⚠ 为什么不能干脆收全部 NEED_SLOT：C10-A 的那条铁律——
    # **「第N条」只许指向用户最后一眼看到的那份列表**。没有渲染成选择卡的
    # NEED_SLOT 步，它的 items 用户一眼都没见过，收进来就是给序数指代埋雷。
    visible_choice = {
        r.step_id for r in results
        if getattr(getattr(r, "status", None), "value", "") == "need_slot"
        and _is_choice_card(getattr(r, "ui_card", None))}
    by_id = {r.step_id: r for r in results}
    focus = Focus()
    for step in getattr(plan, "steps", []):
        if step.id not in ok and step.id not in visible_choice:
            continue
        if step.id in visible_choice and step.id not in ok:
            # 只取候选集这一维：控制焦点/目的地/城市等都是「这一步做成了什么」，
            # 而它没做成。**放宽的是可见性，不是成功与否。**
            #
            # ⚠ **候选从卡里取，不是从 `data` 里取**（2026-08-30，第三层）：
            # 成功步的候选走 `data["items"]`（那是产生方给下游用的结构化结果），
            # 而**选择卡的产生方把候选放在 `ui_card["items"]`**——
            # `luckin._store_choices` 的 `data` 只有 `checkout_token`，
            # `charging` 那条 `data` 干脆是空的。
            # 于是前两层都改对了、真栈仍答「没有您刚才那页选项的记录」。
            # **而这一支本来就该读卡**：它的主张是「**用户看得见的那份列表**」，
            # 卡片正是他看见的东西，`data` 不是。
            result = by_id.get(step.id)
            data = getattr(result, "data", None) or {}
            card = getattr(result, "ui_card", None) or {}
            choice_items = (card.get("items") if isinstance(card, dict) else None)
            if not isinstance(choice_items, list):
                choice_items = data.get("stops") or data.get("items")
            if isinstance(choice_items, list):
                items = _candidate_items(choice_items)
                if items:
                    focus.candidate_sets.append({
                        "source_intent": step.intent or "",
                        "agent_id": step.agent_id or "",
                        "purpose": "list",
                        "ts": time.time(),
                        "is_fallback": bool(data.get("_fallback")),
                        "label": _candidate_label(data),
                        "items": items,
                        # W08：这一组是**哪次查询**产的；同能力不同查询靠它共存
                        "query_signature": step_fingerprint(step.intent or "", step.slots),
                        "place_hint": _place_hint(step.slots, data),
                        "revision": 1,
                    })
            continue
        domain = (step.intent or "").split(".")[0]
        if domain in _CONTROL_FOCUS:
            focus.obj, focus.attr = _CONTROL_FOCUS[domain]
            pos = _scan_positions(step.slots)
            # 位置描述的是**这一步**（与 obj / last_intent 同一步）：前一个控制步的「副驾」不许粘到后一个不带位置的步上
            # （修前只在有值时赋值：「打开副驾车窗，再开空调」之后的「关掉」会是 `hvac.off {副驾}`）
            focus.positions = pos
            focus.last_agent_id, focus.last_intent = step.agent_id, step.intent
            # 评审四轮 R4-04：这一步真正执行过的目标随焦点落盘，下一轮账本覆盖时位置从这里取
            focus.control_targets.append({"command": step.intent, "positions": list(pos)})
        dest = (step.slots or {}).get("destination")
        if dest:
            focus.last_destination = str(dest)
        if step.intent in WEATHER_CONTEXT_INTENTS:
            city = normalize_weather_city_slot((step.slots or {}).get("city"))
            if city:
                focus.last_city = city
        if step.intent == "info.stock":
            symbol = (step.slots or {}).get("symbol")
            if isinstance(symbol, str) and symbol.strip():
                focus.last_stock_symbol = symbol.strip()
        data = getattr(by_id.get(step.id), "data", None) or {}
        poi = _first_poi(data)
        if poi:
            focus.last_poi = poi
        choice_items = data.get("stops") or data.get("items")
        if isinstance(choice_items, list):
            # Q2：候选集升格成一等对象。名字数组（last_choices）改由它派生，
            # **结构化属性一并留下**——卡片渲染完就丢，是 I-018/I-023 的成因。
            items = _candidate_items(choice_items)
            if items:
                focus.candidate_sets.append({
                    "source_intent": step.intent or "",
                    "agent_id": step.agent_id or "",
                    "purpose": ("waypoint" if isinstance(data.get("stops"), list)
                                else "list"),
                    "ts": time.time(),
                    # 兜底与否**由产生方声明**（保留键 `_fallback`，同 `_route_session`
                    # 族）：编排看不出「搜的和他说的是不是一回事」。
                    "is_fallback": bool(data.get("_fallback")),
                    # 「这一组该怎么被称呼」（保留键 `_candidate_label`，I-030）。
                    # 同族同判据：编排看不出 `mcd.menu` 那一组该叫「麦当劳」。
                    # 未声明 = 空串 = 这一组点不了名，行为逐字同旧。
                    "label": _candidate_label(data),
                    "items": items,
                    # W08：查询签名 / 地点提示 / 版本（见上一处同款注释）
                    "query_signature": step_fingerprint(step.intent or "", step.slots),
                    "place_hint": _place_hint(step.slots, data),
                    "revision": 1,
                })
        # 导航 Agent 的成功结果带地图已解析坐标。只从 navigation 域消费，避免把天气/
        # 搜索结果里的同名字段误当成下一轮“那边”的目的地。
        if domain == "navigation":
            resolved_destination = data.get("destination")
            if resolved_destination:
                # 地图已解析的具体地点比 Planner 原始模糊槽（如「南山科技园」）更权威。
                focus.last_destination = str(resolved_destination)
            try:
                lat, lng = float(data.get("lat")), float(data.get("lng"))
                if -90 <= lat <= 90 and -180 <= lng <= 180:
                    focus.destination_lat, focus.destination_lng = lat, lng
            except (TypeError, ValueError):
                pass
        if not focus.last_agent_id:
            focus.last_agent_id, focus.last_intent = step.agent_id, step.intent

    # W07 任务帧：本轮**最后一个**任务性步骤（非 response_only；OK ⇒ completed，
    # 可见选择卡的 NEED_SLOT ⇒ pending_slot）。多步计划里取最后一步是刻意的：
    # 「查天气 → 建提醒」里用户会改的是提醒。
    for step in reversed(list(getattr(plan, "steps", []) or [])):
        if bool(getattr(step, "response_only", False)) or not step.intent:
            continue
        result = by_id.get(step.id)
        wrote = task_writes(step, result)
        if step.id in ok:
            focus.active_task = _task_frame(
                plan, step, "completed", "write" if wrote else "read")
            break
        if step.id in visible_choice:
            focus.active_task = _task_frame(plan, step, "pending_slot", "write")
            break

    # 跨轮门店锚定：只认 `nearby.search`，且只留三个标量。
    # **按 results 的 `source_intent` 取，不按传进来的 plan 找步骤**——
    # salvage/replan 轮里调用方给的是重规划后的 plan，`nearby.search` 那一步
    # 根本不在里面，门店列表会从一开始就存不下（2026-08-13 真栈实证：
    # 同一句话走 toolcall 时三轮通、走 toolcall_salvage 时第三轮回到「请先查询附近的瑞幸门店」）。
    # `source_intent` 是**执行器**用权威 Step 盖的章（`_stamp_source`），比 plan 可靠。
    # **不存 deptId 之类商户内部 id**——那是每次现查的事实，缓存它等于把商户的
    # 内部状态当成我们的事实；也不存卡片/话术。
    for result in results:
        if str(getattr(result, "source_intent", "") or "") != "nearby.search":
            continue
        if getattr(getattr(result, "status", None), "value", "") != "ok":
            continue
        places = []
        for item in ((getattr(result, "data", None) or {}).get("items") or [])[:10]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            try:
                lng, lat = float(item.get("lng")), float(item.get("lat"))
            except (TypeError, ValueError):
                continue
            if name and -180 <= lng <= 180 and -90 <= lat <= 90:
                place = {"name": name, "lng": lng, "lat": lat}
                # city 是第四个下游实际引用的安全标量（2026-08-14）：麦当劳官方
                # 检索 searchType=2 城市必填，而指名门店的直点句本轮没有 nearby
                # 生产者——城市只能从跨轮焦点来。仍不存 deptId/卡片/话术。
                city = str(item.get("city") or "").strip()
                if city:
                    place["city"] = city
                places.append(place)
        if places:
            focus.last_places = places
            focus.last_places_ts = time.time()

    # ── Q9 安全告警：**登记挂在输入上，不挂在路由上**（C1-B，2026-08-26 QA P0-01）──
    # 原实现里告警只有一条写入通道：Agent 在 data 里声明保留键 `_safety_alert`，
    # 而全仓只有 manual-rag / road-safety / chitchat 三家会声明。于是
    # **同一句「红色机油灯亮了怎么办」在四个 persona 走了三条不同的错法**：
    # 被规划成 `warning_light.close` 的那一轮里，「红色机油灯」这个事实**整个没进系统**
    # （车控步没有 data 通道），后续三轮消费的还是更早那一轮留下的黄灯。
    #
    # 判据（本轮沉淀，§4.3）：**登记不能是路由的副作用**。告警、焦点、账本这类
    # 「系统必须知道的事实」，写入要挂在**输入或产出的形态**上——路由是有方差的，
    # 事实不能跟着抖。这里扫的是本轮原话，纯函数、零 LLM、与走了哪条路由无关。
    #
    # 顺序在保留键之前：原话是**事实**，Agent 声明是**补充**；两者经同一条严重级
    # 比较合流，所以 Agent 报了更高等级仍然赢，报了更低等级不会把事实降级。
    raw_text = str(getattr(plan, "raw_text", "") or "")
    # C12-B 会话内偏好：与告警登记同一条判据——**登记挂在输入的形态上，不挂在路由上**。
    # T28「我不吃辣，也不想排长队」落的是 chitchat，如果只在 nearby 那条路上抽，
    # 下一轮的推荐就永远读不到它（那正是真栈发生的事）。
    if raw_text:
        # W03 / 评审三轮 R3-02（2026-09-23）：这里存的是**本轮补丁**——`constraints_in` 原样，带 `None` 墓碑（撤销），
        # 含 `others` 子对象；把它合进旧快照、再归一成「说过且仍有效」的，**只在** `update_focus` 做一次。
        # 修前这里先 `merge_constraints({}, …)` 提前归一：只要同一句里还有 SET，DELETE 就被归一掉了——
        # 「今天想吃辣，排不排队都行」只剩 `{no_spicy: False}` 进跨轮合并，旧的 `no_queue=True` 复活，
        # 致谢话术还念出用户刚撤掉的「不想排队」。只有纯删除那一种当年走了保留 `None` 的旁支，所以撤销用例一直是绿的。
        patch = constraints_in(raw_text)
        if patch:
            focus.session_constraints = patch
    if raw_text:
        # QA T47 裁决 A（2026-09-19）：用户明确说「机油灯灭了 / 处理好了 / 是误报」⇒ 会话里那条
        # 告警解除。判据在 `runtime.safety_signal.alert_resolved`（与 chitchat / road-safety /
        # `alert_level` 同一份）：只认点名了告警对象的**完成态陈述**，问句、指令、否定、
        # 「我会靠边停车检查」这类意图陈述都不算。旗子交给 `update_focus` 的接力分支——
        # 这里的 `safety_alert` 本来就是每轮重建的空格子，清它没意义，**挡住接力**才是动作。
        if alert_resolved(raw_text):
            focus.safety_alert = {}
            focus.safety_alert_cleared = True
        # ⚠ 2026-08-29 补驾驶员状态（余项 ①）：判据本体在 `input_safety_alert`，
        # 那里记着「首版只扫车辆告警」为什么等于没有兑现这条判据。
        # 解除之后同一句里若还有新告警（「机油灯灭了但是水温灯亮了」），`alert_level`
        # 只看解除标记之后那半，新告警照常登记——解除的是旧的那条，不是安全约束本身。
        scanned = _valid_safety_alert(input_safety_alert(raw_text))
        if scanned:
            focus.safety_alert = merge_safety_alert(focus.safety_alert, scanned)

    # G8 路线会话：任何成功步经保留键 `_route_session` 声明活动路线（通用契约，
    # 编排不认识 navigation 的私有字段——与 `_escalate` 同族，登记 conventions §9.1）。
    # 多步都声明时取最后一个成功声明（后发的 navigate 是最新路线）。
    for result in results:
        if getattr(getattr(result, "status", None), "value", "") != "ok":
            continue
        data = getattr(result, "data", None)
        session = _valid_route_session(
            data.get("_route_session") if isinstance(data, dict) else None)
        if session:
            focus.active_route = session
        # G8 终止（QA I-017，2026-08-19）：导航被取消 ⇒ 活动路线**清空**。
        # 同族保留键、声明式，编排照旧不认识 navigation 的私有字段。
        # 顺序在 stamp 之后：同一轮不会既开新路线又终止，真出现时以终止为准
        # ——「说了取消却还挂着」是本条要修的那个形态。
        if isinstance(data, dict) and data.get("_route_session_end") is True:
            focus.active_route = {}
            focus.route_ended = True      # 让接力知道这次空是**故意的**
        # Q9 安全告警：同族保留键。多步都声明时按**严重级**比较（C1-C），
        # 不再是「最后写入者胜」——amber 顶掉仍然有效的 critical 是降级，不是更新。
        alert = _valid_safety_alert(
            data.get("_safety_alert") if isinstance(data, dict) else None)
        if alert:
            focus.safety_alert = merge_safety_alert(focus.safety_alert, alert)
    _derive_choice_view(focus)
    return None if focus.is_empty() else focus


def _derive_choice_view(focus: "Focus") -> None:
    """`last_choices`/`last_choice_purpose` = 最近一份**非兜底**候选的派生视图。

    它们是 prompt 渲染面与既有消费方的接口，形状一个字不变；变的是**数据从哪来**
    ——从「每轮重建的一格」变成「候选集台账的一个视图」。于是：
      · 不产生候选的轮不再抹平上一份（I-019）；
      · 兜底那份不再顶替用户点名的那份（N5/I-011）——序数问的是后者。
    """
    entry = newest_candidate_set(focus, allow_fallback=True)
    if not entry:
        return
    focus.last_choices = [str(i.get("name") or "") for i in entry.get("items", [])][:5]
    focus.last_choices = [n for n in focus.last_choices if n]
    if focus.last_choices:
        focus.last_choice_purpose = str(entry.get("purpose") or "list")


class ContextManager:
    """编排器侧上下文统一读写门面。Phase 1 只做装配（assemble）。"""

    def __init__(self, clients, session=None, *, top_k: int | None = None,
                 history_n: int | None = None):
        self.clients = clients
        self.session = session   # SessionStore，供焦点态 load/save（None 则不启用焦点）
        # 取回条数跟着渲染视窗走（W02）：N 对 exchange 要 2N 条，多取一对做裁剪余量。
        # 缺省 N=4 ⇒ 10 条（2026-09-21 起；N=2 时是此前写死的 6 条）。
        if history_n is None:
            history_n = 2 * max(1, _HISTORY_EXCHANGES) + 2
        # 默认给足 headroom：高于当前 agent 规模，预筛只在真正大规模(20+)时触发，
        # 此前是 no-op（避免在小规模误丢需要的 agent，见 dangerous_trunk_confirm 回归）。
        self.top_k = top_k if top_k is not None else int(
            os.getenv("PLANNER_CATALOG_TOP_K", "20"))
        self.history_n = history_n

    async def assemble(self, text: str, ctx, *, mem_on: bool = True,
                       granted_permissions: list[str] | None = None) -> WorkingSet:
        """装配一次规划轮的工作上下文。失败的子项各自降级为空/全量，绝不阻塞规划。

        四个子项（历史 / 记忆召回 / 焦点 / catalog）互不依赖，并发取——各自是一次跨服务
        往返（memory 两次、session 存储一次、registry + embed 一次），串行时规划前的装配段
        真栈读数 p50 632ms / p95 813ms（2026-09-12，App 轮次 memory_enabled=true），
        并发后墙钟按最慢的一项算。降级语义不变：每一项仍在自己函数里吞异常回空。
        """
        async def _none():
            return None

        async def _off():
            return [], memory_read.OFF

        exchanges = _pinned_history_exchanges(ctx)
        (history, history_state), (memories, memory_state), focus, (catalog, registry_agents) = \
            await asyncio.gather(
                self._history(ctx, exchanges=exchanges) if mem_on else _off(),
                self._recall(text, ctx) if mem_on else _off(),
                self._load_focus(ctx.session_id, ctx.user_id,
                                 occupant_id=getattr(ctx, "occupant_id", ""))
                if (mem_on and self.session) else _none(),
                self._catalog_with_registry(text),
            )
        # Q7-EL1/OR2：用**最近执行事实**刷新车控焦点（跨轮取会话轮次的
        # `actions`，同轮取端侧刚执行掉的那批）。端侧本地快路径根本不写云侧焦点，
        # 「打开天窗」→「不用了，关掉」此前只能让 planner 猜对象。
        # `mem_on=false` 时 history 为空，但**同轮那半仍然成立**——它不来自记忆。
        focus = augment_focus_with_execution(
            focus, history, getattr(ctx, "edge_executed", None),
            previous_local_exchange=getattr(
                ctx, "previous_local_exchange", ""),
            previous_local_actions=getattr(
                ctx, "previous_local_actions", None),
            edge_executed_targets=getattr(ctx, "edge_executed_targets", None),
            previous_local_targets=getattr(ctx, "previous_local_targets", None))
        return WorkingSet(catalog=catalog, registry_agents=registry_agents,
                          history=history, memories=memories, focus=focus,
                          history_state=history_state, memory_state=memory_state,
                          history_exchanges=exchanges)

    async def _load_focus(self, session_id: str, user_id: str, occupant_id: str = ""):
        """载入会话焦点。失败/无则 None，不阻塞规划。

        `occupant_id`（评审二轮 R7）：私有那两格按说话人投影——别人的约束 / 任务帧读不到。
        """
        try:
            d = await self.session.load_focus(
                session_id, owner_user_id=user_id)
            if not d:
                return None
            valid = {f.name for f in fields(Focus)}
            d = _project_owned_fields(d, occupant_id)
            # W09：短时引用按 `focus_ts` 过期，活动状态照旧（各按自己的 ts 判活）
            focus = expire_short_term(Focus(**{k: v for k, v in d.items() if k in valid}))
            if focus is not None and not active_task_live(focus.active_task):
                focus.active_task = {}                      # W07：过期的任务帧不再是改口对象
            return focus
        except Exception as e:
            logger.debug("load_focus failed: %s", e)
            return None

    async def update_focus(self, session_id: str, plan, results, *,
                           user_id: str, exchange_id: str = "", occupant_id: str = ""):
        """每轮成功完成后更新焦点态（供下一轮指代消解）。绝不抛错、不阻塞主链路。

        `occupant_id`（评审二轮 R7）：私有那两格写进说话人自己的格子，别人的原样保留。
        """
        if not self.session:
            return
        try:
            focus = extract_focus(plan, results)
            if focus is not None:
                focus.origin_exchange_id = str(exchange_id or "").strip()
                # 门店列表是**粘性**的：只有新的 nearby.search 才该替换它。
                # focus 每轮都从当前 plan 重建，不接力的话，紧跟其后的任何一轮
                # （比如「第一个」直接落 luckin.menu）就会把上一轮取回的门店抹成空，
                # 下一句「这家的菜单」又变回「请先查询附近的瑞幸门店」。
                # 2026-08-13 真栈三轮实证；两轮测试测不出来——第二轮恰好紧邻搜索轮。
                # G8 active_route 同款粘性：只有新的 navigate 才替换活动路线。
                # ⚠ 2026-08-27 起**无条件载入**（C1-C）。原条件里的
                # `not focus.safety_alert` 是「为空才去取旧值」，而严重级比较恰恰
                # 只在**本轮有新告警**时才需要旧值——那一轮原条件不去取。
                # 其余几维行为逐字不变：它们的接力分支都带 `not focus.X` 前置，
                # 只要那个前置成立，原条件本来也会载入。代价是极少数轮多一次 Redis 读。
                previous = await self._load_focus(session_id, user_id,
                                                  occupant_id=occupant_id)
                stored_before = await self.session.load_focus(
                    session_id, owner_user_id=user_id) or {}
                # W18-a：墓碑接力（ts 不续期），本轮被顶掉 / 过期的组在下面追加
                retired = list(getattr(previous, "retired_candidate_sets", None) or []) \
                    if previous is not None else []
                # Q2 候选集台账：**旧组保留、新组追加**，按 ts 限龄、封顶 N 组。
                # 这里刻意**不是**「第四个字段也加一条粘性接力」——那是卡里点名
                # 不要做的第三次打补丁。三格粘性（last_places/active_route/
                # safety_alert）各自是被真栈烧出来的补丁；候选集换的是**载体**：
                # 一张有来源、有版本、有时效的台账，新旧共存而不是互相覆盖。
                if previous is not None:
                    prior_sets = list(getattr(previous, "candidate_sets", None) or [])
                    merged = _live_candidate_sets(prior_sets)
                    # 过期的那些从台账里掉出去了——它们的名字进墓碑（W18-a）
                    retired += [t for t in (_tombstone(s, time.time())
                                            for s in prior_sets if s not in merged) if t]
                    fresh = focus.candidate_sets
                    # 合并键带 `is_fallback`：**兜底那份与点名那份不是同一件事的两个
                    # 版本，是两种东西**。首版键只有 (intent, purpose)，于是
                    # 「川菜 → 兜底美食」两轮同键，兜底当场把点名那份挤掉——
                    # N5 换了个地方原样复发（测试当场抓到）。
                    # W08 起键带第四维 `query_signature`：A 附近 / B 附近是两批共存，
                    # 「换一批」是同键新版本（revision+1）。判据在 `candidate_merge_key`。
                    _key = candidate_merge_key
                    previous_by_key = {_key(s): s for s in merged}
                    for entry in fresh:
                        prior = previous_by_key.get(_key(entry))
                        if prior is not None:
                            entry["revision"] = int(prior.get("revision") or 1) + 1
                    fresh_keys = {_key(s) for s in fresh}
                    # 同键的旧组被本轮新组取代；其余原样留着，
                    # **ts 原样携带不续期**（时效从它产生那一刻起算）。
                    focus.candidate_sets = [
                        s for s in merged if _key(s) not in fresh_keys
                    ] + fresh
                kept = _live_candidate_sets(focus.candidate_sets)
                # 封顶时被顶掉的那几组也进墓碑（W18-a）：它们是被**新批**挤出去的，不是过期
                evicted = kept[:-_CANDIDATE_SETS_MAX] if len(kept) > _CANDIDATE_SETS_MAX else []
                retired += [t for t in (_tombstone(s, time.time()) for s in evicted) if t]
                focus.candidate_sets = kept[-_CANDIDATE_SETS_MAX:]
                focus.retired_candidate_sets = _live_tombstones(retired)[-_RETIRED_SETS_MAX:]
                _derive_choice_view(focus)
                if previous is not None and not focus.last_places \
                        and previous.last_places:
                    focus.last_places = list(previous.last_places)
                    # 接力**原样携带**取回时刻，不续期——时效从 nearby.search
                    # 那一刻起算，接力多少轮都不能让「刚才那家」变成「上周那家」。
                    focus.last_places_ts = float(
                        getattr(previous, "last_places_ts", 0.0) or 0.0)
                if previous is not None and not focus.active_route \
                        and not focus.route_ended \
                        and getattr(previous, "active_route", None):
                    # 原样携带（含 ts 不续期）：路线时效从 navigate 那一刻起算。
                    # ⚠ `route_ended` 时**不接力**：那一轮的空是显式终止，不是「本轮没
                    # 产生新路线」。两者在数据上都是空 dict，判据只能靠旗子（QA I-017）。
                    focus.active_route = dict(previous.active_route)
                # Q9 安全告警同款粘性：**只有新的告警才替换它**，普通轮不得把它抹掉。
                # 这一格正是 SF3 那三轮缺的东西——第二轮问「高速还能开吗」不产生
                # 任何告警，不接力的话安全态当场蒸发，第三轮自然就只剩音量可挑了。
                # 同样**原样携带 ts 不续期**：告警时效从它响起那一刻算。
                # ⚠ 2026-08-27 起这里**不再只在本轮为空时接力**（C1-C）：
                # 本轮新来一条 amber、上一轮那条 critical 还没解除时，
                # 「有新的就换掉」等于**用一句无关的话把安全约束降了级**。
                # 改成同一条严重级比较——本轮为空那种情况仍然逐字同旧（merge 取旧）。
                # ⚠ 2026-09-19 起 `safety_alert_cleared` 时**不接力**（QA T47 裁决 A）：那一轮的空
                # 是用户明确解除，不是「本轮没产生新告警」。同一句里的新告警仍在 `focus.safety_alert`
                # 里，不受影响。
                if (previous is not None and getattr(previous, "safety_alert", None)
                        and not focus.safety_alert_cleared):
                    focus.safety_alert = merge_safety_alert(
                        dict(previous.safety_alert), focus.safety_alert)
                # W07：活动任务帧粘性接力——ts 不续期。写任务只被写任务顶掉：本轮只是一次
                # 查询（read）而上一件写任务还活着时，帧留给写任务（改口的对象是它）。
                prior_task = getattr(previous, "active_task", None) if previous is not None else None
                if active_task_live(prior_task):
                    fresh_task = focus.active_task
                    same_task = bool(fresh_task) and fresh_task.get("task_id") == prior_task.get("task_id")
                    if not fresh_task or (
                            not same_task and fresh_task.get("kind") == "read"
                            and prior_task.get("kind") == "write"):
                        focus.active_task = dict(prior_task)
                # C12-B：会话偏好约束**后说的覆盖先说的、没说的沿用**。
                # 普通轮（这一句没提口味）必须原样保住——不接力就等于「说过的话
                # 只算一轮」，那和没有载体是一回事。
                if previous is not None and getattr(previous, "session_constraints", None):
                    focus.session_constraints = merge_constraints(
                        dict(previous.session_constraints), focus.session_constraints)
                # W03：撤销（None）只在合并里有意义，落盘的永远是归一后的「说过且仍有效」
                focus.session_constraints = merge_constraints(
                    {}, focus.session_constraints)
                if previous is not None and not focus.last_city \
                        and focus.last_intent not in WEATHER_CONTEXT_INTENTS \
                        and getattr(previous, "last_city", ""):
                    focus.last_city = str(previous.last_city)
                # `route_ended` 是**本轮事实**，不跨轮——存进去下一轮读回来仍是 True
                # 就会永久关掉接力（一个只该响一次的旗子变成了常态）。
                focus.route_ended = False
                focus.safety_alert_cleared = False
                focus.focus_ts = time.time()          # W09：短时引用的寿命从此刻起算
                record = _merge_owned_fields(
                    stored_before, asdict(focus), occupant_id)
                await self.session.save_focus(
                    session_id, record, owner_user_id=user_id)
        except Exception as e:
            logger.debug("update_focus failed: %s", e)

    async def append_turn(self, session_id: str, role: str, text: str,
                          user_id: str = "", vehicle_id: str = "",
                          occupant_id: str = "",
                          e2e_memory_capability: str = "",
                          turn_id: str = "", exchange_id: str = "",
                          actions=None, sources=None):
        """写入一轮对话到 memory（指代/抽取的数据来源）。memory 不可用或 clients 未提供
        该能力时静默跳过（不阻塞主链路）。user_id 透传给 memory 触发异步偏好抽取。

        occupant_id 决定**抽取出的偏好写给谁**（M4 P4）——这一步漏了，多用户隔离就只在
        读侧成立、写侧全部堆在 primary 名下，越用越错。"""
        fn = getattr(self.clients, "append_turn", None)
        if not fn:
            return
        try:
            args, kwargs = _adapt_append_turn_call(
                fn,
                session_id,
                role,
                text,
                user_id=user_id,
                vehicle_id=vehicle_id,
                occupant_id=occupant_id,
                e2e_memory_capability=e2e_memory_capability,
                turn_id=turn_id,
                exchange_id=exchange_id,
                actions=actions,
                sources=sources,
            )
            await fn(*args, **kwargs)
        except Exception as e:
            logger.debug("append_turn failed: %s", e)

    async def _catalog(self, text: str) -> list:
        """catalog 语义预筛（见 `_catalog_with_registry`；既有调用方只要预筛后的那份）。"""
        return (await self._catalog_with_registry(text))[0]

    async def _catalog_with_registry(self, text: str) -> tuple[list, list]:
        """→ `(预筛后的 catalog, 完整注册表)`。

        预筛：agent 数 ≤ top_k 时返回全量（no-op）；否则 resolve top-K ∪ always-include；
        resolve 不可用/为空 → 回退全量（de-risk）。完整注册表另给一份（W15）：route_hints
        从它扫，能力可见性与规则存亡分开。"""
        try:
            full = await self.clients.list_agents()
        except Exception as e:
            logger.warning("list_agents failed: %s", e)
            return [], []
        full = list(full)
        if len(full) <= self.top_k:
            return full, full
        fn = getattr(self.clients, "resolve", None)
        top = []
        if fn:
            try:
                top = list(await fn(query=text, top_k=self.top_k) or [])
            except Exception as e:
                logger.debug("catalog resolve failed, using full catalog: %s", e)
                top = []
        if not top:
            return full, full
        by_id = {a.manifest.agent_id: a for a in full}
        picked = {a.manifest.agent_id: a for a in top if a.manifest.agent_id in by_id}
        # 兜底 Agent + core Agent 必须在 catalog（R2.1 P5 / M5 P2；W15 起 hint 不再是资格）
        for a in full:
            if _always_include(a):
                picked.setdefault(a.manifest.agent_id, a)
        # 安全核心：edge/edge_fast 车控 agent（edge-vehicle/edge-media）始终保留——
        # 它们少、core、require_confirm 安全敏感，绝不能被相关性预筛丢掉，否则车控/
        # 危险动作二次确认会退化成 chitchat 兜底（dangerous_trunk_confirm 回归根因）。
        # 渲染层 render_catalog 同样保它不被预算裁剪丢掉（用同一 _is_edge_core 判据）。
        for a in full:
            if _is_edge_core(a):
                picked.setdefault(a.manifest.agent_id, a)
        logger.info("catalog pre-filtered: %d/%d agents (top_k=%d)",
                    len(picked), len(full), self.top_k)
        return list(picked.values()), full

    async def _history(self, ctx, *, exchanges: int = 0) -> tuple[list[dict], str]:
        """取最近对话历史（供指代消解）→ `(turns, 读态)`。失败返回空 + `unavailable`，不阻塞规划。
        `exchanges` 非零（W19 pin）时取回条数按它算（2N+2），否则用构造时的 `history_n`。

        M-B：按 OwnerKey 取，默认 OWNER_ONLY。车里只有一个会话而说话人会换——
        不按 owner 过滤时，上一位的称呼会比 system 提示更近，把当前这位的答案盖掉
        （P4 真机第四批实测：先聊过阿灵再问「我是谁」会答成阿灵）。

        批 5 W17：生产客户端有 `get_session_read`（服务端自报 degraded 也算读不到）；
        旧形态 / 测试替身只有 `get_session`，读态由结果与异常推断。
        """
        owner = dict(user_id=getattr(ctx, "user_id", "") or "",
                     occupant_id=getattr(ctx, "occupant_id", "") or "primary")
        last_n = (2 * max(1, int(exchanges)) + 2) if exchanges else self.history_n
        fn = getattr(self.clients, "get_session_read", None)
        if fn:
            try:
                turns, state = await fn(ctx.session_id, last_n, **owner)
                return list(turns or []), state
            except Exception as e:
                logger.debug("get_session_read failed: %s", e)
                return [], memory_read.UNAVAILABLE
        fn = getattr(self.clients, "get_session", None)
        if not fn:
            return [], memory_read.OFF
        try:
            turns = list(await _call_with_owner(fn, ctx.session_id, last_n, **owner) or [])
            return turns, memory_read.read_state(turns)
        except Exception as e:
            logger.debug("get_session failed: %s", e)
            return [], memory_read.UNAVAILABLE

    # G6（EVA 二轮）：历史指代词 → 放开情景记忆召回。episodic 此前被 kinds=["semantic"]
    # 写死永远进不了规划——「带我去上次看夜景那个地方」只能在闲聊里被复述。
    # 只在话里出现历史指代时才放开：episodic 噪声大，无差别注入会挤掉偏好（预算 400 字符）。
    _EPISODIC_REF_RE = re.compile(r"上次|上回|那次|上一次|之前去过?的?|前几天去")

    async def _recall(self, text: str, ctx) -> tuple[list[dict], str]:
        """召回与本轮相关的长期偏好（供 planner）→ `(items, 读态)`。只取现行高置信语义偏好，
        阈值过滤避免污染；失败返回空 + `unavailable`、无能力 / 无 user 返回空 + `off`，不阻塞规划。"""
        read_fn = getattr(self.clients, "recall_read", None)
        fn = read_fn or getattr(self.clients, "recall", None)
        if not fn or not getattr(ctx, "user_id", ""):
            return [], memory_read.OFF
        kinds = ["semantic"]
        if self._EPISODIC_REF_RE.search(text or ""):
            kinds = ["semantic", "episodic"]
        # M4 P4：按乘员召回。memory 侧 recall 本来就是 occupant 精确过滤，
        # 传进去隔离即自动成立（缺的从来不是记忆能力，是这个参数）。
        kwargs = dict(kinds=kinds, occupant_id=getattr(ctx, "occupant_id", "") or "primary",
                      top_k=3, min_confidence=0.5)
        try:
            if read_fn:
                mems, state = await read_fn(ctx.user_id, text, **kwargs)
                mems = list(mems or [])
            else:
                mems = list(await fn(ctx.user_id, text, **kwargs) or [])
                state = memory_read.read_state(mems)
        except Exception as e:
            logger.debug("recall failed: %s", e)
            return [], memory_read.UNAVAILABLE
        if mems:
            logger.info("memory recall for %s: %d items %s", ctx.user_id,
                        len(mems), [m.get("predicate") for m in mems])
        return mems, state


#: W19 请求级视窗 pin 的值域：1–6 对。上限 6 = 14 条取回（`2N+2`），再大也被 1400 字符预算裁掉。
_HISTORY_PIN_MAX = 6


def _pinned_history_exchanges(ctx) -> int:
    """`PlanContext.history_exchanges`（已在 `build_context` 里夹紧）→ 本轮视窗对数；0 = 缺省。"""
    try:
        value = int(getattr(ctx, "history_exchanges", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return value if 1 <= value <= _HISTORY_PIN_MAX else 0


def pinned_history_exchanges_from_meta(meta: dict | None) -> int:
    """`meta.planner_history_exchanges` → 1–6 的整数，其余（缺省 / 非法 / 越界）一律 0。
    模型输出与客户端值都当不可信输入：只认落在值域内的整数字面量。"""
    raw = str((meta or {}).get("planner_history_exchanges", "") or "").strip()
    if not raw.isdigit():
        return 0
    value = int(raw)
    return value if 1 <= value <= _HISTORY_PIN_MAX else 0


def build_context(request) -> PlanContext:
    """从 HandleRequest 解析出本次编排的 PlanContext（权限/会话偏好/位置/trace）。

    granted_permissions 来源：meta["granted_scopes"]（逗号分隔），PoC 由 Edge Gateway 注入；
    量产换成 token scope。精确位置只在本轮请求携带，需同时满足浏览器已授权 + location.read。
    无状态纯函数（不依赖 ContextManager 实例），故同时供 engine staticmethod 委托。"""
    meta = dict(getattr(request, "meta", {}) or {})

    # ws8 P0: 有 granted_scopes 用真实权限；无时按 PERMISSIONS_FAIL_OPEN 决定——
    # 默认 true = PoC 全开 fallback（保持现状）；量产翻 false = fail-closed（granted 留空，
    # 仅无权限 Agent 如 chitchat 可达，与 planning._filter_by_permission 语义一致）。
    # 判据本体在 security.session_scopes：端侧 T0 同源消费（AR05 F07）。
    vehicle_id = (getattr(request.context, "vehicle_id", "")
                  if hasattr(request, "context") and request.context else "")
    granted, scope_source = resolve_granted_scopes(
        meta, vehicle_id=vehicle_id, trace_id=meta.get("trace_id", ""), audit=_audit)
    if scope_source == SOURCE_POC_DEFAULT:
        logger.warning(
            "No granted_scopes in request; PERMISSIONS_FAIL_OPEN=on → using PoC defaults. "
            "Production MUST inject from session token/device identity.")
    elif scope_source != SOURCE_TOKEN:
        logger.warning(
            "No granted_scopes in request; PERMISSIONS_FAIL_OPEN=off → fail-closed "
            "(only no-permission agents reachable).")

    # HMI 会话级偏好（透传给 Agent，见 hmi/src/settings.tsx buildMeta）
    # M4 P4：本轮说话人（声纹识别结果，HMI 在唤醒窗内锁定后随每轮 meta 上来）。
    # 缺省/空 → "primary" = 逐字回落到 P4 之前。**刻意不参与 granted 的任何分支**（§6.1）。
    occupant = (meta.get("occupant_id") or "").strip() or "primary"

    prefs = {k: meta[k] for k in
             ("model_pref", "answer_length", "assistant_name", "memory_enabled",
              "poi_page",          # "换一批"翻页页码，透传给 navigation
              "vehicle_battery",   # 端侧真实电量，透传给 charging
              "input_source",      # 本轮来源：voice_* 免唤醒/S2S；ptt 为 Android 手动录音
              "voice_utterance_ms",  # R4.4：本轮 speech 累计时长（数字字符串）
              "clarify_resume",    # R4.4：澄清续接标记（"1"）——engine 据此深度=1 抑制再澄清
              "occupant_name",     # M4 P4：说话人称呼（声纹识别出的显示名）——「你知道我是谁」
                                   # 靠它确定性答出；与 occupant_id 同样**不参与权限判定**
              "vision_frame_id",   # M4 P4：车外单帧的**引用**（图像本体只在网关内存里）。
                                   # 按 _SENSITIVE_SCOPE 最小化下发——只有声明了 vision
                                   # context_scope 的 Agent 收得到，其余 Agent 连引用都看不见。
              "llm_provider", "llm_model")  # 运行时硬化 D2：请求级 LLM pin（评测/重放 A/B），
                                            # 随 prefs 下发全部 Agent + engine 设 planner 侧 pin
             if meta.get(k)}
    if "location.read" in granted:
        prefs.update({k: meta[k] for k in
                      ("current_lat", "current_lng", "current_accuracy_m",
                       "current_location_at", "current_location_source")
                      if meta.get(k)})

    # 声纹结果随 prefs 下发给全部 Agent（同 thinking/llm pin 的既有惯例：改一处全 Agent 覆盖），
    # SDK 侧据此构造 Context.occupant_id，Agent 的 recall/remember 自动按乘员隔离。
    prefs["occupant_id"] = occupant

    return PlanContext(
        request_id=getattr(request, "request_id", ""),
        session_id=getattr(request, "session_id", ""),
        user_id=getattr(request.context, "user_id", "") if hasattr(request, "context") and request.context else "",
        vehicle_id=getattr(request.context, "vehicle_id", "") if hasattr(request, "context") and request.context else "",
        occupant_id=occupant,
        e2e_memory_capability=getattr(request, "e2e_memory_capability", ""),
        is_confirmation=getattr(request, "is_confirmation", False),
        operation_id=str(getattr(request, "operation_id", "") or "").strip(),
        granted_permissions=granted,
        trace_id=meta.get("trace_id", ""),
        prefs=prefs,
        edge_nlu=meta.get("_edge_nlu", ""),   # M5 P2-D2：端侧初判，观测用（不进 prompt）
        # Q7-OR2：本轮端侧已执行的动作名（混合路径的同轮上下文）。逗号分隔，空=没有。
        edge_executed=[a.strip() for a in
                       str(meta.get("_edge_executed", "") or "").split(",") if a.strip()],
        previous_local_exchange=str(
            meta.get("_edge_previous_local_exchange", "") or "").strip(),
        previous_local_actions=[a.strip() for a in str(
            meta.get("_edge_previous_local_actions", "") or ""
        ).split(",") if a.strip()],
        # 评审四轮 R4-04：同两份名字配套的执行目标（位置），端侧签发
        edge_executed_targets=parse_control_targets(meta.get("_edge_executed_targets", "")),
        previous_local_targets=parse_control_targets(meta.get("_edge_previous_local_targets", "")),
        history_exchanges=pinned_history_exchanges_from_meta(meta),
    )
