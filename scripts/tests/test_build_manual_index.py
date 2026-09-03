"""车型手册索引构建器的纯函数契约；测试不依赖 pypdf。"""
from __future__ import annotations

import base64
import gzip
import json
import zipfile

import pytest

from agents.manual_rag.src.index_format import (
    ExtractedPage,
    ExtractedVisualAsset,
    IndexFormatError,
    build_index_bundle,
    build_visual_manifest,
    load_index_bundle,
    load_manual_package,
    write_index_bundle,
    write_manual_package,
)
from scripts.build_manual_index import _paths_by_page, main


def _bundle():
    return build_index_bundle(
        [
            ExtractedPage(3, ("导言", "手册说明"), "第一段真实手册内容。"),
            ExtractedPage(245, ("车辆规格", "车轮与轮胎参数"),
                          "轮胎压力（bar）为 2.9。"),
        ],
        document_id="xiaomi-su7-2024-user-manual",
        title="SU7用户手册",
        publisher="小米汽车",
        vehicle_model="xiaomi-su7-2024",
        vehicle_aliases=["SU7", "小米SU7"],
        revision="2024-04-15",
        source_file=r"D:\private\manual.pdf",
        source_sha256="b" * 64,
        source_pages=278,
    )


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _visual_manifest():
    manifest, blobs = build_visual_manifest(
        [ExtractedVisualAsset(
            asset_id="xiaomi-su7-2024-user-manual:p0193:i12",
            page_number=193,
            xobject_name="/I12",
            media_type="image/png",
            width=167,
            height=168,
            bbox=(85.72, 131.0, 109.58, 155.0),
            caption="安全带未系提醒指示灯",
            aliases=("小人背着宝剑", "背剑小人"),
            description="此灯点亮表示乘员未系好安全带。",
            role="warning_icon",
            data=_ONE_PIXEL_PNG,
        )],
        document_id="xiaomi-su7-2024-user-manual",
        source_sha256="b" * 64,
        skipped_assets=[{"page_start": 9, "xobject_name": "/I1",
                         "reason": "unsupported_filter:/LZWDecode"}],
    )
    return manifest, blobs


def test_bundle_is_deterministic_and_does_not_leak_absolute_source_path(tmp_path):
    first = tmp_path / "first.json.gz"
    second = tmp_path / "second.json.gz"

    write_index_bundle(first, _bundle())
    write_index_bundle(second, _bundle())

    assert first.read_bytes() == second.read_bytes()
    raw = gzip.decompress(first.read_bytes())
    assert b"D:\\private" not in raw
    assert json.loads(raw)["document"]["source_file"] == "manual.pdf"


def test_write_refuses_to_overwrite_without_explicit_force(tmp_path):
    path = tmp_path / "manual.json.gz"
    write_index_bundle(path, _bundle())

    with pytest.raises(FileExistsError):
        write_index_bundle(path, _bundle())


def test_load_rejects_tampered_chunk_hash(tmp_path):
    path = tmp_path / "manual.json.gz"
    write_index_bundle(path, _bundle())
    payload = json.loads(gzip.decompress(path.read_bytes()))
    payload["chunks"][0]["page_start"] = 99
    path.write_bytes(gzip.compress(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8"),
        mtime=0,
    ))

    with pytest.raises(IndexFormatError, match="页码非法"):
        load_index_bundle(path)


def test_duplicate_page_or_empty_corpus_is_rejected():
    common = dict(
        document_id="d", title="t", publisher="p", vehicle_model="m",
        vehicle_aliases=["m"], revision="2024-01-01", source_file="x.pdf",
        source_sha256="c" * 64, source_pages=2,
    )
    with pytest.raises(IndexFormatError, match="不能为空"):
        build_index_bundle([], **common)

    duplicate = [
        ExtractedPage(1, ("a",), "足够长的第一页正文内容。"),
        ExtractedPage(1, ("b",), "足够长的重复页正文内容。"),
    ]
    with pytest.raises(IndexFormatError, match="重复"):
        build_index_bundle(duplicate, **common)


def test_outline_siblings_on_same_page_are_both_kept_in_citation():
    class _Destination:
        def __init__(self, title, page):
            self.title = title
            self.page = page

    class _Reader:
        pages = [object(), object()]
        outline = [
            _Destination("车辆规格", 0),
            [_Destination("规格与参数", 0), [
                _Destination("车轮与轮胎参数", 0),
                _Destination("四轮定位", 0),
            ]],
        ]

        @staticmethod
        def get_destination_page_number(item):
            return item.page

    paths, _ = _paths_by_page(_Reader())

    assert paths[1] == ("车辆规格", "规格与参数", "车轮与轮胎参数 / 四轮定位")
    assert paths[2] == ("车辆规格", "规格与参数", "四轮定位")


def test_cli_rejects_wrong_source_sha_before_pdf_parsing(tmp_path):
    source = tmp_path / "manual.pdf"
    source.write_bytes(b"not-a-pdf")

    with pytest.raises(ValueError, match="源 PDF SHA-256 不符"):
        main([
            "--pdf", str(source),
            "--output", str(tmp_path / "index.json.gz"),
            "--expected-sha256", "0" * 64,
        ])


def test_visual_package_is_deterministic_hash_bound_and_legacy_index_readable(tmp_path):
    manifest, blobs = _visual_manifest()
    first = tmp_path / "first.mrag"
    second = tmp_path / "second.mrag"

    write_manual_package(first, _bundle(), manifest, blobs)
    write_manual_package(second, _bundle(), manifest, blobs)

    assert first.read_bytes() == second.read_bytes()
    loaded = load_manual_package(first)
    assert loaded.index["document"]["content_sha256"] == _bundle()["document"]["content_sha256"]
    assert loaded.visual["asset_count"] == 1
    assert loaded.visual["skipped_asset_count"] == 1
    asset = loaded.visual["assets"][0]
    assert asset["caption"] == "安全带未系提醒指示灯"
    assert loaded.read_asset(asset["asset_id"]) == _ONE_PIXEL_PNG
    assert load_index_bundle(first) == loaded.index


def test_visual_package_rejects_tampered_blob(tmp_path):
    manifest, blobs = _visual_manifest()
    source = tmp_path / "source.mrag"
    tampered = tmp_path / "tampered.mrag"
    write_manual_package(source, _bundle(), manifest, blobs)
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(tampered, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.startswith("assets/"):
                data = data[:-1] + bytes([data[-1] ^ 1])
            zout.writestr(info.filename, data)

    with pytest.raises(IndexFormatError, match="图片.*SHA-256"):
        load_manual_package(tampered)


def test_visual_package_rejects_source_mismatch(tmp_path):
    manifest, blobs = _visual_manifest()
    manifest["source_sha256"] = "c" * 64

    with pytest.raises(IndexFormatError, match="源 PDF"):
        write_manual_package(tmp_path / "bad.mrag", _bundle(), manifest, blobs)
