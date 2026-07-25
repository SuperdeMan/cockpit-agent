"""关系边：封闭词表 + 归一 + 一跳解析（M2 记忆图谱 P1）。

**为什么要有关系边**：母提案 §1.2-E2 举的 Eva 例子是「带我去接孩子放学」——要走通它，
系统得知道「孩子=小雨」「小雨在 XX 小学」。这是长链路规划的真实前置，也是关系边**唯一
非做不可的理由**；没有这条消费链，图谱就是死数据（子 RFC §4 「消费面先于存储面」）。

**rel 是封闭词表**：LLM 自由造词的代价这个仓库付过两次（`predicate_class` 的 hvac.temperature
vs climate.temperature 别名爆炸、B3-3 M2 修复）。词表外的候选一律丢弃，不做兜底猜测。

纯函数 + 词表，无 IO——存储在 pg_store，消费在 navigation。
"""
from __future__ import annotations

import re

# ── 封闭 rel 词表（新增须先登记 docs/conventions.md，中央不为具体 rel 写分支）──
REL_FAMILY = "family"            # 小雨 —family→ 女儿
REL_PLACE_OF = "place_of"        # 小雨 —place_of→ XX小学
REL_WORKS_AT = "works_at"
REL_LIVES_AT = "lives_at"
REL_OWNS = "owns"                # 我 —owns→ 特斯拉Model Y（车书问答接地）
REL_PREFERS_BRAND = "prefers_brand"

REL_VOCAB = (REL_FAMILY, REL_PLACE_OF, REL_WORKS_AT, REL_LIVES_AT,
             REL_OWNS, REL_PREFERS_BRAND)

# LLM 常造的同义词 → canonical（照 _PRED_ALIAS 先例，只认已知别名，不做模糊匹配）
_REL_ALIAS = {
    "relative": REL_FAMILY, "family_member": REL_FAMILY, "kinship": REL_FAMILY,
    "is_family": REL_FAMILY, "relation": REL_FAMILY,
    "school": REL_PLACE_OF, "school_of": REL_PLACE_OF, "studies_at": REL_PLACE_OF,
    "location_of": REL_PLACE_OF, "place": REL_PLACE_OF, "kindergarten": REL_PLACE_OF,
    "work_at": REL_WORKS_AT, "company": REL_WORKS_AT, "workplace": REL_WORKS_AT,
    "live_at": REL_LIVES_AT, "home": REL_LIVES_AT, "lives": REL_LIVES_AT,
    "own": REL_OWNS, "vehicle": REL_OWNS, "car": REL_OWNS,
    "brand_preference": REL_PREFERS_BRAND, "prefers": REL_PREFERS_BRAND,
}

# 人称词 → 亲属关系宾语（消费侧：「去接孩子放学」的「孩子」要能对到 family 边的 object）
_KINSHIP_SYNONYMS: dict[str, tuple[str, ...]] = {
    "女儿": ("女儿", "闺女"),
    "儿子": ("儿子",),
    "孩子": ("孩子", "娃", "小孩", "女儿", "儿子", "闺女"),   # 泛称覆盖具体称谓
    "老婆": ("老婆", "妻子", "太太", "媳妇", "爱人"),
    "老公": ("老公", "丈夫", "先生", "爱人"),
    "妈妈": ("妈妈", "妈", "母亲", "老妈", "妈咪"),
    "爸爸": ("爸爸", "爸", "父亲", "老爸", "爹"),
}
# 话术里可能出现的人称触发词（长词优先匹配，防「女儿」被「儿」截断）
_PERSON_WORDS = sorted(
    {w for words in _KINSHIP_SYNONYMS.values() for w in words} | set(_KINSHIP_SYNONYMS),
    key=len, reverse=True)
_PERSON_RE = re.compile("|".join(re.escape(w) for w in _PERSON_WORDS))


# family 边的 object 是亲属称谓，LLM 会写成「用户的女儿」「我女儿」等自然语言变体，
# 而查询侧是**精确匹配**（刻意的：模糊会把「小雨」匹到「小雨点」，导航到错地方）。
# 故入库时把称谓归一到裸词——真栈首验实测：存成「用户的女儿」，「去接孩子」就查不到了。
_KIN_PREFIX_RE = re.compile(r"^(用户的?|我的?|他的?|她的?)")


def normalize_kinship(obj: str) -> str:
    """亲属称谓归一：剥「用户的/我的」前缀 → 映射到 canonical 称谓词。非亲属原样返回。"""
    t = _KIN_PREFIX_RE.sub("", (obj or "").strip()).strip()
    if not t:
        return (obj or "").strip()
    for canon, syns in _KINSHIP_SYNONYMS.items():
        if t in syns or t == canon:
            return canon
    return t


def normalize_rel(rel: str) -> str:
    """已知别名 → canonical；词表内原样；**词表外返回空串（调用方丢弃）**。"""
    r = (rel or "").strip().lower()
    if r in REL_VOCAB:
        return r
    return _REL_ALIAS.get(r, "")


def is_valid(rel: str) -> bool:
    return bool(normalize_rel(rel))


def find_person_word(text: str) -> str:
    """从话术里取第一个人称词（「去接孩子放学」→「孩子」）。无 → 空串。"""
    m = _PERSON_RE.search(text or "")
    return m.group(0) if m else ""


def kinship_aliases(person_word: str) -> tuple[str, ...]:
    """人称词 → 该称谓的同义集合（用于匹配 family 边的 object）。

    「孩子」是泛称，要能命中存成「女儿」的边——这是解析成功率的关键（用户存的时候说
    「我女儿叫小雨」，用的时候说「去接孩子」）。
    """
    w = (person_word or "").strip()
    if not w:
        return ()
    if w in _KINSHIP_SYNONYMS:
        return _KINSHIP_SYNONYMS[w]
    for canon, syns in _KINSHIP_SYNONYMS.items():
        if w in syns:
            return syns
    return (w,)


def normalize_candidate(c: dict) -> dict | None:
    """LLM 抽取的关系候选 → 规范化边；不合法返回 None（丢弃，不猜）。

    要求：subject / object 非空且非纯空白，rel 在封闭词表内。
    """
    if not isinstance(c, dict):
        return None
    subject = str(c.get("subject") or "").strip()
    obj = str(c.get("object") or "").strip()
    rel = normalize_rel(c.get("rel") or c.get("relation") or "")
    if not subject or not obj or not rel:
        return None
    if len(subject) > 40 or len(obj) > 80:      # 明显是句子不是实体名 → 丢
        return None
    if rel == REL_FAMILY:
        obj = normalize_kinship(obj)
    return {
        "subject": subject,
        "rel": rel,
        "object": obj,
        "object_ref": str(c.get("object_ref") or "").strip(),
        "confidence": _conf(c.get("confidence")),
        "provenance": str(c.get("provenance") or "user_stated"),
        "privacy_level": str(c.get("privacy_level") or "sensitive"),
        "source_turn_ids": str(c.get("source_turn_ids") or ""),
    }


def _conf(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 1.0
