"""PlanBuilder：LLM 生成 DAG 计划 + 解析 + 校验 + 重试 + 降级。

WS3 §4。LLM 把已注册 Agent 能力当工具，输出 JSON DAG 计划。
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from security.permission import check_permission
from .models import Plan, Step, PlanContext, ReplanDecision
from .context import (
    WorkingSet,
    _FALLBACK_AGENT,
    _is_edge_core,
    assemble_budgeted_catalog,
)
from .route_hints import RouteHintEngine
from . import exemplars as _exemplars
from . import skills as _skills

logger = logging.getLogger("planner.planning")


@dataclass(frozen=True)
class PlannerCapabilityCatalog:
    """Immutable, request-local authority shared by every planner wire path."""

    visible_agents: tuple
    semantic_mapping_text: str
    ref_to_pair: MappingProxyType
    pair_to_ref: MappingProxyType
    agent_map: MappingProxyType
    catalog_stats: MappingProxyType


_CAPABILITY_MAPPING_HEAD = (
    "== 本请求 capability_ref 映射；capabilities 对象的 key 才是 capability_ref；"
    "数组值只读解释，禁止放入 step 或当作 capability_ref =="
)


def _capability_pairs(agents: list) -> list[tuple[str, str]]:
    return sorted({
        (agent_id, intent)
        for agent in (agents or [])
        if (agent_id := str(getattr(
            getattr(agent, "manifest", None), "agent_id", "") or "").strip())
        for cap in (getattr(agent.manifest, "capabilities", None) or [])
        if (intent := str(getattr(cap, "intent", "") or "").strip())
    })


def _build_ref_maps(agents: list) -> tuple[dict[str, tuple[str, str]],
                                           dict[tuple[str, str], str]]:
    ref_to_pair = {
        f"cap_{index:04d}": pair
        for index, pair in enumerate(_capability_pairs(agents), 1)
    }
    return ref_to_pair, {pair: ref for ref, pair in ref_to_pair.items()}


def _unique_agents_by_id(agents: list) -> dict[str, object]:
    """Index candidates without letting duplicate registry identities diverge.

    A duplicate ID with complementary capabilities cannot be represented by the
    existing ``agent_map`` without silently discarding one endpoint.  Reject that
    malformed request catalog before rendering refs which the validator could not
    later honour.
    """
    by_id: dict[str, object] = {}
    for agent in (agents or []):
        agent_id = str(getattr(
            getattr(agent, "manifest", None), "agent_id", "") or "").strip()
        if not agent_id:
            continue
        if agent_id in by_id:
            raise ValueError(f"duplicate planner agent_id: {agent_id}")
        by_id[agent_id] = agent
    return by_id


def _render_capability_mapping(agents: list) -> str:
    """Render the exact grouped ref mapping exposed to and charged for the LLM."""
    ref_to_pair, _ = _build_ref_maps(agents)
    by_id = _unique_agents_by_id(agents)
    capabilities_by_agent: dict[str, dict[str, list]] = {}
    for ref, (agent_id, intent) in ref_to_pair.items():
        agent = by_id[agent_id]
        cap = next((candidate for candidate in agent.manifest.capabilities
                    if str(getattr(candidate, "intent", "") or "").strip() == intent),
                   None)
        if cap is None:
            raise ValueError(
                f"planner capability disappeared while rendering: {agent_id}/{intent}")
        semantics = [intent]
        # Preserve the measured edge-core compression: the semantic name carries its routing
        # semantics, while repeated empty slots/descriptions only add prompt cost.
        if not _is_edge_core(agent):
            semantics.extend([
                list(getattr(cap, "slots", None) or []),
                str(getattr(cap, "description", "") or ""),
            ])
        capabilities_by_agent.setdefault(agent_id, {})[ref] = semantics

    lines = [_CAPABILITY_MAPPING_HEAD]
    for agent_id, capabilities in capabilities_by_agent.items():
        agent = by_id[agent_id]
        semantics = {
            "service": agent_id,
            "kind": str(getattr(agent.manifest, "kind", "") or "agent"),
            "deployment": str(getattr(agent.manifest, "deployment", "") or "cloud"),
            "trust": str(getattr(agent.manifest, "trust_level", "") or ""),
            "capabilities": capabilities,
        }
        lines.append(json.dumps(
            semantics, ensure_ascii=False, separators=(",", ":")))
    if not ref_to_pair:
        lines.append("（本请求无可用能力；steps 必须为 []）")
    return "\n".join(lines)


def _assemble_capability_catalog(agents: list) -> PlannerCapabilityCatalog:
    """Build the sole request-local capability authority after permission filtering."""
    stats: dict = {}
    visible, mapping_text = assemble_budgeted_catalog(
        list(agents or []), _render_capability_mapping, stats)
    ref_to_pair, pair_to_ref = _build_ref_maps(visible)
    agent_map = {
        str(getattr(agent.manifest, "agent_id", "") or ""): agent
        for agent in visible
    }
    return PlannerCapabilityCatalog(
        visible_agents=tuple(visible),
        semantic_mapping_text=mapping_text,
        ref_to_pair=MappingProxyType(ref_to_pair),
        pair_to_ref=MappingProxyType(pair_to_ref),
        agent_map=MappingProxyType(agent_map),
        catalog_stats=MappingProxyType(dict(stats)),
    )


def _verification_dict(cap) -> dict:
    """capability.verification（proto/dict/None）→ Step 用的纯 dict。

    编排核心**只搬运不解释**：`expect` 的领域语义由 verify.py 的求值器按 mode 消费，
    这里出现任何 intent/agent 字面量分支即违反 M2 声明式铁律（契约测试 test_verify 锁）。
    """
    v = getattr(cap, "verification", None)
    if v is None:
        return {}
    if isinstance(v, dict):
        raw = v
    else:
        if not str(getattr(v, "mode", "") or "").strip():
            return {}          # proto 未设该字段时 mode 为空串
        from google.protobuf.json_format import MessageToDict
        raw = MessageToDict(v, preserving_proto_field_name=True)
    mode = str(raw.get("mode", "") or "").strip()
    if not mode or mode == "none":
        return {}
    return {
        "mode": mode,
        "timeout_ms": int(raw.get("timeout_ms") or 0),
        "on_fail": str(raw.get("on_fail", "") or ""),
        "max_attempts": int(raw.get("max_attempts") or 0),
        "expect": raw.get("expect") if isinstance(raw.get("expect"), dict) else {},
    }


# R4.4 修正（2026-07-27 真机）：**祈使式指令一律视为「在跟助手说话」，不问模型**。
# 真机现象：「记住，我女儿叫小满」被 planner 判 `addressed=false`（当成乘客间闲聊）→ 静默
# 短路，用户**说了、没回、也没记**（拒识轮按设计不落库不进画像）。而它是间歇的——同一句
# 某次 3/3 被拒、换一批 6 条又只拒 1 条，是 LLM 判定的方差，不是判据问题。
# 同「系统持有的事实不交给 LLM 答」一族：**用户用祈使句直接对你下指令，这件事不需要模型
# 判断**。只覆盖无歧义的祈使前缀（记忆类指令，误判代价最大的一类），其余仍由模型判。
# 须**锚在句首**（去礼貌前缀后）：「我不记得了」「他记住了」不是指令，不能劫持。
_POLITE_PREFIX_RE = re.compile(r"^[\s，,。.、]*(那|哎|诶|嘿|嗯|请|麻烦|你好|喂)*[\s，,、]*")
_DIRECTIVE_RE = re.compile(
    r"^(帮我|给我|你|请)?(记住|记一下|记下来|记下|记着|记得|别忘了|别忘记|别忘)")


def _is_directive_to_assistant(text: str) -> bool:
    """句首是显式祈使指令（「记住…」）→ 必然是对助手说的，不接受模型的 not_addressed 判定。"""
    return bool(_DIRECTIVE_RE.match(_POLITE_PREFIX_RE.sub("", (text or "").strip())))


# M2 P2（子 RFC §2.3）：会话级情绪信号的封闭词表。**不进记忆层**——短 TTL 且不入画像的
# 东西是会话态不是记忆；它唯一的消费方是 TTS 情感参数（M1b 已就绪的能力面），要的是
# 「当前这轮」不是画像。词表外/缺省一律 neutral（fail-open，与 addressed/clarify 同款）。
EMOTIONS = ("neutral", "happy", "tired", "urgent", "frustrated")


def _parse_emotion(raw) -> str:
    """LLM 输出的情绪标签 → 封闭词表内的值；非法/缺省 → ""（= neutral，不发信号）。"""
    v = str(raw or "").strip().lower()
    if v in EMOTIONS and v != "neutral":
        return v
    return ""


def _date_line() -> str:
    """规划 prompt 的日期锚（上海时区，日粒度——时刻由端侧墙钟直答负责，不进 prompt 防
    每分钟扰动）。badcase f11aa344：prompt 无日期锚，LLM 把「今年世界杯」按训练先验改写成
    「2024年世界杯」灌进检索槽位——相对时间词必须有权威基准可换算。"""
    now = datetime.now(timezone(timedelta(hours=8)))
    wd = "一二三四五六日"[now.weekday()]
    return f"当前日期：{now.year}年{now.month}月{now.day}日 周{wd}（今年={now.year}年）"

# 路由兜底已全部机制化：research.run 与 trip.*（含 trip.plan 的目的地/天数/偏好抽取）均由各
# Agent manifest.route_hints 声明、通用 RouteHintEngine 消费（R2.1）；trip.plan 的话术抽取在
# trip-planner Agent 的 extract.py。编排核心不再持任何领域正则/Agent 字面量。

# M0b Full Migration（2026-07-24，canary A/B 达标后收敛）：领域组合知识（多日出行/顺路
# 停靠/条件依赖）与跨域判据（时效深度/隐式车控）已外迁 skills/{guides,policies}/*.yaml，
# 由 plan_skills() 检索后注入 user message（== 规划知识 == 块，SKILLS_MODE 默认 full）。
# 本常量为唯一 base，只留通用规划契约与通用示例——加规划知识=投 skill 文件，不改此处。
_PLANNER_BASE = (
    "你是智能座舱的任务编排器。根据用户话术和可用 agent 能力清单，输出 JSON 调用计划。\n"
    "格式严格为：{\"complexity\":\"simple|adaptive\",\"goal\":\"一句话目标\","
    "\"steps\":[{\"id\":\"s1\",\"capability_ref\":\"从本请求映射选择\","
    "\"slots\":{..},\"depends_on\":[],\"slot_refs\":{}}]}\n"
    "simple 表示一次可确定全部步骤；adaptive 表示必须根据运行结果决定下一步"
    "（例如满了换次近、失败换一家、探索式查询）。普通单域、多意图并行、固定串行都选 simple。\n"
    "**个人偏好指代**（「我喜欢的温度」「常用的那个」「老样子」）：槽位值只能取自上下文里"
    "召回的用户偏好记忆；记忆里没有对应值就**留空该槽位**让能力方追问——绝不臆造一个数值"
    "直接执行（把空调猜成 22 度打开比多问一句糟糕得多）。\n"
    "\n"
    "== 意图拆分 ==\n"
    "- 用户一句话包含多个意图时（如『打开空调并播放音乐』），必须拆成多个 step\n"
    "- 单意图只输出一个 step，不要过度拆分\n"
    "\n"
    "== 并行 vs 串行 ==\n"
    "- 无数据依赖的步骤 → 各自 depends_on=[]，执行器会自动并行\n"
    "- 有数据依赖（如先搜索再预订）→ 后续步骤用 depends_on + slot_refs 引用前序结果\n"
    "- 判断依据：后一步是否需要前一步的输出数据？不需要则并行\n"
    "\n"
    "== 指令类型（规划时参考，影响执行语义）==\n"
    "- 控制类（control）：车控/媒体等硬件操作，立即执行，可并行。如 hvac.set、media.play\n"
    "- 引导类（guide）：打开 UI/导航界面。如 navigation.search_poi\n"
    "- 播报类（query）：查询后播报结果，需要联网。如 info.weather、info.news\n"
    "- 不同类型互不阻塞，可并行；同类型也可并行（只要无数据依赖）\n"
    "\n"
    "== DAG 形态规则 ==\n"
    "- 两个独立动作：输出两个 step，两者 depends_on 都为空\n"
    "- 先获取结果再执行：后一步 depends_on 指向前一步，slot_refs 引用它的结果\n"
    "- 控制与查询没有数据依赖时仍并行，不因领域不同强制串行\n"
    "- 每个 step 的 capability_ref 只能从本请求末尾映射中选择\n"
    "\n"
    "== 通用规则 ==\n"
    "- 用 slot_refs 引用前序 step 结果，如 {\"poi_id\":\"s1.data.items.0.id\"}\n"
    "- 若用户话术含指代（如『再调高一点』『还是刚才那家』『换个颜色』），"
    "优先结合下方『当前对话焦点』（对象/位置/属性/上个地点）、再参考『最近对话』"
    "补全对象/槽位后再规划\n"
    "- **省略式追问延续上一轮**：用户只说『明天呢』『那后天呢』『换成XX呢』这类省略句，"
    "是把**最近对话里最后一轮**的问题换个时间/对象重问——必须沿用上一轮的意图与能力"
    "（『当前对话焦点』的上一轮意图可参考），只替换对应槽位；不得凭省略句里的零星词"
    "改判到别的领域（如上一轮问比赛赛程，『明天呢』=查明天赛程，不是查天气）\n"
    "- 只输出 JSON，不要任何解释\n"
    "- 无法匹配时输出 {\"steps\":[]}"
)


_CATALOG_ALLOWLIST_SECTION = (
    "\n\n== 本轮动态能力白名单 ==\n"
    "- 本请求末尾映射是唯一调用权；每个 group 的 capabilities 是对象，"
    "capabilities 的 object key 才是 capability_ref，step.capability_ref 只能逐字复制其中一个 key\n"
    "- 数组值只读解释，禁止放入 step 或当作 capability_ref；"
    "每个 step 只能包含 id、capability_ref、slots、depends_on、slot_refs 五个字段\n"
    "- 抽象正反例：能力条目 {\"cap_0042\":[\"example.semantic\",[\"argument\"],"
    "\"abstract description\"]}；正确：step.capability_ref=\"cap_0042\"；"
    "错误：step.capability_ref=\"example.semantic\"\n"
    "- 不得编造映射里没有的 ref，不得输出缺席能力，也不得替换用户真正请求但"
    "缺席的能力（包括拿清单中已有的相近能力顶替）\n"
    "- 如果用户请求只能由本轮 catalog 中缺席的能力承接，仍视为已受话，严格返回"
    " {\"addressed\":true,\"steps\":[]}；不要为了给出非空 steps 而替换或虚构能力"
)

# R4.4：受话判定段——恒附在 base 之后（消费端 engine 按 input_source 门控，附着无副作用）。
# 保守取向：拿不准输出 true（宁可处理不可误丢，母卡 §7 风险缓解）。provider 无关：纯 JSON、
# 字段可选、fail-open。
_ADDRESSED_SECTION = (
    "\n\n== 受话判定（必须输出）==\n"
    "输出顶层布尔字段 \"addressed\"：这句话是否是对你（车载助手）说的。\n"
    "- true：请求/问题/指令/情绪表达（如『好烦啊』『我有点冷』也需要你回应）\n"
    "- false：明显不是对助手说的——乘客间对话片段（『妈你到哪了』）、自言自语、"
    "电台/视频/新闻播报腔（『本台记者报道…』『欢迎收听今天的节目』）、"
    "称呼他人姓名的交谈（『王总我马上发您』）、无法构成请求的残句\n"
    "- **拿不准时必须输出 true**（宁可处理，不可误丢）\n"
    "- addressed 为 false 时输出 {\"addressed\":false,\"steps\":[]}，不要输出其他内容"
)

# R4.4：路由歧义澄清段——仅当 CLARIFY_ENABLED=on 时拼入（off 时 LLM 不会输出 clarify，
# 避免它输出后被 engine 丢弃退化成空计划话术，母卡实施计划 §0-10）。
_CLARIFY_SECTION = (
    "\n\n== 路由歧义澄清（谨慎使用）==\n"
    "仅当这句话确实是对你说的、但在能力清单上存在两种以上合理且结果差异明显的落法、"
    "且从『当前对话焦点』『最近对话』都无法确定用户要哪种时，输出澄清代替 steps：\n"
    "{\"addressed\":true,\"steps\":[],\"clarify\":{\"question\":\"口语化一句提问\","
    "\"options\":[{\"label\":\"不超过10字\",\"send_text\":\"消歧后的完整第一人称指令\"}]}}\n"
    "- options 2~3 个；send_text 必须可直接当用户新指令执行（如『帮我找附近的川菜馆』）\n"
    "- **绝大多数请求是明确的，明确请求绝不允许反问**\n"
    "- **整句只有一个名词、动词完全缺失**（如『上海』『华润大厦』『稻香』）是典型歧义："
    "用户给了对象却没说要拿它做什么，导航/查天气/查限行/播放都讲得通且结果差异很大"
    "——**必须澄清，不要替用户选一个动作**\n"
    "- **缺槽位不算歧义**（『导航』缺目的地→照常输出 step，由对应 agent 追问）；"
    "缺的是**槽位**才照常执行，缺的是**动词**要澄清\n"
    # 2026-08-03 泓舟拍板。判据刻意写成**两面**：只写「状态句要澄清」会把「有点闷」
    # 「太吵了」「有点热」一起带进反问——那几条的 gold 明确接受任一缓解手段。
    # 分界是**猜错有没有实际代价**：干挡风上刮雨刷是有害的，而开窗还是开空调都只是
    # 程度不同的缓解。改动前后各跑 7 条 ex.colloquial.* × 3 次比对过，兄弟用例零回退。
    "- 用户只描述一个不适**状态**、没说要什么动作时：若各手段只是**程度不同的缓解**"
    "（闷/吵/热/冷）→ 照常直接执行**一个**，绝不反问；只有手段**互斥且猜错有实际代价**"
    "（『看不清路』＝开大灯 还是 开雨刷——干挡风上刮雨刷是有害的）才澄清\n"
    "- 多意图句只要主意图清楚就正常拆 step，不因次要成分歧义而澄清"
)


# M2 P2（子 RFC §2.3）：会话级情绪信号。**刻意走 prompt-only、不进 submit_plan schema**
# ——B4-1 两轮教训已证明「模型对 schema 结构的响应强于 description 文本」，把可选字段
# 摆进 schema 会诱发多填；emotion 是旁路信号（只喂 TTS 选参、不影响 steps），更不值得
# 冒行为漂移的风险。模型不输出=neutral（fail-open，与 addressed/clarify 同款姿态）。
_EMOTION_SECTION = (
    "\n\n== 情绪标注（可选，一个词）==\n"
    "如果用户这句话明显带情绪，额外输出顶层字段 \"emotion\"，取值只能是："
    "happy（开心/兴奋）、tired（疲惫/困倦）、urgent（着急/赶时间）、frustrated（烦躁/不满）。\n"
    "- 平静陈述、普通指令、单纯提问一律**不要输出该字段**（默认中性）\n"
    "- 它只影响播报语气，不影响你的规划——绝不为了标情绪改变 steps"
)


# M1a（submit_plan 结构化输出，RFC §3.3）：toolcall 模式不改 base/受话段/澄清段——JSON
# 协议描述同时是工具 schema 的语义说明，双路径共享领域协议=A/B 单变量；仅追加输出通道指令。
# 「完整参数表」段=真栈 B1-4 修复：工具输出形态自带「函数入参只传需要的」先验，省略式
# 追问会只写变化槽（date）丢继承槽（city）执行错对象；schema description 压不住（1/3），
# 形态 few-shot 才有效。示例用抽象 A市 + 通用槽键，不嵌 agent/intent 字面量（铁律）。
_TOOLCALL_SECTION = (
    "\n\n== 输出通道（工具调用模式）==\n"
    "上述全部输出协议（计划 JSON / addressed / clarify）一律通过调用 submit_plan 工具提交："
    "顶层 JSON 对象即工具参数。不要以文本形式输出 JSON，不要输出任何解释。\n"
    "arguments 每次必须同时包含 addressed 和 steps；无步骤也必须显式 steps=[]，"
    "不得只提交 addressed。\n"
    "steps 数组中每一项只能包含 id、capability_ref、slots、depends_on、slot_refs 这五个字段。"
    "这五个字段名必须逐字原样输出，不得转义、增删字符或改变拼写。"
    "属于 step 的字段必须留在对应 step 对象内，不得移到顶层参数。\n"
    "slots 是该步骤的**完整参数表，不是增量**：省略式追问必须把上一轮继承的槽位与本轮"
    "变化的槽位一起写全。例：上一轮『明天A市天气怎么样』该步 slots={\"city\":\"A市\","
    "\"date\":\"明天\"}，本轮用户说『那后天呢』→ 本轮 slots 必须={\"city\":\"A市\","
    "\"date\":\"后天\"}；只写 {\"date\":\"后天\"} 丢掉继承的 city 会执行错对象。\n"
    "写全指**上下文里实际有的值**：用户话术和上下文里都没有的槽位直接省略该键，"
    "绝不编造占位值（如『当前位置』『未知』）——留空由能力方用定位/追问补全，"
    "填了占位字面量反而会当成真值执行出错。"
)

_SUBMIT_PLAN_NAME = "submit_plan"
_PLANNER_STEP_FIELDS = (
    "id", "capability_ref", "slots", "depends_on", "slot_refs",
)


def _submit_plan_tools(catalog: PlannerCapabilityCatalog | None = None) -> dict:
    """submit_plan 工具定义（线格式 ``{"tools":[...],"tool_choice":named 强制}``，RFC §3.2）。

    schema 顶层=现 JSON 协议顶层——语义零漂移，`_parse_and_validate_data` 直接消费；
    **无 require_confirm**（确认权不在 LLM，M0a 已中央落实）；clarify 属性与
    _CLARIFY_SECTION 同门控（off 时 schema 不反向引导模型输出澄清）。named tool_choice
    强制 + prompt 指令双保险；某家不认 → build() 轮内降级承接（RFC §4）。

    build() 传入权限过滤且预算裁剪后的请求级 catalog；工具、prompt、解析和
    validator 因此共用同一可见面。缺 catalog 时只能提交空 steps，不退回开放字符串。"""
    refs = list(catalog.ref_to_pair) if catalog is not None else []
    capability_ref_schema = {
        "type": "string",
        "description": (
            "只能逐字复制本请求映射中 capabilities 对象的 key；"
            "数组值只读解释，禁止放入 step 或作为 capability_ref；"
            "请求能力缺席时保持 steps=[]"),
    }
    if refs:
        capability_ref_schema["enum"] = refs

    step_field_schemas = {
        "id": {"type": "string"},
        "capability_ref": capability_ref_schema,
        # 语义必须随字段走（真栈 B1-4：空 object 诱发省略追问丢继承槽）
        # （date=后天）丢继承槽（city=杭州），执行错落定位城市；JSON 文本路径靠
        # prompt 规则+few-shot 引导写全、从不丢。
        "slots": {"type": "object", "description": (
            "该步骤的全部槽位键值。省略式追问（如『那后天呢』『换成XX呢』）必须把"
            "从上一轮继承的槽位（城市/对象等）与本轮变化的槽位一起显式写全——"
            "只写变化的槽位会导致执行错对象")},
        "depends_on": {"type": "array", "items": {"type": "string"}},
        "slot_refs": {"type": "object"},
    }
    step_item_schema = {"type": "object", "properties": {
        field: step_field_schemas[field] for field in _PLANNER_STEP_FIELDS
    }, "required": list(_PLANNER_STEP_FIELDS),
        "additionalProperties": False}
    steps_schema = {
        "type": "array",
        "description": (
            "arguments 每次必须同时包含 addressed 和 steps；无步骤也必须显式 steps=[]，"
            "不得只提交 addressed。步骤只能使用本请求动态 catalog 的 capability_ref；"
            "请求所需能力缺席时返回 addressed=true 且 steps=[]，不得编造或用相近能力替换"),
        "items": step_item_schema,
    }
    if not refs:
        # 空 catalog 没有合法 enum（JSON Schema enum 必须非空）；直接约束 steps 为空。
        steps_schema["maxItems"] = 0

    props = {
        "complexity": {"type": "string", "enum": ["simple", "adaptive"],
                       "description": "simple=一次可确定全部步骤；adaptive=须按运行结果决定下一步"},
        "goal": {"type": "string", "description": "一句话目标"},
        "addressed": {"type": "boolean",
                      "description": "这句话是否是对车载助手说的；拿不准必须输出 true"},
        "steps": steps_schema,
    }
    # clarify 刻意**不进 schema**（真栈 B4-1 两轮教训）：schema 把 clarify 变成「摆在
    # 眼前的可选字段」，结构可见性把误澄清率从 0 抬到 ~50-66%（历史追问「我刚才让你调到
    # 多少」被反问），且 description 带满「绝大多数请求明确」约束也压不回去——模型对
    # schema 结构的响应强于 description 文本。退回 prompt-only 触发面（_CLARIFY_SECTION
    # 恒拼，见 _planner_system）＝R4.4 验收时的原始形态：软 schema 下模型按 prompt 在
    # arguments 里输出 clarify 属额外字段、完全合法，_parse_and_validate_data 照常消费
    # ——触发条件两路径对称，都只由 prompt 判据承载。
    return {
        "tools": [{"type": "function", "function": {
            "name": _SUBMIT_PLAN_NAME,
            "description": (
                "提交本轮规划结果。这是唯一合法的输出通道。arguments 每次必须同时包含 "
                "addressed 和 steps；无步骤也必须显式 steps=[]，不得只提交 addressed。"),
            "parameters": {"type": "object", "properties": props,
                           "required": ["addressed", "steps"]},
        }}],
        "tool_choice": {"type": "function", "function": {"name": _SUBMIT_PLAN_NAME}},
    }


def _planner_system(toolcall: bool = False) -> str:
    """每次 build() 实时拼 Planner system prompt：base + 受话段（恒附）+ 澄清段（CLARIFY_ENABLED=on）。
    os.getenv 实时读——env 翻转即刻生效，且让 monkeypatch 单测可行（母卡实施计划 §0-10）。
    Full Migration 后 base 唯一（领域知识由 skill 注入块承载，见 skills.py）。
    toolcall=True（M1a）追加输出通道指令段，其余逐字一致。"""
    prompt = _PLANNER_BASE + _ADDRESSED_SECTION
    if os.getenv("PLANNER_EMOTION", "on").strip().lower() != "off":
        prompt += _EMOTION_SECTION
    # 缺省 on = 与部署缺省同源。`.env.example` 与 compose 自 2026-07-08 真栈 CDP 验收后
    # 就是 `${CLARIFY_ENABLED:-on}`，只有**代码兜底**还停在 off——于是任何不经 compose
    # 起的进程（评测/单测/CLI）测的都不是生产装配。对抗测试 §4.1 那四条 candidate 正是
    # 在这个错位下被记成「生产默认 off」的（findings 的这句话是读代码兜底读出来的）。
    if os.getenv("CLARIFY_ENABLED", "on").lower() == "on":
        prompt += _CLARIFY_SECTION
    # 放在全部静态示例和行为段之后，避免示例里的 agent/intent 在当前 catalog 缺席时
    # 被弱模型误当成仍可调用；toolcall 只在其后追加输出通道，不改变能力边界。
    prompt += _CATALOG_ALLOWLIST_SECTION
    if toolcall:
        prompt += _TOOLCALL_SECTION
    return prompt


_REPLAN_SYSTEM = (
    "你是智能座舱有界任务循环的再规划器。根据用户目标、最近观察和可用能力，"
    "一次性判断任务是否完成，并在未完成时给出下一批 JSON DAG。\n"
    "下方本请求 capability_ref 映射是唯一调用权；steps 只能原样选择映射中的 ref。"
    "不得编造 catalog 外能力，不得替换"
    "用户请求的缺席能力，也不得输出 catalog 中缺席的能力；没有可承接能力时保持"
    " steps=[]，不得拿相近能力顶替。\n"
    "严格输出 JSON：{\"done\":true|false,\"steps\":[{\"id\":\"r1\","
    "\"capability_ref\":\"从本请求映射选择\",\"slots\":{},\"depends_on\":[],"
    "\"slot_refs\":{}}]}。仅在必要时改变计划；不得输出解释。"
)


def _hint_effect(hit: bool, before: list, after: list, had_clarify: bool) -> str:
    """RouteHintEngine 对计划的实际作用分类（纯观测，数据飞轮 P0）。

    "" = 未命中任何 hint；noop = 命中但计划未变（LLM 已正确路由 / append 目标已存在）；
    fill = 空计划被 hint 补出步骤；fill_over_clarify = 补步同时盖掉了待出的澄清卡
    （D3：hint 在澄清之前生效，是否合意待数据裁决）；append = 在既有步骤上并列补步；
    replace = 非空计划被整条改写。"""
    if not hit:
        return ""
    if after == before:
        return "noop"
    if not before:
        return "fill_over_clarify" if had_clarify else "fill"
    if len(after) > len(before) and after[:len(before)] == before:
        return "append"
    return "replace"


class PlanBuilder:
    def __init__(self, llm_fn, registry_fn, llm_tool_fn=None):
        """
        llm_fn: async (messages: list[dict]) -> str
        registry_fn: async (query: str, top_k: int) -> list[ResolvedAgent]
        llm_tool_fn: async (messages, tools: dict) -> (content: str, tool_calls: list[dict])
            —— M1a submit_plan 结构化输出通道，可选：None 时 PLANNER_TOOLCALL 即使 on 也
            走 JSON 路径（存量测试/spy 零波及，RFC §4）。
        """
        self._llm = llm_fn
        self._resolve = registry_fn
        self._llm_tools = llm_tool_fn
        # R2.1：确定性路由兜底降为通用引擎——领域正则由各 Agent manifest.route_hints 声明，
        # 编排核心不再硬编码特定 Agent/意图（恢复「新增 Agent 不改编排核心」铁律）。
        self._route_hints = RouteHintEngine(self._validated_steps)

    async def build(self, text: str, working_set: WorkingSet, ctx: PlanContext,
                    granted_permissions: list[str] = None) -> Plan:
        """构建执行计划。最多重试 1 次，失败降级到语义路由。

        working_set: 由 ContextManager 装配的工作上下文——已语义预筛的 catalog +
        最近对话历史 + 长期记忆召回，统一字符预算渲染（见 context.py）。
        granted_permissions: 用户已授予的权限列表。规划时过滤掉越权能力，
        LLM 看不到用户无权调用的 Agent/意图（越权能力不暴露给 LLM）。
        """
        agents = list(working_set.catalog)
        # 权限过滤：只保留用户有权调用的 Agent
        if granted_permissions is not None:
            agents = self._filter_by_permission(agents, granted_permissions)
        catalog = _assemble_capability_catalog(agents)
        agents = list(catalog.visible_agents)
        agent_map = catalog.agent_map
        working_set.catalog_stats = dict(catalog.catalog_stats)

        # M0b Skill 层（Full Migration 后默认 full）：canary/full=注入块；shadow=只检索
        # 记录；off=注入关（debug 档，无领域知识）。词法档零网络同步计算；hybrid 档一次
        # Embed 语义预筛（fail-open 回词法，见 skills.py）。名单落 plan.skills 供
        # cloud.planning span 归因（@lex/@vec=检索通道，!clipped=超预算未注入）。
        # M5 P1 范例库并行检索（第三通道，最软层）：两条通道各要一次 Embed，串行等于把
        # 规划轮的网络等待翻倍——gather 并发后墙钟仍是一次（共享 embedding.py 的冷却，
        # 网关挂了两边一起回落词法而不是各超时一次）。
        (sk_mode, sk_names, sk_block), (_, ex_names, ex_block) = await asyncio.gather(
            _skills.plan_skills(text, capability_refs=catalog.pair_to_ref),
            _exemplars.plan_exemplars(text, capability_refs=catalog.pair_to_ref),
        )

        # M1a（RFC §4）：PLANNER_TOOLCALL=on 且注入了 llm_tool_fn → 第 1 轮走 submit_plan
        # 工具通道；协议失败（异常/无 tool_calls）同轮内容抢救、第 2 轮直接 JSON 路径——
        # 降级轮内闭合，最坏 2 次调用与现状重试上限一致。默认 on（2026-07-24 泓舟拍板，
        # A/B 材料 RFC §11：协议畸形归零+功能持平+journeys 15/15）；off=JSON 纯文本
        # 回退档（badcase 对照/弱 tool-calling 厂商应急用）。
        toolcall = (os.getenv("PLANNER_TOOLCALL", "on").strip().lower() == "on"
                    and self._llm_tools is not None)
        plan = None
        plan_mode = "json"
        last_raw = ""
        no_action = 0        # 「受话了、但不该做任何动作」连续出现的次数，见循环后
        last_mode = "json"
        for attempt in range(2):
            mode = "json"
            if toolcall and attempt == 0:
                raw, args = await self._llm_plan_tools(text, catalog, working_set,
                                                       skills_block=sk_block,
                                                       exemplars_block=ex_block)
                last_raw = raw or last_raw
                if args is not None:
                    data = args
                    parsed = self._parse_and_validate_data(args, catalog, text)
                    mode = "toolcall"
                else:
                    # 模型无视工具直接文本输出（或 named tool_choice 不被支持）→ 同轮抢救
                    data = self._extract_data(raw)
                    parsed = (self._parse_and_validate_data(data, catalog, text)
                              if data is not None else None)
                    mode = "toolcall_salvage"
            else:
                raw = await self._llm_plan(text, catalog, working_set,
                                           skills_block=sk_block,
                                           exemplars_block=ex_block)
                last_raw = raw or last_raw
                data = self._extract_data(raw)
                parsed = (self._parse_and_validate_data(data, catalog, text)
                          if data is not None else None)
                mode = "toolcall_fallback" if toolcall else "json"
            # 祈使指令不接受 not_addressed（2026-07-27 真机）：置为「本次没解析出计划」，
            # 走既有的重试→fallback 机制。**刻意不直接改判成 chitchat**——重试有机会拿到
            # 正确的计划（「记住，明天八点提醒我开会」该进提醒域而不是闲聊），只有两次都判
            # 不受话才落 chitchat 兜底。代价仅在误判时多一次规划调用。
            if (parsed is not None and not parsed.addressed and not parsed.steps
                    and _is_directive_to_assistant(text)):
                logger.info("planner said not_addressed on a directive, overriding: %s",
                            text[:40])
                parsed = None
            # R4.4：放行「合法的空 steps 计划」——受话判定 addressed=false / 澄清 clarify
            # 的正确输出 steps 恰为空，不能当解析失败去重试+fallback（母卡实施计划 §0-1/§0-2）。
            if parsed and (parsed.steps or not parsed.addressed or parsed.clarify):
                plan = parsed
                plan_mode = mode
                break
            if parsed is None and self._looks_like_no_action(data):
                no_action += 1
                last_mode = mode

        # **第三种合法的空 steps：受话了，而且不该做任何动作。**
        # 上面那条 R4.4 的放行只白名单了两种（不受话 / 澄清）。可是「空调先别关」这类
        # 否定句里，`negation-and-deferral` policy 教出来的**正确**输出恰恰是
        # `{"addressed":true,"steps":[]}`——旧实现把它当解析失败，重试一次后落
        # `_fallback`，而兜底产物又恰好也是 `chitchat.talk`（这一族的 gold）。
        # 于是「模型判断对了」与「模型没答上来」在观测上逐字相同：`plan_mode` 记成
        # `toolcall_degraded`，落域评测那边也分不出这条绿是判断给的还是兜底给的
        # （2026-08-03 实测，对抗套件 findings §5.5 的「否定簇已修 ✅」因此站不住）。
        #
        # 只在**第二次也这么说**时才认：一次空 steps 可能只是模型抽风（「打开空调」
        # 偶尔也会空手而归），那时重试仍是那条便宜的保险。两次都说「不需要动作」
        # 就是它的判断，不是失败——再重试一次是在second-guess一个明确答案。
        # 用户可见行为**一个字都没变**（仍是一条 chitchat.talk），变的只有诚实度。
        if plan is None and no_action >= 2:
            talk = self._talk_only_plan(text, agents)
            if talk is not None:
                logger.info("planner said 'no action needed' twice, honouring it: %s",
                            text[:40])
                plan = talk
                plan_mode = f"{last_mode}_no_action"

        if plan is None:
            logger.warning("Plan parse failed twice, falling back to chitchat/routing")
            # 降级：chitchat 全局兜底 / Registry 语义路由 top-1
            plan = await self._fallback(text, agents)
            plan_mode = "toolcall_degraded" if toolcall else "json"
        # 观测：保留 LLM 最后一次原始输出（fallback 路径保留失败现场），供 planning span 门控采集
        plan.raw_llm = last_raw
        plan.skills = sk_names
        plan.exemplars = ex_names
        plan.plan_mode = plan_mode
        # guide 的软提示偶尔会被模型忽略。plan_repairs 是同一资产里的声明式窄归一：
        # 只能给已存在且唯一的两步补数据接线，不新增路由、不覆盖用户真值；实际作用
        # 单列 skill_effects，不能让「模型原生答对」与「知识层修过」在观测上混成一个。
        plan.skill_effects = _skills.apply_plan_repairs(plan, text, sk_names)

        # 确定性路由兜底（覆盖 LLM 解析成功 + 降级语义路由两条路径）：通用 RouteHintEngine
        # 按各 Agent manifest.route_hints（priority 降序）施加。research.run 与 trip.*（含
        # trip.plan append 新出行兜底）全部为各 Agent 声明式 route_hints——编排核心不含任何
        # 领域 Agent/意图字面量（恢复「新增 Agent 不改编排核心」铁律）。
        # 落域可观测（数据飞轮 P0）：记录 hint 对计划的实际作用——「hint 改写率」是规则
        # 依赖率（北极星 N2）的分子，「盖掉澄清」是 D3 行为裁决的数据。仅观测不改行为。
        had_clarify = plan.clarify is not None
        before = [(s.agent_id, s.intent) for s in plan.steps]
        hit = self._route_hints.apply(plan, text, agent_map)
        plan.hint_effect = _hint_effect(
            hit, before, [(s.agent_id, s.intent) for s in plan.steps], had_clarify)
        plan.catalog_stats = dict(working_set.catalog_stats)
        step_summary = [(s.id, s.agent_id, s.intent) for s in plan.steps]
        logger.info("Plan ready: complexity=%s steps=%s", plan.complexity, step_summary)
        return plan

    @staticmethod
    def _planner_user_msg(text: str, catalog: PlannerCapabilityCatalog,
                          working_set: WorkingSet,
                          skills_block: str = "", exemplars_block: str = "") -> str:
        """双路径共用的 user message（逐字一致=A/B 单变量，RFC §3.3）。"""
        ctx_block = working_set.render_context()  # 记忆 +（焦点）+ 历史，统一预算
        # skills 块紧跟日期锚之后（policy 文本引用「上方『当前日期』」，顺序是契约）
        sk_part = f"{skills_block}\n\n" if skills_block else ""
        # 范例块在知识块**之后**：权威链上范例是最软层，块内抬头也写明「与上方规划知识
        # 冲突以知识为准」——位置与文案一起表达同一件事，不靠模型自己揣摩优先级。
        ex_part = f"{exemplars_block}\n\n" if exemplars_block else ""
        # 软资产与历史可能引用已经从本轮 working set 移除的能力。catalog 若放在它们
        # 前面，弱模型会把后出现的旧范例当成仍可调用的能力；因此把本轮唯一白名单
        # 放到全部知识、范例和上下文之后，紧贴用户原话重新封口。
        return (f"{_date_line()}\n{sk_part}{ex_part}{ctx_block}"
                f"{catalog.semantic_mapping_text}\n\n用户说: {text}")

    async def _llm_plan(self, text: str, catalog: PlannerCapabilityCatalog,
                        working_set: WorkingSet,
                        skills_block: str = "", exemplars_block: str = "") -> str:
        user_msg = self._planner_user_msg(text, catalog, working_set, skills_block,
                                          exemplars_block)
        try:
            raw = await self._llm([
                {"role": "system", "content": _planner_system()},
                {"role": "user", "content": user_msg},
            ])
            logger.info("LLM plan raw: %s", (raw or "")[:500])
            return raw
        except Exception as e:
            logger.warning("LLM plan exception: %s", e)
            return ""

    async def _llm_plan_tools(self, text: str, catalog: PlannerCapabilityCatalog,
                              working_set: WorkingSet,
                              skills_block: str = "", exemplars_block: str = ""
                              ) -> tuple[str, dict | None]:
        """M1a submit_plan 工具通道（RFC §4）。返回 (raw, args)：args=工具 arguments
        dict（协议成功）；None=协议失败（异常/无 tool_calls），raw 保留 content 供同轮
        文本抢救与 obs raw_llm 采集。"""
        user_msg = self._planner_user_msg(text, catalog, working_set, skills_block,
                                          exemplars_block)
        try:
            content, calls = await self._llm_tools([
                {"role": "system", "content": _planner_system(toolcall=True)},
                {"role": "user", "content": user_msg},
            ], _submit_plan_tools(catalog))
        except Exception as e:
            logger.warning("LLM plan toolcall exception: %s", e)
            return "", None
        args = next((c.get("arguments") for c in (calls or [])
                     if isinstance(c, dict) and c.get("name") == _SUBMIT_PLAN_NAME
                     and isinstance(c.get("arguments"), dict)), None)
        if args is not None:
            raw = json.dumps(args, ensure_ascii=False)
            logger.info("LLM plan toolcall args: %s", raw[:500])
            return raw, args
        logger.info("LLM plan toolcall no tool_calls, content: %s", (content or "")[:300])
        return content or "", None

    async def replan(self, goal: str, observations: list[dict], agents: list,
                     ctx: PlanContext, granted_permissions: list[str] = None,
                     working_set: WorkingSet = None,
                     skill_names: list[str] | None = None,
                     exemplar_names: list[str] | None = None) -> ReplanDecision:
        """Decide completion and optionally produce the next validated batch.

        working_set: 复用初规划的同一装配——再规划也注入历史(+焦点)，消除初规划与
        再规划上下文不一致（见 docs/design/2026-06-25-context-system-redesign.md P3）。
        skill_names: 初规划实际注入的 skill 名单（plan.skills）——T2 再规划继承同一份
        规划知识（2026-07-27 评审缺口：conditional-reminder 类「看结果再决定」的知识
        若只在初规划在场，再规划轮恰好是决策发生的地方却失忆）。
        exemplar_names: 同理的范例名单（plan.exemplars，M5 P1）——范例示范的是**落域
        形态**，再规划正是要再挑一次能力的地方，只注初规划等于范例白给。
        """
        if granted_permissions is not None:
            agents = self._filter_by_permission(agents, granted_permissions)
        catalog = _assemble_capability_catalog(agents)
        if working_set is not None:
            working_set.catalog_stats = dict(catalog.catalog_stats)
        ctx_block = working_set.render_context() if working_set is not None else ""
        sk_block = _skills.render_for_names(
            skill_names, capability_refs=catalog.pair_to_ref)
        sk_part = f"{sk_block}\n\n" if sk_block else ""   # 位置同初规划：紧跟日期锚（顺序契约）
        ex_block = _exemplars.render_for_names(
            exemplar_names, capability_refs=catalog.pair_to_ref)
        ex_part = f"{ex_block}\n\n" if ex_block else ""   # 同初规划：范例在知识之后
        prompt = (
            f"{_date_line()}\n"
            f"{sk_part}{ex_part}{ctx_block}最近观察：{json.dumps(observations, ensure_ascii=False)}\n"
            f"{catalog.semantic_mapping_text}\n\n目标：{goal}"
        )
        try:
            raw = await self._llm([
                {"role": "system", "content": _REPLAN_SYSTEM},
                {"role": "user", "content": prompt},
            ])
            data = json.loads(self._extract_json(raw))
        except Exception as exc:
            logger.warning("Replan failed: %s", exc)
            return ReplanDecision(done=True)

        parsed = self._parse_and_validate_data(data, catalog, goal)
        if bool(data.get("done")):
            return ReplanDecision(done=True)
        steps = list(parsed.steps) if parsed is not None else []
        repair_plan = Plan(steps=steps)
        effects = _skills.apply_plan_repairs(repair_plan, goal, skill_names)
        return ReplanDecision(done=not bool(steps), steps=steps, skill_effects=effects)

    def _extract_data(self, raw: str):
        """raw 文本 → dict；解析不出来返回 None。

        从 `_parse_and_validate` 里拆出来是因为**「校验后没有计划」有两种原因**，而
        `Plan | None` 这个返回类型装不下这个区别：JSON 根本没解析出来，与解析出来了、
        模型明说「不需要做任何动作」。调用方要能分开问这两件事（见 `_looks_like_no_action`）。
        """
        if not raw:
            return None
        try:
            return json.loads(self._extract_json(raw))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Plan JSON parse failed: %s", e)
            return None

    @staticmethod
    def _looks_like_no_action(data) -> bool:
        """模型说「受话了，但不该做任何动作」的形态。

        判据落在**原始 dict** 上而不是校验结果上，这一点是关键：模型规划了 3 步、
        但全被能力集校验丢掉时，`steps` 非空 —— 那是「规划错了」，绝不是「不需要动作」。
        两者的校验结果都是 `None`，只有原始数据分得开。
        """
        return (isinstance(data, dict) and data.get("addressed") is True
                and "steps" in data and type(data["steps"]) is list
                and len(data["steps"]) == 0 and not data.get("clarify"))

    def _parse_and_validate(self, raw: str, catalog: PlannerCapabilityCatalog,
                            fallback_text: str) -> Plan | None:
        data = self._extract_data(raw)
        if data is None:
            return None
        return self._parse_and_validate_data(data, catalog, fallback_text)

    def _parse_and_validate_data(self, wire, catalog: PlannerCapabilityCatalog,
                                 fallback_text: str) -> Plan | None:
        """Resolve the ref-only LLM wire, then apply the existing pair validator.

        This is the sole production seam for JSON, toolcall, salvage, retry and
        replan.  The model cannot bypass request-local authority by emitting the
        internal ``agent_id``/``intent`` representation directly.
        """
        if not isinstance(wire, dict):
            return None

        # R4.4: accepted non-addressed/clarify responses may omit ``steps``.
        # Keep these semantic shortcuts ahead of container validation, but do not
        # let an explicitly malformed ``steps`` value become a legal empty list.
        if wire.get("addressed") is False:
            if "steps" in wire:
                if type(wire["steps"]) is not list:
                    logger.warning("Plan steps is %s (not list), dropping plan for retry",
                                   type(wire["steps"]).__name__)
                    return None
                if wire["steps"]:
                    logger.warning(
                        "Plan addressed=false contradicts nonempty steps, dropping plan for retry")
                    return None
            return Plan(steps=[], raw_text=fallback_text, addressed=False)
        clarify = self._parse_clarify(wire.get("clarify"))
        if "steps" not in wire:
            if clarify:
                return Plan(steps=[], raw_text=fallback_text, clarify=clarify)
            logger.warning("Plan steps is missing, dropping plan for retry")
            return None

        raw_steps = wire["steps"]
        if not isinstance(raw_steps, list):
            logger.warning("Plan steps is %s (not list), dropping plan for retry",
                           type(raw_steps).__name__)
            return None

        resolved_steps = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                logger.warning("Plan step is %s (not object), dropping plan for retry",
                               type(raw_step).__name__)
                return None
            if set(raw_step) != set(_PLANNER_STEP_FIELDS):
                logger.warning(
                    "Plan step fields differ from the exact wire contract, dropping plan for retry")
                return None
            if "agent_id" in raw_step or "intent" in raw_step:
                logger.warning("Legacy planner capability identity rejected")
                return None
            ref = raw_step.get("capability_ref")
            if not isinstance(ref, str):
                logger.warning("Missing/non-string capability_ref in plan: %r", ref)
                return None
            pair = catalog.ref_to_pair.get(ref)
            if pair is None:
                logger.warning("Unknown capability_ref in plan: %s", ref)
                return None
            resolved = dict(raw_step)
            resolved.pop("capability_ref", None)
            resolved["agent_id"], resolved["intent"] = pair
            resolved_steps.append(resolved)

        steps = self._validated_steps(resolved_steps, catalog.agent_map)
        if not steps:
            if clarify:      # 是请求但落法歧义：无 steps 但带合法 clarify → 合法计划（P1 消费）
                return Plan(steps=[], raw_text=fallback_text, clarify=clarify)
            return None
        # steps 非空 → clarify 忽略（互斥，执行优先，母卡 D6-2>D6-3）；后续现状不动。

        # Chitchat is the open-domain fallback. Never trust an LLM-generated
        # text slot here: it can be missing or stale, which makes the agent
        # answer an empty/previous request instead of the current utterance.
        for step in steps:
            if step.agent_id == _FALLBACK_AGENT:
                step.slots["text"] = fallback_text

        complexity = wire.get("complexity", "simple")
        if complexity not in ("simple", "adaptive"):
            complexity = "simple"
        goal = str(wire.get("goal", "") or "")
        emotion = _parse_emotion(wire.get("emotion"))

        # 校验 depends_on 引用
        valid_ids = {s.id for s in steps}
        for s in steps:
            s.depends_on = [d for d in s.depends_on if d in valid_ids]

        return Plan(
            steps=steps,
            raw_text=fallback_text,
            complexity=complexity,
            goal=goal,
            emotion=emotion,
        )

    @staticmethod
    def _parse_clarify(raw) -> dict | None:
        """R4.4：解析澄清输出。非 dict / question 空 / 有效 options<2 → None；options>3 截断为 3；
        每项须 label+send_text 均为非空 str。纯函数不读 env——CLARIFY_ENABLED 由 prompt 拼接
        （生产端）与 engine 消费（消费端）两端门控，解析器只认格式（母卡实施计划 §0-10）。"""
        if not isinstance(raw, dict):
            return None
        question = raw.get("question")
        if not isinstance(question, str) or not question.strip():
            return None
        opts_raw = raw.get("options")
        if not isinstance(opts_raw, list):
            return None
        options = []
        for o in opts_raw:
            if not isinstance(o, dict):
                continue
            label, send_text = o.get("label"), o.get("send_text")
            if (isinstance(label, str) and label.strip()
                    and isinstance(send_text, str) and send_text.strip()):
                options.append({"label": label.strip(), "send_text": send_text.strip()})
        if len(options) < 2:
            return None
        return {"question": question.strip(), "options": options[:3]}

    @staticmethod
    def _unwrap_freeform_object(raw):
        """Unwrap providers' synthetic envelope for free-form JSON objects.

        Some OpenAI-compatible tool-call implementations cannot represent an
        unconstrained object directly and return ``{"$text": "{...}"}``.
        Accept only the exact one-field envelope and only when its payload is a
        JSON object; all other shapes keep the existing fail-closed behavior.
        """
        if not isinstance(raw, dict) or set(raw) != {"$text"}:
            return raw
        payload = raw.get("$text")
        if not isinstance(payload, str):
            return raw
        try:
            decoded = json.loads(payload.strip())
        except (json.JSONDecodeError, ValueError):
            return raw
        return decoded if isinstance(decoded, dict) else raw

    @staticmethod
    def _validated_steps(raw_steps: list, agent_map: dict) -> list[Step]:
        if not isinstance(raw_steps, list):
            logger.warning("Plan steps is %s (not list), dropping plan for retry",
                           type(raw_steps).__name__)
            return []
        # F4：按 agent 校验 intent（不是全局集合），防止 LLM 错配 agent/intent
        agent_intents: dict[str, set[str]] = {
            aid: {c.intent for c in a.manifest.capabilities}
            for aid, a in agent_map.items()
        }
        # intent → 拥有它的 agent 集合。用于「intent 对、agent_id 猜错」的归位（见下）。
        intent_owners: dict[str, list[str]] = {}
        for aid, intents in agent_intents.items():
            for intent in intents:
                intent_owners.setdefault(intent, []).append(aid)

        steps = []
        invalid = False
        for s in raw_steps:
            # 外层是 list 不代表元素就是 JSON object。真栈曾返回合法 step 后混一条
            # 字符串；直接 `.get()` 会把整个请求/跑批打死。与 slots/depends_on 的防御
            # 同一原则：一直校验到真正要消费的元素，畸形一步触发整份计划原子拒绝。
            if not isinstance(s, dict):
                logger.warning("Plan step is %s (not object), dropping plan for retry: %r",
                               type(s).__name__, s)
                invalid = True
                continue
            aid = s.get("agent_id", "")
            intent = s.get("intent", "")

            # 校验 agent_id
            if aid not in agent_map:
                logger.warning("Unknown agent_id in plan: %s, skipping", aid)
                invalid = True
                continue

            # F4：intent 必须属于该 agent 的能力集。
            # 但**「丢一步」和「幻觉一步」对用户是同一件事**——都没做成。对抗测试实测：
            # 「调高音量」计划是对的 `volume.inc`，只是被派给了 edge-media（它属 edge-vehicle），
            # 整步被静默丢掉、计划退化成 chitchat.talk（findings §2 簇 F）。
            # 判据：**intent 在全清单里唯一归属时归位，否则仍然丢**——
            # 归位不发明能力（intent 必须真实存在于某个已注册且已过权限过滤的 agent），
            # 归属有歧义（≥2 家）时不猜，能力集校验挡幻觉的作用一分不减。
            if intent not in agent_intents.get(aid, set()):
                owners = intent_owners.get(intent, [])
                if len(owners) == 1:
                    logger.info("Intent %s misattributed to %s, re-homing to %s",
                                intent, aid, owners[0])
                    aid = owners[0]
                else:
                    logger.warning(
                        "Intent %s not in agent %s capabilities (owners=%s), dropping step",
                        intent, aid, owners or "none")
                    invalid = True
                    continue

            # slots 是唯一「宽进」的 LLM 输出通道——但宽进不等于不设防：模型偶发输出
            # list（如 ["item>拿铁"]）时 .items() 直接 AttributeError 崩掉整个 Handle
            # （验收真栈抓到：空响应、确认挂起蒸发）。非 dict 按无效步走原子拒绝→重试。
            raw_slots = PlanBuilder._unwrap_freeform_object(
                s.get("slots") or {},
            )
            if not isinstance(raw_slots, dict):
                logger.warning("Step slots is %s (not dict), dropping plan for retry: %r",
                               type(raw_slots).__name__, raw_slots)
                invalid = True
                continue

            manifest = agent_map[aid].manifest
            step = Step(
                id=s.get("id", f"s{len(steps)+1}"),
                agent_id=aid,
                endpoint=agent_map[aid].endpoint,
                kind=getattr(manifest, "kind", "") or "agent",
                deployment=getattr(manifest, "deployment", "") or "cloud",
                intent=intent,
                slots={k: str(v) for k, v in raw_slots.items()},
                # 同族防御：模型会把这两个字段输出成 ""（真栈日志实证）。depends_on 非
                # list 会被逐字符迭代、slot_refs 非 dict 在 executor._resolve_slot_refs
                # 处 .items() 同款崩——都归一为空（依赖丢失顶多退化为顺序执行）。
                #
                # **元素层也要归一，不只是容器层**（2026-08-03 真栈实证）：`[["s0"]]`
                # 是 list、isinstance 照过，直到下面 `dep in valid_ids` 拿 list 去 hash
                # 才崩 TypeError，而 `_parse_and_validate_data` 在 build() 里没有异常
                # 防护——一次畸形模型输出就把整条规划抛出去（一趟 140 选集的 L1 跑批
                # 被它整趟打死）。防御要一路防到**真正会被拿去 hash 的那个值**。
                # 非 str 元素直接丢而不是 str() 转换：id 本身是 str，转出来的
                # `"['s0']"` 匹配不上任何步骤，却会在日志里留下一个不存在的 id。
                depends_on=[d for d in (s.get("depends_on") or [])
                            if isinstance(d, str)]
                if isinstance(s.get("depends_on"), list) else [],
                # 同族第二处，而且崩在**执行期**不是规划期：`executor._resolve_ref`
                # 对 ref_path 做 `.split(".")`，非 str value 直接 AttributeError。
                # JSON object 的 key 恒为 str，可变的是 value——所以防的是 value。
                slot_refs=(
                    {k: v for k, v in normalized_slot_refs.items()
                     if isinstance(v, str)}
                    if isinstance(
                        normalized_slot_refs := PlanBuilder._unwrap_freeform_object(
                            s.get("slot_refs") or {},
                        ),
                        dict,
                    )
                    else {}
                ),
                latency_budget_ms=int(manifest.latency_budget_ms or 5000),
                required_permissions=list(
                    getattr(manifest, "requires_permissions", []) or []),
                trust_level=getattr(manifest, "trust_level", "") or "",
                context_scopes=list(getattr(manifest, "context_scopes", []) or []),
                heavy=next((bool(getattr(c, "heavy", False))
                            for c in manifest.capabilities if c.intent == intent), False),
                # M0a-3：确认权威=capability manifest。LLM 计划输出的 require_confirm 一律
                # 不读——不可降级也不可升级（升级权在 Agent/action/VAL 硬层）；Agent 漏标由
                # executor._enforce_capability_confirm 兜底（契约 test_capability_confirm）。
                require_confirm=next(
                    (bool(getattr(c, "require_confirm", False))
                     for c in manifest.capabilities if c.intent == intent), False),
                # M2 Verifier：执行后对账期望同样只从 capability 读（LLM 字段不读——
                # 「验不验、验什么」不是模型的决定权，与 require_confirm 同一条权威链）。
                verification=next(
                    (_verification_dict(c)
                     for c in manifest.capabilities if c.intent == intent), {}),
            )
            steps.append(step)

        # Plans are atomic: executing only the valid remainder silently drops
        # user intents and can falsely report completion. Reject the whole plan
        # so the caller retries or falls back with the original utterance.
        if invalid:
            return []

        valid_ids = {step.id for step in steps}
        for step in steps:
            step.depends_on = [dep for dep in step.depends_on if dep in valid_ids]
        PlanBuilder._derive_depends_on_from_refs(steps, valid_ids)
        return steps

    # 引用了另一步的输出，**就是依赖的定义**。模型经常把引用写全、却把 `depends_on`
    # 留空（真栈实测：`nearby.detail` 的 `slot_refs={"poi_id": "s1.data.items.0.id"}`
    # 而 `depends_on: []`）。那不是「路由错」，是**计划自相矛盾**——执行侧
    # `_topo_layers` 只看 `depends_on`，两步会被排进同一层并行下发；等 s2 去取 s1 的
    # 结果时 s1 还没回来，`_resolve_slot_refs` 解析成 None，字面量
    # `s1.data.items.0.id` 就当成真 POI id 发给了下游。
    #
    # 归一放在这里，与「`depends_on` 非 list 归空」「intent 唯一归属时归位」同一族：
    # 把模型输出里**自相矛盾**的部分补成一致，不发明任何路由——被引用的步必须真实
    # 存在于本计划（`valid_ids`），自引用不补（那是环不是依赖）。
    _REF_HEAD_RE = re.compile(r"^\$?\{?\s*([A-Za-z_][A-Za-z0-9_]*)\.data\.")

    @staticmethod
    def _derive_depends_on_from_refs(steps: list, valid_ids: set) -> None:
        for step in steps:
            derived = []
            for raw in list(step.slot_refs.values()) + list(step.slots.values()):
                if not isinstance(raw, str):
                    continue
                m = PlanBuilder._REF_HEAD_RE.match(raw.strip())
                if not m:
                    continue
                producer = m.group(1)
                if (producer in valid_ids and producer != step.id
                        and producer not in step.depends_on
                        and producer not in derived):
                    derived.append(producer)
            if derived:
                logger.info("Step %s references %s but declared depends_on=%s; "
                            "deriving the missing edge(s)",
                            step.id, derived, step.depends_on)
                step.depends_on = list(step.depends_on) + derived

    def _talk_only_plan(self, text: str, agents: list = None) -> Plan | None:
        """把原话交给全局兜底 Agent（默认 chitchat）：**只回一句话，不做任何写操作。**

        单独成方法是为了让两个**语义完全不同**的调用方各有各的名字：
        `_fallback` 是「我们失败了」，`build()` 的空动作分支是「模型判断不该做事」。
        两者产物相同（一条 `chitchat.talk`），但记成同一件事会让观测说谎——
        实测代价见 `build()` 里那段注释。找不到兜底 Agent 时返回 None，由调用方决定。
        """
        for a in (agents or []):
            if a.manifest.agent_id == _FALLBACK_AGENT:
                intent = a.manifest.capabilities[0].intent if a.manifest.capabilities else "chitchat.talk"
                return Plan(steps=[Step(
                    id="s1", agent_id=a.manifest.agent_id, endpoint=a.endpoint,
                    kind=getattr(a.manifest, "kind", "") or "agent",
                    deployment=getattr(a.manifest, "deployment", "") or "cloud",
                    intent=intent, slots={"text": text},
                    required_permissions=list(
                        getattr(a.manifest, "requires_permissions", []) or []),
                    trust_level=getattr(a.manifest, "trust_level", "") or "",
                )], raw_text=text)
        return None

    async def _fallback(self, text: str, agents: list = None) -> Plan:
        """规划失败的降级。优先兜底到全局兜底 Agent（env PLANNER_FALLBACK_AGENT，默认
        chitchat；开放域/LLM 抽风时仍有回应），其次 Registry 语义路由 top-1。"""
        # 1) 全局兜底 Agent：把原话交给它（已在权限过滤后的 agents 里）
        talk = self._talk_only_plan(text, agents)
        if talk is not None:
            return talk

        # 2) Registry 语义路由 top-1
        try:
            resolved = await self._resolve(text, top_k=1)
            if not resolved:
                return Plan(steps=[])
            a = resolved[0]
            # R4.4 D5-2：低分不再硬执行 capabilities[0]（堵「score 0.36 也硬套首个能力」真 bug）。
            # 门槛与 SEMANTIC_PROMOTE_SIM 对齐（精确 intent=1.0/关键词 0.3+/语义重排=真 cosine）。
            # 分数不足 → 诚实降级空计划（engine 出「没听清」话术），不臆断。chitchat 全局兜底
            # （上面第 1 优先分支）不受影响——门槛只作用于「chitchat 不在 catalog、走语义 top-1」路径。
            if (float(getattr(a, "score", 0.0) or 0.0)
                    < float(os.getenv("CLARIFY_FALLBACK_MIN", "0.5"))):
                logger.info("Fallback top-1 score %.3f below threshold, honest degrade",
                            float(getattr(a, "score", 0.0) or 0.0))
                return Plan(steps=[])
            intent = a.manifest.capabilities[0].intent if a.manifest.capabilities else ""
            return Plan(steps=[Step(
                id="s1", agent_id=a.manifest.agent_id, endpoint=a.endpoint,
                kind=getattr(a.manifest, "kind", "") or "agent",
                deployment=getattr(a.manifest, "deployment", "") or "cloud",
                intent=intent, slots={},
                required_permissions=list(
                    getattr(a.manifest, "requires_permissions", []) or []),
                trust_level=getattr(a.manifest, "trust_level", "") or "",
            )])
        except Exception as e:
            logger.error("Fallback routing failed: %s", e)
            return Plan(steps=[])

    @staticmethod
    def _extract_json(s: str) -> str:
        i, j = s.find("{"), s.rfind("}")
        return s[i:j + 1] if i >= 0 and j > i else s

    # 上下文/能力清单渲染已迁入 context.py 的 WorkingSet（统一字符预算）。

    @staticmethod
    def _build_intent_set(agents: list) -> set:
        intents = set()
        for a in agents:
            for c in a.manifest.capabilities:
                intents.add(c.intent)
        return intents

    @staticmethod
    def _filter_by_permission(agents: list, granted: list[str]) -> list:
        """过滤掉用户无权调用的 Agent（越权能力不暴露给 LLM）。

        判定委托运行时唯一决策 `security.permission.check_permission`（与 dispatch 执行期同源）：
        - granted 为 None → 不过滤（权限系统未启用，PoC 兼容）
        - granted 为空列表 → 只放行无权限要求的 Agent（零授权 = 最小权限）
        - Agent 的 requires_permissions 被 granted（父子覆盖）全覆盖 → 保留
        - third_party Agent 的 vehicle.control 无论 granted 都被拒绝
        """
        if granted is None:
            return agents
        filtered = []
        for a in agents:
            m = a.manifest
            d = check_permission(
                agent_id=m.agent_id, trust_level=m.trust_level,
                required=list(m.requires_permissions), granted=granted, kind="agent")
            if not d.allowed:
                logger.debug("Filtered %s: %s", m.agent_id, d.reason)
                continue
            filtered.append(a)
        return filtered
