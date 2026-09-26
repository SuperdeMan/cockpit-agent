"""真实车型手册的只读文件索引 Provider。

单手册语料用中文字符 n-gram BM25 召回，再以章节、短语和 IDF 覆盖率重排。低相关
查询 fail closed；不依赖网络、数据库或在线 embedding。

## 查询理解（2026-09-26，collector 真实问法驱动）

手册问答的真实问法是「座椅加热有几个档位」「空调有哪些模式」「后备箱能放几个行李箱」。
v2 只按整句双字算覆盖率：问句壳切出的「有几 / 几个 / 个档」在手册里一次都不存在、IDF
最高，把正确页（排序第一）的覆盖率压到闸下，collector 562 轮手册问答 488 轮零命中。
现在每个检索变体拆成三类词，各管各的事：

- **主题词**：用户自己的内容词。闸只看它们的覆盖率——“问的东西在不在这一页”；
- **证据词**：意图扩展补的「参数 / 定期保养 / 整车尺寸参数」。计入排序覆盖率，
  不计入闸——否则「比亚迪海豹的电池容量」会被补上的证据词洗成 SU7 参数页；
- **只排序词**：答案类型词（方式 / 功能…）、症状（关不上 / 打不开）、词间接缝。

问句壳的词表只认 `runtime.question_shape`（问句判据的唯一实现），这里不另抄一份。
"""
from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
import math
from pathlib import Path
import re
import unicodedata
from typing import Any

import yaml

from agents.manual_rag.src.index_format import IndexFormatError, load_manual_package
from agents.manual_rag.src.toc import TocEntry, build_table_of_contents
from runtime import question_shape
from runtime.clause_split import split_clauses
from .base import Chunk, KnowledgeRetriever, ManualImage


_CJK_SEQUENCE_RE = re.compile(r"[\u3400-\u9fff]+")
_TOKEN_RE = re.compile(
    r"[\u3400-\u9fff]+|[a-z0-9]+(?:[._+/-][a-z0-9]+)*",
    re.IGNORECASE,
)
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._+/-][a-z0-9]+)*",
                             re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_MEASUREMENT_SPACE_RE = re.compile(
    r"(?<=\d)\s+(?=(?:km/h|bar|km|mm|cm|min|m|l|w|v|h|s)\b)",
    re.IGNORECASE,
)
_ASCII_GATE_EXEMPT = frozenset({
    "bar", "km/h", "km", "mm", "cm", "m", "l", "w", "v", "h", "s", "min",
})
# 字母 + 数字的两字符型号码（L9 / M9 / P7 / X5）：手册里没有就是别的车型，同三字符以上的
# 未知专名一样零命中。单字母（P 挡）与纯数字不受影响。
_MODEL_CODE_RE = re.compile(r"(?=[a-z]*\d)(?=\d*[a-z])[a-z0-9]{2}", re.IGNORECASE)
_MAX_IMAGE_BYTES = 640 * 1024
_MAX_IMAGE_TOTAL_BYTES = 768 * 1024
_MAX_IMAGES = 2
_VISUAL_CONTEXT_MARKERS = (
    "图标", "指示灯", "仪表", "灯亮", "亮了", "常亮", "闪烁",
)
# 灯态词之后还有话，就是前提说完了（「胎压灯亮了应该补到多少」「胎压灯亮起应该补到多少」）：
# 仪表灯语境承接主语时当分句边界用。规划器把前提并进本步槽时也是这个形态。
_LAMP_STATE_BOUNDARY_RE = re.compile(r"(?<=亮了|亮起|亮着|常亮|闪烁|闪了)(?=[^，,。；;！!？?\s]{2,})")
# 闸：主题覆盖率与质量分下限（v2 起的口径不变，变的是覆盖率只算用户自己的词）。
_MIN_QUALITY = 1.0
_MIN_TOPIC_COVERAGE = 0.42
# 排序：质量 = BM25 × 排序覆盖率^1.5。线性乘时，只缺一个关键概念、但主题词重复更多
# 的页会反超「概念全部在场」的页（充电限值「最低 50%」页压过「建议 80% / 100%」页）。
_RANK_COVERAGE_EXPONENT = 1.5
# 词间接缝：夹在两个已知双字之间、df 不到两侧较小者 1/4、且跨它的四字串全书至多出现
# 一次（「车载冰箱」的「载冰」）。四字串条件排除「车道保持」这种由高频词组成的真复合词。
_JUNCTION_DF_RATIO = 4
# 封闭虚字类（零领域词）。含其一、且整本手册一次都没出现过的双字，只可能是分词跨过
# 虚词的产物（「亮了是」的「了是」、「能开门」的「还能」），不计分也不计覆盖率。
_FUNCTION_CHARS = frozenset(
    "了的地得是有在把将对和与或及被给让从向往用以为于都也还就才又再会要能可该"
    "吗呢吧啊呀嘛么哦这那它他她我你您咱们个些几哪啥谁多很太最更挺不没别")
# 段首的介词/指代虚字（「把车窗打开」的「把车」）与段尾的语气/助词虚字（「天窗有」的
# 「窗有」）组成的双字只表达句法、不表达主题，手册里偶然出现过也不计。
_LEADING_FUNCTION_CHARS = frozenset("的了把将对在从给被让向往和与或及这那它我你您咱该每其")
_TRAILING_FUNCTION_CHARS = frozenset("了的地得吗呢吧啊呀嘛么哦是有着过")
# 可能补语的否定式（关不上 / 打不开 / 调不动）说的是“做不到”，是故障问法的答案类型；
# 手册正文几乎不会原样写出，只参与排序。
_SYMPTOM_RE = re.compile(r"[\u3400-\u9fff]不[上开了动下掉起出进住来去]")


class ManualIndexError(ValueError):
    """手册索引或检索配置不可用。"""


@dataclass(frozen=True)
class _PreparedChunk:
    raw: dict[str, Any]
    normalized_content: str
    normalized_section: str
    body_terms: Counter[str]
    section_terms: Counter[str]
    body_length: int


@dataclass(frozen=True)
class _Expansion:
    when_any: tuple[str, ...]
    require_any: tuple[str, ...]
    unless_any: tuple[str, ...]
    append: tuple[str, ...]
    drop_any: tuple[str, ...]
    standalone: bool


@dataclass(frozen=True)
class _RetrievalConfig:
    noise_phrases: tuple[str, ...]
    aliases: dict[str, tuple[str, ...]]
    expansions: tuple[_Expansion, ...]
    facet_terms: tuple[str, ...]
    foreign_vehicle_markers: tuple[str, ...]
    absent_subject_markers: tuple[str, ...]


@dataclass(frozen=True)
class _Variant:
    """一个检索变体：用户内容（已换同义词、剥壳）+ 扩展证据词。`standalone` 的扩展在
    内容剥空时自己就是问题（「这车有多长」）。"""

    content: str
    hint: str = ""
    standalone: bool = False


# 档 / 挡 在“挡位”义上通用，手册统一写“挡”；语料与查询走同一次规范化，对称折叠。
_VARIANT_FOLD = str.maketrans({"档": "挡"})


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.translate(_VARIANT_FOLD)
    text = _SPACE_RE.sub(" ", text).strip()
    return _MEASUREMENT_SPACE_RE.sub("", text)


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", _normalize(value))


def _tokens(value: str) -> list[str]:
    """中文取双字 n-gram；Latin/数字保留完整 token。"""
    result: list[str] = []
    for part in _TOKEN_RE.findall(_normalize(value)):
        if _CJK_SEQUENCE_RE.fullmatch(part):
            if len(part) == 1:
                # 单字召回噪声极高；只有整个查询就是一个字时由调用方兜底。
                continue
            result.extend(part[i:i + 2] for i in range(len(part) - 1))
        else:
            result.append(part)
    if not result:
        compact = _compact(value)
        if len(compact) == 1:
            result.append(compact)
    return result


def _alternation(words) -> str:
    return "|".join(map(re.escape, sorted({w for w in words if w},
                                          key=lambda word: (-len(word), word))))


def _mask_spans(text: str, phrases, patterns=()) -> str:
    """去掉任一短语 / 模式命中的全部字符（重叠出现取并集），连续被去掉的一段换成一个空格。

    与剥离顺序无关：「在哪里」=「在哪」∪「哪里」，「是什么意思」=「是什么」∪「什么意思」。
    逐个 replace 会让先剥的短语吃掉后一个的头（「怎么」吃掉「怎么回事」），而等长短语的
    先后又取决于集合迭代序——字符串哈希每个进程随机，同一句话会剥出不同结果。"""
    covered = [False] * len(text)
    for pattern in patterns:
        for match in pattern.finditer(text):
            covered[match.start():match.end()] = [True] * (match.end() - match.start())
    for phrase in phrases:
        start = text.find(phrase)
        while start >= 0:
            covered[start:start + len(phrase)] = [True] * len(phrase)
            start = text.find(phrase, start + 1)
    pieces: list[str] = []
    for char, drop in zip(text, covered):
        if not drop:
            pieces.append(char)
        elif not pieces or pieces[-1] != " ":
            pieces.append(" ")
    return _SPACE_RE.sub(" ", "".join(pieces)).strip()


# 问句壳：问句判据唯一实现里的封闭虚词类。“多少 / 怎么”这类原本就在检索噪声表里的
# 词在两处出现是历史（v1 噪声表先于问句判据），以本表为准的部分不在 yaml 里再加。
_SHELL_PHRASES = tuple(sorted({
    _normalize(word)
    for name in ("ENUMERATION_ASKS", "DEFINITION_ASKS", "REASON_ASKS", "CHOICE_ASKS",
                 "CAPABILITY_ASKS", "PROPERTY_ASKS", "REFERENCE_ASKS", "MANNER_ASKS",
                 "POLITE_TAILS", "QUESTION_TAILS")
    for word in getattr(question_shape, name)
    if _normalize(word)
}, key=lambda word: (-len(word), word)))
# 请求开头词（请问 / 告诉我 / 介绍一下 / 查一下…）只在句首成立，同问句判据的用法；全句遮罩
# 会把章节名词吃掉（「车辆介绍 外部介绍」的「介绍」）。
_OPENER_RE = re.compile(
    r"^(?:请|麻烦|帮我|帮忙|给我|替我|你|您)*\s*(?:"
    + _alternation(_normalize(word) for name in ("ASK_PREFIXES", "EXPLAIN_REQUESTS",
                                                  "LOOKUP_REQUESTS")
                   for word in getattr(question_shape, name))
    + ")")
# 计数 / 属性问的整段壳：头（有 / 能 / 可以 / 最多…）+ 至多两个字的动词 + 几量词 / 多大多高…
# 同问句判据的计数问结构（「后备箱能放几个行李箱」「座椅能调多高」「最多能装几个」）。
# 问句判据为了**认出**计数问允许头与「几」之间隔四个字；剥离要的是紧挨着的那一段——
# 隔四个字时「能量回收有几档」的头会从「能量」的「能」起跳，把整句吃空。
_COUNT_SHELL_RE = re.compile(
    f"(?:{_alternation(question_shape.COUNT_HEADS)})"
    r"[^，,。；;！!？?\s]{0,2}?几"
    f"[{''.join(sorted({_normalize(unit) for unit in question_shape.COUNT_UNITS}))}]")
_PROPERTY_SHELL_RE = re.compile(
    f"(?:{_alternation(question_shape.COUNT_HEADS)})"
    r"[^，,。；;！!？?\s]{0,2}?"
    f"(?:{_alternation(_normalize(word) for word in question_shape.PROPERTY_ASKS)})")


def _has_visual_context(text: str) -> bool:
    """描述仪表灯 / 图标的语境（「亮了」「指示灯」「仪表」…）。"""
    compact = _compact(text)
    return any(_compact(marker) in compact for marker in _VISUAL_CONTEXT_MARKERS)


def _interleave_ranked(lists: list[list[tuple]]) -> list[tuple]:
    """按名次交替合并几份排序（各份第 1 名、再各份第 2 名…），同一页只留第一次出现。"""
    merged: list[tuple] = []
    seen: set[int] = set()
    for depth in range(max((len(items) for items in lists), default=0)):
        for items in lists:
            if depth < len(items):
                page = items[depth][-1].raw["page_start"]
                if page not in seen:
                    seen.add(page)
                    merged.append(items[depth])
    return merged


def _string_list(rule: dict, key: str, where: str) -> tuple[str, ...]:
    values = rule.get(key) or []
    if not isinstance(values, list):
        raise ManualIndexError(f"{where}.{key} 必须是列表")
    return tuple(_normalize(item) for item in values if _normalize(item))


def _load_retrieval_config(path: Path) -> _RetrievalConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ManualIndexError(f"检索配置无法读取：{path}：{exc}") from exc
    # v2（2026-09-26）：意图扩展补的词从“主题”改为“证据”（只计排序覆盖率），并新增
    # facet_terms / foreign_vehicle_markers / absent_subject_markers 与扩展的
    # drop_any / standalone。
    if raw.get("schema_version") != 2:
        raise ManualIndexError(
            f"检索配置 schema_version 非法：{raw.get('schema_version')!r}")
    noise = _string_list(raw, "query_noise_phrases", "retrieval")
    aliases: dict[str, tuple[str, ...]] = {}
    for source, targets in (raw.get("aliases") or {}).items():
        key = _normalize(source)
        values = tuple(_normalize(item) for item in (targets or []) if _normalize(item))
        if not key or not values:
            raise ManualIndexError(f"检索同义词声明非法：{source!r}")
        aliases[key] = values
    expansions: list[_Expansion] = []
    for pos, rule in enumerate(raw.get("intent_expansions") or []):
        where = f"检索意图扩展[{pos}]"
        if not isinstance(rule, dict):
            raise ManualIndexError(f"{where} 必须是 object")
        unknown = set(rule) - {"when_any", "require_any", "unless_any", "append",
                               "drop_any", "standalone"}
        if unknown:
            raise ManualIndexError(f"{where} 未知键：{sorted(unknown)}")
        expansion = _Expansion(
            when_any=_string_list(rule, "when_any", where),
            require_any=_string_list(rule, "require_any", where),
            unless_any=_string_list(rule, "unless_any", where),
            append=_string_list(rule, "append", where),
            drop_any=_string_list(rule, "drop_any", where),
            standalone=bool(rule.get("standalone", False)),
        )
        if not expansion.when_any or not expansion.append:
            raise ManualIndexError(f"{where} 缺 when_any/append")
        if expansion.standalone and not expansion.require_any:
            # 单独成立 = 内容剥空也检索；没有主语约束的扩展（“多少”→参数）若单独成立，
            # 「这个多少钱」就会凭空检出一张参数页。
            raise ManualIndexError(f"{where} standalone 必须配 require_any")
        expansions.append(expansion)
    return _RetrievalConfig(
        noise_phrases=tuple(sorted(set(noise), key=lambda word: (-len(word), word))),
        aliases=aliases,
        expansions=tuple(expansions),
        facet_terms=tuple(sorted(set(_string_list(raw, "facet_terms", "retrieval")),
                                 key=lambda word: (-len(word), word))),
        foreign_vehicle_markers=_string_list(raw, "foreign_vehicle_markers", "retrieval"),
        absent_subject_markers=_string_list(raw, "absent_subject_markers", "retrieval"),
    )


def _validate_trusted_catalog(path: Path, document: dict[str, Any],
                              visual: dict[str, Any]) -> dict[str, Any]:
    """以 tracked 指纹表作为信任锚；索引自带 hash 只能证明自洽，不能证明获准。"""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ManualIndexError(f"手册 catalog 无法读取：{path}：{exc}") from exc
    if raw.get("schema_version") != 1 or not isinstance(raw.get("documents"), dict):
        raise ManualIndexError("手册 catalog schema_version/documents 非法")
    document_id = document["document_id"]
    trusted = raw["documents"].get(document_id)
    if not isinstance(trusted, dict):
        raise ManualIndexError(f"手册未登记为可信：{document_id}")
    for key in ("title", "publisher", "vehicle_model", "revision",
                "source_pages", "source_sha256", "content_sha256"):
        if trusted.get(key) != document.get(key):
            raise ManualIndexError(
                f"手册 catalog 指纹不一致：{key}，"
                f"expected={trusted.get(key)!r}, actual={document.get(key)!r}")
    if visual:
        expected_visual = {
            "visual_assets_sha256": visual["assets_sha256"],
            "visual_asset_count": visual["asset_count"],
        }
        for key, actual in expected_visual.items():
            if trusted.get(key) != actual:
                raise ManualIndexError(
                    f"手册 catalog 指纹不一致：{key}，"
                    f"expected={trusted.get(key)!r}, actual={actual!r}")
    return dict(trusted)


class ManualIndexRetriever(KnowledgeRetriever):
    """加载一个通过 hash 校验的真实车型手册索引。"""

    def __init__(self, index_path: str | Path, *, vehicle_model: str = "",
                 retrieval_config_path: str | Path | None = None,
                 catalog_path: str | Path | None = None):
        self.index_path = Path(index_path)
        try:
            self.package = load_manual_package(self.index_path)
            bundle = self.package.index
        except FileNotFoundError as exc:
            raise ManualIndexError(f"手册索引不存在：{self.index_path}") from exc
        except IndexFormatError as exc:
            raise ManualIndexError(str(exc)) from exc

        self.document = dict(bundle["document"])
        resources = Path(__file__).resolve().parents[2] / "resources"
        trusted_path = (Path(catalog_path) if catalog_path
                        else resources / "manual_catalog.yaml")
        self.visual_manifest = dict(self.package.visual)
        self.visual_assets = list(self.visual_manifest.get("assets") or [])
        self.catalog_entry = _validate_trusted_catalog(
            trusted_path, self.document, self.visual_manifest)
        if self.visual_manifest:
            self.document.update({
                "visual_assets_sha256": self.visual_manifest["assets_sha256"],
                "visual_asset_count": self.visual_manifest["asset_count"],
                "visual_skipped_asset_count": self.visual_manifest[
                    "skipped_asset_count"],
            })
        configured_model = _normalize(vehicle_model).replace(" ", "-")
        indexed_model = _normalize(self.document["vehicle_model"]).replace(" ", "-")
        if configured_model and configured_model != indexed_model:
            raise ManualIndexError(
                f"配置车型不一致：configured={vehicle_model!r}, "
                f"index={self.document['vehicle_model']!r}")

        config_path = (Path(retrieval_config_path) if retrieval_config_path
                       else resources / "retrieval.yaml")
        self._config = _load_retrieval_config(config_path)
        self._strip_phrases = tuple(sorted(
            {*self._config.noise_phrases, *_SHELL_PHRASES}, key=lambda word: (-len(word), word)))
        self._vehicle_aliases = {
            _normalize(item) for item in self.document["vehicle_aliases"]
            if _normalize(item)
        }
        self._strip_phrases = tuple(sorted(
            {*self._strip_phrases, *self._vehicle_aliases}, key=lambda word: (-len(word), word)))
        self._vehicle_ascii_tokens = {
            token for alias in self._vehicle_aliases
            for token in _ASCII_TOKEN_RE.findall(alias)
        }
        self._assets_by_page: dict[int, list[dict[str, Any]]] = {}
        self._visual_needles: list[tuple[str, str, dict[str, Any]]] = []
        for asset in self.visual_assets:
            self._assets_by_page.setdefault(asset["page_start"], []).append(asset)
            for alias in asset.get("aliases") or []:
                needle = _compact(alias)
                if len(needle) >= 3:
                    self._visual_needles.append((needle, "visual_alias", asset))
            caption = _compact(asset.get("caption", ""))
            # 三字正式名称（后雾灯/近光灯/位置灯/左转向/右转向）也属于受控目录，
            # 但只能在查询同时具备视觉上下文时消费，避免“后雾灯怎么打开”被图标页劫持。
            if len(caption) >= 3:
                self._visual_needles.append((caption, "visual_caption", asset))
        self._visual_needles.sort(key=lambda value: (-len(value[0]), value[2]["asset_id"]))
        for page_assets in self._assets_by_page.values():
            page_assets.sort(key=lambda item: (
                item.get("role") != "illustration",
                -(int(item.get("width", 0)) * int(item.get("height", 0))),
                item["asset_id"],
            ))

        self._chunks: list[_PreparedChunk] = []
        self._document_frequency: Counter[str] = Counter()
        self._ascii_vocabulary: set[str] = set()
        normalized_corpus: list[str] = []
        for raw in bundle["chunks"]:
            content = _normalize(raw["content"])
            section = _normalize(" > ".join(raw["section_path"]))
            body_terms = Counter(_tokens(content))
            section_terms = Counter(_tokens(section))
            prepared = _PreparedChunk(
                raw=dict(raw),
                normalized_content=content,
                normalized_section=section,
                body_terms=body_terms,
                section_terms=section_terms,
                body_length=max(1, sum(body_terms.values())),
            )
            self._chunks.append(prepared)
            self._document_frequency.update(set(body_terms) | set(section_terms))
            self._ascii_vocabulary.update(_ASCII_TOKEN_RE.findall(content))
            self._ascii_vocabulary.update(_ASCII_TOKEN_RE.findall(section))
            normalized_corpus.extend((content, section))
        self._normalized_corpus = "\n".join(normalized_corpus)
        self._normalized_corpus_compact = _SPACE_RE.sub("", self._normalized_corpus)
        self._compact_chunks = [
            _compact(chunk.normalized_section + " " + chunk.normalized_content)
            for chunk in self._chunks]
        self._substring_df_cache: dict[str, int] = {}
        self._avg_body_length = max(
            1.0,
            sum(chunk.body_length for chunk in self._chunks) / len(self._chunks),
        )
        self._validate_scope_markers()
        self._toc = build_table_of_contents(bundle["chunks"])
        self._toc_by_id = {entry.entry_id: entry for entry in self._toc}
        self._chunks_by_page = {chunk.raw["page_start"]: chunk for chunk in self._chunks}

    def _validate_scope_markers(self) -> None:
        """「别的车型」「本车没有的对象」是对这本手册的断言：若手册里其实写着它，声明就是
        错的——会把真实内容挡成零命中（「海豚」出现在「qborn 小海豚儿童安全座椅」里）。
        启动期逐条核对，矛盾即拒绝启动。"""
        contradicted = sorted(
            marker for marker in (*self._config.foreign_vehicle_markers,
                                  *self._config.absent_subject_markers)
            if _compact(marker) and _compact(marker) in self._normalized_corpus_compact)
        if contradicted:
            raise ManualIndexError(f"检索声明与手册矛盾（手册里出现了）：{contradicted}")

    @property
    def vehicle_model(self) -> str:
        return self.document["vehicle_model"]

    @property
    def revision(self) -> str:
        return self.document["revision"]

    def table_of_contents(self) -> list[TocEntry]:
        """获准索引还原出的目录叶子（供目录路由做封闭集合选择）。"""
        return list(self._toc)

    # ── 查询理解 ────────────────────────────────────────────────────────────

    def _strip_noise(self, value: str) -> str:
        return _mask_spans(value, self._strip_phrases,
                           (_OPENER_RE, _COUNT_SHELL_RE, _PROPERTY_SHELL_RE))

    def _query_variants(self, query: str) -> list[_Variant]:
        original = _normalize(query)
        variants = [original]
        # 同义词是受控声明，不让模型动态改写查询。组合只允许原问法中**不重叠**的
        # source spans；这样“刹车油+换”可同时变成“制动液+更换”，而“推荐胎压”与
        # 内含的“胎压”不会级联成畸形词。
        replacements: list[tuple[int, int, str]] = []
        for source, targets in self._config.aliases.items():
            start = original.find(source)
            if start < 0:
                continue
            for target in targets:
                candidate = original[:start] + target + original[start + len(source):]
                if candidate not in variants:
                    variants.append(candidate)
                replacements.append((start, start + len(source), target))
                if len(variants) >= 24:
                    break
            if len(variants) >= 24:
                break
        for left, right in combinations(replacements, 2):
            if not (left[1] <= right[0] or right[1] <= left[0]):
                continue
            candidate = original
            for start, end, target in sorted((left, right), reverse=True):
                candidate = candidate[:start] + target + candidate[end:]
            if candidate not in variants:
                variants.append(candidate)
            if len(variants) >= 24:
                break
        expanded: list[_Variant] = []
        for rule in self._config.expansions:
            if not any(marker in original for marker in rule.when_any):
                continue
            if rule.require_any and not any(
                    marker in original for marker in rule.require_any):
                continue
            if any(marker in original for marker in rule.unless_any):
                continue
            hint = " ".join(rule.append)
            for variant in variants:
                content = variant
                for subject in rule.drop_any:
                    content = content.replace(subject, " ")
                candidate = _Variant(content, hint, rule.standalone)
                if candidate not in expanded:
                    expanded.append(candidate)
                if len(expanded) >= 24:
                    break
            if len(expanded) >= 24:
                break
        # 一旦问法明确要“规格/周期”证据，就只按带意图的变体排；同时保留基础变体
        # 会让高频主题页靠重复词压过真正回答该维度的页。
        planned = expanded or [_Variant(variant) for variant in variants]
        cleaned: list[_Variant] = []
        for variant in planned:
            candidate = _Variant(self._strip_noise(variant.content), variant.hint,
                                 variant.standalone)
            if (candidate.content or candidate.standalone) and candidate not in cleaned:
                cleaned.append(candidate)
        return cleaned

    def _substring_df(self, needle: str) -> int:
        cached = self._substring_df_cache.get(needle)
        if cached is None:
            cached = sum(1 for chunk in self._compact_chunks if needle in chunk)
            self._substring_df_cache[needle] = cached
        return cached

    def _content_terms(self, text: str) -> tuple[Counter[str], set[str], set[str]]:
        """内容双字 + 其中的句法产物 + 词间接缝。

        产物：段首介词虚字 / 段尾语气虚字组成的双字；整本手册不存在且含虚字的双字；
        整本手册不存在、左字是前一个真实双字之尾、右字是后一个真实双字之头（或句尾悬挂）
        的双字（「把车窗打开」的「窗打」、「手机壳厚度」的「壳厚」）。整段都不在手册里的
        双字（机油、油箱、海豹）不是产物——零命中闸靠的正是它们。
        """
        terms: Counter[str] = Counter()
        artifacts: set[str] = set()
        junctions: set[str] = set()
        for part in _TOKEN_RE.findall(_normalize(text)):
            if not _CJK_SEQUENCE_RE.fullmatch(part):
                terms[part] += 1
                continue
            if len(part) == 1:
                continue
            grams = [part[i:i + 2] for i in range(len(part) - 1)]
            df = [self._document_frequency.get(gram, 0) for gram in grams]
            for i, gram in enumerate(grams):
                terms[gram] += 1
                if ((i == 0 and gram[0] in _LEADING_FUNCTION_CHARS)
                        or (i + 1 == len(grams) and gram[1] in _TRAILING_FUNCTION_CHARS)):
                    artifacts.add(gram)
                    continue
                if df[i]:
                    if (0 < i < len(grams) - 1 and df[i - 1] and df[i + 1]
                            and df[i] * _JUNCTION_DF_RATIO < min(df[i - 1], df[i + 1])
                            and self._substring_df(part[i - 1:i + 3]) <= 1):
                        junctions.add(gram)
                    continue
                if any(char in _FUNCTION_CHARS for char in gram):
                    artifacts.add(gram)
                    continue
                if i > 0 and df[i - 1] and (i + 1 == len(grams) or df[i + 1]):
                    artifacts.add(gram)
        if not terms:
            compact = _compact(text)
            if len(compact) == 1 and compact not in _FUNCTION_CHARS:
                terms[compact] += 1
        return terms, artifacts, junctions

    def _variant_terms(self, variant: _Variant) -> tuple[Counter[str], Counter[str], Counter[str]]:
        """一个检索变体拆成 主题词 / 扩展证据词 / 只排序词。"""
        content = variant.content
        soft_parts: list[str] = []
        for facet in self._config.facet_terms:
            if facet in content:
                soft_parts.append(facet)
                content = content.replace(facet, " ")
        soft_parts.extend(match.group(0) for match in _SYMPTOM_RE.finditer(content))
        content = _SYMPTOM_RE.sub(" ", content)
        terms, artifacts, junctions = self._content_terms(content)
        topic = Counter({term: count for term, count in terms.items()
                         if term not in artifacts and term not in junctions})
        evidence = Counter(_tokens(variant.hint)) if variant.hint else Counter()
        soft = Counter(term for part in soft_parts for term in _tokens(part))
        for junction in junctions:
            soft[junction] += terms[junction]
        if not topic and variant.standalone:
            # 剥掉问法壳后只剩「多长 / 多重」这类整车维度：声明了整车主语的规格扩展
            # 本身就是问题的主题。「这个多少钱」剥完同样没有主题，不能拿「参数」凑一页。
            topic, evidence = evidence, Counter()
        return topic, evidence, soft

    # ── 打分 ────────────────────────────────────────────────────────────────

    def _idf(self, term: str) -> float:
        count = self._document_frequency.get(term, 0)
        total = len(self._chunks)
        return math.log(1.0 + (total - count + 0.5) / (count + 0.5))

    def _bm25(self, chunk: _PreparedChunk, topic: Counter[str], evidence: Counter[str],
              soft: Counter[str]) -> tuple[float, float, float]:
        """BM25 分数 + 主题覆盖率（闸）+ 排序覆盖率（主题 + 证据）。"""
        k1, b = 1.2, 0.75
        body_score = 0.0
        section_score = 0.0
        topic_total = topic_matched = rank_total = rank_matched = 0.0
        merged: dict[str, tuple[int, str]] = {}
        for kind, bag in (("soft", soft), ("evidence", evidence), ("topic", topic)):
            for term, qtf in bag.items():
                merged[term] = (qtf, kind)
        for term, (qtf, kind) in merged.items():
            idf = self._idf(term)
            body_tf = chunk.body_terms.get(term, 0)
            section_tf = chunk.section_terms.get(term, 0)
            present = bool(body_tf or section_tf)
            if kind == "topic":
                topic_total += idf
                topic_matched += idf if present else 0.0
            if kind != "soft":
                rank_total += idf
                rank_matched += idf if present else 0.0
            if body_tf:
                denominator = body_tf + k1 * (
                    1.0 - b + b * chunk.body_length / self._avg_body_length)
                body_score += idf * (body_tf * (k1 + 1.0) / denominator) * min(qtf, 2)
            if section_tf:
                section_score += idf * min(section_tf, 2) * min(qtf, 2)
        topic_coverage = topic_matched / topic_total if topic_total else 0.0
        rank_coverage = rank_matched / rank_total if rank_total else 0.0
        return body_score + 1.8 * section_score, topic_coverage, rank_coverage

    def _prepare_variants(self, variants: list[_Variant]) -> list[tuple]:
        """每个变体的词集只算一次，不随逐页打分重算；没有主题词的变体不参与。"""
        prepared = []
        for variant in variants:
            topic, evidence, soft = self._variant_terms(variant)
            if not topic:
                continue
            phrases = [part for part in _CJK_SEQUENCE_RE.findall(
                f"{variant.content} {variant.hint}") if len(part) >= 3]
            prepared.append((topic, evidence, soft, _compact(variant.content), phrases))
        return prepared

    def _score(self, prepared: list[tuple], chunk: _PreparedChunk) -> tuple[float, float, bool]:
        """(质量, 主题覆盖率, 章节命中)。章节命中 = 某个变体的主题词出现在这一页的章节路径里；
        主题词只在正文里撞上的页（「运动模式到底在哪切换」撞上讲特殊路况的页）不算有把握。"""
        best_quality = 0.0
        best_coverage = 0.0
        section_hit = False
        haystack = _compact(chunk.normalized_section + " " + chunk.normalized_content)
        for topic, evidence, soft, needle, phrases in prepared:
            section_hit = section_hit or any(term in chunk.section_terms for term in topic)
            raw, coverage, rank_coverage = self._bm25(chunk, topic, evidence, soft)
            if len(needle) >= 2 and needle in haystack:
                raw += 3.0
            for phrase in phrases:
                if phrase in chunk.normalized_content:
                    raw += min(5.0, 1.0 + len(phrase) * 0.6)
                elif phrase in chunk.normalized_section:
                    raw += min(6.0, 1.5 + len(phrase) * 0.7)
            # 重复出现一个局部词不应压过“查询概念全部在场”的页面；覆盖率直接参与
            # 乘法，不留固定底座（刹车油周期问法否则会被高频“检查制动液”页抢走）。
            quality = raw * rank_coverage ** _RANK_COVERAGE_EXPONENT
            if quality > best_quality:
                best_quality, best_coverage = quality, coverage
        return best_quality, best_coverage, section_hit

    # ── 范围闸 ──────────────────────────────────────────────────────────────

    def _unknown_ascii_terms(self, query: str) -> set[str]:
        normalized = _normalize(query)
        without_vehicle = normalized
        for alias in sorted(self._vehicle_aliases, key=lambda word: (-len(word), word)):
            without_vehicle = without_vehicle.replace(alias, " ")
        tokens = [item.casefold() for item in _ASCII_TOKEN_RE.findall(without_vehicle)]
        query_terms = {item for item in tokens
                       if len(item) >= 3 or _MODEL_CODE_RE.fullmatch(item)}
        unknown = {
            item for item in query_terms
            if item not in self._vehicle_ascii_tokens
            and item not in _ASCII_GATE_EXEMPT
            and item not in self._ascii_vocabulary
        }
        # `Android` 与 `Auto` 分别可能出现在不同段落；协议/产品问法必须整短语存在，
        # 不能用逐 token 命中拼出一个手册从未声明的兼容性结论。
        ascii_phrases = re.findall(
            r"[a-z0-9][a-z0-9._+/-]*(?:\s+[a-z0-9][a-z0-9._+/-]*)+",
            without_vehicle,
        )
        for phrase in ascii_phrases:
            phrase = _SPACE_RE.sub(" ", phrase).strip()
            if (phrase and phrase not in self._normalized_corpus
                    and _SPACE_RE.sub("", phrase) not in self._normalized_corpus_compact):
                unknown.add(phrase)
        return unknown

    def _in_scope_variants(self, query: str) -> tuple[list[_Variant], str]:
        normalized = _normalize(query)
        if any(marker in normalized for marker in self._config.foreign_vehicle_markers):
            return [], "foreign_vehicle"
        if any(marker in normalized for marker in self._config.absent_subject_markers):
            return [], "absent_subject"
        variants = self._query_variants(query)
        # CarPlay 这类手册没有的专名不得凭“手机连接”近似命中；受控同义词换掉的专名
        # （NOA → 智能领航辅助）按换过之后的变体判。
        kept = [variant for variant in variants
                if not self._unknown_ascii_terms(variant.content)]
        if variants and not kept:
            return [], "unknown_term"
        return kept, ""

    def scope_veto(self, query: str) -> str:
        """问的明显不是本车手册的东西（别的车型 / 本车没有的对象 / 手册没有的专名）时返回
        原因，否则空串。"""
        return self._in_scope_variants(query)[1]

    def unknown_subject_terms(self, query: str) -> list[str]:
        """用户内容里整本手册都没有、同义词也换不掉的实词双字（句法产物不算）。

        取最好的那个变体：只要有一个同义词变体把它换成了手册用词（尾箱 → 后备箱），它就
        不算未知。非空说明问的东西手册不认识——可能是车主叫法（刮车、小冰箱），也可能是
        别家车型（奇骏）；词法覆盖率分不开这两种，交给目录路由判。"""
        variants, veto = self._in_scope_variants(query)
        if veto or not variants:
            return []
        best: list[str] | None = None
        for variant in variants:
            topic, _evidence, _soft = self._variant_terms(variant)
            unknown = sorted(term for term in topic
                             if _CJK_SEQUENCE_RE.fullmatch(term)
                             and not self._document_frequency.get(term, 0))
            if best is None or len(unknown) < len(best):
                best = unknown
            if not best:
                break
        return best or []

    def route_block_reason(self, query: str) -> str:
        """这句话能不能交给目录路由：范围闸拦下的不能（LLM 不越过确定性闸）；没有实词的不能；描述仪表灯 /
        图标的也不能——认图标只认受控视觉目录，把「黄色感叹号」路由到告警表，等于让生成
        模型在表格相邻行之间猜（生产曾把「背宝剑小人」猜成安全气囊）。"""
        variants, veto = self._in_scope_variants(query)
        if veto:
            return veto
        # 剥掉问法壳一个实字都不剩（「这是什么」「怎么回事」）：没有东西可路由，交给模型
        # 只会凭空挑一节（曾把「这是什么」路由到「外部介绍」）。单字实词（「这车都有哪些挡」
        # 的「挡」）进不了双字表，但它是内容，照样交给路由。
        if not any(self._variant_terms(variant)[0]
                   or any(_CJK_SEQUENCE_RE.fullmatch(char) and char not in _FUNCTION_CHARS
                          for char in variant.content)
                   for variant in variants):
            return "no_content"
        if _has_visual_context(query):
            return "visual_context"
        return ""

    # ── 视觉与图片 ──────────────────────────────────────────────────────────

    def _matched_visual_assets(self, query: str) -> list[tuple[dict[str, Any], str]]:
        compact = _compact(query)
        has_visual_context = _has_visual_context(query)
        matched: list[tuple[dict[str, Any], str]] = []
        seen: set[str] = set()
        for needle, kind, asset in self._visual_needles:
            if needle not in compact or asset["asset_id"] in seen:
                continue
            if kind == "visual_caption" and len(needle) < 4 and not has_visual_context:
                continue
            matched.append((asset, kind))
            seen.add(asset["asset_id"])
        return matched

    def _materialize_image(self, asset: dict[str, Any], match_kind: str,
                           remaining: int) -> ManualImage | None:
        byte_length = int(asset.get("byte_length") or 0)
        if byte_length <= 0 or byte_length > _MAX_IMAGE_BYTES or byte_length > remaining:
            return None
        try:
            data = self.package.read_asset(asset["asset_id"])
        except IndexFormatError:
            # 启动期已经全量验过；运行期读取仍失败说明包在进程存活期间被替换/损坏，
            # 该图 fail closed，不影响已经核验过的文本答案。
            return None
        encoded = base64.b64encode(data).decode("ascii")
        return ManualImage(
            asset_id=asset["asset_id"],
            caption=asset["caption"],
            description=asset.get("description", ""),
            page_start=asset["page_start"],
            media_type=asset["media_type"],
            data_uri=f"data:{asset['media_type']};base64,{encoded}",
            sha256=asset["blob_sha256"],
            width=asset["width"],
            height=asset["height"],
            bbox=tuple(float(value) for value in asset["bbox"]),
            role=asset["role"],
            match_kind=match_kind,
        )

    def _materialize(self, ranked: list[tuple[float, float, bool, _PreparedChunk]],
                     visual_by_page: dict[int, list[tuple[dict[str, Any], str]]]) -> list[Chunk]:
        result: list[Chunk] = []
        embedded_bytes = 0
        embedded_count = 0
        embedded_blobs: set[str] = set()
        title = self.document["title"]
        for rank, (quality, coverage, section_hit, prepared) in enumerate(ranked):
            raw = prepared.raw
            section_path = tuple(raw["section_path"])
            page_start, page_end = raw["page_start"], raw["page_end"]
            pages = (f"PDF第{page_start}页" if page_start == page_end
                     else f"PDF第{page_start}-{page_end}页")
            section = " > ".join(section_path)
            source = " · ".join(item for item in (title, section, pages) if item)
            image_candidates: list[tuple[dict[str, Any], str]] = list(
                visual_by_page.get(page_start, ()))
            if not image_candidates and rank == 0:
                page_assets = self._assets_by_page.get(page_start, ())
                # 多图页若没有正式 caption/别名命中，随便挑一张会把图标与名称再次错配；
                # 只有单图页或明确标成 illustration 的大图才作为同页证据返回。
                illustrations = [item for item in page_assets
                                 if item.get("role") == "illustration"]
                if len(page_assets) == 1:
                    image_candidates = [(page_assets[0], "page_evidence")]
                elif illustrations:
                    image_candidates = [(illustrations[0], "page_evidence")]
            images: list[ManualImage] = []
            for asset, match_kind in image_candidates:
                if embedded_count >= _MAX_IMAGES:
                    break
                if asset["blob_sha256"] in embedded_blobs:
                    continue
                image = self._materialize_image(
                    asset, match_kind,
                    _MAX_IMAGE_TOTAL_BYTES - embedded_bytes)
                if image is None:
                    continue
                images.append(image)
                embedded_count += 1
                embedded_bytes += int(asset["byte_length"])
                embedded_blobs.add(asset["blob_sha256"])
            result.append(Chunk(
                content=raw["content"],
                source=source,
                score=min(0.999, 1.0 - math.exp(-quality / 8.0)),
                source_type="manual",
                document_id=self.document["document_id"],
                vehicle_model=self.vehicle_model,
                page_start=page_start,
                page_end=page_end,
                section_path=section_path,
                images=tuple(images),
                coverage=round(coverage, 6),
                section_hit=section_hit,
            ))
        return result

    # ── 检索入口 ────────────────────────────────────────────────────────────

    def _model_mismatch(self, vehicle_model: str) -> bool:
        requested_model = _normalize(vehicle_model).replace(" ", "-")
        indexed_model = _normalize(self.vehicle_model).replace(" ", "-")
        return bool(requested_model) and requested_model != indexed_model

    def _rank(self, prepared: list[tuple],
              visual_by_page: dict[int, list[tuple[dict[str, Any], str]]]) -> list[tuple]:
        ranked: list[tuple[float, float, bool, _PreparedChunk]] = []
        for chunk in self._chunks:
            quality, coverage, section_hit = self._score(prepared, chunk)
            page = chunk.raw["page_start"]
            if page in visual_by_page:
                # 人工审定别名/正式 caption 是比词法近似更强的证据；只提升其所属物理页，
                # 不把 caption/答案注入其它页，也不对未知视觉描述做模糊匹配。
                quality = max(quality, 24.0 + max(
                    len(_compact(asset["caption"]))
                    for asset, _ in visual_by_page[page]))
                coverage, section_hit = 1.0, True
            if quality < _MIN_QUALITY or coverage < _MIN_TOPIC_COVERAGE:
                continue
            ranked.append((quality, coverage, section_hit, chunk))
        ranked.sort(key=lambda item: (-item[0], item[3].raw["page_start"]))
        return ranked

    @staticmethod
    def _question_clauses(query: str) -> list[str]:
        """一句话的各个分句（问句与陈述都在内）：先按问号 / 句号断，再按分句词表
        （`runtime.clause_split`）断。"""
        clauses: list[str] = []
        for sentence in re.split(r"[？?。！!；;]", str(query or "")):
            clauses.extend(part for part in split_clauses(sentence) if part)
        return clauses

    def _leading_subject(self, clause: str) -> str:
        """分句里第一个手册认识的名词串（「胎压黄灯亮了」→「胎压」，「方向盘加热在哪」→
        「方向盘加热」）：剥掉问句壳后，第一段相邻主题双字（同 `_content_terms` 的产物 / 接缝
        判法，且手册里出现过）连成的串，至少两个字。"""
        for part in _CJK_SEQUENCE_RE.findall(self._strip_noise(_normalize(clause))):
            if len(part) < 2:
                continue
            _terms, artifacts, junctions = self._content_terms(part)
            usable = [gram not in artifacts and gram not in junctions
                      and bool(self._document_frequency.get(gram, 0))
                      for gram in (part[i:i + 2] for i in range(len(part) - 1))]
            if True in usable:
                begin = usable.index(True)
                end = begin
                while end < len(usable) and usable[end]:
                    end += 1
                return part[begin:end + 1]
        return ""

    def _mentions(self, chunk: _PreparedChunk, subject: str) -> bool:
        haystack = _compact(chunk.normalized_section + " " + chunk.normalized_content)
        return any(_compact(name) in haystack
                   for name in (subject, *self._config.aliases.get(subject, ())))

    def _carried_rankings(self, query: str) -> list[list[tuple]]:
        """仪表灯语境的一句多问（「胎压黄灯亮了，还能继续开吗？应该补到多少？」）：前文有主语时，
        后续每个问句按「主语 + 问句」再排一次（「应该补到多少」按「胎压应该补到多少」查；问句
        自己带着这个主语就按原样查），只留提到这个主语（或其受控同义词）的页。主语取第一个
        分句开头的手册名词串。

        只在仪表灯语境里做：这类句子不交给目录路由（`route_block_reason`），整句排序又被
        「黄灯亮了」拉向告警页，后半问只能靠这里。别的一句多问由 Agent 的目录路由按章节
        补（盲写复合问句集 42 条上第一批已 77/84）；在那里承接只添噪声——前提句开头的
        名词常是顺口带出的（「太阳当头照…咋整」承接成「太阳」），换了主语的问句（「安全带
        提示音咋设置，后排儿童锁又在哪扳」）承接出来的前一个主语的页会把命中页挤出前四。

        只承接、不让问句自己单独排：「还能继续开吗」的「能继 / 继续 / 续开」在讲电动尾翼的页
        全在场，自己排只会引入别处的页。"""
        if not _has_visual_context(query):
            return []
        clauses: list[str] = []
        for clause in self._question_clauses(query):
            head, *rest = [part for part in _LAMP_STATE_BOUNDARY_RE.split(clause) if part]
            # 灯态词之后的话自己带主题词才是另一问（「…亮了应该补到多少」）；「…亮了怎么办」
            # 「…亮着是什么意思」问的就是这盏灯，整句排序已经照顾到，切开只会承接出旁页。
            if rest and all(self._prepare_variants(self._in_scope_variants(part)[0])
                            for part in rest):
                clauses.extend([head, *rest])
            else:
                clauses.append(clause)
        if len(clauses) < 2:
            return []
        # 主语本身是显示位置（「仪表盘亮了个红灯，还能开吗」的「仪表盘」）时说明灯没有点名：
        # 认图标只认受控视觉目录，不承接。
        subject = self._leading_subject(clauses[0])
        if not subject or _has_visual_context(subject):
            return []
        rankings: list[list[tuple]] = []
        for clause in clauses[1:]:
            if not question_shape.is_non_directive_question(clause):
                continue
            asked = clause if subject in _normalize(clause) else subject + clause
            variants = self._in_scope_variants(asked)[0]
            ranking = [item for item in self._rank(self._prepare_variants(variants), {})
                       if self._mentions(item[-1], subject)]
            if ranking:
                rankings.append(ranking)
        return rankings

    async def retrieve(self, query: str, vehicle_model: str = "",
                       top_k: int = 4) -> list[Chunk]:
        if not str(query or "").strip() or top_k <= 0:
            return []
        if self._model_mismatch(vehicle_model):
            return []
        variants, veto = self._in_scope_variants(query)
        if veto or not variants:
            return []

        # 受控视觉目录按原话与同义词换过的内容都匹配（「胎压灯亮了」→ 胎压监测报警指示灯）；
        # 短 caption 仍要求视觉语境。
        visual_by_page: dict[int, list[tuple[dict[str, Any], str]]] = {}
        seen_assets: set[str] = set()
        for text in [query, *(variant.content for variant in variants)]:
            for asset, kind in self._matched_visual_assets(text):
                if asset["asset_id"] in seen_assets:
                    continue
                seen_assets.add(asset["asset_id"])
                visual_by_page.setdefault(asset["page_start"], []).append((asset, kind))

        ranked = self._rank(self._prepare_variants(variants), visual_by_page)
        carried = self._carried_rankings(query)
        if carried:
            # 整句排序被「亮了」拉向告警页；承接出来的排序按名次与之交替，整句首页不变。
            ranked = _interleave_ranked([ranked, *carried])
        return self._materialize(ranked[:min(int(top_k), 10)], visual_by_page)

    async def retrieve_sections(self, query: str, entry_ids: list[str],
                                top_k: int = 4) -> list[Chunk]:
        """目录路由选中的叶子 → 其物理页。每个叶子内部按本问法的词法分排序（不过闸：章节
        已由路由选定），叶子之间轮转取页——先每节最相关的一页、再每节第二页——保证每个被
        选中的章节至少进一页，而不是前两节把名额占满。未知编号直接忽略。"""
        if top_k <= 0:
            return []
        variants, veto = self._in_scope_variants(query)
        if veto:
            return []
        prepared = self._prepare_variants(variants)
        per_entry: list[list[_PreparedChunk]] = []
        claimed: set[int] = set()
        for entry_id in entry_ids:
            entry = self._toc_by_id.get(str(entry_id))
            if entry is None:
                continue
            scored = []
            for page in entry.pages:
                chunk = self._chunks_by_page.get(page)
                if chunk is None or page in claimed:
                    continue
                quality = self._score(prepared, chunk)[0] if prepared else 0.0
                scored.append((quality, chunk))
            scored.sort(key=lambda item: (-item[0], item[1].raw["page_start"]))
            pages = [chunk for _quality, chunk in scored[:2]]
            claimed.update(chunk.raw["page_start"] for chunk in pages)
            per_entry.append(pages)
        limit = min(int(top_k), 10)
        picked: list[tuple[float, float, bool, _PreparedChunk]] = []
        for depth in range(2):
            for pages in per_entry:
                if depth < len(pages) and len(picked) < limit:
                    # 目录路由的页没有经过词法闸，覆盖率记 0：它们不是词法证据。
                    picked.append((0.0, 0.0, False, pages[depth]))
        return self._materialize(picked, {})
