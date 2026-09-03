#!/usr/bin/env python3
"""从用户提供的车型手册 PDF 构建 deterministic 私有索引包。"""
from __future__ import annotations

import argparse
import binascii
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import unicodedata
from typing import Any
import zlib

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.manual_rag.src.index_format import (
    ExtractedPage,
    ExtractedVisualAsset,
    build_index_bundle,
    build_visual_manifest,
    write_index_bundle,
    write_manual_package,
)


DEFAULT_ALIASES = ["SU7", "小米SU7", "Xiaomi SU7", "SU7 Pro", "SU7 Max"]
DEFAULT_VISUAL_CATALOG = (
    REPO_ROOT / "agents" / "manual_rag" / "resources" / "visual_assets.yaml")
_PDF_DATE_RE = re.compile(r"^D:(\d{4})(\d{2})(\d{2})")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _revision(metadata: dict[str, Any], override: str) -> str:
    if override:
        return override
    value = str(metadata.get("/ModDate") or metadata.get("/CreationDate") or "")
    match = _PDF_DATE_RE.match(value)
    if not match:
        raise ValueError("PDF 没有可识别的 ModDate/CreationDate，请显式传 --revision")
    return "-".join(match.groups())


def _outline_entries(reader) -> list[tuple[int, int, int, str]]:
    entries: list[tuple[int, int, int, str]] = []
    order = 0

    def walk(items, depth: int = 0) -> None:
        nonlocal order
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            title = str(getattr(item, "title", "") or "").strip()
            if not title:
                continue
            try:
                page_number = reader.get_destination_page_number(item) + 1
            except Exception:
                continue
            entries.append((page_number, order, depth, title))
            order += 1

    walk(reader.outline)
    entries.sort(key=lambda value: (value[0], value[1]))
    return entries


def _paths_by_page(reader) -> tuple[dict[int, tuple[str, ...]], set[str]]:
    entries = _outline_entries(reader)
    hierarchy: list[str] = []
    cursor = 0
    result: dict[int, tuple[str, ...]] = {}
    top_level = {title for _, _, depth, title in entries if depth == 0}
    for page_number in range(1, len(reader.pages) + 1):
        page_paths: list[tuple[str, ...]] = []
        while cursor < len(entries) and entries[cursor][0] <= page_number:
            _, _, depth, title = entries[cursor]
            hierarchy = hierarchy[:depth]
            hierarchy.append(title)
            if entries[cursor][0] == page_number:
                page_paths.append(tuple(hierarchy))
            cursor += 1
        if not page_paths:
            result[page_number] = tuple(hierarchy)
            continue
        # 一个物理页可能同时开始两个同级章节（例如 PDF 245 同含“车轮与轮胎参数”
        # 和“四轮定位”）。取最后一个会把前半页引用错名；保留共同祖先并合并叶节点。
        leaves = [
            path for path in page_paths
            if not any(len(other) > len(path) and other[:len(path)] == path
                       for other in page_paths)
        ]
        if len(leaves) == 1:
            result[page_number] = leaves[0]
            continue
        common_length = 0
        for values in zip(*leaves):
            if len(set(values)) != 1:
                break
            common_length += 1
        suffixes = [" > ".join(path[common_length:]) for path in leaves]
        combined = " / ".join(dict.fromkeys(item for item in suffixes if item))
        result[page_number] = leaves[0][:common_length] + ((combined,) if combined else ())
    return result, top_level


def _clean_page_text(text: str, page_number: int,
                     top_level_titles: set[str]) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00ad", "").replace("\u200b", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip()
             for line in normalized.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    if lines and lines[-1] == str(page_number):
        lines.pop()
        while lines and not lines[-1]:
            lines.pop()
        if lines and lines[-1] in top_level_titles:
            lines.pop()
    elif lines:
        for title in top_level_titles:
            if lines[-1] == f"{title} {page_number}":
                lines.pop()
                break
    cleaned = "\n".join(lines).strip()
    return _MULTI_BLANK_RE.sub("\n\n", cleaned)


def extract_pdf_pages(pdf_path: Path) -> tuple[list[ExtractedPage], dict[str, Any], int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "缺少构建期依赖 pypdf；请安装 agents/manual_rag/requirements-ingest.txt") from exc

    reader = PdfReader(str(pdf_path))
    if reader.is_encrypted:
        raise ValueError("不接受加密 PDF")
    paths, top_level = _paths_by_page(reader)
    pages: list[ExtractedPage] = []
    for page_number, page in enumerate(reader.pages, start=1):
        content = _clean_page_text(page.extract_text() or "", page_number, top_level)
        # 纯封面、空白页、只剩章节页脚的页不进入检索。
        if len(re.sub(r"\s+", "", content)) < 20:
            continue
        pages.append(ExtractedPage(page_number, paths[page_number], content))
    return pages, dict(reader.metadata or {}), len(reader.pages)


def _load_visual_catalog(path: Path, *, document_id: str,
                         source_sha256: str) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ValueError(f"视觉目录无法读取：{path}：{exc}") from exc
    if raw.get("schema_version") != 1:
        raise ValueError("视觉目录 schema_version 非法")
    if raw.get("document_id") != document_id:
        raise ValueError("视觉目录 document_id 与构建参数不一致")
    if str(raw.get("source_sha256") or "").lower() != source_sha256:
        raise ValueError("视觉目录 source_sha256 与输入 PDF 不一致")
    if not isinstance(raw.get("table_pages") or {}, dict):
        raise ValueError("视觉目录 table_pages 非法")
    if not isinstance(raw.get("asset_overrides") or [], list):
        raise ValueError("视觉目录 asset_overrides 非法")
    try:
        max_pixels = int(raw.get("max_flate_pixels", 300000))
    except (TypeError, ValueError) as exc:
        raise ValueError("视觉目录 max_flate_pixels 非法") from exc
    if not 1 <= max_pixels <= 2_000_000:
        raise ValueError("视觉目录 max_flate_pixels 超出范围")
    raw["max_flate_pixels"] = max_pixels
    return raw


def _image_placements(page) -> list[tuple[str, tuple[float, float, float, float]]]:
    placements: list[tuple[str, tuple[float, float, float, float]]] = []

    def before(operator, operands, cm_matrix, _tm_matrix):
        if operator != b"Do" or not operands:
            return
        name = str(operands[0])
        a, b, c, d, e, f = (float(value) for value in cm_matrix)
        points = ((e, f), (a + e, b + f), (c + e, d + f),
                  (a + c + e, b + d + f))
        xs = [value[0] for value in points]
        ys = [value[1] for value in points]
        placements.append((name, (
            round(min(xs), 4), round(min(ys), 4),
            round(max(xs), 4), round(max(ys), 4),
        )))

    page.extract_text(visitor_operand_before=before)
    return placements


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = binascii.crc32(kind + data) & 0xffffffff
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def _encode_png(width: int, height: int, rgb: bytes,
                alpha: bytes | None = None) -> bytes:
    channels = 4 if alpha is not None else 3
    if len(rgb) != width * height * 3:
        raise ValueError("RGB 解码长度与图片尺寸不一致")
    if alpha is not None and len(alpha) != width * height:
        raise ValueError("alpha 解码长度与图片尺寸不一致")
    rows: list[bytes] = []
    for y in range(height):
        row = bytearray([0])  # PNG filter=none
        rgb_start = y * width * 3
        alpha_start = y * width
        if alpha is None:
            row.extend(rgb[rgb_start:rgb_start + width * 3])
        else:
            for x in range(width):
                pos = rgb_start + x * 3
                row.extend((rgb[pos], rgb[pos + 1], rgb[pos + 2],
                            alpha[alpha_start + x]))
        rows.append(bytes(row))
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", width, height, 8,
                         6 if channels == 4 else 2, 0, 0, 0)
    return (signature + _png_chunk(b"IHDR", header)
            + _png_chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + _png_chunk(b"IEND", b""))


def _filters(image) -> tuple[str, ...]:
    value = image.get("/Filter")
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return (str(value),) if value else ()


def _extract_image_data(image, *, max_flate_pixels: int) -> tuple[str, bytes] | str:
    filters = _filters(image)
    width = int(image.get("/Width") or 0)
    height = int(image.get("/Height") or 0)
    if filters == ("/DCTDecode",):
        data = bytes(image._data)
        return ("image/jpeg", data) if data.startswith(b"\xff\xd8") \
            else "jpeg_magic_mismatch"
    if filters != ("/FlateDecode",):
        return f"unsupported_filter:{'+'.join(filters) or 'none'}"
    if width * height > max_flate_pixels:
        return f"flate_pixel_limit:{width}x{height}"
    if str(image.get("/ColorSpace")) != "/DeviceRGB" \
            or int(image.get("/BitsPerComponent") or 0) != 8:
        return (f"unsupported_flate_layout:{image.get('/ColorSpace')}:"
                f"{image.get('/BitsPerComponent')}")
    try:
        rgb = bytes(image.get_data())
        alpha = None
        if image.get("/SMask"):
            mask = image["/SMask"].get_object()
            if str(mask.get("/ColorSpace")) != "/DeviceGray" \
                    or int(mask.get("/BitsPerComponent") or 0) != 8 \
                    or int(mask.get("/Width") or 0) != width \
                    or int(mask.get("/Height") or 0) != height:
                return "unsupported_smask_layout"
            alpha = bytes(mask.get_data())
        return "image/png", _encode_png(width, height, rgb, alpha)
    except Exception as exc:
        return f"decode_error:{type(exc).__name__}"


def _label_spec(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        return {"caption": raw}
    if not isinstance(raw, dict) or not str(raw.get("caption") or "").strip():
        raise ValueError("视觉目录 label 必须是字符串或带 caption 的 object")
    aliases = raw.get("aliases") or []
    if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
        raise ValueError("视觉目录 label.aliases 非法")
    return dict(raw)


def extract_pdf_visual_assets(
    pdf_path: Path,
    *,
    document_id: str,
    source_sha256: str,
    catalog_path: Path,
) -> tuple[list[ExtractedVisualAsset], list[dict[str, Any]]]:
    """提取浏览器可显示的 PDF 图片，并按受控目录绑定表格图标语义。"""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "缺少构建期依赖 pypdf；请安装 agents/manual_rag/requirements-ingest.txt") from exc

    catalog = _load_visual_catalog(
        catalog_path, document_id=document_id, source_sha256=source_sha256)
    reader = PdfReader(str(pdf_path))
    paths, _ = _paths_by_page(reader)
    table_pages = {int(key): value for key, value in catalog["table_pages"].items()}
    overrides: dict[tuple[int, str], dict[str, Any]] = {}
    for raw in catalog.get("asset_overrides") or []:
        if not isinstance(raw, dict):
            raise ValueError("视觉目录 asset_overrides 项非法")
        key = (int(raw.get("page_start") or 0), str(raw.get("xobject_name") or ""))
        if key[0] < 1 or not key[1] or key in overrides:
            raise ValueError(f"视觉目录 asset_overrides 键非法或重复：{key}")
        overrides[key] = dict(raw)
    matched_overrides: set[tuple[int, str]] = set()

    assets: list[ExtractedVisualAsset] = []
    skipped: list[dict[str, Any]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        resources = page.get("/Resources") or {}
        xobject_ref = resources.get("/XObject")
        if not xobject_ref:
            continue
        xobjects = xobject_ref.get_object()
        placements = []
        for name, bbox in _image_placements(page):
            if name not in xobjects:
                continue
            image = xobjects[name].get_object()
            if str(image.get("/Subtype")) != "/Image":
                continue
            placements.append((name, bbox, image))
        placements.sort(key=lambda value: (-value[1][3], value[1][0], value[0]))

        label_specs: list[dict[str, Any]] = []
        if page_number in table_pages:
            table = table_pages[page_number]
            raw_labels = table.get("labels") if isinstance(table, dict) else None
            if not isinstance(raw_labels, list):
                raise ValueError(f"视觉目录第 {page_number} 页 labels 非法")
            label_specs = [_label_spec(item) for item in raw_labels]
            if len(label_specs) != len(placements):
                raise ValueError(
                    f"视觉目录第 {page_number} 页图标数量不一致："
                    f"labels={len(label_specs)}, images={len(placements)}")

        for position, (name, bbox, image) in enumerate(placements, start=1):
            extracted = _extract_image_data(
                image, max_flate_pixels=catalog["max_flate_pixels"])
            spec: dict[str, Any] = {}
            if label_specs:
                spec.update(label_specs[position - 1])
                spec.setdefault("role", "warning_icon")
            override = overrides.get((page_number, name))
            if override:
                spec.update(override)
                matched_overrides.add((page_number, name))
            if isinstance(extracted, str):
                if label_specs:
                    raise ValueError(
                        f"受控图标无法提取：PDF第{page_number}页 {name}：{extracted}")
                skipped.append({
                    "page_start": page_number,
                    "xobject_name": name,
                    "reason": extracted,
                })
                continue
            media_type, data = extracted
            width = int(image.get("/Width") or 0)
            height = int(image.get("/Height") or 0)
            caption = str(spec.get("caption") or "").strip()
            if not caption:
                section = " > ".join(paths.get(page_number, ())) or "手册"
                caption = f"{section}配图"
            role = str(spec.get("role") or "").strip()
            if not role:
                role = "illustration" if width * height >= 300000 else "icon"
            assets.append(ExtractedVisualAsset(
                asset_id=f"{document_id}:p{page_number:04d}:i{position:02d}",
                page_number=page_number,
                xobject_name=name,
                media_type=media_type,
                width=width,
                height=height,
                bbox=bbox,
                caption=caption,
                aliases=tuple(str(item).strip() for item in spec.get("aliases") or []
                              if str(item).strip()),
                description=str(spec.get("description") or "").strip(),
                role=role,
                data=data,
            ))
    missing_overrides = set(overrides) - matched_overrides
    if missing_overrides:
        raise ValueError(f"视觉目录 override 未命中 PDF 图片：{sorted(missing_overrides)}")
    return assets, skipped


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--document-id", default="xiaomi-su7-2024-user-manual")
    parser.add_argument("--title", default="")
    parser.add_argument("--publisher", default="小米汽车")
    parser.add_argument("--vehicle-model", default="xiaomi-su7-2024")
    parser.add_argument("--alias", action="append", default=[])
    parser.add_argument("--revision", default="")
    parser.add_argument("--expected-sha256", default="")
    parser.add_argument("--visual-catalog", type=Path,
                        default=DEFAULT_VISUAL_CATALOG,
                        help=".mrag 输出使用的受控视觉目录")
    parser.add_argument("--force", action="store_true",
                        help="显式允许覆盖已有生成索引")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    pdf_path = args.pdf
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF 不存在：{pdf_path}")
    source_hash = _file_sha256(pdf_path)
    expected = args.expected_sha256.strip().lower()
    if expected and source_hash != expected:
        raise ValueError(
            f"源 PDF SHA-256 不符：expected={expected}, actual={source_hash}")

    pages, metadata, source_pages = extract_pdf_pages(pdf_path)
    title = args.title.strip() or str(metadata.get("/Title") or "").strip()
    if not title:
        raise ValueError("PDF 标题为空，请显式传 --title")
    bundle = build_index_bundle(
        pages,
        document_id=args.document_id,
        title=title,
        publisher=args.publisher,
        vehicle_model=args.vehicle_model,
        vehicle_aliases=args.alias or DEFAULT_ALIASES,
        revision=_revision(metadata, args.revision),
        source_file=pdf_path.name,
        source_sha256=source_hash,
        source_pages=source_pages,
    )
    visual = {}
    if args.output.suffix.lower() == ".mrag":
        assets, skipped = extract_pdf_visual_assets(
            pdf_path,
            document_id=args.document_id,
            source_sha256=source_hash,
            catalog_path=args.visual_catalog,
        )
        visual, blobs = build_visual_manifest(
            assets,
            document_id=args.document_id,
            source_sha256=source_hash,
            skipped_assets=skipped,
        )
        output = write_manual_package(
            args.output, bundle, visual, blobs, overwrite=args.force)
    else:
        output = write_index_bundle(args.output, bundle, overwrite=args.force)
    summary = {
        "status": "built",
        "output": str(output),
        "source_sha256": source_hash,
        "source_pages": source_pages,
        "indexed_chunks": len(bundle["chunks"]),
        "content_sha256": bundle["document"]["content_sha256"],
        "index_sha256": _file_sha256(output),
        "vehicle_model": bundle["document"]["vehicle_model"],
        "revision": bundle["document"]["revision"],
        "visual_asset_count": visual.get("asset_count", 0),
        "visual_blob_count": visual.get("blob_count", 0),
        "visual_skipped_asset_count": visual.get("skipped_asset_count", 0),
        "visual_assets_sha256": visual.get("assets_sha256", ""),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
