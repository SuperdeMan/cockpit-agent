"""用户明说「记住…」时当场写入（`RememberUtterance`，2026-10-13，docs/design/2026-10-13-v2-200x5-followups.md §2）。

与后台抽取同一份抽取与治理、同一个写入函数、同一道代际栅栏；结果类别如实交回，说「记住了」的一方只认 `stored`。
走真实 proto 消息 + MemoryServicer + 内存兜底；抽取模型换成假的（`extract._default_complete`）。
"""
import asyncio
import importlib.util
import json
import os
import sys

import pytest

_MEM_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _MEM_DIR)
from cockpit.memory.v1 import memory_pb2  # noqa: E402
import extract  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "memory_server_remember_utterance_under_test", os.path.join(_MEM_DIR, "server.py"))
_mem_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mem_server)

UID = "u-remember"


def _servicer():
    svc = _mem_server.MemoryServicer()
    svc.store.url = ""            # Redis 内存兜底
    svc.store._vstore._dsn = ""   # 向量存储内存兜底
    return svc


class _LLM:
    """按句子回放抽取结果；记下被调用的次数（PII 那条一次都不该调）。"""

    def __init__(self, by_text: dict[str, list[dict]] | None = None, fail: bool = False):
        self.by_text = by_text or {}
        self.fail = fail
        self.calls = 0

    async def __call__(self, messages):
        self.calls += 1
        if self.fail:
            raise RuntimeError("llm-gateway down")
        convo = messages[-1]["content"]
        for text, cands in self.by_text.items():
            if text in convo:
                return json.dumps(cands, ensure_ascii=False)
        return "[]"


def _pref(text, predicate="taste.coffee"):
    return {"category": "explicit_preference", "kind": "semantic", "predicate": predicate,
            "text": text, "scope": "profile.taste", "confidence": 0.9}


@pytest.fixture
def llm(monkeypatch):
    fake = _LLM()
    monkeypatch.setattr(extract, "_default_complete", fake)
    return fake


async def _say(svc, text, epoch=""):
    return await svc.RememberUtterance(memory_pb2.RememberUtteranceRequest(
        user_id=UID, occupant_id="primary", session_id="s1", text=text, memory_epoch=epoch), None)


async def _current(svc):
    return [d["text"] for d, _ in await svc.store.recall(UID, occupant_id="primary", query="")]


def test_an_explicit_preference_is_stored_and_said_so(llm):
    llm.by_text = {"我喜欢喝拿铁": [_pref("用户喜欢喝拿铁")]}
    svc = _servicer()

    async def go():
        return await _say(svc, "记住我喜欢喝拿铁"), await _current(svc)

    resp, current = asyncio.run(go())
    assert (resp.outcome, resp.stored_count, list(resp.stored_texts)) == ("stored", 1, ["用户喜欢喝拿铁"])
    assert current == ["用户喜欢喝拿铁"]


def test_a_revised_preference_replaces_the_old_one_through_the_same_write_path(llm):
    """CA2-15 S3a 的修订照样生效：「改喝美式」取代「喝拿铁」，不并存（E03）。"""
    llm.by_text = {"我喜欢喝拿铁": [_pref("用户喜欢喝拿铁")], "改喝美式": [_pref("用户现在喝美式")]}
    svc = _servicer()

    async def go():
        await _say(svc, "记住，我喜欢喝拿铁")
        second = await _say(svc, "记住，我现在改喝美式了")
        return second, await _current(svc)

    second, current = asyncio.run(go())
    assert second.outcome == "stored"
    assert current == ["用户现在喝美式"]


def test_saying_it_again_is_still_stored_not_a_failure(llm):
    """同一句再说一遍：修订只加强旧条目、不出新 id——那也是「记着了」，不能答「没记下来」。"""
    llm.by_text = {"我喜欢喝拿铁": [_pref("用户喜欢喝拿铁")]}
    svc = _servicer()

    async def go():
        first, again = await _say(svc, "记住我喜欢喝拿铁"), await _say(svc, "记住我喜欢喝拿铁")
        return first, again, await _current(svc)

    first, again, current = asyncio.run(go())
    assert first.outcome == again.outcome == "stored"
    assert current == ["用户喜欢喝拿铁"]


def test_a_phone_number_is_refused_without_asking_the_model(llm):
    """E08：电话 / 证件号这类可识别隐私不记，也不调抽取模型；答复据此不能说「记住了」。"""
    svc = _servicer()

    async def go():
        return await _say(svc, "记住，我老婆的手机号是13800138000"), await _current(svc)

    resp, current = asyncio.run(go())
    assert (resp.outcome, resp.stored_count) == ("pii", 0)
    assert llm.calls == 0 and current == []


def test_nothing_storable_is_reported_as_not_stored(llm):
    llm.by_text = {"我有高血压": [{"category": "sensitive_fact", "text": "用户有高血压"}]}
    svc = _servicer()

    async def go():
        return await _say(svc, "记住我有高血压"), await _current(svc)

    resp, current = asyncio.run(go())
    assert (resp.outcome, resp.stored_count) == ("not_stored", 0)
    assert current == []


def test_a_family_name_is_kept_as_a_relation_even_when_the_model_is_down(monkeypatch):
    """抽取模型不可用时确定性的关系候选照常入库（与后台抽取同一条 P4 规则）：「我女儿叫小满」。"""
    monkeypatch.setattr(extract, "_default_complete", _LLM(fail=True))
    svc = _servicer()

    async def go():
        resp = await _say(svc, "记住，我女儿叫小满")
        edges = await svc.QueryRelations(memory_pb2.QueryRelationsRequest(user_id=UID), None)
        return resp, edges

    resp, edges = asyncio.run(go())
    assert resp.outcome == "stored" and resp.stored_count >= 1 and list(resp.stored_texts) == []
    assert any(e.subject == "小满" for e in edges.edges)


def test_a_delete_between_observing_and_writing_leaves_nothing(llm):
    """Agent 观测到的代际之后记忆被删过 ⇒ 不写、答 stale（同 Remember / 后台抽取的栅栏）。"""
    llm.by_text = {"我喜欢喝拿铁": [_pref("用户喜欢喝拿铁")]}
    svc = _servicer()

    async def go():
        before = (await svc.GetMemoryEpoch(memory_pb2.GetMemoryEpochRequest(user_id=UID), None)).memory_epoch
        await svc.ForgetUser(memory_pb2.ForgetUserRequest(user_id=UID), None)
        return await _say(svc, "记住我喜欢喝拿铁", epoch=before), await _current(svc)

    resp, current = asyncio.run(go())
    assert resp.outcome == "stale" and current == []


def test_without_a_user_nothing_is_attempted(llm):
    svc = _servicer()
    resp = asyncio.run(svc.RememberUtterance(memory_pb2.RememberUtteranceRequest(text="记住我喜欢喝拿铁"), None))
    assert resp.outcome == "off" and llm.calls == 0
