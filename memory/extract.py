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

import relation

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
    # G6（EVA 二轮）路线偏好族：此前 route.avoid_highway 只在本 prompt 举例里出现过、
    # 全仓零消费方；现在 navigation 按 route.* 前缀确定性消费（G11 strategy 面），
    # 归一防 LLM 自由造词让消费方够不着。
    "route.avoid_highway": ("route.no_highway", "navigation.avoid_highway",
                            "route.highway_avoid", "route.highway"),
    "route.avoid_congestion": ("route.no_congestion", "navigation.avoid_congestion",
                               "route.avoid_jam", "route.congestion"),
    "route.avoid_toll": ("route.no_toll", "navigation.avoid_toll", "route.toll"),
    "route.preferred_road": ("route.road_preference", "route.prefer_road",
                             "navigation.preferred_road", "route.preference"),
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
    "light.ambient_color（氛围灯颜色）、seat.heating（座椅加热）；"
    "路线偏好统一用：route.avoid_highway（不走高速）、route.avoid_congestion（避堵）、"
    "route.avoid_toll（少收费）、route.preferred_road（固定走某条路，text 里写清路名）。"
    "偏好/事实类可选字段："
    '"subject"=这条内容是关于谁的——用户本人**留空**；关于家人填亲属称谓'
    "（爸爸/妈妈/老婆/老公/女儿/儿子/孩子，如『我爸不喜欢空调太冷』subject=爸爸）；"
    '"polarity"="like"（喜欢）或"dislike"（不喜欢/嫌弃，如『这家咖啡太酸了』），非偏好留空。'
    "**店铺级差评要把具体店名写进 text**：用户说『第一家/这家太酸』时，店名以对话中"
    "助手确认的为准（如助手答『以后不推荐三立方』则 text=『用户不喜欢三立方的咖啡（太酸）』，"
    "predicate=taste.coffee, polarity=dislike）——丢掉店名的差评没法用于降权该店。"
    "episodic 事件若用户明说了**未来**的时间——明确日期（『下个月15号』）或星期"
    "（『下周五』『周六下午三点』），带不带具体时刻都算——按当前日期换算后"
    '额外给 "event_time"（ISO 8601，如 2026-08-22T15:00:00，只有日期没有时刻用 00:00:00）；'
    "没有明确未来时间的不要编造。"
    # M2 P1 关系边：与偏好共用同一个 JSON 数组（不改输出结构），靠 category 分流。
    "另可抽取【实体关系】：category=\"relation\"，额外给 "
    '{"subject":"实体名","rel":"关系","object":"对象"}。'
    "rel 只能是：family（亲属，如 小雨-family-女儿）、place_of（常去地点，如 小雨-place_of-XX小学）、"
    "works_at、lives_at、owns（车辆等）、prefers_brand。**不在这六个里的关系不要抽**。"
    "只有用户明说的才抽（『我女儿叫小雨』『小雨在XX小学上学』），不要从上下文推断亲属关系。"
    "family 边方向固定：subject 必须是家人成员的名字，object 必须是用户说出的亲属称谓；"
    "绝不能把 object 写成用户/我。例如『我女儿叫小雨，她在阳光小学上学』必须同时输出"
    "小雨-family-女儿与小雨-place_of-阳光小学两条 relation。"
    "一句话有多个明说关系时逐条完整输出。"
    "只输出 JSON，不要解释。"
)


def _now() -> int:
    return int(time.time())


def _date_line() -> str:
    """抽取 prompt 的日期锚（planning.py::_date_line 同款判据）：相对时间词必须有
    权威基准可换算。G7 真栈补验（2026-08-14）实锤：无锚时「下周五提车」8 次采样
    0 次给出 event_time，「下个月15号」全靠 planner 回复轮凑巧复述了换算——
    锚+措辞修后两种形态各 4/4 且日期正确。"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    wd = "一二三四五六日"[now.weekday()]
    return f"当前日期：{now.year}年{now.month}月{now.day}日 周{wd}（今年={now.year}年）"


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
            session_id: str, source_turn_ids: str = "") -> dict | None:
    """把一条 LLM 候选治理成可入库的 MemoryItem dict；不合规返回 None（丢弃）。"""
    category = (c.get("category") or "").strip()
    # M2 P1 关系边：**必须在 text 空检查之前分流**——关系候选是 (subject, rel, object)
    # 三元组、本来就没有 text 字段，放在后面会被「text 为空即丢弃」提前吃掉（实测踩到）。
    # 黑名单同样生效：拿 subject+object 过一遍坐标/PII 检查（家庭住址不该进关系图）。
    if category == "relation":
        probe = f'{c.get("subject") or ""} {c.get("object") or ""}'
        if _has_coords(probe) or _PII_RE.search(probe):
            logger.debug("extract drop (relation coords/pii): %s", probe[:40])
            return None
        edge = relation.normalize_candidate(
            dict(c, source_turn_ids=source_turn_ids or session_id))
        if not edge:      # 词表外 / 残缺 → 丢弃，绝不猜
            logger.debug("extract drop (bad relation): %s", str(c)[:60])
            return None
        return {"_relation": edge}

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
        # 真实证据轮次（M-B）。此前这里是空的、关系边填的是 session_id——于是
        # `weighting.evidence_count` 永远数出 1，「说过一次 vs 每周三次」分不出来。
        "source_turn_ids": source_turn_ids,
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

    # ── G6（EVA 二轮）：关于谁 + 偏好极性 ─────────────────────────────
    # subject 经亲属称谓归一（爸→爸爸）；「用户/我/本人」即本人 → 归空；polarity 闭集。
    subj = relation.normalize_kinship(str(c.get("subject") or "").strip())
    if subj in ("用户", "我", "本人", "自己"):
        subj = ""
    if subj and len(subj) <= 20:
        item["subject"] = subj
    pol = str(c.get("polarity") or "").strip().lower()
    if pol in ("like", "dislike"):
        item["polarity"] = pol
    # ── G7：未来事件时刻（确定性校验：可解析、在未来、90 天内；不合格丢字段不猜）──
    ev = str(c.get("event_time") or "").strip()
    if item.get("kind") == "episodic" and ev:
        ts = _parse_event_time(ev)
        if ts and _now() < ts <= _now() + 90 * 86400:
            item["value_json"] = json.dumps(
                {"event_time": ts, "event_time_iso": ev}, ensure_ascii=False)
    return item


def _parse_event_time(iso: str) -> int | None:
    """ISO 8601 → epoch 秒；解析不出返回 None。无时区按车机默认 UTC+8。"""
    from datetime import datetime, timezone, timedelta
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return int(dt.timestamp())


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


# ── P4（EVA 遗留卡）：散句 relation 确定性前置抽取 ─────────────────────────
# 「我老婆平时在深圳湾万象城上班」经 LLM 抽取常落成 semantic 偏好而非 relation 边
# （2026-08-15 真栈实测）——而人称→地点是关系图谱唯一非做不可的消费面（「去接老婆」
# 靠它）。两族句式确定性抽取，与 LLM 候选合流去重；**LLM 挂了确定性候选照常返回**。
# 人称词表与 relation._KINSHIP_SYNONYMS 同源（消费侧 kinship_aliases 认得的才抽）。
_KIN_WORDS_ALT = "|".join(sorted(
    {w for words in relation._KINSHIP_SYNONYMS.values() for w in words}
    | set(relation._KINSHIP_SYNONYMS), key=len, reverse=True))
# 「我(的){人称}叫{名}」「{人称}的名字是{名}」→ family 边（subject=名）
_REL_NAME_RE = re.compile(
    rf"我的?({_KIN_WORDS_ALT})(?:的名字)?(?:叫|名字是)([一-鿿A-Za-z]{{2,8}})")
# 「(我的){人称}(平时|一般)?(都)?在{地点}{上班|上学|…}」→ family+place 两条边
# （无名时以称谓作实体名——resolve_person_place 的 family 查询按称谓命中）。
# 地点段 lazy + 动词 lookahead 兜边界（中文捕获组边界只能靠 lookahead，§36 教训）。
_REL_PLACE_RE = re.compile(
    rf"我的?({_KIN_WORDS_ALT})(?:平时|一般)?(?:都)?在"
    rf"([一-鿿0-9A-Za-z]{{2,16}}?)(上班|上学|读书|念书|工作|上幼儿园)")
_REL_PLACE_VERB_TO_REL = {
    "上班": relation.REL_WORKS_AT, "工作": relation.REL_WORKS_AT,
    "上学": relation.REL_PLACE_OF, "读书": relation.REL_PLACE_OF,
    "念书": relation.REL_PLACE_OF, "上幼儿园": relation.REL_PLACE_OF,
}


def deterministic_relations(window: list[dict]) -> list[dict]:
    """用户轮里的两族关系句式 → relation 候选（与 LLM 候选同形状，_govern 统一治理）。"""
    out: list[dict] = []
    seen: set[tuple] = set()

    def _add(subject: str, rel: str, obj: str) -> None:
        key = (subject, rel, obj)
        if subject and obj and key not in seen:
            seen.add(key)
            out.append({"category": "relation", "subject": subject, "rel": rel,
                        "object": obj, "confidence": 0.95,
                        "provenance": "user_stated"})

    for t in window:
        if (t.get("role") or "user") != "user":
            continue
        text = t.get("text") or ""
        for m in _REL_NAME_RE.finditer(text):
            kin, name = m.group(1), m.group(2).strip()
            _add(name, relation.REL_FAMILY, relation.normalize_kinship(kin))
        for m in _REL_PLACE_RE.finditer(text):
            kin, place, verb = m.group(1), m.group(2).strip(), m.group(3)
            canon = relation.normalize_kinship(kin)
            # 无名实体以称谓自身作实体名：family(称谓→称谓) + place(称谓→地点)，
            # resolve_person_place 按称谓命中 persons=[称谓] → 地点，一跳可达。
            _add(canon, relation.REL_FAMILY, canon)
            _add(canon, _REL_PLACE_VERB_TO_REL.get(verb, relation.REL_PLACE_OF),
                 place)
    return out


async def extract(turns: list[dict], *, user_id: str, occupant_id: str = "primary",
                  vehicle_id: str = "", session_id: str = "", complete_fn=None
                  ) -> list[dict]:
    """从最近对话抽取治理后的候选记忆。LLM 不可用/解析失败 → 确定性候选仍返回。"""
    if not user_id or not turns:
        return []
    window = [t for t in turns[-_CONSOLIDATE_LOOKBACK:] if t.get("text")]
    # 证据轮次用真实 turn id。**owner 过滤是调用方的责任**（consolidate 先按 OwnerKey
    # 取窗口再进来）——归属判定不能交给 LLM，它看到的只是一段文本。
    turn_ids = ",".join(t["turn_id"] for t in window if t.get("turn_id"))
    convo = "\n".join(f'{t.get("role","user")}: {t.get("text","")}' for t in window)
    if not convo.strip():
        return []
    det_candidates = deterministic_relations(window)
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user",
                 "content": f"{_date_line()}\n对话：\n{convo}\n\n抽取 JSON："}]
    try:
        raw = await (complete_fn or _default_complete)(messages)
    except Exception as e:
        logger.debug("extract LLM unavailable: %s", e)
        raw = ""          # P4：LLM 挂了确定性关系候选照常走治理入库
    # 合流去重：确定性候选在前（置信更高），LLM 撞车的 (subject,rel,object) 丢弃。
    det_keys = {(c["subject"], c["rel"], c["object"]) for c in det_candidates}
    llm_candidates = []
    for c in _parse(raw):
        if (isinstance(c, dict) and c.get("category") == "relation"
                and (str(c.get("subject") or "").strip(),
                     relation.normalize_rel(c.get("rel") or c.get("relation") or ""),
                     str(c.get("object") or "").strip()) in det_keys):
            continue
        llm_candidates.append(c)
    out = []
    for c in det_candidates + llm_candidates:
        if not isinstance(c, dict):
            continue
        # M1 黑名单（确定性，不信 prompt）：场景配置参数不是个人偏好
        if (c.get("category") in _PREF_CATEGORIES
                and _scene_config_only(c.get("text") or "", window)):
            logger.debug("extract drop (scene config): %s", (c.get("text") or "")[:40])
            continue
        item = _govern(c, user_id=user_id, occupant_id=occupant_id,
                       vehicle_id=vehicle_id, session_id=session_id,
                       source_turn_ids=turn_ids)
        if item:
            out.append(item)
    return out
