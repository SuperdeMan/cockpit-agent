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
    "另严禁抽取：一次性指令、未确认的地址、精确坐标/经纬度、车内音视频内容。只输出 JSON，不要解释。"
)


def _now() -> int:
    return int(time.time())


async def _default_complete(messages: list[dict]) -> str:
    """默认经 gRPC 调 llm-gateway。失败抛异常由上层吞。"""
    import grpc
    from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
    addr = os.getenv("LLM_GATEWAY_ADDR", "llm-gateway:50052")
    async with grpc.aio.insecure_channel(addr) as ch:
        stub = llm_pb2_grpc.LLMGatewayStub(ch)
        req = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role=m["role"], content=m["content"]) for m in messages],
            temperature=0.2, max_tokens=512)
        resp = await stub.Complete(req, timeout=20)
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
    predicate = (c.get("predicate") or "").strip()
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
    convo = "\n".join(f'{t.get("role","user")}: {t.get("text","")}'
                      for t in turns[-_CONSOLIDATE_LOOKBACK:] if t.get("text"))
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
        item = _govern(c, user_id=user_id, occupant_id=occupant_id,
                       vehicle_id=vehicle_id, session_id=session_id)
        if item:
            out.append(item)
    return out
