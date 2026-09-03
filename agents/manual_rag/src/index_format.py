"""车型手册只读索引格式。

索引正文是私有运行资产，不进 Git；本模块只定义稳定 schema、hash 与确定性 gzip 编码。
在线 Provider 不需要 PDF 解析依赖。
"""
from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class IndexFormatError(ValueError):
    """索引 schema、内容或完整性校验失败。"""


@dataclass(frozen=True)
class ExtractedPage:
    """PDF 提取后的单页；页号是 PDF 物理页号（从 1 开始）。"""

    page_number: int
    section_path: tuple[str, ...]
    content: str


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _chunks_sha256(chunks: list[dict[str, Any]]) -> str:
    return _sha256_bytes(_canonical_json(chunks))


def _basename(value: str) -> str:
    """同时处理 Windows/POSIX 路径，索引不得泄漏构建机绝对路径。"""
    return re.split(r"[\\/]", str(value).strip())[-1]


def build_index_bundle(
    pages: Iterable[ExtractedPage],
    *,
    document_id: str,
    title: str,
    publisher: str,
    vehicle_model: str,
    vehicle_aliases: Iterable[str],
    revision: str,
    source_file: str,
    source_sha256: str,
    source_pages: int,
) -> dict[str, Any]:
    """从已提取页面构建 schema v1 bundle；不读写文件。"""
    document_id = str(document_id).strip()
    title = str(title).strip()
    publisher = str(publisher).strip()
    vehicle_model = str(vehicle_model).strip().lower()
    revision = str(revision).strip()
    source_sha256 = str(source_sha256).strip().lower()
    source_file = _basename(source_file)
    if not all((document_id, title, publisher, vehicle_model, source_file)):
        raise IndexFormatError("document 元数据不能为空")
    if not _SHA256_RE.fullmatch(source_sha256):
        raise IndexFormatError("source_sha256 必须是 64 位十六进制")
    if not _REVISION_RE.fullmatch(revision):
        raise IndexFormatError("revision 必须是 YYYY-MM-DD")
    if not isinstance(source_pages, int) or source_pages < 1:
        raise IndexFormatError("source_pages 必须是正整数")

    aliases: list[str] = []
    for raw in vehicle_aliases:
        alias = str(raw).strip()
        if alias and alias.casefold() not in {a.casefold() for a in aliases}:
            aliases.append(alias)
    if not aliases:
        raise IndexFormatError("vehicle_aliases 不能为空")

    chunks: list[dict[str, Any]] = []
    seen_pages: set[int] = set()
    for page in sorted(pages, key=lambda item: item.page_number):
        number = page.page_number
        if not isinstance(number, int) or not 1 <= number <= source_pages:
            raise IndexFormatError(f"page_number 非法：{number!r}")
        if number in seen_pages:
            raise IndexFormatError(f"page_number 重复：{number}")
        seen_pages.add(number)
        content = str(page.content).strip()
        if not content:
            continue
        section_path = [str(item).strip() for item in page.section_path
                        if str(item).strip()]
        chunk = {
            "chunk_id": f"{document_id}:p{number:04d}",
            "page_start": number,
            "page_end": number,
            "section_path": section_path,
            "content": content,
            "chunk_sha256": _sha256_bytes(content.encode("utf-8")),
        }
        chunks.append(chunk)

    if not chunks:
        raise IndexFormatError("手册页面不能为空")

    bundle: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "document": {
            "document_id": document_id,
            "title": title,
            "publisher": publisher,
            "vehicle_model": vehicle_model,
            "vehicle_aliases": aliases,
            "revision": revision,
            "source_file": source_file,
            "source_sha256": source_sha256,
            "source_pages": source_pages,
            "chunk_count": len(chunks),
            "content_sha256": _chunks_sha256(chunks),
        },
        "chunks": chunks,
    }
    validate_index_bundle(bundle)
    return bundle

def validate_index_bundle(bundle: Mapping[str, Any]) -> None:
    """完整校验 bundle；只有通过后才能把来源盖成 ``real``。"""
    if not isinstance(bundle, Mapping):
        raise IndexFormatError("索引根节点必须是 object")
    if bundle.get("schema_version") != SCHEMA_VERSION:
        raise IndexFormatError(
            f"不支持的 schema_version={bundle.get('schema_version')!r}")
    document = bundle.get("document")
    chunks = bundle.get("chunks")
    if not isinstance(document, Mapping):
        raise IndexFormatError("document 必须是 object")
    if not isinstance(chunks, list) or not chunks:
        raise IndexFormatError("chunks 不能为空")

    for key in ("document_id", "title", "publisher", "vehicle_model",
                "revision", "source_file", "source_sha256",
                "content_sha256"):
        if not isinstance(document.get(key), str) or not document[key].strip():
            raise IndexFormatError(f"document.{key} 不能为空")
    if _basename(document["source_file"]) != document["source_file"]:
        raise IndexFormatError("document.source_file 只能保存文件名，不能含路径")
    if not _SHA256_RE.fullmatch(document["source_sha256"].lower()):
        raise IndexFormatError("document.source_sha256 非法")
    if not _SHA256_RE.fullmatch(document["content_sha256"].lower()):
        raise IndexFormatError("document.content_sha256 非法")
    if not _REVISION_RE.fullmatch(document["revision"]):
        raise IndexFormatError("document.revision 非法")
    source_pages = document.get("source_pages")
    if not isinstance(source_pages, int) or source_pages < 1:
        raise IndexFormatError("document.source_pages 非法")
    if document.get("chunk_count") != len(chunks):
        raise IndexFormatError("document.chunk_count 与 chunks 数量不一致")
    aliases = document.get("vehicle_aliases")
    if not isinstance(aliases, list) or not aliases or not all(
            isinstance(item, str) and item.strip() for item in aliases):
        raise IndexFormatError("document.vehicle_aliases 非法")

    ids: set[str] = set()
    pages: set[tuple[int, int]] = set()
    for pos, raw in enumerate(chunks):
        if not isinstance(raw, Mapping):
            raise IndexFormatError(f"chunks[{pos}] 必须是 object")
        chunk_id = raw.get("chunk_id")
        content = raw.get("content")
        section_path = raw.get("section_path")
        start, end = raw.get("page_start"), raw.get("page_end")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise IndexFormatError(f"chunks[{pos}].chunk_id 非法")
        if chunk_id in ids:
            raise IndexFormatError(f"chunk_id 重复：{chunk_id}")
        ids.add(chunk_id)
        if not (isinstance(start, int) and isinstance(end, int)
                and 1 <= start <= end <= source_pages):
            raise IndexFormatError(f"chunks[{pos}] 页码非法")
        if (start, end) in pages:
            raise IndexFormatError(f"chunks 页码范围重复：{start}-{end}")
        pages.add((start, end))
        if not isinstance(section_path, list) or not all(
                isinstance(item, str) and item.strip() for item in section_path):
            raise IndexFormatError(f"chunks[{pos}].section_path 非法")
        if not isinstance(content, str) or not content.strip():
            raise IndexFormatError(f"chunks[{pos}].content 不能为空")
        expected = _sha256_bytes(content.encode("utf-8"))
        if raw.get("chunk_sha256") != expected:
            raise IndexFormatError(f"chunks[{pos}].chunk_sha256 不一致")

    expected_content_hash = _chunks_sha256(chunks)
    if document["content_sha256"].lower() != expected_content_hash:
        raise IndexFormatError("document.content_sha256 与 chunks 不一致")


def encode_index_bundle(bundle: Mapping[str, Any]) -> bytes:
    validate_index_bundle(bundle)
    # mtime=0 保证同输入逐字节一致；online 只需 Python stdlib 即可解压。
    return gzip.compress(_canonical_json(bundle), compresslevel=9, mtime=0)


def write_index_bundle(path: str | Path, bundle: Mapping[str, Any], *,
                       overwrite: bool = False) -> Path:
    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(f"索引已存在，拒绝覆盖：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encode_index_bundle(bundle))
    return target


def load_index_bundle(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        raw = gzip.decompress(source.read_bytes())
        bundle = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise IndexFormatError(f"索引无法读取：{exc}") from exc
    validate_index_bundle(bundle)
    return bundle
