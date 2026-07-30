"""范例库 Exemplar Store（M5 P1，数据飞轮的核心新机制）。

设计 `docs/design/2026-07-28-intent-accuracy-data-flywheel.md` §5-P1；契约
`skills/exemplars/README.md`（唯一真相源）。

**它解决什么**：在此之前，修一个落域 badcase 的标准产物是**正则**（route_hints
`policy=replace` 在 LLM 之后改写整条计划）——规则只进不出、每加一条全局耦合加深一分。
范例库把标准产物换成**数据**：一条 `话术 → 正确落域` 的记录，检索后作为 few-shot 影响
LLM 判断。

**与 route_hints 的本质区别**（也是它劫持面远小于 hint 的原因）：
- route_hints 在 LLM **之后**、**硬改写**计划——模型判对了也会被踩掉；
- exemplars 在 LLM **之前**、只进 prompt——模型看完仍可自行决定。**范例错了只是噪声，
  hint 错了是事故。** 所以它是权威链的**最软层**（在 PlanningGuide 之下）。

机制整体骑 `skills.py` 现成范式（RFC §6「复用不重建」）：hybrid 双通道检索（词法
bigram Dice + 语义余弦，共享 `embedding.py` 出口与冷却）、fail-open、预算硬帽、
`plan.exemplars` obs 归因（对齐 `plan.skills` 契约）、T2 再规划与挂起恢复继承。

- `EXEMPLARS_MODE`：full=检索并注入（默认）｜shadow=只检索记录不注入（A/B 研究档）｜
  off=完全关闭。每轮实时读（热翻转）。
- 阈值不拍脑袋：`EXEMPLAR_LEX_THRESHOLD` / `EXEMPLAR_SEM_THRESHOLD` 的默认值由
  `test/eval_exemplars.py --scan` 的召回/噪声扫描拍板（同 skills 0.40 的来历）。
- 向量**后台预热**：范例条数是百量级，逐轮补齐会让语义通道几十轮才生效；首次检索时
  起一个后台任务分批填满（失败即停、下轮再起）。预热未完成不影响可用性——已填的参与
  语义、没填的只走词法（部分可用优于全有全无）。
"""
from __future__ import annotations

import asyncio
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
from .skills import env_float, env_int

logger = logging.getLogger("cloud.exemplars")

EXEMPLAR_BUDGET = env_int("EXEMPLAR_BUDGET", 700, min_val=0)   # 注入块字符预算（硬帽）
EXEMPLAR_TOP_K = env_int("EXEMPLAR_TOP_K", 3, min_val=1)       # 单轮注入条数上限

_RESCAN_S = 30.0            # 目录重扫最小间隔（与 skills 同档，容器内投文件 30s 生效）
_WARM_BATCH = 8             # 后台预热每批文本数（provider 批量上限保守取值）
_WARM_GAP_S = 0.05          # 批间让出事件循环，预热绝不与在途规划抢
_WORD_RE = re.compile(r"[一-鿿A-Za-z0-9]")

# 文件顶层键白名单：未知键=大概率拼写错误，静默忽略会让作者以为范例生效了（同 skills）。
_KNOWN_FILE_KEYS = {"domain", "exemplars", "version"}
# 目录下的非范例文件（评测/治理产物，不是 <domain>.yaml）。不排除的话每轮重扫都会为它
# 打两条 warning（未知顶层字段 + 缺 exemplars），30s 一次刷满日志。
_RESERVED_FILES = {"boundaries.yaml"}
_KNOWN_ITEM_KEYS = {"id", "text", "plan", "source", "added", "tags", "note"}
# source 封闭集：范例的**来路**是治理信息（哪些是死资产盘活、哪些是真实 badcase 转化），
# 自由文本会让 P2 的「范例来源结构」统计立刻失效。
SOURCES = ("manifest", "trace", "manual")


@dataclass(frozen=True)
class Exemplar:
    eid: str                # "<domain>#<1-based 序号>"：obs 归因用的稳定短标识
    domain: str
    text: str
    plan: tuple             # tuple[dict]：[{agent, intent, slots}]（slots 是骨架，可空）
    source: str             # manifest | trace | manual
    added: str = ""
    tags: tuple = ()
    path: str = ""

    def intents(self) -> list[str]:
        return [str(s.get("intent") or "") for s in self.plan if s.get("intent")]


def _chars(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if _WORD_RE.match(ch))


def _bigrams(text: str) -> set:
    s = _chars(text)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def default_root() -> Path:
    """范例库根目录。跟随 SKILLS_DIR（容器里 skills/ 是只读挂载，范例同挂载即生效）。"""
    override = os.getenv("EXEMPLARS_DIR", "").strip()
    if override:
        return Path(override)
    base = os.getenv("SKILLS_DIR", "").strip()
    return Path(base or Path(__file__).resolve().parents[2] / "skills") / "exemplars"


# ── 加载 ─────────────────────────────────────────────────────────────────────

class ExemplarStore:
    """文件系统加载器：skills/exemplars/<domain>.yaml，mtime 热更新 + last-known-good。

    容错姿态与 SkillStore 逐条对齐——运行时保**可用性**（坏文件跳过/沿用上一版、绝不
    崩规划），整洁由 `test/eval_exemplars.py` 的硬失败车道保证（坏文件到不了主干）。
    """

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else default_root()
        self._items: list[Exemplar] = []
        self._mtimes: dict[str, float] = {}
        self._last_scan = 0.0
        self._vecs: dict[str, tuple[float, ...]] = {}   # text → 向量（按文本键，重排不失效）
        self._vec_model = ""
        self._last_good: dict[str, list[Exemplar]] = {}  # path → 上一版好文件的条目
        self._warm_task: asyncio.Task | None = None
        self._idf: dict[str, float] | None = None

    def idf(self) -> dict[str, float]:
        """词法权重表（随 load 失效重算——投一个范例文件，权重当轮就跟上）。"""
        if self._idf is None:
            self._idf = build_idf(self.load())
        return self._idf

    # -- 加载 --
    def load(self, force: bool = False) -> list[Exemplar]:
        now = time.monotonic()
        if not force and self._items and now - self._last_scan < _RESCAN_S:
            return self._items
        self._last_scan = now
        paths = ([p for p in sorted(self.root.glob("*.yaml"))
                  if p.name not in _RESERVED_FILES] if self.root.is_dir() else [])
        mtimes = {}
        for p in paths:
            try:
                mtimes[str(p)] = p.stat().st_mtime
            except OSError:
                continue
        if not force and mtimes == self._mtimes and self._items:
            return self._items
        items: list[Exemplar] = []
        for p in paths:
            try:
                rows = self._parse(p)
            except Exception as e:      # 任何单文件异常都不许崩规划（fail-open 兜底闸）
                logger.warning("exemplar %s 解析异常，跳过: %s", p.name, e)
                rows = None
            if rows is None:
                rows = self._last_good.get(str(p))
                if rows is not None:
                    logger.warning("exemplar %s 本次加载失败，沿用上一版（LKG）", p.name)
            if not rows:
                continue
            self._last_good[str(p)] = rows
            items.extend(rows)
        self._items, self._mtimes = items, mtimes
        # 文件变了：向量缓存与 IDF 权重整体失效（向量按 text 键，未改动的条目下轮预热回填）
        self._vecs, self._vec_model, self._idf = {}, "", None
        if self._warm_task is not None and not self._warm_task.done():
            self._warm_task.cancel()
        self._warm_task = None
        return self._items

    @staticmethod
    def _parse(path: Path) -> list[Exemplar] | None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            logger.warning("exemplar %s 顶层必须是映射（实际 %s），跳过",
                           path.name, type(raw).__name__)
            return None
        unknown = set(raw) - _KNOWN_FILE_KEYS
        if unknown:
            logger.warning("exemplar %s 含未知顶层字段 %s——已忽略，检查拼写",
                           path.name, sorted(unknown))
        domain = str(raw.get("domain") or path.stem).strip()
        rows = raw.get("exemplars")
        if not isinstance(rows, list):
            logger.warning("exemplar %s 缺 exemplars 列表，跳过", path.name)
            return None
        out: list[Exemplar] = []
        for i, r in enumerate(rows, 1):
            item = ExemplarStore._parse_item(r, domain, i, path)
            if item is not None:
                out.append(item)
        return out

    @staticmethod
    def _parse_item(r, domain: str, idx: int, path: Path) -> Exemplar | None:
        if not isinstance(r, dict):
            logger.warning("exemplar %s#%d 不是映射，跳过", path.name, idx)
            return None
        bad = set(r) - _KNOWN_ITEM_KEYS
        if bad:
            logger.warning("exemplar %s#%d 含未知字段 %s——已忽略", path.name, idx, sorted(bad))
        text = str(r.get("text") or "").strip()
        steps = ExemplarStore._parse_plan(r.get("plan"))
        if not text or not steps:
            logger.warning("exemplar %s#%d 缺 text 或 plan（须至少一步且带 intent），跳过",
                           path.name, idx)
            return None
        source = str(r.get("source") or "manual").strip().lower()
        if source not in SOURCES:
            logger.warning("exemplar %s#%d source=%r 非法（%s）——按 manual 处理",
                           path.name, idx, source, "|".join(SOURCES))
            source = "manual"
        tags_raw = r.get("tags") or []
        if not isinstance(tags_raw, list):   # 写成字符串会被逐字符迭代成噪声 tag
            logger.warning("exemplar %s#%d tags 必须是列表——忽略", path.name, idx)
            tags_raw = []
        return Exemplar(
            eid=str(r.get("id") or f"{domain}#{idx}"), domain=domain, text=text,
            plan=tuple(steps), source=source, added=str(r.get("added") or ""),
            tags=tuple(str(t) for t in tags_raw), path=str(path))

    @staticmethod
    def _parse_plan(raw) -> list[dict]:
        """plan 规范化：只收 {agent,intent,slots}，intent 必填。多余键丢弃——注入块是
        给 LLM 看的形态示范，混进 endpoint/trace_id 之类只会稀释信号。"""
        if not isinstance(raw, list):
            return []
        steps = []
        for s in raw:
            if not isinstance(s, dict):
                continue
            intent = str(s.get("intent") or "").strip()
            if not intent:
                return []                 # 半条计划比没有更糟：整条范例作废
            slots = s.get("slots") if isinstance(s.get("slots"), dict) else {}
            steps.append({"agent": str(s.get("agent") or "").strip(),
                          "intent": intent, "slots": slots})
        return steps

    # -- 语义向量（查询顺带捎带 + 后台预热） --
    async def query_vec(self, text: str) -> tuple[float, ...] | None:
        """embed query，**顺带捎带**最多 `_WARM_BATCH-1` 条尚未缓存的范例向量。

        捎带不是优化是必需：只 embed query 的话，冷启动第一轮 `_vecs` 全空、语义通道
        等于不存在，得等后台预热跑完才生效——「刚重启的 planner 前几轮悄悄少一条通道」
        是那种永远查不出来的行为漂移。捎带让第一轮就有语义可用，后台预热只负责补长尾。"""
        pending = [e.text for e in self._items
                   if e.text not in self._vecs and e.text != text]
        batch = [text] + list(dict.fromkeys(pending))[:_WARM_BATCH - 1]
        out = await _embedding.embed_texts(batch, _embed_timeout())
        if out is None:
            return None
        vecs, model = out
        if model and self._vec_model and model != self._vec_model:
            # 网关换了 embedding 模型：跨模型比余弦是无意义的，缓存整体作废重预热
            logger.warning("embedding 模型由 %s 换为 %s——范例向量整体重建",
                           self._vec_model, model)
            self._vecs = {}
        self._vec_model = model or self._vec_model
        self._vecs.update(zip(batch[1:], vecs[1:]))
        return vecs[0]

    async def warm_blocking(self, max_batches: int = 64) -> int:
        """**等**向量填满（评测/CLI 用；服务侧用非阻塞的 warm()）。

        为什么必须有这个：后台预热依赖一个长命事件循环。评测进程跑几十轮就退出，
        语义通道实际只覆盖到零星几条向量——**A/B 会系统性低估语义档**，而这正是范例库
        泛化能力的来源。首轮 N3 测量就栽在这里（翻正的句子 `inj` 是空的＝那是采样方差
        不是范例的功）。返回已缓存向量数。"""
        for _ in range(max_batches):
            missing = [e.text for e in self.load() if e.text not in self._vecs]
            if not missing:
                break
            batch = list(dict.fromkeys(missing))[:_WARM_BATCH]
            out = await _embedding.embed_texts(batch, max(5.0, _embed_timeout()))
            if out is None:
                logger.warning("范例向量预热中断（Embed 不可用），已填 %d 条", len(self._vecs))
                break
            self._vecs.update(zip(batch, out[0]))
            self._vec_model = out[1] or self._vec_model
        return len(self._vecs)

    def warm(self) -> None:
        """起后台预热任务（幂等；无事件循环时静默跳过，如离线单测/CLI）。"""
        if self._warm_task is not None and not self._warm_task.done():
            return
        if not [e for e in self._items if e.text not in self._vecs]:
            return
        try:
            self._warm_task = asyncio.get_running_loop().create_task(self._warm_loop())
        except RuntimeError:
            self._warm_task = None

    async def _warm_loop(self) -> None:
        try:
            while True:
                missing = [e.text for e in self._items if e.text not in self._vecs]
                if not missing:
                    return
                batch = list(dict.fromkeys(missing))[:_WARM_BATCH]
                out = await _embedding.embed_texts(batch, _embed_timeout())
                if out is None:
                    # 网关不可用：停手（共享冷却已生效），下轮检索会重新起任务
                    return
                vecs, model = out
                if model and self._vec_model and model != self._vec_model:
                    self._vecs = {}
                self._vec_model = model or self._vec_model
                self._vecs.update(zip(batch, vecs))
                await asyncio.sleep(_WARM_GAP_S)
        except asyncio.CancelledError:
            raise
        except Exception as e:                 # 预热永远不该把规划链带崩
            logger.warning("范例向量预热异常，已停止（下轮重试）: %s", e)
        finally:
            self._warm_task = None


# ── 检索（词法 Dice + 语义余弦补位；同 skills 的「词法恒保留」姿态） ───────────

def _lex_threshold() -> float:
    """词法 Dice 下限。默认 0.34 由 `test/eval_exemplars.py --scan` 拍板：范例文本短
    （5-15 字），原始 bigram 交集数受长度支配，故用 Dice 归一到 [0,1] 与余弦同量纲。
    钳 (0,1]：0 等于全量放行（skills 的 min_score 钳 0 教训同款）。"""
    return env_float("EXEMPLAR_LEX_THRESHOLD", 0.34, min_val=0.01, max_val=1.0)


def _sem_threshold() -> float:
    """语义余弦下限。默认 0.65 由同一份扫描拍板（166 例探针，词法档固定 0.34）：
    0.70→0.65 是 +8 hit / +2 miss（4:1，划算），0.65→0.62 是 +4/+4（1:1，不再划算）；
    0.62→0.60 更是 +0 hit / +1 miss 的纯亏。相对纯词法：hit 63→81、miss 7→10。
    比 skills 的 0.40 高得多是应该的——skills 比「话术 vs guide 描述」跨文体，
    范例比「话术 vs 话术」同文体，余弦基线整体抬高，照抄 0.40 会把整个语料召回成噪声。"""
    return env_float("EXEMPLAR_SEM_THRESHOLD", 0.65, min_val=0.0, max_val=1.0)


def _embed_timeout() -> float:
    return env_float("EXEMPLAR_EMBED_TIMEOUT", 1.0, min_val=0.1)


def build_idf(items: list[Exemplar]) -> dict[str, float]:
    """按语料自身算 bigram IDF。

    **为什么必须加权**：范例文本很短（5-15 字），裸 Dice 会被功能词 bigram 支配——
    首轮扫描实测「请问现在是什么时间」靠「现在」「什么」检回了 vision 范例。
    另一条路是维护停用词表，但那正是这一期要消灭的东西（手工规则、只进不出、
    每加一个词全局耦合深一分）；**IDF 是语料自己长出来的权重**，加范例即自动重算。
    """
    n = max(1, len(items))
    df: dict[str, int] = {}
    for e in items:
        for g in _bigrams(e.text):
            df[g] = df.get(g, 0) + 1
    return {g: math.log(1.0 + n / (1.0 + c)) for g, c in df.items()}


def _idf_w(g: str, idf: dict[str, float], default: float) -> float:
    # 语料里没见过的 bigram 给最高权重：query 里全是陌生词时**不该**判成强匹配
    return idf.get(g, default)


def lex_score(text: str, ex: Exemplar, idf: dict[str, float] | None = None,
              hi: float | None = None) -> float:
    """IDF 加权 Dice = 2·Σw(A∩B) / (Σw(A)+Σw(B))，A/B 为 query 与范例文本的 bigram 集。
    idf=None 时退化为裸 Dice（单测/离线诊断用）。hi=陌生 bigram 的权重（idf 最大值）；
    批量打分时由调用方算一次传入——每次调用都 max 全表是 O(V)，一轮 200 条就是 200 次全表扫描。"""
    a, b = _bigrams(text), _bigrams(ex.text)
    if not a or not b:
        return 0.0
    if idf is None:
        return 2.0 * len(a & b) / (len(a) + len(b))
    if hi is None:
        hi = max(idf.values(), default=1.0)
    wa = sum(_idf_w(g, idf, hi) for g in a)
    wb = sum(_idf_w(g, idf, hi) for g in b)
    inter = sum(_idf_w(g, idf, hi) for g in a & b)
    return 2.0 * inter / (wa + wb) if (wa + wb) else 0.0


def top_lexical(text: str, items: list[Exemplar], k: int = EXEMPLAR_TOP_K,
                min_score: float | None = None,
                idf: dict[str, float] | None = None) -> list[tuple[Exemplar, float]]:
    thr = _lex_threshold() if min_score is None else min_score
    table = build_idf(items) if idf is None else idf
    hi = max(table.values(), default=1.0)
    hits = [(e, s) for e in items if (s := lex_score(text, e, table, hi)) >= thr]
    hits.sort(key=lambda x: (-x[1], x[0].eid))
    return hits[:k]


def retrieval_mode() -> str:
    mode = os.getenv("EXEMPLARS_RETRIEVAL", "hybrid").strip().lower()
    return mode if mode in ("lexical", "hybrid") else "hybrid"


async def retrieve(text: str, store: ExemplarStore, k: int = EXEMPLAR_TOP_K
                   ) -> list[tuple[Exemplar, str, float]]:
    """→ [(范例, 通道, 分数)]，按检索相关度降序。词法命中恒保留（高精度信号），
    hybrid 下语义只**补位**词法漏召的 paraphrase——范例库存在的全部理由就是泛化到
    没写进语料的说法，词法档等于把它退化成一张查找表。

    **同域去重在选取时生效**（不是选完再删）：同一 domain 最多进 1 条。3 条同域近义句
    挤满预算等于放弃了让模型看到**边界**的机会——「附近咖啡店」旁边摆一条 nearby 和一条
    navigation 的对照，比摆三条 nearby 有用得多；而选完再删会白白空出名额。
    两条通道不混排（Dice 与余弦不同量纲、不可直比），沿用 skills 的「词法在前语义补位」。"""
    items = store.load()
    if not items:
        return []
    picked: list[tuple[Exemplar, str, float]] = []
    domains: set[str] = set()

    def _take(cands: list[tuple[Exemplar, str, float]]) -> None:
        for e, ch, s in cands:
            if len(picked) >= k:
                return
            if e.domain in domains:
                continue
            domains.add(e.domain)
            picked.append((e, ch, s))

    # 多取候选（k*4）再去重截断：只取 k 条会让「前 k 名同域」直接饿死后面的对照项
    _take([(e, "lex", s) for e, s in top_lexical(text, items, k * 4, idf=store.idf())])
    if retrieval_mode() == "hybrid" and len(picked) < k:
        store.warm()
        qv = await store.query_vec(text)
        if qv is not None:
            thr = _sem_threshold()
            extras = []
            for e in items:
                v = store._vecs.get(e.text)     # 预热未覆盖的条目本轮不参与（部分可用）
                if v is None:
                    continue
                s = _embedding.cos(qv, v)
                if s >= thr:
                    extras.append((s, e))
            extras.sort(key=lambda x: (-x[0], x[1].eid))
            _take([(e, "vec", s) for s, e in extras])
    return picked


# ── 渲染 ─────────────────────────────────────────────────────────────────────

_BLOCK_HEAD = (
    "== 相似历史范例（落域参考）==\n"
    "以下是与本次话术相似的历史案例及其正确落域，**仅供参考不是规则**："
    "与上方「规划知识」冲突时以规划知识为准，与用户实际所说不符时以用户原话为准。")


def _render_one(e: Exemplar) -> str:
    plan = json.dumps([{k: v for k, v in s.items() if v not in ("", {}, None)}
                       for s in e.plan], ensure_ascii=False, separators=(",", ":"))
    return f"- 用户：『{e.text}』→ {plan}"


def render_block(items: list[Exemplar], budget: int | None = None
                 ) -> tuple[str, list[Exemplar], list[Exemplar]]:
    """→ (注入块, 实际注入, 超预算被裁)。名单必须反映**真实注入**——「说注入了实际
    被裁」会让 badcase 归因说谎（skills 2026-07-26 教训同款）。

    budget 默认在**调用时**读模块常量而不是写进默认参数（默认参数在 def 期求值，
    改了全局也不生效——这类「以为改了其实没改」的坑一次就够了）。"""
    if not items:
        return "", [], []
    budget = EXEMPLAR_BUDGET if budget is None else budget
    parts, used = [_BLOCK_HEAD], len(_BLOCK_HEAD)
    injected, clipped = [], []
    for e in items:
        line = _render_one(e)
        if used + len(line) + 1 > budget:
            logger.info("exemplar %s 超预算被裁（used=%d）", e.eid, used)
            clipped.append(e)
            continue
        parts.append(line)
        used += len(line) + 1
        injected.append(e)
    if not injected:                 # 一条都没进：不要留一个只有抬头的空块污染 prompt
        return "", [], clipped
    return "\n".join(parts), injected, clipped


# ── 规划轮入口 ───────────────────────────────────────────────────────────────

_default_store: ExemplarStore | None = None


def default_store() -> ExemplarStore:
    global _default_store
    if _default_store is None:
        _default_store = ExemplarStore()
    return _default_store


def mode() -> str:
    m = os.getenv("EXEMPLARS_MODE", "full").strip().lower()
    return m if m in ("off", "shadow", "full") else "full"


async def plan_exemplars(text: str) -> tuple[str, list[str], str]:
    """规划轮入口：→ (mode, 归因名单, 注入块)。

    名单契约（对齐 `plan.skills`）：`<mode>:<eid>@lex:0.55`／`@vec:0.71`，超预算被裁
    加 `!clipped`。shadow 档只检索记录、块为空——零行为变化的 A/B 对照。"""
    m = mode()
    if m == "off":
        return m, [], ""
    try:
        pairs = await retrieve(text, default_store())
    except Exception as e:            # 范例层任何异常都不许影响规划（最软层的本分）
        logger.warning("范例检索异常，本轮跳过: %s", e)
        return m, [], ""

    def _tag(ch: str, s: float) -> str:
        return f"@lex:{s:.2f}" if ch == "lex" else f"@vec:{s:.2f}"

    tags = {e.eid: _tag(ch, s) for e, ch, s in pairs}
    if m == "shadow":
        return m, [f"{m}:{e.eid}{tags[e.eid]}" for e, _, _ in pairs], ""
    block, injected, clipped = render_block([e for e, _, _ in pairs])
    names = [f"{m}:{e.eid}{tags[e.eid]}" for e in injected]
    names += [f"{m}:{e.eid}{tags[e.eid]}!clipped" for e in clipped]
    return m, names, block


def render_for_names(names: list[str] | None) -> str:
    """T2 再规划 / 挂起恢复的范例继承（同 skills.render_for_names 姿态）：按初规划
    **实际注入**的 eid 重渲染，不重新检索——再规划的输入是观察结果不是用户话术。
    shadow 轮与被裁项本来就没进初规划上下文，同样不进再规划。"""
    if not names:
        return ""
    wanted = []
    for n in names or []:
        mode_part, _, rest = str(n).partition(":")
        if mode_part != "full" or not rest or rest.endswith("!clipped"):
            continue
        wanted.append(rest.split("@", 1)[0])
    if not wanted:
        return ""
    by_id = {e.eid: e for e in default_store().load()}
    picked = [by_id[i] for i in wanted if i in by_id]
    block, _, _ = render_block(picked)
    return block
