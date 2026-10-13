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


# ── 2026-10-13 用户裁决：「记住…」里的生日、纪念日存成个人事实（设计 §12）────────────────────────────────

def _items(svc):
    return [d for d, _ in asyncio.run(svc.store.recall(UID, occupant_id="primary", query=""))]


def test_a_family_birthday_is_kept_even_when_the_model_files_it_as_third_party_privacy(llm):
    """核心旅程 E02 / E07：「记住我老婆的生日是五月二十号」当场抽取 10 次只收 1–5 次——模型时而当第三方隐私丢掉。
    用户明说「记住」时确定性收下：本人 / 家人的生日是个人事实（敏感，记忆页可删，没认出说话人时读不到）。"""
    llm.by_text = {"我老婆的生日": [{"category": "sensitive_fact", "text": "用户老婆的生日是五月二十号"}]}
    svc = _servicer()
    resp = asyncio.run(_say(svc, "记住，我老婆的生日是五月二十号"))
    assert (resp.outcome, list(resp.stored_texts)) == ("stored", ["用户老婆的生日是5月20日"])
    [item] = _items(svc)
    assert (item["predicate"], item["subject"], item["privacy_level"]) == ("person.birthday", "老婆", "sensitive")


def test_the_model_saying_the_same_birthday_again_does_not_make_a_second_entry(llm):
    """确定性收下的那类日期，模型同一句里的同类条目不再写（谓词不同就会并存两条）；同一句里别的内容照收。"""
    llm.by_text = {"我老婆的生日": [
        {"category": "personal_fact", "predicate": "person.spouse_birthday", "subject": "老婆",
         "text": "用户老婆的生日是五月二十号", "scope": "profile.person"},
        {"category": "explicit_preference", "kind": "semantic", "predicate": "taste.flower", "subject": "老婆",
         "text": "用户老婆喜欢百合", "scope": "profile.taste"}]}
    svc = _servicer()
    resp = asyncio.run(_say(svc, "记住我老婆的生日是五月二十号，她喜欢百合"))
    assert resp.outcome == "stored"
    texts = sorted(d["text"] for d in _items(svc))
    assert texts == ["用户老婆喜欢百合", "用户老婆的生日是5月20日"]


def test_birthdays_and_anniversaries_survive_the_model_being_down(monkeypatch):
    monkeypatch.setattr(extract, "_default_complete", _LLM(fail=True))
    svc = _servicer()
    for text in ("记住我的生日是三月八号", "别忘了我和老婆的结婚纪念日是十月一号"):
        assert asyncio.run(_say(svc, text)).outcome == "stored"
    got = sorted((d["predicate"], d["subject"], d["text"]) for d in _items(svc))
    assert got == [("person.anniversary", "", "用户和老婆的结婚纪念日是10月1日"),
                   ("person.birthday", "", "用户的生日是3月8日")]


def test_only_the_explicit_remember_path_tells_the_model_dates_are_storable(llm):
    """口径只在用户明说「记住」的那一句上放宽：后台抽取看不到那句话，生日也不确定性地收（第三方隐私的口径不变）。"""
    seen = []

    async def spy(messages):
        seen.append(messages[0]["content"])
        return "[]"

    asyncio.run(extract.extract([{"role": "user", "text": "我老婆的生日是五月二十号"}], user_id=UID, complete_fn=spy))
    background = asyncio.run(extract.extract([{"role": "user", "text": "记住我老婆的生日是五月二十号"}],
                                             user_id=UID, complete_fn=spy))
    explicit = asyncio.run(extract.extract([{"role": "user", "text": "记住我老婆的生日是五月二十号"}],
                                           user_id=UID, complete_fn=spy, explicit=True))
    assert background == [] and [c["text"] for c in explicit] == ["用户老婆的生日是5月20日"]
    assert [extract._EXPLICIT_NOTE in s for s in seen] == [False, False, True]


def test_a_lunar_or_vague_date_is_left_to_the_model(llm):
    """农历换算要历表、「快到了」不是日子：确定性那条不收，模型也没收就如实说没记下。"""
    svc = _servicer()
    for text in ("记住我妈生日是农历八月十五", "记住我老婆生日快到了"):
        assert asyncio.run(_say(svc, text)).outcome == "not_stored"
    assert _items(svc) == []


def test_the_background_pass_over_the_same_sentence_does_not_add_a_second_birthday(llm):
    """后台抽取稍后用旧口径再看这一句，模型自造谓词（A/B 实测 person.spouse_birthday / person.wife_birthday）：
    归一到 person.birthday、同一个人，按事实取代，不并存两条。"""
    svc = _servicer()
    asyncio.run(_say(svc, "记住我老婆的生日是五月二十号"))
    background = [{"category": "personal_fact", "predicate": "person.wife_birthday", "subject": "老婆",
                   "text": "用户主动告知：老婆的生日是五月二十号。", "scope": "profile.person"}]
    items = asyncio.run(extract.extract([{"role": "user", "text": "记住我老婆的生日是五月二十号"}], user_id=UID,
                                        complete_fn=_LLM({"我老婆的生日": background})))
    from extract import predicate_class
    asyncio.run(svc.store._write_candidates(UID, "primary", items, predicate_class))
    assert [(d["predicate"], d["text"]) for d in _items(svc)] == [("person.birthday", "用户主动告知：老婆的生日是五月二十号。")]

