"""接地合成流式化（2026-09-13，性能评审 §3.4）。

三件事要钉住：① `AnswerFieldStreamer` 逐片抽出的 answer 与 `parse_synth` 整段解析的 answer
逐字相同（转义 / 裸引号 / 片界落在转义中间 / 字段名被拆开）；② `grounded_synthesis_stream`
的增量拼起来 == 最终 result 的 answer，且 prompt 与一次性版同源；③ 内容风控拒收在首 token
之前 → 收窄 top-2 重试一次，中途出错 → 按已到文本收口而不是两手空空。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from agents._sdk.grounding import (
    AnswerFieldStreamer, grounded_synthesis, grounded_synthesis_stream, parse_synth,
    synthesis_messages)


def _stream_all(raw: str, chunk: int) -> str:
    st = AnswerFieldStreamer()
    out = []
    for i in range(0, len(raw), chunk):
        out.append(st.feed(raw[i:i + chunk]))
    out.append(st.close())
    return "".join(out)


_RAW = json.dumps({
    "answer": "深圳湾公园位于西南部，\n开放时间 6:00—23:00；名字来自「深圳湾」。",
    "key_points": ["滨海公园", "6:00—23:00"],
    "confidence": "high",
    "used_sources": [1, 2],
}, ensure_ascii=False)


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 50, 10_000])
def test_streamer_matches_parse_synth_at_any_chunk_size(chunk):
    expect = parse_synth(_RAW)["answer"]
    assert _stream_all(_RAW, chunk) == expect


def test_streamer_handles_unicode_escapes_split_across_chunks():
    raw = '{"answer": "A\\u6df1\\u5733B", "confidence": "high"}'
    expect = parse_synth(raw)["answer"]
    for chunk in (1, 2, 3, 4, 5):
        assert _stream_all(raw, chunk) == expect == "A深圳B"


def test_streamer_keeps_bare_quotes_inside_answer_like_parse_synth():
    # 裸英文引号（模型没照做用「」）：不是边界，正文原样保留——与 extract_json_str_field 同判据
    raw = '{"answer": "他说"你好", 然后走了。", "key_points": [], "confidence": "medium", "used_sources": []}'
    expect = parse_synth(raw)["answer"]
    assert expect == '他说"你好", 然后走了。'
    for chunk in (1, 4, 9, 100):
        assert _stream_all(raw, chunk) == expect


def test_streamer_truncated_stream_yields_prefix_and_never_leaks_json_shell():
    raw = '```json\n{"answer": "结论第一句，第二句还没写完'
    text = _stream_all(raw, 3)
    assert text == "结论第一句，第二句还没写完"
    assert "{" not in text and "answer" not in text


def test_streamer_nothing_before_answer_key_and_nothing_after_close():
    st = AnswerFieldStreamer()
    assert st.feed('{"key_points": [], ') == ""
    assert st.feed('"answ') == ""
    assert st.feed('er": "hi') == "hi"
    assert st.feed('", "confidence": "high"}') == ""
    assert st.done
    assert st.feed("zzz") == ""


class _FakeLLM:
    def __init__(self, pieces, *, fail_after=None, fail_first=False, err=RuntimeError("boom")):
        self.pieces = pieces
        self.fail_after = fail_after
        self.fail_first = fail_first
        self.err = err
        self.calls: list[list[dict]] = []

    async def stream(self, messages, **kw):
        self.calls.append(messages)
        if self.fail_first and len(self.calls) == 1:
            raise self.err
        for i, p in enumerate(self.pieces):
            if self.fail_after is not None and i >= self.fail_after:
                raise self.err
            yield p

    async def complete(self, messages, **kw):
        self.calls.append(messages)
        return "".join(self.pieces)


def _sources(n=4):
    return [{"title": f"t{i}", "url": f"https://a{i}.example.com/p", "source": f"a{i}",
             "published": "", "body": "正文" * 10} for i in range(1, n + 1)]


def _collect(gen):
    async def run():
        deltas, result = [], "UNSET"
        async for kind, payload in gen:
            if kind == "delta":
                deltas.append(payload)
            else:
                result = payload
        return deltas, result
    return asyncio.run(run())


def test_stream_deltas_join_to_final_answer_and_same_prompt_as_unary():
    pieces = [_RAW[i:i + 5] for i in range(0, len(_RAW), 5)]
    llm = _FakeLLM(pieces)
    deltas, result = _collect(grounded_synthesis_stream(llm, "深圳湾公园", _sources()))
    assert result and "".join(deltas) == result["answer"] == parse_synth(_RAW)["answer"]
    assert result["confidence"] == "high" and result["used_sources"] == [1, 2]
    # prompt 唯一声明源：流式与一次性版给 LLM 的是同一份 messages（同一段 synthesis_messages）
    unary = _FakeLLM(pieces)
    asyncio.run(grounded_synthesis(unary, "深圳湾公园", _sources()))
    assert llm.calls[0] == unary.calls[0]
    assert llm.calls[0][0]["role"] == "system" and "用户问题：深圳湾公园" in llm.calls[0][1]["content"]
    assert synthesis_messages("深圳湾公园", _sources()[:6])[1]["content"] == llm.calls[0][1]["content"]


def test_stream_content_rejection_before_first_token_retries_with_top2():
    llm = _FakeLLM([_RAW], fail_first=True, err=RuntimeError("422 new_sensitive"))
    deltas, result = _collect(grounded_synthesis_stream(llm, "q", _sources(5)))
    assert len(llm.calls) == 2
    assert "共2条" in llm.calls[1][1]["content"]           # 收窄到权威 top-2
    assert result and "".join(deltas) == result["answer"]


def test_stream_midway_failure_keeps_what_arrived():
    pieces = ['{"answer": "先到的半句', '，后半句', '", "confidence": "high"}']
    llm = _FakeLLM(pieces, fail_after=2)
    deltas, result = _collect(grounded_synthesis_stream(llm, "q", _sources()))
    assert "".join(deltas) == "先到的半句，后半句"
    assert result and result["answer"] == "先到的半句，后半句" and result["confidence"] == "low"


def test_stream_total_failure_yields_none_like_unary():
    llm = _FakeLLM([], fail_first=True)
    deltas, result = _collect(grounded_synthesis_stream(llm, "q", _sources(2)))
    assert deltas == [] and result is None
