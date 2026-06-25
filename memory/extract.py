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

_SYSTEM = (
    "你是车载助手的记忆抽取器。只从对话中抽取【稳定的用户偏好】或【显著事件】，"
    "输出 JSON 数组，无可抽取则输出 []。每个元素字段："
    '{"category":"explicit_preference|temporary_preference|inferred_preference|sensitive_fact|episodic",'
    '"kind":"semantic|episodic","predicate":"如 taste.spicy/route.avoid_highway（情景留空）",'
    '"text":"自然语言陈述","scope":"如 profile.taste","confidence":0.0~1.0}。'
    "严禁抽取：一次性指令、未确认的地址、精确坐标/经纬度、车内音视频内容、"
    "第三方隐私、可能引发歧视的敏感画像。只输出 JSON，不要解释。"
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
    # 黑名单：精确坐标 → 丢弃（任何类别）
    if _has_coords(text):
        logger.debug("extract drop (coords): %s", text[:40])
        return None
    # 敏感事实默认不自动写（家/公司/孩子姓名/联系人等须用户显式设置）
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
