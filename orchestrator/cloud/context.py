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
]
# 敏感上下文键：默认按值广播，Phase 4 起按 manifest context_scopes 最小化下发。
_SENSITIVE_CONTEXT_KEYS = (
    "current_lat", "current_lng", "current_accuracy_m",
    "current_location_at", "current_location_source", "vehicle_battery",
)

# 装配预算（字符近似，避免引入 tokenizer 依赖；沿用既有 block[:400] 的 char-proxy 思路）。
_CTX_BUDGET = int(os.getenv("PLANNER_CTX_BUDGET_CHARS", "1400"))   # 记忆+历史(+焦点)合计
_MEMORY_BUDGET = 400                                              # 记忆块上限（同旧 _format_memory）
_CATALOG_BUDGET = int(os.getenv("PLANNER_CATALOG_BUDGET_CHARS", "8000"))

# 全局兜底 Agent（LLM 抽风/规划失败时降级）由 env 指定，不再硬编码 agent_id（R2.1 P5）。
_FALLBACK_AGENT = os.environ.get("PLANNER_FALLBACK_AGENT", "chitchat")


def _always_include(a) -> bool:
    """catalog 语义预筛/预算裁剪都不得丢的 Agent（取代硬编码 _ALWAYS_INCLUDE，去领域字面量）：
    ①全局兜底 Agent（env PLANNER_FALLBACK_AGENT）；②声明了 route_hints 的 Agent——其确定性
    路由兜底依赖该 manifest 在 catalog 中可见（被预筛丢掉后 RouteHintEngine 就看不到其 hints）。"""
    m = getattr(a, "manifest", None)
    return (getattr(m, "agent_id", "") == _FALLBACK_AGENT
            or bool(getattr(m, "route_hints", [])))

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

    def is_empty(self) -> bool:
        return not (self.obj or self.positions or self.attr
                    or self.last_poi or self.last_destination)


@dataclass
class WorkingSet:
    """一次规划轮装配好的工作上下文。catalog 是已（语义）预筛的 agent 列表。"""
    catalog: list = field(default_factory=list)        # ResolvedAgent 列表（含 .manifest/.endpoint）
    history: list[dict] = field(default_factory=list)  # [{role, text, ts}]
    memories: list[dict] = field(default_factory=list) # [{text, scope, predicate, provenance, confidence}]
    focus: "Focus | None" = None                       # 结构化焦点态（指代消解）

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
    def render_catalog(agents: list) -> str:
        """能力清单 JSON；超 catalog 预算时优先丢相关性最低的**非受保护** agent（从尾部找）。

        受保护 = edge 车控核心（edge-vehicle/edge-media）∪ 兜底 Agent（env）∪ 有 route_hints 的 Agent（见 _always_include）。
        根因修复：edge-vehicle 有几十个 caps、渲染体积大，旧逻辑无差别 pop 尾部会把它或
        chitchat 丢掉——丢 edge 车控→危险动作规划空计划退化（dangerous_trunk_confirm）；丢
        chitchat→开放域兜底缺席、误路由到 info（cloud_chitchat_streaming）。叠加 edge 紧凑
        渲染（见 _catalog_item），正常情况下根本不触发裁剪。"""
        items = [_catalog_item(a) for a in agents]
        protected = [_is_edge_core(a) or _always_include(a) for a in agents]
        out = json.dumps(items, ensure_ascii=False)
        while len(out) > _CATALOG_BUDGET and len(items) > 1:
            idx = next((i for i in range(len(items) - 1, -1, -1) if not protected[i]), None)
            if idx is None:
                break  # 只剩受保护项 → 宁可略超预算也不丢
            items.pop(idx)
            protected.pop(idx)
            out = json.dumps(items, ensure_ascii=False)
        return out


def _is_edge_core(a) -> bool:
    """安全核心：edge/edge_fast 车控 Agent。catalog 预筛与渲染都须保它不被丢，
    否则危险车控的二次确认会退化成 chitchat 兜底。与 ContextManager 预筛判据一致。"""
    m = a.manifest
    return (getattr(m, "deployment", "") == "edge"
            or getattr(m, "kind", "") == "edge_fast")


def _catalog_item(a) -> dict:
    if _is_edge_core(a):
        # edge 车控核心 caps 多（几十个）；只渲染意图名（trunk.open 等），不带 slots/desc——
        # 否则其体积（数千字符）撑爆 catalog 预算、挤掉 chitchat 等 → 路由退化/偏置。
        # slot 由 planner 从用户原话推断（如"26度"→temp），无需 catalog 提示。
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


def _render_memory(memory: list[dict] | None) -> str:
    """长期偏好记忆 → prompt 片段（最多 3 条、≤_MEMORY_BUDGET）。逐字沿用旧 _format_memory。
    勿向用户暴露置信度；高风险动作仍需确认（由执行层保证）。"""
    if not memory:
        return ""
    lines = []
    for m in memory[:3]:
        txt = (m.get("text") or "").strip()
        if not txt:
            continue
        tag = m.get("scope") or m.get("predicate") or ""
        prov = m.get("provenance") or ""
        try:
            conf = float(m.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        lines.append(f"- [{tag} | {conf:.2f} | {prov}] {txt}")
    if not lines:
        return ""
    block = ("已知用户记忆（仅在与当前任务相关时参考，勿向用户暴露置信度）：\n"
             + "\n".join(lines))
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
    if not parts:
        return ""
    return ("当前对话焦点（用于指代消解，仅在用户话术含指代时参考）：\n"
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
    items = data.get("items")
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
        poi = _first_poi(getattr(by_id.get(step.id), "data", None) or {})
        if poi:
            focus.last_poi = poi
        if not focus.last_agent_id:
            focus.last_agent_id, focus.last_intent = step.agent_id, step.intent
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
        history = await self._history(ctx.session_id) if mem_on else []
        memories = await self._recall(text, ctx) if mem_on else []
        focus = await self._load_focus(ctx.session_id) if (mem_on and self.session) else None
        catalog = await self._catalog(text)
        return WorkingSet(catalog=catalog, history=history, memories=memories,
                          focus=focus)

    async def _load_focus(self, session_id: str):
        """载入会话焦点。失败/无则 None，不阻塞规划。"""
        try:
            d = await self.session.load_focus(session_id)
            if not d:
                return None
            valid = {f.name for f in fields(Focus)}
            return Focus(**{k: v for k, v in d.items() if k in valid})
        except Exception as e:
            logger.debug("load_focus failed: %s", e)
            return None

    async def update_focus(self, session_id: str, plan, results):
        """每轮成功完成后更新焦点态（供下一轮指代消解）。绝不抛错、不阻塞主链路。"""
        if not self.session:
            return
        try:
            focus = extract_focus(plan, results)
            if focus is not None:
                await self.session.save_focus(session_id, asdict(focus))
        except Exception as e:
            logger.debug("update_focus failed: %s", e)

    async def append_turn(self, session_id: str, role: str, text: str,
                          user_id: str = "", vehicle_id: str = ""):
        """写入一轮对话到 memory（指代/抽取的数据来源）。memory 不可用或 clients 未提供
        该能力时静默跳过（不阻塞主链路）。user_id 透传给 memory 触发异步偏好抽取。"""
        fn = getattr(self.clients, "append_turn", None)
        if not fn:
            return
        try:
            await fn(session_id, role, text, user_id=user_id, vehicle_id=vehicle_id)
        except TypeError:
            await fn(session_id, role, text)  # 兼容只接受 3 参的旧 stub
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

    async def _history(self, session_id: str) -> list[dict]:
        """取最近对话历史（供指代消解）。失败返回空，不阻塞规划。"""
        fn = getattr(self.clients, "get_session", None)
        if not fn:
            return []
        try:
            return await fn(session_id, self.history_n)
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
            mems = await fn(ctx.user_id, text, kinds=["semantic"],
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
    prefs = {k: meta[k] for k in
             ("model_pref", "answer_length", "assistant_name", "memory_enabled",
              "poi_page",          # "换一批"翻页页码，透传给 navigation
              "vehicle_battery",   # 端侧真实电量，透传给 charging
              "input_source",      # R4.4：hands-free 语音来源（voice_wake|voice_followup|voice_bargein）
              "voice_utterance_ms",  # R4.4：本轮 speech 累计时长（数字字符串）
              "clarify_resume",    # R4.4：澄清续接标记（"1"）——engine 据此深度=1 抑制再澄清
              "llm_provider", "llm_model")  # 运行时硬化 D2：请求级 LLM pin（评测/重放 A/B），
                                            # 随 prefs 下发全部 Agent + engine 设 planner 侧 pin
             if meta.get(k)}
    if "location.read" in granted:
        prefs.update({k: meta[k] for k in
                      ("current_lat", "current_lng", "current_accuracy_m",
                       "current_location_at", "current_location_source")
                      if meta.get(k)})

    return PlanContext(
        request_id=getattr(request, "request_id", ""),
        session_id=getattr(request, "session_id", ""),
        user_id=getattr(request.context, "user_id", "") if hasattr(request, "context") and request.context else "",
        vehicle_id=getattr(request.context, "vehicle_id", "") if hasattr(request, "context") and request.context else "",
        is_confirmation=getattr(request, "is_confirmation", False),
        granted_permissions=granted,
        trace_id=meta.get("trace_id", ""),
        prefs=prefs,
    )
