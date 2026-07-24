"""Skill 层 v1（M0b）：规划知识的声明式载体——加载、检索、渲染。

三型（`skills/README.md` 契约）：guide（语义预筛注入）/ policy（常驻注入）/
workflow（v2 未实装）。设计稿 `docs/design/2026-07-24-eva-benchmark-intelligence-upgrade.md` §4.A。

- 检索 v1 刻意用**纯词法**（keywords 命中 + 中文字符 bigram 重合）：零网络调用、离线
  确定可测；是否升级 embedding 预筛由 Shadow Retrieval 阶段的召回数据决定（eval 先行）。
- `SKILLS_MODE`：off=完全关闭｜shadow=只检索记录不注入（零行为变化，默认）｜
  canary=瘦身 base + 注入｜full=同 canary（中央领域知识删除后与 canary 合流）。
- 权威链：skill 永远在软层——确认/权限/隐私由 VAL/manifest/Validator 硬层承担。
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger("cloud.skills")

SKILL_BUDGET = int(os.getenv("SKILL_BUDGET", "2400"))       # 注入块字符预算
SKILL_TOP_K = int(os.getenv("SKILL_TOP_K", "3"))            # guide 预筛条数
_MIN_SCORE = int(os.getenv("SKILL_MIN_SCORE", "10"))        # 词法命中阈值（一个关键词=10）
_RESCAN_S = 30.0                                            # 目录重扫最小间隔（热更新）

_WORD_RE = re.compile(r"[一-鿿A-Za-z0-9]")


@dataclass(frozen=True)
class SkillDoc:
    name: str
    type: str                      # guide | policy | workflow
    description: str
    knowledge: str
    priority: int = 50
    keywords: tuple = ()
    golden: tuple = ()
    owner: str = ""
    version: int = 1
    path: str = ""


def _chars(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if _WORD_RE.match(ch))


def _bigrams(text: str) -> set:
    s = _chars(text)
    return {s[i:i + 2] for i in range(len(s) - 1)}


class SkillStore:
    """文件系统加载器：skills/{guides,policies,workflows}/*.yaml，mtime 热更新。"""

    def __init__(self, root: str | None = None):
        self.root = Path(root or os.getenv("SKILLS_DIR", "")
                         or Path(__file__).resolve().parents[2] / "skills")
        self._docs: list[SkillDoc] = []
        self._mtimes: dict[str, float] = {}
        self._last_scan = 0.0

    def load(self, force: bool = False) -> list[SkillDoc]:
        import time
        now = time.monotonic()
        if not force and self._docs and now - self._last_scan < _RESCAN_S:
            return self._docs
        self._last_scan = now
        paths = sorted(self.root.glob("*/*.yaml")) if self.root.is_dir() else []
        mtimes = {}
        for p in paths:
            try:
                mtimes[str(p)] = p.stat().st_mtime
            except OSError:
                continue
        if not force and mtimes == self._mtimes and self._docs:
            return self._docs
        docs = []
        for p in paths:
            doc = self._parse(p)
            if doc:
                docs.append(doc)
        self._docs, self._mtimes = docs, mtimes
        return self._docs

    @staticmethod
    def _parse(path: Path) -> SkillDoc | None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:                     # 坏文件跳过不崩规划（fail-open + 告警）
            logger.warning("skill %s 解析失败，跳过: %s", path.name, e)
            return None
        name = str(raw.get("name") or "").strip()
        stype = str(raw.get("type") or "").strip()
        desc = str(raw.get("description") or "").strip()
        knowledge = str(raw.get("knowledge") or "").strip()
        if not (name and desc and knowledge and stype in ("guide", "policy", "workflow")):
            logger.warning("skill %s 缺必填字段（name/type/description/knowledge），跳过", path.name)
            return None
        return SkillDoc(
            name=name, type=stype, description=desc, knowledge=knowledge,
            priority=int(raw.get("priority") or 50),
            keywords=tuple(str(k) for k in (raw.get("keywords") or [])),
            golden=tuple((g or {}) for g in (raw.get("golden") or [])
                         if isinstance(g, dict)),
            owner=str(raw.get("owner") or ""),
            version=int(raw.get("version") or 1),
            path=str(path),
        )

    def guides(self) -> list[SkillDoc]:
        return [d for d in self.load() if d.type == "guide"]

    def policies(self) -> list[SkillDoc]:
        return sorted((d for d in self.load() if d.type == "policy"),
                      key=lambda d: -d.priority)


def score(text: str, doc: SkillDoc) -> int:
    """词法相关性：显式关键词命中（各 10 分）+ 与 description/keywords 的 bigram 重合。
    刻意不拿 knowledge 全文参与（few-shot 里的地名/JSON 会造成假重合）。"""
    kw_hits = sum(1 for k in doc.keywords if k and k in text)
    base = _bigrams(text) & _bigrams(doc.description + " " + " ".join(doc.keywords))
    return kw_hits * 10 + len(base)


def top_guides(text: str, guides: list[SkillDoc], k: int = SKILL_TOP_K,
               min_score: int = _MIN_SCORE) -> list[SkillDoc]:
    scored = [(score(text, d), d) for d in guides]
    hits = [(s, d) for s, d in scored if s >= min_score]
    hits.sort(key=lambda x: (-x[0], -x[1].priority, x[1].name))
    return [d for _, d in hits[:k]]


def render_skills_block(policies: list[SkillDoc], guides: list[SkillDoc],
                        budget: int = SKILL_BUDGET) -> str:
    """policies 常驻在前（小而全量），guides 按 priority 在预算内注入。"""
    if not policies and not guides:
        return ""
    parts = ["== 规划知识（按需注入）=="]
    used = len(parts[0])
    for d in policies:
        parts.append(d.knowledge)
        used += len(d.knowledge)
    for d in sorted(guides, key=lambda d: -d.priority):
        if used + len(d.knowledge) > budget:
            logger.info("skill %s 超预算被裁（used=%d）", d.name, used)
            continue
        parts.append(d.knowledge)
        used += len(d.knowledge)
    return "\n\n".join(parts)


_default_store: SkillStore | None = None


def default_store() -> SkillStore:
    global _default_store
    if _default_store is None:
        _default_store = SkillStore()
    return _default_store


def skills_mode() -> str:
    mode = os.getenv("SKILLS_MODE", "shadow").strip().lower()
    return mode if mode in ("off", "shadow", "canary", "full") else "shadow"


def plan_skills(text: str) -> tuple[str, list[str], str]:
    """规划轮入口：返回 (mode, 记录名单, 注入块)。

    shadow：检索并记录（obs/plan.skills），块为空——零行为变化；
    canary/full：检索 + 渲染注入块（policies 常驻 + guides top-K）。
    检索为纯词法同步计算，不增加规划轮网络调用。"""
    mode = skills_mode()
    if mode == "off":
        return mode, [], ""
    store = default_store()
    guides = top_guides(text, store.guides())
    policies = store.policies()
    names = [f"{mode}:{d.name}" for d in guides]
    if mode == "shadow":
        return mode, names, ""
    names += [f"{mode}:{d.name}" for d in policies]
    return mode, names, render_skills_block(policies, guides)
