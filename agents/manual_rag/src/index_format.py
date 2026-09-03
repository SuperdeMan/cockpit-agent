"""车型手册只读索引格式。

索引正文是私有运行资产，不进 Git；本模块只定义稳定 schema、hash 与确定性 gzip 编码。
在线 Provider 不需要 PDF 解析依赖。
"""
from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
import zipfile


SCHEMA_VERSION = 1
VISUAL_SCHEMA_VERSION = 1
PACKAGE_INDEX_ENTRY = "index.json"
PACKAGE_VISUAL_ENTRY = "visual-assets.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ASSET_PATH_RE = re.compile(r"^assets/([0-9a-f]{64})\.(jpg|png)$")
_IMAGE_MAGIC = {
    "image/jpeg": b"\xff\xd8",
    "image/png": b"\x89PNG\r\n\x1a\n",
}
_MAX_VISUAL_ASSETS = 5000
_MAX_ASSET_BYTES = 8 * 1024 * 1024
_MAX_VISUAL_TOTAL_BYTES = 512 * 1024 * 1024


class IndexFormatError(ValueError):
    """索引 schema、内容或完整性校验失败。"""


@dataclass(frozen=True)
class ExtractedPage:
    """PDF 提取后的单页；页号是 PDF 物理页号（从 1 开始）。"""

    page_number: int
    section_path: tuple[str, ...]
    content: str


@dataclass(frozen=True)
class ExtractedVisualAsset:
    """离线从 PDF 提取的一次图片放置；二进制只进入 ignored 私有包。"""

    asset_id: str
    page_number: int
    xobject_name: str
    media_type: str
    width: int
    height: int
    bbox: tuple[float, float, float, float]
    caption: str
    aliases: tuple[str, ...]
    description: str
    role: str
    data: bytes


@dataclass(frozen=True)
class LoadedManualPackage:
    """已完成启动期全量校验的手册包；图片按需再次校验读取。"""

    path: Path
    index: dict[str, Any]
    visual: dict[str, Any]
    is_archive: bool

    def read_asset(self, asset_id: str) -> bytes:
        if not self.is_archive or not self.visual:
            raise IndexFormatError("当前手册索引不含视觉资产")
        asset = next(
            (item for item in self.visual["assets"]
             if item.get("asset_id") == asset_id),
            None,
        )
        if asset is None:
            raise IndexFormatError(f"视觉资产不存在：{asset_id}")
        try:
            with zipfile.ZipFile(self.path, "r") as archive:
                data = archive.read(asset["blob_path"])
        except Exception as exc:
            raise IndexFormatError(f"视觉资产无法读取：{asset_id}：{exc}") from exc
        _validate_asset_blob(asset, data)
        return data


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _chunks_sha256(chunks: list[dict[str, Any]]) -> str:
    return _sha256_bytes(_canonical_json(chunks))


def _visual_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "assets": manifest.get("assets"),
        "skipped_assets": manifest.get("skipped_assets"),
    }


def _visual_sha256(manifest: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(_visual_payload(manifest)))


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


def build_visual_manifest(
    assets: Iterable[ExtractedVisualAsset],
    *,
    document_id: str,
    source_sha256: str,
    skipped_assets: Iterable[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """构建视觉 manifest 与去重 blob 表；不读写文件。"""
    document_id = str(document_id).strip()
    source_sha256 = str(source_sha256).strip().lower()
    if not document_id:
        raise IndexFormatError("视觉资产 document_id 不能为空")
    if not _SHA256_RE.fullmatch(source_sha256):
        raise IndexFormatError("视觉资产 source_sha256 非法")

    records: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}
    seen_ids: set[str] = set()
    for item in assets:
        asset_id = str(item.asset_id).strip()
        media_type = str(item.media_type).strip().lower()
        data = bytes(item.data)
        if not asset_id or asset_id in seen_ids:
            raise IndexFormatError(f"视觉 asset_id 为空或重复：{asset_id!r}")
        seen_ids.add(asset_id)
        if media_type not in _IMAGE_MAGIC:
            raise IndexFormatError(f"视觉资产 MIME 不支持：{media_type}")
        if not data.startswith(_IMAGE_MAGIC[media_type]):
            raise IndexFormatError(f"视觉资产内容与 MIME 不符：{asset_id}")
        if not isinstance(item.page_number, int) or item.page_number < 1:
            raise IndexFormatError(f"视觉资产页码非法：{asset_id}")
        if not isinstance(item.width, int) or item.width < 1 \
                or not isinstance(item.height, int) or item.height < 1:
            raise IndexFormatError(f"视觉资产尺寸非法：{asset_id}")
        caption = str(item.caption).strip()
        role = str(item.role).strip()
        if not caption or not role:
            raise IndexFormatError(f"视觉资产 caption/role 不能为空：{asset_id}")
        bbox = [round(float(value), 4) for value in item.bbox]
        if len(bbox) != 4 or not all(math.isfinite(value) for value in bbox):
            raise IndexFormatError(f"视觉资产 bbox 非法：{asset_id}")
        aliases = list(dict.fromkeys(
            str(value).strip() for value in item.aliases if str(value).strip()))
        digest = _sha256_bytes(data)
        ext = "jpg" if media_type == "image/jpeg" else "png"
        blob_path = f"assets/{digest}.{ext}"
        existing = blobs.get(blob_path)
        if existing is not None and existing != data:
            raise IndexFormatError(f"视觉资产 hash 碰撞：{asset_id}")
        blobs[blob_path] = data
        records.append({
            "asset_id": asset_id,
            "page_start": item.page_number,
            "xobject_name": str(item.xobject_name).strip(),
            "media_type": media_type,
            "width": item.width,
            "height": item.height,
            "bbox": bbox,
            "caption": caption,
            "aliases": aliases,
            "description": str(item.description).strip(),
            "role": role,
            "blob_sha256": digest,
            "blob_path": blob_path,
            "byte_length": len(data),
        })
    records.sort(key=lambda value: (value["page_start"], value["asset_id"]))

    skipped: list[dict[str, Any]] = []
    for raw in skipped_assets:
        page = raw.get("page_start", raw.get("page"))
        name = str(raw.get("xobject_name") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if not isinstance(page, int) or page < 1 or not name or not reason:
            raise IndexFormatError("skipped_assets 项非法")
        skipped.append({
            "page_start": page,
            "xobject_name": name,
            "reason": reason,
        })
    skipped.sort(key=lambda value: (
        value["page_start"], value["xobject_name"], value["reason"]))

    manifest: dict[str, Any] = {
        "schema_version": VISUAL_SCHEMA_VERSION,
        "document_id": document_id,
        "source_sha256": source_sha256,
        "asset_count": len(records),
        "blob_count": len(blobs),
        "skipped_asset_count": len(skipped),
        "assets": records,
        "skipped_assets": skipped,
    }
    manifest["assets_sha256"] = _visual_sha256(manifest)
    validate_visual_manifest(manifest)
    return manifest, blobs


def validate_visual_manifest(manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest, Mapping):
        raise IndexFormatError("视觉 manifest 必须是 object")
    if manifest.get("schema_version") != VISUAL_SCHEMA_VERSION:
        raise IndexFormatError("视觉 manifest schema_version 非法")
    if not isinstance(manifest.get("document_id"), str) \
            or not manifest["document_id"].strip():
        raise IndexFormatError("视觉 manifest document_id 非法")
    source_sha = str(manifest.get("source_sha256") or "").lower()
    assets_sha = str(manifest.get("assets_sha256") or "").lower()
    if not _SHA256_RE.fullmatch(source_sha):
        raise IndexFormatError("视觉 manifest source_sha256 非法")
    if not _SHA256_RE.fullmatch(assets_sha):
        raise IndexFormatError("视觉 manifest assets_sha256 非法")
    assets = manifest.get("assets")
    skipped = manifest.get("skipped_assets")
    if not isinstance(assets, list) or not isinstance(skipped, list):
        raise IndexFormatError("视觉 manifest assets/skipped_assets 非法")
    if manifest.get("asset_count") != len(assets):
        raise IndexFormatError("视觉 manifest asset_count 不一致")
    if len(assets) > _MAX_VISUAL_ASSETS:
        raise IndexFormatError("视觉 manifest asset_count 超出上限")
    if manifest.get("skipped_asset_count") != len(skipped):
        raise IndexFormatError("视觉 manifest skipped_asset_count 不一致")
    ids: set[str] = set()
    paths: set[str] = set()
    for pos, asset in enumerate(assets):
        if not isinstance(asset, Mapping):
            raise IndexFormatError(f"视觉 assets[{pos}] 非法")
        asset_id = asset.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id or asset_id in ids:
            raise IndexFormatError(f"视觉 assets[{pos}].asset_id 非法")
        ids.add(asset_id)
        page = asset.get("page_start")
        if not isinstance(page, int) or page < 1:
            raise IndexFormatError(f"视觉 assets[{pos}].page_start 非法")
        if asset.get("media_type") not in _IMAGE_MAGIC:
            raise IndexFormatError(f"视觉 assets[{pos}].media_type 非法")
        if not isinstance(asset.get("width"), int) or asset["width"] < 1 \
                or not isinstance(asset.get("height"), int) or asset["height"] < 1:
            raise IndexFormatError(f"视觉 assets[{pos}] 尺寸非法")
        bbox = asset.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4 or not all(
                isinstance(value, (int, float)) and math.isfinite(value)
                for value in bbox):
            raise IndexFormatError(f"视觉 assets[{pos}].bbox 非法")
        for key in ("caption", "role", "blob_sha256", "blob_path"):
            if not isinstance(asset.get(key), str) or not asset[key].strip():
                raise IndexFormatError(f"视觉 assets[{pos}].{key} 非法")
        if not isinstance(asset.get("description"), str):
            raise IndexFormatError(f"视觉 assets[{pos}].description 非法")
        aliases = asset.get("aliases")
        if not isinstance(aliases, list) or not all(
                isinstance(value, str) and value.strip() for value in aliases):
            raise IndexFormatError(f"视觉 assets[{pos}].aliases 非法")
        digest = asset["blob_sha256"].lower()
        match = _ASSET_PATH_RE.fullmatch(asset["blob_path"])
        expected_ext = "jpg" if asset["media_type"] == "image/jpeg" else "png"
        if not _SHA256_RE.fullmatch(digest) or not match \
                or match.group(1) != digest or match.group(2) != expected_ext:
            raise IndexFormatError(f"视觉 assets[{pos}] blob 指纹非法")
        if not isinstance(asset.get("byte_length"), int) \
                or not 1 <= asset["byte_length"] <= _MAX_ASSET_BYTES:
            raise IndexFormatError(f"视觉 assets[{pos}].byte_length 非法")
        paths.add(asset["blob_path"])
    if manifest.get("blob_count") != len(paths):
        raise IndexFormatError("视觉 manifest blob_count 不一致")
    for pos, item in enumerate(skipped):
        if not isinstance(item, Mapping) \
                or not isinstance(item.get("page_start"), int) \
                or item["page_start"] < 1 \
                or not isinstance(item.get("xobject_name"), str) \
                or not item["xobject_name"].strip() \
                or not isinstance(item.get("reason"), str) \
                or not item["reason"].strip():
            raise IndexFormatError(f"视觉 skipped_assets[{pos}] 非法")
    if assets_sha != _visual_sha256(manifest):
        raise IndexFormatError("视觉 manifest assets_sha256 不一致")


def _validate_package_pair(index: Mapping[str, Any],
                           visual: Mapping[str, Any]) -> None:
    validate_index_bundle(index)
    validate_visual_manifest(visual)
    document = index["document"]
    if visual["document_id"] != document["document_id"]:
        raise IndexFormatError("视觉资产 document_id 与文本索引不一致")
    if visual["source_sha256"] != document["source_sha256"]:
        raise IndexFormatError("视觉资产源 PDF 与文本索引不一致")
    source_pages = document["source_pages"]
    if any(asset["page_start"] > source_pages for asset in visual["assets"]):
        raise IndexFormatError("视觉资产页码超出源 PDF")


def _validate_asset_blob(asset: Mapping[str, Any], data: bytes) -> None:
    if len(data) != asset["byte_length"]:
        raise IndexFormatError(
            f"图片 {asset['asset_id']} 长度不一致")
    if _sha256_bytes(data) != asset["blob_sha256"]:
        raise IndexFormatError(
            f"图片 {asset['asset_id']} SHA-256 不一致")
    magic = _IMAGE_MAGIC[asset["media_type"]]
    if not data.startswith(magic):
        raise IndexFormatError(
            f"图片 {asset['asset_id']} MIME 内容不一致")


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def write_manual_package(
    path: str | Path,
    index: Mapping[str, Any],
    visual: Mapping[str, Any],
    blobs: Mapping[str, bytes],
    *,
    overwrite: bool = False,
) -> Path:
    """写 deterministic `.mrag`；所有图片在落盘前逐个核验。"""
    _validate_package_pair(index, visual)
    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(f"索引已存在，拒绝覆盖：{target}")
    expected_paths = {asset["blob_path"] for asset in visual["assets"]}
    if set(blobs) != expected_paths:
        raise IndexFormatError("视觉 blob 表与 manifest 不一致")
    by_path = {asset["blob_path"]: asset for asset in visual["assets"]}
    for blob_path, data in blobs.items():
        # 同一 blob 可被多页复用；任选一条元数据做逐字节校验。
        _validate_asset_blob(by_path[blob_path], bytes(data))
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(_zip_info(PACKAGE_INDEX_ENTRY), _canonical_json(index))
        archive.writestr(_zip_info(PACKAGE_VISUAL_ENTRY), _canonical_json(visual))
        for blob_path in sorted(blobs):
            archive.writestr(_zip_info(blob_path), bytes(blobs[blob_path]))
    return target

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


def _load_gzip_index(path: str | Path) -> dict[str, Any]:
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


def load_manual_package(path: str | Path) -> LoadedManualPackage:
    """加载旧文本 gzip 或 v2 `.mrag`，启动时校验包内全部图片。"""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if not zipfile.is_zipfile(source):
        return LoadedManualPackage(
            path=source,
            index=_load_gzip_index(source),
            visual={},
            is_archive=False,
        )
    try:
        with zipfile.ZipFile(source, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise IndexFormatError("手册包含重复 ZIP entry")
            if PACKAGE_INDEX_ENTRY not in names or PACKAGE_VISUAL_ENTRY not in names:
                raise IndexFormatError("手册包缺少 index.json/visual-assets.json")
            index = json.loads(archive.read(PACKAGE_INDEX_ENTRY).decode("utf-8"))
            visual = json.loads(archive.read(PACKAGE_VISUAL_ENTRY).decode("utf-8"))
            _validate_package_pair(index, visual)
            expected = {
                PACKAGE_INDEX_ENTRY,
                PACKAGE_VISUAL_ENTRY,
                *{asset["blob_path"] for asset in visual["assets"]},
            }
            if set(names) != expected:
                raise IndexFormatError("手册包 ZIP entry 与视觉 manifest 不一致")
            infos = {info.filename: info for info in archive.infolist()}
            expected_blob_sizes = {
                asset["blob_path"]: asset["byte_length"]
                for asset in visual["assets"]
            }
            if sum(expected_blob_sizes.values()) > _MAX_VISUAL_TOTAL_BYTES:
                raise IndexFormatError("手册包视觉资产总量超出上限")
            for blob_path, expected_size in expected_blob_sizes.items():
                if infos[blob_path].file_size != expected_size:
                    raise IndexFormatError(f"图片 {blob_path} ZIP 长度不一致")
            checked: set[str] = set()
            for asset in visual["assets"]:
                blob_path = asset["blob_path"]
                if blob_path in checked:
                    continue
                _validate_asset_blob(asset, archive.read(blob_path))
                checked.add(blob_path)
    except FileNotFoundError:
        raise
    except IndexFormatError:
        raise
    except Exception as exc:
        raise IndexFormatError(f"手册包无法读取：{exc}") from exc
    return LoadedManualPackage(
        path=source,
        index=dict(index),
        visual=dict(visual),
        is_archive=True,
    )


def load_index_bundle(path: str | Path) -> dict[str, Any]:
    """兼容入口：旧 gzip 直接加载，`.mrag` 返回其中的文本 bundle。"""
    return load_manual_package(path).index
