"""CA2-15 S3a：一个偏好维度只说一个现行的话——显式修改胜过旧偏好。

线上实测（2026-10-03，车主 `u1`）：60 条现行 semantic 条目里，7 个维度同时有多条现行；「常点的咖啡」一个维度 8 条，
全是同一件事的不同说法；唯一的真冲突是较早的推断「喜欢（辣的菜）」与较新的显式「不吃辣」并存。成因：巩固只认逐字相同为等价、
冲突只替换最新一条；Agent 的 `remember` 不走修订；推断可以替换显式。

判据（维度 = 谓词等价类 + subject）：
- **单值**（默认，含 LLM 自造的谓词）：新的显式陈述替换维度内全部现行条目；已有显式条目时推断不写入，没有时替换全部现行推断；
  读取时每个维度只给一条——显式优先、较新优先。存量重复不需要迁移数据就只剩一条。
- **多值**（`extract.MULTI_VALUED`）：不同的值并存；近似复述（字面二元组 Dice ≥ 0.75）按等价加强而不新增，读取时只留一条。
- **事实类**（`extract.FACT_PREFIXES`）：写入维持原规则，读取只合并逐字重复（「女儿叫小雨 / 小美」可能是两个人）。
"""
from __future__ import annotations

import re

from extract import FACT_PREFIXES, MULTI_VALUED, normalize_predicate
from relation import (REL_FAMILY, SELF_WORDS, is_kinship_word, kinship_spellings, normalize_kinship,
                      normalize_subject)

NEAR_DUPLICATE = 0.75
_NOISE = re.compile(r"[\s，。,.!！?？、;；:：/\\()（）\"'“”‘’\-]+")


def is_fact(predicate: str) -> bool:
    return normalize_predicate(predicate).startswith(FACT_PREFIXES)


def is_multi_valued(predicate: str) -> bool:
    return normalize_predicate(predicate) in MULTI_VALUED


def is_single_valued(predicate: str) -> bool:
    return bool(predicate) and not is_fact(predicate) and not is_multi_valued(predicate)


def explicit(item) -> bool:
    return (item.get("provenance") or "user_stated") == "user_stated"


def _bigrams(text: str) -> set[str]:
    norm = _NOISE.sub("", (text or "").lower())
    return {norm[i:i + 2] for i in range(len(norm) - 1)} if len(norm) > 1 else {norm}


def equivalent(a: str, b: str) -> bool:
    """同一维度里两句话是不是同一件事的不同说法（逐字相同或字面二元组 Dice ≥ 0.75）。"""
    if (a or "").strip() == (b or "").strip():
        return True
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return False
    return 2 * len(x & y) / (len(x) + len(y)) >= NEAR_DUPLICATE


def preference_order(items: list[dict]) -> list[dict]:
    """显式优先、较新优先。"""
    return sorted(items, key=lambda it: (explicit(it), it.get("valid_from") or 0), reverse=True)


def aliases_from_family(edges) -> dict[str, str]:
    """同一个人的名字 → 称谓（亲属关系边「名字 —family→ 称谓」）。只在没有歧义时归一：一个称谓只对应一个具名的人、
    这个名字也只对应一个称谓。两个女儿各有名字就不合并（宁可分开存，也不能把一个人的偏好记到另一个人头上）。"""
    named: dict[str, set[str]] = {}     # 称谓 → 具名的人
    kins: dict[str, set[str]] = {}      # 名字 → 称谓
    for edge in edges or ():
        if (edge.get("rel") or REL_FAMILY) != REL_FAMILY:
            continue
        kin = normalize_kinship(str(edge.get("object") or "").strip())
        who = str(edge.get("subject") or "").strip()
        if is_kinship_word(kin) and who and not is_kinship_word(who) and normalize_subject(who):
            named.setdefault(kin, set()).add(who)
            kins.setdefault(who, set()).add(kin)
    return {next(iter(people)): kin for kin, people in named.items()
            if len(people) == 1 and len(kins[next(iter(people))]) == 1}


def canonical_subject(subject, aliases: dict | None = None) -> str:
    """同一个人的称谓与名字归成一个主体（`pg_store.subject_aliases`：一个称谓只对应一个具名的人时才归一）。"""
    s = normalize_subject(subject)
    return (aliases or {}).get(s, s) if s else ""


def subject_group(subject, aliases: dict | None = None) -> list[str]:
    """与 `subject` 指同一个人的全部写法：库里按写入时的原样存（存量与 Agent 写入未必归一过），查的时候要一起查。"""
    canon = canonical_subject(subject, aliases)
    if not canon:
        return ["", *SELF_WORDS]
    group = {canon, str(subject or "").strip(), *kinship_spellings(canon)}
    group |= {name for name, kin in (aliases or {}).items() if kin == canon}
    return sorted(s for s in group if s)


def dimension(item, aliases: dict | None = None) -> tuple:
    return (item.get("occupant_id") or "primary", normalize_predicate(item.get("predicate") or ""),
            canonical_subject(item.get("subject"), aliases))


def fold(scored: list[tuple[dict, float]], aliases: dict | None = None) -> list[tuple[dict, float]]:
    """读取折叠：每个维度留下该留的那几条，其余条目从结果里去掉；保持原来的相关性次序。"""
    groups: dict[tuple, list[dict]] = {}
    for item, _score in scored:
        if item.get("kind") == "semantic" and item.get("predicate"):
            groups.setdefault(dimension(item, aliases), []).append(item)
    keep: set[int] = set()
    for (_occ, predicate, _subject), members in groups.items():
        ordered = preference_order(members)
        if is_single_valued(predicate):
            keep.add(id(ordered[0]))
            continue
        kept: list[dict] = []
        for item in ordered:
            if is_fact(predicate):
                duplicate = any((k.get("text") or "").strip() == (item.get("text") or "").strip() for k in kept)
            else:
                duplicate = any(equivalent(k.get("text") or "", item.get("text") or "") for k in kept)
            if not duplicate:
                kept.append(item)
                keep.add(id(item))
    return [(item, score) for item, score in scored
            if not (item.get("kind") == "semantic" and item.get("predicate")) or id(item) in keep]
