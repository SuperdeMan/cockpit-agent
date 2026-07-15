"""记忆抽取管线（P1）：对话 → llm-gateway 抽取稳定偏好/显著事件 → 治理 → 候选记忆。

评审治理（设计稿 §7）：
- 四分类写策略：explicit / temporary(带 expires_at) / inferred(低置信) / sensitive_fact(默认不写)。
- 抽取黑名单：一次性命令、未确认地址、精确坐标、车内音视频、第三方隐私、敏感画像 → 丢弃。

memory 服务自己拥有抽取（"上下文唯一真相源"），经 llm-gateway（唯一 LLM 出口）。
`complete_fn` 可注入（async (messages:list[dict])->str），便于单测不连真实 LLM。
"""
from __future__ import annotations
import json
import logging
import os
import re
import time

logger = logging.getLogger("memory.extract")

_CONSOLIDATE_LOOKBACK = 12         # 抽取回看轮数
_TEMP_TTL = 12 * 3600              # 临时偏好默认有效期（秒）
_INFERRED_MAX_CONF = 0.5          # 推断类置信上限
# 精确坐标启发式：4+ 位小数的十进制数（地理坐标特征），或显式经纬度词
_COORD_RE = re.compile(r"\d{1,3}\.\d{4,}")
_COORD_WORDS = ("经度", "纬度", "lat", "lng", "latitude", "longitude", "坐标")
# 7+ 连续数字 ≈ 电话/证件号等可识别隐私（年份仅 4 位，不误伤）
_PII_RE = re.compile(r"\d{7,}")

# ── 场景配置参数黑名单（旅程 B3-3 M1）─────────────────────────
# 「创建钓鱼模式：空调22度」的 22 度是**场景配置**，被 LLM 抽成「用户最喜欢 22 度」
# 会污染个人偏好。确定性判据：偏好类候选的参数锚点（数字/颜色）若只能溯源到
# 「模式/场景」语境的用户话轮（且该话轮无「记住/我喜欢」偏好口吻），即场景配置→丢弃。
_SCENE_WORD_RE = re.compile(r"模式|场景")
_PREF_STATE_RE = re.compile(r"记住|记好|别忘了|我(最|比较|还是)?(喜欢|习惯|偏好)")
_ANCHOR_RE = re.compile(r"\d+(?:\.\d+)?|[红橙黄绿青蓝紫粉白金棕灰]色?")
_PREF_CATEGORIES = {"explicit_preference", "temporary_preference", "inferred_preference"}

# ── 常用车控偏好 predicate 归一（旅程 B3-3 M2）───────────────────
# LLM 每次自由造词（hvac.temperature / climate.temp / ac.temperature…）导致
# current_by_predicate 精确匹配失手 → 新偏好插入却 supersede 不到旧值，新旧并存。
# 归一到 canonical + 已知别名类，写入与冲突查找都按同一口径。
_PRED_CANON: dict[str, tuple[str, ...]] = {
    "climate.temperature": (
        "hvac.temperature", "hvac.temp", "climate.temp", "ac.temperature",
        "ac.temp", "aircon.temperature", "hvac.temperature_preference",
        "climate.preferred_temperature", "temperature.preference",
        "comfort.temperature"),
    "media.volume": (
        "audio.volume", "media.volume_preference", "volume.preference",
        "sound.volume"),
    "light.ambient_color": (
        "light.color", "ambient.color", "ambient_light.color",
        "atmosphere.color", "light.ambient"),
    "seat.heating": ("seat.heat", "seat.heating_preference", "seat.warmer"),
}
_PRED_ALIAS = {a: canon for canon, aliases in _PRED_CANON.items() for a in aliases}


def normalize_predicate(pred: str) -> str:
    """已知别名 → canonical；未知原样返回。"""
    p = (pred or "").strip()
    return _PRED_ALIAS.get(p, p)


def predicate_class(pred: str) -> tuple[str, ...]:
    """谓词等价类（canonical + 全部别名），供巩固时的冲突 supersede 查找。"""
    canon = normalize_predicate(pred)
    return (canon, *_PRED_CANON.get(canon, ()))


def _scene_config_only(cand_text: str, turns: list[dict]) -> bool:
    """候选偏好的参数锚点是否只能溯源到场景/模式语境的用户话轮（→场景配置，丢弃）。"""
    anchors = _ANCHOR_RE.findall(cand_text or "")
    if not anchors:
        return False
    scene_turns, other_turns = [], []
    for t in turns:
        txt = t.get("text") or ""
        if t.get("role") != "user" or not txt:
            continue
        if _SCENE_WORD_RE.search(txt) and not _PREF_STATE_RE.search(txt):
            scene_turns.append(txt)
        else:
            other_turns.append(txt)
    hit_scene = any(a in s for a in anchors for s in scene_turns)
    hit_other = any(a in o for a in anchors for o in other_turns)
    return hit_scene and not hit_other

_SYSTEM = (
    "你是车载助手的记忆抽取器。从对话中抽取三类：【稳定的用户偏好】、【显著事件】，"
    "以及【用户主动告知、希望被记住的个人实体】（本人称呼/昵称、宠物名、家人成员的称呼）。"
    "输出 JSON 数组，无可抽取则输出 []。每个元素字段："
    '{"category":"explicit_preference|temporary_preference|inferred_preference|personal_fact|sensitive_fact|episodic",'
    '"kind":"semantic|episodic","predicate":"如 taste.spicy/route.avoid_highway/person.pet（情景留空）",'
    '"text":"自然语言陈述","scope":"如 profile.taste / profile.person","confidence":0.0~1.0}。'
    "personal_fact：用户**主动告知**的个人称呼/宠物/家人实体（如『我的宠物叫旺财』『我儿子叫小明』），"
    "predicate 用 person.pet/person.child/person.self 等，scope=profile.person。"
    "归为 sensitive_fact（将被丢弃）或干脆不抽：健康/种族/宗教/政治等特殊敏感画像、"
    "电话/证件号/精确住址等可识别隐私、第三方隐私、Agent 推断而非用户明说的敏感信息。"
    "另严禁抽取：一次性指令、未确认的地址、精确坐标/经纬度、车内音视频内容；"
    "以及**场景/模式配置里的参数**——『创建/修改/开启XX模式：空调22度、氛围灯蓝色』"
    "这类话里的 22 度/蓝色是该场景的配置，不是用户偏好（用户明说『记住/我最喜欢』的才是）。"
    "常用车控偏好的 predicate 统一用：climate.temperature（空调温度）、media.volume（音量）、"
    "light.ambient_color（氛围灯颜色）、seat.heating（座椅加热）。只输出 JSON，不要解释。"
)


def _now() -> int:
    return int(time.time())


def _build_complete_request(messages: list[dict]):
    """构造抽取用 CompleteRequest。caller_service 让 obs.llm 把这笔消耗记到记忆抽取
    头上——抽取是后台自发调用（无请求级 trace），此前 caller 为空 = 消耗归属盲区
    （2026-07-13 排查）。刻意不用 "caller"（那是网关限流桶键，惯例同 planner/SDK）。"""
    from cockpit.llm.v1 import llm_pb2
    req = llm_pb2.CompleteRequest(
        messages=[llm_pb2.Message(role=m["role"], content=m["content"]) for m in messages],
        temperature=0.2, max_tokens=512)
    req.meta["caller_service"] = "memory-extract"
    return req


async def _default_complete(messages: list[dict]) -> str:
    """默认经 gRPC 调 llm-gateway。失败抛异常由上层吞。"""
    from cockpit.llm.v1 import llm_pb2_grpc
    from runtime.grpcio import aio_channel
    addr = os.getenv("LLM_GATEWAY_ADDR", "llm-gateway:50052")
    async with aio_channel(addr) as ch:
        stub = llm_pb2_grpc.LLMGatewayStub(ch)
        resp = await stub.Complete(_build_complete_request(messages), timeout=20)
        return resp.content


def _has_coords(text: str) -> bool:
    if _COORD_RE.search(text or ""):
        return True
    low = (text or "").lower()
    return any(w in low for w in _COORD_WORDS)


def _govern(c: dict, *, user_id: str, occupant_id: str, vehicle_id: str,
            session_id: str) -> dict | None:
    """把一条 LLM 候选治理成可入库的 MemoryItem dict；不合规返回 None（丢弃）。"""
    category = (c.get("category") or "").strip()
    text = (c.get("text") or "").strip()
    if not text:
        return None
    # 黑名单：精确坐标 / 电话证件号等可识别隐私 → 丢弃（任何类别）
    if _has_coords(text) or _PII_RE.search(text):
        logger.debug("extract drop (coords/pii): %s", text[:40])
        return None
    # 真正敏感画像（健康/种族/宗教/电话证件/Agent 推断的隐私）→ 丢弃。
    # 注：用户主动告知、想被记住的个人实体（宠物/家人称呼）走 personal_fact 而非 sensitive_fact。
    if category == "sensitive_fact":
        logger.debug("extract drop (sensitive_fact): %s", text[:40])
        return None

    kind = c.get("kind") or ("episodic" if category == "episodic" else "semantic")
    predicate = normalize_predicate(c.get("predicate") or "")   # M2：别名归一到 canonical
    scope = (c.get("scope") or "").strip()
    try:
        conf = float(c.get("confidence", 0.6))
    except (TypeError, ValueError):
        conf = 0.6

    item = {
        "user_id": user_id, "occupant_id": occupant_id or "primary",
        "vehicle_id": vehicle_id, "kind": kind, "predicate": predicate,
        "text": text, "scope": scope, "review_status": "auto_extracted",
        "source_session": session_id, "source_ts": _now(), "valid_from": _now(),
    }
    if category == "explicit_preference":
        item.update(provenance="user_stated", confidence=max(conf, 0.7))
    elif category == "personal_fact":
        # 用户主动告知的个人实体（宠物/家人称呼）：存为 profile.person，标 sensitive。
        # sensitive（非 highly_sensitive）→ 可被泛化召回（"我宠物叫啥"答得上），但记忆页可删。
        item.update(provenance="user_stated", confidence=max(conf, 0.8),
                    scope=scope or "profile.person", privacy_level="sensitive")
    elif category == "temporary_preference":
        item.update(provenance="user_stated", confidence=max(conf, 0.6),
                    expires_at=_now() + _TEMP_TTL)
    elif category == "inferred_preference":
        item.update(provenance="agent_inferred", confidence=min(conf, _INFERRED_MAX_CONF))
    elif category == "episodic":
        item.update(provenance="agent_inferred", confidence=conf, kind="episodic",
                    scope=scope or "episodic.general")
    else:
        # 未知类别：保守按推断处理（低置信）
        item.update(provenance="agent_inferred", confidence=min(conf, _INFERRED_MAX_CONF))
    return item


def _parse(text: str) -> list[dict]:
    """从 LLM 输出中解析 JSON 数组（容忍 ```json 包裹与前后噪声）。"""
    if not text:
        return []
    s = text.strip()
    if "```" in s:  # 去围栏
        s = re.sub(r"```(?:json)?", "", s).strip()
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(s[start:end + 1])
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


async def extract(turns: list[dict], *, user_id: str, occupant_id: str = "primary",
                  vehicle_id: str = "", session_id: str = "", complete_fn=None
                  ) -> list[dict]:
    """从最近对话抽取治理后的候选记忆。LLM 不可用/解析失败 → []（静默，不阻塞）。"""
    if not user_id or not turns:
        return []
    window = [t for t in turns[-_CONSOLIDATE_LOOKBACK:] if t.get("text")]
    convo = "\n".join(f'{t.get("role","user")}: {t.get("text","")}' for t in window)
    if not convo.strip():
        return []
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"对话：\n{convo}\n\n抽取 JSON："}]
    try:
        raw = await (complete_fn or _default_complete)(messages)
    except Exception as e:
        logger.debug("extract LLM unavailable: %s", e)
        return []
    out = []
    for c in _parse(raw):
        if not isinstance(c, dict):
            continue
        # M1 黑名单（确定性，不信 prompt）：场景配置参数不是个人偏好
        if (c.get("category") in _PREF_CATEGORIES
                and _scene_config_only(c.get("text") or "", window)):
            logger.debug("extract drop (scene config): %s", (c.get("text") or "")[:40])
            continue
        item = _govern(c, user_id=user_id, occupant_id=occupant_id,
                       vehicle_id=vehicle_id, session_id=session_id)
        if item:
            out.append(item)
    return out
