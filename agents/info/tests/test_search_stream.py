"""info.search 流式化（2026-09-13，性能评审 §3.4）：`handle_stream` 对 info.search 边合成边流，
增量拼起来 == final.speech；卡片 / 来源 / follow_up 与一次性版同源；其余意图仍只出一个 final。"""
from __future__ import annotations

import asyncio
import json

from agents._sdk.testing import run_handle, run_handle_stream
from agents.info.src.agent import InfoAgent
from agents.info.src.providers.base import SearchResult


class _SearchSpy:
    async def search(self, query, **kwargs):
        return [SearchResult(title="深圳湾公园", snippet="滨海休闲带", source="fixture",
                             content="资料正文：深圳湾公园位于深圳市西南部，开放时间 6:00—23:00。")]


_SYNTH = json.dumps({
    "answer": "深圳湾公园位于深圳市西南部，\n开放时间 6:00—23:00。",
    "key_points": ["滨海公园"], "confidence": "high", "used_sources": [1],
}, ensure_ascii=False)


def _agent(pieces: list[str]):
    agent = InfoAgent()
    agent.search = _SearchSpy()
    calls = {"stream": 0, "complete": 0}

    async def fake_stream(messages, **kwargs):
        calls["stream"] += 1
        for p in pieces:
            yield p

    async def fake_complete(messages, **kwargs):
        calls["complete"] += 1
        return "".join(pieces)

    agent.llm.stream = fake_stream
    agent.llm.complete = fake_complete
    return agent, calls


def test_search_stream_deltas_join_to_final_speech_and_card_is_same_as_unary():
    pieces = [_SYNTH[i:i + 4] for i in range(0, len(_SYNTH), 4)]
    agent, calls = _agent(pieces)
    q = "介绍一下深圳湾公园"
    events = asyncio.run(run_handle_stream(agent, "info.search", slots={"query": q}, raw_text=q))
    kinds = [k for k, _ in events]
    assert kinds[-1] == "final" and kinds.count("final") == 1
    deltas = "".join(p for k, p in events if k == "speech")
    final = events[-1][1]
    assert deltas == final.speech == "深圳湾公园位于深圳市西南部，\n开放时间 6:00—23:00。"
    assert final.ui_card["type"] == "search_result" and final.ui_card["confidence"] == "high"
    assert final.ui_card["sources"][0]["title"] == "深圳湾公园"
    assert calls == {"stream": 1, "complete": 0}          # 流式路径只走 llm.stream

    # 一次性版（executor 路径仍会调它）结论逐字相同，走的是 llm.complete
    unary = asyncio.run(run_handle(agent, "info.search", slots={"query": q}, raw_text=q))
    assert unary.speech == final.speech and unary.ui_card["type"] == "search_result"
    assert calls == {"stream": 1, "complete": 1}


def test_search_stream_early_exit_yields_only_final():
    agent, calls = _agent([_SYNTH])
    events = asyncio.run(run_handle_stream(agent, "info.search", slots={}, raw_text="搜一下"))
    assert [k for k, _ in events] == ["final"]
    assert events[0][1].missing_slots == ["query"]       # 缺槽照旧不合成
    assert calls == {"stream": 0, "complete": 0}


def test_other_info_intents_keep_default_single_final():
    agent, calls = _agent([_SYNTH])
    events = asyncio.run(run_handle_stream(agent, "info.weather", slots={"city": "深圳"}, raw_text="深圳天气"))
    assert [k for k, _ in events] == ["final"]
    assert calls == {"stream": 0, "complete": 0}
