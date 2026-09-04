"""整本手册覆盖 evaluator 的有限分母与防自证契约。"""
from __future__ import annotations

import pytest

from scripts.eval_manual_rag_full_coverage import (
    CoverageContractError,
    build_page_anchor_cases,
    build_outline_leaf_cases,
    build_section_cases,
    build_visual_semantic_cases,
    canonical_sha256,
    validate_inventory,
)


def _chunks():
    return [
        {
            "chunk_id": "manual:p0003",
            "page_start": 3,
            "page_end": 3,
            "section_path": ["导言", "前言", "敬告用户 / 手册说明"],
            "content": "敬告用户。使用车辆前请仔细阅读危险、注意和说明等提示信息。",
        },
        {
            "chunk_id": "manual:p0004",
            "page_start": 4,
            "page_end": 4,
            "section_path": ["导言", "前言", "手册说明"],
            "content": "驾驶车辆时请保持全神贯注，不要在饮酒或服用镇静药物后驾驶。",
        },
    ]


def test_section_cases_cover_every_exact_path_with_parent_context():
    cases = build_section_cases(
        _chunks(),
        "我想知道手册里{parent} {leaf}？",
        {"导言 > 前言 > 手册说明": "驾驶员须知为什么禁止酒后驾驶？"},
    )

    assert len(cases) == 2
    assert {tuple(case["expect_section_path"]) for case in cases} == {
        ("导言", "前言", "敬告用户 / 手册说明"),
        ("导言", "前言", "手册说明"),
    }
    simple = next(
        case for case in cases
        if case["expect_section_path"] == ["导言", "前言", "手册说明"]
    )
    assert simple["query"] == "驾驶员须知为什么禁止酒后驾驶？"


def test_page_anchor_cases_cover_every_page_without_using_page_number():
    cases = build_page_anchor_cases(_chunks())

    assert [case["expect_pages_any"] for case in cases] == [[3], [4]]
    assert all("第3页" not in case["query"] and "第4页" not in case["query"]
               for case in cases)
    assert "服用镇静药物" in cases[1]["query"]


def test_visual_cases_group_duplicate_caption_and_keep_semantic_context():
    catalog = {
        "table_pages": {
            "192": {"labels": [{"caption": "后雾灯"}]},
            "193": {"labels": [{"caption": "后雾灯"}]},
        },
        "asset_overrides": [
            {"page_start": 95, "caption": "前风挡雨刮拨杆开关操作示意"},
            {"page_start": 96, "caption": "前风挡雨刮拨杆开关操作示意"},
        ],
    }
    config = {
        "visual_warning_query_template": "仪表上的{caption}图标是什么意思",
        "visual_illustration_query_template": "{caption}怎么使用",
        "visual_query_overrides": {
            "warning:后雾灯": "后雾灯亮了怎么处理？",
        },
    }

    cases = build_visual_semantic_cases(catalog, config)

    assert len(cases) == 2
    warning = next(case for case in cases if case["id"].startswith("warning-"))
    illustration = next(case for case in cases
                        if case["id"].startswith("illustration-"))
    assert warning["expect_image_pages_any"] == [192, 193]
    assert warning["query"] == "后雾灯亮了怎么处理？"
    assert illustration["expect_image_pages_any"] == [95, 96]


def test_inventory_is_digest_bound_and_rejects_silent_section_loss():
    chunks = _chunks()
    paths = sorted({tuple(item["section_path"]) for item in chunks})
    visual = {
        "asset_count": 2,
        "blob_count": 2,
        "skipped_asset_count": 1,
        "assets_sha256": "v" * 64,
    }
    config = {
        "content_sha256": "c" * 64,
        "visual_assets_sha256": "v" * 64,
        "expected": {
            "source_pages": 4,
            "indexed_pages": 2,
            "excluded_text_pages": [1, 2],
            "indexed_pages_sha256": canonical_sha256([3, 4]),
            "chunk_ids_sha256": canonical_sha256(
                ["manual:p0003", "manual:p0004"]),
            "index_section_paths": 2,
            "index_section_paths_sha256": canonical_sha256(
                [list(path) for path in paths]),
            "visual_assets": 2,
            "visual_blobs": 2,
            "visual_skipped_assets": 1,
        },
    }
    index = {
        "document": {
            "source_pages": 4,
            "content_sha256": "c" * 64,
        },
        "chunks": chunks,
    }

    summary = validate_inventory(index, visual, config)
    assert summary["index_section_paths"] == 2

    index["chunks"] = chunks[:1]
    with pytest.raises(CoverageContractError, match="indexed_pages"):
        validate_inventory(index, visual, config)


def test_outline_leaf_cases_keep_two_topics_on_the_same_page_separate():
    leaves = [
        {"page": 202, "section_path": [
            "信息显示和娱乐", "空调控制", "车内高温保护"]},
        {"page": 202, "section_path": [
            "信息显示和娱乐", "个性化娱乐", "地图和导航"]},
    ]

    cases = build_outline_leaf_cases(
        leaves,
        "我想知道手册里{parent} {leaf}？",
    )

    assert len(cases) == 2
    assert cases[0]["query"] == "我想知道手册里空调控制 车内高温保护？"
    assert cases[1]["query"] == "我想知道手册里个性化娱乐 地图和导航？"
    assert cases[0]["expect_pages_any"] == cases[1]["expect_pages_any"] == [202]
