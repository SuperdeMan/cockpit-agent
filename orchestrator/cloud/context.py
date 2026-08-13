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
from dataclasses import dataclass, field, fields, asdict

from .models import PlanContext
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
    last_choice_purpose: str = ""                       # list | waypoint（最新候选卡的语义）
    last_choices: list[str] = field(default_factory=list)  # 最新候选名，按卡片顺序（最多 5 个）
    destination_lat: float | None = None                # 已解析目的地坐标（供“那边”确定性续接）
    destination_lng: float | None = None
    # 上一轮 nearby.search 取回的公开 POI（只留 name/lng/lat 三标量，最多 10 条）。
    # 真实商户下单/看菜单要求门店三元组来自「同一次 nearby.search 的同一条 item」，
    # 而该 provenance 每轮被执行器清掉——于是「先查附近的瑞幸」「在最近那家点一杯」
    # 两轮走不通。这一格把**服务端记得取回过哪些门店**这件事跨轮留住；
    # **刻意不进 prompt**（`_render_focus` 不渲染它）：它是给执行器补槽用的结构化事实，
    # 让模型看见只会诱导它自己编坐标。
    last_places: list[dict] = field(default_factory=list)

    def is_empty(self) -> bool:
        # last_intent 也算有效焦点：纯信息轮（查赛程/天气）此前不落焦点，「明天呢」这类
        # 省略式追问就只能靠裸历史猜域（badcase demo-i9c92i 追问被错绑到天气）。
        return not (self.obj or self.positions or self.attr
                    or self.last_poi or self.last_destination or self.last_intent
                    or self.last_choice_purpose or self.last_choices
                    or self.last_places
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
            choices = [
                str(item.get("name") or item.get("title") or item.get("poi_name") or "")
                for item in choice_items[:5]
                if isinstance(item, dict)
            ]
            focus.last_choices = [name for name in choices if name]
            if focus.last_choices:
                focus.last_choice_purpose = (
                    "waypoint" if isinstance(data.get("stops"), list) else "list"
                )
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
                places.append({"name": name, "lng": lng, "lat": lat})
        if places:
            focus.last_places = places
    return None if focus.is_empty() else focus


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
                if not focus.last_places:
                    previous = await self._load_focus(session_id, user_id)
                    if previous is not None and previous.last_places:
                        focus.last_places = list(previous.last_places)
                await self.session.save_focus(
                    session_id, asdict(focus), owner_user_id=user_id)
        except Exception as e:
            logger.debug("update_focus failed: %s", e)

    async def append_turn(self, session_id: str, role: str, text: str,
                          user_id: str = "", vehicle_id: str = "",
                          occupant_id: str = "",
                          e2e_memory_capability: str = "",
                          turn_id: str = "", exchange_id: str = ""):
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

    async def _recall(self, text: str, ctx) -> list[dict]:
        """召回与本轮相关的长期偏好（供 planner）。只取现行高置信语义偏好，
        阈值过滤避免污染；失败/无能力返回空，不阻塞规划。"""
        fn = getattr(self.clients, "recall", None)
        if not fn or not getattr(ctx, "user_id", ""):
            return []
        try:
            # M4 P4：按乘员召回。memory 侧 recall 本来就是 occupant 精确过滤，
            # 传进去隔离即自动成立（缺的从来不是记忆能力，是这个参数）。
            mems = await fn(ctx.user_id, text, kinds=["semantic"],
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
        granted_permissions=granted,
        trace_id=meta.get("trace_id", ""),
        prefs=prefs,
        edge_nlu=meta.get("_edge_nlu", ""),   # M5 P2-D2：端侧初判，观测用（不进 prompt）
    )
