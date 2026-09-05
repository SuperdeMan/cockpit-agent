"""真实车型手册的只读文件索引 Provider。

单手册语料用中文字符 n-gram BM25 召回，再以章节、短语和 IDF 覆盖率重排。低相关
查询 fail closed；不依赖网络、数据库或在线 embedding。
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
_MAX_IMAGE_BYTES = 640 * 1024
_MAX_IMAGE_TOTAL_BYTES = 768 * 1024
_MAX_IMAGES = 2
_VISUAL_CONTEXT_MARKERS = (
    "图标", "指示灯", "仪表", "灯亮", "亮了", "常亮", "闪烁",
)


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


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
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


def _load_retrieval_config(
        path: Path,
) -> tuple[list[str], dict[str, list[str]], list[dict[str, list[str]]]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ManualIndexError(f"检索配置无法读取：{path}：{exc}") from exc
    if raw.get("schema_version") != 1:
        raise ManualIndexError(
            f"检索配置 schema_version 非法：{raw.get('schema_version')!r}")
    noise = [_normalize(item) for item in (raw.get("query_noise_phrases") or [])
             if _normalize(item)]
    aliases: dict[str, list[str]] = {}
    for source, targets in (raw.get("aliases") or {}).items():
        key = _normalize(source)
        values = [_normalize(item) for item in (targets or []) if _normalize(item)]
        if not key or not values:
            raise ManualIndexError(f"检索同义词声明非法：{source!r}")
        aliases[key] = values
    expansions: list[dict[str, list[str]]] = []
    for pos, rule in enumerate(raw.get("intent_expansions") or []):
        if not isinstance(rule, dict):
            raise ManualIndexError(f"检索意图扩展[{pos}] 必须是 object")
        parsed = {
            key: [_normalize(item) for item in (rule.get(key) or []) if _normalize(item)]
            for key in ("when_any", "require_any", "unless_any", "append")
        }
        if not parsed["when_any"] or not parsed["append"]:
            raise ManualIndexError(f"检索意图扩展[{pos}] 缺 when_any/append")
        expansions.append(parsed)
    return sorted(set(noise), key=len, reverse=True), aliases, expansions


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
        (self._noise_phrases, self._aliases,
         self._intent_expansions) = _load_retrieval_config(config_path)
        self._vehicle_aliases = {
            _normalize(item) for item in self.document["vehicle_aliases"]
            if _normalize(item)
        }
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
        self._avg_body_length = max(
            1.0,
            sum(chunk.body_length for chunk in self._chunks) / len(self._chunks),
        )

    @property
    def vehicle_model(self) -> str:
        return self.document["vehicle_model"]

    @property
    def revision(self) -> str:
        return self.document["revision"]

    def _strip_noise(self, value: str) -> str:
        result = value
        for alias in sorted(self._vehicle_aliases, key=len, reverse=True):
            result = result.replace(alias, " ")
        for phrase in self._noise_phrases:
            result = result.replace(phrase, " ")
        return _SPACE_RE.sub(" ", result).strip()

    def _query_variants(self, query: str) -> list[str]:
        original = _normalize(query)
        variants = [original]
        # 同义词是受控声明，不让模型动态改写查询。组合只允许原问法中**不重叠**的
        # source spans；这样“刹车油+换”可同时变成“制动液+更换”，而“推荐胎压”与
        # 内含的“胎压”不会级联成畸形词。
        replacements: list[tuple[int, int, str]] = []
        for source, targets in self._aliases.items():
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
        expanded_variants: list[str] = []
        base_variants = list(variants)
        for rule in self._intent_expansions:
            if not any(marker in original for marker in rule["when_any"]):
                continue
            if rule["require_any"] and not any(
                    marker in original for marker in rule["require_any"]):
                continue
            if any(marker in original for marker in rule["unless_any"]):
                continue
            suffix = " ".join(rule["append"])
            for variant in base_variants:
                candidate = f"{variant} {suffix}"
                if candidate not in expanded_variants:
                    expanded_variants.append(candidate)
                if len(expanded_variants) >= 24:
                    break
            if len(expanded_variants) >= 24:
                break
        # 一旦问法明确要“规格/周期”证据，就只按带意图的变体排；同时保留基础变体
        # 会让高频主题页靠重复词压过真正回答该维度的页。
        if expanded_variants:
            variants = expanded_variants
        cleaned: list[str] = []
        for variant in variants:
            candidate = self._strip_noise(variant)
            if candidate and candidate not in cleaned:
                cleaned.append(candidate)
        return cleaned

    def _idf(self, term: str) -> float:
        count = self._document_frequency.get(term, 0)
        total = len(self._chunks)
        return math.log(1.0 + (total - count + 0.5) / (count + 0.5))

    def _bm25(self, terms: Counter[str], chunk: _PreparedChunk) -> tuple[float, float]:
        k1, b = 1.2, 0.75
        body_score = 0.0
        section_score = 0.0
        matched_weight = 0.0
        total_weight = 0.0
        for term, qtf in terms.items():
            idf = self._idf(term)
            total_weight += idf
            body_tf = chunk.body_terms.get(term, 0)
            section_tf = chunk.section_terms.get(term, 0)
            if body_tf or section_tf:
                matched_weight += idf
            if body_tf:
                denominator = body_tf + k1 * (
                    1.0 - b + b * chunk.body_length / self._avg_body_length)
                body_score += idf * (body_tf * (k1 + 1.0) / denominator) * min(qtf, 2)
            if section_tf:
                section_score += idf * min(section_tf, 2) * min(qtf, 2)
        coverage = matched_weight / total_weight if total_weight else 0.0
        return body_score + 1.8 * section_score, coverage

    def _score(self, variants: list[str], chunk: _PreparedChunk) -> tuple[float, float]:
        best_quality = 0.0
        best_coverage = 0.0
        haystack = _compact(chunk.normalized_section + " " + chunk.normalized_content)
        for variant in variants:
            terms = Counter(_tokens(variant))
            if not terms:
                continue
            raw, coverage = self._bm25(terms, chunk)
            needle = _compact(variant)
            if len(needle) >= 2 and needle in haystack:
                raw += 3.0
            for phrase in re.findall(r"[\u3400-\u9fff]{3,}", variant):
                if phrase in chunk.normalized_content:
                    raw += min(5.0, 1.0 + len(phrase) * 0.6)
                elif phrase in chunk.normalized_section:
                    raw += min(6.0, 1.5 + len(phrase) * 0.7)
            # 重复出现一个局部词不应压过“查询概念全部在场”的页面；覆盖率直接参与
            # 乘法，不留固定底座（刹车油周期问法否则会被高频“检查制动液”页抢走）。
            quality = raw * coverage
            if quality > best_quality:
                best_quality, best_coverage = quality, coverage
        return best_quality, best_coverage

    def _unknown_ascii_terms(self, query: str) -> set[str]:
        normalized = _normalize(query)
        without_vehicle = normalized
        for alias in sorted(self._vehicle_aliases, key=len, reverse=True):
            without_vehicle = without_vehicle.replace(alias, " ")
        query_terms = {
            item.casefold() for item in _ASCII_TOKEN_RE.findall(without_vehicle)
            if len(item) >= 3
        }
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

    def _matched_visual_assets(self, query: str) -> list[tuple[dict[str, Any], str]]:
        compact = _compact(query)
        has_visual_context = any(
            _compact(marker) in compact for marker in _VISUAL_CONTEXT_MARKERS)
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

    async def retrieve(self, query: str, vehicle_model: str = "",
                       top_k: int = 4) -> list[Chunk]:
        if not str(query or "").strip() or top_k <= 0:
            return []
        requested_model = _normalize(vehicle_model).replace(" ", "-")
        indexed_model = _normalize(self.vehicle_model).replace(" ", "-")
        if requested_model and requested_model != indexed_model:
            return []
        # CarPlay 这类手册没有的专名不得凭“手机连接”近似命中。
        if self._unknown_ascii_terms(query):
            return []
        variants = self._query_variants(query)
        if not variants:
            return []

        visual_matches = self._matched_visual_assets(query)
        visual_by_page: dict[int, list[tuple[dict[str, Any], str]]] = {}
        for asset, kind in visual_matches:
            visual_by_page.setdefault(asset["page_start"], []).append((asset, kind))

        ranked: list[tuple[float, int, _PreparedChunk]] = []
        for chunk in self._chunks:
            quality, coverage = self._score(variants, chunk)
            page = chunk.raw["page_start"]
            if page in visual_by_page:
                # 人工审定别名/正式 caption 是比词法近似更强的证据；只提升其所属物理页，
                # 不把 caption/答案注入其它页，也不对未知视觉描述做模糊匹配。
                quality = max(quality, 24.0 + max(
                    len(_compact(asset["caption"]))
                    for asset, _ in visual_by_page[page]))
                coverage = 1.0
            if quality < 1.0 or coverage < 0.42:
                continue
            ranked.append((quality, chunk.raw["page_start"], chunk))
        ranked.sort(key=lambda item: (-item[0], item[1]))

        result: list[Chunk] = []
        embedded_bytes = 0
        embedded_count = 0
        embedded_blobs: set[str] = set()
        title = self.document["title"]
        for rank, (quality, _, prepared) in enumerate(
                ranked[:min(int(top_k), 10)]):
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
            ))
        return result
