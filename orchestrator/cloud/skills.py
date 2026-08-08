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

from . import embedding as _embedding

logger = logging.getLogger("cloud.skills")

def _env_int(name: str, default: int, min_val: int | None = None) -> int:
    """环境变量取整（2026-07-27 评审二批）：compose 以 ${VAR:-} 空默认透传（默认值只活
    这里一处），空串/垃圾值（SKILL_BUDGET=oops）一律回默认并告警——配置错误不该让
    Planner 起不来。"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        logger.warning("env %s=%r 非整数——回默认 %d（修配置！）", name, raw, default)
        return default
    if min_val is not None and v < min_val:
        logger.warning("env %s=%d 低于下限 %d——按下限处理", name, v, min_val)
        return min_val
    return v


def _env_float(name: str, default: float, min_val: float | None = None,
               max_val: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = float(raw)
    except ValueError:
        logger.warning("env %s=%r 非数值——回默认 %s（修配置！）", name, raw, default)
        return default
    if not math.isfinite(v):     # nan 能穿过上下限比较、inf 让超时失效（2026-07-27 四批）
        logger.warning("env %s=%r 非有限值——回默认 %s（修配置！）", name, raw, default)
        return default
    # 范围钳制（2026-07-27 评审三批）：越界值不崩但会**静默**改变行为——阈值>1=语义
    # 召回全关、超时<0=embedding 必超时、min_score<0=词法全量放行，都比崩溃更难发现
    if min_val is not None and v < min_val:
        logger.warning("env %s=%s 低于下限 %s——按下限处理", name, v, min_val)
        return min_val
    if max_val is not None and v > max_val:
        logger.warning("env %s=%s 高于上限 %s——按上限处理", name, v, max_val)
        return max_val
    return v


# 公开别名：exemplars.py 共用同一套取值语义（空串回默认 / 垃圾值不崩 / nan·inf 拒收 /
# 上下限钳制）——这些是三轮评审换来的教训，复制一份就等于分叉一份（M5 P1）。
env_int, env_float = _env_int, _env_float


# 2400→2600（2026-08-02）：policy 常驻、guide 按检索序注入，两者**共用这一个预算**——
# 于是「加一条 policy」会静默把最相关的那条 guide 挤成 !clipped。实测：新增
# negation-and-deferral（277 字）后 policies 合计 1047 + 头 14 + navigation-with-stop
# 1428 = 2489 > 2400，对抗用例 ki.navigation-with-stop.hit 当场红。
# 守卫见 `tests/test_skills_budget_headroom.py`：常驻总量 + 最大 guide 必须放得下。
SKILL_BUDGET = _env_int("SKILL_BUDGET", 2600, min_val=0)    # 注入块字符预算
SKILL_TOP_K = _env_int("SKILL_TOP_K", 3, min_val=1)         # guide 预筛条数
_MIN_SCORE = _env_int("SKILL_MIN_SCORE", 10, min_val=1)     # 词法命中阈值（一个关键词=10）。
                                                            # 下限 1 不是 0：score≥0 恒真，
                                                            # 钳 0 仍等于全量放行（评审四批）
_RESCAN_S = 30.0                                            # 目录重扫最小间隔（热更新）

_WORD_RE = re.compile(r"[一-鿿A-Za-z0-9]")

# 范例库（M5 P1）同处 skills/ 但不是 skill 文档：它没有 name/type/knowledge，被
# SkillStore 扫到只会刷一屏「缺必填字段」告警。目录级排除，契约见 skills/exemplars/README.md。
_NON_SKILL_DIRS = ("exemplars",)

# schema 顶层键白名单（skills/README.md）：未知键=大概率拼写错误（few_shot/keyword…），
# 静默忽略会让作者以为知识生效了——告警但不拒载（fail-open）。
_KNOWN_KEYS = {"name", "type", "description", "knowledge", "priority",
               "semantic_min_score", "capability_dependencies", "keywords",
               "few_shots", "plan_repairs", "golden", "owner", "version"}
_PLAN_REPAIR_KEYS = {"kind", "trigger_any", "producer_intent", "consumer_intent",
                     "slot", "source_path"}


@dataclass(frozen=True)
class PlanRepair:
    """由已注入 skill 声明的窄计划归一，不新增步骤、不覆盖用户真值。"""
    kind: str
    trigger_any: tuple[str, ...]
    producer_intent: str
    consumer_intent: str
    slot: str
    source_path: str


@dataclass(frozen=True)
class SkillDoc:
    name: str
    type: str                      # guide | policy | workflow
    description: str
    knowledge: str
    body: str = ""                 # 静态 knowledge；few_shots 在请求时按 capability refs 渲染
    priority: int = 50
    semantic_min_score: float | None = None  # guide 可按实测噪声抬高自己的语义补位门槛
    capability_dependencies: tuple[str, ...] = ()  # knowledge 可引用的受治理语义能力
    keywords: tuple = ()
    few_shots: tuple = ()
    plan_repairs: tuple[PlanRepair, ...] = ()
    golden: tuple = ()
    owner: str = ""
    version: int = 1
    path: str = ""


def _chars(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if _WORD_RE.match(ch))


def _bigrams(text: str) -> set:
    s = _chars(text)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _resolve_few_shot_plan(plan, capability_refs) -> dict | None:
    """Translate one governed semantic plan to this request's ref-only wire.

    Governance assets keep real pairs so they remain reviewable.  A partial DAG is
    more misleading than no example, therefore one missing pair drops the whole
    example instead of leaving the remaining steps behind.
    """
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        return None
    resolved_steps = []
    for step in plan["steps"]:
        if not isinstance(step, dict):
            return None
        agent_id = str(step.get("agent_id") or step.get("agent") or "").strip()
        intent = str(step.get("intent") or "").strip()
        ref = capability_refs.get((agent_id, intent)) if capability_refs is not None else None
        if not ref:
            return None
        slots = step.get("slots", {})
        depends_on = step.get("depends_on", [])
        slot_refs = step.get("slot_refs", {})
        if (not isinstance(slots, dict) or not isinstance(depends_on, list)
                or not isinstance(slot_refs, dict)):
            return None
        # Render the exact five-field planner wire.  Governed assets may omit
        # empty structural fields for readability, but examples shown to the LLM
        # must not contradict the parser/schema that consumes its answer.
        resolved = {
            "id": step.get("id", f"s{len(resolved_steps) + 1}"),
            "capability_ref": ref,
            "slots": slots,
            "depends_on": depends_on,
            "slot_refs": slot_refs,
        }
        resolved_steps.append(resolved)
    return {**{k: v for k, v in plan.items() if k != "steps"},
            "steps": resolved_steps}


def _render_few_shots(shots: tuple, capability_refs=None) -> str:
    """Render structured examples through the current request's ref mapping."""
    parts = []
    for s in shots:
        user = str(s.get("user") or "").strip()
        plan = _resolve_few_shot_plan(s.get("plan"), capability_refs)
        if not user or plan is None:
            continue
        plan_txt = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
        parts.append(f"示例——用户：『{user}』\n→ {plan_txt}")
    return "\n\n".join(parts)


def _parse_plan_repairs(raw, path_name: str) -> tuple[PlanRepair, ...]:
    """运行时 fail-open：坏声明告警并跳过；CI 的 eval_skills 会硬失败。"""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        logger.warning("skill %s plan_repairs 必须是列表——已忽略", path_name)
        return ()
    out = []
    for index, row in enumerate(raw):
        where = f"{path_name}.plan_repairs[{index}]"
        if not isinstance(row, dict):
            logger.warning("skill %s 必须是映射——已忽略", where)
            continue
        unknown = set(row) - _PLAN_REPAIR_KEYS
        if unknown:
            logger.warning("skill %s 含未知字段 %s——已忽略该条", where, sorted(unknown))
            continue
        triggers = row.get("trigger_any")
        kind = str(row.get("kind") or "").strip()
        producer = str(row.get("producer_intent") or "").strip()
        consumer = str(row.get("consumer_intent") or "").strip()
        slot = str(row.get("slot") or "").strip()
        source_path = str(row.get("source_path") or "").strip()
        if (kind != "dependency_slot_ref" or not isinstance(triggers, list)
                or not all(isinstance(v, str) and v.strip() for v in triggers)
                or not triggers or not producer or not consumer or not slot
                or not re.fullmatch(r"data(?:\.[A-Za-z0-9_]+)+", source_path)):
            logger.warning("skill %s 声明不完整或不安全——已忽略", where)
            continue
        out.append(PlanRepair(
            kind=kind,
            trigger_any=tuple(v.strip() for v in triggers),
            producer_intent=producer,
            consumer_intent=consumer,
            slot=slot,
            source_path=source_path,
        ))
    return tuple(out)


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
        paths = sorted(p for p in self.root.glob("*/*.yaml")
                       if p.parent.name not in _NON_SKILL_DIRS) if self.root.is_dir() else []
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

    @staticmethod
    def _coerce_optional_unit_float(raw_val, path_name: str,
                                    field: str) -> float | None:
        """可选 [0,1] 浮点：运行时非法值告警并回到全局默认门槛。

        字符串和 bool 都拒绝；否则 YAML 中 ``"0.5"``/``true`` 会静默改变检索行为。
        严格 CI 车道会把同一问题升级为硬失败。
        """
        if raw_val is None:
            return None
        if (isinstance(raw_val, bool)
                or not isinstance(raw_val, (int, float))
                or not math.isfinite(float(raw_val))
                or not 0.0 <= float(raw_val) <= 1.0):
            logger.warning(
                "skill %s 字段 %s=%r 必须是 [0,1] 内有限数值——按全局门槛处理（修文件！）",
                path_name, field, raw_val)
            return None
        return float(raw_val)

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
        plan_repairs = _parse_plan_repairs(raw.get("plan_repairs"), path.name)
        deps_value = raw.get("capability_dependencies")
        # Missing/null means the optional field is absent.  Other falsy values
        # (notably ``false`` and ``""``) are malformed and must not silently
        # disable capability-aware rendering during a hot reload.
        deps_raw = [] if deps_value is None else deps_value
        if (not isinstance(deps_raw, list)
                or any(not isinstance(value, str) or not value.strip()
                       for value in deps_raw)):
            # An invalid allowlist must not silently turn capability-aware
            # rendering off. Skip this version; hot reload can retain its LKG.
            logger.warning(
                "skill %s capability_dependencies 必须是非空字符串列表——跳过",
                path.name)
            return None
        capability_dependencies = tuple(dict.fromkeys(
            value.strip() for value in deps_raw))
        # Request refs do not exist at load time.  ``body`` remains the static
        # knowledge portion; structured few-shots are rendered only at injection.
        body = knowledge
        kw_raw = raw.get("keywords") or []
        if not isinstance(kw_raw, list):           # 写成字符串会被逐字符迭代成噪声关键词
            logger.warning("skill %s keywords 必须是列表（实际 %s）——忽略",
                           path.name, type(kw_raw).__name__)
            kw_raw = []
        return SkillDoc(
            name=name, type=stype, description=desc, knowledge=knowledge,
            body=body,
            priority=cls._coerce_int(raw.get("priority"), 50, path.name, "priority"),
            semantic_min_score=cls._coerce_optional_unit_float(
                raw.get("semantic_min_score"), path.name, "semantic_min_score"),
            capability_dependencies=capability_dependencies,
            keywords=tuple(str(k) for k in kw_raw),
            few_shots=few_shots,
            plan_repairs=plan_repairs,
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


def _sem_threshold() -> float:
    """默认 0.40 由 paraphrase 语料阈值扫描拍板（2026-07-26，eval_skills --retrieval both）：
    0.40 召回 10/11 且语义通道零新增噪声；0.45 掉到 6/11；0.35 噪声不再降。
    钳 [0,1]：余弦域外的阈值=语义通道静默全关/全开。"""
    return _env_float("SKILL_SEM_THRESHOLD", 0.40, min_val=0.0, max_val=1.0)


def _embed_timeout() -> float:
    return _env_float("SKILL_EMBED_TIMEOUT", 1.0, min_val=0.1)


async def _embed_texts(texts: list[str]) -> tuple[list[tuple[float, ...]], str] | None:
    """经共享 Embed 出口（embedding.py：与 exemplars 同 stub 同冷却，故障一起 fail-open）。
    失败/超时 → None（该轮及冷却期内纯词法，不刷日志不堵规划）。"""
    return await _embedding.embed_texts(texts, _embed_timeout())


_cos = _embedding.cos


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
                          min_score: int = _MIN_SCORE
                          ) -> list[tuple[SkillDoc, str, float]]:
    """guide 预筛 → [(doc, 通道, 分数)]，**按检索相关度降序**（词法命中在前、语义补位
    在后；组内各按分数）。词法命中恒保留（keywords 是作者显式设计的高精度信号）；
    hybrid 下语义只**补位**词法漏召的 paraphrase。分数随归因名单外发（`@lex:23`/
    `@vec:0.52`）——边缘共召回的取证靠它，没有分数只能靠复跑分类（评审四批）。"""
    guides = store.guides()
    lex_hits = top_guides(text, guides, k, min_score)
    pairs: list[tuple[SkillDoc, str, float]] = [
        (d, "lex", float(score(text, d))) for d in lex_hits]
    if skills_retrieval() != "hybrid" or not guides or len(pairs) >= k:
        return pairs
    sem = await _semantic_scores(text, guides, store)
    if not sem:
        return pairs
    have = {d.name for d, _, _ in pairs}
    thr = _sem_threshold()
    extras = sorted(((s, d) for d in guides
                     if (s := sem.get(d.name, 0.0))
                     >= max(thr, d.semantic_min_score
                            if d.semantic_min_score is not None else thr)
                     and d.name not in have),
                    key=lambda x: (-x[0], -x[1].priority, x[1].name))
    for s, d in extras:
        if len(pairs) >= k:
            break
        pairs.append((d, "vec", s))
    return pairs


# ── 渲染 ─────────────────────────────────────────────────────────────────────

def _dependency_ref_map(doc: SkillDoc, capability_refs=None) -> dict[str, str] | None:
    """Resolve every declared semantic dependency to exactly one request ref.

    ``None`` means the document is blocked for this request. Multiple refs for
    one semantic intent are also blocked: choosing an owner is a routing decision,
    not something a knowledge renderer may guess.
    """
    if not doc.capability_dependencies:
        return {}
    if capability_refs is None:
        return None
    by_intent: dict[str, list[str]] = {}
    for pair, ref in capability_refs.items():
        if (not isinstance(pair, tuple) or len(pair) != 2
                or not isinstance(ref, str) or not ref):
            continue
        by_intent.setdefault(str(pair[1]), []).append(ref)
    resolved = {}
    for intent in doc.capability_dependencies:
        refs = list(dict.fromkeys(by_intent.get(intent, ())))
        if len(refs) != 1:
            return None
        resolved[intent] = refs[0]
    return resolved


def _render_doc(doc: SkillDoc, capability_refs=None) -> str | None:
    dependency_refs = _dependency_ref_map(doc, capability_refs)
    if dependency_refs is None:
        return None
    knowledge = doc.knowledge
    for intent, ref in dependency_refs.items():
        knowledge = re.sub(
            rf"(?<![A-Za-z0-9_.-]){re.escape(intent)}(?![A-Za-z0-9_.-])",
            ref, knowledge)
    shots = _render_few_shots(doc.few_shots, capability_refs)
    return knowledge + (f"\n\n{shots}" if shots else "")


def render_skills_block(policies: list[SkillDoc], guides: list[SkillDoc],
                        budget: int = SKILL_BUDGET, capability_refs=None
                        ) -> tuple[str, list[SkillDoc], list[SkillDoc]]:
    """policies 常驻在前（小而全量，治理层控总量）；guides **按调用方传入顺序**注入与
    裁剪——plan_skills 传的是检索相关度序（2026-07-27 四批：此前按 priority 重排，
    边缘语义共召回的高 priority guide 会排到强相关 guide 前面放大干扰，且预算裁剪
    裁掉的反而是更相关的；priority 只在检索同分时定序）。
    返回 (注入块, 实际注入的 guides, 超预算被裁的 guides)——名单必须反映真实注入，
    「说注入了实际被裁」会让 badcase 归因说谎（2026-07-26 修）。"""
    if not policies and not guides:
        return "", [], []
    parts = ["== 规划知识（按需注入）=="]
    used = len(parts[0])
    for d in policies:
        rendered = _render_doc(d, capability_refs)
        if rendered is None:
            logger.info("skill policy %s 的 capability_dependencies 本轮不完整——不注入",
                        d.name)
            continue
        parts.append(rendered)
        used += len(rendered)
    injected, clipped = [], []
    for d in guides:
        rendered = _render_doc(d, capability_refs)
        if rendered is None:
            logger.info("skill guide %s 的 capability_dependencies 本轮不完整——不注入",
                        d.name)
            continue
        if used + len(rendered) > budget:
            logger.info("skill %s 超预算被裁（used=%d）", d.name, used)
            clipped.append(d)
            continue
        parts.append(rendered)
        used += len(rendered)
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


async def plan_skills(text: str, capability_refs=None) -> tuple[str, list[str], str]:
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

    def _tag(name: str) -> str:
        ch, s = tags[name]
        return f"@lex:{int(s)}" if ch == "lex" else f"@vec:{s:.2f}"

    tags = {d.name: (ch, s) for d, ch, s in pairs}
    if mode == "shadow":
        return mode, [f"{mode}:{d.name}{_tag(d.name)}" for d, _, _ in pairs], ""
    policies = store.policies()
    blocked_policies = [d for d in policies
                        if _dependency_ref_map(d, capability_refs) is None]
    policies = [d for d in policies if d not in blocked_policies]
    blocked_guides = [d for d, _, _ in pairs
                      if _dependency_ref_map(d, capability_refs) is None]
    available_guides = [d for d, _, _ in pairs if d not in blocked_guides]
    block, injected, clipped = render_skills_block(
        policies, available_guides, capability_refs=capability_refs)
    names = [f"{mode}:{d.name}{_tag(d.name)}" for d in injected]
    names += [f"{mode}:{d.name}{_tag(d.name)}!clipped" for d in clipped]
    names += [f"{mode}:{d.name}" for d in policies]
    names += [f"{mode}:{d.name}{_tag(d.name)}!capability-blocked"
              for d in blocked_guides]
    names += [f"{mode}:{d.name}!capability-blocked" for d in blocked_policies]
    return mode, names, block


def render_for_names(names: list[str] | None, capability_refs=None) -> str:
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
        if (mode not in ("canary", "full") or not rest
                or rest.endswith(("!clipped", "!capability-blocked"))):
            continue
        wanted.append(rest.split("@", 1)[0])
    if not wanted:
        return ""
    docs = {d.name: d for d in default_store().load()}
    policies = [docs[n] for n in wanted if n in docs and docs[n].type == "policy"]
    guides = [docs[n] for n in wanted if n in docs and docs[n].type == "guide"]
    block, _, _ = render_skills_block(
        policies, guides, capability_refs=capability_refs)
    return block


def _active_docs_for_names(names: list[str] | None,
                           store: SkillStore | None = None) -> list[SkillDoc]:
    """只还原实际注入的 full/canary 资产；shadow/off/!clipped 不得暗中生效。"""
    wanted = []
    for tagged in names or []:
        mode, sep, rest = str(tagged).partition(":")
        if (not sep or mode not in {"canary", "full"} or not rest
                or rest.endswith(("!clipped", "!capability-blocked"))):
            continue
        wanted.append(rest.split("@", 1)[0])
    docs = {d.name: d for d in (store or default_store()).load()}
    return [docs[name] for name in dict.fromkeys(wanted) if name in docs]


def apply_plan_repairs(plan, text: str, skill_names: list[str] | None,
                       store: SkillStore | None = None) -> list[str]:
    """执行已注入 skill 的声明式窄归一，并返回可观测 effect 名单。

    安全边界：只连接计划里**已经存在且唯一**的生产/消费步骤；仅在声明触发词命中、
    目标槽没有真实值也没有指向已声明生产步的有效引用时，补
    `slot_refs + depends_on`。不新增 intent，不覆盖用户真值，
    多生产者/多消费者不猜。安全、权限、确认仍由后面的硬层裁决。
    """
    effects: list[str] = []
    steps = list(getattr(plan, "steps", None) or [])
    for doc in _active_docs_for_names(skill_names, store):
        for repair in doc.plan_repairs:
            if not any(trigger in (text or "") for trigger in repair.trigger_any):
                continue
            producers = [s for s in steps if getattr(s, "intent", "") == repair.producer_intent]
            consumers = [s for s in steps if getattr(s, "intent", "") == repair.consumer_intent]
            if len(producers) != 1 or len(consumers) != 1:
                continue
            producer, consumer = producers[0], consumers[0]
            real_value = str((getattr(consumer, "slots", None) or {}).get(repair.slot) or "").strip()
            existing_ref = str((getattr(consumer, "slot_refs", None) or {}).get(repair.slot) or "").strip()
            # 真栈出现过 `{"item":"item"}`：非空 token 不等于引用。声明已经
            # 给出唯一 producer，只有指向它 `.data.*` 的值才能阻止归一；
            # 不指向它的字符串是模型格式噪声，不是用户槽位值。
            valid_existing_ref = existing_ref.startswith(f"{producer.id}.data.")
            if real_value or valid_existing_ref or producer.id == consumer.id:
                continue
            consumer.slot_refs[repair.slot] = f"{producer.id}.{repair.source_path}"
            if producer.id not in consumer.depends_on:
                consumer.depends_on.append(producer.id)
            effect = (f"{doc.name}:{repair.kind}:{repair.producer_intent}->"
                      f"{repair.consumer_intent}.{repair.slot}")
            effects.append(effect)
            logger.info("skill plan repair applied: %s", effect)
    return effects
