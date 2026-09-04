"""真实手册 retrieval evaluator 的判定契约。"""
from __future__ import annotations

import asyncio

from agents.manual_rag.src.providers.base import Chunk, ManualImage
from scripts.eval_manual_rag import evaluate_cases


class _Retriever:
    async def retrieve(self, query, vehicle_model="", top_k=4):
        if "不存在" in query:
            return []
        return [Chunk(
            content="轮胎压力（bar）为 2.9。",
            source="SU7用户手册 · PDF第245页",
            score=0.9,
            source_type="manual",
            page_start=245,
            page_end=245,
            section_path=("车辆规格", "车轮与轮胎参数"),
            vehicle_model="xiaomi-su7-2024",
            images=(ManualImage(
                asset_id="manual:p0245:i1",
                caption="轮胎压力标签示意",
                description="",
                page_start=245,
                media_type="image/png",
                data_uri="data:image/png;base64,eA==",
                sha256="a" * 64,
                width=1,
                height=1,
                bbox=(0.0, 0.0, 1.0, 1.0),
                role="illustration",
                match_kind="page_evidence",
            ),),
        )]


def test_evaluator_checks_page_content_and_negative_cases():
    cases = [
        {"id": "positive", "query": "胎压多少", "expect_pages_any": [245],
         "expect_top_page": 245, "expect_text_all": ["2.9"]},
        {"id": "negative", "query": "不存在的能力", "expect_empty": True},
    ]

    report = asyncio.run(evaluate_cases(_Retriever(), cases, top_k=4))

    assert {key: report["summary"][key] for key in ("total", "passed", "failed")} == {
        "total": 2, "passed": 2, "failed": 0,
    }
    assert report["summary"]["latency_ms"]["max"] >= 0
    assert report["summary"]["splits"] == {
        "main": {"total": 2, "passed": 2, "failed": 0},
    }


def test_evaluator_does_not_pass_on_page_number_alone():
    cases = [{"id": "wrong-content", "query": "胎压多少",
              "expect_pages_any": [245], "expect_text_all": ["2.5"]}]

    report = asyncio.run(evaluate_cases(_Retriever(), cases, top_k=4))

    assert report["summary"]["failed"] == 1
    assert "missing text" in report["cases"][0]["reason"]


def test_evaluator_can_require_multiple_complementary_pages():
    cases = [{"id": "needs-spec-and-context", "query": "胎压多少",
              "expect_pages_all": [245, 256]}]

    report = asyncio.run(evaluate_cases(_Retriever(), cases, top_k=4))

    assert report["summary"]["failed"] == 1
    assert "missing required pages [256]" in report["cases"][0]["reason"]


def test_evaluator_checks_visual_caption_and_page():
    cases = [{
        "id": "visual",
        "query": "轮胎标签长什么样",
        "expect_pages_any": [245],
        "expect_image_pages_any": [245],
        "expect_image_caption_all": ["轮胎压力标签"],
    }]

    report = asyncio.run(evaluate_cases(_Retriever(), cases, top_k=4))

    assert report["summary"]["failed"] == 0
    assert report["cases"][0]["images"][0]["caption"] == "轮胎压力标签示意"


def test_evaluator_fails_when_expected_visual_evidence_is_missing():
    cases = [{
        "id": "wrong-visual",
        "query": "轮胎标签长什么样",
        "expect_image_pages_any": [193],
        "expect_image_caption_all": ["安全带"],
    }]

    report = asyncio.run(evaluate_cases(_Retriever(), cases, top_k=4))

    assert report["summary"]["failed"] == 1
    assert "missing expected image page" in report["cases"][0]["reason"]
    assert "missing image caption" in report["cases"][0]["reason"]


def test_evaluator_requires_the_exact_section_path_not_only_the_page():
    cases = [{
        "id": "wrong-section",
        "query": "胎压多少",
        "expect_pages_any": [245],
        "expect_section_path": ["车辆规格", "规格与参数", "电机参数"],
    }]

    report = asyncio.run(evaluate_cases(_Retriever(), cases, top_k=4))

    assert report["summary"]["failed"] == 1
    assert "missing expected section" in report["cases"][0]["reason"]
    assert report["cases"][0]["hits"][0]["section_path"] == [
        "车辆规格", "车轮与轮胎参数",
    ]
