"""Skill 层 v1（M0b）+ 2026-07-26 补全：规划知识的声明式载体——加载、检索、渲染。

三型（`skills/README.md` 契约）：guide（预筛注入）/ policy（常驻注入）/
workflow（v2 未实装）。设计稿 `docs/design/2026-07-24-eva-benchmark-intelligence-upgrade.md` §4.A。

- 检索双通道 `SKILLS_RETRIEVAL`：**lexical**=纯词法（keywords 命中 + 中文字符 bigram
  重合；零网络、离线确定可测）；**hybrid**=词法 ∪ 语义预筛（guide `description` 向量
  与用户话术余弦，经 llm-gateway Embed、与 registry 语义路由同源）——词法命中恒保留，
  语义只补词法漏召的 paraphrase（keywords 没写到的说法）。**语义通道 fail-open**：
  embedding 不可用/超时 → 该轮纯词法，冷却期内不重试（借 registry/store.py 姿态）。
  档位由 `test/eval_skills.py` 的 paraphrase 车道数据拍板（eval 先行）。
- `SKILLS_MODE`：**full=注入（默认，Full Migration 2026-07-24 后中央 base 不再含领域
  知识）**｜canary=同 full（A/B 阶段命名保留）｜shadow=只检索记录不注入（研究档，
  注意 Full 后 base 无领域知识，shadow/off 会缺知识）｜off=完全关闭（debug 档）。
- 归因诚实（2026-07-26 修）：`plan.skills` 名单在**渲染之后**生成——超预算被裁的
  guide 记 `!clipped` 后缀而不是谎称已注入；guide 名单带检索通道后缀 `@lex`/`@vec`
  （badcase 归因：词法还是语义检回的）。
- `few_shots` 字段实装（2026-07-26，验收立卡）：解析并渲染进注入块（此前是「文档有、
  代码不读」的空契约，照 README 写会被静默丢弃）。
- 热生效：SKILLS_MODE / SKILLS_RETRIEVAL / 阈值超时每轮实时读；SKILL_BUDGET /
  SKILL_TOP_K / SKILL_MIN_SCORE 进程启动时定（重启生效）。
- 权威链：skill 永远在软层——确认/权限/隐私由 VAL/manifest/Validator 硬层承担。
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger("cloud.skills")

# `or` 兜底：compose 以 ${VAR:-} 透传（不带默认值，避免默认值在 compose 与代码两处漂移），
# 未设置时容器收到空串——int("") 会炸 import。
SKILL_BUDGET = int(os.getenv("SKILL_BUDGET") or "2400")     # 注入块字符预算
SKILL_TOP_K = int(os.getenv("SKILL_TOP_K") or "3")          # guide 预筛条数
_MIN_SCORE = int(os.getenv("SKILL_MIN_SCORE") or "10")      # 词法命中阈值（一个关键词=10）
_RESCAN_S = 30.0                                            # 目录重扫最小间隔（热更新）
_EMBED_COOLDOWN_S = 30.0                                    # 语义通道失败冷却（防抖打日志）

_WORD_RE = re.compile(r"[一-鿿A-Za-z0-9]")

# schema 顶层键白名单（skills/README.md）：未知键=大概率拼写错误（few_shot/keyword…），
# 静默忽略会让作者以为知识生效了——告警但不拒载（fail-open）。
_KNOWN_KEYS = {"name", "type", "description", "knowledge", "priority",
               "keywords", "few_shots", "golden", "owner", "version"}


@dataclass(frozen=True)
class SkillDoc:
    name: str
    type: str                      # guide | policy | workflow
    description: str
    knowledge: str
    body: str = ""                 # 实际注入文本 = knowledge + few_shots 渲染（预算按它算）
    priority: int = 50
    keywords: tuple = ()
    few_shots: tuple = ()
    golden: tuple = ()
    owner: str = ""
    version: int = 1
    path: str = ""


def _chars(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if _WORD_RE.match(ch))


def _bigrams(text: str) -> set:
    s = _chars(text)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _render_few_shots(shots: tuple) -> str:
    """few_shots → 注入文本（与既有 knowledge 内嵌示例同风格）。plan 为 dict 时紧凑
    JSON 序列化——few-shot 的价值恰在输出形态示范，格式必须与要求的输出一致。"""
    parts = []
    for s in shots:
        user = str(s.get("user") or "").strip()
        plan = s.get("plan")
        if not user or plan is None:
            continue
        plan_txt = plan.strip() if isinstance(plan, str) else json.dumps(
            plan, ensure_ascii=False, separators=(",", ":"))
        parts.append(f"示例——用户：『{user}』\n→ {plan_txt}")
    return "\n\n".join(parts)


class SkillStore:
    """文件系统加载器：skills/{guides,policies,workflows}/*.yaml，mtime 热更新。"""

    def __init__(self, root: str | None = None):
        self.root = Path(root or os.getenv("SKILLS_DIR", "")
                         or Path(__file__).resolve().parents[2] / "skills")
        self._docs: list[SkillDoc] = []
        self._mtimes: dict[str, float] = {}
        self._last_scan = 0.0
        self._desc_vecs: dict[str, tuple[float, ...]] = {}   # guide description 向量缓存
        self._desc_model = ""                                # 缓存向量的 embedding 模型
        self._last_good: dict[str, SkillDoc] = {}            # path → 上一版好文档（LKG）

    def load(self, force: bool = False) -> list[SkillDoc]:
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
        docs, seen = [], {}
        for p in paths:
            try:
                doc = self._parse(p)
            except Exception as e:      # 任何单文件异常都不许崩规划（fail-open 兜底闸）
                logger.warning("skill %s 解析异常，跳过: %s", p.name, e)
                doc = None
            if doc is None:
                # last-known-good：热更新改坏文件时沿用上一版好文档（文件被删除则
                # 不在 paths 里、自然下线——删除是意图，改坏不是）
                doc = self._last_good.get(str(p))
                if doc is not None:
                    logger.warning("skill %s 本次加载失败，沿用上一版（LKG）", p.name)
            if doc is None:
                continue
            if doc.name in seen:        # 重名：先到者胜（glob 有序=确定性），后到跳过
                logger.warning("skill 重名 %s（%s 与 %s）——后者跳过",
                               doc.name, seen[doc.name], p.name)
                continue
            seen[doc.name] = p.name
            self._last_good[str(p)] = doc
            docs.append(doc)
        self._docs, self._mtimes = docs, mtimes
        self._desc_vecs, self._desc_model = {}, ""      # 文件变了，向量缓存整体失效
        return self._docs

    @staticmethod
    def _coerce_int(raw_val, default: int, path_name: str, field: str) -> int:
        """priority/version 宽容取整：非法值回默认并告警（运行时知识可用性 > 整洁；
        eval_skills 契约车道对同类问题是**硬失败**，坏文件到不了主干）。"""
        try:
            return int(raw_val if raw_val is not None else default)
        except (TypeError, ValueError):
            logger.warning("skill %s 字段 %s=%r 非整数——按默认 %d 处理（修文件！）",
                           path_name, field, raw_val, default)
            return default

    @classmethod
    def _parse(cls, path: Path) -> SkillDoc | None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:                     # 坏文件跳过不崩规划（fail-open + 告警）
            logger.warning("skill %s 解析失败，跳过: %s", path.name, e)
            return None
        if not isinstance(raw, dict):              # 顶层写成数组/标量：结构性坏文件
            logger.warning("skill %s 顶层必须是映射（实际 %s），跳过",
                           path.name, type(raw).__name__)
            return None
        name = str(raw.get("name") or "").strip()
        stype = str(raw.get("type") or "").strip()
        desc = str(raw.get("description") or "").strip()
        knowledge = str(raw.get("knowledge") or "").strip()
        if not (name and desc and knowledge and stype in ("guide", "policy", "workflow")):
            logger.warning("skill %s 缺必填字段（name/type/description/knowledge），跳过", path.name)
            return None
        expected_dir = {"guide": "guides", "policy": "policies", "workflow": "workflows"}[stype]
        if path.parent.name != expected_dir:       # 目录/type 不一致：按 type 生效但告警
            logger.warning("skill %s type=%s 应放 %s/（实际在 %s/）——语义按 type 生效，请归位",
                           path.name, stype, expected_dir, path.parent.name)
        unknown = set(raw) - _KNOWN_KEYS
        if unknown:
            logger.warning("skill %s 含未知顶层字段 %s——已忽略，检查拼写（契约见 skills/README.md）",
                           path.name, sorted(unknown))
        few_shots = tuple(s for s in (raw.get("few_shots") or []) if isinstance(s, dict))
        shots_txt = _render_few_shots(few_shots)
        body = knowledge + (f"\n\n{shots_txt}" if shots_txt else "")
        kw_raw = raw.get("keywords") or []
        if not isinstance(kw_raw, list):           # 写成字符串会被逐字符迭代成噪声关键词
            logger.warning("skill %s keywords 必须是列表（实际 %s）——忽略",
                           path.name, type(kw_raw).__name__)
            kw_raw = []
        return SkillDoc(
            name=name, type=stype, description=desc, knowledge=knowledge,
            body=body,
            priority=cls._coerce_int(raw.get("priority"), 50, path.name, "priority"),
            keywords=tuple(str(k) for k in kw_raw),
            few_shots=few_shots,
            golden=tuple((g or {}) for g in (raw.get("golden") or [])
                         if isinstance(g, dict)),
            owner=str(raw.get("owner") or ""),
            version=cls._coerce_int(raw.get("version"), 1, path.name, "version"),
            path=str(path),
        )

    def guides(self) -> list[SkillDoc]:
        return [d for d in self.load() if d.type == "guide"]

    def policies(self) -> list[SkillDoc]:
        return sorted((d for d in self.load() if d.type == "policy"),
                      key=lambda d: -d.priority)


# ── 词法通道 ──────────────────────────────────────────────────────────────────

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


# ── 语义通道（hybrid；fail-open 回词法） ──────────────────────────────────────

_embed_stub = None
_embed_fail_ts = 0.0


def _sem_threshold() -> float:
    """默认 0.40 由 paraphrase 语料阈值扫描拍板（2026-07-26，eval_skills --retrieval both）：
    0.40 召回 10/11 且语义通道零新增噪声；0.45 掉到 6/11；0.35 噪声不再降。"""
    return float(os.getenv("SKILL_SEM_THRESHOLD") or "0.40")


def _embed_timeout() -> float:
    return float(os.getenv("SKILL_EMBED_TIMEOUT") or "1.0")


async def _embed_texts(texts: list[str]) -> tuple[list[tuple[float, ...]], str] | None:
    """经 llm-gateway Embed（与 registry/memory 同源，百炼 text-embedding-v4）。
    失败/超时 → None + 冷却（该轮及冷却期内纯词法，不刷日志不堵规划）。"""
    global _embed_stub, _embed_fail_ts
    if time.monotonic() - _embed_fail_ts < _EMBED_COOLDOWN_S:
        return None
    try:
        from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
        if _embed_stub is None:
            from runtime.grpcio import aio_channel
            _embed_stub = llm_pb2_grpc.LLMGatewayStub(
                aio_channel(os.getenv("LLM_GATEWAY_ADDR", "llm-gateway:50052")))
        resp = await _embed_stub.Embed(
            llm_pb2.EmbedRequest(texts=texts), timeout=_embed_timeout())
        vecs = [tuple(e.values) for e in resp.embeddings]
        if len(vecs) != len(texts) or any(not v for v in vecs):
            raise ValueError(f"embedding 返回异常（{len(vecs)}/{len(texts)}）")
        return vecs, str(resp.model_used or "")
    except Exception as e:
        _embed_fail_ts = time.monotonic()
        logger.warning("skill 语义预筛不可用，回落纯词法（%.0fs 冷却）: %s",
                       _EMBED_COOLDOWN_S, e)
        return None


def _cos(a: tuple, b: tuple) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0


async def _semantic_scores(text: str, guides: list[SkillDoc],
                           store: SkillStore) -> dict[str, float] | None:
    """query 与各 guide description 的余弦。query 向量与缺失的 doc 向量并入**一次**
    Embed 调用（同轮同模型，杜绝跨模型比余弦）；模型切换 → 缓存整体失效重嵌。"""
    need = [d for d in guides if d.name not in store._desc_vecs]
    out = await _embed_texts([text] + [d.description for d in need])
    if out is None:
        return None
    vecs, model = out
    if store._desc_vecs and model and store._desc_model and model != store._desc_model:
        store._desc_vecs = {}                       # 网关换了 embedding 模型：全量重嵌
        need = list(guides)
        out = await _embed_texts([text] + [d.description for d in need])
        if out is None:
            return None
        vecs, model = out
    qvec = vecs[0]
    for d, v in zip(need, vecs[1:]):
        store._desc_vecs[d.name] = v
    store._desc_model = model or store._desc_model
    return {d.name: _cos(qvec, store._desc_vecs[d.name])
            for d in guides if d.name in store._desc_vecs}


def skills_retrieval() -> str:
    mode = os.getenv("SKILLS_RETRIEVAL", "hybrid").strip().lower()
    return mode if mode in ("lexical", "hybrid") else "hybrid"


async def retrieve_guides(text: str, store: SkillStore, k: int = SKILL_TOP_K,
                          min_score: int = _MIN_SCORE) -> list[tuple[SkillDoc, str]]:
    """guide 预筛 → [(doc, 通道)]。词法命中恒保留（keywords 是作者显式设计的高精度
    信号）；hybrid 下语义只**补位**词法漏召的 paraphrase，不与词法争排序。"""
    guides = store.guides()
    pairs: list[tuple[SkillDoc, str]] = [(d, "lex") for d in top_guides(text, guides, k, min_score)]
    if skills_retrieval() != "hybrid" or not guides or len(pairs) >= k:
        return pairs
    sem = await _semantic_scores(text, guides, store)
    if not sem:
        return pairs
    have = {d.name for d, _ in pairs}
    thr = _sem_threshold()
    extras = sorted(((s, d) for d in guides
                     if (s := sem.get(d.name, 0.0)) >= thr and d.name not in have),
                    key=lambda x: (-x[0], -x[1].priority, x[1].name))
    for s, d in extras:
        if len(pairs) >= k:
            break
        pairs.append((d, "vec"))
    return pairs


# ── 渲染 ─────────────────────────────────────────────────────────────────────

def render_skills_block(policies: list[SkillDoc], guides: list[SkillDoc],
                        budget: int = SKILL_BUDGET
                        ) -> tuple[str, list[SkillDoc], list[SkillDoc]]:
    """policies 常驻在前（小而全量，治理层控总量）；guides 按 priority 在预算内注入。
    返回 (注入块, 实际注入的 guides, 超预算被裁的 guides)——名单必须反映真实注入，
    「说注入了实际被裁」会让 badcase 归因说谎（2026-07-26 修）。"""
    if not policies and not guides:
        return "", [], []
    parts = ["== 规划知识（按需注入）=="]
    used = len(parts[0])
    for d in policies:
        parts.append(d.body)
        used += len(d.body)
    injected, clipped = [], []
    for d in sorted(guides, key=lambda d: -d.priority):
        if used + len(d.body) > budget:
            logger.info("skill %s 超预算被裁（used=%d）", d.name, used)
            clipped.append(d)
            continue
        parts.append(d.body)
        used += len(d.body)
        injected.append(d)
    return "\n\n".join(parts), injected, clipped


_default_store: SkillStore | None = None


def default_store() -> SkillStore:
    global _default_store
    if _default_store is None:
        _default_store = SkillStore()
    return _default_store


def skills_mode() -> str:
    mode = os.getenv("SKILLS_MODE", "full").strip().lower()
    return mode if mode in ("off", "shadow", "canary", "full") else "full"


async def plan_skills(text: str) -> tuple[str, list[str], str]:
    """规划轮入口：返回 (mode, 记录名单, 注入块)。

    shadow：检索并记录（obs/plan.skills），块为空——零行为变化；
    canary/full：检索 + 渲染注入块（policies 常驻 + guides top-K）。
    名单契约：guide 记 `mode:name@通道`（lex/vec），被裁再加 `!clipped`；policy 记
    `mode:name`。词法档零网络同步计算；hybrid 档一次 Embed（fail-open 回词法）。"""
    mode = skills_mode()
    if mode == "off":
        return mode, [], ""
    store = default_store()
    pairs = await retrieve_guides(text, store)
    if mode == "shadow":
        return mode, [f"{mode}:{d.name}@{ch}" for d, ch in pairs], ""
    policies = store.policies()
    block, injected, clipped = render_skills_block(policies, [d for d, _ in pairs])
    ch = {d.name: c for d, c in pairs}
    names = [f"{mode}:{d.name}@{ch[d.name]}" for d in injected]
    names += [f"{mode}:{d.name}@{ch[d.name]}!clipped" for d in clipped]
    names += [f"{mode}:{d.name}" for d in policies]
    return mode, names, block


def render_for_names(names: list[str] | None) -> str:
    """T2 再规划的知识继承（2026-07-27，评审缺口 4）：按初规划**实际注入**的名单重渲染。

    不重新检索——再规划的输入是观察结果不是用户话术，检索无锚；shadow/off 轮与被裁
    （`!clipped`）项本来就没进初规划上下文，同样不进再规划。文件按当前版本重渲染
    （mtime 热更新窗 30s，replan 与初规划相隔秒级，刻意不做版本 hash 钉扎——为一个
    几乎不会发生的漂移引入快照管理不值得；若未来出现跨小时长任务再议）。"""
    if not names:
        return ""
    wanted = []
    for n in names:
        mode, _, rest = n.partition(":")
        if mode not in ("canary", "full") or not rest or rest.endswith("!clipped"):
            continue
        wanted.append(rest.split("@", 1)[0])
    if not wanted:
        return ""
    docs = {d.name: d for d in default_store().load()}
    policies = [docs[n] for n in wanted if n in docs and docs[n].type == "policy"]
    guides = [docs[n] for n in wanted if n in docs and docs[n].type == "guide"]
    block, _, _ = render_skills_block(policies, guides)
    return block
