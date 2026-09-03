"""真实手册 retrieval evaluator 的判定契约。"""
from __future__ import annotations

import asyncio

from agents.manual_rag.src.providers.base import Chunk
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
