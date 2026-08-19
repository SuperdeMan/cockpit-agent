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

import inspect
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, fields, asdict

from .models import PlanContext
from runtime.clock import hhmm as clock_hhmm
from security.audit import AuditLogger

logger = logging.getLogger("planner.context")
_audit = AuditLogger()

# PoC 默认权限：未注入 granted_scopes 时使用（fail-open for PoC）。
# 量产必须从会话 token/设备身份解析 scope，不得使用此默认值。
_POC_DEFAULT_SCOPES = [
    "vehicle.control", "media.control", "navigation",
    "food.ordering",
    "location.read", "navigation.control",
    "network.external", "payment.invoke",
    "profile.read", "profile.write",
    # 真实商户 MCP（麦当劳/瑞幸，§9.9/§9.17）：桥对 workflow 与查单工具校验这两个
    # scope。PoC 里没有任何"商户授权"发放入口，漏在这里 = 能力永远不可达——
    # 2026-08-12 实测每一句真实下单都回「当前账号缺少商户授权」，而 e2e 自己往 meta
    # 塞 granted_scopes 所以一直是绿的。写操作的安全边界不在这里：require_confirm
    # 中央闸 + 只创建未支付订单 + 支付独立走 payment-gateway，三道都不受本行影响。
    "merchant.read", "merchant.write",
    # M4 P4 视觉：**单帧**（用户显式问「那是什么」时抓一张），不是 camera.read 连续流——
    # 后者在 conventions §3 维持 ❌ 禁。采集门控在端侧（默认不采），这里只是让能力可路由。
    "camera.frame",
]
# 敏感上下文键：默认按值广播，Phase 4 起按 manifest context_scopes 最小化下发。
_SENSITIVE_CONTEXT_KEYS = (
    "current_lat", "current_lng", "current_accuracy_m",
    "current_location_at", "current_location_source", "vehicle_battery",
)


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
    ②声明了 `route_hints` 的 Agent——**机制依赖**：RouteHintEngine 从 `agent_map` 里读
      hints，manifest 被预筛丢掉它就看不到（这一条不是巧合耦合，是硬约束）；
    ③`category: core` 的 Agent——**领域重要性**（M5 P2 补）。

    为什么补③：此前只有①②，于是保护资格与领域重要性无关——`navigation` 与 `road-safety`
    都是 `core` 却因为**恰好没写 route_hints** 而全程可被裁掉（数据飞轮 D1 的根因，
    navigation 恰是 Shadow NLU 里缺口最大的域）。而 P2 的 hint 退役流水线会让这件事更糟：
    摘掉一条 hint 会**顺手删掉那个 Agent 的 catalog 保护**——治理规则的动作不该有这种
    远处的副作用。`category` 是 manifest/proto/registry 已有的字段，用它即机制化，不新增管道。

    ⚠ 注意②与③是**并集不是替换**（RFC §5-P2-4 原设想是替换，实测会打断 hint 可见性）。"""
    m = getattr(a, "manifest", None)
    return (getattr(m, "agent_id", "") == _FALLBACK_AGENT
            or bool(getattr(m, "route_hints", []))
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
}
_POSITION_WORDS = ("主驾驶", "副驾驶", "主驾", "副驾", "后排", "左后", "右后", "前排")


@dataclass
class Focus:
    """跨轮对话焦点（指代消解用）。只记能可靠抽取的字段；空字段不注入 prompt。"""
    last_agent_id: str = ""
    last_intent: str = ""
    obj: str = ""                                       # 语义对象，如 "空调"/"氛围灯"
    positions: list[str] = field(default_factory=list)  # ["副驾"]
    attr: str = ""                                      # "温度"/"颜色"...
    last_poi: str = ""                                  # 上个 POI（"还是刚才那家"）
    last_destination: str = ""                          # 上个导航目的地
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

    def is_empty(self) -> bool:
        # last_intent 也算有效焦点：纯信息轮（查赛程/天气）此前不落焦点，「明天呢」这类
        # 省略式追问就只能靠裸历史猜域（badcase demo-i9c92i 追问被错绑到天气）。
        return not (self.obj or self.positions or self.attr
                    or self.last_poi or self.last_destination or self.last_intent
                    or self.last_choice_purpose or self.last_choices
                    or self.candidate_sets
                    or self.last_places or self.active_route or self.safety_alert
                    or self.destination_lat is not None
                    or self.destination_lng is not None)


@dataclass
class WorkingSet:
    """一次规划轮装配好的工作上下文。catalog 是已（语义）预筛的 agent 列表。"""
    catalog: list = field(default_factory=list)        # ResolvedAgent 列表（含 .manifest/.endpoint）
    history: list[dict] = field(default_factory=list)  # [{role, text, ts}]
    memories: list[dict] = field(default_factory=list) # [{text, scope, predicate, provenance, confidence}]
    focus: "Focus | None" = None                       # 结构化焦点态（指代消解）
    # 落域可观测（数据飞轮 P0）：render_catalog 回填 {chars_full, chars_final, dropped}
    catalog_stats: dict = field(default_factory=dict)

    def render_context(self) -> str:
        """焦点 + 记忆 + 历史块，统一字符预算、按优先级裁剪。

        优先级：焦点 > 记忆 > 历史（焦点/画像比旧对话轮更值得留）。无焦点且预算内时输出与旧
        `_format_memory + _format_history` 逐字一致（不扰动既有 LLM 行为）。"""
        focus_block = _render_focus(self.focus)
        mem_block = _render_memory(self.memories)
        budget_left = max(0, _CTX_BUDGET - len(focus_block) - len(mem_block))
        hist_block = _render_history(self.history, budget=budget_left)
        return focus_block + mem_block + hist_block

    @staticmethod
    def render_catalog(agents: list, stats: dict | None = None) -> str:
        """能力清单 JSON；超 catalog 预算时优先丢相关性最低的**非受保护** agent（从尾部找）。

        受保护 = edge 车控核心（edge-vehicle/edge-media）∪ 兜底 Agent（env）∪ 有 route_hints 的 Agent（见 _always_include）。
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
    parts = []
    if weighted:
        weighted.sort(key=lambda x: x[0], reverse=True)
        parts.append("已知用户偏好（按强度排序，仅在与当前任务相关时参考）：\n"
                     + "\n".join(f"- {txt}（{_strength_label(w)}）"
                                 for w, txt in weighted))
    if plain:
        head = "相关记忆：" if weighted else "已知用户记忆（仅在与当前任务相关时参考，勿向用户暴露置信度）："
        parts.append(head + "\n" + "\n".join(plain[:3]))
    block = "\n".join(parts)
    return block[:_MEMORY_BUDGET] + "\n\n"


def _render_history(history: list[dict] | None, budget: int = _CTX_BUDGET) -> str:
    """最近对话 → prompt 片段（最多 4 轮，供指代消解）。逐字沿用旧 _format_history；
    超预算时从最旧一轮起逐条丢弃（focus/记忆优先于陈旧对话轮）。"""
    if not history:
        return ""
    turns = list(history[-4:])
    while turns:
        lines = []
        for t in turns:
            txt = (t.get("text") or "").strip()
            if txt:
                who = "用户" if t.get("role") == "user" else "助手"
                lines.append(f"{who}：{txt}")
        if not lines:
            return ""
        block = "最近对话（用于指代消解）：\n" + "\n".join(lines) + "\n\n"
        if len(block) <= budget or len(turns) == 1:
            return block
        turns.pop(0)  # 丢最旧一轮再试
    return ""


def _render_focus(focus) -> str:
    """结构化焦点 → 紧凑 prompt 块（仅非空字段）。供 LLM 在用户话术含指代时复用。"""
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
    if focus.last_intent:
        parts.append(f"上一轮意图={focus.last_intent}")  # 省略式追问（「明天呢」）延续判据
    if focus.obj:
        parts.append(f"对象={focus.obj}")
    if focus.positions:
        parts.append("位置=" + "/".join(focus.positions))
    if focus.attr:
        parts.append(f"属性={focus.attr}")
    if focus.last_poi:
        parts.append(f"上个地点={focus.last_poi}")
    if focus.last_destination:
        parts.append(f"上个目的地={focus.last_destination}")
    if focus.last_choices:
        purpose = ("顺路途经点选择"
                   if focus.last_choice_purpose == "waypoint" else "列表选择")
        parts.append(f"最新候选用途={purpose}")
        parts.append("最新候选=" + "/".join(
            f"{idx}:{name}" for idx, name in enumerate(focus.last_choices, 1)))
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
    """从槽位值里扫出座位/区域词（主驾/副驾/后排…）。"""
    found = []
    for v in (slots or {}).values():
        s = str(v)
        for w in _POSITION_WORDS:
            if w in s and w not in found:
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
#: 交集：焦点态本身 `_FOCUS_TTL`（当前 300s）**每成功一轮就续期**，而告警的 `ts`
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
    sets = _live_candidate_sets(getattr(focus, "candidate_sets", None) or [])
    if not sets:
        return None
    for entry in reversed(sets):
        if not entry.get("is_fallback"):
            return entry
    return sets[-1] if allow_fallback else None


#: 候选集**跨层下发**给 Agent 时的投影（Q10 接手第 7 步的下发面）。
#: 与 `_CANDIDATE_ITEM_KEYS` 是**两张表，故意的**——那张回答「哪些事实值得跨轮留住」
#: （消费方在云侧，四个聚合维度都要数值），这张回答「哪些字段可以**离开编排**、
#: 进到一个 `trust_level: third_party` 的 Agent 里」。两个问题的答案不一样，
#: 合成一张表就会让「留住」自动等于「下发」。
#:
#: 留 `index` 与 `name`，**只留这两个**：
#: · `lat`/`lng`/`city`/`address` —— 精确位置是红线级敏感上下文（CLAUDE.md §5），
#:   而桥的 manifest 连 `location` scope 都没有；候选集不能成为绕过它的第二条路。
#: · `open_today`/`rating`/`cost`/`price`/`distance_km` —— 它们的消费方
#:   （`candidate_query` 那四个聚合维度）**在云侧**，桥拿到也没人算。
#: · `id` —— 收敛目标是**按钮送出的那个规范名**（`send_text` 里就是 `item["name"]`），
#:   不是另造一条 id 通道；再给一条 id 会让两个入口重新变得不一样，只是反了个方向。
#: 三条都是 B4 那句「加字段要有真实消费方，无消费方的声明只会漂移」的逐条应用。
_DOWNLINK_ITEMS_MAX = 10
_DOWNLINK_NAME_MAX = 40


def candidate_downlink(entry: dict | None) -> dict | None:
    """候选集 → 下发给 Agent 的最小投影；没有可下发内容时返回 None。

    形状 `{"source_intent": str, "items": [{"index": 1, "name": str}, ...]}`。
    `index` 是**从 1 开始的卡片序号**——「第一杯」说的就是它。下发方给序号而不是
    让消费方去数数组下标，是因为这里会裁剪（`_DOWNLINK_ITEMS_MAX`），
    下标会随裁剪漂移而序号不会。

    `source_intent` 是消费方**唯一的归属判据**：桥只认自己那家商户产出的候选，
    否则「附近的瑞幸」之后一句「点第一个」会把一个 POI 名塞进 `item_query`。
    """
    items = [it for it in ((entry or {}).get("items") or []) if isinstance(it, dict)]
    out = []
    for index, item in enumerate(items[:_DOWNLINK_ITEMS_MAX], start=1):
        name = str(item.get("name") or "").strip()[:_DOWNLINK_NAME_MAX]
        if name:
            out.append({"index": index, "name": name})
    if not out:
        return None
    return {"source_intent": str((entry or {}).get("source_intent") or ""),
            "items": out}


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

    ⚠ **覆盖边界**：`_CONTROL_FOCUS` 只有 8 个域而 VAL 车控对象有 67 个。
    表外对象（如 `rear_view_mirror`）解不出 ⇒ 返回 None ⇒ 退化成本函数存在之前的行为
    （fail-open）。**不引入新的不一致**——`extract_focus` 本来就是这个覆盖面。

    形状不可信（history 来自 gRPC）：非 dict / actions 非 list / 元素非 str 一律跳过，
    同 CLAUDE.md §6「防御要一路防到真正会被拿去用的那个值」。
    """
    groups: list[list[str]] = []
    if isinstance(history, (list, tuple)):
        for turn in history:
            if not isinstance(turn, dict):
                continue
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


def augment_focus_with_execution(focus, history, edge_executed=None) -> "Focus | None":
    """用最近执行事实刷新车控焦点；解不出时原焦点不动。

    会话历史按轮次有序，`actions` 是成功执行事实；同轮 `edge_executed` 又比历史更新。
    因此一旦解出，它就应覆盖 Redis 里可能由更早云侧轮次留下的控制对象。只填空会把
    「取不到对象」变成「稳定使用陈旧对象」（云侧调氛围灯→本地开天窗→关掉）。

    动作账本没有位置与 agent，覆盖时清掉这两格，避免把旧对象的「副驾」等限定粘到
    新对象上。地点、候选集、活动路线等其他正交焦点原样保留。
    """
    found = recent_control_execution(history, edge_executed)
    if found is None:
        return focus
    obj, attr, intent = found
    out = focus if focus is not None else Focus()
    out.obj, out.attr = obj, attr
    # `last_intent` 与 obj 必须来自同一事实：省略守卫按它的 namespace 校验计划。
    out.last_intent = intent
    out.positions = []
    out.last_agent_id = ""
    return out


def extract_focus(plan, results) -> "Focus | None":
    """从本轮执行的 plan + 成功结果抽取焦点（best-effort，启发式）。

    控制类取最近一个成功控制步的对象/属性/位置；导航/搜索类取目的地与第一个 POI。
    全空返回 None（不持久、不注入）。绝不抛错——抽取失败由调用方吞掉。"""
    ok = {r.step_id for r in results if getattr(r, "status", None)
          and getattr(r.status, "value", "") == "ok"}
    by_id = {r.step_id: r for r in results}
    focus = Focus()
    for step in getattr(plan, "steps", []):
        if step.id not in ok:
            continue
        domain = (step.intent or "").split(".")[0]
        if domain in _CONTROL_FOCUS:
            focus.obj, focus.attr = _CONTROL_FOCUS[domain]
            pos = _scan_positions(step.slots)
            if pos:
                focus.positions = pos
            focus.last_agent_id, focus.last_intent = step.agent_id, step.intent
        dest = (step.slots or {}).get("destination")
        if dest:
            focus.last_destination = str(dest)
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
                    "items": items,
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
        # Q9 安全告警：同族保留键。多步都声明时取最后一个（后发的更新）。
        alert = _valid_safety_alert(
            data.get("_safety_alert") if isinstance(data, dict) else None)
        if alert:
            focus.safety_alert = alert
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
                 history_n: int = 6):
        self.clients = clients
        self.session = session   # SessionStore，供焦点态 load/save（None 则不启用焦点）
        # 默认给足 headroom：高于当前 agent 规模，预筛只在真正大规模(20+)时触发，
        # 此前是 no-op（避免在小规模误丢需要的 agent，见 dangerous_trunk_confirm 回归）。
        self.top_k = top_k if top_k is not None else int(
            os.getenv("PLANNER_CATALOG_TOP_K", "20"))
        self.history_n = history_n

    async def assemble(self, text: str, ctx, *, mem_on: bool = True,
                       granted_permissions: list[str] | None = None) -> WorkingSet:
        """装配一次规划轮的工作上下文。失败的子项各自降级为空/全量，绝不阻塞规划。"""
        history = await self._history(ctx) if mem_on else []
        memories = await self._recall(text, ctx) if mem_on else []
        focus = await self._load_focus(
            ctx.session_id, ctx.user_id) if (mem_on and self.session) else None
        # Q7-EL1/OR2：用**最近执行事实**刷新车控焦点（跨轮取会话轮次的
        # `actions`，同轮取端侧刚执行掉的那批）。端侧本地快路径根本不写云侧焦点，
        # 「打开天窗」→「不用了，关掉」此前只能让 planner 猜对象。
        # `mem_on=false` 时 history 为空，但**同轮那半仍然成立**——它不来自记忆。
        focus = augment_focus_with_execution(
            focus, history, getattr(ctx, "edge_executed", None))
        catalog = await self._catalog(text)
        return WorkingSet(catalog=catalog, history=history, memories=memories,
                          focus=focus)

    async def _load_focus(self, session_id: str, user_id: str):
        """载入会话焦点。失败/无则 None，不阻塞规划。"""
        try:
            d = await self.session.load_focus(
                session_id, owner_user_id=user_id)
            if not d:
                return None
            valid = {f.name for f in fields(Focus)}
            return Focus(**{k: v for k, v in d.items() if k in valid})
        except Exception as e:
            logger.debug("load_focus failed: %s", e)
            return None

    async def update_focus(self, session_id: str, plan, results, *,
                           user_id: str):
        """每轮成功完成后更新焦点态（供下一轮指代消解）。绝不抛错、不阻塞主链路。"""
        if not self.session:
            return
        try:
            focus = extract_focus(plan, results)
            if focus is not None:
                # 门店列表是**粘性**的：只有新的 nearby.search 才该替换它。
                # focus 每轮都从当前 plan 重建，不接力的话，紧跟其后的任何一轮
                # （比如「第一个」直接落 luckin.menu）就会把上一轮取回的门店抹成空，
                # 下一句「这家的菜单」又变回「请先查询附近的瑞幸门店」。
                # 2026-08-13 真栈三轮实证；两轮测试测不出来——第二轮恰好紧邻搜索轮。
                # G8 active_route 同款粘性：只有新的 navigate 才替换活动路线。
                previous = None
                if (not focus.last_places or not focus.active_route
                        or not focus.safety_alert
                        or len(focus.candidate_sets) < _CANDIDATE_SETS_MAX):
                    previous = await self._load_focus(session_id, user_id)
                # Q2 候选集台账：**旧组保留、新组追加**，按 ts 限龄、封顶 N 组。
                # 这里刻意**不是**「第四个字段也加一条粘性接力」——那是卡里点名
                # 不要做的第三次打补丁。三格粘性（last_places/active_route/
                # safety_alert）各自是被真栈烧出来的补丁；候选集换的是**载体**：
                # 一张有来源、有版本、有时效的台账，新旧共存而不是互相覆盖。
                if previous is not None:
                    merged = _live_candidate_sets(
                        list(getattr(previous, "candidate_sets", None) or []))
                    fresh = focus.candidate_sets
                    # 合并键带 `is_fallback`：**兜底那份与点名那份不是同一件事的两个
                    # 版本，是两种东西**。首版键只有 (intent, purpose)，于是
                    # 「川菜 → 兜底美食」两轮同键，兜底当场把点名那份挤掉——
                    # N5 换了个地方原样复发（测试当场抓到）。
                    def _key(s):
                        return (s.get("source_intent"), s.get("purpose"),
                                bool(s.get("is_fallback")))
                    fresh_keys = {_key(s) for s in fresh}
                    # 同键的旧组被本轮新组取代；其余原样留着，
                    # **ts 原样携带不续期**（时效从它产生那一刻起算）。
                    focus.candidate_sets = [
                        s for s in merged if _key(s) not in fresh_keys
                    ] + fresh
                focus.candidate_sets = _live_candidate_sets(
                    focus.candidate_sets)[-_CANDIDATE_SETS_MAX:]
                _derive_choice_view(focus)
                if previous is not None and not focus.last_places \
                        and previous.last_places:
                    focus.last_places = list(previous.last_places)
                    # 接力**原样携带**取回时刻，不续期——时效从 nearby.search
                    # 那一刻起算，接力多少轮都不能让「刚才那家」变成「上周那家」。
                    focus.last_places_ts = float(
                        getattr(previous, "last_places_ts", 0.0) or 0.0)
                if previous is not None and not focus.active_route \
                        and getattr(previous, "active_route", None):
                    # 原样携带（含 ts 不续期）：路线时效从 navigate 那一刻起算。
                    focus.active_route = dict(previous.active_route)
                # Q9 安全告警同款粘性：**只有新的告警才替换它**，普通轮不得把它抹掉。
                # 这一格正是 SF3 那三轮缺的东西——第二轮问「高速还能开吗」不产生
                # 任何告警，不接力的话安全态当场蒸发，第三轮自然就只剩音量可挑了。
                # 同样**原样携带 ts 不续期**：告警时效从它响起那一刻算。
                if previous is not None and not focus.safety_alert \
                        and getattr(previous, "safety_alert", None):
                    focus.safety_alert = dict(previous.safety_alert)
                await self.session.save_focus(
                    session_id, asdict(focus), owner_user_id=user_id)
        except Exception as e:
            logger.debug("update_focus failed: %s", e)

    async def append_turn(self, session_id: str, role: str, text: str,
                          user_id: str = "", vehicle_id: str = "",
                          occupant_id: str = "",
                          e2e_memory_capability: str = "",
                          turn_id: str = "", exchange_id: str = "",
                          actions=None):
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
            )
            await fn(*args, **kwargs)
        except Exception as e:
            logger.debug("append_turn failed: %s", e)

    async def _catalog(self, text: str) -> list:
        """catalog 语义预筛：agent 数 ≤ top_k 时返回全量（no-op）；否则 resolve top-K
        ∪ always-include；resolve 不可用/为空 → 回退全量（de-risk）。"""
        try:
            full = await self.clients.list_agents()
        except Exception as e:
            logger.warning("list_agents failed: %s", e)
            return []
        full = list(full)
        if len(full) <= self.top_k:
            return full
        fn = getattr(self.clients, "resolve", None)
        top = []
        if fn:
            try:
                top = list(await fn(query=text, top_k=self.top_k) or [])
            except Exception as e:
                logger.debug("catalog resolve failed, using full catalog: %s", e)
                top = []
        if not top:
            return full
        by_id = {a.manifest.agent_id: a for a in full}
        picked = {a.manifest.agent_id: a for a in top if a.manifest.agent_id in by_id}
        # 兜底 Agent + 有 route_hints 的 Agent 必须在 catalog（确定性路由依赖），R2.1 P5
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
        return list(picked.values())

    async def _history(self, ctx) -> list[dict]:
        """取最近对话历史（供指代消解）。失败返回空，不阻塞规划。

        M-B：按 OwnerKey 取，默认 OWNER_ONLY。车里只有一个会话而说话人会换——
        不按 owner 过滤时，上一位的称呼会比 system 提示更近，把当前这位的答案盖掉
        （P4 真机第四批实测：先聊过阿灵再问「我是谁」会答成阿灵）。
        """
        fn = getattr(self.clients, "get_session", None)
        if not fn:
            return []
        try:
            return await _call_with_owner(
                fn, ctx.session_id, self.history_n,
                user_id=getattr(ctx, "user_id", "") or "",
                occupant_id=getattr(ctx, "occupant_id", "") or "primary")
        except Exception as e:
            logger.debug("get_session failed: %s", e)
            return []

    # G6（EVA 二轮）：历史指代词 → 放开情景记忆召回。episodic 此前被 kinds=["semantic"]
    # 写死永远进不了规划——「带我去上次看夜景那个地方」只能在闲聊里被复述。
    # 只在话里出现历史指代时才放开：episodic 噪声大，无差别注入会挤掉偏好（预算 400 字符）。
    _EPISODIC_REF_RE = re.compile(r"上次|上回|那次|上一次|之前去过?的?|前几天去")

    async def _recall(self, text: str, ctx) -> list[dict]:
        """召回与本轮相关的长期偏好（供 planner）。只取现行高置信语义偏好，
        阈值过滤避免污染；失败/无能力返回空，不阻塞规划。"""
        fn = getattr(self.clients, "recall", None)
        if not fn or not getattr(ctx, "user_id", ""):
            return []
        try:
            kinds = ["semantic"]
            if self._EPISODIC_REF_RE.search(text or ""):
                kinds = ["semantic", "episodic"]
            # M4 P4：按乘员召回。memory 侧 recall 本来就是 occupant 精确过滤，
            # 传进去隔离即自动成立（缺的从来不是记忆能力，是这个参数）。
            mems = await fn(ctx.user_id, text, kinds=kinds,
                            occupant_id=getattr(ctx, "occupant_id", "") or "primary",
                            top_k=3, min_confidence=0.5)
            if mems:
                logger.info("memory recall for %s: %d items %s", ctx.user_id,
                            len(mems), [m.get("predicate") for m in mems])
            return mems
        except Exception as e:
            logger.debug("recall failed: %s", e)
            return []


def build_context(request) -> PlanContext:
    """从 HandleRequest 解析出本次编排的 PlanContext（权限/会话偏好/位置/trace）。

    granted_permissions 来源：meta["granted_scopes"]（逗号分隔），PoC 由 Edge Gateway 注入；
    量产换成 token scope。精确位置只在本轮请求携带，需同时满足浏览器已授权 + location.read。
    无状态纯函数（不依赖 ContextManager 实例），故同时供 engine staticmethod 委托。"""
    meta = dict(getattr(request, "meta", {}) or {})
    raw_scopes = meta.get("granted_scopes", "")
    granted = [s.strip() for s in raw_scopes.split(",") if s.strip()] if raw_scopes else []

    # ws8 P0: 有 granted_scopes 用真实权限；无时按 PERMISSIONS_FAIL_OPEN 决定——
    # 默认 true = PoC 全开 fallback（保持现状）；量产翻 false = fail-closed（granted 留空，
    # 仅无权限 Agent 如 chitchat 可达，与 planning._filter_by_permission 语义一致）。
    if not granted:
        vehicle_id = (getattr(request.context, "vehicle_id", "")
                      if hasattr(request, "context") and request.context else "")
        if os.getenv("PERMISSIONS_FAIL_OPEN", "true").lower() != "false":
            granted = list(_POC_DEFAULT_SCOPES)
            _audit.fail_open_scopes(vehicle_id=vehicle_id,
                                    trace_id=meta.get("trace_id", ""), scopes=granted)
            logger.warning(
                "No granted_scopes in request; PERMISSIONS_FAIL_OPEN=on → using PoC defaults. "
                "Production MUST inject from session token/device identity.")
        else:
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
              "input_source",      # R4.4：hands-free 语音来源（voice_wake|voice_followup|voice_bargein）
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
    )
