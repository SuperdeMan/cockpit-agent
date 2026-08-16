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


# ── 写入闸（QA 卡 Q5）─────────────────────────────────────────────────────
# 此前这里只归一**谓词词表**，没有角色/自环/单值约束，`superseded_by` 列存在但
# 从未写过。2026-08-15 psql 实测的后果：主宾颠倒 2 条、自环 4 条、
# 同一个孩子三个学校。**这直接改写了 I-044 的定性**：不是模型幻觉，
# 是图谱里真有三条互相矛盾的边，每轮召回哪条看运气。

#: 地点类关系：方向**固定**为 人 → 地点。反过来存进去，消费侧永远查不到，
#: 而它在库里看起来像一条正常的边。
_PLACE_RELS = (REL_PLACE_OF, REL_WORKS_AT, REL_LIVES_AT)
#: 「人」的判据：亲属称谓 + 自指/泛指人称。刻意窄——**宁可漏挡也不误挡**
#: （挡错一条真边的代价是消费侧永远查不到，比多留一条脏边贵）。
_PERSON_LIKE = frozenset(_PERSON_WORDS) | {"用户", "我", "他", "她", "本人", "自己"}
#: 「地点」的判据：机构/建筑/道路后缀。同上，只认明确的。
_PLACE_SUFFIX_RE = re.compile(
    r"(?:公司|集团|大楼|大厦|工厂|学校|小学|中学|大学|幼儿园|医院|商场|广场|"
    r"路|街|道|区|市|省|县|镇|村|园|站|机场|码头|酒店|门店|分店|店)$")
#: **槽名泄漏**：`深圳 --place_of--> 出发地`（库里实测）——「出发地」不是实体，
#: 是 planner 的槽名漏进了图谱。它既不是人也不是地点，前两道闸都不触发，
#: 所以单独成一类。存进去的边永远没有消费方，只会在召回里当噪声。
_ROLE_PLACEHOLDER = frozenset({
    "出发地", "目的地", "起点", "终点", "地点", "位置", "地址", "途经点",
    "当前位置", "公司", "家", "学校", "未知", "未提供", "无", "null", "none",
})
#: 低于此置信度不落库。缺省 confidence 是 1.0（既有语义），
#: 所以这条只过滤**明确低分**的候选，不影响没写这个字段的调用方。
_MIN_CONFIDENCE = 0.4

#: 单值谓词。⚠ 清洗 dry-run 当场劝退过一条判据：**「同一个 subject 有多个 object」
#: 不是冲突，除非那个谓词本身是单值的**——首版把 `爸妈--family-->爸爸` 与
#: `爸妈--family-->妈妈` 判成冲突，准备把「妈妈」标失效，**直接丢掉一个真实的人**。
_SINGLE_VALUED = frozenset(_PLACE_RELS)


def is_single_valued(rel: str) -> bool:
    """这个谓词是不是「一个 subject 只能有一个 object」——决定要不要 supersede。"""
    return normalize_rel(rel) in _SINGLE_VALUED


def _is_person_like(name: str) -> bool:
    return name in _PERSON_LIKE or normalize_kinship(name) in _PERSON_LIKE


def _is_place_like(name: str) -> bool:
    return bool(_PLACE_SUFFIX_RE.search(name))


def _role_ok(subject: str, rel: str, obj: str) -> bool:
    """地点类关系的主宾角色校验。**只挡明确反了的**，认不出一律放行。

    挡错一条真边的代价（消费侧永远查不到、用户觉得系统「忘了」）比多留一条脏边贵，
    所以判据两头都窄：object 明确是人、或 subject 明确是地点，才拒。
    """
    if rel not in _PLACE_RELS:
        return True
    if _is_person_like(obj):
        return False                 # 「大楼 --works_at--> 用户」：宾语是人 ⇒ 反了
    if _is_place_like(subject) and not _is_person_like(subject):
        return False                 # 「公司 --lives_at--> 大楼」：主语是地点 ⇒ 反了
    return True


def normalize_candidate(c: dict) -> dict | None:
    """LLM 抽取的关系候选 → 规范化边；不合法返回 None（丢弃，不猜）。

    要求：subject / object 非空且非纯空白，rel 在封闭词表内，
    **非自环、主宾角色不颠倒、置信度不低于阈值**（Q5 写入闸）。
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
    # 自环。⚠ **`family` 例外，而且这个例外是被实测按出来的**：
    # 卡 §3-Q5 把 `老婆 --family--> 老婆` 记成「自环边，零信息」，读消费方之后发现
    # **它是「无名的人」的表示法**——`store.resolve_person_place` 靠 family 边的
    # **object** 反查人实体，没名字的人就以称谓自身作实体名。删了它，
    # 「老婆在哪上班」这类一跳解析当场失效（`test_resolve_person_place_via_works_at`
    # 与 `test_deterministic_place_relation_without_name` 两条既有断言当场红）。
    # **卡上的定性可以是错的——落地前读一遍消费方**（同 I-021 那次推翻）。
    if rel != REL_FAMILY and (
            normalize_kinship(subject) == normalize_kinship(obj) or subject == obj):
        return None
    if obj in _ROLE_PLACEHOLDER or subject in _ROLE_PLACEHOLDER:
        return None                  # 槽名漏进了图谱，不是实体
    if not _role_ok(subject, rel, obj):
        return None
    if _conf(c.get("confidence")) < _MIN_CONFIDENCE:
        return None
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
